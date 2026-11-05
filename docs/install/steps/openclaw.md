# Install the Runtime Plugin Gate for OpenClaw and NemoClaw

## What it is

An OpenClaw plugin that sends every tool call the agent makes, built-in or MCP, to Bindable for review before it runs.

## Requirements

- macOS or Linux.
- OpenClaw, or a NemoClaw sandbox running OpenClaw.
- Your agent registered in the Bindable Portal, and a Gate credential for it.
- A Contract for the agent. With no Contract, Bindable blocks every call.

## Install

**OpenClaw on your own machine:**

### Download

Download `bindable-ai-gate-openclaw-0.1.0.tgz` from the Gate's GitHub release (https://github.com/bindable-ai/gate/releases/download/v0.4.1/bindable-ai-gate-openclaw-0.1.0.tgz). Check its SHA-256 before you install it. On macOS:

```bash
shasum -a 256 bindable-ai-gate-openclaw-0.1.0.tgz
```

On Linux, use `sha256sum bindable-ai-gate-openclaw-0.1.0.tgz`. Either should print:

```text
6cbc2e8539c62d01c129eca08bfa587cb23e8f0cd924adaa67fe90b9b462deb7
```

If it prints anything else, do not install the file.

```bash
openclaw plugins install ./bindable-ai-gate-openclaw-0.1.0.tgz
openclaw plugins enable bindable-gate
```

### pip / npm (after the packages are published)

```bash
openclaw plugins install @bindable-ai/gate-openclaw
openclaw plugins enable bindable-gate
```

### A NemoClaw sandbox

Download the file as in the Download tab, then run these on the host (the sandbox here is named `my-assistant`):

```bash
nemoclaw my-assistant upload ./bindable-ai-gate-openclaw-0.1.0.tgz /sandbox/bindable-ai-gate-openclaw-0.1.0.tgz
nemoclaw my-assistant exec --no-stdin --no-tty -- openclaw plugins install /sandbox/bindable-ai-gate-openclaw-0.1.0.tgz --force --accept-capabilities
nemoclaw my-assistant exec --no-stdin --no-tty -- openclaw plugins enable bindable-gate --accept-capabilities
```

The file holds everything the plugin runs, so the install needs no network. Inside a NemoClaw sandbox the install cannot ask questions, so `--force` and `--accept-capabilities` answer them. The plugin uses three OpenClaw hooks and nothing else: `before_tool_call`, `after_tool_call` and `gateway_stop`.

**NemoClaw network policy:** the plugin runs in OpenClaw's Node process, so the sandbox's network policy must let Node reach your Bindable API URL. If it cannot, every call is blocked with `decision unavailable`.

Keep the agent's built-in tools on. The plugin reviews them, so you need no tool restrictions.

## Settings

Put the settings in OpenClaw's `.env` file: `~/.openclaw/.env`, or `/sandbox/.openclaw/.env` in a NemoClaw sandbox. Make it readable by you only (`chmod 600`), because it holds the Gate credential in plain text.

```bash
BINDABLE_API_BASE_URL=<your Bindable API URL>
BINDABLE_AGENT_ID=did:webvh:QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX:bindable-ai.com:agent:3f2b8c1e-5d4a-4e7b-9c61-0a2f7d9e4b13
BINDABLE_GATE_CREDENTIAL=<your Gate credential>
BINDABLE_REVIEW_CONTEXT=test
BINDABLE_AGENT_KEY_FILE=/sandbox/.openclaw/bindable-agent-key.pem
BINDABLE_AGENT_KEY_ID=did:webvh:QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX:bindable-ai.com:agent:3f2b8c1e-5d4a-4e7b-9c61-0a2f7d9e4b13#7c9e6679-7425-40de-944b-e07fc1f90ae7
```

Leave out the last two lines if you do not sign requests. A key file must be readable by you only (`chmod 600`). If a setting is wrong, the plugin blocks every call and says which setting once at startup.

| Setting | Example | Where it goes |
| --- | --- | --- |
| API URL | `<your Bindable API URL>` | `BINDABLE_API_BASE_URL` |
| Agent | `did:webvh:QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX:bindable-ai.com:agent:3f2b8c1e-5d4a-4e7b-9c61-0a2f7d9e4b13` | `BINDABLE_AGENT_ID` |
| Gate credential | `bnd_gt_…` | `BINDABLE_GATE_CREDENTIAL` |
| Agent key file | `/sandbox/.openclaw/bindable-agent-key.pem` | `BINDABLE_AGENT_KEY_FILE` (optional) |
| Key ID | `did:webvh:QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX:bindable-ai.com:agent:3f2b8c1e-5d4a-4e7b-9c61-0a2f7d9e4b13#7c9e6679-7425-40de-944b-e07fc1f90ae7` | `BINDABLE_AGENT_KEY_ID` (with a key file) |
| Developer key | `bnd_sk_…` | not used; press Enter to skip if asked |
| Review context | `test` | `BINDABLE_REVIEW_CONTEXT` (`test` unless you set it) |

- **API URL:** the Bindable API URL shown in the Portal.
- **Agent:** your agent's full did:webvh, as the Portal shows it. A Gate takes nothing else: not the Agent ID on its own, and not a `did:web:` name.
- **Gate credential:** a `bnd_gt_` token, shown once when you create the Gate in the Portal. Treat it like a password.
- **Agent key file and Key ID** (optional, together): a P-256 private key in PEM, and its Key ID: your agent's did:webvh, then `#`, then the key's id from the Portal. With a key, every request is signed; without one, the Gate says once that nothing is signed.
- **Developer key** (optional): a `bnd_sk_` key. The Gate does not need it. If you are asked for it, press Enter to skip.
- **Review context:** `test` while you try things out, `live` when you are ready.

## Check it works

1. Restart OpenClaw so it reads the settings: `openclaw gateway restart`, or `nemoclaw my-assistant gateway restart` for a sandbox.
2. OpenClaw's log has one line starting `Bindable Gate: connected;`. It names your agent and ends by saying whether requests are signed. If it says `configuration invalid`, it names the setting to fix.
3. Ask the agent to run `ls`. The call runs if your Contract releases it.
4. Open your agent's Track in the Bindable Portal: you should see this action in your Track.

## Uninstall

```bash
openclaw plugins disable bindable-gate
openclaw gateway restart
```

In a NemoClaw sandbox, run the first line through `nemoclaw my-assistant exec --no-stdin --no-tty --`, and restart with `nemoclaw my-assistant gateway restart`. Then delete the `BINDABLE_` lines from the `.env` file, and the key file if you added one.
