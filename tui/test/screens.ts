// The screen the screen tests drive: the hand-written renderer in src/live,
// or the Ink screen the app uses when TMT_TEST_SCREEN is `ink` (the `ink`
// project in vitest.config.ts runs those files a second time). The two keep
// the same contract and the same terminal behaviour (the scrollback, the
// resizes, the keys), so those scenarios hold for both; what each draws in the
// live frame differs (the Python UI's look against Claude Code's), so the
// scenarios about that look are skipped on the Ink screen (`INK`) and
// test/ink-frame.test.ts holds the Ink screen's own.

import { composeFrame as composeInk } from '../src/ink/layout.js';
import { createInkSessionScreen } from '../src/ink/screen.js';
import { composeFrame as composeLive } from '../src/live/frame.js';
import { createSessionScreen as createLive } from '../src/live/session.js';

const ink = process.env['TMT_TEST_SCREEN'] === 'ink';

/** Whether the screen under test is the Ink one. */
export const INK = ink;

export const createSessionScreen: typeof createLive = ink ? createInkSessionScreen : createLive;
export const composeFrame: typeof composeLive = ink ? composeInk : composeLive;
