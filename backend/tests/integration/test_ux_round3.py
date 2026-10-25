"""UX round 3 (UX_Feedback_v3.pdf) backend enforcement against disposable PostgreSQL.

AI-1: a teammate never re-sends a multiword line already delivered in the session —
the generated text is gated (``repeat_message``/``near_repeat``, validator
``teammate_output_v7``) and the deterministic fallback draw skips delivered lines;
the seat's earlier lines also reach the grounded prompt as no-repeat context.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import insert

from app.db import schema as s
from app.domain.manifests.store import ContentStore
from app.providers.base import ProviderErrorKind
from app.services import engines
from tests.integration.test_wp06_engines import (
    _drive_to_task,
    _events,
    _job,
    _jobs,
    _messages,
    _run,
    _set_active,
    _stage,
    _tick,
)
from tests.integration.test_wp08_workbench_api import _authed_client
from tests.integration.test_wp10a_generation import (
    _CaptureProvider,
    _FailProvider,
    _model_calls,
    _shown,
    _tick_provider,
)

from .conftest import MigratedDatabase

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def store() -> ContentStore:
    return ContentStore.load_default()


def _seed_line(db: MigratedDatabase, sid: uuid.UUID, actor: str, body: str) -> None:
    with db.runtime.begin() as conn:
        conn.execute(
            insert(s.message).values(
                session_id=sid,
                message_id=uuid.uuid4(),
                actor=actor,
                channel="team",
                body=body,
                status="delivered",
                accepted_seq=1,
                committed_seq=1,
            )
        )


def _reply_and_deliver(
    db: MigratedDatabase,
    store: ContentStore,
    sid: uuid.UUID,
    writer: uuid.UUID,
    provider: object,
    *,
    to: str = "JT",
) -> None:
    _run(
        db,
        store,
        sid,
        writer,
        "social_reply",
        {"body": "how are you doing?", "to": to},
        _stage(db, sid)[1],
    )
    _tick_provider(db, store, provider)
    deliver = _job(db, sid, "deliver")
    assert deliver is not None
    _set_active(db, sid, int(deliver["due_active_ms"]) + 50)
    _tick_provider(db, store, provider)


def test_generated_repeat_of_a_delivered_line_is_rejected(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "pe_demo_v3", "UXR3A", "b-uxr3a")
    sid, writer = result.session_id, result.writer_id
    earlier = "doing ok, bit buried in my crates"
    _seed_line(migrated, sid, "PM", earlier)  # the other seat's line counts too
    provider = _CaptureProvider("Doing OK -- bit buried in my crates.")
    _reply_and_deliver(migrated, store, sid, writer, provider)

    call = _model_calls(migrated, sid)[0]
    assert call["status"] == "validation_rejected"
    assert "repeat_message" in call["validation"]["violations"]
    assert call["validation"]["validator_version"] == "teammate_output_v8"
    shown = _shown(migrated, sid)[0]
    assert shown["fallback"] == "deterministic_script"
    assert shown["shown_text"].strip().lower() != earlier


def test_seat_history_reaches_the_grounded_prompt(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "pe_demo_v3", "UXR3B", "b-uxr3b")
    sid, writer = result.session_id, result.writer_id
    _seed_line(migrated, sid, "JT", "yeah stuck on one of mine")
    _seed_line(migrated, sid, "PM", "Sounds good, thanks for that.")
    provider = _CaptureProvider("ha, same here, slow going")
    _reply_and_deliver(migrated, store, sid, writer, provider)

    user = provider.requests[0].messages[1].content
    assert "- yeah stuck on one of mine" in user  # JT's own line
    # O-14: PM's line reaches the prompt via RECENT CHAT — the EARLIER block
    # still carries only the speaking seat's own lines (AI-1 no-repeat context).
    earlier = user.split("<<<EARLIER>>>")[1]
    assert "Sounds good, thanks for that." not in earlier
    assert "PM: Sounds good, thanks for that." in user
    assert _model_calls(migrated, sid)[0]["status"] == "succeeded"


def test_fallback_draw_never_repeats_a_delivered_multiword_line(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "pe_demo_v3", "UXR3C", "b-uxr3c")
    sid, writer = result.session_id, result.writer_id
    used = ["doing fine, bit swamped", "ok, deep in my own crates"]
    for line in used:
        _seed_line(migrated, sid, "JT", line)
    _reply_and_deliver(migrated, store, sid, writer, _FailProvider(ProviderErrorKind.UNAVAILABLE))
    shown = _shown(migrated, sid)[0]
    assert shown["fallback"] == "deterministic_script"
    assert shown["shown_text"] not in used
    # AI-3: JT renders the shared bank line in his lower-case voice.
    assert shown["shown_text"][:1].islower()


# ----- post-E22 audit remediations -------------------------------------------------------------


def test_concurrent_inflight_replies_cannot_deliver_the_same_line(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Post-E22 audit: two replies can validate identical text while both are still in
    flight — the generation-time gate only sees committed deliveries. The delivery-time
    recheck inside the session lock substitutes the second committer and flags it."""
    result = _drive_to_task(migrated, store, "pe_demo_v3", "UXR3D", "b-uxr3d")
    sid, writer = result.session_id, result.writer_id
    provider = _CaptureProvider("checking my notes on that now")
    _run(
        migrated,
        store,
        sid,
        writer,
        "social_reply",
        {"body": "how are you doing?", "to": "JT"},
        _stage(migrated, sid)[1],
    )
    _run(
        migrated,
        store,
        sid,
        writer,
        "social_reply",
        {"body": "what do you think?", "to": "PM"},
        _stage(migrated, sid)[1],
    )
    _tick_provider(migrated, store, provider)  # both generate jobs validate in flight
    delivers = [j for j in _jobs(migrated, sid) if j["kind"] == "deliver"]
    assert len(delivers) == 2
    _set_active(migrated, sid, max(int(j["due_active_ms"]) for j in delivers) + 50)
    _tick_provider(migrated, store, provider)

    bodies = [
        str(m["body"])
        for m in _messages(migrated, sid)
        if m["channel"] == "team" and m["actor"] in ("JT", "PM")
    ]
    # No two multiword teammate lines may be repeat-equivalent anywhere in the session.
    seen: list[tuple[str, ...]] = []
    for body in bodies:
        words = engines._line_words(body)
        if len(words) >= engines.REPEAT_MIN_WORDS:
            assert words not in seen, f"repeated teammate line delivered: {body!r}"
        seen.append(words)
    # The second delivery was substituted deterministically and carries the audit flag.
    delivered = _events(migrated, sid, "message_delivered")
    assert any(e["payload"].get("repeat_substituted") for e in delivered)


class _SeatKeyedProvider(_CaptureProvider):
    """Returns per-seat text keyed on the grounded system prompt's "You are {seat}"."""

    def __init__(self, by_seat: dict[str, str]) -> None:
        super().__init__("")
        self._by_seat = by_seat

    def generate(self, request: Any, *, timeout_s: float) -> Any:
        system = str(request.messages[0].content)
        for seat, text in self._by_seat.items():
            if f"You are {seat}," in system:
                self._text = text
                break
        return super().generate(request, timeout_s=timeout_s)


def test_concurrent_inflight_near_paraphrases_are_substituted(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """Lab walkthrough (2026-10-04): two seats generated convergent near-paraphrases —
    "send me a request and i will/i'll pass along what i have" — which read as one
    scripted reply. Both validate in flight before either delivers, so the commit-time
    recheck must substitute the second and flag it ``near`` (not ``exact``)."""
    result = _drive_to_task(migrated, store, "pe_demo_v3", "UXR3N", "b-uxr3n")
    sid, writer = result.session_id, result.writer_id
    provider = _SeatKeyedProvider(
        {
            "JT": "i'd have to check my notes. send me a request and i will pass along what i have",
            "PM": "i'd have to check. send me a request and i'll pass along what i have",
        }
    )
    _run(
        migrated,
        store,
        sid,
        writer,
        "social_reply",
        {"body": "how are you both doing?"},
        _stage(migrated, sid)[1],
    )
    _tick_provider(migrated, store, provider)  # both seats generate in flight
    delivers = [j for j in _jobs(migrated, sid) if j["kind"] == "deliver"]
    assert len(delivers) == 2
    _set_active(migrated, sid, max(int(j["due_active_ms"]) for j in delivers) + 50)
    _tick_provider(migrated, store, provider)

    bodies = [
        str(m["body"])
        for m in _messages(migrated, sid)
        if m["channel"] == "team" and m["actor"] in ("JT", "PM")
    ]
    # No two multiword teammate lines may be near-repeats anywhere in the session.
    delivered_lines: list[str] = []
    for body in bodies:
        assert not engines.is_near_repeat(body, delivered_lines), (
            f"near-duplicate teammate lines delivered: {body!r} ~ {delivered_lines!r}"
        )
        delivered_lines.append(body)
    # The second delivery was substituted deterministically, flagged as a near repeat.
    delivered = _events(migrated, sid, "message_delivered")
    assert any(
        e["payload"].get("repeat_substituted")
        and e["payload"].get("repeat_substituted_reason") == "near"
        for e in delivered
    )


def test_near_repeat_at_generation_time_validates_out(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """The generation-time ``near_repeat`` gate (teammate_output_v7): when the model's
    text near-paraphrases a line already committed before validation — e.g. a slower
    sibling delivered first — the call validates out and a deterministic line ships."""
    result = _drive_to_task(migrated, store, "pe_demo_v3", "UXR3G", "b-uxr3g")
    sid, writer = result.session_id, result.writer_id
    _seed_line(
        migrated,
        sid,
        "JT",
        "i'd have to check. send me a request and i'll pass along what i have",
    )
    provider = _SeatKeyedProvider(
        {"PM": "i'd have to check my notes. send me a request and i will pass along what i have"}
    )
    _reply_and_deliver(migrated, store, sid, writer, provider, to="PM")
    calls = _model_calls(migrated, sid)
    social = [c for c in calls if "near_repeat" in (c["validation"] or {}).get("violations", ())]
    assert social, "near-paraphrase generation was not gated"
    shown = _shown(migrated, sid)
    assert shown[-1]["fallback"] == "deterministic_script"
    assert "pass along" not in str(shown[-1]["shown_text"])


def test_deterministic_fallback_renders_pm_sentence_case(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    """AI-3: the shared bank is styled at draw time — PM renders normal capitalization
    where JT renders lower case, so deterministic-only replies are still seat-distinct."""
    result = _drive_to_task(migrated, store, "pe_demo_v3", "UXR3E", "b-uxr3e")
    sid, writer = result.session_id, result.writer_id
    _reply_and_deliver(
        migrated, store, sid, writer, _FailProvider(ProviderErrorKind.UNAVAILABLE), to="PM"
    )
    shown = _shown(migrated, sid)[0]
    assert shown["fallback"] == "deterministic_script"
    assert shown["shown_text"][:1].isupper()


# ----- FR-1: interaction-mode feature flag -----------------------------------------------------


@pytest.mark.parametrize("manifest_id", ["tp_demo_nl_v1", "pe_demo_nl_v1"])
def test_nl_only_refuses_structured_requests_server_side(
    migrated: MigratedDatabase, store: ContentStore, manifest_id: str
) -> None:
    pid = f"NL{manifest_id[:2].upper()}"
    result = _drive_to_task(migrated, store, manifest_id, pid, f"b-{pid.lower()}")
    sid, writer = result.session_id, result.writer_id
    team = _run(
        migrated,
        store,
        sid,
        writer,
        "teammate_request",
        {"crate": 3},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not team.accepted and team.reason == "channel_closed"
    crate_only = _run(
        migrated,
        store,
        sid,
        writer,
        "assistant_request",
        {"crate": 3},
        _stage(migrated, sid)[1],
        accept=False,
    )
    assert not crate_only.accepted and crate_only.reason == "structured_request_disabled"
    typed = _run(
        migrated,
        store,
        sid,
        writer,
        "assistant_request",
        {"body": "where was crate 3 last seen?"},
        _stage(migrated, sid)[1],
    )
    assert typed.accepted
    # Natural-language team chat still works.
    _run(
        migrated,
        store,
        sid,
        writer,
        "social_reply",
        {"body": "anyone have crate 3?"},
        _stage(migrated, sid)[1],
    )


@pytest.mark.parametrize(
    ("manifest_id", "structured"),
    [
        ("tp_demo_v4", True),
        ("tp_demo_nl_v1", False),
        ("pe_demo_v4", True),
        ("pe_demo_nl_v1", False),
    ],
)
def test_workbench_projects_a_neutral_capability_flag(
    migrated: MigratedDatabase,
    store: ContentStore,
    make_settings: Any,
    manifest_id: str,
    structured: bool,
) -> None:
    pid = f"WB{manifest_id[:2].upper()}{int(structured)}"
    result = _drive_to_task(migrated, store, manifest_id, pid, f"b-{pid.lower()}")
    with _authed_client(make_settings, migrated, result) as client:
        body = client.get("/workbench").json()
    assert body["structured_requests"] is structured
    assert ("structured_seeking" in body["channels"]) is structured
    # The flag carries no profile / arm vocabulary.
    text = str(body)
    assert "nl_only" not in text and "structured_and_nl" not in text
    assert "interaction_mode" not in text


def test_structured_profiles_still_accept_structured_requests(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "pe_demo_v4", "STRV4", "b-strv4")
    sid, writer = result.session_id, result.writer_id
    out = _run(
        migrated, store, sid, writer, "assistant_request", {"crate": 2}, _stage(migrated, sid)[1]
    )
    assert out.accepted


# ----- UX-5: the own time-pressure rating is required on round-3 TP profiles ------------------


def test_round3_tp_profile_refuses_the_pressure_skip(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v4", "SKIP4", "b-skip4")
    sid, writer = result.session_id, result.writer_id
    _set_active(migrated, sid, 60_000)
    _tick(migrated, store)
    assert _stage(migrated, sid)[0] == "checkin_collect"
    skip = _run(
        migrated, store, sid, writer, "pressure_skip", {}, _stage(migrated, sid)[1], accept=False
    )
    assert not skip.accepted and skip.reason == "skip_not_permitted"
    assert _stage(migrated, sid)[0] == "checkin_collect"
    _run(migrated, store, sid, writer, "pressure_response", {"value": 5}, _stage(migrated, sid)[1])
    assert _stage(migrated, sid)[0] == "checkin_wait"


def test_earlier_tp_profiles_keep_their_published_skip(
    migrated: MigratedDatabase, store: ContentStore
) -> None:
    result = _drive_to_task(migrated, store, "tp_demo_v3", "SKIP3", "b-skip3")
    sid, writer = result.session_id, result.writer_id
    _set_active(migrated, sid, 60_000)
    _tick(migrated, store)
    _run(migrated, store, sid, writer, "pressure_skip", {}, _stage(migrated, sid)[1])
    assert _stage(migrated, sid)[0] == "checkin_wait"
