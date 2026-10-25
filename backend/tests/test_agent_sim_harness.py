"""Unit tests for the agent-participant simulation harness (plan §21, BA-78..BA-88).

These cover the guard/provider/evidence mechanics without a database; the SQL-level
remedies (BA-79 file-join scoping, BA-82 probe-event correlation, BA-83 per-request
delivery) are exercised by the disposable-DB batch plus the integration suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.db.guard import UnsafeTestDatabaseError
from app.services.entry import EntryError, enter
from harness import cli, simdb
from harness import report as report_mod
from harness import runner as runner_mod
from harness.oracles import Finding
from harness.personas import Action, NormalPersona

TEST_DSN = "postgresql://u:p@127.0.0.1:55999/investigators_test_db"
LIVE_DSN = "postgresql://live:pw@db.internal:5432/investigators_db"


@pytest.mark.parametrize("seed", range(10))
def test_required_choice_persona_uses_declared_option(seed: int) -> None:
    options = ["People", "Computer-controlled teammates", "Not sure"]
    action = NormalPersona(seed)._item_action("nature", {"skippable": False, "options": options})
    assert action.kind == "item_response"
    assert action.payload["value"] in options


def test_persona_preserves_numeric_and_confidence_answers() -> None:
    persona = NormalPersona(1)
    assert 1 <= persona._item_action("rating", {}).payload["value"] <= 7
    assert 20 <= persona._item_action("confidence", {}, crate=1).payload["value"] <= 95


def _env(**over: str) -> dict[str, str]:
    base = {
        "INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS": "1",
        "APP_MODE": "qa",
        "INVESTIGATORS_EXTRA_LIVE_DSNS": LIVE_DSN,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# BA-78: provision() honors the real opt-in, app mode, and live env file
# ---------------------------------------------------------------------------


def test_provision_refuses_without_optin() -> None:
    env = _env()
    del env["INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS"]
    with pytest.raises(UnsafeTestDatabaseError):
        simdb.provision(TEST_DSN, environ=env)


def test_provision_refuses_live_app_mode() -> None:
    with pytest.raises(UnsafeTestDatabaseError):
        simdb.provision(TEST_DSN, environ=_env(APP_MODE="live"))


def test_provision_refuses_dsn_matching_only_configured_live_env(tmp_path) -> None:
    """A test-named DSN equal to the *env-file* live entry is refused — the guard
    must read the configured live env file, not just process env."""
    live_env = tmp_path / ".env"
    # The "live" DSN here points at a test-named database to isolate the
    # env-file comparison path from the name-convention check.
    live_env.write_text(f"DATABASE_URL={TEST_DSN}\n", encoding="utf-8")
    env = _env()
    del env["INVESTIGATORS_EXTRA_LIVE_DSNS"]
    with pytest.raises(UnsafeTestDatabaseError):
        simdb.provision(TEST_DSN, live_env_file=live_env, environ=env)


def test_provision_fails_closed_without_live_reference(monkeypatch, tmp_path) -> None:
    # No configured live reference anywhere: no env var, no env-file arg, and an
    # absent project .env — the guard must fail closed.
    monkeypatch.setattr(simdb, "_PROJECT_ENV_FILE", tmp_path / ".env")
    env = _env()
    del env["INVESTIGATORS_EXTRA_LIVE_DSNS"]
    with pytest.raises(UnsafeTestDatabaseError, match="no live DSN"):
        simdb.provision(TEST_DSN, environ=env)


def test_provision_project_env_is_a_live_reference(monkeypatch, tmp_path) -> None:
    """BA-78 residual: a bare `python -m harness.cli` call supplies no
    live_env_file arg and no INVESTIGATORS_LIVE_ENV_FILE — but the project
    .env must still be consulted. Another env-var reference cannot excuse
    omitting it."""
    project_env = tmp_path / ".env"
    # The "live" DSN here is test-named so only the env-file comparison bites.
    project_env.write_text(f"DATABASE_URL={TEST_DSN}\n", encoding="utf-8")
    monkeypatch.setattr(simdb, "_PROJECT_ENV_FILE", project_env)
    with pytest.raises(UnsafeTestDatabaseError):
        simdb.provision(TEST_DSN, environ=_env())  # EXTRA_LIVE_DSNS present


def test_provision_project_env_absent_is_not_an_error(monkeypatch, tmp_path) -> None:
    """No project .env on disk (e.g. a packaged deploy) is not itself a failure —
    the configured/env-var references still apply."""
    monkeypatch.setattr(simdb, "_PROJECT_ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(simdb, "reset_disposable_schema", lambda e, d: None)
    monkeypatch.setattr(simdb, "_alembic_head", lambda dsn: None)
    monkeypatch.setattr(
        simdb,
        "apply_bootstrap",
        lambda engine, dsn, db: simdb.RoleDsns(
            superuser=dsn,
            migrator=dsn + "?m",
            runtime=dsn + "?r",
            research=dsn + "?re",
            compensation=dsn + "?c",
        ),
    )
    monkeypatch.setattr(simdb, "make_engine", lambda dsn: SimpleNamespace(dispose=lambda: None))
    db, _ = simdb.provision(TEST_DSN, environ=_env())
    assert db.container is None


# ---------------------------------------------------------------------------
# BA-86: a spawned container is removed on any post-spawn failure
# ---------------------------------------------------------------------------


def test_provision_removes_spawned_container_on_guard_failure(monkeypatch) -> None:
    removed: list[str] = []
    monkeypatch.setattr(simdb, "_spawn_container", lambda: ("simdb-fake", TEST_DSN))
    monkeypatch.setattr(simdb, "_dispose_container", removed.append)
    env = _env()
    del env["INVESTIGATORS_ALLOW_DESTRUCTIVE_DB_TESTS"]
    with pytest.raises(UnsafeTestDatabaseError):
        simdb.provision(environ=env)
    assert removed == ["simdb-fake"]


def test_provision_removes_spawned_container_on_bootstrap_failure(monkeypatch) -> None:
    removed: list[str] = []
    disposed: list[str] = []
    fake_engine = SimpleNamespace(dispose=lambda: disposed.append("engine"))
    monkeypatch.setattr(simdb, "_spawn_container", lambda: ("simdb-fake", TEST_DSN))
    monkeypatch.setattr(simdb, "_dispose_container", removed.append)
    monkeypatch.setattr(simdb, "make_engine", lambda dsn: fake_engine)
    monkeypatch.setattr(simdb, "reset_disposable_schema", lambda e, d: None)

    def _boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("bootstrap failed")

    monkeypatch.setattr(simdb, "apply_bootstrap", _boom)
    with pytest.raises(RuntimeError, match="bootstrap failed"):
        simdb.provision(environ=_env())
    assert removed == ["simdb-fake"]
    assert disposed == ["engine"]


def test_provision_happy_path_mocked(monkeypatch) -> None:
    """Guard passes with the real opt-in/env inputs; engines get built per role."""
    monkeypatch.setattr(simdb, "reset_disposable_schema", lambda e, d: None)
    monkeypatch.setattr(simdb, "_alembic_head", lambda dsn: None)
    monkeypatch.setattr(
        simdb,
        "apply_bootstrap",
        lambda engine, dsn, db: simdb.RoleDsns(
            superuser=dsn,
            migrator=dsn + "?m",
            runtime=dsn + "?r",
            research=dsn + "?re",
            compensation=dsn + "?c",
        ),
    )
    engines_made: list[str] = []
    monkeypatch.setattr(
        simdb,
        "make_engine",
        lambda dsn: engines_made.append(dsn) or SimpleNamespace(dispose=lambda: None),
    )
    db, disposable = simdb.provision(TEST_DSN, environ=_env())
    assert disposable.dsn == TEST_DSN
    assert db.container is None
    assert len(engines_made) == 5  # superuser + 4 role engines


# ---------------------------------------------------------------------------
# BA-85: --provider ollama must verify the effective provider
# ---------------------------------------------------------------------------


def test_make_provider_ollama_refuses_none_resolution() -> None:
    settings = SimpleNamespace(MODEL_PROVIDER="none")
    with (
        patch("app.settings.load_settings", return_value=settings),
        patch("app.providers.select.provider_from_settings", return_value=None),
        pytest.raises(SystemExit),
    ):
        cli._make_provider("ollama")


def test_make_provider_ollama_refuses_double() -> None:
    stub = SimpleNamespace(is_double=True, is_remote=False, provider_id="stub")
    settings = SimpleNamespace(MODEL_PROVIDER="auto")
    with (
        patch("app.settings.load_settings", return_value=settings),
        patch("app.providers.select.provider_from_settings", return_value=stub),
        pytest.raises(SystemExit),
    ):
        cli._make_provider("ollama")


def test_make_provider_ollama_accepts_local_adapter() -> None:
    live = SimpleNamespace(is_double=False, is_remote=False, provider_id="ollama-local")
    settings = SimpleNamespace(MODEL_PROVIDER="ollama")
    with (
        patch("app.settings.load_settings", return_value=settings),
        patch("app.providers.select.provider_from_settings", return_value=live),
    ):
        assert cli._make_provider("ollama") is live


# ---------------------------------------------------------------------------
# BA-88: same-second runs must not overwrite each other
# ---------------------------------------------------------------------------


class _FrozenDatetime:
    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        return datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)


def _report() -> runner_mod.SessionReport:
    return runner_mod.SessionReport(
        session_id="s-1",
        persona="normal",
        seed=1,
        manifest_id="tp_demo_v5",
        arm="control",
        stage="completion",
        terminal_reason=None,
        active_ms=360_000,
    )


def test_write_report_collision_resistant(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(report_mod, "datetime", _FrozenDatetime)
    monkeypatch.setattr(report_mod, "_source_stamp", lambda: "teststamp")
    first = report_mod.write_report([_report()], out_dir=tmp_path)
    second = report_mod.write_report([_report()], out_dir=tmp_path)
    assert first != second
    assert (first / "summary.md").exists()
    assert (second / "summary.md").exists()
    assert (first / "sessions").is_dir() and (second / "sessions").is_dir()


def test_write_report_stamps_provider_identities(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(report_mod, "_source_stamp", lambda: "teststamp")
    out = report_mod.write_report(
        [_report()],
        out_dir=tmp_path,
        provider_requested="ollama",
        provider_effective="ollama-local",
    )
    text = (out / "summary.md").read_text(encoding="utf-8")
    assert "requested `ollama`, effective `ollama-local`" in text


# ---------------------------------------------------------------------------
# BA-81: accepted hostile commands become fail findings
# ---------------------------------------------------------------------------


def test_expect_reject_finding_on_accepted() -> None:
    action = Action("teammate_request", {"crate": 9}, expect_reject=True, note="bad-crate")
    finding = runner_mod._expect_reject_finding(action, SimpleNamespace(accepted=True), "task")
    assert finding is not None
    assert finding.severity == "fail"
    assert finding.check == "INV-EXPECT-REJECT"


def test_expect_reject_quiet_on_rejection() -> None:
    action = Action("teammate_request", {"crate": 9}, expect_reject=True)
    assert (
        runner_mod._expect_reject_finding(action, SimpleNamespace(accepted=False), "task") is None
    )
    assert (
        runner_mod._expect_reject_finding(
            Action("social_reply", {"body": "hi"}), SimpleNamespace(accepted=True), "task"
        )
        is None
    )


# ---------------------------------------------------------------------------
# BA-80 / BA-84 structural guarantees
# ---------------------------------------------------------------------------


def test_file_entry_has_no_hidden_location_field() -> None:
    """The bot's file projection must not carry record.location_id (BA-80)."""
    from harness.view import FileEntry

    assert "location_id" not in FileEntry.__dataclass_fields__
    assert set(FileEntry.__dataclass_fields__) == {
        "record_id",
        "crate",
        "stamp",
        "body",
        "event_type",
        "source",
        "shared_by",
    }


def test_enter_refuses_seed_override_in_live_mode() -> None:
    """BA-84: the deterministic-seed hook is test-only — live mode refuses before
    any database access."""
    with pytest.raises(EntryError, match="seed_override_forbidden"):
        enter(
            SimpleNamespace(),  # engine is never reached
            SimpleNamespace(),
            query={"PROLIFIC_PID": "p", "STUDY_ID": "s", "SESSION_ID": "x", "batch": "b"},
            app_mode="live",
            preview_key=None,
            current_manifest_sha256={},
            build_source_sha256="x",
            seed_hex="ab" * 32,
        )


def test_finding_is_serializable() -> None:
    import dataclasses
    import json

    f = Finding("INV-X", "warn", "detail", {"a": 1})
    assert json.dumps(dataclasses.asdict(f))


# ---------------------------------------------------------------------------
# BA-94: a same-seat warn must not consume the dedupe key for a cross-seat fail
# ---------------------------------------------------------------------------


def _repeat_lines(*actors: str):
    body = "i'm checking the last few crates we talked about today"
    return [("team", actor, body) for actor in actors]


def test_near_repeat_cross_seat_fail_survives_same_seat_dedupe(monkeypatch) -> None:
    """BA-94 acceptance: JT/JT/PM identical qualifying lines → at least one fail;
    the JT/JT warn cannot shadow the JT/PM collision."""
    from harness import oracles

    monkeypatch.setattr(
        oracles,
        "_delivered_texts",
        lambda engine, sid, exclude_cards=False: _repeat_lines("JT", "JT", "PM"),
    )
    monkeypatch.setattr(oracles.engines, "is_near_repeat", lambda body, prior: True)
    findings = oracles._inv_near_repeat(None, None, frozenset())  # type: ignore[arg-type]
    assert any(f.severity == "fail" for f in findings)


def test_near_repeat_same_seat_only_warns(monkeypatch) -> None:
    """BA-94 acceptance: a JT/JT pair stays warn — same-seat repetition is a
    believability cost but not the cross-seat impersonation signal."""
    from harness import oracles

    monkeypatch.setattr(
        oracles,
        "_delivered_texts",
        lambda engine, sid, exclude_cards=False: _repeat_lines("JT", "JT"),
    )
    monkeypatch.setattr(oracles.engines, "is_near_repeat", lambda body, prior: True)
    findings = oracles._inv_near_repeat(None, None, frozenset())  # type: ignore[arg-type]
    assert findings and all(f.severity == "warn" for f in findings)


# ---------------------------------------------------------------------------
# Permanent oracle fixtures — the archived failing lines from the behaviors
# register, run through the *real* checks (no monkeypatched is_near_repeat).
# Whenever an invariant is loosened, these must still catch the original
# failure mode — the "did the catch survive the fix" contract.
# ---------------------------------------------------------------------------

# AB-06 (session c53e237e): convergent cross-seat replies, Dice ≈ 0.87.
_AB06_JT = "i'd have to check. send me a request and i'll pass along what i have."
_AB06_PM = "i'd have to check my notes. send me a request and i will pass along what i have."
# AB-16 (run-20261004T193346Z, disconnecter): grounded-assignment collision.
_AB16_JT = "i'm on crates 4, 5, 6 and 8"
_AB16_PM = "I'm on crates 2, 3, 4 and 5"
# SQ-9 re-ask pair (run-20261005T004059Z): legitimately similar — warn, not fail.
_SQ9_JT_FIRST = "i'm on crates 4, 5, 6 and 7"
_SQ9_JT_REASK = "still on crates 4, 5, 6 and 7"


def test_fixture_ab06_cross_seat_convergence_still_fails() -> None:
    from harness import oracles

    findings = oracles._near_repeat_findings([("team", "JT", _AB06_JT), ("team", "PM", _AB06_PM)])
    assert any(f.severity == "fail" for f in findings), findings


def test_fixture_ab16_grounded_collision_still_fails() -> None:
    from harness import oracles

    findings = oracles._near_repeat_findings([("team", "JT", _AB16_JT), ("team", "PM", _AB16_PM)])
    assert any(f.severity == "fail" for f in findings), findings


def test_fixture_sq9_reask_frame_is_warn_not_fail() -> None:
    from harness import oracles

    findings = oracles._near_repeat_findings(
        [("team", "JT", _SQ9_JT_FIRST), ("team", "JT", _SQ9_JT_REASK)]
    )
    assert findings and all(f.severity == "warn" for f in findings), findings


def test_fixture_assistant_model_multicrate_still_fails() -> None:
    """Pre-SQ-8 contract: generated text naming >1 crate is answer_key_like —
    a delivered line of that shape means the app's own gate failed."""
    from harness import oracles

    assert oracles._model_gate_breach(
        "Crate 1 has laptops and Crate 4 has the TVs per your file.", "none"
    )
    assert not oracles._model_gate_breach("Crate 1 has the laptops.", "none")


def test_fixture_contents_route_map_is_exempt() -> None:
    from harness import oracles

    contents_map = "; ".join(f"Crate {i} — item{i}" for i in range(1, 9))
    assert not oracles._model_gate_breach(contents_map, "deterministic_contents_summary")
    # …but the same text delivered as model output still breaches the bound.
    assert oracles._model_gate_breach(contents_map, "none")


def test_fixture_location_dump_fails_everywhere() -> None:
    from harness import oracles

    assert oracles._location_dump("Crate 3 at The Sawmill, and Crate 5 at The Marina")
    assert not oracles._location_dump("Crate 3 — laptops; Crate 5 — headphones")


# ---------------------------------------------------------------------------
# Lab-transcript utterance corpus (harvest.py + utterances._lab_lines)
# ---------------------------------------------------------------------------


def test_harvest_classify_routes_lines_to_pools() -> None:
    from harness import harvest

    assert harvest._classify("are you guys bots?", "team") == "PROBES"
    assert harvest._classify("is anyone here actually real", "team") == "NEAR_MISS_PROBES"
    assert harvest._classify("Hey!", "team") == "GREETS"
    assert harvest._classify("what do you have on crate 4?", "team") == "TEAM_ASKS"
    assert harvest._classify("nothing on that one sorry", "team") == "TEAM_DECLINE_ASK"
    assert harvest._classify("how are you guys", "team") == "TEAM_SOCIAL"
    assert harvest._classify("what's in crate 3", "assistant") == "ASSISTANT_CRATE"
    assert harvest._classify("which crates have laptops", "assistant") == "ASSISTANT_CONTENTS"
    assert harvest._classify("", "team") is None
    assert harvest._classify("x" * 300, "team") is None
    # The structured-request render is a message row but not authored text —
    # harvesting it into TEAM_ASKS would make typed asks parrot UI chrome.
    assert harvest._classify("Requesting intel on Crate 3.", "team") is None
    assert harvest._classify("requesting intel on crate #5", "team") is None


def test_harvest_normalize_slots_crate_digits() -> None:
    from harness import harvest

    assert harvest._normalize("Requesting intel on Crate 4.") == "Requesting intel on Crate {c}."
    assert harvest._normalize("anything on crate #7?") == "anything on crate #{c}?"


def test_lab_corpus_file_feeds_draw_pool(tmp_path, monkeypatch) -> None:
    """A committed corpus line must appear in the effective draw pool."""
    import random

    from harness import utterances

    monkeypatch.setattr(utterances, "_lab_corpus", {"TEAM_SOCIAL": ["corpus line xyz"]})
    draws = {utterances.draw(utterances.TEAM_SOCIAL, random.Random(i)) for i in range(200)}
    assert "corpus line xyz" in draws


def test_lab_corpus_absent_file_is_noop(monkeypatch, tmp_path) -> None:
    from harness import utterances

    monkeypatch.setattr(utterances, "_CORPUS_PATH", tmp_path / "absent.json")
    monkeypatch.setattr(utterances, "_lab_corpus", None)
    assert utterances._lab_lines(utterances.TEAM_SOCIAL) == []


def test_typed_ask_pools_exclude_the_structured_render() -> None:
    """The server's canonical structured-request line must not live in the
    typed-ask pools — a persona typing it would parrot UI chrome."""
    import re

    from harness import utterances

    render = re.compile(r"^requesting intel on crate", re.I)
    for pool in (utterances.TEAM_ASKS, utterances.TEAM_ASK_FOLLOWUPS):
        assert not any(render.match(line) for line in pool)


def test_mutate_never_splits_a_word_with_a_space() -> None:
    """The double-space mutation may only double an existing inter-word space —
    inserting a space mid-word produced "a re" from "are" (sim observation)."""
    import random
    import re

    from harness.utterances import mutate

    original = "what stolen objects are in which crates?"
    words = set(re.findall(r"\w+", original))
    for seed in range(3000):
        out = mutate(original, random.Random(seed), intensity=0.6)
        for token in re.findall(r"\w+", out):
            # a fragment of an original word means a word was split apart
            assert not any(token != w and token in w for w in words), f"{seed}: {out!r}"
