import asyncio

import discord
import psycopg
import pytest

from asistente.agent.loop import ResultadoAgente
from asistente.discord_bot.bot import MSG_ERROR, MSG_SIN_CONEXION, AsistenteBot
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


# ---------- un corte de red no apaga el bot; otra instancia sí ----------


def secuencia(*estados):
    """Un vigilante que devuelve los estados en orden (y luego repite el último)."""
    pendientes = list(estados)
    llamadas = []

    def vigilar():
        llamadas.append(1)
        return pendientes.pop(0) if len(pendientes) > 1 else pendientes[0]

    vigilar.llamadas = llamadas
    return vigilar


def test_sin_conexion_el_bot_no_se_apaga_y_lo_dice_una_sola_vez(monkeypatch, caplog):
    vigilar = secuencia(None, None, None, True)
    bot, enviados = hacer_bot(monkeypatch, vigilar=vigilar)
    bot._avisado_inicio = True

    async def escenario():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(bot._vigilar(intervalo_s=0.01, intervalo_incierto_s=0.01), timeout=0.3)

    with caplog.at_level("INFO", logger="asistente.discord_bot.bot"):
        asyncio.run(escenario())
    assert bot.bloqueo_perdido is False and enviados == []  # nunca se apagó
    assert caplog.text.count("Sin conexión a la base") == 1  # no repite el aviso en cada revisión
    assert caplog.text.count("Conexión restablecida") == 1


def test_al_volver_la_conexion_y_ser_otra_instancia_la_duena_se_detiene(monkeypatch):
    bot, enviados = hacer_bot(monkeypatch, vigilar=secuencia(None, None, False))
    bot._avisado_inicio = True

    async def escenario():
        tarea = asyncio.create_task(bot._vigilar(intervalo_s=0.01, intervalo_incierto_s=0.01))
        bot._tareas.append(tarea)
        await asyncio.wait_for(tarea, timeout=2)

    asyncio.run(escenario())
    assert bot.bloqueo_perdido is True
    assert enviados == ["Asistente detenido en mi-servidor (otra instancia tomó el control)."]


def test_tras_un_corte_se_revisa_mas_seguido_para_decidir_pronto(monkeypatch):
    # Intervalo normal 0.3 s, tras un corte 0.01 s: con revisión adaptativa termina en ~0.33 s;
    # sin ella tardaría 1.2 s y el tiempo límite de 0.8 s la cortaría.
    bot, _ = hacer_bot(monkeypatch, vigilar=secuencia(None, None, None, False))
    bot._avisado_inicio = True

    async def escenario():
        tarea = asyncio.create_task(bot._vigilar(intervalo_s=0.3, intervalo_incierto_s=0.01))
        bot._tareas.append(tarea)
        await asyncio.wait_for(tarea, timeout=0.8)

    asyncio.run(escenario())
    assert bot.bloqueo_perdido is True


# ---------- mensajes de error cuando falta la red ----------


class _Escribiendo:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Canal:
    id = 3

    def typing(self):
        return _Escribiendo()

    async def send(self, texto):
        self.enviados = getattr(self, "enviados", []) + [texto]


class _Mensaje:
    def __init__(self):
        from types import SimpleNamespace

        self.author = SimpleNamespace(id=1, bot=False)  # el owner de ALLOW
        self.guild = SimpleNamespace(id=2)
        self.channel = _Canal()
        self.content = "hola"
        self.respuestas = []

    async def reply(self, texto, mention_author=False):
        self.respuestas.append(texto)


def bot_con_responder_que_falla(monkeypatch, error):
    async def responder(texto, historial):
        raise error

    bot = AsistenteBot(ALLOW, Despachador(ALLOW, responder), host="mi-servidor")
    return bot


def test_sin_base_de_datos_el_usuario_recibe_un_mensaje_claro_y_sin_traceback(monkeypatch, caplog):
    bot = bot_con_responder_que_falla(monkeypatch, psycopg.OperationalError("sin red"))
    msg = _Mensaje()
    with caplog.at_level("WARNING", logger="asistente.discord_bot.bot"):
        asyncio.run(bot.on_message(msg))
    assert msg.respuestas == [MSG_SIN_CONEXION]
    assert "Traceback" not in caplog.text  # es transitorio: una línea, no un traceback


def test_otro_error_sigue_dando_el_mensaje_generico(monkeypatch):
    bot = bot_con_responder_que_falla(monkeypatch, RuntimeError("bug"))
    msg = _Mensaje()
    asyncio.run(bot.on_message(msg))
    assert msg.respuestas == [MSG_ERROR]


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
