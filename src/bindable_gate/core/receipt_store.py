# bindable-gate/src/bindable_gate/core/receipt_store.py
"""Keeping the developer's own copy of every Receipt.

Point a Gate at ``receipts_dir=`` and each Receipt it sees — released and
blocked alike — is written there as ``<receipt_id>.json``.

**This folder is the customer's own audit copy. The authoritative record is
server-side, and nothing is ever read back from this folder.** The Gate
never consults it to make a decision, never dedupes against it, and never
treats a file here as proof of anything: it is a convenience for the people
who have to answer questions later.

The bytes are the Receipt exactly as the platform returned it — the dict as
received, serialized once. Nothing is added, reordered, or normalised, so
``bindable.verify_receipt`` on the file verifies the same Receipt the
platform minted. A Gate that "tidied" a Receipt on the way to disk would
hand its customer a file that fails verification.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from bindable_gate.core.errors import ReceiptNotStored


class ReceiptStore:
    """A directory of Receipts, written and never read."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ReceiptNotStored("(none)", str(self.directory), str(exc)) from exc

    def write(self, receipt: Mapping[str, Any]) -> Path | None:
        """Write one Receipt. Returns the path, or None if there is nothing to write.

        Raises:
            ReceiptNotStored: if the write fails. A developer who asked for a
                local audit trail must never silently lack one, so this is
                raised rather than warned — only they can decide whether to
                proceed without the copy they asked for.
        """
        receipt_id = receipt.get("receipt_id") if isinstance(receipt, Mapping) else None
        if not isinstance(receipt_id, str) or not receipt_id:
            return None  # nothing to file it under; a blocked pre-review denial
        path = self.directory / f"{_safe_name(receipt_id)}.json"
        try:
            # Serialized once, from the dict as received: no re-shaping, so
            # offline verification of this file still succeeds.
            path.write_text(json.dumps(dict(receipt)), encoding="utf-8")
        except (OSError, TypeError, ValueError) as exc:
            raise ReceiptNotStored(receipt_id, str(path), str(exc)) from exc
        return path


def _safe_name(receipt_id: str) -> str:
    """Keep a receipt id from escaping the directory it belongs in."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in receipt_id)


__all__ = ["ReceiptStore"]
