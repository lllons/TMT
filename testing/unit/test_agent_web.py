"""Tests for `agent_web`, the two network tools.

This is the first module in TMT that makes a request of its own, and it is the
one module where a mistake reaches somebody else's machine. So the properties
pinned here are mostly refusals, and each of them is a refusal the module would
still *look* correct without:

* **Nothing here touches the network.** `_open` is the single seam every path
  goes through, and it is replaced in every test that would otherwise open a
  socket. `_resolve` is replaced too, in the harness rather than per test, so a
  test that forgets is still off the network rather than quietly doing a DNS
  lookup for `example.com`. The one test that drives `_resolve` itself hands
  `agent_web` a stub `socket` module and puts the real one back.
* **"Not configured" must never read like "found nothing".** They are the two
  answers a model would act on in opposite directions, and the difference is
  carried entirely by prose, which is exactly the kind of thing that erodes.
* **The SSRF check runs on the URL that is opened, not on the one that was
  asked for.** A redirect is the whole reason `agent_web` refuses redirects at
  the opener and follows them by hand, so the redirect test asserts the second
  request was never made rather than merely that the answer was a refusal.
* **A query is checked for this machine's own credentials before it is sent.**
  That is the one guard in the module protecting the user from the model, and
  the test drives it with a fake secret and then asserts `_open` was never
  reached -- "it was refused" and "it was not sent" are two claims and only the
  second one matters.

Everything that reads a credential is driven against a temporary
`SEARCH_FILE` with the three environment variables cleared, so the result does
not depend on whether the developer running the suite happens to have a Tavily
key. `_known_secrets` is replaced for the same reason: the real one reads the
provider store, and a test that passes only on a configured machine is a test
that fails on a fresh clone.
"""

import inspect
import json
import os
import shutil
import socket
import tempfile
import types
import urllib.parse
from pathlib import Path

import agent_web


# The environment this module reads. Cleared by the harness and put back by it,
# because the suite has no isolation and a leaked key here would change the
# answer of every later test that asks what is configured.
ENV_NAMES = ("TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY", "SERPER_API_KEY",
             "TMT_SEARCH_BACKEND")

# A key long enough to be a credential (>= 16 characters, which is the floor
# `_known_secrets` uses) and obviously not one of anybody's real ones.
FAKE_SECRET = "sk-fake-0123456789abcdef-not-a-real-key"

PUBLIC = "93.184.216.34"


class Recorder:
    """`agent_web._open`, replaced. Scripts the answers and keeps the asks.

    Every reply is `(status, final_url, headers, body bytes)` -- the exact
    shape the real `_open` returns -- or an exception to raise. Running out of
    replies is an AssertionError rather than an IndexError, because "the code
    made a request the test did not expect" is the finding, and a test that
    dies with a bare IndexError buries it.
    """

    def __init__(self, replies=()):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, data=None, headers=None, timeout=None, limit=None):
        self.calls.append({"url": url, "data": data,
                           "headers": dict(headers or {}),
                           "timeout": timeout, "limit": limit})
        if not self.replies:
            raise AssertionError(
                "_open was called more times than the test scripted; the "
                "unexpected request was for %s" % url)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def count(self):
        return len(self.calls)

    def body(self, index=0):
        """The JSON a POSTing backend was sent."""
        return json.loads(self.calls[index]["data"].decode("utf-8"))

    def query(self, index=0):
        """The query string a GETing backend was sent, as a dict."""
        return dict(urllib.parse.parse_qsl(
            urllib.parse.urlsplit(self.calls[index]["url"]).query))


class Web:
    """`agent_web` with no environment, no settings file, no DNS, no socket.

    close() restores all four and must run in a finally block -- a test that
    leaves `_open` replaced would make every later module in the run answer
    from a dead recorder, and a test that leaves an API key in the environment
    would make "is anything configured" true for the rest of the suite.
    """

    def __init__(self, keys=None, backend="", replies=(), secrets=(),
                 addresses=None):
        self.saved_env = {name: os.environ.get(name) for name in ENV_NAMES}
        for name in ENV_NAMES:
            os.environ.pop(name, None)
        self.dir = Path(tempfile.mkdtemp(prefix="tmt_web_"))
        self.saved_file = agent_web.SEARCH_FILE
        agent_web.SEARCH_FILE = self.dir / ".tmt_search.json"
        if keys or backend:
            self.write({"backend": backend, "keys": dict(keys or {})})
        self.open = Recorder(replies)
        self.saved_open = agent_web._open
        agent_web._open = self.open
        self.saved_secrets = agent_web._known_secrets
        agent_web._known_secrets = lambda: list(secrets)
        # Every name resolves to a public address unless the test says
        # otherwise, so no path here can reach a real resolver.
        self.addresses = dict(addresses or {})
        self.saved_resolve = agent_web._resolve
        agent_web._resolve = self.resolve

    def resolve(self, host, port):
        answer = self.addresses.get(host, [PUBLIC])
        if isinstance(answer, Exception):
            raise answer
        return list(answer)

    def write(self, data):
        agent_web.SEARCH_FILE.write_text(json.dumps(data), encoding="utf-8")

    def write_raw(self, text):
        agent_web.SEARCH_FILE.write_text(text, encoding="utf-8")

    def env(self, name, value):
        os.environ[name] = value

    def close(self):
        agent_web._open = self.saved_open
        agent_web._known_secrets = self.saved_secrets
        agent_web._resolve = self.saved_resolve
        agent_web.SEARCH_FILE = self.saved_file
        for name, value in self.saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(self.dir, ignore_errors=True)


def ok(payload, status=200):
    """A successful JSON reply from a search backend."""
    return (status, "https://backend.example/search",
            {"Content-Type": "application/json"},
            json.dumps(payload).encode("utf-8"))


def page(body, content_type="text/html; charset=utf-8", status=200,
         url="https://docs.example/guide", headers=None):
    """A reply from a fetched page."""
    sent = {"Content-Type": content_type}
    sent.update(headers or {})
    if isinstance(body, str):
        body = body.encode("utf-8")
    return (status, url, sent, body)


def redirect(location, url="https://docs.example/guide", status=302):
    return (status, url, {"Location": location}, b"")


def tavily(*rows):
    return {"results": [{"title": t, "url": u, "content": c} for t, u, c in rows]}


def brave(*rows):
    return {"web": {"results": [{"title": t, "url": u, "description": c}
                                for t, u, c in rows]}}


def serper(*rows):
    return {"organic": [{"title": t, "link": u, "snippet": c}
                        for t, u, c in rows]}


def refusal(url):
    """The message `check_url` refused a URL with, or None if it allowed it."""
    try:
        agent_web.check_url(url)
    except agent_web.WebError as error:
        return str(error)
    return None


# --- what a query has to be -------------------------------------------------


def test_an_empty_query_is_refused_and_nothing_is_sent():
    """An empty search box is a mistake, not a search for nothing.

    The refusal has to come before the request, or the model pays a round trip
    to be told by a third party what TMT already knew.
    """
    web = Web(keys={"brave": "k"})
    try:
        answer = agent_web.search("")
        assert "Web search failed" in answer, answer
        assert "query is empty" in answer, answer
        assert web.open.count == 0, web.open.calls
    finally:
        web.close()


def test_a_query_of_nothing_but_whitespace_is_empty_too():
    """`" ".join(query.split())` is what makes this true, and it is easy to
    lose to a `strip()` that only takes the ends off. A tab and two newlines
    is not a search term."""
    web = Web(keys={"brave": "k"})
    try:
        answer = agent_web.search("   \t\n\n  ")
        assert "query is empty" in answer, answer
        assert web.open.count == 0, web.open.calls
    finally:
        web.close()


def test_a_query_that_is_not_text_is_refused_in_words():
    """The model writes the action object, so `"query": 12` is a shape that
    really arrives. It has to come back as a sentence rather than as a
    TypeError raised through the dispatcher."""
    web = Web(keys={"brave": "k"})
    try:
        for value in (12, None, ["a"], {"q": "a"}):
            answer = agent_web.search(value)
            assert "query must be text" in answer, (value, answer)
        assert web.open.count == 0, web.open.calls
    finally:
        web.close()


def test_a_query_over_the_limit_is_refused_and_the_message_names_the_limit():
    """A model that pastes a whole traceback into the query gets a refusal it
    can act on: how long the query was, what the limit is, and what to send
    instead. A bare "too long" would leave it guessing at the size."""
    web = Web(keys={"brave": "k"})
    try:
        answer = agent_web.search("x" * (agent_web.MAX_QUERY_CHARS + 1))
        assert str(agent_web.MAX_QUERY_CHARS) in answer, answer
        assert str(agent_web.MAX_QUERY_CHARS + 1) in answer, answer
        assert web.open.count == 0, web.open.calls
    finally:
        web.close()


def test_a_query_exactly_at_the_limit_is_accepted():
    """The boundary in the other direction, so `>` cannot quietly become
    `>=` and cost the last character."""
    web = Web(keys={"brave": "k"}, replies=[ok(brave())])
    try:
        answer = agent_web.search("x" * agent_web.MAX_QUERY_CHARS)
        assert "Web search failed" not in answer, answer
        assert web.open.count == 1, web.open.calls
    finally:
        web.close()


def test_max_results_is_clamped_to_the_range_the_backend_is_asked_for():
    """Zero results is not a question and a thousand is not an answer. The
    clamp is what stops either reaching the backend, and it is asserted on the
    REQUEST rather than on the reply, because that is where the damage would
    be done."""
    web = Web(keys={"serper": "k"}, replies=[ok(serper()), ok(serper()),
                                             ok(serper())])
    try:
        agent_web.search("q", max_results=0)
        assert web.open.body(0)["num"] == 1, web.open.body(0)
        agent_web.search("q", max_results=999)
        assert web.open.body(1)["num"] == agent_web.MAX_RESULTS, web.open.body(1)
        agent_web.search("q", max_results=None)
        assert web.open.body(2)["num"] == agent_web.DEFAULT_RESULTS, web.open.body(2)
    finally:
        web.close()


def test_max_results_true_is_refused_by_name_rather_than_read_as_one():
    """`int(True)` is 1, so a `"max_results": true` would silently become a
    request for exactly one result -- an answer, for a key the model plainly
    did not mean as a count. `agent_grep` and `agent_verify` refuse a bool for
    the same reason, and this is the third place the same mistake is available.
    """
    web = Web(keys={"brave": "k"})
    try:
        answer = agent_web.search("q", max_results=True)
        assert "max_results must be a number" in answer, answer
        assert web.open.count == 0, web.open.calls
        assert "Web search failed" in agent_web.search("q", max_results=False)
    finally:
        web.close()


def test_max_results_that_is_not_a_number_at_all_is_refused():
    """A string of digits is a number a model reasonably writes; a word is
    not, and it must not arrive at the backend as a `num` it cannot read."""
    web = Web(keys={"serper": "k"}, replies=[ok(serper())])
    try:
        assert "max_results must be a number" in agent_web.search("q", max_results="lots")
        assert "max_results must be a number" in agent_web.search("q", max_results=["3"])
        agent_web.search("q", max_results="3")
        assert web.open.body(0)["num"] == 3, web.open.body(0)
    finally:
        web.close()


def test_a_recency_the_backends_do_not_have_is_refused_and_a_real_one_is_not():
    """`recency` is one of four words. Anything else is a filter that does not
    exist, and applying nothing while saying nothing would answer a broader
    question than the one that was asked."""
    web = Web(keys={"brave": "k"}, replies=[ok(brave()) for _ in agent_web.RECENCY])
    try:
        answer = agent_web.search("q", recency="fortnight")
        assert "recency must be one of" in answer, answer
        for word in agent_web.RECENCY:
            assert "recency must be one of" not in agent_web.search("q", recency=word)
        assert web.open.count == len(agent_web.RECENCY), web.open.calls
    finally:
        web.close()


# --- the three backends, each returning what it really returns --------------


def test_a_tavily_search_returns_the_rows_tavily_gave_it():
    """Tavily's rows are `title`/`url`/`content`, and `content` is the field a
    later edit is most likely to mistake for `snippet` -- the other two
    backends both call it something else."""
    web = Web(keys={"tavily": "tv-key"},
              replies=[ok(tavily(("Zip slip", "https://a.example/1", "How it happens.")))])
    try:
        found = agent_web.collect("zip slip")
        assert found["backend"] == "tavily", found
        assert found["results"] == [{"title": "Zip slip",
                                     "url": "https://a.example/1",
                                     "snippet": "How it happens."}], found
        call = web.open.calls[0]
        assert call["url"] == agent_web.BACKEND_URLS["tavily"], call
        assert web.open.body(0)["api_key"] == "tv-key", web.open.body(0)
        assert web.open.body(0)["query"] == "zip slip", web.open.body(0)
    finally:
        web.close()


def test_a_brave_search_returns_the_rows_brave_gave_it():
    """Brave nests its rows under `web.results` and calls the snippet
    `description`, and it is the one backend that authenticates with a header
    rather than in the body -- so the key must be in the header and NOT in the
    URL, where it would land in somebody's access log."""
    web = Web(keys={"brave": "br-key"},
              replies=[ok(brave(("Linker error", "https://b.example/2", "What it means.")))])
    try:
        found = agent_web.collect("linker error")
        assert found["backend"] == "brave", found
        assert found["results"][0]["title"] == "Linker error", found
        assert found["results"][0]["snippet"] == "What it means.", found
        call = web.open.calls[0]
        assert call["headers"].get("X-Subscription-Token") == "br-key", call
        assert "br-key" not in call["url"], call["url"]
        assert web.open.query(0)["q"] == "linker error", web.open.query(0)
    finally:
        web.close()


def test_a_serper_search_returns_the_rows_serper_gave_it():
    """Serper's rows are under `organic` and its URL field is `link`, which is
    the one name none of the other two use."""
    web = Web(keys={"serper": "sp-key"},
              replies=[ok(serper(("Rate limits", "https://c.example/3", "The table.")))])
    try:
        found = agent_web.collect("rate limits")
        assert found["backend"] == "serper", found
        assert found["results"][0]["url"] == "https://c.example/3", found
        assert found["results"][0]["snippet"] == "The table.", found
        call = web.open.calls[0]
        assert call["headers"].get("X-API-KEY") == "sp-key", call
        assert web.open.body(0)["q"] == "rate limits", web.open.body(0)
    finally:
        web.close()


def test_a_row_with_no_title_still_reads_as_a_row():
    """A backend that returns a URL and no title must not produce a blank
    line the reader cannot tell from a formatting bug. `(untitled)` is the
    honest version of an absent field."""
    web = Web(keys={"tavily": "k"},
              replies=[ok({"results": [{"url": "https://d.example/4"}]})])
    try:
        found = agent_web.collect("q")
        assert found["results"][0]["title"] == "(untitled)", found
        assert found["results"][0]["snippet"] == "", found
    finally:
        web.close()


def test_the_same_page_under_a_trailing_slash_is_returned_once():
    """Backends return one page twice more often than they return two, and it
    is nearly always a trailing slash or a case difference in the host. Two
    identical rows in a five-row answer is a fifth of the answer wasted."""
    web = Web(keys={"tavily": "k"}, replies=[ok(tavily(
        ("One", "https://x.example/a", "first"),
        ("One again", "https://x.example/a/", "second"),
        ("One shouting", "https://X.EXAMPLE/A", "third"),
        ("Two", "https://x.example/b", "fourth"),
    ))])
    try:
        found = agent_web.collect("q")
        assert [row["url"] for row in found["results"]] == [
            "https://x.example/a", "https://x.example/b"], found
        assert found["results"][0]["snippet"] == "first", found
    finally:
        web.close()


def test_a_row_with_no_url_is_dropped_rather_than_rendered_as_a_dead_link():
    """A result the model cannot fetch is not a result. There is nowhere for
    it to go, so it costs tokens and offers nothing."""
    web = Web(keys={"tavily": "k"}, replies=[ok({"results": [
        {"title": "Nowhere", "url": "", "content": "x"},
        {"title": "Somewhere", "url": "https://y.example/1", "content": "y"},
        "not even a dict",
    ]})])
    try:
        found = agent_web.collect("q")
        assert [row["title"] for row in found["results"]] == ["Somewhere"], found
    finally:
        web.close()


def test_more_results_than_were_asked_for_are_cut_to_the_count():
    """A backend can ignore `count` -- Serper in particular returns what it
    likes -- so the ceiling has to be applied to the ANSWER as well as sent
    with the question."""
    rows = [("T%d" % n, "https://z.example/%d" % n, "s") for n in range(9)]
    web = Web(keys={"serper": "k"}, replies=[ok(serper(*rows))])
    try:
        found = agent_web.collect("q", max_results=3)
        assert len(found["results"]) == 3, found
        assert [row["title"] for row in found["results"]] == ["T0", "T1", "T2"], found
    finally:
        web.close()


def test_a_long_snippet_is_cut_to_the_limit_and_says_it_was_cut():
    """A backend returning a whole page as the description would spend the
    context window on one row. The ellipsis is the part that matters: a
    snippet that stops mid-sentence with no mark reads as the page ending
    there."""
    web = Web(keys={"tavily": "k"}, replies=[ok(tavily(
        ("Long", "https://w.example/1", "a" * 900)))])
    try:
        snippet = agent_web.collect("q")["results"][0]["snippet"]
        assert snippet.endswith("..."), snippet[-20:]
        assert len(snippet) == agent_web.MAX_SNIPPET_CHARS + 3, len(snippet)
    finally:
        web.close()


def test_a_snippet_is_flattened_to_one_line():
    """A result block is three lines -- number, URL, snippet -- and a snippet
    carrying its own newlines would break that shape and make the numbering
    unreadable."""
    web = Web(keys={"tavily": "k"}, replies=[ok(tavily(
        ("T", "https://w.example/1", "first line\n\n  second   line\t")))])
    try:
        snippet = agent_web.collect("q")["results"][0]["snippet"]
        assert snippet == "first line second line", repr(snippet)
    finally:
        web.close()


# --- every failure says something different ---------------------------------


def test_an_unconfigured_search_says_so_and_does_not_read_as_an_empty_result_set():
    """The failure this module's prose exists for. "0 results" and "no key" are
    the two answers a model would act on in OPPOSITE directions -- one means
    the answer is not out there, the other means TMT never looked -- and a
    model told the first when the second is true will confidently report that
    the thing does not exist."""
    web = Web()
    try:
        answer = agent_web.search("anything")
        assert "not an empty result set" in answer, answer
        assert "not configured" in answer, answer
        for name in agent_web.KEY_ENV.values():
            assert name in answer, (name, answer)
        assert "0 result" not in answer, answer
        assert web.open.count == 0, web.open.calls
    finally:
        web.close()


def test_a_search_that_ran_and_found_nothing_says_it_ran():
    """The other half of the pair above, and it has to state the opposite
    fact in words: the search HAPPENED. Without that, an empty answer is
    indistinguishable from an unconfigured one and the model cannot tell
    whether trying different words would help."""
    web = Web(keys={"brave": "k"}, replies=[ok(brave())])
    try:
        answer = agent_web.search("nothing at all")
        assert "0 results" in answer, answer
        assert "The search ran" in answer, answer
        assert "try different words" in answer, answer
        assert "not configured" not in answer, answer
    finally:
        web.close()


def test_a_rejected_key_is_reported_as_the_key_and_names_where_to_fix_it():
    """401 and 403 both mean the credential, and the model cannot fix a
    credential -- so the message is for the USER, and it has to name the
    environment variable and the file rather than saying "authentication
    failed"."""
    for status in (401, 403):
        web = Web(keys={"brave": "k"},
                  replies=[(status, "u", {}, b'{"error":"bad key"}')])
        try:
            answer = agent_web.search("q")
            assert "rejected the API key" in answer, (status, answer)
            assert str(status) in answer, (status, answer)
            assert "BRAVE_SEARCH_API_KEY" in answer, answer
            assert "Brave Search" in answer, answer
        finally:
            web.close()


def test_a_rate_limited_key_says_to_wait_rather_than_to_retry():
    """429 is the one failure where retrying immediately makes it worse, and
    the model's instinct on any failure is to try again. The message says what
    to do instead."""
    web = Web(keys={"serper": "k"}, replies=[(429, "u", {}, b"slow down")])
    try:
        answer = agent_web.search("q")
        assert "rate-limiting" in answer, answer
        assert "429" in answer, answer
        assert "Wait" in answer, answer
    finally:
        web.close()


def test_a_backend_failing_says_it_is_the_backend_and_not_the_query():
    """A 5xx sends a model rewriting a query that was never the problem. The
    sentence exists to stop it spending its rounds on that."""
    for status in (500, 502, 503):
        web = Web(keys={"tavily": "k"}, replies=[(status, "u", {}, b"")])
        try:
            answer = agent_web.search("q")
            assert "is failing" in answer, (status, answer)
            assert "not the query" in answer, (status, answer)
            assert str(status) in answer, (status, answer)
        finally:
            web.close()


def test_an_unnamed_status_reports_the_code_rather_than_guessing_a_diagnosis():
    """Everything outside the three named cases reports the number and quotes
    what the backend said. Inventing a meaning for a 418 would be the
    fabrication rule broken in the one place the model has nothing else to go
    on."""
    web = Web(keys={"brave": "k"},
              replies=[(418, "u", {}, b"i am a teapot")])
    try:
        answer = agent_web.search("q")
        assert "418" in answer, answer
        assert "i am a teapot" in answer, answer
    finally:
        web.close()


def test_a_body_that_is_not_json_is_reported_as_that():
    """A backend behind a captive portal or a proxy returns an HTML login
    page with a 200 on it. Letting `json.loads` raise would end the turn with
    a ValueError; saying "returned something that is not JSON" tells the user
    what to go and look at."""
    web = Web(keys={"tavily": "k"},
              replies=[(200, "u", {}, b"<html>sign in</html>")])
    try:
        answer = agent_web.search("q")
        assert "not JSON" in answer, answer
        assert "Tavily" in answer, answer
    finally:
        web.close()


def test_json_that_is_not_an_object_is_reported_as_an_unexpected_shape():
    """`json.loads("[]")` succeeds and then `payload.get` does not exist. The
    shape check is what stops a valid-JSON-wrong-thing becoming an
    AttributeError out of the dispatcher."""
    web = Web(keys={"tavily": "k"}, replies=[(200, "u", {}, b"[1, 2, 3]")])
    try:
        answer = agent_web.search("q")
        assert "unexpected shape" in answer, answer
    finally:
        web.close()


def test_a_transport_failure_is_words_and_not_an_exception():
    """`search` is documented never to raise, and a WebError from `_open` --
    DNS down, TLS refused, timed out -- is the commonest way one arrives. The
    turn has to survive it."""
    web = Web(keys={"brave": "k"},
              replies=[agent_web.WebError("the request timed out after 20s")])
    try:
        answer = agent_web.search("q")
        assert answer.startswith("Web search failed:"), answer
        assert "timed out" in answer, answer
    finally:
        web.close()


def test_an_explicit_backend_with_no_key_is_refused_by_name():
    """`backend: "brave"` on a machine with only a Tavily key is a question
    TMT cannot answer, and silently answering it with Tavily instead would be
    answering a different question."""
    web = Web(keys={"tavily": "k"})
    try:
        answer = agent_web.search("q", backend="brave")
        assert "no API key for Brave Search" in answer, answer
        assert web.open.count == 0, web.open.calls
    finally:
        web.close()


def test_a_backend_tmt_has_never_heard_of_is_refused_and_lists_the_ones_it_has():
    """A model that guesses `"backend": "google"` gets the three real names
    back, which costs one round rather than a search that silently used
    something else."""
    web = Web(keys={"tavily": "k"})
    try:
        answer = agent_web.search("q", backend="google")
        assert "unknown search backend" in answer, answer
        for name in agent_web.BACKENDS:
            assert name in answer, (name, answer)
        assert web.open.count == 0, web.open.calls
    finally:
        web.close()


# --- recency honesty --------------------------------------------------------


def test_brave_is_actually_sent_the_recency_hint():
    """Brave spells it `freshness=pw`. Asserted on the request, because the
    only way to know a filter was applied is that it was sent."""
    web = Web(keys={"brave": "k"}, replies=[ok(brave())])
    try:
        found = agent_web.collect("q", recency="week")
        assert web.open.query(0)["freshness"] == "pw", web.open.query(0)
        assert found["recency_applied"] is True, found
    finally:
        web.close()


def test_serper_is_actually_sent_the_recency_hint():
    """Serper spells the same thing `tbs=qdr:m`, in the body rather than the
    query string -- two backends, two spellings, and one of them would go
    unnoticed if only the other were tested."""
    web = Web(keys={"serper": "k"}, replies=[ok(serper())])
    try:
        found = agent_web.collect("q", recency="month")
        assert web.open.body(0)["tbs"] == "qdr:m", web.open.body(0)
        assert found["recency_applied"] is True, found
    finally:
        web.close()


def test_tavily_is_not_sent_a_recency_hint_and_the_answer_says_it_was_not_applied():
    """Tavily's freshness control applies to its news topic, not to a web
    search, so the hint cannot be honoured. Both halves are load-bearing:
    nothing invented is sent, AND the rendered answer says the filter was not
    applied -- because a model that asked for the last week and is not told
    otherwise will read the results as being from the last week."""
    web = Web(keys={"tavily": "k"},
              replies=[ok(tavily(("T", "https://a.example/1", "s"))),
                       ok(tavily(("T", "https://a.example/1", "s")))])
    try:
        found = agent_web.collect("q", recency="week")
        sent = web.open.body(0)
        assert set(sent) == {"api_key", "query", "max_results", "search_depth"}, sent
        assert found["recency"] == "week", found
        assert found["recency_applied"] is False, found
        answer = agent_web.search("q", recency="week", backend="tavily")
    finally:
        web.close()
    assert "cannot filter by recency" in answer, answer
    assert "not applied" in answer, answer


def test_no_recency_asked_for_means_nothing_said_about_recency():
    """The notice is about a hint that was given and dropped. Printing it when
    nobody asked would be an apology for a thing that did not happen."""
    web = Web(keys={"tavily": "k"},
              replies=[ok(tavily(("T", "https://a.example/1", "s")))])
    try:
        answer = agent_web.search("q")
        assert "recency" not in answer, answer
    finally:
        web.close()


# --- the guard that protects the user from the model ------------------------


def test_a_query_carrying_one_of_this_machines_own_keys_is_refused_and_never_sent():
    """The one guard here pointing the other way. The model can read files, so
    a key it has just read is a string it can put in a query without meaning
    anything by it -- and a search query goes to a third party, who logs it.

    Both assertions matter and only the second is the point: "it was refused"
    is what the model sees, "it was not sent" is what the user is owed.
    """
    web = Web(keys={"brave": "k"}, secrets=[FAKE_SECRET])
    try:
        answer = agent_web.search("why does %s return 401" % FAKE_SECRET)
        assert "contains one of this machine's own API keys" in answer, answer
        assert web.open.count == 0, web.open.calls
        assert FAKE_SECRET not in answer, answer
    finally:
        web.close()


def test_the_leak_check_refuses_rather_than_scrubbing_the_key_out():
    """A query with the key cut out of it is a DIFFERENT question, and
    answering a different question without saying so is worse than saying no.
    The evidence is that the search does not happen at all."""
    web = Web(keys={"brave": "k"}, secrets=[FAKE_SECRET],
              replies=[ok(brave(("T", "https://a.example/1", "s")))])
    try:
        answer = agent_web.search(FAKE_SECRET)
        assert "Web search failed" in answer, answer
        assert web.open.count == 0, web.open.calls
        assert len(web.open.replies) == 1, "the scripted reply was consumed"
    finally:
        web.close()


def test_a_query_that_carries_no_secret_goes_through_untouched():
    """The guard has to be exact rather than fuzzy, or it becomes a filter
    that refuses ordinary questions and gets turned off."""
    web = Web(keys={"brave": "k"}, secrets=[FAKE_SECRET],
              replies=[ok(brave(("T", "https://a.example/1", "s")))])
    try:
        found = agent_web.collect("openrouter 401 unauthorized")
        assert len(found["results"]) == 1, found
        assert web.open.query(0)["q"] == "openrouter 401 unauthorized"
    finally:
        web.close()


def test_a_short_string_is_not_treated_as_a_credential():
    """`_known_secrets` has a 16-character floor, and it is what keeps the
    check from matching half the queries there are. A four-character key would
    otherwise refuse any query containing those four characters."""
    web = Web()
    try:
        agent_web._known_secrets = web.saved_secrets   # the real one, briefly
        web.env("TAVILY_API_KEY", "short")
        assert "short" not in agent_web._known_secrets()
        web.env("TAVILY_API_KEY", FAKE_SECRET)
        assert FAKE_SECRET in agent_web._known_secrets()
    finally:
        web.close()


# --- where a key comes from -------------------------------------------------


def test_the_environment_beats_the_file():
    """`agent_credentials` decides it the same way and for the same reason: a
    variable set in the shell is the one thing a user can change without
    editing anything, so it has to win."""
    web = Web(keys={"brave": "from-file"})
    try:
        assert agent_web.credential("brave") == "from-file"
        web.env("BRAVE_SEARCH_API_KEY", "from-env")
        assert agent_web.credential("brave") == "from-env"
    finally:
        web.close()


def test_a_backend_named_in_the_file_only_wins_while_it_still_has_a_key():
    """A `"backend": "brave"` left behind after the Brave key was removed
    would otherwise turn a working Tavily install into a broken one -- the
    file names a backend, the backend has nothing, and the search fails on a
    machine that can search perfectly well."""
    web = Web(keys={"tavily": "tv"}, backend="brave")
    try:
        assert agent_web.active_backend() == "tavily", agent_web.active_backend()
        web.env("BRAVE_SEARCH_API_KEY", "br")
        assert agent_web.active_backend() == "brave", agent_web.active_backend()
    finally:
        web.close()


def test_a_damaged_settings_file_means_nothing_configured_rather_than_a_crash():
    """The caller's question is whether there is a usable key, and the answer
    for bytes that cannot be parsed is no. `agent_credentials._read_store`
    decides it the same way."""
    for text in ("{not json", "[]", '"a string"', '{"keys": "not a dict"}', ""):
        web = Web()
        try:
            web.write_raw(text)
            assert agent_web.is_configured() is False, text
            assert agent_web.configured_backends() == (), text
            assert agent_web.active_backend() == "", text
        finally:
            web.close()


def test_a_key_of_only_spaces_is_no_key_at_all():
    """A user who exported the variable and pasted nothing has an empty
    credential, and reporting the install as configured would send an empty
    token and get back a 401 nobody can explain."""
    web = Web(keys={"brave": "   "})
    try:
        assert agent_web.credential("brave") == ""
        assert agent_web.is_configured() is False
        web.env("BRAVE_SEARCH_API_KEY", "  \t ")
        assert agent_web.credential("brave") == ""
    finally:
        web.close()


def test_the_status_line_says_which_backend_and_which_others_are_there():
    """What `/config` reads. It has to distinguish "not configured" from
    "configured", and name the one that would actually be used -- a readout
    that only said "yes" would not help somebody whose two keys disagree."""
    web = Web()
    try:
        assert "not configured" in agent_web.status_line()
        web.env("TAVILY_API_KEY", "a")
        web.env("SERPER_API_KEY", "b")
        line = agent_web.status_line()
        assert line.startswith("Web search: Tavily"), line
        assert "also configured: Serper" in line, line
    finally:
        web.close()


# --- SSRF: what may not be opened -------------------------------------------


def test_only_https_can_be_fetched():
    """This is not a general download tool. Each scheme gets its own assert
    because each is a different way in: `file:` reads the disk, `data:` is a
    payload pretending to be a fetch, `javascript:` is not a fetch at all, and
    plain `http` is the one somebody would argue for."""
    assert "only https" in (refusal("http://example.com/") or ""), "http"
    assert "only https" in (refusal("file:///etc/passwd") or ""), "file"
    assert "only https" in (refusal("data:text/html,<b>hi</b>") or ""), "data"
    assert "only https" in (refusal("javascript:alert(1)") or ""), "javascript"
    assert "only https" in (refusal("ftp://files.example/x") or ""), "ftp"
    assert refusal("not a url at all") is not None, "no scheme"


def test_a_literal_private_or_loopback_address_is_refused():
    """The addresses that reach this machine and the network it sits on. Each
    block is its own assert, because a range dropped from a hand-written list
    is invisible -- which is why the module asks `is_global` rather than
    keeping such a list."""
    for host in ("127.0.0.1", "10.1.2.3", "172.16.9.9", "192.168.1.1",
                 "0.0.0.0", "224.0.0.1"):
        message = refusal("https://%s/x" % host)
        assert message is not None, host
        assert "private, loopback or link-local" in message, (host, message)


def test_the_cloud_metadata_endpoint_is_refused():
    """169.254.169.254 is the single most valuable address an SSRF can reach:
    on a cloud host it hands back the instance's credentials. It is
    link-local, which is what `is_global` already knows."""
    message = refusal("https://169.254.169.254/latest/meta-data/")
    assert message is not None
    assert "link-local" in message, message


def test_an_ipv6_loopback_or_unique_local_address_is_refused():
    """A bracketed IPv6 literal is a second spelling of the same attack, and a
    check written against dotted quads misses it entirely."""
    for host in ("[::1]", "[fc00::1]", "[fe80::1]"):
        message = refusal("https://%s/x" % host)
        assert message is not None, host
        assert "TMT will not fetch" in message, (host, message)


def test_an_ipv4_address_wearing_an_ipv6_hat_is_refused():
    """`::ffff:127.0.0.1` reaches the same host as `127.0.0.1` and is longer,
    which is the whole trick: a check that trusted a long address for being
    long would open the loopback back up."""
    message = refusal("https://[::ffff:127.0.0.1]/x")
    assert message is not None
    assert "TMT will not fetch" in message, message


def test_a_url_carrying_credentials_is_refused():
    """`https://user:password@host/` is a credential in a string the model
    wrote, and there is no version of `web_fetch` that should be sending one.
    Refused rather than stripped, so nothing is sent under half a URL."""
    message = refusal("https://user:secret@example.com/x")
    assert message is not None
    assert "user:password@" in message, message


def test_a_url_with_no_host_is_refused():
    """`https:///path` parses, has the right scheme, and has nowhere to go."""
    message = refusal("https:///just/a/path")
    assert message is not None
    assert "no host" in message, message


def test_a_hostname_that_resolves_to_a_private_address_is_refused():
    """The attack a scheme check does not stop: a perfectly ordinary public
    name whose DNS answer points inside. This is why the check resolves rather
    than pattern-matching the host."""
    web = Web(addresses={"internal.example": ["10.0.0.7"]})
    try:
        message = refusal("https://internal.example/admin")
        assert message is not None
        assert "10.0.0.7" in message, message
        assert "internal.example" in message, message
    finally:
        web.close()


def test_one_public_answer_among_private_ones_is_still_refused():
    """A host answering with four addresses gets connected to one of them, and
    TMT does not choose which. So every address has to pass, not any -- an
    `any()` here would be a hole that looks like a working check."""
    web = Web(addresses={"mixed.example": [PUBLIC, "127.0.0.1"]})
    try:
        message = refusal("https://mixed.example/x")
        assert message is not None
        assert "127.0.0.1" in message, message
    finally:
        web.close()


def test_a_host_that_resolves_to_nothing_is_refused():
    """An empty answer must not fall through the `for address in addresses`
    loop as though every address had passed."""
    web = Web(addresses={"empty.example": []})
    try:
        message = refusal("https://empty.example/x")
        assert message is not None
        assert "no addresses" in message, message
    finally:
        web.close()


def test_a_host_that_cannot_be_resolved_is_refused_in_words():
    """`_resolve` itself, with `agent_web`'s view of `socket` replaced rather
    than the real module patched -- a global socket change would follow this
    test out into the rest of the suite. A DNS failure has to arrive as a
    WebError, because a bare gaierror out of the dispatcher ends the turn."""
    saved = agent_web.socket

    def angry(host, port, proto=None):
        raise socket.gaierror("nodename nor servname provided")

    agent_web.socket = types.SimpleNamespace(
        getaddrinfo=angry, IPPROTO_TCP=socket.IPPROTO_TCP,
        gaierror=socket.gaierror, herror=socket.herror)
    try:
        message = refusal("https://nowhere.invalid/x")
        assert message is not None
        assert "could not be resolved" in message, message
    finally:
        agent_web.socket = saved


def test_an_ordinary_public_https_url_is_accepted():
    """The check has to let the normal case through, and this is the assert
    that would fail if a future tightening refused everything. Resolution is
    replaced so no lookup happens."""
    web = Web(addresses={"docs.python.org": [PUBLIC]})
    try:
        parsed = agent_web.check_url("https://docs.python.org/3/library/os.html")
        assert parsed.hostname == "docs.python.org", parsed
        assert refusal("https://docs.python.org/3/library/os.html") is None
        assert refusal("https://%s/x" % PUBLIC) is None, "a public literal"
    finally:
        web.close()


def test_a_url_that_is_not_a_string_is_refused_rather_than_raising():
    """`web_fetch` takes whatever the model put in the `url` key."""
    for value in (None, 12, [], {}):
        assert refusal(value) is not None, value
    assert "a url is required" in (refusal("   ") or "")


# --- web_fetch --------------------------------------------------------------


def test_a_redirect_to_a_private_address_is_refused_on_the_hop_it_appears_on():
    """The most important test in this file.

    A public URL that 302s to 169.254.169.254 is how an SSRF check that runs
    once gets walked round, and it is the reason `agent_web` refuses redirects
    at the opener and follows them by hand. The assertion that carries the
    property is `count == 1`: the second request was never made, so the check
    ran on the URL that would have been OPENED rather than on the one that was
    asked for.
    """
    web = Web(replies=[redirect("https://169.254.169.254/latest/meta-data/",
                                url="https://safe.example/go"),
                       page("<p>secrets</p>")],
              addresses={"safe.example": [PUBLIC]})
    try:
        answer = agent_web.fetch("https://safe.example/go")
        assert "Web fetch failed" in answer, answer
        assert "link-local" in answer, answer
        assert web.open.count == 1, web.open.calls
        assert "secrets" not in answer, answer
    finally:
        web.close()


def test_a_redirect_to_another_scheme_is_refused_on_the_hop_too():
    """The same hole with a different lever: `https` first, then `http` or
    `file` once the check is behind you."""
    for location in ("http://safe.example/plain", "file:///etc/passwd"):
        web = Web(replies=[redirect(location, url="https://safe.example/go")],
                  addresses={"safe.example": [PUBLIC]})
        try:
            answer = agent_web.fetch("https://safe.example/go")
            assert "Web fetch failed" in answer, (location, answer)
            assert web.open.count == 1, web.open.calls
        finally:
            web.close()


def test_redirects_up_to_the_limit_are_followed_and_the_answer_says_how_many():
    """Real documentation redirects -- a version alias, a trailing slash, a
    country domain -- so refusing them all would make the tool useless. The
    count in the head is what lets a reader see they did not land where they
    asked."""
    web = Web(replies=[redirect("https://a.example/2", url="https://a.example/1"),
                       redirect("https://a.example/3", url="https://a.example/2"),
                       redirect("https://a.example/4", url="https://a.example/3"),
                       page("<p>Arrived</p>", url="https://a.example/4")],
              addresses={"a.example": [PUBLIC]})
    try:
        answer = agent_web.fetch("https://a.example/1")
        assert "Arrived" in answer, answer
        assert "https://a.example/4" in answer, answer
        assert "via 3 redirects" in answer, answer
        assert web.open.count == agent_web.MAX_REDIRECTS + 1, web.open.calls
    finally:
        web.close()


def test_one_redirect_is_reported_in_the_singular():
    """`1 redirects` in the one line a reader actually looks at."""
    web = Web(replies=[redirect("https://a.example/2", url="https://a.example/1"),
                       page("<p>Here</p>", url="https://a.example/2")],
              addresses={"a.example": [PUBLIC]})
    try:
        answer = agent_web.fetch("https://a.example/1")
        assert "via 1 redirect)" in answer, answer
    finally:
        web.close()


def test_more_redirects_than_the_limit_is_refused():
    """A redirect loop is otherwise a request that never ends, on a tool with
    a timeout per hop rather than for the whole thing."""
    web = Web(replies=[redirect("https://a.example/%d" % n,
                                url="https://a.example/%d" % (n - 1))
                       for n in range(1, 8)],
              addresses={"a.example": [PUBLIC]})
    try:
        answer = agent_web.fetch("https://a.example/0")
        assert "redirected more than %d times" % agent_web.MAX_REDIRECTS in answer, answer
        assert web.open.count == agent_web.MAX_REDIRECTS + 1, web.open.calls
    finally:
        web.close()


def test_a_redirect_that_does_not_say_where_to_is_reported():
    """A 302 with no Location leaves `urljoin` nothing to work with, and
    following the same URL again would be the loop above with no way out."""
    web = Web(replies=[(302, "https://a.example/1", {}, b"")],
              addresses={"a.example": [PUBLIC]})
    try:
        answer = agent_web.fetch("https://a.example/1")
        assert "without saying where to" in answer, answer
    finally:
        web.close()


def test_a_relative_redirect_is_resolved_against_the_url_that_produced_it():
    """`Location: /v2/guide` is the common form, and resolving it wrongly
    would either fetch nothing or -- worse -- resolve it against something
    else and fetch somewhere nobody named."""
    web = Web(replies=[redirect("/v2/guide", url="https://a.example/v1/guide"),
                       page("<p>Second</p>", url="https://a.example/v2/guide")],
              addresses={"a.example": [PUBLIC]})
    try:
        answer = agent_web.fetch("https://a.example/v1/guide")
        assert "Second" in answer, answer
        assert web.open.calls[1]["url"] == "https://a.example/v2/guide", web.open.calls
    finally:
        web.close()


def test_a_non_text_content_type_is_refused():
    """`web_fetch` reads documentation and source. A PNG or a tarball decoded
    as text is a screen of replacement characters that costs the whole context
    window and says nothing."""
    for kind in ("image/png", "application/zip", "application/octet-stream",
                 "video/mp4"):
        web = Web(replies=[page(b"\x89PNG binary", content_type=kind)],
                  addresses={"docs.example": [PUBLIC]})
        try:
            answer = agent_web.fetch("https://docs.example/guide")
            assert "which is not text" in answer, (kind, answer)
            assert kind in answer, (kind, answer)
        finally:
            web.close()


def test_the_text_types_a_developer_actually_fetches_are_allowed():
    """The other side of the same rule. JSON, plain text and XML are all
    things a coding agent has a real reason to read, and a content-type check
    written as a whitelist of one would refuse them."""
    for kind in ("text/plain", "application/json", "application/xml",
                 "text/markdown", "application/vnd.api+json"):
        web = Web(replies=[page('{"ok": true}', content_type=kind)],
                  addresses={"docs.example": [PUBLIC]})
        try:
            answer = agent_web.fetch("https://docs.example/guide")
            assert "not text" not in answer, (kind, answer)
            assert '{"ok": true}' in answer, (kind, answer)
        finally:
            web.close()


def test_a_reply_with_no_content_type_at_all_is_still_read():
    """Plenty of servers send none, and refusing on an absent header would
    refuse a plain text file for having no opinion about itself."""
    web = Web(replies=[(200, "https://docs.example/x", {}, b"just words")],
              addresses={"docs.example": [PUBLIC]})
    try:
        answer = agent_web.fetch("https://docs.example/x")
        assert "just words" in answer, answer
    finally:
        web.close()


def test_a_non_200_is_reported_with_its_code():
    """A 404 is a fact about the URL the model can act on -- it wrote the URL.
    Rendering the error page as though it were the document would have it
    reading a "not found" page as documentation."""
    web = Web(replies=[page("<h1>Not Found</h1>", status=404)],
              addresses={"docs.example": [PUBLIC]})
    try:
        answer = agent_web.fetch("https://docs.example/guide")
        assert "returned HTTP 404" in answer, answer
        assert "Not Found" not in answer, answer
    finally:
        web.close()


def test_script_and_style_contents_are_gone_from_the_extracted_text():
    """A page's JavaScript is not the page, and a coding agent reading it as
    though it were will confidently quote a minified bundle back. The tags are
    skipped WITH their contents rather than merely unwrapped, which is the
    distinction a naive tag-stripper gets wrong."""
    html = ("<html><head><title>The Guide</title>"
            "<style>.warn { color: red }</style></head>"
            "<body><script>var token = 'do-not-read-me';</script>"
            "<p>Install it first.</p><p>Then run it.</p>"
            "<noscript>enable javascript</noscript></body></html>")
    title, text = agent_web.extract_text(html.encode("utf-8"), "text/html")
    assert title == "The Guide", title
    assert "do-not-read-me" not in text, text
    assert "var token" not in text, text
    assert "color: red" not in text, text
    assert "enable javascript" not in text, text
    assert "Install it first." in text, text
    assert "Then run it." in text, text
    assert text.count("\n\n") == 0 or "\n\n\n" not in text, repr(text)


def test_a_fetched_page_is_headed_by_its_url_and_its_title():
    """Two facts a reader needs before the text: where this came from -- the
    FINAL url, after redirects -- and what the page calls itself."""
    web = Web(replies=[page("<html><head><title>Os Module</title></head>"
                            "<body><p>Miscellaneous interfaces.</p></body></html>",
                            url="https://docs.example/final")],
              addresses={"docs.example": [PUBLIC]})
    try:
        answer = agent_web.fetch("https://docs.example/guide")
        lines = answer.split("\n")
        assert lines[0] == "https://docs.example/final", lines[0]
        assert lines[1] == "Os Module", lines[1]
        assert "Miscellaneous interfaces." in answer, answer
    finally:
        web.close()


def test_text_longer_than_the_limit_is_cut_and_the_answer_says_it_was_cut():
    """Both halves. A page cut without a word said reads as a page that ends
    there, and a model will report the missing half as absent from the
    documentation."""
    web = Web(replies=[page("<p>" + "x" * (agent_web.MAX_TEXT_CHARS + 5000) + "</p>")],
              addresses={"docs.example": [PUBLIC]})
    try:
        answer = agent_web.fetch("https://docs.example/guide")
        assert "the page is longer" in answer, answer[:200]
        assert str(agent_web.MAX_TEXT_CHARS) in answer, answer[:200]
        body = answer.split("\n\n", 1)[1]
        assert len(body) == agent_web.MAX_TEXT_CHARS, len(body)
    finally:
        web.close()


def test_a_page_that_fits_is_not_labelled_as_truncated():
    """The notice is a claim about this page, and putting it on every page
    would make it worthless."""
    web = Web(replies=[page("<p>Short enough.</p>")],
              addresses={"docs.example": [PUBLIC]})
    try:
        answer = agent_web.fetch("https://docs.example/guide")
        assert "the page is longer" not in answer, answer
    finally:
        web.close()


def test_a_page_with_no_readable_text_says_so_rather_than_returning_nothing():
    """An empty string back from a tool reads as a tool that failed silently.
    Naming the URL and the type tells the user which of the two happened."""
    web = Web(replies=[page("<html><body><script>x()</script></body></html>")],
              addresses={"docs.example": [PUBLIC]})
    try:
        answer = agent_web.fetch("https://docs.example/guide")
        assert "found no readable text" in answer, answer
        assert "docs.example" in answer, answer
    finally:
        web.close()


def test_a_non_html_type_is_returned_as_it_stands():
    """Parsing JSON or a source file as HTML would eat every angle bracket in
    it -- a generic C++ template or a Python type hint would come back
    mangled, and the model would debug the damage."""
    source = "template<typename T> void f(T x) { g(x); }"
    title, text = agent_web.extract_text(source.encode("utf-8"), "text/plain")
    assert title == "", title
    assert text == source, text


def test_a_declared_charset_is_honoured_and_a_bad_one_falls_back():
    """A page that declares latin-1 and is decoded as utf-8 comes back full of
    replacement characters, which the model then quotes. An unknown charset
    must not raise -- it is a fact about the server, not a reason to fail."""
    body = "café".encode("latin-1")
    _title, text = agent_web.extract_text(body, "text/plain; charset=iso-8859-1")
    assert text == "café", repr(text)
    _title, text = agent_web.extract_text(body, "text/plain; charset=not-a-charset")
    assert text, "an unknown charset must still return something"


def test_malformed_html_is_still_worth_reading():
    """A page that breaks the parser is still a page. Falling back to the raw
    decode with the tags taken out beats refusing to read it at all."""
    _title, text = agent_web.extract_text(
        b"<p>Half a <b>page", "text/html")
    assert "Half a" in text, text


# --- the timeout ------------------------------------------------------------


def test_fetch_clamps_its_timeout_to_the_ceiling_and_the_floor():
    """A model that writes `"timeout": 600` means minutes and has stopped the
    session for ten of them. The clamp is asserted on the value handed to
    `_open`, because that is the number that would actually be waited out."""
    web = Web(replies=[page("<p>a</p>"), page("<p>b</p>"), page("<p>c</p>")],
              addresses={"docs.example": [PUBLIC]})
    try:
        agent_web.fetch("https://docs.example/guide", timeout=600)
        assert web.open.calls[0]["timeout"] == agent_web.MAX_FETCH_TIMEOUT, web.open.calls
        agent_web.fetch("https://docs.example/guide", timeout=0)
        assert web.open.calls[1]["timeout"] == 1, web.open.calls
        agent_web.fetch("https://docs.example/guide")
        assert web.open.calls[2]["timeout"] == agent_web.FETCH_TIMEOUT, web.open.calls
    finally:
        web.close()


def test_a_timeout_that_is_not_a_number_is_refused_before_anything_is_opened():
    """`int("soon")` is a ValueError, and letting it out of `fetch` would end
    the turn over a key the model can simply be told about."""
    web = Web(replies=[page("<p>a</p>")], addresses={"docs.example": [PUBLIC]})
    try:
        for value in ("soon", [30], {}, None if False else "30s"):
            answer = agent_web.fetch("https://docs.example/guide", timeout=value)
            assert "timeout must be a number" in answer, (value, answer)
        assert web.open.count == 0, web.open.calls
    finally:
        web.close()


def test_fetch_never_raises():
    """Documented in its own docstring, and it is what lets the dispatcher
    hand the result straight to the model. Every refusal above arrives as
    text; this pins the transport failure too."""
    web = Web(replies=[agent_web.WebError("the request failed (connection refused)")],
              addresses={"docs.example": [PUBLIC]})
    try:
        answer = agent_web.fetch("https://docs.example/guide")
        assert answer.startswith("Web fetch failed:"), answer
        assert "connection refused" in answer, answer
    finally:
        web.close()


# --- the shape of the module ------------------------------------------------


def test_open_is_the_only_place_in_the_module_that_makes_a_request():
    """Everything above is driven by replacing `_open`, and that is only a
    complete test of the module while `_open` is the only thing that opens
    anything. A second call site -- added later, for a good reason -- would be
    a path with no SSRF check and no test coverage, and nothing would fail.

    Read off the module's own source, the way `agent_update` and
    `agent_uninstall` are read for the commands they must never run.
    """
    source = Path(inspect.getfile(agent_web)).read_text(encoding="utf-8")
    assert source.count("_OPENER.open") == 1, "more than one opener call"
    assert "_OPENER.open" in inspect.getsource(agent_web._open)
    assert "urlopen" not in source, "urlopen bypasses the no-redirect opener"
    assert "import requests" not in source, "the transport is urllib on purpose"
    assert "subprocess" not in source, "this module starts no process"


def test_the_opener_refuses_redirects_so_every_hop_is_the_callers_decision():
    """If the opener followed redirects itself, `check_url` would run on the
    first URL and on trust thereafter -- which is the hole the hop test above
    is about, arriving through the transport instead of through the loop."""
    handler = agent_web._NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {},
                                    "https://elsewhere.example/") is None


def test_web_error_never_leaves_the_module_through_the_two_tools():
    """`search` and `fetch` are what `agent_actions` calls, and both are
    documented to answer in words. A WebError escaping either would end the
    turn rather than being something the model could work around."""
    web = Web(secrets=[])
    try:
        assert isinstance(agent_web.search(""), str)
        assert isinstance(agent_web.fetch("file:///etc/passwd"), str)
        assert isinstance(agent_web.fetch("https://10.0.0.1/x"), str)
    finally:
        web.close()
