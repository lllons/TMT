// The session screen drawn by Ink: the live frame, the input field, the
// queue, and the terminal modes they need, behind the small controller the
// app wires to the backend's events. The same contract as the hand-written
// renderer in src/live (see tui/README.md for the API); only the drawing moved.
//
// The keyboard is read here, from the real stdin, by the same parser as
// before (src/input/keys.ts: keys, bracketed paste, mouse reports, cursor
// position reports). Ink never sees it: it is given an inert stdin (see
// InkTerminal), so the two never both own the terminal's input.

import type { EventEmitter } from 'node:events';
import { StringDecoder } from 'node:string_decoder';
import { renderToString } from 'ink';
import type { ReactNode } from 'react';
import { LineEditor } from '../input/editor.js';
import { caretAt } from '../input/field.js';
import { KeyParser, type KeyEvent } from '../input/keys.js';
import { plain, sanitizeCommitLine } from '../input/width.js';
import type { Caption, Meter } from '../protocol.js';
import { detectTruecolor } from '../theme.js';
import { composeFrame, type Panel, type Progress } from './layout.js';
import { NO_LIVE, type Live } from './live.js';
import { InkTerminal, type Output } from './terminal.js';

export type { Live, Output, Progress };

/** How often the frame is recomposed while a progress row is up: the elapsed time and the colour cycle move with the clock. */
const TICK_MS = 100;

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
  /** Draw in 24-bit colour, not the nearest of 256. Defaults to what COLORTERM and WT_SESSION advertise. */
  truecolor?: boolean;
  /** The clock, in ms: the progress row's elapsed time and colour cycle. Defaults to Date.now. */
  clock?: () => number;
}

/** Esc is an interrupt only while a turn runs and the line is empty; otherwise it clears the line. */
export type InterruptKey = 'ctrl-c' | 'ctrl-d' | 'escape';

export interface AskOptions {
  /** Shadow text in the empty box while the question is open, drawn like the placeholder. */
  hint?: string;
  /**
   * One-key mode: while the question is open a typed character that is one of
   * these resolves it at once with that character, no Enter; Esc resolves
   * `null`; any other character, a paste and Enter are ignored (nothing is put
   * in the box). Without keys (or an empty list) the question takes a line.
   */
  keys?: string[];
  /** What the footer under the box says while the question is open. */
  footer?: string;
  /**
   * Ask the way the Python UI does: while the question is open the box, the
   * caption or counts, the progress row, the reply and the right-hand column
   * are not drawn. A line question is the typed text at column 0 with the
   * caret, right under the output; a one-key question is nothing at all, the
   * cursor's row under the output. All of it comes back when it closes.
   */
  bare?: boolean;
}

export interface SessionScreen {
  /** Write text into the scrollback above the frame. Never redrawn. */
  commit(text: string): void;
  /** The reply in progress: plain text, its last rows drawn live in a box above the prompt box. */
  setReply(text: string): void;
  /** A plain row under the box. Not drawn while a progress row is set. */
  setStatus(text: string): void;
  /**
   * The row above the box between turns, right-aligned: the clock, provider and
   * model as parts (the narrowest that fits is drawn), or text taken as it is.
   */
  setCaption(caption: string | Caption): void;
  /** The session's counts: the left end of the caption between turns, a row of their own during a turn. */
  setMeter(meter: Meter | null): void;
  /**
   * The status row above the box (`✻ Running npm test… (6s · ↓ 48 tokens)`), or
   * null for none: `label` says what is happening, `since` and `tokens` are
   * its readout. Redrawn with the clock.
   */
  setProgress(progress: Progress | null): void;
  /** The tool calls that are running, the plan, the agents and the notes drawn around the status row. */
  setLive(live: Live): void;
  /** The right-hand column, as styled rows beside the live area; null (or no rows) for none. */
  setPanel(panel: Panel | null): void;
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
   * A second ask replaces the first, which resolves `null`. With `keys`, one
   * of them typed answers it at once (no Enter).
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

export interface InkSessionScreen extends SessionScreen {
  /**
   * Commit an Ink component instead of text: it is rendered once at the
   * window's width and written like `commit`, so it is measured, kept in the
   * scrollback and never redrawn the same way.
   */
  commitNode(node: ReactNode): void;
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

export function createInkSessionScreen(options: SessionScreenOptions): InkSessionScreen {
  const { stdin, stdout } = options;
  const mouse = options.mouseCaret === true;
  const color = options.color ?? !process.env['NO_COLOR'];
  const truecolor = options.truecolor ?? detectTruecolor();
  const clock = options.clock ?? Date.now;
  const editor = new LineEditor();
  const terminal = new InkTerminal(stdout, () => schedule());
  const parser = new KeyParser(() => terminal.awaiting > 0);
  const decoder = new StringDecoder('utf8');
  const submitHandlers = new Set<(line: string) => void>();
  const interruptHandlers = new Set<(key: InterruptKey) => void>();
  const queue: { line: string; shown: string }[] = [];
  const commits: string[] = [];
  let reply = '';
  let status = '';
  let caption: string | Caption = '';
  let meter: Meter | null = null;
  let progress: Progress | null = null;
  let panel: Panel | null = null;
  let extras: Live = NO_LIVE;
  let ticker: ReturnType<typeof setInterval> | undefined;
  let busy = false;
  let live = false;
  let stopped = false;
  let scheduled = false;
  let draining = false;
  let escapeTimer: ReturnType<typeof setTimeout> | undefined;
  // The open question, if any, with the draft it displaced.
  let asking: {
    hint: string;
    footer: string;
    keys: string[];
    bare: boolean;
    draft: { value: string; cursor: number; pastes: string[]; placeholderVisible: boolean };
    resolve: (line: string | null) => void;
  } | null = null;

  const state = () => ({
    editor, reply, status, caption, meter, progress, panel, busy, color, truecolor,
    live: extras,
    ask: asking ? asking.hint : null,
    askFooter: asking?.footer ?? '',
    bare: asking?.bare ? (asking.keys.length > 0 ? 'key' as const : 'line' as const) : null,
    pending: queue.map((entry) => entry.shown),
    now: clock(),
  });

  // The status row's elapsed time, its star and shimmer, and the blinking
  // bullet of a running tool call move with the clock, so the frame is composed
  // again on a timer while one is up; an unchanged frame is not written (see
  // InkTerminal.paint).
  function tick(): void {
    const moving = live && (progress !== null || extras.calls.length > 0);
    if (moving && !ticker) {
      ticker = setInterval(schedule, TICK_MS);
      ticker.unref();
    } else if (!moving && ticker) {
      clearInterval(ticker);
      ticker = undefined;
    }
  }

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
    // A one-key question has no use for Enter.
    if (asking?.keys.length) return;
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
      case 'text': {
        // A one-key question: the first offered key typed is the answer.
        if (asking?.keys.length) {
          const typed = [...event.text].find((char) => asking!.keys.includes(char));
          if (typed !== undefined) closeAsk(typed);
          return;
        }
        editor.insert(event.text);
        return;
      }
      case 'paste':
        if (asking?.keys.length) return;
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
    // Esc stops a running turn, as the footer says, when there is no line for it to clear.
    if (key === 'escape' && busy && !editor.value) {
      for (const handler of [...interruptHandlers]) handler('escape');
      return;
    }
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
                          row, x - frame.field.prefix));
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
  // would make the shell the user returns to unusable. Ink is muted first, so
  // its own exit handler draws nothing after this.
  function restoreModes(): void {
    terminal.mute();
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
    tick();
    schedule();
  }

  function release(): void {
    // Committed text still waiting is written first, even mid-resize, so
    // nothing is lost to whatever takes the terminal next.
    if (commits.length > 0) terminal.paint(composeFrame(state(), terminal.columns, terminal.rows), commits.splice(0));
    live = false;
    tick();
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

  const screen: InkSessionScreen = {
    commit(text) {
      commits.push(text);
      schedule();
    },
    commitNode(node) {
      const text = renderToString(node, { columns: terminal.columns });
      // A block's last row may be a blank one; `commit` takes a final line break for the end of the text, so say it twice.
      screen.commit(text.endsWith('\n') ? text + '\n' : text);
    },
    setReply(text) {
      reply = plain(text, true);
      schedule();
    },
    setStatus(text) {
      status = plain(text);
      schedule();
    },
    setCaption(value) {
      caption = typeof value === 'string' ? plain(value) : {
        clock: plain(value.clock ?? ''), provider: plain(value.provider ?? ''), model: plain(value.model ?? ''),
      };
      schedule();
    },
    setMeter(value) {
      meter = value;
      schedule();
    },
    setProgress(value) {
      progress = value ? { ...value, label: plain(value.label) } : null;
      tick();
      schedule();
    },
    setLive(value) {
      // Text from the model and the backend (a command, a plan step): no escape reaches the frame.
      extras = {
        calls: value.calls.map((call) => ({ label: plain(call.label), target: plain(call.target) })),
        plan: value.plan.map((step) => ({ ...step, title: plain(step.title ?? step.id ?? '') })),
        agents: value.agents.map((agent) => ({ ...agent, kind: plain(agent.kind ?? ''), status: plain(agent.status ?? ''), task: plain(agent.task ?? ''), activity: plain(agent.activity ?? '') })),
        notes: value.notes.map((note) => ({ ...note, text: plain(note.text) })),
      };
      tick();
      schedule();
    },
    setPanel(value) {
      // Colour is kept (when the screen has any) and every other escape is not:
      // the rows are the backend's, and a row that moved the cursor would move
      // it off the frame.
      const rows = (value?.rows ?? []).map((row) => (color ? sanitizeCommitLine(String(row)) : plain(String(row))));
      panel = value && rows.length > 0 ? { rows, width: Math.max(0, Math.floor(value.width) || 0) } : null;
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
        asking = { hint: plain(askOptions.hint ?? ''), footer: plain(askOptions.footer ?? ''), keys: askOptions.keys ?? [], bare: askOptions.bare === true, draft, resolve };
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
      terminal.unmount();
      stopped = true;
    },
  };
  return screen;
}
