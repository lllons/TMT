"""Tests for `agent_shell`, the parser that keeps a shell out of the loop.

The module exists so that no `/bin/sh` and no `cmd.exe` is ever handed a
model-authored string. Everything downstream -- the policy layer, the
sandbox -- decides what may run by READING ARGUMENTS, so anything that hides
an argument's meaning has to die here or the whole command policy stops
meaning anything. That is what most of this file is about: the refusals are
not edge cases being tidied up, they are the boundary, and a later edit that
quietly starts accepting `$(...)` would leave every other guard reading text
that is not what runs.

Three groups are worth naming before they are read.

**The backslash rule is deliberately not a shell's**, and it is the one thing
here most likely to be "fixed" back by a future reader who knows bash. A
backslash escapes only the characters this parser gives a meaning to; before
anything else it is a literal backslash, so `python src\\main.py` survives as
itself instead of becoming `srcmain.py` on the platform TMT actually runs on.
It is pinned in both directions -- what stays literal AND what still escapes,
because `\\$` is what makes refusing `$` liveable.

**A Stage carries the operator that PRECEDES it.** A test that got that
backwards would pass while `a && b || c` short-circuited on the wrong
operator, so it is asserted positionally and against the wrong answer as
well as the right one.

**`describe()` round-trips.** `parse(describe(parse(t))) == parse(t)` is what
makes it safe to show a user "this is what TMT will run": if the render and
the execution could disagree, the render would be a lie. It is checked as a
property over a table rather than one string at a time.

The expansion tests each build their own throwaway workspace and point
`agent_config.ROOT_DIR` at it, for the reason `test_agent_glob` gives: a test
that read the real TMT repository would pass or fail on whatever happened to
be checked out that day.
"""

import os
import shutil
import stat
import tempfile
from pathlib import Path

import agent_config
import agent_shell


# --- helpers ----------------------------------------------------------------

def argv_of(text):
    """The argv of a line that parses to exactly one command.

    The single-command assertion is part of the helper on purpose: a test
    about quoting that silently received two commands would still be reading
    argv[0] and would still pass.
    """
    stages = agent_shell.parse(text)
    assert len(stages) == 1, stages
    assert len(stages[0].commands) == 1, stages
    return stages[0].commands[0].argv


def refusal(text):
    """The refusal sentence for a line that must not be accepted.

    Raises rather than returning None when the line is accepted, so a test
    whose premise has quietly stopped holding fails loudly instead of
    asserting against nothing.
    """
    try:
        stages = agent_shell.parse(text)
    except agent_shell.ShellError as exc:
        return str(exc)
    raise AssertionError("parse(%r) was accepted as %r" % (text, stages))


def says(message, *fragments):
    for fragment in fragments:
        assert fragment in message, (fragment, message)


def remove_tree(path):
    """Delete a temp tree, including a directory symlink on Windows.

    Windows will not unlink a directory symlink; rmdir removes the link
    without touching what it points at, which matters here because what it
    points at is the "outside the workspace" directory a test is about.
    """
    def on_error(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
        except OSError:
            pass
        try:
            func(target)
        except OSError:
            if os.path.isdir(target):
                os.rmdir(target)
            else:
                raise
    shutil.rmtree(path, onerror=on_error)


class Workspace:
    """A throwaway directory as the workspace root.

    close() restores agent_config.ROOT_DIR and must run in a finally block: a
    leaked root points every later test at a directory that has been deleted.
    """

    def __init__(self, files=None, dirs=None):
        self.previous_root = agent_config.ROOT_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_shell_")).resolve()
        for name, body in (files or {}).items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body.encode("utf-8"))
        for name in (dirs or ()):
            (self.path / name).mkdir(parents=True, exist_ok=True)
        agent_config.ROOT_DIR = self.path

    def close(self):
        agent_config.ROOT_DIR = self.previous_root
        remove_tree(self.path)


# --- words, whitespace and quoting ------------------------------------------

def test_a_plain_line_is_words_and_the_first_one_is_the_program():
    """The base case, and `program` being argv[0] is what the policy layer
    asks of every command it classifies."""
    stages = agent_shell.parse("python -m pytest -q")
    assert len(stages) == 1, stages
    command = stages[0].commands[0]
    assert command.argv == ["python", "-m", "pytest", "-q"], command
    assert command.program == "python", command
    assert command.redirects == [], command


def test_runs_of_spaces_and_tabs_separate_words_and_produce_no_empty_ones():
    """A model writing a line by concatenation produces double spaces; an
    empty argv entry would reach the program as an empty argument."""
    assert argv_of("ls    -la") == ["ls", "-la"]
    assert argv_of("ls\t-la") == ["ls", "-la"]
    assert argv_of("   ls -la   ") == ["ls", "-la"]


def test_single_quotes_group_and_are_taken_literally():
    """`'a b'` is one argument and the quotes are not part of it. Nothing
    inside is interpreted, which is why the backslash stays."""
    assert argv_of("echo 'a b'") == ["echo", "a b"]
    assert argv_of("git commit -m 'two words'") == ["git", "commit", "-m", "two words"]
    assert argv_of("echo 'a\\b'") == ["echo", "a\\b"]


def test_double_quotes_group_and_expand_nothing():
    """They group, and that is all they do -- there is nothing left to expand
    because substitution is refused everywhere. So a double-quoted argument
    and a single-quoted one differ only in what escapes are honoured."""
    assert argv_of('echo "a b"') == ["echo", "a b"]
    assert argv_of('echo "a b" c') == ["echo", "a b", "c"]


def test_quotes_join_to_what_touches_them_rather_than_starting_a_new_word():
    """`--message='a b'` is one argument, as it is in a shell. A parser that
    started a new word at the quote would hand the program a stray flag."""
    assert argv_of("git commit --message='a b'") == ["git", "commit", "--message=a b"]
    assert argv_of('a"b"c') == ["abc"]


def test_an_empty_quoted_string_is_still_an_argument():
    """`grep "" f` passes an empty argument, and dropping it would change the
    command's meaning rather than tidy it."""
    assert argv_of('grep "" file') == ["grep", "", "file"]
    assert argv_of("grep '' file") == ["grep", "", "file"]


def test_an_unterminated_single_quote_is_refused_and_says_how_to_close_it():
    message = refusal("echo 'unterminated")
    says(message, "Unterminated single quote", "Close the quote", "backslash")


def test_an_unterminated_double_quote_is_refused_and_says_how_to_close_it():
    message = refusal('echo "unterminated')
    says(message, "Unterminated double quote", "Close the quote", "backslash")


def test_a_hash_at_a_word_boundary_starts_a_comment():
    """Outside quotes and at the start of a word, exactly where a shell puts
    it. Everything to the end of the line goes."""
    assert argv_of("ls -la # list everything") == ["ls", "-la"]
    assert argv_of("ls\t# tab then a comment") == ["ls"]


def test_a_hash_inside_a_word_is_an_ordinary_character():
    """`git log --oneline#x` must not lose everything after the hash, and a
    quoted `#` is text: a commit message mentioning issue #3 is the case."""
    assert argv_of("git log --oneline#x") == ["git", "log", "--oneline#x"]
    assert argv_of("git commit -m 'fix#3'") == ["git", "commit", "-m", "fix#3"]
    assert argv_of('git commit -m "fix #3"') == ["git", "commit", "-m", "fix #3"]


def test_a_comment_spanning_the_whole_line_leaves_no_command():
    """Refused as an empty line rather than accepted as a command with no
    program -- an empty argv must never reach a caller."""
    says(refusal("# just a note"), "There is no command to run")


def test_a_comment_ends_at_the_newline_and_the_next_line_still_runs():
    stages = agent_shell.parse("ls # a note\nwc -l")
    assert [s.commands[0].argv for s in stages] == [["ls"], ["wc", "-l"]], stages


# --- the backslash rule, which is deliberately not a shell's ----------------

def test_a_windows_path_written_unquoted_survives_as_itself():
    """THE decision in this module a future reader is most likely to reverse.
    A shell escapes whatever follows a backslash, which here would read
    `src\\main.py` as `srcmain.py` -- silently, producing a file-not-found
    about a file the model named correctly. Before anything else a backslash
    is a literal backslash."""
    assert argv_of("python src\\main.py") == ["python", "src\\main.py"]
    assert argv_of("type C:\\temp\\notes.txt") == ["type", "C:\\temp\\notes.txt"]


def test_a_backslash_before_an_ordinary_letter_keeps_both_characters():
    """The direction the shell rule loses: `\\n`, `\\t` and `\\q` are a
    backslash and a letter, not the letter alone. `C:\\temp\\new` is the
    path that made this rule, and it must not lose its n."""
    assert argv_of("echo C:\\temp\\new") == ["echo", "C:\\temp\\new"]
    assert argv_of("echo a\\qb") == ["echo", "a\\qb"]
    assert argv_of("echo \\name") == ["echo", "\\name"]


def test_a_backslash_still_escapes_the_characters_the_parser_gives_meaning_to():
    """The other direction, and it is what makes the rule liveable rather
    than merely convenient: `\\$` is the documented way to write a literal
    dollar, and refusing `$` is only enforceable because it exists."""
    assert argv_of("echo \\$HOME") == ["echo", "$HOME"]
    assert argv_of("echo a\\ b") == ["echo", "a b"]
    assert argv_of("echo a\\;b") == ["echo", "a;b"]
    assert argv_of("echo a\\|b") == ["echo", "a|b"]
    assert argv_of("echo \\#notacomment") == ["echo", "#notacomment"]
    assert argv_of("echo \\(") == ["echo", "("]


def test_a_doubled_backslash_is_one_literal_backslash():
    """`\\\\` is in the escapable set, so it collapses -- which is what makes
    the quoting in `describe` re-parseable."""
    assert argv_of("echo a\\\\b") == ["echo", "a\\b"]


def test_a_backslash_at_the_very_end_is_refused_rather_than_dropped():
    """It promises a character that is not there. Dropping it silently would
    run a command the model did not write."""
    message = refusal("ls \\")
    says(message, "ends with a single backslash", "Remove it")


def test_a_backslash_before_a_newline_splices_the_line():
    """The one place the shell rule is kept, because a continued line is a
    single command written over two rows and nothing else could mean that."""
    assert argv_of("python -m pytest \\\n  -q") == ["python", "-m", "pytest", "-q"]


def test_inside_double_quotes_a_backslash_escapes_only_the_four_shell_characters():
    """Which is what bash does inside double quotes, and it matters here for
    the same Windows reason: `"C:\\temp\\new"` must not lose its n."""
    assert argv_of('echo "C:\\temp\\new"') == ["echo", "C:\\temp\\new"]
    assert argv_of('echo "a\\"b"') == ["echo", 'a"b']
    assert argv_of('echo "a\\\\b"') == ["echo", "a\\b"]
    assert argv_of('echo "\\$5"') == ["echo", "$5"]


# --- operators and structure ------------------------------------------------

def test_a_pipe_joins_commands_inside_one_stage():
    """The two sides of a pipe are one job. A pipe that made two stages would
    let a caller run the right-hand side after the left had finished, which
    is not what a pipe means."""
    stages = agent_shell.parse("cat notes.txt | wc -l")
    assert len(stages) == 1, stages
    assert [c.argv for c in stages[0].commands] == [["cat", "notes.txt"], ["wc", "-l"]]
    assert stages[0].operator == "", stages


def test_each_stage_carries_the_operator_that_precedes_it():
    """Load-bearing, and the assertion most worth getting the right way
    round: a caller walks left to right and asks each stage, from the stage
    itself, whether it should run given how the last one ended. Written
    forwards instead, `a && b || c` short-circuits on the wrong operator and
    nothing looks broken."""
    stages = agent_shell.parse("a && b || c")
    assert [s.operator for s in stages] == ["", "&&", "||"], stages
    assert [s.commands[0].argv for s in stages] == [["a"], ["b"], ["c"]], stages
    # The wrong answer, named: if the operator FOLLOWED its stage, the first
    # would carry "&&" and the last would carry "".
    assert stages[0].operator != "&&", stages
    assert stages[-1].operator != "", stages


def test_the_three_stage_operators_each_reach_the_stage_they_precede():
    for text, operator in (("a && b", "&&"), ("a || b", "||"), ("a ; b", ";")):
        stages = agent_shell.parse(text)
        assert len(stages) == 2, (text, stages)
        assert stages[0].operator == "", (text, stages)
        assert stages[1].operator == operator, (text, stages)


def test_a_newline_separates_stages_exactly_as_a_semicolon_does():
    """Two lines are two commands, and the operator recorded is `;` -- a
    newline is a separator, not a conjunction, so nothing downstream has to
    learn a fourth operator."""
    assert agent_shell.parse("ls\nwc -l") == agent_shell.parse("ls ; wc -l")
    assert [s.operator for s in agent_shell.parse("ls\nwc -l")] == ["", ";"]


def test_blank_lines_do_not_become_empty_commands():
    """A model writing a block of commands leaves blank rows in it. Each one
    is whitespace, not a command that is missing."""
    stages = agent_shell.parse("\n\nls\n\n\nwc -l\n\n")
    assert [s.commands[0].argv for s in stages] == [["ls"], ["wc", "-l"]], stages


def test_a_newline_after_an_operator_continues_the_line():
    """`a &&\\nb` is one line wrapped, exactly as it is in a shell. Treating
    the newline as a second separator would produce an empty command between
    the two and refuse a line that is fine."""
    assert agent_shell.parse("a &&\nb") == agent_shell.parse("a && b")


def test_carriage_returns_are_normalised_before_anything_is_read():
    """A command pasted from Windows arrives with CRLF, and a bare CR is what
    an old editor leaves. Both mean the same line break."""
    assert agent_shell.parse("a\r\nb") == agent_shell.parse("a\nb")
    assert agent_shell.parse("a\rb") == agent_shell.parse("a\nb")


def test_a_trailing_newline_is_accepted_because_it_is_not_an_operator():
    """`python x.py\\n` is the commonest thing anyone writes, and refusing it
    for a trailing separator would refuse almost every line."""
    assert agent_shell.parse("python x.py\n") == agent_shell.parse("python x.py")


def test_a_trailing_semicolon_is_accepted_while_a_trailing_conjunction_is_not():
    """The asymmetry is the point, and it is not arbitrary: `&&` PROMISES a
    command and the promise is unkept, where `;` terminates one that has
    already been written. `ls;` is a finished line."""
    assert agent_shell.parse("ls;") == agent_shell.parse("ls")
    assert agent_shell.parse("ls ;") == agent_shell.parse("ls")
    assert agent_shell.parse("a ; b ;") == agent_shell.parse("a ; b")
    for text in ("ls &&", "ls ||", "ls |"):
        says(refusal(text), "ends with the operator", "Remove it")


# --- redirections -----------------------------------------------------------

def test_the_four_file_redirects_are_read_with_their_target_and_descriptor():
    """`fd` is a table rather than something each caller works out, so it
    cannot mean one thing where it is built and another where it is read."""
    for text, kind, target, fd in (("ls > out.txt", ">", "out.txt", 1),
                                   ("ls >> out.txt", ">>", "out.txt", 1),
                                   ("wc < in.txt", "<", "in.txt", 0),
                                   ("ls 2> err.txt", "2>", "err.txt", 2)):
        command = agent_shell.parse(text)[0].commands[0]
        assert len(command.redirects) == 1, (text, command)
        redirect = command.redirects[0]
        assert (redirect.kind, redirect.target, redirect.fd) == (kind, target, fd), \
            (text, redirect)


def test_a_redirect_is_not_an_argument():
    """The file named after `>` belongs to the redirect and must not also
    reach the program: `ls > out.txt` runs `ls`, not `ls out.txt`."""
    command = agent_shell.parse("ls -la > out.txt")[0].commands[0]
    assert command.argv == ["ls", "-la"], command
    assert command.redirects == [agent_shell.Redirect(">", "out.txt")], command


def test_two_to_one_has_no_target_and_speaks_about_the_error_stream():
    """It names a descriptor rather than a file, so `target` is None -- and a
    caller wiring the pipeline has to be able to tell that apart from a
    target it failed to read."""
    command = agent_shell.parse("ls 2>&1")[0].commands[0]
    assert len(command.redirects) == 1, command
    redirect = command.redirects[0]
    assert redirect.kind == "2>&1", redirect
    assert redirect.target is None, redirect
    assert redirect.fd == 2, redirect


def test_redirects_are_kept_in_the_order_they_were_written():
    """`> file 2>&1` and `2>&1 > file` mean different things, so the order is
    the meaning and cannot be normalised away."""
    command = agent_shell.parse("python x.py > out.txt 2>&1")[0].commands[0]
    assert command.argv == ["python", "x.py"], command
    assert [r.kind for r in command.redirects] == [">", "2>&1"], command
    assert command.redirects[0].target == "out.txt", command


def test_a_redirect_ends_the_word_it_is_standing_against():
    """`echo a>b` is `echo a > b`, which is what it means in a shell. A
    parser that kept it as one word would run `echo` with a literal `a>b`."""
    command = agent_shell.parse("echo a>b")[0].commands[0]
    assert command.argv == ["echo", "a"], command
    assert command.redirects == [agent_shell.Redirect(">", "b")], command


def test_a_digit_inside_a_word_is_not_a_descriptor():
    """`report2>out` is the word `report2` redirected with `>`. A leading
    digit is a descriptor only when it starts the word, which is the rule
    everywhere else and is why the word does not lose its 2."""
    command = agent_shell.parse("echo report2>out")[0].commands[0]
    assert command.argv == ["echo", "report2"], command
    assert [r.kind for r in command.redirects] == [">"], command


def test_each_command_in_a_pipeline_keeps_its_own_redirects():
    stages = agent_shell.parse("cat a.txt 2> err.txt | wc -l > count.txt")
    commands = stages[0].commands
    assert [r.kind for r in commands[0].redirects] == ["2>"], commands
    assert [r.kind for r in commands[1].redirects] == [">"], commands
    assert commands[1].redirects[0].target == "count.txt", commands


# --- refusals: substitution, which is the whole boundary --------------------

def test_command_substitution_is_refused_and_says_what_to_do_instead():
    """A substitution is a second command hiding inside an argument, and
    every guard between here and the process reads arguments. The refusal has
    to name the route out or the model retries the same shape."""
    message = refusal("echo $(id)")
    says(message, "$(...)", "Command substitution is not available",
         "own bash call", "literally")


def test_backticks_are_refused_as_command_substitution_too():
    """The older spelling of the same thing, and a model that has read a lot
    of shell scripts writes it."""
    message = refusal("echo `id`")
    says(message, "`...`", "Command substitution is not available")


def test_brace_expansion_of_a_variable_is_refused_and_names_the_escape():
    message = refusal("echo ${HOME}")
    says(message, "${...}", "Variable expansion is not available",
         "Write the value", "\\$")


def test_a_bare_variable_is_refused_and_the_whole_name_is_quoted_back():
    """A refusal that said `$H` when the line said `$HOME` reads as though
    TMT misparsed the line, and the model's next attempt corrects the wrong
    thing."""
    says(refusal("echo $HOME"), "$HOME")
    says(refusal("cat $PATHEXT/x"), "$PATHEXT")
    assert "$H " not in refusal("echo $HOME")


def test_the_positional_and_special_variables_are_refused_as_well():
    """`$1`, `$?`, `$@` and the rest are expansions too, and a model reaching
    for one is thinking in shell script rather than in one command."""
    for text, form in (("echo $1", "$1"), ("echo $?", "$?"), ("echo $@", "$@"),
                       ("echo $#", "$#"), ("echo $$", "$$")):
        says(refusal(text), form, "Variable expansion is not available")


def test_a_variable_inside_single_quotes_is_refused_which_is_stricter_than_a_shell():
    """Deliberately stricter, and the choice is worth pinning because it
    looks like a bug: bash treats `'$HOME'` as inert. "A dollar sign means a
    substitution and TMT refuses substitutions" is a rule a model can hold in
    one piece; "unless it is inside single quotes, unless the program expands
    it itself" is a rule nobody applies correctly under pressure."""
    says(refusal("echo '$HOME'"), "$HOME", "Variable expansion is not available")
    says(refusal("echo '$(id)'"), "$(...)", "Command substitution is not available")
    says(refusal('echo "$HOME"'), "$HOME")
    # And the escape the refusal names really is the way through.
    assert argv_of("echo \\$HOME") == ["echo", "$HOME"]


def test_a_dollar_that_is_not_a_substitution_is_an_ordinary_character():
    """`grep 'total$'` and `echo 5$` mean exactly what they say in any shell,
    and refusing them would make ordinary regular expressions unwritable. The
    check is on what FOLLOWS the dollar, not on the character."""
    assert argv_of("grep 'total$' report.txt") == ["grep", "total$", "report.txt"]
    assert argv_of("echo 5$") == ["echo", "5$"]
    assert argv_of("grep -E 'a$|b$' f") == ["grep", "-E", "a$|b$", "f"]


def test_the_same_mistake_always_reads_the_same_way():
    """The wording lives in one constant rather than at each raise, so a
    model that meets a refusal twice concludes the shape is wrong rather than
    that it was unlucky."""
    assert refusal("echo $(id)") == refusal("cat $(ls)")
    assert refusal("echo `id`") == refusal("cat `ls`")


# --- refusals: everything else that hides a meaning -------------------------

def test_background_execution_is_refused_and_points_at_the_operation_key():
    """`&` is not merely unsupported: background work goes through the bash
    tool's own operation, where it is registered, limited and cleaned up. The
    sentence has to say so, or the model reads it as "no background work"."""
    message = refusal("sleep 60 &")
    says(message, "Background execution", "operation", "start", "logs", "stopped")


def test_process_substitution_is_refused_in_both_directions():
    for text, form in (("diff <(a) b", "<(...)"), ("tee >(cat) < x", ">(...)")):
        says(refusal(text), form, "Process substitution", "redirect")


def test_here_documents_and_here_strings_are_refused_together():
    """Both are a way of writing file content inside a command line, and the
    remedy is the same for both: write the file, redirect it in."""
    for text in ("cat <<EOF", "cat <<<hello"):
        says(refusal(text), "Here-documents", "write_file", "redirect it in with <")


def test_only_two_to_one_may_duplicate_a_stream():
    """A duplication that was parsed and then not applied would be the render
    disagreeing with the execution, so anything but `2>&1` is refused by
    name, with the supported spelling in the sentence."""
    for text in ("ls >&2", "ls &> out.txt", "ls 1>&2"):
        says(refusal(text), "Only 2>&1 is supported", "command > file 2>&1")


def test_subshells_and_grouping_are_refused_and_name_the_alternative():
    for text in ("(ls)", "ls && (wc -l)", "echo )"):
        says(refusal(text), "Subshells and grouping", "&&", "quote the parentheses")


def test_a_line_that_begins_with_an_operator_is_refused_naming_that_operator():
    for text, operator in (("| ls", "|"), ("&& ls", "&&"), ("|| ls", "||")):
        message = refusal(text)
        says(message, "begins with the operator", "'%s'" % operator)


def test_a_doubled_operator_is_refused_naming_both_neighbours():
    """Naming both is what tells the model WHERE the gap is: "there is no
    command between && and ||" is actionable, and "syntax error" is not."""
    message = refusal("ls && || wc")
    says(message, "no command between", "'&&'", "'||'", "Remove the extra operator")
    says(refusal("a | | b"), "no command between", "'|'")


def test_a_redirect_with_no_file_after_it_is_refused_with_an_example():
    says(refusal("ls >"), "has no file after it", "python x.py > out.txt")
    says(refusal("ls > > out.txt"), "has no file after it", "another redirect follows")
    says(refusal("ls > 2>&1"), "has no file after it", "before 2>&1")


def test_a_redirect_with_no_command_is_refused_separately():
    """A different mistake from a missing target and it gets its own
    sentence: there is a file, and nothing to point at it."""
    says(refusal("> out.txt"), "A redirect needs a command", "Write the command")


def test_an_empty_line_is_refused_with_an_example_of_a_real_one():
    for text in ("", "   ", "\n\n", "\t"):
        says(refusal(text), "There is no command to run", "python -m pytest -q")


def test_every_refusal_says_what_to_do_instead_rather_than_that_it_failed():
    """The property the whole set is written for. A bare "syntax error" costs
    a round and teaches nothing, so each sentence has to carry a remedy verb
    and enough words to be read as an instruction."""
    lines = [
        "echo $(id)", "echo `id`", "echo ${HOME}", "echo $HOME", "echo '$HOME'",
        "sleep 60 &", "diff <(a) b", "cat <<EOF", "cat <<<hi", "ls >&2",
        "ls &> x", "(ls)", "echo 'open", 'echo "open', "ls \\", "| ls",
        "ls &&", "ls && || wc", "ls >", "> out.txt", "",
    ]
    verbs = ("Write", "write", "Use", "Run", "Remove", "Close", "Sequence",
             "Escape", "escape", "pass it")
    for text in lines:
        message = refusal(text)
        assert len(message) >= 60, (text, message)
        assert any(verb in message for verb in verbs), (text, message)
        assert "syntax error" not in message.lower(), (text, message)


def test_a_refusal_is_a_shell_error_and_nothing_else_escapes():
    """Every mistake in this module leaves as a ShellError, so a caller can
    catch one class and turn it into a result the model can act on."""
    for text in ("echo $(id)", "ls &&", "(ls)", "echo 'open", "ls \\"):
        try:
            agent_shell.parse(text)
        except agent_shell.ShellError:
            continue
        except Exception as exc:                       # noqa: BLE001 - the point
            raise AssertionError("%r raised %r" % (text, exc))
        raise AssertionError("%r was accepted" % (text,))


# --- describe: the render that must not be a lie ----------------------------

ROUND_TRIP = [
    "ls",
    "python -m pytest -q",
    "echo 'a b'",
    'echo "a b" c',
    "grep 'total$' report.txt",
    "python src\\main.py",
    "echo C:\\temp\\new",
    "git commit -m 'fix#3'",
    "cat notes.txt | wc -l",
    "a && b || c",
    "a ; b",
    "a\nb",
    "ls > out.txt",
    "ls >> out.txt 2>&1",
    "wc -l < in.txt",
    "ls 2> err.txt",
    "cat a 2> e | wc -l > c",
    "grep '' file",
    "echo *.py",
    'echo "2>&1"',
    'echo "a|b"',
    "ls;",
    "python x.py\n",
    # The four the render has to ESCAPE rather than merely wrap in quotes.
    # Without them the table round-trips even with the escaping removed --
    # a backslash before an ordinary letter survives either way -- so these
    # are the ones that actually hold `_quote` to its promise.
    "echo \\$HOME",
    "echo \\`",
    "echo a\\\\",
    'echo "a\\"b"',
]


def test_describe_round_trips_for_every_shape_the_parser_accepts():
    """THE property that makes it safe to show a user "this is what TMT will
    run". If the render and the execution could disagree, the render would be
    a lie about what is running -- and it is what a refusal quotes back, so
    the disagreement would be invisible at exactly the moment it mattered."""
    for text in ROUND_TRIP:
        first = agent_shell.parse(text)
        rendered = agent_shell.describe(first)
        again = agent_shell.parse(rendered)
        assert again == first, (text, rendered, first, again)


def test_describe_is_one_line_whatever_it_was_given():
    """It goes into a log row and into a refusal sentence; a render that
    carried the original line breaks would break both."""
    for text in ("a\nb\nc", "a &&\nb", "ls\n\nwc -l"):
        assert "\n" not in agent_shell.describe(agent_shell.parse(text)), text


def test_describe_leaves_a_plain_word_plain_and_quotes_only_what_needs_it():
    """Quoting everything would be safe and unreadable, and the render is
    read by a person deciding whether to approve the command."""
    assert agent_shell.describe(agent_shell.parse("ls -la src")) == "ls -la src"
    assert agent_shell.describe(agent_shell.parse("echo 'a b'")) == 'echo "a b"'
    assert agent_shell.describe(agent_shell.parse("echo ''")) == 'echo ""'


def test_describe_uses_double_quotes_so_a_dollar_can_be_rendered_at_all():
    """Single quotes refuse a dollar in this parser, so an argument that
    legitimately contains one -- a regular expression, a price -- would
    render into something that could not be read back."""
    rendered = agent_shell.describe(agent_shell.parse("grep 'total$' f"))
    assert rendered == 'grep "total\\$" f', rendered
    assert agent_shell.parse(rendered)[0].commands[0].argv[1] == "total$"


def test_describe_shows_a_glob_pattern_as_written_rather_than_expanded():
    """describe is TMT saying what it UNDERSTOOD, and expansion happens
    later against a working directory it is not given. A render that showed
    matches would be describing a different command."""
    assert agent_shell.describe(agent_shell.parse("ls *.py")) == "ls *.py"
    assert agent_shell.describe(agent_shell.parse("ls src/**/*.py")) == "ls src/**/*.py"


def test_describe_renders_the_structure_including_the_operators():
    assert agent_shell.describe(agent_shell.parse("a|b")) == "a | b"
    assert agent_shell.describe(agent_shell.parse("a&&b")) == "a && b"
    assert agent_shell.describe(agent_shell.parse("a\nb")) == "a; b"
    assert agent_shell.describe(agent_shell.parse("ls>out")) == "ls > out"


def test_describe_separates_hand_built_stages_that_carry_no_operator():
    """Defensive, and worth pinning: a list assembled in code rather than by
    `parse` would otherwise render as one run-on command line, which is the
    render claiming something that is not going to happen."""
    hand = [agent_shell.Stage(agent_shell.Pipeline([agent_shell.Command(["a"])]), ""),
            agent_shell.Stage(agent_shell.Pipeline([agent_shell.Command(["b"])]), "")]
    assert agent_shell.describe(hand) == "a; b", agent_shell.describe(hand)


# --- the small structural promises ------------------------------------------

def test_equality_compares_the_structure_and_refuses_a_foreign_type():
    """The round-trip property is asserted with `==`, so these have to mean
    what they look like -- and comparing against something else must be
    False rather than an exception."""
    assert agent_shell.parse("ls -la") == agent_shell.parse("ls   -la")
    assert agent_shell.parse("ls -la") != agent_shell.parse("ls -l")
    assert not (agent_shell.Redirect(">", "a") == "a")
    assert not (agent_shell.Command(["a"]) == ["a"])
    assert not (agent_shell.Pipeline([]) == [])
    assert not (agent_shell.Stage(agent_shell.Pipeline([]), "") == "")


def test_a_stage_reads_its_pipeline_commands_without_reaching_through_it():
    stages = agent_shell.parse("a | b")
    assert stages[0].commands == stages[0].pipeline.commands, stages


def test_a_command_with_no_argv_answers_with_an_empty_program():
    """`parse` never produces one, but a caller working on a hand-built
    Command should get an answer rather than an IndexError."""
    assert agent_shell.Command([]).program == ""


def test_the_redirect_table_and_the_operator_lists_stay_in_step():
    """The constants are what callers switch on, so a kind that is parsed and
    is not in the table would carry a silently wrong descriptor."""
    assert set(agent_shell.REDIRECT_KINDS) == {">", ">>", "<", "2>", "2>&1"}
    assert agent_shell.STAGE_OPERATORS == ("&&", "||", ";")
    for kind in agent_shell.REDIRECT_KINDS:
        assert agent_shell.Redirect(kind, "f").fd in (0, 1, 2), kind


# --- expansion, the one thing here that reads the disk ----------------------

def test_a_star_expands_against_the_working_directory_and_comes_back_sorted():
    """Sorted because a caller shows the argv to a user and an order that
    changed with the filesystem's would read as a different command."""
    box = Workspace(files={"b.py": "x", "a.py": "x", "c.txt": "x"})
    try:
        assert agent_shell.expand(["ls", "*.py"], box.path) == ["ls", "a.py", "b.py"]
        assert agent_shell.expand(["ls", "*"], box.path) == [
            "ls", "a.py", "b.py", "c.txt"]
    finally:
        box.close()


def test_a_question_mark_matches_exactly_one_character():
    box = Workspace(files={"a1.txt": "x", "a2.txt": "x", "a12.txt": "x"})
    try:
        assert agent_shell.expand(["cat", "a?.txt"], box.path) == [
            "cat", "a1.txt", "a2.txt"]
    finally:
        box.close()


def test_a_star_does_not_cross_a_directory_separator():
    """`src/*.py` is the one file in src, not everything beneath it -- the
    same rule `glob` keeps, because it is the same compiler."""
    box = Workspace(files={"src/shallow.py": "x", "src/deep/buried.py": "x"})
    try:
        assert agent_shell.expand(["ls", "src/*.py"], box.path) == [
            "ls", "src/shallow.py"]
    finally:
        box.close()


def test_a_double_star_segment_reaches_any_depth_including_none():
    box = Workspace(files={"src/a.py": "x", "src/deep/b.py": "x", "top.py": "x"})
    try:
        assert agent_shell.expand(["ls", "src/**/*.py"], box.path) == [
            "ls", "src/a.py", "src/deep/b.py"]
        assert agent_shell.expand(["ls", "**/*.py"], box.path) == [
            "ls", "src/a.py", "src/deep/b.py", "top.py"]
    finally:
        box.close()


def test_a_pattern_with_no_separator_means_here_rather_than_anywhere():
    """`glob_filter` falls back to matching a basename at any depth, which is
    right for the `glob` action and wrong for a command line: `ls *.py` must
    not list a file three directories down, because the program would then be
    handed paths it was never asked about."""
    box = Workspace(files={"top.py": "x", "src/buried.py": "x"})
    try:
        assert agent_shell.expand(["ls", "*.py"], box.path) == ["ls", "top.py"]
    finally:
        box.close()


def test_a_pattern_that_matches_nothing_is_left_exactly_as_written():
    """The shell convention, and it is what makes `grep x *.py` in a
    directory with no Python files fail with a message about `*.py` rather
    than succeed against nothing."""
    box = Workspace(files={"a.txt": "x"})
    try:
        assert agent_shell.expand(["grep", "x", "*.py"], box.path) == [
            "grep", "x", "*.py"]
    finally:
        box.close()


def test_an_argument_with_no_glob_character_is_passed_straight_through():
    """Untouched means untouched: the same string object's value, not a
    normalised version of it. A path the model wrote is what the program
    should receive."""
    box = Workspace(files={"a.py": "x"})
    try:
        assert agent_shell.expand(["python", "src\\main.py", "-q"], box.path) == [
            "python", "src\\main.py", "-q"]
        assert agent_shell.expand([], box.path) == []
    finally:
        box.close()


def test_a_pattern_cannot_climb_out_of_the_workspace():
    """`../*` and an absolute pattern match nothing and are left as written,
    to be refused by the policy layer, which owns the sentence about paths.
    What must never happen is expansion HANDING BACK a path outside."""
    box = Workspace(files={"inside.py": "x"})
    outside = Path(tempfile.mkdtemp(prefix="tmt_shell_out_")).resolve()
    (outside / "secret.py").write_bytes(b"classified\n")
    try:
        escape = str(outside).replace("\\", "/") + "/*.py"
        for pattern in ("../*", "../*.py", "../../*", "/etc/*", escape):
            result = agent_shell.expand(["cat", pattern], box.path)
            assert result == ["cat", pattern], (pattern, result)
            assert not any("secret" in entry for entry in result), (pattern, result)
        # Not vacuous: the walk really did run and really did find something.
        assert agent_shell.expand(["cat", "*.py"], box.path) == ["cat", "inside.py"]
    finally:
        box.close()
        remove_tree(outside)


def test_a_star_does_not_sweep_up_dot_files():
    """`cat *` must not quietly include a `.env`. A shell's `*` does not
    match a leading dot and neither does this, so a secret is not handed to a
    program by an argument nobody wrote."""
    box = Workspace(files={".env": "SECRET=1", "a.py": "x", ".config/x.ini": "y"})
    try:
        assert agent_shell.expand(["cat", "*"], box.path) == ["cat", "a.py"]
        assert agent_shell.expand(["cat", "**/*"], box.path) == ["cat", "a.py"]
    finally:
        box.close()


def test_a_pattern_that_asks_for_dot_names_gets_them():
    """The other half of the rule: the exclusion is a default, not a ban, so
    `.*` and `.env*` still work when the model wrote the dot itself."""
    box = Workspace(files={".env": "x", ".envrc": "x", "a.py": "x"})
    try:
        assert agent_shell.expand(["cat", ".env*"], box.path) == [
            "cat", ".env", ".envrc"]
    finally:
        box.close()


def test_machinery_is_never_matched_because_the_walk_prunes_it():
    """`agent_file_ops` owns the single answer to what counts as machinery.
    A second answer here would be one more thing to keep in step, and a `*`
    that dragged in node_modules would be unusable anyway."""
    box = Workspace(files={"a.py": "x",
                           "__pycache__/junk.py": "x",
                           ".git/config": "x",
                           "node_modules/pkg/index.js": "x"})
    try:
        result = agent_shell.expand(["ls", "**/*"], box.path)
        assert result == ["ls", "a.py"], result
        for machinery in ("__pycache__", ".git", "node_modules"):
            assert not any(machinery in entry for entry in result), (machinery, result)
    finally:
        box.close()


def test_expansion_is_relative_to_the_cwd_it_was_given():
    """The cwd is a parameter rather than something read from a config, so a
    pipeline whose stages were given different working directories cannot
    expand one against another's -- and so this is testable without a
    session."""
    box = Workspace(files={"top.py": "x", "src/deep.py": "x", "src/other.py": "x"})
    try:
        assert agent_shell.expand(["ls", "*.py"], box.path / "src") == [
            "ls", "deep.py", "other.py"]
        assert agent_shell.expand(["ls", "*.py"], box.path) == ["ls", "top.py"]
    finally:
        box.close()


def test_matches_come_back_with_one_separator_whatever_the_platform_uses():
    """A backslash in an expanded path would be re-read as an escape by
    anything that parsed the argv again, and it would not match what the
    describe of the same line shows."""
    box = Workspace(files={"src/deep/a.py": "x"})
    try:
        result = agent_shell.expand(["ls", "src/**/*.py"], box.path)
        assert result == ["ls", "src/deep/a.py"], result
        assert "\\" not in result[1], result
    finally:
        box.close()


def test_a_working_directory_outside_the_workspace_expands_to_nothing():
    """Every match is put through `within_workspace`, which asks about the
    workspace root and not about the cwd it walked -- so a cwd that escaped
    cannot be turned into a list of files by a pattern."""
    box = Workspace(files={"inside.py": "x"})
    outside = Path(tempfile.mkdtemp(prefix="tmt_shell_out_")).resolve()
    (outside / "secret.py").write_bytes(b"classified\n")
    try:
        assert agent_shell.expand(["cat", "*.py"], outside) == ["cat", "*.py"]
    finally:
        box.close()
        remove_tree(outside)


def test_a_symlink_leaving_the_workspace_is_never_expanded_into():
    """The walk does not descend a directory symlink, but it does yield a
    file one -- and an expanded argument is a path handed straight to a
    program, so naming one that resolves outside would be an expansion
    producing the escape every other action refuses."""
    outside = Path(tempfile.mkdtemp(prefix="tmt_shell_out_")).resolve()
    (outside / "secret.py").write_bytes(b"classified\n")
    box = Workspace(files={"inside.py": "x"})
    try:
        try:
            os.symlink(str(outside / "secret.py"), str(box.path / "leak.py"))
            os.symlink(str(outside), str(box.path / "outward"),
                       target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError, TypeError):
            return              # A platform or account that cannot make one.
        for pattern in ("*", "*.py", "**/*"):
            result = agent_shell.expand(["cat", pattern], box.path)
            assert "leak.py" not in result, (pattern, result)
            assert "outward" not in result, (pattern, result)
            assert not any("secret.py" in entry for entry in result), (pattern, result)
        # Not vacuous: the walk really did run and really did find something.
        assert agent_shell.expand(["cat", "*.py"], box.path) == ["cat", "inside.py"]
    finally:
        box.close()
        remove_tree(outside)


def test_a_redirect_target_is_not_reachable_by_expansion():
    """`expand` takes argv and nothing else, so a pattern written as a
    redirect target stays a single name -- a redirect names one file, and a
    pattern that matched two would have no meaning to apply."""
    box = Workspace(files={"a.log": "x", "b.log": "x"})
    try:
        command = agent_shell.parse("ls > *.log")[0].commands[0]
        assert command.redirects[0].target == "*.log", command
        expanded = agent_shell.expand(command.argv, box.path)
        assert expanded == ["ls"], expanded
    finally:
        box.close()


# --- the module runs nothing ------------------------------------------------

def test_the_parser_never_reaches_a_shell_or_a_subprocess():
    """The claim the whole design rests on, checked against the source rather
    than against the docstring that makes it. This module is the thing that
    exists so that no model-authored string is ever handed to `/bin/sh` or
    `cmd.exe`; the day it imports subprocess, that claim is gone and nothing
    else in the pipeline would notice."""
    source = Path(agent_shell.__file__).read_text(encoding="utf-8")
    for forbidden in ("import subprocess", "subprocess.", "os.system",
                      "shell=True", "Popen", "os.exec", "os.spawn",
                      "eval(", "exec("):
        assert forbidden not in source, forbidden
    assert not hasattr(agent_shell, "subprocess"), agent_shell.subprocess


def test_the_module_reads_the_disk_in_one_place_and_through_the_shared_walk():
    """Globbing is the single exception to "nothing here touches anything",
    and it goes through `agent_file_ops` -- the same walk, the same pattern
    compiler and the same containment test `glob` and `grep` use. A second
    answer to "what counts as inside the workspace" is the kind of thing that
    gets updated in one place only."""
    source = Path(agent_shell.__file__).read_text(encoding="utf-8")
    for helper in ("agent_file_ops.iter_workspace_entries",
                   "agent_file_ops.glob_filter",
                   "agent_file_ops.within_workspace"):
        assert helper in source, helper
    # No second walk and no second containment test living here.
    assert "os.walk" not in source
    assert "def within_workspace" not in source
    assert "import os" not in source
