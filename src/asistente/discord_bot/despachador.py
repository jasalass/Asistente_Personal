import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from asistente.agent.loop import ResultadoAgente
from asistente.discord_bot.util import Historial
from asistente.security.allowlist import Allowlist

Responder = Callable[[str, Sequence[dict[str, Any]]], Awaitable[ResultadoAgente]]


class Despachador:
    """Decide si un mensaje se atiende y lo pasa al agente. No conoce a discord.py.

    Todo lo que no venga del owner, en su servidor y en un canal permitido se ignora sin
    responder: ni siquiera se revela que el bot está escuchando.
    """

    def __init__(
        self, allowlist: Allowlist, responder: Responder, historial: Historial | None = None
    ) -> None:
        self._allowlist = allowlist
        self._responder = responder
        self._historial = historial or Historial()
        # Un mensaje a la vez: evita carreras en la base y ráfagas contra el límite de Groq.
        self._turno = asyncio.Lock()

    def atiende(
        self, *, user_id: int, guild_id: int | None, channel_id: int, es_bot: bool
    ) -> bool:
        return not es_bot and self._allowlist.is_allowed(
            user_id=user_id, guild_id=guild_id, channel_id=channel_id
        )

    async def manejar(
        self, *, user_id: int, guild_id: int | None, channel_id: int, es_bot: bool, texto: str
    ) -> ResultadoAgente | None:
        """Devuelve el resultado completo (no solo el texto): el llamador decide cómo mostrar,
        por ejemplo, las propuestas pendientes de aprobación (eso sí es cosa de discord.py)."""
        # Se vuelve a comprobar aquí aunque el bot ya lo haya hecho: defensa en profundidad.
        if not self.atiende(
            user_id=user_id, guild_id=guild_id, channel_id=channel_id, es_bot=es_bot
        ):
            return None
        texto = texto.strip()
        if not texto:
            return None

        async with self._turno:
            resultado = await self._responder(texto, self._historial.obtener(channel_id))
        # pasos == 0 significa pausa o LLM caído: no tiene sentido recordarlo como conversación.
        if resultado.pasos > 0:
            self._historial.agregar(channel_id, texto, resultado.respuesta)
        return resultado
