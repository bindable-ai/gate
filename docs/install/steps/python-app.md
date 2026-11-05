# Install the Library Gate in your Python app

## What it is

A decorator for your own Python functions: each one runs only after Bindable reviews the call.

## Requirements

- macOS or Linux.
- Python 3.11 or later.
- Your agent registered in the Bindable Portal, and a Gate credential for it.
- A Contract for the agent. With no Contract, Bindable blocks every call.

## Install

Install the Gate into your project's Python environment.

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
pip install ./bindable_gate-0.4.1-py3-none-any.whl
```

### pip / npm (after the packages are published)

```bash
pip install bindable-gate
```

### Put the Gate around your functions

```python
import os
from bindable_gate import Gate

gate = Gate(
    base_url=os.environ["BINDABLE_API_BASE_URL"],
    credential=os.environ["BINDABLE_GATE_CREDENTIAL"],
    review_context="test",
)

AGENT = "did:webvh:QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX:bindable-ai.com:agent:3f2b8c1e-5d4a-4e7b-9c61-0a2f7d9e4b13"

@gate.control(agent_id=AGENT, action_type="send_invoice", payload_fields=["customer", "amount_cents"])
def send_invoice(customer: str, amount_cents: int) -> str:
    return billing.send(customer, amount_cents)
```

Only the arguments named in `payload_fields` are sent to Bindable. Never put passwords, keys or personal data in them. When a review does not pass, the function does not run and `GateBlocked` is raised.

## Settings

The Library Gate takes its settings from your code. Read them from the environment or your secret store, as above, and never write the credential into your code.

| Setting | Example | Where it goes |
| --- | --- | --- |
| API URL | `<your Bindable API URL>` | `base_url=` |
| Agent | `did:webvh:QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX:bindable-ai.com:agent:3f2b8c1e-5d4a-4e7b-9c61-0a2f7d9e4b13` | `agent_id=` on each `@gate.control` |
| Gate credential | `bnd_gt_…` | `credential=`, from the environment or your secret store |
| Agent key file | `~/keys/my-agent.pem` | `agent_key=` (optional; see below) |
| Key ID | `did:webvh:QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX:bindable-ai.com:agent:3f2b8c1e-5d4a-4e7b-9c61-0a2f7d9e4b13#7c9e6679-7425-40de-944b-e07fc1f90ae7` | `agent_key=` (with a key file) |
| Developer key | `bnd_sk_…` | `developer_key=` (optional; only to record Evidence; press Enter to skip if asked) |
| Review context | `test` | `review_context=` |

To sign every request with your agent key:

```python
from bindable_gate.contract.signing import load_agent_key

key = load_agent_key("~/keys/my-agent.pem", "did:webvh:QmT5NvUtoM5nWFfrQdVrFtvGfKFmG7AHE8P34isapyhCxX:bindable-ai.com:agent:3f2b8c1e-5d4a-4e7b-9c61-0a2f7d9e4b13#7c9e6679-7425-40de-944b-e07fc1f90ae7")
gate = Gate(..., agent_key=key)
```

- **API URL:** the Bindable API URL shown in the Portal.
- **Agent:** your agent's full did:webvh, as the Portal shows it. A Gate takes nothing else: not the Agent ID on its own, and not a `did:web:` name.
- **Gate credential:** a `bnd_gt_` token, shown once when you create the Gate in the Portal. Treat it like a password.
- **Agent key file and Key ID** (optional, together): a P-256 private key in PEM, and its Key ID: your agent's did:webvh, then `#`, then the key's id from the Portal. With a key, every request is signed; without one, the Gate says once that nothing is signed.
- **Developer key** (optional): a `bnd_sk_` key. The Gate does not need it. If you are asked for it, press Enter to skip.
- **Review context:** `test` while you try things out, `live` when you are ready.

## Check it works

1. Call one function you put the Gate around, with harmless values.
2. It runs if your Contract releases it; otherwise `GateBlocked` says why.
3. Open your agent's Track in the Bindable Portal: you should see this action in your Track.

## Uninstall

Remove the Gate lines from your code, then:

```bash
pip uninstall bindable-gate
```
