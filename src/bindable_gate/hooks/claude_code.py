# bindable-gate/src/bindable_gate/hooks/claude_code.py
"""The Claude Code adapter for the Agent Hook Gate.

Claude Code runs one command for each hook event and passes the event as JSON
on standard input (https://code.claude.com/docs/en/hooks). This adapter maps
the events to the core:

==================== ============================================ ==========
Event                The core                                      Exit
==================== ============================================ ==========
PreToolUse           ``before``: a pass returns no decision        0
                     a deny or any error: one sentence on stderr   2
PostToolUse          ``after``: report ``released``                0
PostToolUseFailure   ``after``: the tool ran and failed, which     0
                     the Gate released, so ``released``
PermissionDenied     ``withheld``: auto mode refused the call      0
Stop, SessionEnd     ``sweep``: a call refused at the permission   0
                     prompt sends no event, so report ``blocked``
                     for every call of the session still waiting
SessionStart         tell the person, once per session, whether   0
                     calls are reviewed or recorded locally, and
                     whether they are signed
==================== ============================================ ==========

Standalone (no Gate credential configured), ``PreToolUse`` allows every call
and ``PostToolUse`` / ``PostToolUseFailure`` append it to the local Track with
its named fields, its result and how long it ran.

A pass returns no decision, never ``allow``: in Claude Code ``allow`` skips the
permission prompt, and the hook decides only whether Action Review released the
call. Exit 2 blocks the tool whatever else the hook printed. Claude Code lets a
tool through when a hook exits with any other code or times out, so this
adapter answers within :data:`DEADLINE_SECONDS`, under the
:data:`HOOK_TIMEOUT_SECONDS` the managed settings give it, and turns every
error in ``PreToolUse`` into exit 2.

Tools whose names start with ``mcp__bindable`` are skipped: the hosted MCP Gate
already reviews them.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, TextIO

from bindable_gate.contract.local_track import NO_RESULT
from bindable_gate.contract.signing import signing_notice
from bindable_gate.hooks.config import ConfigError, HookConfig
from bindable_gate.hooks.core import Call, Deny, HookGate
from bindable_gate.library.payload import named_fields

CLI: Final[str] = "claude-code"

#: The ``timeout`` each hook entry carries in the managed settings, in seconds.
HOOK_TIMEOUT_SECONDS: Final[int] = 30

#: The hook's own deadline, under :data:`HOOK_TIMEOUT_SECONDS`.
DEADLINE_SECONDS: Final[float] = 25.0

#: Tools the hosted MCP Gate already reviews.
SKIPPED_PREFIX: Final[str] = "mcp__bindable"

#: The named fields a Proposal carries, by Claude Code tool. Any
#: other tool proposes with an empty payload. File contents, ``old_string``,
#: ``new_string``, prompts and every other field stay on the computer.
PAYLOAD_FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    "Bash": ("command",),
    "Read": ("file_path",),
    "Edit": ("file_path",),
    "Write": ("file_path",),
    "NotebookEdit": ("notebook_path",),
    "Glob": ("pattern", "path"),
    "Grep": ("pattern", "path"),
    "WebFetch": ("url",),
    "WebSearch": ("query",),
}

#: The most characters of a field a Proposal carries.
PAYLOAD_LIMITS: Final[Mapping[str, int]] = {"command": 200}

EVENTS: Final[tuple[str, ...]] = (
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionDenied",
    "Stop",
    "SessionEnd",
    "SessionStart",
)
"""The events the managed settings send to this adapter."""


def payload_for(tool_name: str, tool_input: Mapping[str, Any]) -> dict[str, Any]:
    """The Proposal's payload for one Claude Code tool call: its named fields,
    credentials masked, then cut."""
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
    failed = event.get("hook_event_name") == "PostToolUseFailure"
    duration = event.get("duration_ms")
    return Call(
        cli=CLI,
        tool_name=tool_name if isinstance(tool_name, str) else "",
        tool_use_id=str(event.get("tool_use_id") or ""),
        payload=payload_for(str(tool_name or ""), tool_input if isinstance(tool_input, Mapping) else {}),
        session_id=str(event.get("session_id") or ""),
        result=event.get("tool_response", NO_RESULT),
        error=str(event.get("error") or "the tool failed") if failed else None,
        duration_ms=duration if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None,
    )


def standalone_notice(gate: HookGate) -> str:
    """What the person sees once per session when no credential is configured."""
    return (
        "Bindable Gate: this agent is recording locally to its local Track "
        f"({gate.local_track_path}) and is not connected to Bindable. Tool calls are "
        "allowed and not reviewed. Run bindable-gate install claude-code with your "
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
    if name in ("PostToolUse", "PostToolUseFailure"):
        gate.after(call_from(event))
    elif name == "PermissionDenied":
        gate.withheld(call_from(event))
    elif name in ("Stop", "SessionEnd"):
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
