"""Convierte un Proceso en una frase, siempre igual, sin dejárselo al modelo.

Igual que `agenda.ocurrencias.describir`: lo que queda guardado se confirma tal cual, con los
mismos datos y el mismo orden cada vez. El modelo relee el campo `resumen` en vez de redactarlo
de memoria (y a veces olvidar `esperando_a`, o inventar una prioridad que no se dijo).
"""

from zoneinfo import ZoneInfo

from asistente.db.models import Prioridad, Proceso, ProcesoEstado

_ESTADO_TEXTO = {
    ProcesoEstado.IDEA: "idea",
    ProcesoEstado.ACTIVO: "activo",
    ProcesoEstado.EN_ESPERA: "en espera",
    ProcesoEstado.BLOQUEADO: "bloqueado",
    ProcesoEstado.COMPLETADO: "completado",
    ProcesoEstado.CANCELADO: "cancelado",
}


def describir_proceso(p: Proceso, tz: ZoneInfo) -> str:
    partes = [f"{p.nombre} — {_ESTADO_TEXTO[p.estado]}"]
    if p.estado is ProcesoEstado.EN_ESPERA and p.esperando_a:
        partes[-1] += f" (de {p.esperando_a})"
    if p.estado is ProcesoEstado.BLOQUEADO and p.bloqueo_detalle:
        partes[-1] += f": {p.bloqueo_detalle}"
    if p.proxima_accion:
        cuando = f" ({p.proxima_accion_fecha.astimezone(tz):%d/%m %H:%M})" if p.proxima_accion_fecha else ""
        partes.append(f"próximo: {p.proxima_accion}{cuando}")
    if p.fecha_limite:
        partes.append(f"fecha límite {p.fecha_limite:%d/%m}")
    if p.prioridad is not Prioridad.MEDIA:
        partes.append(f"prioridad {p.prioridad.value}")
    return ". ".join(partes) + "."


def formatear_lista(procesos: list[Proceso], tz: ZoneInfo) -> str:
    """Lo que ve el usuario: una línea por proceso, en el orden en que vino la lista."""
    if not procesos:
        return "No tienes procesos abiertos."
    return "\n".join(f"• {describir_proceso(p, tz)}" for p in procesos)
