"""The launch screen: the first thing TMT draws, every time it starts.

What this is for. TMT used to open straight into its startup menu, which is a
menu -- a list of things to choose between, drawn the moment the process is
alive. There was no moment that belonged to the program itself, no point at
which the user had arrived rather than been dropped in, and nowhere to put the
one job that has to happen before anything else does: finding out whether the
copy of TMT about to run is the current one.

So a launch screen. A large wordmark, an oscillating line under it saying what
to press, and -- once the user has pressed it -- the update check, reported on
the same screen rather than as a burst of git output. It is always shown; the
`Auto Update on Launch` setting decides only what happens after Enter.

Five things here are deliberate and are the ones to read before changing it:

- **THERE IS NO ALTERNATE SCREEN BUFFER, AND THERE MUST NOT BE.** The obvious
  way to write a full-screen splash is `\\033[?1049h`, and it is banned in this
  repository: the alternate buffer throws the terminal's scrollback away, and
  that scrollback is the user's own shell session. DECSTBM is banned for the
  same reason -- lines scrolled out of a narrowed region are discarded rather
  than pushed into history. There is a test that greps the modules for both.
  Full-screen here means what it means everywhere else in TMT: `clear_screen`
  puts the cursor at the top of the viewport, and the frame is built to fill
  it. Nothing is destroyed and nothing is borrowed.
- **The wordmark is derived from `agent_menu.LOGO`, not written again.** That
  five-row block is the canonical TMT letterform and the startup menu already
  draws it. The splash wants a bigger one, so it SCALES that -- one source of
  truth for the shape, three sizes derived from it, and a test asserting the
  copy kept here for a broken install still matches. A second hand-drawn logo
  is a second logo to get wrong, and the day they disagree is the day TMT has
  two wordmarks.
- **The logo holds still and only the subtitle moves.** The subtitle is the
  instrument -- it is the thing saying "this program is alive and waiting for
  you" -- and it is the only part of the frame that changes between ticks. A
  wordmark that is a different colour every frame is not a wordmark, which is
  the reason `BRAND_PHASE` exists, and an animating logo would also mean every
  repaint rewrites every row for a change nobody asked to see.
- **Colour is never the message, so the oscillation has three forms.** With
  truecolor it is the gradient sweeping along the text. With ANSI but no
  colour it is a slow dim/normal/bold pulse. With neither it does not animate
  at all, and the screen still says exactly what it says. A splash that is
  only legible in a good terminal is a splash that has failed on the terminals
  that need it most.
- **Nothing here can stop TMT launching.** The state machine has an entry for
  every way the update can fail, every one of them continues into the
  application, and `run_splash` returns "start" without drawing anything at
  all when the terminal cannot be driven -- which is what keeps every piped
  run, every script and the whole test suite behaving exactly as they did
  before this screen existed.
"""

import sys
import time

from agent_ui import (
    BOLD, DIM, GRADIENT_TICK, RESET, _supports_color, cycle_text,
    display_width, encodable, fit_to_width, plain_output,
)

# How often the screen is redrawn while it waits. The same period the menus
# already animate at, named separately so the launch screen's refresh rate is
# one number somebody can find and change rather than a constant borrowed from
# somewhere else. At 0.08s the pulse is smooth and the process is idle between
# ticks -- the read is what blocks, not a sleep, so a splash left on screen
# costs a key poll every 80ms and nothing else.
SPLASH_TICK = GRADIENT_TICK

# Where the wordmark's gradient starts, and how far along the scale it runs.
# The same fixed phase the header's wordmark takes and for the same reason: a
# logo that is a different colour every launch is not a logo. It starts past
# red because red is the error end of the scale and a launch is not an error,
# and it spans far enough to reach green so the full red -> orange -> green
# identity is visible across the letters.
LOGO_PHASE = 0.15
LOGO_SPREAD = 0.85

# How much the gradient shifts between one logo row and the next, so the
# colour runs diagonally down the block rather than banding. Borrowed from
# `agent_menu._LOGO_ROW_SPREAD`, which does the same thing for the menu's
# smaller banner.
LOGO_ROW_SPREAD = 0.05

# How many terminal cells each cell of the canonical logo becomes on the
# launch screen. Two: the wordmark is the dominant element here rather than a
# banner above a menu, and doubling both axes keeps the letterforms in
# proportion while making them unmistakably the largest thing on the screen.
LOGO_SCALE = 2

# The canonical five-row wordmark, kept here ONLY as the fallback for an
# install where `agent_menu` cannot be imported -- a frozen module list that
# has not caught up, or that module mid-edit. `agent_menu.LOGO` is the
# authority and `base_logo()` prefers it; there is a test that the two agree,
# so a change to one that is not made to the other is caught rather than
# shipped as two different wordmarks.
_FALLBACK_LOGO = (
    "████████ ███    ███ ████████",
    "   ██    ████  ████    ██   ",
    "   ██    ██ ████ ██    ██   ",
    "   ██    ██  ██  ██    ██   ",
    "   ██    ██      ██    ██   ",
)

# The last resort, for a terminal too narrow for even the unscaled block. It
# is spaced rather than plain so it still reads as a wordmark rather than as a
# word, and it fits in seven columns.
TINY_LOGO = ("T M T",)

# Columns of margin the logo will not be drawn without, on top of the one
# spare column every row in TMT leaves at the right. One: a block letterform
# flush against the edge of the window reads as a clipped one, and more than
# one would refuse the unscaled wordmark on a 30-column terminal that has
# exactly enough room for it.
LOGO_MARGIN = 1


# --- the states a launch can be in -----------------------------------------
#
# These are the runtime's words for where startup has got to, and they are
# what the subtitle is drawn from. They are deliberately not the updater's
# statuses: `agent_update` answers "what is true of this checkout", and this
# answers "what should the person watching be told right now", which is a
# smaller set and a different question. `state_for_update` is the one place
# the two are mapped onto each other.

# Before Enter. The only state that reads a key.
WAITING = "waiting"
# After Enter, while the update check is out. Only reachable when the setting
# is on -- see `SKIPPED`.
CHECKING = "checking"
# A fast-forward is being applied.
UPDATING = "updating"
# The checkout was already current. No pull happened and no restart will.
CURRENT = "current"
# A fast-forward was applied. The caller restarts.
UPDATED = "updated"
# An update exists or might, and it is not safe to take: local changes, a
# diverged branch, no upstream, not a git checkout. The work is untouched.
BLOCKED = "blocked"
# The check itself failed -- no network, no git, a bad remote. TMT continues.
FAILED = "failed"
# `Auto Update on Launch` is off. Nothing was checked and the screen does not
# pretend otherwise: it must never say "Searching for updates" for a search
# that did not happen.
SKIPPED = "skipped"
# The screen is finished with and the application is about to take over.
DONE = "done"

STATES = (WAITING, CHECKING, UPDATING, CURRENT, UPDATED, BLOCKED, FAILED,
          SKIPPED, DONE)

# The states that are still moving, and whose subtitle therefore animates its
# trailing dots. A settled state's line is a fact and holds still.
_WORKING = (CHECKING, UPDATING)

# What the subtitle says in each state, before any detail from the updater is
# added. `CHECKING` and `UPDATING` carry no dots of their own: `_dots` adds
# them, so the animation has somewhere to happen and the settled states do not
# inherit an ellipsis that never moves.
_SUBTITLES = {
    WAITING: "Press Enter to Continue",
    CHECKING: "Searching for updates",
    UPDATING: "Updating",
    CURRENT: "Up to date.",
    UPDATED: "Update complete. Restarting...",
    BLOCKED: "Continuing without updating.",
    FAILED: "Update check failed. Continuing without update.",
    SKIPPED: "Starting...",
    DONE: "",
}

# The same sentences for a terminal too narrow to hold them. Shortened rather
# than clipped: "Press Ent" is not an instruction, it is the beginning of one,
# and a user who has to guess what the rest of the word was has been given
# nothing. Every one of these still says the thing that has to be acted on.
_SHORT_SUBTITLES = {
    WAITING: "Press Enter",
    CHECKING: "Checking",
    UPDATING: "Updating",
    CURRENT: "Up to date.",
    UPDATED: "Updated. Restarting...",
    BLOCKED: "Not updated.",
    FAILED: "Update failed.",
    SKIPPED: "Starting...",
    DONE: "",
}

# The last tier, for a terminal narrow enough that even the short form does
# not fit. Only the states the user has to ACT on are worth a third spelling:
# an informational line that will not fit can be clipped without costing
# anybody anything, while "Press Ent" is a word cut in half where the one
# instruction on the screen should be.
_TINY_SUBTITLES = {
    WAITING: "Enter",
}

# How many trailing dots a working state cycles through, and how long each one
# is held. Four steps rather than three so the line's width changes visibly
# without the row ever growing enough to move anything around it, and 0.25s
# because faster reads as a stutter rather than as progress.
_DOT_STEPS = 4
_DOT_PERIOD = 0.25

# How long one full pulse takes on a terminal with ANSI but no colour, in
# seconds, and the three weights it moves through. Slow on purpose: this is
# the fallback, it has only three steps to work with, and anything quicker
# than this reads as a flicker rather than as a pulse.
#
# SGR 2 rather than `agent_ui.DIM`, and that is not a detail. TMT's `DIM` is a
# truecolor grey -- it is the one neutral, a colour -- and this path is
# reached precisely when colour has been refused, by a terminal that cannot
# show it or by a user who set NO_COLOR. Emitting a 24-bit colour escape there
# would be answering "no colour" with a colour. Faint and bold are ATTRIBUTES,
# which is what is actually wanted: the same words, at a different weight.
_FAINT = "\033[2m"
_PULSE_PERIOD = 1.2
_PULSE_STEPS = (_FAINT, "", BOLD, "")


def base_logo():
    """The canonical five-row wordmark.

    `agent_menu.LOGO` when that module can be reached, which is the authority,
    and the local copy when it cannot. Imported inside the call for the reason
    `agent_panel._menu` imports inside the call: an editable install freezes
    its module list at install time, so a module present in the source tree
    can be invisible to the installed entry point -- and a launch screen that
    could not draw because of that would be a launch screen that stops TMT
    starting, which is the one thing it must never do.
    """
    try:
        import agent_menu
        rows = tuple(agent_menu.LOGO)
        if rows:
            return rows
    except Exception:
        pass
    return _FALLBACK_LOGO


def scale_logo(rows, factor=LOGO_SCALE):
    """The same letterforms, `factor` times as large on both axes.

    Every cell becomes a `factor` x `factor` block, so the shape is preserved
    exactly and the strokes stay in proportion. Scaling rather than drawing a
    second, bigger logo is what keeps one wordmark in the program: a hand-made
    large version would be a second thing to change, and the day somebody
    changed only one of them TMT would have two wordmarks that were nearly the
    same, which is worse than having one that is too small.
    """
    factor = max(1, int(factor))
    if factor == 1:
        return tuple(rows)
    scaled = []
    for row in rows:
        wide = "".join(char * factor for char in row)
        scaled.extend([wide] * factor)
    return tuple(scaled)


def _degrade(rows, stream):
    """The same rows in characters this stream can actually encode.

    `#` for the block, which is what `agent_menu.render_banner` already
    degrades to -- the same substitution in both places, so the fallback
    wordmark is one shape rather than two.
    """
    if plain_output(stream) or not encodable(stream, "█"):
        return tuple(row.replace("█", "#") for row in rows)
    return tuple(rows)


def logo_for(columns, stream=None):
    """The largest wordmark that fits in `columns`, as (rows, width).

    Three sizes, tried in order, and each is a real letterform rather than a
    clipped version of a bigger one. That is the whole of the narrow-terminal
    behaviour: a block logo cut off at the right-hand edge does not read as a
    logo that did not fit, it reads as a fault, so the screen gives up a size
    rather than giving up the right-hand end of the letters.

    The smallest is `T M T`, which is seven columns and will fit anywhere a
    terminal can be said to exist at all.
    """
    stream = sys.stdout if stream is None else stream
    columns = max(1, int(columns))
    canonical = base_logo()
    room = columns - 1 - LOGO_MARGIN * 2
    for rows in (scale_logo(canonical, LOGO_SCALE), canonical, TINY_LOGO):
        rows = _degrade(rows, stream)
        width = max(display_width(row) for row in rows)
        if width <= room:
            return rows, width
    # Narrower than seven columns plus its margins. The wordmark is given up
    # rather than drawn over the edge; the subtitle still says what to press,
    # which is the only thing on this screen the user has to act on.
    return (), 0


def _centre(text, width):
    """`text` centred in `width` columns, measured and never counted.

    `display_width`, not `len`: the block glyph and the ellipsis are not the
    only wide characters that can end up here, and a row centred by character
    count sits visibly off-centre and can overflow into a second screen line.
    """
    room = max(0, int(width) - display_width(text))
    return " " * (room // 2) + text


def _dots(state, moment):
    """The trailing ellipsis for a state that is still working, or "".

    Its own animation rather than part of the subtitle string, so a settled
    line -- "Up to date." -- cannot inherit an ellipsis that never moves, and
    so the dots can cycle without the rest of the row being rebuilt.
    """
    if state not in _WORKING:
        return ""
    step = int(float(moment) / _DOT_PERIOD) % _DOT_STEPS
    return "." * (step + 1)


def _pulse(text, stream, moment):
    """The oscillation for a terminal with ANSI but no truecolor.

    Weight rather than colour, because there is no colour to use. Three steps
    over `_PULSE_PERIOD`, which is slow enough to read as breathing; anything
    faster on two states would be a blink, and a blinking line on the screen a
    user is being asked to look at is worse than a still one.
    """
    step = int(float(moment) / (_PULSE_PERIOD / len(_PULSE_STEPS))) % len(_PULSE_STEPS)
    weight = _PULSE_STEPS[step]
    return (weight + text + RESET) if weight else text


def paint_subtitle(text, stream, state=WAITING, moment=None, phase=None):
    """The subtitle, painted with whatever oscillation this terminal can carry.

    Three forms, and the choice is made on the terminal rather than on taste:

    - truecolor: the gradient sweeps along the text, which is the same
      red-orange-green language the rest of TMT uses and needs no new colour.
    - ANSI but no colour: a slow weight pulse.
    - neither: the text, still. A terminal with no escapes cannot animate
      anything, and the screen has to remain readable there -- which it does,
      because the words are the message and the movement never was.

    A settled state does not animate at all. "Up to date." is a fact, and a
    fact that pulses looks like it is still deciding.
    """
    if not text:
        return ""
    moment = time.monotonic() if moment is None else moment
    if state not in (WAITING,) + _WORKING:
        return text
    if _supports_color(stream):
        # A there-and-back sweep, so the line can oscillate forever without
        # snapping from green back to red. `gradient_phase` walks exactly that
        # cycle; the phase is taken from the caller's clock so a test can drive
        # the animation without waiting for one.
        if phase is None:
            from agent_ui import GRADIENT_CYCLE
            phase = (float(moment) % GRADIENT_CYCLE) / GRADIENT_CYCLE
        return cycle_text(text, stream, phase, spread=0.6)
    if _has_ansi(stream):
        return _pulse(text, stream, moment)
    return text


def _has_ansi(stream):
    """Whether escapes reach this stream at all, colour or not.

    `_supports_color` is `isatty() and not NO_COLOR`, so a terminal with
    `NO_COLOR` set still takes bold and dim -- which is exactly the case the
    weight pulse exists for, and the reason this asks a narrower question than
    `_supports_color` does.
    """
    try:
        return bool(stream.isatty())
    except Exception:
        return False


class SplashState:
    """Where the launch screen has got to, and what it should say.

    An explicit state machine rather than a handful of flags, because the
    startup sequence has real branches -- checked or not, current or updated
    or blocked or failed -- and every one of them has to be reachable from a
    test without a terminal, a network or a git repository. `advance` is the
    only way the state moves and it takes one of `STATES`, so a typo is a
    ValueError here rather than a subtitle that silently never appears.
    """

    def __init__(self, state=WAITING, detail=""):
        self.state = state if state in STATES else WAITING
        self.detail = str(detail or "")
        self.result = None
        # The settled state the launch actually reached, kept once the screen
        # moves on to DONE. Without it every launch ends indistinguishable
        # from every other -- "done" says the screen is finished and nothing
        # about what it found -- and there would be nothing for a test to
        # assert on but the side effects.
        self.outcome = None

    def advance(self, state, detail="", result=None):
        """Move to `state`. Returns self so a caller can chain and a test can read."""
        if state not in STATES:
            raise ValueError("%r is not a launch state. Use one of: %s."
                             % (state, ", ".join(STATES)))
        self.state = state
        self.detail = str(detail or "")
        if result is not None:
            self.result = result
        return self

    def finish(self):
        """Move to DONE, keeping the record of how the launch ended.

        Its own method rather than `advance(DONE)` because DONE is the one
        transition that must not clear anything: the state it came from is the
        answer to "what happened", the detail is why, and both are read after
        the screen is gone -- by the caller deciding whether to restart, and
        by every test that drives this without a terminal.
        """
        if self.state != DONE:
            self.outcome = self.state
        self.state = DONE
        return self

    @property
    def waiting(self):
        return self.state == WAITING

    @property
    def working(self):
        return self.state in _WORKING

    @property
    def finished(self):
        return self.state == DONE

    def subtitle(self, moment=None, width=None):
        """The line under the wordmark, with its animation applied but unpainted.

        `width` is how many columns it has. When the full sentence will not
        fit, the SHORT one is used rather than the long one cut off: the
        subtitle is the only thing on this screen the user has to act on, and
        half an instruction is not one.
        """
        moment = time.monotonic() if moment is None else moment
        dots = _dots(self.state, moment)
        text = _SUBTITLES.get(self.state, "")
        if width is not None and text:
            # The widest form that fits, tried longest first. The dot budget
            # is counted whether or not this state animates, so the row cannot
            # change width part way through a cycle and shift under the eye.
            room = max(0, int(width) - _DOT_STEPS)
            for table in (_SUBTITLES, _SHORT_SUBTITLES, _TINY_SUBTITLES):
                candidate = table.get(self.state)
                if not candidate:
                    continue
                text = candidate
                if display_width(candidate) <= room:
                    break
        return text + dots if text else ""

    def lines(self, moment=None, width=None):
        """The subtitle and, under it, whatever the updater had to add.

        The detail is a second row rather than a longer first one: the
        subtitle is the sentence the user is reading and the detail is why,
        and running them together produces a line too long to centre on a
        narrow terminal.
        """
        rows = [self.subtitle(moment, width)]
        if self.detail:
            rows.append(self.detail)
        return [row for row in rows if row]

    def __repr__(self):
        return "SplashState(%s%s)" % (
            self.state, ", %r" % self.detail if self.detail else "")


def render_splash_frame(state=None, stream=None, size=None, moment=None,
                        phase=None):
    """The whole launch screen, as lines ready to paint.

    Exactly `rows - 1` of them, which is what fills the viewport: the region
    writes a newline after every line it paints, so a frame as tall as the
    window would scroll the top of itself away and take the repaint arithmetic
    with it. One row is given back, everywhere in TMT, for exactly that.

    The wordmark sits above the middle rather than on it. A block of text
    centred by its own height reads as low on the screen, because the eye
    puts the optical centre above the geometric one -- so the padding above is
    the smaller half.
    """
    stream = sys.stdout if stream is None else stream
    state = SplashState() if state is None else state
    moment = time.monotonic() if moment is None else moment
    columns, rows = _terminal(size)
    width = max(1, columns - 1)
    height = max(1, rows - 1)

    logo, logo_width = logo_for(columns, stream)
    body = []
    for index, row in enumerate(logo):
        # Measured on the plain row and painted afterwards, never the other
        # way round: a painted string carries escapes, and centring by its
        # length would push the wordmark left by however many bytes the colour
        # cost. It is the same order `agent_panel._row` keeps -- fit first,
        # paint second -- for the same reason.
        painted = row
        if _supports_color(stream):
            painted = cycle_text(row, stream,
                                 LOGO_PHASE + index * LOGO_ROW_SPREAD,
                                 spread=LOGO_SPREAD)
        body.append(_pad_left(row, width) + painted)
    if logo:
        body.append("")
        body.append("")
    for index, text in enumerate(state.lines(moment, width)):
        text = fit_to_width(text, width)
        painted = (paint_subtitle(text, stream, state.state, moment, phase)
                   if index == 0 else _dim(text, stream))
        body.append(_pad_left(text, width) + painted)

    above = max(0, (height - len(body)) // 2)
    # The optical centre, not the geometric one: a third of the slack above
    # and the rest below puts the block where the eye expects to find it.
    above = max(0, int(above * 0.8))
    frame = [""] * above + body
    frame.extend([""] * max(0, height - len(frame)))
    return frame[:height]


def _pad_left(text, width):
    """The centring margin for `text`, as spaces, so the painted form can follow.

    Kept apart from `_centre` because a painted string carries escapes and
    measuring those would push the row off centre by however many bytes the
    colour happened to cost.
    """
    return " " * max(0, (int(width) - display_width(text)) // 2)


def _dim(text, stream):
    return DIM + text + RESET if _supports_color(stream) else text


def _terminal(size=None):
    """Columns and rows, re-read per frame so a resize is picked up.

    Read on every frame rather than once at the start, which is the whole of
    the resize handling: the frame is rebuilt from the current size on every
    tick, so a window that changes shape is simply drawn again at the new one.
    There is nothing to invalidate and nothing that can go stale.
    """
    if size is not None:
        columns, rows = size
        return max(1, int(columns)), max(1, int(rows))
    try:
        import shutil
        measured = shutil.get_terminal_size((80, 24))
        return max(1, measured.columns), max(1, measured.lines)
    except Exception:
        return 80, 24


# How long a settled state stays on screen before the application takes over,
# in seconds. Long enough to read "Up to date." and short enough that nobody
# waits for it. It is spent in the same paint-and-poll loop the waiting state
# uses rather than in a sleep, so Ctrl-C still works during it and the screen
# is still being redrawn -- a frozen frame for three quarters of a second
# reads as a hang, which is the opposite of what this row is for.
SETTLED_DWELL = 0.75

# The same, for a state the user has to actually read: a refusal, or a
# failure. Longer, because it carries a detail line under it and because it is
# the one outcome somebody might want to act on later.
NOTICE_DWELL = 1.6


def _menu():
    """agent_menu, imported at call time.

    The direction is `agent_splash` -> `agent_menu`, so nothing here closes a
    cycle; it is imported inside the call for the reason `agent_panel._menu`
    is, which is that an editable install freezes its module list and a module
    that cannot be imported must not be what stops TMT starting.
    """
    import agent_menu
    return agent_menu


def _updater():
    """agent_update, or None when it cannot be reached.

    None is a working answer: it means this launch cannot check for updates,
    which is exactly the situation a checkout with no git in it is already in,
    and the screen has a state for it.
    """
    try:
        import agent_update
        return agent_update
    except Exception:
        return None


def auto_update_enabled():
    """Whether `Auto Update on Launch` is on, defaulting to on.

    Read through `agent_config`, which owns every stored setting in TMT, and
    guarded to True: a configuration file that cannot be read must not be able
    to silently disable a feature, and the default is the documented one.
    """
    try:
        import agent_config
        return bool(agent_config.read_saved_auto_update())
    except Exception:
        return True


def run_splash(stream=None, key_reader=None, region=None, updater=None,
               auto_update=None, restart=None, clock=None, root=None):
    """Show the launch screen, run the update stage, and say what happens next.

    Returns "start" to continue into TMT or "exit" to stop. It may also not
    return at all: a successful update restarts the process, which is the one
    path out of here that is not a return value.

    **It returns "start" without drawing anything when the terminal cannot be
    driven**, and that check is first and unconditional, exactly as
    `agent_menu.run_startup` does it. That single line is what keeps every
    piped run, every script and the whole test suite behaving as they did
    before this screen existed -- and it is why a scripted caller drives the
    pieces (`SplashState`, `render_splash_frame`, `run_update_stage`) rather
    than this function.

    Everything the update needs is injectable so a test needs no terminal, no
    network and no git: `updater` is the module, `auto_update` the setting,
    `restart` the thing that replaces the process, `clock` the passage of
    time.
    """
    menu = _menu()
    stream = sys.stdout if stream is None else stream
    if not menu.is_interactive(stream):
        return "start"
    if key_reader is None:
        key_reader = menu._default_reader()
    region = menu.LiveRegion(stream) if region is None else region
    clock = time.monotonic if clock is None else clock
    state = SplashState()
    try:
        menu._hide_cursor(stream)
        # The screen is a screen: it starts at the top of one rather than
        # halfway down whatever the shell last printed. `clear_screen` erases
        # the viewport and nothing else -- the scrollback above it belongs to
        # the user's shell session and is never TMT's to throw away, which is
        # also why the alternate screen buffer is not used here.
        menu.clear_screen(stream)
        if not _wait_for_enter(state, stream, key_reader, region, clock):
            return "exit"
        run_update_stage(state, stream=stream, region=region, clock=clock,
                         key_reader=key_reader, updater=updater,
                         auto_update=auto_update, restart=restart, root=root)
        return "start"
    except KeyboardInterrupt:
        # Ctrl-C anywhere on this screen closes TMT rather than dropping into
        # it. The user interrupted the launch, and the honest reading of that
        # is that they did not want to launch.
        return "exit"
    finally:
        # Whatever happened, including an exception on the way out: the region
        # is taken down, the caret comes back, and the terminal is out of raw
        # mode before anything else tries to read a key. A splash that left
        # the terminal in raw mode would make the shell it returned to
        # unusable, which is the worst thing on this screen's list.
        try:
            region.clear()
        finally:
            menu._restore_terminal(stream)
            menu.clear_screen(stream)


def _wait_for_enter(state, stream, key_reader, region, clock):
    """Paint and poll until Enter. True to go on, False to leave.

    The animation and the input are the same loop, which is what makes the
    subtitle able to move without a timer thread: the reader is given a
    timeout, so it returns "" on every tick that had no key in it and the
    frame is simply drawn again. Nothing sleeps and nothing is scheduled.

    Only Enter proceeds and only Ctrl-C leaves. Esc and q -- which are "back"
    and "quit" on every menu in TMT -- are ignored here on purpose: a splash
    is not a menu, there is nothing behind it to go back to, and the screen
    names the one key it wants. Ignoring the rest is also what stops a user
    who starts typing their first task before the screen has settled from
    activating something they did not mean to.
    """
    menu = _menu()
    while True:
        region.paint(render_splash_frame(state, stream, moment=clock()))
        key = menu._next_key(key_reader)
        if key == "enter":
            return True
        if key == "interrupt":
            return False
        if key is None:
            # An exhausted reader. It cannot be waited on again, so returning
            # here is the only thing that does not loop forever -- and a
            # scripted caller that ran out of keys did not ask to start.
            return False


def run_update_stage(state, stream=None, region=None, clock=None,
                     key_reader=None, updater=None, auto_update=None,
                     restart=None, root=None):
    """Everything after Enter: check, report, and restart if one was applied.

    Returns the `SplashState` it left the screen in, so a caller that is not a
    terminal -- a test -- can drive the whole sequence and read the outcome
    without a region, a reader or a clock that moves.

    The update runs on its own thread and the screen keeps being painted while
    it does, which is the only reason "Searching for updates" can animate at
    all: a `git fetch` is a subprocess that blocks for as long as the network
    takes, and running it on this thread would freeze the frame for the whole
    of it. The thread is a daemon and is joined before this returns, so
    nothing is left running behind the application.
    """
    stream = sys.stdout if stream is None else stream
    clock = time.monotonic if clock is None else clock
    enabled = auto_update_enabled() if auto_update is None else bool(auto_update)
    if not enabled:
        # Nothing was checked, and the screen says so by saying nothing about
        # it. Showing "Searching for updates" here would be a claim about work
        # that did not happen, on the one screen whose whole job is to report
        # what is going on.
        state.advance(SKIPPED)
        _dwell(state, stream, region, clock, SETTLED_DWELL, key_reader)
        return state.finish()

    module = _updater() if updater is None else updater
    if module is None:
        state.advance(BLOCKED, "Update checking is unavailable here.")
        _dwell(state, stream, region, clock, NOTICE_DWELL, key_reader)
        return state.finish()

    state.advance(CHECKING)
    result = _run_check(module, root, state, stream, region, clock, key_reader)
    if result is None:
        # Interrupted. The git call it was in has its own timeout and both of
        # the operations used here -- fetch, and a fast-forward merge -- are
        # atomic from git's side, so there is no half-finished state to clean
        # up; what there is, is a user who pressed Ctrl-C, and the honest
        # answer to that is to stop.
        raise KeyboardInterrupt
    mapped, detail = state_for_update(result, module)
    state.advance(mapped, detail, result=result)
    if mapped == UPDATED:
        _dwell(state, stream, region, clock, SETTLED_DWELL, key_reader)
        _restart(module, restart)
        # Only reachable when the restart was injected and returned, which is
        # what a test does. A real one replaces the process.
        return state.finish()
    _dwell(state, stream, region, clock,
           NOTICE_DWELL if mapped in (BLOCKED, FAILED) else SETTLED_DWELL,
           key_reader)
    return state.finish()


def _run_check(module, root, state, stream, region, clock, key_reader):
    """Run the update on a worker thread, painting until it lands.

    None when the user interrupted. The result object otherwise -- including
    for every kind of failure, because `agent_update.check_and_update` does
    not raise: a launch screen that could be taken down by a network error
    would be a launch screen that stops TMT launching.
    """
    import threading
    outcome = {}

    def work():
        try:
            outcome["result"] = module.check_and_update(root=root)
        except Exception as error:                      # pragma: no cover
            # Belt and braces over a function documented not to raise. An
            # updater that found a way to would otherwise leave this loop
            # painting forever.
            outcome["result"] = _failure(module, error)

    thread = threading.Thread(target=work, name="tmt-update", daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            _paint(state, stream, region, clock)
            if _tick(key_reader):
                # Ctrl-C, on either platform: the POSIX signal arrives as the
                # exception below, and the Windows console's "" arrives
                # here as a normalised key.
                return None
    except KeyboardInterrupt:
        return None
    thread.join(timeout=1.0)
    return outcome.get("result") or _failure(module, "the update did not report")


def _failure(module, detail):
    """An ERROR result built by hand, for the paths the updater never reached."""
    try:
        return module.UpdateResult(module.ERROR, "Update check failed.",
                                   str(detail))
    except Exception:
        return None


def _restart(module, restart=None):
    """Replace this process with a fresh one running the code just pulled."""
    if restart is not None:
        return restart()
    return module.restart()


def _paint(state, stream, region, clock):
    if region is not None:
        region.paint(render_splash_frame(state, stream, moment=clock()))


def _tick(key_reader):
    """One animation tick. True when the user asked to stop.

    The reader's own timeout is the frame rate, so there is no sleep here and
    no timer. A `None` from an exhausted reader is not an exit on this path --
    the update is already running and abandoning the screen would leave it
    unreported -- so it is simply the tick passing.

    **The key is normalised rather than discarded, and that is the whole
    reason this is not two lines.** On POSIX a Ctrl-C during a read raises,
    and every loop in TMT would see it. On Windows it does not: `msvcrt` hands
    back `"\\x03"` as an ordinary character and no signal is ever raised -- the
    same fact `TypeAhead` exists to work around. A tick that threw its key
    away would therefore swallow Ctrl-C for the whole of an update check,
    which is exactly the window a user most wants to be able to interrupt,
    and only on the platform where they cannot fall back on the signal.
    """
    menu = _menu()
    if key_reader is None:
        time.sleep(SPLASH_TICK)
        return False
    try:
        return menu._next_key(key_reader) == "interrupt"
    except (StopIteration, IndexError):
        time.sleep(SPLASH_TICK)
        return False


def _dwell(state, stream, region, clock, seconds, key_reader=None):
    """Hold a settled frame on screen long enough to be read.

    Spent in the paint-and-poll loop rather than in `time.sleep`, so the
    screen is still being drawn and Ctrl-C still works. With no region and no
    reader -- which is a test -- it returns at once: there is nothing to look
    at and nothing to wait for.
    """
    if region is None:
        return
    deadline = clock() + max(0.0, float(seconds))
    while clock() < deadline:
        _paint(state, stream, region, clock)
        if _tick(key_reader):
            # Ctrl-C while a settled frame is being held. It is still an
            # interrupt, and it still means stop -- the alternative is a
            # screen that ignores the user for a second and a half.
            raise KeyboardInterrupt


def state_for_update(result, module=None):
    """(state, detail) for what the updater came back with.

    The one place the updater's vocabulary and the screen's are mapped onto
    each other, and they are separate vocabularies on purpose: `agent_update`
    answers "what is true of this checkout" and this answers "what should the
    person watching be told", which is a smaller set. Anything unrecognised is
    FAILED rather than a guess -- a status this screen has never heard of is
    not one it can honestly describe, and FAILED continues into TMT, which is
    the safe direction.

    `module` is the updater the statuses came FROM, and it has to be, because
    the caller may have been handed one. Reading the real `agent_update`'s
    constants to interpret a different module's result would map every status
    to FAILED and report a clean check as a failure -- which is exactly what
    it did until a test drove an injected updater through it.
    """
    if module is None:
        module = _updater()
    if module is None:
        return FAILED, ""
    agent_update = module
    status = getattr(result, "status", None)
    detail = str(getattr(result, "headline", "") or "")
    mapping = {
        agent_update.CURRENT: CURRENT,
        agent_update.UPDATED: UPDATED,
        agent_update.BLOCKED_DIRTY: BLOCKED,
        agent_update.BLOCKED_DIVERGED: BLOCKED,
        agent_update.NO_UPSTREAM: BLOCKED,
        agent_update.NOT_A_REPO: BLOCKED,
        # BLOCKED and deliberately not SKIPPED, which reads "nothing was
        # checked". The updater says DISABLED when it looked, found an update,
        # and was not allowed to apply it -- which in practice is the restart
        # guard on the second process of a launch. Something WAS checked, and
        # "Continuing without updating" is what actually happened; "Starting"
        # would throw the finding away. The user turning the setting off never
        # reaches here at all: `run_update_stage` answers that itself, before
        # the updater is called.
        agent_update.DISABLED: BLOCKED,
        agent_update.ERROR: FAILED,
    }
    # `AVAILABLE` is the updater's answer to "what does this comparison mean"
    # rather than to "what happened", and `check_and_update` does not return
    # it -- it either applies the update or says why it may not. Mapped anyway,
    # and to BLOCKED, because if one ever did arrive here the honest reading is
    # "an update exists and was not taken", which is what BLOCKED says.
    # `getattr`, so a module that drops the constant does not take the screen
    # with it.
    available = getattr(agent_update, "AVAILABLE", None)
    if available is not None:
        mapping[available] = BLOCKED
    state = mapping.get(status, FAILED)
    # A settled state's own sentence already says what happened; the detail
    # under it says why, and repeating the headline there would draw the same
    # fact twice on two rows.
    if state in (CURRENT, UPDATED):
        detail = ""
    return state, detail
