"""Arranque del asistente: `python -m asistente`."""

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from asistente.agent.loop import ResultadoAgente
from asistente.agent.service import responder
from asistente.config import Settings, get_settings
from asistente.db.connection import transaccion
from asistente.discord_bot.bot import AsistenteBot, ejecutar
from asistente.discord_bot.despachador import Despachador
from asistente.heartbeat.runner import latido
from asistente.llm.groq import GroqLLM
from asistente.security.allowlist import Allowlist

log = logging.getLogger(__name__)


def construir_bot(cfg: Settings) -> AsistenteBot:
    tz = ZoneInfo(cfg.timezone)
    llm = GroqLLM(cfg.groq_api_key.get_secret_value(), cfg.modelo_agente, reasoning_effort="low")

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

    return AsistenteBot(
        allowlist,
        Despachador(allowlist, responder_async),
        latido=latido_fn,
        canal_avisos_id=cfg.discord_canal_avisos_id,
    )


def verificar_base() -> None:
    """Falla al arrancar, y no al primer mensaje, si la base no está accesible."""
    with transaccion() as conn:
        conn.execute("select 1")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = get_settings()
    ejecutar(
        construir_bot(cfg), cfg.discord_token.get_secret_value(), al_iniciar=verificar_base
    )
