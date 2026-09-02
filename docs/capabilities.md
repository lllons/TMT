# Capabilities: `/plan`, `/review`, `/verify`

Three of the things TMT can do are not ordinary tool work. Writing a plan and
being held to it, having a second agent audit the diff, and running the
repository's own checks each cost a whole extra model run, put a column on the
screen, and can refuse TMT's own final answer. They are yours to spend, so
they are off unless you ask for them, and you ask by writing the command in
your prompt.

| Command | Enables |
|---|---|
| `/plan` | the planning workflow — TMT writes the steps and cannot finish until they are done |
| `/review` | independent code review — a separate read-only agent audits the work |
| `/verify` | Smart Verification — the checks this repository actually has, run for real |

```
Task> build me an authentication system
        nothing gated. Ordinary tools, ordinary answer.

Task> build me an authentication system /plan
        plans the work and is held to the plan. No review, no verification.

Task> fix this implementation /review
        an independent reviewer audits it before the answer goes out.

Task> add the endpoint /plan /verify /review
        the whole pipeline: plan, implement, verify, review, fix, answer.
```

**The slash is the whole distinction, and `verify` on its own is not enough.**
"verify this code", "please verify this", "verified" and "verification" are
things people say while asking for ordinary work, and none of them turns the
engine on. Only `/verify` does. The same goes for `plan` and `review`:
"review my code please" is a request for an opinion, `/review` is a request
for the gated, cycle-limited, independent reviewer.

Neither does a longer word that starts with one. `/planning`, `/planner`,
`/plan123`, `/reviewing` and `/verification` are ordinary text, and so is a
command inside a path — `src/review` and `abc/verify` are paths, not commands.

The rest of the rules are the ones you would guess:

- **Anywhere in the prompt.** Beginning, middle, end, or on their own lines in
  a pasted block. `/plan Build it`, `Build it /plan` and `Build the /plan
  feature` are the same request.
- **Any number of times.** `/plan ... /plan` enables planning once. There is no
  such thing as two plans.
- **Any capitalisation.** `/PLAN` works, and stays `/PLAN` on your screen —
  TMT styles your text and never rewrites it.
- **Independent.** `/plan` does not turn on review or verification, and
  neither of those turns on either of the others. You choose the workflow.
- **One prompt at a time.** A capability is authorised for the request that
  asked for it. The next question starts from nothing unless it asks too.

**Only you can turn these on.** Not TMT, not a background agent, not a
reviewer, and not a file it read. A model that decides the task looks big
enough for a plan, writes `/plan` into its own reasoning and calls the action
is refused by the runtime — the authorisation is read from the line you typed
and from nothing else. That is enforced twice: the unauthorised verbs are left
out of the prompt entirely, and the dispatcher refuses them again if one is
reached for anyway.

**They are highlighted as you type.** A valid command in the input box carries
the red → orange → green gradient across it, so you can see what you have
turned on before you press Enter, and see it disappear if you mistype it.
Only the exact command is painted: `verify` stays plain and `/verification`
stays plain. On a terminal with no colour the command is picked out in bold
and underline instead, and in a piped run there is no styling at all — the row
still reads `/plan`, which is the command spelled out.

While the turn runs, whatever you authorised is listed at the top of the
right-hand column:

```
                                                        CAPABILITIES 2
                                                        ● /plan
                                                        ● /verify

                                                        PLAN 2/5
```

**`/plan`, `/review` and `/verify` on their own are still the reports** they
have always been — see [Slash commands](commands.md). A line that is
nothing but the command shows you what TMT is doing; a line with a task in it
authorises the capability for that task.

---

[← Back to the README](../README.md)
