"""Pruebas de la sincronizacion directa con SharePoint.

Usan un SharePoint falso montado sobre el transporte de httpx: guarda listas y
elementos en memoria y responde como Graph. Asi se prueba el comportamiento real
del servicio (orden de las operaciones, que se escribe y que no) sin un tenant.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.models import OrderStatus, PurchaseOrder, Supplier
from app.services import sharepoint
from app.services.excel_import import import_orders
from tests.conftest import construir_excel, fila

SITIO = "https://tmmbc.sharepoint.com/sites/Proveedores"
SITE_ID = "tmmbc.sharepoint.com,sitio-guid,web-guid"


class SharePointFalso:
    """SharePoint en memoria, con la parte de Graph que usa el portal."""

    def __init__(self):
        self.listas: dict[str, dict] = {}      # id -> {displayName, columns}
        self.elementos: dict[str, dict] = {}   # id -> {list_id, fields, lastModifiedBy...}
        self.siguiente_id = 1
        self.llamadas: list[tuple[str, str]] = []
        # Para simular que alguien mas edito un elemento.
        self.editor = {"user": {"email": "invitado@prov-a.mx", "displayName": "Invitado A"}}

    # --- utilidades para las pruebas -------------------------------------- #

    def crear_lista(self, nombre: str) -> str:
        identificador = f"lista-{self.siguiente_id}"
        self.siguiente_id += 1
        self.listas[identificador] = {"id": identificador, "displayName": nombre, "columns": []}
        return identificador

    def items_de(self, nombre_lista: str) -> list[dict]:
        lista = next((l for l in self.listas.values() if l["displayName"] == nombre_lista), None)
        if lista is None:
            return []
        return [e for e in self.elementos.values() if e["list_id"] == lista["id"]]

    def campos_de(self, nombre_lista: str, clave: str) -> dict | None:
        for elemento in self.items_de(nombre_lista):
            if elemento["fields"].get("Title") == clave:
                return elemento["fields"]
        return None

    def capturar_respuesta(self, nombre_lista: str, clave: str, **campos) -> None:
        """Simula que el proveedor capturo algo en su lista."""
        for elemento in self.items_de(nombre_lista):
            if elemento["fields"].get("Title") == clave:
                elemento["fields"].update(campos)
                elemento["lastModifiedBy"] = self.editor
                elemento["lastModifiedDateTime"] = "2026-09-15T18:30:00Z"
                return
        raise AssertionError(f"No existe el elemento {clave} en {nombre_lista}")

    # --- transporte -------------------------------------------------------- #

    def handler(self, request: httpx.Request) -> httpx.Response:
        ruta = request.url.path.replace("/v1.0", "")
        metodo = request.method
        self.llamadas.append((metodo, ruta))
        cuerpo = json.loads(request.content) if request.content else {}

        if request.headers.get("Authorization") != "Bearer token-de-prueba":
            return httpx.Response(401, json={"error": {"message": "token invalido"}})

        # Resolver el sitio
        if ruta.startswith("/sites/") and ":" in ruta:
            return httpx.Response(200, json={"id": SITE_ID})

        base = f"/sites/{SITE_ID}"

        if ruta == f"{base}/lists" and metodo == "GET":
            return httpx.Response(200, json={"value": list(self.listas.values())})

        if ruta == f"{base}/lists" and metodo == "POST":
            identificador = self.crear_lista(cuerpo["displayName"])
            self.listas[identificador]["columns"] = cuerpo.get("columns", [])
            return httpx.Response(201, json=self.listas[identificador])

        partes = ruta[len(f"{base}/lists/"):].split("/") if ruta.startswith(f"{base}/lists/") else []

        if len(partes) == 2 and partes[1] == "columns":
            lista = self.listas.get(partes[0])
            if metodo == "GET":
                return httpx.Response(200, json={"value": lista["columns"]})
            lista["columns"].append(cuerpo)
            return httpx.Response(201, json=cuerpo)

        if len(partes) == 2 and partes[1] == "items":
            if metodo == "GET":
                elementos = [e for e in self.elementos.values() if e["list_id"] == partes[0]]
                return httpx.Response(200, json={"value": elementos})
            identificador = f"item-{self.siguiente_id}"
            self.siguiente_id += 1
            self.elementos[identificador] = {
                "id": identificador,
                "list_id": partes[0],
                "fields": dict(cuerpo["fields"]),
                "lastModifiedBy": {"user": {"email": "portal@tmmbc"}},
                "lastModifiedDateTime": "2026-09-16T00:00:00Z",
            }
            return httpx.Response(201, json=self.elementos[identificador])

        if len(partes) == 3 and partes[1] == "items" and partes[2] in self.elementos:
            if metodo == "DELETE":
                del self.elementos[partes[2]]
                return httpx.Response(204)

        if len(partes) == 4 and partes[3] == "fields" and metodo == "PATCH":
            self.elementos[partes[2]]["fields"].update(cuerpo)
            return httpx.Response(200, json=cuerpo)

        if len(partes) == 1 and metodo == "DELETE":
            for clave in [k for k, v in self.elementos.items() if v["list_id"] == partes[0]]:
                del self.elementos[clave]
            del self.listas[partes[0]]
            return httpx.Response(204)

        return httpx.Response(404, json={"error": {"message": f"ruta no simulada: {metodo} {ruta}"}})


@pytest.fixture
def sp():
    return SharePointFalso()


@pytest.fixture
def cliente_graph(sp):
    http = httpx.Client(transport=httpx.MockTransport(sp.handler))
    with sharepoint.GraphClient("token-de-prueba", http=http) as client:
        yield client
    http.close()


@pytest.fixture
def datos(db):
    """Dos proveedores con lista asignada y ordenes cargadas."""
    import_orders(
        db,
        construir_excel([
            fila(orden="OC-1001", linea=1, codigo="PROV-A", cantidad=10,
                 requerida=date.today() + timedelta(days=20)),
            fila(orden="OC-1001", linea=2, codigo="PROV-A", cantidad=5),
            fila(orden="OC-2001", linea=1, codigo="PROV-B", nombre="Refacciones BC"),
        ]),
        "reporte.xlsx",
    )
    for proveedor in db.scalars(select(Supplier)).all():
        proveedor.sharepoint_list = f"Ordenes - {proveedor.code}"
    db.commit()
    return db


def sincronizar(db, cliente_graph):
    return sharepoint.SharePointSync(db, cliente_graph, SITIO).sincronizar_todo()


# --------------------------------------------------------------------------- #
# Creacion de listas y publicacion
# --------------------------------------------------------------------------- #


def test_la_primera_sincronizacion_crea_una_lista_por_proveedor(datos, cliente_graph, sp):
    resultado = sincronizar(datos, cliente_graph)

    assert resultado.ok
    nombres = {l["displayName"] for l in sp.listas.values()}
    assert nombres == {"Ordenes - PROV-A", "Ordenes - PROV-B"}
    assert all(p.lista_creada for p in resultado.proveedores)


def test_cada_proveedor_recibe_solo_sus_ordenes(datos, cliente_graph, sp):
    """La regla central del esquema, ahora del lado de SharePoint."""
    sincronizar(datos, cliente_graph)

    claves_a = {e["fields"]["Title"] for e in sp.items_de("Ordenes - PROV-A")}
    claves_b = {e["fields"]["Title"] for e in sp.items_de("Ordenes - PROV-B")}

    assert claves_a == {"OC-1001-1", "OC-1001-2"}
    assert claves_b == {"OC-2001-1"}


def test_el_precio_no_se_publica_si_no_esta_autorizado(datos, cliente_graph, sp):
    sincronizar(datos, cliente_graph)
    campos = sp.campos_de("Ordenes - PROV-A", "OC-1001-1")
    assert "Precio" not in campos
    assert "Moneda" not in campos


def test_el_precio_se_publica_cuando_esta_autorizado(datos, cliente_graph, sp):
    proveedor = datos.scalar(select(Supplier).where(Supplier.code == "PROV-A"))
    proveedor.share_price = True
    datos.commit()

    sincronizar(datos, cliente_graph)

    campos = sp.campos_de("Ordenes - PROV-A", "OC-1001-1")
    assert campos["Precio"] == pytest.approx(845.5)
    assert campos["Moneda"] == "MXN"
    # Y el otro proveedor sigue sin precio.
    assert "Precio" not in sp.campos_de("Ordenes - PROV-B", "OC-2001-1")


def test_la_columna_status_solo_ofrece_el_catalogo_del_proveedor(datos, cliente_graph, sp):
    sincronizar(datos, cliente_graph)
    lista = next(l for l in sp.listas.values() if l["displayName"] == "Ordenes - PROV-A")
    status = next(c for c in lista["columns"] if c["name"] == "Status")

    assert "Pendiente" not in status["choice"]["choices"]
    assert "Retrasada" in status["choice"]["choices"]
    assert status["choice"]["allowTextEntry"] is False


def test_una_segunda_sincronizacion_no_duplica_nada(datos, cliente_graph, sp):
    sincronizar(datos, cliente_graph)
    resultado = sincronizar(datos, cliente_graph)

    assert len(sp.listas) == 2
    assert len(sp.items_de("Ordenes - PROV-A")) == 2
    assert all(not p.lista_creada for p in resultado.proveedores)
    assert resultado.total_publicados == 0  # nada cambio, nada que escribir


# --------------------------------------------------------------------------- #
# Bajada: lo que captura el proveedor
# --------------------------------------------------------------------------- #


def test_baja_el_status_que_selecciono_el_proveedor(datos, cliente_graph, sp):
    sincronizar(datos, cliente_graph)
    sp.capturar_respuesta("Ordenes - PROV-A", "OC-1001-1", Status="En proceso")

    resultado = sincronizar(datos, cliente_graph)

    orden = datos.scalar(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-1001",
                                    PurchaseOrder.line_number == 1)
    )
    datos.refresh(orden)
    assert orden.status is OrderStatus.EN_PROCESO
    assert orden.last_supplier_update_by == "invitado@prov-a.mx"
    assert resultado.total_bajados == 1


def test_la_lista_del_proveedor_tiene_una_sola_columna_editable(datos, cliente_graph, sp):
    """Lo unico que el proveedor captura es el Status."""
    sincronizar(datos, cliente_graph)
    lista = next(l for l in sp.listas.values() if l["displayName"] == "Ordenes - PROV-A")
    nombres = [c["name"] for c in lista["columns"]]

    assert "Status" in nombres
    assert "FechaPromesa" not in nombres
    assert "Comentario" not in nombres


def test_la_fecha_promesa_y_la_nota_interna_nunca_se_publican(datos, cliente_graph, sp):
    """Son del equipo de mantenimiento: no salen hacia la lista del proveedor."""
    from app.services.seguimiento import actualizar_seguimiento

    orden = datos.scalar(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-1001",
                                    PurchaseOrder.line_number == 1)
    )
    actualizar_seguimiento(
        datos, orden, promised_date=date(2026, 11, 5),
        internal_note="Hablo el proveedor por telefono", actor="ana@tmmbc",
    )
    datos.commit()

    sincronizar(datos, cliente_graph)

    campos = sp.campos_de("Ordenes - PROV-A", "OC-1001-1")
    assert "FechaPromesa" not in campos
    assert "Comentario" not in campos
    assert "Hablo el proveedor" not in json.dumps(campos)


def test_si_el_proveedor_agrega_una_columna_a_mano_se_ignora(datos, cliente_graph, sp):
    """Un campo extra en la lista no entra a la base de datos."""
    sincronizar(datos, cliente_graph)
    sp.capturar_respuesta(
        "Ordenes - PROV-A", "OC-1001-1",
        Status="Recibida", Comentario="esto no deberia entrar",
        FechaPromesa="2030-01-01T00:00:00Z",
    )

    sincronizar(datos, cliente_graph)

    orden = datos.scalar(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-1001",
                                    PurchaseOrder.line_number == 1)
    )
    datos.refresh(orden)
    assert orden.status is OrderStatus.RECIBIDA   # el status si entro
    assert orden.promised_date is None            # lo demas no
    assert orden.internal_note is None


def test_un_status_fuera_del_catalogo_se_rechaza_sin_detener_al_resto(datos, cliente_graph, sp):
    sincronizar(datos, cliente_graph)
    sp.capturar_respuesta("Ordenes - PROV-A", "OC-1001-1", Status="ya casi la mando")
    sp.capturar_respuesta("Ordenes - PROV-A", "OC-1001-2", Status="Enviada")

    resultado = sincronizar(datos, cliente_graph)

    proveedor_a = next(p for p in resultado.proveedores if p.codigo == "PROV-A")
    assert proveedor_a.rechazados == 1
    assert proveedor_a.bajados == 1
    assert any("no esta en el catalogo" in e for e in proveedor_a.errores)


def test_la_subida_no_pisa_la_respuesta_del_proveedor(datos, cliente_graph, sp):
    """El orden importa: primero baja, luego sube. Si no, se perderia la captura."""
    sincronizar(datos, cliente_graph)
    sp.capturar_respuesta("Ordenes - PROV-A", "OC-1001-1", Status="Enviada")

    sincronizar(datos, cliente_graph)

    assert sp.campos_de("Ordenes - PROV-A", "OC-1001-1")["Status"] == "Enviada"


# --------------------------------------------------------------------------- #
# Restauracion de los campos del sistema
# --------------------------------------------------------------------------- #


def test_si_el_proveedor_edita_un_campo_del_sistema_se_restaura(datos, cliente_graph, sp):
    sincronizar(datos, cliente_graph)
    sp.capturar_respuesta("Ordenes - PROV-A", "OC-1001-1", Cantidad=9999, Parte="HACK-1")

    resultado = sincronizar(datos, cliente_graph)

    campos = sp.campos_de("Ordenes - PROV-A", "OC-1001-1")
    assert campos["Cantidad"] == pytest.approx(10.0)
    assert campos["Parte"] == "PN-4471"
    assert resultado.total_publicados == 1

    # Y la base de datos nunca se contamino.
    orden = datos.scalar(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-1001",
                                    PurchaseOrder.line_number == 1)
    )
    assert float(orden.quantity) == 10


def test_una_nueva_carga_de_excel_se_refleja_en_sharepoint(datos, cliente_graph, sp):
    sincronizar(datos, cliente_graph)

    import_orders(
        datos,
        construir_excel([fila(orden="OC-1001", linea=1, codigo="PROV-A", cantidad=77)]),
        "reporte2.xlsx",
        close_scope="none",
    )
    sincronizar(datos, cliente_graph)

    assert sp.campos_de("Ordenes - PROV-A", "OC-1001-1")["Cantidad"] == pytest.approx(77.0)


# --------------------------------------------------------------------------- #
# Cierre de ordenes
# --------------------------------------------------------------------------- #


def test_una_orden_cerrada_sale_de_la_lista_del_proveedor(datos, cliente_graph, sp):
    sincronizar(datos, cliente_graph)
    assert len(sp.items_de("Ordenes - PROV-A")) == 2

    orden = datos.scalar(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "OC-1001",
                                    PurchaseOrder.line_number == 2)
    )
    orden.is_closed = True
    datos.commit()

    resultado = sincronizar(datos, cliente_graph)

    claves = {e["fields"]["Title"] for e in sp.items_de("Ordenes - PROV-A")}
    assert claves == {"OC-1001-1"}
    assert resultado.total_eliminados == 1


def test_un_elemento_huerfano_se_retira(datos, cliente_graph, sp):
    """Una fila que el proveedor agrego a mano no corresponde a ninguna orden."""
    sincronizar(datos, cliente_graph)
    lista_id = next(l["id"] for l in sp.listas.values() if l["displayName"] == "Ordenes - PROV-A")
    sp.elementos["intruso"] = {
        "id": "intruso", "list_id": lista_id,
        "fields": {"Title": "OC-9999-1", "Orden": "OC-9999", "Linea": 1},
        "lastModifiedBy": sp.editor, "lastModifiedDateTime": "2026-09-15T10:00:00Z",
    }

    sincronizar(datos, cliente_graph)

    claves = {e["fields"]["Title"] for e in sp.items_de("Ordenes - PROV-A")}
    assert "OC-9999-1" not in claves


# --------------------------------------------------------------------------- #
# Errores y configuracion
# --------------------------------------------------------------------------- #


def test_un_proveedor_sin_lista_no_se_publica(datos, cliente_graph, sp):
    proveedor = datos.scalar(select(Supplier).where(Supplier.code == "PROV-B"))
    proveedor.sharepoint_list = None
    datos.commit()

    resultado = sincronizar(datos, cliente_graph)

    assert {l["displayName"] for l in sp.listas.values()} == {"Ordenes - PROV-A"}
    assert [p.codigo for p in resultado.proveedores] == ["PROV-A"]


def test_un_proveedor_dado_de_baja_no_se_publica(datos, cliente_graph, sp):
    proveedor = datos.scalar(select(Supplier).where(Supplier.code == "PROV-B"))
    proveedor.active = False
    datos.commit()

    sincronizar(datos, cliente_graph)
    assert {l["displayName"] for l in sp.listas.values()} == {"Ordenes - PROV-A"}


def test_sin_proveedores_con_lista_avisa_en_vez_de_fallar(datos, cliente_graph):
    for proveedor in datos.scalars(select(Supplier)).all():
        proveedor.sharepoint_list = None
    datos.commit()

    resultado = sincronizar(datos, cliente_graph)

    assert not resultado.ok
    assert "lista asignada" in resultado.errores[0]


def test_un_token_vencido_se_reporta_como_falta_de_sesion(datos, sp):
    http = httpx.Client(transport=httpx.MockTransport(sp.handler))
    with sharepoint.GraphClient("token-vencido", http=http) as client:
        with pytest.raises(sharepoint.SinSesion):
            sharepoint.SharePointSync(datos, client, SITIO).site_id
    http.close()


def test_sin_liga_del_sitio_el_error_es_claro(datos, cliente_graph):
    with pytest.raises(sharepoint.SharePointError, match="SP_SITE_URL"):
        sharepoint.SharePointSync(datos, cliente_graph, "")


def test_una_liga_mal_escrita_se_detecta(datos, cliente_graph):
    sync = sharepoint.SharePointSync(datos, cliente_graph, "no-es-una-liga")
    with pytest.raises(sharepoint.SharePointError, match="no parece una liga valida"):
        _ = sync.site_id


# --------------------------------------------------------------------------- #
# Conversiones
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "clave,esperado",
    [("OC-1001-3", ("OC-1001", 3)), ("1001-1", ("1001", 1)),
     ("OC-A-B-12", ("OC-A-B", 12)), ("sin-numero", None), ("OC1001", None)],
)
def test_partir_la_clave(clave, esperado):
    assert sharepoint._partir_clave(clave) == esperado


@pytest.mark.parametrize(
    "entrada,esperado",
    [("2026-10-20T00:00:00Z", date(2026, 10, 20)), ("2026-10-20", date(2026, 10, 20)),
     ("", None), (None, None), ("basura", None)],
)
def test_leer_fechas_de_sharepoint(entrada, esperado):
    assert sharepoint._a_fecha(entrada) == esperado
