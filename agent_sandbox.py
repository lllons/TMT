"""The one place in TMT where a process is created on behalf of a model.

Every model-facing execution goes through here: the `bash` tool's pipelines and
`agent_execution.run_command`'s verification runs alike. That is the whole point
of the module. The curated PATH, the constructed environment, the resource
limits and the process-tree kill are applied in one place, so a new caller
cannot arrive later and quietly get none of them.

WHAT IS ACTUALLY ENFORCED, AND WHAT IS NOT
==========================================

There are two levels and this module reports which one ran, never which one it
would have liked to run.

  LEVEL_OS      an OS sandbox helper exists on the host -- `bwrap` on Linux,
                `sandbox-exec` on macOS -- and the command is launched inside
                it. Filesystem confinement is then real and kernel-enforced,
                and under the `offline` network mode the child is put in a
                network namespace with no route out.

  LEVEL_POLICY  no such helper. This is the normal case on Windows, and it is
                the case on the machine this module was written on.

**Under LEVEL_POLICY a child process's filesystem writes are NOT confined.**
Python's standard library cannot confine them on Windows and this repository
forbids adding a dependency, so nothing here pretends otherwise. What is
enforced under LEVEL_POLICY is real but narrower, and it is exactly this:

  * the program that runs is resolved by TMT against a curated PATH, so a
    child cannot reach a tool TMT did not put within its reach by name;
  * the child's environment is built from empty rather than inherited, so no
    credential of the user's reaches it;
  * the working directory is whatever the caller confined it to, and stdin is
    closed, so nothing can sit waiting on a terminal nobody is watching;
  * a wall-clock timeout, after which the whole process tree is killed;
  * a bounded amount of output, read incrementally;
  * process and memory ceilings where the platform provides them.

A permitted build tool that runs repository code can still write wherever the
user can. It is that code doing the writing, and no inspection of the command
line can see it coming. Say that plainly wherever this module is described.

WHAT DEGRADES
=============

A limit that cannot be applied is recorded on the outcome and the command runs
anyway. A build that will not start because a job object could not be created
is worse than a build that runs without one -- but silence about it is worse
than both, so `ExecOutcome.degraded` carries a sentence per limit that was
asked for and not applied, and `ExecOutcome.level` names the level that
actually ran the command rather than the level the host could manage.

`default_limits()` asks only for what the running platform can express, so an
ordinary run's `degraded` is empty and anything in it is real news. A caller
that asks for a limit this platform cannot do is still told so.

WHAT IS EVIDENCE
================

`ExecOutcome.exit_code` is None and only None when the command did not run to
completion. That is `agent_execution.CommandOutcome`'s rule and it survives
here unchanged: an exit code is evidence, its absence is the absence of
evidence, and collapsing the two into a boolean would make "the tool is not
installed" and "the tool ran and found problems" the same answer.
"""

import atexit
import codecs
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import weakref
from pathlib import Path

import agent_config
import agent_memory

# --- what this host can do --------------------------------------------------

LEVEL_OS = "os"
LEVEL_POLICY = "policy"

# The three network modes, spelled here because `agent_policy` decides with
# them and this module builds an environment from them, and a typo shared
# between two modules is a mode that silently means "offline" in one and
# nothing in the other.
NETWORK_OFFLINE = "offline"
NETWORK_DEPS = "deps"
NETWORK_OPEN = "open"

# The helper each platform would need for LEVEL_OS. Nothing else is accepted:
# a sandbox is claimed only for a tool whose confinement this module actually
# invokes, and these two are the ones it knows how to invoke.
_OS_HELPERS = (("linux", "bwrap"), ("darwin", "sandbox-exec"))

# How much output is kept. Same figure as `agent_execution.MAX_COMMAND_OUTPUT`
# and for the same reason: a failing suite's useful part is its tail, and 2000
# characters of a pytest run is sometimes not even the summary.
MAX_OUTPUT = 40000

# The ceiling on one command, matching `agent_execution.MAX_COMMAND_TIMEOUT`.
# Ten minutes: a real test suite can take several, and something that has not
# finished in ten is stuck rather than slow.
MAX_TIMEOUT = 600.0
DEFAULT_TIMEOUT = 120.0

# Read this much off a pipe at a time. Output is read incrementally, never
# accumulated whole, so a runaway producer costs a bounded amount of memory
# rather than the machine's.
_READ_CHUNK = 65536

# How long to wait for a killed tree to actually go before giving up on it.
_KILL_GRACE = 5.0

SANDBOX_DIR_NAME = ".tmt_sandbox"

# The environment variables copied from the host, and the whole list of them.
# Everything else is absent from the child by construction rather than by
# filtering, which is the difference between an allow-list and a hope.
#
# Deliberately NOT here: PYTHONPATH (a child python must not inherit TMT's
# import path), USERNAME and USER (the child has no business knowing), APPDATA
# and LOCALAPPDATA (they are where Windows tools keep the user's real caches
# and credentials, and the sandbox home replaces them).
ENV_ALLOWLIST = (
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS",
    "OS", "TZ", "LANG", "LC_ALL", "TERM",
)

# Vendor prefixes whose variables are credentials whatever the rest of the name
# says. AWS_SESSION_TOKEN is already caught by the word "token"; the vendor's
# next variable might not be, and these are the vendors worth refusing on the
# prefix alone.
_SECRET_PREFIXES = (
    "AWS_", "AZURE_", "GCP_", "GOOGLE_", "GITHUB_", "GITLAB_", "OPENAI_",
    "ANTHROPIC_", "OPENROUTER_", "HF_", "HUGGINGFACE_", "NPM_TOKEN",
    "SLACK_", "STRIPE_", "TWILIO_", "DOCKER_", "TMT_",
)

# The program names probed to build the curated PATH. A fixed list: the point
# is that PATH contains the directories these live in and nothing else, so a
# name that is not here is not reachable by name from a child.
PROGRAMS = (
    # interpreters and their tooling
    "python", "python3", "py", "pip", "pip3", "pytest", "tox", "ruff", "black",
    "mypy", "flake8", "poetry", "uv",
    "node", "npm", "npx", "pnpm", "yarn", "tsc", "eslint", "prettier", "jest",
    "vitest", "deno", "bun",
    "cargo", "rustc", "rustup", "rustfmt",
    "go", "gofmt",
    "ruby", "gem", "bundle", "rake",
    "perl", "php", "composer", "lua", "Rscript",
    "java", "javac", "mvn", "gradle", "dotnet",
    # build drivers and compilers
    "make", "cmake", "ninja", "gcc", "g++", "clang", "clang++", "msbuild",
    # version control
    "git",
    # the reads a shell is mostly used for
    "ls", "dir", "cat", "type", "head", "tail", "wc", "find", "file", "stat",
    "sort", "uniq", "cut", "tr", "diff", "grep", "sed", "awk", "which",
    "where", "tree", "echo", "basename", "dirname", "realpath", "xxd", "od",
)

# The Windows system directory is on the curated PATH because a program loaded
# from anywhere else still needs it to find its DLLs, and because `taskkill`
# and `where` live there. It also puts cmd.exe and powershell.exe within a
# child's reach -- which changes nothing, because a model cannot name them
# (agent_policy denies both) and a permitted tool running repository code was
# already outside what LEVEL_POLICY can see.
_WINDOWS_SYSTEM_SUBDIRS = ("System32", "System32/Wbem")


_LEVEL_CACHE = []
_PATH_CACHE = []


def _platform():
    """The platform, read at call time so a test can patch it."""
    return sys.platform


def _os_helper():
    """The OS sandbox helper this host has, or "" when it has none."""
    for prefix, program in _OS_HELPERS:
        if _platform().startswith(prefix):
            return shutil.which(program) or ""
    return ""


def sandbox_level(refresh=False):
    """LEVEL_OS when this host can confine a child, LEVEL_POLICY otherwise.

    Cached, because it is asked once per command and the answer is a property
    of the machine rather than of the command. `refresh` exists for the tests
    and for the one case where it is not: a helper installed mid-session.

    LEVEL_OS is reported only for a helper this module actually invokes -- see
    `os_sandbox_prefix` -- so the level a run reports is the level it ran
    under rather than the best the host could have managed.
    """
    if refresh:
        del _LEVEL_CACHE[:]
    if not _LEVEL_CACHE:
        _LEVEL_CACHE.append(LEVEL_OS if _os_helper() else LEVEL_POLICY)
    return _LEVEL_CACHE[0]


# --- the curated PATH -------------------------------------------------------

def _windows_system_dirs():
    root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or ""
    if not root:
        return []
    found = []
    for tail in _WINDOWS_SYSTEM_SUBDIRS:
        candidate = os.path.join(root, *tail.split("/"))
        if os.path.isdir(candidate):
            found.append(candidate)
    return found


def _git_tool_dirs():
    """Where Git for Windows keeps its coreutils, if this host has them.

    Beyond what the specification asked for, and here for a measured reason:
    on Windows `ls`, `cat`, `grep` and `sed` resolve only when TMT was launched
    from Git Bash. Launched from PowerShell the same installation resolves none
    of them, and a shell tool where `ls` does not exist is not a shell tool.
    Git ships them in a fixed place beside git.exe, so the directory is found
    from where git is rather than guessed at.

    It grants nothing `agent_policy` does not already allow by name: what it
    makes reachable is the list of reads in step 7 of the classification.
    """
    if os.name != "nt":
        return []
    git = shutil.which("git")
    if not git:
        return []
    # .../Git/cmd/git.exe or .../Git/mingw64/bin/git.exe -- walk up towards the
    # installation root looking for the directory coreutils actually live in.
    here = Path(git).resolve().parent
    for base in [here] + list(here.parents)[:3]:
        candidate = base / "usr" / "bin"
        if candidate.is_dir():
            return [str(candidate)]
    return []


def curated_path(refresh=False):
    """A PATH built from where the development tools really are.

    The host's full PATH is never handed to a child. Each name in `PROGRAMS`
    is resolved against the real PATH once, and the directories those
    resolutions landed in -- and no others -- become the child's PATH.

    Note what this does and does not buy on Windows. CreateProcess resolves a
    bare program name against the PATH of the CALLING process, not against the
    environment block being passed to the child, so a curated PATH on its own
    would be decoration. `resolve_program` is what makes it load-bearing: TMT
    resolves the program itself, against this string, and hands the absolute
    path to the launch. Measured on this machine -- a child launched with a
    PATH containing one empty directory still found the host's python.
    """
    if refresh:
        del _PATH_CACHE[:]
    if _PATH_CACHE:
        return _PATH_CACHE[0]
    directories = []
    # Windows reaches the same directory under either case, so C:\WINDOWS\system32
    # and C:\WINDOWS\System32 must not both go on the PATH.
    seen = set()

    def add(directory):
        key = directory.lower() if os.name == "nt" else directory
        if directory and key not in seen:
            seen.add(key)
            directories.append(directory)

    for name in PROGRAMS:
        try:
            found = shutil.which(name)
        except OSError:
            found = None
        if found:
            add(os.path.dirname(os.path.abspath(found)))
    for directory in _git_tool_dirs() + _windows_system_dirs():
        add(directory)
    _PATH_CACHE.append(os.pathsep.join(directories))
    return _PATH_CACHE[0]


def resolve_program(name, path=None):
    """The absolute path of the program `name`, or "" when there is none.

    Written out rather than delegated to `shutil.which` for three measured
    reasons, all Windows-only and all real:

      * `shutil.which` on Python 3.8 prepends the current directory to the
        search on Windows. This repository supports 3.8, and a search that
        consults the workspace first is a search a repository file called
        `python.exe` wins. Python 3.14 no longer does it; the versions in
        between are not something a boundary should be resting on.
      * The extension order matters. PATHEXT is tried before the bare name, so
        `ls.exe` wins over an extensionless `ls` that Windows could not
        execute anyway.
      * Since the batch-file argument fix, `subprocess` will not run `npm`
        from the bare name at all -- measured on this machine, Python 3.14
        raises FileNotFoundError for ["npm", "-v"] and succeeds for the same
        argv with `executable` set to the resolved npm.CMD. Resolving here is
        what makes npm, npx, yarn and every other .CMD shim work.

    A name that already contains a separator is returned as it stands when it
    exists: confining a path is `agent_policy`'s job and there must not be a
    second containment test here.
    """
    name = str(name or "")
    if not name:
        return ""
    if os.path.isabs(name) or os.sep in name or (os.altsep and os.altsep in name):
        return name if os.path.isfile(name) else ""
    if path is None:
        path = curated_path()
    suffixes = [""]
    if os.name == "nt":
        raw = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
        # PATHEXT first, the bare name last: see the docstring.
        suffixes = [part for part in raw.split(os.pathsep) if part] + [""]
    for directory in str(path).split(os.pathsep):
        if not directory:
            continue
        for suffix in suffixes:
            candidate = os.path.join(directory, name + suffix)
            if not os.path.isfile(candidate):
                continue
            if os.name == "nt" or os.access(candidate, os.X_OK):
                return candidate
    return ""


# --- the sandbox home -------------------------------------------------------

def _workspace_key(path):
    """A short stable name for a workspace path.

    The same keying `agent_index` and `agent_memory` use, for the same reason:
    a path is not a filename, and two projects must not share one home.
    """
    text = str(Path(path).resolve())
    if os.name == "nt":
        text = text.lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sandbox_home(root=None):
    """The directory a child gets as its HOME, cache and temp. Not created.

    Under INSTALL_DIR, never in the workspace: it holds npm's cache and pip's
    wheels, which are TMT's own state and would otherwise turn up in the user's
    git status. `agent_config.INSTALL_DIR` is read at call time rather than
    bound on import, so the tests can redirect it.
    """
    if root is None:
        root = agent_config.ROOT_DIR
    return Path(agent_config.INSTALL_DIR) / SANDBOX_DIR_NAME / _workspace_key(root)


# Where each tool is pointed. The names are the tools' own, so a tool that
# honours its documented variable lands here without being asked twice.
_HOME_SUBDIRS = (
    ("home", ("HOME", "USERPROFILE")),
    ("tmp", ("TEMP", "TMP", "TMPDIR")),
    ("cache", ("XDG_CACHE_HOME",)),
    ("npm", ("npm_config_cache",)),
    ("cargo", ("CARGO_HOME",)),
    ("go", ("GOPATH",)),
    ("pip", ("PIP_CACHE_DIR",)),
)

# Offline is tool-level cooperation and nothing more. Every one of these asks a
# well-behaved tool not to reach the network; none of them is a network
# namespace, and a program that ignores them reaches the network anyway. Under
# LEVEL_OS the namespace is what does the work and these are belt and braces.
_OFFLINE_ENV = (
    ("PIP_NO_INDEX", "1"),
    ("PIP_DISABLE_PIP_VERSION_CHECK", "1"),
    ("NPM_CONFIG_OFFLINE", "true"),
    ("NPM_CONFIG_UPDATE_NOTIFIER", "false"),
    ("CARGO_NET_OFFLINE", "true"),
    ("GOPROXY", "off"),
    ("GOFLAGS", "-mod=mod"),
    # Port 9 is discard: a proxy-honouring client connects to nothing rather
    # than hanging on a route that never answers.
    ("http_proxy", "http://127.0.0.1:9"),
    ("https_proxy", "http://127.0.0.1:9"),
    ("HTTP_PROXY", "http://127.0.0.1:9"),
    ("HTTPS_PROXY", "http://127.0.0.1:9"),
    ("no_proxy", ""),
    ("NO_PROXY", ""),
)

# The one variable read back to recover the mode a built environment was built
# for. `launch` needs it to choose an OS sandbox profile and its signature is
# fixed by the specification, so the answer is derived from what `build_env`
# actually set rather than smuggled through in a variable the child would see.
_OFFLINE_MARKER = "GOPROXY"


def name_is_secretish(name):
    """Whether an environment variable's NAME says it holds a credential.

    The vocabulary is `agent_memory`'s, asked through its public `scrub` rather
    than copied: a second list of what a secret looks like is a second thing to
    keep current, and the failure mode of the copy falling behind is a real key
    reaching a child process. `scrub` redacts the right-hand side of an
    assignment whose left-hand side names a credential, so putting the
    variable's name on the left of a benign assignment asks exactly that
    question in exactly that vocabulary.

    The vendor prefixes are the one addition and they are the ones the
    specification names.
    """
    name = str(name or "")
    if not name:
        return False
    upper = name.upper()
    if any(upper.startswith(prefix) for prefix in _SECRET_PREFIXES):
        return True
    cleaned, _ = agent_memory.scrub("%s=placeholder" % name)
    return agent_memory.REDACTED in cleaned


def build_env(root=None, network=NETWORK_OFFLINE):
    """The child's whole environment, built from empty.

    Nothing is inherited. Ten variables are copied by name because a child
    cannot work without them -- where Windows lives, what an executable
    extension is, which locale to speak -- and every one of those names is
    still asked whether it looks like a credential, so extending
    `ENV_ALLOWLIST` carelessly later cannot quietly add one.

    HOME, the temp directory and every package cache point into a TMT-managed
    directory under INSTALL_DIR, keyed per workspace. A child therefore cannot
    read the user's npm credentials, cargo registry token or pip configuration
    by looking where those live, and cannot leave anything of its own in the
    user's home either.
    """
    mode = str(network or NETWORK_OFFLINE).strip().lower()
    env = {}
    for name in ENV_ALLOWLIST:
        value = os.environ.get(name)
        if value is None or name_is_secretish(name):
            continue
        env[name] = value

    env["PATH"] = curated_path()
    # The same correction `agent_execution._command_env` makes, and for the
    # same reason: a python child printing anything non-ASCII into a pipe on
    # Windows dies with a UnicodeEncodeError and would be reported as a failing
    # check when nothing about the code is wrong.
    env["PYTHONIOENCODING"] = "utf-8"

    home = sandbox_home(root)
    for subdir, names in _HOME_SUBDIRS:
        target = home / subdir
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Best effort. The variables are still set: most tools make their
            # own cache directory, and a missing HOME is a complaint the tool
            # makes about itself rather than a reason TMT refuses to run
            # anything at all.
            pass
        for name in names:
            env[name] = str(target)

    if mode == NETWORK_OFFLINE:
        for name, value in _OFFLINE_ENV:
            env[name] = value
    return env


def network_of(env):
    """The mode a built environment was built for. See `_OFFLINE_MARKER`."""
    if env and env.get(_OFFLINE_MARKER) == "off":
        return NETWORK_OFFLINE
    return NETWORK_OPEN


# --- limits -----------------------------------------------------------------

_GIB = 1024 * 1024 * 1024


class Limits:
    """What a child may consume. Every field may be None, meaning "not asked".

    The fields are not the same limit on the two platforms, and the differences
    are why some of them are off by default:

      memory_bytes    Windows: the job's ProcessMemoryLimit, per process.
                      POSIX:   RLIMIT_AS, the address space. A JVM or a Go
                      program reserves far more address space than it commits,
                      so a value tight enough to matter breaks them outright;
                      the default is deliberately generous.
      processes       Windows only: the job's ActiveProcessLimit, which counts
                      the processes in THIS job and is therefore safe to set
                      tight.
      user_processes  POSIX RLIMIT_NPROC. Off by default and it has to stay
                      off by default: it counts every process the user owns,
                      not this command's, so a value that would bound a fork
                      bomb also stops every fork on a desktop that is already
                      busy. That is a limit that blocks rather than degrades,
                      which is the one thing this module must not do.
      cpu_seconds     Off by default. The wall-clock timeout plus the
                      process-tree kill is what actually bounds a runaway, and
                      a CPU limit loose enough not to kill a legitimate
                      parallel build is a limit that can never bind.
      file_bytes      POSIX RLIMIT_FSIZE. A runaway log is the case it exists
                      for. A job object has no equivalent, so asking for it on
                      Windows is reported as not applied rather than silently
                      forgotten.
    """

    __slots__ = ("cpu_seconds", "memory_bytes", "processes", "user_processes",
                 "file_bytes")

    def __init__(self, cpu_seconds=None, memory_bytes=None, processes=None,
                 user_processes=None, file_bytes=None):
        self.cpu_seconds = cpu_seconds
        self.memory_bytes = memory_bytes
        self.processes = processes
        self.user_processes = user_processes
        self.file_bytes = file_bytes

    def __repr__(self):
        return ("Limits(cpu=%r, memory=%r, processes=%r, user_processes=%r, "
                "file=%r)" % (self.cpu_seconds, self.memory_bytes,
                              self.processes, self.user_processes,
                              self.file_bytes))


def default_limits():
    """The limits a command gets when the caller does not choose.

    Platform-aware on purpose: it asks only for what the running platform can
    express, so an ordinary run's `degraded` list is EMPTY and anything in it
    is real news. A `degraded` that always carried "Windows has no maximum
    file size" would be a report nobody reads, which is the same as no report.
    """
    if os.name == "nt":
        return Limits(memory_bytes=4 * _GIB, processes=64)
    return Limits(memory_bytes=4 * _GIB, file_bytes=_GIB)


# A caller meaning "no limits at all" says so with this rather than with None,
# which means "the defaults".
NO_LIMITS = Limits()


class SandboxError(Exception):
    """A launch that could not be attempted, with a sentence saying why.

    Raised by `launch` only. `run_pipeline` and `run_argv` turn it into an
    `ExecOutcome` with no exit code, because a caller wanting an outcome must
    not have to tell two kinds of failure apart in two different ways.
    """


# --- what a run did ---------------------------------------------------------

class ExecOutcome:
    """What running one command or pipeline actually did.

    `exit_code` is None and only None when the command did not run to
    completion -- it was not on the curated PATH, it timed out, the OS refused
    it. That is the distinction `agent_execution.CommandOutcome` is built on
    and it does not weaken here: an exit code is evidence, its absence is the
    absence of evidence, and the two must never be collapsed into a boolean.
    Nothing here reads the output to decide whether the command succeeded.

    `level`, `degraded` and `argv` are additions to the six fields the
    specification names. The first two are here because the alternative is
    silence: `level` is the sandbox level this run actually had rather than the
    one the host could manage, and `degraded` is a sentence per limit that was
    asked for and could not be applied. A reader told neither cannot tell a
    confined run from an unconfined one.
    """

    __slots__ = ("exit_code", "output", "duration", "error", "killed",
                 "truncated", "level", "degraded", "argv", "total_output")

    def __init__(self, exit_code=None, output="", duration=0.0, error="",
                 killed=False, truncated=False, level=None, degraded=(),
                 argv=(), total_output=0):
        self.exit_code = exit_code
        self.output = output
        self.duration = float(duration)
        self.error = error
        self.killed = bool(killed)
        self.truncated = bool(truncated)
        self.level = level or sandbox_level()
        self.degraded = tuple(degraded or ())
        self.argv = tuple(argv or ())
        # What the command actually produced, not what was kept. A truncation
        # notice that cannot say how much was dropped is a notice nobody can
        # act on.
        self.total_output = int(total_output or 0)

    @property
    def ran(self):
        """Whether an exit code was reported. Never inferred from output."""
        return self.exit_code is not None

    @property
    def ok(self):
        return self.exit_code == 0

    def __repr__(self):
        return "ExecOutcome(exit=%r, level=%r, killed=%r)" % (
            self.exit_code, self.level, self.killed)


# --- output, read incrementally and kept tail-biased -------------------------

class _Tail:
    """The last MAX_OUTPUT characters, and an honest count of the rest.

    Bounded by construction: the buffer is trimmed once it passes twice the
    limit, so a producer writing gigabytes costs a fixed 80k characters of
    memory rather than the machine's. `total` is what was actually produced,
    which is the figure a truncation notice has to quote.

    One tail per pipeline, shared by every pump under a lock, so what a reader
    sees is stdout and stderr in the order they arrived -- which is what a
    terminal would have shown them.
    """

    def __init__(self, limit=MAX_OUTPUT):
        self._limit = max(1, int(limit))
        self._text = ""
        self._lock = threading.Lock()
        self.total = 0

    def feed(self, text):
        if not text:
            return
        with self._lock:
            self.total += len(text)
            self._text += text
            if len(self._text) > self._limit * 2:
                self._text = self._text[-self._limit:]

    @property
    def truncated(self):
        return self.total > self._limit

    @property
    def text(self):
        with self._lock:
            return self._text[-self._limit:]


class _Pump(threading.Thread):
    """Reads one pipe into a shared tail, decoding as the bytes arrive.

    Bytes rather than text mode because a text pipe's `read(n)` blocks until n
    characters exist, which is the opposite of incremental. An incremental
    decoder is what lets a multi-byte character split across two reads survive
    -- that is the failure that turns every accented letter in a build log into
    a replacement character.
    """

    daemon = True

    def __init__(self, stream, tail):
        threading.Thread.__init__(self)
        self._stream = stream
        self._tail = tail

    def run(self):
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        pending_cr = False
        # read1 returns whatever one raw read gave, which is the whole point:
        # a plain read(n) waits for n bytes and would hold a build's output
        # back until the buffer filled.
        reader = getattr(self._stream, "read1", None) or self._stream.read
        try:
            while True:
                chunk = reader(_READ_CHUNK)
                if not chunk:
                    text = decoder.decode(b"", True)
                    if pending_cr:
                        text = "\r" + text
                    self._tail.feed(text)
                    return
                text = decoder.decode(chunk)
                if pending_cr:
                    text = "\r" + text
                    pending_cr = False
                if text.endswith("\r"):
                    # A CRLF split across two reads would otherwise arrive as a
                    # stray carriage return in the middle of a line.
                    text = text[:-1]
                    pending_cr = True
                self._tail.feed(text.replace("\r\n", "\n"))
        except (OSError, ValueError):
            # The pipe went away underneath us, which is what a killed tree
            # looks like from here. Whatever was read is kept.
            return
        finally:
            try:
                self._stream.close()
            except (OSError, ValueError):
                pass


# --- Windows job objects ----------------------------------------------------
#
# A job object is the only thing on Windows that makes "kill the process tree"
# a guarantee rather than a walk of parent pids a process can escape by
# outliving its parent. `taskkill /F /T` is kept AS WELL AS the job rather than
# instead of it, because the two fail in different directions: taskkill needs
# the tree still to be a tree, and the job needs the assignment to have
# succeeded.
#
# The honest gap: `subprocess` gives no way to create a process suspended and
# hand back its thread handle, so the child is assigned to the job immediately
# AFTER CreateProcess returns rather than before it runs its first instruction.
# A child that spawned a grandchild inside that window would leave the
# grandchild outside the job. It is microseconds against a process start, and
# taskkill /T is the second net under it, but it is a window and it is not
# closed.

_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_EXTENDED_LIMIT_INFORMATION = 9
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001

_KERNEL32 = []


def _win32():
    """kernel32 with the prototypes this module uses, or None off Windows.

    argtypes and restypes are declared rather than left to ctypes' defaults
    because a HANDLE is 64 bits and ctypes' default return type is a 32-bit
    int: without them a job handle is silently truncated and every later call
    on it fails for a reason nobody can see.
    """
    if os.name != "nt":
        return None
    if _KERNEL32:
        return _KERNEL32[0]
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    _KERNEL32.append(kernel32)
    return kernel32


def _extended_limit_type():
    import ctypes

    class IoCounters(ctypes.Structure):
        _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong)]

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", ctypes.c_ulong),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", ctypes.c_ulong),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", ctypes.c_ulong),
                    ("SchedulingClass", ctypes.c_ulong)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits),
                    ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    return ExtendedLimits


def _create_job(limits, degraded):
    """A job object carrying `limits`, or None when one could not be made.

    Every failure here is recorded and returns None, and the caller launches
    anyway. A build that will not start because a job object could not be
    created is worse than a build that runs without one.
    """
    kernel32 = _win32()
    if kernel32 is None:
        return None
    import ctypes

    try:
        handle = kernel32.CreateJobObjectW(None, None)
    except OSError as error:
        degraded.append("no job object could be created (%s), so process and "
                        "memory limits are not applied and a kill falls back "
                        "to taskkill" % error)
        return None
    if not handle:
        degraded.append("no job object could be created (Windows error %d), so "
                        "process and memory limits are not applied and a kill "
                        "falls back to taskkill" % ctypes.get_last_error())
        return None

    info = _extended_limit_type()()
    # KILL_ON_JOB_CLOSE is the guarantee: when TMT's handle goes -- on
    # kill_tree, on garbage collection, or because TMT's own process ended --
    # everything still in the job dies with it. It is what makes "nothing
    # survives the session" true rather than hoped for.
    flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if limits.processes:
        info.BasicLimitInformation.ActiveProcessLimit = int(limits.processes)
        flags |= _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    if limits.cpu_seconds:
        # 100-nanosecond units, which is what a job object counts in.
        info.BasicLimitInformation.PerProcessUserTimeLimit = int(
            float(limits.cpu_seconds) * 10000000)
        flags |= _JOB_OBJECT_LIMIT_PROCESS_TIME
    if limits.memory_bytes:
        info.ProcessMemoryLimit = int(limits.memory_bytes)
        flags |= _JOB_OBJECT_LIMIT_PROCESS_MEMORY
    info.BasicLimitInformation.LimitFlags = flags

    ok = kernel32.SetInformationJobObject(
        handle, _JOB_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
        ctypes.sizeof(info))
    if not ok:
        degraded.append("the job object's limits could not be set (Windows "
                        "error %d); it is still used for the process-tree kill"
                        % ctypes.get_last_error())
    if limits.file_bytes:
        degraded.append("a maximum file size is not something a Windows job "
                        "object can express, so it was not applied")
    if limits.user_processes:
        degraded.append("a per-user process limit has no Windows equivalent; "
                        "the job's process limit is what bounds this command")
    return handle


def _assign_job(job, popen, degraded):
    """Put the child in the job. False, with a reason, when it could not go."""
    kernel32 = _win32()
    if kernel32 is None:
        return False
    import ctypes

    handle = getattr(popen, "_handle", None)
    opened = None
    if handle is None:
        opened = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, int(popen.pid))
        handle = opened
    if not handle:
        degraded.append("the child could not be opened for job assignment, so "
                        "the process-tree kill falls back to taskkill")
        return False
    ok = kernel32.AssignProcessToJobObject(job, int(handle))
    error = ctypes.get_last_error()
    if opened:
        kernel32.CloseHandle(opened)
    if not ok:
        degraded.append("the child could not be assigned to the job object "
                        "(Windows error %d), so the process-tree kill falls "
                        "back to taskkill" % error)
    return bool(ok)


# --- POSIX limits -----------------------------------------------------------

def _posix_preexec(limits, degraded):
    """A callable applying rlimits in the child, or None when none are asked.

    Only syscalls run between fork and exec -- setrlimit and nothing else --
    because `preexec_fn` runs in a forked copy of a multi-threaded process,
    where anything that could take a lock can deadlock instead.

    A setrlimit that fails inside the child is swallowed there and cannot be
    reported back: `preexec_fn` has no channel to the parent. The soft values
    are therefore clamped against the hard limits HERE, in the parent, where a
    refusal is visible, which leaves the child's call very little to fail at.
    That is the honest extent of it -- a failure after the fork is silent.
    """
    if os.name == "nt":
        return None
    try:
        import resource
    except ImportError:
        if any((limits.cpu_seconds, limits.memory_bytes,
                limits.user_processes, limits.file_bytes)):
            degraded.append("this platform has no `resource` module, so no "
                            "process resource limits were applied")
        return None

    # `processes` is a ceiling on THIS command's descendants, and POSIX has no
    # rlimit that expresses it -- RLIMIT_NPROC counts every process the user
    # owns, which is a different question and is asked separately below as
    # `user_processes`. It is named here rather than skipped because this
    # module's promise is that a caller asking for a limit the platform cannot
    # apply is TOLD so; the Windows path keeps that promise through the job
    # object's ActiveProcessLimit, and a silent omission on one platform is
    # the promise being one-directional.
    if limits.processes:
        degraded.append("a limit on this command's own descendants is a job "
                        "object and POSIX has no equivalent, so none was "
                        "applied")

    wanted = []
    for attribute, constant, description in (
            ("cpu_seconds", "RLIMIT_CPU", "a CPU-time limit"),
            ("memory_bytes", "RLIMIT_AS", "an address-space limit"),
            ("user_processes", "RLIMIT_NPROC", "a per-user process limit"),
            ("file_bytes", "RLIMIT_FSIZE", "a maximum file size")):
        value = getattr(limits, attribute)
        if not value:
            continue
        which = getattr(resource, constant, None)
        if which is None:
            degraded.append("%s is not available on this platform" % description)
            continue
        try:
            _soft, hard = resource.getrlimit(which)
        except (OSError, ValueError) as error:
            degraded.append("%s could not be read (%s) and was not applied"
                            % (description, error))
            continue
        value = int(value)
        if hard != resource.RLIM_INFINITY and value > hard:
            degraded.append("%s was asked for at %d and the hard limit is %d, "
                            "so the lower one was used"
                            % (description, value, hard))
            value = hard
        wanted.append((which, value))

    if not wanted:
        return None

    def apply_limits():
        for which, value in wanted:
            try:
                resource.setrlimit(which, (value, value))
            except (OSError, ValueError):
                # Nothing can be reported from here. See the docstring.
                pass

    return apply_limits


# --- the OS sandbox ---------------------------------------------------------

def os_sandbox_prefix(root, cwd, network, degraded=None):
    """The argv prefix that confines a child, or [] when there is none.

    WRITTEN FROM THE DOCUMENTED BEHAVIOUR OF bwrap(1) AND sandbox-exec(1) AND
    NEVER EXECUTED ON THE MACHINE THIS MODULE WAS WRITTEN ON, which is Windows
    and has neither. The profiles are spelled out rather than assembled
    cleverly so that a reader on Linux or macOS can check them against what
    they know. When one cannot be built, [] comes back and the run reports
    LEVEL_POLICY -- the level a run names is the level it ran under, and a
    silent downgrade would be exactly the fabrication this repository forbids.

    Under bwrap the whole filesystem is bound read-only and the workspace and
    the sandbox home are bound back read-write, so a write outside them fails
    in the kernel. Under `offline`, `--unshare-net` puts the child in an empty
    network namespace, which is a real boundary rather than the environment
    variables `build_env` sets.
    """
    if degraded is None:
        degraded = []
    helper = _os_helper()
    if not helper:
        return []
    root = str(Path(root).resolve())
    home = str(sandbox_home(root))
    cwd = str(Path(cwd).resolve())
    offline = str(network or NETWORK_OFFLINE).lower() == NETWORK_OFFLINE
    platform = _platform()

    if platform.startswith("linux"):
        argv = [helper, "--die-with-parent",
                "--unshare-pid", "--unshare-uts", "--unshare-ipc",
                # Read-only first, then the two writable binds over the top:
                # bwrap applies its mounts in the order they are given.
                "--ro-bind", "/", "/",
                "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
                "--bind", root, root,
                "--bind", home, home,
                "--chdir", cwd]
        if offline:
            argv.append("--unshare-net")
        return argv

    if platform.startswith("darwin"):
        for path in (root, home):
            if '"' in path or "\\" in path:
                degraded.append("the workspace path holds a character a "
                                "sandbox-exec profile cannot quote, so the "
                                "command ran under policy alone")
                return []
        rules = ['(version 1)', '(allow default)', '(deny file-write*)',
                 '(allow file-write* (subpath "%s") (subpath "%s") '
                 '(literal "/dev/null") (literal "/dev/stdout") '
                 '(literal "/dev/stderr"))' % (root, home)]
        if offline:
            rules.append('(deny network*)')
        return [helper, "-p", "".join(rules)]

    return []


# --- the handle -------------------------------------------------------------

_LIVE = weakref.WeakSet()
_LIVE_LOCK = threading.Lock()


class Process:
    """A launched child, plus the one thing `subprocess` does not give you.

    `kill_tree()` is the guarantee people rely on: after it returns, nothing
    the command started is still running. It is not `Popen.kill()`, which stops
    one process and leaves its children reparented and alive.
    """

    def __init__(self, popen, job=None, degraded=(), level=None, argv=(),
                 timeout=None):
        self._popen = popen
        self._job = job
        self._killed = False
        self.degraded = list(degraded or ())
        self.level = level or sandbox_level()
        self.argv = tuple(argv or ())
        self.timeout = timeout
        with _LIVE_LOCK:
            _LIVE.add(self)

    # A Popen-like surface, so a caller holding one can use the other.
    @property
    def pid(self):
        return self._popen.pid

    @property
    def returncode(self):
        return self._popen.returncode

    @property
    def stdin(self):
        return self._popen.stdin

    @property
    def stdout(self):
        return self._popen.stdout

    @property
    def stderr(self):
        return self._popen.stderr

    @property
    def killed(self):
        return self._killed

    def poll(self):
        return self._popen.poll()

    def wait(self, timeout=None):
        """Wait, defaulting to the deadline this process was launched with."""
        if timeout is None:
            timeout = self.timeout
        return self._popen.wait(timeout=timeout)

    def kill_tree(self):
        """Stop this process and everything it started. Never raises.

        The order matters on Windows and is the reason this is not two lines:
        `taskkill /T` walks the tree by parent pid, so it has to run while the
        tree is still a tree. Terminating the job first orphans the
        grandchildren and taskkill can no longer find them.
        """
        self._killed = True
        if os.name == "nt":
            self._kill_windows()
        else:
            self._kill_posix()
        try:
            self._popen.kill()
        except (OSError, ValueError):
            pass
        try:
            self._popen.wait(timeout=_KILL_GRACE)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        self._close_streams()

    def _kill_windows(self):
        if self._popen.poll() is None:
            try:
                # No shell, argv only, and its output discarded: this is a
                # cleanup, and a cleanup that printed would print past the live
                # region, which this repository does not permit.
                subprocess.run(["taskkill", "/F", "/T", "/PID",
                                str(self._popen.pid)],
                               stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               timeout=_KILL_GRACE)
            except (OSError, subprocess.SubprocessError):
                pass
        self._close_job()

    def _kill_posix(self):
        import signal

        try:
            os.killpg(os.getpgid(self._popen.pid), signal.SIGKILL)
        except (OSError, AttributeError):
            try:
                self._popen.kill()
            except (OSError, ValueError):
                pass

    def _close_job(self):
        job, self._job = self._job, None
        if not job:
            return
        kernel32 = _win32()
        if kernel32 is None:
            return
        try:
            kernel32.TerminateJobObject(job, 1)
            # KILL_ON_JOB_CLOSE fires here too, which is what makes a dropped
            # handle safe rather than a leaked process.
            kernel32.CloseHandle(job)
        except OSError:
            pass

    def _close_streams(self):
        for stream in (self._popen.stdin, self._popen.stdout,
                       self._popen.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def release(self):
        """Let go of the job handle now this process has finished.

        Called once a wait has come back, so a completed command does not hold
        a kernel object for as long as something happens to keep the Python
        object alive.

        It also reaps what the command ORPHANED. A child that exits while a
        grandchild of its own runs on leaves nothing for a tree walk to follow
        -- there is no tree, only a stray -- and on Windows the job object's
        KILL_ON_JOB_CLOSE catches it as the handle goes. POSIX had no
        equivalent and the stray simply survived, which is the one shape of
        "nothing may be left running" that was not true there.

        Signalling the group is safe precisely because `start_new_session`
        gave this command a session of its own: the group holds this command's
        descendants and nothing else, so there is no other process it could
        reach. A group that is already empty raises ProcessLookupError, which
        is the ordinary case and is ignored.

        WRITTEN FROM THE DOCUMENTED BEHAVIOUR OF setsid(2) AND killpg(2) AND
        NEVER EXECUTED: this repository's only host is Windows, where the
        branch cannot be reached. Treat it as unverified.
        """
        self._close_job()
        if os.name != "nt":
            self._reap_posix_group()

    def _reap_posix_group(self):
        """Kill anything left in this command's process group. Never raises."""
        try:
            os.killpg(os.getpgid(self._popen.pid), signal.SIGKILL)
        except Exception:
            # Already gone, never started, or a platform without process
            # groups. Nothing here is worth failing a completed command over.
            pass

    def __del__(self):
        # A dropped handle must not leave a process running. On Windows the
        # job's KILL_ON_JOB_CLOSE does it; POSIX needs the group signal.
        try:
            if self._popen.poll() is None:
                self._kill_posix()
        except Exception:
            pass
        try:
            self._close_job()
        except Exception:
            pass


def live_processes():
    """Every process this module started that has not been released."""
    with _LIVE_LOCK:
        return [process for process in _LIVE]


def kill_all():
    """Kill every process this module started that is still running.

    The session calls it on the way out. Registered with `atexit` as well,
    because "a job that outlives the session" is the thing this has to make
    impossible, and an exception on the way out must not be what decides it.
    """
    for process in live_processes():
        try:
            if process.poll() is None:
                process.kill_tree()
        except Exception:
            pass


atexit.register(kill_all)


# --- launching --------------------------------------------------------------

def _clamp_timeout(timeout):
    try:
        seconds = float(timeout) if timeout is not None else DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        seconds = DEFAULT_TIMEOUT
    return max(0.1, min(seconds, MAX_TIMEOUT))


def launch(argv, cwd=None, env=None, timeout=None, stdin=None, stdout=None,
           stderr=None, limits=None):
    """Start one process under everything this module enforces.

    `argv` is a list and is never a string. There is no shell on this path and
    nothing in this module ever asks `subprocess` for one, so no part of a
    command is interpreted by anything but the program named in its first
    element. The program is resolved by TMT
    against the curated PATH and handed to the launch as an absolute path,
    which is what makes the curated PATH mean anything on Windows -- see
    `curated_path` -- and is also what makes `npm` work there at all.

    `stdin=None` means CLOSED, not inherited, and that is deliberate: a
    command run on a model's behalf must never be able to read the terminal
    the user is typing into, and an interactive tool that would have waited
    forever gets EOF and fails in a second instead. A caller wanting to feed
    the child passes a pipe or a file.

    Raises `SandboxError` when there is nothing to launch: an empty argv, a
    program that is not on the curated PATH, or an OS refusal. Everything that
    CAN run, runs -- a limit that could not be applied is recorded on the
    handle's `degraded` and never stops the launch.
    """
    argv = [str(part) for part in (argv or ())]
    if not argv:
        raise SandboxError("no command was given")
    limits = default_limits() if limits is None else limits
    degraded = []

    root = agent_config.ROOT_DIR
    working = str(Path(cwd).resolve()) if cwd else str(Path(root).resolve())
    if env is None:
        env = build_env(root, NETWORK_OFFLINE)
    seconds = _clamp_timeout(timeout)

    program = resolve_program(argv[0], env.get("PATH") or curated_path())
    if not program:
        raise SandboxError(
            "'%s' was not found on the sandbox PATH. TMT builds that PATH from "
            "the development tools it can find and installs nothing; install "
            "it yourself, or use a command this project already provides."
            % argv[0])

    level = sandbox_level()
    prefix = []
    if level == LEVEL_OS:
        prefix = os_sandbox_prefix(root, working, network_of(env), degraded)
        if not prefix:
            # The helper exists and its profile could not be built. Reporting
            # LEVEL_OS here would be a claim about confinement that did not
            # happen.
            level = LEVEL_POLICY
    if prefix:
        # The inner program is already absolute, so the helper execs exactly
        # what TMT resolved rather than repeating the lookup with its own PATH.
        full_argv = prefix + [program] + argv[1:]
        executable = prefix[0]
    else:
        full_argv = list(argv)
        executable = program

    job = None
    kwargs = {}
    if os.name == "nt":
        job = _create_job(limits, degraded)
        # Its own process group, so a Ctrl-C in TMT's console is not delivered
        # to the child behind the loop's back; the kill is by handle instead.
        kwargs["creationflags"] = _CREATE_NEW_PROCESS_GROUP
    else:
        # setsid in the child, done by subprocess itself rather than in
        # preexec_fn, so the process group exists even when no rlimit is asked
        # for and there is therefore no preexec_fn at all.
        kwargs["start_new_session"] = True
        preexec = _posix_preexec(limits, degraded)
        if preexec is not None:
            kwargs["preexec_fn"] = preexec

    if stdin is None:
        stdin = subprocess.DEVNULL

    try:
        popen = subprocess.Popen(
            full_argv, executable=executable, cwd=working, env=env,
            stdin=stdin, stdout=stdout, stderr=stderr, **kwargs)
    except OSError as error:
        if job is not None:
            kernel32 = _win32()
            if kernel32 is not None:
                try:
                    kernel32.CloseHandle(job)
                except OSError:
                    pass
        raise SandboxError("'%s' could not be started (%s)" % (argv[0], error))

    if job is not None and not _assign_job(job, popen, degraded):
        kernel32 = _win32()
        if kernel32 is not None:
            try:
                kernel32.CloseHandle(job)
            except OSError:
                pass
        job = None

    return Process(popen, job=job, degraded=degraded, level=level, argv=argv,
                   timeout=seconds)


# --- pipelines --------------------------------------------------------------

def _stage_parts(command):
    """(argv, redirects) from a Command, or from a plain argv list.

    Duck-typed rather than imported from `agent_shell`, so this module has no
    import-time dependency on the parser and a test can drive a pipeline with
    two plain lists.
    """
    argv = getattr(command, "argv", None)
    if argv is None:
        return [str(part) for part in command], ()
    return ([str(part) for part in argv],
            tuple(getattr(command, "redirects", ()) or ()))


def _redirect_target(target, cwd):
    """A redirect's destination as an absolute path.

    The caller has already confined it -- that is `agent_bash`'s job, through
    `agent_file_ops.safe_path` -- and a second containment test here would be a
    second answer to a question that must have exactly one.
    """
    path = Path(str(target))
    if not path.is_absolute():
        path = Path(cwd) / path
    return str(path)


def _open_redirects(redirects, cwd, opened):
    """The stdin/stdout/stderr this stage wants, from its redirect list.

    Applied in order with the last one winning, which is what a shell does:
    `cmd > a > b` writes to b.
    """
    plan = {"stdin": None, "stdout": None, "stderr": None}
    modes = {">": ("stdout", "wb"), ">>": ("stdout", "ab"),
             "<": ("stdin", "rb"), "2>": ("stderr", "wb")}
    for redirect in redirects:
        kind = getattr(redirect, "kind", "")
        target = getattr(redirect, "target", "")
        if kind == "2>&1":
            plan["stderr"] = subprocess.STDOUT
            continue
        if kind not in modes:
            raise SandboxError("%r is not a redirect this runner knows" % kind)
        if not target:
            raise SandboxError("a %s redirect has no target" % kind)
        slot, mode = modes[kind]
        try:
            handle = open(_redirect_target(target, cwd), mode)
        except OSError as error:
            raise SandboxError("%s %s could not be opened (%s)"
                               % (kind, target, error))
        opened.append(handle)
        plan[slot] = handle
    return plan


def run_pipeline(commands, cwd=None, env=None, timeout=None, limits=None,
                 network=NETWORK_OFFLINE):
    """Run one pipeline and report exactly what happened.

    The stages are wired with real pipes and TMT never hands a string to a
    shell: `a | b` reached this function as parsed objects and leaves it as
    processes with file descriptors between them.

    One timeout covers the whole pipeline, because a pipeline is one thing the
    user asked for. On expiry EVERY stage's tree is killed, not only the one
    that was slow -- a first stage still producing into a dead second stage is
    exactly the process nobody would notice was left running.

    The exit code is the LAST stage's, which is what a shell reports. Every
    stage's stderr is captured, because a failure in the middle of a pipeline
    would otherwise be invisible. The outcome's `argv` is the FIRST stage's,
    which is the one a reader recognises the command by.
    """
    stages = list(commands or ())
    if not stages:
        return ExecOutcome(error="no command was given")

    limits = default_limits() if limits is None else limits
    root = agent_config.ROOT_DIR
    working = str(Path(cwd).resolve()) if cwd else str(Path(root).resolve())
    if env is None:
        env = build_env(root, network)
    seconds = _clamp_timeout(timeout)

    tail = _Tail()
    opened = []
    pumps = []
    processes = []
    degraded = []
    level = sandbox_level()
    first_argv = _stage_parts(stages[0])[0]
    started = time.monotonic()

    try:
        upstream = None
        for index, command in enumerate(stages):
            argv, redirects = _stage_parts(command)
            if not argv:
                raise SandboxError("a stage of the pipeline is empty")
            plan = _open_redirects(redirects, working, opened)
            last = index == len(stages) - 1

            stdin = plan["stdin"] or upstream or subprocess.DEVNULL
            stdout = plan["stdout"] if plan["stdout"] is not None else subprocess.PIPE
            stderr = plan["stderr"] if plan["stderr"] is not None else subprocess.PIPE

            process = launch(argv, cwd=working, env=env, timeout=seconds,
                             stdin=stdin, stdout=stdout, stderr=stderr,
                             limits=limits)
            processes.append(process)
            degraded.extend(process.degraded)
            level = process.level

            if upstream is not None:
                # The parent's copy of the upstream pipe has to go, or the
                # downstream stage never sees EOF and the pipeline hangs.
                try:
                    upstream.close()
                except (OSError, ValueError):
                    pass
                upstream = None

            if process.stderr is not None:
                pumps.append(_Pump(process.stderr, tail))
            if process.stdout is not None:
                if last:
                    pumps.append(_Pump(process.stdout, tail))
                else:
                    upstream = process.stdout

        for pump in pumps:
            pump.start()

        killed = False
        error = ""
        deadline = started + seconds
        for process in processes:
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                killed = True
                error = ("it did not finish within %d seconds and its whole "
                         "process tree was stopped" % int(seconds))
                break
            except KeyboardInterrupt:
                # Ctrl-C reaches the loop that already catches it, but nothing
                # this module started may survive on the way past.
                for other in processes:
                    other.kill_tree()
                raise

        if killed:
            for process in processes:
                process.kill_tree()

        for pump in pumps:
            pump.join(timeout=_KILL_GRACE)

        exit_code = None
        if not killed:
            code = processes[-1].returncode
            exit_code = None if code is None else int(code)

        for process in processes:
            process.release()

        return ExecOutcome(
            exit_code=exit_code, output=tail.text,
            duration=time.monotonic() - started, error=error, killed=killed,
            truncated=tail.truncated, level=level, degraded=degraded,
            argv=first_argv, total_output=tail.total)

    except SandboxError as refusal:
        for process in processes:
            process.kill_tree()
        return ExecOutcome(output=tail.text,
                           duration=time.monotonic() - started,
                           error=str(refusal), level=level, degraded=degraded,
                           truncated=tail.truncated, argv=first_argv,
                           total_output=tail.total)
    finally:
        for handle in opened:
            try:
                handle.close()
            except (OSError, ValueError):
                pass


def run_argv(argv, cwd=None, env=None, timeout=None, limits=None,
             network=NETWORK_OFFLINE):
    """One argv list, run as a one-stage pipeline.

    A convenience beyond what the specification names, and it is here to stop a
    second output-reading loop being written: `agent_execution.run_command` and
    anything else holding a plain argv list wants exactly this, and the way to
    get it wrong is to reimplement the incremental read and the tail bias.
    """
    return run_pipeline([list(argv or ())], cwd=cwd, env=env, timeout=timeout,
                        limits=limits, network=network)
