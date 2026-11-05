# bindable-gate/src/bindable_gate/core/handling.py
"""Handling a gated call without getting the dangerous case wrong.

A gated call has five endings, and only three of them mean what people
assume:

    RELEASED         the action ran, and the ledger knows.
    BLOCKED          the action did not run. The Receipt says why.
    NOT_RUN          the action did not run. Either no trustworthy Decision
                     was available — unreachable, timed out, unreadable, or
                     Evidence that could not be recorded — or the call never
                     got that far: unusable configuration, or a Receipt the
                     local audit copy could not write, both of which happen
                     before the handler.
    RAN_UNRECORDED   THE ACTION RAN. The ledger does not know it.
    RAN_OFFLINE      THE ACTION RAN, unreviewed, while Bindable was
                     unreachable, under the agent's offline setting. It is
                     an offline record on this machine until Bindable is
                     back, and it has no Receipt.

That last one is the trap. It arrives as an exception, and exceptions read
as failure, so the natural instinct is to retry — which runs the side effect
a second time. Charging a card twice, sending an email twice, releasing a
payment twice. :func:`handle_outcome` exists to make that instinct
impossible to follow by accident: it answers ``safe_to_retry`` explicitly,
and for RAN_UNRECORDED the answer is False.

    result = handle_outcome(lambda: pull_records(matter_id="M", call_cost_usd=12))

    if result.ran:
        deliver(result.outcome.action_result)
    elif result.safe_to_retry:
        schedule_retry()
    else:
        alert_a_human(result)

**This is a helper, not a framework.** It classifies; it never decides what
you do next, and it never owns your control flow. What to do about a
RAN_UNRECORDED is a judgement only the developer can make — reconcile from
the Receipt, alert someone, accept the gap — so nothing here chooses for
you. It only makes sure the choice is made knowingly.

If you would rather keep your own ``try``/``except`` — because your
branches do different work, or you already have error handling this must
fit inside — use :func:`classify` directly and keep the flow:

    try:
        outcome = pull_records(matter_id="M", call_cost_usd=12)
        result = classify(outcome)
    except GateError as exc:
        result = classify(exc)
        if not result.ran:
            raise                       # your policy, not this module's

The safety semantics come baked into whichever you use: an ending that RAN
is never marked ``safe_to_retry``, however you got there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from bindable_gate.core.errors import (
    GateBlocked,
    GateConfigError,
    GateError,
    GateUnreachable,
    ReceiptNotStored,
    TrackpointContradiction,
    TrackpointNotRecorded,
)
from bindable_gate.core.outcome import Outcome
from bindable_gate.library.errors import (
    EvidenceRefused,
    EvidenceUnreachable,
    EvidenceValueRefused,
)
from bindable_gate.library.outcome import Outcome as LibraryOutcome

RELEASED = "released"
BLOCKED = "blocked"
NOT_RUN = "not_run"
RAN_UNRECORDED = "ran_unrecorded"
RAN_OFFLINE = "ran_offline"


@dataclass
class GateResult:
    """One gated call, classified so the next step is obvious."""

    status: str
    ran: bool
    recorded: bool | None
    """Whether the ledger holds it. ``None`` where that cannot be known: an
    Evidence submission that got no answer may have been recorded and lost
    only its reply."""
    safe_to_retry: bool
    outcome: Outcome | LibraryOutcome | None = None
    error: GateError | None = None
    receipt_id: str | None = None
    action_result: Any = None

    @property
    def released(self) -> bool:
        return self.status == RELEASED

    @property
    def blocked(self) -> bool:
        return self.status == BLOCKED

    @property
    def needs_attention(self) -> bool:
        """True when a person should look at this — the action ran unrecorded."""
        return self.status == RAN_UNRECORDED

    def summary(self) -> str:
        """One line, written to be pasted into an incident note."""
        if self.status == RELEASED:
            return f"released and recorded (receipt {self.receipt_id})"
        if self.status == BLOCKED:
            return f"blocked, the action did not run (receipt {self.receipt_id})"
        if self.status == NOT_RUN:
            return f"the action did not run: {self.error}"
        if self.status == RAN_OFFLINE:
            return (
                "the action ran, not reviewed: it ran while Bindable was unreachable, and is "
                "an offline record with no Receipt; do NOT retry the side effect"
            )
        return (
            f"THE ACTION RAN but was not recorded (receipt {self.receipt_id}) — "
            "do NOT retry the side effect; reconcile from the Receipt"
        )


def classify(value: Outcome | LibraryOutcome | GateError) -> GateResult:
    """Classify an Outcome you received or an error you caught.

    The composable half: bring your own ``try``/``except``, hand over what
    came back — an :class:`Outcome`, the Library Gate's own outcome, or one
    of the Gate's errors — and get the same ``ran`` / ``safe_to_retry``
    answers :func:`handle_outcome` gives, without surrendering your control
    flow.

    The Library Gate's outcome is answered here as the core one is. It was
    once refused with a ``TypeError``, and a released call reaches this only
    after the action has run, so that refusal was an exception escaping after
    the side effect: the one ending a caller must never be tempted to retry.

    Raises:
        TypeError: for anything that is neither. A bug in the action is not
            a Gate ending, and silently classifying it would hide it.
    """
    # Both outcome types a gated call returns: the core Outcome and the
    # Library Gate's own. Neither is ever refused.
    if isinstance(value, LibraryOutcome) and value.offline:
        # THE ACTION RAN, unreviewed. Retrying repeats the side effect.
        return GateResult(
            status=RAN_OFFLINE, ran=True, recorded=False, safe_to_retry=False,
            outcome=value, action_result=value.action_result,
        )
    if isinstance(value, (Outcome, LibraryOutcome)):
        if value.blocked:
            return GateResult(
                status=BLOCKED, ran=False, recorded=True, safe_to_retry=False,
                outcome=value, receipt_id=value.receipt_id,
            )
        return GateResult(
            status=RELEASED, ran=True, recorded=True, safe_to_retry=False,
            outcome=value, receipt_id=value.receipt_id,
            action_result=value.action_result,
        )
    if isinstance(value, GateBlocked):
        return GateResult(
            status=BLOCKED, ran=False, recorded=True, safe_to_retry=False,
            outcome=value.outcome, error=value, receipt_id=value.receipt_id,
        )
    if isinstance(value, TrackpointNotRecorded):
        # THE ACTION RAN. Retrying repeats the side effect.
        return GateResult(
            status=RAN_UNRECORDED, ran=True, recorded=False, safe_to_retry=False,
            error=value, receipt_id=value.receipt_id,
            action_result=value.action_result,
        )
    if isinstance(value, TrackpointContradiction):
        # Also ran: the Decision was honoured, the report was refused.
        return GateResult(
            status=RAN_UNRECORDED, ran=True, recorded=False, safe_to_retry=False,
            error=value, receipt_id=value.receipt_id,
        )
    if isinstance(value, EvidenceUnreachable):
        # No answer to an Evidence submission is not proof it was not
        # recorded: the request may have arrived and only the reply been lost.
        # So recorded is unknown, never False. A retry is still safe, because
        # it presents the same idempotency key and is answered with what the
        # first attempt did rather than writing a second record.
        return GateResult(
            status=NOT_RUN, ran=False, recorded=None, safe_to_retry=True,
            error=value,
        )
    if isinstance(value, GateUnreachable):
        # Covers EvidenceNotRecorded too: nothing ran, so a retry is safe.
        return GateResult(
            status=NOT_RUN, ran=False, recorded=False, safe_to_retry=True,
            error=value,
        )
    if isinstance(value, ReceiptNotStored):
        # The Receipt is written BEFORE the handler, so a failed audit write
        # means the action never started. Answering here rather than raising:
        # a caller asking "did my action run" must get an answer, and a
        # TypeError is the one reply that cannot be acted on.
        return GateResult(
            status=NOT_RUN, ran=False, recorded=False, safe_to_retry=True,
            error=value, receipt_id=value.receipt_id,
        )
    if isinstance(value, (EvidenceRefused, EvidenceValueRefused)):
        # submit_evidence's two answered endings: the platform refused the
        # Evidence, or the value was refused before anything was sent. Either
        # way nothing ran and nothing was recorded, and a retry repeats no side
        # effect, though a refusal will be refused again until what it names
        # is fixed, as a configuration error is.
        return GateResult(
            status=NOT_RUN, ran=False, recorded=False, safe_to_retry=True,
            error=value,
        )
    if isinstance(value, GateConfigError):
        # Configuration is refused before anything is proposed — at
        # construction, or when Evidence is malformed — so nothing ran.
        # Retrying repeats no side effect, though it will keep failing until
        # the configuration is fixed.
        return GateResult(
            status=NOT_RUN, ran=False, recorded=False, safe_to_retry=True,
            error=value,
        )
    raise TypeError(
        f"classify expects an Outcome or one of the Gate's errors, got "
        f"{type(value).__name__} — a bug in the action itself is yours to handle"
    )


def handle_outcome(call: Callable[[], Outcome | LibraryOutcome]) -> GateResult:
    """Run a gated call and classify how it ended.

    Convenience over :func:`classify` for the common case. Never raises the
    Gate's own errors: every ending becomes a :class:`GateResult` whose
    ``ran`` and ``safe_to_retry`` can be trusted. Exceptions from the handler
    itself are NOT swallowed — a bug in the action is the developer's to see.

    Reach for :func:`classify` instead when you want to keep your own
    ``try``/``except`` and branch your own way.
    """
    try:
        return classify(call())
    except GateError as exc:
        return classify(exc)


__all__ = [
    "BLOCKED",
    "classify",
    "GateResult",
    "NOT_RUN",
    "RAN_UNRECORDED",
    "RELEASED",
    "handle_outcome",
]
