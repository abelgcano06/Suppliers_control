"""API de sincronizacion. Unico consumidor: Power Automate.

Bajada:  GET  /api/v1/suppliers
         GET  /api/v1/suppliers/{code}/orders
Subida:  POST /api/v1/supplier-updates

Todas requieren el header X-API-Key.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import OrderStatus, Supplier
from app.schemas import (
    OrderOut,
    StatusCatalogOut,
    SupplierOut,
    SupplierUpdateBatchIn,
    SupplierUpdateBatchOut,
    UpdateResultOut,
)
from app.security import require_api_key
from app.services import sync

router = APIRouter(prefix="/api/v1", tags=["sincronizacion"], dependencies=[Depends(require_api_key)])


@router.get("/status-catalog", response_model=StatusCatalogOut)
def status_catalog() -> StatusCatalogOut:
    """Valores de la columna Choice "Status" en las listas de SharePoint."""
    return StatusCatalogOut(
        values=[s.value for s in OrderStatus],
        editable_por_proveedor=[s.value for s in OrderStatus.editable_por_proveedor()],
    )


@router.get("/suppliers", response_model=list[SupplierOut])
def list_suppliers(
    only_active: bool = Query(True, description="Solo proveedores dados de alta y activos."),
    db: Session = Depends(get_db),
) -> list[Supplier]:
    query = select(Supplier).order_by(Supplier.name)
    if only_active:
        query = query.where(Supplier.active == True)  # noqa: E712
    return list(db.scalars(query).all())


@router.get(
    "/suppliers/{code}/orders",
    response_model=list[OrderOut],
    # Sin esto, los campos que el servicio omite (el precio de un proveedor que no
    # lo tiene autorizado) viajarian como null en vez de no existir.
    response_model_exclude_unset=True,
)
def supplier_orders(
    code: str,
    include_closed: bool = Query(
        False, description="Incluye las ordenes cerradas para que el flujo las retire de la lista."
    ),
    db: Session = Depends(get_db),
) -> list[dict]:
    supplier = sync.get_supplier_by_code(db, code)
    if supplier is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Proveedor '{code}' no encontrado.")
    if not supplier.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Proveedor '{code}' esta dado de baja.")
    return sync.orders_for_supplier(db, supplier, include_closed=include_closed)


@router.post("/supplier-updates", response_model=SupplierUpdateBatchOut)
def supplier_updates(
    payload: SupplierUpdateBatchIn,
    db: Session = Depends(get_db),
) -> SupplierUpdateBatchOut:
    """Recibe el status que los proveedores seleccionaron en su lista.

    Cada cambio se valida por separado: uno invalido no cancela el resto del lote.
    """
    supplier = sync.get_supplier_by_code(db, payload.supplier_code)
    if supplier is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Proveedor '{payload.supplier_code}' no encontrado."
        )
    if not supplier.active:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"Proveedor '{payload.supplier_code}' esta dado de baja."
        )

    resultados: list[UpdateResultOut] = []
    aplicados = 0
    rechazados = 0

    for cambio in payload.updates:
        try:
            outcome = sync.apply_supplier_update(
                db,
                supplier=supplier,
                po_number=cambio.po_number,
                line_number=cambio.line_number,
                status=cambio.status,
                changed_by=cambio.changed_by,
                changed_at=cambio.changed_at,
            )
        except sync.SyncError as exc:
            rechazados += 1
            resultados.append(
                UpdateResultOut(
                    po_number=cambio.po_number,
                    line_number=cambio.line_number,
                    applied=False,
                    changed_fields=[],
                    message=str(exc),
                )
            )
            continue

        if outcome.applied:
            aplicados += 1
        resultados.append(
            UpdateResultOut(
                po_number=outcome.po_number,
                line_number=outcome.line_number,
                applied=outcome.applied,
                changed_fields=outcome.changed_fields,
                message=outcome.message,
            )
        )

    db.commit()
    return SupplierUpdateBatchOut(
        supplier_code=supplier.code,
        received=len(payload.updates),
        applied=aplicados,
        rejected=rechazados,
        results=resultados,
    )
