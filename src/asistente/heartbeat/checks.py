"""Qué merece un aviso ahora mismo. SQL y plantillas: sin LLM, sin costo y sin inyección posible."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from asistente.db.connection import Conn
from asistente.db.models import EventoTipo, Proceso
from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.db.repos.procesos import ProcesoRepo
from asistente.db.repos.recordatorios import RecordatorioRepo

# Un recordatorio que llega con más atraso que esto se avisa indicando para cuándo era.
_ATRASO_TOLERADO = timedelta(minutes=10)
# No se avisa de fechas que vencieron hace mucho: serían ruido de un proceso olvidado.
_MAX_VENCIDA = timedelta(days=7)


@dataclass(frozen=True)
class Aviso:
    clave: str  # identifica el aviso; queda en auditoría al enviarse
    texto: str
    confirmar: Callable[[Conn], None]  # se ejecuta SOLO después de que Discord confirmó el envío


def _sin_accion(_: Conn) -> None:
    return None


def recolectar(
    conn: Conn, ahora: datetime, tz: ZoneInfo, *, hora_inicio: int = 8, hora_fin: int = 21
) -> list[Aviso]:
    """Avisos pendientes. Los recordatorios salen a su hora; el resto solo en horario diurno."""
    avisos = _recordatorios(conn, ahora, tz)
    if hora_inicio <= ahora.astimezone(tz).hour < hora_fin:
        avisos += _proxima_accion(conn, ahora, tz)
        avisos += _fecha_limite(conn, ahora, tz)
        avisos += _chequeos(conn, ahora)
    return avisos


def _recordatorios(conn: Conn, ahora: datetime, tz: ZoneInfo) -> list[Aviso]:
    avisos = []
    for r in RecordatorioRepo(conn).vencidos(ahora):
        texto = f"**Recordatorio:** {r.texto}"
        if ahora - r.fecha > _ATRASO_TOLERADO:
            texto += f" (era para el {r.fecha.astimezone(tz):%d/%m a las %H:%M})"
        avisos.append(
            Aviso(
                clave=f"rec:{r.id}",
                texto=texto,
                confirmar=lambda c, rid=r.id: RecordatorioRepo(c).marcar_enviado(rid),
            )
        )
    return avisos


def _proxima_accion(conn: Conn, ahora: datetime, tz: ZoneInfo) -> list[Aviso]:
    auditoria = AuditoriaRepo(conn)
    avisos = []
    for p in ProcesoRepo(conn).con_proxima_accion_entre(
        ahora - _MAX_VENCIDA, ahora + timedelta(hours=24)
    ):
        fecha = p.proxima_accion_fecha
        if ahora >= fecha:
            tramo, cuando = "vencida", "ya pasó"
        elif ahora >= fecha - timedelta(hours=2):
            tramo, cuando = "2h", "es en menos de 2 horas"
        else:
            tramo, cuando = "24h", "es en menos de 24 horas"
        clave = f"pa:{p.id}:{fecha.isoformat()}:{tramo}"
        if auditoria.aviso_ya_enviado(clave):
            continue
        accion = p.proxima_accion or "la próxima acción"
        sufijo = " ¿La actualizo o la doy por hecha?" if tramo == "vencida" else ""
        avisos.append(
            Aviso(
                clave=clave,
                texto=(
                    f"**{p.nombre}:** {accion} ({fecha.astimezone(tz):%d/%m a las %H:%M}) "
                    f"{cuando}.{sufijo}"
                ),
                confirmar=_sin_accion,
            )
        )
    return avisos


def _fecha_limite(conn: Conn, ahora: datetime, tz: ZoneInfo) -> list[Aviso]:
    auditoria = AuditoriaRepo(conn)
    hoy: date = ahora.astimezone(tz).date()
    avisos = []
    for p in ProcesoRepo(conn).con_fecha_limite_entre(
        hoy - _MAX_VENCIDA, hoy + timedelta(days=3)
    ):
        dias = (p.fecha_limite - hoy).days
        if dias < 0:
            tramo, cuando = "vencida", f"venció hace {-dias} día{'s' if dias < -1 else ''}"
        elif dias == 0:
            tramo, cuando = "hoy", "es hoy"
        elif dias == 1:
            tramo, cuando = "1d", "es mañana"
        else:
            tramo, cuando = "3d", f"es en {dias} días"
        clave = f"fl:{p.id}:{p.fecha_limite.isoformat()}:{tramo}"
        if auditoria.aviso_ya_enviado(clave):
            continue
        avisos.append(
            Aviso(
                clave=clave,
                texto=f"**{p.nombre}:** la fecha límite ({p.fecha_limite:%d/%m}) {cuando}.",
                confirmar=_sin_accion,
            )
        )
    return avisos


def _chequeos(conn: Conn, ahora: datetime) -> list[Aviso]:
    avisos = []
    for p in ProcesoRepo(conn).pendientes_de_chequeo(ahora):
        avisos.append(
            Aviso(
                clave=f"chk:{p.id}:{ahora.isoformat()}",
                texto=_texto_chequeo(p, ahora),
                confirmar=lambda c, pid=p.id: _marcar_chequeo(c, pid, ahora),
            )
        )
    return avisos


def _texto_chequeo(p: Proceso, ahora: datetime) -> str:
    # No se usa actualizado_en: el propio chequeo lo modifica y reiniciaría el conteo.
    desde = p.ultimo_chequeo or p.creado_en
    referencia = "el último chequeo" if p.ultimo_chequeo else "que lo registraste"
    detalle = [f"estado: {p.estado.value}"]
    if p.esperando_a:
        detalle.append(f"esperando a: {p.esperando_a}")
    if p.proxima_accion:
        detalle.append(f"próxima acción: {p.proxima_accion}")
    return (
        f"**Chequeo de {p.nombre}** ({'; '.join(detalle)}). "
        f"Han pasado {(ahora - desde).days} días desde {referencia}. ¿Alguna novedad?"
    )


def _marcar_chequeo(conn: Conn, proceso_id, ahora: datetime) -> None:
    repo = ProcesoRepo(conn)
    repo.marcar_chequeado(proceso_id, ahora)
    repo.agregar_evento(proceso_id, EventoTipo.CHEQUEO_AGENTE, "Aviso de chequeo enviado")
