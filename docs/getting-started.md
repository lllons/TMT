# Getting started

## First launch

The order is: the launch screen, then the menu — **Start, Settings, Help, Exit** — and a
question about credentials only if you choose Start without one. Nothing is asked for
before you have seen the program, and choosing Exit asks for nothing at all.

Press Start with no key and TMT asks for an [OpenRouter key](https://openrouter.ai/keys)
there and then; press Esc at that form and you are back on the menu with nothing
started. You can equally set it up first in **Settings → AI Provider / API Key**, which
is the same two screens reached deliberately rather than as a gate.

Either way the key is saved to `.tmt_providers.json` in the installation directory
(git-ignored), obfuscated rather than encrypted — see [Putting a key in by
hand](api-keys.md#putting-a-key-in-by-hand) below. Set `OPENROUTER_API_KEY` to skip the question
entirely. It is asked for once for the install, not once per project.

Type a task at the `Task>` prompt. `quit` or `exit` to leave. Ctrl-C cancels the
current task without closing TMT.

## The tip under the header

A session opens on the wordmark, the date, the directory it may write to — and one
line of something TMT can do:

```
   Wed 02 Sep 2026
   C:\Coding\myproject
   Tip · /note answers a question about this project without changing it
```

There are more than thirty of them and the next one is shown each time that screen is
reached: a launch, or coming back to a session with `/back`. Which one comes next is
kept in `.tmt_tip` in the installation directory (git-ignored), so the list moves on
between launches instead of restarting at the top, and it is the same list in every
project. A terminal too narrow for the sentence shows the command on its own; one with
no room for even that shows no tip, and nothing else on the header moves to make space.

Every tip names something TMT actually does. Most of them are the slash commands
below, which are otherwise invisible until somebody mentions them.

Files under 8 KB are shown to the model automatically, up to a fixed number and total
size; the listing says so when it stops early. Larger files are read on demand.

## What you can ask for

Plain English. TMT picks the actions itself.

```
Task> write a python script that fetches a URL and prints the status code
Task> what does report.py do?
Task> find every TODO in src and list them
Task> change the timeout in net.py from 5 to 30 seconds
Task> run hello.py
Task> run the test suite and tell me what failed
Task> open notes.txt in notepad
Task> commit the changes and push to main
```

---

[← Back to the README](../README.md)
