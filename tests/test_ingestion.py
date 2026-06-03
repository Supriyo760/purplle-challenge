# PROMPT: Generate a pytest suite for the POST /events/ingest endpoint of a FastAPI store intelligence API. Test batch ingestion up to 500 events, idempotency (same event_id inserted twice returns success both times), partial failure (mix of valid and invalid events), batch size limit enforcement, and edge cases: empty payload, all-staff events, and malformed JSON fields.
# CHANGES MADE: Adapted fixtures to use our specific DB/model setup, added test for the 500-event batch limit from the spec, and verified the partial_success response structure matches our actual API response format.

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import uuid
from datetime import datetime, timezone

from app.main import app
from app.db import Base, get_db

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_ingestion.db"
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

def _make_event(**overrides):
    base = {
        "event_id": str(uuid.uuid4()),
        "store_id": "ST1008",
        "camera_id": "CAM1",
        "visitor_id": "VIS_1",
        "event_type": "ENTRY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confidence": 0.95,
        "dwell_ms": 0,
        "is_staff": False,
    }
    base.update(overrides)
    return base


class TestIngestBasic:
    def test_ingest_single_valid_event(self):
        payload = [_make_event()]
        resp = client.post("/events/ingest", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        assert resp.json()["inserted"] == 1

    def test_ingest_empty_list(self):
        resp = client.post("/events/ingest", json=[])
        assert resp.status_code == 200
        assert resp.json()["inserted"] == 0

    def test_ingest_invalid_json_body(self):
        resp = client.post("/events/ingest", content="not json", headers={"Content-Type": "application/json"})
        assert resp.status_code in [400, 422]

    def test_ingest_non_list_payload(self):
        resp = client.post("/events/ingest", json={"event_id": "abc"})
        assert resp.status_code in [400, 422]


class TestIdempotency:
    def test_duplicate_event_id_is_idempotent(self):
        event = _make_event()
        resp1 = client.post("/events/ingest", json=[event])
        assert resp1.json()["inserted"] == 1

        resp2 = client.post("/events/ingest", json=[event])
        assert resp2.status_code == 200
        assert resp2.json()["inserted"] == 1  # idempotent — counts as success

    def test_idempotency_does_not_create_duplicates(self):
        event = _make_event()
        client.post("/events/ingest", json=[event])
        client.post("/events/ingest", json=[event])

        # Verify only 1 entry via metrics
        resp = client.get(f"/stores/{event['store_id']}/metrics")
        assert resp.json()["unique_visitors"] == 1


class TestPartialFailure:
    def test_mix_of_valid_and_invalid(self):
        valid = _make_event()
        invalid = {"invalid": "garbage"}
        resp = client.post("/events/ingest", json=[valid, invalid])
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "partial_success"
        assert data["inserted"] >= 1
        assert len(data["errors"]) >= 1

    def test_all_invalid_events(self):
        resp = client.post("/events/ingest", json=[{"bad": 1}, {"worse": 2}])
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failure"
        assert data["inserted"] == 0


class TestBatchLimit:
    def test_batch_over_500_rejected(self):
        events = [_make_event() for _ in range(501)]
        resp = client.post("/events/ingest", json=events)
        assert resp.status_code in [400, 422]


class TestStaffEvents:
    def test_staff_events_excluded_from_visitor_count(self):
        staff_event = _make_event(visitor_id="VIS_STAFF_1", is_staff=True)
        customer_event = _make_event(visitor_id="VIS_CUST_1", is_staff=False)
        client.post("/events/ingest", json=[staff_event, customer_event])

        resp = client.get("/stores/ST1008/metrics")
        # Only the non-staff visitor should be counted
        assert resp.json()["unique_visitors"] == 1


class TestEdgeCases:
    def test_zero_confidence_event_accepted(self):
        event = _make_event(confidence=0.0)
        resp = client.post("/events/ingest", json=[event])
        assert resp.json()["inserted"] == 1

    def test_all_event_types_accepted(self):
        types = ["ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
                 "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"]
        events = [_make_event(event_type=t) for t in types]
        resp = client.post("/events/ingest", json=events)
        assert resp.json()["inserted"] == len(types)
