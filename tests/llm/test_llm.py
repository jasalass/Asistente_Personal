from types import SimpleNamespace

import groq
import httpx
import pytest

from asistente.llm import combinadores
from asistente.llm import groq as groq_mod
from asistente.llm.base import LLMNoDisponible, LLMRespuesta, ToolCallRechazado
from asistente.llm.combinadores import ContadorLLM, LLMConRespaldo
from asistente.llm.groq import GroqLLM
from asistente.llm.presupuesto import presupuesto_de_espera, restante


class Modelo:
    """LLM de mentira que responde o falla según se le indique y cuenta sus llamadas."""

    def __init__(self, nombre, falla: Exception | None = None, tokens=(10, 5)):
        self.nombre, self.falla, self.llamadas, self.tokens = nombre, falla, 0, tokens

    def chat(self, messages, tools=None, *, json=False):
        self.llamadas += 1
        if self.falla:
            raise self.falla
        return LLMRespuesta(
            contenido=f"responde {self.nombre}", tokens_in=self.tokens[0], tokens_out=self.tokens[1],
            modelo=self.nombre,
        )


MSG = [{"role": "user", "content": "hola"}]


# ---------- respaldo entre modelos ----------


def test_usa_el_principal_mientras_este_disponible():
    principal, respaldo = Modelo("120b"), Modelo("20b")
    r = LLMConRespaldo(principal, respaldo).chat(MSG)
    assert r.modelo == "120b" and r.respaldo is False and respaldo.llamadas == 0


def test_si_el_principal_no_esta_disponible_responde_el_respaldo_y_lo_marca():
    principal, respaldo = Modelo("120b", LLMNoDisponible("cupo")), Modelo("20b")
    r = LLMConRespaldo(principal, respaldo).chat(MSG)
    assert r.modelo == "20b" and r.respaldo is True and r.contenido == "responde 20b"


def test_no_se_vuelve_a_molestar_al_principal_durante_el_bloqueo(monkeypatch):
    reloj = [1000.0]
    monkeypatch.setattr(combinadores.time, "monotonic", lambda: reloj[0])
    principal, respaldo = Modelo("120b", LLMNoDisponible("cupo")), Modelo("20b")
    llm = LLMConRespaldo(principal, respaldo)
    llm.chat(MSG)
    llm.chat(MSG)
    assert principal.llamadas == 1 and respaldo.llamadas == 2
    reloj[0] += 301  # pasado el bloqueo se le vuelve a preguntar
    principal.falla = None
    assert llm.chat(MSG).modelo == "120b" and principal.llamadas == 2


def test_si_ningun_modelo_esta_disponible_falla():
    llm = LLMConRespaldo(Modelo("a", LLMNoDisponible("x")), Modelo("b", LLMNoDisponible("y")))
    with pytest.raises(LLMNoDisponible):
        llm.chat(MSG)
    with pytest.raises(LLMNoDisponible):  # sin respaldos, también
        LLMConRespaldo(Modelo("a", LLMNoDisponible("x"))).chat(MSG)


def test_un_rechazo_de_esquema_no_activa_el_respaldo():
    # Es un error corregible del modelo, no falta de cupo: lo maneja el bucle del agente.
    principal, respaldo = Modelo("120b", ToolCallRechazado("mal")), Modelo("20b")
    with pytest.raises(ToolCallRechazado):
        LLMConRespaldo(principal, respaldo).chat(MSG)
    assert respaldo.llamadas == 0


# ---------- contador de tokens ----------


def test_contador_acumula_los_tokens():
    contador = ContadorLLM(Modelo("m", tokens=(100, 20)))
    contador.chat(MSG)
    contador.chat(MSG)
    assert (contador.tokens_in, contador.tokens_out) == (200, 40)


# ---------- Groq: límite por minuto vs cupo diario ----------


def _rate_limit(retry_after: str | None, mensaje: str = "429") -> groq.RateLimitError:
    cabeceras = {"retry-after": retry_after} if retry_after else {}
    resp = httpx.Response(
        429, headers=cabeceras, request=httpx.Request("POST", "https://api.groq.com/x")
    )
    return groq.RateLimitError(mensaje, response=resp, body=None)


TPM = ("Rate limit reached for model `openai/gpt-oss-120b` in organization `org_x` service tier "
       "`on_demand` on tokens per minute (TPM): Limit 8000, Used 7100, Requested 3400. "
       "Please try again in 42s.")
TPD = ("Rate limit reached for model `openai/gpt-oss-120b` in organization `org_x` service tier "
       "`on_demand` on tokens per day (TPD): Limit 200000, Used 199000, Requested 3400. "
       "Please try again in 30s.")


def _groq_que_falla(errores: list, monkeypatch):
    dormidas: list[float] = []
    monkeypatch.setattr(groq_mod.time, "sleep", dormidas.append)
    llm = GroqLLM("clave", "openai/gpt-oss-120b")
    llamadas = []

    def crear(**_):
        llamadas.append(1)
        if errores:
            raise errores.pop(0)
        msg = SimpleNamespace(content="respuesta", tool_calls=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2),
        )

    llm._client.chat.completions.create = crear  # type: ignore[method-assign]
    return llm, dormidas, llamadas


def test_cupo_diario_agotado_falla_de_inmediato_sin_dormir(monkeypatch):
    llm, dormidas, llamadas = _groq_que_falla([_rate_limit("880")], monkeypatch)
    with pytest.raises(LLMNoDisponible, match="agotado"):
        llm.chat(MSG)
    assert dormidas == [] and len(llamadas) == 1  # no se pierde tiempo esperando 15 minutos


def test_limite_por_minuto_espera_y_reintenta(monkeypatch):
    llm, dormidas, llamadas = _groq_que_falla(
        [_rate_limit("7"), _rate_limit("3"), _rate_limit("3")], monkeypatch
    )
    with pytest.raises(LLMNoDisponible, match="alcanzado"):
        llm.chat(MSG)
    assert dormidas == [7.0, 3.0] and len(llamadas) == 3


def test_sin_cabecera_retry_after_se_asume_una_espera_corta(monkeypatch):
    llm, dormidas, _ = _groq_que_falla([_rate_limit(None), _rate_limit(None), _rate_limit(None)], monkeypatch)
    with pytest.raises(LLMNoDisponible):
        llm.chat(MSG)
    assert dormidas == [5.0, 5.0]


# ---------- regresión: 42 s de espera por minuto NO es cupo diario agotado ----------


def test_una_espera_de_42s_por_limite_por_minuto_se_espera_y_se_reintenta(monkeypatch):
    # Caso real: tres mensajes seguidos superaron los 8.000 tokens por minuto y Groq pidió esperar
    # 42 s. Se trató como cupo diario (umbral de 30 s) y el usuario recibió "no puedo pensar".
    llm, dormidas, llamadas = _groq_que_falla([_rate_limit("42", TPM)], monkeypatch)
    r = llm.chat(MSG)
    assert r.contenido == "respuesta" and dormidas == [42.0] and len(llamadas) == 2


def test_el_cupo_diario_se_reconoce_por_el_mensaje_aunque_la_espera_sea_corta(monkeypatch):
    llm, dormidas, llamadas = _groq_que_falla([_rate_limit("30", TPD)], monkeypatch)
    with pytest.raises(LLMNoDisponible, match="diario"):
        llm.chat(MSG)
    assert dormidas == [] and len(llamadas) == 1  # dormir no sirve para un cupo diario


def test_una_espera_por_minuto_excesiva_no_bloquea_el_bot(monkeypatch):
    llm, dormidas, _ = _groq_que_falla([_rate_limit("200", TPM.replace("42s", "200s"))], monkeypatch)
    with pytest.raises(LLMNoDisponible, match="por minuto"):
        llm.chat(MSG)
    assert dormidas == []


def test_el_presupuesto_de_espera_evita_quedar_pegado_y_informa_cuanto_esperar(monkeypatch):
    # Caso real: un mensaje tardó 107 s en varias esperas de 30-40 s cada una.
    llm, dormidas, llamadas = _groq_que_falla([_rate_limit("42", TPM)], monkeypatch)
    with presupuesto_de_espera(10), pytest.raises(LLMNoDisponible) as e:  # quedan 10 s y Groq pide 42
        llm.chat(MSG)
    assert e.value.espera_s == 42.0 and dormidas == [] and len(llamadas) == 1


def test_con_presupuesto_suficiente_si_se_espera(monkeypatch):
    llm, dormidas, _ = _groq_que_falla([_rate_limit("42", TPM)], monkeypatch)
    with presupuesto_de_espera(60):
        assert llm.chat(MSG).contenido == "respuesta"
    assert dormidas == [42.0]


def test_el_presupuesto_es_por_mensaje_no_se_acumula(monkeypatch):
    with presupuesto_de_espera(5):
        assert restante() is not None and restante() <= 5
    assert restante() is None  # fuera del mensaje no hay tope


def test_sin_pistas_en_el_mensaje_60s_se_espera_y_120s_o_mas_es_cupo_diario(monkeypatch):
    llm, dormidas, _ = _groq_que_falla([_rate_limit("60")], monkeypatch)
    assert llm.chat(MSG).contenido == "respuesta" and dormidas == [60.0]

    llm, dormidas, _ = _groq_que_falla([_rate_limit("121")], monkeypatch)
    with pytest.raises(LLMNoDisponible, match="diario"):
        llm.chat(MSG)
    assert dormidas == []
