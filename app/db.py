"""Motor de base de datos y sesiones.

El mismo modelo corre sobre SQLite (desarrollo) y SQL Server (produccion); la
unica diferencia esta en la URL de conexion y en los argumentos del engine.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        # check_same_thread=False permite que el pool de FastAPI reuse la conexion.
        return {"connect_args": {"check_same_thread": False}}
    # SQL Server: reciclar conexiones evita que el pool entregue sockets muertos.
    return {"pool_pre_ping": True, "pool_recycle": 1800, "fast_executemany": True}


settings = get_settings()
engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Crea las tablas si no existen."""
    from app import models  # noqa: F401  (registra los modelos en el metadata)

    Base.metadata.create_all(bind=engine)
