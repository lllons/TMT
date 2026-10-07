"""Minimal test runner: executes every test_* function in testing/**/test_*.py.

The tests live under `testing/`, split into `testing/unit/` and
`testing/integration/`. This file stays at the repository root because the
modules under test are here, and the root is what has to be on sys.path for
`import agent_config` to work at all.

Discovery is recursive over `testing/`, and every directory holding a test file
goes onto sys.path as well, because the test modules import one another by bare
name. There are no `__init__.py` files under `testing/` on purpose: modules are
imported by bare stem, and a package would change those names and break the
cross-imports. Stems must therefore stay unique across the two directories.

A run that discovers no test files returns 1 and says so. A runner that finds
nothing must never exit 0 -- the whole suite silently disappearing would
otherwise read exactly like a clean run.

Kept dependency-free so the suite runs anywhere the agent runs; the test files
are plain functions with asserts, so pytest can also collect them as-is.
"""

import shutil
import sys
import tempfile
import traceback
from pathlib import Path

TESTS_DIRNAME = "testing"


def isolate_checkpoints():
    """Point the checkpoint store at a temporary directory for this run.

    A driven session that writes a file takes a real before-picture, and the
    store lives in INSTALL_DIR beside the credentials and the index. Every
    driven session builds a NEW temporary workspace, so the store keys by a new
    hash each time and the per-workspace retention never sees the last one --
    it only ever grows, it is git-ignored so nothing shows in `git status`, and
    what it holds is copies of whatever the test wrote.

    Done HERE rather than in a harness because seven different places in this
    suite drive `TMT.main`, and a fix applied to the one everybody happens to
    use is a fix the eighth walks straight past. `testing/conftest.py` does the
    same for a pytest run, which is the other supported way in.

    Returns the directory to remove afterwards, or None when it could not be
    redirected -- which must not stop the suite running.
    """
    try:
        import agent_config
        temporary = Path(tempfile.mkdtemp(prefix="tmt_cp_suite_")).resolve()
        agent_config.CHECKPOINT_DIR = temporary
        return temporary
    except Exception:
        return None


def isolate_reply_format():
    """Pin the model reply format to "json" for this run, whatever is on disk.

    The reply format is a per-installation setting (`.tmt_protocol` in
    INSTALL_DIR) with a default of "tags", and a developer can change it in
    Settings. The 2,794 tests that existed before the setting did were all
    written against the JSON protocol, and they must keep meaning what they
    meant whichever format this machine happens to be set to -- the same
    argument as the credential sandbox: a test that reads the machine is
    asking a question about the machine it is running on. So the file is
    pointed at a temporary one that says "json", and the live value with it.

    Tests of the tag protocol opt in explicitly, through `ReplyFormat` in
    `test_agent_reply_format`, which redirects the file again for the length of
    one test and puts this one back.

    Nothing is restored: the process ends and the directory is removed by the
    caller, the same lifecycle as `isolate_checkpoints`. `testing/conftest.py`
    does the same for a pytest run, and the two are kept in step.

    Must run before any test module is imported, because a module that read
    `agent_config.PROTOCOL` at import would otherwise have read the real one.

    Returns the directory to remove afterwards, or None when it could not be
    redirected -- which must not stop the suite running.
    """
    try:
        import agent_config
        temporary = Path(tempfile.mkdtemp(prefix="tmt_rf_suite_")).resolve()
        protocol_file = temporary / ".tmt_protocol"
        protocol_file.write_text("json\n", encoding="utf-8")
        agent_config.PROTOCOL_FILE = protocol_file
        agent_config.PROTOCOL = "json"
        return temporary
    except Exception:
        return None


def add_to_path(directory):
    """Put a directory first on sys.path, once."""
    text = str(directory)
    if text not in sys.path:
        sys.path.insert(0, text)


def run():
    root = Path(__file__).resolve().parent
    tests_root = root / TESTS_DIRNAME
    add_to_path(root)                          # the modules under test live here
    paths = sorted(tests_root.rglob("test_*.py"))
    if not paths:
        print(f"ERROR: no test files found under {tests_root}")
        print("Nothing was collected, so nothing was verified. "
              "This is a discovery failure, not a passing run.")
        return 1
    # Every test directory, before the first import: the test modules import
    # one another by bare name across unit/ and integration/ both ways.
    for path in paths:
        add_to_path(path.parent)
    # After the path is set up, because it imports agent_config, and before a
    # single test runs, because the first driven session that writes a file
    # takes a checkpoint.
    checkpoints = isolate_checkpoints()
    # Beside it, and for the same reason: before a single test module is
    # imported, so nothing reads the reply format this machine is set to.
    reply_format = isolate_reply_format()
    try:
        return _collect(paths)
    finally:
        for temporary in (checkpoints, reply_format):
            if temporary is not None:
                shutil.rmtree(str(temporary), ignore_errors=True)


def _collect(paths):
    passed, failures = 0, []
    for path in paths:
        module = __import__(path.stem)
        for name in sorted(vars(module)):
            if not name.startswith("test_"):
                continue
            test = getattr(module, name)
            if not callable(test):
                continue
            try:
                test()
                passed += 1
                print(f"PASS {path.stem}.{name}")
            except Exception:
                failures.append(f"{path.stem}.{name}\n{traceback.format_exc()}")
                print(f"FAIL {path.stem}.{name}")
    print(f"\n{passed} passed, {len(failures)} failed")
    for failure in failures:
        print("\n" + failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
