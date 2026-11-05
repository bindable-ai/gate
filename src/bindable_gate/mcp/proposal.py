# bindable-gate/src/bindable_gate/mcp/proposal.py
"""Map an MCP tool call to Bindable Proposal fields.

Translates one tools/call into the Proposal the Action Gate submits to the
hosted Bindable Action Gate: action_type, payload, proposed_action_id, and
nonce. (persistent_agent_id is supplied by the Action Gate from its own
identity configuration, not derived from the tool call.)

Guardrails:
- Thin transport: this module only maps fields. All decide/verify traffic
  goes through the `bindable` PyPI client to the hosted API; no
  canonicalization, hashing, or signature code lives here.
- Fail-closed: a tool call that cannot be mapped to a well-formed Proposal
  is blocked, never forwarded (ProposalError, handled by the Action Gate).
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Mapping
from typing import Any


class ProposalError(ValueError):
    """The tool call cannot be mapped to a well-formed Proposal."""


def build_proposal(tool_call: Mapping[str, Any]) -> dict[str, Any]:
    """Build Proposal fields from one MCP tools/call ``params`` object.

    Args:
        tool_call: The ``params`` of a tools/call request — expects
            ``name`` (str, required) and ``arguments`` (mapping, optional).

    Returns:
        Dict with ``action_type`` (the tool name), ``payload`` (the tool
        arguments as a plain dict), ``proposed_action_id`` (fresh per
        call), and ``nonce`` (>= 8 chars, unique per attempt).

    Raises:
        ProposalError: If the tool call has no usable tool name or its
            arguments are not a mapping.
    """
    name = tool_call.get("name")
    if not isinstance(name, str) or not name:
        raise ProposalError("tools/call has no usable tool name")
    arguments = tool_call.get("arguments") or {}
    if not isinstance(arguments, Mapping):
        raise ProposalError("tools/call arguments are not an object")
    return {
        "action_type": name,
        "payload": dict(arguments),
        "proposed_action_id": f"mcp-{uuid.uuid4().hex}",
        "nonce": secrets.token_hex(16),
    }
