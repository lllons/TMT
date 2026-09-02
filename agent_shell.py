"""The command line, read by TMT rather than handed to a shell.

Pipes, `&&`, `||`, `;` and redirections all work, and no shell is ever
invoked on a model-authored string. That is the whole point of this module:
the moment a line is passed to `/bin/sh` or to `cmd.exe`, every guard
downstream stops being able to see what is about to run -- the string is a
program in a language the guard does not read, and a policy about arguments
cannot inspect a program. So TMT reads the line itself, hands back a
structure, and the only thing that ever reaches an operating system is an
argv list somebody has already looked at.

It is pure state and text, in the division `agent_plan`, `agent_review`,
`agent_verify`, `agent_reviewbot` and `agent_delegation` all keep: no model,
no terminal, no threads, and nothing here creates a child process. `expand()`
is the single exception and it reads the filesystem only -- it is here
because globbing is part of understanding a command line, and it takes the
directory to expand against as an argument so it can be tested without a
session.

Four decisions are load-bearing.

**A substitution is refused, not evaluated.** `$(...)`, backticks, `${...}`
and `$VAR` are all refused with a sentence. Every guard between here and the
process reads ARGUMENTS: the policy asks what the program is, whether a path
escapes the workspace, whether a flag is inline code. A substitution is a
second command, or a value nobody can see, hiding inside one of those
arguments -- so the guard would be reading text that is not what runs. The
refusal is not a limitation being apologised for; it is the reason the rest
of the design can make a claim at all.

**Globbing is TMT's, and it never leaves the workspace.** A shell expands
`*.py` by reading the directory; here that read goes through
`agent_file_ops.iter_workspace_entries` and `glob_filter`, the same walk and
the same pattern compiler `glob` and `grep` use, and every match is put
through `within_workspace` before it is returned. There is no second walk, no
second pattern language and no second containment test -- a second answer to
"what counts as inside the workspace" is the kind of thing that gets updated
in one place only.

**Every refusal is a sentence the model can act on.** A bare "syntax error"
costs a round and teaches nothing; the model retries the same shape because
it has not been told which shape was wrong. Each refusal here names what was
refused and what to write instead.

**`describe()` must be faithful.** It is what the user and the model are
shown as TMT's reading of the line, and it appears in refusals -- so if the
render and the execution could disagree, the render would be a lie about
what is running. It is written so that re-parsing its output gives the same
structure back, and the quoting exists for that reason rather than for
looks.
"""

from pathlib import Path

import agent_file_ops


# --- what the parse is made of ---------------------------------------------

# The operators that separate one stage from the next. `|` is not here: it
# joins commands INSIDE a stage, which is a different relationship -- the two
# sides of a pipe are one job, and the two sides of `&&` are two jobs where
# the second depends on how the first ended.
STAGE_OPERATORS = ("&&", "||", ";")

# Every redirection TMT understands. Deliberately short. Each one has a plain
# meaning that survives being written into a log, and anything outside this
# set is refused by name rather than half-supported: a redirect that is parsed
# and then not applied is the render disagreeing with the execution.
REDIRECT_KINDS = (">", ">>", "<", "2>", "2>&1")

# Which of the child's descriptors each redirect speaks about. Kept as a
# table rather than worked out at each site so that `Redirect.fd` cannot mean
# one thing where it is built and another where it is read.
_REDIRECT_FD = {">": 1, ">>": 1, "<": 0, "2>": 2, "2>&1": 2}

# The characters that make an argument a candidate for expansion. `[` is
# absent on purpose: `glob_filter` compiles `*`, `?` and `**` and treats a
# bracket as an ordinary character, so a class would be matched literally and
# almost never hit. Leaving it out means `[abc].py` is passed through as
# written, which is the same thing that happens to any pattern with no match,
# rather than being silently reinterpreted.
GLOB_CHARS = "*?"

# What a backslash may escape outside quotes: the characters this parser gives
# a meaning to, plus whitespace and the quotes themselves. Before anything else
# a backslash is an ordinary backslash, so a Windows path written unquoted --
# `src\main.py` -- survives as itself instead of quietly becoming `srcmain.py`.
# `$` is in the set because `\$` is the documented way to write a literal
# dollar, and that escape is what makes refusing substitutions liveable.
_ESCAPABLE = frozenset("\\\"'$`|&;<>()#*? \t")


class ShellError(Exception):
    """A refusal, carrying a sentence the model can act on.

    Every raise in this module says what was refused AND what to write
    instead. That is not politeness: the model's only route out of a refusal
    is its next attempt, and an attempt written from "syntax error" is the
    same attempt again.
    """


class Redirect:
    """One redirection: where a descriptor of the command is pointed.

    `kind` is one of REDIRECT_KINDS. `target` is the file named after it, and
    is None for `2>&1`, which names a descriptor rather than a file. `fd` is
    the descriptor being redirected -- 1 for `>` and `>>`, 0 for `<`, 2 for
    `2>` and `2>&1` -- so a caller wiring the pipeline does not have to know
    the table.
    """

    __slots__ = ("kind", "target", "fd")

    def __init__(self, kind, target=None, fd=None):
        self.kind = kind
        self.target = target
        self.fd = _REDIRECT_FD.get(kind, 1) if fd is None else fd

    def __eq__(self, other):
        if not isinstance(other, Redirect):
            return NotImplemented
        return (self.kind, self.target, self.fd) == (other.kind, other.target, other.fd)

    def __repr__(self):
        return "Redirect(%r, %r, %r)" % (self.kind, self.target, self.fd)


class Command:
    """One program and its arguments, with whatever was redirected on it.

    `argv` is already unquoted: `"a b"` is one entry, and nothing downstream
    has to know what quoting produced it. That is the point of parsing here
    rather than passing a string on -- the policy layer reads these entries
    as the program will receive them.
    """

    __slots__ = ("argv", "redirects")

    def __init__(self, argv, redirects=None):
        self.argv = list(argv)
        self.redirects = list(redirects or ())

    @property
    def program(self):
        """The name being run, or "" for a command with no argv.

        A convenience for the policy layer, which asks this of every command
        it classifies. An empty argv never reaches a caller -- `parse` refuses
        it -- but the property answers rather than raising so that a caller
        working on a hand-built Command is not surprised.
        """
        return self.argv[0] if self.argv else ""

    def __eq__(self, other):
        if not isinstance(other, Command):
            return NotImplemented
        return self.argv == other.argv and self.redirects == other.redirects

    def __repr__(self):
        return "Command(%r, %r)" % (self.argv, self.redirects)


class Pipeline:
    """Commands joined by `|`, run together, output flowing left to right."""

    __slots__ = ("commands",)

    def __init__(self, commands):
        self.commands = list(commands)

    def __eq__(self, other):
        if not isinstance(other, Pipeline):
            return NotImplemented
        return self.commands == other.commands

    def __repr__(self):
        return "Pipeline(%r)" % (self.commands,)


class Stage:
    """One pipeline, and the operator that PRECEDES it.

    Carrying the preceding operator rather than the following one is what
    lets a caller evaluate `a && b || c` in one left-to-right walk: at each
    stage it already knows, from the stage itself, whether it should run
    given how the last one ended. The alternative -- an operator pointing
    forward -- makes the caller look ahead, or re-parse, to answer the same
    question.

    The first stage's operator is "", because nothing precedes it.
    """

    __slots__ = ("pipeline", "operator")

    def __init__(self, pipeline, operator=""):
        self.pipeline = pipeline
        self.operator = operator

    @property
    def commands(self):
        """The pipeline's commands, for a caller that only wants to read them."""
        return self.pipeline.commands

    def __eq__(self, other):
        if not isinstance(other, Stage):
            return NotImplemented
        return self.pipeline == other.pipeline and self.operator == other.operator

    def __repr__(self):
        return "Stage(%r, %r)" % (self.pipeline, self.operator)


# --- the refusals, written once --------------------------------------------
#
# The wording lives here rather than at each raise so that the same mistake
# always reads the same way. A model that meets one of these twice in a turn
# should see the identical sentence and conclude the shape is wrong, not that
# it was unlucky.

_COMMAND_SUBSTITUTION = (
    "Command substitution is not available: {form} runs a second command "
    "inside an argument, and TMT decides what may run by reading arguments. "
    "Run that command as its own bash call, read the output, and write the "
    "value you need literally."
)

_VARIABLE_SUBSTITUTION = (
    "Variable expansion is not available: {form} would be read against an "
    "environment TMT builds for the child and you cannot see, so the "
    "argument that runs would not be the argument shown. Write the value "
    "literally, or use a path relative to the working directory."
)

_LITERAL_DOLLAR_HINT = (
    " To pass a literal dollar sign, escape it outside single quotes: \\$."
)

_BACKGROUND = (
    "Background execution with & is not available in a command line. Use the "
    "bash tool's \"operation\": \"start\" instead, which registers the job so "
    "it can be watched with status and logs, and stopped."
)

_HEREDOC = (
    "Here-documents (<<) and here-strings (<<<) are not supported. Write the "
    "text to a file with write_file and redirect it in with <, or pass it as "
    "an ordinary quoted argument."
)

_PROCESS_SUBSTITUTION = (
    "Process substitution ({form}) is not supported: it runs a second command "
    "inside an argument. Run that command as its own stage, redirect it into "
    "a file, and read the file."
)

_DUPLICATION = (
    "Only 2>&1 is supported for redirecting one stream into another. To send "
    "both output streams to one file, write: command > file 2>&1"
)

_GROUPING = (
    "Subshells and grouping with ( ) are not supported. Sequence commands "
    "with &&, || or ; instead, or quote the parentheses if they are part of "
    "an argument."
)


def _dollar_refusal(text, i):
    """The sentence for a `$` at position i, or None when it is a literal.

    A `$` that is not followed by a name, a brace or a parenthesis is not a
    substitution in any shell either -- `grep 'total$'` and `echo 5$` mean
    exactly what they say -- and refusing those would make ordinary regular
    expressions unwritable. So the check is on what FOLLOWS the dollar, not
    on the character itself.
    """
    nxt = text[i + 1] if i + 1 < len(text) else ""
    if nxt == "(":
        return _COMMAND_SUBSTITUTION.format(form="$(...)")
    if nxt == "{":
        return _VARIABLE_SUBSTITUTION.format(form="${...}") + _LITERAL_DOLLAR_HINT
    # $1, $?, $@, $*, $#, $$, $! are all expansions too, and a model that
    # reaches for one is thinking in shell script rather than in one command.
    if nxt and (nxt.isalnum() or nxt in "_?@*#$!"):
        # The whole name is quoted back, not just the first character: a
        # refusal that says "$H" when the line said "$HOME" reads as though
        # TMT misparsed it, and the model's next attempt corrects the wrong
        # thing.
        name = ""
        j = i + 1
        while j < len(text) and (text[j].isalnum() or text[j] == "_"):
            name += text[j]
            j += 1
        return (_VARIABLE_SUBSTITUTION.format(form="$" + (name or nxt))
                + _LITERAL_DOLLAR_HINT)
    return None


# --- reading the text ------------------------------------------------------
#
# One pass, producing ("word", text), ("op", text) and ("nl", ";").
#
# A newline is kept apart from a written `;` for one reason: a line that ends
# with a newline has not ended with an operator, and refusing
# "python x.py\n" for a trailing separator would refuse the commonest thing a
# model can write. A written `;` at the end IS a trailing operator and is
# refused, because the spec asks for that -- see the note on `parse`.


def _scan_single(text, i):
    """A single-quoted run, returned literally. i is at the opening quote.

    Nothing inside is interpreted: no escapes, no expansion. The one thing
    that is still checked is a substitution, which a real shell would treat
    as inert here. Refusing it anyway is deliberate and is stricter than a
    shell: '$(id)' is harmless to bash and harmless here, but "a dollar sign
    means a substitution and TMT refuses substitutions" is a rule a model can
    hold in one piece, where "unless it is inside single quotes, in which
    case it is fine, unless the program you are calling expands it itself"
    is a rule nobody applies correctly under pressure. The escape hatch is
    named in the refusal: \\$ outside single quotes is a literal dollar.
    """
    i += 1
    out = []
    while i < len(text):
        ch = text[i]
        if ch == "'":
            return "".join(out), i + 1
        if ch == "$":
            message = _dollar_refusal(text, i)
            if message:
                raise ShellError(message)
        if ch == "`":
            raise ShellError(_COMMAND_SUBSTITUTION.format(form="`...`"))
        out.append(ch)
        i += 1
    raise ShellError(
        "Unterminated single quote: the command ends inside a quoted string. "
        "Close the quote, or escape the apostrophe with a backslash if it was "
        "meant as an ordinary character."
    )


def _scan_double(text, i):
    """A double-quoted run. i is at the opening quote.

    Double quotes GROUP and expand nothing, because there is nothing left to
    expand: substitution is refused everywhere. So "a b" is one argument and
    that is all it is. A backslash is still an escape before the four
    characters a shell treats specially inside double quotes ($, `, " and \\)
    and is an ordinary backslash before anything else -- which is what bash
    does, and which matters here because "C:\\temp\\new" must not lose its \\n.
    """
    i += 1
    out = []
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt == "\n":
                i += 2
                continue
            if nxt in "$`\"\\":
                out.append(nxt)
                i += 2
                continue
        if ch == '"':
            return "".join(out), i + 1
        if ch == "$":
            message = _dollar_refusal(text, i)
            if message:
                raise ShellError(message)
        if ch == "`":
            raise ShellError(_COMMAND_SUBSTITUTION.format(form="`...`"))
        out.append(ch)
        i += 1
    raise ShellError(
        "Unterminated double quote: the command ends inside a quoted string. "
        "Close the quote, or escape the quote character with a backslash if "
        "it was meant as an ordinary character."
    )


def _tokenize(text):
    """The line as words, operators and separators. Raises ShellError."""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    tokens = []
    word = []
    started = False     # a word has begun, so "" and '' produce an empty one

    def flush():
        if started:
            tokens.append(("word", "".join(word)))
            del word[:]
        return False

    def emit(op):
        tokens.append(("op", op))

    def separator_wanted():
        # A newline after an operator continues the line, exactly as it does
        # in a shell, and two blank lines in a row are not two empty
        # commands. So a newline becomes a separator only when there is a
        # finished command in front of it.
        return bool(tokens) and tokens[-1][0] == "word"

    i, n = 0, len(text)
    while i < n:
        ch = text[i]

        if ch == "\\":
            # A backslash escapes only what there is a reason to escape: the
            # characters this parser gives a meaning to, plus whitespace and
            # the quotes. Before anything else it is an ordinary backslash.
            #
            # A shell escapes whatever follows, and that rule is actively
            # wrong here. TMT runs on Windows, where `src\main.py` is what a
            # model writes for a path -- and under the shell rule that reads
            # as `srcmain.py`, silently, producing a file-not-found about a
            # file the model named correctly. Weighed against it: `\q` reading
            # as `q` is a convenience nobody asked for. The escape that has to
            # keep working is `\$`, because refusing `$` is what makes the
            # substitution rule enforceable, and it does.
            if i + 1 >= n:
                raise ShellError(
                    "The command ends with a single backslash, which escapes "
                    "the character after it -- and there is no character "
                    "after it. Remove it, or write \\\\ for a literal "
                    "backslash."
                )
            if text[i + 1] == "\n":     # a continued line, spliced as a shell does
                i += 2
                continue
            if text[i + 1] in _ESCAPABLE:
                word.append(text[i + 1])
                started = True
                i += 2
                continue
            word.append(ch)             # a literal backslash: a Windows path
            started = True
            i += 1
            continue

        if ch == "'":
            chunk, i = _scan_single(text, i)
            word.append(chunk)
            started = True
            continue

        if ch == '"':
            chunk, i = _scan_double(text, i)
            word.append(chunk)
            started = True
            continue

        if ch == "\n":
            started = flush()
            if separator_wanted():
                tokens.append(("nl", ";"))
            i += 1
            continue

        if ch in " \t":
            started = flush()
            i += 1
            continue

        if ch == "#" and not started:
            # Only at the start of a word, which is where a shell starts a
            # comment too: `git log --oneline` must not lose everything after
            # a `#` in a commit message argument.
            while i < n and text[i] != "\n":
                i += 1
            continue

        if ch == "$":
            message = _dollar_refusal(text, i)
            if message:
                raise ShellError(message)
            word.append(ch)
            started = True
            i += 1
            continue

        if ch == "`":
            raise ShellError(_COMMAND_SUBSTITUTION.format(form="`...`"))

        if ch in "()":
            raise ShellError(_GROUPING)

        # Everything below ends the word it is standing after, which is what
        # makes `echo a>b` mean `echo a > b` as it does in a shell.
        if text.startswith("<(", i):
            raise ShellError(_PROCESS_SUBSTITUTION.format(form="<(...)"))
        if text.startswith(">(", i):
            raise ShellError(_PROCESS_SUBSTITUTION.format(form=">(...)"))
        if text.startswith("<<", i):
            raise ShellError(_HEREDOC)
        if text.startswith("&&", i):
            started = flush()
            emit("&&")
            i += 2
            continue
        if text.startswith("&>", i):
            raise ShellError(_DUPLICATION)
        if ch == "&":
            raise ShellError(_BACKGROUND)
        if text.startswith("||", i):
            started = flush()
            emit("||")
            i += 2
            continue
        if ch == "|":
            started = flush()
            emit("|")
            i += 1
            continue
        if ch == ";":
            started = flush()
            emit(";")
            i += 1
            continue
        # `2>&1` and `2>` are only operators at the start of a word. In a
        # shell a leading digit is a descriptor only when the whole token is
        # digits, and honouring that keeps `report2>out` reading the way it
        # does everywhere else: the word is `report2` and the redirect is `>`.
        if not started and text.startswith("2>&1", i):
            emit("2>&1")
            i += 4
            continue
        if text.startswith(">&", i):
            raise ShellError(_DUPLICATION)
        if not started and text.startswith("2>", i):
            emit("2>")
            i += 2
            continue
        if text.startswith(">>", i):
            started = flush()
            emit(">>")
            i += 2
            continue
        if ch == ">":
            started = flush()
            emit(">")
            i += 1
            continue
        if ch == "<":
            started = flush()
            emit("<")
            i += 1
            continue

        word.append(ch)
        started = True
        i += 1

    flush()
    return tokens


# --- assembling the stages -------------------------------------------------


def _no_command(before, after):
    """The sentence for a command that is not there, naming both neighbours."""
    if before and after:
        return (
            "There is no command between '%s' and '%s'. Remove the extra "
            "operator, or write the command that belongs there." % (before, after)
        )
    if after:
        return (
            "The command begins with the operator '%s', so there is nothing "
            "for it to act on. Remove it, or write the command that should "
            "come first." % after
        )
    return (
        "The command ends with the operator '%s', so there is nothing for it "
        "to run. Remove it, or write the command that should follow it." % before
    )


def parse(text):
    """The command line as a list of Stage. Raises ShellError.

    A trailing `&&`, `||` or `|` is refused: each of those PROMISES a command
    and the promise is unkept, which is a line the model did not finish
    writing. A trailing `;` is accepted, because it terminates rather than
    promises -- every shell takes `ls;`, and refusing it would cost a round to
    teach a rule with no reason behind it. A trailing NEWLINE is not an
    operator either, because "python x.py\\n" is the commonest thing anyone
    writes.
    """
    tokens = _tokenize(text)
    # A newline at the end is whitespace, not punctuation; only a written
    # separator is held to the rule above.
    while tokens and tokens[-1][0] == "nl":
        tokens.pop()
    if not tokens:
        raise ShellError(
            "There is no command to run. Write the command line to execute, "
            "for example: python -m pytest -q"
        )

    stages = []
    commands = []           # the pipeline being built
    argv = []
    redirects = []
    pending = None          # a redirect operator waiting for its target
    operator = ""           # the operator preceding the stage being built
    previous = ""           # the operator immediately before this command

    def close_command(after):
        nonlocal argv, redirects, pending
        if pending:
            raise ShellError(
                "The redirect '%s' has no file after it. Write the file to "
                "redirect into, for example: python x.py > out.txt" % pending
            )
        if not argv:
            if redirects:
                raise ShellError(
                    "A redirect needs a command to redirect. Write the "
                    "command in front of it, for example: python x.py > out.txt"
                )
            raise ShellError(_no_command(previous, after))
        commands.append(Command(argv, redirects))
        argv, redirects = [], []

    def close_stage(after):
        nonlocal commands, operator
        close_command(after)
        stages.append(Stage(Pipeline(commands), operator))
        commands = []

    for index, (kind, value) in enumerate(tokens):
        if kind == "word":
            if pending:
                redirects.append(Redirect(pending, value))
                pending = None
            else:
                argv.append(value)
            continue

        operator_text = value if kind == "op" else ";"
        if operator_text in ("<", ">", ">>", "2>"):
            if pending:
                raise ShellError(
                    "The redirect '%s' has no file after it, and another "
                    "redirect follows it. Write the file each redirect points "
                    "at." % pending
                )
            pending = operator_text
            continue
        if operator_text == "2>&1":
            if pending:
                raise ShellError(
                    "The redirect '%s' has no file after it. Write the file "
                    "it points at before 2>&1." % pending
                )
            redirects.append(Redirect("2>&1"))
            continue
        if operator_text == "|":
            close_command("|")
            previous = "|"
            continue
        # &&, ||, ; and a newline separator all end the stage.
        close_stage(operator_text)
        operator = operator_text
        previous = operator_text

    # A written `;` at the end is a terminator, not a dangling operator. Every
    # shell accepts `ls;`, a model that writes one has already said everything
    # it meant, and refusing it costs a round to teach a rule with no reason
    # behind it. `&&` and `||` are the opposite case and still refuse: each of
    # those PROMISES a command, and the promise is unkept.
    if not (previous == ";" and not argv and not redirects
            and not commands and not pending):
        close_stage("")
    return stages


# --- rendering it back -----------------------------------------------------

# What may be written without quotes. Everything the tokenizer treats
# specially is absent, so a bare word here is a word that reads back as
# itself. `*` and `?` are deliberately present: describe is TMT saying what
# it understood, and an unexpanded pattern is what was written.
_NEEDS_QUOTING = set(" \t\n\r'\"\\$`|&;<>#()")


def _quote(argument):
    """One argument, rendered so that re-parsing it gives the same string.

    Double quotes rather than single, because single quotes refuse a dollar
    sign in this parser and an argument that legitimately contains one -- a
    regular expression, a price -- would render into something that could not
    be read back. The render being re-parseable is the property that makes it
    safe to show as "what TMT will run".
    """
    if argument == "":
        return '""'
    if not any(ch in _NEEDS_QUOTING for ch in argument):
        return argument
    out = []
    for ch in argument:
        if ch in "\\\"$`":
            out.append("\\")
        out.append(ch)
    return '"' + "".join(out) + '"'


def describe(stages):
    """The parse rendered back as one line, for logs and refusals.

    This is what the user and the model are shown as TMT's reading of the
    command. It renders the STRUCTURE, not the original text: a line that
    parsed to something other than what was intended shows the difference
    here, which is the only place it can be seen before it runs.
    """
    parts = []
    for stage in stages:
        if stage.operator:
            parts.append("; " if stage.operator == ";" else " %s " % stage.operator)
        elif parts:
            # Defensive: a hand-built list whose later stages carry no
            # operator would otherwise render as one run-on command line.
            parts.append("; ")
        parts.append(_describe_pipeline(stage.pipeline))
    return "".join(parts)


def _describe_pipeline(pipeline):
    return " | ".join(_describe_command(c) for c in pipeline.commands)


def _describe_command(command):
    parts = [_quote(a) for a in command.argv]
    for redirect in command.redirects:
        if redirect.kind == "2>&1":
            parts.append("2>&1")
        else:
            parts.append("%s %s" % (redirect.kind, _quote(redirect.target or "")))
    return " ".join(parts)


# --- globbing, which is the one thing here that reads the disk --------------


def _hidden_allowed(pattern):
    """Whether a pattern asked for dot-names.

    A shell's `*` does not match a leading dot, and neither does this. The
    rule is approximated at the whole-pattern level rather than per segment:
    a pattern with a dot-name anywhere in it may match dot-names anywhere.
    The approximation is loose only when the model has already written a
    literal dot, and it is tight in the direction that matters -- `cat *`
    does not quietly include a `.env`.
    """
    return any(segment.startswith(".") for segment in pattern.split("/"))


def _matches(pattern, cwd):
    """Sorted workspace-safe matches for one pattern, or None for no match.

    None rather than an empty list, so the caller can tell "this expanded to
    nothing" from "this was not a pattern" -- the shell convention is that an
    unmatched pattern is passed through as written, and both cases take that
    path for different reasons.
    """
    if not any(ch in pattern for ch in GLOB_CHARS):
        return None
    normalised = pattern.replace("\\", "/")
    # An absolute pattern, or one climbing out with `..`, simply matches
    # nothing: the walk yields paths relative to cwd, so there is nothing for
    # it to match against. It is left as written and refused later by the
    # policy layer, which is the module that owns the sentence about paths.
    keep = agent_file_ops.glob_filter(normalised)
    deep = "/" in normalised
    allow_hidden = _hidden_allowed(normalised)

    found = []
    for relative, absolute in agent_file_ops.iter_workspace_entries(
        root=cwd, include_dirs=True
    ):
        text = agent_file_ops.posix(relative)
        segments = text.split("/")
        if not deep and len(segments) != 1:
            # A pattern with no separator means "here", as it does in a
            # shell. `glob_filter` falls back to matching the basename at any
            # depth, which is right for the `glob` action and wrong for this:
            # `ls *.py` must not list a file three directories down.
            continue
        if not allow_hidden and any(s.startswith(".") for s in segments):
            continue
        if not keep(relative):
            continue
        # The walk does not descend a directory symlink, but it does yield a
        # file one, and a match that resolved outside the workspace would be
        # an expansion handing out a path no other action would.
        if not agent_file_ops.within_workspace(absolute):
            continue
        found.append(text)
    return sorted(found) or None


def expand(argv, cwd):
    """argv with `*` and `?` expanded against cwd. The only disk read here.

    The cwd is a parameter rather than something read from a config, so this
    can be driven over a temporary directory without a session -- and so that
    a pipeline whose stages were given different working directories cannot
    accidentally expand one against another's.

    Three properties hold, and each is a decision:

    - **An unmatched pattern is left exactly as written.** That is the shell
      convention, and it matters because it is what makes `grep x *.py` in a
      directory with no Python files fail with a message about `*.py` rather
      than succeeding against nothing.
    - **Nothing outside the workspace is ever produced**, whatever the
      pattern says and whatever the walk finds, because every match is put
      through `within_workspace`.
    - **Machinery is not matched.** The walk prunes `.git`, `node_modules`,
      `__pycache__` and the rest, so `*` here lists less than a shell's `*`
      would. That is `agent_file_ops`' single answer to what counts as
      machinery, and a second answer here would be one more thing to keep
      in step.

    Quoting is not carried this far: `Command.argv` holds the finished
    strings, so `echo "*"` is expanded where a shell would leave it alone.
    The consequence is bounded -- an expansion cannot leave the workspace and
    cannot invent a file -- and the alternative is a parallel structure
    tracking how each argument was written, which every caller downstream
    would then have to keep aligned with argv.

    Redirect targets are deliberately not expanded: a redirect names one
    file, and a pattern that matched two would have no meaning to apply.
    """
    if not argv:
        return []
    cwd = Path(cwd)
    out = []
    for argument in argv:
        matches = _matches(argument, cwd) if isinstance(argument, str) else None
        if matches:
            out.extend(matches)
        else:
            out.append(argument)
    return out
