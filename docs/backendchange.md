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
| 6 | Full suite in a clean clone, live API runs under both protocols, measurements | **in progress** - suite 3064/0 on `0f7cae1`; gaps closed in `4d18777`; SIMULATED acceptance runs PASS under tags and json; real-provider runs and the final clone suite still owed (see Step 6 and HANDOFF 2) | - |

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
