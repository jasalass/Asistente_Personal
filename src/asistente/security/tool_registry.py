from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from asistente.security.approvals import payload_hash


class Level(StrEnum):
    AUTO = "auto"  # el agente ejecuta solo (leer, resumir, clasificar, recordar, investigar)
    PROPONE = "propone"  # se registra una propuesta y espera el OK del owner
    PROHIBIDO = "prohibido"  # nunca se ejecuta (gastar dinero, contactar terceros sin revisión)


class ToolDenied(Exception):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    level: Level
    description: str
    handler: Callable[..., Any]


@dataclass(frozen=True)
class Proposal:
    """Acción que NO se ejecutó: queda a la espera de aprobación del owner."""

    tool: str
    args: dict[str, Any]
    hash: str


class ToolRegistry:
    """Default-deny: solo se puede invocar lo registrado, y el nivel lo decide el código."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool ya registrada: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        spec = self._tools.get(name)
        if spec is None:
            raise ToolDenied(f"Tool no registrada: {name}")
        return spec

    def invoke(self, name: str, args: dict[str, Any]) -> Any | Proposal:
        """AUTO ejecuta; PROPONE devuelve una Proposal sin ejecutar; PROHIBIDO lanza."""
        spec = self.get(name)
        if spec.level is Level.PROHIBIDO:
            raise ToolDenied(f"Tool prohibida: {name}")
        if spec.level is Level.PROPONE:
            return Proposal(tool=name, args=args, hash=payload_hash(name, args))
        return spec.handler(**args)

    def execute_approved(self, proposal: Proposal, *, approved_hash: str) -> Any:
        """Ejecuta una propuesta solo si el hash aprobado coincide con el payload exacto."""
        spec = self.get(proposal.tool)
        if spec.level is not Level.PROPONE:
            raise ToolDenied(f"La tool {proposal.tool} no admite ejecución por aprobación")
        if approved_hash != payload_hash(proposal.tool, proposal.args):
            raise ToolDenied("El payload no coincide con lo aprobado")
        return spec.handler(**proposal.args)
