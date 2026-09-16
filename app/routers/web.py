"""Portal interno: las pantallas que usa el equipo de mantenimiento."""

from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.db import get_db
from app.models import ChangeSource, OrderChange, OrderStatus, PurchaseOrder, Supplier, UploadBatch
from app.services import dashboard
from app.services.excel_import import ExcelImportError, import_orders

router = APIRouter(tags=["portal"])
templates = Jinja2Templates(directory="app/templates")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


def _context(**extra) -> dict:
    settings = get_settings()
    base = {
        "settings": settings,
        "hoy": date.today(),
        "OrderStatus": OrderStatus,
    }
    base.update(extra)
    return base


# --------------------------------------------------------------------------- #
# Tablero
# --------------------------------------------------------------------------- #


@router.get("/", response_class=HTMLResponse)
def tablero(
    request: Request,
    proveedor: int | None = None,
    status_filtro: str | None = None,
    alerta: str | None = None,
    q: str | None = None,
    cerradas: bool = False,
    db: Session = Depends(get_db),
):
    filtros = dashboard.OrderFilters(
        supplier_id=proveedor,
        status=_status_o_none(status_filtro),
        alerta=alerta or None,
        search=q,
        include_closed=cerradas,
    )
    ordenes = dashboard.query_orders(db, filtros)
    return templates.TemplateResponse(
        request,
        "tablero.html",
        _context(
            ordenes=ordenes,
            kpis=dashboard.compute_kpis(db),
            horas_respuesta=dashboard.tiempo_promedio_respuesta_horas(db),
            proveedores=db.scalars(select(Supplier).order_by(Supplier.name)).all(),
            filtros=filtros,
            filtro_status=status_filtro or "",
            filtro_alerta=alerta or "",
            busqueda=q or "",
            ultima_carga=db.scalar(select(UploadBatch).order_by(desc(UploadBatch.uploaded_at))),
        ),
    )


def _status_o_none(valor: str | None) -> OrderStatus | None:
    if not valor:
        return None
    for s in OrderStatus:
        if valor == s.name or valor == s.value:
            return s
    return None


@router.get("/ordenes/{order_id}", response_class=HTMLResponse)
def detalle_orden(order_id: int, request: Request, db: Session = Depends(get_db)):
    orden = db.scalar(
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.supplier), selectinload(PurchaseOrder.changes))
        .where(PurchaseOrder.id == order_id)
    )
    if orden is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Orden no encontrada.")
    return templates.TemplateResponse(request, "orden.html", _context(orden=orden))


@router.get("/export.csv")
def exportar_csv(
    proveedor: int | None = None,
    status_filtro: str | None = None,
    alerta: str | None = None,
    q: str | None = None,
    cerradas: bool = False,
    db: Session = Depends(get_db),
):
    """El tablero tal como se ve, en CSV, para pegarlo en un reporte."""
    filtros = dashboard.OrderFilters(
        supplier_id=proveedor,
        status=_status_o_none(status_filtro),
        alerta=alerta or None,
        search=q,
        include_closed=cerradas,
    )
    ordenes = dashboard.query_orders(db, filtros)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Orden", "Linea", "Proveedor", "Parte", "Descripcion", "Cantidad", "Unidad",
            "Fecha requerida", "Status", "Fecha promesa", "Dias de retraso",
            "Comentario del proveedor", "Ultima respuesta", "Cerrada",
        ]
    )
    hoy = date.today()
    for o in ordenes:
        writer.writerow(
            [
                o.po_number, o.line_number, o.supplier.name if o.supplier else "",
                o.part_number or "", o.description or "",
                float(o.quantity) if o.quantity is not None else "", o.unit or "",
                o.required_date.isoformat() if o.required_date else "",
                o.status.value,
                o.promised_date.isoformat() if o.promised_date else "",
                o.dias_de_retraso(hoy) if o.esta_retrasada(hoy) else 0,
                o.supplier_comment or "",
                o.last_supplier_update_at.isoformat(sep=" ", timespec="minutes")
                if o.last_supplier_update_at else "",
                "Si" if o.is_closed else "No",
            ]
        )

    buffer.seek(0)
    nombre = f"ordenes-{hoy.isoformat()}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


# --------------------------------------------------------------------------- #
# Carga del reporte
# --------------------------------------------------------------------------- #


@router.get("/cargar", response_class=HTMLResponse)
def pantalla_carga(request: Request, db: Session = Depends(get_db)):
    cargas = db.scalars(
        select(UploadBatch).order_by(desc(UploadBatch.uploaded_at)).limit(15)
    ).all()
    return templates.TemplateResponse(
        request, "cargar.html", _context(cargas=cargas, resultado=None, error=None)
    )


@router.post("/cargar", response_class=HTMLResponse)
async def procesar_carga(
    request: Request,
    archivo: UploadFile = File(...),
    cargado_por: str = Form(""),
    alcance_cierre: str = Form("suppliers_in_file"),
    alta_automatica: bool = Form(False),
    db: Session = Depends(get_db),
):
    contenido = await archivo.read()
    resultado = None
    error = None

    if alcance_cierre not in ("all", "suppliers_in_file", "none"):
        alcance_cierre = "suppliers_in_file"

    if not contenido:
        error = "El archivo llego vacio."
    elif len(contenido) > MAX_UPLOAD_BYTES:
        error = f"El archivo pesa mas de {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
    elif not (archivo.filename or "").lower().endswith((".xlsx", ".xlsm")):
        error = "Solo se aceptan archivos .xlsx o .xlsm. Guarda el reporte en ese formato."
    else:
        try:
            resultado = import_orders(
                db,
                content=contenido,
                filename=archivo.filename or "reporte.xlsx",
                uploaded_by=cargado_por.strip() or None,
                close_scope=alcance_cierre,
                create_missing_suppliers=alta_automatica,
            )
        except ExcelImportError as exc:
            db.rollback()
            error = str(exc)

    cargas = db.scalars(
        select(UploadBatch).order_by(desc(UploadBatch.uploaded_at)).limit(15)
    ).all()
    return templates.TemplateResponse(
        request,
        "cargar.html",
        _context(cargas=cargas, resultado=resultado, error=error),
    )


@router.get("/plantilla.csv")
def plantilla():
    """Plantilla con los encabezados que el portal espera del reporte."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Orden", "Linea", "Codigo Proveedor", "Proveedor", "Parte", "Descripcion",
         "Cantidad", "Unidad", "Precio Unitario", "Moneda", "Fecha Requerida"]
    )
    writer.writerow(
        ["OC-1001", "1", "PROV-A", "Pinturas del Norte", "PN-4471",
         "Filtro de cabina de pintura", "12", "PZA", "845.50", "MXN", "2026-10-15"]
    )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="plantilla-ordenes.csv"'},
    )


# --------------------------------------------------------------------------- #
# Proveedores
# --------------------------------------------------------------------------- #


@router.get("/proveedores", response_class=HTMLResponse)
def pantalla_proveedores(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "proveedores.html",
        _context(resumen=dashboard.supplier_summaries(db), error=None),
    )


@router.post("/proveedores")
def alta_proveedor(
    request: Request,
    codigo: str = Form(...),
    nombre: str = Form(...),
    correo: str = Form(""),
    lista_sharepoint: str = Form(""),
    compartir_precio: bool = Form(False),
    db: Session = Depends(get_db),
):
    codigo = codigo.strip()
    error = None
    if not codigo or not nombre.strip():
        error = "El codigo y el nombre son obligatorios."
    elif db.scalar(select(Supplier).where(Supplier.code == codigo)):
        error = f"Ya existe un proveedor con el codigo '{codigo}'. Usa otro o edita el existente."

    if error:
        # Se regresa la pantalla con el mensaje, no un error crudo.
        return templates.TemplateResponse(
            request,
            "proveedores.html",
            _context(resumen=dashboard.supplier_summaries(db), error=error),
            status_code=status.HTTP_409_CONFLICT,
        )

    db.add(
        Supplier(
            code=codigo,
            name=nombre.strip(),
            contact_email=correo.strip() or None,
            sharepoint_list=lista_sharepoint.strip() or None,
            share_price=compartir_precio,
        )
    )
    db.commit()
    return RedirectResponse("/proveedores", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/proveedores/{supplier_id}")
def editar_proveedor(
    supplier_id: int,
    nombre: str = Form(...),
    correo: str = Form(""),
    lista_sharepoint: str = Form(""),
    compartir_precio: bool = Form(False),
    activo: bool = Form(False),
    db: Session = Depends(get_db),
):
    proveedor = db.get(Supplier, supplier_id)
    if proveedor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proveedor no encontrado.")
    proveedor.name = nombre.strip() or proveedor.name
    proveedor.contact_email = correo.strip() or None
    proveedor.sharepoint_list = lista_sharepoint.strip() or None
    proveedor.share_price = compartir_precio
    proveedor.active = activo
    db.commit()
    return RedirectResponse("/proveedores", status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------- #
# Historial de cambios
# --------------------------------------------------------------------------- #


@router.get("/historial", response_class=HTMLResponse)
def historial(request: Request, origen: str | None = None, db: Session = Depends(get_db)):
    query = (
        select(OrderChange)
        .options(selectinload(OrderChange.order).selectinload(PurchaseOrder.supplier))
        .order_by(desc(OrderChange.changed_at))
        .limit(300)
    )
    if origen:
        for fuente in ChangeSource:
            if origen == fuente.name:
                query = query.where(OrderChange.source == fuente)
                break
    return templates.TemplateResponse(
        request,
        "historial.html",
        _context(
            cambios=db.scalars(query).all(),
            ChangeSource=ChangeSource,
            filtro_origen=origen or "",
        ),
    )
