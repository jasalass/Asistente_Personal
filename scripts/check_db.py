"""Verifica la conexión y los permisos del rol asistente_bot. No deja datos: todo se revierte."""

import sys

import psycopg
from psycopg import errors

from asistente.config import get_settings


def main() -> int:
    dsn = get_settings().database_url.get_secret_value()
    fallos = 0

    def check(nombre: str, ok: bool, detalle: str = "") -> None:
        nonlocal fallos
        print(f"[{'OK' if ok else 'FALLA'}] {nombre}" + (f" ({detalle})" if detalle else ""))
        fallos += not ok

    def bloqueado(conn: psycopg.Connection, sql: str) -> bool:
        """True si el rol NO puede ejecutar la sentencia por falta de privilegios."""
        try:
            with conn.transaction():
                conn.execute(sql)
        except errors.InsufficientPrivilege:
            return True
        except psycopg.Error as e:
            print(f"      error inesperado: {type(e).__name__}")
            return False
        return False

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        rol = conn.execute("select current_user").fetchone()[0]
        check("conexión", True)
        check("rol es asistente_bot", rol == "asistente_bot", rol)

        tablas = {
            r[0]
            for r in conn.execute(
                "select table_name from information_schema.tables where table_schema = 'public'"
            )
        }
        esperadas = {
            "procesos", "proceso_eventos", "memorias", "recordatorios",
            "acciones_pendientes", "ejecuciones", "auditoria", "estado_sistema",
        }
        faltan = esperadas - tablas
        check("tablas del núcleo existen", not faltan, f"faltan: {sorted(faltan)}" if faltan else "")

        # Todo lo que sigue corre dentro de una transacción que se revierte al final.
        try:
            with conn.transaction():
                fila = conn.execute(
                    "insert into procesos (nombre) values ('__prueba__') returning id, estado"
                ).fetchone()
                check("insert en procesos (RLS + política)", fila is not None, str(fila[1]))
                n = conn.execute("select count(*) from procesos").fetchone()[0]
                check("select en procesos ve la fila", n >= 1)

                conn.execute(
                    "insert into memorias (contenido, origen) values ('prueba de café', 'usuario')"
                )
                hit = conn.execute(
                    "select count(*) from memorias where busqueda @@ plainto_tsquery('spanish', 'cafe')"
                ).fetchone()[0]
                check("búsqueda de texto en español", hit == 1)

                pausado = conn.execute("select pausado from estado_sistema").fetchone()
                check("estado_sistema legible", pausado is not None and pausado[0] is False)

                conn.execute("insert into auditoria (actor, accion) values ('prueba', 'check_db')")
                check("insert en auditoria", True)

                check("UPDATE en auditoria bloqueado", bloqueado(conn, "update auditoria set actor='x'"))
                check("DELETE en auditoria bloqueado", bloqueado(conn, "delete from auditoria"))
                check("UPDATE en ejecuciones bloqueado", bloqueado(conn, "update ejecuciones set tipo='x'"))
                check("TRUNCATE en procesos bloqueado", bloqueado(conn, "truncate procesos"))
                check("no puede crear tablas", bloqueado(conn, "create table intruso (id int)"))

                raise psycopg.errors.QueryCanceled("revertir")  # fuerza el rollback
        except psycopg.errors.QueryCanceled:
            pass

        restos = conn.execute("select count(*) from procesos where nombre = '__prueba__'").fetchone()[0]
        check("la prueba no dejó datos", restos == 0)

    print("\nTodo bien" if not fallos else f"\n{fallos} verificación(es) fallaron")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
