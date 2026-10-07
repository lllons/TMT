"""What pytest has to do before it collects anything here.

`run_tests.py` is the suite's own entry point and does the same two things; this
is the other supported way in, and the two are kept in step deliberately -- a
test that behaves differently depending on which runner started it is a test
nobody can trust the result of.

Two things are done here, and neither is about pytest. The checkpoint store is
pointed at a temporary directory for the length of the run, and the model reply
format is pinned to "json" -- see `isolate_reply_format` below for why.

The first is described in the paragraphs that follow.

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
_REPLY_FORMAT = None


def isolate_reply_format():
    """Pin the model reply format to "json" for this run, whatever is on disk.

    The same function as `run_tests.isolate_reply_format`, kept in step with it
    deliberately. The reply format is a per-installation setting
    (`.tmt_protocol` in INSTALL_DIR) with a default of "tags", and a developer
    can change it in Settings. The 2,794 tests that existed before the setting
    did were all written against the JSON protocol, and they must keep meaning
    what they meant whichever format this machine is set to -- a test that
    reads the machine is asking a question about the machine it is running on.
    Tests of the tag protocol opt in explicitly, through `ReplyFormat` in
    `test_agent_reply_format`.

    Returns the directory to remove afterwards, or None when it could not be
    redirected -- which must not stop the suite running.
    """
    try:
        import agent_config
        temporary = Path(tempfile.mkdtemp(prefix="tmt_rf_pytest_")).resolve()
        protocol_file = temporary / ".tmt_protocol"
        protocol_file.write_text("json\n", encoding="utf-8")
        agent_config.PROTOCOL_FILE = protocol_file
        agent_config.PROTOCOL = "json"
        return temporary
    except Exception:
        return None


def pytest_configure(config):
    """Redirect the checkpoint store and the reply format before collection."""
    global _TEMPORARY, _REPLY_FORMAT
    try:
        import agent_config
        _TEMPORARY = Path(tempfile.mkdtemp(prefix="tmt_cp_pytest_")).resolve()
        agent_config.CHECKPOINT_DIR = _TEMPORARY
    except Exception:
        # A redirect that could not be made must not stop the suite running.
        _TEMPORARY = None
    _REPLY_FORMAT = isolate_reply_format()


def pytest_unconfigure(config):
    for temporary in (_TEMPORARY, _REPLY_FORMAT):
        if temporary is not None:
            shutil.rmtree(str(temporary), ignore_errors=True)
