"""A deletion asks through the session's approver, never past the live region.

The bug this pins was seen on a real terminal: a multi_tool of nine
`delete_file` calls and one `delete_folder` left ten stray box tops in the
scrollback, one per question. Each deletion confirmed through a bare
`input()`, which printed its question PAST the live region; the next repaint
drew over the question and the region's arithmetic was one row out from then
on. The type-ahead reader on its own thread was competing for the same stdin
the whole time.

`bash` had already solved this: the session puts one `approve` callable in
the action context, and that callable writes the question through the live
region's `write_above` with the type-ahead reader stopped. Deletions now ask
through the same callable. What is pinned here:

- a deletion with an approver in its context asks it, and stdin is NEVER
  read -- `agent_file_ops.input` is replaced with something that raises;
- only "y" and "yes" agree. "always" is a bash notion and there is nothing
  to remember about a file that is about to be gone;
- a context with no approver still gets the console prompt it always had, so
  a direct caller and the threading test keep meaning what they meant;
- the session's own approver puts the question through `write_above` and
  reads the answer, and prints nothing past the region;
- with nobody there to ask -- a piped run -- the answer is no and the file
  stays, which is the direction every terminal question in TMT fails in.
"""

import builtins
import contextlib
import io

import agent_actions
import agent_file_ops
import agent_menu
import TMT

from test_agent_workspace import Workspace

FILES = {
    "a.txt": "one\n",
    "b.txt": "two\n",
    "c.txt": "three\n",
    "box/inner.txt": "four\n",
}


def project():
    box = Workspace(files=FILES)
    box.use()
    return box


def refusing_input(prompt=""):
    raise AssertionError("input() was read past the live region: %r" % (prompt,))


@contextlib.contextmanager
def stdin_off_limits():
    """Make any bare `input()` in agent_file_ops fail the test outright."""
    original = getattr(agent_file_ops, "input", None)
    agent_file_ops.input = refusing_input
    try:
        yield
    finally:
        if original is None:
            del agent_file_ops.input
        else:
            agent_file_ops.input = original


class Approver:
    """Records every question and answers each from a script."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.questions = []

    def __call__(self, question, pattern=""):
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else ""


def run(obj, approve):
    context = {"push_authorized": False}
    if approve is not None:
        context["approve"] = approve
    return str(agent_actions.execute_action(obj, context))


# --- through the dispatcher -----------------------------------------------------

def test_a_deletion_asks_the_sessions_approver_and_never_reads_stdin():
    box = project()
    try:
        approver = Approver(["y"])
        with stdin_off_limits():
            said = run({"action": "delete_file", "path": "a.txt"}, approver)
        assert said == "Deleted file: a.txt", said
        assert not (box.path / "a.txt").exists()
        assert approver.questions == ["Delete a.txt?"], approver.questions
    finally:
        box.close()


def test_only_a_plain_yes_agrees_to_a_deletion():
    """`yes` in either spelling deletes. Everything else keeps the file --
    including "a", which is bash's "always" and means nothing here."""
    for answer in ("n", "", "no", "a", "always", "allow", False, None, 0, "run"):
        box = project()
        try:
            with stdin_off_limits():
                said = run({"action": "delete_file", "path": "a.txt"}, Approver([answer]))
            assert said == "Delete cancelled", (answer, said)
            assert (box.path / "a.txt").exists(), answer
        finally:
            box.close()
    for answer in ("yes", " Y ", True):
        box = project()
        try:
            with stdin_off_limits():
                said = run({"action": "delete_file", "path": "a.txt"}, Approver([answer]))
            assert said == "Deleted file: a.txt", (answer, said)
        finally:
            box.close()


def test_an_approver_that_raises_is_answered_no():
    def broken(question, pattern=""):
        raise RuntimeError("terminal went away")
    box = project()
    try:
        with stdin_off_limits():
            said = run({"action": "delete_file", "path": "a.txt"}, broken)
        assert said == "Delete cancelled", said
        assert (box.path / "a.txt").exists()
    finally:
        box.close()


def test_a_one_argument_approver_is_accepted_too():
    """The shape a test naturally writes, and the shape `agent_bash._ask`
    already accepts."""
    box = project()
    try:
        asked = []
        with stdin_off_limits():
            said = run({"action": "delete_file", "path": "b.txt"},
                       lambda question: asked.append(question) or "y")
        assert said == "Deleted file: b.txt", said
        assert asked == ["Delete b.txt?"]
    finally:
        box.close()


def test_a_folder_deletion_asks_the_same_way_and_names_what_is_inside():
    box = project()
    try:
        approver = Approver(["y"])
        with stdin_off_limits():
            said = run({"action": "delete_folder", "path": "box", "recursive": True}, approver)
        assert said == "Deleted folder: box", said
        assert not (box.path / "box").exists()
        assert approver.questions == ["Delete box and 1 items inside?"], approver.questions
        # And a refusal keeps the folder and everything in it.
        box.path.joinpath("box").mkdir()
        box.path.joinpath("box/inner.txt").write_text("four\n", encoding="utf-8")
        with stdin_off_limits():
            said = run({"action": "delete_folder", "path": "box", "recursive": True}, Approver(["n"]))
        assert said == "Delete cancelled", said
        assert (box.path / "box/inner.txt").exists()
    finally:
        box.close()


def test_with_no_approver_the_console_prompt_is_still_read():
    """A direct caller, a script, the threading test: a context with no
    approver gets the prompt deletions always had, with its old wording."""
    box = project()
    prompts = []
    original = getattr(agent_file_ops, "input", None)
    agent_file_ops.input = lambda prompt="": prompts.append(prompt) or "y"
    try:
        said = run({"action": "delete_file", "path": "c.txt"}, None)
        assert said == "Deleted file: c.txt", said
        assert prompts == ["Delete c.txt? (y/N): "], prompts
        said = agent_file_ops.delete_file("b.txt")
        assert said == "Deleted file: b.txt", said
    finally:
        if original is None:
            del agent_file_ops.input
        else:
            agent_file_ops.input = original
        box.close()


def test_every_deletion_in_a_multi_tool_asks_in_turn_and_none_reads_stdin():
    """The exact shape of the bug: several deletions in one action. Each is
    a question through the approver, in file order, and the answer to one
    does not carry to the next."""
    box = project()
    try:
        # `*.txt` has no `/` in it, so it matches a name at any depth: four
        # files, in sorted order, box/inner.txt among them.
        approver = Approver(["y", "n", "n", "yes"])
        with stdin_off_limits():
            said = run({"action": "multi_tool", "calls": [
                {"action": "delete_file", "for_each": "*.txt"}]}, approver)
        assert said.startswith("multi_tool ran 4 calls."), said
        assert approver.questions == ["Delete a.txt?", "Delete b.txt?",
                                      "Delete box/inner.txt?", "Delete c.txt?"], approver.questions
        assert not (box.path / "a.txt").exists()
        assert (box.path / "b.txt").exists()
        assert (box.path / "box/inner.txt").exists()
        assert not (box.path / "c.txt").exists()
        assert "[2/4] delete_file b.txt\nDelete cancelled" in said, said
    finally:
        box.close()


# --- the session's approver itself ------------------------------------------------

class Relay:
    def __init__(self):
        self.written = []

    def write_above(self, text):
        self.written.append(text)


class Pad:
    def __init__(self):
        self.spent = []

    def spend(self, text):
        self.spent.append(text)


class Box:
    typeahead = None


@contextlib.contextmanager
def terminal(answer, interactive=True):
    """A console that is (or is not) a terminal and answers `answer`."""
    saved_interactive = agent_menu.is_interactive
    saved_input = builtins.input
    agent_menu.is_interactive = lambda stream=None: interactive
    builtins.input = lambda prompt="": answer
    try:
        yield
    finally:
        agent_menu.is_interactive = saved_interactive
        builtins.input = saved_input


def test_the_sessions_approver_puts_a_deletion_question_through_the_live_region():
    """Written with `write_above`, which erases, prints and repaints below,
    and never printed. Printing past a live region is the whole of the bug."""
    relay, pad = Relay(), Pad()
    approve = TMT._command_approval(Box(), {"relay": relay}, pad)
    screen = io.StringIO()
    with terminal("y"), contextlib.redirect_stdout(screen):
        answer = approve("Delete a.txt?")
    assert answer == "y", answer
    assert relay.written == ["Delete a.txt?\n" + TMT._APPROVE_ONCE], relay.written
    assert pad.spent == relay.written
    assert screen.getvalue() == "", screen.getvalue()
    # The hint no longer talks about running something: the same sentence
    # is shown under a command and under a deletion.
    assert "run it" not in TMT._APPROVE_ONCE
    assert "allow it" in TMT._APPROVE_ONCE


def test_the_sessions_approver_and_a_deletion_together_delete_the_file():
    """End to end from the dispatcher into the session's own callable."""
    box = project()
    try:
        relay, pad = Relay(), Pad()
        approve = TMT._command_approval(Box(), {"relay": relay}, pad)
        with terminal("y"), stdin_off_limits():
            said = run({"action": "delete_file", "path": "a.txt"}, approve)
        assert said == "Deleted file: a.txt", said
        assert not (box.path / "a.txt").exists()
        assert relay.written and relay.written[0].startswith("Delete a.txt?"), relay.written
    finally:
        box.close()


def test_with_nobody_to_ask_a_deletion_is_refused_and_the_file_stays():
    """A piped run has a session and an approver, and the approver's first
    question is whether anybody is there. Any doubt means no, so the file
    stays -- where the bare prompt used to read the NEXT TASK LINE off stdin
    as its answer."""
    box = project()
    try:
        relay, pad = Relay(), Pad()
        approve = TMT._command_approval(Box(), {"relay": relay}, pad)
        with terminal("y", interactive=False), stdin_off_limits():
            said = run({"action": "delete_file", "path": "a.txt"}, approve)
        assert said == "Delete cancelled", said
        assert (box.path / "a.txt").exists()
        assert relay.written == [], "a question was drawn with nobody to answer it"
    finally:
        box.close()
