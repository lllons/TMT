#!/usr/bin/env node
/**
 * tmtcode launcher (npm global bin).
 *
 * Resolves the real TMT installation directory (created by postinstall)
 * and hands off to the Python entry point. All of TMT's own state
 * (.tmt_key, logs, model choice, auto-update, etc.) lives there.
 *
 * The directory you run this from (or pass as an argument) is the
 * *project* workspace — that is unchanged from the pip install flow.
 */

"use strict";

const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const INSTALL_MARKER = "TMT.py";
const DEFAULT_HOME = path.join(os.homedir(), ".tmtcode");

function installDir() {
  // Allow override for testing / advanced users.
  if (process.env.TMT_HOME && fs.existsSync(path.join(process.env.TMT_HOME, INSTALL_MARKER))) {
    return process.env.TMT_HOME;
  }
  if (fs.existsSync(path.join(DEFAULT_HOME, INSTALL_MARKER))) {
    return DEFAULT_HOME;
  }
  // Fallback: same directory as this launcher (useful during local dev
  // when someone runs the bin from a full clone without postinstall).
  const here = path.resolve(__dirname, "..");
  if (fs.existsSync(path.join(here, INSTALL_MARKER))) {
    return here;
  }
  return null;
}

function findPython() {
  const candidates =
    process.platform === "win32"
      ? ["py", "python", "python3"]
      : ["python3", "python"];
  for (const cmd of candidates) {
    try {
      const { spawnSync } = require("child_process");
      const r = spawnSync(cmd, ["-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)"], {
        encoding: "utf8",
        windowsHide: true,
      });
      if (r.status === 0) return cmd;
    } catch (_) {
      /* try next */
    }
  }
  return null;
}

function main() {
  const home = installDir();
  if (!home) {
    console.error(
      [
        "tmtcode: installation not found.",
        "",
        "Expected TMT at: " + DEFAULT_HOME,
        "Re-run:  npm install -g tmtcode",
        "Or set TMT_HOME to a directory that contains TMT.py",
      ].join("\n")
    );
    process.exit(1);
  }

  const entry = path.join(home, "TMT.py");
  const python = findPython();
  if (!python) {
    console.error(
      [
        "tmtcode: Python 3.8+ is required but was not found on PATH.",
        "",
        "Install Python from https://www.python.org/downloads/",
        "then run:  npm install -g tmtcode",
      ].join("\n")
    );
    process.exit(1);
  }

  const args = [entry, ...process.argv.slice(2)];
  const child = spawn(python, args, {
    stdio: "inherit",
    windowsHide: true,
    env: {
      ...process.env,
      // Make the install dir explicit for any helper that wants it.
      TMT_HOME: home,
    },
  });

  child.on("error", (err) => {
    console.error("tmtcode: failed to start Python:", err.message);
    process.exit(1);
  });

  child.on("exit", (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    process.exit(code == null ? 1 : code);
  });
}

main();
