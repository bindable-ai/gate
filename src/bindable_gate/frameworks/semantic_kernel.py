# bindable-gate/src/bindable_gate/frameworks/semantic_kernel.py
"""The Gate for Semantic Kernel.

    pip install "bindable-gate[semantic-kernel]"

Two lines, on the kernel your agent or your code invokes functions through::

    from bindable_gate.frameworks.semantic_kernel import protect
    protect(kernel)                 # or protect(agent.kernel)

The hook is Semantic Kernel's function invocation filter
(``FilterTypes.FUNCTION_INVOCATION``): it wraps every ``KernelFunction`` call on
that kernel — a tool the model calls through auto function calling, in any
connector or agent type, and a function your own code runs with
``kernel.invoke``. The Gate's filter decides once per call: it proposes the
call in the agent's did:webvh, calls ``next`` only on a release, and otherwise
sets the function's result to the Gate's reason, which is what the model sees
for that call. Added last, it sits closest to the function, so no other filter
reaches a function body around it.

A prompt function (``function.is_prompt``) is a call to the model, not an
action, and passes untouched — unless the model itself called it as a tool, in
which case it is held like any other. That is what the second filter
(``AUTO_FUNCTION_INVOCATION``) is for: it marks the calls the model made.

Not covered: a kernel :func:`protect` was not given (an agent built without a
kernel makes its own — protect ``agent.kernel``); a plugin's Python method
called directly; tools the model provider runs itself (code interpreter, file
search, web search); a filter added after :func:`protect` that skips ``next``
in its own way; and ``kernel.remove_filter``.

Settings: ``BINDABLE_API_BASE_URL``, ``BINDABLE_GATE_CREDENTIAL``,
``BINDABLE_AGENT_ID`` (the agent's did:webvh), ``BINDABLE_REVIEW_CONTEXT``, and
optionally ``BINDABLE_AGENT_KEY_FILE`` with ``BINDABLE_AGENT_KEY_ID``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, Final

from semantic_kernel import Kernel
from semantic_kernel.filters import (
    AutoFunctionInvocationContext,
    FilterTypes,
    FunctionInvocationContext,
)
from semantic_kernel.functions import FunctionResult

from bindable_gate.frameworks._core import FrameworkGate, decision_unavailable

FRAMEWORK: Final[str] = "semantic-kernel"
DISTRIBUTION: Final[str] = "semantic-kernel"

#: Whether the function now being invoked is one the model called as a tool.
_MODEL_CALLED: ContextVar[bool] = ContextVar("bindable_gate_model_called", default=False)


def framework_gate(gate: FrameworkGate | None = None) -> FrameworkGate:
    """The Gate given, or one configured from the environment."""
    return gate if gate is not None else FrameworkGate.from_env(
        framework=FRAMEWORK, distribution=DISTRIBUTION
    )


def protect(kernel: Kernel, gate: FrameworkGate | None = None) -> Kernel:
    """Hold every function call on ``kernel`` for the Gate, and return it.

    Protecting a kernel twice adds nothing.
    """
    if getattr(kernel, "_bindable_gate", None) is not None:
        return kernel
    held = framework_gate(gate)

    async def model_called(
        context: AutoFunctionInvocationContext,
        next: Callable[[AutoFunctionInvocationContext], Awaitable[None]],
    ) -> None:
        token = _MODEL_CALLED.set(True)
        try:
            await next(context)
        finally:
            _MODEL_CALLED.reset(token)

    async def gate_filter(
        context: FunctionInvocationContext,
        next: Callable[[FunctionInvocationContext], Awaitable[None]],
    ) -> None:
        function = context.function
        if getattr(function, "is_prompt", False) and not _MODEL_CALLED.get():
            await next(context)
            return
        name = str(getattr(function, "fully_qualified_name", "") or "")
        try:
            arguments = {key: value for key, value in (context.arguments or {}).items()}
            decision = await held.adecide(name, arguments)
        except Exception:  # noqa: BLE001 - a raising filter ends the agent's whole turn
            decision = decision_unavailable(name, "The Gate could not decide.")
        if not decision.released:
            held.enforced(decision)
            context.result = FunctionResult(function=function.metadata, value=decision.message)
            return
        try:
            await next(context)
        finally:
            held.enforced(decision)

    kernel.add_filter(FilterTypes.AUTO_FUNCTION_INVOCATION, model_called)
    kernel.add_filter(FilterTypes.FUNCTION_INVOCATION, gate_filter)
    object.__setattr__(kernel, "_bindable_gate", held)
    return kernel


def gate_of(kernel: Kernel) -> FrameworkGate | None:
    """The Gate :func:`protect` put on ``kernel``, to close it at shutdown."""
    held: Any = getattr(kernel, "_bindable_gate", None)
    return held if isinstance(held, FrameworkGate) else None


__all__ = ["framework_gate", "gate_of", "protect"]
