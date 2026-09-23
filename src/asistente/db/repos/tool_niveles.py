"""Niveles de autoridad que el dueño ajustó desde el chat (tabla tool_niveles).

Trabaja con el string plano del nivel ("auto"/"propone"), no con el enum `Level` de
`security.tool_registry`: ese módulo es la capa de seguridad y no debe depender de la capa de
datos, así que la conversión ocurre en agent/tools.py, donde ya se importan ambas.
"""

from asistente.db.connection import Conn


class NivelesRepo:
    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def obtener_todos(self) -> dict[str, str]:
        filas = self._conn.execute("select tool, nivel from tool_niveles").fetchall()
        return {f["tool"]: f["nivel"] for f in filas}

    def fijar(self, tool: str, nivel: str) -> None:
        self._conn.execute(
            "insert into tool_niveles (tool, nivel) values (%s, %s) "
            "on conflict (tool) do update set nivel = excluded.nivel",
            (tool, nivel),
        )

    def quitar(self, tool: str) -> None:
        """Vuelve al nivel que trae el código (sin fila = sin override)."""
        self._conn.execute("delete from tool_niveles where tool = %s", (tool,))

    def disponible(self) -> bool:
        """False si la migración 0006_aprobaciones.sql aún no se aplicó."""
        fila = self._conn.execute(
            "select to_regclass('public.tool_niveles') is not null as ok"
        ).fetchone()
        return bool(fila and fila["ok"])
