# PROMPT: Generate a pytest suite for a FastAPI application that tests an event ingestion endpoint (/events/ingest) and a metrics endpoint (/stores/{id}/metrics). The ingest endpoint accepts a list of JSON events. Include tests for idempotency, partial failures, and basic metric calculations. Use TestClient and a test SQLite database.
# CHANGES MADE: I adapted the generated fixture to use our specific DB setup, added the `metadata` JSON payload structure specific to our models, and updated the metrics assertions to match our exact required fields (unique_visitors, queue_depth, etc.).

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
    response = client.get("/health")
    assert response.status_code == 200
    assert "status" in response.json()

def test_ingest_and_metrics():
    # 1. Ingest valid event
    ev1 = str(uuid.uuid4())
    payload = [{
        "event_id": ev1,
        "store_id": "ST1008",
        "camera_id": "CAM1",
        "visitor_id": "V1",
        "event_type": "ENTRY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confidence": 0.95
    }]
    
    response = client.post("/events/ingest", json=payload)
    assert response.status_code == 200
    assert response.json()["inserted"] == 1
    
    # 2. Test idempotency (ingest same again)
    response = client.post("/events/ingest", json=payload)
    assert response.status_code == 200
    assert response.json()["inserted"] == 1 # still returns 1 as it's idempotent

    # 3. Test partial failure
    bad_payload = payload + [{"invalid": "event"}]
    response = client.post("/events/ingest", json=bad_payload)
    assert response.status_code == 200
    assert response.json()["status"] == "partial_success"
    assert len(response.json()["errors"]) == 1

    # 4. Check metrics
    response = client.get("/stores/ST1008/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["unique_visitors"] == 1
    assert "queue_depth" in data

def test_funnel():
    response = client.get("/stores/ST1008/funnel")
    assert response.status_code == 200
    assert "funnel" in response.json()
    assert "entry" in response.json()["funnel"]
