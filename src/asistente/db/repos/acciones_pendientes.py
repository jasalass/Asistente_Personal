from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from asistente.db.connection import Conn
from asistente.db.models import AccionPendiente


class AccionesPendientesRepo:
    """Tools de nivel `propone`: quedan aquí en vez de ejecutarse, a la espera de Aprobar/Rechazar."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def crear(self, tool: str, args: dict[str, Any], payload_hash: str) -> AccionPendiente:
        fila = self._conn.execute(
            "insert into acciones_pendientes (tool, args, payload_hash) values (%s, %s, %s) "
            "returning *",
            (tool, Jsonb(args), payload_hash),
        ).fetchone()
        return AccionPendiente.model_validate(fila)

    def obtener(self, accion_id: UUID) -> AccionPendiente | None:
        fila = self._conn.execute(
            "select * from acciones_pendientes where id = %s", (accion_id,)
        ).fetchone()
        return AccionPendiente.model_validate(fila) if fila else None

    def pendientes_ids(self) -> list[UUID]:
        """Para re-registrar los botones de Discord al arrancar: sin esto, un reinicio deja los
        botones de los mensajes viejos sin nada que los atienda."""
        filas = self._conn.execute(
            "select id from acciones_pendientes where estado = 'pendiente'"
        ).fetchall()
        return [f["id"] for f in filas]

    def _resolver(
        self, accion_id: UUID, estado: str, resuelto_por: str, resultado: dict[str, Any] | None
    ) -> AccionPendiente | None:
        """Solo transiciona una fila que sigue 'pendiente': evita una doble aprobación por carrera."""
        fila = self._conn.execute(
            "update acciones_pendientes set estado = %s, resuelta_en = now(), "
            "resuelto_por = %s, resultado = %s where id = %s and estado = 'pendiente' returning *",
            (estado, resuelto_por, Jsonb(resultado) if resultado is not None else None, accion_id),
        ).fetchone()
        return AccionPendiente.model_validate(fila) if fila else None

    def marcar_ejecutada(
        self, accion_id: UUID, resuelto_por: str, resultado: dict[str, Any]
    ) -> AccionPendiente | None:
        """El dueño aprobó y la tool corrió bien."""
        return self._resolver(accion_id, "ejecutada", resuelto_por, resultado)

    def marcar_aprobada_con_error(
        self, accion_id: UUID, resuelto_por: str, error: str
    ) -> AccionPendiente | None:
        """El dueño aprobó, pero la tool falló al ejecutarse (no queda como 'pendiente' otra vez)."""
        return self._resolver(accion_id, "aprobada", resuelto_por, {"error": error})

    def marcar_rechazada(self, accion_id: UUID, resuelto_por: str) -> AccionPendiente | None:
        return self._resolver(accion_id, "rechazada", resuelto_por, None)

    def vencidas(self, ahora: Any) -> list[AccionPendiente]:
        filas = self._conn.execute(
            "select * from acciones_pendientes where estado = 'pendiente' and expira_en <= %s "
            "order by expira_en",
            (ahora,),
        ).fetchall()
        return [AccionPendiente.model_validate(f) for f in filas]

    def marcar_expirada(self, accion_id: UUID) -> None:
        self._conn.execute(
            "update acciones_pendientes set estado = 'expirada', resuelta_en = now() "
            "where id = %s and estado = 'pendiente'",
            (accion_id,),
        )
