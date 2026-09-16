"""Conexion directa del portal con SharePoint, via Microsoft Graph.

Sustituye por completo a Power Automate: el portal clasifica las ordenes por
proveedor, crea la lista de cada uno si no existe y las publica el mismo.

En la lista de cada proveedor hay UNA SOLA columna editable: Status. Todo lo
demas lo escribe el portal. La fecha promesa y la nota interna no se publican:
son del equipo de mantenimiento y nunca salen hacia SharePoint.

Una sincronizacion hace dos cosas, SIEMPRE en este orden:

1. BAJA el status que seleccionaron los proveedores.
2. SUBE los campos del sistema, que asi quedan restaurados si alguien los edito.

El orden importa: al reves, la subida borraria un status recien seleccionado.

Autenticacion: dos modos, y el que sirve depende de lo que permita el tenant.

* `delegado`  - inicias sesion una vez con TU cuenta (`scripts/conectar_sharepoint.py`)
                y el portal reusa el token. No necesita administrador si el
                tenant ya tiene consentida la app publica de Microsoft.
* `aplicacion` - un registro de aplicacion con secreto. Necesita que alguien con
                 permisos de administrador lo consienta una sola vez.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import msal
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import OrderStatus, PurchaseOrder, Supplier, utcnow
from app.services import sync

GRAPH = "https://graph.microsoft.com/v1.0"

# App publica de Microsoft ("Microsoft Graph Command Line Tools"). Muchos tenants
# ya la tienen consentida, que es justo lo que permite arrancar sin administrador.
CLIENT_ID_POR_OMISION = "14d82eec-204b-4c2f-b7e8-296a70dab67e"

ALCANCES_DELEGADOS = ["Sites.ReadWrite.All"]
ALCANCE_APLICACION = ["https://graph.microsoft.com/.default"]

# Clave con la que se localiza cada linea de orden dentro de la lista. Se guarda en
# la columna Title, que SharePoint crea sola y exige en toda lista.
CAMPO_CLAVE = "Title"

COLUMNAS_SISTEMA = [
    {"name": "Orden", "text": {}},
    {"name": "Linea", "number": {"decimalPlaces": "none"}},
    {"name": "Parte", "text": {}},
    {"name": "Descripcion", "text": {"allowMultipleLines": True}},
    {"name": "Cantidad", "number": {}},
    {"name": "Unidad", "text": {}},
    {"name": "FechaRequerida", "dateTime": {"format": "dateOnly"}},
]

COLUMNAS_PRECIO = [
    {"name": "Precio", "number": {}},
    {"name": "Moneda", "text": {}},
]

# Lo unico que el proveedor captura. Una columna, un clic.
COLUMNA_STATUS = {
    "name": "Status",
    "choice": {
        "choices": [],  # se llena con el catalogo al crear la lista
        "displayAs": "dropDownMenu",
        "allowTextEntry": False,
    },
}


class SharePointError(Exception):
    """Falla al hablar con SharePoint."""


class SinSesion(SharePointError):
    """No hay token utilizable: hay que iniciar sesion."""


# --------------------------------------------------------------------------- #
# Autenticacion
# --------------------------------------------------------------------------- #


def _cache_de_token(settings: Settings) -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    ruta = Path(settings.sp_token_cache)
    if ruta.exists():
        cache.deserialize(ruta.read_text())
    return cache


def _guardar_cache(settings: Settings, cache: msal.SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return
    ruta = Path(settings.sp_token_cache)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(cache.serialize())
    # El token da acceso al SharePoint: que no lo lea nadie mas de la maquina.
    try:
        os.chmod(ruta, 0o600)
    except OSError:
        pass  # en Windows no aplica


def _autoridad(settings: Settings) -> str:
    return f"https://login.microsoftonline.com/{settings.sp_tenant_id or 'organizations'}"


def _app_publica(settings: Settings, cache) -> msal.PublicClientApplication:
    return msal.PublicClientApplication(
        settings.sp_client_id or CLIENT_ID_POR_OMISION,
        authority=_autoridad(settings),
        token_cache=cache,
    )


def _app_confidencial(settings: Settings) -> msal.ConfidentialClientApplication:
    if not settings.sp_client_id or not settings.sp_client_secret:
        raise SharePointError(
            "El modo 'aplicacion' necesita SP_CLIENT_ID y SP_CLIENT_SECRET en el .env."
        )
    return msal.ConfidentialClientApplication(
        settings.sp_client_id,
        client_credential=settings.sp_client_secret,
        authority=_autoridad(settings),
    )


def obtener_token(settings: Settings | None = None) -> str:
    """Devuelve un token sin intervencion del usuario, o falla pidiendo iniciar sesion."""
    settings = settings or get_settings()

    if settings.sp_auth_mode == "aplicacion":
        resultado = _app_confidencial(settings).acquire_token_for_client(ALCANCE_APLICACION)
        if "access_token" not in resultado:
            raise SharePointError(_mensaje_de_error(resultado))
        return resultado["access_token"]

    cache = _cache_de_token(settings)
    app = _app_publica(settings, cache)
    cuentas = app.get_accounts()
    if not cuentas:
        raise SinSesion(
            "No hay sesion guardada. Corre: python scripts/conectar_sharepoint.py"
        )
    resultado = app.acquire_token_silent(ALCANCES_DELEGADOS, account=cuentas[0])
    _guardar_cache(settings, cache)
    if not resultado or "access_token" not in resultado:
        raise SinSesion(
            "La sesion guardada vencio. Corre otra vez: python scripts/conectar_sharepoint.py"
        )
    return resultado["access_token"]


def sesion_activa(settings: Settings | None = None) -> str | None:
    """Con que cuenta esta conectado el portal, o None si no hay sesion."""
    settings = settings or get_settings()
    if settings.sp_auth_mode == "aplicacion":
        return "Registro de aplicacion" if settings.sp_client_secret else None
    cuentas = _app_publica(settings, _cache_de_token(settings)).get_accounts()
    return cuentas[0].get("username") if cuentas else None


def iniciar_sesion_por_codigo(settings: Settings | None = None, al_mostrar_codigo=print) -> str:
    """Inicio de sesion interactivo por codigo de dispositivo. Bloquea hasta terminar."""
    settings = settings or get_settings()
    cache = _cache_de_token(settings)
    app = _app_publica(settings, cache)

    flujo = app.initiate_device_flow(scopes=ALCANCES_DELEGADOS)
    if "user_code" not in flujo:
        raise SharePointError(_mensaje_de_error(flujo))

    al_mostrar_codigo(flujo["message"])
    resultado = app.acquire_token_by_device_flow(flujo)
    _guardar_cache(settings, cache)

    if "access_token" not in resultado:
        raise SharePointError(_mensaje_de_error(resultado))
    return resultado.get("id_token_claims", {}).get("preferred_username", "cuenta conectada")


def cerrar_sesion(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    ruta = Path(settings.sp_token_cache)
    if ruta.exists():
        ruta.unlink()


def _mensaje_de_error(resultado: dict) -> str:
    error = resultado.get("error", "error_desconocido")
    detalle = resultado.get("error_description", "")
    if "AADSTS65001" in detalle or error == "consent_required":
        return (
            "El tenant no tiene consentida la aplicacion. Revisa el diagnostico: "
            "python scripts/diagnostico_sharepoint.py"
        )
    if "AADSTS700016" in detalle or "AADSTS900023" in detalle:
        return f"El tenant o el client id no son validos. Revisa SP_TENANT_ID. ({detalle[:200]})"
    return f"{error}: {detalle[:400]}"


# --------------------------------------------------------------------------- #
# Cliente de Graph
# --------------------------------------------------------------------------- #


class GraphClient:
    """Envoltura minima sobre Graph, con el manejo de errores en un solo lugar."""

    def __init__(self, token: str, http: httpx.Client | None = None):
        self._propio = http is None
        self._http = http or httpx.Client(timeout=60)
        self._token = token

    def __enter__(self) -> "GraphClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        if self._propio:
            self._http.close()

    def _pedir(self, metodo: str, ruta: str, **kwargs) -> dict:
        url = ruta if ruta.startswith("http") else f"{GRAPH}{ruta}"
        cabeceras = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        cabeceras.update(kwargs.pop("headers", {}))
        respuesta = self._http.request(metodo, url, headers=cabeceras, **kwargs)

        if respuesta.status_code == 401:
            raise SinSesion("SharePoint rechazo el token. Vuelve a iniciar sesion.")
        if respuesta.status_code == 403:
            raise SharePointError(
                "Tu cuenta no tiene permiso para escribir en este sitio de SharePoint. "
                "Necesitas ser propietario o miembro con permiso de edicion."
            )
        if respuesta.status_code == 404:
            raise SharePointError(f"No se encontro en SharePoint: {url}")
        if respuesta.status_code >= 400:
            raise SharePointError(_detalle_http(respuesta))
        if respuesta.status_code == 204 or not respuesta.content:
            return {}
        return respuesta.json()

    def get(self, ruta: str, **kwargs) -> dict:
        return self._pedir("GET", ruta, **kwargs)

    def get_todo(self, ruta: str, **kwargs) -> list[dict]:
        """GET siguiendo la paginacion de Graph hasta el final."""
        elementos: list[dict] = []
        siguiente: str | None = ruta
        while siguiente:
            pagina = self._pedir("GET", siguiente, **kwargs)
            elementos.extend(pagina.get("value", []))
            siguiente = pagina.get("@odata.nextLink")
            kwargs.pop("params", None)  # el nextLink ya los trae
        return elementos

    def post(self, ruta: str, datos: dict) -> dict:
        return self._pedir("POST", ruta, json=datos)

    def patch(self, ruta: str, datos: dict) -> dict:
        return self._pedir("PATCH", ruta, json=datos)

    def delete(self, ruta: str) -> dict:
        return self._pedir("DELETE", ruta)


def _detalle_http(respuesta: httpx.Response) -> str:
    try:
        error = respuesta.json().get("error", {})
        return f"SharePoint respondio {respuesta.status_code}: {error.get('message', '')[:400]}"
    except (json.JSONDecodeError, ValueError):
        return f"SharePoint respondio {respuesta.status_code}: {respuesta.text[:300]}"


# --------------------------------------------------------------------------- #
# Resultado de una sincronizacion
# --------------------------------------------------------------------------- #


@dataclass
class ResultadoProveedor:
    codigo: str
    nombre: str
    lista: str
    lista_creada: bool = False
    bajados: int = 0          # cambios del proveedor traidos al portal
    rechazados: int = 0       # cambios del proveedor que no se pudieron aplicar
    creados: int = 0          # elementos nuevos en la lista
    actualizados: int = 0     # elementos con campos del sistema restaurados
    eliminados: int = 0       # ordenes cerradas retiradas de la lista
    errores: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errores


@dataclass
class ResultadoSync:
    proveedores: list[ResultadoProveedor] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    momento: datetime = field(default_factory=utcnow)

    @property
    def ok(self) -> bool:
        return not self.errores and all(p.ok for p in self.proveedores)

    def _suma(self, campo: str) -> int:
        return sum(getattr(p, campo) for p in self.proveedores)

    @property
    def total_bajados(self) -> int:
        return self._suma("bajados")

    @property
    def total_publicados(self) -> int:
        return self._suma("creados") + self._suma("actualizados")

    @property
    def total_eliminados(self) -> int:
        return self._suma("eliminados")

    @property
    def total_rechazados(self) -> int:
        return self._suma("rechazados")


# --------------------------------------------------------------------------- #
# Sincronizacion
# --------------------------------------------------------------------------- #


class SharePointSync:
    def __init__(self, db: Session, client: GraphClient, site_url: str):
        if not site_url:
            raise SharePointError("Falta SP_SITE_URL en el .env (la liga de tu sitio).")
        self.db = db
        self.client = client
        self.site_url = site_url.rstrip("/")
        self._site_id: str | None = None

    # --- sitio y listas ---------------------------------------------------- #

    @property
    def site_id(self) -> str:
        if self._site_id is None:
            partes = urlparse(self.site_url)
            if not partes.hostname:
                raise SharePointError(
                    f"SP_SITE_URL no parece una liga valida: {self.site_url}"
                )
            ruta = partes.path.rstrip("/")
            destino = f"/sites/{partes.hostname}:{ruta}" if ruta else f"/sites/{partes.hostname}"
            self._site_id = self.client.get(destino)["id"]
        return self._site_id

    def _buscar_lista(self, nombre: str) -> dict | None:
        listas = self.client.get_todo(f"/sites/{self.site_id}/lists", params={"$top": "200"})
        for lista in listas:
            if lista.get("displayName") == nombre:
                return lista
        return None

    def _columnas_de(self, supplier: Supplier) -> list[dict]:
        """Las columnas de la lista: todas del sistema, mas Status."""
        columnas = list(COLUMNAS_SISTEMA)
        if supplier.share_price:
            columnas += COLUMNAS_PRECIO
        status = json.loads(json.dumps(COLUMNA_STATUS))  # copia, para no mutar la constante
        status["choice"]["choices"] = [s.value for s in OrderStatus.editable_por_proveedor()]
        return columnas + [status]

    def asegurar_lista(self, supplier: Supplier, resultado: ResultadoProveedor) -> str:
        """Devuelve el id de la lista del proveedor, creandola si hace falta."""
        nombre = supplier.sharepoint_list
        lista = self._buscar_lista(nombre)

        if lista is None:
            lista = self.client.post(
                f"/sites/{self.site_id}/lists",
                {
                    "displayName": nombre,
                    "description": f"Ordenes abiertas de {supplier.name}. "
                                   "Lo unico que hay que capturar es el Status.",
                    "columns": self._columnas_de(supplier),
                    "list": {"template": "genericList"},
                },
            )
            resultado.lista_creada = True
            return lista["id"]

        # La lista ya existia: agregar solo las columnas que falten.
        existentes = {
            c.get("name") for c in self.client.get_todo(f"/sites/{self.site_id}/lists/{lista['id']}/columns")
        }
        for columna in self._columnas_de(supplier):
            if columna["name"] not in existentes:
                self.client.post(f"/sites/{self.site_id}/lists/{lista['id']}/columns", columna)
        return lista["id"]

    def _elementos(self, lista_id: str) -> dict[str, dict]:
        """Los elementos de la lista, indexados por su clave."""
        elementos = self.client.get_todo(
            f"/sites/{self.site_id}/lists/{lista_id}/items",
            params={"expand": "fields", "$top": "500"},
        )
        por_clave: dict[str, dict] = {}
        for elemento in elementos:
            clave = (elemento.get("fields") or {}).get(CAMPO_CLAVE)
            if clave:
                por_clave[clave] = elemento
        return por_clave

    # --- paso 1: bajar lo que capturo el proveedor ------------------------- #

    def bajar(self, supplier: Supplier, elementos: dict[str, dict],
              resultado: ResultadoProveedor) -> None:
        """Trae el status que selecciono el proveedor. Es lo unico que se baja."""
        for clave, elemento in elementos.items():
            campos = elemento.get("fields") or {}
            referencia = _partir_clave(clave)
            if referencia is None:
                continue
            po_number, line_number = referencia

            autor = (
                (elemento.get("lastModifiedBy") or {}).get("user", {}).get("email")
                or (elemento.get("lastModifiedBy") or {}).get("user", {}).get("displayName")
                or supplier.name
            )
            try:
                salida = sync.apply_supplier_update(
                    self.db,
                    supplier=supplier,
                    po_number=po_number,
                    line_number=line_number,
                    status=campos.get("Status") or None,
                    changed_by=autor,
                    changed_at=_a_momento(elemento.get("lastModifiedDateTime")),
                )
            except sync.SyncError as exc:
                # Una fila rara no debe detener al resto del proveedor.
                resultado.rechazados += 1
                resultado.errores.append(f"{clave}: {exc}")
                continue
            if salida.applied:
                resultado.bajados += 1
        self.db.commit()

    # --- paso 2: subir los campos del sistema ------------------------------ #

    def subir(self, supplier: Supplier, lista_id: str, elementos: dict[str, dict],
              resultado: ResultadoProveedor) -> None:
        ordenes = self.db.scalars(
            select(PurchaseOrder)
            .where(PurchaseOrder.supplier_id == supplier.id)
            .order_by(PurchaseOrder.po_number, PurchaseOrder.line_number)
        ).all()

        vigentes: set[str] = set()

        for orden in ordenes:
            clave = f"{orden.po_number}-{orden.line_number}"
            elemento = elementos.get(clave)

            if orden.is_closed:
                # La orden se cerro en compras: sale de la lista del proveedor.
                if elemento:
                    self.client.delete(
                        f"/sites/{self.site_id}/lists/{lista_id}/items/{elemento['id']}"
                    )
                    resultado.eliminados += 1
                continue

            vigentes.add(clave)
            campos = self._campos_del_sistema(orden, supplier)

            if elemento is None:
                # Al crear si se manda el status, para que la columna no nazca vacia.
                campos["Status"] = orden.status.value
                self.client.post(
                    f"/sites/{self.site_id}/lists/{lista_id}/items", {"fields": campos}
                )
                resultado.creados += 1
                continue

            # Al actualizar NO se toca Status: ahi vive lo que selecciono el
            # proveedor. Solo se reescriben los campos del sistema, que es lo que
            # restaura cualquier edicion indebida.
            if _hay_diferencia(elemento.get("fields") or {}, campos):
                self.client.patch(
                    f"/sites/{self.site_id}/lists/{lista_id}/items/{elemento['id']}/fields",
                    campos,
                )
                resultado.actualizados += 1

        # Elementos huerfanos: ya no corresponden a ninguna orden de este proveedor.
        for clave, elemento in elementos.items():
            if clave not in vigentes:
                self.client.delete(
                    f"/sites/{self.site_id}/lists/{lista_id}/items/{elemento['id']}"
                )
                resultado.eliminados += 1

    def _campos_del_sistema(self, orden: PurchaseOrder, supplier: Supplier) -> dict:
        campos = {
            CAMPO_CLAVE: f"{orden.po_number}-{orden.line_number}",
            "Orden": orden.po_number,
            "Linea": orden.line_number,
            "Parte": orden.part_number or "",
            "Descripcion": orden.description or "",
            "Cantidad": float(orden.quantity) if orden.quantity is not None else None,
            "Unidad": orden.unit or "",
            "FechaRequerida": _de_fecha(orden.required_date),
        }
        if supplier.share_price:
            campos["Precio"] = float(orden.unit_price) if orden.unit_price is not None else None
            campos["Moneda"] = orden.currency or ""
        return campos

    # --- orquestacion ------------------------------------------------------ #

    def sincronizar_proveedor(self, supplier: Supplier) -> ResultadoProveedor:
        resultado = ResultadoProveedor(
            codigo=supplier.code, nombre=supplier.name, lista=supplier.sharepoint_list or ""
        )
        try:
            lista_id = self.asegurar_lista(supplier, resultado)
            elementos = self._elementos(lista_id)
            # Bajar primero: si no, la subida pisaria una respuesta recien capturada.
            self.bajar(supplier, elementos, resultado)
            self.subir(supplier, lista_id, elementos, resultado)
        except SharePointError as exc:
            resultado.errores.append(str(exc))
        return resultado

    def sincronizar_todo(self) -> ResultadoSync:
        resultado = ResultadoSync()
        proveedores = self.db.scalars(
            select(Supplier).where(Supplier.active == True).order_by(Supplier.name)  # noqa: E712
        ).all()

        con_lista = [p for p in proveedores if p.sharepoint_list]
        if not con_lista:
            resultado.errores.append(
                "Ningun proveedor activo tiene lista asignada. Ve a Proveedores y "
                "capturales el nombre de su lista de SharePoint."
            )
            return resultado

        for proveedor in con_lista:
            resultado.proveedores.append(self.sincronizar_proveedor(proveedor))
        return resultado


# --------------------------------------------------------------------------- #
# Punto de entrada
# --------------------------------------------------------------------------- #


def sincronizar(db: Session, settings: Settings | None = None,
                http: httpx.Client | None = None) -> ResultadoSync:
    """Sincroniza todo el catalogo. Es lo que llama el boton del portal."""
    settings = settings or get_settings()
    token = obtener_token(settings)
    with GraphClient(token, http=http) as client:
        return SharePointSync(db, client, settings.sp_site_url).sincronizar_todo()


# --------------------------------------------------------------------------- #
# Conversiones
# --------------------------------------------------------------------------- #


def _partir_clave(clave: str) -> tuple[str, int] | None:
    """'OC-1001-3' -> ('OC-1001', 3). La orden puede traer guiones."""
    if "-" not in clave:
        return None
    cabeza, _, cola = clave.rpartition("-")
    if not cabeza or not cola.isdigit():
        return None
    return cabeza, int(cola)


def _de_fecha(valor: date | None) -> str | None:
    return f"{valor.isoformat()}T00:00:00Z" if valor else None


def _a_fecha(valor: object) -> date | None:
    if not valor:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(texto[:10])
        except ValueError:
            return None


def _a_momento(valor: object) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _hay_diferencia(actuales: dict, nuevos: dict) -> bool:
    """Evita escrituras inutiles en SharePoint (cada PATCH cuesta una llamada)."""
    for nombre, nuevo in nuevos.items():
        actual = actuales.get(nombre)
        if nombre.startswith("Fecha"):
            if _a_fecha(actual) != _a_fecha(nuevo):
                return True
            continue
        if isinstance(nuevo, float) or isinstance(actual, float):
            if (actual is None) != (nuevo is None):
                return True
            if actual is not None and abs(float(actual) - float(nuevo)) > 1e-9:
                return True
            continue
        if (actual or "") != (nuevo or ""):
            return True
    return False
