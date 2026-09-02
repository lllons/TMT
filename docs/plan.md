# The plan

**Ask for it with `/plan` in your prompt.** Without that command TMT does not write
a plan and nothing here gates the answer. See
[Capabilities](capabilities.md).

With `/plan`, for anything substantial — add a feature, fix a bug across the repo, refactor a
subsystem, update the docs throughout a project — TMT writes a plan before it starts
and works through it in front of you. It appears as a column on the right of the live
area while it works, and it stays there, finished, beside the next prompt.

```
                                                        PLAN 2/5
                                                        ─────────────────────────
                                                        S1 ✓ Inspect repository
 09:14 · OpenRouter · MiniMax M3                        S2 ✓ Find and…erminology
 ───────────────────────────────────────────────────    S3 ● Update documentation
 > Describe your next task                              S4 ○ Run tests and verify
 ───────────────────────────────────────────────────    S5 ○ Explain changes
```

| Mark | Status | Colour | Means |
|---|---|---|---|
| `✓` | completed | green | the work for that step is actually done |
| `●` | in progress | orange | the one step being worked on now |
| `○` | pending | red | still to come |
| `!` | blocked | amber | it cannot proceed, and it still counts as unfinished |

Exactly one step is in progress at a time. Completing one promotes the next on its
own. Colour is confirmation, never the message: every status carries a mark as well,
and the whole column degrades to `+ > - !` and ASCII rules on a terminal that cannot
draw the rest.

**The plan is a contract, not a progress bar.** TMT is not permitted to finish a task
while a step is outstanding. A final answer sent with work left over is not shown to
you at all — the runtime refuses it, hands the model back the list of steps it still
owes, and the turn carries on. That is enforced by the program rather than asked for
in the prompt, so a model deciding it is finished does not make it finished:

```
Task> add the feature
 · Planning the work in two steps.
 ◆ Plan created with 2 steps.
 ▲ Plan not finished - 2 steps outstanding, next is S1 Implement the feature. Continuing.
 · The feature is in; running the tests next.
 ◆ S1 (Implement the feature) in_progress -> completed.
 · Suite is green.
 ◆ S2 (Run the tests) in_progress -> completed.
 ┌──────────────────────────────────────────────────────────────┐
 │ Added the feature and the suite is green: 12 tests, 0 failures.│
 └──────────────────────────────────────────────────────────────┘
```

The plan can be revised whenever the work turns out to be different from what was
expected — steps renamed, added, removed, or the whole plan replaced. Two things cannot
happen. A completed step is never reopened: a finished step stays finished, and a plan
whose shape was wrong is replaced outright rather than unwound. And a plan that has had
work done against it cannot be dropped — that was the one route round the gate, so
clearing is refused once any step is completed. Finishing it and reshaping it are both
visible on screen; quietly dropping it would not be.

**Not everything gets a plan.** "What does this function do?" is one answer, and a
plan for it would be noise on the screen and a gate on TMT's own reply. Plans are for
work with stages.

**The plan belongs to the task, not to the session.** It is retired the moment you ask
the next question, so an unfinished plan can never hold up an answer to something
unrelated. Nothing is written to disk. Background agents cannot see it or change it —
it is the main agent's contract with you, and a worker completing a step would let
TMT finish on work it had only claimed.

**On a terminal under 45 columns** the column is not drawn — the prompt box needs the
room more — and `/plan` prints the same thing as ordinary text at any width.

---

[← Back to the README](../README.md)
