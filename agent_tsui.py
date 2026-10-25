"""Starting the optional TypeScript front end, and never being stopped by it.

TMT has two front ends over one agent. The Python one is the hand-written
renderer in `agent_menu` and `TMT`, and it is the default and the fallback. The
other is a Node program in `tui/` that owns the terminal and talks to a Python
backend (`TMT.py --backend-stdio`) over JSON lines. This module is the seam
between them: `TMT.main` asks `launch`, and `launch` either runs the Node front
end to completion and answers its exit code, or answers None -- which means
"carry on with the Python UI, exactly as if this had not been asked".

**It must never stop TMT starting.** Every way the TypeScript front end can be
unavailable is a None and, when somebody is looking, one line on stderr saying
why: no Node, an old Node, `tui/dist/` not built, a stdin or stdout that is not
a terminal, a process that would not spawn. The TypeScript front end is an
alternative the user opted into, and a setting that could lock them out of the
program is worse than one that does nothing.

**`--ci`, piped and scripted runs never get here.** `TMT.main` returns from the
CI branch before asking, and `launch` itself refuses when stdin or stdout is not
a terminal, so those behave exactly as they did whichever front end is saved.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import agent_config

# The oldest Node the front end is built for. Checked by version rather than by
# presence, because an old Node starts fine and then fails on its first import.
MIN_NODE_MAJOR = 22

# What a person types to produce `tui/dist/`. Named in every "unavailable"
# sentence, because the usual reason is a checkout that was never built.
BUILD_COMMAND = "cd tui && npm install && npm run build"


def chosen_ui(flag=None):
    """The front end this run asked for: `--ui`, then `TMT_UI`, then the saved one.

    The first two are for one run only and write nothing; they are the way back
    to Python when the saved choice cannot start. A value that is not a front
    end is skipped as if unset rather than refused, so a typo in the
    environment cannot stop TMT starting any more than one in the file can.
    """
    for value in (flag, os.environ.get("TMT_UI")):
        value = str(value or "").strip().lower()
        if value in agent_config.UIS:
            return value
    return agent_config.read_saved_ui()


def _ui_flag(argv):
    """The value of the last `--ui` in an argument list, or None."""
    found = None
    arguments = list(argv or [])
    for index, argument in enumerate(arguments):
        if argument == "--ui" and index + 1 < len(arguments):
            found = arguments[index + 1]
        elif argument.startswith("--ui="):
            found = argument[len("--ui="):]
    return found


def _node_major(node):
    """The major version of a Node executable, or None if it will not say."""
    try:
        done = subprocess.run([node, "--version"], capture_output=True,
                              text=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    match = re.match(r"v?(\d+)", (done.stdout or "").strip())
    if done.returncode != 0 or not match:
        return None
    return int(match.group(1))


def find_node():
    """The path of a Node 22 or newer on PATH, or None."""
    node = shutil.which("node")
    if not node:
        return None
    major = _node_major(node)
    if major is None or major < MIN_NODE_MAJOR:
        return None
    return node


def find_entry():
    """`tui/dist/main.js` in the installation, or None when it is not built."""
    entry = Path(agent_config.INSTALL_DIR) / "tui" / "dist" / "main.js"
    return entry if entry.is_file() else None


def _reason(node, entry):
    """What is missing, as a sentence, or None when nothing is."""
    if node is None:
        return ("Node.js %d or newer was not found on PATH. Install it, then "
                "build the TypeScript UI with: %s" % (MIN_NODE_MAJOR, BUILD_COMMAND))
    if entry is None:
        return ("The TypeScript UI is not built (tui/dist/main.js is missing). "
                "Build it with: %s" % BUILD_COMMAND)
    return None


def unavailable_reason():
    """Why the TypeScript front end cannot start here, or None if it can.

    Names what is missing and the exact build command, so the line a user sees
    is also the instruction for fixing it.
    """
    return _reason(find_node(), find_entry())


def _is_tty(stream):
    """Whether a stream is a terminal. Never raises, and a missing one is not."""
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _fall_back(reason):
    """Say why the Python UI is running instead, once, and only to a person.

    stderr being a terminal is the test for "somebody is looking": a line
    written into a log or a pipe would be noise, and it is the one stream the
    Python UI does not draw on. Returns None so callers can `return` it.
    """
    if _is_tty(sys.stderr):
        print("tmtcode: using the Python UI. " + reason, file=sys.stderr)
    return None


def launch(argv):
    """Run the TypeScript front end. Its exit code, or None for the Python UI.

    `argv` is the original argument list, handed on unchanged: the front end
    starts this same program as its backend with those arguments, so the
    workspace and every flag mean the same thing in both. Node inherits the
    terminal outright, which is the point -- it owns raw keys and every byte
    drawn -- and gets the interpreter and entry point it should start as the
    backend through `TMT_PYTHON` and `TMT_ENTRY`.

    Never raises into `main`. Anything that goes wrong before the front end is
    running is None; Ctrl-C while it runs is 130.
    """
    try:
        if chosen_ui(_ui_flag(argv)) == "python":
            # An explicit request for Python: no notice, nobody is confused.
            return None
        if not (_is_tty(sys.stdin) and _is_tty(sys.stdout)):
            # Checked before looking for Node, so a piped run never pays for a
            # `node --version` it cannot use.
            return _fall_back("The TypeScript UI needs an interactive "
                              "terminal.")
        node, entry = find_node(), find_entry()
        reason = _reason(node, entry)
        if reason:
            return _fall_back(reason)
        environment = dict(os.environ)
        environment["TMT_PYTHON"] = sys.executable
        environment["TMT_ENTRY"] = str(Path(agent_config.INSTALL_DIR) / "TMT.py")
        process = subprocess.Popen([node, str(entry)] + list(argv or []),
                                   env=environment)
    except Exception as error:          # noqa: BLE001 - never stop TMT starting
        return _fall_back("The TypeScript UI could not be started (%s: %s)."
                          % (type(error).__name__, error))
    try:
        return process.wait()
    except KeyboardInterrupt:
        # The terminal's Ctrl-C reached Node as well, and Node is what puts the
        # terminal back from raw mode, so it is given time to finish rather
        # than being killed mid-restore.
        try:
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            process.kill()
            process.wait()
        return 130
