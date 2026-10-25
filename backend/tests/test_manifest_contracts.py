"""WP02 manifest contracts: INV-0201/0202/0203/0204/0206/0208/0209(bind)/0211(diff)."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from html import unescape
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.compat import compatibility_key
from app.domain.manifests import cli
from app.domain.manifests.binding import BindError, bind
from app.domain.manifests.canonical import content_hash
from app.domain.manifests.diff import (
    check_declared_diff,
    check_paired_arms,
    load_diff_record,
    profile_diff,
)
from app.domain.manifests.publication import PublicationError, publish
from app.domain.manifests.schema import ArtifactRef, Manifest
from app.domain.manifests.store import (
    ContentError,
    ContentStore,
    canonical_manifest_dump,
)
from app.timing.policy import resolve_policy
from tests.wp02 import (
    CONTENT,
    PROFILES,
    REPO,
    bound,
    copy_content,
    edit_json,
    fixture,
    profile_data,
    store,
)

QA_X10 = ArtifactRef(artifact_id="qa.accelerated_x10", version=1)


def test_all_profile_arm_combinations_bind() -> None:
    combos = []
    for manifest_id in PROFILES:
        for mode in ("preview", "qa"):
            b = bind(store(), manifest_id, mode=mode)
            assert not b.launch_eligible, manifest_id
            assert not b.analytic_eligible
            assert b.manifest.timing.active_budget_s == 360
            for arm in b.manifest.arms:
                policy = resolve_policy(b, arm)
                assert policy.budget_ms == 360_000
                if mode == "preview":
                    combos.append((manifest_id, arm))
    assert len(combos) == 2 * len(PROFILES)
    assert {a for _, a in combos} == {"high", "low", "eval", "control"}


def test_unknown_profile_or_arm_is_rejected() -> None:
    with pytest.raises(BindError, match="unknown manifest"):
        bound("tp_demo_v9")
    with pytest.raises(ValueError, match="no arm"):
        resolve_policy(bound("tp_demo_v1"), "eval")


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def change(data: dict[str, Any]) -> None:
        node: Any = data
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[int(part)] if isinstance(node, list) else node[part]
        node[parts[-1]] = value

    return change


DORMANT_OR_UNKNOWN = [
    ("timing.trajectory", "slow_down_after_checkin"),
    ("timing.delivery_failure_injection", "drop_10_percent"),
    ("timing.visibility", "staggered"),
    ("timing.delay_attribution", "system_busy_notice"),
    ("timing.contingent_responsiveness", "faster_after_share"),
    ("timing.availability_windows", "scheduled_absence"),
    ("timing.proactive_sharing", "scripted_volunteering"),
    ("timing.ordering", "first_ready"),
    ("timing.deadline_structure", "per_crate"),
    ("timing.early_submission", True),
    ("timing.rate_limits.experimental_request_budget", 3),
    ("timing.two_stage.acknowledgment_counts_as_fulfillment", True),
    ("timing.response_classes.social_reply.rendering", "streamed"),
    ("timing.response_classes.social_reply.presence_cue", "seen_receipt"),
    ("timing.response_classes.assistant.delay.0.family", "gamma"),
    ("teammates.assistant_referral", "named_seat"),
    ("teammates.unsolicited_requests", "allowed"),
    ("timing.active_budget_s", 480),
    ("condition_set.0.arm", "medium"),
    ("timing.jitter_ms", 250),
    ("surprise_switch", True),
    ("timing.response_classes.structured_record.speed_multiplier", 2),
]


@pytest.mark.parametrize(("path", "value"), DORMANT_OR_UNKNOWN)
def test_unknown_and_dormant_fields_are_rejected(path: str, value: Any) -> None:
    data = copy.deepcopy(profile_data("tp_review_first_v2"))
    Manifest.model_validate_json(json.dumps(data))  # baseline is valid
    _set(path, value)(data)
    with pytest.raises(ValidationError):
        Manifest.model_validate_json(json.dumps(data))


def test_string_numbers_are_not_coerced() -> None:
    data = profile_data("tp_demo_v1")
    data["timing"]["checkin"]["at_active_s"] = "60"
    with pytest.raises(ValidationError):
        Manifest.model_validate_json(json.dumps(data))


def test_demo_mechanisms_rejected_outside_reference_profiles() -> None:
    data = profile_data("tp_review_first_v2")
    reference = profile_data("tp_demo_v1")
    data["timing"]["response_classes"]["social_reply"] = reference["timing"]["response_classes"][
        "social_reply"
    ]
    with pytest.raises(ValidationError, match="reference-only"):
        Manifest.model_validate_json(json.dumps(data))


def test_scripted_request_in_closed_phase_rejected() -> None:
    data = profile_data("tp_review_first_v2")
    data["timing"]["scripted_requests"]["times_active_s"] = [40, 150, 240]
    with pytest.raises(ValidationError, match="sharing is closed"):
        Manifest.model_validate_json(json.dumps(data))


def test_live_binding_refused_for_unapproved_profiles() -> None:
    for manifest_id in PROFILES:
        with pytest.raises(BindError, match="not allowed"):
            bound(manifest_id, mode="live")
    data = profile_data("tp_review_first_v2")
    data["modes_allowed"] = ["preview", "qa", "live"]
    with pytest.raises(ValidationError, match="live mode needs an approved proposed_pilot profile"):
        Manifest.model_validate_json(json.dumps(data))
    data["material_status"] = "approved"
    with pytest.raises(ValidationError, match="every linked decision resolved"):
        Manifest.model_validate_json(json.dumps(data))


def test_warning_is_mandatory_in_every_profile() -> None:
    data = profile_data("pe_pilot_v2")
    data["timing"]["warnings"] = [{"artifact_id": "warning.other", "version": 1}]
    with pytest.raises(ValidationError, match=r"warning\.one_minute_left"):
        Manifest.model_validate_json(json.dumps(data))
    # BA-99: the contract pins the warning artifact identity, not a frozen version —
    # the approved v2 successor (verbatim wording, same trigger) satisfies it.
    data["timing"]["warnings"] = [{"artifact_id": "warning.one_minute_left", "version": 2}]
    Manifest.model_validate_json(json.dumps(data))


def test_canonical_hash_is_default_materialized() -> None:
    data = profile_data("tp_demo_v1")
    explicit = copy.deepcopy(data)
    explicit["timing"]["response_classes"]["structured_record"]["seat_overrides"] = {}
    explicit["timing"]["response_classes"]["structured_record"]["think"] = None
    a = Manifest.model_validate_json(json.dumps(data))
    b = Manifest.model_validate_json(json.dumps(explicit, indent=4, sort_keys=True))
    assert content_hash(canonical_manifest_dump(a)) == content_hash(canonical_manifest_dump(b))
    assert store().manifests["tp_demo_v1"].sha256 == content_hash(canonical_manifest_dump(a))


def test_published_manifest_edit_requires_new_version(tmp_path: Any) -> None:
    root = copy_content(tmp_path)
    edit_json(root / "profiles" / "tp_demo_v1.json", _set("title", "edited in place"))
    edited = ContentStore(root)
    with pytest.raises(BindError, match="changed after publication"):
        bind(edited, "tp_demo_v1", mode="preview")
    with pytest.raises(PublicationError, match="edited after publication"):
        publish(edited, published_on="2026-09-29")


def test_artifact_edit_after_publication_fails_bind(tmp_path: Any) -> None:
    root = copy_content(tmp_path)
    path = root / "scripts" / "warning.one_minute_left.v1.json"
    edit_json(path, _set("body.text", "Sixty seconds left."))
    edited = ContentStore(root)
    for manifest_id in PROFILES:
        with pytest.raises(BindError, match="referenced artifact changed"):
            bind(edited, manifest_id, mode="preview")


def test_new_version_publishes_without_touching_existing_entries(tmp_path: Any) -> None:
    root = copy_content(tmp_path)
    data = profile_data("tp_demo_v1")
    data["manifest_id"], data["manifest_version"] = "tp_demo_v9", 9
    (root / "profiles" / "tp_demo_v9.json").write_text(json.dumps(data), encoding="utf-8")
    scratch = ContentStore(root)
    index, added = publish(scratch, published_on="2026-09-29")
    assert added == ["tp_demo_v9"]
    for manifest_id in PROFILES:
        assert index.manifests[manifest_id] == store().published.manifests[manifest_id]


def test_consent_v3_binds_verbatim_approved_copy(tmp_path: Any) -> None:
    """BA-37: the consent material version every current profile binds carries the literal
    Appendix B text — including the protocol number ('#XXXX-XXXX' with the hyphen the v2
    transcription dropped) and the skip/payment and early-leave sentences — and the
    served projection is that body verbatim."""
    from app.domain.manifests.artifacts import MaterialReferenceArtifact

    current = ("tp_demo_v3", "pe_demo_v3", "tp_review_first_v4", "pe_pilot_v4")
    for manifest_id in current:
        b = bound(manifest_id)
        stored = b.artifacts["instruments.consent"]
        assert stored.key == "consent.reference@v3", manifest_id
        art = stored.artifact
        assert isinstance(art, MaterialReferenceArtifact)
        text = art.body.text
        assert text is not None, "bound consent version must carry literal copy"
        flat = " ".join(p for s in text.sections for p in s.paragraphs)
        assert "#XXXX-XXXX" in flat, manifest_id
        assert (
            "You may skip questions during the study and doing so will not affect your "
            "payment" in flat
        )
        assert "If you do leave the study early, you will not be paid." in flat
        # Body-to-key equivalence: the projection serializes exactly the bound body.
        assert art.body.model_dump(mode="json")["text"]["question"] == text.question

    # Every published lineage carries the version its profile binds — v1 pointer-only
    # and v2 (superseded transcription) ancestors stay pinned for reproduction.
    for manifest_id, key in (
        ("tp_demo_v1", "consent.reference@v1"),
        ("tp_demo_v2", "consent.reference@v2"),
    ):
        assert bound(manifest_id).artifacts["instruments.consent"].key == key
    b = bound("tp_demo_v1")
    assert b.artifacts["instruments.consent"].artifact.body.text is None

    # Editing the published consent copy without a version bump breaks binding — the
    # recorded consent ``text_version`` can never drift from the displayed copy.
    root = copy_content(tmp_path)
    path = root / "instruments" / "consent.reference.v3.json"
    edit_json(
        path,
        _set("body.text.sections.0.title", "Changed in place"),
    )
    edited = ContentStore(root)
    with pytest.raises(BindError, match="referenced artifact changed"):
        bind(edited, "pe_demo_v3", mode="preview")


def test_launch_manifests_require_every_item() -> None:
    """2026-10-08 (owner direction): the fielded launch pair binds consent v5 —
    the skip promise is gone — and every measurement item plus the check-in own
    rating is required."""
    for manifest_id in ("tp_ai_v2", "tp_scripted_v4", "tp_ai_v3", "tp_scripted_v5"):
        b = bind(store(), manifest_id, mode="live")
        m = b.manifest
        assert b.launch_eligible
        assert m.interaction.mode == "nl_only", manifest_id
        assert "structured_seeking" not in m.channels.phases[0].enabled
        assert m.timing.checkin is not None
        assert not m.timing.checkin.own_rating_skippable, manifest_id
        consent = b.artifacts["instruments.consent"]
        assert consent.key == "consent.reference@v5", manifest_id
        text = consent.artifact.body.text
        assert text is not None
        flat = " ".join(p for s in text.sections for p in s.paragraphs)
        assert "may skip questions" not in flat
        assert "If you do leave the study early, you will not be paid." in flat
        for slot in ("instruments.confidence", "instruments.post_task"):
            items = b.artifacts[slot].artifact.body.items
            assert items, slot
            assert all(not i.skippable for i in items), slot
    ai = bound("tp_ai_v2").manifest
    sc = bound("tp_scripted_v4").manifest
    assert ai.teammates.social_generation == "model_with_validated_fallback"
    assert sc.teammates.social_generation == "deterministic_only"


def test_risk_successors_only_change_the_request_script_binding() -> None:
    from app.domain.rng import RandomStreams

    for old, new in (("tp_ai_v2", "tp_ai_v3"), ("tp_scripted_v4", "tp_scripted_v5")):
        previous, current = bound(old), bound(new)
        old_body = previous.manifest.model_dump(mode="json")
        new_body = current.manifest.model_dump(mode="json")
        for body in (old_body, new_body):
            for key in ("manifest_id", "manifest_version", "title", "notes"):
                body.pop(key)
            body["timing"]["scripted_requests"]["script"].pop("version")
        assert old_body == new_body
        script = current.artifacts["timing.scripted_requests.script"]
        assert script.key == "requests.demo@v4"
        assert script.artifact.body.selection == "uniform_without_replacement"
        assert (
            script.artifact.body.templates
            == previous.artifacts["timing.scripted_requests.script"].artifact.body.templates
        )
        for seed in range(100):
            rng = RandomStreams(f"{seed:032x}")
            order = rng.shuffle(
                "jobs", script.artifact.body.templates, "scripted_request_templates", script.key
            )
            assert len(set(order[:3])) == 3
            assert (
                rng.shuffle(
                    "jobs", script.artifact.body.templates, "scripted_request_templates", script.key
                )
                == order
            )


def test_material_reference_text_omitted_from_placeholder_canonical_form() -> None:
    """BA-37 hash continuity: the optional ``text`` field must not appear in the canonical
    dump of placeholder versions — otherwise adding the field would have re-hashed every
    published material_reference artifact and broken their pinned bundles."""
    b = bound("tp_demo_v1")
    body = b.artifacts["instruments.consent"].artifact.body.model_dump(mode="json")
    assert "text" not in body


def test_unknown_artifact_fields_and_bad_file_names_rejected(tmp_path: Any) -> None:
    root = copy_content(tmp_path)
    edit_json(root / "scripts" / "requests.demo.v1.json", _set("body.tone", "urgent"))
    with pytest.raises(ContentError, match="unsupported field"):
        ContentStore(root)
    root2 = copy_content(tmp_path / "second")
    (root2 / "scripts" / "requests.demo.v1.json").rename(root2 / "scripts" / "requests.v1.json")
    with pytest.raises(ContentError, match="file name must be"):
        ContentStore(root2)


def test_artifact_kind_must_match_slot(tmp_path: Any) -> None:
    root = copy_content(tmp_path)
    edit_json(
        root / "profiles" / "pe_pilot_v2.json",
        _set("teammates.fallback_bank", {"artifact_id": "voices.pe_demo", "version": 1}),
    )
    with pytest.raises(BindError, match="expected fallback_bank"):
        bind(ContentStore(root), "pe_pilot_v2", mode="preview")


def test_qa_acceleration_only_in_qa_mode() -> None:
    with pytest.raises(BindError, match="only in qa mode"):
        bound("tp_demo_v1", mode="preview", timing_mode="accelerated_qa", qa_overlay=QA_X10)
    with pytest.raises(BindError, match="explicit qa_timing_overlay"):
        bound("tp_demo_v1", mode="qa", timing_mode="accelerated_qa")
    with pytest.raises(BindError, match="no qa overlay"):
        bound("tp_demo_v1", mode="qa", qa_overlay=QA_X10)
    fast = bound("tp_demo_v1", mode="qa", timing_mode="accelerated_qa", qa_overlay=QA_X10)
    assert fast.clock_speed_factor == 10
    assert not fast.analytic_eligible
    assert resolve_policy(fast, "high").clock_speed_factor == 10


def test_reference_content_matches_demo_fixtures() -> None:
    constants = fixture("demo_constants.json")
    behaviors = {b["id"]: b for b in fixture("behavior_reference.json")["behaviors"]}
    arts = store().artifacts
    warning = arts["warning.one_minute_left@v1"].artifact.body
    assert warning.text == behaviors["REF-TASK-02"]["value"]["message"]  # type: ignore[union-attr]
    assert warning.trigger.at_active_s == behaviors["REF-TASK-02"]["value"]["active_second"]  # type: ignore[union-attr]
    assert arts["requests.demo@v1"].artifact.body.templates == (  # type: ignore[union-attr]
        behaviors["REF-REQ-01"]["value"]["text"],
    )
    # The revised PE demo (2026-10-01) removed the scripted VOICE/ACK_P/crosstalk layers
    # (generative replies + canned fallback instead), so there is no demo literal left to
    # cross-check — the versioned artifacts remain the authoritative banks.
    assert "voices.pe_demo@v1" in arts  # type: ignore[operator]
    for seat in ("JT", "PM"):
        bank = arts["voices.pe_demo@v1"].artifact.body.seats[seat]  # type: ignore[union-attr]
        assert bank.thanks and bank.no_news and bank.after_share
        assert list(bank.greetings) == constants["shared"]["GREETINGS"]["value"]
    evals = constants["peer_evaluation"]["EVAL_CONFIG"]["value"]
    for arm, key in (
        ("eval", "exposure.pe_eval_demo@v1"),
        ("control", "exposure.pe_control_demo@v1"),
    ):
        body = arts[key].artifact.body
        # Entities normalize: the artifact pins `&#8217;` where the revised demo writes
        # `\u2019` — both render the same apostrophe.
        assert unescape(body.intro) == unescape(evals[arm]["intro"])  # type: ignore[union-attr]
        assert unescape(body.callout) == unescape(evals[arm]["callout"])  # type: ignore[union-attr]
        assert body.strip == evals[arm]["strip"]  # type: ignore[union-attr]
        assert body.system_message == evals[arm]["systemMsg"]  # type: ignore[union-attr]
        assert body.peer_intro == evals[arm]["peerIntro"]  # type: ignore[union-attr]
    for study, key, const in (
        ("time_pressure", "post_task.tp_demo@v1", "ITEMS"),
        # renamed DEMO_POST_ITEMS in the revised PE demo (2026-10-01)
        ("peer_evaluation", "post_task.pe_demo@v1", "DEMO_POST_ITEMS"),
    ):
        items = [i.text for i in arts[key].artifact.body.items]  # type: ignore[union-attr]
        demo_items = constants[study][const]["value"]
        # The revised PE demo (2026-10-01) shows only the first seven post-task items;
        # the artifact keeps the two trailing impression-management items (pe_post_08/09),
        # which remain intentional app measures — so the demo list must be a prefix.
        assert demo_items == items[: len(demo_items)]
    prompt = arts["prompt.assistant_demo@v1"].artifact.body
    assert prompt.text == constants["shared"]["AI_SYSTEM"]["value"]  # type: ignore[union-attr]
    tp = store().manifests["tp_demo_v1"].manifest
    pressure = constants["time_pressure"]["PRESSURE_CONFIG"]["value"]
    for spec in tp.condition_set:
        assert list(spec.manipulation.teammate_values) == pressure[spec.arm]  # type: ignore[union-attr]
    assert tp.timing.checkin is not None
    assert tp.timing.checkin.at_active_s == constants["time_pressure"]["CHECKIN_AT"]["value"]
    assert (
        tp.timing.checkin.collect_wall_ms
        == constants["time_pressure"]["CHECKIN_COLLECT_MS"]["value"]
    )
    assert (
        list(tp.timing.scripted_requests.times_active_s)
        == behaviors["REF-REQ-01"]["value"]["times_s"]
    )
    assert sorted(tp.timing.scripted_requests.seat_multiset or ()) == sorted(
        behaviors["REF-REQ-01"]["value"]["seat_multiset"]
    )
    assistant = tp.timing.response_classes["assistant"]
    assert assistant.timeout_ms == constants["shared"]["AI_TIMEOUT_MS"]["value"]
    pe = store().manifests["pe_demo_v1"].manifest
    ack = pe.teammates.acknowledgments
    assert ack is not None
    assert (
        dict(ack.unprompted_seat_weights) == behaviors["REF-PE-03"]["value"]["unprompted_ack_seat"]
    )
    crosstalk = pe.timing.response_classes["crosstalk"].delay[0]
    assert crosstalk.family == "fixed"
    assert crosstalk.seconds == behaviors["REF-PE-04"]["value"]["offset_active_s"]


def test_paired_arms_differ_only_in_manipulation() -> None:
    for manifest_id in PROFILES:
        entries = check_paired_arms(bound(manifest_id))
        paths = {e.path for e in entries}
        assert paths <= {
            "manipulation.arm.arm",
            "manipulation.arm.teammate_values",
            "manipulation.arm.exposure_copy",
        }, paths
        policies = [resolve_policy(bound(manifest_id), a) for a in bound(manifest_id).manifest.arms]
        assert policies[0].behavior_policy_hash == policies[1].behavior_policy_hash
        assert policies[0].manipulation_hash != policies[1].manipulation_hash


def test_reference_to_v2_diffs_match_declared_records() -> None:
    for left, right in cli.PROFILE_PAIRS:
        record = load_diff_record(CONTENT, left, right)
        actual = profile_diff(bound(left), bound(right))
        assert actual, (left, right)
        assert check_declared_diff(record, actual) == []
        mechanisms = {e.path for e in actual if e.path.endswith(".mechanism")}
        assert "behavior.classes.social_reply/JT.mechanism" in mechanisms


def test_compatibility_key_shared_by_arms_and_sensitive_to_inputs() -> None:
    b = bound("tp_review_first_v2")
    high, low = (resolve_policy(b, a) for a in ("high", "low"))
    base = compatibility_key(b, high, batch_id="batch-1", behavior_source_sha256="a" * 64)
    assert (
        base.key
        == compatibility_key(b, low, batch_id="batch-1", behavior_source_sha256="a" * 64).key
    )
    assert base.key.startswith("ck1:")
    other_batch = compatibility_key(b, high, batch_id="batch-2", behavior_source_sha256="a" * 64)
    assert base.differing(other_batch) == ["batch_id"]
    other_build = compatibility_key(b, high, batch_id="batch-1", behavior_source_sha256="b" * 64)
    assert base.differing(other_build) == ["behavior_source_sha256"]
    other_model = compatibility_key(
        b,
        high,
        batch_id="batch-1",
        behavior_source_sha256="a" * 64,
        provider_model_digest="m@sha256:1",
    )
    assert base.differing(other_model) == ["provider_model"]
    fast_b = bound("tp_review_first_v2", mode="qa", timing_mode="accelerated_qa", qa_overlay=QA_X10)
    fast = compatibility_key(
        fast_b, resolve_policy(fast_b, "high"), batch_id="batch-1", behavior_source_sha256="a" * 64
    )
    assert {"mode", "timing_mode", "clock_speed_factor", "behavior_policy"} <= set(
        base.differing(fast)
    )
    ref_b = bound("tp_demo_v1")
    ref = compatibility_key(
        ref_b, resolve_policy(ref_b, "high"), batch_id="batch-1", behavior_source_sha256="a" * 64
    )
    assert {"manifest_id", "manifest_sha256", "behavior_policy", "content"} <= set(
        base.differing(ref)
    )
    # BA-41: the grounded prompt builder version is an explicit pooling boundary.
    from app.services.prompts import PROMPT_BUILDER_VERSION

    assert base.components["prompt_builder"] == PROMPT_BUILDER_VERSION


def test_generated_schemas_are_current() -> None:
    for name, text in cli.generated_schemas().items():
        assert (CONTENT / "schemas" / name).read_text(encoding="utf-8") == text, name


def test_content_check_command_passes() -> None:
    assert cli.run_check(REPO) == []


def test_task_shape_cross_rules() -> None:
    """2026-10-15 crate-condition experiment: ``task`` pins the overlap level,
    the crate count, and an optional thin-file intel budget. The pins only ride
    ``overlap_v1``; ``demo_v1`` must not carry them."""
    # Legacy manifests serialize without the new keys — hashes unchanged.
    legacy = profile_data("tp_ai_v13")
    assert "crate_overlap" not in legacy["task"]
    manifest = Manifest.model_validate_json(json.dumps(legacy))
    assert manifest.task.overlap == "independent"
    dumped = manifest.task.model_dump(mode="json")
    assert "crate_overlap" not in dumped and "participant_intel_budget" not in dumped

    def with_task(**kw: Any) -> dict[str, Any]:
        data = copy.deepcopy(legacy)
        data["task"].update(kw)
        return data

    # demo_v1 may not set the new pins.
    with pytest.raises(ValidationError, match="demo_v1 manifests may not set"):
        Manifest.model_validate_json(json.dumps(with_task(crate_overlap="none")))
    # overlap_v1 must pin at least one new field.
    with pytest.raises(ValidationError, match="must set crate_overlap or"):
        Manifest.model_validate_json(
            json.dumps(with_task(generator="investigators.world/overlap_v1"))
        )
    # Crate count must match the level.
    for overlap, n in (("none", 12), ("some", 8), ("full", 4)):
        data = with_task(
            generator="investigators.world/overlap_v1", crate_overlap=overlap, n_crates=n
        )
        Manifest.model_validate_json(json.dumps(data))
        bad = with_task(
            generator="investigators.world/overlap_v1",
            crate_overlap=overlap,
            n_crates=8 if n != 8 else 12,
        )
        with pytest.raises(ValidationError, match="needs n_crates"):
            Manifest.model_validate_json(json.dumps(bad))
    # Full overlap + unassigned scripted targets is impossible.
    data = with_task(generator="investigators.world/overlap_v1", crate_overlap="full", n_crates=4)
    data["timing"]["scripted_requests"] = {
        "script": {"artifact_id": "requests.demo", "version": 4},
        "times_active_s": [45, 100, 155, 210, 245],
        "seat_policy": "coin_majority_shuffle",
        "targets": ["unassigned"] * 5,
        "status": "approved",
    }
    with pytest.raises(ValidationError, match="unassigned"):
        Manifest.model_validate_json(json.dumps(data))
    # Intel budget: the only supported thin-file budget is exactly 20
    # (audit S03 — the schema rejects every other value as a literal error
    # rather than silently administering an undesigned count).
    for bad in (15, 47):
        with pytest.raises(ValidationError, match="Input should be 20"):
            Manifest.model_validate_json(
                json.dumps(
                    with_task(
                        generator="investigators.world/overlap_v1",
                        crate_overlap="some",
                        participant_intel_budget=bad,
                    )
                )
            )


def test_overlap_profiles_bind() -> None:
    for mid in (
        "tp_ai_ovnone_v1",
        "tp_ai_ovsome_v1",
        "tp_ai_ovfull_v1",
        "tp_ai_ovsome_thin_v1",
        "tp_ai_ovnone_thin_v1",
        "tp_ai_ovfull_thin_v1",
    ):
        b = bind(store(), mid, mode="preview")
        assert b.manifest.task.generator == "investigators.world/overlap_v1"
    assert bound("tp_ai_ovnone_v1", mode="live").manifest.task.n_crates == 12
    assert bound("tp_ai_ovsome_thin_v1", mode="live").manifest.task.participant_intel_budget == 20
