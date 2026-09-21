from asistente.db.connection import Conn


class EstadoSistemaRepo:
    """Kill switch: el heartbeat y el bot consultan `pausado` antes de actuar."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def pausado(self) -> bool:
        fila = self._conn.execute("select pausado from estado_sistema").fetchone()
        # Si la fila no existe, es más seguro asumir pausa que seguir actuando.
        return True if fila is None else fila["pausado"]

    def set_pausado(self, valor: bool) -> None:
        self._conn.execute("update estado_sistema set pausado = %s", (valor,))
