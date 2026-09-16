"""Averigua que te deja hacer tu tenant, sin tener que preguntarle a Sistemas.

    python scripts/diagnostico_sharepoint.py

Revisa, en orden: la configuracion, el inicio de sesion, el acceso al sitio,
el permiso para crear listas y el permiso para escribir en ellas. Se detiene en
el primer paso que falle y te dice exactamente que hacer.

No deja basura: la lista de prueba que crea, la borra al terminar.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services import sharepoint  # noqa: E402

LISTA_DE_PRUEBA = "ZZ Prueba portal - borrar"


def titulo(texto: str) -> None:
    print(f"\n{texto}\n{'-' * len(texto)}")


def bien(texto: str) -> None:
    print(f"  [ok]    {texto}")


def mal(texto: str) -> None:
    print(f"  [FALLA] {texto}")


def nota(texto: str) -> None:
    print(f"          {texto}")


def main() -> int:
    settings = get_settings()
    print("=" * 72)
    print(" Diagnostico de la conexion con SharePoint")
    print("=" * 72)

    # --- 1. Configuracion --------------------------------------------------- #
    titulo("1. Configuracion (.env)")
    if not settings.sp_site_url:
        mal("Falta SP_SITE_URL.")
        nota("Abre tu sitio en el navegador y copia la liga completa, por ejemplo:")
        nota("  SP_SITE_URL=https://tutenant.sharepoint.com/sites/Proveedores")
        return 1
    bien(f"Sitio: {settings.sp_site_url}")
    bien(f"Modo de acceso: {settings.sp_auth_mode}")
    if settings.sp_auth_mode == "delegado":
        cual = settings.sp_client_id or sharepoint.CLIENT_ID_POR_OMISION
        propia = " (propia)" if settings.sp_client_id else " (app publica de Microsoft)"
        bien(f"Client id: {cual}{propia}")

    # --- 2. Sesion ---------------------------------------------------------- #
    titulo("2. Sesion")
    cuenta = sharepoint.sesion_activa(settings)
    if cuenta:
        bien(f"Ya hay sesion guardada: {cuenta}")
    else:
        nota("No hay sesion guardada. Vamos a iniciarla ahora.")
        try:
            cuenta = sharepoint.iniciar_sesion_por_codigo(settings)
            bien(f"Sesion iniciada como {cuenta}")
        except sharepoint.SharePointError as exc:
            mal(str(exc))
            print(_ayuda_consentimiento())
            return 1

    try:
        token = sharepoint.obtener_token(settings)
        bien("El token sirve.")
    except sharepoint.SharePointError as exc:
        mal(str(exc))
        print(_ayuda_consentimiento())
        return 1

    # --- 3 a 5. Acceso real ------------------------------------------------- #
    with sharepoint.GraphClient(token) as client:
        titulo("3. Acceso al sitio")
        try:
            sync = sharepoint.SharePointSync(None, client, settings.sp_site_url)
            site_id = sync.site_id
            bien(f"El sitio existe y lo puedes leer.")
            nota(f"id interno: {site_id[:60]}...")
        except sharepoint.SharePointError as exc:
            mal(str(exc))
            nota("Revisa que la liga sea exactamente la que ves en el navegador,")
            nota("y que tu cuenta tenga acceso al sitio.")
            return 1

        titulo("4. Leer las listas del sitio")
        try:
            listas = client.get_todo(f"/sites/{site_id}/lists", params={"$top": "200"})
            visibles = [l["displayName"] for l in listas if not l.get("system")]
            bien(f"Puedes leer las listas del sitio ({len(visibles)} encontradas).")
            for nombre in visibles[:10]:
                nota(f"- {nombre}")
            if len(visibles) > 10:
                nota(f"... y {len(visibles) - 10} mas.")
        except sharepoint.SharePointError as exc:
            mal(str(exc))
            return 1

        titulo("5. Crear y escribir una lista de prueba")
        creada = None
        try:
            previa = next((l for l in listas if l["displayName"] == LISTA_DE_PRUEBA), None)
            if previa:
                nota("Habia una lista de prueba anterior; se reusa y se borra al final.")
                creada = previa["id"]
            else:
                creada = client.post(
                    f"/sites/{site_id}/lists",
                    {
                        "displayName": LISTA_DE_PRUEBA,
                        "description": "Lista temporal del diagnostico. Se borra sola.",
                        "columns": [{"name": "Prueba", "text": {}}],
                        "list": {"template": "genericList"},
                    },
                )["id"]
                bien("Puedes CREAR listas.")

            client.post(
                f"/sites/{site_id}/lists/{creada}/items",
                {"fields": {"Title": "fila-de-prueba", "Prueba": "ok"}},
            )
            bien("Puedes ESCRIBIR elementos.")
        except sharepoint.SharePointError as exc:
            mal(str(exc))
            nota("Puedes leer, pero no escribir. Necesitas ser propietario del sitio")
            nota("o miembro con permiso de edicion. Si el sitio es tuyo, revisa")
            nota("Configuracion -> Permisos del sitio.")
            return 1
        finally:
            if creada:
                try:
                    client.delete(f"/sites/{site_id}/lists/{creada}")
                    nota("Lista de prueba borrada.")
                except sharepoint.SharePointError:
                    nota(f"No se pudo borrar '{LISTA_DE_PRUEBA}'. Borrala a mano.")

    print("\n" + "=" * 72)
    print(" TODO EN ORDEN. El portal puede publicar en tu SharePoint solo.")
    print("=" * 72)
    print("""
Lo que sigue:
  1. Portal -> Proveedores: a cada uno capturale el nombre de su lista
     (por ejemplo "Ordenes - PROV-A"). El portal la crea sola.
  2. Portal -> SharePoint -> Sincronizar ahora.
  3. En SharePoint, comparte cada lista SOLO con su proveedor.
  4. Para que corra sola, programa scripts/sincronizar.py cada hora.
""")
    return 0


def _ayuda_consentimiento() -> str:
    return """
QUE SIGNIFICA ESTO

Tu tenant no tiene consentida la aplicacion publica de Microsoft, que es la que
el portal usa por omision. Tienes dos salidas, y ninguna necesita que Sistemas
te haga caso si te dejan registrar aplicaciones:

  OPCION 1 - Registrar tu propia aplicacion (10 minutos)
    1. Entra a https://entra.microsoft.com con tu cuenta.
    2. Aplicaciones -> Registros de aplicaciones -> Nuevo registro.
       Nombre: "Portal de ordenes"
       Tipos de cuenta: solo este directorio.
       NO configures URI de redireccion.
    3. Ya creada, ve a Autenticacion -> Configuracion avanzada y pon
       "Permitir flujos de cliente publico" en SI. Guarda.
    4. Permisos de API -> Agregar -> Microsoft Graph -> Delegados ->
       Sites.ReadWrite.All.
    5. Copia el "Id. de aplicacion" y el "Id. de directorio" al .env:
         SP_CLIENT_ID=...
         SP_TENANT_ID=...
    6. Vuelve a correr este diagnostico.

    Si el paso 4 te marca "requiere consentimiento del administrador", esa via
    esta cerrada sin admin. Pasa a la opcion 2.

  OPCION 2 - Sin permisos de nada: exportar y subir a mano
    El portal genera un Excel por proveedor, ya clasificado, y tu los subes a
    SharePoint. Pierdes lo automatico, pero funciona hoy y sin pedirle nada a
    nadie:  Portal -> Proveedores -> Exportar por proveedor.
"""


if __name__ == "__main__":
    raise SystemExit(main())
