"""The hidden `--tui-prelude` mode: the Python startup screens, borrowed.

The TypeScript front end does not port the splash, the startup menu, the API key
form or the git-identity offer. It suspends itself and runs this program with
`--tui-prelude`, which draws those screens on the real terminal exactly as the
Python UI does and exits with a code saying what to do next: 0 start the session,
10 the user chose Exit, 2 the workspace was refused, 1 something went wrong. The
same mode runs again on `/back`, in a new process each time.

This file protects, in order:

THE FLAG. Accepted, off by default, hidden from `--help`, and refused with a
usage error beside `--ci` -- a pipeline must never be able to draw a screen.

THE CODES. Each one, with the five screens (`run_splash`, `run_startup`,
`ensure_api_key`, `ensure_git_identity`, `clear_screen`) and the `refresh_*`
calls stood in for, the way `test_agent_cli` stands them in. Every step runs in
the order `main` runs it, and a step that ends the run stops the ones after it.

NO QUESTIONS ABOUT THE WORKSPACE. A directory that holds files and has no git is
the backend's question, asked later as an `approval` event. `run_prelude` settles
the workspace and never calls `console.input`.

THE DISPATCH. `main` hands over right after `--backend-stdio` and before the
front-end choice, `--ci` never reaches it, and without the flag `main` goes where
it always went.

THE RESTART. A successful update replaces the process with one run on the same
argument list; that list carries `--tui-prelude`, so the replacement is a
prelude again, and its exit code is the one the parent reports.

Nothing here touches the real `.tmt_ui`, the real key or a real terminal.
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import agent_config
import agent_update
import TMT

from test_agent_ui_setting import Ui, environment, patched
from test_agent_workspace import Workspace

INSTALL_DIR = Path(agent_config.__file__).resolve().parent

REFRESHES = ("refresh_model", "refresh_effort", "refresh_protocol",
             "refresh_auto_update", "refresh_project_context")

# The whole of a successful run, in the order `main` runs it.
FULL_RUN = ["splash", *REFRESHES, "startup", "api_key", "git_identity", "clear"]


# --- the harness ----------------------------------------------------------------

class Console:
    """Stands in for `TMT.console`: keeps what was printed, refuses any question."""

    def __init__(self):
        self.printed, self.asked = [], []

    def print(self, *args, **kwargs):
        self.printed.append(" ".join(str(value) for value in args))

    def input(self, prompt=""):
        self.asked.append(prompt)
        raise AssertionError("the prelude asked a question: %r" % (prompt,))

    def said(self):
        return "\n".join(self.printed)


class Screens:
    """The five screens and the five refreshes, recording the order they ran in.

    `splash`, `startup` and `key` are what each one answers; `boom` names a step
    that raises (a RuntimeError, or the exception given as `error`).
    """

    def __init__(self, splash="start", startup="start", key=True, boom=None,
                 error=RuntimeError("the screen broke")):
        self.log, self.answers = [], {"splash": splash, "startup": startup,
                                      "api_key": key}
        self.boom, self.error = boom, error
        self.root_at_startup = self.startup_args = None

    def step(self, name):
        self.log.append(name)
        if self.boom == name:
            raise self.error
        return self.answers.get(name)

    def attributes(self):
        def startup(*args, **kwargs):
            self.root_at_startup = agent_config.ROOT_DIR
            self.startup_args = (args, kwargs)
            return self.step("startup")

        def refresher(name):
            return lambda *args, **kwargs: self.step(name)

        return {
            TMT: dict(
                run_splash=lambda *a, **k: self.step("splash"),
                run_startup=startup,
                ensure_api_key=lambda *a, **k: self.step("api_key"),
                ensure_git_identity=lambda *a, **k: self.step("git_identity"),
                clear_screen=lambda *a, **k: self.step("clear"),
                _session_loop=_refuses("the session loop"),
            ),
            agent_config: {name: refresher(name) for name in REFRESHES},
        }


def _refuses(what):
    def refuse(*args, **kwargs):
        raise AssertionError("%s was reached" % what)
    return refuse


@contextlib.contextmanager
def prelude(screens=None, console=None):
    """The screens stood in for, the console replaced, and the root put back."""
    screens = Screens() if screens is None else screens
    console = Console() if console is None else console
    previous_root = agent_config.ROOT_DIR
    with contextlib.ExitStack() as stack:
        for target, attributes in screens.attributes().items():
            stack.enter_context(patched(target, **attributes))
        stack.enter_context(patched(TMT, console=console))
        try:
            yield screens, console
        finally:
            agent_config.ROOT_DIR = previous_root


@contextlib.contextmanager
def workspace(git=False, files=None):
    box = Workspace(git=git, files=files)
    try:
        yield box
    finally:
        box.close()


def run(argv, screens=None, console=None):
    """`TMT.main` on a `--tui-prelude` command line -> (code, screens, console, stderr)."""
    err = io.StringIO()
    with prelude(screens, console) as (screens, console), \
            contextlib.redirect_stderr(err):
        code = TMT.main(["--tui-prelude"] + list(argv))
    return code, screens, console, err.getvalue()


def usage_error(argv):
    """The exit code and stderr of a command line `parse_args` refuses."""
    err, code = io.StringIO(), None
    with contextlib.redirect_stderr(err):
        try:
            TMT.parse_args(list(argv))
        except SystemExit as error:
            code = error.code
    return code, err.getvalue()


# --- the flag --------------------------------------------------------------------

def test_the_flag_is_accepted_defaults_off_and_is_hidden_from_help():
    assert TMT.parse_args([]).tui_prelude is False
    assert TMT.parse_args(["--tui-prelude"]).tui_prelude is True
    args = TMT.parse_args(["--tui-prelude", "--dir", "somewhere", "--ui", "python"])
    assert args.tui_prelude is True and args.directory == "somewhere", args
    shown, code = io.StringIO(), None
    with contextlib.redirect_stdout(shown):
        try:
            TMT.parse_args(["--help"])
        except SystemExit as error:
            code = error.code
    assert code == 0, code
    assert "--ui" in shown.getvalue()
    assert "tui-prelude" not in shown.getvalue(), "the flag is for the front end, not people"
    assert "backend-stdio" not in shown.getvalue()


def test_the_flag_beside_ci_is_a_usage_error_in_either_order():
    for argv in (["--ci", "--tui-prelude", "fix", "it"],
                 ["--tui-prelude", "--ci", "fix", "it"],
                 ["--tui-prelude", "--ci"]):
        code, err = usage_error(argv)
        assert code == 2, (argv, code)
        assert "--tui-prelude" in err and "--ci" in err, (argv, err)
    # And each is still fine alone.
    assert TMT.parse_args(["--ci", "fix"]).tui_prelude is False
    assert TMT.parse_args(["--tui-prelude"]).ci is False


def test_ci_with_the_flag_is_refused_before_anything_runs():
    ran = []
    with patched(TMT, run_ci=lambda args: ran.append("ci") or 0,
                 run_prelude=_refuses("the prelude"),
                 run_splash=_refuses("the splash")), \
            contextlib.redirect_stderr(io.StringIO()):
        try:
            TMT.main(["--ci", "--tui-prelude", "fix"])
        except SystemExit as error:
            assert error.code == 2, error.code
        else:
            raise AssertionError("main accepted --ci with --tui-prelude")
    assert ran == [], "the CI run started"


# --- the codes --------------------------------------------------------------------

def test_code_0_runs_every_screen_in_main_order_and_clears_the_screen_last():
    with workspace(git=True) as box:
        code, screens, console, err = run(["--dir", str(box.path)])
        assert code == 0, (code, err)
        assert screens.log == FULL_RUN, screens.log
        # The workspace was settled before the first screen, and the menu was
        # told which one it is for.
        assert screens.root_at_startup == box.path, screens.root_at_startup
        assert screens.startup_args == ((), {"workspace": box.path}), screens.startup_args
    assert console.asked == [] and err == "", (console.asked, err)


def test_code_0_with_no_directory_the_workspace_is_where_the_run_was_started():
    with workspace(git=True) as box:
        with patched(TMT, default_workspace=lambda: box.path):
            code, screens, console, err = run([])
        assert code == 0 and screens.log == FULL_RUN, (code, screens.log, err)
        assert screens.root_at_startup == box.path, screens.root_at_startup


def test_code_10_when_the_splash_says_exit_and_nothing_after_it_runs():
    with workspace(git=True) as box:
        code, screens, console, err = run(["--dir", str(box.path)],
                                          Screens(splash="exit"))
    assert code == 10, (code, err)
    assert screens.log == ["splash"], screens.log


def test_code_10_when_the_startup_menu_says_exit_and_no_key_is_asked_for():
    with workspace(git=True) as box:
        code, screens, console, err = run(["--dir", str(box.path)],
                                          Screens(startup="exit"))
    assert code == 10, (code, err)
    assert screens.log == ["splash", *REFRESHES, "startup"], screens.log


def test_code_10_when_the_api_key_is_declined_and_the_screen_is_not_cleared():
    with workspace(git=True) as box:
        code, screens, console, err = run(["--dir", str(box.path)],
                                          Screens(key=False))
    assert code == 10, (code, err)
    assert screens.log == ["splash", *REFRESHES, "startup", "api_key"], screens.log


def test_code_10_when_ctrl_c_reaches_any_screen():
    for step in ("splash", "startup", "api_key", "git_identity"):
        with workspace(git=True) as box:
            code, screens, console, err = run(
                ["--dir", str(box.path)],
                Screens(boom=step, error=KeyboardInterrupt()))
        assert code == 10, (step, code, err)
        assert err == "", (step, err)
        assert "clear" not in screens.log, (step, screens.log)


def test_code_2_when_the_workspace_is_refused_and_the_reason_is_printed():
    missing = Path(tempfile.gettempdir()) / "tmt_prelude_missing_directory"
    assert not missing.exists(), missing
    for target, why in ((missing, "does not exist"),
                        (Path.home(), "home directory")):
        code, screens, console, err = run(["--dir", str(target)])
        assert code == 2, (target, code, err)
        assert screens.log == [], "a screen was drawn for a run that cannot start"
        said = console.said()
        assert "Not a usable workspace" in said and why in said, said
        assert "--dir" in said, said
        assert err == "", err


def test_code_2_for_a_file_and_the_root_is_left_alone():
    with workspace() as box:
        target = box.path / "notes.txt"
        target.write_text("x", encoding="utf-8")
        before = agent_config.ROOT_DIR
        code, screens, console, err = run(["--dir", str(target)])
        assert code == 2 and screens.log == [], (code, screens.log, err)
        assert "is a file" in console.said(), console.said()
        assert agent_config.ROOT_DIR == before


def test_code_1_with_the_traceback_on_stderr_when_a_screen_breaks():
    for step in ("splash", "startup", "api_key", "git_identity", "clear"):
        with workspace(git=True) as box:
            code, screens, console, err = run(["--dir", str(box.path)],
                                              Screens(boom=step))
        assert code == 1, (step, code)
        assert "Traceback" in err and "the screen broke" in err, (step, err)
        assert screens.log[-1] == step, (step, screens.log)


def test_code_1_when_a_refresh_breaks_too():
    with workspace(git=True) as box:
        code, screens, console, err = run(["--dir", str(box.path)],
                                          Screens(boom="refresh_effort"))
    assert code == 1 and "the screen broke" in err, (code, err)
    assert "startup" not in screens.log, screens.log


def test_the_codes_are_the_ones_the_front_end_is_built_against():
    """Spelled out once so a change here is a change to the contract on purpose."""
    outcomes = {}
    with workspace(git=True) as box:
        for name, screens in (("start", Screens()), ("exit", Screens(splash="exit")),
                              ("error", Screens(boom="splash"))):
            outcomes[name] = run(["--dir", str(box.path)], screens)[0]
    outcomes["refused"] = run(["--dir", str(Path.home())])[0]
    assert outcomes == {"start": 0, "exit": 10, "error": 1, "refused": 2}, outcomes


def test_running_it_again_gives_the_same_answer_each_time():
    """Each `/back` is another process, but nothing in one run may make the next
    differ either: the same command line twice, and a refusal between them."""
    with workspace(git=True) as box:
        first = run(["--dir", str(box.path)])
        refused = run(["--dir", str(Path.home())])
        second = run(["--dir", str(box.path)])
    assert (first[0], refused[0], second[0]) == (0, 2, 0)
    assert first[1].log == second[1].log == FULL_RUN


# --- no questions about the workspace ------------------------------------------------

def test_a_populated_directory_without_git_is_never_asked_about():
    with workspace(files={"notes.txt": "keep me", "src/app.py": "print(1)"}) as box:
        # The precondition: this is exactly the directory `resolve_workspace`
        # would stop and ask about.
        assert agent_config.workspace_needs_confirmation(box.path) is True
        with patched(TMT, workspace_needs_confirmation=_refuses("the confirmation check"),
                     resolve_workspace=_refuses("resolve_workspace")):
            code, screens, console, err = run(["--dir", str(box.path)])
        assert code == 0, (code, err)
        assert console.asked == [], console.asked
        assert screens.log == FULL_RUN, screens.log
        assert screens.root_at_startup == box.path
        assert (box.path / "notes.txt").read_text(encoding="utf-8") == "keep me"
    assert "already has files" not in console.said(), console.said()


def test_the_python_ui_still_asks_about_that_same_directory():
    """The other half: the question did not go away, it moved to the backend."""
    asked = []
    with workspace(files={"notes.txt": "keep me"}) as box, \
            contextlib.redirect_stdout(io.StringIO()):
        root = TMT.resolve_workspace(str(box.path),
                                     ask=lambda prompt: asked.append(prompt) or "n")
    assert root is None and len(asked) == 1, (root, asked)


# --- the dispatch ----------------------------------------------------------------------

def test_main_hands_over_before_the_front_end_choice_and_returns_the_code():
    seen = []

    def fake(args):
        seen.append(args)
        return 7

    with Ui("typescript"), environment(TMT_UI="typescript"), \
            patched(TMT, run_prelude=fake,
                    resolve_workspace=_refuses("resolve_workspace"),
                    run_splash=_refuses("the splash")), \
            patched(TMT.agent_tsui, launch=_refuses("the TypeScript launch")):
        assert TMT.main(["--tui-prelude", "--dir", "somewhere"]) == 7
    assert len(seen) == 1 and seen[0].tui_prelude is True, seen
    assert seen[0].directory == "somewhere", seen[0]


def test_backend_stdio_still_goes_first_when_both_are_given():
    backend = []
    import types
    fake = types.ModuleType("agent_backend")
    fake.run = lambda args: backend.append(args) or 42
    missing = object()
    previous = sys.modules.get("agent_backend", missing)
    try:
        sys.modules["agent_backend"] = fake
        with patched(TMT, run_prelude=_refuses("the prelude")):
            assert TMT.main(["--backend-stdio", "--tui-prelude"]) == 42
    finally:
        if previous is missing:
            sys.modules.pop("agent_backend", None)
        else:
            sys.modules["agent_backend"] = previous
    assert len(backend) == 1, backend


def test_ci_never_reaches_the_prelude():
    with patched(TMT, run_ci=lambda args: 3, run_prelude=_refuses("the prelude")):
        assert TMT.main(["--ci", "fix it"]) == 3


def test_without_the_flag_main_goes_where_it_always_went():
    order = []

    def resolve(directory=None, ask=None):
        order.append("resolve_workspace")
        return Path(tempfile.gettempdir())

    def splash(*args, **kwargs):
        order.append("splash")
        return "exit"

    with Ui("python"), environment(TMT_UI=None), \
            patched(TMT, run_prelude=_refuses("the prelude"),
                    resolve_workspace=resolve, run_splash=splash):
        assert TMT.main([]) == 0
        assert TMT.main(["--ui", "python", "somewhere"]) == 0
    assert order == ["resolve_workspace", "splash"] * 2, order
    assert TMT.parse_args(["somewhere"]).tui_prelude is False


# --- the restart ----------------------------------------------------------------------------

def test_an_update_restart_keeps_the_flag_and_every_other_argument():
    """`agent_update.restart` re-runs `sys.argv` as it was: both launch shapes."""
    script = ["TMT.py", "--tui-prelude", "--dir", "C:/work", "--ui", "typescript"]
    assert agent_update.restart_command(script, executable="/py") == \
        ["/py", "TMT.py", "--tui-prelude", "--dir", "C:/work", "--ui", "typescript"]
    console = ["/bin/tmtcode", "--tui-prelude", "C:/work"]
    assert agent_update.restart_command(console, executable="/py") == console


def test_the_restart_reads_sys_argv_by_default_and_hands_on_the_childs_code():
    launched, exits = [], []
    saved = sys.argv
    sys.argv = ["TMT.py", "--tui-prelude", "--dir", "C:/work"]
    try:
        with patched(agent_update, _safe_log=lambda *a, **k: None):
            # POSIX: the process is replaced, so there is no code to hand on.
            agent_update.restart(
                executable="/py", environ={}, windows=False,
                execv=lambda path, command: launched.append(command))
            # Windows: the replacement is spawned and waited for, and its code
            # is this process's code -- 10 stays 10, so Exit is still Exit.
            for child in (0, 10, 2):
                agent_update.restart(
                    executable="/py", environ={}, windows=True,
                    spawn=lambda command, env, child=child:
                        launched.append(command) or child,
                    exit_with=exits.append)
    finally:
        sys.argv = saved
    expected = ["/py", "TMT.py", "--tui-prelude", "--dir", "C:/work"]
    assert launched == [expected] * 4, launched
    assert exits == [0, 10, 2], exits


# --- a real process ----------------------------------------------------------------------------

def _prelude_process(*arguments):
    environment_ = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(INSTALL_DIR / "TMT.py"),
                           "--tui-prelude", *arguments],
                          stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, encoding="utf-8", env=environment_,
                          timeout=120)


def test_two_real_processes_in_a_row_each_refuse_and_report_the_same_way():
    """The `/back` case: one process after another, nothing carried between them."""
    missing = str(Path(tempfile.gettempdir()) / "tmt_prelude_missing_directory")
    results = [_prelude_process("--dir", missing) for _ in range(2)]
    for done in results:
        assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
        assert "Not a usable workspace" in done.stdout, done.stdout
        assert "Traceback" not in done.stderr, done.stderr
    assert results[0].stdout == results[1].stdout


def test_a_real_process_refuses_ci_with_the_flag_as_a_usage_error():
    done = _prelude_process("--ci", "fix it")
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
    assert "--tui-prelude" in done.stderr and "--ci" in done.stderr, done.stderr
    assert done.stdout == "", done.stdout
