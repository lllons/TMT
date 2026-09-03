"""Images the model can look at, and whether the model can look at all.

TMT read text and nothing else. `read_file` is `read_text(encoding="utf-8")`,
so a screenshot, a diagram, a mockup or a photograph of a terminal was not a
file TMT could open -- it was a UnicodeDecodeError. That is the one input a
coding agent cannot reason its way around: a user who can SEE that the box is
drawn in the wrong place has no way to show it.

This module is the whole of what changed. It has four jobs and they are
deliberately separate:

    LOADING    `load()` reads a file from the workspace, decides what it
               actually is from its own first bytes, measures it, and refuses
               it in words when it cannot be sent. It never guesses a type
               from an extension: a `.png` holding a JPEG is rejected by every
               provider here, and the extension is the model's claim while the
               magic number is the file's.

    CARRYING   `parts()` builds TMT's own provider-independent content shape,
               and `attach()`/`attached()` carry loaded images from an action
               handler to whichever loop is assembling the next request. The
               four adapters in `agent_providers` map the neutral shape onto
               their own; nothing outside this module and that one knows what
               an image looks like on the wire.

    BUDGETING  An image is not sent once. The API is stateless, so everything
               in `messages` goes again on every step of the turn -- a 3 MB
               screenshot read at step two rides along on all thirty-three
               steps after it. `prune()` is what stops that, and it says out
               loud when it has dropped one rather than quietly shrinking the
               request.

    ASKING     `supports_images()` answers whether THIS model can be sent one,
               and answers True, False or None. None is the common case and is
               not collapsed into either: a curated free model on OpenRouter
               may or may not take images and TMT cannot know without asking
               the provider, so an unknown model is attempted and its failure
               is explained rather than refused on a guess.

Nothing here runs a process, opens a socket or writes a file. It reads one
file through `agent_file_ops.safe_path`, which is what keeps a path outside
the workspace out of a request.
"""

import base64

# The four every provider TMT speaks to accepts. Gemini also takes HEIC and
# HEIF and OpenAI does not, so they are left out: a format that works on one
# provider and fails on another is a feature that breaks when the user changes
# a setting in Settings, which is the worst shape a capability can have.
MEDIA_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")

# The raw file, before base64 makes it a third larger again.
#
# A JUDGEMENT, and a tighter one than any provider's own limit, for a reason
# that is TMT's rather than theirs: this rides in `messages`, the API is
# stateless, and a turn is up to sixty rounds. At 3 MB a single image is
# ~4 MB of base64 on every request of the turn it was read in, until `prune`
# takes it out. Two of them would be most of a small model's window.
#
# The ceiling cannot be worked around by resizing, because resizing an image
# needs a decoder and TMT takes no dependencies. So an image over the ceiling
# is refused with its size and the ceiling named, which is a thing the user
# can act on with any image editor.
MAX_IMAGE_BYTES = 3_000_000

# How many messages back an image survives. Two: the result the image arrived
# in, and one more, so a model that looks at a screenshot and then runs a grep
# can still see it while it reads the grep's output. Older ones become a line
# of text saying what was there, which is the honest form of dropping it --
# a request that silently lost an image would have the model reasoning about
# something it can no longer see.
IMAGE_KEEP = 2

DROPPED = "[image removed from this request to save space: %s. Use view_image again if you still need it.]"

# What a provider that cannot carry images gets instead. Unreachable with the
# four adapters that exist -- all four take images -- and here because the
# alternative, dropping the part silently, would make a fifth adapter's
# missing implementation look like a model that cannot describe a picture.
NOT_CARRIED = "[an image was attached here, but this provider cannot carry images]"

# The first bytes each format is required to start with. Read in this order;
# WEBP is checked as two ranges because its magic is split by a length field.
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_GIF = (b"GIF87a", b"GIF89a")
_RIFF = b"RIFF"
_WEBP = b"WEBP"

# Enough for every signature above and for every dimension header. Read rather
# than the whole file, so `kind()` can answer about a 40 MB file without
# loading it.
SNIFF_BYTES = 64


def kind(data):
    """The media type these bytes really are, or "" for anything else.

    The file's own claim about itself, never the extension's. Every provider
    here is told the media type in the request and rejects one that does not
    match the bytes, so a `.png` that a screenshot tool actually wrote as a
    JPEG has to be named correctly or the whole request fails on something the
    model cannot see and cannot fix.
    """
    data = bytes(data or b"")
    if data.startswith(_PNG):
        return "image/png"
    if data.startswith(_JPEG):
        return "image/jpeg"
    if data.startswith(_GIF):
        return "image/gif"
    if data.startswith(_RIFF) and data[8:12] == _WEBP:
        return "image/webp"
    return ""


def _png_size(data):
    """Width and height from the IHDR chunk, which PNG requires to be first."""
    if len(data) < 24 or data[12:16] != b"IHDR":
        return 0, 0
    return (int.from_bytes(data[16:20], "big"),
            int.from_bytes(data[20:24], "big"))


def _gif_size(data):
    """Width and height from the logical screen descriptor. Little endian."""
    if len(data) < 10:
        return 0, 0
    return (int.from_bytes(data[6:8], "little"),
            int.from_bytes(data[8:10], "little"))


# The frame-header markers that carry a size. C4, C8 and CC are in the same
# numeric range and are a Huffman table, an extension and an arithmetic-coding
# table -- reading a size out of one of those gives a confident wrong answer.
_JPEG_SOF = tuple(m for m in range(0xC0, 0xD0) if m not in (0xC4, 0xC8, 0xCC))
# Markers that stand alone: they carry no length field, so stepping over them
# by a length read from the next two bytes walks into the middle of the file.
_JPEG_STANDALONE = (0x01, 0xD8, 0xD9) + tuple(range(0xD0, 0xD8))


def _jpeg_size(data):
    """Width and height from the first frame header.

    JPEG is a walk rather than a lookup: the size lives in a segment whose
    position depends on how many comment, quantisation and Huffman segments
    came before it. The loop stops at the first frame header and gives up
    rather than guessing if the segment structure stops making sense.
    """
    index, end = 2, len(data)
    while index + 3 < end:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker == 0xFF:          # fill byte, and legal between segments
            index += 1
            continue
        if marker in _JPEG_STANDALONE:
            index += 2
            continue
        if index + 4 > end:
            break
        length = int.from_bytes(data[index + 2:index + 4], "big")
        if marker in _JPEG_SOF:
            if index + 9 > end:
                break
            return (int.from_bytes(data[index + 7:index + 9], "big"),
                    int.from_bytes(data[index + 5:index + 7], "big"))
        if length < 2:
            break
        index += 2 + length
    return 0, 0


def _webp_size(data):
    """Width and height from whichever of the three WEBP shapes this is.

    Lossy, lossless and extended each store the size differently and in a
    different place, and all three are ordinary `.webp` files.
    """
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                int.from_bytes(data[28:30], "little") & 0x3FFF)
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X" and len(data) >= 30:
        return (int.from_bytes(data[24:27], "little") + 1,
                int.from_bytes(data[27:30], "little") + 1)
    return 0, 0


def dimensions(data):
    """(width, height) in pixels, or (0, 0) when they cannot be read.

    Zero means "not known", never "no pixels", and every caller treats it as
    an absence: the result line leaves the size out rather than printing
    `0x0`. Parsing a header is the only way to get this without a decoder, and
    a header can be truncated, unusual or from a variant this does not know --
    so the answer is either a fact or nothing.
    """
    data = bytes(data or b"")
    media = kind(data)
    if media == "image/png":
        return _png_size(data)
    if media == "image/gif":
        return _gif_size(data)
    if media == "image/jpeg":
        return _jpeg_size(data)
    if media == "image/webp":
        return _webp_size(data)
    return 0, 0


def human_size(count):
    """A byte count as a short string. Whole KB and one decimal of MB."""
    count = max(0, int(count or 0))
    if count < 1024:
        return "%d bytes" % count
    if count < 1024 * 1024:
        return "%d KB" % (count // 1024)
    return "%.1f MB" % (count / (1024.0 * 1024.0))


class Image(object):
    """One loaded image, ready to be put in a request.

    Sealed for `agent_delegation.DelegationConstraints`' reason: this carries
    the bytes that go into a request and the media type they will be labelled
    with, and a caller that could rewrite the type after the load has checked
    it would be putting a mislabelled payload past the one check there is.
    RuntimeError rather than AttributeError, because this codebase is full of
    `getattr(x, name, default)` and broad `except Exception` readers, and an
    AttributeError would be indistinguishable from a typo.
    """

    __slots__ = ("_name", "_media_type", "_data", "_size", "_width", "_height")

    def __init__(self, name, media_type, data, size, width=0, height=0):
        object.__setattr__(self, "_name", str(name))
        object.__setattr__(self, "_media_type", str(media_type))
        object.__setattr__(self, "_data", str(data))
        object.__setattr__(self, "_size", int(size))
        object.__setattr__(self, "_width", int(width or 0))
        object.__setattr__(self, "_height", int(height or 0))

    def __setattr__(self, name, value):
        raise RuntimeError("An Image cannot be changed after it is loaded.")

    def __delattr__(self, name):
        raise RuntimeError("An Image cannot be changed after it is loaded.")

    @property
    def name(self):
        """The workspace-relative path it was read from, as the user wrote it."""
        return self._name

    @property
    def media_type(self):
        return self._media_type

    @property
    def data(self):
        """The bytes, base64 encoded. What every provider actually wants."""
        return self._data

    @property
    def size(self):
        """The raw file size in bytes, before encoding."""
        return self._size

    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    @property
    def pixels(self):
        return self._width * self._height

    def label(self):
        """What the file is, in the words a result line uses.

        The pixel size is left out when it could not be read rather than
        printed as `0x0`, which would be a measurement nobody made.
        """
        parts = [self._media_type.split("/")[-1].upper()]
        if self._width and self._height:
            parts.append("%dx%d" % (self._width, self._height))
        parts.append(human_size(self._size))
        return "%s (%s)" % (self._name, ", ".join(parts))

    def part(self):
        """This image as one entry of TMT's neutral content list."""
        return {"type": "image", "media_type": self._media_type,
                "data": self._data, "source": self._name}

    def tokens(self):
        """A rough token cost, and it is rough on purpose.

        Providers price an image by area and each of them tiles it
        differently, so there is no figure here that is right for all four.
        Area over 750 is the order of magnitude they agree on; a file whose
        dimensions could not be read falls back to its byte count, which is
        worse and is still better than counting a 4 MB base64 string as
        characters -- which is what `record_request` did before this existed
        and would have reported a million-token request.

        Only ever used to decide how much history to carry, and the meter that
        shows it already marks its input figure `~`.
        """
        if self.pixels:
            return max(1, self.pixels // 750)
        return max(1, self._size // 750)

    def __repr__(self):
        return "Image(%r, %r, %d bytes)" % (self._name, self._media_type,
                                            self._size)


def load(path):
    """Read one image from the workspace.

    Raises ValueError for everything that is not a sendable image, with the
    reason in the message: `_run_tool` turns a ValueError into "Refused: ..."
    and that sentence is the only thing the model gets to act on. Every
    refusal here therefore says what was wrong AND what would be different --
    a format that is not one of four, a file that is too large to send, a
    directory, a path outside the workspace.
    """
    import agent_file_ops

    resolved = agent_file_ops.safe_path(path)
    if not resolved.exists():
        raise ValueError("Image not found: %s" % path)
    if resolved.is_dir():
        raise ValueError("%s is a folder, not an image." % path)
    try:
        size = resolved.stat().st_size
    except OSError as error:
        raise ValueError("%s could not be measured: %s" % (path, error))
    if size == 0:
        raise ValueError("%s is empty." % path)
    if size > MAX_IMAGE_BYTES:
        # Named in both directions, because the only fix is outside TMT.
        raise ValueError(
            "%s is %s, over the %s an image may be. TMT cannot resize it: "
            "shrink or crop it and try again, or read what you need another "
            "way." % (path, human_size(size), human_size(MAX_IMAGE_BYTES)))
    try:
        with open(str(resolved), "rb") as handle:
            head = handle.read(SNIFF_BYTES)
            body = head + handle.read()
    except OSError as error:
        raise ValueError("%s could not be read: %s" % (path, error))
    media = kind(head)
    if not media:
        raise ValueError(
            "%s is not an image TMT can send. The formats are PNG, JPEG, GIF "
            "and WEBP, and the file's own first bytes decide -- an extension "
            "is not enough." % path)
    width, height = dimensions(body)
    return Image(name=agent_file_ops.posix(path), media_type=media,
                 data=base64.b64encode(body).decode("ascii"),
                 size=len(body), width=width, height=height)


# --- carrying an image from the handler to the request ----------------------
#
# An action returns a string. The loops put that string in a user message and
# send it, and there is nowhere in that shape for an image to travel. So the
# handler hangs what it loaded on the action object under a private key, and
# whichever loop is building the message asks for it back.
#
# It is `agent_multi._multi_ran`'s precedent and it is used for that reason:
# the loops put the model's own `raw` reply into the conversation, never this
# object, so nothing written here can reach the model. `attached()` ignores
# anything that is not the shape `attach()` writes, so a model that planted
# the key in its own JSON gets nothing by it.

ATTACHED_KEY = "_images_attached"


def attach(obj, images):
    """Hang loaded images on the action object that produced them."""
    if not isinstance(obj, dict) or not images:
        return obj
    obj[ATTACHED_KEY] = list(images)
    return obj


def attached(obj):
    """The images hung on this action object, or []. Never raises."""
    if not isinstance(obj, dict):
        return []
    return [item for item in (obj.get(ATTACHED_KEY) or ())
            if isinstance(item, Image)]


def gather(objs):
    """Every image hung on any of these action objects, in order.

    Takes a list because a batch and a `multi_tool` both run several actions
    into one result message, and an image read in the third of them has to
    reach the same request as the text that describes it.
    """
    found = []
    for obj in objs or ():
        found.extend(attached(obj))
        try:
            import agent_multi
        except Exception:
            continue
        for inner in agent_multi.ran(obj) or ():
            found.extend(attached(inner))
    return found


def parts(text, images):
    """TMT's neutral content value for a message: a string, or a parts list.

    With no images this returns the string it was given, unchanged and
    identical to what every caller built before this module existed -- which
    is what keeps a turn that looks at nothing byte-for-byte what it was.
    """
    images = [image for image in (images or ()) if isinstance(image, Image)]
    if not images:
        return text
    built = [{"type": "text", "text": str(text or "")}] if text else []
    return built + [image.part() for image in images]


def is_parts(content):
    """Whether a message's content is the list form rather than a string."""
    return isinstance(content, list)


def text_of(content):
    """The readable text of a message's content, whatever shape it is.

    Everything that reads a message for its words -- a trim, a record, a test
    -- asks this rather than `str(content)`, which on the list form returns
    the repr of a base64 blob.
    """
    if not isinstance(content, list):
        return "" if content is None else str(content)
    out = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "image":
            out.append("[image: %s]" % (part.get("source") or "attached"))
        elif part.get("text"):
            out.append(str(part["text"]))
    return "\n".join(out)


def measure(content, estimate_tokens):
    """A token estimate for one message's content, images included.

    Takes the estimator rather than importing it, because `agent_session` is
    where that constant lives and importing it here would be a cycle the first
    time the session wants to ask this module anything.
    """
    if not isinstance(content, list):
        return estimate_tokens("" if content is None else str(content))
    total = 0
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "image":
            total += _part_tokens(part)
        else:
            total += estimate_tokens(str(part.get("text") or ""))
    return total


def _part_tokens(part):
    """The estimate for one image part, read back out of the wire shape."""
    data = str(part.get("data") or "")
    raw = (len(data) * 3) // 4
    return max(1, raw // 750)


def prune(messages, keep=IMAGE_KEEP):
    """Take image data out of all but the most recent few messages.

    Returns a NEW list; the messages it does not change are the same objects,
    so nothing a caller still holds is rewritten under it. What replaces an
    image is a line of text naming the file, so the model reads that its
    picture is gone rather than reasoning about one it can no longer see. That
    sentence also names the verb that brings it back.

    This is the only thing bounding what images cost. `trim_messages` drops
    whole messages once there are more than twenty, which for a turn that
    looked at three screenshots in its first five rounds is fifteen rounds too
    late -- each of those rounds re-sends every image in the array.
    """
    keep = max(0, int(keep))
    out = list(messages or ())
    carrying = [index for index, message in enumerate(out)
                if isinstance(message, dict) and is_parts(message.get("content"))]
    for index in carrying[:len(carrying) - keep] if keep else carrying:
        message = out[index]
        out[index] = dict(message, content=_flatten(message.get("content")))
    return out


def _flatten(content):
    """One message's parts as plain text, with each image named as dropped."""
    lines = []
    for part in content or ():
        if not isinstance(part, dict):
            continue
        if part.get("type") == "image":
            lines.append(DROPPED % (part.get("source") or "an image"))
        elif part.get("text"):
            lines.append(str(part["text"]))
    return "\n".join(lines)


# --- whether this model can be sent one -------------------------------------


def supports_images(provider_id=None, model_id=None):
    """True, False or None for "can this model read an image".

    THREE VALUES, and None is the common one. `requests_review` has the same
    shape for the same reason: collapsing "nobody knows" into False turns the
    feature off for every model TMT has not been told about -- which is every
    model on OpenRouter, the default provider -- and collapsing it into True
    claims a capability on a model's behalf.

    The provider adapter answers, because whether a model is multimodal is a
    fact about that provider's own catalogue and the adapters are where every
    other per-provider fact already lives. A provider that cannot be reached
    or does not recognise the id answers None, and a None is attempted: a
    wasted request that comes back with a clear reason is a better failure
    than a capability refused on a guess.
    """
    try:
        import agent_providers
        import agent_models
    except Exception:
        return None
    try:
        if not provider_id:
            import agent_credentials
            provider_id = agent_credentials.selected_provider()
        provider = agent_providers.get_provider(provider_id)
        return provider.supports_images(model_id or agent_models.current_model(provider_id))
    except Exception:
        return None


# Said to the MODEL, not to the user, so it tells the model what to do about
# it: it cannot change the setting itself and must say so rather than trying
# the same path again on the next step.
UNSUPPORTED = (
    "%s cannot be sent to %s, which does not accept images. Nothing was "
    "attached. Tell the user in your next message that this model is text "
    "only and that a model that reads images can be chosen in Settings, then "
    "carry on with what you can do without it."
)

# What an unknown model's failed attempt is explained with. The request really
# was made and really was refused, so this says which of the two things it
# could have been rather than picking one.
REJECTED = (
    "The provider refused the request carrying %s. That usually means this "
    "model does not accept images. Say so and carry on without it rather "
    "than attaching it again."
)


def unavailable_reason(provider_id=None, model_id=None):
    """The refusal for this model, or "" when an image may be attempted."""
    if supports_images(provider_id, model_id) is False:
        try:
            import agent_models
            return agent_models.describe(model_id, provider_id)
        except Exception:
            return str(model_id or "the current model")
    return ""
