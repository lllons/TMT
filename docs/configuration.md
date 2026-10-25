# Configuration

| Variable | Default |
|---|---|
| `OPENROUTER_API_KEY` | from `.tmt_providers.json`, then `.tmt_key` |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` | from `.tmt_providers.json`. See [Putting a key in by hand](api-keys.md#putting-a-key-in-by-hand) |
| `TMT_PROVIDER` | the provider saved in `.tmt_providers.json`, else `openrouter` |
| `OPENROUTER_MODEL` | `nvidia/nemotron-3-ultra-550b-a55b:free`, or the model saved in `.tmt_model` |
| `TMT_STREAM` | `1` |
| effort | `medium`, from `.tmt_effort`; set with `/effort` |
| reply format | `tags`, from `.tmt_protocol`; set in Settings ("Model Reply Format"). `json` is the other value. See [Reply format](reply-format.md) |
| project context | on, from `.tmt_context`; set in Settings. See [Project context](project-context.md) |
| UI | `python`, from `.tmt_ui`; set in Settings ("UI"). `typescript` is the other value and takes effect on the next start. Overridden for one run by `--ui python\|typescript`, then by `TMT_UI`. See [Interface](interface.md#the-typescript-ui-optional) |
| click to place caret | `off`, from `.tmt_mouse`; set in Settings ("Click To Place Caret"). Only the TypeScript UI reads it |
| `TMT_GIT_NAME` | `TMT code` |
| `TMT_GIT_EMAIL` | none — required before TMT will commit |
| `TMT_GIT_ROOT` | the repository containing the project directory |
| the `PATH` argument, or `--dir` | the current directory |

---

[← Back to the README](../README.md)
