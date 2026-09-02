# Interface

While a task runs: a THINKING animation until the first output, then a progress bar,
elapsed time and a live token count. Model text is revealed character by character as
it streams. The final answer is boxed. A count of running agents appears beside the
meter whenever there are any.

**The agents panel is a column at the foot of the screen, not a full-height sidebar.**
It shares the live region with the reply and the prompt box; the conversation above it
keeps the full width and is never redrawn. That is a deliberate limit rather than an
unfinished one: the scrollback is TMT's only permanent record of a session, and both
escapes that would let a program own the whole window — narrowing the scrolling region,
and the alternate screen buffer — destroy it. Lines scrolled out of a narrowed region
are discarded rather than kept, so scrolling up would stop reaching the session's own
history. A test greps the modules to keep either from coming back.

On a terminal under 45 columns the panel takes the whole width of the live region and
the prompt box is not drawn while it is open; under 30 columns it refuses to open and
says why. Cards drop their activity line before their token line, and truncate rather
than wrap.

## Typing while it works

The prompt box stays live for the whole of a turn. You can write the next question
while the agent is still working on the last one, editing keys and all.

**Enter queues the line rather than interrupting.** It is answered as soon as the
current task finishes, and lines are answered in the order you entered them — so you
can stack up three follow-ups and walk away. The box says how many are waiting.

`/note` can be typed there too, which is the point of it: it answers from the workspace
without disturbing the work in progress.

Ctrl-C still stops the running task, exactly as before.

This needs a real terminal. A piped or redirected run reads one task per line and the
box is inert, which is what every scripted run and the test suite get.

Set `TMT_STREAM=0` to disable streaming. Streaming also needs `requests`; without it
TMT runs unstreamed.

---

[← Back to the README](../README.md)
