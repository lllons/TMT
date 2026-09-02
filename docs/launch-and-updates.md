# The launch screen

Every launch of `tmtcode` opens on the same screen: the TMT wordmark filling the
terminal, and under it

```
                              Press Enter to Continue
```

The line pulses while it waits — the gradient sweeping along it on a colour terminal,
a slow weight pulse on one without colour, and nothing at all where escapes cannot be
used, because colour is never the message here either. Enter is the only key that goes
on; Ctrl-C closes TMT. Everything else is ignored, so a first task typed before the
screen has settled cannot set something off.

**The launch screen always appears.** It is not a setting and there is nothing to turn
off. What *is* a setting is what happens after Enter.

## After Enter: the update check

With `Auto Update on Launch` **on** (the default), TMT looks at its own git checkout to
see whether a newer version exists, and says so on the same screen:

```
                              Searching for updates...
```

and then one of

```
                                    Up to date.            nothing was pulled, nothing restarted
                          Update complete. Restarting...   a fast-forward was applied
                            Continuing without updating.   an update could not be taken safely
                Update check failed. Continuing without update.
```

With the setting **off**, none of that happens and TMT does not pretend it did — no
"searching" line is shown for a search that was never made. It goes straight on.

After that, TMT continues exactly as it always has: the API-key setup if this
installation has not been configured yet, and otherwise the ordinary start screen.

## When TMT will and will not update itself

**Every launch asks the same question, whichever way TMT was installed.** npm,
`pip install -e .` and running from a clone all leave TMT's code in a git checkout, and
the updater looks at whichever directory TMT's own code is in — so there is no
npm-specific update path and nothing to configure. The one install that cannot update
is one that is not a checkout at all: a copied folder, or a non-editable `pip install .`
into site-packages. That case says so, and names the two installs that do keep current.

It updates only when the update is unambiguously safe, and it never touches your work.

| What it finds | What it does |
|---|---|
| already current | nothing. No pull, no restart |
| remote ahead, clean tree, fast-forward possible | fast-forwards, then restarts |
| **uncommitted local changes** | tells you an update is waiting and how many commits, and applies nothing. Your changes are untouched |
| **the branch has diverged** — local and remote both moved | refuses. Local commits are never discarded |
| no upstream configured, or a detached HEAD | says it cannot tell, and continues |
| not a git checkout at all | says this install cannot update itself, and how to install one that can |
| no network, no git, a bad remote, a failed merge | reports the failure and continues |

It works on the branch you already have checked out and never creates, switches or
forces one. It uses `git fetch` and `git merge --ff-only` and nothing else: **it never
runs `git reset --hard`, `git clean`, a force checkout, or a plain `git pull`** — a
pull can merge, and a merge during startup is exactly what must not happen. A test
reads the updater's own source and asserts those commands do not appear in it.

TMT stays usable with no internet. A failed update check is a line on the splash and
nothing more.

An npm install updates itself like any other: `~/.tmtcode` is an ordinary shallow
clone with an upstream, so it is a checkout the updater can fast-forward rather than a
folder somebody copied. Reinstalling with npm is not how you update TMT — launching it
is.

A checkout you have edited still checks and still reports; what it will not do is apply
anything on top of your changes. That ordering was the other way round until 2026-09-02,
and an edited checkout used to skip the check entirely — which meant it quietly stopped
being told about updates for as long as the edit sat there.

## Restarting

A successful update replaces the process with a fresh one, so the new code really runs
rather than the old modules staying imported. Your command line is preserved —
`tmtcode --dir ~/project` comes back as `tmtcode --dir ~/project`.

You then see the launch screen again, which is expected: the launch screen is part of
every startup. The restarted process finds itself current and continues. **It cannot
loop** — at most one automatic restart happens per launch, and the second process
knows it is the second.

## Turning it off

Settings → `Auto Update on Launch`, Enter to toggle:

```
  AI Provider            Which service answers a request
  API Key                The credential that service is given
  Model                  Which model TMT runs on
> Auto Update on Launch  Check for a newer TMT after the launch screen  ON
  Back                   Return to the menu
```

It is stored in `.tmt_autoupdate` in the installation directory, beside the model and
effort settings, so it belongs to the install rather than to one project and survives
restarts. A missing file means on; a file nobody can read, or one edited into nonsense,
also means on rather than an error at startup.

**Turning it off does not turn off the launch screen.** The splash is shown either way.

---

[← Back to the README](../README.md)
