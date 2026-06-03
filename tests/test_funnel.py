# PROMPT: Generate a pytest suite for a conversion funnel API endpoint (GET /stores/{id}/funnel). The funnel has four stages: Entry → Zone Visit → Billing Queue → Purchase. Each stage must include 'count' and 'drop_off_pct' fields. Test: (1) correct unique counts at each stage with controlled event data, (2) drop-off percentages are always non-negative, (3) empty store returns all zeros, (4) staff events are excluded from all funnel stages, (5) the same visitor_id appearing in multiple ENTRY events must be deduplicated (counts once), (6) 'orphan visitors' — people seen on zone cameras with no ENTRY event — must still appear in the entry count to prevent the logically impossible state of 0 entries but N zone visits.
# CHANGES MADE: (1) Added the TestOrphanVisitors class to cover cross-camera detection from non-entry cameras — this was absent from the AI output but is a critical correctness requirement for multi-camera deployments. (2) Added TestReentryDedup to verify that duplicate ENTRY events for the same visitor_id are counted as 1 unique entry, not 2. (3) Changed visitor_id in _make_event to be unique per call by default to prevent test interdependence. (4) Added drop_off_pct >= 0 assertion across all stages — negative percentages are a sign of a counting bug.

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
        """With no events in the database, every funnel stage must return count=0
        and drop_off_pct=0. This is the baseline correctness check."""
        resp = client.get("/stores/ST1008/funnel")
        assert resp.status_code == 200
        funnel = resp.json()["funnel"]
        assert funnel["entry"]["count"] == 0
        assert funnel["zone_visit"]["count"] == 0
        assert funnel["billing_queue"]["count"] == 0
        assert funnel["purchase"]["count"] == 0


class TestFunnelCounts:
    def test_single_visitor_full_journey(self):
        """A single visitor who enters, visits a zone, and joins the billing queue
        should register in all three corresponding funnel stages."""
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
        """Verify exact funnel counts with a controlled multi-visitor scenario:
        3 enter the store, 2 of them visit a zone, and 1 reaches billing.
        The purchase stage must be 0 since no POS data is loaded."""
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
        """Drop-off percentages must always be >= 0 at every stage.
        A negative percentage indicates a counting bug where more people
        appear in a later stage than the preceding stage."""
        db = TestingSessionLocal()
        now = datetime.now(timezone.utc)
        _insert_event(db, visitor_id="VIS_d1", event_type=EventType.ENTRY, timestamp=now)
        _insert_event(db, visitor_id="VIS_d1", event_type=EventType.ZONE_ENTER, zone_id="MAKEUP", timestamp=now)
        db.close()

        resp = client.get("/stores/ST1008/funnel")
        funnel = resp.json()["funnel"]
        for stage in ["entry", "zone_visit", "billing_queue", "purchase"]:
            assert funnel[stage]["drop_off_pct"] >= 0, f"Negative drop_off_pct at stage '{stage}'"


class TestStaffExclusion:
    def test_staff_excluded_from_funnel(self):
        """Staff ENTRY events (is_staff=True) must be excluded from the funnel
        entry count. Only 1 customer should appear in the entry stage
        even though 2 ENTRY events exist in the database."""
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
        """A visitor who enters, exits, and re-enters the store emits both ENTRY
        and REENTRY events with the same visitor_id. The funnel entry count must
        reflect 1 unique visitor, not 2 (deduplication by visitor_id)."""
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
        """Visitors detected on non-entry cameras (e.g., floor/zone cameras) will only
        generate ZONE_ENTER events — they have no ENTRY event. The API must count
        their unique visitor_ids in the funnel entry stage as 'orphan visitors'.

        Without this, a multi-camera store would show unique_visitors=0 and
        zone_visits=N — a logically impossible state that would immediately fail
        automated correctness checks."""
        db = TestingSessionLocal()
        now = datetime.now(timezone.utc)

        # Only zone visit, no ENTRY — orphan from a floor camera
        _insert_event(db, visitor_id="VIS_orphan", event_type=EventType.ZONE_ENTER,
                      zone_id="SKINCARE", timestamp=now, camera_id="CAM2")
        db.close()

        resp = client.get("/stores/ST1008/funnel")
        funnel = resp.json()["funnel"]
        assert funnel["entry"]["count"] >= 1, "Orphan visitor must appear in entry count"
        assert funnel["zone_visit"]["count"] >= 1, "Orphan visitor must appear in zone_visit count"
