# Configurar SharePoint y Power Automate

Esta guía cubre la zona Microsoft 365 del esquema: el sitio de SharePoint con una lista
por proveedor y los dos flujos que mueven la información en ambos sentidos.

El portal ya expone todo lo que los flujos necesitan en `/api/v1`. Si levantas el portal
y abres `/docs`, puedes probar cada llamada desde el navegador antes de armar el flujo.

---

## 1. El sitio de SharePoint

Crea **un sitio de comunicación** llamado `Proveedores`. Un solo sitio, no todo el
tenant — es el alcance que se le pide a Sistemas.

En ese sitio, **una lista por proveedor**: `Ordenes - PROV-A`, `Ordenes - PROV-B`, etc.
El nombre que uses aquí es el que capturas en el portal, en Proveedores → Editar →
*Lista de SharePoint*.

### Columnas de cada lista

| Columna | Tipo en SharePoint | Quién la controla | Permisos |
|---|---|---|---|
| `Clave` | Texto (una línea) | Sistema | Solo lectura |
| `Orden` | Texto | Sistema | Solo lectura |
| `Linea` | Número | Sistema | Solo lectura |
| `Parte` | Texto | Sistema | Solo lectura |
| `Descripcion` | Texto (varias líneas) | Sistema | Solo lectura |
| `Cantidad` | Número | Sistema | Solo lectura |
| `Unidad` | Texto | Sistema | Solo lectura |
| `FechaRequerida` | Fecha | Sistema | Solo lectura |
| **`Status`** | **Elección** | **Proveedor** | **Editable** |
| **`FechaPromesa`** | **Fecha** | **Proveedor** | **Editable** |
| **`Comentario`** | **Texto (varias líneas)** | **Proveedor** | **Editable** |

`Precio` y `Moneda` solo se agregan a las listas de los proveedores que tengan
*Publicar precio* activado en el portal. Para el resto, el campo ni siquiera viaja en el
payload de la API.

> SharePoint no tiene columnas de solo lectura por usuario. La protección real es la de
> la regla 2 del README: **el portal ignora cualquier campo del sistema que le manden y
> lo restaura en la siguiente bajada**. Si además quieres que el proveedor ni las vea
> editables, ocúltalas del formulario con *Editar formulario → Editar columnas*.

### Valores de la columna `Status`

Pégalos tal cual. El portal los devuelve en
`GET /api/v1/status-catalog` → `editable_por_proveedor`:

```
Recibida
En proceso
Enviada
Retrasada
Entregada
```

**No incluyas `Pendiente`**: ese valor lo asigna el sistema a las órdenes que el
proveedor todavía no contesta, y la API rechaza que un proveedor lo seleccione.

### Permisos y versionado

1. En cada lista: **Configuración → Permisos → Dejar de heredar permisos**.
2. Quita todos los grupos y deja solo: el equipo de mantenimiento (Editar) y la cuenta
   de invitado de ese proveedor (Editar).
3. **Configuración de versiones → Crear una versión cada vez que edite un elemento: Sí.**
   Esto conserva quién cambió qué y cuándo del lado de SharePoint; el portal guarda su
   propio historial en paralelo.

Con permisos independientes por lista, un proveedor no puede ver la lista de otro.

---

## 2. Flujo de bajada: portal → SharePoint

**Cuándo corre:** programado, cada hora en horario laboral.

```
Recurrencia (cada 1 hora)
│
├─ HTTP: GET {URL_PORTAL}/api/v1/suppliers
│    Headers: X-API-Key = @{variables('ClaveAPI')}
│
└─ Aplicar a cada  (proveedor del cuerpo de la respuesta)
   │
   ├─ Condición: sharepoint_list no está vacío
   │
   └─ HTTP: GET {URL_PORTAL}/api/v1/suppliers/@{item()['code']}/orders?include_closed=true
      │    Headers: X-API-Key = @{variables('ClaveAPI')}
      │
      └─ Aplicar a cada (orden)
         │
         ├─ Obtener elementos  (lista: @{item()['sharepoint_list']},
         │                      filtro: Clave eq '@{items('orden')['key']}')
         │
         ├─ Si NO existe y is_closed = false  → Crear elemento
         │     Clave, Orden, Linea, Parte, Descripcion, Cantidad, Unidad,
         │     FechaRequerida, Status (el que viene del portal)
         │
         ├─ Si SÍ existe y is_closed = false  → Actualizar elemento
         │     Solo las columnas del sistema. NO toques Status, FechaPromesa
         │     ni Comentario: ahí está lo que capturó el proveedor.
         │
         └─ Si is_closed = true               → Eliminar elemento
               La orden se cerró en compras y sale de la lista del proveedor.
```

Guarda la clave de la API en una **variable segura** del flujo o, mejor, en Azure Key
Vault. No la escribas en la acción HTTP a la vista.

Este flujo es el que **restaura** cualquier columna del sistema que un proveedor haya
editado: al actualizar el elemento, la sobreescribe con el valor de la base de datos.

---

## 3. Flujo de subida: SharePoint → portal

**Cuándo corre:** con el disparador *Cuando se crea o modifica un elemento*, uno por
lista de proveedor. Si tienes muchos proveedores, una recurrencia cada 15 minutos que
recorra las listas buscando elementos modificados también sirve y gasta menos.

```
Cuando se crea o modifica un elemento  (lista: Ordenes - PROV-A)
│
├─ Condición: Status no está vacío
│    Un elemento recién creado por el flujo de bajada no debe rebotar.
│    Compara Modificado con Creado, o usa la columna Modificado por.
│
└─ HTTP: POST {URL_PORTAL}/api/v1/supplier-updates
     Headers: X-API-Key = @{variables('ClaveAPI')}
               Content-Type = application/json
     Body:
     {
       "supplier_code": "PROV-A",
       "updates": [
         {
           "po_number":        "@{triggerOutputs()?['body/Orden']}",
           "line_number":      @{triggerOutputs()?['body/Linea']},
           "status":           "@{triggerOutputs()?['body/Status/Value']}",
           "promised_date":    "@{triggerOutputs()?['body/FechaPromesa']}",
           "supplier_comment": "@{triggerOutputs()?['body/Comentario']}",
           "changed_by":       "@{triggerOutputs()?['body/Editor/Email']}",
           "changed_at":       "@{triggerOutputs()?['body/Modified']}"
         }
       ]
     }
```

`updates` acepta hasta 500 cambios por llamada, así que un flujo por lotes puede mandar
todo lo modificado en una sola petición.

### Qué contesta el portal

```json
{
  "supplier_code": "PROV-A",
  "received": 2,
  "applied": 1,
  "rejected": 1,
  "results": [
    { "po_number": "OC-1001", "line_number": 1, "applied": true,
      "changed_fields": ["status", "promised_date"], "message": "Cambio aplicado." },
    { "po_number": "OC-1001", "line_number": 2, "applied": false,
      "changed_fields": [], "message": "Status 'ya casi' no esta en el catalogo. Validos: ..." }
  ]
}
```

**Un cambio inválido no cancela el resto del lote.** El flujo devuelve 200 aunque haya
rechazos: revisa `rejected` y manda un correo al equipo de mantenimiento si es mayor a
cero. Los rechazos que vas a ver en la práctica:

| Mensaje | Qué pasó |
|---|---|
| `no pertenece al proveedor X` | El flujo mandó la orden con el `supplier_code` equivocado. Revisa que cada flujo use el código de su lista. |
| `no esta en el catalogo` | La columna `Status` de SharePoint tiene valores que no coinciden con el catálogo. |
| `'Pendiente' lo asigna el sistema` | Quitaste `Pendiente` de la columna de elección, pero un elemento viejo lo conserva. |
| `ya esta cerrada y no admite cambios` | La orden se cerró en compras. El flujo de bajada la va a quitar de la lista. |
| `no existe en el portal` | El elemento de SharePoint quedó huérfano. El flujo de bajada lo elimina. |

---

## 4. Referencia rápida de la API

Todas requieren el header `X-API-Key`.

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/api/v1/suppliers` | Proveedores activos y su lista asignada |
| `GET` | `/api/v1/suppliers/{code}/orders` | Las órdenes de ese proveedor (`?include_closed=true` para las cerradas) |
| `GET` | `/api/v1/status-catalog` | Valores de la columna `Status` |
| `POST` | `/api/v1/supplier-updates` | Los cambios que capturó el proveedor |

Probar desde la terminal:

```bash
curl -H "X-API-Key: tu-clave" http://127.0.0.1:8000/api/v1/suppliers/PROV-A/orders

curl -X POST http://127.0.0.1:8000/api/v1/supplier-updates \
  -H "X-API-Key: tu-clave" -H "Content-Type: application/json" \
  -d '{"supplier_code":"PROV-A","updates":[
        {"po_number":"OC-1001","line_number":1,"status":"En proceso",
         "promised_date":"2026-10-20","supplier_comment":"En fabricacion",
         "changed_by":"invitado@prov-a.mx"}]}'
```

---

## 5. Orden de arranque del piloto

1. Levanta el portal contra SQL Server y cambia `SYNC_API_KEY`.
2. Carga el reporte de órdenes abiertas del Paint Shop.
3. En Proveedores, deja activos solo los 2 o 3 del piloto y asígnales su lista.
4. Crea el sitio y las listas con las columnas de arriba.
5. Invita a las cuentas de los proveedores, una por lista.
6. Arma el flujo de bajada y córrelo a mano una vez. Verifica que cada proveedor ve
   solo lo suyo — entra con la cuenta de invitado y compruébalo tú.
7. Arma el flujo de subida. Cambia un status desde la lista y confirma que aparece en
   el tablero y en el historial del portal.
8. Deja correr un mes y mide con el tablero: **% de órdenes con status actualizado** y
   **tiempo promedio de respuesta**.
