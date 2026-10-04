"""Brief matutino: un mensaje al día con lo que hay que mirar hoy. Sin LLM: plantilla con lo guardado.

Reutiliza la agenda del día, los procesos con próxima acción vencida u hoy, y las fechas límite de
los próximos tres días. Sale una vez por día, a partir de la hora configurada, y solo de día.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from asistente.agenda.atajos import ConsultaDeAgenda, redactar
from asistente.agenda.consulta import consultar
from asistente.agenda.feriados import CalendarioFeriados
from asistente.agenda.ocurrencias import nombre_dia
from asistente.db.connection import Conn
from asistente.db.repos.procesos import ProcesoRepo
from asistente.procesos.redaccion import describir_proceso

# Un proceso con próxima acción vencida hace más de esto no se incluye: sería ruido de algo olvidado.
_MAX_VENCIDA = timedelta(days=7)
_DIAS_FECHA_LIMITE = 3


def clave_brief(hoy: date) -> str:
    return f"brief:{hoy.isoformat()}"


def construir_brief(
    conn: Conn, ahora: datetime, tz: ZoneInfo, feriados: CalendarioFeriados
) -> str:
    hoy = ahora.astimezone(tz).date()
    partes = [f"**Brief de {nombre_dia(hoy)} {hoy:%d/%m}**"]

    if hoy.isoweekday() == 1:
        # Los lunes, la semana completa: es el momento de planear, no solo de ver el día.
        dias = consultar(conn, hoy, 7, tz, feriados)
        partes.append("**Agenda de la semana**\n" + redactar(dias, ConsultaDeAgenda(hoy, 7, "esta semana"), hoy))
    else:
        dias = consultar(conn, hoy, 1, tz, feriados)
        partes.append("**Agenda de hoy**\n" + redactar(dias, ConsultaDeAgenda(hoy, 1, "hoy"), hoy))

    repo = ProcesoRepo(conn)
    fin_de_hoy = datetime.combine(hoy + timedelta(days=1), time.min, tzinfo=tz)
    accionables = repo.con_proxima_accion_entre(
        ahora - _MAX_VENCIDA, fin_de_hoy - timedelta(microseconds=1)
    )
    if accionables:
        lineas = "\n".join(f"• {describir_proceso(p, tz)}" for p in accionables)
        partes.append("**Procesos para hoy o vencidos**\n" + lineas)

    limites = repo.con_fecha_limite_entre(hoy, hoy + timedelta(days=_DIAS_FECHA_LIMITE))
    if limites:
        lineas = "\n".join(
            f"• {p.nombre}: {p.fecha_limite:%d/%m} ({nombre_dia(p.fecha_limite)})" for p in limites
        )
        partes.append("**Fechas límite próximas**\n" + lineas)

    return "\n\n".join(partes)
