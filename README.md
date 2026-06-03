# Store Intelligence Platform

A production-grade, AI-powered retail analytics system built for the Purplle Tech Challenge. The system processes CCTV footage through a YOLOv8 computer vision pipeline to detect and track shoppers, converts tracking data into a structured event stream, and exposes live retail intelligence through a RESTful API and real-time web dashboard.

---

## System Components

| Component | Description |
|---|---|
| **Intelligence API** | FastAPI backend — ingests detection events, aggregates metrics, funnels, heatmaps, and anomalies |
| **Detection Pipeline** | YOLOv8n + ByteTrack script — processes CCTV video clips, tracks visitors, filters false positives, emits JSON events |
| **Live Dashboard** | Real-time web UI polling the API every 3 seconds for live store metrics |

---

## 🚀 Live Deployment

The API and Dashboard are live and accessible over the internet:

| Resource | URL |
|---|---|
| **Live Dashboard** | **https://purplle-store-api-d8mk.onrender.com** |
| Swagger API Docs | https://purplle-store-api-d8mk.onrender.com/docs |
| GitHub Repository | https://github.com/Supriyo760/purplle-challenge |

> **Part E — Live Dashboard URL:** `https://purplle-store-api-d8mk.onrender.com`
>
> The dashboard auto-refreshes every 3 seconds. It displays: Unique Visitors, Conversion Rate, Queue Depth, Abandonment Rate, Conversion Funnel with drop-off percentages, and Active Anomalies with severity and suggested actions.

---

## Quick Start (Local Docker)

The API and Dashboard are fully containerized. No Python environment setup needed.

**Prerequisites:** Docker Desktop installed and running.

```bash
# 1. Clone the repository
git clone https://github.com/Supriyo760/purplle-challenge.git
cd purplle-challenge

# 2. Start the API
docker-compose up --build
```

Once running, the local environment is available at:

| Service | Local URL |
|---|---|
| 🖥️ **Local Dashboard** | **http://localhost:8000** |
| 📖 Swagger API Docs | http://localhost:8000/docs |
| 📄 ReDoc API Docs | http://localhost:8000/redoc |
| ❤️ Health Check | http://localhost:8000/health |

---

## Running the Detection Pipeline Against the CCTV Clips

The pipeline runs **separately** from the API. It reads the `.mp4` video clips, runs YOLOv8n tracking, applies visitor filtering, and POSTs detection events to the running API.

### Prerequisites

1. **Start the API first** (Docker, see above)
2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Place your CCTV footage clips in a folder. The expected clip names and their camera IDs are:
   ```
   CAM 1.mp4  →  Camera ID: CAM 1  (Store Entry)
   CAM 2.mp4  →  Camera ID: CAM 2
   CAM 3.mp4  →  Camera ID: CAM 3
   CAM 4.mp4  →  Camera ID: CAM 4
   CAM 5.mp4  →  Camera ID: CAM 5
   ```

### Option A — Run All Clips Automatically (Recommended)

**Windows:**
```cmd
cd pipeline
run.bat
```

**Linux / macOS:**
```bash
cd pipeline
chmod +x run.sh
./run.sh
```

This processes all 5 clips sequentially and emits events to `http://localhost:8000` as it goes. The dashboard updates in real-time.

### Option B — Run a Single Clip Manually

```bash
cd pipeline
python detect.py "<path_to_clip.mp4>" "<CAMERA_ID>"
```

**Example:**
```bash
python detect.py "../../CCTV Footage/CAM 1.mp4" "CAM 1"
```

**What `detect.py` does:**
1. Loads `yolov8n.pt` (person detection model, class 0 only)
2. Runs ByteTrack multi-object tracking with custom YAML config (`custom_tracker.yaml`)
3. Applies 3-layer false-positive filtering (bounding box size, motion distance, track duration)
4. Detects staff by tracking ID visibility ratio (>60% of frames = staff)
5. Maps bounding box centroids to store zones (SKINCARE, MAKEUP, BILLING)
6. Emits typed events (`ENTRY`, `EXIT`, `ZONE_ENTER`, `ZONE_DWELL`, `BILLING_QUEUE_JOIN`, etc.) to the API in batches of 50

### Resetting Between Runs

To clear all tracking data before re-running a clip, click the **"Reset Data"** button on the dashboard, or call the API directly:

```bash
curl -X DELETE http://localhost:8000/api/v1/reset
```

### Hardware Note

YOLOv8n requires a GPU for real-time performance. The pipeline was developed and tested on an **NVIDIA GTX 1650 (4GB VRAM)**. On CPU-only machines, processing will be slower but functionally correct.

---

## Running the Tests

```bash
# From the project root
pip install -r requirements.txt
python -m pytest tests/ -v
```

Expected output: all tests pass across 5 test files covering ingestion, metrics, funnel, heatmap, and anomaly detection endpoints.

Each test file contains a `# PROMPT:` block at the top documenting the AI prompt used to generate the initial tests, and a `# CHANGES MADE:` block listing every manual adjustment made after AI generation.

---

## Project Structure

```
purplle-challenge/
├── app/                        # FastAPI application
│   ├── main.py                 # App entry point, router registration
│   ├── db.py                   # SQLAlchemy models and DB session
│   ├── models.py               # Pydantic request/response schemas + EventType enum
│   ├── ingestion.py            # POST /events/ingest — idempotent batch ingestion
│   ├── metrics.py              # GET /stores/{id}/metrics
│   ├── funnel.py               # GET /stores/{id}/funnel
│   ├── heatmap.py              # GET /stores/{id}/heatmap
│   ├── anomalies.py            # GET /stores/{id}/anomalies
│   └── dashboard.html          # Live web dashboard (served at /)
├── pipeline/
│   ├── detect.py               # Main pipeline entry point
│   ├── tracker.py              # StoreTracker class — filtering, zone mapping, event emission
│   ├── emit.py                 # HTTP event emitter with dead-letter queue
│   ├── custom_tracker.yaml     # ByteTrack configuration (thresholds, buffer sizes)
│   ├── run.bat                 # Windows: process all 5 clips
│   ├── run.sh                  # Linux/macOS: process all 5 clips
│   └── yolov8n.pt              # Pre-trained YOLOv8 Nano weights
├── tests/
│   ├── test_metrics.py         # Integration test: ingest → metrics pipeline
│   ├── test_ingestion.py       # Unit tests: batch ingestion, idempotency, limits
│   ├── test_funnel.py          # Unit tests: funnel counts, staff exclusion, orphan visitors
│   ├── test_heatmap.py         # Unit tests: normalization math, data confidence
│   └── test_anomalies.py       # Unit tests: queue spike, dead zone, true-negative tests
├── docs/
│   ├── DESIGN.md               # Architecture overview + AI-Assisted Decisions section
│   └── CHOICES.md              # Model selection, schema design, API architecture rationale
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | API health check |
| `POST` | `/events/ingest` | Ingest a batch of detection events (max 500) |
| `GET` | `/stores/{id}/metrics` | Unique visitors, conversion rate, queue depth, abandonment |
| `GET` | `/stores/{id}/funnel` | 4-stage conversion funnel with drop-off percentages |
| `GET` | `/stores/{id}/heatmap` | Zone traffic heatmap with normalized visit and dwell scores |
| `GET` | `/stores/{id}/anomalies` | Active anomalies (BILLING_QUEUE_SPIKE, DEAD_ZONE, CONVERSION_DROP) |
| `DELETE` | `/api/v1/reset` | Clear all tracking data (development use) |

---

## AI Engineering Documentation

| Document | Contents |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | System architecture diagram, component decisions, **AI-Assisted Decisions** section covering 3 specific cases where LLM input was accepted, modified, or overridden |
| [`docs/CHOICES.md`](docs/CHOICES.md) | Detection model selection (YOLOv8n vs alternatives, VLM evaluation), event schema design rationale, API architecture decision with options comparison tables |

All test files contain `# PROMPT:` and `# CHANGES MADE:` blocks at the top documenting AI-assisted test generation.
