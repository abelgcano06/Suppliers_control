"""Sincroniza con SharePoint sin abrir el portal.

    python scripts/sincronizar.py

Pensado para el Programador de tareas de Windows, para que corra solo cada hora.
Devuelve 0 si todo salio bien y 1 si hubo algun problema, para que el Programador
lo pueda marcar como fallido.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.services import sharepoint  # noqa: E402


def main() -> int:
    marca = datetime.now().strftime("%d/%m/%Y %H:%M")
    settings = get_settings()

    if not settings.sp_site_url:
        print(f"[{marca}] Falta SP_SITE_URL en el .env.")
        return 1

    init_db()
    db = SessionLocal()
    try:
        resultado = sharepoint.sincronizar(db, settings)
    except sharepoint.SinSesion as exc:
        print(f"[{marca}] {exc}")
        return 1
    except sharepoint.SharePointError as exc:
        db.rollback()
        print(f"[{marca}] Error de SharePoint: {exc}")
        return 1
    finally:
        db.close()

    print(
        f"[{marca}] {len(resultado.proveedores)} proveedor(es): "
        f"{resultado.total_bajados} respuestas bajadas, "
        f"{resultado.total_publicados} lineas publicadas, "
        f"{resultado.total_eliminados} retiradas."
    )
    for problema in resultado.errores:
        print(f"  ! {problema}")
    for proveedor in resultado.proveedores:
        for problema in proveedor.errores:
            print(f"  ! {proveedor.codigo}: {problema}")

    return 0 if resultado.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
