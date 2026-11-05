# Bindable Gate

The Bindable Gate sends each action your agent wants to take to Bindable for
review before it runs. Bindable answers from the agent's Contract: the action
runs if it is released, and is held if it is blocked. Each one is recorded in
the agent's Track.

This repository holds the Gates that are released:

| Package | What it is |
| --- | --- |
| [`@bindable-ai/gate-openclaw`](packages/gate-openclaw) | The Runtime Plugin Gate for OpenClaw and NemoClaw: an OpenClaw plugin that reviews every tool call, built-in or MCP |
| [`@bindable-ai/gate`](packages/gate) | The TypeScript core the plugin runs on |
| [`bindable-gate`](src/bindable_gate) | The Python core: the `bindable-gate offline accept` command, the Library Gate and the local MCP Gate |

Licensed under the [Apache License 2.0](LICENSE).

## Install the Gate in OpenClaw or NemoClaw

You need:
- OpenClaw, or a NemoClaw sandbox running OpenClaw;
- your agent registered in the Bindable Portal, and a Gate credential for it;
- a Contract for the agent. With no Contract, Bindable blocks every call.

The full steps are in [docs/install/steps/openclaw.md](docs/install/steps/openclaw.md).
In short:

**1. Get the plugin.** Download `bindable-ai-gate-openclaw-0.1.0.tgz` from
this repository's [v0.4.1 release](https://github.com/bindable-ai/gate/releases/download/v0.4.1/bindable-ai-gate-openclaw-0.1.0.tgz)
and check its SHA-256 against `SHA256SUMS` on the release and the install page.
Or build it from this repository:

```bash
npm ci && npm run build
npm pack -w packages/gate-openclaw
```

**2. Install it.** On your own machine:

```bash
openclaw plugins install ./bindable-ai-gate-openclaw-0.1.0.tgz
openclaw plugins enable bindable-gate
```

In a NemoClaw sandbox (here named `my-assistant`), run these on the host:

```bash
nemoclaw my-assistant upload ./bindable-ai-gate-openclaw-0.1.0.tgz /sandbox/bindable-ai-gate-openclaw-0.1.0.tgz
nemoclaw my-assistant exec --no-stdin --no-tty -- openclaw plugins install /sandbox/bindable-ai-gate-openclaw-0.1.0.tgz --force --accept-capabilities
nemoclaw my-assistant exec --no-stdin --no-tty -- openclaw plugins enable bindable-gate --accept-capabilities
```

The sandbox's network policy must let OpenClaw's Node process reach your
Bindable API URL. If it cannot, every call is blocked.

**3. Set it up.** Put the settings in OpenClaw's `.env` file
(`~/.openclaw/.env`, or `/sandbox/.openclaw/.env` in a sandbox), readable by
you only:

```bash
BINDABLE_API_BASE_URL=<your Bindable API URL>
BINDABLE_AGENT_ID=<your agent's did:webvh, as the Portal shows it>
BINDABLE_GATE_CREDENTIAL=<your Gate credential>
BINDABLE_REVIEW_CONTEXT=test
```

In a NemoClaw sandbox, the Gate credential can stay outside the sandbox:
`BINDABLE_GATE_CREDENTIAL` can hold OpenShell's placeholder for it instead. The
plugin sends that value unchanged, only in the `Authorization` header of its
requests to Bindable, and never logs it.

**4. Check it.** Restart OpenClaw (`openclaw gateway restart`, or
`nemoclaw my-assistant gateway restart`). Its log has one line starting
`Bindable Gate: connected;`. Ask the agent to run `ls`, then open the agent's
Track in the Portal.

## Without Bindable

With no Gate credential, the Gate works on its own: every call runs and is
recorded in a local Track on the machine, a hash-chained file that
`bindable-gate track verify` checks. Nothing is reviewed or sent anywhere.

## When Bindable is unreachable

The Account's offline setting, chosen on the Account page, decides what happens:
*switch automatically*, *ask me first* (the default) or *stop the agent*. Actions
that run while Bindable is unreachable are kept as offline records and sent to
the Track when Bindable is back. See [docs/offline-mode.md](docs/offline-mode.md).

*Ask me first* is accepted with `bindable-gate offline accept`, from the
Python package, so install it beside OpenClaw (inside the sandbox, on NemoClaw):

```bash
pip install https://github.com/bindable-ai/gate/releases/download/v0.4.1/bindable_gate-0.4.1-py3-none-any.whl
```

Once the packages are published, `pip install bindable-gate` does the same.

## The Python core

- [The Library Gate](docs/install/steps/python-app.md): review a function of
  your own before it runs. [Reference](docs/python-library.md).
- [The local MCP Gate](docs/install/steps/any-mcp-client.md): put the review
  in front of any MCP tool server. [Reference](docs/mcp-proxy.md).

## Reporting problems

Open an issue: an **accountability issue** when the Gate let an action run
that it should have held, held one it should have let run, or recorded it
wrongly; a **bug fix** for anything else. For a security problem, see
[SECURITY.md](SECURITY.md). To contribute, see [CONTRIBUTING.md](CONTRIBUTING.md).
