# bindable-gate/src/bindable_gate/contract/local_track.py
"""The local Track: what an agent did while no Gate credential was configured.

One JSON line per tool call, in a hash-chained file, ``~/.bindable/
local-track.jsonl`` by default. The format is the TypeScript core's, field for
field, so a local Track written by either is read by either:

- ``id``, ``previous_hash``, ``chain_hash``, ``agent_id``, ``tool``, ``params``
- ``result_summary``: ``{"digest", "preview"}``, the SHA-256 of the result's
  canonical JSON and its first 200 characters, or null
- ``error`` (at most 500 characters), ``duration_ms``, ``started_at``,
  ``finished_at``
- with an agent key: ``agent_key_id``, ``signed_at`` and ``agent_signature``

``chain_hash`` is SHA-256 over the RFC 8785 canonical JSON of the line without
``chain_hash`` and ``agent_signature``, and that includes ``previous_hash``,
the chain hash of the line before (64 zeros for the first). Changing,
removing or reordering any line breaks every hash after it. ``agent_signature``
covers the same bytes.

Several processes may append at once (a hook runs as one process per tool
call), so each append takes an exclusive lock on the file and reads the chain's
end again under it.

The local Track is a record kept on this machine. Bindable has reviewed none
of it.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from cryptography.hazmat.primitives.asymmetric import ec

from bindable_gate.contract.canonical import canonical_json, sha256_hex, to_json
from bindable_gate.contract.signing import AgentKey, verify_signature
from bindable_gate.core.errors import GateConfigError

GENESIS_HASH: Final[str] = "0" * 64
PREVIEW_CHARS: Final[int] = 200
ERROR_CHARS: Final[int] = 500
_TAIL_CHUNK: Final[int] = 64 * 1024
_HEX: Final[frozenset[str]] = frozenset("0123456789abcdef")

#: Leave the result out of a call: "no result", which is not the same as null.
NO_RESULT: Final[object] = object()


def when(moment: datetime) -> str:
    """An aware time as the TypeScript core writes it: ``toISOString()``."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def default_local_track_path() -> Path:
    """Where the local Track is kept unless told otherwise."""
    return Path.home() / ".bindable" / "local-track.jsonl"


def summarise(result: Any) -> dict[str, Any] | None:
    """A result in a line: its digest and the first characters of its JSON."""
    if result is NO_RESULT:
        return None
    try:
        text = canonical_json(result)
    except ValueError:
        return {"digest": None, "preview": ""}
    return {"digest": sha256_hex(text), "preview": text[:PREVIEW_CHARS]}


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _covered(body: Mapping[str, Any]) -> str:
    return canonical_json(body)


class LocalTrack:
    """Appends calls to one local Track file."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        agent_id: str | None = None,
        key: AgentKey | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path).expanduser() if path is not None else default_local_track_path()
        self._agent_id = agent_id
        self._key = key
        self._clock = clock or (lambda: datetime.now(UTC))

    def open(self) -> None:
        """Make the folder and check the chain's end can be read.

        Raises:
            GateConfigError: The file's last line is not a local Track line,
                or the folder cannot be made.
        """
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.path.exists():
                with self.path.open("rb") as handle:
                    last_chain_hash(handle)
        except OSError:
            raise GateConfigError("the local Track could not be opened") from None

    def append(
        self,
        *,
        tool: str,
        params: Mapping[str, Any],
        started_at: str,
        result: Any = NO_RESULT,
        error: str | None = None,
        duration_ms: float | None = None,
    ) -> dict[str, Any]:
        """Append one call and return its line."""
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "r+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                previous = last_chain_hash(handle)
                finished_at = when(self._clock())
                body: dict[str, Any] = {
                    "id": str(uuid.uuid4()),
                    "previous_hash": previous,
                    "agent_id": self._agent_id,
                    "tool": tool,
                    "params": to_json(dict(params)),
                    "result_summary": summarise(result) if error is None else None,
                    "error": None if error is None else str(error)[:ERROR_CHARS],
                    "duration_ms": _duration(duration_ms),
                    "started_at": started_at,
                    "finished_at": finished_at,
                }
                if self._key is not None:
                    body["agent_key_id"] = self._key.key_id
                    body["signed_at"] = finished_at
                text = _covered(body)
                entry: dict[str, Any] = json.loads(text)
                entry["chain_hash"] = sha256_hex(text)
                if self._key is not None:
                    entry["agent_signature"] = self._key.sign(text)
                handle.seek(0, os.SEEK_END)
                handle.write((json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
                handle.flush()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return entry


def _duration(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return int(value) if value.is_integer() else value
    return value


def last_chain_hash(handle: Any) -> str:
    """The chain hash on the file's last line, read from the end.

    Raises:
        GateConfigError: The last line is not a local Track line.
    """
    handle.seek(0, os.SEEK_END)
    end = handle.tell()
    tail = b""
    while end > 0:
        start = max(0, end - _TAIL_CHUNK)
        handle.seek(start)
        tail = handle.read(end - start) + tail
        end = start
        trimmed = tail.rstrip(b"\n")
        if b"\n" in trimmed or end == 0:
            line = trimmed[trimmed.rfind(b"\n") + 1:]
            if not line:
                return GENESIS_HASH
            try:
                parsed = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                parsed = None
            value = parsed.get("chain_hash") if isinstance(parsed, dict) else None
            if not _is_hash(value):
                raise GateConfigError("the local Track's last line is not a local Track line")
            return str(value)
    return GENESIS_HASH


@dataclass(frozen=True)
class TrackCheck:
    """What checking a local Track found. ``broken_at`` is 1-based."""

    ok: bool
    lines: int
    broken_at: int | None = None
    reason: str | None = None


def verify_local_track(path: str | Path, public_key: ec.EllipticCurvePublicKey | None = None) -> TrackCheck:
    """Recompute the whole chain. With ``public_key``, every line must also
    carry a signature that key made."""
    lines = [line for line in Path(path).expanduser().read_text(encoding="utf-8").split("\n") if line]
    previous = GENESIS_HASH
    for index, line in enumerate(lines, start=1):
        try:
            entry = json.loads(line)
        except ValueError:
            return TrackCheck(False, len(lines), index, "not JSON")
        if not isinstance(entry, dict):
            return TrackCheck(False, len(lines), index, "not JSON")
        chain_hash = entry.pop("chain_hash", None)
        signature = entry.pop("agent_signature", None)
        if entry.get("previous_hash") != previous:
            return TrackCheck(False, len(lines), index, "previous_hash does not match")
        try:
            text = _covered(entry)
        except ValueError:
            return TrackCheck(False, len(lines), index, "chain_hash does not match")
        if sha256_hex(text) != chain_hash:
            return TrackCheck(False, len(lines), index, "chain_hash does not match")
        if public_key is not None and (
            not isinstance(signature, str) or not verify_signature(public_key, text, signature)
        ):
            return TrackCheck(False, len(lines), index, "agent_signature does not verify")
        previous = str(chain_hash)
    return TrackCheck(True, len(lines))
