import requests
import uuid
from datetime import datetime, timezone

class EventEmitter:
    def __init__(self, api_url, store_id, camera_id, batch_size=50):
        self.api_url = f"{api_url}/events/ingest"
        self.store_id = store_id
        self.camera_id = camera_id
        self.batch_size = batch_size
        self.event_buffer = []
        self.recover_dlq()

    def recover_dlq(self):
        import os, json
        if not os.path.exists("dlq.json"):
            return
            
        print("Found Dead-Letter Queue. Attempting recovery...")
        recovered_events = []
        try:
            with open("dlq.json", "r") as f:
                for line in f:
                    if line.strip():
                        recovered_events.append(json.loads(line))
        except Exception as e:
            print(f"Failed to read DLQ: {e}")
            return
            
        if not recovered_events:
            return
            
        # Try to send them in batches
        try:
            for i in range(0, len(recovered_events), self.batch_size):
                batch = recovered_events[i:i + self.batch_size]
                response = requests.post(self.api_url, json=batch, timeout=10)
                response.raise_for_status()
            
            # If all succeed, delete the DLQ
            os.remove("dlq.json")
            print(f"Successfully recovered {len(recovered_events)} events from DLQ.")
        except requests.exceptions.RequestException as e:
            print(f"DLQ recovery failed, API might still be down: {e}")

    def emit(self, visitor_id, event_type, zone_id=None, dwell_ms=0, is_staff=False, confidence=1.0, metadata=None):
        # We generate a UTC timestamp for right now. 
        # In a real replay system, this would be derived from clip start time + frame offset.
        ts = datetime.now(timezone.utc).isoformat()
        
        event = {
            "event_id": str(uuid.uuid4()),
            "store_id": self.store_id,
            "camera_id": self.camera_id,
            "visitor_id": f"VIS_{visitor_id}",
            "event_type": event_type,
            "timestamp": ts,
            "zone_id": zone_id,
            "dwell_ms": dwell_ms,
            "is_staff": is_staff,
            "confidence": round(confidence, 2),
            "metadata": metadata or {}
        }
        
        self.event_buffer.append(event)
        
        if len(self.event_buffer) >= self.batch_size:
            self.flush()

    def flush(self):
        if not self.event_buffer:
            return
            
        try:
            response = requests.post(self.api_url, json=self.event_buffer, timeout=5)
            response.raise_for_status()
            print(f"Successfully emitted {len(self.event_buffer)} events.")
        except requests.exceptions.RequestException as e:
            print(f"Failed to emit events: {e}. Writing to Dead-Letter Queue.")
            try:
                # Append to a dead-letter queue file for future retry
                with open("dlq.json", "a") as f:
                    for ev in self.event_buffer:
                        import json
                        f.write(json.dumps(ev) + "\n")
            except Exception as dlq_e:
                print(f"CRITICAL: Failed to write to DLQ: {dlq_e}")
        
        self.event_buffer = []
