# Images: `view_image`

TMT read text and nothing else. `read_file` is a UTF-8 read, so a screenshot was not a
file it could open — it was a decode error. `view_image` is the other half: it reads an
image out of the workspace and attaches it to the message the model is answered with,
so the model *looks* at the picture instead of being told a file exists it cannot read.

```json
{"action": "view_image", "path": "screenshot.png"}
{"action": "view_image", "path": "design/mockup.jpg"}
```

```
Task> the prompt box is drawn in the wrong place, see bug.png
Task> build the settings page from design/mockup.png
Task> here is a photo of the terminal, what is that error
```

## The shape

- **`path`** is the only key, and the only one there is. There is deliberately nothing
  that names a format, a size or a scale.
- **The format is read from the file's own first bytes**, never from its extension.
  PNG, JPEG, GIF and WEBP — the four every provider TMT speaks to accepts. A `.png`
  that a screenshot tool actually wrote as a JPEG is sent as a JPEG, because the
  provider is told the type in the request and rejects one that disagrees with the
  bytes.
- **The picture arrives in the next message, not in the result.** The result says what
  was attached — `Attached bug.png (PNG, 1440x900, 412 KB)` — and the image itself
  rides along with it.
- **It is taken back out after a couple of steps.** The API is stateless, so everything
  in the conversation is re-sent on every step of a turn; an image left there would go
  again on every round for the rest of the task. What replaces it is a line saying it
  was dropped and naming the verb that brings it back, so the model reads that its
  picture is gone rather than reasoning about one it can no longer see.

## What it will not do

- **It will not resize.** Resizing needs an image decoder and TMT takes no
  dependencies. An image over 3 MB is refused with its size and the ceiling both
  named, which is something you can act on with any image editor. The ceiling is
  tighter than any provider's own, for the reason above: it is not sent once.
- **It will not read a format outside the four.** HEIC works on one provider and not
  another, and a capability that breaks when you change a setting is worse than one
  that was never offered.
- **It will not reach outside the workspace.** The same sandbox every file action uses.

## Whether your model can see

Not every model reads images, and TMT answers in three ways rather than two:

- **Yes** — Claude 3 and later, GPT-4o and later, every current Gemini, and the
  vision-language variants on OpenRouter. It is sent.
- **No** — Claude 2, GPT-3.5 and the other text-only models. Refused before the file is
  even opened, with the model named, and TMT tells you the model is text only and that
  Settings can change it.
- **Unknown** — most of the free OpenRouter catalogue, which TMT cannot ask about
  without a live listing it does not read. It is **attempted**. A wasted request that
  comes back with a clear reason is a better failure than a capability refused on a
  guess, and refusing every model the table has not been told about would turn the
  feature off by default.

If you are on a free model and images are not working, switching to a `-vl` variant or
to one of the vendors' own models in Settings is the fix.

## Who has it

The main agent, and a delegated background worker — a worker sent to fix a layout is
exactly the agent that needs to see it. A read-only delegation may use it too: it reads
one file and changes nothing.

The `/note` agent and the reviewer may not, and are never taught it. Neither of those
jobs is looking at pictures: one answers a question about this workspace, and the other
judges a diff.

## Getting an image in front of TMT

Save it into the workspace and name it in your task. There is no attach gesture at the
prompt yet — a terminal has no way to take image bytes from a paste — so the file has
to be somewhere TMT can reach, which is anywhere under the directory it opened in.
