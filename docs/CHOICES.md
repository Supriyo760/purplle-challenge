# Engineering Choices & Justifications

## 1. Computer Vision Model: YOLOv8 Nano
**Choice:** Ultralytics YOLOv8n with built-in tracking (`model.track(persist=True)`).
**Justification:** The hardware constraint is an NVIDIA GTX 1650 (4GB VRAM). Larger models (like YOLOv8x or custom architectures) would likely exhaust VRAM or result in poor FPS. YOLOv8n achieves high FPS on edge devices while maintaining sufficient accuracy for basic person detection.

## 2. Web Framework: FastAPI
**Choice:** FastAPI with SQLAlchemy.
**Justification:** FastAPI's native integration with Pydantic makes validating the complex incoming JSON event schemas trivial. It also provides automatic Swagger documentation and is highly performant.

## 3. Cross-Camera Deduplication Strategy
**Choice:** Naive edge-based tracking without heavy cross-camera Re-ID, with an explicit `data_confidence` flag in the API response.
**Justification:** The assessment provides 5 independent video clips of ~2.5 minutes each. Implementing a robust appearance-based Re-ID model (like OSNet or DeepSort) across disjointed camera views requires a significant calibration effort and compute overhead. For this prototype, we rely on the ingestion API's `event_id` idempotency and the tracker's ability to maintain IDs within a single camera's view.

**Known limitation:** When multiple cameras feed data into the same store, visitor counts will be inflated because the same physical person receives different `visitor_id` values on each camera. We handle this honestly:
- The `/stores/{id}/metrics` endpoint returns a `data_confidence` field (`"HIGH"` for single-camera, `"LOW"` for multi-camera) and a human-readable `confidence_reason`.
- Non-entry cameras (floor/zone cameras) generate "orphan" visitors — people detected in zones without an explicit ENTRY event. These are included in the store-level visitor count to avoid the logically impossible state of 0 entries but N zone visits.
- In a true production environment, a global Re-ID service (e.g., OSNet embeddings matched across camera feeds via cosine similarity) would be necessary to stitch `VIS_123` from Camera 1 to Camera 2.

## 4. POS Correlation Logic
**Choice:** Time-window based probabilistic correlation.
**Justification:** The problem statement explicitly notes "There is no customer_id in the POS data". We implemented a time-window join: if a visitor was detected in the "BILLING" zone (via `ZONE_ENTER` or `BILLING_QUEUE_JOIN`) within a 5-minute window preceding a POS transaction timestamp, they are considered the "purchaser".

## 5. Storage: SQLite
**Choice:** Local SQLite database (`store_intelligence.db`).
**Justification:** Required zero setup for the reviewer. The application is strictly read/write heavy on a single node for this prototype, and SQLite handles the concurrency adequately for the scale of 5 concurrent clips. In production, this would be replaced by PostgreSQL (for relational data) or a Time-Series DB like ClickHouse for event analytics.

## 6. Group Entry Handling
**Group Entry Handling**: We considered three approaches — (1) pose estimation to count heads/skeletons within merged boxes, (2) a VLM prompt asking "how many people are in this bounding box", and (3) a bounding box area heuristic calibrated on solo detections.

We chose (3) because pose estimation adds ~200ms/frame latency unacceptable for real-time use, and VLM API calls per-frame are cost-prohibitive. The area heuristic handles the 2-3 person group case correctly ~80% of the time based on our testing. We flag group-split events with reduced confidence (0.8x) and include `group_size` in metadata so downstream consumers know the count is estimated.

Known failure mode: groups of 4+ people in heavy occlusion will still undercount. Acceptable for the challenge scope.

## 7. Event Schema Design Rationale
**Choice:** A flat event structure (`event_id`, `visitor_id`, `event_type`, `timestamp`, `zone_id`, `dwell_ms`) with a flexible `metadata` JSON blob.
**Options Considered:** AI suggested a deeply nested schema (e.g., `location.zone.id`, `tracking.confidence.score`) and separate API endpoints for different event types.
**What I Chose and Why:** I overrode the AI's suggestion and chose a flat schema with a single `/events/ingest` endpoint. A flat schema maps perfectly to columnar databases (like ClickHouse) which are industry standard for high-volume event analytics. The flexible `metadata` blob handles event-specific variations (like `queue_depth` for billing, or `group_size` for entries) without breaking the core schema or requiring database migrations.

## 8. AI Disagreements
While AI tools accelerated boilerplate generation, I actively disagreed with and overrode their suggestions in several key areas:
1. **Re-ID Strategy:** The AI suggested implementing DeepSORT with OSNet embeddings for cross-camera tracking. I overrode this because running a heavy Re-ID model on a GTX 1650 alongside YOLOv8 would tank the FPS below real-time requirements. I chose a naive edge-based tracker and honestly documented the multi-camera deduplication limitation via a `data_confidence` flag.
2. **Schema Design:** As mentioned above, the AI suggested a complex nested JSON schema. I overrode this for a flat, analytics-friendly schema.
3. **Anomaly Detection Logic:** For the `CONVERSION_DROP` anomaly, the AI initially suggested alerting on any drop > 5%. I overrode this to a 50% threshold with a hard guard of >= 10 recent sessions (`joins_recent >= 10`), knowing that retail conversion data is highly noisy in small sample sizes.
