"""Sincronizacion con SharePoint a traves de Power Automate.

Power Automate es el unico canal entre la zona interna y la zona externa:

* Bajada  (portal -> SharePoint): `orders_for_supplier` entrega a cada proveedor
  UNICAMENTE sus ordenes, y omite el precio si asi esta configurado el proveedor.
* Subida  (SharePoint -> portal): `apply_supplier_update` acepta UNICAMENTE el
  status. Cualquier otro campo se ignora, y si el proveedor toco una columna
  del sistema, la siguiente bajada la restaura.

Un proveedor nunca puede escribir sobre la orden de otro: la validacion compara
el propietario de la orden contra el proveedor que manda el cambio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ChangeSource,
    OrderChange,
    OrderStatus,
    PurchaseOrder,
    Supplier,
    utcnow,
)


class SyncError(Exception):
    """El cambio del proveedor no se puede aplicar."""


@dataclass
class UpdateOutcome:
    po_number: str
    line_number: int
    applied: bool
    changed_fields: list[str]
    message: str


def get_supplier_by_code(db: Session, code: str) -> Supplier | None:
    return db.scalar(select(Supplier).where(Supplier.code == code))


def orders_for_supplier(
    db: Session, supplier: Supplier, include_closed: bool = False
) -> list[dict]:
    """Las ordenes que Power Automate debe publicar en la lista del proveedor."""
    query = select(PurchaseOrder).where(PurchaseOrder.supplier_id == supplier.id)
    if not include_closed:
        query = query.where(PurchaseOrder.is_closed == False)  # noqa: E712
    query = query.order_by(PurchaseOrder.po_number, PurchaseOrder.line_number)

    payload = []
    for order in db.scalars(query).all():
        item = {
            # Clave con la que Power Automate localiza la fila en la lista.
            "key": f"{order.po_number}-{order.line_number}",
            "po_number": order.po_number,
            "line_number": order.line_number,
            # Campos del sistema: el flujo los reescribe siempre, y asi
            # restaura cualquier edicion indebida del proveedor.
            "part_number": order.part_number,
            "description": order.description,
            "quantity": float(order.quantity) if order.quantity is not None else None,
            "unit": order.unit,
            "required_date": order.required_date.isoformat() if order.required_date else None,
            # Lo unico que captura el proveedor. Se manda el valor actual para
            # no pisar lo que ya haya seleccionado.
            "status": order.status.value,
            "is_closed": order.is_closed,
            "updated_at": order.updated_at.isoformat() if order.updated_at else None,
        }
        if supplier.share_price:
            item["unit_price"] = float(order.unit_price) if order.unit_price is not None else None
            item["currency"] = order.currency
        payload.append(item)
    return payload


def parse_status(value: str) -> OrderStatus:
    """El status es un catalogo cerrado; se acepta el nombre o el valor."""
    if not value:
        raise SyncError("El status viene vacio.")
    texto = str(value).strip()
    for status in OrderStatus:
        if texto.lower() == status.value.lower() or texto.upper() == status.name:
            if status is OrderStatus.PENDIENTE:
                raise SyncError(
                    "'Pendiente' lo asigna el sistema; el proveedor no puede seleccionarlo."
                )
            return status
    permitidos = ", ".join(s.value for s in OrderStatus.editable_por_proveedor())
    raise SyncError(f"Status '{value}' no esta en el catalogo. Validos: {permitidos}.")


def apply_supplier_update(
    db: Session,
    supplier: Supplier,
    po_number: str,
    line_number: int,
    status: str | None = None,
    changed_by: str | None = None,
    changed_at: datetime | None = None,
) -> UpdateOutcome:
    """Aplica el status que el proveedor selecciono en su lista de SharePoint.

    Es lo unico que un proveedor puede cambiar. La fecha promesa y la nota se
    capturan en el portal (ver `app/services/seguimiento.py`).
    """
    order = db.scalar(
        select(PurchaseOrder).where(
            PurchaseOrder.po_number == po_number,
            PurchaseOrder.line_number == line_number,
        )
    )
    if order is None:
        raise SyncError(f"La orden {po_number}-{line_number} no existe en el portal.")
    if order.supplier_id != supplier.id:
        # Nunca se revela de quien es la orden.
        raise SyncError(
            f"La orden {po_number}-{line_number} no pertenece al proveedor {supplier.code}."
        )
    if order.is_closed:
        raise SyncError(
            f"La orden {po_number}-{line_number} ya esta cerrada y no admite cambios."
        )

    momento = changed_at or utcnow()
    actor = changed_by or supplier.name
    cambios: list[str] = []

    if status is not None:
        nuevo = parse_status(status)
        if nuevo is not order.status:
            _log(db, order, "status", order.status.value, nuevo.value, actor, momento)
            order.status = nuevo
            cambios.append("status")

    if cambios:
        order.last_supplier_update_at = momento
        order.last_supplier_update_by = actor
        order.updated_at = utcnow()
        return UpdateOutcome(po_number, line_number, True, cambios, "Cambio aplicado.")

    return UpdateOutcome(po_number, line_number, False, [], "Sin cambios que aplicar.")


def _log(
    db: Session,
    order: PurchaseOrder,
    field: str,
    old: str | None,
    new: str | None,
    actor: str | None,
    momento: datetime,
) -> None:
    db.add(
        OrderChange(
            order_id=order.id,
            field=field,
            old_value=old,
            new_value=new,
            source=ChangeSource.PROVEEDOR,
            actor=actor,
            changed_at=momento,
        )
    )
