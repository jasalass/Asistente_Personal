import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.llm.base import LLM, ToolCall, ToolCallRechazado
from asistente.security.tool_registry import (
    Proposal,
    ToolArgsInvalid,
    ToolDenied,
    ToolError,
    ToolRegistry,
)

MSG_SIN_RESPUESTA = "No logré completar la tarea en el número de pasos permitido. Intenta de nuevo."


@dataclass
class ResultadoAgente:
    respuesta: str
    propuestas: list[Proposal] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    pasos: int = 0
    usa_respaldo: bool = False  # alguna llamada la atendió un modelo de respaldo
    modelos: set[str] = field(default_factory=set)


def ejecutar_agente(
    mensaje: str,
    *,
    llm: LLM,
    registro: ToolRegistry,
    auditoria: AuditoriaRepo,
    system_prompt: str,
    historial: Sequence[dict[str, Any]] = (),
    max_pasos: int = 6,
) -> ResultadoAgente:
    """Bucle de tool calling. El tope de pasos evita ciclos y gasto de cuota descontrolado."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *historial,
        {"role": "user", "content": mensaje},
    ]
    resultado = ResultadoAgente(respuesta=MSG_SIN_RESPUESTA)

    for _ in range(max_pasos):
        resultado.pasos += 1
        try:
            # Se recalcula en cada paso: una tool puede habilitar un grupo (p. ej. el vigía).
            r = llm.chat(messages, registro.definiciones())
        except ToolCallRechazado as e:
            # El proveedor rechazó los argumentos por el esquema: se le informa para que corrija.
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
            salida = _ejecutar_tool(tc, registro, auditoria, resultado)
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
    tc: ToolCall, registro: ToolRegistry, auditoria: AuditoriaRepo, resultado: ResultadoAgente
) -> Any:
    """Nunca lanza: todo fallo se devuelve al modelo como {"error": ...} para que se corrija."""
    try:
        args = json.loads(tc.arguments or "{}")
    except ValueError:
        args = None
    if not isinstance(args, dict):
        auditoria.registrar("agente", f"tool:{tc.name}", {"estado": "args_no_json"})
        return {"error": "Los argumentos deben ser un objeto JSON válido."}

    try:
        salida = registro.invoke(tc.name, args)
    except ToolDenied as e:
        auditoria.registrar("agente", f"tool:{tc.name}", {"estado": "denegada", "args": args})
        return {"error": str(e)}
    except (ToolArgsInvalid, ToolError, ValidationError) as e:
        auditoria.registrar(
            "agente", f"tool:{tc.name}", {"estado": "error", "args": args, "motivo": str(e)}
        )
        return {"error": str(e)}

    if isinstance(salida, Proposal):
        resultado.propuestas.append(salida)
        auditoria.registrar("agente", f"tool:{tc.name}", {"estado": "propuesta", "args": args})
        return {"estado": "pendiente_de_aprobacion", "mensaje": "El usuario debe aprobarla."}

    auditoria.registrar("agente", f"tool:{tc.name}", {"estado": "ok", "args": args})
    return salida
