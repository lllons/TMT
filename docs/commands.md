# Slash commands

At the prompt, a line that is **nothing but** a `/` command is answered by TMT itself
and is never sent to the model. Names are case-insensitive. Everything else is a task
and goes to the model exactly as before — including a line that merely starts with a
path, such as `/usr/bin/python is broken`.

`/plan`, `/review` and `/verify` are the three that read both ways. Alone on the line
each is the read-only report below; with a task after it — `/plan Build the login
page` — the line is that task, with the capability turned on for it. See
[Capabilities](capabilities.md).

| Command | What it does |
|---|---|
| `/context` | the conversation so far: model, provider, workspace, how many turns are carried into the next request, estimated tokens in and out, lines added and removed, and the last few questions |
| `/config` | the settings a request runs under: model, provider, effort, streaming, JSON mode, workspace, and whether an API key is set |
| `/clear` | forget the conversation and start fresh. The model, effort, workspace and every other setting are kept, and no file is touched |
| `/effort` | show the current effort level |
| `/effort low\|medium\|high` | set it |
| `/model` | show the current model and the ones this provider offers |
| `/model <name>` | switch to one, by id or by the name shown in Settings |
| `/note <question>` | answer one question about the workspace, changing nothing |
| `/notes` | what TMT remembers about this project between sessions: where `TMT_Context/` is, what is in each file, and which notes name paths that no longer exist |
| `/agents` | what the background agents are doing |
| `/back` | step out to the startup menu, keeping the session. See below |
| `/plan` | the steps TMT is working through for this task, and what is left |
| `/verify` | what was actually run to check this task's work: every check, its command, its exit code, and the output of anything that failed |
| `/review` | what the independent review found: the verdict, every finding, and the history of this task's reviews |

## `/back` — the menu, without losing the session

`/back` puts the startup menu back on screen over a session that is still running.
Nothing is ended, cleared, cancelled or waited for: the conversation is still the
conversation, the plan is still the plan, and any background agents carry on working
behind it. Before this, Settings and Help were only reachable by quitting.

The menu it opens is the launch menu with three differences:

```
> Resume    Go back to the session, which is still here
  Settings  Provider, API key and the model TMT runs on
  Help      What TMT does, and how to drive it
  Exit      Close TMT and end the session
```

- **Start reads Resume**, and its label keeps moving through the gradient even while
  the cursor is somewhere else — that is the screen saying your session is still there.
- **Exit says it ends the session.** The word is the same as at launch; the
  consequence is not.
- **Settings is not offered while anything is still running.** The row is gone —
  not greyed, not disabled — and a line above the list says what is running and what
  to do:

```
Settings are not offered while work is running: 2 agents. Wait for it to
finish, then /back again.
```

The provider, the key and the model are all read while a request is in flight, so
changing one underneath a running agent lands a change nobody asked for in the middle
of a request that had already started. When the work finishes the row comes back on
the next frame, without closing the menu.

Choosing Resume clears the screen, draws the header again, and returns you to the
prompt with everything as you left it.

**Effort** is how much work TMT will spend on one task. It changes two things, and
only things that are real on every provider: how long a reply is asked for, and how
many rounds of the agent loop one question may take.

| Level | Reply length asked for | Rounds per task |
|---|---|---|
| `low` | 4096 tokens | 12 |
| `medium` (default) | 4096 tokens | 35 |
| `high` | 8192 tokens | 60 |

The reply length does not go below 4096 at any level. Every reply is one JSON object
and the ones that matter carry a whole file inside it, so a smaller limit does not
make the model terser — it cuts the object off mid-string and the write never
happens. The setting is stored in `.tmt_effort` beside the installation and survives
a restart, like the model choice.

**Completion.** In a real terminal, typing `/` lists the ten commands under the
line you are typing, and it narrows as you go: `/mo` leaves `/model`. Tab completes
as far as the candidates agree — `/mo` becomes `/model `, `/co` becomes `/con`,
because `/context` and `/config` still both apply. A piped or redirected run reads a
whole line and draws no list; the commands themselves still work there.

**Secrets.** Neither `/context` nor `/config` ever prints a key, a token or a
password. `/config` says whether a key is set and nothing else about it — not the
value, and not a masked form of it either.

---

[← Back to the README](../README.md)
