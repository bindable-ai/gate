# bindable-gate/src/bindable_gate/frameworks/__init__.py
"""The Gate for agent frameworks: one adapter per framework, one core shared.

Each adapter is its own module and its own extra, so a customer installs
only the framework it uses: ``bindable-gate[langgraph]``, ``[adk]``,
``[openai-agents]``, ``[autogen]``, ``[crewai]``, ``[semantic-kernel]``.
See :mod:`bindable_gate.frameworks._core` for what every adapter does.
"""

from bindable_gate.frameworks._core import Decision, FrameworkGate

__all__ = ["Decision", "FrameworkGate"]
