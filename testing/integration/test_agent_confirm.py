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
- the session's own approver puts the question through the live region and
  reads the answer, and prints nothing past the region;
- with nobody there to ask -- a piped run -- the answer is no and the file
  stays, which is the direction every terminal question in TMT fails in.

Moving the question into the region fixed the printing half. The READ was
still done with the region UP, which left one stray box rule per question --
the terminal echoes the answer and the Enter that ends it, so the caret
finished a row below where `LiveRegion` believed it was and the next repaint
landed one row high. The answer now goes through `LiveRegion.ask_below`,
which takes the region down with the caret restored, reads, and paints it
again. Also pinned here:

- the region is DOWN at the moment the read happens, and the caret is back.
  The discriminator is the LAST thing written before the read: a paint is
  full of escapes, so "an escape reached the stream" proves nothing, and only
  the show-cursor escape is a region letting go;
- the caret is visible for the read, which it was not before -- `_paint`
  hides it and nothing was giving it back, so the user typed their answer
  with nothing on screen saying where the letters would land;
- an interrupt or an input that ends still paints the region again on the
  way out, and the type-ahead reader is still stopped for the length of the
  question and started again after.
"""

import builtins
import contextlib
import io
import re

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
    """The two ways permanent text reaches the scrollback past a live region.

    Both record into `written`, because what a test of the approver cares
    about is that the text went through the region rather than past it.
    `asked` records the questions that were also READ through it, which is the
    half a `write_above` cannot do: it prints and paints again, leaving the
    region up while the answer is typed onto rows it believes are its own.
    """

    def __init__(self):
        self.written = []
        self.asked = []

    def write_above(self, text):
        self.written.append(text)

    def ask_below(self, text, read):
        self.written.append(text)
        self.asked.append(text)
        return read()


class Pad:
    def __init__(self):
        self.spent = []
        self.taken = []

    def spend(self, text):
        self.spent.append(text)

    def take(self, lines):
        self.taken.append(lines)


class Box:
    typeahead = None


class Terminal(io.StringIO):
    """A stream that claims to be a console, so the region really paints."""

    encoding = "utf-8"

    def isatty(self):
        return True


# Every escape, not only the colour ones: the region writes cursor moves and
# the show/hide pair, and it is the LAST of them before a read that says
# whether the region let go of its rows.
ESCAPE_RE = re.compile("\033" + r"\[[0-9;?]*[A-Za-z]")
SHOW_CURSOR = "\033[?25h"


def escapes_in(text):
    return ESCAPE_RE.findall(text)


@contextlib.contextmanager
def terminal(answer, interactive=True):
    """A console that is (or is not) a terminal and answers `answer`.

    `answer` may be a callable, which is how a test observes the terminal at
    the moment the answer is read rather than afterwards.
    """
    saved_interactive = agent_menu.is_interactive
    saved_input = builtins.input
    agent_menu.is_interactive = lambda stream=None: interactive
    builtins.input = (answer if callable(answer)
                      else (lambda prompt="": answer))
    try:
        yield
    finally:
        agent_menu.is_interactive = saved_interactive
        builtins.input = saved_input


@contextlib.contextmanager
def keyboard(keys, interactive=True):
    """Raw keys for `choose`, in order, and a console that claims to be one."""
    saved_interactive = agent_menu.is_interactive
    saved_read = agent_menu.read_key
    pressed = list(keys)
    agent_menu.is_interactive = lambda stream=None: interactive

    def read_key(raw=False):
        key = pressed.pop(0)
        return key() if callable(key) else key

    agent_menu.read_key = read_key
    try:
        yield
    finally:
        agent_menu.is_interactive = saved_interactive
        agent_menu.read_key = saved_read


def test_the_sessions_approver_puts_a_deletion_question_through_the_live_region():
    """Through the region, never printed past it -- and ASKED through it
    rather than written above it, because the answer has to be read with the
    region down. A `write_above` prints and paints again, so the answer is
    typed onto rows the region believes are still its own."""
    relay, pad = Relay(), Pad()
    approve = TMT._command_approval(Box(), {"relay": relay}, pad)
    screen = io.StringIO()
    with terminal("y"), contextlib.redirect_stdout(screen):
        answer = approve("Delete a.txt?")
    assert answer == "y", answer
    assert relay.written == ["Delete a.txt?\n" + TMT._APPROVE_ONCE], relay.written
    assert relay.asked == relay.written, relay.asked
    assert pad.spent == relay.written
    # One more row than the question needs: the terminal echoes the answer and
    # the Enter that ends it, so that row is spent out of the scrollback too.
    # A pad that did not count it would put the region back one row too low.
    assert pad.taken == [1], pad.taken
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


# --- the region itself, while the answer is being typed -----------------------

def live_relay(screen):
    """A real live region, painted, with a status row and a box in it."""
    import agent_live_renderer

    relay = agent_live_renderer.LiveRelay(
        stream=screen, ansi=True, footer=lambda: ["  > the box"])
    relay.set_status("### 42% Patching")
    relay._repaint()
    assert relay.region._drawn, "nothing is on screen to be taken down"
    return relay


def observed(relay, screen):
    """What the terminal and the region look like AT the moment of the read."""
    return {"drawn": relay.region._drawn,
            "hidden": relay.region._cursor_hidden,
            "stream": screen.getvalue()}


def test_the_region_is_down_and_the_caret_is_back_when_an_answer_is_read():
    """The residue of the ten stray box tops. Moving the QUESTION into the
    region fixed the printing half; the READ was still done with the region
    up, and the terminal echoes the answer and the Enter that ends it -- so
    the caret finished a row below where `LiveRegion` believed it was, the
    next repaint landed one row high, and one copy of the region's top row
    was orphaned in the scrollback per approval. Nine deletions in one
    multi_tool left nine stray box rules.

    The discriminator is the LAST escape written before the read. A paint is
    full of escapes, so "an escape reached the stream" proves nothing: a paint
    hides the caret and only an erase that restores gives it back, so the
    show-cursor escape is the region actually letting go of its rows."""
    screen = Terminal()
    relay = live_relay(screen)
    seen = {}

    def answering(prompt=""):
        seen.update(observed(relay, screen))
        return "y"

    approve = TMT._command_approval(Box(), {"relay": relay}, Pad())
    with terminal(answering):
        answer = approve("Delete a.txt?")

    assert answer == "y", answer
    assert seen["drawn"] == 0, "the region still owned rows while the user typed"
    # The caret is back. It was switched off for the whole question before
    # this: `_paint` hides it and only the prompt box or `clear` gave it back,
    # so there was nothing on screen saying where the letters would appear.
    assert seen["hidden"] is False, "the answer was typed at a hidden caret"
    before = seen["stream"]
    question = "Delete a.txt?\n" + TMT._APPROVE_ONCE
    assert question in before, before[-200:]
    # Everything up to the question: the last thing the region did before
    # handing the rows over.
    assert escapes_in(before.split(question)[0])[-1] == SHOW_CURSOR, \
        escapes_in(before.split(question)[0])[-4:]
    # And it is painted again afterwards, so the turn carries on into a region
    # that is still where it was.
    assert relay.region._drawn, "the region was never painted again"
    relay.abort()


def test_a_one_keystroke_question_reads_with_the_region_down_as_well():
    """`choose` reads ONE KEYSTROKE rather than a line, so it takes the same
    route for the same reason: a raw read with the region up leaves the caret
    somewhere the next repaint does not expect. It costs the pad nothing
    beyond the question, because a raw key is not echoed and no row of the
    scrollback is spent on the answer."""
    screen = Terminal()
    relay = live_relay(screen)
    pad = Pad()
    seen = {}

    def pressed():
        seen.update(observed(relay, screen))
        return "2"

    choose = TMT._question_asker(Box(), {"relay": relay}, pad)
    with keyboard([pressed]):
        answer = choose("Which one?\n  1) keep it\n  2) replace it", ("1", "2"))

    assert answer == "2", answer
    assert seen["drawn"] == 0, "the region still owned rows while the key was read"
    assert seen["hidden"] is False, "the question was put at a hidden caret"
    assert "Which one?" in seen["stream"], seen["stream"][-200:]
    assert pad.spent and pad.taken == [], pad.taken
    assert relay.region._drawn, "the region was never painted again"
    relay.abort()


def test_an_interrupt_at_the_question_still_gives_the_region_back():
    """Ctrl-C at the prompt means the user wants the turn to stop, and the
    loop already knows how to end one -- so it is deliberately not caught
    here. What must not happen is the region staying down behind it: the
    repaint is in a `finally`, so an exception on the way out still leaves the
    screen where the rest of the loop expects to find it.

    The region has to have been DOWN for that to be worth asserting, which is
    also what makes this a test of the fix rather than of the old shape: with
    the read done under a region that was never taken down, the rows were
    never given up and there was nothing to give back."""
    for ending in ("interrupt", "eof"):
        screen = Terminal()
        relay = live_relay(screen)
        seen = {}

        def pressed(ending=ending):
            seen.update(observed(relay, screen))
            if ending == "eof":
                raise EOFError
            return "\x03"

        choose = TMT._question_asker(Box(), {"relay": relay}, Pad())
        with keyboard([pressed]):
            if ending == "interrupt":
                raised = None
                try:
                    choose("Which one?", ("1",))
                except KeyboardInterrupt:
                    raised = True
                assert raised, "Ctrl-C was swallowed at the question"
            else:
                # The input ended underneath the question: nobody was there to
                # answer it, which is not the same as somebody declining.
                assert choose("Which one?", ("1",)) is None
        assert seen["drawn"] == 0, (ending, "the region still owned its rows")
        assert relay.region._drawn, (ending, "the region was left down")
        relay.abort()


def test_the_typeahead_reader_is_stopped_while_the_region_is_down_for_the_read():
    """Two readers on one stdin take it in turns to swallow the user's
    characters, so the type-ahead reader is stopped before the question is
    put and started again after. That guarantee predates this fix; what is
    pinned here is that it still holds at the one moment it matters, which
    has moved: the read now happens with the region DOWN, and both facts have
    to be true at the same instant."""
    class Reader:
        def __init__(self):
            self.active = True
            self.events = []

        def stop(self):
            self.active = False
            self.events.append("stop")
            return True

        def start(self):
            self.active = True
            self.events.append("start")

    class Typing(Box):
        pass

    screen = Terminal()
    relay = live_relay(screen)
    reader = Reader()
    box = Typing()
    box.typeahead = reader

    seen = {}

    def answering(prompt=""):
        reader.events.append("read")
        seen.update(observed(relay, screen))
        seen["reading"] = reader.active
        return "y"

    approve = TMT._command_approval(box, {"relay": relay}, Pad())
    with terminal(answering):
        assert approve("Delete a.txt?") == "y"
    assert reader.events == ["stop", "read", "start"], reader.events
    assert seen["reading"] is False, "two readers were on stdin at once"
    assert seen["drawn"] == 0, "the region still owned rows while the user typed"
    relay.abort()
