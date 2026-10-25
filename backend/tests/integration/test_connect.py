from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.db import schema as s
from app.domain.manifests.store import ContentStore
from app.main import create_app
from app.services import adminops, credentials
from app.settings import ConnectCompletion
from tests.integration import audit_helpers as ah
from tests.integration.conftest import MigratedDatabase

pytestmark = pytest.mark.integration
BATCH = "connect.test"
URL = "https://connect.cloudresearch.com/participant/project/PROJECT1/complete"


def _batch(db: MigratedDatabase, store: ContentStore) -> None:
    adminops.create_batch(
        db.runtime,
        store,
        actor="test",
        batch_id=BATCH,
        manifest_id="tp_review_first_v10",
        mode="live",
    )
    with db.runtime.begin() as conn:
        conn.execute(
            update(s.batch)
            .where(s.batch.c.batch_id == BATCH)
            .values(
                enrollment_open=True,
            )
        )


@pytest.mark.parametrize("handoff", [{"code": "CONNECT-TEST"}, {"url": URL}])
def test_connect_entry_completion_payment_and_reload(
    migrated: MigratedDatabase,
    make_settings: Any,
    handoff: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INVESTIGATORS_BUILD_SOURCE_SHA256", ah.BUILD_SHA)
    store = ContentStore.load_default()
    _batch(migrated, store)
    settings = make_settings(
        APP_MODE="live",
        DATABASE_URL=migrated.dsns.runtime,
        LOCAL_MODEL_DIGEST="ab" * 32,
        CONNECT_COMPLETION={BATCH: handoff},
    )
    app = create_app(settings, engine=migrated.runtime, configure_logs=False)
    query = {
        "batch": BATCH,
        "participantId": "CONNECT-P1",
        "projectId": "PROJECT1",
        "assignmentId": "ASSIGNMENT1",
    }
    with TestClient(app, base_url="https://testserver/investigators/api") as client:
        entered = client.post("/entry", params=query)
        assert entered.status_code == 200, entered.text
        sid = uuid.UUID(entered.json()["session_id"])
        writer = uuid.UUID(client.cookies.get("inv_writer"))
        client.headers["x-writer-id"] = str(writer)
        client.headers[credentials.CSRF_HEADER] = client.cookies.get(credentials.CSRF_COOKIE)
        snap = client.get("/session").json()["session"]
        assert snap["completion_code"] is None and snap["completion_url"] is None
        with migrated.superuser.connect() as conn:
            link = (
                conn.execute(
                    select(s.platform_link).where(
                        s.platform_link.c.session_id == sid,
                    )
                )
                .mappings()
                .one()
            )
            assert (
                link["platform"],
                link["platform_pid"],
                link["study_ref"],
                link["session_ref"],
            ) == (
                "connect",
                "CONNECT-P1",
                "PROJECT1",
                "ASSIGNMENT1",
            )
        resumed = client.post("/entry", params={"batch": BATCH, "participant_id": "CONNECT-P1"})
        assert resumed.status_code == 200 and resumed.json()["resumed"] is True
        writer = uuid.UUID(client.cookies.get("inv_writer"))
        client.headers["x-writer-id"] = str(writer)
        client.headers[credentials.CSRF_HEADER] = client.cookies.get(credentials.CSRF_COOKIE)
        consent = client.post(
            "/commands",
            json={
                "kind": "consent",
                "command_id": str(uuid.uuid4()),
                "expected_revision": ah.stage(migrated, sid)[1],
                "payload": {"action": "consented", "text_version": "consent.reference@v4"},
            },
        )
        assert consent.status_code == 200, consent.text
        # Only the disposable fixture jumps the task; exercise real final commands.
        with migrated.runtime.begin() as conn:
            conn.execute(
                update(s.session).where(s.session.c.session_id == sid).values(stage="debrief")
            )
        ah.run(migrated, store, sid, writer, "debrief_ack", {}, ah.stage(migrated, sid)[1])
        ah.run(
            migrated,
            store,
            sid,
            writer,
            "data_use_choice",
            {"choice": "withdraw_all"},
            ah.stage(migrated, sid)[1],
        )
        command = {"command_id": str(uuid.uuid4()), "expected_revision": ah.stage(migrated, sid)[1]}
        response = client.post("/complete", json=command)
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["completion_code"] == "PLACEHOLDER-CODE"
        assert result["completion_url"] == handoff.get("url")
        assert result["completion_platform"] == "connect"
        assert result["entitlement"]["status"] == "pending"
        assert result["entitlement"]["cents"] == 400
        assert client.post("/complete", json=command).json()["result"] == result
        settings.CONNECT_COMPLETION[BATCH] = ConnectCompletion(code="CHANGED")
        app.state.settings = settings.model_copy(update={"COMPLETION_CODE": "CHANGED"})
        snap = client.get("/session").json()["session"]
        assert snap["completion_code"] == "PLACEHOLDER-CODE"
        assert snap["completion_url"] == handoff.get("url")
        with migrated.runtime.connect() as conn:
            receipt = conn.execute(
                select(s.completion_receipt.c.handoff).where(
                    s.completion_receipt.c.session_id == sid,
                )
            ).scalar_one()
            assert receipt["receipt_code"].startswith("INV-")


def test_prolific_entry_completion_shows_configured_code(
    migrated: MigratedDatabase, make_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prolific completers see COMPLETION_CODE verbatim — participants copy/paste
    it back on the platform, so no redirect URL is ever served."""
    monkeypatch.setenv("INVESTIGATORS_BUILD_SOURCE_SHA256", ah.BUILD_SHA)
    store = ContentStore.load_default()
    _batch(migrated, store)
    settings = make_settings(
        APP_MODE="live",
        DATABASE_URL=migrated.dsns.runtime,
        LOCAL_MODEL_DIGEST="ab" * 32,
        COMPLETION_CODE="TESTPROLIFIC1",
    )
    app = create_app(settings, engine=migrated.runtime, configure_logs=False)
    query = {
        "batch": BATCH,
        "PROLIFIC_PID": "PROLIFIC-P1",
        "STUDY_ID": "STUDY1",
        "SESSION_ID": "SUB1",
    }
    with TestClient(app, base_url="https://testserver/investigators/api") as client:
        entered = client.post("/entry", params=query)
        assert entered.status_code == 200, entered.text
        sid = uuid.UUID(entered.json()["session_id"])
        writer = uuid.UUID(client.cookies.get("inv_writer"))
        client.headers["x-writer-id"] = str(writer)
        client.headers[credentials.CSRF_HEADER] = client.cookies.get(credentials.CSRF_COOKIE)
        consent = client.post(
            "/commands",
            json={
                "kind": "consent",
                "command_id": str(uuid.uuid4()),
                "expected_revision": ah.stage(migrated, sid)[1],
                "payload": {"action": "consented", "text_version": "consent.reference@v4"},
            },
        )
        assert consent.status_code == 200, consent.text
        with migrated.runtime.begin() as conn:
            conn.execute(
                update(s.session).where(s.session.c.session_id == sid).values(stage="debrief")
            )
        ah.run(migrated, store, sid, writer, "debrief_ack", {}, ah.stage(migrated, sid)[1])
        ah.run(
            migrated,
            store,
            sid,
            writer,
            "data_use_choice",
            {"choice": "withdraw_all"},
            ah.stage(migrated, sid)[1],
        )
        command = {"command_id": str(uuid.uuid4()), "expected_revision": ah.stage(migrated, sid)[1]}
        response = client.post("/complete", json=command)
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["completion_code"] == "TESTPROLIFIC1"
        assert result["completion_url"] is None
        assert result["completion_platform"] == "prolific"
        assert result["entitlement"]["status"] == "pending"
        snap = client.get("/session").json()["session"]
        assert snap["completion_code"] == "TESTPROLIFIC1"
        assert snap["completion_url"] is None
        with migrated.runtime.connect() as conn:
            receipt = conn.execute(
                select(s.completion_receipt.c.handoff).where(
                    s.completion_receipt.c.session_id == sid,
                )
            ).scalar_one()
            assert receipt["receipt_code"].startswith("INV-")
            assert receipt["completion_code"] == "TESTPROLIFIC1"


def test_unconfigured_connect_entry_creates_no_session(
    migrated: MigratedDatabase, make_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INVESTIGATORS_BUILD_SOURCE_SHA256", ah.BUILD_SHA)
    store = ContentStore.load_default()
    _batch(migrated, store)
    settings = make_settings(APP_MODE="live", DATABASE_URL=migrated.dsns.runtime)
    with TestClient(create_app(settings, engine=migrated.runtime, configure_logs=False)) as client:
        response = client.post("/entry", params={"batch": BATCH, "participantId": "P1"})
        assert response.status_code == 503
        assert "connect_completion_not_configured" in response.text
    with migrated.runtime.connect() as conn:
        assert conn.execute(select(func.count()).select_from(s.session)).scalar_one() == 0
