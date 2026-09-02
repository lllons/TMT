# Configuration

| Variable | Default |
|---|---|
| `OPENROUTER_API_KEY` | from `.tmt_providers.json`, then `.tmt_key` |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` | from `.tmt_providers.json`. See [Putting a key in by hand](api-keys.md#putting-a-key-in-by-hand) |
| `TMT_PROVIDER` | the provider saved in `.tmt_providers.json`, else `openrouter` |
| `OPENROUTER_MODEL` | `minimax/minimax-m3:free` |
| `TMT_STREAM` | `1` |
| effort | `medium`, from `.tmt_effort`; set with `/effort` |
| project context | on, from `.tmt_context`; set in Settings. See [Project context](project-context.md) |
| `TMT_GIT_NAME` | `TMT code` |
| `TMT_GIT_EMAIL` | none — required before TMT will commit |
| `TMT_GIT_ROOT` | the repository containing the project directory |
| the `PATH` argument, or `--dir` | the current directory |

---

[← Back to the README](../README.md)
