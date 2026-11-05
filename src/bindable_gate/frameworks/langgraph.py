# bindable-gate/src/bindable_gate/frameworks/langgraph.py
"""The Gate for LangGraph and LangChain agents.

    pip install "bindable-gate[langgraph]"

Two lines, with ``langchain.agents.create_agent``::

    from bindable_gate.frameworks.langgraph import BindableMiddleware
    agent = create_agent(model, tools, middleware=[BindableMiddleware()])

or with LangGraph's prebuilt ``ToolNode`` (``create_react_agent``, or a graph
of your own)::

    from bindable_gate.frameworks.langgraph import gated_tool_node
    agent = create_react_agent(model, gated_tool_node(tools))

The hook is the tool node's ``wrap_tool_call`` / ``awrap_tool_call``, which
``create_agent`` middleware also reaches (langgraph-prebuilt 1.0.2,
langchain 1.0). It runs around every tool call the agent's model makes, sync and
async, including a call naming a tool that does not exist. Each call is proposed
in the agent's did:webvh and runs only on a release; anything else hands the
model a ``ToolMessage`` with ``status="error"`` in the tool's place.

Not covered, because no tool node sees them: a tool the model provider runs
itself (a server-side web search, say), and a tool your own code invokes
directly inside a node of its own. Route those through a ToolNode, or through
:class:`~bindable_gate.library.Gate` yourself.

Settings: ``BINDABLE_API_BASE_URL``, ``BINDABLE_GATE_CREDENTIAL``,
``BINDABLE_AGENT_ID`` (the agent's did:webvh), ``BINDABLE_REVIEW_CONTEXT``
(``test`` or ``live``), and optionally ``BINDABLE_AGENT_KEY_FILE`` with
``BINDABLE_AGENT_KEY_ID`` to sign every Proposal.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Final

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt import ToolNode

from bindable_gate.frameworks._core import Decision, FrameworkGate

FRAMEWORK: Final[str] = "langgraph"
DISTRIBUTION: Final[str] = "langgraph"


def framework_gate(gate: FrameworkGate | None = None) -> FrameworkGate:
    """The Gate given, or one configured from the environment."""
    return gate if gate is not None else FrameworkGate.from_env(
        framework=FRAMEWORK, distribution=DISTRIBUTION
    )


class _Wrapper:
    """``wrap_tool_call`` and ``awrap_tool_call``, as ToolNode and middleware
    both call them: with the request and the function that runs the tool."""

    def __init__(self, gate: FrameworkGate) -> None:
        self.gate = gate

    def wrap(self, request: Any, execute: Callable[[Any], Any]) -> Any:
        call = request.tool_call
        decision = self.gate.decide(str(call.get("name", "")), call.get("args"))
        if not decision.released:
            self.gate.enforced(decision)
            return _blocked(call, decision)
        try:
            return execute(request)
        finally:
            self.gate.enforced(decision)

    async def awrap(self, request: Any, execute: Callable[[Any], Awaitable[Any]]) -> Any:
        call = request.tool_call
        decision = await self.gate.adecide(str(call.get("name", "")), call.get("args"))
        if not decision.released:
            self.gate.enforced(decision)
            return _blocked(call, decision)
        try:
            return await execute(request)
        finally:
            self.gate.enforced(decision)


def gated_tool_node(
    tools: Sequence[Any], *, gate: FrameworkGate | None = None, **options: Any
) -> ToolNode:
    """A ``ToolNode`` over ``tools`` whose every call passes the Gate first.

    ``options`` go to ``ToolNode`` as they are, except the two wrappers, which
    are the Gate's.
    """
    wrapper = _Wrapper(framework_gate(gate))
    return ToolNode(
        list(tools), wrap_tool_call=wrapper.wrap, awrap_tool_call=wrapper.awrap, **options
    )


class BindableMiddleware(AgentMiddleware):  # type: ignore[misc,unused-ignore]
    """``create_agent`` middleware that passes every tool call through the Gate."""

    def __init__(self, gate: FrameworkGate | None = None) -> None:
        super().__init__()
        self._wrapper = _Wrapper(framework_gate(gate))

    @property
    def gate(self) -> FrameworkGate:
        return self._wrapper.gate

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._wrapper.wrap(request, handler)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return await self._wrapper.awrap(request, handler)


def _blocked(call: Any, decision: Decision) -> ToolMessage:
    """What the model is told in the tool's place, under the same call id."""
    return ToolMessage(
        content=decision.message,
        name=str(call.get("name", "")),
        tool_call_id=str(call.get("id") or ""),
        status="error",
    )


__all__ = ["BindableMiddleware", "framework_gate", "gated_tool_node"]
