from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from .db import get_db, DBEvent
from .models import EventType

router = APIRouter()

@router.get("/stores/{store_id}/heatmap")
def get_store_heatmap(store_id: str, db: Session = Depends(get_db)):
    # Zone visit frequency (Count of distinct visits per zone)
    zone_visits = db.query(DBEvent.zone_id, func.count(func.distinct(DBEvent.visitor_id)))\
        .filter(DBEvent.store_id == store_id, DBEvent.event_type == EventType.ZONE_ENTER, DBEvent.zone_id.isnot(None))\
        .group_by(DBEvent.zone_id).all()

    # Avg dwell per zone
    zone_dwells = db.query(DBEvent.zone_id, func.avg(DBEvent.dwell_ms))\
        .filter(DBEvent.store_id == store_id, DBEvent.event_type == EventType.ZONE_DWELL, DBEvent.zone_id.isnot(None))\
        .group_by(DBEvent.zone_id).all()

    dwell_dict = {z[0]: float(z[1]) for z in zone_dwells}
    visit_dict = {z[0]: int(z[1]) for z in zone_visits}

    # Normalization (0-100)
    max_visits = max(visit_dict.values()) if visit_dict else 1
    max_dwell = max(dwell_dict.values()) if dwell_dict else 1

    heatmap_data = []
    zones = set(list(visit_dict.keys()) + list(dwell_dict.keys()))
    
    for zone in zones:
        v = visit_dict.get(zone, 0)
        d = dwell_dict.get(zone, 0.0)
        heatmap_data.append({
            "zone_id": zone,
            "raw_visits": v,
            "raw_avg_dwell_ms": d,
            "norm_visits": round((v / max_visits) * 100, 2),
            "norm_dwell": round((d / max_dwell) * 100, 2)
        })

    # Data confidence flag (fewer than 20 sessions in window)
    total_sessions = db.query(func.count(func.distinct(DBEvent.visitor_id)))\
        .filter(DBEvent.store_id == store_id).scalar() or 0
        
    data_confidence = "HIGH" if total_sessions >= 20 else "LOW"

    return {
        "data_confidence": data_confidence,
        "total_sessions": total_sessions,
        "heatmap": heatmap_data
    }
