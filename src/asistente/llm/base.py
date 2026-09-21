from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMNoDisponible(Exception):
    """El proveedor no respondió (límite de uso agotado, caída, etc.).

    `espera_s`, si se conoce, es cuánto pidió esperar el proveedor: sirve para decirle al usuario
    cuándo reintentar en vez de un genérico "no puedo pensar".
    """

    def __init__(self, mensaje: str = "", espera_s: float | None = None) -> None:
        super().__init__(mensaje)
        self.espera_s = espera_s


class ToolCallRechazado(Exception):
    """El proveedor rechazó la salida (tool o JSON) por no cumplir el esquema. Es corregible."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON crudo tal como lo generó el modelo; se valida después


@dataclass(frozen=True)
class LLMRespuesta:
    contenido: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    modelo: str = ""  # qué modelo respondió
    respaldo: bool = False  # True si respondió un modelo de respaldo y no el principal


class LLM(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        json: bool = False,
    ) -> LLMRespuesta:
        """`json=True` fuerza una respuesta en JSON válido (el prompt debe mencionar "JSON")."""
        ...
