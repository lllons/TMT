"""The two network tools: `web_search` and `web_fetch`.

TMT could reach the network in exactly one way before this -- the model
provider -- and a model that hit an opaque linker error, an unfamiliar exit
code or an API it half-remembered had nowhere to go but its own recollection.
This module is the other way, and it is deliberately narrow: **research a
development problem**, not browse.

What it is NOT, and the design follows from this rather than apologising for
it afterwards:

- **Not an HTTP client for the model.** There is no method, no body, no
  headers, no auth, no cookies and no non-HTTPS scheme. `web_fetch` takes a
  URL and gives back text.
- **Not a way round `agent_bash`'s network policy.** That policy governs
  processes TMT starts; this is TMT making a request itself, with the shape of
  it fixed here. Nothing in this module makes `curl` any more allowed than it
  was, and there is a test that says so.
- **Not a credential path.** Nothing from `INSTALL_DIR` is sent anywhere
  except the one search key the chosen backend needs, and a query that
  contains one of the user's own stored keys is refused rather than sent --
  see `_leaked_secret`, which is the one guard here that protects the user
  from the model rather than the model from the network.

Two seams the rest of TMT already uses, kept for the reasons it keeps them:

- **The transport is `urllib`, not `agent_config._session`.** That session is
  tuned for the model provider -- an IPv4-pinned adapter, streaming, retries
  -- and it follows redirects itself, which is precisely the decision this
  module has to make for itself on every hop. Going through `urllib.request`
  with an opener that refuses redirects means the SSRF check runs on every URL
  actually opened, on the `requests` and no-`requests` installs alike, rather
  than on the first one and then on trust. It is also why nothing here degrades
  when `requests` is absent: it was never used.
- **No new dependency.** `urllib`, `json`, `socket`, `ipaddress` and
  `html.parser`, all stdlib, exactly as the rest of TMT is.

WHAT IS NOT GUARANTEED, stated here rather than discovered later. The SSRF
check resolves the host and refuses every address that is not global, then
opens the URL by name -- so between the check and the connection there is a
second resolution, and a DNS server that answers differently the second time
is not caught. Pinning the address would mean hand-rolling TLS with an
overridden SNI, which is a larger and more fragile thing than the attack it
closes. What IS closed: a literal private address, a host whose name resolves
to one, a redirect to either, and every scheme but HTTPS.
"""

import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import agent_config

# --- limits -----------------------------------------------------------------
#
# Judgements, not measurements, and named so a future reader can see they are
# choices. Each one exists to keep a result usable in a context window rather
# than to make a page arrive intact.

MAX_QUERY_CHARS = 400        # a search query, not a document
DEFAULT_RESULTS = 5
MAX_RESULTS = 10
MAX_SNIPPET_CHARS = 600      # long enough to act on; see OUTPUT QUALITY below
SEARCH_TIMEOUT = 20          # seconds for the whole search request

FETCH_TIMEOUT = 20           # seconds, default
MAX_FETCH_TIMEOUT = 30
MAX_DOWNLOAD_BYTES = 500_000     # read off the socket before giving up
MAX_TEXT_CHARS = 20_000          # extracted text handed to the model
MAX_REDIRECTS = 3

# Where a search key is kept when it is not in the environment. Beside
# .tmt_key and .tmt_providers.json, for agent_config's reason: a credential
# belongs to the INSTALLATION, so it is the same wherever TMT is run from
# rather than scattered across every project it visits.
SEARCH_FILE = agent_config.INSTALL_DIR / ".tmt_search.json"

RECENCY = ("day", "week", "month", "year")


# --- backends ---------------------------------------------------------------
#
# Three, because the question "do you already have a key for one of these" has
# a much better answer than "do you have a key for this one". They are tried in
# the order below when nothing says otherwise.
#
# Google's HTML is deliberately absent. Scraping it is fragile, against its
# terms, and would break without warning in a tool whose whole purpose is to
# be reliable when something else has already gone wrong.

BACKENDS = ("tavily", "brave", "serper")

BACKEND_LABELS = {
    "tavily": "Tavily",
    "brave": "Brave Search",
    "serper": "Serper",
}

# The environment variable each backend's key is read from, checked before the
# file. Spelled out rather than derived from the name so a rename cannot
# silently change which variable a user's shell has to set.
KEY_ENV = {
    "tavily": "TAVILY_API_KEY",
    "brave": "BRAVE_SEARCH_API_KEY",
    "serper": "SERPER_API_KEY",
}

BACKEND_URLS = {
    "tavily": "https://api.tavily.com/search",
    "brave": "https://api.search.brave.com/res/v1/web/search",
    "serper": "https://google.serper.dev/search",
}

# Where a key comes from, for the settings screen to print. A screen that says
# "paste a key" without saying where one is obtained has asked for something
# the user has no way to get.
KEY_URLS = {
    "tavily": "https://app.tavily.com",
    "brave": "https://brave.com/search/api/",
    "serper": "https://serper.dev",
}

# One line each about what the backend IS, so the chooser is a decision rather
# than three names. Kept short: the row it sits on is fitted to the terminal.
BACKEND_NOTES = {
    "tavily": "built for agents; clean snippets",
    "brave": "independent index",
    "serper": "Google results",
}

# Which backends can honour a recency hint, and how they spell it. A backend
# that is not here IGNORES the hint, and the result says so -- inventing a
# filter the API cannot apply would be answering a narrower question than the
# one that was actually asked.
_BRAVE_FRESHNESS = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}
_SERPER_TBS = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}


class WebError(Exception):
    """Something the model needs told in words. Never leaves this module."""


# --- credentials ------------------------------------------------------------

def _read_search_file():
    """The search settings file, or an empty one. Never raises.

    A missing, unreadable or hand-damaged file means "nothing configured"
    rather than a failure, exactly as `agent_credentials._read_store` decides
    for the provider store: the caller's question is whether there is a usable
    key, and the answer for damaged bytes is no.
    """
    try:
        loaded = json.loads(Path(SEARCH_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"backend": "", "keys": {}}
    if not isinstance(loaded, dict):
        return {"backend": "", "keys": {}}
    backend = loaded.get("backend", "")
    keys = loaded.get("keys", {})
    return {
        "backend": backend.strip().lower() if isinstance(backend, str) else "",
        "keys": {name.strip().lower(): value
                 for name, value in (keys.items() if isinstance(keys, dict) else ())
                 if isinstance(name, str) and isinstance(value, str)},
    }


def credential(backend):
    """The key for one backend, or "". Environment first, then the file.

    Same precedence `agent_credentials._resolve` uses, for the same reason: a
    variable set in the shell is the one thing a user can change without
    editing anything, so it has to be the one that wins.
    """
    if backend not in BACKENDS:
        return ""
    from_env = os.environ.get(KEY_ENV[backend], "").strip()
    if from_env:
        return from_env
    return _read_search_file()["keys"].get(backend, "").strip()


def configured_backends():
    """Every backend with a key, in preference order."""
    return tuple(name for name in BACKENDS if credential(name))


def has_credential(backend):
    """Whether one backend has a key here."""
    return bool(credential(backend))


def key_url(backend):
    """Where a key for this backend is obtained, or ""."""
    return KEY_URLS.get(backend, "")


def masked(backend):
    """A safe description of the stored key, never the key.

    Shows too little to reconstruct one and enough to tell two apart, and it
    names the SOURCE for `agent_credentials.masked`'s reason: an environment
    variable outranking the key the user just typed is otherwise silent, and
    a user who cannot see why their new key did nothing has no way to find
    out.
    """
    if not backend or backend not in BACKENDS:
        return "not set"
    from_env = os.environ.get(KEY_ENV[backend], "").strip()
    key = from_env or _read_search_file()["keys"].get(backend, "").strip()
    if not key:
        return "not set"
    return "%s  from %s" % (
        _mask_body(key), KEY_ENV[backend] if from_env else SEARCH_FILE.name)


def _mask_body(key):
    """The visible part of a masked key. Three tiers, not two.

    `agent_credentials.masked` has three and this had two, and the missing one
    was the short case: a key of eight characters or fewer came back WHOLE,
    which is the opposite of what the function above claims. It is reachable --
    `masked` masks an environment variable as well as the stored key, and that
    is arbitrary user content, so a truncated paste or a typo drew itself in
    full on two screens.

    A real backend key is long, so this is about the accident rather than the
    normal case; but a mask that shows everything when the value is short is a
    mask that fails exactly when the value is most likely to be something the
    user did not mean to put there.
    """
    if len(key) <= 8:
        return "..."
    if len(key) < 20:
        return "..." + key[-4:]
    return key[:4] + "..." + key[-4:]


def _restrict(path):
    """Owner-only permissions, where the filesystem has them. Never raises.

    The same courtesy `agent_credentials._restrict` pays the provider store:
    staying out of commits is half of what protects a credential and the file
    mode is the other half.
    """
    try:
        os.chmod(str(path), 0o600)
    except OSError:
        pass


def _write_search_file(document):
    """Persist the settings file with owner-only permissions.

    Written to a neighbouring temporary file, restricted, then moved into
    place, exactly as the provider store is: the file is never briefly
    world-readable, and an interrupted write cannot leave half a file where
    the credential was.
    """
    path = Path(SEARCH_FILE)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    _restrict(temporary)
    os.replace(str(temporary), str(path))
    _restrict(path)
    return document


def save_credential(backend, key):
    """Store a key for one backend and make it the one in use.

    RAISES rather than reporting quietly, which is the opposite of every read
    in this module and is deliberate: `agent_config.set_auto_update` is the
    precedent. A read that fails should fall back to "not configured", but a
    key the user has just pasted that silently did not persist would show as
    set on this screen and be gone on the next launch -- worse than an error
    they can see.

    Every other backend's key is preserved. The file is read at the moment of
    the write, so a key added in another window is not lost by this one.
    """
    name = str(backend).strip().lower()
    if name not in BACKENDS:
        raise ValueError("Not a search backend TMT offers: %r" % (backend,))
    text = str(key or "").strip()
    if not text:
        raise ValueError("An empty key cannot be stored.")
    _preserve_damaged_file()
    document = _read_search_file()
    document["keys"][name] = text
    document["backend"] = name
    return _write_search_file(document)


def _preserve_damaged_file():
    """Move an unreadable store aside before anything writes over it.

    `_read_search_file` reads a damaged file as "nothing configured", which is
    right for a READ -- the caller's question is whether there is a usable key.
    Combined with a whole-document write it silently destroyed every other
    backend's key: one stray byte in the file and the next save wrote a fresh
    document containing only the key just typed.

    Nothing here can repair the file, and guessing at its contents would be
    worse than losing it. What it can do is not be the thing that throws it
    away: the damaged bytes are kept beside the store so a user who had two
    keys in there can get them back out by hand.
    """
    path = Path(SEARCH_FILE)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return                      # nothing there, which is not damage
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            return                  # readable; leave it exactly as it is
    except ValueError:
        pass
    try:
        os.replace(str(path), str(path) + ".damaged")
    except OSError:
        pass


def save_backend(backend):
    """Persist which backend to prefer, without touching any key."""
    name = str(backend).strip().lower()
    if name not in BACKENDS:
        raise ValueError("Not a search backend TMT offers: %r" % (backend,))
    document = _read_search_file()
    document["backend"] = name
    return _write_search_file(document)


def clear_credential(backend):
    """Forget one backend's key. Returns True when there was one to forget.

    An environment variable is NOT cleared and cannot be from here, so the
    answer says so: a user who removes a key and still sees search working
    needs to know which of the two is speaking.
    """
    name = str(backend).strip().lower()
    if name not in BACKENDS:
        raise ValueError("Not a search backend TMT offers: %r" % (backend,))
    document = _read_search_file()
    had = bool(document["keys"].pop(name, ""))
    if document["backend"] == name:
        document["backend"] = ""
    _write_search_file(document)
    return had


def check_key(backend, key):
    """Ask the backend about a key, and report what it said.

    TMT never decides that a key is valid; only the backend can. This spends
    ONE search from the user's quota, which is the same trade
    `agent_menu._check_credential` already makes for a provider key: finding
    out now beats finding out in the middle of a task.
    """
    label = BACKEND_LABELS.get(backend, backend)
    try:
        # Inside the try: an unknown backend here would otherwise raise a
        # KeyError out of a function whose whole job is to report in words.
        call, _parse = _BACKEND_CALLS[backend]
        status, _final, _headers, body = call(key, "tmt web search check", 1, "")
    except WebError as error:
        # The REASON only, never the exception's own text. `_check_credential`
        # states the rule -- an exception message can carry a request, and a
        # request to Tavily carries the key in its body -- and this branch was
        # the one place in the new code that interpolated the exception.
        return False, "Saved. TMT could not reach %s (%s)." % (
            label, str(error).split("(")[0].strip() or "the request failed")
    except Exception as error:
        return False, "Saved. TMT could not check it with %s (%s)." % (
            label, error.__class__.__name__)
    if status == 200:
        return True, "Saved. %s accepted it." % label
    return False, "Saved, but " + _status_message(backend, status, body)


def active_backend():
    """The backend a search would use, or "" when none is configured.

    An explicit choice in the file wins, but only when it actually has a key:
    a `"backend": "brave"` left behind after the Brave key was removed would
    otherwise turn a working Tavily install into a broken one.
    """
    chosen = _read_search_file()["backend"]
    override = os.environ.get("TMT_SEARCH_BACKEND", "").strip().lower()
    for name in (override, chosen):
        if name in BACKENDS and credential(name):
            return name
    available = configured_backends()
    return available[0] if available else ""


def is_configured():
    """Whether searching is possible at all here."""
    return bool(active_backend())


_NOT_CONFIGURED = (
    "Web search is not configured, so nothing was searched -- this is not an "
    "empty result set. TMT needs an API key for one of: %s. Set one of the "
    "environment variables %s, or put it in %s as "
    '{"keys": {"<backend>": "<key>"}}. Until then, work from the local files, '
    "the command output you already have, and what you know."
)


def _unconfigured_message():
    return _NOT_CONFIGURED % (
        ", ".join(BACKEND_LABELS[name] for name in BACKENDS),
        ", ".join(KEY_ENV[name] for name in BACKENDS),
        SEARCH_FILE,
    )


# --- the one guard that protects the user from the model --------------------

def _known_secrets():
    """Every credential this installation holds, for the leak check.

    Read defensively: this runs on the way to the network, and a store that
    cannot be read must not stop a search. An empty answer means the check
    finds nothing, which is the same position TMT was in before it existed.
    """
    secrets = []
    try:
        secrets.append(agent_config.read_saved_key())
    except Exception:
        pass
    try:
        import agent_credentials
        for provider in agent_credentials.PROVIDERS:
            try:
                secrets.append(agent_credentials.credential(provider))
            except Exception:
                pass
    except Exception:
        pass
    for backend in BACKENDS:
        try:
            secrets.append(credential(backend))
        except Exception:
            pass
    # Short values are not credentials and would match half the queries there
    # are; 16 is comfortably below every key format TMT stores and comfortably
    # above anything a person would type into a search box by accident.
    return [value.strip() for value in secrets
            if isinstance(value, str) and len(value.strip()) >= 16]


def _leaked_secret(text):
    """Whether `text` carries one of this installation's own credentials.

    The model can read files, and a key it has just read is a string it can
    put in a query without meaning anything by it. Sending that to a search
    backend publishes it. This is cheap, exact, and refuses rather than
    scrubs -- a query with a key cut out of it is a different question, and
    answering a different question silently is worse than saying no.
    """
    return any(secret and secret in text for secret in _known_secrets())


_SECRET_IN_QUERY = (
    "that query contains one of this machine's own API keys, and a search "
    "query is sent to a third party. Search for the error or the symptom "
    "instead, with the key taken out."
)


# --- SSRF -------------------------------------------------------------------

def _is_public_address(address):
    """Whether one resolved IP may be connected to.

    `is_global` is the whole test and it is stricter than a hand-written list
    of ranges: it already excludes loopback, link-local (169.254.0.0/16 and
    fe80::/10, which is what makes the cloud metadata endpoint unreachable),
    the RFC1918 blocks, unique-local fc00::/7, carrier-grade NAT, multicast,
    reserved and unspecified. A list written out by hand is a list that is
    missing whatever was allocated after it was written.
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    if parsed.is_private or parsed.is_loopback or parsed.is_link_local:
        return False
    if parsed.is_multicast or parsed.is_reserved or parsed.is_unspecified:
        return False
    # An IPv4 address wearing an IPv6 hat reaches the same host, so it is
    # unwrapped and asked again rather than trusted for being long.
    mapped = getattr(parsed, "ipv4_mapped", None)
    if mapped is not None:
        return _is_public_address(str(mapped))
    return bool(parsed.is_global)


def _resolve(host, port):
    """Every address `host` resolves to. Raises WebError when it cannot."""
    try:
        return [info[4][0] for info in socket.getaddrinfo(
            host, port, proto=socket.IPPROTO_TCP)]
    except (socket.gaierror, socket.herror, UnicodeError, OSError) as error:
        raise WebError("that host could not be resolved (%s)" % error)


def check_url(url):
    """Refuse anything `web_fetch` may not open. Returns the parsed URL.

    Every refusal here is a fact about the URL rather than a judgement about
    the site, so each one can say exactly what was wrong.
    """
    if not isinstance(url, str) or not url.strip():
        raise WebError("a url is required")
    url = url.strip()
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as error:
        raise WebError("that url could not be read (%s)" % error)
    if parsed.scheme.lower() != "https":
        raise WebError(
            "only https:// urls can be fetched, and that one is %s. This is "
            "not a general download tool: http, file, data, ftp and "
            "javascript urls are all refused."
            % (parsed.scheme + "://" if parsed.scheme else "not a url"))
    if not parsed.hostname:
        raise WebError("that url has no host in it")
    if parsed.username or parsed.password:
        raise WebError("a url carrying credentials is refused; take the "
                       "user:password@ part out")
    host = parsed.hostname
    # `urlsplit` is LAZY about the port: it parses the authority but does not
    # look at the number until `.port` is read, so a malformed one raises here
    # and not in the try above. Left uncaught it escaped `fetch`, whose whole
    # contract is that it never raises -- `agent_actions._run_tool` then caught
    # the ValueError and reported it with the wording it keeps for a path
    # outside the workspace, which is a true exception described as the wrong
    # thing entirely.
    try:
        port = parsed.port or 443
    except ValueError as error:
        raise WebError("that url has an unusable port (%s)" % error)
    # A literal address is checked as it stands; a name is checked against
    # every address it resolves to, because one public answer among four
    # private ones is still a way in.
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        addresses = _resolve(host, port)
    if not addresses:
        raise WebError("that host resolved to no addresses")
    for address in addresses:
        if not _is_public_address(address):
            raise WebError(
                "%s resolves to %s, which is a private, loopback or "
                "link-local address. TMT will not fetch from inside this "
                "machine or its network." % (host, address))
    return parsed


# --- transport --------------------------------------------------------------

# No redirects, ever, from the opener. Each hop is inspected and re-checked by
# the caller instead, which is the only way the SSRF test above applies to the
# URL actually opened rather than only to the one that was asked for.
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)

# What TMT calls itself on the wire. The same name it gives OpenRouter, so a
# site owner seeing it in a log can tell what it was.
USER_AGENT = "%s/web (+%s)" % (agent_config.APP_TITLE, agent_config.APP_URL)


def _open(url, data=None, headers=None, timeout=SEARCH_TIMEOUT, limit=MAX_DOWNLOAD_BYTES):
    """One HTTP request. Returns (status, final_url, headers, body bytes).

    The seam every test replaces: nothing above this makes a socket, so the
    whole module can be driven without a network and no test depends on a
    third party being up.

    A redirect comes back as its status and Location rather than being
    followed, because whether it may be followed is not this function's
    decision to make.
    """
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    request.add_header("User-Agent", USER_AGENT)
    try:
        opened = _OPENER.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        # An HTTP error is an answer, not a failure: a 401 from a search
        # backend is the key being wrong, and the caller can say so.
        body = error.read(limit) if hasattr(error, "read") else b""
        return error.code, error.url or url, dict(error.headers or {}), body
    except urllib.error.URLError as error:
        raise WebError("the request failed (%s)" % (error.reason,))
    except (socket.timeout, TimeoutError):
        raise WebError("the request timed out after %ss" % timeout)
    except OSError as error:
        raise WebError("the request failed (%s)" % error)
    try:
        with opened:
            return (opened.status if hasattr(opened, "status") else opened.getcode(),
                    opened.geturl(), dict(opened.headers or {}),
                    opened.read(limit))
    except (socket.timeout, TimeoutError):
        raise WebError("the response timed out after %ss" % timeout)
    except OSError as error:
        raise WebError("the response could not be read (%s)" % error)


# --- web_search -------------------------------------------------------------

def _clean_query(query):
    if not isinstance(query, str):
        raise WebError("query must be text")
    cleaned = " ".join(query.split())
    if not cleaned:
        raise WebError("query is empty; say what to search for")
    if len(cleaned) > MAX_QUERY_CHARS:
        raise WebError(
            "that query is %d characters and the limit is %d. A search query "
            "is the error message or the symptom, not the whole log -- paste "
            "the one line that names the failure."
            % (len(cleaned), MAX_QUERY_CHARS))
    return cleaned


def _clean_count(value):
    """How many results to ask for. A bool is refused by name.

    `int(True)` is 1, so `"max_results": true` would quietly become a request
    for one result -- an answer, for a key the model plainly did not mean as a
    count. `agent_verify.VerificationCheck.record` and `agent_grep` refuse a
    bool for the same reason.
    """
    if value is None:
        return DEFAULT_RESULTS
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise WebError("max_results must be a number")
    try:
        count = int(value)
    except (TypeError, ValueError):
        raise WebError("max_results must be a number")
    return max(1, min(MAX_RESULTS, count))


def _clean_recency(value):
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or value.strip().lower() not in RECENCY:
        raise WebError("recency must be one of: %s" % ", ".join(RECENCY))
    return value.strip().lower()


def _request_tavily(key, query, count, recency):
    body = json.dumps({
        "api_key": key, "query": query, "max_results": count,
        "search_depth": "advanced",
    }).encode("utf-8")
    return _open(BACKEND_URLS["tavily"], data=body,
                 headers={"Content-Type": "application/json"},
                 timeout=SEARCH_TIMEOUT)


def _parse_tavily(payload):
    return [(item.get("title"), item.get("url"), item.get("content"))
            for item in payload.get("results", []) if isinstance(item, dict)]


def _request_brave(key, query, count, recency):
    params = {"q": query, "count": count}
    if recency:
        params["freshness"] = _BRAVE_FRESHNESS[recency]
    url = BACKEND_URLS["brave"] + "?" + urllib.parse.urlencode(params)
    return _open(url, headers={"Accept": "application/json",
                               "X-Subscription-Token": key},
                 timeout=SEARCH_TIMEOUT)


def _parse_brave(payload):
    web = payload.get("web") or {}
    return [(item.get("title"), item.get("url"), item.get("description"))
            for item in web.get("results", []) if isinstance(item, dict)]


def _request_serper(key, query, count, recency):
    body = {"q": query, "num": count}
    if recency:
        body["tbs"] = _SERPER_TBS[recency]
    return _open(BACKEND_URLS["serper"], data=json.dumps(body).encode("utf-8"),
                 headers={"Content-Type": "application/json", "X-API-KEY": key},
                 timeout=SEARCH_TIMEOUT)


def _parse_serper(payload):
    return [(item.get("title"), item.get("link"), item.get("snippet"))
            for item in payload.get("organic", []) if isinstance(item, dict)]


_BACKEND_CALLS = {
    "tavily": (_request_tavily, _parse_tavily),
    "brave": (_request_brave, _parse_brave),
    "serper": (_request_serper, _parse_serper),
}

# Which backends honour `recency`. Tavily's own freshness control applies to
# its news topic rather than to a web search, so the hint is not sent and the
# result says it was not applied.
_HONOURS_RECENCY = ("brave", "serper")


def _status_message(backend, status, body):
    """What an HTTP status from a search backend means, in words.

    Named cases only where the meaning is specific and actionable; everything
    else reports the code, because guessing at a 5xx would be inventing a
    diagnosis.
    """
    label = BACKEND_LABELS[backend]
    if status in (401, 403):
        return ("%s rejected the API key (HTTP %d). The key in %s or %s is "
                "missing, wrong or out of quota."
                % (label, status, KEY_ENV[backend], SEARCH_FILE))
    if status == 429:
        return ("%s is rate-limiting this key (HTTP 429). Wait, or work from "
                "what you already have." % label)
    if status >= 500:
        return "%s is failing (HTTP %d). This is the backend, not the query." % (label, status)
    detail = ""
    if body:
        text = body.decode("utf-8", "replace").strip()
        if text:
            detail = " " + text[:200]
    return "%s returned HTTP %d.%s" % (label, status, detail)


def _dedupe(rows):
    """Drop rows repeating a URL already seen, keeping the first.

    Backends return the same page under a tracking parameter or a trailing
    slash more often than they return it twice verbatim, so the comparison is
    on the URL with those settled -- cheap, and it is the cheap case section 8
    asks for rather than a similarity measure.
    """
    seen, kept = set(), []
    for title, url, snippet in rows:
        if not url:
            continue
        split = urllib.parse.urlsplit(url)
        identity = (split.netloc.lower(),
                    split.path.rstrip("/").lower() or "/")
        if identity in seen:
            continue
        seen.add(identity)
        kept.append((title, url, snippet))
    return kept


def _tidy(text, limit):
    """One field of a result, as a single line, cut to `limit`."""
    if not isinstance(text, str):
        return ""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[:limit].rstrip() + "..."


def collect(query, max_results=None, recency=None, backend=None):
    """The structured results, before anything is rendered.

    Separated from `search` so a test can assert on the rows rather than on
    prose about them, and so the rendering can change without the parsing
    being re-verified.
    """
    cleaned = _clean_query(query)
    if _leaked_secret(cleaned):
        raise WebError(_SECRET_IN_QUERY)
    count = _clean_count(max_results)
    freshness = _clean_recency(recency)
    name = backend or active_backend()
    if not name:
        raise WebError(_unconfigured_message())
    if name not in BACKENDS:
        raise WebError("unknown search backend %r; TMT knows %s"
                       % (name, ", ".join(BACKENDS)))
    key = credential(name)
    if not key:
        raise WebError("there is no API key for %s" % BACKEND_LABELS[name])

    call, parse = _BACKEND_CALLS[name]
    status, _final, _headers, body = call(key, cleaned, count, freshness)
    if status != 200:
        raise WebError(_status_message(name, status, body))
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise WebError("%s returned something that is not JSON"
                       % BACKEND_LABELS[name])
    if not isinstance(payload, dict):
        raise WebError("%s returned an unexpected shape" % BACKEND_LABELS[name])

    rows = _dedupe(parse(payload))[:count]
    return {
        "query": cleaned,
        "backend": name,
        "recency": freshness,
        "recency_applied": bool(freshness) and name in _HONOURS_RECENCY,
        "results": [{"title": _tidy(title, 160) or "(untitled)",
                     "url": url,
                     "snippet": _tidy(snippet, MAX_SNIPPET_CHARS)}
                    for title, url, snippet in rows],
    }


def search(query, max_results=None, recency=None, backend=None):
    """`web_search`. Returns the text the model reads.

    Rendered rather than handed over as JSON because every other tool in TMT
    answers in text and the model reads the result either way -- a numbered
    block with the title, the URL and the snippet costs fewer tokens than the
    same three fields escaped inside braces, and the URL stays clickable in
    the transcript.
    """
    try:
        found = collect(query, max_results=max_results, recency=recency,
                        backend=backend)
    except WebError as error:
        return "Web search failed: %s" % error
    header = "%d result%s for %r via %s" % (
        len(found["results"]), "" if len(found["results"]) == 1 else "s",
        found["query"], BACKEND_LABELS[found["backend"]])
    if not found["results"]:
        return (header + ". The search ran and the backend returned nothing; "
                "try different words, or a quoted exact phrase from the error.")
    lines = [header + ":"]
    if found["recency"] and not found["recency_applied"]:
        lines.append("(%s cannot filter by recency, so %r was not applied.)"
                     % (BACKEND_LABELS[found["backend"]], found["recency"]))
    for index, row in enumerate(found["results"], 1):
        lines.append("")
        lines.append("%d. %s" % (index, row["title"]))
        lines.append("   %s" % row["url"])
        if row["snippet"]:
            lines.append("   %s" % row["snippet"])
    return "\n".join(lines)


# --- web_fetch --------------------------------------------------------------

_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg",
                        "head", "nav", "footer", "form", "iframe"})
_BLOCK_TAGS = frozenset({"p", "div", "br", "li", "tr", "section", "article",
                         "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote",
                         "table", "ul", "ol", "dl", "dt", "dd", "hr"})


class _Text(HTMLParser):
    """HTML reduced to the text a reader would see.

    Not a renderer and not trying to be one: the job is to get the prose and
    the code out of a documentation page without carrying a megabyte of markup
    into the context window. Everything inside a skipped tag is dropped
    entirely -- a page's script is not something a coding agent should be
    reading as though it were the page.
    """

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.parts = []
        self._skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = " ".join(data.split())
        if self._skip:
            return
        if data.strip():
            self.parts.append(data)

    def text(self):
        joined = "".join(self.parts)
        # Collapse runs of blank lines the block tags produce, and trailing
        # spaces with them, so the shape of the page survives without the
        # emptiness of its markup.
        joined = re.sub(r"[ \t\f\v]+", " ", joined)
        joined = re.sub(r" *\n *", "\n", joined)
        return re.sub(r"\n{3,}", "\n\n", joined).strip()


def extract_text(body, content_type=""):
    """The readable text of a response. Returns (title, text)."""
    kind = (content_type or "").split(";")[0].strip().lower()
    charset = "utf-8"
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    try:
        decoded = body.decode(charset, "replace")
    except (LookupError, UnicodeError):
        decoded = body.decode("utf-8", "replace")
    if kind and "html" not in kind:
        # Plain text, markdown, JSON, a source file: it is already what a
        # reader would see, so parsing it as HTML would only damage it.
        return "", decoded.strip()
    parser = _Text()
    try:
        parser.feed(decoded)
        parser.close()
    except Exception:
        # A malformed page is still worth something. Falling back to the raw
        # decode with the tags stripped beats refusing to read it at all.
        return "", re.sub(r"<[^>]+>", " ", decoded).strip()
    return parser.title, parser.text()


_FETCHABLE = ("text/", "application/json", "application/xml",
              "application/xhtml", "application/javascript", "+json", "+xml")


def fetch(url, timeout=None):
    """`web_fetch`. Returns the text the model reads.

    HTTPS only, no private addresses, redirects followed by hand up to
    MAX_REDIRECTS with every hop re-checked, and the body truncated twice --
    once at the socket and once after extraction.
    """
    # A bool is refused BY NAME, for `_clean_count`'s reason three functions
    # up: `int(True)` is 1, so `"timeout": true` would quietly become a
    # one-second deadline -- which fails almost every real fetch and reports
    # it as the network being slow rather than as the malformed key it is.
    if isinstance(timeout, bool):
        return "Web fetch failed: timeout must be a number of seconds"
    try:
        seconds = FETCH_TIMEOUT if timeout is None else int(timeout)
    except (TypeError, ValueError):
        return "Web fetch failed: timeout must be a number of seconds"
    seconds = max(1, min(MAX_FETCH_TIMEOUT, seconds))
    try:
        return _fetch(url, seconds)
    except WebError as error:
        return "Web fetch failed: %s" % error


def _fetch(url, seconds):
    seen = []
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        parsed = check_url(current)
        seen.append(current)
        status, final, headers, body = _open(
            current, headers={"Accept": "text/html,text/plain,*/*"},
            timeout=seconds, limit=MAX_DOWNLOAD_BYTES)
        if status in (301, 302, 303, 307, 308):
            location = headers.get("Location") or headers.get("location")
            if not location:
                raise WebError("that url redirected without saying where to")
            # Relative locations are resolved against the URL that produced
            # them, then checked from scratch by the next pass -- which is
            # what stops a redirect being the way round check_url.
            current = urllib.parse.urljoin(current, location)
            continue
        if status != 200:
            raise WebError("that url returned HTTP %d" % status)
        content_type = (headers.get("Content-Type")
                        or headers.get("content-type") or "")
        kind = content_type.split(";")[0].strip().lower()
        if kind and not any(mark in kind for mark in _FETCHABLE):
            raise WebError(
                "that url is %s, which is not text. web_fetch reads "
                "documentation and source, not binaries, archives or media."
                % kind)
        title, text = extract_text(body, content_type)
        if not text:
            return ("Fetched %s (%s) and found no readable text in it."
                    % (final or current, kind or "unknown type"))
        truncated = len(text) > MAX_TEXT_CHARS
        if truncated:
            text = text[:MAX_TEXT_CHARS].rstrip()
        head = ["%s%s" % (final or current, " (via %d redirect%s)" % (
            len(seen) - 1, "" if len(seen) == 2 else "s") if len(seen) > 1 else "")]
        if title:
            head.append(title)
        if truncated:
            head.append("[first %d characters; the page is longer]" % MAX_TEXT_CHARS)
        return "\n".join(head) + "\n\n" + text
    raise WebError("that url redirected more than %d times" % MAX_REDIRECTS)


# --- what /config and the prompt say ----------------------------------------

def status_line():
    """One line about whether search works here, for a settings readout."""
    name = active_backend()
    if not name:
        return "Web search: not configured (no API key for %s)" % (
            ", ".join(BACKEND_LABELS[backend] for backend in BACKENDS))
    others = [BACKEND_LABELS[backend] for backend in configured_backends()
              if backend != name]
    extra = " (also configured: %s)" % ", ".join(others) if others else ""
    return "Web search: %s%s" % (BACKEND_LABELS[name], extra)
