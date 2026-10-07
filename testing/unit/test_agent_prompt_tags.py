"""The prompt constants that teach the TAGS wire protocol, one at a time.

TMT reads a reply in tags or in JSON (`agent_protocol`), and the model has to
be TAUGHT whichever one the setting says. The JSON teaching is the original
prompt and is untouched; this module protects the other half -- the `_TAGS`
twins of every constant whose prose is about the wire format, the helper that
chooses between the two, and the one property that makes the whole thing safe
to ship beside a suite written against JSON: asking for "json" returns the
very same objects it always did.

THREE THINGS HERE ARE EASY TO LOSE WITHOUT ANYTHING FAILING.

A teaching example the parser would refuse teaches a mistake. A model learns
the format from the examples far more than from the rules, so every example in
every tags constant is read back through `agent_protocol.parse` and checked
with `validate_action`, and most are checked to be exactly what
`agent_protocol.render` would write -- which is the only thing that stops a
hand-edited example drifting a space away from what the parser accepts.

A rule number is a promise to another module. `agent_subprompts._SHARED_OVERRIDES`
tells every background agent which of the OUTPUT FORMAT rules is not for it BY
NUMBER (5, 10 and 11), so the hand-written `OUTPUT_RULES_TAGS` keeps those
numbers meaning what they meant, and a test reads the rules by content rather
than by position.

A swap that matches nothing is a JSON sentence left in a tags prompt.
`agent_prompt.rewrite` is loud about it, and the table of swaps is tested from
both ends: the sentence must be in the JSON constant (so the swap is real) and
absent from the tags one (so it was applied).

The assembled prompts -- the main agent's with and without the three
capabilities, the worker's, the note agent's and the reviewer's -- are in
testing/integration/test_agent_prompt_tags_wiring.py, because they need a
workspace. The helpers below are imported there by bare stem.
"""

import re

import agent_config
import agent_prompt
import agent_protocol
import agent_subprompts

from test_agent_reply_format import ReplyFormat


# --- finding the examples in a prompt ------------------------------------------

# What the prompt's JSON examples start with, and what a tags prompt must not
# contain: a line that is one of them. A line that merely MENTIONS a JSON
# object, after other words, is not an example and is allowed.
JSON_EXAMPLE_LINE = re.compile(
    r'^\s*(You emit:|BAD:|GOOD:|WRONG:|RIGHT:|Then:)?\s*\{"action', re.M)

_LABEL = re.compile(r"[ \t]*(?:(?:You emit:|BAD:|GOOD:|WRONG:|RIGHT:|Then:)[ \t]*)?")
_OPEN = re.compile(r"/([a-z_]+)/")
# An example never runs this long, so a block that has not closed by then is
# prose that happens to start with an action's name.
_LONGEST_BLOCK_LINES = 80

# Examples a prompt writes in a form `render` never does, on purpose: the
# shorthand the grammar section teaches. Everything else must be exact.
SHORTHAND_EXAMPLES = {"/read_file/ notes.txt //read_file/"}


def tag_examples(text):
    """Every top-level tag block that an example line in `text` writes.

    A block starts where a known action's open tag begins a line -- after an
    optional "You emit:" or "BAD:" style label, or after "then" when one line
    carries several -- and ends at its own closer, which may be on the same
    line or lines further down. Blocks nested inside a block are part of it
    and are not returned again. Prose that merely mentions an action's name
    mid-sentence starts no block, and a block that never closes is skipped
    rather than swallowing the rest of the prompt.
    """
    known = set(agent_config.REQUIRED_KEYS)
    lines = text.split("\n")
    found = []
    index = 0
    while index < len(lines):
        rest = lines[index][_LABEL.match(lines[index]).end():]
        while True:
            opened = _OPEN.match(rest)
            if opened is None or opened.group(1) not in known:
                break
            closer = "//%s/" % opened.group(1)
            chunk, last = rest, index
            while closer not in chunk and last + 1 < len(lines) \
                    and last - index < _LONGEST_BLOCK_LINES:
                last += 1
                chunk += "\n" + lines[last]
            end = chunk.find(closer)
            if end < 0:
                break
            end += len(closer)
            found.append(chunk[:end])
            rest = chunk[end:].strip()
            if rest.startswith("then "):
                rest = rest[5:]
            index = last
        index += 1
    return found


def check_example(block):
    """Parse one example block and validate it; return the object it describes.

    Raises AssertionError naming the block when the parser refuses it or when
    any action in it is missing a required key. A call inside multi_tool that
    carries for_each is a TEMPLATE -- its path arrives when it is expanded --
    so its required keys are not asked of it.
    """
    try:
        obj = agent_protocol.parse(block)
    except agent_protocol.ProtocolError as error:
        raise AssertionError("the parser refuses this example (%s):\n%s" % (error, block))
    assert isinstance(obj, dict), "this example is not an action:\n%s" % block
    entries = obj["actions"] if "actions" in obj else [obj]
    for entry in entries:
        problem = agent_prompt.validate_action(entry)
        assert problem is None, "%s -- in this example:\n%s" % (problem, block)
        for call in entry.get("calls") or []:
            if "for_each" not in call:
                problem = agent_prompt.validate_action(call)
                assert problem is None, "%s -- in a call of this example:\n%s" % (problem, block)
    return obj


def written_by_render(block):
    """Whether `block` is exactly what agent_protocol writes for the object it means."""
    obj = agent_protocol.parse(block)
    return block in (agent_protocol.render(obj), agent_protocol.example(obj))


def numbered_rules(text):
    """{number: text} for the lines of `text` that open a numbered rule."""
    rules = {}
    for line in text.split("\n"):
        found = re.match(r"(\d+)\. (.+)$", line)
        if found:
            rules[int(found.group(1))] = found.group(2)
    return rules


# The constants whose prose is about the wire format, and the twin each one
# has. The reminder is a one-line constant and belongs on the list for the
# same reason the rest do.
CONTRACT = ("HEADER", "SPEAKING_RULES", "OUTPUT_RULES", "ANSWERING_EXAMPLES",
            "WORKFLOW_RULES", "PROGRESS_RULES", "REMINDER")


# --- which protocol, and what a wrong answer does -----------------------------------

def test_the_protocol_is_the_argument_and_otherwise_the_setting_read_now():
    """The setting is read per call, because Settings can change it between two
    turns. An explicit argument wins over it, in both directions."""
    with ReplyFormat("tags"):
        assert agent_prompt.resolve_protocol() == "tags"
        assert agent_prompt.resolve_protocol("json") == "json"
    with ReplyFormat("json"):
        assert agent_prompt.resolve_protocol() == "json"
        assert agent_prompt.resolve_protocol("tags") == "tags"


def test_a_protocol_that_is_neither_raises_rather_than_choosing_one():
    """Case matters too: the words are the setting's own and "TAGS" is not one.
    A quiet fallback would be a JSON prompt in front of a tags reader."""
    for wrong in ("xml", "", "TAGS", "Json", "tag", 3, ("tags",)):
        try:
            agent_prompt.resolve_protocol(wrong)
        except ValueError as error:
            assert "tags" in str(error) and "json" in str(error), str(error)
        else:
            raise AssertionError("%r was accepted as a reply format" % (wrong,))
    for builder in (agent_prompt.get_system_prompt, agent_subprompts.worker_prompt,
                    agent_subprompts.note_prompt, agent_subprompts.review_prompt):
        try:
            builder(protocol="xml")
        except ValueError:
            continue
        raise AssertionError("%s accepted a protocol that does not exist" % builder.__name__)


def test_the_background_builders_take_the_protocol_by_keyword_only():
    """They took no arguments for most of their life, and
    test_agent_ask_wiring still probes them with a positional task and relies on
    the TypeError to fall back. A positional protocol would turn that probe into
    a ValueError about a reply format called 'do the thing'."""
    for builder in (agent_subprompts.worker_prompt, agent_subprompts.note_prompt,
                    agent_subprompts.review_prompt):
        try:
            builder("do the thing")
        except TypeError:
            continue
        raise AssertionError("%s accepted a positional argument" % builder.__name__)


def test_the_two_modules_agree_on_what_the_protocols_are():
    assert tuple(agent_config.PROTOCOLS) == tuple(agent_protocol.PROTOCOLS)
    assert agent_protocol.JSON == "json" and agent_protocol.TAGS == "tags"


# --- the helper that edits prose ----------------------------------------------------

def test_rewrite_applies_each_swap_once_and_in_order():
    out = agent_prompt.rewrite("T", "alpha beta gamma", (("alpha", "A"), ("gamma", "G")))
    assert out == "A beta G"
    assert agent_prompt.rewrite("T", "alpha", ()) == "alpha"


def test_rewrite_is_loud_when_the_sentence_has_moved():
    """Zero copies would be a swap silently dropped, and the JSON wording would
    go on being taught to a tags reader."""
    try:
        agent_prompt.rewrite("THING", "alpha beta", (("gamma", "G"),))
    except AssertionError as error:
        assert "THING" in str(error) and "gamma" in str(error), str(error)
    else:
        raise AssertionError("a swap that matches nothing was accepted")


def test_rewrite_is_loud_when_the_sentence_is_ambiguous():
    """Two copies and the wrong one could be rewritten."""
    try:
        agent_prompt.rewrite("THING", "alpha alpha", (("alpha", "A"),))
    except AssertionError as error:
        assert "2 copies" in str(error), str(error)
    else:
        raise AssertionError("an ambiguous swap was accepted")


def test_a_second_swap_sees_the_first_ones_output():
    """Swaps run in order over one string, not each over the original."""
    assert agent_prompt.rewrite("T", "a", (("a", "b"), ("b", "c"))) == "c"


# --- section(): the JSON half is the old prompt, the tags half is not ------------

def test_the_json_section_is_the_constant_itself():
    """The same OBJECT, which is the strongest form of 'byte for byte': a prompt
    built for json from these cannot differ from the constants it always used."""
    for name in CONTRACT + ("ACTION_REFERENCE", "BASH_REFERENCE", "PLAN_REFERENCE",
                            "PREFERENCE_RULES", "TOOL_CHOICE_RULES", "GIT_RULES"):
        assert agent_prompt.section(name, "json") is getattr(agent_prompt, name), name


def test_the_json_constants_are_untouched_and_still_teach_json():
    """Nothing in the JSON half was edited to make room for the other."""
    assert "It goes to a JSON parser" in agent_prompt.HEADER
    assert "Output EXACTLY ONE JSON object" in agent_prompt.OUTPUT_RULES
    assert 'with "apply":true' in agent_prompt.TOOL_CHOICE_RULES
    assert 'a single "actions" array' in agent_prompt.PREFERENCE_RULES
    assert agent_prompt.REMINDER == \
        "Reminder: reply with one JSON object only. Start with { and end with }."
    assert agent_prompt.validate_action({}) == "The reply named no action"


def test_every_contract_constant_has_a_twin_and_the_twin_is_not_the_original():
    for name in CONTRACT:
        twin = getattr(agent_prompt, name + "_TAGS")
        assert twin and twin != getattr(agent_prompt, name), name
        assert agent_prompt.section(name, "tags") is twin, name


def test_no_contract_twin_contains_a_json_example_line():
    """A line that starts an example with a JSON object would teach the very
    format the prompt forbids. Lines that only mention one are allowed."""
    for name in CONTRACT:
        twin = getattr(agent_prompt, name + "_TAGS")
        bad = JSON_EXAMPLE_LINE.findall(twin)
        assert not bad, "%s_TAGS still has %d JSON example line(s)" % (name, len(bad))


def test_a_section_with_no_twin_is_derived_and_has_its_examples_rewritten():
    """ACTION_REFERENCE is the largest: dozens of examples, none hand-written."""
    json_text = agent_prompt.section("ACTION_REFERENCE", "json")
    tags_text = agent_prompt.section("ACTION_REFERENCE", "tags")
    assert JSON_EXAMPLE_LINE.search(json_text)
    assert not JSON_EXAMPLE_LINE.search(tags_text)
    assert len(tag_examples(tags_text)) >= 40, len(tag_examples(tags_text))
    assert tags_text.startswith("=== ACTIONS - REQUIRED KEYS AND AN EXAMPLE OF EACH ===")


def test_a_derived_section_is_built_once_and_served_again():
    first = agent_prompt.section("GIT_RULES", "tags")
    assert agent_prompt.section("GIT_RULES", "tags") is first


def test_every_section_the_prompts_use_survives_transliteration_without_json_examples():
    names = ("ACTION_REFERENCE", "BASH_REFERENCE", "ASK_REFERENCE", "WEB_REFERENCE",
             "IMAGE_REFERENCE", "ORCHESTRATION_REFERENCE", "PLAN_REFERENCE",
             "PLANNING_RULES", "VERIFY_REFERENCE", "VERIFY_RULES", "CONTEXT_REFERENCE",
             "CONTEXT_RULES", "REVIEW_REFERENCE", "REVIEW_RULES", "DELEGATION_RULES",
             "PREFERENCE_RULES", "TOOL_CHOICE_RULES", "GIT_RULES")
    for name in names:
        text = agent_prompt.section(name, "tags")
        assert not JSON_EXAMPLE_LINE.search(text), name
        assert not re.search(r'"[a-z_]+"\s*:\s*[\["{tf0-9]', text), \
            "%s still quotes a key and a value, in JSON's way" % name


# --- the sentences that quote JSON syntax, swapped one for one --------------------

def test_every_json_syntax_sentence_is_in_the_json_text_and_gone_from_the_tags_text():
    """From both ends: the sentence has to exist (or the swap is imaginary) and
    has to be absent afterwards (or it was never applied)."""
    assert agent_prompt._TAG_PROSE, "the table of swaps is empty"
    for name, swaps in agent_prompt._TAG_PROSE.items():
        json_text = getattr(agent_prompt, name)
        tags_text = agent_prompt.section(name, "tags")
        for old, new in swaps:
            assert json_text.count(old) == 1, (name, old)
            assert old not in tags_text, (name, old)
            assert new in tags_text, (name, new)


def test_the_tag_spellings_of_the_booleans_the_prompt_mentions_are_ones_the_parser_reads():
    """/apply/ and /all/ are boolean keys in agent_protocol; a tag spelling the
    parser did not treat as a bool would be taught and then refused."""
    for key in ("apply", "all", "read_only", "file_list", "diff", "summary"):
        assert key in agent_protocol.BOOL_KEYS, key
    assert "/apply/ true //apply/" in agent_prompt.section("TOOL_CHOICE_RULES", "tags")
    assert "/apply/ true //apply/" in agent_prompt.section("ACTION_REFERENCE", "tags")
    assert "/all/ true //all/" in agent_prompt.section("GIT_RULES", "tags")
    for old in ('"apply":true', '"actions" array', '"all": true', '"operation":"start"'):
        for name in ("ACTION_REFERENCE", "TOOL_CHOICE_RULES", "PREFERENCE_RULES",
                     "GIT_RULES", "BASH_REFERENCE"):
            assert old not in agent_prompt.section(name, "tags"), (name, old)


def test_batches_are_taught_as_several_blocks_in_order():
    rule = numbered_rules(agent_prompt.section("PREFERENCE_RULES", "tags"))[10]
    assert "several action blocks" in rule and "in order" in rule, rule


def test_the_delegation_contract_is_described_in_tags_not_as_an_object():
    text = agent_prompt.section("ORCHESTRATION_REFERENCE", "tags")
    assert "report (holding /file_list/, /diff/ and /summary/" in text
    assert "read_only (true)" in text and "timeout_seconds (1 to 3600)" in text
    assert '"report": {' not in text


# --- OUTPUT_RULES_TAGS: the contract ------------------------------------------------

def test_the_numbers_the_background_prompts_cite_still_mean_what_they_meant():
    """_SHARED_OVERRIDES says OUTPUT FORMAT rule 5 is about the message field and
    rules 10 and 11 are about ending with end_conversation. Asserted by content
    in the TAGS rules, so a renumbering fails here and not in a worker."""
    rules = numbered_rules(agent_prompt.OUTPUT_RULES_TAGS)
    assert sorted(rules) == list(range(1, 12)), sorted(rules)
    assert "message" in rules[5] and "send_message" in rules[5] \
        and "end_conversation" in rules[5], rules[5]
    assert "invisible" in rules[5], rules[5]
    assert "end_conversation" in rules[10] and "cannot or will not" in rules[10], rules[10]
    assert "HAVE to end every task with an end_conversation" in rules[11], rules[11]
    assert "message" in rules[11], rules[11]
    # And the same three, read off the JSON rules, so the two lists cannot
    # drift apart in what they say about the same numbers.
    original = numbered_rules(agent_prompt.OUTPUT_RULES)
    assert "message" in original[5] and "end_conversation" in original[10] \
        and "HAVE to end every task" in original[11]


def test_the_heading_the_overrides_name_is_kept():
    assert agent_prompt.OUTPUT_RULES_TAGS.startswith("=== OUTPUT FORMAT - ABSOLUTE RULES ===\n")
    assert "OUTPUT FORMAT" in agent_subprompts._SHARED_OVERRIDES
    assert "OUTPUT FORMAT rule 5" in agent_subprompts._SHARED_OVERRIDES
    assert "OUTPUT FORMAT rules 10 and 11" in agent_subprompts._SHARED_OVERRIDES


def test_the_tags_contract_is_strict_that_nothing_else_is_a_reply():
    """The owner's words: it 'should not' answer in JSON in the first place.
    Every strict sentence, asserted one at a time because a prompt is the only
    place any of them can live."""
    rules = agent_prompt.OUTPUT_RULES_TAGS
    assert "Reply in TAG BLOCKS and nothing else." in rules
    assert "NO JSON, NO code fences" in rules
    assert "NO prose, greeting, explanation or apology before or after the blocks" in rules
    assert "Text outside a block is thrown away." in rules
    assert "Every tag you open, you close." in rules
    assert "no quotes around it, no commas, no braces, no escaping of any kind" in rules
    assert "Never any of these:" in rules
    assert "a code fence around the blocks" in rules
    assert "a tag that is never closed" in rules
    assert "everything you emit is tag blocks" in agent_prompt.HEADER_TAGS
    assert "text outside the tag blocks" in agent_prompt.HEADER_TAGS


def test_the_tags_contract_states_the_whole_grammar():
    """Open and close, key tags, both forms, the shorthand, lists, calls,
    booleans and numbers, the exact keys and the trailing newline, the suffix,
    batches, and where progress and next_step go."""
    rules = agent_prompt.OUTPUT_RULES_TAGS
    for needle in ("/name/", "//name/", "its keys are tags inside it",
                   "Inline form and block form mean the same thing",
                   "bare text inside an action, with no key tags, is its first required key",
                   "/item/", "inside /calls/ every tag is itself an action",
                   "true and false are the bare words and numbers are bare digits",
                   "there is no null",
                   "the content, search and replace tags",
                   "the newline right after the open tag is dropped",
                   "a file ends with a newline the way a heredoc does",
                   "Every other value is trimmed",
                   "give that pair a suffix: /content:a/ ... //content:a/",
                   "Only //content:a/ ends it",
                   "several blocks in one reply are a batch",
                   "run in order",
                   "plus the three optional keys progress, events and next_step"):
        assert needle in rules, needle
    progress = agent_prompt.PROGRESS_RULES_TAGS
    assert "as tags inside the action" in progress
    assert "one /progress/ or /next_step/ may sit beside the blocks" in progress


def test_block_form_is_shown_for_file_text_where_whitespace_matters():
    """write_file and patch_file in block form, with content, search and
    replace, right in the rules. File text is where escaping used to go wrong."""
    rules = agent_prompt.OUTPUT_RULES_TAGS
    assert "/write_file/\n/path/ src/hello.py //path/\n/content/\ndef main():\n" \
           '    print("Hello")\n//content/\n//write_file/\n' in rules
    assert "/patch_file/\n/path/ src/hello.py //path/\n" \
           '/search/ print("Hello") //search/\n' \
           '/replace/ print("Hello, world") //replace/\n//patch_file/\n' in rules


def test_the_examples_in_the_rules_are_read_by_the_parser_and_written_as_render_writes():
    blocks = tag_examples(agent_prompt.OUTPUT_RULES_TAGS)
    assert len(blocks) == 7, blocks
    for block in blocks:
        check_example(block)
        if block not in SHORTHAND_EXAMPLES:
            assert written_by_render(block), "not what render writes:\n" + block
    assert SHORTHAND_EXAMPLES <= set(blocks)
    shorthand = agent_protocol.parse("/read_file/ notes.txt //read_file/")
    assert shorthand == {"action": "read_file", "path": "notes.txt"}


def test_the_batch_example_is_several_blocks_ending_with_the_ending():
    rules = agent_prompt.OUTPUT_RULES_TAGS
    start = rules.index("/create_folder/")
    batch = rules[start:rules.index("10. If you cannot")]
    parsed = agent_protocol.parse(batch)
    assert [entry["action"] for entry in parsed["actions"]] == \
        ["create_folder", "write_file", "end_conversation"]
    assert parsed["actions"][1]["content"] == "# Q3\n"


# --- the other contract twins --------------------------------------------------------

def test_the_header_says_tag_parser_and_never_a_json_one():
    header = agent_prompt.HEADER_TAGS
    assert "It goes to a tag parser" in header
    assert "JSON" not in header
    assert "You are TMT, a coding agent working inside one workspace folder." in header
    assert "every task ends with an end_conversation action, whatever happened" in header


def test_the_speaking_rules_keep_their_prose_and_change_their_examples():
    tags = agent_prompt.SPEAKING_RULES_TAGS
    assert tags.startswith("=== THE TWO VERBS THAT TALK TO THE USER ===")
    assert "Both send text to the user. Only one of them ends the task." in tags
    assert "/end_conversation/ I am starting the implementation. //end_conversation/" in tags
    assert "/send_message/ Two tests failed; I am fixing them. //send_message/" in tags


def test_the_answering_examples_say_tags_where_they_said_json():
    text = agent_prompt.ANSWERING_EXAMPLES_TAGS
    assert "Still tag blocks, and the task is over once you have said hello." in text
    assert "Refuse inside the tags, with the reason." in text
    assert "Ask inside the tags, and the task ends there" in text
    head = text[:text.index("=== WHAT NEVER WORKS ===")]
    assert "JSON" not in head, "the worked examples still mention JSON"
    assert text.startswith("=== HOW TO ANSWER - WORKED EXAMPLES ===")


def test_what_never_works_leads_with_a_json_object_and_is_written_for_tags():
    never = agent_prompt.ANSWERING_EXAMPLES_TAGS.split("=== WHAT NEVER WORKS ===")[1]
    entries = [line.strip() for line in never.split("\n") if line.strip().startswith("BAD:")]
    assert entries[0].startswith('BAD: a JSON object, {"action":"end_conversation"'), entries[0]
    assert "the wrong format" in entries[0]
    # The JSON object is the FIRST entry and the only one that carries any.
    assert sum('{"action"' in entry for entry in entries) == 1
    assert any("outside tags" in entry for entry in entries)
    assert any("backslash escapes" in entry for entry in entries)
    assert any("no closer" in entry for entry in entries)
    assert never.rstrip().endswith("goes in a send_message.")
    assert not JSON_EXAMPLE_LINE.search(never), \
        "a line that BEGINS with a JSON object is the pattern a tags prompt must not contain"


def test_every_example_in_the_derived_contract_constants_is_parsed_validated_and_exact():
    total = 0
    for name in ("SPEAKING_RULES_TAGS", "ANSWERING_EXAMPLES_TAGS", "WORKFLOW_RULES_TAGS",
                 "PROGRESS_RULES_TAGS"):
        blocks = tag_examples(getattr(agent_prompt, name))
        assert blocks, name
        for block in blocks:
            check_example(block)
            assert written_by_render(block), "%s: not what render writes:\n%s" % (name, block)
        total += len(blocks)
    # 37 when this was written; the floor is there so an empty scrape cannot pass.
    assert total >= 30, total


def test_the_progress_rules_describe_events_as_a_list_of_item_tags():
    text = agent_prompt.PROGRESS_RULES_TAGS
    assert "/events/ - a list of /item/ entries, each holding /type/ and /message/" in text
    assert "{" not in "\n".join(line for line in text.split("\n")
                                if not line.lstrip().startswith("RIGHT:")), \
        "a brace in the progress rules outside a transliterated example"
    # The quoted key names became the tags the model actually writes.
    for old in ('"progress"', '"events"', '"next_step"', '"message"'):
        assert old not in text, old
    for new in ("/progress/", "/events/", "/next_step/", "/message/"):
        assert new in text, new


def test_the_behaviour_rules_say_inside_the_tags_and_name_the_message_tag():
    text = agent_prompt.WORKFLOW_RULES_TAGS
    assert text.startswith("=== BEHAVIOUR ===\n"), "_with_plan_rules anchors on this heading"
    assert "INSIDE THE TAGS: the /message/ of the end_conversation" in text
    assert "JSON" not in text
    assert '"message"' not in text


def test_the_two_anchors_the_plan_rows_are_put_back_at_survive_in_the_tags_text():
    """_with_plan_rules asserts its own anchors and would raise at build time
    if the tags text had moved them; asserted here so it fails in a test."""
    tool_choice = agent_prompt.section("TOOL_CHOICE_RULES", "tags")
    workflow = agent_prompt.section("WORKFLOW_RULES", "tags")
    agent_prompt._with_plan_rules(tool_choice, workflow)
    agent_prompt._with_bash_row(tool_choice)
    agent_prompt._with_web_row(tool_choice)
    agent_prompt._with_image_row(tool_choice)


def test_the_reminder_is_a_tags_reminder_and_says_nothing_of_braces():
    reminder = agent_prompt.REMINDER_TAGS
    assert reminder.startswith("Reminder: reply with tag blocks only.")
    assert "/name/" in reminder and "//name/" in reminder
    assert "{" not in reminder and "JSON" not in reminder
    assert agent_prompt.section("REMINDER", "tags") is reminder
    assert agent_prompt.section("REMINDER", "json") is agent_prompt.REMINDER


# --- the background prompts' own constants -----------------------------------------------

def test_the_three_background_headers_say_tag_parser_under_tags():
    for name in ("WORKER_HEADER", "NOTE_HEADER", "REVIEWER_HEADER"):
        json_text = getattr(agent_subprompts, name)
        tags_text = getattr(agent_subprompts, name + "_TAGS")
        assert "It goes to a JSON parser" in json_text, name
        assert "It goes to a tag parser" in tags_text, name
        assert "JSON parser" not in tags_text and "JSON object" not in tags_text, name
        assert "HOW YOU ARE READ - this is the whole contract" in tags_text, name
        # Everything but that one paragraph is the same text.
        assert tags_text.replace(agent_subprompts._PARSER_PARAGRAPH_TAGS,
                                 agent_subprompts._PARSER_PARAGRAPH) == json_text, name
        assert agent_subprompts._section(name, "json") is json_text
        assert agent_subprompts._section(name, "tags") is tags_text


def test_the_reviewers_verdict_is_still_json_but_is_written_raw_under_tags():
    text = agent_subprompts.REVIEW_RESULT_REFERENCE_TAGS
    assert "ONE JSON object as its response" in text
    assert "Write that object raw" in text
    assert "a tag holds plain text" in text
    assert "no escaping" in text
    assert "/response/ tag" in text
    assert 'as its "response" string' not in text
    # The schema itself is unchanged: it is the review result, whatever carries it.
    json_text = agent_subprompts.REVIEW_RESULT_REFERENCE
    assert text.split("The object:")[1] == json_text.split("The object:")[1]


def test_the_background_example_sets_have_no_json_example_lines_under_tags():
    for name in ("WORKER_EXAMPLES", "NOTE_EXAMPLES", "REVIEWER_EXAMPLES"):
        json_text = getattr(agent_subprompts, name)
        tags_text = getattr(agent_subprompts, name + "_TAGS")
        assert JSON_EXAMPLE_LINE.search(json_text), name
        assert not JSON_EXAMPLE_LINE.search(tags_text), name
        assert "one block at a time" in tags_text and "one object at a time" not in tags_text
        blocks = tag_examples(tags_text)
        assert len(blocks) >= 8, (name, len(blocks))
        for block in blocks:
            check_example(block)
            assert written_by_render(block), "%s: not what render writes:\n%s" % (name, block)


def test_the_background_reminder_is_switched_with_the_protocol():
    assert agent_subprompts.SUBPROMPT_REMINDER == (
        "Reminder: reply with one JSON object only. Start with { and end with }. "
        "Finish with exactly one internal_response.")
    tags = agent_subprompts.SUBPROMPT_REMINDER_TAGS
    assert tags.startswith("Reminder: reply with tag blocks only.")
    assert tags.endswith("Finish with exactly one internal_response.")
    assert "{" not in tags and "JSON" not in tags
    assert agent_subprompts._section("SUBPROMPT_REMINDER", "tags") is tags
    assert agent_subprompts._section("SUBPROMPT_REMINDER", "json") \
        is agent_subprompts.SUBPROMPT_REMINDER


def test_a_background_constant_with_no_twin_is_derived_from_its_examples():
    text = agent_subprompts._section("INTERNAL_RESPONSE_REFERENCE", "tags")
    assert not JSON_EXAMPLE_LINE.search(text)
    assert len(tag_examples(text)) == 2
    assert agent_subprompts._section("INTERNAL_RESPONSE_REFERENCE", "json") \
        is agent_subprompts.INTERNAL_RESPONSE_REFERENCE
    agenda = agent_subprompts._section("REVIEW_AGENDA_REFERENCE", "tags")
    assert not JSON_EXAMPLE_LINE.search(agenda)
    assert len(tag_examples(agenda)) >= 6


# --- validate_action ----------------------------------------------------------------

def test_validate_action_names_no_protocol_when_the_action_is_missing():
    """The sentence reaches a model in either protocol, so it may not say JSON."""
    problem = agent_prompt.validate_action({})
    assert problem == "The reply named no action"
    assert "JSON" not in problem and "tag" not in problem
    assert agent_prompt.validate_action({"action": ""}) == problem
    assert agent_prompt.validate_action({"action": "end_conversation", "message": "x"}) is None
    assert "Unknown action" in agent_prompt.validate_action({"action": "nope"})


# --- the scraper is not vacuous -----------------------------------------------------------

def test_the_scraper_finds_inline_block_labelled_and_multi_block_examples():
    text = "\n".join([
        "prose that mentions /read_file/ mid-sentence is not an example",
        "  /read_file/ /path/ a.py //path/ //read_file/",
        "  BAD: /end_conversation/ x //end_conversation/   (commentary)",
        "You emit:",
        "/write_file/",
        "/path/ a.py //path/",
        "/content/",
        "",
        "body",
        "//content/",
        "//write_file/",
        "/git_status/ //git_status/",
        "RIGHT: /git_status/ //git_status/ then /git_diff/ //git_diff/",
        "a block with no closer, such as /read_file/ /path/ a.py //path/",
        "/read_file/ /path/ never closed //path/",
    ])
    found = tag_examples(text)
    assert found == [
        "/read_file/ /path/ a.py //path/ //read_file/",
        "/end_conversation/ x //end_conversation/",
        "/write_file/\n/path/ a.py //path/\n/content/\n\nbody\n//content/\n//write_file/",
        "/git_status/ //git_status/",
        "/git_status/ //git_status/",
        "/git_diff/ //git_diff/",
    ], found
    assert check_example(found[2])["content"] == "\nbody\n"
    try:
        check_example("/read_file/ //read_file/")
    except AssertionError:
        pass
    else:
        raise AssertionError("an action with no path was accepted as a valid example")
