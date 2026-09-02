# Install

**Recommended — two commands:**

```bash
npm install -g tmtcode
tmtcode
```

That is the whole of it. There is no separate clone and no pip step. The install puts
the `tmtcode` command on your PATH; the first time you run it, it downloads TMT into
`~/.tmtcode` — about twenty seconds, and it says so while it happens — and then starts
normally and asks for your API key.

**The setup is on first run and not in an install hook, deliberately.** npm 11.19 and
later refuse a package's install scripts unless you opt in, so a package that did its
setup in a `postinstall` would report "added 1 package" and leave nothing installed.
TMT has no install scripts at all, which means nothing your npm can block.

Needs **Node 14+** to install, and **Python 3.8+** and **git** on PATH to run — TMT is
a Python program, and the npm package is a launcher rather than a copy of it.

**The live install is a git checkout in your home directory, never inside
`node_modules`.** That is deliberate and it is what makes the npm path a first-class
one rather than a convenience: `node_modules` is wiped and rebuilt by npm whenever it
feels like it, and everything TMT owns — your API key, its logs, the model you chose,
its git identity — lives in the installation directory. A checkout in `~/.tmtcode`
survives every reinstall, and it is also what keeps the built-in updater working, since
that fast-forwards a real repository.

Put it somewhere else with `TMT_HOME`, set when you install and when you run:

```bash
TMT_HOME=~/tools/TMT npm install -g tmtcode
```

To remove it: `npm uninstall -g tmtcode` takes the command off your PATH, and
`~/.tmtcode` is TMT itself — delete that directory and your saved key goes with it.

**From a clone, with pip:**

```bash
git clone https://github.com/lllons/TMT.git
cd TMT
pip install -e .                 # puts `tmtcode` on PATH
pip install -e ".[live]"         # optional: adds requests and rich for streaming and colour
```

The agent itself needs nothing beyond the standard library; `requests` and `rich` only
add live streaming and colour, and TMT falls back without them. The npm install offers
to add the same two, and shrugs and carries on if pip is not there.

After installing, leave the clone where it is and run `tmtcode` from wherever your work
is. The clone is TMT's home, not your project.

Without installing, a clone still runs directly, and from anywhere:

```bash
python /path/to/TMT/TMT.py                    # the current directory is the project
python /path/to/TMT/TMT.py ~/Projects/MyWebsite
```

Windows: `py`. macOS/Linux: `python3`.

## If `tmtcode` does not start

**`ModuleNotFoundError: No module named 'TMT'`** — something else called `tmtcode` is
on your PATH ahead of this one, almost always an old `pip install -e .` whose clone has
since been moved or deleted. The traceback names it: look for a path ending in
`Scripts\tmtcode.exe` or `bin/tmtcode`. Remove it and the npm command takes over:

```bash
pip uninstall -y tmtcode
```

**`tmtcode: TMT is not installed yet and could not be installed now`** — the first run
tried to download TMT and could not. The message says which of the three reasons it
was: no git on PATH, no network, or something already sitting at `~/.tmtcode` that is
not a TMT checkout. TMT never clones over a directory it did not make.

**Nothing happens, or the command is not found** — the npm global bin directory is not
on your PATH. `npm bin -g` (older npm) or `npm prefix -g` prints where it puts
commands.

You can always do the download by hand; the launcher will use whatever is there:

```bash
git clone https://github.com/lllons/TMT.git ~/.tmtcode
```

## Uninstalling

Settings → `Danger Zone` → `Uninstall TMT`, then type `UNINSTALL`. It works the
same way whichever install you have.

It removes every file TMT installed and the checkout they live in, then takes the
`tmtcode` command off your PATH — `npm uninstall -g tmtcode` and
`pip uninstall -y tmtcode`, whichever of the two is actually there.

**It keeps everything git ignores.** Your own notes, TMT's saved API key, its logs: none
of that was shipped by TMT and none of it is TMT's to delete. The screen shows both
counts before anything happens, and the report afterwards names the directory so you can
remove it yourself if you want nothing left at all.

The word has to be typed rather than a key pressed, the cursor opens on `Back`, and the
screen states what it would take before it takes it. This is the one action in TMT that
removes TMT.

By hand it is the same two steps:

```bash
npm uninstall -g tmtcode      # or: pip uninstall -y tmtcode
rm -rf ~/.tmtcode             # or wherever your checkout is
```

## `tmtcode` not recognised

The command is installed, but the directory pip put it in is not on your PATH. Find
that directory:

```bash
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
```

It is `Scripts` on Windows and `bin` on macOS and Linux, under the Python or virtual
environment you installed into. Add it to PATH, or use either fallback — both take the
same arguments and pick the project directory the same way:

```bash
python -m TMT                     # anywhere, once installed
python /path/to/TMT/TMT.py        # anywhere, straight from a clone
```

If you installed into a virtual environment, `tmtcode` exists only while that
environment is active.

`tmtcode --help` prints the arguments.

---

[← Back to the README](../README.md)
