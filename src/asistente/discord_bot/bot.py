import asyncio
import contextlib
import logging
import socket
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands

from asistente.db.connection import transaccion
from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.db.repos.sistema import EstadoSistemaRepo
from asistente.discord_bot.despachador import Despachador
from asistente.discord_bot.util import dividir_mensaje
from asistente.security.allowlist import Allowlist
from asistente.vigia.runner import Publicacion, Publicar

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


Enviar = Callable[[str], Awaitable[None]]
Latido = Callable[[Enviar], Awaitable[None]]
Vigia = Callable[[Publicar], Awaitable[None]]
VigiaAhora = Callable[[Publicar], Awaitable[str]]


def crear_embed(pub: Publicacion) -> discord.Embed:
    """Título + resumen + link a la fuente. Los límites de Discord se respetan recortando."""
    embed = discord.Embed(title=pub.titulo[:256], description=pub.descripcion[:4000], url=pub.url)
    if pub.pie:
        embed.set_footer(text=pub.pie[:2000])
    return embed


class AsistenteBot(discord.Client):
    def __init__(
        self,
        allowlist: Allowlist,
        despachador: Despachador,
        *,
        latido: Latido | None = None,
        canal_avisos_id: int | None = None,
        vigia: Vigia | None = None,
        vigia_ahora: VigiaAhora | None = None,
        canal_vigia_id: int | None = None,
        uso: Callable[[], Awaitable[str]] | None = None,
        vigilar_bloqueo: Callable[[], bool] | None = None,
        host: str | None = None,
    ) -> None:
        super().__init__(intents=crear_intents(), allowed_mentions=SIN_MENCIONES)
        self._despachador = despachador
        self._guild = discord.Object(id=allowlist.guild_id)
        self._latido = latido
        self._canal_avisos_id = canal_avisos_id
        self._vigia = vigia
        self._vigia_ahora = vigia_ahora
        self._canal_vigia_id = canal_vigia_id
        self._uso = uso
        self._vigilar_bloqueo = vigilar_bloqueo
        self._host = host or socket.gethostname()
        self._avisado_inicio = False
        self._avisado_cierre = False
        self.bloqueo_perdido = False  # el proceso debe terminar con error para que lo reinicien
        self._tareas: list[asyncio.Task[None]] = []
        self.tree = _Arbol(self, allowlist)
        self._registrar_comandos()

    async def _canal(self, canal_id: int | None) -> discord.abc.Messageable:
        if canal_id is None:
            raise RuntimeError("Canal no configurado")
        return self.get_channel(canal_id) or await self.fetch_channel(canal_id)

    async def enviar_aviso(self, texto: str) -> None:
        """Publica en el canal de avisos, sin menciones y respetando el límite de Discord."""
        canal = await self._canal(self._canal_avisos_id)
        for trozo in dividir_mensaje(texto):
            await canal.send(trozo)

    async def publicar_vigia(self, pub: Publicacion) -> None:
        """Publica un artículo del vigía como embed en el canal de temas."""
        await (await self._canal(self._canal_vigia_id)).send(embed=crear_embed(pub))

    async def _correr(self, tarea: Callable[[], Awaitable[None]]) -> None:
        await self.wait_until_ready()
        await tarea()

    async def _avisar(self, texto: str) -> None:
        """Aviso de estado en el canal de avisos. Nunca lanza: es informativo."""
        if self._canal_avisos_id is None:
            return
        try:
            await self.enviar_aviso(texto)
        except Exception:
            log.warning("No se pudo publicar el aviso de estado", exc_info=True)

    async def _vigilar(self, intervalo_s: float = 60.0) -> None:
        """Si se pierde el bloqueo de instancia única, se detiene: mejor caer que duplicar."""
        while True:
            await asyncio.sleep(intervalo_s)
            if not await asyncio.to_thread(self._vigilar_bloqueo):
                log.critical("Se perdió el bloqueo de instancia única: el proceso se detiene")
                self.bloqueo_perdido = True
                await self.close()
                return

    async def close(self) -> None:
        if self._avisado_inicio and not self._avisado_cierre:
            self._avisado_cierre = True
            motivo = "otra instancia tomó el control" if self.bloqueo_perdido else "apagado normal"
            with contextlib.suppress(Exception):  # incluye el tiempo de espera agotado
                await asyncio.wait_for(
                    self._avisar(f"Asistente detenido en {self._host} ({motivo})."), timeout=5
                )
        actual = asyncio.current_task()
        for t in self._tareas:
            if t is not actual:  # el vigilante se llama a sí mismo: no puede cancelarse a medias
                t.cancel()
        await super().close()

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

        @self.tree.command(
            name="uso", description="Tokens consumidos hoy por el asistente", guild=self._guild
        )
        async def uso(interaction: discord.Interaction) -> None:
            if self._uso is None:
                await interaction.response.send_message("No disponible.", ephemeral=True)
                return
            await interaction.response.send_message(await self._uso(), ephemeral=True)

        @self.tree.command(
            name="vigia",
            description="Revisa ahora todos los temas activos (sin esperar su horario)",
            guild=self._guild,
        )
        async def vigia(interaction: discord.Interaction) -> None:
            if self._vigia_ahora is None:
                await interaction.response.send_message(
                    "El vigía no está configurado (falta DISCORD_CANAL_VIGIA_ID).", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                resumen = await self._vigia_ahora(self.publicar_vigia)
            except Exception:
                log.exception("Error en /vigia")
                resumen = "Falló la revisión. Quedó registrado."
            await interaction.followup.send(resumen, ephemeral=True)

    async def _fijar_pausa(self, interaction: discord.Interaction, valor: bool) -> None:
        await asyncio.to_thread(_escribir_pausa, valor)
        await interaction.response.send_message(
            "Asistente en pausa." if valor else "Asistente reactivado.", ephemeral=True
        )

    async def setup_hook(self) -> None:
        await self.tree.sync(guild=self._guild)
        if self._latido is not None:
            self._tareas.append(
                asyncio.create_task(self._correr(lambda: self._latido(self.enviar_aviso)))
            )
        if self._vigia is not None:
            self._tareas.append(
                asyncio.create_task(self._correr(lambda: self._vigia(self.publicar_vigia)))
            )
        if self._vigilar_bloqueo is not None:
            self._tareas.append(asyncio.create_task(self._vigilar()))

    async def on_ready(self) -> None:
        log.info("Conectado como %s", self.user)
        if not self._avisado_inicio:  # on_ready también se dispara en cada reconexión
            self._avisado_inicio = True
            await self._avisar(f"Asistente en línea en {self._host}.")

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
