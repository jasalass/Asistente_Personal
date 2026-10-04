from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from asistente.agent.tools import construir_registro
from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.db.repos.recordatorios import RecordatorioRepo
from asistente.heartbeat.checks import proxima_ocurrencia, recolectar
from asistente.security.tool_registry import ToolError

TZ = ZoneInfo("America/Santiago")


@pytest.fixture
def conn_recurrencia(conn):
    cols = conn.execute(
        "select 1 from information_schema.columns where table_name='recordatorios' and column_name='repite_dias'"
    ).fetchone()
    if cols is None:
        pytest.skip("Falta aplicar supabase/migrations/0007_recordatorios_recurrentes.sql")
    return conn


def test_la_proxima_ocurrencia_es_el_siguiente_dia_de_la_lista():
    # lunes 21/09 08:00; ahora es lunes 09:00 -> próximo miércoles (días 1 y 3) a las 08:00
    actual = datetime(2026, 9, 21, 8, 0, tzinfo=TZ)
    ahora = datetime(2026, 9, 21, 9, 0, tzinfo=TZ)
    assert proxima_ocurrencia(actual, [1, 3], ahora, TZ) == datetime(2026, 9, 23, 8, 0, tzinfo=TZ)


def test_si_hoy_es_el_dia_pero_ya_paso_la_hora_salta_a_la_semana_siguiente():
    actual = datetime(2026, 9, 21, 8, 0, tzinfo=TZ)
    ahora = datetime(2026, 9, 21, 9, 0, tzinfo=TZ)
    assert proxima_ocurrencia(actual, [1], ahora, TZ) == datetime(2026, 9, 28, 8, 0, tzinfo=TZ)


def test_un_recordatorio_recurrente_avanza_en_vez_de_darse_por_enviado(conn_recurrencia):
    conn = conn_recurrencia
    repo = RecordatorioRepo(conn)
    r = repo.crear("pagar la luz", datetime(2026, 9, 21, 8, 0, tzinfo=TZ), repite_dias=[1])
    ahora = datetime(2026, 9, 21, 9, 0, tzinfo=TZ).astimezone(UTC)

    (aviso,) = [a for a in recolectar(conn, ahora, TZ) if a.clave.startswith("rec:")]
    aviso.confirmar(conn)
    AuditoriaRepo(conn).registrar("heartbeat", "aviso", {"clave": aviso.clave})

    avanzado = repo.obtener(r.id)
    assert avanzado.enviado is False
    assert avanzado.fecha == datetime(2026, 9, 28, 8, 0, tzinfo=TZ)
    assert [a for a in recolectar(conn, ahora, TZ) if a.clave.startswith("rec:")] == []


def test_un_recordatorio_de_una_vez_sigue_marcandose_enviado(conn_recurrencia):
    conn = conn_recurrencia
    repo = RecordatorioRepo(conn)
    r = repo.crear("llamar", datetime(2026, 9, 21, 8, 0, tzinfo=TZ))
    ahora = datetime(2026, 9, 21, 9, 0, tzinfo=TZ).astimezone(UTC)
    (aviso,) = [a for a in recolectar(conn, ahora, TZ) if a.clave.startswith("rec:")]
    aviso.confirmar(conn)
    assert repo.obtener(r.id).enviado is True


def test_la_herramienta_crea_el_recordatorio_repetido_con_resumen(conn_recurrencia):
    reg = construir_registro(conn_recurrencia, TZ, datetime(2026, 9, 21, 12, 0, tzinfo=UTC))
    r = reg.invoke(
        "crear_recordatorio",
        {"texto": "pagar la luz", "fecha": "2026-09-28T08:00:00", "repetir": ["lunes"]},
    )
    assert r["repite_dias"] == [1]
    assert r["resumen"] == "Recordatorio «pagar la luz» todos los lunes a las 08:00"


def test_un_dia_que_no_existe_se_rechaza(conn_recurrencia):
    reg = construir_registro(conn_recurrencia, TZ, datetime(2026, 9, 21, 12, 0, tzinfo=UTC))
    with pytest.raises(ToolError):
        reg.invoke("crear_recordatorio", {"texto": "x", "fecha": "2026-09-28T08:00:00", "repetir": ["funesday"]})
