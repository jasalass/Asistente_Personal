from collections.abc import Iterable
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from psycopg import sql

from asistente.db.connection import Conn
from asistente.db.models import Tema, TemaActualizacion, TemaNuevo


def _valor(v: Any) -> Any:
    return v.value if isinstance(v, Enum) else v


class TemaRepo:
    """Los temas se desactivan (`activo = false`), no se borran: el historial de lo visto se conserva."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def crear(self, nuevo: TemaNuevo) -> Tema:
        datos = {k: _valor(v) for k, v in nuevo.model_dump().items()}
        columnas = sql.SQL(", ").join(sql.Identifier(k) for k in datos)
        marcadores = sql.SQL(", ").join(sql.Placeholder() for _ in datos)
        fila = self._conn.execute(
            sql.SQL("insert into temas_seguimiento ({}) values ({}) returning *").format(
                columnas, marcadores
            ),
            list(datos.values()),
        ).fetchone()
        return Tema.model_validate(fila)

    def obtener(self, tema_id: UUID) -> Tema | None:
        fila = self._conn.execute(
            "select * from temas_seguimiento where id = %s", (tema_id,)
        ).fetchone()
        return Tema.model_validate(fila) if fila else None

    def listar(self, *, solo_activos: bool = False) -> list[Tema]:
        filas = self._conn.execute(
            "select * from temas_seguimiento where (not %s or activo) order by nombre",
            (solo_activos,),
        ).fetchall()
        return [Tema.model_validate(f) for f in filas]

    def buscar_por_nombre(self, texto: str, limite: int = 10) -> list[Tema]:
        patron = "%" + texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        filas = self._conn.execute(
            "select * from temas_seguimiento where nombre ilike %s order by nombre limit %s",
            (patron, limite),
        ).fetchall()
        return [Tema.model_validate(f) for f in filas]

    def actualizar(self, tema_id: UUID, cambios: TemaActualizacion) -> Tema | None:
        actual = self.obtener(tema_id)
        if actual is None:
            return None
        campos = {k: getattr(cambios, k) for k in cambios.model_fields_set}
        if not campos:
            return actual
        # Valida que el resultado siga siendo coherente (p. ej. cada_x_dias con su intervalo).
        base = actual.model_dump(include=set(TemaNuevo.model_fields))
        TemaNuevo.model_validate({**base, **campos})

        asignaciones = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(k)) for k in campos
        )
        fila = self._conn.execute(
            sql.SQL("update temas_seguimiento set {} where id = %s returning *").format(
                asignaciones
            ),
            [*(_valor(v) for v in campos.values()), tema_id],
        ).fetchone()
        return Tema.model_validate(fila)

    def marcar_ejecucion(self, tema_id: UUID, ahora: datetime) -> None:
        self._conn.execute(
            "update temas_seguimiento set ultima_ejecucion = %s where id = %s", (ahora, tema_id)
        )


class ArticuloRepo:
    """Solo alta: lo ya visto no se modifica ni se borra."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def vistos(self, tema_id: UUID, urls: Iterable[str]) -> set[str]:
        filas = self._conn.execute(
            "select url from articulos_vistos where tema_id = %s and url = any(%s)",
            (tema_id, list(urls)),
        ).fetchall()
        return {f["url"] for f in filas}

    def registrar(self, tema_id: UUID, url: str, titulo: str) -> bool:
        """True si era nuevo; False si ya estaba (no falla ante duplicados)."""
        fila = self._conn.execute(
            "insert into articulos_vistos (tema_id, url, titulo) values (%s, %s, %s) "
            "on conflict (tema_id, url) do nothing returning id",
            (tema_id, url, titulo[:500]),
        ).fetchone()
        return fila is not None
