"""`web_search` and `web_fetch` as far as TMT is concerned: registered,
dispatched, drawn, and refused where they must be.

Everything here goes through `agent_actions.execute_action`, never through
`agent_web.search` or `agent_web.fetch` directly, for the reason
`test_agent_grep_glob_wiring.py` states and this change makes sharper still:
a tool that works perfectly and is not registered is a tool that does not
exist. One module was written from scratch and nine registries had to learn
its two names -- the schema, the mutating set it must stay OUT of, the
dispatcher, the labels, the event kinds, the delegation whitelist, the note
and review whitelists it must stay out of, the worker's forbidden set it must
stay out of, and the frozen module list. Any one of them left behind gives a
pair of tools that pass their own unit tests and are unreachable by a model
-- or, worse for the three "must be absent" ones, a pair of tools that quietly
hand the network to an agent nobody meant to give it to.

**No test in this file touches the network, and that is enforced rather than
intended.** `agent_web._open` is the one function in that module that makes a
socket, and `agent_web._resolve` is the one that asks DNS anything; both are
replaced for the whole of every test that could reach them and put back in a
`finally`. A test that reached a third party would be a test that fails when
somebody else's site is down, and a wiring test has no business having an
opinion about that.

The other half of this file is a REGRESSION. TMT gained a way to reach the
network, and the thing that must not have happened is the shell gaining one
too: `agent_policy` still denies `curl`, `wget` and `nc` exactly as it did
before, and `agent_web` still starts no process at all. The two boundaries are
independent and each is asserted here in its own words, because the change
that would weaken the second is the change that looks most reasonable while
somebody is adding the first.
"""

import os
import shutil
import tempfile
from pathlib import Path

import agent_actions
import agent_capabilities
import agent_config
import agent_delegation
import agent_prompt
import agent_subprompts
import agent_web
import agent_worker
import agent_policy as P
import agent_shell as S
import TMT
from agent_config import MUTATING_ACTIONS, REQUIRED_KEYS

# Derived from a module rather than from `__file__`: this file lives two
# directories down, and every path below has to name the repository itself.
REPO = Path(agent_config.__file__).resolve().parent

# Long enough that `agent_web._known_secrets` treats it as a credential, which
# is what a real key would be -- so the leak check runs over the real code path
# rather than over a value it discards for being short.
FAKE_KEY = "tvly-test-0123456789abcdef"

# Every environment variable that can change which backend a search would use.
# All four are cleared or set deliberately, because a developer with a real
# TAVILY_API_KEY exported would otherwise get a different test from a developer
# without one -- and the one without one is the machine that finds the bug.
SEARCH_ENV = ("TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY", "SERPER_API_KEY",
              "TMT_SEARCH_BACKEND")

TAVILY_PAYLOAD = (
    b'{"results": ['
    b'{"title": "ImportError on frozen py-modules",'
    b' "url": "https://example.invalid/a",'
    b' "content": "An editable install freezes its module list."},'
    b'{"title": "Second answer", "url": "https://example.invalid/b",'
    b' "content": "Another snippet."}'
    b']}'
)

PAGE = (b"<html><head><title>A Documentation Page</title>"
        b"<script>var secret = 1;</script></head>"
        b"<body><p>The readable paragraph.</p></body></html>")


class Web(object):
    """The network taken away, and every credential source with it.

    Four things have to be redirected and all four have to be put back.
    `_open` and `_resolve` are the only two functions in `agent_web` that can
    reach anything outside this process; `SEARCH_FILE` is bound at import time
    from `INSTALL_DIR`, so redirecting `INSTALL_DIR` afterwards would not move
    it and a test would read the developer's own key file; and the environment
    is what decides which backend is chosen at all.
    """

    def __init__(self, key=FAKE_KEY, responses=None, resolves="93.184.216.34"):
        self.calls = []
        self.responses = list(responses or [])
        self.resolves = resolves
        self.previous = {name: os.environ.get(name) for name in SEARCH_ENV}
        self.previous_open = agent_web._open
        self.previous_resolve = agent_web._resolve
        self.previous_file = agent_web.SEARCH_FILE
        self.store = Path(tempfile.mkdtemp(prefix="tmt_web_")).resolve()
        for name in SEARCH_ENV:
            os.environ.pop(name, None)
        if key:
            os.environ["TAVILY_API_KEY"] = key
        agent_web.SEARCH_FILE = self.store / ".tmt_search.json"
        agent_web._open = self._open
        agent_web._resolve = self._resolve

    def _open(self, url, data=None, headers=None, timeout=None, limit=None):
        self.calls.append({"url": url, "data": data, "headers": headers or {},
                           "timeout": timeout})
        if not self.responses:
            raise AssertionError("nothing scripted for %s" % url)
        status, body, response_headers = self.responses.pop(0)
        return status, url, dict(response_headers), body

    def _resolve(self, host, port):
        if isinstance(self.resolves, str):
            return [self.resolves]
        return list(self.resolves)

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        agent_web._open = self.previous_open
        agent_web._resolve = self.previous_resolve
        agent_web.SEARCH_FILE = self.previous_file
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(str(self.store), ignore_errors=True)
        return False


class PolicyWorkspace(object):
    """A throwaway workspace and a throwaway saved-rules file, both put back.

    The same two redirections `test_agent_policy.py` makes, and for its
    reasons: a leaked `ROOT_DIR` points every later test in the suite at a
    deleted directory, and a leaked rules path writes into the developer's own
    installation -- TMT's real saved command rules, from a test run. Neither
    failure shows up here; both show up later somewhere unrelated.
    """

    def __init__(self):
        self.previous_root = agent_config.ROOT_DIR
        self.previous_rules_path = P.rules_path
        self.path = Path(tempfile.mkdtemp(prefix="tmt_webpolicy_")).resolve()
        self.store = Path(tempfile.mkdtemp(prefix="tmt_webrules_")).resolve()
        (self.path / "a.py").write_text("x\n", encoding="utf-8")
        agent_config.set_workspace_root(self.path)
        P.rules_path = lambda: self.store / P.RULES_FILE_NAME

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        agent_config.ROOT_DIR = self.previous_root
        P.rules_path = self.previous_rules_path
        shutil.rmtree(str(self.path), ignore_errors=True)
        shutil.rmtree(str(self.store), ignore_errors=True)
        return False


class State(object):
    """One of the two states `note_work` tells, remembering what it was told."""

    def __init__(self):
        self.changes = []
        self.runs = []

    def note_change(self, action, paths=None):
        self.changes.append((action, paths))

    def note_run(self, action, detail):
        self.runs.append((action, detail))


class Recorder(object):
    """A session whose review and verification states only remember.

    The two are SEPARATE objects rather than one worn twice, because
    `note_work` tells them separately on purpose -- a review passes over a diff
    and a verification passes over a tree, and they go stale independently. A
    single recorder would show every write twice and hide which of the two
    branches had actually run.

    `TMT.note_work` swallows every exception it meets, deliberately -- a state
    that raises must not be able to end a turn that has already done its work
    -- so an assertion made INSIDE one of these methods would be swallowed with
    it and the test would pass having proved nothing. Everything is recorded
    and asserted afterwards, outside the guard.
    """

    def __init__(self):
        self.review = State()
        self.verify = State()


_DEFAULT_CONTEXT = object()


def run(action, **keys):
    """One action, exactly as the loop would run it.

    The action context is passed as `_context` rather than as `context`, for
    the reason `test_agent_grep_glob_wiring.py` found the hard way with
    `grep`: an action key sharing a name with the conventional action-context
    argument is silently eaten by the helper. Neither web verb has a `context`
    key today, and the helper is written this way so that adding one later
    cannot quietly break every test in this file.

    The default carries no `capabilities` key, which authorises nothing -- so
    every dispatch below is also evidence that these two verbs need no
    authorisation.
    """
    context = keys.pop("_context", _DEFAULT_CONTEXT)
    if context is _DEFAULT_CONTEXT:
        context = {"push_authorized": False}
    keys["action"] = action
    return str(agent_actions.execute_action(keys, context))


def unwired(result):
    """The two ways a registration failure reads, both of them plausible.

    `_run_tool` answers a module it cannot import with a sentence, and
    `validate_action` answers an unregistered verb with another one, so a tool
    that was never wired up fails quietly and looks like an ordinary tool
    saying no. Every dispatch below is checked against both.
    """
    return ("Unknown action" in result or "is unavailable" in result
            or "Unknown action" in result)


# --- the schema the model is validated against ------------------------------

def test_both_network_verbs_are_registered_with_the_keys_they_require():
    """REQUIRED_KEYS is the whole of what `validate_action` knows. A verb
    missing from it is not a tool with a broken schema, it is a verb that comes
    back "Unknown action" however well its module works -- and the model is
    then told, in the same sentence, the complete list of verbs that DO exist,
    which is a tool list it reads and reaches for."""
    assert REQUIRED_KEYS.get("web_search") == ["query"], REQUIRED_KEYS.get("web_search")
    assert REQUIRED_KEYS.get("web_fetch") == ["url"], REQUIRED_KEYS.get("web_fetch")


def test_validate_action_accepts_the_shapes_a_model_will_write():
    """Success is reported as None, not as an empty string -- a test asserting
    == "" fails on a perfectly valid action, which is a trap this repository
    has already walked into once. Every optional key is exercised too, because
    `validate_action` allows extras on purpose and a schema that had listed
    `max_results` as required would refuse the commonest shape there is."""
    good = [
        {"action": "web_search", "query": "python asyncio TimeoutError"},
        {"action": "web_search", "query": "rustc E0499", "max_results": 3},
        {"action": "web_search", "query": "npm ERR ERESOLVE", "recency": "week"},
        {"action": "web_fetch", "url": "https://docs.python.org/3/library/ast.html"},
        {"action": "web_fetch", "url": "https://example.com/x", "timeout": 10},
    ]
    for obj in good:
        assert agent_prompt.validate_action(obj) is None, obj


def test_validate_action_names_the_one_key_each_verb_cannot_work_without():
    """One operation each, and it is meaningless without its subject -- unlike
    `bash` and `plan`, which take no required key because they have several.
    The missing key is named back so the model corrects it in one round rather
    than guessing at the schema."""
    missing_query = agent_prompt.validate_action({"action": "web_search"})
    assert missing_query and "query" in missing_query, missing_query
    missing_url = agent_prompt.validate_action({"action": "web_fetch"})
    assert missing_url and "url" in missing_url, missing_url
    # A recency or a max_results on its own is still a search with no query.
    partial = agent_prompt.validate_action(
        {"action": "web_search", "recency": "day", "max_results": 5})
    assert partial and "query" in partial, partial


def test_the_web_module_is_in_the_frozen_module_list():
    """An editable install writes `py-modules` at install time, so a module
    that is not in it is invisible to `tmtcode` however well it works in a test
    that puts the repository on sys.path. `_run_tool` would answer "agent_web
    is unavailable" and the model would work around a tool sitting on disk --
    which is the failure this module is MOST exposed to, being the newest one
    in TMT."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"agent_web"' in text, "agent_web is not in pyproject.toml"
    assert (REPO / "agent_web.py").is_file()


def test_the_search_credential_file_is_ignored_by_git():
    """`.tmt_search.json` holds a third party's API key, so it is ignored for
    `.tmt_providers.json`'s reason rather than `.tmt_context`'s: staying out of
    commits is half of what protects it. TMT's install and its workspace are
    the same directory whenever TMT is run on TMT, so without the entry the
    first search anybody configures here writes a live key into this
    repository's own git status."""
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert ".tmt_search.json" in ignored, ".tmt_search.json is not git-ignored"
    # The name the module actually uses, so the ignore entry cannot drift away
    # from the file it is protecting.
    assert Path(agent_web.SEARCH_FILE).name == ".tmt_search.json"


# --- dispatch, with the network taken away ----------------------------------

def test_web_search_reaches_its_module_through_the_dispatcher():
    """The registration test. Driven through `execute_action` because that is
    the only path a model can take: a `search` that works and a branch that
    does not exist are indistinguishable from anywhere except here."""
    with Web(responses=[(200, TAVILY_PAYLOAD, {})]) as web:
        result = run("web_search", query="frozen py-modules ImportError")
        assert not unwired(result), result
        assert "Web search failed" not in result, result
        assert result.startswith("2 results for"), result
        assert "ImportError on frozen py-modules" in result, result
        assert "https://example.invalid/a" in result, result
        assert "An editable install freezes its module list." in result, result
        # One request, to the backend the key selected, and nowhere else.
        assert len(web.calls) == 1, web.calls
        assert web.calls[0]["url"] == agent_web.BACKEND_URLS["tavily"], web.calls


def test_the_dispatcher_carries_max_results_and_recency_to_the_module():
    """Two optional keys that are easy to register and forget to forward. A
    branch that read `obj["query"]` and dropped the rest would pass every test
    above while silently answering a wider question than the one asked."""
    with Web(responses=[(200, TAVILY_PAYLOAD, {})]) as web:
        result = run("web_search", query="rustc E0499", max_results=1,
                     recency="week")
        assert not unwired(result), result
        sent = web.calls[0]["data"].decode("utf-8")
        assert '"max_results": 1' in sent, sent
        # Only one row comes back, so the count really was applied rather than
        # merely transmitted.
        assert result.startswith("1 result for"), result
        # Tavily cannot filter a web search by recency, and the result says so
        # rather than implying a filter that was never applied.
        assert "not applied" in result, result


def test_web_fetch_reaches_its_module_and_comes_back_as_readable_text():
    """The second branch, and the one with more to get wrong: a page arrives as
    markup and what the model must be handed is prose. The script tag is the
    assertion that matters -- a page's JavaScript is not something a coding
    agent should be reading as though it were the page."""
    with Web(responses=[(200, PAGE, {"Content-Type": "text/html; charset=utf-8"})]):
        result = run("web_fetch", url="https://docs.example.com/guide")
        assert not unwired(result), result
        assert "Web fetch failed" not in result, result
        assert "A Documentation Page" in result, result
        assert "The readable paragraph." in result, result
        assert "var secret" not in result, result
        assert "<p>" not in result and "<html>" not in result, result


def test_the_dispatcher_carries_the_fetch_timeout_to_the_module():
    """The one optional key on the other verb, forwarded and clamped. A model
    that asks for an hour gets the module's ceiling rather than an hour, and a
    branch that dropped the key would give every fetch the default while the
    model believed it had chosen."""
    with Web(responses=[(200, PAGE, {"Content-Type": "text/html"})]) as web:
        run("web_fetch", url="https://docs.example.com/guide", timeout=5)
        assert web.calls[0]["timeout"] == 5, web.calls
    with Web(responses=[(200, PAGE, {"Content-Type": "text/html"})]) as web:
        run("web_fetch", url="https://docs.example.com/guide", timeout=9999)
        assert web.calls[0]["timeout"] == agent_web.MAX_FETCH_TIMEOUT, web.calls


def test_an_unconfigured_install_is_told_so_rather_than_shown_an_empty_result():
    """The single most misleading answer this tool could give. No key means
    nothing was searched, and "0 results" would be read as "the web does not
    know", which is a different and much worse fact. It has to arrive as an
    ordinary action result too -- a raised exception here ends the session over
    a missing setting."""
    with Web(key=None) as web:
        result = run("web_search", query="anything at all")
        assert not unwired(result), result
        assert "Web search failed" in result, result
        assert "not configured" in result, result
        assert "this is not an empty result set" in result, result
        # Every environment variable a user could set is named, so the refusal
        # is actionable rather than merely correct.
        for name in agent_web.KEY_ENV.values():
            assert name in result, (name, result)
        assert not web.calls, "an unconfigured search still made a request"


def test_a_backend_that_rejects_the_key_comes_back_as_a_sentence():
    """`_run_tool` turns a ValueError into words and `agent_web` catches its own
    WebError before that -- so an HTTP failure has to reach the model as a
    readable result rather than as a traceback out of the dispatcher. A 401 is
    the key being wrong, which is a thing the USER has to fix, so the sentence
    names where the key lives."""
    with Web(responses=[(401, b'{"error":"unauthorized"}', {})]):
        result = run("web_search", query="anything")
        assert not unwired(result), result
        assert "Traceback" not in result, result
        assert "Web search failed" in result, result
        assert "401" in result, result
        assert "key" in result.lower(), result


def test_web_fetch_refuses_a_url_it_may_not_open_and_says_which_rule_it_broke():
    """Four refusals, each a fact about the URL rather than a judgement about
    the site, and each arriving as a result string. The private-address cases
    use a literal address and a patched resolver, so this test states what the
    guard does without asking DNS anything."""
    cases = [
        ("http://example.com/x", "https"),
        ("file:///etc/passwd", "https"),
        ("https://127.0.0.1/x", "loopback"),
        ("https://user:pw@example.com/x", "credentials"),
    ]
    for url, expected in cases:
        with Web(responses=[(200, PAGE, {"Content-Type": "text/html"})]) as web:
            result = run("web_fetch", url=url)
            assert not unwired(result), (url, result)
            assert "Traceback" not in result, (url, result)
            assert "Web fetch failed" in result, (url, result)
            assert expected in result, (url, expected, result)
            assert not web.calls, (url, "a refused url was still opened")
    # A NAME that resolves inside the network is refused on the same rule, so
    # the guard is not merely a check on the spelling of the host.
    with Web(responses=[(200, PAGE, {})], resolves="10.0.0.5") as web:
        result = run("web_fetch", url="https://internal.example.com/x")
        assert "Web fetch failed" in result, result
        assert "10.0.0.5" in result, result
        assert not web.calls, "a private host was still opened"


def test_neither_verb_needs_a_capability_to_run():
    """`/plan`, `/review` and `/verify` are authorised per prompt by the user's
    own typed line. These two are ordinary read verbs and must never join that
    set: a context carrying no `capabilities` key authorises nothing, and both
    still run. Asserted against the registry and through three shapes of
    context, because the registry being right and the guard consulting
    something else would look identical from outside."""
    assert "web_search" not in agent_capabilities.CAPABILITIES
    assert "web_fetch" not in agent_capabilities.CAPABILITIES
    for context in ({}, None, {"push_authorized": False}):
        with Web(responses=[(200, TAVILY_PAYLOAD, {})]):
            searched = run("web_search", _context=context, query="x")
        with Web(responses=[(200, PAGE, {"Content-Type": "text/html"})]):
            fetched = run("web_fetch", _context=context,
                          url="https://docs.example.com/g")
        for result in (searched, fetched):
            assert "not authorised" not in result.lower(), result
            assert agent_delegation.VIOLATION_HEADER not in result, result
        assert searched.startswith("2 results for"), searched
        assert "The readable paragraph." in fetched, fetched


# --- the registries the interface reads -------------------------------------

def test_the_labels_the_interface_draws_name_both_verbs():
    """Derived labels, so `Web Search` and `Web Fetch` rather than the raw
    verb. A registered action with no entry shows the reader `web_search` in a
    column where every neighbouring row is a phrase."""
    labels = agent_actions.ACTION_LABELS
    assert labels.get("web_search") == "Web Search", labels.get("web_search")
    assert labels.get("web_fetch") == "Web Fetch", labels.get("web_fetch")


def test_both_verbs_are_drawn_as_tools_and_not_as_reads_of_the_workspace():
    """`file_read` is the kind every workspace-reading verb takes, and every
    row of that kind names a local path. A web result has no path, so a
    `file_read` here would be the only row of its kind in the transcript with
    nothing local behind it. The map is asserted directly AND through the event
    it produces, because the map being right and the lookup missing it would
    look identical from outside."""
    kinds = agent_actions._EVENT_KIND_FOR_ACTION
    assert kinds.get("web_search") == "tool", kinds.get("web_search")
    assert kinds.get("web_fetch") == "tool", kinds.get("web_fetch")
    for action, keys, result in (
            ("web_search", {"query": "asyncio TimeoutError"}, "2 results for 'x'"),
            ("web_fetch", {"url": "https://docs.example.com/g"}, "Title\n\nBody")):
        obj = dict(keys, action=action)
        event = agent_actions.action_event(action, obj, result)
        assert event is not None, action
        assert event.kind == "tool", (action, event.kind)


def test_a_web_row_describes_the_request_and_never_quotes_the_answer():
    """Neither verb is in `_REPORTED_ACTIONS`, and that is the right side of
    that decision: a search result and a fetched page are DATA -- somebody
    else's prose -- and quoting the first line of it would put a stranger's
    sentence in the transcript as though it were TMT's report of what it did.
    A page beginning "Error 404" would be drawn as the action having failed.

    So the row describes the request instead. `web_search` has `query`, which
    is already one of the keys `_describe` reads.

    `web_fetch` names its subject in `url`, and MEASURED 2026-09-03 that is not
    one of the keys either `_describe` or `agent_worker._activity_label` looks
    at -- so a fetch row is the bare words "Web Fetch", the fortieth one is the
    same row as the first, and a reader scrolling back cannot tell which page
    was read. It is the `glob`/`pattern` gap this repository has already met
    once, arriving through a different key. Nothing here asserts the gap in
    either direction: what is pinned is the property that must hold whichever
    way it is settled -- the row starts with the label and never carries the
    page's own words."""
    searched = agent_actions.action_event(
        "web_search", {"action": "web_search", "query": "rustc E0499"},
        "2 results for 'rustc E0499':\n\n1. Borrow checker\n   https://x/y")
    assert searched.message.startswith("Web Search"), searched.message
    assert "rustc E0499" in searched.message, searched.message
    assert "Borrow checker" not in searched.message, searched.message
    assert "https://x/y" not in searched.message, searched.message

    fetched = agent_actions.action_event(
        "web_fetch", {"action": "web_fetch", "url": "https://docs.example.com/g"},
        "https://docs.example.com/g\nA Documentation Page\n\nError 404 in the text")
    assert fetched is not None
    assert fetched.message.startswith("Web Fetch"), fetched.message
    # The page's own words stay out of the row whatever they say, which is the
    # property that keeps a transcript honest about what TMT did.
    assert "Error 404" not in fetched.message, fetched.message
    assert "A Documentation Page" not in fetched.message, fetched.message


def test_a_web_search_result_reaches_the_model_as_the_string_it_read():
    """`build_result_message` is the last thing between an action's result and
    the model, and it rewrites some of them. Neither web verb is one of the
    actions it rewrites, so what the model reads is what the module wrote --
    which matters because the module's failure sentences are written to be
    acted on, and a wrapper that prefixed them would be answering for it."""
    with Web(responses=[(200, TAVILY_PAYLOAD, {})]):
        result = run("web_search", query="frozen py-modules",
                     progress="Looking up the error.")
    message = agent_actions.build_result_message(
        "web_search", result, {"action": "web_search", "query": "x",
                               "progress": "Looking up the error."})
    assert result in message, message
    assert "FAILED" not in message, message
    # And the nudge that rides on a result rides on this one too, so a search
    # with nothing said about it is corrected like every other silent action.
    silent = agent_actions.build_result_message(
        "web_search", result, {"action": "web_search", "query": "x"})
    assert "No \"progress\"" in silent, silent


# --- the sets these two verbs must stay OUT of ------------------------------

def test_neither_verb_is_a_mutating_action():
    """`MUTATING_ACTIONS` is read by NAME, and `TMT.note_work` reads it to make
    a passed review and a passed verification STALE. A search that joined it
    would re-gate the final answer every time the model looked something up --
    the answer would be held while a reviewer re-read a diff that had not moved
    and a test suite ran again over a tree nothing had touched. It is also what
    throws away the cached system prompt, so the workspace would be re-walked
    per search."""
    assert "web_search" not in MUTATING_ACTIONS, MUTATING_ACTIONS
    assert "web_fetch" not in MUTATING_ACTIONS, MUTATING_ACTIONS


def test_a_web_search_does_not_make_a_passed_review_or_verification_stale():
    """The set asserted through the function that reads it, because the set
    being right and `note_work` consulting something else would look identical
    from outside. `write_file` is driven in the same test as the control: a
    run in which NOTHING is recorded proves nothing about the recorder."""
    session = Recorder()
    TMT.note_work(session, "web_search", {"action": "web_search", "query": "x"})
    TMT.note_work(session, "web_fetch",
                  {"action": "web_fetch", "url": "https://example.com/x"})
    assert session.review.changes == [], session.review.changes
    assert session.verify.changes == [], session.verify.changes
    # Nor is either of them recorded as what verification RAN. Only `bash` is,
    # and a search quoted to a reviewer under "WHAT VERIFICATION ACTUALLY RAN"
    # would be fabricating a run in the one readout whose whole job is to say
    # what really happened.
    assert session.review.runs == [], session.review.runs
    # The control: a run in which nothing is recorded proves nothing about the
    # recorder, so the verb that DOES make both states stale is driven too.
    TMT.note_work(session, "write_file",
                  {"action": "write_file", "path": "a.py", "content": "x"})
    assert [action for action, _p in session.review.changes] == ["write_file"], \
        session.review.changes
    assert [action for action, _p in session.verify.changes] == ["write_file"], \
        session.verify.changes


def test_the_note_agent_and_the_reviewer_cannot_reach_the_network():
    """Two whitelists, and a whitelist rather than a blacklist for the reason
    `NOTE_ACTIONS` gives: a blacklist silently admits every verb added after it
    was written, and the person adding the verb is not the person who wrote the
    list. The note agent answers a question about the WORKSPACE, and the
    reviewer audits a diff that is already on disk -- neither has any business
    making an outbound request, and the reviewer least of all: a review is
    supposed to be a judgement about this code rather than about something it
    found on the internet halfway through.

    Both copies of each list are asserted, because the prompt offering a verb
    the loop refuses costs the agent a step being told a name is wrong."""
    for names in (agent_worker.NOTE_ACTIONS, agent_worker.REVIEW_ACTIONS,
                  agent_subprompts.NOTE_VERBS, agent_subprompts.REVIEW_VERBS):
        assert "web_search" not in names, names
        assert "web_fetch" not in names, names
    # And the two copies still agree with each other, which is the test that
    # catches a verb added to one list and not the other.
    assert set(agent_worker.NOTE_ACTIONS) == set(agent_subprompts.NOTE_VERBS)
    assert set(agent_worker.REVIEW_ACTIONS) == set(agent_subprompts.REVIEW_VERBS)


def test_an_ordinary_delegated_worker_is_not_forbidden_either_verb():
    """`WORKER_FORBIDDEN` is the other direction, and the answer here is the
    opposite one: an ordinary worker delegated a research subtask is exactly
    the agent that should be able to look something up. Neither verb needs a
    terminal -- which is why `bash` is refused and these are not -- so there is
    no approval question waiting on a thread nobody is watching."""
    for name in ("web_search", "web_fetch"):
        assert name not in agent_worker.WORKER_FORBIDDEN, agent_worker.WORKER_FORBIDDEN
        assert name not in agent_worker.WORKER_NEEDS_TERMINAL, name


# --- the read-only delegation contract --------------------------------------

def test_a_read_only_delegation_may_search_and_fetch_and_may_not_write():
    """`agent_delegation.READ_ONLY_ACTIONS` is a SECURITY whitelist, not a list
    of hints: a verb absent from it is refused to a read-only worker. Read-only
    here means "changes nothing", and reaching the network is not the property
    that set is about -- these two touch nothing in the workspace and nothing
    on this machine, so a read-only delegation may use them exactly as it may
    use `grep`. `bash` is absent from the same set because a command CAN write,
    which is the distinction being pinned."""
    contract = agent_delegation.DelegationConstraints(read_only=True)
    assert agent_delegation.refusal(contract, "web_search") == ""
    assert agent_delegation.refusal(contract, "web_fetch") == ""
    assert agent_delegation.refusal(contract, "write_file") != ""
    assert agent_delegation.refusal(contract, "bash") != ""
    for name in ("web_search", "web_fetch"):
        assert name in agent_delegation.READ_ONLY_ACTIONS, name


def test_a_read_only_worker_reaches_both_verbs_through_the_dispatcher():
    """The second of the two layers. `agent_worker` refuses a mutating verb
    before dispatch; this is the dispatcher's own copy of the same rule, asked
    with the same function, and it must let these two through. A read-only
    worker whose research verbs were refused could not do the one thing a
    read-only worker is for."""
    context = {"push_authorized": False, "read_only": True}
    with Web(responses=[(200, TAVILY_PAYLOAD, {})]):
        searched = run("web_search", _context=context, query="asyncio")
    assert agent_delegation.VIOLATION_HEADER not in searched, searched
    assert searched.startswith("2 results for"), searched
    with Web(responses=[(200, PAGE, {"Content-Type": "text/html"})]):
        fetched = run("web_fetch", _context=context,
                      url="https://docs.example.com/g")
    assert agent_delegation.VIOLATION_HEADER not in fetched, fetched
    assert "The readable paragraph." in fetched, fetched
    blocked = run("bash", _context=context, command="ls")
    assert agent_delegation.VIOLATION_HEADER in blocked, blocked


# --- the regression: the shell did not gain a network with the model --------

def test_the_bash_network_policy_still_refuses_every_network_program():
    """THE REGRESSION THAT MATTERS. TMT gained a way to reach the network, and
    the change that must NOT have ridden along is the shell gaining one --
    "the model can search now, so surely `curl` is fine" is the reasonable-
    sounding edit that would quietly hand a model an unbounded HTTP client
    with a method, a body, headers and an output file.

    The two boundaries are independent. `agent_policy` governs processes TMT
    STARTS; `agent_web` is TMT making one fixed-shape request itself. Nothing
    in the second makes the first any more allowed than it was, and this is
    the test that says so. Every line goes through the real parser, because a
    hand-assembled argv is a command shape nothing produces."""
    with PolicyWorkspace():
        for line in ("curl https://example.com/x", "wget https://example.com/x",
                     "nc example.com 80", "ncat example.com 80",
                     "netcat example.com 80", "ftp example.com",
                     "tftp example.com", "rsync a b"):
            for mode in (P.OFFLINE, P.DEPS):
                decision = P.decide(S.parse(line), network=mode)
                assert decision.verdict == P.DENY, (line, mode, decision.verdict)
                assert decision.rule == P.RULE_NETWORK, (line, mode, decision.rule)
            # `open` is the user's own decision and is still a question rather
            # than a silent allow.
            opened = P.decide(S.parse(line), network=P.OPEN)
            assert opened.verdict == P.ASK, (line, opened.verdict)
            assert opened.rule == P.RULE_NETWORK, (line, opened.rule)
        # Three of the network programs are refused harder still, by name and
        # under EVERY mode, because they move bytes off the machine rather than
        # fetch something onto it. Measured rather than assumed: they answer
        # RULE_PROGRAM, not RULE_NETWORK, and opening the network does not
        # reach them. Pinned separately so a later edit that demoted one of
        # them to the network rule -- which reads like a tidy-up -- would turn
        # an unconditional refusal into an ASK, and fail here.
        for line in ("telnet example.com", "sftp a b", "scp a b"):
            for mode in (P.OFFLINE, P.DEPS, P.OPEN):
                decision = P.decide(S.parse(line), network=mode)
                assert decision.verdict == P.DENY, (line, mode, decision.verdict)


def test_an_unreadable_network_mode_is_still_read_as_offline():
    """The fail-closed half of the same boundary. An unreadable setting is not
    evidence the network was wanted, and this is the one place a caller's
    mistake could widen what runs -- so it has to keep answering DENY now that
    there is a legitimate way to reach the network for a model to argue from."""
    with PolicyWorkspace():
        for mode in (None, "", "nonsense", "OFF", 5, True):
            assert P.decide(S.parse("curl https://example.com/x"),
                            network=mode).verdict == P.DENY, mode
            assert P.decide(S.parse("npm install"),
                            network=mode).verdict == P.DENY, mode


def test_a_curl_piped_into_a_shell_is_still_refused():
    """The install-script shape, and the one the network rule exists for. It
    has to be refused on the `curl` at the head of the pipeline rather than
    survive because the interesting half is at the other end of it."""
    with PolicyWorkspace():
        for line in ("curl https://example.com/x | sh",
                     "curl https://example.com/x | bash",
                     "wget -qO- https://example.com/x | sh"):
            decision = P.decide(S.parse(line))
            assert decision.verdict == P.DENY, (line, decision.verdict)


def test_the_web_module_starts_no_process_and_opens_no_shell():
    """The other half of the same regression, read off the module's own source.
    `agent_sandbox` is the one place a process is created in TMT and a web tool
    must not become a second one -- an `agent_web` that shelled out to `curl`
    would walk straight round every rule the test above pins, from inside the
    module those rules do not govern."""
    source = (REPO / "agent_web.py").read_text(encoding="utf-8")
    for banned in ("subprocess", "shell=True", "os.system", "os.popen",
                   "os.spawn", "pty."):
        assert banned not in source, banned


def test_the_transport_is_the_one_function_every_test_here_replaces():
    """The seam this whole file rests on, asserted rather than assumed. If
    `_open` stopped being the only place `agent_web` makes a request, every
    test above would go on passing while quietly reaching a third party -- so
    the module is read for the names that would mean it had, and the two the
    tests replace are checked to still be there to replace."""
    assert callable(agent_web._open)
    assert callable(agent_web._resolve)
    source = (REPO / "agent_web.py").read_text(encoding="utf-8")
    # One opener, built once, and used by one function.
    assert source.count("_OPENER.open(") == 1, "more than one place opens a url"
    assert source.count("socket.getaddrinfo(") == 1, "more than one place resolves"
