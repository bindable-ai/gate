# bindable-gate/src/bindable_gate/mcp/__init__.py
"""The MCP form of the Action Gate — an in-path stdio proxy.

Holds every ``tools/call`` between an MCP client and a downstream tool
server, submits it to the hosted Action Review, and forwards it only when
the verdict releases it. Same core, same one rule, same Trackpoints as the
in-process form; only the shape of the call and what a block looks like
differ.
"""

from bindable_gate.mcp.config import GateConfig, GateConfigError
from bindable_gate.mcp.proxy import ActionGateProxy, main

__all__ = ["ActionGateProxy", "GateConfig", "GateConfigError", "main"]
