# Contract test vectors

What the Python and the TypeScript Gates are both tested against.

## `platform-vector.json`: the platform team's vector

The platform's test vector for agent-signed Proposals, copied as published
from the platform's `docs/vectors/agent-signed-proposal.json`. Both test
suites assert against it: the Gate gives its `signing_input` from its
`proposal`, `signed_at` and the did in `agent_key_id`, and verifies its
`agent_signature` under `public_key_jwk`. Replace the file when the platform
publishes a new one; do not edit it here.

The copy here is from the platform's main branch. It signs the agent's
did:webvh DID as `persistent_agent_id`, and `agent_key_id` names the key under
that DID. Both suites give its `signing_input` and, signing with the key from
`private_key_seed`, its `agent_signature` exactly: the nonce is RFC 6979's, so
the signature is deterministic. Nothing is skipped.

The fields the tests read:

```json
{
  "private_key_seed": "<SHA-256 of it is the test key's private scalar>",
  "public_key_jwk": {"kty": "EC", "crv": "P-256", "x": "...", "y": "..."},
  "proposal": {"agent_id": "...", "action_type": "...", "target": "...", "payload": {}, "nonce": "..."},
  "signed_at": "<timestamp>",
  "agent_key_id": "did:webvh:<SCID>:<host>:agent:<Agent ID>#<key uuid>",
  "signing_input": "<the RFC 8785 canonical JSON, exactly>",
  "signing_input_sha256_hex": "...",
  "agent_signature": "<base64 DER, RFC 6979>"
}
```

The signed bytes have six members, sorted by name as RFC 8785 requires:
`action`, `nonce`, `parameters`, `persistent_agent_id`, `signed_at`, `target`.
`target` is `null` when the Proposal has none. `persistent_agent_id` is the
agent's did:webvh DID.

## `contract-vectors.json` and the local Tracks

The shared vector both Gates make and verify: the signed bytes of the worked
example, signing the made-up did:webvh DID, one signature made by each side,
and one local Track written by each side.

## The offline records

`offline-records.python.jsonl` and `offline-records.typescript.jsonl`: three
offline records each, written by each Gate's offline mode and signed with a
key made for the vector, whose public half is in `contract-vectors.json`
under `offline_records`. Each suite verifies both chains with
`verify_records` / `verifyRecords`, and the Python suite checks the two sides
write the same fields.
