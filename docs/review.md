# Independent review

**Ask for it with `/review` in your prompt.** Without that command no reviewer is
started and nothing here gates the answer. See
[Capabilities](capabilities.md).

Verification and review answer different questions, and a substantial change needs
both. Verification asks *does this pass executable checks*; review asks *is this the
right change, and is it safe*. A green suite says the code does what its tests say — it
does not say the tests are the right tests, and it does not notice that you built the
wrong feature.

TMT reviews its own work before it is allowed to say it is done — and not by asking
itself. A **separate agent** reads the repository, the diff and your original request,
without having written any of it, and reports what it found. The main agent has to act
on the blocking findings, and the runtime will not let it answer until a review has
actually passed.

The point is the failure a green test suite does not catch:

> You asked for authentication with refresh-token support. The tests pass. The
> reviewer reads the diff and finds that refresh tokens are never checked for expiry,
> and that `/health` has quietly moved behind authentication. Neither is tested,
> because the same agent wrote the code and the tests.

## The cycle

```
      your request
           |
      plan --> implement --> tests
           |
      independent review
           |
   +-------+--------+
   |                |
 PASS            FINDINGS
   |                |
   |          main agent fixes
   |                |
   |            tests again
   |                |
   +-------<---- review again
           |
     plan complete
           |
      final answer
```

## What the reviewer sees, and what it does not

It is given the **user's original request** — treated as the source of truth — the
plan the implementing agent wrote, the plan's completion state, `git status`,
`git diff`, `git diff --stat`, the current commit, the paths that were actually
written, and a note of what was actually executed in the session. Everything past
that it fetches itself: the changed files, the code that calls them, the tests, the
project's own conventions. The diff comes first and the rest is expanded into only
where the diff cannot answer the question.

It is **read-only**, enforced in code rather than asked for in its prompt: every
writing verb is refused before it is dispatched. It reports; the main agent makes
every change. It also cannot run anything, so it cannot review the result of its own
run — what the session executed is stated to it as an observed fact, and judging what
that proves is its job.

It is told, in as many words, not to trust the tests, not to trust the plan, and not
to trust the implementing agent's account of its own work.

## Findings

Every finding carries a severity, a file, a line where one was actually read, what the
evidence was, why it matters, and a direction for the fix.

| Severity | Blocks completion |
|---|---|
| `CRITICAL` | yes — correctness, security, data loss, destructive behaviour |
| `MAJOR` | yes — a real bug, missing required behaviour, a likely regression, an important missing test |
| `MINOR` | no — a smaller defect worth fixing |
| `SUGGESTION` | no — optional |

Alongside the findings the reviewer returns a **requirements checklist** built from
your request, each marked satisfied, partial or not satisfied. That is the part that
tells "does this code look good" apart from "did we build what was asked", and it is
the check that catches a clean implementation of the wrong feature.

The verdict is `PASS`, `PASS_WITH_WARNINGS` or `FAIL`. A reply claiming a pass while
listing a blocking finding is recorded as a **FAIL** — the findings decide, and the
reviewer's own claim is shown beside it so the contradiction is visible.

## What stops it being gamed

- **Only a real review can produce a pass.** There is no key on any action that sets a
  verdict. The only thing that moves review state is a reviewer agent's own output,
  parsed and validated field by field. A model writing "review passed" writes a
  sentence, and sentences do not move the state machine.
- **A review that did not complete is not a pass.** A reviewer that crashed, timed
  out or returned something unreadable leaves the task in `ERROR`, which blocks the
  final answer exactly as a failure does.
- **A passing review goes stale when the code moves under it.** Edit a file after a
  review passed and the next answer needs a fresh one — what passed is not what would
  ship.
- **The review step in the plan cannot be ticked off early.** Marking a step whose
  title names review as completed is refused while the review has not passed.
- **A reviewer cannot review its own work.** `review` is refused to every background
  agent, including the reviewer, and the verb is documented only in the main agent's
  prompt.
- **The reviewer cannot be started while workers are running.** They would be writing
  to the tree it is reading, and the review would be of a state that never existed.

## When it happens

A review is required when the runtime has seen **both** halves of substantial work: a
plan of three or more steps, and at least one file actually written. Neither alone
counts — a long plan that changed nothing was research, and a one-line patch with no
plan was a favour. Both are facts TMT observed rather than claims the model made, so
a model cannot describe its work as small to avoid one.

You override it in either direction with your own words. "…and review the changes"
turns one on; "no review needed" turns it off. Saying nothing leaves the decision to
the evidence, which is the usual case.

## Limits

There are **three review cycles per task**. If the third still reports blocking
issues, the answer is released rather than held forever — holding it further would
spend the turn and end with no answer at all. It goes out carrying a line saying
plainly that review did not pass and how many findings are open, and the main agent
is required to say the same in its own words. Silence would be the worse failure.

## On screen

The review sits under the plan in the same right-hand column, in three rows at most:

```
PLAN 2/5                       PLAN 5/5
S1 + Inspect repository        S1 + Inspect repository
S2 + Implement feature         S2 + Implement feature
S3 > Add tests                 S3 + Add tests
S4 - Independent review        S4 + Independent review
S5 - Final verification        S5 + Final verification

REVIEW 1/3                     REVIEW 2/3
> Running independent review   + Review passed
```

Every state carries a mark as well as a colour, so the column reads with the escapes
stripped and on a terminal with no colour at all. `/review` prints the findings in
full, and is the way in on a window too narrow for two columns.

---

[← Back to the README](../README.md)
