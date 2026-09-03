"""The tip under the session header, and the rotation that keeps it moving.

TMT opens every session on the same three facts -- the wordmark, the date and
the workspace -- and none of them teaches anybody anything. A user who never
finds out that `/note` exists will never type it, and there is no screen in
TMT whose job is to tell them: the Help screen is in the menu, which is the
one place somebody who is already working is not looking.

So the header carries one line of what this program can do, and a different
one each time it is drawn.

**The catalogue is the whole of the feature and it is a claim about the
product.** Every entry here is a thing TMT actually does, written from the
code that does it rather than from what it ought to do -- a tip is advice the
user will act on, so a wrong one costs them a keystroke and a little of their
trust in everything else on the screen. There is a test that reads the
command registry and asserts every slash command named here is one that
exists, which is the half of it a machine can check; the other half is that
somebody wrote these having read the handlers.

**This module is pure apart from one function.** The catalogue, the arithmetic
and the shape of a tip are plain data and plain functions, testable without a
filesystem; `next_tip` is the single impure entry point, and it is impure
because the rotation has to survive a launch. Where it survives is
`agent_config`'s business -- the cursor is per-installation state and lives in
INSTALL_DIR beside the model and the effort level, never in the workspace.

Nothing here decides how the row is DRAWN. The width, the colour, the
separator and the degradation are `agent_menu`'s, which is the one place in
this project that defines any of those; what this module hands over is two
strings.
"""

import agent_config

# A tip is (gesture, detail): the thing to type or press, and what it does,
# written so the two read as one sentence in that order.
#
# Two fields rather than one string because the row has to degrade. A terminal
# too narrow for the sentence still has room for the gesture, and a gesture on
# its own is a true and useful thing to show -- where "Press Ent" is neither.
# It is the launch screen's rule about its own subtitle, applied to the one
# other place TMT writes a sentence somebody has to act on: give up a TIER,
# never the right-hand end of the words.
#
# The details are written to be read after the gesture and are deliberately
# not sentences of their own: "answers a question" completes "/note", and
# putting a full stop on it would make the row look like prose that had been
# cut in half.
TIPS = (
    ("/note", "answers a question about this project without changing it"),
    ("/notes", "shows what TMT knows about this project and what is left"),
    ("/plan", "makes TMT list its steps and finish them before it answers"),
    ("/review", "has a second agent audit the work before you see the answer"),
    ("/verify", "runs the checks this project already has, and reports them"),
    ("/undo", "puts the workspace back to before a turn changed it"),
    ("/checkpoints", "lists the turns TMT can put the workspace back to"),
    ("/agents", "lists the background agents and what each one is doing"),
    ("/back", "opens the menu without ending the session you are in"),
    ("/clear", "forgets the conversation and keeps everything else"),
    ("/context", "says how much of the model's window the conversation fills"),
    ("/config", "states the provider, model and effort a request runs under"),
    ("/model", "lists the models this provider offers, and switches them"),
    ("/effort high", "lets one task take up to 60 steps instead of 35"),
    ("/effort low", "keeps a small task short: 12 steps rather than 35"),
    ("/plan fix the parser", "does both: authorises the plan, sends the task"),
    ("Tab", "completes a slash command while you are still typing it"),
    ("Right Arrow", "at the end of the line opens the agents panel"),
    ("Esc", "clears the line you are typing; type quit to close TMT"),
    ("Ctrl-C", "stops the turn that is running, and keeps the session"),
    ("Typing while TMT works", "queues the line for the moment the turn ends"),
    ("A pasted block", "folds to one token in the box and is sent whole"),
    ("A push", "needs your own words: say commit and push to main"),
    ("Every commit", "keeps you as the author and adds TMT as a co-author"),
    ("Answering a to a command", "allows it in this project from now on"),
    ("An unfamiliar command", "is put to you before anything runs"),
    ("Deleting a file", "asks you first, whatever the task said"),
    ("A numbered question", "is answered with one key, and the turn goes on"),
    ("tmtcode <path>", "opens a session on whichever project you point it at"),
    ("Settings", "holds the provider, the API key and the model TMT runs on"),
    ("Auto Update on Launch", "in Settings keeps TMT's own checkout current"),
    ("The Help screen", "is in the menu, and /back reaches it mid-session"),
    ("Background work", "goes to background agents, and up to 10 run at once"),
    ("TMT_Context/notes.md", "is what TMT remembers about this project"),
    ("The meter above the box", "counts lines changed and tokens as you go"),
    ("The workspace", "is the directory you launched in; edits stay there"),
)


def count():
    """How many tips there are. Read rather than written down twice."""
    return len(TIPS)


def tip_at(index):
    """The (gesture, detail) at `index`, which may be any integer.

    Wrapped rather than bounded, so a cursor that has run past the end of the
    catalogue -- or a file edited by hand into something absurd -- names a tip
    instead of an error. There is no index this can refuse.
    """
    try:
        position = int(index)
    except (TypeError, ValueError):
        position = 0
    return TIPS[position % len(TIPS)]


def following(index):
    """The cursor after `index`, wrapped back to the start at the end.

    Sequential rather than random, which is the difference between a rotation
    a user can get through and one that shows them the same three tips all
    week. Thirty-odd launches sees all of them exactly once.
    """
    try:
        position = int(index)
    except (TypeError, ValueError):
        position = 0
    return (position + 1) % len(TIPS)


def next_tip():
    """The tip to show now, advancing the stored cursor. Never raises.

    The one impure function here, and it is called from exactly the two places
    the session header is drawn -- opening a session, and coming back to one
    through `/back`. That is what "each time the user reaches this screen"
    means in code: not a timer, not a random draw per repaint, but one step of
    a cursor per drawing of the header.

    It advances BEFORE it is used, so the tip a launch shows is the one after
    the tip the launch before it showed. Storing afterwards would mean a run
    that crashed between the two showed the same tip again next time, which is
    a small thing to get wrong and an easy one to avoid.

    Nothing about a decoration is worth an exception in the middle of drawing
    a header, so a cursor that cannot be read reads as the first tip and one
    that cannot be stored is simply not stored -- the tip repeats, which is
    the smallest possible failure and is invisible next to the alternative.
    """
    try:
        position = following(agent_config.read_saved_tip_cursor())
        agent_config.set_tip_cursor(position)
    except Exception:
        position = 0
    return tip_at(position)
