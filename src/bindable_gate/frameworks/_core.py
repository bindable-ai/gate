# bindable-gate/src/bindable_gate/frameworks/_core.py
"""What every framework adapter shares: one decision, one report, one refusal.

An adapter puts the Gate in front of a framework's tool calls. Whatever hook
the framework offers, it does the same four things, and they are all here
so that no adapter does them differently:

1. **Propose.** The tool call becomes a Proposal: the tool's name is the
   ``action_type``, its arguments the ``payload``, and the agent is its
   ``did:webvh`` (``BINDABLE_AGENT_ID``). Where an agent key is set
   (``BINDABLE_AGENT_KEY_FILE``, ``BINDABLE_AGENT_KEY_ID``) the Library Gate
   signs it, exactly as it does for every other form.
2. **Decide.** :meth:`FrameworkGate.decide` asks the Action Review and returns
   a :class:`Decision`. It never raises: an adapter's hook that raised would,
   in most frameworks, either abort the agent's run or — in CrewAI — let the
   tool run. Every way of not getting a decision becomes one blocked Decision,
   made in one function, :func:`decision_unavailable`. Where Bindable itself
   is unreachable (no answer, a timeout or a 5xx), the agent's offline setting
   decides first (:mod:`bindable_gate.contract.offline`): the call is blocked
   with the person's words, or released unreviewed, with no Receipt, and kept
   as an offline record when it is enforced.
3. **Release or block.** The adapter runs the tool only on ``released``, and
   otherwise hands the model :meth:`Decision.message` in the tool's place.
4. **Report.** :meth:`FrameworkGate.enforced` queues the outcome for the
   Receipt on a background thread. A report never delays a tool, never fails
   one, and never changes what the model is told; one that cannot be delivered
   is a missing Trackpoint, not an unenforced action.

The report says what reported: ``gate_kind`` is ``framework_adapter`` once the
platform takes it (``bindable_gate.contract.kinds``; until then the one switch
there makes it ``python_library``), and ``gate_version`` is
``<framework>/<version>``, the framework's own installed version.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import queue
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import metadata
from typing import Any, Final

import httpx

from bindable_gate.contract.agent import parse_agent_id
from bindable_gate.contract.kinds import FRAMEWORK_ADAPTER, report_gate_kind
from bindable_gate.contract.offline import OfflineDecision, OfflineMode, to_stderr
from bindable_gate.contract.signing import (
    AgentKey,
    check_key_id,
    load_agent_key,
)
from bindable_gate.core.derivation import BLOCKED, RELEASED
from bindable_gate.core.errors import GateConfigError, TrackpointNotRecorded
from bindable_gate.library import Gate as LibraryGate
from bindable_gate.library._clock import now
from bindable_gate.library.outcome import REVIEW_CONTEXTS
from bindable_gate.mcp.proposal import ProposalError, build_proposal

log = logging.getLogger(__name__)

#: What a framework adapter reports as, once the platform takes it.
GATE_KIND: Final[str] = report_gate_kind(FRAMEWORK_ADAPTER)

#: The settings an adapter reads, the same names every customer-run form uses.
API_BASE_URL_ENV: Final[str] = "BINDABLE_API_BASE_URL"
GATE_CREDENTIAL_ENV: Final[str] = "BINDABLE_GATE_CREDENTIAL"
AGENT_ID_ENV: Final[str] = "BINDABLE_AGENT_ID"
REVIEW_CONTEXT_ENV: Final[str] = "BINDABLE_REVIEW_CONTEXT"
AGENT_KEY_FILE_ENV: Final[str] = "BINDABLE_AGENT_KEY_FILE"
AGENT_KEY_ID_ENV: Final[str] = "BINDABLE_AGENT_KEY_ID"
REQUIRED_ENV: Final[tuple[str, ...]] = (
    API_BASE_URL_ENV,
    GATE_CREDENTIAL_ENV,
    AGENT_ID_ENV,
    REVIEW_CONTEXT_ENV,
)

#: The adapter's own codes for a call that did not run for a reason the review
#: did not give.
DECISION_UNAVAILABLE: Final[str] = "decision_unavailable"
CALL_UNMAPPABLE: Final[str] = "call_unmappable"

#: How long closing waits for queued reports to be delivered.
DRAIN_SECONDS: Final[float] = 10.0


@dataclass(frozen=True)
class Decision:
    """What the Gate decided about one tool call.

    Attributes:
        released: Whether the tool may run. Nothing else releases it.
        tool_name: The tool, as the framework named it.
        receipt: The Receipt the review minted, where there is one.
        proposed_at: When the Proposal was made, for the report.
        reason_code: The platform's reason, where the review gave one.
        failed_rule: The rule that failed, where one did.
        code: The adapter's own code, where no review decided.
        detail: One line for the model and the log. Never a secret.
        offline: Offline mode's decision, where Bindable was unreachable and
            the agent's setting decided. A released one ran unreviewed and
            has no Receipt.
        payload: What the Proposal would have carried, for the offline record.
    """

    released: bool
    tool_name: str
    receipt: Mapping[str, Any] | None = None
    proposed_at: str = ""
    reason_code: str | None = None
    failed_rule: str | None = None
    code: str | None = None
    detail: str | None = None
    offline: OfflineDecision | None = None
    payload: Mapping[str, Any] | None = None

    @property
    def receipt_id(self) -> str | None:
        value = (self.receipt or {}).get("receipt_id")
        return value if isinstance(value, str) and value else None

    @property
    def message(self) -> str:
        """What the model is told in place of a blocked tool's result."""
        if self.offline is not None and self.offline.message:
            return self.offline.message
        reason = self.reason_code or self.code or "refused"
        text = f"Blocked by the Bindable Action Gate ({reason}): {self.tool_name} did not run."
        if self.receipt_id:
            text += f" Receipt {self.receipt_id}."
        if self.failed_rule:
            text += f" Failed rule {self.failed_rule}."
        if self.detail:
            text += f" {self.detail}"
        return text


def decision_unavailable(tool_name: str, detail: str, *, proposed_at: str = "") -> Decision:
    """The one answer for every way of not getting a decision: blocked.

    The only place an adapter decides without the Action Review. When
    Bindable is unreachable, offline mode answers first and this is the
    answer under *stop the agent*.
    """
    return Decision(
        released=False,
        tool_name=tool_name,
        proposed_at=proposed_at,
        code=DECISION_UNAVAILABLE,
        detail=detail,
    )


class FrameworkGate:
    """One agent's Gate inside one framework.

    Built once, at the customer's startup, and shared by every tool call the
    agent makes. ``framework`` and ``distribution`` name the framework for
    ``gate_version``: ``langgraph`` and the PyPI distribution whose version is
    the framework's.
    """

    def __init__(
        self,
        *,
        framework: str,
        distribution: str,
        api_base_url: str,
        gate_credential: str,
        agent_id: str,
        review_context: str,
        agent_key: AgentKey | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if review_context not in REVIEW_CONTEXTS:
            raise GateConfigError(
                f"{REVIEW_CONTEXT_ENV} must be one of {', '.join(REVIEW_CONTEXTS)}"
            )
        # The agent's did:webvh DID, the only agent identifier the platform
        # takes; a bare Agent ID is refused here, by name.
        self.agent_id = parse_agent_id(agent_id, setting=AGENT_ID_ENV).did
        self.framework = framework
        self.review_context = review_context
        self.gate_version = f"{framework}/{_installed(distribution)}"
        self._library = LibraryGate(
            base_url=api_base_url,
            credential=gate_credential,
            review_context=review_context,
            timeout=timeout,
            transport=transport,
            agent_key=agent_key,
        )
        self._offline = OfflineMode(
            agent_id=self.agent_id,
            base_url=api_base_url,
            credential=gate_credential,
            gate_version=self.gate_version,
            key=agent_key,
            transport=transport,
            notify=_tell,
        )
        self._reports: queue.Queue[tuple[Mapping[str, Any], str, str, str] | None] = (
            queue.Queue()
        )
        self._worker = threading.Thread(
            target=self._deliver, name=f"bindable-gate-{framework}-reports", daemon=True
        )
        self._worker.start()
        atexit.register(self.close)

    @classmethod
    def from_env(
        cls,
        *,
        framework: str,
        distribution: str,
        environ: Mapping[str, str] | None = None,
        **overrides: Any,
    ) -> FrameworkGate:
        """The Gate, configured from the environment.

        Raises:
            GateConfigError: Naming each missing setting, by name only; or the
                agent key settings, where a key is set and cannot be used.
        """
        source = os.environ if environ is None else environ
        missing = [name for name in REQUIRED_ENV if not source.get(name, "").strip()]
        if missing:
            raise GateConfigError(
                "missing required environment variable(s): " + ", ".join(missing)
            )
        agent_id = source[AGENT_ID_ENV].strip()
        return cls(
            framework=framework,
            distribution=distribution,
            api_base_url=source[API_BASE_URL_ENV].strip(),
            gate_credential=source[GATE_CREDENTIAL_ENV].strip(),
            agent_id=agent_id,
            review_context=source[REVIEW_CONTEXT_ENV].strip(),
            agent_key=_agent_key(source, agent_id),
            **overrides,
        )

    # ─── Deciding ──────────────────────────────────────────────────────

    def decide(
        self, tool_name: str, arguments: Any, *, target: str | None = None
    ) -> Decision:
        """Propose this call and return the Decision. Never raises.

        ``arguments`` is what the framework holds: a mapping, or a JSON object
        as text. Anything that is not an object is not a call the Gate can
        propose, and it is blocked.
        """
        proposed_at = now()
        try:
            proposal = build_proposal({"name": tool_name, "arguments": _object(arguments)})
        except ProposalError as exc:
            return Decision(
                released=False,
                tool_name=tool_name,
                proposed_at=proposed_at,
                code=CALL_UNMAPPABLE,
                detail=f"The call could not be proposed: {exc}.",
            )
        offline = self._offline.before()
        if offline is not None:
            return self._unreviewed(tool_name, proposal, offline)
        try:
            review = self._library.review(
                self.agent_id,
                proposal["action_type"],
                proposal["payload"],
                target,
                proposed_action_id=proposal["proposed_action_id"],
                nonce=proposal["nonce"],
                proposed_at=proposed_at,
            )
        except Exception as exc:  # noqa: BLE001 - every failure to decide blocks
            # Unreachable, timed out, refused, unreadable, passed with no
            # Receipt, a key that would not sign: no decision to release on,
            # unless Bindable is unreachable and the offline setting decides.
            offline = self._offline.failed(exc)
            if offline is not None:
                return self._unreviewed(tool_name, proposal, offline)
            log.warning(
                "no decision; blocked",
                extra={"fields": {"tool": tool_name, "fault": type(exc).__name__}},
            )
            return decision_unavailable(
                tool_name,
                "The Action Review could not be reached for a decision.",
                proposed_at=proposed_at,
            )
        self._offline.reviewed()
        return Decision(
            released=review.released,
            tool_name=tool_name,
            receipt=review.receipt,
            proposed_at=proposed_at,
            reason_code=review.reason_code,
            failed_rule=review.failed_rule,
        )

    def _unreviewed(self, tool_name: str, proposal: Mapping[str, Any], offline: OfflineDecision) -> Decision:
        """Offline mode's Decision: no review, no Receipt."""
        if offline.message:
            _tell(offline.message)
        return Decision(
            released=offline.run,
            tool_name=tool_name,
            # The action starts now, after the outage began: not when it was proposed.
            proposed_at=now(),
            code=None if offline.run else DECISION_UNAVAILABLE,
            offline=offline,
            payload=dict(proposal["payload"]),
        )

    async def adecide(
        self, tool_name: str, arguments: Any, *, target: str | None = None
    ) -> Decision:
        """:meth:`decide`, off the event loop."""
        return await asyncio.to_thread(self.decide, tool_name, arguments, target=target)

    # ─── After the fact ────────────────────────────────────────────────

    def enforced(self, decision: Decision) -> None:
        """Queue the outcome of a Decision once it has been enforced. Never
        blocks, never raises. A Decision with no Receipt has nothing to report
        against, and is noted rather than sent. One offline mode released is
        kept as an offline record instead, since it has no Receipt."""
        if decision.offline is not None:
            if decision.released:
                self._offline.record(
                    decision.offline,
                    tool=decision.tool_name,
                    params=decision.payload or {},
                    started_at=decision.proposed_at or now(),
                )
            return
        if decision.receipt is None or decision.receipt_id is None:
            return
        try:
            self._reports.put_nowait(
                (
                    decision.receipt,
                    RELEASED if decision.released else BLOCKED,
                    decision.proposed_at,
                    now(),
                )
            )
        except Exception as exc:  # noqa: BLE001 - reporting may never propagate
            log.warning(
                "report dropped; enforcement was unaffected",
                extra={"fields": {"fault": type(exc).__name__}},
            )

    def run(self, tool_name: str, arguments: Any, call: Callable[[], Any]) -> tuple[Decision, Any]:
        """Decide, run ``call`` only on a release, report. For an adapter
        whose hook wraps the tool. The tool's own error propagates as it would
        have without the Gate; the release is reported either way."""
        decision = self.decide(tool_name, arguments)
        if not decision.released:
            self.enforced(decision)
            return decision, None
        try:
            return decision, call()
        finally:
            self.enforced(decision)

    async def arun(
        self, tool_name: str, arguments: Any, call: Callable[[], Any]
    ) -> tuple[Decision, Any]:
        """:meth:`run`, for a tool that is awaited."""
        decision = await self.adecide(tool_name, arguments)
        if not decision.released:
            self.enforced(decision)
            return decision, None
        try:
            return decision, await call()
        finally:
            self.enforced(decision)

    def close(self) -> None:
        """Deliver what is queued, within :data:`DRAIN_SECONDS`, then close."""
        if not self._worker.is_alive():
            return
        self._reports.put(None)
        self._worker.join(DRAIN_SECONDS)
        self._library.close()

    def _deliver(self) -> None:
        while True:
            item = self._reports.get()
            if item is None:
                return
            receipt, outcome, proposed_at, enforced_at = item
            try:
                self._library.report(
                    receipt,
                    outcome=outcome,
                    proposed_at=proposed_at,
                    enforced_at=enforced_at,
                    gate_kind=GATE_KIND,
                    gate_version=self.gate_version,
                )
            except TrackpointNotRecorded as exc:
                log.warning(
                    "trackpoint not recorded; enforcement was unaffected",
                    extra={"fields": {"receipt_id": exc.receipt_id}},
                )
            except Exception as exc:  # noqa: BLE001 - the worker must outlive any fault
                log.warning(
                    "report failed; enforcement was unaffected",
                    extra={"fields": {"fault": type(exc).__name__}},
                )


def _tell(message: str) -> None:
    """Offline mode's lines, for the person and the log."""
    to_stderr(message)
    log.warning(message)


def _object(arguments: Any) -> Any:
    """A tool's arguments as an object, from a mapping or JSON text."""
    if isinstance(arguments, (str, bytes)):
        import json

        try:
            return json.loads(arguments or "{}")
        except ValueError:
            return arguments
    if arguments is None:
        return {}
    if isinstance(arguments, Mapping):
        return dict(arguments)
    return arguments


def _installed(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "unknown"


def _agent_key(source: Mapping[str, str], agent_id: str) -> AgentKey | None:
    """The agent key, where ``BINDABLE_AGENT_KEY_FILE`` is set; otherwise none.

    Checked here, at startup, so a key that cannot sign stops the customer's
    process rather than blocking every call later.
    """
    path = source.get(AGENT_KEY_FILE_ENV, "").strip()
    if not path:
        return None
    did = parse_agent_id(agent_id, setting=AGENT_ID_ENV).did
    key_id = check_key_id(source.get(AGENT_KEY_ID_ENV), did=did)
    return load_agent_key(path, key_id)


__all__ = [
    "CALL_UNMAPPABLE",
    "DECISION_UNAVAILABLE",
    "GATE_KIND",
    "Decision",
    "FrameworkGate",
    "decision_unavailable",
]
