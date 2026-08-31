"""Tests for the reviewbot strip: the rows `agent_panel` draws for the reviewer.

The reviewer used to be invisible. It blocked the main loop for as long as it
took and the only thing on screen about it was `REVIEW 1/3 / ● Running
independent review` in the column -- no tokens, no elapsed time, no activity,
nothing saying whether it was working through the job or reading the same file
for the fortieth time. The strip is the fix, and everything on it was already
being measured; none of it was being shown.

So two things are being protected here, and they pull in opposite directions.

The first is that the strip says what is actually happening: the reviewer's own
declared agenda, its own token figure, its own clock, and a bar filled by the
share of that agenda it has itself reported finished. Nothing here may be
invented -- there is no default agenda anywhere in the runtime, and a test
below says the panel draws none either.

The second is that the strip is decoration, and decoration in TMT is bound by
three rules that have each been paid for once already. It measures with
`display_width` and never with `len`, because a row drawn past `columns - 1`
wraps on the terminals that auto-wrap and marches the whole live frame down the
screen. It adds no colour of its own -- every gradient position it uses is one
an existing event kind already holds. And it does not animate: two calls with
the same inputs return the same strings, because a shimmering readout would
force back the per-tick repaint that was removed to fix the flickering caret.

Every assertion about what is on screen is made with the escapes stripped.
Colour is confirmation here and never the message.

The fake terminal comes from `test_agent_menu`, the same harness
`test_agent_panel` borrows. A second one for the same rows would drift from the
first, and the drift would be silent.
"""

import io
import os

import agent_manager
import agent_panel
import agent_plan
import agent_review
import agent_reviewbot
import agent_ui

from test_agent_menu import Console, visible


# --- the harness -------------------------------------------------------------

# The moment every record in this file is created at. Fixed, because
# `_elapsed_text` reads `time.monotonic()` whenever the caller hands it None,
# and a row whose clock came from the machine is a row two calls cannot agree
# about.
START = 1000.0

TITLES = ("Understand what was asked", "Read the changeset",
          "Read the implementation in context", "Check the tests cover it",
          "Look for regressions")


class Colour:
    """NO_COLOR out of the environment, and put back in close().

    `_supports_color` is `isatty() and not os.environ.get("NO_COLOR")`, so a
    stream that claims to be a tty still draws no colour on a machine that has
    that variable set. The tests that assert an escape is present have to say
    so; the ones that assert text are unaffected either way.
    """

    def __init__(self):
        self.previous = os.environ.pop("NO_COLOR", None)

    def close(self):
        os.environ.pop("NO_COLOR", None)
        if self.previous is not None:
            os.environ["NO_COLOR"] = self.previous


class Hostile:
    """An object that raises on every attribute and on len().

    Stands in for a record or an agenda being written by the reviewer's own
    thread while the renderer thread draws it. What it is really testing is the
    rule rather than the race: decoration is never allowed to end a turn, so
    the strip's job when it cannot read its own state is to draw less, never to
    raise.
    """

    def __getattr__(self, name):
        raise RuntimeError("this object refuses %r" % name)

    def __len__(self):
        raise RuntimeError("this object refuses len()")


def reviewer(items=TITLES, done=0, steps=5, max_steps=20,
             activity="Read Lines agent_review.py", tokens=True):
    """A manager holding one running reviewbot, and that record.

    Built through a real `AgentManager` in its own `_review` slot -- which is
    where `agent_actions` puts it, and why `visible_agents` never returns it
    and it had no row at all until the strip existed. Nothing is started: a
    real reviewer is a model call, and no test here may make one.
    """
    manager = agent_manager.AgentManager(clock=lambda: START)
    record = manager.spawn("review the change", kind="review")
    record.status = agent_manager.Status.RUNNING
    record.started_at = START
    record.steps = steps
    record.max_steps = max_steps
    if items:
        record.agenda = agent_reviewbot.Agenda(list(items))
        for position in range(1, done + 1):
            record.agenda.update(position, "done")
    if activity:
        record.activity = activity
    if tokens:
        manager.set_tokens(record.id, tokens_in=8000, tokens_out=4000)
        manager.set_pending_output(record.id, 840)
    return manager, record


def seen(row):
    """One painted row as the terminal shows it."""
    return agent_ui.strip_ansi(row)


def width_of(row):
    """The columns a painted row occupies.

    `display_width` over `strip_ansi`, never `len`. An escape sequence is made
    of characters that occupy no columns at all, and a full-width character
    occupies two -- counting either way has already marched the live frame down
    the screen in this project.
    """
    return agent_ui.display_width(agent_ui.strip_ansi(row))


def status_row(record, columns, agenda=None, now=START + 18, **kwargs):
    """The reviewbot's own row, at a fixed moment."""
    agenda = record.agenda if agenda is None else agenda
    return agent_panel.reviewbot_status_row(record, columns, Console(), now,
                                            agenda, **kwargs)


def strip_rows(record, columns=80, review=None, now=START + 18, **kwargs):
    """The whole strip, at a fixed moment."""
    return agent_panel.reviewbot_rows(record, review, columns, Console(), now,
                                      **kwargs)


# --- the row itself ----------------------------------------------------------

def test_the_row_is_named_reviewbot_and_never_a_number():
    """A reviewer is not a numbered member of the fleet. It lives in the
    manager's own `_review` slot, `visible_agents` never returns it, and a `#4`
    on this row would send the reader looking for it in the AGENTS panel, where
    it is not. The record still HAS an id, so an implementation that reused
    `agent_status_row` verbatim would draw one -- which is what this kills."""
    manager, record = reviewer()
    text = seen(status_row(record, 100))

    assert agent_panel.REVIEWBOT_NAME == "reviewbot"
    assert "reviewbot" in text, text
    assert "#" not in text, text
    assert "#%s" % record.id not in text, (record.id, text)


def test_the_identity_is_the_last_thing_the_row_gives_up():
    """Walked down the widths one column at a time. Every part of the row goes
    before the word does, and when the word finally goes the row is exactly the
    bar -- never a fragment of it. A row that said `reviewb` would be a readout
    of an agent nobody can name."""
    manager, record = reviewer(done=2)
    bar = agent_panel.reviewbot_bar(
        agent_panel.reviewbot_progress(record, record.agenda), Console())

    lost_at = None
    for columns in range(100, 11, -1):
        text = seen(status_row(record, columns))
        if "reviewbot" in text:
            # Once given up it may never come back: the parts are dropped in
            # one order and a width that regained the name would mean the run
            # of shorter forms is not ordered at all.
            assert lost_at is None, (columns, lost_at, text)
            continue
        if lost_at is None:
            lost_at = columns
        assert text == seen(bar), (columns, text)
    assert lost_at == 19, lost_at


def test_the_row_gives_up_its_parts_from_the_right():
    """Elapsed before tokens, tokens before the agenda count, the count before
    the name. Each shorter form drops the least useful thing left, and the
    order is the whole of what makes a narrow terminal readable rather than
    arbitrary."""
    manager, record = reviewer(done=2)

    everything = seen(status_row(record, 60))
    assert "18s" in everything and "12k" in everything and "2/5" in everything

    no_clock = seen(status_row(record, 43))
    assert "18s" not in no_clock, no_clock
    assert "12k" in no_clock and "2/5" in no_clock, no_clock

    no_tokens = seen(status_row(record, 38))
    assert "12k" not in no_tokens, no_tokens
    assert "2/5" in no_tokens and "reviewbot" in no_tokens, no_tokens

    name_only = seen(status_row(record, 24))
    assert "2/5" not in name_only, name_only
    assert "reviewbot" in name_only, name_only


def test_every_row_of_a_populated_strip_fits_inside_the_spare_column():
    """`columns - 1`, at every width from 20 to 120, measured and not counted.
    A row filled to the last column auto-wraps on some terminals and costs a
    screen line the repaint arithmetic does not know about -- which is the
    defect that marches the whole live frame down the screen on every repaint,
    and it is silent until somebody looks at it."""
    manager, record = reviewer(items=["x" * 72] + list(TITLES), done=3)
    record.activity = "Read Lines " + "some/deep/path/" * 6 + "module.py"

    for columns in range(20, 121):
        for row in strip_rows(record, columns):
            assert width_of(row) <= columns - 1, (columns, width_of(row),
                                                  seen(row))


def test_a_strip_with_no_agenda_fits_every_width_too():
    """The shorter block is measured by the same rule. It is drawn from a
    different run of parts -- there is no `settled/total` in it -- so a fit
    that only ever held for the populated case would be a fit nobody checked
    for the common one."""
    manager, record = reviewer(items=())

    for columns in range(20, 121):
        for row in strip_rows(record, columns):
            assert width_of(row) <= columns - 1, (columns, width_of(row),
                                                  seen(row))


def test_a_wide_character_in_a_title_is_measured_as_two_columns():
    """`display_width`, not `len`. A CJK title counts two columns per character
    and a row that counted them as one would be twice as wide as the arithmetic
    thinks -- the failure DESIGN_PRINCIPLES names as having already happened
    twice in this project."""
    manager, record = reviewer(items=["読み込み中 agent_ui.py の変更を確認する"])
    record.activity = "Read Lines 読み込み中.py"

    for columns in (24, 40, 60, 80):
        for row in strip_rows(record, columns):
            assert width_of(row) <= columns - 1, (columns, seen(row))


def test_the_strip_loses_nothing_but_colour_when_the_escapes_are_stripped():
    """Colour is confirmation and never the message. The painted strip and the
    strip drawn to a stream with no colour at all are the same text, character
    for character, so a reader on a pipe, a redirect or a NO_COLOR terminal
    reads exactly what a reader on a colour terminal reads."""
    colour = Colour()
    try:
        manager, record = reviewer(done=2)
        painted = strip_rows(record, 80)
        bare = agent_panel.reviewbot_rows(record, None, 80,
                                          Console(tty=False), START + 18)

        assert [agent_ui.strip_ansi(row) for row in painted] == bare, painted
        assert any("\033" in row for row in painted), painted
        assert not any("\033" in row for row in bare), bare
    finally:
        colour.close()


def test_the_lines_slot_carries_the_agenda_count_rather_than_line_counts():
    """A reviewer writes nothing, by construction: every writing verb is
    refused to it before dispatch, so `+0 -0` would be two zeroes on every row
    of every review -- a readout of an absence, which is the rule the corner
    meter already refuses. What goes in that slot is the figure that moves.

    The record's line fields are deliberately set here, so an implementation
    that reused `agent_status_row`'s run of parts is caught rather than passing
    because the numbers happened to be zero."""
    manager, record = reviewer(done=2)
    record.lines_added = 17
    record.lines_removed = 3

    text = seen(status_row(record, 100))
    assert "2/5" in text, text
    assert "+17" not in text and "-3" not in text, text
    assert "+0" not in text and "-0" not in text, text


# --- the bar -----------------------------------------------------------------

def test_the_bar_fills_with_the_agendas_own_share_when_there_is_one():
    """The one completion figure in TMT that is not a guess. Every other bar
    shows budget spent, because nothing can know how close a worker is to
    done -- here the reviewer itself declared how many things it would check
    and has itself reported which are finished, so `2 of 5` is a fact it stated
    rather than a number the runtime made up about it."""
    manager, record = reviewer(done=2)

    assert record.agenda.counts() == (2, 5)
    assert agent_panel.reviewbot_progress(record, record.agenda) == 40
    # And not the step budget, which is a different number on the same record.
    assert agent_panel.agent_progress(record) == 25


def test_the_bar_falls_back_to_the_step_budget_when_nothing_was_declared():
    """A reviewer that declared no agenda still has a bar, and it is the same
    figure and the same honesty as every other background agent's: the
    allowance it has spent. Falling back to nothing would leave the one row
    that says a review is alive with no instrument on it."""
    manager, record = reviewer(items=())

    assert record.agenda is None
    assert agent_panel.reviewbot_progress(record, None) == 25
    assert agent_panel.reviewbot_progress(record, None) == \
        agent_panel.agent_progress(record)


def test_a_terminal_reviewer_is_drawn_full_whichever_figure_it_used():
    """Being over is the one moment completion is actually known, so it is the
    one moment a full bar is not a claim. Asserted with an agenda and without
    one, because the terminal check has to come before both branches -- a
    finished reviewer with three of seven items ticked is still finished."""
    manager, record = reviewer(done=3, items=TITLES)
    record.status = agent_manager.Status.COMPLETED
    record.finished_at = START + 30

    assert agent_panel.reviewbot_progress(record, record.agenda) == 100
    assert agent_panel.reviewbot_progress(record, None) == 100


def test_an_empty_agenda_is_zero_and_never_full():
    """A full bar over an empty list is the readout at its most misleading: it
    says every declared check is behind it when nothing was declared at all.
    Zero is the honest painting, and the strip's fallback has to agree with the
    agenda's own arithmetic rather than dividing by a total of nothing."""
    empty = agent_reviewbot.Agenda()
    manager, record = reviewer(items=(), steps=0, max_steps=0)

    assert len(empty) == 0
    assert empty.progress() == 0
    assert agent_panel.reviewbot_progress(record, empty) == 0
    assert agent_panel.reviewbot_progress(record, None) == 0


def test_the_quiet_bar_is_byte_identical_to_every_other_agents():
    """Not merely similar. The reviewbot has to read as one of the background
    agents, drawn where they are drawn in the ramp they are drawn in, and the
    gradient stays the main agent's alone. A bar that differed by one escape
    would be a second visual idea for the same thing."""
    colour = Colour()
    try:
        stream = Console()
        for progress in (0, 1, 40, 99, 100):
            for width in (1, 4, 8, 20):
                assert agent_panel.reviewbot_bar(progress, stream, width=width) \
                    == agent_panel.neutral_bar(progress, stream, width=width), \
                    (progress, width)
                assert agent_panel.reviewbot_bar(progress, stream, alarm=False,
                                                 width=width) \
                    == agent_panel.neutral_bar(progress, stream, width=width)
    finally:
        colour.close()


def test_the_alarm_bar_is_red_and_the_neutral_ramp_is_gone_from_it():
    """One colour rather than a scale, because it is reporting a state and not
    a position. It is the gradient's sharpest distinction spent rather than a
    palette invented for a new feature, and it is why the bar is worth watching
    at the end as well as during: red in the strip says the answer is held.

    The ramp greys have to be absent as well as the red present -- a bar that
    painted both would be two claims on one row."""
    colour = Colour()
    try:
        stream = Console()
        red = "\033[38;2;%d;%d;%dm" % agent_ui._gradient(
            agent_panel.REVIEWBOT_FAILED_POSITION)
        alarm = agent_panel.reviewbot_bar(100, stream, alarm=True)
        quiet = agent_panel.neutral_bar(100, stream)

        assert red in alarm, repr(alarm)
        assert red not in quiet, repr(quiet)
        for step in agent_panel.NEUTRAL_STEPS:
            grey = "38;2;%d;%d;%d" % step
            assert grey not in alarm, (grey, repr(alarm))
            assert grey in quiet, (grey, repr(quiet))
    finally:
        colour.close()


def test_the_unfilled_cells_of_an_alarm_bar_stay_the_one_neutral():
    """`DIM` is the only neutral there is, and it is what an unfilled cell has
    always been. Painting the empty half red as well would say the whole bar is
    the state, which is a bar with no reading on it at all."""
    colour = Colour()
    try:
        alarm = agent_panel.reviewbot_bar(40, Console(), alarm=True)
        assert agent_ui.DIM in alarm, repr(alarm)
        assert seen(alarm) == "███░░░░░", repr(seen(alarm))
    finally:
        colour.close()


def test_a_stream_with_no_colour_draws_one_bar_for_both_forms():
    """Degrade on purpose. With no colour the two forms are the same glyphs and
    carry no escapes at all, so nothing about the alarm survives as an escape
    nobody can see -- and a piped run gets characters rather than a row of
    replacement marks. Blocks where the stream can carry them, `#`/`-` where it
    cannot."""
    blocks = Console(tty=False)
    ascii_only = Console(encoding="cp1252", tty=False)

    for stream, expected in ((blocks, "███░░░░░"), (ascii_only, "###-----")):
        alarm = agent_panel.reviewbot_bar(40, stream, alarm=True)
        quiet = agent_panel.reviewbot_bar(40, stream, alarm=False)
        assert alarm == quiet == expected, (expected, repr(alarm), repr(quiet))
        assert "\033" not in alarm, repr(alarm)


def test_a_colour_terminal_that_cannot_draw_blocks_still_gets_ascii():
    """The two questions are separate: whether the stream has colour, and
    whether it can encode the glyphs. cp1252 is what a plain Windows console
    reports and it can carry neither block, so the bar is `#`/`-` while the
    escapes stay."""
    colour = Colour()
    try:
        bar = agent_panel.reviewbot_bar(40, Console(encoding="cp1252"))
        assert seen(bar) == "###-----", repr(bar)
        assert "\033" in bar, repr(bar)
    finally:
        colour.close()


def test_a_blocking_review_and_one_that_never_reported_raise_the_alarm():
    """`failed` is blocking findings and `error` is a review that never came
    back. They are exactly the two states `agent_review` refuses to release a
    final answer from, which is what makes the red bar mean "the answer is
    being held" rather than "something looks bad"."""
    failed = agent_review.ReviewState()
    failed.begin()
    failed.settle(agent_review.parse_result(
        '{"status": "FAIL", "summary": "the guard is missing",'
        ' "issues": [{"severity": "critical", "title": "no guard",'
        ' "detail": "the write is unguarded"}]}'))
    assert failed.display == "failed"
    assert agent_panel.reviewbot_alarm(failed) is True

    errored = agent_review.ReviewState()
    errored.begin()
    errored.fail("the reviewer timed out")
    assert errored.display == "error"
    assert agent_panel.reviewbot_alarm(errored) is True


def test_a_stale_pass_is_not_an_alarm():
    """A review that really did pass, and then the work moved under it. That is
    amber news about the CODE, not bad news from the reviewer: nothing was
    found, the finding is that nobody has looked at what changed since. The
    column already says so -- `ReviewState.display` answers `warnings` while it
    is stale, precisely so the tick does not appear beside work the gate is
    refusing -- and painting the strip's bar red for it would report a verdict
    the reviewer never gave."""
    review = agent_review.ReviewState()
    review.begin()
    review.settle(agent_review.parse_result(
        '{"status": "PASS", "summary": "the change is correct", "issues": []}'))
    assert agent_panel.reviewbot_alarm(review) is False

    review.note_change("write_file", ["agent_panel.py"])
    assert review.stale is True
    assert review.display == "warnings"
    assert agent_panel.reviewbot_alarm(review) is False

    # And neither is a review that has not reported yet: the bar is the
    # ordinary ramp for the whole of a review, which is the whole of when it is
    # being watched. Red for `running` would say the answer is held before
    # anything at all had been found.
    fresh = agent_review.ReviewState()
    assert agent_panel.reviewbot_alarm(fresh) is False
    fresh.begin()
    assert fresh.display == "running"
    assert agent_panel.reviewbot_alarm(fresh) is False


def test_an_alarm_asked_of_something_that_raises_is_false():
    """Decoration is never allowed to end a turn. The review state is written
    from the loop and read from the renderer, so the answer when it cannot be
    read is the quiet bar -- and quiet is the right direction, because a red
    bar invented from an exception would be a verdict nobody reached."""
    assert agent_panel.reviewbot_alarm(Hostile()) is False
    assert agent_panel.reviewbot_alarm(None) is False


# --- the agenda rows ---------------------------------------------------------

def test_one_row_per_item_carrying_its_label_its_mark_and_its_title():
    """The label is what the reviewer refers to an item by, so it has to be on
    the row it names -- an agenda drawn without `A3` is a list the reviewer's
    own `update A3` cannot be read against."""
    manager, record = reviewer(done=2)
    rows = [seen(row) for row in agent_panel.agenda_rows(record.agenda, 80,
                                                         Console())]

    assert len(rows) == 5, rows
    assert rows[0].strip() == "A1 ✓ Understand what was asked", rows
    assert rows[2].strip() == "A3 ● Read the implementation in context", rows
    assert rows[4].strip() == "A5 ○ Look for regressions", rows
    assert agent_panel.agenda_row_text(
        record.agenda.items[0], agent_panel.agenda_marks(Console())) == \
        "A1 ✓ Understand what was asked"


def test_the_marks_degrade_for_a_stream_that_cannot_carry_them():
    """The fallback is chosen on the marks THEMSELVES as well as on decoration
    generally, because a terminal that draws a box rule but not a tick would
    otherwise put a replacement character on the one column the reader is
    scanning.

    The cp1252 stream matters and is an explicitly documented trap in
    CLAUDE.local.md: a bare `io.StringIO` has no `encoding` attribute at all,
    so `encodable` says it can carry anything and the unicode marks are used.
    A test that reached for the plain buffer would assert the ASCII table and
    be handed the unicode one."""
    unicode_marks = agent_panel.agenda_marks(Console())
    assert unicode_marks["done"] == "✓" and unicode_marks["active"] == "●"
    assert unicode_marks["pending"] == "○" and unicode_marks["skipped"] == "–"

    ascii_marks = agent_panel.agenda_marks(Console(encoding="cp1252"))
    assert ascii_marks["done"] == "+" and ascii_marks["active"] == ">"
    assert ascii_marks["pending"] == "." and ascii_marks["skipped"] == "-"

    assert agent_panel.agenda_marks(io.StringIO()) == unicode_marks

    # The marks are asked about SEPARATELY from decoration generally, and that
    # second question is the one with no natural test: no codec in the standard
    # library carries `█░─『』` and yet refuses `✓●○–`, because DECORATION holds
    # the rarer characters of the two. So the substitution below is the only
    # way to state the rule -- a stream that can draw a box rule but not a tick
    # takes the ASCII table, rather than putting a replacement character on the
    # one column the reader is scanning.
    original = agent_panel.encodable
    try:
        agent_panel.encodable = \
            lambda stream, text: text == agent_ui.DECORATION
        assert agent_panel.agenda_marks(Console()) == ascii_marks
    finally:
        agent_panel.encodable = original
    assert agent_panel.agenda_marks(Console()) == unicode_marks


def test_the_four_marks_are_distinct_from_each_other_in_both_tables():
    """Colour is never the message, so the status has to survive the escapes
    being stripped -- which it only does if no two statuses share a glyph. Two
    items drawn with the same mark would be indistinguishable on exactly the
    rows a reader is scanning to see what is left."""
    for marks in (agent_panel.agenda_marks(Console()),
                  agent_panel.agenda_marks(Console(encoding="cp1252"))):
        assert sorted(marks) == ["active", "done", "pending", "skipped"], marks
        assert len(set(marks.values())) == 4, marks


def test_a_skipped_item_says_why_on_its_own_row():
    """A skip with no reason is indistinguishable from a check that was quietly
    dropped, which is the one thing the agenda exists to make visible. The
    reason is the difference between coverage the reviewer chose not to have
    and coverage it claimed."""
    manager, record = reviewer()
    record.agenda.update(1, "skipped", note="binary file, could not read")
    rows = [seen(row) for row in agent_panel.agenda_rows(record.agenda, 80,
                                                         Console())]

    assert rows[0].strip() == ("A1 – Understand what was asked "
                               "(binary file, could not read)"), rows
    # And the mark is the skipped one rather than the checked one, so a reader
    # scanning the column cannot mistake a set-aside check for a done one.
    assert "✓" not in rows[0], rows


def test_the_window_follows_the_item_being_checked():
    """The one row that must always be on screen is the one the work is
    happening on. An agenda scrolled to its top while the reviewer works on A9
    is a list about a different moment, and it is the moment the user is
    watching for."""
    manager, record = reviewer(items=["Item number %d" % n
                                      for n in range(1, 11)], done=8)
    assert record.agenda.active().id == "A9"

    rows = [seen(row) for row in agent_panel.agenda_rows(record.agenda, 80,
                                                         Console(), height=4)]
    assert len(rows) == 4, rows
    assert any(row.strip().startswith("A9 ") for row in rows), rows
    assert not any(row.strip().startswith("A1 ") for row in rows), rows


def test_the_agenda_never_draws_more_rows_than_it_is_allowed():
    """`AGENDA_MAX_ROWS` is the ceiling whatever the caller asks for, because
    the strip shares the live region with the reply box and the reply gives up
    its rows for this. A twelve-item agenda drawn in full would take the reply
    down to nothing."""
    manager, record = reviewer(items=["Item number %d" % n
                                      for n in range(1, 13)])

    assert len(record.agenda) == 12
    for height in (None, 99, agent_panel.AGENDA_MAX_ROWS + 3):
        rows = agent_panel.agenda_rows(record.agenda, 80, Console(),
                                       height=height)
        assert len(rows) == agent_panel.AGENDA_MAX_ROWS, (height, len(rows))
    assert len(agent_panel.agenda_rows(record.agenda, 80, Console(),
                                       height=2)) == 2


def test_an_agenda_given_no_room_draws_nothing():
    """A height below one is not a request for one row. Drawing one anyway
    would push the region a row taller than the caller worked out it had, and
    the repaint arithmetic below it is counted rather than measured."""
    manager, record = reviewer()
    for height in (0, -1, -20):
        assert agent_panel.agenda_rows(record.agenda, 80, Console(),
                                       height=height) == [], height


def test_a_reviewer_that_declared_nothing_gets_no_agenda_rows():
    """THE rule this feature is built on, and it is the "never fabricate" rule
    on the one display whose whole job is to say what is actually happening.
    The runtime supplies no default agenda and no fallback list anywhere: a
    list this module invented would be a description of what a review is
    SUPPOSED to do, drawn beside a review that might be doing something else,
    and the user would have no way to tell the two apart.

    What is still drawn is the bar and the activity row, because both are
    measured facts about a process that is running."""
    manager, record = reviewer(items=())
    assert record.agenda is None
    assert agent_panel.agenda_rows(None, 80, Console()) == []
    assert agent_panel.agenda_rows(agent_reviewbot.Agenda(), 80,
                                   Console()) == []

    rows = [seen(row) for row in strip_rows(record, 80)]
    assert len(rows) == 2, rows
    assert "reviewbot" in rows[0], rows
    assert rows[1].strip() == "Read Lines agent_review.py", rows
    assert not any(row.strip().startswith("A") for row in rows), rows


# --- the activity row --------------------------------------------------------

def test_the_activity_row_carries_the_label_dim_and_indented():
    """Its own row rather than a part of the row above, for a measured reason:
    a label is `Read Lines agent_review.py` and paths are long, so putting it in
    the fitted run of parts would push the token figure and the elapsed time
    off the row on any terminal narrower than about a hundred columns -- and
    those two are the other half of what was asked for.

    Dim, because it is the one thing here that changes every few seconds and a
    bright row that flickers pulls the eye off the bar it belongs to."""
    colour = Colour()
    try:
        manager, record = reviewer()
        row = agent_panel.reviewbot_activity_row(record, 80, Console())

        assert seen(row) == "  Read Lines agent_review.py", repr(row)
        assert row.startswith(agent_ui.DIM), repr(row)
        assert row.endswith(agent_ui.RESET), repr(row)
    finally:
        colour.close()


def test_an_agent_with_no_activity_draws_no_row_at_all():
    """Nothing to report means nothing drawn, not a blank row. A blank row
    inside the region costs a screen line and says an agent is doing something
    unnamed, which is worse than the bar saying it is running."""
    manager, record = reviewer(activity="")
    for label in ("", "   ", None):
        record.activity = label
        assert agent_panel.reviewbot_activity_row(record, 80, Console()) == "", \
            repr(label)
        rows = strip_rows(record, 80)
        assert not any(seen(row).strip() == "" for row in rows), rows


def test_a_long_label_is_elided_rather_than_wrapped():
    """Middle-elided with the marker the rest of TMT already cuts with, and
    still inside `columns - 1`. Wrapping it would put a second row in a region
    whose height the caller has already worked out, and a region that grew
    under the repaint arithmetic is the frame marching down the screen."""
    manager, record = reviewer()
    record.activity = "Read Lines " + "some/deep/path/" * 8 + "module.py"

    for columns in (24, 40, 80):
        row = agent_panel.reviewbot_activity_row(record, columns, Console())
        text = seen(row)
        assert width_of(row) <= columns - 1, (columns, width_of(row), text)
        assert "\n" not in row, repr(row)
        assert "…" in text or "..." in text, (columns, text)


# --- the strip as a whole ----------------------------------------------------

def test_a_session_that_never_reviews_draws_nothing_at_all():
    """Byte-for-byte the screen it drew before any of this existed. The strip
    is composed into the live region on every repaint, so a row drawn for a
    review that never happened would be paid for by every session."""
    assert agent_panel.reviewbot_rows(None) == []
    assert agent_panel.reviewbot_rows(None, None, 80, Console(), START) == []


def test_a_running_reviewer_draws_the_bar_the_activity_and_the_agenda():
    """The three questions the strip answers, in the order it answers them:
    how far through its own declared list it is and what it has cost, what it
    is doing right now, and what the list actually says."""
    manager, record = reviewer(done=2)
    rows = [seen(row) for row in strip_rows(record, 80)]

    assert len(rows) == 7, rows
    assert rows[0].startswith("███░░░░░ reviewbot"), rows
    assert "2/5" in rows[0] and "12k" in rows[0] and "18s" in rows[0], rows
    assert rows[1].strip() == "Read Lines agent_review.py", rows
    assert [row.strip()[:2] for row in rows[2:]] == \
        ["A1", "A2", "A3", "A4", "A5"], rows


def test_a_finished_reviewer_keeps_its_rows_for_the_retention_window():
    """The same window a finished worker's card is kept for, because they are
    the same fact drawn twice and they have to disappear together -- and
    because the last thing a reader wants is the ticked agenda vanishing at the
    instant the review reports. The clock is driven rather than slept through:
    a test that proved five seconds by waiting five seconds would measure the
    machine."""
    manager, record = reviewer(done=5)
    record.status = agent_manager.Status.COMPLETED
    record.finished_at = START + 30
    edge = record.finished_at + agent_manager.RETENTION_SECONDS

    assert agent_panel._aged_out(record, edge - 0.1) is False
    assert strip_rows(record, 80, now=edge - 0.1), "retained rows are missing"

    assert agent_panel._aged_out(record, edge) is True
    assert strip_rows(record, 80, now=edge) == []
    assert strip_rows(record, 80, now=edge + 60) == []


def test_a_finished_record_with_no_finish_time_does_not_age_out():
    """`finished_at` is stamped by every terminal transition, so a record
    without one is a record whose ending nobody timed. Ageing it out on a
    missing figure would make the strip vanish for a reason nothing measured --
    the same rule `visible_agents` keeps for a worker's card."""
    manager, record = reviewer(done=5)
    record.status = agent_manager.Status.COMPLETED
    record.finished_at = None

    assert agent_panel._aged_out(record, START + 99999) is False
    assert strip_rows(record, 80, now=START + 99999), "the strip vanished"


def test_a_live_reviewer_never_ages_out_however_long_it_takes():
    """Retention is about a finished agent's rows outliving it. A review that
    has been running for an hour is exactly the case the strip was built for,
    and a filter that read the clock rather than the terminal state would take
    the readout away at the moment it is most wanted."""
    manager, record = reviewer(done=1)
    assert record.is_terminal() is False
    assert agent_panel._aged_out(record, START + 99999) is False
    assert strip_rows(record, 80, now=START + 99999)


def test_height_caps_the_whole_block():
    """The strip shares the live region with the reply, and the caller works
    out how many rows it can spare. Every row over that is a row taken from the
    reply without the arithmetic above knowing."""
    manager, record = reviewer(items=["Item number %d" % n
                                      for n in range(1, 11)], done=8)

    assert len(strip_rows(record, 80)) == agent_panel.REVIEWBOT_MAX_ROWS
    for height in range(0, 12):
        rows = strip_rows(record, 80, height=height)
        assert len(rows) <= max(0, min(height, agent_panel.REVIEWBOT_MAX_ROWS)), \
            (height, len(rows))


def test_at_one_row_only_the_instrument_is_drawn():
    """The status row is the one that is never given up, exactly as the
    identity is the part of it that is never given up. It carries the bar, the
    count, the cost and the clock; the activity and the agenda are what a short
    region spends."""
    manager, record = reviewer(done=2)
    rows = strip_rows(record, 80, height=1)

    assert len(rows) == 1, rows
    assert "reviewbot" in seen(rows[0]), rows
    assert strip_rows(record, 80, height=0) == []
    assert strip_rows(record, 80, height=-4) == []


def test_the_block_is_the_status_row_the_activity_row_and_the_agenda():
    """`REVIEWBOT_MAX_ROWS` has to leave room for `AGENDA_MAX_ROWS` under two
    rows of instrument. Raising the agenda ceiling without raising the block's
    would silently drop the last agenda rows, and the drop would look like a
    reviewer that declared fewer items than it did."""
    assert agent_panel.REVIEWBOT_MAX_ROWS == agent_panel.AGENDA_MAX_ROWS + 2


def test_a_record_that_raises_costs_the_strip_its_rows_and_nothing_else():
    """Decoration is never allowed to end a turn. The record is written by the
    reviewer's own thread and read by the renderer's, so the strip's job when
    it cannot read its state is to draw less -- never to raise into a repaint
    and take the session with it."""
    assert agent_panel.reviewbot_rows(Hostile(), None, 80, Console(),
                                      START) == []
    assert agent_panel.reviewbot_rows(Hostile(), Hostile(), 80, Console(),
                                      START, agenda=Hostile()) == []


def test_an_agenda_that_raises_costs_only_the_agenda_rows():
    """The narrower half of the same rule. The bar and the activity row are
    read off the record and are still true, so they are still drawn -- and the
    status row falls back to the step budget rather than reporting a share it
    could not read. Losing the whole strip because one of its three sources
    misbehaved would take away the two facts that were still available."""
    manager, record = reviewer(items=())
    rows = [seen(row) for row in strip_rows(record, 80, agenda=Hostile())]

    assert len(rows) == 2, rows
    assert "reviewbot" in rows[0], rows
    assert "/" not in rows[0], rows
    assert rows[1].strip() == "Read Lines agent_review.py", rows
    assert agent_panel.agenda_rows(Hostile(), 80, Console()) == []


def test_the_strip_composes_beside_a_column_without_overflowing():
    """The shape a real session has: the strip under the main bar on the left,
    a column on the right, both inside one live region. A left row wider than
    its column pushes the column right and wraps the region, which is the
    failure that is not recoverable -- every later repaint is drawn against
    rows that have moved."""
    manager, record = reviewer(done=2)
    plan = agent_plan.Plan(["Inspect the repository", "Implement it",
                            "Add the tests"])
    plan.update(1, "completed")

    left = strip_rows(record, 70)
    right = agent_panel.plan_rows(plan, 28, stream=Console())
    rows = agent_panel.compose(left, right, 100)

    assert len(rows) == max(len(left), len(right))
    for row in rows:
        assert agent_ui.visible_width(row) <= 100, (
            agent_ui.visible_width(row), seen(row))
    assert "reviewbot" in seen(rows[0]), rows
    assert "PLAN 1/3" in seen(rows[0]), rows
    assert any("A3 ● Read the implementation in context" in seen(row)
               for row in rows), [seen(row) for row in rows]


# --- the design pins ---------------------------------------------------------

def test_the_strip_adds_no_colour_of_its_own():
    """A new element takes a position on the one gradient; it never gets a
    palette. Every position the strip uses is one an existing event kind
    already holds -- a checked item is `success`, the one in hand is
    `background_agent`, a skipped one is `warning`, and the alarm is `error`.
    A fifth value here would be a second colour system for a feature that is
    already drawn in the first."""
    known = {agent_panel.DONE_POSITION, agent_panel.AGENT_POSITION,
             agent_panel.KILLED_POSITION, agent_panel.FAILED_POSITION}
    assert known == {95, 40, 60, 10}, known

    assert set(agent_panel._AGENDA_POSITIONS.values()) <= known, \
        agent_panel._AGENDA_POSITIONS
    assert agent_panel.REVIEWBOT_FAILED_POSITION in known
    assert agent_panel.REVIEWBOT_FAILED_POSITION == agent_panel.FAILED_POSITION


def test_a_pending_item_takes_no_position_and_is_drawn_dim():
    """The honest painting: it has not happened. Giving it a place on the
    gradient would say something about work nobody has done, and the one
    neutral is what "recede" already means everywhere else in the interface."""
    colour = Colour()
    try:
        manager, record = reviewer(done=2)
        assert "pending" not in agent_panel._AGENDA_POSITIONS
        rows = agent_panel.agenda_rows(record.agenda, 80, Console())
        # A1 done, A3 active, A4 and A5 pending.
        assert rows[3].startswith(agent_ui.DIM), repr(rows[3])
        assert rows[4].startswith(agent_ui.DIM), repr(rows[4])
        assert not rows[0].startswith(agent_ui.DIM), repr(rows[0])
    finally:
        colour.close()


def test_the_strip_carries_no_animation():
    """Two calls with the same inputs return the same strings. The strip has no
    phase, reads no clock of its own and does not ride the gradient cycle -- a
    shimmering readout would make every composed frame differ from the last,
    which forces back the per-tick repaint that was removed to fix the
    flickering caret. `LiveRegion` skips a repaint only when the frame is
    identical, so sameness here IS the fix."""
    manager, record = reviewer(done=2)

    assert strip_rows(record, 80) == strip_rows(record, 80)
    assert status_row(record, 80) == status_row(record, 80)
    assert agent_panel.agenda_rows(record.agenda, 80, Console()) == \
        agent_panel.agenda_rows(record.agenda, 80, Console())
    assert agent_panel.reviewbot_bar(40, Console(), alarm=True) == \
        agent_panel.reviewbot_bar(40, Console(), alarm=True)


def test_no_row_of_the_strip_ever_carries_a_line_break():
    """A newline inside a live region is not a long row, it is a broken frame:
    the region paints a fixed number of rows and moves the cursor back up by
    that count, so one extra line break leaves every later repaint pointing at
    rows that have moved.

    Both of the strip's variable texts come from a model -- the activity label
    is built from an action name and a path out of the reviewer's JSON, and an
    agenda title is the reviewer's own sentence -- so both can arrive with a
    break in them, and both have to be settled before they are drawn."""
    manager, record = reviewer(items=["Read the\nchangeset", "Check\r\nthe tests"])
    manager.set_activity(record.id, "Read Lines\nagent_review.py")

    rows = strip_rows(record, 80)
    assert rows, rows
    for row in rows:
        assert "\n" not in row and "\r" not in row, repr(row)
