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
            yield c
        finally:
            c.rollback()  # nunca se hace commit: ni el vaciado ni lo que creó el test persisten
