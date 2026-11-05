# bindable-gate/src/bindable_gate/hooks/codex_cli.py
"""The Codex CLI adapter for the Agent Hook Gate.

Codex runs one command for each hook event and passes the event as JSON on
standard input (https://developers.openai.com/codex/hooks). This adapter maps
the events to the core:

==================== ============================================ ==========
Event                The core                                      Exit
==================== ============================================ ==========
PreToolUse           ``before``: a pass returns no decision        0
                     a deny or any error: one sentence on stderr   2
PostToolUse          ``after``: report ``released``                0
Stop                 ``sweep``: a call refused at the approval     0
                     prompt sends no event, so report ``blocked``
                     for every call of the session still waiting
SessionStart         tell the person, once per session, whether   0
                     calls are reviewed or recorded locally, and
                     whether they are signed
==================== ============================================ ==========

Standalone (no Gate credential configured), ``PreToolUse`` allows every call
and ``PostToolUse`` appends it to the local Track.

A pass prints nothing: no ``permissionDecision``, and never ``updatedInput``,
so the hook never changes a call's parameters and never skips Codex's own
approval. Exit 2 with the reason on stderr is the deny every Codex version
takes. Codex lets the tool run when a ``PreToolUse`` hook times out, crashes,
exits with any other code, or exits 2 with nothing on stderr (openai/codex,
``codex-rs/hooks/src/events/pre_tool_use.rs``). So this adapter answers
within :data:`DEADLINE_SECONDS`, under the :data:`HOOK_TIMEOUT_SECONDS` its
hook entries carry, turns every error in ``PreToolUse`` into exit 2 with one
sentence, and the installed command turns any other exit into exit 2 as well.

Codex's hooks cover shell commands, ``apply_patch``, MCP tools and its other
local function tools. Hosted tools, such as web search, never reach a hook.

Tools whose names start with ``mcp__bindable`` are skipped: the hosted MCP Gate
already reviews them.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, TextIO

from bindable_gate.contract.local_track import NO_RESULT
from bindable_gate.contract.signing import signing_notice
from bindable_gate.hooks.config import ConfigError, HookConfig
from bindable_gate.hooks.core import Call, Deny, HookGate
from bindable_gate.library.payload import mask_credentials, named_fields

CLI: Final[str] = "codex"

#: The ``timeout`` each hook entry carries in ``hooks.json``, in seconds.
HOOK_TIMEOUT_SECONDS: Final[int] = 30

#: The hook's own deadline, under :data:`HOOK_TIMEOUT_SECONDS`.
DEADLINE_SECONDS: Final[float] = 25.0

#: Tools the hosted MCP Gate already reviews.
SKIPPED_PREFIX: Final[str] = "mcp__bindable"

#: The named fields a Proposal carries, by Codex tool. Shell commands, unified
#: exec included, reach the hook as ``Bash``; ``exec_command`` is kept for a
#: Codex that names it. ``apply_patch`` carries the files it touches (see
#: :func:`patch_paths`), never the patch. Any other tool proposes with an
#: empty payload, so file contents, prompts and every other field stay on the
#: computer.
PAYLOAD_FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    "Bash": ("command",),
    "exec_command": ("cmd", "command"),
}

#: The most characters of a field a Proposal carries.
PAYLOAD_LIMITS: Final[Mapping[str, int]] = {"command": 200, "cmd": 200}

#: The most files of one patch a Proposal names, and the most characters of each.
PATCH_FILES_LIMIT: Final[int] = 20
PATCH_PATH_LIMIT: Final[int] = 200

_PATCH_FILE: Final[re.Pattern[str]] = re.compile(
    r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$", re.MULTILINE
)

EVENTS: Final[tuple[str, ...]] = ("PreToolUse", "PostToolUse", "Stop", "SessionStart")
"""The events ``hooks.json`` sends to this adapter."""


def patch_paths(patch: str) -> list[str]:
    """The files an ``apply_patch`` patch adds, updates, deletes or moves to,
    in order, each once."""
    seen: list[str] = []
    for path in _PATCH_FILE.findall(patch):
        if path not in seen:
            seen.append(path)
    return seen


def payload_for(tool_name: str, tool_input: Mapping[str, Any]) -> dict[str, Any]:
    """The Proposal's payload for one Codex tool call: its named fields,
    credentials masked, then cut."""
    if tool_name == "apply_patch":
        command = tool_input.get("command")
        paths = patch_paths(command) if isinstance(command, str) else []
        return {
            "file_paths": [
                mask_credentials(path)[:PATCH_PATH_LIMIT] for path in paths[:PATCH_FILES_LIMIT]
            ]
        }
    return named_fields(
        tool_input,
        PAYLOAD_FIELDS.get(tool_name, ()),
        mask=True,
        limits=PAYLOAD_LIMITS,
    )


def call_from(event: Mapping[str, Any]) -> Call:
    """The core's :class:`Call` for a tool event."""
    tool_name = event.get("tool_name")
    tool_input = event.get("tool_input")
    return Call(
        cli=CLI,
        tool_name=tool_name if isinstance(tool_name, str) else "",
        tool_use_id=str(event.get("tool_use_id") or ""),
        payload=payload_for(str(tool_name or ""), tool_input if isinstance(tool_input, Mapping) else {}),
        session_id=str(event.get("session_id") or ""),
        result=event.get("tool_response", NO_RESULT),
    )


def standalone_notice(gate: HookGate) -> str:
    """What the person sees once per session when no credential is configured."""
    return (
        "Bindable Gate: this agent is recording locally to its local Track "
        f"({gate.local_track_path}) and is not connected to Bindable. Tool calls are "
        "allowed and not reviewed. Run bindable-gate install codex with your "
        "agent and base URL, and add the Gate credential to the keychain, to connect. "
        f"Signing: {signing_notice(gate.agent_key, connected=False, setting='agent_key_file')}."
    )


def connected_notice(gate: HookGate) -> str:
    """What the person sees once per session when the hook is connected."""
    return (
        "Bindable Gate: connected; every tool call is reviewed by Bindable in the "
        f"{gate.config.review_context} context for agent {gate.config.agent_shown}; "
        f"{signing_notice(gate.agent_key, connected=True, setting='agent_key_file')}."
    )


def handle(event: Mapping[str, Any], gate: HookGate) -> tuple[int, str]:
    """The exit code and the stderr text for one hook event."""
    name = event.get("hook_event_name")
    tool_name = event.get("tool_name")
    if isinstance(tool_name, str) and tool_name.startswith(SKIPPED_PREFIX):
        return 0, ""
    if name == "PreToolUse":
        if not isinstance(event.get("tool_input"), Mapping):
            return 2, "The hook input carries no tool input, so Action Gate blocked the tool."
        answer = gate.before(call_from(event))
        if isinstance(answer, Deny):
            return 2, answer.reason
        return 0, ""
    if name == "PostToolUse":
        gate.after(call_from(event))
    elif name == "Stop":
        gate.sweep(str(event.get("session_id") or ""))
    return 0, ""


def run(config_path: Path, stdin: TextIO, stderr: TextIO, stdout: TextIO | None = None) -> int:
    """One hook event from ``stdin``. Never raises: in ``PreToolUse`` any
    error is exit 2 with one sentence; elsewhere it is exit 0."""
    try:
        event = json.load(stdin)
    except ValueError:
        event = None
    if not isinstance(event, dict):
        stderr.write("The hook input could not be read as JSON, so Action Gate blocked the tool.\n")
        return 2
    blocking = event.get("hook_event_name") == "PreToolUse"
    try:
        gate = HookGate(HookConfig.load(config_path), cli=CLI, deadline_seconds=DEADLINE_SECONDS)
        if event.get("hook_event_name") == "SessionStart":
            notice = standalone_notice(gate) if gate.standalone else connected_notice(gate)
            (stdout or sys.stdout).write(json.dumps({"systemMessage": notice}) + "\n")
            return 0
        code, text = handle(event, gate)
        if code == 0 and gate.last_notice:
            # Offline mode's line for the person, beside an allowed call.
            (stdout or sys.stdout).write(json.dumps({"systemMessage": gate.last_notice}) + "\n")
    except ConfigError as error:
        if event.get("hook_event_name") == "SessionStart":
            (stdout or sys.stdout).write(json.dumps({"systemMessage": f"Bindable Gate: configuration invalid. {error}"}) + "\n")
            return 0
        code, text = (2 if blocking else 0), str(error)
    except Exception as error:  # noqa: BLE001 - fail closed before a tool runs
        code, text = (2 if blocking else 0), (
            f"The agent hook failed with {type(error).__name__}, so Action Gate blocked the tool."
            if blocking else ""
        )
    if text:
        stderr.write(text.rstrip() + "\n")
    return code


def main(config_path: Path) -> int:
    return run(config_path, sys.stdin, sys.stderr, sys.stdout)
