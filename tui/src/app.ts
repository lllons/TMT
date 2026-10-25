// The front end: the backend's events in, the session screen's calls out, and
// the keyboard's answers back. `runApp` is everything `main.ts` does except
// own the real process, so a test can run it with a fake terminal and a fake
// backend.

import { spawn } from 'node:child_process';
import { Backend, type BackendOptions } from './backend.js';
import {
  agentsBlock, captionText, commandLines, eventLines, finalLines, makeStyle, noticeLines, planBlock,
  reviewBlock, sessionLines, statusText, taskLine, verifyBlock, type Style,
} from './format.js';
import { createSessionScreen, type Input, type InterruptKey, type SessionScreen } from './live/session.js';
import type { Output } from './live/terminal.js';
import type { BackendEvent, Caption, Choice, Meter } from './protocol.js';

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

class App {
  private screen: SessionScreen | undefined;
  private backend!: Backend;
  private readonly style: Style;
  private readonly color: boolean;
  private resolve: (code: number) => void = () => {};
  // 'working' from a submit until the next prompt, a turn or a slash command alike.
  private state: 'starting' | 'idle' | 'working' = 'starting';
  private turn = false;
  private quitting = false;
  private finished = false;
  private quitTimer: ReturnType<typeof setTimeout> | undefined;
  private replyText = '';
  private lastLevel: number | null = null;
  private noted = false;
  private caption: Caption = {};
  private meter: Meter = {};
  private status: Of<'status'> | null = null;
  private version = '';
  private readonly blocks = new Map<string, string>();
  private openAsk: { abandoned: boolean } | null = null;
  private readonly inbox: Item[] = [];
  private pumping = false;

  constructor(private readonly options: AppOptions) {
    this.color = options.color ?? !process.env['NO_COLOR'];
    this.style = makeStyle(this.color);
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

  private commit(...lines: string[]): void {
    for (const line of lines) this.screen?.commit(line);
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
    switch (item.type) {
      case 'session':
        this.commit('', ...sessionLines(item, this.version, new Date().toLocaleDateString('en-CA'), this.style), '');
        return;
      case 'prompt':
        this.turn = false;
        this.status = null;
        this.replyText = '';
        this.caption = item.caption ?? {};
        this.meter = item.meter ?? {};
        screen.setReply('');
        screen.setStatus('');
        screen.setCaption(captionText(this.meter, this.caption));
        screen.setPlaceholder(item.placeholder ?? '');
        // The state first: setBusy(false) sends a queued line, and its handler
        // turns the screen busy again.
        this.state = 'idle';
        screen.setBusy(false);
        return;
      case 'task':
        this.lastLevel = null;
        this.noted = false;
        if (item.caption) {
          this.caption = item.caption;
          screen.setCaption(captionText(this.meter, this.caption));
        }
        this.commit('', taskLine(item.text ?? '', this.style));
        return;
      case 'turn_start':
        this.turn = true;
        this.state = 'working';
        screen.setBusy(true);
        return;
      case 'status':
        this.status = item;
        screen.setStatus(statusText(this.status, this.meter));
        return;
      case 'meter':
        this.meter = item;
        screen.setCaption(captionText(this.meter, this.caption));
        screen.setStatus(statusText(this.status, this.meter));
        return;
      // The reply is only shown live, as the Python UI does: `final` carries
      // the same text, and `reply_reset` takes back what was streamed, so
      // committing lines as they arrive would print some twice and keep some
      // that were taken back. The frame shows its last ten rows either way.
      case 'delta': {
        let text = this.replyText + (item.text ?? '');
        // Cut at a line start, so the rows that stay wrap the way they did.
        const cut = text.length > REPLY_KEPT ? text.indexOf('\n', text.length - REPLY_KEPT) : -1;
        if (cut >= 0) text = text.slice(cut + 1);
        this.replyText = text;
        screen.setReply(text);
        return;
      }
      case 'reply_reset':
        this.replyText = '';
        screen.setReply('');
        return;
      case 'event': {
        const level = item.level ?? 1;
        // A blank row where the record crosses between prominent rows and the rest.
        if (this.lastLevel !== null && (this.lastLevel === 2) !== (level === 2)) this.commit('');
        this.lastLevel = level;
        this.commit(...eventLines(item, this.style));
        return;
      }
      case 'notice':
        this.noted = true;
        this.commit(...noticeLines(item.level, item.text ?? '', this.style));
        return;
      case 'text':
        this.commit(item.text ?? '');
        return;
      case 'final':
        this.noted = true;
        this.replyText = '';
        screen.setReply('');
        this.commit(...finalLines(item.text ?? ''));
        return;
      case 'turn_end':
        this.turn = false;
        this.replyText = '';
        screen.setReply('');
        if (item.outcome && !this.noted) this.commit(this.style.dim(item.outcome));
        return;
      case 'command':
        this.commit(...commandLines(item, this.style), '');
        if (item.to_menu) return this.openMenu();
        return;
      case 'approval':
        this.approval(item);
        return;
      case 'question':
        void this.question(item);
        return;
      case 'plan':
        return this.snapshot('plan', planBlock(item.steps ?? [], this.style));
      case 'review':
        return this.snapshot('review', reviewBlock(item, this.style));
      case 'verify':
        return this.snapshot('verify', verifyBlock(item, this.style));
      case 'agents':
        return this.snapshot('agents', agentsBlock(item.agents ?? [], this.style));
      case 'protocol_error':
        this.commit(this.style.red(`Protocol error: ${item.message ?? 'a message was not accepted'}`));
        return;
      case 'exit':
        this.exited(item);
        return;
      default:
        // `ready` again, `settings` (the mouse setting needs a restart), the
        // tool events, and anything a newer backend adds.
        return;
    }
  }

  /** `ready`: the screen can be made now, with the saved mouse setting. */
  private begin(ready: Of<'ready'>): void {
    const { stdin, stdout } = this.options;
    const screen = createSessionScreen({ stdin, stdout, mouseCaret: ready.mouse === 'on', color: this.color });
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
    if (this.quitting) return this.finish(0);
    if (this.state === 'working') {
      if (key === 'ctrl-c') this.backend.interrupt();
      return;
    }
    this.quit();
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

  private approval(event: Of<'approval'>): void {
    const choices = event.choices ?? [];
    this.commit('', ...(event.question ?? '').split('\n'), '', ...choices.map((choice) => `  ${choice.key}  ${choice.label}`));
    void this.ask(event.hint).then((answer) => {
      if (answer) this.backend.approvalReply(event.id, approvalAnswer(answer.line, choices));
    });
  }

  private async question(event: Of<'question'>): Promise<void> {
    const keys = (event.keys ?? []).map(String);
    this.commit('', ...(event.text ?? '').split('\n'));
    const hint = `Type ${keys.join(', ')} and Enter, or Esc to skip`;
    for (;;) {
      const answer = await this.ask(hint);
      if (!answer) return;
      const typed = (answer.line ?? '').trim();
      const key = keys.find((candidate) => candidate.toLowerCase() === typed.toLowerCase());
      // Esc and an empty line dismiss it. Anything else is asked again here:
      // the backend would answer a key it did not offer with an error.
      if (answer.line === null || typed === '') return this.backend.answer(event.id, '');
      if (key !== undefined) return this.backend.answer(event.id, key);
      this.commit(this.style.yellow(`"${typed}" is not one of ${keys.join(', ')}. Type one of them, or press Esc to skip.`));
    }
  }

  /** One question in the box. null when an interrupt took it back or the session is over. */
  private async ask(hint: string | undefined): Promise<{ line: string | null } | null> {
    const slot = { abandoned: false };
    this.openAsk = slot;
    const line = await this.screen!.ask({ hint });
    if (this.openAsk === slot) this.openAsk = null;
    return slot.abandoned || this.finished ? null : { line };
  }

  /** The panels, as text, only when they changed. */
  private snapshot(kind: string, block: string): void {
    if (this.blocks.get(kind) === block) return;
    this.blocks.set(kind, block);
    if (block) this.commit('', ...block.split('\n'));
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
