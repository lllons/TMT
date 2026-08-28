"""Minimal test runner: executes every test_* function in test_*.py.

Kept dependency-free so the suite runs anywhere the agent runs; the test files
are plain functions with asserts, so pytest can also collect them as-is.
"""

import sys
import traceback
from pathlib import Path


def run():
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    passed, failures = 0, []
    for path in sorted(root.glob("test_*.py")):
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
