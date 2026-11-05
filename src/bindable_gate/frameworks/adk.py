# bindable-gate/src/bindable_gate/frameworks/adk.py
"""The Gate for Google's Agent Development Kit.

    pip install "bindable-gate[adk]"

Two lines, for every agent a runner runs::

    from bindable_gate.frameworks.adk import BindablePlugin
    runner = InMemoryRunner(agent=root_agent, app_name="app", plugins=[BindablePlugin()])

(``Runner(..., plugins=[...])`` and ``App(plugins=[...])`` take it the same
way.) Or for one agent only::

    from bindable_gate.frameworks.adk import before_tool_callback
    agent = Agent(..., before_tool_callback=before_tool_callback())

The hook is ADK's ``before_tool_callback``: on a plugin it runs before every
tool call of every agent the runner runs — function tools, MCP toolset tools,
``AgentTool`` sub-agents and their own tools, ``transfer_to_agent`` — and before
any agent's own callbacks. Returning ``None`` lets the tool run; returning a
dict skips it and the dict is the function response the model sees. Each call
is proposed in the agent's did:webvh and runs only on a release.

The plugin is the one to use: the per-agent callback does not reach sub-agents,
nor ADK 2's workflow tool nodes. Not covered either way: Gemini's built-in tools
(Google Search, code execution, Vertex AI Search, URL context), which run inside
the model's API; a remote A2A agent's tools, which run in its own process; and
an ``AgentTool(include_plugins=False)`` sub-agent.

ADK offers no hook after every ending of a tool, so the outcome is reported when
the Gate enforces it: blocked, or released as the tool is handed back to run.

Settings: ``BINDABLE_API_BASE_URL``, ``BINDABLE_GATE_CREDENTIAL``,
``BINDABLE_AGENT_ID`` (the agent's did:webvh), ``BINDABLE_REVIEW_CONTEXT``, and
optionally ``BINDABLE_AGENT_KEY_FILE`` with ``BINDABLE_AGENT_KEY_ID``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

from google.adk.plugins.base_plugin import BasePlugin

from bindable_gate.frameworks._core import Decision, FrameworkGate, decision_unavailable

FRAMEWORK: Final[str] = "adk"
DISTRIBUTION: Final[str] = "google-adk"
PLUGIN_NAME: Final[str] = "bindable_action_gate"


def framework_gate(gate: FrameworkGate | None = None) -> FrameworkGate:
    """The Gate given, or one configured from the environment."""
    return gate if gate is not None else FrameworkGate.from_env(
        framework=FRAMEWORK, distribution=DISTRIBUTION
    )


async def _checked(gate: FrameworkGate, tool: Any, args: Any) -> dict[str, Any] | None:
    """``None`` to let the tool run, or the response the model gets instead.

    Never raises: an exception from a plugin aborts the whole run in ADK.
    """
    name = str(getattr(tool, "name", "") or "")
    try:
        decision = await gate.adecide(name, dict(args) if isinstance(args, Mapping) else args)
    except Exception:  # noqa: BLE001 - adecide does not raise; this holds if it ever does
        decision = decision_unavailable(name, "The Gate could not decide.")
    gate.enforced(decision)
    return None if decision.released else _blocked(decision)


class BindablePlugin(BasePlugin):  # type: ignore[misc,unused-ignore]
    """A runner plugin that passes every tool call through the Gate."""

    def __init__(self, gate: FrameworkGate | None = None, *, name: str = PLUGIN_NAME) -> None:
        super().__init__(name=name)
        self.gate = framework_gate(gate)

    async def before_tool_callback(
        self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any
    ) -> dict[str, Any] | None:
        return await _checked(self.gate, tool, tool_args)


def before_tool_callback(
    gate: FrameworkGate | None = None,
) -> Callable[[Any, dict[str, Any], Any], Awaitable[dict[str, Any] | None]]:
    """An agent's ``before_tool_callback`` that passes its tool calls through
    the Gate. Prefer :class:`BindablePlugin`, which also reaches sub-agents."""
    held = framework_gate(gate)

    async def callback(tool: Any, args: dict[str, Any], tool_context: Any) -> dict[str, Any] | None:
        return await _checked(held, tool, args)

    return callback


def _blocked(decision: Decision) -> dict[str, Any]:
    """The function response the model sees in a blocked tool's place."""
    response: dict[str, Any] = {
        "error": decision.message,
        "blocked_by": PLUGIN_NAME,
    }
    if decision.receipt_id:
        response["receipt_id"] = decision.receipt_id
    return response


__all__ = ["BindablePlugin", "before_tool_callback", "framework_gate"]
