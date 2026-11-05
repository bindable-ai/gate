# bindable-gate/src/bindable_gate/contract/__init__.py
"""The Gate contract every attachment form shares, in Python and TypeScript.

What is here is written once and matches ``@bindable-ai/gate`` byte for byte:

- ``kinds``: the ``gate_kind`` each form is, and the one switch that decides
  what a report carries while the platform takes only the older values.
- ``canonical``: RFC 8785 canonical JSON, from the maintained ``rfc8785``
  library, and SHA-256 over it.
- ``agent``: the agent's identity, its did:webvh DID, and nothing else.
- ``signing``: the agent key and the signature a Proposal carries.
- ``local_track``: the hash-chained record a Gate keeps when it has no Gate
  credential.

None of it decides anything. Action Review decides; a Gate enforces.
"""

from bindable_gate.contract.agent import (
    SEND_AGENT_DID,
    AgentIdentity,
    parse_agent_id,
    wire_agent_id,
)
from bindable_gate.contract.canonical import (
    canonical_json,
    canonical_sha256,
    sha256_hex,
)
from bindable_gate.contract.kinds import (
    FRAMEWORK_ADAPTER,
    LIBRARY,
    MCP_PROXY,
    PLATFORM_ACCEPTS_GATE_KINDS,
    RUNTIME_PLUGIN,
    report_gate_kind,
)
from bindable_gate.contract.local_track import (
    GENESIS_HASH,
    LocalTrack,
    TrackCheck,
    default_local_track_path,
    verify_local_track,
)
from bindable_gate.contract.signing import (
    AgentKey,
    check_key_id,
    load_agent_key,
    proposal_signing_bytes,
    sign_proposal,
    signing_notice,
    verify_signature,
)

__all__ = [
    "FRAMEWORK_ADAPTER",
    "GENESIS_HASH",
    "LIBRARY",
    "MCP_PROXY",
    "PLATFORM_ACCEPTS_GATE_KINDS",
    "RUNTIME_PLUGIN",
    "SEND_AGENT_DID",
    "AgentIdentity",
    "AgentKey",
    "LocalTrack",
    "TrackCheck",
    "canonical_json",
    "canonical_sha256",
    "check_key_id",
    "default_local_track_path",
    "load_agent_key",
    "parse_agent_id",
    "proposal_signing_bytes",
    "report_gate_kind",
    "sha256_hex",
    "sign_proposal",
    "signing_notice",
    "verify_local_track",
    "verify_signature",
    "wire_agent_id",
]
