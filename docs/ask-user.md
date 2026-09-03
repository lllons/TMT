# Asking you to decide: `ask_user`

When a decision changes what gets built and the answer is not in the code, TMT puts
the question on screen with numbered options. You press one digit and **the same turn
carries straight on** with your answer.

```
What should the database layer use?

  1. Node with better-sqlite3
  2. Python's standard-library sqlite3
  3. Something else - I will say what

Press 1, 2 or 3 to choose, or Esc to skip the question.
```

Nothing is lost while you think: TMT is mid-task, holding everything it has already
read, and your keypress is the next thing it acts on.

## Why it is not just a question in the answer

Before this, a model that did not know something had two choices and both were bad.
Guess, and possibly build the wrong thing for twenty minutes. Or end the task to ask
— which throws away everything it had loaded and makes you restate the job.

`ask_user` is the third option, and the important part is that **it does not end the
task**. `end_conversation` is the only verb that does.

## The shape

- **Two options minimum, five maximum.** Two is the fewest that is a question rather
  than a statement; five is the most that can be answered with one digit.
- **The key is the number you can see.** Option 1 is `1`. There is no second
  numbering to drift out of step with the one on screen.
- **A digit that was never offered is not an answer.** Press 4 on a three-option
  question and the question stays up — a question that accepted a choice it never
  offered would be recording a decision nobody made.
- **Esc skips it.** TMT is told you declined, and carries on with what it would have
  recommended, saying which and why. A question you cannot get out of would have
  taken the session hostage.
- **Ctrl-C stops the turn**, exactly as it does anywhere else.

## When there is nobody to ask

A piped run, a script, a CI job, the test suite — none of them has anybody at a
keyboard. TMT does not block on a keystroke that will never arrive. The action comes
back saying, in as many words, that the question could not be put, and the model is
told to decide for itself and to **say in its final message which option it chose and
that it chose it because nobody could be asked**.

So a scripted TMT never hangs on a question, and never reports a decision as though
you made it.

## Background agents cannot ask

Nobody is watching a background agent, so its question would be drawn nowhere and
answered by nobody. A worker that needs a decision reports what needs deciding, and
the agent that delegated the work — the one with you in front of it — puts the
question. The same rule, and the same reason, as the two delete verbs.

## What it should be used for

A decision that changes what gets built and that TMT cannot find in your code:
which of two libraries already in the project to build on, whether to replace an
existing module or add one beside it, which of several plausible readings of an
ambiguous request you meant.

Not for: a decision it can make from the code, a preference with an obvious default,
or anything it could simply do and report. Asking costs a round trip and your
attention; getting an easy call wrong costs one edit. The system prompt says so in
those terms, because a model that treats asking as a way of being agreeable will ask
before every edit — which is slower than doing the work and worse than getting it
wrong once.
