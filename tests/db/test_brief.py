from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from asistente.agenda.feriados import Feriados
from asistente.brief.matutino import construir_brief
from asistente.db.models import EventoNuevo, ProcesoEstado, ProcesoNuevo
from asistente.db.repos.agenda import EventoRepo
from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.db.repos.procesos import ProcesoRepo
from asistente.heartbeat.checks import recolectar

TZ = ZoneInfo("America/Santiago")
# 2026-09-21 12:00 en Chile (lunes), horario diurno
AHORA = datetime(2026, 9, 21, 15, 0, tzinfo=UTC)


class FeriadosFalsos(Feriados):
    def nombre(self, fecha):
        return None


def test_brief_incluye_agenda_procesos_y_fechas_limite(conn):
    EventoRepo(conn).crear(EventoNuevo(nombre="Clase de hoy", dias_semana=[1], hora=time(20, 30)))
    repo = ProcesoRepo(conn)
    repo.crear(ProcesoNuevo(
        nombre="Renovar pasaporte", proxima_accion="retirar documentos",
        proxima_accion_fecha=datetime(2026, 9, 21, 18, 0, tzinfo=TZ),
    ))
    repo.crear(ProcesoNuevo(nombre="Declaración de renta", fecha_limite=date(2026, 9, 23)))
    repo.crear(ProcesoNuevo(nombre="Lejana", fecha_limite=date(2026, 12, 1)))  # fuera de 3 días

    texto = construir_brief(conn, AHORA, TZ, FeriadosFalsos())
    assert "Brief de lunes 21/09" in texto
    assert "20:30 Clase de hoy" in texto
    assert "Renovar pasaporte" in texto and "retirar documentos" in texto
    assert "Declaración de renta: 23/09" in texto
    assert "Lejana" not in texto


def test_brief_sin_nada_igual_se_envia_y_lo_dice(conn):
    texto = construir_brief(conn, AHORA, TZ, FeriadosFalsos())
    assert "No tienes nada agendado para hoy." in texto


def test_proceso_cerrado_no_aparece(conn):
    ProcesoRepo(conn).crear(ProcesoNuevo(
        nombre="Ya cerrado", estado=ProcesoEstado.COMPLETADO,
        proxima_accion="nada", proxima_accion_fecha=datetime(2026, 9, 21, 18, 0, tzinfo=TZ),
    ))
    assert "Ya cerrado" not in construir_brief(conn, AHORA, TZ, FeriadosFalsos())


def test_el_brief_sale_una_sola_vez_al_dia_desde_su_hora(conn):
    manana = datetime(2026, 9, 21, 11, 0, tzinfo=UTC)  # 08:00 en Chile
    antes = datetime(2026, 9, 21, 10, 30, tzinfo=UTC)  # 07:30: todavía no

    assert [a for a in recolectar(conn, antes, TZ, brief_hora=8) if a.clave.startswith("brief:")] == []

    (aviso,) = [a for a in recolectar(conn, manana, TZ, brief_hora=8) if a.clave.startswith("brief:")]
    assert aviso.clave == "brief:2026-09-21"
    aviso.confirmar(conn)
    AuditoriaRepo(conn).registrar("heartbeat", "aviso", {"clave": aviso.clave})
    # mismo día, más tarde: no se repite
    assert [a for a in recolectar(conn, AHORA, TZ, brief_hora=8) if a.clave.startswith("brief:")] == []


def test_brief_desactivado_no_sale(conn):
    avisos = recolectar(conn, AHORA, TZ, brief_hora=None)
    assert not [a for a in avisos if a.clave.startswith("brief:")]


def test_brief_no_sale_de_madrugada(conn):
    de_noche = datetime(2026, 9, 22, 2, 0, tzinfo=UTC)  # 23:00 en Chile
    assert not [a for a in recolectar(conn, de_noche, TZ, brief_hora=8) if a.clave.startswith("brief:")]
