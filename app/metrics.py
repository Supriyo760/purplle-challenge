from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone, date
from typing import Dict, Any
from .db import get_db, DBEvent, DBTransaction
from .models import EventType
from datetime import timedelta
import json
import os

router = APIRouter()

def get_today_bounds():
    # In a real app, this would be based on the local timezone of the store.
    # For this challenge, we will just use the current UTC date.
    # Or to make it work with the sample data date (April 10, 2026), we could find the max date in DB.
    return None # We will filter by the date of the most recent event for robustness in testing

@router.get("/stores/{store_id}/metrics")
def get_store_metrics(store_id: str, db: Session = Depends(get_db)):
    # Find the date of the most recent event to simulate "Today" for the static dataset
    latest_event = db.query(func.max(DBEvent.timestamp)).filter(DBEvent.store_id == store_id).scalar()
    
    if not latest_event:
         return {
            "unique_visitors": 0,
            "conversion_rate": 0.0,
            "avg_dwell_per_zone": {},
            "queue_depth": 0,
            "abandonment_rate": 0.0
        }

    # "Today" bounds
    today_start = latest_event.replace(hour=0, minute=0, second=0, microsecond=0)
    
    base_query = db.query(DBEvent).filter(
        DBEvent.store_id == store_id,
        DBEvent.is_staff == False,
        DBEvent.timestamp >= today_start
    )

    # 1. Unique visitors today
    # Entry visitors = those with explicit ENTRY events (from entry cameras like CAM 1)
    entry_visitor_ids = set(
        r[0] for r in base_query.filter(DBEvent.event_type == EventType.ENTRY)
        .with_entities(DBEvent.visitor_id).distinct().all()
    )
    
    # Orphan visitors = those seen in zones but never had an ENTRY event
    # (detected by non-entry cameras like CAM 2, CAM 3, etc.)
    all_zone_visitor_ids = set(
        r[0] for r in base_query.filter(
            DBEvent.event_type.in_([EventType.ZONE_ENTER, EventType.BILLING_QUEUE_JOIN])
        ).with_entities(DBEvent.visitor_id).distinct().all()
    )
    
    # Store-level unique visitors = union of entry + orphan
    unique_visitors = len(entry_visitor_ids | all_zone_visitor_ids)

    # 2. Avg dwell per zone
    # ZONE_DWELL events happen every 30s. Or we can just sum dwell_ms per zone per visitor and average.
    # The requirement asks for avg dwell per zone.
    zone_dwells = base_query.filter(DBEvent.event_type == EventType.ZONE_DWELL, DBEvent.zone_id.isnot(None))\
        .with_entities(DBEvent.zone_id, func.avg(DBEvent.dwell_ms)).group_by(DBEvent.zone_id).all()
    
    avg_dwell_per_zone = {z[0]: float(z[1]) for z in zone_dwells}

    # 3. Queue Depth (Current)
    # Find the most recent BILLING_QUEUE_JOIN or EXIT to estimate. 
    # For simplicity, let's take the latest BILLING_QUEUE_JOIN metadata queue_depth.
    latest_queue_event = base_query.filter(DBEvent.event_type == EventType.BILLING_QUEUE_JOIN)\
        .order_by(DBEvent.timestamp.desc()).first()
    
    queue_depth = 0
    if latest_queue_event and latest_queue_event.metadata_json:
        queue_depth = latest_queue_event.metadata_json.get("queue_depth", 0)

    # 4. Conversion Rate & Abandonment Rate
    # Find total unique visitors who entered billing
    billing_visitors = base_query.filter(
        DBEvent.zone_id == "BILLING",
        DBEvent.event_type.in_([EventType.ZONE_ENTER, EventType.ZONE_DWELL, EventType.BILLING_QUEUE_JOIN])
    ).all()
    
    unique_billing_visitors = len(set([v.visitor_id for v in billing_visitors]))
    
    # Conversion Rate = Visitors who completed a purchase / Total unique visitors
    conversion_rate = 0.0
    converted_visitors = set()
    
    if unique_visitors > 0:
        transactions = db.query(DBTransaction.timestamp).filter(
            DBTransaction.store_id == store_id,
            DBTransaction.timestamp >= today_start
        ).all()
        
        transaction_times = [t[0] for t in transactions]
        
        if transaction_times:
            import bisect
            transaction_times.sort()
            
            for v in billing_visitors:
                v_time = v.timestamp
                idx = bisect.bisect_left(transaction_times, v_time)
                if idx < len(transaction_times):
                    t_time = transaction_times[idx]
                    if t_time <= v_time + timedelta(minutes=5):
                        converted_visitors.add(v.visitor_id)
            
            conversion_rate = len(converted_visitors) / unique_visitors

    purchasers = len(converted_visitors)
    abandonment_rate = ((unique_billing_visitors - purchasers) / unique_billing_visitors) if unique_billing_visitors > 0 else 0.0

    # Data confidence: flag when multiple cameras have fed data, since we lack
    # cross-camera Re-ID (appearance embeddings like OSNet). Visitor counts may
    # be inflated because the same physical person gets different track IDs on
    # different cameras.
    distinct_cameras = db.query(func.count(func.distinct(DBEvent.camera_id))).filter(
        DBEvent.store_id == store_id,
        DBEvent.timestamp >= today_start
    ).scalar() or 0

    if distinct_cameras > 1:
        data_confidence = "LOW"
        confidence_reason = "Cross-camera deduplication not implemented; visitor counts may be inflated across camera angles"
    else:
        data_confidence = "HIGH"
        confidence_reason = "Single camera source — no cross-camera duplication risk"

    return {
        "unique_visitors": unique_visitors,
        "conversion_rate": conversion_rate,
        "avg_dwell_per_zone": avg_dwell_per_zone,
        "queue_depth": queue_depth,
        "abandonment_rate": abandonment_rate,
        "data_confidence": data_confidence,
        "confidence_reason": confidence_reason
    }
