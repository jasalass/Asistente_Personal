from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from asistente.agent.tools import construir_registro
from asistente.db.repos.recordatorios import RecordatorioRepo
from asistente.security.tool_registry import ToolArgsInvalid, ToolError

TZ = ZoneInfo("America/Santiago")
AHORA = datetime(2026, 9, 21, 20, 0, tzinfo=UTC)  # lunes 17:00 en Chile
FUTURO = AHORA + timedelta(days=1)


@pytest.fixture
def reg(conn):
    return construir_registro(conn, TZ, AHORA)


def pendientes(conn):
    return [(r.texto) for r in RecordatorioRepo(conn).entre(AHORA - timedelta(days=30), AHORA + timedelta(days=30))]


def test_cancela_por_parte_del_texto_y_solo_ese(reg, conn):
    repo = RecordatorioRepo(conn)
    repo.crear("Probar el heartbeat", FUTURO)
    repo.crear("Llamar al banco", FUTURO)
    r = reg.invoke("cancelar_recordatorio", {"recordatorio": "heartbeat"})
    assert r == {"cancelado": {"texto": "Probar el heartbeat", "fecha": "22/09 17:00", "ya_avisado": False}}
    assert pendientes(conn) == ["Llamar al banco"]  # el otro no se toca


def test_cancela_por_id(reg, conn):
    rec = RecordatorioRepo(conn).crear("Pagar patente", FUTURO)
    reg.invoke("cancelar_recordatorio", {"id": str(rec.id)})
    assert RecordatorioRepo(conn).obtener(rec.id) is None


def test_tambien_se_puede_cancelar_uno_ya_avisado(reg, conn):
    repo = RecordatorioRepo(conn)
    rec = repo.crear("Probar el heartbeat", AHORA - timedelta(hours=15))
    repo.marcar_enviado(rec.id)
    r = reg.invoke("cancelar_recordatorio", {"recordatorio": "probar"})
    assert r["cancelado"]["ya_avisado"] is True and pendientes(conn) == []


def test_si_hay_varios_pide_elegir_por_id(reg, conn):
    repo = RecordatorioRepo(conn)
    a = repo.crear("Llamar a Ana", FUTURO)
    b = repo.crear("Llamar a Luis", FUTURO + timedelta(hours=1))
    with pytest.raises(ToolError) as e:
        reg.invoke("cancelar_recordatorio", {"recordatorio": "llamar"})
    assert str(a.id) in str(e.value) and str(b.id) in str(e.value)
    assert len(pendientes(conn)) == 2  # ante la duda no se borra nada
    reg.invoke("cancelar_recordatorio", {"recordatorio": "llamar a ana"})  # el texto exacto sí resuelve
    assert pendientes(conn) == ["Llamar a Luis"]


def test_entre_uno_pendiente_y_uno_ya_avisado_cancela_el_pendiente(reg, conn):
    repo = RecordatorioRepo(conn)
    viejo = repo.crear("Regar las plantas", AHORA - timedelta(days=3))
    repo.marcar_enviado(viejo.id)
    repo.crear("Regar las plantas del balcón", FUTURO)
    reg.invoke("cancelar_recordatorio", {"recordatorio": "regar"})
    quedan = RecordatorioRepo(conn).buscar_por_texto("regar")
    assert [q.texto for q in quedan] == ["Regar las plantas"] and quedan[0].enviado is True


def test_sin_coincidencias_o_con_id_inexistente(reg):
    with pytest.raises(ToolError, match="No encontré"):
        reg.invoke("cancelar_recordatorio", {"recordatorio": "no existe zzz"})
    with pytest.raises(ToolError, match="No existe"):
        reg.invoke("cancelar_recordatorio", {"id": "00000000-0000-0000-0000-000000000000"})


def test_hay_que_indicarlo_de_una_sola_forma(reg):
    for args in ({}, {"id": "00000000-0000-0000-0000-000000000000", "recordatorio": "x"}):
        with pytest.raises(ToolArgsInvalid):
            reg.invoke("cancelar_recordatorio", args)


def test_el_texto_se_busca_literal_y_no_como_comodin(reg, conn):
    repo = RecordatorioRepo(conn)
    repo.crear("Llamar al banco", FUTURO)
    repo.crear("Reunión 100% obligatoria", FUTURO + timedelta(hours=1))
    with pytest.raises(ToolError, match="No encontré"):
        reg.invoke("cancelar_recordatorio", {"recordatorio": "_"})  # comodines de SQL: no coinciden con todo
    assert len(pendientes(conn)) == 2
    reg.invoke("cancelar_recordatorio", {"recordatorio": "100%"})  # un % literal sí se busca tal cual
    assert pendientes(conn) == ["Llamar al banco"]


def test_queda_constancia_de_lo_que_se_cancelo(reg, conn):
    RecordatorioRepo(conn).crear("Probar el heartbeat", FUTURO)
    reg.invoke("cancelar_recordatorio", {"recordatorio": "heartbeat"})
    fila = conn.execute(
        "select actor, detalle from auditoria where accion = 'recordatorio_cancelado' order by id desc limit 1"
    ).fetchone()
    assert fila["actor"] == "agente"
    assert fila["detalle"] == {"texto": "Probar el heartbeat", "fecha": "22/09 17:00", "enviado": False}


def test_el_recordatorio_cancelado_ya_no_sale_en_la_agenda(reg, conn):
    RecordatorioRepo(conn).crear("Probar el heartbeat", FUTURO)
    assert any("heartbeat" in i for i in reg.invoke("listar_agenda", {"desde": "2026-09-22"})["martes 22/09"])
    reg.invoke("cancelar_recordatorio", {"recordatorio": "heartbeat"})
    assert reg.invoke("listar_agenda", {"desde": "2026-09-22"})["martes 22/09"] == []
