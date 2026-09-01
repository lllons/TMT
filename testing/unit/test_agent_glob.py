"""Tests for `glob`, the path-discovery action.

Every one of these builds its own throwaway workspace and points
agent_config.ROOT_DIR at it, for the reason test_agent_tree gives: a test that
read the real TMT repository would pass or fail on whatever happened to be
checked out that day.

What is being pinned is mostly the decisions rather than the happy path. `*`
stopping at a separator, `[` being an ordinary character, sorting happening
before the cut, the header counting what matched rather than what fitted, and
the scan-ceiling note appearing on an empty result are all things a later edit
could reverse without anything looking broken.
"""

import os
import shutil
import stat
import tempfile
from pathlib import Path

import agent_config
import agent_file_ops
import agent_glob


def remove_tree(path):
    """Delete a temp tree, including anything left read-only on Windows.

    The rmdir fallback is for the symlink test: Windows will not unlink a
    directory symlink, and rmdir removes the link without touching whatever it
    points at -- which matters here, because what it points at is the "outside
    the workspace" directory the test is about.
    """
    def on_error(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
        except OSError:
            pass
        try:
            func(target)
        except OSError:
            if os.path.isdir(target):
                os.rmdir(target)
            else:
                raise
    shutil.rmtree(path, onerror=on_error)


class Workspace:
    """A throwaway directory as the workspace root.

    close() restores agent_config.ROOT_DIR and must run in a finally block: a
    leaked root points every later test at a directory that has been deleted.
    """

    def __init__(self, files=None, dirs=None):
        self.previous_root = agent_config.ROOT_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_glob_")).resolve()
        for name, body in (files or {}).items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body.encode("utf-8"))
        for name in (dirs or ()):
            (self.path / name).mkdir(parents=True, exist_ok=True)
        agent_config.ROOT_DIR = self.path

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        remove_tree(self.path)


def rows_of(result):
    """The path rows: everything after the header, minus any trailing note."""
    return [line for line in result.splitlines()[1:] if not line.startswith("(")]


def header_of(result):
    return result.splitlines()[0]


# --- what the pattern means -------------------------------------------------

def test_a_name_pattern_finds_its_files_and_says_how_many():
    """The plain case, and the shape of the header: a counted noun, the
    pattern quoted back so the answer says what question it answers, and a
    colon introducing the list."""
    box = Workspace(files={"agent_glob.py": "a\n", "agent_grep.py": "b\n",
                           "README.md": "c\n"})
    try:
        result = agent_glob.glob("agent_*.py")
        assert header_of(result) == "2 matches for `agent_*.py`:", result
        assert rows_of(result) == ["agent_glob.py", "agent_grep.py"], result
        # Nothing was cut short, so nothing claims it was.
        assert "The walk stopped at" not in result, result
    finally:
        box.close()


def test_one_match_is_not_pluralised():
    box = Workspace(files={"only.py": "a\n", "other.md": "b\n"})
    try:
        result = agent_glob.glob("*.py")
        assert header_of(result) == "1 match for `*.py`:", result
    finally:
        box.close()


def test_a_star_stops_at_a_directory_separator():
    """`src/*.py` is the one file in src, not everything beneath it. fnmatch
    would match both, which is exactly why this repository compiles its own
    regex instead of using it."""
    box = Workspace(files={"src/shallow.py": "a\n", "src/deep/buried.py": "b\n"})
    try:
        result = agent_glob.glob("src/*.py")
        assert rows_of(result) == ["src/shallow.py"], result
        assert "buried.py" not in result, result
    finally:
        box.close()


def test_a_double_star_segment_means_any_depth_including_none():
    """`src/**/*.py` has to cover `src/a.py` as well as `src/deep/a.py`, or the
    model has to write two patterns to ask one question."""
    box = Workspace(files={"src/shallow.py": "a\n",
                           "src/deep/buried.py": "b\n",
                           "src/deep/deeper/lowest.py": "c\n",
                           "elsewhere/skip.py": "d\n"})
    try:
        result = agent_glob.glob("src/**/*.py")
        assert rows_of(result) == ["src/deep/buried.py",
                                   "src/deep/deeper/lowest.py",
                                   "src/shallow.py"], result
        assert "elsewhere/skip.py" not in result, result
    finally:
        box.close()


def test_a_pattern_with_no_separator_matches_a_basename_anywhere():
    """This is what makes `*.py` mean every Python file rather than only the
    root's, and it is the difference between one action and a walk of the
    tree by hand."""
    box = Workspace(files={"top.py": "a\n", "src/mid.py": "b\n",
                           "src/deep/low.py": "c\n", "src/notes.md": "d\n"})
    try:
        result = agent_glob.glob("*.py")
        assert rows_of(result) == ["src/deep/low.py", "src/mid.py",
                                   "top.py"], result
        assert "notes.md" not in result, result
    finally:
        box.close()


def test_a_question_mark_matches_exactly_one_character_and_not_a_separator():
    box = Workspace(files={"a1.py": "a\n", "a12.py": "b\n", "a/1.py": "c\n"})
    try:
        result = agent_glob.glob("a?.py")
        assert rows_of(result) == ["a1.py"], result
    finally:
        box.close()


def test_square_brackets_are_literal_characters_and_not_a_character_class():
    """Surprising, deliberate, and the kind of thing a later reader `fixes`.
    `_glob_to_regex` escapes everything it does not itself treat as a wildcard,
    so `agent_[gt]*.py` is a request for a file whose name really contains
    `[gt]` -- it does not mean `agent_g*.py` or `agent_t*.py`."""
    box = Workspace(files={"agent_[gt]x.py": "a\n", "agent_glob.py": "b\n",
                           "agent_tree.py": "c\n"})
    try:
        result = agent_glob.glob("agent_[gt]*.py")
        assert rows_of(result) == ["agent_[gt]x.py"], result
        assert "agent_glob.py" not in result, result
        assert "agent_tree.py" not in result, result
    finally:
        box.close()


def test_a_pattern_written_with_backslashes_still_matches():
    """A model on Windows writes the separator it sees. The pattern is
    normalised before it is compiled, so `src\\*.py` asks the same question as
    `src/*.py` -- and the header still quotes back what was actually typed,
    because the answer has to say what question it answered."""
    box = Workspace(files={"src/a.py": "x\n", "other/b.py": "y\n"})
    try:
        result = agent_glob.glob("src\\*.py")
        assert rows_of(result) == ["src/a.py"], result
        assert header_of(result) == "1 match for `src\\*.py`:", result
    finally:
        box.close()


def test_nested_paths_come_back_posix_separated_and_workspace_relative():
    """Windows produces backslashes and absolute paths; neither may reach a
    result. Everything named is something the model can hand straight back to
    read_lines."""
    box = Workspace(files={"a/b/c/deep.txt": "x\n"})
    try:
        result = agent_glob.glob("**/*.txt")
        rows = rows_of(result)
        assert rows == ["a/b/c/deep.txt"], result
        for row in rows:
            assert "\\" not in row, row
            assert not os.path.isabs(row), row
            assert (box.path / row).exists(), row
        assert str(box.path) not in result, result
    finally:
        box.close()


# --- kind -------------------------------------------------------------------

def test_files_are_the_default_and_directories_are_left_out():
    box = Workspace(files={"src/a.py": "x\n"}, dirs=["src/empty"])
    try:
        result = agent_glob.glob("*")
        rows = rows_of(result)
        assert "src/a.py" in rows, result
        assert not [row for row in rows if row.endswith("/")], result
    finally:
        box.close()


def test_dirs_returns_only_directories_and_marks_each_with_a_separator():
    """The trailing `/` is how the two kinds read apart once the colour is
    stripped, which is the only form some terminals ever show."""
    box = Workspace(files={"src/a.py": "x\n", "docs/notes.md": "y\n"})
    try:
        result = agent_glob.glob("*", kind="dirs")
        rows = rows_of(result)
        assert rows == ["docs/", "src/"], result
        for row in rows:
            assert row.endswith("/"), row
        assert "a.py" not in result, result
    finally:
        box.close()


def test_any_returns_both_kinds_with_the_directories_still_marked():
    box = Workspace(files={"src/a.py": "x\n"})
    try:
        result = agent_glob.glob("*", kind="any")
        rows = rows_of(result)
        assert "src/" in rows, result
        assert "src/a.py" in rows, result
        assert header_of(result) == "2 matches for `*`:", result
    finally:
        box.close()


def test_the_obvious_synonyms_for_kind_are_accepted_case_insensitively():
    """A refused action costs a whole round to recover from, and the word the
    model reaches for is not always the word this module uses."""
    box = Workspace(files={"src/a.py": "x\n"})
    try:
        for word in ("file", "files", "FILES", "  Files  "):
            rows = rows_of(agent_glob.glob("*", kind=word))
            assert rows == ["src/a.py"], (word, rows)
        for word in ("dir", "dirs", "directory", "directories", "folder",
                     "folders", "DIRS", "Directory"):
            rows = rows_of(agent_glob.glob("*", kind=word))
            assert rows == ["src/"], (word, rows)
        for word in ("any", "all", "both", "ANY", "Both"):
            rows = rows_of(agent_glob.glob("*", kind=word))
            assert rows == ["src/", "src/a.py"], (word, rows)
        # An absent or empty kind is files, not "everything".
        assert rows_of(agent_glob.glob("*", kind=None)) == ["src/a.py"]
        assert rows_of(agent_glob.glob("*", kind="")) == ["src/a.py"]
    finally:
        box.close()


def test_an_unknown_kind_is_refused_and_named_back():
    """Named back so the correction is one word rather than a guess."""
    box = Workspace(files={"a.py": "x\n"})
    try:
        assert (agent_glob.glob("*", kind="sideways")
                == "kind must be files, dirs or any -- got: sideways")
        # Not a string at all: still an answer, never a traceback.
        assert (agent_glob.glob("*", kind=7)
                == "kind must be files, dirs or any -- got: 7")
        assert (agent_glob.glob("*", kind=["files"])
                == "kind must be files, dirs or any -- got: ['files']")
    finally:
        box.close()


# --- nothing matched --------------------------------------------------------

def test_the_empty_result_says_which_of_the_three_questions_was_asked():
    """`No files match` cannot be read as "there are no directories like
    that", so each kind gets its own wording."""
    box = Workspace(files={"a.py": "x\n"})
    try:
        assert agent_glob.glob("*.rs").startswith("No files match: *.rs")
        assert agent_glob.glob("*.rs", kind="dirs").startswith(
            "No directories match: *.rs")
        assert agent_glob.glob("*.rs", kind="any").startswith(
            "No paths match: *.rs")
    finally:
        box.close()


def test_an_empty_result_says_what_was_and_was_not_searched():
    box = Workspace(files={"src/a.py": "x\n", "other/b.md": "y\n"})
    try:
        result = agent_glob.glob("*.rs", path="src")
        lines = result.splitlines()
        assert lines[0] == "No files match: *.rs", result
        assert "Only paths under src were searched." in lines, result
        assert ("Machinery directories (.git, node_modules, __pycache__, "
                ".venv and similar) are never searched.") in lines, result
    finally:
        box.close()


def test_an_empty_result_with_no_path_does_not_claim_a_subtree():
    box = Workspace(files={"a.py": "x\n"})
    try:
        result = agent_glob.glob("*.rs")
        assert "Only paths under" not in result, result
        assert "never searched" in result, result
    finally:
        box.close()


# --- ceilings, ordering and truncation --------------------------------------

def test_results_are_sorted_before_they_are_cut():
    """"The first 2" has to name the first two rows of one stable ordering,
    not whichever two the walk happened to reach. os.walk yields the root's own
    files before it descends, so a cut-then-sort implementation would answer
    y.py and z.py here and look entirely reasonable doing it."""
    box = Workspace(files={"y.py": "a\n", "z.py": "b\n", "aaa/b.py": "c\n"})
    try:
        result = agent_glob.glob("*.py", limit=2)
        assert rows_of(result) == ["aaa/b.py", "y.py"], result
    finally:
        box.close()


def test_a_truncated_header_states_the_real_total_and_drops_the_colon():
    """The number in the header is the one a model decides its next action on,
    so it counts everything that matched rather than the part that fitted. The
    sentence ends the header, so the colon that would introduce the list is
    dropped rather than left stranded after a full stop."""
    box = Workspace(files={"f%d.py" % i: "x\n" for i in range(5)})
    try:
        result = agent_glob.glob("*.py", limit=2)
        head = header_of(result)
        assert head.startswith("5 matches for `*.py` --"), head
        assert "showing the first 2, the limit for one result." in head, head
        assert "Narrow the pattern, or pass a 'path'." in head, head
        assert not head.endswith(":"), head
        assert len(rows_of(result)) == 2, result
    finally:
        box.close()


def test_an_untruncated_header_ends_with_a_colon_and_says_nothing_about_limits():
    box = Workspace(files={"a.py": "x\n", "b.py": "y\n"})
    try:
        result = agent_glob.glob("*.py", limit=200)
        assert header_of(result) == "2 matches for `*.py`:", result
        assert "showing the first" not in result, result
    finally:
        box.close()


def test_the_default_cap_is_the_modules_own_constant():
    """Not a magic number written out twice: the default the caller gets when
    it passes no limit is GLOB_MAX_RESULTS itself."""
    box = Workspace(files={"a.py": "1\n", "b.py": "2\n", "c.py": "3\n"})
    saved = agent_glob.GLOB_MAX_RESULTS
    try:
        agent_glob.GLOB_MAX_RESULTS = 2
        result = agent_glob.glob("*.py")
        assert "3 matches for `*.py` --" in result, result
        assert "showing the first 2" in result, result
        assert rows_of(result) == ["a.py", "b.py"], result
    finally:
        agent_glob.GLOB_MAX_RESULTS = saved
        box.close()


def test_a_callers_limit_is_clamped_to_the_hard_ceiling():
    """Past the ceiling the result stops being something a model reads and
    becomes something it has to search, which is the problem glob exists to
    remove -- so a large number is capped rather than obeyed."""
    box = Workspace(files={"a.py": "1\n", "b.py": "2\n", "c.py": "3\n"})
    saved = agent_glob.GLOB_HARD_MAX
    try:
        agent_glob.GLOB_HARD_MAX = 2
        result = agent_glob.glob("*.py", limit=999999)
        assert "showing the first 2, the limit for one result." in result, result
        assert len(rows_of(result)) == 2, result
    finally:
        agent_glob.GLOB_HARD_MAX = saved
        box.close()


def test_a_limit_below_one_is_clamped_up_rather_than_returning_nothing():
    box = Workspace(files={"a.py": "1\n", "b.py": "2\n"})
    try:
        for asked in (0, -4):
            result = agent_glob.glob("*.py", limit=asked)
            assert rows_of(result) == ["a.py"], (asked, result)
            assert "showing the first 1" in result, (asked, result)
    finally:
        box.close()


def test_the_limit_arithmetic_clamps_at_both_ends_and_reads_strings():
    """The clamp itself, against the real constants, without needing a
    thousand files on disk to observe the top of it."""
    assert agent_glob._result_limit(None) == agent_glob.GLOB_MAX_RESULTS
    assert agent_glob._result_limit("") == agent_glob.GLOB_MAX_RESULTS
    assert agent_glob._result_limit(999999) == agent_glob.GLOB_HARD_MAX
    assert agent_glob._result_limit(agent_glob.GLOB_HARD_MAX + 1) == agent_glob.GLOB_HARD_MAX
    assert agent_glob._result_limit(0) == 1
    assert agent_glob._result_limit(-100) == 1
    assert agent_glob._result_limit(7) == 7
    # A model writing the number as a string is not a mistake worth a round.
    assert agent_glob._result_limit("7") == 7
    assert agent_glob._result_limit("many") is None
    assert agent_glob._result_limit([2]) is None


def test_a_limit_that_is_not_a_number_is_refused_in_words():
    box = Workspace(files={"a.py": "x\n"})
    try:
        assert (agent_glob.glob("*.py", limit="many")
                == "limit must be a whole number of results")
        assert (agent_glob.glob("*.py", limit=["two"])
                == "limit must be a whole number of results")
    finally:
        box.close()


# --- the scan ceiling -------------------------------------------------------

def test_a_walk_that_hit_the_ceiling_says_so_under_the_results():
    """The walk stops dead at its ceiling, so a full basket is the only signal
    there was. Silence here would claim the whole workspace was examined.

    Exactly at the ceiling, not past it, because that is the only number the
    real walk can ever produce: iter_workspace_entries returns the moment it
    has yielded its limit, so a check for MORE than the limit would be a check
    that never fires."""
    box = Workspace(files={"a.py": "1\n", "b.py": "2\n", "c.py": "3\n"})
    saved = agent_glob.WORKSPACE_MAX_SCAN
    try:
        agent_glob.WORKSPACE_MAX_SCAN = 3
        result = agent_glob.glob("*.py")
        lines = result.splitlines()
        assert lines[0] == "3 matches for `*.py`:", result
        assert lines[-1] == ("(The walk stopped at 3 entries, so paths beyond "
                             "that were never examined.)"), result
    finally:
        agent_glob.WORKSPACE_MAX_SCAN = saved
        box.close()


def test_the_ceiling_note_appears_on_an_empty_result_too():
    """The empty result is where hiding it does the most damage: the path
    being looked for may be exactly the one the walk never reached, and
    "No files match" would then be a claim nobody checked."""
    box = Workspace(files={"a.py": "1\n", "b.py": "2\n", "c.py": "3\n"})
    saved = agent_glob.WORKSPACE_MAX_SCAN
    try:
        agent_glob.WORKSPACE_MAX_SCAN = 3
        result = agent_glob.glob("*.rs")
        lines = result.splitlines()
        assert lines[0] == "No files match: *.rs", result
        assert lines[-1] == ("(The walk stopped at 3 entries, so paths beyond "
                             "that were never examined.)"), result
    finally:
        agent_glob.WORKSPACE_MAX_SCAN = saved
        box.close()


def test_a_walk_that_finished_never_claims_it_stopped_short():
    """The other side of the boundary. A basket one short of the ceiling is a
    walk that ran out of workspace rather than out of budget, and saying so
    would send the reader looking for files that are not there."""
    box = Workspace(files={"a.py": "1\n", "b.py": "2\n", "c.py": "3\n"})
    saved = agent_glob.WORKSPACE_MAX_SCAN
    try:
        agent_glob.WORKSPACE_MAX_SCAN = 4
        assert "The walk stopped at" not in agent_glob.glob("*.py")
        assert "The walk stopped at" not in agent_glob.glob("*.rs")
    finally:
        agent_glob.WORKSPACE_MAX_SCAN = saved
        box.close()


# --- guards -----------------------------------------------------------------

def test_a_missing_or_unusable_pattern_is_refused_before_anything_is_walked():
    box = Workspace(files={"a.py": "x\n"})
    refusal = "glob needs a 'pattern' -- there is nothing to look for."
    try:
        assert agent_glob.glob("") == refusal
        assert agent_glob.glob(None) == refusal
        assert agent_glob.glob(123) == refusal
        assert agent_glob.glob("   ") == refusal
        assert agent_glob.glob("\t\n ") == refusal
        # The pattern is checked first, so a second mistake does not hide it.
        assert agent_glob.glob("", kind="sideways") == refusal
    finally:
        box.close()


def test_a_path_that_does_not_exist_is_reported_rather_than_raised():
    box = Workspace(files={"a.py": "x\n"})
    try:
        assert agent_glob.glob("*.py", path="nowhere") == "Path not found: nowhere"
    finally:
        box.close()


def test_a_path_naming_a_single_file_matches_nothing_and_blames_nothing():
    """A `path` is a subtree to search under. Given a file there is no walk at
    all, so the answer is empty -- and it does NOT offer pruned machinery as
    the reason, which would send the reader looking in the wrong place."""
    box = Workspace(files={"a.py": "x\n"})
    try:
        result = agent_glob.glob("*.py", path="a.py")
        assert result.splitlines()[0] == "No files match: *.py", result
        assert "Only paths under a.py were searched." in result, result
        assert "never searched" not in result, result
    finally:
        box.close()


def test_a_pattern_that_cannot_be_compiled_is_answered_rather_than_raised():
    """glob_filter escapes everything it does not treat as a wildcard, so no
    pattern should reach this. The guard is here so that one stray character
    could never end a turn, and it is pinned so a later edit cannot drop it."""
    box = Workspace(files={"a.py": "x\n"})
    real = agent_file_ops.glob_filter

    def exploding(_pattern):
        raise RuntimeError("no such thing")

    try:
        agent_file_ops.glob_filter = exploding
        assert agent_glob.glob("*.py") == "Invalid pattern: no such thing"
    finally:
        agent_file_ops.glob_filter = real
        box.close()


# --- what is never walked ---------------------------------------------------

def test_machinery_directories_never_appear_and_are_never_descended_into():
    """Pruned during the walk, not filtered after it. A node_modules that is
    merely filtered out has already cost the time it took to read."""
    box = Workspace(files={"src/a.py": "a\n",
                           "node_modules/pkg/index.js": "junk\n",
                           ".git/config": "[core]\n",
                           "__pycache__/a.pyc": "b\n",
                           ".venv/lib/venvfile.py": "c\n"})
    try:
        result = agent_glob.glob("*", kind="any")
        assert "src/a.py" in result, result
        for absent in ("node_modules", ".git", "__pycache__", ".venv",
                       "index.js", "config", "a.pyc", "venvfile.py"):
            assert absent not in result, (absent, result)
    finally:
        box.close()


def test_a_pattern_that_names_machinery_still_finds_nothing():
    """The prune is not defeated by asking for it directly."""
    box = Workspace(files={"node_modules/pkg/index.js": "junk\n",
                           "src/a.py": "a\n"})
    try:
        result = agent_glob.glob("node_modules/**/*", kind="any")
        assert result.splitlines()[0] == "No paths match: node_modules/**/*", result
    finally:
        box.close()


# --- security ---------------------------------------------------------------

def test_a_path_outside_the_workspace_raises_rather_than_answering():
    """safe_path's ValueError is allowed straight out: the dispatcher turns it
    into words. An empty result here would read as "there is nothing there",
    which is a different and much worse answer."""
    box = Workspace(files={"inside.py": "x\n"})
    attempts = ["../..", str(box.path.parent), "/etc"]
    if os.name == "nt":
        attempts.append("C:/Windows")
    try:
        for attempt in attempts:
            refused = False
            try:
                agent_glob.glob("*.py", path=attempt)
            except ValueError:
                refused = True
            assert refused, "a path outside the root must be refused: %r" % attempt
    finally:
        box.close()


def test_a_pattern_full_of_dot_dot_simply_matches_nothing():
    """Matching happens on the path from the WORKSPACE root, which is what
    makes an escaping pattern harmless: it describes somewhere no
    workspace-relative path can ever be written."""
    box = Workspace(files={"a.py": "x\n", "src/b.py": "y\n"})
    try:
        assert agent_glob.glob("../../*").splitlines()[0] == "No files match: ../../*"
        assert agent_glob.glob("../*.py").splitlines()[0] == "No files match: ../*.py"
        result = agent_glob.glob("**/../*.py")
        assert result.splitlines()[0] == "No files match: **/../*.py", result
    finally:
        box.close()


def test_a_symlink_leaving_the_workspace_is_never_named_and_never_followed():
    """os.walk does not descend a directory symlink, and within_workspace is
    what stops the link ITSELF being named -- a row is a path the model will
    hand to read_lines, so naming one that resolves outside would be handing
    it a way out of the sandbox."""
    outside = Path(tempfile.mkdtemp(prefix="tmt_glob_outside_")).resolve()
    (outside / "secret.py").write_bytes(b"classified\n")
    box = Workspace(files={"src/inside.py": "x\n"})
    try:
        try:
            os.symlink(str(outside), str(box.path / "outward"),
                       target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError, TypeError):
            return          # A platform or account that cannot make one.
        for kind in ("files", "dirs", "any"):
            result = agent_glob.glob("*", kind=kind)
            assert "outward" not in result, (kind, result)
            assert "secret.py" not in result, (kind, result)
            assert str(outside) not in result, (kind, result)
        # Not vacuous: the walk really did run and really did find things.
        assert "src/inside.py" in agent_glob.glob("*"), agent_glob.glob("*")
    finally:
        box.close()
        remove_tree(outside)


def test_a_narrowed_path_still_answers_in_workspace_relative_rows():
    """`path` narrows what is walked; it does not change what a row means. A
    row relative to the subtree would not survive being handed to read_lines."""
    box = Workspace(files={"src/deep/a.py": "x\n", "other/b.py": "y\n"})
    try:
        result = agent_glob.glob("*.py", path="src")
        assert rows_of(result) == ["src/deep/a.py"], result
        assert "other/b.py" not in result, result
    finally:
        box.close()


# --- the disk misbehaving ---------------------------------------------------

def test_a_directory_that_cannot_be_read_is_skipped_rather_than_raising():
    """chmod does not deny a listing on Windows, so the refusal is injected at
    os.scandir, which is what os.walk reaches for. The point under test is
    that one unreadable directory costs its own contents and not the result."""
    box = Workspace(files={"locked/hidden.py": "x\n", "open/seen.py": "y\n"})
    real_scandir = os.scandir

    def refusing_scandir(target):
        if os.path.basename(str(target).rstrip("/\\")) == "locked":
            raise PermissionError(13, "Permission denied")
        return real_scandir(target)

    try:
        agent_file_ops.os.scandir = refusing_scandir
        result = agent_glob.glob("*.py")
        assert rows_of(result) == ["open/seen.py"], result
        assert "hidden.py" not in result, result
    finally:
        agent_file_ops.os.scandir = real_scandir
        box.close()


def test_an_entry_that_cannot_be_measured_is_treated_as_not_a_directory():
    """A file that vanished mid-walk, or that nobody may stat, is a fact about
    that one entry and never a reason to lose the whole result."""
    class Unmeasurable:
        def is_dir(self):
            raise OSError(13, "Permission denied")

    assert agent_glob._is_dir(Unmeasurable()) is False


# --- the module's own promises ----------------------------------------------

def test_glob_never_opens_a_file():
    """Every row costs a stat and no read at all. A path-discovery tool that
    read contents would spend a large repository's worth of time and tokens
    answering a question about names."""
    source = Path(agent_glob.__file__).read_text(encoding="utf-8")
    for forbidden in ("read_text", "open(", "read_bytes", "readlines"):
        assert forbidden not in source, forbidden


def test_glob_does_not_carry_a_second_traversal_of_its_own():
    """One walk, one set of pruning rules, one scan ceiling, one answer to
    what counts as inside the workspace. A second os.walk here would be a
    second copy of all four, and only one of the two would be updated the day
    any of them changed."""
    source = Path(agent_glob.__file__).read_text(encoding="utf-8")
    assert "os.walk" not in source, source
    assert "iter_workspace_entries" in source, source
    assert "within_workspace" in source, source


def test_the_published_ceilings_are_what_the_spec_names():
    assert agent_glob.GLOB_MAX_RESULTS == 200
    assert agent_glob.GLOB_HARD_MAX == 1000


def test_a_boolean_limit_is_refused_rather_than_read_as_one():
    """`int(True)` is 1, so `"limit": true` would come back as a single row
    plus a truncation notice for a cap nobody chose."""
    box = Workspace(files={"a.py": "x\n", "b.py": "y\n"})
    try:
        assert agent_glob.glob("*.py", limit=True) == (
            "limit must be a whole number of results")
        assert agent_glob.glob("*.py", limit=False) == (
            "limit must be a whole number of results")
        # A real number still works: the guard names bools, not everything
        # that is not an int.
        assert "showing the first 1" in agent_glob.glob("*.py", limit=1)
    finally:
        box.close()


def test_a_path_that_is_not_text_is_answered_in_words():
    """safe_path builds `root / user_path`, a TypeError for anything not
    path-like. Every other bad-argument shape here is answered in words for
    one round; this one used to escape as a traceback instead."""
    box = Workspace(files={"a.py": "x\n"})
    try:
        assert agent_glob.glob("*.py", path=5) == (
            "path must be a folder path written as text")
        assert agent_glob.glob("*.py", path=["."]) == (
            "path must be a folder path written as text")
        # None is not "the wrong shape": it is how the key is left out.
        assert "1 match" in agent_glob.glob("*.py", path=None)
    finally:
        box.close()
