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


def _color(text: str, progress: int, stream) -> str:
    if not _supports_color(stream):
        return text
    r, g, b = _gradient(progress)
    return f"\033[38;2;{r};{g};{b}m{text}\033[0m"


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
        self._render("THINKING")
        self._thread = threading.Thread(target=self._animate, name="tmt-thinking", daemon=True)
        self._thread.start()

    def _animate(self):
        while not self._stop.wait(self.interval):
            with self._lock:
                if not self._active or self._progress_started:
                    return
            self._render(random.choice(_THINKING_STYLES))

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

    def _render(self, text):
        with self._lock:
            if not self._active:
                return
        width = shutil.get_terminal_size((80, 24)).columns
        text = text[:max(1, width - 1)]
        self.stream.write("\r\033[2K" + text)
        self.stream.flush()
        self._last_render = text

    def _render_progress(self, label, estimate="calculating..."):
        with self._lock:
            if not self._active:
                return
            progress = self._progress
        width = min(12, max(4, shutil.get_terminal_size((80, 24)).columns // 8))
        filled = round(width * progress / 100)
        bar = "█" * filled + "░" * (width - filled)
        line = f"{_color(bar, progress, self.stream)} {progress:>3}% {label}"
        if estimate and progress < 100:
            elapsed = max(0.01, time.monotonic() - self._started_at)
            estimate_text = estimate if progress >= 95 else f"~{max(1, round(elapsed * (100 - progress) / max(1, progress)))} seconds"
            line += f" | Estimated finish: {estimate_text}"
        self._render(line)

    def _clear(self):
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
    stream.write(border + "\n")
    for line in lines:
        stream.write("│ " + line.ljust(inner) + " │\n")
    stream.write(bottom + "\n")
    stream.flush()
