import psycopg
import pytest
from psycopg.rows import dict_row
from pydantic import ValidationError

from asistente.config import get_settings

# Tablas de trabajo que los tests necesitan vacías. Se borran SOLO dentro de la transacción del
# test, que siempre termina en rollback: los datos reales de la base quedan intactos.
# (auditoria y ejecuciones son append-only y no se tocan; los tests miden diferencias.)
_TABLAS_AISLADAS = ("recordatorios", "procesos", "memorias")


@pytest.fixture
def conn():
    """Conexión real a la base, aislada de los datos reales y con rollback al final."""
    try:
        dsn = get_settings().database_url.get_secret_value()
    except ValidationError:
        pytest.skip("Sin configuración de base de datos (.env)")
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as c:
        try:
            for tabla in _TABLAS_AISLADAS:
                c.execute(f"delete from {tabla}")  # nombres fijos de arriba, no de usuario
            if _hay_tablas_del_vigia(c):
                # El rol no puede borrar temas (solo desactivarlos): así los reales no participan.
                c.execute("update temas_seguimiento set activo = false")
            if _hay_tablas_de_agenda(c):
                c.execute("update eventos_agenda set activo = false")  # ídem con los eventos
            yield c
        finally:
            c.rollback()  # nunca se hace commit: ni el vaciado ni lo que creó el test persisten


def _hay_tablas_del_vigia(c) -> bool:
    return c.execute("select to_regclass('public.temas_seguimiento') as t").fetchone()["t"] is not None


def _hay_tablas_de_agenda(c) -> bool:
    return c.execute("select to_regclass('public.eventos_agenda') as t").fetchone()["t"] is not None


@pytest.fixture
def conn_agenda(conn):
    """Como `conn`, pero se omite si aún no se aplicó la migración 0004_agenda.sql."""
    if not _hay_tablas_de_agenda(conn):
        pytest.skip("Falta aplicar supabase/migrations/0004_agenda.sql")
    return conn


@pytest.fixture
def conn_vigia(conn):
    """Como `conn`, pero se omite si aún no se aplicó la migración 0003_vigia.sql."""
    if not _hay_tablas_del_vigia(conn):
        pytest.skip("Falta aplicar supabase/migrations/0003_vigia.sql")
    return conn
