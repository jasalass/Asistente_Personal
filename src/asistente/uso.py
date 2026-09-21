"""Cuánto consumió el asistente hoy, a partir de sus propios registros (tabla `ejecuciones`)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from asistente.db.connection import Conn

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
    lineas.append(
        "_El cupo de Groq se cuenta por modelo y por día. Esto no incluye pruebas manuales ni "
        "otros usos de la misma clave._"
    )
    return "\n".join(lineas)
