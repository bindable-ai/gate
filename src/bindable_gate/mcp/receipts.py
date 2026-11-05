# bindable-gate/src/bindable_gate/mcp/receipts.py
"""Receipt verification via the hosted verify endpoint.

Verifies a Receipt by POSTing it FLAT to POST /v1/verify on the hosted
Bindable API and returning the response. There is NO local verification
in this layer: offline verification is the Python thin client's job, not
this repo's.

Guardrails:
- Thin transport: this module must never contain canonicalization, hashing,
  or signature code.
- Fail-closed: an unverifiable or indeterminate Receipt is treated as not
  verified (VerifyUnavailableError — the caller must not treat it as valid).
- The API key is never logged and never appears in error text.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from bindable_gate.mcp.config import GateConfig


class VerifyUnavailableError(RuntimeError):
    """The hosted verify endpoint could not produce a verdict.

    Fail-closed: callers must treat the Receipt as NOT verified.
    """


def verify_via_hosted(
    receipt: Mapping[str, Any], config: GateConfig, *, timeout: float = 30.0
) -> dict[str, Any]:
    """Verify a Receipt via the hosted verify endpoint. No local verification.

    POSTs the receipt FLAT (the receipt mapping itself is the request body)
    to ``{api_base_url}/v1/verify`` with bearer-token auth and returns the
    parsed response verbatim.

    Raises:
        VerifyUnavailableError: On a non-2xx response, transport failure,
            or unparseable body — fail-closed, the Receipt is NOT verified.
            The error text never carries the API key.
    """
    try:
        response = httpx.post(
            f"{config.api_base_url}/v1/verify",
            json=dict(receipt),
            headers={
                "Authorization": f"Bearer {config.gate_credential}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise VerifyUnavailableError(
            f"verify transport failure: {type(exc).__name__}"
        ) from exc
    if not response.is_success:
        raise VerifyUnavailableError(
            f"verify returned HTTP {response.status_code}"
        )
    try:
        body: dict[str, Any] = response.json()
    except ValueError as exc:
        raise VerifyUnavailableError("verify returned an unparseable body") from exc
    return body
