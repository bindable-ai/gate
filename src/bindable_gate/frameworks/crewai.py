# bindable-gate/src/bindable_gate/frameworks/crewai.py
"""The Gate for CrewAI.

    pip install "bindable-gate[crewai]"

Two lines, once, before any crew or agent is kicked off::

    from bindable_gate.frameworks.crewai import install
    install()

The hook is CrewAI's tool-call interception (``crewai.hooks``): a
``PRE_TOOL_CALL`` hook runs before every tool call any agent makes — native
function calling and the text (ReAct) path, ``@tool`` functions, ``BaseTool``
subclasses, MCP tools and delegation tools — in ``Agent.kickoff`` and
``Crew.kickoff`` alike. CrewAI's hooks are process-wide, so :func:`install`
protects every agent in the process, and installing twice installs once.

Each call is proposed in the agent's did:webvh and runs only on a release. A
block raises ``HookAborted``, which skips the tool; a ``POST_TOOL_CALL`` hook
then hands the model the Gate's reason and Receipt in place of CrewAI's own
fixed "blocked" line.

**CrewAI runs the tool when a hook raises anything but** ``HookAborted``. Every
fault here is therefore turned into a ``HookAborted``: the one exception that
blocks.

Not covered: a tool your code calls directly (``tool.run()``), an LLM called
directly with ``available_functions``, and anything after
``crewai.hooks.clear_all_hooks()`` — which removes the Gate with every other
hook. CrewAI gives a hook no call id, so the Proposal carries none.

The outcome is reported when the Gate enforces it: blocked, or released as the
tool is handed back to run.

Settings: ``BINDABLE_API_BASE_URL``, ``BINDABLE_GATE_CREDENTIAL``,
``BINDABLE_AGENT_ID`` (the agent's did:webvh), ``BINDABLE_REVIEW_CONTEXT``, and
optionally ``BINDABLE_AGENT_KEY_FILE`` with ``BINDABLE_AGENT_KEY_ID``.
"""

from __future__ import annotations

import threading
from typing import Any, Final

from crewai.hooks import HookAborted, InterceptionPoint, on, unregister_hook

from bindable_gate.frameworks._core import FrameworkGate, decision_unavailable

FRAMEWORK: Final[str] = "crewai"
DISTRIBUTION: Final[str] = "crewai"
SOURCE: Final[str] = "bindable-action-gate"


class _Installed:
    """The one Gate the process's hooks answer with, and the hooks themselves."""

    def __init__(self, gate: FrameworkGate) -> None:
        self.gate = gate
        # What the model is told in place of a blocked call, from the pre hook
        # to the post hook. CrewAI hands both the same arguments dict.
        self.blocked: dict[int, str] = {}
        self.lock = threading.Lock()
        # Registered and unregistered as these same two plain functions: CrewAI
        # marks a hook with an attribute, which a bound method cannot take.

        def pre(context: Any) -> None:
            self.before(context)

        def post(context: Any) -> str | None:
            return self.after(context)

        self.pre = pre
        self.post = post

    def before(self, context: Any) -> None:
        name = str(getattr(context, "tool_name", "") or "")
        arguments = getattr(context, "tool_input", None)
        try:
            decision = self.gate.decide(name, arguments)
        except Exception:  # noqa: BLE001 - only HookAborted blocks in CrewAI
            decision = decision_unavailable(name, "The Gate could not decide.")
        self.gate.enforced(decision)
        if decision.released:
            return
        with self.lock:
            self.blocked[id(arguments)] = decision.message
        raise HookAborted(reason=decision.message, source=SOURCE)

    def after(self, context: Any) -> str | None:
        with self.lock:
            return self.blocked.pop(id(getattr(context, "tool_input", None)), None)


_lock = threading.Lock()
_installed: _Installed | None = None


def install(gate: FrameworkGate | None = None) -> FrameworkGate:
    """Hold every CrewAI tool call in this process for the Gate. Idempotent.

    Returns the Gate, configured from the environment unless one is given, so
    it can be closed at shutdown.
    """
    global _installed
    with _lock:
        if _installed is not None:
            return _installed.gate
        held = gate if gate is not None else FrameworkGate.from_env(
            framework=FRAMEWORK, distribution=DISTRIBUTION
        )
        installed = _Installed(held)
        on(InterceptionPoint.PRE_TOOL_CALL)(installed.pre)
        on(InterceptionPoint.POST_TOOL_CALL)(installed.post)
        _installed = installed
        return held


def uninstall() -> None:
    """Remove the Gate's hooks, and close its Gate."""
    global _installed
    with _lock:
        if _installed is None:
            return
        unregister_hook(InterceptionPoint.PRE_TOOL_CALL, _installed.pre)
        unregister_hook(InterceptionPoint.POST_TOOL_CALL, _installed.post)
        _installed.gate.close()
        _installed = None


__all__ = ["install", "uninstall"]
