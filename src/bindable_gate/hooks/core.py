# bindable-gate/src/bindable_gate/hooks/core.py
"""The Agent Hook Gate's core: one tool call of an agent CLI, before and after.

An adapter for each CLI turns the CLI's hook input into a :class:`Call` and
the core's answer into what that CLI understands. The core knows no CLI.

- :meth:`HookGate.before` proposes the call through the Library Gate's
  ``propose()`` and answers :class:`Allow` or :class:`Deny`. A Receipt that
  releases the call is kept until the call has run.
- :meth:`HookGate.after` reports ``released`` for a call that ran, through the
  Library Gate's ``report()``, and answers :class:`Report`.
- :meth:`HookGate.withheld` reports ``blocked`` for a released call that the
  CLI then did not run, for example one refused at its permission prompt.
- :meth:`HookGate.sweep` does the same for every call of a session still
  waiting, when the CLI says the session's turn or the session has ended.

FAIL-CLOSED. :meth:`before` answers within its own deadline, set under the
CLI's hook timeout, and every error becomes a :class:`Deny` with one plain
sentence. A CLI that lets a tool through when its hook times out or crashes
must never see either from here.

The core holds no review, Receipt or hash logic. The release decision is the
Library's ``release_permitted``; the payload rule and the credential mask are
the Library's ``named_fields`` and ``mask_credentials``, which the adapter
applies before it builds the :class:`Call`.

A hook is a ``runtime_plugin`` Gate, and its reports carry ``runtime_plugin``
(through the one switch in :mod:`bindable_gate.contract.kinds`) and ``gate_version``
``<cli>/<version>``, so the Track names the runtime. A
Proposal carries neither: the platform's Proposal has no such field.

STANDALONE. With no keychain item in the configuration there is no Gate
credential: :meth:`before` allows every call and :meth:`after` appends it to
the local Track, as the TypeScript core does. Nothing is sent anywhere.

With an agent key, every Proposal is signed and so is every local Track line.

OFFLINE. Where Bindable is unreachable (no answer, a timeout or a 5xx), the
agent's offline setting decides, in :meth:`HookGate._unavailable` and through
:mod:`bindable_gate.contract.offline`, as for every Gate: blocked with the
person's words, or allowed unreviewed and kept as an offline record once the
call has run. What the person is to be told travels as ``notice`` on the
answer, for the adapter to show the way its CLI shows it.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, TypeVar

import httpx

from bindable_gate.contract.kinds import RUNTIME_PLUGIN, report_gate_kind
from bindable_gate.contract.local_track import NO_RESULT, LocalTrack
from bindable_gate.contract.offline import OfflineDecision, OfflineMode
from bindable_gate.contract.signing import AgentKey
from bindable_gate.core.derivation import release_permitted
from bindable_gate.core.errors import GateConfigError, TrackpointNotRecorded
from bindable_gate.hooks.config import HookConfig
from bindable_gate.hooks.credential import (
    CredentialReader,
    CredentialUnavailable,
    read_keychain,
)
from bindable_gate.library._version import library_version
from bindable_gate.library.errors import ProposalRefused, ProposalUnreachable
from bindable_gate.library.gate import Gate
from bindable_gate.library.outcome import Identifiers

#: What the hook reports as: a ``runtime_plugin``.
REPORT_GATE_KIND: Final[str] = report_gate_kind(RUNTIME_PLUGIN)

#: Seconds the Library waits on one call, and its attempts at one Proposal.
CALL_TIMEOUT_SECONDS: Final[float] = 10.0
ATTEMPTS: Final[int] = 2

#: The gaps log, in the state folder: one line per report that failed.
GAPS_LOG: Final[str] = "gaps.log"

_ID: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_\-.]{1,200}$")

T = TypeVar("T")


# ─── What goes in and what comes out ───────────────────────────────────


@dataclass(frozen=True)
class Call:
    """One tool call, as an adapter normalises it.

    ``payload`` must already be safe: the named fields of the tool's input and
    nothing else, with credentials masked (``named_fields``).
    """

    cli: str
    tool_name: str
    tool_use_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    session_id: str = ""
    #: What the tool returned, for the local Track. ``NO_RESULT`` when unknown.
    result: Any = NO_RESULT
    #: What went wrong, where the tool failed.
    error: str | None = None
    #: How long the tool ran, where the CLI says.
    duration_ms: float | None = None


@dataclass(frozen=True)
class Allow:
    """Action Review passed, or offline mode let the call run unreviewed
    (``receipt_id`` empty). The CLI's own permission check still applies.
    ``notice`` is what the person is to be told, where there is something."""

    receipt_id: str
    notice: str = ""


@dataclass(frozen=True)
class Deny:
    """The call must not run. ``reason`` is one plain sentence or more."""

    reason: str
    receipt_id: str | None = None


@dataclass(frozen=True)
class Report:
    """What became of one report. ``recorded`` is false for a gap."""

    receipt_id: str | None
    outcome: str | None
    recorded: bool
    detail: str = ""


class _Timeout(Exception):
    pass


# ─── The core ──────────────────────────────────────────────────────────


class HookGate:
    """The Library Gate in front of an agent CLI's tool calls."""

    def __init__(
        self,
        config: HookConfig,
        *,
        cli: str,
        deadline_seconds: float,
        credential_reader: CredentialReader = read_keychain,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Bind the hook to one agent and one CLI.

        Raises:
            ConfigError: The agent key is configured and cannot be used.

        Args:
            config: The administrator's configuration.
            cli: The CLI's name as it appears in ``gate_version``.
            deadline_seconds: The longest :meth:`before` may take. Set it
                under the CLI's hook timeout, so the hook answers first.
            credential_reader: Reads the Gate credential. The keychain by
                default.
            transport: An ``httpx`` transport, for a test that answers without
                a network. It changes where calls go and nothing about what is
                released.
        """
        if deadline_seconds <= 0:
            raise GateConfigError("deadline_seconds must be more than zero")
        self.config = config
        self.cli = cli
        self.deadline_seconds = deadline_seconds
        self.gate_version = f"{cli}/{library_version()}"
        self._read_credential = credential_reader
        self._transport = transport
        self._agent_key = config.agent_key()
        self._track = LocalTrack(
            config.local_track, agent_id=config.agent_shown, key=self._agent_key
        ) if config.standalone else None
        self._notices: list[str] = []
        #: What the person is to be told about the last call :meth:`before`
        #: allowed, for an adapter that shows it apart from the answer.
        self.last_notice = ""
        self._offline: OfflineMode | None = None
        keychain, base_url = config.keychain, config.base_url
        if self._track is None and keychain is not None and base_url and config.agent_shown:
            self._offline = OfflineMode(
                agent_id=config.agent_shown,
                base_url=base_url,
                credential=lambda: self._read_credential(keychain),
                gate_version=self.gate_version,
                key=self._agent_key,
                transport=transport,
                notify=self._notices.append,
            )

    @property
    def standalone(self) -> bool:
        return self._track is not None

    @property
    def agent_key(self) -> AgentKey | None:
        """The agent key every Proposal is signed with, or None: nothing is signed."""
        return self._agent_key

    @property
    def local_track_path(self) -> Path | None:
        return self._track.path if self._track is not None else None

    # ─── Before ────────────────────────────────────────────────────────

    def before(self, call: Call) -> Allow | Deny:
        """Propose the call. :class:`Allow` only when the Receipt releases it,
        or offline mode lets it run unreviewed."""
        self._notices.clear()
        try:
            answer = self._within(lambda: self._before(call))
        except Exception as error:  # noqa: BLE001 - every error is decision unavailable
            answer = self._unavailable(call, error)
        notices = " ".join(self._notices)
        self._notices.clear()
        if notices and isinstance(answer, Deny):
            answer = Deny(f"{notices} {answer.reason}", answer.receipt_id)
        elif notices and isinstance(answer, Allow):
            answer = Allow(answer.receipt_id, " ".join(n for n in (notices, answer.notice) if n))
        self.last_notice = answer.notice if isinstance(answer, Allow) else ""
        return answer

    def _unavailable(self, call: Call, error: BaseException) -> Allow | Deny:
        """No decision could be obtained for ``call``.

        The one place every hook Gate answers a call without a Receipt: a
        timeout, an unreachable or refusing platform, a credential that cannot
        be read, a key that cannot sign, or any other error. Where Bindable is
        unreachable, the agent's offline setting decides; every other case,
        and *stop the agent*, denies with one sentence.
        """
        if self._offline is not None:
            decision = self._offline.failed(error, timed_out=isinstance(error, _Timeout))
            if decision is not None:
                return self._offline_answer(call, decision)
        if isinstance(error, _Timeout):
            return Deny(
                f"Action Review did not answer within {self.deadline_seconds:g} "
                f"seconds, so Action Gate blocked {call.tool_name or 'the tool'}."
            )
        return Deny(self._sentence(call, error))

    def _before(self, call: Call) -> Allow | Deny:
        if self._track is not None:
            return self._before_standalone(call)
        if not call.tool_name:
            raise _Unusable("The hook input names no tool, so Action Gate blocked it.")
        path = self._state_path(call.session_id, call.tool_use_id)
        if self._offline is not None:
            decision = self._offline.before()
            if decision is not None:
                return self._offline_answer(call, decision)
        identifiers = Identifiers.mint()
        with self._gate() as gate:
            review = gate.review(
                self.config.agent_shown or "",
                call.tool_name,
                dict(call.payload),
                proposed_action_id=identifiers.proposed_action_id,
                nonce=identifiers.nonce,
                proposed_at=identifiers.proposed_at,
            )
            if self._offline is not None:
                self._offline.reviewed()
            receipt = review.receipt
            receipt_id = str(receipt.get("receipt_id") or "")
            if release_permitted({"verdict": receipt.get("verdict"), "receipt": receipt}):
                try:
                    _write_private(path, {
                        "cli": call.cli,
                        "tool_name": call.tool_name,
                        "proposed_at": identifiers.proposed_at,
                        "receipt": dict(receipt),
                    })
                except OSError:
                    self._report(gate, call, receipt, identifiers.proposed_at, "blocked")
                    raise _Unusable(
                        f"The hook could not keep the Receipt in {path.parent}, "
                        f"so Action Gate blocked {call.tool_name}."
                    ) from None
                return Allow(receipt_id)
            self._report(gate, call, receipt, identifiers.proposed_at, "blocked")
            return Deny(_refusal(call.tool_name, receipt), receipt_id or None)

    def _offline_answer(self, call: Call, decision: OfflineDecision) -> Allow | Deny:
        """Offline mode's answer. A call it lets run is kept, like a released
        one, until the CLI says it ran; then it becomes an offline record."""
        if not decision.run:
            return Deny(decision.message)
        try:
            _write_private(self._state_path(call.session_id, call.tool_use_id), {
                "cli": call.cli,
                "tool_name": call.tool_name,
                "payload": dict(call.payload),
                "started_at": _now(),
                "offline": decision.as_json(),
            })
        except (OSError, _Unusable, TypeError, ValueError):
            return Deny(f"The hook could not keep the call's place, so Action Gate blocked {call.tool_name}.")
        return Allow("", decision.message)

    def _before_standalone(self, call: Call) -> Allow:
        """Allow, and keep what the local Track line needs once the call has run.
        A call whose start cannot be kept is still allowed: the local Track is
        a record, not a decision."""
        try:
            _write_private(self._state_path(call.session_id, call.tool_use_id), {
                "cli": call.cli,
                "tool_name": call.tool_name,
                "payload": dict(call.payload),
                "started_at": _now(),
            })
        except (OSError, _Unusable, TypeError, ValueError):
            pass
        return Allow("")

    # ─── After ─────────────────────────────────────────────────────────

    def after(self, call: Call) -> Report:
        """Report ``released`` for a call that ran, or failed while running."""
        return self._settle(call, "released")

    def withheld(self, call: Call) -> Report:
        """Report ``blocked`` for a released call the CLI did not run."""
        return self._settle(call, "blocked")

    def sweep(self, session_id: str) -> list[Report]:
        """Report ``blocked`` for every call of the session still waiting.

        A call still waiting when the CLI ends the turn or the session never
        ran: the CLI refused it at its permission prompt, or the person stopped
        it, and the CLI sends no event for either.
        """
        folder = self._session_dir(session_id)
        if not folder.is_dir():
            return []
        reports: list[Report] = []
        for path in sorted(folder.glob("*.json")):
            reports.append(self._settle(
                Call(cli=self.cli, tool_name="", tool_use_id=path.stem, session_id=session_id),
                "blocked",
            ))
        return reports

    def _settle(self, call: Call, outcome: str) -> Report:
        if self._track is not None:
            return self._settle_standalone(call, outcome)
        receipt_id: str | None = None
        try:
            path = self._state_path(call.session_id, call.tool_use_id)
            if not path.is_file():
                if outcome == "released":
                    self._gap(call, None, "no Receipt was kept for this call")
                    return Report(None, outcome, False, "no Receipt was kept for this call")
                return Report(None, outcome, False, "no Receipt was waiting")
            record = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(record.get("offline"), Mapping):
                return self._settle_offline(call, outcome, path, record)
            receipt = record["receipt"]
            receipt_id = str(receipt.get("receipt_id") or "") or None
            tool = call.tool_name or str(record.get("tool_name") or "")
            call = Call(call.cli, tool, call.tool_use_id, {}, call.session_id)

            def deliver() -> Report:
                with self._gate() as gate:
                    gate.report(
                        receipt,
                        outcome=outcome,
                        proposed_at=record["proposed_at"],
                        enforced_at=_enforced_at(receipt),
                        gate_kind=REPORT_GATE_KIND,
                        gate_version=self.gate_version,
                    )
                return Report(receipt_id, outcome, True)

            report = self._within(deliver)
            path.unlink(missing_ok=True)
            return report
        except _Timeout:
            detail = f"the report did not finish within {self.deadline_seconds:g} seconds"
        except Exception as error:  # noqa: BLE001 - a report never blocks
            detail = f"{type(error).__name__}: {error}"
        self._gap(call, receipt_id, detail)
        return Report(receipt_id, outcome, False, detail)

    def _settle_offline(self, call: Call, outcome: str, path: Path, record: Mapping[str, Any]) -> Report:
        """A call offline mode let run: an offline record once it has run, and
        nothing when it did not. Never reported: it has no Receipt."""
        kept: dict[str, Any] | None = None
        if outcome == "released" and self._offline is not None:
            kept = self._offline.record(
                OfflineDecision.from_json(record["offline"]),
                tool=call.tool_name or str(record.get("tool_name") or ""),
                params=record.get("payload") or {},
                started_at=str(record.get("started_at") or _now()),
                result=call.result,
                failed=call.error is not None,
                duration_ms=call.duration_ms,
            )
        path.unlink(missing_ok=True)
        if outcome == "released" and kept is None:
            self._gap(call, None, "the offline record was not written")
            return Report(None, outcome, False, "the offline record was not written")
        return Report(None, outcome, True, "offline record" if kept else "did not run")

    def _settle_standalone(self, call: Call, outcome: str) -> Report:
        """Append a call that ran to the local Track. A call that did not run
        leaves no line. Never raises."""
        track = self._track
        if track is None:
            return Report(None, outcome, False, "the hook is connected")
        try:
            path = self._state_path(call.session_id, call.tool_use_id)
            record: Mapping[str, Any] = {}
            if path.is_file():
                record = json.loads(path.read_text(encoding="utf-8"))
            if outcome == "released":
                track.append(
                    tool=call.tool_name or str(record.get("tool_name") or ""),
                    params=record.get("payload") or dict(call.payload),
                    started_at=str(record.get("started_at") or _now()),
                    result=call.result,
                    error=call.error,
                    duration_ms=call.duration_ms,
                )
            path.unlink(missing_ok=True)
            return Report(None, outcome, True)
        except Exception as error:  # noqa: BLE001 - a record never blocks
            detail = f"the local Track was not written: {type(error).__name__}: {error}"
            self._gap(call, None, detail)
            return Report(None, outcome, False, detail)

    # ─── Parts ─────────────────────────────────────────────────────────

    def _gate(self) -> Gate:
        keychain, base_url = self.config.keychain, self.config.base_url
        if keychain is None or base_url is None:
            raise _Unusable("The hook has no Gate credential configured, so it cannot propose.")
        return Gate(
            base_url=base_url,
            credential=self._read_credential(keychain),
            review_context=self.config.review_context,
            timeout=CALL_TIMEOUT_SECONDS,
            attempts=ATTEMPTS,
            transport=self._transport,
            agent_key=self._agent_key,
        )

    def _report(self, gate: Gate, call: Call, receipt: Mapping[str, Any], proposed_at: str, outcome: str) -> None:
        """Report at once; a failure is a gap and changes nothing else."""
        try:
            gate.report(
                receipt,
                outcome=outcome,
                proposed_at=proposed_at,
                enforced_at=_enforced_at(receipt),
                gate_kind=REPORT_GATE_KIND,
                gate_version=self.gate_version,
            )
        except TrackpointNotRecorded as error:
            self._gap(call, str(receipt.get("receipt_id") or "") or None, f"{outcome} report failed: {error}")

    def _within(self, work: Callable[[], T]) -> T:
        """``work()``, or :class:`_Timeout` once the deadline passes. The
        worker is a daemon thread: the process exits without waiting for it."""
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["value"] = work()
            except BaseException as error:  # noqa: BLE001 - carried to the caller
                box["error"] = error

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(self.deadline_seconds)
        if worker.is_alive():
            raise _Timeout()
        if "error" in box:
            raise box["error"]
        return box["value"]  # type: ignore[no-any-return]

    def _session_dir(self, session_id: str) -> Path:
        name = session_id or "no-session"
        if not _ID.match(name):
            raise _Unusable("The hook input carries a session id the hook cannot use.")
        return self.config.state_dir / name

    def _state_path(self, session_id: str, tool_use_id: str) -> Path:
        if not _ID.match(tool_use_id or ""):
            raise _Unusable("The hook input carries no usable tool-use id, so Action Gate blocked the tool.")
        return self._session_dir(session_id) / f"{tool_use_id}.json"

    def _gap(self, call: Call, receipt_id: str | None, detail: str) -> None:
        """One line in the gaps log. It never raises."""
        try:
            line = "\t".join((
                _now(), call.cli, call.session_id or "-", call.tool_use_id or "-",
                call.tool_name or "-", receipt_id or "-", " ".join(detail.split())[:300],
            ))
            self.config.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(
                self.config.state_dir / GAPS_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:  # noqa: BLE001, S110 - a gap that cannot be logged still must not block
            pass

    def _sentence(self, call: Call, error: BaseException) -> str:
        """One plain sentence for an error :meth:`before` could not get past."""
        tool = call.tool_name or "the tool"
        if isinstance(error, (_Unusable, CredentialUnavailable)):
            return str(error)
        if isinstance(error, ProposalRefused):
            return (
                f"Action Review refused the Proposal ({error.status} {error.code or 'refused'}), "
                f"so Action Gate blocked {tool}. Check the Gate credential and the agent "
                "in the hook configuration."
            )
        if isinstance(error, ProposalUnreachable):
            return (
                f"Action Review could not be reached, so Action Gate blocked {tool}. "
                "Check the network and the base URL in the hook configuration."
            )
        if isinstance(error, GateConfigError):
            return f"The Library Gate could not be set up, so Action Gate blocked {tool}: {error}."
        return f"The agent hook failed with {type(error).__name__}, so Action Gate blocked {tool}."


class _Unusable(Exception):
    """Something in the input or on disk the hook cannot use; one sentence."""


def _refusal(tool_name: str, receipt: Mapping[str, Any]) -> str:
    verdict = receipt.get("verdict") or "no verdict"
    rule = receipt.get("failed_rule")
    sentence = f"Action Gate blocked {tool_name}. Action Review returned {verdict}"
    sentence += f" under the rule {rule}." if rule else "."
    detail = receipt.get("reason_detail")
    if isinstance(detail, str) and detail.strip():
        sentence += " " + " ".join(detail.split())
    return sentence + f" Receipt {receipt.get('receipt_id', '-')}."


def _enforced_at(receipt: Mapping[str, Any]) -> str:
    """The Library's own rule: now, or the decision's time if the clock reads earlier."""
    return str(Gate._enforced_at(receipt))  # the Library's rule, not a copy


def _write_private(path: Path, record: Mapping[str, Any]) -> None:
    """``record`` as JSON at ``path``, mode 600, in a folder of mode 700, written whole."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(record, handle)
    os.replace(temporary, path)


def _now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
