"""Configuracion de la aplicacion, leida de variables de entorno o del archivo .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Base de datos: SQLite en desarrollo, SQL Server en produccion.
    database_url: str = "sqlite:///./suppliers_control.db"

    # Clave que Power Automate envia en el header X-API-Key.
    sync_api_key: str = "cambia-esta-clave"

    # Dias sin respuesta del proveedor antes de levantar la alerta.
    no_response_days: int = 3
    # Dias de anticipacion para la alerta "por vencer".
    due_soon_days: int = 7

    timezone: str = "America/Tijuana"

    # Nombre que se muestra en el encabezado del portal.
    app_title: str = "Seguimiento de ordenes con proveedores"
    app_subtitle: str = "Mantenimiento Paint Shop - TMMBC"


@lru_cache
def get_settings() -> Settings:
    return Settings()
