# bindable-gate/src/bindable_gate/library/_clock.py
"""Timestamps in the one shape the platform takes.

An aware timestamp to the millisecond, UTC, ``Z`` rather than ``+00:00``:

    2026-09-23T15:04:05.123Z

Twenty-four characters, inside the platform's twenty-to-forty, and the shape
its own Proposals and reports carry. A naive datetime is refused rather than
assumed to be UTC: a Gate guessing its own offset is how a report ends up
before the decision it reports on.
"""

from __future__ import annotations

from datetime import UTC, datetime


def when(moment: datetime) -> str:
    """Render an aware ``datetime`` as the platform's timestamp."""
    if moment.tzinfo is None:
        raise ValueError("an aware datetime is required; a naive one has no offset to trust")
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now() -> str:
    """The present, in the platform's timestamp."""
    return when(datetime.now(UTC))


def parse(text: str) -> datetime | None:
    """Read a platform timestamp back, or ``None`` when it cannot be read.

    ``None`` rather than an exception, because the only caller is the one
    choosing an ``enforced_at`` floor, and a Receipt with an unreadable
    ``decided_at`` must not stop an enforcement outcome being reported.

    In the package this came from, ``Z`` was substituted first because
    ``fromisoformat`` did not accept it before Python 3.11. This package
    requires 3.11, so the substitution is gone rather than carried along.
    """
    try:
        moment = datetime.fromisoformat(text)
    except (ValueError, AttributeError):
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
