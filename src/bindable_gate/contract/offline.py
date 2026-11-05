# bindable-gate/src/bindable_gate/contract/offline.py
"""Offline mode: what a Gate does while Bindable is unreachable.

Every customer-run Gate (the hooks, the MCP proxy, the Library Gate and the
framework adapters) decides through this one module when a review cannot be
had. The TypeScript core's ``offline.ts`` keeps the same files in the same
format, so the person's ``bindable-gate offline accept`` reaches either.

THE SETTING. The Account chooses one of three in Bindable. A Gate reads it
with its own credential (``GET /v1/account/offline-mode``) while Bindable is
reachable, and keeps the last one on this machine for when it is not:

============== =========================================================
``automatic``  switch automatically: the action runs, with the line
               :data:`UNREACHABLE_MESSAGE` said once
``ask_first``  ask me first: the action is blocked with
               :data:`ASK_MESSAGE` until the person runs
               ``bindable-gate offline accept``; then it runs, as above.
               The person has :data:`ACCEPT_WINDOW` (10 minutes) from
               the outage's start; after that, with nobody accepted,
               the agent is stopped for the rest of the outage
               (:data:`STOPPED_MESSAGE`), as under ``stop``
``stop``       stop the agent: the Gate's fail-closed answer, unchanged
============== =========================================================

With nothing kept, the setting is ``ask_first``.

UNREACHABLE means no answer, a timeout or a 5xx, and nothing else
(:func:`is_unreachable`). A 4xx is an answer, and so is a block: neither
ever switches, whatever the setting.

WHILE OFFLINE, no review is attempted. Each action that runs becomes one
offline record, in the form ``POST /v1/agents/{did}/offline-records`` takes:
the tool, the SHA-256 of its arguments' canonical JSON (the arguments are
never kept or sent), the SHA-256 of its result, whether it failed, when it
started and finished, when the outage began (``offline_since``), the setting
that let it run (``allowed_by``) and, under *ask me first*, when the person
accepted (``asked_at``) and who (``accepted_by``, ``user@host``; never with
*switch automatically*). The records of one Gate for one agent form one chain:
``previous_hash`` is the record before's ``chain_hash`` (64 zeros for the
first), and ``chain_hash`` is SHA-256 over the RFC 8785 canonical JSON of the
record without ``chain_hash`` and ``agent_signature``. With an agent key the
record also carries ``agent_key_id``, ``signed_at`` and ``agent_signature``
over the same bytes. No Receipt is created, imitated or returned for any of
it.

RECONNECT. Bindable is probed with backoff. When it answers, the Gate goes
back to review at once and uploads the records it holds, one offline period
to a request; a record leaves the upload list only once Bindable takes it,
and none is ever deleted. An upload that fails is retried, with backoff, on
later calls.

FILES, for one agent on this machine, under ``~/.bindable/offline/<agent>/``
(``BINDABLE_OFFLINE_DIR`` moves the root): ``state.json`` (the outage, and
what has been sent), ``setting.json``, ``accepts.jsonl`` (who accepted, and
when), ``records-<gate>.jsonl`` (one chain per Gate), and ``lock`` while one
process changes them. Several processes use them at once (a hook runs as one
process per call), so every change takes the lock and reads the files again
under it.

Nothing here puts the Gate credential or the agent key into a file, a message
or a record.
"""

from __future__ import annotations

import contextlib
import getpass
import hashlib
import json
import math
import os
import re
import socket
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives.asymmetric import ec

from bindable_gate.contract.canonical import canonical_json, sha256_hex
from bindable_gate.contract.local_track import GENESIS_HASH, NO_RESULT, when
from bindable_gate.contract.signing import AgentKey, verify_signature

# ─── The settings and the words ────────────────────────────────────────

AUTOMATIC: Final[str] = "automatic"
ASK_FIRST: Final[str] = "ask_first"
STOP: Final[str] = "stop"
SETTINGS: Final[tuple[str, ...]] = (AUTOMATIC, ASK_FIRST, STOP)

#: The setting when none has been read from the platform.
DEFAULT_SETTING: Final[str] = ASK_FIRST

#: How each setting is named to a person.
SETTING_NAMES: Final[Mapping[str, str]] = {
    AUTOMATIC: "switch automatically",
    ASK_FIRST: "ask me first",
    STOP: "stop the agent",
}

UNREACHABLE_MESSAGE: Final[str] = "Bindable is unreachable. This agent's actions are not being reviewed."
ASK_MESSAGE: Final[str] = (
    "Bindable is unreachable. This agent's actions are not being reviewed. "
    "To continue anyway run: bindable-gate offline accept"
)
#: How long *ask me first* waits, from the outage's start, for a person to
#: accept. A call is never held open that long: a hook's or a plugin's
#: runtime would end it (and OpenClaw would then run the tool), so each call
#: inside the window is blocked with :data:`ASK_MESSAGE` at once.
ACCEPT_WINDOW: Final[timedelta] = timedelta(minutes=10)
#: Said for each call once :data:`ACCEPT_WINDOW` has passed with nobody accepted.
STOPPED_MESSAGE: Final[str] = (
    "Bindable is unreachable and nobody ran bindable-gate offline accept within 10 minutes, "
    "so this action was stopped. This agent stays stopped until Bindable is reachable again."
)
#: What every offline record is, wherever this package shows one.
NOT_REVIEWED: Final[str] = "Not reviewed: ran while Bindable was unreachable"


def counted(n: int) -> str:
    """``1 offline record``, ``3 offline records``."""
    return f"{n} offline record" if n == 1 else f"{n} offline records"


def reconnected_message(sent: int) -> str:
    """What the person is told when Bindable is back and the records went up."""
    return (
        "Bindable is reachable and your agent's actions are being reviewed again. "
        f"{counted(sent)} sent to the Track."
    )


def kept_message(kept: int) -> str:
    """What the person is told when Bindable is back and the upload failed."""
    return (
        "Bindable is reachable and your agent's actions are being reviewed again. "
        f"{counted(kept)} {'is' if kept == 1 else 'are'} kept on this machine and will be sent "
        "to the Track later."
    )


# ─── The platform's endpoints ─────────────────────────────────────────

SETTING_PATH: Final[str] = "/v1/account/offline-mode"
RECORDS_PATH: Final[str] = "/v1/agents/{did}/offline-records"
PROBE_PATH: Final[str] = "/v1/service-status"

#: Seconds between probes, and between upload attempts, while they fail.
BACKOFF_SECONDS: Final[tuple[int, ...]] = (5, 15, 30, 60, 120, 300)
#: How long a kept setting is used before it is read again, while online.
SETTING_REFRESH: Final[timedelta] = timedelta(minutes=15)
#: The most records one upload request carries.
UPLOAD_BATCH: Final[int] = 500
#: The most characters Bindable takes in ``accepted_by``.
ACCEPTED_BY_LIMIT: Final[int] = 100

OFFLINE_DIR_ENV: Final[str] = "BINDABLE_OFFLINE_DIR"
STATE_FILE: Final[str] = "state.json"
SETTING_FILE: Final[str] = "setting.json"
ACCEPTS_FILE: Final[str] = "accepts.jsonl"
LOCK_FILE: Final[str] = "lock"

#: The members a record's chain hash and signature do not cover.
UNCOVERED: Final[tuple[str, ...]] = ("chain_hash", "agent_signature")

_LOCK_WAIT_SECONDS: Final[float] = 10.0
_LOCK_STALE_SECONDS: Final[float] = 30.0
_CHAIN_NAME: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9_.-]")


class OfflineError(Exception):
    """An offline command could not do what was asked; one plain sentence."""


def is_unreachable(error: BaseException | None, *, timed_out: bool = False) -> bool:
    """Whether a review that failed with ``error`` means Bindable is unreachable.

    No answer, a timeout or a 5xx. A refusal (any 4xx), an answer that could
    not be read, an answer with no Receipt, a key that would not sign, or any
    other error is not.
    """
    if timed_out or isinstance(error, (httpx.TransportError, TimeoutError)):
        return True
    status = getattr(error, "status", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return status >= 500
    return getattr(error, "no_answer", False) is True


def offline_root() -> Path:
    """Where every agent's offline files are kept on this machine."""
    configured = os.environ.get(OFFLINE_DIR_ENV, "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".bindable" / "offline"


def agent_folder(agent_id: str, root: Path | None = None) -> Path:
    """One agent's folder: named by the SHA-256 of its DID, so any DID fits."""
    return (root or offline_root()) / hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:32]


def records_file(chain: str) -> str:
    """The file one Gate's chain is kept in, inside the agent's folder."""
    return f"records-{_CHAIN_NAME.sub('-', chain) or 'gate'}.jsonl"


def chain_of(gate_version: str) -> str:
    """Which Gate a chain is: the runtime or form ``gate_version`` names
    (``claude-code/0.4.0`` is ``claude-code``), or ``library``."""
    return gate_version.split("/", 1)[0] if "/" in gate_version else "library"


def who() -> str:
    """Who is accepting: the user on this machine, and the machine."""
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = "unknown"
    return f"{user}@{socket.gethostname()}"


def chain_hash(record: Mapping[str, Any]) -> str:
    """A record's ``chain_hash``: SHA-256 of its covered bytes."""
    return sha256_hex(covered_text(record))


def covered_text(record: Mapping[str, Any]) -> str:
    """The canonical JSON a record's chain hash and signature cover."""
    return canonical_json({k: v for k, v in record.items() if k not in UNCOVERED})


@dataclass(frozen=True)
class OfflineDecision:
    """What offline mode decided for one action.

    Attributes:
        run: Whether the action may run. When it does, it runs unreviewed and
            becomes an offline record; there is no Receipt.
        message: What the person is shown: the block's reason, or the line
            said when the Gate switched. Empty when there is nothing new to say.
        setting: The setting this outage is decided under.
        offline_since: When this outage began.
        accepted_at: When the person accepted, under ``ask_first``.
        accepted_by: Who accepted.
    """

    run: bool
    message: str
    setting: str
    offline_since: str
    accepted_at: str | None = None
    accepted_by: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "run": self.run,
            "setting": self.setting,
            "offline_since": self.offline_since,
            "accepted_at": self.accepted_at,
            "accepted_by": self.accepted_by,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> OfflineDecision:
        return cls(
            run=bool(value.get("run")),
            message="",
            setting=str(value.get("setting") or DEFAULT_SETTING),
            offline_since=str(value.get("offline_since") or ""),
            accepted_at=_text(value.get("accepted_at")),
            accepted_by=_text(value.get("accepted_by")),
        )


# ─── One agent's offline mode ─────────────────────────────────────────


class OfflineMode:
    """Offline mode for one agent, in one Gate.

    Call :meth:`before` before every review; when it answers, no review is
    made and its decision stands. Call :meth:`failed` when a review failed;
    when it answers, its decision replaces the Gate's fail-closed answer.
    Call :meth:`reviewed` after every review that was answered. Call
    :meth:`record` once an action that offline mode let run has run.

    None of them raises: a fault in offline mode itself is the Gate's
    fail-closed answer.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        base_url: str,
        credential: str | Callable[[], str],
        gate_version: str,
        chain: str | None = None,
        key: AgentKey | None = None,
        root: str | Path | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        notify: Callable[[str], None] | None = None,
        probe_timeout: float = 3.0,
        upload_timeout: float = 5.0,
        upload_budget: float = 8.0,
    ) -> None:
        self.agent_id = agent_id
        self.folder = agent_folder(agent_id, Path(root).expanduser() if root is not None else None)
        self.chain = chain or chain_of(gate_version)
        self._base_url = base_url.rstrip("/")
        self._credential = credential
        self._key = key
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(UTC))
        self._notify = notify or to_stderr
        self._probe_timeout = probe_timeout
        self._upload_timeout = upload_timeout
        self._upload_budget = upload_budget

    @property
    def records_path(self) -> Path:
        return self.folder / records_file(self.chain)

    # ─── The setting ───────────────────────────────────────────────────

    def setting(self) -> str:
        """The kept setting, or :data:`DEFAULT_SETTING` when none is kept."""
        kept = _read_json(self.folder / SETTING_FILE)
        value = kept.get("when_unreachable") if kept else None
        return value if value in SETTINGS else DEFAULT_SETTING

    def refresh_setting(self) -> None:
        """Read the Account's setting from Bindable and keep it. Whatever the
        answer, the next read waits :data:`SETTING_REFRESH`, so a platform
        that has none to give is not asked on every call. Never raises."""
        kept = _read_json(self.folder / SETTING_FILE) or {}
        value = kept.get("when_unreachable")
        with contextlib.suppress(Exception):
            response = self._client(self._probe_timeout).get(self._base_url + SETTING_PATH,
                                                              headers=self._headers())
            if response.status_code == 200:
                answered = response.json().get("when_unreachable")
                value = answered if answered in SETTINGS else value
        with contextlib.suppress(OSError):
            self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write_json(self.folder / SETTING_FILE, {
                "when_unreachable": value if value in SETTINGS else None,
                "read_at": when(self._clock()),
            })

    # ─── Deciding ──────────────────────────────────────────────────────

    def before(self) -> OfflineDecision | None:
        """Before a review. ``None``: review as usual. Otherwise Bindable is
        still unreachable, no review is to be made, and this decides."""
        if not (self.folder / STATE_FILE).exists():
            return None
        try:
            with self._locked():
                state = self._state()
                if not state.get("offline"):
                    if self._pending(state) and _due(state.get("next_upload_at"), self._clock()):
                        self._upload(state, reconnecting=False)
                        self._save(state)
                    return None
                if _due(state.get("next_probe_at"), self._clock()):
                    if self._probe():
                        self._back(state)
                        self._save(state)
                        return None
                    self._schedule(state, "probe")
                decision = self._decide(state)
                self._save(state)
                return decision
        except Exception:  # noqa: BLE001 - offline mode never decides on a fault
            return None

    def failed(self, error: BaseException | None, *, timed_out: bool = False) -> OfflineDecision | None:
        """After a review failed. ``None``: the Gate's own fail-closed answer
        stands (the setting is ``stop``, or Bindable answered). Otherwise
        offline mode decides."""
        if not is_unreachable(error, timed_out=timed_out):
            return None
        setting = self.setting()
        if setting == STOP:
            return None
        try:
            with self._locked():
                state = self._state()
                if not state.get("offline"):
                    state.update({
                        "offline": True,
                        "offline_since": when(self._clock()),
                        "setting_used": setting,
                        "accepted_at": None,
                        "accepted_by": None,
                        "announced": False,
                        "probe_attempts": 0,
                    })
                    self._schedule(state, "probe")
                decision = self._decide(state)
                self._save(state)
                return decision
        except Exception:  # noqa: BLE001 - offline mode never decides on a fault
            return None

    def reviewed(self) -> None:
        """After a review that was answered: end an outage, since Bindable
        answered, and read the setting again when the kept one is old.
        Never raises."""
        state = _read_json(self.folder / STATE_FILE)
        if state and state.get("offline"):
            with contextlib.suppress(Exception), self._locked():
                current = self._state()
                if current.get("offline"):
                    self._back(current)
                    self._save(current)
        kept = _read_json(self.folder / SETTING_FILE)
        read_at = _parse(kept.get("read_at")) if kept else None
        if read_at is None or self._clock() - read_at >= SETTING_REFRESH:
            self.refresh_setting()

    def _decide(self, state: dict[str, Any]) -> OfflineDecision:
        setting = str(state.get("setting_used") or DEFAULT_SETTING)
        accepted_at = _text(state.get("accepted_at"))
        since = str(state.get("offline_since") or "")
        if setting == AUTOMATIC or accepted_at:
            message = "" if state.get("announced") else UNREACHABLE_MESSAGE
            state["announced"] = True
            return OfflineDecision(True, message, setting, since, accepted_at, _text(state.get("accepted_by")))
        if accept_window_closed(since, self._clock()):
            return OfflineDecision(False, STOPPED_MESSAGE, setting, since)
        return OfflineDecision(False, ASK_MESSAGE, setting, since)

    def _back(self, state: dict[str, Any]) -> None:
        """Bindable answered: back to review, the outage's end kept for its
        records, and the records go up."""
        since = _text(state.get("offline_since"))
        if since is not None:
            state.setdefault("periods", {})[since] = when(self._clock())
        for name in ("offline", "announced"):
            state[name] = False
        for name in ("offline_since", "setting_used", "accepted_at", "accepted_by", "next_probe_at",
                     "next_upload_at"):
            state[name] = None
        state["probe_attempts"] = 0
        state["upload_attempts"] = 0
        self._upload(state, reconnecting=True)

    # ─── Recording ─────────────────────────────────────────────────────

    def record(
        self,
        decision: OfflineDecision,
        *,
        tool: str,
        params: Mapping[str, Any],
        started_at: str,
        result: Any = NO_RESULT,
        failed: bool = False,
        duration_ms: float | None = None,
    ) -> dict[str, Any] | None:
        """Append one offline record for an action that ran. Returns the
        record, or ``None`` where it could not be written; the action has run
        either way, so this never raises."""
        try:
            with self._locked():
                state = self._state()
                entry = self._append(state, decision, tool=tool, params=params, started_at=started_at,
                                     result=result, failed=failed, duration_ms=duration_ms)
                self._save(state)
                return entry
        except Exception as error:  # noqa: BLE001 - a record never stops the agent
            self._notify(f"Bindable Gate: the offline record for {tool} was not written "
                         f"({type(error).__name__}).")
            return None

    def _append(
        self,
        state: dict[str, Any],
        decision: OfflineDecision,
        *,
        tool: str,
        params: Mapping[str, Any],
        started_at: str,
        result: Any,
        failed: bool,
        duration_ms: float | None,
    ) -> dict[str, Any]:
        records = _records(self.records_path)
        finished_at = when(self._clock())
        # An action that finished after Bindable was back still belongs to the
        # outage it ran in; that outage ends when the action did.
        periods = state.get("periods") or {}
        ended = _parse(periods.get(decision.offline_since))
        if ended is not None and ended < self._clock():
            periods[decision.offline_since] = finished_at
            state["periods"] = periods
        body: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "previous_hash": str(records[-1]["chain_hash"]) if records else GENESIS_HASH,
            "agent_id": self.agent_id,
            "tool": tool or "unknown",
            "params_digest": sha256_hex(canonical_json(dict(params))),
            "result_digest": _digest(result),
            "failed": bool(failed),
            "duration_ms": _whole(duration_ms),
            "started_at": started_at,
            "finished_at": finished_at,
            "offline_since": decision.offline_since,
            "allowed_by": AUTOMATIC if decision.accepted_at is None else ASK_FIRST,
            "asked_at": decision.accepted_at,
        }
        if decision.accepted_at is not None and decision.accepted_by:
            body["accepted_by"] = decision.accepted_by[:ACCEPTED_BY_LIMIT]
        if self._key is not None:
            body["agent_key_id"] = self._key.key_id
            body["signed_at"] = finished_at
        text = canonical_json(body)
        entry: dict[str, Any] = json.loads(text)
        entry["chain_hash"] = sha256_hex(text)
        if self._key is not None:
            entry["agent_signature"] = self._key.sign(text)
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.records_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
        return entry

    # ─── Uploading ─────────────────────────────────────────────────────

    def sync(self) -> int:
        """Upload every record of this Gate not yet taken by Bindable, now.
        Returns how many went up.

        Raises:
            OfflineError: Bindable did not take them; they are kept.
        """
        with self._locked():
            state = self._state()
            pending = self._pending(state)
            sent = self._send(state)
            self._save(state)
        if sent < pending:
            raise OfflineError(
                f"{counted(pending - sent)} {'is' if pending - sent == 1 else 'are'} kept on this machine: "
                "Bindable did not take them."
            )
        return sent

    def _pending(self, state: Mapping[str, Any]) -> int:
        sent = int((state.get("sent") or {}).get(self.chain) or 0)
        return max(0, _count(self.records_path) - sent)

    def _upload(self, state: dict[str, Any], *, reconnecting: bool) -> None:
        pending = self._pending(state)
        if pending == 0:
            if reconnecting:
                self._notify(reconnected_message(0))
            return
        sent = self._send(state)
        if sent == pending:
            state["upload_attempts"] = 0
            state["next_upload_at"] = None
            self._notify(reconnected_message(sent))
            return
        self._schedule(state, "upload")
        if reconnecting:
            self._notify(kept_message(pending - sent))

    def _send(self, state: dict[str, Any]) -> int:
        """Send what is pending, in chain order, one offline period to a
        request, within the upload budget. A period still going on is not
        sent. Returns how many Bindable took."""
        done = state.setdefault("sent", {})
        through = int(done.get(self.chain) or 0)
        pending = _records(self.records_path)[through:]
        periods = state.get("periods") or {}
        started = time.monotonic()
        sent = 0
        while pending and time.monotonic() - started <= self._upload_budget:
            since = str(pending[0].get("offline_since") or "")
            ended = periods.get(since)
            if ended is None:
                break
            batch: list[dict[str, Any]] = []
            for record in pending:
                if record.get("offline_since") != since or len(batch) == UPLOAD_BATCH:
                    break
                batch.append(record)
            try:
                response = self._client(self._upload_timeout).post(
                    self._url(RECORDS_PATH),
                    json={"offline_period": {"started_at": since, "ended_at": ended}, "records": batch},
                    headers=self._headers(),
                )
            except Exception:  # noqa: BLE001 - kept, and sent again later
                break
            if response.status_code not in (200, 201):
                break
            sent += len(batch)
            done[self.chain] = through + sent
            pending = pending[len(batch):]
        return sent

    # ─── Parts ─────────────────────────────────────────────────────────

    def _probe(self) -> bool:
        """Whether Bindable answers now: any answer under 500."""
        try:
            response = self._client(self._probe_timeout).get(f"{self._base_url}{PROBE_PATH}")
        except Exception:  # noqa: BLE001 - no answer is unreachable
            return False
        return response.status_code < 500

    def _schedule(self, state: dict[str, Any], what: str) -> None:
        attempts = int(state.get(f"{what}_attempts") or 0)
        gap = BACKOFF_SECONDS[min(attempts, len(BACKOFF_SECONDS) - 1)]
        state[f"{what}_attempts"] = attempts + 1
        state[f"next_{what}_at"] = when(self._clock() + timedelta(seconds=gap))

    def _state(self) -> dict[str, Any]:
        state = _read_json(self.folder / STATE_FILE) or {}
        state["agent_id"] = self.agent_id
        return state

    def _save(self, state: Mapping[str, Any]) -> None:
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_json(self.folder / STATE_FILE, state)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        with folder_lock(self.folder):
            yield

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(timeout=timeout, transport=self._transport)

    def _headers(self) -> dict[str, str]:
        credential = self._credential() if callable(self._credential) else self._credential
        return {"Authorization": f"Bearer {credential}", "Content-Type": "application/json"}

    def _url(self, path: str) -> str:
        return self._base_url + path.format(did=quote(self.agent_id, safe=":"))


# ─── The person's commands ────────────────────────────────────────────


@dataclass(frozen=True)
class AgentOffline:
    """One agent's offline state on this machine, as a command shows it."""

    agent_id: str
    offline: bool
    setting: str
    offline_since: str | None
    accepted_at: str | None
    accepted_by: str | None
    records: int
    pending: int
    chains: tuple[str, ...] = ()

    @property
    def waiting(self) -> bool:
        """Blocked under ``ask_first``, nobody accepted yet. Whether it can
        still be accepted is :meth:`stopped`."""
        return self.offline and self.setting == ASK_FIRST and self.accepted_at is None

    def stopped(self, at: datetime) -> bool:
        """Waiting past :data:`ACCEPT_WINDOW`: stopped until Bindable is back."""
        return self.waiting and accept_window_closed(self.offline_since, at)


def accept_window_closed(offline_since: str | None, at: datetime) -> bool:
    """Whether :data:`ACCEPT_WINDOW` from the outage's start has passed. An
    outage with no readable start counts as closed: nothing then runs."""
    began = _parse(offline_since)
    return began is None or at >= began + ACCEPT_WINDOW


def agents(root: Path | None = None) -> list[AgentOffline]:
    """Every agent with offline files on this machine."""
    base = root or offline_root()
    found: list[AgentOffline] = []
    if not base.is_dir():
        return found
    for folder in sorted(p for p in base.iterdir() if p.is_dir()):
        state = _read_json(folder / STATE_FILE)
        if not state or not isinstance(state.get("agent_id"), str):
            continue
        sent = state.get("sent") or {}
        chains = sorted(folder.glob("records-*.jsonl"))
        counts = {path.name[len("records-"):-len(".jsonl")]: _count(path) for path in chains}
        found.append(AgentOffline(
            agent_id=state["agent_id"],
            offline=bool(state.get("offline")),
            setting=str(state.get("setting_used") or DEFAULT_SETTING),
            offline_since=_text(state.get("offline_since")),
            accepted_at=_text(state.get("accepted_at")),
            accepted_by=_text(state.get("accepted_by")),
            records=sum(counts.values()),
            pending=sum(max(0, n - int(sent.get(chain) or 0)) for chain, n in counts.items()),
            chains=tuple(path.name for path in chains),
        ))
    return found


def accept(
    agent_id: str | None = None,
    *,
    root: Path | None = None,
    by: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AgentOffline:
    """Accept running unreviewed for one agent on this machine, until Bindable
    is back. Records who accepted and when, in the agent's state and in
    ``accepts.jsonl``.

    Raises:
        OfflineError: No agent is waiting, several are and none was named, or
            the agent waited past :data:`ACCEPT_WINDOW` and is stopped.
    """
    now = (clock or (lambda: datetime.now(UTC)))()
    asked = [a for a in agents(root) if a.waiting and (agent_id is None or a.agent_id == agent_id)]
    waiting = [a for a in asked if not a.stopped(now)]
    if asked and not waiting:
        raise OfflineError("; ".join(_stopped_text(a) for a in asked) + ".")
    if not waiting:
        named = f"Agent {agent_id} is" if agent_id else "No agent on this machine is"
        raise OfflineError(f"{named} waiting to continue while Bindable is unreachable; there is nothing to accept.")
    if len(waiting) > 1:
        raise OfflineError(
            "More than one agent is waiting; name one with --agent-id: "
            + ", ".join(a.agent_id for a in waiting)
        )
    chosen = waiting[0]
    folder = agent_folder(chosen.agent_id, root)
    moment = when(now)
    accepted_by = by or who()
    with folder_lock(folder):
        state = _read_json(folder / STATE_FILE) or {}
        if not state.get("offline"):
            raise OfflineError(f"Bindable is reachable again for agent {chosen.agent_id}; there is nothing to accept.")
        if accept_window_closed(_text(state.get("offline_since")), now):
            raise OfflineError(_stopped_text(chosen) + ".")
        state["accepted_at"] = moment
        state["accepted_by"] = accepted_by
        _write_json(folder / STATE_FILE, state)
        descriptor = os.open(folder / ACCEPTS_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "agent_id": chosen.agent_id, "offline_since": state.get("offline_since"),
                "accepted_at": moment, "accepted_by": accepted_by,
            }, separators=(",", ":")) + "\n")
    return AgentOffline(
        chosen.agent_id, True, chosen.setting, chosen.offline_since, moment, accepted_by,
        chosen.records, chosen.pending, chosen.chains,
    )


def _stopped_text(agent: AgentOffline) -> str:
    return (
        f"Agent {agent.agent_id} was stopped: nobody accepted within 10 minutes of Bindable becoming "
        f"unreachable at {agent.offline_since}. It runs again, reviewed, when Bindable is reachable"
    )


# ─── Checking a chain ─────────────────────────────────────────────────


@dataclass(frozen=True)
class RecordsCheck:
    """What checking an offline record file found. ``broken_at`` is 1-based."""

    ok: bool
    records: int
    broken_at: int | None = None
    reason: str | None = None


def verify_records(path: str | Path, public_key: ec.EllipticCurvePublicKey | None = None) -> RecordsCheck:
    """Recompute the whole chain, as Bindable does when it takes the records.
    With ``public_key``, every record must also carry a signature that key
    made."""
    lines = [line for line in Path(path).expanduser().read_text(encoding="utf-8").split("\n") if line]
    previous = GENESIS_HASH
    for index, line in enumerate(lines, start=1):
        try:
            entry = json.loads(line)
        except ValueError:
            return RecordsCheck(False, len(lines), index, "not JSON")
        if not isinstance(entry, dict):
            return RecordsCheck(False, len(lines), index, "not JSON")
        if entry.get("previous_hash") != previous:
            return RecordsCheck(False, len(lines), index, "previous_hash does not match")
        if "receipt_id" in entry or "receipt" in entry or "verdict" in entry:
            return RecordsCheck(False, len(lines), index, "not an offline record")
        try:
            text = covered_text(entry)
        except ValueError:
            return RecordsCheck(False, len(lines), index, "chain_hash does not match")
        if sha256_hex(text) != entry.get("chain_hash"):
            return RecordsCheck(False, len(lines), index, "chain_hash does not match")
        signature = entry.get("agent_signature")
        if public_key is not None and (
            not isinstance(signature, str) or not verify_signature(public_key, text, signature)
        ):
            return RecordsCheck(False, len(lines), index, "agent_signature does not verify")
        previous = str(entry["chain_hash"])
    return RecordsCheck(True, len(lines))


# ─── Files ────────────────────────────────────────────────────────────


@contextlib.contextmanager
def folder_lock(folder: Path) -> Iterator[None]:
    """Hold ``folder``'s lock: a file made exclusively, naming this process.
    A lock whose process has ended, or older than 30 seconds, is taken over.
    The TypeScript core takes the same lock the same way."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / LOCK_FILE
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS
    while True:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if _stale(path):
                path.unlink(missing_ok=True)
                continue
            if time.monotonic() > deadline:
                raise OfflineError("the offline files are held by another process") from None
            time.sleep(0.01)
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        break
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _stale(path: Path) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if age > _LOCK_STALE_SECONDS:
        return True
    if not text.isdigit():
        return age > 1.0
    try:
        os.kill(int(text), 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle)
    os.replace(temporary, path)


def _records(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in text.split("\n") if line]


def _count(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            return sum(1 for line in handle if line.strip())
    except FileNotFoundError:
        return 0


def _digest(result: Any) -> str | None:
    if result is NO_RESULT:
        return None
    try:
        return sha256_hex(canonical_json(result))
    except ValueError:
        return None


def _whole(value: float | None) -> int | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return max(0, round(value))


def _due(at: Any, now: datetime) -> bool:
    moment = _parse(at)
    return moment is None or moment <= now


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: FURB162 - Python 3.10 reads no Z
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def to_stderr(message: str) -> None:
    """Print one line for the person, on standard error."""
    print(message, file=sys.stderr, flush=True)


__all__ = [
    "ACCEPT_WINDOW",
    "ASK_FIRST",
    "ASK_MESSAGE",
    "AUTOMATIC",
    "DEFAULT_SETTING",
    "NOT_REVIEWED",
    "RECORDS_PATH",
    "SETTINGS",
    "SETTING_NAMES",
    "SETTING_PATH",
    "STOP",
    "STOPPED_MESSAGE",
    "UNREACHABLE_MESSAGE",
    "AgentOffline",
    "OfflineDecision",
    "OfflineError",
    "OfflineMode",
    "RecordsCheck",
    "accept",
    "accept_window_closed",
    "agent_folder",
    "agents",
    "chain_hash",
    "chain_of",
    "covered_text",
    "is_unreachable",
    "kept_message",
    "offline_root",
    "reconnected_message",
    "records_file",
    "to_stderr",
    "verify_records",
]
