"""The delegation contract: what it says, what it refuses, and what it reports.

Everything here is pure. No model, no network, no thread, no terminal and no
clock that has to actually pass -- `agent_delegation` was built that way for
exactly this reason, in the division `agent_plan`, `agent_review`,
`agent_verify` and `agent_reviewbot` already use. What is tested here is the
RULE; that the rule is enforced on every path a worker can take is tested in
`test_agent_delegation_wiring.py`, against the real loop and the real
dispatcher.

Three groups earn their keep.

**The whitelist.** `READ_ONLY_ACTIONS` is asserted disjoint from
`agent_config.MUTATING_ACTIONS` and asserted to exclude the four verbs that
are mutation paths without being in that set -- `bash`, `git_commit`,
`open_app` and `remember`. Those four are the whole reason this is its own
list rather than a derivation, and a future edit that "tidied" it into one
would fail here rather than in production. `bash` took `run_file`'s place in
that four when the one guarded command tool replaced the two execution verbs;
it is the sharpest of the four, because a command can write anything and no
inspection of the command line can see it coming.

**Validation.** Every refusal comes back as a sentence and produces no
constraints at all, because a delegation started under half a contract is the
one outcome nobody can reason about.

**Immutability.** The absence of a setter is asserted by name, the way
`test_agent_capabilities` asserts the absence of one on `Capabilities`: a
method that could be handed `("read_only", False)` is a method a later edit
can wire model output into.
"""

import agent_config
import agent_delegation as D


# --- the read-only whitelist -------------------------------------------------

def test_a_read_only_delegation_may_read_search_and_inspect():
    """The verbs an investigation is made of. If any of these were refused the
    constraint would not be "read-only", it would be "do nothing"."""
    constraints = D.DelegationConstraints(read_only=True)
    for action in ("read_file", "read_lines", "list_files", "glob", "grep",
                   "find_symbol", "tree", "code_map",
                   "related_tests", "recall", "git_status", "git_diff",
                   "git_identity", "internal_response", "send_message"):
        assert D.refusal(constraints, action) == "", action


def test_a_read_only_delegation_is_refused_every_verb_that_writes_a_file():
    """The list in section 5, verb by verb. Each one is a separate assertion
    rather than a loop with one message, so a failure names the verb."""
    constraints = D.DelegationConstraints(read_only=True)
    for action in ("write_file", "append_file", "write_files", "patch_file",
                   "replace_lines", "replace_across", "copy_file",
                   "rename_file", "create_folder", "delete_file",
                   "delete_folder"):
        said = D.refusal(constraints, action)
        assert said, "%s was allowed under a read-only delegation" % action
        assert D.VIOLATION_HEADER in said, said
        assert action in said, said
        assert "blocked" in said, said


def test_the_four_mutation_paths_that_are_not_file_writes_are_refused_too():
    """The whole reason READ_ONLY_ACTIONS is its own list rather than the
    inverse of agent_config.MUTATING_ACTIONS.

    None of these four is in that set, because that set answers a different
    question -- does this invalidate the cached prompt -- and all four can make
    a persistent change: a command can write anything, a commit changes the
    repository, an app launch reaches outside the workspace entirely, and
    `remember` writes to TMT's own store.

    `bash` is the one that replaced `run_file` and `run_python` here, and it is
    a wider hole than either of them was: they ran one file by extension, and
    this runs whatever the model wrote. The two old names are asserted too --
    they are verbs TMT no longer has, and a whitelist that had kept either of
    them would let the legacy net land a translated execution verb somewhere
    permitted. That the net actually lands on `bash` is
    `test_agent_delegation_wiring`'s to prove, against the real dispatcher.
    """
    constraints = D.DelegationConstraints(read_only=True)
    for action in ("bash", "git_commit", "open_app",
                   "remember", "verify", "project_context", "git_push",
                   "run_file", "run_python"):
        assert action not in D.READ_ONLY_ACTIONS, action
        assert D.refusal(constraints, action), action


def test_the_whitelist_holds_nothing_agent_config_calls_mutating():
    """Disjoint, and asserted rather than assumed. The two sets are built for
    different questions and neither is derived from the other, so the day they
    disagree is a day somebody has to look."""
    overlap = D.READ_ONLY_ACTIONS & set(agent_config.MUTATING_ACTIONS)
    assert not overlap, "read-only would permit mutating actions: %s" % sorted(overlap)


def test_a_verb_nobody_has_written_yet_is_refused_rather_than_allowed():
    """A whitelist and not a blacklist. Every action added to TMT after this
    was written is refused by default, which is the failure that actually
    matters: the person adding the action is not the person who wrote the
    list."""
    constraints = D.DelegationConstraints(read_only=True)
    assert D.refusal(constraints, "deploy_to_production")
    assert D.refusal(constraints, "")


def test_a_delegation_that_is_not_read_only_is_refused_nothing():
    """Section 4. A contract with no read-only flag on it must not quietly
    constrain anything, or every worker spawned before this existed changes
    behaviour."""
    for constraints in (D.DEFAULT, None,
                        D.DelegationConstraints(read_only=False),
                        D.DelegationConstraints(timeout_seconds=60)):
        for action in ("write_file", "delete_file", "bash", "git_commit"):
            assert D.refusal(constraints, action) == "", (constraints, action)


def test_a_contract_that_cannot_be_read_fails_closed():
    """The one direction this guard differs from every other guard in TMT.

    `plan_block`, `review_block` and `verify_block` all swallow and return "",
    because there the worst outcome is finished work held hostage. Here the
    worst outcome is a write nobody authorised, so an object that cannot
    answer is treated as read-only.
    """
    class Broken(object):
        @property
        def read_only(self):
            raise RuntimeError("no")
    assert D.refusal(Broken(), "write_file")
    assert D.refusal(Broken(), "read_file") == "", "reading is still allowed"


def test_the_refusal_says_what_may_be_done_instead():
    """A model told only "not permitted" reasonably looks for another route to
    the same effect. This one names the reason, and names the way out."""
    said = D.refusal(D.DelegationConstraints(read_only=True), "bash")
    assert "runs a command" in said, said
    assert "internal_response" in said, said
    assert "read_file" in said, said


# --- validating what the model sent -----------------------------------------

def test_no_constraints_is_the_default_contract():
    for value in (None, {}):
        constraints, error = D.parse(value)
        assert error == "", error
        assert constraints.is_default()
        assert constraints.read_only is False
        assert constraints.timeout_seconds is None
        assert constraints.report.any() is False


def test_a_full_contract_is_read_exactly_as_written():
    constraints, error = D.parse({
        "read_only": True, "timeout_seconds": 600,
        "report": {"file_list": True, "diff": True, "summary": True}})
    assert error == "", error
    assert constraints.read_only is True
    assert constraints.timeout_seconds == 600
    assert constraints.report.names() == ("file_list", "diff", "summary")
    assert not constraints.is_default()


def test_each_report_requirement_stands_on_its_own():
    for name in ("file_list", "diff", "summary"):
        constraints, error = D.parse({"report": {name: True}})
        assert error == "", error
        assert constraints.report.names() == (name,), constraints
        assert getattr(constraints.report, name) is True
        for other in ("file_list", "diff", "summary"):
            if other != name:
                assert getattr(constraints.report, other) is False


def test_an_unknown_constraint_is_refused_rather_than_ignored():
    """Section 38. A model that wrote "timeout" and was silently given a
    delegation with none would believe it had one, which is the whole value of
    a contract gone."""
    constraints, error = D.parse({"timeout": 600})
    assert error.startswith("FAILED:"), error
    assert "timeout_seconds" in error, error
    assert constraints.is_default(), "a refused contract still produced one"

    constraints, error = D.parse({"read_only": True, "readonly": True})
    assert error and "readonly" in error, error
    assert constraints.is_default()


def test_an_unknown_report_requirement_is_refused():
    constraints, error = D.parse({"report": {"stdout": True}})
    assert error and "stdout" in error, error
    assert constraints.is_default()


def test_a_negative_or_zero_timeout_is_refused_and_says_what_to_write():
    for value in (0, -1, -600):
        constraints, error = D.parse({"timeout_seconds": value})
        assert error.startswith("FAILED:"), (value, error)
        assert "Leave it out" in error, error
        assert constraints.is_default()


def test_a_timeout_past_the_ceiling_is_refused():
    constraints, error = D.parse({"timeout_seconds": D.MAX_TIMEOUT_SECONDS + 1})
    assert error and str(D.MAX_TIMEOUT_SECONDS) in error, error
    assert constraints.is_default()
    ok, error = D.parse({"timeout_seconds": D.MAX_TIMEOUT_SECONDS})
    assert error == "" and ok.timeout_seconds == D.MAX_TIMEOUT_SECONDS


def test_a_timeout_of_the_wrong_type_is_refused_and_true_is_not_one_second():
    """`True` is an int in Python, so a bool reaching the int branch would
    become a one-second deadline -- a delegation killed before it started, from
    a key that looked accepted."""
    for value in (True, False, "600", "ten minutes", [600], {"seconds": 600},
                  600.5):
        constraints, error = D.parse({"timeout_seconds": value})
        assert error.startswith("FAILED:"), (value, error)
        assert constraints.is_default()
    # A whole float is a model computing rather than choosing, and is fine.
    ok, error = D.parse({"timeout_seconds": 600.0})
    assert error == "" and ok.timeout_seconds == 600


def test_a_flag_of_the_wrong_type_is_refused_rather_than_coerced():
    """"true" is not true. A model that wrote a string meant the flag, and
    accepting it here would mean the two sides of the contract read the same
    object differently -- which is section 38's whole concern."""
    for value in ("true", 1, 0, "yes", None if False else []):
        constraints, error = D.parse({"read_only": value})
        assert error.startswith("FAILED:"), (value, error)
        assert constraints.is_default()


def test_constraints_that_are_not_an_object_are_refused_with_an_example():
    for value in ("read_only", 600, ["read_only"], True):
        constraints, error = D.parse(value)
        assert error.startswith("FAILED:"), (value, error)
        assert "read_only" in error, error
        assert constraints.is_default()


def test_nothing_is_half_accepted():
    """One bad key and the whole contract is refused -- section 38's "do not
    partially start a worker with half-valid constraints", asserted at the
    parser rather than trusted at the caller."""
    constraints, error = D.parse({"read_only": True, "timeout_seconds": -5,
                                  "report": {"summary": True}})
    assert error, "a contract with a bad timeout was accepted"
    assert constraints is D.DEFAULT or constraints.is_default()
    assert constraints.read_only is False, "half of the contract survived"


# --- immutability and isolation ---------------------------------------------

def test_a_contract_has_no_setter_at_all():
    """Section 39, asserted by absence. A method that takes a name and a value
    is a method a later edit can wire model output into, and the model in
    question is the one the constraint exists to bound."""
    constraints, _ = D.parse({"read_only": True})
    for name in dir(constraints):
        assert not name.startswith("set_"), name
        assert name not in ("update", "adopt", "apply", "relax", "extend",
                            "allow", "grant"), name


def test_a_contracts_fields_cannot_be_assigned_to():
    """Including the PRIVATE slot under each property, which is the half a
    `__slots__` plus read-only properties leaves open. "Immutable except for
    the four names right beside the four immutable ones" is not a guarantee
    anybody can rely on."""
    constraints, _ = D.parse({"read_only": True, "timeout_seconds": 60})
    for name, value in (("read_only", False), ("_read_only", False),
                        ("timeout_seconds", 99999), ("_timeout_seconds", 0),
                        ("report", None), ("_report", None)):
        try:
            setattr(constraints, name, value)
        except (AttributeError, RuntimeError):
            continue
        raise AssertionError("%s could be reassigned to %r" % (name, value))
    for name in ("read_only", "_read_only", "timeout_seconds"):
        try:
            delattr(constraints, name)
        except (AttributeError, RuntimeError):
            continue
        raise AssertionError("%s could be deleted" % name)
    assert constraints.read_only is True
    assert constraints.timeout_seconds == 60


def test_assigning_to_a_contract_raises_something_nobody_swallows():
    """A RuntimeError rather than an AttributeError. This codebase is full of
    `getattr(x, name, default)` and broad `except Exception` readers, and an
    AttributeError is what a typo produces -- so an assignment to a contract
    would be indistinguishable from one, and could go through quietly."""
    constraints, _ = D.parse({"read_only": True})
    try:
        constraints._read_only = False
    except RuntimeError as error:
        assert "immutable" in str(error), error
    else:
        raise AssertionError("the contract was quietly reassigned")


def test_a_report_requirements_object_cannot_be_edited_either():
    report = D.ReportRequirements(summary=True)
    for name in ("summary", "_summary", "diff", "_diff", "file_list",
                 "_file_list"):
        try:
            setattr(report, name, True)
        except (AttributeError, RuntimeError):
            continue
        raise AssertionError("%s could be reassigned" % name)
    assert report.names() == ("summary",)


def test_no_new_attribute_can_be_attached_to_stand_in_for_a_field():
    """__slots__ closes the other door. Without it a later edit could set
    `constraints.read_only_override` and a reader could grow to consult it."""
    constraints, _ = D.parse({"read_only": True})
    try:
        constraints.override = True
    except (AttributeError, RuntimeError):
        return
    raise AssertionError("a new attribute could be attached to a contract")


def test_two_delegations_parsed_together_do_not_share_state():
    """Section 22. There is no module global on this path and the default is
    an immutable singleton, so even the fallback cannot carry anything between
    two delegations."""
    first, _ = D.parse({"read_only": True, "timeout_seconds": 100,
                        "report": {"file_list": True}})
    second, _ = D.parse({"read_only": False, "timeout_seconds": 500,
                         "report": {"diff": True}})
    assert first.read_only is True and second.read_only is False
    assert first.timeout_seconds == 100 and second.timeout_seconds == 500
    assert first.report.names() == ("file_list",)
    assert second.report.names() == ("diff",)
    assert first.report is not second.report


def test_ten_delegations_each_keep_their_own_contract():
    made = []
    for index in range(10):
        constraints, error = D.parse({
            "read_only": index % 2 == 0,
            "timeout_seconds": 60 + index,
            "report": {"summary": index % 3 == 0}})
        assert error == "", error
        made.append(constraints)
    for index, constraints in enumerate(made):
        assert constraints.read_only is (index % 2 == 0), index
        assert constraints.timeout_seconds == 60 + index, index
        assert constraints.report.summary is (index % 3 == 0), index


def test_the_default_contract_is_one_shared_immutable_object():
    first, _ = D.parse(None)
    second, _ = D.parse({})
    assert first is D.DEFAULT and second is D.DEFAULT
    assert first.is_default()


# --- how a contract reads ----------------------------------------------------

def test_the_contract_describes_itself_in_words_a_model_can_act_on():
    constraints, _ = D.parse({"read_only": True, "timeout_seconds": 600,
                              "report": {"file_list": True, "summary": True}})
    said = constraints.describe()
    assert "READ ONLY" in said, said
    assert "10:00" in said and "600 seconds" in said, said
    assert "file_list, summary" in said, said


def test_a_default_contract_describes_itself_as_nothing_at_all():
    """"" rather than "no constraints", because the caller uses the emptiness
    to decide whether to draw anything -- and a sentence saying there is
    nothing to say is a readout of an absence."""
    assert D.DEFAULT.describe() == ""
    assert D.DEFAULT.chips() == ()
    assert D.DEFAULT.report.chips() == ()


def test_the_chips_are_short_enough_for_a_card_and_survive_stripping():
    constraints, _ = D.parse({"read_only": True, "timeout_seconds": 90,
                              "report": {"file_list": True, "diff": True}})
    assert constraints.chips() == ("READ ONLY", "TIMEOUT 1:30")
    assert constraints.report.chips() == ("FILES", "DIFF")


def test_a_duration_reads_the_same_way_everywhere():
    assert D.clock_text(0) == "0:00"
    assert D.clock_text(59) == "0:59"
    assert D.clock_text(60) == "1:00"
    assert D.clock_text(600) == "10:00"
    assert D.clock_text(3600) == "1:00:00"
    assert D.clock_text(3661) == "1:01:01"
    # Never raises and never goes negative: every caller is drawing a
    # countdown or deciding how long to block.
    assert D.clock_text(-5) == "0:00"
    assert D.clock_text(None) == "0:00"
    assert D.clock_text("nonsense") == "0:00"


# --- violations --------------------------------------------------------------

def test_a_violation_records_the_operation_and_the_path_it_named():
    entry = D.violation("write_file", ["src/auth.py"])
    assert entry == {"type": "write_blocked", "operation": "write_file",
                     "path": "src/auth.py"}


def test_a_violation_with_no_path_carries_no_empty_one():
    """A None under "path" would read as a write to a file called None."""
    entry = D.violation("bash")
    assert "path" not in entry, entry
    assert entry["operation"] == "bash"


def test_the_violation_line_counts_and_names_without_running_away():
    assert D.violations_line(()) == ""
    one = D.violations_line([D.violation("write_file", ["a.py"])])
    assert one == "1 write operation blocked (write_file a.py)", one
    many = D.violations_line([D.violation("write_file", ["f%d.py" % i])
                              for i in range(6)])
    assert many.startswith("6 write operations blocked"), many
    assert "and 3 more" in many, many


# --- the structured result ---------------------------------------------------

def _result(**keys):
    keys.setdefault("worker_id", "3")
    keys.setdefault("status", D.COMPLETED)
    return D.DelegationResult(**keys)


def test_the_six_outcomes_are_kept_apart():
    """Section 14 and section 44. "timed out after inspecting forty files" and
    "crashed on an exception" are different failures, and a main agent told
    "failed" about the first would go looking for a bug that is not there."""
    words = set()
    for status in (D.COMPLETED, D.FAILED, D.TIMED_OUT, D.CANCELLED,
                   D.CONSTRAINT_VIOLATION, D.ERROR):
        word = _result(status=status).status_word()
        assert word not in words, "%s collapsed into another status" % status
        words.add(word)
    assert _result(status=D.TIMED_OUT).status_word() == "TIMED OUT"
    assert _result(status=D.CANCELLED).status_word() == "CANCELLED"


def test_a_report_carries_only_the_sections_its_contract_asked_for():
    constraints, _ = D.parse({"report": {"summary": True}})
    said = _result(constraints=constraints, summary="Found AuthService.",
                   inspected=("a.py",), changed=("b.py",),
                   diff="+ something").describe()
    assert "SUMMARY" in said and "Found AuthService." in said
    assert "FILES" not in said, said
    assert "DIFF" not in said, said


def test_the_file_list_counts_and_names_both_halves():
    constraints, _ = D.parse({"report": {"file_list": True}})
    said = _result(constraints=constraints,
                   inspected=("src/auth/service.py", "src/auth/token.py"),
                   changed=("src/auth/service.py",)).describe()
    assert "FILES" in said
    assert "Inspected (2)" in said, said
    assert "Changed (1)" in said, said
    assert "src/auth/token.py" in said


def test_a_file_list_with_nothing_in_it_says_none_rather_than_nothing():
    constraints, _ = D.parse({"report": {"file_list": True}})
    said = _result(constraints=constraints).describe()
    assert "Inspected: none" in said, said
    assert "Changed: none" in said, said


def test_a_very_long_file_list_says_how_many_more_there_were():
    """Section 40: the main agent gets the high-value information, not every
    tool call it ever made."""
    constraints, _ = D.parse({"report": {"file_list": True}})
    paths = tuple("f%d.py" % index for index in range(D.MAX_REPORT_PATHS + 12))
    said = _result(constraints=constraints, inspected=paths).describe()
    assert "Inspected (%d)" % len(paths) in said, said
    assert "and 12 more" in said, said
    assert said.count("f%d.py" % (len(paths) - 1)) == 0, "listed past the cap"


def test_a_read_only_delegations_diff_says_so_rather_than_no_changes():
    """Section 18 and section 45. "No workspace changes" would be true and
    would read as though the worker had been able to make some and chose not
    to."""
    constraints, _ = D.parse({"read_only": True, "report": {"diff": True}})
    said = _result(constraints=constraints).describe()
    assert "No changes permitted by delegation." in said, said


def test_a_writing_delegation_that_changed_nothing_says_no_workspace_changes():
    constraints, _ = D.parse({"report": {"diff": True}})
    assert "No workspace changes." in _result(constraints=constraints).describe()
    said = _result(constraints=constraints, diff="(no changes)").describe()
    assert "No workspace changes." in said, said


def test_a_real_diff_is_quoted_and_clipped():
    constraints, _ = D.parse({"report": {"diff": True}})
    said = _result(constraints=constraints, diff="+ added a line").describe()
    assert "+ added a line" in said, said
    long = _result(constraints=constraints,
                   diff="x" * (D.MAX_REPORT_DIFF_CHARS + 500)).describe()
    assert "diff clipped" in long, long[-200:]
    assert len(long) < D.MAX_REPORT_DIFF_CHARS + 1000


def test_a_timed_out_delegation_still_reports_what_it_got_through():
    """Section 21: do not discard useful information merely because the worker
    did not complete normally."""
    constraints, _ = D.parse({"timeout_seconds": 600,
                              "report": {"file_list": True, "summary": True}})
    said = _result(status=D.TIMED_OUT, constraints=constraints,
                   inspected=tuple("f%d.py" % i for i in range(17)),
                   duration=600, steps=23).describe()
    assert "STATUS: TIMED OUT" in said, said
    assert "Runtime: 10:00 of 10:00" in said, said
    assert "23 actions taken" in said and "17 files inspected" in said, said
    assert "Inspected (17)" in said, said
    assert "stopped at its deadline" in said, said


def test_a_delegation_with_no_timeout_reports_its_runtime_alone():
    said = _result(duration=102).describe()
    assert "Runtime: 1:42" in said, said
    assert " of " not in said.split("Runtime:")[1].splitlines()[0], said


def test_violations_are_in_every_report_whether_or_not_one_was_asked_for():
    """Section 41. A blocked write is often the reason a delegation did not
    finish its task, and hiding it would leave the main agent reading an
    incomplete result with no explanation for it."""
    constraints, _ = D.parse({"read_only": True})
    said = _result(constraints=constraints,
                   violations=(D.violation("write_file", ["a.py"]),)).describe()
    assert "Constraint violations: 1 write operation blocked" in said, said
    assert "write_file a.py" in said, said


def test_the_contract_is_repeated_back_on_the_report():
    constraints, _ = D.parse({"read_only": True, "timeout_seconds": 300,
                              "report": {"diff": True}})
    said = _result(constraints=constraints).describe()
    assert "READ ONLY" in said and "TIMEOUT 5:00" in said and "DIFF" in said


def test_the_timeout_on_a_result_is_the_contracts_own():
    constraints, _ = D.parse({"timeout_seconds": 45})
    assert _result(constraints=constraints).timeout == 45
    assert _result().timeout is None
