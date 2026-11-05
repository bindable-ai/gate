# bindable-gate/src/bindable_gate/frameworks/autogen.py
"""The Gate for AutoGen AgentChat.

    pip install "bindable-gate[autogen]"

Two lines::

    from bindable_gate.frameworks.autogen import protect
    agent = protect(AssistantAgent("assistant", model_client=client, tools=[...]))

or, without touching the agent after it is built::

    from bindable_gate.frameworks.autogen import GatedWorkbench
    agent = AssistantAgent("assistant", model_client=client,
                           workbench=GatedWorkbench(StaticWorkbench(tools)))

AutoGen has no hook made for this. What it has is the ``Workbench``: an
``AssistantAgent`` sends every tool call that is not a handoff through its
workbenches' ``call_tool`` or ``call_tool_stream`` — plain tools through a
``StaticWorkbench``, MCP tools through an ``McpWorkbench``. :class:`GatedWorkbench`
wraps any workbench and holds each call for the Gate: proposed in the agent's
did:webvh, run only on a release, and otherwise answered with an error result
the model sees under the same call id. :func:`protect` wraps an agent's
workbenches in place; it reaches into ``AssistantAgent._workbench``, a list
since AutoGen 0.6, which is why the ``workbench=`` form is offered beside it.

**AutoGen is no longer maintained** — Microsoft archived it at 0.7.5 and points
new work at Microsoft Agent Framework. This adapter is pinned to 0.7.x for
customers already on it.

Not covered: handoffs, which AutoGen runs without a workbench; code executors
(``CodeExecutorAgent``); ``OpenAIAssistantAgent``, whose tools run at OpenAI.
For MCP tools, ``autogen-ext[mcp]`` 0.7.5 needs ``mcp<2`` installed beside it.

Settings: ``BINDABLE_API_BASE_URL``, ``BINDABLE_GATE_CREDENTIAL``,
``BINDABLE_AGENT_ID`` (the agent's did:webvh), ``BINDABLE_REVIEW_CONTEXT``, and
optionally ``BINDABLE_AGENT_KEY_FILE`` with ``BINDABLE_AGENT_KEY_ID``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from typing import Any, Final

from autogen_core import CancellationToken
from autogen_core.tools import TextResultContent, ToolResult, ToolSchema, Workbench

from bindable_gate.frameworks._core import Decision, FrameworkGate

FRAMEWORK: Final[str] = "autogen"
DISTRIBUTION: Final[str] = "autogen-agentchat"


def framework_gate(gate: FrameworkGate | None = None) -> FrameworkGate:
    """The Gate given, or one configured from the environment."""
    return gate if gate is not None else FrameworkGate.from_env(
        framework=FRAMEWORK, distribution=DISTRIBUTION
    )


class GatedWorkbench(Workbench):  # type: ignore[misc,unused-ignore]
    """Any workbench, with every tool call held for the Gate first."""

    def __init__(self, inner: Workbench, gate: FrameworkGate | None = None) -> None:
        self.inner = inner
        self.gate = framework_gate(gate)

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        cancellation_token: CancellationToken | None = None,
        call_id: str | None = None,
    ) -> ToolResult:
        decision = await self.gate.adecide(name, dict(arguments or {}))
        if not decision.released:
            self.gate.enforced(decision)
            return _blocked(decision)
        try:
            result: ToolResult = await self.inner.call_tool(
                name, arguments, cancellation_token, call_id
            )
            return result
        finally:
            self.gate.enforced(decision)

    async def call_tool_stream(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        cancellation_token: CancellationToken | None = None,
        call_id: str | None = None,
    ) -> AsyncGenerator[Any, None]:
        decision = await self.gate.adecide(name, dict(arguments or {}))
        if not decision.released:
            self.gate.enforced(decision)
            yield _blocked(decision)
            return
        try:
            # Only a streaming workbench streams; any other answers in one piece.
            stream = getattr(self.inner, "call_tool_stream", None)
            if stream is None:
                yield await self.inner.call_tool(name, arguments, cancellation_token, call_id)
            else:
                async for event in stream(name, arguments, cancellation_token, call_id):
                    yield event
        finally:
            self.gate.enforced(decision)

    # Everything else is the wrapped workbench's own.

    async def list_tools(self) -> list[ToolSchema]:
        tools: list[ToolSchema] = await self.inner.list_tools()
        return tools

    async def start(self) -> None:
        await self.inner.start()

    async def stop(self) -> None:
        await self.inner.stop()

    async def reset(self) -> None:
        await self.inner.reset()

    async def save_state(self) -> Mapping[str, Any]:
        state: Mapping[str, Any] = await self.inner.save_state()
        return state

    async def load_state(self, state: Mapping[str, Any]) -> None:
        await self.inner.load_state(state)

    def _to_config(self) -> Any:
        return self.inner._to_config()


def protect(agent: Any, gate: FrameworkGate | None = None) -> Any:
    """Wrap every workbench of an ``AssistantAgent`` in a :class:`GatedWorkbench`,
    once, and return the agent."""
    held = framework_gate(gate)
    workbenches = getattr(agent, "_workbench", None)
    if not isinstance(workbenches, list):
        raise TypeError(
            "protect() takes an AssistantAgent; for any other agent, give it "
            "workbench=GatedWorkbench(...)"
        )
    agent._workbench = [
        bench if isinstance(bench, GatedWorkbench) else GatedWorkbench(bench, held)
        for bench in workbenches
    ]
    return agent


def gate_of(agent: Any) -> FrameworkGate | None:
    """The Gate on ``agent``'s workbenches, to close it at shutdown."""
    for bench in getattr(agent, "_workbench", None) or []:
        if isinstance(bench, GatedWorkbench):
            return bench.gate
    return None


def _blocked(decision: Decision) -> ToolResult:
    """What the model is handed in a blocked tool's place."""
    return ToolResult(
        name=decision.tool_name,
        result=[TextResultContent(content=decision.message)],
        is_error=True,
    )


__all__ = ["GatedWorkbench", "framework_gate", "gate_of", "protect"]
