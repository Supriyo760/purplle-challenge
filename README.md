# Store Intelligence Pipeline

This repository contains a full-stack, AI-powered Store Intelligence system using YOLOv8, FastAPI, and a live HTML dashboard, designed for the Purplle Tech Challenge.

## Components
1. **Intelligence API**: FastAPI backend that ingests detection events and serves aggregated metrics, funnels, heatmaps, and anomalies.
2. **Detection Pipeline**: Python script utilizing YOLOv8n to process CCTV footage, track visitors, and emit events.
3. **Live Dashboard**: Real-time web UI showing store activity metrics.

## Quick Start (Docker)

1. Ensure Docker and Docker Compose are installed.
2. Run the application:
   ```bash
   docker-compose up --build
   ```
3. Open your browser and navigate to the Live Dashboard at `http://localhost:8000` to view real-time metrics, funnels, and anomalies.

## Running the Detection Pipeline
Once the API is running, you can ingest the video streams. Note: This requires a GPU for real-time performance.

1. Install dependencies locally: `pip install -r requirements.txt`
2. Run the pipeline script:
   ```cmd
   cd pipeline
   run.bat
   ```
3. The dashboard at `http://localhost:8000` will update in real-time as events are emitted from the video streams.

## API Documentation
Once the server is running, you can explore the OpenAPI documentation and test the endpoints directly via:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

## Project Structure
- `/app`: FastAPI application code (endpoints, models, db).
- `/pipeline`: YOLOv8 CV script and bounding-box tracking logic.
- `/docs`: AI Engineering documentation (`DESIGN.md` and `CHOICES.md`).
- `/tests`: Pytest suite (run with `python -m pytest`).
