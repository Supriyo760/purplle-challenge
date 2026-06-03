# PROMPT: Generate pytest tests for a zone heatmap API endpoint (GET /stores/{id}/heatmap). The heatmap returns zone_id, raw_visits, raw_avg_dwell_ms, normalised scores (0–100), and a data_confidence flag ("HIGH" if >= 20 sessions, "LOW" otherwise). Test normalization math, empty store response, data_confidence thresholds, and that all zones with any activity appear in the output.
# CHANGES MADE: Adjusted the session count threshold to match our implementation (20 sessions), verified that the normalization correctly maps the highest-traffic zone to 100, and added a test for the LOW confidence flag with sparse data.

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import uuid
from datetime import datetime, timezone

from app.main import app
from app.db import Base, get_db, DBEvent
from app.models import EventType

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_heatmap.db"
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

def _insert_event(db, **kwargs):
    defaults = {
        "event_id": str(uuid.uuid4()),
        "store_id": "ST1008",
        "camera_id": "CAM1",
        "visitor_id": f"VIS_{uuid.uuid4().hex[:6]}",
        "event_type": EventType.ZONE_ENTER,
        "timestamp": datetime.now(timezone.utc),
        "is_staff": False,
        "confidence": 0.9,
    }
    defaults.update(kwargs)
    ev = DBEvent(**defaults)
    db.add(ev)
    db.commit()
    return ev


class TestEmptyHeatmap:
    def test_empty_store_returns_empty_heatmap(self):
        resp = client.get("/stores/ST1008/heatmap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["heatmap"] == []
        assert data["data_confidence"] == "LOW"
        assert data["total_sessions"] == 0


class TestHeatmapData:
    def test_zones_appear_in_heatmap(self):
        db = TestingSessionLocal()
        _insert_event(db, zone_id="SKINCARE")
        _insert_event(db, zone_id="MAKEUP")
        db.close()

        resp = client.get("/stores/ST1008/heatmap")
        zones = [z["zone_id"] for z in resp.json()["heatmap"]]
        assert "SKINCARE" in zones
        assert "MAKEUP" in zones

    def test_normalization_max_is_100(self):
        db = TestingSessionLocal()
        # SKINCARE gets 3 visits, MAKEUP gets 1
        for _ in range(3):
            _insert_event(db, zone_id="SKINCARE")
        _insert_event(db, zone_id="MAKEUP")
        db.close()

        resp = client.get("/stores/ST1008/heatmap")
        heatmap = resp.json()["heatmap"]
        skincare = [z for z in heatmap if z["zone_id"] == "SKINCARE"][0]
        makeup = [z for z in heatmap if z["zone_id"] == "MAKEUP"][0]

        assert skincare["norm_visits"] == 100.0
        assert 0 < makeup["norm_visits"] < 100


class TestDataConfidence:
    def test_low_confidence_with_few_sessions(self):
        db = TestingSessionLocal()
        for i in range(5):
            _insert_event(db, visitor_id=f"VIS_{i}", zone_id="SKINCARE")
        db.close()

        resp = client.get("/stores/ST1008/heatmap")
        assert resp.json()["data_confidence"] == "LOW"

    def test_high_confidence_with_many_sessions(self):
        db = TestingSessionLocal()
        for i in range(25):
            _insert_event(db, visitor_id=f"VIS_{i}", zone_id="SKINCARE")
        db.close()

        resp = client.get("/stores/ST1008/heatmap")
        assert resp.json()["data_confidence"] == "HIGH"


class TestHeatmapStructure:
    def test_heatmap_entry_has_required_fields(self):
        db = TestingSessionLocal()
        _insert_event(db, zone_id="SKINCARE")
        db.close()

        resp = client.get("/stores/ST1008/heatmap")
        entry = resp.json()["heatmap"][0]
        assert "zone_id" in entry
        assert "raw_visits" in entry
        assert "raw_avg_dwell_ms" in entry
        assert "norm_visits" in entry
        assert "norm_dwell" in entry
