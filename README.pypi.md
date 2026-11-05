# bindable-gate

A Bindable Gate sits between your agent and its tools. Before a tool call runs,
the Gate sends it to Bindable for review, and the call runs only if Bindable
releases it. Afterwards the Gate reports what happened, and the report lands on
your agent's Track.

If the Gate cannot get a decision, the call does not run.

## Install

```bash
pip install bindable-gate
```

Python 3.11 or later. This one package carries these Gates:

| Gate | For | Install |
| --- | --- | --- |
| Agent Hook Gate | Claude Code, Codex CLI, Cursor, Gemini CLI, GitHub Copilot CLI, Windsurf. macOS only. | `pip install bindable-gate`, then `bindable-gate install <agent> ...` |
| Framework Gate | LangGraph and other agent frameworks | `pip install "bindable-gate[langgraph]"`, one extra per framework |
| Library Gate | Your own Python code | `pip install bindable-gate` |
| Local MCP Gate | Any MCP client that starts local tool servers | `pip install bindable-gate`, then run your tool server through `bindable-gate` |

For OpenClaw, use the npm package `@bindable-ai/gate-openclaw`.

## What you need

- Your agent's DID, which starts `did:webvh:`. You get it when you register
  your agent with Bindable.
- A Gate credential, shown once when you register a Gate for the agent.
- The Bindable API base URL you were given.

## Documentation

Step-by-step pages for each agent and framework, with a check at the end of
each one, are under "Install a Bindable Gate" on the Bindable documentation
site: https://docs.bindable-ai.com

## Licence

Apache-2.0.
