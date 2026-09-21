import asyncio

from asistente.agent.loop import ResultadoAgente
from asistente.discord_bot.despachador import Despachador
from asistente.discord_bot.util import Historial, dividir_mensaje
from asistente.security.allowlist import Allowlist

OWNER, GUILD, CANAL = 111, 222, 333
ALLOW = Allowlist(owner_id=OWNER, guild_id=GUILD, channel_ids=frozenset({CANAL}))


class ResponderFalso:
    def __init__(self, pasos: int = 1, respuesta: str = "ok") -> None:
        self.llamadas: list[tuple[str, list]] = []
        self._pasos, self._respuesta = pasos, respuesta

    async def __call__(self, texto, historial):
        self.llamadas.append((texto, list(historial)))
        return ResultadoAgente(respuesta=self._respuesta, pasos=self._pasos)


def manejar(desp, **kw):
    datos = {"user_id": OWNER, "guild_id": GUILD, "channel_id": CANAL, "es_bot": False, "texto": "hola"}
    return asyncio.run(desp.manejar(**{**datos, **kw}))


def test_owner_en_canal_permitido_recibe_respuesta():
    r = ResponderFalso(respuesta="hola!")
    assert manejar(Despachador(ALLOW, r)) == "hola!"
    assert r.llamadas[0][0] == "hola"


def test_no_autorizados_se_ignoran_sin_llamar_al_agente():
    r = ResponderFalso()
    d = Despachador(ALLOW, r)
    for kw in (
        {"user_id": 999},
        {"guild_id": 555},
        {"guild_id": None},
        {"channel_id": 444},
        {"es_bot": True},
    ):
        assert manejar(d, **kw) is None
    assert r.llamadas == []


def test_mensaje_vacio_se_ignora():
    r = ResponderFalso()
    assert manejar(Despachador(ALLOW, r), texto="   \n ") is None
    assert r.llamadas == []


def test_atiende_coincide_con_la_allowlist():
    d = Despachador(ALLOW, ResponderFalso())
    ok = {"user_id": OWNER, "guild_id": GUILD, "channel_id": CANAL, "es_bot": False}
    assert d.atiende(**ok)
    assert not d.atiende(**{**ok, "user_id": 1})
    assert not d.atiende(**{**ok, "es_bot": True})


def test_historial_pasa_al_siguiente_mensaje():
    r = ResponderFalso(respuesta="r1")
    d = Despachador(ALLOW, r)
    manejar(d, texto="uno")
    manejar(d, texto="dos")
    assert r.llamadas[0][1] == []
    assert r.llamadas[1][1] == [
        {"role": "user", "content": "uno"},
        {"role": "assistant", "content": "r1"},
    ]


def test_pausa_o_llm_caido_no_entran_al_historial():
    r = ResponderFalso(pasos=0, respuesta="Estoy en pausa")
    d = Despachador(ALLOW, r)
    manejar(d, texto="uno")
    manejar(d, texto="dos")
    assert r.llamadas[1][1] == []


def test_historial_es_por_canal():
    h = Historial()
    h.agregar(1, "a", "b")
    assert h.obtener(2) == [] and len(h.obtener(1)) == 2


def test_historial_esta_acotado():
    h = Historial(max_mensajes=4, max_chars=10)
    for i in range(5):
        h.agregar(1, f"pregunta larga {i}", "respuesta larga")
    msgs = h.obtener(1)
    assert len(msgs) == 4 and all(len(m["content"]) <= 10 for m in msgs)
    assert msgs[-2]["content"] == "pregunta la"[:10]


def test_mensajes_se_procesan_de_a_uno():
    activos, maximo = 0, 0

    async def lento(texto, historial):
        nonlocal activos, maximo
        activos += 1
        maximo = max(maximo, activos)
        await asyncio.sleep(0.02)
        activos -= 1
        return ResultadoAgente(respuesta="ok", pasos=1)

    d = Despachador(ALLOW, lento)

    async def dos_a_la_vez():
        datos = {"user_id": OWNER, "guild_id": GUILD, "channel_id": CANAL, "es_bot": False}
        await asyncio.gather(d.manejar(**datos, texto="a"), d.manejar(**datos, texto="b"))

    asyncio.run(dos_a_la_vez())
    assert maximo == 1


def test_dividir_mensaje_corto_no_se_toca():
    assert dividir_mensaje("hola") == ["hola"]
    assert dividir_mensaje("   ") == []


def test_dividir_mensaje_largo_respeta_limite_y_no_pierde_texto():
    texto = " ".join(f"palabra{i}" for i in range(600))
    trozos = dividir_mensaje(texto, limite=200)
    assert all(len(t) <= 200 for t in trozos) and len(trozos) > 1
    assert " ".join(trozos).split() == texto.split()


def test_dividir_mensaje_sin_espacios_hace_corte_duro():
    trozos = dividir_mensaje("x" * 450, limite=200)
    assert [len(t) for t in trozos] == [200, 200, 50]
