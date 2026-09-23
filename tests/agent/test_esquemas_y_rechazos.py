import json
from zoneinfo import ZoneInfo

import groq
import httpx
import pytest
from pydantic import ValidationError

from asistente.agent.loop import MSG_SIN_RESPUESTA, ejecutar_agente
from asistente.agent.tools import (
    ActualizarTemaArgs,
    CrearProcesoArgs,
    CrearRecordatorioArgs,
    CrearTemaArgs,
    construir_registro,
)
from asistente.llm.base import ToolCallRechazado
from asistente.llm.groq import GroqLLM
from tests.agent.test_loop import hacer_registro
from tests.fakes import FakeAuditoria, FakeLLM, llamada, texto

TZ = ZoneInfo("America/Santiago")


def registro_completo():
    """Con los grupos bajo demanda activados: así se revisan los esquemas de todas las tools."""
    reg = construir_registro(None, TZ)
    reg.activar_grupo("vigia")
    reg.activar_grupo("autoridad")
    return reg


# ---------- esquemas: nada que Groq valide de forma estricta y el modelo no pueda cumplir ----------


def test_los_esquemas_no_usan_formatos_date_time_ni_time():
    # Regresión: Groq rechazaba con 400 "08:00" (format: time) y obligaba a agregar un desfase a
    # las fechas (format: date-time), lo que corría las horas. Los formatos `date` sí son seguros.
    defs = json.dumps(registro_completo().definiciones())
    assert '"format": "date-time"' not in defs
    assert '"format": "time"' not in defs


def test_las_horas_y_fechas_llegan_como_texto_con_patron():
    esquema = registro_completo().definiciones()
    props = {d["function"]["name"]: d["function"]["parameters"]["properties"] for d in esquema}
    assert props["crear_tema"]["hora_preferida"]["type"] == "string"
    assert props["crear_tema"]["hora_preferida"]["pattern"]
    assert props["crear_recordatorio"]["fecha"]["type"] == "string"
    assert props["actualizar_proceso"]["proxima_accion_fecha"]["type"] == "string"


@pytest.mark.parametrize("hora", ["08:00", "00:00", "23:59"])
def test_hora_valida(hora):
    assert CrearTemaArgs.model_validate(
        {"nombre": "x", "query_busqueda": "consulta", "hora_preferida": hora}
    ).hora_preferida == hora


@pytest.mark.parametrize("hora", ["8:00", "08:00:00", "24:00", "08:60", "las 8", "08:00 am"])
def test_hora_invalida(hora):
    with pytest.raises(ValidationError):
        CrearTemaArgs.model_validate({"nombre": "x", "query_busqueda": "consulta", "hora_preferida": hora})
    with pytest.raises(ValidationError):
        ActualizarTemaArgs.model_validate({"id": "00000000-0000-0000-0000-000000000000", "hora_preferida": hora})


@pytest.mark.parametrize(
    "fecha",
    ["2026-10-15T13:00", "2026-10-15T13:00:00", "2026-10-15T13:00:00Z",
     "2026-10-15T13:00:00-04:00", "2026-10-15T13:00:00+09:00"],
)
def test_fechas_con_o_sin_desfase_se_aceptan(fecha):
    assert CrearRecordatorioArgs.model_validate({"texto": "x", "fecha": fecha}).fecha == fecha
    assert CrearProcesoArgs.model_validate({"nombre": "x", "proxima_accion_fecha": fecha})


@pytest.mark.parametrize("fecha", ["mañana", "15/10/2026", "2026-10-15", "2026-10-15 13:00", ""])
def test_fechas_en_otros_formatos_se_rechazan(fecha):
    with pytest.raises(ValidationError):
        CrearRecordatorioArgs.model_validate({"texto": "x", "fecha": fecha})


def test_avisar_sin_novedades_solo_si_el_usuario_lo_pide():
    esquema = registro_completo().definiciones()
    crear = next(d for d in esquema if d["function"]["name"] == "crear_tema")
    descripcion = crear["function"]["parameters"]["properties"]["avisar_sin_novedades"]["description"]
    assert "expresamente" in descripcion


# ---------- el agente se corrige cuando Groq rechaza una llamada ----------


def test_un_rechazo_de_esquema_se_informa_al_modelo_y_se_reintenta():
    llm = FakeLLM(
        ToolCallRechazado("`/hora_preferida`: '08:00' is not valid time"),
        llamada("eco", {"valor": "x"}),
        texto("listo"),
    )
    aud = FakeAuditoria()
    res = ejecutar_agente(
        "hola", llm=llm, registro=hacer_registro([]), auditoria=aud, system_prompt="sp"
    )
    assert res.respuesta == "listo" and res.pasos == 3
    aviso = llm.llamadas[1][-1]
    assert aviso["role"] == "user" and "rechazada" in aviso["content"]
    assert "hora_preferida" in aviso["content"]
    assert aud.registros[0][1] == "llm:tool_call_rechazado"


def test_si_los_rechazos_no_paran_se_corta_por_el_tope_de_pasos():
    llm = FakeLLM(*[ToolCallRechazado("mal") for _ in range(10)])
    res = ejecutar_agente(
        "hola", llm=llm, registro=hacer_registro([]), auditoria=FakeAuditoria(),
        system_prompt="sp", max_pasos=3,
    )
    assert res.respuesta == MSG_SIN_RESPUESTA and res.pasos == 3 and len(llm.llamadas) == 3


# ---------- el cliente de Groq traduce los 400 corregibles ----------


def _error_400(codigo: str, mensaje: str) -> groq.BadRequestError:
    respuesta = httpx.Response(400, request=httpx.Request("POST", "https://api.groq.com/x"))
    cuerpo = {"error": {"message": mensaje, "code": codigo, "failed_generation": "SECRETO-DEL-MODELO"}}
    return groq.BadRequestError("Error 400", response=respuesta, body=cuerpo["error"])


def _llm_que_falla(error: Exception) -> GroqLLM:
    llm = GroqLLM("clave", "openai/gpt-oss-120b")

    def crear(**_):
        raise error

    llm._client.chat.completions.create = crear  # type: ignore[method-assign]
    return llm


@pytest.mark.parametrize("codigo", ["tool_use_failed", "json_validate_failed"])
def test_groq_traduce_los_400_corregibles_sin_filtrar_lo_generado(codigo):
    llm = _llm_que_falla(_error_400(codigo, "'08:00' is not valid time"))
    with pytest.raises(ToolCallRechazado) as e:
        llm.chat([{"role": "user", "content": "hola"}])
    assert "not valid time" in str(e.value) and "SECRETO-DEL-MODELO" not in str(e.value)


def test_groq_no_traduce_otros_400():
    llm = _llm_que_falla(_error_400("model_not_found", "no existe"))
    with pytest.raises(groq.BadRequestError):
        llm.chat([{"role": "user", "content": "hola"}])
