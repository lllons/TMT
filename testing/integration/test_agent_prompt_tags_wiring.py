"""The assembled prompts, taught in tags and in JSON.

`testing/unit/test_agent_prompt_tags.py` protects the constants one at a time.
This module builds the five prompts a model can be given -- the main agent's
with nothing authorised and with /plan /review /verify, the worker's, the note
agent's and the reviewer's -- in a real (temporary) workspace, in both
protocols, and asks what a model would actually read.

WHAT IS BEING GUARDED, IN ORDER OF HOW QUIETLY IT CAN FAIL.

The JSON prompt is the original. Everything written against it before there
was a second protocol still holds, so asking for "json" must return the prompt
that existed before, and the TAGS prompt must not leak into it. The two are
cached apart and the setting is read per call, so a session that changes its
reply format between two turns is taught the new one on the next request.

A tags prompt that still contains a JSON example line teaches the format it
forbids. A model that is shown `{"action":...}` as GOOD, forty times, answers
in it. So no line of any tags prompt may BEGIN an example with a JSON object,
and every tag example a prompt contains is read back through the parser,
validated, and checked against what `agent_protocol.render` writes -- with a
floor on how many there are, because a scrape that finds nothing proves
nothing.

The word JSON is a leak of the other protocol. It is allowed in a short list
of places, enumerated in `ALLOWED_JSON_LINES`, and nowhere else: the one
strict sentence, the one entry on the WHAT NEVER WORKS list, the name of a
file format, and the reviewer's verdict, which is a JSON object in either
protocol because it is the review result and not the wire format.

The background prompts reuse the main agent's rules and cite them by number.
Those numbers and what they cite have to hold in the tags text too, or a worker
is told that a rule about tags is a rule about an ending.

The prompt sizes are printed in the assertion message of the size test. The
tags prompt is longer, because a block of tags is longer than the compact JSON
it replaces, and the owner tracks what that costs.

The main prompt is built in a fixture workspace because it INLINES the
workspace: built over this repository it would contain every JSON mention in
every module, and a test about where that word appears would be about the
repository instead of the prompt.
"""

import json
import re

import agent_capabilities as C
import agent_prompt
import agent_protocol
import agent_review
import agent_subprompts

from test_agent_orchestration import Project
from test_agent_prompt_tags import (JSON_EXAMPLE_LINE, SHORTHAND_EXAMPLES, check_example,
                                    numbered_rules, tag_examples, written_by_render)
from test_agent_reply_format import ReplyFormat

FILES = {"src/a.py": "x = 1\n", "README.md": "# demo\n"}
EVERYTHING = "Build it /plan /review /verify"

PROMPT_NAMES = ("main", "main (all three)", "worker", "note", "review")

# A floor per prompt, below what each measured when this was written (111, 124,
# 75, 67 and 76 blocks). They are not the point; a scrape that came back empty
# passing every assertion below is, and these are what stop it.
FLOORS = {"main": 100, "main (all three)": 120, "worker": 65, "note": 60, "review": 65}


def build(protocol):
    """{name: prompt} for all five, built for `protocol`, in the open Project."""
    return {
        "main": agent_prompt.get_system_prompt(protocol=protocol),
        "main (all three)": agent_prompt.get_system_prompt(C.Capabilities(EVERYTHING),
                                                           protocol=protocol),
        "worker": agent_subprompts.worker_prompt(protocol=protocol),
        "note": agent_subprompts.note_prompt(protocol=protocol),
        "review": agent_subprompts.review_prompt(protocol=protocol),
    }


def with_project(test):
    """Run `test(box)` in a fresh temporary workspace and always tear it down."""
    box = Project(files=FILES)
    try:
        return test(box)
    finally:
        box.close()


def tokens(text):
    """The estimate every figure in this repository's notes uses."""
    return len(text) // 4


# --- the JSON prompt is the one there always was ---------------------------------------

def test_the_json_main_prompt_is_the_original_and_holds_the_json_constants_verbatim():
    def check(box):
        for caps in (None, C.Capabilities(EVERYTHING)):
            prompt = agent_prompt.get_system_prompt(caps, protocol="json")
            for name in ("HEADER", "SPEAKING_RULES", "OUTPUT_RULES", "ANSWERING_EXAMPLES",
                         "ACTION_REFERENCE", "BASH_REFERENCE", "PROGRESS_RULES", "GIT_RULES"):
                assert getattr(agent_prompt, name) in prompt, name
            assert prompt.endswith(agent_prompt.REMINDER)
            assert "It goes to a JSON parser" in prompt
            assert "Output EXACTLY ONE JSON object" in prompt
            assert tag_examples(prompt) == [], "a tag example in the JSON prompt"
            assert agent_prompt.HEADER_TAGS not in prompt
            assert agent_prompt.OUTPUT_RULES_TAGS not in prompt
            assert agent_prompt.REMINDER_TAGS not in prompt
    with_project(check)


def test_the_json_background_prompts_are_the_originals_too():
    def check(box):
        worker = agent_subprompts.worker_prompt(protocol="json")
        note = agent_subprompts.note_prompt(protocol="json")
        review = agent_subprompts.review_prompt(protocol="json")
        for prompt, header, examples in (
                (worker, agent_subprompts.WORKER_HEADER, agent_subprompts.WORKER_EXAMPLES),
                (note, agent_subprompts.NOTE_HEADER, agent_subprompts.NOTE_EXAMPLES),
                (review, agent_subprompts.REVIEWER_HEADER, agent_subprompts.REVIEWER_EXAMPLES)):
            assert header in prompt and examples in prompt
            assert agent_prompt.OUTPUT_RULES in prompt
            assert agent_prompt.ACTION_REFERENCE in prompt
            assert prompt.endswith(agent_subprompts.SUBPROMPT_REMINDER)
            assert tag_examples(prompt) == []
            assert "tag parser" not in prompt
        assert agent_subprompts.REVIEW_RESULT_REFERENCE in review
    with_project(check)


# --- two protocols, two caches ------------------------------------------------------

def test_json_and_tags_prompts_are_cached_apart_and_each_is_served_again():
    def check(box):
        first = build("json")
        second = build("tags")
        for name in PROMPT_NAMES:
            assert first[name] != second[name], name
        again = build("json")
        also = build("tags")
        for name in PROMPT_NAMES:
            assert again[name] is first[name], "%s: json was rebuilt or replaced" % name
            assert also[name] is second[name], "%s: tags was rebuilt or replaced" % name
    with_project(check)


def test_the_setting_chooses_the_prompt_when_no_protocol_is_given():
    def check(box):
        explicit = {"tags": build("tags"), "json": build("json")}
        for chosen in ("tags", "json", "tags"):
            with ReplyFormat(chosen):
                live = {
                    "main": agent_prompt.get_system_prompt(),
                    "main (all three)": agent_prompt.get_system_prompt(C.Capabilities(EVERYTHING)),
                    "worker": agent_subprompts.worker_prompt(),
                    "note": agent_subprompts.note_prompt(),
                    "review": agent_subprompts.review_prompt(),
                }
            for name in PROMPT_NAMES:
                assert live[name] == explicit[chosen][name], (chosen, name)
    with_project(check)


def test_an_explicit_protocol_beats_the_setting_in_both_directions():
    def check(box):
        with ReplyFormat("json"):
            assert agent_prompt.get_system_prompt(protocol="tags") \
                .endswith(agent_prompt.REMINDER_TAGS)
            assert agent_subprompts.worker_prompt(protocol="tags") \
                .endswith(agent_subprompts.SUBPROMPT_REMINDER_TAGS)
        with ReplyFormat("tags"):
            assert agent_prompt.get_system_prompt(protocol="json") \
                .endswith(agent_prompt.REMINDER)
            assert agent_subprompts.worker_prompt(protocol="json") \
                .endswith(agent_subprompts.SUBPROMPT_REMINDER)
    with_project(check)


def test_changing_the_workspace_drops_every_protocols_prompt_not_just_one():
    """invalidate_prompt empties the main cache and every subprompt cache; a
    cache that kept the other protocol's entry would describe a directory that
    has moved, to whichever reply format asked second."""
    def check(box):
        before = build("json"), build("tags")
        assert all("zzz_new.py" not in text for group in before for text in group.values())
        box.write("src/zzz_new.py", "y = 2\n")
        agent_prompt.invalidate_prompt()
        for protocol, group in (("json", build("json")), ("tags", build("tags"))):
            for name, text in group.items():
                assert "zzz_new.py" in text, "%s (%s) did not notice the new file" % (name, protocol)
        assert build("json")["worker"] is not before[0]["worker"]
        assert build("tags")["worker"] is not before[1]["worker"]
    with_project(check)


# --- what a tags prompt contains ---------------------------------------------------------

def test_no_tags_prompt_has_a_line_that_begins_an_example_with_a_json_object():
    def check(box):
        for name, prompt in build("tags").items():
            lines = JSON_EXAMPLE_LINE.findall(prompt)
            assert not lines, "%s prompt has %d JSON example line(s)" % (name, len(lines))
    with_project(check)


def test_every_tag_example_in_every_tags_prompt_is_parsed_validated_and_exact():
    def check(box):
        grand = 0
        for name, prompt in build("tags").items():
            blocks = tag_examples(prompt)
            assert len(blocks) >= FLOORS[name], \
                "%s prompt: only %d tag examples found, floor %d" % (name, len(blocks), FLOORS[name])
            for block in blocks:
                check_example(block)
                if block not in SHORTHAND_EXAMPLES:
                    assert written_by_render(block), \
                        "%s prompt: an example is not what render writes:\n%s" % (name, block)
            grand += len(blocks)
        assert grand >= 120, grand
    with_project(check)


def test_no_json_example_was_lost_in_the_translation():
    """Each JSON example line becomes at least one tag block, so a tags prompt
    with FEWER blocks than the JSON prompt has example lines dropped one."""
    def check(box):
        json_prompts, tags_prompts = build("json"), build("tags")
        for name in PROMPT_NAMES:
            lines = len(JSON_EXAMPLE_LINE.findall(json_prompts[name]))
            blocks = len(tag_examples(tags_prompts[name]))
            assert lines > 0, name
            assert blocks >= lines, \
                "%s: %d JSON example lines but only %d tag blocks" % (name, lines, blocks)
    with_project(check)


def test_no_tags_prompt_still_quotes_the_json_syntax_the_swaps_replaced():
    """Every sentence a swap rewrote must be gone from every tags prompt, in the
    assembled text and not only in the constant -- a background prompt that
    quoted the shared TOOL_CHOICE_RULES in its JSON form would teach
    `"apply":true` to a tags reader, and no example line would show it."""
    def check(box):
        olds = [old for swaps in agent_prompt._TAG_PROSE.values() for old, _ in swaps]
        olds += [old for old, _ in agent_subprompts._HEADER_SWAPS]
        olds += ['carries ONE JSON object as its "response" string.',
                 "It goes to a JSON parser", "one JSON object only",
                 "Start with { and end with }", "INSIDE THE JSON:", "Still JSON, and"]
        json_prompts, tags_prompts = build("json"), build("tags")
        for old in olds:
            assert any(old in prompt for prompt in json_prompts.values()), \
                "no JSON prompt holds %r, so nothing proves it is gone from the tags ones" % old[:60]
            for name, prompt in tags_prompts.items():
                assert old not in prompt, "%s prompt still says %r" % (name, old[:60])
    with_project(check)


# The lines on which the word may appear in a tags prompt, and why. Everything
# else that says it is the other protocol leaking into this one.
ALLOWED_JSON_LINES = (
    ("2. NO JSON, NO code fences", "the one strict sentence of the contract"),
    ("  BAD: a JSON object, {", "the first entry of WHAT NEVER WORKS"),
    ("read_document - keys: path.", "a file format the verb converts"),
    ("Your single internal_response carries ONE JSON object",
     "the reviewer's verdict, which is JSON in either protocol"),
)


def test_the_word_json_appears_in_a_tags_prompt_only_where_it_is_allowed():
    def check(box):
        expected = {
            "main": 3, "main (all three)": 3, "worker": 2, "note": 2, "review": 3}
        for name, prompt in build("tags").items():
            lines = [line for line in prompt.split("\n") if re.search(r"\bJSON\b", line)]
            for line in lines:
                assert any(line.startswith(start) for start, _ in ALLOWED_JSON_LINES), \
                    "%s prompt says JSON where it should not:\n%s" % (name, line[:200])
            assert len(lines) == expected[name], \
                "%s prompt: %d lines say JSON, expected %d:\n%s" % (
                    name, len(lines), expected[name], "\n".join(l[:80] for l in lines))
            # Lower case says it too, but only as the name of a file.
            leftover = re.sub(r"package\.json", "", prompt)
            others = [line for line in leftover.split("\n")
                      if re.search(r"json", line, re.I) and not re.search(r"\bJSON\b", line)]
            assert not others, "%s prompt: %r" % (name, others[:3])
        # And the verdict sentence is in the review prompt and nowhere else.
        prompts = build("tags")
        for name in ("main", "main (all three)", "worker", "note"):
            assert "ONE JSON object" not in prompts[name], name
        assert "ONE JSON object" in prompts["review"]
    with_project(check)


def test_the_json_prompt_says_json_often_so_the_count_above_means_something():
    """The same count over the JSON prompts, so a pattern that matched nothing
    would show up here as a number that is not far larger."""
    def check(box):
        json_, tags = build("json"), build("tags")
        for name in PROMPT_NAMES:
            said_json = len(re.findall(r"\bJSON\b", json_[name]))
            said_tags = len(re.findall(r"\bJSON\b", tags[name]))
            assert said_json >= 8, (name, said_json)
            assert said_tags <= 4 and said_tags * 3 < said_json, (name, said_tags, said_json)
    with_project(check)


def test_the_strict_sentences_are_in_every_tags_prompt():
    def check(box):
        for name, prompt in build("tags").items():
            for needle in ("Reply in TAG BLOCKS and nothing else.",
                           "NO JSON, NO code fences, NO language label",
                           "Text outside a block is thrown away.",
                           "Every tag you open, you close.",
                           "no quotes around it, no commas, no braces, no escaping of any kind",
                           "Never any of these: a code fence around the blocks"):
                assert needle in prompt, "%s prompt lacks: %s" % (name, needle)
            assert agent_prompt.OUTPUT_RULES_TAGS in prompt, name
            assert agent_prompt.OUTPUT_RULES not in prompt, name
        main = build("tags")["main"]
        assert agent_prompt.HEADER_TAGS in main
        assert agent_prompt.SPEAKING_RULES_TAGS in main
        assert agent_prompt.ANSWERING_EXAMPLES_TAGS in main
        assert agent_prompt.PROGRESS_RULES_TAGS in main
        assert agent_prompt.HEADER not in main
        assert agent_prompt.ANSWERING_EXAMPLES not in main
        assert agent_prompt.section("ACTION_REFERENCE", "tags") in main
        assert agent_prompt.ACTION_REFERENCE not in main
        assert agent_prompt.WORKFLOW_RULES_TAGS in main
        assert agent_prompt.WORKFLOW_RULES not in main
    with_project(check)


def test_the_output_rules_in_every_tags_prompt_keep_the_numbers_that_are_cited():
    def check(box):
        for name, prompt in build("tags").items():
            start = prompt.index("=== OUTPUT FORMAT - ABSOLUTE RULES ===")
            end = prompt.index("Never any of these:", start)
            rules = numbered_rules(prompt[start:end])
            assert sorted(rules) == list(range(1, 12)), (name, sorted(rules))
            assert "message" in rules[5] and "end_conversation" in rules[5], (name, rules[5])
            assert "end_conversation" in rules[10], (name, rules[10])
            assert "HAVE to end every task with an end_conversation" in rules[11], (name, rules[11])
    with_project(check)


def test_the_overrides_cite_rules_that_still_say_what_they_are_cited_for_under_tags():
    """What test_agent_orchestration pins for JSON, restated for the tags text,
    and then the things those strings POINT AT: the rule numbers by content."""
    def check(box):
        prompts = build("tags")
        for name in ("worker", "note", "review"):
            prompt = prompts[name]
            assert "=== WHERE THE SHARED RULES DIFFER FOR YOU ===" in prompt, name
            assert "OUTPUT FORMAT rules 10 and 11" in prompt, name
            assert "OUTPUT FORMAT rule 5" in prompt, name
            assert agent_subprompts._SHARED_OVERRIDES in prompt, name
            for heading in ("=== OUTPUT FORMAT - ABSOLUTE RULES ===",
                            "=== EDITING PREFERENCES - FOLLOW IN THIS ORDER ===",
                            "=== CHOOSING A TOOL - ALWAYS TAKE THE NARROWEST ONE ==="):
                assert heading in prompt, (name, heading)
            # What the overrides name by number, read off the tags text.
            editing = numbered_rules(agent_prompt.section("PREFERENCE_RULES", "tags"))
            choosing = numbered_rules(agent_prompt.section("TOOL_CHOICE_RULES", "tags"))
            assert "8 KB" in editing[8] and "already pasted" in editing[8], editing[8]
            assert "8 KB" in choosing[7] and "already pasted" in choosing[7], choosing[7]
        worker = prompts["worker"]
        assert "send_message" in worker and "internal_response" in worker
        assert "Emitting one costs you a step" in worker
    with_project(check)


def test_the_three_background_prompts_say_tag_parser_and_never_a_json_parser():
    def check(box):
        prompts = build("tags")
        for name in ("worker", "note", "review"):
            prompt = prompts[name]
            assert "It goes to a tag parser" in prompt, name
            assert "JSON parser" not in prompt, name
            assert "one JSON object only" not in prompt, name
            assert "the whole contract, and everything else follows from it" in prompt, name
        assert "an internal_response" in prompts["worker"]
    with_project(check)


def test_the_closing_reminder_is_the_protocols_own_in_all_five_prompts():
    def check(box):
        tags, json_ = build("tags"), build("json")
        for name in ("main", "main (all three)"):
            assert tags[name].endswith(agent_prompt.REMINDER_TAGS), name
            assert json_[name].endswith(agent_prompt.REMINDER), name
            assert agent_prompt.REMINDER not in tags[name], name
            assert agent_prompt.REMINDER_TAGS not in json_[name], name
        for name in ("worker", "note", "review"):
            assert tags[name].endswith(agent_subprompts.SUBPROMPT_REMINDER_TAGS), name
            assert json_[name].endswith(agent_subprompts.SUBPROMPT_REMINDER), name
            assert "one JSON object only" not in tags[name], name
        for name, prompt in tags.items():
            assert "Start with { and end with }" not in prompt, name
    with_project(check)


# --- the reviewer's verdict ---------------------------------------------------------------

def _json_verdicts(prompt):
    """The verdict strings the JSON review prompt's examples carry."""
    found = []
    for line in prompt.split("\n"):
        match = re.match(r'\s*(?:Then:\s*)?(\{"action":"internal_response","response":.*\})\s*$', line)
        if match:
            found.append(json.loads(match.group(1))["response"])
    return [text for text in found if text.startswith("{")]


def _tag_verdicts(prompt):
    found = []
    for block in tag_examples(prompt):
        obj = agent_protocol.parse(block)
        if obj.get("action") == "internal_response" and obj["response"].startswith("{"):
            found.append((block, obj["response"]))
    return found


def test_the_reviewers_verdict_is_the_same_json_object_written_raw_in_the_response():
    def check(box):
        original = _json_verdicts(build("json")["review"])
        raw = _tag_verdicts(build("tags")["review"])
        assert len(original) >= 3, len(original)
        # A verdict under tags is character for character the one under JSON:
        # carrying it changed, what it is did not. The tags prompt also holds
        # the BAD ones (a bare status, an unknown one), which are not in the
        # JSON list because they are written with commentary on the line.
        good = [text for _, text in raw if text in original]
        assert sorted(good) == sorted(original), (len(good), len(original))
        statuses = set()
        for text in good:
            result = agent_review.parse_result(text)
            statuses.add(result.stated_verdict)
        assert {"PASS", "FAIL", "PASS_WITH_WARNINGS"} <= statuses, statuses
        # Written raw means there is no second layer to undo: what the tags
        # protocol hands over is itself the object, not a string holding one
        # in escaped form.
        for text in good:
            assert isinstance(json.loads(text), dict)
            # The bare body and an explicit /response/ tag are the same thing,
            # so a reviewer that writes the longer form is not misread.
            explicit = "/internal_response/ /response/ " + text + " //response/ //internal_response/"
            assert agent_protocol.parse(explicit)["response"] == text
    with_project(check)


def test_the_reviewer_is_told_the_verdict_needs_no_escaping_and_is_still_json():
    def check(box):
        review = build("tags")["review"]
        reference = agent_subprompts.REVIEW_RESULT_REFERENCE_TAGS
        assert reference in review
        assert agent_subprompts.REVIEW_RESULT_REFERENCE not in review
        assert "ONE JSON object as its response" in review
        assert "no escaping" in review and "Write that object raw" in review
        assert "internal_response" in review
        # The bad examples teach what a tags verdict must not be.
        assert "/internal_response/ {\"status\":\"PASS\"} //internal_response/" in review
    with_project(check)


# --- the capability sections and the rows spliced into the tool-choice table ------

def test_the_capability_sections_follow_the_authorisation_in_tags_as_in_json():
    def check(box):
        none_ = agent_prompt.get_system_prompt(protocol="tags")
        plan = agent_prompt.get_system_prompt(C.Capabilities("/plan"), protocol="tags")
        everything = agent_prompt.get_system_prompt(C.Capabilities(EVERYTHING), protocol="tags")
        assert "=== THE PLAN - ONE ACTION, SIX OPERATIONS ===" not in none_
        assert "=== CAPABILITIES YOU WERE NOT GIVEN ===" in none_
        assert "=== THE PLAN - ONE ACTION, SIX OPERATIONS ===" in plan
        assert "=== VERIFICATION - ONE ACTION" not in plan
        assert "=== THE PLAN - ONE ACTION, SIX OPERATIONS ===" in everything
        assert "=== VERIFICATION - ONE ACTION" in everything
        assert "=== THE REVIEW - ONE ACTION" in everything
        assert "=== CAPABILITIES YOU WERE NOT GIVEN ===" not in everything
        assert len(everything) > len(plan) > len(none_)
        # The plan's examples are tags: a created plan parses to a list of steps.
        created = [obj for obj in (agent_protocol.parse(b) for b in tag_examples(plan))
                   if obj.get("action") == "plan" and obj.get("operation") == "create"]
        assert created and created[0]["steps"][0] == "Inspect the repository", created
        updates = [obj for obj in (agent_protocol.parse(b) for b in tag_examples(plan))
                   if obj.get("action") == "plan" and "steps" in obj and obj["operation"] == "update"]
        assert updates and updates[0]["steps"][0]["step"] == 2, updates
    with_project(check)


def test_the_rows_and_rules_spliced_into_the_tables_are_in_the_tags_prompt():
    def check(box):
        plan = agent_prompt.get_system_prompt(C.Capabilities("/plan"), protocol="tags")
        for text in (agent_prompt.PLAN_TOOL_ROW, agent_prompt.BASH_TOOL_ROW,
                     agent_prompt.WEB_TOOL_ROW, agent_prompt.IMAGE_TOOL_ROW,
                     agent_prompt.PLAN_BEHAVIOUR_RULE):
            assert text in plan, text
        none_ = agent_prompt.get_system_prompt(protocol="tags")
        assert agent_prompt.PLAN_TOOL_ROW not in none_
        assert agent_prompt.PLAN_BEHAVIOUR_RULE not in none_
        for text in (agent_prompt.BASH_TOOL_ROW, agent_prompt.WEB_TOOL_ROW,
                     agent_prompt.IMAGE_TOOL_ROW):
            assert text in none_, text
    with_project(check)


def test_the_background_prompts_teach_the_same_verbs_in_tags_as_in_json():
    """Isolation is by what each prompt contains, and a protocol switch must not
    move it: the worker has the web and image verbs and nothing it is refused,
    the note agent and the reviewer have neither."""
    def check(box):
        for protocol in ("json", "tags"):
            prompts = build(protocol)
            for name in ("worker", "note", "review"):
                prompt = prompts[name]
                assert "bash" not in prompt, (protocol, name)
                assert "ask_user" not in prompt, (protocol, name)
                assert "spawn_agent" not in prompt, (protocol, name)
                assert "=== THE PLAN" not in prompt, (protocol, name)
            assert "web_search" in prompts["worker"] and "view_image" in prompts["worker"]
            for name in ("note", "review"):
                assert "web_search" not in prompts[name], (protocol, name)
                assert "view_image" not in prompts[name], (protocol, name)
            assert "review_agenda" in prompts["review"] and "review_agenda" not in prompts["worker"]
    with_project(check)


# --- size ------------------------------------------------------------------------------------

def test_the_tags_prompts_are_longer_and_by_how_much_is_in_the_message():
    """Sizes are `len // 4`, the estimate the notes use, over a fixture workspace.
    The owner tracks them, so they are printed in the assertion message and
    written to the output when the test passes."""
    def check(box):
        json_, tags = build("json"), build("tags")
        report = []
        for name in PROMPT_NAMES:
            a, b = tokens(json_[name]), tokens(tags[name])
            report.append("%s: json %d, tags %d (%+d, %+.1f%%)"
                          % (name, a, b, b - a, 100.0 * (b - a) / a))
        message = "prompt sizes in estimated tokens, fixture workspace:\n  " + "\n  ".join(report)
        for name in PROMPT_NAMES:
            a, b = tokens(json_[name]), tokens(tags[name])
            assert a < b < a * 1.35, "%s\n(%s)" % (message, name)
        print(message)
    with_project(check)


# --- validate_action, as the loops will meet it -------------------------------------------

def test_a_reply_with_no_action_is_refused_in_words_that_fit_either_protocol():
    assert agent_prompt.validate_action({"message": "hi"}) == "The reply named no action"
    parsed = agent_protocol.parse("/progress/ thinking //progress/")
    assert agent_prompt.validate_action(parsed) == "The reply named no action"
