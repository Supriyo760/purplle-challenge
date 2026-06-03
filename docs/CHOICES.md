# Engineering Choices & Justifications

This document records the three primary engineering decisions made during implementation: which detection model to use, how to design the event schema, and which API architecture to adopt. For each decision, the options considered, AI suggestions, and the final reasoning are documented.

---

## Decision 1: Detection Model — YOLOv8 Nano + ByteTrack

### Options Considered

| Option | Pros | Cons |
|---|---|---|
| **YOLOv8n** (chosen) | Fastest inference, low VRAM, >30 FPS on GTX 1650 | Lower mAP than larger variants |
| YOLOv8m / YOLOv8l | Higher accuracy | ~3–4× more VRAM, drops to ~12 FPS on GTX 1650 — below real-time |
| Detectron2 / DETR | State-of-the-art accuracy | Extremely heavy, incompatible with real-time edge deployment |
| MediaPipe Pose | Skeleton-based counting | Not designed for tracking identity across frames |

### What AI Suggested

The AI initially recommended **YOLOv8x** (extra-large), arguing that maximum accuracy was paramount for visitor counting. It also suggested combining this with **BoT-SORT** for tracking and **OSNet** for cross-camera Re-ID via appearance embeddings.

### What I Chose and Why

**YOLOv8n + ByteTrack.** The GTX 1650 has 4GB VRAM. Running YOLOv8n tracking consumes approximately 2.5GB, leaving headroom for other processes. YOLOv8x would consume ~6GB — exceeding available VRAM entirely.

I implemented the AI's BoT-SORT recommendation and observed `WARNING: not enough matching points` on every single frame. BoT-SORT's Camera Motion Compensation requires a richly textured background to compute sparse optical flow keypoints. The retail store's flat white walls and uniform shelving provided insufficient texture. The result was *more* ID switches, not fewer. I reverted to ByteTrack.

**VLM Evaluation (Group Entry Detection):** The AI also suggested using a Vision-Language Model (VLM) to count people within merged bounding boxes by prompting: *"How many distinct people are visible in this crop?"* I evaluated this approach and rejected it for two reasons:
1. A VLM API call per-detected-group adds ~800–1200ms per frame, making real-time processing impossible.
2. The GTX 1650 cannot run a local VLM at any reasonable speed.

I instead used a bounding box area heuristic: if a detected box area exceeds 1.8× the calibrated single-person area, it is flagged as a potential group. This executes in <1ms and handles 2–3 person groups correctly approximately 80% of the time in our testing.

---

## Decision 2: Event Schema Design

### Options Considered

| Option | Description |
|---|---|
| **Flat schema with metadata blob** (chosen) | Core fields flat at root level; variable fields in a JSON `metadata` object |
| Deeply nested JSON | Each concept nested: `location.zone.id`, `tracking.confidence.score` |
| Separate endpoints per event type | `POST /events/entry`, `POST /events/zone_enter`, etc. |
| Columnar event log (append-only) | Events stored as rows in a time-series format without joins |

### What AI Suggested

The AI drafted a deeply nested schema, arguing it was "more RESTful, self-documenting, and extensible":
```json
{
  "session": { "visitor": { "id": "VIS_abc", "is_staff": false } },
  "location": { "zone": { "id": "SKINCARE", "floor": 1 } },
  "tracking": { "confidence": { "score": 0.91, "model": "yolov8n" } },
  "metadata": { "group": { "size": 1, "estimated": false } }
}
```
It also recommended creating separate ingest endpoints for each event type, stating this would "improve schema validation granularity."

### What I Chose and Why

**Flat schema with a single `/events/ingest` endpoint accepting all event types.**

The primary reason is analytics performance. In retail analytics, event data is typically queried in aggregate across millions of rows: *"How many ENTRY events occurred in the last hour per store?"* With a nested schema, this requires `JSON_EXTRACT(data, '$.session.visitor.id')` on every row — a full table scan with JSON parsing overhead. With a flat schema, this becomes `SELECT COUNT(DISTINCT visitor_id) WHERE event_type='ENTRY'` — a trivially fast indexed query.

The `metadata` JSON blob handles event-specific variations (e.g., `queue_depth` only exists on `BILLING_QUEUE_JOIN` events) without requiring either nullable columns for every possible field or database schema migrations every time a new event type is added.

A single ingest endpoint was chosen over multiple type-specific endpoints because:
1. It simplifies the pipeline emitter — one `POST` URL regardless of event type
2. It keeps ingestion logic centralized — idempotency, batch limits, and error handling are implemented once
3. It matches the pattern used by commercial analytics platforms (Segment, Mixpanel, Amplitude) for good reason

**Session sequence (`session_seq`):** Each event includes an ordinal counter within a visitor's session. This enables downstream consumers to reconstruct the exact journey order without relying on timestamp precision.

**SKU zone mapping (`sku_zone`):** Each zone is mapped to a representative SKU category (`SKINCARE→MOISTURISER`, `MAKEUP→FOUNDATION`) so that zone dwell data can be joined directly with inventory or sales data without a separate lookup table.

---

## Decision 3: API Architecture — FastAPI + SQLite vs. Alternatives

### Options Considered

| Option | Pros | Cons |
|---|---|---|
| **FastAPI + SQLite** (chosen) | Zero setup for reviewer, self-contained, fast enough for prototype scale | Not suitable for production multi-writer concurrency |
| Flask + PostgreSQL | More familiar, production-grade DB | Requires a running Postgres instance; complex setup |
| Django REST Framework | Built-in admin, ORM | Heavy, slow to boot, opinionated — poor fit for a focused API |
| FastAPI + PostgreSQL | Production-grade | Requires Docker Compose coordination; reviewer setup complexity |
| FastAPI + in-memory dict | No persistence | Events lost on restart — not acceptable for the challenge |

### What AI Suggested

The AI recommended **FastAPI + PostgreSQL** immediately, citing "production readiness" and "proper ACID transaction support for concurrent writes."

### What I Chose and Why

**FastAPI + SQLite for the prototype; documented the production migration path.**

The challenge explicitly states the evaluator will clone and run the system. Every additional service in the dependency chain (Postgres, Redis) is a potential failure point for the reviewer. SQLite with SQLAlchemy requires zero external dependencies — `docker compose up` and the API is live.

SQLite adequately handles the concurrency pattern here: the pipeline runs sequentially (one camera at a time), producing batches of events. The API is read-heavy. SQLite's WAL mode (Write-Ahead Logging) handles this pattern without contention.

**Gunicorn with Uvicorn workers:** The production CMD in the Dockerfile uses `gunicorn -k uvicorn.workers.UvicornWorker -w 4` rather than plain `uvicorn`. This provides:
- Process-level isolation (a crash in one worker doesn't kill the entire API)
- Automatic worker respawn
- Better handling of slow requests that block the event loop

**POS Correlation:** The challenge notes there is no `customer_id` in POS data. The AI suggested building a probabilistic matching model using dwell time distributions. I implemented a simpler and more auditable approach: a 5-minute time-window join. If a visitor was detected at the billing zone within 5 minutes before a POS transaction, they are counted as a purchaser. This is transparent, debuggable, and produces correct results given the data quality available.

---

## Decision 4: Where I Disagreed With AI

These are the concrete cases where AI-generated suggestions were evaluated and rejected:

| AI Suggestion | My Override | Reason |
|---|---|---|
| Use BoT-SORT for tracking | Stayed with ByteTrack | BoT-SORT's CMC failed on low-texture store walls — empirically worse results |
| Use YOLOv8x for detection | Used YOLOv8n | Hardware constraint: GTX 1650 has 4GB VRAM; YOLOv8x requires ~6GB |
| Use OSNet for cross-camera Re-ID | Documented limitation + `data_confidence` flag | VRAM exhaustion risk; honest limitation disclosure preferred over broken implementation |
| Deeply nested event JSON schema | Flat schema with metadata blob | Nested schemas are incompatible with efficient columnar database queries |
| Alert CONVERSION_DROP on any drop >5% | Used 50% threshold + ≥10 session guard | Retail conversion data is highly noisy at small sample sizes; a 5% threshold would generate constant false positives |
| Use a VLM to count people in groups | Bounding box area heuristic | VLM API latency (~1000ms/call) is incompatible with real-time pipeline requirements |
