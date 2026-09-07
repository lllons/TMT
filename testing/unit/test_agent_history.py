"""Walking back through what has already been typed.

The box used to be write-only. A line entered while a turn was running went
onto a queue and could never be looked at again, let alone corrected -- so a
typo in a task queued twenty seconds ago ran as a typo, and the only remedy
was to watch it fail and say it again. And between turns there was no way back
to anything at all: every question had to be typed out in full, every time.

Two tiers, and the difference between them is the whole feature. A PENDING
line has not run yet, so an edit of it REPLACES it where it stands. A RECORDED
line has already been answered, so an edit of it is entered again as a new
line. Everything below is about keeping those two apart, and about the gesture
costing nothing when the user changes their mind.
"""

import agent_history


class Editor:
    """The part of `agent_menu.LineEditor` the recall actually touches.

    Deliberately not the real one: `agent_history` states in its own docstring
    that it knows no terminal and no keys, and a unit test that reached for
    `agent_menu` to prove it would be proving the opposite. The real editor is
    driven end to end in `test_agent_recall_wiring`.
    """

    def __init__(self, value="", placeholder=""):
        self.value = value
        self.cursor = len(value)
        self.pastes = []
        self.placeholder_visible = bool(placeholder)

    def insert(self, text, pasted=False):
        self.placeholder_visible = False
        if pasted and "\n" in text:
            self.pastes.append(text)
            text = "[Pasted text #%d]" % len(self.pastes)
        self.value = self.value[:self.cursor] + text + self.value[self.cursor:]
        self.cursor += len(text)

    def expanded(self):
        if not self.pastes:
            return self.value
        text = self.value
        for index, paste in enumerate(self.pastes, 1):
            text = text.replace("[Pasted text #%d]" % index, paste)
        return text


def queue_of(*lines):
    queue = agent_history.PendingQueue()
    for line in lines:
        queue.add(line)
    return queue


def history_of(*lines):
    history = agent_history.History()
    for line in lines:
        history.record(line)
    return history


def recall_over(queue=None, history=None):
    return agent_history.Recall(
        lambda: agent_history.entries_for(queue, history))


# --- the queue ---------------------------------------------------------------

def test_a_queued_line_keeps_its_place_when_it_is_edited():
    """The queue is an ORDER. A correction that joined the back of it would
    run the mistake first and the fix afterwards, which is the one outcome an
    edit must not be able to produce."""
    queue = queue_of("one", "two", "three")
    line = queue.entries()[1]        # newest first, so this is "two"
    assert line.text == "two"
    assert queue.replace(line.handle, "TWO") is True
    assert queue.texts() == ["one", "TWO", "three"]


def test_an_empty_edit_drops_the_queued_line():
    """The only way to unsay something already queued. Anything else would
    leave the user watching a task they had changed their mind about run."""
    queue = queue_of("one", "two", "three")
    handle = queue.entries()[1].handle
    assert queue.replace(handle, "   ") is True
    assert queue.texts() == ["one", "three"]


def test_replacing_a_line_that_is_gone_says_so_and_changes_nothing():
    queue = queue_of("one")
    handle = queue.entries()[0].handle
    assert queue.pop() == "one"
    assert queue.replace(handle, "edited") is False
    assert queue.texts() == []


def test_a_handle_is_never_reused():
    """Identity, not position. A position shifts under anything that adds or
    removes a line -- which, during a turn, is the user -- so an edit aimed by
    position could land on a line the user never looked at."""
    queue = queue_of("one", "two")
    first, second = queue.entries()[1].handle, queue.entries()[0].handle
    assert first != second
    queue.replace(first, "")
    queue.add("three")
    assert queue.entries()[0].handle not in (first, second)


def test_an_empty_line_is_never_queued():
    queue = agent_history.PendingQueue()
    assert queue.add("") is None
    assert queue.add("   \n ") is None
    assert queue.add(None) is None
    assert queue.count() == 0


def test_pop_takes_one_and_take_takes_them_all():
    queue = queue_of("one", "two", "three")
    assert queue.pop() == "one"
    assert queue.count() == 2
    assert queue.take() == ["two", "three"]
    assert queue.count() == 0
    assert queue.pop() is None


# --- the record --------------------------------------------------------------

def test_the_newest_line_is_the_first_one_reached():
    history = history_of("first", "second", "third")
    assert history.recent() == ["third", "second", "first"]
    assert history.lines() == ["first", "second", "third"]


def test_the_same_line_twice_in_a_row_is_one_entry():
    """As it is in every shell. Pressing Up should reach the thing BEFORE the
    repeat rather than walking through the repeat twice."""
    history = history_of("build", "build", "build")
    assert history.lines() == ["build"]
    history.record("test")
    history.record("build")
    assert history.lines() == ["build", "test", "build"]


def test_an_empty_line_is_never_recorded():
    history = agent_history.History()
    assert history.record("") is False
    assert history.record("   ") is False
    assert history.record(None) is False
    assert history.count() == 0


def test_the_oldest_entry_is_the_one_that_goes():
    """A ring rather than a cap that refuses. The oldest entry is the one
    nobody is reaching for, and a history that stopped recording would be
    silently wrong about what "previous" means."""
    history = agent_history.History(limit=3)
    for index in range(6):
        history.record("task %d" % index)
    assert history.lines() == ["task 3", "task 4", "task 5"]


def test_history_has_no_len_so_an_empty_one_cannot_be_falsy():
    """`Session` defines `__len__`, an empty session is therefore falsy, and
    the caption silently dropped the meter for the whole of a session's first
    question because of it. This one does not offer the same trap."""
    history = agent_history.History()
    assert not hasattr(history, "__len__")
    assert history.count() == 0


# --- the two tiers together --------------------------------------------------

def test_pending_comes_before_recorded_and_only_pending_is_editable():
    queue = queue_of("waiting one", "waiting two")
    history = history_of("answered one", "answered two")
    entries = agent_history.entries_for(queue, history)
    assert [entry.text for entry in entries] == [
        "waiting two", "waiting one", "answered two", "answered one"]
    assert [entry.editable for entry in entries] == [True, True, False, False]


def test_either_tier_may_be_missing():
    assert agent_history.entries_for(None, None) == []
    assert len(agent_history.entries_for(queue_of("a"), None)) == 1
    assert len(agent_history.entries_for(None, history_of("a"))) == 1


# --- the cursor --------------------------------------------------------------

def test_up_walks_back_and_down_walks_forward():
    recall = recall_over(history=history_of("one", "two", "three"))
    editor = Editor()
    assert recall.back(editor) is True and editor.value == "three"
    assert recall.back(editor) is True and editor.value == "two"
    assert recall.back(editor) is True and editor.value == "one"
    assert recall.back(editor) is False, "there is nothing older than the oldest"
    assert editor.value == "one", "a refused step must not disturb the line"
    assert recall.forward(editor) is True and editor.value == "two"


def test_the_half_written_line_is_put_back_exactly():
    """The one thing a read-only gesture must never do is destroy what the
    user was in the middle of writing."""
    recall = recall_over(history=history_of("an old task"))
    editor = Editor("half a thought")
    editor.cursor = 4
    recall.back(editor)
    assert editor.value == "an old task"
    assert recall.forward(editor) is True
    assert editor.value == "half a thought"
    assert editor.cursor == 4, "the caret comes back where it was, not at the end"
    assert recall.forward(editor) is False, "the draft is as far forward as it goes"


def test_the_placeholder_comes_back_with_the_draft():
    """An empty box shows shadow text. Stepping out to look at something and
    stepping back must leave the box in the state it was found in, and a box
    that lost its suggestion on the way would have been visibly changed by a
    gesture that changed nothing."""
    recall = recall_over(history=history_of("an old task"))
    editor = Editor(placeholder="Describe your next task")
    assert editor.placeholder_visible is True
    recall.back(editor)
    assert editor.placeholder_visible is False, "there is text in the field now"
    recall.forward(editor)
    assert editor.placeholder_visible is True
    assert editor.value == ""


def test_the_cursor_says_which_kind_of_line_is_on_screen():
    """The same keystroke does two different things to the two tiers, so
    whoever is drawing the box has to be able to say which."""
    recall = recall_over(queue_of("waiting"), history_of("answered"))
    editor = Editor()
    assert recall.current() is None and recall.active is False
    recall.back(editor)
    assert recall.active is True
    assert recall.current().editable is True
    recall.back(editor)
    assert recall.current().editable is False
    recall.forward(editor)
    recall.forward(editor)
    assert recall.current() is None, "back on the draft, which is nobody's entry"


def test_the_entries_are_re_read_at_every_step():
    """Both tiers move while the box is open -- a turn ends, or the user
    queues another line. A cursor built over a snapshot taken when the box
    opened would offer lines that are no longer there."""
    queue = queue_of("first")
    recall = recall_over(queue)
    editor = Editor()
    recall.back(editor)
    assert editor.value == "first"
    queue.add("second")
    recall.reset()
    recall.back(editor)
    assert editor.value == "second", "the newest is the newest as of now"


def test_a_queue_that_shrinks_under_the_cursor_does_not_strand_it():
    recall = recall_over(queue_of("one", "two", "three"))
    editor = Editor()
    recall.back(editor)
    recall.back(editor)
    recall.back(editor)
    assert recall.position == 3
    recall._entries = lambda: []
    assert recall.forward(editor) is True
    assert recall.position == 0
    assert recall.current() is None


def test_a_recall_that_cannot_read_its_entries_does_nothing_at_all():
    """Every guard on this surface fails towards a keystroke that does
    nothing. The worst outcome available here is an Up that is ignored; the
    alternative is an exception raised out of a reader thread."""
    def broken():
        raise RuntimeError("the queue is on fire")

    recall = agent_history.Recall(broken)
    editor = Editor("mid sentence")
    assert recall.snapshot() == []
    assert recall.back(editor) is False
    assert editor.value == "mid sentence"


def test_reset_forgets_the_position_and_the_stash():
    recall = recall_over(history=history_of("one"))
    editor = Editor("draft")
    recall.back(editor)
    recall.reset()
    assert recall.position == 0
    assert recall.current() is None
    assert recall.forward(editor) is False
    assert editor.value == "one", "reset is not an undo; it forgets, it does not restore"


# --- loading a line back into a field ----------------------------------------

def test_a_block_comes_back_folded_and_expands_to_exactly_what_it_was():
    """Loaded through the editor's own `insert`, so the field's rule about
    blocks applies to a recalled line exactly as it applied when the line was
    first entered. Nothing here knows the folding rules."""
    block = "line one\nline two\nline three"
    recall = recall_over(history=history_of(block))
    editor = Editor()
    recall.back(editor)
    assert "\n" not in editor.value, editor.value
    assert editor.expanded() == block


def test_loading_empties_the_paste_list_first():
    """The recalled text came out of `expanded()`, so any token in it has
    already been resolved. A stale paste left behind would resolve a token in
    the recalled line against text from a completely different one."""
    recall = recall_over(history=history_of("plain line"))
    editor = Editor()
    editor.insert("a\nb", pasted=True)
    assert editor.pastes
    recall.back(editor)
    assert editor.pastes == []
    assert editor.expanded() == "plain line"


def test_a_recalled_line_puts_the_caret_at_the_end():
    recall = recall_over(history=history_of("commit and push"))
    editor = Editor()
    recall.back(editor)
    assert editor.cursor == len("commit and push")


def test_capture_and_restore_survive_an_editor_missing_the_optional_parts():
    """`capture` is asked about whatever the caller's field happens to be, and
    a field with no paste list is a field with no folded pastes rather than a
    reason to raise."""
    class Bare:
        value = "text"
        cursor = 2

    state = agent_history.capture(Bare())
    assert state["value"] == "text" and state["cursor"] == 2
    assert state["pastes"] == [] and state["placeholder_visible"] is False
    editor = Editor()
    assert agent_history.restore(editor, state) is True
    assert (editor.value, editor.cursor) == ("text", 2)
    assert agent_history.restore(editor, None) is False


def test_a_captured_caret_past_the_end_is_clamped_rather_than_trusted():
    editor = Editor()
    agent_history.restore(editor, {"value": "ab", "cursor": 99})
    assert editor.cursor == 2
    agent_history.restore(editor, {"value": "ab", "cursor": -4})
    assert editor.cursor == 0
