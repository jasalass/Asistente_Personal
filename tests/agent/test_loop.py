import json

from pydantic import BaseModel

from asistente.agent.loop import MSG_SIN_RESPUESTA, ejecutar_agente
from asistente.security.tool_registry import Level, ToolError, ToolRegistry, ToolSpec
from tests.fakes import FakeAuditoria, FakeLLM, llamada, texto


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
