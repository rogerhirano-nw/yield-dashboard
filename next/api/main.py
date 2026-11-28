"""Newsweek AdOps dashboard API (FastAPI) + the React app's static files.

Run: uvicorn next.api.main:app --port 8000   (from the repo root)

Auth is App Service's Entra login in front of the whole app (as for the
Streamlit dashboard), so every route here is already behind the Newsweek login.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from .data import settings, store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
app = FastAPI(title="Newsweek AdOps", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(GZipMiddleware, minimum_size=1024)
WEB = Path(__file__).resolve().parents[1] / "web" / "dist"


@app.on_event("startup")
def _boot() -> None:
    store()  # starts the background loader


@app.get("/api/health")
def health():
    s = store()
    return {"ok": True, "version": s.version, "tables": len(s.frames),
            "loaded_at": s.loaded_at, "age_s": round(time.time() - s.loaded_at) if s.loaded_at else None}


@app.get("/api/meta")
def meta():
    s = store()
    cfg = settings(s)
    return {"version": s.version, "loaded_at": s.loaded_at,
            "pulled_at": {t: (str(df["_pulled_at"].max()) if "_pulled_at" in df and len(df) else None)
                          for t, df in s.frames.items()},
            "ae_names": cfg.get("ae_names", {})}


from . import campaigns  # noqa: E402  (registers /api/campaigns routes)

app.include_router(campaigns.router)
store().warmers.append(campaigns._prepared)

if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = WEB / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(WEB / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def no_web():
        return JSONResponse({"detail": "web/dist not built; run npm run build in next/web"})
