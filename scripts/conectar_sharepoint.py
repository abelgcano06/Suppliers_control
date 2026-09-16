"""Conecta el portal con tu SharePoint. Se corre una vez.

    python scripts/conectar_sharepoint.py

Te da un codigo, lo pegas en el navegador, inicias sesion con TU cuenta de
Toyota, y el portal guarda la sesion. A partir de ahi sincroniza solo.

La sesion dura semanas y se renueva sola mientras se use. Si vence, vuelves a
correr esto mismo.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services import sharepoint  # noqa: E402


def main() -> int:
    settings = get_settings()

    if not settings.sp_site_url:
        print("Falta SP_SITE_URL en el archivo .env.")
        print("Copia la liga de tu sitio del navegador, algo como:")
        print("  SP_SITE_URL=https://tutenant.sharepoint.com/sites/Proveedores")
        return 1

    if settings.sp_auth_mode == "aplicacion":
        print("Estas en modo 'aplicacion': no hace falta iniciar sesion.")
        print("El portal usa SP_CLIENT_ID y SP_CLIENT_SECRET directamente.")
        try:
            sharepoint.obtener_token(settings)
        except sharepoint.SharePointError as exc:
            print(f"\nPero el registro no funciona: {exc}")
            return 1
        print("El registro de aplicacion responde correctamente.")
        return 0

    ya = sharepoint.sesion_activa(settings)
    if ya:
        print(f"Ya hay una sesion guardada: {ya}")
        respuesta = input("Quieres reemplazarla? [s/N] ").strip().lower()
        if respuesta != "s":
            print("Se deja como esta.")
            return 0
        sharepoint.cerrar_sesion(settings)

    print(f"Sitio: {settings.sp_site_url}")
    print("\nSigue estas instrucciones:\n")

    try:
        cuenta = sharepoint.iniciar_sesion_por_codigo(settings)
    except sharepoint.SharePointError as exc:
        print(f"\nNo se pudo conectar: {exc}\n")
        print("Si dice que el tenant no consintio la aplicacion, corre:")
        print("  python scripts/diagnostico_sharepoint.py")
        return 1

    print(f"\nListo. El portal quedo conectado como: {cuenta}")
    print("Ya puedes sincronizar desde el portal, en la pantalla SharePoint.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
