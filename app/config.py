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

    # --- SharePoint (conexion directa, sin Power Automate) -------------------
    # Liga del sitio, tal como aparece en el navegador.
    sp_site_url: str = ""
    # "delegado" (inicias sesion con tu cuenta) o "aplicacion" (registro con secreto).
    sp_auth_mode: str = "delegado"
    sp_tenant_id: str = ""
    sp_client_id: str = ""
    sp_client_secret: str = ""
    # Donde se guarda la sesion de SharePoint. Contiene un token: no lo subas a git.
    sp_token_cache: str = ".sharepoint-token.json"

    timezone: str = "America/Tijuana"

    # Nombre que se muestra en el encabezado del portal.
    app_title: str = "Seguimiento de ordenes con proveedores"
    app_subtitle: str = "Mantenimiento Paint Shop - TMMBC"


@lru_cache
def get_settings() -> Settings:
    return Settings()
