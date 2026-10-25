// The entry point `agent_tsui.launch` runs as `tui/dist/main.js`. It owns the
// real process; everything else is in app.ts.

import { fileURLToPath } from 'node:url';
import { runApp } from './app.js';

// `agent_tsui.launch` sets both. The defaults are for running this file by
// hand from a checkout, where TMT.py sits two folders above dist/.
const python = process.env['TMT_PYTHON'] || (process.platform === 'win32' ? 'python' : 'python3');
const entry = process.env['TMT_ENTRY'] || fileURLToPath(new URL('../../TMT.py', import.meta.url));

let code = 1;
try {
  code = await runApp({
    stdin: process.stdin,
    stdout: process.stdout,
    stderr: process.stderr,
    python,
    entry,
    args: process.argv.slice(2),
  });
} catch (error) {
  process.stderr.write(`TMT: the TypeScript UI failed: ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`);
}
process.exit(code);
