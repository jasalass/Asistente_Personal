"""La agenda de un rango de días: eventos, recordatorios y procesos con fecha, ordenados por hora."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from asistente.agenda.feriados import CalendarioFeriados
from asistente.agenda.ocurrencias import nombre_dia, ocurrencia
from asistente.db.connection import Conn
from asistente.db.repos.agenda import EventoRepo
from asistente.db.repos.procesos import ProcesoRepo
from asistente.db.repos.recordatorios import RecordatorioRepo

MAX_DIAS = 14


@dataclass(frozen=True)
class ItemAgenda:
    hora: time | None  # None = todo el día
    tipo: str  # evento | recordatorio | proceso | fecha_limite
    texto: str
    estado: str = "pendiente"  # pendiente | suspendido | avisado


@dataclass
class DiaAgenda:
    fecha: date
    feriado: str | None
    items: list[ItemAgenda] = field(default_factory=list)


def consultar(
    conn: Conn, desde: date, dias: int, tz: ZoneInfo, feriados: CalendarioFeriados
) -> list[DiaAgenda]:
    dias = max(1, min(dias, MAX_DIAS))
    hasta = desde + timedelta(days=dias - 1)
    ini = datetime.combine(desde, time.min, tzinfo=tz)
    fin = datetime.combine(hasta + timedelta(days=1), time.min, tzinfo=tz)

    repo_eventos = EventoRepo(conn)
    # Sin la migración de la agenda igual se muestran recordatorios y procesos con fecha.
    hay_eventos = repo_eventos.disponible()
    eventos = repo_eventos.listar(solo_activos=True) if hay_eventos else []
    excepciones = repo_eventos.excepciones(desde, hasta) if hay_eventos else {}
    recordatorios = RecordatorioRepo(conn).entre(ini, fin)
    procesos = ProcesoRepo(conn)
    proximas_acciones = procesos.con_proxima_accion_entre(ini, fin - timedelta(microseconds=1))
    fechas_limite = procesos.con_fecha_limite_entre(desde, hasta)

    resultado: list[DiaAgenda] = []
    for i in range(dias):
        dia = DiaAgenda(desde + timedelta(days=i), None)
        dia.feriado = feriados.nombre(dia.fecha)

        for evento in eventos:
            o = ocurrencia(evento, dia.fecha, excepciones, feriados)
            if o is None:
                continue
            texto = f"{evento.hora:%H:%M} {evento.nombre}"
            if evento.duracion_min:
                texto += f" ({evento.duracion_min} min)"
            if evento.descripcion:
                texto += f" — {evento.descripcion}"
            if o.suspendida:
                texto += f" — SUSPENDIDO ({o.motivo})"
            dia.items.append(
                ItemAgenda(evento.hora, "evento", texto, "suspendido" if o.suspendida else "pendiente")
            )

        for r in recordatorios:
            local = r.fecha.astimezone(tz)
            if local.date() == dia.fecha:
                texto = f"{local:%H:%M} Recordatorio: {r.texto}"
                dia.items.append(
                    ItemAgenda(
                        local.time(), "recordatorio",
                        texto + (" (ya avisado)" if r.enviado else ""),
                        "avisado" if r.enviado else "pendiente",
                    )
                )

        for p in proximas_acciones:
            local = p.proxima_accion_fecha.astimezone(tz)
            if local.date() == dia.fecha:
                accion = p.proxima_accion or "próxima acción"
                dia.items.append(
                    ItemAgenda(local.time(), "proceso", f"{local:%H:%M} {p.nombre}: {accion}")
                )

        for p in fechas_limite:
            if p.fecha_limite == dia.fecha:
                dia.items.append(ItemAgenda(None, "fecha_limite", f"Fecha límite de {p.nombre}"))

        # Lo que tiene hora va primero, en orden; lo de todo el día al final.
        dia.items.sort(key=lambda it: (it.hora is None, it.hora or time.min))
        resultado.append(dia)
    return resultado


def formatear(dias: list[DiaAgenda]) -> dict[str, list[str]]:
    """Forma compacta para el modelo: {'lunes 21/09': ['20:30 Clase...', ...]}. Vacío = nada agendado."""
    salida: dict[str, list[str]] = {}
    for d in dias:
        titulo = f"{nombre_dia(d.fecha)} {d.fecha:%d/%m}"
        if d.feriado:
            titulo += f" (feriado: {d.feriado})"
        salida[titulo] = [it.texto for it in d.items]
    return salida
