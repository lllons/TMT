"""The Claude subscription route: Anthropic calls through the local `claude -p`.

Nothing here starts a real `claude`. `subprocess.Popen` and the executable
lookup are replaced, so what is checked is the command line TMT builds, the
environment it hands the child, and how a failure is reported.
"""

import io
import json
import os

import agent_credentials
import agent_providers
from test_agent_credentials import Credentials

MANDATORY = ("--setting-sources", "--strict-mcp-config", "--tools",
             "--no-session-persistence", "--system-prompt-file")


class FakeProcess:
    """Just enough of Popen for `_cli_run`: replays canned stdout lines."""

    pid = 1

    def __init__(self, events, stderr=b""):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(b"".join(json.dumps(e).encode() + b"\n" for e in events))
        self.stderr = io.BytesIO(stderr)

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class Runner:
    """Swaps in a fake `claude` and records how it was started."""

    def __init__(self, events, executable="claude-fake"):
        self.events, self.executable, self.calls = events, executable, []

    def __enter__(self):
        self.saved = (agent_providers._cli_executable, agent_providers.subprocess.Popen)
        agent_providers._cli_executable = lambda: self.executable

        def popen(args, **kwargs):
            self.calls.append((args, kwargs))
            return FakeProcess(self.events)

        agent_providers.subprocess.Popen = popen
        return self

    def __exit__(self, *exception):
        agent_providers._cli_executable, agent_providers.subprocess.Popen = self.saved
        return False


def _run(provider_id="anthropic"):
    provider = agent_providers.get_provider(provider_id)
    return "".join(agent_providers._cli_run(
        provider, [{"role": "system", "content": "be brief"},
                   {"role": "user", "content": "hi"}], "sonnet"))


def test_the_route_defaults_to_api_and_persists_a_swap():
    with Credentials():
        assert agent_credentials.anthropic_route() == "api"
        agent_credentials.set_anthropic_route("subscription")
        assert agent_credentials.anthropic_route() == "subscription"
        agent_credentials.set_anthropic_route("api")
        assert agent_credentials.anthropic_route() == "api"


def test_an_unknown_route_is_refused():
    with Credentials():
        try:
            agent_credentials.set_anthropic_route("telepathy")
        except ValueError:
            pass
        else:
            raise AssertionError("an unknown route was accepted")
        assert agent_credentials.anthropic_route() == "api"


def test_uses_cli_follows_the_route_and_only_for_anthropic():
    with Credentials():
        anthropic = agent_providers.get_provider("anthropic")
        openai = agent_providers.get_provider("openai")
        assert not agent_providers.uses_cli(anthropic)
        agent_credentials.set_anthropic_route("subscription")
        assert agent_providers.uses_cli(anthropic)
        assert not agent_providers.uses_cli(openai)


def test_the_subscription_route_needs_no_key():
    with Credentials(key=None, provider="anthropic"):
        assert not agent_credentials.has_credential("anthropic")
        agent_credentials.set_anthropic_route("subscription")
        assert agent_credentials.has_credential("anthropic")


def test_the_claude_cli_adapter_is_not_a_provider_the_user_can_pick():
    assert "claude_cli" not in agent_providers.PROVIDERS
    assert "claude_cli" not in agent_credentials.PROVIDERS


def test_the_command_line_has_every_mandatory_flag_and_never_bare():
    args = agent_providers._cli_args("claude", "sonnet", "system.txt")
    for flag in MANDATORY:
        assert flag in args, flag
    assert "--bare" not in args
    assert args[args.index("--setting-sources") + 1] == ""
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--system-prompt-file") + 1] == "system.txt"


def test_the_child_environment_drops_the_api_key():
    saved = {n: os.environ.get(n) for n in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")}
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-not-real"
    os.environ["ANTHROPIC_BASE_URL"] = "http://example.invalid"
    try:
        env = agent_providers._cli_env()
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_BASE_URL" not in env


def test_a_run_passes_the_flags_a_clean_env_and_the_same_workdir_each_time():
    result = {"type": "result", "result": "ok", "usage": {"output_tokens": 1}}
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-not-real"
    try:
        with Credentials(), Runner([result]) as runner:
            assert _run() == "ok"
            _run()
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    (args, kwargs), (_, again) = runner.calls
    assert args[0] == "claude-fake" and "-p" in args and "--bare" not in args
    for flag in MANDATORY:
        assert flag in args, flag
    assert "ANTHROPIC_API_KEY" not in kwargs["env"]
    assert kwargs["cwd"] == again["cwd"]


def test_claude_not_installed_is_a_clear_error():
    with Credentials(), Runner([], executable=None):
        try:
            _run()
        except agent_providers.ProviderError as error:
            assert "isn't installed" in str(error)
            assert error.kind == "not_found"
        else:
            raise AssertionError("a missing claude did not raise")


def test_a_usage_limit_maps_to_a_plain_non_retryable_error():
    event = {"type": "result", "is_error": True,
             "result": "You've hit your limit for today"}
    with Credentials(), Runner([event]):
        try:
            _run()
        except agent_providers.ProviderError as error:
            assert "usage limit reached" in str(error)
        else:
            raise AssertionError("a usage limit did not raise")


def test_a_rejected_rate_limit_event_is_a_usage_limit_too():
    info = {"type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "resetsAt": 4102444800}}
    event = {"type": "result", "is_error": True, "result": "blocked"}
    with Credentials(), Runner([info, event]):
        try:
            _run()
        except agent_providers.ProviderError as error:
            assert "usage limit reached" in str(error) and "resets around" in str(error)
        else:
            raise AssertionError("a rejected window did not raise")


def test_a_signed_out_claude_is_reported_as_not_signed_in():
    event = {"type": "result", "is_error": True, "result": "Not logged in", "api_error_status": 401}
    with Credentials(), Runner([event]):
        try:
            _run()
        except agent_providers.ProviderError as error:
            assert error.kind == "auth" and "not signed in" in str(error)
        else:
            raise AssertionError("a signed-out claude did not raise")


def test_complete_and_stream_take_the_cli_path_on_the_subscription_route():
    event = {"type": "result", "result": "hello",
             "usage": {"input_tokens": 3, "output_tokens": 2}}
    with Credentials(key=None, provider="anthropic"), Runner([event]) as runner:
        agent_credentials.set_anthropic_route("subscription")
        provider = agent_providers.get_provider("anthropic")
        text, out, inp = agent_providers.complete(provider, "", [{"role": "user", "content": "hi"}],
                                                  model="sonnet")
        assert (text, out, inp) == ("hello", 2, 3)
        streamed = "".join(agent_providers.stream_completion(
            provider, "", [{"role": "user", "content": "hi"}], model="sonnet"))
        assert streamed == "hello" and len(runner.calls) == 2


# --- Settings: the Anthropic route screen -----------------------------------

def _setup(*names):
    import agent_menu
    from test_agent_menu import Keys
    keys = Keys(*names)
    return agent_menu.provider_setup(stream=io.StringIO(), key_reader=keys,
                                     region=agent_menu.LiveRegion(io.StringIO()))


def test_choosing_the_subscription_route_needs_no_key_and_selects_anthropic():
    with Credentials(key=None):
        assert _setup("down", "down", "enter", "down", "enter") == "anthropic"
        assert agent_credentials.anthropic_route() == "subscription"
        assert agent_credentials.selected_provider() == "anthropic"


def test_backing_out_of_the_route_screen_changes_nothing():
    with Credentials(key=None):
        assert _setup("down", "down", "enter", "esc", "esc") is None
        assert agent_credentials.anthropic_route() == "api"


# --- the subscription makes the API key invisible ---------------------------

def test_on_the_subscription_route_no_lookup_returns_the_anthropic_key():
    with Credentials(key=None):
        agent_credentials.set_credential("anthropic", "sk-ant-stored-" + "k" * 20)
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-env-" + "e" * 20
        assert agent_credentials.credential("anthropic").startswith("sk-ant-env-")
        agent_credentials.set_anthropic_route("subscription")
        assert agent_credentials.credential("anthropic") == ""
        assert agent_credentials.masked("anthropic") == ""
        assert agent_credentials.source("anthropic") == agent_credentials.SOURCE_NONE
        assert agent_credentials.has_stored_key("anthropic")      # kept, only unused
        agent_credentials.set_anthropic_route("api")              # swapping back restores it
        assert agent_credentials.credential("anthropic").startswith("sk-ant-env-")
        del os.environ["ANTHROPIC_API_KEY"]
        assert agent_credentials.credential("anthropic").startswith("sk-ant-stored-")


def test_the_http_path_refuses_anthropic_on_the_subscription_route():
    posted = []

    class Session:
        def post(self, *a, **k):
            posted.append(a)

    saved = agent_providers._session
    agent_providers._session = lambda: Session()
    try:
        with Credentials(key=None):
            agent_credentials.set_anthropic_route("subscription")
            provider = agent_providers.get_provider("anthropic")
            try:
                agent_providers._send(provider.chat_url, {}, {}, "sk-ant-" + "x" * 30)
            except agent_providers.ProviderError as error:
                assert "subscription" in str(error)
            else:
                raise AssertionError("the HTTP send was not refused")
            assert posted == []
            agent_credentials.set_anthropic_route("api")
            agent_providers._send(provider.chat_url, {}, {}, "sk-ant-" + "x" * 30)
            assert len(posted) == 1
    finally:
        agent_providers._session = saved


def test_a_cli_failure_reports_its_error_and_never_falls_back_to_the_api():
    saved = (agent_providers._cli_executable, agent_providers._send)
    agent_providers._cli_executable = lambda: None
    agent_providers._send = lambda *a, **k: (_ for _ in ()).throw(AssertionError("HTTP was used"))
    try:
        with Credentials(key=None):
            agent_credentials.set_credential("anthropic", "sk-ant-stored-" + "k" * 20)
            agent_credentials.set_anthropic_route("subscription")
            provider = agent_providers.get_provider("anthropic")
            try:
                agent_providers.complete(provider, "", [{"role": "user", "content": "hi"}])
            except agent_providers.ProviderError as error:
                assert error.kind == "not_found"
            else:
                raise AssertionError("a missing claude did not raise")
    finally:
        agent_providers._cli_executable, agent_providers._send = saved


def test_settings_says_the_api_key_is_not_used_on_the_subscription_route():
    import agent_menu
    with Credentials(key=None):
        agent_credentials.set_credential("anthropic", "sk-ant-stored-" + "k" * 20)
        assert "sk-ant" in agent_menu.provider_key_hint("anthropic")
        agent_credentials.set_anthropic_route("subscription")
        hint = agent_menu.provider_key_hint("anthropic")
        assert hint == "Using your Claude subscription - API key not used", hint
        assert "sk-ant" not in hint
        assert "no key" not in agent_menu.provider_key_hint("anthropic")


def test_choosing_the_subscription_offers_to_forget_the_stored_key():
    with Credentials(key=None):
        agent_credentials.set_credential("anthropic", "sk-ant-stored-" + "k" * 20)
        assert _setup("down", "down", "enter", "down", "enter", "enter") == "anthropic"   # keep (default)
        assert agent_credentials.has_stored_key("anthropic")
        agent_credentials.set_anthropic_route("api")
        agent_credentials.set_provider("openrouter")
        assert _setup("down", "down", "enter", "down", "enter", "down", "enter") == "anthropic"
        assert not agent_credentials.has_stored_key("anthropic")
        assert agent_credentials.anthropic_route() == "subscription"


def test_with_no_provider_a_model_call_fails_with_one_clean_sentence():
    import agent_model
    with Credentials(key=None):
        provider, key, problem = agent_model.selected_provider()
        assert provider is None and problem == agent_credentials.NO_PROVIDER, problem
        data, error = agent_model._post_chat({"messages": [{"role": "user", "content": "hi"}]},
                                             spinner=False)
        assert data is None and error == agent_credentials.NO_PROVIDER
        try:
            list(agent_model.stream_chat({"messages": []}))
        except agent_model.StreamError as raised:
            assert str(raised) == agent_credentials.NO_PROVIDER
        else:
            raise AssertionError("a stream was opened with nothing configured")
        reply = agent_model.ask_model([{"role": "user", "content": "hi"}], quiet=True)
        assert agent_credentials.NO_PROVIDER in reply, reply
