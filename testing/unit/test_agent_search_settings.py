"""Tests for the Web Search entry in Settings.

Two things are being protected. The first is that a key can be PASTED into it:
that is the whole reason this screen exists rather than a line in the README,
and the paste path it uses is the one that was silently dropping a key until
`normalize_text_key` was fixed. The second is that the key never reaches the
screen, the transcript, or any file outside the installation directory.

Nothing here touches the network or the developer's own store: `SEARCH_FILE`
is redirected to a temporary path, `check_key` is replaced, and the four search
environment variables are cleared for the duration -- so the result does not
depend on whether the machine running the suite happens to have a search key.
"""

import io
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import agent_config

ESCAPE = re.compile("\033\\[[0-9;?]*[ -/]*[@-~]")

KEY = "tvly-dev-0123456789abcdef0123"


def menu():
    import agent_menu
    return agent_menu


def web():
    import agent_web
    return agent_web


def visible(text):
    return ESCAPE.sub("", text)


class Terminal(io.StringIO):
    """A buffer that claims to be a terminal, so the region actually paints."""

    def isatty(self):
        return True

    @property
    def encoding(self):
        return "utf-8"


class Scripted:
    def __init__(self, reads):
        self.reads = list(reads)

    def __call__(self):
        if not self.reads:
            raise IndexError("the script ran out")
        return self.reads.pop(0)


class Store:
    """A redirected search store, with the environment out of the way.

    An environment variable outranks the file, so a developer with
    TAVILY_API_KEY exported would otherwise see these tests assert against
    their own key rather than the one the test wrote.
    """

    def __init__(self, accepted=True):
        self.accepted = accepted

    def __enter__(self):
        module = web()
        self.directory = Path(tempfile.mkdtemp())
        self.saved_file = module.SEARCH_FILE
        self.saved_check = module.check_key
        self.saved_env = {name: os.environ.pop(name, None)
                          for name in module.KEY_ENV.values()}
        module.SEARCH_FILE = self.directory / ".tmt_search.json"
        module.check_key = self._check
        self.checked = []
        return self

    def _check(self, backend, key):
        self.checked.append((backend, key))
        label = web().BACKEND_LABELS[backend]
        if self.accepted:
            return True, "Saved. %s accepted it." % label
        return False, "Saved, but %s rejected the API key (HTTP 401)." % label

    def path(self):
        return web().SEARCH_FILE

    def __exit__(self, *_):
        module = web()
        module.SEARCH_FILE = self.saved_file
        module.check_key = self.saved_check
        for name, value in self.saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        # The directory goes too. Sixteen of these run per suite pass, each
        # leaving behind a file with a test key in it -- the same leak this
        # project already records for `test_agent_setup.py`, and there is no
        # reason to add a seventeenth site of it.
        shutil.rmtree(self.directory, ignore_errors=True)
        return False


# --- the store --------------------------------------------------------------

def test_a_saved_key_is_readable_back_and_becomes_the_active_backend():
    """Saving is the whole point of the screen, and a key that is stored
    without becoming the one in use would leave a user who has just pasted one
    looking at a search that is still off."""
    with Store():
        module = web()
        assert not module.is_configured()
        module.save_credential("brave", KEY)
        assert module.credential("brave") == KEY
        assert module.active_backend() == "brave"
        assert module.is_configured()


def test_saving_one_backend_leaves_another_backend_s_key_alone():
    """The file holds a key per backend and the screen writes one at a time.
    A save that dropped the others would silently un-configure a backend the
    user had set up earlier."""
    with Store():
        module = web()
        module.save_credential("brave", "brave-" + KEY)
        module.save_credential("tavily", "tavily-" + KEY)
        assert module.credential("brave") == "brave-" + KEY
        assert module.credential("tavily") == "tavily-" + KEY
        assert set(module.configured_backends()) == {"brave", "tavily"}


def test_the_backend_just_given_a_key_is_the_one_that_gets_used():
    """The order matters and only one ordering exposes it.

    `active_backend` falls back to the first CONFIGURED backend when nothing
    is chosen, and that fallback walks BACKENDS order -- so saving Brave alone,
    or Tavily after Brave, gives the right answer whether or not the save
    records a choice. Saving Tavily FIRST and Brave second is the case that
    tells them apart, and it is the real one: a user replacing a Tavily key
    that has stopped working with a Brave key would otherwise carry on
    searching through the dead one.
    """
    with Store():
        module = web()
        module.save_credential("tavily", "tavily-" + KEY)
        assert module.active_backend() == "tavily"
        module.save_credential("brave", "brave-" + KEY)
        assert module.active_backend() == "brave", (
            "the key that was just pasted is not the one being used")


def test_the_store_refuses_a_key_it_cannot_mean_anything_by():
    """It RAISES rather than reporting quietly, which is the opposite of every
    read in that module: a key the user just pasted that silently did not
    persist would show as set now and be gone next launch."""
    with Store():
        module = web()
        for bad in ("", "   ", None):
            try:
                module.save_credential("brave", bad)
                raise AssertionError("stored %r" % (bad,))
            except ValueError:
                pass
        try:
            module.save_credential("not-a-backend", KEY)
            raise AssertionError("stored a key for a backend TMT has no code for")
        except ValueError:
            pass


def test_the_search_store_lives_beside_tmt_and_never_in_the_workspace():
    """A credential belongs to the INSTALLATION, so TMT is the same agent in
    every directory. This is the rule `test_installation_state_does_not_follow_the_workspace`
    keeps for the other four; the search key is the fifth."""
    path = Path(web().SEARCH_FILE)
    assert path.parent == Path(agent_config.INSTALL_DIR), path
    assert path.name == ".tmt_search.json"


def test_the_mask_shows_too_little_to_reconstruct_a_key_and_says_where_it_came_from():
    """A mask that showed the key would defeat the screen that refuses to draw
    it, and one that did not name the source would leave a user whose
    environment variable outranks the file with no way to find that out."""
    with Store():
        module = web()
        module.save_credential("brave", KEY)
        shown = module.masked("brave")
        assert KEY not in shown
        # The body is the two ends of the key with the middle gone; the
        # SOURCE follows it, so the mask does not end with the key.
        assert shown.startswith(KEY[:4]), shown
        assert KEY[-4:] in shown, shown
        assert ".tmt_search.json" in shown
        # Enough of the key is missing that it cannot be reconstructed.
        assert len(shown.split("  from ")[0]) < len(KEY), shown
        os.environ[module.KEY_ENV["brave"]] = "from-the-environment-9999"
        try:
            assert module.KEY_ENV["brave"] in module.masked("brave")
        finally:
            os.environ.pop(module.KEY_ENV["brave"], None)
        assert module.masked("tavily") == "not set"


# --- the screens ------------------------------------------------------------

def test_the_settings_screen_names_the_web_search_row_and_its_value():
    """The row summarises a screen you would otherwise have to go into, which
    is what the other three fields in that block do."""
    with Store():
        frame = visible("\n".join(menu().render_settings_menu_frame(
            0, Terminal(), size=(90, 40))))
        assert "Web Search" in frame
        assert "not set" in frame
        web().save_credential("tavily", KEY)
        frame = visible("\n".join(menu().render_settings_menu_frame(
            0, Terminal(), size=(90, 40))))
        assert "Tavily" in frame
        assert KEY not in frame


def test_the_settings_summary_leaves_a_gap_between_every_name_and_its_value():
    """`Web Search` is exactly as wide as the name column used to be, so the
    row drew as `Web Searchnot set` -- a label and a value with nothing
    between them. The width is derived from the labels now, so the next one
    added cannot bring it back."""
    with Store():
        frame = menu().render_settings_menu_frame(0, Terminal(), size=(90, 40))
        for row in [visible(line) for line in frame]:
            for name in menu()._SETTINGS_FIELD_NAMES:
                if row.strip().startswith(name):
                    rest = row.strip()[len(name):]
                    assert rest.startswith(" "), row


def test_the_backend_chooser_says_which_have_keys_and_where_to_get_one():
    """A screen that says "paste a key" without saying where one is obtained
    has asked for something the user has no way to get."""
    with Store():
        web().save_credential("brave", KEY)
        frame = visible("\n".join(menu().render_search_frame(
            0, "brave", Terminal(), size=(90, 40))))
        for backend in web().BACKENDS:
            assert web().BACKEND_LABELS[backend] in frame, backend
        assert "key set" in frame and "no key" in frame
        assert "(active)" in frame
        assert web().key_url("tavily") in frame     # the row the cursor is on
        assert KEY not in frame


def test_the_key_screen_draws_a_mask_and_never_the_key():
    """The frame is built from HOW MUCH was typed and has no access to what
    was typed, so no drawing path can put a key on screen."""
    with Store():
        frame = visible("\n".join(menu().render_search_key_frame(
            "tavily", 24, "", Terminal(), size=(90, 40))))
        assert menu().MASK_CHAR * 24 in frame
        blank = visible("\n".join(menu().render_search_key_frame(
            "tavily", 0, "", Terminal(), size=(90, 40))))
        assert "paste or type it" in blank


# --- the flow, driven -------------------------------------------------------

def test_a_key_pasted_with_the_newline_a_copy_brings_is_stored_exactly():
    """The defect this whole screen would otherwise inherit. Selecting a key
    in a browser takes the newline at the end of it with you, and until
    `normalize_text_key` was fixed that dropped the entire paste in silence.

    Asserted on what reached DISK, not on what the stubbed `check_key` was
    handed: a key stored with a line break on the end is one every request
    sends and every backend rejects, and only the store can answer that. The
    first version of this test read `store.checked` and said in its docstring
    that it read the store -- which is the one thing a test must never do.
    """
    for tail in ("", "\n", "\r", "\r\n"):
        with Store() as store:
            out = Terminal()
            chosen = menu().search_setup(
                stream=out, region=menu().LiveRegion(out),
                key_reader=Scripted(["enter", KEY + tail, "enter", "enter"]))
            frames = visible(out.getvalue())
            assert chosen == "tavily", tail
            # Read back through the module while the redirect is still the one
            # the flow wrote to -- outside the `with`, SEARCH_FILE is restored
            # and this would be asking the developer's own store.
            assert web()._read_search_file()["keys"]["tavily"] == KEY, tail
            assert web().credential("tavily") == KEY, tail
            assert KEY not in frames, tail
            assert "accepted it" in frames, tail
            assert store.checked == [("tavily", KEY)], (tail, store.checked)


def test_the_whole_flow_leaves_the_key_in_the_store_and_off_the_screen():
    """End to end through the real screens: choose a backend, paste, save."""
    with Store() as store:
        out = Terminal()
        chosen = menu().search_setup(
            stream=out, region=menu().LiveRegion(out),
            key_reader=Scripted(["down", "enter", KEY + "\r\n", "enter", "enter"]))
        assert chosen == "brave"
        assert web().credential("brave") == KEY
        assert web().active_backend() == "brave"
        assert KEY not in visible(out.getvalue())
        assert store.checked == [("brave", KEY)]


def test_a_paste_of_more_than_one_line_says_so_and_stores_nothing():
    """Half a key stored is worse than no key stored, and silence is worse
    than both -- that is the defect this message exists to avoid."""
    with Store() as store:
        out = Terminal()
        menu().search_setup(
            stream=out, region=menu().LiveRegion(out),
            key_reader=Scripted(["enter", "KEY=%s\nexport OTHER=1" % KEY,
                                 "esc", "esc"]))
        frames = visible(out.getvalue())
        assert "That paste is 2 lines" in frames, frames[-400:]
        assert not web().is_configured()
        assert store.checked == []


def test_escape_at_the_key_screen_saves_nothing_and_returns_to_the_list():
    """A user with no key to hand is never trapped, and never has one written
    on their behalf."""
    with Store() as store:
        out = Terminal()
        chosen = menu().search_setup(
            stream=out, region=menu().LiveRegion(out),
            key_reader=Scripted(["enter", "esc", "esc"]))
        assert chosen is None
        assert not web().is_configured()
        assert store.checked == []


def test_enter_with_nothing_typed_is_a_message_rather_than_a_save():
    """Every other way this field can be given something it cannot use answers
    in words; an empty Enter must too."""
    with Store() as store:
        out = Terminal()
        menu().search_setup(
            stream=out, region=menu().LiveRegion(out),
            key_reader=Scripted(["enter", "enter", "esc", "esc"]))
        assert "Nothing was typed" in visible(out.getvalue())
        assert store.checked == []


def test_a_backend_that_rejects_the_key_still_stores_it_and_says_what_happened():
    """TMT does not decide a credential is invalid on the backend's behalf --
    the user may be looking at a quota problem rather than a typo, and a key
    thrown away on a 401 would have to be pasted again to find out."""
    with Store(accepted=False):
        out = Terminal()
        chosen = menu().search_setup(
            stream=out, region=menu().LiveRegion(out),
            key_reader=Scripted(["enter", KEY, "enter", "enter"]))
        assert chosen == "tavily"
        assert web().credential("tavily") == KEY
        assert "rejected the API key" in visible(out.getvalue())


def test_a_short_key_is_masked_rather_than_shown_whole():
    """`masked` had two tiers where `agent_credentials.masked` has three, and
    the missing one returned a short value UNCHANGED -- so a truncated paste,
    a typo, or an environment variable holding something short drew itself in
    full on two screens, under a docstring promising it could not be
    reconstructed."""
    with Store():
        module = web()
        for short in ("abc", "12345678"):
            module.save_credential("brave", short)
            shown = module.masked("brave")
            assert short not in shown, (short, shown)
        module.save_credential("brave", "123456789012")      # 12: middle tier
        shown = module.masked("brave")
        assert "123456789012" not in shown and shown.startswith("..."), shown


def test_a_damaged_store_is_kept_rather_than_written_over():
    """A read treats damaged bytes as "nothing configured", which is right.
    Combined with a whole-document write it silently destroyed every other
    key in the file. Nothing can repair it; what it must not do is be the
    thing that throws it away."""
    with Store():
        module = web()
        module.save_credential("tavily", "tavily-" + KEY)
        module.save_credential("serper", "serper-" + KEY)
        # A stray byte APPENDED, which is what damage actually looks like --
        # a truncated write, a crash, an editor. Replacing the whole file with
        # garbage would destroy the keys before this code ever saw it, and an
        # earlier version of this test did exactly that and then asserted they
        # had been preserved.
        path = Path(module.SEARCH_FILE)
        path.write_text(path.read_text(encoding="utf-8") + "}", encoding="utf-8")
        assert module.configured_backends() == ()      # the read's honest answer
        module.save_credential("brave", "brave-" + KEY)
        kept = Path(str(module.SEARCH_FILE) + ".damaged")
        assert kept.exists(), "the damaged store was overwritten and lost"
        text = kept.read_text(encoding="utf-8")
        assert "tavily-" in text and "serper-" in text, text[:200]
        # And the new key really did land in a fresh, readable store.
        assert module.credential("brave") == "brave-" + KEY


def test_choosing_a_backend_that_already_has_a_key_switches_to_it():
    """The chooser draws "(active)", moves a cursor and accepts Enter, so it
    reads as a chooser. It was not one: the backend was only ever recorded as
    a side effect of saving a key, so Enter then Esc changed nothing and the
    only way to switch was to re-type a key already given."""
    with Store():
        module = web()
        module.save_credential("tavily", "tavily-" + KEY)
        module.save_credential("brave", "brave-" + KEY)
        assert module.active_backend() == "brave"
        out = Terminal()
        # Up to Tavily, Enter to choose it, Esc rather than re-pasting.
        chosen = menu().search_setup(
            stream=out, region=menu().LiveRegion(out),
            key_reader=Scripted(["up", "enter", "esc"]))
        assert chosen == "tavily", chosen
        assert module.active_backend() == "tavily", (
            "choosing a configured backend did not switch to it")
        assert module.credential("brave") == "brave-" + KEY   # and kept the other


def test_every_row_of_both_new_frames_fits_the_terminal_it_was_drawn_for():
    """Two rows of the chooser were built painted and never fitted, so they
    overflowed at 40 columns as well as 30 -- and `_fit_height` cannot see a
    wrap, so the region's row arithmetic was off by up to two.

    The footer is excluded, as `test_agent_launch`'s width test excludes it:
    `_footer` is the one row no settings screen fits, it has always been that
    way, and giving it a width parameter touches every menu screen."""
    with Store():
        web().save_credential("brave", KEY)
        for columns in (30, 40, 60, 80):
            frames = (
                ("search", menu().render_search_frame(0, "brave", Terminal(),
                                                      size=(columns, 40))),
                ("key", menu().render_search_key_frame("brave", 6, "", Terminal(),
                                                       size=(columns, 40))),
                ("settings", menu().render_settings_menu_frame(0, Terminal(),
                                                               size=(columns, 40))),
            )
            for name, frame in frames:
                rows = [visible(row) for row in frame if visible(row).strip()]
                for row in rows[:-1]:
                    assert menu().display_width(row) <= columns - 1, (
                        name, columns, menu().display_width(row), row)


def test_the_screens_are_skipped_rather_than_hung_when_there_is_no_terminal():
    """Any doubt about raw keys means no: a wrong yes hangs the process on a
    read nobody will answer, which is why every screen in this module checks
    first."""
    plain = io.StringIO()          # not a terminal
    assert menu().search_backend_screen(stream=plain) is None
    assert menu().search_key_screen("tavily", stream=plain) is None
    assert menu().search_setup(stream=plain) is None
