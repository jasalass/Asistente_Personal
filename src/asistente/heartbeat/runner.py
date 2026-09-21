import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import psycopg

from asistente.db.connection import Conn, transaccion
from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.db.repos.sistema import EstadoSistemaRepo
from asistente.heartbeat.checks import Aviso, recolectar

log = logging.getLogger(__name__)

Enviar = Callable[[str], Awaitable[None]]
AbrirConexion = Callable[[], AbstractContextManager[Conn]]


async def ciclo(
    enviar: Enviar,
    *,
    tz: ZoneInfo,
    ahora: datetime,
    abrir: AbrirConexion = transaccion,
    hora_inicio: int = 8,
    hora_fin: int = 21,
) -> int:
    """Un latido: recoge avisos, los envía y confirma cada uno solo si el envío salió bien.

    Devuelve cuántos avisos se enviaron. Si el envío falla, el aviso queda sin confirmar y se
    reintenta en el siguiente ciclo (puede llegar duplicado solo si el proceso muere justo entre
    el envío y la confirmación).
    """
    inicio = time.monotonic()

    def recoger() -> list[Aviso]:
        with abrir() as conn:
            if EstadoSistemaRepo(conn).pausado():
                return []
            return recolectar(conn, ahora, tz, hora_inicio=hora_inicio, hora_fin=hora_fin)

    def confirmar(aviso: Aviso) -> None:
        with abrir() as conn:
            aviso.confirmar(conn)
            AuditoriaRepo(conn).registrar(
                "heartbeat", "aviso", {"clave": aviso.clave, "texto": aviso.texto}
            )

    avisos = await asyncio.to_thread(recoger)
    enviados = 0
    for aviso in avisos:
        try:
            await enviar(aviso.texto)
        except Exception:  # un envío fallido no debe impedir los demás
            log.exception("No se pudo enviar el aviso %s", aviso.clave)
            continue
        await asyncio.to_thread(confirmar, aviso)
        enviados += 1

    if avisos:  # no se registra un latido vacío por minuto: serían 1440 filas diarias
        ms = int((time.monotonic() - inicio) * 1000)

        def registrar() -> None:
            with abrir() as conn:
                AuditoriaRepo(conn).registrar_ejecucion(
                    "heartbeat",
                    duracion_ms=ms,
                    detalle={"avisos": len(avisos), "enviados": enviados},
                    error=None if enviados == len(avisos) else "envío incompleto",
                )

        await asyncio.to_thread(registrar)
    return enviados


async def latido(
    enviar: Enviar,
    *,
    tz: ZoneInfo,
    intervalo_s: int,
    hora_inicio: int,
    hora_fin: int,
) -> None:
    """Bucle infinito. Ningún error, de la base o de Discord, lo termina."""
    log.info("Heartbeat activo (cada %s s)", intervalo_s)
    while True:
        try:
            await ciclo(
                enviar,
                tz=tz,
                ahora=datetime.now(UTC),
                hora_inicio=hora_inicio,
                hora_fin=hora_fin,
            )
        except psycopg.OperationalError:
            # Sin red o base inalcanzable: es transitorio, no un error del programa.
            log.warning("Heartbeat sin conexión a la base de datos: se reintenta en el próximo ciclo")
        except Exception:
            log.exception("Error en el ciclo del heartbeat")
        await asyncio.sleep(intervalo_s)
