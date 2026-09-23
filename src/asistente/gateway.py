"""Arranque del asistente: `python -m asistente`."""

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from asistente.agent.loop import ResultadoAgente
from asistente.agent.service import resolver_aprobacion, responder
from asistente.bloqueo import BloqueoInstancia
from asistente.config import Settings, get_settings
from asistente.db.connection import transaccion
from asistente.db.repos.acciones_pendientes import AccionesPendientesRepo
from asistente.db.repos.agenda import EventoRepo
from asistente.db.repos.tool_niveles import NivelesRepo
from asistente.discord_bot.bot import AsistenteBot, ejecutar
from asistente.discord_bot.despachador import Despachador
from asistente.heartbeat.runner import latido
from asistente.llm.combinadores import LLMConRespaldo
from asistente.llm.groq import GroqLLM
from asistente.security.allowlist import Allowlist
from asistente.uso import texto_uso
from asistente.vigia import runner as vigia_runner
from asistente.vigia.tavily import TavilyBuscador

log = logging.getLogger(__name__)


def cadena_de_modelos(principal: str, respaldos: Sequence[str]) -> list[str]:
    """El principal primero, luego cada respaldo una sola vez y en el orden dado.

    Cada modelo tiene su propio cupo diario (200K tokens) en el plan gratuito de Groq: si el
    principal se agota, el chat pasa al siguiente de la lista en vez de quedarse sin responder.
    Cada respaldo que se agrega es cupo extra, no solo tolerancia a fallos.
    """
    cadena = [principal]
    for modelo in respaldos:
        if modelo and modelo not in cadena:
            cadena.append(modelo)
    return cadena


def construir_bot(
    cfg: Settings, *, vigilar_bloqueo: Callable[[], bool | None] | None = None
) -> AsistenteBot:
    tz = ZoneInfo(cfg.timezone)
    clave_groq = cfg.groq_api_key.get_secret_value()
    cadena = cadena_de_modelos(cfg.modelo_agente, cfg.modelos_respaldo)
    llm = LLMConRespaldo(
        GroqLLM(clave_groq, cadena[0], reasoning_effort="low"),
        *(GroqLLM(clave_groq, modelo, reasoning_effort="low") for modelo in cadena[1:]),
    )

    async def responder_async(texto: str, historial: Sequence[dict[str, Any]]) -> ResultadoAgente:
        def trabajo() -> ResultadoAgente:
            # Una transacción por mensaje: si algo falla a mitad, no queda nada a medias.
            with transaccion() as conn:
                return responder(
                    conn, texto, llm=llm, tz=tz, ahora=datetime.now(UTC), historial=historial
                )

        return await asyncio.to_thread(trabajo)

    allowlist = Allowlist(
        owner_id=cfg.discord_owner_id,
        guild_id=cfg.discord_guild_id,
        channel_ids=cfg.discord_channel_ids,
    )

    latido_fn = None
    if cfg.discord_canal_avisos_id is None:
        log.warning("DISCORD_CANAL_AVISOS_ID no está definido: el heartbeat queda desactivado")
    else:

        def latido_fn(enviar):
            return latido(
                enviar,
                tz=tz,
                intervalo_s=cfg.heartbeat_intervalo_s,
                hora_inicio=cfg.aviso_hora_inicio,
                hora_fin=cfg.aviso_hora_fin,
            )

    vigia_fn = vigia_ahora_fn = None
    if cfg.discord_canal_vigia_id is None:
        log.warning("DISCORD_CANAL_VIGIA_ID no está definido: el vigía queda desactivado")
    elif not tablas_del_vigia_existen():
        log.warning(
            "Faltan las tablas del vigía: ejecuta supabase/migrations/0003_vigia.sql. "
            "El vigía queda desactivado."
        )
    else:
        buscador = TavilyBuscador(cfg.tavily_api_key.get_secret_value())
        llm_resumen = GroqLLM(
            cfg.groq_api_key.get_secret_value(), cfg.modelo_resumen, reasoning_effort="low"
        )

        def vigia_fn(publicar):
            return vigia_runner.vigia(
                publicar,
                buscador=buscador,
                llm=llm_resumen,
                tz=tz,
                intervalo_s=cfg.vigia_intervalo_s,
                max_busquedas_dia=cfg.vigia_max_busquedas_dia,
            )

        async def vigia_ahora_fn(publicar):
            hechos = await vigia_runner.ciclo(
                buscador=buscador,
                llm=llm_resumen,
                publicar=publicar,
                tz=tz,
                ahora=datetime.now(tz),
                max_busquedas_dia=cfg.vigia_max_busquedas_dia,
                max_por_ciclo=5,
                forzar=True,
            )
            return resumen_manual(hechos)

    async def uso_async() -> str:
        def leer() -> str:
            with transaccion() as conn:
                return texto_uso(conn, datetime.now(tz), tz, cfg.groq_limite_diario_tokens)

        return await asyncio.to_thread(leer)

    if not tabla_niveles_existe():
        log.warning(
            "Faltan las tablas de aprobaciones: ejecuta supabase/migrations/0006_aprobaciones.sql. "
            "Los botones Aprobar/Rechazar no van a poder resolverse mientras tanto."
        )

    async def resolver_aprobacion_async(accion_id: UUID, aprobar: bool, resuelto_por: int) -> str:
        def trabajo() -> str:
            with transaccion() as conn:
                return resolver_aprobacion(
                    conn, accion_id, aprobar=aprobar, resuelto_por=str(resuelto_por), tz=tz
                )

        return await asyncio.to_thread(trabajo)

    async def pendientes_al_arrancar_async() -> list[UUID]:
        def trabajo() -> list[UUID]:
            with transaccion() as conn:
                return AccionesPendientesRepo(conn).pendientes_ids()

        return await asyncio.to_thread(trabajo)

    return AsistenteBot(
        allowlist,
        Despachador(allowlist, responder_async),
        tz=tz,
        latido=latido_fn,
        canal_avisos_id=cfg.discord_canal_avisos_id,
        vigia=vigia_fn,
        vigia_ahora=vigia_ahora_fn,
        canal_vigia_id=cfg.discord_canal_vigia_id,
        uso=uso_async,
        vigilar_bloqueo=vigilar_bloqueo,
        resolver_aprobacion=resolver_aprobacion_async,
        pendientes_al_arrancar=pendientes_al_arrancar_async,
    )


def resumen_manual(hechos: list) -> str:
    if not hechos:
        return "No hay temas activos para revisar (o se alcanzó el límite diario de búsquedas)."
    lineas = []
    for tema, r in hechos:
        estado = f"{r.publicados} publicados de {r.nuevos} nuevos ({r.encontrados} encontrados)"
        lineas.append(f"• {tema.nombre}: {estado}" + (f" — error: {r.error}" if r.error else ""))
    return "\n".join(lineas)


def tablas_del_vigia_existen() -> bool:
    try:
        with transaccion() as conn:
            conn.execute("select 1 from temas_seguimiento limit 1")
            conn.execute("select 1 from articulos_vistos limit 1")
    except psycopg.errors.UndefinedTable:
        return False
    return True


def tabla_niveles_existe() -> bool:
    with transaccion() as conn:
        return NivelesRepo(conn).disponible()


def advertir_si_falta_la_agenda() -> None:
    with transaccion() as conn:
        if not EventoRepo(conn).disponible():
            log.warning(
                "Faltan las tablas de la agenda: ejecuta supabase/migrations/0004_agenda.sql. "
                "Mientras tanto no hay eventos recurrentes ni sus avisos."
            )


def verificar_base() -> None:
    """Falla al arrancar, y no al primer mensaje, si la base no está accesible."""
    with transaccion() as conn:
        conn.execute("select 1")
    advertir_si_falta_la_agenda()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = get_settings()

    # Solo una instancia activa: si ya hay otra (tu PC y un servidor), esta espera su turno.
    bloqueo = BloqueoInstancia(cfg.database_url.get_secret_value())
    bloqueo.esperar()
    try:
        bot = construir_bot(cfg, vigilar_bloqueo=bloqueo.conservar)
        ejecutar(bot, cfg.discord_token.get_secret_value(), al_iniciar=verificar_base)
        if bot.bloqueo_perdido:
            raise SystemExit(1)  # salida con error para que el supervisor lo reinicie
    finally:
        bloqueo.liberar()
