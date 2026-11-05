# The Python library

The Library Gate puts the hosted Action Review in front of a function you already
have. It submits the Proposal, obtains the Receipt, runs your function only where
the verdict was `passed`, and reports what it did either way. Those four things
are one act, and there is no way to do any of them without the others.

```python
from bindable_gate import Gate

gate = Gate(base_url=BINDABLE_URL, credential=GATE_CREDENTIAL, review_context="test")

@gate.control(
    agent_id=AGENT_ID,
    action_type="send_invoice",
    payload_fields=["customer", "amount_cents"],
)
def send_invoice(customer: str, amount_cents: int, api_key: str) -> str:
    return billing.send(customer, amount_cents, api_key=api_key)

outcome = send_invoice(customer="acme", amount_cents=250_000, api_key=BILLING_KEY)
```

`send_invoice` runs only if the review passed. `outcome.action_result` is what it
returned, and `outcome.receipt_id` is the Receipt that permitted it. The Proposal
carries `customer` and `amount_cents` and nothing else: `api_key` is an argument
of your function, and it stays in your process.

> **Never put API keys, passwords, secrets or personal data into a payload.
> Send only what your rules need.** `payload_fields` is the list of the
> function's arguments that are sent; every other argument stays in your process
> and is never sent.

## Constructing the Gate

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `base_url` | `str` | **yes** | Where the platform is. |
| `credential` | `str` | **yes** | The Gate credential, a `bnd_gt_` token. |
| `review_context` | `str` | **yes** | `test` or `live`. See below. |
| `developer_key` | `str \| None` | no | The Account's API key, a `bnd_sk_` token. Needed only by `submit_evidence`. See below. |
| `timeout` | `float` | no | Seconds to wait on a call. Default 30. |
| `attempts` | `int` | no | Attempts at one Proposal, the first included. Default 3. |
| `transport` | `httpx.BaseTransport \| None` | no | For a test that answers without a network. It changes where calls go and nothing about what is released. |
| `offline_dir` | `str \| Path \| None` | no | Where offline mode keeps each agent's files. Default `~/.bindable/offline`, or `BINDABLE_OFFLINE_DIR`. |
| `notify` | `Callable[[str], None] \| None` | no | What prints offline mode's lines for the person. Standard error by default. |

Anything missing raises `GateConfigError` **at construction, before any network
call**, so a Gate that cannot propose never exists to be called.

## The review context

A Gate is bound to one review context — `test` or `live` — when it is
constructed, and it never takes one per call. Every Proposal it makes carries
that context. Two contexts means two Gate objects.

A Gate constructed without a context raises. There is no default: which context
your actions are reviewed in is not something a library should guess.

## What you can call

Every call names the agent by its DID, `did:webvh:<SCID>:<host>:agent:<Agent ID>`,
as `agent_id`, and the Proposal carries that DID as its `agent_id`. Nothing
else is taken: a bare Agent ID or a `did:web:` name raises `GateConfigError`,
and nothing is sent.

**`gate.propose(agent_id, action_type, payload, target=None)`** submits a
Proposal and returns the Receipt. It returns the Receipt whatever the verdict
was, and it releases nothing: a review that failed is still a review that
happened. Use it when you want to read the verdict yourself.

You may pass `proposed_action_id`, `nonce` and `proposed_at` to retry a Proposal
you have already made. Leave them out and the Gate mints them.

**`gate.run(agent_id=..., action_type=..., payload=..., handler=...)`** is the
whole contract: review, then run or withhold, then report. `handler` is called
with no arguments, once, only on a pass. Pass `on_blocked="return"` to get the
blocked outcome back instead of an exception; neither releases anything.

Pass `identifiers=` to retry a whole gated call you have already attempted. It
takes an `Identifiers(proposed_action_id, nonce, proposed_at)`, and the Gate
presents those three rather than minting new ones, so the platform answers with
the Receipt the earlier attempt already minted instead of creating a second
record of one action. Leave it out and the Gate mints them itself.

**`gate.control(agent_id=..., action_type=..., payload_fields=[...])`** is the
decorator above. The decorated function returns an `Outcome`, never its own bare
return value: what it produced and the Receipt that permitted it are one answer.

`payload_fields` is required: a non-empty list of the function's own argument
names. Only those arguments become the Proposal's payload, in the order you list
them, with their defaults applied; every other argument stays in your process
and is never sent. A missing or empty list, a bare string, a name given twice,
or a name the function does not take (including the names of `*args` and
`**kwargs`) raises `GateConfigError` when the function is decorated, before any
network call.

**Never put API keys, passwords, secrets or personal data into a payload. Send
only what your rules need.** The payload is sent to Bindable and is what your
Contract's rules read. `gate.run` and `gate.propose` take a `payload` you build
yourself; the same warning applies to what you put in it.

**`gate.report(receipt, outcome=..., proposed_at=..., enforced_at=...)`** reports
an enforcement outcome for a Receipt. `run` and `control` call it for you.

Every report names `gate_kind` `library` and this package's version, so a
Trackpoint says what enforced and which build of it did. `action_result_digest`
is sent as null: computing one would mean hashing your result here, and this
package holds no hashing at all.

## Recording Evidence

A Contract rule may read a fact you record beforehand. `submit_evidence` records
one:

```python
record = gate.submit_evidence(
    AGENT_ID,
    "invoice.raised",
    {"invoice_total_cents": 250_000, "currency": "USD"},
    external_id="inv-2026-0001",
    applicability="test_and_live",
    source="billing",
)
record["evidence_id"]
record["digest"]
```

**This is the one call that does not present the Gate credential.** Evidence
belongs to the Account rather than to a Gate, so the endpoint takes the Account's
developer key and refuses a Gate credential. Construct the Gate with
`developer_key=` to use it; without one the call raises `GateConfigError` before
anything is sent.

`external_id` is the record's identity and it is yours to choose. A second
submission under the same one is refused with `external_id_exists`, and the
library raises it — it is never reported as a success, because a caller must not
believe it wrote something it did not.

`applicability` is `test_only` or `test_and_live`. `recorded_at` defaults to now.
`value` is canonical JSON **with no floats**: a float anywhere inside it, `1.0`
included, is refused before anything is sent, and the error names where it is.
So is anything JSON cannot carry, such as a `Decimal`, a `datetime`, a set, or a
mapping key that is not a string: `EvidenceValueRefused` names where it is and
what it is, never a bare `TypeError`. The library will not convert it for you — converting would be this code deciding
what you meant, and a rule comparing the result would be evaluating a number
nobody wrote.

The digest comes back from the platform in the record. Nothing here computes one.

Retrying is safe here too: the library mints one `Idempotency-Key` per call and
presents it on every attempt, so an attempt whose answer was lost is answered with
what it did rather than writing a second record. `EvidenceUnreachable` carries that
key if you want to retry the call yourself, through `idempotency_key=`.

## Retrying is safe

The Gate mints `proposed_action_id` and `nonce` once per intended action and
presents the same ones on every attempt at it. The platform answers the same
Proposal with the Receipt it already minted, so a retry after a dropped
connection collects the original Receipt instead of creating a second record of
one action.

If the Gate gives up, `ProposalUnreachable` carries those identifiers so you can
present them again yourself:

```python
from bindable_gate import Identifiers, ProposalUnreachable

try:
    outcome = send_invoice(customer="acme", amount_cents=250_000)
except ProposalUnreachable as lost:
    outcome = gate.run(
        agent_id=AGENT_ID,
        action_type="send_invoice",
        payload={"customer": "acme", "amount_cents": 250_000},
        handler=lambda: billing.send("acme", 250_000),
        identifiers=Identifiers(lost.proposed_action_id, lost.nonce, lost.proposed_at),
    )
```

## Fail-closed, and offline mode

If the review cannot be read, is refused, or passes with no Receipt to tie the
release to, **your function does not run.** There is no `fail_open` argument, no
environment variable and no test hook.

If Bindable cannot be reached at all (no answer, a timeout or a 5xx), your
agent's offline setting, kept in Bindable, decides. See
[offline-mode.md](offline-mode.md):

- *stop the agent*: `run` raises `ProposalUnreachable`, as above;
- *ask me first*: `run` raises `GateOffline`, whose message tells the person to
  run `bindable-gate offline accept`; after that, as below. If nobody accepts
  within 10 minutes of the outage's start, `run` keeps raising `GateOffline`,
  with the message saying the action was stopped because nobody accepted in
  time, until Bindable is back;
- *switch automatically*: your function runs, not reviewed, and `run` returns an
  `Outcome` with `outcome == "offline"`, `offline_record` set and no Receipt.
  `classify()` answers it `ran_offline`: it ran, so it is not safe to retry.

A retry with the identifiers of a Proposal already made always asks the review
again.

Use one Gate credential per machine: Bindable keeps one offline record chain
for each Gate and agent, so a credential shared by two machines would have
the second machine's offline records refused.

## Reading an error: did my action run?

Every error the library raises means the action did not run, except
`TrackpointNotRecorded`, which means it ran and the ledger does not know. That
difference is the only thing you need from this table. The three `Evidence` rows
are about `submit_evidence`, which gates nothing. `EvidenceValueRefused` and
`EvidenceRefused` mean no Evidence was recorded. `EvidenceUnreachable` means the
library does not know: the submission may have been recorded and only its answer
lost, so `classify()` reports `recorded` as unknown (`None`), never `False`, and
still `safe_to_retry`, because a retry under the same key is answered with what
the first attempt did.

| Error | Already ran? | What happened |
|---|---|---|
| `GateBlocked` | **no** | A Receipt was obtained and the verdict was not `passed`. The Gate working, not failing. |
| `ProposalUnreachable` | no | No Receipt could be obtained. Carries the identifiers to retry with. |
| `GateOffline` | no | Bindable is unreachable and the agent's offline setting is ask me first. Its message tells the person to run `bindable-gate offline accept`. A kind of `ProposalUnreachable`, carrying the same identifiers. |
| `ProposalRefused` | no | The platform answered the Proposal and refused it, with a code. |
| `GateUnreachable` | no | The base of the two above. The library never raises it directly; catch it to mean "no Receipt, so nothing ran". |
| `GateConfigError` | no | The Gate could not be constructed. Raised before any call. |
| `TrackpointNotRecorded` | **yes** | The action ran and its outcome could not be reported. Carries the action's result rather than losing it. |
| `EvidenceValueRefused` | no | The Evidence value holds a float, or something JSON cannot carry. It names where. Nothing was sent, and no idempotency key was spent. |
| `EvidenceRefused` | no | The platform answered an Evidence submission and refused it — `external_id_exists` among the reasons. |
| `EvidenceUnreachable` | no | An Evidence submission got no answer, so whether it was recorded is unknown. Carries the idempotency key to retry with, which is safe. |

All of them are `GateError`, so one `except GateError` covers every ending.

Three more errors exist in this package and the library cannot raise any of them.
They are listed so that nobody writes a handler for one and waits for it:

| Error | Already ran? | Why it is not yours |
|---|---|---|
| `EvidenceNotRecorded` | no | MCP Gate only; the library does not raise it. Recording Evidence before a Proposal is the MCP Gate's path. |
| `ReceiptNotStored` | no | MCP Gate only; the library does not raise it. Writing Receipts to a local folder is the MCP Gate's; the library takes no directory and writes none. |
| `TrackpointContradiction` | **yes** | MCP Gate only; the library does not raise it. Where the MCP Gate does, the action had already run. |

## Environment variables

**The library reads none.** Everything it needs is a constructor argument, and a
test holds that its code touches the environment nowhere.

If you have used the MCP Gate, these are its variables and the library **ignores**
every one of them:

| Variable | Status for the library |
|---|---|
| `BINDABLE_GATE_CREDENTIAL` | Ignored. Pass `credential` to the constructor instead. |
| `BINDABLE_API_BASE_URL` | Ignored. Pass `base_url` instead. |
| `BINDABLE_AGENT_ID` | Ignored. Pass `agent_id` per call or per decorator. |
| `BINDABLE_REVIEW_CONTEXT` | Ignored. Pass `review_context` instead. |

Setting one changes nothing here. That is stated rather than left quiet, because a
variable that is read in one form and silently ignored in another is how someone
comes to believe they have configured something they have not.

## What this package does not do

It holds no rules, evaluates nothing, and contains no cryptography. The hosted
Action Review returns the verdict and mints the signed Receipt; this enforces it
and records what happened.

To verify a Receipt yourself, use the platform's `POST /v1/verify`. Do not write a
local reimplementation: a second implementation of the digest is a second answer
to whether a record is authentic.
