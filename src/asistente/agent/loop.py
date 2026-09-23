import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError

from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.llm.base import LLM, LLMNoDisponible, ToolCall, ToolCallRechazado
from asistente.security.tool_registry import (
    Proposal,
    ToolArgsInvalid,
    ToolDenied,
    ToolError,
    ToolRegistry,
)


class Trazas(Protocol):
    """Lo que necesita el bucle para trazar: real (TrazaRepo) o de prueba (FakeTrazas)."""

    def disponible(self) -> bool: ...
    def registrar_paso(self, traza_id: UUID, orden: int, tipo: str, nombre: str, **kw: Any) -> None: ...

MSG_SIN_RESPUESTA = "No logré completar la tarea en el número de pasos permitido. Intenta de nuevo."
MSG_HECHO_SIN_RESPUESTA = (
    "Alcancé a hacer esto, pero no pude redactar la respuesta completa "
    "(límite de uso del modelo). Reintenta en un minuto si algo no quedó como querías:"
)

# Las herramientas que solo consultan no cuentan como "algo que se hizo".
_SOLO_LECTURA = ("listar_", "ver_", "buscar_", "habilitar_")


@dataclass
class ResultadoAgente:
    respuesta: str
    propuestas: list[Proposal] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    pasos: int = 0
    usa_respaldo: bool = False  # alguna llamada la atendió un modelo de respaldo
    modelos: set[str] = field(default_factory=set)
    acciones: list[str] = field(default_factory=list)  # lo que se cambió, en palabras del código
    parcial: bool = False  # se hizo trabajo pero no se pudo redactar la respuesta
    traza_id: UUID | None = None  # correlaciona con traza_pasos; None si no había tabla de trazas


class _Trazador:
    """Lleva el traza_id y el orden de los pasos. No hace nada si no hay tabla de trazas."""

    def __init__(self, trazas: Trazas | None) -> None:
        self.activo = trazas is not None and trazas.disponible()
        self._trazas = trazas
        self.id: UUID | None = uuid4() if self.activo else None
        self._orden = 0

    def paso(self, tipo: str, nombre: str, **kw: Any) -> None:
        if not self.activo:
            return
        self._orden += 1
        self._trazas.registrar_paso(self.id, self._orden, tipo, nombre, **kw)  # type: ignore[union-attr]


def _ms(desde: float) -> int:
    return int((time.monotonic() - desde) * 1000)


def _describir_accion(nombre: str, salida: Any) -> str | None:
    """Una línea que cuenta qué hizo una herramienta que modifica datos, sin depender del modelo."""
    if nombre.startswith(_SOLO_LECTURA):
        return None
    if isinstance(salida, dict):
        if salida.get("error"):
            return None  # falló: no hay nada hecho que contar
        if salida.get("resumen"):
            return f"• {salida['resumen']}"
        if isinstance(salida.get("cancelado"), dict):
            return f"• Cancelé el recordatorio «{salida['cancelado'].get('texto')}»"
        if salida.get("nombre") or salida.get("texto"):
            return f"• {nombre}: {salida.get('nombre') or salida.get('texto')}"
    return f"• {nombre}"


def ejecutar_agente(
    mensaje: str,
    *,
    llm: LLM,
    registro: ToolRegistry,
    auditoria: AuditoriaRepo,
    system_prompt: str,
    historial: Sequence[dict[str, Any]] = (),
    max_pasos: int = 6,
    trazas: Trazas | None = None,
) -> ResultadoAgente:
    """Bucle de tool calling. El tope de pasos evita ciclos y gasto de cuota descontrolado."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *historial,
        {"role": "user", "content": mensaje},
    ]
    trazador = _Trazador(trazas)
    resultado = ResultadoAgente(respuesta=MSG_SIN_RESPUESTA, traza_id=trazador.id)

    for _ in range(max_pasos):
        resultado.pasos += 1
        # Se recalcula en cada paso: una tool puede habilitar un grupo (p. ej. el vigía).
        tools = registro.definiciones()
        entrada_llm = {"mensajes": len(messages), "tools_ofrecidas": len(tools)}
        t0 = time.monotonic()
        try:
            r = llm.chat(messages, tools)
        except ToolCallRechazado as e:
            # El proveedor rechazó los argumentos por el esquema: se le informa para que corrija.
            trazador.paso("llm", "?", duracion_ms=_ms(t0), entrada=entrada_llm, error=str(e))
            auditoria.registrar("agente", "llm:tool_call_rechazado", {"motivo": str(e)})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Tu última llamada a una herramienta fue rechazada: {e}. "
                        "Vuelve a intentarla con argumentos que cumplan exactamente el esquema."
                    ),
                }
            )
            continue
        except LLMNoDisponible as e:
            trazador.paso("llm", "?", duracion_ms=_ms(t0), entrada=entrada_llm, error=str(e))
            if not resultado.acciones:
                raise  # no se hizo nada: el servicio responde con el aviso habitual
            # Ya hubo cambios (y se confirman en la base): decirlo, en vez de un "no puedo pensar"
            # que haría creer que no pasó nada.
            resultado.respuesta = MSG_HECHO_SIN_RESPUESTA + "\n" + "\n".join(resultado.acciones)
            resultado.parcial = True
            return resultado
        trazador.paso(
            "llm", r.modelo or "?", duracion_ms=_ms(t0), tokens_in=r.tokens_in, tokens_out=r.tokens_out,
            entrada=entrada_llm,
            salida=(
                {"tool_calls": [tc.name for tc in r.tool_calls]} if r.tool_calls
                else {"contenido": r.contenido}
            ),
        )
        resultado.tokens_in += r.tokens_in
        resultado.tokens_out += r.tokens_out
        resultado.usa_respaldo = resultado.usa_respaldo or r.respaldo
        if r.modelo:
            resultado.modelos.add(r.modelo)

        if not r.tool_calls:
            resultado.respuesta = (r.contenido or "").strip() or "Listo."
            return resultado

        messages.append(
            {
                "role": "assistant",
                "content": r.contenido,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                    for tc in r.tool_calls
                ],
            }
        )
        for tc in r.tool_calls:
            salida = _ejecutar_tool(tc, registro, auditoria, resultado, trazador)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": json.dumps(salida, default=str),
                }
            )
    return resultado


def _ejecutar_tool(
    tc: ToolCall, registro: ToolRegistry, auditoria: AuditoriaRepo, resultado: ResultadoAgente,
    trazador: _Trazador,
) -> Any:
    """Nunca lanza: todo fallo se devuelve al modelo como {"error": ...} para que se corrija."""
    t0 = time.monotonic()

    def paso(salida: Any, error: str | None, entrada: Any = None) -> None:
        trazador.paso(
            "tool", tc.name, duracion_ms=_ms(t0), entrada=entrada,
            salida=salida if error is None else None, error=error,
        )

    try:
        args = json.loads(tc.arguments or "{}")
    except ValueError:
        args = None
    if not isinstance(args, dict):
        auditoria.registrar("agente", f"tool:{tc.name}", {"estado": "args_no_json"})
        paso(None, "los argumentos no son un objeto JSON válido")
        return {"error": "Los argumentos deben ser un objeto JSON válido."}

    try:
        salida = registro.invoke(tc.name, args)
    except ToolDenied as e:
        auditoria.registrar("agente", f"tool:{tc.name}", {"estado": "denegada", "args": args})
        paso(None, str(e), entrada=args)
        return {"error": str(e)}
    except (ToolArgsInvalid, ToolError, ValidationError) as e:
        auditoria.registrar(
            "agente", f"tool:{tc.name}", {"estado": "error", "args": args, "motivo": str(e)}
        )
        paso(None, str(e), entrada=args)
        return {"error": str(e)}

    if isinstance(salida, Proposal):
        resultado.propuestas.append(salida)
        auditoria.registrar("agente", f"tool:{tc.name}", {"estado": "propuesta", "args": args})
        paso({"estado": "propuesta"}, None, entrada=args)
        return {"estado": "pendiente_de_aprobacion", "mensaje": "El usuario debe aprobarla."}

    auditoria.registrar("agente", f"tool:{tc.name}", {"estado": "ok", "args": args})
    paso(salida, None, entrada=args)
    if (linea := _describir_accion(tc.name, salida)) is not None:
        resultado.acciones.append(linea)
    return salida
