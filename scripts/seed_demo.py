"""Llena la base de datos con un escenario de demostracion.

Genera el reporte de ejemplo, lo carga por el mismo camino que usa el portal y
simula respuestas de proveedores para que el tablero muestre datos reales.

    python scripts/seed_demo.py
"""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import OrderStatus, PurchaseOrder, Supplier, utcnow  # noqa: E402
from app.services.excel_import import import_orders  # noqa: E402
from app.services.seguimiento import actualizar_seguimiento  # noqa: E402
from app.services.sync import apply_supplier_update  # noqa: E402
from scripts.generar_reporte_ejemplo import generar  # noqa: E402

RESPUESTAS = [
    (OrderStatus.RECIBIDA, 0.20, None),
    (OrderStatus.EN_PROCESO, 0.25, 12),
    (OrderStatus.ENVIADA, 0.15, 5),
    (OrderStatus.RETRASADA, 0.15, 25),
    (OrderStatus.ENTREGADA, 0.10, -2),
    (None, 0.15, None),  # el proveedor no contesto
]

# Notas que captura mantenimiento despues de hablarle al proveedor. El proveedor
# no las escribe: el solo selecciona el status en su lista.
NOTAS_INTERNAS = {
    OrderStatus.RECIBIDA: "Hablo el proveedor: confirma disponibilidad esta semana.",
    OrderStatus.EN_PROCESO: "En fabricacion, sale de planta segun fecha promesa.",
    OrderStatus.ENVIADA: "Embarcado con guia 7742-MX, llega por paqueteria.",
    OrderStatus.RETRASADA: "Material de importacion detenido en aduana.",
    OrderStatus.ENTREGADA: "Entregado en almacen de mantenimiento.",
}


def main() -> None:
    random.seed(11)
    init_db()

    reporte = generar(Path("reporte-ejemplo.xlsx"))
    db = SessionLocal()
    try:
        resultado = import_orders(
            db,
            content=reporte.read_bytes(),
            filename=reporte.name,
            uploaded_by="demo@tmmbc",
            close_scope="all",
            create_missing_suppliers=True,
        )
        print(
            f"Carga #{resultado.batch_id}: {resultado.rows_created} altas, "
            f"{resultado.rows_updated} actualizadas, {resultado.rows_rejected} rechazadas."
        )

        # Configurar los proveedores como quedarian despues del alta manual.
        for indice, proveedor in enumerate(db.scalars(select(Supplier)).all()):
            proveedor.sharepoint_list = f"Ordenes - {proveedor.code}"
            proveedor.contact_email = f"ventas@{proveedor.code.lower()}.mx"
            proveedor.share_price = indice == 0  # solo al primero se le publica el precio
        db.commit()

        # Las ordenes se dan de alta con fecha de hoy, pero para la demo interesa
        # que el proveedor haya contestado DESPUES del alta: se retrocede el alta.
        alta = utcnow() - timedelta(days=7)
        for orden in db.scalars(select(PurchaseOrder)).all():
            orden.created_at = alta
        db.commit()

        # Simular lo que capturarian los proveedores en sus listas.
        hoy = date.today()
        aplicados = 0
        for orden in db.scalars(select(PurchaseOrder)).all():
            status, _, dias = _elegir_respuesta()
            if status is None:
                continue
            # 1. El proveedor selecciona el status en su lista de SharePoint.
            apply_supplier_update(
                db,
                supplier=orden.supplier,
                po_number=orden.po_number,
                line_number=orden.line_number,
                status=status.value,
                changed_by=f"invitado@{orden.supplier.code.lower()}.mx",
                changed_at=alta + timedelta(hours=random.randint(4, 140)),
            )
            # 2. Mantenimiento captura en el portal lo que hablo con el proveedor.
            actualizar_seguimiento(
                db,
                orden,
                promised_date=(orden.required_date or hoy) + timedelta(days=dias or 0),
                internal_note=NOTAS_INTERNAS[status],
                actor="demo@tmmbc",
            )
            aplicados += 1
        db.commit()
        print(f"{aplicados} ordenes con status del proveedor y seguimiento interno.")
        print("Listo. Arranca el portal con: uvicorn app.main:app --reload")
    finally:
        db.close()


def _elegir_respuesta():
    tirada = random.random()
    acumulado = 0.0
    for opcion in RESPUESTAS:
        acumulado += opcion[1]
        if tirada <= acumulado:
            return opcion
    return RESPUESTAS[-1]


if __name__ == "__main__":
    main()
