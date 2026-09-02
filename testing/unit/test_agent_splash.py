"""Tests for the launch screen: the first thing TMT draws, every time.

Three things are being protected here, and only the first of them is about
what the screen looks like.

The first is that the frame fits the window it was given. A splash is the one
surface in TMT that is built to fill the viewport exactly, so every one of its
rows is one column away from the auto-wrap that costs a screen line the
repaint arithmetic does not know about, and its height is one row away from
scrolling its own top away. Both are swept rather than sampled: every width
from a terminal too narrow to hold the word "Enter" up to a wide one, and
every state the screen can be in. Every assertion about what is on screen is
made with `agent_ui.strip_ansi` first and `agent_ui.display_width` after,
because colour is confirmation here and never the message, and because `len()`
is not a width.

The second is that nothing on this screen can stop TMT launching. It is the
first code the process runs, so a failure here is not a degraded feature, it
is a program that does not start. `run_splash` returning "start" without
drawing anything to a stream that is not a terminal is the single most
important assertion in this file: it is what keeps every piped run, every
script and the whole of this suite behaving exactly as they did before the
screen existed. Everything after it -- an updater that raises, an updater that
cannot be imported, a status nobody has heard of -- is the same rule asked a
different way.

The third is that the screen never says something that is not true. The
`SKIPPED` state exists because the setting was off and nothing was checked,
and the test that it never contains the word "search" is the whole point of
its existing rather than being folded into `CURRENT`.

Nothing here reads a key, sleeps, opens a socket or starts a process. Every
function in `agent_splash` that involves time takes a `moment=` or a `clock=`,
and those are what is driven.
"""

import ast
import io
import os
import re
import sys
from pathlib import Path

import agent_splash

from agent_ui import display_width, strip_ansi


def menu():
    """agent_menu, imported at call time.

    The same reason `test_agent_menu` and `agent_splash.base_logo` both do it:
    a module-level import would make a missing or broken `agent_menu` an error
    in the runner's collection rather than a failure of the two tests that
    actually depend on it -- and the whole point of `_FALLBACK_LOGO` is that
    the launch screen survives exactly that.
    """
    import agent_menu
    return agent_menu


# --- the fake terminals ------------------------------------------------------
#
# A plain io.StringIO is NOT "plain output" in this repository: it has no
# `encoding` attribute, so `encodable` says yes to every decorative glyph, and
# it has no `isatty`, so it gets no colour. The three classes below are the
# three terminals that actually exist -- one that can carry everything, one
# that can carry escapes but has been told not to use colour, and a Windows
# console through a pipe, which is where the ASCII fallbacks are needed.

class Tty(io.StringIO):
    """A stream that claims to be a terminal and can encode anything."""

    encoding = "utf-8"

    def isatty(self):
        return True


class Cp1252(io.StringIO):
    """A Windows console through a pipe: no colour, and no block glyph.

    `cp1252` is the encoding a plain Windows console really reports and it
    cannot carry a single one of TMT's decorative characters, so this is the
    stream the degradation rules exist for.
    """

    encoding = "cp1252"

    def isatty(self):
        return False


class NoColour:
    """NO_COLOR set for the length of a test, and put back afterwards.

    `_supports_color` is `isatty() and not os.environ["NO_COLOR"]`, so a fake
    tty on its own still gets colour -- the weight-pulse fallback cannot be
    reached without setting the variable, and a test that forgot to put it
    back would silently turn the colour off for every test that ran after it.
    """

    def __init__(self):
        self.previous = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"

    def close(self):
        os.environ.pop("NO_COLOR", None)
        if self.previous is not None:
            os.environ["NO_COLOR"] = self.previous


def visible(text):
    """The row as a reader sees it, with every escape sequence removed."""
    return strip_ansi(text)


def width_of(text):
    """The columns a painted row really occupies.

    Measured, never counted: an escape sequence is made of characters that
    occupy no columns at all, and the block glyph and the ellipsis are not the
    only things here that a character count gets wrong.
    """
    return display_width(strip_ansi(text))


def frame(state=None, stream=None, size=(80, 24), moment=0.4):
    """One composed launch screen, as the terminal would show it."""
    return agent_splash.render_splash_frame(
        state, stream=Tty() if stream is None else stream, size=size,
        moment=moment)


# --- the injected updater ----------------------------------------------------
#
# The real `agent_update` is never imported by this file. It is a separate
# module with its own tests, and `state_for_update` reads its status constants
# off whichever module the caller handed it precisely so that a stub works --
# reading the real module's constants to interpret a different module's result
# maps every status to FAILED and reports a clean check as a failure.

class Result:
    """What `check_and_update` hands back, reduced to what the splash reads.

    Three positional arguments, because `agent_splash._failure` builds one by
    hand as `UpdateResult(ERROR, headline, detail)` for the paths the updater
    never reached. A two-argument stand-in makes that raise, `_failure`
    returns None, and the stage reports the user's own Ctrl-C for what was
    really a failed check.
    """

    def __init__(self, status, headline="", detail=""):
        self.status = status
        self.headline = headline
        self.detail = detail


class Updater:
    """A stand-in for `agent_update`, which records what was asked of it.

    Its status constants are deliberately NOT the strings the real module
    uses. A stub whose values happened to match would let a mapping that read
    the real module's constants pass, which is the bug this shape exists to
    catch.
    """

    CURRENT = "u-current"
    UPDATED = "u-updated"
    BLOCKED_DIRTY = "u-dirty"
    BLOCKED_DIVERGED = "u-diverged"
    NO_UPSTREAM = "u-no-upstream"
    NOT_A_REPO = "u-not-a-repo"
    DISABLED = "u-disabled"
    ERROR = "u-error"
    UpdateResult = Result

    def __init__(self, status=None, headline="what the updater said",
                 raises=None):
        self.status = self.CURRENT if status is None else status
        self.headline = headline
        self.raises = raises
        self.checks = 0
        self.roots = []
        self.restarts = 0

    def check_and_update(self, root=None):
        self.checks += 1
        self.roots.append(root)
        if self.raises is not None:
            raise self.raises
        return Result(self.status, self.headline)

    def restart(self):
        self.restarts += 1
        return "restarted"


def stage(updater, auto_update=True, restart=None, root=None, state=None):
    """`run_update_stage` driven with no terminal, no clock and no reader.

    `region=None` is what makes `_dwell` return at once -- there is nothing to
    look at and nothing to wait for -- so the whole sequence runs in the time
    the stub takes to answer.
    """
    state = agent_splash.SplashState() if state is None else state
    return agent_splash.run_update_stage(
        state, stream=io.StringIO(), region=None, clock=lambda: 0.0,
        key_reader=lambda: "", updater=updater, auto_update=auto_update,
        restart=restart, root=root)


def code_lines(path):
    """The module's source with its comments and its docstrings taken out.

    The prose has to go before the source can be scanned for a banned escape,
    because `agent_splash`'s own module docstring NAMES both of them in order
    to say they must never be used. A grep that could not tell the ban from
    the thing banned would fail on the sentence explaining the rule.
    """
    source = path.read_text(encoding="utf-8")
    prose = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Expr):
            continue
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for number in range(node.lineno, (node.end_lineno or node.lineno) + 1):
            prose.add(number)
    return [(number, line)
            for number, line in enumerate(source.splitlines(), 1)
            if number not in prose and not line.lstrip().startswith("#")]


# --- the wordmark ------------------------------------------------------------

def test_the_fallback_wordmark_is_the_same_letterform_the_menu_draws():
    """`_FALLBACK_LOGO` exists for an install where `agent_menu` cannot be
    imported -- an editable install freezes its module list, so a module in
    the source tree can be invisible to the entry point. It is a COPY, and the
    failure being prevented is the day the two disagree and TMT has two
    wordmarks that are nearly the same, which is worse than having one that is
    too small. This is the anti-drift assertion the module docstring
    promises."""
    assert agent_splash._FALLBACK_LOGO == tuple(menu().LOGO)


def test_base_logo_prefers_the_menus_own_wordmark():
    """`agent_menu.LOGO` is the authority and the copy is only the fallback.
    An implementation that returned the local copy first would never notice
    the menu's wordmark changing under it."""
    assert agent_splash.base_logo() == tuple(menu().LOGO)


def test_the_wordmark_falls_back_when_agent_menu_cannot_be_imported():
    """A launch screen that could not draw because of a frozen module list
    would be a launch screen that stops TMT starting, which is the one thing
    it must never do. `import agent_menu` raising has to leave a wordmark."""
    previous = sys.modules.get("agent_menu")
    sys.modules["agent_menu"] = None        # `import agent_menu` -> ImportError
    try:
        assert agent_splash.base_logo() == agent_splash._FALLBACK_LOGO
    finally:
        if previous is None:
            sys.modules.pop("agent_menu", None)
        else:
            sys.modules["agent_menu"] = previous


def test_the_wordmark_falls_back_when_the_menu_hands_back_nothing():
    """An importable `agent_menu` whose LOGO is empty is the mid-edit case, and
    an empty tuple would reach `max()` over no rows in `logo_for` and raise.
    The falsy check in `base_logo` is what stops that."""

    class Emptied(object):
        LOGO = ()

    previous = sys.modules.get("agent_menu")
    sys.modules["agent_menu"] = Emptied()
    try:
        assert agent_splash.base_logo() == agent_splash._FALLBACK_LOGO
    finally:
        if previous is None:
            sys.modules.pop("agent_menu", None)
        else:
            sys.modules["agent_menu"] = previous


def test_scale_logo_doubles_both_axes_exactly():
    """The large wordmark is the canonical one scaled, not a second one drawn
    by hand. Doubling one axis and not the other would stretch the
    letterforms, which is the failure a test on the row count alone misses."""
    base = agent_splash.base_logo()
    scaled = agent_splash.scale_logo(base, 2)
    assert len(scaled) == len(base) * 2
    assert display_width(scaled[0]) == display_width(base[0]) * 2


def test_scale_logo_at_factor_one_is_the_identity():
    """`logo_for` offers the unscaled block as its middle size by passing the
    canonical rows straight through. A factor of one that changed anything at
    all would make the medium wordmark a different shape from the menu's."""
    base = agent_splash.base_logo()
    assert agent_splash.scale_logo(base, 1) == tuple(base)


def test_scale_logo_refuses_a_factor_below_one():
    """Zero rows and zero columns is not a smaller logo, it is no logo, and a
    caller that computed a factor from a width could reach one. `max(1, ...)`
    is what makes that the unscaled block instead of nothing."""
    base = agent_splash.base_logo()
    assert agent_splash.scale_logo(base, 0) == tuple(base)
    assert agent_splash.scale_logo(base, -3) == tuple(base)


def test_every_row_of_the_scaled_wordmark_is_the_same_width():
    """The centring margin is computed from one row and applied to all of
    them, so a block whose rows were not all the same width would come out
    with ragged left edges that no single test of one row would catch."""
    for factor in (1, 2, 3):
        rows = agent_splash.scale_logo(agent_splash.base_logo(), factor)
        widths = {display_width(row) for row in rows}
        assert len(widths) == 1, (factor, widths)


def test_scaling_preserves_the_shape_exactly():
    """Sampling every Nth cell of every Nth row has to recover the original.
    That is the difference between a scaled letterform and a smeared one, and
    it is the property that lets one wordmark serve three sizes."""
    base = agent_splash.base_logo()
    for factor in (2, 3):
        scaled = agent_splash.scale_logo(base, factor)
        sampled = tuple(scaled[index * factor][::factor]
                        for index in range(len(base)))
        assert sampled == tuple(base), factor


def test_logo_for_picks_large_then_medium_then_tiny_as_the_width_falls():
    """Three real letterforms, each tried in turn, rather than one clipped at
    the right-hand edge. A block logo cut off does not read as a logo that did
    not fit, it reads as a fault."""
    base = agent_splash.base_logo()
    large = agent_splash.scale_logo(base, agent_splash.LOGO_SCALE)
    assert agent_splash.logo_for(100, Tty())[0] == large
    assert agent_splash.logo_for(40, Tty())[0] == tuple(base)
    assert agent_splash.logo_for(20, Tty())[0] == agent_splash.TINY_LOGO


def test_the_wordmark_always_fits_the_columns_it_was_given():
    """Every row in TMT is drawn to `columns - 1`, because a row filled to the
    last column auto-wraps on some terminals and costs a screen line the
    repaint arithmetic does not know about. Swept rather than sampled, since a
    size boundary is exactly where an off-by-one lives."""
    for stream in (Tty(), Cp1252()):
        for columns in range(4, 121):
            rows, reported = agent_splash.logo_for(columns, stream)
            assert reported <= columns - 1, (columns, reported)
            if rows:
                measured = max(display_width(row) for row in rows)
                assert measured == reported, (columns, measured, reported)


def test_the_wordmark_only_ever_gets_smaller_as_the_terminal_narrows():
    """Monotonic, which is what makes the three sizes a ladder rather than
    three separate decisions. A width that produced a BIGGER wordmark than a
    wider one would be a rule nobody could reason about, and it is the shape
    of bug a `<=` written the wrong way round produces."""
    previous = None
    for columns in range(120, 3, -1):
        rows, reported = agent_splash.logo_for(columns, Tty())
        current = (len(rows), reported)
        if previous is not None:
            assert current <= previous, (columns, current, previous)
        previous = current


def test_the_block_glyph_degrades_to_a_hash_on_a_cp1252_stream():
    """A row of replacement marks reads as a bug; a deliberate ASCII interface
    reads as a choice. `#` is what `agent_menu.render_banner` already degrades
    to, so the fallback wordmark is one shape rather than two."""
    rows, _ = agent_splash.logo_for(100, Cp1252())
    joined = "".join(rows)
    assert "\u2588" not in joined
    assert "#" in joined
    assert "\ufffd" not in joined


def test_the_tiny_wordmark_is_used_when_nothing_else_fits():
    """`T M T` is spaced rather than plain so it still reads as a wordmark
    rather than as a word, and it is the last size before the screen gives the
    logo up entirely."""
    rows, reported = agent_splash.logo_for(20, Tty())
    assert rows == agent_splash.TINY_LOGO
    assert reported == display_width(agent_splash.TINY_LOGO[0])
    assert reported == 5


def test_an_absurdly_narrow_terminal_is_given_no_wordmark_at_all():
    """Narrower than seven columns plus its margins and the wordmark is given
    up rather than drawn over the edge. Returning a clipped `T M` would put a
    letterform on screen that is not the letterform."""
    for columns in range(1, 8):
        rows, reported = agent_splash.logo_for(columns, Tty())
        assert rows == (), (columns, rows)
        assert reported == 0, columns


# --- the frame ---------------------------------------------------------------

def test_the_frame_is_exactly_one_row_shorter_than_the_window():
    """The region writes a newline after every line it paints, so a frame as
    tall as the window scrolls the top of itself away and takes the repaint
    arithmetic with it. One row is given back, everywhere in TMT, for exactly
    that."""
    for rows in range(2, 41):
        for columns in (20, 40, 80, 120):
            drawn = frame(size=(columns, rows))
            assert len(drawn) == rows - 1, (columns, rows, len(drawn))


def test_no_row_of_the_frame_ever_reaches_the_last_column():
    """The auto-wrap rule, swept across every width, every state and both
    kinds of terminal. This is the one that silently marches a live frame down
    the screen when it is wrong, and it is measured with the escapes stripped
    because a painted row is mostly characters that occupy no columns."""
    detail = "a detail line long enough to need trimming on a narrow window"
    # Every single width, on the cheap stream: the boundaries between the three
    # wordmarks and the three subtitle tiers are exactly where an off-by-one
    # lives, so none of them may be stepped over.
    for columns in range(6, 121):
        for name in agent_splash.STATES:
            state = agent_splash.SplashState(name, detail)
            for row in frame(state, Cp1252(), (columns, 24), 0.37):
                assert width_of(row) <= columns - 1, (
                    columns, name, repr(visible(row)))
    # And a coarser pass with the colour on, at several window heights and two
    # moments, because a painted row is mostly characters that occupy no
    # columns and measuring it by length is the mistake this guards.
    for columns in range(6, 121, 6):
        for rows in (5, 24, 40):
            for name in agent_splash.STATES:
                for moment in (0.0, 0.37):
                    state = agent_splash.SplashState(name, detail)
                    for row in frame(state, Tty(), (columns, rows), moment):
                        assert width_of(row) <= columns - 1, (
                            columns, rows, name, repr(visible(row)))


def test_the_wordmark_is_centred_within_a_column():
    """Centred by measurement and never by character count, and asserted as
    the exact margin rather than as an approximation: the left pad and the
    right remainder may differ by one column and no more, which is the parity
    of an odd amount of slack and nothing else."""
    for columns in (100, 80, 60, 40, 30, 20):
        stream = Cp1252()          # no escapes, so the row IS what it says
        logo, logo_width = agent_splash.logo_for(columns, stream)
        if not logo:
            continue
        width = columns - 1
        pad = (width - logo_width) // 2
        remainder = width - pad - logo_width
        assert abs(pad - remainder) <= 1, (columns, pad, remainder)
        drawn = [row for row in frame(stream=stream, size=(columns, 30))
                 if row.strip()]
        assert drawn[:len(logo)] == [" " * pad + row for row in logo], columns


def test_the_frame_is_vertically_balanced():
    """Blank space above the wordmark and below the subtitle. A block pushed
    against the top of the viewport is not a launch screen, it is the top of
    one, and the optical centre is above the geometric one, so the padding
    above is deliberately the smaller half."""
    drawn = [visible(row) for row in frame(size=(80, 24))]
    filled = [index for index, row in enumerate(drawn) if row.strip()]
    assert filled, drawn
    assert filled[0] >= 2, filled
    assert len(drawn) - 1 - filled[-1] >= 2, filled
    assert filled[0] <= len(drawn) - 1 - filled[-1], filled


def test_the_wordmark_and_the_subtitle_are_in_the_same_frame():
    """One frame is the whole screen. A composition that drew the logo and
    left the subtitle to a second paint would show a wordmark with no
    instruction under it for however long the gap was."""
    drawn = [visible(row) for row in frame(stream=Cp1252(), size=(80, 24))]
    assert any("#" in row for row in drawn), drawn
    assert any("Press Enter to Continue" in row for row in drawn), drawn


def test_two_frames_at_the_same_moment_are_byte_identical():
    """Nothing here reads a hidden clock. If it did, a test that drove the
    animation would be measuring the machine, and `LiveRegion` would repaint a
    frame that had not changed -- which is the flicker this repository already
    fixed once."""
    state = agent_splash.SplashState()
    stream = Tty()
    assert (agent_splash.render_splash_frame(state, stream, (80, 24), 1.7)
            == agent_splash.render_splash_frame(state, stream, (80, 24), 1.7))


def test_the_wordmark_moves_with_the_subtitle_while_the_screen_waits():
    """The launch screen is an attract screen: nothing on it is being read
    yet, so the wordmark rides the same red -> orange -> green cycle the
    subtitle does and says the program is alive.

    This test asserted the opposite until the wordmark was asked to move, and
    it is kept rather than deleted because the half that matters is unchanged:
    the movement is COLOUR ONLY. Every row still reads the same with the
    escapes stripped, which is the rule the whole interface keeps.
    """
    state = agent_splash.SplashState()
    stream = Tty()
    first = agent_splash.render_splash_frame(state, stream, (80, 24), 0.0)
    second = agent_splash.render_splash_frame(state, stream, (80, 24), 0.9)
    moved = [index for index, (a, b) in enumerate(zip(first, second)) if a != b]
    # The ten logo rows and the subtitle, rather than the subtitle alone.
    assert len(moved) > 1, moved
    assert any("Press Enter to Continue" in visible(second[index])
               for index in moved), moved
    assert any(set(visible(second[index]).strip()) <= set("█ ")
               and visible(second[index]).strip() for index in moved), moved
    # Nothing but the colour changed on any of them.
    for index in moved:
        assert visible(first[index]) == visible(second[index]), index


def test_a_settled_wordmark_holds_still_so_the_repaint_is_skipped():
    """The other half of the rule, and the load-bearing half. A fact that
    pulses looks like it is still deciding -- and a frame that stops changing
    is a frame `LiveRegion` never repaints, which is what keeps a screen being
    read cheap and its cursor still."""
    stream = Tty()
    # SKIPPED is deliberately absent: with the setting off its bar is still
    # filling, so that frame is meant to change. It gets its own test below.
    for name in (agent_splash.CURRENT, agent_splash.UPDATED,
                 agent_splash.BLOCKED, agent_splash.FAILED, agent_splash.DONE):
        state = agent_splash.SplashState(name)
        early = agent_splash.render_splash_frame(state, stream, (80, 24), 0.0)
        later = agent_splash.render_splash_frame(state, stream, (80, 24), 1.7)
        assert early == later, name


def _bar_row(rows):
    """The bar's row from a rendered frame, or None when there is not one."""
    for row in rows:
        body = visible(row).strip()
        if body and not set(body) - set("#-█░"):
            return body
    return None


def test_waiting_draws_no_bar_at_all():
    """Nothing is under way before Enter, and an empty bar under "Press Enter
    to Continue" would say that something was."""
    assert agent_splash.SplashState().progress(1.0) is None
    assert _bar_row(frame(agent_splash.SplashState(), Tty(), (80, 24))) is None


def test_with_auto_update_off_the_bar_fills_over_the_stated_time():
    """The ask, in one test: with nothing to check, the bar takes
    LAUNCH_BAR_SECONDS to fill and the stage ends when it is full."""
    state = agent_splash.SplashState(agent_splash.SKIPPED).begin(0.0)
    span = agent_splash.LAUNCH_BAR_SECONDS
    assert state.progress(0.0) == 0, state.progress(0.0)
    assert 40 <= state.progress(span / 2) <= 60, state.progress(span / 2)
    assert state.progress(span) == 100, state.progress(span)
    # And it never runs past the end, however long the frame is held.
    assert state.progress(span * 4) == 100


def test_the_skipped_stage_is_exactly_as_long_as_the_bar():
    """The bar is not decoration laid over a wait of some other length. The
    1.5 seconds it takes to fill IS the stage."""
    held = []

    class Clock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

    clock = Clock()

    class Region:
        def paint(self, frame):
            held.append(clock.now)
            clock.now += 0.08

    state = agent_splash.run_update_stage(
        agent_splash.SplashState(), Tty(), Region(), clock, auto_update=False)
    assert state.outcome == agent_splash.SKIPPED, state
    assert held, "the stage painted nothing"
    # It ran for the bar's length, not the settled dwell's.
    assert abs(held[-1] - agent_splash.LAUNCH_BAR_SECONDS) < 0.15, held[-1]
    assert held[-1] > agent_splash.SETTLED_DWELL, held[-1]


def test_a_check_of_unknown_length_never_claims_to_be_finished():
    """The rule the agent bars already keep, and the reason this is not a
    fabricated number: nothing can know how long a `git fetch` takes, so the
    bar approaches the end and stops there. 100 means the work is over."""
    state = agent_splash.SplashState(agent_splash.CHECKING).begin(0.0)
    for moment in (0.5, 1.5, 15.0, 600.0):
        assert state.progress(moment) <= 99, (moment, state.progress(moment))
    # It reaches 100 only once something has actually settled.
    assert agent_splash.SplashState(agent_splash.CURRENT).progress(0.0) == 100
    assert agent_splash.SplashState(agent_splash.DONE).progress(0.0) == 100


def test_a_bar_is_never_drawn_full_until_the_work_really_is_done():
    """The cap has to be VISIBLE or it is not a cap. `cycle_bar` rounds, and
    99% of 28 cells rounds to all 28 -- so without holding the last cell back
    a check of unknown length looked exactly like a finished one, which is the
    single thing this bar must never say."""
    checking = agent_splash.SplashState(agent_splash.CHECKING).begin(0.0)
    for moment in (9.0, 600.0):
        row = _bar_row(frame(checking, Tty(), (72, 20), moment))
        assert row.endswith("░"), (moment, row)
    for done in (agent_splash.SplashState(agent_splash.CURRENT),
                 agent_splash.SplashState(agent_splash.SKIPPED).begin(0.0)):
        row = _bar_row(frame(done, Tty(), (72, 20), agent_splash.LAUNCH_BAR_SECONDS))
        assert set(row) == {"█"}, row


def test_the_bar_and_the_wordmark_stop_together():
    """One rule for the whole frame. A wordmark frozen over a moving bar, or
    a bar still cycling under a settled wordmark, would each say the screen
    was in two states at once."""
    filling = agent_splash.SplashState(agent_splash.SKIPPED).begin(0.0)
    assert filling.moving(0.5) is True
    assert filling.moving(agent_splash.LAUNCH_BAR_SECONDS) is False
    assert agent_splash.SplashState(agent_splash.CHECKING).moving(9.9) is True
    assert agent_splash.SplashState(agent_splash.CURRENT).moving(0.0) is False
    # Once it has stopped, the frame really is byte-identical between ticks.
    full = agent_splash.SplashState(agent_splash.SKIPPED).begin(0.0)
    span = agent_splash.LAUNCH_BAR_SECONDS
    assert (agent_splash.render_splash_frame(full, Tty(), (80, 24), span)
            == agent_splash.render_splash_frame(full, Tty(), (80, 24), span + 3))


def test_the_bar_reads_on_a_console_that_can_draw_none_of_it():
    """cp1252 through a pipe carries neither the block characters nor the
    colour, and the screen still has to say how far along it is."""
    state = agent_splash.SplashState(agent_splash.SKIPPED).begin(0.0)
    row = _bar_row(frame(state, Cp1252(), (80, 24), agent_splash.LAUNCH_BAR_SECONDS))
    assert row is not None and set(row) <= set("#-"), row
    assert "#" in row, row


def test_the_detail_is_drawn_as_its_own_row_under_the_subtitle():
    """Running the sentence and the reason together makes a line too long to
    centre on a narrow terminal. Two rows, and the second one recedes."""
    state = agent_splash.SplashState(agent_splash.BLOCKED,
                                     "Local changes; your work is untouched.")
    drawn = [visible(row) for row in frame(state, Cp1252(), (80, 24))]
    # The bar is the last row of the block now, so it is taken off first --
    # this test is about the two SENTENCES and the order they are in.
    filled = [row.strip() for row in drawn
              if row.strip() and set(row.strip()) - set("#-█░")]
    assert filled[-2] == "Continuing without updating.", filled
    assert filled[-1] == "Local changes; your work is untouched.", filled


def test_a_frame_with_no_wordmark_still_says_what_to_press():
    """The subtitle is the only thing on this screen the user has to act on,
    so it is the last thing to be given up -- long after the logo has gone."""
    drawn = [visible(row) for row in frame(stream=Cp1252(), size=(7, 10))]
    assert any("Enter" in row for row in drawn), drawn
    assert not any("#" in row for row in drawn), drawn


def test_the_whole_frame_reads_with_no_escapes_at_all_on_a_pipe():
    """Colour is never the message. A stream that is not a terminal gets the
    words and nothing else, and a stray escape reaching a log file or a pipe
    is the visible half of that rule being broken."""
    for row in frame(stream=Cp1252(), size=(80, 24)):
        assert "\033" not in row, repr(row)


# --- the subtitle, and its animation -----------------------------------------

def test_every_state_has_something_to_say_except_done():
    """DONE means the screen is finished with and the application is taking
    over, so it draws nothing. Every other state is a state somebody is
    looking at, and one that produced an empty line would leave the screen
    saying nothing while something was happening."""
    for name in agent_splash.STATES:
        text = agent_splash.SplashState(name).subtitle(0.0)
        if name == agent_splash.DONE:
            assert text == "", name
        else:
            assert text.strip(), name


def test_the_waiting_state_names_the_one_key_it_wants():
    """The screen names one key and ignores the rest, so the sentence has to
    be that key. "Press any key" would be a lie: Esc and q do nothing here."""
    assert (agent_splash.SplashState().subtitle(0.0, 79)
            == "Press Enter to Continue")


def test_a_working_state_cycles_its_trailing_dots():
    """The dots are the whole of the animation on a terminal that cannot
    colour, and they are what says a check is still out rather than stuck.
    Driven off the moment, so nothing sleeps to prove it."""
    state = agent_splash.SplashState(agent_splash.CHECKING)
    seen = [state.subtitle(step * agent_splash._DOT_PERIOD, 79)
            for step in range(agent_splash._DOT_STEPS + 1)]
    assert len(set(seen)) == agent_splash._DOT_STEPS, seen
    assert seen[0] == seen[agent_splash._DOT_STEPS], seen


def test_the_dots_never_exceed_the_configured_number():
    """The row's width has to change visibly without ever growing enough to
    move anything around it. An unbounded counter would push the centred line
    off its own centre as it ran."""
    for name in (agent_splash.CHECKING, agent_splash.UPDATING):
        state = agent_splash.SplashState(name)
        for step in range(40):
            text = state.subtitle(step * 0.07, 79)
            dots = len(text) - len(text.rstrip("."))
            assert 1 <= dots <= agent_splash._DOT_STEPS, (name, text)


def test_a_settled_state_never_gains_dots():
    """"Up to date." is a fact, and a fact that grows an ellipsis looks like
    it is still deciding. Asserted as sameness across moments rather than as
    an absence of dots, because UPDATED's own sentence ends in three of
    them."""
    for name in (agent_splash.CURRENT, agent_splash.UPDATED,
                 agent_splash.BLOCKED, agent_splash.FAILED,
                 agent_splash.SKIPPED):
        state = agent_splash.SplashState(name)
        said = {state.subtitle(step * 0.13, 79) for step in range(20)}
        assert len(said) == 1, (name, said)


def test_the_skipped_state_never_claims_a_search_that_did_not_happen():
    """When `Auto Update on Launch` is off, nothing was checked. Saying
    "Searching for updates" there would be a claim about work that did not
    happen, on the one screen whose whole job is to report what is going on --
    which is why SKIPPED is its own state rather than folded into CURRENT."""
    state = agent_splash.SplashState(agent_splash.SKIPPED)
    for width in list(range(6, 121, 4)) + [None]:
        for moment in (0.0, 0.4, 1.1):
            said = state.subtitle(moment, width).lower()
            assert "update" not in said, (width, said)
            assert "search" not in said, (width, said)
            assert "check" not in said, (width, said)


def test_a_narrow_terminal_shortens_the_sentence_rather_than_cutting_a_word():
    """"Press Ent" is not an instruction, it is the beginning of one, and a
    user who has to guess what the rest of the word was has been given
    nothing. Every width has to produce a WHOLE form from one of the three
    tables, never a prefix of a longer one."""
    whole = {text for table in (agent_splash._SUBTITLES,
                                agent_splash._SHORT_SUBTITLES,
                                agent_splash._TINY_SUBTITLES)
             for text in table.values() if text}
    for width in range(6, 121):
        text = agent_splash.SplashState().subtitle(0.0, width)
        assert text in whole, (width, text)
        assert not (text.startswith("Press Ent")
                    and text not in ("Press Enter", "Press Enter to Continue")), (
            width, text)


def test_the_subtitle_the_user_must_act_on_always_fits_its_room():
    """The instruction is the one line that may not be clipped, so at every
    width from six columns up the form chosen has to be one that fits. An
    informational line may be trimmed by the frame; this one may not."""
    for width in range(6, 121):
        text = agent_splash.SplashState().subtitle(0.0, width)
        assert display_width(text) <= width, (width, text)


def test_the_shortest_form_is_reached_before_the_room_runs_out():
    """The three tiers have to actually be three tiers. An implementation that
    only ever fell back one step would put "Press Enter" on a terminal with
    room for seven columns, which is the clipped instruction the tiers exist
    to avoid."""
    assert agent_splash.SplashState().subtitle(0.0, 79) == "Press Enter to Continue"
    assert agent_splash.SplashState().subtitle(0.0, 20) == "Press Enter"
    assert agent_splash.SplashState().subtitle(0.0, 8) == "Enter"


def test_paint_subtitle_sweeps_the_gradient_on_a_truecolor_terminal():
    """Red-orange-green along the text, which is the same language the rest of
    TMT uses and needs no new colour. The sweep has to actually move: two
    moments that painted identically would be a still line pretending to
    animate."""
    text = "Press Enter to Continue"
    first = agent_splash.paint_subtitle(text, Tty(), agent_splash.WAITING, 0.0)
    later = agent_splash.paint_subtitle(text, Tty(), agent_splash.WAITING, 0.8)
    assert "38;2;" in first
    assert first != later
    assert visible(first) == text
    assert visible(later) == text


def test_paint_subtitle_pulses_the_weight_when_colour_is_refused():
    """A terminal with NO_COLOR set still takes bold and faint, and those are
    ATTRIBUTES rather than colours -- which is what makes them the honest
    answer to "no colour". A pulse is the oscillation this screen can still
    afford there."""
    guard = NoColour()
    try:
        text = "Press Enter"
        seen = [agent_splash.paint_subtitle(text, Tty(), agent_splash.WAITING,
                                            step * 0.35)
                for step in range(4)]
        assert any("\033[2m" in painted for painted in seen), seen
        assert any("\033[1m" in painted for painted in seen), seen
        assert any(painted == text for painted in seen), seen
    finally:
        guard.close()


def test_the_weight_pulse_never_reaches_for_a_colour():
    """Emitting a 24-bit colour escape on the NO_COLOR path would be answering
    "no colour" with a colour. TMT's own `DIM` is a truecolor grey, so reusing
    it here -- the obvious thing to do -- is exactly the bug."""
    guard = NoColour()
    try:
        for step in range(8):
            painted = agent_splash.paint_subtitle(
                "Press Enter", Tty(), agent_splash.WAITING, step * 0.17)
            assert "38;2;" not in painted, (step, repr(painted))
    finally:
        guard.close()


def test_paint_subtitle_writes_no_escapes_at_all_to_a_stream_that_is_not_a_terminal():
    """A pipe cannot animate anything, and the screen still has to say exactly
    what it says. An escape reaching a log file is the visible half of "colour
    is never the message" being broken."""
    for step in range(6):
        painted = agent_splash.paint_subtitle(
            "Press Enter", Cp1252(), agent_splash.WAITING, step * 0.21)
        assert painted == "Press Enter", (step, repr(painted))


def test_a_settled_state_does_not_animate_at_all():
    """A fact that pulses looks like it is still deciding. The choice is made
    on the STATE, not on the terminal, so a truecolor terminal gets the plain
    words for a settled line."""
    for name in (agent_splash.CURRENT, agent_splash.UPDATED,
                 agent_splash.BLOCKED, agent_splash.FAILED,
                 agent_splash.SKIPPED, agent_splash.DONE):
        for moment in (0.0, 0.4, 1.3):
            painted = agent_splash.paint_subtitle("Up to date.", Tty(), name,
                                                  moment)
            assert painted == "Up to date.", (name, moment, repr(painted))


def test_every_form_of_the_animation_reads_the_same_with_the_escapes_stripped():
    """Three terminals, one sentence. The movement was never the message, so
    all three have to say the identical thing once the escapes are gone."""
    text = "Press Enter to Continue"
    coloured = agent_splash.paint_subtitle(text, Tty(), agent_splash.WAITING, 0.3)
    guard = NoColour()
    try:
        pulsed = agent_splash.paint_subtitle(text, Tty(),
                                             agent_splash.WAITING, 0.0)
    finally:
        guard.close()
    plain = agent_splash.paint_subtitle(text, Cp1252(), agent_splash.WAITING, 0.3)
    assert visible(coloured) == visible(pulsed) == visible(plain) == text


def test_an_empty_subtitle_paints_to_nothing():
    """DONE has no line, and painting nothing must not produce a bare pair of
    escapes -- a row that is only a colour and a reset still costs a screen
    line and still repaints."""
    assert agent_splash.paint_subtitle("", Tty(), agent_splash.WAITING, 0.4) == ""
    assert agent_splash.paint_subtitle("", Tty(), agent_splash.DONE, 0.4) == ""


def test_the_lines_of_a_state_drop_what_is_empty():
    """A state with no detail must not contribute a blank row, or the
    subtitle's own centring shifts up by one whenever the updater happened to
    say nothing."""
    assert agent_splash.SplashState().lines(0.0, 79) == ["Press Enter to Continue"]
    assert agent_splash.SplashState(agent_splash.DONE).lines(0.0, 79) == []
    assert agent_splash.SplashState(agent_splash.FAILED, "no network").lines(
        0.0, 79) == ["Update check failed. Continuing without update.",
                     "no network"]


# --- the state machine -------------------------------------------------------

def test_a_new_launch_is_waiting():
    """WAITING is the only state that reads a key, and starting anywhere else
    would mean the screen ran the update stage before anybody pressed
    anything."""
    state = agent_splash.SplashState()
    assert state.state == agent_splash.WAITING
    assert state.waiting and not state.working and not state.finished
    assert state.detail == "" and state.outcome is None and state.result is None


def test_waiting_working_and_finished_answer_for_every_state():
    """The three questions the caller asks, over the whole set. A state added
    later that answered two of them wrongly would paint an animation on a
    settled line, or hold the screen on a state that had finished."""
    for name in agent_splash.STATES:
        state = agent_splash.SplashState(name)
        assert state.waiting == (name == agent_splash.WAITING), name
        assert state.working == (name in (agent_splash.CHECKING,
                                          agent_splash.UPDATING)), name
        assert state.finished == (name == agent_splash.DONE), name


def test_advancing_to_a_state_that_does_not_exist_is_refused():
    """A typo has to be a ValueError here rather than a subtitle that silently
    never appears, and the message has to name the set so the fix is in the
    error rather than in the source."""
    try:
        agent_splash.SplashState().advance("nearly-done")
    except ValueError as error:
        message = str(error)
    else:
        raise AssertionError("an unknown launch state was accepted")
    assert "nearly-done" in message
    for name in agent_splash.STATES:
        assert name in message, (name, message)


def test_an_unknown_opening_state_falls_back_to_waiting():
    """The constructor is the one door a caller can push a bad value through
    without going near `advance`. It cannot raise -- this object is built
    before the screen exists -- so it settles on the safe state instead."""
    assert agent_splash.SplashState("nonsense").state == agent_splash.WAITING


def test_advance_records_the_detail_and_the_updaters_own_result():
    """The detail is the second row on screen and the result is what the
    caller reads to decide whether to restart. Losing either one leaves the
    screen right and the decision wrong."""
    outcome = Result(Updater.UPDATED, "fast-forwarded 3 commits")
    state = agent_splash.SplashState().advance(agent_splash.BLOCKED,
                                               "local changes", outcome)
    assert state.state == agent_splash.BLOCKED
    assert state.detail == "local changes"
    assert state.result is outcome


def test_finish_records_the_outcome_and_keeps_the_detail():
    """DONE says the screen is finished and nothing about what it found. The
    state it came from is the answer to "what happened" and the detail is why,
    and both are read after the screen is gone."""
    state = agent_splash.SplashState().advance(agent_splash.BLOCKED,
                                               "Local changes")
    state.finish()
    assert state.state == agent_splash.DONE
    assert state.outcome == agent_splash.BLOCKED
    assert state.detail == "Local changes"
    assert state.finished


def test_finishing_twice_does_not_overwrite_the_outcome():
    """The second call would otherwise record DONE as the outcome and lose the
    only record of how the launch ended -- the same shape of bug as a retire
    that clears what it was meant to keep."""
    state = agent_splash.SplashState().advance(agent_splash.FAILED, "no git")
    state.finish()
    state.finish()
    assert state.outcome == agent_splash.FAILED
    assert state.state == agent_splash.DONE


def test_state_for_update_maps_every_status_the_updater_can_report():
    """The one place two vocabularies meet. `agent_update` answers "what is
    true of this checkout" and this answers "what should the person watching
    be told", and every status the updater has must land somewhere -- a
    status that fell through would report a clean check as a failure.

    DISABLED is BLOCKED and deliberately not SKIPPED, which is the one entry
    worth reading twice: SKIPPED means "nothing was checked", and the updater
    says DISABLED when it DID look, found an update and was not allowed to
    apply it. The user turning the setting off never reaches this mapping at
    all -- `run_update_stage` answers that itself, before the updater is
    called."""
    module = Updater()
    expected = {
        module.CURRENT: agent_splash.CURRENT,
        module.UPDATED: agent_splash.UPDATED,
        module.BLOCKED_DIRTY: agent_splash.BLOCKED,
        module.BLOCKED_DIVERGED: agent_splash.BLOCKED,
        module.NO_UPSTREAM: agent_splash.BLOCKED,
        module.NOT_A_REPO: agent_splash.BLOCKED,
        module.DISABLED: agent_splash.BLOCKED,
        module.ERROR: agent_splash.FAILED,
    }
    for status, want in expected.items():
        got, _ = agent_splash.state_for_update(Result(status, "why"), module)
        assert got == want, (status, got, want)


def test_the_statuses_are_read_off_the_module_that_was_handed_in():
    """The stub's constants are deliberately not the real module's strings. A
    mapping that read `agent_update`'s own constants to interpret somebody
    else's result would send every status to FAILED, which is exactly what it
    did until a test drove an injected updater through it."""
    module = Updater()
    assert module.CURRENT not in agent_splash.STATES
    got, _ = agent_splash.state_for_update(Result(module.CURRENT), module)
    assert got == agent_splash.CURRENT


def test_a_status_the_updater_may_not_even_have_is_read_with_getattr():
    """`AVAILABLE` is the updater's answer to "what does this comparison mean"
    rather than to "what happened", and `check_and_update` does not return it.
    It is mapped anyway, to BLOCKED, and read with `getattr` -- so an updater
    that drops the constant does not take the launch screen with it, and one
    that has it does not get an unrecognised status reported as a failure."""
    without = Updater()
    assert not hasattr(without, "AVAILABLE")
    assert agent_splash.state_for_update(Result(without.CURRENT), without)[0] \
        == agent_splash.CURRENT

    class WithAvailable(Updater):
        AVAILABLE = "u-available"

    module = WithAvailable()
    state, detail = agent_splash.state_for_update(
        Result(module.AVAILABLE, "one commit behind"), module)
    assert state == agent_splash.BLOCKED
    assert detail == "one commit behind"


def test_an_unrecognised_status_is_a_failure_and_never_a_guess():
    """A status this screen has never heard of is not one it can honestly
    describe, and FAILED continues into TMT, which is the safe direction. A
    result object with no status at all takes the same road."""
    module = Updater()
    assert agent_splash.state_for_update(Result("who-knows"), module)[0] == \
        agent_splash.FAILED
    assert agent_splash.state_for_update(None, module)[0] == agent_splash.FAILED
    assert agent_splash.state_for_update(object(), module)[0] == \
        agent_splash.FAILED


def test_current_and_updated_clear_the_detail():
    """"Up to date." already says what happened, so the headline under it
    would draw the same fact twice on two rows. Every other outcome keeps its
    reason, because for those the sentence is not the whole story."""
    module = Updater()
    for status in (module.CURRENT, module.UPDATED):
        _, detail = agent_splash.state_for_update(
            Result(status, "already at 0cbc902"), module)
        assert detail == "", status
    for status in (module.BLOCKED_DIRTY, module.NO_UPSTREAM, module.ERROR):
        _, detail = agent_splash.state_for_update(
            Result(status, "the reason"), module)
        assert detail == "the reason", status


def test_state_for_update_with_no_updater_at_all_is_a_failure():
    """No updater is not an exception here. The screen has a state for a
    checkout it cannot check, and raising would be a launch screen that stops
    TMT launching."""
    previous = agent_splash._updater
    agent_splash._updater = lambda: None
    try:
        assert agent_splash.state_for_update(Result("anything")) == (
            agent_splash.FAILED, "")
    finally:
        agent_splash._updater = previous


# --- the launch flow ---------------------------------------------------------

def test_a_stream_that_is_not_a_terminal_starts_tmt_and_draws_nothing():
    """The single most important assertion in this file. `run_splash` refuses
    a stream it cannot drive, before anything is painted and before a key is
    ever asked for, and that one line is what keeps every piped run, every
    script and the whole of this suite behaving exactly as they did before
    this screen existed."""

    def never(*args, **kwargs):
        raise AssertionError("a key was read from a stream that is not a tty")

    stream = io.StringIO()
    assert agent_splash.run_splash(stream=stream, key_reader=never) == "start"
    assert stream.getvalue() == ""


def test_the_updater_is_never_asked_when_the_setting_is_off():
    """`Auto Update on Launch` off means nothing is checked -- not checked
    quietly, not checked and discarded. A stub that counts its calls is the
    only way to see the difference, because both spellings end on the same
    screen."""
    module = Updater()
    state = stage(module, auto_update=False)
    assert module.checks == 0
    assert state.outcome == agent_splash.SKIPPED
    assert state.finished


def test_every_status_the_updater_reports_reaches_the_right_outcome():
    """End to end through `run_update_stage` rather than through
    `state_for_update` alone: the mapping being right is not the same as the
    stage using it, and the outcome is what the caller reads."""
    module = Updater()
    expected = (
        (module.CURRENT, agent_splash.CURRENT),
        (module.UPDATED, agent_splash.UPDATED),
        (module.BLOCKED_DIRTY, agent_splash.BLOCKED),
        (module.BLOCKED_DIVERGED, agent_splash.BLOCKED),
        (module.NO_UPSTREAM, agent_splash.BLOCKED),
        (module.NOT_A_REPO, agent_splash.BLOCKED),
        (module.DISABLED, agent_splash.BLOCKED),
        (module.ERROR, agent_splash.FAILED),
    )
    for status, want in expected:
        state = stage(Updater(status), restart=lambda: None)
        assert state.outcome == want, (status, state.outcome)
        assert state.finished, status


def test_only_a_successful_update_restarts_the_process():
    """A restart on a checkout that was already current would relaunch TMT for
    no reason, every single time it was started. Counted rather than observed,
    and asserted at zero for every other outcome."""
    module = Updater(Updater.UPDATED)
    calls = []
    state = stage(module, restart=lambda: calls.append("restart"))
    assert state.outcome == agent_splash.UPDATED
    assert calls == ["restart"]

    for status in (Updater.CURRENT, Updater.BLOCKED_DIRTY, Updater.ERROR,
                   Updater.NOT_A_REPO, Updater.DISABLED):
        calls = []
        stage(Updater(status), restart=lambda: calls.append("restart"))
        assert calls == [], status

    calls = []
    stage(Updater(Updater.UPDATED), auto_update=False,
          restart=lambda: calls.append("restart"))
    assert calls == []


def test_an_updater_that_raises_still_lets_tmt_launch():
    """`check_and_update` is documented not to raise, and the belt-and-braces
    catch is there because a launch screen that could be taken down by a
    network error would be a launch screen that stops TMT starting."""
    module = Updater(raises=RuntimeError("git is not on the PATH"))
    calls = []
    state = stage(module, restart=lambda: calls.append("restart"))
    assert module.checks == 1
    assert state.outcome == agent_splash.FAILED
    assert calls == []
    assert state.finished


def test_an_updater_that_returns_nothing_is_a_failure_rather_than_a_pass():
    """`None` back from the worker means the check never reported. Treating
    that as a clean result would draw "Up to date." for a check that did not
    happen, which is the one thing this screen may not do."""

    class Silent(Updater):
        def check_and_update(self, root=None):
            self.checks += 1
            return None

    state = stage(Silent())
    assert state.outcome == agent_splash.FAILED


def test_an_updater_that_cannot_be_imported_blocks_rather_than_raising():
    """A frozen module list, or `agent_update` mid-edit. The screen has a
    state for a checkout it cannot check and it says so in words, and the
    launch carries on."""
    previous = agent_splash._updater
    agent_splash._updater = lambda: None
    try:
        state = stage(None)
    finally:
        agent_splash._updater = previous
    assert state.outcome == agent_splash.BLOCKED
    assert "unavailable" in state.detail.lower(), state.detail


def test_the_workspace_root_reaches_the_updater():
    """`root` is how a caller says which checkout to look at. Dropped on the
    way through, the update would check whatever directory the process
    happened to be standing in."""
    module = Updater()
    stage(module, root="C:/Coding/TMT")
    assert module.roots == ["C:/Coding/TMT"]


def test_the_stage_hands_back_the_state_it_left_the_screen_in():
    """A caller that is not a terminal -- a test, or TMT deciding what to do
    next -- reads the outcome off the returned object rather than off the
    side effects."""
    module = Updater(Updater.BLOCKED_DIVERGED, "your branch has diverged")
    state = stage(module)
    assert isinstance(state, agent_splash.SplashState)
    assert state.state == agent_splash.DONE
    assert state.outcome == agent_splash.BLOCKED
    assert state.detail == "your branch has diverged"
    assert state.result is not None


def test_auto_update_enabled_reads_the_setting():
    """`agent_config` owns every stored setting in TMT, and this must read it
    rather than keeping a copy -- a second source of truth for a switch the
    user throws is a switch that stops working."""
    import agent_config
    previous = agent_config.read_saved_auto_update
    try:
        agent_config.read_saved_auto_update = lambda: False
        assert agent_splash.auto_update_enabled() is False
        agent_config.read_saved_auto_update = lambda: True
        assert agent_splash.auto_update_enabled() is True
    finally:
        agent_config.read_saved_auto_update = previous


def test_auto_update_defaults_to_on_when_the_setting_cannot_be_read():
    """A configuration file that cannot be read must not be able to silently
    disable a feature. The default is the documented one, and guarding to
    False would turn a permissions error into a TMT that never updates."""
    import agent_config
    previous = agent_config.read_saved_auto_update

    def broken():
        raise OSError("the settings file is not readable")

    try:
        agent_config.read_saved_auto_update = broken
        assert agent_splash.auto_update_enabled() is True
    finally:
        agent_config.read_saved_auto_update = previous


# --- design pins -------------------------------------------------------------

def test_the_launch_screen_never_reaches_for_the_two_banned_escapes():
    """DECSTBM discards the lines that scroll out of a narrowed region and the
    alternate screen buffer throws the terminal's history away wholesale.
    TMT's permanent surface IS that scrollback -- it is the only record a
    finished session leaves, and it is the user's own shell session above it.

    A full-screen splash is the feature most tempted by both, which is exactly
    why it may not have either: full-screen here means `clear_screen` and a
    frame built to fill the viewport, and nothing else. The module's own prose
    names both escapes in order to say they are banned, so the docstrings and
    the comments are taken out before the code is scanned."""
    path = Path(agent_splash.__file__).resolve()
    for number, line in code_lines(path):
        assert "1049" not in line, (number, line)
        assert "?47" not in line, (number, line)
        assert "?1047" not in line, (number, line)
        assert not re.search(r"\\033\[[^\"']*r[\"']", line), (number, line)


def test_the_launch_screen_defines_no_colour_of_its_own():
    """One gradient, one neutral. A new element takes a position on the
    existing scale and never gets a colour of its own, so every escape carrying
    an RGB triple has to come from `agent_ui` -- the only place in TMT where
    colour is defined. The weight pulse is the case that makes this worth
    pinning: reaching for `DIM` there is the obvious thing to do and `DIM` is
    a truecolor grey."""
    path = Path(agent_splash.__file__).resolve()
    for number, line in code_lines(path):
        assert "38;2;" not in line, (number, line)


def test_the_launch_screen_takes_its_colour_from_agent_ui_and_nowhere_else():
    """The companion to the test above, asserted from the other direction: the
    gradient, the neutral and the reset are imported rather than spelled out,
    so the one place they are defined stays the one place."""
    source = Path(agent_splash.__file__).resolve().read_text(encoding="utf-8")
    assert "from agent_ui import" in source
    for name in ("cycle_text", "display_width", "plain_output", "encodable"):
        assert name in source, name


def test_the_module_is_clean_utf_8_and_needs_nothing_outside_the_library():
    """A heredoc has corrupted this repository eight times, once writing a NUL
    byte into a module so Python refused to import it. The rest of the suite
    guards every module; this one guards the newest -- and it is the module
    that runs first, so a corrupt byte here is a TMT that cannot start at
    all."""
    text = Path(agent_splash.__file__).resolve().read_bytes().decode("utf-8")
    assert "\x00" not in text
    assert "import requests" not in text and "import rich" not in text


def test_the_splash_reads_the_terminal_on_every_frame():
    """A window resized between two paints is drawn at its new size, which is
    the whole of the resize handling: there is nothing to invalidate and
    nothing that can go stale. A size cached at startup would leave the frame
    the wrong shape for the rest of the launch."""
    state = agent_splash.SplashState()
    stream = Tty()
    assert len(agent_splash.render_splash_frame(state, stream, (80, 24), 0.4)) == 23
    assert len(agent_splash.render_splash_frame(state, stream, (80, 40), 0.4)) == 39
    narrow = agent_splash.render_splash_frame(state, stream, (30, 24), 0.4)
    wide = agent_splash.render_splash_frame(state, stream, (100, 24), 0.4)
    assert max(width_of(row) for row in narrow) <= 29
    assert max(width_of(row) for row in wide) > 29
