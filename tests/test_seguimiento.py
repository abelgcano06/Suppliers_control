"""Pruebas del seguimiento interno: lo que captura mantenimiento, no el proveedor."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import ChangeSource, OrderChange, OrderStatus, PurchaseOrder
from app.services.excel_import import import_orders
from app.services.seguimiento import actualizar_seguimiento
from tests.conftest import construir_excel, fila


@pytest.fixture
def orden(db):
    import_orders(db, construir_excel([fila(orden="OC-1001", linea=1, codigo="PROV-A")]),
                  "reporte.xlsx")
    return db.scalar(select(PurchaseOrder))


def test_captura_fecha_promesa_y_nota(db, orden):
    cambios = actualizar_seguimiento(
        db, orden, promised_date=date(2026, 11, 5),
        internal_note="Hablo el proveedor: sale el viernes", actor="ana@tmmbc",
    )
    db.commit()

    assert set(cambios) == {"promised_date", "internal_note"}
    assert orden.promised_date == date(2026, 11, 5)
    assert orden.internal_note == "Hablo el proveedor: sale el viernes"


def test_el_seguimiento_queda_marcado_como_del_portal(db, orden):
    actualizar_seguimiento(db, orden, promised_date=date(2026, 11, 5), actor="ana@tmmbc")
    db.commit()

    cambio = db.scalar(select(OrderChange).where(OrderChange.field == "promised_date"))
    assert cambio.source is ChangeSource.PORTAL
    assert cambio.actor == "ana@tmmbc"
    assert cambio.new_value == "2026-11-05"


def test_guardar_lo_mismo_no_genera_ruido(db, orden):
    actualizar_seguimiento(db, orden, promised_date=date(2026, 11, 5))
    db.commit()
    cambios = actualizar_seguimiento(db, orden, promised_date=date(2026, 11, 5))

    assert cambios == []
    assert len(db.scalars(select(OrderChange).where(OrderChange.field == "promised_date")).all()) == 1


def test_se_puede_borrar_la_fecha_promesa(db, orden):
    actualizar_seguimiento(db, orden, promised_date=date(2026, 11, 5))
    db.commit()

    cambios = actualizar_seguimiento(db, orden, limpiar_fecha=True)
    db.commit()

    assert cambios == ["promised_date"]
    assert orden.promised_date is None


def test_el_seguimiento_no_toca_el_status(db, orden):
    """El status es del proveedor; el portal no lo cambia por aqui."""
    actualizar_seguimiento(db, orden, promised_date=date(2026, 11, 5), internal_note="algo")
    db.commit()
    assert orden.status is OrderStatus.PENDIENTE


def test_la_nota_se_recorta_si_viene_larguisima(db, orden):
    actualizar_seguimiento(db, orden, internal_note="x" * 5000)
    db.commit()
    assert len(orden.internal_note) == 2000


def test_una_nota_vacia_se_guarda_como_nada(db, orden):
    actualizar_seguimiento(db, orden, internal_note="Algo")
    db.commit()
    actualizar_seguimiento(db, orden, internal_note="   ")
    db.commit()
    assert orden.internal_note is None


# --------------------------------------------------------------------------- #
# Desde el portal
# --------------------------------------------------------------------------- #


def test_guardar_seguimiento_desde_la_pantalla(client, db, orden):
    promesa = (date.today() + timedelta(days=12)).isoformat()
    respuesta = client.post(
        f"/ordenes/{orden.id}/seguimiento",
        data={"fecha_promesa": promesa, "nota": "Va retrasado por aduana",
              "capturado_por": "ana@tmmbc"},
        follow_redirects=False,
    )

    assert respuesta.status_code == 303
    db.refresh(orden)
    assert orden.promised_date.isoformat() == promesa
    assert orden.internal_note == "Va retrasado por aduana"


def test_una_fecha_invalida_se_rechaza(client, db, orden):
    respuesta = client.post(
        f"/ordenes/{orden.id}/seguimiento",
        data={"fecha_promesa": "32/13/2026", "nota": ""},
        follow_redirects=False,
    )
    assert respuesta.status_code == 400


def test_seguimiento_de_una_orden_inexistente(client, db):
    assert client.post("/ordenes/99999/seguimiento", data={"nota": "x"}).status_code == 404


def test_la_pantalla_muestra_el_formulario_de_seguimiento(client, db, orden):
    html = client.get(f"/ordenes/{orden.id}").text
    assert "Seguimiento interno" in html
    assert "no aparece en la lista del proveedor" in html
