"""Tests for the capability commands: the parser, the state, and the guard.

Five things are being protected here and the first two are the whole feature.

The PARSER decides what a capability command is, and almost all of the risk is
in what it must NOT match. `verify` is an ordinary English word; `/verification`
and `/planning` are longer words that start with a command; `abc/verify` is a
path. Every one of those is a test, because a parser that is merely generous
turns the three most expensive things TMT can do into something that happens
by accident.

The STATE is a set of three booleans with exactly two ways to move: adopt from
the user's text, or retire. There is deliberately no setter that takes a name
and a value, and there is a test that says so, because a method that could be
handed ("plan", True) is a method a later edit can wire model output into.

The GUARD is what makes the flags authorisation rather than decoration. It is
asked at dispatch, it fails closed, and a context that never heard of
capabilities authorises nothing.

The COLUMN and the INPUT BOX are asserted with the escapes stripped, because
colour is confirmation here and never the message: the row still says
`/plan` and the field still says what the user typed. The gradient is asserted
as positions on TMT's one scale rather than as escape sequences, so the tests
describe the rule and not the palette.

The one thing NOT tested here is the loop: the runtime guard driven through
`execute_action`, the completion gate and the session's per-turn scoping are
in testing/integration/test_agent_authorization.py, because they need the
dispatcher and TMT.main.

Helpers come from test_agent_menu. A second harness for the same box would
drift from the first, and the drift would be silent.
"""

import io

import agent_capabilities as C
import agent_menu
import agent_panel
import agent_prompt
import agent_ui

from test_agent_menu import Terminal, visible


def enabled(text):
    """The capabilities a line of user text turns on, as a tuple."""
    return C.Capabilities(text).active()


def field_row(text, stream=None):
    """The typed row of a real PromptBox, painted, as it would be drawn."""
    box = agent_menu.PromptBox(stream=Terminal() if stream is None else stream)
    editor = agent_menu.LineEditor()
    editor.insert(text)
    rows = box.lines(editor, size=(100, 24))
    return [row for row in rows if visible(row).strip().startswith(">")][0]


def painted(text, stream=None):
    """Whether anything in the typed row carries an escape sequence."""
    return "\033" in field_row(text, stream)


class Cp1252(io.StringIO):
    """A console that claims to be a terminal it cannot draw on."""

    def isatty(self):
        return True

    @property
    def encoding(self):
        return "cp1252"


class NoColour(io.StringIO):
    """A real terminal with colour turned off. ANSI is safe here; colour is not."""

    def isatty(self):
        return True

    @property
    def encoding(self):
        return "utf-8"


# --- the parser, and the three commands it knows ----------------------------

def test_a_prompt_with_no_command_authorises_nothing():
    """The default, and the point of the feature. Substantial work, no
    commands, and none of the three is available."""
    for said in ("Build me an authentication system.", "Build it.",
                 "Fix this bug", "", "   "):
        assert enabled(said) == (), said


def test_each_command_turns_on_its_own_capability_and_no_other():
    """The independence requirement, stated one command at a time. None of
    the three implies either of the others."""
    assert enabled("Build it /plan") == ("plan",)
    assert enabled("Build it /review") == ("review",)
    assert enabled("Build it /verify") == ("verify",)


def test_the_pairs_and_the_full_set_are_exactly_what_was_asked_for():
    assert enabled("Build it /plan /review") == ("plan", "review")
    assert enabled("Build it /plan /verify") == ("plan", "verify")
    assert enabled("Build it /review /verify") == ("review", "verify")
    assert enabled("Build it /plan /review /verify") == ("plan", "review", "verify")


def test_the_order_they_are_written_in_does_not_change_the_answer():
    """Reported in one fixed order however they were typed, so nothing
    downstream can depend on the order of the user's words."""
    for said in ("/verify /review /plan Build it",
                 "Build /review it /plan and /verify",
                 "/plan Build it /verify /review"):
        assert enabled(said) == ("plan", "review", "verify"), said


def test_a_command_is_found_at_the_beginning_the_middle_and_the_end():
    assert enabled("/plan Build the feature") == ("plan",)
    assert enabled("Build the feature /plan") == ("plan",)
    assert enabled("Build the /plan feature") == ("plan",)
    assert enabled("Build the feature using /plan") == ("plan",)


def test_a_command_is_found_on_its_own_line():
    """Multiline input is a paste, and a paste is exactly where somebody puts
    the commands on their own lines under the request."""
    said = "Build the API.\nAdd authentication.\n/plan\n/review\n/verify"
    assert enabled(said) == ("plan", "review", "verify")
    # And with the line endings a Windows paste actually carries.
    assert enabled("Build it.\r\n/plan\r\n/verify") == ("plan", "verify")


def test_repeating_a_command_enables_it_once_and_not_twice():
    """Authorisation is a boolean. Two `/plan`s are not two plans, two plan
    tools or two capability states -- there is no such thing to be."""
    assert enabled("/plan Build this and then /plan update the plan.") == ("plan",)
    assert enabled("/review /review") == ("review",)
    assert enabled("/verify /verify /verify") == ("verify",)
    assert enabled("/plan /plan /review /verify /verify") == ("plan", "review", "verify")
    # Every occurrence is still reported for the UI to paint, which is a
    # different question from how many capabilities were turned on.
    assert len(C.spans("/plan /plan /review")) == 3


def test_the_command_is_recognised_however_it_is_capitalised():
    """Case-insensitive, matching `agent_commands.parse`, which has
    lower-cased command names since before this existed. A CLI that took
    /plan but not /Plan would be the odd one out in its own interface."""
    for said in ("/PLAN", "/Plan", "/pLaN", "Build this /PLAN"):
        assert enabled(said) == ("plan",), said
    assert enabled("/REVIEW /VERIFY") == ("review", "verify")


def test_the_users_own_text_is_never_rewritten():
    """The line is authorisation and it is also the task. Lower-casing it, or
    cutting the command out of it, would change what the model is asked."""
    said = "Build this /PLAN and be careful"
    granted = C.Capabilities(said)
    assert granted.source == said
    assert granted.plan
    # The span points at the characters as typed, so the box paints /PLAN.
    start, end, name = C.spans(said)[0]
    assert said[start:end] == "/PLAN", said[start:end]
    assert name == "plan"


# --- what must NOT be a command ---------------------------------------------
#
# The sharp edge of the whole feature. Everything below is something a person
# says while asking for ordinary work, and every one of them used to be enough
# to turn a capability on.

def test_the_word_verify_without_a_slash_authorises_nothing():
    """The requirement called out hardest in the brief, and the reason the
    slash exists at all: "verify" is an ordinary English word."""
    for said in ("verify", "VERIFY", "verify this code", "please verify this",
                 "verification", "verified", "verifying", "myverify",
                 "Build a login page verify", "can you verify the tests pass"):
        assert enabled(said) == (), said


def test_a_longer_word_beginning_with_a_command_is_not_that_command():
    """The trailing boundary. `/planning` is somebody naming a document."""
    for said in ("/planning", "/planner", "/plans", "/plan123", "/plan-b",
                 "/reviewing", "/reviewer", "/reviews", "/review123",
                 "/verification", "/verified", "/verifying", "/verify123"):
        assert enabled(said) == (), said


def test_a_command_inside_a_path_is_not_a_command():
    """The leading boundary. A slash in the middle of a token is a path
    separator, and TMT is a tool people paste paths into."""
    for said in ("abc/plan", "my/plan", "abc/verify", "src/review",
                 "/usr/local/plan", "docs/plan/readme.md", "a/review/b"):
        assert enabled(said) == (), said


def test_the_word_plan_and_the_word_review_without_a_slash_authorise_nothing():
    """The same rule as verify, for the other two. "make a plan" is how
    somebody describes the work, not how they buy the gate."""
    for said in ("plan", "planning", "make a plan for this",
                 "review", "reviewing", "review my code please",
                 "write a plan and review it"):
        assert enabled(said) == (), said


def test_a_command_may_sit_against_punctuation_but_not_against_a_word():
    """Punctuation cannot be part of a name, so allowing it cannot admit a
    longer word -- and a command inside a sentence is how people write."""
    assert enabled("Build it /plan.") == ("plan",)
    assert enabled("Build it /verify, please") == ("verify",)
    assert enabled("(/review)") == ("review",)
    assert enabled("Do it /plan!") == ("plan",)
    assert enabled('"/verify"') == ("verify",)
    # But a word character still ends it.
    assert enabled("/planx") == ()


def test_two_commands_run_together_are_neither_of_them():
    """`/plan/review` is a path shape, and reading it as both commands would
    make the leading-boundary rule mean nothing."""
    assert enabled("/plan/review") == ()
    assert enabled("/verify/plan") == ()


# --- the state, and the two ways it may move --------------------------------

def test_a_new_state_has_nothing_authorised():
    granted = C.Capabilities()
    assert not granted.any()
    assert granted.active() == ()
    assert not granted.plan and not granted.review and not granted.verify


def test_adopting_replaces_rather_than_accumulates():
    """The whole capability lifetime. A new prompt is parsed on its own, so
    nothing carries and nothing has to be expired."""
    granted = C.Capabilities("Build it /plan /verify")
    assert granted.active() == ("plan", "verify")
    granted.adopt("now change the button styling")
    assert granted.active() == (), granted.active()
    granted.adopt("and now /review it")
    assert granted.active() == ("review",)


def test_retiring_turns_everything_off_and_cannot_refuse():
    """Total on purpose. Turning authorisation OFF is never the dangerous
    direction, and a guarded retirement is what killed the session when the
    plan shipped -- `Plan.clear` was both the model's verb and the session's
    way of retiring a finished plan."""
    granted = C.Capabilities("/plan /review /verify")
    assert granted.retire() is None
    assert granted.active() == ()
    assert granted.source == ""
    granted.retire()            # again, on an empty one
    assert granted.active() == ()


def test_the_state_is_emptied_in_place_and_never_rebound():
    """The invariant the whole guard rests on. The agent loop puts THIS object
    in the action context before the turn starts, so a new one assigned later
    would leave the guard reading flags nothing writes to -- authorisation
    silently off, with no error anywhere."""
    granted = C.Capabilities("/plan")
    identity = id(granted)
    granted.adopt("/review")
    assert id(granted) is not None and id(granted) == identity
    granted.retire()
    assert id(granted) == identity


def test_there_is_no_way_to_set_a_capability_by_name():
    """Section 12, implemented as an absence. A method that could be handed
    ("plan", True) is a method a later edit can wire model output into, so the
    only two ways in are the user's text and total retirement."""
    granted = C.Capabilities()
    for attempt in ("enable", "set", "grant", "allow", "authorise", "authorize",
                    "turn_on", "note_choice"):
        assert not hasattr(granted, attempt), attempt
    # The flags themselves are read-only properties.
    for name in ("plan", "review", "verify"):
        raised = None
        try:
            setattr(granted, name, True)
        except Exception as error:
            raised = error
        assert raised is not None, name
        assert not granted.enabled(name), name


def test_enabled_answers_by_name_and_an_unknown_name_is_never_authorised():
    granted = C.Capabilities("/plan")
    assert granted.enabled("plan")
    assert granted.enabled("PLAN"), "the name is matched case-insensitively"
    assert not granted.enabled("review")
    for nonsense in ("", None, "spawn_agent", "git_push", "planning"):
        assert not granted.enabled(nonsense), nonsense


# --- the guard --------------------------------------------------------------

def test_an_authorised_capability_is_not_refused():
    granted = C.Capabilities("/plan /review /verify")
    for name in C.CAPABILITIES:
        assert C.refusal(granted, name) == "", name


def test_an_unauthorised_capability_is_refused_and_the_refusal_says_what_to_do():
    """A model told only "not permitted" reasonably goes looking for another
    route to the same effect, and there isn't one -- so the message names the
    user's command instead of leaving it to be guessed at."""
    granted = C.Capabilities("Build it")
    for name in C.CAPABILITIES:
        said = C.refusal(granted, name)
        assert said.startswith("REFUSED:"), said
        assert "/" + name in said, said
        assert "not enabled" in said, said
        assert "authorised by the user" in said, said
        # And it says what to do instead, rather than only what not to do.
        assert "ordinary actions" in said, said


def test_each_capability_is_refused_on_its_own():
    """Independence again, this time at the guard. Authorising one must not
    quietly let the other two through."""
    for granted_name in C.CAPABILITIES:
        granted = C.Capabilities("/" + granted_name)
        for name in C.CAPABILITIES:
            said = C.refusal(granted, name)
            if name == granted_name:
                assert said == "", (granted_name, name, said)
            else:
                assert said.startswith("REFUSED:"), (granted_name, name)


def test_no_capabilities_at_all_refuses_all_three():
    """Fails CLOSED, and it is the one guard in the loop that does. A turn
    whose authorisation could not be read has not authorised anything, and the
    cost of being wrong is a task done with the ordinary actions. It is also
    what a background agent's context looks like."""
    for nothing in (None, C.Capabilities()):
        for name in C.CAPABILITIES:
            assert C.refusal(nothing, name).startswith("REFUSED:"), name


def test_a_state_object_that_raises_authorises_nothing():
    """The same direction. Every other guard in the loop fails open because
    the worst outcome there is finished work held hostage; here the worst
    outcome is a permission nobody gave."""

    class Exploding:
        def enabled(self, name):
            raise RuntimeError("no")

    for name in C.CAPABILITIES:
        assert C.refusal(Exploding(), name).startswith("REFUSED:"), name


def test_the_guard_says_nothing_about_any_other_action():
    """It gates three verbs and no others. Every ordinary tool keeps working
    exactly as it did, whatever was authorised."""
    granted = C.Capabilities("Build it")
    for action in ("read_file", "write_file", "patch_file", "grep",
                   "glob", "code_map", "run_file", "git_commit",
                   "git_push", "spawn_agent", "respond", "done", "announce",
                   "tree", "related_tests", "remember", "delete_file"):
        assert C.refusal(granted, action) == "", action
    assert C.refusal(None, "read_file") == ""


# --- what the model is offered ----------------------------------------------

def test_the_allowed_and_gated_sets_are_exactly_complementary():
    """The prompt subtracts one and the guard permits the other, so a verb in
    neither -- or in both -- would be a capability taught and refused, or
    permitted and never described."""
    for text in ("", "/plan", "/review", "/verify", "/plan /review",
                 "/plan /verify", "/review /verify", "/plan /review /verify"):
        granted = C.Capabilities(text)
        allowed = C.allowed_actions(granted)
        gated = C.gated_actions(granted)
        assert set(allowed) | set(gated) == set(C.CAPABILITIES), text
        assert set(allowed) & set(gated) == set(), text
        assert allowed == granted.active(), text


def test_a_missing_state_gates_everything_and_allows_nothing():
    assert C.gated_actions(None) == C.CAPABILITIES
    assert C.allowed_actions(None) == ()


def test_the_prompt_teaches_only_what_was_authorised():
    """Layer one. An unauthorised verb is never described, so the model is not
    being asked to resist a tool it can see."""
    sections = {
        "plan": (agent_prompt.PLAN_REFERENCE, agent_prompt.PLANNING_RULES),
        "review": (agent_prompt.REVIEW_REFERENCE, agent_prompt.REVIEW_RULES),
        "verify": (agent_prompt.VERIFY_REFERENCE, agent_prompt.VERIFY_RULES),
    }
    for text in ("", "/plan", "/review", "/verify", "/plan /review",
                 "/plan /verify", "/review /verify", "/plan /review /verify"):
        granted = C.Capabilities(text)
        prompt = agent_prompt.get_system_prompt(granted)
        for name, blocks in sections.items():
            for block in blocks:
                if granted.enabled(name):
                    assert block in prompt, (text, name)
                else:
                    assert block not in prompt, (text, name)


def test_a_prompt_built_with_no_authorisation_teaches_none_of_the_three():
    """The default fails closed, the same direction the guard does. A prompt
    built without knowing what the user allowed must not be the one that
    teaches all three."""
    prompt = agent_prompt.get_system_prompt()
    for block in (agent_prompt.PLAN_REFERENCE, agent_prompt.REVIEW_REFERENCE,
                  agent_prompt.VERIFY_REFERENCE):
        assert block not in prompt


def test_the_prompt_names_what_was_withheld_and_who_can_enable_it():
    """Without this a model reaches for a verb the prompt is silent about and
    spends a round finding out it cannot. It is also the only way it can tell
    the user which command to type."""
    prompt = agent_prompt.get_system_prompt(C.Capabilities("/plan"))
    assert "CAPABILITIES YOU WERE NOT GIVEN" in prompt
    assert "/review" in prompt and "/verify" in prompt
    assert "You cannot enable one" in prompt
    # And nothing is withheld when everything was given.
    whole = agent_prompt.get_system_prompt(C.Capabilities("/plan /review /verify"))
    assert "CAPABILITIES YOU WERE NOT GIVEN" not in whole


def test_the_shared_rules_do_not_tell_a_gated_turn_to_plan():
    """The leak that hid in the rules rather than in the reference sections.

    `TOOL_CHOICE_RULES` and `WORKFLOW_RULES` each carried one line instructing
    the model to plan, and both blocks are on every main prompt AND reused by
    agent_subprompts -- so an unauthorised turn was told to plan by the very
    prompt that had left the plan verb out, and every background agent has
    always been told to plan something `WORKER_FORBIDDEN` refuses outright.
    """
    import agent_subprompts
    instructions = ("several stages", "STARTS with a plan")
    gated = agent_prompt.get_system_prompt(C.Capabilities("Build it"))
    for said in instructions:
        assert said not in gated, said
    allowed = agent_prompt.get_system_prompt(C.Capabilities("Build it /plan"))
    for said in instructions:
        assert said in allowed, said
    # And no background agent is told to plan, whatever the main turn asked.
    for prompt in (agent_subprompts.worker_prompt(),
                   agent_subprompts.note_prompt(),
                   agent_subprompts.review_prompt()):
        for said in instructions:
            assert said not in prompt, said


def test_the_planning_lines_cannot_be_dropped_by_an_edit_that_moves_them():
    """They are put back by an anchored replace, and a replace that silently
    found nothing is the failure that reads like a clean run. So the helper
    raises rather than returning the block unchanged."""
    raised = None
    try:
        agent_prompt._with_plan_rules("nothing like the table", "nor this")
    except AssertionError as error:
        raised = error
    assert raised is not None, "a missing anchor was accepted"
    # And the real blocks still carry both anchors.
    tools, flow = agent_prompt._with_plan_rules(agent_prompt.TOOL_CHOICE_RULES,
                                                agent_prompt.WORKFLOW_RULES)
    assert agent_prompt.PLAN_TOOL_ROW in tools
    assert agent_prompt.PLAN_BEHAVIOUR_RULE in flow


def test_the_prompt_is_cached_per_authorisation_and_not_shared_between_them():
    """Eight possible answers and one workspace snapshot. Handing a turn the
    previous turn's prompt would be the authorisation silently wrong."""
    first = agent_prompt.get_system_prompt(C.Capabilities("/plan"))
    other = agent_prompt.get_system_prompt(C.Capabilities("/review"))
    assert first != other
    assert first == agent_prompt.get_system_prompt(C.Capabilities("/plan"))
    # The same authorisation written differently is the same prompt, so the
    # cache is keyed on what was granted rather than on the words.
    assert first == agent_prompt.get_system_prompt(
        C.Capabilities("Build something else entirely /plan"))


# --- the column -------------------------------------------------------------

def rows_of(capabilities, width=30, height=None, stream=None):
    """The painted block, with the escapes stripped."""
    stream = Terminal() if stream is None else stream
    return [visible(row) for row in
            agent_panel.capabilities_rows(capabilities, width, height=height,
                                          stream=stream)]


def test_the_column_shows_only_what_is_active():
    """Rows for the three that are off would say nothing the absence of the
    block does not already say, and would be drawn on every turn."""
    assert rows_of(C.Capabilities("/review")) == ["CAPABILITIES 1", "● /review"]
    assert rows_of(C.Capabilities("/plan /verify")) == [
        "CAPABILITIES 2", "● /plan", "● /verify"]
    assert rows_of(C.Capabilities("/plan /review /verify")) == [
        "CAPABILITIES 3", "● /plan", "● /review", "● /verify"]


def test_the_column_draws_nothing_when_nothing_is_authorised():
    """Most turns. A permanent heading over three switches the user left alone
    would be a block in a column three other things want."""
    assert rows_of(C.Capabilities("Build it")) == []
    assert rows_of(C.Capabilities()) == []
    assert rows_of(None) == []


def test_the_column_states_the_capability_with_a_mark_as_well_as_a_colour():
    """Colour is never the message. Read it with the escapes stripped and
    nothing has been lost but confirmation -- the command is spelled out."""
    rows = rows_of(C.Capabilities("/plan"))
    assert "/plan" in rows[1]
    assert agent_panel._CAPABILITY_MARK in rows[1]


def test_the_column_takes_a_position_on_the_one_gradient():
    """A new element takes a place on the existing scale; it never gets a
    palette. Green is the settled end, and an authorised capability is a
    switch that is closed rather than something in progress."""
    assert agent_panel.CAPABILITY_POSITION == agent_panel.DONE_POSITION == 95
    painted_rows = agent_panel.capabilities_rows(
        C.Capabilities("/plan"), 30, stream=Terminal())
    red, green, blue = agent_ui._gradient(agent_panel.CAPABILITY_POSITION)
    assert "\033[38;2;%d;%d;%dm" % (red, green, blue) in painted_rows[0]


def test_the_column_degrades_where_the_terminal_cannot_draw():
    """The console is cp1252 on Windows through a pipe. Anything decorative
    has to survive that."""
    rows = [visible(row) for row in agent_panel.capabilities_rows(
        C.Capabilities("/plan /verify"), 20, stream=Cp1252())]
    assert rows == ["CAPABILITIES 2", "+ /plan", "+ /verify"], rows
    for row in rows:
        row.encode("cp1252")     # raises if anything unencodable got through


def test_the_column_fits_every_width_and_never_overflows():
    granted = C.Capabilities("/plan /review /verify")
    for width in range(8, 40):
        for row in rows_of(granted, width=width):
            assert agent_ui.display_width(row) <= width, (width, row)


def test_the_column_gives_up_commands_before_it_gives_up_the_count():
    """The header counts every capability rather than the ones that fit, so
    `CAPABILITIES 3` over two rows has already said one is not drawn."""
    granted = C.Capabilities("/plan /review /verify")
    assert rows_of(granted, height=2) == ["CAPABILITIES 3", "● /plan"]
    assert rows_of(granted, height=3) == ["CAPABILITIES 3", "● /plan", "● /review"]
    # Below two rows a header alone is a word with no news in it.
    assert rows_of(granted, height=1) == []
    assert rows_of(granted, height=0) == []


def test_a_capability_object_that_raises_is_drawn_as_nothing():
    """Decoration is never allowed to end a turn."""

    class Exploding:
        def active(self):
            raise RuntimeError("no")

    assert rows_of(Exploding()) == []


def test_the_report_names_every_capability_and_what_would_enable_it():
    """The unambiguous alternate to the column, and the only way in on a
    terminal too narrow for two columns."""
    said = agent_panel.capabilities_report(C.Capabilities("/review"))
    assert "CAPABILITIES 1/3" in said, said
    assert "+ /review  enabled" in said, said
    assert "- /plan" in said and "not enabled" in said, said
    empty = agent_panel.capabilities_report(C.Capabilities("Build it"))
    assert "CAPABILITIES 0/3" in empty, empty
    assert "cannot enable these itself" in empty, empty


# --- the input box ----------------------------------------------------------

def test_a_command_in_the_field_is_painted_and_ordinary_text_is_not():
    """Live detection: the box is drawn from the buffer on every frame, so
    what is asserted here is exactly what a keystroke produces."""
    for said in ("/plan", "/review", "/verify", "Build auth /plan",
                 "/plan Build auth", "Build /review this",
                 "Build it /plan /review /verify"):
        assert painted(said), said


def test_the_field_never_paints_something_that_is_not_a_command():
    """The same negative list the parser has, asserted where the user can
    actually see it. A highlighted `verify` would be TMT saying it had
    understood a command nobody typed."""
    for said in ("verify", "verify this code", "please verify this",
                 "verification", "verified", "myverify", "/verification",
                 "/verify123", "abc/verify", "/planning", "/reviewing",
                 "make a plan and review it", "Build a login page verify"):
        assert not painted(said), said


def test_only_the_command_itself_is_painted_and_never_the_words_around_it():
    """Token-aware. The escapes begin at the slash and end at the last
    character of the name."""
    row = field_row("Build the /plan feature")
    assert visible(row).strip() == "> Build the /plan feature", visible(row)
    before, marked = row.split("\033", 1)
    assert before.endswith("Build the "), before
    # Everything after the token's reset is plain again.
    assert row.endswith(" feature"), row[-40:]


def test_every_occurrence_is_painted_even_though_one_capability_is_enabled():
    """A token the user can see and TMT has not painted reads as a token TMT
    did not understand, which is the opposite of what the colour is for."""
    row = field_row("/plan do this /plan and that")
    assert row.count(agent_ui.RESET) == 2, row
    assert visible(row).strip() == "> /plan do this /plan and that"


def test_the_painted_field_reads_exactly_as_it_was_typed():
    """The user's text is styled, never rewritten. `/PLAN` stays `/PLAN`."""
    for said in ("Build this /PLAN", "Build it /plan /review /verify",
                 "/Verify the thing"):
        assert visible(field_row(said)).strip() == "> " + said, said


def test_the_gradient_runs_red_through_orange_to_green_across_the_token():
    """The progression the user asked for, asserted as positions on TMT's one
    scale rather than as a palette."""
    row = field_row("/plan")
    assert "\033[38;2;220;38;38m" in row, "the slash is red"
    assert "\033[38;2;74;222;128m" in row, "the last character is green"
    # And the middle really passes through the warm end rather than jumping.
    middle = [chunk for chunk in row.split("\033[38;2;") if chunk[:3].isdigit()]
    reds = [int(chunk.split(";")[0]) for chunk in middle]
    greens = [int(chunk.split(";")[1]) for chunk in middle]
    assert reds[0] > reds[-1], reds
    assert greens[0] < greens[-1], greens


def test_the_paint_is_deterministic_and_two_frames_are_byte_identical():
    """The flicker fix and this feature are the same fix. The box repaints
    only when its frame differs from the last one, so a gradient that read the
    clock would walk the caret to the foot of the box and back twelve times a
    second -- on the one surface being typed into."""
    box = agent_menu.PromptBox(stream=Terminal())
    editor = agent_menu.LineEditor()
    editor.insert("Build it /plan /verify")
    first = box.lines(editor, size=(100, 24))
    second = box.lines(editor, size=(100, 24))
    assert first == second, "two frames of an untouched box must be equal"
    # And no clock is consulted on the way.
    assert agent_menu.CAPABILITY_PHASE == 0.0


def test_editing_the_line_adds_and_removes_the_paint():
    """Live editing, driven through the real editor rather than asserted
    about it: typing a command paints it and deleting one unpaints it."""
    box = agent_menu.PromptBox(stream=Terminal())
    editor = agent_menu.LineEditor()
    editor.insert("Build this")
    assert "\033" not in box.lines(editor, size=(100, 24))[1]
    editor.insert(" /plan")
    row = [r for r in box.lines(editor, size=(100, 24)) if visible(r).strip().startswith(">")][0]
    assert "\033" in row
    # Backspacing the name away takes the paint with it. Driven through the
    # real keystroke path, so this is what the key actually does.
    for _ in range(len("plan")):
        editor.handle("key", "backspace")
    row = [r for r in box.lines(editor, size=(100, 24)) if visible(r).strip().startswith(">")][0]
    assert "\033" not in row, visible(row)
    # And turning it into something that is not a command leaves it plain.
    editor.insert("verification")
    row = [r for r in box.lines(editor, size=(100, 24)) if visible(r).strip().startswith(">")][0]
    assert "\033" not in row, visible(row)


def test_a_pasted_block_is_detected_across_its_lines():
    """A paste is exactly where somebody puts the commands under the request,
    and the field holds the block whole."""
    box = agent_menu.PromptBox(stream=Terminal())
    editor = agent_menu.LineEditor()
    editor.insert(agent_menu.normalize_paste("Build the API.\n/plan\n/verify"),
                  pasted=True)
    assert C.names_in(editor.expanded()) == ("plan", "verify")


def _painted_rows(text, inner=20):
    """Every visible row of a field this wide, painted the way `_field` does."""
    rows = agent_menu.visible_field_rows(text, len(text), inner)[0]
    spans = C.spans(text)
    return [agent_menu.paint_capabilities(body, start, spans, Terminal())
            for start, body in rows]


def test_a_command_split_across_two_wrapped_rows_is_painted_on_both():
    """The field wraps at a column and not at a word, so a row is an arbitrary
    slice of the line. This one breaks `/plan` between the slash and the name,
    and both halves have to be found -- which only works because the spans are
    matched against the whole line and mapped back onto the rows."""
    text = "x" * 17 + " /plan"
    rows = agent_menu.visible_field_rows(text, len(text), 20)[0]
    assert len(rows) == 2 and rows[1][1] == "plan", rows
    painted_rows = _painted_rows(text)
    assert sum("\033" in row for row in painted_rows) == 2, painted_rows
    assert "".join(visible(row) for row in painted_rows) == text


def test_a_wrapped_row_cannot_invent_a_command_the_line_does_not_contain():
    """The other half of the same rule, and the reason row-local matching
    would be wrong: `abc/plan` is a path, and wrapping it so that `/plan`
    begins a row must not turn it into a command."""
    text = "x" * 15 + "abc/plan"
    assert C.spans(text) == (), C.spans(text)
    painted_rows = _painted_rows(text)
    assert len(painted_rows) > 1, painted_rows
    assert not any("\033" in row for row in painted_rows), painted_rows
    assert "".join(visible(row) for row in painted_rows) == text


def test_a_terminal_with_no_colour_still_marks_the_command():
    """NO_COLOR on a real terminal: ANSI is safe here and colour is not.

    Weight and a rule rather than a hue, so what the colour was
    distinguishing is still distinguished. It reads correctly with every
    escape stripped either way -- the row still says `/plan` -- so this is a
    courtesy on top of a line that was already legible, not the thing holding
    the meaning up.
    """
    import os
    previous = os.environ.get("NO_COLOR")
    os.environ["NO_COLOR"] = "1"
    try:
        row = field_row("Build it /plan", stream=NoColour())
    finally:
        if previous is None:
            del os.environ["NO_COLOR"]
        else:
            os.environ["NO_COLOR"] = previous
    assert agent_ui.BOLD in row and agent_ui.UNDERLINE in row, repr(row)
    assert "38;2;" not in row, "no colour was asked for"
    assert visible(row).strip() == "> Build it /plan"


def test_a_piped_run_gets_no_escapes_at_all():
    """Every scripted, redirected and tested run. A pipe is not a terminal, so
    ANSI would land in whatever captured it -- and the row still reads
    `/plan`, which is the command spelled out."""
    row = field_row("Build it /plan /review", stream=io.StringIO())
    assert "\033" not in row, repr(row)
    assert row.strip() == "> Build it /plan /review"


def test_the_placeholder_is_never_painted():
    """It is TMT's own words standing in for an empty field, so a command
    highlighted in it would be marking something the user never typed."""
    box = agent_menu.PromptBox(stream=Terminal())
    editor = agent_menu.LineEditor("Describe your next task /plan")
    rows = box.lines(editor, size=(100, 24))
    row = [r for r in rows if visible(r).strip().startswith(">")][0]
    # Dimmed as one whole row, with no gradient inside it.
    assert "\033[38;2;220;38;38m" not in row, row


def test_a_token_cut_off_by_the_width_still_closes_its_colour():
    """Fit first, paint second -- and the half of that rule that only shows at
    a narrow width. A row trimmed through the middle of an escape leaves half
    of one on the screen, which the terminal swallows along with whatever came
    after it. Here the trim happens while the row is still plain, so a clipped
    token is a shorter token and its RESET is still written."""
    import agent_session
    session = agent_session.Session()
    session.begin_turn("x /plan", "prompt")
    for width in range(28, 62, 2):
        box = agent_menu.PromptBox(stream=Terminal(), session=session)
        editor = agent_menu.LineEditor()
        editor.insert("Build the login page /plan and /verify it")
        for row in box.lines(editor, size=(width, 24)):
            if "\033" not in row:
                continue
            # Whatever follows the last reset must carry no colour of its own.
            tail = row.rsplit(agent_ui.RESET, 1)[-1]
            assert "38;2;" not in tail, (width, repr(row))


def test_the_painted_box_never_overflows_its_column():
    """The composed region measures painted rows with `visible_width`, and a
    row drawn to the last column wraps on the terminals that auto-wrap and
    costs a screen line the repaint arithmetic does not know about."""
    import agent_session
    session = agent_session.Session()
    session.begin_turn("Build it /plan /verify", "prompt")
    session.plan.create(["Inspect", "Implement", "Test"])
    for width in (100, 60, 46, 45, 44, 30, 26):
        box = agent_menu.PromptBox(stream=Terminal(), session=session)
        editor = agent_menu.LineEditor()
        editor.insert("Build the login page /plan and /verify it")
        for row in box.lines(editor, size=(width, 24)):
            assert agent_ui.visible_width(row) <= width - 1, (width, repr(row))


def test_painting_never_changes_how_wide_a_row_is():
    """Measured, never counted. The composed region and the caret arithmetic
    both depend on the row being exactly as wide as its text."""
    for said in ("/plan", "Build it /plan /review /verify", "no commands here"):
        row = field_row(said)
        assert agent_ui.visible_width(row) == agent_ui.display_width(visible(row)), said


# --- the one parser ---------------------------------------------------------

def test_the_dispatcher_and_the_parser_name_the_same_three_verbs():
    """Two spellings of one set are two chances for one to grow a member the
    other does not know about, and the member that went missing would be a
    capability running unauthorised."""
    import agent_actions
    assert agent_actions._CAPABILITY_ACTIONS == frozenset(C.CAPABILITIES)


def test_the_commands_the_box_offers_include_all_three():
    """They are still slash commands in their own right -- the read-only
    reports -- so the completion list must still carry them."""
    import agent_commands
    assert agent_commands._CAPABILITY_COMMANDS == frozenset(C.CAPABILITIES)
    for name in C.CAPABILITIES:
        assert name in agent_commands.names(), name


def test_every_capability_has_a_summary_and_a_command_spelling():
    """The refusal, the withheld notice and the report all read from these."""
    for name in C.CAPABILITIES:
        assert C.SUMMARY.get(name), name
        assert C.command(name) == "/" + name, name
