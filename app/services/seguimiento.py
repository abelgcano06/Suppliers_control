"""Seguimiento interno: lo que captura mantenimiento, no el proveedor.

El proveedor solo cambia el status en su lista de SharePoint. La fecha promesa y
la nota las captura el equipo en el portal, tipicamente con lo que el proveedor
dijo por telefono o por correo.

Estos campos nunca salen hacia SharePoint: no aparecen en la lista del proveedor.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import ChangeSource, OrderChange, PurchaseOrder, utcnow

MAX_NOTA = 2000


def actualizar_seguimiento(
    db: Session,
    order: PurchaseOrder,
    promised_date: date | None = None,
    internal_note: str | None = None,
    actor: str | None = None,
    limpiar_fecha: bool = False,
) -> list[str]:
    """Guarda la fecha promesa y la nota interna. Devuelve los campos que cambiaron."""
    momento = utcnow()
    cambios: list[str] = []

    nueva_fecha = None if limpiar_fecha else promised_date
    if (limpiar_fecha or promised_date is not None) and nueva_fecha != order.promised_date:
        _log(
            db, order, "promised_date",
            order.promised_date.isoformat() if order.promised_date else None,
            nueva_fecha.isoformat() if nueva_fecha else None,
            actor, momento,
        )
        order.promised_date = nueva_fecha
        cambios.append("promised_date")

    if internal_note is not None:
        nota = internal_note.strip()[:MAX_NOTA] or None
        if nota != order.internal_note:
            _log(db, order, "internal_note", order.internal_note, nota, actor, momento)
            order.internal_note = nota
            cambios.append("internal_note")

    if cambios:
        order.updated_at = momento
    return cambios


def _log(db: Session, order: PurchaseOrder, field: str, old: str | None,
         new: str | None, actor: str | None, momento) -> None:
    db.add(
        OrderChange(
            order_id=order.id,
            field=field,
            old_value=old,
            new_value=new,
            source=ChangeSource.PORTAL,
            actor=actor,
            changed_at=momento,
        )
    )
