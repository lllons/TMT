"""Tests for `agent_images`, the module that lets the model look at things.

Every image here is BUILT rather than committed: a PNG, a JPEG, a GIF and a
WEBP assembled byte by byte from their own specifications. Two reasons, and
both matter more than the convenience of a fixture file would.

The first is that the thing under test is a parser of file headers, and a
fixture is one example of one encoder's output -- so a test that reads
`fixture.png` proves the parser handles whatever wrote that file and nothing
else. Building them here means the JPEG's frame header can be put after two
other segments, which is the case that actually breaks a naive walk, and the
WEBP can be built in all three of its shapes.

The second is that this repository's suite must stay hermetic: a binary
fixture is a file somebody has to trust, and the bytes below are readable.

What is pinned is mostly the decisions rather than the happy path. That the
type comes from the bytes and never the extension, that an unreadable size is
absent rather than zero, that an unknown model is attempted rather than
refused, and that `parts` gives back the plain string when nothing attached
anything are all things a later edit could reverse with nothing looking wrong.
"""

import base64
import os
import shutil
import tempfile

import agent_config
import agent_images


# --- images, built from their own specifications ----------------------------


def png_bytes(width=1, height=1):
    """A PNG whose IHDR really says these dimensions.

    Only the signature and the IHDR chunk are needed: nothing here decodes
    pixels, and a file that stops after IHDR is exactly what `dimensions` has
    to cope with when a read is truncated.
    """
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = (b"IHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")
            + b"\x08\x06\x00\x00\x00")
    return header + (len(ihdr) - 4).to_bytes(4, "big") + ihdr + b"\x00" * 4


def jpeg_bytes(width=400, height=300, before=2):
    """A JPEG with `before` junk segments in front of the frame header.

    The segments before the SOF are the whole point. JPEG stores its size in a
    segment whose position depends on how many comment, quantisation and
    Huffman segments came first, so a parser that reads a fixed offset -- or
    that steps over a standalone marker as though it carried a length -- gets
    a confident wrong answer on a real file and the right one on a minimal
    fixture.
    """
    out = [b"\xff\xd8"]
    for _ in range(before):
        payload = b"\x00" * 12
        out.append(b"\xff\xfe" + (len(payload) + 2).to_bytes(2, "big") + payload)
    # A restart marker, which carries no length at all. Stepping over this one
    # by two bytes plus a length read from the next two walks into the middle
    # of the file and never finds the frame header.
    out.append(b"\xff\xd0")
    sof = (b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big")
           + b"\x03" + b"\x00" * 9)
    out.append(b"\xff\xc0" + (len(sof) + 2).to_bytes(2, "big") + sof)
    return b"".join(out)


def gif_bytes(width=7, height=5):
    return (b"GIF89a" + width.to_bytes(2, "little") + height.to_bytes(2, "little")
            + b"\x00" * 10)


def webp_lossy(width=640, height=480):
    body = (b"VP8 " + (0).to_bytes(4, "little") + b"\x00\x00\x00"
            + b"\x9d\x01\x2a" + width.to_bytes(2, "little")
            + height.to_bytes(2, "little"))
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WEBP" + body


def webp_lossless(width=13, height=17):
    bits = (width - 1) | ((height - 1) << 14)
    body = b"VP8L" + (0).to_bytes(4, "little") + b"\x2f" + bits.to_bytes(4, "little")
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WEBP" + body


def webp_extended(width=100, height=200):
    body = (b"VP8X" + (10).to_bytes(4, "little") + b"\x00" * 4
            + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little"))
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WEBP" + body


class Workspace:
    """A throwaway directory as the workspace root.

    close() restores agent_config.ROOT_DIR and must run in a finally block: a
    leaked root points every later test at a directory that has been deleted.
    """

    def __init__(self):
        self.previous = agent_config.ROOT_DIR
        self.root = tempfile.mkdtemp(prefix="tmt_images_")
        agent_config.set_workspace_root(self.root)

    def write(self, name, data):
        path = os.path.join(self.root, name)
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(path, "wb") as handle:
            handle.write(data)
        return name

    def close(self):
        agent_config.set_workspace_root(str(self.previous))
        shutil.rmtree(self.root, ignore_errors=True)


# --- what a file actually is ------------------------------------------------


def test_every_format_is_recognised_from_its_own_first_bytes():
    assert agent_images.kind(png_bytes()) == "image/png"
    assert agent_images.kind(jpeg_bytes()) == "image/jpeg"
    assert agent_images.kind(gif_bytes()) == "image/gif"
    assert agent_images.kind(webp_lossy()) == "image/webp"
    assert agent_images.kind(b"GIF87a" + b"\x00" * 10) == "image/gif"


def test_anything_that_is_not_one_of_the_four_is_not_an_image():
    assert agent_images.kind(b"") == ""
    assert agent_images.kind(None) == ""
    assert agent_images.kind(b"hello, this is text") == ""
    assert agent_images.kind(b"%PDF-1.7") == ""
    # RIFF without WEBP after it is a WAV, and it must not be offered as an
    # image: the provider would reject the request and the model would be
    # looking for a fault in a file that is exactly what it claims to be.
    assert agent_images.kind(b"RIFF" + b"\x00" * 4 + b"WAVE") == ""


def test_the_extension_is_never_asked_and_the_bytes_always_are():
    """A .png holding a JPEG is a JPEG.

    The failure this prevents is the expensive one: every provider is told the
    media type in the request and rejects one that disagrees with the payload,
    so a mislabelled image fails the WHOLE request -- not the image -- with an
    error about the request body that the model cannot connect to the file it
    named.
    """
    space = Workspace()
    try:
        space.write("screenshot.png", jpeg_bytes())
        image = agent_images.load("screenshot.png")
        assert image.media_type == "image/jpeg"
        assert image.name == "screenshot.png"
    finally:
        space.close()


# --- dimensions -------------------------------------------------------------


def test_dimensions_are_read_from_each_format():
    assert agent_images.dimensions(png_bytes(1440, 900)) == (1440, 900)
    assert agent_images.dimensions(gif_bytes(7, 5)) == (7, 5)
    assert agent_images.dimensions(webp_lossy(640, 480)) == (640, 480)
    assert agent_images.dimensions(webp_lossless(13, 17)) == (13, 17)
    assert agent_images.dimensions(webp_extended(100, 200)) == (100, 200)


def test_a_jpeg_size_is_found_past_the_segments_in_front_of_it():
    """The case a fixed offset gets wrong and a minimal fixture never shows."""
    for before in (0, 1, 5):
        assert agent_images.dimensions(jpeg_bytes(400, 300, before)) == (400, 300)


def test_a_size_that_cannot_be_read_is_absent_rather_than_zero():
    """(0, 0) means "not known", and every caller has to treat it as absence.

    A truncated header, a variant this does not know and a file that is not an
    image at all all answer the same way, and `label` leaves the size out
    rather than printing `0x0` -- which would be a measurement nobody made.
    """
    assert agent_images.dimensions(b"\x89PNG\r\n\x1a\n") == (0, 0)
    assert agent_images.dimensions(b"\xff\xd8\xff") == (0, 0)
    assert agent_images.dimensions(b"nonsense") == (0, 0)
    assert agent_images.dimensions(None) == (0, 0)
    unmeasured = agent_images.Image("x.png", "image/png", "QQ==", 3)
    assert "0x0" not in unmeasured.label()
    assert unmeasured.label() == "x.png (PNG, 3 bytes)"


def test_a_jpeg_whose_segments_stop_making_sense_gives_up_rather_than_looping():
    """A length of zero would step backwards forever. It stops instead."""
    broken = b"\xff\xd8" + b"\xff\xe0" + (0).to_bytes(2, "big") + b"\x00" * 20
    assert agent_images.dimensions(broken) == (0, 0)


# --- loading ----------------------------------------------------------------


def test_a_loaded_image_carries_its_bytes_encoded_and_its_size_raw():
    space = Workspace()
    try:
        data = png_bytes(30, 20)
        space.write("a.png", data)
        image = agent_images.load("a.png")
        assert base64.b64decode(image.data) == data
        assert image.size == len(data)
        assert (image.width, image.height) == (30, 20)
        assert image.label() == "a.png (PNG, 30x20, %s)" % agent_images.human_size(len(data))
    finally:
        space.close()


def test_every_refusal_says_what_was_wrong_and_what_would_be_different():
    """`_run_tool` turns these into the only sentence the model can act on."""
    space = Workspace()
    try:
        space.write("notes.txt", b"just words")
        space.write("empty.png", b"")
        os.makedirs(os.path.join(space.root, "pictures"))
        for path, expected in (("missing.png", "not found"),
                               ("notes.txt", "PNG, JPEG, GIF and WEBP"),
                               ("empty.png", "is empty"),
                               ("pictures", "folder, not an image")):
            try:
                agent_images.load(path)
            except ValueError as error:
                assert expected in str(error), (path, str(error))
            else:
                raise AssertionError("%s was not refused" % path)
    finally:
        space.close()


def test_an_image_over_the_ceiling_is_refused_with_both_numbers_in_it():
    """TMT cannot resize, so the refusal has to be actionable outside TMT.

    It names the size AND the ceiling, because "too large" without either is a
    message whose only possible response is to try again and fail again.
    """
    space = Workspace()
    try:
        oversized = png_bytes(1, 1) + b"\x00" * (agent_images.MAX_IMAGE_BYTES + 1)
        space.write("huge.png", oversized)
        try:
            agent_images.load("huge.png")
        except ValueError as error:
            said = str(error)
            assert agent_images.human_size(len(oversized)) in said
            assert agent_images.human_size(agent_images.MAX_IMAGE_BYTES) in said
            assert "resize" in said
        else:
            raise AssertionError("an oversized image was not refused")
    finally:
        space.close()


def test_a_path_outside_the_workspace_never_gets_as_far_as_being_read():
    """The sandbox is `safe_path`'s, borrowed rather than repeated."""
    space = Workspace()
    try:
        try:
            agent_images.load("../../secret.png")
        except ValueError as error:
            assert "unsafe path" in str(error).lower()
        else:
            raise AssertionError("a path outside the workspace was accepted")
    finally:
        space.close()


def test_a_loaded_image_cannot_be_rewritten_after_it_has_been_checked():
    """Sealed, and sealed against the private slots too.

    `agent_delegation.DelegationConstraints` was sealed after exactly this was
    found there: read-only properties leave the slot under each one
    assignable, and "immutable except for the four names beside the four
    immutable ones" is not a guarantee. What is being protected here is that
    the media type checked against the bytes is the media type sent.
    """
    image = agent_images.Image("a.png", "image/png", "QQ==", 3, 1, 1)
    for name in ("media_type", "_media_type", "data", "_data", "name"):
        try:
            setattr(image, name, "image/jpeg")
        except RuntimeError:
            continue
        raise AssertionError("%s could be reassigned" % name)
    try:
        del image._data
    except RuntimeError:
        pass
    else:
        raise AssertionError("a slot could be deleted")
    assert image.media_type == "image/png"


# --- carrying it ------------------------------------------------------------


def test_with_no_images_the_content_is_the_string_it_always_was():
    """The property the rest of the program rests on.

    Every message-building site in TMT calls `parts` now, and a turn that
    looked at no image has to produce byte-for-byte the request it produced
    before this module existed. Asserted with `is`, not with equality.
    """
    text = "Result: 12 lines written"
    assert agent_images.parts(text, []) is text
    assert agent_images.parts(text, None) is text
    assert agent_images.parts(text, ["not an image"]) is text


def test_an_image_makes_the_content_a_list_with_the_text_first():
    image = agent_images.Image("a.png", "image/png", "QQ==", 3, 1, 1)
    content = agent_images.parts("look", [image])
    assert agent_images.is_parts(content)
    assert content[0] == {"type": "text", "text": "look"}
    assert content[1]["type"] == "image"
    assert content[1]["media_type"] == "image/png"
    assert content[1]["source"] == "a.png"


def test_what_is_attached_to_an_action_comes_back_and_nothing_else_does():
    """A model that planted the key in its own JSON gets nothing by it."""
    image = agent_images.Image("a.png", "image/png", "QQ==", 3, 1, 1)
    obj = {"action": "view_image", "path": "a.png"}
    agent_images.attach(obj, [image])
    assert agent_images.attached(obj) == [image]

    forged = {"action": "read_file", agent_images.ATTACHED_KEY: [
        {"type": "image", "media_type": "image/png", "data": "QQ=="}]}
    assert agent_images.attached(forged) == []
    assert agent_images.attached("not a dict") == []
    assert agent_images.attached({}) == []


def test_text_of_reads_the_words_and_never_the_payload():
    """Anything asking a message for its text has to go through this.

    `str()` on the list form is the repr of a base64 blob, which is how a
    screenshot becomes a million characters in a trim, a record or a test.
    """
    image = agent_images.Image("shot.png", "image/png", "QUJDRA==", 4, 1, 1)
    content = agent_images.parts("here", [image])
    said = agent_images.text_of(content)
    assert "here" in said
    assert "[image: shot.png]" in said
    assert "QUJDRA==" not in said
    assert agent_images.text_of("plain") == "plain"
    assert agent_images.text_of(None) == ""


def test_an_image_is_measured_by_its_pixels_and_not_by_its_encoded_length():
    """The estimate that keeps the corner meter honest.

    A megabyte of base64 counted as characters is ~350k tokens, which would
    report a session as having filled a window it has barely touched.
    """
    tokens = lambda text: (len(text or "") + 3) // 4
    big = agent_images.Image("a.png", "image/png", "Q" * 400000, 300000, 800, 600)
    content = agent_images.parts("hi", [big])
    measured = agent_images.measure(content, tokens)
    assert measured < 1500, measured
    assert measured > 100, measured
    # And a plain string is measured exactly as it always was.
    assert agent_images.measure("hello", tokens) == tokens("hello")


# --- keeping it bounded -----------------------------------------------------


def test_only_the_most_recent_images_survive_a_prune():
    image = agent_images.Image("a.png", "image/png", "QQ==", 3, 1, 1)
    messages = [{"role": "user", "content": agent_images.parts("one", [image])},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": agent_images.parts("two", [image])},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": agent_images.parts("three", [image])}]
    pruned = agent_images.prune(messages, keep=2)
    assert not agent_images.is_parts(pruned[0]["content"])
    assert agent_images.is_parts(pruned[2]["content"])
    assert agent_images.is_parts(pruned[4]["content"])


def test_a_dropped_image_says_so_and_names_the_verb_that_brings_it_back():
    """Silence here would leave the model reasoning about what it cannot see."""
    image = agent_images.Image("shot.png", "image/png", "QQ==", 3, 1, 1)
    messages = [{"role": "user", "content": agent_images.parts("look", [image])},
                {"role": "user", "content": "later"}]
    said = agent_images.prune(messages, keep=0)[0]["content"]
    assert "look" in said
    assert "shot.png" in said
    assert "view_image" in said
    assert "QQ==" not in said


def test_a_prune_returns_a_new_list_and_rewrites_nothing_in_place():
    """A caller still holding the old list must not have it changed under it."""
    image = agent_images.Image("a.png", "image/png", "QQ==", 3, 1, 1)
    original = {"role": "user", "content": agent_images.parts("one", [image])}
    messages = [original, {"role": "user", "content": "two"}]
    pruned = agent_images.prune(messages, keep=0)
    assert pruned is not messages
    assert agent_images.is_parts(original["content"])
    assert pruned[1] is messages[1]


def test_a_conversation_with_no_images_comes_back_unchanged():
    messages = [{"role": "user", "content": "one"},
                {"role": "assistant", "content": "two"}]
    pruned = agent_images.prune(messages)
    assert pruned == messages
    assert all(a is b for a, b in zip(pruned, messages))


# --- whether the model can be sent one --------------------------------------


def test_the_answer_is_three_valued_and_unknown_is_the_common_one():
    """None is not collapsed into either, and the reason is asymmetric.

    Wrong towards None costs one request that comes back with a clear reason.
    Wrong towards False silently removes the feature for every model the table
    has not been told about -- which is most of OpenRouter, the default.
    """
    assert agent_images.supports_images("anthropic", "claude-sonnet-5") is True
    assert agent_images.supports_images("anthropic", "claude-2.1") is False
    assert agent_images.supports_images("gemini", "gemini-2.5-flash") is True
    assert agent_images.supports_images("openai", "gpt-4o") is True
    assert agent_images.supports_images("openai", "gpt-3.5-turbo") is False
    assert agent_images.supports_images("openrouter", "minimax/minimax-m3:free") is None


def test_a_vendor_prefixed_id_gets_the_same_answer_as_the_bare_one():
    """OpenRouter hosts everyone's models under `vendor/model`."""
    assert agent_images.supports_images("openrouter", "anthropic/claude-opus-5") is True
    assert agent_images.supports_images("openrouter", "openai/gpt-4o") is True
    assert agent_images.supports_images("openrouter", "mistralai/pixtral-12b") is True
    assert agent_images.supports_images("openrouter", "qwen/qwen2.5-vl-7b:free") is True


def test_only_a_definite_no_produces_a_refusal():
    assert agent_images.unavailable_reason("openai", "gpt-3.5-turbo")
    assert agent_images.unavailable_reason("anthropic", "claude-sonnet-5") == ""
    assert agent_images.unavailable_reason("openrouter", "minimax/minimax-m3:free") == ""


def test_the_refusal_tells_the_model_what_to_do_rather_than_what_went_wrong():
    """It is read by a model that cannot change the setting itself."""
    said = agent_images.UNSUPPORTED % ("shot.png", "gpt-3.5-turbo")
    assert "shot.png" in said
    assert "gpt-3.5-turbo" in said
    assert "Settings" in said
    assert "carry on" in said


def test_human_size_says_bytes_then_kb_then_mb():
    assert agent_images.human_size(0) == "0 bytes"
    assert agent_images.human_size(900) == "900 bytes"
    assert agent_images.human_size(2048) == "2 KB"
    assert agent_images.human_size(3 * 1024 * 1024) == "3.0 MB"
