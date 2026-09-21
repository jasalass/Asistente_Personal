"""Consultas de agenda que se responden sin el modelo.

"Qué tengo mañana" no necesita razonar: se sabe qué herramienta usar y cómo mostrar el resultado.
Resolverlo desde el código es instantáneo, no gasta tokens (el límite por minuto de Groq es de solo
8.000) y no puede alterar los datos: el modelo llegó a escribir "Caturrufo" donde la base decía
"CUTURRUFO" y a omitir apellidos.

Solo se reconocen frases completas y conocidas; ante cualquier otra cosa (incluidas las peticiones
de cambiar algo) decide el modelo. Equivocarse hacia el modelo es barato; hacia el atajo, no.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from asistente.agenda.consulta import DiaAgenda
from asistente.agenda.ocurrencias import DIAS, nombre_dia


@dataclass(frozen=True)
class ConsultaDeAgenda:
    desde: date
    dias: int
    etiqueta: str  # "hoy", "mañana", "esta semana"... para el mensaje de "nada agendado"


def _plano(texto: str) -> str:
    """Minúsculas, sin tildes ni signos, espacios simples: 'Qué tengo mañana?' -> 'que tengo manana'."""
    texto = unicodedata.normalize("NFD", texto.casefold())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", texto)).strip()


_PREFIJO = r"(?:y |ok |a ver |dime |mostrame |muestrame |dame )?"
_QUE_TENGO = r"(?:(?:que|q) tengo |mi agenda |agenda |que hay |que hago )"
_PARA = r"(?:para |por |en |de )?"
_DIA_SEMANA = "|".join(_plano(d) for d in DIAS)

_PATRONES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"^{_PREFIJO}{_QUE_TENGO}{_PARA}pasado manana$|^{_PREFIJO}pasado manana$"), "pasado_manana"),
    (re.compile(rf"^{_PREFIJO}{_QUE_TENGO}{_PARA}manana$|^{_PREFIJO}manana$"), "manana"),
    (re.compile(rf"^{_PREFIJO}{_QUE_TENGO}{_PARA}hoy(?: dia)?$|^{_PREFIJO}hoy$"), "hoy"),
    (re.compile(rf"^{_PREFIJO}{_QUE_TENGO}{_PARA}(?:esta|la) semana$"), "semana"),
    (re.compile(rf"^{_PREFIJO}{_QUE_TENGO}{_PARA}la proxima semana$"), "proxima_semana"),
    (re.compile(rf"^{_PREFIJO}{_QUE_TENGO}{_PARA}(?:el |este )?({_DIA_SEMANA})$"), "dia_semana"),
    (re.compile(rf"^{_PREFIJO}{_QUE_TENGO}{_PARA}los proximos (\d{{1,2}}) dias$"), "proximos"),
]


def detectar_consulta(mensaje: str, hoy: date) -> ConsultaDeAgenda | None:
    plano = _plano(mensaje)
    if not plano or len(plano) > 60:
        return None
    for patron, tipo in _PATRONES:
        m = patron.match(plano)
        if not m:
            continue
        if tipo == "hoy":
            return ConsultaDeAgenda(hoy, 1, "hoy")
        if tipo == "manana":
            return ConsultaDeAgenda(hoy + timedelta(days=1), 1, "mañana")
        if tipo == "pasado_manana":
            return ConsultaDeAgenda(hoy + timedelta(days=2), 1, "pasado mañana")
        if tipo == "semana":
            return ConsultaDeAgenda(hoy, 7, "esta semana")
        if tipo == "proxima_semana":
            lunes = hoy + timedelta(days=7 - hoy.weekday())  # el lunes siguiente
            return ConsultaDeAgenda(lunes, 7, "la próxima semana")
        if tipo == "dia_semana":
            objetivo = [_plano(d) for d in DIAS].index(m.group(1))
            falta = (objetivo - hoy.weekday()) % 7  # hoy mismo si coincide
            return ConsultaDeAgenda(hoy + timedelta(days=falta), 1, f"el {DIAS[objetivo]}")
        if tipo == "proximos":
            n = int(m.group(1))
            return ConsultaDeAgenda(hoy, n, f"los próximos {n} días") if 1 <= n <= 14 else None
    return None


def _titulo(dia: DiaAgenda, hoy: date) -> str:
    relativo = {0: "Hoy", 1: "Mañana", 2: "Pasado mañana"}.get((dia.fecha - hoy).days)
    base = f"{nombre_dia(dia.fecha)} {dia.fecha:%d/%m}"
    titulo = f"{relativo}, {base}" if relativo else base[:1].upper() + base[1:]
    if dia.feriado:
        titulo += f" (feriado: {dia.feriado})"
    return f"**{titulo}**"


def redactar(dias: list[DiaAgenda], consulta: ConsultaDeAgenda, hoy: date) -> str:
    """El texto exacto que verá el usuario: lo guardado, sin paráfrasis."""
    con_algo = [d for d in dias if d.items]
    if not con_algo:
        if len(dias) == 1 and dias[0].feriado:
            return f"No tienes nada agendado para {consulta.etiqueta} (feriado: {dias[0].feriado})."
        return f"No tienes nada agendado para {consulta.etiqueta}."
    bloques = []
    for d in con_algo:
        lineas = "\n".join(f"• {it.texto}" for it in d.items)
        bloques.append(f"{_titulo(d, hoy)}\n{lineas}")
    return "\n\n".join(bloques)
