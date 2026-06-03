# Store Intelligence Platform — Architecture Design

## 1. System Overview

The Store Intelligence Platform is a production-grade, end-to-end retail analytics system. It processes raw CCTV footage through a Computer Vision (CV) pipeline, converts detections into a structured event stream, and exposes that stream as a RESTful Intelligence API with a live web dashboard.

The system is designed around two primary concerns:
- **Accuracy at the edge:** correctly identifying, filtering, and tracking people in a noisy retail environment (reflective surfaces, product posters, TV displays, staff)
- **Correctness at the API layer:** aggregating raw events into business-meaningful metrics, with honest confidence signals when data quality is limited

---

## 2. Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                     Edge Processing Layer                         │
│                                                                   │
│  ┌──────────┐    ┌──────────────┐    ┌──────────┐   ┌─────────┐  │
│  │  CCTV    │───▶│  YOLOv8n    │───▶│ByteTrack │───▶│Tracker  │  │
│  │ Footage  │    │  Detection   │    │  (YAML)  │   │  Logic  │  │
│  └──────────┘    └──────────────┘    └──────────┘   └────┬────┘  │
│                                                           │       │
│  Filters applied: bbox size, motion distance, track duration,     │
│  frame visibility ratio (staff heuristic)                │       │
│                                                      EventEmitter │
└──────────────────────────────────────────────────────────┼────────┘
                                                           │ HTTP POST
                                                           ▼ (batches ≤500)
┌───────────────────────────────────────────────────────────────────┐
│                     Intelligence API Layer                        │
│                                                                   │
│  POST /events/ingest ────▶ Idempotency Check ────▶ SQLite DB     │
│                                                       │           │
│         GET /stores/{id}/metrics  ◀───────────────────┤           │
│         GET /stores/{id}/funnel   ◀───────────────────┤           │
│         GET /stores/{id}/heatmap  ◀───────────────────┤           │
│         GET /stores/{id}/anomalies ◀──────────────────┤           │
│         GET /health               ◀───────────────────┘           │
│                                                                   │
│  POS Transactions ────▶ Time-Window Correlation (±5 min)         │
└──────────────────────────────────────────────────────────────────┘
                            │
                            ▼
              Live Web Dashboard (localhost:8000)
              Polls all endpoints every 3 seconds
```

---

## 3. Key Component Decisions

### 3.1 CV Pipeline (`pipeline/`)

| Component | Technology | Rationale |
|---|---|---|
| Person Detection | YOLOv8 Nano | Fastest YOLO variant; sufficient accuracy for retail; fits in GTX 1650 VRAM |
| Tracking | ByteTrack (custom YAML) | More stable ID retention than BoT-SORT for low-texture retail environments |
| Event Emission | Batched HTTP with Dead Letter Queue | Absorbs short API downtime without losing events; configurable batch size |

**Filtering pipeline (in order of application):**
1. **Bounding box size filter:** Detections smaller than 1.5% of frame area are rejected (product faces, distant reflections)
2. **Motion + duration filter:** Object must move >5% of frame diagonal AND persist ≥1.5 seconds (eliminates TV screens with moving content)
3. **Staff heuristic:** Any tracker ID visible for >60% of total processed frames is tagged `is_staff=True` and excluded from customer metrics

### 3.2 Intelligence API (`app/`)

Built with **FastAPI + SQLAlchemy + SQLite**. The endpoint design follows a thin-service pattern:
- Ingestion (`ingestion.py`) is responsible only for validation and persistence
- Each analytics endpoint (`metrics.py`, `funnel.py`, `heatmap.py`, `anomalies.py`) is a stateless SQL query layer
- No shared in-memory state between requests — all reads go to the DB

### 3.3 Cross-Camera Deduplication

The system does not implement appearance-based Re-ID (e.g., OSNet). See Section 4.2 for the full rationale. Instead:
- Each camera produces independent `visitor_id` values
- Visitors detected on non-entry cameras (no `ENTRY` event) are counted as "orphan visitors" and included in the store-level unique visitor count to avoid the logically impossible state of `unique_visitors=0, zone_visits=N`
- The `/metrics` endpoint returns `data_confidence: "LOW"` when multi-camera data is present, with a human-readable `confidence_reason`

---

## 4. AI-Assisted Decisions

Throughout this project, LLMs were used to accelerate development of boilerplate, draft tests, and evaluate tradeoffs. Below are three specific decisions where AI input materially shaped (or was overridden in) the final design.

### 4.1 Tracker Algorithm: ByteTrack vs. BoT-SORT

**AI Suggestion:** When asked to reduce ID switches (the primary cause of overcounting), the AI recommended switching to **BoT-SORT** with Camera Motion Compensation (CMC) enabled, citing its superior performance on the MOT17 benchmark.

**What happened in practice:** I implemented this recommendation (tracker_type: botsort in the YAML config), ran the pipeline, and got `WARNING: not enough matching points` on every frame. BoT-SORT's CMC algorithm requires a sufficiently textured background to compute sparse optical flow for camera stabilization. The retail store environment — with large, flat white walls and product shelving units — provided too little texture for the algorithm to find keypoints. The result was *more* ID switches than before, not fewer.

**My Decision: Override.** I reverted to ByteTrack, but fixed the actual root cause of overcounting: premature track cleanup (our stale threshold was shorter than ByteTrack's internal buffer), oversensitive motion validation (accepting TV screen flicker), and undersized bounding box filtering (accepting product face detections). After fixing these root causes, ByteTrack produced accurate results.

**Key learning:** Benchmark performance (MOT17 indoors) does not transfer directly to low-texture retail CCTV. Always validate AI recommendations empirically on your specific environment.

---

### 4.2 Cross-Camera Re-ID: OSNet vs. Naive Edge Tracking

**AI Suggestion:** For multi-camera visitor deduplication, the AI strongly recommended implementing **DeepSORT with an OSNet-based appearance embedding model** to match visitors across cameras via cosine similarity on feature vectors.

**My Decision: Override for hardware and scope reasons.** The GTX 1650 has 4GB of VRAM. Running YOLOv8 tracking already consumes ~2.5GB. Adding OSNet inference per-detected-person would either:
- (a) Cause VRAM exhaustion and crash the pipeline, or
- (b) Require running on CPU at ~200ms per embedding, making the pipeline run at <5 FPS — far below real-time.

Instead, I chose an honest approach: accept the cross-camera inflation, and surface it explicitly via the `data_confidence` field in the API response. In a true production system with a dedicated GPU for Re-ID, OSNet would be the correct choice. For this prototype on constrained hardware, acknowledging the limitation is more valuable than implementing a broken solution.

---

### 4.3 Event Schema: Flat vs. Nested JSON

**AI Suggestion:** The AI drafted a deeply nested event schema:
```json
{
  "location": { "zone": { "id": "SKINCARE", "name": "Skincare" } },
  "tracking": { "confidence": { "score": 0.91 } },
  "session": { "visitor": { "id": "VIS_abc" } }
}
```
The rationale was "RESTful best practices" and "extensibility."

**My Decision: Override.** A flat schema with a single `metadata` blob was chosen:
```json
{
  "event_id": "...", "visitor_id": "VIS_abc",
  "zone_id": "SKINCARE", "confidence": 0.91,
  "metadata": { "group_size": 1, "sku_zone": "MOISTURISER" }
}
```
**Why:** Flat schemas map directly to columnar databases (ClickHouse, BigQuery) which are the industry standard for high-volume event analytics. Every additional level of nesting in JSON requires a corresponding `JSON_EXTRACT()` operation at query time, destroying query performance at scale. The `metadata` blob handles event-specific variations without requiring schema migrations.

---

## 5. Reflection on AI Assistance

AI tools were most valuable for:
- Generating the FastAPI boilerplate, SQLAlchemy model definitions, and Pydantic validators (≈60% of `app/` written by AI, then reviewed)
- Drafting the initial pytest suites, which I then corrected for our specific DB fixture setup and exact response schema
- Evaluating algorithmic tradeoffs (ByteTrack vs BoT-SORT, SQLite vs PostgreSQL)

AI tools were least reliable for:
- Predicting real-world performance on constrained hardware (the BoT-SORT failure is the clearest example)
- Understanding the specific nuances of the retail CCTV environment (poster false positives, TV screen detections)
- Threshold calibration — every numeric threshold (staff detection at 60% visibility, queue spike at depth>5, dead zone at 30 minutes) required empirical testing against the provided footage, not AI estimation
