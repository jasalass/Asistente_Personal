import time
from typing import Any

import groq

from asistente.llm.base import LLMNoDisponible, LLMRespuesta, ToolCall, ToolCallRechazado

_RECHAZOS_CORREGIBLES = ("tool_use_failed", "json_validate_failed")
_REINTENTOS = 2
# Esperar sirve para el límite por minuto (segundos). Si Groq pide esperar más, es el cupo diario
# agotado: dormir no ayuda, y es mejor fallar de inmediato para que otro modelo tome el mensaje.
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
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        json: bool = False,
    ) -> LLMRespuesta:
        params: dict[str, Any] = {"model": self._modelo, "messages": messages, "temperature": 0}
        if tools:
            params.update(tools=tools, tool_choice="auto")
        if json:
            params["response_format"] = {"type": "json_object"}
        if self._reasoning_effort:
            params["reasoning_effort"] = self._reasoning_effort

        for intento in range(_REINTENTOS + 1):
            try:
                r = self._client.chat.completions.create(**params)
                break
            except groq.BadRequestError as e:
                cuerpo = e.body if isinstance(e.body, dict) else {}
                cuerpo = cuerpo.get("error", cuerpo)
                if cuerpo.get("code") not in _RECHAZOS_CORREGIBLES:
                    raise
                # Groq valida los argumentos (o el JSON) y los rechaza con un 400: el modelo puede
                # corregirse si se le dice qué falló (sin el texto que generó).
                raise ToolCallRechazado(str(cuerpo.get("message", "esquema inválido"))[:300]) from None
            except groq.RateLimitError as e:
                espera = _espera(e)
                if espera > _ESPERA_MAX_S:
                    raise LLMNoDisponible(
                        f"Cupo de {self._modelo} agotado (Groq pide esperar {int(espera)} s)"
                    ) from None
                if intento == _REINTENTOS:
                    raise LLMNoDisponible(f"Límite de uso de {self._modelo} alcanzado") from None
                time.sleep(espera)
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
            modelo=self._modelo,
        )


def _espera(e: groq.RateLimitError) -> float:
    """Segundos que Groq pide esperar (cabecera retry-after); 5 si no la trae o es ilegible."""
    try:
        return float(e.response.headers.get("retry-after", 5))
    except (TypeError, ValueError):
        return 5.0
