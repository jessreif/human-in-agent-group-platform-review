"""INV-1202/1209: deterministic coding pipeline — expected-vs-actual unit tests.

Each INV-1209 trap gets a dedicated test plus the whole dev corpus is re-verified by
``test_coding_corpus.py``. These tests assert the full coded outcome (status, spans,
polarity, stance, novelty), not merely that code runs.
"""

from __future__ import annotations

import uuid

import pytest

from app.coding import CODER, Attachment, HistoryItem, Subject
from app.coding.subjects import session_subjects
from app.coding.types import EventType, HistoryScope
from app.measurement.rows import SessionRows

M = "The Marina"
B = "Blackwell Storage"


def subj(body: str, *, seq: int = 5, actor: str = "participant", **kw: object) -> Subject:
    return Subject(seq=seq, actor=actor, channel="team", body=body, **kw)  # type: ignore[arg-type]


def record(
    seq: int,
    rid: str,
    crate: int,
    loc: str,
    ev: EventType,
    *,
    actor: str = "JT",
    scope: HistoryScope = "broadcast",
) -> HistoryItem:
    return HistoryItem(
        seq=seq,
        actor=actor,
        kind="record",
        scope=scope,
        record_id=rid,
        crate=crate,
        location_id=loc,
        event_type=ev,
    )


def message(seq: int, actor: str, body: str, scope: HistoryScope = "broadcast") -> HistoryItem:
    return HistoryItem(seq=seq, actor=actor, kind="message", scope=scope, body=body)


def test_wrong_crate_does_not_match_location_lineage() -> None:
    history = (record(1, "r-8", 8, M, "arrival"),)
    result = CODER.code(subj("Crate 7 arrived at The Marina."), history)
    assert result.status == "coded"
    (a,) = result.assertions
    assert a.crate == 7 and a.location_id == M and a.event_type == "arrival"
    assert a.novelty == "first_introduction" and a.novel_to_recipient


def test_crate_8_arrival_never_matches_crate_3() -> None:
    history = (record(1, "r-3", 3, B, "arrival"),)
    result = CODER.code(subj("Crate 8 arrived at The Marina."), history)
    (a,) = result.assertions
    assert a.novelty == "first_introduction"


def test_negated_arrival_is_negated_not_supported() -> None:
    result = CODER.code(subj("Crate 3 did not arrive at The Marina."), ())
    (a,) = result.assertions
    assert a.assertion == "negated" and a.event_type == "arrival"
    assert a.novelty == "unresolved" and not a.novel_to_recipient
    evidence = [s for s in a.spans if s.kind == "evidence"]
    assert evidence and evidence[0].text == "did not arrive at The Marina"


def test_never_left_is_negated_departure() -> None:
    result = CODER.code(subj("Crate 8 never left Blackwell Storage."), ())
    (a,) = result.assertions
    assert a.event_type == "departure" and a.assertion == "negated"


def test_departure_is_not_arrival() -> None:
    history = (record(1, "r-3", 3, B, "arrival"),)
    result = CODER.code(subj("Crate 3 departed Blackwell Storage."), history)
    (a,) = result.assertions
    assert a.event_type == "departure" and a.assertion == "asserted"
    assert a.novelty == "first_introduction"


def test_transit_is_not_arrival() -> None:
    result = CODER.code(subj("Crate 4 is heading to The Marina."), ())
    (a,) = result.assertions
    assert a.event_type == "transit" and a.assertion == "asserted"


def test_stale_record_silent_supersession() -> None:
    history = (
        record(1, "r-3-arr", 3, B, "arrival"),
        record(2, "r-3-dep", 3, B, "departure"),
    )
    result = CODER.code(subj("Crate 3 is at Blackwell Storage."), history)
    (a,) = result.assertions
    assert a.event_type == "still_at" and a.novelty == "unresolved"
    assert not a.novel_to_recipient


def test_quoted_disagreement_keeps_attribution() -> None:
    history = (message(1, "JT", "Crate 8 departed Blackwell Storage."),)
    result = CODER.code(subj("JT says crate 8 is at The Marina, but that record is old."), history)
    (a,) = result.assertions
    assert a.attribution == "JT" and a.temporal_challenge
    assert a.stance == "contradict" and a.assertion == "uncertain"
    assert a.novelty == "unresolved"


def test_third_party_disagreement_is_uncertain_stance() -> None:
    result = CODER.code(subj("I think crate 8 is at The Marina, but JT disagrees."), ())
    (a,) = result.assertions
    assert a.attribution == "JT" and a.stance == "uncertain" and a.assertion == "hedged"


def test_repeated_lineage_across_channels_is_one() -> None:
    history = (
        record(1, "r-2", 2, "The Sawmill", "arrival", actor="JT"),
        message(2, "PM", "Crate 2 arrived at The Sawmill."),
    )
    result = CODER.code(subj("Crate 2 is at The Sawmill."), history)
    (a,) = result.assertions
    assert a.novelty == "other_source_repetition" and not a.novel_to_recipient
    assert a.lineage == "record:r-2"


def test_self_repetition_paraphrase() -> None:
    history = (message(1, "participant", "Crate 5 arrived at Blackwell Storage."),)
    result = CODER.code(subj("Crate 5 is at Blackwell Storage."), history)
    (a,) = result.assertions
    assert a.novelty == "self_repetition" and not a.novel_to_recipient


def test_participant_file_scope_is_novel_to_team() -> None:
    """Recipient-specific novelty: a record only in the participant's own file is a
    lineage repetition but still novel to team recipients."""
    history = (record(1, "r-8", 8, M, "arrival", scope="participant_file"),)
    result = CODER.code(subj("Crate 8 is at The Marina."), history)
    (a,) = result.assertions
    assert a.novelty == "other_source_repetition" and a.novel_to_recipient


def test_late_restatement_still_matches_lineage() -> None:
    """Late-generated/early-context: unrelated traffic between record and restatement
    must not break lineage resolution."""
    history = (
        record(1, "r-8", 8, M, "arrival"),
        message(2, "PM", "Send me whatever records you have."),
        message(4, "PM", "Thanks!"),
    )
    result = CODER.code(subj("Crate 8 arrived at The Marina."), history)
    (a,) = result.assertions
    assert a.novelty == "other_source_repetition" and not a.novel_to_recipient


def test_solicitation_specific_crate() -> None:
    result = CODER.code(subj("Anything useful for my crate 3?", context_crate=3), ())
    assert result.status == "coded"
    assert not result.assertions
    (q,) = result.questions
    assert q.crate == 3
    (so,) = result.solicitations
    assert so.crates == (3,)


def test_solicitation_general() -> None:
    result = CODER.code(subj("Send me whatever records you have."), ())
    (so,) = result.solicitations
    assert so.crates == () and not result.questions


def test_acknowledgment_is_not_evidence() -> None:
    result = CODER.code(subj("Thanks!"), ())
    assert result.status == "coded" and result.acknowledgment_only
    assert not result.assertions
    # the codes payload exists (coded != missing coverage) and is empty-valued
    assert result.codes is not None and result.codes["assertions"] == []


def test_ambiguous_referent_abstains() -> None:
    result = CODER.code(subj("What about the one near the dock?"), ())
    assert result.status == "abstained" and result.codes is None
    assert result.adjudication and result.adjudication["reasons"]


def test_conflicting_locations_abstain() -> None:
    result = CODER.code(subj("Crate 5 was at The Marina and also at Blackwell Storage."), ())
    assert result.status == "abstained"


def test_attachment_bypasses_text() -> None:
    subject = subj(
        "fyi",
        attachments=(
            Attachment(
                record_id="r-6", crate=6, location_id="Ridgeline Bunker", event_type="arrival"
            ),
        ),
    )
    result = CODER.code(subject, ())
    (a,) = result.assertions
    assert (a.crate, a.location_id, a.event_type) == (6, "Ridgeline Bunker", "arrival")
    assert a.novelty == "attachment"


def test_attachment_is_authoritative_over_contradicting_text() -> None:
    subject = subj(
        "Crate 7 is at The Marina.",
        attachments=(
            Attachment(
                record_id="r-6", crate=6, location_id="Ridgeline Bunker", event_type="arrival"
            ),
        ),
    )
    result = CODER.code(subject, ())
    (a,) = result.assertions
    assert a.crate == 6 and a.location_id == "Ridgeline Bunker"


def test_attachment_already_broadcast_is_repetition() -> None:
    history = (record(1, "r-6", 6, "Ridgeline Bunker", "arrival"),)
    subject = subj(
        "record attached",
        attachments=(
            Attachment(
                record_id="r-6", crate=6, location_id="Ridgeline Bunker", event_type="arrival"
            ),
        ),
    )
    result = CODER.code(subject, history)
    (a,) = result.assertions
    assert a.novelty == "other_source_repetition" and not a.novel_to_recipient


def test_inference_marker() -> None:
    result = CODER.code(subj("Crate 8 is at The Marina because the storm moved it."), ())
    (a,) = result.assertions
    assert a.inference and a.assertion == "asserted"


def test_uncertainty_marker() -> None:
    result = CODER.code(subj("Crate 8 might be at The Marina."), ())
    (a,) = result.assertions
    assert a.assertion == "uncertain"


def test_system_actor_is_not_coded() -> None:
    result = CODER.code(subj("system notice", actor="system"), ())
    assert result.status == "not_coded" and result.codes is None


def test_exact_spans_are_retained() -> None:
    body = "Crate 8 arrived at The Marina yesterday."
    result = CODER.code(subj(body), ())
    (a,) = result.assertions
    by_kind = {s.kind: s for s in a.spans}
    assert by_kind["crate"].text == "Crate 8"
    assert body[by_kind["crate"].start : by_kind["crate"].end] == "Crate 8"
    assert by_kind["location"].norm == "The Marina"
    assert by_kind["time"].text == "yesterday"
    assert by_kind["evidence"].text == "arrived at The Marina"


def test_off_crate_flag() -> None:
    result = CODER.code(subj("Crate 7 is at The Marina.", context_crate=3), ())
    assert result.assertions[0].off_crate


def test_single_message_cannot_establish_own_novelty() -> None:
    """The subject's own text is never part of its history."""
    result = CODER.code(subj("Crate 8 is at The Marina."), ())
    assert result.assertions[0].novelty == "first_introduction"


# ----- SessionRows adapter (authoritative fields only) -------------------------------------------


def _session_rows() -> SessionRows:
    session_id = uuid.uuid4()
    rec = {
        "session_id": session_id,
        "record_id": "r-8-marina",
        "crate": 8,
        "event_type": "arrival",
        "location_id": "The Marina",
        "stamp": "Today, 10:00 AM",
        "body": "Field team: Crate 8 arriving at The Marina.",
    }
    messages = [
        {
            "message_id": uuid.uuid4(),
            "actor": "JT",
            "channel": "team",
            "record_id": "r-8-marina",
            "crate": 8,
            "body": "Crate 8 arrived at The Marina.",
            "status": "delivered",
            "accepted_seq": 3,
            "committed_seq": 3,
        },
        {
            "message_id": uuid.uuid4(),
            "actor": "participant",
            "channel": "team",
            "record_id": None,
            "crate": None,
            "body": "Crate 8 is at The Marina.",
            "status": "delivered",
            "accepted_seq": 4,
            "committed_seq": 4,
        },
    ]
    return SessionRows(
        session_id=session_id,
        messages=tuple(messages),
        records=(rec,),
        shares=(
            {
                "sharer": "JT",
                "record_id": "r-8-marina",
                "recipients": ["participant", "PM"],
                "delivered_seq": 2,
            },
        ),
    )


def test_session_subjects_attachment_and_history() -> None:
    views = session_subjects(_session_rows())
    assert len(views) == 2
    first, second = views
    # message 1 carries record_id -> authenticated attachment
    assert first.subject.attachments and first.subject.attachments[0].crate == 8
    # message 2 sees the earlier share + message as history, never itself
    assert all(h.seq < 4 for h in second.history)
    assert any(h.kind == "record" and h.record_id == "r-8-marina" for h in second.history)

    r2 = CODER.code(second.subject, second.history)
    (a,) = r2.assertions
    assert a.novelty == "other_source_repetition" and not a.novel_to_recipient

    r1 = CODER.code(first.subject, first.history)
    # the share at seq 2 already broadcast the record, so the attachment is a
    # repetition of that lineage, not a fresh attachment novelty
    assert r1.assertions[0].novelty == "other_source_repetition"
    assert r1.assertions[0].location_id == "The Marina"


def test_subject_keys_are_session_scoped() -> None:
    views = session_subjects(_session_rows())
    for view in views:
        assert view.subject_key.startswith(f"{view.session_id}:msg:")


def test_llm_path_is_dormant() -> None:
    from app.coding.llm import ModelCoder, ModelCodingUnavailableError
    from app.domain.manifests.artifacts import CodingPromptArtifact
    from app.domain.manifests.store import ContentStore

    prompt = ContentStore.load_default().artifacts["coding.prompt@v1"].artifact
    assert isinstance(prompt, CodingPromptArtifact)
    assert prompt.body.dormant_until == "D05"
    with pytest.raises(ModelCodingUnavailableError):
        ModelCoder(
            object(),  # type: ignore[arg-type]
            prompt=prompt,
            model="m",
            model_digest="d",
            d05_approved=False,
        )
