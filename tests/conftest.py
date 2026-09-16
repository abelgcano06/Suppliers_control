"""Base comun de las pruebas: base de datos en memoria y cliente HTTP."""

from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base, get_db
from app.main import app

API_KEY = "clave-de-prueba"

ENCABEZADOS = [
    "Orden", "Linea", "Codigo Proveedor", "Proveedor", "Parte", "Descripcion",
    "Cantidad", "Unidad", "Precio Unitario", "Moneda", "Fecha Requerida",
]


@pytest.fixture
def engine():
    motor = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(motor)
    yield motor
    Base.metadata.drop_all(motor)
    motor.dispose()


@pytest.fixture
def db(engine):
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    sesion = Session()
    try:
        yield sesion
    finally:
        sesion.close()


@pytest.fixture
def client(db, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("SYNC_API_KEY", API_KEY)
    get_settings.cache_clear()

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def construir_excel(filas: list[list], encabezados: list[str] | None = None,
                    filas_previas: int = 0) -> bytes:
    """Arma un .xlsx en memoria con el formato del reporte de compras."""
    libro = Workbook()
    hoja = libro.active
    for i in range(filas_previas):
        hoja.append([f"Fila de titulo {i + 1}"])
    hoja.append(encabezados or ENCABEZADOS)
    for fila in filas:
        hoja.append(fila)
    buffer = BytesIO()
    libro.save(buffer)
    return buffer.getvalue()


def fila(orden="OC-1001", linea=1, codigo="PROV-A", nombre="Pinturas del Norte",
         parte="PN-4471", descripcion="Filtro de cabina", cantidad=10, unidad="PZA",
         precio=845.5, moneda="MXN", requerida=None) -> list:
    return [
        orden, linea, codigo, nombre, parte, descripcion, cantidad, unidad,
        precio, moneda, requerida if requerida is not None else date.today() + timedelta(days=20),
    ]
