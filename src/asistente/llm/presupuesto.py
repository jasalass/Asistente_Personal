"""Tope de espera por mensaje, para que el bot nunca quede "pegado" esperando al proveedor.

Cuando Groq responde 429 (límite por minuto) conviene esperar, pero un mensaje puede necesitar
varias llamadas y cada una esperar de nuevo: un solo mensaje llegó a tardar 107 s. Este presupuesto
limita la espera TOTAL de un mensaje; al agotarse se responde al usuario cuánto debe esperar.

Usa una variable de contexto, que `asyncio.to_thread` propaga a los hilos de trabajo.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_limite: ContextVar[float | None] = ContextVar("limite_de_espera", default=None)


@contextmanager
def presupuesto_de_espera(segundos: float) -> Iterator[None]:
    token = _limite.set(time.monotonic() + segundos)
    try:
        yield
    finally:
        _limite.reset(token)


def restante() -> float | None:
    """Segundos de espera que quedan, o None si no hay tope."""
    limite = _limite.get()
    return None if limite is None else max(0.0, limite - time.monotonic())
