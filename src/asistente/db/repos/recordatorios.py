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

    def entre(self, desde: datetime, hasta: datetime) -> list[Recordatorio]:
        """Recordatorios con fecha en [desde, hasta), enviados o no (para mostrar la agenda)."""
        filas = self._conn.execute(
            "select * from recordatorios where fecha >= %s and fecha < %s order by fecha",
            (desde, hasta),
        ).fetchall()
        return [Recordatorio.model_validate(f) for f in filas]

    def obtener(self, recordatorio_id: UUID) -> Recordatorio | None:
        fila = self._conn.execute(
            "select * from recordatorios where id = %s", (recordatorio_id,)
        ).fetchone()
        return Recordatorio.model_validate(fila) if fila else None

    def buscar_por_texto(self, texto: str, limite: int = 8) -> list[Recordatorio]:
        patron = "%" + texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        filas = self._conn.execute(
            "select * from recordatorios where texto ilike %s order by fecha desc limit %s",
            (patron, limite),
        ).fetchall()
        return [Recordatorio.model_validate(f) for f in filas]

    def eliminar(self, recordatorio_id: UUID) -> bool:
        """Cancela un recordatorio (borra la fila). True si existía."""
        fila = self._conn.execute(
            "delete from recordatorios where id = %s returning id", (recordatorio_id,)
        ).fetchone()
        return fila is not None

    def marcar_enviado(self, recordatorio_id: UUID) -> None:
        self._conn.execute(
            "update recordatorios set enviado = true where id = %s", (recordatorio_id,)
        )
