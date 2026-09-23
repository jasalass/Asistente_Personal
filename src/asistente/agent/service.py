import math
import time
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from asistente.agenda.atajos import detectar_consulta as detectar_consulta_de_agenda
from asistente.agenda.atajos import redactar as redactar_agenda
from asistente.agenda.consulta import consultar
from asistente.agenda.feriados import CalendarioFeriados, Feriados
from asistente.agent.loop import ResultadoAgente, ejecutar_agente
from asistente.agent.prompt import construir_prompt
from asistente.agent.tools import construir_registro
from asistente.db.connection import Conn
from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.db.repos.procesos import ProcesoRepo
from asistente.db.repos.sistema import EstadoSistemaRepo
from asistente.db.repos.trazas import TrazaRepo
from asistente.llm.base import LLM, LLMNoDisponible
from asistente.llm.presupuesto import presupuesto_de_espera
from asistente.procesos.atajos import detectar_consulta as detectar_consulta_de_procesos
from asistente.procesos.atajos import redactar as redactar_procesos

MSG_PAUSADO = "Estoy en pausa. Usa /reanudar para volver a activarme."
NOTA_RESPALDO = (
    "\n\n_(Respondí con el modelo de respaldo porque el principal agotó su cupo; "
    "revisa que lo que hice sea lo que pediste.)_"
)
MSG_LLM_CAIDO = "No puedo pensar en este momento (límite de uso o caída del servicio). Reintenta en unos minutos."
# Espera TOTAL tolerada por mensaje ante un límite por minuto de Groq. Pasado esto se le dice al
# usuario cuánto esperar, en vez de dejarlo mirando "escribiendo…" (un mensaje llegó a tardar 107 s).
PRESUPUESTO_ESPERA_S = 45.0


def _mensaje_sin_modelo(e: LLMNoDisponible) -> str:
    if e.espera_s:
        return (
            "Estoy al límite de uso del modelo (Groq). "
            f"Reintenta en unos {math.ceil(e.espera_s)} segundos."
        )
    return MSG_LLM_CAIDO


def _resultado_de_atajo(
    trazas: TrazaRepo, auditoria: AuditoriaRepo, inicio: float, nombre: str,
    consulta_etiqueta: str, resumen_salida: dict[str, Any], respuesta: str,
) -> ResultadoAgente:
    """Registra un atajo (consulta resuelta sin el modelo) igual que un mensaje normal, en 0 tokens."""
    traza_id = uuid4() if trazas.disponible() else None
    detalle: dict[str, Any] = {"atajo": nombre, "consulta": consulta_etiqueta}
    if traza_id:
        trazas.registrar_paso(
            traza_id, 1, "atajo", nombre,
            duracion_ms=int((time.monotonic() - inicio) * 1000),
            entrada={"consulta": consulta_etiqueta}, salida=resumen_salida,
        )
        detalle["traza"] = str(traza_id)
    auditoria.registrar_ejecucion(
        "mensaje", duracion_ms=int((time.monotonic() - inicio) * 1000),
        tokens_in=0, tokens_out=0, detalle=detalle,
    )
    return ResultadoAgente(respuesta=respuesta, pasos=0, traza_id=traza_id)


def responder(
    conn: Conn,
    mensaje: str,
    *,
    llm: LLM,
    tz: ZoneInfo,
    ahora: datetime,
    historial: Sequence[dict[str, Any]] = (),
    feriados: CalendarioFeriados | None = None,
) -> ResultadoAgente:
    """Punto de entrada por mensaje del owner: kill switch, agente y registro de la ejecución.

    La autorización del remitente (allowlist) es responsabilidad del bot, antes de llegar acá.
    """
    if EstadoSistemaRepo(conn).pausado():
        return ResultadoAgente(respuesta=MSG_PAUSADO)

    auditoria = AuditoriaRepo(conn)
    inicio = time.monotonic()

    trazas = TrazaRepo(conn)
    hoy = ahora.astimezone(tz).date()
    if consulta := detectar_consulta_de_agenda(mensaje, hoy):
        # "Qué tengo mañana": se responde desde el código, sin modelo (ver agenda/atajos.py).
        dias = consultar(conn, consulta.desde, consulta.dias, tz, feriados or Feriados())
        return _resultado_de_atajo(
            trazas, auditoria, inicio, "agenda", consulta.etiqueta,
            {"dias": len(dias)}, redactar_agenda(dias, consulta, hoy),
        )

    if consulta_p := detectar_consulta_de_procesos(mensaje):
        # "Cómo van mis procesos": mismo principio (ver procesos/atajos.py).
        lista = ProcesoRepo(conn).listar(consulta_p.estados, limite=50)
        return _resultado_de_atajo(
            trazas, auditoria, inicio, "procesos", consulta_p.etiqueta,
            {"procesos": len(lista)}, redactar_procesos(lista, consulta_p, tz),
        )

    try:
        with presupuesto_de_espera(PRESUPUESTO_ESPERA_S):
            resultado = ejecutar_agente(
                mensaje,
                llm=llm,
                registro=construir_registro(conn, tz, ahora, feriados),
                auditoria=auditoria,
                system_prompt=construir_prompt(ahora, tz),
                historial=historial,
                trazas=trazas,
            )
    except LLMNoDisponible as e:
        auditoria.registrar_ejecucion(
            "mensaje", duracion_ms=int((time.monotonic() - inicio) * 1000), error=str(e)
        )
        return ResultadoAgente(respuesta=_mensaje_sin_modelo(e))

    detalle = {
        "pasos": resultado.pasos,
        "propuestas": len(resultado.propuestas),
        "modelos": sorted(resultado.modelos),
        "respaldo": resultado.usa_respaldo,
    }
    if resultado.traza_id:
        detalle["traza"] = str(resultado.traza_id)
    auditoria.registrar_ejecucion(
        "mensaje",
        duracion_ms=int((time.monotonic() - inicio) * 1000),
        tokens_in=resultado.tokens_in,
        tokens_out=resultado.tokens_out,
        error="respuesta parcial: el modelo no estuvo disponible" if resultado.parcial else None,
        detalle=detalle,
    )
    if resultado.usa_respaldo:
        # El modelo de respaldo es menos fiable: el usuario debe saberlo para revisar lo hecho.
        resultado.respuesta += NOTA_RESPALDO
    return resultado
