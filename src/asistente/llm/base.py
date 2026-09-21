from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMNoDisponible(Exception):
    """El proveedor no respondió (límite de uso agotado, caída, etc.)."""


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
