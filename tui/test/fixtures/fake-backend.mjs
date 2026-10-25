// A stand-in for `TMT.py --backend-stdio`, in plain Node, driven by a script:
//
//   node fake-backend.mjs --backend-stdio <script.json> <received.jsonl>
//
// The script is a JSON array of steps, run in order. A step with `on` waits
// for a message from the front end whose fields include everything in `on`;
// a step without it runs at once. A step then does, in this order:
//   delay   milliseconds to wait
//   stderr  text to write to stderr (first, as a real backend's traceback is)
//   send    events to write to stdout (`v: 1` is added; a string is written as is)
//   exit    end the process with this code, without any `exit` event
// Every message received, and the arguments, are appended to <received.jsonl>
// so a test can read what the front end said.

import { appendFileSync, readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';

const [, , flag, scriptPath, receivedPath, ...rest] = process.argv;
const steps = JSON.parse(readFileSync(scriptPath, 'utf8'));
const record = (value) => appendFileSync(receivedPath, JSON.stringify(value) + '\n');
record({ argv: [flag, scriptPath, receivedPath, ...rest] });

let index = 0;
let running = false;

const matches = (wanted, message) => Object.entries(wanted).every(([key, value]) => JSON.stringify(message[key]) === JSON.stringify(value));
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const write = (text) => new Promise((resolve) => process.stdout.write(text, resolve));

async function run(step) {
  if (step.delay) await pause(step.delay);
  if (step.stderr) await new Promise((resolve) => process.stderr.write(step.stderr, resolve));
  for (const event of step.send ?? []) {
    await write((typeof event === 'string' ? event : JSON.stringify({ v: 1, ...event })) + '\n');
  }
  if (step.exit !== undefined) {
    await new Promise((resolve) => process.stderr.write('', resolve));
    process.exit(step.exit);
  }
}

// Steps without `on` run straight away; the loop stops at the first that waits.
async function advance() {
  if (running) return;
  running = true;
  while (index < steps.length && !steps[index].on) await run(steps[index++]);
  running = false;
}

async function receive(message) {
  record(message);
  // The loop may be mid-step; wait for it to settle before matching.
  while (running) await pause(1);
  const step = steps[index];
  if (step && step.on && matches(step.on, message)) {
    index += 1;
    running = true;
    await run(step);
    running = false;
    await advance();
  }
}

const lines = createInterface({ input: process.stdin });
let chain = Promise.resolve();
lines.on('line', (line) => {
  let message;
  try {
    message = JSON.parse(line);
  } catch {
    record({ unparsed: line });
    return;
  }
  chain = chain.then(() => receive(message));
});
lines.on('close', () => chain.then(() => process.exit(0)));
await advance();
