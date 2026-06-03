from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import time
import logging
import uuid
from .db import init_db, SessionLocal, DBTransaction
import pandas as pd
import os

# Configure structured logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("store_api")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_pos_data()
    logger.info('{"event": "system_startup", "message": "Store Intelligence API initialized"}')
    yield

app = FastAPI(title="Store Intelligence API", lifespan=lifespan)

def load_pos_data():
    db = SessionLocal()
    try:
        # Check if already loaded
        if db.query(DBTransaction).first():
            return
            
        pos_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Brigade_Bangalore_10_April_26 (1)bc6219c.csv")
        if not os.path.exists(pos_path):
            logger.warning("POS CSV not found. Skipping POS data load.")
            return
            
        df_pos = pd.read_csv(pos_path)
        df_pos['dt'] = pd.to_datetime(df_pos['order_date'] + ' ' + df_pos['order_time'], format='%d-%m-%Y %H:%M:%S', errors='coerce')
        df_pos = df_pos.dropna(subset=['dt'])
        
        # Insert all transactions at once for performance
        transactions = []
        for _, row in df_pos.iterrows():
            transactions.append(DBTransaction(
                store_id="ST1008", # Since Brigade Bangalore is ST1008
                timestamp=row['dt']
            ))
        db.bulk_save_objects(transactions)
        db.commit()
        logger.info(f"Loaded {len(transactions)} POS transactions into SQLite.")
    except Exception as e:
        logger.error(f"Failed to load POS data: {e}")
    finally:
        db.close()



@app.middleware("http")
async def structured_logging_middleware(request: Request, call_next):
    trace_id = str(uuid.uuid4())
    start_time = time.time()
    
    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception as e:
        # Graceful degradation for unhandled errors
        status_code = 503
        logger.error(f'{{"trace_id": "{trace_id}", "error": "{str(e)}"}}')
        response = JSONResponse(
            status_code=503,
            content={"error": "Service Unavailable", "message": "An internal error occurred."}
        )

    latency_ms = int((time.time() - start_time) * 1000)
    
    # Fix middleware store_id extraction from path (e.g. /stores/ST1008/metrics)
    parts = request.url.path.strip("/").split("/")
    store_id = "UNKNOWN"
    if len(parts) >= 2 and parts[0] == "stores":
        store_id = parts[1]
    
    log_data = {
        "trace_id": trace_id,
        "store_id": store_id,
        "endpoint": f"{request.method} {request.url.path}",
        "latency_ms": latency_ms,
        "status_code": status_code
    }
    
    if hasattr(request.state, "event_count"):
        log_data["event_count"] = request.state.event_count
    
    logger.info(str(log_data).replace("'", '"'))
    
    return response

from .ingestion import router as ingestion_router
from .metrics import router as metrics_router
from .funnel import router as funnel_router
from .heatmap import router as heatmap_router
from .anomalies import router as anomalies_router
from .health import router as health_router

from fastapi.responses import HTMLResponse
import os

@app.get("/")
def serve_dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    with open(dashboard_path, "r") as f:
        html = f.read()
    return HTMLResponse(content=html, status_code=200)

app.include_router(ingestion_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(heatmap_router)
app.include_router(anomalies_router)
app.include_router(health_router)

from fastapi import Depends
from sqlalchemy.orm import Session
from .db import get_db, DBEvent

@app.delete("/api/v1/reset")
def reset_data(db: Session = Depends(get_db)):
    try:
        deleted_count = db.query(DBEvent).delete()
        db.commit()
        logger.info(f'{{"event": "data_reset", "message": "Cleared {deleted_count} events from database"}}')
        return {"status": "success", "message": f"Successfully cleared {deleted_count} events from the system."}
    except Exception as e:
        db.rollback()
        logger.error(f'{{"event": "reset_error", "message": "{str(e)}"}}')
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
