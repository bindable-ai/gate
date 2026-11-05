# Offline mode: when Bindable is unreachable

Every Gate you run decides what happens to your agent's tool calls when
Bindable cannot be reached: the hooks, the OpenClaw plugin, the MCP proxy, the
Library Gate and the framework adapters. They all behave the same way.

## The setting

Your Account has one setting, chosen in Bindable:

| Setting | What happens while Bindable is unreachable |
|---|---|
| **Switch automatically** | The action runs, not reviewed. The Gate says once: *Bindable is unreachable. This agent's actions are not being reviewed.* |
| **Ask me first** | The action is blocked, with: *Bindable is unreachable. This agent's actions are not being reviewed. To continue anyway run: bindable-gate offline accept*. You have 10 minutes from when Bindable became unreachable. If you run it in time, the agent's actions run, not reviewed, until Bindable is back. If nobody runs it within 10 minutes, the agent stops, as under *stop the agent*: each action is blocked with *Bindable is unreachable and nobody ran bindable-gate offline accept within 10 minutes, so this action was stopped. This agent stays stopped until Bindable is reachable again.* |
| **Stop the agent** | The action is blocked, as every Gate did before offline mode. |

Each Gate reads the setting from Bindable with its own credential, every 15
minutes while Bindable is reachable, and keeps the last one on your computer,
so it knows what to do when Bindable cannot be asked. If it has never read
one, it uses **ask me first**.

## What counts as unreachable

No answer, a timeout, or a 5xx error from Bindable. Nothing else:

- a 4xx answer (a refused credential, a request Bindable could not take) is an
  answer, and the action is blocked as before, whatever the setting;
- a review that does not pass blocks the action, as always.

## `bindable-gate offline accept`

Run it on the computer where the agent runs. The command comes with the
`bindable-gate` Python package. A computer that runs only the OpenClaw plugin
needs it installed too (`pipx install bindable-gate`): the plugin ships no
command of its own, and the two Gates keep the same files, so the Python
command accepts for the plugin.

It accepts for that agent on that computer only, and only until Bindable is
back; the next outage asks again. It
records who accepted (your user name and the computer's name) and when, in
`accepts.jsonl` beside the agent's records, and every offline record that ran
because of it carries when you accepted and who (`user@host`), so the Track
shows both.

### The 10 minutes

Nothing runs without review unless a person accepts. The 10 minutes count
from the first action Bindable could not review in this outage:

- **Within 10 minutes**, each action is blocked with the ask line at once, and
  the agent may try again. Once you accept, its actions run, not reviewed.
- **After 10 minutes with nobody accepted**, the agent is stopped until
  Bindable is reachable again. Each action is blocked with the line saying
  nobody accepted in time, `bindable-gate offline accept` refuses with the
  same reason, and `bindable-gate offline status` shows the agent as stopped.
  When Bindable answers again, the agent's actions are reviewed as usual; the
  next outage starts a new 10 minutes.

The Gate does not hold an action open for the 10 minutes. Agent runtimes end
a hook that takes too long, and some (OpenClaw among them) then run the tool,
so holding it would let it run unreviewed.

```bash
bindable-gate offline accept                # the one agent waiting
bindable-gate offline accept --agent-id did:webvh:...   # when several are waiting
bindable-gate offline status                # each agent: reachable or not, its records
bindable-gate offline verify                # recompute an agent's offline records' chain
```

## What is kept while offline

Each action that runs while Bindable is unreachable becomes an **offline
record**: *not reviewed, ran while Bindable was unreachable*. It is kept on
your computer, one chain per Gate, in
`~/.bindable/offline/<agent>/records-<gate>.jsonl` (`BINDABLE_OFFLINE_DIR`
moves the folder).

**Use one Gate credential per computer.** Bindable keeps one chain for each
Gate and agent, and each computer writes its own. If the same Gate credential
serves the same agent on two computers, Bindable takes the first computer's
records and refuses the second's, which then stay on that computer. Give each
computer its own Gate.

An offline record carries:

- the tool's name, the SHA-256 of its arguments (never the arguments
  themselves), the SHA-256 of its result where the Gate saw one, and whether
  it failed;
- when it started and finished, and when Bindable became unreachable
  (`offline_since`);
- the setting that let it run (`allowed_by`: `automatic` or `ask_first`) and,
  under ask me first, when you accepted (`asked_at`) and who
  (`accepted_by`: your user name and the computer's, `user@host`, at most 100
  characters). Under switch automatically there is no `accepted_by`;
- its place in the chain: `previous_hash` is the record before's
  `chain_hash`, and `chain_hash` is SHA-256 over the record's RFC 8785
  canonical JSON. Changing, removing or reordering any record breaks every
  hash after it;
- the agent's signature, when `BINDABLE_AGENT_KEY_FILE` is set.

Your agent's Track shows offline records apart from its reviewed actions,
labelled *Unreviewed action (Bindable unreachable)*, with the outage's start
and end.

An offline record is not a Receipt. No Receipt is created for an action that
ran offline, and nothing the Gate returns looks like one: the Library Gate's
`Outcome` has `outcome == "offline"` and an empty `receipt`, and `classify()`
answers `ran_offline`.

## When Bindable is back

The Gate checks again, waiting longer each time it is still unreachable (5
seconds, then 15, 30, 60, 120, and every 5 minutes after that). When Bindable
answers, the Gate reviews every action again at once, sends the offline
records to your agent's Track (one offline period to a request), and says:

*Bindable is reachable and your agent's actions are being reviewed again. 3
offline records sent to the Track.* (With one record: *1 offline record sent
to the Track.*)

If Bindable does not take the records yet, the Gate keeps them, says so, and
sends them again later. A record is never deleted from your computer.

## Where the Gate says it

| Gate | Where its lines appear |
|---|---|
| Claude Code, Codex CLI, Gemini CLI hooks | As the runtime's system message, beside the call it let run; in the block's reason for a call it blocked |
| Cursor hook | As the allow's `user_message`; in the block's reason |
| GitHub Copilot CLI, Windsurf (Devin Local) hooks | On the hook's standard error, which the runtime may show only in its logs; in the block's reason |
| OpenClaw plugin | In the Gateway's log; in the block's reason |
| MCP proxy | On standard error; as the error's message for a call it blocked |
| Library Gate | On standard error, or through `notify=`; as `GateOffline`'s message |
| Framework adapters | On standard error and in the log; as what the model is told for a call it blocked |
