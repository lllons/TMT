"""Tests for the npm install path: the package, the launcher, the setup.

TMT is a Python program and this is the one part of it written in JavaScript,
so almost nothing here can be tested by running it -- the suite has no node and
must not need one. What it can do is check the things that are wrong on
somebody else's machine and silent on this one, and every test here is one of
those:

  The two halves must agree about where TMT lives.  `scripts/install.js`
      clones into a directory and `bin/tmtcode.js` looks for TMT in one, and
      they are two separate literals in two separate files. Drift there
      installs perfectly and then says "installation not found" forever.

  The package must stay thin.  `files` ships the launcher and nothing else; the
      Python tree is fetched as a git checkout on first run, because a copy
      under `node_modules` is wiped by npm and would take the user's API key,
      logs and settings with it.

  There must be no install script to block.  npm 11.19 and later refuse a
      package's install scripts unless the user opts in, and TMT shipped with
      a postinstall that did the whole setup -- so `npm install -g tmtcode`
      reported success and left no TMT on the disk. The setup happens on first
      launch now, where no package manager's policy can skip it.

  The setup must never destroy anything.  It runs unattended against a
      directory in somebody's home. It may clone; it may not remove, reset,
      clean or force. This is
      `test_the_updater_never_runs_a_destructive_command`'s rule, applied to the
      other program in this repository that touches git.

  The version must be one number.  Under an npm install there is no installed
      distribution, so `importlib.metadata` finds nothing and the version TMT
      reports is `agent_menu.FALLBACK_VERSION`. That makes the fallback the
      REAL version for npm users rather than a last resort, and it has to agree
      with what npm published.

`node --check` is run over both scripts when node is on PATH, and skipped when
it is not. That is the one thing here that can catch a syntax error, and a
suite that required node to run would be a worse trade than a check that is
sometimes not made -- so it says which happened rather than pretending.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import agent_config
import agent_menu

INSTALL_DIR = Path(agent_config.__file__).resolve().parent
PACKAGE_JSON = INSTALL_DIR / "package.json"
LAUNCHER = INSTALL_DIR / "bin" / "tmtcode.js"
SETUP = INSTALL_DIR / "scripts" / "install.js"

# What the launcher looks for to decide a directory really is TMT, and what
# the setup runs to make one. Both are the entry point pyproject already
# declares, which is the point: three install paths, one program.
MARKER = "TMT.py"

# Where TMT installs itself when npm puts it there. Written out once here and
# compared against both scripts, because it appears in both of them.
HOME_EXPRESSION = 'path.join(os.homedir(), ".tmtcode")'


def package():
    """package.json, parsed. A syntax error here breaks `npm install`."""
    return json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))


def pyproject():
    """The [project] table, however this Python can read TOML."""
    text = (INSTALL_DIR / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib
    except ImportError:
        version = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        url = re.search(r'^Homepage\s*=\s*"([^"]+)"', text, re.M)
        return {"version": version.group(1) if version else "",
                "urls": {"Homepage": url.group(1) if url else ""},
                "scripts": {"tmtcode": "TMT:main"}}
    return tomllib.loads(text).get("project", {})


# --- the package ------------------------------------------------------------

def test_the_package_offers_the_same_command_the_python_install_offers():
    """`npm install -g tmtcode` and `pip install -e .` have to put the same
    word on PATH. Two names for one program is two things to document, and
    the whole promise of the npm path is that it lands on the same `tmtcode`
    every other page of the README talks about."""
    data = package()
    assert data["name"] == "tmtcode", data["name"]
    assert list(data["bin"]) == ["tmtcode"], data["bin"]
    assert "tmtcode" in pyproject().get("scripts", {})


def test_the_bin_the_package_declares_is_a_file_that_exists():
    """npm links whatever this names. A path that is wrong ships a package
    whose command cannot start, and nothing before publication says so."""
    data = package()
    target = INSTALL_DIR / data["bin"]["tmtcode"]
    assert target.is_file(), target
    assert target == LAUNCHER, target
    first = target.read_text(encoding="utf-8").splitlines()[0]
    assert first == "#!/usr/bin/env node", first


def test_the_package_ships_the_launcher_and_not_the_python_tree():
    """The thin-package rule, which is the load-bearing decision of this whole
    feature. What npm installs is a launcher; what runs is a git checkout in
    the user's home. Shipping the Python here instead would put TMT under
    `node_modules` -- which npm wipes and rebuilds at will, taking the API
    key, the logs, the chosen model and the git identity with it."""
    shipped = package()["files"]
    assert set(shipped) == {"bin/", "scripts/", "LICENSE", "README.md"}, shipped
    for entry in shipped:
        assert (INSTALL_DIR / entry.rstrip("/")).exists(), entry
    for forbidden in ("TMT.py", "*.py", "agent_config.py", "testing/",
                      "pyproject.toml"):
        assert forbidden not in shipped, forbidden


def test_the_npm_version_is_the_version_tmt_reports_under_it():
    """Under npm there is no pip distribution to ask, so
    `importlib.metadata.version("tmtcode")` raises and TMT falls back to
    `agent_menu.FALLBACK_VERSION`. That makes the fallback the version an npm
    user actually sees -- so these three numbers are one number, and this is
    the test that says so when the next release bumps only two of them."""
    assert package()["version"] == pyproject().get("version"), package()["version"]
    assert package()["version"] == agent_menu.FALLBACK_VERSION


def test_the_package_points_at_this_repository():
    """The setup clones from a URL and package.json advertises one. A
    package that installed a different repository than it links to would be
    the worst kind of wrong, so both are checked against pyproject's."""
    data = package()
    home = pyproject().get("urls", {}).get("Homepage")
    assert data["homepage"] == home, (data["homepage"], home)
    assert data["repository"]["url"].endswith(home.split("://")[-1] + ".git"), data
    source = SETUP.read_text(encoding="utf-8")
    clone_from = re.search(r'const REPO = "([^"]+)"', source)
    assert clone_from, source[:200]
    assert clone_from.group(1) == home + ".git", clone_from.group(1)


# --- the launcher and the setup agree ---------------------------------------

def test_both_scripts_agree_where_tmt_lives():
    """The one drift that installs cleanly and is broken forever after: the
    setup clones into one directory and the launcher looks in another,
    so `npm install -g tmtcode` succeeds and every `tmtcode` afterwards says
    "installation not found"."""
    for script in (LAUNCHER, SETUP):
        source = script.read_text(encoding="utf-8")
        assert HOME_EXPRESSION in source, script.name
        assert "TMT_HOME" in source, script.name
    # And the fallback home is never inside node_modules, however it is
    # spelled -- that is the decision the whole design rests on.
    for script in (LAUNCHER, SETUP):
        assert "node_modules" not in script.read_text(encoding="utf-8"), script.name


def test_the_launcher_looks_for_the_entry_point_this_repository_has():
    """It decides a directory is TMT by finding a file in it, and runs that
    file with Python. Both are `TMT.py`, which is also what pyproject's
    console script imports -- so a rename would have to pass here."""
    source = LAUNCHER.read_text(encoding="utf-8")
    assert 'const INSTALL_MARKER = "%s"' % MARKER in source, source[:400]
    assert '"%s"' % MARKER in source
    assert (INSTALL_DIR / MARKER).is_file()
    # The Python it looks for is the Python TMT needs, stated as a version
    # test rather than as a name: `python3` on a machine that has 3.7 is not
    # a Python this program can use.
    assert "sys.version_info >= (3, 8)" in source
    assert "requires-python" not in source          # not the place for it
    requires = (INSTALL_DIR / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.8"' in requires


def test_the_setup_checks_for_the_same_python_the_launcher_does():
    """Two version tests, and they have to be the same test. A setup that
    accepted 3.7 would report success and leave a launcher that then refuses
    to start."""
    assert "sys.version_info >= (3, 8)" in SETUP.read_text(encoding="utf-8")


# --- what the setup may never do --------------------------------------------

def test_the_setup_never_runs_a_destructive_command():
    """It runs unattended, at install time, against a directory in somebody's
    home. The same rule the updater keeps and for the same reason: it may
    clone, fetch and fast-forward, and it may not remove, reset, clean, force
    or plain-`pull` -- a pull can merge, and a merge nobody asked for is not
    something an install should be capable of.

    Read out of the script's own source, exactly as
    `test_the_updater_never_runs_a_destructive_command` reads the updater's.
    """
    source = SETUP.read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith(("*", "//", "/*")))
    for forbidden in ('"reset"', '"clean"', '"--hard"', '"--force"', '"-f"',
                      '"pull"', '"merge"', "rmSync", "rmdirSync", "unlinkSync",
                      "rimraf", "rm -rf"):
        assert forbidden not in code, (forbidden, code)
    # One git command, and it is the one that creates rather than changes.
    # Updating an existing checkout is TMT's own job on its launch screen;
    # doing it here would mean a fetch on every single start.
    assert '"clone"' in code


def test_the_setup_refuses_a_directory_that_is_not_a_tmt_checkout():
    """`~/.tmtcode` might be somebody's own directory. The script has to leave
    it alone and say so rather than clone over it, and it decides what a TMT
    checkout is by looking for TMT's own files plus a .git."""
    source = SETUP.read_text(encoding="utf-8")
    assert "is not a TMT checkout" in source, source[:200]
    for name in (MARKER, "agent_config.py", ".git"):
        assert '"%s"' % name in source, name


def test_the_package_declares_no_install_script_to_be_blocked():
    """THE BUG THIS EXISTS FOR, and it was reported from a real machine.

    npm 11.19 and later refuse a package's install scripts unless the user
    opts in. TMT's whole setup WAS a postinstall, so `npm install -g tmtcode`
    said "added 1 package", warned about scripts "not yet covered by
    allowScripts", and left no TMT anywhere on the disk -- and the command it
    had just installed pointed at nothing.

    So there is no install hook at all now. A package with no scripts cannot
    have them blocked, and the setup happens on first launch instead, where
    no package manager's policy reaches it.
    """
    data = package()
    assert "scripts" not in data or not data["scripts"], data.get("scripts")
    assert not (INSTALL_DIR / "scripts" / "postinstall.js").exists(), (
        "a file named postinstall.js is run as one by npm, which is the "
        "version dependence this change removed")


def test_the_launcher_installs_tmt_when_there_is_none():
    """The other half of the same fix: the command has to be able to make the
    thing it launches. Read out of the source, because the alternative is a
    test that clones this repository over the network."""
    source = LAUNCHER.read_text(encoding="utf-8")
    assert 'require("../scripts/install.js")' in source, source[:400]
    assert "setup.ensure(" in source, source
    # And it says so while it happens: twenty seconds of silence on a first
    # run reads as a command that has hung.
    assert "First run" in SETUP.read_text(encoding="utf-8")


def test_a_setup_that_cannot_be_done_is_explained_rather_than_hidden():
    """No git, no network, an occupied directory: each has to come back as a
    sentence with a way out, and as a non-zero exit -- the command really did
    fail, and a script wrapping it has to be able to tell."""
    launcher = LAUNCHER.read_text(encoding="utf-8")
    assert "could not be installed now" in launcher, launcher[:400]
    assert "git clone https://github.com/lllons/TMT.git" in launcher
    assert "TMT_HOME" in launcher
    setup = SETUP.read_text(encoding="utf-8")
    assert "git-scm.com" in setup, "a missing git names where to get one"
    assert "is not a TMT checkout" in setup


def test_the_launcher_hands_back_the_exit_code_and_the_signal():
    """It is a front for another process, so a script wrapping `tmtcode` has
    to see what TMT did. Swallowing the code would make every failure look
    like a success to anything calling it."""
    source = LAUNCHER.read_text(encoding="utf-8")
    assert 'stdio: "inherit"' in source
    assert "process.exit(code == null ? 1 : code)" in source
    assert "process.kill(process.pid, signal)" in source


# --- the parts a machine can only check when node is here -------------------

def test_both_scripts_are_clean_utf8_text():
    """The same guard every module in this repository has. A stray byte from
    a shell heredoc has corrupted source here before, and these two files are
    read by a program that is not Python and would report it differently."""
    for script in (LAUNCHER, SETUP, PACKAGE_JSON):
        raw = script.read_bytes()
        assert b"\x00" not in raw, script.name
        text = raw.decode("utf-8")            # raises if it is not clean
        assert text.strip(), script.name


def test_the_launcher_keeps_the_line_endings_the_shebang_needs():
    """A CR at the end of the first line is not a shebang.

    `core.autocrlf` is true in this repository and its blobs are a mix, so a
    fresh clone on Windows would hand these files CRLF -- and `npm publish`
    ships the working tree. `#!/usr/bin/env node\\r` sends macOS and Linux
    looking for an interpreter called "node\\r", so the package would be
    broken for everybody except the person who published it, who would see it
    work.

    `.gitattributes` pins the three paths, and this is the alarm if the pin is
    removed or a file is added beside them without one.
    """
    for script in (LAUNCHER, SETUP, PACKAGE_JSON):
        assert b"\r" not in script.read_bytes(), script.name
    rules = (INSTALL_DIR / ".gitattributes").read_text(encoding="utf-8")
    for pattern in ("bin/*.js text eol=lf", "scripts/*.js text eol=lf",
                    "package.json text eol=lf"):
        assert pattern in rules, pattern


def test_node_accepts_both_scripts_when_node_is_available():
    """`node --check` is the only thing here that can catch a syntax error,
    and it needs node. When node is not on PATH this test asserts the files
    are there and says nothing about their syntax -- a suite that required a
    second runtime would cost more than the check is worth."""
    node = shutil.which("node")
    if not node:
        assert LAUNCHER.is_file() and SETUP.is_file()
        return
    for script in (LAUNCHER, SETUP):
        done = subprocess.run([node, "--check", str(script)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=60)
        assert done.returncode == 0, (script.name,
                                      done.stdout.decode("utf-8", "replace"))
