"""Tests for the persistent project context: the two files, and what protects them.

TMT_Context/notes.md and TMT_Context/progress.md are the first state TMT keeps
inside the user's own project rather than beside its installation, and they are
the first state a user is invited to edit by hand. Both of those make the
interesting tests here defensive rather than functional: the feature working is
one test, and everything else is about what it must never do to a file somebody
else wrote.

Three properties carry most of the file:

  Round-tripping is exact.  `Document(text).render()` gives back what it was
      handed, so a read never rewrites and an update differs in one section.
      Without it every write produces a diff nobody can review, and the "never
      blindly overwrite" rule is a wish rather than a mechanism.

  A workspace is never shared.  The context path is derived from
      agent_config.ROOT_DIR on every call, so Project A's notes are
      unreachable from Project B by construction. The test moves the root
      between two directories and checks both directions.

  Nothing raises.  A read-only directory, a file replaced by a directory, a
      workspace that does not exist: each one must come back as a Result, not
      as an exception, because the caller is a session loop in the middle of
      somebody's task.
"""

import os
import shutil
import stat
import tempfile
from pathlib import Path

import agent_config
import agent_context

INSTALL_DIR = Path(agent_config.__file__).resolve().parent


def remove_tree(path):
    """Delete a temp tree, including anything made read-only by a test."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


class Project:
    """A throwaway directory as the workspace, restored in close().

    Restores agent_config.ROOT_DIR, which must happen in a finally block: a
    leaked root points every later test at a deleted directory. The setting is
    saved and restored too, because several tests here turn the feature off and
    a leaked OFF would silently disable the rest of the file.
    """

    def __init__(self, files=None):
        self.previous_root = agent_config.ROOT_DIR
        self.previous_setting = agent_config.PROJECT_CONTEXT
        self.path = Path(tempfile.mkdtemp(prefix="tmt_ctx_")).resolve()
        for name, body in (files or {}).items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")

    def use(self):
        agent_config.PROJECT_CONTEXT = True
        return agent_config.set_workspace_root(self.path)

    def read(self, name):
        return (self.path / agent_context.CONTEXT_DIR_NAME
                / name).read_text(encoding="utf-8")

    def write(self, name, body):
        directory = self.path / agent_context.CONTEXT_DIR_NAME
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(body, encoding="utf-8")

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        agent_config.PROJECT_CONTEXT = self.previous_setting
        try:
            import agent_prompt
            agent_prompt.invalidate_prompt()
        except Exception:
            pass
        remove_tree(self.path)


# --- the document model -----------------------------------------------------
#
# Everything that protects a user's file is in `Document`, so it is tested on
# its own before anything touches a disk.

def test_a_document_round_trips_exactly():
    """A file that is only READ must never be rewritten. If render() does not
    reproduce its input, every update produces a diff full of changes nobody
    made and the real change is impossible to find."""
    text = ("# TMT Project Notes\n\n<!-- TMT_Context format version: 1 -->\n\n"
            "## Architecture\n\nThe entry point is `src/cli.py`.\n\n"
            "## My Own Section\n\nSomething the user wrote.\n")
    assert agent_context.Document(text).render() == text


def test_an_update_changes_one_section_and_leaves_every_other_byte():
    """The whole of "never blindly overwrite". A section TMT has never heard
    of, written by the user, has to come back identical."""
    text = ("# TMT Project Notes\n\n"
            "## Architecture\n\nOld fact.\n\n"
            "## Deployment Runbook\n\nStep one: ssh to the box.\nStep two: pull.\n\n"
            "## Known Issues\n\nNone recorded yet.\n")
    document = agent_context.Document(text)
    document.set("Architecture", "New fact.")
    rendered = document.render()
    assert "New fact." in rendered
    assert "Old fact." not in rendered
    # The user's section, verbatim, including its own line break.
    assert "Step one: ssh to the box.\nStep two: pull." in rendered
    assert "## Deployment Runbook" in rendered
    assert "## Known Issues" in rendered
    # And its position: it must not have been moved to the end.
    assert rendered.index("## Deployment Runbook") < rendered.index("## Known Issues")


def test_an_unknown_heading_is_never_dropped_or_renamed():
    """A real project's notes will hold headings TMT does not know. Losing one
    while appending a line to another would destroy exactly the thing this
    feature exists to keep."""
    document = agent_context.Document(
        "## Weird Heading\n\nbody\n\n## another one\n\nmore\n")
    document.append("Architecture", "added")
    names = document.names()
    assert "Weird Heading" in names, names
    assert "another one" in names, names
    assert "Architecture" in names, names


def test_a_heading_is_matched_case_insensitively_rather_than_duplicated():
    """A user who typed "## important files" meant the section TMT calls
    "Important Files". Creating a second heading beside theirs is the file
    duplicating itself one edit at a time."""
    document = agent_context.Document("## important files\n\n- `a.py`\n")
    document.append("Important Files", "- `b.py`")
    assert len(document.sections) == 1, document.names()
    assert "- `a.py`" in document.render()
    assert "- `b.py`" in document.render()


def test_a_placeholder_is_replaced_by_the_first_real_fact_not_appended_to():
    """A section still saying "Not yet recorded." is saying nothing. Appending
    under it leaves every file carrying a line that contradicts the lines
    below it."""
    document = agent_context.Document(
        "## Build\n\n%s\n" % agent_context.NOTES_PLACEHOLDER)
    document.append("Build", "`make all`")
    assert agent_context.NOTES_PLACEHOLDER not in document.render()
    assert "`make all`" in document.render()


def test_the_same_list_line_is_never_recorded_twice():
    """This is what stops progress.md becoming a chat log: two turns that both
    finish the same piece of work record it once."""
    document = agent_context.Document("## Completed\n\n")
    document.add_line("Completed", "- [x] Added OAuth")
    document.add_line("Completed", "- [x] Added OAuth")
    assert document.section("Completed").count("Added OAuth") == 1


def test_a_long_list_folds_the_oldest_away_and_says_how_many():
    """Section 36. A hundred completed items answers none of the three
    questions progress.md exists to answer -- but dropping them silently would
    be the file pretending to be complete."""
    document = agent_context.Document("## Completed\n\n")
    for number in range(agent_context.MAX_LIST_ENTRIES + 5):
        document.add_line("Completed", "- [x] Task %d" % number)
    body = document.section("Completed")
    assert "folded away" in body, body
    assert "Task 0" not in body, body
    assert "Task %d" % (agent_context.MAX_LIST_ENTRIES + 4) in body, body


def test_a_sub_heading_stays_inside_its_section():
    """The plan mirror is a `###` inside "Currently Working On". If it started
    a section of its own the mirror could not be replaced without touching
    whatever else that section held."""
    document = agent_context.Document(
        "## Currently Working On\n\nSomething.\n\n### Plan Progress\n\n- [x] One\n")
    assert document.names() == ["Currently Working On"], document.names()
    assert "### Plan Progress" in document.section("Currently Working On")


def test_a_document_reads_its_own_format_version():
    """Section 25. The marker is what a later migration would read, so it has
    to survive a round trip and be findable."""
    text = agent_context.initial_notes(tempfile.gettempdir())
    assert agent_context.Document(text).version() == agent_context.FORMAT_VERSION


def test_crlf_in_a_section_does_not_rewrite_every_line_of_the_file():
    """These files are hand-edited on Windows as often as they are written
    here. A section read as CRLF and written back as LF rewrites the whole
    file for a one-line change."""
    document = agent_context.Document("## Build\r\n\r\n`make`\r\n")
    assert "\r" not in document.render()
    assert "`make`" in document.render()


# --- creating it ------------------------------------------------------------

def test_the_first_task_creates_the_directory_and_both_files():
    """Section 40A. The whole feature, in the case where there is nothing."""
    box = Project(files={"app.py": "print(1)\n"})
    try:
        box.use()
        context = agent_context.ProjectContext()
        assert not context.available
        result = context.ensure("add dark mode")
        assert result.ok, result
        assert result.status == agent_context.CREATED, result.status
        assert (box.path / "TMT_Context").is_dir()
        assert (box.path / "TMT_Context" / "notes.md").is_file()
        assert (box.path / "TMT_Context" / "progress.md").is_file()
        assert context.available
    finally:
        box.close()


def test_it_is_created_in_the_project_and_nowhere_else():
    """Section 4. In the workspace root, not in TMT's installation and not
    loose in the user's home directory.

    The home check is `!=` and not `not in parents`, deliberately: on Windows
    a temp directory IS under the user's home (AppData/Local/Temp), so the
    stronger-looking assertion is one this test's own fixture cannot satisfy
    and would fail for a reason that has nothing to do with the feature. What
    section 4 actually forbids is the context being placed in the home
    directory instead of the project, and that is what is asserted.
    """
    box = Project()
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("do something")
        assert context.directory.parent == box.path, context.directory
        assert INSTALL_DIR not in context.directory.parents, context.directory
        assert context.directory != Path.home() / "TMT_Context", context.directory
        assert context.directory.parent != Path(tempfile.gettempdir()), context.directory
    finally:
        box.close()


def test_an_existing_directory_is_not_recreated_and_loses_nothing():
    """Section 40B. A new conversation must not be able to destroy what an
    older one recorded."""
    box = Project()
    try:
        box.use()
        box.write("notes.md", "# TMT Project Notes\n\n## Architecture\n\nMine.\n")
        box.write("progress.md", "# TMT Project Progress\n\n## Completed\n\n- [x] A thing\n")
        result = agent_context.ProjectContext().ensure("a new conversation")
        assert result.status == agent_context.EXISTING, result.status
        assert "Mine." in box.read("notes.md")
        assert "- [x] A thing" in box.read("progress.md")
    finally:
        box.close()


def test_one_missing_file_is_created_and_the_other_is_left_alone():
    """Section 40C. The guard is per FILE, not per directory: a user who
    deleted progress.md gets it back without notes.md being touched."""
    box = Project()
    try:
        box.use()
        box.write("notes.md", "# TMT Project Notes\n\n## Architecture\n\nOnly mine.\n")
        result = agent_context.ProjectContext().ensure("carry on")
        assert result.status == agent_context.CREATED, result.status
        assert box.read("notes.md") == ("# TMT Project Notes\n\n"
                                        "## Architecture\n\nOnly mine.\n")
        assert "TMT Project Progress" in box.read("progress.md")
    finally:
        box.close()


def test_a_user_written_file_survives_a_whole_session_of_updates():
    """Section 40D, and the one a user will actually notice. Their prose has
    to be there afterwards, unchanged, with TMT's addition beside it."""
    box = Project()
    try:
        box.use()
        mine = ("# TMT Project Notes\n\n"
                "## Architecture\n\n"
                "DO NOT TOUCH: the scheduler must start before the API.\n\n"
                "## My Deployment Notes\n\n"
                "Deploy with `./ship.sh`. Ask Priya first.\n")
        box.write("notes.md", mine)
        context = agent_context.ProjectContext()
        context.ensure("a task")
        context.update_notes("Architecture", "Routes live in `src/routes/`.")
        context.update_notes("Testing", "`pytest -q`.")
        context.update_progress("Completed", "- [x] Something", mode="line")
        after = box.read("notes.md")
        assert "DO NOT TOUCH: the scheduler must start before the API." in after
        assert "Deploy with `./ship.sh`. Ask Priya first." in after
        assert "## My Deployment Notes" in after
        assert "Routes live in `src/routes/`." in after
    finally:
        box.close()


def test_a_second_session_reads_what_the_first_one_wrote():
    """Section 40E, and the reason the feature exists. Two separate
    ProjectContext objects, one after the other, with nothing carried between
    them but the disk."""
    box = Project()
    try:
        box.use()
        first = agent_context.ProjectContext()
        first.ensure("session one")
        first.update_notes("Architecture",
                           "Commands are registered in `src/commands/reg.py`.")
        del first
        # A brand-new object, exactly as the next launch would build.
        second = agent_context.ProjectContext()
        assert second.available
        assert ("Commands are registered in `src/commands/reg.py`."
                in second.notes().section("Architecture"))
        assert "src/commands/reg.py" in second.for_prompt()
    finally:
        box.close()


def test_two_projects_never_see_each_others_context():
    """Section 40F and section 26. The path is derived from the workspace on
    every call, so this is a property of the code rather than of remembering
    to reset something."""
    first = Project()
    second = Project()
    try:
        first.use()
        one = agent_context.ProjectContext()
        one.ensure("project one")
        one.update_notes("Architecture", "PROJECT-ONE-SECRET-SHAPE")

        second.use()
        two = agent_context.ProjectContext()
        two.ensure("project two")
        two.update_notes("Architecture", "PROJECT-TWO-SECRET-SHAPE")

        assert "PROJECT-ONE-SECRET-SHAPE" not in two.for_prompt()
        assert "PROJECT-TWO-SECRET-SHAPE" in two.for_prompt()

        # And back the other way, with the SAME object that wrote project two:
        # it follows the workspace rather than remembering a root.
        first.use()
        assert "PROJECT-TWO-SECRET-SHAPE" not in two.for_prompt()
        assert "PROJECT-ONE-SECRET-SHAPE" in two.for_prompt()
    finally:
        second.close()
        first.close()


def test_ensure_is_idempotent_and_says_so_the_second_time():
    """It runs on every turn. The first call creates and says so; every later
    one has to be quiet, or the user is told about a directory once per
    question."""
    box = Project()
    try:
        box.use()
        context = agent_context.ProjectContext()
        assert context.ensure("one").status == agent_context.CREATED
        assert context.ensure("two").status == agent_context.EXISTING
        assert context.ensure("three").status == agent_context.EXISTING
    finally:
        box.close()


# --- what goes in a new file ------------------------------------------------

def test_the_generated_notes_state_what_is_unknown_rather_than_guessing():
    """Section 7. A project with no build script must not be given one."""
    box = Project(files={"a.py": "x = 1\n"})
    try:
        box.use()
        text = agent_context.initial_notes(box.path)
        assert "has not yet been confirmed" in text, text
        # And nothing was invented about a build system that is not there.
        assert "npm run build" not in text
        assert "make all" not in text
    finally:
        box.close()


def test_the_generated_notes_carry_facts_the_repository_actually_states():
    """The other half of section 7: real information, from real files. This
    project declares a test command and a console script, and both must
    appear, with the reason the test command was chosen."""
    box = Project(files={
        "pyproject.toml": ("[project]\nname = \"thing\"\n\n"
                           "[project.scripts]\nthing = \"thing.cli:main\"\n"),
        "run_tests.py": "print('tests')\n",
        "README.md": "# Thing\n\nThing manages widgets for the warehouse team.\n",
        "src/cli.py": "def main():\n    pass\n",
    })
    try:
        box.use()
        text = agent_context.initial_notes(box.path)
        assert "python run_tests.py" in text, text
        assert "thing.cli:main" in text, text
        assert "Thing manages widgets for the warehouse team." in text, text
        assert "pyproject.toml" in text
    finally:
        box.close()


def test_every_stable_heading_is_present_in_a_new_file():
    """Section 20. Both a human and TMT find things by heading, and an update
    is scoped to one -- so a file missing a heading is a file an update has to
    invent a place in."""
    box = Project()
    try:
        box.use()
        notes = agent_context.Document(agent_context.initial_notes(box.path))
        for name in agent_context.NOTES_SECTIONS:
            assert notes.has(name), name
        progress = agent_context.Document(agent_context.initial_progress("a task"))
        for name in agent_context.PROGRESS_SECTIONS:
            assert progress.has(name), name
    finally:
        box.close()


def test_a_new_progress_file_claims_no_work_that_has_not_happened():
    """Section 10 and section 31. The one thing genuinely done at this moment
    is the initialisation, so that is the one box ticked -- and the user's
    task is OPEN, because it has not been started."""
    text = agent_context.initial_progress("Add OAuth authentication")
    assert "- [x] TMT project context initialized" in text
    assert "- [ ] Add OAuth authentication" in text
    assert "- [x] Add OAuth authentication" not in text
    assert "No tests run yet." in text


def test_the_context_directory_is_not_listed_as_part_of_the_project():
    """Found by running two real sessions against a temp project: the second
    one opened "Top-level directories: src/, TMT_Context/", which makes the
    file describe itself. TMT's notes are not the project's architecture."""
    box = Project(files={"src/app.py": "x = 1\n"})
    try:
        box.use()
        agent_context.ProjectContext().ensure("a task")
        # Regenerated with the directory already on disk, which is exactly the
        # state a second session finds.
        text = agent_context.initial_notes(box.path)
        architecture = agent_context.Document(text).section("Architecture")
        assert "`src/`" in architecture, architecture
        assert "TMT_Context" not in architecture, architecture
    finally:
        box.close()


def test_a_readme_callout_is_not_mistaken_for_the_description():
    """Found by running it on TMT's own repository: the README opens with a
    blockquote saying "Needs Python 3.8+", and the notes quoted a version
    requirement as though it were what the project does."""
    box = Project(files={"README.md": (
        "# Thing\n\n> **Needs Python 3.8+.**\n\n"
        "[![build](a.svg)](b)\n\nThing indexes the warehouse.\n")})
    try:
        box.use()
        text = agent_context.initial_notes(box.path)
        assert "Thing indexes the warehouse." in text, text
        assert "Needs Python 3.8+" not in text, text
        assert "build](a.svg" not in text, text
    finally:
        box.close()


# --- secrets ----------------------------------------------------------------

def test_a_secret_is_never_written_into_the_context():
    """Sections 23 and 47. These files get committed and pushed, which makes
    this the highest-consequence write in the program."""
    box = Project()
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        result = context.update_notes(
            "Configuration",
            "Set API_KEY=sk-or-v1-0123456789abcdef0123456789abcdef and run it.")
        assert result.ok, result
        written = box.read("notes.md")
        assert "sk-or-v1-0123456789abcdef0123456789abcdef" not in written
        assert "[redacted]" in written
        # And the redaction is REPORTED rather than done quietly, so the model
        # and the transcript both know a credential was handled here.
        assert "Redacted" in str(result), str(result)
    finally:
        box.close()


def test_a_note_that_was_nothing_but_a_secret_is_refused_rather_than_stored():
    """Storing "[redacted]" teaches the next session nothing and only records
    that a secret was once handled here."""
    box = Project()
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        result = context.update_notes("Configuration",
                                      "sk-or-v1-0123456789abcdef0123456789abcd")
        assert not result.ok, result
        assert "redaction" in str(result).lower(), str(result)
    finally:
        box.close()


def test_the_generated_notes_name_environment_variables_but_never_their_values():
    """Section 47 exactly: "API_KEY environment variable is required", never
    "API_KEY=abc123". The real .env is not opened at all."""
    box = Project(files={
        ".env.example": "API_KEY=\nDATABASE_URL=\nDEBUG=false\n",
        ".env": "API_KEY=sk-live-REALSECRETVALUE9876\n",
    })
    try:
        box.use()
        text = agent_context.initial_notes(box.path)
        assert "API_KEY" in text, text
        assert "DATABASE_URL" in text, text
        assert "sk-live-REALSECRETVALUE9876" not in text, text
        # The real file's presence is recorded from a stat; its contents are not.
        assert "`.env`" in text, text
        assert "does not read" in text, text
    finally:
        box.close()


# --- staleness --------------------------------------------------------------

def test_a_note_naming_a_file_that_is_gone_is_reported_as_stale():
    """Sections 17 and 18, and section 40H. The context is memory; the
    repository is true."""
    box = Project(files={"src/service.py": "x = 1\n"})
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        context.update_notes("Architecture",
                             "The entry point is `src/main.py`. "
                             "Services live in `src/service.py`.")
        stale = context.stale_notes()
        assert "src/main.py" in stale, stale
        assert "src/service.py" not in stale, stale
        # And the doubt is stated beside the claim, in the prompt itself, so
        # the model reads both together rather than believing the note.
        block = context.for_prompt()
        assert "STALE" in block, block
        assert "src/main.py" in block
    finally:
        box.close()


def test_staleness_never_edits_the_notes_by_itself():
    """A file may be missing because the note is stale or because the user is
    mid-refactor, and the runtime cannot tell those apart. Correcting it
    automatically would be the runtime guessing."""
    box = Project()
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        context.update_notes("Architecture", "Start at `src/gone.py`.")
        before = box.read("notes.md")
        context.stale_notes()
        context.for_prompt()
        assert box.read("notes.md") == before
    finally:
        box.close()


def test_an_identifier_in_backticks_is_not_mistaken_for_a_missing_file():
    """`agent_config.ROOT_DIR` is a name, not a path. Reporting it as a
    missing file would fill the warning with noise and teach the model to
    ignore it."""
    box = Project()
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        context.update_notes("Architecture",
                             "The root is `agent_config.ROOT_DIR`, set by "
                             "`set_workspace_root`, and `/etc/hosts` is read.")
        stale = context.stale_notes()
        assert "agent_config.ROOT_DIR" not in stale, stale
        assert "set_workspace_root" not in stale, stale
        # An absolute path is not relative to this workspace, so its absence
        # proves nothing and it is not reported either.
        assert not any("etc/hosts" in name for name in stale), stale
    finally:
        box.close()


# --- the prompt block -------------------------------------------------------

def test_the_prompt_block_is_bounded_and_says_what_it_left_out():
    """Section 40K and section 15. Nothing stops these files growing, and a
    truncated block that looked complete would teach the model that a section
    it cannot see does not exist."""
    box = Project()
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        # A notes file far past any budget, in a low-priority section.
        context.update_notes("Dependencies", "x " * 20000, mode="replace")
        block = context.for_prompt()
        assert len(block) < (agent_context.NOTES_BUDGET
                             + agent_context.PROGRESS_BUDGET + 4000), len(block)
        assert "not shown here to save room" in block, block[-500:]
    finally:
        box.close()


def test_a_hand_edited_duplicate_heading_is_not_shown_twice_or_lost():
    """`_split` keeps both when a user's file holds two sections with the same
    name, and `section(name)` can only ever answer with the first -- so a
    budgeting rule that selected by NAME would emit the first twice and
    silently drop the second, which looks exactly like the file being shown
    correctly. It selects by position instead."""
    document = agent_context.Document(
        "## Architecture\n\nFIRST-COPY\n\n## Architecture\n\nSECOND-COPY\n")
    text = agent_context._budgeted(document, 40, ("Architecture",), "notes.md")
    assert text.count("FIRST-COPY") + text.count("SECOND-COPY") <= 2, text
    assert text.count("FIRST-COPY") <= 1, text


def test_the_dropped_sections_line_names_the_right_file():
    """Worked out from the caller rather than from the document's own H1: a
    user who retitled their notes would otherwise be told to read
    progress.md."""
    document = agent_context.Document(
        "# Something The User Renamed\n\n## Architecture\n\n" + "x " * 400)
    text = agent_context._budgeted(document, 50, (), agent_context.NOTES_NAME)
    assert "TMT_Context/notes.md" in text, text[-200:]


def test_a_small_context_is_shown_whole():
    """Section 15 says loading both files is acceptable when they are small,
    and that is the common case. Truncating a short file would spend the
    feature's whole value to save nothing."""
    box = Project()
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        context.update_notes("Architecture", "UNIQUE-ARCHITECTURE-FACT")
        context.update_notes("Constraints", "UNIQUE-CONSTRAINT-FACT")
        block = context.for_prompt()
        assert "UNIQUE-ARCHITECTURE-FACT" in block
        assert "UNIQUE-CONSTRAINT-FACT" in block
        assert "not shown here to save room" not in block
    finally:
        box.close()


def test_the_prompt_block_tells_the_model_the_repository_outranks_it():
    """Section 17. The block is memory and has to introduce itself as memory,
    or a model reads it as a specification."""
    box = Project()
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        block = context.for_prompt()
        assert "not truth" in block, block
        assert "repository is right" in block, block
    finally:
        box.close()


def test_there_is_no_block_at_all_when_there_is_no_context():
    """A prompt with no project context must be byte for byte the prompt that
    existed before there was such a thing."""
    box = Project()
    try:
        box.use()
        assert agent_context.ProjectContext().for_prompt() == ""
    finally:
        box.close()


# --- the setting ------------------------------------------------------------

def test_with_the_setting_off_nothing_is_created_read_or_written():
    """Section 39. OFF has to mean off at every entry point, not only at the
    one the loop happens to call."""
    box = Project()
    try:
        box.use()
        agent_config.PROJECT_CONTEXT = False
        context = agent_context.ProjectContext()
        assert context.ensure("a task").status == agent_context.DISABLED
        assert not (box.path / "TMT_Context").exists()
        assert context.for_prompt() == ""
        assert context.update_notes("Architecture", "x").status \
            == agent_context.DISABLED
        assert not context.available
    finally:
        box.close()


def test_turning_the_setting_off_never_deletes_an_existing_context():
    """Section 39, and the half that matters: the files are the project's, and
    a setting is not consent to remove somebody's notes."""
    box = Project()
    try:
        box.use()
        agent_context.ProjectContext().ensure("a task")
        box.write("notes.md", "# TMT Project Notes\n\n## Architecture\n\nKeep me.\n")
        agent_config.PROJECT_CONTEXT = False
        agent_context.ProjectContext().ensure("another task")
        assert (box.path / "TMT_Context" / "notes.md").is_file()
        assert "Keep me." in box.read("notes.md")
    finally:
        box.close()


def test_the_setting_defaults_to_on_for_every_unreadable_value():
    """The rule read_saved_effort and read_saved_auto_update already follow: a
    missing file is a fresh installation and a typo is a typo, and neither is
    evidence the user wanted the feature off."""
    previous = agent_config.PROJECT_CONTEXT_FILE
    box = tempfile.mkdtemp(prefix="tmt_setting_")
    try:
        agent_config.PROJECT_CONTEXT_FILE = Path(box) / ".tmt_context"
        assert agent_config.read_saved_project_context() is True
        agent_config.PROJECT_CONTEXT_FILE.write_text("banana\n", encoding="utf-8")
        assert agent_config.read_saved_project_context() is True
        agent_config.PROJECT_CONTEXT_FILE.write_text("off\n", encoding="utf-8")
        assert agent_config.read_saved_project_context() is False
        agent_config.PROJECT_CONTEXT_FILE.write_text("YES\n", encoding="utf-8")
        assert agent_config.read_saved_project_context() is True
    finally:
        agent_config.PROJECT_CONTEXT_FILE = previous
        remove_tree(box)


def test_the_setting_file_lives_beside_the_install_and_never_in_the_workspace():
    """The context FILES are project data and belong in the project. The
    SETTING is TMT's and belongs with the other per-install choices -- this is
    the one part of the feature that follows the original rule."""
    box = Project()
    try:
        box.use()
        path = Path(agent_config.PROJECT_CONTEXT_FILE).resolve()
        assert path.parent == INSTALL_DIR, path
        assert box.path not in path.parents, path
    finally:
        box.close()


# --- failing safely ---------------------------------------------------------

def test_a_workspace_that_cannot_be_written_does_not_raise():
    """Section 38 and section 40J. A read-only checkout is not a reason to
    refuse work the user asked for."""
    box = Project()
    try:
        box.use()
        # A FILE where the directory has to go: the most reliable way to make
        # mkdir fail on every platform this runs on.
        (box.path / "TMT_Context").write_text("not a directory", encoding="utf-8")
        context = agent_context.ProjectContext()
        result = context.ensure("a task")
        assert not result.ok, result
        assert result.status == agent_context.FAILED
        assert "Continuing without TMT_Context" in str(result), str(result)
        # And every other entry point survives it too, because the loop will
        # go on calling them for the rest of the session.
        assert context.for_prompt() == ""
        assert context.stale_notes() == []
        assert not context.update_notes("Architecture", "x").ok
        assert isinstance(context.describe(), str)
    finally:
        box.close()


def test_a_failure_is_announced_once_and_not_at_the_top_of_every_question():
    """A context that cannot be made fails again on EVERY turn -- the
    directory is still not there and `ensure` still cannot make it -- so a
    caller that printed the message each time would put the same yellow
    warning above every question for the rest of the session."""
    box = Project()
    try:
        box.use()
        (box.path / "TMT_Context").write_text("not a directory", encoding="utf-8")
        context = agent_context.ProjectContext()
        assert context.ensure("turn one").status == agent_context.FAILED
        assert context.announce() is True
        for turn in range(4):
            assert context.ensure("turn %d" % turn).status == agent_context.FAILED
            assert context.announce() is False, turn
        # And once it can be made, the failure is forgotten -- so the SAME
        # problem happening again later is reported again rather than being
        # swallowed as "already said".
        (box.path / "TMT_Context").unlink()
        assert context.ensure("turn five").ok
        assert context.announce() is False
        (box.path / "TMT_Context" / "notes.md").unlink()
        (box.path / "TMT_Context" / "progress.md").unlink()
        (box.path / "TMT_Context").rmdir()
        (box.path / "TMT_Context").write_text("again", encoding="utf-8")
        assert context.ensure("turn six").status == agent_context.FAILED
        assert context.announce() is True
    finally:
        box.close()


def test_an_unreadable_context_file_reads_as_an_empty_one():
    """A context that cannot be read is a context that says nothing, which is
    what an empty one says. The two need no separate handling and neither may
    raise."""
    box = Project()
    try:
        box.use()
        directory = box.path / "TMT_Context"
        directory.mkdir()
        # A directory where notes.md has to be.
        (directory / "notes.md").mkdir()
        context = agent_context.ProjectContext()
        assert context.notes().sections == []
        assert isinstance(context.for_prompt(), str)
    finally:
        box.close()


def test_a_write_past_the_file_ceiling_is_refused_rather_than_performed():
    """Section 36. A context file that has grown past reading is one nobody
    will ever correct, and the honest response is to say so."""
    box = Project()
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        big = "y" * (agent_context.MAX_FILE_CHARS + 1000)
        (box.path / "TMT_Context" / "notes.md").write_text(
            "# TMT Project Notes\n\n## Architecture\n\n" + big + "\n",
            encoding="utf-8")
        result = context.update_notes("Constraints", "one more line")
        assert not result.ok, result
        assert "Prune it" in str(result), str(result)
    finally:
        box.close()


# --- what the plan, the verification and the review contribute --------------

def test_a_plan_is_mirrored_with_only_completed_steps_ticked():
    """Section 13. A step in progress is an open box: ticking it would be the
    file claiming work the plan itself says is unfinished."""
    import agent_plan
    plan = agent_plan.Plan()
    plan.create([{"title": "Inspect auth"}, {"title": "Implement OAuth"},
                 {"title": "Add tests"}])
    plan.update(1, "completed")
    checklist = agent_context.plan_checklist(plan)
    assert "- [x] Inspect auth" in checklist, checklist
    assert "- [ ] Implement OAuth (in progress)" in checklist, checklist
    assert "- [ ] Add tests" in checklist, checklist
    assert "- [x] Add tests" not in checklist


def test_no_plan_produces_no_mirror_rather_than_an_empty_one():
    """An empty checklist under a heading reads as a plan with nothing in it,
    which is a different claim from having made no plan."""
    assert agent_context.plan_checklist(None) == ""
    import agent_plan
    assert agent_context.plan_checklist(agent_plan.Plan()) == ""


def test_a_test_result_is_only_recorded_when_a_run_actually_produced_it():
    """Section 11, and the guard is that there is no other source for the
    number: an absent or unsettled verification produces nothing at all."""
    import agent_verify
    assert agent_context.tests_line(None) == ""
    assert agent_context.tests_line(agent_verify.VerificationState()) == ""


def test_a_real_verification_result_is_recorded_with_the_number_it_produced():
    """The other half of section 11: what IS written is what the exit codes
    said, quoted from the result object rather than described."""
    import agent_verify
    check = agent_verify.VerificationCheck("c1", "Full test suite", "test", 6,
                                           command=("python", "run_tests.py"))
    check.start()
    check.record(0, "1509 passed, 0 failed")
    result = agent_verify.VerificationResult(checks=(check,), number=1)
    state = agent_verify.VerificationState()
    state.settle(result)
    line = agent_context.tests_line(state)
    assert "VERIFY PASSED" in line, line
    assert "1 passed, 0 failed" in line, line
    assert "python run_tests.py" in line, line


def test_a_finished_task_is_recorded_as_completed_and_an_unfinished_one_is_not():
    """Section 10, at the one place it is easiest to break. Evidence is a plan
    that finished or files that were written -- never the model's opinion."""
    box = Project()
    try:
        box.use()
        import agent_plan
        context = agent_context.ProjectContext()
        context.ensure("first task")

        # Nothing ran: it stays open.
        agent_context.finalize(context, "Add password reset")
        progress = context.progress()
        assert "- [ ] Add password reset" in progress.section("Currently Working On")
        assert "Add password reset" not in progress.section("Completed")

        # A plan that finished: it is completed, and it stops being current.
        plan = agent_plan.Plan()
        plan.create([{"title": "one"}, {"title": "two"}])
        plan.update(1, "completed")
        plan.update(2, "completed")
        agent_context.finalize(context, "Add password reset", plan=plan)
        progress = context.progress()
        assert "- [x] Add password reset" in progress.section("Completed")
        assert "- [ ] Add password reset" not in progress.section("Currently Working On")
    finally:
        box.close()


def test_a_task_is_never_listed_as_both_outstanding_and_complete():
    """Found by driving TMT on its own repository, in the first progress file
    the feature ever wrote. Two callers write the same task into the same
    file -- `initial_progress` opens it and `finalize` closes it -- and they
    truncated it at different lengths, so the close never matched what the
    open had written and the entry appeared under BOTH headings at once."""
    box = Project()
    try:
        box.use()
        long_task = ("Everything for this change is already staged, so commit "
                     "exactly what is staged and then push to main; do not "
                     "name any paths yourself and do not build a path list, "
                     "pass no paths at all to git_commit so it commits the "
                     "staged index as it stands.")
        context = agent_context.ProjectContext()
        # ensure() writes the OPEN item through initial_progress...
        context.ensure(long_task)
        progress = context.progress()
        assert long_task[:40] in progress.section("Currently Working On")
        # ...and finalize must find and close that exact item.
        agent_context.finalize(context, long_task, wrote=("src/app.py",))
        progress = context.progress()
        assert long_task[:40] in progress.section("Completed"), \
            progress.section("Completed")
        assert long_task[:40] not in progress.section("Currently Working On"), \
            progress.section("Currently Working On")
    finally:
        box.close()


def test_a_progress_entry_is_a_short_line_and_not_a_pasted_instruction():
    """Sections 34 and 35. A progress file has to answer three questions at a
    glance, and a 700-character instruction in a bullet answers none of them.
    The first clause is kept where there is one, so what survives is the
    request rather than a sentence sawn through at a character count."""
    said = agent_context._headline(
        "Everything for this change is already staged, so commit exactly what "
        "is staged and then push to main; do not name any paths yourself and "
        "do not build a path list, pass no paths at all to git_commit.")
    assert len(said) <= agent_context.MAX_HEADLINE_CHARS, (len(said), said)
    assert "push to main" in said, said
    assert "do not name any paths" not in said, said
    # A task with no clause break is still capped, and says it was cut.
    long_one = agent_context._headline("x " * 300)
    assert len(long_one) <= agent_context.MAX_HEADLINE_CHARS, len(long_one)
    assert long_one.endswith("…"), long_one
    # A short task is left exactly as it is.
    assert agent_context._headline("Add dark mode") == "Add dark mode"


def test_finalizing_preserves_a_users_own_prose_in_the_same_section():
    """Section 19 and section 37, at the end of a task rather than mid-way.
    The plan mirror replaces the mirror and nothing else."""
    box = Project()
    try:
        box.use()
        import agent_plan
        context = agent_context.ProjectContext()
        context.ensure("a task")
        context.update_progress("Currently Working On",
                                "NOTE FROM PRIYA: do not touch the billing code.",
                                mode="replace")
        plan = agent_plan.Plan()
        plan.create([{"title": "one"}])
        agent_context.finalize(context, "some task", plan=plan)
        current = context.progress().section("Currently Working On")
        assert "NOTE FROM PRIYA: do not touch the billing code." in current, current
        assert "### Plan Progress" in current, current
        # And a SECOND finalize replaces the mirror without duplicating it or
        # eating the note.
        plan.update(1, "completed")
        agent_context.finalize(context, "some task", plan=plan)
        current = context.progress().section("Currently Working On")
        assert current.count("### Plan Progress") == 1, current
        assert "NOTE FROM PRIYA: do not touch the billing code." in current, current
    finally:
        box.close()


def test_finalizing_against_a_broken_state_object_is_a_result_rather_than_a_crash():
    """It runs at the END of a turn the user has already waited for, and it
    walks a plan, a verification result and a review verdict -- any of which
    could be an object of a shape it has not seen. The worst outcome of
    swallowing is a note that was not written; the worst outcome of raising is
    the answer being lost after the work was done."""
    class Hostile:
        @property
        def steps(self):
            raise RuntimeError("not a plan")

    box = Project()
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        result = agent_context.finalize(context, "a task", plan=Hostile(),
                                        verify=Hostile(), review=Hostile())
        assert result.status == agent_context.FAILED, result.status
        assert isinstance(str(result), str)
    finally:
        box.close()


def test_finalizing_without_a_context_is_a_result_rather_than_a_crash():
    """It runs at the end of every turn that answers. A memory that cannot be
    written must never be able to stop an answer the user has waited for."""
    assert agent_context.finalize(None, "a task").status == agent_context.DISABLED
    box = Project()
    try:
        box.use()
        # A context that was never created: nothing to update, and it says so.
        result = agent_context.finalize(agent_context.ProjectContext(), "a task")
        assert result.status == agent_context.DISABLED, result.status
    finally:
        box.close()
