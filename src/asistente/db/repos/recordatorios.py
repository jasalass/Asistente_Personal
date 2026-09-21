from datetime import datetime
from uuid import UUID

from asistente.db.connection import Conn
from asistente.db.models import Recordatorio


class RecordatorioRepo:
    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def crear(self, texto: str, fecha: datetime) -> Recordatorio:
        if fecha.tzinfo is None:
            raise ValueError("La fecha del recordatorio debe incluir zona horaria")
        fila = self._conn.execute(
            "insert into recordatorios (texto, fecha) values (%s, %s) returning *",
            (texto, fecha),
        ).fetchone()
        return Recordatorio.model_validate(fila)

    def vencidos(self, ahora: datetime) -> list[Recordatorio]:
        filas = self._conn.execute(
            "select * from recordatorios where not enviado and fecha <= %s order by fecha",
            (ahora,),
        ).fetchall()
        return [Recordatorio.model_validate(f) for f in filas]

    def marcar_enviado(self, recordatorio_id: UUID) -> None:
        self._conn.execute(
            "update recordatorios set enviado = true where id = %s", (recordatorio_id,)
        )
