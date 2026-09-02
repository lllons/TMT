"""The verification runner, approved desktop application launching, and the
extension-to-command table both of those outlived.

TMT used to run code here: `run_file` took a path, looked its extension up in
`RUNNERS`, and ran that one file for ten seconds. It is gone, and so is
`run_python` beside it and the `.c`/`.cpp`/`.java` compile-and-run paths. What
replaced it is the `bash` action, which runs a real command line -- pipes,
`&&`, redirects and all -- under a parser TMT owns, a policy that decides what
may run, and a sandbox that is the one place in the program a process is
created.

`RUNNERS` stays. It is no longer a dispatcher, it is a TABLE: the answer to
"what command runs a file with this extension", which is a fact about the
ecosystem rather than about how TMT executes anything. What reads it is
`agent_actions.adopt_verb`, which turns a legacy `run_file` into the `bash`
command it meant, and it is the only thing that does -- the prompt teaches the
model the same facts in its own words, and a file type that is not in here is
a file TMT will not guess a command for.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path
import agent_config
from agent_file_ops import safe_path

# What runs a file of each type. A table of facts, read and never dispatched
# through: see the module docstring. An extension that is not here has no
# known runner, and the honest answer to a file of that type is to say so --
# guessing a command for it would be inventing one.
RUNNERS = {
    ".py": ["python", "{file}"], ".js": ["node", "{file}"], ".rb": ["ruby", "{file}"],
    ".php": ["php", "{file}"], ".lua": ["lua", "{file}"], ".pl": ["perl", "{file}"],
    ".r": ["Rscript", "{file}"], ".go": ["go", "run", "{file}"],
    ".ts": ["npx", "--yes", "ts-node", "{file}"],
}

APP_REGISTRY = {
    "notepad": {"exe": "notepad.exe", "description": "Windows Notepad — opens .txt and other text files", "accepts_path": True, "accepts_url": False},
    "explorer": {"exe": "explorer.exe", "description": "Windows File Explorer — opens folder with the file selected and highlighted", "accepts_path": True, "accepts_url": False, "path_prefix": "/select,"},
}

def open_app(app_name, file_path=None, url=None):
    key = app_name.lower().strip()
    if key not in APP_REGISTRY:
        return f"App '{app_name}' is not permitted. Permitted apps: {', '.join(APP_REGISTRY)}"
    cfg = APP_REGISTRY[key]
    cmd = [cfg["exe"]]
    if file_path:
        if not cfg["accepts_path"]:
            return f"'{key}' does not accept file paths."
        p = safe_path(file_path)
        if not p.exists():
            return f"File not found: {file_path}"
        prefix = cfg.get("path_prefix", "")
        cmd.append(f"{prefix}{p}" if prefix else str(p))
    elif url:
        if not cfg["accepts_url"]:
            return f"'{key}' does not accept URLs."
        cmd.append(url)
    try:
        subprocess.Popen(cmd)
        target = file_path or url or ""
        return f"Opened {target} in {key}" if target else f"Launched {key}"
    except FileNotFoundError:
        return f"Could not find '{cfg['exe']}' — is it installed and on PATH?"
    except OSError as error:
        return f"Error launching {key}: {error}"


# --- running a project's own verification commands --------------------------
#
# The verification engine's runner: a timeout measured in minutes rather than
# seconds, the real exit code rather than a string, and the ability to run a
# command that is not a file at all.
#
# It deliberately does NOT add a way to execute things. It never had its own
# one -- it used `subprocess.run` where `run_file` used `subprocess.run` -- and
# now that there is exactly one place in TMT where a process is created, it
# uses that: `agent_sandbox.run_argv`. So a verification command and a model's
# `bash` command are started the same way, read their output the same way and
# get the same process-tree kill on timeout, and there is one piece of code to
# get right instead of two.
#
# The one rule that matters is stated here because everything else rests on it:
#
#   **THE COMMAND IS ALWAYS AN ARGV LIST AND THERE IS NEVER A SHELL.**
#
# `shell=True` appears nowhere on this path, and a repository-defined command
# is therefore never a string that gets interpreted. `agent_verify_discovery`
# builds argv from a fixed table of runner shapes plus a NAME taken from the
# project -- a package.json script, a Makefile target -- and validates that
# name before it goes anywhere near here. So a package.json containing
# `"test": "vitest && rm -rf /"` is run as `npm run test`, by npm, with npm's
# own semantics; TMT never parses or executes that string itself. There is a
# test that greps this module and the verify modules for `shell=True`.

# The ceiling on one verification command. Ten minutes: a real test suite can
# take several, and something that has not finished in ten is stuck rather
# than slow. It is a ceiling on the caller's request, not the default -- the
# caller passes what it thinks the check needs.
MAX_COMMAND_TIMEOUT = 600.0
DEFAULT_COMMAND_TIMEOUT = 300.0

# How much of a command's output is captured. Larger than `_run_cmd`'s 2000,
# because a failing test suite's useful part is its tail and 2000 characters of
# a pytest run is sometimes not even the summary. `agent_verify` trims it again
# for the model; this is what is read off the pipe.
MAX_COMMAND_OUTPUT = 40000


class CommandOutcome:
    """What running one command actually did.

    `exit_code` is None and only None when the command did not run to
    completion -- it was not on PATH, it timed out, the OS refused it. That is
    the distinction the whole verification engine is built on: an exit code is
    evidence, and its absence is the absence of evidence, and the two must
    never be collapsed into a boolean.
    """

    __slots__ = ("argv", "exit_code", "output", "duration", "error")

    def __init__(self, argv, exit_code=None, output="", duration=0.0, error=""):
        self.argv = tuple(argv or ())
        self.exit_code = exit_code
        self.output = output
        self.duration = float(duration)
        self.error = error

    @property
    def ran(self):
        return self.exit_code is not None

    @property
    def ok(self):
        return self.exit_code == 0

    def __repr__(self):
        return "CommandOutcome(%s, exit=%r)" % (" ".join(self.argv), self.exit_code)


def command_available(argv):
    """The resolved path of a command's executable, or "" when there is none.

    Asked before running rather than inferred from a failure afterwards,
    because "the tool is not installed" and "the tool ran and found problems"
    are different answers and only one of them is about the code. TMT never
    installs anything to make this come back true -- see the verification
    rules; a missing tool is reported, never fixed behind the user's back.
    """
    if not argv:
        return ""
    return shutil.which(str(argv[0])) or ""


def _command_env():
    """The child's environment: this process's, plus one correction.

    Inherited rather than constructed, so a project's virtualenv, PATH and
    tool configuration reach the command exactly as they would if the user ran
    it. The single addition is `PYTHONIOENCODING`, and it is a correction
    rather than a policy: a Python child printing anything non-ASCII into a
    pipe on Windows dies with a UnicodeEncodeError and would be reported as a
    failing check when nothing about the code is wrong. It is only set when
    the environment has not already made a choice.

    NOT `agent_sandbox.build_env`, and that is the one thing this path keeps
    for itself now that it launches through the sandbox. The sandbox builds an
    environment from nothing and a PATH curated from where the development
    tools really are, which is right for a command a MODEL wrote and wrong
    here: what runs here is the project's OWN verification command, chosen by
    `agent_verify_discovery` from the project's own manifests, and it has to
    see the project's own virtualenv and tool configuration or the answer it
    gives is about a machine nobody has. `command_available` also asks
    `shutil.which` against this PATH, and a check reported as "not installed"
    because the child was handed a different PATH from the one it was looked up
    on would be evidence about TMT rather than about the code.

    What the sandbox gives this path is process CREATION -- one primitive, one
    timeout, one process-tree kill -- and that is what it was wanted for.

    The honest cost of that, stated rather than left to be discovered: an
    inherited environment includes the user's own credentials, so a project's
    verification command sees them. That was true of this path before the
    sandbox existed and it is true now; what has changed is that a command a
    MODEL writes no longer works this way, because `bash` goes through
    `agent_sandbox.build_env`, which builds an environment from nothing and
    copies no variable whose name looks like a secret. Whether verification
    should follow it is a real question and is deliberately not answered
    inside a wiring change: narrowing the environment a user's own test suite
    runs under is a behaviour change that belongs to whoever can measure it.
    """
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _launcher():
    """`agent_sandbox`, or None when this install cannot see it.

    Imported at call time for the reason `agent_actions._run_tool` gives: an
    editable install freezes its module list at install time, so a module added
    to the source tree is invisible until pyproject.toml catches up, and the
    failure that produces has to be a sentence rather than an ImportError at
    the top of this file that stops TMT starting at all.

    There is deliberately no `subprocess.run` fallback here. A second way of
    creating a process is exactly what this change removed, and one that
    appeared only when the sandbox was missing would be the path nobody tests
    and nobody remembers -- live on precisely the installs where the sandbox is
    not doing its job. A verification that cannot run reports that it did not
    run, which is what `CommandOutcome` was built to be able to say.
    """
    try:
        import agent_sandbox
    except Exception:
        return None
    return agent_sandbox


def run_command(argv, timeout=DEFAULT_COMMAND_TIMEOUT, cwd=None):
    """Run one argv list in the workspace and report exactly what happened.

    Never a shell, never a string. `argv` is a list and is handed to
    `agent_sandbox.run_argv` as a list, so no part of it is interpreted by
    anything but the program named in its first element.

    `cwd` defaults to the workspace root, which is the same boundary every
    other execution in TMT runs inside. A caller may narrow it to a directory
    under the root; anything outside is refused, because a verification
    command that could pick its own working directory would be a way around
    the sandbox the file tools enforce.

    The contract every caller depends on is unchanged and is the whole reason
    this is careful: `exit_code` is set when and only when the command ran to
    completion. A command that could not start, could not be launched at all,
    or was stopped on the clock comes back with `exit_code` None and a sentence
    in `error`. An exit code is evidence; its absence is the absence of
    evidence, and the two must never collapse into a boolean.
    """
    argv = [str(part) for part in (argv or ())]
    if not argv:
        return CommandOutcome((), error="no command was given")
    seconds = max(1.0, min(float(timeout or DEFAULT_COMMAND_TIMEOUT),
                           MAX_COMMAND_TIMEOUT))
    root = Path(agent_config.ROOT_DIR)
    if cwd is None:
        working = root
    else:
        # Through the same check the file tools use, so a directory outside the
        # workspace raises rather than being quietly accepted.
        working = safe_path(cwd)
    if not command_available(argv):
        return CommandOutcome(
            argv, error="'%s' was not found on PATH. TMT does not install it; "
                        "install it yourself or use a command this project "
                        "already provides." % argv[0])
    sandbox = _launcher()
    if sandbox is None:
        return CommandOutcome(
            argv, error="TMT could not load agent_sandbox, which is the only "
                        "way it starts a process, so nothing ran and nothing "
                        "is known about the code. Reinstall TMT or add "
                        "agent_sandbox to pyproject.toml's py-modules.")
    # `run_argv` rather than `launch`, because the part of running a command
    # that is easy to get wrong is not starting it -- it is reading the output
    # incrementally so a runaway producer cannot exhaust memory, keeping the
    # TAIL of it when there is too much, and killing the whole process tree on
    # the clock. `agent_sandbox` does all three, once. A second copy of that
    # loop here is the thing having one primitive was for.
    environment = _command_env()
    # The network mode this run has, in the channel `agent_sandbox.launch`
    # reads it from. It is "open" and that is the existing behaviour rather
    # than a new permission: verification has always run the project's own
    # commands with the network the user has, and a test suite that binds a
    # loopback socket or a build that resolves a lockfile would otherwise start
    # failing on any host with an OS sandbox helper installed -- reported as
    # the project's tests failing, which is a lie about the code. A command a
    # MODEL wrote is a different question and gets `agent_bash`'s answer to it.
    environment["_TMT_NETWORK"] = getattr(sandbox, "NETWORK_OPEN", "open")
    started = time.time()
    try:
        outcome = sandbox.run_argv(argv, cwd=str(working), env=environment,
                                   timeout=seconds)
    except Exception as error:
        # Every launch failure lands here as the same kind of fact: the command
        # did not run. An OSError from the OS refusing it and a sandbox that
        # could not build its own scaffolding are different causes of one
        # outcome, and the outcome is what the engine reasons about.
        return CommandOutcome(argv, duration=time.time() - started,
                              error="it could not be started (%s)" % error)
    # Mapped field by field rather than returned as it stands, because the two
    # objects are read by different code with different rules: the engine
    # reasons about `CommandOutcome`, and its `exit_code is None` contract is
    # exactly `ExecOutcome`'s, so a timeout, a refusal and a program that was
    # not there all arrive here as no exit code and a sentence -- which is what
    # they already were.
    exit_code = outcome.exit_code
    return CommandOutcome(argv,
                          exit_code=None if exit_code is None else int(exit_code),
                          output=_joined(outcome.output, ""),
                          duration=outcome.duration or (time.time() - started),
                          error=str(outcome.error or ""))


def _joined(out, err):
    """stdout and stderr as one stream, bounded, in the order they are read.

    One stream because a runner splits its report across both and reading only
    one of them loses half the evidence -- pytest's summary is on stdout and a
    traceback from a crashed collection is on stderr.
    """
    parts = []
    for chunk in (out, err):
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", "replace")
        if chunk:
            parts.append(chunk)
    text = "".join(parts)
    if len(text) <= MAX_COMMAND_OUTPUT:
        return text
    return text[-MAX_COMMAND_OUTPUT:]
