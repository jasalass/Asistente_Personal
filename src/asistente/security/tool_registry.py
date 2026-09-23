from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from asistente.security.approvals import payload_hash


class Level(StrEnum):
    AUTO = "auto"  # el agente ejecuta solo (leer, resumir, clasificar, recordar, investigar)
    PROPONE = "propone"  # se registra una propuesta y espera el OK del owner
    PROHIBIDO = "prohibido"  # nunca se ejecuta (gastar dinero, contactar terceros sin revisión)


class ToolDenied(Exception):
    pass


class ToolArgsInvalid(Exception):
    """Los argumentos que generó el LLM no cumplen el esquema de la tool."""


class ToolError(Exception):
    """Fallo esperable dentro de una tool (p. ej. id inexistente). Se devuelve al LLM."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    level: Level
    description: str
    handler: Callable[..., Any]
    params: type[BaseModel] | None = None  # esquema de argumentos; None = sin validar
    # Grupo opcional: sus tools no se muestran ni se pueden invocar hasta que se active el grupo.
    # Ahorra tokens con las poco usadas (el esquema viaja en cada llamada al modelo).
    grupo: str | None = None


@dataclass(frozen=True)
class Proposal:
    """Acción que NO se ejecutó: queda a la espera de aprobación del owner."""

    tool: str
    args: dict[str, Any]
    hash: str


_OMITIDOS = frozenset(
    {"title", "default", "$defs", "additionalProperties", "minLength", "maxLength", "exclusiveMinimum"}
)


def compactar_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Reduce el JSON Schema de Pydantic a lo que el LLM necesita, para gastar menos tokens.

    Inlinea $defs, quita `title` y `default`, y colapsa `anyOf [X, null]` a X. También omite las
    restricciones que el sistema ya valida por su cuenta (largos, campos extra): además de gastar
    tokens, cada una es otra ocasión para que el proveedor rechace la llamada con un 400.
    """
    defs = schema.get("$defs", {})

    def limpiar(nodo: Any) -> Any:
        if isinstance(nodo, list):
            return [limpiar(x) for x in nodo]
        if not isinstance(nodo, dict):
            return nodo
        if "$ref" in nodo:
            return limpiar(defs[nodo["$ref"].rsplit("/", 1)[-1]])
        if "anyOf" in nodo:
            reales = [x for x in nodo["anyOf"] if x.get("type") != "null"]
            if len(reales) == 1:
                resto = {k: v for k, v in nodo.items() if k != "anyOf"}
                return limpiar({**reales[0], **resto})
        salida = {}
        for k, v in nodo.items():
            if k in _OMITIDOS:
                continue
            # Los nombres de propiedades son datos, no metadatos: se conservan tal cual.
            salida[k] = {n: limpiar(s) for n, s in v.items()} if k == "properties" else limpiar(v)
        return salida

    return limpiar(schema)


class ToolRegistry:
    """Default-deny: solo se puede invocar lo registrado, y el nivel lo decide el código."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._grupos_activos: set[str] = set()
        self._niveles_originales: dict[str, Level] = {}  # el nivel del código, nunca se pisa

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool ya registrada: {spec.name}")
        self._tools[spec.name] = spec
        self._niveles_originales[spec.name] = spec.level

    def activar_grupo(self, grupo: str) -> list[str]:
        """Habilita un grupo de tools; devuelve sus nombres. Solo dura lo que este registro."""
        if not any(s.grupo == grupo for s in self._tools.values()):
            raise ValueError(f"Grupo desconocido: {grupo}")
        self._grupos_activos.add(grupo)
        return [s.name for s in self._tools.values() if s.grupo == grupo]

    def _visible(self, spec: ToolSpec) -> bool:
        return spec.grupo is None or spec.grupo in self._grupos_activos

    def get(self, name: str) -> ToolSpec:
        spec = self._tools.get(name)
        if spec is None:
            raise ToolDenied(f"Tool no registrada: {name}")
        if not self._visible(spec):
            raise ToolDenied(f"La herramienta {name} no está habilitada todavía")
        return spec

    def spec_de(self, name: str) -> ToolSpec | None:
        """Como get(), pero sin la comprobación de visibilidad por grupo: para inspeccionar o
        reconfigurar una tool (p. ej. su nivel), no para ejecutarla."""
        return self._tools.get(name)

    def aplicar_overrides(self, niveles: dict[str, Level]) -> None:
        """El dueño puede volver más estricta (auto->propone) o más laxa (propone->auto) una tool
        desde el chat, pero nunca una prohibida: ese techo solo lo cambia el código. Se compara
        siempre contra el nivel original, no contra el efectivo: así da igual el orden o cuántas
        veces se llame."""
        for nombre, nivel in niveles.items():
            original = self._niveles_originales.get(nombre)
            if original is None or original is Level.PROHIBIDO or nivel is Level.PROHIBIDO:
                continue
            self._tools[nombre] = replace(self._tools[nombre], level=nivel)

    def quitar_override(self, nombre: str) -> None:
        """Vuelve una tool a su nivel original. No hace nada si no estaba registrada."""
        original = self._niveles_originales.get(nombre)
        if original is not None:
            self._tools[nombre] = replace(self._tools[nombre], level=original)

    def niveles(self) -> dict[str, Level]:
        """Nivel efectivo de cada tool visible (ni las prohibidas ni las de un grupo sin activar)."""
        return {s.name: s.level for s in self._tools.values() if s.level is not Level.PROHIBIDO and self._visible(s)}

    def definiciones(self) -> list[dict[str, Any]]:
        """Esquemas para el LLM. Ni las prohibidas ni las de grupos sin activar se le muestran."""
        vacio = {"type": "object", "properties": {}}
        return [
            {
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": s.description,
                    "parameters": (
                        compactar_schema(s.params.model_json_schema()) if s.params else vacio
                    ),
                },
            }
            for s in self._tools.values()
            if s.level is not Level.PROHIBIDO and self._visible(s)
        ]

    @staticmethod
    def _validar(spec: ToolSpec, args: dict[str, Any]) -> dict[str, Any]:
        if spec.params is None:
            return args
        try:
            validado = spec.params.model_validate(args)
        except ValidationError as e:
            # Solo ubicación y motivo: nunca el valor recibido.
            detalle = "; ".join(
                f"{'.'.join(str(x) for x in err['loc']) or 'argumentos'}: {err['msg']}"
                for err in e.errors()
            )
            raise ToolArgsInvalid(detalle) from None
        return validado.model_dump(exclude_unset=True)

    def invoke(self, name: str, args: dict[str, Any]) -> Any | Proposal:
        """AUTO ejecuta; PROPONE devuelve una Proposal sin ejecutar; PROHIBIDO lanza."""
        spec = self.get(name)
        if spec.level is Level.PROHIBIDO:
            raise ToolDenied(f"Tool prohibida: {name}")
        kwargs = self._validar(spec, args)
        if spec.level is Level.PROPONE:
            return Proposal(tool=name, args=args, hash=payload_hash(name, args))
        return spec.handler(**kwargs)

    def execute_approved(self, proposal: Proposal, *, approved_hash: str) -> Any:
        """Ejecuta una propuesta solo si el hash aprobado coincide con el payload exacto."""
        spec = self.get(proposal.tool)
        if spec.level is not Level.PROPONE:
            raise ToolDenied(f"La tool {proposal.tool} no admite ejecución por aprobación")
        if approved_hash != payload_hash(proposal.tool, proposal.args):
            raise ToolDenied("El payload no coincide con lo aprobado")
        return spec.handler(**self._validar(spec, proposal.args))
