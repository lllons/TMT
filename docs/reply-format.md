# Reply format

Everything the model does in TMT, it does by writing an **action**: read a file, patch it, run a command, say something to you. The *reply format* is how it writes that down. There are two, and you choose.

| Format | What a reply looks like |
|---|---|
| `tags` (the default) | `/read_file/ /path/ src/main.py //path/ //read_file/` |
| `json` | `{"action":"read_file","path":"src/main.py"}` |

They carry exactly the same information. Every action, every key and every rule about what may run is identical in both; only the spelling on the wire differs.

## Choosing one

Settings, **Model Reply Format**, with Enter to switch between `TAGS` and `JSON`. The choice is saved in `.tmt_protocol` next to the other per-installation settings, and it takes effect on the next request: a session can switch halfway through and the model is taught the new format from its very next turn. See [Configuration](configuration.md).

## Why there are two

JSON was TMT's first format and it still works. Its weak point is file text. A model writing a Python file inside a JSON string has to escape every quote, every backslash and every line break in it, and one escape written wrong is a reply TMT cannot read. A longer file has more chances to get one wrong.

Tags have no escaping at all. Inside a tag, text is written exactly as it is, so a file is written the way it will appear on disk. That is why tags are the default. JSON stays because some models are much better at it, and because nothing that worked before should stop working.

## The grammar

A tag opens as `/name/` and closes as `//name/`. The **action is the outer tag** and its **keys are tags inside it**:

```
/read_file/ /path/ notes.txt //path/ //read_file/
```

**Inline and block form are the same thing.** Use inline for short values and block form for anything long or spread over several lines:

```
/write_file/
/path/ src/hello.py //path/
/content/
def main():
    print("Hello")
//content/
//write_file/
```

**Shorthand.** Bare text inside an action, with no key tags, is the action's first required key:

```
/read_file/ notes.txt //read_file/
/end_conversation/ All done. //end_conversation/
```

**Lists** are a run of `/item/` entries. An item that holds key tags is an object, which is how `write_files` and `events` are written:

```
/git_diff/ /paths/ /item/ a.py //item/ /item/ b.py //item/ //paths/ //git_diff/
```

**`multi_tool`** takes its calls as tags of their own: inside `/calls/`, every tag is itself an action.

```
/multi_tool/
/calls/
/read_file/ /path/ src/a.py //path/ //read_file/
/read_file/ /path/ src/b.py //path/ //read_file/
//calls/
//multi_tool/
```

**Booleans and numbers** are the bare words and bare digits: `/recursive/ true //recursive/`, `/start/ 12 //start/`. There is no `null`.

**Batches.** Several action blocks in one reply run in order, the way a JSON `{"actions":[...]}` does:

```
/create_folder/ /path/ reports //path/ //create_folder/
/write_file/
/path/ reports/q3.md //path/
/content/
# Q3
//content/
//write_file/
/end_conversation/ Created reports/q3.md. //end_conversation/
```

`/progress/`, `/next_step/` and `/events/` go inside the action they belong to. In a batch, a `/progress/` or `/next_step/` may also sit beside the blocks.

### Whitespace, and the three keys that keep it

Almost every value is **trimmed**: leading and trailing spaces and newlines are dropped, so a path written in block form is still `src/a.py` and never `src/a.py` followed by a newline.

Three keys hold file text and keep it exactly: **`content`, `search` and `replace`**. They follow the rule a shell heredoc does. In block form the newline straight after the open tag is dropped, and the newline before a closer that sits on a line of its own is kept, so a file ends with a newline. To leave that last newline out, put the closer at the end of the last line:

```
/content/
no newline after this line //content/
```

### A closer inside a value

Writing a file that itself contains the text `//content/` would end the value early. Give that pair a suffix, and only the suffixed closer ends it:

```
/content:a/
the text //content/ is part of the file
//content:a/
```

If a tag is never closed the reply is refused with a sentence that names this mechanism, and the model is asked again.

## Plain-text replies (all agents)

The parser reads AND writes these rules (`plain_text=True`). Every agent under `tags` uses them on branch alpha: the main agent, and the background worker, note agent and reviewer. The block-only rules described above remain in the parser, but no prompt teaches them any more.

- **Text outside the blocks is a message to you.** Nothing is dropped.
- TMT's own markers (`tmt_synthetic`, `tmt_synthetic_reason`, `tmt_prose`) ride beside the plain text as blocks, so a reply TMT fabricates to report a failure still reads back as one.
- **A reply with no action blocks is the final answer** and ends the turn. A `/next_step/` block beside the text is still the hint shown afterwards.
- **Text before an action is that action's progress line**, so `ok, I'll look at the repo first /list_files/ //list_files/` lists the files and shows the sentence as it does.
- **Text after the last action is shown without ending the turn.** A reply that runs anything never ends the turn, so the model always sees a command's output before it answers.
- **The old `/send_message/`, `/end_conversation/` and `/progress/` tags are still accepted.** An `/end_conversation/` beside actions is shown as a message and does not end the turn.
- **Only a name TMT knows opens a block.** `/usr/bin/python`, `/c/Coding`, `src/a.py` and `docs/plan/x` are text. A name one or two letters away from a real one, such as `/end_conversations/` or `/raed_file/`, is refused with a correction, but only when it is closed later or is the whole reply, so `see /log/ for details` stays text.

### Background agents

The worker, the note agent and the reviewer have no user, so the same rules mean something slightly different for them:

- **A reply with no action blocks is the final answer and ends the run.** Its text is what the main agent reads (for the note agent it is printed for the person who asked). It replaces the `internal_response` ending, which is still accepted if a model writes it.
- **Text beside an action reaches nobody.** A background agent is told to write none; if it does, it is ignored (a sentence before an action is that action's progress line, a sentence after the last one is told that nothing was shown).
- **They are corrected in plain words**: no JSON, no braces, no `internal_response`, and the correction says a reply with no action blocks is the final answer.
- The shared sections of their prompts are the main agent's plain-text ones, so the three background prompts and the main prompt teach the same format rules. Under `json` nothing changed.

## When the model answers in JSON anyway

The prompt is strict: under `tags` the model is told that its reply is read by a tag parser, that nothing outside tag blocks reaches anybody, and that JSON is the wrong format. A reply written in JSON is nevertheless **accepted** rather than thrown away, so a model that slips does not lose the turn, and it is **noted visibly** in the transcript so you can see it happened. It is a mistake and is shown as one.

## The reviewer's verdict

A review ends with a verdict that TMT reads and checks. Under `json` it is the result object (`status`, `summary`, `issues`, and so on), a string inside a string with every quote escaped. Under `tags` the reviewer is taught a plain-text verdict (this is a proposal, see "Session 8" in `docs/backendchange.md`): its final answer is

```
VERDICT: approve
What was reviewed and what was concluded, one smaller point per line.
```

or

```
VERDICT: revise
src/auth/token.py:148 - what is wrong, and the fix wanted. One finding per line.
```

`approve` is read as a PASS. `revise` is read as a FAIL carrying one blocking finding that holds the notes whole, so it sends the task back for another round exactly as a structured MAJOR does. The notes are required, and the line must be the first line. Anything else (an unknown word, no notes, no verdict at all) is a review that did not happen, and the task stays blocked.

The structured result object is still accepted, unchanged, from a reviewer that writes it (bare, or wrapped in a sentence, or as an `internal_response`), under `tags` as under `json`. It is the only way to report a warning that does not block (`PASS_WITH_WARNINGS`), a requirement checklist, or findings with separate fields; the plain shape trades those for being easy to write.

## How the model is taught

Each format has its own system prompt, built from the same sections:

- The **`json` prompt is the original**, byte for byte. Asking for `json` gives the text TMT had before there was a second format.
- The **`tags` prompt** replaces the parts that describe the wire format (the header, the output rules, the worked answers, the behaviour and progress rules, the closing reminder) with plain-text versions, and rewrites every example in every other section as plain-text replies.
- Every example in the `tags` prompt is written by the same code that reads replies back, so each one is exactly what the parser accepts. An example the parser would refuse would teach the model a mistake.

The background agents (workers, the note agent and the reviewer) are taught the same way, in the same format.

A block of tags is longer than the compact JSON it replaces, so the `tags` prompts are somewhat larger. Measured with the plain-text prompts over a small workspace, in estimated tokens (characters divided by four): the main agent's prompt grows by about 4% (11,964 to 12,467 with nothing authorised, 14,883 to 15,434 with `/plan /review /verify`), a worker's by about 3.5% (7,737 to 8,004) and the note agent's by about 4.6% (6,611 to 6,917). The reviewer's is about 3.5% smaller (9,907 to 9,560), because the plain verdict is much shorter to teach than the result object. The workspace listing that goes into the main prompt is the same size in both, so a larger project makes the difference proportionally smaller.

---

[← Back to the README](../README.md)
