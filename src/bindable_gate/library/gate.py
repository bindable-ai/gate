# bindable-gate/src/bindable_gate/library/gate.py
"""The Library Gate: an Action Review in front of your own function.

The Library is a registered Gate that a customer runs. It submits the Proposal,
obtains the Receipt, releases the wrapped action only where the review passed,
and reports what it did. Those four things are one act, and the Library offers
no way to do any of them without the others.

    from bindable_gate import Gate

    gate = Gate(base_url=..., credential="bnd_gt_...", review_context="live")

    @gate.control(
        agent_id=AGENT,
        action_type="send_invoice",
        payload_fields=["customer", "amount_cents"],
    )
    def send_invoice(customer: str, amount_cents: int, api_key: str) -> str:
        return billing.send(customer, amount_cents, api_key)

    outcome = send_invoice(customer="acme", amount_cents=250_000, api_key=KEY)
    outcome.released        # True
    outcome.action_result   # what send_invoice returned
    outcome.receipt_id      # the Receipt that permitted it

FAIL-CLOSED. If the review is unreadable, refuses, or passes without a
Receipt to tie the release to, the wrapped function does not run and the
Library raises. There is no ``fail_open`` argument, no environment variable and
no test hook.

OFFLINE. Where Bindable itself is unreachable (no answer, a timeout or a 5xx),
the agent's offline setting decides, as it does for every Gate
(:mod:`bindable_gate.contract.offline`). The setting is kept in Bindable, not
here: *stop the agent* raises as above; *ask me first* raises
:class:`GateOffline` until the person runs ``bindable-gate offline accept``;
*switch automatically*, or an accepted *ask me first*, runs the function
unreviewed and returns an :class:`Outcome` whose ``outcome`` is ``offline``,
with an offline record and no Receipt. The records go to the Track when
Bindable is back.

The review context is set once, here, and never per call: a Gate belongs to one
context for its whole life. ``gate_id`` is never sent — the platform
takes it from the credential, and a Proposal carrying one is refused at the
boundary.

Two endpoints are called and no others: ``POST /v1/reviews`` for the Proposal and
``POST /v1/receipts/{receipt_id}/report`` for the enforcement outcome. Both
present the Gate credential as a bearer token, which is the only credential
either accepts.
"""

from __future__ import annotations

import functools
import inspect
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Literal

import httpx
from bindable.decision import BLOCKED, PASSED, RELEASED, decision_from

from bindable_gate.contract.agent import (
    AGENT_ID_FORM,
    AgentIdentity,
    parse_agent_id,
    wire_agent_id,
)
from bindable_gate.contract.offline import OfflineDecision, OfflineMode, to_stderr
from bindable_gate.contract.signing import AgentKey, sign_proposal
from bindable_gate.core.errors import (
    GateBlocked,
    GateConfigError,
    TrackpointNotRecorded,
)
from bindable_gate.library._clock import now, parse
from bindable_gate.library._value import refused_at
from bindable_gate.library._version import library_version
from bindable_gate.library.errors import (
    EvidenceRefused,
    EvidenceUnreachable,
    EvidenceValueRefused,
    GateOffline,
    ProposalRefused,
    ProposalUnreachable,
)
from bindable_gate.library.outcome import (
    GATE_KIND,
    OFFLINE,
    REVIEW_CONTEXTS,
    Identifiers,
    Outcome,
    Review,
)
from bindable_gate.library.payload import named_fields, without_secret

#: The two paths the Library calls.
REVIEWS_PATH: Final[str] = "/v1/reviews"
REPORT_PATH: Final[str] = "/v1/receipts/{receipt_id}/report"
EVIDENCE_PATH: Final[str] = "/v1/evidence"

#: Attempts at one Proposal, the first included. A transport failure is retried
#: because the platform answers the same Proposal with the Receipt it already
#: minted; a refusal is not retried, a refusal being an answer.
DEFAULT_ATTEMPTS: Final[int] = 3

OnBlocked = Literal["raise", "return"]


class Gate:
    """A registered Gate, bound to one review context for its whole life."""

    def __init__(
        self,
        *,
        base_url: str,
        credential: str,
        review_context: str,
        developer_key: str | None = None,
        timeout: float = 30.0,
        attempts: int = DEFAULT_ATTEMPTS,
        transport: httpx.BaseTransport | None = None,
        agent_key: AgentKey | None = None,
        offline_dir: str | Path | None = None,
        notify: Callable[[str], None] | None = None,
    ) -> None:
        """Bind the Gate. What is missing is named, never guessed.

        Args:
            base_url: Where the platform is.
            credential: The Gate credential, a ``bnd_gt_`` token. The only
                credential the review and the report accept.
                Its form is not checked: inside an OpenShell sandbox it is a
                placeholder the sandbox's proxy replaces. It is sent unchanged,
                in the ``Authorization`` header only.
            developer_key: The Account's API key, a ``bnd_sk_`` token, for
                ``submit_evidence`` and nothing else. Held apart from the Gate
                credential and never sent in its place: the two endpoints a Gate
                calls do not accept it, and the Evidence endpoint does not accept
                the Gate credential.
            review_context: ``test`` or ``live``. Set once, here. A Gate
                constructed without one raises before any network call.
            timeout: Seconds to wait on a call.
            attempts: Attempts at one Proposal, the first included. Every
                attempt presents the same identifiers, so at most one Receipt
                exists however many are made.
            transport: An ``httpx`` transport, for a test that answers without
                a network. It changes where the calls go and nothing about what
                is released.
            agent_key: The agent's ECDSA P-256 key (``load_agent_key``). Every
                Proposal is then signed, and one that cannot be signed is not
                sent. Without it nothing is signed.
            offline_dir: Where :meth:`run` keeps the offline files of each
                agent. ``~/.bindable/offline`` by default.
            notify: What prints the offline lines for the person. Standard
                error by default.

        Raises:
            GateConfigError: If the base URL, the credential or the review
                context is missing, or the context is not one the platform
                takes, or ``attempts`` is less than one.
        """
        missing = [
            name
            for name, value in (
                ("base_url", base_url),
                ("credential", credential),
                ("review_context", review_context),
            )
            if not value
        ]
        if missing:
            raise GateConfigError(
                "a Gate cannot be constructed without " + ", ".join(missing)
            )
        if review_context not in REVIEW_CONTEXTS:
            raise GateConfigError(
                f"review_context must be one of {', '.join(REVIEW_CONTEXTS)}; "
                f"got {review_context!r}"
            )
        if attempts < 1:
            raise GateConfigError(f"attempts must be at least 1; got {attempts}")
        self.review_context = review_context
        self.attempts = attempts
        self._base_url = base_url.rstrip("/")
        self._credential = credential
        self._developer_key = developer_key
        self._agent_key = agent_key
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._transport = transport
        self._offline_dir = offline_dir
        self._notify = notify
        self._offline: dict[str, OfflineMode] = {}

    # ─── The two calls ─────────────────────────────────────────────────

    def propose(
        self,
        agent_id: str,
        action_type: str,
        payload: Mapping[str, Any],
        target: str | None = None,
        *,
        proposed_action_id: str | None = None,
        nonce: str | None = None,
        proposed_at: str | None = None,
    ) -> Mapping[str, Any]:
        """Submit a Proposal and return the Receipt.

        The Receipt comes back whatever the verdict was: a Receipt records a
        review, and a review that failed is still a review that happened. To
        act on the verdict rather than read it, use :meth:`run`.

        Args:
            agent_id: The agent's DID, did:webvh:<SCID>:<host>:agent:<Agent ID>.
                Nothing else is taken.
            action_type: What is being proposed.
            payload: The action's own fields.
            target: What the action is directed at, where that applies.
            proposed_action_id: Supply this to retry a Proposal already made;
                a fresh one is minted when it is not given.
            nonce: The same, for the nonce.
            proposed_at: The same, for the time first proposed.

        Returns:
            The signed Receipt, as the platform persisted it.

        Raises:
            ProposalUnreachable: If no Receipt could be obtained, for any
                reason. It carries the identifiers to retry with.
            ProposalRefused: If the platform refused the Proposal.
        """
        return self.review(
            agent_id,
            action_type,
            payload,
            target,
            proposed_action_id=proposed_action_id,
            nonce=nonce,
            proposed_at=proposed_at,
        ).receipt

    def review(
        self,
        agent_id: str,
        action_type: str,
        payload: Mapping[str, Any],
        target: str | None = None,
        *,
        proposed_action_id: str | None = None,
        nonce: str | None = None,
        proposed_at: str | None = None,
    ) -> Review:
        """Submit a Proposal and return the whole answer, releasing nothing.

        :meth:`propose` returns the Receipt alone. This returns the verdict and
        the reason beside it, for a caller that holds the action itself and must
        tell whoever was refused why. It runs the same one release rule and
        reports nothing: obtaining a Receipt is not enforcing anything.

        Raises:
            ProposalUnreachable: If no Receipt could be obtained, for any reason.
                It carries the identifiers to retry with.
            ProposalRefused: If the platform refused the Proposal.
        """
        identifiers = Identifiers.mint().given(
            proposed_action_id=proposed_action_id,
            nonce=nonce,
            proposed_at=proposed_at,
        )
        receipt, answer = self._reviewed(
            agent_id, action_type, payload, target, identifiers
        )
        return Review(
            receipt=receipt,
            verdict=str(answer.get("verdict") or ""),
            released=self._releases(answer, receipt),
            identifiers=identifiers,
            reason_code=_text(answer.get("reason_code")),
            failed_rule=_text(answer.get("failed_rule")) or _text(receipt.get("failed_rule")),
        )

    def report(
        self,
        receipt: Mapping[str, Any],
        *,
        outcome: str,
        proposed_at: str,
        enforced_at: str,
        gate_kind: str = GATE_KIND,
        gate_version: str | None = None,
    ) -> Mapping[str, Any] | None:
        """Report the enforcement outcome for a Receipt.

        Carries the reporting form's ``gate_kind`` and version, so the Trackpoint
        says what enforced and which build of it did. The defaults are this
        Library's own; the MCP Gate passes its own, because a report labelled with
        the wrong form would put the enforcement under the wrong name.

        ``action_result_digest`` is sent as null. Computing one would mean
        canonicalising and hashing the result here, and this Library holds no
        hashing of its own.

        Returns:
            The Trackpoint the platform returned, or ``None`` where it was
            recorded and the answer could not be read as one.

        Raises:
            TrackpointNotRecorded: If the report could not be delivered.
        """
        receipt_id = receipt.get("receipt_id")
        if not isinstance(receipt_id, str) or not receipt_id:
            raise TrackpointNotRecorded(
                "",
                detail="the Receipt carries no receipt_id, so its outcome "
                "cannot be reported",
            )
        body = {
            "outcome": outcome,
            "gate_kind": gate_kind,
            "gate_version": gate_version or library_version(),
            "action_result_digest": None,
            "proposed_at": proposed_at,
            "enforced_at": enforced_at,
        }
        path = REPORT_PATH.format(receipt_id=receipt_id)
        last: str = ""
        for attempt in range(self.attempts):
            try:
                response = self._post(path, body, credential=self._credential)
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"
                continue
            if response.status_code in (200, 201):
                # 201 created it; 200 is an identical retry answered with the
                # Trackpoint already stored.
                recorded = self._json(response)
                return recorded if isinstance(recorded, Mapping) else None
            # A store that failed is a server fault and the Gate retries;
            # an identical retry returns the stored Trackpoint.
            if response.status_code == 503 and attempt + 1 < self.attempts:
                last = f"503 {self._refusal(response)[0]}"
                continue
            code, detail, request_id = self._refusal(response)
            raise TrackpointNotRecorded(
                receipt_id,
                detail=f"refused: {response.status_code} "
                f"{code or detail or 'refused'}"
                + (f" (request {request_id})" if request_id else ""),
            )
        raise TrackpointNotRecorded(
            receipt_id,
            detail=f"after {self.attempts} attempts; last was {last}",
        )

    def submit_evidence(
        self,
        agent_id: str,
        event_type: str,
        value: Mapping[str, Any],
        *,
        external_id: str,
        applicability: str,
        source: str,
        recorded_at: str | None = None,
        idempotency_key: str | None = None,
    ) -> Mapping[str, Any]:
        """Record Evidence a Contract rule may read.

        This is the one call that does not present the Gate credential. Evidence
        belongs to the Account, not to a Gate, and the endpoint takes a session or
        the Account's developer key and refuses a Gate credential. The key given
        at construction is used here and nowhere else.

        Args:
            agent_id: The agent the Evidence is about, by its did:webvh DID.
            event_type: What kind of fact this is.
            value: The fact itself. Canonical JSON with no floats — a float
                anywhere inside is refused before anything is sent.
            external_id: The Evidence's identity, the caller's own. A second
                submission under the same one is refused, not quietly accepted.
            applicability: ``test_only`` or ``test_and_live``.
            source: Where the fact came from.
            recorded_at: When it was recorded. Now, when not given.
            idempotency_key: Supply this to retry a submission you have already
                attempted. One is minted when it is not given, and the same one is
                presented on every attempt this call makes.

        Returns:
            The Evidence record the platform persisted, carrying ``evidence_id``
            and the digest it computed. Nothing is computed here.

        Raises:
            GateConfigError: No developer key was given at construction.
            EvidenceValueRefused: The value holds a float, or anything JSON
                cannot carry, such as a ``Decimal``; it names where. Nothing
                was sent.
            EvidenceRefused: The platform answered and refused, ``external_id_exists``
                among the reasons. Never a success.
            EvidenceUnreachable: No answer at all. Carries the key to retry with.
        """
        if not self._developer_key:
            raise GateConfigError(
                "submit_evidence needs the Account's developer key; construct the "
                "Gate with developer_key=. The Gate credential is not accepted here"
            )
        refused = refused_at(value)
        if refused is not None:
            raise EvidenceValueRefused(*refused)

        agent = _identity(agent_id)
        key = idempotency_key or uuid.uuid4().hex
        body = {
            "external_id": external_id,
            "agent_id": wire_agent_id(agent),
            "event_type": event_type,
            "value": dict(value),
            "applicability": applicability,
            "source": source,
            "recorded_at": recorded_at or now(),
        }
        last: str = ""
        for attempt in range(self.attempts):
            try:
                response = self._post(
                    EVIDENCE_PATH,
                    body,
                    credential=self._developer_key,
                    headers={"Idempotency-Key": key},
                )
            except httpx.HTTPError as exc:
                # The same key goes again, so an attempt that arrived and whose
                # answer was lost is answered with what it did, not repeated.
                last = f"{type(exc).__name__}: {exc}"
                continue
            if response.status_code == 201:
                recorded = self._json(response)
                if not isinstance(recorded, Mapping):
                    raise EvidenceUnreachable(
                        "the Evidence was recorded and the answer could not be read",
                        idempotency_key=key,
                    )
                return recorded
            # A store that failed is a server fault and is retried under the same
            # key; a refusal is an answer and is not.
            if response.status_code == 503 and attempt + 1 < self.attempts:
                last = f"503 {self._refusal(response)[0]}"
                continue
            code, detail, request_id = self._refusal(response)
            raise EvidenceRefused(response.status_code, code, detail, request_id)
        raise EvidenceUnreachable(
            f"the Evidence could not be submitted after {self.attempts} attempts; "
            f"last was {last}",
            idempotency_key=key,
        )

    # ─── The flow ──────────────────────────────────────────────────────

    def run(
        self,
        *,
        agent_id: str,
        action_type: str,
        payload: Mapping[str, Any],
        handler: Callable[[], Any],
        target: str | None = None,
        on_blocked: OnBlocked = "raise",
        identifiers: Identifiers | None = None,
    ) -> Outcome:
        """Review, then run or withhold, then report. The whole contract.

        The handler runs only where the verdict is exactly ``passed``.
        It never runs where a Receipt could not be obtained. The enforcement
        outcome is reported either way.

        Args:
            handler: The action. Called with no arguments, once, only on a
                pass.
            on_blocked: ``raise`` for :class:`GateBlocked`, or ``return`` for
                the blocked :class:`Outcome`. Neither releases anything.
            identifiers: The identifiers of a Proposal already attempted, to
                retry it. Fresh ones are minted when none are given.

        Where Bindable is unreachable, the agent's offline setting decides
        instead (see the module's OFFLINE note): the handler runs unreviewed
        and the :class:`Outcome` is ``offline``, with no Receipt, or
        :class:`GateOffline` is raised.

        Raises:
            ProposalUnreachable: No Receipt, so nothing ran.
            GateOffline: Bindable is unreachable and the agent's setting is
                *ask me first*; nothing ran.
            ProposalRefused: The platform refused the Proposal; nothing ran.
            GateBlocked: The review did not pass, with ``on_blocked="raise"``.
            TrackpointNotRecorded: The outcome could not be reported. Where the
                action had already run, its result is carried on the error.
        """
        retrying = identifiers is not None
        identifiers = identifiers or Identifiers.mint()
        offline = self._offline_for(agent_id)
        # A caller retrying a Proposal is asking for the review again, so it is
        # made even while offline mode would not try yet.
        decision = None if retrying else offline.before()
        if decision is not None:
            return self._unreviewed(offline, decision, action_type, payload, target, handler, identifiers)
        try:
            receipt, review = self._reviewed(
                agent_id, action_type, payload, target, identifiers
            )
        except (ProposalUnreachable, ProposalRefused) as exc:
            decision = offline.failed(exc)
            if decision is None:
                raise
            return self._unreviewed(offline, decision, action_type, payload, target, handler, identifiers)
        offline.reviewed()
        verdict = str(review.get("verdict") or "")
        released = self._releases(review, receipt)
        outcome = Outcome(
            outcome=RELEASED if released else BLOCKED,
            verdict=verdict,
            receipt=receipt,
            identifiers=identifiers,
            failed_rule=_text(review.get("failed_rule")) or _text(receipt.get("failed_rule")),
            reason_code=_text(review.get("reason_code")),
        )
        if released:
            outcome.action_result = handler()  # the action happens HERE
            outcome.ran = True
        outcome.enforced_at = self._enforced_at(receipt)
        try:
            outcome.trackpoint = self.report(
                receipt,
                outcome=outcome.outcome,
                proposed_at=identifiers.proposed_at,
                enforced_at=outcome.enforced_at,
            )
        except TrackpointNotRecorded as exc:
            # Never swallowed and never mistaken for a block. Where the action
            # ran, its result travels with the error rather than being lost.
            exc.action_result = outcome.action_result
            raise
        if outcome.blocked and on_blocked == "raise":
            raise GateBlocked(
                outcome.receipt_id,
                verdict,
                reason_code=outcome.reason_code,
                failed_rule=outcome.failed_rule,
                outcome=outcome,
            )
        return outcome

    def control(
        self,
        *,
        agent_id: str,
        action_type: str,
        payload_fields: Sequence[str] | None = None,
        on_blocked: OnBlocked = "raise",
    ) -> Callable[[Callable[..., Any]], Callable[..., Outcome]]:
        """Put a review in front of a function (US-LIB-01).

        The decorated function returns an :class:`Outcome`, never its own bare
        return value: what it produced and the Receipt that permitted it are
        one answer.

        Only the arguments named in ``payload_fields`` become the Proposal's
        payload, and every other argument stays in this process and is never
        sent. Name only what your Contract's rules need. Never
        put API keys, passwords, secrets or personal data into a payload.

        Args:
            payload_fields: Required. A non-empty list of the function's own
                argument names. Missing, empty, or naming an argument the
                function does not have, it raises before any call is made.

        Raises:
            GateConfigError: At decoration, if ``payload_fields`` is missing,
                empty, not a list of names, or names an argument the function
                does not take.
        """
        fields = _payload_fields(payload_fields)

        def decorate(handler: Callable[..., Any]) -> Callable[..., Outcome]:
            signature = inspect.signature(handler)
            _known_to(signature, fields, handler)

            @functools.wraps(handler)
            def gated(*args: Any, **kwargs: Any) -> Outcome:
                return self.run(
                    agent_id=agent_id,
                    action_type=action_type,
                    payload=_payload_from_call(signature, args, kwargs, fields),
                    handler=lambda: handler(*args, **kwargs),
                    on_blocked=on_blocked,
                )

            gated.action_type = action_type  # type: ignore[attr-defined]
            gated.undecorated = handler  # type: ignore[attr-defined]
            return gated

        return decorate

    def _offline_for(self, agent_id: str) -> OfflineMode:
        """The offline mode of one agent, made once."""
        did = _identity(agent_id).did
        mode = self._offline.get(did)
        if mode is None:
            mode = OfflineMode(
                agent_id=did,
                base_url=self._base_url,
                credential=self._credential,
                gate_version=library_version(),
                chain="library",
                key=self._agent_key,
                root=self._offline_dir,
                transport=self._transport,
                notify=self._notify,
            )
            self._offline[did] = mode
        return mode

    def _unreviewed(
        self,
        offline: OfflineMode,
        decision: OfflineDecision,
        action_type: str,
        payload: Mapping[str, Any],
        target: str | None,
        handler: Callable[[], Any],
        identifiers: Identifiers,
    ) -> Outcome:
        """Run, or withhold, an action while Bindable is unreachable. Nothing
        here is reviewed, reported or given a Receipt."""
        if decision.message:
            (self._notify or to_stderr)(decision.message)
        if not decision.run:
            raise GateOffline(
                decision.message,
                proposed_action_id=identifiers.proposed_action_id,
                nonce=identifiers.nonce,
                proposed_at=identifiers.proposed_at,
            )
        started_at = now()
        try:
            result = handler()  # the action happens HERE, unreviewed
        except BaseException:
            offline.record(decision, tool=action_type, params=payload, started_at=started_at, failed=True)
            raise
        record = offline.record(decision, tool=action_type, params=payload, started_at=started_at,
                                result=result)
        return Outcome(
            outcome=OFFLINE,
            verdict="",
            receipt={},
            identifiers=identifiers,
            ran=True,
            action_result=result,
            enforced_at=started_at,
            offline_record=record,
        )

    # payload_fields defaults to None only so that leaving it out raises
    # GateConfigError, which names it, rather than Python's own TypeError. It
    # is required all the same, and the published surface reads
    # this to say so rather than inferring "optional" from the default.
    control.required_keywords = ("payload_fields",)  # type: ignore[attr-defined]

    # ─── Reading the answer ────────────────────────────────────────────

    def _reviewed(
        self,
        agent_id: str,
        action_type: str,
        payload: Mapping[str, Any],
        target: str | None,
        identifiers: Identifiers,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        """The Receipt and the review that carried it, or nothing at all."""
        agent = _identity(agent_id)
        body: dict[str, Any] = {
            "agent_id": wire_agent_id(agent),
            "action_type": action_type,
            "payload": without_secret(dict(payload), self._credential),
            "proposed_action_id": identifiers.proposed_action_id,
            "nonce": identifiers.nonce,
            "proposed_at": identifiers.proposed_at,
            "review_context": self.review_context,
        }
        if target is not None:
            body["target"] = target
        # With a key every Proposal is signed; a signature that cannot be made
        # raises here, before anything is sent, never an unsigned Proposal.
        signature = self._signature(agent, body)
        if signature is not None:
            body.update(signature)
        review = self._reviewing(body, identifiers)
        receipt = review.get("receipt")
        if not isinstance(receipt, Mapping) or not receipt:
            # No Receipt is no release, whatever else the answer said. Result
            # code ``exception`` arrives here, at 200 and with no Receipt.
            raise ProposalUnreachable(
                "the review carried no Receipt"
                + (f" (result code {review['result_code']})" if review.get("result_code") else ""),
                proposed_action_id=identifiers.proposed_action_id,
                nonce=identifiers.nonce,
                proposed_at=identifiers.proposed_at,
            )
        return receipt, review

    def _signature(self, agent: AgentIdentity, body: Mapping[str, Any]) -> dict[str, str] | None:
        """The agent's signature over this Proposal, where a key was given."""
        if self._agent_key is None:
            return None
        if not self._agent_key.key_id.startswith(agent.did + "#"):
            raise GateConfigError("the agent key's id names another agent than agent_id")
        return sign_proposal(
            self._agent_key,
            persistent_agent_id=agent.did,
            action=body["action_type"],
            nonce=body["nonce"],
            signed_at=now(),
            parameters=body["payload"],
            target=body.get("target"),
        )

    def _reviewing(
        self, body: Mapping[str, Any], identifiers: Identifiers
    ) -> Mapping[str, Any]:
        """Submit the Proposal, retrying the same one until it is answered."""
        last: str = ""
        for _ in range(self.attempts):
            try:
                response = self._post(REVIEWS_PATH, body, credential=self._credential)
            except httpx.HTTPError as exc:
                # The same identifiers go again, so the platform answers with
                # the Receipt it already minted rather than minting a second.
                last = f"{type(exc).__name__}: {exc}"
                continue
            if response.status_code == 200:
                answer = self._json(response)
                if not isinstance(answer, Mapping):
                    raise ProposalUnreachable(
                        "the review could not be read",
                        proposed_action_id=identifiers.proposed_action_id,
                        nonce=identifiers.nonce,
                        proposed_at=identifiers.proposed_at,
                    )
                return answer
            code, detail, request_id = self._refusal(response)
            raise ProposalRefused(response.status_code, code, detail, request_id)
        raise ProposalUnreachable(
            f"the review could not be reached after {self.attempts} attempts; "
            f"last was {last}",
            proposed_action_id=identifiers.proposed_action_id,
            nonce=identifiers.nonce,
            proposed_at=identifiers.proposed_at,
            no_answer=True,
        )

    @staticmethod
    def _releases(review: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
        """Whether this review releases the action. ``passed`` and nothing else.

        Both the review's verdict and the Receipt's must be exactly ``passed``.
        They are the same fact written twice, so a disagreement is not a case to
        choose between: it is a reason to withhold.
        """
        return decision_from(receipt) == RELEASED and review.get("verdict") == PASSED

    @staticmethod
    def _enforced_at(receipt: Mapping[str, Any]) -> str:
        """Now, or the moment the review was decided, whichever is later.

        The platform requires ``proposed_at <= decided_at <= enforced_at``
        (PE-03). Enforcement follows the decision by construction, so a local
        clock reading earlier than ``decided_at`` is skew, and the decision's
        own time is the earliest defensible answer.
        """
        enforced = now()
        decided = receipt.get("decided_at")
        if not isinstance(decided, str):
            return enforced
        left, right = parse(enforced), parse(decided)
        if left is None or right is None or left >= right:
            return enforced
        return decided

    # ─── Transport ─────────────────────────────────────────────────────

    def _post(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        credential: str,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """One request. The credential is required and named by every caller;
        there is no default to fall back on, so the Gate credential and the
        developer key cannot be sent to each other's endpoints by an oversight
        in one branch, and an empty one is refused rather than replaced."""
        if not credential:
            raise GateConfigError(f"no credential was named for {path}")
        sending = {
            "Authorization": f"Bearer {credential}",
            "Content-Type": "application/json",
        }
        sending.update(headers or {})
        return self._client.post(f"{self._base_url}{path}", json=body, headers=sending)

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _refusal(response: httpx.Response) -> tuple[str, str, str]:
        """The refusal's code, message and request id, as far as they can be read."""
        body = Gate._json(response)
        if not isinstance(body, Mapping):
            return "", (response.text or response.reason_phrase), ""
        return (
            _text(body.get("code")) or "",
            _text(body.get("message")) or _text(body.get("detail")) or "",
            _text(body.get("request_id")) or "",
        )

    def close(self) -> None:
        """Close the underlying client."""
        self._client.close()

    # ``Self`` would say this better, and it arrived in 3.11 while this package
    # supports 3.10; typing_extensions would be a dependency for one annotation.
    def __enter__(self) -> Gate:  # noqa: PYI034
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def _identity(agent_id: str) -> AgentIdentity:
    """The agent's identity from its did:webvh DID, the only form taken.

    The Proposal's ``agent_id`` carries the DID (:data:`SEND_AGENT_DID`).

    Raises:
        GateConfigError: ``agent_id`` is not a did:webvh DID. Nothing is sent.
    """
    if not isinstance(agent_id, str):
        raise GateConfigError(f"agent_id is not the agent's DID: expected {AGENT_ID_FORM}")
    return parse_agent_id(agent_id, setting="agent_id")


def _text(value: Any) -> str | None:
    """A string, or ``None`` for anything that is not one."""
    return value if isinstance(value, str) else None


def _payload_fields(given: Sequence[str] | None) -> tuple[str, ...]:
    """The names ``payload_fields`` gives, or ``GateConfigError``.

    A bare string is refused rather than read as a list of its letters, and a
    name given twice is refused rather than quietly collapsed, because each is
    a sign the list is not the one the developer meant.
    """
    if given is None:
        raise GateConfigError(
            "control needs payload_fields: the argument names that become the "
            "Proposal's payload. Every other argument stays in this process."
        )
    if isinstance(given, str) or not isinstance(given, Sequence):
        raise GateConfigError("payload_fields must be a list of argument names")
    fields = tuple(given)
    if not fields:
        raise GateConfigError(
            "payload_fields is empty; name at least one argument your rules need"
        )
    if not all(isinstance(name, str) and name for name in fields):
        raise GateConfigError("payload_fields must be a list of argument names")
    if len(set(fields)) != len(fields):
        raise GateConfigError("payload_fields names an argument more than once")
    return fields


def _known_to(
    signature: inspect.Signature, fields: tuple[str, ...], handler: Callable[..., Any]
) -> None:
    """Every name is one of the function's own named arguments. ``*args`` and
    ``**kwargs`` are not names a payload can carry, whatever arrives in them."""
    named = {
        name
        for name, parameter in signature.parameters.items()
        if parameter.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }
    unknown = [name for name in fields if name not in named]
    if unknown:
        raise GateConfigError(
            f"payload_fields names {', '.join(unknown)}, which "
            f"{getattr(handler, '__name__', 'the function')} does not take"
        )


def _payload_from_call(
    signature: inspect.Signature,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
    fields: tuple[str, ...],
) -> dict[str, Any]:
    """The listed arguments of the decorated call, and only those, as the
    Proposal's payload. Every other argument stays here."""
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    return named_fields(bound.arguments, fields)
