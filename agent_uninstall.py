"""Taking TMT off a machine: what would go, and then taking it away.

The one operation in TMT that destroys TMT. Everything here is built around
that: it is planned before it is done, the plan is what the screen shows, and
the plan and the doing read the same list -- so what the user agreed to is
what happens rather than a description of it written separately.

**The rule for what goes is git's, not a list kept here.** Everything the
repository TRACKS is removed; everything it IGNORES is left exactly where it
is. That is what makes this safe to point at a directory somebody works in:
`CLAUDE.local.md`, `.tmt_key`, `logs/`, a scratch file, an editor's rubbish --
git already knows which of those are TMT's and which are the user's, and it
knows better than any list this module could keep current. A file TMT never
shipped is a file TMT has no business deleting.

The three things that are removed beyond the tracked files are named rather
than implied: `.git` itself (an install is not a checkout afterwards), the
directories that are empty once their contents have gone, and the `tmtcode`
command on PATH -- `npm uninstall -g tmtcode` and `pip uninstall -y tmtcode`,
each run only when that install is actually there.

**Nothing here reads a key, draws a screen or asks a question.** The menu
does the asking; this module answers "what would happen" and "do it". Same
division as `agent_plan` and `agent_review`, and for the same reason: the
part that can destroy something is the part that has to be testable without
a terminal in the way.

**No process is created in this module.** Git goes through `agent_git` and the
two uninstall commands through `agent_execution.run_command`, which are two of
the four modules allowed to start anything; a test parses this file and would
fail if that changed.
"""

import os
import shutil
import stat
import sys
from pathlib import Path

import agent_config

# What the two package managers call TMT. One name, because there is one
# program: `pyproject.toml` and `package.json` both publish `tmtcode`, and a
# test asserts these agree with them rather than with somebody's memory.
PACKAGE = "tmtcode"

# How long each uninstall command is given. npm has to resolve a global tree
# and pip has to read its own metadata; neither is fast, and neither is worth
# waiting on forever when the answer to a hung one is "it was not removed".
COMMAND_TIMEOUT = 120

# Directories that are never the install: a drive root, a home directory, or
# anything shallower than two levels. TMT is not installed in any of them and
# a bug that made this point at one would be unrecoverable rather than
# annoying, which is the one shape of failure worth spending a guard on.
def _is_refusable(root):
    """A sentence saying why this directory must not be uninstalled, or ""."""
    path = Path(root)
    if not path.exists():
        return "%s does not exist." % path
    if not path.is_dir():
        return "%s is not a directory." % path
    resolved = path.resolve()
    if resolved.parent == resolved:
        return "%s is a filesystem root." % resolved
    try:
        home = Path(os.path.expanduser("~")).resolve()
    except Exception:
        home = None
    if home is not None and resolved == home:
        return "%s is your home directory, not a TMT installation." % resolved
    return ""


class UninstallPlan(object):
    """What an uninstall would do, worked out before anything is touched.

    Everything on it is a measurement rather than an intention: `tracked` is
    what git says it tracks, `kept` is what git says it ignores, `commands`
    are the ones whose package manager was actually found. The screen shows
    exactly these, so a user agrees to the real thing.
    """

    __slots__ = ("root", "checkout", "tracked", "kept", "commands",
                 "is_workspace", "refusal", "notes")

    def __init__(self, root, checkout=False, tracked=(), kept=(), commands=(),
                 is_workspace=False, refusal="", notes=()):
        self.root = Path(root)
        self.checkout = bool(checkout)
        self.tracked = tuple(tracked)
        self.kept = tuple(kept)
        self.commands = tuple(commands)
        self.is_workspace = bool(is_workspace)
        self.refusal = str(refusal or "")
        self.notes = tuple(notes)

    @property
    def possible(self):
        """Whether there is anything to do and nothing forbidding it."""
        return not self.refusal and bool(self.tracked or self.commands)

    def summary(self):
        """The two numbers a person actually decides on."""
        return "%d file(s) removed, %d kept" % (len(self.tracked), len(self.kept))


class UninstallReport(object):
    """What actually happened. Counted from the filesystem, never assumed."""

    __slots__ = ("root", "removed", "kept", "failures", "commands", "notes")

    def __init__(self, root, removed=0, kept=0, failures=(), commands=(),
                 notes=()):
        self.root = Path(root)
        self.removed = int(removed)
        self.kept = int(kept)
        self.failures = tuple(failures)
        self.commands = tuple(commands)
        self.notes = tuple(notes)

    @property
    def clean(self):
        """Whether every part of it worked."""
        return not self.failures and all(ok for _, ok, _ in self.commands)

    def lines(self):
        """The report as rows, most important first."""
        rows = ["%d file(s) removed from %s." % (self.removed, self.root)]
        if self.kept:
            rows.append("%d file(s) kept: everything git ignores, including "
                        "your own notes." % self.kept)
        for label, ok, detail in self.commands:
            rows.append(("%s: removed." % label) if ok
                        else ("%s: not removed. %s" % (label, detail)))
        for failure in self.failures[:5]:
            rows.append("Could not remove %s" % failure)
        if len(self.failures) > 5:
            rows.append("... and %d more that could not be removed."
                        % (len(self.failures) - 5))
        rows.extend(self.notes)
        return rows


def _engine(root):
    """agent_git's engine for `root`, or None when this is not a checkout."""
    try:
        import agent_git
        engine = agent_git.TMTGit(root=Path(root))
        probe = engine._run(["rev-parse", "--is-inside-work-tree"], check=False)
    except Exception:
        return None
    if probe.returncode != 0 or "true" not in (probe.stdout or "").lower():
        return None
    return engine


def _listing(engine, args):
    """A -z file listing from git as a tuple of relative paths."""
    try:
        done = engine._run(list(args) + ["-z"], check=False)
    except Exception:
        return ()
    if done.returncode != 0:
        return ()
    return tuple(part for part in (done.stdout or "").split("\0") if part)


def tracked_files(root):
    """What the repository tracks: exactly what an uninstall removes."""
    engine = _engine(root)
    return _listing(engine, ["ls-files"]) if engine is not None else ()


def ignored_files(root):
    """What the repository ignores: exactly what an uninstall keeps.

    Reported rather than merely spared, because "your notes and your key are
    still there" is the half of this operation a user needs told. Untracked
    files that are not ignored are counted here too: they are equally not
    TMT's, and the promise being made is about what is LEFT rather than about
    which git category it came from.
    """
    engine = _engine(root)
    if engine is None:
        return ()
    ignored = _listing(engine, ["ls-files", "--others", "--ignored",
                                "--exclude-standard"])
    others = _listing(engine, ["ls-files", "--others", "--exclude-standard"])
    return tuple(sorted(set(ignored) | set(others)))


def _has_pip_distribution():
    """Whether pip installed TMT as a distribution on this interpreter."""
    try:
        from importlib.metadata import distribution
        distribution(PACKAGE)
        return True
    except Exception:
        return False


def _has_npm():
    """Whether npm is on PATH at all. Its own uninstall answers the rest.

    Deliberately not `npm ls -g tmtcode`: that is a second of startup spent to
    learn what `npm uninstall -g tmtcode` reports for itself in the same
    breath, and an npm that is present but has no TMT exits 0 with nothing
    removed -- which is the honest outcome rather than a failure.
    """
    return bool(shutil.which("npm"))


def detect_commands():
    """[(label, argv)] for the package managers that installed this TMT."""
    found = []
    if _has_npm():
        found.append(("The npm command `%s`" % PACKAGE,
                      ["npm", "uninstall", "-g", PACKAGE]))
    if _has_pip_distribution():
        found.append(("The pip package `%s`" % PACKAGE,
                      [sys.executable, "-m", "pip", "uninstall", "-y", PACKAGE]))
    return found


def plan(root=None):
    """What an uninstall would do. Reads; changes nothing. Never raises."""
    root = Path(agent_config.INSTALL_DIR if root is None else root)
    refusal = _is_refusable(root)
    if refusal:
        return UninstallPlan(root, refusal=refusal)
    tracked = tracked_files(root)
    kept = ignored_files(root)
    checkout = bool(tracked) or _engine(root) is not None
    notes = []
    if not checkout:
        notes.append(
            "This installation is not a git checkout, so there are no TMT "
            "files here to remove -- whatever installed it owns them.")
    try:
        is_workspace = Path(agent_config.ROOT_DIR).resolve() == root.resolve()
    except Exception:
        is_workspace = False
    if is_workspace:
        notes.append(
            "This directory is also the workspace this session is working in.")
    if kept:
        notes.append(
            "%d file(s) git ignores stay exactly where they are, including "
            "your own notes and TMT's saved key." % len(kept))
    return UninstallPlan(root, checkout=checkout, tracked=tracked, kept=kept,
                         commands=detect_commands(), is_workspace=is_workspace,
                         notes=notes)


def _inside(root, target):
    """Whether `target` really is under `root`, symlinks resolved."""
    try:
        root_resolved = Path(root).resolve()
        candidate = Path(target).resolve()
    except OSError:
        return False
    return root_resolved == candidate or root_resolved in candidate.parents


def _clear_readonly(func, target, _exc):
    """Take the read-only bit off and try once more.

    `.git` is the reason this exists. Git marks the files in its object store
    read-only, and on Windows a read-only file cannot be deleted at all -- so
    an uninstall that did not do this removed every source file and then left
    the repository behind, reported as a failure the user could do nothing
    about. Found by the test, not by reading.

    It re-raises when the second attempt fails, so a genuine permission
    problem is still reported rather than swallowed into a rmtree that claims
    to have finished.
    """
    os.chmod(target, stat.S_IWRITE)
    func(target)


def _rmtree(path):
    """rmtree, with the read-only handler this Python version accepts."""
    if sys.version_info >= (3, 12):
        # `onerror` still works and is deprecated from 3.12; `onexc` is the
        # same handler with the exception in place of the exc_info triple,
        # and this one reads neither.
        shutil.rmtree(str(path), onexc=_clear_readonly)
    else:
        shutil.rmtree(str(path), onerror=_clear_readonly)


def _remove(path):
    """Delete one file or link. Returns "" or the reason it survived."""
    try:
        if path.is_dir() and not path.is_symlink():
            _rmtree(path)
        else:
            try:
                os.remove(str(path))
            except PermissionError:
                os.chmod(str(path), stat.S_IWRITE)
                os.remove(str(path))
        return ""
    except OSError as error:
        return getattr(error, "strerror", None) or str(error)


def _prune_empty(root):
    """Remove the directories left empty, deepest first. Never the root.

    Emptiness is re-read with `listdir` rather than taken from the walk's own
    lists, which are collected before anything is deleted -- so a directory
    whose only child has just been pruned still looked occupied and survived.
    `testing/` outliving `testing/unit/` is how that showed up.
    """
    root_resolved = Path(root).resolve()
    for current, _directories, _files in os.walk(str(root), topdown=False):
        if Path(current).resolve() == root_resolved:
            continue
        try:
            if not os.listdir(current):
                os.rmdir(current)
        except OSError:
            pass


def execute(plan_or_root=None, run=None):
    """Carry out an uninstall. Returns an UninstallReport. Never raises.

    Takes the PLAN rather than a directory, so what is removed is the list the
    user was shown. A plan that refuses is honoured here as well as on screen:
    this is the last place that could be wrong, and it is the one where being
    wrong cannot be undone.

    `run` is the command runner, injected so the whole of this can be driven
    without uninstalling anything. It defaults to `agent_execution.run_command`,
    which is one of the four modules allowed to start a process.
    """
    ready = plan_or_root
    if not isinstance(ready, UninstallPlan):
        ready = plan(ready)
    if ready.refusal:
        return UninstallReport(ready.root, notes=(ready.refusal,))

    root = ready.root
    removed, failures = 0, []
    for relative in ready.tracked:
        target = root / relative
        if not _inside(root, target):
            # Cannot happen from `git ls-files`, which is relative and inside
            # by construction. Checked anyway: this is the loop that deletes,
            # and a guard that only matters when something else is already
            # wrong is exactly the guard worth having.
            failures.append("%s (outside the installation)" % relative)
            continue
        if not target.exists() and not target.is_symlink():
            continue
        reason = _remove(target)
        if reason:
            failures.append("%s (%s)" % (relative, reason))
        else:
            removed += 1

    # The repository itself, once its files have gone. Left behind, the
    # directory would still be a checkout of a project with every file
    # deleted, which is a worse state than either finishing or not starting.
    git_dir = root / ".git"
    if ready.checkout and git_dir.exists():
        reason = _remove(git_dir)
        if reason:
            failures.append(".git (%s)" % reason)
        else:
            removed += 1

    _prune_empty(root)

    if run is None:
        try:
            from agent_execution import run_command as run
        except Exception:
            run = None
    commands = []
    for label, argv in ready.commands:
        if run is None:
            commands.append((label, False, "TMT could not run the uninstaller."))
            continue
        try:
            outcome = run(argv, timeout=COMMAND_TIMEOUT)
        except Exception as error:
            commands.append((label, False, "%s: %s" % (type(error).__name__, error)))
            continue
        ok = getattr(outcome, "exit_code", None) == 0
        detail = (getattr(outcome, "error", "") or
                  (getattr(outcome, "output", "") or "").strip().splitlines()[-1:] or [""])
        commands.append((label, ok, detail if isinstance(detail, str) else detail[0]))

    notes = list(ready.notes)
    kept = len(ready.kept)
    if kept:
        notes.append("Delete %s yourself if you want nothing left of it."
                     % root)
    return UninstallReport(root, removed=removed, kept=kept, failures=failures,
                           commands=commands, notes=notes)
