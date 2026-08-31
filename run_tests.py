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

import sys
import traceback
from pathlib import Path

TESTS_DIRNAME = "testing"


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
