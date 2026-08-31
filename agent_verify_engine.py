"""Which checks are worth running for THIS change, and running them.

The middle of the three verification modules. `agent_verify_discovery` says
what this repository could run, `agent_verify` holds what came back, and this
decides what should be run and does it.

The whole of the design is one sentence: **maximum useful confidence for the
least execution.** Two things pull against each other there and both are real.
Running everything is safe and wastes minutes on a one-line change; running
one targeted test is fast and misses the thing next door that broke. What
resolves it is evidence rather than a rule of thumb -- the diff says what
changed, `agent_testsel` says which tests name it, and the risk of what
changed decides how far past that to go.

Four decisions here are load-bearing:

- **The change is read from git and from the runtime, and the two are added
  together.** git knows about edits made before this turn and about untracked
  files; the runtime knows exactly which paths TMT's own actions wrote. Either
  alone has a hole: a new file appears in no diff, and a file edited outside
  the session appears in no action.
- **A runner that cannot be narrowed says so and the whole suite becomes the
  test evidence.** Faking a targeted run by running everything and labelling
  it targeted would be a lie about what was checked; refusing to run tests at
  all because the project has no way to subset them would throw away the only
  test evidence available. So the report says which happened.
- **The first failure stops the run.** Section 22. Once the type checker has
  failed, the ten minutes the integration suite would take are ten minutes
  spent measuring a tree already known to be wrong. Everything after it is
  SKIPPED with that as the reason, so the report says what was not checked
  rather than implying it passed.
- **Nothing here can produce a pass.** Every status on every check comes from
  `VerificationCheck.record` over a real exit code. This module chooses,
  orders, runs and reports; it never decides.
"""

import re
from pathlib import Path

import agent_config
import agent_verify as V
import agent_verify_discovery as D
from agent_execution import command_available, run_command

# How many changed files make a change BROAD. Eight is a judgement rather than
# a measurement: below it a person can still hold every changed file in their
# head, and above it the odds that one of them touched something the others do
# not know about go up sharply. A broad change gets the full suite.
BROAD_CHANGE_FILES = 8

# Path fragments that make a change HIGH RISK, and every one of them is on the
# brief's list in section 9. Matched against the whole path, lowercased, so
# `src/auth/token.py` and `internal/authz.go` both count.
#
# It is deliberately a list of words rather than a cleverer classifier. A word
# list is wrong in both directions sometimes -- `author.py` matches "auth" --
# and being wrong here costs a longer verification, which is the cheap
# direction to be wrong in.
HIGH_RISK_TOKENS = (
    "auth", "login", "session", "credential", "token", "password", "secret",
    "crypto", "security", "permission", "privile", "acl",
    "migration", "migrate", "schema", "database", "/db/", "sql",
    "api", "route", "endpoint", "handler", "controller", "serializer",
    "thread", "concurren", "parallel", "lock", "mutex", "async", "queue",
    "subprocess", "shell", "exec", "eval", "sandbox", "path",
    "payment", "billing", "invoice",
)
# "model" and "order" were on this list and were taken off: they are the two
# that match ordinary filenames in projects that have nothing sensitive in
# them at all -- an ML checkpoint loader, a sort order -- and neither is named
# in the brief's own list of high-risk subjects. Everything still here is.

# Files whose CHANGE is itself high risk whatever is in them: dependencies,
# build configuration and packaging. A change here can break everything while
# touching no source at all, which is exactly the shape a targeted test run
# cannot see.
HIGH_RISK_FILES = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pipfile",
    "poetry.lock", "uv.lock", "package.json", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", "bun.lock", "tsconfig.json", "cargo.toml", "cargo.lock",
    "go.mod", "go.sum", "makefile", "cmakelists.txt", "dockerfile", "pom.xml",
    "build.gradle", "composer.json", "gemfile", "mix.exs",
)

# Extensions that are documentation. A change made only of these gets the
# static checks and no test run -- section 9's "documentation-only change".
DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc", ".rdoc", ".markdown")

# How many paths one command is given. A `py_compile` of four hundred files is
# a command line the OS will refuse on Windows, and a targeted test run of
# forty files is not targeted.
MAX_PATHS_PER_COMMAND = 40

# The default ceiling for an ordinary change: run the tests that name what
# changed and the ones around them, and stop there.
DEFAULT_CEILING = V.LEVEL_RELATED


class Selection:
    """What was chosen to run, and the reasons, before anything runs.

    Separated from the running so the choosing can be tested without a
    subprocess anywhere near it -- which is most of what section 38 asks for.
    """

    def __init__(self, checks=(), ceiling=DEFAULT_CEILING, reasons=(),
                 changed=(), targeted=(), related=(), discovery=None):
        self.checks = list(checks)
        self.ceiling = int(ceiling)
        self.reasons = tuple(reasons)
        self.changed = tuple(changed)
        self.targeted = tuple(targeted)
        self.related = tuple(related)
        self.discovery = discovery

    def describe(self):
        rows = ["Verifying up to level %d (%s)."
                % (self.ceiling, V.LEVEL_NAMES.get(self.ceiling, "?"))]
        rows.extend("  " + reason for reason in self.reasons)
        return "\n".join(rows)

    def __repr__(self):
        return "Selection(%d check(s), ceiling=%d)" % (len(self.checks), self.ceiling)


# --- what changed -----------------------------------------------------------


def git_changes(root=None):
    """(paths, note) -- every path git says has changed, plus untracked ones.

    Both halves matter and only together. The diff misses a file that has
    never been added, and `git status` is the only place a new module shows
    up; the status alone would not tell a rename from a pair of edits. A
    repository with no git at all comes back empty with a sentence saying so,
    because that is a perfectly verifiable repository and refusing it would be
    refusing the wrong thing.
    """
    del root
    try:
        import agent_git
    except Exception as error:
        return (), "git support is unavailable (%s), so the change could not be read" % error
    paths, note = [], ""
    try:
        repository = agent_git.TMTGit.discover()
    except Exception as error:
        text = str(error)
        if "not inside a git repository" in text or "no repository" in text:
            return (), ("this workspace is not a git repository, so there is no "
                        "diff to read; verification falls back to what TMT "
                        "itself wrote this turn")
        return (), "the repository could not be read (%s)" % text
    try:
        state = repository.status()
        for key in ("staged", "unstaged", "untracked"):
            for path in (state.get(key) or ()):
                cleaned = str(path).replace("\\", "/").strip()
                if cleaned and cleaned not in paths:
                    paths.append(cleaned)
    except Exception as error:
        note = "git status could not be read (%s)" % error
    return tuple(paths), note


def changed_paths(state=None, root=None):
    """(paths, notes) -- everything believed to have changed this task.

    git's view plus the runtime's. `VerificationState.changed_paths` is what
    TMT's own actions actually wrote, which is the half no repository state
    can contradict: a model cannot make `write_file` not have happened.
    """
    paths, note = git_changes(root)
    notes = [note] if note else []
    seen = list(paths)
    for path in (getattr(state, "changed_paths", ()) or ()):
        cleaned = str(path).replace("\\", "/").strip()
        if cleaned and cleaned != "(unnamed)" and cleaned not in seen:
            seen.append(cleaned)
    return tuple(seen), tuple(notes)


def _is_documentation(path):
    return Path(str(path)).suffix.lower() in DOC_SUFFIXES


def risk_of(paths, forced=None):
    """(ceiling level, reasons) for a change made of these paths.

    Proportional engineering judgement, written down so it can be argued with
    rather than left in a model's head. Higher risk buys more verification;
    a change that is only documentation buys less.
    """
    reasons = []
    paths = [str(p) for p in (paths or ())]
    if forced:
        return int(forced), ("the level was asked for explicitly",)
    if not paths:
        return V.LEVEL_STATIC, ("nothing is known to have changed, so only the "
                                "repository-wide static checks are worth running",)
    if all(_is_documentation(path) for path in paths):
        return V.LEVEL_STATIC, ("every changed path is documentation, so no "
                                "test run is justified",)
    risky = []
    for path in paths:
        lowered = path.lower()
        name = Path(lowered).name
        if name in HIGH_RISK_FILES:
            risky.append("%s is dependency or build configuration" % path)
            continue
        for token in HIGH_RISK_TOKENS:
            if token in lowered:
                risky.append("%s looks security- or contract-sensitive (%r)"
                             % (path, token))
                break
    if risky:
        reasons.extend(risky[:6])
        if len(risky) > 6:
            reasons.append("... and %d further high-risk path(s)" % (len(risky) - 6))
        reasons.append("high-risk changes are verified to the full suite")
        return V.LEVEL_FULL, tuple(reasons)
    if len(paths) >= BROAD_CHANGE_FILES:
        return V.LEVEL_FULL, ("%d files changed, which is a broad change; the "
                              "full suite is run" % len(paths),)
    reasons.append("%d changed path(s), none high-risk" % len(paths))
    return DEFAULT_CEILING, tuple(reasons)


# --- which tests -----------------------------------------------------------


def test_paths_for(paths):
    """(targeted, related) test files for a set of changed paths.

    `agent_testsel` is asked rather than reimplemented. It already reads the
    diff, ranks test files by evidence and keeps its guesses apart from its
    facts, and a second copy of that reasoning here would drift from the first
    without anything failing. Its two private helpers are used directly for
    the reason `TMT.py` imports `_paths_named` directly: what is wanted is
    exactly the list its own report is built from, and a reimplementation
    would be a second answer to the same question.

    Targeted is the evidenced tier and related is the guessed one, which is
    also the difference between level 3 and level 4.
    """
    try:
        import agent_testsel
    except Exception:
        return (), ()
    try:
        diff, error = agent_testsel._read_diff(None)
        if error or not diff:
            return (), ()
        changed = agent_testsel.parse_diff(diff)
        if not changed:
            return (), ()
        for record in changed:
            if not record.symbols:
                agent_testsel._enclosing(record)
        candidates = agent_testsel.test_files()
        direct = agent_testsel._direct_matches(changed, candidates)
        guesses = agent_testsel._heuristics(changed, candidates, direct)
    except Exception:
        return (), ()
    targeted = tuple(sorted(direct))
    related = tuple(path for path, _ in guesses if path not in direct)
    del paths
    return targeted, related


# --- choosing the checks ---------------------------------------------------


def _changed_python(paths):
    """The changed paths that are python source and still exist.

    Still exist, because a deleted file cannot be compiled and asking for one
    would report a failing syntax check for a file the change removed on
    purpose.
    """
    root = Path(agent_config.ROOT_DIR)
    kept = []
    for path in paths:
        if not str(path).lower().endswith(".py"):
            continue
        try:
            if (root / path).is_file():
                kept.append(str(path))
        except OSError:
            continue
    return kept[:MAX_PATHS_PER_COMMAND]


def select(discovery=None, state=None, paths=None, level=None, full=False):
    """Everything to run for this change, in the order to run it.

    `level` forces a ceiling and `full` forces the whole hierarchy; both come
    from the action, and both are the model asking for more or less than the
    evidence suggests. Neither can make a check pass.
    """
    discovery = discovery if discovery is not None else D.detect()
    changed, notes = changed_paths(state)
    if paths:
        chosen = tuple(str(p).replace("\\", "/") for p in paths if str(p).strip())
        if chosen:
            changed = chosen
            notes = notes + ("the change was narrowed to the paths the action named",)
    forced = V.LEVEL_FULL if full else (int(level) if level else None)
    ceiling, reasons = risk_of(changed, forced)
    reasons = list(notes) + list(reasons)

    targeted, related = test_paths_for(changed)
    runner = discovery.runner
    if ceiling >= V.LEVEL_TARGETED and runner is not None and not runner.supports_paths:
        # The honest resolution of section 6 against a project that cannot
        # subset its tests: say so, and let the whole suite be the test
        # evidence rather than pretending a full run was a targeted one.
        ceiling = V.LEVEL_FULL
        reasons.append("this project's test command (%s) cannot be narrowed to "
                       "specific paths, so the whole suite is the only test "
                       "evidence available" % " ".join(runner.argv))

    checks = []
    # LEVEL 1 -- what changed, on its own terms.
    changed_py = _changed_python(changed)
    if changed_py and ceiling >= V.LEVEL_BASIC:
        checks.append(V.VerificationCheck(
            "syntax", "Syntax", V.SYNTAX, V.LEVEL_BASIC,
            ("python", "-m", "py_compile") + tuple(changed_py),
            scope=tuple(changed_py),
            why="%d changed python file(s) are parsed before anything else "
                "runs" % len(changed_py)))
    for spec in discovery.by_level(V.LEVEL_BASIC):
        if ceiling >= V.LEVEL_BASIC:
            checks.append(_check_from(spec))
    # LEVEL 2 -- static analysis over the repository.
    if ceiling >= V.LEVEL_STATIC:
        for spec in discovery.by_level(V.LEVEL_STATIC):
            checks.append(_check_from(spec))
    # LEVELS 3 and 4 -- the tests that name what changed, then the ones around.
    if runner is not None and runner.supports_paths:
        if ceiling >= V.LEVEL_TARGETED and targeted:
            argv = runner.argv_for(targeted[:MAX_PATHS_PER_COMMAND])
            if argv:
                checks.append(V.VerificationCheck(
                    "tests:targeted", "Targeted tests", V.TEST, V.LEVEL_TARGETED,
                    argv, scope=targeted,
                    why="%d test file(s) name a changed module or symbol"
                        % len(targeted)))
        if ceiling >= V.LEVEL_RELATED and related:
            argv = runner.argv_for(related[:MAX_PATHS_PER_COMMAND])
            if argv:
                checks.append(V.VerificationCheck(
                    "tests:related", "Related tests", V.TEST, V.LEVEL_RELATED,
                    argv, scope=related,
                    why="%d further test file(s) reach the changed code "
                        "indirectly (a guess, not evidence)" % len(related)))
    # LEVEL 5 -- the project's build.
    if ceiling >= V.LEVEL_BUILD:
        for spec in discovery.by_level(V.LEVEL_BUILD):
            checks.append(_check_from(spec))
    # LEVEL 6 -- everything.
    if ceiling >= V.LEVEL_FULL:
        if runner is not None:
            checks.append(V.VerificationCheck(
                "tests:full", "Full test suite", V.TEST, V.LEVEL_FULL,
                runner.argv, why=runner.why))
        else:
            for spec in discovery.by_level(V.LEVEL_FULL):
                checks.append(_check_from(spec))
    if not checks:
        reasons.append("no check in this repository applies to this change")
    return Selection(checks=checks, ceiling=ceiling, reasons=tuple(reasons),
                     changed=changed, targeted=targeted, related=related,
                     discovery=discovery)


def _check_from(spec):
    return V.VerificationCheck(spec.id, spec.name, spec.category, spec.level,
                               spec.argv, scope=spec.scope, why=spec.why)


# --- running them ----------------------------------------------------------


def run_selection(selection, state=None, timeout=None, on_change=None,
                  reusable=None, runner=None):
    """Run the chosen checks in order and return the finished result.

    `runner` is `agent_execution.run_command` unless a test injects one, which
    is what lets every path here be driven -- a passing command, a failing
    one, a missing tool, a timeout -- with no subprocess anywhere.

    The order is the level order, cheapest first, and the run STOPS at the
    first check that does not pass. Everything after it is skipped with that
    as the recorded reason.
    """
    execute = runner or run_command
    reusable = reusable or {}
    checks = list(selection.checks)
    if state is not None:
        state.running_now(checks)
    stopped = ""
    for check in checks:
        if stopped:
            check.skip("not run: %s" % stopped)
            continue
        earlier = reusable.get(check.id)
        if earlier is not None:
            check.reuse(earlier)
            _changed(state, on_change, check)
            continue
        if not command_available(check.command):
            # Section 29 and 30 together: say what is missing, install
            # nothing. A skipped check is a hole in the evidence and the
            # report says which hole.
            check.skip("'%s' is not installed, so this check could not run. "
                       "TMT does not install it." % (check.command[0]
                                                     if check.command else "?"))
            _changed(state, on_change, check)
            continue
        check.start()
        if state is not None:
            state.note_activity("Running %s…" % check.name)
        _changed(state, on_change, check)
        outcome = execute(list(check.command), timeout=timeout)
        if outcome.ran:
            check.record(outcome.exit_code, outcome.output, outcome.duration)
        else:
            check.fail_to_run(outcome.error or "it produced no result")
        if not check.passed:
            stopped = "%s did not pass, so the checks after it were not run" % check.name
        _changed(state, on_change, check)
    clock = getattr(state, "_clock", None)
    return V.VerificationResult(
        checks=checks,
        started_at=getattr(state, "started_at", None),
        finished_at=clock() if callable(clock) else None,
        changed=selection.changed,
        notes=selection.reasons)


def _changed(state, on_change, check):
    """Tell the screen the run moved on. Never allowed to break the run."""
    del check
    if state is not None:
        try:
            state.set_live(state.checks())
        except Exception:
            pass
    if on_change is None:
        return
    try:
        on_change()
    except Exception:
        pass


def verify(state, paths=None, level=None, full=False, timeout=None,
           on_change=None, discovery=None, runner=None):
    """One whole verification: choose, run, settle. The engine's front door.

    Every failure between those steps lands in `VerificationState.fail`, which
    is the ERROR state, which blocks the final answer -- the same discipline
    `agent_actions._review` keeps, and for the same reason. A verification
    that crashed must never be mistaken for one that approved the work.
    """
    held = state.begin()
    if held:
        return None, held
    try:
        state.note_activity("Inspecting the repository…")
        if on_change is not None:
            try:
                on_change()
            except Exception:
                pass
        selection = select(discovery=discovery, state=state, paths=paths,
                           level=level, full=full)
    except Exception as error:
        return None, "FAILED: %s" % state.fail(
            "the checks could not be chosen (%s: %s)"
            % (type(error).__name__, error))
    try:
        result = run_selection(selection, state=state, timeout=timeout,
                               on_change=on_change, reusable=state.reusable(),
                               runner=runner)
    except KeyboardInterrupt:
        # Ctrl-C during a verification is the user stopping it, and a
        # cancelled verification is not a passed one. It propagates so the
        # session loop's own handler ends the turn, with the state already
        # recording why.
        state.cancel("the user stopped it with Ctrl-C")
        raise
    except Exception as error:
        return None, "FAILED: %s" % state.fail(
            "the checks could not be run (%s: %s)"
            % (type(error).__name__, error))
    state.settle(result)
    return result, ""


# --- what the reviewer is told ---------------------------------------------

_SUMMARY = re.compile(r"\s+")


def review_note(result):
    """One line for the review's brief: what verification ran and what it said.

    Safe to state as a fact because every word of it is measured -- the
    commands are the ones that ran and the statuses are exit codes. It is the
    one place TMT says something happened rather than quoting somebody else,
    and it can, because it is the only part of a run TMT observed itself.
    """
    if result is None:
        return ""
    ran = result.ran()
    if not ran:
        return "verification ran no checks (%s)" % result.summary_line()
    named = ", ".join("%s=%s" % (check.name, check.status) for check in ran[:8])
    return "verification #%d %s: %s" % (result.number, result.status, named)
