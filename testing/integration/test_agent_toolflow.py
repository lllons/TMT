"""The repository-understanding tools, driven the way the model drives them.

Everything here goes through `execute_action`, not through the modules' own
functions, because that is the only path the model can actually take. A tool
that works perfectly and is not registered is a tool that does not exist, and
this file is what would notice.

The workflow is the one the tools were built for: find the shape of the
project, find a definition, find every use of it, preview a coordinated change,
apply it, work out what to re-run, and write down what was learned.
"""

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import agent_actions
import agent_config
import agent_index


def remove_tree(path):
    """Delete a temp tree, including anything left read-only on Windows."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


class Project:
    """A throwaway project, with TMT's own state sent somewhere throwaway too."""

    def __init__(self, files=None):
        self.previous_root = agent_config.ROOT_DIR
        self.previous_install = agent_config.INSTALL_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_flow_")).resolve()
        self.install = Path(tempfile.mkdtemp(prefix="tmt_flowinst_")).resolve()
        agent_config.ROOT_DIR = self.path
        agent_config.INSTALL_DIR = self.install
        for name, body in (files or {}).items():
            self.write(name, body)

    def write(self, name, body):
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body.encode("utf-8"))
        return target

    def read(self, name):
        return (self.path / name).read_bytes().decode("utf-8")

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        agent_config.INSTALL_DIR = self.previous_install
        remove_tree(self.path)
        remove_tree(self.install)


def run(action, **keys):
    """One action, exactly as the loop would run it."""
    keys["action"] = action
    return str(agent_actions.execute_action(keys, {"push_authorized": False}))


PROJECT = {
    "src/calc.py": (
        "def old_function_name(items):\n"
        "    return sum(items)\n"
    ),
    "src/report.py": (
        "from src.calc import old_function_name\n"
        "\n"
        "\n"
        "def build(items):\n"
        "    return old_function_name(items)\n"
    ),
    "tests/test_calc.py": (
        "def test_old_function_name():\n"
        "    assert True\n"
    ),
}


def test_every_new_tool_is_registered_and_reachable_by_the_model():
    """A tool the dispatcher does not know about cannot be used, however well
    it works. `_run_tool` reports a missing module rather than raising, so the
    failure this catches is a silent one: a plausible sentence back instead of
    a result."""
    box = Project(files=PROJECT)
    try:
        calls = {
            "tree": {},
            "glob": {"pattern": "*.py"},
            "grep": {"query": "old_function_name"},
            "find_symbol": {"name": "old_function_name"},
            "code_map": {"target": "old_function_name"},
            "replace_across": {"search": "a", "replace": "b"},
            "related_tests": {},
            "remember": {"note": "The calculator lives in src/calc.py."},
            "recall": {},
        }
        for action, keys in calls.items():
            result = run(action, **keys)
            assert "Unknown action" not in result, (action, result)
            assert "is unavailable" not in result, (action, result)
            assert result.strip(), (action, result)
    finally:
        box.close()


def test_every_new_tool_declares_the_keys_it_needs():
    """The schema the model is validated against. A required key missing from
    REQUIRED_KEYS means a malformed action reaches the tool instead of being
    handed back for correction."""
    from agent_config import REQUIRED_KEYS
    expected = {
        "tree": [], "glob": ["pattern"], "grep": ["query"],
        "find_symbol": ["name"],
        "replace_across": ["search", "replace"], "code_map": ["target"],
        "related_tests": [], "remember": ["note"], "recall": [],
    }
    for action, keys in expected.items():
        assert action in REQUIRED_KEYS, action
        assert REQUIRED_KEYS[action] == keys, (action, REQUIRED_KEYS[action])
    from agent_actions import ACTION_LABELS
    for action in expected:
        assert action in ACTION_LABELS, action


def test_the_whole_workflow_from_shape_to_change_to_tests_to_memory():
    box = Project(files=PROJECT)
    try:
        # 1. What is in this project?
        shape = run("tree")
        assert "calc.py" in shape and "report.py" in shape, shape

        # 2. Which files could hold it? `glob` answers by NAME, before
        # anything is read.
        found = run("glob", pattern="src/*.py")
        assert "src/calc.py" in found.replace("\\", "/"), found
        assert "tests/test_calc.py" not in found.replace("\\", "/"), found

        # 3. Where is the thing defined?
        where = run("find_symbol", name="old_function_name")
        assert "src/calc.py" in where.replace("\\", "/"), where
        assert "structural" in where, where

        # 4. Everywhere it is used. `grep` answers by CONTENT.
        uses = run("grep", query="old_function_name")
        for name in ("src/calc.py", "src/report.py", "tests/test_calc.py"):
            assert name in uses.replace("\\", "/"), (name, uses)

        # 5. What would change? Nothing is written yet.
        preview = run("replace_across", search="old_function_name",
                      replace="new_function_name")
        assert "old_function_name" in box.read("src/calc.py"), "preview wrote to disk"
        assert "would change" in preview.lower(), preview

        # 6. Apply it, and the report matches what actually happened.
        applied = run("replace_across", search="old_function_name",
                      replace="new_function_name", apply=True)
        assert "changed" in applied.lower(), applied
        for name in PROJECT:
            body = box.read(name)
            assert "old_function_name" not in body, (name, body)
            assert "new_function_name" in body, (name, body)

        # 7. The index notices the edit rather than answering from a stale cache.
        agent_index.build_index()
        mapped = run("code_map", target="new_function_name")
        assert "src/calc.py" in mapped.replace("\\", "/"), mapped

        # 8. Write down what was learned, and read it back.
        run("remember", note="old_function_name was renamed to new_function_name.")
        remembered = run("recall")
        assert "new_function_name" in remembered, remembered
    finally:
        box.close()


def test_the_tools_refuse_to_leave_the_workspace():
    """Every one of them takes a path from the model, and the model is the one
    party here that cannot be trusted with one."""
    box = Project(files=PROJECT)
    try:
        outside = [
            ("tree", {"path": "../.."}),
            ("glob", {"pattern": "*", "path": "../.."}),
            ("grep", {"query": "x", "path": "../.."}),
            ("find_symbol", {"name": "x", "path": "../.."}),
            ("replace_across", {"search": "a", "replace": "b",
                                "path": "../..", "apply": True}),
        ]
        for action, keys in outside:
            result = run(action, **keys)
            assert "Refused" in result, (action, result)
            # Refused in words, not as a traceback: the model has to be able to
            # read this and correct itself.
            assert "Traceback" not in result, (action, result)
    finally:
        box.close()


def test_bulk_replacement_cannot_reach_a_file_outside_the_workspace():
    """The single most destructive tool here, aimed deliberately outside."""
    outer = Path(tempfile.mkdtemp(prefix="tmt_outside_")).resolve()
    victim = outer / "untouchable.txt"
    victim.write_bytes(b"old_function_name\n")
    box = Project(files=PROJECT)
    try:
        run("replace_across", search="old_function_name",
            replace="wrecked", path="..", apply=True)
        assert victim.read_bytes() == b"old_function_name\n", victim.read_bytes()
    finally:
        box.close()
        remove_tree(outer)


def test_project_memory_is_kept_out_of_the_project():
    """The rule the whole program runs on: TMT's state lives with TMT. Memory
    written into the workspace would be committed by the next git_commit."""
    box = Project(files=PROJECT)
    try:
        run("remember", note="A convention worth keeping.")
        run("tree")
        agent_index.build_index()
        strays = [p for p in box.path.rglob("*")
                  if p.is_file() and (".tmt" in p.name or ".tmt" in str(p.parent))]
        assert not strays, strays
    finally:
        box.close()


def test_memory_outlives_the_process_that_wrote_it():
    """The whole point of it being persistent. The modules are reloaded from
    scratch, so anything held in memory is gone and only the file can answer."""
    box = Project(files=PROJECT)
    try:
        run("remember", note="The parser is regex based, not a real grammar.")
        script = (
            "import sys, pathlib\n"
            "sys.path.insert(0, %r)\n"
            "import agent_config\n"
            "agent_config.ROOT_DIR = pathlib.Path(%r)\n"
            "agent_config.INSTALL_DIR = pathlib.Path(%r)\n"
            "import agent_memory\n"
            "print(agent_memory.recall())\n"
        # The repo root, derived from the module rather than from __file__:
        # this file lives in testing/integration/, and a child pointed there
        # could not import agent_config at all.
        ) % (str(Path(agent_config.__file__).resolve().parent),
             str(box.path), str(box.install))
        done = subprocess.run([__import__("sys").executable, "-c", script],
                              capture_output=True, text=True, timeout=120)
        assert "regex based" in done.stdout, (done.stdout, done.stderr)
    finally:
        box.close()


def test_a_secret_never_reaches_the_memory_file():
    """Asserted against the bytes on disk, not against what the call returned:
    a function that says it refused and writes anyway is exactly the failure
    worth testing for."""
    import agent_memory
    box = Project(files=PROJECT)
    try:
        run("remember", note="The deploy key is sk-abcdefghijklmnopqrstuvwxyz012345")
        stored = Path(agent_memory.memory_path())
        raw = stored.read_bytes().decode("utf-8") if stored.exists() else ""
        assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in raw, raw
    finally:
        box.close()
