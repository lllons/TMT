"""`read_document` as far as TMT is concerned: registered, dispatched, taught.

Everything here goes through the real seams -- `agent_actions.execute_action`,
the real prompts, the real whitelists -- and never through `agent_documents`
on its own, which is `test_agent_documents`' instrument. The reason is the one
every wiring file in this directory states: a tool that works perfectly and is
not registered is a tool that does not exist, and an editable install freezes
its module list, so a new module is invisible to `tmtcode` until pyproject
names it.

The question this file has that the others do not is WHO may use it.
`view_image` and the two web verbs are held out of the shared action reference
because the note agent and the reviewer are refused them; this one is not, and
that is a decision rather than an oversight -- it reads one workspace file and
answers with text, which is what all four agents already do with `read_file`.
So the whitelists are checked in both directions: on every list it should be
on, and correctly absent from the sets that would change what it means.
"""

import os
import shutil
import stat
import tempfile
import zipfile
import zlib
from pathlib import Path

import agent_actions
import agent_capabilities
import agent_config
import agent_delegation
import agent_documents
import agent_file_ops
import agent_prompt
import agent_subprompts
import agent_worker
from agent_config import REQUIRED_KEYS


def remove_tree(path):
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


def pdf(text="Hello from the specification."):
    """A one-page PDF that really does draw `text`. See test_agent_documents."""
    content = ("BT /F1 12 Tf 72 700 Td (%s) Tj ET" % text).encode("latin-1")
    objects = [
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n",
        b"3 0 obj<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>"
        b"/Contents 5 0 R>>endobj\n",
        b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n",
        b"5 0 obj<</Length " + str(len(content)).encode() + b">>stream\n"
        + content + b"\nendstream endobj\n",
    ]
    return (b"%PDF-1.7\n" + b"".join(objects)
            + b"trailer<</Root 1 0 R>>\n%%EOF\n")


def docx(text="A paragraph in a Word document."):
    body = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
            'openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            '<w:p><w:r><w:t>%s</w:t></w:r></w:p></w:body></w:document>' % text)
    import io
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", body)
    return buffer.getvalue()


class Project:
    """A throwaway workspace, with TMT's own state sent somewhere throwaway too."""

    def __init__(self):
        self.previous_root = agent_config.ROOT_DIR
        self.previous_install = agent_config.INSTALL_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_docs_")).resolve()
        self.install = Path(tempfile.mkdtemp(prefix="tmt_docsinst_")).resolve()
        agent_config.ROOT_DIR = self.path
        agent_config.INSTALL_DIR = self.install
        self.write("spec.pdf", pdf())
        self.write("report.docx", docx())
        self.write("rows.csv", b"name,qty\nwidget,3\n")
        self.write("notes.txt", b"just words\n")

    def write(self, name, body):
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return target

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        agent_config.INSTALL_DIR = self.previous_install
        remove_tree(self.path)
        remove_tree(self.install)


def run(action, **keys):
    """One action through the real dispatcher, authorised like a main turn."""
    obj = dict(keys)
    obj["action"] = action
    return agent_actions.execute_action(
        obj, {"capabilities": agent_capabilities.Capabilities()})


# --- registered and dispatched ----------------------------------------------


def test_the_action_is_registered_with_path_as_its_only_required_key():
    assert REQUIRED_KEYS["read_document"] == ["path"]


def test_the_module_is_declared_in_the_packaging_so_an_install_can_see_it():
    """An editable install freezes py-modules, so a module absent from it is
    invisible to `tmtcode` however well it works from a checkout."""
    declared = Path(agent_config.__file__).resolve().parent / "pyproject.toml"
    assert '"agent_documents"' in declared.read_text(encoding="utf-8")


def test_a_pdf_is_converted_through_the_dispatcher():
    project = Project()
    try:
        answer = run("read_document", path="spec.pdf")
        assert "Hello from the specification." in answer
        assert answer.startswith("spec.pdf (PDF, 1 page,")
    finally:
        project.close()


def test_a_word_document_is_converted_through_the_dispatcher():
    project = Project()
    try:
        answer = run("read_document", path="report.docx")
        assert "A paragraph in a Word document." in answer
        assert "Word document" in answer.split("\n")[0]
    finally:
        project.close()


def test_a_page_range_reaches_the_module():
    project = Project()
    try:
        answer = run("read_document", path="spec.pdf", pages="1")
        assert "## Page 1" in answer
        answer = run("read_document", path="spec.pdf", pages="4")
        assert "Refused:" in answer and "page 4" in answer
    finally:
        project.close()


def test_a_refusal_arrives_as_a_sentence_and_never_as_a_traceback():
    """`_run_tool` turns the module's ValueError into "Refused: ...", and that
    sentence is the only thing the model gets to act on."""
    project = Project()
    try:
        for keys, expected in (
                ({"path": "nope.pdf"}, "not found"),
                ({"path": "notes.txt"}, "read_file"),
                ({"path": "rows.csv", "pages": "2"}, "pages"),
                # The sandbox's own words, reached through `safe_path` exactly
                # as `read_file` reaches it.
                ({"path": "../outside.pdf"}, "unsafe path")):
            answer = run("read_document", **keys)
            assert answer.startswith("Refused:"), (keys, answer)
            assert expected in answer, (keys, answer)
    finally:
        project.close()


def test_the_action_validates_the_way_every_other_one_does():
    assert agent_prompt.validate_action({"action": "read_document"}) is not None
    assert agent_prompt.validate_action(
        {"action": "read_document", "path": "x.pdf"}) is None


def test_a_missing_module_is_answered_in_words_rather_than_ending_the_turn():
    """The frozen-module-list failure, which is the reason `_run_tool` exists."""
    import sys
    saved = sys.modules.pop("agent_documents", None)
    sys.modules["agent_documents"] = None       # an import that raises
    try:
        answer = run("read_document", path="spec.pdf")
        assert "agent_documents is unavailable" in answer
    finally:
        if saved is not None:
            sys.modules["agent_documents"] = saved
        else:
            sys.modules.pop("agent_documents", None)


# --- what read_file does now ------------------------------------------------


def test_read_file_sends_a_document_here_and_names_the_action_to_use():
    project = Project()
    try:
        answer = agent_file_ops.read_file("spec.pdf")
        assert "not text" in answer
        assert '{"action":"read_document","path":"spec.pdf"}' in answer
    finally:
        project.close()


def test_read_file_still_reads_the_text_formats_exactly():
    """The half of this that could silently regress.

    A CSV, a JSON file and an XML file are TEXT. `read_file` returns them
    byte for byte, and a model editing one wants the file rather than a
    rendering of it -- so sending those to `read_document` would be a
    regression dressed as a feature.
    """
    project = Project()
    try:
        assert agent_file_ops.read_file("rows.csv") == "name,qty\nwidget,3\n"
        assert agent_file_ops.read_file("notes.txt") == "just words\n"
        project.write("data.json", b'{"a": 1}')
        assert agent_file_ops.read_file("data.json") == '{"a": 1}'
    finally:
        project.close()


def test_an_image_still_goes_to_view_image_rather_than_here():
    """Asked first and answered first: the two questions must not collide."""
    project = Project()
    try:
        ihdr = b"IHDR" + (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
        project.write("shot.png", b"\x89PNG\r\n\x1a\n"
                      + (len(ihdr) - 4).to_bytes(4, "big") + ihdr + b"\x00" * 8)
        answer = agent_file_ops.read_file("shot.png")
        assert "view_image" in answer
        assert "read_document" not in answer
    finally:
        project.close()


# --- who may use it ---------------------------------------------------------


def test_every_agent_may_read_a_document_including_the_two_that_may_not_see():
    """The decision this feature made differently from `view_image`.

    An image is not text and changes the shape of the request; a web search
    reaches the network. Neither is true here -- this opens one path through
    the same sandbox `read_file` uses and answers with text -- so the note
    agent and the reviewer get it for the reason they already have `read_file`.
    """
    assert "read_document" in agent_worker.NOTE_ACTIONS
    assert "read_document" in agent_worker.REVIEW_ACTIONS
    assert "read_document" in agent_delegation.READ_ONLY_ACTIONS


def test_the_prompts_offer_exactly_what_the_loops_allow():
    """Two lists per agent, and they must agree or the prompt offers a verb
    the loop refuses."""
    assert set(agent_subprompts.NOTE_VERBS) == set(agent_worker.NOTE_ACTIONS)
    assert set(agent_subprompts.REVIEW_VERBS) == set(agent_worker.REVIEW_ACTIONS)


def test_a_read_only_delegation_may_use_it():
    constraints = agent_delegation.parse({"read_only": True})
    assert agent_delegation.refusal(constraints, "read_document") == ""
    assert agent_delegation.refusal(constraints, "write_file") != ""


def test_it_is_not_forbidden_to_a_worker_and_needs_no_terminal():
    assert "read_document" not in agent_worker.WORKER_FORBIDDEN
    assert "read_document" not in agent_worker.WORKER_NEEDS_TERMINAL


def test_a_delegated_worker_records_it_as_a_file_it_inspected():
    """The report a contract asks for is built from the actions' own requests,
    never from prose, so a verb that reads a file has to be on that list."""
    assert "read_document" in agent_worker._READING_ACTIONS


# --- what it does not change ------------------------------------------------


def test_it_is_not_a_mutating_action():
    """It reads one file. A passed review and a passed verification both
    survive it, exactly as they survive `read_file`."""
    assert "read_document" not in agent_config.MUTATING_ACTIONS


def test_it_is_not_one_of_the_verbs_that_end_a_turn_or_need_authorising():
    assert "read_document" not in agent_capabilities.gated_actions(None)
    context = {}                       # no capabilities key at all
    project = Project()
    try:
        # The capability guard fails closed and must not be reachable by an
        # ordinary read: a document is not a capability.
        answer = agent_actions.execute_action(
            {"action": "read_document", "path": "spec.pdf"}, context)
        assert "Hello from the specification." in answer
    finally:
        project.close()


# --- what the user and the model see ----------------------------------------


def test_the_transcript_names_the_file_and_calls_the_action_by_a_phrase():
    project = Project()
    try:
        obj = {"action": "read_document", "path": "spec.pdf"}
        result = run("read_document", path="spec.pdf")
        event = agent_actions.action_event("read_document", obj, result)
        assert event is not None
        assert event.kind == "file_read"
        assert "spec.pdf" in event.message
        assert agent_actions.ACTION_LABELS["read_document"] == "Read Document"
    finally:
        project.close()


def test_it_is_taught_to_every_agent_and_its_example_is_a_real_action():
    """It lives in the shared ACTION_REFERENCE, so all four prompts carry it.

    The example is parsed and validated rather than eyeballed, because an
    example that broke a rule would teach breaking it.
    """
    import json
    import re
    for text in (agent_prompt.get_system_prompt(),
                 agent_subprompts.worker_prompt(),
                 agent_subprompts.note_prompt(),
                 agent_subprompts.review_prompt()):
        assert "read_document - keys: path." in text
    found = re.findall(r'^\s*(\{"action":"read_document".*)$',
                       agent_prompt.ACTION_REFERENCE, re.M)
    assert found, "the reference must carry a worked example"
    for line in found:
        obj = json.loads(line)
        assert agent_prompt.validate_action(obj) is None, line


def test_the_tool_choice_table_says_which_question_it_answers():
    assert "read_document" in agent_prompt.TOOL_CHOICE_RULES


# --- through multi_tool -----------------------------------------------------


def test_several_documents_can_be_read_in_one_action():
    """`multi_tool` dispatches every call back through `execute_action`, so a
    verb that works alone works inside one. Checked rather than assumed,
    because the calls are validated a second time on the way in."""
    project = Project()
    try:
        answer = run("multi_tool", calls=[
            {"action": "read_document", "path": "spec.pdf"},
            {"action": "read_document", "path": "report.docx"},
        ])
        assert "Hello from the specification." in answer
        assert "A paragraph in a Word document." in answer
    finally:
        project.close()


def test_a_pattern_reads_every_matching_document():
    project = Project()
    try:
        project.write("docs/one.pdf", pdf("First document."))
        project.write("docs/two.pdf", pdf("Second document."))
        answer = run("multi_tool", calls=[
            {"action": "read_document", "for_each": "docs/*.pdf"}])
        assert "First document." in answer
        assert "Second document." in answer
    finally:
        project.close()
