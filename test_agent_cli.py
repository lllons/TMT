"""Tests for the launcher: which directory a run adopts, and which it never does.

TMT is installed once and started from wherever the work is, so two directories
have to stay apart for a whole session: the installation, which holds TMT's own
code and credentials, and the workspace, which is the project it may modify.
These cover the choosing of the second without disturbing the first, and then
the packaging declaration, because an installed `tmtcode` that cannot import
its own modules never reaches any of the logic below.

No virtualenv and no pip: a unit suite cannot afford either. What is tested is
the resolution logic and the declaration the install is built from.

Two things here are process-global -- the working directory and
agent_config.ROOT_DIR -- and both are restored in a finally block. A leak in
either would silently relocate every test that ran afterwards.
"""

import contextlib
import importlib
import inspect
import io
import os
import re
from pathlib import Path

import agent_config
import agent_file_ops
import agent_git
import TMT
from test_agent_workspace import INSTALL_DIR, Workspace

PYPROJECT = INSTALL_DIR / "pyproject.toml"
ENTRY_POINT = "TMT:main"


def resolve_from(directory, argv=None, ask=None):
    """Start TMT in `directory` with `argv` and report what it settled on.

    Returns (resolved workspace, agent_config.ROOT_DIR as it stood at that
    moment), because both are restored here before returning: the caller gets
    the values without the process keeping the side effects.
    """
    previous_cwd = Path.cwd()
    previous_root = agent_config.ROOT_DIR
    try:
        os.chdir(str(directory))
        args = TMT.parse_args(list(argv or []))
        resolved = TMT.resolve_workspace(args.directory, ask=ask or (lambda _: "y"))
        return resolved, agent_config.ROOT_DIR
    finally:
        os.chdir(str(previous_cwd))
        agent_config.ROOT_DIR = previous_root


# --- the directory a run adopts ---------------------------------------------

def test_started_in_the_installation_that_directory_is_the_workspace():
    """TMT's own checkout is an ordinary project when you are standing in it.
    That is the one case where the two directories coincide, and they coincide
    by value rather than by being the same thing: the second half moves the
    workspace elsewhere and INSTALL_DIR does not follow.
    """
    resolved, root = resolve_from(INSTALL_DIR)
    assert resolved == INSTALL_DIR, resolved
    assert root == INSTALL_DIR, root
    assert agent_config.INSTALL_DIR == INSTALL_DIR

    box = Workspace(git=True, files={"app.py": "print(1)\n"})
    try:
        box.use()
        assert agent_config.ROOT_DIR == box.path
        assert agent_config.INSTALL_DIR == INSTALL_DIR
    finally:
        box.close()


def test_started_anywhere_else_that_directory_is_the_workspace():
    """The point of the launcher: an unrelated project, nothing copied into
    it, and the installation left where it was."""
    box = Workspace(git=True, files={"app.py": "print(1)\n"})
    try:
        resolved, root = resolve_from(box.path)
        assert resolved == box.path, resolved
        assert root == box.path, root
        assert agent_config.INSTALL_DIR == INSTALL_DIR
        assert resolved != INSTALL_DIR
        assert INSTALL_DIR not in resolved.parents
    finally:
        box.close()


def test_a_nested_subdirectory_is_taken_literally_and_not_walked_up_from():
    """TMT works on the directory it was started in, not on the repository
    that directory happens to belong to. Anything else would quietly widen
    what an agent that overwrites and deletes is allowed to reach."""
    box = Workspace(git=True, files={"src/deep/mod.py": "x = 1\n"})
    try:
        nested = (box.path / "src" / "deep").resolve()
        resolved, root = resolve_from(nested)
        assert resolved == nested, resolved
        assert root == nested, root
        assert resolved != box.path
    finally:
        box.close()


def test_an_explicit_path_is_used_instead_of_the_current_directory():
    standing_in = Workspace(git=True, files={"here.py": "here\n"})
    named = Workspace(git=True, files={"there.py": "there\n"})
    try:
        resolved, root = resolve_from(standing_in.path, argv=[str(named.path)])
        assert resolved == named.path, resolved
        assert root == named.path, root
        assert resolved != standing_in.path
    finally:
        named.close()
        standing_in.close()


def test_dir_still_selects_the_workspace_and_conflicts_with_a_positional():
    """--dir predates the positional argument and still has to work. Naming
    two different directories is refused rather than silently resolved in
    favour of one of them."""
    box = Workspace(git=True, files={"app.py": "print(1)\n"})
    try:
        resolved, root = resolve_from(INSTALL_DIR, argv=["--dir", str(box.path)])
        assert resolved == box.path, resolved
        assert root == box.path, root

        # The same directory said twice is agreement, not a conflict.
        agreed = TMT.parse_args([str(box.path), "--dir", str(box.path)])
        assert agreed.directory == str(box.path), agreed.directory

        exit_error = None
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                TMT.parse_args([str(box.path), "--dir", str(box.path / "other")])
            except SystemExit as error:
                exit_error = error
        assert exit_error is not None, "two different directories must not be accepted"
        assert exit_error.code == 2, exit_error.code
    finally:
        box.close()


def test_a_relative_path_resolves_against_the_directory_tmt_was_started_in():
    """A relative argument means "relative to where I am standing", and what
    comes out is absolute: every later path check compares against it."""
    box = Workspace(git=True)
    try:
        (box.path / "sub").mkdir()
        for spelling in ("sub", os.path.join(".", "sub")):
            resolved, root = resolve_from(box.path, argv=[spelling])
            assert resolved == box.path / "sub", (spelling, resolved)
            assert resolved.is_absolute(), resolved
            assert root == box.path / "sub", root
    finally:
        box.close()


# --- application state versus project state ---------------------------------

def test_application_resources_stay_in_the_installation():
    """The key, the git identity and the git logs belong to the installation,
    so TMT is the same agent in every project and credentials stay in one
    place. None of them may land in the project being worked on."""
    box = Workspace(git=True, files={"app.py": "print(1)\n"})
    try:
        box.use()
        resources = {
            "KEY_FILE": agent_config.KEY_FILE,
            "GIT_IDENTITY_FILE": agent_config.GIT_IDENTITY_FILE,
            "GIT_IDENTITY_LOCAL_FILE": agent_config.GIT_IDENTITY_LOCAL_FILE,
            "agent_git.LOG_DIR": agent_git.LOG_DIR,
        }
        for name, value in resources.items():
            path = Path(value).resolve()
            assert path.parent == INSTALL_DIR, f"{name}: {path}"
            assert path != box.path, name
            assert box.path not in path.parents, f"{name} landed in the workspace: {path}"
    finally:
        box.close()


def test_the_file_listing_shows_the_project_and_none_of_tmts_own_source():
    """What the model is shown is the project, not the agent. TMT's own
    modules appearing here would mean the workspace had quietly stayed on the
    installation."""
    box = Workspace(files={"app.py": "print(1)\n",
                           "docs/notes.txt": "notes\n"})
    try:
        box.use()
        listed = {line.replace("\\", "/") for line in agent_file_ops.list_files().splitlines()}
        assert "app.py" in listed, listed
        assert "docs/notes.txt" in listed, listed
        own_modules = {path.name for path in INSTALL_DIR.glob("*.py")}
        assert not (listed & own_modules), listed & own_modules
        assert "pyproject.toml" not in listed, listed
    finally:
        box.close()


# --- the declaration the installed command is built from --------------------

def parse_pyproject_by_hand(text):
    """Enough TOML for this one file, for interpreters without tomllib.

    Section headers, quoted string values, and string arrays that may span
    lines. Anything else in the file is skipped rather than guessed at.
    """
    data = {}
    section = None
    key = None
    items = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if items is not None:                      # inside a multi-line array
            items.extend(re.findall(r'"([^"]*)"', line))
            if "]" in line:
                section[key] = items
                items = None
            continue
        if line.startswith("[") and line.endswith("]"):
            section = data
            for part in line[1:-1].split("."):
                section = section.setdefault(part.strip(), {})
            continue
        if section is None or "=" not in line:
            continue
        name, _, value = line.partition("=")
        key = name.strip().strip('"')
        value = value.strip()
        if value.startswith("["):
            items = re.findall(r'"([^"]*)"', value)
            if "]" in value:
                section[key] = items
                items = None
            continue
        quoted = re.match(r'"([^"]*)"', value)
        if quoted:
            section[key] = quoted.group(1)
    return data


def read_pyproject():
    text = PYPROJECT.read_text(encoding="utf-8")
    try:
        import tomllib                             # 3.11+
    except ImportError:
        return parse_pyproject_by_hand(text)
    return tomllib.loads(text)


def test_the_packaging_declaration_matches_the_modules_on_disk():
    """The bug this test exists for has already happened: agent_ui was left
    out of py-modules, and the installed `tmtcode` died on its first import.
    Nothing in the rest of the suite notices, because a checkout imports from
    the directory whether the module was declared or not.

    So the declaration is compared against the flat modules actually present,
    in both directions: a module added and never declared breaks the install,
    and a declaration left behind after a rename breaks the build.
    """
    data = read_pyproject()
    scripts = data.get("project", {}).get("scripts", {})
    assert scripts.get("tmtcode") == ENTRY_POINT, scripts

    declared = data.get("tool", {}).get("setuptools", {}).get("py-modules")
    assert declared, "pyproject declares no py-modules"
    assert len(declared) == len(set(declared)), declared

    on_disk = {path.stem for path in INSTALL_DIR.glob("*.py")
               if not path.stem.startswith("test_") and path.stem != "run_tests"}

    undeclared = sorted(on_disk - set(declared))
    assert not undeclared, (
        "on disk but missing from [tool.setuptools] py-modules: "
        + ", ".join(undeclared)
        + " -- the installed tmtcode command will fail at import"
    )
    stale = sorted(set(declared) - on_disk)
    assert not stale, (
        "declared in py-modules but not on disk: " + ", ".join(stale)
        + " -- the build will fail looking for them"
    )

    # The entry point's own module has to be in there too, whatever else is.
    assert ENTRY_POINT.split(":")[0] in declared, declared


def test_the_entry_point_names_a_callable_that_exists():
    """`tmtcode = "TMT:main"` is resolved by the console script at run time,
    so a renamed or removed main is only caught here or by a user."""
    module_name, _, attribute = ENTRY_POINT.partition(":")
    module = importlib.import_module(module_name)
    target = getattr(module, attribute, None)
    assert callable(target), f"{ENTRY_POINT} does not name a callable"
    assert target is TMT.main
    # The console script calls it with no arguments; argv stays optional so the
    # tests and `python -m` can pass one.
    parameters = inspect.signature(target).parameters
    assert list(parameters) == ["argv"], parameters
    assert parameters["argv"].default is None


# --- the session header is drawn once, not once per turn ---------------------

class Replies:
    """Stands in for the console the loop reads from.

    Answers each prompt from a fixed script and records nothing else, so a
    session of any length can be run without a terminal or a model.
    """

    def __init__(self, answers):
        self.answers = list(answers)

    def input(self, prompt=""):
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def print(self, *args, **kwargs):
        return None


def run_session(answers):
    """Drive TMT.main through `answers` turns and return everything it drew.

    Empty answers are used as the turns: the loop reaches the prompt, reads,
    and comes back round without a model request, which is the path the header
    is drawn on and nothing more.
    """
    box = Workspace()
    screen = io.StringIO()
    saved = (TMT.console, TMT.ensure_api_key, TMT.run_startup,
             TMT.ensure_git_identity)
    previous_cwd = Path.cwd()
    try:
        os.chdir(str(box.path))
        TMT.console = Replies(answers)
        TMT.ensure_api_key = lambda: True
        TMT.run_startup = lambda **kwargs: "start"
        TMT.ensure_git_identity = lambda *a, **k: None
        with contextlib.redirect_stdout(screen):
            TMT.main([])
        return screen.getvalue()
    finally:
        os.chdir(str(previous_cwd))
        (TMT.console, TMT.ensure_api_key, TMT.run_startup,
         TMT.ensure_git_identity) = saved
        box.close()


def test_the_session_header_is_drawn_once_however_many_turns_are_taken():
    """The header states what the whole session runs under, and none of it
    changes while the loop is running. Redrawing it before every prompt pushed
    the conversation off the screen for no new information."""
    drawn = run_session(["", "", "", "quit"])
    prompts = drawn.count("Task>")
    assert prompts == 4, (prompts, drawn)
    # The rule under the header is the row that was repeating, and the
    # wordmark and the clock came with it. One of each, across four turns.
    rules = [line for line in drawn.splitlines()
             if line.strip() and set(line.strip()) <= set("\u2500-")]
    assert len(rules) == 1, (rules, drawn)
    assert drawn.count("TMT") == 1, drawn
    assert len(re.findall(r"\d\d:\d\d:\d\d", drawn)) == 1, drawn


def test_every_turn_after_the_first_still_gets_a_prompt():
    """Drawing the header once must not cost the later turns their prompt:
    a read with nothing on screen looks like a hung program."""
    one = run_session(["quit"])
    many = run_session(["", "", "quit"])
    assert one.count("Task>") == 1, one
    assert many.count("Task>") == 3, many
    # Each later prompt opens with a newline of its own. That is what leaves a
    # blank line between the reply above and the question below, once the
    # terminal has echoed the user's own Enter -- which the console faked here
    # does not, so it is asserted at the source rather than in the transcript.
    screen = io.StringIO()
    menu = importlib.import_module("agent_menu")
    assert menu.render_prompt(screen) is not False
    assert screen.getvalue().startswith("\n"), repr(screen.getvalue())
    assert "Task>" in screen.getvalue(), repr(screen.getvalue())
