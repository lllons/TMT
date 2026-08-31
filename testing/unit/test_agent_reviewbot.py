"""Tests for the reviewbot's agenda: the state a reviewer's declared checklist is.

Three things are being protected here, and the third is the one the feature
exists for.

The MACHINE is small and strict -- at most one item active, nothing back out
of `done` or `skipped`, positions that only ever move when `add` appends -- and
every rule is here as a test that fails when the rule is removed. `agent_plan`
is the model this was written against and the differences are deliberate, so
several tests below assert the difference rather than the similarity.

The WORDS are the other half. Every refusal is read by a model that has to
correct itself from it and has spent a step to find out, so a refusal that does
not name the range, the ceiling, the item or the way out is a refusal that
costs a second step. The tests assert the sentence, not just the exception.

The RECORD is what the agenda is for. What is drawn under the progress bar is
the reviewer's own statement of what it was going to check, ticked off as it
goes, and the two guards that make that a record rather than a decoration are
`create` being refused once anything has settled and nothing coming back out of
a settled item. Both are tested from every direction, including through
`apply_operation`, which is the only path the reviewer itself can take.

Pure state: nothing here opens a file, starts a thread, reaches a model or
reads stdin, and nothing here may block.
"""

import agent_reviewbot as reviewbot


def agenda_of(*titles):
    """An agenda of these items, freshly declared."""
    return reviewbot.Agenda(list(titles))


def statuses(agenda):
    return [item.status for item in agenda.items]


def ids(agenda):
    return [item.id for item in agenda.items]


def titles(agenda):
    return [item.title for item in agenda.items]


def refused(call, *args, **kwargs):
    """The sentence a refused agenda operation came back with."""
    try:
        call(*args, **kwargs)
    except reviewbot.AgendaError as error:
        return str(error)
    raise AssertionError("that was allowed and should not have been")


# --- what a status may be ---------------------------------------------------

def test_the_four_statuses_are_the_only_ones_and_a_refusal_names_them():
    """A model that guessed a status would have to spend a step finding out
    which ones exist. The refusal carries the whole set so it does not have
    to, and STATUSES is asserted as the source so a fifth one cannot be added
    without this test seeing it."""
    assert reviewbot.STATUSES == ("pending", "active", "done", "skipped")
    assert reviewbot.SETTLED == ("done", "skipped")
    said = refused(reviewbot.normalize_status, "nearly")
    for status in reviewbot.STATUSES:
        assert status in said, said
    assert "not an agenda status" in said, said


def test_a_status_survives_case_spacing_and_hyphens():
    """A reviewer writing "Done" or "in-progress" means the status it is
    obviously naming. Refusing the spelling would be a rule about typography
    charged at one round of the review."""
    for value in ("done", "Done", " DONE ", "  done  "):
        assert reviewbot.normalize_status(value) == reviewbot.DONE, value
    for value in ("in_progress", "in-progress", "In Progress", "IN-PROGRESS"):
        assert reviewbot.normalize_status(value) == reviewbot.ACTIVE, value
    assert reviewbot.normalize_status("SKIPPED") == reviewbot.SKIPPED
    assert reviewbot.normalize_status("Pending") == reviewbot.PENDING


def test_the_synonyms_the_module_declares_are_accepted():
    """These five are the spellings a model reaches for, and the module says so
    in its own comment. If the map is dropped, a reviewer reporting "completed"
    -- which is what the plan's vocabulary calls it -- is refused for using the
    neighbouring feature's word."""
    assert reviewbot.normalize_status("complete") == reviewbot.DONE
    assert reviewbot.normalize_status("completed") == reviewbot.DONE
    assert reviewbot.normalize_status("in_progress") == reviewbot.ACTIVE
    assert reviewbot.normalize_status("started") == reviewbot.ACTIVE
    assert reviewbot.normalize_status("skip") == reviewbot.SKIPPED


def test_an_unknown_status_is_never_mapped_onto_the_nearest_one():
    """The one judgement this module must never make for the reviewer. Guessing
    between `done` and `skipped` decides whether something was actually checked,
    which is the single fact the row on screen is claiming."""
    for value in ("finished", "checked", "failed", "nearly done", "", None, 0):
        said = refused(reviewbot.normalize_status, value)
        assert "not an agenda status" in said, (value, said)


# --- what an item may say ---------------------------------------------------

def test_a_title_is_collapsed_to_one_line():
    """The agenda is drawn one row per item in the strip under the bar. A title
    carrying its own line breaks would draw rows the layout never counted."""
    assert reviewbot.normalize_title("  spread\n over   lines  ") == "spread over lines"
    assert reviewbot.normalize_title("one\ttwo") == "one two"


def test_an_item_with_no_title_is_refused_and_says_what_a_title_is_for():
    """An untitled row is a tick beside nothing. The reviewer would be claiming
    coverage of a check the user cannot read the name of."""
    for value in ("", "   ", "\n\t ", None):
        said = refused(reviewbot.normalize_title, value)
        assert "needs a title" in said, (value, said)


def test_a_title_past_the_ceiling_is_elided_rather_than_cut_off():
    """The row is narrow because it shares the strip with a bar, a token figure
    and an elapsed time. A hard cut would look like a title that happened to end
    there; the ellipsis says the end is missing, and the result still fits the
    width the layout was measured for."""
    long_title = reviewbot.normalize_title("x" * (reviewbot.MAX_TITLE + 50))
    assert len(long_title) == reviewbot.MAX_TITLE, len(long_title)
    assert long_title[-1] == "…", repr(long_title[-1])
    # And a title that fits is left exactly as it is.
    exact = "y" * reviewbot.MAX_TITLE
    assert reviewbot.normalize_title(exact) == exact


def test_a_note_is_bounded_the_same_way():
    """A note is a clause on a row, not a finding -- findings go in the review
    result. Without the ceiling a reviewer could put a paragraph in the strip
    and push the bar off the screen."""
    long_note = reviewbot.normalize_note("y" * (reviewbot.MAX_NOTE + 200))
    assert len(long_note) == reviewbot.MAX_NOTE, len(long_note)
    assert long_note[-1] == "…", repr(long_note[-1])
    # Unlike a title, an absent note is not an error: most items have none.
    assert reviewbot.normalize_note("") == ""
    assert reviewbot.normalize_note(None) == ""
    assert reviewbot.normalize_note("  two   words  ") == "two words"


def test_an_item_is_labelled_by_its_position_and_is_settled_only_when_it_is_over():
    """`A3` is what the reviewer reads off the strip and types back, so the
    label has to be the position and nothing else. `settled` is what the bar and
    the create-guard both ask, and widening it to include `active` would count
    work in progress as work behind us."""
    item = reviewbot.AgendaItem(3, "Check the gate")
    assert item.id == "A3"
    assert item.status == reviewbot.PENDING and item.note == ""
    assert item.settled is False
    for status in (reviewbot.PENDING, reviewbot.ACTIVE):
        assert not reviewbot.AgendaItem(1, "x", status).settled, status
    assert reviewbot.AgendaItem(1, "x", reviewbot.DONE).settled
    # Skipped needs its reason even here, which is the point of enforcing the
    # rule at construction as well as on the transition: `create` and `add`
    # both mint items from a status, and a reason-less skipped row minted that
    # way is exactly the "check quietly dropped" the rule exists to stop.
    assert reviewbot.AgendaItem(1, "x", reviewbot.SKIPPED, "no test dir").settled
    assert item.as_dict() == {"id": "A3", "title": "Check the gate",
                              "status": "pending", "note": ""}


# --- create -----------------------------------------------------------------

def test_a_new_agenda_numbers_its_items_and_starts_on_the_first():
    """The strip is read the moment the agenda is declared. An agenda whose
    items were all pending would say the review has not started at the exact
    moment it is starting, and there would be no row for the activity label to
    correspond to."""
    agenda = agenda_of("Read the diff", "Check the gate", "Run the tests")
    assert ids(agenda) == ["A1", "A2", "A3"], ids(agenda)
    assert statuses(agenda) == ["active", "pending", "pending"], statuses(agenda)
    assert agenda.active().id == "A1"
    assert agenda.counts() == (0, 3)
    assert not agenda.is_complete()


def test_an_item_may_be_a_bare_title_or_an_object_carrying_status_and_note():
    """Both shapes are things a model emits, and the object form is what lets a
    reviewer declare an agenda that already reflects something it did on its
    first read. Refusing one of them would cost a round to learn a schema."""
    agenda = reviewbot.Agenda([
        "Plain string",
        {"title": "With a status", "status": "done"},
        {"title": "With a note", "note": "  worth   saying  "},
    ])
    assert titles(agenda) == ["Plain string", "With a status", "With a note"]
    assert statuses(agenda) == ["active", "done", "pending"], statuses(agenda)
    assert agenda.items[2].note == "worth saying"
    # The other two keys `_read_one` accepts, because a model writes both.
    assert reviewbot.Agenda([{"item": "from item"}]).items[0].title == "from item"
    assert reviewbot.Agenda([{"text": "from text"}]).items[0].title == "from text"


def test_an_agenda_with_no_items_is_refused_and_says_what_one_is():
    """There is no default agenda and no fallback list -- a reviewer that
    declares nothing gets no rows, which is honest. An empty create that quietly
    succeeded would leave a header saying `0 of 0` beside a running review."""
    said = refused(reviewbot.Agenda().create, [])
    assert "at least one item" in said, said
    assert "check" in said, said
    assert refused(reviewbot.Agenda().create, None)
    assert len(reviewbot.Agenda()) == 0


def test_an_agenda_longer_than_the_ceiling_is_refused_by_number():
    """The ceiling is a presentation limit -- six rows of the strip at a time --
    so the refusal has to name the number, or the reviewer retries with
    something else too long and spends another round finding out."""
    too_many = ["item %d" % n for n in range(reviewbot.MAX_ITEMS + 1)]
    said = refused(reviewbot.Agenda().create, too_many)
    assert str(reviewbot.MAX_ITEMS) in said, said
    assert str(reviewbot.MAX_ITEMS + 1) in said, said
    # Exactly the ceiling is fine; it is a maximum, not a limit one short of it.
    assert len(reviewbot.Agenda(too_many[:-1])) == reviewbot.MAX_ITEMS


def test_the_agenda_cannot_be_redeclared_once_an_item_is_done():
    """The load-bearing guard, and `Plan.clear`'s reason: a reviewer that has
    ticked an item and then declares a fresh agenda has erased its own statement
    of what it was going to check, and the row the user already read becomes a
    claim nobody can check. The refusal has to name what is on the record and
    point at the honest route, which is `add`."""
    agenda = agenda_of("Read the diff", "Check the gate")
    agenda.update(1, "done")
    said = refused(agenda.create, ["Something else entirely"])
    assert "cannot be replaced" in said, said
    assert "A1" in said, said
    assert "add" in said, said
    # And it is still standing, untouched.
    assert titles(agenda) == ["Read the diff", "Check the gate"]
    assert statuses(agenda) == ["done", "active"], statuses(agenda)


def test_the_agenda_cannot_be_redeclared_once_an_item_is_skipped():
    """The same guard from the other side, and it has to be tested separately:
    an implementation that keyed the guard on `done` alone would let a reviewer
    skip everything and then rewrite the list, which is the bypass with the
    smallest possible cost -- a skip needs only a sentence."""
    agenda = agenda_of("Read the diff", "Check the gate")
    agenda.update(1, "skipped", note="the file is binary")
    said = refused(agenda.create, ["Something else entirely"])
    assert "cannot be replaced" in said, said
    assert "A1" in said, said
    assert "add" in said, said
    assert statuses(agenda) == ["skipped", "active"], statuses(agenda)
    # Two settled items are both named, and the count agrees with the list.
    agenda.update(2, "done")
    said = refused(agenda.create, ["Something else"])
    assert "2 items" in said and "A1, A2" in said, said


def test_the_agenda_can_be_redeclared_while_nothing_has_been_reported():
    """The escape that keeps the guard from being a trap. Before anything
    settles this is a reviewer correcting a list nobody has acted on, and an
    `active` item is not a settled one -- treating it as one would freeze the
    agenda the instant it was declared."""
    agenda = agenda_of("First guess", "Second guess")
    assert statuses(agenda) == ["active", "pending"]
    said = agenda.create(["Better", "Much better", "Best"])
    assert "Agenda set" in said, said
    assert titles(agenda) == ["Better", "Much better", "Best"]
    assert statuses(agenda) == ["active", "pending", "pending"], statuses(agenda)


# --- finding the item the reviewer named ------------------------------------

def test_an_item_can_be_named_by_number_or_by_the_label_on_the_strip():
    """The strip says A2 and the reviewer is looking at the strip. Refusing the
    label it can see would be a rule about spelling, and the lower-case form is
    what a model writes half the time."""
    for reference in (2, "2", "A2", "a2", " A2 "):
        agenda = agenda_of("One", "Two")
        agenda.update(reference, "done")
        assert agenda.items[1].status == "done", reference


def test_a_position_that_does_not_exist_is_named_against_the_range_that_does():
    """The correction has to be makeable without another call, so the refusal
    states the range rather than only that the number was wrong."""
    said = refused(agenda_of("One", "Two", "Three").update, 9, "done")
    assert "no item 9" in said, said
    assert "A1 to A3" in said, said
    said = refused(agenda_of("Only one").update, 4, "done")
    assert "1 item," in said, said


def test_a_reference_that_is_not_a_number_says_what_one_looks_like():
    """A model that referred to an item by its title gets shown the two shapes
    that work, in the message, rather than being told only that this one did
    not."""
    said = refused(agenda_of("One", "Two").update, "the second one", "done")
    assert "not an agenda item" in said, said
    assert "A2" in said, said


def test_an_update_before_there_is_an_agenda_says_how_to_declare_one():
    """Reporting on an agenda that was never declared is the reviewer having
    lost its place. The answer that helps is the create call, not the
    observation that there is nothing there."""
    said = refused(reviewbot.Agenda().find, 1)
    assert "no agenda yet" in said, said
    assert "create" in said, said
    assert refused(reviewbot.Agenda().update, 1, "done")


# --- the transitions --------------------------------------------------------

def test_reporting_a_status_an_item_already_holds_is_reported_not_refused():
    """A reviewer that ticks A1 twice has told the truth twice. Refusing it
    would spend a round of the review on a correction with nothing to correct,
    and this is the deliberate difference from `Plan`, which refuses it."""
    agenda = agenda_of("One", "Two")
    agenda.update(1, "done")
    said = agenda.update(1, "done")
    assert "A1 was already done" in said, said
    assert statuses(agenda) == ["done", "active"], statuses(agenda)
    # A note offered alongside is still taken; it is the only new information.
    agenda.update(1, "done", note="and the tests cover it")
    assert agenda.items[0].note == "and the tests cover it"


def test_nothing_comes_back_out_of_done():
    """A check reported as finished is evidence the user has already read, so
    un-ticking it would make the row that was on screen a lie. An agenda whose
    shape turned out wrong is EXTENDED with `add`, which is visible, and the
    refusal points at where the extra words belong instead."""
    agenda = agenda_of("One", "Two")
    agenda.update(1, "done")
    for status in ("pending", "active", "skipped"):
        said = refused(agenda.update, 1, status)
        assert "already done" in said, (status, said)
        assert "cannot be changed back" in said, (status, said)
        assert "review result" in said, (status, said)
    assert agenda.items[0].status == "done"
    assert reviewbot._TRANSITIONS[reviewbot.DONE] == ()


def test_nothing_comes_back_out_of_skipped_either():
    """Separately, because `skipped` is the cheaper of the two to reach -- a
    sentence rather than a check -- so an implementation that only made `done`
    terminal would leave the whole record rewritable through it."""
    agenda = agenda_of("One", "Two")
    agenda.update(1, "skipped", note="no such file")
    for status in ("pending", "active", "done"):
        said = refused(agenda.update, 1, status)
        assert "already skipped" in said, (status, said)
        assert "cannot be changed back" in said, (status, said)
    assert agenda.items[0].status == "skipped"
    assert reviewbot._TRANSITIONS[reviewbot.SKIPPED] == ()


def test_the_item_being_checked_can_only_be_finished_or_set_aside():
    """Backing the active item out to pending would put the strip in a state
    with nothing running while a reviewer is plainly running, and there is no
    fact that transition could be reporting. The refusal names the two moves
    that exist."""
    agenda = agenda_of("One", "Two")
    said = refused(agenda.update, 1, "pending")
    assert "A1 is active" in said, said
    assert "can only become" in said, said
    assert "done" in said and "skipped" in said, said
    assert statuses(agenda) == ["active", "pending"], statuses(agenda)


def test_a_skip_with_no_reason_is_refused():
    """The one thing the agenda exists to make visible. A skip with no reason is
    indistinguishable on the row from a check that was quietly dropped, and the
    difference between those two is the whole value of the readout."""
    agenda = agenda_of("Read the diff", "Check the gate")
    said = refused(agenda.update, 1, "skipped")
    assert "cannot be skipped without a reason" in said, said
    assert "note" in said, said
    assert statuses(agenda) == ["active", "pending"], statuses(agenda)


def test_a_skip_with_a_reason_is_accepted_and_the_reason_is_kept():
    """The other half of the same rule: a reviewer that genuinely could not
    check something must be able to say so on the row, rather than having to
    choose between lying and leaving the agenda stuck for ever."""
    agenda = agenda_of("Read the diff", "Check the gate")
    said = agenda.update(1, "skipped", note="  the file is   binary  ")
    assert "A1 is skipped" in said, said
    assert agenda.items[0].note == "the file is binary"
    assert agenda.items[0].settled
    assert agenda.active().id == "A2", said


def test_an_item_that_already_carries_a_reason_may_be_skipped_without_a_new_one():
    """The reason may have been written when the agenda was declared, or on a
    previous update. Demanding it twice would refuse a reviewer that has already
    said exactly the thing the guard is asking for."""
    agenda = reviewbot.Agenda([{"title": "One", "note": "could not open it"},
                               "Two"])
    said = agenda.update(1, "skipped")
    assert "A1 is skipped" in said, said
    assert agenda.items[0].note == "could not open it"


def test_several_items_can_be_reported_in_one_call():
    """A reviewer that finished two checks in one step should not have to spend
    two steps saying so -- the readout is meant to cost the review as little as
    possible, or it stops being used."""
    agenda = agenda_of("One", "Two", "Three")
    said = agenda.update(updates=[{"item": 1, "status": "done"},
                                  {"item": "A2", "status": "skipped",
                                   "note": "covered by A1"}])
    assert "A1 is done" in said and "A2 is skipped" in said, said
    assert statuses(agenda) == ["done", "skipped", "active"], statuses(agenda)
    assert agenda.items[1].note == "covered by A1"
    assert agenda.counts() == (2, 3)


def test_a_malformed_updates_list_is_refused_with_a_sentence():
    """Every way of getting this wrong has to come back as words the reviewer
    can act on. An exception escaping here would reach `agent_worker` as a
    crash, and a review that died of its own progress report is the worst
    possible trade for a readout."""
    agenda = agenda_of("One", "Two")
    for bad in ({"item": 1, "status": "done"}, "A1", 3, ()):
        said = refused(agenda.update, updates=bad)
        assert "non-empty list" in said, (bad, said)
    said = refused(agenda.update, updates=["A1"])
    assert "must be an object" in said, said
    assert statuses(agenda) == ["active", "pending"], statuses(agenda)


def test_an_update_with_no_item_and_one_with_no_status_each_say_so():
    """Two different mistakes with two different corrections. One sentence for
    both would send half the reviewers that hit it to fix the wrong key."""
    agenda = agenda_of("One", "Two")
    said = refused(agenda.update)
    assert "which item to update" in said, said
    said = refused(agenda.update, 1)
    assert "what to move 1 to" in said, said
    for status in reviewbot.STATUSES:
        assert status in said, said


# --- a refused update leaves no trace of having happened --------------------

def test_a_batch_update_whose_later_entry_is_refused_moves_nothing_at_all():
    """The bug this pins was invisible and permanent. A one-pass update applies
    each entry as it resolves it, so a batch whose second entry names a row that
    does not exist has already moved the first -- and `apply_agenda` returns the
    refusal before it emits, so the strip goes on drawing the frame it had. When
    something eventually forces a repaint the reader sees a checklist with an
    item ticked and nothing in progress, which is the multi-minute blind spot
    this readout exists to remove, reintroduced by a partial write.

    It compounds with the module's own two guards: `done` is terminal, so the
    half-applied move cannot be undone, and `create` is refused for ever after
    because something has settled. One malformed entry would lock the agenda
    into a state the reviewer could neither correct nor redeclare."""
    agenda = agenda_of("One", "Two", "Three")
    before = statuses(agenda)
    said = refused(agenda.update,
                   updates=[{"item": 1, "status": "done"},
                            {"item": 9, "status": "done"}])
    assert "no item 9" in said, said
    assert statuses(agenda) == before, statuses(agenda)
    assert agenda.counts() == (0, 3), agenda.counts()
    # And the agenda is still redeclarable, which it would not be if the first
    # entry had landed.
    agenda.create(["Four", "Five"])
    assert ids(agenda) == ["A1", "A2"], ids(agenda)


def test_a_batch_refused_on_a_transition_rather_than_a_lookup_moves_nothing():
    """The same rule through the other refusal. `done` is terminal, so the
    second entry here is refused by `_TRANSITIONS` rather than by `find` -- a
    different code path, and one a fix that only pre-resolved the references
    would miss."""
    agenda = agenda_of("One", "Two", "Three")
    agenda.update(1, "done")
    said = refused(agenda.update,
                   updates=[{"item": 2, "status": "done"},
                            {"item": 1, "status": "pending"}])
    assert "A1" in said and "cannot be changed back" in said, said
    assert statuses(agenda) == ["done", "active", "pending"], statuses(agenda)


def test_a_batch_refused_for_a_missing_skip_reason_moves_nothing_either():
    """The third refusal `_check` can raise, through the same two passes."""
    agenda = agenda_of("One", "Two")
    said = refused(agenda.update,
                   updates=[{"item": 1, "status": "done"},
                            {"item": 2, "status": "skipped"}])
    assert "without a reason" in said, said
    assert statuses(agenda) == ["active", "pending"], statuses(agenda)


def test_two_entries_naming_the_same_item_are_checked_in_the_order_they_apply():
    """The reason the check reads a simulated status rather than the item's real
    one. Checking both entries against `pending` would let this pair through and
    then fail half way down pass two -- which is the partial write the two
    passes exist to prevent, arriving through the fix for it."""
    agenda = agenda_of("One", "Two")
    said = agenda.update(updates=[{"item": 2, "status": "active"},
                                  {"item": 2, "status": "done"}])
    assert agenda.items[1].status == "done", statuses(agenda)
    assert "A2 is active" in said and "A2 is done" in said, said
    # And an ordering that is genuinely impossible is still refused, with
    # nothing moved: done is terminal, so the second entry cannot run.
    agenda = agenda_of("One", "Two")
    said = refused(agenda.update, updates=[{"item": 2, "status": "done"},
                                           {"item": 2, "status": "active"}])
    assert "cannot be changed back" in said, said
    assert statuses(agenda) == ["active", "pending"], statuses(agenda)


# --- a skip always carries its reason, whichever verb minted it -------------

def test_create_cannot_mint_a_skipped_item_without_a_reason():
    """The guard lived only on the transition, so `create` and `add` walked
    straight past it. A reason-less skipped row is the "check quietly dropped"
    the rule exists to make impossible -- and because it is also a SETTLED row,
    one such call would trip `create`'s own guard and freeze the agenda for the
    rest of the review."""
    said = refused(reviewbot.Agenda,
                   [{"title": "One", "status": "skipped"}])
    assert "without a reason" in said, said
    # With a reason it is fine, and the reason is on the row.
    agenda = reviewbot.Agenda([{"title": "One", "status": "skipped",
                                "note": "no test directory here"}])
    assert agenda.items[0].note == "no test directory here"


def test_add_cannot_mint_a_skipped_item_without_a_reason_either():
    """The second of the two verbs that mint a status directly."""
    agenda = agenda_of("One")
    assert "without a reason" in refused(
        agenda.add, [{"title": "Two", "status": "skipped"}])
    assert "without a reason" in refused(agenda.add, "Two", status="skipped")
    assert len(agenda) == 1, ids(agenda)


# --- the invariants the strip depends on ------------------------------------

def test_finishing_the_active_item_promotes_the_next_pending_one():
    """What makes one call per item enough. Without it a reviewer would have to
    report both the finish and the start, and the strip would sit on a finished
    row for the whole of the next check if it forgot the second half."""
    agenda = agenda_of("One", "Two", "Three")
    agenda.update(1, "done")
    assert statuses(agenda) == ["done", "active", "pending"], statuses(agenda)
    assert agenda.active().id == "A2"
    agenda.update(2, "skipped", note="nothing to read")
    assert statuses(agenda) == ["done", "skipped", "active"], statuses(agenda)


def test_saying_an_item_is_active_when_it_already_is_is_reported_not_refused():
    """Both shapes of reporting have to work: the reviewer that relies on the
    promotion and the reviewer that spells out the start. Refusing the explicit
    one would punish the more careful of the two."""
    agenda = agenda_of("One", "Two")
    said = agenda.update(1, "active")
    assert "A1 was already active" in said, said
    assert statuses(agenda) == ["active", "pending"], statuses(agenda)


def test_at_most_one_item_is_ever_active_and_the_loser_goes_back_to_pending():
    """Two active rows is a readout of a process that cannot be in two places.
    The demotion is to PENDING and never to done: quietly completing the item
    that was running would mark a check finished that nobody did, in the one
    display whose entire purpose is saying what is actually happening."""
    agenda = reviewbot.Agenda([{"title": "One", "status": "active"},
                               {"title": "Two", "status": "active"},
                               {"title": "Three", "status": "active"}])
    # Three minted at once have nothing to tell them apart, so the first in
    # position order wins. That is `_settle`'s tie-break and it is only ever
    # reached from `create` and `add`.
    assert statuses(agenda) == ["active", "pending", "pending"], statuses(agenda)
    assert len([i for i in agenda.items if i.status == "active"]) == 1
    assert agenda.counts() == (0, 3), agenda.counts()


def test_an_update_naming_a_new_active_item_wins_over_the_one_already_running():
    """The opposite tie-break from the one above, and the difference is the
    whole point. An update is the reviewer's own statement about which row it
    is on; keeping whichever item happened to come first would silently
    discard that statement and return a result whose two halves contradict
    each other -- "A3 is active" over "Now on A1". It is the direction
    `Plan._move` already resolves the same conflict in."""
    agenda = agenda_of("One", "Two", "Three")
    assert statuses(agenda) == ["active", "pending", "pending"], statuses(agenda)
    said = agenda.update(3, "active")
    assert statuses(agenda) == ["pending", "pending", "active"], statuses(agenda)
    # The two halves of the result agree, which is what was actually wrong.
    assert "A3 is active" in said, said
    assert "Now on A3" in said, said
    # And the item that lost is pending, not completed: nobody did that work.
    assert agenda.items[0].status == "pending", statuses(agenda)


def test_an_agenda_with_every_item_settled_has_nothing_running():
    """`is_complete` is what the worker asks before it hands back its result,
    and `active()` is what the strip draws a marker beside. An agenda that was
    finished but still claimed an active row would draw a spinner on a review
    that had stopped."""
    agenda = agenda_of("One", "Two")
    said = agenda.update(updates=[{"item": 1, "status": "done"},
                                  {"item": 2, "status": "done"}])
    assert agenda.active() is None
    assert agenda.is_complete()
    assert agenda.outstanding() == ()
    assert len(agenda.settled()) == 2
    assert "Every item is reported" in said, said
    # An agenda nobody declared is not "complete"; there is nothing to be done.
    assert not reviewbot.Agenda().is_complete()


# --- add --------------------------------------------------------------------

def test_add_appends_and_never_moves_an_id_that_is_already_on_screen():
    """Positions are the identity, so an insert would renumber items the
    reviewer has already been shown and may already have ticked -- and its next
    update would land on a different row from the one it named."""
    agenda = agenda_of("One", "Two")
    agenda.update(1, "done")
    before = [(item.id, item.title, item.status) for item in agenda.items]
    said = agenda.add(["Three", "Four"])
    assert "Added 2 items" in said, said
    after = [(item.id, item.title, item.status) for item in agenda.items]
    assert after[:2] == before, (before, after)
    assert ids(agenda) == ["A1", "A2", "A3", "A4"]
    assert titles(agenda)[2:] == ["Three", "Four"]


def test_add_refuses_to_add_nothing():
    """An empty add that quietly succeeded would report "Added 0 items" onto
    the strip and leave the reviewer believing it had extended the agenda."""
    agenda = agenda_of("One")
    for nothing in ([], (), None):
        said = refused(agenda.add, nothing)
        assert "at least one item" in said, (nothing, said)
    assert len(agenda) == 1


def test_add_refuses_to_go_past_the_ceiling_and_names_the_resulting_count():
    """Naming the total rather than the overflow is what lets a reviewer work
    out how many it can still add without another call."""
    agenda = agenda_of("One", "Two")
    extra = ["item %d" % n for n in range(reviewbot.MAX_ITEMS)]
    said = refused(agenda.add, extra)
    assert str(reviewbot.MAX_ITEMS) in said, said
    assert str(len(agenda) + len(extra)) in said, said
    assert len(agenda) == 2, "the refused add appended anyway"
    # Filling it exactly is allowed.
    agenda.add(extra[:reviewbot.MAX_ITEMS - 2])
    assert len(agenda) == reviewbot.MAX_ITEMS


def test_add_takes_a_bare_string_as_well_as_a_list():
    """One extra check is the common case, and a model that wrote the bare
    string would otherwise get a refusal for a shape that is unambiguous."""
    agenda = agenda_of("One")
    said = agenda.add("Two")
    assert "Added 1 item" in said, said
    assert titles(agenda) == ["One", "Two"]
    # A single object is a single item too, not an iterable of its keys.
    agenda.add({"title": "Three", "note": "found while reading A1"})
    assert titles(agenda) == ["One", "Two", "Three"]
    assert agenda.items[2].note == "found while reading A1"


def test_add_starts_the_new_item_when_nothing_was_running():
    """The case that actually happens: a reviewer finishes its declared agenda,
    realises there is one more thing, and adds it. Without the promotion the
    strip would show a full agenda with nothing active while the review carried
    on working."""
    agenda = agenda_of("One")
    agenda.update(1, "done")
    assert agenda.active() is None and agenda.is_complete()
    agenda.add("Two")
    assert agenda.active().id == "A2", statuses(agenda)
    assert not agenda.is_complete()


# --- what the strip reads off it --------------------------------------------

def test_an_empty_agenda_reads_zero_and_not_a_hundred():
    """A full bar over an empty list is the readout at its most misleading: it
    says a review that has declared nothing has finished everything. Nothing is
    declared, so nothing is behind it, and the arithmetic has to say 0 rather
    than fall out of a division it never performed."""
    agenda = reviewbot.Agenda()
    assert agenda.progress() == 0
    assert agenda.progress() != 100
    assert agenda.counts() == (0, 0)


def test_a_skipped_item_counts_as_behind_the_bar():
    """The question the bar answers is "how much of the declared agenda is
    behind it", and an item the reviewer consciously set aside is behind it.
    Counting only `done` would leave a bar that could never fill on a review
    that had honestly reported every row."""
    agenda = agenda_of("One", "Two", "Three")
    assert agenda.progress() == 0
    agenda.update(1, "done")
    assert agenda.progress() == 33, agenda.progress()
    agenda.update(2, "skipped", note="not reachable from here")
    assert agenda.counts() == (2, 3)
    assert agenda.progress() == 67, agenda.progress()
    assert [item.id for item in agenda.settled()] == ["A1", "A2"]
    assert [item.id for item in agenda.outstanding()] == ["A3"]
    agenda.update(3, "done")
    assert agenda.progress() == 100


def test_describe_says_there_is_nothing_and_then_says_what_there_is():
    """This is the text `/review` prints and the text a tool result carries, so
    it has to survive with no colour at all. The count line is what makes it
    readable in a log where the rows have scrolled apart."""
    assert reviewbot.Agenda().describe() == "No review agenda."
    agenda = agenda_of("Read the diff", "Check the gate")
    agenda.update(1, "skipped", note="binary")
    text = agenda.describe()
    assert "A1: Read the diff [skipped] - binary" in text, text
    assert "A2: Check the gate [active]" in text, text
    assert "1 of 2 checked." in text, text
    assert text.count("\n") == 2, text


def test_an_empty_agenda_is_falsy_and_a_declared_one_is_not():
    """`__len__` is defined here, so without `__bool__` an empty agenda would be
    falsy in one place and a live object in another. A declared agenda nobody
    has worked on yet is very much something to draw."""
    assert not reviewbot.Agenda()
    assert len(reviewbot.Agenda()) == 0
    assert agenda_of("One")
    assert bool(agenda_of("One")) is True
    assert len(agenda_of("One", "Two")) == 2


def test_the_window_never_centres_on_a_row_that_does_not_exist():
    """The strip shows a few rows at a time and scrolls to this index. An index
    past the end is an IndexError inside a renderer thread, which is the worst
    place in the program to raise one."""
    agenda = agenda_of("One", "Two", "Three")
    assert agenda.active_index() == 0
    agenda.update(1, "done")
    assert agenda.active_index() == 1
    agenda.update(2, "done")
    agenda.update(3, "done")
    # Everything settled: the last row, which is the one that was finished last.
    assert agenda.active_index() == len(agenda) - 1
    assert 0 <= agenda.active_index() < len(agenda)
    # An agenda with no rows answers 0, which is why every caller checks the
    # agenda is truthy before it draws one.
    assert reviewbot.Agenda().active_index() == 0


# --- retire -----------------------------------------------------------------

def test_retiring_empties_the_agenda_in_place_and_returns_nothing():
    """In place, never rebound: the session hands this object out before the
    turn starts, so a fresh one at the boundary would leave the worker thread
    writing into state nothing draws. Returning None is what says it is the
    runtime's verb and not a report for the model."""
    agenda = agenda_of("One", "Two")
    assert agenda.retire() is None
    assert len(agenda) == 0 and not agenda
    assert agenda.items == ()
    assert agenda.describe() == "No review agenda."
    assert agenda.progress() == 0


def test_retiring_is_total_and_refuses_nothing_whatever_the_state():
    """The `Plan.retire` lesson, applied before it could be learned twice. The
    session retires this between turns, on a path that catches nothing, so a
    retirement that could raise would kill the next question -- and the state it
    would raise on is the normal ending of a successful review, not an edge
    case: an agenda every item of which has been reported is exactly the shape
    `create` refuses."""
    shapes = {
        "nothing declared": lambda: reviewbot.Agenda(),
        "just declared": lambda: agenda_of("One", "Two"),
        "one done": lambda: _reported(agenda_of("One", "Two"), 1),
        "one skipped": lambda: _skipped(agenda_of("One", "Two"), 1),
        "every item reported": lambda: _reported(agenda_of("One", "Two"), 1, 2),
        "settled by create": lambda: reviewbot.Agenda(
            [{"title": "One", "status": "done"}, "Two"]),
    }
    for name, build in shapes.items():
        agenda = build()
        assert agenda.retire() is None, name
        assert len(agenda) == 0 and not agenda, name
        # Retiring twice is not a special case either.
        assert agenda.retire() is None, name
        # And the agenda is usable again afterwards, which is what makes this
        # the runtime's way out rather than a way to destroy the object.
        assert "Agenda set" in agenda.create(["Fresh"]), name

    # The guard it must NOT have taken with it.
    said = refused(_reported(agenda_of("One", "Two"), 1).create, ["Other"])
    assert "cannot be replaced" in said, said


def _reported(agenda, *positions):
    """The same agenda with these items reported done."""
    agenda.update(updates=[{"item": n, "status": "done"} for n in positions])
    return agenda


def _skipped(agenda, position=1):
    """The same agenda with one item set aside, with its reason."""
    agenda.update(position, "skipped", note="no reason to check it")
    return agenda


def test_retiring_is_not_something_the_reviewer_can_ask_for():
    """A reviewer that could name this operation would have the bypass the
    create-guard exists to close: retire, then declare a fresh agenda, and the
    record of what it said it would check is gone with no trace on the strip."""
    assert "retire" not in reviewbot.OPERATIONS, reviewbot.OPERATIONS
    assert reviewbot.OPERATIONS == ("create", "update", "add", "show")
    agenda = _reported(agenda_of("One", "Two"), 1)
    said = refused(reviewbot.apply_operation, agenda,
                   {"operation": "retire"})
    assert "not an agenda operation" in said, said
    assert len(agenda) == 2 and agenda.items[0].status == "done"


# --- apply_operation, the only path the reviewer has ------------------------

def test_every_operation_routes_to_its_own_method():
    """The dispatcher is the whole of the reviewer's reach into this state, so
    an operation that is listed and not wired is an operation that silently does
    nothing to a readout the user is trusting."""
    agenda = reviewbot.Agenda()
    said = reviewbot.apply_operation(agenda, {"operation": "create",
                                              "items": ["One", "Two"]})
    assert "Agenda set" in said and titles(agenda) == ["One", "Two"]
    said = reviewbot.apply_operation(agenda, {"operation": "add",
                                              "items": ["Three"]})
    assert "Added 1 item" in said and len(agenda) == 3
    said = reviewbot.apply_operation(agenda, {"operation": "update",
                                              "item": 1, "status": "done"})
    assert "A1 is done" in said and agenda.items[0].status == "done"
    said = reviewbot.apply_operation(agenda, {"operation": "show"})
    assert said == agenda.describe(), said
    # Case and spacing on the operation name are forgiven too.
    assert reviewbot.apply_operation(agenda, {"operation": "  SHOW "}) == said


def test_an_operation_nobody_defined_names_the_ones_that_exist():
    """A reviewer inventing an operation has usually reached for the plan's
    vocabulary. Naming the four it has costs one line and saves it a round."""
    for bogus in ("remove", "clear", "delete", "plan"):
        said = refused(reviewbot.apply_operation, reviewbot.Agenda(),
                       {"operation": bogus})
        assert "not an agenda operation" in said, (bogus, said)
        for operation in reviewbot.OPERATIONS:
            assert operation in said, (bogus, said)


def test_an_operation_that_is_missing_or_empty_is_refused():
    """A `review_agenda` action with no operation is the reviewer half way
    through writing one. It must come back as a sentence rather than as a
    KeyError inside the worker loop."""
    for obj in ({}, {"operation": ""}, {"operation": "   "},
                {"items": ["One"]}, None):
        said = refused(reviewbot.apply_operation, reviewbot.Agenda(), obj)
        assert "not an agenda operation" in said, (obj, said)


def test_applying_an_operation_to_no_agenda_is_words_and_not_an_attribute_error():
    """This verb belongs to one reviewer's run. Any other agent that emits it
    reaches here with nothing to write to, and the answer has to be an
    AgendaError the loop already turns into a result -- an AttributeError would
    escape as a crash from an action that was only trying to report."""
    said = refused(reviewbot.apply_operation, None, {"operation": "show"})
    assert "no agenda to write to" in said, said
    for operation in reviewbot.OPERATIONS:
        assert refused(reviewbot.apply_operation, None,
                       {"operation": operation, "items": ["One"]})


def test_create_takes_items_and_falls_back_to_steps():
    """"steps" is the plan's key and a reviewer that has read the plan rules
    writes it. Accepting both costs nothing; refusing one costs a round of the
    review to learn which noun this feature uses."""
    agenda = reviewbot.Agenda()
    reviewbot.apply_operation(agenda, {"operation": "create",
                                       "steps": ["One", "Two"]})
    assert titles(agenda) == ["One", "Two"]
    # "items" is the documented key and wins when both are present.
    agenda = reviewbot.Agenda()
    reviewbot.apply_operation(agenda, {"operation": "create",
                                       "items": ["Real"], "steps": ["Other"]})
    assert titles(agenda) == ["Real"], titles(agenda)


def test_add_takes_items_and_falls_back_to_item():
    """Adding one thing and adding several are the same operation, and a model
    reaches for the singular key when it is adding one."""
    agenda = agenda_of("One")
    reviewbot.apply_operation(agenda, {"operation": "add", "item": "Two"})
    assert titles(agenda) == ["One", "Two"]
    reviewbot.apply_operation(agenda, {"operation": "add",
                                       "items": ["Three"], "item": "Ignored"})
    assert titles(agenda) == ["One", "Two", "Three"], titles(agenda)
    # The status the object carries reaches the new item.
    reviewbot.apply_operation(agenda, {"operation": "add", "item": "Four",
                                       "status": "done"})
    assert agenda.items[3].status == "done", statuses(agenda)


def test_update_takes_item_id_or_position():
    """Three keys for the same thing, because all three are what a model writes
    when it is looking at a row labelled A2. A refusal here would be a rule
    about which synonym the schema happened to pick."""
    for key in ("item", "id", "position"):
        agenda = agenda_of("One", "Two")
        reviewbot.apply_operation(agenda, {"operation": "update", key: 2,
                                           "status": "done"})
        assert agenda.items[1].status == "done", key
    # And the note rides along, which is what makes a skip reportable this way.
    agenda = agenda_of("One", "Two")
    reviewbot.apply_operation(agenda, {"operation": "update", "item": "A1",
                                       "status": "skipped",
                                       "note": "generated file"})
    assert agenda.items[0].note == "generated file"


def test_an_agenda_error_is_a_value_error():
    """`agent_worker` turns this into an ordinary result string, and any caller
    that only knows about bad arguments still catches it. Nothing in this module
    may end a session, and a bare Exception subclass would sail past every
    `except ValueError` in the loop that runs the reviewer."""
    assert issubclass(reviewbot.AgendaError, ValueError)
    try:
        reviewbot.Agenda().create([])
    except ValueError as error:
        assert isinstance(error, reviewbot.AgendaError)
    else:
        raise AssertionError("that was allowed and should not have been")


# --- the decisions, pinned as decisions -------------------------------------

def test_there_is_no_method_that_takes_a_name_and_a_value():
    """The reasoning `agent_review.settle` and `Capabilities` are both built on:
    a method that could be handed ("status", "done") is a method a later edit
    can wire model output into, and every status would then have a route into
    the record that never passed the transition table. The public surface is
    asserted whole so a setter cannot be added without this test seeing it."""
    surface = sorted(name for name in dir(reviewbot.Agenda)
                     if not name.startswith("_"))
    assert surface == ["active", "active_index", "add", "as_list", "counts",
                       "create", "describe", "find", "is_complete", "items",
                       "outstanding", "progress", "retire", "settled",
                       "update"], surface
    assert not hasattr(reviewbot.Agenda, "__setitem__")
    # `items` hands out a tuple, so a caller cannot splice the list itself.
    assert isinstance(agenda_of("One").items, tuple)


def test_a_position_is_the_only_identity_an_item_has():
    """A second numbering kept beside the display numbering is two numberings
    that drift, and the reviewer then updates a row it cannot see. There is no
    stored id to drift from: the label is computed from the position every time
    it is asked for, and it cannot be assigned."""
    agenda = agenda_of("One", "Two", "Three")
    for item in agenda.items:
        assert sorted(vars(item)) == ["note", "position", "status", "title"], vars(item)
        assert item.id == "A%d" % item.position
        assert item.as_dict()["id"] == item.id
    try:
        agenda.items[0].id = "A9"
    except AttributeError:
        pass
    else:
        raise AssertionError("the label is settable and can drift from the row")
    # And the only operation that can shift a position appends, so nothing an
    # existing item is called changes under the reviewer while it works.
    agenda.add("Four")
    assert ids(agenda) == ["A1", "A2", "A3", "A4"]


def test_done_is_terminal_on_every_route_the_reviewer_has():
    """Asserted through the dispatcher rather than through the methods, because
    the dispatcher is the whole of what the reviewer can reach: if any of the
    four operations can move a reported item, the strip is a draft rather than a
    record and the guard on `create` is decoration."""
    agenda = agenda_of("Read the diff", "Check the gate")
    reviewbot.apply_operation(agenda, {"operation": "update", "item": 1,
                                       "status": "done"})
    assert agenda.items[0].status == "done"
    assert refused(reviewbot.apply_operation, agenda,
                   {"operation": "create", "items": ["Fresh"]})
    for status in ("pending", "active", "skipped"):
        assert refused(reviewbot.apply_operation, agenda,
                       {"operation": "update", "item": 1, "status": status})
    reviewbot.apply_operation(agenda, {"operation": "add", "item": "A third"})
    reviewbot.apply_operation(agenda, {"operation": "show"})
    assert agenda.items[0].status == "done", statuses(agenda)
    assert agenda.items[0].title == "Read the diff"
    assert titles(agenda) == ["Read the diff", "Check the gate", "A third"]
