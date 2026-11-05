# bindable-gate/src/bindable_gate/contract/agent.py
"""Who the agent is: its DID, ``did:webvh:<SCID>:<host>:agent:<Agent ID>``.

The did:webvh DID is the only agent identifier a Gate takes from a person or
a configuration, the only one it shows, and the one the agent's signature
covers as ``persistent_agent_id``. A bare Agent ID or a did:web name is
refused. A Proposal's and an Evidence record's ``agent_id`` is the DID too
(see :data:`SEND_AGENT_DID`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from bindable_gate.core.errors import GateConfigError

_UUID: Final[str] = (
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
)

#: did:webvh:<SCID>:<host>:agent:<Agent ID>. The SCID is base58btc.
DID_WEBVH: Final[re.Pattern[str]] = re.compile(
    r"^did:webvh:([1-9A-HJ-NP-Za-km-z]+):([^:#?/\s]+(?::[^:#?/\s]+)*):agent:(" + _UUID + r")$"
)

AGENT_ID_FORM: Final[str] = "did:webvh:<SCID>:<host>:agent:<Agent ID>"

#: Whether a Proposal's ``agent_id`` on the wire is the agent's did:webvh DID.
#: One switch: since 2026-11-01 the platform refuses the bare Agent ID there
#: and takes only the DID. Off, the Gate would send the Agent ID inside the DID.
SEND_AGENT_DID: Final[bool] = True

_BARE_AGENT_ID: Final[re.Pattern[str]] = re.compile(r"^" + _UUID + r"$")


@dataclass(frozen=True)
class AgentIdentity:
    """The agent's did:webvh DID, and the Agent ID inside it."""

    #: The agent's DID, as given: what a Gate is configured with, shows and signs.
    did: str
    #: The Agent ID inside the DID. Only a Proposal sent with
    #: :data:`SEND_AGENT_DID` off carries it.
    agent_id: str

    @property
    def shown(self) -> str:
        """What a Gate shows for the agent: its did:webvh DID."""
        return self.did


def parse_agent_id(raw: str, *, setting: str = "BINDABLE_AGENT_ID") -> AgentIdentity:
    """The identity in ``raw``, a did:webvh DID, or :class:`GateConfigError`
    naming ``setting``. A bare Agent ID or a did:web name is refused."""
    text = raw.strip()
    match = DID_WEBVH.match(text)
    if match is None and _BARE_AGENT_ID.match(text):
        raise GateConfigError(
            f"{setting} is a bare Agent ID, which Bindable no longer takes: "
            f"give the agent's DID, {AGENT_ID_FORM}"
        )
    if match is None:
        raise GateConfigError(f"{setting} is not the agent's DID: expected {AGENT_ID_FORM}")
    return AgentIdentity(did=text, agent_id=match.group(3).lower())


def wire_agent_id(agent: AgentIdentity, *, send_did: bool = SEND_AGENT_DID) -> str:
    """What a Proposal's ``agent_id`` carries: the DID, or the Agent ID inside
    it with the switch off."""
    return agent.did if send_did else agent.agent_id


__all__ = ["AGENT_ID_FORM", "DID_WEBVH", "SEND_AGENT_DID", "AgentIdentity", "parse_agent_id", "wire_agent_id"]
