# Git

TMT commits in the repository containing the project directory — not in TMT's own
repository. You stay the author and the committer of every commit it makes; TMT is
credited beside you with a `Co-authored-by` trailer.

```
Task> commit this                        commits, does not push
Task> commit these changes and push       commits and pushes
Task> push this to main                   targets main
Task> fix the bug                         edits only, no commit, no push
```

Commit and push are separate. TMT pushes only when your own words asked for one —
"fix the bug" never triggers a push, and neither does finishing an edit.

Actions: `git_status`, `git_diff`, `git_identity`, `git_commit`, `git_push`.

It stages only the files it changed, so your unrelated work stays uncommitted. It
never creates a branch, never invents a remote, and never force-pushes. If a push
fails, the commit stays local and you get the real error.

## TMT co-authorship

TMT does not commit as you, and it does not commit instead of you. The repository's own
git identity is the author and the committer. TMT adds one trailer to the message and
nothing else. One commit, both of you credited on it.

A commit TMT made, as git reports it:

```
$ git log -1 --format=fuller
commit f1977e70a471011bf9b5ab643aecdf5e18a8e8fa
Author:     Liam <liam@example.com>
AuthorDate: Sat Aug 29 13:10:01 2026 +1200
Commit:     Liam <liam@example.com>
CommitDate: Sat Aug 29 13:10:01 2026 +1200

    Add a greeting file

    Co-authored-by: TMT code <TMT.tmt.code@gmail.com>
```

It is a git trailer, not a line of prose that happens to have a colon in it, so git
itself will hand it back:

```
$ git log -1 --format=%(trailers:key=Co-authored-by)
Co-authored-by: TMT code <TMT.tmt.code@gmail.com>
```

What that means in practice:

- The author and committer are whoever the repository's git configuration says they
  are. TMT never puts itself in either field, and never writes to your git config,
  globally or per repo.
- If you have no git identity set, git refuses the commit. TMT reports that, tells you
  to set `user.name` and `user.email` yourself, and does not stand in as the author.
  Nothing is committed.
- TMT adds the trailer only to commits it creates. A `git commit` you run yourself is
  untouched.
- A message that already credits the TMT address gets one trailer, not two. The match is
  on the address, so the same address under a different display name still counts as
  already credited.
- Existing trailers survive. A `Co-authored-by:` for someone else is kept and TMT is
  added alongside it; a `Signed-off-by:` block is joined rather than pushed into a new
  paragraph.
- History is never rewritten. If the finished commit somehow lacks the trailer, TMT
  reports it and leaves the commit alone rather than amending it.
- With no TMT address configured, or with the shipped placeholder still in place, TMT
  refuses to commit and stages nothing.

Credit on GitHub is a separate question, and TMT does not control the answer:

- TMT decides the commit metadata — the trailer, and only the trailer.
- GitHub decides whether to credit the co-author. It matches the address in the trailer
  against the addresses verified on an account. An address verified on no account is
  credited to nobody, and the display name alone does nothing.
- Even when the address does match, GitHub's contributor and profile data can lag. A
  push does not necessarily change the contributor graph straight away.
- Getting credit is not the same as being allowed to push. Authentication is separate
  and stays yours.

## Co-author identity

```
TMT_GIT_NAME=TMT code
TMT_GIT_EMAIL=someone@example.com
```

The address TMT is credited under. It is never written into a commit as the author.
Read in this order: `TMT_GIT_*` environment variables, then `.tmt_git.local`
(git-ignored, per machine), then `.tmt_git` (tracked, ships with the project). The
name defaults to `TMT code`. The email has no default, and is never taken from your
git config — your git config supplies the author, not the co-author.

Both files sit in the installation directory, so TMT is credited under the same address
in every project you point it at.

`.tmt_git` is tracked on purpose: a commit email is public metadata, not a
credential, so every clone gets the same TMT co-author without setup. It holds a name
and an address and nothing else. Put no tokens, passwords or keys in it, or in
`.tmt_git.local`.

Run `git_identity` to see which source won, which files were consulted, and whether the
address is usable.

## Setting up GitHub attribution

The tracked `.tmt_git` names the address verified on the GitHub account that represents
TMT, so a fresh clone credits it correctly with no setup. If that email is ever a
placeholder, TMT refuses to commit rather than credit a co-author who identifies
nobody. To credit an account of your own instead:

1. Create a GitHub account for TMT.
2. Add and verify an address on it.
3. Put that address in `.tmt_git.local`, or in `.tmt_git` and commit it once.

Four separate things, only one of which TMT decides:

- **Authorship** — the author and committer written into the commit. Yours. TMT reads
  it back to report it and never sets or changes it, and your `user.name` and
  `user.email` are not modified, globally or per repo.
- **Co-author credit** — the `Co-authored-by` trailer. The one part of the commit TMT
  decides.
- **GitHub attribution** — GitHub matching that trailer's address to a verified
  account. Out of TMT's hands, and a display name alone does nothing.
- **Authentication** — who may push. Stays yours: your SSH key, credential manager,
  or `gh` login. TMT stores no credentials and implements no login.

---

[← Back to the README](../README.md)
