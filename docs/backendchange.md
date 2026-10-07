# Backend change: a tag protocol beside JSON

Progress log for converting the model's wire protocol from JSON-only to a
user-selectable choice of **tags** (default) or **json**. Newest entries at the
bottom of each section. Nothing in `app/` is read or touched by this work.

## Status

| Step | What | State | Restore point |
|---|---|---|---|
| 0 | Understand the code; write this plan; baseline commit of the pending catalogue files | done 2026-10-07 | `fb4e5f5`, tag `restore-tags-0` |
| 1 | `agent_protocol.py`: grammar, parser, serializer, streaming parser, tests | done 2026-10-07 | see Step 1 entry |
| 2 | Setting + Settings row + `/config` row + refresh sites + runner isolation + docs | done 2026-10-07 | `679fd34`, tag `restore-tags-2` |
| 3 | Wire `agent_model`, both loops, session carry, correction strings | not started | - |
| 4 | Prompts: tag contract + transliterated examples, cache keys, subprompts | not started | - |
| 5 | Embedded JSON hints in refusal/result strings made protocol-aware | not started | - |
| 6 | Full suite, live API runs under both protocols, measurements | not started | - |

Nothing is called complete until the whole suite is green AND a real-API run
under tags has done real work end to end.

---

## Step 0: understanding (2026-10-07)

Five read-only research agents (Sonnet) traced the protocol; findings below are
theirs, spot-checked by me.

### How a reply becomes an action today

- `agent_model.ask_model` ALWAYS returns a **string that is a JSON object**: the
  model's own object text (first balanced `{...}` from `_extract_json`), or one
  TMT made up (`_made_up` -> `tmt_synthetic` marker; `_prose_reply` ->
  `tmt_prose` marker). There is no separate parsed return.
- Exactly **two sites** turn that string into a dict: `TMT.py:1930` and
  `agent_worker.py:980` (`json.loads(raw)`). Everything downstream reads a dict:
  `adopt_verb`, `validate_action` (dict in, error string out), `execute_action`,
  the three gates, `declared_events`, `agent_multi`.
- `raw` is also: appended verbatim as the assistant turn in history; compared
  for the circuit breaker (3 identical replies); passed to `record_reply`.
- **Streaming**: `StreamingActionParser` is a character-level JSON state machine
  emitting `("action", name)`, `("text", chars)` for top-level `message` only,
  `("progress", s)`, `("next_step", s)` (top level only), `("object", json)`.
  The live box (`agent_live_renderer`) never sees JSON: it is fed only the
  decoded `message` characters. `TMT.stream_handler` and the worker's
  `_StreamSink` consume the events by name.
- **Provider JSON mode**: `agent_config.USE_JSON_MODE`/`_json_mode_ok` ->
  `response_format: json_object` (OpenRouter/OpenAI) and
  `responseMimeType: application/json` (Gemini); Anthropic has none. Gated at
  two blocks in `agent_model` (:611, :691). **Must be off under tags** or the
  provider forces JSON back.
- **Synthesised JSON elsewhere**: `agent_session.Turn.messages()` re-wraps the
  carried answer as `{"action":"end_conversation","message":...}` (the only
  session site). The reviewer's VERDICT is JSON nested inside
  `internal_response.response` (`agent_review.parse_result`), a separate
  payload from the wire protocol.
- **Model-facing strings that say "JSON"**: `TMT._UNREADABLE_FEEDBACK`, the
  prose/batch/invalid complaints (:1975, :1990, :2125, :2134), `_ACTION_RAISED`
  typing advice; `agent_worker._UNREADABLE`, `_PROSE_FEEDBACK`, `_AGENDA_MISSING`
  (embeds a JSON example), stop sentences; `agent_multi._ENTRY_NOT_OBJECT`,
  `_LOOP_VERBS`; `validate_action`'s "Missing 'action' key in JSON".
- **JSON examples embedded in refusal/result strings** (~30 sites): agent_bash,
  agent_plan, agent_review, agent_verify, agent_reviewbot, agent_file_ops,
  agent_delegation, agent_actions. Under tags these would teach the wrong shape.
- **Typed values**: handlers read `recursive`, `ignore_case`, `full` by
  truthiness, so a tag value `false` arriving as the string "false" would be
  TRUE. `read_lines` coerces ints itself. Lists/dicts are read as-is
  (`files`, `calls`, `options`, `steps`, `events`, `updates`, `constraints`).

### Where the prompt teaches JSON

- `agent_prompt.py`: **135** `{"action"` example lines (+8 batches);
  `agent_subprompts.py`: **55** (+1). Contract prose is concentrated in
  `HEADER`, `OUTPUT_RULES`, `ANSWERING_EXAMPLES`, `WORKFLOW_RULES`, the three
  background headers (`WORKER_HEADER`, `NOTE_HEADER`, `REVIEWER_HEADER`) and two
  closing "Reminder: reply with one JSON object only" lines.
- `get_system_prompt(capabilities=None, context=None)` caches on
  `(tuple(allowed), context hash)`: **no protocol dimension**. The three
  subprompt caches are single-slot, unkeyed.
- `_SHARED_OVERRIDES` cites OUTPUT FORMAT rules **5, 10, 11** by number
  (pinned by `test_agent_orchestration`), so a tag `OUTPUT_RULES` must keep
  those numbers meaning the same things.
- 14 tests scrape `{"action"...}` lines out of prompt constants and
  `json.loads` them; ~5 tests pin JSON header phrases; `test_agent_bash:993`
  uses the JSON reminder line as a slice anchor.

### How the suite drives the model

- 2,794 tests in 74 files. Every scripted-reply seam hands in a **JSON string**
  (`Turn`, `drive_session`, nine worker `Replies` helpers); none hands in a
  dict. ~200 tests go through those seams; ~45 go through `agent_model`'s own
  parser via a fake transport (`test_agent_stream`, `test_agent_threading`).
- `run_tests.py` has one runner-level isolation hook (`isolate_checkpoints`)
  mirrored in `testing/conftest.py`. That is the right place to pin the suite's
  protocol explicitly and hermetically.
- No CLI filter; a scratchpad runner importing named stems is how to run a
  subset (recipe recorded by the research agent, reproduced in Step 1 notes).

### Working-tree state at the start

Uncommitted from an earlier session (the free-model catalogue replacement):
`agent_config.py`, `agent_models.py`, `docs/configuration.md`,
`testing/unit/test_agent_menu.py`. Untracked: `app/` (other session, never
staged here), `gui-report/`, `DESIGN_PRINCIPLES.md.ignore`. Branch `alpha`.
Configured model: `nvidia/nemotron-3-ultra-550b-a55b:free` on OpenRouter.

---

## Design proposal (awaiting the owner's answers)

### The grammar

Open tag `/name/`, close tag `//name/`. `name` is `[A-Za-z_][A-Za-z0-9_]*`.

```
/read_file/ /path/ src/main.py //path/ /progress/ Reading the entry point //progress/ //read_file/
```

Block form is the same thing with newlines:

```
/write_file/
/path/ src/hello.py //path/
/content/
def hello():
    return "hi"
//content/
/progress/ Writing the greeting //progress/
//write_file/
```

Rules:

1. **The action is the outer tag.** Its keys are nested tags. Any key may be
   inline or block form; the parser accepts both.
2. **Shorthand for the common case**: bare text directly inside an action tag,
   with no key tags at all, is the action's FIRST REQUIRED KEY
   (`/end_conversation/ All done. //end_conversation/` is message;
   `/read_file/ src/a.py //read_file/` is path; `bash` maps to `command`).
   The decision is made on the first non-whitespace token, so a shorthand body
   may contain slashes and paths freely.
3. **Leaf whitespace**: inline, surrounding spaces are stripped. Block form
   (a newline right after the opening tag) drops that one newline and KEEPS
   the newline before a closer that sits on its own line, so file content ends
   with a newline the way a heredoc does. Put the closer on the last line to
   omit it.
4. **Lists** use `/item/`: `/options/ /item/ yes //item/ /item/ no //item/ //options/`.
   An item with key tags inside is an object (`files`, `events`, `updates`).
   Inside `/calls/` (multi_tool) each child tag IS an action named by its tag.
   Container keys are a fixed set by name (`files, calls, events, steps,
   options, paths, tags, items, updates, constraints, report`); every other
   key is a text leaf in which only its own closer is recognised, so paths and
   code inside a leaf never open a tag.
5. **Batches**: several top-level action blocks run in order (the JSON
   `{"actions":[...]}`). Reply-level `/progress/`, `/next_step/`, `/events/`
   may sit beside the blocks or inside the first one.
6. **Types**: a fixed table of boolean keys (`true`/`false`, anything else is
   refused with a sentence) and integer keys (an integer is coerced, other
   text is left for the handler's own refusal). Everything else is text.
   No escaping exists and none is needed.
7. **A closer inside content** (TMT writing a file that itself contains
   `//content/`): suffix the tag, `/content:a/ ... //content:a/`. The parser's
   "no closer found" error names this. Rare, honest, retry-absorbed.
8. **Text outside all tags** is ignored (as text outside the JSON object is);
   a reply with no tags at all is the existing PROSE path.

### The code shape

- New `agent_protocol.py` (pure, no I/O): `parse(text) -> dict` (detects JSON
  or tags whichever the text is), `render(obj, protocol) -> str`,
  `StreamingTagParser` with the same five event names, typed-key tables,
  `example(obj)` for strings that show the model an action, and
  `transliterate(prompt_text)` which rewrites every JSON example line into
  tags.
- `agent_config`: `PROTOCOL` ("tags"/"json"), `.tmt_protocol`, the effort
  pattern (validated vocabulary, read never raises, set raises).
- `agent_model`: parser chosen per call from `agent_config.PROTOCOL`;
  `response_format` only under json; fabricated replies rendered in the active
  protocol so a handed-back turn never shows the model the other shape.
- The two `json.loads` sites become `agent_protocol.parse`; `raw` stays the
  model's own text so history echoes what it wrote.
- `Turn.messages()` renders the carried answer in the active protocol.
- Correction strings become protocol-aware (`agent_protocol.correction(...)`).
- `get_system_prompt(..., protocol=None)` keyed on protocol; tag variants of
  the contract constants hand-written (same rule numbers 5/10/11), every other
  section transliterated mechanically; subprompt caches keyed too.
- Settings: row "Model Reply Format" with a TAGS/JSON suffix above Danger
  Zone, Enter toggles; `/config` gains a row; refreshed in `main`, `run_ci`,
  `_return_to_menu`; `.gitignore` entry; added to the installation-state test.
- Tests: `run_tests.py`/`conftest.py` pin the suite to **json** explicitly
  (hermetic; the 2,794 existing tests keep meaning what they meant); a
  `Protocol("tags")` helper for new tests; new unit module for the grammar
  (including a round-trip of EVERY prompt example through render->parse) and
  an integration module driving `ask_model`, the main loop, a worker and the
  reviewer under tags.

### The owner's answers (2026-10-07)

1. **Grammar**: build it as proposed (shorthand, `/item/` lists, heredoc-style
   trailing newline, tag suffix for an embedded closer, no escaping).
2. **JSON under tags**: the prompt is STRICT that tags are required; if the
   model answers in JSON anyway it is accepted rather than refused, and the
   transcript says so visibly. (Owner's words: "it can respond in json but if
   it does then we should allow it but it should not in the first place".)
3. **Baseline**: the four pending catalogue files committed first on their own
   as restore point 0 (tag `restore-tags-0`).
4. **Live runs**: the configured free model (probe it answers first).
5. Taken on my recommendation, not separately confirmed: suite pinned to json
   with new tag tests alongside; Settings row "Model Reply Format" with
   TAGS/JSON and no new slash command; the reviewer's verdict stays JSON
   inside its `response` leaf.

## Step 1: `agent_protocol.py` (done 2026-10-07)

Built by an Opus agent; verified by me: `test_agent_protocol` (113),
`test_agent_stream` (52) and `test_agent_cli` (46, holds the packaging test)
**211 passed, 0 failed**. 37 mutations on the module, 36 killed; the survivor
(removing the hold-back of a trailing `\r` while streaming) is equivalent now
that streamed values are trimmed, and was left as a defence.

- 1,235 lines, pure, CRLF, 3.8-safe. Imports `agent_config` lazily and only
  for `REQUIRED_KEYS`. Registered in `pyproject.toml`.
- API: `JSON`, `TAGS`, `PROTOCOLS`, `DEFAULT_PROTOCOL`, `ProtocolError`,
  `detect`, `parse_tags`, `parse`, `render`, `example`, `StreamingTagParser`
  (`feed`, `raw`, `result`, `error`), `transliterate`, `primary_key`,
  `bool_keys`, `int_keys`, `container_keys`, `exact_keys`, `LEGACY_PRIMARY`.
- **All 171 action objects in the two prompt modules round-trip** through
  `render` and `example` (the test's floor is 150 so it cannot pass vacuously);
  `render(obj, JSON)` is byte-identical to the prompt's own text for all 171.
- Type tables, each entry naming its reader: bools `all, apply, diff,
  file_list, summary, final, full, ignore_case, regex, read_only, recursive,
  tmt_prose, tmt_synthetic`; ints `after, step, context, depth, start, end,
  item, position, level, limit, max_results, timeout, timeout_seconds`.
  `id` and `pages` stay text.
- Decisions the spec left open, taken by the builder and accepted: a tag is a
  key only if its own closer appears before the enclosing closer (so
  `/read_file/ /usr/lib/x.py //read_file/` is a shorthand path);
  `detect` reads past leading prose to the first action tag or JSON object,
  as `_extract_json` does today; `render` reads its own output back and
  refuses rather than hand out text that would parse differently; a
  one-action batch renders with an explicit `/actions/` wrapper; `ids` joined
  the container keys (`wait_for_agents`); `respond` joined the legacy table.
- **Grammar rule 3 was narrowed after the first build**: the heredoc rule
  (block form keeps the newline before a closer on its own line) is right for
  file text and wrong for a path, which would have come back as
  `"src/a.py\n"`. Only `content`, `search` and `replace` are EXACT now; every
  other leaf is trimmed of surrounding whitespace at any depth. The module
  docstring and `docs/reply-format.md` (Step 4) state the rule.

## Step 2: the setting (done 2026-10-07, `679fd34`)

Built by a Sonnet agent, verified by me with a fresh run of the eleven
touched-or-neighbouring modules: **421 passed, 0 failed**. 47 mutations run on
a copy of the tree, all killed after two tests were added (the toggle flipping
from the live value instead of the file; the row's fallback label).

- `agent_config.PROTOCOL` / `PROTOCOL_FILE` (`.tmt_protocol`) /
  `read_saved_protocol` / `refresh_protocol` / `set_protocol`, the effort
  pattern. `USE_JSON_MODE` (provider-side `response_format`) stays a separate
  question, noted beside it.
- Settings row `("protocol", "Model Reply Format", ...)` above Danger Zone,
  value TAGS/JSON, Enter toggles from what is on disk. `/config` reports it.
  Refreshed in `main`, `run_ci` and `_return_to_menu`.
- **The suite is pinned to json at the runner** (`isolate_reply_format` in
  `run_tests.py` and `testing/conftest.py`, kept in step): a redirected
  `.tmt_protocol` holding `json`, so a developer's own setting cannot change
  a result. Tag tests opt in with `ReplyFormat("tags")` from
  `test_agent_reply_format`. The two installation-state tests read the
  unredirected path through `real_protocol_file()` for the same reason.
- Known gap: at exactly 30 columns the value clips to `TAG`. `_option_row`
  pads every label to the widest (21 columns) and only protects a suffix that
  fits beside that; `  OFF` fits by one column and `  TAGS` does not. Shared by
  every menu, so left alone here; the width sweep in the new tests starts at
  40 and says why.

### Model routing for the build

Step 1 (parser/serializer/streaming) is Opus: a streaming state machine with
lookahead and a grammar with typed coercion is the one piece where a subtle
bug is silent. Every other step is Sonnet; documentation and probes are
Haiku where they are light.
