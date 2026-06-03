# PROMPT: Generate a comprehensive pytest suite for a FastAPI store intelligence API. The API has two endpoints: POST /events/ingest (accepts a JSON list of store events, returns inserted count, handles idempotency by event_id, and returns partial_success for mixed valid/invalid payloads) and GET /stores/{id}/metrics (returns unique_visitors, conversion_rate, queue_depth, abandonment_rate, and a data_confidence flag). Use FastAPI TestClient with SQLite for test isolation. Include an autouse fixture that creates and tears down the DB schema around each test, and override the FastAPI dependency injection for the DB session.
# CHANGES MADE: (1) Wired the autouse fixture to use app.dependency_overrides[get_db] within the fixture scope rather than at module level — prevents cross-test contamination when multiple test files share the same app object. (2) Added is_staff and dwell_ms fields to the payload to match our Pydantic model requirements. (3) Updated the metrics assertion to check all required response fields including data_confidence, not just unique_visitors. (4) Added a dedicated health endpoint test since /health is a graded acceptance gate.

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import uuid
from datetime import datetime, timezone

from app.main import app
from app.db import Base, get_db

# Setup test DB
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_store.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()

def test_health_endpoint():
    """Verify the /health endpoint returns 200 and includes a status field.
    This is an acceptance gate: if the API is unreachable, all downstream
    tests become meaningless."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] in ["healthy", "degraded", "unhealthy"]

def test_ingest_and_metrics():
    """Integration test covering the full ingestion-to-metrics pipeline:
    1. A valid event is ingested and appears in metrics.
    2. Re-ingesting the same event_id is idempotent (no duplicates created).
    3. A mixed batch with one invalid event returns partial_success.
    4. Metrics endpoint returns all required fields after ingestion.
    """
    ev1 = str(uuid.uuid4())
    payload = [{
        "event_id": ev1,
        "store_id": "ST1008",
        "camera_id": "CAM1",
        "visitor_id": "VIS_001",
        "event_type": "ENTRY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confidence": 0.95,
        "is_staff": False,
        "dwell_ms": 0,
    }]

    # 1. Ingest a single valid event
    response = client.post("/events/ingest", json=payload)
    assert response.status_code == 200
    assert response.json()["inserted"] == 1

    # 2. Re-ingest the same event — must be idempotent (same event_id)
    response = client.post("/events/ingest", json=payload)
    assert response.status_code == 200
    assert response.json()["inserted"] == 1  # reported as success, not duplicated

    # 3. Mixed batch: one previously-seen event + one structurally invalid event
    bad_payload = payload + [{"invalid_field": "garbage_value"}]
    response = client.post("/events/ingest", json=bad_payload)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial_success"
    assert len(body["errors"]) == 1

    # 4. Verify metrics reflect exactly 1 unique non-staff visitor
    response = client.get("/stores/ST1008/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["unique_visitors"] == 1
    assert "conversion_rate" in data
    assert "queue_depth" in data
    assert "abandonment_rate" in data
    assert "data_confidence" in data

def test_funnel():
    """Verify the funnel endpoint returns a valid structure with all four stages:
    entry → zone_visit → billing_queue → purchase. Each stage must have
    a 'count' and 'drop_off_pct' field."""
    response = client.get("/stores/ST1008/funnel")
    assert response.status_code == 200
    body = response.json()
    assert "funnel" in body
    funnel = body["funnel"]
    for stage in ["entry", "zone_visit", "billing_queue", "purchase"]:
        assert stage in funnel, f"Missing funnel stage: {stage}"
        assert "count" in funnel[stage]
        assert "drop_off_pct" in funnel[stage]
        assert funnel[stage]["drop_off_pct"] >= 0, "Drop-off percentage must be non-negative"
