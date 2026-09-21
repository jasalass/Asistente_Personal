import asyncio
import logging
from collections.abc import Callable

import discord
from discord import app_commands

from asistente.db.connection import transaccion
from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.db.repos.sistema import EstadoSistemaRepo
from asistente.discord_bot.despachador import Despachador
from asistente.discord_bot.util import dividir_mensaje
from asistente.security.allowlist import Allowlist

log = logging.getLogger(__name__)

MSG_ERROR = "Ocurrió un error interno y no pude procesar tu mensaje. Quedó registrado."
MSG_NO_AUTORIZADO = "No autorizado."

# Un texto del LLM jamás debe poder mencionar a nadie (@everyone, roles, usuarios).
SIN_MENCIONES = discord.AllowedMentions.none()


class _Arbol(app_commands.CommandTree):
    """Los slash commands pasan por la misma allowlist que los mensajes."""

    def __init__(self, client: discord.Client, allowlist: Allowlist) -> None:
        super().__init__(client)
        self._allowlist = allowlist

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        ok = self._allowlist.is_allowed(
            user_id=interaction.user.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id or 0,
        )
        if not ok:
            await interaction.response.send_message(MSG_NO_AUTORIZADO, ephemeral=True)
        return ok


def crear_intents() -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    intents.message_content = True  # privilegiado: hay que activarlo en el portal de Discord
    return intents


class AsistenteBot(discord.Client):
    def __init__(self, allowlist: Allowlist, despachador: Despachador) -> None:
        super().__init__(intents=crear_intents(), allowed_mentions=SIN_MENCIONES)
        self._despachador = despachador
        self._guild = discord.Object(id=allowlist.guild_id)
        self.tree = _Arbol(self, allowlist)
        self._registrar_comandos()

    def _registrar_comandos(self) -> None:
        @self.tree.command(name="pausa", description="Pausa al asistente", guild=self._guild)
        async def pausa(interaction: discord.Interaction) -> None:
            await self._fijar_pausa(interaction, True)

        @self.tree.command(
            name="reanudar", description="Reactiva al asistente", guild=self._guild
        )
        async def reanudar(interaction: discord.Interaction) -> None:
            await self._fijar_pausa(interaction, False)

        @self.tree.command(name="estado", description="Estado del asistente", guild=self._guild)
        async def estado(interaction: discord.Interaction) -> None:
            pausado = await asyncio.to_thread(_leer_pausa)
            await interaction.response.send_message(
                "En pausa." if pausado else "Activo.", ephemeral=True
            )

    async def _fijar_pausa(self, interaction: discord.Interaction, valor: bool) -> None:
        await asyncio.to_thread(_escribir_pausa, valor)
        await interaction.response.send_message(
            "Asistente en pausa." if valor else "Asistente reactivado.", ephemeral=True
        )

    async def setup_hook(self) -> None:
        await self.tree.sync(guild=self._guild)

    async def on_ready(self) -> None:
        log.info("Conectado como %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        datos = {
            "user_id": message.author.id,
            "guild_id": message.guild.id if message.guild else None,
            "channel_id": message.channel.id,
            "es_bot": message.author.bot,
        }
        # Antes de cualquier señal visible (incluido "escribiendo…"): a los no autorizados,
        # el bot no les revela ni que está escuchando.
        if not self._despachador.atiende(**datos):
            return
        try:
            async with message.channel.typing():
                respuesta = await self._despachador.manejar(**datos, texto=message.content)
        except Exception:  # el bot no debe caerse por un mensaje
            log.exception("Error procesando un mensaje")
            respuesta = MSG_ERROR
        if respuesta is None:
            return

        trozos = dividir_mensaje(respuesta) or ["Listo."]
        await message.reply(trozos[0], mention_author=False)
        for trozo in trozos[1:]:
            await message.channel.send(trozo)


def _leer_pausa() -> bool:
    with transaccion() as conn:
        return EstadoSistemaRepo(conn).pausado()


def _escribir_pausa(valor: bool) -> None:
    with transaccion() as conn:
        EstadoSistemaRepo(conn).set_pausado(valor)
        AuditoriaRepo(conn).registrar("owner", "pausa" if valor else "reanudar")


def ejecutar(bot: AsistenteBot, token: str, *, al_iniciar: Callable[[], None] | None = None) -> None:
    if al_iniciar:
        al_iniciar()
    bot.run(token, log_handler=None)
