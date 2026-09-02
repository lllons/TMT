"""What may run: the decision, and the one file that remembers an answer.

`agent_shell` works out what the model's line MEANS. This works out whether it
may happen. Nothing here starts a process, opens a pipe or launches anything
-- it reads a parsed command and answers one of three words -- and that is the
point: a rule about execution that could itself execute has no boundary in it.

The division is the one `agent_plan`, `agent_review`, `agent_verify`,
`agent_reviewbot` and `agent_delegation` already keep. Pure decisions, one
persisted file, and no import of anything in TMT that runs a program.
`agent_bash` is what asks, `agent_sandbox` is what runs, and neither of them
holds a copy of the policy.

Three things here carry the weight.

**The boundary is four rules, and a remembered rule can never reach them.**
Steps 1, 2 and 3 below -- the program must be a bare name, it must not be a
shell or a privilege tool, and it must not carry inline code -- are the
boundary itself, and step 3a is the fourth: three git operations that TMT
already performs through a structured action which carries a guarantee this
tool cannot carry. `decide()` returns on a DENY *before* the rules file is
consulted at all, so an allow rule cannot switch one of them off. That is a
property of the control flow rather than a check somebody could move or
invert: there is no branch in which a boundary DENY and a rules lookup are
both live. `_apply_rules` refuses an upgrade a second time, which is belt and
braces for a future second caller, but the guarantee does not rest on it.

The general form of the same rule is easier to hold in the head, and it is
what is actually implemented: **a remembered rule is the answer to an approval
question.** You are only ever asked about an ASK, so an allow rule can only
ever settle an ASK. A deny rule can additionally forbid something that would
otherwise have run -- forbidding is never the dangerous direction. Nothing a
user can persist turns a DENY into anything else.

**Inline code is the load-bearing refusal.** Every other rule here works by
reading arguments -- which paths they name, which subcommand was asked for,
whether the network is wanted. `python -c "..."` is the argument that cannot
be read: it is a whole second program hiding inside a string, and no
inspection short of running it says what it does. Refusing it is what makes
the rest of this file mean anything, so it is refused with a sentence that
names the route that still works -- write the script with the file tools and
run the file, which is subject to exactly these limits and can be read back
afterwards.

**Three git operations are refused because TMT already has a better way to do
them.** `git push`, `git commit` and a `git config` write are not refused
because they are dangerous in themselves -- they are refused because TMT's
structured git actions carry guarantees that a command line cannot: a push is
authorised by the USER'S OWN WORDS in the task text and refused as
`PUSH_BLOCKED` otherwise, a commit validates both identities and carries the
`Co-authored-by: TMT code` trailer, and TMT never writes git configuration at
all. Reaching those commands through here would void all three silently, and
this tool cannot even see the task text an authorisation is read from. So it
is a DENY that names the action to use instead, rather than an approval
question: approval is the wrong shape of answer, because what the existing
rule is about is what the user ASKED FOR, not what they clicked through
afterwards.

**Unknown is ASK, never DENY.** A policy that refused everything it had not
heard of would be a policy nobody could use, and the first thing anybody would
do is find a way round it. So an unrecognised program is shown to the user and
they decide. Nothing unknown is ever silently allowed, which is the other half
of the same sentence: ASK with no terminal to ask is a refusal, and
`agent_bash` is where that is enforced.

A rule in the file is a program name (`frobnicate`) or a program and its
subcommand (`npm install`). Never a pattern, never a regular expression. A
regex rule is one nobody reads back correctly a month later, and a security
rule that cannot be read is a security rule nobody can audit.
"""

import hashlib
import json
import os
import re
from pathlib import Path

import agent_config
import agent_file_ops


# --- the three answers ------------------------------------------------------

ALLOW = "allow"
ASK = "ask"
DENY = "deny"

VERDICTS = (ALLOW, ASK, DENY)

# Which answer wins when a line holds several commands. Worst wins: a pipeline
# is one thing the user asked for, and running the harmless half of it while
# refusing the rest would leave a half-finished side effect nobody chose.
_SEVERITY = {ALLOW: 0, ASK: 1, DENY: 2}


# --- how much network the user granted --------------------------------------

# `offline` is the default everywhere, including when the mode handed in is not
# one of these. An unreadable network setting is not evidence that the network
# was wanted.
OFFLINE = "offline"
DEPS = "deps"
OPEN = "open"

NETWORK_MODES = (OFFLINE, DEPS, OPEN)


# --- the rules, by name -----------------------------------------------------
#
# Every Decision carries the name of the rule that produced it, so a refusal
# can say which rule refused and a test can assert on the rule rather than on
# the wording of a sentence. The names are the steps of the classification, in
# order.

RULE_SHAPE = "executable-shape"
RULE_DENIED = "denied-program"
RULE_INLINE = "inline-code"
RULE_GIT = "git-guarantee"
RULE_PATH = "path-argument"
RULE_NETWORK = "network"
RULE_DESTRUCTIVE = "destructive"
RULE_SAFE = "safe-read"
RULE_DEV = "development-tool"
RULE_UNKNOWN = "unknown-program"
RULE_REMEMBERED = "remembered-rule"

# The four that are the boundary. A DENY carrying one of these names is not an
# opinion about a particular command's arguments, it is the edge of what this
# tool is -- and `decide` never consults the rules file for any DENY, so no
# remembered rule reaches any of them.
#
# Strictly, in this module EVERY DENY is un-upgradable: a remembered allow can
# only ever settle an ASK, which is the general form the docstring gives. This
# set names the refusals that are about what the tool IS rather than about what
# one command's arguments happened to say, so `Rules.remember` can refuse to
# SAVE an allow for one of them -- a stored rule claiming a permission TMT does
# not honour would tell a reader something untrue, which is the failure this
# module's whole readable-rules design exists to avoid.
#
# Named as a set as well as being enforced by the control flow, because a test
# can read a set and cannot read a control flow, and because a reader coming to
# this file needs to know which four they are before they can check anything
# else.
BOUNDARY_RULES = frozenset({RULE_SHAPE, RULE_DENIED, RULE_INLINE, RULE_GIT})


class Decision(object):
    """One verdict, the sentence explaining it, and the rule that made it.

    `reason` is written for the model to act on, so it says what happened and
    what would work instead -- never how to get round the refusal. That
    distinction is the whole difference between a refusal that teaches and one
    that provokes a search for another route.

    It is immutable once built. `agent_delegation.DelegationConstraints` was
    sealed after exactly this was found there, and the reasoning carries over:
    a verdict is a security answer, and an object holding one that any caller
    can rewrite is one edit away from a caller that does. `classify` minting a
    fresh object per call makes the hole survivable rather than absent, and
    "survivable" is not the property to rest a boundary on.

    RuntimeError rather than AttributeError, for the reason that module gives:
    this codebase is full of `getattr(x, n, default)` and broad `except
    Exception` readers, and an AttributeError here would be indistinguishable
    from a typo and could go through quietly.
    """

    __slots__ = ("verdict", "reason", "rule")

    def __init__(self, verdict, reason, rule):
        object.__setattr__(self, "verdict", verdict)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "rule", rule)

    def __setattr__(self, name, value):
        raise RuntimeError(
            "A Decision cannot be changed after it is made. Build a new one "
            "with the verdict you mean; rewriting this one would change an "
            "answer the caller has already been given."
        )

    def __delattr__(self, name):
        raise RuntimeError("A Decision cannot have its fields removed.")

    def is_boundary(self):
        """Whether this is a DENY no remembered rule could ever change."""
        return self.verdict == DENY and self.rule in BOUNDARY_RULES

    def __eq__(self, other):
        if not isinstance(other, Decision):
            return NotImplemented
        return (self.verdict, self.reason, self.rule) == (
            other.verdict, other.reason, other.rule)

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self):
        return hash((self.verdict, self.reason, self.rule))

    def __repr__(self):
        return "<Decision %s %s>" % (self.verdict, self.rule)


# --- step 1: the shape of the program name ----------------------------------

# Extensions Windows appends to a program name. Stripped before every table
# lookup below, because `bash.exe` is `bash` and a deny list that only knew the
# bare form would be walked round by typing four extra characters.
_PROGRAM_EXTENSIONS = (".exe", ".com", ".bat", ".cmd", ".ps1")

# A trailing version on a program name: `python3.11`, `php8.2`. Stripped only
# when the stripped form is a name this module actually knows, so it can move a
# name INTO a table and never out of one -- and the denied table is consulted
# first, so the only direction this can push a decision is stricter.
_VERSION_SUFFIX = re.compile(r"^(.+?)(?:\.\d+)+$")
# The whole trailing version, dots and all: `php8.2` -> `php`. Used only after
# the dotted form above has failed, and only when what is left is a name this
# module already knows.
_VERSION_TAIL = re.compile(r"[0-9.]+$")

# A Windows drive letter. Checked explicitly rather than left to os.path.isabs,
# which answers about the platform TMT happens to be running on: `C:\Windows`
# is an absolute path in the model's head whichever machine reads it, and a
# policy that agreed with that only on Windows would be a policy with a hole in
# it that nobody could reproduce.
_DRIVE = re.compile(r"^[A-Za-z]:")


def _canonical(program):
    """The program name every table below is keyed by. "" when unusable."""
    name = str(program or "").strip().lower()
    for extension in _PROGRAM_EXTENSIONS:
        if name.endswith(extension) and len(name) > len(extension):
            name = name[:-len(extension)]
            break
    if name and name not in _KNOWN_NAMES:
        match = _VERSION_SUFFIX.match(name)
        if match and match.group(1) in _KNOWN_NAMES:
            name = match.group(1)
    if name and name not in _KNOWN_NAMES:
        # The dotted rule above reduces `python3.11` to `python3`, which is a
        # name this module knows. It does nothing for `php8.2` or `ruby3.0`,
        # whose reduced forms (`php8`, `ruby3`) are not names anybody lists --
        # so those two arrived unrecognised and `php8.2 -r 'code'` missed the
        # inline-code refusal and landed on "unknown, ask" instead.
        #
        # That failed SAFE, because an unknown program is an ASK and an ASK
        # with no terminal is a refusal. But the inline-code rule is the one
        # every other rule here rests on, and reaching it by luck is not
        # reaching it. Stripping the whole version tail catches the general
        # shape. It can only ever move a name INTO a table that already
        # exists, never invent one: `base64` reduces to `base`, which nobody
        # lists, so it stays unknown and stays an ASK.
        base = _VERSION_TAIL.sub("", name)
        if base and base != name and base in _KNOWN_NAMES:
            name = base
    return name


def _looks_absolute(text):
    """Whether this names a location rather than something under the cwd."""
    if not text:
        return False
    if text[0] in "/\\":
        return True
    if _DRIVE.match(text):
        return True
    return os.path.isabs(text)


def _has_parent_step(text):
    """Whether `..` appears as a path component of this argument."""
    for part in re.split(r"[\\/]+", text):
        if part == "..":
            return True
    return False


_SHAPE_REASON = (
    "DENIED: TMT runs a program by name, and %r is not a bare name. Write the "
    "program on its own -- `python`, not `/usr/bin/python` -- and let the "
    "curated PATH find it. A directory component, a drive letter or `..` in "
    "the program itself is how a policy about program names is walked round, "
    "so it is refused before anything else about the command is looked at."
)

_EMPTY_REASON = (
    "DENIED: there is no program to run in this command. Write the command as "
    "a program followed by its arguments."
)


def _shape_refusal(program):
    """Step 1. A Decision when the program name is not a bare name, else None."""
    text = str(program or "")
    if not text.strip():
        return Decision(DENY, _EMPTY_REASON, RULE_SHAPE)
    if ("/" in text or "\\" in text or _looks_absolute(text)
            or _has_parent_step(text) or ".." in text
            or text.startswith("~") or "\x00" in text):
        return Decision(DENY, _SHAPE_REASON % (text,), RULE_SHAPE)
    return None


# --- step 2: programs that are never available ------------------------------
#
# Four families, kept apart so the refusal can say which one applies. A model
# told only "not permitted" reasonably looks for another route to the same
# effect; a model told "TMT runs the pipeline itself, so a nested shell is
# neither needed nor available" has been told there is no route and why.
#
# The shells are the important family. Everything else in this file works by
# reading a command's arguments, and a shell's argument IS a command -- so a
# permitted `sh` would make every other rule here decorative. `env`, `xargs`,
# `nohup`, `setsid` and `start` are in the same family for the same reason:
# each of them exists to run a program chosen at runtime out of an argument.
#
# It is a blacklist and it is honest about being one: what protects the
# boundary is not that this list is complete but that a program which is NOT on
# it still has to pass steps 3 to 9, and an unknown program is ASK. A new shell
# nobody listed is an approval question, not a silent yes.

_DENIED_SHELLS = frozenset({
    "bash", "sh", "zsh", "ksh", "dash", "ash", "csh", "tcsh", "fish",
    "busybox", "cmd", "command", "powershell", "pwsh", "wsl",
    "env", "xargs", "nohup", "setsid", "start", "exec", "eval", "source",
    "script", "expect", "screen", "tmux",
})

_DENIED_PRIVILEGE = frozenset({"sudo", "su", "runas", "doas", "pkexec"})

_DENIED_REMOTE = frozenset({"ssh", "scp", "sftp", "telnet", "rlogin", "rsh"})

_DENIED_ADMIN = frozenset({
    "systemctl", "service", "shutdown", "reboot", "halt", "poweroff",
    "mount", "umount", "reg", "netsh", "sc", "diskpart", "takeown",
    "icacls", "attrib", "chown", "chgrp",
    # Scheduling is execution with the clock in between: a task registered now
    # runs later, outside this tool, under no policy at all.
    "at", "cron", "crontab", "schtasks",
})

DENIED_PROGRAMS = (_DENIED_SHELLS | _DENIED_PRIVILEGE | _DENIED_REMOTE
                   | _DENIED_ADMIN)

# `chmod` is deliberately NOT here, although the specification names it. What
# it names is "chmod outside the workspace", and that qualification is exactly
# what step 4 already enforces for every program without exception. A flat
# entry would refuse `chmod +x build.sh` on a file in the user's own project,
# which is ordinary work; leaving it out means the same command aimed at
# /etc/shadow is refused by the path rule instead, with a truer sentence.

_DENIED_REASONS = {
    "shell": (
        "DENIED: `%s` is not available. TMT parses the command line itself and "
        "runs each program directly, so a nested shell is neither needed nor "
        "available -- `|`, `&&`, `||`, `;`, `>` and `<` all work without one. "
        "Write the program you actually want to run as the command."
    ),
    "privilege": (
        "DENIED: `%s` raises privileges, and nothing TMT runs on your behalf "
        "may do that. If the work genuinely needs elevated rights, it is work "
        "for the user to do themselves, outside this tool."
    ),
    "remote": (
        "DENIED: `%s` opens a session on another machine, which is outside "
        "this workspace and outside anything TMT can account for. Work on the "
        "files in this workspace instead."
    ),
    "admin": (
        "DENIED: `%s` administers the machine rather than the project. TMT's "
        "command tool is for building, testing and inspecting this workspace; "
        "changes to the system belong to the user."
    ),
}


def _denied_family(name):
    if name in _DENIED_SHELLS:
        return "shell"
    if name in _DENIED_PRIVILEGE:
        return "privilege"
    if name in _DENIED_REMOTE:
        return "remote"
    if name in _DENIED_ADMIN:
        return "admin"
    return ""


def _denied_refusal(name):
    """Step 2. A Decision when the program is never available, else None."""
    family = _denied_family(name)
    if not family:
        return None
    return Decision(DENY, _DENIED_REASONS[family] % (name,), RULE_DENIED)


# --- step 3: inline code, the one argument that cannot be read --------------
#
# THE LOAD-BEARING RULE. Everything else in this file inspects arguments;
# inline code is an argument holding a whole second program, and no inspection
# short of running it says what it will do. A `python -c` that is allowed makes
# the path rule, the network rule and the destructive rule ornamental, because
# every one of them can be re-expressed inside the string.
#
# `python -m` is explicitly fine and always was: a module name is a name, it is
# resolved by the interpreter's own import machinery, and it is readable in the
# command line exactly as `pytest` is.

_INLINE_FLAGS = {
    "python": frozenset({"-c"}),
    "python3": frozenset({"-c"}),
    "py": frozenset({"-c"}),
    "node": frozenset({"-e", "--eval", "-p", "--print"}),
    "deno": frozenset({"-e", "--eval"}),
    "perl": frozenset({"-e", "-E"}),
    "ruby": frozenset({"-e"}),
    "php": frozenset({"-r"}),
    # The shells are already refused outright at step 2. Their inline flags are
    # listed anyway so that the day somebody takes a shell off that list for a
    # good reason, `-c` does not quietly become available with it.
    "bash": frozenset({"-c"}), "sh": frozenset({"-c"}),
    "zsh": frozenset({"-c"}), "ksh": frozenset({"-c"}),
    "dash": frozenset({"-c"}), "ash": frozenset({"-c"}),
    "csh": frozenset({"-c"}), "tcsh": frozenset({"-c"}),
    "fish": frozenset({"-c"}), "busybox": frozenset({"-c"}),
    "cmd": frozenset({"/c", "/k"}),
    "powershell": frozenset({"-command", "-c", "-encodedcommand", "-e", "-ec"}),
    "pwsh": frozenset({"-command", "-c", "-encodedcommand", "-e", "-ec"}),
}

# Programs whose FIRST subcommand is the inline form. `deno eval "..."` is
# `deno -e` written the other way round.
_INLINE_SUBCOMMANDS = {"deno": frozenset({"eval"})}

# Interpreters that read their program from standard input when they are given
# no script. `echo "import os" | python` is inline code arriving through a
# pipe, and the pipe is the half this module cannot see -- `classify` is handed
# one command at a time on purpose -- so the interpreter with nothing to run is
# refused wherever it appears.
_STDIN_INTERPRETERS = frozenset({"python", "python3", "py", "node", "perl",
                                 "ruby", "php", "deno"})

# The arguments that mean "tell me about yourself and exit", so that asking a
# tool its version is not mistaken for asking it to read a program from the
# pipe. `-v` is deliberately absent: it is verbose mode for python, not
# version, and an interpreter left reading standard input is the exact hole
# this rule exists to close.
_INFO_FLAGS = frozenset({"-V", "--version", "-h", "--help"})

# Programs that take a command to run as an argument of their own. `find` is a
# known-safe read at step 7, and `find . -exec rm -rf {} ;` is a shell by
# another name -- the same family as `xargs`, arriving through a program nobody
# thinks of as one.
_RUNS_A_PROGRAM = {
    "find": frozenset({"-exec", "-execdir", "-ok", "-okdir"}),
}

_INLINE_REASON = (
    "DENIED: `%s %s` runs code written inside its own argument. Every rule in "
    "TMT's command policy works by reading a command's arguments, and inline "
    "code is the one argument that cannot be read -- so it is refused "
    "whatever the code says. Write the script to a file with the file tools "
    "and run the file: it goes through exactly the same limits, and it can be "
    "read back afterwards by you and by the user."
)

_STDIN_REASON = (
    "DENIED: `%s` was given no script to run, so it reads its program from "
    "standard input -- inline code arriving through a pipe. Name the file to "
    "run (`%s path/to/script`), or `%s --version` if that is what you wanted."
)

_RUNS_PROGRAM_REASON = (
    "DENIED: `%s %s` runs another program chosen inside an argument, which is "
    "a shell by another name. Let `%s` produce the list of paths and act on "
    "them in a separate command."
)


def _inline_refusal(name, args):
    """Step 3. A Decision when the command carries inline code, else None."""
    flags = _INLINE_FLAGS.get(name, frozenset())
    for arg in args:
        lowered = arg.lower()
        if lowered in flags:
            return Decision(DENY, _INLINE_REASON % (name, arg), RULE_INLINE)
        # A single-dash cluster: python accepts `-Bc "code"`, and a rule that
        # only knew the unbundled spelling would be one keystroke away from
        # being switched off.
        if (name in ("python", "python3", "py") and len(lowered) > 1
                and lowered[0] == "-" and lowered[1] != "-"
                and "c" in lowered[1:]):
            return Decision(DENY, _INLINE_REASON % (name, arg), RULE_INLINE)
    subcommands = _INLINE_SUBCOMMANDS.get(name, frozenset())
    for arg in args:
        if not arg.startswith("-"):
            if arg.lower() in subcommands:
                return Decision(DENY, _INLINE_REASON % (name, arg), RULE_INLINE)
            break
    runs = _RUNS_A_PROGRAM.get(name, frozenset())
    for arg in args:
        if arg.lower() in runs:
            return Decision(DENY, _RUNS_PROGRAM_REASON % (name, arg, name),
                            RULE_INLINE)
    if name in _STDIN_INTERPRETERS:
        if _module_of(args) is None and not _has_operand(args):
            if not any(arg in _INFO_FLAGS for arg in args):
                return Decision(DENY, _STDIN_REASON % (name, name, name),
                                RULE_INLINE)
    return None


def _has_operand(args):
    """Whether anything here is a script to run rather than a flag.

    A lone `-` is not an operand: it is the conventional spelling of "read the
    program from standard input", which is the case above.
    """
    for arg in args:
        if arg and not arg.startswith("-"):
            return True
    return False


def _module_of(args):
    """The module named by `-m`, or None. `python -m pytest` -> `pytest`."""
    for index, arg in enumerate(args):
        if arg == "-m":
            if index + 1 < len(args):
                return args[index + 1]
            return ""
    return None


# --- step 3a: the three git operations TMT already does properly ------------
#
# These are not refused for being dangerous. They are refused because TMT
# performs each of them through a structured action that carries a guarantee
# the command line cannot carry, and reaching the raw command through here
# would void that guarantee without anything noticing:
#
#   push    `agent_git` requires the USER'S OWN WORDS in the task text to
#           authorise a push (`authorizes_push`) and refuses with
#           `PUSH_BLOCKED` otherwise, and the model cannot widen that. This
#           module is handed a command line and a workspace; it never sees the
#           task text, so it cannot make that check and must not pretend to.
#           An approval prompt is NOT the same guarantee -- the existing rule
#           is about what the user asked for, not about what they clicked
#           through afterwards -- which is why this is a DENY and not an ASK.
#   commit  `TMTGit.commit` validates both identities before it stages
#           anything, adds the `Co-authored-by: TMT code` trailer, and commits
#           the index the way TMT records. A raw commit has none of that, and
#           TMT would not know it had happened.
#   config  "TMT never writes git config" is a stated rule of this project.
#           A `git config user.email ...` here is exactly that rule being
#           broken.
#
# Each refusal names the action to use instead. A model told only "denied"
# tries the same command again with different flags; a model told "use the
# `git_push` action" does the right thing on its next round.

GIT_GUARDED_SUBCOMMANDS = frozenset({"push", "commit", "config"})

# Global options that sit BEFORE the subcommand and take a value of their own.
# Without these, `git -C sub push` reads as the subcommand `sub` and the push
# refusal is walked round by two characters.
_GIT_VALUE_FLAGS = frozenset({"-C", "-c", "--git-dir", "--work-tree",
                              "--namespace", "--exec-path", "--config-env",
                              "--super-prefix"})

# `git config` read forms, as a WHITELIST. Only these are allowed through; the
# reason it is a whitelist rather than a list of write flags is that telling a
# read from a write in the old positional syntax means counting operands, and
# `git config --type bool user.email` has an operand that is not an operand.
# That is guesswork, and a security rule built on guesswork is worse than one
# that refuses a form it could have allowed. `git config user.email` -- a real
# read -- is therefore refused too, and the refusal names the spelling that
# works.
_GIT_CONFIG_READ_FLAGS = frozenset({"--list", "-l", "--get", "--get-all",
                                    "--get-regexp", "--get-urlmatch"})
_GIT_CONFIG_READ_SUBCOMMANDS = frozenset({"get", "list"})
_GIT_CONFIG_WRITE_FLAGS = frozenset({"--unset", "--unset-all", "--add",
                                     "--replace-all", "--edit", "-e",
                                     "--rename-section", "--remove-section"})

_GIT_PUSH_REASON = (
    "DENIED: TMT does not push through the command tool. A push is authorised "
    "by the USER'S OWN WORDS in the task text -- the `git_push` action checks "
    "for that and refuses with PUSH_BLOCKED when it is absent -- and a command "
    "line cannot see the task text, so running `git push` here would go round "
    "the only check there is. This is not an approval question: use the "
    "`git_push` action."
)

_GIT_COMMIT_REASON = (
    "DENIED: TMT does not commit through the command tool. The `git_commit` "
    "action validates both identities before it stages anything, adds the "
    "`Co-authored-by: TMT code` trailer, and commits the index the way TMT "
    "records -- a raw `git commit` has none of that, and TMT would not know "
    "the commit had happened. Use the `git_commit` action."
)

_GIT_CONFIG_REASON = (
    "DENIED: TMT never writes git configuration -- not its own and not the "
    "user's -- so `git config` is refused in every form that could write one. "
    "Reading is allowed in the forms that can only read: `git config --list`, "
    "`git config --get <name>`. The commit identity is what the `git_identity` "
    "action reports. Changing configuration is the user's to do, outside this "
    "tool."
)

_GIT_REMOTE_REASON = (
    "DENIED: `git remote %s` changes where a remote name points, and where a "
    "push GOES is part of the same guarantee as whether it may happen. A push "
    "is authorised by the user's own words in the task text; repointing "
    "`origin` first would send that authorised push somewhere they never "
    "named. Reading remotes is allowed -- `git remote`, `git remote -v`, "
    "`git remote show origin`. Changing one is the user's to do, outside this "
    "tool."
)

_GIT_INLINE_CONFIG_REASON = (
    "DENIED: `git %s` sets git configuration for the length of one command. "
    "That is a configuration write, which TMT never makes, and it is also a "
    "way to hand git a program to run (`core.sshCommand`, `core.pager`) -- "
    "the inline-code refusal arriving through an option rather than an "
    "argument. Run git without it."
)


def _git_split(args):
    """(the global options, the subcommand, what follows it) for a git line.

    Written because `subcommand_of` takes the first non-flag argument, and for
    git that is wrong in both directions: `git -C sub push` would report `sub`,
    and `git log -c` would report a global option that is not one. The
    subcommand has to be found the way git finds it or every rule keyed on a
    git subcommand is one flag away from being switched off.
    """
    globals_ = []
    skip = False
    for index, arg in enumerate(args):
        if skip:
            globals_.append(arg)
            skip = False
            continue
        if arg.startswith("-"):
            globals_.append(arg)
            if arg in _GIT_VALUE_FLAGS:
                skip = True
            continue
        return globals_, arg.lower(), list(args[index + 1:])
    return globals_, "", []


def _git_config_reads(rest):
    """Whether this `git config` can only read. A whitelist; see above."""
    lowered = [a.lower() for a in rest]
    if any(a in _GIT_CONFIG_WRITE_FLAGS or a.split("=", 1)[0] in _GIT_CONFIG_WRITE_FLAGS
           for a in lowered):
        return False
    if any(a in _GIT_CONFIG_READ_FLAGS or a.split("=", 1)[0] in _GIT_CONFIG_READ_FLAGS
           for a in lowered):
        return True
    for arg in lowered:
        if not arg.startswith("-"):
            return arg in _GIT_CONFIG_READ_SUBCOMMANDS
    return False


def _git_refusal(name, args):
    """Step 3a. A Decision about a guarded git operation, else None."""
    if name != "git":
        return None
    globals_, subcommand, rest = _git_split(args)
    for index, arg in enumerate(globals_):
        # Only in the GLOBAL position: `git -c x=y commit` is a configuration
        # write, and `git log -c` is an ordinary flag meaning something else
        # entirely. The difference is where it sits, so that is what is read.
        if arg == "-c" or arg == "--config-env" or arg.startswith("--config-env="):
            return Decision(DENY, _GIT_INLINE_CONFIG_REASON % (arg,), RULE_GIT)
    if subcommand == "push":
        return Decision(DENY, _GIT_PUSH_REASON, RULE_GIT)
    if subcommand == "commit":
        return Decision(DENY, _GIT_COMMIT_REASON, RULE_GIT)
    if subcommand == "config" and not _git_config_reads(rest):
        return Decision(DENY, _GIT_CONFIG_REASON, RULE_GIT)
    if subcommand == "remote" and _git_remote_writes(rest):
        # WHERE A PUSH GOES is part of the push guarantee, and this is the
        # gap that shape leaves if only `push` itself is guarded. `git remote
        # set-url origin <somewhere else>` pushes nothing; it repoints the
        # name that the NEXT push resolves through -- and that next push can
        # be a perfectly ordinary `git_push`, authorised by the user's own
        # words, going somewhere they never named. The command that does the
        # damage is not the command that needs the authority, which is why
        # guarding the verb alone is not enough.
        #
        # Reading a remote is left alone: `git remote`, `-v` and `show` say
        # where things point and change nothing, and that is worth having.
        return Decision(DENY, _GIT_REMOTE_REASON % (rest[0],), RULE_GIT)
    return None


# The `git remote` operands that change where a name points. Everything else
# it takes -- nothing at all, `-v`, `show`, `get-url` -- only reports.
_GIT_REMOTE_WRITES = frozenset({
    "add", "set-url", "remove", "rm", "rename", "set-head", "set-branches",
    "prune", "update",
})


def _git_remote_writes(rest):
    """Whether this `git remote` changes a remote rather than reporting one."""
    for argument in rest:
        if argument.startswith("-"):
            continue
        return argument.lower() in _GIT_REMOTE_WRITES
    return False


# --- step 4: every path an argument names -----------------------------------
#
# Containment is `agent_file_ops.within_workspace`, which is TMT's one
# containment test and resolves symlinks. It is not reimplemented here: a
# second test is a second answer, and the day one of them is tightened the
# other becomes the way round it.
#
# `within_workspace` reads the workspace from `agent_config` rather than taking
# it as an argument, which is right for the running program and cannot answer
# about a root a caller handed in. When the two are the same -- every real
# session -- that function IS the answer. When they differ, the same comparison
# is made against the root we were given rather than a different question being
# asked of a different anchor.

_PATH_REASON = (
    "DENIED: %s is outside this workspace. Every path a command names has to "
    "stay inside %s, and that is checked after symbolic links are resolved. "
    "Name the file relative to the working directory."
)

_ABSOLUTE_REASON = (
    "DENIED: %s is an absolute path. Commands name files relative to their "
    "working directory inside the workspace, so that what a command can reach "
    "is the same question as what this workspace holds."
)

_PARENT_REASON = (
    "DENIED: %s climbs out of the working directory with `..`. Paths stay "
    "inside the workspace; write the path relative to the working directory "
    "instead."
)


def _inside(candidate, root):
    """Whether this resolved path is inside the workspace. Never raises.

    False for anything that cannot be resolved -- a broken link, a path too
    long, a parent that cannot be read. An entry that cannot be SHOWN to be
    inside is treated as outside, which is the direction a containment test has
    to fail in.
    """
    try:
        target = Path(candidate).resolve()
    except (OSError, ValueError, TypeError):
        return False
    try:
        configured = Path(agent_file_ops.workspace()).resolve()
    except (OSError, ValueError, TypeError):
        configured = None
    anchor = _root_path(root)
    if configured is not None and anchor == configured:
        return agent_file_ops.within_workspace(target)
    return anchor == target or anchor in target.parents


def _root_path(root):
    try:
        return Path(root or agent_file_ops.workspace()).resolve()
    except (OSError, ValueError, TypeError):
        return Path(agent_config.ROOT_DIR)


def _cwd_path(cwd, root):
    try:
        base = _root_path(root)
        return base if cwd is None else Path(cwd).resolve()
    except (OSError, ValueError, TypeError):
        return _root_path(root)


def _path_candidates(args):
    """The arguments to test as paths, each as (what to show, what to test).

    A `-` prefixed argument is a flag and not a path -- except for the
    `--out=/etc/passwd` form, where the path is the half after the first `=`.
    A flag that carries a path is still a command naming a file, and the rule
    is about what the command can reach rather than about how the argument was
    spelled.
    """
    out = []
    for arg in args:
        if not arg:
            continue
        if arg.startswith("-"):
            if "=" in arg:
                value = arg.split("=", 1)[1]
                if value:
                    out.append((arg, value))
            continue
        out.append((arg, arg))
    return out


def _path_refusal(args, cwd, root):
    """Step 4. A Decision when an argument leaves the workspace, else None."""
    base = _cwd_path(cwd, root)
    for shown, value in _path_candidates(args):
        if _looks_absolute(value):
            return Decision(DENY, _ABSOLUTE_REASON % (_quote(shown),), RULE_PATH)
        if _has_parent_step(value):
            return Decision(DENY, _PARENT_REASON % (_quote(shown),), RULE_PATH)
        if not _inside(base / value, root):
            return Decision(DENY, _PATH_REASON % (_quote(shown),
                                                  _quote(str(_root_path(root)))),
                            RULE_PATH)
    return None


def _redirect_refusal(redirects, cwd, root):
    """Step 4, for a redirect's target. `> ../out.txt` is a path like any other.

    `agent_sandbox.run_pipeline` applies redirects to targets "the caller has
    already confined", and this is where that confinement happens: a redirect
    is the one way a command names a file TMT will open on its behalf without
    the program ever seeing the name.
    """
    for redirect in redirects or ():
        kind = str(getattr(redirect, "kind", "") or "")
        if kind not in (">", ">>", "<", "2>"):
            # `2>&1` names a file descriptor, not a file. There is nothing to
            # contain and nothing to resolve.
            continue
        target = getattr(redirect, "target", None)
        if not isinstance(target, str) or not target:
            continue
        refusal = _path_refusal([target], cwd, root)
        if refusal is not None:
            return Decision(refusal.verdict,
                            refusal.reason + " (this is the target of a `%s` "
                                             "redirect.)" % kind,
                            RULE_PATH)
    return None


def _quote(text):
    return "`%s`" % text


# --- step 5: the network ----------------------------------------------------
#
# Two different things, and they are kept apart because they fail differently.
# A network PROGRAM exists to move bytes off the machine and has no offline
# meaning at all. A package manager has a great deal of offline meaning --
# `npm test`, `cargo build`, `pip list` -- and only some of its subcommands
# fetch, so the subcommand is what is read rather than the program.

NETWORK_PROGRAMS = frozenset({
    "curl", "wget", "nc", "ncat", "netcat", "telnet", "ftp", "sftp", "tftp",
    "rsync", "scp",
})

# Subcommands that reach the network. Keyed by program, so `cargo build` is
# untouched and `cargo fetch` is not.
FETCH_SUBCOMMANDS = {
    "npm": frozenset({"install", "i", "ci", "add", "update", "up", "audit",
                      "publish"}),
    "pnpm": frozenset({"install", "i", "add", "update", "up", "fetch",
                       "publish"}),
    "yarn": frozenset({"install", "add", "up", "upgrade", "publish"}),
    "pip": frozenset({"install", "download", "wheel"}),
    "pip3": frozenset({"install", "download", "wheel"}),
    "cargo": frozenset({"add", "fetch", "install", "publish", "update"}),
    "go": frozenset({"get", "install", "download"}),
    "gem": frozenset({"install", "update", "push"}),
    "bundle": frozenset({"install", "update"}),
    "composer": frozenset({"install", "update", "require"}),
    "dotnet": frozenset({"restore"}),
}

# Whole programs whose every subcommand fetches, and which install into the
# machine rather than into the project.
SYSTEM_PACKAGE_MANAGERS = frozenset({"apt", "apt-get", "apk", "yum", "dnf",
                                     "pacman", "brew", "choco", "winget",
                                     "scoop"})

_NETWORK_DENY_REASON = (
    "DENIED: `%s` needs the network and this command is running with the "
    "network %s. Network access is the user's to grant, not yours: they can "
    "re-run the task with the network set to `deps` for package installs or "
    "`open`. Nothing was fetched and nothing was changed."
)

_NETWORK_ASK_REASON = (
    "`%s` reaches the network, so it needs the user's approval before it runs."
)

_FETCH_DENY_REASON = (
    "DENIED: `%s %s` downloads packages, and this command is running offline. "
    "Dependency installs are the user's to grant: they can re-run the task "
    "with the network set to `deps`. Nothing was fetched, and no lockfile or "
    "cache was written."
)

_FETCH_ASK_REASON = (
    "`%s %s` downloads packages into this project, so it needs the user's "
    "approval before it runs."
)


def _network_mode(network):
    """The mode, defaulting to offline for anything unrecognised.

    An unreadable setting is not evidence the network was wanted, and this is
    the one place in this file where a caller's mistake could otherwise widen
    what runs.
    """
    mode = str(network or "").strip().lower()
    return mode if mode in NETWORK_MODES else OFFLINE


def _network_refusal(name, subcommand, mode):
    """Step 5. A Decision about reaching the network, else None."""
    if name in NETWORK_PROGRAMS:
        if mode == OPEN:
            return Decision(ASK, _NETWORK_ASK_REASON % (name,), RULE_NETWORK)
        return Decision(DENY, _NETWORK_DENY_REASON % (name, mode), RULE_NETWORK)
    if name in SYSTEM_PACKAGE_MANAGERS:
        return _fetch_decision(name, subcommand or "", mode)
    fetches = FETCH_SUBCOMMANDS.get(name)
    if fetches and subcommand and subcommand.lower() in fetches:
        return _fetch_decision(name, subcommand, mode)
    return None


def _fetch_decision(name, subcommand, mode):
    if mode == OFFLINE:
        return Decision(DENY, _FETCH_DENY_REASON % (name, subcommand),
                        RULE_NETWORK)
    return Decision(ASK, _FETCH_ASK_REASON % (name, subcommand), RULE_NETWORK)


# --- step 6: destructive ----------------------------------------------------
#
# ASK rather than DENY, because deleting a file is ordinary work and the person
# who should decide whether this particular deletion is ordinary is the user.
# The one exception is the workspace root itself, or a filesystem root: that is
# not a question anybody wants asked, because there is no answer to it that
# leaves the session with a project to work on.

DESTRUCTIVE_PROGRAMS = frozenset({
    "rm", "rmdir", "del", "rd", "erase", "mv", "move", "dd", "truncate",
    "shred", "kill", "taskkill", "pkill", "killall",
})

# Subcommands that destroy work, keyed by program. `git reset` without
# `--hard` is recoverable and is not here; `git reset --hard` is not, and is.
DESTRUCTIVE_SUBCOMMANDS = {
    "git": frozenset({"clean"}),
}

_DELETING = frozenset({"rm", "rmdir", "del", "rd", "erase", "shred"})

_DESTRUCTIVE_REASON = (
    "`%s` can destroy work that nothing here can put back, so it needs the "
    "user's approval before it runs."
)

_GIT_DESTRUCTIVE_REASON = (
    "`git %s` discards work that is not recoverable from this repository, so "
    "it needs the user's approval before it runs."
)

_ROOT_REASON = (
    "DENIED: `%s` names %s, which is %s. That is not an approval question: "
    "there is no version of it that leaves this session with a project to "
    "work in. Delete the specific files you meant to delete, by name."
)


def _filesystem_root(path):
    """Whether a resolved path is the top of a filesystem: `/` or `C:\\`."""
    return path.parent == path


def _destructive_refusal(name, subcommand, args, cwd, root):
    """Step 6. A Decision about destroying something, else None."""
    if name in _DELETING:
        base = _cwd_path(cwd, root)
        anchor = _root_path(root)
        for _, value in _path_candidates(args):
            try:
                target = (base / value).resolve()
            except (OSError, ValueError, TypeError):
                continue
            if target == anchor:
                return Decision(DENY, _ROOT_REASON % (name, _quote(value),
                                                      "the workspace itself"),
                                RULE_DESTRUCTIVE)
            if _filesystem_root(target):
                return Decision(DENY, _ROOT_REASON % (name, _quote(value),
                                                      "the root of a filesystem"),
                                RULE_DESTRUCTIVE)
    if name in DESTRUCTIVE_PROGRAMS:
        return Decision(ASK, _DESTRUCTIVE_REASON % (name,), RULE_DESTRUCTIVE)
    if name == "git":
        found = _git_destructive(subcommand, args)
        if found:
            return Decision(ASK, _GIT_DESTRUCTIVE_REASON % (found,),
                            RULE_DESTRUCTIVE)
    return None


def _git_destructive(subcommand, args):
    """The destructive git operation this command performs, or "".

    There is deliberately nothing here about `push --force`. Every form of
    `git push` is refused outright at step 3a, which runs first, so a branch
    here could only ever be reached by reordering the steps -- and what it
    would do then is answer ASK to a command the step above answers DENY to,
    which is the more specific rule downgrading the stricter one. One
    question, one answer, in one place. If push ever leaves the guarded set,
    this is where the force case has to come back.
    """
    # Split the way git does rather than assuming the subcommand is args[0]:
    # `git -C sub reset --hard` has two global arguments in front of it.
    _, found, following = _git_split(args)
    sub = (subcommand or found or "").lower()
    rest = [a.lower() for a in following]
    if sub in DESTRUCTIVE_SUBCOMMANDS.get("git", frozenset()):
        return sub
    if sub == "reset" and "--hard" in rest:
        return "reset --hard"
    if sub == "checkout" and "--" in rest:
        return "checkout --"
    if sub == "restore" and any(a in ("--staged", "--worktree", ".")
                                for a in rest):
        return "restore"
    return ""


# --- steps 7 and 8: what is recognised --------------------------------------

# Reading the workspace and printing what is there. These change nothing, and
# what they can reach is already bounded by step 4.
SAFE_PROGRAMS = frozenset({
    "ls", "dir", "cat", "type", "head", "tail", "wc", "find", "file", "stat",
    "pwd", "cd", "echo", "sort", "uniq", "cut", "tr", "diff", "which",
    "where", "tree", "basename", "dirname", "realpath", "printf", "date",
    "grep", "egrep", "fgrep", "rg",
})

# `env` is denied at step 2 and is named here only so a reader looking for it
# in the safe list finds this sentence instead: it runs a program chosen from
# its own arguments, which is the shell family, and printing the environment is
# not worth reopening that.

# `config` is in here and that is not a contradiction of step 3a: step 3a runs
# first and refuses every `git config` that could write one, so the only
# spellings that reach this line are `--list`, `--get`, `--get-all`,
# `--get-regexp`, `--get-urlmatch` and the `get`/`list` subcommands. Reading
# configuration is an ordinary safe read; it is writing it that TMT never does.
#
# `push`, `commit` and the `config` write forms are absent from every ALLOW
# table in this module, and would be unreachable if they were present.
SAFE_SUBCOMMANDS = {
    "git": frozenset({"status", "diff", "log", "show", "branch", "remote",
                      "rev-parse", "describe", "blame", "shortlog",
                      "ls-files", "config"}),
}

# Tools a project is built and tested with. ALLOW, because refusing them is
# refusing the thing this tool exists for -- and because what they can reach is
# still bounded by every rule above.
DEV_PROGRAMS = frozenset({
    "python", "python3", "py", "pip", "pip3", "pytest", "tox", "ruff", "black",
    "mypy", "flake8", "isort", "coverage", "nox",
    "node", "npm", "npx", "pnpm", "yarn", "tsc", "eslint", "prettier", "jest",
    "vitest", "deno", "bun",
    "cargo", "rustc", "rustfmt", "clippy-driver",
    "go", "gofmt", "golangci-lint",
    "make", "cmake", "ninja", "meson", "gradle", "mvn", "javac", "java",
    "kotlinc", "dotnet", "msbuild",
    "bundle", "rake", "ruby", "gem", "composer", "php", "perl",
    "git", "gcc", "g++", "clang", "clang++", "cl", "swiftc",
})

# Every name this module recognises anywhere, used only by `_canonical` to
# decide whether stripping a trailing version leaves something known.
_KNOWN_NAMES = (DENIED_PROGRAMS | NETWORK_PROGRAMS | SYSTEM_PACKAGE_MANAGERS
                | DESTRUCTIVE_PROGRAMS | SAFE_PROGRAMS | DEV_PROGRAMS
                | frozenset(FETCH_SUBCOMMANDS))

_SAFE_REASON = "`%s` reads the workspace and changes nothing."
_SAFE_SUB_REASON = "`%s %s` reports on the repository and changes nothing."
_DEV_REASON = "`%s` is one of this project's development tools."

_UNKNOWN_REASON = (
    "TMT does not recognise `%s`, so it is not refused and it is not run "
    "silently -- the user is shown the command and decides. Approve it for "
    "this run, or remember `%s` so it runs in this workspace without asking "
    "again."
)


# --- putting one command through the steps ----------------------------------

def program_of(command):
    """The program a Command runs, as this module's tables key it. "" if none."""
    argv = _argv(command)
    return _canonical(argv[0]) if argv else ""


def subcommand_of(command):
    """The first non-flag argument -- `install` in `npm install left-pad`.

    git is asked the way git parses itself (`_git_split`), because its global
    options come BEFORE the subcommand and two of them take a value: the naive
    answer for `git -C sub push` is `sub`, and every rule keyed on a git
    subcommand -- this one, the destructive rule, the safe-read rule and the
    pattern a saved rule matches on -- would be one flag away from being
    switched off.
    """
    argv = _argv(command)
    args = argv[1:]
    if argv and _canonical(argv[0]) == "git":
        return _git_split(args)[1]
    for arg in args:
        if arg and not arg.startswith("-"):
            return arg
    return ""


def pattern_for(command):
    """The rules-file pattern that would settle this command.

    `npm install` for a program whose subcommand is what was decided about,
    `frobnicate` for one where the program is the whole story. This is what a
    refusal offers the user and what `Rules.remember` is meant to be handed, so
    it is computed in one place rather than assembled by whoever writes the
    sentence.
    """
    name = program_of(command)
    if not name:
        return ""
    subcommand = subcommand_of(command)
    if subcommand and (name in FETCH_SUBCOMMANDS
                       or name in SAFE_SUBCOMMANDS
                       or name in DESTRUCTIVE_SUBCOMMANDS):
        return "%s %s" % (name, subcommand.lower())
    return name


def _argv(command):
    """A Command's argv as a list of strings, whatever it was handed.

    Duck-typed on purpose. This module is written against `agent_shell.Command`
    but does not import it: a partially-written parser must not be able to stop
    the policy loading, and the only thing the policy needs from a command is
    two attributes.
    """
    argv = getattr(command, "argv", None)
    if argv is None and isinstance(command, (list, tuple)):
        argv = command
    if not argv:
        return []
    return [str(a) for a in argv]


def _redirects(command):
    return getattr(command, "redirects", None) or ()


def _effective_names(name, args):
    """The names step 5 and step 6 should ask about, worst first.

    `python -m pip install requests` is a package install wearing an
    interpreter's name, and a policy that only read `python` would have a hole
    in it the size of the index. So the module named by `-m` is asked about as
    well -- but only ever to make the answer WORSE. If the module is not
    something this module has an opinion about, the command goes on being
    classified as the interpreter it is, so `python -m pytest` stays an
    ordinary development tool and `python -m json.tool` is not turned into an
    unknown program.
    """
    module = _module_of(args)
    if not module:
        return [name]
    return [_canonical(module), name]


def classify(command, cwd=None, root=None, network=OFFLINE):
    """The verdict for ONE command, before any remembered rule is consulted.

    The nine steps in order, first match wins. Everything it needs is in the
    command, the working directory and the workspace -- there is no state, no
    file and no clock, so the same command always gets the same answer and a
    test can ask about any one step in isolation.
    """
    argv = _argv(command)
    program = argv[0] if argv else ""

    # Step 1: the shape of the program name. BOUNDARY.
    refusal = _shape_refusal(program)
    if refusal is not None:
        return refusal

    name = _canonical(program)
    args = argv[1:]

    # Step 2: programs that are never available. BOUNDARY.
    refusal = _denied_refusal(name)
    if refusal is not None:
        return refusal

    # Step 3: inline code. BOUNDARY, and the one the rest of this rests on.
    refusal = _inline_refusal(name, args)
    if refusal is not None:
        return refusal

    # Step 3a: the git operations TMT already does properly. BOUNDARY.
    #
    # Before step 4 and before step 6 on purpose. Before step 4 because "use
    # the `git_push` action" is a more useful correction than a complaint
    # about an argument; before step 6 because `git push --force` must land on
    # the flat push refusal above rather than on the destructive rule's ASK,
    # which would be the more specific rule quietly DOWNGRADING the stricter
    # one.
    refusal = _git_refusal(name, args)
    if refusal is not None:
        return refusal

    # Step 4: every path the command names, including a redirect's target.
    refusal = _path_refusal(args, cwd, root)
    if refusal is not None:
        return refusal
    refusal = _redirect_refusal(_redirects(command), cwd, root)
    if refusal is not None:
        return refusal

    subcommand = subcommand_of(command)
    mode = _network_mode(network)

    # Step 5: the network.
    for candidate in _effective_names(name, args):
        refusal = _network_refusal(candidate, _subcommand_after(candidate, name,
                                                                args, subcommand),
                                   mode)
        if refusal is not None:
            return refusal

    # Step 6: destroying something.
    for candidate in _effective_names(name, args):
        refusal = _destructive_refusal(candidate, subcommand, args, cwd, root)
        if refusal is not None:
            return refusal

    # Step 7: reads that change nothing.
    safe = SAFE_SUBCOMMANDS.get(name)
    if safe and subcommand and subcommand.lower() in safe:
        return Decision(ALLOW, _SAFE_SUB_REASON % (name, subcommand), RULE_SAFE)
    if name in SAFE_PROGRAMS:
        return Decision(ALLOW, _SAFE_REASON % (name,), RULE_SAFE)

    # Step 8: the tools a project is built with.
    if name in DEV_PROGRAMS:
        return Decision(ALLOW, _DEV_REASON % (name,), RULE_DEV)

    # Step 9: everything else is a question, never a silent yes.
    return Decision(ASK, _UNKNOWN_REASON % (name, name), RULE_UNKNOWN)


def _subcommand_after(candidate, name, args, subcommand):
    """The subcommand to read for `candidate`.

    For the program itself that is the command's own first operand. For a
    module reached through `-m` it is the operand after the module name, so
    `python -m pip install x` asks about `pip` and `install` rather than about
    `pip` and `pip`.
    """
    if candidate == name:
        return subcommand
    module = _module_of(args)
    if not module:
        return subcommand
    try:
        index = args.index(module)
    except ValueError:
        return subcommand
    for arg in args[index + 1:]:
        if arg and not arg.startswith("-"):
            return arg
    return ""


# --- the whole line ---------------------------------------------------------

def iter_commands(stages):
    """Every Command in a parsed line, in order.

    Takes what `agent_shell.parse` returns, and also a single Pipeline, a
    single Command, or a plain list of any of those -- because the caller that
    matters most is a test, and a test should not have to build three objects
    to ask about one command. Duck-typed for the reason `_argv` is.
    """
    if stages is None:
        return
    if (hasattr(stages, "argv") or hasattr(stages, "pipeline")
            or hasattr(stages, "commands")):
        stages = [stages]
    for item in stages:
        if hasattr(item, "argv"):
            yield item
            continue
        pipeline = getattr(item, "pipeline", item)
        commands = getattr(pipeline, "commands", None)
        if commands is None:
            if hasattr(pipeline, "argv"):
                yield pipeline
            continue
        for command in commands:
            if hasattr(command, "argv"):
                yield command


_NOTHING_REASON = (
    "DENIED: there is no command to run. Write a program and its arguments."
)


def decide(stages, cwd=None, root=None, network=OFFLINE, rules=None):
    """The verdict for a whole parsed line. Worst wins.

    A pipeline is one thing the user asked for, so the answer is the worst
    answer any part of it gets: running the harmless half of `ls | frobnicate`
    and refusing the rest would leave a side effect nobody chose and a result
    nobody can read.

    ### Where the boundary lives

    A DENY returns from this function BEFORE `rules` is read. Not after a check
    that a rule failed to pass -- before, on a line of its own, with the rules
    argument untouched. There is no branch in which a boundary DENY and a
    remembered rule are both live, so an allow rule cannot switch off step 1,
    step 2 or step 3, and no edit to the rules file can. That is what makes the
    rules file safe to persist at all: if it could reopen `bash -c`, it would
    be the escape hatch this whole design exists to remove.

    The general form, which is what is actually implemented: a remembered rule
    is the answer to an approval question. An allow rule can only ever settle
    an ASK, because ASK is the only verdict that ever reaches `_apply_rules`
    with anything to give. A deny rule can additionally forbid something that
    would otherwise have run, which is never the dangerous direction.
    """
    commands = list(iter_commands(stages))
    if not commands:
        return Decision(DENY, _NOTHING_REASON, RULE_SHAPE)
    worst = None
    for command in commands:
        decision = classify(command, cwd, root, network)
        if decision.verdict == DENY:
            # THE BOUNDARY. `rules` is not consulted for a refusal, here or
            # anywhere else in this function.
            return decision
        decision = _apply_rules(decision, command, rules)
        if worst is None or _SEVERITY[decision.verdict] > _SEVERITY[worst.verdict]:
            worst = decision
    return worst


_REMEMBERED_ALLOW = (
    "`%s` is allowed in this workspace by a rule the user saved earlier."
)

_REMEMBERED_DENY = (
    "DENIED: `%s` is refused by a rule the user saved earlier. The rule is in "
    "TMT's saved command rules and only the user can remove it."
)


def _apply_rules(decision, command, rules):
    """A remembered answer, applied to a verdict that is not already a DENY.

    Two layers, one rule. `decide` returns before this is reached for a DENY;
    this refuses to upgrade one anyway, because the day somebody adds a second
    caller they will not have read the paragraph in `decide` that explains why
    they did not need to.
    """
    if decision.verdict == DENY:
        return decision
    if rules is None:
        return decision
    try:
        remembered = rules.verdict_for(command)
    except Exception:
        # A rules object that cannot answer has not authorised anything. This
        # is the one guard here that fails closed in both directions: the
        # verdict `classify` produced stands, and a broken rules file can
        # neither widen it nor narrow it into nonsense.
        return decision
    pattern = pattern_for(command)
    if remembered == DENY:
        return Decision(DENY, _REMEMBERED_DENY % (pattern,), RULE_REMEMBERED)
    if remembered == ALLOW and decision.verdict == ASK:
        return Decision(ALLOW, _REMEMBERED_ALLOW % (pattern,), RULE_REMEMBERED)
    return decision


# --- the rules file ---------------------------------------------------------
#
# Under INSTALL_DIR, keyed by a hash of the workspace path, exactly as
# `agent_memory` and `agent_index` key theirs -- TMT's own state never lands in
# the user's project, and a saved command rule is as much TMT's own state as a
# saved model choice is.
#
# One file with a section per workspace and a `global` section, rather than a
# file per workspace, because the global rules have to live somewhere and a
# second file for them would be a second thing to find, load and keep in step.

RULES_FILE_NAME = ".tmt_bash_rules.json"

FORMAT_VERSION = 1

WORKSPACE = "workspace"
GLOBAL = "global"
SCOPES = (WORKSPACE, GLOBAL)

# A pattern is one or two words. The first is a program name; the second, when
# there is one, is a subcommand. Nothing else is a pattern -- no wildcards, no
# regular expressions, no flags -- because the whole value of this file is that
# a user can read a line of it a month later and know exactly what it permits.
_PATTERN_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")

MAX_RULES = 200


def rules_path():
    """The rules file. Read at call time; nothing is created."""
    return Path(agent_config.INSTALL_DIR) / RULES_FILE_NAME


def _workspace_key(path):
    """A short stable name for a workspace path.

    `agent_memory._workspace_key`'s reasoning, and its lower-casing on Windows:
    a path is not a filename, and C:\\Coding and c:\\coding are one directory.
    """
    text = str(Path(path).resolve())
    if os.name == "nt":
        text = text.lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def normalise_pattern(pattern):
    """`  NPM   Install ` -> `npm install`. "" when it is not a pattern.

    Lower-cased, because program names are matched lower-cased everywhere else
    in this module and a rule the user typed in capitals must mean the same
    thing as the command they typed in lower case.
    """
    words = str(pattern or "").split()
    if not words or len(words) > 2:
        return ""
    for word in words:
        if not _PATTERN_WORD.match(word):
            return ""
    return " ".join(word.lower() for word in words)


def _boundary_pattern(pattern):
    """Whether a saved ALLOW for this pattern could never take effect.

    Used to refuse SAVING such a rule. The guarantee does not depend on this
    -- `decide` returns before the rules are read -- but a file holding
    `bash: allow` or `git push: allow` would tell a reader something untrue
    about what TMT will do, and a security file nobody can read correctly is
    the thing this module's docstring refuses to produce.

    A bare `git` is NOT here: git is mostly permitted, an allow rule for it
    settles real ASKs, and it simply does not reach the three guarded
    subcommands. Refusing to save it would be refusing a rule that means
    exactly what it says.
    """
    words = pattern.split(" ")
    if words[0] in DENIED_PROGRAMS:
        return True
    return (len(words) == 2 and words[0] == "git"
            and words[1] in GIT_GUARDED_SUBCOMMANDS)


class Rules(object):
    """The allow and deny rules the user has saved. One workspace's view.

    Never fatal, which is `agent_memory`'s rule and for its reason: a rules
    file that is absent, unreadable, truncated, hand-edited into nonsense or
    written by a later version reads as no rules at all. Starting with none is
    exactly the state a first-ever session is in, so it is known to work, and
    deleting the file must always be a safe thing for a user to do.

    Loading it can only ever make TMT more permissive in one direction -- an
    ASK becoming an ALLOW -- so failing to load it fails safe by construction.
    """

    __slots__ = ("workspace", "key", "_allow", "_deny", "_global_allow",
                 "_global_deny")

    def __init__(self, workspace=None, allow=(), deny=(), global_allow=(),
                 global_deny=()):
        self.workspace = str(workspace or agent_config.ROOT_DIR)
        self.key = _workspace_key(self.workspace)
        self._allow = set(allow)
        self._deny = set(deny)
        self._global_allow = set(global_allow)
        self._global_deny = set(global_deny)

    # --- loading ----------------------------------------------------------

    @classmethod
    def load(cls, workspace=None):
        """The saved rules for one workspace, plus the global ones."""
        root = str(workspace or agent_config.ROOT_DIR)
        data = _read_file()
        key = _workspace_key(root)
        section = data.get("workspaces", {}).get(key, {})
        common = data.get(GLOBAL, {})
        return cls(workspace=root,
                   allow=_clean_patterns(section.get(ALLOW)),
                   deny=_clean_patterns(section.get(DENY)),
                   global_allow=_clean_patterns(common.get(ALLOW)),
                   global_deny=_clean_patterns(common.get(DENY)))

    # --- reading ----------------------------------------------------------

    def patterns(self, scope=WORKSPACE):
        """(allow, deny) for one scope, each sorted, for a report."""
        if scope == GLOBAL:
            return tuple(sorted(self._global_allow)), tuple(sorted(self._global_deny))
        return tuple(sorted(self._allow)), tuple(sorted(self._deny))

    def any(self):
        return bool(self._allow or self._deny or self._global_allow
                    or self._global_deny)

    def verdict_for(self, command):
        """The remembered verdict for this command, or None.

        Both spellings are looked for -- `npm` and `npm install` -- so a rule
        can be as broad or as narrow as the user meant it.

        **A remembered DENY always beats a remembered ALLOW**, whichever is the
        more specific and whichever scope it is in. A deny is a decision to
        forbid something, and a rule that could be undone by a narrower rule
        somewhere else in the file is a rule nobody can read back with
        confidence -- which is the property this whole file is organised
        around. There is no precedence to remember: if any rule says no, the
        answer is no.
        """
        candidates = self._candidates(command)
        if not candidates:
            return None
        if any(c in self._deny or c in self._global_deny for c in candidates):
            return DENY
        if any(c in self._allow or c in self._global_allow for c in candidates):
            return ALLOW
        return None

    def _candidates(self, command):
        name = program_of(command)
        if not name:
            return ()
        subcommand = subcommand_of(command)
        if subcommand:
            return (name, "%s %s" % (name, subcommand.lower()))
        return (name,)

    # --- writing ----------------------------------------------------------

    def remember(self, pattern, verdict, scope=WORKSPACE):
        """Save a rule, and say what was saved.

        Raises `ValueError` for a pattern or a verdict it cannot store. Every
        READ in this module defaults quietly, because a missing rule costs an
        approval question and nothing else; a WRITE that silently did nothing
        would show the user a rule they had just approved and have nothing on
        disk, and they would find out the next time they were asked about the
        same command. That is `agent_config.set_auto_update`'s reasoning and it
        is the one place here that raises.

        `ASK` forgets a rule rather than storing one: it is what the user means
        by "ask me about this again", and it is the only way back out of a rule
        that turned out to be wrong.
        """
        cleaned = normalise_pattern(pattern)
        if not cleaned:
            raise ValueError(
                "A command rule is a program name (`frobnicate`) or a program "
                "and a subcommand (`npm install`). %r is neither. Wildcards "
                "and patterns are not accepted: a rule nobody can read back "
                "exactly is a rule nobody can check." % (pattern,))
        if verdict not in VERDICTS:
            raise ValueError(
                "A command rule is %s, %s or %s (which forgets it); %r is "
                "none of those." % (ALLOW, DENY, ASK, verdict))
        if scope not in SCOPES:
            raise ValueError("A command rule is saved for the %r or the %r; "
                             "%r is neither." % (WORKSPACE, GLOBAL, scope))
        if verdict == ALLOW and _boundary_pattern(cleaned):
            # Refused at the point of saving as well as ignored at the point of
            # deciding. `decide` already guarantees this rule could never take
            # effect; what a stored `bash: allow` would do is tell whoever
            # reads the file that it had.
            raise ValueError(
                "`%s` cannot be allowed by a saved rule. It is refused by the "
                "boundary itself -- a shell, a privilege tool, a remote "
                "session, or a git operation TMT performs through its own "
                "action -- and no saved rule reaches that decision. Saving one "
                "would put a line in the rules file claiming a permission TMT "
                "does not honour." % (cleaned,))
        allow, deny = self._sets(scope)
        allow.discard(cleaned)
        deny.discard(cleaned)
        if verdict == ALLOW:
            allow.add(cleaned)
        elif verdict == DENY:
            deny.add(cleaned)
        if len(allow) + len(deny) > MAX_RULES:
            # The ceiling exists so the file cannot grow for the life of a
            # project. Refused rather than trimmed: dropping somebody's oldest
            # rule to make room for a new one changes what TMT will run without
            # anybody being told.
            allow.discard(cleaned)
            deny.discard(cleaned)
            raise ValueError(
                "There are already %d saved command rules for this %s, which "
                "is the limit. Remove one before adding another."
                % (MAX_RULES, scope))
        self._persist(scope)
        if verdict == ASK:
            return "Forgot the rule for `%s`; TMT will ask about it again." % cleaned
        return "Saved: `%s` is %sed in this %s from now on." % (cleaned, verdict,
                                                               scope)

    def forget(self, pattern, scope=WORKSPACE):
        """Drop a rule. `remember(pattern, ASK)` said the other way round."""
        return self.remember(pattern, ASK, scope)

    def _sets(self, scope):
        if scope == GLOBAL:
            return self._global_allow, self._global_deny
        return self._allow, self._deny

    def _persist(self, scope):
        """Write this scope's rules back into the file, keeping the rest.

        The file is RE-READ here rather than written from whatever was loaded
        when this object was made. Two workspaces share one file, a session can
        be minutes old, and the version being changed has to be the version on
        disk -- otherwise saving a rule in one project silently reverts a rule
        saved in another. That is `agent_context`'s rule about re-reading at
        the moment of the write, and it is the same failure it exists for.
        """
        data = _read_file()
        allow, deny = self._sets(scope)
        if scope == GLOBAL:
            data[GLOBAL] = {ALLOW: sorted(allow), DENY: sorted(deny)}
        else:
            workspaces = data.setdefault("workspaces", {})
            workspaces[self.key] = {"workspace": self.workspace,
                                    ALLOW: sorted(allow), DENY: sorted(deny)}
        _write_file(data)

    def __repr__(self):
        return "<Rules %d allow, %d deny (+%d, %d global)>" % (
            len(self._allow), len(self._deny), len(self._global_allow),
            len(self._global_deny))


def _clean_patterns(values):
    """Only what is really a pattern survives a load.

    One hand-edited line does not condemn the file -- `agent_memory._load`'s
    rule -- but a line that is not a readable pattern is dropped rather than
    stored, because the alternative is a rule in the file that matches nothing
    and reads as though it matches something.
    """
    if not isinstance(values, list):
        return []
    out = []
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = normalise_pattern(value)
        if cleaned:
            out.append(cleaned)
    return out


def _empty_file():
    return {"version": FORMAT_VERSION, GLOBAL: {ALLOW: [], DENY: []},
            "workspaces": {}}


def _read_file():
    """The whole rules file, or an empty one.

    Every failure lands here on purpose: absent, unreadable, not JSON, JSON of
    the wrong shape, a version this build does not know. None of them is worth
    a traceback, and the recovery for all of them is the same -- no rules,
    which is the state every first session is in.
    """
    try:
        raw = rules_path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _empty_file()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return _empty_file()
    if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
        return _empty_file()
    if not isinstance(data.get(GLOBAL), dict):
        data[GLOBAL] = {ALLOW: [], DENY: []}
    if not isinstance(data.get("workspaces"), dict):
        data["workspaces"] = {}
    return data


def _write_file(data):
    """Replace the rules file in one step.

    Written to a neighbour and renamed over the top, so an interrupted save
    leaves the previous rules intact rather than half a JSON document. A
    half-written security file that reads as "no rules" would be safe; one that
    reads as a truncated allow list would not, and neither is worth risking.
    """
    path = rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    os.replace(str(temporary), str(path))
