"""`agent_ask` on its own: the shape of a question, and the words it answers with.

The module reads no terminal, so a test needs none: everything here is a
question object, a rendered block of text, and the sentence a keystroke turns
into. Which key actually arrived is the session's to find out, and
`test_agent_ask_wiring` drives that half against a fake console.

What is pinned, and why each is worth a test of its own:

- there are THREE outcomes, not two. None means nobody was there to ask, ""
  means a person was there and pressed Esc, and a key means they chose. Telling
  a model "the user dismissed your question" about a piped run would have it
  reasoning about somebody who was never in the room;
- the keys ARE the numbering. Option 1 is "1", so there is no second numbering
  to drift out of step with the one on screen -- the rule `agent_plan` settled
  for its steps;
- a digit past the last option is not an answer, because a question that
  accepted a 4 it never offered would record a choice nobody made;
- every refusal names what to do instead, because a model handed "invalid"
  spends a retry guessing and the retry budget is for real mistakes;
- the rendered block wraps on words and reads with every escape stripped,
  because it is prose somebody has to read before they can answer it.
"""

import agent_ask as A


def question(*options, **keys):
    text = keys.pop("question", "What should the database layer use?")
    return A.parse({"question": text, "options": list(options)})


# --- the shape ---------------------------------------------------------------

def test_a_well_formed_request_parses_and_keeps_its_options_in_order():
    made, refused = question("use nodejs", "use standard python", "something else")
    assert refused == ""
    assert made.options == ("use nodejs", "use standard python", "something else")
    assert made.keys() == ("1", "2", "3")
    assert made.key_list() == "1, 2 or 3"


def test_two_options_is_the_fewest_and_five_the_most():
    for count in range(A.MIN_OPTIONS, A.MAX_OPTIONS + 1):
        made, refused = question(*["option %d" % n for n in range(count)])
        assert refused == "", count
        assert len(made.keys()) == count
        assert made.keys() == A.KEYS[:count]
    made, refused = question("only one")
    assert made is None and "at least 2" in refused, refused
    made, refused = question(*["option %d" % n for n in range(6)])
    assert made is None and "at most 5" in refused and "given 6" in refused, refused


def test_the_key_is_the_number_on_screen_and_there_is_no_second_numbering():
    """`agent_plan` settled this for its steps: a display numbering and an
    internal numbering are two numberings that drift, and the model then
    answers about a choice the user cannot see."""
    made, _ = question("first", "second", "third")
    rendered = A.render(made, 80)
    for position, option in enumerate(made.options, start=1):
        assert "%d. %s" % (position, option) in rendered, option
        assert made.index_for(str(position)) == position - 1
        assert made.chosen(position - 1).startswith('The user chose %d: "%s"'
                                                    % (position, option))


def test_a_digit_past_the_last_option_is_not_an_answer():
    """A question that accepted a 4 it never offered would be recording a
    choice nobody made."""
    made, _ = question("first", "second")
    assert made.index_for("3") is None
    assert made.index_for("5") is None
    for other in ("a", "", " ", "0", "12", None, "\x1b"):
        assert made.index_for(other) is None, repr(other)


# --- the three outcomes ------------------------------------------------------

def test_nobody_there_and_a_dismissal_are_different_answers():
    """They look alike from inside the loop and are entirely different facts.
    A model told "the user dismissed your question" about a piped run would be
    reasoning about somebody who was never in the room."""
    made, _ = question("first", "second")
    assert A.answer(made, None) == A.NO_TERMINAL
    assert A.answer(made, "") == A.DISMISSED
    assert A.NO_TERMINAL != A.DISMISSED
    # And each says what to do next, because a model with no answer and no
    # instruction asks again.
    assert "Do not ask again" in A.NO_TERMINAL
    assert "Do not ask the same thing again" in A.DISMISSED
    assert "could not be put" in A.NO_TERMINAL


def test_a_chosen_option_comes_back_with_both_its_number_and_its_text():
    """The number alone would make the model count the list again to find out
    what it meant; the text alone would lose the correspondence with what the
    user actually pressed, which is the one fact this action delivers."""
    made, _ = question("use nodejs", "use standard python")
    said = A.answer(made, "2")
    assert "2" in said and "use standard python" in said, said
    assert "do not ask again" in said.lower(), said


# --- what is refused ---------------------------------------------------------

def test_every_refusal_says_what_to_do_instead():
    """A model handed "invalid" spends a retry guessing, and the retry budget
    is for real mistakes."""
    for bad in ({"options": ["a", "b"]},
                {"question": "  ", "options": ["a", "b"]},
                {"question": "q"},
                {"question": "q", "options": []},
                {"question": "q", "options": "ab"},
                {"question": "q", "options": {"1": "a"}},
                {"question": "q", "options": ["a"]},
                {"question": "q", "options": ["a", 7]},
                {"question": "q", "options": ["a", "   "]},
                "not an object", None, 7):
        made, refused = A.parse(bad)
        assert made is None, bad
        assert refused.startswith("ask_user"), refused
        assert len(refused) > 30, refused


def test_a_string_of_options_is_not_a_list_of_them():
    """"ab" is iterable and would silently become two options called "a" and
    "b" -- a question nobody wrote, put to a real person."""
    made, refused = A.parse({"question": "q", "options": "ab"})
    assert made is None
    assert "list" in refused, refused


def test_the_question_and_the_options_are_bounded_rather_than_endless():
    made, refused = A.parse({"question": "why " * 400,
                             "options": ["so " * 200, "because " * 200]})
    assert refused == ""
    assert len(made.question) <= A.MAX_QUESTION_CHARS + 3
    for option in made.options:
        assert len(option) <= A.MAX_OPTION_CHARS + 3, len(option)
        assert option.endswith("...")


def test_whitespace_in_an_option_is_settled_before_it_is_measured():
    made, _ = A.parse({"question": "  what   now?\n",
                       "options": ["use\n  nodejs", "  use python  "]})
    assert made.question == "what now?"
    assert made.options == ("use nodejs", "use python")


# --- the block that goes on screen -------------------------------------------

def test_the_rendered_block_reads_with_every_escape_stripped():
    """Colour is never the message, and this one is not coloured at all: the
    options are read rather than watched, which is where DESIGN_PRINCIPLES
    keeps the gradient off."""
    made, _ = question("use nodejs", "use standard python")
    rendered = A.render(made, 80)
    assert "\x1b" not in rendered
    assert made.question in rendered
    assert "1. use nodejs" in rendered
    assert "2. use standard python" in rendered


def test_the_hint_names_both_ways_out():
    """A question a user cannot get out of is a question that has taken the
    session hostage."""
    made, _ = question("first", "second", "third")
    rendered = A.render(made, 80)
    assert "1, 2 or 3" in rendered, rendered
    assert "Esc" in rendered, rendered


def test_a_narrow_terminal_wraps_the_question_rather_than_cutting_it():
    """`agent_ui.wrap_lines` clips at the column by design, which is right for
    a row laid out by hand and wrong for a sentence somebody has to read
    before they can answer it."""
    made, _ = A.parse({"question": "Should the retry loop back off "
                                   "exponentially or wait a fixed interval "
                                   "between attempts?",
                       "options": ["Exponential backoff with a ceiling",
                                   "A fixed one second wait"]})
    for width in (30, 40, 60, 100):
        rendered = A.render(made, width)
        for row in rendered.splitlines():
            assert len(row) <= width, (width, len(row), row)
        # Nothing was lost: every word of the question is still somewhere in
        # the block, which is what "wrapped" means and "clipped" does not.
        flat = " ".join(rendered.split())
        for word in made.question.split():
            assert word in flat, (width, word)


def test_the_block_is_the_same_two_times_running():
    """It is written into the scrollback through `write_above`, so it is drawn
    once -- but a renderer that varied would mean two questions that looked
    different for no reason the reader could see."""
    made, _ = question("first", "second")
    assert A.render(made, 80) == A.render(made, 80)


def test_a_question_object_carries_nothing_it_was_not_given():
    made, _ = question("first", "second")
    assert isinstance(made.options, tuple), "options must not be editable in place"
    assert not hasattr(made, "__dict__"), "__slots__ keeps the shape closed"
