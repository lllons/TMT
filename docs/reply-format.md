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

## When the model answers in JSON anyway

The prompt is strict: under `tags` the model is told that its reply is read by a tag parser, that nothing outside tag blocks reaches anybody, and that JSON is the wrong format. A reply written in JSON is nevertheless **accepted** rather than thrown away, so a model that slips does not lose the turn, and it is **noted visibly** in the transcript so you can see it happened. It is a mistake and is shown as one.

## The reviewer's verdict is JSON either way

A review ends with a result object (`status`, `summary`, `issues`, and so on) that TMT reads and checks. That object is not the reply format, and it stays JSON under both. What changes is how it is carried. Under `json` it is a string inside a string, with every quote escaped. Under `tags` it is the plain text of the reviewer's `internal_response`, written raw:

```
/internal_response/ {"status":"PASS","summary":"...","issues":[]} //internal_response/
```

## How the model is taught

Each format has its own system prompt, built from the same sections:

- The **`json` prompt is the original**, byte for byte. Asking for `json` gives the text TMT had before there was a second format.
- The **`tags` prompt** replaces the parts that describe the wire format (the header, the output rules, the worked answers, the behaviour and progress rules, the closing reminder) with tag versions, and rewrites every example in every other section as tags.
- Every example in the `tags` prompt is written by the same code that reads replies back, so each one is exactly what the parser accepts. An example the parser would refuse would teach the model a mistake.

The background agents (workers, the note agent and the reviewer) are taught the same way, in the same format.

A block of tags is longer than the compact JSON it replaces, so the `tags` prompts are somewhat larger. Measured over a small workspace, in estimated tokens (characters divided by four): the main agent's prompt grows by about 9% (11,964 to 13,085 with nothing authorised, 14,883 to 16,115 with `/plan /review /verify`), a worker's by about 8% (7,737 to 8,353), the note agent's by about 9% (6,611 to 7,202) and the reviewer's by about 7% (9,907 to 10,614). The workspace listing that goes into the main prompt is the same size in both, so a larger project makes the difference proportionally smaller.

---

[← Back to the README](../README.md)
