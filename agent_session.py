"""The one record of a running TMT session.

Everything that outlives a single question and dies with the process lives
here: which provider and model the next request goes to, which directory it
may touch, and what has already been said. One object, held by the agent loop
for as long as the process runs and never written anywhere.

Two rules shape the whole module.

**Nothing here is a second copy of state another module owns.** The provider
comes from agent_credentials, the model from agent_models, the workspace from
agent_config, and each is read at the moment it is asked for. A session that
remembered the model chosen at launch would keep sending to it after Settings
had moved on, and the status line would be telling the truth while the request
was not.

**Nothing here reaches the disk.** The conversation belongs to the run. Closing
TMT ends it, and the next launch starts with nothing, so a task abandoned on
Tuesday cannot turn up as context on Wednesday. There is no path in this file
that opens a file, and a test says so.

The conversation is kept in TMT's own shape -- a list of ``{"role", "content"}``
dicts, the same shape the agent loop already speaks -- and converted for
whichever provider is answering by the adapter in agent_providers. That is what
lets the model or the provider change mid-session without the history having to
be rebuilt or thrown away: no provider's request format ever gets to be the
place the conversation is stored.
"""

import json

import agent_config
import agent_models
from agent_ui import CHARS_PER_TOKEN

# What one turn is allowed to contribute, and how much of the window all of
# them together may take.
#
# The carried conversation is not the only thing in a request: the system
# prompt and its workspace snapshot go in front of it, and the turn's own
# tool results pile up behind it. Handing the whole window to the history
# would mean the request that finally fails is the one where the agent was
# doing the most work. A quarter leaves the rest of the turn the room it
# actually uses.
CONTEXT_SHARE = 0.25

# A cap on one stored reply. A single long answer must not be able to crowd
# out every turn before it; what is kept is the front of it, which is where an
# answer says what it did.
MAX_ANSWER_CHARS = 2000

# And a cap on the number of turns, whatever the arithmetic says. Context
# windows are large enough now that a budget alone would carry a whole day's
# work into every request, most of it irrelevant.
MAX_TURNS_KEPT = 12

# Used only when the model's real window cannot be looked up. Small on
# purpose: an unknown window is a reason to carry less, not more.
FALLBACK_CONTEXT = 32000

_TRUNCATION_NOTE = "\n[...answer truncated for context.]"

# The event kinds that name a file the turn actually changed. Read off the
# turn's own history, so what is carried forward is measured rather than
# described -- the same rule the transcript works under.
_FILE_KINDS = ("file_create", "file_edit", "file_delete")

# And the kinds whose own message is the fact. A commit and a push say what
# they did in one line -- "Committed 6f0a4f5 on main" -- and say it nowhere
# else: they touch no file that `_FILE_KINDS` would catch, so a turn that only
# used git carried nothing at all into the next question.
_MILESTONE_KINDS = ("milestone",)

# How much of a milestone's own sentence is kept. It is one line by
# construction; the cap is only so a long one cannot crowd out the answer.
MAX_FACT_CHARS = 120


def estimate_tokens(text):
    """A token estimate for a block of text, and it is only an estimate.

    Every provider counts differently and none of them will count for us
    before the request is sent, so this is characters divided by the same
    constant the live token readout uses. It is labelled an estimate
    everywhere it is used, and it is used only to decide how much history to
    carry -- never reported as a token figure.
    """
    return (len(text or "") + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


class Turn:
    """One question and what came of it.

    `facts` is what the turn measurably did -- the files it changed and the
    milestones it reached -- taken from the events the turn actually emitted.
    It is carried with the answer because "now add percentage support" is a
    sentence about a file, and the file is the part the answer most often
    leaves out; and because a turn that committed and pushed says so nowhere
    else once its answer text has been read.

    `outcome` is why there is no answer, for a turn that did not produce one.
    Such a turn is still recorded: the question was asked, and dropping it
    left the next question with no idea the exchange had happened, which is
    the whole of what "it has no context" looked like from the outside.
    """

    __slots__ = ("task", "answer", "facts", "outcome")

    def __init__(self, task, answer="", facts=(), outcome=""):
        self.task = str(task or "")
        self.answer = str(answer or "")
        self.facts = tuple(facts)
        self.outcome = str(outcome or "")

    def messages(self):
        """The turn as TMT's own provider-independent messages.

        The answer is carried as the JSON action the model speaks in, not as
        the bare sentence. Every other assistant message in a request is a
        JSON object -- that is the whole of what the system prompt demands --
        and dropping loose prose into the same array put examples of the
        forbidden shape in front of the model, in its own voice, immediately
        before asking it not to use that shape. The words are the model's own
        either way; only the wrapper is restored.
        """
        out = []
        task = self.task
        if self.outcome:
            # Said by TMT rather than by the model, because it is TMT's report
            # of what happened to the turn. It rides on the question so the
            # roles still alternate, which some providers require.
            task = (task + "\n\n[That turn ended with no answer: %s]" % self.outcome).strip()
        if task:
            out.append({"role": "user", "content": task})
        if self.answer:
            message = self.answer
            if self.facts:
                message += "\n\nIn that turn: " + ", ".join(self.facts)
            out.append({"role": "assistant",
                        "content": json.dumps({"action": "respond",
                                               "message": message})})
        return out

    def size(self):
        return sum(estimate_tokens(message["content"]) for message in self.messages())

    def __repr__(self):
        return "Turn(task=%r, answer=%r, outcome=%r)" % (
            self.task[:40], self.answer[:40], self.outcome)


def files_touched(history):
    """What a turn measurably did, in order, each stated once.

    Read off the history's own events, so it is the same set of facts the
    transcript printed rather than a second account of them. A file event
    contributes the path it named; a milestone contributes its own sentence,
    which is where a commit and a push are recorded. An event that carries
    neither contributes nothing rather than a guess, and a turn that did
    neither reports nothing rather than an empty-looking zero.
    """
    seen, out = set(), []

    def keep(value):
        value = " ".join(str(value or "").split())[:MAX_FACT_CHARS]
        if value and value not in seen:
            seen.add(value)
            out.append(value)

    for event in (history or ()):
        kind = getattr(event, "kind", "")
        if kind in _FILE_KINDS:
            for name in (getattr(event, "detail", None) or {}).get("paths") or ():
                keep(name)
        elif kind in _MILESTONE_KINDS:
            keep(getattr(event, "message", ""))
    return tuple(out)


class Session:
    """Provider, model, workspace and conversation for one run of TMT.

    The three facts are properties rather than fields: they answer from the
    modules that own them every time they are asked. The conversation is the
    only thing this object actually holds.
    """

    def __init__(self, workspace=None, share=CONTEXT_SHARE):
        self._workspace = workspace
        self._share = float(share)
        self._turns = []
        # What the session has cost and changed so far, for the readout in the
        # corner of the screen. Lines are counted -- they come off the events
        # the actions themselves reported. Tokens are estimated on the way out
        # and exact on the way back whenever the provider says, and `exact`
        # records which, because a number presented as measured when it was
        # guessed is the one thing this project will not do.
        self.lines_added = 0
        self.lines_removed = 0
        self.tokens_in = 0
        self._tokens_out = 0
        self.tokens_out_exact = True
        # Characters generated by the request in flight, before the provider
        # has said how many tokens they were. The readout has to move while
        # the reply is arriving rather than jumping at the end of it, so what
        # is on screen is this estimate until the real figure lands and
        # replaces it.
        self._streaming_chars = 0

    # --- what the next request runs under ---------------------------------

    @property
    def provider_id(self):
        try:
            import agent_credentials
            return agent_credentials.selected_provider()
        except Exception:
            return ""

    @property
    def model_id(self):
        try:
            return agent_models.current_model(self.provider_id or None)
        except Exception:
            return ""

    @property
    def workspace(self):
        return agent_config.ROOT_DIR if self._workspace is None else self._workspace

    def context_window(self):
        """The active model's real window in tokens, or a cautious guess.

        Taken from the catalogue entry for the model actually selected, which
        is where the provider's own figure lands. A model TMT cannot find an
        entry for gets FALLBACK_CONTEXT, which is smaller than any real window
        TMT runs on -- an unknown limit is a reason to send less.
        """
        model_id = self.model_id
        try:
            entries = agent_models.catalogue(self.provider_id or None)
        except Exception:
            entries = ()
        for entry in entries or ():
            if entry.get("id") == model_id:
                window = entry.get("context")
                if isinstance(window, int) and window > 0:
                    return window
        return FALLBACK_CONTEXT

    def carry_budget(self):
        """How many estimated tokens of history a request may carry."""
        return max(0, int(self.context_window() * self._share))

    # --- the conversation --------------------------------------------------

    @property
    def turns(self):
        return tuple(self._turns)

    def carried(self):
        """The turns that fit the budget, oldest dropped first.

        Dropped rather than summarised. A summary of a turn is a claim about
        what happened in it, and this module has no way to make one that is
        true -- the honest thing to leave out is the whole turn.
        """
        kept, spent, budget = [], 0, self.carry_budget()
        for turn in reversed(self._turns[-MAX_TURNS_KEPT:]):
            size = turn.size()
            if kept and spent + size > budget:
                break
            kept.append(turn)
            spent += size
        kept.reverse()
        return tuple(kept)

    def carried_messages(self):
        """The carried turns as TMT's own message list."""
        out = []
        for turn in self.carried():
            out.extend(turn.messages())
        return out

    def begin_turn(self, task, system_prompt=""):
        """The messages one turn starts from, and how many of them are fixed.

        The list is the system prompt, then every carried turn, then the new
        task. The count returned is all of that: it is what the agent loop
        must not trim away, because trimming the task out of a request leaves
        the model answering a question nobody asked. Everything the loop adds
        after this point -- an action, its result, the next action -- is
        trimmable and this session never sees it.
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": str(system_prompt)})
        messages.extend(self.carried_messages())
        messages.append({"role": "user", "content": str(task)})
        return messages, len(messages)

    def record(self, task, answer="", history=None, outcome=""):
        """Append a finished turn. Called once, however the turn ended.

        Every turn is recorded, including one that produced no answer. It used
        to take an answer to be recorded at all, which meant a stream failure,
        a circuit break, an unreadable reply or a turn that ran out of steps
        dropped the user's question with it -- and the next question then
        arrived with no sign the exchange had ever happened. That is precisely
        what "it has no context between prompts" looked like from outside.
        `outcome` is what to say instead, and it is stated rather than glossed.

        The answer is stored as the model wrote it, capped at MAX_ANSWER_CHARS
        and saying so when it had to be cut. A cap that silently truncated
        would carry a sentence forward with its meaning changed by where it
        stopped.
        """
        task = str(task or "").strip()
        if not task:
            return None
        answer = str(answer or "").strip()
        if len(answer) > MAX_ANSWER_CHARS:
            answer = answer[:MAX_ANSWER_CHARS].rstrip() + _TRUNCATION_NOTE
        turn = Turn(task, answer, files_touched(history), outcome)
        self._turns.append(turn)
        return turn

    # --- what the session has cost and changed ----------------------------

    def count_event(self, event):
        """Add one event's line counts to the session totals.

        Only where both halves are actually known. An event carries `added`
        and `removed` when the action that made it could count them -- a
        patch knows both, a `write_file` knows only what it wrote, because
        what it replaced was gone before anyone could count it. A missing half
        contributes nothing rather than a zero, which would read as "removed
        none" when the truth is "nobody knows".
        """
        detail = (getattr(event, "detail", None) or {})
        for key, name in (("added", "lines_added"), ("removed", "lines_removed")):
            value = detail.get(key)
            if isinstance(value, int) and value >= 0:
                setattr(self, name, getattr(self, name) + value)
        return event

    def count_history(self, history):
        """Add a whole turn's events, however the turn ended.

        Called on every path out of a turn, including a cancelled one: a file
        that was changed before the user pressed Ctrl-C was still changed.
        """
        for event in (history or ()):
            self.count_event(event)

    def record_request(self, messages):
        """Add an estimate of what a request costs to send.

        An estimate, and never anything else: no provider will count a request
        before it is sent, and the one that would count it afterwards reports
        its own figure only for what it generated. The readout says so.
        """
        self.tokens_in += sum(
            estimate_tokens(str(message.get("content", "")))
            for message in (messages or ()) if isinstance(message, dict))

    @property
    def tokens_out(self):
        """What has been generated, including the reply still arriving.

        The settled total plus an estimate of the request in flight, so the
        readout climbs while the reply streams instead of standing still and
        then jumping when the turn ends. The estimate is replaced by the
        provider's own figure the moment `record_reply` is called, and the
        readout is marked as an estimate for as long as one is in it.
        """
        return self._tokens_out + estimate_tokens("x" * self._streaming_chars)

    @property
    def streaming(self):
        """Whether part of the token total is still an estimate in flight."""
        return bool(self._streaming_chars)

    @property
    def tokens_settled(self):
        """Only what has actually been accounted for. For tests, not display."""
        return self._tokens_out

    def note_output(self, characters):
        """More characters have arrived from the request in flight.

        Counted the way LiveUI counts them -- incrementally, across the whole
        turn -- so the two readouts on screen never disagree about how much
        has been generated.
        """
        self._streaming_chars += max(0, int(characters or 0))

    def record_reply(self, text, tokens=None):
        """Add what a reply cost, exactly when the provider said so.

        `tokens` is the provider's own count of what it generated. When it
        gives one it supersedes any estimate; when it does not, the reply's
        length is the only thing there is, and the whole readout drops to
        being labelled an estimate rather than quietly mixing the two.

        Either way the streaming estimate is cleared, because whatever it was
        standing in for has now been counted properly.
        """
        self._streaming_chars = 0
        if isinstance(tokens, int) and tokens >= 0:
            self._tokens_out += tokens
        else:
            self._tokens_out += estimate_tokens(text)
            self.tokens_out_exact = False

    def clear(self):
        """Forget the conversation. The session's own facts are unaffected."""
        self._turns = []

    def __len__(self):
        return len(self._turns)

    def __repr__(self):
        return "Session(provider=%r, model=%r, turns=%d)" % (
            self.provider_id, self.model_id, len(self._turns))
