from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from asistente.agent.service import MSG_LLM_CAIDO, MSG_PAUSADO, responder
from asistente.db.repos.procesos import ProcesoRepo
from asistente.db.repos.recordatorios import RecordatorioRepo
from asistente.db.repos.sistema import EstadoSistemaRepo
from asistente.llm.base import LLMNoDisponible, LLMRespuesta
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


def test_si_respondio_el_modelo_de_respaldo_se_avisa_y_se_registra(conn):
    llm = FakeLLM(
        LLMRespuesta(contenido="Listo.", modelo="openai/gpt-oss-20b", respaldo=True, tokens_in=50)
    )
    res = responder(conn, "hola", llm=llm, tz=TZ, ahora=AHORA)
    assert res.respuesta.startswith("Listo.") and "modelo de respaldo" in res.respuesta
    ejec = conn.execute(
        "select detalle from ejecuciones where tipo = 'mensaje' order by id desc limit 1"
    ).fetchone()
    assert ejec["detalle"]["respaldo"] is True and ejec["detalle"]["modelos"] == ["openai/gpt-oss-20b"]


def test_sin_respaldo_no_se_agrega_ninguna_nota(conn):
    res = responder(conn, "hola", llm=FakeLLM(texto("Hola!")), tz=TZ, ahora=AHORA)
    assert res.respuesta == "Hola!"


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


def test_si_cae_el_modelo_tras_hacer_cambios_se_conservan_y_se_cuentan(conn):
    # Caso real: el asistente creó 4 eventos y luego falló al redactar la respuesta. El usuario
    # recibió "no puedo pensar" aunque el trabajo estaba hecho.
    llm = FakeLLM(
        llamada("crear_recordatorio", {"texto": "llamar al banco", "fecha": "2026-09-22T09:00:00"}),
        LLMNoDisponible("límite por minuto"),
    )
    res = responder(conn, "recuérdame llamar al banco mañana a las 9", llm=llm, tz=TZ, ahora=AHORA)
    assert res.parcial and res.respuesta.startswith("Alcancé a hacer esto")
    assert "llamar al banco" in res.respuesta
    assert "llamar al banco" in [r.texto for r in RecordatorioRepo(conn).buscar_por_texto("banco")]
    ejec = conn.execute("select error from ejecuciones where tipo = 'mensaje' order by id desc limit 1").fetchone()
    assert ejec["error"] == "respuesta parcial: el modelo no estuvo disponible"


def test_si_el_limite_pide_esperar_se_le_dice_al_usuario_cuanto(conn):
    class LimitePorMinuto:
        def chat(self, messages, tools=None, *, json=False):
            raise LLMNoDisponible("Límite por minuto alcanzado", espera_s=31.2)

    res = responder(conn, "anota que me gusta el té", llm=LimitePorMinuto(), tz=TZ, ahora=AHORA)
    assert res.respuesta == "Estoy al límite de uso del modelo (Groq). Reintenta en unos 32 segundos."


# ---------- consultas de agenda: se responden sin el modelo ----------


def test_que_tengo_manana_se_responde_sin_llamar_al_modelo_y_con_los_datos_exactos(conn_agenda):
    from datetime import time

    from asistente.db.models import EventoNuevo
    from asistente.db.repos.agenda import EventoRepo

    EventoRepo(conn_agenda).crear(EventoNuevo(
        nombre="DSY1107 Desarrollo Cloud Native I", dias_semana=[2], hora=time(15, 31), duracion_min=79,
        descripcion="Profesor: IGNACIO ANDRES CUTURRUFO GONZALEZ, Sala TP2 LABORATORIO DE HARDWARE (30)",
    ))
    llm = FakeLLM(texto("NO debería usarse"))
    res = responder(conn_agenda, "que tengo mañana?", llm=llm, tz=TZ, ahora=AHORA)  # AHORA es lunes
    assert llm.llamadas == []  # cero tokens y sin esperar el límite por minuto
    assert res.pasos == 0 and res.tokens_in == 0
    assert res.respuesta.startswith("**Mañana, martes 22/09**")
    assert "CUTURRUFO GONZALEZ" in res.respuesta  # el modelo lo había escrito "Caturrufo"
    ejec = conn_agenda.execute(
        "select tokens_in, detalle from ejecuciones where tipo = 'mensaje' order by id desc limit 1"
    ).fetchone()
    assert ejec["tokens_in"] == 0
    assert ejec["detalle"]["atajo"] == "agenda" and ejec["detalle"]["consulta"] == "mañana"


def test_una_orden_que_menciona_el_dia_no_es_un_atajo_y_va_al_modelo(conn):
    llm = FakeLLM(texto("Listo, lo anoté."))
    res = responder(conn, "borra lo de hoy", llm=llm, tz=TZ, ahora=AHORA)
    assert len(llm.llamadas) == 1 and res.respuesta == "Listo, lo anoté."


# ---------- consultas de procesos: se responden sin el modelo ----------


def test_como_van_mis_procesos_se_responde_sin_llamar_al_modelo(conn):
    from asistente.db.models import ProcesoEstado, ProcesoNuevo
    from asistente.db.repos.procesos import ProcesoRepo

    ProcesoRepo(conn).crear(ProcesoNuevo(nombre="Renovar pasaporte", estado=ProcesoEstado.EN_ESPERA,
                                          esperando_a="Registro Civil"))
    llm = FakeLLM(texto("NO debería usarse"))
    res = responder(conn, "como van mis procesos?", llm=llm, tz=TZ, ahora=AHORA)
    assert llm.llamadas == []
    assert res.pasos == 0 and res.tokens_in == 0
    assert res.respuesta == "• Renovar pasaporte — en espera (de Registro Civil)."
    ejec = conn.execute(
        "select tokens_in, detalle from ejecuciones where tipo = 'mensaje' order by id desc limit 1"
    ).fetchone()
    assert ejec["tokens_in"] == 0
    assert ejec["detalle"]["atajo"] == "procesos" and ejec["detalle"]["consulta"] == "abiertos"


def test_sin_procesos_abiertos_el_atajo_lo_dice_sin_el_modelo(conn):
    llm = FakeLLM(texto("NO debería usarse"))
    res = responder(conn, "mis procesos", llm=llm, tz=TZ, ahora=AHORA)
    assert llm.llamadas == [] and res.respuesta == "No tienes procesos abiertos."


def test_una_orden_sobre_un_proceso_puntual_no_es_un_atajo_y_va_al_modelo(conn):
    llm = FakeLLM(texto("Listo, lo anoté."))
    res = responder(conn, "como va el proceso del pasaporte", llm=llm, tz=TZ, ahora=AHORA)
    assert len(llm.llamadas) == 1 and res.respuesta == "Listo, lo anoté."


def test_en_pausa_tampoco_responde_las_consultas_de_agenda(conn):
    EstadoSistemaRepo(conn).set_pausado(True)
    res = responder(conn, "que tengo hoy?", llm=FakeLLM(), tz=TZ, ahora=AHORA)
    assert res.respuesta == MSG_PAUSADO


def test_llm_caido_responde_con_gracia_y_lo_registra(conn):
    res = responder(conn, "hola", llm=LLMCaido(), tz=TZ, ahora=AHORA)
    assert res.respuesta == MSG_LLM_CAIDO
    ejec = conn.execute("select error from ejecuciones order by id desc limit 1").fetchone()
    assert ejec["error"] == "Groq no responde"
