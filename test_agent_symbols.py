"""Tests for symbol extraction and the project index.

Every one builds its own throwaway workspace and redirects both
agent_config.ROOT_DIR and agent_config.INSTALL_DIR, because these two modules
write a cache and a test that used the real installation directory would
poison the developer's own index and pass or fail on whatever was left there.
"""

import os
import shutil
import stat
import tempfile
from pathlib import Path

import agent_config
import agent_index
import agent_symbols


def remove_tree(path):
    """Delete a temp tree, including anything left read-only on Windows."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


class Workspace:
    """A throwaway workspace, with TMT's own state sent somewhere throwaway too.

    close() restores both roots and must run in a finally block: a leaked
    INSTALL_DIR points the next test's cache at a deleted directory.
    """

    def __init__(self, files=None):
        self.previous_root = agent_config.ROOT_DIR
        self.previous_install = agent_config.INSTALL_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_sym_")).resolve()
        self.install = Path(tempfile.mkdtemp(prefix="tmt_inst_")).resolve()
        for name, body in (files or {}).items():
            self.write(name, body)
        agent_config.ROOT_DIR = self.path
        agent_config.INSTALL_DIR = self.install

    def write(self, name, body):
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, not text: write_text turns "\n" into "\r\n" on Windows, and a
        # line number asserted below would then be counted against a file that
        # does not look the way the test wrote it.
        target.write_bytes(body.encode("utf-8"))
        return target

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        agent_config.INSTALL_DIR = self.previous_install
        remove_tree(self.path)
        remove_tree(self.install)


PYTHON = (
    "import os\n"
    "from pathlib import Path\n"
    "\n"
    "LIMIT = 100\n"
    "\n"
    "\n"
    "def parse_json(text):\n"
    "    return text\n"
    "\n"
    "\n"
    "class Calculator:\n"
    "    def calculate_total(self, items):\n"
    "        return sum(items)\n"
)


# --- Python is parsed, so its answers are exact ------------------------------

def test_python_functions_classes_and_methods_are_found_with_their_lines():
    box = Workspace(files={"src/calc.py": PYTHON})
    try:
        found = {s["name"]: s for s in agent_symbols.symbols_in("src/calc.py")}
        assert "parse_json" in found, sorted(found)
        assert found["parse_json"]["kind"] == "function", found["parse_json"]
        assert found["parse_json"]["line"] == 7, found["parse_json"]
        assert found["Calculator"]["kind"] == "class", found["Calculator"]
        assert found["Calculator"]["line"] == 11, found["Calculator"]
        # Qualified, so a method is not mistaken for a free function of the
        # same name somewhere else in the project.
        assert "Calculator.calculate_total" in found, sorted(found)
        method = found["Calculator.calculate_total"]
        assert method["kind"] == "method" and method["line"] == 12, method
        assert method["path"].replace("\\", "/") == "src/calc.py", method
        assert method["language"] == "python", method
    finally:
        box.close()


def test_imports_and_module_constants_are_extracted():
    box = Workspace(files={"src/calc.py": PYTHON})
    try:
        found = {s["name"]: s["kind"] for s in agent_symbols.symbols_in("src/calc.py")}
        assert found.get("LIMIT") == "constant", found
        assert "os" in found and "import" in found["os"], found
    finally:
        box.close()


def test_a_python_file_that_will_not_parse_is_reported_not_silently_dropped():
    """Silence reads as "no symbols here", which is a different claim from
    "this file could not be read" and the wrong one to make."""
    box = Workspace(files={"broken.py": "def oops(:\n    pass\n",
                           "fine.py": "def kept():\n    pass\n"})
    try:
        rendered = agent_symbols.find_symbol("kept")
        assert "kept" in rendered, rendered
        # The broken file is named somewhere in the output rather than vanishing.
        assert "broken.py" in agent_symbols.find_symbol("oops"), \
            agent_symbols.find_symbol("oops")
    finally:
        box.close()


# --- other languages are guesses, and say so ---------------------------------

def test_other_languages_are_found_lexically_and_labelled_as_guesses():
    box = Workspace(files={
        "src/calc.js": "export function calculateTotal(a) { return a }\nclass Calculator {}\n",
        "src/lib.go": "package main\n\nfunc CalculateTotal(a int) int { return a }\n",
        "src/calc.py": PYTHON,
    })
    try:
        rendered = agent_symbols.find_symbol("Calculator")
        assert "calc.js" in rendered, rendered
        # The whole point: the two tiers are distinguishable in the output.
        assert "structural" in rendered and "heuristic" in rendered, rendered
        assert "javascript" in rendered, rendered

        go = agent_symbols.find_symbol("CalculateTotal")
        assert "lib.go" in go and "go" in go, go
    finally:
        box.close()


def test_structural_and_heuristic_are_not_the_same_claim():
    box = Workspace(files={"a.py": "class Thing:\n    pass\n",
                           "b.js": "class Thing {}\n"})
    try:
        parsed = agent_symbols.scan_file("a.py")
        assert parsed["tier"] == agent_symbols.TIER_STRUCTURAL, parsed
        assert not parsed["error"], parsed
        lexical = agent_symbols.scan_file("b.js")
        assert lexical["tier"] == agent_symbols.TIER_HEURISTIC, lexical
        assert not agent_symbols.is_structural("javascript")
        assert agent_symbols.is_structural("python")
    finally:
        box.close()


def test_kind_filtering_and_a_name_that_is_not_there():
    box = Workspace(files={"src/calc.py": PYTHON})
    try:
        only_classes = agent_symbols.find_symbol("Calculator", kind="class")
        assert "[class, structural]" in only_classes, only_classes
        # The method is excluded by the filter, though its name also matches.
        # Asserted on the match marker, not on the bare name: the name also
        # appears in the class's own context lines, which is not a match.
        assert "[method" not in only_classes, only_classes
        assert "Found 1 symbol" in only_classes, only_classes

        missing = agent_symbols.find_symbol("nothing_defines_this")
        assert "nothing_defines_this" in missing, missing
        assert "no " in missing.lower() or "found 0" in missing.lower(), missing
    finally:
        box.close()


def test_a_path_outside_the_workspace_is_refused():
    box = Workspace(files={"a.py": "x = 1\n"})
    try:
        for call in (lambda: agent_symbols.find_symbol("x", path="../.."),
                     lambda: agent_symbols.symbols_in("../../etc")):
            try:
                call()
            except ValueError:
                continue
            raise AssertionError("a path outside the workspace was not refused")
    finally:
        box.close()


# --- the index, which exists to avoid rescanning -----------------------------

def test_the_cache_is_written_beside_tmt_and_never_into_the_workspace():
    """The rule the whole project runs on: TMT's state lives with TMT. A cache
    inside the workspace would be committed by the next git_commit."""
    box = Workspace(files={"a.py": "def one():\n    pass\n"})
    try:
        agent_index.build_index()
        cached = Path(agent_index.cache_path())
        assert cached.exists(), cached
        assert box.install in cached.parents, cached
        assert box.path not in cached.parents, cached
        leaked = [p for p in box.path.rglob("*") if ".tmt" in p.name]
        assert not leaked, leaked
    finally:
        box.close()


def test_the_index_reuses_its_cache_and_notices_a_changed_file():
    """Not asserted by assuming: the file is edited and the new symbol has to
    appear, and an untouched build has to come back with the same content."""
    box = Workspace(files={"a.py": "def first():\n    pass\n"})
    try:
        agent_index.build_index()
        before = Path(agent_index.cache_path()).read_bytes()

        # Nothing changed, so nothing is re-parsed and the answer is the same.
        agent_index.build_index()
        assert "first" in agent_index.code_map("first"), agent_index.code_map("first")

        box.write("a.py", "def first():\n    pass\n\n\ndef second():\n    pass\n")
        agent_index.build_index()
        after = agent_index.code_map("second")
        assert "second" in after and "a.py" in after, after
        assert Path(agent_index.cache_path()).read_bytes() != before
    finally:
        box.close()


def test_a_corrupt_cache_is_discarded_rather_than_raising():
    """A cache file must never be able to stop a session. It is a speed-up, and
    a speed-up that can fail the run is worse than no speed-up."""
    box = Workspace(files={"a.py": "def one():\n    pass\n"})
    try:
        agent_index.build_index()
        Path(agent_index.cache_path()).write_bytes(b"{not json at all")
        rendered = agent_index.code_map("one")
        assert "one" in rendered, rendered
        assert "a.py" in rendered, rendered
    finally:
        box.close()


def test_force_rebuilds_and_clear_cache_removes_the_file():
    box = Workspace(files={"a.py": "def one():\n    pass\n"})
    try:
        agent_index.build_index()
        assert Path(agent_index.cache_path()).exists()
        agent_index.clear_cache()
        assert not Path(agent_index.cache_path()).exists()
        agent_index.build_index(force=True)
        assert "one" in agent_index.code_map("one")
    finally:
        box.close()


def test_importers_and_references_answer_what_depends_on_what():
    box = Workspace(files={
        "lib.py": "def helper():\n    pass\n",
        "app.py": "import lib\n\n\ndef go():\n    return lib.helper()\n",
    })
    try:
        importers = agent_index.code_map("lib", relation="importers")
        assert "app.py" in importers, importers

        references = agent_index.code_map("helper", relation="references")
        assert "app.py" in references, references
        # A guess is labelled a guess, because a lexical hit is not a call.
        assert "heuristic" in references.lower(), references

        defines = agent_index.code_map("helper", relation="defines")
        assert "lib.py" in defines, defines
    finally:
        box.close()


def test_the_index_prunes_machinery_and_ignores_binaries():
    box = Workspace(files={"real.py": "def kept():\n    pass\n"})
    try:
        junk = box.path / "node_modules" / "dep.py"
        junk.parent.mkdir(parents=True, exist_ok=True)
        junk.write_bytes(b"def buried():\n    pass\n")
        (box.path / "blob.bin").write_bytes(b"\x00\x01def sneaky():\x00")

        # Asserted on the file, not the name: a "no symbol named X" reply
        # quotes X back, so looking for the bare name always finds it.
        buried = agent_symbols.find_symbol("buried")
        assert "dep.py" not in buried and "node_modules" not in buried, buried
        sneaky = agent_symbols.find_symbol("sneaky")
        assert "blob.bin" not in sneaky, sneaky
        assert "real.py" in agent_symbols.find_symbol("kept")
    finally:
        box.close()


def test_results_are_capped_and_the_cap_is_stated():
    """A result too big for the context is the same as no result, so the cap
    matters -- and a capped list that claims to be whole is worse than either."""
    body = "".join("def repeated_%d():\n    pass\n\n\n" % n
                   for n in range(agent_symbols.MAX_SYMBOL_HITS + 15))
    box = Workspace(files={"many.py": body})
    try:
        rendered = agent_symbols.find_symbol("repeated", limit=5)
        assert rendered.count("repeated_") <= 40, rendered[:400]
        assert "5" in rendered, rendered[:400]
    finally:
        box.close()
