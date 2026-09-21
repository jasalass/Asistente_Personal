from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from psycopg import sql

from asistente.db.connection import Conn
from asistente.db.models import (
    EventoTipo,
    Proceso,
    ProcesoActualizacion,
    ProcesoEstado,
    ProcesoEvento,
    ProcesoNuevo,
)

# Estados en los que tiene sentido que el heartbeat vuelva a mirar el proceso.
_ESTADOS_VIGILABLES = ("activo", "en_espera", "bloqueado")


def _valor(v: Any) -> Any:
    return v.value if isinstance(v, Enum) else v


class ProcesoRepo:
    """No hay borrado: un proceso que ya no importa se cancela y su historial se conserva."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def crear(self, nuevo: ProcesoNuevo) -> Proceso:
        datos = {k: _valor(v) for k, v in nuevo.model_dump().items()}
        columnas = sql.SQL(", ").join(sql.Identifier(k) for k in datos)
        marcadores = sql.SQL(", ").join(sql.Placeholder() for _ in datos)
        fila = self._conn.execute(
            sql.SQL("insert into procesos ({}) values ({}) returning *").format(
                columnas, marcadores
            ),
            list(datos.values()),
        ).fetchone()
        proceso = Proceso.model_validate(fila)
        self.agregar_evento(proceso.id, EventoTipo.NOTA, "Proceso creado")
        return proceso

    def obtener(self, proceso_id: UUID) -> Proceso | None:
        fila = self._conn.execute(
            "select * from procesos where id = %s", (proceso_id,)
        ).fetchone()
        return Proceso.model_validate(fila) if fila else None

    def listar(
        self, estados: Sequence[ProcesoEstado] | None = None, limite: int = 50
    ) -> list[Proceso]:
        filas = self._conn.execute(
            """
            select * from procesos
            where %(todos)s or estado = any(%(estados)s::proceso_estado[])
            order by prioridad desc, proxima_accion_fecha nulls last, actualizado_en desc
            limit %(limite)s
            """,
            {
                "todos": estados is None,
                "estados": [_valor(e) for e in estados or []],
                "limite": limite,
            },
        ).fetchall()
        return [Proceso.model_validate(f) for f in filas]

    def buscar_por_nombre(self, texto: str, limite: int = 10) -> list[Proceso]:
        patron = "%" + texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        filas = self._conn.execute(
            "select * from procesos where nombre ilike %s order by actualizado_en desc limit %s",
            (patron, limite),
        ).fetchall()
        return [Proceso.model_validate(f) for f in filas]

    def actualizar(self, proceso_id: UUID, cambios: ProcesoActualizacion) -> Proceso | None:
        campos = {k: _valor(getattr(cambios, k)) for k in cambios.model_fields_set}
        if not campos:
            return self.obtener(proceso_id)

        previo = self._conn.execute(
            "select estado from procesos where id = %s for update", (proceso_id,)
        ).fetchone()
        if previo is None:
            return None

        asignaciones = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(k)) for k in campos
        )
        fila = self._conn.execute(
            sql.SQL("update procesos set {} where id = %s returning *").format(asignaciones),
            [*campos.values(), proceso_id],
        ).fetchone()

        if "estado" in campos and campos["estado"] != previo["estado"]:
            self.agregar_evento(
                proceso_id,
                EventoTipo.CAMBIO_ESTADO,
                f"{previo['estado']} -> {campos['estado']}",
            )
        return Proceso.model_validate(fila)

    def agregar_evento(
        self, proceso_id: UUID, tipo: EventoTipo, contenido: str
    ) -> ProcesoEvento:
        fila = self._conn.execute(
            # clock_timestamp(): now() es fijo dentro de una transacción y empataría el orden.
            "insert into proceso_eventos (proceso_id, tipo, contenido, creado_en) "
            "values (%s, %s, %s, clock_timestamp()) returning *",
            (proceso_id, tipo.value, contenido),
        ).fetchone()
        return ProcesoEvento.model_validate(fila)

    def eventos(self, proceso_id: UUID, limite: int = 20) -> list[ProcesoEvento]:
        filas = self._conn.execute(
            "select * from proceso_eventos where proceso_id = %s "
            "order by creado_en desc, id limit %s",
            (proceso_id, limite),
        ).fetchall()
        return [ProcesoEvento.model_validate(f) for f in filas]

    def pendientes_de_chequeo(self, ahora: datetime) -> list[Proceso]:
        """Procesos vigilables cuyo último chequeo ya venció según su frecuencia."""
        filas = self._conn.execute(
            """
            select * from procesos
            where frecuencia_chequeo_dias is not null
              and estado = any(%s::proceso_estado[])
              and (ultimo_chequeo is null
                   or ultimo_chequeo + make_interval(days => frecuencia_chequeo_dias) <= %s)
            order by prioridad desc, ultimo_chequeo nulls first
            """,
            (list(_ESTADOS_VIGILABLES), ahora),
        ).fetchall()
        return [Proceso.model_validate(f) for f in filas]

    def marcar_chequeado(self, proceso_id: UUID, ahora: datetime) -> None:
        self._conn.execute(
            "update procesos set ultimo_chequeo = %s where id = %s", (ahora, proceso_id)
        )
