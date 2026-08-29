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
import json
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
    assert prompt_boxes(drawn) == 4, (prompt_boxes(drawn), drawn)
    # The header is the part that was repeating. The wordmark and the clock
    # belong to it, so one of each across four turns says it was drawn once.
    assert drawn.count("TMT") == 1, drawn
    assert len(re.findall(r"\d\d:\d\d:\d\d", drawn)) == 1, drawn


def prompt_boxes(drawn):
    """How many prompt boxes were drawn, counted by their marker row."""
    return sum(1 for line in drawn.splitlines() if line.startswith(" > "))


def test_every_turn_after_the_first_still_gets_a_prompt():
    """Drawing the header once must not cost the later turns their prompt:
    a read with nothing on screen looks like a hung program."""
    one = run_session(["quit"])
    many = run_session(["", "", "quit"])
    assert prompt_boxes(one) == 1, one
    assert prompt_boxes(many) == 3, many
    # Each box opens with a blank line of its own, so it never runs into the
    # rule or the reply above it. Asserted at the source: the console faked
    # here does not echo the user's Enter the way a terminal does.
    screen = io.StringIO()
    menu = importlib.import_module("agent_menu")
    menu.PromptBox(stream=screen, line_reader=lambda: "").ask("a hint")
    assert screen.getvalue().startswith("\n"), repr(screen.getvalue())

    # The bare prompt is still the one `render_status` draws by default, for
    # any caller that is not putting a box under it. It has to keep working.
    plain = io.StringIO()
    assert menu.render_prompt(plain) is not False
    assert "Task>" in plain.getvalue(), repr(plain.getvalue())


# --- events, and a whole turn played through the real loop -------------------

import agent_actions
import agent_ui
from agent_live_renderer import LiveRegion


def test_an_event_never_reports_an_outcome_the_action_did_not_have():
    """The one rule the transcript cannot bend. A result is only read for
    success or failure when it is the action's own report; a program's output
    is data. Scanning it for words like "failed" called a fully green test run
    a failure, which is a lie told confidently."""
    green = agent_actions.action_event("run_python", {"path": "run_tests.py"},
                                       "180 passed, 0 failed")
    assert green.kind == "command", green.kind

    # A read's result is file content and may contain anything at all.
    read = agent_actions.action_event("read_file", {"path": "a.py"},
                                      "def f():\n    raise Error: not found")
    assert read.kind == "file_read", read.kind

    # An action's own report, though, is read and believed.
    missed = agent_actions.action_event("patch_file",
                                        {"path": "a.py", "search": "q", "replace": "r"},
                                        "Search text not found in a.py")
    assert missed.kind == "warning", missed.kind


def test_the_counts_on_an_edit_are_counted_rather_than_guessed():
    """+18 -4 has to mean eighteen lines arrived and four left."""
    event = agent_actions.action_event(
        "patch_file",
        {"path": "a.py", "search": "one\ntwo\nthree\nfour", "replace": "x\ny"},
        "Patched file: a.py")
    assert event.detail["added"] == 2, event.detail
    assert event.detail["removed"] == 4, event.detail

    none_given = agent_actions.action_event("git_status", {}, "clean")
    assert "added" not in none_given.detail, none_given.detail


def test_permanent_output_leaves_the_live_region_intact():
    """The two surfaces share one terminal. Writing history has to erase the
    live region, print, and paint it again -- printing straight past it leaves
    the next repaint pointing at rows that have since moved."""
    screen = io.StringIO()
    region = LiveRegion(stream=screen, ansi=True)
    region.paint(["live status row"])
    assert region.write_above("a permanent line\n")
    body = screen.getvalue()
    assert "a permanent line" in body
    # The region was painted again after the permanent text, so its content is
    # the last thing on screen rather than something the text scrolled over.
    assert body.rindex("live status row") > body.rindex("a permanent line"), body


def test_a_region_that_cannot_use_ansi_still_prints_its_history():
    """A pipe has no cursor to move. The permanent record still has to arrive,
    because that is the part the user keeps."""
    screen = io.StringIO()
    region = LiveRegion(stream=screen, ansi=False)
    region.paint(["ignored"])
    assert region.write_above("kept\n")
    assert "kept" in screen.getvalue()


class Turn:
    """A scripted model: fixed replies, with progress reported as it streams."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.asked = 0

    def __call__(self, messages, on_event=None):
        raw = self.replies[min(self.asked, len(self.replies) - 1)]
        self.asked += 1
        if on_event is not None:
            on_event(("first_content", ""))
            on_event(("output", len(raw)))
            obj = json.loads(raw)
            # The real parser reports these mid-stream, before the object is
            # finished; reporting them here is the same contract.
            if obj.get("progress"):
                on_event(("progress", obj["progress"]))
            if obj.get("next_step"):
                on_event(("next_step", obj["next_step"]))
        return raw


def run_turn(replies, answers=("do the thing", "quit")):
    """Drive TMT.main through one real turn.

    Returns (drawn, files). The workspace is read before it is removed, so a
    caller can check what the actions actually did on disk -- which is the
    only way to tell a real edit from a convincing message about one.
    """
    box = Workspace()
    screen = io.StringIO()
    saved = (TMT.console, TMT.ensure_api_key, TMT.run_startup,
             TMT.ensure_git_identity, TMT.ask_model)
    previous_cwd = Path.cwd()
    try:
        os.chdir(str(box.path))
        TMT.console = Replies(list(answers))
        TMT.ensure_api_key = lambda: True
        TMT.run_startup = lambda **kwargs: "start"
        TMT.ensure_git_identity = lambda *a, **k: None
        TMT.ask_model = Turn(replies)
        with contextlib.redirect_stdout(screen):
            TMT.main([])
        files = {item.name: item.read_text(encoding="utf-8")
                 for item in box.path.iterdir() if item.is_file()}
        return screen.getvalue(), files
    finally:
        os.chdir(str(previous_cwd))
        (TMT.console, TMT.ensure_api_key, TMT.run_startup,
         TMT.ensure_git_identity, TMT.ask_model) = saved
        box.close()


REAL_TURN = [
    json.dumps({"action": "list_files",
                "progress": "Inspecting the existing implementation."}),
    json.dumps({"action": "write_file", "path": "notes.txt",
                "content": "one\ntwo\nthree\n",
                "progress": "Found the shared abstraction. Writing the change."}),
    json.dumps({"action": "done", "message": "The change is written.",
                "progress": "Checking the result.",
                "events": [{"type": "success", "message": "The file was created."}],
                "next_step": "Review the changed files"}),
]


def test_a_whole_turn_keeps_every_event_it_showed():
    """The end-to-end claim. Three progress messages, two actions and a
    declared success all happened during one turn, and every one of them has
    to still be on screen when the turn is over -- which is exactly what the
    single repainted status row could not do."""
    drawn, files = run_turn(REAL_TURN)

    expected = [
        "Inspecting the existing implementation.",
        "Found the shared abstraction. Writing the change.",
        "Checking the result.",
        "The file was created.",
        "Review the changed files",
    ]
    for message in expected:
        assert message in drawn, (message, drawn)

    # In the order they happened, and each exactly once.
    positions = [drawn.index(message) for message in expected]
    assert positions == sorted(positions), positions
    for message in expected[:-1]:
        assert drawn.count(message) == 1, (message, drawn.count(message))
    # The hint is the one thing drawn twice, and both are deliberate: once as
    # the lead-in to the answer, and once as shadow text in the box waiting
    # for the next task. The second is a drawing, not a value -- that it never
    # becomes input is covered separately.
    assert drawn.count(expected[-1]) == 2, drawn.count(expected[-1])

    # The work was real: the action actually wrote the file it reported.
    assert files.get("notes.txt") == "one\ntwo\nthree\n", sorted(files)
    # And the file event carried the count that was actually written. A write
    # replaces the file, so only the lines written are reported: how many the
    # old content had is not knowable once it has been overwritten, and a
    # "-0" there would be a confident falsehood.
    assert "3 lines" in drawn, drawn
    assert "-0" not in drawn, drawn


def test_the_suggestion_lands_between_the_work_and_the_answer():
    """Order is the contract: the hint reads as a lead-in to the answer, so it
    sits after everything that was done and before the answer itself."""
    drawn, _ = run_turn(REAL_TURN)
    assert drawn.index("The file was created.") < drawn.index("Review the changed files")
    assert drawn.index("Review the changed files") < drawn.index("The change is written."), drawn


def test_the_suggestion_is_never_submitted_as_the_users_next_task():
    """It is shadow text. The turn after it must be the user's own words, and
    the transcript must show no second turn triggered by the hint."""
    asked = []

    class Watching(Replies):
        def input(self, prompt=""):
            answer = Replies.input(self, prompt)
            asked.append(answer)
            return answer

    box = Workspace()
    screen = io.StringIO()
    saved = (TMT.console, TMT.ensure_api_key, TMT.run_startup,
             TMT.ensure_git_identity, TMT.ask_model)
    previous_cwd = Path.cwd()
    try:
        os.chdir(str(box.path))
        TMT.console = Watching(["do the thing", "quit"])
        TMT.ensure_api_key = lambda: True
        TMT.run_startup = lambda **kwargs: "start"
        TMT.ensure_git_identity = lambda *a, **k: None
        TMT.ask_model = Turn(REAL_TURN)
        with contextlib.redirect_stdout(screen):
            TMT.main([])
    finally:
        os.chdir(str(previous_cwd))
        (TMT.console, TMT.ensure_api_key, TMT.run_startup,
         TMT.ensure_git_identity, TMT.ask_model) = saved
        box.close()

    assert asked == ["do the thing", "quit"], asked
    assert "Review the changed files" not in asked


def test_a_turn_that_offers_no_suggestion_still_gets_a_true_one():
    """A missing hint cannot fail a turn, and the substitute has to be read
    off what the turn did rather than invented."""
    replies = [
        json.dumps({"action": "write_file", "path": "a.txt", "content": "x\n"}),
        json.dumps({"action": "done", "message": "Written."}),
    ]
    drawn, _ = run_turn(replies)
    assert "Review the changed files" in drawn, drawn
    assert "Written." in drawn, drawn


def test_an_over_long_suggestion_is_cut_to_five_words_before_it_is_shown():
    """The model does not get to decide the length."""
    replies = [json.dumps({"action": "done", "message": "Done.",
                           "next_step": "Please go and run the integration tests now"})]
    drawn, _ = run_turn(replies)
    assert "Please go and run the" in drawn, drawn
    assert "integration tests now" not in drawn, drawn


BATCH_TURN = [
    json.dumps({"actions": [
        {"action": "write_file", "path": "a.txt", "content": "one\n",
         "progress": "Writing the first file."},
        {"action": "write_file", "path": "b.txt", "content": "two\n",
         "progress": "Writing the second file."},
        {"action": "done", "message": "Both written.",
         "next_step": "Review the new files"},
    ]}),
]


def test_progress_on_a_batch_entry_is_shown_like_any_other():
    """A batch carries its progress on the entries rather than on the object
    around them, and only top-level values reach the stream. Reading the entry
    before it runs is what keeps a batched turn as legible as a stepped one."""
    drawn, files = run_turn(BATCH_TURN)
    for message in ("Writing the first file.", "Writing the second file."):
        assert message in drawn, (message, drawn)
    assert drawn.index("Writing the first file.") < drawn.index("Writing the second file.")
    # Both actions really ran, and the suggestion from the batch's final entry
    # was picked up rather than falling back.
    assert files.get("a.txt") == "one\n" and files.get("b.txt") == "two\n", sorted(files)
    assert "Review the new files" in drawn, drawn
