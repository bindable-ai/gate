# bindable-gate/src/bindable_gate/hooks/__init__.py
"""The Agent Hook Gate: the Library Gate in front of an agent CLI's tool calls.

One CLI-neutral core, ``core``, and one adapter per CLI: Claude Code, Codex
CLI and GitHub Copilot CLI. The administrator installs the hook into the
CLI's managed settings, where a session cannot change it, and keeps
the Gate credential in the macOS keychain, never in a file.

    python -m bindable_gate hooks install --cli claude-code ...
    python -m bindable_gate hooks install --cli copilot-cli ...
    bindable-gate install codex ...
"""

from bindable_gate.hooks.core import (
    REPORT_GATE_KIND,
    Allow,
    Call,
    Deny,
    HookGate,
    Report,
)

__all__ = ["REPORT_GATE_KIND", "Allow", "Call", "Deny", "HookGate", "Report"]
