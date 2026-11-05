# bindable-gate/src/bindable_gate/frameworks/agent_framework.py
"""The Gate for Microsoft Agent Framework (``agent-framework-core``).

    pip install "bindable-gate[agent-framework]"

Two lines::

    from bindable_gate.frameworks.agent_framework import protect
    agent = protect(Agent(client=client, name="assistant", tools=[...]))

The hook is Agent Framework's function middleware (``FunctionMiddleware``): the
chat client's function-invocation loop runs it around every tool call the model
makes, a ``@tool`` function and an MCP server's tool alike, and the tool runs
only if the middleware calls ``call_next()``. The Gate's middleware proposes the
call in the agent's did:webvh, calls ``call_next()`` only on a release, and
otherwise sets the call's result to the Gate's reason, which is what the model
sees for that call. :func:`protect` adds it to the agent's own middleware, last,
so it sits closest to the tool of everything the agent was built with.

A client that does not put its function-invocation layer outside its chat
middleware layer (``FunctionInvocationLayer`` before ``ChatMiddlewareLayer``, as
every client the framework ships does) hands function middleware to the chat
pipeline, which drops it with a warning, and the tool runs ungated. A client
with no function-invocation layer leaves tool calls to your own code.
:func:`protect` refuses both, at startup, rather than leave a Gate that never
runs.

``BindableMiddleware()`` can also go straight into ``Agent(middleware=[...])``,
without that check.

Not covered: tools the model provider runs itself (code interpreter, file
search, web search, hosted MCP); a tool invoked directly with
``FunctionTool.invoke``; an agent used as another agent's tool, whose own tools
are gated only if that agent is protected too; function middleware passed to
``agent.run(middleware=...)`` or set on the client, which runs inside the Gate
and could change the arguments after the review; and an agent in a workflow
that was not protected.

Settings: ``BINDABLE_API_BASE_URL``, ``BINDABLE_GATE_CREDENTIAL``,
``BINDABLE_AGENT_ID`` (the agent's did:webvh), ``BINDABLE_REVIEW_CONTEXT``, and
optionally ``BINDABLE_AGENT_KEY_FILE`` with ``BINDABLE_AGENT_KEY_ID``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

from agent_framework import (
    ChatMiddlewareLayer,
    FunctionInvocationContext,
    FunctionInvocationLayer,
    FunctionMiddleware,
)
from pydantic import BaseModel

from bindable_gate.core.errors import GateConfigError
from bindable_gate.frameworks._core import FrameworkGate, decision_unavailable

FRAMEWORK: Final[str] = "agent-framework"
DISTRIBUTION: Final[str] = "agent-framework-core"


def framework_gate(gate: FrameworkGate | None = None) -> FrameworkGate:
    """The Gate given, or one configured from the environment."""
    return gate if gate is not None else FrameworkGate.from_env(
        framework=FRAMEWORK, distribution=DISTRIBUTION
    )


class BindableMiddleware(FunctionMiddleware):  # type: ignore[misc,unused-ignore]
    """Function middleware that holds every tool call for the Gate.

    Never raises: an exception from function middleware becomes the tool's
    error result, and a Gate that cannot decide answers the model the same way
    a block does.
    """

    def __init__(self, gate: FrameworkGate | None = None) -> None:
        self.gate = framework_gate(gate)

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        name = str(getattr(context.function, "name", "") or "")
        try:
            decision = await self.gate.adecide(name, _arguments(context.arguments))
        except Exception:  # noqa: BLE001 - adecide does not raise; this holds if it ever does
            decision = decision_unavailable(name, "The Gate could not decide.")
        if not decision.released:
            self.gate.enforced(decision)
            context.result = decision.message
            return
        try:
            await call_next()
        finally:
            self.gate.enforced(decision)


def protect(agent: Any, gate: FrameworkGate | None = None) -> Any:
    """Hold every tool call ``agent`` makes for the Gate, and return it.

    Protecting an agent twice adds nothing.

    Raises:
        GateConfigError: The agent's client would not run function middleware.
    """
    if gate_of(agent) is not None:
        return agent
    _check_client(getattr(agent, "client", None))
    middleware = list(getattr(agent, "middleware", None) or [])
    agent.middleware = [*middleware, BindableMiddleware(gate)]
    return agent


def gate_of(agent: Any) -> FrameworkGate | None:
    """The Gate :func:`protect` put on ``agent``, to close it at shutdown."""
    for middleware in getattr(agent, "middleware", None) or []:
        if isinstance(middleware, BindableMiddleware):
            return middleware.gate
    return None


def _check_client(client: Any) -> None:
    """Refuse a client whose function-invocation loop would not run the Gate."""
    order = type(client).__mro__
    if FunctionInvocationLayer not in order:
        raise GateConfigError(
            f"{type(client).__name__} does not invoke tools itself, so the Gate "
            "would never see a tool call: use a client with a function-invocation layer"
        )
    if ChatMiddlewareLayer in order and order.index(ChatMiddlewareLayer) < order.index(
        FunctionInvocationLayer
    ):
        raise GateConfigError(
            f"{type(client).__name__} puts ChatMiddlewareLayer before FunctionInvocationLayer, "
            "so it would drop the Gate's function middleware and run every tool ungated"
        )


def _arguments(arguments: Any) -> Mapping[str, Any]:
    if isinstance(arguments, BaseModel):
        dumped: dict[str, Any] = arguments.model_dump(mode="json")
        return dumped
    if isinstance(arguments, Mapping):
        return dict(arguments)
    return {}


__all__ = ["BindableMiddleware", "framework_gate", "gate_of", "protect"]
