from collections.abc import Sequence

from asistente.db.connection import Conn
from asistente.db.models import Memoria, MemoriaOrigen


class MemoriaExternaNoConfirmada(Exception):
    pass


class MemoriaRepo:
    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def guardar(
        self,
        contenido: str,
        origen: MemoriaOrigen,
        etiquetas: Sequence[str] = (),
        *,
        confirmado_por_owner: bool = False,
    ) -> Memoria:
        """Lo de origen externo (web, emails) nunca se guarda solo: requiere confirmación."""
        if origen is MemoriaOrigen.EXTERNO and not confirmado_por_owner:
            raise MemoriaExternaNoConfirmada(
                "Una memoria de origen externo requiere confirmación del owner"
            )
        fila = self._conn.execute(
            "insert into memorias (contenido, origen, etiquetas) values (%s, %s, %s) "
            "returning id, contenido, origen, etiquetas, creado_en",
            (contenido, origen.value, list(etiquetas)),
        ).fetchone()
        return Memoria.model_validate(fila)

    def buscar(self, consulta: str, limite: int = 5) -> list[Memoria]:
        filas = self._conn.execute(
            """
            select m.id, m.contenido, m.origen, m.etiquetas, m.creado_en
            from memorias m, websearch_to_tsquery('spanish', %s) q
            where m.busqueda @@ q
            order by ts_rank(m.busqueda, q) desc, m.creado_en desc
            limit %s
            """,
            (consulta, limite),
        ).fetchall()
        return [Memoria.model_validate(f) for f in filas]

    def recientes(self, limite: int = 10) -> list[Memoria]:
        filas = self._conn.execute(
            "select id, contenido, origen, etiquetas, creado_en from memorias "
            "order by creado_en desc limit %s",
            (limite,),
        ).fetchall()
        return [Memoria.model_validate(f) for f in filas]
