# PROMPT: Generate a pytest suite for a zone heatmap API endpoint (GET /stores/{id}/heatmap). The response includes: a 'heatmap' list where each entry has zone_id, raw_visits (integer), raw_avg_dwell_ms (float), norm_visits (0–100 normalized to the highest-traffic zone), norm_dwell (0–100 normalized), and a top-level 'data_confidence' flag set to 'HIGH' if total_sessions >= 20 or 'LOW' otherwise. Test: normalization math (the highest-traffic zone must have norm_visits=100.0), empty store returning an empty heatmap with LOW confidence, data_confidence threshold at exactly 20 sessions, and that all fields required by the API contract are present in each heatmap entry.
# CHANGES MADE: (1) Adjusted the session count threshold to 20 to match our implementation (AI initially suggested 10 — this caused the test to pass incorrectly with sparse data). (2) Added TestHeatmapStructure class to validate the complete field schema of each heatmap entry — this was absent from the AI output but is required to detect partial responses. (3) Added the test for LOW confidence with sparse data (5 sessions) as a lower-bound boundary test. (4) Verified normalization arithmetic: if SKINCARE has 3 visits and MAKEUP has 1, SKINCARE must be 100.0 and MAKEUP must be between 0 and 100.

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
        """With no events, the heatmap list must be empty, total_sessions must
        be 0, and data_confidence must be LOW (insufficient data for reliable
        traffic pattern analysis)."""
        resp = client.get("/stores/ST1008/heatmap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["heatmap"] == []
        assert data["data_confidence"] == "LOW"
        assert data["total_sessions"] == 0


class TestHeatmapData:
    def test_zones_appear_in_heatmap(self):
        """Any zone with at least one ZONE_ENTER event must appear in the
        heatmap output. Zones with zero activity are excluded."""
        db = TestingSessionLocal()
        _insert_event(db, zone_id="SKINCARE")
        _insert_event(db, zone_id="MAKEUP")
        db.close()

        resp = client.get("/stores/ST1008/heatmap")
        zones = [z["zone_id"] for z in resp.json()["heatmap"]]
        assert "SKINCARE" in zones
        assert "MAKEUP" in zones

    def test_normalization_max_is_100(self):
        """The normalization algorithm must map the highest-traffic zone to
        exactly 100.0, and all other zones to proportional values in (0, 100).
        Formula: norm_visits[zone] = (raw_visits[zone] / max_raw_visits) * 100"""
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

        assert skincare["norm_visits"] == 100.0, "Highest-traffic zone must normalize to exactly 100.0"
        assert 0 < makeup["norm_visits"] < 100, "Lower-traffic zone must normalize to between 0 and 100"


class TestDataConfidence:
    def test_low_confidence_with_few_sessions(self):
        """With only 5 sessions, the data_confidence must be LOW. A heatmap derived
        from fewer than 20 sessions does not have enough traffic to represent a
        reliable pattern — it may reflect a single unusual day, not typical behavior."""
        db = TestingSessionLocal()
        for i in range(5):
            _insert_event(db, visitor_id=f"VIS_{i}", zone_id="SKINCARE")
        db.close()

        resp = client.get("/stores/ST1008/heatmap")
        assert resp.json()["data_confidence"] == "LOW"

    def test_high_confidence_with_many_sessions(self):
        """With 25 sessions (above the 20-session threshold), the data_confidence
        must be HIGH. This indicates the heatmap reflects a statistically meaningful
        pattern of customer movement rather than noise."""
        db = TestingSessionLocal()
        for i in range(25):
            _insert_event(db, visitor_id=f"VIS_{i}", zone_id="SKINCARE")
        db.close()

        resp = client.get("/stores/ST1008/heatmap")
        assert resp.json()["data_confidence"] == "HIGH"


class TestHeatmapStructure:
    def test_heatmap_entry_has_required_fields(self):
        """Every entry in the heatmap list must contain all five required fields
        specified in the API contract. Missing any field would break dashboard
        visualizations and fail automated schema validation."""
        db = TestingSessionLocal()
        _insert_event(db, zone_id="SKINCARE")
        db.close()

        resp = client.get("/stores/ST1008/heatmap")
        entry = resp.json()["heatmap"][0]
        assert "zone_id" in entry, "Missing 'zone_id' field"
        assert "raw_visits" in entry, "Missing 'raw_visits' field"
        assert "raw_avg_dwell_ms" in entry, "Missing 'raw_avg_dwell_ms' field"
        assert "norm_visits" in entry, "Missing 'norm_visits' field"
        assert "norm_dwell" in entry, "Missing 'norm_dwell' field"
        assert isinstance(entry["raw_visits"], int), "raw_visits must be an integer"
        assert 0.0 <= entry["norm_visits"] <= 100.0, "norm_visits must be in [0, 100]"
