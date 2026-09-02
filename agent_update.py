"""Whether a newer TMT exists upstream, and whether it is safe to take it.

State, a decision and git. Nothing here draws anything, reads a key, starts a
thread or prints -- the same division `agent_plan`, `agent_review` and
`agent_verify` keep, and for the same reason: this runs at startup, before the
user has typed anything, and a module that also owned a splash screen would
have to be right about the terminal as well as about the repository. The
caller owns every character on screen; `check_and_update` hands it one
`UpdateResult` and nothing else.

What it is for. TMT is installed as a git checkout, so "update" means "move
that checkout forward to what the remote already has". That is a fast-forward
and nothing else. The whole module is built around what must NOT happen while
it does that.

The rules that shaped it, in the order they matter:

- **Nothing here may stop TMT launching.** Every path returns an
  `UpdateResult`; `check_and_update` wraps its whole body, so an unexpected
  exception becomes `ERROR` carrying the exception rather than a traceback out
  of `main`. A user whose update check failed still wants their agent. The one
  function that does not return is `restart`, and it only runs after an update
  was applied on purpose.
- **Nothing here may destroy the user's work.** There is exactly one command
  that changes the repository -- `merge --ff-only` -- and it is the weakest
  one that does the job: it cannot rewrite history, it cannot produce a merge
  commit, and given anything but a straight fast-forward it fails and changes
  nothing. The porcelain wrapper that fetches and merges in one step is
  deliberately not used, because it CAN merge, and a merge conflict raised
  during startup is precisely the state this must never create. A test reads
  this module's own source and asserts that no history-destroying flag appears
  in it at all.
- **The update source is INSTALL_DIR, never ROOT_DIR.** `ROOT_DIR` is the
  user's project and TMT may edit it; `INSTALL_DIR` is where TMT's own code
  lives. Updating TMT means updating the second one. They are the same
  directory when TMT is run on TMT, which is exactly why the default is
  written out rather than left to whatever repository discovery happens to
  find -- `agent_git`'s own discovery honours `TMT_GIT_ROOT`, which points at
  the workspace, and inheriting that would have TMT fast-forwarding somebody
  else's repository.
- **Restarts are bounded; checks are not.** `MAX_UPDATE_RESTARTS` is 1. The
  replacement process is expected to check again and report that it is up to
  date -- that is the flow the user is shown, and refusing to look would make
  the second screen say nothing. What it must not do is restart again, so the
  guard sits on APPLYING an update, not on looking for one. A malformed or
  hostile value in the counter reads as "already restarted", never as a crash
  and never as permission.

The decision sequence, and why each step is where it is:

1. **Not a git checkout -> NOT_A_REPO.** TMT can be installed by copying a
   folder, and that install can never update itself. It is a fact to report,
   not a failure.
2. **The restart guard.** Asked before any network call, because a process
   that may not apply an update can still answer the cheap questions.
3. **A dirty working tree -> BLOCKED_DIRTY, AFTER the fetch.** This ordering
   was the other way round until 2026-09-02 and the reversal is deliberate, so
   both sides are written down. Checking dirtiness first bought a launch with
   no network call in a checkout somebody was editing; what it cost was that
   such a checkout dropped out of the update loop **silently and permanently**
   -- it never fetched, so it never knew, so it never said. One stray file in
   an installation was enough, and the user's only symptom was an agent that
   quietly stopped keeping current. Now every launch of every checkout asks
   the same question and reports the same answer, whichever way TMT was
   installed, and a dirty tree changes only what may be APPLIED. The refusal
   names the waiting commits and how to take them. **Nothing about what may be
   TOUCHED changed: a tree with uncommitted work is still never written to.**
4. **No upstream -> NO_UPSTREAM.** Asked of git rather than guessed at, and a
   detached HEAD lands here too: there is no branch, so there is nothing to
   track, so there is nothing to compare against.
5. **Fetch, with a short clock.** A network call at startup has to be bounded
   or a slow remote becomes a hung launch. Terminal prompts are disabled, so
   an expired credential fails in seconds instead of blocking on a password
   nobody can see behind a splash screen. Every failure here -- network,
   authentication, a remote that has been deleted -- is ERROR, and ERROR
   continues.
6. **Compare, and only then decide.** `behind == 0` is CURRENT and nothing
   runs. Both moved is BLOCKED_DIVERGED: the local commits are the user's, and
   the only ways to resolve a divergence either rewrite history or make a
   merge, both of which are decisions for a person and not for a startup
   screen.
7. **Apply.** A fast-forward, then re-read the sha so `before` and `after` are
   what git says happened rather than what this module expected to happen.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import agent_config
import agent_git

# --- what came back --------------------------------------------------------
#
# These are the runtime's own words. The caller turns them into a sentence for
# the user; nothing outside this module should have to parse a headline to
# work out what happened, which is why the status is a value of its own.

CURRENT = "current"                    # already up to date, nothing to do
UPDATED = "updated"                    # a fast-forward was applied
AVAILABLE = "available"                # newer commits exist and are safe to take
BLOCKED_DIRTY = "blocked-dirty"        # local uncommitted changes; refused
BLOCKED_DIVERGED = "blocked-diverged"  # local and remote have both moved
NO_UPSTREAM = "no-upstream"            # the branch tracks nothing
NOT_A_REPO = "not-a-repo"              # TMT was not installed as a checkout
DISABLED = "disabled"                  # allowed to look, not allowed to apply
ERROR = "error"                        # fetch, network or git failure

STATUSES = (CURRENT, UPDATED, AVAILABLE, BLOCKED_DIRTY, BLOCKED_DIVERGED,
            NO_UPSTREAM, NOT_A_REPO, DISABLED, ERROR)

# AVAILABLE is the answer to "what does this comparison mean", not the answer
# to "what happened": `compare_status` returns it and `check_and_update` then
# either applies the update or explains why it may not. It is public because
# the comparison is a pure function worth testing on its own, and because a
# caller that only wants to know whether an update exists should not have to
# take one.

# A startup check is on the critical path of a launch, so both clocks are
# short enough that the worst case is felt as a pause rather than as a hang.
# The fetch is the one that crosses the network; the fast-forward is local and
# only needs to survive a slow disk.
FETCH_TIMEOUT = 20
MERGE_TIMEOUT = 60

# How much of git's own output travels back in `detail`. Enough to name the
# cause, not enough to fill a screen.
DETAIL_LIMIT = 500

# At most one automatic restart per launch. The counter travels to the
# replacement process in the environment, because that is the only channel a
# fresh interpreter is guaranteed to read -- a file would have to be cleaned
# up by whoever crashed, and a crashed launch is exactly when the loop it
# guards against would start.
MAX_UPDATE_RESTARTS = 1
RESTART_ENV_VAR = "TMT_UPDATE_RESTARTS"


class UpdateResult:
    """One answer, whatever happened. Never an exception, never None.

    `headline` is ONE short line, written to be shown as it is: the caller has
    a splash screen with a subtitle on it and no room to summarise. `detail`
    is the longer form -- git's own words where there are any -- for a log or
    a diagnostic. `before` and `after` are short shas, and they are read back
    from git after the work rather than predicted before it.
    """

    def __init__(self, status, headline, detail="", before="", after="",
                 ahead=0, behind=0):
        self.status = status
        self.headline = headline
        self.detail = detail or ""
        self.before = before or ""
        self.after = after or ""
        self.ahead = int(ahead or 0)
        self.behind = int(behind or 0)

    @property
    def applied(self):
        """Whether the checkout on disk actually moved."""
        return self.status == UPDATED

    @property
    def should_restart(self):
        """Whether the caller must hand over to a fresh interpreter.

        The same question as `applied` today, and kept separate anyway: the
        code that was loaded into this process is no longer the code on disk,
        which is a statement about THIS process rather than about the
        repository. A future status that moved files without needing a
        restart would answer these two differently.
        """
        return self.status == UPDATED

    @property
    def is_failure(self):
        """Whether TMT failed to find out, as opposed to finding out something
        it could not act on. A refusal is an answer; ERROR is the absence of
        one."""
        return self.status == ERROR

    def as_dict(self):
        return {
            "status": self.status, "headline": self.headline,
            "detail": self.detail, "before": self.before, "after": self.after,
            "ahead": self.ahead, "behind": self.behind,
        }

    def __repr__(self):
        return "UpdateResult(%r, %r)" % (self.status, self.headline)


# --- logging ---------------------------------------------------------------

def _safe_log(event, detail=""):
    """Write one line to the existing git log, and never fail because of it.

    Every call is guarded. A log is a diagnostic, and a diagnostic that can
    stop a launch is worse than no diagnostic at all -- the disk being full is
    not a reason for TMT not to start.
    """
    try:
        agent_git.log(event, detail)
    except Exception:
        pass


def _clean(text, limit=DETAIL_LIMIT):
    """git's own words, with any credentials in a remote URL removed.

    Fetch failures quote the remote, and a remote URL can carry the userinfo
    half of a credential. The fallback is a fixed sentence rather than the raw
    text: the whole reason this function exists is that the raw text is not
    safe to show, so a scrubber that failed must not be worked around by
    printing what it refused to clear.
    """
    try:
        cleaned = agent_git._scrub(str(text or ""))
    except Exception:
        return "(git output withheld: it could not be checked for credentials)"
    cleaned = cleaned.strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + " ... (truncated)"
    return cleaned


# --- the pure decisions ----------------------------------------------------

def compare_status(ahead, behind):
    """What `ahead`/`behind` means, with no git and no side effects.

    Three outcomes and no fourth. Nothing behind is CURRENT whatever is ahead:
    a checkout carrying commits of its own that the remote has not got is not
    out of date, it is somebody's work in progress, and there is nothing to
    take. Both moved is a divergence, and a divergence is never resolved here.
    """
    ahead, behind = int(ahead), int(behind)
    if behind <= 0:
        return CURRENT
    if ahead > 0:
        return BLOCKED_DIVERGED
    return AVAILABLE


def split_upstream(upstream, remotes):
    """Split "origin/main" into ("origin", "main") using the real remote names.

    Not a split on the first slash. A branch name may contain slashes
    (`origin/feature/login`), and so the only reliable separator is a remote
    that actually exists -- asked longest-first, so a remote named `upstream`
    is preferred over one named `up` when both are configured. Returns
    ("", "") when no configured remote owns the ref, which the caller reports
    rather than guesses past.
    """
    text = str(upstream or "").strip()
    if not text:
        return "", ""
    for name in sorted([str(r) for r in (remotes or [])], key=len, reverse=True):
        if not name:
            continue
        if text.startswith(name + "/"):
            return name, text[len(name) + 1:]
    return "", ""


# --- the restart -----------------------------------------------------------

def restarts_used(env=None):
    """How many automatic restarts this launch has already spent.

    Fails SAFE in every direction that is not a plain readable count. An
    absent variable is a first launch and reads as 0; anything unparseable,
    negative or hostile reads as the ceiling, which means "already restarted"
    and stops another one. The value arrives from the environment, so it is
    attacker-controlled in the only sense that matters here: a user can set it
    to anything, and no value they can set may either crash the launch or buy
    an extra restart.
    """
    source = os.environ if env is None else env
    try:
        raw = source.get(RESTART_ENV_VAR, "")
    except Exception:
        return MAX_UPDATE_RESTARTS
    text = str(raw or "").strip()
    if not text:
        return 0
    try:
        value = int(text)
    except (TypeError, ValueError):
        return MAX_UPDATE_RESTARTS
    if value < 0:
        return MAX_UPDATE_RESTARTS
    return value


def restart_env(env=None):
    """A copy of the environment for the replacement process, counter raised.

    A copy, never a mutation of the caller's mapping: the process that is
    about to be replaced may still have to report a failure with its own
    environment intact if the handover does not happen.
    """
    source = os.environ if env is None else env
    updated = dict(source)
    updated[RESTART_ENV_VAR] = str(restarts_used(source) + 1)
    return updated


def is_python_script(program):
    """Whether argv[0] is a script that needs an interpreter in front of it.

    The two shapes TMT is launched in are `python TMT.py` and the `tmtcode`
    console script the install generates. In the first, argv[0] is a source
    file and re-running it means handing it to an interpreter; in the second,
    argv[0] is already an executable and putting an interpreter in front of it
    would fail. A small pure function so both shapes can be driven in a test
    without spawning anything.
    """
    return str(program or "").lower().endswith((".py", ".pyw"))


def restart_command(argv=None, executable=None):
    """The argv that runs TMT again, exactly as it was run this time.

    Every argument is preserved. `tmtcode --dir X` has to come back as
    `tmtcode --dir X`, or an update would silently move the user out of the
    workspace they chose -- which is the kind of surprise that makes an
    automatic update feel like something that was done TO somebody.
    """
    argv = list(sys.argv if argv is None else argv)
    runner = str(sys.executable if executable is None else executable) or "python"
    if not argv or not str(argv[0]).strip():
        return [runner]
    first = str(argv[0])
    rest = [str(argument) for argument in argv[1:]]
    if is_python_script(first):
        return [runner, first] + rest
    return [first] + rest


def _program_path(program):
    """An executable git-style name resolved to something execv can run.

    `os.execv` does not search PATH. A console script is usually given to us
    with its full path already, but a bare name has to be looked up or the
    handover fails on the one launch shape that most needs it to work.
    """
    text = str(program)
    if os.sep in text or (os.altsep and os.altsep in text):
        return text
    return shutil.which(text) or text


def restart(argv=None, executable=None, environ=None, execv=None, spawn=None,
            exit_with=None, windows=None):
    """Hand over to a fresh interpreter running the updated code.

    Does not return when it works, which is the point: the code loaded into
    THIS process is the code that was on disk before the fast-forward, so
    carrying on here would run the old TMT while reporting the new one. A
    re-import would not help -- half the modules are already bound.

    Two platforms, two mechanisms, and the difference is not cosmetic. On
    POSIX `os.execv` replaces the process, which keeps one pid, one console
    and one exit status. On Windows `os.execv` is unreliable for a console
    application -- the parent can exit while the child is still attached to
    the console -- so the replacement is spawned and this process waits for it
    and exits with its code. If the handover cannot be made at all the
    spawning path is used as a fallback rather than letting an OSError escape
    into a launch.

    Every moving part is injectable so the whole of this can be driven in a
    test without a process ever being created.
    """
    source = os.environ if environ is None else environ
    prepared = restart_env(source)
    command = restart_command(argv, executable)
    _safe_log("restart", "handing over to " + " ".join(command))
    on_windows = (os.name == "nt") if windows is None else bool(windows)
    if not on_windows:
        runner = os.execv if execv is None else execv
        if environ is None:
            # execv carries this process's environment, so the counter has to
            # be in it before the call rather than passed to it.
            os.environ[RESTART_ENV_VAR] = prepared[RESTART_ENV_VAR]
        try:
            return runner(_program_path(command[0]), command)
        except OSError as error:
            _safe_log("restart", "could not replace this process: %s" % error)
    runner = subprocess.call if spawn is None else spawn
    code = runner(command, env=prepared)
    finish = sys.exit if exit_with is None else exit_with
    return finish(code if isinstance(code, int) else 1)


# --- the check -------------------------------------------------------------

def _result(status, headline, detail="", before="", after="", ahead=0, behind=0):
    _safe_log("update", "%s: %s" % (status, detail or headline))
    return UpdateResult(status, headline, detail, before, after, ahead, behind)


def _engine(root, git):
    """The repository to update, or the reason there is not one.

    Returns (engine, refusal). The refusal is an UpdateResult and is never an
    exception: an installation that is a plain copy of a folder is a supported
    installation, and so is a machine with no git on it. Neither can update,
    and neither is a crash.
    """
    if git is not None:
        return git, None
    target = Path(str(root)) if root else Path(agent_config.INSTALL_DIR)
    engine = agent_git.TMTGit(root=target)
    try:
        probe = engine._run(["rev-parse", "--show-toplevel"], check=False)
    except agent_git.GitError as error:
        return None, _result(
            NOT_A_REPO, "TMT cannot check for updates here.",
            detail=_clean(error))
    top = probe.stdout.strip() if probe.returncode == 0 else ""
    if not top:
        return None, _result(
            NOT_A_REPO,
            "TMT was not installed as a git checkout, so it cannot update itself.",
            # The one status here a user can do something about, so it says
            # what. Both supported installs leave a checkout -- npm clones one
            # into ~/.tmtcode, and `pip install -e .` runs from the clone you
            # made -- and a copied folder or a `pip install .` into
            # site-packages is the case that lands here and stays on whatever
            # version it was made from.
            detail="%s is a copy rather than a clone, so there is nothing to "
                   "update from. Installing with `npm install -g tmtcode`, or "
                   "from a git clone with `pip install -e .`, gives a TMT that "
                   "keeps itself current." % target)
    resolved = Path(top).resolve()
    if resolved != engine.root:
        # Installed into a subdirectory of a larger checkout: the repository
        # is what git says it is, not what the modules happen to sit in.
        engine = agent_git.TMTGit(root=resolved)
    return engine, None


def _short_head(engine):
    """The current commit as a short sha, or "" when there is not one yet."""
    try:
        result = engine._run(["rev-parse", "--short", "HEAD"], check=False)
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _upstream(engine):
    """The ref the current branch tracks, or "".

    `check=False`, so a branch with no upstream and a detached HEAD both come
    back as a return code. Asking with `check=True` would make the ordinary
    case -- somebody working on a branch of their own -- raise.
    """
    result = engine._run(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        check=False)
    if result.returncode != 0:
        return ""
    name = result.stdout.strip()
    return "" if name in ("", "@{u}") else name


def _counts(engine, upstream):
    """(ahead, behind) against the upstream ref, or None when git would not say."""
    result = engine._run(
        ["rev-list", "--left-right", "--count", "HEAD...@{u}"], check=False)
    if result.returncode != 0:
        return None
    fields = result.stdout.split()
    if len(fields) < 2:
        return None
    try:
        return int(fields[0]), int(fields[1])
    except (TypeError, ValueError):
        return None


def _fetch_env():
    """The environment one fetch runs in.

    `GIT_TERMINAL_PROMPT=0` is the whole of it, and it is not optional: a
    remote asking for a password behind a splash screen would block the launch
    on a prompt the user cannot see until the timeout expires.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _dirty_detail(state):
    """The paths that stopped the update, so the refusal names them."""
    names = []
    for key in ("staged", "unstaged", "untracked"):
        names.extend(state.get(key) or [])
    if not names:
        return ""
    shown = ", ".join(names[:6])
    if len(names) > 6:
        shown += " and %d more" % (len(names) - 6)
    return "%d changed path(s): %s" % (len(names), shown)


def check_and_update(root=None, git=None, allow_restart=True, env=None):
    """Decide whether a newer TMT exists, and take it when that is safe.

    Returns an UpdateResult and never raises. `root` defaults to
    `agent_config.INSTALL_DIR` -- TMT's own checkout, not the workspace.
    `git` accepts a ready-made engine so the whole sequence can be driven
    without a repository. `allow_restart` false means "look, but do not
    apply": applying an update the caller cannot restart into would leave this
    process running code that no longer exists on disk, and TMT imports some
    modules lazily, so the next lazy import would load half of a different
    version.

    The outer guard is the point of the function. Startup is the one place
    where an exception costs the user everything they were about to do, so
    anything unforeseen becomes ERROR with the exception in `detail`.
    """
    try:
        return _check_and_update(root, git, allow_restart, env)
    except Exception as error:
        return _result(
            ERROR, "TMT could not check for an update.",
            detail="%s: %s" % (type(error).__name__, _clean(error)))


def _check_and_update(root, git, allow_restart, env):
    engine, refusal = _engine(root, git)
    if refusal is not None:
        return refusal

    before = _short_head(engine)
    _safe_log("update", "checking %s at %s" % (engine.root, before or "no commit"))

    # Step 2. Asked before the network, and it bounds RESTARTS rather than
    # checks: the process that has already restarted once still reports where
    # it stands, it just may not move again.
    may_apply = bool(allow_restart) and restarts_used(env) < MAX_UPDATE_RESTARTS

    try:
        state = engine.status()
    except agent_git.GitError as error:
        return _result(ERROR, "TMT could not read its own repository.",
                       detail=_clean(error), before=before)

    upstream = _upstream(engine)
    if not upstream:
        return _result(
            NO_UPSTREAM,
            "No update source: this checkout tracks no remote branch.",
            detail="branch: %s" % state.get("branch", "unknown"),
            before=before, after=before)

    try:
        remotes = engine.remotes()
    except agent_git.GitError as error:
        return _result(ERROR, "TMT could not read its own remotes.",
                       detail=_clean(error), before=before, after=before)
    remote, branch = split_upstream(upstream, remotes)
    if not remote:
        return _result(
            NO_UPSTREAM,
            "No update source: %s is not a configured remote." % upstream,
            detail="remotes: %s" % (", ".join(remotes) or "none"),
            before=before, after=before)

    # Step 5.
    fetched = engine._run(["fetch", remote, branch], env=_fetch_env(),
                          timeout=FETCH_TIMEOUT, check=False)
    if fetched.returncode != 0:
        return _result(
            ERROR, "TMT could not reach the update server.",
            detail=_clean(fetched.stderr) or _clean(fetched.stdout),
            before=before, after=before)

    # Step 6.
    counts = _counts(engine, upstream)
    if counts is None:
        return _result(ERROR, "TMT could not compare itself with the update.",
                       detail="git could not count the commits between HEAD "
                              "and %s" % upstream,
                       before=before, after=before)
    ahead, behind = counts
    _safe_log("update", "%s: %d ahead, %d behind" % (upstream, ahead, behind))
    decision = compare_status(ahead, behind)

    if decision == CURRENT:
        return _result(CURRENT, "TMT is up to date.",
                       detail="%s is level with %s" % (before or "HEAD", upstream),
                       before=before, after=before, ahead=ahead, behind=behind)
    if decision == BLOCKED_DIVERGED:
        return _result(
            BLOCKED_DIVERGED,
            "Update skipped: this checkout has commits of its own.",
            detail="%d local commit(s) the remote has not got, and %d waiting "
                   "here. Nothing was changed." % (ahead, behind),
            before=before, after=before, ahead=ahead, behind=behind)

    if not may_apply:
        return _result(
            DISABLED,
            "An update is waiting; TMT will apply it on the next launch.",
            detail="%d new commit(s) on %s, not applied: this launch has "
                   "already restarted once." % (behind, upstream),
            before=before, after=before, ahead=ahead, behind=behind)

    # Step 6a. The tree, asked here rather than before the fetch. The state was
    # read at the top and has not changed since; what moved is the moment it is
    # ACTED on, and the difference is the whole of what the user is told. See
    # the module docstring: an edited checkout used to drop out of the update
    # loop silently and permanently, and now it is told what is waiting for it.
    # Nothing about what may be TOUCHED changed -- a dirty tree is still never
    # written to.
    if not state.get("clean", False):
        waiting = ("%d new commit(s) on %s, not applied: commit or stash to "
                   "take it." % (behind, upstream))
        changed = _dirty_detail(state)
        return _result(
            BLOCKED_DIRTY,
            "An update is waiting; TMT's own folder has uncommitted changes.",
            detail=waiting + ((" " + changed) if changed else ""),
            before=before, after=before, ahead=ahead, behind=behind)

    # Step 7. The weakest command that does the job.
    merged = engine._run(["merge", "--ff-only", upstream], timeout=MERGE_TIMEOUT,
                         check=False)
    if merged.returncode != 0:
        return _result(
            ERROR, "TMT could not apply the update.",
            detail=_clean(merged.stderr) or _clean(merged.stdout),
            before=before, after=_short_head(engine), ahead=ahead, behind=behind)
    after = _short_head(engine)
    return _result(
        UPDATED, "TMT updated to the latest version.",
        detail="%s -> %s, %d new commit(s) from %s"
               % (before or "nothing", after or "HEAD", behind, upstream),
        before=before, after=after, ahead=ahead, behind=behind)
