"""Cuánto consumió el asistente hoy, a partir de sus propios registros (tabla `ejecuciones`)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from asistente.db.connection import Conn
from asistente.db.repos.trazas import TrazaRepo

_ETIQUETAS = {"mensaje": ("Chat", "mensaje", "mensajes"), "vigia": ("Vigía", "corrida", "corridas")}


def _miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def texto_uso(conn: Conn, ahora: datetime, tz: ZoneInfo, limite_diario: int) -> str:
    desde = ahora.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    filas = {
        f["tipo"]: f
        for f in conn.execute(
            """
            select tipo, count(*) as n,
                   coalesce(sum(tokens_in), 0) + coalesce(sum(tokens_out), 0) as tokens
            from ejecuciones
            where inicio >= %s and tipo in ('mensaje', 'vigia')
            group by tipo
            """,
            (desde,),
        )
    }
    lineas = ["**Consumo de hoy** (según los registros del asistente):"]
    for tipo, (nombre, singular, plural) in _ETIQUETAS.items():
        f = filas.get(tipo)
        n, tokens = (f["n"], int(f["tokens"])) if f else (0, 0)
        pct = round(100 * tokens / limite_diario) if limite_diario else 0
        lineas.append(
            f"• {nombre}: {_miles(tokens)} tokens en {n} {singular if n == 1 else plural} "
            f"({pct} % de {_miles(limite_diario)})"
        )

    # Desde que hay más de un modelo en la cadena, "% de 200.000" mezclaba cupos separados: con
    # tres modelos de respaldo, 250K repartidos entre dos no es "125 % agotado", son dos cupos
    # distintos. Se desglosa por modelo cuando hay trazas (migración 0005); si no, se omite.
    trazas = TrazaRepo(conn)
    if trazas.disponible() and (por_modelo := trazas.consumo_por_modelo(desde)):
        lineas.append("  Por modelo (cada uno con su propio cupo):")
        for fila in por_modelo:
            tokens = int(fila["tokens"])
            pct = round(100 * tokens / limite_diario) if limite_diario else 0
            lineas.append(f"    - {fila['nombre']}: {_miles(tokens)} tokens ({pct} % de {_miles(limite_diario)})")

    lineas.append(
        "_El cupo de Groq se cuenta por modelo y por día. Esto no incluye pruebas manuales ni "
        "otros usos de la misma clave._"
    )
    return "\n".join(lineas)
