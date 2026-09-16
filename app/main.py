"""Punto de entrada del portal.

    uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import init_db
from app.routers import api, web


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


settings = get_settings()

app = FastAPI(
    title=settings.app_title,
    description=(
        "Portal interno de seguimiento de ordenes abiertas con proveedores. "
        "La API /api/v1 es el unico canal hacia Power Automate y SharePoint."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(web.router)
app.include_router(api.router)


@app.get("/salud", tags=["operacion"])
def salud() -> dict:
    """Chequeo simple para monitoreo."""
    return {"estado": "ok"}
