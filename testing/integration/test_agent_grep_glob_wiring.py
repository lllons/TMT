"""`grep` and `glob` as far as TMT is concerned: registered, dispatched, drawn.

Everything here goes through `agent_actions.execute_action`, never through
`agent_glob.glob` or `agent_grep.grep` directly, for the reason
`test_agent_toolflow.py` states and this change makes sharper than usual: a
tool that works perfectly and is not registered is a tool that does not
exist. Two modules were written from scratch and eight registries had to
learn their names -- the schema, the dispatcher, the labels, the event kinds,
two read-only whitelists, the note and review whitelists, and the frozen
module list. Any one of them left behind gives a tool that passes its own
unit tests and is unreachable by a model.

The other half of this file is the compatibility net. `search_files` and
`find_text` are gone as model-facing verbs, and a reply reaching for either
is translated rather than refused -- with `search_files`'s case-insensitivity
put back on the object, because that half of its meaning lived in a default
and translating the spelling alone would silently change what an old reply
means.
"""

import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

import agent_actions
import agent_capabilities
import agent_config
import agent_delegation
import agent_prompt
import agent_subprompts
import agent_worker
from agent_config import REQUIRED_KEYS

# Derived from a module rather than from __file__: this file lives two
# directories down, and every path below has to name the repository itself.
REPO = Path(agent_config.__file__).resolve().parent

# `path:line: text` -- the shape a model reads a grep result off. Anchored,
# because the whole value of the row is that the two fields before the second
# colon are a place it can go to.
ROW = re.compile(r"^(?P<path>[^\s:]+):(?P<line>\d+): ")

# `7 matches in 3 files`, optionally followed by the truncation sentence. The
# counts are over everything examined, so the first number is the one the
# workflow tests check the rows against.
HEADER = re.compile(r"^(?P<matches>\d+) match(?:es)? in (?P<files>\d+) files?")


def remove_tree(path):
    """Delete a temp tree, including anything left read-only on Windows."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


class Project:
    """A throwaway workspace, with TMT's own state sent somewhere throwaway too.

    The same helper `test_agent_toolflow` uses, and for the same reason: these
    tests must not be able to pass or fail because of what happens to be in
    this repository today.
    """

    def __init__(self, files=None, folders=()):
        self.previous_root = agent_config.ROOT_DIR
        self.previous_install = agent_config.INSTALL_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_search_")).resolve()
        self.install = Path(tempfile.mkdtemp(prefix="tmt_searchinst_")).resolve()
        agent_config.ROOT_DIR = self.path
        agent_config.INSTALL_DIR = self.install
        for name in folders:
            (self.path / name).mkdir(parents=True, exist_ok=True)
        for name, body in (files or {}).items():
            self.write(name, body)

    def write(self, name, body):
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body.encode("utf-8"))
        return target

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        agent_config.INSTALL_DIR = self.previous_install
        remove_tree(self.path)
        remove_tree(self.install)


class Repository:
    """This repository, named explicitly rather than taken from the cwd.

    The two workflow tests are about TMT itself, so they cannot use a
    temporary project -- but they must not depend on which directory the
    runner happened to start in either.
    """

    def __init__(self):
        self.previous_root = agent_config.ROOT_DIR
        agent_config.ROOT_DIR = REPO

    def close(self):
        agent_config.ROOT_DIR = self.previous_root


_DEFAULT_CONTEXT = object()


def run(action, **keys):
    """One action, exactly as the loop would run it.

    The action context is passed as `_context`, not as `context`, because
    `grep` has a key of its own called `context` and a helper that took that
    name would silently eat it -- `run("grep", query="x", context=1)` would
    ask for zero context lines and hand the number 1 to the dispatcher as the
    task's authority. Found the hard way.

    The default carries no `capabilities` key, which authorises nothing -- so
    every dispatch below is also evidence that these two verbs need no
    authorisation.
    """
    context = keys.pop("_context", _DEFAULT_CONTEXT)
    if context is _DEFAULT_CONTEXT:
        context = {"push_authorized": False}
    keys["action"] = action
    return str(agent_actions.execute_action(keys, context))


def rows(result):
    """The `path:line:` rows of a grep result, without its header or notes."""
    return [line for line in result.splitlines() if ROW.match(line)]


def place(row):
    """(path, line number) off one grep row."""
    found = ROW.match(row)
    return found.group("path"), int(found.group("line"))


def paths(result):
    """The path rows of a glob result: no header, and no trailing note.

    A note is parenthesised and is a statement about the WALK rather than a
    path -- the scan ceiling is the one that exists today. Filtering it out
    here keeps every assertion below about rows, so a note appearing would
    never be read as a badly formed path.
    """
    return [line for line in result.splitlines()[1:] if not line.startswith("(")]


PROJECT = {
    "src/calc.py": (
        "def total(items):\n"
        "    return sum(items)\n"
    ),
    "src/report.py": (
        "from src.calc import total\n"
        "\n"
        "\n"
        "def build(items):\n"
        "    return total(items)\n"
    ),
    "src/deep/nested/helper.py": "TOTAL = 1\n",
    "docs/readme.md": "The total is computed here.\n",
    "tests/test_calc.py": (
        "def test_total():\n"
        "    assert True\n"
    ),
}


# --- the schema the model is validated against ------------------------------

def test_both_verbs_are_registered_with_the_keys_they_require():
    """REQUIRED_KEYS is the whole of what `validate_action` knows. A verb
    missing from it is not a tool with a broken schema, it is a verb that
    comes back "Unknown action" however well its module works."""
    assert REQUIRED_KEYS.get("grep") == ["query"], REQUIRED_KEYS.get("grep")
    assert REQUIRED_KEYS.get("glob") == ["pattern"], REQUIRED_KEYS.get("glob")


def test_the_two_old_search_verbs_are_gone_from_the_schema():
    """Not merely unused: absent. A name still in REQUIRED_KEYS would be
    offered in the "Allowed:" list of every validation failure, which is a
    tool list a model reads and reaches for."""
    assert "search_files" not in REQUIRED_KEYS
    assert "find_text" not in REQUIRED_KEYS


def test_validate_action_accepts_the_shapes_the_prompt_teaches():
    """Success is reported as None, not as an empty string -- a test asserting
    == "" fails on a perfectly valid action, which is a trap this repository
    has already walked into once."""
    good = [
        {"action": "glob", "pattern": "*.py"},
        {"action": "glob", "pattern": "testing/**/*.py", "kind": "dirs", "limit": 20},
        {"action": "grep", "query": "end_conversation"},
        {"action": "grep", "query": "def run_file", "glob": "agent_*.py"},
        {"action": "grep", "query": "x", "regex": True, "ignore_case": True,
         "context": 2, "path": "src", "limit": 5},
    ]
    for obj in good:
        assert agent_prompt.validate_action(obj) is None, obj


def test_validate_action_refuses_an_action_missing_its_one_required_key():
    """The key each verb cannot work without, named back so the model can
    correct it in one round rather than guess."""
    missing_query = agent_prompt.validate_action({"action": "grep"})
    assert missing_query and "query" in missing_query, missing_query
    missing_pattern = agent_prompt.validate_action({"action": "glob"})
    assert missing_pattern and "pattern" in missing_pattern, missing_pattern


def test_the_old_names_are_not_reachable_as_model_facing_verbs():
    """Three places had to agree, and each on its own would leave the pair
    half-alive: the validator, the prompt that teaches the tool list, and
    `agent_file_ops`, which is where both functions used to live."""
    import agent_file_ops
    for name in ("search_files", "find_text"):
        assert agent_prompt.validate_action({"action": name, "query": "x"}), name
        assert not hasattr(agent_file_ops, name), name
        assert name not in agent_prompt.ACTION_REFERENCE, name
        assert name not in agent_prompt.TOOL_CHOICE_RULES, name
        assert name not in agent_prompt.PREFERENCE_RULES, name


def test_the_prompt_the_model_actually_receives_teaches_the_new_pair_only():
    """The constants above are what the sections say; this is what is
    assembled and sent. Built over a temporary workspace so the snapshot in it
    is small and says nothing about this repository."""
    box = Project(files=PROJECT)
    try:
        agent_prompt.invalidate_prompt()
        whole = agent_prompt.get_system_prompt()
        assert "search_files" not in whole
        assert "find_text" not in whole
        assert '{"action":"glob","pattern":"*.py"}' in whole, "glob is not taught"
        assert '{"action":"grep","query":"end_conversation"}' in whole, "grep is not taught"
    finally:
        box.close()
        agent_prompt.invalidate_prompt()


# --- dispatch, over a real temporary workspace ------------------------------

def test_glob_reaches_its_module_through_the_dispatcher():
    """The registration test: `_run_tool` answers a module it cannot import
    with a sentence rather than an exception, so an unregistered tool fails
    quietly and plausibly. That is the failure this catches."""
    box = Project(files=PROJECT)
    try:
        result = run("glob", pattern="*.py")
        assert "Unknown action" not in result, result
        assert "is unavailable" not in result, result
        assert result.startswith("4 matches for `*.py`:"), result
        for name in ("src/calc.py", "src/report.py", "tests/test_calc.py",
                     "src/deep/nested/helper.py"):
            assert name in result, (name, result)
    finally:
        box.close()


def test_glob_can_be_asked_for_directories_and_says_which_rows_are_ones():
    """A directory row ends with `/`. That is what makes the two kinds read
    apart with the colour stripped out, which is the only way they are ever
    read in a transcript."""
    box = Project(files=PROJECT)
    try:
        dirs = run("glob", pattern="*", kind="dirs")
        assert "src/" in dirs and "tests/" in dirs, dirs
        for row in paths(dirs):
            assert row.endswith("/"), (row, dirs)
        assert "src/calc.py" not in dirs, dirs

        both = run("glob", pattern="src/*", kind="any")
        assert "src/deep/" in both, both
        assert "src/calc.py" in both, both
    finally:
        box.close()


def test_glob_refuses_a_kind_it_does_not_know_by_naming_it_back():
    box = Project(files=PROJECT)
    try:
        result = run("glob", pattern="*.py", kind="sideways")
        assert "kind must be files, dirs or any" in result, result
        assert "sideways" in result, result
    finally:
        box.close()


def test_a_truncated_glob_header_states_the_real_total():
    """The number in the header is what a model decides its next action on. A
    header reporting the SHOWN figure would understate what is still out there
    every single time it capped."""
    box = Project(files=PROJECT)
    try:
        result = run("glob", pattern="*.py", limit=2)
        head = result.splitlines()[0]
        assert head.startswith("4 matches"), head
        assert "showing the first 2" in head, head
        assert len(result.splitlines()) == 3, result
    finally:
        box.close()


def test_grep_reaches_its_module_and_reports_path_line_and_text():
    """`path:line: text` is the whole contract with the model: somewhere to
    go, and enough of the line to know it is the right one."""
    box = Project(files=PROJECT)
    try:
        result = run("grep", query="total")
        assert "Unknown action" not in result, result
        assert "is unavailable" not in result, result
        assert result.startswith("5 matches in 4 files"), result
        found = dict(place(row) for row in rows(result))
        assert found["src/calc.py"] == 1, result
        assert "docs/readme.md" in found, result
        assert "src/deep/nested/helper.py" not in found, "case-sensitive by default"
    finally:
        box.close()


def test_grep_narrows_to_a_glob_through_the_dispatcher():
    """The key that makes the pair a workflow rather than two tools."""
    box = Project(files=PROJECT)
    try:
        result = run("grep", query="total", glob="src/*.py")
        paths = {place(row)[0] for row in rows(result)}
        assert paths == {"src/calc.py", "src/report.py"}, result
        assert "docs/readme.md" not in result, result
    finally:
        box.close()


def test_grep_is_case_sensitive_until_the_model_says_otherwise():
    """Both halves of what the two old tools did, now one key apart. The
    default is the exact one, because `Path` is not `path` and a model that
    cannot tell them apart edits on a guess."""
    box = Project(files=PROJECT)
    try:
        exact = run("grep", query="TOTAL")
        assert exact.startswith("1 match in 1 file"), exact
        assert "src/deep/nested/helper.py" in exact, exact
        assert "src/calc.py" not in exact, exact

        loose = run("grep", query="TOTAL", ignore_case=True)
        paths = {place(row)[0] for row in rows(loose)}
        assert "src/calc.py" in paths and "src/deep/nested/helper.py" in paths, loose
    finally:
        box.close()


def test_grep_with_context_comes_back_as_a_block_with_readable_markers():
    """`>` marks the match and `|` marks context, so the two read apart with
    ANSI stripped -- colour is never the message here or anywhere else."""
    box = Project(files=PROJECT)
    try:
        result = run("grep", query="return total", context=1)
        assert "src/report.py:5:" in result, result
        assert re.search(r"^\s*5 > ", result, re.M), result
        assert re.search(r"^\s*4 \| ", result, re.M), result
        # A block, not a file: the two lines above the match are not in it.
        assert "from src.calc import total" not in result, result
    finally:
        box.close()


def test_grep_reports_one_row_per_line_a_match_starts_on():
    """A result is a list of places to go and read, and the same place named
    three times is not three places. It is also what keeps the header's count
    and the number of rows the same number."""
    box = Project(files=PROJECT)
    try:
        box.write("src/thrice.py", "x = 'total total total'\n")
        result = run("grep", query="total", glob="src/thrice.py")
        assert result.startswith("1 match in 1 file"), result
        assert len(rows(result)) == 1, result
    finally:
        box.close()


def test_a_path_carrying_a_glob_is_read_as_the_filter_it_obviously_is():
    """The model will write path="*.md", and refusing the shorthand costs a
    whole round to say so. The promotion is visible in the no-match note: it
    says paths MATCHING, not paths UNDER, because `path` became None."""
    box = Project(files=PROJECT)
    try:
        result = run("grep", query="nosuchthing", path="*.md")
        assert "Only paths matching *.md were examined." in result, result
        assert "Only paths under" not in result, result

        hit = run("grep", query="total", path="*.md")
        assert {place(row)[0] for row in rows(hit)} == {"docs/readme.md"}, hit
    finally:
        box.close()


def test_a_bracket_in_a_path_is_a_directory_name_and_not_a_pattern():
    """`agent_file_ops` escapes `[`, so a bracket is an ordinary character to
    the matcher. Reading it as a metacharacter would send a real subtree named
    `[draft]` down the filter path, where it would be compared against whole
    relative paths and match nothing -- a directory turned into an empty
    result by a guess."""
    box = Project(files={"[draft]/note.md": "keepme here\n",
                         "other.md": "keepme too\n"})
    try:
        result = run("grep", query="keepme", path="[draft]")
        assert result.startswith("1 match in 1 file"), result
        assert "[draft]/note.md" in result, result
        assert "other.md" not in result, result
    finally:
        box.close()


def test_both_verbs_refuse_a_path_outside_the_workspace_in_words():
    """`_run_tool` turns `safe_path`'s ValueError into a sentence. The model
    is the one party here that cannot be trusted with a path, and it also has
    to be able to read the refusal and correct itself -- a traceback out of
    the dispatcher does neither."""
    box = Project(files=PROJECT)
    try:
        outside = [
            ("glob", {"pattern": "*.py", "path": "../.."}),
            ("glob", {"pattern": "*", "path": "/etc"}),
            ("grep", {"query": "total", "path": "../.."}),
            ("grep", {"query": "total", "path": "/etc"}),
        ]
        for action, keys in outside:
            result = run(action, **keys)
            assert "Refused" in result, (action, result)
            assert "Traceback" not in result, (action, result)
    finally:
        box.close()


def test_a_glob_result_never_names_anything_outside_the_workspace():
    """Every row is workspace-relative. Nothing absolute, ever -- an absolute
    path in a result is a path the model will hand straight back to a write."""
    box = Project(files=PROJECT)
    try:
        for pattern in ("*.py", "**/*", "../../*"):
            result = run("glob", pattern=pattern, kind="any")
            for row in paths(result):
                assert not row.startswith("/"), (pattern, row)
                assert ":" not in row[:3], (pattern, row)
                assert ".." not in row, (pattern, row)
    finally:
        box.close()


def test_neither_verb_needs_a_capability_to_run():
    """`/plan`, `/review` and `/verify` are authorised per prompt by the user.
    These two are ordinary read verbs and must never join that set: a context
    carrying no `capabilities` key authorises nothing, and both still run."""
    assert "grep" not in agent_capabilities.CAPABILITIES
    assert "glob" not in agent_capabilities.CAPABILITIES
    box = Project(files=PROJECT)
    try:
        for context in ({}, None, {"push_authorized": False}):
            found = run("glob", _context=context, pattern="*.py")
            searched = run("grep", _context=context, query="total")
            for result in (found, searched):
                assert "not authorised" not in result.lower(), result
                assert "CONSTRAINT VIOLATION" not in result, result
            assert found.startswith("4 matches"), found
            assert searched.startswith("5 matches"), searched
    finally:
        box.close()


# --- the registries the interface reads -------------------------------------

def test_the_labels_the_interface_draws_name_the_two_verbs():
    """Derived labels, so `Grep` and `Glob` rather than the raw verb. A
    missing entry draws the bare snake_case name in a transcript where every
    neighbouring row is title-cased."""
    labels = agent_actions.ACTION_LABELS
    assert labels.get("grep") == "Grep", labels.get("grep")
    assert labels.get("glob") == "Glob", labels.get("glob")
    assert "search_files" not in labels
    assert "find_text" not in labels


def test_both_verbs_are_recorded_as_reads_rather_than_as_generic_tools():
    """`file_read` is the kind, so a search is drawn as what it is. The map is
    asserted directly and through the event it produces, because the map being
    right and the lookup missing it would look identical from outside."""
    kinds = agent_actions._EVENT_KIND_FOR_ACTION
    assert kinds.get("grep") == "file_read", kinds.get("grep")
    assert kinds.get("glob") == "file_read", kinds.get("glob")
    assert "search_files" not in kinds
    assert "find_text" not in kinds
    for action, keys in (("grep", {"query": "total"}),
                         ("glob", {"pattern": "*.py"})):
        obj = dict(keys, action=action)
        event = agent_actions.action_event(action, obj, "1 match in 1 file")
        assert event.kind == "file_read", (action, event.kind)


def test_a_glob_row_names_its_pattern_rather_than_reading_as_the_bare_verb():
    """`pattern` had to be added to two target lists -- the transcript's and
    the worker card's. Without it a glob row is the word "Glob", which says a
    search happened and not what was looked for, and the fortieth glob of a
    session is the same row as the first."""
    obj = {"action": "glob", "pattern": "agent_*.py"}
    event = agent_actions.action_event("glob", obj, "38 matches for `agent_*.py`:")
    assert "agent_*.py" in event.message, event.message
    assert event.message.startswith("Glob"), event.message
    assert agent_worker._activity_label("glob", obj) == "Glob agent_*.py"
    assert agent_worker._activity_label(
        "grep", {"action": "grep", "query": "end_conversation"}) == "Grep end_conversation"


def test_both_read_only_whitelists_carry_the_new_names_and_neither_the_old():
    """Two lists, and the second is a SECURITY whitelist: a verb absent from
    `agent_delegation.READ_ONLY_ACTIONS` is refused to a read-only worker, so
    a pair of names left behind there is a read-only delegation that cannot
    search at all. They are deliberately separate lists -- one answers "does
    this action end a turn's read run", the other "may a constrained worker
    do this" -- so both are asserted."""
    for whitelist in (agent_actions.READ_ONLY_ACTIONS,
                      agent_delegation.READ_ONLY_ACTIONS):
        assert "grep" in whitelist, whitelist
        assert "glob" in whitelist, whitelist
        assert "search_files" not in whitelist, whitelist
        assert "find_text" not in whitelist, whitelist


def test_the_note_and_review_whitelists_agree_with_the_prompts_that_teach_them():
    """Two copies of one list, in two modules, and the failure of them
    disagreeing is silent in the worst direction: the prompt offers a verb the
    loop refuses, and the agent spends a step being told a name is wrong."""
    assert set(agent_worker.NOTE_ACTIONS) == set(agent_subprompts.NOTE_VERBS)
    assert set(agent_worker.REVIEW_ACTIONS) == set(agent_subprompts.REVIEW_VERBS)
    for names in (agent_worker.NOTE_ACTIONS, agent_worker.REVIEW_ACTIONS,
                  agent_subprompts.NOTE_VERBS, agent_subprompts.REVIEW_VERBS):
        assert "grep" in names, names
        assert "glob" in names, names
        assert "search_files" not in names, names
        assert "find_text" not in names, names


def test_the_two_new_modules_are_in_the_frozen_module_list():
    """An editable install writes `py-modules` at install time, so a module
    that is not in it is invisible to `tmtcode` however well it works in a
    test that puts the repository on sys.path. `_run_tool` would answer
    "agent_grep is unavailable" and the model would work around a tool that is
    sitting on disk."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"agent_glob"' in text, "agent_glob is not in pyproject.toml"
    assert '"agent_grep"' in text, "agent_grep is not in pyproject.toml"
    assert (REPO / "agent_glob.py").is_file()
    assert (REPO / "agent_grep.py").is_file()


# --- the compatibility net --------------------------------------------------

def test_search_files_translates_to_grep_and_brings_its_looseness_with_it():
    """The whole reason this net is more than a spelling change.
    `search_files` matched case-INSENSITIVELY and `grep` does not, so
    translating the name alone would silently change what an old reply MEANS:
    a model that wrote "todo" expecting to find "TODO" would get a different
    answer under a verb it never chose."""
    obj = {"action": "search_files", "query": "todo"}
    adopted = agent_actions.adopt_verb(obj)
    assert adopted["action"] == "grep", adopted
    assert adopted["ignore_case"] is True, adopted
    assert agent_prompt.validate_action(adopted) is None, adopted


def test_find_text_translates_to_grep_with_no_flag_added():
    """`find_text` was already exact and case-sensitive, which is what `grep`
    is, so it is translated as it stands. A flag added here would be the same
    silent change of meaning in the other direction."""
    obj = {"action": "find_text", "query": "todo", "context": 2}
    adopted = agent_actions.adopt_verb(obj)
    assert adopted["action"] == "grep", adopted
    assert "ignore_case" not in adopted, adopted
    assert adopted["context"] == 2, adopted
    assert agent_prompt.validate_action(adopted) is None, adopted


def test_a_model_that_wrote_ignore_case_itself_is_not_overruled():
    """The fill-in exists for a default that had nowhere to go, not for a
    model that made a choice. Both directions, because the false one is the
    one a careless `setdefault`-shaped fix would trample."""
    off = agent_actions.adopt_verb(
        {"action": "search_files", "query": "todo", "ignore_case": False})
    assert off["action"] == "grep" and off["ignore_case"] is False, off
    on = agent_actions.adopt_verb(
        {"action": "search_files", "query": "todo", "ignore_case": True})
    assert on["action"] == "grep" and on["ignore_case"] is True, on


def test_canonical_action_answers_the_verb_and_adopt_verb_carries_the_meaning():
    """The division the net rests on: `canonical_action` decides WHAT a reply
    means and `adopt_verb` makes the object say it. Asserted apart because the
    dispatcher only ever asks the first -- so a test that drove the
    translation through `execute_action` would be watching the half that does
    not put the flag back."""
    assert agent_actions.canonical_action({"action": "search_files"}) == "grep"
    assert agent_actions.canonical_action({"action": "find_text"}) == "grep"
    assert agent_actions.canonical_action({"action": "grep"}) == "grep"
    assert agent_actions.canonical_action({"action": "glob"}) == "glob"
    # The dispatcher translates the spelling and nothing more, so an old
    # object handed straight to it runs case-SENSITIVELY. That is not a
    # defect, it is why the four real dispatch paths call `adopt_verb` first.
    box = Project(files=PROJECT)
    try:
        spelled = run("search_files", query="TOTAL")
        assert spelled.startswith("1 match in 1 file"), spelled
        adopted = agent_actions.adopt_verb({"action": "search_files", "query": "TOTAL"})
        loose = run(adopted.pop("action"), **adopted)
        assert loose.startswith("6 matches"), loose
    finally:
        box.close()


def test_every_dispatch_path_adopts_the_verb_before_it_validates_it():
    """Load-bearing rather than incidental. `validate_action` no longer knows
    the old names, so a path that validated first would refuse
    `search_files` as an unknown action and the net would never be reached --
    and it would fail exactly for the replies the net exists to catch. Four
    paths: TMT's single action and batch, the worker's single action and
    batch."""
    import inspect
    for function in (agent_worker._run_loop, agent_worker._run_batch):
        source = inspect.getsource(function)
        adopted = source.index("_adopt_verb(")
        validated = source.index("_validate(")
        assert adopted < validated, function.__name__
    loop = (REPO / "TMT.py").read_text(encoding="utf-8")
    for adopt, validate in (("_adopt_verb(obj)", "validate_action(obj)"),
                            ("_adopt_verb(sub_obj)", "validate_action(sub_obj)")):
        assert loop.index(adopt) < loop.index(validate), adopt


def test_the_old_names_are_translated_and_never_taught():
    """A net nobody is told about. No prompt names either verb, no tool list
    offers them, and no refusal suggests them -- it exists only so a habit
    does not cost an answer."""
    assert agent_actions._LEGACY_ACTIONS.get("search_files") == "grep"
    assert agent_actions._LEGACY_ACTIONS.get("find_text") == "grep"
    for text in (agent_prompt.ACTION_REFERENCE,
                 agent_subprompts.worker_prompt(),
                 agent_subprompts.note_prompt(),
                 agent_subprompts.review_prompt()):
        assert "search_files" not in text
        assert "find_text" not in text


# --- background agents ------------------------------------------------------

def test_a_read_only_delegation_may_grep_and_glob_and_may_not_write():
    """The security whitelist asked the way the worker asks it. A read-only
    worker whose only two search verbs were refused could not do the one thing
    a read-only worker is for."""
    contract = agent_delegation.DelegationConstraints(read_only=True)
    assert agent_delegation.refusal(contract, "grep") == ""
    assert agent_delegation.refusal(contract, "glob") == ""
    assert agent_delegation.refusal(contract, "write_file") != ""
    # The old names are refused, which is the whitelist working rather than a
    # gap: they are not verbs any more, and the net translates them before
    # anything asks this question.
    assert agent_delegation.refusal(contract, "search_files") != ""
    assert agent_delegation.refusal(contract, "find_text") != ""


def test_a_read_only_worker_reaches_both_verbs_through_the_dispatcher():
    """The second of the two layers. `agent_worker` refuses a mutating verb
    before dispatch; this is the dispatcher's own copy of the same rule,
    asked with the same function, and it must let these two through."""
    box = Project(files=PROJECT)
    try:
        context = {"push_authorized": False, "read_only": True}
        found = run("glob", _context=context, pattern="*.py")
        assert found.startswith("4 matches"), found
        searched = run("grep", _context=context, query="total")
        assert searched.startswith("5 matches"), searched
        blocked = run("write_file", _context=context, path="new.py", content="x")
        assert agent_delegation.VIOLATION_HEADER in blocked, blocked
        assert not (box.path / "new.py").exists(), "a read-only worker wrote a file"
    finally:
        box.close()


# --- the two workflows the change exists for --------------------------------

def test_the_workflow_glob_then_grep_then_read_lines_lands_on_the_line():
    """The point of the pair, over this repository, as a model would drive it:
    find the candidate files by name, find the lines inside them, then read
    the one region. Asserted on shapes and relationships rather than on counts
    -- the numbers change whenever anybody edits a file, and what is being
    pinned is that the result of each step is precise enough to be the input
    of the next."""
    repo = Repository()
    try:
        found = run("glob", pattern="agent_*.py")
        names = paths(found)
        assert "agent_actions.py" in names, found
        assert "agent_grep.py" in names and "agent_glob.py" in names, found
        # Files only, and nothing outside the repository root.
        assert all(name.endswith(".py") for name in names), found
        assert all(name.startswith("agent_") for name in names), found

        hits = run("grep", query="end_conversation", glob="agent_*.py")
        located = rows(hits)
        assert located, hits
        assert HEADER.match(hits.splitlines()[0]), hits.splitlines()[0]
        # Every place named is one of the files the glob just returned, so the
        # second step really was narrowed by the first.
        for row in located:
            path, line = place(row)
            assert path in names, (row, "not in the globbed set")
            assert line > 0, row

        path, line = place(located[0])
        read = run("read_lines", path=path, start=line, end=line)
        # The line number the grep reported is the line the file has, which is
        # the whole of "precise enough to continue": the model reads three
        # lines rather than a 2000-line module.
        assert "end_conversation" in read, (path, line, read)
        assert read.count("\n") <= 2, read
    finally:
        repo.close()


def test_the_workflow_over_the_test_tree_narrows_from_files_to_lines():
    """`**` recursion into a subtree, then a search restricted to exactly that
    subtree. The assertion that matters is the negative one: nothing outside
    `testing/` comes back from either step."""
    repo = Repository()
    try:
        found = run("glob", pattern="testing/**/*.py")
        names = paths(found)
        assert names, found
        assert all(name.startswith("testing/") for name in names), found
        assert all(name.endswith(".py") for name in names), found
        assert "testing/integration/test_agent_grep_glob_wiring.py" in names, found
        # A depth the pattern has to recurse to reach, and a sibling tree it
        # must not: the modules themselves are not under testing/.
        assert "agent_actions.py" not in names, found

        hits = run("grep", query="def test_", glob="testing/**/*.py", limit=25)
        located = rows(hits)
        assert located, hits
        for row in located:
            path, line = place(row)
            assert path in names, (row, "outside the globbed subtree")
            assert line > 0, row
        assert len(located) <= 25, hits
        # A grep result is places, never contents. The header counts over
        # everything examined rather than over what fitted, so the total in it
        # is at least the number of rows and says so when it is more.
        head = hits.splitlines()[0]
        counted = HEADER.match(head)
        assert counted, head
        assert int(counted.group("matches")) >= len(located), head
        if int(counted.group("matches")) > len(located):
            assert "showing the first 25" in head, head
    finally:
        repo.close()
