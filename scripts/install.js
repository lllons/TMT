#!/usr/bin/env node
/**
 * Putting TMT itself on the machine: the git checkout the launcher runs.
 *
 * **This is called from the launcher, on first run, and not from an npm
 * install hook.** It used to be `postinstall.js` and npm used to run it, and
 * that stopped being true: npm 11.19 and later refuse install scripts unless
 * the user opts in with `--allow-scripts`, so `npm install -g tmtcode`
 * cheerfully reported "added 1 package" and left no TMT anywhere on the disk.
 * The command was then a launcher pointing at nothing.
 *
 * So the setup moved to where it cannot be skipped: the first time somebody
 * runs `tmtcode`, this clones TMT into ~/.tmtcode and the launcher carries on
 * into it. Nothing about the result changed -- same directory, same real git
 * checkout, same auto-update afterwards. What changed is that it no longer
 * depends on a package manager's policy about running other people's scripts.
 *
 * It is still runnable by hand for anyone who wants the clone made now:
 *
 *     node scripts/install.js
 */

"use strict";

const { spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const REPO = "https://github.com/lllons/TMT.git";
const DEFAULT_HOME = path.join(os.homedir(), ".tmtcode");
const MARKER = "TMT.py";

function home() {
  return process.env.TMT_HOME || DEFAULT_HOME;
}

function log(msg) {
  console.log("[tmtcode] " + msg);
}

function warn(msg) {
  console.warn("[tmtcode] " + msg);
}

function run(cmd, args, opts) {
  return spawnSync(cmd, args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
    ...opts,
  });
}

function which(cmd) {
  const checker = process.platform === "win32" ? "where" : "which";
  return run(checker, [cmd]).status === 0;
}

function findPython() {
  const candidates =
    process.platform === "win32"
      ? ["py", "python", "python3"]
      : ["python3", "python"];
  for (const cmd of candidates) {
    if (!which(cmd) && process.platform !== "win32") continue;
    const r = run(cmd, [
      "-c",
      "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)",
    ]);
    if (r.status === 0) return cmd;
  }
  return null;
}

function isTmtCheckout(dir) {
  return (
    fs.existsSync(path.join(dir, MARKER)) &&
    fs.existsSync(path.join(dir, "agent_config.py")) &&
    fs.existsSync(path.join(dir, ".git"))
  );
}

/**
 * Make sure a TMT checkout exists at HOME. Returns {ok, home, reason}.
 *
 * Never destroys anything: a directory that already holds something other
 * than TMT is refused and named, not cloned over. An existing checkout is
 * left exactly as it is -- updating it is TMT's own job, on its launch
 * screen, and doing it here would mean a fetch on every single start.
 */
function ensure(options) {
  const quiet = Boolean(options && options.quiet);
  const target = (options && options.home) || home();

  if (isTmtCheckout(target)) {
    return { ok: true, home: target, reason: "" };
  }
  if (fs.existsSync(target) && fs.readdirSync(target).length) {
    return {
      ok: false,
      home: target,
      reason:
        target +
        " exists but is not a TMT checkout.\n" +
        "Move or remove it, then run tmtcode again.",
    };
  }
  if (!which("git")) {
    return {
      ok: false,
      home: target,
      reason:
        "git is not on PATH, so TMT cannot be downloaded.\n" +
        "Install git from https://git-scm.com/downloads and run tmtcode again.",
    };
  }

  if (!quiet) {
    // Said out loud because it takes twenty seconds. Silence here reads as a
    // command that has hung on its very first run, which is the worst first
    // impression this program could make.
    log("First run: installing TMT into " + target + " ...");
  }
  const parent = path.dirname(target);
  if (!fs.existsSync(parent)) fs.mkdirSync(parent, { recursive: true });
  const cloned = run(
    "git",
    ["clone", "--depth", "1", "--branch", "main", REPO, target],
    { timeout: 300000, stdio: quiet ? ["ignore", "pipe", "pipe"] : "inherit" }
  );
  if (cloned.status !== 0 || !isTmtCheckout(target)) {
    return {
      ok: false,
      home: target,
      reason:
        "TMT could not be downloaded. Check your network and try again.\n" +
        String(cloned.stderr || "").trim(),
    };
  }
  if (!quiet) log("Installed. Starting TMT.");
  return { ok: true, home: target, reason: "" };
}

/**
 * requests and rich, if pip will have them. Entirely optional: they add live
 * streaming and colour and TMT falls back without them, so a failure here is
 * not worth a word on screen.
 */
function extras(python) {
  if (!python) return;
  run(python, ["-m", "pip", "install", "--user", "-q", "requests", "rich"], {
    timeout: 90000,
  });
}

function main() {
  const outcome = ensure({});
  if (!outcome.ok) {
    warn(outcome.reason);
    // Never a non-zero exit. This is also wired as a plain script somebody
    // may run by hand, and a failure here is something to read rather than
    // something to break a shell pipeline over.
    process.exit(0);
  }
  const python = findPython();
  if (!python) {
    warn("Python 3.8+ was not found on PATH. TMT needs it to run.");
    warn("Install it from https://www.python.org/downloads/");
  } else {
    extras(python);
  }
  log("Ready. From any project directory run:  tmtcode");
  log("Install location: " + outcome.home);
}

module.exports = { ensure, findPython, extras, home, isTmtCheckout, DEFAULT_HOME, REPO };

if (require.main === module) {
  main();
}
