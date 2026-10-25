// The session screen: the live frame, the input field, the queue, and the
// terminal modes they need, behind the small controller lane 4 wires to the
// backend's events. See tui/README.md for the API.

import type { EventEmitter } from 'node:events';
import { StringDecoder } from 'node:string_decoder';
import { LineEditor } from '../input/editor.js';
import { caretAt } from '../input/field.js';
import { KeyParser, type KeyEvent } from '../input/keys.js';
import { plain } from '../input/width.js';
import { composeFrame, PROMPT_PREFIX } from './frame.js';
import { LiveTerminal, type Output } from './terminal.js';

export interface Input extends EventEmitter {
  isTTY?: boolean;
  setRawMode?(mode: boolean): unknown;
  resume?(): unknown;
  pause?(): unknown;
}

export interface SessionScreenOptions {
  stdin: Input;
  stdout: Output;
  /** Click to place the caret: the saved `.tmt_mouse` value. Off by default. */
  mouseCaret?: boolean;
  /** Dim the hints and the caption. Defaults to on unless NO_COLOR is set. */
  color?: boolean;
}

export type InterruptKey = 'ctrl-c' | 'ctrl-d';

export interface AskOptions {
  /** Shadow text in the empty box while the question is open, drawn like the placeholder. */
  hint?: string;
}

export interface SessionScreen {
  /** Write text into the scrollback above the frame. Never redrawn. */
  commit(text: string): void;
  /** The reply in progress: plain text, its last rows drawn live above the box. */
  setReply(text: string): void;
  /** The status row under the box. */
  setStatus(text: string): void;
  /** The caption row above the box, right-aligned. */
  setCaption(text: string): void;
  /** Shadow text in an empty, untouched box between turns. */
  setPlaceholder(text: string): void;
  /** A turn is running: Enter queues. Ending it sends the queue, one line per turn. */
  setBusy(busy: boolean): void;
  /** A finished line, folded pastes expanded. Returns an unsubscribe function. */
  onSubmit(handler: (line: string) => void): () => void;
  /** Ctrl-C (box empty, or a turn running) and Ctrl-D on an empty box. */
  onInterrupt(handler: (key: InterruptKey) => void): () => void;
  /**
   * Ask the user one question in the box. The next Enter resolves it with the
   * line (an empty one included) and never reaches `onSubmit`, busy or not;
   * Esc, Ctrl-C, Ctrl-D and `stop()` resolve `null`. A draft typed before it
   * is kept and comes back after, and queued lines stay queued meanwhile.
   * A second ask replaces the first, which resolves `null`.
   */
  ask(options?: AskOptions): Promise<string | null>;
  /** Lines waiting for the turn to end, oldest first. */
  queued(): string[];
  /** Hand the terminal to someone else (a delegated Python screen), and take it back. */
  suspend(): void;
  resume(): void;
  /** Take the frame down and restore the terminal. */
  stop(): void;
}

// Bracketed paste on and off, and SGR mouse reports (button events only) on
// and off. Every mode switched on here is switched off on every way out.
const PASTE_ON = '\x1b[?2004h';
const PASTE_OFF = '\x1b[?2004l';
const MOUSE_ON = '\x1b[?1000h\x1b[?1006h';
const MOUSE_OFF = '\x1b[?1006l\x1b[?1000l';
const SHOW_CURSOR = '\x1b[?25h';
const END_SYNC = '\x1b[?2026l';
// How long a lone ESC waits for the rest of a sequence before it is Escape.
const ESCAPE_WAIT_MS = 30;

const SIGNALS: NodeJS.Signals[] = ['SIGINT', 'SIGTERM', 'SIGHUP', 'SIGBREAK'];

export function createSessionScreen(options: SessionScreenOptions): SessionScreen {
  const { stdin, stdout } = options;
  const mouse = options.mouseCaret === true;
  const color = options.color ?? !process.env['NO_COLOR'];
  const editor = new LineEditor();
  const terminal = new LiveTerminal(stdout, () => schedule());
  const parser = new KeyParser(() => terminal.awaiting > 0);
  const decoder = new StringDecoder('utf8');
  const submitHandlers = new Set<(line: string) => void>();
  const interruptHandlers = new Set<(key: InterruptKey) => void>();
  const queue: { line: string; shown: string }[] = [];
  const commits: string[] = [];
  let reply = '';
  let status = '';
  let caption = '';
  let busy = false;
  let live = false;
  let stopped = false;
  let scheduled = false;
  let draining = false;
  let escapeTimer: ReturnType<typeof setTimeout> | undefined;
  // The open question, if any, with the draft it displaced.
  let asking: {
    hint: string;
    draft: { value: string; cursor: number; pastes: string[]; placeholderVisible: boolean };
    resolve: (line: string | null) => void;
  } | null = null;

  const state = () => ({
    editor, reply, status, caption, busy, color,
    ask: asking ? asking.hint : null,
    pending: queue.map((entry) => entry.shown),
  });

  /** Close the open question with `line` and give the box its draft back. */
  function closeAsk(line: string | null): void {
    const open = asking;
    if (!open) return;
    asking = null;
    Object.assign(editor, open.draft);
    open.resolve(line);
    drain();
    schedule();
  }

  function schedule(): void {
    if (scheduled || !live) return;
    scheduled = true;
    setImmediate(() => {
      scheduled = false;
      if (live && terminal.ready) terminal.paint(composeFrame(state(), terminal.columns, terminal.rows), commits.splice(0));
    });
  }

  function emitSubmit(line: string): void {
    for (const handler of [...submitHandlers]) handler(line);
  }

  function submit(): void {
    const line = editor.expanded();
    const shown = editor.value;
    editor.reset();
    // The answer to an open question, even an empty one, and not a task.
    if (asking) return closeAsk(line);
    if (!line.trim()) return;
    // Typed during a turn: queued, drawn above the box, and sent when the
    // turn ends. Sending it now would interleave two turns against one
    // workspace -- the failure a multi-line paste read as typing caused.
    if (busy) queue.push({ line, shown });
    else emitSubmit(line);
  }

  function drain(): void {
    if (draining) return;
    draining = true;
    try {
      // One line per turn: a handler that starts a turn calls setBusy(true)
      // before returning, which stops this; one that does not (a local
      // command) lets the next queued line go straight after it.
      while (!busy && !asking && queue.length > 0) emitSubmit(queue.shift()!.line);
    } finally {
      draining = false;
    }
  }

  function handle(event: KeyEvent): void {
    switch (event.kind) {
      case 'text':
        editor.insert(event.text);
        return;
      case 'paste':
        editor.insert(event.text, true);
        return;
      case 'cpr':
        terminal.report(event.row);
        return;
      case 'mouse':
        if (mouse && event.press && event.button === 0) click(event.x - 1, event.y - 1);
        return;
      case 'key':
        break;
    }
    const { key } = event;
    if (key === 'enter') return submit();
    if (key === 'escape' && asking) return closeAsk(null);
    if (key === 'ctrl-c') {
      // Between turns, Ctrl-C on a written line abandons the line. Otherwise
      // it is the caller's: the stop for a running turn, the way out between.
      // An open question is cancelled first, so the caller finds it closed.
      if (!asking && !busy && editor.value) return editor.reset();
      closeAsk(null);
      for (const handler of [...interruptHandlers]) handler('ctrl-c');
      return;
    }
    if (key === 'eof' && !editor.value) {
      closeAsk(null);
      for (const handler of [...interruptHandlers]) handler('ctrl-d');
      return;
    }
    if (editor.handle(key) === 'cancel') editor.reset();
  }

  /** A left click at screen column `x`, row `y` (0-based). */
  function click(x: number, y: number): void {
    const frame = composeFrame(state(), terminal.columns, terminal.rows);
    const row = y - terminal.top - terminal.pad - frame.field.first;
    if (row < 0 || row >= frame.field.count) return;
    editor.moveTo(caretAt(editor.value, editor.cursor, frame.field.inner, frame.field.maxRows,
                          row, x - PROMPT_PREFIX));
  }

  function onData(chunk: Buffer | string): void {
    if (!live) return;
    const text = typeof chunk === 'string' ? chunk : decoder.write(chunk);
    for (const event of parser.feed(text)) handle(event);
    clearTimeout(escapeTimer);
    if (parser.pending) {
      escapeTimer = setTimeout(() => {
        for (const event of parser.flush()) handle(event);
        schedule();
      }, ESCAPE_WAIT_MS);
    }
    schedule();
  }

  function onResize(): void {
    if (!live) return;
    terminal.resize();
  }

  // Put the terminal back however the process ends: a clean stop, an exit
  // from anywhere, a crash (Node runs 'exit' handlers after an uncaught
  // error) or a signal. Raw mode, bracketed paste and mouse reporting left on
  // would make the shell the user returns to unusable.
  function restoreModes(): void {
    try {
      stdout.write(END_SYNC + (mouse ? MOUSE_OFF : '') + PASTE_OFF + SHOW_CURSOR);
    } catch {
      // The stream is already gone; there is nothing left to restore.
    }
    try {
      if (stdin.isTTY) stdin.setRawMode?.(false);
    } catch {
      // As above.
    }
  }

  function onSignal(signal: NodeJS.Signals): void {
    restoreModes();
    removeHooks();
    // Re-raised only when nobody else listens, so a caller's own handler
    // still decides what the signal means.
    if (process.listenerCount(signal) === 0) process.kill(process.pid, signal);
  }

  function addHooks(): void {
    process.on('exit', restoreModes);
    for (const signal of SIGNALS) process.on(signal, onSignal);
  }

  function removeHooks(): void {
    process.off('exit', restoreModes);
    for (const signal of SIGNALS) process.off(signal, onSignal);
  }

  function take(): void {
    if (stdin.isTTY) stdin.setRawMode?.(true);
    stdin.resume?.();
    stdin.on('data', onData);
    stdout.on('resize', onResize);
    addHooks();
    stdout.write(PASTE_ON + (mouse ? MOUSE_ON : ''));
    terminal.open();
    live = true;
    schedule();
  }

  function release(): void {
    // Committed text still waiting is written first, even mid-resize, so
    // nothing is lost to whatever takes the terminal next.
    if (commits.length > 0) terminal.paint(composeFrame(state(), terminal.columns, terminal.rows), commits.splice(0));
    live = false;
    clearTimeout(escapeTimer);
    stdin.off('data', onData);
    // Reading stops while the console is still raw. A read left pending when
    // the mode switches back is a line read, and it would take the next line
    // typed away from whatever runs next (a Python menu on this terminal).
    stdin.pause?.();
    stdout.off('resize', onResize);
    terminal.close();
    restoreModes();
    removeHooks();
  }

  take();

  return {
    commit(text) {
      commits.push(text);
      schedule();
    },
    setReply(text) {
      reply = plain(text, true);
      schedule();
    },
    setStatus(text) {
      status = plain(text);
      schedule();
    },
    setCaption(text) {
      caption = plain(text);
      schedule();
    },
    setPlaceholder(text) {
      editor.placeholder = plain(text);
      if (editor.value === '') editor.placeholderVisible = editor.placeholder !== '';
      schedule();
    },
    setBusy(value) {
      busy = value;
      if (!busy) drain();
      schedule();
    },
    onSubmit(handler) {
      submitHandlers.add(handler);
      return () => submitHandlers.delete(handler);
    },
    onInterrupt(handler) {
      interruptHandlers.add(handler);
      return () => interruptHandlers.delete(handler);
    },
    ask(askOptions = {}) {
      if (stopped) return Promise.resolve(null);
      const previous = asking;
      // A replaced question keeps the draft it found, not the answer typed to it.
      const draft = previous ? previous.draft
        : { value: editor.value, cursor: editor.cursor, pastes: editor.pastes, placeholderVisible: editor.placeholderVisible };
      editor.reset();
      return new Promise((resolve) => {
        asking = { hint: plain(askOptions.hint ?? ''), draft, resolve };
        previous?.resolve(null);
        schedule();
      });
    },
    queued() {
      return queue.map((entry) => entry.line);
    },
    suspend() {
      if (!live || stopped) return;
      release();
    },
    resume() {
      if (live || stopped) return;
      take();
    },
    stop() {
      if (stopped) return;
      // Closed without giving the draft back or sending the queue: nothing
      // is left to type into.
      const open = asking;
      asking = null;
      open?.resolve(null);
      if (live) release();
      stopped = true;
    },
  };
}
