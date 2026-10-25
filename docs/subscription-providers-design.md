# Subscription providers for the TMT CLI

Status: design draft, 2026-10-25. No code written. Scope is the root CLI only (`C:\Coding\TMT`, outside `app/`).

## 0. Summary

TMT already routes Anthropic through the user's signed-in `claude -p` binary when `anthropic_route` is `subscription`. This document asks whether the same pattern can cover other vendors' consumer subscriptions (ChatGPT Plus/Pro, Google account, GitHub Copilot), and how to generalise it without making TMT a token holder. Recommendation: a generic `LocalCliProvider` base with a per-vendor spec table, a per-provider route setting, and a phased rollout that starts with GitHub Copilot CLI (best documented non-interactive tool controls), then OpenAI Codex CLI, with Gemini CLI held until its Google-account path and the June 2026 Antigravity change are clarified.

Conventions: every fact is tagged **[verified]** (seen in an official doc page during this research), **[search]** (only seen in a search snippet or third-party page), or **UNVERIFIED** (not confirmed; do not build on it without a spike).

## 1. Research

### 1.1 Vendor comparison

| Vendor CLI | Non-interactive command | Subscription sign-in | Sign-in check without a model call | Model flag | Streaming / JSON | Install (Windows) |
|---|---|---|---|---|---|---|
| Anthropic `claude` (today) | `claude -p` | Claude plan via `claude` login | UNVERIFIED here (existing file check) | `--model` | text | existing |
| OpenAI Codex CLI | `codex exec "<prompt>"` [search] | ChatGPT Plus/Pro/Team/Enterprise via `codex login` (browser) [verified] | `codex login status` [verified] | `-m` / `--model` [search] | `--json` JSONL events UNVERIFIED | npm global package; winget UNVERIFIED |
| Google Gemini CLI | `gemini -p "<prompt>"` [verified: headless page] | Google account OAuth path exists [search]; headless behaviour UNVERIFIED | Cached credentials file UNVERIFIED | `-m` UNVERIFIED on official page | `--output-format json` (`response`, `stats`, `error`) and `stream-json` (JSONL) [verified] | npm `@google/gemini-cli` UNVERIFIED |
| GitHub Copilot CLI | `copilot -p "<prompt>"` [verified] | GitHub login or `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN` [verified]; whether a Copilot subscription is required UNVERIFIED | No documented sign-in file; UNVERIFIED | `--model=` [verified] | `-s` (silent), `--output-format=json` JSONL [verified] | npm package UNVERIFIED |
| opencode | `opencode run "<prompt>"` [search] | `opencode auth login` stores keys in `~/.local/share/opencode/auth.json` [search]; consumer-plan login UNVERIFIED | Read the auth.json provider entry (UNVERIFIED shape) | `-m provider/model` [search] | UNVERIFIED | npm `opencode-ai` UNVERIFIED |
| Qwen Code | `qwen` headless flags UNVERIFIED | Free Qwen OAuth tier closed 2026-04-15 [search] | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| Cursor agent | Not researched in this pass | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |

### 1.2 Per vendor detail

**OpenAI Codex CLI.**
- One-shot: `codex exec "<prompt>"`. Model via `-m`. Sandbox and approval flags exist, but the exact names for a read-only, no-tools run are UNVERIFIED. The closest candidate is a read-only sandbox plus a never-approve policy; a spike must confirm that no shell, file or web tool is reachable.
- Sign-in: `codex login` opens a browser; `codex login --device-auth` is a beta headless option [verified]. Credentials go to `~/.codex/auth.json` (or OS keyring, per `cli_auth_credentials_store`) [verified].
- Sign-in check: `codex login status` shows the active method [verified]. Exit code on logged-out state UNVERIFIED; parse text, do not trust exit code until a spike confirms it.
- Usage limit text: reported forms include "You've hit your usage limit... try again at <date, time>" and "Try again in N days N hours N minutes" [search, community reports; not from an official page]. Map both to a usage-limit ProviderError with the reset text kept for display.
- Caveat that matters: OpenAI's auth page says API keys are "still the recommended default for automation", and that browser ChatGPT sign-in is not the recommended path for programmatic CI use [verified]. Expect policy friction; see §5.
- Windows: npm global install (`@openai/codex`) is the likely route; winget UNVERIFIED. npm creates `.cmd` shims, see §3.5.

**Google Gemini CLI.**
- One-shot: `gemini -p "<prompt>"` (headless is triggered by `-p` or a non-TTY stdin) [verified]. The page flags `-p` as the documented prompt flag; one third-party page calls it deprecated in favour of positional prompts, so re-check `gemini --help` at spike time.
- JSON: single object with `response`, `stats`, optional `error` [verified]. Stream: JSONL with `init`, `message`, `tool_use`, `tool_result`, `error`, `result` [verified]. Exit codes: 0 success, 1 general or API failure, 42 bad input, 53 turn limit [verified]. Map 53 and 1-with-quota text to errors.
- Sign-in: Google account OAuth exists as an auth method [search], but no source confirmed it works unattended. Headless guidance found points at API key or Vertex service accounts. Treat Google-account headless use as UNVERIFIED.
- Critical finding: the Gemini headless page says Gemini CLI "was replaced by Antigravity CLI on June 18th, 2026" for unpaid tier and Google One users [verified on the fetched page; scope and whether `gemini` still works for subscribers UNVERIFIED]. Hold Gemini until this is confirmed.
- Model flag and tool-disable flags: UNVERIFIED on the page fetched. `--output-format` and `-p` are the confirmed ones.

**GitHub Copilot CLI.**
- One-shot: `copilot -p "<prompt>"` runs one prompt and exits [verified]. `-s` / `--silent` prints only the response, which suits piping [verified].
- Tool control: `--allow-tool=` and `--deny-tool=` accept kinds `shell`, `write`, `read`, `url`, `memory`, plus MCP server names, with filters in parentheses [verified]. `--no-ask-user` stops input prompts [verified]. `--allow-all-tools` exists; TMT must never pass it. Deny rules are reported to win over allow rules [search]. For a plain text model, deny `shell`, `write`, `read`, `url`, `memory` and every MCP server; confirm in a spike that a denied call returns an error to the model rather than hanging.
- Output: `--output-format=text` (default) or `json` (JSONL, one object per line) [verified].
- Model: `--model=` with precedence over `COPILOT_MODEL` and settings.json [verified].
- Auth: token env vars read in this order: `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN` [verified]. Whether a Copilot subscription is required and where interactive login is stored: UNVERIFIED.
- Usage limits: none documented on the programmatic page [verified absence]. Error text UNVERIFIED.
- Windows install: UNVERIFIED on the package name; assume npm global with `.cmd` shim.

**opencode.**
- `opencode run` with `-m provider/model` [search]. Credentials via `opencode auth login`, stored in `~/.local/share/opencode/auth.json` [search].
- Its design is provider-agnostic, so a "subscription" here may mean reusing another vendor's login, which is the riskiest case for terms. Treat as Phase 4 or out of scope until the vendor-by-vendor terms are checked.

**Qwen Code.** The free hosted Qwen OAuth tier closed on 2026-04-15 [search]. No subscription path to reuse. Out of scope.

**Cursor agent.** Not researched. Out of scope until checked.

### 1.3 Tradeoffs in the research

- Copilot has the best documented non-interactive controls and JSON output.
- Codex has the clearest sign-in status command, but its own docs push programmatic use to API keys.
- Gemini has the cleanest JSON schema, but its subscription story is in flux.
- Nothing documented gives a true "no agent loop" switch. Every vendor's CLI is an agent first, so TMT must sandbox by tool denial plus an empty workdir, and verify.

## 2. Current behaviour (Anthropic baseline)

The existing `claude -p` route is the model for every vendor: print mode, plain text in and out, tools, plugins and MCP off (`--tools ""`, `--setting-sources ""`, `--strict-mcp-config`), `--system-prompt-file` always passed, `ANTHROPIC_API_KEY` stripped from the child env, a stable empty workdir, children killed on cancel, `status()` equal to installed plus a sign-in file check, and usage-limit and not-signed-in output mapped to `ProviderError`. The generic design keeps every one of these as a spec field or base-class behaviour so no vendor can skip them.

Assumption to confirm when editing: the Anthropic provider lives in root `agent_providers.py`. This document did not read the file.

## 3. Design

### 3.1 LocalCliProvider base

A single base class in `agent_providers.py` owns the process lifecycle: spawn with a timeout, stream stdout, kill the whole process tree on cancel (on Windows, `taskkill /T /F` on the PID), stable empty workdir, env scrubbing, error classification, and `status()`. A vendor is a frozen spec record:

| Spec field | Purpose |
|---|---|
| `executable` | Name to resolve on PATH (`copilot`, `codex`, `gemini`, `claude`) |
| `build_args(prompt_file, model, system_file)` | Returns the argv list, containing the vendor's no-tools, print-mode flags |
| `signed_in()` | Cheap check: a local file or a status command, never a model call |
| `strip_env` | Variables removed from the child env (for example `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GH_TOKEN`, `GITHUB_TOKEN`) |
| `error_patterns` | Regex to `ProviderError` kind map (usage-limit, not-signed-in, unknown) |
| `parse_output(stdout)` | Text, or JSON `response` field for Gemini, or last `result`/`message` JSONL line for Copilot and Codex |
| `models` | Static list shown in the menu; user may type any id |

Adding a vendor means one spec record of about 30 lines plus fixture tests. The base class is the only place that spawns processes.

Strip `GH_TOKEN` and `GITHUB_TOKEN` only for the Copilot spec, and only in subscription mode, so a user's CI token is not silently billed or used.

### 3.2 Per-provider route setting

Replace `anthropic_route: api | subscription` with a map, `provider_routes: {anthropic: api|subscription, openai: api|subscription, google: api|subscription, github: api|subscription}`. Migration: read the old key once, write the new map, keep reading the old key for one release. Default for every provider stays `api`, so nothing changes for existing users.

### 3.3 Settings menu UX

- Under each API provider section, one extra row: "Subscription (via <CLI name>)". Hidden when the executable is not on PATH, shown greyed with "install <name> first" text instead.
- Status line under the row: `installed <version> | signed in | route: subscription` or `not signed in: run "<cli> login"`. The status comes from `signed_in()`, never a model call, and is cached for the menu session.
- Swapping back: selecting "API key" sets the route to `api`; the stored key is untouched. Nothing is deleted.
- Sign-in is never done by TMT. The menu shows the command to run in the user's own terminal.

### 3.4 Transcript flattening and the TMT reply protocol

Each CLI adds its own system prompt and agent scaffolding. TMT cannot remove that for Codex, Gemini or Copilot (only `claude` has `--system-prompt-file` today). Therefore:
- TMT's protocol (tags and JSON reply) must live in the user-turn text, not in a system prompt. The flattener renders the transcript as plain text blocks, with the protocol rules as the first block and the latest user message last, so the vendor's own preamble is less likely to override it.
- Parse defensively: the reply parser accepts the TMT tags, strips code fences, and on failure makes one repair call with a short "reply again in the required format" instruction. A second failure is a reported error, not a loop.
- Empty workdir mitigates project-file context (`GEMINI.md`, `AGENTS.md`, `.github` instructions) loading into the model. Confirm per vendor in a spike, because each one reads its own project files from cwd.

### 3.5 Windows notes for the spawn layer

- npm-installed CLIs appear as `.cmd` shims. Node cannot spawn `.cmd` files directly without a shell, so the base class must resolve the real target or invoke through `cmd.exe /c` with args quoted. Do not use `shell=True` with a prompt string in argv; pass the prompt through a file or stdin.
- Pass the prompt by file (the same way `--system-prompt-file` works) or stdin, so quoting in PowerShell and cmd cannot break it.
- PATH refresh: a CLI installed after TMT starts is not found until the process restarts. The status check should re-resolve on each menu open.

## 4. Risks

| Risk | Detail | Mitigation |
|---|---|---|
| Terms of service | Anthropic permits running the unmodified `claude` binary on the user's own subscription. Other vendors' terms on consumer-plan sign-in through third-party tools are not checked here. OpenAI's own docs steer programmatic use to API keys. | Do not ship subscription routes to other users until the terms are read and a decision is recorded. Keep the route per user, never shared. Ship behind a flag for the owner first. |
| Credential custody | TMT must never read, copy or upload `auth.json`, keyring entries or tokens. | Only status checks by file presence or status command. No token in TMT config or logs. |
| Rate and usage limits | Plan windows (reported five-hour and weekly for Plus) are shared between CLI, IDE and cloud use. Error text varies. | Map known patterns to usage-limit; show reset text; never auto-retry in a tight loop; back off with a visible message. |
| Latency | Each call starts a Node process and an agent; cold start can be seconds. | Show a spinner with the vendor name; keep the process warm only if the vendor supports it (UNVERIFIED). |
| No token numbers | Subscription routes report no usage, so the cost panel cannot show spend. | Show "subscription, usage not metered" in the cost panel; do not invent figures. Gemini JSON `stats` gives counts only where reported. |
| Tool and agent leakage | A CLI may still run shell, edit, or MCP tools, or ask the user questions and hang. | Deny all tool kinds; `--no-ask-user` where supported; empty workdir; kill on timeout; a spike test that proves a denied tool returns an error. |
| Vendor drift | Flags renamed (Gemini `-p` already in question; Gemini replaced by Antigravity for some tiers). | Pin the tested CLI version per spec; a version probe at status time; fixtures refreshed per release. |
| Output format drift | JSON or JSONL schema change breaks parsing. | Parse failures surface the raw stdout tail; fixtures in tests catch drift. |

## 5. Test plan

- Unit tests with a fake executable: a stub script (Python, since it is on PATH) that echoes canned stdout, stderr, exit code and sleep behaviour, selected by an env var. Run through the real base class, so spawn, timeout, kill and env scrubbing are exercised, not mocked away.
- Fixture set per vendor: success text, success JSON, JSONL stream, usage-limit text (both reported forms), not-signed-in text, unknown error, and a hang that must be killed on cancel.
- Mocked `subprocess` only for argv assertions: each spec must produce the no-tools flags, `--system-prompt-file` or equivalent, the stripped env keys, and the empty cwd.
- Status tests: missing executable, installed but signed out, signed in; all without a model call (assert no spawn beyond the status command).
- Cancel test on Windows: child tree gone after cancel (check with a grandchild PID file).
- Migration test: old `anthropic_route` value maps to `provider_routes`.
- Manual spike, owner only, with a real subscription: per vendor, confirm the denied-tool behaviour, exit codes, and one usage-limit reproduction if possible. Record results in the spec comments.
- Existing test suite (`testing/unit/`) must pass unchanged for the Anthropic path after the refactor.

## 6. Rollout order

| Phase | Work | Exit criterion |
|---|---|---|
| 0 | Extract the base class and spec record from the existing Anthropic code; no behaviour change; migrate the route key | Existing tests pass; Anthropic output identical on fixtures |
| 1 | Spike Copilot CLI (owner-only flag): spec, fixtures, tool-denial proof, Settings row behind a dev flag | Tool-denial test passes on a real install; status check works without a model call |
| 2 | Codex CLI spec, sign-in status via `codex login status`; decide on terms before any user-facing release | Usage-limit text confirmed from a real run or documented |
| 3 | Gemini CLI, only after Antigravity status and Google-account headless use are verified | Official doc confirms the path for subscribers |
| 4 | Optional: opencode, Cursor, others, each gated by a terms review | Written terms decision on file |

Each phase is shippable alone. Phase 0 has no user-visible change and should land first.

## 7. Open questions for the owner

1. Is offering subscription routes to other TMT users acceptable, or is it owner-only use? Current recommendation: owner-only until the terms are read.
2. Should Copilot be the first vendor, given its documented controls, or do you prefer Codex because it is the most-used?
3. Should the Settings row show cost as "not metered", or hide cost entirely for subscription routes?
4. Should each spec pin a tested CLI version and refuse to run an untested one, or only warn when the installed version differs?

## 8. Source notes

Sources consulted in this pass (search snippets and fetched pages): OpenAI Codex auth page (redirected to learn.chatgpt.com/docs/auth), the Gemini CLI headless page at geminicli.com/docs/cli/headless, the GitHub Copilot CLI programmatic reference at docs.github.com, the opencode CLI docs at opencode.ai/docs/cli, and community reports on Codex usage-limit text. Items marked UNVERIFIED were not confirmed in any page read.
