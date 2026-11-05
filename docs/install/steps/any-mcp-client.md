# Install the Local MCP Gate for any MCP client

## What it is

A small program your MCP client starts in place of a tool server; it holds each tool call for Bindable's review, then passes it on.

## Requirements

- macOS or Linux.
- Python 3.11 or later.
- An MCP client that starts local tool servers, such as Claude Desktop.
- Your agent registered in the Bindable Portal, and a Gate credential for it.
- A Contract for the agent. With no Contract, Bindable blocks every call.

## Install

Install the Gate in its own Python environment.

### Download

Download `bindable_gate-0.4.1-py3-none-any.whl` from the Gate's GitHub release (https://github.com/bindable-ai/gate/releases/download/v0.4.1/bindable_gate-0.4.1-py3-none-any.whl). Check its SHA-256 before you install it. On macOS:

```bash
shasum -a 256 bindable_gate-0.4.1-py3-none-any.whl
```

On Linux, use `sha256sum bindable_gate-0.4.1-py3-none-any.whl`. Either should print:

```text
fd85178172e12400a219cce2e5a1d980330b4cf1776b6a94abcadb1f9e0472cd
```

If it prints anything else, do not install the file.

```bash
python3 -m venv ~/.bindable-gate
~/.bindable-gate/bin/pip install ./bindable_gate-0.4.1-py3-none-any.whl
```

### pip / npm (after the packages are published)

```bash
python3 -m venv ~/.bindable-gate
~/.bindable-gate/bin/pip install bindable-gate
```

### Put the Gate in front of each tool server

In your MCP client's configuration, start the Gate in place of each tool server, and tell the Gate how to start the server. For example, a file server in Claude Desktop's `claude_desktop_config.json` (use your own home folder in `command`):

```json
{
  "mcpServers": {
    "files": {
      "command": "/Users/you/.bindable-gate/bin/bindable-gate",
      "env": {
        "BINDABLE_API_BASE_URL": "<your Bindable API URL>",
        "BINDABLE_AGENT_ID": "did:webvh:QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX:bindable-ai.com:agent:3f2b8c1e-5d4a-4e7b-9c61-0a2f7d9e4b13",
        "BINDABLE_GATE_CREDENTIAL": "<your Gate credential>",
        "BINDABLE_REVIEW_CONTEXT": "test",
        "BINDABLE_DOWNSTREAM_COMMAND": "npx",
        "BINDABLE_DOWNSTREAM_ARGS": "-y @modelcontextprotocol/server-filesystem /Users/you/Documents"
      }
    }
  }
}
```

The tool server never sees any `BINDABLE_` setting. Every other message passes through unchanged; only tool calls are held for review.

**Coming soon: the Hosted MCP Gate.** Bindable will run it for you: you add it to your MCP client as a remote server, and there is nothing to install.

## Settings

The settings go in the `env` of each server entry. That file now holds your Gate credential in plain text, so keep it readable by you only.

| Setting | Example | Where it goes |
| --- | --- | --- |
| API URL | `<your Bindable API URL>` | `BINDABLE_API_BASE_URL` |
| Agent | `did:webvh:QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX:bindable-ai.com:agent:3f2b8c1e-5d4a-4e7b-9c61-0a2f7d9e4b13` | `BINDABLE_AGENT_ID` |
| Gate credential | `bnd_gt_…` | `BINDABLE_GATE_CREDENTIAL` |
| Agent key file | `/Users/you/keys/my-agent.pem` | `BINDABLE_AGENT_KEY_FILE` (optional) |
| Key ID | `did:webvh:QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX:bindable-ai.com:agent:3f2b8c1e-5d4a-4e7b-9c61-0a2f7d9e4b13#7c9e6679-7425-40de-944b-e07fc1f90ae7` | `BINDABLE_AGENT_KEY_ID` (with a key file) |
| Developer key | `bnd_sk_…` | not used; press Enter to skip if asked |
| Review context | `test` | `BINDABLE_REVIEW_CONTEXT` (required) |
| Tool server | `npx` | `BINDABLE_DOWNSTREAM_COMMAND` |
| Its arguments | `-y @modelcontextprotocol/server-filesystem …` | `BINDABLE_DOWNSTREAM_ARGS` |

- **API URL:** the Bindable API URL shown in the Portal.
- **Agent:** your agent's full did:webvh, as the Portal shows it. A Gate takes nothing else: not the Agent ID on its own, and not a `did:web:` name.
- **Gate credential:** a `bnd_gt_` token, shown once when you create the Gate in the Portal. Treat it like a password.
- **Agent key file and Key ID** (optional, together): a P-256 private key in PEM, and its Key ID: your agent's did:webvh, then `#`, then the key's id from the Portal. With a key, every request is signed; without one, the Gate says once that nothing is signed.
- **Developer key** (optional): a `bnd_sk_` key. The Gate does not need it. If you are asked for it, press Enter to skip.
- **Review context:** `test` while you try things out, `live` when you are ready.

## Check it works

1. Restart your MCP client. The Gate writes one line to the client's server log starting `bindable-gate: connected;`, naming your agent and saying whether requests are signed.
2. Ask the agent to use one of the server's tools, such as listing a folder.
3. The call reaches the server only if your Contract releases it. If it is blocked, the client shows Bindable's reason.
4. Open your agent's Track in the Bindable Portal: you should see this action in your Track.

## Uninstall

Put each server entry back as it was (its own `command` and arguments, no `BINDABLE_` settings), restart your MCP client, then:

```bash
rm -rf ~/.bindable-gate
```
