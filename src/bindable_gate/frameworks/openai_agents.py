# bindable-gate/src/bindable_gate/frameworks/openai_agents.py
"""The Gate for the OpenAI Agents SDK.

    pip install "bindable-gate[openai-agents]"

Two lines::

    from bindable_gate.frameworks.openai_agents import protect
    agent = protect(Agent(name="assistant", tools=[...], mcp_servers=[...]))

The hook is the SDK's tool input guardrail (``tool_input_guardrails``), which
runs before a tool's body: ``allow()`` lets it run, and ``reject_content(msg)``
skips it and hands the model ``msg`` as the tool's output under the same call
id. The SDK has no agent-wide tool guardrail, so :func:`protect` puts the Gate
first on every function tool of the agent, on every MCP server it uses
(``openai-agents`` 0.22.3 and later), and on every agent it can hand off to.
Each call is proposed in the agent's did:webvh and runs only on a release.

Not covered: hosted tools (web search, file search, code interpreter, image
generation, hosted MCP), which run on OpenAI's servers; ``ShellTool``,
``ApplyPatchTool``, ``LocalShellTool``, ``ComputerTool`` and custom tools, which
take no tool guardrail; and a tool or handoff added to an agent after
:func:`protect` ran — call it again after adding one.

The SDK offers no hook after every ending of a tool, so the outcome is reported
when the Gate enforces it: blocked, or released as the tool is handed back.

Settings: ``BINDABLE_API_BASE_URL``, ``BINDABLE_GATE_CREDENTIAL``,
``BINDABLE_AGENT_ID`` (the agent's did:webvh), ``BINDABLE_REVIEW_CONTEXT``, and
optionally ``BINDABLE_AGENT_KEY_FILE`` with ``BINDABLE_AGENT_KEY_ID``.
"""

from __future__ import annotations

from typing import Any, Final

from agents import (
    Agent,
    FunctionTool,
    ToolGuardrailFunctionOutput,
    ToolInputGuardrailData,
    tool_input_guardrail,
)

from bindable_gate.frameworks._core import FrameworkGate, decision_unavailable

FRAMEWORK: Final[str] = "openai-agents"
DISTRIBUTION: Final[str] = "openai-agents"
GUARDRAIL_NAME: Final[str] = "bindable_action_gate"


def framework_gate(gate: FrameworkGate | None = None) -> FrameworkGate:
    """The Gate given, or one configured from the environment."""
    return gate if gate is not None else FrameworkGate.from_env(
        framework=FRAMEWORK, distribution=DISTRIBUTION
    )


def bindable_guardrail(gate: FrameworkGate | None = None) -> Any:
    """The tool input guardrail itself, for a tool :func:`protect` does not reach.

    Never raises: an exception from a guardrail aborts the whole run.
    """
    held = framework_gate(gate)

    @tool_input_guardrail(name=GUARDRAIL_NAME)  # type: ignore[untyped-decorator,unused-ignore]
    async def guardrail(data: ToolInputGuardrailData) -> ToolGuardrailFunctionOutput:
        name = str(getattr(data.context, "tool_name", "") or "")
        try:
            decision = await held.adecide(name, data.context.tool_arguments)
        except Exception:  # noqa: BLE001 - adecide does not raise; this holds if it ever does
            decision = decision_unavailable(name, "The Gate could not decide.")
        held.enforced(decision)
        if decision.released:
            return ToolGuardrailFunctionOutput.allow(output_info={"receipt_id": decision.receipt_id})
        return ToolGuardrailFunctionOutput.reject_content(
            decision.message, output_info={"receipt_id": decision.receipt_id}
        )

    # Kept on the guardrail so gate_of() can find the Gate to close it.
    setattr(guardrail, "bindable_gate", held)  # noqa: B010
    return guardrail


def protect(agent: Agent[Any], gate: FrameworkGate | None = None) -> Agent[Any]:
    """Put the Gate first on every tool call ``agent`` can make, and return it.

    Every function tool, every MCP server and every agent reachable by handoff
    gets the same guardrail, once: calling this twice adds nothing.
    """
    guardrail = bindable_guardrail(gate)
    seen: set[int] = set()

    def first(existing: Any) -> list[Any]:
        others = [g for g in (existing or []) if getattr(g, "name", None) != GUARDRAIL_NAME]
        return [guardrail, *others]

    def walk(current: Agent[Any]) -> None:
        if id(current) in seen:
            return
        seen.add(id(current))
        for tool in current.tools:
            if isinstance(tool, FunctionTool):
                tool.tool_input_guardrails = first(tool.tool_input_guardrails)
        for server in current.mcp_servers:
            server.tool_input_guardrails = first(getattr(server, "tool_input_guardrails", None))
        for handoff in current.handoffs:
            if isinstance(handoff, Agent):
                walk(handoff)

    walk(agent)
    return agent


def gate_of(agent: Agent[Any]) -> FrameworkGate | None:
    """The Gate :func:`protect` put on ``agent``, to close it at shutdown."""
    for tool in agent.tools:
        for guardrail in getattr(tool, "tool_input_guardrails", None) or []:
            held = getattr(guardrail, "bindable_gate", None)
            if isinstance(held, FrameworkGate):
                return held
    for server in agent.mcp_servers:
        for guardrail in getattr(server, "tool_input_guardrails", None) or []:
            held = getattr(guardrail, "bindable_gate", None)
            if isinstance(held, FrameworkGate):
                return held
    return None


__all__ = ["bindable_guardrail", "framework_gate", "gate_of", "protect"]
