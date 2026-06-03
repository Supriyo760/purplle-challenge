from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from .db import get_db, DBEvent, DBTransaction
from .models import EventType
from datetime import timedelta

router = APIRouter()

@router.get("/stores/{store_id}/funnel")
def get_store_funnel(store_id: str, db: Session = Depends(get_db)):
    # 1. Entry (Total Unique Visitors across all cameras)
    # Explicit entries from entry cameras
    entry_ids = set(
        r[0] for r in db.query(DBEvent.visitor_id).filter(
            DBEvent.store_id == store_id, DBEvent.event_type == EventType.ENTRY, DBEvent.is_staff == False
        ).distinct().all()
    )

    # 2. Zone Visit (Visitors who entered any zone)
    zone_visit_ids = set(
        r[0] for r in db.query(DBEvent.visitor_id).filter(
            DBEvent.store_id == store_id, DBEvent.event_type == EventType.ZONE_ENTER, DBEvent.is_staff == False
        ).distinct().all()
    )
    
    # Orphan visitors = seen in zones but never had an ENTRY event (non-entry cameras)
    orphan_ids = zone_visit_ids - entry_ids
    
    # Store-level entry count = explicit entries + orphans
    entries = len(entry_ids | orphan_ids)
    zone_visits = len(zone_visit_ids)

    # 3. Billing Queue (Visitors who joined the billing queue)
    billing_queue = db.query(func.count(func.distinct(DBEvent.visitor_id)))\
        .filter(DBEvent.store_id == store_id, DBEvent.event_type == EventType.BILLING_QUEUE_JOIN, DBEvent.is_staff == False).scalar() or 0

    # 4. Purchase (Converted Visitors)
    purchases = 0
    if billing_queue > 0:
        # Get all transactions for this store today
        transactions = db.query(DBTransaction.timestamp).filter(
            DBTransaction.store_id == store_id
        ).all()
        
        transaction_times = [t[0] for t in transactions]
        
        if transaction_times:
            import bisect
            transaction_times.sort()
            
            billing_visitors = db.query(DBEvent.visitor_id, DBEvent.timestamp)\
                .filter(DBEvent.store_id == store_id, DBEvent.zone_id == "BILLING", DBEvent.is_staff == False)\
                .all()
            
            converted = set()
            for v_id, ts in billing_visitors:
                v_time = ts
                idx = bisect.bisect_left(transaction_times, v_time)
                if idx < len(transaction_times):
                    t_time = transaction_times[idx]
                    if t_time <= v_time + timedelta(minutes=5):
                        converted.add(v_id)
            purchases = len(converted)

    # Calculate drop-off percentages
    def safe_pct(part, whole):
        return round(((whole - part) / whole) * 100, 2) if whole > 0 else 0.0

    return {
        "funnel": {
            "entry": {"count": entries, "drop_off_pct": safe_pct(zone_visits, entries)},
            "zone_visit": {"count": zone_visits, "drop_off_pct": safe_pct(billing_queue, zone_visits)},
            "billing_queue": {"count": billing_queue, "drop_off_pct": safe_pct(purchases, billing_queue)},
            "purchase": {"count": purchases, "drop_off_pct": 0.0} # end of funnel
        }
    }
