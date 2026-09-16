"""Genera un reporte de ordenes abiertas de ejemplo, con el formato que baja de compras.

    python scripts/generar_reporte_ejemplo.py [salida.xlsx]
"""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

PROVEEDORES = [
    ("PROV-A", "Pinturas del Norte"),
    ("PROV-B", "Refacciones Industriales BC"),
    ("PROV-C", "Filtros y Sellos Tijuana"),
]

PARTES = [
    ("PN-4471", "Filtro de cabina de pintura", "PZA", 845.50),
    ("PN-8820", "Boquilla de aplicacion 1.4 mm", "PZA", 1290.00),
    ("PN-1033", "Sello de puerta horno de curado", "MTR", 312.75),
    ("PN-5567", "Banda transportadora seccion B", "PZA", 15400.00),
    ("PN-2298", "Manguera de alta presion 3/8", "MTR", 210.40),
    ("PN-7741", "Bomba de recirculacion", "PZA", 22800.00),
    ("PN-3012", "Kit de empaques bomba", "KIT", 1875.25),
    ("PN-9004", "Termopar tipo K horno 2", "PZA", 640.00),
]


def generar(destino: Path, semilla: int = 7) -> Path:
    random.seed(semilla)
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Ordenes abiertas"

    # Un par de filas de titulo antes del encabezado, como suelen traer los reportes.
    hoja.append(["REPORTE DE ORDENES DE COMPRA ABIERTAS"])
    hoja.append([f"Generado el {date.today().isoformat()} - Mantenimiento Paint Shop"])
    hoja.append([])
    hoja.append(
        ["Orden", "Linea", "Codigo Proveedor", "Proveedor", "Parte", "Descripcion",
         "Cantidad", "Unidad", "Precio Unitario", "Moneda", "Fecha Requerida"]
    )

    hoy = date.today()
    numero_orden = 1001
    for codigo, nombre in PROVEEDORES:
        for _ in range(random.randint(3, 5)):
            orden = f"OC-{numero_orden}"
            numero_orden += 1
            for linea in range(1, random.randint(2, 4)):
                parte, descripcion, unidad, precio = random.choice(PARTES)
                hoja.append(
                    [
                        orden,
                        linea,
                        codigo,
                        nombre,
                        parte,
                        descripcion,
                        random.choice([1, 2, 4, 6, 12, 25]),
                        unidad,
                        precio,
                        "MXN",
                        hoy + timedelta(days=random.randint(-20, 45)),
                    ]
                )

    for columna, ancho in zip("ABCDEFGHIJK", (12, 6, 16, 28, 12, 34, 10, 8, 14, 8, 16)):
        hoja.column_dimensions[columna].width = ancho

    destino.parent.mkdir(parents=True, exist_ok=True)
    libro.save(destino)
    return destino


if __name__ == "__main__":
    salida = Path(sys.argv[1] if len(sys.argv) > 1 else "reporte-ejemplo.xlsx")
    print(f"Reporte de ejemplo escrito en: {generar(salida)}")
