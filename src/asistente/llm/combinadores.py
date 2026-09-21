"""Envoltorios de LLM: respaldo entre modelos y conteo de tokens."""

import logging
import time
from dataclasses import replace
from typing import Any

from asistente.llm.base import LLM, LLMNoDisponible, LLMRespuesta

log = logging.getLogger(__name__)

# Si el principal falló por límite, no se le vuelve a preguntar (y a esperar) en cada mensaje.
_BLOQUEO_PRINCIPAL_S = 300.0


class LLMConRespaldo:
    """Usa el principal; si no está disponible, pasa al siguiente modelo de la lista.

    Solo se cubre la indisponibilidad (cupo agotado o caída). Los rechazos de esquema no, porque
    son errores del modelo que el bucle del agente ya sabe corregir.
    """

    def __init__(self, principal: LLM, *respaldos: LLM) -> None:
        self._principal = principal
        self._respaldos = respaldos
        self._principal_bloqueado_hasta = 0.0

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        json: bool = False,
    ) -> LLMRespuesta:
        error: LLMNoDisponible | None = None
        if time.monotonic() >= self._principal_bloqueado_hasta:
            try:
                return self._principal.chat(messages, tools, json=json)
            except LLMNoDisponible as e:
                error = e
                self._principal_bloqueado_hasta = time.monotonic() + _BLOQUEO_PRINCIPAL_S
                log.warning("Modelo principal no disponible (%s): se usa el respaldo", e)

        for respaldo in self._respaldos:
            try:
                return replace(respaldo.chat(messages, tools, json=json), respaldo=True)
            except LLMNoDisponible as e:
                error = e
        raise error or LLMNoDisponible("No hay modelos disponibles")


class ContadorLLM:
    """Acumula los tokens de todas las llamadas que pasan por él."""

    def __init__(self, llm: LLM) -> None:
        self._llm = llm
        self.tokens_in = 0
        self.tokens_out = 0

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        json: bool = False,
    ) -> LLMRespuesta:
        respuesta = self._llm.chat(messages, tools, json=json)
        self.tokens_in += respuesta.tokens_in
        self.tokens_out += respuesta.tokens_out
        return respuesta
