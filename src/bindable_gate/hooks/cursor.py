# bindable-gate/src/bindable_gate/hooks/cursor.py
"""The Cursor adapter for the hook Gate: the Cursor agent and ``cursor-agent``.

Cursor runs each hook in ``hooks.json`` with the event as JSON on standard
input (https://cursor.com/docs/hooks). ``preToolUse`` fires for every tool:
Shell, Read, Write, Grep, Delete, Task and MCP tools. Its input carries
``tool_name``, ``tool_input``, ``tool_use_id`` and ``conversation_id``.

- **Block:** exit 2, which Cursor reads as ``permission: "deny"``, with
  ``{"permission": "deny", "user_message": …, "agent_message": …}``.
- **Allow:** ``{"permission": "allow"}`` and nothing else. The hook entries
  set ``failClosed: true``, so Cursor blocks on a crash, a timeout, a non-zero
  exit or no output, and a pass must say ``allow``. Cursor's docs do not say
  whether ``allow`` also skips Cursor's own approval prompt; ``ask`` is not
  enforced for ``preToolUse``.
- **Never rewrites:** no answer carries ``updated_input``.
- **Report:** ``postToolUse`` and ``postToolUseFailure`` report ``released``;
  ``stop`` and ``sessionEnd`` report ``blocked`` for a call that passed review
  and never ran.
- **Startup line:** ``sessionStart`` answers ``additional_context``, the one
  output it documents; Cursor documents no message shown to the person.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, TextIO

from bindable_gate.hooks import runtime
from bindable_gate.hooks.core import HookGate

CLI: Final[str] = "cursor"
HOOK_TIMEOUT_SECONDS: Final[int] = runtime.HOOK_TIMEOUT_SECONDS
DEADLINE_SECONDS: Final[float] = runtime.DEADLINE_SECONDS

EVENTS: Final[tuple[str, ...]] = (
    "preToolUse", "postToolUse", "postToolUseFailure", "stop", "sessionEnd", "sessionStart",
)


def _block(reason: str) -> runtime.Answer:
    return 2, json.dumps({"permission": "deny", "user_message": reason, "agent_message": reason}), ""


SPEC: Final[runtime.Spec] = runtime.Spec(
    cli=CLI,
    title="Cursor",
    before=frozenset({"preToolUse"}),
    after=frozenset({"postToolUse", "postToolUseFailure"}),
    ending=frozenset({"stop", "sessionEnd"}),
    start=frozenset({"sessionStart"}),
    payload_fields={
        "Shell": ("command",),
        "Read": ("file_path", "path"),
        "Write": ("file_path", "path"),
        "Delete": ("file_path", "path"),
        "Grep": ("pattern", "path"),
    },
    allow=lambda: (0, json.dumps({"permission": "allow"}), ""),
    block=_block,
    notice=lambda text: (0, json.dumps({"additional_context": text}), ""),
    allow_notice=lambda text: (0, json.dumps({"permission": "allow", "user_message": text}), ""),
    session_fields=("conversation_id", "session_id"),
    result_field="tool_output",
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
