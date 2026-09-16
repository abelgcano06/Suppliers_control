# Seguimiento de órdenes abiertas con proveedores

Portal interno para el equipo de mantenimiento de Paint Shop (TMMBC) y su canal de
sincronización hacia SharePoint. Implementa el concepto de la propuesta:

> Nosotros cargamos el reporte de órdenes abiertas una vez, el sistema reparte a cada
> proveedor solo sus órdenes, el proveedor marca el status de cada una, y nosotros
> vemos todo consolidado en un solo tablero.

**Lo único que hace el proveedor es cambiar el status**, en la lista de SharePoint que
le corresponde a él. Su lista tiene exactamente una columna editable.

**Los proveedores nunca entran a este portal.** Su único punto de contacto es su lista
de SharePoint.

El portal publica en SharePoint **él solo**, sin Power Automate y sin depender de
Sistemas: clasifica las órdenes por proveedor, crea la lista de cada uno si no existe,
la mantiene al día y baja lo que el proveedor capturó.

```
Zona interna (mantenimiento)                          Zona externa
┌──────────────────────────┐                      ┌──────────────────┐
│  Excel de compras        │                      │  Proveedor A     │
│           ↓              │   SharePoint         │  ve solo su lista│
│  Portal (este repo)      │◄────────────────────►│  Proveedor B     │
│           ↓              │   1 lista x proveedor│  ve solo su lista│
│  Base de datos SQL       │                      │  Proveedor C     │
└──────────────────────────┘                      └──────────────────┘
```

Si prefieres el esquema original con Power Automate en medio, también está: la API
`/api/v1` sigue completa y documentada en [`docs/power-automate.md`](docs/power-automate.md).

## Arrancar en 3 minutos

```bash
pip install -r requirements-dev.txt
cp .env.example .env            # ajusta SYNC_API_KEY
python scripts/seed_demo.py     # datos de demostración (opcional)
uvicorn app.main:app --reload
```

- Portal: <http://127.0.0.1:8000>
- Documentación interactiva de la API: <http://127.0.0.1:8000/docs>

Pruebas: `python -m pytest -q` (135 pruebas).

## Qué hace el portal

| Pantalla | Para qué sirve |
|---|---|
| **Tablero** (`/`) | Todas las órdenes abiertas de todos los proveedores, con alertas de retraso y de sin respuesta, filtros y exportación a CSV. |
| **Cargar reporte** (`/cargar`) | Sube el Excel de compras. Valida, da de alta, actualiza y cierra órdenes. Muestra fila por fila lo que rechazó y por qué. |
| **Proveedores** (`/proveedores`) | Alta y baja, lista de SharePoint asignada, si se publica el precio, y el % de respuesta de cada uno. |
| **Orden** (`/ordenes/{id}`) | El detalle, su historial completo, y donde mantenimiento captura la **fecha promesa** y una **nota interna** — lo que el proveedor dijo por teléfono. No se publica. |
| **SharePoint** (`/sharepoint`) | Publica las listas de todos los proveedores y baja sus respuestas, con el detalle de lo que pasó proveedor por proveedor. |
| **Historial** (`/historial`) | Quién cambió qué, cuándo y desde dónde (Excel, proveedor o portal). |

### Indicadores del piloto

El plan de arranque mide dos cosas, y las dos están en el tablero:

- **% de órdenes con status actualizado** — cuántas dejaron de estar en `Pendiente`.
- **Tiempo promedio de respuesta del proveedor** — horas entre el alta de la orden y
  la primera captura del proveedor.

## Reglas que el código garantiza

Estas son las reglas de seguridad de la propuesta, cada una cubierta por pruebas:

1. **Aislamiento entre proveedores.** `GET /api/v1/suppliers/{code}/orders` devuelve
   solo las órdenes de ese proveedor, y un `POST` que intente tocar la orden de otro se
   rechaza sin revelar de quién es.
2. **El status es lo único que un proveedor puede cambiar.** Cualquier otro campo que
   venga de afuera se ignora, aunque llegue en el mismo cuerpo de la petición.
3. **Los campos del sistema son intocables desde afuera.** Número, parte, cantidad,
   precio y fecha requerida solo cambian con una carga de Excel, y la siguiente
   sincronización restaura lo que un proveedor haya editado.
4. **El status es un catálogo cerrado.** Seis valores, sin texto libre. `Pendiente` lo
   asigna el sistema y el proveedor no lo puede seleccionar.
5. **El precio es opcional por proveedor.** Con `share_price` apagado el campo no viaja
   en el payload — no viaja vacío, simplemente no existe.
6. **La fecha promesa y la nota nunca se publican.** Son internas: las captura
   mantenimiento en el portal y no aparecen en la lista de ningún proveedor.
7. **Historial completo.** Cada diferencia queda en `order_changes` con campo, valor
   anterior, valor nuevo, origen, quién y cuándo.
8. **La carga nunca pisa el status del proveedor.** El Excel actualiza sus campos y deja
   intacto lo que el proveedor seleccionó.

## La carga del Excel

El portal busca la fila de encabezados en las primeras 15 filas y compara los nombres
sin acentos ni mayúsculas, así que `Fecha Requerida`, `FECHA REQUERIDA` y
`fecha_requerida` son la misma columna. Solo **Orden** y **Línea** son obligatorias.

| Campo | Encabezados aceptados |
|---|---|
| Orden * | Orden, OC, PO, Número Orden, Documento |
| Línea * | Línea, Partida, Posición, Item |
| Proveedor | Código Proveedor, Proveedor, Vendor |
| Parte | Parte, Número Parte, Material, SKU |
| Descripción | Descripción, Detalle, Concepto |
| Cantidad | Cantidad, Qty |
| Unidad | Unidad, UM, UOM |
| Precio | Precio, Precio Unitario, Costo |
| Moneda | Moneda, Currency, Divisa |
| Fecha requerida | Fecha Requerida, Fecha Entrega, Due Date |

Descarga la plantilla desde `/plantilla.csv`.

**Una fila inválida no tumba la carga**: se rechaza, se cuenta y se explica
("Fila 27: 'dos' debe ser un número entero"). El resto del archivo se procesa.

### Qué pasa con las órdenes que ya no vienen en el reporte

Al cargar eliges el alcance del cierre:

- **Solo en los proveedores de este archivo** (por defecto) — seguro para cargas
  parciales; no toca a los proveedores que no vienen en el archivo.
- **En todos** — para cuando el archivo trae realmente *todas* las órdenes abiertas.
- **No cerrar ninguna**.

Si una orden cerrada vuelve a aparecer en un reporte posterior, se reabre sola.

## Instalación en producción

### Base de datos

Desarrollo usa SQLite sin configurar nada. Para SQL Server, cambia `DATABASE_URL`:

```
DATABASE_URL=mssql+pyodbc://usuario:password@servidor/OrdenesProveedores?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
```

Necesitas además `pip install pyodbc` y el *ODBC Driver 18 for SQL Server*. El esquema
se crea solo al arrancar; el mismo modelo corre en los dos motores.

### Variables de entorno

| Variable | Para qué | Valor por omisión |
|---|---|---|
| `DATABASE_URL` | Conexión a la base de datos | SQLite local |
| `SYNC_API_KEY` | Clave que Power Automate manda en `X-API-Key` | `cambia-esta-clave` |
| `NO_RESPONSE_DAYS` | Días antes de marcar "sin respuesta" | 3 |
| `DUE_SOON_DAYS` | Ventana de la alerta "por vencer" | 7 |

**Cambia `SYNC_API_KEY` antes de exponer el portal.** Es lo único que protege la API.

### Qué pedirle a Sistemas

Exactamente lo que dice la propuesta, nada más:

| Requerimiento | Por qué | Alcance |
|---|---|---|
| Usuarios invitados externos en un sitio de SharePoint | Es la única forma de que el proveedor edite su lista sin cuenta corporativa | Un sitio, no todo el tenant |
| Licencia Power Automate Premium para un usuario | El conector a SQL Server es premium | Solo el dueño de los flujos |
| Nada más | El portal y la base de datos viven en la infraestructura del área | Sin accesos nuevos a red |

## Conectar con SharePoint

Guía completa: [`docs/sharepoint-directo.md`](docs/sharepoint-directo.md). En corto:

```bash
# 1. Pon la liga de tu sitio en el .env:  SP_SITE_URL=https://...
# 2. Averigua qué te deja hacer tu tenant (crea una lista de prueba y la borra):
python scripts/diagnostico_sharepoint.py
# 3. Asigna una lista a cada proveedor en el portal. El portal la crea sola.
# 4. Portal -> SharePoint -> Sincronizar ahora.
# 5. Para que corra sola, programa esto cada hora en el Programador de Windows:
python scripts/sincronizar.py
```

Cada sincronización **primero baja** lo que capturaron los proveedores y **después sube**
los campos del sistema. El orden importa: al revés, la subida borraría una respuesta
recién capturada.

Lo único manual, y a propósito: **compartir cada lista solo con su proveedor**. El portal
no toca permisos, porque un error ahí es justo lo que le enseñaría a un proveedor las
órdenes de otro.

**Si tu tenant no te deja**, el portal exporta un Excel por proveedor ya clasificado
(Proveedores → *Exportar un Excel por proveedor*) y los subes a mano. Funciona hoy, sin
permisos de nada.

## Lo que este esquema no hace

- No reemplaza el sistema de compras; solo da seguimiento a órdenes ya emitidas.
- No permite al proveedor crear, cancelar ni modificar órdenes.
- No expone ningún sistema interno hacia afuera.

## Estructura

```
app/
  models.py              Modelo de datos y reglas derivadas (retraso, sin respuesta)
  config.py  db.py       Configuración y conexión
  schemas.py             Contratos de la API
  security.py            Autenticación por X-API-Key
  services/
    excel_import.py      Lectura, validación y upsert del reporte
    sharepoint.py        Publicación directa en SharePoint (Microsoft Graph)
    sync.py              Validación del status que mandan los proveedores
    seguimiento.py       Fecha promesa y nota interna (las captura mantenimiento)
    dashboard.py         KPIs, filtros y alertas
  routers/
    web.py               Pantallas del portal
    api.py               API para Power Automate
  templates/  static/    Interfaz
scripts/
  generar_reporte_ejemplo.py   Excel de ejemplo con el formato de compras
  seed_demo.py                 Escenario completo de demostración
  diagnostico_sharepoint.py    Averigua qué permite tu tenant, paso por paso
  conectar_sharepoint.py       Inicio de sesión, una sola vez
  sincronizar.py               Sincronización desatendida (Programador de tareas)
  crear_listas_sharepoint.ps1  Alternativa por PnP PowerShell
tests/                   135 pruebas
docs/sharepoint-directo.md   Conectar con tu SharePoint (recomendado)
docs/power-automate.md       Alternativa con Power Automate en medio
```
