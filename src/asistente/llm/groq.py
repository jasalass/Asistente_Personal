import time
from typing import Any

import groq

from asistente.llm.base import LLMNoDisponible, LLMRespuesta, ToolCall

_REINTENTOS = 2
_ESPERA_MAX_S = 30.0


class GroqLLM:
    def __init__(
        self, api_key: str, modelo: str, *, reasoning_effort: str | None = None
    ) -> None:
        self._client = groq.Groq(api_key=api_key, max_retries=0)
        self._modelo = modelo
        # Solo los modelos gpt-oss admiten controlar el esfuerzo de razonamiento.
        self._reasoning_effort = reasoning_effort if "gpt-oss" in modelo else None

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMRespuesta:
        params: dict[str, Any] = {"model": self._modelo, "messages": messages, "temperature": 0}
        if tools:
            params.update(tools=tools, tool_choice="auto")
        if self._reasoning_effort:
            params["reasoning_effort"] = self._reasoning_effort

        for intento in range(_REINTENTOS + 1):
            try:
                r = self._client.chat.completions.create(**params)
                break
            except groq.RateLimitError as e:
                if intento == _REINTENTOS:
                    raise LLMNoDisponible("Límite de uso de Groq alcanzado") from None
                time.sleep(_espera(e))
            except (groq.APIConnectionError, groq.InternalServerError):
                if intento == _REINTENTOS:
                    raise LLMNoDisponible("Groq no responde") from None
                time.sleep(2.0 * (intento + 1))

        msg = r.choices[0].message
        return LLMRespuesta(
            contenido=msg.content,
            tool_calls=[
                ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
                for tc in msg.tool_calls or []
            ],
            tokens_in=r.usage.prompt_tokens if r.usage else 0,
            tokens_out=r.usage.completion_tokens if r.usage else 0,
        )


def _espera(e: groq.RateLimitError) -> float:
    try:
        return min(float(e.response.headers.get("retry-after", 5)), _ESPERA_MAX_S)
    except (TypeError, ValueError):
        return 5.0
