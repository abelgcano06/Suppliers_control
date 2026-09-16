# Seguimiento de órdenes abiertas con proveedores

Portal interno para el equipo de mantenimiento de Paint Shop (TMMBC) y su canal de
sincronización hacia SharePoint. Implementa el concepto de la propuesta:

> Nosotros cargamos el reporte de órdenes abiertas una vez, el sistema reparte a cada
> proveedor solo sus órdenes, el proveedor marca el status de cada una, y nosotros
> vemos todo consolidado en un solo tablero.

**Los proveedores nunca entran a este portal.** Su único punto de contacto es su lista
de SharePoint, y el único puente entre las dos zonas es Power Automate, que habla con
la API `/api/v1` de este repositorio.

```
Zona interna (mantenimiento)          Microsoft 365              Zona externa
┌──────────────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│  Excel de compras        │      │                  │      │  Proveedor A     │
│           ↓              │      │  Power Automate  │      │  ve solo su lista│
│  Portal (este repo)      │◄────►│        ↕         │◄────►│  Proveedor B     │
│           ↓              │ API  │  SharePoint      │      │  ve solo su lista│
│  Base de datos SQL       │ v1   │  1 lista x prov. │      │  Proveedor C     │
└──────────────────────────┘      └──────────────────┘      └──────────────────┘
```

## Arrancar en 3 minutos

```bash
pip install -r requirements-dev.txt
cp .env.example .env            # ajusta SYNC_API_KEY
python scripts/seed_demo.py     # datos de demostración (opcional)
uvicorn app.main:app --reload
```

- Portal: <http://127.0.0.1:8000>
- Documentación interactiva de la API: <http://127.0.0.1:8000/docs>

Pruebas: `python -m pytest -q` (82 pruebas).

## Qué hace el portal

| Pantalla | Para qué sirve |
|---|---|
| **Tablero** (`/`) | Todas las órdenes abiertas de todos los proveedores, con alertas de retraso y de sin respuesta, filtros y exportación a CSV. |
| **Cargar reporte** (`/cargar`) | Sube el Excel de compras. Valida, da de alta, actualiza y cierra órdenes. Muestra fila por fila lo que rechazó y por qué. |
| **Proveedores** (`/proveedores`) | Alta y baja, lista de SharePoint asignada, si se publica el precio, y el % de respuesta de cada uno. |
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
2. **Los campos del sistema son intocables desde afuera.** Número, parte, cantidad,
   precio y fecha requerida solo cambian con una carga de Excel. Si el flujo manda esos
   campos, se ignoran; la siguiente bajada restaura lo que el proveedor haya editado.
3. **El status es un catálogo cerrado.** Seis valores, sin texto libre. `Pendiente` lo
   asigna el sistema y el proveedor no lo puede seleccionar.
4. **El precio es opcional por proveedor.** Con `share_price` apagado el campo no viaja
   en el payload — no viaja vacío, simplemente no existe.
5. **Historial completo.** Cada diferencia queda en `order_changes` con campo, valor
   anterior, valor nuevo, origen, quién y cuándo.
6. **La carga nunca pisa la respuesta del proveedor.** El Excel actualiza sus campos y
   deja intactos status, fecha promesa y comentario.

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

## Conectar SharePoint y Power Automate

La guía paso a paso está en [`docs/power-automate.md`](docs/power-automate.md): las
columnas de la lista, los dos flujos y la tabla de rechazos que se ven en la práctica.

Las listas no se crean a mano — `scripts/crear_listas_sharepoint.ps1` las genera leyendo
los proveedores del propio portal, y es idempotente. No toca permisos a propósito: ese
paso se hace revisado, porque un error ahí es justo lo que expondría la información de
un proveedor a otro.

**Antes de armar ningún flujo**, resuelve la sección 0 de esa guía: Power Automate corre
en la nube y el portal corre en la red interna, así que hay que decidir cómo se alcanzan.
Esa decisión agrega un cuarto requerimiento a la lista de Sistemas.

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
    sync.py              Bajada y subida contra SharePoint
    dashboard.py         KPIs, filtros y alertas
  routers/
    web.py               Pantallas del portal
    api.py               API para Power Automate
  templates/  static/    Interfaz
scripts/
  generar_reporte_ejemplo.py   Excel de ejemplo con el formato de compras
  seed_demo.py                 Escenario completo de demostración
  crear_listas_sharepoint.ps1  Crea las listas de SharePoint (PnP PowerShell)
tests/                   82 pruebas
docs/power-automate.md   Configuración de SharePoint y los dos flujos
```
