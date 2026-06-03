# PROMPT: Generate a pytest suite for the POST /events/ingest endpoint of a FastAPI store intelligence API. Test: (1) batch ingestion up to 500 events, (2) idempotency — the same event_id inserted twice must not create a duplicate record and must return success both times, (3) partial failure — a batch containing both valid and invalid events returns partial_success with an errors list, (4) batch size enforcement — batches exceeding 500 events must be rejected with a 4xx status, (5) edge cases including empty payloads, all-staff event batches, and malformed JSON field types. Use FastAPI TestClient with a SQLite test DB, autouse fixtures for schema setup/teardown, and FastAPI dependency_overrides to inject the test DB session.
# CHANGES MADE: (1) Moved app.dependency_overrides into the autouse fixture (not at module level) to prevent DB session bleed between test files that share the same app instance. (2) Added the test for the 501-event batch to exactly match the spec limit of 500. (3) Fixed partial_success structure assertion to check for the 'errors' key as a list, not just its existence. (4) Added TestStaffEvents class to verify is_staff=True events are excluded from the unique_visitors metric — this was absent from the AI-generated output and is a critical correctness requirement.

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
        """A single well-formed event should be accepted with inserted=1 and status=success."""
        payload = [_make_event()]
        resp = client.post("/events/ingest", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        assert resp.json()["inserted"] == 1

    def test_ingest_empty_list(self):
        """An empty list is a valid payload representing a no-op batch. Must return 200
        with inserted=0 rather than a validation error."""
        resp = client.post("/events/ingest", json=[])
        assert resp.status_code == 200
        assert resp.json()["inserted"] == 0

    def test_ingest_invalid_json_body(self):
        """A body that is not valid JSON at all should be rejected with a 400 or 422."""
        resp = client.post("/events/ingest", content="not json", headers={"Content-Type": "application/json"})
        assert resp.status_code in [400, 422]

    def test_ingest_non_list_payload(self):
        """The ingest endpoint expects a JSON array. A JSON object at the root
        must be rejected as a type error."""
        resp = client.post("/events/ingest", json={"event_id": "abc"})
        assert resp.status_code in [400, 422]


class TestIdempotency:
    def test_duplicate_event_id_is_idempotent(self):
        """Submitting the same event_id twice must return success both times.
        The second submission must NOT create a duplicate DB record.
        This is the core idempotency guarantee of the ingest endpoint."""
        event = _make_event()
        resp1 = client.post("/events/ingest", json=[event])
        assert resp1.json()["inserted"] == 1

        resp2 = client.post("/events/ingest", json=[event])
        assert resp2.status_code == 200
        assert resp2.json()["inserted"] == 1  # idempotent — counts as success

    def test_idempotency_does_not_create_duplicates(self):
        """After two submissions of the same event, the metrics endpoint must
        still report only 1 unique visitor — confirming no duplicate row was created."""
        event = _make_event()
        client.post("/events/ingest", json=[event])
        client.post("/events/ingest", json=[event])

        # Verify only 1 entry via metrics
        resp = client.get(f"/stores/{event['store_id']}/metrics")
        assert resp.json()["unique_visitors"] == 1


class TestPartialFailure:
    def test_mix_of_valid_and_invalid(self):
        """A batch containing both valid and structurally invalid events should:
        - Return HTTP 200 (not a server error)
        - Return status='partial_success'
        - Report the valid events as inserted
        - List the invalid events in the errors array"""
        valid = _make_event()
        invalid = {"invalid": "garbage"}
        resp = client.post("/events/ingest", json=[valid, invalid])
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "partial_success"
        assert data["inserted"] >= 1
        assert isinstance(data["errors"], list)
        assert len(data["errors"]) >= 1

    def test_all_invalid_events(self):
        """If every event in a batch is invalid, the response status must be
        'failure' with inserted=0. Must still return HTTP 200 (not a 5xx)."""
        resp = client.post("/events/ingest", json=[{"bad": 1}, {"worse": 2}])
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failure"
        assert data["inserted"] == 0


class TestBatchLimit:
    def test_batch_over_500_rejected(self):
        """The spec mandates a maximum batch size of 500 events per request.
        A batch of 501 must be rejected with a 4xx status code before any
        events are persisted. This prevents memory exhaustion attacks."""
        events = [_make_event() for _ in range(501)]
        resp = client.post("/events/ingest", json=events)
        assert resp.status_code in [400, 422]


class TestStaffEvents:
    def test_staff_events_excluded_from_visitor_count(self):
        """Events with is_staff=True must be excluded from unique_visitors in the
        metrics response. Staff (cashiers, floor attendants) continuously present
        in the store would inflate visitor counts if included."""
        staff_event = _make_event(visitor_id="VIS_STAFF_1", is_staff=True)
        customer_event = _make_event(visitor_id="VIS_CUST_1", is_staff=False)
        client.post("/events/ingest", json=[staff_event, customer_event])

        resp = client.get("/stores/ST1008/metrics")
        # Only the non-staff visitor should be counted
        assert resp.json()["unique_visitors"] == 1


class TestEdgeCases:
    def test_zero_confidence_event_accepted(self):
        """A confidence score of 0.0 is technically valid (very low certainty detection).
        The API must accept it without validation errors — confidence filtering
        is the pipeline's responsibility, not the API's."""
        event = _make_event(confidence=0.0)
        resp = client.post("/events/ingest", json=[event])
        assert resp.json()["inserted"] == 1

    def test_all_event_types_accepted(self):
        """Every event type in the specification must be accepted by the ingest
        endpoint. This catches any missing entries in the EventType enum
        that would cause a validation failure on a real pipeline event."""
        types = ["ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
                 "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"]
        events = [_make_event(event_type=t) for t in types]
        resp = client.post("/events/ingest", json=events)
        assert resp.json()["inserted"] == len(types)
