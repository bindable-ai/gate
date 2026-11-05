# bindable-gate/src/bindable_gate/contract/signing.py
"""The agent key, and the signature a Proposal carries.

The signed bytes are the RFC 8785 canonical JSON of an object with six
members: ``action`` (the Proposal's ``action_type``), ``nonce``,
``parameters`` (the Proposal's ``payload``), ``persistent_agent_id`` (the
agent's did:webvh DID itself), ``signed_at`` and ``target`` (the Proposal's
target, null when it has none, so a target cannot be added after signing). RFC 8785 orders
members by name, so the bytes run in that order whatever order they are
written in. The signature is ECDSA P-256 over SHA-256
of those bytes, DER-encoded, then base64: what the Bindable verifier reads.

The nonce is RFC 6979's, so the same key and bytes always give the same
signature: the one the TypeScript core and the platform's test vector give.

With a key, a Proposal adds ``agent_signature``, ``agent_key_id`` and
``signed_at``, every time. A signature that cannot be made raises, so the
Proposal is not sent rather than sent unsigned. Without a key nothing is
signed, and :func:`signing_notice` gives the words a startup line says so
with.

Nothing here puts the key, or the contents of the key file, into a message.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.backends.openssl import backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from bindable_gate.contract.agent import DID_WEBVH
from bindable_gate.contract.canonical import canonical_json
from bindable_gate.core.errors import GateConfigError

#: The settings a key error names.
KEY_FILE_SETTING: Final[str] = "BINDABLE_AGENT_KEY_FILE"
KEY_ID_SETTING: Final[str] = "BINDABLE_AGENT_KEY_ID"

_KEY_ID: Final[re.Pattern[str]] = re.compile(
    r"^(did:webvh:[^#\s]+)#"
    r"([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})$"
)


def check_key_id(
    key_id: str | None,
    *,
    did: str | None,
    setting: str = KEY_ID_SETTING,
    file_setting: str = KEY_FILE_SETTING,
) -> str:
    """The agent key id, ``<did>#<key uuid>`` with the agent's did:webvh DID,
    checked. There is no default.

    Raises:
        GateConfigError: It is missing, not that form, or names another agent
            than ``did``.
    """
    if not key_id or not key_id.strip():
        raise GateConfigError(f"{setting} is required with {file_setting}: <did>#<key uuid>")
    match = _KEY_ID.match(key_id.strip())
    if match is None or DID_WEBVH.match(match.group(1)) is None:
        raise GateConfigError(
            f"{setting} is not a key id: expected <did>#<key uuid>, with the agent's did:webvh DID"
        )
    if did is not None and match.group(1) != did:
        raise GateConfigError(f"{setting} names another agent than the agent's DID")
    return key_id.strip()


@dataclass(frozen=True)
class AgentKey:
    """An ECDSA P-256 private key and the id a verifier looks it up by."""

    key_id: str
    _private: ec.EllipticCurvePrivateKey

    def __repr__(self) -> str:
        return f"AgentKey(key_id={self.key_id!r})"

    @property
    def public_key(self) -> ec.EllipticCurvePublicKey:
        return self._private.public_key()

    def public_pem(self) -> str:
        return self.public_key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode("ascii")

    def sign(self, text: str) -> str:
        """Base64 DER ECDSA P-256 / SHA-256 signature over ``text``'s UTF-8
        bytes, with the RFC 6979 nonce."""
        der = self._private.sign(text.encode("utf-8"), ec.ECDSA(hashes.SHA256(), deterministic_signing=True))
        return base64.b64encode(der).decode("ascii")

    @classmethod
    def from_private_key(cls, key: Any, key_id: str, *, setting: str = KEY_FILE_SETTING) -> AgentKey:
        """Check ``key`` is an ECDSA P-256 private key."""
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise GateConfigError(f"{setting} is not an ECDSA P-256 private key")
        if not key_id:
            raise GateConfigError("the agent key has no key id")
        if not backend.ecdsa_deterministic_supported():
            raise GateConfigError(
                f"{setting} cannot be used: this Python's cryptography has no RFC 6979 "
                "signing, which needs OpenSSL 3.2 or later"
            )
        return cls(key_id=key_id, _private=key)


def load_agent_key(path: str | Path, key_id: str, *, setting: str = KEY_FILE_SETTING) -> AgentKey:
    """The PEM private key at ``path``, checked to be ECDSA P-256.

    Raises:
        GateConfigError: naming ``setting``, never the key or the file's contents.
    """
    try:
        pem = Path(path).expanduser().read_bytes()
    except OSError:
        raise GateConfigError(f"{setting} could not be read") from None
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise GateConfigError(f"{setting} is not a PEM private key") from None
    return AgentKey.from_private_key(key, key_id, setting=setting)


def signing_notice(key: AgentKey | None, *, connected: bool, setting: str = KEY_FILE_SETTING) -> str:
    """What a Gate's startup line says about signing. With a key, every
    Proposal (connected) or every local Track line (standalone) is signed;
    without one, nothing is."""
    if key is None:
        return f"nothing is signed, because {setting} is not set"
    signed = "every Proposal" if connected else "every local Track line"
    return f"{signed} is signed with agent key {key.key_id}"


def proposal_signing_bytes(
    *,
    persistent_agent_id: str,
    action: str,
    nonce: str,
    signed_at: str,
    parameters: Mapping[str, Any],
    target: str | None,
) -> str:
    """The canonical text a Proposal's signature covers."""
    return canonical_json({
        "persistent_agent_id": persistent_agent_id,
        "action": action,
        "nonce": nonce,
        "signed_at": signed_at,
        "parameters": dict(parameters),
        "target": target,
    })


def sign_proposal(
    key: AgentKey,
    *,
    persistent_agent_id: str,
    action: str,
    nonce: str,
    signed_at: str,
    parameters: Mapping[str, Any],
    target: str | None,
) -> dict[str, str]:
    """``agent_signature``, ``agent_key_id`` and ``signed_at`` for a Proposal."""
    text = proposal_signing_bytes(
        persistent_agent_id=persistent_agent_id,
        action=action,
        nonce=nonce,
        signed_at=signed_at,
        parameters=parameters,
        target=target,
    )
    return {"agent_signature": key.sign(text), "agent_key_id": key.key_id, "signed_at": signed_at}


def load_public_key(pem: str | bytes) -> ec.EllipticCurvePublicKey:
    """A P-256 public key from PEM."""
    key = serialization.load_pem_public_key(pem.encode("ascii") if isinstance(pem, str) else pem)
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("not an ECDSA P-256 public key")  # noqa: TRY004 - callers catch ValueError
    return key


def verify_signature(public_key: ec.EllipticCurvePublicKey, text: str, signature: str) -> bool:
    """Whether ``signature`` (base64 DER) is ``public_key``'s over ``text``."""
    try:
        public_key.verify(base64.b64decode(signature, validate=True), text.encode("utf-8"),
                          ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True
