"""`agent_ci` on its own: the contract, the clock, and the words.

The module reads no terminal, starts no session and speaks to no model, so a
test needs none of those. Everything here is a flag, a `Run` driven by an
injected clock, and the object it settles into.

What is pinned, and why each is worth a test of its own:

- an approval in CI is ALWAYS refused. That is the whole CI policy and the one
  thing that must never quietly become "yes": `--ci` would then be a documented
  way, from a file anybody can open a pull request against, to run every
  command the interactive agent refuses to run unattended;
- `choose` answers None and not "", because "nobody was there" and "a person
  declined" are different facts and `agent_ask` tells the model which;
- the status is settled from what the RUN did, not from what the model said --
  a timeout beats an answer, and an exhausted budget beats a silence;
- a refusal is only terminal when the run did not finish, because a task that
  was refused one command, took another route and passed is a task that
  succeeded, and failing a green build would make CI mode unusable;
- every flag refuses a bool by name, for `agent_glob`'s reason: `int(True)` is
  1, so `--max-turns` given a flag-shaped value would silently become a
  one-round budget rather than an error.
"""

import json

import agent_ci as C


class Clock:
    """A clock the test moves by hand, so no test waits for a timeout."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def run(task="do the thing", **keys):
    keys.setdefault("workspace", "/tmp/project")
    keys.setdefault("max_turns", 10)
    keys.setdefault("timeout", 60.0)
    return C.Run(task=task, **keys)


# --- the flags ---------------------------------------------------------------

def test_the_defaults_are_used_when_nothing_is_given():
    assert C.check_turns(None) == C.DEFAULT_MAX_TURNS
    assert C.check_timeout(None) == C.DEFAULT_TIMEOUT
    assert C.DEFAULT_MAX_TURNS > 0 and C.DEFAULT_TIMEOUT > 0


def test_max_turns_takes_a_positive_whole_number_and_nothing_else():
    assert C.check_turns("25") == 25
    assert C.check_turns(3) == 3
    for bad in (0, -1, "0", "-4"):
        try:
            C.check_turns(bad)
        except C.CIError as error:
            assert "at least 1" in str(error), (bad, error)
        else:
            raise AssertionError("%r must be refused" % (bad,))
    for bad in ("abc", "", [], {}, 2.5j):
        try:
            C.check_turns(bad)
        except C.CIError as error:
            assert "whole number" in str(error), (bad, error)
        else:
            raise AssertionError("%r must be refused" % (bad,))


def test_a_bool_is_refused_by_name_rather_than_read_as_one():
    """`int(True)` is 1, so a flag-shaped value would silently become a
    one-round budget -- an answer, for an argument nobody meant as a count.
    The same trap `agent_glob` records for `context` and `limit`."""
    for check, word in ((C.check_turns, "turns"), (C.check_timeout, "seconds")):
        for bad in (True, False):
            try:
                check(bad)
            except C.CIError as error:
                assert word in str(error), (bad, error)
            else:
                raise AssertionError("%r must be refused" % (bad,))


def test_a_typo_sized_limit_is_refused_rather_than_obeyed():
    """The ceilings catch `--timeout 6000000` (somebody who meant
    milliseconds) rather than expressing a policy about long runs."""
    for value in (C.MAX_TURNS_CEILING + 1, 100000):
        try:
            C.check_turns(value)
        except C.CIError as error:
            assert "typo" in str(error), error
        else:
            raise AssertionError("%r must be refused" % (value,))
    try:
        C.check_timeout(C.MAX_TIMEOUT + 1)
    except C.CIError as error:
        assert "typo" in str(error), error
    else:
        raise AssertionError("an absurd timeout must be refused")


def test_timeout_takes_seconds_and_refuses_nothing_useful():
    assert C.check_timeout("600") == 600.0
    assert C.check_timeout(1.5) == 1.5
    for bad in (0, -1, "0"):
        try:
            C.check_timeout(bad)
        except C.CIError as error:
            assert "more than 0" in str(error), (bad, error)
        else:
            raise AssertionError("%r must be refused" % (bad,))


# --- the task ----------------------------------------------------------------

class Pipe:
    def __init__(self, text, tty=False):
        self.text = text
        self.tty = tty

    def isatty(self):
        return self.tty

    def read(self):
        return self.text


def test_the_words_after_the_flags_are_joined_into_one_task():
    """`--ci run the tests` is what somebody types the first time, and
    refusing it to insist on quotes teaches nothing."""
    assert C.read_task(["run", "the", "tests"]) == "run the tests"
    assert C.read_task(["run the tests"]) == "run the tests"
    assert C.read_task(["  run   the\ntests  "]) == "run the tests"


def test_a_task_can_be_piped_in_whole_rather_than_a_line_at_a_time():
    """WHOLE, unlike the interactive piped reader which takes one task per
    line. A task is one instruction, and this repository has already recorded
    what splitting one into four does to a repository."""
    assert C.read_task([], Pipe("fix the lint\nerrors in src/")) == \
        "fix the lint errors in src/"


def test_a_terminal_is_never_read_for_the_task():
    """The one thing CI mode must not do. A read that blocked would be a
    pipeline that hung with no output and no way to see why."""
    tty = Pipe("this must not be read", tty=True)
    try:
        C.read_task([], tty)
    except C.CIError as error:
        assert "needs a task" in str(error), error
    else:
        raise AssertionError("a tty must not be read")


def test_no_task_anywhere_is_a_usage_error_that_says_how_to_give_one():
    for stdin in (None, Pipe(""), Pipe("   \n ")):
        try:
            C.read_task([], stdin)
        except C.CIError as error:
            assert error.code == C.EXIT_USAGE
            assert "tmtcode --ci" in str(error), error
        else:
            raise AssertionError("no task must be refused")


# --- the answers nobody is there to give -------------------------------------

def test_an_approval_in_ci_is_always_refused():
    """THE WHOLE CI APPROVAL POLICY, and the one thing that must never quietly
    become yes. `agent_policy` has already decided ALLOW, ASK or DENY before
    this is called -- what reaches it is exactly the set of commands TMT would
    have put to a human, and with no human the only safe answer is no."""
    live = run()
    for question in ("Run `rm -rf build`?", "Delete src/old.py?",
                     "Run `git push --force`?", "", None):
        assert live.approve(question) == ""
        assert live.approve(question, "rm") == ""
    # Refused, and recorded, so the summary can say what was refused rather
    # than leaving a mystery in the build log.
    assert live.refusals(), "a refusal must be recorded"
    assert any("rm -rf build" in note for note in live.refusals())
    assert all("refused automatically" in note for note in live.refusals())


def test_the_same_question_twice_is_recorded_once():
    live = run()
    for _ in range(5):
        live.approve("Run `rm -rf build`?")
    assert len(live.refusals()) == 1, live.refusals()


def test_a_question_answers_nobody_is_here_rather_than_a_refusal():
    """None, not "". "" is a person who pressed Esc; None is nobody at all,
    and `agent_ask.answer` tells the model which."""
    assert run().choose("Which stack?", ("1", "2")) is None


# --- the clock ---------------------------------------------------------------

def test_the_wall_clock_runs_out_and_says_so():
    clock = Clock()
    live = run(timeout=60.0, clock=clock)
    assert not live.expired()
    assert live.remaining() == 60.0
    clock.advance(59.9)
    assert not live.expired()
    clock.advance(0.2)
    assert live.expired()
    assert live.remaining() == 0.0
    result = live.finish(answer="all done", turns=4)
    assert result.status == C.TIMEOUT
    assert result.exit_code() == C.EXIT_LIMIT
    assert not result.ok
    assert "60" in result.message


def test_a_timeout_beats_an_answer_the_model_managed_to_give():
    """The status is settled from what the RUN did. A model that answered on
    the round the clock ran out did not finish inside its budget, and a
    pipeline that was told otherwise would trust a bound that did not hold."""
    clock = Clock()
    live = run(timeout=10.0, clock=clock)
    clock.advance(11)
    assert live.finish(answer="I fixed everything").status == C.TIMEOUT


# --- how a run ends ----------------------------------------------------------

def test_an_answer_with_no_outcome_is_a_completed_run():
    live = run(clock=Clock())
    result = live.finish(answer="Fixed two tests; suite green.", turns=7)
    assert result.status == C.COMPLETED
    assert result.ok and result.exit_code() == C.EXIT_OK
    assert result.message == "Fixed two tests; suite green."
    assert result.turns == 7


def test_running_out_of_turns_is_its_own_status_and_its_own_exit_code():
    live = run(max_turns=5, clock=Clock())
    result = live.finish(outcome="it ran out of steps before answering", turns=5)
    assert result.status == C.MAX_TURNS
    assert result.exit_code() == C.EXIT_LIMIT
    assert "5 turns" in result.message
    assert "in the workspace" in result.message, \
        "a partial run must say the work is still there"


def test_any_other_outcome_is_a_failure_in_the_loop_s_own_words():
    live = run(clock=Clock())
    result = live.finish(outcome="the stream failed", turns=2)
    assert result.status == C.FAILED
    assert result.exit_code() == C.EXIT_FAILED
    assert result.message == "the stream failed"


def test_a_crash_is_an_error_status_carrying_what_broke():
    live = run(clock=Clock())
    result = live.finish(error="RuntimeError: boom")
    assert result.status == C.ERROR
    assert result.error == "RuntimeError: boom"
    assert result.exit_code() == C.EXIT_FAILED


def test_a_refusal_only_fails_the_run_when_the_run_did_not_finish():
    """A task that was refused one command, took another route and passed its
    checks SUCCEEDED. Failing a green build over a refusal the agent recovered
    from would make CI mode unusable -- and the refusal is in the summary
    either way, so nothing is hidden."""
    finished = run(clock=Clock())
    finished.approve("Run `rm -rf build`?")
    result = finished.finish(answer="Did it another way; tests pass.", turns=6)
    assert result.status == C.COMPLETED and result.ok
    assert result.blocked_reason, "the refusal must still be reported"

    stuck = run(clock=Clock())
    stuck.approve("Run `rm -rf build`?")
    blocked = stuck.finish(outcome="it ran out of steps before answering")
    assert blocked.status == C.BLOCKED
    assert blocked.exit_code() == C.EXIT_BLOCKED
    assert blocked.blocked_reason


def test_a_run_that_never_settled_reports_that_rather_than_claiming_success():
    result = run(clock=Clock()).result()
    assert not result.ok
    assert result.status == C.FAILED
    assert "without reporting" in result.message


# --- what a pipeline reads ---------------------------------------------------

def test_the_json_is_one_object_with_every_promised_field():
    clock = Clock()
    live = run(task="fix the tests", clock=clock)
    live.note_action("write_file", ["src/a.py"])
    live.note_action("write_file", ["src/a.py", "src/b.py"])
    clock.advance(12.34)
    live.finish(answer="Fixed.", turns=3,
                verify={"ran": True, "passed": True, "details": "pytest exit 0"})
    body = json.loads(live.result().to_json())
    assert body["ok"] is True
    assert body["status"] == "completed"
    assert body["task"] == "fix the tests"
    assert body["turns"] == 3
    assert body["duration_seconds"] == 12.3
    assert body["changed_files"] == ["src/a.py", "src/b.py"], body["changed_files"]
    assert body["verify"] == {"ran": True, "passed": True,
                              "details": "pytest exit 0"}
    assert body["blocked_reason"] is None and body["error"] is None
    assert set(body) == {"ok", "status", "workspace", "task", "turns",
                         "duration_seconds", "message", "changed_files",
                         "verify", "blocked_reason", "error"}


def test_the_json_stays_valid_for_every_status():
    for finish in (lambda r: r.finish(answer="done", turns=1),
                   lambda r: r.finish(outcome="the stream failed"),
                   lambda r: r.finish(error="RuntimeError: boom"),
                   lambda r: r.finish(outcome="it ran out of steps before answering")):
        live = run(clock=Clock())
        finish(live)
        body = json.loads(live.result().to_json())
        assert body["status"] in (C.COMPLETED, C.FAILED, C.ERROR, C.TIMEOUT,
                                  C.MAX_TURNS, C.BLOCKED), body["status"]
        assert isinstance(body["ok"], bool)
        assert body["ok"] == (body["status"] == C.COMPLETED)


def test_changed_files_are_the_paths_the_actions_named_deduplicated_and_sorted():
    live = run(clock=Clock())
    live.note_action("write_file", ["z.py", "a.py"])
    live.note_action("patch_file", ["a.py"])
    live.note_action("read_file", [])
    assert live.changed() == ["a.py", "z.py"]


def test_a_verification_that_never_ran_says_so_rather_than_claiming_a_pass():
    """The worst possible field to invent, in the one readout a pipeline
    branches on."""
    assert C.verify_summary(None) == {"ran": False, "passed": None,
                                      "details": None}

    class Idle:
        last = None
        passed = False

    assert C.verify_summary(Idle())["ran"] is False

    class Hostile:
        @property
        def last(self):
            raise RuntimeError("boom")

    assert C.verify_summary(Hostile())["ran"] is False


def test_a_verification_that_ran_carries_its_own_summary_line():
    class Ran:
        passed = True

        class last:
            @staticmethod
            def summary_line():
                return "3 passed, 0 failed, 0 errored, 0 skipped"

    summary = C.verify_summary(Ran())
    assert summary == {"ran": True, "passed": True,
                       "details": "3 passed, 0 failed, 0 errored, 0 skipped"}


def test_the_human_summary_states_the_same_facts_without_the_braces():
    clock = Clock()
    live = run(clock=clock)
    live.note_action("write_file", ["src/a.py"])
    clock.advance(5)
    live.finish(answer="Fixed it.", turns=2,
                verify={"ran": True, "passed": False, "details": "pytest exit 1"})
    said = live.result().human()
    assert "completed" in said and "2 turns" in said
    assert "src/a.py" in said
    assert "failed" in said and "pytest exit 1" in said
    assert "\x1b" not in said, "a build log is not a terminal"


def test_every_exit_code_is_distinct_so_a_pipeline_can_branch_on_it():
    codes = {C.EXIT_OK, C.EXIT_FAILED, C.EXIT_USAGE, C.EXIT_LIMIT,
             C.EXIT_BLOCKED}
    assert len(codes) == 5, codes
    assert C.EXIT_OK == 0, "only success may be zero"
    for status in (C.COMPLETED, C.FAILED, C.TIMEOUT, C.MAX_TURNS, C.BLOCKED,
                   C.ERROR):
        code = C.Result(status=status).exit_code()
        assert (code == 0) == (status == C.COMPLETED), (status, code)
