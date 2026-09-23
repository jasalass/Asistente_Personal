"""Traza paso a paso de una ejecución del agente (llamadas al LLM y a las tools), para depurar
sin adivinar. Cada mensaje que pasa por el agente genera un traza_id; cada paso se inserta al
ocurrir, no hay que actualizar nada después (la tabla es append-only, igual que auditoria)."""

from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from asistente.db.connection import Conn

TipoPaso = Literal["llm", "tool", "atajo"]

_LIMITE_STR = 1000
_LIMITE_ITEMS = 20


def _apto_para_jsonb(valor: Any, profundidad: int = 0) -> Any:
    """Recorta lo que se guarda: nunca un jsonb enorme por un resultado largo o muy anidado."""
    if valor is None or isinstance(valor, bool | int | float):
        return valor
    if isinstance(valor, str):
        return valor if len(valor) <= _LIMITE_STR else valor[:_LIMITE_STR] + "…(truncado)"
    if profundidad >= 4:
        return "…(demasiado anidado)"
    if isinstance(valor, dict):
        items = list(valor.items())
        recortado = {k: _apto_para_jsonb(v, profundidad + 1) for k, v in items[:_LIMITE_ITEMS]}
        if len(items) > _LIMITE_ITEMS:
            recortado["_truncado"] = f"+{len(items) - _LIMITE_ITEMS} campos"
        return recortado
    if isinstance(valor, list | tuple):
        items = list(valor)
        recortado = [_apto_para_jsonb(v, profundidad + 1) for v in items[:_LIMITE_ITEMS]]
        if len(items) > _LIMITE_ITEMS:
            recortado.append(f"…+{len(items) - _LIMITE_ITEMS} más")
        return recortado
    return _apto_para_jsonb(str(valor), profundidad)


class TrazaRepo:
    """Solo inserta: el rol de la base no tiene permiso de UPDATE ni DELETE sobre esta tabla."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def registrar_paso(
        self,
        traza_id: UUID,
        orden: int,
        tipo: TipoPaso,
        nombre: str,
        *,
        duracion_ms: int | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        entrada: Any = None,
        salida: Any = None,
        error: str | None = None,
    ) -> None:
        self._conn.execute(
            "insert into traza_pasos "
            "(traza_id, orden, tipo, nombre, duracion_ms, tokens_in, tokens_out, entrada, salida, error) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                str(traza_id),
                orden,
                tipo,
                nombre,
                duracion_ms,
                tokens_in,
                tokens_out,
                Jsonb(_apto_para_jsonb(entrada)) if entrada is not None else None,
                Jsonb(_apto_para_jsonb(salida)) if salida is not None else None,
                error,
            ),
        )

    def disponible(self) -> bool:
        """False si la migración 0005_trazas.sql aún no se aplicó: no debe romper el agente.

        Se consulta el catálogo en vez de intentar leer la tabla: un error de SQL dejaría abortada
        la transacción entera, y con ella los avisos y recordatorios que no tienen que ver.
        """
        fila = self._conn.execute(
            "select to_regclass('public.traza_pasos') is not null as ok"
        ).fetchone()
        return bool(fila and fila["ok"])

    def para(self, traza_id: UUID) -> list[dict[str, Any]]:
        """Los pasos de una traza, en orden. Para depurar un mensaje puntual desde el SQL editor."""
        return self._conn.execute(
            "select orden, tipo, nombre, duracion_ms, tokens_in, tokens_out, entrada, salida, error, ts "
            "from traza_pasos where traza_id = %s order by orden",
            (str(traza_id),),
        ).fetchall()
