# bindable-gate/src/bindable_gate/core/namespace.py
"""Catching an agent pointed at the wrong landscape, before it 401s.

An agent's `did:webvh` DID names the host that minted it, and the API base URL
names the landscape you are calling. When a non-public agent is used against
the public API, or the reverse, every call fails with a 401 that says
nothing about the real cause. The developer then goes looking for a bad key.

So the Gate checks at construction and says the actual problem, naming both
values it compared.

Deliberately conservative: it refuses only when it can identify BOTH sides
and they genuinely disagree. An unfamiliar host, a self-hosted deployment,
or a non-`did:web` identifier produces a warning at most, never a refusal —
a check that guessed would break working setups it does not understand,
which is worse than the 401 it was meant to prevent.

Retired hostnames are classified here on purpose rather than forgotten.
Identifiers minted on `sandbox.bindable-ai.com` and under the `bindbl.ai`
family remain valid forever, so an agent minted under a retired namespace
must pair with the current base URL of its own landscape without being
refused.
"""

from __future__ import annotations

import warnings
from urllib.parse import urlparse

PUBLIC = "public"
NON_PUBLIC = "non-public"

#: Every host this package can place, and the landscape it belongs to.
#:
#: An explicit mapping, not a pattern. These hostnames share no token a
#: substring test could key on — neither `test-api.bindable-ai.com` nor
#: `test.bindable-ai.com` contains "sandbox" — and inferring the landscape
#: from the name is what produced the two defects this replaces: an agent
#: from a retired namespace refused against the current Sandbox base URL,
#: and a non-public agent waved through to the public API. A host absent
#: from this mapping is unknown, and unknown is never a refusal.
_LANDSCAPES = {
    # Non-public. test-api.bindable-ai.com is the only Sandbox base URL.
    # test.bindable-ai.com is the mint host: it publishes no DNS and no
    # did.json by design, so a lookup failing there is correct, and an
    # identifier minted under it is still placed here.
    "test-api.bindable-ai.com": NON_PUBLIC,
    "test.bindable-ai.com": NON_PUBLIC,
    # Retired, and never a refusal — identifiers minted here stay valid.
    "sandbox.bindable-ai.com": NON_PUBLIC,
    "sandbox.bindbl.ai": NON_PUBLIC,
    # Public. bindbl.ai is a retired name for the same landscape as
    # bindable-ai.com, which is why pairing the two is not a mismatch.
    "api.bindable-ai.com": PUBLIC,
    "bindable-ai.com": PUBLIC,
    "bindbl.ai": PUBLIC,
}


def did_web_host(agent_id: str) -> str | None:
    """The host inside a ``did:webvh`` DID or a ``did:web`` name, or None if it
    is neither."""
    if agent_id.startswith("did:webvh:"):
        parts = agent_id.split(":")
        if len(parts) < 4:
            return None
        host = parts[3]
    elif agent_id.startswith("did:web:"):
        host = agent_id[len("did:web:"):].split(":", 1)[0]
    else:
        return None
    return host.replace("%3A", ":").lower() or None


def _landscape_of(host: str) -> str | None:
    """:data:`PUBLIC`, :data:`NON_PUBLIC`, or None when the host is unknown."""
    return _LANDSCAPES.get(host.lower())


def check_agent_namespace(agent_id: str, api_base_url: str) -> str | None:
    """Return a refusal message when the two genuinely disagree, else None.

    Emits a warning and returns None when the agent id is not a ``did:web``
    — there is nothing to compare, but a caller who meant to use a minted
    agent should hear about it.
    """
    host = did_web_host(agent_id)
    if host is None:
        warnings.warn(
            f"agent id {agent_id!r} is not a did:web identifier, so the Gate "
            "cannot check it against the API base URL — if this is a minted "
            "agent, expect 401s when the two do not match",
            stacklevel=3,
        )
        return None

    api_host = (urlparse(api_base_url).hostname or "").lower()
    if not api_host:
        return None

    agent_landscape, api_landscape = _landscape_of(host), _landscape_of(api_host)
    if agent_landscape and api_landscape and agent_landscape != api_landscape:
        return (
            f"the agent and the API are on different landscapes. "
            f"BINDABLE_AGENT_ID={agent_id} is a {agent_landscape} agent, "
            f"but BINDABLE_API_BASE_URL={api_base_url} is {api_landscape}. "
            f"Every call would come back unauthorized."
        )
    return None


__all__ = ["NON_PUBLIC", "PUBLIC", "check_agent_namespace", "did_web_host"]
