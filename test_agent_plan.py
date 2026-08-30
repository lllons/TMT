"""Tests for the planning system: the state, the action, the column and the gate.

Four things are being protected, and the fourth is the one the feature exists
for.

The STATE is a small machine with strict rules -- one step in progress, no way
back out of completed, positions that only move when the model itself moves
them -- and every rule is here as a test that fails when the rule is removed.

The ACTION is registered the way every TMT action is, and every way a model
can get it wrong comes back as a sentence rather than as an exception. A plan
must never be able to end a session.

The COLUMN is asserted with the escapes stripped, because colour is
confirmation here and never the message: every status carries a mark as well.
The three colours the design asks for -- green, orange, red -- are asserted as
positions on TMT's one gradient rather than as escape sequences, so the tests
describe the rule and not the palette.

The GATE is the one that matters. A final answer with steps outstanding is not
shown to the user, and that is enforced in the loop rather than asked for in
the prompt. The last two tests drive TMT.main end to end to prove it: one
where the model tries to finish early and cannot, and one where it finishes
the plan and the answer lands.

Helpers come from test_agent_menu and test_agent_cli. A second harness for the
same box or the same loop would drift from the first, and the drift would be
silent.
"""

import io
import json
import re

import agent_actions
import agent_config
import agent_panel
import agent_plan
import agent_prompt
import agent_session
import agent_worker
import TMT

from test_agent_menu import Stdin, Terminal, menu, visible
from test_agent_cli import drive_session


def plan_of(*titles):
    """A plan of these steps, freshly created."""
    plan = agent_plan.Plan()
    plan.create(list(titles))
    return plan


def statuses(plan):
    return [step.status for step in plan.steps]


def rows_of(plan, width=30, height=None, stream=None):
    """The painted column, with the escapes stripped."""
    stream = Terminal() if stream is None else stream
    return [visible(row) for row in
            agent_panel.plan_rows(plan, width, height=height, stream=stream)]


def refused(call, *args, **kwargs):
    """The sentence a refused plan operation came back with."""
    try:
        call(*args, **kwargs)
    except agent_plan.PlanError as error:
        return str(error)
    raise AssertionError("that was allowed and should not have been")


# --- the state machine ------------------------------------------------------

def test_a_new_plan_starts_with_its_first_step_in_progress():
    """The screen is read the moment the plan is made. A plan whose steps were
    all pending would open entirely red, which says the work has not started
    when the work is starting now."""
    plan = plan_of("Inspect the repository", "Implement", "Run the tests")
    assert statuses(plan) == ["in_progress", "pending", "pending"], statuses(plan)
    assert plan.active().id == "S1"
    assert [step.id for step in plan.steps] == ["S1", "S2", "S3"]
    assert not plan.is_complete()


def test_completing_a_step_promotes_the_next_one():
    """The way people actually work: finishing one thing starts the next. A
    model that sends the second call anyway is told it is already in progress
    rather than being refused, so both shapes work."""
    plan = plan_of("One", "Two", "Three")
    plan.update(1, "completed")
    assert statuses(plan) == ["completed", "in_progress", "pending"], statuses(plan)
    # And saying it explicitly is not an error.
    said = plan.update(2, "in_progress")
    assert "already in_progress" in said, said
    assert statuses(plan) == ["completed", "in_progress", "pending"]


def test_only_one_step_is_in_progress_and_the_other_is_demoted_not_completed():
    """The trap this avoids: quietly completing the step that was active would
    mark work done that nobody did, in the one place the user is trusting."""
    plan = plan_of("One", "Two", "Three")
    plan.update(3, "in_progress")
    assert statuses(plan) == ["pending", "pending", "in_progress"], statuses(plan)
    assert len([s for s in plan.steps if s.status == "in_progress"]) == 1
    # S1 is still outstanding and still visible.
    assert [step.id for step in plan.outstanding()] == ["S1", "S2", "S3"]


def test_a_completed_step_cannot_be_reopened():
    """A finished step stays finished. A plan whose shape turned out wrong is
    replaced -- one decision, one call -- rather than unwound a step at a
    time, and the refusal says exactly that."""
    plan = plan_of("One", "Two")
    plan.update(1, "completed")
    for status in ("pending", "in_progress", "blocked"):
        said = refused(plan.update, 1, status)
        assert "completed" in said and "create" in said, said
    assert statuses(plan)[0] == "completed"


def test_completing_an_already_completed_step_is_refused_by_name():
    """A model completing a step twice has usually lost track of where it is.
    Being told so is more use than a silent success that leaves it just as
    lost."""
    plan = plan_of("One", "Two")
    plan.update(1, "completed")
    said = refused(plan.update, 1, "completed")
    assert "already completed" in said, said
    assert "S1" in said and "One" in said, said


def test_an_unknown_step_is_named_against_the_range_that_exists():
    """The correction has to be makeable without another call, so the refusal
    states the range rather than only that the number was wrong."""
    plan = plan_of("One", "Two", "Three")
    said = refused(plan.update, 9, "completed")
    assert "no step 9" in said and "S1 to S3" in said, said
    said = refused(plan.update, "banana", "completed")
    assert "not a step" in said, said
    # And before there is a plan at all, the answer is how to make one.
    said = refused(agent_plan.Plan().update, 1, "completed")
    assert "no plan yet" in said and "create" in said, said


def test_an_unknown_status_names_the_ones_that_exist():
    """Mapping an unknown status onto the nearest one would put a step into a
    state the model did not ask for and does not know about."""
    said = refused(plan_of("One").update, 1, "nearly")
    for status in agent_plan.STATUSES:
        assert status in said, said
    # The spellings a model actually reaches for are forgiven, though.
    plan = plan_of("One", "Two")
    plan.update(1, "COMPLETED")
    plan.update(2, "in-progress")
    assert statuses(plan) == ["completed", "in_progress"], statuses(plan)


def test_a_step_can_be_referred_to_as_a_number_or_as_a_label():
    """The column says S2 and the model is looking at the column. Refusing the
    label it can see would be a rule about spelling."""
    for reference in (2, "2", "S2", "s2"):
        plan = plan_of("One", "Two")
        plan.update(reference, "completed")
        assert plan.steps[1].status == "completed", reference


def test_a_batch_update_is_all_or_nothing():
    """A batch that failed half way would leave a plan nobody wrote -- some of
    it moved, some not, and the model told only about the part that failed."""
    plan = plan_of("One", "Two", "Three")
    said = refused(plan.update, updates=[{"step": 1, "status": "completed"},
                                         {"step": 9, "status": "completed"}])
    assert "no step 9" in said, said
    assert statuses(plan) == ["in_progress", "pending", "pending"], statuses(plan)
    # And a good batch applies every entry.
    plan.update(updates=[{"step": 1, "status": "completed"},
                         {"step": 2, "status": "completed"}])
    assert statuses(plan) == ["completed", "completed", "in_progress"], statuses(plan)


def test_a_plan_is_complete_only_when_every_step_is():
    plan = plan_of("One", "Two")
    assert not plan.is_complete()
    plan.update(1, "completed")
    assert not plan.is_complete()
    plan.update(2, "completed")
    assert plan.is_complete()
    assert plan.outstanding() == ()
    assert len(plan.completed()) == 2
    # An empty plan is complete: it is the answer to "may this turn end", and
    # a turn nobody planned was never gated.
    assert agent_plan.Plan().is_complete()


def test_a_blocked_step_still_counts_as_outstanding():
    """Otherwise "blocked" would be a way to finish a task by declaring a step
    impossible, which is exactly the bypass the gate exists to prevent."""
    plan = plan_of("One", "Two")
    plan.update(1, "blocked")
    plan.update(2, "completed")
    assert not plan.is_complete()
    assert [step.id for step in plan.outstanding()] == ["S1"]
    assert agent_plan.refusal(plan, "respond"), "a blocked step let the turn end"


def test_create_replaces_the_plan_and_can_carry_progress_forward():
    """The revision path. Real tasks change shape, and re-planning must not
    mean throwing away the work already done."""
    plan = plan_of("One", "Two")
    plan.update(1, "completed")
    said = plan.create([{"title": "One", "status": "completed"},
                        "Two, revised", "Three, new"])
    assert "replaced" in said, said
    assert statuses(plan) == ["completed", "in_progress", "pending"], statuses(plan)
    assert [step.title for step in plan.steps] == ["One", "Two, revised", "Three, new"]
    # A plain create starts again from the top.
    plan.create(["Fresh"])
    assert statuses(plan) == ["in_progress"], statuses(plan)


def test_add_and_remove_renumber_and_say_so():
    """Positions are the identity, so the one thing that can shift a position
    has to state the result. The model is never guessing about a change it did
    not just make."""
    plan = plan_of("One", "Two", "Three")
    said = plan.add("Inserted", after=1)
    assert "S2 Inserted" in said, said
    assert [step.title for step in plan.steps] == ["One", "Inserted", "Two", "Three"]
    said = plan.remove(3)
    assert "Removed S3 (Two)" in said and "The plan is now" in said, said
    assert [step.id for step in plan.steps] == ["S1", "S2", "S3"]
    assert [step.title for step in plan.steps] == ["One", "Inserted", "Three"]
    # Removing the last step leaves no plan, and nothing to gate on.
    for _ in range(3):
        plan.remove(1)
    assert len(plan) == 0 and plan.is_complete()


def test_a_plan_refuses_more_steps_than_it_can_show():
    """A plan is the milestones the user would recognise, not every tool call.
    The ceiling is a presentation limit and the refusal says which."""
    said = refused(agent_plan.Plan().create,
                   ["step %d" % number for number in range(agent_plan.MAX_STEPS + 1)])
    assert str(agent_plan.MAX_STEPS) in said and "milestones" in said, said
    full = plan_of(*["step %d" % number for number in range(agent_plan.MAX_STEPS)])
    assert "full" in refused(full.add, "one more")


def test_an_empty_plan_is_refused_and_clear_is_the_way_to_drop_one():
    """Creating nothing and dropping the plan are different intentions, and a
    create that quietly did the second would drop the gate by accident."""
    said = refused(agent_plan.Plan().create, [])
    assert "clear" in said, said
    assert "must be a list" in refused(agent_plan.Plan().create, "one, two")
    assert "needs a title" in refused(agent_plan.Plan().create, ["   "])
    plan = plan_of("One", "Two")
    assert "2 steps" in plan.clear()
    assert len(plan) == 0 and plan.is_complete()


def test_a_plan_with_work_against_it_cannot_be_cleared():
    """The one route that could have walked round the gate. Every other way out
    of an unfinished plan is a visible statement about the work -- finish it, or
    `create` a plan that describes the task properly, both of which stay on
    screen. Dropping a half-done plan says nothing at all, and would turn the
    contract into a formality."""
    plan = plan_of("Implement", "Run the tests")
    plan.update(1, "completed")
    said = refused(plan.clear)
    assert "cannot be cleared" in said and "S1" in said, said
    assert "create" in said, said
    # It is still there, and it still holds the answer back.
    assert len(plan) == 2
    assert agent_plan.refusal(plan, "respond"), "clearing let the answer through"
    # And through the dispatcher, which is the path the model actually takes.
    assert "FAILED:" in run({"operation": "clear"}, context={"plan": plan})
    # Reshaping it is the route that IS open, because it says what changed.
    plan.create([{"title": "Implement", "status": "completed"},
                 {"title": "Run the tests", "status": "completed"}])
    assert agent_plan.refusal(plan, "respond") == ""


def test_a_title_is_trimmed_to_one_line_and_bounded():
    """The column is one row per step. A title carrying its own line breaks
    would draw rows the layout never counted."""
    plan = agent_plan.Plan()
    plan.create(["  spread\n over   lines  "])
    assert plan.steps[0].title == "spread over lines", plan.steps[0].title
    plan.create(["x" * (agent_plan.MAX_TITLE + 50)])
    assert len(plan.steps[0].title) == agent_plan.MAX_TITLE, len(plan.steps[0].title)


def test_an_empty_plan_is_falsy_and_a_plan_with_steps_is_not():
    """`__len__` is defined here, so without `__bool__` an empty plan would be
    falsy in one place and a live object in another. agent_session has been
    bitten by exactly that before."""
    assert not agent_plan.Plan()
    assert plan_of("One")
    assert bool(plan_of("One")) is True


# --- the gate, as a rule ----------------------------------------------------

def test_the_gate_holds_a_final_action_and_names_what_is_outstanding():
    """"Finish the plan" is not actionable. "S2 Run the tests is still
    outstanding" is, and it is what the model is sent."""
    plan = plan_of("Inspect", "Run the tests")
    plan.update(1, "completed")
    for action in ("respond", "done"):
        said = agent_plan.refusal(plan, action)
        assert said.startswith("BLOCKED"), said
        assert "S2: Run the tests" in said, said
        assert "1 step is" in said, said
        assert "\"operation\":\"update\"" in said, said


def test_the_gate_releases_when_every_step_is_complete():
    plan = plan_of("One", "Two")
    plan.update(1, "completed")
    plan.update(2, "completed")
    assert agent_plan.refusal(plan, "respond") == ""
    assert agent_plan.refusal(plan, "done") == ""


def test_the_gate_never_holds_a_turn_that_made_no_plan():
    """Most turns. The gate is a consequence of having made a plan, never a
    requirement to make one."""
    assert agent_plan.refusal(agent_plan.Plan(), "respond") == ""
    assert agent_plan.refusal(None, "respond") == ""


def test_the_gate_only_ever_holds_the_two_terminal_actions():
    """Everything else is the work the gate is asking for. Holding a read or a
    patch would stop the model doing the very thing it is being told to do."""
    plan = plan_of("One", "Two")
    for action in ("read_file", "patch_file", "announce", "plan", "git_commit",
                   "spawn_agent", "internal_response"):
        assert agent_plan.refusal(plan, action) == "", action


# --- the action -------------------------------------------------------------

def run(obj, plan=None, context=None):
    """One plan action through the real dispatcher, as the loop runs it."""
    if context is None:
        context = {"plan": agent_plan.Plan() if plan is None else plan}
    return agent_actions.execute_action(dict(obj, action="plan"), context)


def test_the_plan_action_is_registered_the_way_every_action_is():
    """A tool that works and is not registered is a tool that does not exist.
    This is the path the model can actually take."""
    assert agent_config.REQUIRED_KEYS["plan"] == ["operation"]
    assert agent_prompt.validate_action({"action": "plan"}) is not None
    assert agent_prompt.validate_action(
        {"action": "plan", "operation": "show"}) is None
    assert agent_actions.ACTION_LABELS["plan"] == "Plan"
    # It changes no file, so it must not invalidate the cached system prompt.
    assert "plan" not in agent_config.MUTATING_ACTIONS


def test_every_operation_runs_through_the_dispatcher():
    plan = agent_plan.Plan()
    context = {"plan": plan}
    assert "created with 3 steps" in run(
        {"operation": "create", "steps": ["One", "Two", "Three"]}, context=context)
    assert "S1 (One) in_progress -> completed" in run(
        {"operation": "update", "step": 1, "status": "completed"}, context=context)
    assert "Added S4" in run({"operation": "add", "title": "Four"}, context=context)
    assert "Removed S4" in run({"operation": "remove", "step": 4}, context=context)
    assert "S2: Two" in run({"operation": "show"}, context=context)
    assert statuses(plan) == ["completed", "in_progress", "pending"], statuses(plan)
    # Clear is refused here -- S1 is done -- so it is exercised on a plan
    # nothing has been done against, which is the only thing it is for.
    plan.create(["Not needed after all"])
    assert "Cleared" in run({"operation": "clear"}, context=context)
    assert len(plan) == 0
    # Several statuses in one call, which is what the schema is for.
    plan.create(["One", "Two"])
    assert "S1" in run({"operation": "update",
                        "steps": [{"step": 1, "status": "completed"},
                                  {"step": 2, "status": "completed"}]},
                       context=context)
    assert plan.is_complete()


def test_every_way_of_getting_it_wrong_comes_back_as_words():
    """A plan is a convenience the turn can survive losing. Nothing a model
    writes into one may raise through the loop."""
    plan = plan_of("One", "Two")
    context = {"plan": plan}
    for obj, expected in (
            ({"operation": "fly"}, "not a plan operation"),
            ({"operation": "update", "step": 9, "status": "completed"}, "no step 9"),
            ({"operation": "update", "step": 1, "status": "sideways"}, "not a status"),
            ({"operation": "update"}, "which step"),
            ({"operation": "create", "steps": []}, "at least one step"),
            ({"operation": "create"}, "must be a list"),
            ({"operation": "remove", "step": "nope"}, "not a step"),
            ({"operation": "add"}, "needs a title"),
    ):
        result = run(obj, context=context)
        assert result.startswith("FAILED:"), (obj, result)
        assert expected in result, (obj, result)
    # Nothing was changed by any of them.
    assert statuses(plan) == ["in_progress", "pending"], statuses(plan)


def test_an_action_context_with_no_plan_answers_in_words():
    """A background agent's context has no plan key AT ALL, and neither has an
    install where the session never wired one in. Both must come back as a
    sentence rather than a KeyError that ends the turn."""
    for context in ({}, None, {"push_authorized": True}):
        result = agent_actions.execute_action(
            {"action": "plan", "operation": "create", "steps": ["One"]}, context)
        assert "not available" in result, result
        assert "respond" in result, result


def test_a_plan_that_raises_is_reported_and_never_escapes():
    """Whatever went wrong in there, the model is told and the work carries
    on. A corrupted plan must never take the session with it."""

    class Exploding:
        steps = ()

        def create(self, steps):
            raise RuntimeError("the plan fell over")

    result = run({"operation": "create", "steps": ["One"]},
                 context={"plan": Exploding()})
    assert result.startswith("FAILED:"), result
    assert "RuntimeError" in result and "fell over" in result, result


def test_the_plan_action_is_refused_to_every_background_agent():
    """The plan is the MAIN agent's contract with the user. A worker
    completing a step would let the main agent finish on work the worker had
    merely claimed. Enforced in code, not in wording."""
    assert "plan" in agent_worker.WORKER_FORBIDDEN
    assert "plan" not in agent_worker.NOTE_ACTIONS
    said = agent_worker._refusal("plan", None, agent_worker.WORKER_FORBIDDEN)
    assert said and "plan" in said, said
    said = agent_worker._refusal("plan", agent_worker.NOTE_ACTIONS,
                                 agent_worker.WORKER_FORBIDDEN)
    assert said, "the note agent was allowed to write the plan"


def test_a_plan_call_becomes_a_milestone_the_user_can_read():
    """It is the coarsest thing that happens in a turn and it is what the user
    is following in the column, so it is a milestone rather than a tool line."""
    plan = plan_of("Inspect", "Run the tests")
    obj = {"action": "plan", "operation": "update", "step": 1,
           "status": "completed"}
    event = agent_actions.action_event("plan", obj, plan.update(1, "completed"))
    assert event.kind == "milestone", event.kind
    assert event.message.startswith("S1 (Inspect) in_progress -> completed"), event.message
    # And a refused operation is read as the warning it is.
    warned = agent_actions.action_event("plan", obj, "FAILED: there is no step 9.")
    assert warned.kind == "warning", warned.kind


# --- the session it belongs to ---------------------------------------------

def test_the_plan_belongs_to_the_task_and_not_to_the_session():
    """The scoping rule, and the bug it prevents: an unfinished plan left
    standing would refuse the answer to the NEXT question, which has nothing
    to do with it."""
    session = agent_session.Session()
    session.begin_turn("first task")
    session.plan.create(["One", "Two"])
    assert not session.plan.is_complete()
    session.begin_turn("a completely different question")
    assert len(session.plan) == 0, session.plan.describe()
    assert agent_plan.refusal(session.plan, "respond") == ""


def test_the_session_empties_its_plan_in_place_and_never_rebinds_it():
    """The loop builds its action context BEFORE it calls begin_turn, so a new
    Plan object assigned there would leave the turn writing into the plan of a
    task that is over. That is not hypothetical -- it is the order in TMT.py."""
    session = agent_session.Session()
    held = session.plan          # what the context would have captured
    session.begin_turn("a task")
    assert session.plan is held, "the session rebound its plan"
    session.plan.create(["One"])
    session.clear()
    assert session.plan is held and len(session.plan) == 0


def test_clearing_the_conversation_clears_the_plan():
    """A session told to forget the conversation that still refused to answer
    until an invisible plan was finished would be the worst of both."""
    session = agent_session.Session()
    session.plan.create(["One", "Two"])
    session.clear()
    assert len(session.plan) == 0
    assert agent_plan.refusal(session.plan, "respond") == ""


# --- the column -------------------------------------------------------------

def test_the_column_states_every_status_with_a_mark_as_well_as_a_colour():
    """Colour is never the message. Read the column with the escapes stripped
    and nothing has been lost but confirmation."""
    plan = plan_of("Inspect repository", "Implement", "Run tests", "Explain")
    plan.update(1, "completed")
    plan.update(4, "blocked")
    rows = rows_of(plan, width=34)
    assert rows[0] == "PLAN 1/4", rows[0]
    assert rows[2] == "S1 ✓ Inspect repository", rows[2]
    assert rows[3] == "S2 ● Implement", rows[3]
    assert rows[4] == "S3 ○ Run tests", rows[4]
    assert rows[5] == "S4 ! Explain", rows[5]
    # Four distinct marks, so the four states are four things on the page.
    assert len(set(agent_panel._PLAN_MARKS.values())) == 4
    assert len(set(agent_panel._PLAN_ASCII_MARKS.values())) == 4


def test_the_three_statuses_take_the_three_positions_on_the_one_gradient():
    """Green for done, orange for the step being worked on, red for the ones
    still to come -- and every one of them is a position an existing event
    kind already holds. A new element takes a place on the one scale; it never
    gets a palette."""
    assert agent_panel.PLAN_DONE_POSITION == agent_panel.DONE_POSITION == 95
    assert agent_panel.PLAN_ACTIVE_POSITION == agent_panel.AGENT_POSITION == 40
    assert agent_panel.PLAN_PENDING_POSITION == agent_panel.FAILED_POSITION == 10
    from agent_ui import _gradient
    red, orange, green = (_gradient(agent_panel.PLAN_PENDING_POSITION),
                          _gradient(agent_panel.PLAN_ACTIVE_POSITION),
                          _gradient(agent_panel.PLAN_DONE_POSITION))
    assert red[0] > red[1] and red[0] > red[2], red        # red is reddest
    assert green[1] > green[0] and green[1] > green[2], green   # green is greenest
    assert orange[0] > orange[1] > orange[2], orange       # orange between them

    plan = plan_of("One", "Two", "Three")
    plan.update(1, "completed")
    painted = agent_panel.plan_rows(plan, 20, stream=Terminal())
    def escape(position):
        return "\033[38;2;%d;%d;%dm" % _gradient(position)
    assert escape(agent_panel.PLAN_DONE_POSITION) in painted[2], repr(painted[2])
    assert escape(agent_panel.PLAN_ACTIVE_POSITION) in painted[3], repr(painted[3])
    assert escape(agent_panel.PLAN_PENDING_POSITION) in painted[4], repr(painted[4])


def test_the_column_degrades_where_the_terminal_cannot_draw():
    """The console is cp1252 on Windows through a pipe. Anything decorative
    has to survive that, and the mark on the ACTIVE step is the one row the
    user is looking for."""

    class Cp1252(io.StringIO):
        def isatty(self):
            return True

        @property
        def encoding(self):
            return "cp1252"

    plan = plan_of("One", "Two")
    plan.update(1, "completed")
    rows = [visible(row) for row in
            agent_panel.plan_rows(plan, 20, stream=Cp1252())]
    assert rows[1] == "-" * 20, rows[1]
    assert rows[2] == "S1 + One", rows[2]
    assert rows[3] == "S2 > Two", rows[3]
    for row in rows:
        row.encode("cp1252")     # raises if anything unencodable got through


def test_the_column_fits_every_width_and_never_overflows():
    """A row drawn past the column pushes the panel right and wraps the whole
    region, which is a frame that marches down the screen on every repaint."""
    plan = plan_of("A short one",
                   "An extremely long step title that will not fit anywhere",
                   "プロジェクトを調べる")
    for width in (1, 4, 8, 12, 18, 24, 34, 80):
        for rows in (agent_panel.plan_rows(plan, width, stream=Terminal()),
                     agent_panel.plan_rows(plan, width, stream=io.StringIO())):
            for row in rows:
                assert menu().display_width(visible(row)) <= width, (
                    width, repr(row), menu().display_width(visible(row)))


def test_a_plan_taller_than_the_region_keeps_the_active_step_in_view():
    """The window follows the step being worked on for the reason the input
    field's window follows the caret: a plan scrolled to its top while the
    agent works on S9 is a plan about somebody else's task."""
    plan = plan_of(*["Step %d" % number for number in range(1, 11)])
    for number in range(1, 9):
        plan.update(number, "completed")
    assert plan.active().id == "S9"
    rows = rows_of(plan, width=20, height=6)
    assert len(rows) <= 6, rows
    assert any("S9" in row for row in rows), rows
    # And the header still counts every step, so the trimming is not silent.
    assert rows[0] == "PLAN 8/10", rows[0]


def test_an_empty_plan_draws_nothing_at_all():
    """A heading over an empty column would promise a plan the task never
    made."""
    assert agent_panel.plan_rows(agent_plan.Plan(), 30, stream=Terminal()) == []
    assert agent_panel.plan_rows(None, 30, stream=Terminal()) == []


def test_the_plan_takes_the_right_hand_column_when_the_panel_is_shut():
    """Which is nearly always: the agents panel is opened by a gesture and
    closed again, while a plan stands for the length of a task."""
    plan = plan_of("Inspect", "Implement", "Run tests")
    state = agent_panel.PanelState(manager=None, stream=Terminal(), plan=plan)
    assert not state.open
    frame = state.frame(80, 24)
    assert frame is not None, "the plan did not take the column"
    left, join = frame
    assert left > 0, left           # the box is still drawn beside it
    rows = [visible(row) for row in join(["reply row one", "reply row two"])]
    assert any(row.rstrip().endswith("PLAN 0/3") for row in rows), rows
    assert any("S1 ● Inspect" in row for row in rows), rows
    assert any("reply row two" in row for row in rows), rows
    # With no plan and no open panel there is no column, and the region is
    # exactly the region it was before any of this existed.
    assert agent_panel.PanelState(stream=Terminal()).frame(80, 24) is None


def test_the_panel_and_the_plan_share_the_column_when_both_want_it():
    """The plan takes the TOP of the column and the panel the rest -- the plan
    is the shape of the whole task and the panel is one thing happening inside
    it."""

    class Register:
        def visible_agents(self, now=None):
            return ()

        def list(self):
            return ()

    plan = plan_of("Inspect", "Implement")
    state = agent_panel.PanelState(manager=Register(), stream=Terminal(),
                                   plan=plan)
    assert state.open_panel(80) is True
    rows = [visible(row) for row in state.frame(80, 40)[1]([])]
    text = "\n".join(rows)
    assert "PLAN 0/2" in text and "AGENTS 0" in text, text
    assert text.index("PLAN 0/2") < text.index("AGENTS 0"), "the plan was not on top"


def test_a_terminal_too_narrow_refuses_the_plan_column_rather_than_the_box():
    """An open panel has focus and may take the whole region. A plan is
    something to glance at while typing, and one that swallowed the prompt box
    would take away the thing being used. /plan is the way in at that width."""
    plan = plan_of("One", "Two")
    state = agent_panel.PanelState(stream=Terminal(), plan=plan)
    assert state.frame(agent_panel.TWO_COLUMN_MIN, 24) is not None
    for columns in range(10, agent_panel.TWO_COLUMN_MIN):
        assert state.frame(columns, 24) is None, columns


def test_a_column_with_no_register_behind_it_does_not_open():
    """There is nothing for the keys to drive, and the gesture would replace a
    plan that is already drawn with an empty heading."""
    state = agent_panel.PanelState(stream=Terminal(), plan=plan_of("One"))
    assert state.open_panel(120) is False
    assert not state.open
    assert "unavailable" in state.message, state.message
    # And the plan still has the column.
    assert state.frame(120, 24) is not None


def test_the_composed_region_survives_every_terminal_width():
    """Resizing mid-turn is the ordinary case, not the edge. Every composed
    row is measured to the content width at every width the region can take."""
    plan = plan_of("Inspect the repository", "Implement the feature",
                   "Run the tests and verify")
    plan.update(1, "completed")
    state = agent_panel.PanelState(stream=Terminal(), plan=plan)
    for columns in (30, 45, 60, 80, 120, 200):
        frame = state.frame(columns, 24)
        if frame is None:
            continue
        left, join = frame
        rows = join(["a reply that is quite long " * 3])
        for row in rows:
            assert menu().visible_width(row) <= columns - 1, (
                columns, repr(row), menu().visible_width(row))


def test_a_plan_provider_that_raises_costs_the_column_and_never_the_turn():
    """Decoration is never allowed to end a turn. That rule already covers a
    register that cannot answer; the plan joins it."""

    def exploding():
        raise RuntimeError("no plan here")

    state = agent_panel.PanelState(stream=Terminal(), plan=exploding)
    assert state.plan_now() is None
    assert state.frame(120, 24) is None


def test_the_prompt_box_draws_the_plan_beside_it():
    """The box asks for a frame and knows nothing about what ended up in it,
    which is what let the column be extended without touching either caller."""
    session = agent_session.Session()
    session.plan.create(["Inspect the repository", "Run the tests"])
    box = menu().PromptBox(stream=Terminal(), instream=Stdin(),
                           session=session)
    rows = [visible(row) for row in
            box.lines(menu().LineEditor(), size=(100, 24))]
    text = "\n".join(rows)
    assert "PLAN 0/2" in text, text
    assert "S1 ● Inspect the repository" in text, text
    assert any(row.strip().startswith(">") for row in rows), rows
    # The plan is read at the moment the row is drawn, never cached: this is
    # the next task, and the box must be showing the next task's plan.
    session.begin_turn("something else entirely")
    text = "\n".join(visible(row) for row in
                     box.lines(menu().LineEditor(), size=(100, 24)))
    assert "PLAN" not in text, text


def test_a_box_with_neither_a_manager_nor_a_session_is_the_box_it_always_was():
    """The rule the panel was built under and this must not break: a session
    without any of this costs exactly nothing -- no import, no frame change,
    no extra repaint."""
    box = menu().PromptBox(stream=Terminal(), instream=Stdin())
    assert box.panel() is None
    before = box.lines(menu().LineEditor(), size=(100, 24))
    after = box.lines(menu().LineEditor(), size=(100, 24))
    assert before == after, "two frames of an untouched box differed"


# --- the way in without the column -----------------------------------------

def test_the_plan_command_reports_the_plan_as_permanent_text():
    """The unambiguous alternate, and the only way in on a terminal too narrow
    to hold two columns."""
    import agent_commands
    session = agent_session.Session()
    empty = agent_commands.dispatch("/plan", session)
    assert empty.title == "Plan"
    assert "no plan" in "\n".join(empty.rows), empty.rows

    session.plan.create(["Inspect", "Run the tests"])
    session.plan.update(1, "completed")
    said = "\n".join(agent_commands.dispatch("/plan", session).rows)
    assert "PLAN 1/2" in said, said
    assert "S1 + Inspect  (completed)" in said, said
    assert "cannot finish this task" in said, said
    session.plan.update(2, "completed")
    said = "\n".join(agent_commands.dispatch("/plan", session).rows)
    assert "Every step is complete." in said, said
    # Registered like every other command, so it completes and is listed.
    assert "plan" in agent_commands.names()
    assert agent_commands.SUMMARY["plan"] and agent_commands.USAGE["plan"]


# --- what the model is taught ----------------------------------------------

_EXAMPLE = re.compile(r"^\s*(\{.*\})\s*$", re.MULTILINE)


def test_every_plan_example_in_the_prompt_is_a_valid_action():
    """An example that broke a rule would teach breaking it. This is the same
    check the other reference sections already get."""
    found = 0
    for block in (agent_prompt.PLAN_REFERENCE, agent_prompt.PLANNING_RULES):
        for match in _EXAMPLE.finditer(block):
            obj = json.loads(match.group(1))
            assert agent_prompt.validate_action(obj) is None, obj
            assert obj["action"] == "plan", obj
            assert obj["operation"] in agent_plan.OPERATIONS, obj
            # Every action that does work carries a sentence about itself.
            assert obj.get("progress"), obj
            found += 1
    assert found >= 8, found


def test_the_plan_is_taught_to_the_main_agent_only():
    """A worker has no user to make a contract with. The isolation is code --
    WORKER_FORBIDDEN -- and the prompts agree with the code rather than being
    the only thing holding it."""
    import agent_subprompts
    main = agent_prompt.get_system_prompt()
    assert "=== THE PLAN" in main
    assert "WHEN TO PLAN" in main
    for prompt in (agent_subprompts.worker_prompt(), agent_subprompts.note_prompt()):
        assert "=== THE PLAN" not in prompt
        assert "WHEN TO PLAN" not in prompt


def test_the_prompt_states_that_the_runtime_enforces_the_contract():
    """Not "remember to finish the plan". The model is told the program will
    refuse it, because that is what actually happens."""
    reference = agent_prompt.PLAN_REFERENCE
    assert "WILL NOT LET YOU FINISH" in reference, reference[:400]
    assert "enforced by the program" in reference
    assert "MILESTONES THE USER WOULD RECOGNISE" in agent_prompt.PLANNING_RULES


# --- the gate, driven through the real loop ---------------------------------

def test_a_final_answer_is_refused_while_the_plan_is_unfinished():
    """The requirement the whole feature turns on, proved where it is enforced.

    The model makes a plan and then tries to answer with both steps
    outstanding. The answer is not shown to the user at all: it comes back as
    the model's own next input, naming the step it still owes, and the turn
    goes on. Every reply is different, so it is the GATE being tested and not
    the identical-reply circuit breaker, which would otherwise get there
    first.
    """
    early = "Everything is finished, I promise."
    replies = [json.dumps({"action": "plan", "operation": "create",
                           "steps": ["Inspect the repository", "Run the tests"]})]
    replies += [json.dumps({"action": "respond", "message": "%s (%d)" % (early, n)})
                for n in range(agent_config.rounds_for_effort() + 2)]
    drawn, seen, console = drive_session(["add the feature", "quit"], replies)

    # It was refused, and the refusal named the work rather than scolding.
    handed_back = seen[-1][-1]["content"]
    assert handed_back.startswith("BLOCKED"), handed_back
    assert "S1: Inspect the repository" in handed_back, handed_back
    assert "S2: Run the tests" in handed_back, handed_back
    # The user never saw the answer the model tried to give.
    assert early not in visible(drawn), visible(drawn)[-3000:]
    # And they were told the turn was still going, not left guessing.
    assert "Plan not finished" in visible(drawn), visible(drawn)[-3000:]


def test_the_answer_lands_once_every_step_is_complete():
    """The other half of the contract: the gate has to let go, and the moment
    it does the answer reaches the user exactly as it always did."""
    answer = "Added the feature and the suite is green."
    replies = [
        json.dumps({"action": "plan", "operation": "create",
                    "steps": ["Implement it", "Run the tests"]}),
        json.dumps({"action": "respond", "message": "too early"}),
        json.dumps({"action": "plan", "operation": "update", "step": 1,
                    "status": "completed"}),
        json.dumps({"action": "plan", "operation": "update", "step": 2,
                    "status": "completed"}),
        json.dumps({"action": "respond", "message": answer}),
    ]
    drawn, seen, console = drive_session(["add the feature", "quit"], replies)

    assert len(seen) == len(replies), len(seen)
    assert answer in visible(drawn), visible(drawn)[-3000:]
    # The refusal happened on the way, and it was the second request that met
    # it -- so the turn really did continue rather than starting again.
    assert seen[2][-1]["content"].startswith("BLOCKED"), seen[2][-1]["content"]


def test_a_batch_that_ends_in_an_early_answer_keeps_the_work_it_did():
    """A batch stopped at its final entry must not throw away the entries
    before it. Those results are real work and go back with the refusal, so
    the model is not asked to redo them."""
    replies = [
        json.dumps({"actions": [
            {"action": "plan", "operation": "create",
             "steps": ["Write the file", "Run the tests"]},
            {"action": "write_file", "path": "made.txt", "content": "hi\n"},
            {"action": "respond", "message": "All done already."}]}),
        json.dumps({"action": "plan", "operation": "update",
                    "steps": [{"step": 1, "status": "completed"},
                              {"step": 2, "status": "completed"}]}),
        json.dumps({"action": "respond", "message": "Wrote made.txt."}),
    ]
    drawn, seen, console = drive_session(["make a file", "quit"], replies)

    handed_back = seen[1][-1]["content"]
    assert "Batch results:" in handed_back, handed_back
    assert "made.txt" in handed_back, handed_back      # the write is not lost
    assert "BLOCKED" in handed_back, handed_back
    assert "All done already." not in visible(drawn), visible(drawn)[-2000:]
    assert "Wrote made.txt." in visible(drawn), visible(drawn)[-2000:]
