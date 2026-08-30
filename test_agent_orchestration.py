"""The two background prompts, the delegation actions, and /note.

Everything that can go through `execute_action` goes through it, because that
is the only path a model can take: an action that works perfectly and is not
registered is an action that does not exist, and `test_agent_toolflow.py` is
the file that learned it. Everything else goes through `agent_worker`'s own
loop with the model injected, so nothing here makes a request, opens a socket,
or depends on a key being configured.

Three things are asserted structurally rather than by reading a prompt, because
a prompt is a request and these are guarantees:

  * `internal_response` is not in the main loop's terminal tuple, so a main
    model that emitted one would get an ordinary action result and carry on.
  * a background agent's action context carries no `manager`, so every
    delegation verb reports itself unavailable there instead of letting a
    worker spawn workers.
  * `git_push` comes back PUSH_BLOCKED for a worker while the main agent's
    push reaches git, proved against an injected git module so that no test
    here can ever reach a real remote.

No test may block. There is no per-test timeout in this suite, so a wait that
never returned would hang the whole run rather than fail: every wait below has
a short real timeout, every thread parked on an event is released in a
`finally`, and nothing reaches an action that stops for a human to confirm.
"""

import json
import os
import shutil
import stat
import sys
import tempfile
import threading
from pathlib import Path

import agent_actions
import agent_commands
import agent_config
import agent_manager
import agent_prompt
import agent_session
import agent_subprompts
import agent_worker


# --- a throwaway workspace --------------------------------------------------

def remove_tree(path):
    """Delete a temp tree, including anything left read-only on Windows."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


PROJECT = {
    "src/calc.py": (
        "MAX_RETRIES = 3\n"
        "\n"
        "\n"
        "def add(a, b):\n"
        "    return a + b\n"
    ),
    "src/net.py": "timeout = 5\n",
    "tests/test_calc.py": "def test_add():\n    assert True\n",
}


class Project:
    """A temp workspace, with TMT's own state sent somewhere temporary too.

    The same shape as `test_agent_toolflow.Project`, and for the same reason:
    the tools resolve every path against `agent_config.ROOT_DIR`, so a test
    that did not move it would be running destructive actions against the
    repository it is testing.
    """

    def __init__(self, files=None):
        self.previous_root = agent_config.ROOT_DIR
        self.previous_install = agent_config.INSTALL_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_orch_")).resolve()
        self.install = Path(tempfile.mkdtemp(prefix="tmt_orchinst_")).resolve()
        agent_config.ROOT_DIR = self.path
        agent_config.INSTALL_DIR = self.install
        for name, body in (files or {}).items():
            self.write(name, body)
        # Every prompt caches a picture of the workspace, so a cache built for
        # the last test describes a directory this one has never seen. Both
        # ends of the fixture drop them: leaving a temp project's tree in the
        # cache would poison whatever ran next, including tests in other files.
        agent_prompt.invalidate_prompt()

    def write(self, name, body):
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def read(self, name):
        return (self.path / name).read_text(encoding="utf-8")

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        agent_config.INSTALL_DIR = self.previous_install
        agent_prompt.invalidate_prompt()
        remove_tree(self.path)
        remove_tree(self.install)


class Script:
    """Scripted model replies, handed out in order.

    Indexed by call count, like `test_agent_cli.drive_session`, and for the
    same reason it is worth saying out loud: a turn that calls the model an
    extra time -- a retry, a handed-back mistake -- takes the reply meant for
    the next call. Running out raises rather than returning None, so a worker
    that asked one more time than the script expected fails here instead of
    looping to its step limit.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.prompts = []

    def __call__(self, messages, on_event=None, model=None, max_tokens=None,
                 quiet=False):
        assert quiet is True, "a background agent must never be allowed to print"
        self.calls.append(list(messages))
        self.prompts.append(messages[0]["content"])
        assert self.replies, "the worker asked for more replies than were scripted"
        return self.replies.pop(0)


def act(action, context=None, **keys):
    """One action, exactly as the loop would run it."""
    keys["action"] = action
    return str(agent_actions.execute_action(keys, context))


def parked_runner(gate):
    """A worker body that waits until the test lets it go.

    Used wherever a test needs an agent that is genuinely still running. The
    gate is always set in a `finally`, so a failed assertion cannot leave a
    thread parked for the rest of the suite.
    """
    def run(record, manager):
        gate.wait(5.0)
        return "parked agent %s finished" % record.id
    return run


# --- the two prompts --------------------------------------------------------

def test_a_worker_gets_the_worker_prompt_and_not_the_main_one():
    """They share their rule constants and must not share their ending. The
    main prompt's whole closing argument -- summarise what you made, for the
    user -- describes a channel a worker does not have."""
    box = Project(files=PROJECT)
    try:
        worker = agent_subprompts.worker_prompt()
        main = agent_prompt.get_system_prompt()
        assert worker != main

        # The reused halves are really reused, not paraphrased.
        for heading in ("=== OUTPUT FORMAT - ABSOLUTE RULES ===",
                        "=== ACTIONS - REQUIRED KEYS AND TWO EXAMPLES EACH ===",
                        "=== EDITING PREFERENCES - FOLLOW IN THIS ORDER ===",
                        "=== CHOOSING A TOOL - ALWAYS TAKE THE NARROWEST ONE ==="):
            assert heading in worker, heading
            assert heading in main, heading

        # And the user-facing ending is not.
        for gone in ("=== HOW TO ANSWER - WORKED EXAMPLES ===",
                     "=== BEHAVIOUR ===",
                     "YOU MUST FINISH BY SUMMARISING WHAT YOU MADE",
                     "=== PROGRESS, EVENTS AND NEXT STEP"):
            assert gone in main, gone
            assert gone not in worker, gone

        # The rules it does inherit that would be wrong are overridden by
        # number, rather than left to contradict the section above them.
        assert "=== WHERE THE SHARED RULES DIFFER FOR YOU ===" in worker
        assert "OUTPUT FORMAT rules 10 and 11" in worker
        assert "OUTPUT FORMAT rule 5" in worker
    finally:
        box.close()


def test_a_worker_is_told_it_cannot_talk_to_anyone_and_must_do_real_work():
    """Every clause the design asks for, asserted one at a time. A prompt is
    the only place several of these can live -- the loop cannot enforce "do
    real work" -- so a clause quietly dropped in an edit would be invisible."""
    box = Project(files=PROJECT)
    try:
        worker = agent_subprompts.worker_prompt()
        assert "background executor" in worker
        assert "You have no user." in worker
        assert "You cannot talk to the user" in worker
        assert "Do real work." in worker
        assert "Describing what you intend to do is not doing it" in worker
        assert "Emit no progress and no commentary." in worker
        assert "Do not push to git." in worker
        assert "git_status, git_diff and git_identity are yours" in worker
        assert "Stay inside the workspace." in worker
        assert "Finish with exactly one internal_response" in worker
    finally:
        box.close()


def test_a_worker_is_told_plainly_that_it_cannot_verify_the_test_suite():
    """run_file gives up at 10 seconds and the suite needs about 60, so a
    worker asked to verify tests gets a timeout. The danger is not the
    timeout, it is a worker reporting a pass it never saw -- the main agent
    commits on that."""
    box = Project(files=PROJECT)
    try:
        worker = agent_subprompts.worker_prompt()
        assert "YOU CANNOT RUN THE TEST SUITE." in worker
        assert "10 seconds" in worker and "about 60" in worker
        assert "do not report a pass or a failure you did not see" in worker
        assert "say what you did instead" in worker
        assert "A fabricated green run" in worker
        # And it is shown as well as stated: one worked example is a worker
        # that was asked to confirm the suite and said it could not.
        assert "I could not confirm the suite passes" in worker
    finally:
        box.close()


def test_the_worker_prompt_carries_the_shape_of_the_workspace_and_no_contents():
    """The main prompt inlines file contents; the worker prompt inlines none.
    A worker that had been handed a snapshot would multiply the largest single
    cost in a request by five, once per worker, on every step."""
    box = Project(files=PROJECT)
    try:
        box.write("secret_marker.py", "SENTINEL_STRING_NOT_IN_ANY_TREE = 1\n")
        agent_subprompts.invalidate_subprompts()
        worker = agent_subprompts.worker_prompt()

        assert "=== THE SHAPE OF THE WORKSPACE ===" in worker
        assert "=== CURRENT WORKSPACE FILES AND CONTENTS ===" not in worker
        # The path is there. What is inside the file is not.
        assert "secret_marker.py" in worker
        assert "SENTINEL_STRING_NOT_IN_ANY_TREE" not in worker
        assert "MAX_RETRIES" not in worker

        # The main prompt, in the same workspace, does inline it -- which is
        # what makes this a difference between the two rather than a fact
        # about the snapshot being empty here.
        agent_prompt.invalidate_prompt()
        assert "SENTINEL_STRING_NOT_IN_ANY_TREE" in agent_prompt.get_system_prompt()

        # A tree can stop before it has shown everything, and it says so. The
        # worker is told to look again rather than to conclude a file is gone,
        # which is the failure this substitution could otherwise cause.
        assert "never a file that does not exist" in worker
    finally:
        box.close()


def test_the_note_prompt_offers_exactly_the_verbs_the_dispatcher_allows():
    """The prompt lists them and `agent_worker.NOTE_ACTIONS` enforces them.
    Two lists, one truth: a verb added to the whitelist and not to the prompt
    is never used, and one added to the prompt and not the whitelist is
    offered and then refused."""
    box = Project(files=PROJECT)
    try:
        assert set(agent_subprompts.NOTE_VERBS) == set(agent_worker.NOTE_ACTIONS)
        note = agent_subprompts.note_prompt()
        for verb in agent_subprompts.NOTE_VERBS:
            if verb == "internal_response":
                continue
            assert verb in note, verb
        # And nothing that writes is offered anywhere in the rules it is given.
        assert "You may not create, edit, patch, append, replace, delete" in note
        assert "read-only" in note.lower()
        assert "Finish with exactly one internal_response" in note
        # Its answer, unlike a worker's, IS shown to the person who asked.
        assert "shown to the person who asked" in note
    finally:
        box.close()


def test_every_example_in_both_background_prompts_is_valid_and_a_real_action():
    """The worked examples are the teaching device: a model that reads nothing
    else copies the nearest one. An example that broke a rule would teach
    breaking it, which is what the equivalent test over the main prompt
    exists to stop."""
    box = Project(files=PROJECT)
    try:
        seen = 0
        for prompt in (agent_subprompts.worker_prompt(),
                       agent_subprompts.note_prompt()):
            for line in prompt.splitlines():
                line = line.strip()
                for marker in ("You emit:", "BAD:", "GOOD:"):
                    if line.startswith(marker):
                        line = line[len(marker):].strip()
                if not line.startswith("{"):
                    continue
                # Several BAD lines carry a parenthetical after the object
                # saying why they are bad. The object still has to be a real
                # one -- these are wrong because they are refused, not because
                # they are malformed -- so the aside is cut and the JSON is
                # judged on its own, exactly as WORKFLOW_RULES' WRONG lines are.
                line = line[:line.rfind("}") + 1]
                seen += 1
                obj = json.loads(line)   # an unparseable example is a broken one
                for entry in obj.get("actions", [obj]):
                    assert agent_prompt.validate_action(entry) is None, line
        assert seen >= 20, seen
    finally:
        box.close()


def test_neither_background_prompt_teaches_a_worker_to_delegate():
    """The delegation verbs live in their own constant precisely so that the
    background prompts, which reuse ACTION_REFERENCE verbatim, do not inherit
    them. A worker that had learned spawn_agent would delegate its own work,
    and the five-worker cap and the flat shape of the system both depend on it
    not doing that."""
    box = Project(files=PROJECT)
    try:
        for prompt in (agent_subprompts.worker_prompt(),
                       agent_subprompts.note_prompt()):
            for verb in ("spawn_agent", "wait_for_agents", "wait_for_agent",
                         "kill_agent", "agent_status", "agent_result"):
                assert verb not in prompt, verb
        main = agent_prompt.get_system_prompt()
        for verb in ("spawn_agent", "wait_for_agents", "kill_agent"):
            assert verb in main, verb
    finally:
        box.close()


def test_the_main_prompt_never_documents_the_verb_a_worker_ends_on():
    """This is half of the isolation and the half that lives in a prompt. The
    other half is in the loop and is asserted below."""
    box = Project(files=PROJECT)
    try:
        assert "internal_response" not in agent_prompt.get_system_prompt()
        assert "internal_response" not in agent_prompt.ACTION_REFERENCE
        assert "internal_response" not in agent_prompt.ORCHESTRATION_REFERENCE
        assert "internal_response" in agent_subprompts.worker_prompt()
        assert "internal_response" in agent_subprompts.note_prompt()
    finally:
        box.close()


def test_both_prompts_are_cached_and_are_dropped_when_the_workspace_changes():
    box = Project(files=PROJECT)
    try:
        first = agent_subprompts.worker_prompt()
        assert agent_subprompts.worker_prompt() is first, "not cached"
        note_first = agent_subprompts.note_prompt()
        assert agent_subprompts.note_prompt() is note_first, "not cached"

        box.write("brand_new_module.py", "x = 1\n")
        # The worker loop calls agent_prompt.invalidate_prompt() after every
        # action; that has to reach these caches too, or a second worker
        # spawned after the first one edited the tree gets the old shape.
        agent_prompt.invalidate_prompt()
        rebuilt = agent_subprompts.worker_prompt()
        assert rebuilt is not first
        assert "brand_new_module.py" in rebuilt
        assert "brand_new_module.py" in agent_subprompts.note_prompt()
    finally:
        box.close()


# --- registration -----------------------------------------------------------

def test_every_orchestration_action_declares_the_keys_it_needs():
    """The schema the model is validated against. A required key missing from
    REQUIRED_KEYS means a malformed action reaches the branch instead of being
    handed back for correction."""
    expected = {
        "spawn_agent": ["task"], "agent_status": [], "agent_result": ["id"],
        "wait_for_agent": ["id"], "wait_for_agents": [], "kill_agent": ["id"],
        "internal_response": ["response"],
    }
    for action, keys in expected.items():
        assert action in agent_config.REQUIRED_KEYS, action
        assert agent_config.REQUIRED_KEYS[action] == keys, action
        assert action in agent_actions.ACTION_LABELS, action
        assert agent_prompt.validate_action(
            dict({key: "x" for key in keys}, action=action)) is None, action


def test_every_orchestration_action_is_registered_and_reachable():
    """Through execute_action, which is the only path the model can take."""
    box = Project(files=PROJECT)
    manager = agent_manager.AgentManager()
    gate = threading.Event()
    try:
        record = manager.spawn("hold still")
        manager.start(record, parked_runner(gate))
        context = {"push_authorized": False, "manager": manager}
        calls = {
            "spawn_agent": {"task": "do something"},
            "agent_status": {},
            "agent_result": {"id": record.id},
            "wait_for_agent": {"id": record.id, "timeout": 0.05},
            "wait_for_agents": {"timeout": 0.05},
            "kill_agent": {"id": record.id},
            "internal_response": {"response": "the report"},
        }
        for action, keys in calls.items():
            if action == "spawn_agent":
                # It would start a real worker, which would reach a real
                # model. The branch is exercised through its capacity and
                # unavailable paths elsewhere; here it only has to be
                # dispatched rather than fall through to "Unknown action".
                keys = dict(keys)
            result = act(action, context, **keys)
            assert "Unknown action" not in result, (action, result)
            assert result.strip(), action
    finally:
        gate.set()
        manager.kill_all()
        box.close()


def test_internal_response_is_registered_and_is_not_terminal_for_the_main_loop():
    """The load-bearing half of the isolation. The main loop ends a turn on
    `done` and `respond`; a main model that somehow emitted an
    internal_response gets an ordinary action result and the turn carries on,
    so a worker's private report can never become a user's answer.

    Asserted against TMT.py's own source, because the property being claimed
    is about that tuple and nothing else."""
    assert "internal_response" not in ("done", "respond")
    assert act("internal_response", {}, response="a worker's report") == \
        "a worker's report"
    assert act("internal_response", {}) == ""

    source = (Path(agent_actions.__file__).resolve().parent / "TMT.py").read_text(
        encoding="utf-8")
    assert 'action in ("done", "respond")' in source, \
        "the loop's terminal check is not the tuple this test is about"
    assert 'sub_action in ("done", "respond")' in source
    # And the tuple has not quietly grown a third member.
    assert "internal_response" not in source, \
        "TMT.py names internal_response; the isolation is no longer structural"


def test_an_action_with_no_manager_in_the_context_says_so_and_changes_nothing():
    """A background agent's context has no `manager` key AT ALL -- not a None
    under it -- and the main loop's may not have one either. Every branch has
    to answer that in words: an AttributeError on None would end the run
    instead of being something the model could work around."""
    record = agent_manager.AgentRecord("1", 1, "worker", "a task")
    worker_context = agent_worker._context(record)
    assert "manager" not in worker_context, worker_context

    for context in ({}, None, worker_context, {"push_authorized": True}):
        for action, keys in (("spawn_agent", {"task": "delegate this"}),
                             ("agent_status", {}),
                             ("agent_result", {"id": "1"}),
                             ("wait_for_agent", {"id": "1"}),
                             ("wait_for_agents", {}),
                             ("kill_agent", {"id": "1"})):
            result = act(action, context, **keys)
            assert "not available" in result, (action, result)
            assert action in result, (action, result)
            assert "Traceback" not in result


# --- delegation, driven the way the model drives it -------------------------

def test_spawning_past_the_cap_comes_back_as_a_sentence_not_as_silence():
    """A bare failure is something a model retries forever. The refusal has to
    name the cap and say what to do about it."""
    box = Project(files=PROJECT)
    manager = agent_manager.AgentManager(max_workers=2)
    gate = threading.Event()
    context = {"manager": manager}
    try:
        for _ in range(2):
            record = manager.spawn("hold still")
            manager.start(record, parked_runner(gate))
        assert manager.active_count() == 2

        refused = act("spawn_agent", context, task="one too many")
        assert "maximum of 2" in refused, refused
        assert "wait_for_agents" in refused and "kill_agent" in refused, refused
        assert manager.active_count() == 2, "a refused spawn still made an agent"
        assert len(manager.list()) == 2, "a refused spawn still made a record"
    finally:
        gate.set()
        manager.kill_all()
        box.close()


def test_spawn_agent_refuses_a_task_that_is_not_a_self_contained_instruction():
    manager = agent_manager.AgentManager()
    context = {"manager": manager}
    for empty in ("", "   ", None):
        result = act("spawn_agent", context, task=empty)
        assert "needs a 'task'" in result, result
    assert len(manager.list()) == 0


def test_waiting_gives_each_finished_report_verbatim_and_names_the_rest():
    """The report is quoted, not summarised. It is the whole of what a worker
    produced, and a paraphrase here would be TMT restating a report it did not
    write."""
    manager = agent_manager.AgentManager()
    gate = threading.Event()
    context = {"manager": manager}
    try:
        done = manager.spawn("finish quickly")
        manager.start(done, lambda rec, mgr: "I rewrote src/net.py: timeout is now 30.")
        slow = manager.spawn("take your time")
        manager.start(slow, parked_runner(gate))

        assert manager.wait([done.id], timeout=2.0), "the quick agent never finished"
        report = act("wait_for_agents", context, timeout=0.05)
        assert "I rewrote src/net.py: timeout is now 30." in report, report
        assert "#%s" % done.id in report
        assert "Still running after the wait" in report, report
        assert "#%s" % slow.id in report

        # One agent, by id, is the same story with one line in it.
        single = act("wait_for_agent", context, id=done.id, timeout=0.05)
        assert "timeout is now 30" in single, single
        assert "Still running" not in single, single
    finally:
        gate.set()
        manager.kill_all()


def test_waiting_returns_still_running_rather_than_hanging_the_session():
    """The timeout is the promise that a session cannot be held forever by a
    worker stuck on a socket that will never answer."""
    manager = agent_manager.AgentManager()
    gate = threading.Event()
    context = {"manager": manager}
    try:
        record = manager.spawn("never finishes in time")
        manager.start(record, parked_runner(gate))
        report = act("wait_for_agent", context, id=record.id, timeout=0.05)
        assert "Still running after the wait" in report, report
        assert "their work is not lost" in report.lower(), report
        assert not record.is_terminal()
    finally:
        gate.set()
        manager.kill_all()


def test_a_wait_names_a_file_that_two_agents_both_wrote():
    """The whole of the concurrent-write story: there is no lock manager and
    no transaction, and this is the fact the main agent cannot work out for
    itself and does need in order to go and look."""
    manager = agent_manager.AgentManager()
    context = {"manager": manager}
    first = manager.spawn("edit the ui")
    second = manager.spawn("edit the ui differently")
    manager.note_paths(first.id, ("agent_ui.py", "agent_menu.py"))
    manager.note_paths(second.id, ("agent_ui.py",))
    manager.complete(first.id, "first done")
    manager.complete(second.id, "second done")

    report = act("wait_for_agents", context, timeout=0.05)
    assert "Two or more agents wrote the same file" in report, report
    assert "agent_ui.py" in report and "#%s" % first.id in report
    # A file only one of them touched is not a clash and is not reported as one.
    assert "agent_menu.py -- agents" not in report, report


def test_status_and_result_report_only_what_the_record_actually_holds():
    """Including the `~` on a figure no provider reported. An unmarked
    estimate is a number TMT guessed and then stated as fact."""
    manager = agent_manager.AgentManager()
    context = {"manager": manager}
    record = manager.spawn("do the thing")
    manager.set_activity(record.id, "Reading src/net.py")
    manager.set_tokens(record.id, tokens_in=1200, tokens_out=340,
                       output_exact=True)

    listing = act("agent_status", context)
    assert "#%s" % record.id in listing
    assert "Reading src/net.py" in listing
    assert "~1200 tokens in" in listing, listing      # nobody reported it
    assert "340 out" in listing and "~340 out" not in listing, listing

    # A result that does not exist yet says it does not exist yet.
    pending = act("agent_result", context, id=record.id)
    assert "has not finished" in pending, pending

    manager.complete(record.id, "It was already 30 seconds.")
    assert "It was already 30 seconds." in act("agent_result", context,
                                               id=record.id)
    # An id nobody has is a sentence, not a crash.
    for action, keys in (("agent_status", {"id": "99"}),
                         ("agent_result", {"id": "99"}),
                         ("wait_for_agent", {"id": "99"}),
                         ("kill_agent", {"id": "99"})):
        assert "no background agent" in act(action, context, **keys).lower()


def test_killing_an_agent_claims_exactly_what_it_can_guarantee():
    """A thread cannot be terminated and a stream has no abort, so "killed"
    cannot mean "stopped instantly". What is enforceable is that no further
    action runs, and that is what the sentence says."""
    manager = agent_manager.AgentManager()
    gate = threading.Event()
    context = {"manager": manager}
    try:
        record = manager.spawn("keep going")
        manager.start(record, parked_runner(gate))
        manager.note_paths(record.id, ("src/net.py",))

        killed = act("kill_agent", context, id=record.id)
        assert "Stopped background agent #%s" % record.id in killed, killed
        assert "run no further action" in killed, killed
        assert "may still complete" in killed, killed
        assert "src/net.py" in killed, killed
        assert record.status == agent_manager.Status.KILLED
        assert record.cancel.is_set()

        again = act("kill_agent", context, id=record.id)
        assert "already killed" in again, again
    finally:
        gate.set()


def test_an_id_the_model_wrote_as_a_number_still_addresses_the_agent():
    """Models write 2 as often as they write "2", and an id that only worked
    quoted would fail as a silent "no such agent"."""
    manager = agent_manager.AgentManager()
    context = {"manager": manager}
    record = manager.spawn("a task")
    manager.complete(record.id, "reported")
    assert "reported" in act("agent_result", context, id=int(record.id))


# --- a worker, end to end, with the model injected --------------------------

def test_a_worker_runs_on_the_worker_prompt_uses_tools_and_ends_on_one_verb():
    """The whole path: the prompt it is handed, real tools through
    execute_action, and exactly one internal_response as the ending."""
    box = Project(files=PROJECT)
    manager = agent_manager.AgentManager()
    try:
        script = Script(
            json.dumps({"action": "read_lines", "path": "src/net.py",
                        "start": 1, "end": 5}),
            json.dumps({"action": "patch_file", "path": "src/net.py",
                        "search": "timeout = 5", "replace": "timeout = 30"}),
            json.dumps({"action": "internal_response",
                        "response": "src/net.py now reads timeout = 30."}),
        )
        # Captured before the run: the worker patches a file, which drops both
        # prompt caches, so the same call afterwards would rebuild a tree with
        # different sizes in it and compare unequal for the wrong reason.
        expected_worker = agent_subprompts.worker_prompt()
        expected_main = agent_prompt.get_system_prompt()

        record = manager.spawn("raise the socket timeout to 30 seconds")
        answer = agent_worker.run_worker(record, manager, ask=script)

        assert answer == "src/net.py now reads timeout = 30."
        assert box.read("src/net.py") == "timeout = 30\n", "the tool never ran"
        # The prompt it was actually handed, on every call, is the worker one.
        assert script.prompts, "the model was never asked"
        for prompt in script.prompts:
            assert prompt == expected_worker
            assert prompt != expected_main
        # The paths it wrote are recorded, which is what conflicts() reads.
        assert record.paths == ("src/net.py",), record.paths
    finally:
        manager.kill_all()
        box.close()


def test_a_worker_that_reaches_for_a_user_facing_ending_is_refused_not_obeyed():
    """respond and done end a turn for a user, and a worker has no user. The
    refusal is a check on the action name before dispatch, so it holds
    whatever the prompt said and whatever the model believes."""
    box = Project(files=PROJECT)
    manager = agent_manager.AgentManager()
    try:
        script = Script(
            json.dumps({"action": "respond", "message": "All done!"}),
            json.dumps({"action": "done"}),
            json.dumps({"action": "announce", "message": "I'll start now."}),
            json.dumps({"action": "internal_response", "response": "Nothing to do."}),
        )
        record = manager.spawn("a small task")
        assert agent_worker.run_worker(record, manager, ask=script) == "Nothing to do."

        # Each refusal was handed back as a correction, in the model's own
        # conversation, rather than ending the run.
        fed_back = "\n".join(
            message["content"] for message in record.conversation
            if message["role"] == "user")
        assert "REFUSED: 'respond'" in fed_back, fed_back
        assert "REFUSED: 'done'" in fed_back, fed_back
        assert "no user to answer" in fed_back
        assert "nobody sees your messages" in fed_back or \
            "shown to anyone" in fed_back, fed_back
    finally:
        manager.kill_all()
        box.close()


class FakeGit:
    """A stand-in for agent_git, so no test here can reach a real remote.

    `agent_actions._run_git` imports agent_git at call time, which is what
    makes this possible without touching that module: putting a fake in
    sys.modules is enough, and it is removed again in a finally.
    """

    class GitError(Exception):
        pass

    class TMTGit:
        pushed = []

        @classmethod
        def discover(cls):
            return cls()

        def push(self, branch=None, remote=None):
            FakeGit.TMTGit.pushed.append((branch, remote))
            return {"branch": branch or "main", "remote": remote or "origin",
                    "remote_url_host": "example.invalid", "summary": "up to date"}


def test_a_worker_cannot_push_while_the_main_agent_still_can():
    """Two independent gates, and both are asserted. The whitelist refuses the
    verb before dispatch; the missing push_authorized in a worker's context
    blocks it again behind that. Belt and braces, and the belt is the part the
    user's safety rests on."""
    box = Project(files=PROJECT)
    manager = agent_manager.AgentManager()
    previous = sys.modules.get("agent_git")
    sys.modules["agent_git"] = FakeGit
    FakeGit.TMTGit.pushed = []
    try:
        # 1. The whitelist: the worker never reaches the dispatcher at all.
        script = Script(
            json.dumps({"action": "git_push"}),
            json.dumps({"action": "internal_response",
                        "response": "I could not push; it is refused to me."}),
        )
        record = manager.spawn("commit and push the timeout fix")
        agent_worker.run_worker(record, manager, ask=script)
        assert "git_push" in agent_worker.WORKER_FORBIDDEN
        assert FakeGit.TMTGit.pushed == [], "a worker reached the remote"

        # 2. The context: even dispatched directly, a worker's authority is
        # not enough.
        blocked = act("git_push", agent_worker._context(record))
        assert blocked == agent_actions.PUSH_BLOCKED, blocked
        assert FakeGit.TMTGit.pushed == [], "a worker reached the remote"

        # 3. The main agent, with the user's own words behind it, still pushes.
        pushed = act("git_push", {"push_authorized": True})
        assert "example.invalid" in pushed, pushed
        assert FakeGit.TMTGit.pushed == [(None, None)], FakeGit.TMTGit.pushed
    finally:
        if previous is None:
            sys.modules.pop("agent_git", None)
        else:
            sys.modules["agent_git"] = previous
        manager.kill_all()
        box.close()


def test_a_worker_aimed_outside_the_workspace_changes_nothing():
    """Path validation is not relaxed for a background agent. safe_path
    refuses an escape for a worker exactly as it does for the main agent, and
    the refusal comes back as a correction rather than as a crash."""
    box = Project(files=PROJECT)
    manager = agent_manager.AgentManager()
    outside = Path(tempfile.mkdtemp(prefix="tmt_outside_")).resolve()
    victim = outside / "untouched.txt"
    victim.write_text("original contents\n", encoding="utf-8")
    try:
        escape = str(victim).replace("\\", "/")
        script = Script(
            json.dumps({"action": "write_file", "path": escape,
                        "content": "OVERWRITTEN"}),
            json.dumps({"action": "write_file", "path": "../../escape.txt",
                        "content": "OVERWRITTEN"}),
            json.dumps({"action": "internal_response",
                        "response": "Both paths were refused; I changed nothing."}),
        )
        record = manager.spawn("write to that file")
        agent_worker.run_worker(record, manager, ask=script)

        assert victim.read_text(encoding="utf-8") == "original contents\n"
        assert not (outside / "escape.txt").exists()
        assert not (box.path.parent / "escape.txt").exists()
        # And it really tried, twice. A file left alone because the worker
        # never reached the action would pass this test for the wrong reason,
        # which is exactly the failure mode this suite keeps finding.
        refusals = [message["content"] for message in record.conversation
                    if message["role"] == "user"
                    and "Blocked unsafe path" in message["content"]]
        assert len(refusals) == 2, record.conversation
    finally:
        manager.kill_all()
        remove_tree(outside)
        box.close()


def test_a_worker_cannot_delegate_because_its_context_carries_no_register():
    """Not by being untaught -- that is the prompt's half -- but because there
    is nothing in its context to spawn with. A worker that guessed the verb
    still gets a sentence and changes nothing."""
    box = Project(files=PROJECT)
    manager = agent_manager.AgentManager()
    try:
        script = Script(
            json.dumps({"action": "spawn_agent", "task": "do half of this"}),
            json.dumps({"action": "internal_response",
                        "response": "I did it myself; I cannot delegate."}),
        )
        record = manager.spawn("a task with two halves")
        agent_worker.run_worker(record, manager, ask=script)
        assert len(manager.list()) == 1, "a worker started a worker"
        fed_back = "\n".join(message["content"] for message in record.conversation
                             if message["role"] == "user")
        assert "not available" in fed_back, fed_back
    finally:
        manager.kill_all()
        box.close()


# --- the note agent ---------------------------------------------------------

def test_the_note_agent_can_read_and_search_but_cannot_change_anything():
    """Read-only is enforced by a whitelist checked before dispatch, never by
    a blacklist: a blacklist silently admits every action added after it was
    written."""
    box = Project(files=PROJECT)
    manager = agent_manager.AgentManager()
    try:
        script = Script(
            json.dumps({"action": "find_text", "query": "MAX_RETRIES"}),
            json.dumps({"action": "read_lines", "path": "src/calc.py",
                        "start": 1, "end": 3}),
            json.dumps({"action": "write_file", "path": "src/calc.py",
                        "content": "WIPED"}),
            json.dumps({"action": "delete_file", "path": "src/net.py"}),
            json.dumps({"action": "git_push"}),
            json.dumps({"action": "internal_response",
                        "response": "MAX_RETRIES = 3, at src/calc.py line 1."}),
        )
        record = manager.spawn("where is the retry limit set?", kind="note")
        answer = agent_worker.run_note(record, manager, ask=script)

        assert answer == "MAX_RETRIES = 3, at src/calc.py line 1."
        # The reads worked.
        results = "\n".join(message["content"] for message in record.conversation
                            if message["role"] == "user")
        assert "MAX_RETRIES" in results
        # The writes did not, and the files are exactly as they were.
        assert box.read("src/calc.py") == PROJECT["src/calc.py"]
        assert (box.path / "src/net.py").exists()
        for verb in ("write_file", "delete_file", "git_push"):
            assert "REFUSED: '%s'" % verb in results, verb
        for verb in ("write_file", "delete_file"):
            assert verb not in agent_worker.NOTE_ACTIONS, verb
    finally:
        manager.kill_all()
        box.close()


def test_a_note_returns_exactly_one_response_and_the_answer_reaches_the_terminal():
    """It renders through the ordinary permanent surface -- a Result, drawn
    once into scrollback -- and not as a card, not as a stream, not as a
    panel."""
    box = Project(files=PROJECT)
    manager = agent_manager.AgentManager()
    previous = agent_worker.run_note
    answers = []

    def fake_note(record, mgr, **kwargs):
        answers.append(record.task)
        return "MAX_RETRIES = 3, set at src/calc.py line 1.\nNothing else reads it."

    agent_worker.run_note = fake_note
    try:
        result = agent_commands.run_note("where is the retry limit set?",
                                         manager=manager, timeout=5.0)
        assert result.ok, result.text()
        assert result.title == "Note"
        assert answers == ["where is the retry limit set?"], answers
        text = result.text()
        assert "MAX_RETRIES = 3, set at src/calc.py line 1." in text
        assert "Nothing else reads it." in text
        assert "Nothing in the workspace was changed." in text
        # Exactly one note record, and it is not a worker.
        note = manager.note()
        assert note is not None and note.kind == "note"
        assert note.status == agent_manager.Status.COMPLETED
        assert len(manager.list()) == 0, "the note was counted as a worker"
    finally:
        agent_worker.run_note = previous
        box.close()


def test_a_note_does_not_disturb_the_main_agent_or_the_workers():
    """It runs beside them: it does not count against the cap, it is not in
    the worker list, and nothing about a running worker changes while it goes."""
    box = Project(files=PROJECT)
    manager = agent_manager.AgentManager(max_workers=2)
    gate = threading.Event()
    previous = agent_worker.run_note
    agent_worker.run_note = lambda record, mgr, **kwargs: "two modules, six tests"
    try:
        busy = []
        for _ in range(2):
            record = manager.spawn("hold still")
            manager.start(record, parked_runner(gate))
            busy.append(record)
        assert manager.active_count() == 2, "the cap is already full"

        result = agent_commands.run_note("how many modules are there?",
                                         manager=manager, timeout=5.0)
        assert result.ok, result.text()
        assert "two modules, six tests" in result.text()

        # The cap was full and the note ran anyway, which is the point of it
        # not counting.
        assert manager.active_count() == 2
        assert [record.id for record in manager.list()] == \
            [record.id for record in busy]
        for record in busy:
            assert record.status == agent_manager.Status.RUNNING
            assert not record.cancel.is_set()
    finally:
        gate.set()
        agent_worker.run_note = previous
        manager.kill_all()
        box.close()


def test_a_note_that_does_not_answer_in_time_gives_the_prompt_back():
    """Somebody is sitting waiting for this one, so it cannot block forever
    and it must not claim an answer it does not have."""
    box = Project(files=PROJECT)
    manager = agent_manager.AgentManager()
    gate = threading.Event()
    previous = agent_worker.run_note
    agent_worker.run_note = lambda record, mgr, **kwargs: (gate.wait(5.0), "late")[1]
    try:
        result = agent_commands.run_note("a slow question", manager=manager,
                                         timeout=0.05)
        assert not result.ok
        assert "still running" in result.title.lower(), result.title
        assert "Nothing was changed" in result.text()
    finally:
        gate.set()
        agent_worker.run_note = previous
        manager.kill_note()
        box.close()


def test_note_is_driven_the_way_a_piped_run_drives_it_one_task_per_line():
    """The piped reader takes ONE TASK PER LINE, so a command that could only
    be reached by prompting twice would be unreachable from a pipe and from
    this suite. The inline form is the primary one and it is what is tested."""
    box = Project(files=PROJECT)
    previous = agent_worker.run_note
    agent_worker.run_note = lambda record, mgr, **kwargs: \
        "The prompt box lives in agent_menu.py."
    try:
        assert agent_commands.parse("/note which module owns the prompt box?") == \
            ("note", "which module owns the prompt box?")
        assert "note" in agent_commands._TAKES_ARGUMENT

        result = agent_commands.dispatch("/note which module owns the prompt box?")
        assert result is not None and result.ok, result
        assert "agent_menu.py" in result.text()
        # It did not ask for anything else: an inline note is finished.
        assert result.prompt_for == "", result.prompt_for
    finally:
        agent_worker.run_note = previous
        box.close()


def test_bare_note_asks_for_the_question_instead_of_guessing_one():
    """The interactive convenience, and only that. It starts no agent."""
    result = agent_commands.dispatch("/note")
    assert result is not None and result.ok
    assert result.prompt_for == "note", result.prompt_for
    assert "/note <question about this workspace>" in result.text()
    # The default is empty, so nothing that existed before this key asks for
    # a second prompt by accident.
    assert agent_commands.Result("x").prompt_for == ""
    assert agent_commands.dispatch("/config").prompt_for == ""


def test_note_is_a_command_with_a_summary_and_a_usage_line_like_the_others():
    """The completion list and the refusal messages read from these tables; a
    command in one and not the others offers a blank row or crashes a
    refusal."""
    assert "note" in agent_commands.names()
    assert agent_commands.SUMMARY["note"]
    assert agent_commands.USAGE["note"].startswith("/note")
    assert [name for name, _ in agent_commands.completions("/n")] == ["/note"]
    assert agent_commands.suggestion("/n") == "ote"


def test_a_note_run_without_a_register_makes_its_own_and_still_answers():
    """/note has to work in a session that never wired background agents in at
    all -- a piped run, or this suite -- so a missing manager is a manager it
    creates, not a refusal."""
    box = Project(files=PROJECT)
    previous = agent_worker.run_note
    agent_worker.run_note = lambda record, mgr, **kwargs: "six modules."
    try:
        result = agent_commands.run_note("how many modules?", timeout=5.0)
        assert result.ok, result.text()
        assert "six modules." in result.text()
    finally:
        agent_worker.run_note = previous
        box.close()


def test_an_empty_note_question_is_refused_rather_than_asked():
    result = agent_commands.run_note("   ")
    assert not result.ok
    assert "/note" in result.text()


def test_the_new_module_is_clean_utf_8_and_needs_nothing_outside_the_library():
    """A heredoc has corrupted this repository five times, once writing a NUL
    byte into a module so Python refused to import it. The rest of the suite
    guards every module; this guards the newest one."""
    text = Path(agent_subprompts.__file__).resolve().read_bytes().decode("utf-8")
    assert "\x00" not in text
    assert "import requests" not in text and "import rich" not in text


# --- the measured cost of the substitution ----------------------------------

def test_the_worker_prompt_is_smaller_than_the_main_one_in_this_repository():
    """The reason the substitution exists, asserted rather than assumed. It is
    measured against this repository, where the snapshot is the largest single
    thing in the main prompt; the numbers themselves are in CLAUDE.local.md,
    and this only asserts the direction, because an assertion on a figure that
    moves with the repository would fail on the next commit."""
    # Both caches dropped first, or one of them is still describing whichever
    # temp workspace ran last and the two figures are not about the same
    # place at all.
    agent_prompt.invalidate_prompt()
    worker = agent_session.estimate_tokens(agent_subprompts.worker_prompt())
    main = agent_session.estimate_tokens(agent_prompt.get_system_prompt())
    assert worker < main, (worker, main)
    snapshot = agent_session.estimate_tokens(agent_prompt._workspace_snapshot())
    assert snapshot > 0
