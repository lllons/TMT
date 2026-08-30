"""The AGENTS panel: a column inside the live region at the foot of the screen.

What this is not, and why. The panel was asked for as a full-height column
down the right-hand quarter of the terminal, beside everything already on
screen. TMT cannot hold those rows. The scrollback above the live region is
already printed and is the permanent surface -- the only record a finished
session leaves -- and the two escapes that would let a program own the whole
window are both banned here for the same reason: narrowing the scrolling
region *discards* the lines that scroll out of it rather than pushing them
into the terminal's history, and the alternate screen buffer throws the
history away wholesale. Both were tried. Both destroyed the session record,
and a test greps the modules to keep them out.

So the panel is a column inside the region that is already alive: the block
at the foot of the window that holds the reply, the prompt box and the status
row, and that already repaints in place on its own thread. Each row of that
region composes as `left content + gutter + panel column`. The scrollback
above it stays full width and is never redrawn. That is the honest version of
the picture in this architecture, and it gets the described layout for the
part of the screen that is actually moving.

Everything here is a pure function of a record list and a width. Nothing in
this module writes to a stream, reads a key, takes a lock or asks the clock:
the two callers -- the prompt box and the live relay -- own all of that, and
that is what makes every rule below testable by comparing strings.

Three rules it obeys, all of them from DESIGN_PRINCIPLES.md:

**No colour of its own.** `background_agent` is already an event kind, at
prominence 1 and gradient position 40, and that is the position a running
agent takes here. A finished one takes 95, a failed one 10 and a killed one
60 -- the positions `success`, `error` and `warning` already hold. A new
element takes a place on the one gradient; it never gets a palette.

**Colour is never the message.** Every state is a word, every selection is a
`>` in the first column, and the destructive entry is marked `!` and set off
by a blank row. Read the panel with the escapes stripped and nothing has been
lost but confirmation.

**Measured, never counted.** `display_width` for plain text and
`visible_width` for anything already painted, and the composed row is drawn to
`columns - 1`, because a row filled to the last column wraps on the terminals
that auto-wrap and costs a screen line the repaint arithmetic does not know
about.
"""

import sys
import time

from agent_manager import Status
from agent_ui import (
    DIM, RESET, _color, _supports_color, display_width, encodable,
    fit_to_width, plain_output, strip_ansi, visible_width,
)

# How much of the content width the panel takes, and the range it is allowed
# to take it in. Twenty-eight percent of a wide terminal is a readable column
# without the conversation becoming a strip; the floor is the narrowest column
# a card still fits in (`> #1: 42k T +4120` is seventeen columns) and the
# ceiling stops a very wide window handing the panel half the screen for three
# short cards.
PANEL_SHARE = 0.28
PANEL_MIN = 18
PANEL_MAX = 34

# Blank columns between the two. Two rather than one: a single column between
# a padded left cell and a panel row reads as a space inside a sentence.
GUTTER = 2

# The two degradation thresholds, in REAL terminal columns rather than content
# columns. `_MIN_CONTENT` in agent_menu is 24 and the panel floor is 18, so
# two columns need 24 + 2 + 18 = 44 content columns, which is 45 real ones
# once the spare column at the right is given up.
TWO_COLUMN_MIN = 45

# Below this the panel does not open at all. Thirty columns is the narrowest
# window in which a card is still a sentence rather than an ellipsis, and a
# panel that opened into something unreadable would be worse than the line
# saying it cannot.
PANEL_ONLY_MIN = 30

TITLE = "AGENTS"

# The one destructive entry, and the id it answers to. It is the last entry in
# the list, set off by a blank row, and marked `!` rather than by colour --
# with the escapes stripped it is still the only row on the panel wearing a
# mark that is not a selection.
KILL_ALL_LABEL = "Kill All Agents"
KILL_ALL = "kill_all"

# What the two keys do, said in the panel rather than left to be discovered.
# Enter is destructive and the panel is the only place that can say so before
# it is pressed, which is why this row exists at all and why it is the first
# thing given up when the region is short of rows.
#
# Three of them, longest first, and the first that FITS is the one drawn. An
# instruction is the one row on the panel that must not be elided: a card cut
# in the middle still says which agent it is, while "Enter kil...t closes"
# says nothing and looks like a fault.
HINTS = ("Enter kills, Left closes", "Enter kills", "")
HINT = HINTS[0]

# Positions on the one gradient. Every one of these is a place an existing
# event kind already holds: a running agent is `background_agent` (40), a
# finished one is `success` (95), a failed one is `error` (10), a killed one
# is `warning` (60), and killing everything is the red end of the scale.
AGENT_POSITION = 40
DONE_POSITION = 95
KILLED_POSITION = 60
FAILED_POSITION = 10
DANGER_POSITION = 10

# The word each status is drawn as. Short because the column is narrow, and a
# word rather than a glyph because the state has to survive the escapes being
# stripped.
_STATE_WORDS = {
    Status.CREATED: "starting",
    Status.STARTING: "starting",
    Status.RUNNING: "running",
    Status.WAITING: "waiting",
    Status.COMPLETED: "done",
    Status.KILLED: "killed",
    Status.FAILED: "failed",
}

_STATE_POSITIONS = {
    Status.COMPLETED: DONE_POSITION,
    Status.KILLED: KILLED_POSITION,
    Status.FAILED: FAILED_POSITION,
}

# Every spelling of the four keys the panel takes while it has focus, as both
# the raw sequence a POSIX terminal sends and the name the Windows console
# reader hands back for a key with no character of its own. Both, because
# `read_key(raw=True)` returns the sequence on one platform and the name on
# the other, and a panel that only knew one of them would be dead on the
# other.
#
# `j` and `k` are deliberately absent. They are up and down on the menus,
# where every key is a command; here they are letters in a task the user is
# still typing.
_PANEL_KEYS = {
    "up": "up", "\x1b[A": "up", "\x1bOA": "up",
    "down": "down", "\x1b[B": "down", "\x1bOB": "down",
    "left": "left", "\x1b[D": "left", "\x1bOD": "left",
    "right": "right", "\x1b[C": "right", "\x1bOC": "right",
    "enter": "enter", "\r": "enter", "\n": "enter", "\r\n": "enter",
    "esc": "esc", "\x1b": "esc",
}


def _menu():
    """agent_menu, imported at call time.

    The import direction is agent_menu -> agent_panel: the prompt box draws
    the panel beside itself and reaches for this module inside the call, the
    same way it reaches for agent_credentials, so a panel that cannot be built
    never stops a session opening. Importing agent_menu back at module scope
    from here would close that into a cycle, so the two formatters this module
    borrows -- the compact token count and the middle-eliding truncator -- are
    fetched when they are used rather than when this module loads.

    They are borrowed rather than reimplemented on purpose. A second token
    formatter would drift from the corner meter's, and two readouts of the
    same number in two shapes is worse than either of them.
    """
    import agent_menu
    return agent_menu


def _short_count(value):
    """A token figure at the size a narrow column can carry."""
    return _menu()._short_count(value)


def _elide(text, columns):
    """`text`, cut to fit, with the marker the rest of TMT already cuts with.

    Middle-elided rather than tail-cut: the two ends of a card row are the
    agent's number and its state, and those are the two facts a reader is
    scanning the column for. Cutting the tail would take the state off every
    card at once.
    """
    text = str(text)
    if columns <= 0:
        return ""
    if display_width(text) <= columns:
        return text
    return _menu()._shorten_middle(text, columns)


def _row(text, columns, stream, position=None, dim=False):
    """One panel row: fitted first, painted second.

    That order is not a preference. Painting first and fitting afterwards cuts
    through an escape sequence and leaves half of one on the row, which the
    terminal then swallows along with whatever followed it.
    """
    text = _elide(text, columns)
    if not _supports_color(stream):
        return text
    if dim:
        return DIM + text + RESET
    if position is None:
        return text
    return _color(text, position, stream)


def panel_width(content_width):
    """How many columns the panel takes of a content width this wide."""
    share = int(round(max(0, int(content_width)) * PANEL_SHARE))
    return max(PANEL_MIN, min(PANEL_MAX, share))


def can_open(columns):
    """"" when the panel may open in a terminal this wide, else the reason.

    A sentence rather than a False, because the only thing that can act on it
    is a person: it names the width they have and the width they need, which
    is the whole of what they can do about it.
    """
    columns = int(columns)
    if columns >= PANEL_ONLY_MIN:
        return ""
    return ("The terminal is %d columns wide and the agents panel needs %d. "
            "Widen the window, or use /agents." % (columns, PANEL_ONLY_MIN))


def layout(columns):
    """(mode, left columns, panel columns) for a terminal this wide.

    Three modes, and the two thresholds between them are the whole of the
    narrow-terminal behaviour:

    "two_column"  -- 45 columns and up. The conversation on the left, the
                     panel on the right, as designed.
    "panel_only"  -- 30 to 44. The panel takes the whole width of the live
                     region and the prompt box is not drawn while it is open.
                     Nothing is lost: the box is not accepting input at that
                     moment, and a box that looked ready for input while the
                     panel had focus would be a lie about what the program is
                     doing. That is the rule `running_lines` already follows.
    "refused"     -- under 30. `can_open` says so and the panel stays shut.
    """
    columns = int(columns)
    if columns < PANEL_ONLY_MIN:
        return ("refused", 0, 0)
    content = max(1, columns - 1)
    if columns < TWO_COLUMN_MIN:
        return ("panel_only", 0, content)
    width = panel_width(content)
    return ("two_column", content - GUTTER - width, width)


def counter_text(count):
    """The agent counter for the main screen, or "" when there are none.

    Nothing to report means nothing drawn, exactly as the corner meter does
    it: a row reading "0 agents" is a readout of an absence, and the absence
    is already on screen as the panel not being there.
    """
    count = max(0, int(count))
    if not count:
        return ""
    return "%d agent%s" % (count, "" if count == 1 else "s")


# The per-agent bars that sit under the main one, and the whole of why they
# look different.
#
# The colour gradient means "the main agent is working, and this is how far
# along it is". Painting a worker's bar with it would say the same thing about
# a different thing on the row below, and at a glance the eye would read five
# gradient bars as one process reported five times. So the agent bars are the
# NEUTRAL ramp -- black through grey to white -- and the gradient stays the
# main agent's alone. That is the rule "one gradient, one neutral" spent on
# its most useful distinction rather than a new palette invented for a new
# feature: the agents get no colour of their own, they get the absence of one.
#
# It also survives having the escapes stripped, like everything else here: the
# row says `#1` and a state word whatever the terminal can draw.
NEUTRAL_STEPS = ((90, 90, 90), (140, 140, 140), (190, 190, 190), (235, 235, 235))

# The width of an agent's bar. Narrower than the main one on purpose -- it is
# subordinate to it, and the row has four other figures to carry.
AGENT_BAR_WIDTH = 8


def _neutral(index, span):
    """A step along the black-grey-white ramp, as an (r, g, b)."""
    if span <= 0:
        return NEUTRAL_STEPS[-1]
    position = min(1.0, max(0.0, index / float(span)))
    slot = int(position * (len(NEUTRAL_STEPS) - 1))
    return NEUTRAL_STEPS[slot]


def neutral_bar(progress, stream, width=AGENT_BAR_WIDTH, plain=None):
    """A progress bar in the neutral ramp rather than in the gradient.

    The same shape as `agent_ui.cycle_bar` and deliberately not the same
    colour. It does not ride the animation cycle either: a worker's bar that
    shimmered would pull the eye away from the main one, which is the row that
    is actually about what the user asked for.
    """
    plain = plain_output(stream) if plain is None else plain
    full, empty = ("#", "-") if plain else ("█", "░")
    width = max(1, int(width))
    progress = min(100, max(0, int(progress)))
    filled = round(width * progress / 100.0)
    if not _supports_color(stream):
        return full * filled + empty * (width - filled)
    cells = []
    for index in range(width):
        if index < filled:
            red, green, blue = _neutral(index, max(1, width - 1))
            cells.append("\033[38;2;%d;%d;%dm%s" % (red, green, blue, full))
        else:
            cells.append(DIM + empty)
    cells.append(RESET)
    return "".join(cells)


def agent_progress(record):
    """How full an agent's bar is, 0-100.

    The step budget it has spent, NOT a guess at how close it is to finishing.
    Nothing can know the second, and a bar that implied it would be inventing
    the one figure nobody has. A terminal agent is drawn full whatever its
    step count, because it is over -- which is the one moment completion is
    actually known.
    """
    if getattr(record, "is_terminal", None) and record.is_terminal():
        return 100
    budget = int(getattr(record, "max_steps", 0) or 0)
    if budget <= 0:
        return 0
    spent = int(getattr(record, "steps", 0) or 0)
    return min(99, max(0, int(round(100.0 * spent / budget))))


def _elapsed_text(record, now=None):
    """How long an agent has been working, as a compact string.

    `now` is read here when the caller did not supply one. It has to be: the
    records measure against `time.monotonic`, and passing None straight
    through made every row report 0s -- a clock that never moved, on the one
    figure whose whole job is to move.
    """
    try:
        moment = time.monotonic() if now is None else now
        seconds = int(max(0, round(record.elapsed(moment))))
    except Exception:
        return ""
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm%02ds" % (seconds // 60, seconds % 60)
    return "%dh%02dm" % (seconds // 3600, (seconds % 3600) // 60)


def agent_status_row(record, columns, stream=None, now=None):
    """One agent's row for under the main progress bar.

    `bar #1  +12 -3  ~1.2k out  8s  running`. Everything on it is measured:
    the lines come off the same `action_event` detail the session's own meter
    reads, the tokens are the provider's figure where it gave one and marked
    `~` where it did not, and the elapsed time stops when the agent does.

    The row gives up its parts from the right as the terminal narrows, so a
    narrow window loses the state word before the tokens and the tokens before
    the identity. What it never loses is which agent it is: a row that cannot
    say that is not worth drawing.
    """
    stream = sys.stdout if stream is None else stream
    bar = neutral_bar(agent_progress(record), stream)
    name = "#%s" % getattr(record, "id", "?")
    lines = "+%d -%d" % (int(getattr(record, "lines_added", 0) or 0),
                         int(getattr(record, "lines_removed", 0) or 0))
    out = int(getattr(record, "tokens_out", 0) or 0)
    mark = "" if getattr(record, "tokens_out_exact", False) else "~"
    tokens = ("%s%s out" % (mark, _short_count(out))) if out else ""
    elapsed = _elapsed_text(record, now)
    state = _state_word(record)
    # Widest first, then each shorter form drops the least useful thing left.
    for parts in ((name, lines, tokens, elapsed, state),
                  (name, lines, tokens, elapsed),
                  (name, lines, elapsed),
                  (name, lines),
                  (name,)):
        text = "  ".join(part for part in parts if part)
        # The bar is measured as drawn rather than as painted: it carries
        # escapes, and counting those would shrink the row for characters the
        # terminal never puts on screen.
        if display_width(text) + AGENT_BAR_WIDTH + 2 <= max(1, int(columns) - 1):
            return bar + " " + fit_to_width(text, max(1, int(columns) - AGENT_BAR_WIDTH - 2))
    return bar


def agent_status_rows(records, columns, stream=None, now=None):
    """A row per agent, for the strip under the main progress bar.

    Nothing at all when there are no agents, so a session that never
    delegates draws exactly the screen it drew before any of this existed.
    """
    return [agent_status_row(record, columns, stream, now)
            for record in (records or ())]


def _state_word(record):
    return _STATE_WORDS.get(getattr(record, "status", ""), "running")


def _state_position(record):
    return _STATE_POSITIONS.get(getattr(record, "status", ""), AGENT_POSITION)


def _token_text(record):
    """`42k T +4120`: the running total, then the request in flight.

    Two figures rather than one because the total has to stay a total. An
    agent on its fourth request has three earlier replies inside `tokens_out`,
    so the reply arriving now cannot be recovered from it by subtraction.

    A leading `~` on either half means the figure was estimated rather than
    reported, which is the rule everywhere in TMT: a guessed number says it is
    guessed. `tokens_in_exact` and `tokens_out_exact` are that signal, and
    they start False because nothing has been reported yet -- an unmarked zero
    would be a claim that the provider said zero.

    When nothing is in flight the state word takes the slot instead, so the
    row that is never dropped always carries the agent's state.
    """
    counted = max(0, int(record.total_tokens()))
    pending = max(0, int(getattr(record, "tokens_out_pending", 0) or 0))
    if not counted and not pending:
        # Nothing has been reported and nothing is in flight. The state word
        # stands alone rather than beside a `~0 T`, which is a readout of an
        # absence: the same rule that leaves the corner meter blank instead of
        # drawing a row of zeroes.
        return _state_word(record)
    exact = (bool(getattr(record, "tokens_in_exact", False))
             and bool(getattr(record, "tokens_out_exact", False)))
    total = ("" if exact else "~") + _short_count(counted)
    if pending and not record.is_terminal():
        mark = "" if getattr(record, "tokens_out_exact", False) else "~"
        # The total is abbreviated and this one is not, which is a deliberate
        # difference rather than an oversight. The total is large, historical
        # and only read for its order of magnitude, so `42k` says everything
        # it needs to. The figure beside it is the reply arriving right now:
        # it is the only number on the card that moves, it is bounded by the
        # effort setting's max_tokens so it never grows wide, and rounding a
        # live counter to `+4k` would hold it still for a thousand tokens at
        # a time -- which reads as a worker that has stopped.
        return "%s T +%s%d" % (total, mark, pending)
    return "%s T %s" % (total, _state_word(record))


def card_lines(record, width, selected=False, now=None, stream=None,
               activity=True):
    """One agent's card, painted, every row at most `width` columns.

    Two rows. The first carries the number, the token figures and the state,
    and is never dropped. The second carries the activity label and is the
    first thing given up when the region is short of rows -- an agent whose
    label has gone is still identifiably running, while an agent whose numbers
    have gone is a row of prose.

    `now` is accepted so a caller with a clock has somewhere to put it, and
    deliberately reaches nothing: no card reports elapsed time. The live
    region repaints when its composed frame CHANGES and at no other time --
    that is the flicker fix, and it is why the relay stopped repainting on a
    timer -- so a duration drawn here would either not be repainted, and be a
    stale number presented as a live one, or force back the per-tick repaint
    that was removed. A wrong clock is worse than no clock.
    """
    stream = sys.stdout if stream is None else stream
    width = max(1, int(width))
    marker = ">" if selected else " "
    head = "%s #%s: %s" % (marker, record.id, _token_text(record))
    rows = [_row(head, width, stream, position=_state_position(record))]
    if activity:
        label = str(getattr(record, "activity", "") or "").strip()
        if label:
            # Indented under the number and dim: it is the detail on the card,
            # and the one neutral is where detail that must recede lives.
            rows.append(_row("   " + label, width, stream, dim=True))
    return rows


def _fitting_hint(width):
    """The longest form of the hint that fits, or "" when none does."""
    for text in HINTS:
        if display_width(text) <= width:
            return text
    return ""


def entry_count(records):
    """Selectable entries: one per agent, plus the Kill All Agents row."""
    return len(list(records)) + 1


def panel_rows(records, width, height=None, selected=0, now=None, stream=None):
    """The panel column, painted, every row at most `width` columns.

    A header naming the panel and counting the agents, one rule under it, a
    card per agent, a blank row, the Kill All Agents entry and a hint saying
    what Enter and Left do.

    One rule, not two. The rule under the header marks that boundary; the
    boundary above the destructive entry is marked by the blank row, because
    two rules a blank line apart are not a boundary, they are a box with
    nothing in it.

    `height` is how many rows the region can spare. What is given up, in
    order: the hint, which is teaching and can be learned once; then every
    activity label, which is the rule the brief fixes -- the activity line
    goes before the token line; then cards themselves. The window follows the
    selection, so the row the user is on is never the row that was trimmed.

    Trimming is never silent, and it costs no row to say so: the header counts
    every agent the manager can see, not the ones that fit, so a header
    reading AGENTS 5 above one card has already said that four are not drawn.
    A "+4 more" row would have cost exactly the row that would have shown
    another card, which is a worse trade than the count that is there anyway.
    """
    stream = sys.stdout if stream is None else stream
    width = max(1, int(width))
    records = list(records)
    total = len(records)
    selected = max(0, min(entry_count(records) - 1, int(selected)))

    def build(shown, first, activity, hint):
        rows = [_row("%s %d" % (TITLE, total), width, stream,
                     position=AGENT_POSITION),
                ("-" if plain_output(stream) else "─") * width]
        for offset, record in enumerate(shown):
            rows.extend(card_lines(record, width,
                                   selected=(first + offset) == selected,
                                   now=now, stream=stream, activity=activity))
        if records:
            rows.append("")
        killing = selected >= total
        rows.append(_row(("%s %s" % (">" if killing else "!", KILL_ALL_LABEL)),
                         width, stream, position=DANGER_POSITION))
        if hint:
            said = _fitting_hint(width)
            if said:
                rows.append(_row(said, width, stream, dim=True))
        return rows

    for activity, hint in ((True, True), (True, False), (False, False)):
        rows = build(records, 0, activity, hint)
        if height is None or len(rows) <= int(height):
            return rows

    # Still too tall: show fewer cards, keeping the selected one in view.
    # `first` slides only as far as it must, so the list scrolls rather than
    # jumping to centre on the selection -- and a selection that had been
    # trimmed away would leave the user driving something they cannot see.
    count = total
    while count > 0:
        count -= 1
        first = 0
        if selected < total:
            first = max(0, min(selected - count + 1, total - count))
        rows = build(records[first:first + count], first, False, False)
        if len(rows) <= int(height):
            return rows
    return build([], 0, False, False)[:max(1, int(height))]


# ---------------------------------------------------------------------------
# The PLAN column.
#
# The same column as the panel above, drawn from the same primitives, obeying
# the same three rules. It is what the right-hand column shows whenever the
# agents panel is shut, which is nearly always -- the panel is opened by a
# gesture and closed again, while a plan stands for the length of a task.
#
# Where the two want the column at once, the plan takes the TOP of it and the
# panel the rest. That is the layout the plan was asked for and it is also the
# right one: the plan is the shape of the whole task and the panel is one
# thing happening inside it.

PLAN_TITLE = "PLAN"

# Positions on the one gradient, and every one of them is a position an
# existing event kind already holds. Completed is `success` (95, green), the
# active step is `background_agent` (40, orange), an outstanding step is
# `error` (10, red) and a blocked one is `warning` (60). The three the brief
# asks for -- green, orange, red -- are the three ends of the scale TMT
# already paints with, so this adds no colour to the program.
PLAN_DONE_POSITION = 95
PLAN_ACTIVE_POSITION = 40
PLAN_PENDING_POSITION = 10
PLAN_BLOCKED_POSITION = 60

_PLAN_POSITIONS = {
    "completed": PLAN_DONE_POSITION,
    "in_progress": PLAN_ACTIVE_POSITION,
    "pending": PLAN_PENDING_POSITION,
    "blocked": PLAN_BLOCKED_POSITION,
}

# Colour is never the message: every status also has a mark, and the marks are
# distinct with the escapes stripped and on a terminal with no colour at all.
_PLAN_MARKS = {"completed": "✓", "in_progress": "●", "pending": "○",
               "blocked": "!"}
_PLAN_ASCII_MARKS = {"completed": "+", "in_progress": ">", "pending": "-",
                     "blocked": "!"}

# The most rows the plan takes when it is sharing the column with the agents
# panel. A plan that filled the column would leave the panel a row to say
# everything in, and the panel is the one with focus at that moment.
PLAN_SHARED_MAX = 8

# Below this many rows the plan block is not drawn at all when sharing. Two
# steps and a header is the least that is worth the room; less than that is a
# heading over an ellipsis.
PLAN_SHARED_MIN = 4


def plan_marks(stream=None):
    """The status marks this stream can actually show.

    The marks are asked about directly as well as through `plain_output`.
    That decision is made on one fixed sample of decoration, and a terminal
    that can draw a box rule but not a filled circle would otherwise put a
    replacement character where the active step's mark should be -- on the one
    row the user is looking for.
    """
    if plain_output(stream) or not encodable(stream, "".join(_PLAN_MARKS.values())):
        return _PLAN_ASCII_MARKS
    return _PLAN_MARKS


def plan_step_text(step, marks):
    """One step as plain text: its label, its mark, and its title."""
    return "%s %s %s" % (step.id, marks.get(step.status, "?"), step.title)


def _plan_window(steps, room, active_index):
    """(first, shown) -- the steps that fit, keeping the active one in view.

    The window follows the step being worked on for the reason the input
    field's window follows the caret: the one row that must always be on
    screen is the one the work is happening on. A plan scrolled to its top
    while the agent works on S9 is a plan about somebody else's task.
    """
    total = len(steps)
    room = max(1, int(room))
    if total <= room:
        return 0, list(steps)
    first = 0
    if active_index >= room:
        first = active_index - room + 1
    first = min(first, total - room)
    return first, list(steps[first:first + room])


def plan_rows(plan, width, height=None, stream=None):
    """The plan column, painted, every row at most `width` columns.

    A header naming it and counting what is done, one rule under it, and a row
    per step. The same shape as the agents panel, because it is the same
    column and two different shapes in one column would read as two programs.

    `height` is how many rows the column can spare. Steps are given up before
    the header is, and the header counts EVERY step rather than the ones that
    fit -- so `PLAN 2/9` above four rows has already said that five are not
    drawn. That is the same bargain `panel_rows` strikes, and for the same
    reason: a "+5 more" row costs exactly the row that would have shown
    another step.

    An empty plan draws nothing at all. There is no plan to show, and a
    heading over an empty column would be a promise the task never made.
    """
    stream = sys.stdout if stream is None else stream
    width = max(1, int(width))
    steps = list(getattr(plan, "steps", ()) or ())
    if not steps:
        return []
    marks = plan_marks(stream)
    done = sum(1 for step in steps if step.status == "completed")
    header = _row("%s %d/%d" % (PLAN_TITLE, done, len(steps)), width, stream,
                  position=PLAN_DONE_POSITION if done == len(steps)
                  else PLAN_ACTIVE_POSITION)
    rule = ("-" if plain_output(stream) else "─") * width
    room = None if height is None else max(1, int(height) - 2)
    active = next((index for index, step in enumerate(steps)
                   if step.status == "in_progress"), 0)
    shown = steps if room is None else _plan_window(steps, room, active)[1]
    rows = [header, rule]
    rows.extend(_row(plan_step_text(step, marks), width, stream,
                     position=_PLAN_POSITIONS.get(step.status,
                                                  PLAN_PENDING_POSITION))
                for step in shown)
    return rows


def plan_report(plan):
    """What `/plan` prints: the plan as permanent text.

    The unambiguous alternate to the column, and the only way in on a terminal
    too narrow to hold two columns. It goes to the permanent surface like any
    other command result, so it is a record rather than a frame, and it says
    the same things the column does in the same words.
    """
    steps = list(getattr(plan, "steps", ()) or ())
    if not steps:
        return ("There is no plan for this task. TMT makes one for "
                "substantial work; a question that is answered by reading "
                "does not need one.")
    marks = _PLAN_ASCII_MARKS
    done = sum(1 for step in steps if step.status == "completed")
    rows = ["%s %d/%d" % (PLAN_TITLE, done, len(steps))]
    rows.extend("%s  (%s)" % (plan_step_text(step, marks), step.status)
                for step in steps)
    if done == len(steps):
        rows.append("Every step is complete.")
    else:
        rows.append("TMT cannot finish this task until every step is complete.")
    return "\n".join(rows)


def _pad(text, columns):
    """Pad painted text out to `columns`, measuring past the escapes.

    A row that has already been painted is mostly characters that occupy no
    columns at all, so padding it by `display_width` puts the panel several
    columns too far right and the whole region wraps.
    """
    return text + " " * max(0, columns - visible_width(text))


def compose(left_rows, right_rows, width, gutter=GUTTER):
    """One region row per row of the taller column.

    The left column is flush with the BOTTOM of the block and the right column
    flush with the TOP, and neither is a matter of taste. The prompt box is
    the last thing in the left column and it has to stay at the foot of the
    window, so the blank rows a short left column needs go above it. The panel
    is read downward from its header, so the blank rows a short panel needs go
    below it.

    A left row wider than the column it was built for would push the panel
    right and wrap the region, so one is stripped of its colour and cut to
    fit. Losing the paint on an overflowing row is a visible degradation; a
    region that wraps is a frame that marches down the screen on every
    repaint, and that one is not recoverable.
    """
    width = max(1, int(width))
    gutter = max(0, int(gutter))
    left_rows = list(left_rows or [])
    right_rows = list(right_rows or [])
    panel = max([visible_width(row) for row in right_rows] or [0])
    left_width = max(0, width - gutter - panel)
    height = max(len(left_rows), len(right_rows))
    left_rows = [""] * (height - len(left_rows)) + left_rows
    right_rows = right_rows + [""] * (height - len(right_rows))
    rows = []
    for left, right in zip(left_rows, right_rows):
        if visible_width(left) > left_width:
            left = fit_to_width(strip_ansi(left), left_width)
        rows.append(_pad(left, left_width) + " " * gutter + right)
    return rows


def panel_key(raw):
    """The panel's name for a keystroke, or "" for one it does not take.

    Only the four keys the panel captures while it has focus, plus Esc.
    Everything else comes back "" and is handed on to the field, so a
    character typed while the panel is open is still typed.
    """
    if not isinstance(raw, str):
        return ""
    return _PANEL_KEYS.get(raw, "")


def agents_report(manager, now=None):
    """What `/agents` prints: the visible agents, as permanent text.

    The unambiguous alternate to the arrow gesture, and the only way in on a
    terminal too narrow for the panel to open into. It goes to the permanent
    surface like any other command result, so it is a record rather than a
    frame, and it says the same things the cards do in the same words.
    """
    if manager is None:
        return "Background agents are unavailable in this session."
    try:
        records = tuple(manager.visible_agents(now))
    except Exception:
        return "Background agents are unavailable in this session."
    if not records:
        return "No background agents are running."
    lines = ["%s %d" % (TITLE, len(records))]
    for record in records:
        lines.append("#%s: %s" % (record.id, _token_text(record)))
        label = str(getattr(record, "activity", "") or "").strip()
        if label:
            lines.append("   " + label)
        task = str(getattr(record, "task", "") or "").strip()
        if task:
            lines.append("   " + _elide(task, 68))
    return "\n".join(lines)


class PanelState:
    """The right-hand column: whether the panel is open, and what is drawn.

    Held by the prompt box for the life of a session, so the selection and the
    open state survive a turn: the panel is a view onto the manager, and the
    manager is the only place an agent's state lives. Nothing here caches a
    record.

    It owns the column rather than only the agents panel, because there is one
    column and two things that want it. `open` still means what it always
    meant -- the agents panel has focus -- and the plan is drawn whenever
    there is one, above the panel when both are showing and alone when the
    panel is shut. Keeping that decision here is what lets both callers stay
    exactly as they were: the prompt box and the live relay each ask for a
    frame and know nothing about what ended up in it.
    """

    def __init__(self, manager=None, stream=None, plan=None):
        self.manager = manager
        self.stream = sys.stdout if stream is None else stream
        self.open = False
        self.selected = 0
        # The plan to draw, as an object or as a callable returning one. A
        # callable because the session owns the plan and empties it between
        # turns, and a panel holding the object it was built with would go on
        # drawing a task that is over. Nothing here caches it, for the same
        # reason nothing here caches an agent record.
        self.plan = plan
        # The reason the panel could not open, shown once and cleared by the
        # next thing that happens. It is a live message about a live gesture,
        # so it belongs on the temporary surface with the box that carries it.
        self.message = ""

    def records(self, now=None):
        """The agents a panel should draw right now.

        `visible_agents` filters the five-second retention on read, so a
        finished agent leaves the panel and the counter together and without
        a timer anywhere. A manager that raises is treated as no agents:
        decoration is never allowed to end a turn.
        """
        if self.manager is None:
            return ()
        try:
            return tuple(self.manager.visible_agents(now))
        except Exception:
            return ()

    def count(self, now=None):
        return len(self.records(now))

    def counter(self, now=None):
        return counter_text(self.count(now))

    def open_panel(self, columns):
        """Open it, or record why it cannot. Returns whether it opened.

        A column with no register behind it does not open. There is nothing
        for the keys to drive and the panel would be an empty heading -- and
        the column may well be drawing a plan already, which the gesture would
        replace with nothing.
        """
        if self.manager is None:
            self.open = False
            self.message = "Background agents are unavailable in this session."
            return False
        refusal = can_open(columns)
        if refusal:
            self.open = False
            self.message = refusal
            return False
        self.message = ""
        self.open = True
        self.selected = 0
        return True

    def close(self):
        self.open = False
        self.message = ""

    def handle(self, intent, now=None):
        """One panel keystroke. "" for a key the panel does not take.

        Returns "moved", "closed", "killed", "killed_all" or "" -- the caller
        only has to know whether the key was taken, and the words say what
        happened for anything that wants to report it.
        """
        if not self.open or not intent:
            return ""
        entries = entry_count(self.records(now))
        if intent == "up":
            self.selected = max(0, self.selected - 1)
            return "moved"
        if intent == "down":
            self.selected = min(entries - 1, self.selected + 1)
            return "moved"
        if intent in ("left", "esc"):
            self.close()
            return "closed"
        if intent == "enter":
            return self.activate(now)
        return ""

    def activate(self, now=None):
        """Act on the selected entry: kill that agent, or kill them all.

        Killing is what a panel of running agents is for, and it is the only
        thing this one does. The hint row says so before Enter is pressed,
        which is the whole reason that row exists.
        """
        if self.manager is None:
            return ""
        records = self.records(now)
        if self.selected >= len(records):
            self.manager.kill_all()
            return "killed_all"
        self.manager.kill(records[self.selected].id)
        return "killed"

    def plan_now(self):
        """The plan to draw right now, or None when there is nothing to draw.

        An empty plan is None here: `Plan.__bool__` is whether it has steps,
        and a heading over an empty column would promise a plan the task never
        made. A provider that raises is treated as no plan, for the reason
        `records` treats a broken register as no agents -- decoration is never
        allowed to end a turn.
        """
        try:
            plan = self.plan() if callable(self.plan) else self.plan
        except Exception:
            return None
        return plan or None

    def frame(self, columns, rows=None):
        """(left columns, join) for a region this wide, or None for no column.

        `join(left_rows)` turns the caller's own rows into the composed
        region. The caller therefore needs to know only one number -- how wide
        its column is -- and every rule about widths, gutters, padding and
        which column is flush with which edge stays in this module.

        `left columns` is 0 in panel-only mode, which is the caller's signal
        to draw no box at all.

        A window narrowed past the floor while the panel is open closes it and
        leaves the reason behind, rather than drawing something unreadable --
        and the plan, if there is one, keeps the column.
        """
        columns = int(columns)
        plan = self.plan_now()
        if not self.open:
            return self._plan_frame(columns, rows, plan)
        mode, left, width = layout(columns)
        if mode == "refused":
            self.open = False
            self.message = can_open(columns)
            return self._plan_frame(columns, rows, plan)
        # Two rows kept back for the status row under the region and the spare
        # row a region never draws on.
        height = None if rows is None else max(1, int(rows) - 2)
        body = self._shared_rows(plan, width, height, mode)
        content = max(1, columns - 1)

        def join(left_rows):
            return compose(left_rows, body, content)

        return (left, join)

    def _shared_rows(self, plan, width, height, mode):
        """The column when the agents panel has focus: plan on top, panel below.

        In panel-only mode the panel takes the whole column and the plan is
        not drawn. The panel is the thing with focus there, the window is
        already too narrow for two columns, and a plan squeezed in above it
        would cost the cards the rows that make them readable. `/plan` says
        the same thing on any width.
        """
        if mode != "two_column" or not plan:
            return panel_rows(self.records(), width, height=height,
                              selected=self.selected, stream=self.stream)
        room = None if height is None else max(0, int(height) - PLAN_SHARED_MIN)
        block = plan_rows(plan, width,
                          height=PLAN_SHARED_MAX if room is None
                          else min(PLAN_SHARED_MAX, room),
                          stream=self.stream)
        if room is not None and room < PLAN_SHARED_MIN:
            # Not enough rows for both. The panel wins: it was opened by a
            # deliberate gesture a moment ago and it is what the keys are
            # driving.
            block = []
        left = None if height is None else max(1, int(height) - len(block) - 1)
        panel = panel_rows(self.records(), width, height=left,
                           selected=self.selected, stream=self.stream)
        return block + ([""] if block else []) + panel

    def _plan_frame(self, columns, rows, plan):
        """(left columns, join) for the plan alone, or None.

        Refused below the two-column threshold rather than taking the whole
        region the way an open panel does. An open panel has focus and is not
        accepting input anywhere else; a plan is something to glance at while
        typing, and one that swallowed the prompt box would take away the
        thing the user was using. `/plan` is the way in at that width, exactly
        as `/agents` is.
        """
        if not plan:
            return None
        mode, left, width = layout(columns)
        if mode != "two_column":
            return None
        height = None if rows is None else max(1, int(rows) - 2)
        body = plan_rows(plan, width, height=height, stream=self.stream)
        if not body:
            return None
        content = max(1, columns - 1)

        def join(left_rows):
            return compose(left_rows, body, content)

        return (left, join)
