# Verification

**Ask for it with `/verify` in your prompt.** Without that command none of this
happens and nothing here gates the answer; the plain word `verify` is not enough.
See [Capabilities](capabilities.md).

With `/verify`, before TMT is allowed to say a piece of work is done it runs the
checks this repository actually has, and the runtime will not let it answer until
they pass.

The point is the distinction between evidence and an opinion:

> "This should work" is not verification. `43 passed, 0 failed` is.

TMT does not ask the model whether the code works. It reads the repository, works out
what this project tests and lints and builds itself with, reads the diff to see what
changed, picks the checks that are worth running for *that* change, runs them, and
reports the exit codes. Nothing the model writes can move that result — there is no
key on any action that sets a status, and a check passes when a process exits zero and
at no other time.

## What it decides to run

**It prefers your commands over its own guesses.** In order:

1. a command this repository defines by name — a `package.json` script, a `Makefile`
   target, a `run_tests.py` in the root
2. tooling this repository configures — `[tool.ruff]`, `[tool.mypy]`, `tsconfig.json`
3. the project's package manager — `npm`, `pnpm`, `yarn`, `bun`, `uv`, `poetry`
4. the standard command for the ecosystem — `cargo test`, `go vet`, `pytest`
5. a guess, labelled as one

If your `package.json` says `"test": "vitest run"`, TMT runs `npm run test`. It does
not decide that node projects use jest. If your repository has a `run_tests.py`, that
is the test command — even where `pytest` would also work, because running a different
thing from what you run and calling it your verification would be wrong even when it
passed.

CI configuration is read as *evidence* of which tools you really use, and never as a
source of commands. Nothing TMT runs is a string taken out of a project file: what is
taken is a name, and the command is built from a fixed table around it. There is no
shell anywhere on that path.

**It runs cheap checks before expensive ones**, and stops at the first that fails:

| Level | What |
|---|---|
| 1 | syntax and format of what changed |
| 2 | lint, type checking, compiler checks |
| 3 | the tests that name what you changed |
| 4 | the tests around them |
| 5 | the project's build |
| 6 | the whole suite |

Once the type checker has failed, the ten minutes the integration suite would take are
ten minutes spent measuring a tree already known to be wrong. Everything after a
failure is reported as skipped, with that as the reason — so what was *not* checked is
visible rather than implied.

**It goes deeper when the change is riskier.** Authentication, migrations, database
schema, API contracts, concurrency, filesystem boundaries, shell execution, dependency
or build configuration — or simply a lot of files at once — get the full suite. A
documentation-only change gets the static checks and no test run.

## The four outcomes, kept apart

| | Means |
|---|---|
| **PASSED** | the command ran and exited 0. The only kind of evidence there is |
| **FAILED** | the command ran and exited non-zero. Something is wrong, and the output says what |
| **SKIPPED** | it was not run — the tool is not installed, or an earlier check had already failed |
| **ERROR** | it could not run or did not finish. Nothing is known, and this is *not* a failure of your code |

They are never collapsed into a boolean. A timeout is not a failure; a missing linter
is not a passing lint. TMT will not install anything to close one of those holes —
a missing dependency is reported, never quietly fixed.

## What it looks like

```
VERIFY 1/3
✓ Syntax          passed
✓ Lint            passed
✗ Targeted tests  2 failed, 41 passed
– Full suite      not run: Targeted tests did not pass
```

`/verify` prints the whole run, including the output of anything that failed.

## Which tests it picks

For a project whose test command can take paths, TMT works out which tests the change
reaches and runs those first. A test file counts as **targeted** when there is evidence
for it — it names a changed module, imports a changed symbol, or sits where the
project's own naming convention says it should — and as **related** when it is a
reachability guess. The two are kept apart and levelled apart, because a guess
presented as a measurement is worse than no selection at all.

Where the project's test command *cannot* be narrowed to paths — `npm test`, or a
`run_tests.py` that runs everything — TMT says so and runs the whole suite as the test
evidence. It does not run everything and label it targeted.

## When it happens

Verification is required when the runtime has seen **both** halves of substantial work:
a plan of three or more steps, and at least one file actually written — the same two
facts a review is decided on, observed rather than claimed. A question, a read, or a
small patch with no plan is not gated at all.

You override it in either direction with your own words. "…and run the tests" turns it
on; "no verification needed" turns it off. Saying nothing leaves it to the evidence.

It also has a place in the plan. A plan step named for verification cannot be marked
completed while verification is outstanding — that refusal is in code, not in a prompt.
And the final answer needs all three: **the plan complete, verification passed, and the
review passed.** None of them excuses another.

## The cycle, and its limits

A failing check is feedback, not the end of the task: TMT reads the output, fixes what
it reports, and verifies again. At most three rounds — after that the answer is
released rather than held forever, carrying a line saying verification never passed.
Silence would be the worse failure.

**A pass goes stale the moment the code moves under it.** Editing anything after
verification passed means what passed is not what would ship, and the next answer is
held until it has been verified again. That is what makes the fix-and-verify loop close
rather than being a suggestion.

If a repository has nothing runnable in it at all — no test command, no linter, nothing
installed — verification says so and the answer is released, and TMT has to tell you
plainly that the work is unverified. "I could not verify this" is useful; "verified"
when nothing ran is not.

---

[← Back to the README](../README.md)
