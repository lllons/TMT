# Talking to you: `send_message` and `end_conversation`

TMT has exactly two ways of putting words on your screen, and the whole
difference between them is whether the task carries on afterwards.

| Action | Shows you text | Ends the task |
|---|---|---|
| `send_message` | yes | **no** — control returns to the agent, every time |
| `end_conversation` | yes | **yes** — and it is the only action that ends one |

**`send_message` is for saying things on the way.** "I'll read the parser
first", "the tests are green, so the docs are next", "this file is larger than
I expected." It is printed into the session where you can scroll back to it,
and then TMT carries on from exactly where it was. It can be used as many times
in one task as it is worth using; there is no cap and nothing about it is
final.

**`end_conversation` is the ending.** Its message is the summary you are left
looking at, which is why the agent is told that work not described there might
as well not have happened. There is no second way to stop: no separate `done`,
and no flag on a message that quietly turns it into the last one. TMT finishes
a task with this action or it does not finish it.

**Wanting to end it is not the same as being allowed to.** `end_conversation`
is what the completion gates hold, and any capability you turned on for that
prompt can refuse it: a plan with steps still outstanding, a review that has not
passed, verification that has not run or that found a failing check. A refusal
is not an error and it does not end the turn — TMT is handed the reason, goes
back to work, and the answer is still unsaid. See
[Capabilities](capabilities.md).

Background agents have neither channel in any useful sense: nobody is reading
them, so a message costs a step and reaches no one, and the ending is a report
to the main agent instead. See [Background agents](background-agents.md).

---

[← Back to the README](../README.md)
