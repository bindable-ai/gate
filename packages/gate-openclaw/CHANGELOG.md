# Changelog

## Unreleased

- On NemoClaw, `BINDABLE_GATE_CREDENTIAL` can hold OpenShell's placeholder
  instead of the credential: the plugin sends it unchanged in the
  `Authorization` header to Bindable only, and never logs it.
- Offline mode, from the core: while Bindable is unreachable the agent's
  offline setting decides each tool call, and the plugin's lines go to the
  Gateway's log. `bindable-gate offline accept` comes from the
  `bindable-gate` Python package, which an OpenClaw-only machine needs
  installed for *ask me first*.

## 0.1.0

First release.

- `before_tool_call`, with no matcher, reviews every tool call: built-in,
  plugin and MCP.
  - Released: returns nothing.
  - Blocked: `{ block: true, blockReason }` with Bindable's reason.
  - No decision: `{ block: true, blockReason: "decision unavailable" }`.
- The Proposal is built from `toolName`, `params`, `toolKind`,
  `derivedPaths`, `runId`, `toolCallId`, `agentId` and `sessionKey`.
- `BINDABLE_AGENT_ID` is the agent's did:webvh DID, and nothing else is
  accepted. The connected message names the agent by it.
- `after_tool_call`, with no matcher, reports the enforcement outcome of each
  call that ran. `gate_version` is `gate-openclaw/0.1.0`.
- Never throws in a hook or at registration:
  - A configuration that cannot be used is logged once at startup, and every
    call is then blocked with the reason.
  - The hook's timeout is pinned at 15 seconds, and the Gate answers within
    12.
- The startup line says whether the Gate signs: `every Proposal is signed
  with agent key <key id>`, or `nothing is signed, because
  BINDABLE_AGENT_KEY_FILE is not set`. A key file that cannot be read or is
  not ECDSA P-256 blocks every call, with that reason.
- Standalone mode: with no Gate credential, the plugin logs once at startup
  that the agent is recording locally and is not connected to Bindable. Every
  call is allowed and kept on the local Track.
- One bundled file: the archive installs with no dependencies of its own.
