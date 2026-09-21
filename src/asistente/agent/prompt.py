from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# workspace/ es de solo lectura para el agente: ninguna tool escribe ahí.
WORKSPACE = Path(__file__).resolve().parents[3] / "workspace"

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def construir_prompt(ahora: datetime, tz: ZoneInfo, workspace: Path = WORKSPACE) -> str:
    local = ahora.astimezone(tz)
    partes = [
        (workspace / "SOUL.md").read_text(encoding="utf-8"),
        (workspace / "AGENTS.md").read_text(encoding="utf-8"),
        *(p.read_text(encoding="utf-8") for p in sorted((workspace / "skills").glob("*.md"))),
        (
            f"# Contexto actual\nHoy es {_DIAS[local.weekday()]} {local:%Y-%m-%d}, "
            f"son las {local:%H:%M} (zona horaria {tz.key})."
        ),
    ]
    return "\n\n".join(partes)
