#!/usr/bin/env node
/**
 * postinstall for the tmtcode npm package.
 *
 * Goal: after `npm install -g tmtcode` the user only needs to type
 * `tmtcode` — no separate clone or pip step.
 *
 * What this script does:
 *   1. Picks a stable install home:  ~/.tmtcode  (override with TMT_HOME)
 *   2. Ensures that directory is a real git checkout of lllons/TMT
 *      so the existing auto-updater and INSTALL_DIR logic keep working.
 *   3. Verifies Python 3.8+ is available (required by TMT itself).
 *   4. Optionally installs the lightweight "live" extras if pip is free.
 *
 * The first real run of `tmtcode` still shows the existing API-key
 * setup screen (agent_setup.py). Nothing extra is required.
 */

"use strict";

const { spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const REPO = "https://github.com/lllons/TMT.git";
const DEFAULT_HOME = path.join(os.homedir(), ".tmtcode");
const HOME = process.env.TMT_HOME || DEFAULT_HOME;

function log(msg) {
  console.log("[tmtcode] " + msg);
}

function warn(msg) {
  console.warn("[tmtcode] " + msg);
}

function run(cmd, args, opts) {
  const r = spawnSync(cmd, args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
    ...opts,
  });
  return r;
}

function which(cmd) {
  const checker = process.platform === "win32" ? "where" : "which";
  const r = run(checker, [cmd]);
  return r.status === 0;
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

function hasGit() {
  return which("git");
}

function isTmtCheckout(dir) {
  return (
    fs.existsSync(path.join(dir, "TMT.py")) &&
    fs.existsSync(path.join(dir, "agent_config.py")) &&
    fs.existsSync(path.join(dir, ".git"))
  );
}

function ensureCheckout() {
  if (isTmtCheckout(HOME)) {
    log("Using existing install at " + HOME);
    // Soft update attempt — never fail the whole install over this.
    const r = run("git", ["-C", HOME, "fetch", "--quiet", "origin"], {
      timeout: 30000,
    });
    if (r.status === 0) {
      const ff = run("git", ["-C", HOME, "merge", "--ff-only", "--quiet", "origin/main"], {
        timeout: 15000,
      });
      if (ff.status === 0) log("Updated to latest main.");
      else log("Local changes or non-ff state — left as-is (safe).");
    }
    return true;
  }

  if (!hasGit()) {
    warn("git is not on PATH. Cannot clone TMT into " + HOME);
    warn("Install git, then re-run:  npm install -g tmtcode");
    return false;
  }

  // Fresh install.
  if (fs.existsSync(HOME)) {
    // Directory exists but is not a valid checkout — do not destroy it.
    warn(HOME + " exists but is not a TMT checkout.");
    warn("Move or remove it, then re-run:  npm install -g tmtcode");
    return false;
  }

  log("Cloning TMT into " + HOME + " …");
  const parent = path.dirname(HOME);
  if (!fs.existsSync(parent)) fs.mkdirSync(parent, { recursive: true });

  const r = run("git", ["clone", "--depth", "1", "--branch", "main", REPO, HOME], {
    timeout: 120000,
    stdio: "inherit",
  });
  if (r.status !== 0) {
    warn("git clone failed. Check network / git and try again.");
    return false;
  }
  log("Clone complete.");
  return true;
}

function ensurePython() {
  const py = findPython();
  if (!py) {
    warn("Python 3.8+ was not found on PATH.");
    warn("TMT needs Python. Install it from https://www.python.org/downloads/");
    warn("After installing Python, just run:  tmtcode");
    return null;
  }
  log("Found Python: " + py);
  return py;
}

function optionalLiveExtras(python) {
  // Non-fatal. requests + rich improve streaming/colour; TMT works without them.
  try {
    const r = run(python, ["-m", "pip", "install", "--user", "-q", "requests", "rich"], {
      timeout: 90000,
    });
    if (r.status === 0) log("Optional live extras (requests, rich) installed.");
    else log("Optional live extras skipped (pip not available or failed).");
  } catch (_) {
    log("Optional live extras skipped.");
  }
}

function main() {
  log("Setting up TMT…");

  const ok = ensureCheckout();
  if (!ok) {
    // Do not fail the npm install hard — the bin still exists and will
    // print a clear error if the home is missing.
    warn("Setup incomplete. Run `tmtcode` later for details, or re-install.");
    process.exit(0);
  }

  const python = ensurePython();
  if (python) optionalLiveExtras(python);

  log("");
  log("Done. From any project directory run:");
  log("");
  log("    tmtcode");
  log("");
  log("First launch will ask for your OpenRouter API key (saved under " + HOME + ").");
  log("Install location: " + HOME);
  log("Override with env TMT_HOME if needed.");
}

main();
