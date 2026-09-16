"""Consultas del tablero consolidado: KPIs, filtros y alertas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models import OrderStatus, PurchaseOrder, Supplier


@dataclass
class OrderFilters:
    supplier_id: int | None = None
    status: OrderStatus | None = None
    # "retrasadas" | "sin_respuesta" | "por_vencer" | "al_dia" | None
    alerta: str | None = None
    search: str | None = None
    include_closed: bool = False


@dataclass
class Kpis:
    abiertas: int
    con_status: int
    sin_respuesta: int
    retrasadas: int
    por_vencer: int
    entregadas: int

    @property
    def porcentaje_respuesta(self) -> float:
        """El indicador del piloto: % de ordenes con status actualizado."""
        if self.abiertas == 0:
            return 0.0
        return round(self.con_status * 100 / self.abiertas, 1)


def _base_query(include_closed: bool):
    query = select(PurchaseOrder).options(selectinload(PurchaseOrder.supplier))
    if not include_closed:
        query = query.where(PurchaseOrder.is_closed == False)  # noqa: E712
    return query


def query_orders(db: Session, filters: OrderFilters, limit: int | None = None) -> list[PurchaseOrder]:
    query = _base_query(filters.include_closed)

    if filters.supplier_id:
        query = query.where(PurchaseOrder.supplier_id == filters.supplier_id)
    if filters.status:
        query = query.where(PurchaseOrder.status == filters.status)
    if filters.search:
        patron = f"%{filters.search.strip()}%"
        query = query.where(
            or_(
                PurchaseOrder.po_number.like(patron),
                PurchaseOrder.part_number.like(patron),
                PurchaseOrder.description.like(patron),
            )
        )

    query = query.order_by(PurchaseOrder.po_number, PurchaseOrder.line_number)
    ordenes = list(db.scalars(query).all())

    # Las alertas dependen de fechas y de la regla de "sin respuesta", asi que se
    # resuelven en Python para que la logica viva en un solo lugar (models.py).
    if filters.alerta:
        ordenes = [o for o in ordenes if _cumple_alerta(o, filters.alerta)]

    return ordenes[:limit] if limit else ordenes


def _cumple_alerta(order: PurchaseOrder, alerta: str) -> bool:
    hoy = date.today()
    settings = get_settings()
    if alerta == "retrasadas":
        return order.esta_retrasada(hoy)
    if alerta == "sin_respuesta":
        return order.sin_respuesta
    if alerta == "por_vencer":
        referencia = order.promised_date or order.required_date
        return (
            referencia is not None
            and not order.esta_retrasada(hoy)
            and order.status is not OrderStatus.ENTREGADA
            and hoy <= referencia <= hoy + timedelta(days=settings.due_soon_days)
        )
    if alerta == "al_dia":
        return not order.esta_retrasada(hoy) and not order.sin_respuesta
    return True


def compute_kpis(db: Session) -> Kpis:
    hoy = date.today()
    abiertas = list(db.scalars(_base_query(include_closed=False)).all())
    return Kpis(
        abiertas=len(abiertas),
        con_status=sum(1 for o in abiertas if o.status is not OrderStatus.PENDIENTE),
        sin_respuesta=sum(1 for o in abiertas if o.sin_respuesta),
        retrasadas=sum(1 for o in abiertas if o.esta_retrasada(hoy)),
        por_vencer=sum(1 for o in abiertas if _cumple_alerta(o, "por_vencer")),
        entregadas=sum(1 for o in abiertas if o.status is OrderStatus.ENTREGADA),
    )


@dataclass
class SupplierSummary:
    supplier: Supplier
    abiertas: int
    con_status: int
    sin_respuesta: int
    retrasadas: int
    ultima_respuesta: object | None

    @property
    def porcentaje_respuesta(self) -> float:
        if self.abiertas == 0:
            return 0.0
        return round(self.con_status * 100 / self.abiertas, 1)


def supplier_summaries(db: Session) -> list[SupplierSummary]:
    """Una fila por proveedor: cuanto le falta contestar y que trae retrasado."""
    hoy = date.today()
    proveedores = db.scalars(select(Supplier).order_by(Supplier.name)).all()
    ordenes = list(db.scalars(_base_query(include_closed=False)).all())

    por_proveedor: dict[int, list[PurchaseOrder]] = {}
    for orden in ordenes:
        por_proveedor.setdefault(orden.supplier_id, []).append(orden)

    resumen = []
    for proveedor in proveedores:
        suyas = por_proveedor.get(proveedor.id, [])
        respuestas = [o.last_supplier_update_at for o in suyas if o.last_supplier_update_at]
        resumen.append(
            SupplierSummary(
                supplier=proveedor,
                abiertas=len(suyas),
                con_status=sum(1 for o in suyas if o.status is not OrderStatus.PENDIENTE),
                sin_respuesta=sum(1 for o in suyas if o.sin_respuesta),
                retrasadas=sum(1 for o in suyas if o.esta_retrasada(hoy)),
                ultima_respuesta=max(respuestas) if respuestas else None,
            )
        )
    return resumen


def tiempo_promedio_respuesta_horas(db: Session) -> float | None:
    """Horas promedio entre el alta de la orden y la primera respuesta del proveedor.

    Es la segunda metrica del piloto descrita en el plan de arranque.
    """
    filas = db.execute(
        select(PurchaseOrder.created_at, PurchaseOrder.last_supplier_update_at).where(
            PurchaseOrder.last_supplier_update_at.is_not(None)
        )
    ).all()
    if not filas:
        return None
    horas = [
        (respuesta - creada).total_seconds() / 3600
        for creada, respuesta in filas
        if respuesta and creada and respuesta >= creada
    ]
    if not horas:
        return None
    return round(sum(horas) / len(horas), 1)
