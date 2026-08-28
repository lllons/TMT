"""Live terminal UI for model request lifecycle."""

import os
import random
import shutil
import sys
import threading
import time
from typing import Optional


_THINKING_STYLES = [
    "THINKING", "𝕋ℍ𝕀ℕ𝕂𝕀ℕ𝔾", "𝓣𝓗𝓘𝓝𝓚𝓘𝓝𝓖", "𝙏𝙃𝙄𝙉𝙆𝙄𝙉𝙂", "ＴＨＩＮＫＩＮＧ",
    "𝗧𝗛𝗜𝗡𝗞𝗜𝗡𝗚", "𝘛𝘏𝘐𝘕𝘒𝘐𝘕𝘎", "𝚃𝙷𝙸𝙽𝙺𝙸𝙽𝙶", "ᴛʜɪɴᴋɪɴɢ", "ＴＨＩＮＫＩＮＧ",
    "T H I N K I N G", "T·H·I·N·K·I·N·G", "T•H•I•N•K•I•N•G", "T-H-I-N-K-I-N-G",
    "T_H_I_N_K_I_N_G", "[ THINKING ]", "< THINKING >", "⟦ THINKING ⟧", "✦ THINKING ✦",
    "✧ THINKING ✧", "◆ THINKING ◆", "◇ THINKING ◇", "◈ THINKING ◈", "» THINKING «",
    "« THINKING »", "── THINKING ──", "━━ THINKING ━━", "▸ THINKING", "◂ THINKING",
    "⟶ THINKING", "⟵ THINKING", "☼ THINKING", "✺ THINKING", "✹ THINKING", "✷ THINKING",
    "✦ T H I N K I N G ✦", "⟡ T H I N K I N G ⟡", "Ｔ·Ｈ·Ｉ·Ｎ·Ｋ·Ｉ·Ｎ·Ｇ",
    "T̲H̲I̲N̲K̲I̲N̲G̲", "T̶H̶I̶N̶K̶I̶N̶G̶", "T̳H̳I̳N̳K̳I̳N̳G̳", "T͟H͟I͟N͟K͟I͟N͟G͟",
    "T H I N K I N G...", "THINKING...", "THINKING ···", "THINKING /", "THINKING \\",
]

# The colour cycle every animated surface shares: red -> orange -> green and
# back again, repeating for as long as the surface is on screen.
GRADIENT_CYCLE = 2.4     # seconds for one full there-and-back pass
GRADIENT_TICK = 0.08     # repaint period while a surface is animating
DIM = "\033[38;2;88;88;88m"
RESET = "\033[0m"


def _supports_color(stream) -> bool:
    return bool(getattr(stream, "isatty", lambda: False)()) and not os.environ.get("NO_COLOR")


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
        self._sink = None

    def attach_sink(self, sink):
        """Route the status line through a renderer (the live relay) instead
        of writing it directly, so both share one terminal region."""
        self._sink = sink

    @property
    def progress_started(self):
        return self._progress_started

    def start(self):
        with self._lock:
            self._active = True
            self._progress_started = False
            self._progress = 0
            self._events = 0
            self._started_at = time.monotonic()
        self._render("THINKING", painter=self._paint_cycle)
        self._thread = threading.Thread(target=self._animate, name="tmt-thinking", daemon=True)
        self._thread.start()

    def _paint_cycle(self, text):
        return cycle_text(text, self.stream)

    def _animate(self):
        """Keep the colour cycle turning for as long as the line is on screen.

        The THINKING word is swapped every `interval`; the gradient underneath
        it — and under the progress bar once it appears — repaints far more
        often, so the colour never settles.
        """
        tick = min(self.interval, GRADIENT_TICK)
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
                word = random.choice(_THINKING_STYLES)
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
        with self._lock:
            if not self._active:
                return
            self._progress = 100
        self._render_progress("Complete!", estimate=None)
        time.sleep(0.05)
        self.stop(clear=True)

    def stop(self, clear=True):
        with self._lock:
            self._active = False
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
        if clear:
            self._clear()

    def _render(self, text, painter=None):
        """Render one plain line, optionally painted after truncation.

        Truncation happens before painting so an escape sequence can never be
        cut in half, and `_last_render` stays readable text.
        """
        with self._lock:
            if not self._active:
                return
        width = shutil.get_terminal_size((80, 24)).columns
        text = text[:max(1, width - 1)]
        self._last_render = text
        painted = painter(text) if painter else text
        if self._sink is not None:
            self._sink(painted)
            return
        self.stream.write("\r\033[2K" + painted)
        self.stream.flush()

    def _render_progress(self, label, estimate="calculating..."):
        with self._lock:
            if not self._active:
                return
            progress = self._progress
            self._label, self._estimate = label, estimate
        width = min(12, max(4, shutil.get_terminal_size((80, 24)).columns // 8))
        bar = "█" * round(width * progress / 100) + "░" * (width - round(width * progress / 100))
        line = f"{bar} {progress:>3}% {label}"
        if estimate and progress < 100:
            elapsed = max(0.01, time.monotonic() - self._started_at)
            estimate_text = estimate if progress >= 95 else f"~{max(1, round(elapsed * (100 - progress) / max(1, progress)))} seconds"
            line += f" | Estimated finish: {estimate_text}"

        def paint(text, bar_width=len(bar)):
            return cycle_bar(progress, self.stream, width=bar_width) + text[bar_width:]

        self._render(line, painter=paint)

    def _clear(self):
        if self._sink is not None:
            self._sink("")
            return
        self.stream.write("\r\033[2K")
        self.stream.flush()


def render_response(response: str, stream=None):
    stream = stream or sys.stdout
    width = max(20, shutil.get_terminal_size((80, 24)).columns)
    inner = max(10, width - 4)
    lines = []
    for raw in str(response).splitlines() or [""]:
        while len(raw) > inner:
            lines.append(raw[:inner])
            raw = raw[inner:]
        lines.append(raw)
    border = "┌" + "─" * (inner + 2) + "┐"
    bottom = "└" + "─" * (inner + 2) + "┘"
    stream.write(cycle_text(border, stream) + "\n")
    for line in lines:
        stream.write(cycle_text("│", stream) + " " + line.ljust(inner) + " " + cycle_text("│", stream) + "\n")
    stream.write(cycle_text(bottom, stream, phase=gradient_phase() + 0.5) + "\n")
    stream.flush()
