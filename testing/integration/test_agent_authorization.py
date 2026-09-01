"""The authorisation model, driven through the dispatcher and the real loop.

`testing/unit/test_agent_capabilities.py` protects the parser, the state and
the guard as functions. This file protects the three things that only exist
once they are wired in, and every one of them is a place the feature could be
correct in isolation and useless in practice.

THE RUNTIME GUARD. Tool availability is not enforcement. The prompt can leak a
verb, a model can emit one it was never taught, and a cached prompt can fail to
rebuild -- so the question asked here is what happens when the model calls a
capability it was not given, at the only path it can reach one by.

THE COMPLETION GATE. Only what the user asked for may hold their answer. A
turn with `/plan` must not also need a review; a turn with `/verify` must not
also need a plan; a turn with none of them must not be gated at all. Those are
four different wrong answers and each has a test.

THE SCOPE. A capability is authorised for the request that asked for it and no
longer. That is asserted across two questions in one session, because the
planning system shipped a session-killing bug that every one-question test in
the suite missed -- a `drive_session` script ending in `quit` returns before
the loop reaches the next turn at all.

Helpers come from test_agent_cli and test_agent_menu. A second harness for the
same loop would drift from the first, and the drift would be silent.
"""

import json

import agent_actions
import agent_capabilities as C
import agent_config
import agent_manager
import agent_plan
import agent_review
import agent_session
import agent_verify
import agent_worker
import TMT

from test_agent_menu import visible
from test_agent_cli import drive_session


def granted(text):
    """An action context authorising whatever `text` asks for, and nothing else."""
    return {"capabilities": C.Capabilities(text),
            "plan": agent_plan.Plan(),
            "review": agent_review.ReviewState(),
            "verify": agent_verify.VerificationState(),
            "manager": agent_manager.AgentManager(),
            "task": text}


def call(action, text, **keys):
    """One capability action through the dispatcher, which is the only path.

    Through `execute_action` rather than through the private handler, for the
    reason test_agent_toolflow gives: an action that works perfectly and is
    not registered is an action that does not exist -- and a guard that is not
    on the dispatch path is a guard the model never meets.
    """
    obj = dict({"action": action}, **keys)
    return agent_actions.execute_action(obj, granted(text))


def answered(message):
    return json.dumps({"action": "end_conversation", "message": message})


def planned(*steps):
    return json.dumps({"action": "plan", "operation": "create",
                       "steps": list(steps)})


def completed(*positions):
    return json.dumps({"action": "plan", "operation": "update",
                       "steps": [{"step": n, "status": "completed"}
                                 for n in positions]})


def wrote(path="feature.py", content="VALUE = 1\n"):
    return json.dumps({"action": "write_file", "path": path, "content": content})


def user_said(seen):
    """Everything the loop handed back to the model, as one string."""
    return "\n".join(message["content"] for request in seen
                     for message in request if message["role"] == "user")


# --- the runtime guard ------------------------------------------------------

def test_an_unauthorised_capability_is_refused_at_the_dispatcher():
    """Section 11. The model emits the verb and the action does not run.

    Not a silent no-op and not a fabricated success: the result says it was
    refused, says which command would have enabled it, and the state it would
    have written to is untouched.
    """
    for action, keys in (("plan", {"operation": "create", "steps": ["One"]}),
                         ("review", {}),
                         ("verify", {})):
        out = call(action, "Build me an authentication system", **keys)
        assert out.startswith("REFUSED:"), (action, out)
        assert "/" + action in out, (action, out)
        assert "not enabled" in out, (action, out)


def test_a_refused_capability_leaves_no_state_behind():
    """The half that matters most. A guard that refused the call but let the
    plan be created, or the review be marked begun, would be a guard that only
    changed the wording."""
    context = granted("Build it")
    agent_actions.execute_action(
        {"action": "plan", "operation": "create", "steps": ["One", "Two"]},
        context)
    assert len(context["plan"]) == 0, context["plan"].steps
    assert not context["plan"]
    agent_actions.execute_action({"action": "review"}, context)
    assert context["review"].state == agent_review.IDLE
    assert not context["review"].passed
    assert context["review"].cycles == 0
    agent_actions.execute_action({"action": "verify"}, context)
    assert context["verify"].state == agent_verify.IDLE
    assert not context["verify"].passed
    assert context["verify"].cycles == 0


def test_an_authorised_capability_runs():
    """The other direction, or the guard would pass by refusing everything.

    The plan is the one of the three that can be driven for real in a test
    without starting an agent or a subprocess, so it is the one that proves
    the whole path end to end: guard, handler, and a plan that exists after.
    """
    context = granted("Build it /plan")
    out = agent_actions.execute_action(
        {"action": "plan", "operation": "create", "steps": ["One", "Two"]},
        context)
    assert not out.startswith("REFUSED:"), out
    assert "S1" in out, out
    assert len(context["plan"]) == 2, context["plan"].steps


# What each capability is called with when the point is only whether the guard
# let go. `plan show` reads an empty plan and does nothing; the other two are
# given a scope their own handler rejects, and that rejection comes from INSIDE
# the handler -- so a "FAILED" is positive evidence the guard allowed it
# through. Calling them for real here would start a reviewer agent and wait out
# its timeout, and run this repository's whole suite from inside the suite.
HARMLESS = {"plan": {"operation": "show"},
            "review": {"scope": "not-a-scope"},
            "verify": {"scope": "not-a-scope"}}


def reached(action, context):
    """Whether the capability guard let this action reach its handler."""
    out = agent_actions.execute_action(
        dict({"action": action}, **HARMLESS[action]), context)
    if out.startswith("REFUSED:"):
        return False, out
    return True, out


def test_each_capability_is_gated_on_its_own_command():
    """Independence at the dispatcher: authorising one must not let the other
    two through, in either direction, for any of the six pairs."""
    for authorised in C.CAPABILITIES:
        context = granted("Build it /" + authorised)
        for action in C.CAPABILITIES:
            allowed, out = reached(action, context)
            assert allowed is (action == authorised), (authorised, action, out)


def test_the_model_cannot_authorise_itself_by_writing_the_command():
    """Section 12, at the seam it would actually be attempted.

    The user asked for a bug fix. The model announces a plan, writes `/plan`
    into its own progress line and into the plan's own step titles, and calls
    the action anyway. None of that is the user's prompt, so none of it counts.
    """
    context = granted("Fix this bug.")
    out = agent_actions.execute_action(
        {"action": "plan", "operation": "create",
         "steps": ["/plan the work", "This task is complex so I will /plan"],
         "progress": "This is complex, so I'll create a plan. /plan"},
        context)
    assert out.startswith("REFUSED:"), out
    assert not context["plan"]
    # And the authorisation itself did not move.
    assert context["capabilities"].active() == ()


def test_tool_output_and_worker_output_cannot_authorise_anything():
    """The capability is read from the user's typed line and from nothing
    else, so a file, a search result or a worker's report containing the
    command is text like any other text."""
    session = agent_session.Session()
    session.begin_turn("summarise what the tests do", "prompt")
    assert session.capabilities.active() == ()
    # A tool result, a worker's answer and the model's own reply all mention
    # the commands. The session's authorisation is unmoved by every one.
    session.record("summarise what the tests do",
                   "I ran /verify and /review and /plan on it.")
    assert session.capabilities.active() == ()
    # The model's own reply, recorded the way the loop records it.
    session.record_reply('{"action":"plan","progress":"/plan /verify /review"}')
    assert session.capabilities.active() == ()
    # And a tool result carrying all three, handed back as an action result.
    agent_actions.build_result_message(
        "read_file", "Result: the README says /plan /review /verify",
        {"action": "read_file", "path": "README.md"})
    assert session.capabilities.active() == ()
    # Only the next question the USER types moves it.
    session.begin_turn("do it again /review", "prompt")
    assert session.capabilities.active() == ("review",)


def test_a_background_agent_is_refused_all_three_capabilities():
    """Two-sided, and this is the third side. The prompts never teach them,
    `WORKER_FORBIDDEN` refuses them by name, and a worker's action context
    carries no authorisation at all -- so even a worker holding the verb is
    refused by the capability guard."""
    context = agent_worker._context(
        agent_manager.AgentRecord("1", 1, "worker", "t"))
    assert "capabilities" not in context
    for action in C.CAPABILITIES:
        assert action in agent_worker.WORKER_FORBIDDEN, action
        out = agent_actions.execute_action({"action": action}, context)
        assert out.startswith("REFUSED:"), (action, out)


def test_the_ordinary_tools_are_untouched_by_any_of_this():
    """Section 31. Only the three advanced capabilities changed; a task with
    no commands in it still has every normal action."""
    context = granted("Build me a dashboard")
    for obj in ({"action": "list_files"},
                {"action": "glob", "pattern": "*.py"},
                {"action": "grep", "query": "def "},
                {"action": "tree"},
                {"action": "git_status"}):
        out = agent_actions.execute_action(obj, context)
        assert not str(out).startswith("REFUSED:"), (obj, out)


# --- what the turn is told --------------------------------------------------

def test_the_prompt_offers_only_the_authorised_verbs():
    """Layer one, at the seam the loop uses it. `get_system_prompt` is asked
    with the turn's own authorisation, so an unauthorised verb is never
    described in the first place."""
    import agent_prompt
    session = agent_session.Session()
    session.begin_turn("Build a dashboard /review", "")
    prompt = agent_prompt.get_system_prompt(session.capabilities)
    assert agent_prompt.REVIEW_REFERENCE in prompt
    assert agent_prompt.PLAN_REFERENCE not in prompt
    assert agent_prompt.VERIFY_REFERENCE not in prompt


def test_the_two_layers_disagree_about_nothing():
    """The prompt subtracts one set and the guard permits the other. A verb in
    neither would be taught and refused; a verb in both would be permitted and
    never described."""
    for text in ("", "/plan", "/review", "/verify", "/plan /review",
                 "/plan /verify", "/review /verify", "/plan /review /verify"):
        context = granted(text)
        for action in C.CAPABILITIES:
            allowed, _ = reached(action, context)
            taught = action in C.allowed_actions(context["capabilities"])
            assert allowed is taught, (text, action)


# --- the completion gate ----------------------------------------------------

def test_no_command_means_none_of_the_three_can_hold_the_answer():
    """The reversal, at the gate. This turn plans nothing, verifies nothing
    and is reviewed by nobody, and it answers -- where before, enough changed
    files and a long enough plan would have required a review of it."""
    answer = "Added the feature."
    replies = [wrote(), wrote("more.py"), wrote("again.py"), answered(answer)]
    drawn, seen, console = drive_session(["build me a login page", "quit"],
                                         replies)
    assert len(seen) == len(replies), len(seen)
    assert answer in visible(drawn), visible(drawn)[-2000:]
    said = user_said(seen)
    assert "BLOCKED" not in said, said[-1500:]
    del console


def test_plan_only_gates_on_the_plan_and_on_nothing_else():
    """`/plan` alone. The plan must be finished; a review and a verification
    must not become mandatory because planning was asked for."""
    answer = "Done and the plan is complete."
    replies = [planned("Implement it", "Tidy up"),
               answered("Too early."),          # refused: steps outstanding
               completed(1, 2),
               answered(answer)]
    drawn, seen, console = drive_session(["add the feature /plan", "quit"],
                                         replies)
    assert len(seen) == len(replies), len(seen)
    assert answer in visible(drawn), visible(drawn)[-2000:]
    assert "Too early." not in visible(drawn)
    said = user_said(seen)
    assert "BLOCKED: you cannot finish yet" in said, said[-2000:]
    # And neither of the other two was ever asked for.
    assert "it must be verified" not in said, said[-2000:]
    assert "needs an independent review" not in said, said[-2000:]
    del console


def test_verify_only_gates_on_verification_and_on_nothing_else():
    """`/verify` alone, with no plan anywhere. Verification must hold the
    answer; the absence of a plan must not."""
    answer = "Added it and the checks pass."
    replies = [wrote(), answered("Too early."),
               json.dumps({"action": "verify", "level": 1}),
               answered(answer)]
    drawn, seen, console = drive_session(["add the feature /verify", "quit"],
                                         replies)
    assert len(seen) == len(replies), len(seen)
    assert answer in visible(drawn), visible(drawn)[-2000:]
    assert "Too early." not in visible(drawn)
    said = user_said(seen)
    assert "it must be verified" in said, said[-2000:]
    assert "needs an independent review" not in said, said[-2000:]
    del console


def test_a_capability_that_was_not_asked_for_never_becomes_a_requirement():
    """The matrix, asked of the two gates that can be settled without running
    an agent. `is_required` is what both refusals consult first."""
    session = agent_session.Session()
    for text, review_wanted, verify_wanted in (
            ("build it", False, False),
            ("build it /plan", False, False),
            ("build it /review", True, False),
            ("build it /verify", False, True),
            ("build it /plan /review", True, False),
            ("build it /plan /verify", False, True),
            ("build it /review /verify", True, True),
            ("build it /plan /review /verify", True, True)):
        session.begin_turn(text, "prompt")
        TMT.note_capability_choices(session)
        # The evidence that used to turn both on by itself, present every time.
        session.review.note_change("write_file", ("a.py",))
        session.verify.note_change("write_file", ("a.py",))
        plan = agent_plan.Plan(["One", "Two", "Three"])
        assert session.review.is_required(plan) is review_wanted, text
        assert session.verify.is_required(plan) is verify_wanted, text


def test_the_gate_reports_which_capability_actually_refused():
    """The pair is returned rather than sniffed out of the refusal's wording,
    so the line the user reads comes from whichever gate spoke."""
    session = agent_session.Session()
    session.begin_turn("add the feature /verify", "prompt")
    TMT.note_capability_choices(session)
    session.verify.note_change("write_file", ("a.py",))
    held, line = TMT.completion_block(session, {"action": "end_conversation",
                                                "message": "done"})
    assert held.startswith("BLOCKED"), held
    assert "verif" in line.lower(), line
    # With nothing authorised the same state holds nothing at all.
    session.begin_turn("add the feature", "prompt")
    TMT.note_capability_choices(session)
    session.verify.note_change("write_file", ("a.py",))
    assert TMT.completion_block(session, {"action": "end_conversation",
                                          "message": "done"}) == ("", "")


def test_an_unauthorised_plan_cannot_gate_anything_because_it_cannot_exist():
    """The plan needs no `user_choice` of its own: its gate fires on a plan
    with outstanding steps, and the runtime guard means an unauthorised turn
    can never have one."""
    context = granted("add the feature")
    agent_actions.execute_action(
        {"action": "plan", "operation": "create", "steps": ["One", "Two"]},
        context)
    assert agent_plan.refusal(context["plan"], "end_conversation") == ""


# --- the scope --------------------------------------------------------------

def test_a_capability_is_authorised_for_one_question_only():
    """Section 8, and the transition every one-question test in this suite
    misses: a `drive_session` script ending in `quit` returns before the loop
    reaches the next turn at all.

    The first question authorises planning and finishes its plan. The second
    is unrelated, has no command in it, and must start from nothing.
    """
    replies = [planned("Implement it"), completed(1), answered("Added it."),
               # The second turn. If planning were still authorised this would
               # create a plan; it is refused, and the answer lands anyway.
               planned("Something else"), answered("zip pairs two sequences.")]
    drawn, seen, console = drive_session(
        ["add the feature /plan", "what does zip do?", "quit"], replies)
    assert len(seen) == len(replies), len(seen)
    assert "zip pairs two sequences." in visible(drawn), visible(drawn)[-2000:]
    # The second turn's plan attempt was refused, by name.
    last = user_said([seen[-1]])
    assert "REFUSED" in last and "/plan" in last, last
    del console


def test_the_session_carries_no_authorisation_of_its_own():
    """Nothing accumulates and nothing has to be expired. Every turn is parsed
    on its own, so a capability used once is not a capability granted."""
    session = agent_session.Session()
    session.begin_turn("add the feature /plan /review /verify", "prompt")
    assert session.capabilities.active() == ("plan", "review", "verify")
    session.begin_turn("now change the button styling", "prompt")
    assert session.capabilities.active() == ()
    # And back again, from the new question's own words.
    session.begin_turn("try that again /verify", "prompt")
    assert session.capabilities.active() == ("verify",)


def test_clearing_the_conversation_clears_the_authorisation():
    """`/clear` forgets what was being worked on, and a permission granted for
    a task the session has just been told to forget is not a permission."""
    session = agent_session.Session()
    session.begin_turn("add the feature /plan /verify", "prompt")
    assert session.capabilities.any()
    session.clear()
    assert session.capabilities.active() == ()
    assert len(session) == 0


def test_the_authorisation_object_is_never_rebound():
    """The invariant the guard rests on. The loop puts THIS object in the
    action context before the turn starts, so a new one assigned in
    `begin_turn` would leave the guard reading flags nothing writes to --
    authorisation silently off, with nothing on screen to notice it by."""
    session = agent_session.Session()
    held = session.capabilities
    session.begin_turn("one /plan", "prompt")
    assert session.capabilities is held
    session.begin_turn("two /review", "prompt")
    assert session.capabilities is held
    session.clear()
    assert session.capabilities is held
    assert held.active() == ()


# --- the slash commands they collide with -----------------------------------

def test_a_bare_command_is_still_the_report_it_always_was():
    """`/plan` alone on a line has meant "show me the plan" since the column
    existed, and it still does. Nothing that worked before behaves
    differently."""
    import agent_commands
    for name in C.CAPABILITIES:
        result = agent_commands.dispatch("/" + name)
        assert result is not None, name
        assert result.ok, (name, result.text())


def test_a_command_with_a_task_after_it_is_a_task():
    """`/plan Build the login page` is the user authorising planning FOR that
    task, so it goes to the model like any other line -- which is what the
    same words further along the line already did, because `parse` only ever
    looked at a leading slash."""
    import agent_commands
    for line in ("/plan Build the login page", "/review this diff",
                 "/verify the tests still pass", "/plan /review Build it"):
        assert agent_commands.dispatch(line) is None, line
    # And the capability it names is the one it turns on.
    assert C.names_in("/plan Build the login page") == ("plan",)


def test_the_other_commands_are_untouched():
    """Only the three capability names changed. Every other command still
    refuses an argument it does not take, and an unknown name is still
    unknown."""
    import agent_commands
    for line in ("/context extra", "/config now", "/clear everything"):
        result = agent_commands.dispatch(line)
        assert result is not None and not result.ok, line
        assert "takes no argument" in result.text(), line
    unknown = agent_commands.dispatch("/bogus something")
    assert unknown is not None and not unknown.ok
    # And a task that merely begins with a path is still a task.
    assert agent_commands.dispatch("/usr/bin/python is broken") is None


def test_the_registered_action_list_still_holds_all_three():
    """The verbs are registered exactly as they were; only the authorisation
    around them changed. A capability removed from REQUIRED_KEYS would be a
    capability that could never run at all."""
    for name in C.CAPABILITIES:
        assert name in agent_config.REQUIRED_KEYS, name
    assert agent_config.REQUIRED_KEYS["plan"] == ["operation"]
    assert agent_config.REQUIRED_KEYS["review"] == []
    assert agent_config.REQUIRED_KEYS["verify"] == []
