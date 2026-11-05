# bindable-gate/src/bindable_gate/core/__init__.py
"""The shared spine the Gate sits on.

Form-agnostic: the one derivation rule, the Outcome model, and the errors. The
platform client that used to live here spoke a wire the platform no longer
serves; both forms now reach it through the Library Gate's client.
"""

from bindable_gate.core.derivation import (
    BLOCKED,
    RELEASED,
    decision_from,
    receipt_id_of,
    release_permitted,
)
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
from bindable_gate.core.namespace import check_agent_namespace
from bindable_gate.core.outcome import (
    GATE_RUNNER,
    MAX_ACTION_RESULT_BYTES,
    Outcome,
    prepare_action_result,
)
from bindable_gate.core.receipt_store import ReceiptStore

__all__ = [
    "BLOCKED",
    "GATE_RUNNER",
    "MAX_ACTION_RESULT_BYTES",
    "RELEASED",
    "EvidenceNotRecorded",
    "GateBlocked",
    "GateConfigError",
    "GateError",
    "GateResult",
    "GateUnreachable",
    "Outcome",
    "ReceiptNotStored",
    "ReceiptStore",
    "TrackpointContradiction",
    "TrackpointNotRecorded",
    "check_agent_namespace",
    "classify",
    "decision_from",
    "handle_outcome",
    "prepare_action_result",
    "receipt_id_of",
    "release_permitted",
]
