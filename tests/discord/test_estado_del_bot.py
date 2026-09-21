import asyncio

import discord
import pytest

from asistente.agent.loop import ResultadoAgente
from asistente.discord_bot.bot import AsistenteBot
from asistente.discord_bot.despachador import Despachador
from asistente.security.allowlist import Allowlist

ALLOW = Allowlist(owner_id=1, guild_id=2, channel_ids=frozenset({3}))


async def _responder(texto, historial):
    return ResultadoAgente(respuesta="ok", pasos=1)


def hacer_bot(monkeypatch, *, canal_avisos=3, vigilar=None, avisos=None, falla_al_enviar=False):
    """Bot sin conexión real: `enviar_aviso` y `Client.close` quedan simulados."""
    bot = AsistenteBot(
        ALLOW, Despachador(ALLOW, _responder), canal_avisos_id=canal_avisos,
        vigilar_bloqueo=vigilar, host="mi-servidor",
    )
    enviados = avisos if avisos is not None else []

    async def enviar(texto):
        if falla_al_enviar:
            raise ConnectionError("Discord caído")
        enviados.append(texto)

    async def cerrar_cliente(self):
        return None

    monkeypatch.setattr(bot, "enviar_aviso", enviar)
    monkeypatch.setattr(discord.Client, "close", cerrar_cliente)
    return bot, enviados


# ---------- avisos de estado ----------


def test_avisa_que_esta_en_linea_una_sola_vez_aunque_on_ready_se_repita(monkeypatch):
    bot, enviados = hacer_bot(monkeypatch)

    async def escenario():
        await bot.on_ready()
        await bot.on_ready()  # discord.py lo dispara también en cada reconexión

    asyncio.run(escenario())
    assert enviados == ["Asistente en línea en mi-servidor."]


def test_sin_canal_de_avisos_no_avisa_ni_falla(monkeypatch):
    bot, enviados = hacer_bot(monkeypatch, canal_avisos=None)
    asyncio.run(bot.on_ready())
    assert enviados == []


def test_si_el_aviso_falla_el_bot_sigue_funcionando(monkeypatch):
    bot, _ = hacer_bot(monkeypatch, falla_al_enviar=True)
    asyncio.run(bot.on_ready())  # no debe lanzar
    assert bot._avisado_inicio is True


def test_al_cerrar_avisa_solo_si_llego_a_estar_en_linea_y_una_sola_vez(monkeypatch):
    bot, enviados = hacer_bot(monkeypatch)
    asyncio.run(bot.close())
    assert enviados == []  # nunca estuvo en línea

    bot, enviados = hacer_bot(monkeypatch)

    async def escenario():
        await bot.on_ready()
        await bot.close()
        await bot.close()

    asyncio.run(escenario())
    assert enviados == [
        "Asistente en línea en mi-servidor.",
        "Asistente detenido en mi-servidor (apagado normal).",
    ]


def test_un_aviso_de_cierre_lento_no_bloquea_el_apagado(monkeypatch):
    bot, _ = hacer_bot(monkeypatch)

    async def colgado(texto):
        await asyncio.sleep(60)

    monkeypatch.setattr(bot, "enviar_aviso", colgado)
    bot._avisado_inicio = True

    async def escenario():
        await asyncio.wait_for(bot.close(), timeout=10)  # el aviso tiene 5 s de tope

    asyncio.run(escenario())  # termina sin agotar el timeout externo


# ---------- vigilante del bloqueo de instancia única ----------


def test_si_se_pierde_el_bloqueo_el_bot_se_detiene_y_lo_dice(monkeypatch):
    bot, enviados = hacer_bot(monkeypatch, vigilar=lambda: False)
    bot._avisado_inicio = True

    async def escenario():
        tarea = asyncio.create_task(bot._vigilar(intervalo_s=0.01))
        bot._tareas.append(tarea)
        # Si close() cancelara la propia tarea vigilante, esto lanzaría CancelledError.
        await asyncio.wait_for(tarea, timeout=2)

    asyncio.run(escenario())
    assert bot.bloqueo_perdido is True
    assert enviados == ["Asistente detenido en mi-servidor (otra instancia tomó el control)."]


def test_mientras_el_bloqueo_siga_vigente_no_se_detiene(monkeypatch):
    llamadas = []
    bot, enviados = hacer_bot(monkeypatch, vigilar=lambda: llamadas.append(1) or True)
    bot._avisado_inicio = True

    async def escenario():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(bot._vigilar(intervalo_s=0.01), timeout=0.1)

    asyncio.run(escenario())
    assert len(llamadas) >= 3 and bot.bloqueo_perdido is False and enviados == []
