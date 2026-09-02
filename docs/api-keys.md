# Putting a key in by hand

**If pasting into the key screen does not work, you do not have to fight it — write the
key into the file yourself.** All three routes below are read on the next launch, and
none of them needs the screen.

They are listed in the order TMT checks them, and **the first one with a key in it
wins**: an environment variable beats the file, so a stale `OPENROUTER_API_KEY` will
quietly outrank a key you have just typed into `.tmt_providers.json`.

**1. An environment variable.** No file to edit, and it works for every provider:

| Provider | Variable |
|---|---|
| OpenRouter | `OPENROUTER_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Gemini | `GEMINI_API_KEY` |

`TMT_PROVIDER` (`openrouter`, `openai`, `anthropic` or `gemini`) picks which one to
use for that run.

**2. `.tmt_providers.json`, in the installation directory.** This is the file the key
screen writes, and it can be edited by hand. Stored keys normally carry an `obf1:`
tag, but **a value without that tag is read exactly as you typed it** — the store is
built to accept a hand-edited file, so you do not have to reproduce the obfuscation:

```json
{
  "provider": "anthropic",
  "credentials": {
    "anthropic": "sk-ant-your-key-here"
  }
}
```

`"provider"` is which one TMT will use, and it matters: leave it out and TMT falls
back to OpenRouter however many keys are in the file. `"credentials"` may hold one
entry per provider, named `openrouter`, `openai`, `anthropic` or `gemini`.

**3. `.tmt_key`, in the installation directory.** One line, plain text, **OpenRouter
only** — it is the file that predates the store above, and it is still read when there
is no stored OpenRouter credential:

```
sk-or-v1-your-key-here
```

One catch, and it is the one that looks like this file being ignored for no reason:
if you have ever *cleared* the OpenRouter key in Settings, TMT records that you meant
it (`"legacy_key_dismissed": true` in `.tmt_providers.json`) and stops reading
`.tmt_key` at all — otherwise clearing a key would hand back the one underneath it.
Set that field to `false`, or use route 1 or 2 instead.

A trailing newline is fine in all three: it is stripped on the way in. Which directory
these live in is the [installation directory](workspace.md#the-two-directories) — `~/.tmtcode`
after an npm install, or wherever you cloned TMT — never your project. Both files are
git-ignored, and both are written owner-only where the filesystem supports it; a file
you create yourself will not be, so set the permissions yourself if that matters to
you.

A key put in by hand is not checked by anything until the first request, so a mistyped
one shows up as the provider rejecting it rather than as an error from TMT.

---

[← Back to the README](../README.md)
