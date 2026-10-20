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
| `TMT_GIT_NAME` | `TMT code` |
| `TMT_GIT_EMAIL` | none — required before TMT will commit |
| `TMT_GIT_ROOT` | the repository containing the project directory |
| the `PATH` argument, or `--dir` | the current directory |

---

[← Back to the README](../README.md)
