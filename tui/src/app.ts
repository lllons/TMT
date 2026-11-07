// The front end: the backend's events in, the session screen's calls out, and
// the keyboard's answers back. `runApp` is everything `main.ts` does except
// own the real process, so a test can run it with a fake terminal and a fake
// backend.

import { spawn } from 'node:child_process';
import { createElement, type ComponentType } from 'react';
import { Backend, type BackendOptions } from './backend.js';
import {
  approvalEcho, approvalHint, approvalTitle, captionText, commandLines, isResultKind, makeStyle, oneLine, questionHint, renderedRows,
  toolActivity, type Style,
} from './format.js';
import { Answer } from './ink/components/Answer.js';
import { Dialog } from './ink/components/Dialog.js';
import { Header } from './ink/components/Header.js';
import { Notice, type NoticeKind } from './ink/components/Notice.js';
import { Task } from './ink/components/Task.js';
import { failed, ToolCall } from './ink/components/ToolCall.js';
import { MAX_CONTENT_WIDTH } from './ink/layout.js';
import { NO_LIVE, RAIL, railed, type Call, type Live, type Note } from './ink/live.js';
import { createInkSessionScreen, type InkSessionScreen, type Input, type InterruptKey, type Output } from './ink/screen.js';
import type { AgentCard, BackendEvent, Caption, Choice, Meter, PlanStep } from './protocol.js';
import { detectTruecolor, makeTheme, type Theme } from './theme.js';
import { wrapRows } from './wrap.js';

export interface PreludeRequest {
  python: string;
  entry: string;
  args: string[];
}

/**
 * Runs the Python start-up screens (splash, menu, key entry) on the real
 * terminal and answers their exit code: 0 start, 10 the user chose Exit,
 * 2 the workspace was refused.
 */
export type PreludeRunner = (request: PreludeRequest) => Promise<number>;

export interface AppOptions extends Pick<BackendOptions, 'python' | 'entry' | 'args' | 'logPath'> {
  stdin: Input;
  stdout: Output;
  stderr: { write(text: string): unknown };
  runPrelude?: PreludeRunner;
  /** Colour in what is committed. Default: on unless NO_COLOR is set. */
  color?: boolean;
  /** 24-bit colour, not the nearest of 256. Default: what COLORTERM and WT_SESSION advertise. */
  truecolor?: boolean;
  /** How long a quit waits for the backend's `exit` before the screen comes down anyway. */
  quitWaitMs?: number;
}

/** The exit code that means "the user chose Exit" in the Python prelude. */
const PRELUDE_EXIT = 10;
const PRELUDE_REFUSED = 2;
/** Characters of reply kept for the live frame, which shows its last ten rows. */
const REPLY_KEPT = 4000;
/** How long a finished backend is given to end before it is killed. */
const GRACE_MS = 2000;
/** What the status row says about the user's turn at the box. */
const WAITING_FOR_APPROVAL = 'Waiting for your approval';
const WAITING_FOR_ANSWER = 'Waiting for your answer';
/** The states of a background agent that are still going. */
const AGENT_BUSY = new Set(['created', 'starting', 'running', 'waiting']);

export const runPreludeProcess: PreludeRunner = ({ python, entry, args }) => new Promise((resolve) => {
  // Ctrl-C belongs to the screen being run; this process has to outlive it.
  const ignore = (): void => {};
  process.on('SIGINT', ignore);
  const done = (code: number): void => {
    process.off('SIGINT', ignore);
    resolve(code);
  };
  try {
    const child = spawn(python, [entry, '--tui-prelude', ...args], { stdio: 'inherit' });
    child.on('error', () => done(1));
    child.on('close', (code) => done(code ?? 1));
  } catch {
    done(1);
  }
});

export async function runApp(options: AppOptions): Promise<number> {
  const prelude = options.runPrelude ?? runPreludeProcess;
  const code = await prelude({ python: options.python, entry: options.entry, args: options.args });
  if (code === PRELUDE_EXIT) return 0;
  if (code === PRELUDE_REFUSED) return PRELUDE_REFUSED;
  if (code !== 0) {
    options.stderr.write(`TMT: the start-up screens failed (exit code ${code}).\n`);
    return 1;
  }
  return new App(options).run();
}

type Item = BackendEvent | { type: 'lost'; reason: string };
type Of<T extends BackendEvent['type']> = Extract<BackendEvent, { type: T }>;

/** A tool call the main agent's loop started. */
interface Started extends Call {
  id: number;
  action: string;
}

// Events that end the wait for a tool call's own result: whatever they commit
// belongs under the call, so the call is committed (bare) first.
const FLUSHES = new Set<string>(['session', 'prompt', 'task', 'tool_start', 'final', 'turn_end', 'notice', 'text', 'command', 'approval', 'question', 'protocol_error', 'exit']);

class App {
  private screen: InkSessionScreen | undefined;
  private backend!: Backend;
  private readonly style: Style;
  private readonly theme: Theme;
  private readonly color: boolean;
  private resolve: (code: number) => void = () => {};
  // 'working' from a submit until the next prompt, a turn or a slash command alike.
  private state: 'starting' | 'idle' | 'working' = 'starting';
  private turn = false;
  private quitting = false;
  private finished = false;
  private quitTimer: ReturnType<typeof setTimeout> | undefined;
  private replyText = '';
  private noted = false;
  private caption: Caption = {};
  private meter: Meter = {};
  private status: Of<'status'> | null = null;
  // When the status row appeared, for its clock (ms).
  private since = 0;
  private version = '';
  // Tool calls that have started and not ended, and the one that has ended
  // and is waiting for the `event` that says what came of it.
  private readonly started = new Map<number, Started>();
  private awaiting: Started | null = null;
  private plan: PlanStep[] = [];
  private agents: AgentCard[] = [];
  private readonly agentStates = new Map<string, string>();
  private readonly notes = new Map<'review' | 'verify', Note>();
  private readonly blocks = new Map<string, string>();
  private openAsk: { abandoned: boolean } | null = null;
  // Approvals and questions in progress: while one is open the live reply is hidden.
  private asks = 0;
  private waiting = '';
  private readonly inbox: Item[] = [];
  private pumping = false;

  constructor(private readonly options: AppOptions) {
    this.color = options.color ?? !process.env['NO_COLOR'];
    this.style = makeStyle(this.color);
    this.theme = makeTheme({ color: this.color, truecolor: options.truecolor ?? detectTruecolor() });
  }

  run(): Promise<number> {
    return new Promise((resolve) => {
      this.resolve = resolve;
      this.backend = new Backend(this.options, {
        onEvent: (event) => this.enqueue(event),
        onLost: (reason) => this.enqueue({ type: 'lost', reason }),
      });
      this.backend.hello(this.options.stdout.columns || 80, this.options.stdout.rows || 24);
    });
  }

  // Events are handled one at a time, in order. Almost all of them finish at
  // once; the one that does not is the menu, which holds the rest back until
  // the terminal is the screen's again.
  private enqueue(item: Item): void {
    this.inbox.push(item);
    void this.pump();
  }

  private async pump(): Promise<void> {
    if (this.pumping) return;
    this.pumping = true;
    try {
      while (this.inbox.length > 0 && !this.finished) {
        const item = this.inbox.shift()!;
        try {
          const wait = this.handle(item);
          if (wait) await wait;
        } catch (error) {
          this.complain([this.style.red(`TMT: could not show a ${item.type} event (${error instanceof Error ? error.message : String(error)}).`)]);
        }
      }
    } finally {
      this.pumping = false;
    }
  }

  /** The width committed blocks are laid out for: the box's, less the spare column. */
  private width(): number {
    return Math.min(MAX_CONTENT_WIDTH, Math.max(1, (this.options.stdout.columns || 80) - 1));
  }

  /**
   * Committed rows, wrapped at a word boundary to the width the frame is drawn
   * at, as it is now: a row left to the terminal would be cut mid-word at the
   * window's edge, and run past the 100-column block in a wide window.
   */
  private commit(...lines: string[]): void {
    const width = this.width();
    for (const line of lines) for (const row of wrapRows(line, width)) this.screen?.commit(row);
  }

  /** A block of the record: an Ink component, laid out once for the width the window has now. */
  private block<P extends object>(component: ComponentType<P>, props: Omit<P, 'theme' | 'width'>): void {
    this.screen?.commitNode(createElement(component as ComponentType<object>, { ...props, theme: this.theme, width: this.width() }));
  }

  /**
   * The backend's own rows for an event, laid out by the Python renderer for
   * this width, blank rows and colour included: committed exactly as they
   * are, never wrapped or reformatted. Used only when the event has none of
   * the structured fields to draw it from.
   */
  private commitRendered(rows: unknown): boolean {
    const own = renderedRows(rows);
    if (!own) return false;
    for (const row of own) this.screen?.commit(row);
    return true;
  }

  /** Errors go above the box, or to stderr while there is no box. */
  private complain(lines: string[]): void {
    if (this.screen) this.commit(...lines);
    else this.options.stderr.write(lines.join('\n') + '\n');
  }

  private handle(item: Item): void | Promise<void> {
    if (item.type === 'lost') return this.lost(item.reason);
    const screen = this.screen;
    if (!screen) {
      // Before `ready` there is nothing to draw on, and nothing else can come first.
      if (item.type === 'ready') this.begin(item);
      else if (item.type === 'exit') this.exited(item);
      return;
    }
    if (FLUSHES.has(item.type)) this.flushCall();
    switch (item.type) {
      case 'session':
        this.block(Header, { version: this.version, workspace: item.workspace, tip: item.tip });
        return;
      case 'prompt':
        this.turn = false;
        this.status = null;
        this.replyText = '';
        this.started.clear();
        this.caption = item.caption ?? {};
        this.meter = item.meter ?? {};
        screen.setReply('');
        screen.setProgress(null);
        this.syncLive();
        screen.setCaption(this.caption);
        screen.setMeter(this.meter);
        screen.setPlaceholder(item.placeholder ?? '');
        // The state first: setBusy(false) sends a queued line, and its handler
        // turns the screen busy again.
        this.state = 'idle';
        screen.setBusy(false);
        return;
      case 'task':
        this.noted = false;
        if (item.caption) {
          this.caption = item.caption;
          screen.setCaption(captionText(this.meter, this.caption));
        }
        if (typeof item.text === 'string') this.block(Task, { text: item.text });
        else this.commitRendered(item.rows);
        return;
      case 'turn_start':
        this.turn = true;
        this.state = 'working';
        this.since = Date.now();
        this.status = { type: 'status', state: 'thinking', label: '' };
        screen.setBusy(true);
        this.showProgress();
        this.syncLive();
        return;
      case 'status':
        // The clock starts when the row appears; the backend's own `elapsed`
        // (in `meter`) corrects it as soon as one arrives.
        if (!this.status?.state || this.status.state === 'idle') this.since = Date.now();
        this.status = item;
        this.showProgress();
        return;
      case 'meter':
        this.meter = item;
        if (typeof item.elapsed === 'number' && Number.isFinite(item.elapsed)) {
          // The backend's clock, once ours is off by more than its tenth of a
          // second of rounding: re-anchored every time, the seconds would flicker.
          const since = Date.now() - item.elapsed * 1000;
          if (Math.abs(since - this.since) > 500) this.since = since;
        }
        screen.setMeter(item);
        this.showProgress();
        return;
      // The reply is only shown live, as the Python UI does: `final` carries
      // the same text, and `reply_reset` takes back what was streamed, so
      // committing lines as they arrive would print some twice and keep some
      // that were taken back. The frame shows its last ten rows either way.
      case 'delta': {
        const first = this.replyText === '';
        let text = this.replyText + (item.text ?? '');
        // Cut at a line start, so the rows that stay wrap the way they did.
        const cut = text.length > REPLY_KEPT ? text.indexOf('\n', text.length - REPLY_KEPT) : -1;
        if (cut >= 0) text = text.slice(cut + 1);
        this.replyText = text;
        if (this.asks === 0) screen.setReply(text);
        if (first) this.showProgress();
        return;
      }
      case 'reply_reset':
        this.replyText = '';
        screen.setReply('');
        this.showProgress();
        return;
      case 'tool_start': {
        // An older backend's bare start says nothing to draw.
        if (!item.action && !item.label && !item.target) return;
        const action = item.action ?? '';
        const id = item.id ?? this.started.size + 1;
        this.started.set(id, { id, action, label: item.label || humanize(action), target: item.target ?? '' });
        this.showProgress();
        this.syncLive();
        return;
      }
      case 'tool_end':
        this.toolEnded(item);
        return;
      case 'event':
        this.event(item);
        return;
      case 'notice': {
        this.noted = true;
        const text = item.text;
        if (typeof text === 'string') this.block(Notice, { kind: item.level === 'error' ? 'error' : item.level === 'warning' ? 'warning' : 'info', text });
        else this.commitRendered(item.rows);
        return;
      }
      case 'text':
        this.commit(item.text ?? '');
        return;
      case 'final': {
        this.noted = true;
        this.replyText = '';
        screen.setReply('');
        const text = item.text ?? '';
        if (text.trim()) this.block(Answer, { text });
        else this.commitRendered(item.rows);
        return;
      }
      case 'turn_end':
        this.turn = false;
        this.replyText = '';
        screen.setReply('');
        this.endStarted();
        this.syncLive();
        if (item.outcome && !this.noted) this.commit(this.style.dim(item.outcome));
        return;
      case 'command':
        if (!this.commitRendered(item.rows)) this.commit(...commandLines(item, this.style), '');
        if (item.to_menu) return this.openMenu();
        return;
      case 'approval':
        this.approval(item);
        return;
      case 'question':
        void this.question(item);
        return;
      case 'plan':
        this.plan = item.steps ?? [];
        this.syncLive();
        return;
      case 'review':
      case 'verify':
        this.note(item.type, item.state, item.headline);
        return;
      case 'agents':
        this.agentsChanged(item.agents ?? []);
        return;
      case 'protocol_error':
        this.commit(this.style.red(`Protocol error: ${item.message ?? 'a message was not accepted'}`));
        return;
      case 'exit':
        this.exited(item);
        return;
      default:
        // `ready` again, `settings` (the mouse setting needs a restart), the
        // right-hand `panel` (the plan is drawn from the structured events
        // instead), and anything a newer backend adds.
        return;
    }
  }

  /** `ready`: the screen can be made now, with the saved mouse setting. */
  private begin(ready: Of<'ready'>): void {
    const { stdin, stdout } = this.options;
    const screen = createInkSessionScreen({ stdin, stdout, mouseCaret: ready.mouse === 'on', color: this.color, truecolor: this.theme.truecolor });
    this.screen = screen;
    this.version = ready.version ?? '';
    // Starting counts as busy: a line typed before the first prompt waits for it.
    screen.setBusy(true);
    screen.onSubmit((line) => this.submit(line));
    screen.onInterrupt((key) => this.interrupted(key));
    stdout.on('resize', this.resized);
    if (ready.configured === false) {
      this.commit(this.style.yellow('No API key is set, so a task will fail. Type /back to open the menu and add one.'));
    }
  }

  private readonly resized = (): void => {
    this.backend.resize(this.options.stdout.columns || 80, this.options.stdout.rows || 24);
  };

  /** A line from the box. The screen must be busy before this returns (the queue rule). */
  private submit(line: string): void {
    if (this.quitting) return;
    this.state = 'working';
    this.screen!.setBusy(true);
    this.backend.submit(line);
  }

  private interrupted(key: InterruptKey): void {
    if (this.finished) return;
    if (this.openAsk) {
      // The screen has already closed the question. In a turn the backend
      // abandons what it was waiting for; outside one the question answers
      // itself with a refusal.
      if (key === 'ctrl-c' && this.turn) {
        this.openAsk.abandoned = true;
        this.backend.interrupt();
      }
      return;
    }
    if (this.quitting) return key === 'escape' ? undefined : this.finish(0);
    if (this.state === 'working') {
      if (key === 'ctrl-c' || key === 'escape') this.backend.interrupt();
      return;
    }
    // Esc is only an interrupt: between prompts there is nothing for it to stop.
    if (key !== 'escape') this.quit();
  }

  private quit(): void {
    if (this.quitting) return;
    this.quitting = true;
    this.backend.quit();
    // A backend that does not answer must not keep the user in the box.
    this.quitTimer = setTimeout(() => this.finish(0), this.options.quitWaitMs ?? 3000);
  }

  /** The menu: the screen steps aside, the Python menu runs on the terminal, the screen returns. */
  private async openMenu(): Promise<void> {
    const screen = this.screen!;
    const prelude = this.options.runPrelude ?? runPreludeProcess;
    screen.suspend();
    let code: number;
    try {
      code = await prelude({ python: this.options.python, entry: this.options.entry, args: this.options.args });
    } catch {
      code = 1;
    }
    screen.resume();
    if (code === PRELUDE_EXIT) this.quit();
    else if (code === 0) this.backend.reload();
    else this.commit(this.style.red(`The menu closed with an error (exit code ${code}).`));
  }

  // -------------------------------------------------------------------------
  // Tool calls: `│ Name target` in the frame while it runs, one block in the
  // record when its result is known.

  private toolEnded(end: Of<'tool_end'>): void {
    const call = (end.id !== undefined ? this.started.get(end.id) : undefined) ?? [...this.started.values()].find((other) => other.action === end.action);
    if (!call) return;
    this.started.delete(call.id);
    if (end.status === 'raised' || end.status === 'interrupted') {
      // No result event follows a call that threw: the error is its result.
      this.commitCall(call, { error: (end.error ?? '').trim() || (end.status === 'interrupted' ? 'Interrupted' : 'Failed') });
    } else {
      this.flushCall();
      this.awaiting = call;
    }
    this.showProgress();
    this.syncLive();
  }

  /** The call that ended and heard nothing of its result, committed as it is. */
  private flushCall(): void {
    const call = this.awaiting;
    this.awaiting = null;
    if (call) this.commitCall(call, {});
  }

  /** Calls still running when the turn ends: they were cut short. */
  private endStarted(): void {
    for (const call of this.started.values()) this.commitCall(call, { error: 'Interrupted' });
    this.started.clear();
  }

  private commitCall(call: Call, result: { kind?: string; message?: string; detail?: Record<string, unknown>; error?: string }): void {
    const tone = result.error !== undefined || failed(result.detail) ? 'error' : 'ok';
    this.block(ToolCall, { label: call.label, target: call.target, tone, ...result });
  }

  /** The rows of the record for an `event`: the result of the call before it, or a row of its own. */
  private event(event: Of<'event'>): void {
    const kind = event.kind ?? '';
    if (isResultKind(kind)) {
      const call = this.awaiting ?? { label: KIND_LABEL[kind] ?? 'Tool', target: '' };
      this.awaiting = null;
      this.commitCall(call, { kind, message: event.message ?? '', detail: event.detail });
      return;
    }
    this.flushCall();
    if (typeof event.message !== 'string') {
      this.commitRendered(event.rows);
      return;
    }
    this.block(Notice, { kind: kind in NOTICE_KIND ? NOTICE_KIND[kind]! : 'info', text: event.message });
  }

  // -------------------------------------------------------------------------
  // The plan, the reviews and the agents.

  /** A review or a verification: live while it runs, one row in the record when it ends. */
  private note(which: 'review' | 'verify', state: string | undefined, headline: string | undefined): void {
    const word = which === 'review' ? 'Review' : 'Verify';
    if (!state || state === 'idle') {
      this.notes.delete(which);
    } else if (state === 'running' || state === 'planning') {
      this.notes.set(which, { state: 'running', text: headline || `${word} ${state}` });
    } else {
      this.notes.delete(which);
      const text = headline || `${word} ${state}`;
      const key = `${state}|${text}`;
      if (this.blocks.get(which) !== key) {
        this.blocks.set(which, key);
        this.block(Notice, { kind: state === 'passed' ? 'success' : state === 'warnings' ? 'warning' : state === 'failed' || state === 'error' ? 'error' : 'progress', text });
      }
    }
    this.syncLive();
  }

  /** The agents on the board: a row in the frame while one works, one in the record when it stops. */
  private agentsChanged(agents: AgentCard[]): void {
    for (const agent of agents) {
      const id = String(agent.id ?? agent.number ?? '');
      const was = this.agentStates.get(id);
      const now = agent.status ?? '';
      this.agentStates.set(id, now);
      if (was === undefined || !AGENT_BUSY.has(was) || AGENT_BUSY.has(now)) continue;
      const paint = now === 'completed' ? this.theme.success : this.theme.error;
      this.commit(`${paint('⎇')} ${agent.number ?? agent.id ?? '?'} ${agent.kind ?? 'agent'}  ${paint(now)}  ${oneLine(agent.task ?? '')}`.trimEnd());
    }
    this.agents = agents;
    this.syncLive();
  }

  /** What the frame draws around the status row: the running calls, the plan while a turn runs, the agents and notes. */
  private syncLive(): void {
    const live: Live = {
      calls: [...this.started.values()].map(({ label, target }) => ({ label, target })),
      plan: this.turn ? this.plan : [],
      agents: this.agents.filter((agent) => AGENT_BUSY.has(agent.status ?? '')),
      notes: [...this.notes.values()],
    };
    this.screen?.setLive(live.calls.length + live.plan.length + live.agents.length + live.notes.length === 0 ? NO_LIVE : live);
  }

  // -------------------------------------------------------------------------
  // Approvals and questions.

  /** What the record says once a dialog is answered: the dialog's own rail, then the answer dim. */
  private answered(text: string): void {
    this.commit(railed(this.theme.needsYou(RAIL.heavy), this.theme.dim(text)));
  }

  private approval(event: Of<'approval'>): void {
    const choices = event.choices ?? [];
    // The backend's whole sentence says the same as the choices; it is kept
    // only for an approval that offers none.
    const sentence = (event.hint ?? '').trim();
    const text = event.question ?? '';
    if (text || choices.length > 0) {
      this.block(Dialog, {
        title: approvalTitle(event.action),
        body: [...text.split('\n'), ...(choices.length === 0 && sentence ? ['', sentence] : [])],
        options: choices.map(({ key, label }) => ({ key, label })),
      });
    } else {
      this.commitRendered(event.rows);
    }
    void this.whileAsking(WAITING_FOR_APPROVAL, () => this.ask(approvalHint(choices, sentence), undefined, 'esc refuses · ctrl-c interrupts')).then((answer) => {
      if (!answer) return;
      const reply = approvalAnswer(answer.line, choices);
      this.answered(approvalEcho(reply, choices));
      this.backend.approvalReply(event.id, reply);
    });
  }

  private question(event: Of<'question'>): Promise<void> {
    const keys = (event.keys ?? []).map(String);
    const options = Array.isArray(event.options) ? event.options.map(String) : [];
    if (event.question && options.length > 0) {
      this.block(Dialog, { title: event.question, options: options.map((label, index) => ({ key: keys[index] ?? String(index + 1), label })) });
    } else if (event.text) {
      this.block(Dialog, { title: 'Question', body: event.text.split('\n') });
    } else {
      this.commitRendered(event.rows);
    }
    const hint = questionHint(keys);
    return this.whileAsking(WAITING_FOR_ANSWER, async () => {
      for (;;) {
        // One key answers it, as in the Python UI: no Enter.
        const answer = await this.ask(hint, keys, 'esc skips the question · ctrl-c interrupts');
        if (!answer) return;
        const typed = (answer.line ?? '').trim();
        const key = keys.find((candidate) => candidate.toLowerCase() === typed.toLowerCase());
        // Esc and an empty line dismiss it. Anything else is asked again here:
        // the backend would answer a key it did not offer with an error.
        if (answer.line === null || typed === '') {
          this.answered('Skipped');
          return this.backend.answer(event.id, '');
        }
        if (key !== undefined) {
          this.answered(options[keys.indexOf(key)] ?? key);
          return this.backend.answer(event.id, key);
        }
        this.commit(this.style.yellow(`"${typed}" is not one of ${keys.join(', ')}. Type one of them, or press Esc to skip.`));
      }
    });
  }

  /**
   * Run an approval or a question with the live reply hidden. The reply came
   * before it, and drawn under the question that was just committed it reads as
   * coming after. It is drawn again when the last one closes, if the turn is
   * still streaming (a `final` or `reply_reset` meanwhile has emptied it). The
   * status row says whose turn it is while it is open.
   */
  private async whileAsking<T>(waiting: string, run: () => Promise<T>): Promise<T> {
    this.asks += 1;
    this.waiting = waiting;
    this.screen?.setReply('');
    this.showProgress();
    try {
      return await run();
    } finally {
      this.asks -= 1;
      if (this.asks === 0) this.waiting = '';
      if (this.asks === 0 && this.turn && this.replyText && !this.finished) this.screen?.setReply(this.replyText);
      if (!this.finished) this.showProgress();
    }
  }

  /** One question in the box. null when an interrupt took it back or the session is over. */
  private async ask(hint: string | undefined, keys?: string[], footer?: string): Promise<{ line: string | null } | null> {
    const slot = { abandoned: false };
    this.openAsk = slot;
    const line = await this.screen!.ask({ hint, keys, footer });
    if (this.openAsk === slot) this.openAsk = null;
    return slot.abandoned || this.finished ? null : { line };
  }

  // -------------------------------------------------------------------------

  /** What the status row says is happening, from what is known: the user's turn, the call that runs, the reply, or thinking. */
  private activity(): string {
    if (this.waiting) return this.waiting;
    const call = [...this.started.values()].at(-1);
    if (call) return toolActivity(call.action, call.label, call.target);
    if (this.notes.has('review')) return 'Reviewing the changes';
    if (this.notes.has('verify')) return 'Verifying the changes';
    const label = this.status?.label ?? '';
    if (/review/i.test(label)) return 'Reviewing the changes';
    if (/verif/i.test(label)) return 'Verifying the changes';
    if (/final/i.test(label)) return 'Finalizing the answer';
    if (!this.turn) return 'Working';
    return this.replyText ? 'Writing the reply' : 'Thinking';
  }

  /** The status row above the box: what is happening, with the turn's clock and its tokens. */
  private showProgress(): void {
    const screen = this.screen;
    if (!screen) return;
    const { state } = this.status ?? {};
    if (!state || state === 'idle') {
      screen.setProgress(null);
      return;
    }
    screen.setProgress({
      state: 'working',
      label: this.activity(),
      percent: null,
      since: this.since,
      tokens: this.meter.turn_tokens ?? 0,
      waiting: this.waiting !== '',
    });
  }

  private exited(event: Of<'exit'>): void {
    if (event.reason === 'crashed') {
      this.complain([this.style.red(`The backend crashed${event.message ? `: ${event.message}` : ''}.`), ...this.logLines()]);
    }
    this.finish(typeof event.code === 'number' ? event.code : 1);
  }

  /** The backend could not start, or ended without saying goodbye. */
  private lost(reason: string): void {
    this.complain([this.style.red(`The backend stopped unexpectedly: ${reason}.`), ...this.logLines()]);
    this.finish(1);
  }

  private logLines(): string[] {
    const tail = this.backend.logTail();
    if (tail.length === 0) return [this.style.dim(`Nothing was written to ${this.backend.logPath}.`)];
    return [this.style.dim(`Last lines of ${this.backend.logPath}:`), ...tail.map((line) => this.style.dim('  ' + line))];
  }

  /** Take the screen down, let the backend end, answer the exit code. */
  private finish(code: number): void {
    if (this.finished) return;
    this.finished = true;
    clearTimeout(this.quitTimer);
    this.options.stdout.off('resize', this.resized);
    this.screen?.stop();
    const grace = setTimeout(() => this.backend.kill(), GRACE_MS);
    const giveUp = setTimeout(() => this.resolve(code), GRACE_MS + 1000);
    void this.backend.closed.then(() => {
      clearTimeout(grace);
      clearTimeout(giveUp);
      this.resolve(code);
    });
  }
}

/** The result kinds a call is named by when its start was never seen. */
const KIND_LABEL: Record<string, string> = {
  file_read: 'Read', file_edit: 'Edit', file_create: 'Write', file_delete: 'Delete', command: 'Run', test: 'Test', tool: 'Tool', background_agent: 'Agent',
};

/** What the events that are not a tool's result look like. */
const NOTICE_KIND: Record<string, NoticeKind> = {
  progress: 'progress', milestone: 'milestone', warning: 'warning', success: 'success', error: 'error',
};

/** `read_file` as `Read File`, for a call whose label did not come. */
const humanize = (action: string): string => action.split('_').filter(Boolean).map((word) => word[0]!.toUpperCase() + word.slice(1)).join(' ') || 'Tool';

/**
 * What to send for an approval: a choice's key when that is what was typed,
 * the refusing choice (the last) for Esc or an empty line, and anything else
 * as typed, which the backend takes as a refusal.
 */
function approvalAnswer(line: string | null, choices: Choice[]): string {
  const refuse = choices.length > 0 ? choices[choices.length - 1]!.key : 'n';
  const typed = (line ?? '').trim().toLowerCase();
  if (typed === '') return refuse;
  return choices.find((choice) => choice.key.toLowerCase() === typed)?.key ?? line!;
}
