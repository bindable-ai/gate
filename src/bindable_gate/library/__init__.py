# bindable-gate/src/bindable_gate/library/__init__.py
"""The Library Gate — an Action Review in front of your own function.

A Gate the customer runs. It submits the Proposal, obtains the Receipt, releases
the wrapped action only where the verdict was ``passed``, and reports the
enforcement outcome either way. Those are one act, and there is no way to do any
of them without the others.

    from bindable_gate import Gate

    gate = Gate(base_url=..., credential="bnd_gt_...", review_context="live")

    @gate.control(
        agent_id=AGENT,
        action_type="send_invoice",
        payload_fields=["customer", "amount_cents"],
    )
    def send_invoice(customer: str, amount_cents: int) -> str:
        return billing.send(customer, amount_cents)

    outcome = send_invoice(customer="acme", amount_cents=250_000)

Only the arguments named in ``payload_fields`` become the Proposal's payload;
every other argument stays in this process. Never put API keys,
passwords, secrets or personal data into a payload; send only what your rules
need.

It calls two endpoints and no others, presenting the Gate credential as a bearer
token, which is the only credential either accepts:
``POST /v1/reviews`` and ``POST /v1/receipts/{receipt_id}/report``. It reports
``gate_kind`` ``library``, and it carries a review context — ``test`` or
``live`` — set once at construction, never a stage.

This replaces the in-process Gate, which spoke to routes the platform no longer
serves.
"""

from bindable_gate.library.errors import (
    EvidenceRefused,
    EvidenceUnreachable,
    EvidenceValueRefused,
    GateOffline,
    ProposalRefused,
    ProposalUnreachable,
)
from bindable_gate.library.gate import (
    DEFAULT_ATTEMPTS,
    EVIDENCE_PATH,
    REPORT_PATH,
    REVIEWS_PATH,
    Gate,
)
from bindable_gate.library.outcome import (
    GATE_KIND,
    OFFLINE,
    REVIEW_CONTEXTS,
    Identifiers,
    Outcome,
    Review,
)
from bindable_gate.library.payload import mask_credentials, named_fields

__all__ = [
    "DEFAULT_ATTEMPTS",
    "EVIDENCE_PATH",
    "GATE_KIND",
    "OFFLINE",
    "REPORT_PATH",
    "REVIEWS_PATH",
    "REVIEW_CONTEXTS",
    "EvidenceRefused",
    "EvidenceUnreachable",
    "EvidenceValueRefused",
    "Gate",
    "GateOffline",
    "Identifiers",
    "Outcome",
    "ProposalRefused",
    "ProposalUnreachable",
    "Review",
    "mask_credentials",
    "named_fields",
]
