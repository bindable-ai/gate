# @bindable-ai/gate

The Bindable Gate core, in TypeScript. It handles one tool call at a time:

- builds the Proposal
- calls Bindable
- enforces the verdict
- reports the enforcement outcome
- fails closed
- signs, if an agent key is configured

A host adapter, such as `@bindable-ai/gate-openclaw`, connects it to an agent
runtime.

This package speaks the same wire contract as the Python Gate in this
repository:

- `POST /v1/reviews` for the Proposal
- `POST /v1/receipts/{receipt_id}/report` for the enforcement outcome

Both present the Gate credential as `Authorization: Bearer`.

## Install

```bash
npm install @bindable-ai/gate
```

Node 20 or later.

## Use

```ts
import { Gate, readConfig } from "@bindable-ai/gate";

const gate = new Gate(readConfig(process.env), { gateVersion: "my-host/1.0.0", logger: console });

const call = { tool: "send_invoice", params: { customer: "acme" }, callId: "call-1" };
const decision = await gate.before(call);
if (decision.outcome === "released") {
  const result = await sendInvoice(call.params);
  await gate.after(call, { result });
} else {
  console.log(decision.reason); // Bindable's reason, or "decision unavailable"
}
```

- `before` never throws. A call is released only when the review's verdict and
  the Receipt's verdict are both exactly `passed` and the Receipt has a
  `receipt_id`. The call is blocked, with `decision unavailable`, if Bindable:
  - answers with a 4xx
  - sends an answer that cannot be read
  - sends an answer with no Receipt
- If Bindable is unreachable, does not answer within the deadline (12 s), or
  answers with a 5xx, the agent's offline setting decides (`offline.ts`; the
  repository's `docs/offline-mode.md`). *Stop the agent* blocks with `decision
  unavailable`; *ask me first*, the default, blocks with the line telling the
  person to run `bindable-gate offline accept`; *switch automatically*, or an
  accepted *ask me first*, releases the call as `{ outcome: "released",
  offline: true }`, with no Receipt, and `after` keeps it as an offline record.
  The records are sent to the Track when Bindable answers again.
- A blocked call is reported `blocked` straight away. `after` reports
  `released`, with the SHA-256 of the result's canonical JSON as
  `action_result_digest`.
- `after` never throws. A report that cannot be delivered is logged as a gap.

## Configure

`readConfig(env)` reads:

| Variable | Value |
| --- | --- |
| `BINDABLE_AGENT_ID` | The agent's DID, `did:webvh:<SCID>:<host>:agent:<Agent ID>`. A bare Agent ID or a did:web name is refused. |
| `BINDABLE_GATE_CREDENTIAL` | The Gate credential. Absent: standalone mode. |
| `BINDABLE_API_BASE_URL` | Where Bindable is. https only (http only for localhost). |
| `BINDABLE_REVIEW_CONTEXT` | `test` or `live`; `test` when absent. |
| `BINDABLE_AGENT_KEY_FILE` | Optional PEM ECDSA P-256 private key: every Proposal is signed. |
| `BINDABLE_AGENT_KEY_ID` | Required with a key file: `<did>#<key uuid>`, the key's verification method id, with the agent's did:webvh DID. There is no default. |

The did:webvh DID is the only agent identifier the Gate takes, and the only
one it shows: in its messages and on the local Track. A Proposal's `agent_id`
is the DID too, because since 2026-11-01 the platform refuses the bare Agent ID
there. One constant, `SEND_AGENT_DID`, sets that. A bare Agent ID given as
`BINDABLE_AGENT_ID` is refused with its own message.

If anything that is set cannot be used, `readConfig` throws `GateConfigError`.
The message names the setting and never contains its value. A host must then
block every call.

## Signing

With a key, each Proposal's signature covers the RFC 8785 canonical JSON of six
members, in the order RFC 8785 sorts them: `action`, `nonce`, `parameters`,
`persistent_agent_id`, `signed_at` and `target`. `target` is `null` when the
Proposal has none, so a target cannot be added after signing.
`persistent_agent_id` is the agent's did:webvh DID itself, and `agent_key_id`
names the key under that same DID. The signature is ECDSA
P-256 over SHA-256, DER-encoded, then base64, with the RFC 6979 nonce: the same
key and bytes always give the same signature, in this core, in the Python Gate
and on the platform. It is sent as `agent_signature`, `agent_key_id` and
`signed_at`.

With `BINDABLE_AGENT_KEY_FILE` set, every Proposal is signed. Without it,
nothing is signed, and `signingNotice` gives the words a host's startup line
says so with. A signature that cannot be made blocks the call; the Gate never
sends the Proposal unsigned instead.

## Reports

`gate_kind` is `REPORT_GATE_KIND`, which is `runtime_plugin`. `gate_version` is whatever the host
passes.

## Standalone mode: the local Track

With no Gate credential, every call is allowed and appended to a local,
hash-chained file, `~/.bindable/local-track.jsonl`. Nothing is sent anywhere.
`verifyLocalTrack(path, publicKey?)` recomputes the chain and checks each
line's signature. The local Track is a record kept on this machine. Bindable
has reviewed none of it.

## Licence

Apache-2.0.
