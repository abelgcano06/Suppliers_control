"""Autenticacion de la API de sincronizacion.

Power Automate se identifica con una clave estatica en el header `X-API-Key`.
Es el unico consumidor de esta API; el portal interno no la usa.
"""

import secrets

from fastapi import Header, HTTPException, status

from app.config import get_settings


def require_api_key(x_api_key: str = Header(default="", alias="X-API-Key")) -> None:
    esperada = get_settings().sync_api_key
    # Comparacion en tiempo constante para no filtrar la clave por temporizacion.
    if not x_api_key or not secrets.compare_digest(x_api_key, esperada):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clave de API invalida o ausente.",
            headers={"WWW-Authenticate": "X-API-Key"},
        )
