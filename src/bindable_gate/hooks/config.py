# bindable-gate/src/bindable_gate/hooks/config.py
"""The agent hook's configuration: one JSON file, holding no credential.

Connected, the file names the platform, the agent, the review context and the
keychain item that holds the Gate credential:

    {
      "base_url": "https://<Bindable API host>",
      "agent_id": "did:webvh:<SCID>:<host>:agent:<Agent ID>",
      "review_context": "test",
      "keychain": {"service": "bindable-agent-hook", "account": "<the same DID>"}
    }

``agent_id`` is the agent's did:webvh DID, and nothing else is taken: a bare
Agent ID or a did:web name is refused.

With no ``keychain`` item the hook is STANDALONE: no Gate credential, so every
call is allowed and recorded on the local Track, as the TypeScript core does.
``base_url`` is then not needed.

Optional, either way:

- ``agent_key_file``: a PEM ECDSA P-256 private key. Proposals are signed with
  it, and so is every
  line of the local Track. A key that cannot be used blocks every call.
- ``agent_key_id``: the key's id, ``<did>#<key uuid>`` with the agent's
  did:webvh DID. Required with
  ``agent_key_file``; there is no default.
- ``local_track``: where the local Track is kept, ``~/.bindable/
  local-track.jsonl`` by default.
- ``state_dir``: where the hook keeps calls waiting for their report. By
  default the user's Library folder on macOS and ``$XDG_STATE_HOME`` elsewhere.
- ``review_context``: ``test`` when not given.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from bindable_gate.contract.agent import parse_agent_id
from bindable_gate.contract.signing import AgentKey, check_key_id, load_agent_key
from bindable_gate.core.errors import GateConfigError
from bindable_gate.hooks.credential import KeychainItem
from bindable_gate.library.outcome import REVIEW_CONTEXTS

#: Where ``hooks install`` puts the configuration on macOS.
DEFAULT_CONFIG_DIR: Final[str] = "/Library/Application Support/Bindable/agent-hook"

_LOCAL_HOSTS: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1", "::1"})


class ConfigError(Exception):
    """The configuration cannot be used. The message is one plain sentence."""


@dataclass(frozen=True)
class HookConfig:
    """What the hook needs to propose and report for one agent, or to record
    locally when it has no credential."""

    base_url: str | None
    #: The Agent ID inside the DID.
    agent_id: str | None
    review_context: str
    keychain: KeychainItem | None
    state_dir: Path
    #: The agent's did:webvh DID: what the hook is configured with and shows.
    did: str | None = None
    agent_key_file: Path | None = None
    agent_key_id: str | None = None
    local_track: Path | None = None

    @property
    def standalone(self) -> bool:
        """No credential is configured: allow and record locally."""
        return self.keychain is None

    @property
    def agent_shown(self) -> str | None:
        """The agent as the hook shows it: its did:webvh DID."""
        return self.did

    def agent_key(self) -> AgentKey | None:
        """The agent key, loaded and checked, or None when none is configured.

        Raises:
            ConfigError: The key cannot be used. The message names the setting.
        """
        if self.agent_key_file is None:
            return None
        try:
            key_id = check_key_id(self.agent_key_id, did=self.did, setting="agent_key_id",
                                  file_setting="agent_key_file")
            return load_agent_key(self.agent_key_file, key_id, setting="agent_key_file")
        except GateConfigError as error:
            raise ConfigError(f"{error}.") from None

    @classmethod
    def load(cls, path: Path) -> HookConfig:
        """The configuration at ``path``, or :class:`ConfigError`."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ConfigError(
                f"The hook configuration {path} does not exist. "
                "Ask your administrator to install it."
            ) from None
        except (OSError, ValueError):
            raise ConfigError(
                f"The hook configuration {path} cannot be read as JSON."
            ) from None
        if not isinstance(raw, dict):
            raise ConfigError(f"The hook configuration {path} is not a JSON object.")
        return cls.from_mapping(raw, source=str(path))

    @classmethod
    def from_mapping(cls, raw: dict[str, Any], *, source: str = "the configuration") -> HookConfig:
        """Validate the values; name the first one that is wrong."""

        def text(name: str, value: Any) -> str:
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"{source} has no {name}.")
            return value.strip()

        def optional(name: str) -> str | None:
            value = raw.get(name)
            return None if value is None else text(name, value)

        keychain = raw.get("keychain")
        item: KeychainItem | None = None
        if keychain is not None:
            if not isinstance(keychain, dict):
                raise ConfigError(f"{source} has no keychain item.")
            item = KeychainItem(
                service=text("keychain.service", keychain.get("service")),
                account=text("keychain.account", keychain.get("account")),
            )

        base_url: str | None = None
        if item is not None or raw.get("base_url") is not None:
            base_url = text("base_url", raw.get("base_url")).rstrip("/")
            parts = urlsplit(base_url)
            local = parts.hostname in _LOCAL_HOSTS
            if parts.scheme != "https" and not (parts.scheme == "http" and local):
                raise ConfigError(f"{source} gives a base_url that is not https.")
            if parts.path not in ("", "/") or parts.query or parts.fragment:
                raise ConfigError(f"{source} gives a base_url with a path; give the origin only.")

        agent_id: str | None = None
        did: str | None = None
        raw_agent = text("agent_id", raw.get("agent_id")) if item is not None else optional("agent_id")
        if raw_agent is not None:
            try:
                identity = parse_agent_id(raw_agent, setting="agent_id")
            except GateConfigError as error:
                reason = str(error).removeprefix("agent_id ")
                raise ConfigError(f"{source} gives an agent_id that {reason}.") from None
            agent_id, did = identity.agent_id, identity.did

        review_context = optional("review_context") or "test"
        if review_context not in REVIEW_CONTEXTS:
            raise ConfigError(
                f"{source} gives review_context {review_context!r}; "
                f"use one of {', '.join(REVIEW_CONTEXTS)}."
            )

        key_file = optional("agent_key_file")
        key_id = optional("agent_key_id")
        if key_file is not None:
            try:
                check_key_id(key_id, did=did, setting="agent_key_id", file_setting="agent_key_file")
            except GateConfigError as error:
                raise ConfigError(f"{source}: {error}.") from None
        track = optional("local_track")
        state = optional("state_dir")
        state_dir = Path(state) if state is not None else default_state_dir(agent_id or "standalone")
        return cls(
            base_url=base_url,
            agent_id=agent_id,
            review_context=review_context,
            keychain=item,
            state_dir=state_dir.expanduser(),
            did=did,
            agent_key_file=Path(key_file).expanduser() if key_file else None,
            agent_key_id=key_id,
            local_track=Path(track).expanduser() if track else None,
        )


def default_state_dir(agent_id: str) -> Path:
    """Where the hook keeps Receipts waiting for their report, per agent."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Bindable" / "agent-hook"
    else:
        state_home = os.environ.get("XDG_STATE_HOME")
        base = (Path(state_home) if state_home else Path.home() / ".local" / "state") / "bindable" / "agent-hook"
    return base / agent_id
