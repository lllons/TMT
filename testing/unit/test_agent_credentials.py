"""The credential store, and the sandbox that keeps a test out of the real one.

`agent_credentials._resolve` reads a key from three places, highest first: an
environment variable, the store file in `INSTALL_DIR`, and -- for OpenRouter
alone -- the `.tmt_key` that first-launch setup wrote before the store
existed. All three belong to the INSTALLATION, so a test that reads any of
them is asking a question about the machine it happens to be running on.

Four tests did exactly that, and nobody noticed for months, because the answer
is right on a developer's machine and wrong on a fresh clone:

    test_agent_menu.test_enter_on_the_first_item_starts
    test_agent_menu.test_the_frame_names_the_current_model_and_the_workspace
    test_agent_menu.test_the_selected_item_is_marked_without_relying_on_colour
    test_agent_stream.test_a_multibyte_character_survives_the_stream_intact

The first three press Enter on Start, and Start is where TMT asks for a
credential when there is none -- so with no key the key screen opens and eats
the scripted keystrokes, and the test fails saying the menu asked for a key
the script did not provide. The fourth replaces the transport and never
reaches it, because `stream_chat` refuses without a credential before it
posts. **None of the four is about credentials**, which is the whole point:
they were reading the machine by accident.

`Credentials` is the fix, and it lives here so the two modules that need it
can import it by bare stem -- the shape `test_agent_workspace.Workspace`
already has. `test_agent_search_settings` solved the identical problem for the
SEARCH key and says so in its own docstring; this is that reasoning applied to
the credential TMT cannot start without.

The tests below are about the sandbox itself. A sandbox that quietly stopped
isolating would put the four tests back to reading the machine, and they would
still pass here -- so the isolation needs a test of its own, and it has to be
one that fails on a machine WITH a key as well as on one without.
"""

import os
import shutil
import tempfile
from pathlib import Path

import agent_config
import agent_credentials

# Obviously not a key, and it never leaves the process: every test that pins
# one has already replaced the transport. Shaped so that a search of this
# repository for something credential-looking finds a string that says what it
# is.
FAKE_KEY = "sk-test-not-a-real-key-0000"

INSTALL_DIR = Path(agent_config.__file__).resolve().parent


class Credentials:
    """A credential store of the test's own, in a temporary directory.

    Every source `_resolve` reads is redirected or emptied: the four
    environment variables, `TMT_PROVIDER`, `agent_credentials.STORE_FILE`, and
    the legacy `agent_config.KEY_FILE`. `agent_config.OPENROUTER_API_KEY` goes
    with them -- it is a module global bound at import, and `agent_model` puts
    it straight into a request header.

    **Both directions matter.** Pinning only the empty case would still leave a
    developer's own provider choice deciding what `current_provider()` returns,
    so a test that draws a screen naming the provider would pass here and fail
    on a machine set to Anthropic. The sandbox therefore states the answer
    rather than hiding the question: openrouter, with a key, unless asked
    otherwise.

    `key=None` is the other half -- a store that is genuinely empty, for a test
    about what TMT does when it cannot reach a model.

    Usable as a context manager or held open and `close()`d, because
    `test_agent_menu.Sandbox` holds one for the length of a test and
    `test_agent_stream` wants it round a single call.
    """

    def __init__(self, key=FAKE_KEY, provider="openrouter"):
        self.dir = Path(tempfile.mkdtemp(prefix="tmt_cred_"))
        self.saved_store = agent_credentials.STORE_FILE
        self.saved_key_file = agent_config.KEY_FILE
        self.saved_config_key = agent_config.OPENROUTER_API_KEY
        self.saved_env = {
            name: os.environ.pop(name, None)
            for name in tuple(agent_credentials.KEY_ENV.values())
            + (agent_credentials.PROVIDER_ENV,)
        }
        agent_credentials.STORE_FILE = self.dir / ".tmt_providers.json"
        agent_config.KEY_FILE = self.dir / ".tmt_key"
        agent_config.OPENROUTER_API_KEY = key or ""
        # CHECKED BEFORE A BYTE IS WRITTEN, and this is not defensive
        # decoration: the two lines above are the whole isolation, and the
        # three below WRITE. A sandbox whose redirect did not take is a
        # sandbox that writes a fake credential over the real one -- which is
        # not hypothetical, it is what a mutation run did to this machine's
        # store before this guard existed. The failure is silent afterwards:
        # the store looks fine, and TMT answers HTTP 401 on the next launch.
        # Fail closed, the `agent_capabilities.refusal` shape, because what is
        # being protected here is somebody's credential.
        self._must_be_mine(agent_credentials.STORE_FILE, "STORE_FILE")
        self._must_be_mine(agent_config.KEY_FILE, "KEY_FILE")
        # Written through the store's own API rather than as hand-built JSON.
        # The stored value is obfuscated, and a test that wrote the plain key
        # would be asserting against a file shape the product never produces.
        agent_credentials.set_provider(provider)
        if key:
            agent_credentials.set_credential(provider, key)

    def _must_be_mine(self, path, name):
        """Refuse to write anywhere but this sandbox's own directory."""
        if self.dir not in Path(path).parents:
            self.close()
            raise AssertionError(
                "%s is %s, which is not inside the sandbox at %s. Nothing was "
                "written: a redirect that did not take would put a test's fake "
                "key over the real one." % (name, path, self.dir))

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()
        return False

    def close(self):
        agent_credentials.STORE_FILE = self.saved_store
        agent_config.KEY_FILE = self.saved_key_file
        agent_config.OPENROUTER_API_KEY = self.saved_config_key
        for name, value in self.saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(self.dir, ignore_errors=True)


# --- the sandbox isolates, in both directions -------------------------------

def test_a_sandboxed_key_comes_from_the_sandbox_and_not_from_the_machine():
    """The answer has to be the sandbox's, not the developer's. `source` is
    what proves it: SOURCE_STORE with STORE_FILE redirected means the key was
    read from the file the test wrote, wherever the machine keeps its own."""
    with Credentials() as store:
        assert agent_credentials.selected_provider() == "openrouter"
        assert agent_credentials.has_credential("openrouter")
        assert agent_credentials.credential("openrouter") == FAKE_KEY
        assert agent_credentials.source("openrouter") == agent_credentials.SOURCE_STORE
        assert store.dir in agent_credentials.STORE_FILE.parents


def test_an_empty_sandbox_has_no_key_however_the_machine_is_configured():
    """The direction that fails on a developer's machine if the isolation
    breaks. An environment variable outranks everything, so this plants one
    and asserts the sandbox refuses to see it -- exactly what a test about
    'TMT cannot reach a model' needs, and what it silently would not get."""
    previous = os.environ.get("OPENROUTER_API_KEY")
    os.environ["OPENROUTER_API_KEY"] = "sk-planted-by-the-machine"
    try:
        with Credentials(key=None):
            assert not agent_credentials.has_credential("openrouter")
            assert agent_credentials.credential("openrouter") == ""
            assert agent_credentials.source("openrouter") == agent_credentials.SOURCE_NONE
        # And it is put back, or the next test inherits it.
        assert os.environ["OPENROUTER_API_KEY"] == "sk-planted-by-the-machine"
    finally:
        if previous is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = previous


def test_the_legacy_key_file_is_redirected_too():
    """`.tmt_key` predates the store and is still read for OpenRouter, so a
    sandbox that redirected only the store would find the developer's key by
    the back door -- and only on the one provider every test uses."""
    with Credentials(key=None):
        assert agent_config.KEY_FILE.parent != INSTALL_DIR
        agent_config.KEY_FILE.write_text("sk-legacy\n", encoding="utf-8")
        assert agent_credentials.credential("openrouter") == "sk-legacy"
        assert agent_credentials.source("openrouter") == agent_credentials.SOURCE_LEGACY_FILE


def test_a_sandbox_that_is_not_isolating_writes_nothing():
    """The guard, and it is here because the thing it prevents happened.

    A mutation run un-redirected `STORE_FILE` to check that the isolation was
    load-bearing -- and the sandbox cheerfully wrote its fake key into this
    machine's real credential store, over the top of the working one. Nothing
    looked wrong: the store was valid, the tests failed the way a killed
    mutation should, and TMT answered HTTP 401 on its next launch.

    So the redirect is verified before anything is written, and a sandbox that
    is not isolating takes itself down instead of writing. Fail closed: what is
    being protected is somebody's credential, not a test result.
    """
    store = Credentials()
    sandboxed = agent_credentials.STORE_FILE
    try:
        store._must_be_mine(INSTALL_DIR / ".tmt_providers.json", "STORE_FILE")
    except AssertionError as error:
        assert "not inside the sandbox" in str(error), error
    else:
        store.close()
        raise AssertionError("a path outside the sandbox was accepted")
    # And it let go on the way out rather than leaving the redirect standing.
    assert agent_credentials.STORE_FILE != sandboxed
    assert agent_config.KEY_FILE.parent == INSTALL_DIR


def test_the_legacy_module_global_is_the_sandbox_key_and_not_the_machine_key():
    """`agent_config.OPENROUTER_API_KEY` is bound at import, and two things
    read it: `agent_model._headers` puts it straight into an Authorization
    header, and `agent_setup.ensure_api_key` decides from it whether to run
    first-launch setup. A sandbox that redirected the files and left the
    global alone would build a request carrying the developer's real key the
    moment a test replaced the transport -- which is exactly what the tests
    that need this sandbox do."""
    import agent_model
    with Credentials():
        assert agent_config.OPENROUTER_API_KEY == FAKE_KEY
        assert FAKE_KEY in agent_model._headers()["Authorization"]
    with Credentials(key=None):
        assert agent_config.OPENROUTER_API_KEY == ""


def test_nothing_is_written_to_the_installation_and_everything_is_put_back():
    """The rule the whole suite depends on: a test may not touch the
    installation's own state. Asserted by mtime as well as by the restored
    module globals, because 'put the path back' and 'never wrote to it' are
    two different promises."""
    real_store, real_key = agent_credentials.STORE_FILE, agent_config.KEY_FILE
    before = [(path, path.stat().st_mtime if path.exists() else None)
              for path in (real_store, real_key)]
    with Credentials():
        agent_credentials.set_credential("anthropic", "sk-ant-test")
    assert agent_credentials.STORE_FILE == real_store
    assert agent_config.KEY_FILE == real_key
    for path, stamp in before:
        assert (path.stat().st_mtime if path.exists() else None) == stamp, path


def test_a_stored_key_is_not_on_disk_in_plain_text():
    """Obfuscated, not encrypted -- which is what `agent_credentials` says it
    is. The honest claim is 'not readable at a glance', and this is the test
    that keeps it true rather than a claim in a docstring."""
    with Credentials():
        written = agent_credentials.STORE_FILE.read_text(encoding="utf-8")
        assert FAKE_KEY not in written, written
        assert "openrouter" in written, written
