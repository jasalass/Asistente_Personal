from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from asistente.agenda.feriados import CalendarioFeriados
from asistente.db.models import AccionExcepcion, Evento

Excepciones = Mapping[tuple[UUID, date], tuple[AccionExcepcion, str | None]]

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


@dataclass(frozen=True)
class Ocurrencia:
    evento: Evento
    fecha: date
    suspendida: bool = False
    motivo: str | None = None  # por qué se suspende: "feriado: X" o "omitida: ..."

    def inicio(self, tz: ZoneInfo) -> datetime:
        return datetime.combine(self.fecha, self.evento.hora, tzinfo=tz)


def ocurrencia(
    evento: Evento, fecha: date, excepciones: Excepciones, feriados: CalendarioFeriados
) -> Ocurrencia | None:
    """La ocurrencia de un evento en una fecha, o None si ese día no le corresponde.

    Una ocurrencia puede estar suspendida (feriado u omitida a mano): se devuelve igual, marcada,
    para que la agenda pueda decir "hoy no hay clases" en vez de callar.
    """
    if not evento.activo:
        return None
    if evento.vigente_desde and fecha < evento.vigente_desde:
        return None
    if evento.vigente_hasta and fecha > evento.vigente_hasta:
        return None
    if fecha.isoweekday() not in evento.dias_semana:
        return None

    accion, motivo = excepciones.get((evento.id, fecha), (None, None))
    if accion is AccionExcepcion.OMITIR:
        return Ocurrencia(evento, fecha, True, f"omitida: {motivo}" if motivo else "omitida")
    if accion is AccionExcepcion.MANTENER:
        return Ocurrencia(evento, fecha)  # el usuario confirmó que sí ocurre, aunque sea feriado
    if evento.suspender_feriados:
        feriado = feriados.nombre(fecha)
        if feriado:
            return Ocurrencia(evento, fecha, True, f"feriado: {feriado}")
    return Ocurrencia(evento, fecha)


def proximas(
    evento: Evento,
    desde: date,
    excepciones: Excepciones,
    feriados: CalendarioFeriados,
    *,
    cuantas: int = 4,
    max_dias: int = 120,
) -> list[Ocurrencia]:
    """Las próximas ocurrencias, incluidas las suspendidas, para que el usuario vea qué pasará."""
    resultado: list[Ocurrencia] = []
    for i in range(max_dias):
        o = ocurrencia(evento, desde + timedelta(days=i), excepciones, feriados)
        if o:
            resultado.append(o)
            if len(resultado) == cuantas:
                break
    return resultado


def nombre_dia(fecha: date) -> str:
    return DIAS[fecha.isoweekday() - 1]


def _lista_de_dias(dias: list[int]) -> str:
    nombres = [DIAS[d - 1] + ("s" if d >= 6 else "") for d in dias]  # los lunes... los sábados
    if len(nombres) == 1:
        return f"los {nombres[0]}"
    return "los " + ", ".join(nombres[:-1]) + " y " + nombres[-1]


def describir(evento: Evento) -> str:
    """Todo lo que quedó configurado, en una frase generada por el código.

    El modelo puede llenar parámetros opcionales que nadie le pidió (le pasó con una fecha de
    inicio) y no mencionarlos. Con este texto el usuario ve siempre lo que realmente se guardó.
    """
    partes = [f"{evento.nombre}: {_lista_de_dias(evento.dias_semana)} a las {evento.hora:%H:%M}"]
    if evento.duracion_min:
        partes.append(f"dura {evento.duracion_min} min")
    partes.append(
        f"aviso {evento.aviso_min_antes} min antes" if evento.aviso_min_antes else "sin aviso previo"
    )
    partes.append(
        "se suspende en feriados" if evento.suspender_feriados else "ocurre aunque sea feriado"
    )
    if evento.vigente_desde or evento.vigente_hasta:
        vigencia = []
        if evento.vigente_desde:
            vigencia.append(f"desde el {evento.vigente_desde:%d/%m/%Y}")
        if evento.vigente_hasta:
            vigencia.append(f"hasta el {evento.vigente_hasta:%d/%m/%Y}")
        partes.append("vigente " + " ".join(vigencia))
    else:
        partes.append("sin fecha de inicio ni de término")
    if evento.descripcion:
        partes.append(evento.descripcion)
    return "; ".join(partes)


def advertencia_de_vigencia(evento: Evento, hoy: date) -> str | None:
    """Avisa si la vigencia deja fuera lo que el usuario esperaría (p. ej. la clase de hoy)."""
    if evento.vigente_desde and evento.vigente_desde > hoy:
        return (
            f"OJO: solo aplica desde el {evento.vigente_desde:%d/%m/%Y}; hasta entonces no ocurre "
            "(hoy tampoco). Si el usuario no pidió esa fecha, quítala."
        )
    if evento.vigente_hasta and evento.vigente_hasta < hoy:
        return f"OJO: ya terminó el {evento.vigente_hasta:%d/%m/%Y}; no volverá a ocurrir."
    return None
