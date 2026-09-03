"""`ask_user`: a question with numbered options, answered by one keystroke.

Until this existed a model that did not know something had two choices, and
both were bad: guess, and possibly build the wrong thing for twenty minutes; or
end the turn with `end_conversation` to ask, which throws away everything it had
loaded and makes the user re-state the task. This is the third: it puts the
question on screen, the user presses 1-5, and the SAME turn carries on with the
answer in hand.

    {"action": "ask_user",
     "question": "What should the database layer use?",
     "options": ["Node with better-sqlite3",
                 "Standard library sqlite3 in Python",
                 "Something else -- I will say what"],
     "progress": "Asking which stack the database should use"}

The division is the one every state module here keeps: this decides the SHAPE
and the WORDS, and reads no terminal. Which keystroke arrived is the session's
to find out, because reading a terminal is the session's -- a module that
reached for stdin itself would do it from a background agent's thread, from a
piped run and from the test suite as well, where there is nobody to answer and
no per-test timeout to rescue a read that blocks forever.

WHAT IT IS NOT
--------------
It does not end the turn. `end_conversation` is the verb that ends turns and it
is the only one; this is an ordinary action whose result goes back to the model
like a file read, so the stream picks straight up with the answer.

It is refused where there is nobody to ask -- a piped run, a script, the test
suite, any background agent -- rather than blocking on a read that will never
be answered. The refusal says so and tells the model to decide for itself and
state the assumption, because a model left with no answer and no instruction
will ask again.
"""

import agent_ui

# Two is the fewest that is a question rather than a statement; five is the
# most that can be answered by a digit without the user reading a keyboard.
# The keys ARE the numbering -- option 1 is "1" -- so there is no second
# numbering to drift out of step with the one on screen, which is the rule
# `agent_plan` settled for its steps.
MIN_OPTIONS = 2
MAX_OPTIONS = 5
KEYS = ("1", "2", "3", "4", "5")

# A question is read at a glance and answered at once, so both ends are bounded
# rather than wrapped indefinitely. Judgements, not measurements.
MAX_QUESTION_CHARS = 300
MAX_OPTION_CHARS = 120

DISMISS = "esc"

_NO_QUESTION = ('ask_user needs "question": one sentence saying what you need '
                'decided.')
_NO_OPTIONS = ('ask_user needs "options": a list of %d to %d short strings, '
               'one per choice.' % (MIN_OPTIONS, MAX_OPTIONS))
_TOO_FEW = ('ask_user needs at least %d options -- with one there is nothing '
            'to choose. Ask something with a real alternative in it, or decide '
            'it yourself and say what you assumed.' % MIN_OPTIONS)
_TOO_MANY = ('ask_user takes at most %d options and was given %%d. The answer '
             'is one keypress, 1 to %d. Narrow it, or ask twice.'
             % (MAX_OPTIONS, MAX_OPTIONS))
_BAD_OPTION = 'ask_user option %d is not text: every option must be a string.'
_EMPTY_OPTION = 'ask_user option %d is empty. Every option has to say something.'

NO_TERMINAL = (
    "There is nobody to ask: this run has no terminal, so the question was not "
    "shown and nothing was chosen. Do not ask again. Decide it yourself, pick "
    "the option you would have recommended, and say plainly in your final "
    "message which one you chose and that you chose it because the question "
    "could not be put.")

DISMISSED = (
    "The user dismissed the question without choosing. Do not ask the same "
    "thing again. Either carry on with the option you would have recommended, "
    "saying which one and why, or ask something narrower.")

# The row under the options. It states both keys, because a question a user
# cannot get out of is a question that has taken the session hostage.
HINT = "Press %s to choose, or Esc to skip the question."


class Question:
    """A question and its options, already validated. Immutable by intent.

    `options` is a tuple, and the index into it IS the number on screen minus
    one. There is no second identifier: `agent_plan` settled that a display
    numbering and an internal numbering are two numberings that drift, and the
    model then answers about a choice the user cannot see.
    """

    __slots__ = ("question", "options")

    def __init__(self, question, options):
        self.question = str(question)
        self.options = tuple(str(option) for option in options)

    def __repr__(self):
        return "Question(%r, %d options)" % (self.question, len(self.options))

    def keys(self):
        """The keystrokes that answer this question, in order."""
        return KEYS[:len(self.options)]

    def key_list(self):
        """The keys as a person reads them: "1, 2 or 3"."""
        keys = list(self.keys())
        if len(keys) == 1:
            return keys[0]
        return "%s or %s" % (", ".join(keys[:-1]), keys[-1])

    def index_for(self, key):
        """The option a keystroke chose, or None for anything else.

        Anything else really is anything else: a letter, an arrow, a digit
        past the last option. A question that accepted a 4 it never offered
        would be recording a choice nobody made.
        """
        text = str(key or "")
        for position, candidate in enumerate(self.keys()):
            if text == candidate:
                return position
        return None

    def chosen(self, index):
        """The result handed back to the model for a chosen option.

        It carries the number AND the text. The number alone would make the
        model count the list again to find out what it meant, and the text
        alone would lose the correspondence with what the user actually
        pressed -- which is the one fact this whole action exists to deliver.
        """
        return ('The user chose %d: "%s". Carry on with that and do not ask '
                'again.' % (index + 1, self.options[index]))


def parse(obj):
    """(Question, "") for a usable request, or (None, why not).

    Every refusal names what is wrong AND what to do instead, because a model
    handed "invalid" spends a retry guessing. The retry budget is the model's
    to spend on real mistakes.
    """
    if not isinstance(obj, dict):
        return None, _NO_QUESTION
    question = obj.get("question")
    if not isinstance(question, str) or not question.strip():
        return None, _NO_QUESTION
    options = obj.get("options")
    if isinstance(options, str) or not isinstance(options, (list, tuple)):
        return None, _NO_OPTIONS
    if len(options) < MIN_OPTIONS:
        return None, _TOO_FEW
    if len(options) > MAX_OPTIONS:
        return None, _TOO_MANY % len(options)
    cleaned = []
    for position, option in enumerate(options, start=1):
        if not isinstance(option, str):
            return None, _BAD_OPTION % position
        text = " ".join(option.split())
        if not text:
            return None, _EMPTY_OPTION % position
        cleaned.append(_clip(text, MAX_OPTION_CHARS))
    return Question(_clip(" ".join(question.split()), MAX_QUESTION_CHARS),
                    cleaned), ""


def _clip(text, limit):
    """Cut at a word where one is near the end, and mark that it was cut."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    space = head.rfind(" ")
    if space > limit - 20:
        head = head[:space]
    return head.rstrip() + "..."


def render(question, columns=80, stream=None):
    """The block written above the live region, as one string.

    Wrapped on words rather than clipped at the column: this is prose somebody
    has to read before they can answer it, and `agent_ui.wrap_lines` clips by
    design -- which is right for a row whose columns were laid out by hand and
    wrong for a sentence. `agent_menu._wrap_words` is the same measurement with
    a word boundary preferred, and it is what prose rows want; it is reached
    through a guard so a missing module costs the wrapping and never the
    question.

    Plain text with no gradient. The options are read, not watched, and
    DESIGN_PRINCIPLES keeps the gradient off surfaces that are read -- which is
    also what makes this legible with every escape stripped.
    """
    width = max(20, int(columns or 80) - 2)
    rows = list(_wrap(question.question, width))
    rows.append("")
    # `"  1. "` is five columns, so the body gets five fewer -- not four. The
    # off-by-one is invisible for every option short enough to fit on its row
    # and puts the longest one past the right-hand edge, which is the failure
    # this repository already records: a row filled past the last column
    # auto-wraps on some terminals and costs a screen line the repaint
    # arithmetic does not know about.
    for position, option in enumerate(question.options, start=1):
        body = _wrap(option, max(10, width - len(_OPTION_INDENT)))
        rows.append("  %d. %s" % (position, body[0]))
        rows.extend(_OPTION_INDENT + line for line in body[1:])
    rows.append("")
    # Wrapped like everything else rather than clipped. It is the one row that
    # says how to answer, and half of it is worse than two rows of it.
    rows.extend(_wrap(HINT % question.key_list(), width))
    return "\n".join(rows)


_OPTION_INDENT = "     "


def _wrap(text, width):
    try:
        import agent_menu
        wrapped = agent_menu._wrap_words(text, width)
    except Exception:
        wrapped = agent_ui.wrap_lines(text, width)
    return list(wrapped) or [""]


def answer(question, key):
    """What the model is told, given the keystroke that arrived.

    One place decides this, so the action, the tests and any later caller
    cannot disagree about what any of the three outcomes means -- and there
    are three, not two:

      None  nobody was there to ask. The question was never drawn.
      ""    the user was there and dismissed it with Esc.
      a key the user chose that option.

    The first two look alike from inside the loop and are entirely different
    facts about the world, so they are answered differently. Telling a model
    "the user dismissed your question" when the run was a pipe with no
    terminal would have it reasoning about a person who was never there --
    and telling it "there was nobody to ask" when somebody pressed Esc would
    have it override a decision that was made.
    """
    if key is None:
        return NO_TERMINAL
    index = question.index_for(key)
    if index is None:
        return DISMISSED
    return question.chosen(index)
