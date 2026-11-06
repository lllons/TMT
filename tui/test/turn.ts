// A realistic turn for the fake backend: the user asks for a change, the model
// reads a file, edits it, asks to run a command (an approval), runs two (one
// passes, one fails), keeps a plan, and streams an answer with markdown in it.
// The steps pause where a test wants to look at the screen: the approval is
// open until it is answered, and the reply is half streamed until the test
// sends a `resize` (which the fake takes as its cue).

import { prompt, QUIT, READY, SESSION, TASK, type Step } from './run.js';

export const ASK = 'Add a --verbose flag to cli.py and run the tests';

const steps = (...status: string[]) => [
  { id: 'S1', position: 1, title: 'Read cli.py', status: status[0] },
  { id: 'S2', position: 2, title: 'Add the --verbose flag', status: status[1] },
  { id: 'S3', position: 3, title: 'Run the tests and the linter', status: status[2] },
  { id: 'S4', position: 4, title: 'Write up what changed', status: status[3] },
];

const call = (id: number, action: string, label: string, target: string, result: Step) => [
  { type: 'tool_start', id, action, label, target },
  { type: 'tool_end', id, action, status: 'done' },
  result,
];

/** What the model streams, in the order and the pieces it arrives in. */
export const REPLY_PARTS = [
  'I added **--verbose** to `cli.py` and ran the checks.\n\n## What changed\n\n',
  '- The flag is read in `parse_args`, see the [argparse docs](https://docs.python.org/3/library/argparse.html).\n',
  '- Logging now goes to *stderr* when it is set, and nothing changes without it.\n',
  '1. `npm test` passes: 14 tests.\n2. `npm run lint` fails on `cli.py`, a line is too long.\n\n',
  '> The lint failure was there before this change.\n\n',
  '```python\nparser.add_argument("--verbose", action="store_true")\n```\n',
];
export const REPLY = REPLY_PARTS.join('');

const APPROVAL = {
  type: 'approval', id: 1, action: 'bash', pattern: 'npm test',
  question: 'TMT wants to run a command that needs your approval.\n\n    npm test\n    in .\n\nThe rule this would be remembered as: npm test',
  hint: 'Type y and Enter to run it once, a to allow `npm test` in this project from now on, or anything else to refuse:',
  choices: [{ key: 'y', label: 'Run it once' }, { key: 'a', label: 'Allow `npm test` in this project from now on' }, { key: 'n', label: 'Refuse' }],
};

/**
 * The session: ready, the header, the first prompt, the turn in three parts, and a quit.
 * The last part waits for `release`: a `resize` by default, which a test that wants to
 * resize the window while the turn runs replaces with an `interrupt`.
 */
export const turn = (release: Step = { type: 'resize' }): Step[] => [
  { on: { type: 'hello' }, send: [READY, SESSION, { type: 'plan', steps: [] }, prompt('Describe your first task')] },
  { on: { type: 'submit', text: ASK }, send: [
    TASK(ASK), { type: 'turn_start', task: ASK }, { type: 'status', state: 'thinking', label: '', progress: null },
    { type: 'event', kind: 'progress', message: 'Looking at the project first.', stage: '', level: 0, detail: {} },
    { type: 'plan', steps: steps('in_progress', 'pending', 'pending', 'pending') },
    ...call(1, 'read_file', 'Read File', 'cli.py', { type: 'event', kind: 'file_read', message: 'Read cli.py', stage: '', level: 1, detail: { lines: 120, paths: ['cli.py'] } }),
    { type: 'status', state: 'working', label: 'Read File', progress: 20 },
    { type: 'plan', steps: steps('completed', 'in_progress', 'pending', 'pending') },
    ...call(2, 'patch_file', 'Patch File', 'cli.py', { type: 'event', kind: 'file_edit', message: 'Edited cli.py', stage: '', level: 2, detail: { added: 4, removed: 1, paths: ['cli.py'] } }),
    { type: 'plan', steps: steps('completed', 'completed', 'in_progress', 'pending') },
    { type: 'tool_start', id: 3, action: 'bash', label: 'Bash', target: 'npm test' },
    APPROVAL,
  ] },
  { on: { type: 'approval_reply', id: 1, answer: 'y' }, send: [
    { type: 'tool_end', id: 3, action: 'bash', status: 'done' },
    { type: 'event', kind: 'test', message: 'Ran npm test: 14 passed', stage: '', level: 2, detail: { status: 0 } },
    ...call(4, 'bash', 'Bash', 'npm run lint', { type: 'event', kind: 'command', message: 'Ran npm run lint: cli.py:42 line too long (101 > 100)', stage: '', level: 2, detail: { status: 1 } }),
    { type: 'event', kind: 'success', message: 'The tests pass.', stage: '', level: 1, detail: {} },
    { type: 'meter', tokens_in: 14500, tokens_out: 168, tokens_out_exact: false, lines_added: 4, lines_removed: 1, agent_tokens: 0, turn_tokens: 1240, elapsed: 14 },
    { type: 'tool_start', id: 5, action: 'bash', label: 'Bash', target: 'npm test -- --coverage' },
    ...REPLY_PARTS.map((text) => ({ type: 'delta', text })),
  ] },
  { on: release, send: [
    { type: 'tool_end', id: 5, action: 'bash', status: 'done' },
    { type: 'event', kind: 'test', message: 'Ran npm test -- --coverage: 14 passed', stage: '', level: 2, detail: { status: 0 } },
    { type: 'plan', steps: steps('completed', 'completed', 'completed', 'completed') },
    { type: 'status', state: 'idle', label: '', progress: null },
    { type: 'final', text: REPLY },
    { type: 'turn_end', task: ASK, answer: REPLY, outcome: '', suggestion: 'Fix the long line' },
    prompt('Fix the long line', { tokens_in: 14500, tokens_out: 168, tokens_out_exact: true, lines_added: 4, lines_removed: 1 }),
  ] },
  QUIT,
];

export const TURN = turn();
