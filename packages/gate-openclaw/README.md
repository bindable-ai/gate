# @bindable-ai/gate-openclaw

The Bindable Gate for [OpenClaw](https://docs.openclaw.ai). Every tool call the
agent makes is reviewed by Bindable before it runs. That covers built-in tools,
other plugins' tools and MCP tools.

- **Released**: the call runs. Nothing else changes.
- **Blocked**: the call does not run. The agent sees Bindable's reason.
- **No decision**: the call does not run. This covers Bindable answering with
  a 4xx or with anything but a Receipt. The agent sees `decision unavailable`.
- **Bindable unreachable** (no answer, a timeout or a 5xx): the agent's offline
  setting decides. *Stop the agent* blocks with `decision unavailable`. *Ask me
  first*, the default, blocks with *Bindable is unreachable. This agent's
  actions are not being reviewed. To continue anyway run: bindable-gate
  offline accept*. If nobody runs it within 10 minutes, the agent stops until
  Bindable is back, each call blocked with a line saying so. *Switch
  automatically* lets the call run, not reviewed, says
  so once in the Gateway's log, and keeps it as an offline record, which goes
  to the Track when Bindable is back.
- **The accept command needs the Python package.** `bindable-gate offline
  accept` is the `bindable-gate` Python package's command; this plugin ships
  none. On a machine that runs only OpenClaw, install it once
  (`pipx install bindable-gate`) so the person can accept under *ask me
  first*. Both Gates keep the same files, so it accepts for this plugin too.

After a call runs, the Gate reports its enforcement outcome to Bindable.

The plugin has no allow-list. It restricts no tool and changes no network
setting. The one requirement is that the machine running the OpenClaw Gateway
can reach the Bindable API host.

## Install

```bash
openclaw plugins install @bindable-ai/gate-openclaw
openclaw plugins enable bindable-gate
```

To install from a tarball, run
`openclaw plugins install ./bindable-ai-gate-openclaw-0.1.0.tgz`. In a
non-interactive install, add `--force --accept-capabilities`, after you have
reviewed the plugin. It declares three hooks: `before_tool_call`,
`after_tool_call` and `gateway_stop`. The archive carries everything the
plugin runs, so it installs with no network access.

## Configure

The Gate reads its settings from the Gateway's environment. For a Gateway run
as a service, put them in `~/.openclaw/.env`.

| Variable | Required | Value |
| --- | --- | --- |
| `BINDABLE_AGENT_ID` | yes, to connect | The agent's DID, `did:webvh:<SCID>:<host>:agent:<Agent ID>`, as Bindable shows it. Nothing else is accepted. |
| `BINDABLE_GATE_CREDENTIAL` | yes, to connect | The Gate credential (`bnd_gt_…`). Without it, the Gate runs standalone (see below). |
| `BINDABLE_API_BASE_URL` | yes, to connect | Where Bindable is: the Bindable API base URL you were given. Must be https. |
| `BINDABLE_REVIEW_CONTEXT` | no | `test` or `live`. `test` if not set. |
| `BINDABLE_AGENT_KEY_FILE` | no | Path to a PEM ECDSA P-256 private key. If set, every Proposal is signed. |
| `BINDABLE_AGENT_KEY_ID` | with a key | The key's id, `<did>#<key uuid>`, with the agent's did:webvh DID. Required when `BINDABLE_AGENT_KEY_FILE` is set; there is no default. |

The Gate never logs the credential or the key and never puts them in an error
message. If the credential appears in a tool's parameters, it is masked before
the Proposal is sent.

## Restart

Restart the Gateway so it reads the new settings:

```bash
openclaw gateway restart
```

When the Gateway starts, it logs one line: whether the Gate is connected
(and in which review context) or recording locally, and whether it signs.
Without `BINDABLE_AGENT_KEY_FILE` the line ends `nothing is signed, because
BINDABLE_AGENT_KEY_FILE is not set`.

## What a Proposal carries

- `action_type`: the tool's name.
- `payload`: `params` (the tool's parameters), plus whichever of `toolKind`,
  `derivedPaths`, `runId`, `toolCallId`, `agentId` and `sessionKey` OpenClaw
  supplies.

`agentId` and `sessionKey` are OpenClaw's own names for the agent and session.
They are not the agent's Bindable DID.

## Signing

When `BINDABLE_AGENT_KEY_FILE` is set, the Gate signs every Proposal:

- `agent_signature`: base64 DER ECDSA P-256 / SHA-256 signature.
- `agent_key_id`: the key's id.
- `signed_at`: when the Proposal was signed.

The signed bytes are the RFC 8785 canonical JSON of six members, in the order
RFC 8785 sorts them: `action` (the tool's name), `nonce`, `parameters` (the
payload), `persistent_agent_id`, `signed_at` and `target`. `persistent_agent_id`
is the agent's did:webvh DID itself. An
OpenClaw tool call has no target, so `target` is `null`.

The signature uses the RFC 6979 nonce, so the same key and bytes always give
the same signature, as the platform's test vector requires. Without a key file
nothing is signed, and the startup line says so.

A key file that cannot be read, is not PEM, or is not P-256 is reported once
at startup. After that, every tool call is blocked with that reason. It does
not fall back to unsigned Proposals.

## Standalone mode: the local Track

With no `BINDABLE_GATE_CREDENTIAL`, the Gate is not connected to Bindable. It
logs this once at startup. Every call is allowed, and each one is appended to
the **local Track**, `~/.bindable/local-track.jsonl`. Each line is one JSON
object for one call:

- `id`, `agent_id`, `tool`, `params`
- `result_summary`: the result's digest and its first 200 characters
- `error`, `duration_ms`, `started_at`, `finished_at`
- `previous_hash` and `chain_hash`

`chain_hash` is SHA-256 over the RFC 8785 canonical JSON of the line without
`chain_hash` and `agent_signature`. `previous_hash` is the line before's
`chain_hash` (64 zeros for the first line). If any line is changed, removed or
reordered, every hash after it breaks. With an agent key, each line also
carries `agent_key_id`, `signed_at` and `agent_signature` over the same bytes.

To check a local Track, run `verifyLocalTrack(path, publicKey?)` from
`@bindable-ai/gate`.

The local Track is a record kept on this machine. Bindable has reviewed none
of it.

## How it fails closed

- OpenClaw lets a tool run if a `before_tool_call` handler throws or times
  out. So the handler never throws, and the Gate answers within 12 seconds.
  The hook's timeout is pinned at 15 seconds rather than left to OpenClaw's
  default.
- OpenClaw drops every hook of a plugin whose registration throws. So a
  configuration that cannot be used does not throw. Instead it registers a
  handler that blocks every call and gives the reason.
- A call is released only when the review's verdict and the Receipt's verdict
  are both exactly `passed` and the Receipt has a `receipt_id`.

## Requirements

Node 20 or later, and any OpenClaw with typed plugin hooks
(`before_tool_call` / `after_tool_call`). Tested against OpenClaw's
2026.10.7 plugin SDK.

## Licence

Apache-2.0. See `THIRD_PARTY_NOTICES.md` for what the bundle includes.
