# The backend protocol (`--backend-stdio`), version 1

This is the contract between the TypeScript front end (`tui/`) and the Python
backend (`agent_backend.py`). The TypeScript lanes build against this file; if the
code and this file disagree, fix one of them so they agree again.

```
node tui/dist/main.js                      owns the terminal (raw keys, rendering)
  spawns: python TMT.py --backend-stdio [--dir PATH | PATH]
          stdin  <- messages from the front end (JSON lines)
          stdout -> events from the backend   (JSON lines, nothing else)
          stderr -> a log file (tracebacks, diagnostics; never parsed)
```

The backend runs the same session loop as the Python UI (`TMT._session_loop`):
the same tools, gates, policy, sandbox, checkpoints and `/undo`. Only the screens
are different: each thing the Python UI would draw becomes an event, and each
thing it would read from the keyboard becomes a message.

## Framing

- UTF-8. One JSON object per line, ended by `\n`. A line never contains a raw
  line break: `\n` and `\r` inside strings are JSON escapes, and the backend also
  escapes U+2028, U+2029 and U+0085, so splitting on `\n` is always correct.
- Non-ASCII text is sent as itself (not `\u` escaped). A lone surrogate, which
  is not valid UTF-8, is sent as a `\udXXX` escape.
- Every event has `"v": 1` and a string `"type"`. Messages to the backend should
  carry `"v": 1` too; the backend does not require it.
- **Unknown event types: ignore them. Unknown fields: ignore them.** A newer
  backend may add both. The backend does the same with unknown message types
  (logged to stderr, never answered).
- stdout carries events only. Everything else the backend or the tools it runs
  might print goes to stderr. A line on stdout that is not JSON is a backend bug.

## Handshake and lifetime

1. The front end sends `hello` (with the terminal size) as its first message.
2. The backend's first event is always `ready`. Nothing is written before it; a
   bad message before `hello` is logged, not answered.
3. If the workspace needs confirming (a directory with files and no git), an
   `approval` with `action: "workspace"` follows. Refused: `exit` with code 2.
4. `session` (the header), then the panels (`plan`, `review`, `verify`,
   `agents`, and the rendered column `panel`), then the first `prompt`.
5. From then on the backend alternates between **idle** (a `prompt` is open and
   it waits for `submit`) and **busy** (a command or a turn is running).
6. The session ends with `exit`, after which nothing more is written and the
   process ends. A process that ends without `exit` crashed: show the log.

| How it ends | `exit.reason` | `exit.code` |
|---|---|---|
| `quit` message, or `quit` / `exit` / `/quit` / `/exit` submitted | `quit` | 0 |
| stdin closed (the front end went away) | `eof` | 0 |
| workspace refused | `workspace` | 2 |
| unexpected error (traceback on stderr) | `crashed` | 1 |
| a real SIGINT outside the session loop | `interrupted` | 130 |

## Messages: front end to backend

| type | fields | when |
|---|---|---|
| `hello` | `columns`, `rows` (ints) | first message, once |
| `resize` | `columns`, `rows` | whenever the terminal changes size |
| `submit` | `text` (string) | answer to a `prompt`: a task, a slash command, `quit` |
| `approval_reply` | `id`, `answer` (string) | answer to an `approval` |
| `answer` | `id`, `key` (string or null) | answer to a `question` |
| `interrupt` | - | the user pressed Ctrl-C during a turn |
| `reload` | - | re-read saved settings (after a delegated Python screen changed them) |
| `quit` | - | end the session at the next prompt |

```json
{"v":1,"type":"hello","columns":120,"rows":40}
{"v":1,"type":"resize","columns":100,"rows":30}
{"v":1,"type":"submit","text":"add a --verbose flag\nand document it"}
{"v":1,"type":"approval_reply","id":3,"answer":"y"}
{"v":1,"type":"answer","id":4,"key":"2"}
{"v":1,"type":"interrupt"}
{"v":1,"type":"reload"}
{"v":1,"type":"quit"}
```

Rules:

- **`submit.text`** is the user's line exactly as it should run: a folded paste
  already expanded, any line breaks kept. It is answered the way the Python UI
  answers a typed line: blank does nothing (a new `prompt` follows), `quit` /
  `exit` / `/quit` / `/exit` end the session, a line the slash-command parser
  recognises is a command, anything else is a task. A `submit` sent while the
  backend is busy is held and answers the next `prompt` (that `prompt` is still
  sent first). The design default is that the front end queues what is typed
  during a turn itself and sends it when the next `prompt` arrives.
- **`resize`/`hello`** set `COLUMNS`/`LINES` in the backend, so text it lays out to
  a width (the question block, command rows, the meter's short forms, every
  event's `rows`) matches the front end's terminal. A `resize` is also answered
  with a fresh `panel` (see there). The front end still lays out the live frame
  and the input field itself.
- **`interrupt`** stops a running turn: the loop gets the same KeyboardInterrupt a
  Ctrl-C raises at the terminal, so the turn ends on its usual path (a `notice`
  "Task cancelled. Returning to prompt.", then `turn_end` with outcome
  `you stopped it with Ctrl-C`) and the next `prompt` follows. Pending approvals
  and questions in that turn are abandoned unanswered (nothing is approved). While
  idle, or during a slash command, `interrupt` is ignored.
- **`reload`** is handled while idle (sent during a turn, it waits for the next
  prompt, so a turn never changes model halfway): the backend re-reads every
  saved setting and answers with `settings`. The open prompt stays open (no new
  `prompt`).
- **A reply to the wrong id, or of the wrong type,** is ignored (logged). A reply
  with the right id whose content is not acceptable (an `answer` with a key that
  was not offered, a non-string `answer`) gets a `protocol_error` carrying that
  `id`, and the request is **still waiting**: send a valid reply.
- **Closing stdin** means the front end has gone: pending approvals are refused,
  pending questions answer "nobody was there", a running turn is interrupted, and
  the backend sends `exit` with reason `eof`.
- **Ctrl-C and the process group.** In raw mode the front end receives Ctrl-C as
  data and sends `interrupt`. When raw mode is off (a delegated Python screen),
  a console Ctrl-C reaches the backend too: while idle the backend treats it like
  the Python prompt does (the next `prompt` has the placeholder
  "Use 'quit' or 'exit' to close."); during a turn it cancels the turn.

## Rendered rows

Besides its facts, an event can carry **`rows`: `list[str]`**, the terminal rows
the Python UI would have printed for it. They are not a second renderer: each
list is made by calling the Python UI's own function on a stand-in terminal, so
a front end that commits them as they are shows what the Python UI shows and
cannot drift from it. The facts (`text`, `message`, `question`, `choices`, ...)
are sent unchanged, and a front end uses them when `rows` is absent.

Sent on `session`, `task`, `event`, `command`, `final`, `notice`, `approval`
and `question`. The right-hand column has its own event, `panel`, whose `rows`
follow the same rules.

- **One row is one terminal row**, as a string with no `\n` or `\r` and no
  trailing newline. Rows are laid out for the terminal size the front end last
  reported (`hello`/`resize`). The Python UI leaves the last column spare, so a
  row is at most `columns - 1` wide, and it caps most blocks (the caption, the
  answer box, a command's rows) at 100 columns however wide the window is.
- **Only ANSI SGR escapes** (`ESC [ ... m`), truecolor included
  (`38;2;r;g;b`), and the reset `0m`, `1m` (bold) and so on. Never cursor
  movement, erasing, OSC or any other control sequence: the backend removes them
  from anything it relays, such as a command a model wrote.
- **Blank rows are rows.** Rows are what the Python UI prints, blank-line
  rhythm included: a leading `""` where the Python UI puts a blank line before
  a block (a `command`'s title, an `event` that crosses between prominent and
  other rows, a `notice` that starts with a line break), and `""` between a
  question's parts. Commit every row, in order, verbatim: do not wrap, trim or
  restyle them, and do not add blank rows of your own.
- **Colour follows the Python UI**: truecolor on, the box-drawing glyphs and not
  the ASCII fallbacks, unless `NO_COLOR` is set in the backend's environment, in
  which case there is no colour at all (the Python UI does the same).
- **Absent means lay it out yourself.** If a row list cannot be made the event
  is still sent, without `rows`, and the reason goes to stderr. An empty `[]`
  means the Python UI printed nothing; treat it like absent.

| Event | `rows` is made by | Holds |
|---|---|---|
| `session` | `agent_menu.render_status` | blank row, the 5-row wordmark in its gradient, date, workspace, tip |
| `task` | `agent_menu.render_task` | the caption row (clock, provider, model, right-aligned and dim) and the `> task` row. No blank row after: the Python UI draws none |
| `event` | `agent_ui.Transcript.lines_for` and `_separator`, as `Transcript.emit` prints them | the marker row(s) in the gradient, the dim facts row of a prominent event, and a leading blank row where the record crosses between level 2 and the rest |
| `command` | `agent_menu.render_command` | a leading blank row, the title in its gradient, the label/value rows (label dim), the note |
| `final` | `agent_ui.render_response` | the bordered answer box with the reply's markdown in colour |
| `notice` | the rich console, as `console.print` would print it (highlighting included) | the line(s), wrapped for the width, colour included |
| `approval` | the text `_command_approval` prints before it reads the answer (plain) | the question, then the hint line |
| `question` | the block `_question_asker` prints before it reads the key (plain) | the question, a blank row, the numbered options, a blank row, how to answer |

**`command.rows` was already a field.** It used to be the command's structured
rows (`{"label","value"}` or `{"text"}` objects). When the Python rows are
available they take the name, as on every other event, and the structured rows
are sent as **`items`** (same shape as before). When the Python rows cannot be
made, `rows` is the structured list as it always was and there is no `items`:
a front end tells the two apart by the type of the elements (strings are
rendered rows, objects are structured).

## Events: backend to front end

Field types: `str`, `int`, `bool`, `num` (int or float), `obj`, `list`, `null`.
Every event also has `"v": 1` and `"type"`.

### `ready`
The handshake answer and always the first event. The saved settings the front end
needs, and the terminal size as the backend took it.

| field | meaning |
|---|---|
| `workspace` str | the workspace the backend is about to adopt (absolute) |
| `columns`, `rows` int | the size from `hello` |
| `protocol` str | the model reply format, `"tags"` or `"json"` |
| `mouse` str | click-to-place-caret setting, `"off"` or `"on"` |
| `effort` str | the effort level (`low`, `medium`, `high`, ...) |
| `provider`, `provider_label` str | provider id and its display name |
| `model`, `model_label` str | model id and its display name |
| `configured` bool | a credential is set; when false a task's model call fails, so offer the (delegated) API key screen |
| `version` str | TMT's version, `""` if unknown |

```json
{"v":1,"type":"ready","workspace":"C:\\work\\app","columns":120,"rows":40,"protocol":"tags","mouse":"off","effort":"high","provider":"openrouter","provider_label":"OpenRouter","model":"nvidia/nemotron-3-ultra-550b-a55b:free","model_label":"Nemotron 3 Ultra 550B","configured":true,"version":"0.1.1"}
```

### `settings`
The answer to `reload`: the same fields as `ready` minus `workspace`, `columns`,
`rows`.

```json
{"v":1,"type":"settings","protocol":"json","mouse":"on","effort":"low","provider":"openrouter","provider_label":"OpenRouter","model":"x/y","model_label":"Y","configured":true,"version":"0.1.1"}
```

### `session`
The session header (Python: `render_status`): drawn once when the session opens.
`tip` is the rotating tip. `rows` (see Rendered rows) is the whole header as the
Python UI prints it: the wordmark in its gradient, today's date, the workspace
and the tip; without `rows` the front end draws those itself.

```json
{"v":1,"type":"session","workspace":"C:\\work\\app","tip":{"gesture":"/notes","detail":"shows what TMT knows about this project and what is left"},"rows":["","\u001b[38;2;246;107;24m█..."," Sun 25 Oct 2026"," C:\\work\\app"]}
```

### `prompt`
The backend is idle and waiting for `submit` (Python: the prompt box opens).
`placeholder` is the shadow text for an empty field: the opening suggestion, the
last turn's suggested next step, "Use 'quit' or 'exit' to close." after a
cancelled prompt, or "Ask one question about this workspace" when `/note` asks
for its question. It is never part of the answer. `caption` is the row above the
box; `meter` is the left end of that row (all zeros until something is counted,
which the Python UI draws as no meter at all).

```json
{"v":1,"type":"prompt","placeholder":"Review the changed files","caption":{"clock":"09:30:00","provider":"OpenRouter","model":"Nemotron 3 Ultra 550B"},"meter":{"tokens_in":12127,"tokens_out":168,"tokens_out_exact":true,"lines_added":2,"lines_removed":0,"agent_tokens":0}}
```

### `task`
The submitted line, as the Python UI writes it into the scrollback (caption row,
then `> text`). Sent for tasks and slash commands alike. `caption.clock` is the
time the prompt was opened. The Python UI collapses whitespace when it draws it;
`text` is the line as submitted. `rows` is the two rows the Python UI wrote: the
caption and the `> task` line.

```json
{"v":1,"type":"task","text":"write the notes","caption":{"clock":"09:30:00","provider":"OpenRouter","model":"Nemotron 3 Ultra 550B"},"rows":["                         \u001b[38;2;88;88;88m09:30:00 · OpenRouter · Nemotron 3 Ultra 550B\u001b[0m"," > write the notes"]}
```

### `command`
A slash command's answer (Python: `render_command`): a title, rows, a note. A row
is `{"label","value"}` (two columns, label dim) or `{"text"}`. `ok: false` is a
failed command (Python paints the title in the error colour). `prompt_for:
"note"` means the next `prompt` asks for the note's question. `to_menu: true` is
`/back`: the Python UI would open the startup menu here; the backend resumes at
once, so a front end that wants a menu shows its own (and sends `reload` if a
delegated screen changed settings). Also sent once when a turn creates the
project's `TMT_Context` (title is the message, no rows).

`rows` is what the Python UI printed: a `list[str]` (see Rendered rows), with
the structured rows above sent as `items`. If the Python rows could not be made,
`rows` is the structured list and there is no `items`; tell them apart by the
type of the elements.

```json
{"v":1,"type":"command","title":"Plan","ok":true,"rows":[""," \u001b[38;2;246;107;24mP\u001b[38;2;236;173;9ml\u001b[38;2;137;203;21ma\u001b[38;2;74;222;128mn\u001b[0m","   There is no plan for this task."],"items":[{"text":"There is no plan for this task."}],"note":"","prompt_for":"","to_menu":false}
```

### `turn_start`
A task has started running (not sent for slash commands). Busy until `turn_end`.

```json
{"v":1,"type":"turn_start","task":"write the notes"}
```

### `status`
The status row under the box (Python: `LiveUI`). `state` is `thinking` (the
animated THINKING word, before the model has produced anything; `progress` null),
`working` (a progress bar at `progress` percent with `label`, e.g. "Write File",
"FINALIZING", "Complete!"), or `idle` (the row is cleared). Within a turn the bar
only moves forward. The Python row also shows an elapsed/tokens readout and an
estimated finish; draw those from `meter` and your own clock. A slow slash command
(`/note`) shows `working` with no `progress`, then `idle`.

```json
{"v":1,"type":"status","state":"working","label":"Write File","progress":23}
```

### `meter`
The running totals (Python: the caption's meter and the status row's readout).
Sent when they change, at most every 0.25 s while a reply streams.
`tokens_in` is the context of the request in flight (always an estimate, drawn
with `~`); `tokens_out` the session's output, exact when `tokens_out_exact`
(otherwise drawn with `~`); `lines_*` include background agents' edits;
`agent_tokens` is what background agents spent. During a turn also
`turn_tokens` (this turn's output so far) and `elapsed` (seconds since the turn
started).

```json
{"v":1,"type":"meter","tokens_in":11998,"tokens_out":54,"tokens_out_exact":false,"lines_added":2,"lines_removed":0,"agent_tokens":0,"turn_tokens":84,"elapsed":3.1}
```

### `delta`
A piece of the user-facing reply text as it streams (the `message` of a
`send_message` or of the final answer; Python reveals it in the live region).
Append in order.

```json
{"v":1,"type":"delta","text":"Done — wrote "}
```

### `reply_reset`
Discard everything streamed by `delta` so far: it was a message that is shown as
an `event` instead, or a reply being retried.

```json
{"v":1,"type":"reply_reset"}
```

### `event`
One permanent row of the turn's record (Python: `Transcript.emit`): progress
messages, warnings, and the row for each action that ran. `kind` is one of
`progress`, `milestone`, `warning`, `success`, `error`, `tool`, `file_read`,
`file_edit`, `file_create`, `file_delete`, `command`, `test`,
`background_agent`. `level` is its prominence: 0 dim (progress), 1 normal,
2 prominent (file changes, commands, tests). The Python UI puts a blank line
before an event when it crosses between level 2 and the others. `detail` holds
measured facts; the Python UI prints a second dim row from `added`/`removed`
(`+A -R`), `lines`, `files`, `bytes`, `calls` and `status` (`exit S`); `paths`
lists the files the action named and is not drawn. `stage` is usually `""`.
`rows` is what `Transcript.emit` printed for it: the marker row(s) and the dim
facts row, with the blank row in front where the record crosses between level 2
and the rest.

```json
{"v":1,"type":"event","kind":"file_create","message":"Created file: notes.txt","stage":"","level":2,"detail":{"added":2,"removed":0,"paths":["notes.txt"]},"rows":["","   \u001b[38;2;132;204;22m▸\u001b[0m Created file: notes.txt","\u001b[38;2;88;88;88m     +2 -0\u001b[0m"]}
```

### `tool_start`, `tool_end`
Around every action the main agent's loop executes, except the final
`end_conversation` (that is the answer: see `final`). `id` pairs them. `label`
is the action's display name, `target` what it is about in the request's words
(a path, command, pattern, URL or question; `""` if none). A `multi_tool` is one
tool. Approvals and questions happen between the two. `tool_end.status` is
`done` (it returned; the action may still have failed or been refused, which its
`event` row says), `raised` (it threw: `error` has the text, a `notice` follows and
the model is asked to correct itself) or `interrupted`. The row the Python UI
prints for the action arrives as an `event` after `tool_end`. Background agents'
own actions are not reported here; they show in `agents`.

```json
{"v":1,"type":"tool_start","id":2,"action":"bash","label":"Bash","target":"npm test"}
{"v":1,"type":"tool_end","id":2,"action":"bash","status":"done"}
```

### `approval`
A question that blocks the turn until `approval_reply` arrives (Python:
`_command_approval`, and the workspace question). `question` is the full text
(several lines); `hint` is the sentence the Python prompt shows under it;
`choices` are the answers to offer, in order; `pattern` is the rule an "always"
answer would remember (`""` when there is none); `action` is what asked (`bash`,
`delete_file`, `delete_folder`, ..., or `workspace`). There is no deadline, as
at the terminal. Answer with the choice's `key`: for `bash`, `y` runs it once,
`a` runs it and remembers `pattern` for this project, anything else refuses; for
deletions and the workspace, `y` allows and anything else refuses. `rows` is the
text `_command_approval` prints before it reads the answer: the question's lines,
then the hint line (plain, no colour). For the workspace question it is the one
prompt line.

```json
{"v":1,"type":"approval","id":1,"question":"TMT wants to run a command that needs your approval.\n\n    rm notes.txt\n    in .\n\ndestructive: `rm` can destroy work ...\nThe rule this would be remembered as: rm","pattern":"rm","hint":"Type y and Enter to run it once, a to allow `rm` in this project from now on, or anything else to refuse:","choices":[{"key":"y","label":"Run it once"},{"key":"a","label":"Allow `rm` in this project from now on"},{"key":"n","label":"Refuse"}],"action":"bash","rows":["TMT wants to run a command that needs your approval.","","    rm notes.txt","    in .","","destructive: `rm` can destroy work ...","The rule this would be remembered as: rm","Type y and Enter to run it once, a to allow `rm` in this project from now on, or anything else to refuse:"]}
```

### `question`
The model's `ask_user` (Python: `_question_asker`), answered by ONE key. `text` is
the whole block exactly as the Python UI prints it (question, numbered options,
how to answer); `question` and `options` are the same content structured (null
if the backend could not tell); `keys` are the accepted keys, option N is key
`"N"`. Reply with `answer`: `key` one of `keys`; `""` means the user dismissed it
(Esc); `null` means nobody could be asked. Any other key gets a `protocol_error`
and the question stays open, as a mistyped key does at the terminal. There is no
free-text question: free text is always a `prompt`. `rows` is `text` split into
rows: the block `_question_asker` prints above the one-key read.

```json
{"v":1,"type":"question","id":2,"text":"Which colour?\n\n  1. Red\n  2. Blue\n\nPress 1 or 2 to choose, or Esc to skip the question.","keys":["1","2"],"question":"Which colour?","options":["Red","Blue"],"rows":["Which colour?","","  1. Red","  2. Blue","","Press 1 or 2 to choose, or Esc to skip the question."]}
```

### `plan`, `review`, `verify`, `agents`
The right-hand column of the Python UI (the PLAN rows, the review and
verification states, the AGENTS cards and the reviewbot strip), as snapshots. Each
is sent once when the session opens and again whenever it changes, from any
thread, idle or busy. Replace the previous snapshot of the same type.

- `plan.steps`: `[{id, position, title, status}]`, `status` one of `pending`,
  `in_progress`, `completed`, `blocked`. An empty list is no plan.
- `review`: `state` (what to draw: `idle`, `running`, `passed`, `warnings`,
  `failed`, `error`; a passed review made stale by later edits draws as
  `warnings`), `headline` (the one-line summary the column prints), `counts`
  (issues per severity), `cycles`, `required` (this task cannot end without one).
- `verify`: `state` (`idle`, `planning`, `running`, `passed`, `failed`, `error`,
  `cancelled`, `stale`), `headline`, `checks` (`[{id, name, category, level,
  command, status, exit_code, duration, scope, reason, reused, summary}]`),
  `cycles`, `required`.
- `agents.agents`: `[{id, number, kind, task, status, activity}]` for the
  background agents the Python panel would show (finished ones stay for a while).
  `status` is one of `created`, `starting`, `running`, `waiting`, `completed`,
  `killed`, `failed`, `timed_out`, ...; `activity` is the card's live label.

```json
{"v":1,"type":"plan","steps":[{"id":"S1","position":1,"title":"Write the notes","status":"in_progress"}]}
{"v":1,"type":"review","state":"idle","headline":"No review yet","counts":{"CRITICAL":0,"MAJOR":0,"MINOR":0,"SUGGESTION":0},"cycles":0,"required":false}
{"v":1,"type":"verify","state":"passed","headline":"Verified - 1 check passed","checks":[],"cycles":1,"required":true}
{"v":1,"type":"agents","agents":[{"id":"1","number":1,"kind":"worker","task":"port the parser","status":"running","activity":"Reading parser.py"}]}
```

### `panel`
The right-hand column as the Python UI draws it, already laid out: `rows` are
terminal rows (the same rules as every `rows`, see Rendered rows) and `width` is
the column's display width, the widest of its rows. The structured snapshots
above say what is in the column; this says what it looks like.

It is made by the call the Python prompt box makes for its column
(`PanelState.frame(columns, rows)`) for the terminal size the front end last
reported. It holds, stacked with a blank row between the blocks that exist:
CAPABILITIES (what the task's wording authorised: `/plan`, `/review`,
`/verify`), PLAN, VERIFY and REVIEW. The AGENTS cards are not in it: the Python
UI draws those only while the user has the agents panel open, which a front end
has no gesture for, and they come as `agents`.

- **`rows: []` and `width: 0`** when the Python UI would draw no column: nothing
  to show (no capabilities, no plan, no review or verify activity), or a
  terminal narrower than 45 columns (too narrow for two columns).
- **Where it goes.** The Python UI composes it beside the live area (the reply,
  the status row and the box): flush with the top of that area, `width` columns
  wide, its right edge one column short of the window's (the spare column every
  row leaves), with 2 columns between it and the left area, which is
  `columns - 1 - 2 - width` wide. Rows are not padded.
- **When it is sent.** Once before the first `prompt`, empty or not; then
  whenever its content changes (checked wherever `plan`, `review`, `verify` and
  `agents` are, and sent after them), including when what the task authorised
  changes at the start of a turn; and after every `resize`, always, even if the
  content is the same, because the layout depends on the size. A `resize` that
  arrives before the first `panel` sends nothing: that first one carries the new
  size. Replace the previous `panel`.

```json
{"v":1,"type":"panel","width":28,"rows":["\u001b[38;2;34;197;94mCAPABILITIES 1\u001b[0m","\u001b[38;2;34;197;94m● /plan\u001b[0m","","\u001b[38;2;249;115;22mPLAN 0/2\u001b[0m","────────────────────────────","\u001b[38;2;249;115;22mS1 ● Write the notes\u001b[0m","\u001b[38;2;220;38;38mS2 ○ Check them\u001b[0m"]}
{"v":1,"type":"panel","width":0,"rows":[]}
```

### `notice`
A line the Python UI prints straight to the console rather than into the turn's
record: errors and warnings from the loop ("Stream failed: ...", "Circuit Breaker
Tripped: ...", "Unreadable reply, asking again: ...", "Invalid action: ...",
"Action failed: ...", "Task cancelled. Returning to prompt.") and the workspace
messages. `level` is `error`, `warning` or `info`; `text` is plain (no markup).
`rows` is what the console printed, colour and rich's highlighting included,
wrapped for the terminal width; a message that starts with a line break has a
leading `""`.

```json
{"v":1,"type":"notice","level":"warning","text":"Task cancelled. Returning to prompt.","rows":["","\u001b[33mTask cancelled. Returning to prompt.\u001b[0m"]}
```

### `text`
Fallback: permanent plain text the backend had no structured event for. Not
expected in normal use; draw it as a plain row.

```json
{"v":1,"type":"text","text":"something printed above the live region"}
```

### `final`
The answer (Python: the bordered box `render_response` draws). Markdown text.
Sent after the turn's status row has gone `idle`. `rows` is the box as the Python
UI draws it, the markdown rendered in colour.

```json
{"v":1,"type":"final","text":"Done — wrote notes.txt ✓","rows":[" ┌──────────────────────────────┐"," │  Done — wrote notes.txt ✓    │"," └──────────────────────────────┘"]}
```

### `turn_end`
The turn is over, however it ended. `answer` is the final answer or `""`;
`outcome` is `""` when it answered, otherwise why it did not (for example
`you stopped it with Ctrl-C`, `the stream failed`, `the model repeated itself and
was stopped`, `the model could not produce a usable action in 6 attempts`,
`it ran out of steps before answering`). `suggestion` is the next step the next
`prompt` will show as its placeholder.

```json
{"v":1,"type":"turn_end","task":"write the notes","answer":"Done — wrote notes.txt ✓","outcome":"","suggestion":"Review the notes"}
```

### `protocol_error`
A message from the front end could not be used. `message` says why; `id` is the
request it was meant to answer (still waiting), `line` the start of an
unparseable line.

```json
{"v":1,"type":"protocol_error","message":"that reply does not answer request 2","id":2}
```

### `exit`
The last event. `code` is the process exit code, `reason` as in the table above,
`message` the error for `crashed` (else `""`).

```json
{"v":1,"type":"exit","code":0,"reason":"quit","message":""}
```

## One session, in order

```
-> {"type":"hello","columns":100,"rows":30}
<- ready
<- session
<- plan / review / verify / agents / panel  (initial snapshots)
<- prompt     placeholder "Describe your first task"
-> {"type":"submit","text":"write the notes"}
<- task
<- turn_start
<- status thinking -> status working "Just Started!" 10
<- event progress "Writing the notes."
<- tool_start write_file  <- tool_end done  <- event file_create (+2 -0)
<- tool_start delete_file <- approval id=1
-> {"type":"approval_reply","id":1,"answer":"y"}
<- tool_end done          <- event file_delete
<- tool_start ask_user    <- question id=2
-> {"type":"answer","id":2,"key":"2"}
<- tool_end done          <- event tool "Ask User Which colour?"
<- delta "Done " <- delta "— wrote notes.txt ✓"
<- status FINALIZING 95 -> Complete! 100 -> idle
<- final
<- turn_end
<- prompt     placeholder "Review the notes"
-> {"type":"submit","text":"/plan"}
<- task <- command
<- prompt
-> {"type":"quit"}
<- exit       reason "quit"
```

`meter` events are interleaved wherever counts change, and panel snapshots
(`plan`, `review`, `verify`, `agents`, then `panel`) wherever a panel changes; a
`resize` from the front end is answered with a `panel`.

## Where each event comes from

| Python UI | Event | Reply |
|---|---|---|
| `render_status` (session header) | `session` | |
| `PromptBox.ask` | `prompt` | `submit` |
| `render_task` | `task` | |
| `render_command` | `command` | |
| `LiveUI` (start, progress, clear) | `turn_start`, `status` | |
| `meter_text`, the status row's readout | `meter` | |
| `LiveRelay.feed` / `reset` | `delta` / `reply_reset` | |
| `Transcript.emit` | `event` | |
| `execute_action` | `tool_start`, `tool_end` | |
| `_command_approval`, `resolve_workspace`'s question | `approval` | `approval_reply` |
| `_question_asker` | `question` | `answer` |
| `render_response` (in `finish_response`) | `final` | |
| `Session.record` | `turn_end` | |
| the right-hand panel column | `plan`, `review`, `verify`, `agents` | |
| the right-hand column as drawn (`PanelState.frame`) | `panel` | |
| `console.print` | `notice` | |
| Ctrl-C (`TypeAhead` interrupt) | | `interrupt` |

How it is wired: `agent_backend.Headless` supplies a stand-in for each of these
names (`agent_backend.SEAMS`) and installs them over TMT.py's own for the length
of the run, so the loop itself is the unchanged one. Each stand-in also calls the
Python UI's own renderer for its event on a stand-in terminal and sends the
result as `rows` (see Rendered rows). Tests:
`testing/unit/test_agent_backend_protocol.py` (framing, routing, stand-ins) and
`testing/integration/test_agent_backend_events.py` (whole sessions through a
reference client, the real process, and a golden capture of the Python UI).
