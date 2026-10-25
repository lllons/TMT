"""The two wire protocols a model can answer TMT in: TAGS and JSON.

JSON is what TMT spoke first: one object per reply, `{"action":"read_file",
"path":"x"}`, or a batch `{"actions":[...]}`. TAGS says exactly the same thing
without a single escape, and that is the whole reason it exists: a model
writing a Python file inside a JSON string has to escape every quote,
backslash and newline in it, and one escape written wrong is a reply TMT
cannot read. Inside a tag a file is written exactly as it is.

This module is the protocol and nothing else. It does no I/O, it chooses no
protocol for anybody -- the caller passes one where it matters -- and it reads
nothing from the session. It imports `agent_config` lazily and only for
REQUIRED_KEYS, which is what the shorthand below is defined against.

THE GRAMMAR
-----------

A tag is `/name/`, closed by `//name/`. A name is `[A-Za-z_][A-Za-z0-9_]*`,
matched case-insensitively and normalised to lowercase. A name may carry a
suffix, `/name:a/`, written `:[A-Za-z0-9_-]+`, and then only `//name:a/`
closes it. The suffix exists for one job: a value that contains its own
closer, such as a file that contains the text `//content/`, is written as
`/content:a/ ... //content:a/`.

1. THE ACTION IS THE OUTER TAG, and its keys are tags nested inside it. Inline
   and block form are the same grammar and mix freely:

       /read_file/ /path/ src/main.py //path/ /progress/ Reading it //progress/ //read_file/

       /write_file/
       /path/ src/hello.py //path/
       /content/
       def hello():
           return "hi"
       //content/
       //write_file/

2. SHORTHAND. Bare text directly inside an action, with no key tags, is the
   action's first required key (`agent_config.REQUIRED_KEYS[action][0]`;
   `bash` takes `command`, and the legacy names in LEGACY_PRIMARY take theirs).
   So `/end_conversation/ All done. //end_conversation/` is
   `{"action":"end_conversation","message":"All done."}`. The decision is made
   on the first non-whitespace token after the action's open tag: when it is a
   key tag the body is structured, and otherwise everything up to the
   action's own closer is the value and no tag inside it is recognised -- so
   `src/agent/x.py` is a path and never a tag.

   A "key tag" there means an open tag whose own closer appears BEFORE the
   enclosing closer. `/read_file/ /usr/lib/x.py //read_file/` is therefore a
   path: `/usr/` looks like a tag, but `//usr/` never follows it before
   `//read_file/` does. The same test decides whether an `/item/` (rule 4) is
   an object or a piece of text.

   A structured body may hold only key tags and whitespace; loose text between
   two key tags is refused with a sentence. `/action/` is never a key.

3. LEAF WHITESPACE. Two rules, decided by the key. CRLF is read as LF
   everywhere first.

   The EXACT KEYS -- content, search and replace, the three that hold file
   text -- keep heredoc semantics. Inline form (anything on the same line as
   the open tag) strips the spaces and tabs around the value. Block form (a
   newline, optionally after spaces or tabs, straight after the open tag)
   drops that one newline and keeps everything else; when the closer sits on
   a line of its own, the spaces or tabs before it are dropped and the newline
   before them is KEPT, so a file ends with "\\n" the way a heredoc does. A
   closer on the last content line leaves no trailing newline. Either way,
   spaces and tabs immediately before a closer are not part of the value.

   EVERY OTHER LEAF, at any depth -- path, message, query, command, a string
   in a list, all of them -- is TRIMMED of leading and trailing whitespace,
   newlines included, however it was written. Its interior is kept exactly. A
   path written in block form is therefore `src/a.py`, never `src/a.py\\n`.

4. LISTS AND OBJECTS. Only the CONTAINER KEYS below hold structure; every
   other key is a TEXT LEAF in which nothing but its own closer is recognised,
   so code and paths inside a leaf never open a tag. A list is written as
   `/item/` entries -- `/options/ /item/ yes //item/ /item/ no //item/
   //options/` -- and an item whose body starts with a key tag is an object.
   A container whose children are key tags other than `/item/` is an object
   (`/constraints/`, `/report/`). Inside `/calls/` and `/actions/` every child
   tag IS an action, named by its tag, and may use the shorthand. A container
   holding bare text is refused with a sentence naming `/item/`.

5. BATCHES. Several top-level action blocks run in order and read as
   `{"actions":[...]}`; so does an explicit `/actions/ ... //actions/` wrapper,
   even around one action. Reply-level `/progress/`, `/next_step/` and
   `/events/` may sit beside the blocks: they belong to the batch, or are
   merged into the one action when there is exactly one.

6. TYPES. A BOOLEAN KEY must hold true or false (any case) and is read as a
   bool; anything else is refused. An INTEGER KEY holding an integer literal
   (optional sign) is read as an int; any other text is left as text for the
   handler's own refusal. Both tables apply at every depth. Everything else is
   text, and `null` is not special. Agent ids are text.

7. A leaf whose closer never appears is refused with a sentence that names the
   suffix mechanism.

8. Text outside every tag at the top level is ignored, as text around a JSON
   object always was. A reply with no tags at all is not a TAGS reply: `parse`
   answers None and the caller's prose path takes it. An action that never
   closes is refused with a sentence naming it.

9. TMT's own markers -- `tmt_synthetic`, `tmt_prose`, `tmt_synthetic_reason`
   -- are ordinary key tags, so a reply TMT fabricates can be written in
   whichever protocol is in force.

At the top level a tag opens a block when its name is an action TMT knows (the
legacy names included), a reply-level key, or `actions` -- or, for any other
name, when its closer follows it, so an unknown action reaches
`validate_action` and is refused there in words rather than vanishing. A
tag-shaped token in prose ("TCP/IP/UDP") opens nothing.

PLAIN TEXT
----------

`parse`, `parse_tags`, `detect` and `locate` take `plain_text=False`. With the
default every rule above holds unchanged. With `plain_text=True` (the main
agent under tags, from Step 2 on):

A. Text outside the blocks is a message to the user; nothing is dropped.
B. A reply with no action blocks is the final answer: `end_conversation`
   carrying the text, trimmed and uncapped, plus any reply-level `/next_step/`,
   `/events/` or `/progress/`. Blank text is still None.
C. Text before an action block (since the previous block or the start) is its
   `progress`; an explicit `/progress/` inside it follows after one space.
   Reply-level blocks do not split the text around them.
D. Text after the last action block is appended as a `send_message`. A lone
   old ending block takes it into its message instead, after a blank line.
E. Old tags are accepted. An ending block (`end_conversation`, `done`,
   `respond`) beside any block that is not a speaking verb becomes a
   `send_message` in place, and its `next_step` moves to reply level.
F. Only a known action, a reply-level key or `actions` opens a top-level
   block, and one glued to the text before it (`docs/plan/x`) opens one only
   when its closer follows. Everything else is prose. JSON is read only for a
   reply that is one JSON object, starts with an action object, or is the
   JSON-drift case (an action object before the first tag).
G. An unknown top-level name within edit distance 2 of a taught name, or a
   key name at the top, is refused -- but only when its closer follows or it
   is the whole reply. The untaught names are answered with the plain-text
   rule, never with their old tag.
H. `render`, `example` and `transliterate` take `plain_text=False` too, and
   with it write what the rules above read back (see `render`); a shape
   plain text cannot say raises, and `transliterate` names the example.
I. `StreamingTagParser(plain_text=True)` streams the prose outside every
   block as ("text", ...) as it arrives, held back at an undecided tag.
J. `correction(..., plain_text=True)` and `TAG_CONTRACT_PLAIN` are the
   model-facing sentences in plain form, and the parser's refusals about an
   unclosed or doubled untaught tag are too: no JSON, no braces, none of
   /progress/, /send_message/ or /end_conversation/.
K. TMT's three markers (rule 9) are reply-level keys here, like /next_step/:
   a top-level marker block is carried onto the object and does not split
   the prose around it, so a fabricated ending survives `render` and `parse`.
"""

import json
import re

JSON = "json"
TAGS = "tags"
# The order Settings offers them. `agent_config.PROTOCOLS` spells the same two
# words -- it cannot import this module without closing a cycle -- and a test
# asserts the two agree.
PROTOCOLS = (TAGS, JSON)
DEFAULT_PROTOCOL = TAGS


class ProtocolError(ValueError):
    """A reply that could not be read, carrying a sentence the model can act on."""


# The first key of the names TMT still understands but no longer teaches. They
# are recognised as action tags so the compatibility net in
# `agent_actions.adopt_verb` gets them exactly as it gets the JSON spelling:
# `/respond/ All done. //respond/` is `{"action":"respond","message":...}`.
# `respond` is here although `agent_actions._LEGACY_ACTIONS` does not list it:
# `canonical_action` translates it by hand, reading its old `final` flag.
LEGACY_PRIMARY = {
    "respond": "message",
    "done": "message",
    "announce": "message",
    "search_files": "query",
    "find_text": "query",
    "run_file": "path",
    "run_python": "path",
}

# Actions whose first useful key is not their first REQUIRED key. `bash`
# requires nothing (the operation decides what it needs), and its commonest
# shape by far is a command.
_PRIMARY_OVERRIDES = {"bash": "command"}

# Keys read as true/false, and the handler that reads each one. A JSON reply
# carries a real bool; a tag carries text, and `bool("false")` is True, so
# without this table `/regex/ false //regex/` would turn regex ON.
_BOOL_READERS = {
    "all": "git_commit: agent_actions passes stage_all=bool(obj.get('all'))",
    "apply": "replace_across: agent_actions passes apply=bool(obj.get('apply'))",
    "diff": "spawn_agent constraints.report: agent_delegation._report/_flag",
    "file_list": "spawn_agent constraints.report: agent_delegation._report/_flag",
    "final": "legacy respond: agent_actions.canonical_action reads obj.get('final')",
    "full": "verify: agent_actions._verify passes full=bool(obj.get('full'))",
    "ignore_case": "grep: agent_actions passes ignore_case=bool(obj.get('ignore_case'))",
    "read_only": "spawn_agent constraints: agent_delegation.parse/_flag",
    "recursive": "delete_folder: agent_file_ops.delete_folder(recursive=...)",
    "regex": "grep: agent_actions passes regex=bool(obj.get('regex'))",
    "summary": "spawn_agent constraints.report: agent_delegation._report/_flag",
    "tmt_prose": "agent_model.is_prose (PROSE_KEY)",
    "tmt_synthetic": "agent_model.is_synthetic (SYNTHETIC_KEY)",
}

# Keys read as whole numbers, and the handler that reads each one. Most of
# these handlers call int() themselves and would cope with "5", but
# agent_delegation refuses a numeric string on purpose, and a plan step of 2
# and "2" should not depend on which protocol was in force.
_INT_READERS = {
    "after": "plan add: agent_plan.Plan.find (1, '1' and 'S1' all accepted)",
    "context": "grep: agent_grep._as_context",
    "depth": "tree: agent_tree._as_int",
    "end": "read_lines / replace_lines: agent_file_ops int(end)",
    "item": "review_agenda: agent_reviewbot.Agenda.find (2 or 'A2')",
    "level": "verify: agent_actions._verify_level",
    "limit": "tree, glob, grep, find_symbol, recall, multi_tool: each int(limit)",
    "max_results": "web_search: agent_web._clean_count",
    "position": "review_agenda: agent_reviewbot (alias of item)",
    "start": "read_lines / replace_lines: agent_file_ops int(start)",
    "step": "plan update/remove: agent_plan.Plan.find",
    "timeout": "bash (agent_bash._timeout), web_fetch (agent_web.fetch), "
               "review / verify / wait_for_agent(s) (agent_actions._timeout)",
    "timeout_seconds": "spawn_agent constraints: agent_delegation._timeout (strict int)",
}

# The only keys that hold lists or objects. Everything else is a text leaf, and
# that is what keeps a file's contents from ever being read as tags.
_CONTAINER_READERS = {
    "actions": "a batch: the step loops in TMT and agent_worker",
    "calls": "multi_tool: agent_multi",
    "constraints": "spawn_agent: agent_delegation.parse (an object)",
    "events": "any action: agent_actions._said_something, agent_ui",
    "files": "write_files: agent_file_ops",
    "ids": "wait_for_agents: agent_actions._wait_for_agents",
    "items": "review_agenda create/add: agent_reviewbot.apply_operation",
    "options": "ask_user: agent_ask",
    "paths": "git_commit, git_diff, review, verify: agent_actions",
    "report": "spawn_agent constraints: agent_delegation._report (an object)",
    "steps": "plan create/update: agent_plan.Plan.create/update",
    "tags": "remember: agent_memory.remember",
    "updates": "review_agenda update: agent_reviewbot.Agenda.update",
}

BOOL_KEYS = frozenset(_BOOL_READERS)
INT_KEYS = frozenset(_INT_READERS)
CONTAINER_KEYS = frozenset(_CONTAINER_READERS)
# The two containers that are objects rather than lists, which matters only
# for an empty one: `/report/ //report/` is {} and `/paths/ //paths/` is [].
DICT_CONTAINERS = frozenset({"constraints", "report"})
# The two whose children are actions named by their tag.
ACTION_CONTAINERS = frozenset({"calls", "actions"})
# What may sit beside the action blocks at the top of a reply.
REPLY_LEAVES = frozenset({"progress", "next_step"})
REPLY_KEYS = frozenset({"progress", "next_step", "events"})
# TMT's own markers (rule 9). Under the plain-text rules they are reply-level
# keys as well (rule K), so a reply TMT fabricated keeps them when it is
# written back as plain text and read again.
MARKER_KEYS = frozenset({"tmt_synthetic", "tmt_synthetic_reason", "tmt_prose"})
PLAIN_REPLY_KEYS = REPLY_KEYS | MARKER_KEYS
# The verbs `render` writes in the shorthand. Everything else is written with
# its keys named, which is the form the model is taught.
SHORTHAND_VERBS = frozenset({"send_message", "end_conversation", "internal_response"})
# The keys that hold file text and keep heredoc whitespace (rule 3): write_file,
# append_file, replace_lines and write_files entries hold `content`, patch_file
# and replace_across hold `search` and `replace`. A trailing newline or a
# leading indent is part of what those three mean; anywhere else it is an
# accident of layout -- a path ending in "\n" is a file that does not exist --
# so every other leaf is trimmed.
EXACT_KEYS = ("content", "search", "replace")
# The longest line `render` writes an action on before it goes to block form.
INLINE_WIDTH = 110


def primary_key(action):
    """The key bare text inside this action is the value of, or None."""
    name = str(action or "").lower()
    if name in LEGACY_PRIMARY:
        return LEGACY_PRIMARY[name]
    if name in _PRIMARY_OVERRIDES:
        return _PRIMARY_OVERRIDES[name]
    keys = _required_keys().get(name)
    return keys[0] if keys else None


def bool_keys():
    return BOOL_KEYS


def int_keys():
    return INT_KEYS


def container_keys():
    return CONTAINER_KEYS


def exact_keys():
    return EXACT_KEYS


_REQUIRED = None


def _required_keys():
    """agent_config.REQUIRED_KEYS, imported on first use; {} if it cannot be."""
    global _REQUIRED
    if _REQUIRED is None:
        try:
            import agent_config
        except Exception:
            return {}
        _REQUIRED = agent_config.REQUIRED_KEYS
    return _REQUIRED


def _known_actions():
    return frozenset(_required_keys()) | frozenset(LEGACY_PRIMARY)


# --- the lexical pieces -------------------------------------------------------

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TAG = re.compile(r"/(/?)([A-Za-z_][A-Za-z0-9_]*)(?::([A-Za-z0-9_-]+))?/")
# A tag with blanks inside its slashes, such as "/ git_push/" or "/git_push /".
_SPACED_TAG = re.compile(r"/(/?)(?:[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*|([A-Za-z_][A-Za-z0-9_]*)[ \t]+)/")
# Text that could still become a tag once more of it arrives.
_PARTIAL_TAG = re.compile(r"//?(?:[A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z0-9_-]*)?)?\Z")
_BLOCK_START = re.compile(r"[ \t]*\n")
_INT = re.compile(r"[+-]?[0-9]+")
_SPACE = " \t\n\r"
# Case-folding for tag names only. str.lower() can change a string's length
# ("İ" becomes two code points), and the reader indexes the original text
# with offsets it found in the folded copy, so only ASCII is folded.
_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _fold(text):
    return text.translate(_FOLD)


def _open(name, suffix=None):
    return "/%s%s/" % (name, ":" + suffix if suffix else "")


def _close(name, suffix=None):
    return "/" + _open(name, suffix)


def _leaf_value(raw, exact):
    """Rule 3: a leaf's text as written, made into its value."""
    if not exact:
        return raw.strip(_SPACE)
    start = _BLOCK_START.match(raw)
    raw = raw[start.end():] if start else raw.lstrip(" \t")
    return raw.rstrip(" \t")


def _snippet(text):
    line = text.strip().split("\n", 1)[0]
    return line if len(line) <= 40 else line[:37] + "..."


_NEVER_CLOSED = ("%s is never closed: %s does not appear after it. If the "
                 "value itself contains %s, mark the pair with a suffix the "
                 "value does not contain, as %s ... %s.")
_UNCLOSED = ("%s is never closed. End it with %s after what it holds.")
_LOOSE = ("Text %r sits loose inside %s, between its key tags. Put every value "
          "in its own key tag, as /path/ src/main.py //path/ -- or write the "
          "body with no key tags at all, when it is the action's one value.")
_STRAY_CLOSER = "%s appears inside %s where a key tag or %s was expected."
_ACTION_KEY = ("/action/ is not a key: the action is the outer tag itself, as "
               "/read_file/ /path/ x.py //path/ //read_file/.")
_TWICE = "%s is given twice inside %s. Give each key once."
_LIST_TEXT = ("%s holds a list: write each entry as /item/ ... //item/ inside "
              "it, as %s /item/ first //item/ /item/ second //item/ %s.")
_DICT_TEXT = ("%s holds key tags, as /read_only/ true //read_only/, not bare "
              "text.")
_MIXED = ("%s mixes /item/ entries with %s. Every entry of a list is its own "
          "/item/ ... //item/.")
_NOT_AN_ACTION = ("Inside %s every entry is an action tag, as /read_file/ "
                  "/path/ x.py //path/ //read_file/; %r is not one.")
_NO_BARE = ("%s takes no bare value. Write it with nothing inside, as %s %s, "
            "or name each key in its own tag.")
_BARE_LIST = ("The bare text inside %s would be its %s, which is a list. Write "
              "%s /%s/ %s //%s/ %s.")
_NOT_BOOL = "/%s/ must be true or false, not %r."
_BOTH_PLACES = "/%s/ is given both inside /%s/ and beside it. Give it once."
_TWICE_BESIDE = "/%s/ is given twice beside the actions. Give it once."


# --- the reader -----------------------------------------------------------

# Where an action sits, which decides what the live reader reports about it.
_TOP = "top"          # a block at the top of the reply
_WRAPPED = "wrapped"  # a child of a top-level /actions/ wrapper
_CALL = "call"        # a child of /calls/, or of an /actions/ nested deeper
_NESTED = "nested"    # an item object or a dict container


class _Incomplete(Exception):
    """Live reading only: the text ends before this part can be decided."""


class _Reader(object):
    """One pass over a reply's text, building the object it describes.

    The same code reads a finished reply (strict: every fault is a
    ProtocolError) and a reply that is still arriving (live: it stops quietly
    where the text runs out, and records the events the stream reports).
    One reader for both is what keeps the live view and the final answer from
    drifting apart.
    """

    def __init__(self, text, live=False, base=0):
        self.text = text
        self.low = _fold(text)
        self.n = len(text)
        self.live = live
        self.base = base
        self.events = []
        self.done = 0
        self.known = _known_actions()
        # Set by reply_plain: the refusals about the three untaught tags then
        # take their plain-text wording (rule J).
        self.plain = False

    # -- small helpers --

    def fail(self, message):
        if self.live:
            raise _Incomplete()
        raise ProtocolError(message)

    def emit(self, at, kind, value):
        if self.live:
            self.events.append(((kind, self.base + at), kind, value))

    def skip(self, p):
        text, n = self.text, self.n
        while p < n and text[p] in _SPACE:
            p += 1
        return p

    def tag_at(self, p):
        found = _TAG.match(self.text, p)
        if found is None and self.live and _PARTIAL_TAG.match(self.text, p):
            raise _Incomplete()
        return found

    def need(self, p, name, suffix):
        if p >= self.n:
            self.fail(self.unclosed(name, _UNCLOSED % (_open(name, suffix),
                                                       _close(name, suffix))))

    def unclosed(self, name, sentence):
        """An unclosed-tag refusal; in plain-text mode an untaught tag is not named."""
        if self.plain and name in _UNTAUGHT:
            return _PLAIN_PROGRESS_UNCLOSED if name == "progress" else _PLAIN_UNCLOSED
        return sentence

    def coerce(self, key, value):
        if key in BOOL_KEYS:
            word = _fold(value.strip())
            if word == "true":
                return True
            if word == "false":
                return False
            if self.live:
                return value
            raise ProtocolError(_NOT_BOOL % (key, value))
        if key in INT_KEYS:
            number = value.strip()
            if _INT.fullmatch(number):
                return int(number)
        return value

    # -- the top of the reply --

    def reply(self, start=0):
        text, low = self.text, self.low
        blocks, extra, wrapped = [], {}, False
        p = start
        while True:
            slash = text.find("/", p)
            if slash < 0:
                self.done = self.n
                break
            self.done = slash
            found = self.tag_at(slash)
            if found is None or found.group(1):
                p = slash + 1 if found is None else found.end()
                continue
            name, suffix = found.group(2).lower(), found.group(3)
            if name in REPLY_LEAVES or name == "events":
                if name == "events":
                    value, p = self.container(name, suffix, found.end(), _NESTED)
                else:
                    value, p = self.leaf(name, suffix, found.end(), slash,
                                         live_kind=name)
                if name in extra and not self.live:
                    raise ProtocolError(_TWICE_BESIDE % name)
                extra[name] = value
            elif name == "actions":
                value, p = self.container(name, suffix, found.end(), _TOP)
                blocks.extend(value)
                wrapped = True
                self.whole(slash, p)
            elif name in self.known or (
                    not self.live and low.find(_fold(_close(name, suffix)), found.end()) >= 0):
                block, p = self.action(name, suffix, found.end(), _TOP, slash)
                blocks.append(block)
                self.whole(slash, p)
            else:
                p = found.end()
        if self.live:
            return None
        return self.assemble(blocks, extra, wrapped)

    def assemble(self, blocks, extra, wrapped):
        if wrapped or len(blocks) > 1:
            out = {"actions": blocks}
            out.update(extra)
            return out
        if not blocks:
            return dict(extra) if extra else None
        out = blocks[0]
        for key, value in extra.items():
            if key in out:
                raise ProtocolError(_BOTH_PLACES % (key, out["action"]))
            out[key] = value
        return out

    def whole(self, start, end):
        """Live: a top-level block has closed, so report it as an object."""
        if not self.live:
            return
        try:
            block = _Reader(self.text[start:end]).reply()
        except ProtocolError:
            return
        if block is not None:
            self.emit(start, "object", json.dumps(block, ensure_ascii=False))

    # -- actions, bodies, containers, items, leaves --

    def action(self, name, suffix, pos, where, at):
        closer = _fold(_close(name, suffix))
        # Every action is reported, wherever it sits: an action can only be
        # at the top, in a wrapper or in /calls/, and the JSON parser reports
        # every "action" value it meets as well.
        self.emit(at, "action", name)
        p = self.skip(pos)
        self.need(p, name, suffix)
        if self.low.startswith(closer, p):
            return {"action": name}, p + len(closer)
        if self.first_is_key(p, closer, name, suffix):
            body, end = self.body(closer, p, name, suffix, where)
            out = {"action": name}
            out.update(body)
            return out, end
        key = primary_key(name)
        speak = where in (_TOP, _WRAPPED) and key == "message"
        value, end = self.until(closer, pos, at, speak, name, suffix,
                                key in EXACT_KEYS)
        out = {"action": name}
        if key is not None and key not in CONTAINER_KEYS:
            out[key] = self.coerce(key, value)
        elif self.live:
            pass          # malformed, but the live view carries on past it
        elif key is None:
            raise ProtocolError(_NO_BARE % (_open(name, suffix), _open(name, suffix),
                                            _close(name, suffix)))
        else:
            entry = "/read_file/ ... //read_file/" if key in ACTION_CONTAINERS \
                else "/item/ ... //item/"
            raise ProtocolError(_BARE_LIST % (_open(name, suffix), key,
                                              _open(name, suffix), key, entry, key,
                                              _close(name, suffix)))
        return out, end

    def first_is_key(self, p, closer, name, suffix):
        """Rule 2: whether the body that starts at p is made of key tags.

        It is when its first token is an open tag whose own closer appears
        before the enclosing one. A tie -- `/item/ /item/ 3 //item/ ...`,
        where the two closers are the same text -- is a key: its closer is
        the first one there is.
        """
        found = self.tag_at(p)
        if found is None or found.group(1):
            return False
        inner = self.low.find(_fold(_close(found.group(2), found.group(3))),
                              found.end())
        outer = self.low.find(closer, found.end())
        if inner >= 0 and (outer < 0 or inner <= outer):
            return True
        if outer >= 0:
            return False
        # Neither closer has arrived. Live, a tag here is far more often a
        # key than the start of a path, so it is read as one until the text
        # says otherwise; finished, the block is simply unclosed.
        if self.live:
            return True
        raise ProtocolError(self.unclosed(name, _UNCLOSED % (_open(name, suffix),
                                                             _close(name, suffix))))

    def body(self, closer, p, name, suffix, where):
        owner = _open(name, suffix)
        out = {}
        while True:
            p = self.skip(p)
            self.need(p, name, suffix)
            if self.low.startswith(closer, p):
                return out, p + len(closer)
            found = self.tag_at(p)
            if found is None:
                if self.live:
                    following = self.text.find("/", p + 1)
                    if following < 0:
                        raise _Incomplete()
                    p = following
                    continue
                raise ProtocolError(_LOOSE % (_snippet(self.text[p:]), owner))
            if found.group(1):
                if self.live:
                    p = found.end()
                    continue
                raise ProtocolError(_STRAY_CLOSER % (found.group(0), owner,
                                                     _close(name, suffix)))
            key, ksuffix = found.group(2).lower(), found.group(3)
            if key == "action" and not self.live:
                raise ProtocolError(_ACTION_KEY)
            if key in out and not self.live:
                if self.plain and key == "progress":
                    raise ProtocolError(_PLAIN_PROGRESS_TWICE)
                if self.plain and name in _UNTAUGHT:
                    raise ProtocolError(_PLAIN_TWICE % key)
                raise ProtocolError(_TWICE % (_open(key), owner))
            if key in CONTAINER_KEYS:
                value, p = self.container(key, ksuffix, found.end(), where)
            else:
                value, p = self.leaf(
                    key, ksuffix, found.end(), found.start(),
                    live_kind=key if where == _TOP and key in REPLY_LEAVES else None,
                    speak=where in (_TOP, _WRAPPED) and key == "message")
                value = self.coerce(key, value)
            if key != "action":
                out[key] = value

    def container(self, key, suffix, pos, where):
        closer = _fold(_close(key, suffix))
        p = self.skip(pos)
        self.need(p, key, suffix)
        if self.low.startswith(closer, p):
            return ({} if key in DICT_CONTAINERS else []), p + len(closer)
        found = self.tag_at(p)
        if found is None:
            if key in ACTION_CONTAINERS:
                self.fail(_NOT_AN_ACTION % (_open(key, suffix),
                                            _snippet(self.text[p:])))
            if key in DICT_CONTAINERS:
                self.fail(_DICT_TEXT % _open(key, suffix))
            self.fail(_LIST_TEXT % (_open(key, suffix), _open(key, suffix),
                                    _close(key, suffix)))
        if found.group(1):
            self.fail(_STRAY_CLOSER % (found.group(0), _open(key, suffix),
                                       _close(key, suffix)))
        if key in ACTION_CONTAINERS:
            child = _WRAPPED if key == "actions" and where == _TOP else _CALL
            return self.actions(closer, p, key, suffix, child)
        if found.group(2).lower() == "item":
            return self.items(closer, p, key, suffix)
        return self.body(closer, p, key, suffix, _NESTED)

    def actions(self, closer, p, key, suffix, where):
        out = []
        while True:
            p = self.skip(p)
            self.need(p, key, suffix)
            if self.low.startswith(closer, p):
                return out, p + len(closer)
            found = self.tag_at(p)
            if found is None or found.group(1):
                self.fail(_NOT_AN_ACTION % (_open(key, suffix),
                                            _snippet(self.text[p:])))
            block, p = self.action(found.group(2).lower(), found.group(3),
                                   found.end(), where, found.start())
            out.append(block)

    def items(self, closer, p, key, suffix):
        out = []
        while True:
            p = self.skip(p)
            self.need(p, key, suffix)
            if self.low.startswith(closer, p):
                return out, p + len(closer)
            found = self.tag_at(p)
            if found is None:
                self.fail(_LIST_TEXT % (_open(key, suffix), _open(key, suffix),
                                        _close(key, suffix)))
            if found.group(1):
                self.fail(_STRAY_CLOSER % (found.group(0), _open(key, suffix),
                                           _close(key, suffix)))
            if found.group(2).lower() != "item":
                self.fail(_MIXED % (_open(key, suffix), found.group(0)))
            value, p = self.item(found.group(3), found.end())
            out.append(value)

    def item(self, suffix, pos):
        closer = _fold(_close("item", suffix))
        p = self.skip(pos)
        self.need(p, "item", suffix)
        if self.low.startswith(closer, p):
            return "", p + len(closer)
        if self.first_is_key(p, closer, "item", suffix):
            return self.body(closer, p, "item", suffix, _NESTED)
        return self.until(closer, pos, pos, False, "item", suffix, False)

    def leaf(self, key, suffix, pos, at, live_kind=None, speak=False):
        value, end = self.until(_fold(_close(key, suffix)), pos, at, speak,
                                key, suffix, key in EXACT_KEYS)
        if live_kind and value:
            self.emit(at, live_kind, value)
        return value, end

    def until(self, closer, pos, at, speak, name, suffix, exact):
        """A text leaf: everything up to the first `closer`, rule 3 applied.

        `exact` is whether the value is one of the EXACT_KEYS -- for the
        shorthand, whether the action's first key is.
        """
        end = self.low.find(closer, pos)
        if end < 0:
            if self.live:
                if speak:
                    self.partial(at, self.text[pos:], closer)
                raise _Incomplete()
            other = "b" if _fold(suffix or "") == "a" else "a"
            raise ProtocolError(self.unclosed(name, _NEVER_CLOSED % (
                _open(name, suffix), _close(name, suffix), _close(name, suffix),
                _open(name, other), _close(name, other))))
        value = _leaf_value(self.text[pos:end], exact)
        if speak and value:
            self.emit(at, "text", value)
        return value, end + len(closer)

    def partial(self, at, raw, closer):
        """Live: the part of an unfinished message that is certainly its value.

        Only a message is ever streamed, and a message is trimmed (rule 3), so
        held back are: a tail that could be the start of the closer, and any
        whitespace -- newlines included -- that may yet turn out to be the end
        of the value; leading whitespace is never shown at all. What is
        reported is therefore always the beginning of the value the finished
        leaf will have, which is what lets a live box never print a character
        it later has to take back.
        """
        hold = 0
        for size in range(min(len(closer) - 1, len(raw)), 0, -1):
            if _fold(raw[len(raw) - size:]) == closer[:size]:
                hold = size
                break
        raw = raw[:len(raw) - hold].lstrip(_SPACE)
        raw = raw.rstrip(_SPACE)
        if raw:
            self.emit(at, "text", raw)


    # -- PLAIN TEXT mode (parse only; see "PLAIN TEXT" in the docstring) --

    def reply_plain(self):
        """The plain-text reading; live, it also streams the top-level prose.

        Live (rule I), the prose of a segment -- the text between two action
        blocks, which reply-level blocks do not split -- is reported as
        ("text", ...) under its own key as far as it is certain: trimmed, and
        held back at a tag that is not yet decided (a partial opener, a glued
        known name whose closer may still come, a tag that may yet be
        refused). `done` is the start of the open segment, so the next pass
        re-reads the whole segment under the same key.
        """
        self.plain = True
        text, low, live = self.text, self.low, self.live
        items, extra, pieces = [], {}, []
        p = mark = after = seg = 0
        self.done = 0

        def say(upto):
            self.prose(seg, "".join(pieces) + text[mark:upto])

        while True:
            slash = text.find("/", p)
            if slash < 0:
                break
            try:
                found = self.tag_at(slash)
            except _Incomplete:
                say(slash)
                raise
            if found is None and not live:
                self.spaced(slash)
            if found is None or found.group(1):
                p = slash + 1 if found is None else found.end()
                continue
            name, suffix = found.group(2).lower(), found.group(3)
            closer_after = low.find(_fold(_close(name, suffix)), found.end()) >= 0
            glued =slash > 0 and slash != after and text[slash - 1] not in _SPACE
            top = name in self.known or name in PLAIN_REPLY_KEYS or name == "actions"
            if not top or (glued and not closer_after):
                if live and (top or self.mistaken(found, name, closer_after)):
                    say(slash)
                    raise _Incomplete()
                if not top:
                    self.mistake(found, name, closer_after)
                p = found.end()
                continue
            say(slash)
            pieces.append(text[mark:slash])
            if name in PLAIN_REPLY_KEYS:
                if name == "events":
                    value, p = self.container(name, suffix, found.end(), _NESTED)
                else:
                    value, p = self.leaf(
                        name, suffix, found.end(), slash,
                        live_kind=name if name in REPLY_LEAVES else None)
                    value = self.coerce(name, value)
                if name in extra and not live:
                    raise ProtocolError(_PLAIN_PROGRESS_TWICE if name == "progress"
                                        else _TWICE_BESIDE % name)
                extra[name] = value
            else:
                outside = "".join(pieces).strip(_SPACE)
                pieces = []
                if name == "actions":
                    value, p = self.container(name, suffix, found.end(), _TOP)
                    for index, block in enumerate(value):
                        items.append((block, outside if index == 0 else ""))
                else:
                    block, p = self.action(name, suffix, found.end(), _TOP, slash)
                    items.append((block, outside))
                self.whole(slash, p)
                seg = self.done = p
            mark = after = p
        if live:
            say(len(text))
            return None
        trailing = ("".join(pieces) + text[mark:]).strip(_SPACE)
        return _assemble_plain(items, extra, trailing)

    def prose(self, at, raw):
        """Live: one segment's top-level prose, trimmed, as a text event."""
        if self.live:
            value = raw.strip(_SPACE)
            if value:
                self.events.append((("prose", self.base + at), "text", value))

    def mistake(self, found, name, closer_after):
        """Rule G: refuse a near-miss of a known name, or a key at the top."""
        sentence = self.mistaken(found, name, closer_after)
        if sentence:
            raise ProtocolError(sentence)

    def spaced(self, slash):
        """Rule G: a taught name with blanks inside its slashes, closer following."""
        found = _SPACED_TAG.match(self.text, slash)
        if found is None:
            return
        name = (found.group(2) or found.group(3)).lower()
        if name not in _known_actions() and name not in PLAIN_REPLY_KEYS:
            return
        closer = re.compile(r"/[ \t]*" + re.escape(name) + r"[ \t]*/", re.I)
        if closer.search(self.text, found.end()) is None:
            return
        raise ProtocolError(_SPACED % (found.group(0), _open(name)))

    def mistaken(self, found, name, closer_after):
        """The rule-G refusal for this tag, or None when it is prose."""
        if not (closer_after or self.text.strip(_SPACE) == found.group(0)):
            return None
        if name in _key_names():
            if name == "message":
                return _KEY_MESSAGE
            return _KEY_AT_TOP % (_open(name), _open(name))
        near = _nearest(name)
        if near is None:
            return None
        if near in _UNTAUGHT:
            return _UNTAUGHT_NEAR % found.group(0)
        return _NEAR_MISS % (found.group(0), _open(near))


# Plain-text mode: the names that are no longer taught, the verbs that speak,
# and the sentences a refusal hands back. None of them mentions JSON.
_UNTAUGHT = frozenset({"progress", "send_message", "end_conversation"})
_SPEAKING = frozenset({"send_message", "announce", "end_conversation", "done", "respond"})
# A near-miss of a legacy name is answered with the name in force now.
_LEGACY_TAUGHT = {"respond": "end_conversation", "done": "end_conversation",
                  "announce": "send_message", "search_files": "grep",
                  "find_text": "grep", "run_file": "bash", "run_python": "bash"}
_PLAIN_MESSAGE = ("A message to the user is written as plain text outside any "
                  "block, and a reply with no action blocks is the final answer.")
_NEAR_MISS = "%s is not an action TMT knows; did you mean %s?"
_UNTAUGHT_NEAR = "%s is not a tag TMT reads. " + _PLAIN_MESSAGE
_SPACED = "%s has blanks inside its slashes. Write it without them: %s."
_KEY_MESSAGE = ("/message/ is not needed at the top of a reply. " + _PLAIN_MESSAGE)
_KEY_AT_TOP = ("%s is a key, not an action: write %s inside the action block it "
               "belongs to, as /read_file/ /path/ src/a.py //path/ //read_file/.")
# Rule J: the parser's own refusals about the untaught tags, in plain form.
_PLAIN_UNCLOSED = "That tag is never closed, and none is needed. " + _PLAIN_MESSAGE
_PLAIN_PROGRESS = ("A progress line needs no tag: write it as plain text just "
                   "before the action block it describes.")
_PLAIN_PROGRESS_UNCLOSED = "That progress tag is never closed. " + _PLAIN_PROGRESS
_PLAIN_PROGRESS_TWICE = "The progress line is given twice. " + _PLAIN_PROGRESS
_PLAIN_TWICE = "/%s/ is given twice. Give it once."


def _plain_both(key, action):
    """A key given inside a block and beside it, said in plain-text words."""
    if key == "progress":
        return _PLAIN_PROGRESS_TWICE
    if action in _SPEAKING:
        return _PLAIN_TWICE % key
    return _BOTH_PLACES % (key, action)


def _ends(obj):
    """Whether an action object ends the turn (the old ending verbs)."""
    name = obj.get("action")
    return name in ("end_conversation", "done") or (
        name == "respond" and obj.get("final", True) is not False)


def _assemble_plain(items, extra, trailing):
    """Rules B-E: the object a plain-text reply's blocks and text describe."""
    if not items:
        if not trailing:
            return dict(extra) if extra else None
        out = {"action": "end_conversation", "message": trailing}
        out.update(extra)
        return out
    work = any(block.get("action") not in _SPEAKING for block, _ in items)
    entries, inside = [], []
    for block, outside in items:
        had = "progress" in block
        if work and _ends(block):
            step = block.pop("next_step", None)
            if step is not None:
                if "next_step" in extra:
                    raise ProtocolError(_plain_both("next_step", block["action"]))
                extra["next_step"] = step
            block.pop("final", None)
            block["action"] = "send_message"
        if outside:
            explicit = block.get("progress")
            block["progress"] = outside + " " + explicit if explicit else outside
        entries.append(block)
        inside.append(had)
    if trailing:
        last = entries[-1]
        if len(entries) == 1 and _ends(last):
            said = last.get("message")
            last["message"] = said + "\n\n" + trailing if said else trailing
        else:
            entries.append({"action": "send_message", "message": trailing})
            inside.append(False)
    if len(entries) > 1:
        out = {"actions": entries}
        out.update(extra)
        return out
    out = entries[0]
    for key, value in extra.items():
        if key == "progress" and not inside[0]:
            out["progress"] = out["progress"] + " " + value if out.get("progress") else value
            continue
        if key in out:
            raise ProtocolError(_plain_both(key, out["action"]))
        out[key] = value
    return out


_KEYS = None


def _key_names():
    """Every key name an action is known to take, minus the top-level names."""
    global _KEYS
    if _KEYS is None:
        keys = {key for keys in _required_keys().values() for key in keys}
        keys |= BOOL_KEYS | INT_KEYS | CONTAINER_KEYS | set(EXACT_KEYS)
        keys |= {"item", "message", "command"}
        _KEYS = frozenset(keys - _known_actions() - REPLY_KEYS - {"actions"})
    return _KEYS


def _distance(a, b, limit=2):
    """Levenshtein distance, or limit + 1 once it is certainly past `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    row = list(range(len(b) + 1))
    for i, char in enumerate(a, 1):
        new = [i]
        for j, other in enumerate(b, 1):
            new.append(min(row[j] + 1, new[j - 1] + 1, row[j - 1] + (char != other)))
        if min(new) > limit:
            return limit + 1
        row = new
    return row[-1]


def _nearest(name):
    """The taught name `name` is a near-miss of (distance 2 or less), or None.

    Nearest wins; a tie goes to the first candidate in alphabetical order.
    """
    best = None
    for known in sorted(_known_actions() | REPLY_KEYS):
        gap = _distance(name, known)
        if gap <= 2 and (best is None or gap < best[0]):
            best = (gap, known)
    if best is None:
        return None
    return _LEGACY_TAUGHT.get(best[1], best[1])


def parse_tags(text, *, plain_text=False):
    """The object a TAGS reply describes, or None when it holds no tags.

    With `plain_text`, the PLAIN TEXT rules in the module docstring apply and
    the answer is None only for blank text.

    Raises ProtocolError with a sentence the model can act on.
    """
    if not isinstance(text, str):
        raise ProtocolError("A reply is text, not %r." % (type(text).__name__,))
    reader = _Reader(text.replace("\r\n", "\n"))
    return reader.reply_plain() if plain_text else reader.reply()


# --- telling the two apart, and reading either --------------------------------

def _object_end(text, start):
    """The index after the JSON object opening at `start`, or -1.

    String-aware, so a `{` or `}` inside a string does not count.
    """
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return -1


def _first_object(text):
    """Where the first JSON object carrying an action starts, or -1."""
    at = text.find("{")
    while at >= 0:
        end = _object_end(text, at)
        if end < 0:
            return -1
        try:
            value = json.loads(text[at:end])
        except ValueError:
            value = None
        if isinstance(value, dict) and ("action" in value or "actions" in value):
            return at
        at = text.find("{", at + 1)
    return -1


def _first_tag(text):
    """Where the first known top-level tag with a closer after it starts, or -1."""
    known = _known_actions() | REPLY_KEYS | frozenset({"actions"})
    low = _fold(text)
    for found in _TAG.finditer(text):
        if found.group(1):
            continue
        name = found.group(2).lower()
        if name in known and low.find(_fold(_close(name, found.group(3))),
                                      found.end()) >= 0:
            return found.start()
    return -1


_JSON_START = re.compile(r'\{\s*"actions?"')


def _locate_plain(text, start):
    """Rule F: JSON only for an object reply or the JSON-drift case; else TAGS."""
    if text[start] == "{":
        if _JSON_START.match(text, start):
            return JSON, start
        end = _object_end(text, start)
        if end >= 0 and not text[end:].strip():
            try:
                whole = json.loads(text[start:end])
            except ValueError:
                whole = None
            if isinstance(whole, dict):
                return JSON, start
    at = _first_object(text)
    if at >= 0:
        tag = _first_tag(text)
        if tag < 0 or at < tag:
            return JSON, at
    return TAGS, start


def _locate(text, plain_text=False):
    """(protocol or None, where the reply starts)."""
    if not isinstance(text, str):
        return None, -1
    start = len(text) - len(text.lstrip())
    if start >= len(text):
        return None, -1
    if plain_text:
        return _locate_plain(text, start)
    if text[start] == "{":
        return JSON, start
    found = _TAG.match(text, start)
    if found and not found.group(1):
        return TAGS, start
    # Prose first. A JSON reply has always been read wherever its object
    # starts, and rule 8 says the same of tags; the earlier of the two wins.
    found = [(at, kind) for at, kind in ((_first_tag(text), TAGS),
                                         (_first_object(text), JSON)) if at >= 0]
    if not found:
        return None, -1
    at, kind = min(found)
    return kind, at


def detect(text, *, plain_text=False):
    """What a reply looks like: JSON, TAGS, or None for neither.

    With `plain_text` any text that is not blank and not JSON is TAGS.
    """
    return _locate(text, plain_text)[0]


def locate(text, *, plain_text=False):
    """(JSON, TAGS or None, the index the reply starts at, or -1).

    `detect` with the position as well, for a caller that has to cut the
    prose in front of a reply away before handing the rest to something
    that only reads one protocol. With `plain_text` a TAGS reply starts at its
    first character that is not whitespace, because its prose is part of it.
    """
    return _locate(text, plain_text)


def normal(protocol):
    """`protocol` when it names one of the two, and the default otherwise.

    The setting is validated where it is read, so this only ever matters to
    a caller that was handed something else; it answers the way the setting
    itself answers a file edited into nonsense.
    """
    return protocol if protocol in PROTOCOLS else DEFAULT_PROTOCOL


def _parse_json(text, at):
    end = _object_end(text, at)
    if end < 0:
        raise ProtocolError("The reply opens a JSON object and never closes it: "
                            "every { needs its }.")
    try:
        value = json.loads(text[at:end])
    except ValueError as error:
        raise ProtocolError("The reply is not valid JSON: %s." % error)
    if not isinstance(value, dict):
        raise ProtocolError("The reply must be one JSON object, not %s."
                            % type(value).__name__)
    return value


def parse(text, *, plain_text=False):
    """The action object a reply describes, in whichever protocol it is in.

    None when the text is neither -- the caller's prose path. Raises
    ProtocolError with a sentence the model can act on. With `plain_text`
    the PLAIN TEXT rules apply, the whole text is read, and None means blank.
    """
    kind, at = _locate(text, plain_text)
    if kind is None:
        return None
    if kind == JSON:
        return _parse_json(text, at)
    if plain_text:
        return parse_tags(text, plain_text=True)
    return parse_tags(text[at:])


# --- writing ------------------------------------------------------------------

def render(obj, protocol=TAGS, *, plain_text=False):
    """An action object written in a protocol. TAGS is the inverse of parse_tags.

    With `plain_text` under TAGS (rule H) the reply is written the way the
    PLAIN TEXT rules read it, and checked by reading it back with
    `parse(..., plain_text=True)`: a lone end_conversation is its message as
    plain text, followed by any /next_step/ and /events/ blocks; a `progress`
    is a prose line before its block; a batch's final send_message is
    trailing text; a lone send_message is the old /send_message/ block. An
    end_conversation inside a batch, a send_message that is not a batch's
    last entry, a batch of one, a batch-level progress, and any key an ending
    cannot carry as plain text all raise ProtocolError. Under JSON
    `plain_text` is ignored.

    JSON is the compact form the prompt's examples are written in. TAGS is
    inline when the action fits on a line of INLINE_WIDTH and no value holds a
    newline, block form otherwise; the shorthand is used only for
    send_message, end_conversation and internal_response carrying their one
    key. Raises ProtocolError for an object tags cannot express exactly.

    Every value except the EXACT_KEYS (content, search, replace) is written
    TRIMMED of leading and trailing whitespace, newlines included, because
    that is how rule 3 reads it back: nothing is lost on the wire that the
    reader would have kept. An exact value keeps its whitespace, except that
    spaces or tabs at its very end cannot be written and are dropped.
    """
    if protocol == JSON:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    _known_protocol(protocol)
    if plain_text:
        return _render_plain_checked(obj, INLINE_WIDTH)
    return _render_checked(obj, INLINE_WIDTH)


def example(obj, protocol=TAGS, *, plain_text=False):
    """An action written on one line, for use inside a sentence.

    Block form only when a value cannot be written inline: it holds a newline,
    or it is file text (an EXACT_KEYS value) that starts with a space or tab.
    `plain_text` is `render`'s (rule H).
    """
    if protocol == JSON:
        return render(obj, JSON)
    _known_protocol(protocol)
    if plain_text:
        return _render_plain_checked(obj, None)
    return _render_checked(obj, None)


def hint(obj, as_json=None):
    """An action written in the reply format in force right now, for a sentence.

    This is what every refusal and result that shows the model an action that
    would work calls, so the modules that build those sentences share one
    reading of the setting instead of each carrying a private copy. The format
    is read HERE, at call time and never at import: it is a setting the user
    can change between two turns, and a hint that taught the other shape would
    be teaching the model to leave the one it was asked to use. `agent_config`
    is imported inside the function, for the reason the tool modules are
    imported lazily everywhere in TMT: a module list frozen at install time
    must not be able to stop a module loading. If the setting cannot be read,
    or the object cannot be written in that format, the answer is compact
    JSON, which is what these sentences were before there was a choice.

    `as_json` is the exact text a sentence has always carried under JSON, for
    the one hint `json.dumps` cannot reproduce byte for byte.
    """
    try:
        import agent_config
        protocol = agent_config.PROTOCOL
    except Exception:
        protocol = JSON
    if protocol == JSON and as_json is not None:
        return as_json
    try:
        return example(obj, protocol)
    except Exception:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _known_protocol(protocol):
    if protocol not in PROTOCOLS:
        raise ProtocolError("%r is not a reply format; use %s."
                            % (protocol, " or ".join(PROTOCOLS)))


def _render_checked(obj, width):
    if not isinstance(obj, dict):
        raise ProtocolError("Only an object can be written as tags, not %r." % (obj,))
    pieces = _render_reply(obj, width)
    if width is None and all(len(piece) == 1 for piece in pieces):
        text = " ".join(piece[0] for piece in pieces)
    else:
        text = "\n".join(line for piece in pieces for line in piece)
    # Read back before it is handed out. Rendering is only worth anything if
    # it is exact, and a shape that cannot be expressed should say so here
    # rather than reach a model as something it did not mean.
    try:
        back = parse_tags(text)
    except ProtocolError as error:
        raise ProtocolError("That object cannot be written as tags exactly: %s" % error)
    if back != _canonical(obj):
        raise ProtocolError("That object cannot be written as tags exactly: it "
                            "would read back as %r." % (back,))
    return text


_PLAIN_END_IN_BATCH = ("An end_conversation inside a batch has no plain-text "
                       "form: a reply that runs anything cannot end the turn.")
_PLAIN_EARLY_MESSAGE = ("A send_message that is not the last entry of a batch "
                        "has no plain-text form: only text after the last block "
                        "is a message.")
_PLAIN_ONE = "A batch of one action has no plain-text form: write the action alone."
_PLAIN_BATCH_PROGRESS = ("A progress beside a batch has no plain-text form: give "
                         "each action its own.")
_PLAIN_ENDING_KEY = ("%s cannot carry %r as plain text: an ending is its message, "
                     "with only next_step and events beside it.")
_PLAIN_BLANK = "%s has no plain-text form without a message."


def _render_plain_checked(obj, width):
    """Rule H: `obj` as a plain-text reply, read back before it is handed out."""
    if not isinstance(obj, dict):
        raise ProtocolError("Only an object can be written as tags, not %r." % (obj,))
    pieces = _render_plain(obj, width)
    if width is None and all(len(piece) == 1 for piece in pieces):
        text = " ".join(piece[0] for piece in pieces)
    else:
        text = "\n".join(line for piece in pieces for line in piece)
    try:
        back = parse(text, plain_text=True)
    except ProtocolError as error:
        raise ProtocolError("That object cannot be written as plain-text tags "
                            "exactly: %s" % error)
    if back != _canonical(obj):
        raise ProtocolError("That object cannot be written as plain-text tags "
                            "exactly: it would read back as %r." % (back,))
    return text


def _prose_piece(value):
    return _scalar(value).strip(_SPACE).split("\n")


def _plain_ending(obj, width):
    """A lone end_conversation: its message as text, then its reply-level blocks."""
    beside = [key for key in obj if key not in ("action", "message")]
    for key in beside:
        if key not in PLAIN_REPLY_KEYS or key == "progress":
            raise ProtocolError(_PLAIN_ENDING_KEY % (obj["action"], key))
    message = obj.get("message")
    if not isinstance(message, str) or not message.strip(_SPACE):
        raise ProtocolError(_PLAIN_BLANK % obj["action"])
    return [_prose_piece(message)] + [_render_key(key, obj[key], width)
                                      for key in beside]


def _plain_block(obj, width):
    """An action with its progress, if any, as a prose line before the block."""
    if not isinstance(obj, dict):
        raise ProtocolError("An action must be an object, not %r." % (obj,))
    rest = {key: value for key, value in obj.items() if key != "progress"}
    block = _render_block(rest, width)
    if "progress" in obj:
        return [_prose_piece(obj["progress"]), block]
    return [block]


def _render_plain(obj, width):
    if "action" in obj:
        if obj.get("action") == "end_conversation":
            return _plain_ending(obj, width)
        return _plain_block(obj, width)
    if "actions" not in obj:
        if "progress" in obj:
            raise ProtocolError(_PLAIN_BATCH_PROGRESS)
        return _render_reply(obj, width)
    actions = obj["actions"]
    if not isinstance(actions, (list, tuple)) or not actions:
        raise ProtocolError("\"actions\" must be a list of actions.")
    if len(actions) == 1:
        raise ProtocolError(_PLAIN_ONE)
    extra = [key for key in obj if key != "actions"]
    for key in extra:
        if key not in PLAIN_REPLY_KEYS:
            raise ProtocolError("%r cannot sit beside a batch; only %s can."
                                % (key, ", ".join(sorted(PLAIN_REPLY_KEYS))))
    if "progress" in extra:
        raise ProtocolError(_PLAIN_BATCH_PROGRESS)
    pieces, last = [], len(actions) - 1
    for index, entry in enumerate(actions):
        if isinstance(entry, dict) and _ends(entry):
            raise ProtocolError(_PLAIN_END_IN_BATCH)
        if isinstance(entry, dict) and entry.get("action") == "send_message":
            if index != last:
                raise ProtocolError(_PLAIN_EARLY_MESSAGE)
            if set(entry) != {"action", "message"} or not isinstance(
                    entry["message"], str) or not entry["message"].strip(_SPACE):
                raise ProtocolError("A closing send_message is its message alone, "
                                    "written as text after the last block.")
            pieces.append(_prose_piece(entry["message"]))
            continue
        pieces.extend(_plain_block(entry, width))
    return pieces + [_render_key(key, obj[key], width) for key in extra]


def _render_reply(obj, width):
    if "action" in obj:
        return [_render_block(obj, width)]
    if "actions" in obj:
        actions = obj["actions"]
        if not isinstance(actions, (list, tuple)):
            raise ProtocolError("\"actions\" must be a list of actions.")
        extra = [key for key in obj if key != "actions"]
        for key in extra:
            if key not in REPLY_KEYS:
                raise ProtocolError("%r cannot sit beside a batch; only %s can."
                                    % (key, ", ".join(sorted(REPLY_KEYS))))
        blocks = [_render_block(entry, width) for entry in actions]
        pieces = blocks if len(blocks) > 1 else [_wrap("actions", blocks, width)]
        return pieces + [_render_key(key, obj[key], width) for key in extra]
    if not obj:
        raise ProtocolError("An empty object has no tags form.")
    for key in obj:
        if key not in REPLY_KEYS:
            raise ProtocolError("%r is neither an action nor a reply-level key." % (key,))
    return [_render_key(key, value, width) for key, value in obj.items()]


def _render_block(obj, width):
    if not isinstance(obj, dict):
        raise ProtocolError("An action must be an object, not %r." % (obj,))
    name = obj.get("action")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ProtocolError("%r cannot be an action tag." % (name,))
    keys = [key for key in obj if key != "action"]
    if (name in SHORTHAND_VERBS and len(keys) == 1 and keys[0] == primary_key(name)
            and isinstance(obj[keys[0]], str) and _bare_ok(obj[keys[0]])):
        return _render_leaf(name, obj[keys[0]], exact=keys[0] in EXACT_KEYS)
    return _wrap(name, [_render_key(key, obj[key], width) for key in keys], width)


def _bare_ok(value):
    """Whether a value can be an action's whole body without being misread."""
    stripped = value.lstrip(_SPACE)
    if not stripped.strip(_SPACE):
        return False
    found = _TAG.match(stripped)
    return not (found and not found.group(1))


def _render_key(key, value, width):
    if not isinstance(key, str) or not _NAME.fullmatch(key) or key == "action":
        raise ProtocolError("%r cannot be a key tag." % (key,))
    if key in CONTAINER_KEYS:
        if isinstance(value, dict):
            if not value and key not in DICT_CONTAINERS:
                raise ProtocolError("An empty /%s/ reads back as a list." % key)
            return _wrap(key, [_render_key(k, v, width) for k, v in value.items()], width)
        if isinstance(value, (list, tuple)):
            if not value and key in DICT_CONTAINERS:
                raise ProtocolError("An empty /%s/ reads back as an object." % key)
            if key in ACTION_CONTAINERS:
                children = [_render_block(entry, width) for entry in value]
            else:
                children = [_render_item(entry, width) for entry in value]
            return _wrap(key, children, width)
        raise ProtocolError("/%s/ holds a list or an object, not %r." % (key, value))
    if isinstance(value, (dict, list, tuple)):
        raise ProtocolError("/%s/ is a text key and cannot hold %s; only %s do."
                            % (key, type(value).__name__,
                               ", ".join(sorted(CONTAINER_KEYS))))
    return _render_leaf(key, _scalar(value), exact=key in EXACT_KEYS)


def _render_item(entry, width):
    if isinstance(entry, dict):
        if not entry:
            raise ProtocolError("An empty object in a list reads back as text.")
        return _wrap("item", [_render_key(k, v, width) for k, v in entry.items()], width)
    if isinstance(entry, (list, tuple)):
        raise ProtocolError("A list inside a list has no tags form.")
    text = _scalar(entry).strip(_SPACE)
    # An entry whose text starts with `/item/` would tie with its own closer
    # and read as an object (rule 2), so its closer takes a different suffix.
    found = _TAG.match(text)
    avoid = _close("item", found.group(3)) if found and not found.group(1) \
        and found.group(2).lower() == "item" else None
    return _render_leaf("item", text, avoid, exact=False)


def _scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    raise ProtocolError("%r has no tags form: a value is text, a number, true or "
                        "false, a list or an object." % (value,))


def _suffixes():
    yield None
    for letter in "abcdefghijklmnopqrstuvwxyz":
        yield letter
    for number in range(1, 1000):
        yield "s%d" % number


def _free_suffix(tag, body, avoid=None):
    """The suffix that keeps `body` from containing its own closer."""
    for suffix in _suffixes():
        closer = _fold(_close(tag, suffix))
        if avoid is not None and closer == _fold(avoid):
            continue
        if _fold(body + closer).find(closer) == len(body):
            return suffix
    raise ProtocolError("No suffix keeps /%s/ from closing early." % tag)


def _render_leaf(tag, text, avoid=None, exact=True):
    if not exact:
        # Trimmed on the way back in, so trimmed on the way out -- and that
        # lets a value with a newline in it put its closer on its own line.
        text = text.strip(_SPACE)
        body = "\n" + text + "\n" if "\n" in text else " " + text + " " if text else " "
    elif "\n" in text or text[:1] in (" ", "\t"):
        body = "\n" + text
    elif text:
        body = " " + text + " "
    else:
        body = " "
    suffix = _free_suffix(tag, body, avoid)
    return (_open(tag, suffix) + body + _close(tag, suffix)).split("\n")


def _wrap(tag, children, width):
    if all(len(child) == 1 for child in children):
        inner = " ".join(child[0] for child in children)
        body = " " + inner + " " if inner else " "
        suffix = _free_suffix(tag, body)
        line = _open(tag, suffix) + body + _close(tag, suffix)
        if width is None or len(line) <= width:
            return [line]
    lines = [line for child in children for line in child]
    suffix = _free_suffix(tag, "\n" + "\n".join(lines) + "\n")
    return [_open(tag, suffix)] + lines + [_close(tag, suffix)]


def _canonical(obj):
    """What `obj` reads back as once written: the types the tables give it."""
    if "action" not in obj and "actions" in obj:
        out = {"actions": [_canonical_object(entry) for entry in obj["actions"]]}
        for key, value in obj.items():
            if key != "actions":
                out[key] = _canonical_value(key, value)
        return out
    return _canonical_object(obj)


def _canonical_object(obj):
    if not isinstance(obj, dict):
        return obj
    return {key: value if key == "action" else _canonical_value(key, value)
            for key, value in obj.items()}


def _canonical_value(key, value):
    if key in CONTAINER_KEYS:
        if isinstance(value, dict):
            return _canonical_object(value)
        if isinstance(value, (list, tuple)):
            return [_canonical_object(entry) if isinstance(entry, dict)
                    or key in ACTION_CONTAINERS else _canonical_text(entry, False)
                    for entry in value]
        return value
    if isinstance(value, (dict, list, tuple)):
        return value
    text = _canonical_text(value, key in EXACT_KEYS)
    if key in BOOL_KEYS:
        word = _fold(text.strip())
        return True if word == "true" else False if word == "false" else text
    if key in INT_KEYS and _INT.fullmatch(text.strip()):
        return int(text.strip())
    return text


def _canonical_text(value, exact):
    text = _scalar(value).replace("\r\n", "\n")
    return text.rstrip(" \t") if exact else text.strip(_SPACE)


# --- prompts --------------------------------------------------------------

_EXAMPLE_LINE = re.compile(
    r"([ \t]*)((?:You emit:|BAD:|GOOD:|WRONG:|RIGHT:|Then:)[ \t]*)?(?=\{\"actions?\")")


def transliterate(text, *, plain_text=False):
    """A prompt constant with every JSON example rewritten as tags.

    A line that is an example -- optionally after a label such as "You emit:"
    or "BAD:" -- has each JSON action object in it replaced by its tags
    rendering. A line that is nothing but one object is written with `render`,
    so a long or multi-line one goes to block form on the lines after the
    label; an object sharing its line with commentary is written with
    `example`, in place. Every other line comes back byte for byte, and an
    object that cannot be read or written is left exactly as it was.

    With `plain_text` every object is written in the plain-text form (rule H)
    and nothing is left as it was: an example that cannot be read, or cannot
    be expressed in plain text (an end_conversation inside a batch, a
    send_message that is not a batch's last entry), raises ProtocolError
    naming the example, so the prompt builder finds every one that has to be
    rewritten by hand.
    """
    return "\n".join(_transliterate_line(line, plain_text) for line in text.split("\n"))


_PLAIN_EXAMPLE = ("This prompt example has no plain-text tags form and must be "
                  "rewritten by hand: %s -- %s")


def _transliterate_line(line, plain_text=False):
    found = _EXAMPLE_LINE.match(line)
    if found is None:
        return line
    indent, label = found.group(1), found.group(2) or ""
    rest = line[found.end():]
    pieces, pos = [], 0
    while True:
        at = rest.find('{"action', pos)
        end = _object_end(rest, at) if at >= 0 else -1
        if at < 0 or end < 0:
            if plain_text and at >= 0:
                raise ProtocolError(_PLAIN_EXAMPLE % (line.strip(), "it never closes"))
            pieces.append(rest[pos:])
            break
        pieces.append(rest[pos:at])
        try:
            value = json.loads(rest[at:end])
            if not isinstance(value, dict):
                raise ValueError("not an object")
            pieces.append(value)
        except ValueError as error:
            if plain_text:
                raise ProtocolError(_PLAIN_EXAMPLE % (line.strip(), error))
            pieces.append(rest[at:end])
        pos = end
    objects = [piece for piece in pieces if isinstance(piece, dict)]
    if not objects:
        return line
    if len(objects) == 1 and all(isinstance(piece, dict) or not piece.strip()
                                 for piece in pieces):
        try:
            written = render(objects[0], TAGS, plain_text=plain_text)
        except ProtocolError as error:
            if plain_text:
                raise ProtocolError(_PLAIN_EXAMPLE % (line.strip(), error))
            return line
        if "\n" not in written:
            return indent + label + written
        return (indent + label.rstrip() + "\n" + written) if label else written
    out = indent + label
    for piece in pieces:
        if not isinstance(piece, dict):
            out += piece
            continue
        try:
            written = example(piece, TAGS, plain_text=plain_text)
        except ProtocolError as error:
            if plain_text:
                raise ProtocolError(_PLAIN_EXAMPLE % (line.strip(), error))
            written = json.dumps(piece, ensure_ascii=False, separators=(",", ":"))
        if "\n" in written:
            out = out.rstrip(" \t") + "\n" + written + "\n"
        else:
            out += written
    return out


# --- streaming ------------------------------------------------------------

class StreamingTagParser(object):
    """Reads a TAGS reply as it arrives and reports what can already be shown.

    The events are the ones `agent_model.StreamingActionParser` reports, under
    the same rules:

        ("action", name)     an action's open tag, at the top of the reply or
                             inside /calls/ or /actions/
        ("text", chars)      the characters of a /message/ directly inside a
                             top-level action (or a child of a top-level
                             /actions/), or of the shorthand body of one whose
                             first key is message -- never inside /events/ or
                             /calls/
        ("progress", str)    a /progress/ beside the actions or directly
        ("next_step", str)   inside a top-level action, when it closes
        ("object", json)     a top-level block has closed: its object, as JSON

    Text is reported as it arrives with rule 3 applied, and nothing is
    reported that might still turn out to be part of a closer or the spaces in
    front of one. Feeding one character at a time reports exactly what one
    large chunk would (with neighbouring text events joined).

    `result()` is the authority: it reads the whole text with parse_tags. The
    events are the live view of the same reading and agree with it for every
    reply that parses; where a reply is malformed they may stop early.
    """

    def __init__(self, *, plain_text=False):
        # With plain_text (rule I) the live events come from the plain-text
        # reader: everything above, plus ("text", chars) for the prose outside
        # every block as it arrives -- trimmed, and held back at a tag not
        # yet decided (`_Reader.reply_plain`). `result()` reads the reply
        # with `parse(..., plain_text=True)`.
        self.plain_text = plain_text
        self.raw = ""
        self.error = None
        self._text = ""
        self._resume = 0
        self._seen = set()
        self._said = {}

    def feed(self, chunk):
        if not chunk:
            return []
        self.raw += chunk
        if self._text.endswith("\r") and chunk.startswith("\n"):
            self._text = self._text[:-1]
        self._text += chunk.replace("\r\n", "\n")
        # A trailing CR may yet become half of a CRLF, so it is not read until
        # the next character says which.
        settled = self._text[:-1] if self._text.endswith("\r") else self._text
        reader = _Reader(settled[self._resume:], live=True, base=self._resume)
        try:
            if self.plain_text:
                reader.reply_plain()
            else:
                reader.reply()
        except _Incomplete:
            pass
        out = []
        for key, kind, value in reader.events:
            if kind == "text":
                said = self._said.get(key, "")
                if len(value) > len(said) and value.startswith(said):
                    self._said[key] = value
                    if out and out[-1][0] == "text":
                        out[-1] = ("text", out[-1][1] + value[len(said):])
                    else:
                        out.append(("text", value[len(said):]))
            elif key not in self._seen:
                self._seen.add(key)
                out.append((kind, value))
        self._resume += reader.done
        return out

    def result(self):
        """The object the whole reply describes, or None; sets `error`."""
        try:
            if self.plain_text:
                value = parse(self.raw, plain_text=True)
            else:
                value = parse_tags(self.raw)
        except ProtocolError as error:
            self.error = str(error)
            return None
        self.error = None
        return value


# --- what a step loop tells a model about the shape of its reply -------------
#
# Every sentence the two step loops (TMT.py and agent_worker) and agent_multi
# hand back to a model about the SHAPE of what it wrote, in both protocols, in
# one place. Under JSON each is byte for byte the string the loop used before
# tags existed: tests pin several of them and a transcript a user has read
# should not change under them. Under TAGS the same sentence states the tag
# contract instead, so a model that was asked for tags is never corrected
# towards JSON -- the one thing a correction must not do is teach the shape
# the prompt forbids.
#
# The protocol is passed in, never read here: this module chooses no protocol
# for anybody (see the module docstring), and a loop that reads the setting
# once per reply is the one place that knows which reply it is answering.

TAG_CONTRACT = ("Reply with one or more tag blocks and nothing else -- no "
                "JSON, no prose, no code fences; start with /action_name/ and "
                "end with //action_name/.")
# The same contract under the plain-text rules (rule J): prose is allowed, and
# none of JSON, braces or the three untaught tags is named.
TAG_CONTRACT_PLAIN = ("Write each action as a tag block, /action_name/ ... "
                      "//action_name/, and anything the user should read as "
                      "plain text outside the blocks. A reply with no action "
                      "blocks is your final answer and ends the turn.")

_JSON_TYPES = ("Check the type of every key you sent: paths and text are "
               "strings, line numbers are unquoted numbers, flags are unquoted "
               "true or false.")
_TAG_TYPES = ("Check every key you sent: flags are the words true or false, "
              "numbers are bare digits, lists use /item/.")
_TAG_BLOCK = "as a tag block: /action_name/ ... //action_name/."

# kind: (the JSON sentence, the TAGS sentence). Fields are %(name)s.
_CORRECTIONS = {
    # The main loop's answer to a reply that could not be read at all.
    "unreadable": (
        "INVALID: that reply could not be read as JSON. The parser said: "
        "%(error)s\nReply with exactly one JSON object and nothing else -- no "
        "prose before it, no prose after it, no code fences. Emit the action "
        "you meant.",
        "INVALID: that reply could not be read as tags. The parser said: "
        "%(error)s\n" + TAG_CONTRACT + " Emit the action you meant."),
    # The worker loop's, which has always ended one sentence sooner.
    "worker_unreadable": (
        "INVALID: that reply could not be read as JSON. The parser said: "
        "%(error)s\nReply with exactly one JSON object and nothing else -- no "
        "prose before it, no prose after it, no code fences.",
        "INVALID: that reply could not be read as tags. The parser said: "
        "%(error)s\n" + TAG_CONTRACT),
    # What a worker's reply that was no action at all is said to be.
    "not_an_object": (
        "the reply was not a JSON object",
        "the reply held no action block"),
    "prose_before_work": (
        "That was prose, and nothing has run yet, so it announced work rather "
        "than reporting it. Emit the action you just described, as one JSON "
        "object.",
        "That was prose, and nothing has run yet, so it announced work rather "
        "than reporting it. Emit the action you just described, " + _TAG_BLOCK),
    "worker_prose": (
        "That was prose, and nothing has run yet, so it described work rather "
        "than doing it. Emit the action you just described, as one JSON object.",
        "That was prose, and nothing has run yet, so it described work rather "
        "than doing it. Emit the action you just described, " + _TAG_BLOCK),
    # Both loops, word for word the same.
    "invalid": (
        "INVALID: %(error)s. Output a corrected action JSON.",
        "INVALID: %(error)s. Output the corrected action as a tag block."),
    "invalid_in_batch": (
        "INVALID: %(invalid)s\nRan before it:\n%(ran)s\nOutput a corrected "
        "action JSON.",
        "INVALID: %(invalid)s\nRan before it:\n%(ran)s\nOutput the corrected "
        "action as a tag block."),
    "actions_not_list": (
        "INVALID: 'actions' must be a non-empty list. Try again.",
        "INVALID: a batch must hold at least one action block. Try again."),
    # `raised` is the exception's type name and `error` its text.
    "action_raised": (
        "INVALID: the action '%(action)s' could not run with those arguments "
        "-- it raised %(raised)s: %(error)s\n" + _JSON_TYPES
        + " Emit a corrected action.",
        "INVALID: the action '%(action)s' could not run with those arguments "
        "-- it raised %(raised)s: %(error)s\n" + _TAG_TYPES
        + " Emit a corrected action."),
    "worker_action_raised": (
        "INVALID: the action '%(action)s' could not run with those arguments "
        "-- it raised %(raised)s: %(error)s\n" + _JSON_TYPES,
        "INVALID: the action '%(action)s' could not run with those arguments "
        "-- it raised %(raised)s: %(error)s\n" + _TAG_TYPES),
    # How a worker that never managed a readable reply is recorded as ending.
    "stop_unreadable": (
        "stopped: the model's reply could not be read as JSON after "
        "%(attempts)d attempts",
        "stopped: the model's reply could not be read as tags after "
        "%(attempts)d attempts"),
    "stop_not_object": (
        "stopped: the model did not send a JSON object after %(attempts)d "
        "attempts",
        "stopped: the model did not send an action block after %(attempts)d "
        "attempts"),
    "batch_entry": (
        "every entry in 'actions' must be a JSON object",
        "every entry in a batch must be an action block"),
    # The schema-free fallback both the worker and agent_multi validate with.
    "missing_action": (
        "Missing 'action' key in JSON",
        "No action was named: every action is a block, /action_name/ ... "
        "//action_name/"),
}

# kind: the sentence under TAGS with the plain-text rules (rule J). Every kind
# has one; where the TAGS sentence names nothing the plain rules forbid it is
# that sentence, and the four that told the model to reply in blocks alone say
# where prose goes instead.
_PLAIN_CORRECTIONS = dict((kind, forms[1]) for kind, forms in _CORRECTIONS.items())
_PLAIN_CORRECTIONS.update({
    "unreadable": (
        "INVALID: that reply could not be read. The parser said: %(error)s\n"
        + TAG_CONTRACT_PLAIN + " Write the action you meant."),
    "worker_unreadable": (
        "INVALID: that reply could not be read. The parser said: %(error)s\n"
        + TAG_CONTRACT_PLAIN),
    "prose_before_work": (
        "That was plain text, and nothing has run yet, so it announced work "
        "rather than reporting it. Put the action you just described in the "
        "same reply, as a tag block: " + TAG_CONTRACT_PLAIN),
    "worker_prose": (
        "That was plain text, and nothing has run yet, so it described work "
        "rather than doing it. Put the action you just described in the same "
        "reply, as a tag block: " + TAG_CONTRACT_PLAIN),
})

# The reply arrived in the protocol that is not in force. Keyed by the one
# that IS in force, because that is the one the model is pointed back at.
_DRIFT = {
    JSON: ("The reply was tags but this session speaks JSON. It was accepted "
           "this time; from now on reply in JSON."),
    TAGS: ("The reply was JSON but this session speaks tags. It was accepted "
           "this time; from now on reply in tags."),
}

# The row the user is shown for the same thing. Said, never refused: the
# owner's rule is a strict prompt and a forgiving reader.
_DRIFT_NOTICE = {
    JSON: "The model replied in tags instead of JSON; accepted and told to use JSON.",
    TAGS: "The model replied in JSON instead of tags; accepted and told to use tags.",
}


def correction_kinds():
    """Every kind `correction` answers, for a caller (or a test) to walk."""
    return tuple(sorted(_CORRECTIONS))


def correction(kind, protocol, *, plain_text=False, **fields):
    """The sentence a loop hands back for `kind`, in `protocol`'s words.

    Under JSON it is exactly the string that loop used before tags existed.
    An unknown protocol answers as the default does (see `normal`); an
    unknown kind is a KeyError, because that is a typo in TMT, not a model's
    mistake, and it should fail where it was made. With `plain_text` under
    TAGS the plain-text form (rule J) is used: it names no JSON, no braces
    and none of /progress/, /send_message/ and /end_conversation/. Under
    JSON `plain_text` is ignored.
    """
    json_form, tags_form = _CORRECTIONS[kind]
    if normal(protocol) == TAGS:
        template = _PLAIN_CORRECTIONS[kind] if plain_text else tags_form
    else:
        template = json_form
    return template % fields


def drift(expected, got):
    """The line appended to a result when a reply was in the other protocol.

    "" when there was no drift: `got` is the protocol in force, or neither
    protocol at all (prose, which has its own path).
    """
    expected = normal(expected)
    if got not in PROTOCOLS or got == expected:
        return ""
    return _DRIFT[expected]


def drift_notice(expected, got):
    """The transcript row saying the same thing to the user, or ""."""
    expected = normal(expected)
    if got not in PROTOCOLS or got == expected:
        return ""
    return _DRIFT_NOTICE[expected]
