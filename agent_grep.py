"""Searching file contents.

One action for "where does this text appear". It replaces a pair of tools --
an exact, case-sensitive one that could match a block spanning lines, and a
loose, case-insensitive one that could not -- which forced the model to choose
between them before it knew which it needed. Both halves are still here and
both are a key rather than a verb: `ignore_case` is the loose half, `regex`
is the pattern half, and the default is literal and case-sensitive because
that is what grep means everywhere else.

It answers with PLACES, never with contents: a path, a line number, and the
line itself. Whatever is found is read afterwards with read_lines. There is
deliberately no option that returns a whole file, because a search that can
return the file is a search the model will reach for instead of reading, and
the point of this tool is to make reading the whole repository unnecessary.

Every count in the header is counted over everything examined rather than over
what fitted in the reply. A capped result that reports the shown figure
understates the work still out there every time it caps, which is the one
place this tool could quietly mislead the reader.
"""

import re

import agent_config
import agent_file_ops

# Matches rendered in one result, and the ceiling on a caller who asks for
# more. The cap names itself in the output when it is the reason something is
# missing -- a truncated result that reads as complete sends the model off
# believing a name has one use when it has ninety.
GREP_MAX_MATCHES = 100
GREP_HARD_MAX = 1000

# Context lines either side of a match. Ten is already most of a screen per
# match, and this tool exists to find the place rather than to read it.
GREP_MAX_CONTEXT = 10

# One line of a minified bundle is the whole bundle, so a line is cut rather
# than allowed to fill the reply on its own.
GREP_MAX_LINE = 400

# A file this size is a database, a bundle or a fixture, and reading it costs
# more than anything found inside it is worth. Skipped, but counted and named,
# because "nothing matched" and "the only place it could have been was never
# opened" are different answers.
GREP_MAX_FILE_BYTES = 2_000_000

# Derived rather than written out, so the sentence cannot go on saying "2 MB"
# after somebody changes the number above it.
_SIZE_LABEL = "%g MB" % (GREP_MAX_FILE_BYTES / 1_000_000.0)

# The characters that make a path a pattern. A model that writes path="*.py"
# means the filter: there is no workspace in which a directory literally named
# `*.py` is the useful reading, and refusing the shorthand costs a whole turn
# to say so. copy_file already accepts to/new_path/dest for the same reason.
# `[` is deliberately NOT one of them: `agent_file_ops._glob_to_regex` escapes
# it, so a bracket is an ordinary character to the matcher rather than a class.
# Reading it as a pattern would send a directory genuinely named `[draft]` down
# the filter path, where it would be compared against whole relative paths and
# match nothing -- a real subtree turned into an empty result by a guess.
_GLOB_MARKS = "*?"

_NO_QUERY = "grep needs a 'query' -- there is nothing to look for."


def _as_context(value):
    """Context lines clamped into range, or None when it was not a number.

    None rather than a default, because a model that wrote "two" meant
    something and silently reading it as zero hides the mistake in a result
    that looks like an answer.
    """
    # A bool is refused by name. `int(True)` is 1, so `"context": true` would
    # quietly become one line of context -- an answer, for a key the model
    # plainly did not mean as a count. `agent_verify.VerificationCheck.record`
    # refuses True the same way and for the same reason.
    if isinstance(value, bool):
        return None
    try:
        return max(0, min(GREP_MAX_CONTEXT, int(value or 0)))
    except (TypeError, ValueError):
        return None


def _as_limit(value):
    """The match cap clamped into range, or None when it was not a number."""
    if isinstance(value, bool):        # see _as_context: True is not a count
        return None
    try:
        cap = GREP_MAX_MATCHES if value in (None, "") else int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(GREP_HARD_MAX, cap))


def _compile(subject, regex, ignore_case):
    """(pattern, error message). A literal needle is escaped, never trusted.

    Both modes end up as one compiled pattern so that ignore_case is the same
    flag in both -- the alternative is a lowercased copy of every file for the
    literal path, which is a second matching engine with its own edge cases.
    """
    flags = re.IGNORECASE if ignore_case else 0
    if not regex:
        return re.compile(re.escape(subject), flags), None
    try:
        return re.compile(subject, flags), None
    except re.error as error:
        return None, "Invalid regex: %s" % error


def _read(path):
    """(text, skip reason) for one file. Never raises.

    The cheap question is asked first: a size taken from stat costs nothing,
    where reading a 40 MB fixture to discover it is too big costs the action.
    """
    try:
        if path.stat().st_size > GREP_MAX_FILE_BYTES:
            return None, "oversize"
    except OSError:
        return None, "unreadable"
    try:
        data = path.read_bytes()
    except OSError:
        return None, "unreadable"
    if agent_file_ops.looks_binary(data):
        return None, "binary"
    # to_lf here as well as on the needle: a CRLF file must match a needle
    # written with plain newlines, and the line numbers below count "\n".
    return agent_file_ops.to_lf(data.decode("utf-8", "replace")), None


def _matches(pattern, text, regex):
    """(lines, [(start line, end line)]) -- one entry per line a match begins on.

    A literal needle is searched over the whole text so that it can span
    lines, which is the half of this tool the old exact search existed for,
    and it reports the line the match STARTS on. A regex is searched line by
    line, because a pattern allowed to span lines lets one `.*` swallow the
    file and report it as a single match.

    One entry per starting line in both modes: the result is a list of places
    to go and read, and the same place named twice is not two places. It also
    keeps the header's count and the number of rows the same number, which is
    what makes "showing the first 100" arithmetic anybody can follow.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if regex:
        return lines, [(n, n) for n, line in enumerate(lines, 1)
                       if pattern.search(line)]
    found, seen = [], set()
    for match in pattern.finditer(text):
        start = text.count("\n", 0, match.start()) + 1
        if start in seen:
            continue
        seen.add(start)
        # The last character the match covers, not the position after it, or a
        # needle ending in a newline would be reported one line too far down.
        last = max(match.start(), match.end() - 1)
        found.append((start, text.count("\n", 0, last) + 1))
    return lines, found


def _clip(body):
    """A line cut to fit, saying so. The marker is what stops a truncated
    line being read as the whole line."""
    if len(body) > GREP_MAX_LINE:
        return body[:GREP_MAX_LINE] + " ..."
    return body


def _row(relative, lines, start):
    """The flat form: one match, one line, ready to be scanned down.

    Stripped, because the indentation of a line seen out of its block carries
    nothing and costs the columns the line itself needs.
    """
    body = lines[start - 1] if 0 < start <= len(lines) else ""
    return "%s:%d: %s" % (agent_file_ops.posix(relative), start,
                          _clip(body.strip()))


def _block(relative, lines, start, end, context):
    """One match block: a locator line, then the matched lines and any context.

    `>` marks the match and `|` marks context, so the two read apart with ANSI
    stripped -- colour is never allowed to be the only thing carrying it. The
    bodies keep their indentation here, because a block is read as code.
    """
    first = max(1, start - context)
    last = min(len(lines), end + context)
    out = ["%s:%d:" % (agent_file_ops.posix(relative), start)]
    for number in range(first, last + 1):
        marker = ">" if start <= number <= end else "|"
        out.append("%6d %s %s" % (number, marker, _clip(lines[number - 1])))
    out.append("")
    return "\n".join(out)


def _first_line(subject):
    """The head of the query, for a message that has to fit on one row."""
    return (subject.splitlines() or [""])[0][:120]


def grep(query, path=None, glob=None, regex=False, ignore_case=False,
         context=0, limit=None):
    """Find `query` in the workspace's files and say where it is.

    Literal and case-SENSITIVE by default: `Path` is not `path`, and a model
    that cannot tell them apart edits on a guess. `regex=True` compiles the
    query instead, and `ignore_case=True` loosens either mode.

    `path` narrows to a subtree or a single file and goes through safe_path
    inside search_targets, so anything outside the workspace raises ValueError
    and the agent loop hands the model a correction rather than an empty
    result that reads as "there is nothing there".
    """
    if not isinstance(query, str) or not query:
        return _NO_QUERY
    # Answered in words rather than left to raise. `safe_path` builds
    # `root / user_path`, which is a TypeError for anything that is not a
    # path-like, and `_run_tool` catches only the ValueError a refusal is made
    # of -- so without this the one bad shape that is not a security question
    # would be the one that escapes as a traceback.
    if path is not None and not isinstance(path, str):
        return "path must be a folder or file path written as text"
    if glob is not None and not isinstance(glob, str):
        return "glob must be a path pattern written as text"

    context = _as_context(context)
    if context is None:
        return "context must be a whole number of lines"
    cap = _as_limit(limit)
    if cap is None:
        return "limit must be a whole number of matches"

    regex = bool(regex)
    ignore_case = bool(ignore_case)
    if regex:
        # Compiled exactly as it was written. Decoding a regex would rewrite
        # its author's own escapes, which is a change of meaning rather than
        # the convenience it is for a literal.
        subject = query
    else:
        subject = agent_file_ops.to_lf(agent_file_ops.decode_content(query))
        if not subject:
            return _NO_QUERY
    pattern, error = _compile(subject, regex, ignore_case)
    if error:
        return error

    if path and not glob and any(mark in str(path) for mark in _GLOB_MARKS):
        path, glob = None, path

    targets, scan_capped = agent_file_ops.search_targets(path, glob)
    if targets is None:
        return "Path not found: %s" % path

    rendered, total, files_hit = [], 0, 0
    skipped = {"binary": 0, "oversize": 0, "unreadable": 0}
    for relative, absolute in targets:
        if not absolute.is_file():
            continue
        # Asked before the file is opened rather than after it is read: a
        # symlink out of the workspace is refused by not reading it, and a
        # guard that reads first has already done the thing it forbids.
        if not agent_file_ops.within_workspace(absolute):
            continue
        text, reason = _read(absolute)
        if reason:
            skipped[reason] += 1
            continue
        lines, found = _matches(pattern, text, regex)
        if not found:
            continue
        files_hit += 1
        for start, end in found:
            # Counted whatever happens, rendered only while there is room.
            # Every file is examined even once the cap is spent, because the
            # header's total is the one figure the reader cannot check.
            total += 1
            if total > cap:
                continue
            rendered.append(_block(relative, lines, start, end, context)
                            if context else _row(relative, lines, start))

    if not total:
        out = ["No match for: %s" % _first_line(subject)]
        if not ignore_case:
            # Named because it is the likeliest reason a search that should
            # have worked did not, and the fix is one key.
            out.append('Matching is case-sensitive; pass "ignore_case": true '
                       "for a loose match.")
        if glob:
            out.append("Only paths matching %s were examined." % glob)
        if path:
            out.append("Only paths under %s were examined." % path)
        if skipped["binary"]:
            out.append("%s skipped."
                       % agent_file_ops.plural(skipped["binary"], "binary file"))
        return "\n".join(out)

    head = "%s in %s" % (agent_file_ops.plural(total, "match", "matches"),
                         agent_file_ops.plural(files_hit, "file"))
    if total > cap:
        head += (" -- showing the first %d, the limit for one result. "
                 "Narrow the query, or pass a 'glob'." % cap)
    out = [head, ""] + rendered
    if skipped["binary"]:
        out.append("(%s skipped.)"
                   % agent_file_ops.plural(skipped["binary"], "binary file"))
    if skipped["oversize"]:
        out.append("(%s over %s skipped.)"
                   % (agent_file_ops.plural(skipped["oversize"], "file"),
                      _SIZE_LABEL))
    if skipped["unreadable"]:
        out.append("(%s skipped.)"
                   % agent_file_ops.plural(skipped["unreadable"],
                                           "unreadable file"))
    if scan_capped:
        out.append("(The walk stopped at %d entries, so files beyond that "
                   "were never examined.)" % agent_config.WORKSPACE_MAX_SCAN)
    return "\n".join(out).rstrip()
