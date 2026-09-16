"""Contratos de la API de sincronizacion (lo que Power Automate manda y recibe)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    contact_email: str | None = None
    sharepoint_list: str | None = None
    share_price: bool
    active: bool


class OrderOut(BaseModel):
    """Una fila tal como debe quedar en la lista de SharePoint del proveedor."""

    key: str
    po_number: str
    line_number: int
    part_number: str | None = None
    description: str | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_price: float | None = None
    currency: str | None = None
    required_date: str | None = None
    status: str
    promised_date: str | None = None
    supplier_comment: str | None = None
    is_closed: bool
    updated_at: str | None = None


class SupplierUpdateIn(BaseModel):
    """Un cambio capturado por el proveedor en su lista.

    Solo se aceptan estos tres campos editables. Cualquier otro dato que mande
    el flujo se ignora, y la siguiente bajada restaura los campos del sistema.
    """

    po_number: str = Field(min_length=1, max_length=50)
    line_number: int = Field(ge=0)
    status: str | None = None
    promised_date: date | None = None
    supplier_comment: str | None = Field(default=None, max_length=2000)
    changed_by: str | None = Field(default=None, max_length=200)
    changed_at: datetime | None = None


class SupplierUpdateBatchIn(BaseModel):
    supplier_code: str = Field(min_length=1, max_length=50)
    updates: list[SupplierUpdateIn] = Field(min_length=1, max_length=500)


class UpdateResultOut(BaseModel):
    po_number: str
    line_number: int
    applied: bool
    changed_fields: list[str] = []
    message: str


class SupplierUpdateBatchOut(BaseModel):
    supplier_code: str
    received: int
    applied: int
    rejected: int
    results: list[UpdateResultOut]


class StatusCatalogOut(BaseModel):
    """El catalogo cerrado con el que se alimenta la columna Choice de SharePoint."""

    values: list[str]
    editable_por_proveedor: list[str]
