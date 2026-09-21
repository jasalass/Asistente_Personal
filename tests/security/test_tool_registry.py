import pytest

from asistente.security.approvals import payload_hash
from asistente.security.tool_registry import Level, Proposal, ToolDenied, ToolRegistry, ToolSpec


def make_registry(calls: list) -> ToolRegistry:
    reg = ToolRegistry()

    def record(name):
        def handler(**kwargs):
            calls.append((name, kwargs))
            return "ok"

        return handler

    reg.register(ToolSpec("leer", Level.AUTO, "lee", record("leer")))
    reg.register(ToolSpec("enviar_email", Level.PROPONE, "envía", record("enviar_email")))
    reg.register(ToolSpec("pagar", Level.PROHIBIDO, "paga", record("pagar")))
    return reg


def test_tool_no_registrada_se_deniega():
    with pytest.raises(ToolDenied):
        make_registry([]).invoke("borrar_todo", {})


def test_auto_ejecuta():
    calls = []
    assert make_registry(calls).invoke("leer", {"x": 1}) == "ok"
    assert calls == [("leer", {"x": 1})]


def test_prohibido_nunca_ejecuta():
    calls = []
    with pytest.raises(ToolDenied):
        make_registry(calls).invoke("pagar", {"monto": 10})
    assert calls == []


def test_propone_no_ejecuta_y_devuelve_propuesta():
    calls = []
    result = make_registry(calls).invoke("enviar_email", {"to": "a@b.cl"})
    assert isinstance(result, Proposal)
    assert result.hash == payload_hash("enviar_email", {"to": "a@b.cl"})
    assert calls == []


def test_aprobacion_con_hash_correcto_ejecuta():
    calls = []
    reg = make_registry(calls)
    prop = reg.invoke("enviar_email", {"to": "a@b.cl"})
    reg.execute_approved(prop, approved_hash=prop.hash)
    assert calls == [("enviar_email", {"to": "a@b.cl"})]


def test_aprobacion_con_payload_alterado_se_rechaza():
    calls = []
    reg = make_registry(calls)
    aprobado = reg.invoke("enviar_email", {"to": "a@b.cl"})
    alterado = Proposal("enviar_email", {"to": "atacante@x.com"}, aprobado.hash)
    with pytest.raises(ToolDenied):
        reg.execute_approved(alterado, approved_hash=aprobado.hash)
    assert calls == []


def test_execute_approved_no_sirve_para_tools_prohibidas_ni_auto():
    reg = make_registry([])
    for tool in ("pagar", "leer"):
        prop = Proposal(tool, {}, payload_hash(tool, {}))
        with pytest.raises(ToolDenied):
            reg.execute_approved(prop, approved_hash=prop.hash)


def test_registro_duplicado_falla():
    reg = make_registry([])
    with pytest.raises(ValueError):
        reg.register(ToolSpec("leer", Level.PROHIBIDO, "intento de degradar", lambda: None))


def test_hash_es_estable_ante_orden_de_claves():
    assert payload_hash("t", {"a": 1, "b": 2}) == payload_hash("t", {"b": 2, "a": 1})
