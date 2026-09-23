from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from asistente.agenda.ocurrencias import DIAS

# workspace/ es de solo lectura para el agente: ninguna tool escribe ahí.
WORKSPACE = Path(__file__).resolve().parents[3] / "workspace"


def _proximas_fechas_por_dia(hoy: datetime) -> str:
    """"El jueves" -> qué fecha calendario es, ya calculada.

    Probamos los tres modelos de Groq con el mismo pedido ("el jueves tengo dentista") y ninguno
    acertó la fecha dejándolo a su propia aritmética: uno se fue una semana entera, otro dio un
    día que ni siquiera caía jueves. Es más barato calcularlo en código que confiar en que el
    modelo cuente días bien.
    """
    fecha = hoy.date()
    entradas = []
    for i, nombre in enumerate(DIAS):
        desfase = (i - hoy.weekday()) % 7  # 0 si hoy mismo es ese día
        entradas.append(f"{nombre} {(fecha + timedelta(days=desfase)):%Y-%m-%d}")
    return ", ".join(entradas)


def construir_prompt(ahora: datetime, tz: ZoneInfo, workspace: Path = WORKSPACE) -> str:
    local = ahora.astimezone(tz)
    partes = [
        (workspace / "SOUL.md").read_text(encoding="utf-8"),
        (workspace / "AGENTS.md").read_text(encoding="utf-8"),
        *(p.read_text(encoding="utf-8") for p in sorted((workspace / "skills").glob("*.md"))),
        (
            f"# Contexto actual\nHoy es {DIAS[local.weekday()]} {local:%Y-%m-%d}, "
            f"son las {local:%H:%M} (zona horaria {tz.key}).\n"
            f"Próxima fecha de cada día de la semana (hoy incluido si coincide): "
            f"{_proximas_fechas_por_dia(local)}. Para 'el jueves', 'el lunes que viene', etc., "
            f"usa la fecha de esta lista tal cual: no la calcules de memoria."
        ),
    ]
    return "\n\n".join(partes)
