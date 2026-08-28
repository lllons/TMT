# TMT

"To Many Tools" CLI coding agent.

## Quick start

Needs Python 3.8+.

```bash
git clone https://github.com/lllons/TMT.git
cd TMT
pip install requests rich      # optional: adds live streaming + colour
python agent1.py               # Windows: py agent1.py   macOS/Linux: python3 agent1.py
```

First launch asks for an [OpenRouter key](https://openrouter.ai/keys) and saves it to `.tmt_key` (git-ignored). Set `OPENROUTER_API_KEY` in your environment to skip that.

Type a task at the `Task>` prompt; `quit` to exit. The agent only touches the `output/` folder, created beside the code on first launch.

Run the tests with `python run_tests.py`.

## TMT Git Identity & Autonomous Push

Ask TMT things like "commit this", "commit and push to main", or "push this to git"
and it will run the corresponding git commands itself, committing under its own
identity rather than yours.

### Configuration

- `TMT_GIT_NAME` — defaults to `TMT code`.
- `TMT_GIT_EMAIL` — required, no default. TMT never reads your git config's email
  and never commits as you.

Set both as environment variables, or in a git-ignored `.tmt_git` file next to the
code:

```
name=TMT code
email=tmt-code@example.invalid
```

Without `TMT_GIT_EMAIL` set, any commit action fails with a setup error instead of
falling back to your identity.

### Git identity vs. GitHub attribution vs. GitHub auth

These are three separate things and TMT only controls one of them:

- **Git commit identity** — the author/committer name and email written into the
  commit. TMT fully controls this, via `TMT_GIT_NAME`/`TMT_GIT_EMAIL`.
- **GitHub contributor attribution** — whether GitHub shows the commit as made by
  an account, with its avatar. GitHub decides this itself by matching the commit
  email to an account's verified emails. TMT cannot force it. Setting a name alone
  does nothing for this; it only works once the configured email has been added to
  a GitHub account.
- **GitHub authentication** — who is allowed to push at all. This is unchanged and
  stays yours: your SSH key, credential manager, or `gh` login. TMT stores no
  credentials and does not implement login.

Your own git identity (global or repo-local `user.name`/`user.email`) is never
touched, so your own commits are unaffected.

### Commit and push are separate

Committing never implies pushing. TMT only pushes when your task actually asked for
one ("push", "push to main", "commit and push", "send it to github", ...). If it
didn't, the commit is made locally and TMT tells you it's ready to push, without
sending anything to the remote.

If a push fails, the commit stays local, TMT reports the real error, and it never
force-pushes.

### Setting up the TMT GitHub account

To have commits show up as made by "TMT" on GitHub: create a dedicated GitHub
account for it, add the address from `TMT_GIT_EMAIL` to that account, and verify it.
Only then will GitHub attribute the commits to it. This is a manual, one-time step
outside TMT.
