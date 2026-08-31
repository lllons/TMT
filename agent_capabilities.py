"""Which of the three higher-level capabilities the user authorised, this turn.

`plan`, `review` and `verify` are the three things TMT can do that are not
ordinary tool work: writing a contract for the task, having a separate agent
audit it, and running the repository's own checks. Each of them costs a whole
extra model run, puts a column on the screen, and gates the final answer. They
are the user's to spend, so they are opt-in and the opt-in is a command in the
user's own words:

    Build the login page /plan /verify

Nothing else turns them on. Not the model, not a worker, not the reviewer, not
a plan the model wrote, not a sentence in a tool result -- and not this module
reading the task and deciding the work looks substantial. The whole point is
that the decision has one author, and it is the person typing.

**This is the only parser.** The action layer asks it whether a verb may run,
the completion gate asks it what may hold an answer, the prompt asks it which
sections to include, and the input box asks it which characters to paint. One
regex, one meaning; three copies of it would be three chances to disagree
about whether `/verification` counts.

Pure state and one regular expression. Nothing here opens a file, imports a
model, or touches the terminal -- the same division `agent_plan`,
`agent_review` and `agent_verify` keep, and for the same reason: a thing that
decides whether work is allowed must be testable without any of the machinery
it is deciding about.

### Why a slash, and why exactly a slash

`verify` is an ordinary English word. "verify this code", "please verify",
"verified" and "verification" are all things somebody says while asking for
something that is not the Smart Verification Engine, and a task that spent an
extra agent run every time the word appeared would be a tax on saying what you
mean. `review` and `plan` are the same: "review my code" is a request for an
opinion, "/review" is a request for the gated, independent, cycle-limited
reviewer.

So the slash is the whole distinction and it is not negotiable. `/verify`
authorises; `verify` does not.
"""

import re

# The three names, and the order they are drawn and reported in. This tuple is
# the definition -- everything else derives from it, so adding a fourth
# capability is one entry here and one branch in whatever it gates, rather
# than a search for every place three names were spelled out.
PLAN = "plan"
REVIEW = "review"
VERIFY = "verify"
CAPABILITIES = (PLAN, REVIEW, VERIFY)

# What each one authorises, in the words the refusal uses. Held here so the
# error the model reads and the row the user sees cannot drift apart.
SUMMARY = {
    PLAN: "planning",
    REVIEW: "independent review",
    VERIFY: "smart verification",
}

# The command as it is typed, for a message that has to show it.
def command(name):
    """`/plan` for `plan`. The token the user would have to type."""
    return "/" + str(name)


# What makes a capability command, and the whole of it.
#
# The negative lookbehind is the LEADING boundary: the slash must open a token
# rather than sit inside one, so `my/plan` and `abc/verify` are paths and not
# commands. It is written as "not preceded by a character that is neither
# whitespace nor an opening bracket or quote" rather than as an alternation of
# lookbehinds, because that form also succeeds at the start of the string --
# there is no character there to fail on -- which is what lets a line begin
# with the command.
#
# The negative lookahead is the TRAILING boundary and it is where the false
# positives actually live. `/planning`, `/planner`, `/plan123`, `/plan-b` and
# `/reviewing` must all stay ordinary text, so a word character, a digit, an
# underscore, a hyphen or a further slash immediately after the name means
# this was never the command. `/verification` and `/verified` never reach the
# lookahead at all: they diverge from "verify" at the sixth character, so the
# name itself does not match.
#
# Punctuation after the name is allowed on purpose -- `/plan.`, `/verify,` and
# `(/review)` are how people write a command inside a sentence -- because none
# of those characters can be part of a name, so allowing them cannot admit a
# longer word.
#
# Case-insensitive, matching `agent_commands.parse`, which has lower-cased
# command names since before this module existed. `/PLAN` is the plan command
# shouted, and a CLI that recognised `/plan` but not `/Plan` would be the odd
# one out in its own interface.
_TOKEN = re.compile(
    r"(?<![^\s(\[{'\"])/(plan|review|verify)(?![A-Za-z0-9_/-])",
    re.IGNORECASE,
)


def spans(text):
    """Every capability command in the text, as (start, end, name).

    Offsets into the string exactly as given, so the caller can paint the
    characters it found without the text being rewritten, re-cased or
    re-wrapped on the way. That is the requirement the input box has: the user
    typed `/PLAN` and must go on seeing `/PLAN`.

    In the order they appear, and one entry per occurrence -- `/plan /plan` is
    two spans and one capability. The UI wants every occurrence, because a
    token the user can see and TMT has not painted reads as a token TMT did
    not understand; the authorisation wants the set, which is what `parse`
    returns.
    """
    if not isinstance(text, str) or not text:
        return ()
    found = []
    for match in _TOKEN.finditer(text):
        found.append((match.start(), match.end(), match.group(1).lower()))
    return tuple(found)


def names_in(text):
    """The set of capabilities named in the text, in CAPABILITIES order.

    Deduplicated, because authorisation is a boolean and not a count: a user
    who writes `/plan` twice has asked for planning once, and the alternative
    reading -- two plans, two columns, two gates -- is not a thing TMT has.
    """
    found = set(name for _, _, name in spans(text))
    return tuple(name for name in CAPABILITIES if name in found)


class Capabilities:
    """Which capabilities this turn may use. One per session, emptied in place.

    Built ONCE by the session and re-adopted for each turn rather than rebound,
    which is the rule the plan, the review and the verification all keep and
    for the same reason: the agent loop builds its action context BEFORE it
    starts the turn, so a new object assigned here would leave the guards
    reading a set of flags nothing writes to. That failure is silent -- the
    authorisation would simply be off, with no error anywhere -- so it is
    written down here as well as there.

    The flags are private and there is no setter that takes a name and a
    value. The only two ways to move them are `adopt`, which reads the user's
    own text, and `retire`, which turns everything off. That is not
    decoration: a method that could be handed `("plan", True)` is a method a
    later edit can wire model output into, and the guarantee this module
    exists for is that model output cannot reach these three booleans.
    """

    __slots__ = ("_enabled", "_source")

    def __init__(self, text=None):
        self._enabled = frozenset()
        self._source = ""
        if text is not None:
            self.adopt(text)

    # --- reading ----------------------------------------------------------

    @property
    def plan(self):
        return PLAN in self._enabled

    @property
    def review(self):
        return REVIEW in self._enabled

    @property
    def verify(self):
        return VERIFY in self._enabled

    @property
    def source(self):
        """The text the current authorisation was read from."""
        return self._source

    def enabled(self, name):
        """Whether one capability, by name, is authorised.

        The form the guards use, so a guard is one lookup rather than a
        three-way branch that has to be repeated everywhere a verb is checked.
        An unknown name is False: a capability that does not exist is not one
        anybody has been authorised to use.
        """
        return str(name or "").lower() in self._enabled

    def active(self):
        """The authorised capabilities, in CAPABILITIES order."""
        return tuple(name for name in CAPABILITIES if name in self._enabled)

    def any(self):
        return bool(self._enabled)

    # --- writing, both of them --------------------------------------------

    def adopt(self, text):
        """Take the authorisation from the user's own words. In place.

        Called with the task the user typed, and with nothing else. Every
        turn re-reads it from scratch, so a capability is authorised for the
        request that asked for it and no longer: the next question starts from
        no capabilities at all unless it names them too. There is no
        accumulation and nothing to expire, because nothing is carried.

        Returns self, so a caller can build and adopt in one expression.
        """
        self._source = text if isinstance(text, str) else ""
        self._enabled = frozenset(names_in(self._source))
        return self

    def retire(self):
        """Every capability off. In place, unconditionally, no refusal.

        Total on purpose, and the reason is a bug the plan system shipped:
        `Plan.clear` was both the model's guarded verb and the session's way
        of retiring a finished plan, so retiring one that had done its work
        raised and killed the session on the next question. There is nothing
        to guard here -- turning authorisation OFF is never the dangerous
        direction -- so this cannot refuse and cannot raise.
        """
        self._enabled = frozenset()
        self._source = ""

    def __repr__(self):
        active = ", ".join(self.active()) or "none"
        return "<Capabilities %s>" % active


# --- the guard -------------------------------------------------------------

# What the model is told when it reaches for a capability nobody authorised.
# Two sentences: what happened, and the exact thing that would change it. The
# second one matters more than it looks -- a model told only "not permitted"
# reasonably goes looking for another route to the same effect, and there
# isn't one, so the message names the user's command instead of leaving it to
# be guessed at.
_REFUSED = (
    "REFUSED: the %s capability is not enabled. %s is authorised by the user, "
    "not by you, and this task's prompt did not contain it. Add %s to the "
    "prompt to enable %s. Do the work with the ordinary actions and say in "
    "your final answer that %s was not run because %s was not requested."
)


def refusal(capabilities, action):
    """The refusal for this action, or "" when it may run.

    The runtime half of the authorisation, and the half that has to be right.
    Filtering the verb out of the prompt makes an unauthorised capability
    invisible; this makes it unavailable, which is a different property and
    the one that survives a model inventing a verb it was never taught, a
    prompt that failed to rebuild, and any future edit that leaks the
    reference back into a system message.

    `capabilities` of None is treated as nothing authorised. That is the
    deliberate direction to fail in: a turn whose authorisation could not be
    read has not authorised anything, and the cost of being wrong is an
    ordinary task done with ordinary tools.
    """
    name = str(action or "").lower()
    if name not in CAPABILITIES:
        return ""
    if capabilities is not None:
        try:
            if capabilities.enabled(name):
                return ""
        except Exception:
            # A state object that raises is not a state object that
            # authorised anything.
            pass
    verb = command(name)
    return _REFUSED % (verb, verb, verb, SUMMARY[name], name, verb)


def gated_actions(capabilities):
    """The capability verbs this turn may NOT use.

    What the prompt builder subtracts. Derived from the same flags the guard
    reads rather than from a second list, so the set the model is taught and
    the set the runtime permits are the same set by construction.
    """
    if capabilities is None:
        return CAPABILITIES
    try:
        return tuple(name for name in CAPABILITIES
                     if not capabilities.enabled(name))
    except Exception:
        return CAPABILITIES


def allowed_actions(capabilities):
    """The capability verbs this turn may use."""
    if capabilities is None:
        return ()
    try:
        return tuple(name for name in CAPABILITIES if capabilities.enabled(name))
    except Exception:
        return ()
