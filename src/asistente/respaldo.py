"""Respaldo de los datos personales a un archivo JSON. Solo lee: nunca modifica la base.

Uso:  python -m asistente.respaldo   (escribe en ./respaldos/, que no se sube a git)

Se exportan las tablas con tus datos. Quedan fuera los registros de ejecución y las trazas, que
crecen mucho y se pueden regenerar. Para restaurar, el archivo sirve de referencia para volver a
insertar las filas en el SQL Editor de Supabase, en el orden de la lista TABLAS.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from asistente.db.connection import Conn, transaccion

# Constantes fijas (nunca vienen del usuario), así que pueden ir en el SQL. El orden importa para
# restaurar: primero lo que otras tablas referencian.
TABLAS = (
    "procesos",
    "proceso_eventos",
    "memorias",
    "recordatorios",
    "eventos_agenda",
    "excepciones_agenda",
    "temas_seguimiento",
    "articulos_vistos",
    "acciones_pendientes",
    "tool_niveles",
    "estado_sistema",
)


def exportar(conn: Conn) -> dict[str, Any]:
    tablas = {}
    for tabla in TABLAS:
        tablas[tabla] = conn.execute(f"select * from {tabla}").fetchall()
    return {"creado": datetime.now(UTC).isoformat(timespec="seconds"), "tablas": tablas}


def escribir(datos: dict[str, Any], carpeta: Path) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    marca = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    destino = carpeta / f"respaldo-{marca}.json"
    destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return destino


def main() -> None:
    with transaccion() as conn:
        datos = exportar(conn)
    destino = escribir(datos, Path(__file__).resolve().parents[2] / "respaldos")
    filas = sum(len(v) for v in datos["tablas"].values())
    print(f"Respaldo listo: {destino} ({filas} filas)")


if __name__ == "__main__":
    main()
