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
            "EFFORT_FILE": agent_config.EFFORT_FILE,
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
        # What the loop reported through the console rather than through the
        # transcript -- the errors and refusals. They go nowhere near stdout,
        # so a test that wants to see them has to look here.
        self.printed = []

    def input(self, prompt=""):
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def print(self, *args, **kwargs):
        return None

    def said(self):
        return "\n".join(self.printed)


class Reporting(Replies):
    """A Replies that keeps what the loop printed through the console.

    The loop's errors -- Bad JSON, an invalid action, a stream failure -- go to
    `console.print`, not to stdout, so a test that captures only the drawn
    screen cannot see them at all.
    """

    def print(self, *args, **kwargs):
        self.printed.append(" ".join(str(value) for value in args))


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


def test_the_session_opens_at_the_top_of_the_window_once():
    """The startup menu has just been on this screen. Clearing it puts the
    session at the top of the window, so the header is the first thing on it
    and everything after reads downward from there.

    Once, and before the header: clearing afterwards would erase it."""
    calls = []
    saved = TMT.clear_screen
    try:
        def watched(*args, **kwargs):
            calls.append(len(args))
            return False
        TMT.clear_screen = watched
        drawn = run_session(["", "quit"])
    finally:
        TMT.clear_screen = saved
    assert len(calls) == 1, calls
    # A pipe has no screen to clear, so a captured run gets no escape -- the
    # real one clears, this one just proves it asked.
    assert "\033[2J" not in drawn, repr(drawn[:40])
    assert starts_with_wordmark(drawn), repr(drawn[:60])


def test_the_session_header_is_drawn_once_however_many_turns_are_taken():
    """The header states what the session was started with, and none of that
    changes while the loop is running. Redrawing it before every prompt pushed
    the conversation off the screen for no new information.

    The clock is not part of it. That is the fact which does change, so it is
    stated on the prompt box -- once per question, drawn again with the box it
    belongs to -- and a session that asked four questions has four of them."""
    drawn = run_session(["", "", "", "quit"])
    assert prompt_boxes(drawn) == 4, (prompt_boxes(drawn), drawn)
    # The wordmark belongs to the header, so one of it across four turns says
    # the header was drawn once. Counted by its TOP ROW rather than by the
    # word: on a window with room the header draws the five-row block, and
    # the letters are then glyphs rather than characters.
    assert wordmarks(drawn) == 1, drawn
    assert len(re.findall(r"\d\d:\d\d:\d\d", drawn)) == 4, drawn


def wordmark_rows():
    """The first row of the wordmark, in each form a terminal can show it in.

    The header signs the screen with the five-row block where there is room
    for it and with the plain word where there is not, and a plain console
    gets the same block in `#`. Counting the literal string "TMT" stopped
    being the same question the day the block arrived.
    """
    import agent_menu
    # The leading space is part of it: every row TMT draws leaves the first
    # column clear for the markers and the '>', so `render_banner` indents the
    # block by one and the plain wordmark by one. Without it `startswith`
    # misses a header that is on screen and correct.
    top = " " + agent_menu.LOGO[0]
    return (top, top.replace("█", "#"), " TMT")


def wordmarks(drawn):
    """How many times the header signed this output, in any of its forms.

    Counted at the START of a row, not anywhere in the text. The wordmark IS a
    row -- every form of it is the first thing on a line of its own -- and the
    header now hangs a tip under it, so the letters TMT turn up inside a
    sentence a few rows further down. A substring count read those as
    signatures, and it would have gone wrong on exactly the windows too small
    for the block: the narrow form of the wordmark and a tip mentioning TMT
    are the same three letters after a space.
    """
    rows = [re.sub(r"\033\[[0-9;]*m", "", line) for line in drawn.splitlines()]
    for form in wordmark_rows():
        count = sum(1 for line in rows if line.startswith(form))
        if count:
            return count
    return 0


def starts_with_wordmark(drawn):
    """Whether the output opens on the wordmark, whichever form it took."""
    head = drawn.lstrip("\n")
    return any(head.startswith(form) for form in wordmark_rows())


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
import agent_model
import agent_ui
from agent_live_renderer import LiveRegion


def test_an_event_never_reports_an_outcome_the_action_did_not_have():
    """The one rule the transcript cannot bend. A result is only read for
    success or failure when it is the action's own report; a program's output
    is data. Scanning it for words like "failed" called a fully green test run
    a failure, which is a lie told confidently."""
    green = agent_actions.action_event("bash", {"command": "python run_tests.py"},
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


# --- what a ranged read says it read ----------------------------------------
#
# Every one of these drives the real `read_lines` through `execute_action`
# rather than handing `action_event` a string somebody typed here, because the
# whole point of the range is that it comes back off what the action actually
# returned. A fabricated result would test the parser against itself.

NUMBERED = "\n".join("line %d" % n for n in range(1, 41)) + "\n"


def read_lines_event(**keys):
    """Run one real read_lines in the active workspace, return its event."""
    obj = dict(keys, action="read_lines")
    return agent_actions.action_event(
        "read_lines", obj, agent_actions.execute_action(obj))


def test_a_ranged_read_says_which_lines_it_read():
    """"Read Lines app.py" is the same row for the fortieth read of a file as
    for the first, and which part of the file was looked at is most of what a
    ranged read means. It is also measurable, so it is said."""
    box = Workspace(files={"app.py": NUMBERED})
    try:
        box.use()
        event = read_lines_event(path="app.py", start=12, end=15)
        assert event.message == "Read Lines (12-15) app.py", event.message
        assert event.kind == "file_read", event.kind

        # A single line is still start-end. "(12)" beside "(12-15)" reads as a
        # count of lines to anyone who has not been told otherwise.
        one = read_lines_event(path="app.py", start=7, end=7)
        assert one.message == "Read Lines (7-7) app.py", one.message
    finally:
        box.close()


def test_the_range_shown_is_what_was_read_not_what_was_asked_for():
    """read_lines clamps to the file: 1-500 of a forty-line file is answered
    with 1-40, and an absent end is filled in with the last line. The row is
    built from the gutter that came back, so it says forty both times. Taking
    the numbers off the request instead would print a range nobody read."""
    box = Workspace(files={"app.py": NUMBERED})
    try:
        box.use()
        overshot = read_lines_event(path="app.py", start=1, end=500)
        assert overshot.message == "Read Lines (1-40) app.py", overshot.message

        to_the_end = read_lines_event(path="app.py", start=38)
        assert to_the_end.message == "Read Lines (38-40) app.py", to_the_end.message
    finally:
        box.close()


def test_a_read_that_returned_no_lines_claims_no_range():
    """A missing file, a range the wrong way round and a start past the end
    all come back as a sentence or as nothing, and none of them says which
    lines were read. The row drops back to the plain label rather than
    guessing one out of the request."""
    box = Workspace(files={"app.py": NUMBERED})
    try:
        box.use()
        for keys in ({"path": "gone.py", "start": 1, "end": 20},
                     {"path": "app.py", "start": 30, "end": 10},
                     {"path": "app.py", "start": 900, "end": 950}):
            event = read_lines_event(**keys)
            assert event.message == "Read Lines %s" % keys["path"], event.message
    finally:
        box.close()

    # The parser is asked directly for the one shape no real result can carry.
    # read_lines renders its block with range(start, end + 1), so the last
    # gutter is never below the first -- but a range printed backwards would
    # be a lie on the row, and the guard against it has to be reachable by
    # something or it is only a comment.
    assert agent_actions._lines_read("   30 | a\n   10 | b") is None


def test_a_file_whose_own_lines_look_like_gutters_is_read_honestly():
    """The number on the row is TMT's own numbering, not the file's contents.
    A file that happens to contain "  99 | ..." would hand a looser reading
    the wrong range, and it would look entirely plausible."""
    box = Workspace(files={"log.txt": "   99 | first\n  100 | second\n  101 | third\n"})
    try:
        box.use()
        event = read_lines_event(path="log.txt", start=1, end=3)
        assert event.message == "Read Lines (1-3) log.txt", event.message
    finally:
        box.close()


def test_only_the_ranged_read_gains_a_range():
    """read_file takes no range and read_lines is the only action whose extent
    is a fact. Nothing else on the transcript changes shape."""
    box = Workspace(files={"app.py": NUMBERED})
    try:
        box.use()
        whole = agent_actions.action_event(
            "read_file", {"path": "app.py"},
            agent_actions.execute_action({"action": "read_file", "path": "app.py"}))
        assert whole.message == "Read File app.py", whole.message

        # And the batch path is the same path: it composes its events through
        # action_event, so a read inside a batch is described identically.
        obj = {"action": "read_lines", "path": "app.py", "start": 2, "end": 4}
        batch = agent_actions.batch_events(
            [obj], [agent_actions.execute_action(obj)])
        assert [event.message for event in batch] == ["Read Lines (2-4) app.py"], batch
    finally:
        box.close()


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
            try:
                obj = json.loads(raw)
            except ValueError:
                # A reply that cannot be read reports no progress and no next
                # step, which is what the real parser does with one: it never
                # reaches the keys. Scripting an unreadable reply has to be
                # possible here, because that is the case the loop hands back.
                obj = {}
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
    json.dumps({"action": "end_conversation", "message": "The change is written.",
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
    for message in expected:
        assert drawn.count(message) == 1, (message, drawn.count(message))
    # The hint's one appearance is the shadow text in the box waiting for the
    # next task, after the answer rather than before it. It is a drawing and
    # not a value -- that it never becomes input is covered separately -- and
    # it is not also announced in the reply, which would be the same sentence
    # twice in two styles a few rows apart.
    assert drawn.index(expected[-1]) > drawn.index("The change is written."), drawn
    assert "Next:" not in drawn, drawn

    # The work was real: the action actually wrote the file it reported.
    assert files.get("notes.txt") == "one\ntwo\nthree\n", sorted(files)
    # notes.txt did not exist, so this was a creation: it gained three lines
    # and lost none, and "none" there is a measurement rather than a guess.
    assert "Created file: notes.txt" in drawn, drawn
    assert "+3 -0" in drawn, drawn


def test_the_suggestion_reaches_the_next_prompt_and_nowhere_else():
    """It is shadow text in the box that asks the next question, and that is
    the whole of where it appears. It used to be printed as a lead-in above
    the answer as well, which put the same five words on screen twice, a few
    rows apart, in two different styles -- and announced, inside the reply,
    a line the user was about to read under their own cursor."""
    drawn, _ = run_turn(REAL_TURN)
    hint = "Review the changed files"
    assert drawn.count(hint) == 1, drawn
    # After the work and after the answer: it is drawn in the next box, not
    # in the reply that came before it.
    assert drawn.index("The file was created.") < drawn.index(hint), drawn
    assert drawn.index("The change is written.") < drawn.index(hint), drawn
    # And it is on the marker row of a prompt box, not on a row of its own.
    marker_rows = [line for line in drawn.splitlines() if line.startswith(" > ")]
    assert any(hint in line for line in marker_rows), marker_rows


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
    # Nor is the opening placeholder, which is the same thing on the first
    # question of a session: the box opens with it drawn and the buffer empty.
    assert agent_ui.OPENING_SUGGESTION not in asked, asked
    assert agent_ui.OPENING_SUGGESTION in screen.getvalue(), screen.getvalue()


def drive_session(answers, replies):
    """Run TMT.main through `answers`, answering with `replies` in order.

    Returns (what was drawn, the message list of every request made, and the
    console the loop reported its errors through).
    """
    seen = []

    def watching_model(messages, on_event=None):
        seen.append([dict(message) for message in messages])
        return replies[min(len(seen) - 1, len(replies) - 1)]

    box = Workspace()
    screen = io.StringIO()
    saved = (TMT.console, TMT.ensure_api_key, TMT.run_startup,
             TMT.ensure_git_identity, TMT.ask_model)
    previous_cwd = Path.cwd()
    try:
        os.chdir(str(box.path))
        console = TMT.console = Reporting(answers)
        TMT.ensure_api_key = lambda: True
        TMT.run_startup = lambda **kwargs: "start"
        TMT.ensure_git_identity = lambda *a, **k: None
        TMT.ask_model = watching_model
        with contextlib.redirect_stdout(screen):
            TMT.main([])
    finally:
        os.chdir(str(previous_cwd))
        (TMT.console, TMT.ensure_api_key, TMT.run_startup,
         TMT.ensure_git_identity, TMT.ask_model) = saved
        box.close()
    return screen.getvalue(), seen, console


def test_a_turn_that_failed_still_leaves_its_question_in_the_context():
    """The bug this exists for: it took an answer to be recorded at all, so a
    turn that ended in a stream failure, a circuit break, an unreadable reply
    or a run out of steps dropped the user's QUESTION with it. The next
    question arrived with no sign the exchange had happened, which is exactly
    what "it has no context between prompts" looked like from outside."""
    # Unreadable replies all the way down, so the retries really are spent
    # rather than the turn ending on the first one. One more than the ceiling
    # allows: the first attempt plus MAX_INVALID_RETRIES corrections.
    #
    # Each one different, so it is the retry ceiling being reached and not the
    # circuit breaker, which stops three IDENTICAL replies and would otherwise
    # get there first. Both are wanted; this is the test for one of them.
    attempts = TMT.MAX_INVALID_RETRIES + 1
    drawn, seen, console = drive_session(
        ["translate the readme", "did that work?", "quit"],
        ['{"action": "end_conversation" "message": "broken %d"}' % number
         for number in range(attempts)]
        + [json.dumps({"action": "end_conversation", "message": "Second turn answered."})])

    # It really did keep asking, and it really did stop.
    assert len(seen) == attempts + 1, len(seen)
    assert "Gave up" in console.said(), console.said()
    # The second question carries the first, and says plainly what became of
    # it -- said by TMT, on the question, not as words the model never wrote.
    second = seen[-1]
    assert [message["role"] for message in second] == ["system", "user", "user"], second
    assert "translate the readme" in second[1]["content"], second[1]
    assert "could not produce a usable action" in second[1]["content"], second[1]
    assert second[-1]["content"] == "did that work?", second[-1]


def test_a_model_repeating_one_unreadable_reply_is_stopped_before_the_ceiling():
    """The retry budget answers a model that is getting it wrong; the circuit
    breaker answers one that is stuck. A model sending the SAME reply three
    times is not correcting anything, so waiting for the full budget would be
    six more requests to learn what the third already said.

    This path had no test at all before retries made it reachable: nothing
    used to send the same reply three times, because the first one ended the
    turn."""
    drawn, seen, console = drive_session(
        ["translate the readme", "quit"],
        ['{"action": "end_conversation" "message": "stuck"}'] * 6)

    assert "Circuit Breaker" in console.said(), console.said()
    # Three identical replies, and it stopped there rather than spending the
    # whole retry budget.
    assert len(seen) == 4, len(seen)


def test_an_unreadable_reply_is_handed_back_and_the_turn_carries_on():
    """The defect this exists for. An action that failed validation had always
    been returned to the model to be corrected, but a reply that would not
    parse ended the turn where it stood -- throwing away whatever the turn had
    already done and making the user ask the same question again. The two are
    the same kind of mistake and now get the same answer."""
    drawn, files = run_turn([
        '{"action": "write_file" "path": "a.txt"}',      # no comma: unreadable
        json.dumps({"action": "write_file", "path": "notes.txt",
                    "content": "one\ntwo\n"}),
        json.dumps({"action": "end_conversation", "message": "Wrote notes.txt."}),
    ])

    # The work happened, in the same turn, with no second question asked.
    assert files.get("notes.txt") == "one\ntwo\n", files
    assert "Wrote notes.txt." in drawn, drawn
    # And what could not be read was never shown as the answer.
    assert "broken" not in drawn, drawn
    assert '"path": "a.txt"' not in drawn, drawn


def test_a_truncated_reply_is_handed_back_like_any_other_unreadable_one():
    """A stream that stops mid-object leaves brace-balanced nonsense or no
    close at all. Either way it is unreadable, and unreadable is recoverable."""
    drawn, files = run_turn([
        '{"action": "write_file", "path": "a.txt", "content": "half',
        json.dumps({"action": "write_file", "path": "whole.txt", "content": "x"}),
        json.dumps({"action": "end_conversation", "message": "Recovered."}),
    ])
    assert files.get("whole.txt") == "x", files
    assert "a.txt" not in files, files            # the truncated one never ran
    assert "Recovered." in drawn, drawn


def test_arguments_the_action_cannot_use_end_the_action_and_not_the_program():
    """`execute_action` reads its arguments straight off the object, so a key
    of the wrong type raised through the loop and out of main -- a model
    writing a number where a path belongs could end the session. It is the
    model's mistake and the model can correct it."""
    drawn, files = run_turn([
        json.dumps({"action": "write_file", "path": 123, "content": "x"}),
        json.dumps({"action": "write_file", "path": "ok.txt", "content": "x"}),
        json.dumps({"action": "end_conversation", "message": "Wrote ok.txt."}),
    ])
    assert files.get("ok.txt") == "x", files
    assert "Wrote ok.txt." in drawn, drawn


def test_a_missing_key_and_an_unknown_action_are_both_handed_back():
    """Two validation failures that used to be handed back only for a batch.
    Neither ends the task; both are corrected in the same turn."""
    drawn, files = run_turn([
        json.dumps({"action": "patch_file", "path": "a.txt", "search": "x"}),
        json.dumps({"action": "frobnicate", "path": "a.txt"}),
        json.dumps({"action": "write_file", "path": "fixed.txt", "content": "y"}),
        json.dumps({"action": "end_conversation", "message": "Corrected twice."}),
    ])
    assert files.get("fixed.txt") == "y", files
    assert "Corrected twice." in drawn, drawn


def test_a_message_is_shown_and_the_work_still_happens():
    """The progress defect. The verb that ends a task is right for an answer
    and wrong for the sentence a model writes before it starts, so "I'll
    inspect the files first" ended the turn with nothing inspected and nothing
    to show for it. `send_message` is the verb that says it without ending."""
    drawn, files = run_turn([
        json.dumps({"action": "send_message",
                    "message": "I'll inspect the files first."}),
        json.dumps({"action": "write_file", "path": "made.txt", "content": "z"}),
        json.dumps({"action": "end_conversation", "message": "Inspected and written."}),
    ])

    assert "I'll inspect the files first." in drawn, drawn   # the user saw it
    assert files.get("made.txt") == "z", files               # and it carried on
    assert "Inspected and written." in drawn, drawn
    # The message is not the answer, so it is not in the answer's box.
    assert drawn.index("I'll inspect the files first.") < drawn.index("Inspected and written."), drawn


def test_the_old_respond_with_final_false_still_says_what_is_coming():
    """The behaviour this replaced still happens, because models still write
    it. `respond` with `"final": false` was the old way to talk without
    ending, and `canonical_action` translates it to a `send_message` before
    anything in the loop looks at it -- so a reply written under the old rules
    does exactly what it always did. A rename is not worth a lost answer, and
    it is not worth a lost sentence either."""
    drawn, files = run_turn([
        json.dumps({"action": "respond", "final": False,
                    "message": "I'll inspect the files first."}),
        json.dumps({"action": "write_file", "path": "made.txt", "content": "z"}),
        json.dumps({"action": "end_conversation", "message": "Inspected and written."}),
    ])

    assert "I'll inspect the files first." in drawn, drawn
    assert files.get("made.txt") == "z", files
    assert "Inspected and written." in drawn, drawn


def test_send_message_cannot_end_the_turn_however_it_is_written():
    """The reason it is a separate verb rather than a flag. The old shape was
    `respond` plus `"final": false`, and forgetting the flag ended the task
    silently, in the worst direction. There is nothing to forget now:
    `send_message` carries the key that used to make a `respond` terminal and
    is still not terminal, because the key is not read here or anywhere."""
    drawn, files = run_turn([
        # "final": true is exactly what would END the old verb. It must not
        # end this: send_message has no terminal meaning to switch on.
        json.dumps({"action": "send_message", "message": "Starting now.",
                    "final": True}),
        json.dumps({"action": "write_file", "path": "after.txt", "content": "q"}),
        json.dumps({"action": "end_conversation", "message": "Ran to the end."}),
    ])
    assert "Starting now." in drawn, drawn
    assert files.get("after.txt") == "q", files
    assert "Ran to the end." in drawn, drawn


def test_send_message_leads_a_batch_without_finishing_it():
    """A batch whose first entry speaks must still run the entries after it."""
    drawn, files = run_turn([
        json.dumps({"actions": [
            {"action": "send_message", "message": "Writing both files now."},
            {"action": "write_file", "path": "one.txt", "content": "1"},
            {"action": "write_file", "path": "two.txt", "content": "2"},
            {"action": "end_conversation", "message": "Both written."},
        ]}),
    ])
    assert "Writing both files now." in drawn, drawn
    assert files.get("one.txt") == "1" and files.get("two.txt") == "2", files
    assert "Both written." in drawn, drawn


def test_several_messages_can_come_before_the_answer():
    """Nothing about a message is once-only. A model that narrates two steps
    before it finishes is doing what it was asked to do."""
    drawn, files = run_turn([
        json.dumps({"action": "send_message", "message": "First I look."}),
        json.dumps({"action": "send_message", "message": "Now I write."}),
        json.dumps({"action": "write_file", "path": "two.txt", "content": "q"}),
        json.dumps({"action": "end_conversation", "message": "Both said, one written."}),
    ])
    assert "First I look." in drawn and "Now I write." in drawn, drawn
    assert files.get("two.txt") == "q", files
    assert "Both said, one written." in drawn, drawn


def test_a_result_still_reaches_the_model_after_a_message():
    """A message must not break the chain the loop runs on: the action after
    it still runs, and its result still comes back as the user turn the model
    reads next."""
    drawn, seen, _ = drive_session(
        ["look around", "quit"],
        [json.dumps({"action": "send_message", "message": "Looking."}),
         json.dumps({"action": "list_files"}),
         json.dumps({"action": "end_conversation", "message": "Looked."})])

    assert len(seen) == 3, len(seen)
    # The request after the message carries it as the assistant's turn and
    # TMT's nudge as the user's, so the model is looking at what it said.
    after_speaking = seen[1]
    assert after_speaking[-2]["role"] == "assistant", after_speaking[-2]
    assert "Looking." in after_speaking[-2]["content"], after_speaking[-2]
    assert "not finished" in after_speaking[-1]["content"], after_speaking[-1]
    # And the request after the action carries that action's real result.
    assert "Result:" in seen[2][-1]["content"], seen[2][-1]


def test_a_message_followed_by_an_unreadable_reply_still_recovers():
    """The two failures together, which is the shape a real turn takes: the
    model says what it will do, then fumbles the JSON for doing it."""
    drawn, files = run_turn([
        json.dumps({"action": "send_message", "message": "About to write."}),
        '{"action": "write_file", "path" "broken.txt"}',
        json.dumps({"action": "write_file", "path": "good.txt", "content": "v"}),
        json.dumps({"action": "end_conversation", "message": "Written after all."}),
    ])
    assert "About to write." in drawn, drawn
    assert files.get("good.txt") == "v", files
    assert "broken.txt" not in files, files
    assert "Written after all." in drawn, drawn


def test_a_retry_never_runs_the_action_that_already_ran_a_second_time():
    """An action runs only after its arguments have parsed and validated, so
    a reply that could not be read cannot have run anything -- and the reply
    handed back is the unreadable one, never the action before it. `append`
    is the test that can tell: run twice, it says so."""
    drawn, files = run_turn([
        json.dumps({"action": "write_file", "path": "log.txt", "content": ""}),
        json.dumps({"action": "append_file", "path": "log.txt", "content": "once\n"}),
        '{"action": "end_conversation" "message": "unreadable"}',
        json.dumps({"action": "end_conversation", "message": "Appended once."}),
    ])
    # Twice would read "once\nonce\n". The append ran before the unreadable
    # reply and is not re-sent with it: what goes back is the reply that could
    # not be read, never the action that already succeeded.
    assert files.get("log.txt") == "once\n", files
    assert "Appended once." in drawn, drawn


def test_a_reply_tmt_made_up_is_shown_but_never_recorded_as_the_answer():
    """A stream that died and a reply that could not be read both arrive as a
    `done` carrying an explanation, because the loop has no other shape to
    receive one in. It is shown -- the user has to be told -- and it is not
    written into the session as the model's answer, which used to tell the
    next turn that the model had said "no JSON object found in response"."""
    made_up = agent_model._error_reply("HTTP 429 rate limited")
    drawn, seen, _ = drive_session(
        ["push it", "did that work?", "quit"],
        [made_up, json.dumps({"action": "end_conversation", "message": "ok"})])

    assert "429" in drawn, drawn                       # shown to the user
    second = seen[-1]
    # No assistant turn at all: the model never said this, TMT did. What is
    # carried is the question, and what became of it, in TMT's own voice.
    assert [message["role"] for message in second] == ["system", "user", "user"], second
    carried = second[1]["content"]
    assert carried.startswith("push it"), carried
    assert "[That turn ended with no answer" in carried, carried
    assert "429" in carried, carried


def test_an_invalid_entry_in_a_batch_is_handed_back_rather_than_ending_the_turn():
    """It used to break out of both loops onto a bare `break`: the batch's
    results were thrown away, the model was told nothing, nothing was printed,
    and the turn ended where it stood with work done and no word about why.
    A bad single action has always been handed back to be corrected; a bad
    batch entry now is too."""
    batch = json.dumps({"actions": [
        {"action": "write_file", "path": "a.txt", "content": "one\n"},
        {"action": "write_file"},                      # no content: invalid
    ]})
    drawn, seen, console = drive_session(
        ["do the thing", "quit"],
        [batch, json.dumps({"action": "end_conversation", "message": "Corrected."})])

    assert "Invalid action in batch" in console.said(), console.said()
    assert "Corrected." in drawn, drawn
    # The model was told what was wrong AND what had already run before it.
    assert len(seen) == 2, len(seen)
    handed_back = seen[1][-1]["content"]
    assert handed_back.startswith("INVALID:"), handed_back
    assert "Ran before it:" in handed_back, handed_back
    assert "a.txt" in handed_back, handed_back


def test_the_question_and_the_answer_reach_the_next_turns_request():
    """The end-to-end claim for the session context, made where it can only
    pass for the right reason: at the messages the model is actually handed.

    "Now add percentage support" means nothing on its own. It means what it
    means because of the turn before it, so that turn has to be in the second
    request or the model is answering a fragment."""
    seen = []

    def watching_model(messages, on_event=None):
        seen.append([dict(message) for message in messages])
        return json.dumps({"action": "end_conversation", "message": "Done: %d." % len(seen)})

    box = Workspace()
    screen = io.StringIO()
    saved = (TMT.console, TMT.ensure_api_key, TMT.run_startup,
             TMT.ensure_git_identity, TMT.ask_model)
    previous_cwd = Path.cwd()
    try:
        os.chdir(str(box.path))
        TMT.console = Replies(["Use the Calc.py architecture.",
                               "Now add percentage support.", "quit"])
        TMT.ensure_api_key = lambda: True
        TMT.run_startup = lambda **kwargs: "start"
        TMT.ensure_git_identity = lambda *a, **k: None
        TMT.ask_model = watching_model
        with contextlib.redirect_stdout(screen):
            TMT.main([])
    finally:
        os.chdir(str(previous_cwd))
        (TMT.console, TMT.ensure_api_key, TMT.run_startup,
         TMT.ensure_git_identity, TMT.ask_model) = saved
        box.close()

    assert len(seen) == 2, len(seen)
    # The first question arrives on its own: there is nothing behind it yet.
    assert [message["role"] for message in seen[0]] == ["system", "user"], seen[0]

    second = seen[1]
    roles = [message["role"] for message in second]
    assert roles == ["system", "user", "assistant", "user"], roles
    assert "Calc.py architecture" in second[1]["content"], second[1]
    assert second[-1]["content"] == "Now add percentage support.", second[-1]
    # The carried answer is the JSON action the model speaks in, not the bare
    # sentence. Every other assistant message in a request is a JSON object,
    # and dropping loose prose into the same array put an example of the
    # forbidden shape in front of the model, in its own voice, immediately
    # before asking it not to use that shape.
    carried = json.loads(second[2]["content"])
    assert carried["action"] == "end_conversation", carried
    assert carried["message"].startswith("Done: 1."), carried

    # And the question itself is in the terminal's own scrollback, because the
    # box that collected it is taken down as soon as it is answered.
    assert "> Use the Calc.py architecture." in screen.getvalue(), screen.getvalue()


def test_a_turn_that_offers_no_suggestion_still_gets_a_true_one():
    """A missing hint cannot fail a turn, and the substitute has to be read
    off what the turn did rather than invented."""
    replies = [
        json.dumps({"action": "write_file", "path": "a.txt", "content": "x\n"}),
        json.dumps({"action": "end_conversation", "message": "Written."}),
    ]
    drawn, _ = run_turn(replies)
    assert "Review the changed files" in drawn, drawn
    assert "Written." in drawn, drawn


def test_an_over_long_suggestion_is_cut_to_five_words_before_it_is_shown():
    """The model does not get to decide the length."""
    replies = [json.dumps({"action": "end_conversation", "message": "Done.",
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
        {"action": "end_conversation", "message": "Both written.",
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


def test_a_slash_command_is_answered_by_tmt_and_never_becomes_a_request():
    """The fork, checked where it actually happens. A command must be handled
    before a task is built, and an ordinary line must still go all the way to
    the model -- including one that merely starts with a path."""
    import agent_commands
    import agent_config
    import agent_models
    import shutil as _shutil
    import tempfile

    settings = Path(tempfile.mkdtemp(prefix="tmt_cmd_cli_"))
    saved_effort_file = agent_config.EFFORT_FILE
    saved_effort = agent_config.EFFORT
    saved_model_file = agent_models.MODEL_FILE
    saved_env = os.environ.get("OPENROUTER_MODEL")
    try:
        agent_config.EFFORT_FILE = settings / ".tmt_effort"
        agent_models.MODEL_FILE = settings / ".tmt_model"
        os.environ.pop("OPENROUTER_MODEL", None)
        drawn, seen, _ = drive_session(
            ["fix the bug", "/config", "/effort high", "/clear", "/bogus",
             "/usr/bin/python is broken", "quit"],
            [json.dumps({"action": "end_conversation", "message": "Did it."})])

        # Two lines were tasks; four were commands and never left the loop.
        asked = [request[-1]["content"] for request in seen]
        assert asked == ["fix the bug", "/usr/bin/python is broken"], asked
        for command in ("/config", "/effort high", "/clear", "/bogus"):
            assert command not in asked, command

        # Each was answered on screen instead.
        for expected in ("Configuration", "Effort set to high",
                         "Conversation cleared", "Unknown command"):
            assert expected in drawn, expected
        # And the setting one of them made actually took.
        assert agent_config.EFFORT == "high"
        assert agent_config.max_tokens_for_effort() == 8192
        # No credential reached the screen.
        assert "sk-or" not in drawn, drawn
    finally:
        agent_config.EFFORT_FILE = saved_effort_file
        agent_config.EFFORT = saved_effort
        agent_models.MODEL_FILE = saved_model_file
        os.environ.pop("OPENROUTER_MODEL", None)
        if saved_env is not None:
            os.environ["OPENROUTER_MODEL"] = saved_env
        _shutil.rmtree(settings, ignore_errors=True)
