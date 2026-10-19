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
| 3 | Wire `agent_model`, both loops, session carry, correction strings | done 2026-10-07 | `a0e3ef1`, tag `restore-tags-3` |
| 4 | Prompts: tag contract + transliterated examples, cache keys, subprompts | done 2026-10-07 | `9c170f6`, tag `restore-tags-4` |
| 5 | Embedded JSON hints in refusal/result strings made protocol-aware | done 2026-10-07 | `b6d123b`, tag `restore-tags-5` |
| 6 | Full suite in a clean clone, live API runs under both protocols, measurements | **in progress** - suite 3064/0 on `0f7cae1`, 3067/0 on `5225964`, 3071/0 on `cf3a384` (the CI `0 turns` fix); gaps closed in `4d18777`; SIMULATED acceptance runs PASS under tags and json; owner decisions on the observations recorded; real-provider runs MADE 2026-10-08 (Step 6 "Session 4"): tags on the configured model is format-clean but gave a wrong result, json was correct, and the small model failed under tags. The owner chose the HANDOFF 4 redesign. Session 5: small model PASSES under json and FAILS under current tags (raw reply recorded); owner's answers recorded; build steps 1-5 not started. Session 6 (2026-10-20): HANDOFF 5 step 1 parse side built (`02a771a`, tag `restore-tags-6`, 447/0 over the eleven protocol modules; whole suite not run); render/streaming/corrections and steps 2-5 open; see HANDOFF 6 | `02a771a` |

**NOT COMPLETE.** The code for every step is committed and the core modules
pass (638 passed, 0 failed across the 16 protocol-bearing modules, verified
by the session owner, not only by the builders), but the WHOLE suite has not
been run on these commits and NO real-API run has been made. Nothing here
may be called finished until both have happened; the handoff section says
exactly how.

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
Haiku where they are light. (Step 3 also went to Opus and was cut off by a
usage limit during its mutation pass; its work was complete on disk, its
mutations ran on a copy so the tree was never at risk, and the owner then
asked for low-cost models only.)

## Steps 3, 4 and 5 (done 2026-10-07, committed in that order as 5, 4, 3)

- **Step 5 `b6d123b`**: 38 hint sites in seven modules render through
  `agent_protocol.example` at call time; json byte-identical; 15 tests with
  a source sweep; 36/36 mutations killed. Each module carries its own small
  private `_example`/`_protocol` because the builder could not touch
  `agent_protocol`; a shared `agent_protocol.hint()` would let those seven
  copies be deleted (cleanup, not a defect). Hints NOT converted because they
  sit in a module nobody owned at the time: `agent_shell.py:242`
  (`bash tool's "operation": "start"`).
- **Step 4 `9c170f6`**: `get_system_prompt(..., protocol=None)` keyed per
  protocol; seven json prompts byte-identical (saved before, compared after);
  hand-written `HEADER_TAGS`, `OUTPUT_RULES_TAGS` (rules 5/10/11 keep their
  numbers), `REMINDER_TAGS`, three background header twins,
  `REVIEW_RESULT_REFERENCE_TAGS`; everything else via `transliterate` plus a
  `_TAG_PROSE` swap table; `worker_prompt`/`note_prompt`/`review_prompt`
  take keyword-only `protocol`; `validate_action` says "The reply named no
  action". Sizes (len//4, fixture workspace): main 11,964 -> 13,085 (+9.4%),
  all three authorised 14,883 -> 16,115, worker +616, note +591, review
  +707. 66 tests; 41/41 mutations killed. `docs/reply-format.md` is the
  human-facing grammar.
- **Step 3 `a0e3ef1`**: `ask_model` reads the setting per call; no provider
  JSON mode under tags; `StreamingTagParser` chosen by the reply's first
  token (so the two "chosen by the setting" mutations are equivalent and
  survived); fabricated replies rendered in the format in force; both loops
  use `agent_protocol.parse`; drift (a reply in the other format) is
  ACCEPTED, drawn as a warning row in the main loop, and one drift line is
  appended to the result the model is handed (worker: the line only); the
  correction table lives in `agent_protocol` with json texts byte-identical;
  `Turn.messages()` carries the answer as a tag block under tags. 38 wiring
  tests; 32/34 mutations killed.
- **My own verification of all three together**: `test_agent_protocol,
  _protocol_wiring, _stream, _threading, _session, _agents, _verbs, _multi,
  _multi_wiring, _prompt_tags, _prompt_tags_wiring, _hints, _reply_format,
  _reply_format_wiring, _cli, _orchestration` = **638 passed, 0 failed**.
  The builders' wider runs reported 1224 passed, 2 failed, the two being
  the known `app/`-in-workspace failures
  (`test_agent_git.test_the_entry_point_is_tmt_and_nothing_still_names_the_old_module`,
  `test_agent_grep_glob_wiring.test_the_workflow_glob_then_grep_then_read_lines_lands_on_the_line`).

## Step 6: verification (in progress, 2026-10-07)

HEAD moved to `0f7cae1` (the handoff commit) before verification started; the
clone was of that commit.

Full suite in a clean clone: `bash verify_clone.sh head` (the script from the
HANDOFF, copied into the new session's scratchpad), clone of `0f7cae1` on
`alpha`, started 18:21:47 and finished 18:25:17 NZDT on 2026-10-07, **3064
passed, 0 failed**, exit 0. That is 2,794 plus 270 new tests (the handoff
estimated ~310). It ran in three and a half minutes rather than fifteen
because the clone has no credentials, so the one live test in
`test_agent_review` cannot start a reviewer and passes vacuously, exactly as
the HANDOFF predicted; the live reviewer path is therefore NOT exercised by
this run. No test that pins JSON wording went red, so the json
byte-identity promised by Steps 3-5 held under the suite's json pin.

Live API runs: **blocked, none attempted.** A probe through
`agent_providers.complete` (max_tokens 600, key never printed) against the
configured `nvidia/nemotron-3-ultra-550b-a55b:free` returned `OpenRouter HTTP
429: Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000
free model requests per day` twice, 30 seconds apart. Two alternatives probed
by explicit id, `nvidia/nemotron-3-super-120b-a12b:free` and
`cohere/north-mini-code:free`, returned the identical 429, so it is the
account's daily free quota rather than one model's throttle. `live_run.sh` was
not run; `.tmt_protocol` remains absent (tags, the default, in force); nothing
in the install was modified. Keys are configured for openrouter (selected),
openai and anthropic; none for gemini. Note that only the OpenRouter adapter
has ever been exercised live (the project's notes say so), so a run through
OpenAI or Anthropic would test an unverified adapter and the new protocol at
once. The choice between waiting for the quota reset, adding OpenRouter
credits, or using another configured provider was put to the owner.

`HEADER_TAGS` observation: the tags header is a word-for-word twin of the
JSON `HEADER`, including the clause that text outside the blocks makes "the
turn fails"; the JSON header has carried the same overstatement all along
(text outside the object is in fact ignored, and under tags a JSON reply is
accepted and flagged). Recorded as a shared wording question for the owner,
not a tags defect; nothing changed.

The known-gaps work (HANDOFF item 3) is recorded in the subsection below.

### Known gaps closed (HANDOFF item 3, commit 4d18777)

Built by a Sonnet agent in an isolated git worktree (so the live install's imports could not be disturbed while runs were made), based on `0f7cae1`; its commit `738fd06` was cherry-picked onto `alpha` as **`4d18777`** by the session owner after reading the diff; the worktree and its branch were then removed. Nine files: agent_actions, agent_bash, agent_plan, agent_protocol, agent_review, agent_reviewbot, agent_shell, agent_verify and testing/unit/test_agent_hints.py; 190 insertions, 108 deletions. Line endings preserved (no whole-file diff on any file). Not pushed.

`agent_shell.py`: the `&` refusal no longer hard-codes the JSON shape. The literal `_BACKGROUND` became `_BACKGROUND_SAID` plus a `_background()` function built when said; under json it is byte-identical to the old text via `as_json="the bash tool's \"operation\": \"start\""`, under tags the clause reads `/bash/ /operation/ start //operation/ //bash/`. Two tests added (json byte-identity against the old literal; the tags form, which must parse back to the action).

`agent_protocol.hint(obj, as_json=None)` is the one shared helper: reads `agent_config.PROTOCOL` at call time with a lazy import, falls back to JSON if the setting cannot be read or the object cannot be rendered; `as_json` returns the exact historical json text for the one hint `json.dumps` cannot reproduce byte for byte (agent_plan's). The private `_example` copies in agent_bash, agent_plan, agent_review, agent_verify, agent_reviewbot and agent_actions were deleted; each module imports `hint` at module level inside a try, with a local JSON fallback for an install whose frozen module list lacks agent_protocol. agent_review and agent_verify also lost their now-unused `_protocol` helper (agent_plan and agent_reviewbot keep theirs; still used).

NOT converted, deliberately, because they are not copies of the same behaviour: `agent_file_ops._example_action` (returns None under json, and its json fallbacks are hand-written so Windows paths are not escaped; `json.dumps` would change those bytes) and `agent_delegation._constraints_example` (shows the bare constraints object under json but a whole `spawn_agent` under tags). The handoff said seven modules carried private helpers; the count is eight, six plain copies plus these two.

Tests: a sweep (AST over every `agent_*.py`) asserts no module defines a private `_example`; the old "agent_protocol missing" test, which set `sys.modules["agent_protocol"] = None` in-process, no longer works because the modules bind `hint` at import, so it now runs a fresh interpreter with the import blocked and tags set, and asserts JSON from all the converted modules plus agent_shell, agent_file_ops and agent_delegation. Run through a scratchpad subset runner pinned to json (the live test `test_retire_is_not_an_operation_the_model_can_reach` skipped by name): 17 modules, **933 passed before, 936 after, 0 failed** (test_agent_hints 15 -> 18; every other module unchanged). Mutations: (a) `hint` ignoring the setting and always rendering JSON killed 7 tests (six in test_agent_hints, one in test_agent_protocol_wiring); (b) `_background` reverted to the literal killed its tags test. One intermediate run saw `test_agent_delegation_wiring.test_the_whole_stack_delegates_under_a_contract_from_a_real_session` fail ("the contract is not in the main prompt"); it passed on two immediate reruns and in the final run and was not investigated — recorded as a possible flake, not a fix.

30-column Settings row, confirmed and left alone: `render_settings_menu_frame(size=(30, 40))` draws `'   Model Reply Format     TAG'`; the neighbouring rows clip the same way (`'   Auto Update on Launch   ON'`, `'   Danger Zone            Uni'`), which is the shared `_option_row` rule. Observation made in passing, not investigated: the row read `TAG` even with `agent_config.PROTOCOL` set to json in memory, consistent with the Step 2 note that the row toggles from what is on disk rather than the live value.

Line endings, `git ls-files --eol` in the fresh worktree (`core.autocrlf` true): agent_bash, agent_delegation, agent_multi, agent_protocol and agent_reviewbot are `i/lf w/crlf`; agent_actions is `i/crlf w/crlf`. A fresh checkout shows all six as CRLF in the working tree, so the working-copy LF drift the HANDOFF mentioned does not reproduce in a clean checkout; it was a property of the previous session's working copy only.

`HEADER_TAGS` and the tags prompt size: untouched.

---


### Acceptance runs with a stand-in model (2026-10-07, evening)

The free quota was exhausted (above) and the owner's instruction was to
simulate the model with a cheap Claude agent rather than wait. So the runs
below are NOT real-provider runs and must not be recorded as such. What they
do exercise, unmodified: TMT's real OpenRouter adapter over HTTP (streaming
SSE, keepalive comments, `usage`), `ask_model`, the streaming tag/JSON
parsers, the main loop, the file actions, `bash` and the CI path. What they
do not exercise: the configured model, OpenRouter itself, and `response_format`
being honoured by a real provider.

**The harness** (`output/sim/`, git-ignored; built and smoke-tested under
both formats by a Sonnet agent, then copied there from the session scratchpad
untested from that location). `sim_server.py` is a local `ThreadingHTTPServer`
that writes each request body to `req/N.json` + a readable `req/N.prompt.md`,
sends `: keepalive` SSE comments every 5 s (the client's read timeout is
120 s), and when `resp/N.ready` appears streams `resp/N.txt` back as
OpenRouter-shaped chunks ending in `finish_reason: stop`, an ESTIMATED `usage`
(chars // 4) and `data: [DONE]`. It never writes request headers (the key is
in them). `run_sim.py` imports `agent_config`, sets `OPENROUTER_URL` from
`TMT_SIM_URL`, then imports `agent_model`/`agent_providers` and asserts both
took the redirect before calling `TMT.main()`. `sim_run.sh <tags|json> <label>
"<task>"` is `live_run.sh` with that wrapper, the same `.tmt_protocol`
save/set/restore, `--max-turns 15 --timeout 1500`. `wait_request.py <simdir>`
blocks until an unanswered request exists and prints where to write the
reply; the stand-in model is a Haiku subagent looping on it, told only to be
the model, to read the request file and reply exactly as its system message
instructs, and NOTHING about tags or JSON. `canned_model.py` answers every
request with a fixed file, for smoke tests. `run_ci` makes no other network
call (no key check, no models listing, no updater), confirmed by the builder
reading the path and by exactly one request per smoke run.

**Tags run (`sim-tags1`, 18:42:28-18:42:59 NZDT): PASS, real work end to
end.** Task: the HANDOFF's suggested goodbye(name) task. The stand-in
answered in tags on its FIRST reply, unprompted, as a batch of five
top-level blocks: three `patch_file`, one `bash`, one `end_conversation`
with `/next_step/ Commit the changes //next_step/`. One request (63,195
bytes, 2 messages), reply 1,227 chars in 52 chunks, no drift warning, no
retry, no hand-back, exit 0. Transcript rows, in order:

    · Adding the goodbye function to hello.py.
    · Updating imports to include goodbye.
    · Adding a test for the goodbye function.
    · Running the test suite.
      ▸ Patched file: hello.py        +6 -2
      ▸ Patched file: test_hello.py   +1 -1
      ▸ Patched file: test_hello.py   +6 -2
      ▸ Bash python run_tests.py
    TMT CI: completed (0 turns, 30.6s)
    Changed 2 files: hello.py, test_hello.py

Bytes on disk afterwards: `hello.py` ends `def goodbye(name):\r\n    return
"Bye, " + name\r\n`; `test_hello.py` imports `hello, goodbye` and has
`test_goodbye`; no tag residue anywhere. `python run_tests.py` in that
workspace, run by hand afterwards: `PASS test_goodbye / PASS test_hello /
0 failed`. The reply text and server log are in `output/sim/tags1-*`.

Three observations from that run, none yet acted on:

- **`patch_file` rewrote both files from LF to CRLF.** The originals were
  LF (written by a bash heredoc, confirmed from the git blob); every line is
  CRLF now. Almost certainly `agent_file_ops.patch_file` writing with the
  platform newline and nothing to do with the protocol, but the json run has
  to show the same before that can be stated. Whether it is a defect at all
  is a separate question (TMT on Windows has always done whatever it does).
- **The CI footer said `0 turns`** for a round that ran five actions; the
  smoke run's single `end_conversation` said `1 turn`. Looks like the batch
  path not counting. Display only. Check `run_ci` in TMT.py.
- **`bash` and `end_conversation` in one batch means the summary cannot
  reflect the command's output.** The stand-in wrote "all tests passed"
  before any result could have reached it; it happened to be true. The JSON
  protocol has the same batch feature, so this is not tags-specific, but a
  prompt rule ("do not end in the same batch as a command whose result you
  are about to report") is worth considering.

**Json run (`sim-json1`, 18:57:34-18:58:18 NZDT): PASS, and the same shape
to the byte.** A fresh Haiku stand-in (no memory of the tags run) answered
with one JSON object holding a five-entry `actions` batch: the same three
`patch_file`, `bash`, `end_conversation`, same progress sentences, same
four-word `next_step`. One request (59,743 bytes, `response_format` PRESENT
as it must be under json), reply 1,135 chars in 48 chunks, no retry, no
hand-back, exit 0, footer `TMT CI: completed (0 turns, 42.9s)`, `Changed 2
files`. `hello.py` and `test_hello.py` are BYTE-IDENTICAL to the tags run's,
CRLF included; `python run_tests.py` by hand: both pass. `.tmt_protocol`
absent afterwards. Evidence in `output/sim/json1-*` and `tmt-json1.log`.
So the LF->CRLF rewrite and the `0 turns` footer are both independent of the
protocol: they belong to `patch_file` and to the CI batch path respectively,
and the third observation (ending in the same batch as a command) applies to
both formats equally.

What the pair proves and does not: two different model instances, given
only TMT's prompt in each format, produced a correct, complete, parseable
reply first time, and TMT ran it identically either way. It does NOT prove
what the configured free model will do; see HANDOFF 2 item 4.

### Session 3 (2026-10-08, from 09:10 NZDT; the owner gave it one hour)

**HANDOFF 2 item 2, done.** `bash output/sim/verify_clone.sh final`, clone of
`5225964` on `alpha`, 09:15:36-09:19:43 NZDT: **3067 passed, 0 failed**, exit
0, no `^FAIL` line. Exactly the predicted 3064 + 3. Log in
`output/sim/suite-final.log`. As before, the clone has no credentials, so the
live reviewer test passes vacuously.

**Quota probe (item 4), still blocked.** 09:16 NZDT (20:16 UTC on the 7th),
one request through `agent_providers.complete` to the configured
`nvidia/nemotron-3-ultra-550b-a55b:free` (scratchpad `probe.py`, key never
printed): `OpenRouter HTTP 429: Rate limit exceeded: free-models-per-day`.
OpenRouter's free daily limit resets at 00:00 UTC, which is **13:00 NZDT**, so
no real run fits in this session's hour. Nothing in the install was changed
(`.tmt_protocol` absent, tags in force).

**Item 3: the owner's decisions (asked 09:20 NZDT)**, with what was found in
the code first:

- **`patch_file` LF -> CRLF.** Cause: `read_text` translates newlines and
  `write_text` writes the platform newline, so on Windows EVERY file TMT
  writes or patches comes out CRLF (`agent_file_ops.py` lines 155, 168, 207,
  403). Protocol-independent; the json sim run showed the same bytes.
  Owner: keep it as it is, unless it turns out to stop the tags work being
  proved. It does not (both sims passed with it). **Recorded, not changed.**
- **CI footer `0 turns`.** Cause: in `TMT.py`'s batch loop the
  `if sub_action == END_CONVERSATION:` branch `break`s without `steps += 1`,
  while the single-action path increments before its END_CONVERSATION check.
  So a turn that is one terminal batch reports `turns: 0` in the CI summary,
  a figure nobody measured. Owner: **fix now, separate commit** (see below).
- **`bash` and `end_conversation` in one batch** (summary written before the
  command's output exists; same under json). Owner: **record as a follow-up,
  no change in this work.** Options for whoever picks it up: a one-sentence
  prompt rule in both formats, or a runtime stop that runs the batch's work,
  holds the `end_conversation`, and hands the results back for a real answer.
- **`HEADER_TAGS` / `HEADER` "and the turn fails".** Owner: the protocol has
  to work well for SMALLER models; if the clause makes it harder for them to
  produce good answers, change it. That is a question for evidence, not
  wording taste, so it is deferred to the real runs: in addition to the
  configured 550B model, run the tags task once on a small free model
  (`nvidia/nemotron-3.5-lightning:free` or `cohere/north-mini-code:free`) and
  read whether it keeps the format and answers well. Change the clause (in
  both headers, or in HEADER_TAGS alone if json must stay byte-identical) only
  if that run shows a format or quality problem the clause plausibly causes.

**The `0 turns` fix, commit `cf3a384`.** Built by a Sonnet agent in an
isolated worktree; its commit `912f2a6` was cherry-picked onto `alpha` as
**`cf3a384`** after the session owner read the diff, and the worktree and
branch were removed. Two files, 91 insertions, no deletions: `TMT.py` gains
`steps += 1` (with a comment) as the first line of the batch loop's
`if sub_action == END_CONVERSATION:` branch, the one batch exit that did not
count itself; `TMT.py` stays CRLF in the working tree and the diff is one
hunk. `testing/integration/test_agent_ci_wiring.py` gains four tests through
the existing `ci()` harness: one terminal batch is `turns == 1` (json
summary), the footer of the same run says `completed (1 turn,`, a batch that
goes on plus a terminal batch is 2 (no double count), and a batch that goes
on plus a lone `end_conversation` is still 2. Mutation (increment removed):
the first three fail, the fourth passes as it must. The agent's module runs
with the fix: test_agent_ci 27/0, test_agent_ci_wiring 26/0, test_agent_cli
46/0, test_agent_protocol_wiring 40/0, test_agent_multi_wiring 29/0.

Worth knowing about that build: the agent's worktree was created at
`8b3bf5b` (main), not at `alpha`; it reset its own scratch branch to
`5225964` before editing. A future worktree build should be told to check
`git log -1` first. In a wider sweep inside the worktree, two tests failed
**with and without** the fix and were not touched:
`test_agent_plan.test_the_three_statuses_take_the_three_positions_on_the_one_gradient`
(no colour escape in the painted row) and
`test_agent_delegation_wiring.test_the_whole_stack_delegates_under_a_contract_from_a_real_session`
(`PermissionError` WinError 32 removing a temp workspace; the same test was
recorded as a possible flake in the known-gaps entry above). Both passed in
both clean-clone runs, so they look specific to that runner/environment.

**`output/sim/live_run.sh` gained a model override** (git-ignored, built by a
Haiku agent, reviewed and sent back once): an optional 4th argument MODEL
sets the `openrouter` entry of the install's `.tmt_model` for the run and
restores the file byte-for-byte afterwards, from a `trap ... EXIT` so a
Ctrl-C restores too. The first version passed the Git Bash path
`/c/Coding/TMT/.tmt_model` inside a `python -c` string; native Windows Python
reads that as `C:\c\Coding\TMT\.tmt_model`, so the override would have
silently not happened and a "small model" run would have used the 550B
default. It now converts with `cygpath -w`, passes path and model as argv,
reads the value back, and stops with exit 3 before TMT runs if it did not
take. The same pass fixed a trap-ordering hole that could delete a
pre-existing `.tmt_protocol` on an early exit. Checked by the agent against a
fake install under the scratchpad (override visible mid-run, both files
`cmp`-identical afterwards, early exit leaves `.tmt_protocol` alone); NOT
yet run against the real install, because that makes real requests.

### Session 4 (2026-10-08, from 15:54 NZDT): the real-provider runs

The probe returned `OK ... reply='ready'` at 15:54 NZDT, so the quota had
reset. All three HANDOFF 3 item 1 runs were made through `live_run.sh`
against real OpenRouter, using the task text above. Afterwards
`.tmt_protocol` was absent and `.tmt_model` was byte-unchanged. Logs are in
`output/sim/live-<label>.log` and the workspaces in
`output/sim/live-ws-<label>/`. The owner asked for the runs to be made and
recorded and nothing more, so nothing below was acted on.

| Run | Model | Format errors | Rounds | Outcome |
|---|---|---|---|---|
| `real-tags` | nemotron-3-ultra-550b (configured) | none: no retry, no drift warning | 1 (14.6s) | **Wrong result.** Patched both files and ran bash, but never changed `from hello import hello`, so `test_goodbye` fails (`name 'goodbye' is not defined`). The summary says "The test suite ran and passed both tests", which is **false**. |
| `real-json` | same | none | 4 (52.0s) | **Correct.** Round 1 was a `multi_tool` (2 patches + bash). It saw the failure, fixed the import, re-ran bash, then ended. 2 passed, 0 failed. The summary was accurate. |
| `real-tags-small` | nemotron-3.5-lightning (override confirmed in the log) | **2 retries**: "`/content/` is never closed", then "`/progress/` is given twice beside the actions" | 3 (218.7s) | **Failed.** Only `goodbye` was appended to hello.py, with no blank lines before it. No test was added and bash never ran. The final message shown to the user was the literal text `/end_conversations/`. |

What this shows:

- **Tags works mechanically on the configured model.** It answered in tags
  with no warning or retry. The bytes landed (CRLF, as recorded), and the
  footer's `1 turn` is true, so `cf3a384` holds live.
- **The `bash` + `end_conversation` batch that the owner deferred has now
  produced a false claim on a real model.** The tags run ended in the same
  batch as its test command, so it reported a pass that did not happen. In
  the json run the same model used `multi_tool` and saw the result. One run
  of each is not evidence that the protocol caused the difference, but the
  follow-up is no longer hypothetical.
- **The small model struggled with the tag grammar.** It produced an
  unclosed block, a duplicated key and a malformed ending. None of these is
  text outside the blocks, so the "and the turn fails" clause is not a
  plausible cause. Under the owner's rule for HANDOFF 3 item 2, **the header
  clause is left unchanged.** No json run on the small model was made, so
  nothing yet shows whether it does better under json.

**Step 6 is not marked complete.** The runs it was waiting on have been
made, but one of the two configured-model runs under tags gave a wrong
result and the small model failed under tags. Whether that is good enough to
call the work done is the owner's decision. Possible next steps, none
started: a json run on the small model for comparison; the `bash` +
`end_conversation` follow-up (a prompt rule, or a runtime hold).

### Session 5 (2026-10-08, from 18:16 NZDT; the owner gave it 30 minutes)

HANDOFF 4 step 0 done, and the owner answered the open questions. No product
code changed.

**Quota.** `python -I output/sim/probe.py` at 18:16 NZDT: `OK ... reply='ready'`.
This is the same UTC day as session 4, so the two sessions share one day's
free quota. Session 5 made 1 probe and 4 model requests (3 json + 1 tags).

**Small model under json, the baseline (`real-json-small`, 18:17:33-18:18:32
NZDT): PASS.** Model `nvidia/nemotron-3.5-lightning:free`, and the log's
override line confirms it. The run had no retry and no hand-back.
`TMT CI: completed (3 turns, 57.7s)`. In order, the model read `hello.py`,
appended `goodbye`, patched the import to `from hello import hello, goodbye`,
appended `test_goodbye`, and ran `bash python run_tests.py`. Then it ended.
Its summary says "The test suite ran: both tests passed with 0 failures",
which is **true**. Running `python run_tests.py` in the workspace afterwards
gave `PASS test_goodbye / PASS test_hello / 0 failed`. Both appended files
end without a trailing newline, which is cosmetic. Log:
`output/sim/live-real-json-small.log`. **So problem 2 (the small model's
grammar errors) comes from the tag grammar, not from the model.** The same
model with the same task did the work correctly under json.

**Raw-reply capture (HANDOFF 4 step 0b), built.** A Sonnet agent built it in
the git-ignored `output/sim/` and tested it only against a local fake
upstream.
- `record_proxy.py --dir OUT --port P [--upstream URL]` forwards to real
  OpenRouter. It relays SSE live (first chunk at 0.01s of a 1.67s stream)
  and writes `req/N.json`, `resp/N.raw`, `resp/N.txt` (the reassembled
  assistant text) and `proxy.log`. **It never writes a header value.** The
  test sent `Authorization: Bearer SECRET123` and checked all 13 output
  files for it.
- A 429 passes through with its body.
- `test_record_proxy.py [--e2e]` and `fake_upstream.py` are its tests.
- `live_run.sh` gained an opt-in `RECORD=1`. It starts the proxy on port
  8766 or the next free one, runs TMT through `run_sim.py`, writes to
  `output/sim/raw-<label>/`, and kills the proxy in the existing cleanup
  trap. Without `RECORD=1` the old path is unchanged.

Usage: `RECORD=1 bash output/sim/live_run.sh <tags|json> <label> "<task>" [MODEL]`.

**`.tmt_protocol` was present before this session's run.** It held
`tags\r\n`, the default, so nothing behaves differently. Session 4 recorded
it absent, so something set it between the two sessions: Settings or
another session. `live_run.sh` restored it byte for byte, as designed.
`.tmt_model` was restored to the 550B model.

**Recorded tags run on the small model under the CURRENT grammar
(`real-tags-small-rec`, 18:25:55-18:26:09 NZDT): FAILED, and it is problem 1
on a small model.** This was the first real run through `RECORD=1`, and the
proxy worked: one request, 54,566 bytes in, status 200, 10.08s. The raw reply
(`output/sim/raw-real-tags-small-rec/resp/1.txt`, 499 chars) is
grammatically clean. It holds two `read_file` blocks, each with its own
`/progress/`, then an `/end_conversation/` that has a `/message/` and a
`/next_step/` in the same reply. So TMT read both files and **ended the turn
having done none of the task**. The summary just reports the two files'
contents. `TMT CI: completed (1 turn, 12.2s)`, `hello.py` unchanged, and
`run_tests.py` shows only `PASS test_hello`. Under the agreed design, an old
`/end_conversation/` beside actions does not end the turn, so this run would
have gone on to its second round. This time there was no unclosed
`/content/` to read; the next session can reproduce that with `RECORD=1`.
`.tmt_protocol` (`tags\r\n`) and `.tmt_model` were restored.

| Run | Model | Format | Outcome |
|---|---|---|---|
| `real-json-small` | nemotron-3.5-lightning | json | **PASS**, 3 turns, true summary |
| `real-tags-small-rec` | nemotron-3.5-lightning | tags (current grammar) | **FAIL**, reads plus `end_conversation` in one reply, task not done |

**The owner's answers to the HANDOFF 4 questions (about 18:18 NZDT)**:
1. **`next_step`**: keep a reply-level `/next_step/` block. A reply holding
   only text and `/next_step/` still ends the turn.
2. **Old tags** (`/send_message/`, `/end_conversation/`, `/progress/`):
   accept them but do not teach them. If a reply holds action blocks AND an
   `/end_conversation/`, run the actions, show the end's text as a message,
   and do NOT end the turn.
3. **Prose that looks like a tag**: only a known action name or reply-level
   key opens a block. A top-level `/name/` within edit distance 2 of a known
   action name gets a "did you mean ..." correction. Anything else is prose.
4. **Background agents**: the main agent only in this build. The owner
   asked the next session to also bring the new rules to all subagents
   (worker, note agent, reviewer) after the main agent works. The
   reviewer's verdict is JSON inside a `response` leaf, so its shape needs a
   decision when that happens.

The prose-before-work question (HANDOFF 4 "Things to settle") was not asked
separately. It follows from the design the owner already chose: a text-only
reply always ends the turn, so the early-prose path at `TMT.py:2015` goes
under tags.

### Session 6 (2026-10-20, from 09:35 NZDT; the owner gave it until 10:15)

The owner's instructions for this session, in substance: continue the build;
keep json as a choice with the Settings toggle; when tags is selected inject
a system prompt under which the model "completely forgets that it can use
json and only uses tags"; when tags is selected JSON mode is false; run the
tests when done; never touch `app/`; the manager delegates all building; and
(sent later) "once done push all to alpha with tmt".

**HANDOFF 5 step 1, parse side, built and committed: `02a771a`, tag
`restore-tags-6`.** One Opus agent on `agent_protocol.py` and its unit tests,
given a hard stop at 10:03 and stopped itself at 09:48. Its API: keyword-only
`plain_text=False` on `parse`, `parse_tags`, `detect`, `locate` and
`StreamingTagParser`; the default is byte-for-byte today's behaviour (the
background agents keep the old contract until step 6). With `plain_text=True`
under tags, rules A-G of HANDOFF 6 below hold. Verified by me, not only by the
builder: `test_agent_protocol` 124, `_stream` 52, `_protocol_wiring` 40,
`_prompt_tags` 42, `_prompt_tags_wiring` 24, `_hints` 18, `_reply_format` 17,
`_reply_format_wiring` 19, `_session` 22, `_verbs` 60, `_multi_wiring` 29:
**447 passed, 0 failed**. CRLF held on all three files. No mutation pass was
run (cut for time). The whole suite was NOT run on this commit.

**Not built** (the builder's own list): `render`, `example` and
`transliterate` have no plain mode (rule H); top-level prose is not streamed
live as `("text", ...)` (rule I, partial: `result()` agrees with `parse`);
the plain forms of the `_CORRECTIONS` entries, the unclosed-`/end_conversation/`
sentence and the progress-twice sentence still name the old tags (rule J,
partial: the near-miss and top-level-key refusals are plain-text-safe).

Decisions the builder took, accepted: a known name glued to text
(`docs/plan/x`) opens a block only when its closer follows; a known name on
its own with no closer is still "unclosed", so a truncated `/write_file/`
never becomes a final answer; a KEY name at top level (`/path/ x //path/`) is
refused with the near-miss guard; a lone old `/end_conversation/` takes
trailing text into its message; near-miss candidates exclude `actions`, ties
go alphabetical, legacy names map to current ones. One consequence for step
3: prose such as "Use /plan/ to plan." is refused as unclosed, so the prompt
must write commands as `/plan` with no trailing slash.

Steps 2-5 of HANDOFF 5 were not started. One small item was taken out of
step 2 and done on its own (Sonnet, 09:53-09:54): the `/config` line used to
report the static `USE_JSON_MODE` ("on") under tags although no request
carries `response_format` there (pinned by `test_agent_protocol_wiring`);
it now reads the live `PROTOCOL` and says "off (tags)", json unchanged. Two
tests in `test_agent_reply_format_wiring` (21/0), `test_agent_orchestration`
37/0. Committed with explicit paths after `02a771a`.

**Push.** The owner asked for the push to alpha to be made with TMT. The
docs commit after `02a771a` was driven through TMT in CI mode
(`--ci --allow-push`, format pinned to json for the run because the tags
parser is mid-change, `.tmt_protocol` restored afterwards); the outcome is
recorded in the commit itself and in the next session's first `git log`.

## HANDOFF: what the next session must do (written 2026-10-07 15:25 NZDT)

The owner stopped this session on a time limit. Everything is committed on
`alpha`; HEAD is `a0e3ef1`. Nothing is pushed. The working tree holds the
other session's untracked `app/` (NEVER stage it: no `git add -A`, no
`git add .`, no `git stash -u`). Restore points are the tags
`restore-tags-0` .. `restore-tags-5`; `git diff restore-tags-0 HEAD --stat`
shows the whole change.

Remaining work, in order. Use Sonnet for building and Haiku for light
work; the owner asked for low-cost models.

1. **Full suite in a clean clone** (the working tree cannot give a green
   run because `app/node_modules` is in the workspace). The script is ready:
   `bash <scratchpad>/verify_clone.sh head` clones HEAD into the scratchpad
   and runs `python -u run_tests.py` there (about 15 minutes; the one live
   test in `test_agent_review` passes vacuously in a clone with no
   credentials). The script was written by this session in
   `C:\Users\ALiam\AppData\Local\Temp\claude\C--Coding-TMT\6d6c6399-19a9-4c69-ad1e-7cffbe188d92\scratchpad\verify_clone.sh`;
   if that scratchpad is gone, it is ten lines: clone, `cd`, run
   `PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python -u run_tests.py`,
   grep `^FAIL`. Expect 2,794 + ~310 new tests. Fix anything red; a red
   test that pins JSON wording under the suite's json default is a real
   regression, because Steps 3-5 promised json byte-identity.
2. **Live API runs, tags then json** - the thing the owner cares most about.
   The script is `live_run.sh` in the same scratchpad: it builds a throwaway
   project (hello.py, test_hello.py, run_tests.py), sets the install's
   `.tmt_protocol` for the run and restores it exactly afterwards, and runs
   `python TMT.py --ci --dir <ws> --max-turns 15 --timeout 600 "<task>"`
   from the real install so the real key, model and prompt are exercised.
   Suggested task: `Read hello.py and test_hello.py. Add a function
   goodbye(name) to hello.py that returns "Bye, " plus the name, add a test
   for it to test_hello.py, run python run_tests.py with bash, and finish
   with a short summary of what you changed.` Read the whole log: did the
   model answer in tags unprompted? Did any drift warning appear? Did the
   file bytes land correctly (trailing newline on the new function)? Did
   bash run? Then the same task under json. Put both transcripts' key lines
   in this file. The configured model is
   `nvidia/nemotron-3-ultra-550b-a55b:free`; free models rate-limit and
   rot, so if it 404s or 429s pick another entry from
   `agent_models.FREE_MODELS` and say which. Do NOT print the key.
3. **Known gaps to close or record**: `agent_shell.py:242` hint still says
   `"operation": "start"`; the seven private `_example` copies could become
   one `agent_protocol.hint()`; the Settings row clips its value to `TAG`
   at exactly 30 columns (shared `_option_row`, left alone); `HEADER_TAGS`
   says anything outside the blocks makes "the turn fail", which mirrors the
   JSON header's strictness while a JSON reply is in fact accepted and
   reported - decide whether to soften; the tags prompt is ~9% larger;
   the working copy's CRLF/LF mix moved for `agent_bash.py`,
   `agent_delegation.py`, `agent_multi.py`, `agent_reviewbot.py` (LF now;
   commits are unaffected because `autocrlf` normalises, but check
   `git ls-files --eol` if diffs look whole-file).
4. **Then and only then** update the Status table above to "complete",
   add the live transcripts, and tell the owner. Do not push unless the
   owner says to, in their own words.

### Handoff prompt (paste this to the next session)

> We are converting the TMT CLI at C:\Coding\TMT (branch alpha, HEAD
> a0e3ef1) from its JSON action protocol to a tag protocol with a Settings
> toggle (tags default, json alternative). Read docs/backendchange.md
> first - it holds the research, the grammar, the owner's decisions, every
> restore point and the HANDOFF section with the remaining steps. HARD
> RULES: never read, list or edit anything in C:\Coding\TMT\app (another
> live session owns it), never git add -A or stage app/, never push unless
> the owner says so, use Sonnet for building and Haiku for light work, and
> call nothing complete until (1) the full suite is green in a clean clone
> of HEAD and (2) a real-API run under tags has done real work end to end
> with the transcript recorded in docs/backendchange.md. Start with the
> clean-clone suite run (verify_clone.sh in the previous session's
> scratchpad, or the ten lines described in the handoff), then the two
> live runs with live_run.sh, then the known gaps, documenting progress in
> docs/backendchange.md as you go and making an explicit-path restore
> commit after each major change.

## HANDOFF 2: what the next session must do (written 2026-10-07 19:00 NZDT)

HEAD is `4d18777` plus this documentation change (committed as the next
commit, explicit path). Nothing is pushed. `app/` is still another session's
untracked directory: never stage it, never `git add -A`.

1. **(Done above.)** Both simulated runs are recorded. To repeat one: start
   a Haiku subagent looping on `wait_request.py <simdir>` with the
   stand-in-model instructions (be the model; the request file is your only
   context; reply exactly as the system message says; write `resp/N.txt`
   then `resp/N.ready`) and in parallel `bash output/sim/sim_run.sh <tags|json>
   <label> "<task>"`. The harness in `output/sim/` has not been run from that
   location; its scripts locate `C:\Coding\TMT` absolutely, so it should.
2. **Final clean-clone suite on the final HEAD**: `bash output/sim/verify_clone.sh
   final` (3.5 minutes in a credential-less clone; it was 3064/0 on
   `0f7cae1`, before `4d18777` landed). The gaps commit added 3 tests, so
   expect 3067 passed, 0 failed.
3. **Decide the three observations above** with the owner, and the
   `HEADER_TAGS` "turn fails" wording (a shared overstatement with the JSON
   header; recommendation: leave both or drop the clause from both).
4. **A REAL provider run is still owed.** The owner chose simulation because
   the quota was exhausted, not instead of a real run forever. When the
   OpenRouter daily quota resets (or credits are added), run
   `bash output/sim/live_run.sh tags real-tags "<task>"` and then `json`, and
   record whether the configured model answers in tags unprompted. Only then
   may the Status table say complete.
5. Update the Status table and tell the owner. Push only on the owner's word.

#### Paste-ready prompt for the next session

> We are finishing the tag-protocol change in the TMT CLI at C:\Coding\TMT
> (branch alpha; HEAD is the docs commit after 4d18777). Read
> docs/backendchange.md first, especially "Step 6" and "HANDOFF 2" at the
> end: they hold the clean-clone result (3064/0), the known-gaps commit, the
> simulated acceptance runs under tags and json (both PASS, with a Claude
> stand-in for the model because the free quota was exhausted). HARD RULES: never read, list or
> edit C:\Coding\TMT\app or gui-report (another live session owns app/);
> never git add -A, git add . or stage app/; never push unless the owner
> says so in their own words; all building work goes to subagents (Sonnet
> builds, Haiku for light work); call nothing complete until the final
> clean-clone suite is green AND the real-provider runs in HANDOFF 2 item 4
> have been made. Start with HANDOFF 2 item 1 (the json run's result, or
> rerun it with the harness in output/sim/), then item 2, documenting in
> docs/backendchange.md as you go and committing with explicit paths.

## HANDOFF 3: what the next session must do (written 2026-10-08 09:35 NZDT)

The owner gave session 3 one hour. HEAD on `alpha` is the docs commit after
`cf3a384` (the `0 turns` fix). Nothing is pushed. `app/`, `gui-report/` and
`.claude/` are untracked and belong to others: never stage them, never
`git add -A` or `git add .`.

Done in session 3: HANDOFF 2 item 2 (clean-clone suite 3067/0 on `5225964`),
item 3 (the owner's four decisions, recorded under "Session 3" above), the
`0 turns` fix (`cf3a384`, clean-clone suite 3071 passed, 0 failed, 09:29-09:34 NZDT), and a model
override for `live_run.sh`. NOT done: any real-provider run, because the
OpenRouter free quota was still exhausted at 09:16 NZDT.

1. **Real-provider runs (HANDOFF 2 item 4), the only thing between this work
   and "complete".** OpenRouter's free daily limit resets at 00:00 UTC
   (13:00 NZDT). First `python -I output/sim/probe.py` (one small request to
   the configured model, key never printed); go on only when it prints `OK`.
   The free tier is about 50 requests a day without credits and each run may
   use up to 15, so run these in order and stop if the quota runs out:
   - `bash output/sim/live_run.sh tags real-tags "<task>"` (configured
     `nvidia/nemotron-3-ultra-550b-a55b:free`)
   - `bash output/sim/live_run.sh json real-json "<task>"`
   - `bash output/sim/live_run.sh tags real-tags-small "<task>" nvidia/nemotron-3.5-lightning:free`
     (or `cohere/north-mini-code:free`): the small-model evidence the owner
     asked for. The 4th argument is the new model override; check the log's
   `model override:` line and `model:` line say the small model.
   Task text, as before: `Read hello.py and test_hello.py. Add a function
   goodbye(name) to hello.py that returns "Bye, " plus the name, add a test
   for it to test_hello.py, run python run_tests.py with bash, and finish
   with a short summary of what you changed.` Logs land in
   `output/sim/live-<label>.log`. Read each whole: tags unprompted? any drift
   warning or retry? correct bytes on disk (CRLF is expected and accepted)?
   did bash run? the footer's turn count now true (after `cf3a384`)?
   Afterwards confirm `.tmt_protocol` is absent and `.tmt_model` is
   unchanged (`live_run.sh` restores both). Record the key lines here.
2. **The header clause.** Owner's rule: if the small-model run shows the
   "and the turn fails" clause making it harder to answer well, change it
   (drop it from both headers, or from `HEADER_TAGS` only if json must stay
   byte-identical); otherwise leave it. Any change is built by a Sonnet
   agent and followed by a clean-clone suite run
   (`bash output/sim/verify_clone.sh <label>`).
3. **Then** set the Status table's Step 6 row to complete, tell the owner,
   and push only if the owner says so in their own words.
4. **Follow-ups the owner chose NOT to do in this work** (record them, do not
   build them unprompted): TMT's write paths turn LF files into CRLF on
   Windows; a batch can hold `bash` and `end_conversation`, so the summary is
   written before the command's output exists.

#### Paste-ready prompt for session 4

> We are finishing the tag-protocol change in the TMT CLI at C:\Coding\TMT
> (branch alpha; HEAD is the docs commit after cf3a384). Read
> docs/backendchange.md first, especially "Session 3" under Step 6 and
> "HANDOFF 3" at the end: the clean-clone suite is green, the owner has
> decided the open observations, the CI "0 turns" bug is fixed in cf3a384,
> and output/sim/live_run.sh now takes an optional model id. HARD RULES:
> never read, list or edit C:\Coding\TMT\app, gui-report or .claude (other
> sessions own them); never git add -A, git add . or stage those; never push
> unless the owner says so in their own words; all building work goes to
> subagents (Sonnet builds, Haiku for light work); call nothing complete
> until the real-provider runs in HANDOFF 3 item 1 have been made and
> recorded. Start by checking the OpenRouter quota with
> python -I output/sim/probe.py (it resets at 13:00 NZDT); then run the three
> live runs in HANDOFF 3 item 1, read every log in full, decide the header
> clause from the small-model run as item 2 says, and document in
> docs/backendchange.md as you go, committing with explicit paths.

## HANDOFF 4: plain text is the message (written 2026-10-08 16:15 NZDT)

HEAD on `alpha` is the docs commit after `2689886`. Nothing is pushed.
`app/`, `gui-report/` and `.claude/` belong to other sessions: never read,
list, edit or stage them. Never `git add -A` or `git add .`. Note for
searches: a repo-wide Grep with a glob also matches
`app/backend/tmt-snapshot/`, so always scope searches to the root files.

### Why

The Session 4 real runs (above) found three problems under tags:

1. **A false "tests passed" (550B).** The model put `bash` and
   `end_conversation` in one reply, so its summary was written before the
   test ran. The test actually failed: the import was never fixed.
2. **Grammar errors from a small model (nemotron-3.5-lightning).** An
   unclosed `/content/`, a `/progress/` given twice, and a final reply that
   was the literal text `/end_conversations/`. The task was not done.
3. **Too many tags.** Every action needs a `/progress/`, and every
   user-facing sentence needs a `/send_message/` or `/end_conversation/`
   wrapper. The owner thinks this overhead is what trips models up.

### The owner's design (decided 2026-10-08)

Tags stay, but under the **tags** protocol only:

- **Text outside the tag blocks is a message to the user.** No message tag
  is needed. Example reply: `ok, I'll look at the repo first /list_files/ //list_files/`.
- **A reply with no action blocks is the final answer and ends the turn.**
  Internally it becomes `end_conversation` with that text as the message,
  so the plan, review and verify gates hold it exactly as before. Because
  a reply that runs anything can never end the turn, the model always
  sees the command's output before it answers. That fixes problem 1 by
  construction.
- **Text before an action is that action's progress line.** The
  `/progress/` tag is no longer taught.
- **json is untouched**, byte for byte. The suite's json pin must stay
  green; Steps 3-5 promised this.

### Things the builder must settle with the owner BEFORE building (recommendations given)

- **`next_step`** (the hint shown after an answer): allow a reply-level
  `/next_step/` block in the final reply. It is not an action, so a reply
  holding only text and `/next_step/` still ends the turn.
- **Old tags still sent by a model**: `/send_message/`, `/end_conversation/`
  and `/progress/` are still accepted but no longer taught, the way
  `canonical_action` handles old verbs. If a reply holds action blocks AND
  an `/end_conversation/`, run the actions, show the end's text as a
  message, and do NOT end the turn. This keeps the fix for problem 1 for
  models that use the old shape.
- **Prose that looks like a tag** (`/usr/bin`, `/c/Coding`, the
  `/end_conversations/` typo): only a known action name or reply-level key
  opens a block. A top-level `/name/` within edit distance 2 of a known
  action name gets a correction ("did you mean ..."). Anything else is
  prose.
- **The prose-before-work rule goes, under tags.** `TMT.py:2015`
  (`is_prose(obj) and not turn_state["acted"]`) shows early prose as
  progress and asks for an action. Under the new rule a greeting must end
  the turn, so a text-only reply always ends it. The prompt must say: if
  you say you will do something, put its action in the same reply. Risk:
  a model that announces and stops. Watch for it in the real runs.
- **Background agents** (worker, note, reviewer): keep their current tag
  contract in this step. The reviewer's verdict is JSON inside a
  `response` leaf, and a text-only reviewer reply would collide with the
  JSON-accepted-under-tags path. Main agent only, unless the owner says
  otherwise.

### Build steps (Sonnet builds in isolated worktrees and cherry-picks onto alpha; Haiku for light work)

0. **Baseline and raw-reply capture first, before any code changes.**
   (a) Run the small model under **json**, which has never been done:
   `bash output/sim/live_run.sh json real-json-small "<task>" nvidia/nemotron-3.5-lightning:free`.
   That shows whether problem 2 comes from tags or from the model.
   (b) Give the git-ignored harness in `output/sim/` a pass-through mode
   (a recording proxy that forwards to real OpenRouter and saves request
   and response bodies, NEVER headers), so a real run's raw replies can be
   read. Today CI logs show what TMT did, not what the model sent, which
   is why the unclosed-`/content/` error cannot yet be diagnosed.
1. **Parser** (`agent_protocol.py`): top-level text becomes ordered prose
   segments around the action blocks. The streaming parser emits that
   prose live, so it streams into the reply box. Unknown-name handling is
   as decided above. Unit tests use real Session 4 shapes, including
   `/end_conversations/`.
2. **Loop** (`TMT.py`, single and batch paths, plus `agent_model`'s
   `PROSE_KEY` path): a text-only reply becomes END_CONVERSATION through
   the existing gates. Prose beside actions becomes progress rows. An
   explicit `/end_conversation/` beside actions does not end the turn.
   Session carry (`Turn.messages()`) must render a carried answer as plain
   text under tags, or the history teaches the old shape.
3. **Prompt** (`HEADER_TAGS` and the tag variants of the contract
   sections): teach the three rules, drop `/progress/`, `/send_message/`
   and `/end_conversation/` from every tag example, and say "end your
   reply after any action whose result you need". Measure the tags prompt
   size before and after.
4. **Verify**: run `bash output/sim/verify_clone.sh <label>` (clean-clone
   suite; it was 3071/0 on `cf3a384`). Every json-pinned test must stay
   green.
5. **Real runs**: probe first (`python -I output/sim/probe.py`). The free
   quota is about 50 requests a day and resets at 13:00 NZDT. Run new-tags
   on the small model, then new-tags on the configured 550B, with the same
   task text. Passing means: `python run_tests.py` passes in the run's
   workspace afterwards, no false claim in the summary, and the small
   model under tags does no worse than its json baseline from step 0.
   Record each run's key lines here.
6. Set the Status table only with the owner's agreement. Push only on the
   owner's own word.

Still deferred (do not build unprompted): TMT's write paths turn LF files
into CRLF on Windows; the json protocol's `bash` + `end_conversation` batch
(json must stay byte-identical, so it keeps that behaviour).

#### Paste-ready prompt for session 5

> We are reworking the tag protocol in the TMT CLI at C:\Coding\TMT
> (branch alpha; HEAD is the docs commit after 2689886). Read
> docs/backendchange.md first: "Session 4" under Step 6 (three real-provider
> runs and the problems they found) and "HANDOFF 4" at the end (the owner's
> new design: under tags, text outside the blocks is the message to the
> user, a reply with no action blocks is the final answer and ends the turn,
> and text before an action is its progress; json stays byte-identical).
> HARD RULES: never read, list or edit C:\Coding\TMT\app, gui-report or
> .claude (other sessions own them; scope every search to the repo root
> files because a glob also matches app/); never git add -A, git add . or
> stage those; never push unless the owner says so in their own words; all
> building work goes to subagents (Sonnet builds in isolated worktrees,
> cherry-picked onto alpha after you read the diff; Haiku for light work);
> consult the owner on the open questions in HANDOFF 4 before building.
> Start with HANDOFF 4 step 0 (probe the quota with
> python -I output/sim/probe.py, run the small model under json as the
> baseline, add raw-reply capture to the output/sim harness), then steps
> 1-5. Document in docs/backendchange.md as you go and commit with
> explicit paths. Call nothing complete until the clean-clone suite is
> green and the real runs in step 5 pass.

## HANDOFF 5: build the plain-text tags rules (written 2026-10-08 18:27 NZDT)

HEAD on `alpha` is the docs commit that adds "Session 5" above. Nothing is
pushed. `app/`, `gui-report/` and `.claude/` belong to other sessions: never
read, list, edit or stage them, and scope every search to the repo root
files. `.tmt_protocol` holds `tags\r\n` and is not this work's to remove.

Done: HANDOFF 4 step 0 (the json baseline on the small model PASSED, raw
capture `RECORD=1` works against real OpenRouter, and one recorded tags run
on the small model FAILED by ending in the same reply as its reads), and
every question in "Things the builder must settle" is answered (Session 5
above). **Building has not started.** The design and the answers are final.
Do not ask them again.

Steps, in order (Sonnet builds in isolated worktrees and is cherry-picked
onto alpha after you read the diff; Haiku for light work; a worktree agent
must check `git log -1` and reset its scratch branch to the alpha HEAD
first, because worktrees have been created at `main`):

1. **Parser** (`agent_protocol.py`), HANDOFF 4 step 1. Use these real
   replies as unit-test fixtures:
   `output/sim/raw-real-tags-small-rec/resp/1.txt` (reads plus an old
   `/end_conversation/`), the `/end_conversations/` typo, and
   `ok, I'll look at the repo first /list_files/ //list_files/`. Use the
   owner's near-miss rule (edit distance 2 against known action names,
   top-level only).
2. **Loop**, HANDOFF 4 step 2, including the owner's old-tag rule: an
   `/end_conversation/` beside actions is shown as a message and does NOT
   end the turn. Keep `/next_step/` as reply-level.
3. **Prompt**, HANDOFF 4 step 3, main agent only. Measure the tags prompt
   size before and after.
4. **Clean-clone suite**: `bash output/sim/verify_clone.sh <label>` (3071/0
   on `cf3a384`). Every json-pinned test must stay green, because json is
   byte-identical.
5. **Real runs**, with `RECORD=1` so the raw replies are kept. Probe first
   (`python -I output/sim/probe.py`); the quota resets at 00:00 UTC (13:00
   NZDT). Run the small model under the new tags, then the 550B under the
   new tags, with the task text from HANDOFF 3. **Passing** means:
   `python run_tests.py` passes in the workspace, the summary makes no false
   claim, and the small model does no worse than `real-json-small` (PASS,
   3 turns). Watch for the risk HANDOFF 4 named: a model that announces
   work in text and then stops, because a text-only reply now ends the turn.
6. **Then, as the owner asked: bring the same rules to all subagents**
   (worker, note agent, reviewer). The reviewer's verdict is JSON in a
   `response` leaf and collides with "JSON is accepted under tags", so
   propose its new shape to the owner before building that part. Same
   suite and real-run bar afterwards; a delegated run is the real test.
7. Set the Status table only with the owner's agreement. Push only on the
   owner's own word.

Still deferred (do not build unprompted): LF to CRLF on Windows writes;
json's `bash` + `end_conversation` batch (json stays byte-identical).

### Code sites for steps 1-3 (read-only map, 2026-10-08 18:28 NZDT, at `faccc4b`)

A read-only Sonnet agent made this map. I spot-checked five of the lines and
all five matched. Line numbers will drift once building starts.

- **Where top-level text is dropped today**: `agent_protocol._Reader.reply()`
  (about 429-467). It scans for `/` and skips anything that is not a tag
  (`p = slash + 1`), including stray closers and unknown `/name/`
  (463-464). An unknown top-level `/name/` counts as an action only if it
  is not live, has a later closer, and is not in `self.known` (458-460).
  No edit-distance logic exists.
- **Prose before the first tag is also cut** in `parse()` (846-857, which
  calls `parse_tags(text[at:])`) and in `_locate` / `_first_tag`
  (770-803).
- **Reply-level keys**: `REPLY_LEAVES` = {progress, next_step} (220) and
  `REPLY_KEYS` adds events (221). They are handled at 444-452. `assemble`
  (469-481) merges them into the action or into `{"actions": [...]}`, and
  `_BOTH_PLACES` refuses a key given both beside and inside an action.
- **Known names**: `_known_actions()` (278) = `REQUIRED_KEYS` |
  `LEGACY_PRIMARY`.
- **Streaming**: `StreamingTagParser` (1212-1286) re-runs
  `_Reader(live=True)`. `("text", ...)` comes only from `until()`/`partial()`
  (688-711), and only when `speak` is set by `action()` (512) for an action
  whose primary key is `message`. **Nothing emits top-level prose today.**
  `("progress"|"next_step", s)` comes from `leaf(live_kind=...)` (664-669)
  and `("object", json)` from `whole()` (483-492).
- **Corrections**: `_CORRECTIONS` (1316), including `unreadable` (1318) and
  `prose_before_work` (1336). `TAG_CONTRACT` (1304) says "no prose".
- **`agent_model.py`**: `_prose_reply` (481-500, capped at
  `PROSE_REPLY_LIMIT` 2000, marked `PROSE_KEY`) is reached from
  `_tags_reply` (604-644) when there is no tag and no object, or when
  `parse` returns None. `_LiveReader._choose` (679-686) picks the stream
  parser on the first non-space character; prose first under tags already
  gets `StreamingTagParser`.
- **`TMT.py`**:
  - `stream_handler` (82-130) sends `"text"` to `relay.feed` and
    `"progress"` to a progress row, and holds `"next_step"`.
  - The early-prose hand-back is at 2015-2023 and is the site to change.
  - `_adopt_verb` (2029), then `declared_events` (2030, defined at 895).
  - Batch loop from 2043: its END_CONVERSATION exit is at 2116-2141.
  - Single path: `validate_action` (2187), `send_message` (2201-2209,
    defined at 881), the `completion_block` gate (2215-2224),
    `execute_action` (2233) and END_CONVERSATION (2256-2274).
- **`agent_session._carried`** (175-193) renders the carried answer as an
  `end_conversation` tag block under tags. Under the new rules it becomes
  plain text.
- **`agent_prompt.py`**:
  - Hand-written: `HEADER_TAGS` (713-717, which says outside text "reaches
    nobody" and "every task ends with an end_conversation"),
    `OUTPUT_RULES_TAGS` (727-765, rules 2, 5 and 9 teach the old shape;
    rules 5, 10 and 11 must keep their numbers for `_SHARED_OVERRIDES`)
    and `REMINDER_TAGS` (768).
  - Twins built from the json constants: `SPEAKING_RULES_TAGS` (823),
    `_NEVER_WORKS_TAGS` (835), `ANSWERING_EXAMPLES_TAGS` (845),
    `WORKFLOW_RULES_TAGS` (852) and `PROGRESS_RULES_TAGS` (856).
  - `_TAG_PROSE` (869-902) has no entries for progress, send_message or
    end_conversation.
  - The `ACTION_REFERENCE` examples for `send_message` and
    `end_conversation` are at 226-229.
- **Worker (step 6 only)**: `agent_worker.py` `agent_protocol.parse`
  (1018), drift/detect (1034) and `agent_model.is_prose` (551).
- **Tests**: `testing/unit/test_agent_protocol.py`,
  `testing/integration/test_agent_protocol_wiring.py`,
  `testing/unit/test_agent_prompt_tags.py` and
  `testing/integration/test_agent_prompt_tags_wiring.py`.

#### Paste-ready prompt for session 6

> We are reworking the tag protocol in the TMT CLI at C:\Coding\TMT
> (branch alpha; HEAD is the docs commit that adds "Session 5"). Read
> docs/backendchange.md first: "Session 4" and "Session 5" under Step 6,
> then "HANDOFF 4" (the owner's design: under tags, text outside the
> blocks is the message to the user, a reply with no action blocks is the
> final answer and ends the turn, and text before an action is its
> progress; json stays byte-identical) and "HANDOFF 5" (the owner's
> answers, which are final, and the build steps). HARD RULES: never read,
> list or edit C:\Coding\TMT\app, gui-report or .claude (other sessions
> own them; scope every search to the repo root files because a glob also
> matches app/); never git add -A, git add . or stage those; never push
> unless the owner says so in their own words; all building work goes to
> subagents (Sonnet builds in isolated worktrees, cherry-picked onto alpha
> after you read the diff; Haiku for light work). Start with HANDOFF 5
> step 1 (parser) and go through step 5 (real runs with RECORD=1, small
> model then 550B). When the main agent passes, do step 6: bring the same
> rules to all subagents (worker, note agent, reviewer), proposing the
> reviewer's verdict shape to the owner first. Document in
> docs/backendchange.md as you go and commit with explicit paths. Call
> nothing complete until the clean-clone suite is green and the real runs
> pass.

## HANDOFF 6: finish the plain-text tags build (written 2026-10-20 10:00 NZDT)

HEAD on `alpha` is the docs commit after `ede08e3` (the `/config` change),
which follows `02a771a` (tag `restore-tags-6`). Everything is pushed to
`origin/alpha`. `app/`, `gui-report/` and `.claude/` belong to other
sessions: never read, list, grep or edit them, and scope every search to the
repo root files. A README.md edit that no lane of session 6 made was found
in the working tree at 09:54 NZDT and was left unstaged; it is somebody
else's.

### The owner's rules from session 6 (in force, in addition to HANDOFF 5)

- json stays a choice: the Settings row "Model Reply Format" (TAGS/JSON)
  stays as it is.
- Under tags the model must "completely forget that it can use json": the
  main tags prompt, and every correction, refusal or hint handed to the
  model under tags, must never mention JSON, braces or `{"action"`. The one
  allowed sense is a file format in `read_document`'s reference. A drift
  notice answering a reply that WAS JSON may name JSON.
- Under tags JSON mode is off. The request side already held, and `/config`
  now reports the effective value ("off (tags)"); done in session 6.
- Commits with explicit paths; pushes to alpha driven through TMT when the
  owner asks for them; never `git add -A`.

### The API contract (built on the parse side; steps 2 and 3 are briefed from it)

Keyword-only `plain_text=False` on `parse`, `parse_tags`, `detect`,
`locate`, `StreamingTagParser` (done) and on `render`, `example`,
`transliterate` (NOT done). Default False keeps today's behaviour for the
background agents until step 6 flips them. With True, under TAGS only:
- A. text outside the blocks is a message; nothing is dropped;
- B. a reply with no action blocks is `end_conversation` with the text
  trimmed and uncapped, plus reply-level `next_step`/`events`;
- C. text before a block is its `progress` (outside text, a space, then an
  explicit `/progress/` if both);
- D. text after the last block is a trailing `send_message`;
- E. old `/send_message/` and `/end_conversation/` alone parse as today; an
  `/end_conversation/` beside work blocks becomes a `send_message` in place;
- F. only a known name opens a top-level block; paths and braces in prose
  are prose; JSON is read only for a whole-object reply or the existing
  drift case;
- G. near-miss (edit distance 2 of a known name, or a key name at top
  level) refuses with "did you mean", only when a closer follows or the
  token is the whole reply; the three untaught names are answered with the
  plain-text rule;
- H (to build). `render(..., plain_text=True)`: lone end_conversation ->
  plain text (+ `/next_step/` block); lone send_message -> the legacy block;
  `progress` -> a prose line before the block; trailing send_message ->
  trailing text; an end_conversation inside a batch or a non-final
  send_message RAISES; `transliterate` raises naming the example, so step 3
  finds every prompt example that must be rewritten;
- I (to build). streaming emits `("text", s)` for top-level prose live, with
  the usual hold-back of a partial `/na`;
- J (to build). plain forms of every `_CORRECTIONS` entry and of the
  unclosed-tag and progress-twice sentences: no JSON, no old tags.

### Steps, in order (Sonnet builds; Opus only if the parser work proves subtle; Haiku for docs)

1. Finish step 1: rules H, I, J in `agent_protocol.py` and
   `testing/unit/test_agent_protocol.py`, then the mutation pass that was
   cut (ten mutations minimum, `.bak` on disk, `__pycache__` cleared). Keep
   the twelve protocol-bearing modules green (the list is in Session 6).
2. Step 2, loop (Sonnet; files: `TMT.py`, `agent_model.py`,
   `agent_session.py`, `agent_actions.py`, `agent_plan.py`,
   `agent_review.py`, `agent_verify.py`, `agent_commands.py`,
   `testing/integration/test_agent_protocol_wiring.py`): pass
   `plain_text=True` on the MAIN agent's paths only (`_tags_reply`,
   `_LiveReader`, both loop paths, `_carried`). A text-only reply ends the
   turn through the existing `completion_block` gates; prose beside actions
   is a progress row; a converted old `/end_conversation/` beside actions is
   shown and does not end the turn; the early-prose hand-back
   (`is_prose(obj) and not turn_state["acted"]`) is skipped under tags; the
   live box shows streamed prose once and the transcript records it once.
   Sweep model-facing strings ("Now output an end_conversation action" in
   `build_result_message`; `agent_plan.py:581`, `agent_review.py:755`,
   `agent_verify.py:715`) so that under tags they say "answer in plain
   text" and never say JSON. The worker loop keeps the default. json
   byte-identical. (`/config` already reports effective JSON mode.)
3. Step 3, prompt (Sonnet, in parallel with 2; files: `agent_prompt.py`,
   `testing/unit/test_agent_prompt_tags.py`,
   `testing/integration/test_agent_prompt_tags_wiring.py`; its tests must
   not drive the main loop): a THIRD variant of every format-bearing section
   for the main agent under tags; the three background tags prompts stay
   byte-identical (save before, compare after). Rewrite `HEADER_TAGS`,
   `OUTPUT_RULES_TAGS` (rules 5/10/11 keep their numbers),
   `REMINDER_TAGS`, the twins and `_TAG_PROSE`: teach rules A-D, say "end
   your reply after any action whose result you need; if you say you will
   do something, put its action in the same reply", drop `/progress/`,
   `/send_message/` and `/end_conversation/` from every example, write
   commands as `/plan` with no trailing slash, and remove every mention of
   JSON. Tests build the tags prompt over an EMPTY fixture workspace (the
   repo's own files contain the word) and assert: no reply-format mention of
   JSON, none of the three old tags, every example parses with
   `plain_text=True`, json prompt byte-identical, background tags prompts
   byte-identical. Measure the tags prompt size before/after.
4. Clean-clone suite: `bash output/sim/verify_clone.sh <label>` (3071/0 at
   `cf3a384`; not run since). Every json-pinned test stays green.
5. Real runs with `RECORD=1` (probe first: `python -I output/sim/probe.py`;
   it answered `ready` on the 550B at 09:45 NZDT on 2026-10-20; quota
   resets 13:00 NZDT): small model under new tags, then the 550B, with the
   HANDOFF 3 task text. Pass = `python run_tests.py` passes in the
   workspace, no false claim in the summary, small model no worse than
   `real-json-small` (PASS, 3 turns). Watch for a model that announces and
   stops.
6. Step 6 from HANDOFF 5 (worker, note agent, reviewer; propose the
   reviewer's verdict shape to the owner first), then delete the temporary
   third prompt variant.
7. Update `docs/reply-format.md` (its "Plain-text replies (being built)"
   section), set the Status table only with the owner's agreement, push to
   alpha through TMT when the owner asks (recipe: stage by hand, task text
   "commit exactly what is staged ... pass no paths and no all flag to
   git_commit ... push to alpha", CI mode `--ci --allow-push`, format pinned
   to json for the drive while the tags parser is unfinished, then
   `git log -1` and `git ls-remote` to verify, never the sha in TMT's answer).

#### Paste-ready prompt for session 7

> We are reworking the tag protocol in the TMT CLI at C:\Coding\TMT
> (branch alpha). Read docs/backendchange.md first: "HANDOFF 4" (the
> owner's design), "HANDOFF 5" (final answers), then "Session 6" under
> Step 6 and "HANDOFF 6" at the end (what was built on 2026-10-20, the API
> contract, the owner's rule that the tags prompt and every tags correction
> must never mention JSON, and /config showing JSON mode off under tags).
> HARD RULES: never read, list, grep or edit C:\Coding\TMT\app, gui-report
> or .claude (other sessions own them; scope every search to the repo root
> files because a glob also matches app/); never git add -A or git add .;
> commit with explicit paths; never push unless the owner says so in their
> own words, and then drive the push through TMT as HANDOFF 6 step 7
> describes; all building goes to subagents (Sonnet for the loop and the
> prompt, Opus only for the parser if it proves subtle, Haiku for docs);
> never use a shell heredoc to write or search Python. Do HANDOFF 6 steps
> 1-5 in order (steps 2 and 3 in parallel on disjoint files once step 1 is
> green), run the clean-clone suite, make the real runs with RECORD=1, then
> step 6. Document in docs/backendchange.md as you go. Call nothing
> complete until the clean-clone suite is green and the real runs pass.
