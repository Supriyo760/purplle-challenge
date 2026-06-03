from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Dict, Any
from .db import get_db, DBEvent
from .models import DetectionEvent
from pydantic import ValidationError

router = APIRouter()

@router.post("/events/ingest")
async def ingest_events(request: Request, db: Session = Depends(get_db)):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(payload, list):
        raise HTTPException(status_code=400, detail="Payload must be a list of events")

    if len(payload) > 500:
        raise HTTPException(status_code=400, detail="Batch size exceeds limit of 500 events")

    successful_inserts = 0
    errors = []
    
    valid_events = []
    
    # 1. Parse and validate all events
    for idx, raw_event in enumerate(payload):
        try:
            event = DetectionEvent(**raw_event)
            valid_events.append((idx, raw_event, event))
        except ValidationError as ve:
            errors.append({"index": idx, "event_id": raw_event.get("event_id"), "error": ve.errors()})
        except Exception as e:
            errors.append({"index": idx, "event_id": raw_event.get("event_id"), "error": str(e)})

    if valid_events:
        event_ids = [ev.event_id for _, _, ev in valid_events]
        
        # 2. Bulk check idempotency
        try:
            existing_rows = db.query(DBEvent.event_id).filter(DBEvent.event_id.in_(event_ids)).all()
            existing_ids = {row[0] for row in existing_rows}
            
            new_db_events = []
            for idx, raw_event, event in valid_events:
                if event.event_id in existing_ids:
                    # Already processed, consider it successful for idempotency
                    successful_inserts += 1
                    continue
                    
                # Note: using model_dump() for Pydantic V2 instead of deprecated dict()
                metadata_dict = event.metadata.model_dump() if event.metadata else None
                
                db_event = DBEvent(
                    event_id=event.event_id,
                    store_id=event.store_id,
                    camera_id=event.camera_id,
                    visitor_id=event.visitor_id,
                    event_type=event.event_type,
                    timestamp=event.timestamp,
                    zone_id=event.zone_id,
                    dwell_ms=event.dwell_ms,
                    is_staff=event.is_staff,
                    confidence=event.confidence,
                    metadata_json=metadata_dict
                )
                new_db_events.append(db_event)
                
            # 3. Bulk insert new events
            if new_db_events:
                db.bulk_save_objects(new_db_events)
                db.commit()
                successful_inserts += len(new_db_events)
                
        except IntegrityError:
            db.rollback()
            # Race condition: Another request inserted an ID. Fallback to single inserts.
            for idx, raw_event, event in valid_events:
                if event.event_id in existing_ids:
                    continue # already counted
                try:
                    metadata_dict = event.metadata.model_dump() if event.metadata else None
                    db_event = DBEvent(
                        event_id=event.event_id, store_id=event.store_id, camera_id=event.camera_id,
                        visitor_id=event.visitor_id, event_type=event.event_type, timestamp=event.timestamp,
                        zone_id=event.zone_id, dwell_ms=event.dwell_ms, is_staff=event.is_staff,
                        confidence=event.confidence, metadata_json=metadata_dict
                    )
                    db.add(db_event)
                    db.commit()
                    successful_inserts += 1
                except IntegrityError:
                    db.rollback()
                    successful_inserts += 1 # idempotency
                except Exception as e:
                    db.rollback()
                    errors.append({"index": idx, "event_id": raw_event.get("event_id"), "error": "Fallback insert failed"})
                    
        except Exception as e:
            db.rollback()
            errors.append({"error": "Bulk insert transaction failed"})

    # Log event count for ingest as required
    request.state.event_count = successful_inserts

    if errors:
        return {
            "status": "partial_success" if successful_inserts > 0 else "failure",
            "inserted": successful_inserts,
            "errors": errors
        }

    return {"status": "success", "inserted": successful_inserts}
