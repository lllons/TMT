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
            self.fail(_UNCLOSED % (_open(name, suffix), _close(name, suffix)))

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
        raise ProtocolError(_UNCLOSED % (_open(name, suffix), _close(name, suffix)))

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
            raise ProtocolError(_NEVER_CLOSED % (
                _open(name, suffix), _close(name, suffix), _close(name, suffix),
                _open(name, other), _close(name, other)))
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


def parse_tags(text):
    """The object a TAGS reply describes, or None when it holds no tags.

    Raises ProtocolError with a sentence the model can act on.
    """
    if not isinstance(text, str):
        raise ProtocolError("A reply is text, not %r." % (type(text).__name__,))
    return _Reader(text.replace("\r\n", "\n")).reply()


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


def _locate(text):
    """(protocol or None, where the reply starts)."""
    if not isinstance(text, str):
        return None, -1
    start = len(text) - len(text.lstrip())
    if start >= len(text):
        return None, -1
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


def detect(text):
    """What a reply looks like: JSON, TAGS, or None for neither."""
    return _locate(text)[0]


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


def parse(text):
    """The action object a reply describes, in whichever protocol it is in.

    None when the text is neither -- the caller's prose path. Raises
    ProtocolError with a sentence the model can act on.
    """
    kind, at = _locate(text)
    if kind is None:
        return None
    if kind == JSON:
        return _parse_json(text, at)
    return parse_tags(text[at:])


# --- writing ------------------------------------------------------------------

def render(obj, protocol=TAGS):
    """An action object written in a protocol. TAGS is the inverse of parse_tags.

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
    return _render_checked(obj, INLINE_WIDTH)


def example(obj, protocol=TAGS):
    """An action written on one line, for use inside a sentence.

    Block form only when a value cannot be written inline: it holds a newline,
    or it is file text (an EXACT_KEYS value) that starts with a space or tab.
    """
    if protocol == JSON:
        return render(obj, JSON)
    _known_protocol(protocol)
    return _render_checked(obj, None)


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


def transliterate(text):
    """A prompt constant with every JSON example rewritten as tags.

    A line that is an example -- optionally after a label such as "You emit:"
    or "BAD:" -- has each JSON action object in it replaced by its tags
    rendering. A line that is nothing but one object is written with `render`,
    so a long or multi-line one goes to block form on the lines after the
    label; an object sharing its line with commentary is written with
    `example`, in place. Every other line comes back byte for byte, and an
    object that cannot be read or written is left exactly as it was.
    """
    return "\n".join(_transliterate_line(line) for line in text.split("\n"))


def _transliterate_line(line):
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
            pieces.append(rest[pos:])
            break
        pieces.append(rest[pos:at])
        try:
            value = json.loads(rest[at:end])
            if not isinstance(value, dict):
                raise ValueError("not an object")
            pieces.append(value)
        except ValueError:
            pieces.append(rest[at:end])
        pos = end
    objects = [piece for piece in pieces if isinstance(piece, dict)]
    if not objects:
        return line
    if len(objects) == 1 and all(isinstance(piece, dict) or not piece.strip()
                                 for piece in pieces):
        try:
            written = render(objects[0], TAGS)
        except ProtocolError:
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
            written = example(piece, TAGS)
        except ProtocolError:
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

    def __init__(self):
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
            value = parse_tags(self.raw)
        except ProtocolError as error:
            self.error = str(error)
            return None
        self.error = None
        return value
