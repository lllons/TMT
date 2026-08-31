# The test suite

Every test in this project lives under `testing/`. Nothing named `test_*.py` belongs
beside the modules at the repository root any more — the root is for the program.

```
testing/
    unit/           16 files
    integration/     9 files
```

## Running it

From the repository root:

```bash
PYTHONIOENCODING=utf-8 python run_tests.py
```

`run_tests.py` stays at the root. It is the documented entry point, it is named in the
top-level `README.md` and in `agent_prompt.py`'s own examples, and it needs to sit
beside the modules it puts on `sys.path`. It walks `testing/` recursively, so a new
file in either directory is picked up with no registration step.

Set `PYTHONIOENCODING=utf-8` for any run that prints TMT output: the console is cp1252
on Windows through a pipe. `PYTHONDONTWRITEBYTECODE=1` as well for a mutation run — a
`.pyc` is validated on whole-second mtime plus size, and two edits within one second
make the interpreter load the previous one's bytecode.

The suite takes a couple of minutes. Most of that is real sleeping in the live-renderer
tests. TMT's own `run_file` gives up at ten seconds, so TMT cannot verify its own suite
and will correctly refuse to commit unverified work — run it yourself and tell it the
result in the task text.

There is no per-test timeout. A test that blocks on input hangs the whole run.

## What goes where

The rule is **how much of the program a test has to stand up to answer its question**.

`unit/` — one subsystem, exercised through its own module. A renderer measured by
composing a frame and reading it back, a state machine driven through its own
transitions, a prompt read as text. These are fast and they fail with the defect named.

`integration/` — more than one subsystem working together, and specifically any test
that stands up:

- **the session loop** (`TMT.main`, `drive_session`, a scripted turn end to end),
- **the worker loop** (a background agent, the reviewer, the note agent), or
- **a real subprocess** (a git process, a child interpreter).

`test_agent_git.py` is here for the third reason: it runs real `git` against real
throwaway repositories rather than simulating them. `test_agent_toolflow.py` drives
actions through `execute_action` rather than through their own functions, because that
is the only path the model can take — an action that works and is not registered is an
action that does not exist.

When a test could plausibly go either way, ask which failure it is meant to catch. If
it would still catch it with everything else stubbed out, it is a unit test.

## Two rules the runner depends on

**Stems must stay unique across both directories.** The runner imports each file by its
bare stem (`__import__(path.stem)`), so a `unit/test_agent_ui.py` and an
`integration/test_agent_ui.py` would be one module, and whichever was imported second
would be silently skipped.

**There are no `__init__.py` files under `testing/`, and there must not be.** Making
these directories packages would change every module's name to a dotted one, and the
seven cross-imports between test modules — `test_agent_cli` importing `Workspace` from
`test_agent_workspace`, `test_agent_plan` and `test_agent_review` importing
`drive_session` from `test_agent_cli`, and so on — all resolve by bare name. The runner
puts every directory holding a test file on `sys.path` instead, which is what makes
those imports work across `unit/` and `integration/` in both directions.

A corollary for anything that spawns a child interpreter: the child does not inherit
this. Put the test directories on its `PYTHONPATH` (the mutation scripts in `output/`
do exactly that), or point it at the repository root and import the modules under test
rather than the tests.

## Deriving paths

**Never use `Path(__file__).resolve().parent` to mean the project root.** It meant that
when these files sat at the root; it now means the test directory. Derive the root from
a module under test instead, which is the pattern the suite already uses:

```python
INSTALL_DIR = Path(agent_config.__file__).resolve().parent
```

`agent_config` resolves its own identity files from its own `__file__`, so the
directory holding it *is* the root, and the derivation stays correct wherever the test
file is moved to next. This is not a style preference: a root-scan written against
`__file__` does not fail after a move, it quietly finds nothing and passes. The
whole-repository UTF-8 and NUL-byte guard in `integration/test_agent_git.py` would have
gone vacuous exactly that way, so it now also asserts that it checked a non-zero number
of modules.

## Writing one

Plain `def test_*()` functions with asserts, at column zero, in a `test_*.py` file. No
classes, no fixtures, no framework — the runner just imports the module and calls them,
which also means pytest can collect them unchanged.

Two things worth knowing before adding one:

- **A `drive_session` script that ends in `quit` stops one line short of the next
  turn.** If what you are testing is state carried *between* turns, the script has to
  ask another ordinary question before it quits, or the code that starts a second turn
  never runs. A session-killing bug shipped green through exactly that gap.
- **Mutation-test a new test.** Break the code it covers, confirm the test fails,
  restore. Several tests in this repository passed for the wrong reason on the first
  try.
