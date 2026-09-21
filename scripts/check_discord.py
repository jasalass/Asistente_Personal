"""Verifica que el bot conecta, está en tu servidor y tiene permisos en cada canal.

No publica ningún mensaje. Se conecta, inspecciona y se desconecta.
"""

import asyncio
import sys

import discord

from asistente.config import get_settings
from asistente.discord_bot.bot import crear_intents

PERMISOS = {
    "view_channel": "ver canal",
    "send_messages": "enviar mensajes",
    "embed_links": "insertar enlaces",
    "read_message_history": "leer historial",
}


async def main() -> int:
    cfg = get_settings()
    fallos = 0
    client = discord.Client(intents=crear_intents())
    listo = asyncio.Event()

    @client.event
    async def on_ready() -> None:
        listo.set()

    def check(nombre: str, ok: bool, detalle: str = "") -> None:
        nonlocal fallos
        print(f"[{'OK' if ok else 'FALLA'}] {nombre}" + (f" ({detalle})" if detalle else ""))
        fallos += not ok

    try:
        await client.login(cfg.discord_token.get_secret_value())
    except discord.LoginFailure:
        print("[FALLA] token inválido: revisa DISCORD_TOKEN (¿copiaste el de la pestaña Bot?)")
        await client.close()
        return 1
    check("token válido", True)

    tarea = asyncio.create_task(client.connect())
    espera = asyncio.create_task(listo.wait())
    await asyncio.wait({tarea, espera}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
    if not espera.done():
        error = tarea.exception() if tarea.done() else None
        if isinstance(error, discord.PrivilegedIntentsRequired):
            print(
                "[FALLA] Message Content Intent desactivado: actívalo en el portal de "
                "desarrolladores > tu app > Bot > Privileged Gateway Intents"
            )
        elif error:
            print(f"[FALLA] no conectó: {type(error).__name__}")
        else:
            print("[FALLA] no conectó en 30 s")
        espera.cancel()
        await client.close()
        return 1
    check("conexión al gateway e intents", True, str(client.user))

    guild = client.get_guild(cfg.discord_guild_id)
    check("el bot está en tu servidor", guild is not None, guild.name if guild else "")
    if guild:
        check("servidor único (no está en otros)", len(client.guilds) == 1, f"{len(client.guilds)} servidores")
        yo = guild.me
        for canal_id in sorted(cfg.discord_channel_ids):
            canal = guild.get_channel(canal_id)
            if canal is None:
                check(f"canal {canal_id} visible", False, "no existe o el bot no lo ve")
                continue
            perms = canal.permissions_for(yo)
            faltan = [txt for attr, txt in PERMISOS.items() if not getattr(perms, attr)]
            check(f"#{canal.name}", not faltan, f"faltan: {', '.join(faltan)}" if faltan else "permisos OK")
        check("NO es administrador", not yo.guild_permissions.administrator)
        # fetch_member consulta la API; get_member solo ve la caché, que está vacía porque
        # el intent de miembros está apagado a propósito.
        try:
            dueno = await guild.fetch_member(cfg.discord_owner_id)
        except discord.NotFound:
            dueno = None
        check("tu usuario está en el servidor", dueno is not None, dueno.name if dueno else "")

    await client.close()
    print("\nTodo bien" if not fallos else f"\n{fallos} verificación(es) fallaron")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
