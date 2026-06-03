from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
from .db import get_db, DBEvent

router = APIRouter()

@router.get("/health")
def get_health(db: Session = Depends(get_db)):
    # Get max timestamp per store
    store_latest = db.query(DBEvent.store_id, func.max(DBEvent.timestamp))\
        .group_by(DBEvent.store_id).all()
        
    stores_status = {}
    stale_feeds = False
    
    # In a real system, we compare against datetime.now(timezone.utc)
    # Since we are using static data, we find the global max timestamp to act as "now"
    global_latest = db.query(func.max(DBEvent.timestamp)).scalar()
    now = global_latest if global_latest else datetime.now(timezone.utc)
    
    for store_id, latest_ts in store_latest:
        lag = now - latest_ts
        is_stale = lag > timedelta(minutes=10)
        if is_stale:
            stale_feeds = True
            
        stores_status[store_id] = {
            "last_event_timestamp": latest_ts,
            "lag_seconds": int(lag.total_seconds()),
            "status": "STALE_FEED" if is_stale else "OK"
        }

    return {
        "service": "Store Intelligence API",
        "status": "WARN" if stale_feeds else "OK",
        "stores": stores_status
    }
