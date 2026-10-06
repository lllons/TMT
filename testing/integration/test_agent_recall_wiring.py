"""Up, through the real reader, the real box and the real session loop.

`test_agent_history` proves what going back MEANS with no terminal in the
room. This proves the wiring: that the keystroke arrives, that it arrives only
in the field that asked for it, that the box says which of the two things
Enter is about to do, and that a corrected line reaches the session loop in
its own position rather than behind the mistake it corrected.

The defect this closes is a shape the notes already record twice in other
places: something the user can see is happening and cannot reach. A line
queued during a turn was on screen as a number ("3 queued") and there was no
gesture that led to it.
"""

import io
import threading
import time

import agent_history
import agent_menu
from agent_ui import display_width, strip_ansi


class Tty(io.StringIO):
    encoding = "utf-8"

    def isatty(self):
        return True


class Keys:
    """A scripted key reader. Returns "" once exhausted, like a real tick."""

    def __init__(self, *strokes):
        self.strokes = list(strokes)
        self.lock = threading.Lock()

    def __call__(self, *args, **kwargs):
        with self.lock:
            if self.strokes:
                return self.strokes.pop(0)
        return ""


def drain(reader, settled, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with reader.lock:
            done = not reader.strokes
        if done and settled():
            return True
        time.sleep(0.01)
    return False


def scripted(*strokes):
    """A reader for `PromptBox.ask`, which reads on the calling thread."""
    it = iter(strokes)
    return lambda *a, **k: next(it)


def typeahead(*strokes, **kw):
    """A started TypeAhead over a scripted reader, plus its reader."""
    reader = Keys(*strokes)
    typed = agent_menu.TypeAhead(reader=reader, **kw)
    assert typed.start()
    return typed, reader


# --- the keystroke reaches the field that asked for it, and no other ---------

def test_the_arrows_are_history_keys_only_where_they_are_asked_for():
    """Every other text field in TMT is one line with nothing behind it -- an
    API key, the uninstall word -- and in those an arrow is a tick, which is
    what their own tests assert. So this is opt-in by name rather than four
    more rows in the shared key table."""
    for stroke in ("\x1b[A", "\x1bOA", "up"):
        assert agent_menu.normalize_text_key(stroke) == ("", "")
        assert agent_menu.normalize_text_key(stroke, allow_multiline=True) == ("", "")
        assert agent_menu.normalize_text_key(stroke, allow_history=True) == ("key", "up")
    for stroke in ("\x1b[B", "\x1bOB", "down"):
        assert agent_menu.normalize_text_key(stroke) == ("", "")
        assert agent_menu.normalize_text_key(stroke, allow_history=True) == ("key", "down")


def test_j_and_k_are_still_letters_of_a_task():
    """They move a menu cursor. In a field they are characters, and a history
    that swallowed them would make two letters untypeable."""
    for letter in ("j", "k", "J", "K"):
        assert agent_menu.normalize_text_key(letter, allow_history=True) == ("char", letter)


def test_the_api_key_screen_is_untouched_by_any_of_this():
    """It calls `_next_text_key` with neither flag, so an arrow there is what
    it always was: nothing. A key has no history and half of one pasted from
    a recalled line would be worse than none."""
    import inspect
    source = inspect.getsource(agent_menu.api_key_screen)
    assert "allow_history" not in source, source
    assert agent_menu._next_text_key(lambda: "\x1b[A") == ("", "")
    assert agent_menu._next_text_key(lambda: "up") == ("", "")


# --- editing a line that has not run yet -------------------------------------

def test_up_during_a_turn_brings_back_the_line_just_queued():
    typed, reader = typeahead(*(list("aad a test") + ["\r", "up"]))
    try:
        assert drain(reader, lambda: typed.text()[0] == "aad a test"), typed.text()
        assert typed.pending() == 1
        assert typed.recall.current() is not None
        assert typed.recall.current().editable is True
    finally:
        typed.stop()


def test_the_edit_replaces_the_queued_line_where_it_stands():
    """The queue is an ORDER. A correction that queued itself behind the
    mistake would run the mistake first and the fix afterwards."""
    queue = agent_history.PendingQueue()
    strokes = (list("one") + ["\r"] + list("tow") + ["\r"] + list("three") + ["\r"]
               + ["up", "up", "\x15"] + list("two") + ["\r"])
    typed, reader = typeahead(*strokes, queue=queue)
    try:
        assert drain(reader, lambda: queue.texts() == ["one", "two", "three"]), \
            queue.texts()
    finally:
        typed.stop()
    assert queue.count() == 3, "an edit adds nothing"


def test_an_empty_edit_drops_the_queued_line_and_only_that_one():
    queue = agent_history.PendingQueue()
    strokes = (list("keep") + ["\r"] + list("drop") + ["\r"]
               + ["up", "\x15", "\r"])
    typed, reader = typeahead(*strokes, queue=queue)
    try:
        assert drain(reader, lambda: queue.texts() == ["keep"]), queue.texts()
    finally:
        typed.stop()


def test_esc_while_editing_a_queued_line_leaves_the_queue_alone():
    """A recalled line is a COPY on the screen until Enter. Clearing the box
    has never been a way to delete anything and this must not make it one."""
    queue = agent_history.PendingQueue()
    strokes = list("careful") + ["\r", "up", "\x15", "\x1b"]
    typed, reader = typeahead(*strokes, queue=queue)
    try:
        assert drain(reader, lambda: typed.text()[0] == ""), typed.text()
        assert queue.texts() == ["careful"], queue.texts()
        assert typed.active, "esc must not stop the reader"
    finally:
        typed.stop()


def test_the_draft_survives_a_look_at_what_was_queued():
    """The one thing a read-only gesture must never do is destroy what the
    user was in the middle of writing."""
    strokes = list("queued") + ["\r"] + list("half a thought") + ["up", "down"]
    typed, reader = typeahead(*strokes)
    try:
        assert drain(reader, lambda: typed.text()[0] == "half a thought"), typed.text()
        value, cursor = typed.text()
        assert cursor == len("half a thought")
        assert typed.recall.current() is None
    finally:
        typed.stop()


def test_a_line_queued_during_an_earlier_turn_is_still_reachable():
    """The reason the queue is shared with the session loop rather than owned
    by the reader. One of these is built per turn and the queue outlives all
    of them: somebody who queued three lines during one turn and wants to fix
    the third while the NEXT turn runs is asking about a line the current
    reader never saw."""
    queue = agent_history.PendingQueue()
    first, reader = typeahead(*(list("from turn one") + ["\r"]), queue=queue)
    try:
        assert drain(reader, lambda: queue.count() == 1)
    finally:
        first.stop()
    second, reader = typeahead("up", queue=queue)
    try:
        assert drain(reader, lambda: second.text()[0] == "from turn one"), second.text()
        assert second.recall.current().editable is True
    finally:
        second.stop()


# --- re-entering something that has already been answered --------------------

def test_an_answered_line_comes_back_and_is_entered_again_as_a_new_one():
    history = agent_history.History()
    history.record("run the tests")
    queue = agent_history.PendingQueue()
    typed, reader = typeahead("up", *(list(" again") + ["\r"]),
                              queue=queue, history=history)
    try:
        assert drain(reader, lambda: queue.texts() == ["run the tests again"]), \
            queue.texts()
    finally:
        typed.stop()
    assert history.lines() == ["run the tests"], "the answered line is unchanged"


def test_pending_lines_are_reached_before_answered_ones():
    history = agent_history.History()
    history.record("answered")
    queue = agent_history.PendingQueue()
    queue.add("waiting")
    typed, reader = typeahead("up", queue=queue, history=history)
    try:
        assert drain(reader, lambda: typed.text()[0] == "waiting"), typed.text()
    finally:
        typed.stop()


# --- the box between turns ---------------------------------------------------

def test_ask_walks_the_history_and_returns_the_edited_line():
    history = agent_history.History()
    history.record("run the tests")
    history.record("commit and push")
    box = agent_menu.PromptBox(stream=Tty(), history=history,
                               reader=scripted("\x1b[A", "\x1b[A", "\x1b[B",
                                               "!", "\r"))
    assert box.ask("Describe your next task") == "commit and push!"


def test_ask_puts_the_untouched_placeholder_back_when_the_cursor_returns():
    """A gesture that changed nothing must leave the box in the state it was
    found in, shadow text included."""
    history = agent_history.History()
    history.record("an old task")
    box = agent_menu.PromptBox(stream=Tty(), history=history,
                               reader=scripted("\x1b[A", "\x1b[B", "\r"))
    assert box.ask("Describe your next task") == ""


def test_ask_with_nothing_behind_it_ignores_the_arrows_entirely():
    box = agent_menu.PromptBox(stream=Tty(),
                               reader=scripted("\x1b[A", "\x1b[B", "\x1b[A",
                                               *(list("typed") + ["\r"])))
    assert box.ask("") == "typed"


def test_the_cursor_goes_with_the_box():
    """Left set, the relay's footer for the turn about to start would inherit
    a position in a list nobody is walking and draw a sentence about it under
    an empty field."""
    history = agent_history.History()
    history.record("something")
    box = agent_menu.PromptBox(stream=Tty(), history=history,
                               reader=scripted("\x1b[A", "\r"))
    assert box.ask("") == "something"
    assert box.recall is None


def test_a_piped_run_reads_a_line_and_never_a_key():
    """`is_interactive` gates the whole raw path, so a redirect, a pipe and
    the suite get exactly the box they always got."""
    class NotATty(io.StringIO):
        encoding = "utf-8"

        def isatty(self):
            return False

    box = agent_menu.PromptBox(stream=NotATty(), instream=io.StringIO("a task\n"),
                               history=agent_history.History())
    assert box.ask("") == "a task"
    # And no cursor is left standing. That path returns before the `finally`
    # that takes one down, so one built above it would outlive the call with
    # nothing to clear it.
    assert box.recall is None


# --- what the screen says about it -------------------------------------------

def test_the_box_says_which_of_the_two_things_enter_will_do():
    """Up puts a COPY of something already entered into the field, and from
    the outside the two kinds look identical -- one will replace a task still
    waiting, the other will be entered again as a new one."""
    queue = agent_history.PendingQueue()
    history = agent_history.History()
    history.record("answered once")
    queue.add("still waiting")
    stream = Tty()
    box = agent_menu.PromptBox(stream=stream, queue=queue, history=history)
    typed, reader = typeahead("up", stream=stream, queue=queue, history=history)
    box.typeahead = typed
    try:
        assert drain(reader, lambda: typed.text()[0] == "still waiting")
        rows = [strip_ansi(row) for row in box.running_lines(size=(100, 24))]
        assert any("Editing a queued task" in row for row in rows), rows
        assert any("Enter replaces it" in row for row in rows), rows
        with reader.lock:
            reader.strokes.append("up")
        assert drain(reader, lambda: typed.text()[0] == "answered once")
        rows = [strip_ansi(row) for row in box.running_lines(size=(100, 24))]
        assert any("From history" in row for row in rows), rows
        assert not any("Editing a queued task" in row for row in rows), rows
    finally:
        typed.stop()


def test_a_stopped_reader_draws_no_sentence_about_a_line_it_is_not_showing():
    """The reader is stopped at the end of every turn, and its cursor may
    still be standing on an entry. The box goes back to the plain hint at
    that moment, so a note about a recalled line would be describing text
    that is no longer in the field."""
    stream = Tty()
    queue = agent_history.PendingQueue()
    queue.add("waiting")
    box = agent_menu.PromptBox(stream=stream, queue=queue)
    typed, reader = typeahead("up", stream=stream, queue=queue)
    box.typeahead = typed
    assert drain(reader, lambda: typed.recall.current() is not None)
    typed.stop()
    rows = [strip_ansi(row) for row in box.running_lines(size=(100, 24))]
    assert not any("Editing a queued task" in row for row in rows), rows
    assert box.recall is None


def test_ask_writes_an_edited_pending_line_back_rather_than_answering_with_it():
    """The queue is asked before the box is drawn, so a corrected line is
    picked up from there in its own position on the next pass. Returning it
    here as well would run it twice."""
    queue = agent_history.PendingQueue()
    queue.add("first")
    queue.add("tow")
    box = agent_menu.PromptBox(stream=Tty(), queue=queue,
                               history=agent_history.History(),
                               reader=scripted("\x1b[A", "\x15",
                                               *(list("two") + ["\r"])))
    assert box.ask("") == "", "an edit of something waiting is not this answer"
    assert queue.texts() == ["first", "two"], queue.texts()


def test_an_untouched_box_says_nothing_about_any_of_this():
    """A box built the way they were built before this existed draws exactly
    the rows it drew before it existed."""
    stream = Tty()
    plain = agent_menu.PromptBox(stream=stream)
    with_recall = agent_menu.PromptBox(stream=stream, history=agent_history.History(),
                                       queue=agent_history.PendingQueue())
    editor = agent_menu.LineEditor("Describe your next task")
    assert plain.lines(editor, size=(100, 24)) == \
        with_recall.lines(editor, size=(100, 24))


def test_the_hint_names_the_key_once_there_is_something_to_go_back_to():
    """A queued line the user cannot find their way back to is a line they
    have to type again, and this row is the only place on screen that knows
    there is anything to go back to."""
    stream = Tty()
    queue = agent_history.PendingQueue()
    queue.add("one")
    queue.add("two")
    box = agent_menu.PromptBox(stream=stream, queue=queue)
    typed, reader = typeahead(stream=stream, queue=queue)
    box.typeahead = typed
    try:
        rows = " ".join(strip_ansi(row) for row in box.running_lines(size=(100, 24)))
        assert "2 queued tasks" in rows, rows
        assert "Up to edit" in rows, rows
    finally:
        typed.stop()


def test_one_waiting_task_is_a_task_and_not_tasks():
    assert agent_menu._queued_hint(1).startswith("1 queued task -")
    assert agent_menu._queued_hint(2).startswith("2 queued tasks -")
    assert agent_menu._queued_hint(0) == ""


def test_the_hint_gives_up_the_explanation_before_it_gives_up_the_key():
    """The ladder, not just its top rung. A test that only looks at a wide
    terminal never sees the middle form at all -- and the middle form is the
    one that matters, because it is what a narrow window keeps: the count and
    the key, which are the two things somebody can act on."""
    seen = [agent_menu._queued_hint(3, width) for width in range(8, 90)]
    assert "3 queued tasks - Up to edit, and they run when this one finishes" in seen
    assert "3 queued tasks - Up to edit" in seen, sorted(set(seen))
    assert "3 queued tasks" in seen
    # And it never degrades past naming the key while there is room for it.
    for width in range(8, 90):
        hint = agent_menu._queued_hint(3, width)
        assert hint in agent_menu._queued_hint(3), hint
        if display_width("3 queued tasks - Up to edit") <= width:
            assert "Up to edit" in hint, (width, hint)


def test_every_row_of_the_running_box_still_fits_at_every_width():
    """A row drawn to the last column soft-wraps, and the wrapped half is a
    screen line `LiveRegion` does not know it has drawn -- from then on every
    repaint writes into the middle of a row instead of over the top of it.
    Two new rows can reach this box, so both are swept."""
    stream = Tty()
    queue = agent_history.PendingQueue()
    for index in range(12):
        queue.add("a queued task number %d that is quite long" % index)
    box = agent_menu.PromptBox(stream=stream, queue=queue)
    typed, reader = typeahead("up", stream=stream, queue=queue)
    box.typeahead = typed
    try:
        assert drain(reader, lambda: typed.recall.current() is not None)
        for columns in range(20, 140):
            for rows in (box.running_lines(size=(columns, 24)),
                         box.lines(agent_menu.LineEditor(
                             agent_menu._queued_hint(12, columns)),
                             size=(columns, 24))):
                for row in rows:
                    assert display_width(strip_ansi(row)) <= columns - 1, \
                        (columns, repr(strip_ansi(row)))
    finally:
        typed.stop()


def test_the_note_gives_up_words_rather_than_being_cut_through_one():
    """The launch screen's subtitle rule. The row is fitted rather than
    wrapped, so a sentence too long for it is cut mid-word -- and half an
    instruction the user has to guess the end of is worth nothing."""
    class Standing:
        def current(self):
            return agent_history.Entry("x", handle=1)

    seen = [agent_menu.recall_note(Standing(), width) for width in range(10, 90)]
    assert "Editing a queued task" in seen
    assert "Editing a queued task - Enter replaces it" in seen
    assert all(row in agent_menu._RECALL_EDITING for row in seen), \
        [row for row in seen if row not in agent_menu._RECALL_EDITING]
    assert agent_menu.recall_note(None) == ""


def test_the_note_reads_with_the_colour_stripped():
    stream = Tty()
    queue = agent_history.PendingQueue()
    queue.add("waiting")
    box = agent_menu.PromptBox(stream=stream, queue=queue)
    box.recall = agent_history.Recall(lambda: queue.entries())
    box.recall.back(agent_menu.LineEditor())
    rows = box._recall_row(80)
    assert rows and "\x1b" in rows[0], "it is dim on a terminal that has colour"
    assert "Editing a queued task" in strip_ansi(rows[0])


# --- the session loop --------------------------------------------------------

def test_the_loop_takes_one_queued_line_at_a_time_and_never_drains_them():
    """A line lifted out of the queue into a local list would be waiting to
    run with no way back to it. The loop asks the queue for one line per pass
    instead, so everything still waiting is still editable."""
    import inspect
    import TMT
    source = inspect.getsource(TMT._session_loop)
    assert "pending.pop()" in source, source
    assert "queued.extend" not in source
    assert "typed_history.record(task)" in source


def test_a_dispatched_line_moves_from_the_queue_into_the_history():
    """A line is in exactly one tier at a time. Recorded when it is
    dispatched rather than when it is typed: recorded at the box it would be
    in both, and an edit of it would leave the version before the edit
    sitting underneath the corrected one."""
    queue = agent_history.PendingQueue()
    history = agent_history.History()
    queue.add("the task")
    assert [entry.editable for entry in
            agent_history.entries_for(queue, history)] == [True]
    task = queue.pop()
    history.record(task)
    entries = agent_history.entries_for(queue, history)
    assert [(entry.text, entry.editable) for entry in entries] == \
        [("the task", False)]
