"""Pruebas de la API que consume Power Automate.

El foco esta en las reglas de seguridad del concepto: un proveedor solo ve y solo
escribe lo suyo, el status es un catalogo cerrado, y los campos del sistema son
intocables desde afuera.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import ChangeSource, OrderChange, OrderStatus, PurchaseOrder, Supplier
from app.services.excel_import import import_orders
from tests.conftest import API_KEY, construir_excel, fila

CABECERA = {"X-API-Key": API_KEY}


@pytest.fixture
def datos(db):
    """Dos proveedores con ordenes, tal como quedarian despues de una carga."""
    import_orders(
        db,
        construir_excel([
            fila(orden="OC-1001", linea=1, codigo="PROV-A"),
            fila(orden="OC-1001", linea=2, codigo="PROV-A"),
            fila(orden="OC-2001", linea=1, codigo="PROV-B", nombre="Refacciones BC"),
        ]),
        "reporte.xlsx",
    )
    for proveedor in db.scalars(select(Supplier)).all():
        proveedor.sharepoint_list = f"Ordenes - {proveedor.code}"
    db.commit()
    return db


# --------------------------------------------------------------------------- #
# Autenticacion
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "ruta", ["/api/v1/suppliers", "/api/v1/suppliers/PROV-A/orders", "/api/v1/status-catalog"]
)
def test_la_api_exige_clave(client, datos, ruta):
    assert client.get(ruta).status_code == 401


def test_clave_incorrecta_es_rechazada(client, datos):
    respuesta = client.get("/api/v1/suppliers", headers={"X-API-Key": "clave-equivocada"})
    assert respuesta.status_code == 401


def test_el_portal_interno_no_pide_clave(client, datos):
    assert client.get("/").status_code == 200


# --------------------------------------------------------------------------- #
# Bajada: portal -> SharePoint
# --------------------------------------------------------------------------- #


def test_cada_proveedor_recibe_solo_sus_ordenes(client, datos):
    a = client.get("/api/v1/suppliers/PROV-A/orders", headers=CABECERA).json()
    b = client.get("/api/v1/suppliers/PROV-B/orders", headers=CABECERA).json()

    assert {o["po_number"] for o in a} == {"OC-1001"}
    assert {o["po_number"] for o in b} == {"OC-2001"}
    assert len(a) == 2 and len(b) == 1


def test_el_precio_se_omite_si_el_proveedor_no_lo_tiene_autorizado(client, datos):
    ordenes = client.get("/api/v1/suppliers/PROV-A/orders", headers=CABECERA).json()
    assert all("unit_price" not in o for o in ordenes)
    assert all("currency" not in o for o in ordenes)


def test_el_precio_se_publica_si_esta_autorizado(client, datos):
    proveedor = datos.scalar(select(Supplier).where(Supplier.code == "PROV-A"))
    proveedor.share_price = True
    datos.commit()

    ordenes = client.get("/api/v1/suppliers/PROV-A/orders", headers=CABECERA).json()
    assert all("unit_price" in o for o in ordenes)


def test_las_ordenes_cerradas_se_omiten_por_defecto(client, datos):
    orden = datos.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-2001"))
    orden.is_closed = True
    datos.commit()

    assert client.get("/api/v1/suppliers/PROV-B/orders", headers=CABECERA).json() == []
    con_cerradas = client.get(
        "/api/v1/suppliers/PROV-B/orders?include_closed=true", headers=CABECERA
    ).json()
    assert len(con_cerradas) == 1


def test_proveedor_inexistente_o_dado_de_baja(client, datos):
    assert client.get("/api/v1/suppliers/PROV-Z/orders", headers=CABECERA).status_code == 404

    proveedor = datos.scalar(select(Supplier).where(Supplier.code == "PROV-B"))
    proveedor.active = False
    datos.commit()
    assert client.get("/api/v1/suppliers/PROV-B/orders", headers=CABECERA).status_code == 403


def test_el_catalogo_de_status_no_ofrece_pendiente_al_proveedor(client, datos):
    catalogo = client.get("/api/v1/status-catalog", headers=CABECERA).json()
    assert "Pendiente" in catalogo["values"]
    assert "Pendiente" not in catalogo["editable_por_proveedor"]
    assert "Retrasada" in catalogo["editable_por_proveedor"]


# --------------------------------------------------------------------------- #
# Subida: SharePoint -> portal
# --------------------------------------------------------------------------- #


def enviar(client, supplier_code, updates):
    return client.post(
        "/api/v1/supplier-updates",
        headers=CABECERA,
        json={"supplier_code": supplier_code, "updates": updates},
    )


def test_el_proveedor_actualiza_el_status(client, datos):
    respuesta = enviar(client, "PROV-A", [{
        "po_number": "OC-1001", "line_number": 1,
        "status": "En proceso", "changed_by": "invitado@prov-a.mx",
    }])

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["applied"] == 1 and cuerpo["rejected"] == 0

    orden = datos.scalar(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-1001", PurchaseOrder.line_number == 1)
    )
    datos.refresh(orden)
    assert orden.status is OrderStatus.EN_PROCESO
    assert orden.last_supplier_update_by == "invitado@prov-a.mx"


def test_el_status_es_lo_unico_que_el_proveedor_puede_cambiar(client, datos):
    """Aunque el cuerpo traiga mas campos, solo se aplica el status."""
    cuerpo = enviar(client, "PROV-A", [{
        "po_number": "OC-1001", "line_number": 1, "status": "Enviada",
        # Nada de esto debe entrar: no es del proveedor.
        "promised_date": (date.today() + timedelta(days=10)).isoformat(),
        "supplier_comment": "quiero escribir aqui",
        "internal_note": "y aqui tambien",
    }]).json()

    assert cuerpo["applied"] == 1
    assert cuerpo["results"][0]["changed_fields"] == ["status"]

    orden = datos.scalar(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-1001", PurchaseOrder.line_number == 1)
    )
    datos.refresh(orden)
    assert orden.status is OrderStatus.ENVIADA
    assert orden.promised_date is None
    assert orden.internal_note is None


def test_un_proveedor_no_puede_tocar_la_orden_de_otro(client, datos):
    """La regla mas importante del esquema: aislamiento entre proveedores."""
    respuesta = enviar(client, "PROV-B", [{
        "po_number": "OC-1001", "line_number": 1, "status": "Entregada",
    }])

    cuerpo = respuesta.json()
    assert cuerpo["applied"] == 0 and cuerpo["rejected"] == 1
    assert "no pertenece" in cuerpo["results"][0]["message"]

    orden = datos.scalar(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-1001", PurchaseOrder.line_number == 1)
    )
    assert orden.status is OrderStatus.PENDIENTE


def test_el_status_es_un_catalogo_cerrado(client, datos):
    cuerpo = enviar(client, "PROV-A", [{
        "po_number": "OC-1001", "line_number": 1, "status": "ya casi la mando",
    }]).json()

    assert cuerpo["rejected"] == 1
    assert "no esta en el catalogo" in cuerpo["results"][0]["message"]


def test_el_proveedor_no_puede_poner_pendiente(client, datos):
    cuerpo = enviar(client, "PROV-A", [{
        "po_number": "OC-1001", "line_number": 1, "status": "Pendiente",
    }]).json()

    assert cuerpo["rejected"] == 1
    assert "lo asigna el sistema" in cuerpo["results"][0]["message"]


def test_la_bajada_no_ofrece_campos_que_el_proveedor_no_controla(client, datos):
    """El payload que va a SharePoint no trae fecha promesa ni nota interna."""
    ordenes = client.get("/api/v1/suppliers/PROV-A/orders", headers=CABECERA).json()
    assert all("promised_date" not in o for o in ordenes)
    assert all("supplier_comment" not in o for o in ordenes)
    assert all("internal_note" not in o for o in ordenes)
    assert all("status" in o for o in ordenes)


def test_los_campos_del_sistema_se_ignoran_aunque_los_mande_el_flujo(client, datos):
    """Si el proveedor edito la cantidad en SharePoint, el portal la ignora."""
    cuerpo = enviar(client, "PROV-A", [{
        "po_number": "OC-1001", "line_number": 1, "status": "Recibida",
        "quantity": 9999, "unit_price": 1, "part_number": "HACK-1",
        "required_date": "2030-01-01", "is_closed": True,
    }]).json()

    assert cuerpo["applied"] == 1
    orden = datos.scalar(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-1001", PurchaseOrder.line_number == 1)
    )
    datos.refresh(orden)
    assert float(orden.quantity) == 10          # la del reporte, intacta
    assert orden.part_number == "PN-4471"
    assert orden.is_closed is False


def test_una_orden_cerrada_no_admite_cambios(client, datos):
    orden = datos.scalar(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-1001", PurchaseOrder.line_number == 1)
    )
    orden.is_closed = True
    datos.commit()

    cuerpo = enviar(client, "PROV-A", [{"po_number": "OC-1001", "line_number": 1, "status": "Enviada"}]).json()
    assert cuerpo["rejected"] == 1
    assert "cerrada" in cuerpo["results"][0]["message"]


def test_orden_inexistente_se_rechaza_sin_tumbar_el_lote(client, datos):
    cuerpo = enviar(client, "PROV-A", [
        {"po_number": "OC-9999", "line_number": 1, "status": "Enviada"},
        {"po_number": "OC-1001", "line_number": 2, "status": "Enviada"},
    ]).json()

    assert cuerpo["received"] == 2
    assert cuerpo["applied"] == 1
    assert cuerpo["rejected"] == 1


def test_reenviar_el_mismo_valor_no_genera_ruido(client, datos):
    payload = [{"po_number": "OC-1001", "line_number": 1, "status": "Recibida"}]
    enviar(client, "PROV-A", payload)
    cuerpo = enviar(client, "PROV-A", payload).json()

    assert cuerpo["applied"] == 0
    assert cuerpo["results"][0]["message"] == "Sin cambios que aplicar."
    cambios = datos.scalars(select(OrderChange).where(OrderChange.field == "status")).all()
    assert len(cambios) == 1


def test_cada_cambio_del_proveedor_queda_en_el_historial(client, datos):
    enviar(client, "PROV-A", [{
        "po_number": "OC-1001", "line_number": 1, "status": "Enviada",
        "changed_by": "invitado@prov-a.mx",
    }])

    cambios = datos.scalars(select(OrderChange)).all()
    del_proveedor = [c for c in cambios if c.source is ChangeSource.PROVEEDOR]
    assert {c.field for c in del_proveedor} == {"status"}
    assert all(c.actor == "invitado@prov-a.mx" for c in del_proveedor)
    assert del_proveedor[0].new_value == "Enviada"


def test_lote_de_proveedor_inexistente(client, datos):
    respuesta = enviar(client, "PROV-Z", [{"po_number": "OC-1001", "line_number": 1, "status": "Enviada"}])
    assert respuesta.status_code == 404


def test_lote_vacio_se_rechaza(client, datos):
    assert enviar(client, "PROV-A", []).status_code == 422
