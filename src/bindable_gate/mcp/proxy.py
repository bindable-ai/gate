# bindable-gate/src/bindable_gate/mcp/proxy.py
"""ActionGateProxy — the Bindable Action Gate for MCP.

The Gate sits between an MCP client (our stdio) and one downstream MCP tool
server (a spawned subprocess). It evaluates nothing: no rules, no Verdicts,
no cryptography live here — the hosted Action Review decides, and this
enforces what it decided. MCP stdio traffic is newline-delimited JSON-RPC.
Non-gated traffic (initialize, tools/list, notifications, responses) is
relayed byte-for-byte untouched, so the Gate contributes no tools of its
own. Every tools/call request is HELD: it is mapped to a Proposal and
submitted to POST /v1/review through the `bindable` PyPI client. Only a
released Decision forwards the original request line to the downstream
server; a blocked Decision returns a JSON-RPC error to the client carrying
reason_code, receipt.receipt_id and receipt.failed_rule. The downstream
server never sees a blocked call.

STANDALONE. With no BINDABLE_GATE_CREDENTIAL nothing is reviewed: every
tools/call is forwarded, and when its answer comes back it is appended to the
local Track (bindable_gate.contract.local_track). The Gate says so once on
stderr at startup.

Guardrails:
- In-path: no tools/call bypasses the Gate.
- Thin transport: all Action Review traffic goes through the Library Gate to
  the hosted API. This module contains no canonicalization, hashing, or
  signature code; the agent signature and the local Track are
  bindable_gate.contract's.
- Fail-closed: the Action Review refusing, non-2xx, or an unparseable /
  indeterminate Verdict => the tool call is NOT forwarded. Where Bindable is
  unreachable (no answer, a timeout or a 5xx), the agent's offline setting
  decides (bindable_gate.contract.offline): not forwarded, or forwarded
  unreviewed and kept as an offline record once the tool answers, with no
  Receipt. The API key is never logged and never appears in error text.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bindable_gate.contract.agent import AgentIdentity, parse_agent_id
from bindable_gate.contract.local_track import NO_RESULT, LocalTrack
from bindable_gate.contract.offline import OfflineDecision, OfflineMode, to_stderr
from bindable_gate.contract.signing import AgentKey, signing_notice
from bindable_gate.core.derivation import BLOCKED
from bindable_gate.core.errors import GateConfigError
from bindable_gate.library import Gate as LibraryGate
from bindable_gate.library import ProposalRefused, ProposalUnreachable, Review
from bindable_gate.library._clock import now as platform_now
from bindable_gate.library._version import library_version
from bindable_gate.mcp.config import GateConfig
from bindable_gate.mcp.proposal import ProposalError, build_proposal
from bindable_gate.mcp.trackpoint import (
    GATE_KIND,
    EnforcementReport,
    TrackpointReporter,
    gate_version,
)

#: Every variable the Gate reads starts with this, and none reaches the
#: downstream tool server.
GATE_ENV_PREFIX = "BINDABLE_"

# JSON-RPC error codes surfaced to the MCP client.
BLOCKED_ERROR_CODE = -32001
FAIL_CLOSED_ERROR_CODE = -32002
#: The review released the call and the downstream never received it.
#: Distinct from a block, which is the review refusing, and from
#: fail-closed, which is the review not answering: here the review passed and
#: the tool is the thing that could not be reached.
DELIVERY_FAILED_ERROR_CODE = -32003

_BLOCKED_MESSAGE = "Blocked by Bindable Action Gate"
_DELIVERY_FAILED_MESSAGE = (
    "Released by the Action Review, but the tool could not be reached — the call did not run"
)
_FAIL_CLOSED_MESSAGE = (
    "Bindable Action Gate decision unavailable — tool call not forwarded (fail-closed)"
)


def _utc_now() -> str:
    """The moments this process owns, in the one shape the platform takes.

    An aware millisecond timestamp ending in Z. ``isoformat()`` gives
    microseconds and ``+00:00``, which the platform's boundary refuses on length.
    """
    return platform_now()


@dataclass
class EnforcementRecord:
    """What one enforced Decision looked like, for reporting AFTER the fact.

    Purely observational: nothing here participates in the hold/decide/
    forward path, and an incomplete record can only mean an unreported
    Outcome, never an unenforced one.

    ``decided_at`` is the Receipt's own ``verified_at`` — read from the
    Decision, never generated here. ``proposed_at`` and ``enforced_at`` are
    this process's own wall-clock moments: when the call was held, and when
    it was actually forwarded or the block returned.
    """

    receipt_id: str | None
    decision: str
    proposed_at: str
    decided_at: str | None
    enforced_at: str | None = None


class ActionGateProxy:
    """The Gate: hold tools/call, submit it to the hosted Action Review, then
    forward (Released) or reject with the Receipt reference (Blocked)."""

    def __init__(
        self,
        config: GateConfig,
        *,
        persistent_agent_id: str | None = None,
        gate: LibraryGate | None = None,
        reporter: TrackpointReporter | None = None,
        local_track_path: str | Path | None = None,
        offline: OfflineMode | None = None,
    ) -> None:
        """Bind the proxy to a GateConfig (gate endpoint, credentials,
        downstream server spec).

        ``persistent_agent_id`` names the registered bound agent this proxy
        proposes as, by its did:webvh DID; it falls back to the config's
        agent, then to the BINDABLE_AGENT_ID environment variable. Nothing
        but a did:webvh DID is taken. ``gate`` is injectable for tests; by default the Library
        Gate's client is built from the config, which is what reaches the
        platform's own two endpoints.
        """
        self._config = config
        raw_agent = persistent_agent_id or os.environ.get("BINDABLE_AGENT_ID")
        if persistent_agent_id:
            agent: AgentIdentity | None = parse_agent_id(persistent_agent_id, setting="persistent_agent_id")
        elif config.agent is not None:
            agent = config.agent
        else:
            agent = parse_agent_id(raw_agent) if raw_agent and raw_agent.strip() else None
        # Standalone: the calls being recorded locally, keyed by request id.
        self._recording: dict[Any, tuple[str, dict[str, Any], str, float]] = {}
        # Offline: the calls forwarded unreviewed, keyed by request id, until
        # the tool answers and each becomes an offline record.
        self._unreviewed: dict[Any, tuple[str, dict[str, Any], str, OfflineDecision]] = {}
        self._offline: OfflineMode | None = None
        self._track: LocalTrack | None = None
        if config.standalone:
            shown = agent.shown if agent else None
            self._track = LocalTrack(local_track_path, agent_id=shown, key=config.agent_key)
            self._track.open()
            self._persistent_agent_id = shown or ""
            self._gate: LibraryGate | None = None
            self._pending_record: EnforcementRecord | None = None
            self._reporter: TrackpointReporter | None = None
            return
        if agent is None:
            raise GateConfigError(
                "missing required environment variable(s): BINDABLE_AGENT_ID"
            )
        self._persistent_agent_id = agent.did
        self._gate = gate or LibraryGate(
            base_url=config.api_base_url,
            credential=config.gate_credential,
            review_context=config.review_context,
            agent_key=config.agent_key,
        )
        self._offline = offline or OfflineMode(
            agent_id=agent.did,
            base_url=config.api_base_url,
            credential=config.gate_credential,
            gate_version=gate_version(library_version()),
            key=config.agent_key,
            transport=getattr(self._gate, "_transport", None),
        )
        # Set by handle_tool_call, stamped and cleared by _complete_enforcement
        # once the call has actually been forwarded or the block returned. The
        # relay reads one client line at a time, so this never races.
        self._pending_record = None
        # Reporting only. None when switched off; nothing else changes.
        if reporter is not None:
            self._reporter = reporter
        elif config.trackpoints_enabled:
            self._reporter = TrackpointReporter(self._gate)
        else:
            self._reporter = None

    # ─── Gate decision ─────────────────────────────────────────────────

    async def handle_tool_call(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Hold one tools/call request. Returns None when the Decision is
        Released (caller forwards the ORIGINAL request untouched), or a
        JSON-RPC error response dict that must be sent to the client
        instead (Blocked, or fail-closed on any indeterminate outcome)."""
        request_id = request.get("id")
        # The call is held from here: this is the moment custody begins.
        proposed_at = _utc_now()
        self._pending_record = None
        if self._track is not None:
            self._remember(request, proposed_at)
            return None
        if self._gate is None:
            return _fail_closed_response(request_id, "no Library Gate is configured")
        try:
            proposal = build_proposal(request.get("params") or {})
        except ProposalError as exc:
            return _fail_closed_response(request_id, f"unmappable tool call: {exc}")
        if self._offline is not None:
            decision = await asyncio.to_thread(self._offline.before)
            if decision is not None:
                return self._unreviewed_call(request, proposal, decision, proposed_at)
        try:
            review = await asyncio.to_thread(
                self._gate.review,
                self._persistent_agent_id,
                proposal["action_type"],
                proposal["payload"],
                None,
                proposed_action_id=proposal["proposed_action_id"],
                nonce=proposal["nonce"],
                proposed_at=proposed_at,
            )
        except ProposalRefused as exc:
            decision = self._offline.failed(exc) if self._offline is not None else None
            if decision is not None:
                return self._unreviewed_call(request, proposal, decision, proposed_at)
            return _fail_closed_response(
                request_id, f"review refused the Proposal: {exc.status} {exc.code}"
            )
        except ProposalUnreachable as exc:
            # No Receipt was obtained, by any route: unreachable, timed out,
            # unreadable, or passed with nothing to tie a release to. One class
            # covers them all; where Bindable is unreachable the offline
            # setting decides, and every other one blocks.
            decision = self._offline.failed(exc) if self._offline is not None else None
            if decision is not None:
                return self._unreviewed_call(request, proposal, decision, proposed_at)
            return _fail_closed_response(request_id, str(exc))
        if self._offline is not None:
            await asyncio.to_thread(self._offline.reviewed)

        # The one rule, single-sourced: released iff the verdict is exactly
        # "passed" on both the answer and the Receipt. The Receipt is persisted
        # before release, so its presence is the release precondition; verifying
        # its signature is the engine's job and never the Gate's.
        if review.released:
            self._pending_record = _record_for(review, "released", proposed_at)
            return None

        # Every non-released review blocks.
        self._pending_record = _record_for(review, "blocked", proposed_at)
        return _blocked_response(request_id, review)

    # ─── Offline ───────────────────────────────────────────────────────

    def _unreviewed_call(
        self,
        request: Mapping[str, Any],
        proposal: Mapping[str, Any],
        decision: OfflineDecision,
        started_at: str,
    ) -> dict[str, Any] | None:
        """Offline mode's answer: forward the call unreviewed (``None``), or
        the JSON-RPC error that withholds it. Nothing here is reported."""
        if decision.message:
            to_stderr(decision.message)
        if not decision.run:
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": FAIL_CLOSED_ERROR_CODE, "message": decision.message},
            }
        # The action starts now, after the outage began: not when it was held.
        self._unreviewed[_key(request.get("id"))] = (
            str(proposal["action_type"]), dict(proposal["payload"]), _utc_now(), decision,
        )
        return None

    def _record_unreviewed(self, line: str) -> None:
        """An offline record for a call forwarded unreviewed, once its answer
        is back. Never raises: the record is not a decision."""
        if not self._unreviewed or self._offline is None:
            return
        try:
            message = json.loads(line)
            if not isinstance(message, dict) or "id" not in message or "method" in message:
                return
            call = self._unreviewed.pop(_key(message.get("id")), None)
            if call is None:
                return
            tool, payload, started_at, decision = call
            self._offline.record(
                decision,
                tool=tool,
                params=payload,
                started_at=started_at,
                result=message.get("result", NO_RESULT),
                failed="error" in message,
            )
        except Exception as exc:  # noqa: BLE001 - a record never stops the relay
            print(f"bindable-gate: the offline record was not written ({type(exc).__name__})",
                  file=sys.stderr, flush=True)

    # ─── Standalone ────────────────────────────────────────────────────

    def _remember(self, request: Mapping[str, Any], started_at: str) -> None:
        """Keep a forwarded call until its answer comes back."""
        params = request.get("params") or {}
        name = params.get("name") if isinstance(params, Mapping) else None
        arguments = params.get("arguments") if isinstance(params, Mapping) else None
        self._recording[_key(request.get("id"))] = (
            name if isinstance(name, str) else "",
            dict(arguments) if isinstance(arguments, Mapping) else {},
            started_at,
            time.monotonic(),
        )

    def _record_answer(self, line: str) -> None:
        """Append a recorded call to the local Track once its answer is back.
        Never raises: the local Track is a record, not a decision."""
        if self._track is None or not self._recording:
            return
        try:
            message = json.loads(line)
            if not isinstance(message, dict) or "id" not in message or "method" in message:
                return
            call = self._recording.pop(_key(message.get("id")), None)
            if call is None:
                return
            tool, arguments, started_at, began = call
            error = message.get("error")
            self._track.append(
                tool=tool,
                params=arguments,
                started_at=started_at,
                result=message.get("result", NO_RESULT),
                error=None if error is None else json.dumps(error, separators=(",", ":")),
                duration_ms=round((time.monotonic() - began) * 1000, 3),
            )
        except Exception as exc:  # noqa: BLE001 - a record never stops the relay
            print(f"bindable-gate: the local Track was not written ({type(exc).__name__})",
                  file=sys.stderr, flush=True)

    def _delivery_failed(self, line: str, exc: BaseException) -> str:
        """Turn a released call that never reached the tool into a blocked one.

        The Receipt stands: the review happened and passed. What did not happen is
        the action, so the enforcement outcome is ``blocked`` — reporting
        ``released`` would put a Trackpoint on the Track saying a tool ran when it
        did not, which is the one thing the Track must never say.

        The reason travels to the client and to the operational log. The platform's
        report carries ``outcome`` and no reason field, so it is not on the wire, and
        that gap is recorded rather than worked around.
        """
        reason = f"{type(exc).__name__}: {exc}"
        if self._pending_record is not None:
            self._pending_record.decision = BLOCKED
        print(
            f"bindable-gate: the tool could not be reached after a passed review "
            f"({reason}); reported blocked, and the call did not run",
            file=sys.stderr,
            flush=True,
        )
        request_id: Any = None
        receipt_id: str | None = None
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            request_id = json.loads(line).get("id")
        if self._pending_record is not None:
            receipt_id = self._pending_record.receipt_id
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": DELIVERY_FAILED_ERROR_CODE,
                    "message": f"{_DELIVERY_FAILED_MESSAGE}: {reason}",
                    "data": {"receipt_id": receipt_id, "reason": reason},
                },
            },
            separators=(",", ":"),
        )

    def _complete_enforcement(self) -> EnforcementRecord | None:
        """Stamp and take the pending record once enforcement has HAPPENED.

        Called after the forward (or the block response) is on the wire, so
        ``enforced_at`` is the real moment. Returns None when the last line
        was not a held Decision — pass-through traffic is never reported.
        """
        record = self._pending_record
        self._pending_record = None
        if record is None:
            return None
        record.enforced_at = _utc_now()
        return record

    def _report_enforcement(self) -> None:
        """Hand the completed record to the reporter. Never raises, never awaits.

        Called only after the call has been forwarded or the block returned,
        so nothing here can affect whether the action ran. Pass-through
        traffic produces no record and is never reported. A Decision with no
        Receipt (a pre-gate denial) has no Trackpoint anchor, so it is noted
        and skipped rather than sent anchorless.
        """
        record = self._complete_enforcement()
        if record is None or self._reporter is None:
            return
        try:
            if record.receipt_id is None:
                print(
                    "bindable-gate: no receipt on the Decision — nothing to "
                    "anchor a trackpoint to (enforcement was unaffected)",
                    file=sys.stderr,
                    flush=True,
                )
                return
            self._reporter.submit(
                EnforcementReport(
                    receipt_id=record.receipt_id,
                    outcome=record.decision,
                    proposed_at=record.proposed_at,
                    enforced_at=record.enforced_at or _utc_now(),
                    gate_version=gate_version(library_version()),
                    gate_kind=GATE_KIND,
                )
            )
        except Exception as exc:  # noqa: BLE001 - reporting may never propagate
            print(
                f"bindable-gate: trackpoint report dropped "
                f"({type(exc).__name__}) — enforcement was unaffected",
                file=sys.stderr,
                flush=True,
            )

    # ─── Relay ─────────────────────────────────────────────────────────

    async def _dispatch_client_line(self, line: str) -> tuple[str, str]:
        """Route one raw line from the MCP client.

        Returns ("downstream", line) to relay the line untouched,
        ("client", response_line) to answer the client without forwarding,
        or ("drop", "") for a gated call that permits no error response
        (a tools/call notification — fail-closed, never forwarded).
        """
        try:
            message = json.loads(line)
        except ValueError:
            # Not JSON-RPC — cannot be a tools/call; relay untouched.
            return ("downstream", line)
        if not (isinstance(message, dict) and message.get("method") == "tools/call"):
            return ("downstream", line)
        if "id" not in message:
            # A tools/call notification cannot receive an error response;
            # fail-closed means it must not run.
            print(
                "bindable-gate: dropped tools/call notification (fail-closed)",
                file=sys.stderr,
                flush=True,
            )
            return ("drop", "")
        response = await self.handle_tool_call(message)
        if response is None:
            return ("downstream", line)
        return ("client", json.dumps(response, separators=(",", ":")))

    async def serve(self) -> None:
        """Run the Action Gate: spawn the downstream server, relay stdio traffic,
        holding every tools/call for the hosted Action Review."""
        downstream = await asyncio.create_subprocess_exec(
            self._config.downstream_command,
            *self._config.downstream_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            env=downstream_environment(os.environ),
        )
        # The pipes are bound to names here rather than read off the process at
        # each use, for two reasons. An `assert` is removed under `python -O`, so
        # it is not a guarantee, and this must fail closed: without both pipes the
        # Gate cannot hold a tools/call, submit it, or forward it, so relaying
        # anything would move traffic past a review that never happened. And a
        # name cannot be None later, where an attribute read again might be.
        to_downstream = downstream.stdin
        from_downstream = downstream.stdout
        if to_downstream is None or from_downstream is None:
            missing = ", ".join(
                name
                for name, pipe in (("stdin", to_downstream), ("stdout", from_downstream))
                if pipe is None
            )
            raise GateConfigError(
                f"the downstream server started without its {missing} pipe, which the "
                "Gate relays through; nothing can be held for review, so the Gate stops"
            )
        if self._reporter is not None:
            self._reporter.start()
        loop = asyncio.get_running_loop()
        client_reader = asyncio.StreamReader()
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(client_reader), sys.stdin
        )

        # An MCP host that quits — or any supervisor — SIGTERMs this process.
        # Python's default handling would end it outright, skipping the
        # teardown below and silently losing any Outcome still queued for
        # reporting. Feeding EOF instead unwinds the relay the same way a
        # closed stdin does, so the drain in `finally` actually runs.
        def _begin_shutdown() -> None:
            if not client_reader.at_eof():
                client_reader.feed_eof()

        for signal_number in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.add_signal_handler(signal_number, _begin_shutdown)
        stdout_lock = asyncio.Lock()
        if self._track is not None:
            print(standalone_notice(self._track, self._config.agent_key), file=sys.stderr, flush=True)
        else:
            print(connected_notice(self._config.review_context, self._persistent_agent_id,
                                   self._config.agent_key), file=sys.stderr, flush=True)

        async def to_client(payload: str) -> None:
            async with stdout_lock:
                sys.stdout.write(payload + "\n")
                sys.stdout.flush()

        async def relay_client_to_downstream() -> None:
            while True:
                raw = await client_reader.readline()
                if not raw:
                    break
                line = raw.decode("utf-8").rstrip("\r\n")
                if not line:
                    continue
                destination, payload = await self._dispatch_client_line(line)
                if destination == "downstream":
                    try:
                        to_downstream.write(payload.encode("utf-8") + b"\n")
                        await to_downstream.drain()
                    except (OSError, BrokenPipeError, ConnectionError) as exc:
                        # The review released the call and the tool never
                        # got it, so nothing ran: the enforcement outcome is blocked,
                        # and the client is told which of the three things happened.
                        # The reason reaches the client and the operational log; the
                        # platform's report has no field for it.
                        await to_client(self._delivery_failed(payload, exc))
                elif destination == "client":
                    await to_client(payload)
                # Enforcement is COMPLETE at this point — the call has been
                # forwarded or the block returned. Everything after this line
                # is observation and cannot affect what just happened.
                self._report_enforcement()
            to_downstream.close()

        async def relay_downstream_to_client() -> None:
            while True:
                raw = await from_downstream.readline()
                if not raw:
                    break
                answer = raw.decode("utf-8").rstrip("\r\n")
                await to_client(answer)
                self._record_answer(answer)
                self._record_unreviewed(answer)

        try:
            await asyncio.gather(
                relay_client_to_downstream(), relay_downstream_to_client()
            )
        finally:
            if downstream.returncode is None:
                downstream.terminate()
            await downstream.wait()
            if self._reporter is not None:
                await self._reporter.aclose()


def standalone_notice(track: LocalTrack, key: AgentKey | None = None) -> str:
    """The one line a standalone Gate prints at startup."""
    return (
        "bindable-gate: this agent is recording locally to its local Track "
        f"({track.path}) and is not connected to Bindable. Tool calls are allowed "
        "and not reviewed. Set BINDABLE_GATE_CREDENTIAL to connect. "
        f"Signing: {signing_notice(key, connected=False)}."
    )


def connected_notice(review_context: str, did: str, key: AgentKey | None) -> str:
    """The one line a connected Gate prints at startup."""
    return (
        "bindable-gate: connected; every tool call is reviewed by Bindable in the "
        f"{review_context} context for agent {did}; {signing_notice(key, connected=True)}."
    )


def _key(request_id: Any) -> str:
    """A JSON-RPC id as a dictionary key: 1 and "1" are different ids."""
    return json.dumps(request_id)


# ─── Response builders ─────────────────────────────────────────────────


def _receipt_of(review: Review) -> dict[str, Any]:
    """The Decision's Receipt as a mapping ({} when absent or malformed).

    A pre-gate denial (for example NO_CONTRACT_REGISTERED) carries no
    Receipt at all; callers must tolerate the empty case.
    """
    return dict(review.receipt)


def _record_for(
    review: Review, decision: str, proposed_at: str
) -> EnforcementRecord:
    """Keep the Receipt anchor and the Receipt's own decided-at."""
    receipt = _receipt_of(review)
    return EnforcementRecord(
        receipt_id=receipt.get("receipt_id"),
        decision=decision,
        proposed_at=proposed_at,
        decided_at=receipt.get("verified_at"),
    )


def downstream_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """The environment the downstream tool server starts with: the Gate's own,
    without any ``BINDABLE_*`` variable. The Gate credential is the Gate's
    alone. A tool server that can run commands or read its environment would
    otherwise hand it to the agent the Gate is reviewing."""
    return {
        name: value
        for name, value in environment.items()
        if not name.upper().startswith(GATE_ENV_PREFIX)
    }


def _blocked_response(request_id: Any, review: Review) -> dict[str, Any]:
    receipt = _receipt_of(review)
    reason_code = review.reason_code
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": BLOCKED_ERROR_CODE,
            "message": _BLOCKED_MESSAGE,
            "data": {
                "reason_code": reason_code,
                "receipt_id": receipt.get("receipt_id"),
                "failed_rule": review.failed_rule,
            },
        },
    }


def _fail_closed_response(request_id: Any, detail: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": FAIL_CLOSED_ERROR_CODE,
            "message": f"{_FAIL_CLOSED_MESSAGE}: {detail}",
        },
    }


# ─── Entry point ───────────────────────────────────────────────────────


def main() -> None:
    """Run the Action Gate from environment configuration."""
    try:
        config = GateConfig.from_env()
        proxy = ActionGateProxy(config)
        # serve() raises this too, for a downstream started without the pipes the
        # Gate relays through. It is the same class of fault and it gets the same
        # named exit, rather than a traceback for the operator to read.
        asyncio.run(proxy.serve())
    except GateConfigError as exc:
        print(f"bindable-gate: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
