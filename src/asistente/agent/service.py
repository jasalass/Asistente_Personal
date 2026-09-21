import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from asistente.agent.loop import ResultadoAgente, ejecutar_agente
from asistente.agent.prompt import construir_prompt
from asistente.agent.tools import construir_registro
from asistente.db.connection import Conn
from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.db.repos.sistema import EstadoSistemaRepo
from asistente.llm.base import LLM, LLMNoDisponible

MSG_PAUSADO = "Estoy en pausa. Usa /reanudar para volver a activarme."
NOTA_RESPALDO = (
    "\n\n_(Respondí con el modelo de respaldo porque el principal agotó su cupo; "
    "revisa que lo que hice sea lo que pediste.)_"
)
MSG_LLM_CAIDO = "No puedo pensar en este momento (límite de uso o caída del servicio). Reintenta en unos minutos."


def responder(
    conn: Conn,
    mensaje: str,
    *,
    llm: LLM,
    tz: ZoneInfo,
    ahora: datetime,
    historial: Sequence[dict[str, Any]] = (),
) -> ResultadoAgente:
    """Punto de entrada por mensaje del owner: kill switch, agente y registro de la ejecución.

    La autorización del remitente (allowlist) es responsabilidad del bot, antes de llegar acá.
    """
    if EstadoSistemaRepo(conn).pausado():
        return ResultadoAgente(respuesta=MSG_PAUSADO)

    auditoria = AuditoriaRepo(conn)
    inicio = time.monotonic()
    try:
        resultado = ejecutar_agente(
            mensaje,
            llm=llm,
            registro=construir_registro(conn, tz),
            auditoria=auditoria,
            system_prompt=construir_prompt(ahora, tz),
            historial=historial,
        )
    except LLMNoDisponible as e:
        auditoria.registrar_ejecucion(
            "mensaje", duracion_ms=int((time.monotonic() - inicio) * 1000), error=str(e)
        )
        return ResultadoAgente(respuesta=MSG_LLM_CAIDO)

    auditoria.registrar_ejecucion(
        "mensaje",
        duracion_ms=int((time.monotonic() - inicio) * 1000),
        tokens_in=resultado.tokens_in,
        tokens_out=resultado.tokens_out,
        detalle={
            "pasos": resultado.pasos,
            "propuestas": len(resultado.propuestas),
            "modelos": sorted(resultado.modelos),
            "respaldo": resultado.usa_respaldo,
        },
    )
    if resultado.usa_respaldo:
        # El modelo de respaldo es menos fiable: el usuario debe saberlo para revisar lo hecho.
        resultado.respuesta += NOTA_RESPALDO
    return resultado
