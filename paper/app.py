"""Read-only dashboard API for the isolated paper engine."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from engine import PaperEngine

ROOT = Path(__file__).resolve().parent
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
engine = PaperEngine()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    engine.start()
    yield
    engine.stop()


app = FastAPI(title="Yeni V4 2020–2024 · Core 10x Paper", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(ROOT / "static/index.html", media_type="text/html")


@app.get("/app.css")
def css():
    return FileResponse(ROOT / "static/app.css", media_type="text/css")


@app.get("/app.js")
def js():
    return FileResponse(ROOT / "static/app.js", media_type="application/javascript")


@app.get("/api/state")
def state():
    data = engine.state()
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    data = engine.state()
    ready = data["engine_running"] and all(
        item["ready"] and item["enabled"] for item in data["filters"].values())
    return JSONResponse({"ready": ready, "mode": "PAPER_ONLY",
                         "last_heartbeat_ms": data["last_heartbeat_ms"]},
                        status_code=200 if ready else 503,
                        headers={"Cache-Control": "no-store"})
