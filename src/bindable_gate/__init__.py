# bindable-gate/src/bindable_gate/__init__.py
"""bindable-gate — the Bindable Action Gate.

Two forms, one core. Both put the hosted Action Review in front of a
consequential action and let it run only when the verdict releases it; they
differ only in how the action reaches them.

- **Library** — decorate your own function::

      gate = Gate(base_url=..., credential="bnd_gt_...", review_context="live")

      @gate.control(
          agent_id=AGENT,
          action_type="paid_data_call",
          payload_fields=["matter_id", "call_cost_usd"],
      )
      def pull_records(matter_id: str, call_cost_usd: int) -> str:
          return vendor.pull(matter_id)

  Only the arguments named in ``payload_fields`` are sent. Never put API
  keys, passwords, secrets or personal data into a payload; send only what
  your rules need.

- **MCP** — sit between an MCP client and a tool server, with no changes to
  the agent's code: ``python -m bindable_gate``.

What both forms share is everything that matters: one derivation rule
(released if and only if the verdict is exactly ``passed``), one release
precondition (a Receipt must anchor it), one Trackpoint contract, and
fail-closed behaviour with no switch to turn it off.

This package decides nothing itself. It holds no rules and evaluates nothing:
the hosted Action Review returns the verdict and mints the signed Receipt, and
this enforces it and records what happened. Its only cryptography is in
``bindable_gate.contract``: the agent's own signature over its Proposal, and the
hash chain of the local Track a Gate keeps when it has no credential.
Receipt verification, if you want it, is ``bindable.verify_receipt`` or the
platform's ``POST /v1/verify``, never a local reimplementation.
"""

from bindable_gate.core.derivation import BLOCKED, RELEASED, decision_from
from bindable_gate.core.errors import (
    EvidenceNotRecorded,
    GateBlocked,
    GateConfigError,
    GateError,
    GateUnreachable,
    ReceiptNotStored,
    TrackpointContradiction,
    TrackpointNotRecorded,
)
from bindable_gate.core.handling import GateResult, classify, handle_outcome
from bindable_gate.core.outcome import Outcome
from bindable_gate.library import (
    EvidenceRefused,
    EvidenceUnreachable,
    EvidenceValueRefused,
    Gate,
    GateOffline,
    Identifiers,
    ProposalRefused,
    ProposalUnreachable,
)

__all__ = [
    "BLOCKED",
    "RELEASED",
    "EvidenceNotRecorded",
    "EvidenceRefused",
    "EvidenceUnreachable",
    "EvidenceValueRefused",
    "Gate",
    "GateBlocked",
    "GateConfigError",
    "GateError",
    "GateOffline",
    "GateResult",
    "GateUnreachable",
    "Identifiers",
    "Outcome",
    "ProposalRefused",
    "ProposalUnreachable",
    "ReceiptNotStored",
    "Review",
    "TrackpointContradiction",
    "TrackpointNotRecorded",
    "classify",
    "decision_from",
    "handle_outcome",
]
