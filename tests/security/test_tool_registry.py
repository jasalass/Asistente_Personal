import json
from enum import StrEnum

import pytest
from pydantic import BaseModel, ConfigDict

from asistente.security.approvals import payload_hash
from asistente.security.tool_registry import (
    Level,
    Proposal,
    ToolArgsInvalid,
    ToolDenied,
    ToolRegistry,
    ToolSpec,
    compactar_schema,
)


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


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str


def test_argumentos_invalidos_no_llegan_al_handler_ni_generan_propuesta():
    calls = []
    reg = ToolRegistry()
    reg.register(ToolSpec("enviar", Level.PROPONE, "e", lambda **k: calls.append(k), _Args))
    reg.register(ToolSpec("leer", Level.AUTO, "l", lambda **k: calls.append(k), _Args))
    for tool in ("enviar", "leer"):
        for args in ({}, {"to": 123}, {"to": "a@b.cl", "extra": 1}):
            with pytest.raises(ToolArgsInvalid):
                reg.invoke(tool, args)
    assert calls == []


def test_definiciones_excluyen_prohibidas_e_incluyen_esquema():
    reg = ToolRegistry()
    reg.register(ToolSpec("leer", Level.AUTO, "lee", lambda **k: None, _Args))
    reg.register(ToolSpec("pagar", Level.PROHIBIDO, "paga", lambda **k: None))
    defs = reg.definiciones()
    assert [d["function"]["name"] for d in defs] == ["leer"]
    assert "to" in defs[0]["function"]["parameters"]["properties"]


class _Estado(StrEnum):
    A = "a"
    B = "b"


class _Complejo(BaseModel):
    title: str  # una propiedad llamada 'title' no debe perderse
    estado: _Estado | None = None
    tags: list[str] | None = None


def test_compactar_schema_inlinea_defs_quita_ruido_y_conserva_propiedades():
    schema = compactar_schema(_Complejo.model_json_schema())
    assert "$defs" not in schema and "$ref" not in json.dumps(schema)
    assert set(schema["properties"]) == {"title", "estado", "tags"}
    assert schema["properties"]["estado"]["enum"] == ["a", "b"]
    assert schema["properties"]["tags"]["type"] == "array"
    assert schema["required"] == ["title"]
    assert "default" not in json.dumps(schema)
    assert len(json.dumps(schema)) < len(json.dumps(_Complejo.model_json_schema()))


def test_hash_es_estable_ante_orden_de_claves():
    assert payload_hash("t", {"a": 1, "b": 2}) == payload_hash("t", {"b": 2, "a": 1})
