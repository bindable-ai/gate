# bindable-gate/src/bindable_gate/hooks/copilot_cli.py
"""The GitHub Copilot CLI adapter for the Agent Hook Gate.

Copilot CLI runs one command for each hook event and passes the event as JSON
on standard input (https://docs.github.com/en/copilot/reference/hooks-reference).
The policy file this package prints configures the camelCase events, so the
input uses ``sessionId``, ``toolName`` and ``toolArgs``. The PascalCase form
(``session_id``, ``tool_name``, ``tool_input``) is read too.

====================== ============================================ ==========
Event                  The core                                      Exit
====================== ============================================ ==========
preToolUse             ``before``: a pass prints nothing             0
                       a deny or any error: a deny decision          2
postToolUse            ``after``: report ``released``                0
postToolUseFailure     ``after``: the tool ran and failed, which     0
                       the Gate released, so ``released``
agentStop, sessionEnd  ``sweep``: a call refused at the permission   0
                       prompt sends no event, so report ``blocked``
                       for every call of the session still waiting
sessionStart           say once whether calls are reviewed or        0
                       recorded locally, and whether they are
                       signed, as ``additionalContext``
====================== ============================================ ==========

``additionalContext`` is the one output ``sessionStart`` documents; the
reference says it is injected into the session, not that the person sees it.

Every tool is proposed, MCP tools included. The reference does not say how
Copilot CLI names an MCP server's tools, and skipping a tool on a guessed name
would let it through unreviewed. A tool the hosted MCP Gate also reviews is
reviewed twice, which costs a Receipt and lets nothing through.

A pass prints nothing, never ``allow``: in Copilot CLI ``allow`` suppresses the
approval prompt. Copilot CLI denies the tool when a ``preToolUse`` command
exits with any non-zero code, and lets it through when the hook times out, so
this adapter answers within :data:`DEADLINE_SECONDS`, under the
:data:`HOOK_TIMEOUT_SECONDS` the policy file gives it. A pass never prints
``modifiedArgs``, so the hook never changes a call's arguments.

NO CALL ID. Copilot CLI's hook input carries no tool-call identifier, so this
adapter pairs a ``postToolUse`` with its ``preToolUse`` itself. ``preToolUse``
gives each call a fresh key and, once Action Review releases it, adds the key
to the session's pending list with the tool name and the safe payload. After
the call, the adapter takes the oldest pending entry with the same tool name
and payload. Two identical calls in flight at once are told apart by order
only, and both are the same action.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import sys
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Final, TextIO

from bindable_gate.contract.signing import signing_notice
from bindable_gate.hooks.config import ConfigError, HookConfig
from bindable_gate.hooks.core import Call, Deny, HookGate
from bindable_gate.library.payload import named_fields

CLI: Final[str] = "copilot-cli"

#: The ``timeoutSec`` each policy hook carries. Copilot CLI's default is 30.
HOOK_TIMEOUT_SECONDS: Final[int] = 30

#: The hook's own deadline, under :data:`HOOK_TIMEOUT_SECONDS`.
DEADLINE_SECONDS: Final[float] = 25.0

#: The named fields a Proposal carries, by Copilot CLI tool, under
#: both the runtime names and the Claude-format names the PascalCase input uses.
#: The hooks reference names the tools but not their argument fields; these are
#: the tools' own argument names. A field not named here is never sent, so a
#: name that is wrong leaves a field out and never lets content in.
PAYLOAD_FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    "bash": ("command",),
    "powershell": ("command",),
    "view": ("path",),
    "create": ("path",),
    "edit": ("path",),
    "str_replace_editor": ("path",),
    "grep": ("pattern", "path"),
    "rg": ("pattern", "path"),
    "glob": ("pattern", "path"),
    "web_fetch": ("url",),
    "web_search": ("query",),
    "Bash": ("command",),
    "Read": ("path", "file_path"),
    "Write": ("path", "file_path"),
    "Edit": ("path", "file_path"),
    "Grep": ("pattern", "path"),
    "Glob": ("pattern", "path"),
    "WebFetch": ("url",),
    "WebSearch": ("query",),
}

#: The most characters of a field a Proposal carries.
PAYLOAD_LIMITS: Final[Mapping[str, int]] = {"command": 200}

#: The events the policy file sends to this adapter.
EVENTS: Final[tuple[str, ...]] = (
    "preToolUse",
    "postToolUse",
    "postToolUseFailure",
    "agentStop",
    "sessionEnd",
    "sessionStart",
)

_PASCAL: Final[Mapping[str, str]] = {
    "PreToolUse": "preToolUse",
    "PostToolUse": "postToolUse",
    "PostToolUseFailure": "postToolUseFailure",
    "Stop": "agentStop",
    "SessionEnd": "sessionEnd",
    "SessionStart": "sessionStart",
}

#: The session's pending list, beside the Receipts the core keeps. Not a
#: ``.json`` name, so the core's sweep never reads it as a Receipt.
PENDING: Final[str] = "copilot-pending.list"
_LOCK: Final[str] = "copilot-pending.lock"


# ─── Reading the input ─────────────────────────────────────────────────


def event_name(event: Mapping[str, Any], configured: str | None) -> str:
    """The camelCase name of the event: the one the command was configured
    for, else the PascalCase input's own ``hook_event_name``."""
    if configured:
        return configured
    name = event.get("hook_event_name")
    return _PASCAL.get(name, "") if isinstance(name, str) else ""


def tool_name_of(event: Mapping[str, Any]) -> str:
    name = event.get("toolName", event.get("tool_name"))
    return name if isinstance(name, str) else ""


def tool_args_of(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The tool's arguments as a mapping. The reference types them ``unknown``,
    so a JSON object given as text is read too."""
    args = event.get("toolArgs", event.get("tool_input"))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    return args if isinstance(args, Mapping) else None


def session_of(event: Mapping[str, Any]) -> str:
    value = event.get("sessionId", event.get("session_id"))
    return str(value) if value else ""


def payload_for(tool_name: str, tool_args: Mapping[str, Any]) -> dict[str, Any]:
    """The Proposal's payload: the tool's named fields, credentials masked, cut."""
    return named_fields(tool_args, PAYLOAD_FIELDS.get(tool_name, ()), mask=True, limits=PAYLOAD_LIMITS)


# ─── The pending list ──────────────────────────────────────────────────


_SESSION: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_\-.]{1,200}$")


class Pending:
    """The session's released calls waiting for their ``postToolUse``."""

    def __init__(self, state_dir: Path, session_id: str) -> None:
        name = session_id or "no-session"
        if not _SESSION.match(name) or name in (".", ".."):
            raise ValueError("the hook input carries a session id the hook cannot use")
        self.folder = state_dir / name

    @contextlib.contextmanager
    def _locked(self) -> Iterator[list[dict[str, Any]]]:
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.folder / _LOCK, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            path = self.folder / PENDING
            entries: list[dict[str, Any]] = []
            if path.is_file():
                entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
            yield entries
            temporary = path.with_suffix(".tmp")
            temporary.unlink(missing_ok=True)
            handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.writelines(json.dumps(entry, sort_keys=True) + "\n" for entry in entries)
            os.replace(temporary, path)
        finally:
            os.close(descriptor)

    def add(self, key: str, tool_name: str, payload: Mapping[str, Any]) -> None:
        with self._locked() as entries:
            entries.append({"key": key, "tool_name": tool_name, "payload": dict(payload)})

    def take(self, tool_name: str, payload: Mapping[str, Any]) -> str | None:
        """The key of the oldest waiting call with this tool and payload."""
        with self._locked() as entries:
            for position, entry in enumerate(entries):
                if entry["tool_name"] == tool_name and entry["payload"] == dict(payload):
                    del entries[position]
                    return str(entry["key"])
        return None

    def clear(self) -> None:
        if self.folder.is_dir():
            with self._locked() as entries:
                entries.clear()


# ─── Handling one event ────────────────────────────────────────────────


def handle(event: Mapping[str, Any], gate: HookGate, configured: str | None = None) -> tuple[int, str]:
    """The exit code and the standard output for one hook event."""
    name = event_name(event, configured)
    tool_name = tool_name_of(event)
    session = session_of(event)
    if name == "preToolUse":
        args = tool_args_of(event)
        if not tool_name or args is None:
            return deny("The hook input names no tool or no arguments, so Action Gate blocked the tool.")
        payload = payload_for(tool_name, args)
        key = f"cp-{uuid.uuid4().hex}"
        answer = gate.before(Call(cli=CLI, tool_name=tool_name, tool_use_id=key, payload=payload,
                                  session_id=session))
        if isinstance(answer, Deny):
            return deny(answer.reason)
        try:
            Pending(gate.config.state_dir, session).add(key, tool_name, payload)
        except (OSError, ValueError):
            gate.withheld(Call(cli=CLI, tool_name=tool_name, tool_use_id=key, session_id=session))
            return deny(f"The hook could not keep the call's place, so Action Gate blocked {tool_name}.")
        if answer.notice:
            # Offline mode's line for the person: Copilot CLI's allow carries
            # no message, so it goes to standard error.
            print(answer.notice, file=sys.stderr, flush=True)
        return 0, ""
    if name in ("postToolUse", "postToolUseFailure"):
        args = tool_args_of(event) or {}
        found: str | None
        try:
            found = Pending(gate.config.state_dir, session).take(tool_name, payload_for(tool_name, args))
        except ValueError:
            found = None
        # No match is reported as a gap by the core: no Receipt was kept for it.
        gate.after(Call(cli=CLI, tool_name=tool_name, tool_use_id=found or f"unmatched-{uuid.uuid4().hex}",
                        session_id=session))
    elif name in ("agentStop", "sessionEnd"):
        gate.sweep(session)
        Pending(gate.config.state_dir, session).clear()
    return 0, ""


def deny(reason: str) -> tuple[int, str]:
    """Exit 2 with the deny decision on standard output: Copilot CLI denies on
    exit 2 whatever the output says, and shows the reason to the agent."""
    return 2, json.dumps({"permissionDecision": "deny", "permissionDecisionReason": reason})


def standalone_notice(gate: HookGate) -> str:
    """What the session is told once when no credential is configured."""
    return (
        "Bindable Gate: this agent is recording locally to its local Track "
        f"({gate.local_track_path}) and is not connected to Bindable. Tool calls are "
        "allowed and not reviewed. "
        f"Signing: {signing_notice(gate.agent_key, connected=False, setting='agent_key_file')}."
    )


def connected_notice(gate: HookGate) -> str:
    """What the session is told once when the hook is connected."""
    return (
        "Bindable Gate: connected; every tool call is reviewed by Bindable in the "
        f"{gate.config.review_context} context for agent {gate.config.agent_shown}; "
        f"{signing_notice(gate.agent_key, connected=True, setting='agent_key_file')}."
    )


def run(config_path: Path, configured: str | None, stdin: TextIO, stdout: TextIO) -> int:
    """One hook event from ``stdin``. Never raises: in ``preToolUse`` any
    error is a deny; elsewhere it is exit 0."""
    try:
        event = json.load(stdin)
    except ValueError:
        event = None
    if not isinstance(event, dict):
        if configured not in (None, "preToolUse"):
            return 0
        code, text = deny("The hook input could not be read as JSON, so Action Gate blocked the tool.")
    else:
        name = event_name(event, configured)
        blocking = name == "preToolUse"
        try:
            gate = HookGate(HookConfig.load(config_path), cli=CLI, deadline_seconds=DEADLINE_SECONDS)
            if name == "sessionStart":
                notice = standalone_notice(gate) if gate.standalone else connected_notice(gate)
                code, text = 0, json.dumps({"additionalContext": notice})
            else:
                code, text = handle(event, gate, configured)
        except ConfigError as error:
            if name == "sessionStart":
                code, text = 0, json.dumps({"additionalContext": f"Bindable Gate: configuration invalid. {error}"})
            else:
                code, text = deny(str(error)) if blocking else (0, "")
        except Exception as error:  # noqa: BLE001 - fail closed before a tool runs
            code, text = deny(
                f"The agent hook failed with {type(error).__name__}, so Action Gate blocked the tool."
            ) if blocking else (0, "")
        if not blocking and code != 0:
            code, text = 0, ""
    if text:
        stdout.write(text + "\n")
    return code


def main(config_path: Path, configured: str | None) -> int:
    return run(config_path, configured, sys.stdin, sys.stdout)
