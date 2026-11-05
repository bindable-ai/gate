# bindable-gate/src/bindable_gate/library/outcome.py
"""What a gated call produced: the enforcement outcome and the record of it.

The Gate's result is an **enforcement outcome**, ``released`` or ``blocked``,
and it is never the bare return value of the wrapped function. The Receipt
that permitted or withheld the action is part of the answer, because the
point of the Library is that the action and the record of it are one act.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from bindable.decision import BLOCKED, RELEASED

from bindable_gate.contract.kinds import LIBRARY, report_gate_kind
from bindable_gate.library._clock import now

#: What this Gate reports as ``gate_kind``: the Library Gate is ``library``
#: (through the one switch in :mod:`bindable_gate.contract.kinds`).
GATE_KIND: Final[str] = report_gate_kind(LIBRARY)

#: The two contexts an Action Review is performed in, as the wire spells them. A Gate carries one for its whole life.
REVIEW_CONTEXTS: Final[tuple[str, ...]] = ("test", "live")


@dataclass(frozen=True)
class Identifiers:
    """The three values that make a Proposal the same Proposal on a retry.

    The platform is idempotent on (``agent_id``, ``gate_id``,
    ``proposed_action_id``, ``nonce``, ``review_context``): the same Proposal
    from the same Gate in the same context returns the Receipt the first
    attempt minted, rather than minting a second one.

    So these are minted once per intended action and reused for every attempt
    at it. Minting a fresh pair on retry is the defect this exists to
    prevent: it would turn one action into two records.
    """

    proposed_action_id: str
    nonce: str
    proposed_at: str

    @classmethod
    def mint(cls) -> Identifiers:
        """Fresh identifiers for one intended action."""
        return cls(
            proposed_action_id=str(uuid.uuid4()),
            nonce=secrets.token_hex(16),
            proposed_at=now(),
        )

    def given(
        self,
        *,
        proposed_action_id: str | None,
        nonce: str | None,
        proposed_at: str | None,
    ) -> Identifiers:
        """These identifiers with whatever the caller supplied put in place.

        A caller driving its own retry supplies what it used last time; each
        value it leaves out keeps the one minted here.
        """
        return Identifiers(
            proposed_action_id=proposed_action_id or self.proposed_action_id,
            nonce=nonce or self.nonce,
            proposed_at=proposed_at or self.proposed_at,
        )


@dataclass(frozen=True)
class Review:
    """What the Action Review answered, whole.

    :meth:`Gate.propose` returns the Receipt alone, which is all most callers
    need. A caller that holds an action itself, rather than handing it to
    :meth:`Gate.run`, needs the rest of the answer too: the verdict to decide on,
    and the reason to tell whoever was refused. The MCP Gate is that caller.

    ``released`` is the derivation, not a second opinion: exactly ``passed`` on
    both the answer and the Receipt, decided by the one published rule.

    Attributes:
        receipt: The signed Receipt, as the platform persisted it.
        verdict: ``passed``, ``failed`` or ``error``.
        released: Whether this review releases the action.
        reason_code: The platform's reason code, where it gave one.
        failed_rule: The rule that failed, where one did.
        identifiers: What the Proposal was made with, to reuse on a retry.
    """

    receipt: Mapping[str, Any]
    verdict: str
    released: bool
    identifiers: Identifiers
    reason_code: str | None = None
    failed_rule: str | None = None


#: The enforcement outcome of an action that ran unreviewed, while Bindable was
#: unreachable, under the agent's offline setting. It has no Receipt.
OFFLINE: Final[str] = "offline"


@dataclass
class Outcome:
    """One gated call, start to finish.

    Attributes:
        outcome: ``released`` or ``blocked``. The enforcement outcome.
        verdict: The review's verdict, ``passed``, ``failed`` or ``error``.
        receipt: The signed Receipt that records the review.
        identifiers: What the Proposal was made with, to reuse on a retry.
        ran: Whether the wrapped action actually ran. Only ever true on
            ``released``.
        action_result: What the wrapped action returned, where it ran.
        enforced_at: The time the Gate reported enforcing at.
        trackpoint: The Trackpoint the platform returned for the report.
        failed_rule: The rule that failed, where one did.
        reason_code: The platform's reason code, where it gave one.
        offline_record: Where the action ran while Bindable was unreachable
            (``outcome`` ``offline``), the offline record it became. There is
            no Receipt then, and ``receipt`` is empty.
    """

    outcome: str
    verdict: str
    receipt: Mapping[str, Any]
    identifiers: Identifiers
    ran: bool = False
    action_result: Any = None
    enforced_at: str = ""
    trackpoint: Mapping[str, Any] | None = None
    failed_rule: str | None = None
    reason_code: str | None = None
    offline_record: Mapping[str, Any] | None = None

    @property
    def offline(self) -> bool:
        """The action ran unreviewed while Bindable was unreachable."""
        return self.outcome == OFFLINE

    @property
    def released(self) -> bool:
        """The review passed and the action was let through."""
        return self.outcome == RELEASED

    @property
    def blocked(self) -> bool:
        """The review did not pass and the action was withheld."""
        return self.outcome == BLOCKED

    @property
    def receipt_id(self) -> str | None:
        """The Receipt's own identifier, or ``None`` if it carries none."""
        value = self.receipt.get("receipt_id")
        return value if isinstance(value, str) else None
