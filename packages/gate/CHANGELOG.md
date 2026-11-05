# Changelog

## Unreleased

- The Gate credential's form is not checked: a placeholder that a sandbox's
  proxy replaces, such as OpenShell's `openshell:resolve:env:<NAME>`, is sent
  unchanged in the `Authorization` header to Bindable and nowhere else, and is
  redacted from the payload and the log like any credential. Tests pin it.
- Offline mode (`offline.ts`): where Bindable is unreachable (no answer, a
  timeout or a 5xx), the agent's offline setting decides. *Switch
  automatically* releases the call, not reviewed, as `{ outcome: "released",
  offline: true }` with no Receipt, and `after` keeps it as an offline record;
  *ask me first*, the default, blocks with the line telling the person to run
  `bindable-gate offline accept`, and after 10 minutes with nobody accepted
  (`ACCEPT_WINDOW_MS`) blocks with `STOPPED_MESSAGE` until Bindable is back; *stop the agent* blocks with `decision
  unavailable`, as before. A 4xx and a block never switch. The records are
  hash-chained, signed with the agent key where one is set, and sent to the
  Track when Bindable answers again. The same files as the Python Gate's.
- `DecisionUnavailable` carries `noAnswer`.

## 0.1.0

First release.

- `Gate.before` submits each tool call as a Proposal to `POST /v1/reviews`.
  It releases only on `passed` from both the review and the Receipt, with a
  `receipt_id`. Otherwise it blocks with Bindable's reason.
- Fails closed with `decision unavailable` when Bindable:
  - is unreachable
  - times out
  - answers with anything but 200
  - sends an unreadable answer
  - sends no Receipt

  `before` never throws, and it answers within 12 seconds. A transport failure
  is retried once with the same Proposal.
- Reports the enforcement outcome to `POST /v1/receipts/{receipt_id}/report`:
  `blocked` at once, `released` after the call runs. A released report carries
  the result's SHA-256 as `action_result_digest`. `gate_kind` is
  `runtime_plugin`.
- `readConfig` reads `BINDABLE_AGENT_ID` (the agent's did:webvh DID, and
  nothing else),
  `BINDABLE_GATE_CREDENTIAL`, `BINDABLE_API_BASE_URL`,
  `BINDABLE_REVIEW_CONTEXT`, `BINDABLE_AGENT_KEY_FILE` and
  `BINDABLE_AGENT_KEY_ID`.
- With an agent key, signs every Proposal: the RFC 8785 bytes of `action`,
  `nonce`, `parameters`, `persistent_agent_id`, `signed_at` and `target`
  (`null` when there is none), ECDSA P-256 with the RFC 6979 nonce, base64
  DER, so the same key and bytes always give the same signature (signing by
  `@noble/curves` 2.4.0, since Node has no deterministic ECDSA).
  `persistent_agent_id` is the agent's did:webvh DID itself.
  `BINDABLE_AGENT_KEY_ID` (`<did:webvh DID>#<key uuid>`) is required with a
  key. With a key, every Proposal carries `agent_signature`, `agent_key_id`
  and `signed_at`; a signature that cannot be made blocks the call, and the
  Proposal is never sent unsigned. Without a key nothing is signed, and
  `signingNotice` says so for the host's startup line.
- The agent's did:webvh DID is the only agent identifier the Gate takes or
  shows, and a Proposal's `agent_id` is the DID too: since 2026-11-01 the
  platform refuses the bare Agent ID there. One constant, `SEND_AGENT_DID`,
  sets that. A bare Agent ID is refused with its own message.
- The local Track reads its chain's end again before each append, so a
  Python Gate writing the same file does not fork the chain. Node has no file
  lock, so two writers appending at the same instant still can.
- Standalone mode: with no Gate credential, every call is allowed and appended
  to the hash-chained local Track, `~/.bindable/local-track.jsonl`.
  `verifyLocalTrack` recomputes the chain.
- The Gate credential is masked in Proposal payloads and redacted from every
  log line. It never appears in an error message.
