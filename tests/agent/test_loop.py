import json

import pytest
from pydantic import BaseModel

from asistente.agent.loop import MSG_SIN_RESPUESTA, ejecutar_agente
from asistente.llm.base import LLMNoDisponible, LLMRespuesta, ToolCall
from asistente.security.tool_registry import Level, ToolError, ToolRegistry, ToolSpec
from tests.fakes import FakeAuditoria, FakeLLM, FakeTrazas, llamada, texto


class EcoArgs(BaseModel):
    valor: str


def hacer_registro(ejecutadas: list) -> ToolRegistry:
    reg = ToolRegistry()

    def eco(valor):
        ejecutadas.append(valor)
        return {"eco": valor}

    def falla():
        raise ToolError("no existe")

    reg.register(ToolSpec("eco", Level.AUTO, "repite", eco, EcoArgs))
    reg.register(ToolSpec("falla", Level.AUTO, "siempre falla", falla))
    reg.register(ToolSpec("enviar_email", Level.PROPONE, "envía", lambda **k: "enviado"))
    reg.register(ToolSpec("pagar", Level.PROHIBIDO, "paga", lambda **k: "pagado"))
    return reg


def correr(llm, registro=None, **kw):
    aud = FakeAuditoria()
    res = ejecutar_agente(
        "hola", llm=llm, registro=registro or hacer_registro([]), auditoria=aud, system_prompt="sp", **kw
    )
    return res, aud


def test_respuesta_directa_sin_tools():
    res, _ = correr(FakeLLM(texto("Hola!")))
    assert res.respuesta == "Hola!"
    assert res.pasos == 1 and res.tokens_in == 10


def test_ejecuta_tool_y_devuelve_resultado_al_modelo():
    ejecutadas = []
    llm = FakeLLM(llamada("eco", {"valor": "x"}), texto("hecho"))
    res, aud = correr(llm, hacer_registro(ejecutadas))
    assert ejecutadas == ["x"] and res.respuesta == "hecho" and res.pasos == 2
    mensaje_tool = llm.llamadas[1][-1]
    assert mensaje_tool["role"] == "tool" and json.loads(mensaje_tool["content"]) == {"eco": "x"}
    assert aud.registros[0][1] == "tool:eco" and aud.registros[0][2]["estado"] == "ok"


def test_tool_desconocida_no_rompe_y_se_informa_al_modelo():
    llm = FakeLLM(llamada("borrar_todo", {}), texto("no pude"))
    res, aud = correr(llm)
    assert res.respuesta == "no pude"
    assert "error" in json.loads(llm.llamadas[1][-1]["content"])
    assert aud.registros[0][2]["estado"] == "denegada"


def test_tool_prohibida_denegada_y_no_ejecutada():
    llm = FakeLLM(llamada("pagar", {"monto": 5}), texto("ok"))
    _, aud = correr(llm)
    assert "error" in json.loads(llm.llamadas[1][-1]["content"])
    assert aud.registros[0][2]["estado"] == "denegada"


def test_argumentos_invalidos_se_devuelven_para_corregir():
    llm = FakeLLM(llamada("eco", {"otro": 1}), texto("ok"))
    correr(llm)
    assert "valor" in json.loads(llm.llamadas[1][-1]["content"])["error"]


def test_argumentos_que_no_son_json():
    llm = FakeLLM(llamada("eco", "{no es json"), texto("ok"))
    _, aud = correr(llm)
    assert "error" in json.loads(llm.llamadas[1][-1]["content"])
    assert aud.registros[0][2]["estado"] == "args_no_json"


def test_error_de_tool_se_informa_sin_romper():
    llm = FakeLLM(llamada("falla", {}), texto("ok"))
    correr(llm)
    assert json.loads(llm.llamadas[1][-1]["content"]) == {"error": "no existe"}


def test_tool_propone_genera_propuesta_y_no_ejecuta():
    llm = FakeLLM(llamada("enviar_email", {"to": "a@b.cl"}), texto("Espero tu OK"))
    res, aud = correr(llm)
    assert len(res.propuestas) == 1 and res.propuestas[0].tool == "enviar_email"
    assert json.loads(llm.llamadas[1][-1]["content"])["estado"] == "pendiente_de_aprobacion"
    assert aud.registros[0][2]["estado"] == "propuesta"


def test_tope_de_pasos_corta_el_ciclo():
    llm = FakeLLM(*[llamada("eco", {"valor": "x"}, id=f"c{i}") for i in range(20)])
    res, _ = correr(llm, max_pasos=3)
    assert res.pasos == 3 and res.respuesta == MSG_SIN_RESPUESTA
    assert len(llm.llamadas) == 3


def test_el_modelo_no_ve_tools_prohibidas():
    reg = hacer_registro([])
    nombres = {d["function"]["name"] for d in reg.definiciones()}
    assert "pagar" not in nombres and {"eco", "enviar_email"} <= nombres


def test_historial_se_incluye_entre_system_y_mensaje():
    llm = FakeLLM(texto("ok"))
    correr(llm, historial=[{"role": "user", "content": "antes"}])
    roles = [m["content"] for m in llm.llamadas[0]]
    assert roles == ["sp", "antes", "hola"]


# ---------- si el modelo cae DESPUÉS de haber hecho cambios, se cuentan en vez de "no puedo pensar" ----------


def registro_con_escritura(hechas: list) -> ToolRegistry:
    reg = ToolRegistry()

    def crear_cosa(nombre):
        hechas.append(nombre)
        return {"resumen": f"Cosa creada: {nombre}", "nombre": nombre}

    class Args(BaseModel):
        nombre: str

    reg.register(ToolSpec("crear_cosa", Level.AUTO, "crea", crear_cosa, Args))
    reg.register(ToolSpec("listar_cosas", Level.AUTO, "lista", list))
    reg.register(ToolSpec("falla", Level.AUTO, "falla", lambda: (_ for _ in ()).throw(ToolError("no existe"))))
    return reg


def dos_llamadas_en_paralelo(*args_json):
    calls = [ToolCall(f"c{i}", "crear_cosa", a) for i, a in enumerate(args_json)]
    return LLMRespuesta(contenido=None, tool_calls=calls, tokens_in=10, tokens_out=5)


def test_si_cae_el_modelo_tras_hacer_cambios_se_cuentan_los_cambios():
    hechas = []
    llm = FakeLLM(
        dos_llamadas_en_paralelo('{"nombre": "Fullstack"}', '{"nombre": "Cloud Native"}'),
        LLMNoDisponible("límite por minuto"),
    )
    res, _ = correr(llm, registro_con_escritura(hechas))
    assert hechas == ["Fullstack", "Cloud Native"]  # los cambios sí quedaron hechos
    assert res.parcial is True
    assert res.respuesta.startswith("Alcancé a hacer esto, pero no pude redactar la respuesta")
    assert "• Cosa creada: Fullstack" in res.respuesta and "• Cosa creada: Cloud Native" in res.respuesta
    assert "no puedo pensar" not in res.respuesta.lower()


def test_si_solo_se_consulto_y_cae_el_modelo_se_propaga_el_error_habitual():
    llm = FakeLLM(llamada("listar_cosas", {}), LLMNoDisponible("sin cupo"))
    with pytest.raises(LLMNoDisponible):
        correr(llm, registro_con_escritura([]))


def test_una_herramienta_que_fallo_no_cuenta_como_algo_hecho():
    llm = FakeLLM(llamada("falla", {}), LLMNoDisponible("sin cupo"))
    with pytest.raises(LLMNoDisponible):
        correr(llm, registro_con_escritura([]))


def test_sin_llamadas_previas_el_error_del_modelo_se_propaga():
    with pytest.raises(LLMNoDisponible):
        correr(FakeLLM(LLMNoDisponible("sin cupo")), registro_con_escritura([]))


# ---------- trazas: un paso por cada llamada al LLM y a cada tool, cuando hay tabla ----------


def test_sin_trazas_el_resultado_no_trae_id():
    res, _ = correr(FakeLLM(texto("ok")))
    assert res.traza_id is None


def test_si_la_tabla_no_esta_disponible_no_se_registra_ningun_paso():
    trazas = FakeTrazas(disponible=False)
    res, _ = correr(FakeLLM(texto("ok")), trazas=trazas)
    assert res.traza_id is None and trazas.pasos == []


def test_con_trazas_se_registra_cada_llamada_al_llm_y_cada_tool():
    llm = FakeLLM(llamada("eco", {"valor": "x"}), texto("hecho"))
    trazas = FakeTrazas()
    res, _ = correr(llm, hacer_registro([]), trazas=trazas)
    assert res.traza_id is not None
    secuencia = [(p["orden"], p["tipo"], p["nombre"]) for p in trazas.pasos]
    assert secuencia == [(1, "llm", "?"), (2, "tool", "eco"), (3, "llm", "?")]
    paso_tool = trazas.pasos[1]
    assert paso_tool["entrada"] == {"valor": "x"} and paso_tool["salida"] == {"eco": "x"}
    assert paso_tool["error"] is None and paso_tool["duracion_ms"] is not None


def test_una_tool_que_falla_se_traza_con_su_error_y_sin_salida():
    llm = FakeLLM(llamada("falla", {}), texto("ok"))
    trazas = FakeTrazas()
    correr(llm, trazas=trazas)
    (_, paso_tool, _) = trazas.pasos
    assert paso_tool["tipo"] == "tool" and paso_tool["error"] == "no existe" and paso_tool["salida"] is None


def test_un_rechazo_de_esquema_se_traza_como_error_del_paso_llm():
    from asistente.llm.base import ToolCallRechazado

    llm = FakeLLM(ToolCallRechazado("mal esquema"), texto("ok"))
    trazas = FakeTrazas()
    correr(llm, trazas=trazas)
    assert trazas.pasos[0]["tipo"] == "llm" and trazas.pasos[0]["error"] == "mal esquema"
