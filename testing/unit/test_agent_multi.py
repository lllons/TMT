"""`agent_multi` on its own: the shape of a multi_tool, and nothing that runs.

Every test here hands `run` a fake dispatcher and reads back what it was
asked to run, in what order, and what the result text says about it. The
module's whole contract is that it decides the shape and runs nothing itself,
so a fake that records calls is the complete instrument: the real dispatcher,
the whitelists and the loops are `test_agent_multi_wiring`'s to drive.

What is pinned, and why each is worth a test of its own:

- one unusable entry refuses the whole action with NOTHING run, because a
  multi_tool that ran four calls and then complained about the fifth would
  have the model re-sending the four;
- the four loop verbs and a nested multi_tool are refused with the place
  they belong named, because reaching them through a dispatcher does
  something other than what the model meant;
- a `for_each` template is a complete call once its file is filled in, which
  is the bug the first smoke run found -- the template was validated before
  the expansion supplied its `path`;
- a cancellation is re-raised and stops the calls after it, because "no
  further tool call executes" is the whole of the kill guarantee;
- the ceiling counts calls AFTER expansion, and a pattern past it is refused
  with the count rather than cut, because a fan-out over "every file" that
  quietly stopped short would be claiming a completeness it did not have.
"""

import agent_config
import agent_multi as M
from agent_manager import WorkerCancelled

from test_agent_workspace import Workspace


class Recorder:
    """A dispatcher that remembers every call and answers from a table."""

    def __init__(self, answers=None, raise_on=None, raise_with=None):
        self.calls = []
        self.answers = dict(answers or {})
        self.raise_on = raise_on
        self.raise_with = raise_with or RuntimeError("boom")

    def __call__(self, call):
        self.calls.append(call)
        subject = call.get("path") or call.get("query") or call.get("command") or ""
        if self.raise_on is not None and subject == self.raise_on:
            raise self.raise_with
        return self.answers.get(subject, "ok %s" % subject)


def multi(*calls, **extra):
    obj = {"action": M.MULTI_ACTION, M.CALLS: list(calls)}
    obj.update(extra)
    return obj


def read(path, **keys):
    keys.update(action="read_file", path=path)
    return keys


FILES = {
    "src/a.py": "A = 1\n",
    "src/b.py": "B = 2\n",
    "src/deep/c.py": "C = 3\n",
    "docs/notes.md": "notes\n",
    "node_modules/pkg/index.py": "never = 1\n",
}


def project():
    """A throwaway workspace, MADE the workspace. `Workspace` only builds the
    directory; `use()` is what points TMT at it, and a `for_each` walked
    over this repository the first time that line was left out."""
    box = Workspace(files=FILES)
    box.use()
    return box


# --- the verb and its keys ------------------------------------------------------

def test_the_verb_and_its_keys_are_spelled_once():
    """The worker loop and the dispatcher both name the verb; they take it
    from here rather than each spelling it, so a rename cannot leave one of
    them dispatching a verb the other refuses."""
    assert M.MULTI_ACTION == "multi_tool"
    assert M.CALLS == "calls"
    assert M.FOR_EACH == "for_each"
    assert M.RAN_KEY.startswith("_"), "the record must be a private key"


def test_calls_must_be_a_non_empty_list_of_objects():
    for bad in ({"action": "multi_tool"},
                {"action": "multi_tool", "calls": []},
                {"action": "multi_tool", "calls": "read_file"},
                {"action": "multi_tool", "calls": {"action": "read_file"}},
                "not an object", None):
        dispatch = Recorder()
        said = M.run(bad, dispatch=dispatch)
        assert "needs \"calls\"" in said, said
        assert said.endswith("Nothing ran."), said
        assert dispatch.calls == [], bad
        assert not M.started(bad)


def test_an_entry_that_is_not_an_object_refuses_the_whole_action_and_names_it():
    dispatch = Recorder()
    obj = multi(read("a"), 7, read("b"))
    said = M.run(obj, dispatch=dispatch)
    assert said.startswith("multi_tool call 2 of 3 cannot run:"), said
    assert "not a JSON object" in said, said
    assert said.endswith("Nothing ran."), said
    # Not the first entry, which was fine: NOTHING ran, because the model is
    # about to resend the corrected list and would otherwise run it twice.
    assert dispatch.calls == []
    assert not M.started(obj)


def test_an_entry_that_fails_validation_refuses_the_whole_action():
    dispatch = Recorder()
    obj = multi(read("a"), {"action": "read_file"})
    said = M.run(obj, dispatch=dispatch)
    assert "call 2 of 2" in said, said
    assert "missing required keys" in said and "path" in said, said
    assert dispatch.calls == []
    unknown = multi({"action": "no_such_verb"})
    said = M.run(unknown, dispatch=dispatch)
    assert "Unknown action" in said and "no_such_verb" in said, said
    assert dispatch.calls == []


def test_the_loop_verbs_and_a_nested_multi_tool_are_refused_with_the_place_they_belong():
    """These four are given their meaning by the loop BEFORE dispatch. Run
    through a dispatcher they do something else -- an `end_conversation`
    inside a list would run the calls after it and then carry on -- so each
    is refused with the place it belongs named, and nothing runs."""
    cases = {
        "send_message": ({"message": "hi"}, "batch"),
        "end_conversation": ({"message": "bye"}, "batch"),
        "internal_response": ({"response": "done"}, "own action"),
        "review_agenda": ({"operation": "show"}, "batch"),
        "multi_tool": ({"calls": [read("a")]}, "flat list"),
    }
    for verb, (keys, place) in cases.items():
        dispatch = Recorder()
        entry = dict(keys, action=verb)
        obj = multi(read("first"), entry)
        said = M.run(obj, dispatch=dispatch)
        assert said.startswith("multi_tool call 2 of 2 cannot run:"), (verb, said)
        assert place in said, (verb, said)
        assert dispatch.calls == [], verb
        assert not M.started(obj), verb


def test_an_old_name_inside_is_translated_before_anything_else_looks_at_it():
    """The compatibility net applies per entry, exactly as it does per batch
    entry: `find_text` becomes `grep` and `done` is refused as the ending it
    now means, rather than as an unknown verb."""
    dispatch = Recorder()
    obj = multi({"action": "find_text", "query": "needle"})
    said = M.run(obj, dispatch=dispatch)
    assert said.startswith("multi_tool ran 1 call."), said
    assert [call["action"] for call in dispatch.calls] == ["grep"]
    said = M.run(multi({"action": "done"}), dispatch=Recorder())
    assert "end_conversation" in said and "Nothing ran." in said, said


# --- running the calls --------------------------------------------------------------

def test_every_call_runs_in_order_and_the_result_is_headed_per_call():
    dispatch = Recorder(answers={"a": "first body", "b": "second body"})
    obj = multi(read("a"), read("b"), {"action": "grep", "query": "needle"})
    said = M.run(obj, dispatch=dispatch)
    assert [call.get("path") or call.get("query") for call in dispatch.calls] == ["a", "b", "needle"]
    lines = said.splitlines()
    assert lines[0] == "multi_tool ran 3 calls.", lines[0]
    assert "[1/3] read_file a" in lines, lines
    assert "[2/3] read_file b" in lines, lines
    assert "[3/3] grep needle" in lines, lines
    assert said.index("first body") < said.index("second body")
    # The record is the calls that ran, paired with what each returned.
    ran = M.ran(obj)
    assert [r for _, r in ran] == ["first body", "second body", "ok needle"]
    assert M.started(obj)


def test_a_call_that_raises_is_marked_failed_and_the_calls_after_it_still_run():
    """A call's own failure is that call's, never the list's. The count is
    in the header so a model reading the top line knows to look for it."""
    dispatch = Recorder(raise_on="b", raise_with=TypeError("no such shape"))
    obj = multi(read("a"), read("b"), read("c"))
    said = M.run(obj, dispatch=dispatch)
    assert said.startswith("multi_tool ran 3 calls; 1 raised and is marked FAILED."), said
    assert "FAILED: the call raised TypeError: no such shape" in said, said
    assert [call["path"] for call in dispatch.calls] == ["a", "b", "c"]
    results = [result for _, result in M.ran(obj)]
    assert results[1].startswith("FAILED:"), results
    assert results[2] == "ok c"


def test_a_cancellation_is_re_raised_and_stops_the_calls_after_it():
    """The kill guarantee, inside a list: after the flag is set no further
    call runs. A cancellation is the agent stopping, not a call failing, and
    recording it as FAILED and carrying on would run every remaining call on
    a worker somebody had just stopped."""
    dispatch = Recorder(raise_on="b", raise_with=WorkerCancelled("agent 3 was cancelled"))
    obj = multi(read("a"), read("b"), read("c"))
    try:
        M.run(obj, dispatch=dispatch)
    except WorkerCancelled as error:
        assert "agent 3" in str(error)
    else:
        raise AssertionError("the cancellation was swallowed")
    assert [call["path"] for call in dispatch.calls] == ["a", "b"], dispatch.calls
    # What DID run is still on the record, so the loop can account for it.
    assert [call["path"] for call, _ in M.ran(obj)] == ["a"]


def test_refuse_is_asked_about_every_template_before_anything_runs():
    """The worker loop's hook. It is asked about the TEMPLATES, before any
    expansion, so a contract violation is recorded once for the entry rather
    than once per matched file -- and a refusal runs nothing."""
    asked = []

    def refuse(entry):
        asked.append(entry["action"])
        return "REFUSED: 'write_file' is not available to you." if entry["action"] == "write_file" else ""

    dispatch = Recorder()
    obj = multi(read("a"), {"action": "write_file", "path": "x", "content": "y"}, read("b"))
    said = M.run(obj, dispatch=dispatch, refuse=refuse)
    assert said.startswith("multi_tool call 2 of 3 cannot run: REFUSED: 'write_file' is not available to you. Nothing ran."), said
    assert asked == ["read_file", "write_file"], asked
    assert dispatch.calls == []
    assert not M.started(obj)
    # And a hook that says "" to everything lets everything run.
    said = M.run(multi(read("a")), dispatch=dispatch, refuse=lambda entry: "")
    assert said.startswith("multi_tool ran 1 call."), said


# --- for_each -----------------------------------------------------------------------

def test_for_each_expands_to_one_call_per_matching_file_in_sorted_order():
    box = project()
    try:
        dispatch = Recorder()
        obj = multi({"action": "read_lines", "for_each": "**/*.py", "start": 1, "end": 2})
        said = M.run(obj, dispatch=dispatch)
        assert said.startswith("multi_tool ran 3 calls."), said
        assert 'for_each "**/*.py" (read_lines) matched 3 files.' in said, said
        assert [call["path"] for call in dispatch.calls] == ["src/a.py", "src/b.py", "src/deep/c.py"]
        # The call the dispatcher sees is an ordinary read_lines: the template
        # key is gone and the range came through unchanged.
        first = dispatch.calls[0]
        assert M.FOR_EACH not in first
        assert (first["start"], first["end"]) == (1, 2)
        assert "[3/3] read_lines src/deep/c.py" in said
    finally:
        box.close()


def test_a_template_with_no_path_of_its_own_is_a_complete_call_once_expanded():
    """The bug the first smoke run found. `read_lines` requires `path`, the
    expansion supplies it, and validating the template as written refused
    the commonest template there is."""
    box = project()
    try:
        templates, refusal = M.entries(multi({"action": "read_lines", "for_each": "*.py"}))
        assert refusal == "", refusal
        assert len(templates) == 1
        # Without for_each the same object IS missing its path.
        _, refusal = M.entries(multi({"action": "read_lines"}))
        assert "missing required keys" in refusal, refusal
    finally:
        box.close()


def test_a_template_and_plain_calls_keep_their_written_order():
    box = project()
    try:
        dispatch = Recorder()
        obj = multi(read("docs/notes.md"),
                    {"action": "read_file", "for_each": "src/*.py"},
                    {"action": "grep", "query": "C"})
        M.run(obj, dispatch=dispatch)
        subjects = [call.get("path") or call.get("query") for call in dispatch.calls]
        assert subjects == ["docs/notes.md", "src/a.py", "src/b.py", "C"], subjects
    finally:
        box.close()


def test_for_each_matching_nothing_is_said_and_refused_only_when_it_was_all_there_was():
    box = project()
    try:
        dispatch = Recorder()
        alone = multi({"action": "read_file", "for_each": "*.zzz"})
        said = M.run(alone, dispatch=dispatch)
        assert said.startswith("multi_tool has nothing to run:"), said
        assert '"*.zzz" (read_file) matched no files' in said, said
        assert dispatch.calls == []
        assert not M.started(alone)
        beside = multi({"action": "read_file", "for_each": "*.zzz"}, read("docs/notes.md"))
        said = M.run(beside, dispatch=dispatch)
        assert said.startswith("multi_tool ran 1 call."), said
        assert 'for_each "*.zzz" (read_file) matched no files.' in said, said
        assert [call["path"] for call in dispatch.calls] == ["docs/notes.md"]
    finally:
        box.close()


def test_for_each_must_be_a_pattern_written_as_text():
    for bad in (3, "", "   ", ["*.py"], True):
        dispatch = Recorder()
        said = M.run(multi({"action": "read_file", "for_each": bad}), dispatch=dispatch)
        assert "call 1 of 1" in said and "path pattern written as text" in said, (bad, said)
        assert dispatch.calls == []


def test_files_matching_names_files_only_inside_the_workspace_and_prunes_machinery():
    box = project()
    try:
        found, capped = M.files_matching("**/*.py")
        assert found == ["src/a.py", "src/b.py", "src/deep/c.py"], found
        assert capped is False
        # A pattern that names directories names nothing: a template is a call
        # that takes a file.
        assert M.files_matching("src")[0] == []
        assert M.files_matching("deep")[0] == []
        assert M.files_matching("*.md")[0] == ["docs/notes.md"]
    finally:
        box.close()


# --- placeholders ---------------------------------------------------------------------

def test_the_matched_path_goes_in_path_unless_a_placeholder_says_where():
    call = M.fill({"action": "read_lines", "for_each": "*.py", "start": 1}, "src/app.py")
    assert call == {"action": "read_lines", "start": 1, "path": "src/app.py"}, call
    # A placeholder anywhere claims the path, and `path` is then left alone.
    call = M.fill({"action": "bash", "for_each": "*.py", "command": "python -m py_compile {path}"},
                  "src/app.py")
    assert call == {"action": "bash", "command": "python -m py_compile src/app.py"}, call
    # Even when the template also wrote a `path` of its own.
    call = M.fill({"action": "write_file", "for_each": "*.py", "path": "docs/{stem}.md",
                   "content": "# {name}\n"}, "src/app.py")
    assert call == {"action": "write_file", "path": "docs/app.md", "content": "# app.py\n"}, call


def test_placeholders_reach_into_lists_and_objects():
    """A `git_diff` whose `paths` is a list and a `write_files` whose entries
    are objects both need the file written inside a nested value."""
    call = M.fill({"action": "git_diff", "for_each": "*.py", "paths": ["{path}"]}, "src/a.py")
    assert call == {"action": "git_diff", "paths": ["src/a.py"]}, call
    call = M.fill({"action": "write_files", "for_each": "*.py",
                   "files": [{"path": "out/{stem}.txt", "content": "{name}"}]}, "src/a.py")
    assert call["files"] == [{"path": "out/a.txt", "content": "a.py"}], call
    assert "path" not in call, call


def test_stem_and_name_are_the_last_segment_with_and_without_its_extension():
    call = M.fill({"action": "read_file", "for_each": "*", "path": "{stem}|{name}|{path}"},
                  "a/b/c.tar.gz")
    assert call["path"] == "c.tar|c.tar.gz|a/b/c.tar.gz", call
    call = M.fill({"action": "read_file", "for_each": "*", "path": "{stem}|{name}"}, ".env")
    # A dotfile has no extension to take off.
    assert call["path"] == ".env|.env", call
    call = M.fill({"action": "read_file", "for_each": "*", "path": "{stem}"}, "Makefile")
    assert call["path"] == "Makefile", call


def test_a_plain_call_is_never_substituted():
    """Only a template is filled. A literal `{path}` in an ordinary call --
    inside a Python f-string being written, say -- is the model's text."""
    box = project()
    try:
        dispatch = Recorder()
        M.run(multi({"action": "write_file", "path": "t.py", "content": "f'{path}'"}),
              dispatch=dispatch)
        assert dispatch.calls[0]["content"] == "f'{path}'"
    finally:
        box.close()


# --- the ceiling ---------------------------------------------------------------------

def test_the_ceiling_counts_calls_after_expansion_and_refuses_rather_than_cuts():
    box = project()
    try:
        dispatch = Recorder()
        obj = multi({"action": "read_file", "for_each": "**/*.py"}, limit=2)
        said = M.run(obj, dispatch=dispatch)
        assert said.startswith("multi_tool would run 3 calls and the ceiling is 2."), said
        assert "Nothing ran." in said and str(M.HARD_MAX_CALLS) in said, said
        assert dispatch.calls == []
        assert not M.started(obj)
        # Plain calls count too, and `limit` widens it.
        obj = multi(read("docs/notes.md"), {"action": "read_file", "for_each": "**/*.py"}, limit=4)
        said = M.run(obj, dispatch=dispatch)
        assert said.startswith("multi_tool ran 4 calls."), said
    finally:
        box.close()


def test_the_default_ceiling_and_the_hard_one_are_what_the_prompt_says():
    assert M.MAX_CALLS == 200
    assert M.HARD_MAX_CALLS == 1000
    assert M._cap({"limit": None}) == 200
    assert M._cap({"limit": ""}) == 200
    assert M._cap({}) == 200
    assert M._cap({"limit": 5000}) == 1000
    assert M._cap({"limit": 0}) == 1
    assert M._cap({"limit": "7"}) == 7


def test_a_limit_that_is_not_a_count_is_refused_by_name():
    """`int(True)` is 1, so a bool would refuse every fan-out wider than one
    call for a ceiling nobody chose. It is refused rather than read."""
    for bad in (True, False, "many", [3]):
        dispatch = Recorder()
        said = M.run(multi(read("a"), limit=bad), dispatch=dispatch)
        assert "\"limit\" must be a whole number" in said, (bad, said)
        assert dispatch.calls == [], bad


# --- the result budget ----------------------------------------------------------------

def test_the_budget_is_shared_so_that_what_fits_is_kept_whole():
    """Water-filling: every result that fits keeps its whole text, and the
    ones that do not share what is left evenly."""
    assert M._allocate([10, 20, 30], 100) == [10, 20, 30]
    assert M._allocate([10, 1000, 1000], 100) == [10, 45, 45]
    assert M._allocate([500, 500], 100) == [50, 50]
    assert M._allocate([], 100) == []
    assert M._allocate([5], 0) == [0]


def test_a_result_that_did_not_fit_is_cut_and_the_cut_is_marked_in_its_own_block():
    saved = M.MAX_CHARS
    M.MAX_CHARS = 600
    try:
        dispatch = Recorder(answers={"big": "x" * 2000, "small": "tiny"})
        obj = multi(read("big"), read("small"))
        said = M.run(obj, dispatch=dispatch)
        assert len(said) <= 600, len(said)
        assert "tiny" in said, "the result that fitted was cut"
        assert "more characters not shown" in said, said
        assert "Run this call on its own" in said, said
        # The record keeps the whole thing; only the rendering is cut.
        assert [len(r) for _, r in M.ran(obj)] == [2000, 4]
    finally:
        M.MAX_CHARS = saved


def test_the_default_budget_is_what_the_module_says_it_is():
    assert M.MAX_CHARS == 60000
    text = "y" * 400
    assert M._clip(text, 400) == text
    cut = M._clip(text, 200)
    assert len(cut) <= 200, len(cut)
    assert cut.startswith("yyyy") and cut.endswith("read all of it.]"), cut
    # A share too small to hold the marker yields the marker alone: what is
    # said about the cut is never itself cut.
    tiny = M._clip(text, 10)
    assert tiny.startswith("\n[... 400 more characters"), tiny


# --- what the rest of TMT reads off a multi_tool ----------------------------------------

def test_ran_started_and_mutates_read_the_record_and_never_the_request():
    obj = multi(read("a"), {"action": "write_file", "path": "w", "content": "c"})
    assert M.ran(obj) == ()
    assert not M.started(obj)
    assert M.mutates(obj) is False, "nothing has run, so nothing has mutated"
    M.run(obj, dispatch=Recorder())
    assert M.started(obj)
    assert [call["action"] for call, _ in M.ran(obj)] == ["read_file", "write_file"]
    assert M.mutates(obj) is True
    reads = multi(read("a"), {"action": "grep", "query": "q"})
    M.run(reads, dispatch=Recorder())
    assert M.mutates(reads) is False
    assert "write_file" in agent_config.MUTATING_ACTIONS, "the test above rests on this"


def test_ran_ignores_a_record_the_model_could_have_written():
    """The key is private, but a model could still write it. Anything that is
    not the shape `run` writes is ignored rather than trusted."""
    for planted in ("yes", 3, [("x", "y")], [("not a call", "r")], [{"action": "write_file"}]):
        obj = dict(multi(read("a")), **{M.RAN_KEY: planted})
        assert M.ran(obj) == (), planted
    obj = dict(multi(read("a")), **{M.RAN_KEY: [({"action": "write_file"}, "r"), "junk"]})
    assert [call["action"] for call, _ in M.ran(obj)] == ["write_file"]


def test_subject_and_head_name_the_call_the_way_a_model_can_quote_it_back():
    assert M.subject({"action": "read_file", "path": "src/a.py"}) == "src/a.py"
    assert M.subject({"action": "grep", "query": "needle"}) == "needle"
    assert M.subject({"action": "bash", "command": "make   test"}) == "make test"
    assert M.subject({"action": "list_files"}) == ""
    long = M.subject({"action": "grep", "query": "q" * 300})
    assert len(long) == 120 and long.endswith("..."), long
    assert M.head(2, 5, {"action": "read_file", "path": "x"}) == "[2/5] read_file x"
    assert M.head(1, 1, {"action": "list_files"}) == "[1/1] list_files"
