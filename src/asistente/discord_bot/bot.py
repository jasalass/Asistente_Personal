import asyncio
import contextlib
import logging
import socket
from collections.abc import Awaitable, Callable
from uuid import UUID
from zoneinfo import ZoneInfo

import discord
import psycopg
from discord import app_commands

from asistente.db.connection import transaccion
from asistente.db.models import AccionPendiente
from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.db.repos.sistema import EstadoSistemaRepo
from asistente.discord_bot.despachador import Despachador
from asistente.discord_bot.util import dividir_mensaje
from asistente.security.allowlist import Allowlist
from asistente.vigia.runner import Publicacion, Publicar

log = logging.getLogger(__name__)

MSG_ERROR = "Ocurrió un error interno y no pude procesar tu mensaje. Quedó registrado."
MSG_SIN_CONEXION = "Ahora mismo no tengo conexión con mi base de datos. Reintenta en un minuto."
MSG_NO_AUTORIZADO = "No autorizado."
MSG_ERROR_APROBACION = "Ocurrió un error interno al resolver esto. Quedó registrado."

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
# (id de la acción, aprobar, id de Discord de quien decide) -> texto de confirmación. Nunca lanza.
ResolverAprobacion = Callable[[UUID, bool, int], Awaitable[str]]
PendientesAlArrancar = Callable[[], Awaitable[list[UUID]]]


def crear_embed(pub: Publicacion) -> discord.Embed:
    """Título + resumen + link a la fuente. Los límites de Discord se respetan recortando."""
    embed = discord.Embed(title=pub.titulo[:256], description=pub.descripcion[:4000], url=pub.url)
    if pub.pie:
        embed.set_footer(text=pub.pie[:2000])
    return embed


def crear_embed_propuesta(accion: AccionPendiente, tz: ZoneInfo) -> discord.Embed:
    """Qué tool, con qué argumentos y hasta cuándo se puede aprobar."""
    detalle = "\n".join(f"**{k}**: {v}" for k, v in accion.args.items()) or "(sin argumentos)"
    embed = discord.Embed(
        title=f"¿Aprobar «{accion.tool}»?", description=detalle[:4000], color=discord.Color.orange()
    )
    embed.set_footer(text=f"Expira el {accion.expira_en.astimezone(tz):%d/%m a las %H:%M}")
    return embed


class VistaAprobacion(discord.ui.View):
    """Aprobar/Rechazar, con el id de la acción en el custom_id (no en la instancia): así sigue
    funcionando después de un reinicio, mientras el bot vuelva a registrar las que quedaron
    pendientes en setup_hook. Solo el owner puede pulsarlos, igual que los slash commands."""

    def __init__(self, accion_id: UUID, allowlist: Allowlist, resolver: ResolverAprobacion) -> None:
        super().__init__(timeout=None)
        self._allowlist = allowlist
        self._resolver = resolver
        self._accion_id = accion_id

        aprobar = discord.ui.Button(
            label="Aprobar", style=discord.ButtonStyle.success,
            custom_id=f"aprobacion:aprobar:{accion_id}",
        )
        aprobar.callback = self._callback(True)
        rechazar = discord.ui.Button(
            label="Rechazar", style=discord.ButtonStyle.danger,
            custom_id=f"aprobacion:rechazar:{accion_id}",
        )
        rechazar.callback = self._callback(False)
        self.add_item(aprobar)
        self.add_item(rechazar)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        ok = self._allowlist.is_allowed(
            user_id=interaction.user.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id or 0,
        )
        if not ok:
            await interaction.response.send_message(MSG_NO_AUTORIZADO, ephemeral=True)
        return ok

    def _callback(self, aprobar: bool) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def click(interaction: discord.Interaction) -> None:
            for item in self.children:
                item.disabled = True  # type: ignore[attr-defined]
            await interaction.response.edit_message(view=self)
            try:
                texto = await self._resolver(self._accion_id, aprobar, interaction.user.id)
            except Exception:
                log.exception("Error resolviendo una aprobación")
                texto = MSG_ERROR_APROBACION
            await interaction.followup.send(texto)
            self.stop()

        return click


class AsistenteBot(discord.Client):
    def __init__(
        self,
        allowlist: Allowlist,
        despachador: Despachador,
        *,
        tz: ZoneInfo,
        latido: Latido | None = None,
        canal_avisos_id: int | None = None,
        vigia: Vigia | None = None,
        vigia_ahora: VigiaAhora | None = None,
        canal_vigia_id: int | None = None,
        uso: Callable[[], Awaitable[str]] | None = None,
        vigilar_bloqueo: Callable[[], bool | None] | None = None,
        resolver_aprobacion: ResolverAprobacion | None = None,
        pendientes_al_arrancar: PendientesAlArrancar | None = None,
        host: str | None = None,
    ) -> None:
        super().__init__(intents=crear_intents(), allowed_mentions=SIN_MENCIONES)
        self._despachador = despachador
        self._allowlist = allowlist
        self._guild = discord.Object(id=allowlist.guild_id)
        self._tz = tz
        self._latido = latido
        self._canal_avisos_id = canal_avisos_id
        self._vigia = vigia
        self._vigia_ahora = vigia_ahora
        self._canal_vigia_id = canal_vigia_id
        self._uso = uso
        self._vigilar_bloqueo = vigilar_bloqueo
        self._resolver_aprobacion = resolver_aprobacion
        self._pendientes_al_arrancar = pendientes_al_arrancar
        self._host = host or socket.gethostname()
        self._avisado_inicio = False
        self._avisado_cierre = False
        self.bloqueo_perdido = False  # el proceso debe terminar con error para que lo reinicien
        self._tareas: list[asyncio.Task[None]] = []
        self.tree = _Arbol(self, allowlist)
        self._registrar_comandos()

    def _vista_aprobacion(self, accion_id: UUID) -> VistaAprobacion:
        assert self._resolver_aprobacion is not None
        return VistaAprobacion(accion_id, self._allowlist, self._resolver_aprobacion)

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

    async def _vigilar(self, intervalo_s: float = 60.0, intervalo_incierto_s: float = 10.0) -> None:
        """Si OTRA instancia toma el bloqueo, se detiene: mejor caer que duplicar.

        Un corte de red (`None`) no cuenta como pérdida: se sigue esperando y se revisa más seguido
        para decidir cuanto antes al volver la conexión.
        """
        incierto = False
        while True:
            await asyncio.sleep(intervalo_incierto_s if incierto else intervalo_s)
            estado = await asyncio.to_thread(self._vigilar_bloqueo)
            if estado is False:
                log.critical("Otra instancia tomó el bloqueo de instancia única: el proceso se detiene")
                self.bloqueo_perdido = True
                await self.close()
                return
            if estado is None and not incierto:
                log.warning("Sin conexión a la base: no puedo confirmar la instancia única; sigo esperando")
            elif estado is not None and incierto:
                log.info("Conexión restablecida: sigo siendo la única instancia")
            incierto = estado is None

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
        if self._pendientes_al_arrancar is not None:
            # Re-registra los botones de lo que quedó pendiente antes de un reinicio: sin esto,
            # los mensajes viejos se ven igual pero sus botones ya no responden a nada.
            for accion_id in await self._pendientes_al_arrancar():
                self.add_view(self._vista_aprobacion(accion_id))
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
        acciones_pendientes: list[AccionPendiente] = []
        try:
            async with message.channel.typing():
                resultado = await self._despachador.manejar(**datos, texto=message.content)
        except psycopg.OperationalError:
            log.warning("Sin conexión a la base de datos al procesar un mensaje")
            texto: str | None = MSG_SIN_CONEXION
        except Exception:  # el bot no debe caerse por un mensaje
            log.exception("Error procesando un mensaje")
            texto = MSG_ERROR
        else:
            if resultado is None:
                return
            texto, acciones_pendientes = resultado.respuesta, resultado.acciones_pendientes

        trozos = dividir_mensaje(texto) or ["Listo."]
        await message.reply(trozos[0], mention_author=False)
        for trozo in trozos[1:]:
            await message.channel.send(trozo)
        for accion in acciones_pendientes:
            await message.channel.send(
                embed=crear_embed_propuesta(accion, self._tz), view=self._vista_aprobacion(accion.id)
            )


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
