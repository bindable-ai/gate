# bindable-gate/src/bindable_gate/mcp/config.py
"""GateConfig — configuration for the Action Gate (ActionGateProxy).

Holds: the Bindable API base URL, the API key (read from the environment
variable BINDABLE_GATE_CREDENTIAL, never from a file in this repo), the downstream
MCP server spec (command + args), and fail_closed — which is True and
NOT configurable off: there is no fail-open mode. The Action Gate itself
decides nothing; these values only point it at the hosted Action Review.

With no BINDABLE_GATE_CREDENTIAL the Gate is STANDALONE: every tools/call is
forwarded and recorded on the local Track, and nothing is sent to Bindable.
Only the downstream command is then required.

BINDABLE_AGENT_KEY_FILE (optional) is a PEM ECDSA P-256 private key. Every
Proposal is signed with it, and so is every local Track line.
BINDABLE_AGENT_KEY_ID names the key, <did>#<key uuid> with the agent's
did:webvh DID, and is required with it: there is no default.

BINDABLE_AGENT_ID is the agent's did:webvh DID, did:webvh:<SCID>:<host>:agent:
<Agent ID>. Nothing else is taken: a bare Agent ID or a did:web name stops the
Gate at startup. A key or key id that cannot be
used stops the Gate at startup, naming the setting.

Guardrails:
- Thin transport: config only points the `bindable` PyPI client at the
  hosted API; no canonicalization, hashing, or signature code lives here.
- Fail-closed: if the Action Gate cannot be reached or the decision is
  indeterminate, the tool call is blocked.
- The API key is never logged and never appears in error text; a missing
  variable is reported by NAME only.
"""

from __future__ import annotations

import os
import shlex

from bindable_gate.contract.agent import AgentIdentity, parse_agent_id
from bindable_gate.contract.signing import AgentKey, check_key_id, load_agent_key
from bindable_gate.core.errors import GateConfigError
from bindable_gate.library.outcome import REVIEW_CONTEXTS

#: Which review context this Gate proposes in, from the environment.
REVIEW_CONTEXT_ENV_VAR = "BINDABLE_REVIEW_CONTEXT"

#: Re-exported: this module used to define a second class of the same name and
#: the same meaning, which is a trap for anyone writing `except`. There is one
#: now, and every import of it from here still resolves.
__all__ = ["REVIEW_CONTEXT_ENV_VAR", "GateConfig", "GateConfigError"]


class GateConfig:
    """Configuration for one Action Gate instance.

    Fields: api_base_url, gate_credential (from BINDABLE_GATE_CREDENTIAL), downstream_command,
    downstream_args. ``fail_closed`` is a read-only True — there is no
    parameter, environment variable, or attribute that turns it off.
    """

    def __init__(
        self,
        *,
        api_base_url: str,
        gate_credential: str,
        downstream_command: str,
        downstream_args: tuple[str, ...] = (),
        review_context: str,
        trackpoints_enabled: bool = True,
        agent: AgentIdentity | None = None,
        agent_key: AgentKey | None = None,
        standalone: bool = False,
    ) -> None:
        self.standalone = standalone
        self.agent = agent
        self.agent_key = agent_key
        if not downstream_command:
            raise GateConfigError("downstream_command must be non-empty")
        if standalone:
            self.api_base_url = ""
            self.gate_credential = ""
            self.downstream_command = downstream_command
            self.downstream_args = tuple(downstream_args)
            self.trackpoints_enabled = False
            self.review_context = review_context
            return
        if not api_base_url:
            raise GateConfigError("api_base_url must be non-empty")
        if not gate_credential:
            raise GateConfigError("gate_credential must be non-empty")
        if review_context not in REVIEW_CONTEXTS:
            raise GateConfigError(
                f"{REVIEW_CONTEXT_ENV_VAR} must be one of "
                f"{', '.join(REVIEW_CONTEXTS)}; a Gate proposes in one context and "
                "there is no default to guess"
            )
        self.api_base_url = api_base_url.rstrip("/")
        self.gate_credential = gate_credential
        self.downstream_command = downstream_command
        self.downstream_args = tuple(downstream_args)
        # Reporting only. Switching it off changes nothing about whether an
        # action runs — enforcement is identical either way.
        self.trackpoints_enabled = trackpoints_enabled
        # The context every Proposal from this Gate carries, set once here. A
        # stage is a different idea and this form no longer has one: the platform
        # takes a review context and serves no stage field at all.
        self.review_context = review_context

    @property
    def fail_closed(self) -> bool:
        """Always True. There is no fail-open mode."""
        return True

    @classmethod
    def from_env(cls) -> GateConfig:
        """Build a GateConfig from the environment.

        Required: BINDABLE_GATE_CREDENTIAL, BINDABLE_API_BASE_URL,
        BINDABLE_DOWNSTREAM_COMMAND and BINDABLE_REVIEW_CONTEXT (test or
        live). Optional: BINDABLE_DOWNSTREAM_ARGS (shell-style split into
        argv items) and BINDABLE_TRACKPOINTS_ENABLED (default true;
        "false", "0", "no" or "off" switch Outcome reporting off —
        enforcement is unaffected).

        With no BINDABLE_GATE_CREDENTIAL, only BINDABLE_DOWNSTREAM_COMMAND
        is required, and the Gate is standalone.

        Raises:
            GateConfigError: naming the missing variable (name only, never
                a value), or naming BINDABLE_REVIEW_CONTEXT and both
                allowed values when it is set to something else, or naming
                BINDABLE_AGENT_KEY_FILE when the key cannot be used.
        """
        agent = _agent(os.environ.get("BINDABLE_AGENT_ID"))
        if not (os.environ.get("BINDABLE_GATE_CREDENTIAL") or "").strip():
            if not os.environ.get("BINDABLE_DOWNSTREAM_COMMAND"):
                raise GateConfigError(
                    "missing required environment variable(s): BINDABLE_DOWNSTREAM_COMMAND"
                )
            return cls(
                api_base_url="",
                gate_credential="",
                downstream_command=os.environ["BINDABLE_DOWNSTREAM_COMMAND"],
                downstream_args=tuple(shlex.split(os.environ.get("BINDABLE_DOWNSTREAM_ARGS", ""))),
                review_context=os.environ.get(REVIEW_CONTEXT_ENV_VAR) or "test",
                agent=agent,
                agent_key=_agent_key(agent),
                standalone=True,
            )
        missing = [
            name
            for name in (
                "BINDABLE_GATE_CREDENTIAL",
                "BINDABLE_API_BASE_URL",
                "BINDABLE_DOWNSTREAM_COMMAND",
                REVIEW_CONTEXT_ENV_VAR,
            )
            if not os.environ.get(name)
        ]
        if missing:
            raise GateConfigError(
                "missing required environment variable(s): " + ", ".join(missing)
            )
        return cls(
            api_base_url=os.environ["BINDABLE_API_BASE_URL"],
            gate_credential=os.environ["BINDABLE_GATE_CREDENTIAL"],
            downstream_command=os.environ["BINDABLE_DOWNSTREAM_COMMAND"],
            downstream_args=tuple(
                shlex.split(os.environ.get("BINDABLE_DOWNSTREAM_ARGS", ""))
            ),
            review_context=os.environ[REVIEW_CONTEXT_ENV_VAR],
            trackpoints_enabled=_flag(
                os.environ.get("BINDABLE_TRACKPOINTS_ENABLED"), default=True
            ),
            agent=agent,
            agent_key=_agent_key(agent),
        )


def _agent(raw: str | None) -> AgentIdentity | None:
    """The agent from BINDABLE_AGENT_ID, its did:webvh DID, or None when it is
    not set.

    Raises:
        GateConfigError: It is set and is not a did:webvh DID.
    """
    return parse_agent_id(raw) if raw and raw.strip() else None


def _agent_key(agent: AgentIdentity | None) -> AgentKey | None:
    """The agent key from BINDABLE_AGENT_KEY_FILE, or None when it is not set."""
    path = (os.environ.get("BINDABLE_AGENT_KEY_FILE") or "").strip()
    if not path:
        return None
    key_id = check_key_id(os.environ.get("BINDABLE_AGENT_KEY_ID"), did=agent.did if agent else None)
    return load_agent_key(path, key_id)


def _flag(value: str | None, *, default: bool) -> bool:
    """Parse an optional boolean environment value. Unset means the default."""
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in {"false", "0", "no", "off"}
