"""Code runners and approved desktop application launching."""

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
import agent_config
from agent_file_ops import safe_path

RUNNERS = {
    ".py": ["python", "{file}"], ".js": ["node", "{file}"], ".rb": ["ruby", "{file}"],
    ".php": ["php", "{file}"], ".lua": ["lua", "{file}"], ".pl": ["perl", "{file}"],
    ".r": ["Rscript", "{file}"], ".go": ["go", "run", "{file}"],
    ".ts": ["npx", "--yes", "ts-node", "{file}"],
}

def _run_cmd(cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=agent_config.ROOT_DIR)
        output = (result.stdout + result.stderr).strip()
        return output[:2000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: timed out after 10 seconds"
    except FileNotFoundError:
        return f"Error: '{cmd[0]}' not found — is it installed and on PATH?"

def run_file(path):
    p = safe_path(path)
    if not p.exists():
        return f"File not found: {path}"
    ext = p.suffix.lower()
    if ext in RUNNERS:
        return _run_cmd([c.replace("{file}", str(p)) for c in RUNNERS[ext]])
    if ext in {".c", ".cpp"}:
        compiler = "gcc" if ext == ".c" else "g++"
        out = p.with_suffix(".exe" if platform.system() == "Windows" else "")
        compile_out = _run_cmd([compiler, str(p), "-o", str(out)])
        if not out.exists():
            return f"Compile error:\n{compile_out}"
        run_out = _run_cmd([str(out)])
        try:
            out.unlink()
        except OSError:
            pass
        return run_out
    if ext == ".java":
        compile_out = _run_cmd(["javac", str(p)])
        if "error" in compile_out.lower():
            return f"Compile error:\n{compile_out}"
        run_out = _run_cmd(["java", "-cp", str(p.parent), p.stem])
        try:
            p.with_suffix(".class").unlink()
        except OSError:
            pass
        return run_out
    supported = ", ".join(list(RUNNERS) + [".c", ".cpp", ".java"])
    return f"Unsupported file type: '{ext}'. Supported: {supported}"

run_python = run_file

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
# `run_file` above runs ONE FILE by its extension, which is the right shape for
# "run this script and show me what it printed" and the wrong shape for "run
# whatever this repository tests itself with". Verification needs the second,
# and it needs three things `run_file` does not give it: a timeout measured in
# minutes rather than ten seconds, the real exit code rather than a string, and
# the ability to run a command that is not a file at all.
#
# What it deliberately does NOT add is a new way to execute things. Same
# `subprocess.run`, same `cwd=agent_config.ROOT_DIR`, same capture. The one
# rule that matters is stated here because everything else rests on it:
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
    """
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def run_command(argv, timeout=DEFAULT_COMMAND_TIMEOUT, cwd=None):
    """Run one argv list in the workspace and report exactly what happened.

    Never a shell, never a string. `argv` is a list and is passed straight to
    `subprocess.run`, so no part of it is interpreted by anything but the
    program named in its first element.

    `cwd` defaults to the workspace root, which is the same boundary every
    other execution in TMT runs inside. A caller may narrow it to a directory
    under the root; anything outside is refused, because a verification
    command that could pick its own working directory would be a way around
    the sandbox the file tools enforce.
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
    started = time.time()
    try:
        done = subprocess.run(argv, capture_output=True, cwd=str(working),
                              timeout=seconds, env=_command_env(),
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as expired:
        # A timeout is NOT a failure. The command did not report, so nothing is
        # known about the code -- what came out before the clock ran out is
        # kept, because it is often where the hang is visible.
        partial = _joined(getattr(expired, "stdout", ""), getattr(expired, "stderr", ""))
        return CommandOutcome(argv, output=partial,
                              duration=time.time() - started,
                              error="it did not finish within %d seconds and "
                                    "was stopped" % int(seconds))
    except OSError as error:
        return CommandOutcome(argv, duration=time.time() - started,
                              error="it could not be started (%s)" % error)
    return CommandOutcome(argv, exit_code=int(done.returncode),
                          output=_joined(done.stdout, done.stderr),
                          duration=time.time() - started)


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
