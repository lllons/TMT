"""The project context through the real loop: created, loaded, written, persisted.

`test_agent_context.py` tests the module. This tests the six places it is
wired into a running TMT, because every one of them is a place a feature can be
correct and unreachable:

    the first-task hook       TMT._session_loop, after the command fork
    the action                agent_actions.execute_action -> _project_context
    the prompt                agent_prompt.get_system_prompt(caps, context)
    the persist hook          TMT.persist_context, at both terminal sites
    the worker refusal        agent_worker.WORKER_FORBIDDEN
    the packaging             pyproject.toml py-modules

Everything here drives `TMT.main` through `drive_session`, which is the only
path a real user takes. Two traps from CLAUDE.local.md shape how the scripts
are written and are worth restating, because both produce a green test that
proves nothing:

  A script ending in "quit" stops one turn short. `TMT.py` returns on quit
      BEFORE `begin_turn`, so a suite whose every test asks one question and
      quits never runs the code that starts a SECOND turn -- which is exactly
      how the planning crash shipped green. Anything here about state carried
      between turns asks another ordinary question first.

  Replies are indexed by GLOBAL model-call count, not per turn. Anything that
      makes a turn call the model an extra time eats the reply meant for the
      next one, and the test then fails somewhere unrelated.
"""

import io
import json
import os
import shutil
import tempfile
from pathlib import Path

import agent_actions
import agent_config
import agent_context
import agent_prompt
import agent_worker
import TMT
from test_agent_cli import drive_session
from test_agent_workspace import Workspace


def answer(message="Done."):
    return json.dumps({"action": "end_conversation", "message": message})


def context_in(box):
    return box / agent_context.CONTEXT_DIR_NAME


def notes_of(box):
    return (context_in(box) / agent_context.NOTES_NAME).read_text(encoding="utf-8")


def progress_of(box):
    return (context_in(box) / agent_context.PROGRESS_NAME).read_text(encoding="utf-8")


class Setting:
    """The project-context setting, put back afterwards.

    It redirects the FILE and not only the module global, and that is not
    tidiness -- `TMT.main` calls `agent_config.refresh_project_context()` at
    startup, which re-reads the file and overwrites whatever a test assigned.
    A test that set only the global would watch the feature switch itself back
    on at the top of the run and then fail for a reason nowhere near the line
    that caused it. (Found exactly that way.)

    Both are process-global, exactly as ROOT_DIR is, and a leaked OFF would
    silently disable the feature for every test that ran afterwards -- which
    would look like the feature being broken rather than like this file being
    untidy.
    """

    def __init__(self, value):
        self.previous = agent_config.PROJECT_CONTEXT
        self.previous_file = agent_config.PROJECT_CONTEXT_FILE
        self.box = Path(tempfile.mkdtemp(prefix="tmt_ctxsetting_"))
        agent_config.PROJECT_CONTEXT_FILE = self.box / ".tmt_context"
        agent_config.PROJECT_CONTEXT_FILE.write_text(
            "on\n" if value else "off\n", encoding="utf-8")
        agent_config.PROJECT_CONTEXT = value

    def close(self):
        agent_config.PROJECT_CONTEXT = self.previous
        agent_config.PROJECT_CONTEXT_FILE = self.previous_file
        shutil.rmtree(self.box, ignore_errors=True)


def run_in(box, answers, reply, files=None):
    """Drive TMT.main inside `box`, and return (drawn, requests, console).

    `drive_session` builds its own throwaway workspace and deletes it before
    returning, which is right for the tests that only read the screen and
    wrong for every test here that has to look at the FILES afterwards. This
    is the same drive against a workspace the caller owns.

    `reply` is called with the request count so far, so a test can answer
    differently per turn without the global-index trap `drive_session` has.
    Anything the loop reports through `console.print` -- which is where a
    context that could not be created is said -- lands on the returned console
    rather than on the screen, so both are handed back.
    """
    import contextlib
    from test_agent_cli import Reporting
    screen = io.StringIO()
    saved = (TMT.console, TMT.ensure_api_key, TMT.run_startup,
             TMT.ensure_git_identity, TMT.ask_model)
    previous = Path.cwd()
    seen = []
    try:
        os.chdir(str(box))
        console = TMT.console = Reporting(list(answers))
        TMT.ensure_api_key = lambda: True
        TMT.run_startup = lambda **kwargs: "start"
        TMT.ensure_git_identity = lambda *a, **k: None

        def watching(messages, on_event=None):
            seen.append([dict(message) for message in messages])
            return reply(len(seen))
        TMT.ask_model = watching
        with contextlib.redirect_stdout(screen):
            TMT.main([])
    finally:
        os.chdir(str(previous))
        (TMT.console, TMT.ensure_api_key, TMT.run_startup,
         TMT.ensure_git_identity, TMT.ask_model) = saved
    return screen.getvalue(), seen, console


# --- the first task ---------------------------------------------------------

def test_the_first_real_task_creates_the_context_in_the_workspace():
    """Section 40A end to end. `drive_session` runs TMT.main in a fresh temp
    directory, so this is the whole flow: launch, first prompt, directory."""
    setting = Setting(True)
    try:
        drawn, seen, console = drive_session(
            ["add a dark mode", "quit"], [answer("Added it.")])
    finally:
        setting.close()
    # The workspace `drive_session` used is gone by now, so what is checked is
    # what reached the screen and what reached the prompt.
    assert "TMT_Context" in drawn, drawn[-3000:]
    assert "notes.md" in drawn and "progress.md" in drawn, drawn[-3000:]
    # And the prompt for that very turn carried the context it had just made.
    system = seen[0][0]["content"]
    assert "=== PROJECT CONTEXT (TMT_Context/) ===" in system
    assert "notes.md (how this project works)" in system


def test_a_slash_command_is_not_a_first_task_and_creates_nothing():
    """Section 3. The context is initialised when the user starts WORKING, not
    when they ask what model they are on. `/context` and `/config` reach the
    loop before the initialiser and must go straight past it."""
    setting = Setting(True)
    try:
        drawn, seen, console = drive_session(
            ["/config", "/context", "quit"], [answer()])
    finally:
        setting.close()
    # No model call at all, so no turn happened...
    assert seen == [], seen
    # ...and nothing announced a directory.
    assert "Created notes.md" not in drawn, drawn[-2000:]


def test_an_existing_context_is_loaded_rather_than_rebuilt():
    """Sections 40B and 40E through the real loop: a context written before
    TMT started is in the first prompt of the session, unchanged.

    `git=True` on the workspace, and it is load-bearing rather than scenery:
    `agent_config.workspace_needs_confirmation` asks out loud before adopting
    a directory that already holds files and has no version control, and that
    question consumes one of the scripted answers -- so a non-git fixture with
    files in it silently eats the first task and the test then fails with an
    IndexError nowhere near the cause. A git repository starts silently.
    """
    box = Workspace(git=True, files={
        "TMT_Context/notes.md":
            "# TMT Project Notes\n\n## Architecture\n\nCARRIED-FROM-LAST-SESSION\n",
        "TMT_Context/progress.md":
            "# TMT Project Progress\n\n## Remaining\n\n- [ ] STILL-TO-DO-ITEM\n",
    })
    setting = Setting(True)
    try:
        drawn, seen, console = run_in(
            box.path, ["carry on", "quit"], lambda n: answer("Carried on."))
        system = seen[0][0]["content"]
        assert "CARRIED-FROM-LAST-SESSION" in system, system[-3000:]
        assert "STILL-TO-DO-ITEM" in system, system[-3000:]
        # And what was there is still there, in the section TMT did not write.
        assert "CARRIED-FROM-LAST-SESSION" in notes_of(box.path)
    finally:
        setting.close()
        box.close()


def test_with_the_setting_off_no_context_is_created_and_the_prompt_is_unchanged():
    """Section 39 through the loop. OFF has to reach every one of the six
    wiring points, not only the one the loop calls first."""
    box = Workspace(git=True)
    setting = Setting(False)
    try:
        drawn, seen, console = run_in(box.path, ["do something", "quit"],
                                      lambda n: answer())
        assert not (box.path / "TMT_Context").exists()
    finally:
        setting.close()
        box.close()
    system = seen[0][0]["content"]
    assert "PROJECT CONTEXT" not in system, system[-2000:]
    assert "project_context" not in system, system[-2000:]
    assert "TMT_Context" not in drawn, drawn[-2000:]


# --- the prompt -------------------------------------------------------------

def test_a_prompt_with_no_context_is_the_prompt_that_existed_before():
    """The compatibility property. Everything that ever called
    `get_system_prompt(capabilities)` must get exactly what it used to, or
    every test in the suite is testing a different program."""
    box = Workspace()
    try:
        box.use()
        agent_prompt.invalidate_prompt()
        without = agent_prompt.get_system_prompt()
        agent_prompt.invalidate_prompt()
        with_none = agent_prompt.get_system_prompt(None, None)
        assert without == with_none
        # Equality is the whole assertion, and it is stronger than looking for
        # a blank gap: the prompt has ALWAYS held runs of three newlines (a
        # section whose own text ends in one, plus the two the join adds), so
        # "no triple newline" fails against pristine HEAD and would be testing
        # the joiner rather than this feature.
    finally:
        box.close()


def test_the_prompt_teaches_the_verb_only_when_there_is_a_context_to_write():
    """Teaching a model to correct a file it has not been shown costs ~1.5k
    tokens on every request and invites a call that can only come back saying
    there is nothing there."""
    box = Workspace()
    try:
        box.use()
        agent_prompt.invalidate_prompt()
        assert "project_context" not in agent_prompt.get_system_prompt()

        context = agent_context.ProjectContext()
        context.ensure("a task")
        agent_prompt.invalidate_prompt()
        taught = agent_prompt.get_system_prompt(None, context)
        assert "THE PROJECT'S MEMORY" in taught
        assert '{"action":"project_context"' in taught
        assert "USING THE PROJECT'S MEMORY" in taught
    finally:
        box.close()


def test_a_changed_context_is_not_served_from_the_prompt_cache():
    """The cache is keyed on the capabilities, and these two files change
    without anything calling invalidate_prompt: the model writes a note
    mid-turn, and the USER edits them in another window. A cache that ignored
    that would tell round four that round three's note does not exist."""
    box = Workspace()
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        agent_prompt.invalidate_prompt()
        before = agent_prompt.get_system_prompt(None, context)
        assert "MARKER-WRITTEN-MID-TURN" not in before
        # No invalidate_prompt() here, deliberately: this is the case the key
        # exists for.
        context.update_notes("Architecture", "MARKER-WRITTEN-MID-TURN")
        after = agent_prompt.get_system_prompt(None, context)
        assert "MARKER-WRITTEN-MID-TURN" in after, after[-2500:]
        # And an unchanged context still hits the cache, which is what keeps
        # the common case as cheap as it was.
        assert agent_prompt.get_system_prompt(None, context) is after
    finally:
        box.close()


def test_the_context_block_is_placed_before_the_workspace_snapshot():
    """Section 16. The order is the feature: read what was already worked out,
    THEN look at the repository."""
    box = Workspace(files={"a.py": "x = 1\n"})
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        agent_prompt.invalidate_prompt()
        prompt = agent_prompt.get_system_prompt(None, context)
        assert (prompt.index("=== PROJECT CONTEXT")
                < prompt.index("=== CURRENT WORKSPACE FILES AND CONTENTS ===")), \
            "the memory must be read before the repository"
    finally:
        box.close()


def test_the_context_files_are_named_in_the_snapshot_but_not_inlined_twice():
    """They are already in the prompt, budgeted, a few sections above. The
    snapshot inlines anything under 8 KB whole and knows nothing about that
    budget, so without this the same text went in twice -- and spent the
    snapshot's 40 KB allowance on TMT's own writing instead of on the user's
    source, which is what the allowance is for.

    NAMED rather than hidden, in both directions: the snapshot says the files
    are there and says where their content is, and `list_files` still finds
    them, because the skip is local to the snapshot and nothing else walks
    through it.
    """
    box = Workspace(files={"app.py": "print(1)\n"})
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        context.update_notes("Architecture", "UNIQUE-NOTE-BODY-MARKER " * 200)
        agent_prompt.invalidate_prompt()
        prompt = agent_prompt.get_system_prompt(None, context)
        snapshot = prompt[prompt.index("=== CURRENT WORKSPACE FILES AND CONTENTS ==="):]
        assert "--- TMT_Context" not in snapshot, snapshot[:1500]
        # But it says they exist, and where to find what is in them.
        assert "TMT_Context/notes.md" in snapshot, snapshot[:1500]
        assert "PROJECT CONTEXT" in snapshot, snapshot[:1500]
        # The user's own source is inlined exactly as it always was.
        assert "--- app.py ---" in snapshot
        # And every other surface still sees them as ordinary files.
        import agent_actions
        listed = agent_actions.execute_action({"action": "list_files"}, {})
        assert "notes.md" in str(listed), listed
    finally:
        box.close()


def test_the_context_does_not_grow_the_prompt_without_bound():
    """Section 40K. The block rides on every request of every step and is
    pinned, so it comes straight off the conversation-history budget for the
    whole session. Whatever the files hold, the cost has a ceiling."""
    box = Workspace()
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        agent_prompt.invalidate_prompt()
        small = len(agent_prompt.get_system_prompt(None, context))
        for section in agent_context.NOTES_SECTIONS:
            context.update_notes(section, "z " * 8000, mode="replace")
        for section in agent_context.PROGRESS_SECTIONS:
            context.update_progress(section, "z " * 8000, mode="replace")
        agent_prompt.invalidate_prompt()
        huge = len(agent_prompt.get_system_prompt(None, context))
        ceiling = (agent_context.NOTES_BUDGET + agent_context.PROGRESS_BUDGET
                   + 4000)
        assert huge - small < ceiling, (small, huge)
    finally:
        box.close()


# --- the action -------------------------------------------------------------

def test_the_action_writes_one_section_and_reports_what_it_did():
    box = Workspace()
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        result = agent_actions.execute_action(
            {"action": "project_context", "operation": "note",
             "section": "Architecture",
             "content": "Routes are registered in `src/routes.py`."},
            {"context": context})
        assert "Recorded under 'Architecture'" in str(result), result
        assert "src/routes.py" in notes_of(box.path)
    finally:
        box.close()


def test_the_action_has_a_label_and_is_registered():
    """Two registries a new verb has to reach, and nothing global checks
    either: `validate_action` refuses an unregistered verb for EVERY agent,
    and a missing label draws the raw verb in a transcript where every
    neighbouring row is title-cased. CLAUDE.local.md records that happening."""
    assert agent_config.REQUIRED_KEYS["project_context"] == ["operation"]
    assert agent_actions.ACTION_LABELS["project_context"] == "Project Context"
    assert agent_prompt.validate_action(
        {"action": "project_context", "operation": "show"}) is None
    problem = agent_prompt.validate_action({"action": "project_context"})
    assert problem and "operation" in problem, problem


def test_every_example_in_the_context_prompt_is_a_real_action():
    """An example that broke a rule would teach breaking it. The same test
    `REVIEW_REFERENCE` has, with the same regex, because these examples are
    read by the same model under the same rules -- and one of them is a
    deliberately WRONG example inside rule 5, which must still be a valid
    action or it would be teaching two mistakes instead of one."""
    import re
    found = 0
    for section in (agent_prompt.CONTEXT_REFERENCE, agent_prompt.CONTEXT_RULES):
        for match in re.findall(r'^\s*(?:BAD:|GOOD:)?\s*(\{"action".*)$',
                                section, re.MULTILINE):
            obj = json.loads(match)
            assert agent_prompt.validate_action(obj) is None, match
            found += 1
    assert found >= 6, found
    # And every operation the reference names is one the handler accepts, so
    # the prompt cannot teach a verb the dispatcher answers with "FAILED".
    for operation in agent_actions.CONTEXT_OPERATIONS:
        assert '"operation":"%s"' % operation in agent_prompt.CONTEXT_REFERENCE \
            or "  %s " % operation in agent_prompt.CONTEXT_REFERENCE, operation


def test_the_action_refuses_a_bad_operation_in_words_rather_than_raising():
    """A call the model got wrong is a mistake to correct on the next step,
    exactly like a patch whose search string did not match."""
    box = Workspace()
    try:
        box.use()
        context = agent_context.ProjectContext()
        context.ensure("a task")
        for obj in ({"action": "project_context", "operation": "delete"},
                    {"action": "project_context", "operation": "note"},
                    {"action": "project_context", "operation": "note",
                     "section": "Architecture", "content": "x", "mode": "nuke"}):
            result = agent_actions.execute_action(obj, {"context": context})
            assert str(result).startswith("FAILED"), result
    finally:
        box.close()


def test_the_action_is_not_in_mutating_actions():
    """Two consequences, and the second is the heavier one. It would rebuild
    the whole workspace snapshot to say the same thing about the same source
    files -- and `TMT.note_work` would make a PASSED review and a PASSED
    verification stale, so every note stored would re-gate the final answer."""
    assert "project_context" not in agent_config.MUTATING_ACTIONS


def test_a_background_agent_can_neither_use_nor_reach_the_context():
    """Two-sided, exactly as plan, review and verify are: refused by the
    worker loop, and absent from the context a background agent is given, so
    even reaching execute_action directly finds nothing to write to."""
    assert "project_context" in agent_worker.WORKER_FORBIDDEN
    assert "project_context" not in agent_worker.NOTE_ACTIONS
    assert "project_context" not in agent_worker.REVIEW_ACTIONS
    # And it is taught to none of the three background prompts.
    import agent_subprompts
    for prompt in (agent_subprompts.worker_prompt(),
                   agent_subprompts.note_prompt(),
                   agent_subprompts.review_prompt()):
        assert "THE PROJECT'S MEMORY" not in prompt
    # A worker's own context carries no key, so the action answers in words.
    result = agent_actions.execute_action(
        {"action": "project_context", "operation": "note",
         "section": "Architecture", "content": "x"},
        {"push_authorized": False})
    assert "not available" in str(result), result


# --- persisting at the end of a task ----------------------------------------

def test_the_end_of_a_task_records_it_without_bypassing_any_gate():
    """Section 45. The order must stay work -> verify -> review -> persist ->
    end, and the way that is guaranteed is position: `persist_context` is
    called after `completion_block` returned an empty refusal and after
    `execute_action` ran the ending."""
    source = Path(TMT.__file__).read_text(encoding="utf-8")
    # Both terminal sites call it, and both calls come after the gate.
    assert source.count("persist_context(session, task, history, transcript)") == 2
    gate = source.index("held, held_line = completion_block(session, obj)")
    persist = source.index("persist_context(session, task, history, transcript)",
                           gate)
    assert gate < persist
    # And it is never reached from send_message, which must not finalise
    # anything: a model saying "I have implemented the feature" is a sentence,
    # not evidence. Section 46.
    message_site = source.index("if is_send_message(obj):")
    following = source[message_site:message_site + 700]
    assert "persist_context" not in following, following


def test_a_finished_task_leaves_its_result_in_progress_for_the_next_session():
    """The whole point, driven through the loop: two turns, then the file. The
    second question matters -- a script that asks one and quits never runs the
    code that starts a second turn."""
    box = Workspace()
    try:
        box.use()
        setting = Setting(True)
        previous = Path.cwd()
        screen = io.StringIO()
        saved = (TMT.console, TMT.ensure_api_key, TMT.run_startup,
                 TMT.ensure_git_identity, TMT.ask_model)
        try:
            os.chdir(str(box.path))
            import contextlib
            from test_agent_cli import Reporting
            TMT.console = Reporting(["add the retry helper", "and now the tests",
                                     "quit"])
            TMT.ensure_api_key = lambda: True
            TMT.run_startup = lambda **kwargs: "start"
            TMT.ensure_git_identity = lambda *a, **k: None
            calls = []

            def watching(messages, on_event=None):
                calls.append(1)
                if len(calls) == 1:
                    return json.dumps({
                        "action": "write_file", "path": "retry.py",
                        "content": "def retry():\n    pass\n",
                        "progress": "Writing the retry helper."})
                return answer("Wrote retry.py.")
            TMT.ask_model = watching
            with contextlib.redirect_stdout(screen):
                TMT.main([])
        finally:
            os.chdir(str(previous))
            (TMT.console, TMT.ensure_api_key, TMT.run_startup,
             TMT.ensure_git_identity, TMT.ask_model) = saved
            setting.close()
        recorded = progress_of(box.path)
        # A file was really written, so the task is recorded as done.
        assert "add the retry helper" in recorded, recorded
        assert "- [x] add the retry helper" in recorded, recorded
        # And the second task, which wrote nothing, is NOT claimed as done.
        assert "- [x] and now the tests" not in recorded, recorded
    finally:
        box.close()


def test_a_context_that_cannot_be_written_does_not_stop_the_task():
    """Section 38 through the loop. A file where the directory has to go is
    the most reliable way to break it on every platform, and the user's
    question still has to be answered.

    The warning is read off the CONSOLE and not off the screen: the loop
    reports it through `console.print`, which a test capturing only stdout
    cannot see at all -- the same trap `Reporting` exists for.
    """
    box = Workspace(git=True, files={"TMT_Context": "not a directory\n"})
    setting = Setting(True)
    try:
        drawn, seen, console = run_in(
            box.path, ["what is two plus two", "quit"],
            lambda n: answer("It is four."))
        assert "It is four." in drawn, drawn[-2000:]
        assert "Continuing without TMT_Context" in console.said(), console.said()
        # The task really was answered, so the turn was not lost to it.
        assert len(seen) == 1, len(seen)
    finally:
        setting.close()
        box.close()


def test_the_context_survives_begin_turn_and_clear_when_the_others_do_not():
    """The one inversion in the whole feature, and the property most likely to
    be undone by a future edit tidying five things that look alike.

    `begin_turn` retires the plan, the review, the verification and the
    authorisation, because each belongs to ONE TASK and would gate or excuse an
    unrelated question if it survived. The project context belongs to the
    PROJECT. Retiring it between turns would empty the memory at the start of
    every question, which is exactly the amnesia it exists to end -- and
    `Session.clear` is the same decision again, because `/clear` means "forget
    the conversation", not "forget what this project is".
    """
    from agent_session import Session
    box = Workspace()
    try:
        box.use()
        session = Session(workspace=box.path)
        held = session.context
        session.context.ensure("a task")
        session.context.update_notes("Architecture", "SURVIVES-THE-TURN")

        session.begin_turn("a second question", "prompt")
        assert session.context is held, "the context must not be rebound"
        assert "SURVIVES-THE-TURN" in session.context.notes().section("Architecture")
        # The four that DO get retired still do.
        assert len(session.plan) == 0
        assert session.capabilities.active() == ()

        session.clear()
        assert session.context is held
        assert "SURVIVES-THE-TURN" in session.context.notes().section("Architecture")
        assert len(session) == 0
        # And there is no verb here that could empty it, which is what makes an
        # edit adding one beside the other four fail loudly instead of quietly.
        assert not hasattr(session.context, "retire")
    finally:
        box.close()


# --- packaging --------------------------------------------------------------

def test_the_module_is_declared_so_an_editable_install_can_import_it():
    """An editable install freezes py-modules at install time, so a module
    that is not declared is invisible to `tmtcode` however well it works from
    the checkout. There is a general test for this; this one names the module
    so the failure says which."""
    text = (Path(agent_config.__file__).resolve().parent
            / "pyproject.toml").read_text(encoding="utf-8")
    assert '"agent_context",' in text, "agent_context is missing from py-modules"
