"""What the agents are costing, and saying so between tool calls.

Two things the interface was not reporting. A background agent did real work
and the session's own meter read +0, because the lines were counted on a
thread the meter had never heard of. And the main agent ran tools in silence:
three reads in a row with nothing said between them, which from outside is
indistinguishable from a stuck loop.

Both are readouts rather than work, so nothing here may ever be able to fail a
turn -- and the tests say so as much as they say the figures are right.
"""

import io
import re
import time

import agent_actions
import agent_manager
import agent_menu
import agent_panel
import agent_prompt
import agent_worker
from agent_session import Session
from agent_ui import cycle_bar, strip_ansi


class Tty(io.StringIO):
    """A stream that claims to be a terminal, so colour is actually painted."""

    def isatty(self):
        return True

    @property
    def encoding(self):
        return "utf-8"


# --- the row under the main progress bar ------------------------------------

def test_an_agent_row_carries_the_bar_lines_tokens_and_elapsed():
    """One row per agent, under the main bar. Everything on it is measured:
    the lines come off the same `action_event` detail the session's own meter
    reads, the tokens are the provider's figure where it gave one, and the
    elapsed time is real."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("write the parser")
    record.status = agent_manager.Status.RUNNING
    record.started_at = time.monotonic() - 47
    manager.set_steps(record.id, 12, 40)
    manager.add_lines(record.id, 45, 3)
    manager.set_tokens(record.id, tokens_out=4200, output_exact=True)

    row = strip_ansi(agent_panel.agent_status_row(record, 80))
    assert "#%s" % record.id in row, row
    assert "+45 -3" in row, row
    assert "4k out" in row and "~4k" not in row, row   # exact, so unmarked
    assert "47s" in row, row
    assert "running" in row, row


def test_an_estimated_agent_figure_says_it_is_estimated():
    """The rule everywhere in TMT: a guessed number says it is guessed."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("t")
    manager.set_tokens(record.id, tokens_out=900, output_exact=False)
    assert "~900 out" in strip_ansi(agent_panel.agent_status_row(record, 80))


def test_a_narrow_row_gives_up_its_parts_from_the_right():
    """It loses the state word before the tokens and the tokens before the
    identity. What it never loses is which agent it is: a row that cannot say
    that is not worth drawing."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("t")
    record.status = agent_manager.Status.RUNNING
    manager.add_lines(record.id, 45, 3)
    manager.set_tokens(record.id, tokens_out=4200, output_exact=True)

    wide = strip_ansi(agent_panel.agent_status_row(record, 80))
    narrow = strip_ansi(agent_panel.agent_status_row(record, 34))
    assert "running" in wide and "running" not in narrow, (wide, narrow)
    assert "#%s" % record.id in narrow, narrow
    # It DROPS the field it cannot fit rather than clipping the row through
    # the middle of one. `fit_to_width` leaves no marker behind, so a merely
    # truncated row looks identical to a shortened one except that it ends
    # mid-word -- which is the whole thing being tested here. Asserted against
    # the set of legal renderings rather than against a suffix, because at
    # some widths a truncation lands on a space and a suffix check passes.
    legal = {"#%s  +45 -3  4k out  0s  running" % record.id,
             "#%s  +45 -3  4k out  0s" % record.id,
             "#%s  +45 -3  0s" % record.id,
             "#%s  +45 -3" % record.id,
             "#%s" % record.id,
             ""}
    for columns in (20, 26, 34, 38, 44, 50, 80, 120):
        row = strip_ansi(agent_panel.agent_status_row(record, columns))
        assert agent_menu.display_width(row) <= columns - 1, (columns, row)
        # Everything after the eight-cell bar and its one space.
        assert row[9:] in legal, (columns, repr(row[9:]))


def test_an_agent_bar_shows_budget_spent_and_never_claims_completion():
    """The bar fills with the step budget spent, which is a measurement. It is
    NOT a guess at how close the agent is to finishing -- nothing can know
    that, and a bar implying it would be inventing the one figure nobody has.
    A terminal agent is full because it is over, which is the one moment
    completion actually is known."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("t")
    assert agent_panel.agent_progress(record) == 0

    manager.set_steps(record.id, 20, 40)
    assert agent_panel.agent_progress(record) == 50

    # Never 100 while it is still going, however much budget is gone.
    record.status = agent_manager.Status.RUNNING
    manager.set_steps(record.id, 40, 40)
    assert agent_panel.agent_progress(record) == 99

    manager.complete(record.id, "done")
    assert agent_panel.agent_progress(record) == 100


def test_the_agent_bar_is_the_neutral_ramp_and_not_the_gradient():
    """The colour gradient means "the main agent is working, and this is how
    far along it is". Painting a worker's bar with it would say the same thing
    about a different thing on the row below, and at a glance five gradient
    bars read as one process reported five times. The agents get the absence
    of a colour rather than a colour of their own."""
    stream = Tty()
    neutral = agent_panel.neutral_bar(100, stream, width=8)
    gradient = cycle_bar(100, stream, width=8)

    greys = re.findall(r"38;2;(\d+);(\d+);(\d+)m", neutral)
    assert greys, neutral
    for red, green, blue in greys:
        assert red == green == blue, (red, green, blue)

    coloured = re.findall(r"38;2;(\d+);(\d+);(\d+)m", gradient)
    assert any(not (r == g == b) for r, g, b in coloured), gradient

    # It still reads with the colour stripped, because colour is never the
    # message: the filled cells are there whether or not anything is painted.
    assert strip_ansi(neutral).count("█") == 8, strip_ansi(neutral)
    assert strip_ansi(agent_panel.neutral_bar(0, stream, width=8)).count("█") == 0


def test_the_rows_appear_only_when_there_are_agents():
    """A session that never delegates draws exactly the screen it drew before
    any of this existed."""
    assert agent_panel.agent_status_rows((), 80) == []
    assert agent_panel.agent_status_rows(None, 80) == []


# --- the meter above the input box ------------------------------------------

def test_the_meter_counts_lines_a_background_agent_wrote():
    """A line a worker wrote is a line this session wrote. The user asked for
    one file and got one file; which thread held the pen is an implementation
    detail, and a meter reading +0 while five workers rewrote the project
    would be telling the truth about the main thread and a lie about the
    session."""
    stream = Tty()
    session = Session()
    session.lines_added, session.lines_removed = 10, 2
    session.tokens_in = 11000
    session.record_reply("x" * 40, 300)

    alone = strip_ansi(agent_menu.meter_text(session, stream, columns=100))
    assert "+10 lines, -2 lines" in alone, alone

    manager = agent_manager.AgentManager()
    record = manager.spawn("t")
    manager.add_lines(record.id, 45, 3)
    manager.set_tokens(record.id, tokens_out=4200, output_exact=True)

    together = strip_ansi(agent_menu.meter_text(session, stream, columns=100,
                                                manager=manager))
    assert "+55 lines, -5 lines" in together, together
    # The agents' spend is reported and kept OUT of the context figure: that
    # one is how full the window of the request in flight is, and five workers
    # added into it would describe a context that does not exist.
    assert "~11k context" in together, together
    # "agents" leads the figure: "22k agent", the other way round and
    # shortened, reads as a count of agents rather than as what they cost.
    assert "agents ~4k tokens" in together, together


def test_the_meter_is_unchanged_when_nothing_has_been_delegated():
    """The row a session without agents draws is exactly the row it drew
    before any of this existed."""
    stream = Tty()
    session = Session()
    session.lines_added, session.lines_removed = 10, 2
    session.tokens_in = 11000
    session.record_reply("x" * 40, 300)

    without = agent_menu.meter_text(session, stream, columns=100)
    empty = agent_menu.meter_text(session, stream, columns=100,
                                  manager=agent_manager.AgentManager())
    assert without == empty, (without, empty)


def test_a_register_that_cannot_answer_costs_the_row_its_figures_not_the_row():
    """The meter is a readout. A broken register must never be able to take
    the whole line down with it."""
    class Broken:
        def totals(self, now=None):
            raise RuntimeError("no")

    stream = Tty()
    session = Session()
    session.lines_added = 10
    session.tokens_in = 11000
    text = agent_menu.meter_text(session, stream, columns=100, manager=Broken())
    assert "+10 lines" in strip_ansi(text), text


def test_a_worker_reports_the_lines_it_wrote_by_the_same_rule_as_the_main_loop():
    """Counted through `agent_actions.action_event`, so a worker's line is
    counted by exactly the rule a main-agent line is -- including the awkward
    case: a write over an existing file reports only what it wrote, because
    what it replaced was gone before anyone could count it."""
    manager = agent_manager.AgentManager()
    record = manager.spawn("t")

    agent_worker._record_paths(manager, record, "write_file",
                               {"path": "new.py", "content": "a\nb\nc\n"},
                               "Created file: new.py")
    assert (record.lines_added, record.lines_removed) == (3, 0), record.lines_added

    # An overwrite knows only what it wrote, so it adds nothing rather than
    # claiming it removed nothing.
    before = (record.lines_added, record.lines_removed)
    agent_worker._record_paths(manager, record, "write_file",
                               {"path": "old.py", "content": "x\ny\n"},
                               "Wrote file: old.py")
    assert (record.lines_added, record.lines_removed) == before, record.lines_added

    # A read contributes nothing at all.
    agent_worker._record_paths(manager, record, "read_file",
                               {"path": "old.py"}, "contents")
    assert (record.lines_added, record.lines_removed) == before

    # A patch knows both halves.
    agent_worker._record_paths(manager, record, "patch_file",
                               {"path": "p.py", "search": "a\nb\n",
                                "replace": "c\n"}, "Patched file: p.py")
    assert record.lines_removed == before[1] + 2, record.lines_removed


def test_a_running_worker_reports_its_steps_so_its_bar_moves():
    """The bar is fed by `set_steps` from inside the worker's own loop. Nothing
    else calls it, so without that call every agent row would sit at zero for
    its whole life and the strip would look like five stalled processes."""
    import json

    manager = agent_manager.AgentManager()
    record = manager.spawn("do three things")
    seen = []

    replies = [
        json.dumps({"action": "read_file", "path": "a.py",
                    "progress": "Reading a.py."}),
        json.dumps({"action": "read_file", "path": "b.py",
                    "progress": "Reading b.py now, for what a.py referred to."}),
        json.dumps({"action": "internal_response", "response": "Read both."}),
    ]

    def ask(messages, on_event=None, model=None, max_tokens=None, quiet=False):
        # Recorded as the loop sees it, before this step's action runs, so the
        # figure asserted below is the one a repaint would actually have read.
        seen.append(record.steps)
        return replies[min(len(seen) - 1, len(replies) - 1)]

    answer = agent_worker.run_worker(record, manager, ask=ask,
                                     execute=lambda obj, ctx: "contents",
                                     system_prompt="test prompt")

    assert answer == "Read both.", answer
    # It climbed, one per step, rather than staying where it started.
    assert seen == [0, 1, 2], seen
    assert record.max_steps > 0, record.max_steps
    # And the bar it feeds moved with it. Driven through `run_worker` directly
    # rather than through `manager.start`, so the record is deliberately not
    # terminal here -- it is the wrapper in `start` that marks it completed --
    # and the bar therefore shows the budget spent rather than the full row a
    # finished agent gets.
    assert not record.is_terminal(), record.status
    assert agent_panel.agent_progress(record) == round(100.0 * 2 / record.max_steps)


def test_agent_totals_mark_the_whole_readout_estimated_if_any_half_was():
    """A total mixing one measured number with one guessed number is a guess."""
    manager = agent_manager.AgentManager()
    first = manager.spawn("a")
    manager.set_tokens(first.id, tokens_in=100, tokens_out=200,
                       input_exact=True, output_exact=True)
    assert manager.totals()["exact"] is True

    second = manager.spawn("b")
    manager.set_tokens(second.id, tokens_out=50, output_exact=False)
    assert manager.totals()["exact"] is False
    assert manager.totals()["tokens"] == 350


def test_totals_count_only_running_agents_but_all_of_their_work():
    """`agents` is what a "2 agents" readout means to somebody watching. The
    other figures cover every agent this session has had, because the work a
    finished one did did not finish with it."""
    manager = agent_manager.AgentManager()
    running = manager.spawn("a")
    running.status = agent_manager.Status.RUNNING
    manager.add_lines(running.id, 5, 0)

    finished = manager.spawn("b")
    manager.add_lines(finished.id, 7, 2)
    manager.complete(finished.id, "done")

    totals = manager.totals()
    assert totals["agents"] == 1, totals
    assert totals["lines_added"] == 12, totals
    assert totals["lines_removed"] == 2, totals


# --- saying what you are doing between tool calls ---------------------------

def test_an_action_with_no_progress_is_reminded_rather_than_failed():
    """Taught in the prompt and skipped anyway. The reminder rides on the
    result so it costs no turn and cannot fail one: `validate_action` still
    does not require "progress", because rejecting an action that ran and did
    its job would throw the work away over a missing sentence."""
    silent = agent_actions.build_result_message(
        "read_file", "contents", {"action": "read_file", "path": "a.py"})
    assert 'No "progress"' in silent, silent
    assert "read_file" in silent, silent
    # The action still ran: the reminder is appended to a real result rather
    # than put in place of one.
    assert "contents" in silent, silent

    spoke = agent_actions.build_result_message(
        "read_file", "contents",
        {"action": "read_file", "path": "a.py", "progress": "Checking a.py."})
    assert 'No "progress"' not in spoke, spoke

    # An events entry is a public record too, so a model that reported its
    # work the other offered way is not nagged for it.
    via_events = agent_actions.build_result_message(
        "read_file", "contents",
        {"action": "read_file", "path": "a.py",
         "events": [{"type": "file_read", "message": "Read a.py"}]})
    assert 'No "progress"' not in via_events, via_events

    # The two that ARE the thing being said are never reminded. There used to
    # be three; the rename collapsed them, and `_SPEAKS_FOR_ITSELF` is now the
    # same two names the loop's own terminal test and the prompt's exception
    # list use.
    for action in ("send_message", "end_conversation"):
        message = agent_actions.build_result_message(action, "ok",
                                                     {"action": action})
        assert 'No "progress"' not in message, (action, message)


def test_the_reminder_is_absent_when_no_object_is_available_to_judge():
    """Called the old way, with no object, it must behave exactly as it did.
    Nagging about an action nobody described would be inventing a fault."""
    assert 'No "progress"' not in agent_actions.build_result_message(
        "read_file", "contents")


def test_the_missing_progress_reminder_never_displaces_a_correction():
    """A failed action's message is the correction the model needs. The
    reminder is appended after it, never instead of it."""
    message = agent_actions.build_result_message(
        "patch_file", "Search text not found in a.py",
        {"action": "patch_file", "path": "a.py"})
    assert message.startswith("FAILED:"), message
    assert "didn't match exactly" in message, message


def test_progress_is_still_not_required_to_validate():
    """The line this deliberately does not cross. An action without progress
    is a valid action; enforcing a presentation rule in the validator would
    turn a missing sentence into a failed turn and lose the work."""
    assert agent_prompt.validate_action(
        {"action": "read_file", "path": "a.py"}) is None


def test_the_prompt_names_the_silent_tool_call_as_the_failure_to_avoid():
    """The rule is broken by omission rather than by writing a bad sentence,
    so the prompt has to show the omission."""
    rules = agent_prompt.PROGRESS_RULES
    for number in ("3d.", "3e.", "3f."):
        assert number in rules, number
    # Asserted on what the rules SAY, not on their numbering. A rule that
    # kept its number and lost its content would otherwise pass this.
    assert "GAP BETWEEN TWO TOOL CALLS" in rules, rules[:400]
    assert "broken by omission" in rules, rules[:400]
    # It shows the omission rather than only describing it, because that is
    # the shape the mistake actually takes on screen.
    assert "Read File multiply.py" in rules, rules[:400]
    # It names delegation specifically: a background agent's own actions are
    # never shown to the user, so an unnarrated spawn is invisible work.
    for verb in ("spawn_agent", "wait_for_agents"):
        assert verb in rules, verb
    assert "NEVER shown to the user" in rules, rules[:400]
