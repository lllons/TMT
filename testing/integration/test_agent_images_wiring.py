"""`view_image` as far as TMT is concerned: registered, dispatched, carried, sent.

Everything here goes through the real seams -- `agent_actions.execute_action`,
the real provider adapters, the real worker loop -- and never through
`agent_images` on its own, which is `test_agent_images`' instrument. The
reason is the one every wiring file in this directory states: a tool that
works perfectly and is not registered is a tool that does not exist.

This one has a second question the other wiring files do not, and it is the
one worth the file: an image is the first thing in TMT whose result is not
entirely text. So the run is followed the whole way -- action, attachment,
message, adapter, wire -- and the payload is asserted at the far end, in each
of the four providers' own shapes, because a picture that reaches three of
them and is silently dropped by the fourth is a feature that breaks when the
user changes a setting in Settings.
"""

import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

import agent_actions
import agent_config
import agent_delegation
import agent_file_ops
import agent_images
import agent_prompt
import agent_providers
import agent_session
import agent_subprompts
import agent_worker
from agent_config import REQUIRED_KEYS
from agent_manager import AgentManager


def remove_tree(path):
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


def png(width=1440, height=900):
    """A PNG whose IHDR really says these dimensions. See test_agent_images."""
    ihdr = (b"IHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")
            + b"\x08\x06\x00\x00\x00")
    return (b"\x89PNG\r\n\x1a\n" + (len(ihdr) - 4).to_bytes(4, "big") + ihdr
            + b"\x00" * 32)


class Project:
    """A throwaway workspace, with TMT's own state sent somewhere throwaway too."""

    def __init__(self):
        self.previous_root = agent_config.ROOT_DIR
        self.previous_install = agent_config.INSTALL_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_images_")).resolve()
        self.install = Path(tempfile.mkdtemp(prefix="tmt_imagesinst_")).resolve()
        agent_config.ROOT_DIR = self.path
        agent_config.INSTALL_DIR = self.install
        self.write_bytes("shot.png", png())
        self.write_bytes("notes.txt", b"just words\n")

    def write_bytes(self, name, body):
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return target

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        agent_config.INSTALL_DIR = self.previous_install
        agent_prompt.invalidate_prompt()
        remove_tree(self.path)
        remove_tree(self.install)


def run(obj, context=None):
    """One action through the dispatcher, with the loop's default authority."""
    return str(agent_actions.execute_action(
        obj, {"push_authorized": False} if context is None else context))


def act(name, **keys):
    keys["action"] = name
    return json.dumps(keys)


FINISH = act("internal_response", response="done")


class Replies:
    """Scripted model replies for a background agent, handed out in order."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.messages = []

    def __call__(self, messages, on_event=None, model=None, max_tokens=None,
                 quiet=False, **extra):
        self.messages = [dict(m) for m in messages]
        return self.replies.pop(0) if self.replies else FINISH


# --- registration -------------------------------------------------------------

def test_the_verb_is_registered_with_the_one_key_it_requires():
    """`path` and nothing else. There is deliberately no key for a format, a
    size or a scale: the format is read off the bytes, the size is refused
    rather than resized, and there is nothing to scale."""
    assert REQUIRED_KEYS.get("view_image") == ["path"], REQUIRED_KEYS.get("view_image")
    assert agent_prompt.validate_action({"action": "view_image", "path": "a.png"}) is None
    assert agent_prompt.validate_action({"action": "view_image"}) is not None


def test_the_verb_is_labelled_kinded_and_in_the_right_whitelists():
    assert agent_actions.ACTION_LABELS.get("view_image") == "View Image"
    assert agent_actions._EVENT_KIND_FOR_ACTION.get("view_image") == "file_read"
    # A security whitelist: reading one file changes nothing, so a read-only
    # delegation may look at a screenshot exactly as it may read a source file.
    assert "view_image" in agent_delegation.READ_ONLY_ACTIONS
    # A worker may. The note agent and the reviewer may not, and those two are
    # whitelists -- so being absent is the refusal.
    assert "view_image" not in agent_worker.WORKER_FORBIDDEN
    assert "view_image" not in agent_worker.WORKER_NEEDS_TERMINAL
    assert "view_image" not in agent_worker.NOTE_ACTIONS
    assert "view_image" not in agent_worker.REVIEW_ACTIONS
    assert set(agent_worker.NOTE_ACTIONS) == set(agent_subprompts.NOTE_VERBS)
    assert set(agent_worker.REVIEW_ACTIONS) == set(agent_subprompts.REVIEW_VERBS)


def test_it_is_not_a_mutation_so_a_passed_review_survives_looking_at_something():
    """MUTATING_ACTIONS is what `TMT.note_work` reads to make a passed review
    and a passed verification stale. Looking at a file changes nothing, so a
    review that approved the tree still approves it -- exactly as `read_file`
    leaves one standing."""
    assert "view_image" not in agent_config.MUTATING_ACTIONS
    # Nor the "now answer the question" nudge: the loop an image belongs to
    # ends in a fix, not in an answer.
    assert "view_image" not in agent_actions.READ_ONLY_ACTIONS


def test_the_packaging_declaration_carries_the_new_module():
    """An editable install freezes py-modules, so a module missing from it is
    invisible to `tmtcode` however well it works from a checkout."""
    declared = (Path(agent_config.__file__).resolve().parent / "pyproject.toml"
                ).read_text(encoding="utf-8")
    assert '"agent_images"' in declared


# --- the action ---------------------------------------------------------------

def test_the_image_reaches_the_message_the_result_goes_back_in():
    """The whole of the feature, followed end to end through the real seams.

    The result is a string, as every action's is; what is different is that
    the object it ran on now carries the picture, and `result_content` is what
    puts the two into one message. Nothing here hand-builds a content list.
    """
    project = Project()
    try:
        obj = {"action": "view_image", "path": "shot.png"}
        said = run(obj)
        assert "shot.png" in said
        assert "1440x900" in said

        content = agent_actions.result_content(said, [obj])
        assert agent_images.is_parts(content)
        assert content[0]["text"] == said
        assert content[1]["type"] == "image"
        assert content[1]["media_type"] == "image/png"
        assert content[1]["source"] == "shot.png"
    finally:
        project.close()


def test_an_action_that_attached_nothing_produces_the_string_it_always_did():
    """Every message-building site in TMT goes through `result_content` now.

    A turn that looked at no image has to send byte-for-byte the request it
    sent before this existed, and `is` is what proves that rather than `==`.
    """
    project = Project()
    try:
        obj = {"action": "read_file", "path": "notes.txt"}
        run(obj)
        text = "Result: just words"
        assert agent_actions.result_content(text, [obj]) is text
        assert agent_actions.result_content(text, []) is text
    finally:
        project.close()


def test_a_for_each_over_a_pattern_attaches_every_image_it_matched():
    """One action, several pictures, one message.

    This is the interaction that had a bug in it: `agent_multi.ran` answers
    with (call, result) PAIRS rather than call objects, so a `gather` that
    treated each entry as a dict found nothing and the images were silently
    dropped -- an action whose result said it had attached two of them and a
    request that carried neither. Nothing else in the feature would have
    looked wrong.
    """
    project = Project()
    try:
        project.write_bytes("second.png", png(10, 10))
        obj = {"action": "multi_tool",
               "calls": [{"action": "view_image", "for_each": "*.png"}]}
        said = run(obj)
        assert "matched 2 files" in said, said[:200]
        content = agent_actions.result_content(said, [obj])
        assert agent_images.is_parts(content)
        sources = sorted(part["source"] for part in content
                         if isinstance(part, dict) and part.get("type") == "image")
        assert sources == ["second.png", "shot.png"], sources
        # And one transcript row for the lot, not one per file.
        row = agent_actions.action_event("multi_tool", obj, said)
        assert "View Image x2" in row.message
    finally:
        project.close()


def test_every_refusal_comes_back_as_a_sentence_and_never_as_an_exception():
    """`_run_tool`'s discipline, applied to the one action that opens bytes.

    A model that named the wrong file, a file that is not an image, a path
    outside the workspace and a missing key are all mistakes to correct on the
    next step -- so each is a result, and none of them ends the turn.
    """
    project = Project()
    try:
        for obj, expected in (
                ({"action": "view_image", "path": "nope.png"}, "not found"),
                ({"action": "view_image", "path": "notes.txt"}, "PNG, JPEG"),
                ({"action": "view_image", "path": "../outside.png"}, "unsafe path"),
                ({"action": "view_image", "path": ""}, "needs a 'path'"),
                ({"action": "view_image", "path": 17}, "needs a 'path'")):
            said = run(obj)
            assert expected in said, (obj, said)
            assert agent_images.attached(obj) == []
    finally:
        project.close()


def test_read_file_on_an_image_names_the_verb_that_can_open_it():
    """It used to be a UnicodeDecodeError raised out of `read_file`, which the
    session loop caught and handed back as "the action failed to run" -- true,
    unhelpful, and wrong about whose mistake it was."""
    project = Project()
    try:
        said = run({"action": "read_file", "path": "shot.png"})
        assert "view_image" in said
        assert "PNG" in said
        assert "not text" in said
        # And a binary that is NOT an image says so without sending the model
        # to a verb that would refuse it.
        project.write_bytes("thing.bin", b"\x00\x01\x02" + b"\x00" * 100)
        other = run({"action": "read_file", "path": "thing.bin"})
        assert "binary file" in other
        assert "nothing here to read" in other

        # An image with NO NUL byte anywhere near its front. `_looks_binary`
        # is a heuristic and misses this one, which is why the signature is
        # asked about first and independently: a sniff that said "text" here
        # would send a GIF to `read_text` and raise the decode error all of
        # this exists to answer.
        # 1799 is 0x0707, so neither dimension contributes a zero byte -- the
        # first draft of this used 7x5 and got two NULs out of the little-
        # endian widths, which made the fixture prove the opposite of what it
        # was written for.
        project.write_bytes(
            "tiny.gif",
            b"GIF89a" + (1799).to_bytes(2, "little")
            + (1799).to_bytes(2, "little") + b"\xf7\xff\xff")
        assert not agent_file_ops._looks_binary(
            (project.path / "tiny.gif").read_bytes())
        assert "view_image" in run({"action": "read_file", "path": "tiny.gif"})

        # Text is untouched by any of it.
        assert run({"action": "read_file", "path": "notes.txt"}) == "just words\n"
    finally:
        project.close()


# --- the wire -----------------------------------------------------------------

def test_all_four_providers_carry_the_image_in_their_own_shape():
    """The payload asserted at the far end, per provider.

    This is the assertion the file exists for. An image that reaches three
    adapters and is silently dropped by the fourth is a capability that breaks
    when the user changes a setting, and nothing upstream would notice.
    """
    image = agent_images.Image("a.png", "image/png", "QUJDRA==", 4, 10, 20)
    messages = [{"role": "system", "content": "SYS"},
                {"role": "user", "content": agent_images.parts("look", [image])}]

    # The SHAPE, spelled out per provider, and not merely "the payload is in
    # the JSON somewhere". A mutation that made Gemini wrap its already-built
    # parts a second time -- {"parts": [{"text": [ ...the real parts... ]}]} --
    # survived a test that only searched the serialised body, because the
    # base64 really was in there. It would be rejected by the API.
    expected = {
        "openrouter": [
            {"type": "text", "text": "look"},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,QUJDRA=="}}],
        "openai": [
            {"type": "text", "text": "look"},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,QUJDRA=="}}],
        "anthropic": [
            {"type": "text", "text": "look"},
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/png",
                                         "data": "QUJDRA=="}}],
        "gemini": [
            {"text": "look"},
            {"inline_data": {"mime_type": "image/png", "data": "QUJDRA=="}}],
    }
    assert set(expected) == set(agent_providers.PROVIDERS), sorted(expected)

    for provider_id in agent_providers.PROVIDERS:
        provider = agent_providers.get_provider(provider_id)
        system, converted = provider.convert_messages(messages)
        assert system == "SYS", provider_id
        assert len(converted) == 1, provider_id
        # Gemini puts its blocks under "parts"; the other three under
        # "content". Nothing else may be there.
        key = "parts" if provider_id == "gemini" else "content"
        assert set(converted[0]) == {"role", key}, (provider_id, converted[0])
        assert converted[0]["role"] == "user", provider_id
        assert converted[0][key] == expected[provider_id], provider_id


def test_the_whole_request_body_carries_it_for_each_provider():
    """Through `chat_payload`, which is what actually gets posted."""
    image = agent_images.Image("a.png", "image/png", "QUJDRA==", 4, 10, 20)
    messages = [{"role": "system", "content": "SYS"},
                {"role": "user", "content": agent_images.parts("look", [image])}]
    for provider_id in agent_providers.PROVIDERS:
        provider = agent_providers.get_provider(provider_id)
        _url, body = provider.chat_payload(messages, model="m")
        assert "QUJDRA==" in json.dumps(body), provider_id


def test_a_conversation_with_no_image_is_the_payload_it_always_was():
    """The four adapters had one shape before this and must still have it."""
    messages = [{"role": "system", "content": "SYS"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"}]
    for provider_id in agent_providers.PROVIDERS:
        provider = agent_providers.get_provider(provider_id)
        _system, converted = provider.convert_messages(messages)
        for message in converted:
            body = message.get("content")
            if provider_id == "gemini":
                assert message["parts"] == [{"text": message["parts"][0]["text"]}]
            else:
                assert isinstance(body, str), provider_id


def test_a_system_message_is_still_lifted_out_even_in_the_list_form():
    """The lift is what keeps a system role out of "messages".

    TMT never builds a system message any other way than as text, and the
    guard is here because the failure if one ever arrived is the silent kind
    `agent_providers`' own docstring is about: Anthropic rejects a system role
    inside the message array and Gemini ignores it, so a list falling past the
    lift would produce an agent answering with none of TMT's rules.
    """
    image = agent_images.Image("a.png", "image/png", "QUJDRA==", 4, 10, 20)
    messages = [{"role": "system", "content": agent_images.parts("RULES", [image])},
                {"role": "user", "content": "hi"}]
    for provider_id in agent_providers.PROVIDERS:
        provider = agent_providers.get_provider(provider_id)
        system, converted = provider.convert_messages(messages)
        assert "RULES" in system, provider_id
        assert [m["role"] for m in converted] == ["user"], provider_id
        assert "QUJDRA==" not in json.dumps(converted), provider_id


def test_a_provider_that_cannot_carry_an_image_says_so_instead_of_dropping_it():
    """Unreachable with the four adapters that ship, and here so that a fifth
    which forgets `image_block` is visibly missing the feature rather than
    quietly losing the picture."""
    class Bare(agent_providers.Provider):
        ROLE_MAP = {"user": "user", "assistant": "assistant"}

    image = agent_images.Image("a.png", "image/png", "QUJDRA==", 4, 10, 20)
    _system, converted = Bare().convert_messages(
        [{"role": "user", "content": agent_images.parts("look", [image])}])
    wire = json.dumps(converted)
    assert "QUJDRA==" not in wire
    assert "cannot carry images" in wire


# --- the budget ---------------------------------------------------------------

def test_the_meter_counts_an_image_by_its_pixels_and_not_by_its_base64():
    """`record_request` used to `str()` the content.

    On the list form that is the repr of a base64 blob, so one screenshot
    would have been reported as a request of roughly a million tokens -- in
    the one readout the user is watching to know how full the window is.
    """
    session = agent_session.Session()
    big = agent_images.Image("a.png", "image/png", "Q" * 400000, 300000, 800, 600)
    counted = session.record_request(
        [{"role": "user", "content": agent_images.parts("hi", [big])}])
    assert counted < 2000, counted
    assert counted > 50, counted


def test_the_worker_counts_one_the_same_way():
    """The agent card's own token readout, which had the identical bug."""
    big = agent_images.Image("a.png", "image/png", "Q" * 400000, 300000, 800, 600)
    chars = agent_worker._message_chars(
        [{"role": "user", "content": agent_images.parts("hi", [big])}])
    assert chars < 5000, chars
    # And an ordinary conversation is measured exactly as it was.
    assert agent_worker._message_chars(
        [{"role": "user", "content": "hello"}]) == len("hello")


def test_the_session_loop_prunes_images_and_the_worker_loop_does_too():
    """Both loops, because a branch only rehearsed in the rare case is a
    branch nobody has read -- which this repository has been bitten by twice."""
    source = (Path(agent_config.__file__).resolve().parent / "TMT.py"
              ).read_text(encoding="utf-8")
    assert "agent_images.prune(messages)" in source
    worker = (Path(agent_config.__file__).resolve().parent / "agent_worker.py"
              ).read_text(encoding="utf-8")
    assert "_prune_images(messages)" in worker


# --- what each kind of agent is taught ----------------------------------------

def test_the_main_prompt_teaches_it_and_the_note_and_review_prompts_do_not():
    """Two-sided isolation, the shape `web_search` already has: a worker has
    the verb and is taught it, and the two agents refused it are never shown
    it. A section offering a verb the reader is refused is the same defect as
    the verb being reachable."""
    project = Project()
    try:
        main = agent_prompt.get_system_prompt()
        assert "view_image" in main
        assert "=== LOOKING AT AN IMAGE ===" in main
        agent_subprompts.invalidate_subprompts()
        assert "view_image" in agent_subprompts.worker_prompt()
        assert "view_image" not in agent_subprompts.note_prompt()
        assert "view_image" not in agent_subprompts.review_prompt()
    finally:
        agent_subprompts.invalidate_subprompts()
        project.close()


def test_the_tool_choice_table_offers_it_only_where_the_verb_exists():
    table = agent_prompt._with_image_row(agent_prompt.TOOL_CHOICE_RULES)
    assert "-> view_image" in table
    assert "view_image" not in agent_prompt.TOOL_CHOICE_RULES
    # The arrows form a column, and it is measured off the row beside it
    # rather than counted by hand.
    rows = [line for line in table.split("\n") if "->" in line]
    columns = set(line.index("->") for line in rows)
    assert len(columns) == 1, sorted(columns)


def test_the_example_in_the_prompt_is_a_real_action_that_validates():
    """A prompt example that would be refused teaches being refused."""
    found = 0
    for line in agent_prompt.IMAGE_REFERENCE.split("\n"):
        line = line.strip()
        if not line.startswith('{"action"'):
            continue
        obj = json.loads(line)
        assert agent_prompt.validate_action(obj) is None, line
        assert obj["action"] == "view_image"
        found += 1
    assert found >= 2, found


# --- background agents --------------------------------------------------------

def test_the_note_agent_and_the_reviewer_are_refused_it_before_it_runs():
    """A whitelist checked before dispatch, so nothing is opened at all."""
    project = Project()
    manager = AgentManager()
    try:
        for kind, allowed in (("note", agent_worker.NOTE_ACTIONS),
                              ("review", agent_worker.REVIEW_ACTIONS)):
            record = manager.spawn("q", kind=kind)
            said = agent_worker._refusal("view_image", allowed,
                                         agent_worker.WORKER_FORBIDDEN)
            assert said, kind
            assert "view_image" in said, kind
            manager.kill(record.id)
    finally:
        manager.kill_all()
        project.close()


def test_a_worker_looks_at_an_image_and_it_reaches_that_worker_s_own_request():
    """The real worker loop, with an injected `ask` and a real workspace.

    What is asserted is not the result string -- that is `execute_action`'s and
    is tested above -- but that the picture arrived in the NEXT request the
    worker made. A worker whose result said "attached" and whose next request
    carried nothing would look entirely correct from every other angle.
    """
    project = Project()
    manager = AgentManager()
    try:
        replies = Replies([act("view_image", path="shot.png"), FINISH])
        record = manager.spawn("look at the screenshot")
        agent_worker.run_worker(record, manager, ask=replies)
        carrying = [m for m in replies.messages
                    if agent_images.is_parts(m.get("content"))]
        assert carrying, [str(m.get("content"))[:60] for m in replies.messages]
        wire = json.dumps(carrying[-1]["content"])
        assert "image/png" in wire
        assert "shot.png" in wire
    finally:
        manager.kill_all()
        project.close()


def test_a_worker_s_batch_path_carries_an_image_too():
    """The other dispatch path, and the one that shipped broken.

    The first version of this named the batch list `batch`, which is what
    TMT.py's own loop calls it and is NOT what this loop calls it -- so the
    worker's batch path raised a NameError on every batch a worker ever ran,
    image or no image. The image tests all took the single-action path and
    were green; the full suite caught it.

    That is the lesson this repository keeps recording: a branch only
    rehearsed in the rare case is a branch nobody has read. Both paths are
    driven here for the same reason both are guarded in `_run_batch`.
    """
    project = Project()
    manager = AgentManager()
    try:
        batch = json.dumps({"actions": [
            {"action": "read_file", "path": "notes.txt"},
            {"action": "view_image", "path": "shot.png"}]})
        replies = Replies([batch, FINISH])
        record = manager.spawn("look at the screenshot")
        agent_worker.run_worker(record, manager, ask=replies)
        carrying = [m for m in replies.messages
                    if agent_images.is_parts(m.get("content"))]
        assert carrying, [str(m.get("content"))[:60] for m in replies.messages]
        wire = json.dumps(carrying[-1]["content"])
        assert "image/png" in wire
        assert "shot.png" in wire
    finally:
        manager.kill_all()
        project.close()


def test_a_worker_is_asked_about_its_own_model_and_not_the_session_s():
    """`spawn_agent` takes a model of its own, so the capability question has
    to be about the model the request is actually going to."""
    manager = AgentManager()
    try:
        record = manager.spawn("q", model="gpt-3.5-turbo")
        context = agent_worker._context(record)
        assert context.get("model") == "gpt-3.5-turbo"
        said = agent_actions.execute_action(
            {"action": "view_image", "path": "shot.png"}, context)
        assert "gpt-3.5-turbo" in said
        assert "Settings" in said
    finally:
        manager.kill_all()


def test_a_model_that_cannot_see_is_refused_before_the_file_is_even_opened():
    """Loading three megabytes only to find out it cannot be sent wastes the
    read and, worse, produces a refusal that reads as though the file were the
    problem. The path named does not have to exist for this to answer."""
    said = agent_actions.execute_action(
        {"action": "view_image", "path": "does-not-exist-anywhere.png"},
        {"model": "gpt-3.5-turbo"})
    assert "text only" in said or "does not accept images" in said
    assert "not found" not in said
