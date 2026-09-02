"""Tests for `grep`, the one action that searches file contents.

It replaced a pair of tools -- an exact, case-sensitive one that could match a
block spanning lines, and a loose, case-insensitive one that could not -- so
everything both of them did well has to still be true here, and the halves that
used to be two verbs are now two keys.

These run against real directories on a real disk, like the search tests they
grew out of, because every hazard in this module is a property of the disk
rather than of a mock: a binary file, a CRLF file, a file too big to open, a
symlink pointing out of the workspace.

What is pinned here beyond the happy paths, because each is a decision a later
edit could quietly reverse:

* one row per LINE a match starts on, never one per occurrence;
* the header's totals counted over everything examined, not over what fitted;
* a literal needle may span lines, a regex is matched per line and is compiled
  exactly as its author wrote it;
* a `path` carrying `*` or `?` becomes the glob and stops being a path, while
  `[` stays an ordinary character so a directory really named `[draft]` is a
  subtree;
* nothing outside the workspace is ever read or named;
* no result ever contains a whole file.
"""

import inspect
import os
import shutil
import stat
import tempfile
from pathlib import Path

import agent_config
import agent_file_ops
import agent_grep


def remove_tree(path):
    """Delete a temp tree, including read-only files on Windows."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


class Workspace:
    """A throwaway directory pointed at by agent_config.ROOT_DIR.

    close() restores the previous root and must run in a finally block: a
    leaked root would point every later test at a deleted directory.
    """

    def __init__(self, files=None):
        self.previous_root = agent_config.ROOT_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_grep_")).resolve()
        for name, body in (files or {}).items():
            self.write(name, body)
        agent_config.set_workspace_root(self.path)

    def write(self, name, body):
        """Bytes go down exactly as given -- the line-ending tests depend on
        nothing translating on the way in either."""
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            target.write_bytes(body)
        else:
            target.write_bytes(body.encode("utf-8"))
        return target

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        remove_tree(self.path)


def rows(out):
    """The match rows of a flat result: everything after the header and its
    blank line. Written as a helper so a test asserting on the count of them
    cannot be fooled by a trailing note."""
    body = out.splitlines()[2:]
    return [line for line in body
            if line and not line.startswith("(") and ":" in line]


# --- the constants this module promises -------------------------------------

def test_the_constants_are_the_documented_numbers():
    """Every one of these is quoted in a message the model reads, so a change
    to any of them is a change to the tool's contract rather than a tune."""
    assert agent_grep.GREP_MAX_MATCHES == 100
    assert agent_grep.GREP_HARD_MAX == 1000
    assert agent_grep.GREP_MAX_CONTEXT == 10
    assert agent_grep.GREP_MAX_LINE == 400
    assert agent_grep.GREP_MAX_FILE_BYTES == 2_000_000
    # Derived from the byte count, so the sentence cannot go on saying 2 MB
    # after somebody changes the number.
    assert agent_grep._SIZE_LABEL == "2 MB"


# --- the flat row, which is the default form --------------------------------

def test_grep_reports_the_path_the_line_number_and_the_line():
    box = Workspace(files={"src/a.py": "one\ntwo\nTARGET here\nfour\n"})
    try:
        out = agent_grep.grep("TARGET")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert out.splitlines()[1] == "", out
        assert "src/a.py:3: TARGET here" in out, out
    finally:
        box.close()


def test_the_row_is_stripped_of_the_indentation_it_was_found_with():
    """A line seen out of its block carries nothing in its leading spaces and
    they cost the columns the line itself needs."""
    box = Workspace(files={"a.py": "def f():\n        deep = TARGET\n"})
    try:
        out = agent_grep.grep("TARGET")
        assert "a.py:2: deep = TARGET" in out, out
        assert "a.py:2:         deep" not in out, out
    finally:
        box.close()


def test_line_numbers_are_the_files_own_numbering():
    box = Workspace(files={"a.txt": "HIT\nb\nc\nd\nHIT"})
    try:
        out = agent_grep.grep("HIT")
        assert "a.txt:1: HIT" in out, out
        assert "a.txt:5: HIT" in out, out
        assert "2 matches in 1 file" in out, out
    finally:
        box.close()


def test_a_match_is_reported_once_per_line_not_once_per_occurrence():
    """The deliberate difference from the search this replaced, which counted
    occurrences. A result is a list of PLACES to go and read, and the same
    place named ninety times is still one place -- it is also what keeps the
    header's count and the number of rows the same number."""
    box = Workspace(files={"a.txt": "ab" * 90 + "\n"})
    try:
        out = agent_grep.grep("ab")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert len(rows(out)) == 1, out
        assert out.count("a.txt:1:") == 1, out
        assert "90" not in out, out
    finally:
        box.close()


def test_several_occurrences_on_several_lines_are_one_row_each():
    box = Workspace(files={"a.txt": "x x x\nnothing\nx x\n"})
    try:
        out = agent_grep.grep("x")
        assert out.splitlines()[0] == "2 matches in 1 file", out
        assert len(rows(out)) == 2, out
        assert "a.txt:1: x x x" in out, out
        assert "a.txt:3: x x" in out, out
    finally:
        box.close()


def test_totals_are_real_across_files():
    box = Workspace(files={"a.txt": "hit x hit\nhit\n", "b.txt": "hit\n",
                           "c.txt": "nothing\n"})
    try:
        out = agent_grep.grep("hit")
        # Three LINES carry it, in two files -- the first line holds it twice
        # and is still one place.
        assert out.splitlines()[0] == "3 matches in 2 files", out
        assert "c.txt" not in out, out
    finally:
        box.close()


# --- the header's totals, and what the cap governs --------------------------

def test_the_header_counts_everything_examined_not_what_was_rendered():
    """The cap governs what is shown and nothing else. A header reporting the
    shown figure would understate the work still out there every single time
    it truncated, which is the one place this tool could quietly mislead."""
    box = Workspace(files={"a.txt": "hit\nhit\nhit\nhit\nhit\n"})
    try:
        out = agent_grep.grep("hit", limit=2)
        assert out.splitlines()[0] == (
            "5 matches in 1 file -- showing the first 2, the limit for one "
            "result. Narrow the query, or pass a 'glob'."), out
        assert len(rows(out)) == 2, out
    finally:
        box.close()


def test_the_file_count_covers_files_beyond_the_cap_too():
    """Every file is examined even once the cap is spent, so the second half
    of the header is as real as the first."""
    box = Workspace(files={"f%d.txt" % n: "hit\n" for n in range(1, 6)})
    try:
        out = agent_grep.grep("hit", limit=2)
        assert out.startswith("5 matches in 5 files -- showing the first 2"), out
        assert len(rows(out)) == 2, out
    finally:
        box.close()


def test_an_untruncated_header_says_nothing_about_a_limit():
    box = Workspace(files={"a.txt": "hit\n"})
    try:
        out = agent_grep.grep("hit")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "showing the first" not in out, out
    finally:
        box.close()


# --- literal matching, which may span lines ---------------------------------

def test_a_literal_needle_may_span_lines_and_reports_the_line_it_starts_on():
    """The half of this tool the old exact search existed for: a line-at-a-time
    search cannot find the five lines a model is about to replace."""
    body = "head\ndef f():\n    a = 1\n    return a\ntail\n"
    box = Workspace(files={"m.py": body, "other.py": "def f():\n    pass\n"})
    try:
        out = agent_grep.grep("def f():\n    a = 1\n    return a")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "m.py:2: def f():" in out, out
        assert "other.py" not in out, out
    finally:
        box.close()


def test_a_needle_written_with_an_escaped_newline_works():
    """A model writes JSON, so the newline in its query arrives as the two
    characters backslash-n. decode_content is what makes that a real newline."""
    box = Workspace(files={"a.txt": "one\na\nb\ntwo\n"})
    try:
        out = agent_grep.grep("a\\nb")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "a.txt:2: a" in out, out
    finally:
        box.close()


def test_a_multi_line_needle_matches_a_crlf_file():
    """A block copied out of a CRLF file arrives with plain newlines in it, and
    the match still has to happen -- both sides are flattened to LF first."""
    box = Workspace(files={"m.py": b"head\r\ndef f():\r\n    a = 1\r\ntail\r\n"})
    try:
        out = agent_grep.grep("def f():\n    a = 1")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "m.py:2: def f():" in out, out
    finally:
        box.close()


def test_a_regex_metacharacter_is_an_ordinary_character_by_default():
    """Literal means literal: `a.c` does not find `abc`."""
    box = Workspace(files={"a.txt": "abc\na.c\n"})
    try:
        out = agent_grep.grep("a.c")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "a.txt:2: a.c" in out, out
        assert "a.txt:1:" not in out, out
    finally:
        box.close()


# --- regex matching ---------------------------------------------------------

def test_a_regex_matches_per_line():
    """A pattern allowed to span lines lets one `.*` swallow the file and
    report it as a single match, which is the opposite of a list of places."""
    box = Workspace(files={"a.txt": "alpha\nbeta\ngamma\n"})
    try:
        out = agent_grep.grep("a.*a", regex=True)
        assert out.splitlines()[0] == "2 matches in 1 file", out
        assert "a.txt:1: alpha" in out, out
        assert "a.txt:3: gamma" in out, out
        assert "a.txt:2:" not in out, out
    finally:
        box.close()


def test_a_regex_is_compiled_exactly_as_it_was_written():
    """A regex is NOT run through decode_content. Decoding it would rewrite its
    author's own escapes, which is a change of meaning rather than the
    convenience it is for a literal.

    The query is the three characters backslash, backslash, n -- a regex for
    "a backslash followed by an n", which is what the file holds. Decoded
    first, it would become a backslash followed by a real newline, and since a
    regex is matched a line at a time that can never match anything.
    """
    box = Workspace(files={"a.txt": 'wanted = "a\\nb"\n'})
    try:
        found = agent_grep.grep("\\\\n", regex=True)
        assert found.splitlines()[0] == "1 match in 1 file", found
        assert 'a.txt:1: wanted = "a\\nb"' in found, found
        # The same query read as a literal IS decoded, so it looks for a
        # backslash followed by a real newline and correctly finds nothing.
        missed = agent_grep.grep("\\\\n")
        assert missed.startswith("No match for:"), missed
    finally:
        box.close()


def test_an_invalid_regex_is_reported_rather_than_raised():
    box = Workspace(files={"a.txt": "x\n"})
    try:
        out = agent_grep.grep("(unclosed", regex=True)
        assert out.startswith("Invalid regex: "), out
        assert "a.txt" not in out, out
    finally:
        box.close()


# --- case ------------------------------------------------------------------

def test_matching_is_case_sensitive_by_default_and_says_so():
    """`Path` is not `path`, and a model that cannot tell them apart edits on
    a guess. The hint is named because it is the likeliest reason a search that
    should have worked did not, and the fix is one key."""
    box = Workspace(files={"a.py": "Alpha = 1\n"})
    try:
        missed = agent_grep.grep("alpha")
        assert missed.splitlines()[0] == "No match for: alpha", missed
        assert ('Matching is case-sensitive; pass "ignore_case": true for a '
                "loose match.") in missed, missed
        assert "1 match in 1 file" in agent_grep.grep("Alpha")
    finally:
        box.close()


def test_ignore_case_loosens_the_literal_match():
    box = Workspace(files={"a.py": "Alpha = 1\n"})
    try:
        out = agent_grep.grep("alpha", ignore_case=True)
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "a.py:1: Alpha = 1" in out, out
    finally:
        box.close()


def test_ignore_case_loosens_the_regex_match_as_well():
    """Both modes end up as one compiled pattern so that ignore_case is the
    same flag in both."""
    box = Workspace(files={"a.py": "Alpha = 1\n"})
    try:
        assert agent_grep.grep("al.ha", regex=True).startswith("No match")
        out = agent_grep.grep("al.ha", regex=True, ignore_case=True)
        assert out.splitlines()[0] == "1 match in 1 file", out
    finally:
        box.close()


def test_the_case_hint_is_not_offered_to_someone_who_already_took_it():
    box = Workspace(files={"a.py": "Alpha = 1\n"})
    try:
        out = agent_grep.grep("zzz", ignore_case=True)
        assert out.splitlines()[0] == "No match for: zzz", out
        assert "case-sensitive" not in out, out
    finally:
        box.close()


# --- choosing what to read: path and glob -----------------------------------

def test_grep_can_be_pointed_at_one_subtree():
    box = Workspace(files={"src/a.txt": "NEEDLE\n", "other/b.txt": "NEEDLE\n"})
    try:
        out = agent_grep.grep("NEEDLE", path="src")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "src/a.txt" in out, out
        assert "other/b.txt" not in out, out
    finally:
        box.close()


def test_grep_can_be_pointed_at_one_file():
    box = Workspace(files={"a.txt": "NEEDLE\n", "b.txt": "NEEDLE\n"})
    try:
        out = agent_grep.grep("NEEDLE", path="a.txt")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "a.txt:1: NEEDLE" in out, out
        assert "b.txt" not in out, out
    finally:
        box.close()


def test_grep_filters_by_glob():
    box = Workspace(files={"src/deep/a.py": "NEEDLE\n", "src/b.py": "NEEDLE\n",
                           "docs/c.md": "NEEDLE\n", "top.py": "NEEDLE\n"})
    try:
        out = agent_grep.grep("NEEDLE", glob="src/**/*.py")
        assert out.splitlines()[0] == "2 matches in 2 files", out
        assert "src/deep/a.py" in out, out
        assert "src/b.py" in out, out
        assert "docs/c.md" not in out, out
        assert "top.py" not in out, out
    finally:
        box.close()


def test_a_glob_with_no_separator_in_it_means_anywhere():
    """What a model intends by `*.py` is every Python file, not the root's."""
    box = Workspace(files={"src/deep/a.py": "NEEDLE\n", "docs/c.md": "NEEDLE\n",
                           "top.py": "NEEDLE\n"})
    try:
        out = agent_grep.grep("NEEDLE", glob="*.py")
        assert out.splitlines()[0] == "2 matches in 2 files", out
        assert "src/deep/a.py" in out, out
        assert "top.py" in out, out
        assert "docs/c.md" not in out, out
    finally:
        box.close()


def test_a_path_carrying_a_glob_star_is_promoted_to_the_glob():
    """The model will write grep("run_command", path="*.py") and that must work.
    There is no workspace in which a directory literally named `*.py` is the
    useful reading, and refusing the shorthand costs a whole turn to say so."""
    box = Workspace(files={"src/a.py": "NEEDLE\n", "b.md": "NEEDLE\n"})
    try:
        out = agent_grep.grep("NEEDLE", path="*.py")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "src/a.py:1: NEEDLE" in out, out
        assert "b.md" not in out, out
    finally:
        box.close()


def test_a_path_carrying_a_question_mark_is_promoted_too():
    box = Workspace(files={"a1.py": "NEEDLE\n", "a12.py": "NEEDLE\n"})
    try:
        out = agent_grep.grep("NEEDLE", path="a?.py")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "a1.py:1: NEEDLE" in out, out
        assert "a12.py" not in out, out
    finally:
        box.close()


def test_a_promoted_path_stops_being_a_path():
    """It becomes the glob and nothing is left behind, so the sentence a
    fruitless search prints names the filter that was really applied rather
    than a subtree that was never consulted."""
    box = Workspace(files={"a.py": "something\n"})
    try:
        out = agent_grep.grep("zzz", path="*.py")
        assert "Only paths matching *.py were examined." in out, out
        assert "Only paths under" not in out, out
    finally:
        box.close()


def test_an_explicit_glob_leaves_the_path_alone():
    """The promotion only fires when no glob was given, so a caller who meant
    both gets both."""
    box = Workspace(files={"src/a.py": "NEEDLE\n", "src/b.md": "NEEDLE\n",
                           "other/c.py": "NEEDLE\n"})
    try:
        out = agent_grep.grep("NEEDLE", path="src", glob="*.py")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "src/a.py" in out, out
        assert "src/b.md" not in out, out
        assert "other/c.py" not in out, out
    finally:
        box.close()


def test_a_directory_named_with_brackets_is_a_subtree_not_a_pattern():
    """`[` is deliberately not one of the marks that promote a path.
    agent_file_ops escapes it, so a bracket is an ordinary character to the
    matcher rather than a character class -- reading it as a pattern would send
    a directory genuinely named `[draft]` down the filter path, where it would
    be compared against whole relative paths and match nothing. A real subtree
    turned into an empty result by a guess."""
    box = Workspace(files={"[draft]/x.txt": "NEEDLE here\n",
                           "elsewhere.txt": "NEEDLE\n"})
    try:
        out = agent_grep.grep("NEEDLE", path="[draft]")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "[draft]/x.txt:1: NEEDLE here" in out, out
        assert "elsewhere.txt" not in out, out
    finally:
        box.close()


def test_machinery_directories_are_never_searched():
    """Same rule as the walk: they are pruned rather than filtered, so a match
    inside node_modules is not a match at all."""
    box = Workspace(files={"src/a.py": "NEEDLE\n",
                           "node_modules/pkg/index.js": "NEEDLE\n",
                           ".git/config": "NEEDLE\n",
                           "__pycache__/x.txt": "NEEDLE\n"})
    try:
        out = agent_grep.grep("NEEDLE")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "src/a.py" in out, out
        assert "node_modules" not in out, out
        assert ".git" not in out, out
        assert "__pycache__" not in out, out
    finally:
        box.close()


# --- when nothing matched ---------------------------------------------------

def test_nothing_matched_says_so_and_names_the_glob():
    box = Workspace(files={"a.py": "content\n"})
    try:
        out = agent_grep.grep("zzz", glob="*.md")
        assert out.splitlines()[0] == "No match for: zzz", out
        assert "Only paths matching *.md were examined." in out, out
        assert "Only paths under" not in out, out
    finally:
        box.close()


def test_nothing_matched_names_the_path():
    box = Workspace(files={"src/a.py": "content\n"})
    try:
        out = agent_grep.grep("zzz", path="src")
        assert "Only paths under src were examined." in out, out
        assert "Only paths matching" not in out, out
    finally:
        box.close()


def test_the_no_match_line_quotes_only_the_head_of_the_query():
    """It has to fit on one row, so only the first line is quoted and it is cut
    at 120 characters."""
    box = Workspace(files={"a.txt": "content\n"})
    try:
        out = agent_grep.grep("q" * 200)
        assert out.splitlines()[0] == "No match for: " + "q" * 120, out
        multi = agent_grep.grep("first line\nsecond line")
        assert multi.splitlines()[0] == "No match for: first line", multi
        assert "second line" not in multi, multi
    finally:
        box.close()


# --- what was skipped, and saying so ----------------------------------------

def test_a_binary_file_is_skipped_counted_and_never_quoted():
    box = Workspace(files={"good.txt": "NEEDLE\n",
                           "blob.bin": b"NEEDLE\x00\x01\x02rubbish"})
    try:
        out = agent_grep.grep("NEEDLE")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "good.txt" in out, out
        assert "blob.bin" not in out, out
        assert "rubbish" not in out, out
        assert "(1 binary file skipped.)" in out, out
    finally:
        box.close()


def test_a_fruitless_search_still_says_what_it_never_opened():
    """A fruitless search and a search that never opened the only place the
    text could have been are different answers, and the reader has to be able
    to tell which one they got."""
    box = Workspace(files={"blob.bin": b"NEEDLE\x00\x01\x02rubbish"})
    try:
        out = agent_grep.grep("NEEDLE")
        assert out.splitlines()[0] == "No match for: NEEDLE", out
        assert "1 binary file skipped." in out, out
        assert "rubbish" not in out, out
    finally:
        box.close()


def test_a_file_over_the_size_limit_is_skipped_and_counted():
    """The cheap question is asked first -- a size taken from stat costs
    nothing, where reading a 40 MB fixture to discover it is too big costs the
    whole action."""
    box = Workspace(files={"small.txt": "NEEDLE\n"})
    box.write("huge.txt", "NEEDLE\n" + "x" * (agent_grep.GREP_MAX_FILE_BYTES + 1))
    try:
        out = agent_grep.grep("NEEDLE")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "small.txt" in out, out
        assert "huge.txt" not in out, out
        assert "(1 file over 2 MB skipped.)" in out, out
    finally:
        box.close()


def test_an_unreadable_file_is_skipped_and_counted():
    """A permission error on one entry never ends the search. The refusal is
    injected at Path.read_bytes rather than with chmod, because a Windows
    account can usually read its own read-only file and the case would then
    silently go untested on the machine this is developed on."""
    box = Workspace(files={"a.txt": "NEEDLE\n", "bad.txt": "NEEDLE\n"})
    real_read_bytes = Path.read_bytes
    denied = (box.path / "bad.txt").resolve()

    def refusing(self, *args, **kwargs):
        if Path(self).resolve() == denied:
            raise PermissionError(13, "denied")
        return real_read_bytes(self, *args, **kwargs)

    try:
        Path.read_bytes = refusing
        out = agent_grep.grep("NEEDLE")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "a.txt:1: NEEDLE" in out, out
        assert "bad.txt" not in out, out
        assert "(1 unreadable file skipped.)" in out, out
    finally:
        Path.read_bytes = real_read_bytes
        box.close()


def test_the_walk_ceiling_is_reported_when_it_was_hit():
    """A truncated view that claims to be complete is worse than no view."""
    box = Workspace(files={"a.txt": "one\n", "b.txt": "one\n"})
    saved_config = agent_config.WORKSPACE_MAX_SCAN
    saved_ops = agent_file_ops.WORKSPACE_MAX_SCAN
    try:
        agent_config.WORKSPACE_MAX_SCAN = 2
        agent_file_ops.WORKSPACE_MAX_SCAN = 2
        out = agent_grep.grep("one")
        assert ("(The walk stopped at 2 entries, so files beyond that were "
                "never examined.)") in out, out
    finally:
        agent_config.WORKSPACE_MAX_SCAN = saved_config
        agent_file_ops.WORKSPACE_MAX_SCAN = saved_ops
        box.close()


def test_nothing_is_said_about_a_ceiling_that_was_not_hit():
    box = Workspace(files={"a.txt": "one\n"})
    try:
        out = agent_grep.grep("one")
        assert "The walk stopped" not in out, out
        assert "skipped" not in out, out
    finally:
        box.close()


# --- one line is never allowed to be the whole reply ------------------------

def test_a_long_line_is_cut_and_says_that_it_was():
    """One line of a minified bundle is the whole bundle. The marker is what
    stops a truncated line being read as the whole line."""
    box = Workspace(files={"a.txt": "NEEDLE" + "z" * 450 + "TAIL\n"})
    try:
        out = agent_grep.grep("NEEDLE")
        row = rows(out)[0]
        body = row.split(": ", 1)[1]
        assert len(body) == 404, len(body)
        assert body.endswith(" ..."), body
        assert body[:400] == ("NEEDLE" + "z" * 450)[:400]
        assert "TAIL" not in out, out
    finally:
        box.close()


def test_a_line_that_fits_is_not_marked_as_cut():
    box = Workspace(files={"a.txt": "NEEDLE short line\n"})
    try:
        out = agent_grep.grep("NEEDLE")
        assert rows(out)[0] == "a.txt:1: NEEDLE short line", out
        assert " ..." not in out, out
    finally:
        box.close()


# --- context lines ----------------------------------------------------------

def test_context_lines_are_marked_apart_from_the_match():
    """Colour is never the message, so the two have to read apart in plain
    text: `>` is the match, `|` is the surrounding."""
    box = Workspace(files={"a.txt": "one\ntwo\nHIT\nfour\nfive\n"})
    try:
        out = agent_grep.grep("HIT", context=1)
        assert "a.txt:3:" in out, out
        assert "     2 | two" in out, out
        assert "     3 > HIT" in out, out
        assert "     4 | four" in out, out
        assert "one" not in out, out
        assert "five" not in out, out
    finally:
        box.close()


def test_every_line_of_a_multi_line_match_is_marked_as_match():
    box = Workspace(files={"m.py": "head\ndef f():\n    a = 1\n    return a\ntail\n"})
    try:
        out = agent_grep.grep("def f():\n    a = 1\n    return a", context=1)
        assert "     1 | head" in out, out
        assert "     2 > def f():" in out, out
        assert "     3 >     a = 1" in out, out
        assert "     4 >     return a" in out, out
        assert "     5 | tail" in out, out
    finally:
        box.close()


def test_a_block_keeps_the_indentation_a_flat_row_strips():
    """A block is read as code, where the shape of it is half the meaning; a
    flat row is scanned down a column, where the indentation is noise."""
    box = Workspace(files={"a.py": "def f():\n        HIT\n"})
    try:
        assert "     2 >         HIT" in agent_grep.grep("HIT", context=1)
        assert "a.py:2: HIT" in agent_grep.grep("HIT")
    finally:
        box.close()


def test_context_is_clamped_to_the_ceiling():
    """Ten is already most of a screen per match, and this tool exists to find
    the place rather than to read it."""
    box = Workspace(files={"a.txt": "".join("L%d\n" % n for n in range(1, 41))
                           .replace("L21\n", "HIT\n")})
    try:
        out = agent_grep.grep("HIT", context=999)
        assert "    11 | L11" in out, out
        assert "    31 | L31" in out, out
        assert "L10" not in out, out
        assert "L32" not in out, out
    finally:
        box.close()


def test_a_negative_context_is_simply_no_context():
    box = Workspace(files={"a.txt": "one\nHIT\nthree\n"})
    try:
        out = agent_grep.grep("HIT", context=-5)
        assert "a.txt:2: HIT" in out, out
        assert "one" not in out, out
        assert "three" not in out, out
    finally:
        box.close()


def test_context_does_not_run_off_either_end_of_the_file():
    box = Workspace(files={"a.txt": "HIT\nb\n"})
    try:
        out = agent_grep.grep("HIT", context=5)
        assert "     1 > HIT" in out, out
        assert "     2 | b" in out, out
        assert "     0" not in out, out
        assert "     3" not in out, out
    finally:
        box.close()


# --- the guards -------------------------------------------------------------

def test_an_empty_query_is_refused():
    box = Workspace(files={"a.txt": "x\n"})
    try:
        assert agent_grep.grep("") == agent_grep._NO_QUERY
        assert "nothing to look for" in agent_grep.grep("")
    finally:
        box.close()


def test_a_query_that_is_not_a_string_is_refused():
    box = Workspace(files={"a.txt": "x\n"})
    try:
        for bad in (None, 123, ["x"], {"query": "x"}):
            assert agent_grep.grep(bad) == agent_grep._NO_QUERY, bad
    finally:
        box.close()


def test_a_context_that_is_not_a_number_is_refused():
    """A model that wrote "two" meant something, and silently reading it as
    zero would hide the mistake inside a result that looks like an answer."""
    box = Workspace(files={"a.txt": "HIT\n"})
    try:
        assert agent_grep.grep("HIT", context="two") == (
            "context must be a whole number of lines")
        assert agent_grep.grep("HIT", context=[1]) == (
            "context must be a whole number of lines")
    finally:
        box.close()


def test_a_limit_that_is_not_a_number_is_refused():
    box = Workspace(files={"a.txt": "HIT\n"})
    try:
        assert agent_grep.grep("HIT", limit="lots") == (
            "limit must be a whole number of matches")
    finally:
        box.close()


def test_the_limit_is_clamped_at_both_ends():
    assert agent_grep._as_limit(None) == agent_grep.GREP_MAX_MATCHES
    assert agent_grep._as_limit("") == agent_grep.GREP_MAX_MATCHES
    assert agent_grep._as_limit(0) == 1
    assert agent_grep._as_limit(-40) == 1
    assert agent_grep._as_limit(99999) == agent_grep.GREP_HARD_MAX
    assert agent_grep._as_limit(agent_grep.GREP_HARD_MAX + 1) == 1000
    assert agent_grep._as_limit(7) == 7


def test_a_limit_of_zero_still_shows_one_match():
    """Clamped rather than obeyed: a search that returns nothing because the
    caller wrote 0 is a search that looks like a repository with nothing in
    it."""
    box = Workspace(files={"a.txt": "hit\nhit\nhit\n"})
    try:
        out = agent_grep.grep("hit", limit=0)
        assert out.startswith("3 matches in 1 file -- showing the first 1"), out
        assert len(rows(out)) == 1, out
    finally:
        box.close()


def test_a_path_that_does_not_exist_is_named():
    box = Workspace(files={"a.txt": "x\n"})
    try:
        assert agent_grep.grep("x", path="nowhere") == "Path not found: nowhere"
    finally:
        box.close()


# --- the workspace boundary -------------------------------------------------

def test_a_path_above_the_workspace_raises_rather_than_returning_nothing():
    """safe_path's ValueError is allowed straight out of this module: the agent
    loop turns it into a correction the model can act on, where an empty result
    would read as "there is nothing there"."""
    box = Workspace(files={"a.txt": "x\n"})
    try:
        refused = False
        try:
            agent_grep.grep("x", path="../..")
        except ValueError:
            refused = True
        assert refused, "a path above the root must be refused, not searched"
    finally:
        box.close()


def test_an_absolute_path_outside_the_workspace_is_refused():
    box = Workspace(files={"a.txt": "x\n"})
    outside = str(Path(tempfile.gettempdir()).resolve())
    try:
        refused = False
        try:
            agent_grep.grep("x", path=outside)
        except ValueError:
            refused = True
        assert refused, outside
    finally:
        box.close()


def test_a_drive_or_filesystem_root_path_is_refused():
    box = Workspace(files={"a.txt": "x\n"})
    outside = "C:/Windows" if os.name == "nt" else "/etc"
    try:
        refused = False
        try:
            agent_grep.grep("x", path=outside)
        except ValueError:
            refused = True
        assert refused, outside
    finally:
        box.close()


def test_a_glob_full_of_dot_dot_escapes_nothing():
    """The filter runs over workspace-relative paths, so `../../*` simply
    matches none of them -- it is not a route out."""
    box = Workspace(files={"a.txt": "NEEDLE\n"})
    try:
        out = agent_grep.grep("NEEDLE", glob="../../*")
        assert out.splitlines()[0] == "No match for: NEEDLE", out
        assert "a.txt" not in out, out
    finally:
        box.close()


def test_a_symlink_pointing_outside_the_workspace_contributes_nothing():
    """os.walk does not follow a directory symlink, but a FILE symlink is read
    where it points -- so containment is asked before the file is opened, and a
    guard that read first would already have done the thing it forbids."""
    outside = Path(tempfile.mkdtemp(prefix="tmt_grep_out_")).resolve()
    (outside / "secret.txt").write_bytes(b"SMUGGLED\n")
    box = Workspace(files={"inside.txt": "SMUGGLED\n"})
    try:
        try:
            os.symlink(str(outside / "secret.txt"), str(box.path / "link.txt"))
        except (OSError, NotImplementedError, AttributeError):
            return          # Windows without the privilege: nothing to test.
        out = agent_grep.grep("SMUGGLED")
        assert out.splitlines()[0] == "1 match in 1 file", out
        assert "inside.txt" in out, out
        assert "link.txt" not in out, out
        assert "secret" not in out, out
    finally:
        box.close()
        remove_tree(outside)


def test_every_path_in_a_result_is_relative_to_the_workspace():
    box = Workspace(files={"src/deep/a.py": "NEEDLE\n", "b.py": "NEEDLE\n"})
    try:
        out = agent_grep.grep("NEEDLE")
        assert str(box.path).replace("\\", "/") not in out.replace("\\", "/"), out
        for row in rows(out):
            named = row.rsplit(":", 2)[0]
            assert not Path(named).is_absolute(), row
            assert "\\" not in row, row
        assert "src/deep/a.py:1: NEEDLE" in out, out
    finally:
        box.close()


# --- it answers with places, never with contents ----------------------------

def test_no_result_ever_contains_a_whole_file():
    """There is deliberately no option that returns a file, because a search
    that can return the file is a search the model will reach for instead of
    reading -- and the point of this tool is to make reading the whole
    repository unnecessary."""
    body = "".join("line%d\n" % n for n in range(1, 21)).replace(
        "line7\n", "line7 NEEDLE\n")
    box = Workspace(files={"a.txt": body})
    try:
        flat = agent_grep.grep("NEEDLE")
        assert "a.txt:7: line7 NEEDLE" in flat, flat
        for n in list(range(1, 7)) + list(range(8, 21)):
            assert "line%d" % n not in flat, (n, flat)
        # And with context it shows the context it was asked for and no more.
        near = agent_grep.grep("NEEDLE", context=2)
        for n in (5, 6, 8, 9):
            assert "line%d" % n in near, (n, near)
        for n in (1, 2, 3, 4, 11, 20):
            assert "line%d" % n not in near, (n, near)
    finally:
        box.close()


def test_there_is_no_key_that_asks_for_file_contents():
    """The absence is the feature. A `full`, `body` or `show_file` key added
    later would be the whole guarantee gone, so the signature is pinned."""
    names = list(inspect.signature(agent_grep.grep).parameters)
    assert names == ["query", "path", "glob", "regex", "ignore_case",
                     "context", "limit"], names


# --- arguments of the wrong shape are answered, never raised ----------------


def test_a_boolean_context_or_limit_is_refused_rather_than_read_as_one():
    """`int(True)` is 1, so a bool would quietly become one line of context or
    a one-match cap -- an answer, for a key the model plainly did not mean as a
    count, and a truncation notice for a limit nobody chose. Refused by name,
    the way agent_verify refuses True as an exit code."""
    box = Workspace(files={"a.txt": "needle\n"})
    try:
        assert agent_grep.grep("needle", context=True) == (
            "context must be a whole number of lines")
        assert agent_grep.grep("needle", context=False) == (
            "context must be a whole number of lines")
        assert agent_grep.grep("needle", limit=True) == (
            "limit must be a whole number of matches")
        # A real number is still a real number: the guard names bools, not
        # everything that is not an int.
        assert "1 match" in agent_grep.grep("needle", limit=5)
    finally:
        box.close()


def test_a_path_or_glob_that_is_not_text_is_answered_in_words():
    """safe_path builds `root / user_path`, which is a TypeError for anything
    not path-like, and _run_tool catches only the ValueError a refusal is made
    of. Without the guard the one bad shape that is NOT a security question is
    the one that escapes as a traceback."""
    box = Workspace(files={"a.txt": "needle\n"})
    try:
        assert agent_grep.grep("needle", path=5) == (
            "path must be a folder or file path written as text")
        assert agent_grep.grep("needle", path=["a.txt"]) == (
            "path must be a folder or file path written as text")
        assert agent_grep.grep("needle", glob=5) == (
            "glob must be a path pattern written as text")
        # None is not "the wrong shape": it is how both keys are left out.
        assert "1 match" in agent_grep.grep("needle", path=None, glob=None)
    finally:
        box.close()
