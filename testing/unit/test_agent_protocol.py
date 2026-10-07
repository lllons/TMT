"""Tests for agent_protocol: the TAGS wire protocol, JSON detection, rendering.

Nothing here talks to a model or touches the disk. The prompt examples are
read out of agent_prompt and agent_subprompts as they stand, because the
promise this module makes is that every one of them can be written as tags
and read back unchanged -- and a promise about a hand-picked sample is a
promise about the sample.
"""

import json
import re

import agent_protocol as P
from agent_protocol import (JSON, TAGS, ProtocolError, StreamingTagParser,
                            detect, example, parse, parse_tags, render,
                            transliterate)


# --- helpers ------------------------------------------------------------------

def refused(text, *words):
    """parse_tags must refuse `text`, with every one of `words` in the sentence."""
    try:
        got = parse_tags(text)
    except ProtocolError as error:
        message = str(error)
        for word in words:
            assert word in message, (word, message)
        return message
    raise AssertionError("not refused: %r -> %r" % (text, got))


def feed(text, size=1):
    """Drive a StreamingTagParser `size` characters at a time."""
    parser = StreamingTagParser()
    events = []
    for index in range(0, len(text), size):
        events.extend(parser.feed(text[index:index + size]))
    return parser, events


def merged(events):
    """Events with neighbouring text joined, which is all chunking may change."""
    out = []
    for kind, value in events:
        if kind == "text" and out and out[-1][0] == "text":
            out[-1] = ("text", out[-1][1] + value)
        else:
            out.append((kind, value))
    return out


def texts(events):
    return "".join(value for kind, value in events if kind == "text")


def values(events, kind):
    return [value for name, value in events if name == kind]


def round_trips(obj):
    """render and example both read back as `obj`, and JSON does too."""
    for written in (render(obj), example(obj)):
        back = parse_tags(written)
        assert back == obj, (obj, written, back)
    assert parse(render(obj, JSON)) == obj
    assert "\n" not in example(obj) or any(
        "\n" in str(v) or str(v)[:1] in (" ", "\t") for v in _leaves(obj)), example(obj)


def _leaves(obj):
    if isinstance(obj, dict):
        for value in obj.values():
            for leaf in _leaves(value):
                yield leaf
    elif isinstance(obj, list):
        for value in obj:
            for leaf in _leaves(value):
                yield leaf
    else:
        yield obj


# --- the vocabulary -----------------------------------------------------------

def test_the_two_protocols_agree_with_agent_config():
    import agent_config
    assert P.PROTOCOLS == ("tags", "json") == tuple(agent_config.PROTOCOLS)
    assert P.DEFAULT_PROTOCOL == TAGS == agent_config.DEFAULT_PROTOCOL
    assert (P.TAGS, P.JSON) == ("tags", "json")


def test_the_legacy_table_is_every_old_name_the_actions_module_translates():
    import agent_actions
    # `respond` is translated by canonical_action itself (it reads `final`),
    # which is why it is not in _LEGACY_ACTIONS and is in this table.
    assert set(P.LEGACY_PRIMARY) == set(agent_actions._LEGACY_ACTIONS) | {"respond"}


def test_the_primary_key_is_the_first_required_key():
    import agent_config
    for action, keys in agent_config.REQUIRED_KEYS.items():
        expected = "command" if action == "bash" else (keys[0] if keys else None)
        assert P.primary_key(action) == expected, action
    assert P.primary_key("respond") == "message"
    assert P.primary_key("search_files") == "query"
    assert P.primary_key("run_file") == "path"
    assert P.primary_key("READ_FILE") == "path"
    assert P.primary_key("no_such_action") is None


def test_the_tables_are_disjoint_and_name_their_readers():
    assert not (P.BOOL_KEYS & P.INT_KEYS)
    assert not (P.CONTAINER_KEYS & (P.BOOL_KEYS | P.INT_KEYS))
    assert P.bool_keys() == P.BOOL_KEYS
    assert P.int_keys() == P.INT_KEYS
    assert P.container_keys() == P.CONTAINER_KEYS
    for table in (P._BOOL_READERS, P._INT_READERS, P._CONTAINER_READERS):
        for key, reader in table.items():
            assert isinstance(reader, str) and reader.strip(), key
    # Agent ids are text: `"id": 3` and `"id": "3"` are the same agent.
    assert "id" not in P.INT_KEYS and "id" not in P.BOOL_KEYS


def test_the_markers_tmt_writes_are_typed_like_the_model_module_reads_them():
    import agent_model
    assert agent_model.SYNTHETIC_KEY in P.BOOL_KEYS
    assert agent_model.PROSE_KEY in P.BOOL_KEYS
    assert agent_model.SYNTHETIC_REASON not in P.BOOL_KEYS | P.INT_KEYS
    got = parse_tags("/end_conversation/ /message/ x //message/ /tmt_synthetic/ true "
                     "//tmt_synthetic/ /tmt_synthetic_reason/ parse //tmt_synthetic_reason/ "
                     "/tmt_prose/ FALSE //tmt_prose/ //end_conversation/")
    assert got == {"action": "end_conversation", "message": "x", "tmt_synthetic": True,
                   "tmt_synthetic_reason": "parse", "tmt_prose": False}


def test_every_typed_key_is_read_by_the_module_the_table_names():
    """A guard against a table entry somebody invented: each key's name, in
    quotes, appears in the source of a module that reads action objects."""
    import inspect
    import agent_actions, agent_ask, agent_delegation, agent_model, agent_reviewbot, agent_worker
    source = "".join(inspect.getsource(module) for module in
                     (agent_actions, agent_ask, agent_delegation, agent_model,
                      agent_reviewbot, agent_worker))
    for key in P.BOOL_KEYS | P.INT_KEYS | P.CONTAINER_KEYS:
        assert '"%s"' % key in source, key


# --- rule 1: the action is the outer tag --------------------------------------

def test_inline_form():
    got = parse_tags("/read_file/ /path/ src/main.py //path/ "
                     "/progress/ Reading the entry point //progress/ //read_file/")
    assert got == {"action": "read_file", "path": "src/main.py",
                   "progress": "Reading the entry point"}


def test_block_form_keeps_the_trailing_newline_like_a_heredoc():
    got = parse_tags('/write_file/\n/path/ src/hello.py //path/\n/content/\n'
                     'def hello():\n    return "hi"\n//content/\n'
                     '/progress/ Writing the greeting //progress/\n//write_file/')
    assert got == {"action": "write_file", "path": "src/hello.py",
                   "content": 'def hello():\n    return "hi"\n',
                   "progress": "Writing the greeting"}


def test_inline_and_block_mix_freely():
    got = parse_tags("/write_file/ /path/ a.py //path/ /content/\nx = 1\n//content/ //write_file/")
    assert got == {"action": "write_file", "path": "a.py", "content": "x = 1\n"}


def test_names_are_case_insensitive_and_lowercased():
    got = parse_tags("/Read_File/ /PATH/ a.py //path/ //READ_FILE/")
    assert got == {"action": "read_file", "path": "a.py"}


# --- rule 3: leaf whitespace --------------------------------------------------

def test_inline_strips_the_spaces_and_tabs_around_a_value():
    assert parse_tags("/read_file/ /path/ \t a b \t //path/ //read_file/")["path"] == "a b"


def test_a_closer_on_the_last_content_line_leaves_no_trailing_newline():
    got = parse_tags("/write_file/ /path/ a //path/ /content/\nline one\nline two//content/ //write_file/")
    assert got["content"] == "line one\nline two"


def test_a_closer_on_its_own_indented_line_drops_the_indent_but_keeps_the_newline():
    got = parse_tags("/write_file/ /path/ a //path/ /content/   \n  x\n    //content/ //write_file/")
    assert got["content"] == "  x\n"


def test_spaces_before_a_closer_on_a_content_line_are_not_part_of_the_value():
    got = parse_tags("/write_file/ /path/ a //path/ /content/\nx = 1   \t//content/ //write_file/")
    assert got["content"] == "x = 1"


def test_block_form_keeps_the_first_lines_indentation_and_blank_lines():
    got = parse_tags("/write_file/ /path/ a //path/ /content/\n\n    indented\n\n//content/ //write_file/")
    assert got["content"] == "\n    indented\n\n"


def test_only_file_text_keeps_its_whitespace():
    assert P.EXACT_KEYS == ("content", "search", "replace") == P.exact_keys()
    assert not set(P.EXACT_KEYS) & (P.BOOL_KEYS | P.INT_KEYS | P.CONTAINER_KEYS)


def test_a_path_in_block_form_is_trimmed():
    got = parse_tags("/read_file/\n/path/\n  src/a.py\n//path/\n//read_file/")
    assert got == {"action": "read_file", "path": "src/a.py"}
    got = parse_tags("/read_file/\nsrc/a.py\n//read_file/")
    assert got == {"action": "read_file", "path": "src/a.py"}


def test_a_message_in_block_form_is_trimmed_and_keeps_its_interior():
    body = "\n\n  First line.\n\n    indented\nLast line.  \n\n"
    for reply in ("/end_conversation/%s//end_conversation/" % body,
                  "/end_conversation/\n/message/%s//message/\n//end_conversation/" % body,
                  "/send_message/ /message:a/%s//message:a/ //send_message/" % body):
        got = parse_tags(reply)
        assert got["message"] == "First line.\n\n    indented\nLast line.", (reply, got)
        # The live box follows the same rule: no leading blank lines, and no
        # trailing whitespace shown before it is known not to be the end.
        parser = StreamingTagParser()
        shown = ""
        for char in reply:
            shown += texts(parser.feed(char))
            assert got["message"].startswith(shown), (reply, shown)
        assert shown == got["message"], (reply, shown)


def test_content_in_block_form_keeps_its_trailing_newline():
    got = parse_tags("/write_file/\n/path/\nsrc/a.py\n//path/\n/content/\n\n  x = 1\n\n//content/\n//write_file/")
    assert got == {"action": "write_file", "path": "src/a.py", "content": "\n  x = 1\n\n"}


def test_search_and_replace_keep_their_indentation_and_newline():
    got = parse_tags("/patch_file/\n/path/ net.py //path/\n/search/\n    timeout = 5\n//search/\n"
                     "/replace/\n    timeout = 30\n    retries = 3\n//replace/\n//patch_file/")
    assert got == {"action": "patch_file", "path": "net.py",
                   "search": "    timeout = 5\n", "replace": "    timeout = 30\n    retries = 3\n"}
    got = parse_tags("/replace_across/\n    old_name()\n//replace_across/")
    assert got["search"] == "    old_name()\n"      # the shorthand of an exact key is exact


def test_no_other_key_ever_ends_in_a_newline():
    import agent_config
    keys = {key for keys in agent_config.REQUIRED_KEYS.values() for key in keys}
    keys |= {"progress", "next_step", "glob", "pattern", "url", "command", "note", "title",
             "status", "operation", "model", "id", "for_each", "recency", "tmt_synthetic_reason"}
    keys -= set(P.EXACT_KEYS) | P.CONTAINER_KEYS | P.BOOL_KEYS | P.INT_KEYS
    for key in sorted(keys):
        for written in ("/%s/\nvalue\n//%s/", "/%s/ value\n\n//%s/", "/%s/\n\tvalue \n  //%s/"):
            got = parse_tags("/review/ /scope/ diff //scope/ " + written % (key, key) + " //review/")
            assert got[key] == "value", (key, written, got)
    got = parse_tags("/git_commit/ /message/ m //message/ /paths/ /item/\nsrc/a.py\n//item/ "
                     "/item/ b.py \n //item/ //paths/ //git_commit/")
    assert got["paths"] == ["src/a.py", "b.py"]
    got = parse_tags("/plan/ /operation/ create //operation/ /steps/ /item/ /title/\nRead\n//title/ "
                     "/status/ pending\n//status/ //item/ //steps/ //plan/")
    assert got["steps"] == [{"title": "Read", "status": "pending"}]


def test_render_trims_a_value_the_reader_would_trim():
    assert render({"action": "read_file", "path": "  src/a.py\n"}) == \
        "/read_file/ /path/ src/a.py //path/ //read_file/"
    assert render({"action": "end_conversation", "message": "\n Done. \n"}) == \
        "/end_conversation/ Done. //end_conversation/"
    assert render({"action": "git_commit", "message": "m", "paths": [" a.py\n"]}) == \
        "/git_commit/ /message/ m //message/ /paths/ /item/ a.py //item/ //paths/ //git_commit/"
    written = render({"action": "git_commit", "message": "Fix it\n\nBecause."})
    assert written == "/git_commit/\n/message/\nFix it\n\nBecause.\n//message/\n//git_commit/"
    # File text is not trimmed: its leading indent and trailing newline survive.
    obj = {"action": "patch_file", "path": "a", "search": "  x\n", "replace": "\ty\n"}
    assert parse_tags(render(obj)) == obj


def test_crlf_is_read_as_lf_inside_values():
    got = parse_tags("/write_file/\r\n/path/ a.py //path/\r\n/content/\r\nx = 1\r\ny = 2\r\n//content/\r\n//write_file/")
    assert got == {"action": "write_file", "path": "a.py", "content": "x = 1\ny = 2\n"}


# --- rule 2: the shorthand ----------------------------------------------------

def test_bare_text_is_the_first_required_key():
    assert parse_tags("/read_file/ src/main.py //read_file/") == {
        "action": "read_file", "path": "src/main.py"}
    assert parse_tags("/grep/ TODO //grep/") == {"action": "grep", "query": "TODO"}
    assert parse_tags("/end_conversation/ All done. //end_conversation/") == {
        "action": "end_conversation", "message": "All done."}
    assert parse_tags("/internal_response/ Found it on line 4. //internal_response/") == {
        "action": "internal_response", "response": "Found it on line 4."}


def test_bash_shorthand_is_its_command():
    assert parse_tags("/bash/ python -m pytest -q //bash/") == {
        "action": "bash", "command": "python -m pytest -q"}


def test_slashes_in_a_shorthand_body_never_open_a_tag():
    assert parse_tags("/read_file/ src/agent/x.py //read_file/")["path"] == "src/agent/x.py"
    assert parse_tags("/read_file/ /usr/lib/python3/x.py //read_file/")["path"] == \
        "/usr/lib/python3/x.py"
    assert parse_tags("/bash/ /usr/bin/env python -c x //bash/")["command"] == \
        "/usr/bin/env python -c x"


def test_a_key_is_a_tag_whose_closer_comes_before_the_actions_own():
    """`//usr/` appearing LATER in the reply -- here inside a file:/// URL in
    the next block -- does not make `/usr/` a key of the first one."""
    got = parse_tags("/read_file/ /usr/share/doc/x //read_file/\n"
                     "/end_conversation/ It is at file:///usr/share/doc/x //end_conversation/")
    assert got == {"actions": [
        {"action": "read_file", "path": "/usr/share/doc/x"},
        {"action": "end_conversation", "message": "It is at file:///usr/share/doc/x"}]}


def test_a_tag_inside_a_shorthand_body_is_text():
    got = parse_tags("/end_conversation/ Use /path/ inside /read_file/ next time. "
                     "//end_conversation/")
    assert got["message"] == "Use /path/ inside /read_file/ next time."


def test_a_shorthand_message_can_span_lines():
    got = parse_tags("/end_conversation/\nDone.\n\n- one\n- two\n//end_conversation/")
    assert got["message"] == "Done.\n\n- one\n- two"


def test_the_legacy_names_are_actions_with_their_old_first_key():
    assert parse_tags("/respond/ response //respond/") == {"action": "respond", "message": "response"}
    assert parse_tags("/done/ ok //done/") == {"action": "done", "message": "ok"}
    assert parse_tags("/done/ //done/") == {"action": "done"}
    assert parse_tags("/announce/ Reading //announce/") == {"action": "announce", "message": "Reading"}
    assert parse_tags("/search_files/ todo //search_files/") == {"action": "search_files", "query": "todo"}
    assert parse_tags("/find_text/ TODO //find_text/") == {"action": "find_text", "query": "TODO"}
    assert parse_tags("/run_file/ x.py //run_file/") == {"action": "run_file", "path": "x.py"}
    assert parse_tags("/run_python/ x.py //run_python/") == {"action": "run_python", "path": "x.py"}
    assert parse_tags("/respond/ /message/ hi //message/ /final/ false //final/ //respond/") == {
        "action": "respond", "message": "hi", "final": False}


def test_the_legacy_names_reach_the_compatibility_net_unchanged():
    import agent_actions
    obj = agent_actions.adopt_verb(parse_tags("/respond/ All set //respond/"))
    assert obj == {"action": "end_conversation", "message": "All set"}
    obj = agent_actions.adopt_verb(parse_tags("/search_files/ todo //search_files/"))
    assert obj == {"action": "grep", "query": "todo", "ignore_case": True}


def test_an_empty_action_has_no_keys():
    assert parse_tags("/git_status/ //git_status/") == {"action": "git_status"}
    assert parse_tags("/git_status///git_status/") == {"action": "git_status"}
    assert parse_tags("/git_status/\n\n//git_status/") == {"action": "git_status"}


def test_bare_text_in_an_action_with_no_first_key_is_refused():
    refused("/git_status/ please //git_status/", "/git_status/", "takes no bare value")


def test_bare_text_where_the_first_key_is_a_list_is_refused():
    refused("/multi_tool/ read everything //multi_tool/", "calls", "/calls/", "/read_file/")
    refused("/write_files/ a.py //write_files/", "files", "/files/", "/item/")


def test_loose_text_between_key_tags_is_refused():
    refused("/read_file/ /path/ a.py //path/ and then //read_file/", "loose", "/read_file/")
    refused("/read_file/ /path/ a.py //path/ /progress/ x //progress/ oops //read_file/",
            "oops")


def test_a_closer_where_a_key_was_expected_is_refused():
    refused("/read_file/ /path/ a.py //path/ //path/ //read_file/", "//path/")


def test_action_is_never_a_key():
    refused("/read_file/ /action/ grep //action/ /path/ a //path/ //read_file/", "/action/")


def test_a_key_given_twice_is_refused():
    refused("/read_file/ /path/ a //path/ /path/ b //path/ //read_file/", "twice", "/path/")


# --- rule 4: leaves, lists and objects ---------------------------------------

def test_a_leaf_recognises_nothing_but_its_own_closer():
    content = "x = '/path/ not a tag //other/ nor this /content/'\nurl = 'https://a/b'\n"
    got = parse_tags("/write_file/ /path/ a.py //path/ /content/\n" + content +
                     "//content/ //write_file/")
    assert got["content"] == content


def test_the_suffix_lets_a_value_contain_its_own_closer():
    got = parse_tags("/write_file/ /path/ a.md //path/ /content:a/\n"
                     "Close a content tag with //content/.\n//content:a/ //write_file/")
    assert got["content"] == "Close a content tag with //content/.\n"
    got = parse_tags("/write_file:x/ /path/ a //path/ /content/ see //write_file/ //content/ //write_file:x/")
    assert got["content"] == "see //write_file/"


def test_a_suffixed_tag_is_closed_only_by_the_same_suffix():
    message = refused("/read_file/ /progress/ p //progress/ /path:a/ x //path/ //read_file/",
                      "/path:a/", "//path:a/")
    assert "/path:b/" in message           # the hint offers a suffix not in use
    # As the FIRST token, a tag whose closer never comes before the action's
    # is not a key at all (rule 2): the body is the shorthand value.
    assert parse_tags("/read_file/ /path:a/ x //path/ //read_file/")["path"] == \
        "/path:a/ x //path/"


def test_a_leaf_that_never_closes_names_the_suffix_mechanism():
    message = refused("/write_file/ /path/ a //path/ /content/ x //write_file/",
                      "/content/", "//content/", "/content:a/", "//content:a/")
    assert "suffix" in message


def test_an_action_that_never_closes_is_refused_by_name():
    refused("/read_file/ /path/ a.py //path/", "/read_file/", "//read_file/")
    refused("/end_conversation/ half a sentence", "/end_conversation/")
    refused("/read_file/ /path/ a.py", "/read_file/")


def test_a_list_of_strings():
    got = parse_tags("/ask_user/ /question/ Which? //question/ "
                     "/options/ /item/ yes //item/ /item/ no, not now //item/ //options/ //ask_user/")
    assert got == {"action": "ask_user", "question": "Which?", "options": ["yes", "no, not now"]}


def test_a_list_of_objects():
    got = parse_tags("/write_files/ /files/\n/item/ /path/ a.py //path/ /content/\nx\n//content/ //item/\n"
                     "/item/\n/path/ b.py //path/\n/content/ y //content/\n//item/\n//files/ //write_files/")
    assert got == {"action": "write_files", "files": [
        {"path": "a.py", "content": "x\n"}, {"path": "b.py", "content": "y"}]}


def test_an_empty_list_and_an_empty_object():
    assert parse_tags("/git_commit/ /message/ m //message/ /paths/ //paths/ //git_commit/")[
        "paths"] == []
    got = parse_tags("/spawn_agent/ /task/ t //task/ /constraints/ //constraints/ //spawn_agent/")
    assert got["constraints"] == {}


def test_a_list_item_that_starts_like_a_tag_is_still_text():
    got = parse_tags("/git_commit/ /message/ m //message/ "
                     "/paths/ /item/ /usr/local/x.py //item/ /item/ src/y.py //item/ //paths/ //git_commit/")
    assert got["paths"] == ["/usr/local/x.py", "src/y.py"]


def test_an_item_whose_first_key_is_named_item():
    got = parse_tags("/review_agenda/ /operation/ update //operation/ /updates/ "
                     "/item/ /item/ 3 //item/ /status/ done //status/ //item/ "
                     "/item/ /item/ A4 //item/ /status/ skipped //status/ /note/ n //note/ //item/ "
                     "//updates/ //review_agenda/")
    assert got == {"action": "review_agenda", "operation": "update", "updates": [
        {"item": 3, "status": "done"}, {"item": "A4", "status": "skipped", "note": "n"}]}


def test_a_container_holding_bare_text_is_refused_naming_item():
    refused("/git_commit/ /message/ m //message/ /paths/ a.py b.py //paths/ //git_commit/",
            "/paths/", "/item/")
    refused("/spawn_agent/ /task/ t //task/ /constraints/ read only //constraints/ //spawn_agent/",
            "/constraints/")


def test_a_list_that_mixes_items_with_other_tags_is_refused():
    refused("/ask_user/ /question/ q //question/ /options/ /item/ a //item/ /b/ x //b/ "
            "//options/ //ask_user/", "mixes", "/item/")


def test_calls_holds_actions_named_by_their_tags_with_templates_and_shorthand():
    got = parse_tags("/multi_tool/ /calls/\n"
                     "/read_lines/ /for_each/ **/*.py //for_each/ /start/ 1 //start/ /end/ 6 //end/ //read_lines/\n"
                     "/read_file/ README.md //read_file/\n"
                     "/grep/ TODO //grep/\n"
                     "//calls/ /limit/ 300 //limit/ //multi_tool/")
    assert got == {"action": "multi_tool", "limit": 300, "calls": [
        {"action": "read_lines", "for_each": "**/*.py", "start": 1, "end": 6},
        {"action": "read_file", "path": "README.md"},
        {"action": "grep", "query": "TODO"}]}


def test_calls_holding_text_is_refused():
    refused("/multi_tool/ /calls/ read it all //calls/ //multi_tool/", "/calls/", "action tag")
    refused("/multi_tool/ /calls/ /read_file/ a //read_file/ loose //calls/ //multi_tool/",
            "/calls/", "loose")


def test_constraints_and_report_are_objects_and_their_keys_are_typed():
    got = parse_tags("/spawn_agent/ /task/ Write the parser //task/ /constraints/ "
                     "/read_only/ true //read_only/ /timeout_seconds/ 600 //timeout_seconds/ "
                     "/report/ /file_list/ true //file_list/ /diff/ False //diff/ /summary/ TRUE //summary/ //report/ "
                     "//constraints/ //spawn_agent/")
    assert got == {"action": "spawn_agent", "task": "Write the parser", "constraints": {
        "read_only": True, "timeout_seconds": 600,
        "report": {"file_list": True, "diff": False, "summary": True}}}
    import agent_delegation
    constraints, refusal = agent_delegation.parse(got["constraints"])
    assert refusal == "" and constraints.timeout_seconds == 600, refusal


def test_events_entries_are_objects_and_their_message_is_not_the_reply():
    got = parse_tags("/end_conversation/ /message/ Green. //message/ /events/ "
                     "/item/ /type/ test //type/ /message/ Ran 173 tests //message/ //item/ "
                     "//events/ //end_conversation/")
    assert got["events"] == [{"type": "test", "message": "Ran 173 tests"}]
    assert got["message"] == "Green."


# --- rule 6: types ------------------------------------------------------------

def test_every_bool_key_reads_true_and_false_in_any_case():
    for key in sorted(P.BOOL_KEYS):
        for word, expected in (("true", True), ("FALSE", False), (" True\n", True)):
            got = parse_tags("/grep/ /query/ q //query/ /%s/%s//%s/ //grep/" % (key, word, key))
            assert got[key] is expected, (key, word, got)


def test_a_bool_key_holding_anything_else_is_refused_by_name():
    for key in sorted(P.BOOL_KEYS):
        for word in ("yes", "1", "maybe", ""):
            refused("/grep/ /query/ q //query/ /%s/ %s //%s/ //grep/" % (key, word, key),
                    "/%s/" % key, "true or false")


def test_every_int_key_reads_an_integer_literal():
    for key in sorted(P.INT_KEYS):
        for word, expected in (("12", 12), ("-3", -3), ("+7", 7), (" 40 \n", 40)):
            got = parse_tags("/read_lines/ /path/ a //path/ /%s/%s//%s/ //read_lines/" % (key, word, key))
            assert got[key] == expected and type(got[key]) is int, (key, word, got)


def test_an_int_key_holding_other_text_is_left_for_the_handler():
    for word in ("12abc", "1.5", "S2", "A2", "", "1_000", "\u0661\u0662"):
        got = parse_tags("/plan/ /operation/ update //operation/ /step/ %s //step/ //plan/" % word)
        assert got["step"] == word.strip(), (word, got)


def test_ids_and_every_other_key_stay_text():
    assert parse_tags("/agent_result/ /id/ 3 //id/ //agent_result/") == {"action": "agent_result", "id": "3"}
    assert parse_tags("/read_file/ /path/ 42 //path/ //read_file/")["path"] == "42"
    assert parse_tags("/web_search/ /query/ true //query/ //web_search/")["query"] == "true"


def test_null_is_not_special():
    assert parse_tags("/read_file/ /path/ null //path/ //read_file/")["path"] == "null"
    assert parse_tags("/grep/ /query/ q //query/ /limit/ null //limit/ //grep/")["limit"] == "null"


def test_types_apply_inside_lists_too():
    got = parse_tags("/plan/ /operation/ update //operation/ /steps/ "
                     "/item/ /step/ 1 //step/ /status/ completed //status/ //item/ //steps/ //plan/")
    assert got["steps"] == [{"step": 1, "status": "completed"}]


# --- rule 5: batches ----------------------------------------------------------

def test_consecutive_blocks_are_a_batch_in_order():
    got = parse_tags("/create_folder/ reports //create_folder/\n"
                     "/write_file/ /path/ reports/q3.md //path/ /content/\n# Q3\n//content/ //write_file/\n"
                     "/end_conversation/ Created reports/q3.md. //end_conversation/")
    assert got == {"actions": [
        {"action": "create_folder", "path": "reports"},
        {"action": "write_file", "path": "reports/q3.md", "content": "# Q3\n"},
        {"action": "end_conversation", "message": "Created reports/q3.md."}]}


def test_reply_level_keys_attach_to_the_batch():
    got = parse_tags("/progress/ Two reads //progress/ /read_file/ a //read_file/ "
                     "/read_file/ b //read_file/ /next_step/ Fix it //next_step/")
    assert got == {"actions": [{"action": "read_file", "path": "a"},
                               {"action": "read_file", "path": "b"}],
                   "progress": "Two reads", "next_step": "Fix it"}


def test_reply_level_keys_merge_into_a_single_action():
    got = parse_tags("/end_conversation/ Done. //end_conversation/\n/next_step/ Run the tests //next_step/"
                     "\n/events/ /item/ /type/ success //type/ /message/ ok //message/ //item/ //events/")
    assert got == {"action": "end_conversation", "message": "Done.", "next_step": "Run the tests",
                   "events": [{"type": "success", "message": "ok"}]}


def test_a_reply_level_key_given_inside_and_beside_one_action_is_refused():
    refused("/read_file/ /path/ a //path/ /progress/ x //progress/ //read_file/ /progress/ y //progress/",
            "/progress/", "both")
    refused("/progress/ x //progress/ /progress/ y //progress/ /read_file/ a //read_file/",
            "twice")


def test_the_actions_wrapper_is_a_batch_even_around_one_action():
    assert parse_tags("/actions/ /git_status/ //git_status/ //actions/") == {
        "actions": [{"action": "git_status"}]}
    got = parse_tags("/actions/\n/git_push/ //git_push/\n/end_conversation/ Pushed. //end_conversation/\n"
                     "//actions/\n/next_step/ Open a PR //next_step/")
    assert got == {"actions": [{"action": "git_push"},
                               {"action": "end_conversation", "message": "Pushed."}],
                   "next_step": "Open a PR"}


# --- rule 8: what is not a tag -------------------------------------------------

def test_text_outside_every_tag_is_ignored():
    got = parse_tags("Sure, here it is:\n\n/read_file/ a.py //read_file/\n\nThat should do it.")
    assert got == {"action": "read_file", "path": "a.py"}


def test_a_reply_with_no_tags_is_none():
    assert parse_tags("I fixed the parser and the tests pass.") is None
    assert parse_tags("") is None
    assert parse_tags("TCP/IP/UDP and and/or are not tags") is None
    assert parse("I fixed the parser and the tests pass.") is None


def test_an_unknown_action_with_a_closer_reaches_validation():
    assert parse_tags("/frobnicate/ /path/ a //path/ //frobnicate/") == {
        "action": "frobnicate", "path": "a"}


def test_parse_tags_refuses_what_is_not_text():
    try:
        parse_tags(None)
    except ProtocolError:
        return
    raise AssertionError("None was read as a reply")


# --- detection and JSON --------------------------------------------------------

def test_detect():
    assert detect('{"action":"read_file","path":"x"}') == JSON
    assert detect('  \n {"actions":[]}') == JSON
    assert detect("/read_file/ x //read_file/") == TAGS
    assert detect("\n  /end_conversation/ hi //end_conversation/") == TAGS
    assert detect("I fixed it.") is None
    assert detect("") is None and detect("   ") is None and detect(None) is None
    assert detect('Sure: {"action":"git_status"}') == JSON
    assert detect("Sure: /git_status/ //git_status/") == TAGS
    assert detect("Use /plan/ to plan.") is None
    assert detect("I would write {braces} here") is None
    assert detect('First /git_status/ //git_status/ then {"action":"git_push"}') == TAGS
    assert detect('First {"action":"git_push"} then /git_status/ //git_status/') == JSON
    assert detect("//end_conversation/ a stray closer is not a reply") is None


def test_parse_reads_json_with_a_brace_inside_a_string():
    reply = '{"action":"write_file","path":"a.py","content":"d = {\\"k\\": \\"}\\"}\\n"} trailing'
    assert parse(reply) == {"action": "write_file", "path": "a.py", "content": 'd = {"k": "}"}\n'}


def test_parse_reads_json_after_prose():
    assert parse('Here you go: {"action":"git_status"} -- done') == {"action": "git_status"}


def test_a_json_error_is_quoted():
    bad = '{"action":"read_file",}'
    try:
        json.loads(bad)
    except ValueError as error:
        said = str(error)            # worded differently across Python versions
    try:
        parse(bad)
    except ProtocolError as error:
        assert "not valid JSON" in str(error) and said in str(error), str(error)
    else:
        raise AssertionError("bad JSON was read")
    try:
        parse('{"action":"read_file","path":"a"')
    except ProtocolError as error:
        assert "never closes" in str(error)
    else:
        raise AssertionError("unclosed JSON was read")


def test_parse_reads_tags_after_prose():
    assert parse("Sure.\n/end_conversation/ All done. //end_conversation/\nBye") == {
        "action": "end_conversation", "message": "All done."}
    try:
        parse("/read_file/ /path/ a //path/")
    except ProtocolError as error:
        assert "/read_file/" in str(error)
    else:
        raise AssertionError("an unclosed action was read")


# --- render -------------------------------------------------------------------

def test_render_json_is_the_compact_form_the_prompt_uses():
    assert render({"action": "read_file", "path": "x"}, JSON) == '{"action":"read_file","path":"x"}'
    assert render({"action": "end_conversation", "message": "caf\u00e9"}, "json") == \
        '{"action":"end_conversation","message":"caf\u00e9"}'
    assert example({"action": "git_status"}, JSON) == '{"action":"git_status"}'


def test_render_inline_explicit_keys_and_the_messaging_shorthand():
    assert render({"action": "read_file", "path": "src/main.py"}) == \
        "/read_file/ /path/ src/main.py //path/ //read_file/"
    assert render({"action": "end_conversation", "message": "All done."}) == \
        "/end_conversation/ All done. //end_conversation/"
    assert render({"action": "send_message", "message": "Reading."}) == \
        "/send_message/ Reading. //send_message/"
    assert render({"action": "internal_response", "response": "Found."}) == \
        "/internal_response/ Found. //internal_response/"
    # Not the shorthand: another verb, a second key, or a body that starts like a tag.
    assert render({"action": "bash", "command": "ls"}) == "/bash/ /command/ ls //command/ //bash/"
    assert render({"action": "end_conversation", "message": "x", "next_step": "y"}) == \
        "/end_conversation/ /message/ x //message/ /next_step/ y //next_step/ //end_conversation/"
    assert render({"action": "end_conversation", "message": "/path/ goes here //path/"}) == \
        "/end_conversation/ /message/ /path/ goes here //path/ //message/ //end_conversation/"
    assert render({"action": "git_status"}) == "/git_status/ //git_status/"


def test_render_goes_to_block_form_for_a_newline_or_a_long_line():
    written = render({"action": "write_file", "path": "a.py", "content": "x = 1\n"})
    assert written == "/write_file/\n/path/ a.py //path/\n/content/\nx = 1\n//content/\n//write_file/"
    written = render({"action": "read_file", "path": "a" * 120})
    assert written == "/read_file/\n/path/ %s //path/\n//read_file/" % ("a" * 120)
    for line in render({"action": "grep", "query": "q", "progress": "p" * 60, "glob": "*.py"}).split("\n"):
        assert len(line) <= P.INLINE_WIDTH, line


def test_render_writes_types_and_lists():
    obj = {"action": "grep", "query": "x", "regex": True, "ignore_case": False, "context": 2}
    assert example(obj) == ("/grep/ /query/ x //query/ /regex/ true //regex/ "
                            "/ignore_case/ false //ignore_case/ /context/ 2 //context/ //grep/")
    # 113 columns, so render puts one key on each line.
    assert render(obj) == ("/grep/\n/query/ x //query/\n/regex/ true //regex/\n"
                           "/ignore_case/ false //ignore_case/\n/context/ 2 //context/\n//grep/")
    round_trips(obj)
    round_trips({"action": "ask_user", "question": "Which?", "options": ["a", "b"]})


def test_render_picks_a_suffix_when_a_value_holds_its_closer():
    obj = {"action": "write_file", "path": "a.md", "content": "End it with //content/ please\n"}
    written = render(obj)
    assert "/content:a/" in written and "//content:a/" in written
    round_trips(obj)
    # The closer completed by the closer's own first slash.
    round_trips({"action": "write_file", "path": "a", "content": "x//content"})
    written = render({"action": "write_file", "path": "a", "content": "line\nx//content"})
    assert "/content:a/" in written, written
    round_trips({"action": "write_file", "path": "a", "content": "line\nx//content"})
    round_trips({"action": "write_file", "path": "a", "content": "x//CONTENT/ and //content:a/"})
    round_trips({"action": "end_conversation", "message": "Say //end_conversation/ to end."})
    round_trips({"action": "write_file", "path": "a", "content": "tail //write_file/\n"})


def test_render_one_action_batch_uses_the_wrapper_and_many_do_not():
    one = {"actions": [{"action": "git_status"}]}
    assert render(one) == "/actions/ /git_status/ //git_status/ //actions/"
    round_trips(one)
    many = {"actions": [{"action": "git_status"}, {"action": "end_conversation", "message": "ok"}],
            "next_step": "Commit it"}
    assert render(many) == ("/git_status/ //git_status/\n/end_conversation/ ok //end_conversation/\n"
                            "/next_step/ Commit it //next_step/")
    assert example(many) == ("/git_status/ //git_status/ /end_conversation/ ok //end_conversation/ "
                             "/next_step/ Commit it //next_step/")
    round_trips(many)
    round_trips({"actions": []})


def test_example_is_one_line_unless_a_value_cannot_be():
    long = {"action": "end_conversation", "message": "word " * 40 + "end", "next_step": "Next"}
    assert "\n" not in example(long)
    round_trips(long)
    assert "\n" in example({"action": "write_file", "path": "a", "content": "x\ny"})


def test_render_values_that_need_care_round_trip():
    for value in ("", " leading", "\tleading tab", "\nleading newline", "trailing newline\n",
                  "two\n\n", "a/b/c", "https://host/a//b", "/usr/bin/x", "/item/ not a tag",
                  "100%", "caf\u00e9 \u2014 \U0001F600", "{\"json\": true}", "line\r\nline"):
        obj = {"action": "write_file", "path": "a", "content": value}
        written = render(obj)
        back = parse_tags(written)
        assert back["content"] == value.replace("\r\n", "\n"), (value, written, back)


def test_render_refuses_what_tags_cannot_say():
    for bad in ({"action": "read_file", "path": None},
                {"action": "read_file", "path": ["a", "b"]},
                {"action": "read_file", "path": {"a": 1}},
                {"action": "grep", "query": "q", "regex": "maybe"},
                {"action": "bad name"},
                {"action": "read_file", "bad key": "x"},
                {"action": "git_commit", "message": "m", "paths": [["a"]]},
                {"actions": [{"action": "git_status"}], "extra": 1},
                {"path": "x"}, {}, "not an object",
                {"action": "multi_tool", "calls": ["read everything"]}):
        try:
            written = render(bad)
        except ProtocolError:
            continue
        raise AssertionError("rendered %r as %r" % (bad, written))


def test_render_refuses_an_unknown_protocol():
    for call in (render, example):
        try:
            call({"action": "git_status"}, "xml")
        except ProtocolError as error:
            assert "tags" in str(error) and "json" in str(error)
        else:
            raise AssertionError("xml was accepted")


def test_render_writes_a_scalar_item_that_starts_like_a_tag_or_refuses():
    round_trips({"action": "git_commit", "message": "m", "paths": ["/usr/x.py", "/item/x"]})
    try:
        render({"action": "git_commit", "message": "m", "paths": ["/usr/ x //usr/"]})
    except ProtocolError:
        pass
    else:
        raise AssertionError("a list entry that reads back as an object was rendered")


def _sample(key):
    samples = {
        "files": [{"path": "app/main.py", "content": "print(\"start\")\n"},
                  {"path": "app/util.py", "content": "def add(a, b):\n    return a + b\n"}],
        "calls": [{"action": "read_lines", "for_each": "**/*.py", "start": 1, "end": 6},
                  {"action": "grep", "query": "TODO"}],
        "options": ["Node", "Python"],
        "content": "def f():\n    return '//not a closer' + \"/x/\"\n",
        "search": "timeout=5", "replace": "timeout=30\n",
        "message": "Fixed it -- see https://example.com/a//b and `x/y`.",
        "operation": "create",
    }
    if key in samples:
        return samples[key]
    if key in P.INT_KEYS:
        return 12
    if key in P.BOOL_KEYS:
        return True
    return "value of %s with a/b/c" % key


def test_every_registered_action_round_trips():
    import agent_config
    extras = {
        "grep": {"regex": True, "ignore_case": False, "context": 2, "limit": 50, "glob": "*.py"},
        "read_lines": {"start": 10, "end": 40},
        "git_commit": {"paths": ["src/a.py", "/abs/b.py"], "all": False},
        "spawn_agent": {"constraints": {"read_only": True, "timeout_seconds": 600,
                                        "report": {"file_list": True, "diff": False, "summary": True}},
                        "model": "m"},
        "plan": {"steps": ["Read", {"title": "Fix", "status": "pending"}], "step": 2, "after": 1},
        "review_agenda": {"updates": [{"item": 3, "status": "done"}, {"item": "A4", "status": "done"}],
                          "items": ["One", "Two"]},
        "verify": {"level": 3, "full": True, "timeout": 120, "paths": ["a.py"]},
        "wait_for_agents": {"ids": ["1", "2"], "timeout": 30},
        "web_search": {"max_results": 5, "recency": "week"},
        "remember": {"tags": ["build", "ci"]},
        "delete_folder": {"recursive": True},
        "replace_across": {"apply": False, "glob": "**/*.py"},
        "tree": {"depth": 2, "limit": 300},
        "bash": {"command": "pytest -q", "timeout": 60, "id": "3"},
        "end_conversation": {"next_step": "Run it", "events": [{"type": "test", "message": "ran"}]},
    }
    for action, keys in agent_config.REQUIRED_KEYS.items():
        obj = {"action": action}
        for key in keys:
            obj[key] = _sample(key)
        obj.update(extras.get(action, {}))
        obj["progress"] = "Doing %s" % action
        round_trips(obj)
        round_trips({key: value for key, value in obj.items() if key != "progress"})


# --- the prompt's own examples ----------------------------------------------

# The scrape the spec names: a whole line that is one JSON action.
_SPEC_LINE = re.compile(r'^\s*(?:You emit:|BAD:|GOOD:|WRONG:|RIGHT:)?\s*(\{"action".*\})\s*$')
# The wider one the transliteration rewrites: any example line, including the
# batches, the "Then:" walkthroughs and lines with commentary after the object.
_EXAMPLE_START = re.compile(r'^[ \t]*(?:You emit:|BAD:|GOOD:|WRONG:|RIGHT:|Then:)?[ \t]*(?=\{"actions?")')


def prompt_constants():
    import agent_prompt
    import agent_subprompts
    for module in (agent_prompt, agent_subprompts):
        for name, value in sorted(vars(module).items()):
            if isinstance(value, str) and not name.startswith("__"):
                yield module.__name__, name, value


def objects_on(line):
    """The JSON action objects on one line, as text, left to right."""
    found, pos = [], 0
    while True:
        at = line.find('{"action', pos)
        end = P._object_end(line, at) if at >= 0 else -1
        if end < 0:
            return found
        found.append(line[at:end])
        pos = end


def example_objects():
    """(where, object text) for every JSON action on an example line."""
    found = []
    for module, name, value in prompt_constants():
        for line in value.split("\n"):
            if _EXAMPLE_START.match(line):
                found.extend(("%s.%s" % (module, name), text) for text in objects_on(line))
    return found


def test_every_example_in_the_prompts_round_trips_through_tags():
    found = example_objects()
    spec = [line for _, _, value in prompt_constants() for line in value.split("\n")
            if _SPEC_LINE.match(line)]
    assert len(found) >= 150, len(found)
    assert len(spec) >= 130, len(spec)
    texts_found = {text for _, text in found}
    for line in spec:
        # The spec's pattern is greedy, so on a "{...} then {...}" line its
        # group is several objects; each of them is among what was found.
        on_line = objects_on(_SPEC_LINE.match(line).group(1))
        assert on_line and all(text in texts_found for text in on_line), line
    for where, text in found:
        obj = json.loads(text)
        assert parse_tags(render(obj)) == obj, (where, text, render(obj))
        assert parse_tags(example(obj)) == obj, (where, text, example(obj))
        assert render(obj, JSON) == text, (where, text)


def test_transliterate_rewrites_example_lines_and_nothing_else():
    rewritten = 0
    for module, name, value in prompt_constants():
        lines = value.split("\n")
        assert transliterate(value) == "\n".join(transliterate(line) for line in lines)
        for line in lines:
            out = transliterate(line)
            if not _EXAMPLE_START.match(line):
                assert out == line, (name, line)
                continue
            rewritten += 1
            objects = [json.loads(text) for text in objects_on(line)]
            assert objects, line
            assert '{"action' not in out, (name, out)
            # The label and any commentary are text outside the tags, which
            # the reader ignores, so the rewritten line reads back as exactly
            # the objects that were on it.
            back = parse_tags(out)
            want = objects[0] if len(objects) == 1 else {"actions": objects}
            assert back == want, (name, line, out, back)
    assert rewritten >= 150, rewritten


def test_transliterate_keeps_the_label_and_the_indent():
    assert transliterate('  You emit: {"action":"read_file","path":"x"}') == \
        "  You emit: /read_file/ /path/ x //path/ //read_file/"
    assert transliterate('{"action":"git_status"}') == "/git_status/ //git_status/"
    written = transliterate('  GOOD: {"action":"write_file","path":"a","content":"x\\n"}')
    assert written == "  GOOD:\n/write_file/\n/path/ a //path/\n/content/\nx\n//content/\n//write_file/"
    assert parse_tags(written) == {"action": "write_file", "path": "a", "content": "x\n"}


def test_transliterate_rewrites_objects_inside_commentary_in_place():
    line = '  BAD: {"action":"end_conversation","message":"Added it."} Anything else?   (two objects)'
    assert transliterate(line) == \
        "  BAD: /end_conversation/ Added it. //end_conversation/ Anything else?   (two objects)"
    line = '{"action":"git_status"} then {"action":"read_file","path":"a"}'
    assert transliterate(line) == \
        "/git_status/ //git_status/ then /read_file/ /path/ a //path/ //read_file/"


def test_transliterate_leaves_what_it_cannot_read_alone():
    for line in ('You emit: {"action":"read_file","path":}',
                 'You emit: {"action":"read_file","path":null}',
                 "plain prose with {braces} and /tags/",
                 "",
                 '  {"action" is how a JSON reply starts'):
        assert transliterate(line) == line, line


# --- streaming ----------------------------------------------------------------

STREAMED = [
    "/end_conversation/ Hello world //end_conversation/",
    "/end_conversation/\n/message/ Hi, see https://x.org/a//b //message/\n"
    "/next_step/ Run it //next_step/\n//end_conversation/",
    "/write_file/\n/path/ a.py //path/\n/content/\nx = 1\n//content/\n/progress/ Writing a.py //progress/\n"
    "//write_file/\n/end_conversation/ Wrote a.py.   //end_conversation/",
    "/actions/ /read_file/ /path/ a //path/ /progress/ p //progress/ //read_file/ "
    "/send_message/ Done //send_message/ //actions/ /progress/ top //progress/",
    "/end_conversation/ /usr/lib is the path //end_conversation/",
    "/end_conversation/\r\nLine one\r\nLine two\r\n//end_conversation/",
    "Sure!\n/read_file/ a.py //read_file/\nthen\n/send_message/ x/y //send_message/",
    "/multi_tool/ /calls/ /send_message/ hidden //send_message/ /read_file/ a //read_file/ "
    "//calls/ /progress/ Fanning out //progress/ //multi_tool/",
    "/respond/ caf\u00e9 \u2014 \U0001F600 \u4f60\u597d //respond/",
    "/end_conversation/ /message:a/ Say //message/ to end. //message:a/ //end_conversation/",
]


def test_the_same_events_whole_or_one_character_at_a_time():
    for reply in STREAMED:
        whole = merged(feed(reply, len(reply))[1])
        for size in (1, 2, 3, 5, 8):
            assert merged(feed(reply, size)[1]) == whole, (size, reply)


def test_the_result_is_the_parse_of_the_whole_text():
    for reply in STREAMED:
        parser, events = feed(reply, 1)
        assert parser.result() == parse_tags(reply), reply
        assert parser.error is None
        assert parser.raw == reply


def test_text_streams_for_the_shorthand_and_for_a_message_key():
    _, events = feed(STREAMED[0])
    assert texts(events) == "Hello world"
    assert merged(events)[0] == ("action", "end_conversation")
    _, events = feed(STREAMED[1])
    assert texts(events) == "Hi, see https://x.org/a//b"
    _, events = feed("/git_commit/ /message/ Fix it //message/ //git_commit/")
    assert texts(events) == "Fix it"          # as the JSON parser does
    _, events = feed("/done/ All set. //done/")
    assert texts(events) == "All set."


def test_text_does_not_stream_for_anything_else():
    for reply in ("/write_file/ /path/ secret.txt //path/ /content/ api-key-here //content/ //write_file/",
                  "/internal_response/ Found it. //internal_response/",
                  "/read_file/ /path/ a //path/ /progress/ Reading //progress/ //read_file/",
                  "/end_conversation/ /events/ /item/ /type/ t //type/ /message/ Ran 3 //message/ "
                  "//item/ //events/ //end_conversation/",
                  STREAMED[7]):
        _, events = feed(reply)
        assert texts(events) == "", (reply, events)


def test_a_batch_of_blocks_streams_each_message_and_the_wrapper_too():
    reply = ("/write_file/ /path/ a.txt //path/ /content/ xyz //content/ //write_file/\n"
             "/end_conversation/ Wrote the file. //end_conversation/")
    _, events = feed(reply)
    assert texts(events) == "Wrote the file."
    _, events = feed(STREAMED[3])
    assert texts(events) == "Done"


def test_progress_and_next_step_are_reported_as_their_leaves_close():
    reply = "/read_file/ /path/ a.txt //path/ /progress/ Reading a.txt now. //progress/ //read_file/"
    parser = StreamingTagParser()
    progress_at = object_at = None
    for index, char in enumerate(reply):
        for kind, value in parser.feed(char):
            if kind == "progress":
                progress_at = index
                assert value == "Reading a.txt now."
            elif kind == "object":
                object_at = index
    assert progress_at == reply.index("//progress/") + len("//progress/") - 1
    assert object_at == len(reply) - 1
    _, events = feed(STREAMED[1])
    assert values(events, "next_step") == ["Run it"]
    assert values(events, "progress") == []


def test_progress_streams_beside_blocks_and_in_top_level_blocks_but_not_in_a_wrapper():
    _, events = feed(STREAMED[3])
    assert values(events, "progress") == ["top"]
    _, events = feed("/read_file/ a //read_file/ /read_file/ /path/ b //path/ /progress/ B //progress/ "
                     "//read_file/ /progress/ both //progress/ /next_step/ Next //next_step/")
    assert values(events, "progress") == ["B", "both"]
    assert values(events, "next_step") == ["Next"]
    _, events = feed("/write_file/ /path/ d.json //path/ /content/ "
                     "/progress/ from the file //progress/ //content/ /progress/ Writing //progress/ "
                     "//write_file/")
    assert values(events, "progress") == ["Writing"]


def test_an_empty_progress_or_next_step_is_not_reported():
    for reply in ("/read_file/ /path/ a //path/ /progress/ //progress/ //read_file/",
                  "/end_conversation/ /message/ ok //message/ /next_step/   //next_step/ "
                  "//end_conversation/",
                  "/progress/ //progress/ /read_file/ a //read_file/"):
        _, events = feed(reply, 1)
        assert values(events, "progress") == [] and values(events, "next_step") == [], reply


def test_an_action_is_reported_the_moment_its_tag_is_open():
    reply = "/write_file/ /path/ a //path/ /content/\n" + "x\n" * 50 + "//content/ //write_file/"
    parser = StreamingTagParser()
    seen = []
    for index, char in enumerate(reply):
        for kind, value in parser.feed(char):
            seen.append((index, kind, value))
    assert seen[0] == (len("/write_file/") - 1, "action", "write_file")
    _, events = feed(STREAMED[7])
    assert values(events, "action") == ["multi_tool", "send_message", "read_file"]


def test_one_object_per_top_level_block():
    _, events = feed(STREAMED[2])
    objects = [json.loads(value) for value in values(events, "object")]
    assert objects == parse_tags(STREAMED[2])["actions"]
    _, events = feed(STREAMED[3])
    assert [json.loads(value) for value in values(events, "object")] == [
        {"actions": parse_tags(STREAMED[3])["actions"]}]


def test_partial_input_never_raises():
    # Feeding one character at a time already reads every prefix; feeding
    # each prefix whole as well reads it from a fresh parser's point of view.
    for reply in STREAMED + ["/read_file/ /path/ a //pa", "///x//y/ /z:/ //", "/" * 20,
                             "/write_file/ /path/ a //path/ /x/ stray //write_file/",
                             "/multi_tool/ /calls/ text //calls/ //multi_tool/ /done/ ok //done/",
                             "/read_file/ /action/ x //action/ //read_file/ /git_status/ hi //git_status/"]:
        feed(reply, 1)[0].result()
        for end in range(len(reply) + 1):
            parser = StreamingTagParser()
            parser.feed(reply[:end])
            parser.result()


def test_a_bad_value_is_refused_by_the_result_not_by_the_stream():
    reply = ("/grep/ /query/ q //query/ /regex/ maybe //regex/ /progress/ Searching //progress/ //grep/\n"
             "/end_conversation/ Next. //end_conversation/")
    parser, events = feed(reply, 1)
    assert values(events, "progress") == ["Searching"]
    assert texts(events) == "Next."
    assert parser.result() is None and "/regex/" in parser.error


def test_a_malformed_block_does_not_stop_the_live_view_of_the_next_one():
    reply = ("/write_file/ /path/ a //path/ stray words /content/ x //content/ //write_file/\n"
             "/end_conversation/ Still shown. //end_conversation/")
    parser, events = feed(reply, 1)
    assert texts(events) == "Still shown."
    assert parser.result() is None and "loose" in parser.error


def test_an_unterminated_leaf_has_no_result_and_says_why():
    parser, events = feed("/end_conversation/ /progress/ Halfway there. //progress/ /message/ cut", 1)
    assert values(events, "progress") == ["Halfway there."]
    assert texts(events) == "cut"
    assert parser.result() is None
    assert "/message/" in parser.error and "suffix" in parser.error
    parser, _ = feed("no tags here at all")
    assert parser.result() is None and parser.error is None


def test_no_protocol_text_leaks_into_the_text_stream():
    """At every point of the stream, what has been shown is the beginning of
    the message the finished reply holds -- never a tag, a slash of a closer,
    or the spaces in front of one."""
    reply = ("/end_conversation/\n/progress/ Writing //progress/\n/message/ All set,   \n  done.  \t"
             "//message/\n/events/ /item/ /type/ t //type/ /message/ m //message/ //item/ //events/\n"
             "/next_step/ Ship it //next_step/\n//end_conversation/")
    final = parse_tags(reply)["message"]
    assert final == "All set,   \n  done."
    for size in (1, 2, 4, 9, len(reply)):
        parser = StreamingTagParser()
        shown = ""
        for index in range(0, len(reply), size):
            shown += texts(parser.feed(reply[index:index + size]))
            assert final.startswith(shown), (size, shown)
        assert shown == final, (size, shown)


def test_a_multiline_code_block_and_unicode_survive_exactly():
    message = "Here you go:\n```py\nprint('h\u00e9llo \U0001F600')\n```\nDone \u2014 \u4f60\u597d"
    reply = render({"action": "end_conversation", "message": message})
    for size in (1, 3, len(reply)):
        parser, events = feed(reply, size)
        assert texts(events) == message, size
        assert parser.result()["message"] == message


def test_a_message_with_a_double_slash_that_is_not_a_closer_streams_intact():
    message = "See https://docs.python.org//3/ and //message (not a closer) and a//b."
    reply = "/end_conversation/ /message/ %s //message/ //end_conversation/" % message
    parser, events = feed(reply, 1)
    assert texts(events) == message
    assert parser.result()["message"] == message


def test_spaces_and_a_half_closer_are_held_back_until_they_are_decided():
    parser = StreamingTagParser()
    shown = texts(parser.feed("/end_conversation/ Hello   //end_conv"))
    assert shown == "Hello", shown
    shown += texts(parser.feed("ersation/"))
    assert shown == "Hello"
    parser = StreamingTagParser()
    shown = texts(parser.feed("/end_conversation/ a  //"))
    assert shown == "a"
    shown += texts(parser.feed("b"))         # "//b" can no longer be the closer
    assert shown == "a  //b"
    shown += texts(parser.feed(" c //end_conversation/"))
    assert shown == "a  //b c"


def test_a_crlf_split_across_chunks_is_one_newline():
    parser = StreamingTagParser()
    events = []
    for chunk in ("/end_conversation/\r", "\nOne\r", "\nTwo\r", "\n//end_conversation/"):
        events.extend(parser.feed(chunk))
    assert texts(events) == "One\nTwo"
    assert parser.result()["message"] == "One\nTwo"


def test_unicode_split_one_character_at_a_time():
    reply = "/respond/ /progress/ caf\u00e9 \u2014 checking //progress/ /message/ ok //message/ //respond/"
    parser, events = feed(reply, 1)
    assert values(events, "progress") == ["caf\u00e9 \u2014 checking"]
    assert texts(events) == "ok"
    assert parser.result()["message"] == "ok"


def test_empty_chunks_are_ignored():
    parser = StreamingTagParser()
    events = []
    for chunk in ("", "/done/", "", " ok //done/", ""):
        events.extend(parser.feed(chunk))
    assert texts(events) == "ok"
    assert parser.feed("") == []
