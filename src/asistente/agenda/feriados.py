from datetime import date
from functools import lru_cache
from typing import Protocol

import holidays


class CalendarioFeriados(Protocol):
    def nombre(self, fecha: date) -> str | None:
        """Nombre del feriado, o None si es un día normal."""
        ...


@lru_cache(maxsize=16)
def _feriados_del_anio(pais: str, anio: int) -> dict[date, str]:
    return dict(holidays.country_holidays(pais, years=[anio], language="es"))


class Feriados:
    """Feriados legales calculados por la librería `holidays` (Chile por defecto).

    No incluye feriados decretados a última hora ni los recesos de tu universidad o trabajo: para
    eso existen las excepciones por fecha de cada evento.
    """

    def __init__(self, pais: str = "CL") -> None:
        self._pais = pais

    def nombre(self, fecha: date) -> str | None:
        return _feriados_del_anio(self._pais, fecha.year).get(fecha)
