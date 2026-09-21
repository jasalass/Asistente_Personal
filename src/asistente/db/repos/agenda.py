from datetime import date
from enum import Enum
from typing import Any
from uuid import UUID

from psycopg import sql

from asistente.db.connection import Conn
from asistente.db.models import AccionExcepcion, Evento, EventoActualizacion, EventoNuevo


def _valor(v: Any) -> Any:
    return v.value if isinstance(v, Enum) else v


# (accion, motivo) por evento y fecha
Excepciones = dict[tuple[UUID, date], tuple[AccionExcepcion, str | None]]


class EventoRepo:
    """Los eventos se desactivan (`activo = false`), no se borran."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def disponible(self) -> bool:
        """¿Existen las tablas de la agenda (migración 0004)? Sin ellas, lo demás debe seguir andando.

        Se consulta el catálogo en vez de intentar leer la tabla: un error de SQL dejaría abortada
        la transacción entera, y con ella los avisos y recordatorios que no tienen que ver.
        """
        fila = self._conn.execute(
            "select to_regclass('public.eventos_agenda') is not null as ok"
        ).fetchone()
        return bool(fila["ok"])

    def crear(self, nuevo: EventoNuevo) -> Evento:
        datos = {k: _valor(v) for k, v in nuevo.model_dump().items()}
        columnas = sql.SQL(", ").join(sql.Identifier(k) for k in datos)
        marcadores = sql.SQL(", ").join(sql.Placeholder() for _ in datos)
        fila = self._conn.execute(
            sql.SQL("insert into eventos_agenda ({}) values ({}) returning *").format(
                columnas, marcadores
            ),
            list(datos.values()),
        ).fetchone()
        return Evento.model_validate(fila)

    def obtener(self, evento_id: UUID) -> Evento | None:
        fila = self._conn.execute(
            "select * from eventos_agenda where id = %s", (evento_id,)
        ).fetchone()
        return Evento.model_validate(fila) if fila else None

    def listar(self, *, solo_activos: bool = False) -> list[Evento]:
        filas = self._conn.execute(
            "select * from eventos_agenda where (not %s or activo) order by hora, nombre",
            (solo_activos,),
        ).fetchall()
        return [Evento.model_validate(f) for f in filas]

    def buscar_por_nombre(self, texto: str, limite: int = 10) -> list[Evento]:
        patron = "%" + texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        filas = self._conn.execute(
            "select * from eventos_agenda where nombre ilike %s order by nombre limit %s",
            (patron, limite),
        ).fetchall()
        return [Evento.model_validate(f) for f in filas]

    def actualizar(self, evento_id: UUID, cambios: EventoActualizacion) -> Evento | None:
        actual = self.obtener(evento_id)
        if actual is None:
            return None
        campos = {k: getattr(cambios, k) for k in cambios.model_fields_set}
        if not campos:
            return actual
        # El resultado debe seguir siendo un evento válido (p. ej. vigencia coherente).
        base = actual.model_dump(include=set(EventoNuevo.model_fields))
        campos_norm = EventoNuevo.model_validate({**base, **campos}).model_dump()
        campos = {k: campos_norm[k] for k in campos}

        asignaciones = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(k)) for k in campos
        )
        fila = self._conn.execute(
            sql.SQL("update eventos_agenda set {} where id = %s returning *").format(asignaciones),
            [*(_valor(v) for v in campos.values()), evento_id],
        ).fetchone()
        return Evento.model_validate(fila)

    def registrar_excepcion(
        self, evento_id: UUID, fecha: date, accion: AccionExcepcion, motivo: str | None = None
    ) -> None:
        """Una sola excepción por evento y fecha: la última decisión reemplaza a la anterior."""
        self._conn.execute(
            "insert into excepciones_agenda (evento_id, fecha, accion, motivo) "
            "values (%s, %s, %s, %s) "
            "on conflict (evento_id, fecha) do update "
            "set accion = excluded.accion, motivo = excluded.motivo",
            (evento_id, fecha, accion.value, motivo),
        )

    def excepciones(self, desde: date, hasta: date) -> Excepciones:
        filas = self._conn.execute(
            "select evento_id, fecha, accion, motivo from excepciones_agenda "
            "where fecha between %s and %s",
            (desde, hasta),
        ).fetchall()
        return {
            (f["evento_id"], f["fecha"]): (AccionExcepcion(f["accion"]), f["motivo"])
            for f in filas
        }
