# bindable-gate/src/bindable_gate/core/outcome.py
"""What a gated action produced, and what gets written to the ledger.

Two objects, deliberately distinct:

- :class:`Outcome` is what the CALLER receives — the Gate's result for one
  action: the Decision, the Receipt behind it, and (when the action ran)
  whatever the handler returned.
- :class:`TrackpointReport` is what goes ON THE WIRE to
  the platform's own report. It is deliberately thinner than the Outcome.

RESULT REPORTING IS OFF BY DEFAULT, and that is a privacy position, not an
oversight. The trackpoint chain is append-only and the ledger is PII-free by
design: Receipts carry ids, never content. A handler's return value is
arbitrary customer data — an email agent's return could be message bodies.
Sending it by default would write customer PII into a chain that can never
be redacted. So a developer opts in per gated function
(``report_result=True``), and even then the value is bounded: non-object
returns are wrapped as ``{"value": ...}`` and anything serializing beyond
:data:`MAX_ACTION_RESULT_BYTES` is truncated with a ``"truncated": true``
marker. The ledger records that an action happened; it is not a payload
store.

``gate_runner`` is the other field that is not the caller's to set, for the
opposite reason. It records WHO OPERATED the Gate that reported, which is a
provenance claim rather than an observation, so :data:`GATE_RUNNER` is
stamped on every report and there is no way to construct one saying anything
else. See that constant for why it is a constant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: Serialized ceiling for an opted-in action_result. Past this the ledger is
#: being used as a payload store, which it is not.
MAX_ACTION_RESULT_BYTES = 8 * 1024

RELEASED = "released"
BLOCKED = "blocked"

#: Who operated the reporting Gate, as the ledger records it. Distinct from
#: ``gate_kind``: that is the FORM (mcp_proxy or in_process), this is the
#: OPERATOR.
#:
#: Constant, and deliberately not configurable. This package is the
#: developer-run Gate — every install of it runs in the developer's own
#: environment — so "self-hosted" is not a default to be overridden, it is
#: the true value for all of them. A parameter or an environment variable
#: here would let a self-hosted Gate report itself as ``bindable-hosted``,
#: which is provenance forgery wearing the clothes of configuration. When
#: Bindable-hosted runtimes exist, THEIR deployment states its own runner;
#: it is never something a customer's install gets to claim.
GATE_RUNNER = "self-hosted"


def prepare_action_result(result: Any) -> dict[str, Any] | None:
    """Shape a handler's return value for the ledger, or None if unusable.

    The wire type is ``object | null``, so a bare string, int or list is
    wrapped as ``{"value": ...}``. Anything that will not serialize is
    reported as its type name rather than dropped silently — the ledger
    should say "something came back and it was a Foo", not imply nothing did.
    """
    if result is None:
        return None
    payload: dict[str, Any] = result if isinstance(result, dict) else {"value": result}
    try:
        encoded = json.dumps(payload)
    except (TypeError, ValueError):
        return {"value": f"<unserializable {type(result).__name__}>"}
    if len(encoded.encode("utf-8")) <= MAX_ACTION_RESULT_BYTES:
        return payload
    # Over the ceiling: keep a bounded, obviously-marked prefix.
    room = MAX_ACTION_RESULT_BYTES // 2
    return {
        "value": encoded[:room],
        "truncated": True,
        "original_bytes": len(encoded.encode("utf-8")),
    }


@dataclass
class Outcome:
    """The Gate's result for one action — what the caller gets back.

    A gated function returns this, never the handler's bare value: the
    Decision and the Receipt behind it are part of the result, not
    metadata to be discovered later. ``action_result`` here is the handler's
    real return value regardless of the reporting opt-in — the opt-in
    governs what reaches the LEDGER, not what the caller sees.
    """

    decision: str
    verdict: str
    receipt_id: str | None
    receipt: dict[str, Any] | None = None
    reason: str | None = None
    reason_code: str | None = None
    failed_rule: str | None = None
    action_result: Any = None
    ran: bool = False
    proposed_at: str | None = None
    enforced_at: str | None = None
    trackpoint_id: str | None = None
    gate_kind: str | None = None
    gate_version: str | None = None

    @property
    def released(self) -> bool:
        return self.decision == RELEASED

    @property
    def blocked(self) -> bool:
        return self.decision == BLOCKED


__all__ = [
    "BLOCKED",
    "GATE_RUNNER",
    "MAX_ACTION_RESULT_BYTES",
    "RELEASED",
    "Outcome",
    "prepare_action_result",
]
