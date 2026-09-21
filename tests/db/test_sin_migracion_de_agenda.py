"""Si falta la migración 0004, la agenda no existe pero NADA de lo demás debe romperse."""

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from asistente import gateway
from asistente.agenda.consulta import consultar, formatear
from asistente.agent.tools import construir_registro
from asistente.db.models import ProcesoNuevo
from asistente.db.repos.agenda import EventoRepo
from asistente.db.repos.procesos import ProcesoRepo
from asistente.db.repos.recordatorios import RecordatorioRepo
from asistente.heartbeat.checks import recolectar
from asistente.security.tool_registry import ToolError
from tests.agenda.test_agenda_pura import LUNES, FeriadosFalsos

TZ = ZoneInfo("America/Santiago")
AHORA = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)  # lunes 09:00 en Chile


@pytest.fixture(autouse=True)
def sin_tablas_de_agenda(monkeypatch):
    monkeypatch.setattr(EventoRepo, "disponible", lambda self: False)


def test_el_heartbeat_sigue_enviando_los_recordatorios(conn):
    RecordatorioRepo(conn).crear("pagar cuenta", AHORA - timedelta(minutes=1))
    avisos = recolectar(conn, AHORA, TZ, feriados=FeriadosFalsos())
    assert [a.texto for a in avisos] == ["**Recordatorio:** pagar cuenta"]


def test_la_agenda_del_dia_muestra_lo_que_si_existe(conn):
    RecordatorioRepo(conn).crear("llamar al banco", datetime(2026, 9, 21, 12, 0, tzinfo=UTC))
    ProcesoRepo(conn).crear(ProcesoNuevo(nombre="Declaración de renta", fecha_limite=LUNES))
    salida = formatear(consultar(conn, LUNES, 1, TZ, FeriadosFalsos()))
    assert salida == {
        "lunes 21/09": ["09:00 Recordatorio: llamar al banco", "Fecha límite de Declaración de renta"]
    }


def test_las_tools_de_eventos_explican_que_falta_la_migracion(conn):
    reg = construir_registro(conn, TZ, AHORA, FeriadosFalsos())
    for tool, args in (
        ("crear_evento", {"nombre": "Clase", "dias": ["lunes"], "hora": "20:30"}),
        ("actualizar_evento", {"evento": "clase", "activo": False}),
    ):
        with pytest.raises(ToolError, match="0004_agenda.sql"):
            reg.invoke(tool, args)
    assert reg.invoke("listar_agenda", {}) == {"lunes 21/09": []}  # sin romperse


def test_el_arranque_avisa_en_el_log_que_falta_la_agenda(caplog):
    with caplog.at_level(logging.WARNING, logger="asistente.gateway"):
        gateway.advertir_si_falta_la_agenda()
    assert "0004_agenda.sql" in caplog.text
