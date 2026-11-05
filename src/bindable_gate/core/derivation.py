# bindable-gate/src/bindable_gate/core/derivation.py
"""The released/blocked rule — borrowed, never re-implemented.

The rule itself lives in the ``bindable`` SDK as ``decision_from``, and is
re-exported here so this package has one obvious place to look without ever
owning a second copy. One rule, one implementation, estate-wide:

    released  iff  verdict == "passed"
    blocked   for every other verdict, and for any Review that cannot be read

On top of the rule sits one precondition this Gate adds, and it is a
precondition for RELEASE only:

    releasing also requires a Receipt carrying a receipt_id.

The Receipt is persisted before release, so a passed verdict the
Gate cannot tie to a Receipt is a malformed Review — it must fail closed
rather than release blind. Blocking needs no Receipt: a pre-review denial,
a duplicate, or a ledger write failure legitimately arrives without one.

Presence is all that is checked. Verifying the Receipt's signature is the
engine's job and the offline verifier's; this package holds no crypto.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bindable.decision import BLOCKED, RELEASED, decision_from

__all__ = ["BLOCKED", "RELEASED", "decision_from", "receipt_id_of", "release_permitted"]


def receipt_id_of(review: Mapping[str, Any] | None) -> str | None:
    """The Receipt id a Review carries, or None when it carries none."""
    if not isinstance(review, Mapping):
        return None
    receipt = review.get("receipt")
    if not isinstance(receipt, Mapping):
        return None
    receipt_id = receipt.get("receipt_id")
    return receipt_id if isinstance(receipt_id, str) and receipt_id else None


def release_permitted(review: Mapping[str, Any] | None) -> bool:
    """True only when the verdict releases AND a Receipt anchors it.

    Anything else is False — which means blocked when the verdict is a real
    non-passing verdict, and fail-closed when the Review is malformed. The
    caller distinguishes those two; this answers only "may the action run?"
    """
    return decision_from(review) == RELEASED and receipt_id_of(review) is not None
