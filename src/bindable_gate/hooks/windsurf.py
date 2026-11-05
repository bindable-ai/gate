# bindable-gate/src/bindable_gate/hooks/windsurf.py
"""The Windsurf adapter for the hook Gate: Devin Local, Windsurf's agent now.

Windsurf is Devin Desktop. Its Cascade agent no longer starts or opens
conversations; new ones start with Devin Local, which shares Devin CLI's agent
harness and its hooks (https://docs.devin.ai/cli/extensibility/hooks). So
this Gate hooks Devin Local. Cascade's own ``pre_*`` hooks are not used.

``PreToolUse`` runs before every tool (``exec``, ``read``, ``write``,
``edit``, ``apply_patch``, ``grep``, ``glob``, ``webfetch``, MCP tools). Its
input carries ``tool_name``, ``tool_input`` and ``session_id``; no call id is
documented, so a released call waits in the session's pending list for its
``PostToolUse``.

- **Block:** exit 2 with the reason on standard error.
- **Allow:** exit 0 and no output, so Devin's own permission prompt applies.
- **Fail closed:** Devin logs a hook that errors, times out or answers
  unreadably and lets the tool run, and documents no fail-closed option. So
  the hook answers within 25 seconds, under the 30-second timeout its entries
  carry, and the installed command turns any other exit into exit 2.
- **Never rewrites:** no answer carries ``updatedInput``.
- **Report:** ``PostToolUse`` reports ``released``; ``Stop`` and
  ``SessionEnd`` report ``blocked`` for a call that passed review and never ran.
- **Startup line:** ``SessionStart`` answers ``additionalContext``, the one
  output it documents; it reaches the agent, and Devin documents no message
  shown to the person.

Tools whose names start with ``mcp__bindable`` are skipped: the hosted MCP
Gate already reviews them.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, TextIO

from bindable_gate.hooks import runtime
from bindable_gate.hooks.core import HookGate

CLI: Final[str] = "windsurf"
HOOK_TIMEOUT_SECONDS: Final[int] = runtime.HOOK_TIMEOUT_SECONDS
DEADLINE_SECONDS: Final[float] = runtime.DEADLINE_SECONDS

EVENTS: Final[tuple[str, ...]] = ("PreToolUse", "PostToolUse", "Stop", "SessionEnd", "SessionStart")

SPEC: Final[runtime.Spec] = runtime.Spec(
    cli=CLI,
    title="Windsurf (Devin Local)",
    before=frozenset({"PreToolUse"}),
    after=frozenset({"PostToolUse"}),
    ending=frozenset({"Stop", "SessionEnd"}),
    start=frozenset({"SessionStart"}),
    payload_fields={
        "exec": ("command",),
        "read": ("file_path", "path"),
        "write": ("file_path", "path"),
        "edit": ("file_path", "path"),
        "grep": ("pattern", "path"),
        "glob": ("pattern", "path"),
        "webfetch": ("url",),
    },
    allow=lambda: (0, "", ""),
    block=lambda reason: (2, "", reason),
    notice=lambda text: (0, json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": text}}), ""),
    call_id_field=None,
    skipped_prefixes=("mcp__bindable",),
)


def connected_notice(gate: HookGate) -> str:
    return runtime.notice_for(SPEC, gate)


def handle(event: Mapping[str, Any], gate: HookGate) -> tuple[int, str]:
    code, out, err = runtime.handle(SPEC, event, gate)
    return code, out or err


def run(config_path: Path, stdin: TextIO, stderr: TextIO, stdout: TextIO | None = None) -> int:
    return runtime.run(SPEC, config_path, stdin, stderr, stdout)


def main(config_path: Path) -> int:
    return run(config_path, sys.stdin, sys.stderr, sys.stdout)
