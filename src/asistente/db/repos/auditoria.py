from typing import Any, Literal

from psycopg.types.json import Jsonb

from asistente.db.connection import Conn

Actor = Literal["owner", "agente", "heartbeat", "sistema"]
TipoEjecucion = Literal["mensaje", "heartbeat", "vigia"]


class AuditoriaRepo:
    """Solo inserta: el rol de la base no tiene permiso de UPDATE ni DELETE sobre estas tablas."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def registrar(self, actor: Actor, accion: str, detalle: dict[str, Any] | None = None) -> None:
        self._conn.execute(
            "insert into auditoria (actor, accion, detalle) values (%s, %s, %s)",
            (actor, accion, Jsonb(detalle) if detalle is not None else None),
        )

    def aviso_ya_enviado(self, clave: str) -> bool:
        """El registro de un aviso enviado es también la marca que evita reenviarlo."""
        fila = self._conn.execute(
            "select 1 from auditoria where actor = 'heartbeat' and accion = 'aviso' "
            "and detalle->>'clave' = %s limit 1",
            (clave,),
        ).fetchone()
        return fila is not None

    def registrar_ejecucion(
        self,
        tipo: TipoEjecucion,
        *,
        duracion_ms: int | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        error: str | None = None,
        detalle: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            "insert into ejecuciones (tipo, duracion_ms, tokens_in, tokens_out, error, detalle) "
            "values (%s, %s, %s, %s, %s, %s)",
            (
                tipo,
                duracion_ms,
                tokens_in,
                tokens_out,
                error,
                Jsonb(detalle) if detalle is not None else None,
            ),
        )
