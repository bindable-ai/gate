# bindable-gate/src/bindable_gate/hooks/gemini_cli.py
"""The Gemini CLI adapter for the hook Gate.

Gemini CLI runs each command hook in ``settings.json`` with the event as JSON
on standard input (google-gemini/gemini-cli, ``docs/hooks/reference.md``).
``BeforeTool`` runs before every tool, built-in, web search and MCP alike,
and before Gemini CLI's own policy and approval check. Its input carries
``tool_name``, ``tool_input`` and ``session_id``, and no call id, so a
released call waits in the session's pending list for its ``AfterTool``.

- **Block:** exit 2 with the reason on standard error and nothing on standard
  output. Gemini CLI reads JSON on standard output in place of the exit code,
  so none is printed.
- **Allow:** exit 0 and no output. Gemini CLI's own policy and confirmation
  still apply.
- **Fail closed:** Gemini CLI lets the tool run when a hook times out,
  crashes, or exits with no output (``packages/core/src/hooks/hookRunner.ts``).
  So the hook answers within 25 seconds, under the 30-second timeout its
  entries carry, always writes a reason with exit 2, and the installed
  command turns any other exit into exit 2 with a reason.
- **Never rewrites:** no answer carries ``hookSpecificOutput.tool_input``.
- **Report:** ``AfterTool`` reports ``released``; ``AfterAgent`` and
  ``SessionEnd`` report ``blocked`` for a call that passed review and never ran.
- **Startup line:** ``SessionStart`` answers ``systemMessage``, which Gemini
  CLI shows at the start of the session.

Tools whose names start with ``mcp_bindable_`` are skipped: the hosted MCP
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

CLI: Final[str] = "gemini-cli"
HOOK_TIMEOUT_SECONDS: Final[int] = runtime.HOOK_TIMEOUT_SECONDS
DEADLINE_SECONDS: Final[float] = runtime.DEADLINE_SECONDS

EVENTS: Final[tuple[str, ...]] = ("BeforeTool", "AfterTool", "AfterAgent", "SessionEnd", "SessionStart")

SPEC: Final[runtime.Spec] = runtime.Spec(
    cli=CLI,
    title="Gemini CLI",
    before=frozenset({"BeforeTool"}),
    after=frozenset({"AfterTool"}),
    ending=frozenset({"AfterAgent", "SessionEnd"}),
    start=frozenset({"SessionStart"}),
    payload_fields={
        "run_shell_command": ("command",),
        "read_file": ("file_path", "absolute_path"),
        "read_many_files": ("paths",),
        "write_file": ("file_path",),
        "replace": ("file_path",),
        "list_directory": ("dir_path", "path"),
        "glob": ("pattern", "path", "dir_path"),
        "search_file_content": ("pattern", "path", "dir_path"),
        "google_web_search": ("query",),
    },
    allow=lambda: (0, "", ""),
    block=lambda reason: (2, "", reason),
    notice=lambda text: (0, json.dumps({"systemMessage": text}), ""),
    allow_notice=lambda text: (0, json.dumps({"systemMessage": text}), ""),
    call_id_field=None,
    skipped_prefixes=("mcp_bindable_",),
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
