import json
from typing import Any

from asistente.llm.base import LLMRespuesta, ToolCall


class FakeLLM:
    """LLM guionado: devuelve las respuestas en orden y guarda lo que recibió."""

    def __init__(self, *respuestas: LLMRespuesta) -> None:
        self._respuestas = list(respuestas)
        self.llamadas: list[list[dict[str, Any]]] = []

    def chat(self, messages, tools=None):
        self.llamadas.append([dict(m) for m in messages])
        return self._respuestas.pop(0) if self._respuestas else LLMRespuesta(contenido="fin")


def llamada(nombre: str, args: dict[str, Any] | str, id: str = "c1") -> LLMRespuesta:
    crudo = args if isinstance(args, str) else json.dumps(args)
    return LLMRespuesta(contenido=None, tool_calls=[ToolCall(id, nombre, crudo)], tokens_in=10, tokens_out=5)


def texto(contenido: str) -> LLMRespuesta:
    return LLMRespuesta(contenido=contenido, tokens_in=10, tokens_out=5)


class FakeAuditoria:
    def __init__(self) -> None:
        self.registros: list[tuple[str, str, dict | None]] = []

    def registrar(self, actor, accion, detalle=None):
        self.registros.append((actor, accion, detalle))

    def registrar_ejecucion(self, *args, **kwargs):
        pass
