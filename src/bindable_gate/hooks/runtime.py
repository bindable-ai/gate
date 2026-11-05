# bindable-gate/src/bindable_gate/hooks/runtime.py
"""One adapter for the hook Gates whose runtimes differ only in names and
answers: Cursor, Gemini CLI and Windsurf (Devin Local).

Each runtime runs one command per hook event and passes the event as JSON on
standard input. What differs is described by a :class:`Spec`: the event names,
where the tool, its input, the call and the session are in the payload, which
fields of each tool a Proposal carries, and how the runtime reads an allow, a
block and a startup line. The behaviour is the same for all of them:

- before a tool runs, the call is proposed with the agent's did:webvh and the
  hook allows it only when Action Review releases it; every other answer,
  decision unavailable included, blocks it (the core decides; see
  ``HookGate._unavailable``);
- after it runs, it is reported ``released``; when the turn or session ends,
  every call that passed review and never ran is reported ``blocked``;
- the session start says once whether calls are reviewed and signed;
- no answer ever carries new input for the tool, so a call's parameters are
  never rewritten.

A runtime whose payload carries no call id gets the pairing Copilot CLI's
adapter uses: each released call waits in the session's pending list, and
the tool's after event takes the oldest waiting call with the same tool and
payload.
"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from bindable_gate.contract.local_track import NO_RESULT
from bindable_gate.contract.signing import signing_notice
from bindable_gate.hooks.config import ConfigError, HookConfig
from bindable_gate.hooks.copilot_cli import Pending
from bindable_gate.hooks.core import Call, Deny, HookGate
from bindable_gate.library.payload import named_fields

#: What one hook event is answered with: the exit code, standard output and
#: standard error.
Answer = tuple[int, str, str]

#: The hook's own deadline, under the timeout every hook entry carries.
HOOK_TIMEOUT_SECONDS = 30
DEADLINE_SECONDS = 25.0

#: The most characters of a field a Proposal carries.
PAYLOAD_LIMITS: Mapping[str, int] = {"command": 200}


@dataclass(frozen=True)
class Spec:
    """How one runtime's hooks read and answer."""

    #: The runtime's name in ``gate_version`` and on the command line.
    cli: str
    #: The runtime's name for a person.
    title: str
    before: frozenset[str]
    after: frozenset[str]
    ending: frozenset[str]
    start: frozenset[str]
    #: The named fields a Proposal carries, by tool. Any other tool proposes
    #: with an empty payload, so file contents and prompts stay on the computer.
    payload_fields: Mapping[str, tuple[str, ...]]
    allow: Callable[[], Answer]
    block: Callable[[str], Answer]
    notice: Callable[[str], Answer]
    #: An allow that also tells the person something (offline mode's line).
    #: The plain allow, with the line on standard error, when not given.
    allow_notice: Callable[[str], Answer] | None = None
    #: Where the call id is, or ``None`` when the payload carries none.
    call_id_field: str | None = "tool_use_id"
    session_fields: tuple[str, ...] = ("session_id",)
    #: Where the tool's input is; some runtimes send it as a JSON string.
    input_field: str = "tool_input"
    result_field: str = "tool_response"
    skipped_prefixes: tuple[str, ...] = field(default=())


def _session(spec: Spec, event: Mapping[str, Any]) -> str:
    for name in spec.session_fields:
        value = event.get(name)
        if value:
            return str(value)
    return ""


def tool_input_of(spec: Spec, event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = event.get(spec.input_field)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, Mapping) else None


def payload_for(spec: Spec, tool_name: str, tool_input: Mapping[str, Any]) -> dict[str, Any]:
    """The Proposal's payload: the tool's named fields, credentials masked, cut."""
    return named_fields(tool_input, spec.payload_fields.get(tool_name, ()), mask=True, limits=PAYLOAD_LIMITS)


def notice_for(spec: Spec, gate: HookGate) -> str:
    """The one startup line."""
    if gate.standalone:
        return (
            "Bindable Gate: this agent is recording locally to its local Track "
            f"({gate.local_track_path}) and is not connected to Bindable. Tool calls are "
            f"allowed and not reviewed. Run bindable-gate install {spec.cli} with your agent "
            "and base URL, and add the Gate credential to the keychain, to connect. "
            f"Signing: {signing_notice(gate.agent_key, connected=False, setting='agent_key_file')}."
        )
    return (
        "Bindable Gate: connected; every tool call is reviewed by Bindable in the "
        f"{gate.config.review_context} context for agent {gate.config.agent_shown}; "
        f"{signing_notice(gate.agent_key, connected=True, setting='agent_key_file')}."
    )


def handle(spec: Spec, event: Mapping[str, Any], gate: HookGate) -> Answer:
    """The answer to one hook event."""
    name = event.get("hook_event_name")
    tool_name = event.get("tool_name")
    tool_name = tool_name if isinstance(tool_name, str) else ""
    if spec.skipped_prefixes and tool_name.startswith(spec.skipped_prefixes):
        return spec.allow() if name in spec.before else (0, "", "")
    session = _session(spec, event)
    if name in spec.before:
        tool_input = tool_input_of(spec, event)
        if not tool_name or tool_input is None:
            return spec.block("The hook input names no tool or no tool input, so Action Gate blocked the tool.")
        payload = payload_for(spec, tool_name, tool_input)
        call_id = str(event.get(spec.call_id_field) or "") if spec.call_id_field else ""
        key = call_id or f"{spec.cli}-{uuid.uuid4().hex}"
        answer = gate.before(Call(cli=spec.cli, tool_name=tool_name, tool_use_id=key, payload=payload,
                                  session_id=session))
        if isinstance(answer, Deny):
            return spec.block(answer.reason)
        if not call_id:
            try:
                Pending(gate.config.state_dir, session).add(key, tool_name, payload)
            except (OSError, ValueError):
                gate.withheld(Call(cli=spec.cli, tool_name=tool_name, tool_use_id=key, session_id=session))
                return spec.block(f"The hook could not keep the call's place, so Action Gate blocked {tool_name}.")
        if answer.notice:
            if spec.allow_notice is not None:
                return spec.allow_notice(answer.notice)
            code, out, _ = spec.allow()
            return code, out, answer.notice
        return spec.allow()
    if name in spec.after:
        tool_input = tool_input_of(spec, event) or {}
        payload = payload_for(spec, tool_name, tool_input)
        key = str(event.get(spec.call_id_field) or "") if spec.call_id_field else ""
        if not key:
            try:
                key = Pending(gate.config.state_dir, session).take(tool_name, payload) or ""
            except ValueError:
                key = ""
        # No match is reported as a gap by the core: no Receipt was kept for it.
        gate.after(Call(cli=spec.cli, tool_name=tool_name, tool_use_id=key or f"unmatched-{uuid.uuid4().hex}",
                        payload=payload, session_id=session, result=event.get(spec.result_field, NO_RESULT)))
    elif name in spec.ending:
        gate.sweep(session)
        if spec.call_id_field is None:
            try:
                Pending(gate.config.state_dir, session).clear()
            except ValueError:
                pass
    return 0, "", ""


def run(spec: Spec, config_path: Path, stdin: TextIO, stderr: TextIO, stdout: TextIO | None = None) -> int:
    """One hook event from ``stdin``. Never raises: before a tool runs, any
    error blocks it; after, every answer is exit 0."""
    out = stdout or sys.stdout
    try:
        event = json.load(stdin)
    except ValueError:
        event = None
    if not isinstance(event, dict):
        code, text, err = spec.block("The hook input could not be read as JSON, so Action Gate blocked the tool.")
    else:
        name = event.get("hook_event_name")
        blocking = name in spec.before
        try:
            gate = HookGate(HookConfig.load(config_path), cli=spec.cli, deadline_seconds=DEADLINE_SECONDS)
            if name in spec.start:
                code, text, err = spec.notice(notice_for(spec, gate))
            else:
                code, text, err = handle(spec, event, gate)
        except ConfigError as error:
            if name in spec.start:
                code, text, err = spec.notice(f"Bindable Gate: configuration invalid. {error}")
            else:
                code, text, err = spec.block(str(error)) if blocking else (0, "", "")
        except Exception as error:  # noqa: BLE001 - fail closed before a tool runs
            code, text, err = spec.block(
                f"The agent hook failed with {type(error).__name__}, so Action Gate blocked the tool."
            ) if blocking else (0, "", "")
    if text:
        out.write(text.rstrip() + "\n")
    if err:
        stderr.write(err.rstrip() + "\n")
    return code
