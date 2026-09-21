import psycopg
import pytest
from psycopg.rows import dict_row
from pydantic import ValidationError

from asistente.config import get_settings


@pytest.fixture
def conn():
    """Conexión real a la base con rollback al final: los tests no dejan datos."""
    try:
        dsn = get_settings().database_url.get_secret_value()
    except ValidationError:
        pytest.skip("Sin configuración de base de datos (.env)")
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as c:
        try:
            yield c
        finally:
            c.rollback()
