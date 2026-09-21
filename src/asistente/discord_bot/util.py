from collections import defaultdict, deque
from typing import Any

LIMITE_DISCORD = 1900  # el máximo real es 2000; se deja margen


def dividir_mensaje(texto: str, limite: int = LIMITE_DISCORD) -> list[str]:
    """Parte un texto largo respetando saltos de línea y espacios cuando puede."""
    texto = texto.strip()
    if not texto:
        return []
    trozos: list[str] = []
    while len(texto) > limite:
        corte = max(texto.rfind("\n", 0, limite), texto.rfind(" ", 0, limite))
        if corte < limite // 2:  # sin un buen punto de corte: corte duro
            corte = limite
        trozos.append(texto[:corte].rstrip())
        texto = texto[corte:].lstrip()
    if texto:
        trozos.append(texto)
    return trozos


class Historial:
    """Memoria conversacional por canal, solo en RAM y acotada (mensajes y tamaño).

    Guarda únicamente el texto del usuario y la respuesta final: nunca los resultados de tools.
    Se pierde al reiniciar; lo importante se persiste en la base a través de las tools.
    """

    def __init__(self, max_mensajes: int = 6, max_chars: int = 1200) -> None:
        self._max_chars = max_chars
        self._por_canal: defaultdict[int, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=max_mensajes)
        )

    def obtener(self, canal_id: int) -> list[dict[str, Any]]:
        return list(self._por_canal[canal_id])

    def agregar(self, canal_id: int, usuario: str, asistente: str) -> None:
        cola = self._por_canal[canal_id]
        cola.append({"role": "user", "content": usuario[: self._max_chars]})
        cola.append({"role": "assistant", "content": asistente[: self._max_chars]})

    def limpiar(self, canal_id: int) -> None:
        self._por_canal.pop(canal_id, None)
