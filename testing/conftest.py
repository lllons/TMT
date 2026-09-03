"""What pytest has to do before it collects anything here.

`run_tests.py` is the suite's own entry point and does the same two things; this
is the other supported way in, and the two are kept in step deliberately -- a
test that behaves differently depending on which runner started it is a test
nobody can trust the result of.

Only ONE thing is done here, and it is not about pytest: the checkpoint store is
pointed at a temporary directory for the length of the run.

A driven session that writes a file takes a real before-picture, and that store
lives in INSTALL_DIR beside the credentials and the code index. Every driven
session builds a new temporary workspace, so the store keys by a new hash each
time and the per-workspace retention never sees the last one -- it only ever
grows. It is git-ignored, so nothing shows in `git status`, and what it holds is
copies of whatever the test happened to write. Nothing about it is noticeable
except the size.

It is done at the RUNNER rather than in a harness because seven different places
in this suite drive `TMT.main`, and a fix applied to the one everybody happens to
use is a fix the eighth walks straight past.

There is deliberately no `__init__.py` anywhere under `testing/` -- the modules
are imported by bare stem and a package would change those names and break the
cross-imports. A conftest is not a package marker and does not affect that.
"""

import shutil
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _directory in (_ROOT, _ROOT / "testing" / "unit", _ROOT / "testing" / "integration"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

_TEMPORARY = None


def pytest_configure(config):
    """Redirect the checkpoint store before the first test is collected."""
    global _TEMPORARY
    try:
        import agent_config
        _TEMPORARY = Path(tempfile.mkdtemp(prefix="tmt_cp_pytest_")).resolve()
        agent_config.CHECKPOINT_DIR = _TEMPORARY
    except Exception:
        # A redirect that could not be made must not stop the suite running.
        _TEMPORARY = None


def pytest_unconfigure(config):
    if _TEMPORARY is not None:
        shutil.rmtree(str(_TEMPORARY), ignore_errors=True)
