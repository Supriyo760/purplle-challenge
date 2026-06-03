# PROMPT: Generate pytest tests for a store conversion funnel API endpoint (GET /stores/{id}/funnel). The funnel stages are Entry → Zone Visit → Billing Queue → Purchase. Test: correct counts at each stage, drop-off percentage calculations, empty store returning zeros, re-entry not double-counting a visitor, and that staff events are excluded from the funnel.
# CHANGES MADE: Added test for the orphan visitor fix (visitors from non-entry cameras should still appear in funnel entry count), verified drop-off percentages are non-negative, and added a zero-purchase scenario test since POS data may not be loaded.

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import uuid
from datetime import datetime, timezone

from app.main import app
from app.db import Base, get_db, DBEvent
from app.models import EventType

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_funnel.db"
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
        "visitor_id": "VIS_test1",
        "event_type": EventType.ENTRY,
        "timestamp": datetime.now(timezone.utc),
        "is_staff": False,
        "confidence": 0.9,
    }
    defaults.update(kwargs)
    ev = DBEvent(**defaults)
    db.add(ev)
    db.commit()
    return ev


class TestEmptyStore:
    def test_empty_store_returns_zero_funnel(self):
        resp = client.get("/stores/ST1008/funnel")
        assert resp.status_code == 200
        funnel = resp.json()["funnel"]
        assert funnel["entry"]["count"] == 0
        assert funnel["zone_visit"]["count"] == 0
        assert funnel["billing_queue"]["count"] == 0
        assert funnel["purchase"]["count"] == 0


class TestFunnelCounts:
    def test_single_visitor_full_journey(self):
        db = TestingSessionLocal()
        now = datetime.now(timezone.utc)
        vid = "VIS_journey1"

        _insert_event(db, visitor_id=vid, event_type=EventType.ENTRY, timestamp=now)
        _insert_event(db, visitor_id=vid, event_type=EventType.ZONE_ENTER, zone_id="SKINCARE", timestamp=now)
        _insert_event(db, visitor_id=vid, event_type=EventType.BILLING_QUEUE_JOIN, zone_id="BILLING", timestamp=now)
        db.close()

        resp = client.get("/stores/ST1008/funnel")
        funnel = resp.json()["funnel"]
        assert funnel["entry"]["count"] >= 1
        assert funnel["zone_visit"]["count"] >= 1
        assert funnel["billing_queue"]["count"] >= 1

    def test_multiple_visitors_correct_counts(self):
        db = TestingSessionLocal()
        now = datetime.now(timezone.utc)

        # 3 enter, 2 visit zones, 1 reaches billing
        for i in range(3):
            _insert_event(db, visitor_id=f"VIS_v{i}", event_type=EventType.ENTRY, timestamp=now)
        for i in range(2):
            _insert_event(db, visitor_id=f"VIS_v{i}", event_type=EventType.ZONE_ENTER, zone_id="SKINCARE", timestamp=now)
        _insert_event(db, visitor_id="VIS_v0", event_type=EventType.BILLING_QUEUE_JOIN, zone_id="BILLING", timestamp=now)
        db.close()

        resp = client.get("/stores/ST1008/funnel")
        funnel = resp.json()["funnel"]
        assert funnel["entry"]["count"] == 3
        assert funnel["zone_visit"]["count"] == 2
        assert funnel["billing_queue"]["count"] == 1
        assert funnel["purchase"]["count"] == 0  # no POS data


class TestDropOff:
    def test_drop_off_percentages_non_negative(self):
        db = TestingSessionLocal()
        now = datetime.now(timezone.utc)
        _insert_event(db, visitor_id="VIS_d1", event_type=EventType.ENTRY, timestamp=now)
        _insert_event(db, visitor_id="VIS_d1", event_type=EventType.ZONE_ENTER, zone_id="MAKEUP", timestamp=now)
        db.close()

        resp = client.get("/stores/ST1008/funnel")
        funnel = resp.json()["funnel"]
        for stage in ["entry", "zone_visit", "billing_queue", "purchase"]:
            assert funnel[stage]["drop_off_pct"] >= 0


class TestStaffExclusion:
    def test_staff_excluded_from_funnel(self):
        db = TestingSessionLocal()
        now = datetime.now(timezone.utc)

        # Staff entry — should NOT count
        _insert_event(db, visitor_id="VIS_staff", event_type=EventType.ENTRY, is_staff=True, timestamp=now)
        # Customer entry — should count
        _insert_event(db, visitor_id="VIS_cust", event_type=EventType.ENTRY, is_staff=False, timestamp=now)
        db.close()

        resp = client.get("/stores/ST1008/funnel")
        funnel = resp.json()["funnel"]
        assert funnel["entry"]["count"] == 1  # only the customer


class TestReentryDedup:
    def test_reentry_does_not_double_count(self):
        db = TestingSessionLocal()
        now = datetime.now(timezone.utc)
        vid = "VIS_reentry1"

        # Same visitor enters twice — funnel should show 1 unique entry
        _insert_event(db, visitor_id=vid, event_type=EventType.ENTRY, timestamp=now)
        _insert_event(db, visitor_id=vid, event_type=EventType.ENTRY, timestamp=now)
        db.close()

        resp = client.get("/stores/ST1008/funnel")
        funnel = resp.json()["funnel"]
        assert funnel["entry"]["count"] == 1  # distinct visitor_id


class TestOrphanVisitors:
    def test_zone_visitors_without_entry_counted(self):
        """Visitors seen on non-entry cameras (no ENTRY event) should still
        appear in the funnel entry count as orphan visitors."""
        db = TestingSessionLocal()
        now = datetime.now(timezone.utc)

        # Only zone visit, no ENTRY — orphan from a floor camera
        _insert_event(db, visitor_id="VIS_orphan", event_type=EventType.ZONE_ENTER,
                      zone_id="SKINCARE", timestamp=now, camera_id="CAM2")
        db.close()

        resp = client.get("/stores/ST1008/funnel")
        funnel = resp.json()["funnel"]
        assert funnel["entry"]["count"] >= 1  # orphan counted
        assert funnel["zone_visit"]["count"] >= 1
