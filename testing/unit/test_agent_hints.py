"""The hints a refusal hands back to the model are written in the format in force.

A great many sentences that go BACK to the model end by showing it the action
that would have worked: "Run {"action":"review"}", "Emit {"action":"bash",
...}". They were written when JSON was the only way to answer TMT. Under the
tags format they are the wrong lesson -- a model told in its own refusal to
write braces and quotes is a model nudged back to the shape the owner asked
it not to use -- so every one of them is now rendered when the sentence is
BUILT, through `agent_protocol.example`, from the reply format in force then.

Three properties are protected here, and each one has a different way of
quietly regressing:

UNDER JSON NOTHING MOVED. The existing suite is pinned to json and a hundred
tests read these sentences. `OLD` below holds each one as it was before this
change, taken from HEAD by running the same producers against a clean export of
it, so "unchanged" is a comparison with the real old text and not with
whatever the new code happens to say. Byte for byte, newlines included.

UNDER TAGS THE EXAMPLE IS REAL. A tags sentence that merely LOOKED like tags
would teach a shape the parser refuses. So the example is cut back out of the
sentence the model was handed, parsed with `agent_protocol.parse`, compared
with the action it is meant to show, and put through `validate_action` -- and
the delegation example is handed to `agent_delegation.parse` as well, because
an example that its own refusal would refuse is a joke at the model's expense.

THE FORMAT IS READ WHEN THE SENTENCE IS BUILT. Several of these used to be
module-level constants. Every test below runs after the modules were imported
under json, so a sentence frozen at import could not turn into tags under
`ReplyFormat("tags")` -- and the sweep at the end reads the source, so a hint
added later as a raw JSON literal fails here rather than in front of a model.

What is deliberately NOT here: the reviewer's own result. A review ends in a
JSON verdict inside its `response` under either format (the owner's answer 5),
so `Each requirement must be an object such as {"text":...}` stays JSON, and a
test says so.
"""

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import agent_bash
import agent_config
import agent_delegation
import agent_file_ops
import agent_plan
import agent_prompt
import agent_protocol
import agent_review
import agent_reviewbot
import agent_shell
import agent_verify as V

from test_agent_reply_format import ReplyFormat

REPO = Path(agent_config.__file__).resolve().parent

MODULES = ("agent_bash", "agent_plan", "agent_review", "agent_verify",
           "agent_reviewbot", "agent_file_ops", "agent_delegation")

# The refusal of `&` as `agent_shell` has always said it under JSON, taken
# from the literal that was in the module before it was built at call time.
OLD_BACKGROUND = (
    "Background execution with & is not available in a command line. Use the "
    "bash tool's \"operation\": \"start\" instead, which registers the job so "
    "it can be watched with status and logs, and stopped."
)


# --- a workspace, because three of the sites read the disk ------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 20
PDF = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n"


class Workspace(object):
    """A throwaway workspace, with TMT's own state sent somewhere throwaway.

    `close()` stops every background job first and empties the registry by
    name -- `agent_bash._JOBS` is module state, there is deliberately no public
    way to forget a job, and a job left behind would change what the next test's
    `status` says.
    """

    def __init__(self):
        self.root = agent_config.ROOT_DIR
        self.install = agent_config.INSTALL_DIR
        self.path = Path(tempfile.mkdtemp(prefix="tmt_hints_")).resolve()
        self.inst = Path(tempfile.mkdtemp(prefix="tmt_hintsinst_")).resolve()
        agent_config.ROOT_DIR = self.path
        agent_config.INSTALL_DIR = self.inst
        (self.path / "pic.png").write_bytes(PNG)
        (self.path / "spec.pdf").write_bytes(PDF)
        (self.path / "notes.txt").write_bytes(b"a needle here\n")
        (self.path / "box").mkdir()
        (self.path / "box" / "inner.txt").write_bytes(b"inner\n")
        (self.path / "sleeper.py").write_bytes(b"import time\ntime.sleep(30)\n")

    def close(self):
        agent_bash.shutdown()
        del agent_bash._JOBS[:]
        agent_config.ROOT_DIR = self.root
        agent_config.INSTALL_DIR = self.install
        shutil.rmtree(str(self.path), ignore_errors=True)
        shutil.rmtree(str(self.inst), ignore_errors=True)


def refused(call, *args, **kwargs):
    """The sentence a ValueError carries -- PlanError and AgendaError both are."""
    try:
        call(*args, **kwargs)
    except ValueError as error:
        return str(error)
    raise AssertionError("%s did not refuse" % getattr(call, "__name__", call))


# --- one producer per sentence, through the PUBLIC API of its module --------
#
# Written against public names only (`bash`, `Plan.update`, `refusal`,
# `plan_veto`, `read_file`, ...) so the same function runs against HEAD, which
# is how OLD below was taken, and against the working tree.

def bash_no_command():
    return agent_bash.bash(operation="run")


def bash_job_limit():
    saved = agent_bash._capacity
    agent_bash._capacity = lambda: agent_bash.MAX_JOBS
    try:
        return agent_bash.bash(command="echo hi", operation="start")
    finally:
        agent_bash._capacity = saved


def bash_job_started():
    """A REAL job, started and stopped; the hint is the last line of the answer.

    The id is pinned to 41 so the sentence is the same on every run -- the
    counter is for the life of the process and would otherwise make this
    depend on how many jobs earlier tests started.
    """
    before = agent_bash._NEXT_ID[0]
    agent_bash._NEXT_ID[0] = 41
    try:
        started = agent_bash.bash(command="python sleeper.py",
                                  operation="start", timeout=30)
        agent_bash.bash(operation="stop", id="41")
    finally:
        agent_bash._NEXT_ID[0] = before
    return started.splitlines()[-1]


def bash_status_no_jobs():
    saved = list(agent_bash._JOBS)
    del agent_bash._JOBS[:]
    try:
        return agent_bash.bash(operation="status")
    finally:
        agent_bash._JOBS[:] = saved


def plan_find_without_a_plan():
    return refused(agent_plan.Plan().find, 1)


def plan_steps_not_a_list():
    return refused(agent_plan.Plan().create, "just text")


def plan_steps_empty():
    return refused(agent_plan.Plan().create, [])


def plan_updates_empty():
    return refused(agent_plan.Plan(["a"]).update, updates=[])


def plan_update_entry_not_an_object():
    return refused(agent_plan.Plan(["a"]).update, updates=["x"])


def plan_update_without_a_step():
    return refused(agent_plan.Plan(["a"]).update)


def plan_incomplete():
    return agent_plan.refusal(agent_plan.Plan(["Write it", "Test it"]),
                              "end_conversation")


FAIL = ('{"status":"FAIL","summary":"It does not work.","issues":[{"id":'
        '"R-001","severity":"CRITICAL","title":"Broken","description":'
        '"It is broken."}]}')
PASS = '{"status":"PASS","summary":"Read the diff.","issues":[]}'


def a_review():
    review = agent_review.ReviewState()
    review.note_change("write_file", ("a.py",))
    review.note_user_choice(True)
    return review


def a_review_plan():
    return agent_plan.Plan(["Implement it", "Test it", "Independent review"])


def review_required_action():
    return agent_review.parse_result(FAIL).required_action()


def review_refusal_idle():
    return agent_review.refusal(a_review(), a_review_plan(), "end_conversation")


def review_refusal_failed():
    review = a_review()
    review.begin()
    review.settle(agent_review.parse_result(FAIL))
    return agent_review.refusal(review, a_review_plan(), "end_conversation")


def review_refusal_error():
    review = a_review()
    review.begin()
    review.fail("the reviewer timed out")
    return agent_review.refusal(review, a_review_plan(), "end_conversation")


def review_refusal_stale():
    review = a_review()
    review.begin()
    review.settle(agent_review.parse_result(PASS))
    review.note_change("write_file", ("b.py",))
    return agent_review.refusal(review, a_review_plan(), "end_conversation")


def step_done():
    return {"action": "plan", "operation": "update", "step": 3,
            "status": "completed"}


def review_veto_not_run():
    return agent_review.plan_veto(a_review(), a_review_plan(), step_done())


def review_veto_failed():
    review = a_review()
    review.begin()
    review.settle(agent_review.parse_result(FAIL))
    return agent_review.plan_veto(review, a_review_plan(), step_done())


def a_verify():
    state = V.VerificationState()
    state.note_change("write_file", ("a.py",))
    state.note_user_choice(True)
    return state


def a_verify_plan():
    return agent_plan.Plan(["Implement it", "Test it", "Verify the result"])


def a_check(code, output):
    return V.VerificationCheck("tests", "Tests", V.TEST, V.LEVEL_FULL,
                               ("python", "-c", "pass")).record(code, output)


def verify_recommendation():
    result = V.VerificationResult(checks=[a_check(1, "2 failed")])
    return result.recommendations()[0]


def verify_refusal_not_run():
    return V.refusal(a_verify(), a_verify_plan(), "end_conversation")


def verify_refusal_failed():
    state = a_verify()
    state.begin()
    state.settle(V.VerificationResult(checks=[a_check(1, "2 failed")]))
    return V.refusal(state, a_verify_plan(), "end_conversation")


def verify_refusal_error():
    state = a_verify()
    state.begin()
    state.fail("it could not start")
    return V.refusal(state, a_verify_plan(), "end_conversation")


def verify_refusal_cancelled():
    state = a_verify()
    state.begin()
    state.cancel("interrupted")
    return V.refusal(state, a_verify_plan(), "end_conversation")


def verify_refusal_stale():
    state = a_verify()
    state.begin()
    state.settle(V.VerificationResult(checks=[a_check(0, "3 passed")]))
    state.note_change("write_file", ("b.py",))
    return V.refusal(state, a_verify_plan(), "end_conversation")


def verify_veto_not_run():
    return V.plan_veto(a_verify(), a_verify_plan(), step_done())


def verify_veto_failed():
    state = a_verify()
    state.begin()
    state.settle(V.VerificationResult(checks=[a_check(1, "2 failed")]))
    return V.plan_veto(state, a_verify_plan(), step_done())


def reviewbot_find_without_an_agenda():
    return refused(agent_reviewbot.Agenda().find, 1)


def reviewbot_update_without_an_item():
    return refused(agent_reviewbot.Agenda(["Read the diff"]).update)


def reviewbot_update_without_a_status():
    return refused(agent_reviewbot.Agenda(["Read the diff"]).update,
                   reference=1)


def file_ops_image():
    return agent_file_ops.read_file("pic.png")


def file_ops_document():
    return agent_file_ops.read_file("spec.pdf")


def file_ops_replace_preview():
    return agent_file_ops.replace_across("needle", "thread").splitlines()[-1]


def file_ops_delete_folder():
    return agent_file_ops.delete_folder("box")


def delegation_constraints():
    return agent_delegation.parse("nope")[1]


PRODUCERS = [
    (name, function) for name, function in sorted(globals().items())
    if callable(function) and getattr(function, "__module__", None) == __name__
    and name.split("_")[0] in ("bash", "plan", "review", "verify", "reviewbot",
                               "file", "delegation")
    and name not in ("step_done",)
    # `a_review`, `a_check`, `refused` and the rest are fixtures, not sites.
    and not name.startswith("a_")
]


# --- what each sentence said before this change, taken from HEAD -----------

OLD = {
    "bash_job_limit": (
        'FAILED: 4 background jobs are already running, which is the '
        'limit. Stop one with '
        '{"action":"bash","operation":"stop","id":"..."} before starting '
        'another. There is no queue: TMT has no scheduler to put one in, '
        'and a job that claimed to be waiting would be a claim about '
        'something that is not happening.'
    ),
    "bash_job_started": (
        'Read what it prints with '
        '{"action":"bash","operation":"logs","id":"41"} and stop it with '
        '{"action":"bash","operation":"stop","id":"41"}.'
    ),
    "bash_no_command": (
        'FAILED: the `run` operation needs a `command` -- the command '
        'line to run. Emit {"action":"bash","command":"python '
        'run_tests.py"}.'
    ),
    "bash_status_no_jobs": (
        'There are no background jobs. Start one with '
        '{"action":"bash","operation":"start","command":"..."}.'
    ),
    "delegation_constraints": (
        'FAILED: "constraints" must be an object, not \'nope\'. Example: '
        '{"read_only":true,"timeout_seconds":600,"report":{"summary":true}}'
    ),
    "file_ops_delete_folder": (
        'box is not empty (1 items). Retry with "recursive": true to '
        'delete everything inside.'
    ),
    "file_ops_document": (
        'spec.pdf is a PDF, not text. Use read_document to read it -- it '
        'converts the file to Markdown: '
        '{"action":"read_document","path":"spec.pdf"}'
    ),
    "file_ops_image": (
        'pic.png is a PNG image, not text. Use view_image to look at it: '
        '{"action":"view_image","path":"pic.png"}'
    ),
    "file_ops_replace_preview": (
        'Nothing on disk was touched. Re-run with "apply": true to make '
        'these changes.'
    ),
    "plan_find_without_a_plan": (
        'There is no plan yet. Create one first with '
        '{"action":"plan","operation":"create","steps":["..."]}.'
    ),
    "plan_incomplete": (
        'BLOCKED: you cannot finish yet. The plan you made is the '
        'contract for this task, and 2 steps are still outstanding:\n'
        '  S1: Write it [in_progress]\n'
        '  S2: Test it [pending]\n'
        'Do the work for the next step, then mark it completed with '
        '{"action":"plan","operation":"update","step":N,'
        '"status":"completed"}. '
        'If a step turned out not to be needed, say so in its title and '
        'complete it, or replace the plan with "create". Do not call '
        'end_conversation again until every step is completed.'
    ),
    "plan_steps_empty": (
        'A plan needs at least one step. To drop the plan instead, use '
        '{"action":"plan","operation":"clear"}.'
    ),
    "plan_steps_not_a_list": (
        '"steps" must be a list of step titles, such as ["Inspect the '
        'repository", "Run the tests"].'
    ),
    "plan_update_entry_not_an_object": (
        'Each update must be an object such as '
        '{"step":2,"status":"completed"}.'
    ),
    "plan_update_without_a_step": (
        'Say which step to update, as "step": 2 or "step": "S2".'
    ),
    "plan_updates_empty": (
        '"steps" for an update must be a non-empty list of '
        '{"step":N,"status":"..."} objects.'
    ),
    "review_refusal_error": (
        'BLOCKED: you cannot finish yet. The last review did not produce '
        'a usable result (the reviewer timed out), so nothing has '
        'actually been reviewed. A review that failed to run is not a '
        'review that passed.\n'
        'Run {"action":"review"} again.'
    ),
    "review_refusal_failed": (
        'BLOCKED: you cannot finish yet. Review #1 found 1 blocking '
        'issue(s):\n'
        '  R-001 CRITICAL: Broken\n'
        'Fix them, run verification again, then request another review '
        'with {"action":"review"}. A finding you believe is wrong is '
        'still yours to investigate and answer in the next review -- '
        'disagreeing with it does not clear it, and you cannot mark the '
        'review passed yourself.'
    ),
    "review_refusal_idle": (
        'BLOCKED: you cannot finish yet. This task changed 1 file(s) '
        'against a 3-step plan, so it needs an independent review before '
        'it can be called done, and no review has been run.\n'
        'Run one now with {"action":"review"}. It reads the diff, the '
        'plan and your request, and comes back with findings you must act '
        'on. Do not call end_conversation again until a review has '
        'passed.'
    ),
    "review_refusal_stale": (
        'BLOCKED: you cannot finish yet. Review #1 passed, but 1 file(s) '
        'have been changed since it ran (b.py), so what passed is not '
        'what you are about to report.\n'
        'Run verification again, then request another review with '
        '{"action":"review"}.'
    ),
    "review_required_action": (
        'Required action: fix R-001, run verification again, then request '
        'review again with {"action":"review"}. Do not answer until a '
        'review passes.'
    ),
    "review_veto_failed": (
        'FAILED: S3 (Independent review) is the review step and the '
        'review has not passed -- it is reporting 1 blocking issue(s). '
        'Fix the blocking findings, then run {"action":"review"} again. A '
        'review step cannot be completed by saying it is complete; run '
        '{"action":"review"} and let it report.'
    ),
    "review_veto_not_run": (
        'FAILED: S3 (Independent review) is the review step and the '
        'review has not passed -- it is not been run. Run '
        '{"action":"review"}. A review step cannot be completed by saying '
        'it is complete; run {"action":"review"} and let it report.'
    ),
    "reviewbot_find_without_an_agenda": (
        'There is no agenda yet. Declare one first with '
        '{"action":"review_agenda","operation":"create","items":["..."]}.'
    ),
    "reviewbot_update_without_a_status": (
        'Say what to move 1 to, as "status": one of: pending, active, '
        'done, skipped.'
    ),
    "reviewbot_update_without_an_item": (
        'Say which item to update, as "item": 2 or "item": "A2".'
    ),
    "verify_recommendation": (
        'Fix what Tests reported, then run {"action":"verify"} again.'
    ),
    "verify_refusal_cancelled": (
        'BLOCKED: you cannot finish yet. The last verification was '
        'cancelled (interrupted), so nothing was verified. Run '
        '{"action":"verify"} again.'
    ),
    "verify_refusal_error": (
        'BLOCKED: you cannot finish yet. Verification #0 could not '
        'complete (it could not start), so nothing has actually been '
        'verified. A verification that failed to run is not a '
        'verification that passed.\n'
        'Fix what stopped it and run {"action":"verify"} again.'
    ),
    "verify_refusal_failed": (
        'BLOCKED: you cannot finish yet. Verification #1 failed: 1 '
        'check(s) did not pass.\n'
        '  Tests: 2 failed\n'
        'Fix what they reported, then run {"action":"verify"} again. You '
        'cannot mark verification passed yourself -- the only thing that '
        'moves it is a command actually exiting zero.'
    ),
    "verify_refusal_not_run": (
        'BLOCKED: you cannot finish yet. This task changed 1 file(s) '
        'against a 3-step plan, so it must be verified before it can be '
        'called done, and no verification has been run.\n'
        'Run one now with {"action":"verify"}. It inspects this '
        'repository, works out which checks are worth running for what '
        'you changed, runs them, and reports what they said. Do not call '
        'end_conversation again until it passes.'
    ),
    "verify_refusal_stale": (
        'BLOCKED: you cannot finish yet. Verification #1 passed, but 1 '
        'file(s) have changed since it ran (b.py), so what passed is not '
        'what you are about to report.\n'
        'Run {"action":"verify"} again.'
    ),
    "verify_veto_failed": (
        'FAILED: S3 (Verify the result) is the verification step and '
        'verification has not passed -- it is reporting 1 failing '
        'check(s). Fix the failing checks, then run {"action":"verify"} '
        'again. A verification step cannot be completed by saying it is '
        'complete; run {"action":"verify"} and let it report.'
    ),
    "verify_veto_not_run": (
        'FAILED: S3 (Verify the result) is the verification step and '
        'verification has not passed -- it is not been run. Run '
        '{"action":"verify"}. A verification step cannot be completed by '
        'saying it is complete; run {"action":"verify"} and let it '
        'report.'
    ),
}


# --- what each sentence shows the model, as actions -------------------------

REVIEW = {"action": "review"}
VERIFY = {"action": "verify"}
UPDATE_ONE = {"action": "plan", "operation": "update",
              "steps": [{"step": 2, "status": "completed"}]}

# The whole actions a sentence shows, in the order it shows them, as the
# object the example must read back as. Everything that is not here is a hint
# at a KEY and is in KEYS below.
SHOWS = {
    "bash_job_limit": [{"action": "bash", "operation": "stop", "id": "..."}],
    "bash_job_started": [{"action": "bash", "operation": "logs", "id": "41"},
                         {"action": "bash", "operation": "stop", "id": "41"}],
    "bash_no_command": [{"action": "bash", "command": "python run_tests.py"}],
    "bash_status_no_jobs": [{"action": "bash", "operation": "start",
                             "command": "..."}],
    "delegation_constraints": [{
        "action": "spawn_agent", "task": "...",
        "constraints": {"read_only": True, "timeout_seconds": 600,
                        "report": {"summary": True}}}],
    "file_ops_delete_folder": [{"action": "delete_folder", "path": "box",
                                "recursive": True}],
    "file_ops_document": [{"action": "read_document", "path": "spec.pdf"}],
    "file_ops_image": [{"action": "view_image", "path": "pic.png"}],
    "plan_find_without_a_plan": [{"action": "plan", "operation": "create",
                                  "steps": ["..."]}],
    # `N` is a placeholder the model is meant to replace, and it reads back as
    # the text "N": the step key accepts text for the handler to refuse.
    "plan_incomplete": [{"action": "plan", "operation": "update", "step": "N",
                         "status": "completed"}],
    "plan_steps_empty": [{"action": "plan", "operation": "clear"}],
    "plan_steps_not_a_list": [{"action": "plan", "operation": "create",
                               "steps": ["Inspect the repository",
                                         "Run the tests"]}],
    "plan_update_entry_not_an_object": [UPDATE_ONE],
    "plan_updates_empty": [UPDATE_ONE],
    "review_refusal_error": [REVIEW],
    "review_refusal_failed": [REVIEW],
    "review_refusal_idle": [REVIEW],
    "review_refusal_stale": [REVIEW],
    "review_required_action": [REVIEW],
    "review_veto_failed": [REVIEW, REVIEW],
    "review_veto_not_run": [REVIEW, REVIEW],
    "reviewbot_find_without_an_agenda": [{
        "action": "review_agenda", "operation": "create", "items": ["..."]}],
    "verify_recommendation": [VERIFY],
    "verify_refusal_cancelled": [VERIFY],
    "verify_refusal_error": [VERIFY],
    "verify_refusal_failed": [VERIFY],
    "verify_refusal_not_run": [VERIFY],
    "verify_refusal_stale": [VERIFY],
    "verify_veto_failed": [VERIFY, VERIFY],
    "verify_veto_not_run": [VERIFY, VERIFY],
}

# The hints that name a KEY and not a whole action: the action they belong to
# (wrapped round each fragment to read it back) and the fragments, each with
# what it must read back as. `None` for the wrapper means the sentence names a
# key without showing a value, so there is nothing to read back.
KEYS = {
    "plan_update_without_a_step": ("plan", [
        ("/step/ 2 //step/", {"action": "plan", "step": 2}),
        ("/step/ S2 //step/", {"action": "plan", "step": "S2"})]),
    "reviewbot_update_without_an_item": ("review_agenda", [
        ("/item/ 2 //item/", {"action": "review_agenda", "item": 2}),
        ("/item/ A2 //item/", {"action": "review_agenda", "item": "A2"})]),
    "file_ops_replace_preview": ("replace_across", [
        ("/apply/ true //apply/", {"action": "replace_across", "apply": True})]),
    "reviewbot_update_without_a_status": (None, [("/status/", None)]),
}


def test_every_site_is_covered_by_exactly_one_table():
    """A sentence in no table is a site nobody checks. The producers, OLD, and
    the union of SHOWS and KEYS must name the same thing -- which is also what
    makes the table below a list of every site changed."""
    names = set(name for name, _ in PRODUCERS)
    assert names == set(OLD), (sorted(names ^ set(OLD)))
    assert names == set(SHOWS) | set(KEYS), sorted(names ^ (set(SHOWS) | set(KEYS)))
    assert not set(SHOWS) & set(KEYS), sorted(set(SHOWS) & set(KEYS))
    assert len(names) >= 34, len(names)


_CACHE = {}


def sentences(protocol):
    """Every producer's sentence under `protocol`, built once per run.

    Built once because one of them starts a real job, and because the same
    sentences are asked three different questions. Each run is made under
    `ReplyFormat`, which is the whole of how a test chooses a format.
    """
    if protocol not in _CACHE:
        box = Workspace()
        try:
            with ReplyFormat(protocol):
                assert agent_config.PROTOCOL == protocol
                _CACHE[protocol] = dict((name, function())
                                        for name, function in PRODUCERS)
        finally:
            box.close()
    return _CACHE[protocol]


# --- under json ---------------------------------------------------------

def test_every_hint_reads_exactly_as_it_did_before_under_json():
    """The first property. Quoted from HEAD in OLD, compared whole -- one
    changed space is a failure, because every one of these is pinned somewhere
    by a test that was written against the old words."""
    got = sentences("json")
    bad = ["%s\n  was: %r\n  now: %r" % (name, OLD[name], got[name])
           for name in sorted(OLD) if got[name] != OLD[name]]
    assert not bad, "\n".join(bad)


def test_the_raw_json_a_sentence_keeps_under_json_is_only_what_json_dumps_cannot_make():
    """Three sites keep JSON they WRITE by hand rather than render, and the
    reason is the same each time: byte-identity with what a model was told
    before. `"step":N` is not valid JSON, so `json.dumps` cannot say it; a path
    is put into the view_image hint unescaped, as it always was. They are the
    whole of the exceptions and each is named."""
    got = sentences("json")
    assert '"step":N,"status":"completed"}' in got["plan_incomplete"]
    assert '"step":N,"status":"..."}' in got["plan_updates_empty"]
    assert '{"action":"view_image","path":"pic.png"}' in got["file_ops_image"]


# --- under tags ---------------------------------------------------------

JSON_SHAPE = re.compile(r'\{"|"[A-Za-z_]+"\s*:')


def test_every_hint_is_written_as_tags_under_tags():
    """The second property, first half: the sentence carries the tag rendering
    of what it shows, and none of the JSON shapes -- no `{"`, and no quoted key
    followed by a colon -- survives to teach the model the other format."""
    got = sentences("tags")
    bad = []
    for name, text in sorted(got.items()):
        if text == OLD[name]:
            bad.append("%s: unchanged under tags: %r" % (name, text))
        if JSON_SHAPE.search(text):
            bad.append("%s: still carries a JSON shape: %r" % (name, text))
        for shown in SHOWS.get(name, ()):
            wanted = agent_protocol.example(shown, "tags")
            if wanted not in text:
                bad.append("%s: %r is not in %r" % (name, wanted, text))
        for fragment, _ in KEYS.get(name, (None, ()))[1]:
            if fragment not in text:
                bad.append("%s: %r is not in %r" % (name, fragment, text))
    assert not bad, "\n".join(bad)


def test_a_sentence_that_is_not_about_the_format_is_the_same_under_both():
    """Everything outside the example is left alone: strip the example from
    each pair and what is left is the same words. A sentence rewritten while
    it was being made protocol-aware would show up here."""
    json_text, tags_text = sentences("json"), sentences("tags")
    # These are written differently around the example on purpose: the JSON
    # sentence is about "steps" and "objects", the tags one about /steps/ and
    # /item/. Each is checked on its own above.
    reworded = {"plan_steps_not_a_list", "plan_updates_empty",
                "plan_update_entry_not_an_object",
                "reviewbot_update_without_a_status"}
    bad = []
    for name in sorted(json_text):
        if name in reworded:
            continue
        keep_json, keep_tags = json_text[name], tags_text[name]
        for shown in SHOWS.get(name, ()):
            keep_json = keep_json.replace(agent_protocol.example(shown, "json"),
                                          "<example>", 1)
            keep_tags = keep_tags.replace(agent_protocol.example(shown, "tags"),
                                          "<example>", 1)
        for fragment, _ in KEYS.get(name, (None, ()))[1]:
            keep_tags = keep_tags.replace(fragment, "<key>", 1)
        if name == "plan_incomplete":
            keep_json = keep_json.replace(
                '{"action":"plan","operation":"update","step":N,'
                '"status":"completed"}', "<example>", 1)
        if name == "delegation_constraints":
            keep_json = keep_json.replace(OLD[name].split("Example: ")[1],
                                          "<example>", 1)
        if name == "file_ops_delete_folder":
            keep_json = keep_json.replace('"recursive": true', "<example>", 1)
        for old, new in (('"step": 2', "<key>"), ('"step": "S2"', "<key>"),
                         ('"item": 2', "<key>"), ('"item": "A2"', "<key>"),
                         ('"apply": true', "<key>")):
            keep_json = keep_json.replace(old, new, 1)
        if keep_json != keep_tags:
            bad.append("%s\n  json: %r\n  tags: %r" % (name, keep_json, keep_tags))
    assert not bad, "\n".join(bad)


def examples_in(text, action):
    """Every `/action/ ... //action/` span in a sentence, as written."""
    return re.findall(r"/%s/.*?//%s/" % (action, action), text, re.S)


def test_the_example_in_every_tag_hint_reads_back_as_the_action_it_shows():
    """The second property, second half. The example is cut out of the
    sentence the model was handed -- not rebuilt from the object it was made
    from, which would prove only that the renderer agrees with itself -- read
    by the real parser, compared with the action it is meant to be, and put
    through `validate_action`, which is what the loop does to it next."""
    got = sentences("tags")
    bad = []
    for name, shown in sorted(SHOWS.items()):
        found = []
        for action in sorted(set(item["action"] for item in shown)):
            found.extend(examples_in(got[name], action))
        if len(found) != len(shown):
            bad.append("%s: %d example(s) shown, %d found in %r"
                       % (name, len(shown), len(found), got[name]))
            continue
        for want, text in zip(shown, found):
            try:
                read = agent_protocol.parse(text)
            except agent_protocol.ProtocolError as error:
                bad.append("%s: %r does not parse: %s" % (name, text, error))
                continue
            if read != want:
                bad.append("%s: %r reads back as %r, not %r"
                           % (name, text, read, want))
                continue
            problem = agent_prompt.validate_action(read)
            if problem is not None:
                bad.append("%s: %r fails validate_action: %s" % (name, text, problem))
    assert not bad, "\n".join(bad)


def test_the_key_fragments_in_tag_hints_read_back_as_the_key_they_name():
    """The hints that name a key, not an action, are read back inside the action
    they belong to: `/step/ 2 //step/` is meant to mean step 2 and `/apply/
    true //apply/` is meant to mean true, a bool and not the word."""
    got = sentences("tags")
    bad = []
    for name, (action, fragments) in sorted(KEYS.items()):
        for fragment, want in fragments:
            if want is None:
                continue
            read = agent_protocol.parse("/%s/ %s //%s/" % (action, fragment, action))
            if read != want:
                bad.append("%s: %r reads back as %r, not %r" % (name, fragment, read, want))
    assert not bad, "\n".join(bad)
    # The one that names /status/ without a value says what the values are.
    assert "one of: pending, active, done, skipped" in got["reviewbot_update_without_a_status"]


def test_the_delegation_example_is_a_contract_its_own_refusal_would_accept():
    """An example the refusal itself rejects would be the cruellest sentence in
    the program. The constraints in the tags example are read back and handed to
    the same `parse` that refused the model's own."""
    text = sentences("tags")["delegation_constraints"]
    read = agent_protocol.parse(examples_in(text, "spawn_agent")[0])
    constraints, error = agent_delegation.parse(read["constraints"])
    assert error == "", error
    assert constraints.read_only is True
    assert constraints.timeout_seconds == 600
    assert constraints.report.summary is True


def test_the_gates_still_refuse_under_tags():
    """Making the sentence protocol-aware must not touch what it is a sentence
    ABOUT. Each refusal is still non-empty and still names what is wrong; a
    gate whose message could not be built would, in `refusal`'s own words, let
    the answer through."""
    got = sentences("tags")
    for name in ("review_refusal_idle", "review_refusal_failed",
                 "review_refusal_error", "review_refusal_stale",
                 "verify_refusal_not_run", "verify_refusal_failed",
                 "verify_refusal_error", "verify_refusal_cancelled",
                 "verify_refusal_stale", "plan_incomplete"):
        assert got[name].startswith("BLOCKED: you cannot finish yet."), (name, got[name])
    for name in ("review_veto_not_run", "review_veto_failed",
                 "verify_veto_not_run", "verify_veto_failed"):
        assert got[name].startswith("FAILED: S3 ("), (name, got[name])


# --- read when called ---------------------------------------------------

def test_the_format_is_read_when_the_sentence_is_built_and_not_before():
    """One process, one import, the setting changed between two calls: the
    sentence changes with it. A hint frozen into a module-level constant at
    import would give the same answer both times."""
    for protocol, wanted in (("json", '{"action":"review"}'),
                             ("tags", "/review/ //review/"),
                             ("json", '{"action":"review"}')):
        with ReplyFormat(protocol):
            assert agent_review._run_review() == wanted, protocol
            assert V._run_verify() == wanted.replace("review", "verify"), protocol
            assert agent_bash._bash(operation="status") == (
                '{"action":"bash","operation":"status"}' if protocol == "json"
                else "/bash/ /operation/ status //operation/ //bash/"), protocol


def test_no_hint_is_built_at_import():
    """The structural half of the test above. A module-level statement that
    calls a hint builder runs once, at import, under whatever format was in
    force then -- so the seven modules may define builders and may not call
    one outside a function."""
    builders = {"_example", "_example_action", "_key", "_bash", "_run_review",
                "_run_verify", "_update_example", "_path_action", "_flag_hint",
                "_constraints_example", "_no_command", "_job_limit", "_protocol", "_hint"}
    bad = []
    for module in MODULES:
        tree = ast.parse((REPO / (module + ".py")).read_text(encoding="utf-8"))
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.ClassDef)):
                continue
            for node in ast.walk(statement):
                if isinstance(node, ast.Call):
                    function = node.func
                    called = (function.id if isinstance(function, ast.Name)
                              else getattr(function, "attr", ""))
                    if called in builders:
                        bad.append("%s.py:%d calls %s at import" % (module, node.lineno, called))
    assert not bad, "\n".join(bad)


def test_a_missing_protocol_module_costs_the_json_example_and_never_the_sentence():
    """`agent_protocol` is imported lazily and guarded, so an editable install
    whose frozen module list lacks it -- the failure `_run_tool` exists for --
    still refuses in words. The fallback is JSON, because JSON is the format
    every reply has always been readable in."""
    # The modules bind `agent_protocol.hint` when they are imported, so the
    # failure has to be present at THAT moment: a fresh interpreter whose
    # `agent_protocol` import raises, with the setting on tags.
    program = "\n".join((
        "import sys",
        "sys.modules['agent_protocol'] = None",
        "import agent_config",
        "agent_config.PROTOCOL = 'tags'",
        "import agent_review, agent_verify, agent_plan, agent_reviewbot",
        "import agent_bash, agent_delegation, agent_file_ops, agent_shell",
        "assert agent_review._run_review() == '{\"action\":\"review\"}'",
        "assert agent_verify._run_verify() == '{\"action\":\"verify\"}'",
        "assert agent_plan._hint({'action': 'plan'}) == '{\"action\":\"plan\"}'",
        "assert agent_reviewbot._hint({'action': 'x'}) == '{\"action\":\"x\"}'",
        "assert agent_bash._bash(command='x') == '{\"action\":\"bash\",\"command\":\"x\"}'",
        "assert agent_delegation._constraints_example().startswith('{\"read_only\":true')",
        "assert agent_file_ops._path_action('view_image', 'p.png') == "
        "'{\"action\":\"view_image\",\"path\":\"p.png\"}'",
        "assert agent_shell._background() == OLD_BACKGROUND",
    ))
    program = program.replace("OLD_BACKGROUND", repr(OLD_BACKGROUND))
    done = subprocess.run([sys.executable, "-c", program], cwd=str(REPO),
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


def _shell_refusal(command):
    try:
        agent_shell.parse(command)
    except agent_shell.ShellError as error:
        return str(error)
    raise AssertionError("%r was not refused" % command)


def test_the_refusal_of_background_execution_is_unchanged_under_json():
    """The `&` refusal in `agent_shell` was the one hint left as a literal. Under
    json it is that literal, byte for byte; it is read when it is said."""
    with ReplyFormat("json"):
        assert _shell_refusal("sleep 60 &") == OLD_BACKGROUND


def test_the_refusal_of_background_execution_names_start_as_tags_under_tags():
    with ReplyFormat("tags"):
        message = _shell_refusal("sleep 60 &")
    shown = agent_protocol.example({"action": "bash", "operation": "start"}, "tags")
    assert shown in message, message
    assert message != OLD_BACKGROUND
    assert not JSON_SHAPE.search(message), message
    read = agent_protocol.parse(examples_in(message, "bash")[0])
    assert read == {"action": "bash", "operation": "start"}, read


def test_there_is_one_shared_hint_and_no_module_keeps_a_private_example():
    """`agent_protocol.hint` is the one reading of the setting for a sentence.
    The private `_example` copies the first conversion needed are gone, and a
    new one is a def a sweep over every module finds."""
    assert callable(agent_protocol.hint)
    with ReplyFormat("json"):
        assert agent_protocol.hint({"action": "review"}) == '{"action":"review"}'
        assert agent_protocol.hint({"action": "x"}, as_json="as-is") == "as-is"
    with ReplyFormat("tags"):
        assert agent_protocol.hint({"action": "review"}) == "/review/ //review/"
        assert agent_protocol.hint({"action": "x"}, as_json="as-is") == "/x/ //x/"
    private = []
    for path in sorted(REPO.glob("agent_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_example":
                private.append("%s.py:%d" % (path.stem, node.lineno))
    assert not private, "a private _example is back: %s" % ", ".join(private)


# --- what is deliberately still JSON ----------------------------------------

def test_the_reviewers_own_result_hint_stays_json_under_tags():
    """A review ends in a JSON verdict inside the reviewer's `response`, under
    either format, so the sentence correcting a malformed requirement names JSON
    in both. Rendering it as tags would teach the reviewer a shape the parser
    of its verdict does not read."""
    bad = ('{"status":"PASS","summary":"s","issues":[],'
           '"requirements":[7]}')
    for protocol in ("json", "tags"):
        with ReplyFormat(protocol):
            message = refused(agent_review.parse_result, bad)
        assert '{"text":"...","status":"satisfied"}' in message, (protocol, message)


# --- the sweep ----------------------------------------------------------------

# Every string in the seven modules that is a JSON object or a JSON key written
# out by hand, and why it is allowed to be. A hint added later as a raw literal
# is not on this list, which is the point of the list.
RAW_JSON = {
    ("agent_plan", '"steps" for an update must be a non-empty list of '
                   '{"step":N,"status":"..."} objects.'):
        "the JSON side of a hint whose tags side is the next branch",
    ("agent_plan", 'Each update must be an object such as '
                   '{"step":2,"status":"completed"}.'):
        "the JSON side of a hint whose tags side is the next branch",
    ("agent_plan", '{"action":"plan","operation":"update","step":N,'
                   '"status":"completed"}'):
        "`as_json`: N is not valid JSON, so json.dumps cannot say what the "
        "sentence has always said",
    ("agent_review", 'Each requirement must be an object such as '
                     '{"text":"...","status":"satisfied"}; entry %d was %s.'):
        "the REVIEWER's own result, which is JSON under either format",
    ("agent_reviewbot", 'Say what to move %s to, as "status": one of: %s.'):
        "the JSON side of a hint whose tags side is the branch above it",
    ("agent_file_ops", '{"action":"'):
        "the by-hand JSON of `_path_action`, kept so a path goes in "
        "unescaped exactly as it always did",
    ("agent_file_ops", '","path":"'):
        "the same f-string, its second piece",
    ("agent_file_ops", '"recursive": true'):
        "the JSON side of the delete_folder hint",
}

HAND_WRITTEN_JSON = re.compile(r'\{"|"[A-Za-z_]+"\s*:')


def _docstring_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def test_no_raw_json_hint_is_left_in_the_seven_modules():
    """The sweep. Every string constant that is not a docstring is read, and a
    JSON object or a quoted key with a colon in one has to be on RAW_JSON by
    name. It reads the AST rather than grepping the text because the same
    sentence is spelled `{\\"action\\":...}` in one file and `{{"action":...}}`
    in an f-string in another, and a grep for one spelling misses the other.
    The list is checked both ways: an entry that no longer exists is a stale
    excuse, which is worse than none."""
    found = set()
    for module in MODULES:
        tree = ast.parse((REPO / (module + ".py")).read_text(encoding="utf-8"))
        docs = _docstring_ids(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docs
                    and HAND_WRITTEN_JSON.search(node.value)):
                found.add((module, node.value))
    unexplained = sorted(found - set(RAW_JSON))
    stale = sorted(set(RAW_JSON) - found)
    assert not unexplained, ("a raw JSON hint that is not on the list: %r"
                             % (unexplained,))
    assert not stale, "entries that no longer exist: %r" % (stale,)


def test_the_text_of_the_seven_modules_names_a_compact_json_action_only_where_listed():
    """The plain-text half of the sweep, for a reader who greps. A compact
    `{"action":"` in the source -- as opposed to the dict literal
    `{"action": "x"}` code is written with -- is a JSON string being typed out
    by hand, whichever way it is escaped or quoted."""
    pattern = re.compile(r'\{+\\?"action\\?":\\?"')
    hits = []
    for module in MODULES:
        for number, line in enumerate(
                (REPO / (module + ".py")).read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and not line.lstrip().startswith("#"):
                hits.append((module, line.strip()))
    modules = sorted(module for module, _ in hits)
    assert modules == ["agent_file_ops", "agent_plan"], hits
    assert any(line.startswith("as_json='{\"action\":\"plan\"") for _, line in hits), hits
    assert any("{{\"action\":\"{action}\"" in line for _, line in hits), hits
