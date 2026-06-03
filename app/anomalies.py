from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from .db import get_db, DBEvent
from .models import EventType

router = APIRouter()

@router.get("/stores/{store_id}/anomalies")
def get_store_anomalies(store_id: str, db: Session = Depends(get_db)):
    anomalies = []
    
    # We base "now" on the latest event timestamp for the store to simulate real-time on static data
    latest_event_ts = db.query(func.max(DBEvent.timestamp)).filter(DBEvent.store_id == store_id).scalar()
    
    if not latest_event_ts:
        return {"anomalies": []}

    # 1. Queue Spike
    # Check latest queue depth
    latest_queue = db.query(DBEvent)\
        .filter(DBEvent.store_id == store_id, DBEvent.event_type == EventType.BILLING_QUEUE_JOIN)\
        .order_by(DBEvent.timestamp.desc()).first()
        
    if latest_queue and latest_queue.metadata_json:
        q_depth = latest_queue.metadata_json.get("queue_depth", 0)
        if q_depth > 5:
            anomalies.append({
                "type": "BILLING_QUEUE_SPIKE",
                "severity": "CRITICAL" if q_depth > 10 else "WARN",
                "description": f"Billing queue depth is currently {q_depth}.",
                "suggested_action": "Open additional billing counter immediately."
            })

    # 2. Conversion Drop vs 7-day avg (Simplified mock for this exercise since we don't have 7 days of POS data)
    # We will simulate a check here. In a real system, we query historical conversion.
    # For now, we'll flag a WARN if abandonment rate is > 50% recently
    recent_window = latest_event_ts - timedelta(minutes=30)
    joins_recent = db.query(DBEvent).filter(DBEvent.store_id == store_id, DBEvent.event_type == EventType.BILLING_QUEUE_JOIN, DBEvent.timestamp >= recent_window).count()
    abandons_recent = db.query(DBEvent).filter(DBEvent.store_id == store_id, DBEvent.event_type == EventType.BILLING_QUEUE_ABANDON, DBEvent.timestamp >= recent_window).count()
    
    if joins_recent >= 10 and (abandons_recent / joins_recent) > 0.5:
        anomalies.append({
            "type": "CONVERSION_DROP",
            "severity": "WARN",
            "description": "High billing queue abandonment detected in the last 30 minutes.",
            "suggested_action": "Investigate billing delays or system issues at POS."
        })

    # 3. Dead Zone (no visits in 30 min)
    # Find all zones, then check if any had 0 visits in the last 30 minutes
    all_zones = db.query(DBEvent.zone_id).filter(DBEvent.store_id == store_id, DBEvent.zone_id.isnot(None)).distinct().all()
    all_zones = [z[0] for z in all_zones]
    
    recent_visits = db.query(DBEvent.zone_id)\
        .filter(DBEvent.store_id == store_id, DBEvent.event_type.in_([EventType.ZONE_ENTER, EventType.BILLING_QUEUE_JOIN]), DBEvent.timestamp >= recent_window, DBEvent.zone_id.isnot(None))\
        .distinct().all()
    recent_zones = [z[0] for z in recent_visits]
    
    dead_zones = set(all_zones) - set(recent_zones)
    
    for dz in dead_zones:
        anomalies.append({
            "type": "DEAD_ZONE",
            "severity": "INFO",
            "description": f"Zone {dz} has had no visitors in the last 30 minutes.",
            "suggested_action": "Verify camera feed is active and not obstructed, or restock area."
        })

    return {"anomalies": anomalies}
