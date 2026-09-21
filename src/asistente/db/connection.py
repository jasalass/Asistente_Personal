from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from asistente.config import get_settings

Conn = psycopg.Connection[dict[str, Any]]


@contextmanager
def transaccion(dsn: str | None = None) -> Iterator[Conn]:
    """Una conexión = una unidad de trabajo. Confirma al salir bien, revierte si hay excepción.

    Se abre una conexión por operación: el Session pooler del plan gratuito tiene pocos cupos.
    """
    dsn = dsn or get_settings().database_url.get_secret_value()
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as conn:
        yield conn
