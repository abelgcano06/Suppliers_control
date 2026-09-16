"""Pruebas de la carga del reporte: validacion, upsert y cierre de ordenes."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import ChangeSource, OrderChange, OrderStatus, PurchaseOrder, Supplier
from app.services.excel_import import ExcelImportError, import_orders, parse_date, parse_number
from tests.conftest import construir_excel, fila


def cargar(db, filas, **kwargs):
    return import_orders(db, construir_excel(filas), "reporte.xlsx", **kwargs)


def test_carga_inicial_da_de_alta_ordenes_y_proveedores(db):
    resultado = cargar(db, [fila(), fila(linea=2), fila(orden="OC-1002", codigo="PROV-B", nombre="Refacciones BC")])

    assert resultado.rows_total == 3
    assert resultado.rows_created == 3
    assert resultado.rows_rejected == 0
    assert resultado.suppliers_created == 2
    assert db.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-1001")).status is OrderStatus.PENDIENTE


def test_encuentra_el_encabezado_aunque_haya_filas_de_titulo(db):
    contenido = construir_excel([fila()], filas_previas=4)
    resultado = import_orders(db, contenido, "reporte.xlsx")
    assert resultado.rows_created == 1


def test_acepta_encabezados_con_acentos_y_mayusculas(db):
    encabezados = ["ORDEN", "Línea", "Código Proveedor", "Proveedor", "Núm. Parte",
                   "Descripción", "Cantidad", "Unidad", "Precio", "Moneda", "Fecha Requerida"]
    contenido = construir_excel([fila()], encabezados=encabezados)
    resultado = import_orders(db, contenido, "reporte.xlsx")
    assert resultado.rows_created == 1
    orden = db.scalar(select(PurchaseOrder))
    assert orden.part_number == "PN-4471"


def test_falla_si_faltan_columnas_obligatorias(db):
    contenido = construir_excel([["Pinturas del Norte", 10]], encabezados=["Proveedor", "Cantidad"])
    with pytest.raises(ExcelImportError, match="encabezados"):
        import_orders(db, contenido, "reporte.xlsx")


def test_archivo_que_no_es_excel(db):
    with pytest.raises(ExcelImportError, match="No se pudo leer"):
        import_orders(db, b"esto no es un xlsx", "reporte.xlsx")


def test_rechaza_filas_invalidas_sin_tumbar_la_carga(db):
    filas = [
        fila(),
        fila(orden=None, linea=2),                        # sin numero de orden
        fila(orden="OC-1003", linea="dos"),               # linea no numerica
        fila(orden="OC-1004", cantidad=-5),               # cantidad negativa
        fila(orden="OC-1005", requerida="32/13/2026"),    # fecha imposible
        fila(orden="OC-1006"),
    ]
    resultado = cargar(db, filas)

    assert resultado.rows_created == 2
    assert resultado.rows_rejected == 4
    assert len(resultado.errors) == 4
    assert all(e.startswith("Fila ") for e in resultado.errors)


def test_rechaza_lineas_duplicadas_dentro_del_mismo_archivo(db):
    resultado = cargar(db, [fila(), fila()])
    assert resultado.rows_created == 1
    assert resultado.rows_rejected == 1
    assert "repetida" in resultado.errors[0]


def test_sin_alta_automatica_se_rechaza_el_proveedor_desconocido(db):
    resultado = cargar(db, [fila()], create_missing_suppliers=False)
    assert resultado.rows_created == 0
    assert resultado.rows_rejected == 1
    assert "no esta dado de alta" in resultado.errors[0]


def test_la_segunda_carga_sobreescribe_los_campos_del_sistema(db):
    cargar(db, [fila(cantidad=10, precio=845.5)])
    orden = db.scalar(select(PurchaseOrder))
    assert float(orden.quantity) == 10

    resultado = cargar(db, [fila(cantidad=25, precio=900.0, descripcion="Filtro nuevo")])

    db.refresh(orden)
    assert resultado.rows_updated == 1
    assert float(orden.quantity) == 25
    assert float(orden.unit_price) == 900.0
    assert orden.description == "Filtro nuevo"

    campos = {c.field for c in db.scalars(select(OrderChange)).all()}
    assert {"quantity", "unit_price", "description"} <= campos


def test_la_carga_no_toca_lo_que_capturo_el_proveedor(db):
    """Regla central: el Excel manda sobre los campos del sistema, nunca sobre los del proveedor."""
    cargar(db, [fila()])
    orden = db.scalar(select(PurchaseOrder))
    orden.status = OrderStatus.EN_PROCESO
    orden.promised_date = date.today() + timedelta(days=15)
    orden.supplier_comment = "En fabricacion"
    db.commit()

    cargar(db, [fila(cantidad=99)])

    db.refresh(orden)
    assert float(orden.quantity) == 99                    # el sistema si se actualizo
    assert orden.status is OrderStatus.EN_PROCESO         # la respuesta del proveedor se conservo
    assert orden.promised_date == date.today() + timedelta(days=15)
    assert orden.supplier_comment == "En fabricacion"


def test_una_carga_identica_no_marca_cambios(db):
    cargar(db, [fila()])
    resultado = cargar(db, [fila()])
    assert resultado.rows_updated == 0
    assert resultado.rows_unchanged == 1


def test_cierra_las_ordenes_que_ya_no_vienen_en_el_reporte(db):
    cargar(db, [fila(), fila(orden="OC-1002")])
    resultado = cargar(db, [fila()])

    assert resultado.orders_closed == 1
    cerrada = db.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-1002"))
    assert cerrada.is_closed is True
    assert cerrada.closed_at is not None


def test_el_alcance_por_proveedor_no_cierra_ordenes_de_otros(db):
    cargar(db, [fila(codigo="PROV-A"), fila(orden="OC-2001", codigo="PROV-B", nombre="Refacciones BC")])

    # Carga parcial: solo el reporte del proveedor A.
    resultado = cargar(db, [fila(codigo="PROV-A")], close_scope="suppliers_in_file")

    assert resultado.orders_closed == 0
    otra = db.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-2001"))
    assert otra.is_closed is False


def test_alcance_all_si_cierra_ordenes_de_otros_proveedores(db):
    cargar(db, [fila(codigo="PROV-A"), fila(orden="OC-2001", codigo="PROV-B", nombre="Refacciones BC")])
    resultado = cargar(db, [fila(codigo="PROV-A")], close_scope="all")

    assert resultado.orders_closed == 1


def test_close_scope_none_no_cierra_nada(db):
    cargar(db, [fila(), fila(orden="OC-1002")])
    resultado = cargar(db, [fila()], close_scope="none")
    assert resultado.orders_closed == 0


def test_una_orden_cerrada_se_reabre_si_vuelve_al_reporte(db):
    cargar(db, [fila(), fila(orden="OC-1002")])
    cargar(db, [fila()])
    cerrada = db.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-1002"))
    assert cerrada.is_closed is True

    cargar(db, [fila(), fila(orden="OC-1002")])

    db.refresh(cerrada)
    assert cerrada.is_closed is False
    assert cerrada.closed_at is None


def test_el_cambio_de_proveedor_se_registra(db):
    cargar(db, [fila(codigo="PROV-A")])
    cargar(db, [fila(codigo="PROV-B", nombre="Refacciones BC")], close_scope="none")

    orden = db.scalar(select(PurchaseOrder))
    proveedor_b = db.scalar(select(Supplier).where(Supplier.code == "PROV-B"))
    assert orden.supplier_id == proveedor_b.id
    assert any(c.field == "supplier_id" for c in db.scalars(select(OrderChange)).all())


def test_todo_cambio_de_la_carga_queda_marcado_como_excel(db):
    cargar(db, [fila()], uploaded_by="ana@tmmbc")
    cargar(db, [fila(cantidad=50)], uploaded_by="ana@tmmbc")

    cambios = db.scalars(select(OrderChange)).all()
    assert cambios
    assert all(c.source is ChangeSource.EXCEL for c in cambios)
    assert all(c.actor == "ana@tmmbc" for c in cambios)


def test_columnas_desconocidas_se_reportan_pero_no_estorban(db):
    encabezados = ["Orden", "Linea", "Codigo Proveedor", "Comprador", "Centro de costos"]
    contenido = construir_excel([["OC-1001", 1, "PROV-A", "Luis", "CC-880"]], encabezados=encabezados)
    resultado = import_orders(db, contenido, "reporte.xlsx")

    assert resultado.rows_created == 1
    assert "Comprador" in resultado.ignored_columns
    assert "Centro de costos" in resultado.ignored_columns


@pytest.mark.parametrize(
    "entrada,esperado",
    [("1,234.56", 1234.56), ("$1,000", 1000), ("(100)", -100), (42, 42), (None, None), ("", None)],
)
def test_parse_number(entrada, esperado):
    resultado = parse_number(entrada)
    assert (float(resultado) if resultado is not None else None) == esperado


@pytest.mark.parametrize(
    "entrada,esperado",
    [("2026-10-15", date(2026, 10, 15)), ("15/10/2026", date(2026, 10, 15)),
     ("15.10.2026", date(2026, 10, 15)), (None, None)],
)
def test_parse_date(entrada, esperado):
    assert parse_date(entrada) == esperado


def test_parse_date_rechaza_basura():
    with pytest.raises(ValueError, match="no es una fecha valida"):
        parse_date("manana")
