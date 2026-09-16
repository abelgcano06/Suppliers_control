"""Modelo de datos: la base de datos es la fuente unica de verdad.

Reglas que el modelo hace explicitas (seccion "Reglas de seguridad" del concepto):

* Los campos de la orden que controla el sistema (numero, parte, cantidad,
  precio, fecha requerida) vienen del Excel de compras y se sobreescriben en
  cada carga.
* Los campos que controla el proveedor (status, fecha promesa, comentario) se
  conservan entre cargas y solo cambian por la sincronizacion de SharePoint.
* Todo cambio queda registrado en `order_changes` con quien, cuando y desde donde.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OrderStatus(str, enum.Enum):
    """Catalogo cerrado de status. El proveedor no captura texto libre aqui."""

    PENDIENTE = "Pendiente"          # asignado por el sistema, el proveedor aun no contesta
    RECIBIDA = "Recibida"            # el proveedor acuso de recibido
    EN_PROCESO = "En proceso"
    ENVIADA = "Enviada"
    RETRASADA = "Retrasada"
    ENTREGADA = "Entregada"

    @classmethod
    def editable_por_proveedor(cls) -> list["OrderStatus"]:
        """Todo el catalogo menos PENDIENTE, que solo pone el sistema."""
        return [s for s in cls if s is not cls.PENDIENTE]

    @property
    def es_cerrado(self) -> bool:
        return self is OrderStatus.ENTREGADA


class ChangeSource(str, enum.Enum):
    """De donde vino un cambio."""

    EXCEL = "Carga de Excel"
    PROVEEDOR = "Proveedor (SharePoint)"
    PORTAL = "Portal interno"
    SISTEMA = "Sistema"


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Codigo del proveedor tal como aparece en el reporte de compras.
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(200), default=None)
    # Nombre de la lista de SharePoint asignada a este proveedor.
    sharepoint_list: Mapped[str | None] = mapped_column(String(200), default=None)
    # Si es False, el precio no se publica en la lista del proveedor.
    share_price: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    orders: Mapped[list["PurchaseOrder"]] = relationship(back_populates="supplier")

    def __repr__(self) -> str:  # pragma: no cover - ayuda en consola
        return f"<Supplier {self.code} {self.name}>"


class PurchaseOrder(Base):
    """Una linea de orden de compra. La llave del negocio es (orden, linea)."""

    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("po_number", "line_number", name="uq_po_line"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # --- Campos que controla el sistema (se sobreescriben en cada carga) ------
    po_number: Mapped[str] = mapped_column(String(50), index=True)
    line_number: Mapped[int] = mapped_column(Integer)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    part_number: Mapped[str | None] = mapped_column(String(100), default=None)
    description: Mapped[str | None] = mapped_column(String(500), default=None)
    quantity: Mapped[float | None] = mapped_column(Numeric(18, 3), default=None)
    unit: Mapped[str | None] = mapped_column(String(20), default=None)
    unit_price: Mapped[float | None] = mapped_column(Numeric(18, 4), default=None)
    currency: Mapped[str | None] = mapped_column(String(10), default=None)
    required_date: Mapped[date | None] = mapped_column(Date, default=None)

    # --- Campos que controla el proveedor (se conservan entre cargas) --------
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, native_enum=False, length=20), default=OrderStatus.PENDIENTE
    )
    promised_date: Mapped[date | None] = mapped_column(Date, default=None)
    supplier_comment: Mapped[str | None] = mapped_column(Text, default=None)
    last_supplier_update_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_supplier_update_by: Mapped[str | None] = mapped_column(String(200), default=None)

    # --- Control interno -----------------------------------------------------
    # Una orden se cierra cuando deja de aparecer en el reporte de compras.
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    first_seen_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("upload_batches.id"), default=None
    )
    last_seen_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("upload_batches.id"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    supplier: Mapped[Supplier] = relationship(back_populates="orders")
    changes: Mapped[list["OrderChange"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderChange.changed_at.desc()"
    )

    # --- Reglas derivadas ----------------------------------------------------

    @property
    def sin_respuesta(self) -> bool:
        return self.status is OrderStatus.PENDIENTE and not self.is_closed

    def esta_retrasada(self, hoy: date | None = None) -> bool:
        """Retrasada si el proveedor lo declaro, o si ya vencio una fecha comprometida."""
        hoy = hoy or date.today()
        if self.is_closed or self.status is OrderStatus.ENTREGADA:
            return False
        if self.status is OrderStatus.RETRASADA:
            return True
        referencia = self.promised_date or self.required_date
        return referencia is not None and referencia < hoy

    def dias_de_retraso(self, hoy: date | None = None) -> int:
        hoy = hoy or date.today()
        referencia = self.promised_date or self.required_date
        if referencia is None or referencia >= hoy:
            return 0
        return (hoy - referencia).days

    @property
    def total(self) -> float | None:
        if self.quantity is None or self.unit_price is None:
            return None
        return float(self.quantity) * float(self.unit_price)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PurchaseOrder {self.po_number}-{self.line_number} {self.status.value}>"


class OrderChange(Base):
    """Historial completo de cambios, campo por campo."""

    __tablename__ = "order_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    field: Mapped[str] = mapped_column(String(50))
    old_value: Mapped[str | None] = mapped_column(String(500), default=None)
    new_value: Mapped[str | None] = mapped_column(String(500), default=None)
    source: Mapped[ChangeSource] = mapped_column(
        Enum(ChangeSource, native_enum=False, length=30), default=ChangeSource.SISTEMA
    )
    actor: Mapped[str | None] = mapped_column(String(200), default=None)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    order: Mapped[PurchaseOrder] = relationship(back_populates="changes")


class UploadBatch(Base):
    """Una carga del reporte de ordenes abiertas."""

    __tablename__ = "upload_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(300))
    uploaded_by: Mapped[str | None] = mapped_column(String(200), default=None)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_created: Mapped[int] = mapped_column(Integer, default=0)
    rows_updated: Mapped[int] = mapped_column(Integer, default=0)
    rows_unchanged: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    orders_closed: Mapped[int] = mapped_column(Integer, default=0)
    suppliers_created: Mapped[int] = mapped_column(Integer, default=0)
    # Errores de validacion en texto plano, una linea por problema.
    error_log: Mapped[str | None] = mapped_column(Text, default=None)
