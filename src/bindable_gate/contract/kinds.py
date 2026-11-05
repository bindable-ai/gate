# bindable-gate/src/bindable_gate/contract/kinds.py
"""What kind of Gate reported: ``gate_kind``.

Each attachment form has its own kind:

==================== ===================================================
``runtime_plugin``   a plugin or hook inside the agent's runtime: the
                     Claude Code and Copilot CLI hooks, the OpenClaw plugin
``framework_adapter`` an adapter for an agent framework
``mcp_proxy``        the local MCP proxy in front of your tool servers
``library``          the Library Gate, in code you write
==================== ===================================================

``mcp_gateway`` is the hosted MCP Gate's, which Bindable operates.

The platform's report takes all four kinds above besides ``mcp_gateway``
and ``python_library``, so each customer-run form reports its own kind. A
kind left out of :data:`PLATFORM_ACCEPTS_GATE_KINDS` reports
``python_library``. One switch, here, and nowhere else.
"""

from __future__ import annotations

from typing import Final

RUNTIME_PLUGIN: Final[str] = "runtime_plugin"
FRAMEWORK_ADAPTER: Final[str] = "framework_adapter"
MCP_PROXY: Final[str] = "mcp_proxy"
LIBRARY: Final[str] = "library"

#: What every customer-run form reports while the platform takes only it.
LEGACY_GATE_KIND: Final[str] = "python_library"

KINDS: Final[frozenset[str]] = frozenset({RUNTIME_PLUGIN, FRAMEWORK_ADAPTER, MCP_PROXY, LIBRARY})

#: Which of the four kinds above the platform takes on a report.
PLATFORM_ACCEPTS_GATE_KINDS: Final[frozenset[str]] = KINDS


def report_gate_kind(kind: str, *, accepted: frozenset[str] = PLATFORM_ACCEPTS_GATE_KINDS) -> str:
    """The ``gate_kind`` a report from a Gate of ``kind`` carries."""
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not a gate_kind a customer-run Gate has")
    return kind if kind in accepted else LEGACY_GATE_KIND
