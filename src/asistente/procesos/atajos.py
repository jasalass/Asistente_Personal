"""Consultas de procesos que se responden sin el modelo, igual que agenda.atajos.

Solo frases completas y conocidas que mencionan "proceso(s)" o "trámite(s)" explícitamente —
así nunca compite con el atajo de agenda ("qué tengo hoy") ni con una orden real ("cancela el
proceso X"), que van al modelo. Equivocarse hacia el modelo es barato; hacia el atajo, no.
"""

import re
import unicodedata
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from asistente.db.models import Proceso, ProcesoEstado
from asistente.procesos.redaccion import formatear_lista

_ABIERTOS = (ProcesoEstado.IDEA, ProcesoEstado.ACTIVO, ProcesoEstado.EN_ESPERA, ProcesoEstado.BLOQUEADO)

_ESTADO_PALABRAS = {
    "idea": ProcesoEstado.IDEA,
    "ideas": ProcesoEstado.IDEA,
    "activo": ProcesoEstado.ACTIVO,
    "activos": ProcesoEstado.ACTIVO,
    "en espera": ProcesoEstado.EN_ESPERA,
    "bloqueado": ProcesoEstado.BLOQUEADO,
    "bloqueados": ProcesoEstado.BLOQUEADO,
    "completado": ProcesoEstado.COMPLETADO,
    "completados": ProcesoEstado.COMPLETADO,
    "cancelado": ProcesoEstado.CANCELADO,
    "cancelados": ProcesoEstado.CANCELADO,
}

# Para el mensaje "No tienes procesos ...": una forma fija, sin importar cómo lo haya escrito el
# usuario (singular o plural).
_ESTADO_PLURAL = {
    ProcesoEstado.IDEA: "en idea",
    ProcesoEstado.ACTIVO: "activos",
    ProcesoEstado.EN_ESPERA: "en espera",
    ProcesoEstado.BLOQUEADO: "bloqueados",
    ProcesoEstado.COMPLETADO: "completados",
    ProcesoEstado.CANCELADO: "cancelados",
}


@dataclass(frozen=True)
class ConsultaDeProcesos:
    estados: tuple[ProcesoEstado, ...]
    etiqueta: str  # para "no tienes procesos ..." y para registrar el atajo usado


def _plano(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto.casefold())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", texto)).strip()


_PREFIJO = r"(?:y |ok |a ver |dime |mostrame |muestrame |dame )?"
_PROCESO_O_TRAMITE = r"(?:proceso|tramite)s?"
_ESTADO = "|".join(sorted(_ESTADO_PALABRAS, key=len, reverse=True))

_PATRONES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(rf"^{_PREFIJO}como van mis {_PROCESO_O_TRAMITE}$"),
        "abiertos",
    ),
    (
        re.compile(
            rf"^{_PREFIJO}(?:mis {_PROCESO_O_TRAMITE}|que {_PROCESO_O_TRAMITE} tengo|"
            rf"resumen de {_PROCESO_O_TRAMITE}|lista de {_PROCESO_O_TRAMITE})$"
        ),
        "abiertos",
    ),
    (
        re.compile(rf"^{_PREFIJO}que {_PROCESO_O_TRAMITE} (?:estan |tengo )?({_ESTADO})$"),
        "por_estado",
    ),
    (
        re.compile(rf"^{_PREFIJO}que {_PROCESO_O_TRAMITE} ({_ESTADO}) tengo$"),
        "por_estado",
    ),
    (
        re.compile(rf"^{_PREFIJO}{_PROCESO_O_TRAMITE} ({_ESTADO})$"),
        "por_estado",
    ),
]


def detectar_consulta(mensaje: str) -> ConsultaDeProcesos | None:
    plano = _plano(mensaje)
    if not plano or len(plano) > 60:
        return None
    for patron, tipo in _PATRONES:
        m = patron.match(plano)
        if not m:
            continue
        if tipo == "abiertos":
            return ConsultaDeProcesos(_ABIERTOS, "abiertos")
        if tipo == "por_estado":
            estado = _ESTADO_PALABRAS[m.group(1)]
            return ConsultaDeProcesos((estado,), _ESTADO_PLURAL[estado])
    return None


def redactar(procesos: list[Proceso], consulta: ConsultaDeProcesos, tz: ZoneInfo) -> str:
    """El texto exacto que verá el usuario: lo guardado, sin paráfrasis."""
    if not procesos:
        return f"No tienes procesos {consulta.etiqueta}."
    return formatear_lista(procesos, tz)
