"""Pruebas del portal interno: alertas, KPIs y pantallas."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import OrderStatus, PurchaseOrder, Supplier
from app.services import dashboard
from app.services.excel_import import import_orders
from tests.conftest import construir_excel, fila


@pytest.fixture
def datos(db):
    hoy = date.today()
    import_orders(
        db,
        construir_excel([
            # Vencida y sin contestar -> retrasada y sin respuesta.
            fila(orden="OC-1001", linea=1, codigo="PROV-A", requerida=hoy - timedelta(days=5)),
            # Proxima a vencer.
            fila(orden="OC-1002", linea=1, codigo="PROV-A", requerida=hoy + timedelta(days=3)),
            # Lejana.
            fila(orden="OC-1003", linea=1, codigo="PROV-A", requerida=hoy + timedelta(days=60)),
            fila(orden="OC-2001", linea=1, codigo="PROV-B", nombre="Refacciones BC",
                 requerida=hoy + timedelta(days=30)),
        ]),
        "reporte.xlsx",
    )
    return db


def orden(db, po):
    return db.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number == po))


# --------------------------------------------------------------------------- #
# Reglas de alerta
# --------------------------------------------------------------------------- #


def test_una_orden_recien_cargada_esta_sin_respuesta(datos):
    assert orden(datos, "OC-1003").sin_respuesta is True


def test_la_fecha_requerida_vencida_marca_retraso(datos):
    o = orden(datos, "OC-1001")
    assert o.esta_retrasada() is True
    assert o.dias_de_retraso() == 5


def test_la_fecha_promesa_manda_sobre_la_requerida(datos):
    o = orden(datos, "OC-1001")
    o.promised_date = date.today() + timedelta(days=10)
    o.status = OrderStatus.EN_PROCESO
    datos.commit()

    assert o.esta_retrasada() is False  # el proveedor comprometio una fecha futura


def test_el_status_retrasada_manda_aunque_las_fechas_esten_al_dia(datos):
    o = orden(datos, "OC-1003")
    o.status = OrderStatus.RETRASADA
    datos.commit()
    assert o.esta_retrasada() is True


def test_una_orden_entregada_nunca_aparece_retrasada(datos):
    o = orden(datos, "OC-1001")
    o.status = OrderStatus.ENTREGADA
    datos.commit()
    assert o.esta_retrasada() is False


def test_una_orden_cerrada_no_genera_alertas(datos):
    o = orden(datos, "OC-1001")
    o.is_closed = True
    datos.commit()
    assert o.esta_retrasada() is False
    assert o.sin_respuesta is False


# --------------------------------------------------------------------------- #
# KPIs y filtros
# --------------------------------------------------------------------------- #


def test_kpis_de_arranque(datos):
    k = dashboard.compute_kpis(datos)
    assert k.abiertas == 4
    assert k.sin_respuesta == 4
    assert k.con_status == 0
    assert k.porcentaje_respuesta == 0.0
    assert k.retrasadas == 1


def test_el_porcentaje_de_respuesta_sube_cuando_contestan(datos):
    o = orden(datos, "OC-1003")
    o.status = OrderStatus.RECIBIDA
    datos.commit()

    k = dashboard.compute_kpis(datos)
    assert k.con_status == 1
    assert k.porcentaje_respuesta == 25.0


def test_filtro_por_proveedor(datos):
    proveedor = datos.scalar(select(Supplier).where(Supplier.code == "PROV-B"))
    ordenes = dashboard.query_orders(datos, dashboard.OrderFilters(supplier_id=proveedor.id))
    assert [o.po_number for o in ordenes] == ["OC-2001"]


def test_filtro_por_alerta_de_retraso(datos):
    ordenes = dashboard.query_orders(datos, dashboard.OrderFilters(alerta="retrasadas"))
    assert [o.po_number for o in ordenes] == ["OC-1001"]


def test_filtro_por_vencer_respeta_la_ventana_configurada(datos):
    ordenes = dashboard.query_orders(datos, dashboard.OrderFilters(alerta="por_vencer"))
    assert [o.po_number for o in ordenes] == ["OC-1002"]  # 3 dias, dentro de la ventana de 7


def test_busqueda_por_orden_y_por_parte(datos):
    assert len(dashboard.query_orders(datos, dashboard.OrderFilters(search="OC-1002"))) == 1
    assert len(dashboard.query_orders(datos, dashboard.OrderFilters(search="PN-4471"))) == 4


def test_las_cerradas_se_excluyen_salvo_que_se_pidan(datos):
    o = orden(datos, "OC-1001")
    o.is_closed = True
    datos.commit()

    assert len(dashboard.query_orders(datos, dashboard.OrderFilters())) == 3
    assert len(dashboard.query_orders(datos, dashboard.OrderFilters(include_closed=True))) == 4


def test_resumen_por_proveedor(datos):
    resumen = {r.supplier.code: r for r in dashboard.supplier_summaries(datos)}
    assert resumen["PROV-A"].abiertas == 3
    assert resumen["PROV-A"].retrasadas == 1
    assert resumen["PROV-B"].abiertas == 1


# --------------------------------------------------------------------------- #
# Pantallas
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("ruta", ["/", "/cargar", "/proveedores", "/historial", "/salud"])
def test_las_pantallas_responden(client, datos, ruta):
    assert client.get(ruta).status_code == 200


def test_el_tablero_muestra_las_ordenes(client, datos):
    html = client.get("/").text
    assert "OC-1001" in html
    assert "Sin respuesta" in html


def test_el_tablero_filtra_por_querystring(client, datos):
    html = client.get("/?alerta=retrasadas").text
    assert "OC-1001" in html
    assert "OC-1003" not in html


def test_detalle_de_orden(client, datos):
    o = orden(datos, "OC-1001")
    html = client.get(f"/ordenes/{o.id}").text
    assert "OC-1001" in html
    assert "Historial de cambios" in html


def test_detalle_de_orden_inexistente(client, datos):
    assert client.get("/ordenes/99999").status_code == 404


def test_exportar_csv(client, datos):
    respuesta = client.get("/export.csv")
    assert respuesta.status_code == 200
    assert "text/csv" in respuesta.headers["content-type"]
    lineas = respuesta.text.strip().splitlines()
    assert lineas[0].startswith("Orden,Linea,Proveedor")
    assert len(lineas) == 5  # encabezado + 4 ordenes


def test_alta_de_proveedor_desde_el_portal(client, datos):
    respuesta = client.post(
        "/proveedores",
        data={"codigo": "PROV-C", "nombre": "Filtros Tijuana", "correo": "",
              "lista_sharepoint": "Ordenes - PROV-C"},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert datos.scalar(select(Supplier).where(Supplier.code == "PROV-C")) is not None


def test_no_se_permiten_dos_proveedores_con_el_mismo_codigo(client, datos):
    respuesta = client.post(
        "/proveedores", data={"codigo": "PROV-A", "nombre": "Duplicado"}, follow_redirects=False
    )
    assert respuesta.status_code == 409


def test_baja_de_proveedor(client, datos):
    proveedor = datos.scalar(select(Supplier).where(Supplier.code == "PROV-B"))
    client.post(
        f"/proveedores/{proveedor.id}",
        data={"nombre": proveedor.name, "correo": "", "lista_sharepoint": ""},
        follow_redirects=False,
    )
    datos.refresh(proveedor)
    assert proveedor.active is False  # el checkbox no viaja cuando esta desmarcado


def test_carga_desde_la_pantalla_del_portal(client, datos):
    contenido = construir_excel([fila(orden="OC-3001", codigo="PROV-A")])
    respuesta = client.post(
        "/cargar",
        files={"archivo": ("reporte.xlsx", contenido,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"cargado_por": "ana@tmmbc", "alcance_cierre": "none", "alta_automatica": "true"},
    )
    assert respuesta.status_code == 200
    assert "procesada" in respuesta.text
    assert orden(datos, "OC-3001") is not None


def test_la_carga_rechaza_un_archivo_que_no_es_excel(client, datos):
    respuesta = client.post(
        "/cargar",
        files={"archivo": ("notas.txt", b"hola", "text/plain")},
        data={"alcance_cierre": "none"},
    )
    assert "Solo se aceptan archivos" in respuesta.text


def test_la_carga_reporta_un_excel_corrupto_sin_reventar(client, datos):
    respuesta = client.post(
        "/cargar",
        files={"archivo": ("reporte.xlsx", b"archivo corrupto", "application/vnd.ms-excel")},
        data={"alcance_cierre": "none"},
    )
    assert respuesta.status_code == 200
    assert "No se pudo leer" in respuesta.text


def test_el_codigo_duplicado_muestra_el_mensaje_en_la_pantalla(client, datos):
    respuesta = client.post(
        "/proveedores", data={"codigo": "PROV-A", "nombre": "Duplicado"}, follow_redirects=False
    )
    assert respuesta.status_code == 409
    assert "Ya existe un proveedor" in respuesta.text
    assert "Desempeno por proveedor" in respuesta.text  # sigue siendo la pantalla, no un JSON


def test_un_alcance_de_cierre_invalido_no_revienta(client, datos):
    """Un POST manipulado cae al alcance seguro en vez de dar 500."""
    respuesta = client.post(
        "/cargar",
        files={"archivo": ("reporte.xlsx", construir_excel([fila(orden="OC-1001", codigo="PROV-A")]),
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"alcance_cierre": "borra-todo"},
    )
    assert respuesta.status_code == 200
    assert "procesada" in respuesta.text
