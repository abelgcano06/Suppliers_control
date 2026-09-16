<#
.SINOPSIS
    Crea en SharePoint una lista por proveedor, con las columnas y el versionado
    que espera el portal de seguimiento de ordenes.

.DESCRIPCION
    Lee los proveedores desde la API del portal y, por cada uno que tenga lista
    asignada, crea la lista si no existe y le agrega las columnas faltantes.

    Es idempotente: se puede correr las veces que haga falta. No borra columnas,
    no borra listas y no toca los datos que ya esten capturados.

    NO asigna permisos ni invita a los proveedores. Eso se hace a mano, lista por
    lista, y viene explicado al final de la ejecucion.

.REQUISITOS
    Install-Module PnP.PowerShell -Scope CurrentUser

.EJEMPLO
    # Primero en seco, para ver que haria sin tocar nada:
    .\crear_listas_sharepoint.ps1 -SitioUrl https://tmmbc.sharepoint.com/sites/Proveedores `
                                  -UrlPortal http://localhost:8000 -ClaveApi "tu-clave" -Simular

    # Y ya en serio:
    .\crear_listas_sharepoint.ps1 -SitioUrl https://tmmbc.sharepoint.com/sites/Proveedores `
                                  -UrlPortal http://localhost:8000 -ClaveApi "tu-clave"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $SitioUrl,
    [Parameter(Mandatory = $true)] [string] $UrlPortal,
    [Parameter(Mandatory = $true)] [string] $ClaveApi,

    # Solo estos proveedores (por codigo). Vacio = todos los activos con lista asignada.
    [string[]] $SoloProveedores = @(),

    # Muestra lo que haria, sin crear nada.
    [switch] $Simular
)

$ErrorActionPreference = "Stop"

# --------------------------------------------------------------------------- #
# Columnas de la lista
# --------------------------------------------------------------------------- #
# OJO: la columna "Clave" es en realidad la columna Title que SharePoint crea
# sola, renombrada. En Power Automate se referencia como 'Title', no 'Clave'.

$ColumnasSistema = @(
    @{ Nombre = "Orden";          Tipo = "Text" }
    @{ Nombre = "Linea";          Tipo = "Number" }
    @{ Nombre = "Parte";          Tipo = "Text" }
    @{ Nombre = "Descripcion";    Tipo = "Note" }
    @{ Nombre = "Cantidad";       Tipo = "Number" }
    @{ Nombre = "Unidad";         Tipo = "Text" }
    @{ Nombre = "FechaRequerida"; Tipo = "DateTime" }
)

# Solo se agregan a los proveedores con el precio autorizado en el portal.
$ColumnasPrecio = @(
    @{ Nombre = "Precio"; Tipo = "Currency" }
    @{ Nombre = "Moneda"; Tipo = "Text" }
)

# Lo unico que el proveedor puede capturar.
$ColumnasProveedor = @(
    @{ Nombre = "FechaPromesa"; Tipo = "DateTime" }
    @{ Nombre = "Comentario";   Tipo = "Note" }
)

# --------------------------------------------------------------------------- #

function Escribir($mensaje, $color = "White") { Write-Host $mensaje -ForegroundColor $color }

function Invocar-Portal($ruta) {
    $uri = "$($UrlPortal.TrimEnd('/'))$ruta"
    try {
        return Invoke-RestMethod -Uri $uri -Headers @{ "X-API-Key" = $ClaveApi } -Method Get
    } catch {
        throw "No se pudo consultar $uri. Revisa que el portal este corriendo y que la clave sea correcta. Detalle: $($_.Exception.Message)"
    }
}

function Asegurar-Columna($lista, $columna) {
    $existente = Get-PnPField -List $lista -Identity $columna.Nombre -ErrorAction SilentlyContinue
    if ($existente) {
        Escribir "      = $($columna.Nombre) (ya existe)" "DarkGray"
        return
    }
    if ($Simular) {
        Escribir "      + $($columna.Nombre) [$($columna.Tipo)]  (simulado)" "Yellow"
        return
    }

    $parametros = @{
        List          = $lista
        DisplayName   = $columna.Nombre
        InternalName  = $columna.Nombre
        Type          = $columna.Tipo
        AddToDefaultView = $true
    }
    if ($columna.Opciones) { $parametros.Choices = $columna.Opciones }

    Add-PnPField @parametros | Out-Null
    Escribir "      + $($columna.Nombre) [$($columna.Tipo)]" "Green"
}

# --------------------------------------------------------------------------- #
# Arranque
# --------------------------------------------------------------------------- #

if ($Simular) {
    Escribir "`n*** MODO SIMULACION: no se va a crear ni modificar nada ***`n" "Yellow"
}

Escribir "Consultando el catalogo de status del portal..." "Cyan"
$catalogo = Invocar-Portal "/api/v1/status-catalog"
$opcionesStatus = $catalogo.editable_por_proveedor
Escribir "  Status que podra elegir el proveedor: $($opcionesStatus -join ', ')" "DarkGray"
Escribir "  ('Pendiente' NO se incluye a proposito: lo asigna el sistema.)" "DarkGray"

$ColumnaStatus = @{ Nombre = "Status"; Tipo = "Choice"; Opciones = $opcionesStatus }

Escribir "`nConsultando proveedores activos..." "Cyan"
$proveedores = Invocar-Portal "/api/v1/suppliers?only_active=true"

if ($SoloProveedores.Count -gt 0) {
    $proveedores = $proveedores | Where-Object { $SoloProveedores -contains $_.code }
}

$conLista = $proveedores | Where-Object { $_.sharepoint_list }
$sinLista = $proveedores | Where-Object { -not $_.sharepoint_list }

foreach ($p in $sinLista) {
    Escribir "  ! $($p.code) - $($p.name): sin lista asignada en el portal, se omite." "Yellow"
}

if ($conLista.Count -eq 0) {
    Escribir "`nNo hay proveedores con lista asignada." "Red"
    Escribir "Ve al portal -> Proveedores -> Editar y capturales la 'Lista de SharePoint'." "Red"
    exit 1
}

Escribir "`nConectando a $SitioUrl ..." "Cyan"
Connect-PnPOnline -Url $SitioUrl -Interactive
Escribir "  Conectado." "Green"

# --------------------------------------------------------------------------- #
# Una lista por proveedor
# --------------------------------------------------------------------------- #

$creadas = 0
$actualizadas = 0

foreach ($proveedor in $conLista) {
    $nombreLista = $proveedor.sharepoint_list
    Escribir "`n--- $($proveedor.code) - $($proveedor.name)" "White"
    Escribir "    Lista: $nombreLista" "DarkGray"

    $lista = Get-PnPList -Identity $nombreLista -ErrorAction SilentlyContinue

    if (-not $lista) {
        if ($Simular) {
            Escribir "    (se crearia la lista)" "Yellow"
        } else {
            New-PnPList -Title $nombreLista -Template GenericList -EnableVersioning | Out-Null
            Escribir "    Lista creada." "Green"
        }
        $creadas++
    } else {
        Escribir "    La lista ya existe; solo se revisan las columnas." "DarkGray"
        $actualizadas++
    }

    if ($Simular -and -not $lista) { continue }  # sin lista real no hay columnas que revisar

    # La columna Title obligatoria de SharePoint se reusa como la clave de la orden.
    if (-not $Simular) {
        Set-PnPField -List $nombreLista -Identity "Title" -Values @{
            Title       = "Clave"
            Description = "Identificador de la linea de orden. Lo escribe el sistema; no editar."
            Indexed     = $true
        } | Out-Null
        Escribir "      = Clave (columna Title renombrada e indexada)" "Green"
    }

    Escribir "    Columnas del sistema (las reescribe el flujo en cada bajada):" "DarkGray"
    foreach ($columna in $ColumnasSistema) { Asegurar-Columna $nombreLista $columna }

    if ($proveedor.share_price) {
        Escribir "    Precio autorizado para este proveedor:" "DarkGray"
        foreach ($columna in $ColumnasPrecio) { Asegurar-Columna $nombreLista $columna }
    } else {
        Escribir "    Precio NO autorizado: no se crean las columnas Precio/Moneda." "DarkGray"
    }

    Escribir "    Columnas que captura el proveedor:" "DarkGray"
    Asegurar-Columna $nombreLista $ColumnaStatus
    foreach ($columna in $ColumnasProveedor) { Asegurar-Columna $nombreLista $columna }

    if (-not $Simular) {
        Set-PnPList -Identity $nombreLista -EnableVersioning $true -EnableAttachments $false | Out-Null
        Escribir "      Versionado activado, archivos adjuntos desactivados." "Green"
    }
}

# --------------------------------------------------------------------------- #
# Lo que queda a mano
# --------------------------------------------------------------------------- #

Escribir "`n=======================================================================" "Cyan"
Escribir " Listas creadas: $creadas    Listas ya existentes revisadas: $actualizadas" "Cyan"
Escribir "=======================================================================" "Cyan"

if ($Simular) {
    Escribir "`nFue una simulacion. Vuelve a correrlo sin -Simular para aplicarlo." "Yellow"
    exit 0
}

Escribir @"

FALTA HACERLO A MANO, lista por lista. El script NO toca permisos a proposito:
un error aqui es justo lo que expondria la informacion de un proveedor a otro.

  1. PERMISOS (lo mas importante)
     Lista -> Configuracion -> Permisos para esta lista
       a) Dejar de heredar permisos.
       b) Quitar todos los grupos heredados.
       c) Dejar solo: el equipo de mantenimiento (Editar) y la cuenta de
          invitado de ESE proveedor (Editar).
     Con esto un proveedor no puede ver la lista de otro.

  2. INVITAR AL PROVEEDOR
     Compartir la lista con su correo. Le llega una invitacion de invitado.
     Requiere que Sistemas haya habilitado invitados externos en este sitio.

  3. OCULTAR DEL FORMULARIO LAS COLUMNAS DEL SISTEMA
     Lista -> Editar formulario -> Editar columnas: deja visibles solo
     Status, FechaPromesa y Comentario.
     Es comodidad, no seguridad: aunque el proveedor las edite, el portal
     ignora esos campos y los restaura en la siguiente bajada.

  4. VERIFICAR COMO PROVEEDOR
     Entra con la cuenta de invitado de uno de ellos y confirma tu mismo
     que solo ve su lista. No lo des por hecho.

  5. ARMAR LOS DOS FLUJOS
     docs/power-automate.md, secciones 2 y 3.

RECORDATORIO PARA POWER AUTOMATE:
  La columna que ves como "Clave" tiene nombre interno 'Title'.
  En las expresiones del flujo se referencia como Title, no como Clave.

"@ "White"

Disconnect-PnPOnline
