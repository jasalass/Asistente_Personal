from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from asistente.agent.service import MSG_LLM_CAIDO, MSG_PAUSADO, responder
from asistente.db.repos.procesos import ProcesoRepo
from asistente.db.repos.sistema import EstadoSistemaRepo
from asistente.llm.base import LLMNoDisponible
from tests.fakes import FakeLLM, llamada, texto

TZ = ZoneInfo("America/Santiago")
AHORA = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


class LLMCaido:
    def chat(self, messages, tools=None):
        raise LLMNoDisponible("Groq no responde")


def test_flujo_completo_crea_proceso_y_registra_auditoria(conn):
    llm = FakeLLM(
        llamada("crear_proceso", {"nombre": "Renovar pasaporte", "estado": "en_espera",
                                   "esperando_a": "Registro Civil"}),
        texto("Guardé 'Renovar pasaporte', en espera del Registro Civil."),
    )
    res = responder(conn, "renovando pasaporte, espero hora del Registro Civil", llm=llm, tz=TZ, ahora=AHORA)

    assert "Registro Civil" in res.respuesta and res.pasos == 2
    assert [p.nombre for p in ProcesoRepo(conn).buscar_por_nombre("pasaporte")] == ["Renovar pasaporte"]

    acciones = [r["accion"] for r in conn.execute("select accion from auditoria order by id desc limit 1")]
    assert acciones == ["tool:crear_proceso"]
    ejec = conn.execute("select tipo, tokens_in, error from ejecuciones order by id desc limit 1").fetchone()
    assert ejec["tipo"] == "mensaje" and ejec["tokens_in"] == 20 and ejec["error"] is None


def test_el_prompt_incluye_reglas_fecha_y_zona(conn):
    llm = FakeLLM(texto("ok"))
    responder(conn, "hola", llm=llm, tz=TZ, ahora=AHORA)
    system = llm.llamadas[0][0]["content"]
    assert "Niveles de autoridad" in system and "Skill: procesos" in system
    assert "2026-09-21" in system and "America/Santiago" in system


def test_en_pausa_no_llama_al_llm(conn):
    EstadoSistemaRepo(conn).set_pausado(True)
    llm = FakeLLM(texto("no debería usarse"))
    res = responder(conn, "hola", llm=llm, tz=TZ, ahora=AHORA)
    assert res.respuesta == MSG_PAUSADO and llm.llamadas == []


def test_llm_caido_responde_con_gracia_y_lo_registra(conn):
    res = responder(conn, "hola", llm=LLMCaido(), tz=TZ, ahora=AHORA)
    assert res.respuesta == MSG_LLM_CAIDO
    ejec = conn.execute("select error from ejecuciones order by id desc limit 1").fetchone()
    assert ejec["error"] == "Groq no responde"
