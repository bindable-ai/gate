# bindable-gate/src/bindable_gate/mcp/trackpoint.py
"""Recording Outcomes from the MCP form, without ever delaying an action.

The delivery itself — the wire contract, the timeouts, the 409
classification, the retry budget — lives in :mod:`bindable_gate.core.client`
and is shared with the in-process Gate. This module adds only what the MCP
form needs on top: a bounded queue and a worker, so reporting happens
strictly after the fact and never on the path of a tool call.

That difference is deliberate, and it is the one place the two forms part
company. The in-process Gate reports synchronously and raises when the
ledger is missing an Outcome, because its caller is still there to be told.
The MCP proxy has no such caller: by the time a report is attempted the tool
call has already been forwarded or blocked and the relay has moved on, so a
failure is reported to stderr as loudly as a stderr line can be. Enforcement
integrity never depends on a report succeeding.

The core client is synchronous by design, so the worker calls it in a
thread. That keeps one delivery implementation for both forms and leaves an
async client addable later without touching either Gate.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from bindable_gate.contract.kinds import MCP_PROXY, report_gate_kind
from bindable_gate.core.errors import TrackpointNotRecorded
from bindable_gate.library import Gate as LibraryGate

#: What the local MCP Gate reports. It is an ``mcp_proxy`` Gate, never
#: ``mcp_gateway``, which is the Bindable-operated hosted Gate's (through the
#: one switch in bindable_gate.contract.kinds), and the proxy is named in
#: gate_version.
GATE_KIND = report_gate_kind(MCP_PROXY)

#: How gate_version names the local MCP Gate: the proxy, then the package version.
GATE_VERSION_PREFIX = "mcp-proxy/"


def gate_version(package_version: str) -> str:
    """The local MCP Gate's gate_version, ``mcp-proxy/<package version>``."""
    return f"{GATE_VERSION_PREFIX}{package_version}"

MAX_QUEUE = 256  # bounded; drop rather than grow without limit


def _note(message: str) -> None:
    """Silent except for stderr: stdout is the MCP wire and must stay clean."""
    print(f"bindable-gate: {message}", file=sys.stderr, flush=True)


@dataclass(frozen=True)
class EnforcementReport:
    """One enforcement outcome, in the fields the platform's report takes.

    Field for field ``POST /v1/receipts/{receipt_id}/report``: the Receipt names
    itself in the path, the outcome is ``released`` or ``blocked``, and the three
    times and the two identifying fields are all required. What the earlier shape
    carried and this does not — a ``decision``, a ``gate_runner``, a Gate-minted
    ``trackpoint_id``, an ``action_result`` object — the platform has no field for.
    """

    receipt_id: str
    outcome: str
    proposed_at: str
    enforced_at: str
    gate_version: str
    gate_kind: str = GATE_KIND


class TrackpointReporter:
    """Best-effort, after-the-fact recording for the MCP form."""

    def __init__(
        self,
        gate: LibraryGate,
        *,
        max_queue: int = MAX_QUEUE,
    ) -> None:
        self._gate = gate
        self._queue: asyncio.Queue[EnforcementReport] = asyncio.Queue(maxsize=max_queue)
        self._worker: asyncio.Task[None] | None = None

    # ─── Enforcement-path surface (must never block or raise) ──────────

    def submit(self, report: EnforcementReport) -> None:
        """Queue a report. Returns immediately; never raises, never does I/O.

        On a full queue the report is dropped with a note: a missing
        Trackpoint is preferable to a delayed action, and the enforcement
        decision was made long before this was called.
        """
        try:
            self._queue.put_nowait(report)
        except asyncio.QueueFull:
            _note(
                f"trackpoint queue full — dropped the report for receipt "
                f"{report.receipt_id} (enforcement was unaffected)"
            )
        except Exception as exc:  # noqa: BLE001 - reporting may never propagate
            _note(f"trackpoint report dropped ({type(exc).__name__})")

    # ─── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.get_running_loop().create_task(self._drain())

    async def aclose(self, *, drain_timeout: float = 5.0) -> None:
        """Stop the worker, giving queued reports a chance to flush first.

        Reached from the Gate's teardown, which a handled SIGTERM makes sure
        actually runs — otherwise the last Outcome of the session would be
        lost exactly when it matters most.
        """
        if self._worker is not None:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=drain_timeout)
            except (TimeoutError, Exception):  # noqa: BLE001
                pass
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._worker = None
        await asyncio.to_thread(self._gate.close)

    # ─── Worker ────────────────────────────────────────────────────────

    async def _drain(self) -> None:
        while True:
            report = await self._queue.get()
            try:
                await asyncio.to_thread(
                    self._gate.report,
                    {"receipt_id": report.receipt_id},
                    outcome=report.outcome,
                    proposed_at=report.proposed_at,
                    enforced_at=report.enforced_at,
                    gate_kind=report.gate_kind,
                    gate_version=report.gate_version,
                )
            except asyncio.CancelledError:  # pragma: no cover - shutdown
                raise
            except TrackpointNotRecorded as exc:
                _note(f"{exc} — enforcement was unaffected")
            except Exception as exc:  # noqa: BLE001 - never escapes the worker
                _note(f"trackpoint report failed ({type(exc).__name__})")
            finally:
                self._queue.task_done()


__all__ = [
    "GATE_KIND",
    "EnforcementReport",
    "TrackpointNotRecorded",
    "TrackpointReporter",
]
