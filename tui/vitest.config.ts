import { defineConfig } from 'vitest/config';

// The screen tests run twice: against the hand-written renderer (src/live)
// and, in the `ink` project, against the Ink screen the app uses (src/ink).
// test/screens.ts picks the screen from TMT_TEST_SCREEN.
const SCREEN_TESTS = ['test/frame.test.ts', 'test/live.test.ts', 'test/look.test.ts', 'test/mouse.test.ts'];

export default defineConfig({
  test: {
    projects: [
      { test: { name: 'tui', include: ['test/**/*.test.ts'] } },
      { test: { name: 'ink', include: SCREEN_TESTS, env: { TMT_TEST_SCREEN: 'ink' } } },
    ],
  },
});
