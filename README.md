# TMT

"To Many Tools" CLI coding agent.

## Quick start

Needs Python 3.8+.

```bash
git clone https://github.com/lllons/TMT.git
cd TMT
pip install requests rich      # optional: adds live streaming + colour
python TMT.py                  # Windows: py TMT.py      macOS/Linux: python3 TMT.py
```

First launch asks for an [OpenRouter key](https://openrouter.ai/keys) and saves it to `.tmt_key` (git-ignored). Set `OPENROUTER_API_KEY` in your environment to skip that.

Type a task at the `Task>` prompt; `quit` to exit. The agent only touches the `output/` folder, created beside the code on first launch.

Run the tests with `python run_tests.py`.

## TMT Git Identity & Autonomous Push

Ask TMT things like "commit this", "commit and push to main", or "push this to git"
and it will run the corresponding git commands itself, committing under its own
identity rather than yours.

### Configuration

TMT's identity comes from the first of these that supplies a value, highest first:

1. The `TMT_GIT_NAME` / `TMT_GIT_EMAIL` environment variables.
2. `.tmt_git.local` beside the code — git-ignored, per machine.
3. `.tmt_git` beside the code — tracked in the repo, so every clone has the same
   TMT identity without anyone inventing an address for it.
4. A built-in default for the name only, `TMT code`. There is no default email.

Both files are `key=value` lines; blank lines and `#` comments are ignored:

```
TMT_GIT_NAME=TMT code
TMT_GIT_EMAIL=tmt-code@example.invalid
```

The older `name=` / `email=` spelling still loads.

`.tmt_git` is tracked on purpose: a commit email is public metadata, not a
credential — it is printed in every commit of every public repo. Tokens, passwords
and keys never go in it. Use `.tmt_git.local` or the environment for anything you
do not want committed.

The tracked file ships a placeholder address, not a real one. TMT detects it and
refuses to commit rather than authoring commits under an address GitHub cannot
attribute to anyone. Without a usable email, every commit action fails with a setup
error instead of falling back to your identity.

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

No attribution happens at all while `.tmt_git` still holds the shipped placeholder.
It only starts once that line is replaced with an address verified on the GitHub
account that represents TMT.

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
account for it, add an address to that account and verify it, then put that address
in `.tmt_git` in place of the placeholder. Only then will GitHub attribute the
commits to it. This is a manual, one-time step outside TMT.
