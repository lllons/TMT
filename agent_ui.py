"""Live terminal UI for model request lifecycle."""

import os
import random
import re
import shutil
import sys
import threading
import time
import unicodedata
from typing import Optional


_THINKING_STYLES = [
    "THINKING", "𝕋ℍ𝕀ℕ𝕂𝕀ℕ𝔾", "𝓣𝓗𝓘𝓝𝓚𝓘𝓝𝓖", "𝙏𝙃𝙄𝙉𝙆𝙄𝙉𝙂", "ＴＨＩＮＫＩＮＧ",
    "𝗧𝗛𝗜𝗡𝗞𝗜𝗡𝗚", "𝘛𝘏𝘐𝘕𝘒𝘐𝘕𝘎", "𝚃𝙷𝙸𝙽𝙺𝙸𝙽𝙶", "ᴛʜɪɴᴋɪɴɢ", "ＴＨＩＮＫＩＮＧ",
    "T H I N K I N G", "T·H·I·N·K·I·N·G", "T•H•I•N•K•I•N•G", "T-H-I-N-K-I-N-G",
    "T_H_I_N_K_I_N_G", "[ THINKING ]", "< THINKING >", "⟦ THINKING ⟧", "✦ THINKING ✦",
    "✧ THINKING ✧", "◆ THINKING ◆", "◇ THINKING ◇", "◈ THINKING ◈", "» THINKING «",
    "« THINKING »", "── THINKING ──", "━━ THINKING ━━", "▸ THINKING", "◂ THINKING",
    "⟶ THINKING", "⟵ THINKING", "☼ THINKING", "✺ THINKING", "✹ THINKING", "✷ THINKING",
    "T̲H̲I̲N̲K̲I̲N̲G̲", "T̶H̶I̶N̶K̶I̶N̶G̶", "T̳H̳I̳N̳K̳I̳N̳G̳", "T͟H͟I͟N͟K͟I͟N͟G͟",
    "THINKING...", "THINKING ···", "THINKING /", "THINKING \\",
]

# The colour cycle every animated surface shares: red -> orange -> green and
# back again, repeating for as long as the surface is on screen.
GRADIENT_CYCLE = 2.4     # seconds for one full there-and-back pass
GRADIENT_TICK = 0.08     # repaint period while a surface is animating
DIM = "\033[38;2;88;88;88m"
RESET = "\033[0m"

# Weight and rule, for the one case where something must stand out on a
# terminal that has ANSI but no colour -- NO_COLOR set, or a palette the user
# has deliberately turned off. They are not part of the gradient and they are
# not an alternative to it: the only caller uses them as the fallback for a
# surface whose colour it has just been refused, so that what the colour was
# distinguishing is still distinguished. Everything here still reads correctly
# with every escape stripped, which is the rule these must not become an
# exception to.
BOLD = "\033[1m"
UNDERLINE = "\033[4m"

# The activity readout drawn hard against the right edge of the status row,
# opposite the progress bar: a turning glyph, elapsed time and token count.
ACTIVITY_GLYPHS = ("✻", "✽", "✻")
ACTIVITY_TICK = 0.4      # seconds each glyph is held
ACTIVITY_GAP = 2         # minimum blank columns kept between the two halves
CHARS_PER_TOKEN = 4      # token size assumed until the provider reports its own

_ZWJ = "‍"
_VARIATION_SELECTOR_16 = "️"


def display_width(text):
    """Terminal columns occupied by text, treating wide characters as two."""
    width = 0
    for char in text:
        if unicodedata.combining(char) or char == _ZWJ:
            continue
        if char == _VARIATION_SELECTOR_16:
            width += 1
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


_SGR_RE = re.compile("\033\\[[0-9;?]*[A-Za-z]")


def strip_ansi(text):
    """The text a reader sees, with every escape sequence removed."""
    return _SGR_RE.sub("", text or "")


def visible_width(text):
    """Terminal columns occupied by painted text.

    `display_width` measures characters, and an escape sequence is made of
    characters that occupy no columns at all. Anything that has already been
    painted has to be measured through this or it comes out several times too
    wide, which for a right-aligned readout means it starts off the screen.
    """
    return display_width(strip_ansi(text))


def fit_to_width(text, columns):
    """Trim text to at most `columns` terminal columns.

    Measured rather than counted, so a full-width or combining style cannot
    overflow the row and wrap onto a second screen line.
    """
    if display_width(text) <= columns:
        return text
    kept, used = [], 0
    for char in text:
        size = display_width(char)
        # Zero-width marks never reach the break, so they stay attached to the
        # base character rather than being orphaned onto a space.
        if used + size > columns:
            break
        kept.append(char)
        used += size
    return "".join(kept)


# Grapheme-ish clustering: keeps combining marks, variation selectors, ZWJ
# sequences and flag pairs attached to their base character.
_MARKS = "[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︀-️︠-︯]"
_GRAPHEME_RE = re.compile(
    "\r\n|[\U0001F1E6-\U0001F1FF]{2}|"
    "(?:.%s*(?:%s.%s*)*)" % (_MARKS, _ZWJ, _MARKS),
    re.DOTALL,
)


def iter_graphemes(text):
    """Split text into user-perceived characters without breaking Unicode."""
    return [match.group() for match in _GRAPHEME_RE.finditer(text)] if text else []


def pad_to_width(text, columns):
    return text + " " * max(0, columns - display_width(text))


def clip_to_width(text, columns):
    """Return (head, rest) where head fits within `columns` columns."""
    used, index = 0, 0
    for grapheme in iter_graphemes(text):
        size = display_width(grapheme)
        if used + size > columns:
            break
        used += size
        index += len(grapheme)
    if index == 0 and text:
        index = len(iter_graphemes(text)[0])
    return text[:index], text[index:]


def wrap_lines(text, columns):
    lines = []
    for raw in text.split("\n"):
        raw = raw.replace("\t", "    ").replace("\r", "")
        while display_width(raw) > columns:
            head, raw = clip_to_width(raw, columns)
            lines.append(head)
        lines.append(raw)
    return lines


def activity_glyph(now=None, plain=False):
    """The glyph standing in for a spinner, advanced by the wall clock."""
    if plain:
        return "*"
    now = time.monotonic() if now is None else now
    return ACTIVITY_GLYPHS[int(now / ACTIVITY_TICK) % len(ACTIVITY_GLYPHS)]


def thinking_styles(stream):
    """The THINKING styles this stream can actually draw.

    Most are decorative Unicode. Plain "THINKING" always survives the filter,
    so the animation degrades instead of emptying.
    """
    return [style for style in _THINKING_STYLES if encodable(stream, style)] or ["THINKING"]


def _supports_color(stream) -> bool:
    return bool(getattr(stream, "isatty", lambda: False)()) and not os.environ.get("NO_COLOR")


def encodable(stream, text) -> bool:
    """Whether the stream's encoding can carry every character in text."""
    encoding = getattr(stream, "encoding", None) or ""
    if not encoding:
        # No declared encoding means this is not a byte sink -- an in-memory
        # buffer, say -- so nothing can fail to encode on the way out.
        return True
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


# Every decorative glyph the interface draws, asked once. A stream that cannot
# carry them gets a deliberate ASCII interface rather than a row of
# replacement marks -- or, before this was checked at all, a crash.
DECORATION = "█░✳✻✽…·↓┌┐└┘│─『』"


def plain_output(stream) -> bool:
    """Whether this stream needs the ASCII fallbacks."""
    return not encodable(stream, DECORATION)


def safe_write(stream, text) -> bool:
    """Write text, degrading only the characters the stream cannot encode.

    A terminal that cannot represent a glyph must not end the run: the task
    matters more than its decoration. Returns False when the stream is gone,
    so a caller can stop drawing to it.
    """
    try:
        stream.write(text)
        stream.flush()
        return True
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        stream.write(text.encode(encoding, "replace").decode(encoding, "replace"))
        stream.flush()
        return True
    except (ValueError, OSError):
        return False


def _gradient(progress: int):
    stops = [(10, (220, 38, 38)), (40, (249, 115, 22)), (60, (234, 179, 8)), (80, (132, 204, 22)), (95, (34, 197, 94)), (100, (74, 222, 128))]
    progress = max(10, min(100, progress))
    for (p1, c1), (p2, c2) in zip(stops, stops[1:]):
        if progress <= p2:
            ratio = (progress - p1) / (p2 - p1)
            return tuple(round(a + (b - a) * ratio) for a, b in zip(c1, c2))
    return stops[-1][1]


def gradient_at(ratio: float):
    """Colour at a point in the repeating cycle.

    The cycle travels red -> green over the first half and back over the
    second, so it can loop forever without snapping from green to red.
    """
    ratio %= 1.0
    travel = ratio * 2 if ratio <= 0.5 else (1.0 - ratio) * 2
    return _gradient(round(10 + 90 * travel))


def gradient_phase(now=None) -> float:
    """Where the shared colour cycle stands right now, in [0, 1)."""
    now = time.monotonic() if now is None else now
    return (now % GRADIENT_CYCLE) / GRADIENT_CYCLE


def _color(text: str, progress: int, stream) -> str:
    if not _supports_color(stream):
        return text
    r, g, b = _gradient(progress)
    return f"\033[38;2;{r};{g};{b}m{text}{RESET}"


def cycle_text(text: str, stream, phase=None, spread: float = 1.0) -> str:
    """Paint text across the gradient, offset by the running colour cycle."""
    if not _supports_color(stream) or not text:
        return text
    phase = gradient_phase() if phase is None else phase
    span = max(1, len(text) - 1)
    parts = []
    for index, char in enumerate(text):
        r, g, b = gradient_at(phase + spread * index / span)
        parts.append(f"\033[38;2;{r};{g};{b}m{char}")
    parts.append(RESET)
    return "".join(parts)


def cycle_bar(progress: int, stream, width: int = 12, plain: bool = False, phase=None, spread: float = 0.6) -> str:
    """A progress bar whose filled cells ride the same repeating cycle."""
    full, empty = ("#", "-") if plain else ("█", "░")
    filled = round(width * progress / 100)
    if not _supports_color(stream):
        return full * filled + empty * (width - filled)
    phase = gradient_phase() if phase is None else phase
    span = max(1, width - 1)
    cells = []
    for index in range(width):
        if index < filled:
            r, g, b = gradient_at(phase + spread * index / span)
            cells.append(f"\033[38;2;{r};{g};{b}m{full}")
        else:
            cells.append(DIM + empty)
    cells.append(RESET)
    return "".join(cells)


class LiveUI:
    """Single-line lifecycle display; model execution remains on the caller thread."""

    def __init__(self, stream=None, interval: float = 1.0):
        self.stream = stream or sys.stdout
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._active = False
        self._progress_started = False
        self._progress = 0
        self._events = 0
        self._started_at = 0.0
        self._last_render = ""
        self._label = "Just Started!"
        self._estimate = "calculating..."
        self._settled_tokens = 0
        self._pending_chars = 0
        self._sink = None

    def attach_sink(self, sink):
        """Route the status line through a renderer (the live relay) instead
        of writing it directly, so both share one terminal region."""
        self._sink = sink

    def add_output(self, characters):
        """Add generated characters to the running total.

        Everything the model produces counts: the JSON that carries an action,
        the paths and file contents inside it, and the reply the user reads.
        Totals run across the whole task rather than per request, so the figure
        keeps climbing through the agent loop instead of restarting on each
        call. The animation thread repaints often enough to show it.
        """
        with self._lock:
            self._pending_chars += characters

    def settle_tokens(self, tokens):
        """Replace the estimate for the request that just finished with the
        provider's own count, which is exact.

        Earlier requests keep their settled figures, so a task mixing replies
        that report usage with replies that do not stays correct rather than
        being rebased on the last number to arrive.
        """
        with self._lock:
            self._settled_tokens += tokens
            self._pending_chars = 0

    def _token_total(self):
        """Settled counts plus an estimate for output not yet accounted for.

        The caller holds the lock.
        """
        return self._settled_tokens + round(self._pending_chars / CHARS_PER_TOKEN)

    @property
    def progress_started(self):
        return self._progress_started

    def start(self):
        with self._lock:
            self._active = True
            self._progress_started = False
            self._progress = 0
            self._events = 0
            self._settled_tokens = 0
            self._pending_chars = 0
            self._started_at = time.monotonic()
        self._render("THINKING", painter=self._paint_cycle)
        self._thread = threading.Thread(target=self._animate, name="tmt-thinking", daemon=True)
        self._thread.start()

    def _paint_cycle(self, text):
        return cycle_text(text, self.stream)

    def _activity(self):
        """The right-hand readout: turning glyph, elapsed time, tokens seen.

        Empty until the progress bar takes the row, and empty again once the
        row is finished. It is the bar's counterpart, so it keeps the bar's
        company: beside the THINKING animation it would only say twice, in
        two places, what that word already says.
        """
        with self._lock:
            if not self._progress_started or self._progress >= 100:
                return ""
            started_at, tokens = self._started_at, self._token_total()
        elapsed = max(0, round(time.monotonic() - started_at))
        plain = plain_output(self.stream)
        separator, arrow, trail = ("-", "", "...") if plain else ("·", "↓ ", "…")
        detail = f"{elapsed}s" if not tokens else f"{elapsed}s {separator} {arrow}{tokens} tokens"
        return f"{activity_glyph(plain=plain)} thinking{trail} ({detail})"

    def _paint_activity(self, text):
        """Glyph and word ride the shared cycle; the detail stays dim."""
        head, separator, tail = text.partition(" (")
        if not separator:
            return cycle_text(text, self.stream)
        detail = separator + tail
        if not _supports_color(self.stream):
            return head + detail
        return cycle_text(head, self.stream) + DIM + detail + RESET

    def _animate(self):
        """Keep the colour cycle turning for as long as the line is on screen.

        The THINKING word is swapped every `interval`; the gradient underneath
        it — and under the progress bar once it appears — repaints far more
        often, so the colour never settles.
        """
        tick = min(self.interval, GRADIENT_TICK)
        styles = thinking_styles(self.stream)
        word = "THINKING"
        next_word = time.monotonic() + self.interval
        while not self._stop.wait(tick):
            with self._lock:
                if not self._active:
                    return
                started, label, estimate = self._progress_started, self._label, self._estimate
            if started:
                self._render_progress(label, estimate)
                continue
            if time.monotonic() >= next_word:
                word = random.choice(styles)
                next_word = time.monotonic() + self.interval
            self._render(word, painter=self._paint_cycle)

    def meaningful_output(self):
        with self._lock:
            if not self._active or self._progress_started:
                return
            self._progress_started = True
            self._progress = 10
        self._render_progress("Just Started!")

    def intermediate_event(self, label="Processing..."):
        with self._lock:
            if not self._active:
                return
            if not self._progress_started:
                self._progress_started = True
                self._progress = 10
            self._events += 1
            # Event-driven growth with diminishing increments; never reaches 100.
            self._progress = min(90, max(10, self._progress + max(5, 14 - self._events)))
        self._render_progress(label)

    def final_event(self):
        with self._lock:
            if not self._active:
                return
            self._progress_started = True
            self._progress = 95
        self._render_progress("FINALIZING", estimate="completing...")

    def complete(self):
        # The animation stops before the final line is drawn, not after: while
        # it runs it repaints the same finished row a moment later, which
        # doubles it on any stream without a real cursor to overwrite.
        with self._lock:
            if not self._active:
                return
            self._progress = 100
        self._halt_animation()
        self._render_progress("Complete!", estimate=None)
        time.sleep(0.05)
        self.stop(clear=True)

    def _halt_animation(self):
        """Stop the animation thread, leaving the line it drew on screen."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1)

    def stop(self, clear=True):
        with self._lock:
            self._active = False
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
        if clear:
            self._clear()

    def _render(self, text, painter=None, optional=""):
        """Render one status row: `text` on the left, the activity readout
        against the right edge.

        `optional` is a lower-priority tail of the left half — the finish
        estimate — which is given up first when the row cannot hold
        everything, because the readout already carries the elapsed time.
        Only if the row still will not fit is the readout dropped, and then
        whole rather than truncated to a fragment.

        Both halves are trimmed and measured in terminal columns before they
        are painted, so an escape sequence is never cut in half, the row can
        never overflow onto a second screen line, and `_last_render` stays
        readable text.
        """
        with self._lock:
            if not self._active:
                return
        budget = max(1, shutil.get_terminal_size((80, 24)).columns - 1)
        right = self._activity()
        for left in ((text + optional, text) if optional else (text,)):
            left = fit_to_width(left, budget)
            gap = budget - display_width(left) - display_width(right)
            if right and gap >= ACTIVITY_GAP:
                self._last_render = left + " " * gap + right
                self._write_row((painter(left) if painter else left)
                                + " " * gap + self._paint_activity(right))
                return
        left = fit_to_width(text + optional, budget)
        self._last_render = left
        self._write_row(painter(left) if painter else left)

    def _write_row(self, painted):
        if self._sink is not None:
            self._sink(painted)
            return
        # The escape goes to a stream that may not be a terminal. That is
        # deliberate rather than overlooked: this path is what a LiveUI with no
        # sink attached does, and the tests around it exist precisely to pin
        # down what happens "wherever no cursor could overwrite". A session
        # never takes it -- the loop attaches the relay's sink before the first
        # request -- so the row a pipe sees comes from LiveRegion, which does
        # check. Suppressing it here silences the case the tests are about.
        safe_write(self.stream, "\r\033[2K" + painted)

    def _render_progress(self, label, estimate="calculating..."):
        with self._lock:
            if not self._active:
                return
            progress = self._progress
            self._label, self._estimate = label, estimate
        width = min(12, max(4, shutil.get_terminal_size((80, 24)).columns // 8))
        plain = plain_output(self.stream)
        full, empty = ("#", "-") if plain else ("█", "░")
        filled = round(width * progress / 100)
        bar = full * filled + empty * (width - filled)
        line = f"{bar} {progress:>3}% {label}"
        detail = ""
        if estimate and progress < 100:
            elapsed = max(0.01, time.monotonic() - self._started_at)
            estimate_text = estimate if progress >= 95 else f"~{max(1, round(elapsed * (100 - progress) / max(1, progress)))} seconds"
            detail = f" | Estimated finish: {estimate_text}"

        def paint(text, bar_width=len(bar)):
            return cycle_bar(progress, self.stream, width=bar_width, plain=plain) + text[bar_width:]

        self._render(line, painter=paint, optional=detail)

    def _clear(self):
        if self._sink is not None:
            self._sink("")
            return
        safe_write(self.stream, "\r\033[2K")


def render_response(response: str, stream=None):
    """Draw the finished reply in a box sized to the terminal.

    Wrapped and padded by display width rather than character count, so a
    reply carrying wide or combining characters cannot push a row past the
    edge and wrap it onto a second line. One column is left spare because
    terminals disagree about what a row filled to the last column does.

    The box is undecorated. It is the one thing on screen the user is there
    to read, and the gradient's job is to mark what is alive or measuring --
    the bar, the thinking word, the wordmark. Colouring the border of the
    answer as well made the answer look like another instrument, and the
    border is structure the reader does not need drawn to.
    """
    stream = stream or sys.stdout
    width = max(20, shutil.get_terminal_size((80, 24)).columns)
    plain = plain_output(stream)
    edge, rule = ("|", "-") if plain else ("│", "─")
    inner = max(10, width - 5)
    lines = wrap_lines(str(response), inner) or [""]
    fill = rule * (inner + 2)
    if plain:
        border = bottom = "+" + fill + "+"
    else:
        border, bottom = "┌" + fill + "┐", "└" + fill + "┘"
    out = [border]
    out.extend(f"{edge} {pad_to_width(line, inner)} {edge}" for line in lines)
    out.append(bottom)
    safe_write(stream, "\n".join(out) + "\n")


# --- the response history -----------------------------------------------
#
# Everything above this point draws a row that is repainted in place: one
# mutable status line, erased and rewritten as the turn moves on. That is the
# right shape for a progress bar and the wrong shape for anything the user is
# meant to keep. A message that says what the agent just did has to survive the
# next message, and until now none did -- `_write_row` writes "\r\033[2K", which
# is a carriage return and an erase, so each intermediate label overwrote the
# one before it and the turn ended with nothing but its last line.
#
# So there are two surfaces, and the difference between them is the whole
# design. An AgentEvent is permanent: appended to a ResponseHistory, printed
# once into the terminal's own scrollback, never repainted and never erased.
# The status row stays temporary. Nothing below writes to the status row and
# nothing above appends to the history.

EVENT_KINDS = ("progress", "milestone", "warning", "success", "error", "tool",
               "file_read", "file_edit", "file_create", "file_delete", "command",
               "test", "background_agent", "next_step_suggestion", "final")

# Where each kind sits in the shared gradient, and how much of the screen it is
# allowed to take. Position carries the meaning, exactly as it does on the
# progress bar: red is trouble, green is finished, and the one neutral is for
# detail that must recede. There is no second colour system.
#
# The level is prominence, 0 lowest to 3 highest. A terminal cannot size type
# independently, so weight is carried by what it does have: dimness, indent,
# the blank line before a block, and how loud the marker is.
_EVENT_STYLE = {
    "progress":           {"level": 0, "position": None, "mark": ("·", "-")},
    "next_step_suggestion": {"level": 0, "position": None, "mark": ("", "")},
    "tool":               {"level": 1, "position": 40, "mark": ("▸", ">")},
    "file_read":          {"level": 1, "position": 40, "mark": ("▸", ">")},
    "background_agent":   {"level": 1, "position": 40, "mark": ("▸", ">")},
    "milestone":          {"level": 1, "position": 40, "mark": ("◆", "*")},
    "warning":            {"level": 1, "position": 60, "mark": ("▲", "!")},
    "error":              {"level": 1, "position": 10, "mark": ("✗", "x")},
    "success":            {"level": 1, "position": 95, "mark": ("✓", "+")},
    "command":            {"level": 2, "position": 40, "mark": ("▸", ">")},
    "file_edit":          {"level": 2, "position": 80, "mark": ("▸", ">")},
    "file_create":        {"level": 2, "position": 80, "mark": ("▸", ">")},
    "file_delete":        {"level": 2, "position": 80, "mark": ("▸", ">")},
    "test":               {"level": 2, "position": 80, "mark": ("▸", ">")},
    "final":              {"level": 3, "position": None, "mark": ("", "")},
}

PROMINENCE = {kind: _EVENT_STYLE[kind]["level"] for kind in EVENT_KINDS}

# Two numbers, and the gap between them is deliberate.
#
# TARGET is what the prompt asks the model for and what every example in it
# obeys: four words. MAX is what the code lets through: six. Models kept
# overshooting a single stated limit, and a limit that is also the truncation
# point means every overshoot is cut mid-phrase -- "Run the integration tests
# for" reads worse than the five-word line it was. So the ask is strict and
# the ceiling above it is slack: aim at four, and a five or a six arrives
# whole rather than beheaded. Seven is where it stops being a hint.
TARGET_SUGGESTION_WORDS = 4
MAX_SUGGESTION_WORDS = 6

# Used only when the model gave no usable suggestion and the history says
# nothing more specific. Each is true of any turn, so none of them can claim
# something that did not happen.
FALLBACK_SUGGESTIONS = ("Review the changes", "Run the tests", "Continue working")

# What the first prompt of a session shows, before there is a turn to read a
# hint off. It is a placeholder like every other one: drawn dim in an empty
# box, never assigned to the buffer, and gone on the first character typed.
# Measured by the same rule as the rest -- five words at most -- so the
# opening line and every line after it read as the same kind of thing.
OPENING_SUGGESTION = "Describe your first task"

# Drawn in the box while the turn it asked for is running. The box is on
# screen then -- pinned above the status row at the foot of the window -- but
# nothing typed into it would be read, so it says what is happening and the
# one thing the user can do about it rather than sitting there looking ready.
RUNNING_HINT = "Working. Ctrl-C to stop."

_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)


def count_words(text):
    """Words in `text`, counting neither punctuation nor whitespace.

    "Run the tests." is three. Hyphenated and apostrophed forms are one word
    each, because a suggestion is measured the way a reader would measure it
    rather than the way a tokeniser would.
    """
    if not isinstance(text, str):
        return 0
    return len(_WORD.findall(text))


def validate_suggestion(text):
    """Check a next-step suggestion. Returns (ok, cleaned, reason).

    `cleaned` is usable whatever `ok` says: an over-long suggestion comes back
    truncated to the first MAX_SUGGESTION_WORDS words, so a caller with no way
    to ask for a shorter one still has something correct to show rather than
    having to choose between a wrong length and nothing at all.
    """
    if not isinstance(text, str):
        return False, "", "A suggestion must be text."
    cleaned = " ".join(text.split())
    if not cleaned:
        return False, "", "The suggestion was empty."
    words = count_words(cleaned)
    if words == 0:
        return False, "", "The suggestion had no words in it."
    if words > MAX_SUGGESTION_WORDS:
        kept, taken = [], 0
        for piece in cleaned.split(" "):
            if taken >= MAX_SUGGESTION_WORDS:
                break
            kept.append(piece)
            taken += count_words(piece)
        return (False, " ".join(kept).rstrip(" ,.;:-"),
                "A suggestion may be at most %d words; this one had %d."
                % (MAX_SUGGESTION_WORDS, words))
    return True, cleaned, ""


def fallback_suggestion(history=None):
    """A suggestion drawn from what this turn actually did.

    It reads the history rather than guessing, because the one thing a hint
    must never do is describe work that did not happen. With nothing to go on
    it says something true of every turn.
    """
    kinds = set(history.kinds()) if history else set()
    if "test" in kinds:
        return "Review the test results"
    if kinds & {"file_edit", "file_create", "file_delete"}:
        return "Review the changed files"
    if "command" in kinds:
        return FALLBACK_SUGGESTIONS[1]
    return FALLBACK_SUGGESTIONS[0]


class AgentEvent:
    """One user-visible thing that happened, fixed once it is made.

    Nothing here is private reasoning. An event exists to be shown, so it
    carries only what the user may read: what happened, and the facts that go
    with it. The agent's deliberation never becomes one of these.
    """

    __slots__ = ("kind", "message", "stage", "detail", "at")

    def __init__(self, kind, message="", stage="", detail=None, at=None):
        self.kind = kind
        self.message = message
        self.stage = stage
        self.detail = dict(detail or {})
        self.at = time.time() if at is None else at

    @classmethod
    def make(cls, kind, message="", stage="", **detail):
        if kind not in EVENT_KINDS:
            detail = dict(detail, reported_kind=kind)
            kind = "progress"
        return cls(kind, str(message or ""), str(stage or ""), detail)

    @classmethod
    def from_payload(cls, payload):
        """Build an event from a dict the model wrote, or None.

        The model is not a trusted source of well-formed data, so this accepts
        anything and never raises. An unrecognised type is kept as a progress
        event with the original name recorded rather than dropped: losing a
        message the model meant the user to see is the exact failure this
        whole surface exists to prevent.
        """
        if not isinstance(payload, dict):
            return None
        message = payload.get("message", payload.get("text", ""))
        if not isinstance(message, str) or not message.strip():
            return None
        kind = payload.get("type", payload.get("kind", "progress"))
        if not isinstance(kind, str):
            kind = "progress"
        stage = payload.get("stage", "")
        if not isinstance(stage, str):
            stage = ""
        detail = {key: value for key, value in payload.items()
                  if key not in ("message", "text", "type", "kind", "stage")}
        return cls.make(kind, message.strip(), stage, **detail)

    def __repr__(self):
        return "AgentEvent(%r, %r)" % (self.kind, self.message)


class ResponseHistory:
    """The events of one turn, in the order they happened.

    Append-only on purpose. There is no method here that replaces, reorders or
    removes an event, because every one of those is a way for something the
    user has already read to disappear from under them. `reset` is the single
    exception and belongs to the start of a new turn.
    """

    def __init__(self, events=None):
        self._events = list(events or [])

    def append(self, event):
        self._events.append(event)
        return event

    def extend(self, events):
        added = tuple(event for event in events if event is not None)
        self._events.extend(added)
        return added

    @property
    def events(self):
        # A tuple: a caller cannot reach in and mutate the record.
        return tuple(self._events)

    def kinds(self):
        return tuple(event.kind for event in self._events)

    def of_kind(self, kind):
        return tuple(event for event in self._events if event.kind == kind)

    def last(self, kind=None):
        for event in reversed(self._events):
            if kind is None or event.kind == kind:
                return event
        return None

    def reset(self):
        """Start a new turn. The only thing that clears the history."""
        self._events = []

    def __len__(self):
        return len(self._events)

    def __iter__(self):
        return iter(self._events)

    def __bool__(self):
        return bool(self._events)


class Transcript:
    """Prints events once, into the terminal's own scrollback.

    The counterpart to LiveUI: that class owns one row and repaints it, this
    one owns everything that must outlive the row. Nothing printed here is
    ever repainted, so the terminal's scrollback is the history and scrolling
    up is how you read it -- no region to redraw, nothing to lose on a resize.

    `writer` is how lines reach the terminal. It exists so the caller can pass
    one that prints above a live region; the default writes straight to the
    stream, which is what a run without a live region wants.
    """

    def __init__(self, stream=None, history=None, writer=None):
        self.stream = sys.stdout if stream is None else stream
        self._history = history if history is not None else ResponseHistory()
        self._writer = writer
        self._last_level = None

    @property
    def history(self):
        return self._history

    def emit(self, event):
        """Record an event and print it. The event is returned either way."""
        if event is None:
            return None
        self._history.append(event)
        lines = self.lines_for(event)
        if lines:
            self._write(self._separator(event) + "\n".join(lines) + "\n")
        return event

    def _separator(self, event):
        """A blank line where the prominence changes, and nowhere else.

        Blank lines are structure, so one belongs at the edge of a block of
        results rather than between every pair of rows. Two file edits in a
        row are one group and stay together; a progress line after them is a
        different thing being said and gets the gap.
        """
        level = PROMINENCE.get(event.kind, 0)
        previous, self._last_level = self._last_level, level
        if previous is None:
            return ""
        return "\n" if (level == 2) != (previous == 2) else ""

    def emit_kind(self, kind, message="", stage="", **detail):
        return self.emit(AgentEvent.make(kind, message, stage, **detail))

    def _write(self, text):
        if self._writer is not None:
            return self._writer(text)
        return safe_write(self.stream, text)

    def _facts(self, event):
        """The second row of a prominent event: only measured facts.

        Every value here came from an action that ran. Nothing is estimated
        and nothing is filled in, so an event with no facts gets no row.
        """
        detail = event.detail
        parts = []
        if detail.get("added") is not None or detail.get("removed") is not None:
            parts.append("+%s -%s" % (detail.get("added", 0), detail.get("removed", 0)))
        for key, label in (("lines", "lines"), ("files", "files"), ("bytes", "bytes")):
            if isinstance(detail.get(key), int):
                parts.append("%d %s" % (detail[key], label))
        if detail.get("status") is not None:
            parts.append("exit %s" % detail["status"])
        return "  ".join(parts)

    def lines_for(self, event):
        """The painted lines for one event, without a trailing newline.

        Two kinds return nothing, for the same reason: something else on
        screen already is them. A `final` event has its own box in
        `render_response`, and drawing it here too would be two answers at
        once. A `next_step_suggestion` is the shadow text of the next prompt
        box and nothing else -- printing it as well would announce, in the
        reply, a line the user is about to see under their own cursor.

        Both are still recorded. The history is what the turn is answerable
        from afterwards, and a hint that was never drawn was still offered.
        """
        style = _EVENT_STYLE.get(event.kind, _EVENT_STYLE["progress"])
        if style["level"] == 3 or event.kind == "next_step_suggestion":
            return []
        stream = self.stream
        plain = plain_output(stream)
        width = max(20, shutil.get_terminal_size((80, 24)).columns - 1)
        mark = style["mark"][1 if plain else 0]

        if style["level"] == 0:
            # Dim, tight, no blank line. It should read as something said in
            # passing, not as a result.
            rows = wrap_lines(event.message, max(10, width - 4)) or [""]
            head = " %s " % mark if mark else "   "
            return [self._dim(head + rows[0], stream)] + [
                self._dim("   " + row, stream) for row in rows[1:]]

        painted_mark = _color(mark, style["position"], stream) if mark else ""
        if style["level"] == 1:
            rows = wrap_lines(event.message, max(10, width - 4)) or [""]
            return [" %s %s" % (painted_mark, rows[0])] + [
                "   " + row for row in rows[1:]]

        # Level 2: indented further and set apart by the blank line `emit`
        # puts at the edge of the block. This is the part of a turn worth
        # finding again when scrolling back, so it is the loudest thing below
        # the answer itself.
        rows = wrap_lines(event.message, max(10, width - 6)) or [""]
        out = ["   %s %s" % (painted_mark, rows[0])]
        out.extend("     " + row for row in rows[1:])
        facts = self._facts(event)
        if facts:
            out.append(self._dim("     " + facts, stream))
        return out

    @staticmethod
    def _dim(text, stream):
        return DIM + text + RESET if _supports_color(stream) else text
