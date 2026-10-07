"""The model reply format setting: where it lives, how it is read, how it is set.

`PROTOCOL` is one word in one file -- `tags` or `json` -- and it decides, in
later work, whether the model is asked to answer with tag blocks or with JSON
objects. This module protects the setting itself and nothing built on it: the
default, the forgiving read, the validated and loud write, the refresh at
startup, and the one property that makes it safe to run a test suite beside it.

THE DEFAULT IS TAGS, AND EVERY FAILURE READS AS THE DEFAULT. A missing file is a
fresh installation, an unreadable one is somebody's permissions, a directory
where the file should be is somebody's tidying, and a file edited by hand into
nonsense is a typo. None of those is a reason to stop TMT starting, and none is
evidence that anyone asked for the other format.

THE WRITE IS THE ONE PATH THAT RAISES. The menu toggles this in place, and a
toggle that silently did not persist would show one value on the row and be the
other on the next launch. The menu catches it; the setter must not swallow it.

**THE SUITE IS PINNED TO JSON, AND THAT NEEDS A TEST OF ITS OWN.** The existing
tests were written against the JSON protocol and must keep meaning what they
meant whatever `.tmt_protocol` says on the machine running them. The runner
(`run_tests.isolate_reply_format`, mirrored in `testing/conftest.py`) points
`PROTOCOL_FILE` at a temporary file that says `json` before any test module is
imported. If it stopped doing that, nothing here would fail on a machine with
no `.tmt_protocol` -- the default is "tags", the suite would quietly be testing
the wrong protocol, and the first sign would be a developer who had chosen
`json` in Settings seeing a different set of failures from everyone else.
`test_the_suite_runs_pinned_to_json_and_not_against_the_real_file` fails on a
machine WITH the file and on one without, which is the only kind of guard that
means anything here.

Nothing here touches the real `.tmt_protocol`. `ReplyFormat` redirects
`PROTOCOL_FILE` into a temporary directory, checks the redirect took before it
writes a byte, and puts the path and the live value back. It is exported for
the other test modules that need a format of their own: they import it by bare
stem, the shape `test_agent_credentials.Credentials` has.
"""

import runpy
import shutil
import tempfile
from pathlib import Path

import agent_config

INSTALL_DIR = Path(agent_config.__file__).resolve().parent


class ReplyFormat:
    """The reply format setting, redirected into a temporary directory.

    `agent_config.PROTOCOL_FILE` is a module attribute and the real one is
    `INSTALL_DIR / ".tmt_protocol"` -- the repository itself when TMT is run on
    TMT. A test that wrote there would silently change the developer's own
    setting and leave an untracked file in the working tree.

    `name` is both what is written to the file and what the live
    `agent_config.PROTOCOL` is set to, because a test about the setting wants
    the two to agree unless it says otherwise. `None` is the other half: no
    file at all, which is a fresh installation, with the live value at the
    default. `contents` is the raw text of the file for a test about what a
    hand-edited one reads as, and wins over `name` for the FILE only.

    `PROTOCOL` is restored as well as the path: `set_protocol` and
    `refresh_protocol` both assign to that global, and a leaked one would have a
    later test reading a value this one put there. Restoring only the path
    would pass every test in this file and corrupt the next module's.

    Usable as a context manager or held open and `close()`d.
    """

    def __init__(self, name=None, contents=None):
        self.dir = Path(tempfile.mkdtemp(prefix="tmt_replyformat_"))
        self.previous_file = agent_config.PROTOCOL_FILE
        self.previous_value = agent_config.PROTOCOL
        self.path = self.dir / ".tmt_protocol"
        agent_config.PROTOCOL_FILE = self.path
        # CHECKED BEFORE A BYTE IS WRITTEN, the `Credentials` rule: the line
        # above is the whole isolation and the lines below write. A sandbox
        # whose redirect did not take would write a test's value over the
        # developer's own setting, and nothing afterwards would look wrong.
        self._must_be_mine(agent_config.PROTOCOL_FILE, "PROTOCOL_FILE")
        text = contents if contents is not None else (
            None if name is None else name + "\n")
        if text is not None:
            self.write(text)
        agent_config.PROTOCOL = (name if name is not None
                                 else agent_config.DEFAULT_PROTOCOL)

    def _must_be_mine(self, path, label):
        """Refuse to write anywhere but this sandbox's own directory."""
        if self.dir not in Path(path).parents:
            self.close()
            raise AssertionError(
                "%s is %s, which is not inside the sandbox at %s. Nothing was "
                "written: a redirect that did not take would put a test's "
                "value over the real setting." % (label, path, self.dir))

    def write(self, text):
        """Put raw text in the file. Checked first, every time."""
        self._must_be_mine(agent_config.PROTOCOL_FILE, "PROTOCOL_FILE")
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def aim_at(self, path):
        """Point the setting somewhere it cannot be written or read."""
        agent_config.PROTOCOL_FILE = Path(path)
        return agent_config.PROTOCOL_FILE

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()
        return False

    def close(self):
        agent_config.PROTOCOL_FILE = self.previous_file
        agent_config.PROTOCOL = self.previous_value
        shutil.rmtree(str(self.dir), ignore_errors=True)


# --- the vocabulary and the default -----------------------------------------

def test_the_two_formats_and_the_default_are_what_was_asked_for():
    """Spelled out as literals, not read back off the constants: a test that
    compared the constant to itself would pass whatever somebody changed it
    to. `agent_protocol` will spell the same two words, and a later test
    asserts the two modules agree -- this is what they are agreeing WITH."""
    assert agent_config.PROTOCOLS == ("tags", "json"), agent_config.PROTOCOLS
    assert agent_config.DEFAULT_PROTOCOL == "tags", agent_config.DEFAULT_PROTOCOL
    assert agent_config.DEFAULT_PROTOCOL in agent_config.PROTOCOLS
    assert agent_config.protocol_names() == ["tags", "json"]
    # A copy, so a caller cannot reorder what Settings offers.
    agent_config.protocol_names().append("xml")
    assert agent_config.protocol_names() == ["tags", "json"]


def test_a_missing_file_is_a_fresh_installation_and_reads_as_the_default():
    with ReplyFormat(None) as box:
        assert not box.path.exists()
        assert agent_config.read_saved_protocol() == "tags"
        assert agent_config.refresh_protocol() == "tags"
        assert agent_config.PROTOCOL == "tags"


def test_a_file_that_cannot_be_read_is_the_default_and_never_an_error():
    """A directory where the file should be raises an OSError on read, and a
    file that is not text at all raises ValueError on decode. Both are caught,
    and a launch must survive both."""
    with ReplyFormat(None) as box:
        box.aim_at(box.dir)                       # a directory: unreadable as a file
        assert agent_config.read_saved_protocol() == "tags"
        box.aim_at(box.dir / "no" / "such" / "place" / ".tmt_protocol")
        assert agent_config.read_saved_protocol() == "tags"
    with ReplyFormat(None) as box:
        box.path.write_bytes(b"\xff\xfe\x00\x80 not text \xc3\x28")
        assert agent_config.read_saved_protocol() == "tags"
        assert agent_config.refresh_protocol() == "tags"


def test_an_unrecognised_value_is_the_default_and_not_the_other_format():
    """Nonsense is a typo, not a request. In particular it must not read as
    `json`: the default is a fact about the installation and a hand-mangled
    file is not evidence of a choice."""
    for text in ("", "\n", "xml", "tag", "jsonn", "json tags", "1", "true",
                 "on", "{\"protocol\": \"json\"}", "tags,json"):
        with ReplyFormat(None, contents=text) as box:
            assert box.path.exists()
            assert agent_config.read_saved_protocol() == "tags", repr(text)


def test_the_stored_word_is_read_case_and_whitespace_insensitively():
    """A file edited in an editor arrives with a trailing newline, and one
    edited by a person arrives however they typed it. A reader that only
    matched the exact lowercase word would treat every one of these as
    nonsense and quietly answer the default."""
    for text in ("json", "JSON", "Json", " json ", "json\n", "\tjson\r\n",
                 "  JsOn  \n"):
        with ReplyFormat(None, contents=text):
            assert agent_config.read_saved_protocol() == "json", repr(text)
    for text in ("tags", "TAGS", "Tags", " tags\n", "\ttags\r\n"):
        with ReplyFormat(None, contents=text):
            assert agent_config.read_saved_protocol() == "tags", repr(text)
    # And the default is not what is making the second list pass: a stored
    # `json` has to beat it, which is the first list.
    with ReplyFormat(None, contents="JSON"):
        assert agent_config.read_saved_protocol() != agent_config.DEFAULT_PROTOCOL


# --- setting it ---------------------------------------------------------------

def test_a_set_value_is_stored_live_and_survives_a_fresh_read():
    """Every persistence assertion reads through `read_saved_protocol()` and
    the file itself rather than trusting the global the setter just assigned:
    the whole point of storing it is the next launch."""
    with ReplyFormat(None) as box:
        assert agent_config.set_protocol("json") == "json"
        assert agent_config.PROTOCOL == "json"
        assert box.path.read_text(encoding="utf-8") == "json\n"
        assert agent_config.read_saved_protocol() == "json"

        agent_config.PROTOCOL = "tags"            # as a fresh import would have it
        assert agent_config.refresh_protocol() == "json"

        assert agent_config.set_protocol("tags") == "tags"
        assert agent_config.PROTOCOL == "tags"
        assert box.path.read_text(encoding="utf-8") == "tags\n"
        assert agent_config.read_saved_protocol() == "tags"


def test_set_protocol_normalises_what_it_is_given_before_storing_it():
    with ReplyFormat(None) as box:
        assert agent_config.set_protocol("  JSON ") == "json"
        assert box.path.read_text(encoding="utf-8") == "json\n"
        assert agent_config.set_protocol("Tags\n") == "tags"
        assert box.path.read_text(encoding="utf-8") == "tags\n"


def test_set_protocol_refuses_an_unknown_name_and_names_the_options():
    """A typo must not become the active setting and surface much later as a
    model being taught a format nothing can parse. The refusal leaves both the
    file and the live value exactly as they were."""
    with ReplyFormat("json") as box:
        before = box.path.read_text(encoding="utf-8")
        for bad in ("xml", "tag", "JSONL", "", None, True, 0, "tags json"):
            raised = None
            try:
                agent_config.set_protocol(bad)
            except ValueError as error:
                raised = error
            assert raised is not None, "accepted %r" % (bad,)
            message = str(raised)
            assert "tags" in message and "json" in message, message
            assert agent_config.PROTOCOL == "json", repr(bad)
            assert box.path.read_text(encoding="utf-8") == before, repr(bad)


def test_the_refusal_has_the_wording_shape_set_effort_uses():
    """Same shape on purpose -- `X is one of a, b; got 'c'.` -- so a reader who
    has seen one refusal can read the other."""
    with ReplyFormat(None):
        try:
            agent_config.set_protocol("xml")
        except ValueError as error:
            assert str(error) == "Reply format is one of tags, json; got 'xml'.", str(error)
        else:
            raise AssertionError("an unknown name was accepted")


def test_a_write_that_cannot_be_made_is_raised_rather_than_lost():
    """The one path through this setting that does NOT default quietly, and
    deliberately: every read defaults because a launch must not be stoppable,
    but a toggle the user just pressed that silently did not persist would
    show one value in the menu and be the other on the next launch."""
    with ReplyFormat("tags") as box:
        # A path inside a directory that does not exist cannot be created.
        box.aim_at(box.dir / "no" / "such" / "place" / ".tmt_protocol")
        raised = None
        try:
            agent_config.set_protocol("json")
        except OSError as error:
            raised = error
        assert raised is not None, "a failed write reported success"
        # And the live value did not move: claiming a change that was not
        # stored is the failure this raise exists to prevent.
        assert agent_config.PROTOCOL == "tags", agent_config.PROTOCOL
    with ReplyFormat("tags") as box:
        box.aim_at(box.dir)                       # a directory cannot be written
        raised = None
        try:
            agent_config.set_protocol("json")
        except OSError as error:
            raised = error
        assert raised is not None, "a failed write reported success"
        assert agent_config.PROTOCOL == "tags", agent_config.PROTOCOL


def test_refresh_protocol_rereads_the_file_and_returns_what_it_found():
    """`refresh_effort` exists because a setting that is written and never
    re-read lasts one session and quietly reverts. This is the same wire: it
    has to assign the global the rest of the process reads AND hand the value
    back to its caller."""
    with ReplyFormat("json") as box:
        agent_config.PROTOCOL = "tags"
        assert agent_config.refresh_protocol() == "json"
        assert agent_config.PROTOCOL == "json"
        box.write("tags\n")
        assert agent_config.refresh_protocol() == "tags"
        assert agent_config.PROTOCOL == "tags"
        box.write("enormous\n")
        agent_config.PROTOCOL = "json"
        assert agent_config.refresh_protocol() == agent_config.DEFAULT_PROTOCOL
        assert agent_config.PROTOCOL == agent_config.DEFAULT_PROTOCOL
        box.path.unlink()
        agent_config.PROTOCOL = "json"
        assert agent_config.refresh_protocol() == agent_config.DEFAULT_PROTOCOL


# --- where it lives -----------------------------------------------------------

def real_protocol_file():
    """Where `agent_config` puts the file when nothing has redirected it.

    The suite redirects `PROTOCOL_FILE` before the first test runs, so the
    attribute at test time is the sandbox's and says nothing about the product.
    Running a fresh copy of the module is the only way to ask the product what
    it would have chosen: the file is read, the settings are not written, and
    the copy is thrown away.
    """
    fresh = runpy.run_path(str(Path(agent_config.__file__).resolve()),
                           run_name="agent_config_unredirected")
    return Path(fresh["PROTOCOL_FILE"]), Path(fresh["INSTALL_DIR"]), fresh["DEFAULT_PROTOCOL"]


def test_the_setting_belongs_to_the_installation_and_never_to_the_workspace():
    """The rule the model file and the effort level already follow. TMT is the
    same agent in every directory, so a setting that followed the workspace
    would be a different answer per project -- and would drop a file into the
    user's repository, which an existing test in the suite forbids outright.

    Asked of an UNREDIRECTED copy of the module (see `real_protocol_file`),
    because the live one is pointed at a temporary file for the length of the
    run and would pass whatever the product did.
    """
    from test_agent_workspace import Workspace
    path, install_dir, default = real_protocol_file()
    assert install_dir == INSTALL_DIR, (install_dir, INSTALL_DIR)
    assert path == INSTALL_DIR / ".tmt_protocol", path
    assert path.parent == INSTALL_DIR, path
    assert path.name.startswith("."), path.name
    assert default == "tags", default
    box = Workspace(git=True)
    try:
        box.use()
        assert path != box.path
        assert box.path not in path.parents, path
        # And pointing TMT at a workspace moves nothing: the live setting is
        # still wherever it was, which is not inside the project either.
        assert box.path not in Path(agent_config.PROTOCOL_FILE).resolve().parents
    finally:
        box.close()


def test_the_file_is_git_ignored_beside_the_other_per_install_choices():
    """A per-install choice is not source. Without the entry the file would
    show up in `git status` for anybody who flipped the switch, and it would be
    one careless `git add -A` from being committed."""
    lines = [line.strip() for line in
             (INSTALL_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert ".tmt_protocol" in lines, "`.tmt_protocol` is not ignored"
    assert ".tmt_effort" in lines, "the setting this one is modelled on moved"


# --- the sandbox itself -------------------------------------------------------

def test_reply_format_redirects_the_file_and_puts_the_path_and_value_back():
    path, value = agent_config.PROTOCOL_FILE, agent_config.PROTOCOL
    with ReplyFormat("tags") as box:
        assert agent_config.PROTOCOL_FILE == box.path
        assert box.dir in agent_config.PROTOCOL_FILE.parents
        assert agent_config.PROTOCOL == "tags"
        assert agent_config.read_saved_protocol() == "tags"
        agent_config.set_protocol("json")         # leaks nothing past the block
        assert agent_config.PROTOCOL == "json"
    assert agent_config.PROTOCOL_FILE == path
    assert agent_config.PROTOCOL == value
    assert not box.dir.exists(), "the temporary directory was left behind"


def test_reply_format_restores_even_when_the_block_raises():
    path, value = agent_config.PROTOCOL_FILE, agent_config.PROTOCOL
    try:
        with ReplyFormat("tags"):
            agent_config.set_protocol("json")
            raise RuntimeError("a test failed inside the block")
    except RuntimeError:
        pass
    assert agent_config.PROTOCOL_FILE == path
    assert agent_config.PROTOCOL == value


def test_a_sandbox_that_is_not_isolating_writes_nothing():
    """The guard, and it is here for `Credentials`' reason: a mutation run that
    un-redirects a sandbox turns every test using it into a writer aimed at the
    real file. The redirect is verified before anything is written, and a
    sandbox that is not isolating takes itself down instead of writing."""
    box = ReplyFormat("json")
    sandboxed = agent_config.PROTOCOL_FILE
    try:
        raised = None
        try:
            box._must_be_mine(INSTALL_DIR / ".tmt_protocol", "PROTOCOL_FILE")
        except AssertionError as error:
            raised = error
        assert raised is not None, "a sandbox pointing at the real file was accepted"
        assert "not inside the sandbox" in str(raised), str(raised)
        # It closed itself: the redirect is gone and the directory with it.
        assert agent_config.PROTOCOL_FILE != sandboxed
        assert not box.dir.exists()
    finally:
        box.close()


def test_the_suite_runs_pinned_to_json_and_not_against_the_real_file():
    """Proves the runner's isolation ran, and fails on a machine WITH a
    `.tmt_protocol` as well as one without: the default is "tags", so a suite
    that was not pinned would read "tags" on a clean checkout, and the stored
    word on a developer's. Either way this is not "json" from a temp file.

    Asserted OUTSIDE any `ReplyFormat` block, which is the whole point -- this
    is what every other test in the suite is standing on."""
    assert agent_config.PROTOCOL == "json", (
        "the suite is not pinned to the JSON protocol; run it through "
        "run_tests.py or pytest so the reply format is isolated first")
    assert agent_config.read_saved_protocol() == "json"
    live = Path(agent_config.PROTOCOL_FILE).resolve()
    assert live.parent != INSTALL_DIR, (
        "PROTOCOL_FILE is the real one: %s" % live)
    assert INSTALL_DIR not in live.parents, live
    assert live.name == ".tmt_protocol", live.name
