# Conectar el portal con tu SharePoint (sin Power Automate, sin Sistemas)

El portal publica él mismo en SharePoint: clasifica las órdenes por proveedor, crea la
lista de cada uno si no existe, la mantiene al día y baja el status que el proveedor
seleccionó.

**El proveedor solo cambia el status, en la lista que le corresponde a él.** Su lista
tiene exactamente una columna editable; todo lo demás lo escribe el portal.

No hace falta Power Automate, ni licencia Premium, ni gateway. Lo único que hace falta
es que tu cuenta pueda escribir en el sitio que ya tienes.

---

## Los 5 pasos

### 1. Captura la liga de tu sitio

Abre tu sitio de SharePoint en el navegador y copia la liga de la barra de direcciones.
Va en el archivo `.env`:

```
SP_SITE_URL=https://TUTENANT.sharepoint.com/sites/Proveedores
```

Es la liga del **sitio**, no la de una lista ni la de una carpeta. Si tu liga trae cosas
después del nombre del sitio (`/Lists/...`, `/SitePages/...`), recórtala ahí.

### 2. Corre el diagnóstico

```bash
python scripts/diagnostico_sharepoint.py
```

Revisa cinco cosas en orden — configuración, sesión, acceso al sitio, lectura de listas
y permiso de escritura — y se detiene en la primera que falle diciéndote qué hacer.
Crea una lista de prueba para verificar que puede escribir, y la borra al terminar.

La primera vez te va a pedir iniciar sesión: te da un código, lo pegas en
<https://microsoft.com/devicelogin> y entras con tu cuenta de Toyota.

**Si el diagnóstico pasa completo, ya está todo listo.** Salta al paso 4.

### 3. Si el diagnóstico falla en la sesión

Significa que tu tenant no tiene consentida la aplicación pública de Microsoft que el
portal usa por omisión. El propio diagnóstico te imprime las dos salidas; en resumen:

**Opción 1 — registra tu propia aplicación** (10 minutos, en
<https://entra.microsoft.com>). Necesita que tu tenant permita que los usuarios
registren aplicaciones, que es lo normal:

1. Aplicaciones → Registros de aplicaciones → Nuevo registro. Nombre: `Portal de órdenes`.
   Tipos de cuenta: solo este directorio. **Sin URI de redirección.**
2. Autenticación → Configuración avanzada → *Permitir flujos de cliente público*: **Sí**.
3. Permisos de API → Agregar → Microsoft Graph → **Delegados** → `Sites.ReadWrite.All`.
4. Copia el *Id. de aplicación* y el *Id. de directorio* al `.env`:
   ```
   SP_CLIENT_ID=...
   SP_TENANT_ID=...
   ```
5. Vuelve a correr el diagnóstico.

Si el paso 3 te dice *"requiere consentimiento del administrador"*, esa vía está cerrada
sin admin. Pasa a la opción 2.

**Opción 2 — exportar y subir a mano.** El portal genera un Excel por proveedor, ya
clasificado: Proveedores → *Exportar un Excel por proveedor*. Pierdes lo automático pero
funciona hoy, sin pedirle permiso a nadie.

### 4. Asigna una lista a cada proveedor

Portal → **Proveedores** → Editar → *Lista de SharePoint*. Por ejemplo `Ordenes - PROV-A`.
No la crees en SharePoint: el portal la crea sola, con sus columnas, en la primera
sincronización.

Ahí mismo decides si a ese proveedor se le publica el precio.

### 5. Sincroniza

Portal → **SharePoint** → *Sincronizar ahora*.

Para que corra sola, programa esto en el **Programador de tareas de Windows**, cada hora:

```
Programa:   C:\ruta\a\Suppliers_control\.venv\Scripts\python.exe
Argumentos: scripts\sincronizar.py
Iniciar en: C:\ruta\a\Suppliers_control
```

Devuelve 0 si todo salió bien y 1 si hubo problemas, así que el Programador marca los
fallos solo.

---

## Lo que hace cada sincronización

Siempre en este orden, y el orden importa:

1. **Baja** el status que seleccionaron los proveedores. Cada cambio queda en el
   historial del portal con quién lo hizo y cuándo.
2. **Sube** los campos del sistema: número, parte, cantidad, precio y fecha requerida.
   Al hacerlo restaura cualquier columna que un proveedor haya editado por error.

Al revés, la subida borraría un status recién seleccionado. Hay una prueba que lo cubre
justamente por eso.

Además:

- Una orden que se cerró en compras **sale de la lista** del proveedor.
- Una fila que alguien agregó a mano y no corresponde a ninguna orden **se retira**.
- Un status fuera del catálogo **se rechaza** y aparece en el detalle de la
  sincronización, sin detener al resto.
- Si nada cambió, no se escribe nada: cada escritura cuesta una llamada.

## Lo único que sí tienes que hacer a mano

**Compartir cada lista solo con su proveedor.** El portal deliberadamente no toca
permisos: un error ahí es exactamente lo que le enseñaría a un proveedor las órdenes de
otro, y no es algo que deba decidir un programa.

En cada lista: Configuración → Permisos → Dejar de heredar → quitar todo → agregar solo
al proveedor con permiso de edición. Después entra con su cuenta y compruébalo tú mismo.

Para invitar proveedores externos, el sitio tiene que permitir invitados. Si tu sitio ya
los permite, no necesitas a nadie. Si no, es la única cosa de toda esta guía que sí
depende de que alguien te habilite algo.

## Las columnas que crea el portal

| Columna | Tipo | Quién la controla |
|---|---|---|
| `Título` | Texto | Sistema — guarda la clave `OC-1001-1` |
| `Orden`, `Linea`, `Parte`, `Descripcion`, `Cantidad`, `Unidad`, `FechaRequerida` | varios | Sistema |
| `Precio`, `Moneda` | Número, Texto | Sistema — solo si autorizaste el precio |
| **`Status`** | **Elección** | **Proveedor — la única editable** |

La **fecha promesa** y la **nota** no están en esta tabla a propósito: son internas. Las
captura mantenimiento en el portal (pantalla de la orden → *Seguimiento interno*),
normalmente con lo que el proveedor dijo por teléfono, y nunca salen hacia SharePoint.

La columna `Status` se crea con el catálogo cerrado y sin captura libre. `Pendiente` no
aparece entre las opciones: lo asigna el sistema a las órdenes sin contestar.

Para que el proveedor no vea siquiera editables las columnas del sistema, escóndelas del
formulario: Lista → Editar formulario → Editar columnas, y deja solo `Status`. Es
comodidad, no seguridad — la seguridad real es que el portal las restaura en la siguiente
sincronización, y que un campo que no sea el status simplemente no entra a la base.

## Cuando algo falla

| Lo que ves | Qué pasa |
|---|---|
| `No hay sesion guardada` | Corre `python scripts/conectar_sharepoint.py`. |
| `La sesion guardada vencio` | Lo mismo. Pasa cada varias semanas si el portal estuvo parado. |
| `El tenant no tiene consentida la aplicacion` | Paso 3 de arriba. |
| `Tu cuenta no tiene permiso para escribir` | Necesitas ser propietario del sitio o miembro con permiso de edición. |
| `No se encontro en SharePoint` | Revisa `SP_SITE_URL`: debe ser la liga del sitio, sin nada después. |
| `SP_SITE_URL no parece una liga valida` | Falta el `https://`. |
| `Ningun proveedor activo tiene lista asignada` | Paso 4. |

El archivo `.sharepoint-token.json` guarda tu sesión y **está en `.gitignore`**. No lo
subas a ningún lado ni lo copies a otra máquina: da acceso a tu SharePoint.
