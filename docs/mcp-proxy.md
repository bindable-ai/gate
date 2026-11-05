# The Bindable Action Gate as an MCP proxy

Put a Decision in front of every tool call an agent makes, without changing
the agent's code.

The Gate runs as a process between an MCP client and one MCP tool server. It
holds every `tools/call`, submits it to the hosted Action Review as a
Proposal, and forwards it to your tool server only when the Verdict releases
it. A blocked call never reaches the tool server at all. Either way you get a
signed Receipt.

Works with any MCP client: Claude Desktop, an agent framework, or your own.

## Two things to know first

**Fail-closed.** If the Action Review answers with something the Gate cannot
use — an unreadable answer, a refusal, no Receipt — the tool call **does not
run**. If Bindable cannot be reached at all (no answer, a timeout or a 5xx),
your agent's offline setting decides: switch automatically, ask me first (the
default), or stop the agent. See [offline-mode.md](offline-mode.md); use one
Gate credential per machine, so each machine's offline records reach the
Track. A Gate
that let actions through when it could not check them, without saying so and
without a record, would be worse than no Gate.

**The Gate only sees tools reached through MCP.** It gates `tools/call`
traffic that passes through it, and nothing else. A tool your framework
declares natively runs inside your own process and is never proposed, never
reviewed, and never gated. Same for a second MCP server your client connects
to directly. If an action must be governed, it has to sit behind a tool server
this Gate fronts. The practical check: for every tool your agent can call, ask
whether the call travels through `python -m bindable_gate`. If not, it is
ungoverned.

## Install

```bash
pip install "git+https://github.com/bindable-ai/gate"
```

Python 3.11 or newer.

## Your key

Export it; do not paste it into a config file.

```bash
export BINDABLE_GATE_CREDENTIAL="bnd_gt_..."
```

Treat it like a password. If a credential leaks, rotate it in the Portal or with the API, and email security@bindable-ai.com. Whether your MCP client can read `${BINDABLE_GATE_CREDENTIAL}`
from a config file depends on the client; check its own documentation.

## Environment variables

The proxy is configured entirely by environment. It reads nine variables.

| Variable | Required | What it does |
| --- | --- | --- |
| `BINDABLE_GATE_CREDENTIAL` | to connect | The Gate's own credential, a `bnd_gt_` token. Without it the Gate is standalone: it forwards every call, records it on the local Track (`~/.bindable/local-track.jsonl`) and reviews nothing. It says so once on stderr at startup. |
| `BINDABLE_API_BASE_URL` | to connect | The Bindable API URL shown in the Portal, where Proposals are submitted. |
| `BINDABLE_AGENT_ID` | to connect | The bound agent every Proposal is attributed to, by its DID: `did:webvh:<SCID>:<host>:agent:<Agent ID>`. Nothing else is accepted. |
| `BINDABLE_DOWNSTREAM_COMMAND` | **yes** | The command that starts your MCP tool server. The Gate launches it. |
| `BINDABLE_DOWNSTREAM_ARGS` | no | Arguments for that command, split shell-style. |
| `BINDABLE_REVIEW_CONTEXT` | to connect | `test` or `live`. The context every Proposal from this Gate is reviewed in, set once here. There is no default: which context your calls are reviewed in is not something the Gate should guess. |
| `BINDABLE_TRACKPOINTS_ENABLED` | no | Default `true`. Set `false`, `0`, `no`, or `off` to stop reporting Outcomes. Enforcement is identical either way. |
| `BINDABLE_AGENT_KEY_FILE` | no | A PEM ECDSA P-256 private key. The Gate signs every Proposal and every local Track line with it. Without it nothing is signed, and the startup line says so. A key that cannot be used stops the Gate at startup, naming this variable. |
| `BINDABLE_AGENT_KEY_ID` | with a key | The key's id, `<agent DID>#<key uuid>`. Required with `BINDABLE_AGENT_KEY_FILE`; there is no default. |
| `BINDABLE_OFFLINE_DIR` | no | Where offline mode keeps each agent's offline state and records. Default `~/.bindable/offline`. See [offline-mode.md](offline-mode.md). |

**Values are read verbatim.** A trailing `# sandbox key` comment or wrapping
quotes become part of the value.

**Your tool server never sees these.** The Gate starts the tool server with its
own environment minus every `BINDABLE_*` variable, so a server that can run
commands or read its environment cannot hand the Gate credential to the agent.
Everything else, such as `PATH`, `HOME` and the server's own settings, passes
through unchanged.

### `BINDABLE_REVIEW_CONTEXT` deserves a paragraph

A review context is not a stage. It says whether an Action Review is a **Test** or
a **Live** one, and the Gate carries exactly one for its whole life: it is read
once at startup and every Proposal the Gate makes goes out in that context.

There is no default. A Gate started without it refuses to start, naming the
variable, rather than guessing which context your calls should be reviewed in.

Two contexts means two Gates. That is deliberate — a process that could switch
context mid-session could report a Live enforcement as a Test one.

## Configuration shape

The Gate is launched as a command with an environment. Every MCP client
expresses that differently, but it is always these three things: the command
`python`, the arguments `-m bindable_gate`, and the environment above.

```bash
export BINDABLE_GATE_CREDENTIAL="bnd_gt_..."
export BINDABLE_API_BASE_URL="<your Bindable API URL>"
export BINDABLE_AGENT_ID="did:webvh:..."
export BINDABLE_DOWNSTREAM_COMMAND="npx"
export BINDABLE_DOWNSTREAM_ARGS="-y @modelcontextprotocol/server-filesystem /Users/you/Documents"

python -m bindable_gate
```

Run that way it speaks MCP over stdin and stdout, which is what an MCP client
connects to. You will normally not run it by hand — your client launches it.

One important detail if your client passes an environment explicitly: many MCP
clients do **not** hand the child process your whole shell environment. They
pass a small fixed list plus whatever the config declares. So a variable you
exported in your shell may not arrive. Declare every `BINDABLE_*` variable in
the client's own config rather than relying on your shell.

## What happens at startup when something is wrong

Every configuration problem stops the Gate before it serves a single call, and
the message names what is wrong. Nothing starts half-configured. The one
exception is deliberate: with no `BINDABLE_GATE_CREDENTIAL` at all, the Gate
starts standalone, records locally, and says so on stderr.

**Missing variables** — named, all at once, by name only. Your key value is
never echoed:

```
bindable-gate: missing required environment variable(s): BINDABLE_DOWNSTREAM_COMMAND
```

**A bad review context** — names the variable and every value that is
allowed:

```
bindable-gate: BINDABLE_REVIEW_CONTEXT must be one of test, live; a Gate proposes in one context and there is no default to guess
```

Note it is case-sensitive: `Live` is refused.

**An empty key** — an empty variable is treated as absent, so it reports as
missing rather than failing later with a confusing authentication error.

The process exits non-zero in every case, so a supervisor or MCP client sees a
real failure rather than a process that came up and did nothing.

## Confirming it works

You need a Bindable agent and a Contract governing the action you are about to
call. Then drive the Gate with any MCP client and make two calls — one your
Contract permits and one it refuses.

What a working Gate looks like:

- **The permitted call** returns your tool's normal result. A Receipt is
  minted with Verdict `passed`.
- **The refused call** returns an MCP error carrying `reason_code`,
  `receipt_id`, and `failed_rule`. A Receipt is minted with Verdict `failed`.
  **Your tool server never receives the call** — the surest confirmation is
  that whatever your tool logs or writes shows no sign of it.

Then verify either Receipt independently:

```bash
curl -s -X POST "$BINDABLE_API_BASE_URL/v1/verify" \
  -H "Authorization: Bearer $BINDABLE_GATE_CREDENTIAL" \
  -H "Content-Type: application/json" \
  -d @receipt.json
```

A valid Receipt answers `"valid": true` and `"signature_valid": true`. That
answer does not depend on trusting the Gate, your logs, or us — it is the
point of the Receipt.

To list Receipts your agent has accumulated:

```bash
curl -s "$BINDABLE_API_BASE_URL/v1/receipts?persistent_agent_id=$BINDABLE_AGENT_ID&limit=10" \
  -H "Authorization: Bearer $BINDABLE_GATE_CREDENTIAL"
```

## Limits worth knowing

- **stdio transport only.** The Gate fronts stdio MCP tool servers.
- **One tool server per Gate.** Front a second server with a second Gate.
- **The Gate adds no tools of its own.** `tools/list` passes through
  untouched, so your agent sees exactly the tools your server exposes.
- **A retried call is a new call.** If your client times out and retries, the
  Gate mints a fresh Proposal and the action can run twice. Set your client's
  tool-call timeout above your slowest gated tool, and switch retries off for
  actions with side effects.

## Related

- [The Python library](./python-library.md) — gate a function in your own code
- [Install the local MCP Gate](./install/steps/any-mcp-client.md) — the steps for any MCP client
- [Offline mode](./offline-mode.md) — what happens while Bindable is unreachable
