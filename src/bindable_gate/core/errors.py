# bindable-gate/src/bindable_gate/core/errors.py
"""What the Action Gate raises, and what each one means for the action.

Read these as answers to one question — did the action run?

- :class:`GateBlocked`         no. The Action Review said so.
- :class:`GateUnreachable`     no. The Gate could not obtain a Decision it
                               could trust, so it refused to run the action.
- :class:`TrackpointNotRecorded`  YES, and that is the point: the action ran
                               but the ledger does not know. Never confuse
                               this with a block.
- :class:`TrackpointContradiction`  a bug in this Gate, caught by the
                               platform acting as derivation oracle.
"""

from __future__ import annotations

from typing import Any


class GateError(RuntimeError):
    """Base class for every Action Gate failure."""


class GateConfigError(GateError):
    """Configuration is missing or unusable. Names variables, never values."""


class GateBlocked(GateError):
    """The Action Review blocked this action; the handler never ran.

    Carries the Receipt reference so the caller can cite exactly which
    Decision refused the action, and the reason the Contract gave.
    """

    def __init__(
        self,
        receipt_id: str | None,
        verdict: str,
        *,
        reason: str | None = None,
        reason_code: str | None = None,
        failed_rule: str | None = None,
        outcome: Any = None,
    ) -> None:
        detail = reason or reason_code or f"verdict {verdict!r}"
        super().__init__(
            f"blocked by the Action Review ({detail})"
            + (f" — receipt {receipt_id}" if receipt_id else "")
        )
        self.receipt_id = receipt_id
        self.verdict = verdict
        self.reason = reason
        self.reason_code = reason_code
        self.failed_rule = failed_rule
        self.outcome = outcome


class GateUnreachable(GateError):
    """No trustworthy Decision, so the action did not run.

    Raised when the Action Review cannot be reached, times out, answers
    something unreadable, or returns a released verdict with no Receipt to
    tie it to. Fail-closed is the whole point: there is no parameter,
    environment variable, or test hook that turns this into a release.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"no Decision could be obtained, so the action did not run: {detail}"
        )
        self.detail = detail


class EvidenceNotRecorded(GateUnreachable):
    """Evidence the Contract will chain on could not be recorded.

    Deliberately a kind of :class:`GateUnreachable`: the action did NOT run,
    and code already handling "no trustworthy Decision" handles this too.

    Failing closed here is the kinder behaviour. A Contract that chains on
    Evidence cannot be satisfied without it, so proposing anyway would come
    back blocked for a reason that looks like a policy refusal rather than a
    missing prerequisite — the developer would go hunting through their
    Contract for a rule that was never the problem.
    """

    def __init__(self, evidence_id: str, detail: str) -> None:
        GateError.__init__(
            self,
            f"the Evidence this action depends on was not recorded "
            f"({evidence_id}): {detail} — the action did not run",
        )
        self.evidence_id = evidence_id
        self.detail = detail


class ReceiptNotStored(GateError):
    """A Receipt could not be written to the local audit folder.

    Raised rather than warned: a developer who asked for a local audit trail
    must never quietly not have one. The Decision itself stands — the
    authoritative record is server-side — but the copy they asked for is
    missing, and only they can decide whether to proceed without it.
    """

    def __init__(self, receipt_id: str, path: str, detail: str) -> None:
        super().__init__(
            f"the Receipt {receipt_id} could not be written to {path}: {detail}"
        )
        self.receipt_id = receipt_id
        self.path = path
        self.detail = detail


class TrackpointNotRecorded(GateError):
    """The action's Outcome could not be recorded.

    THE ACTION ALREADY RAN (or was already blocked). This is never a block
    and must never be reported as one — it says the ledger is missing a
    Trackpoint that should exist. ``action_result`` carries whatever the
    handler returned so the caller does not lose it.
    """

    def __init__(
        self, receipt_id: str, action_result: Any = None, detail: str = ""
    ) -> None:
        super().__init__(
            f"the action ran but its Outcome was not recorded for receipt "
            f"{receipt_id}" + (f" ({detail})" if detail else "")
        )
        self.receipt_id = receipt_id
        self.action_result = action_result
        self.detail = detail


class TrackpointContradiction(GateError):
    """The platform refused the report: the decision contradicts the verdict.

    Never a transport problem. The platform is the derivation oracle, and it
    has just caught this Gate reporting a decision the Receipt does not
    support — a correctness bug to fix, not to retry.
    """

    def __init__(self, receipt_id: str, decision: str) -> None:
        super().__init__(
            f"the platform refused a {decision!r} Trackpoint for receipt "
            f"{receipt_id}: it contradicts the Receipt's verdict — this Gate's "
            "derivation is wrong"
        )
        self.receipt_id = receipt_id
        self.decision = decision


__all__ = [
    "EvidenceNotRecorded",
    "GateBlocked",
    "GateConfigError",
    "GateError",
    "GateUnreachable",
    "ReceiptNotStored",
    "TrackpointContradiction",
    "TrackpointNotRecorded",
]
