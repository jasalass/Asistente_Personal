import asyncio
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from asistente.db.models import EventoTipo, ProcesoEstado, ProcesoNuevo
from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.db.repos.procesos import ProcesoRepo
from asistente.db.repos.recordatorios import RecordatorioRepo
from asistente.db.repos.sistema import EstadoSistemaRepo
from asistente.heartbeat.checks import recolectar
from asistente.heartbeat.runner import ciclo

TZ = ZoneInfo("America/Santiago")
AHORA = datetime(2026, 9, 21, 15, 0, tzinfo=UTC)  # 12:00 en Chile (UTC-3): horario diurno
MADRUGADA = datetime(2026, 9, 21, 5, 0, tzinfo=UTC)  # 02:00 en Chile: horario silencioso


def textos(avisos):
    return [a.texto for a in avisos]


def enviar_como_el_runner(conn, aviso):
    """Simula un envío exitoso: lo que hace el runner tras confirmar en Discord."""
    aviso.confirmar(conn)
    AuditoriaRepo(conn).registrar("heartbeat", "aviso", {"clave": aviso.clave})


# ---------- recordatorios ----------


def test_recordatorio_vencido_genera_aviso_y_no_se_repite(conn):
    RecordatorioRepo(conn).crear("pagar cuenta de luz", AHORA - timedelta(minutes=1))
    avisos = recolectar(conn, AHORA, TZ)
    assert textos(avisos) == ["**Recordatorio:** pagar cuenta de luz"]
    enviar_como_el_runner(conn, avisos[0])
    assert recolectar(conn, AHORA, TZ) == []


def test_recordatorio_atrasado_indica_para_cuando_era(conn):
    RecordatorioRepo(conn).crear("llamar al banco", AHORA - timedelta(hours=2))
    (aviso,) = recolectar(conn, AHORA, TZ)
    assert "(era para el 21/09 a las 10:00)" in aviso.texto


def test_recordatorio_futuro_no_avisa(conn):
    RecordatorioRepo(conn).crear("mañana", AHORA + timedelta(hours=1))
    assert recolectar(conn, AHORA, TZ) == []


def test_recordatorio_llega_aunque_sea_de_madrugada(conn):
    RecordatorioRepo(conn).crear("tomar vuelo", MADRUGADA - timedelta(minutes=1))
    assert len(recolectar(conn, MADRUGADA, TZ)) == 1


# ---------- próxima acción ----------


def crear_con_fecha(conn, fecha, **kw):
    return ProcesoRepo(conn).crear(
        ProcesoNuevo(nombre="Renovar pasaporte", proxima_accion="Ir a la cita", proxima_accion_fecha=fecha, **kw)
    )


def test_proxima_accion_avisa_por_tramos_sin_repetir(conn):
    fecha = AHORA + timedelta(hours=22)  # 10:00 del día siguiente, hora de Chile
    crear_con_fecha(conn, fecha)

    (a24,) = recolectar(conn, AHORA, TZ)
    assert "Renovar pasaporte" in a24.texto and "menos de 24 horas" in a24.texto
    enviar_como_el_runner(conn, a24)
    assert recolectar(conn, AHORA, TZ) == []  # ya avisado en este tramo

    # 08:30 del día siguiente: a menos de 2 h y ya en horario diurno
    (a2,) = recolectar(conn, AHORA + timedelta(hours=20, minutes=30), TZ)
    assert "menos de 2 horas" in a2.texto
    enviar_como_el_runner(conn, a2)

    (vencida,) = recolectar(conn, AHORA + timedelta(hours=23), TZ)
    assert "ya pasó" in vencida.texto and "¿La actualizo" in vencida.texto


def test_proxima_accion_dentro_del_horario_silencioso_espera_a_la_manana(conn):
    crear_con_fecha(conn, AHORA + timedelta(hours=22))
    # 07:00 local: a menos de 2 h de la cita, pero todavía no es horario de avisos
    assert recolectar(conn, AHORA + timedelta(hours=19), TZ) == []
    assert len(recolectar(conn, AHORA + timedelta(hours=20), TZ)) == 1  # 08:00 local


def test_proxima_accion_lejana_cerrada_o_muy_vieja_no_avisa(conn):
    crear_con_fecha(conn, AHORA + timedelta(hours=30))
    crear_con_fecha(conn, AHORA + timedelta(hours=1), estado=ProcesoEstado.COMPLETADO)
    crear_con_fecha(conn, AHORA - timedelta(days=8))
    assert recolectar(conn, AHORA, TZ) == []


# ---------- fecha límite ----------


@pytest.mark.parametrize(
    ("limite", "fragmento"),
    [
        (date(2026, 9, 24), "es en 3 días"),
        (date(2026, 9, 22), "es mañana"),
        (date(2026, 9, 21), "es hoy"),
        (date(2026, 9, 20), "venció hace 1 día."),
        (date(2026, 9, 18), "venció hace 3 días"),
    ],
)
def test_fecha_limite_tramos(conn, limite, fragmento):
    ProcesoRepo(conn).crear(ProcesoNuevo(nombre="Declaración", fecha_limite=limite))
    (aviso,) = recolectar(conn, AHORA, TZ)
    assert fragmento in aviso.texto


def test_fecha_limite_lejana_no_avisa_y_no_repite(conn):
    repo = ProcesoRepo(conn)
    repo.crear(ProcesoNuevo(nombre="lejana", fecha_limite=date(2026, 9, 25)))
    assert recolectar(conn, AHORA, TZ) == []
    repo.crear(ProcesoNuevo(nombre="cerca", fecha_limite=date(2026, 9, 22)))
    (aviso,) = recolectar(conn, AHORA, TZ)
    enviar_como_el_runner(conn, aviso)
    assert recolectar(conn, AHORA, TZ) == []


# ---------- horario silencioso ----------


def test_de_madrugada_solo_salen_recordatorios(conn):
    crear_con_fecha(conn, MADRUGADA + timedelta(hours=1))
    ProcesoRepo(conn).crear(ProcesoNuevo(nombre="X", fecha_limite=date(2026, 9, 21)))
    assert recolectar(conn, MADRUGADA, TZ) == []
    RecordatorioRepo(conn).crear("r", MADRUGADA)
    assert textos(recolectar(conn, MADRUGADA, TZ)) == ["**Recordatorio:** r"]


# ---------- chequeos periódicos ----------


def test_chequeo_periodico_y_su_registro(conn):
    repo = ProcesoRepo(conn)
    p = repo.crear(
        ProcesoNuevo(nombre="Seguro de salud", esperando_a="aseguradoras", frecuencia_chequeo_dias=7)
    )
    base = datetime.now(UTC) + timedelta(days=8)
    ahora = base.astimezone(TZ).replace(hour=12, minute=0, second=0, microsecond=0)

    assert recolectar(conn, datetime.now(UTC).astimezone(TZ).replace(hour=12), TZ) == []  # recién creado
    (aviso,) = recolectar(conn, ahora, TZ)
    assert "Chequeo de Seguro de salud" in aviso.texto
    assert "esperando a: aseguradoras" in aviso.texto and "desde que lo registraste" in aviso.texto

    enviar_como_el_runner(conn, aviso)
    assert repo.obtener(p.id).ultimo_chequeo is not None
    assert EventoTipo.CHEQUEO_AGENTE in [e.tipo for e in repo.eventos(p.id)]
    assert recolectar(conn, ahora, TZ) == []


# ---------- acciones pendientes vencidas ----------


def test_una_accion_vencida_avisa_y_se_cierra(conn):
    from asistente.db.repos.acciones_pendientes import AccionesPendientesRepo

    acciones = AccionesPendientesRepo(conn)
    fila = acciones.crear("enviar_email", {"a": "b@c.cl"}, "h")
    conn.execute(
        "update acciones_pendientes set expira_en = %s where id = %s",
        (AHORA - timedelta(hours=1), fila.id),
    )

    assert recolectar(conn, AHORA - timedelta(hours=2), TZ) == []  # todavía no vence
    (aviso,) = recolectar(conn, AHORA, TZ)
    assert "enviar_email" in aviso.texto and "expiró" in aviso.texto

    enviar_como_el_runner(conn, aviso)
    assert acciones.obtener(fila.id).estado == "expirada"
    assert recolectar(conn, AHORA, TZ) == []  # no se repite


def test_una_accion_ya_resuelta_no_genera_aviso_de_vencimiento(conn):
    from asistente.db.repos.acciones_pendientes import AccionesPendientesRepo

    acciones = AccionesPendientesRepo(conn)
    fila = acciones.crear("enviar_email", {}, "h")
    acciones.marcar_rechazada(fila.id, "123")
    conn.execute(
        "update acciones_pendientes set expira_en = %s where id = %s",
        (AHORA - timedelta(hours=1), fila.id),
    )
    assert recolectar(conn, AHORA, TZ) == []


# ---------- ciclo completo (envío + confirmación) ----------


def correr_ciclo(conn, enviar, ahora=AHORA):
    @contextmanager
    def abrir():
        yield conn  # comparte la conexión de prueba: nada se confirma de verdad en la base

    return asyncio.run(ciclo(enviar, tz=TZ, ahora=ahora, abrir=abrir))


class Envios:
    def __init__(self, falla=False):
        self.mensajes, self.falla = [], falla

    async def __call__(self, texto):
        if self.falla:
            raise ConnectionError("Discord caído")
        self.mensajes.append(texto)


def contar(conn, sql):
    return conn.execute(sql).fetchone()["n"]


def test_ciclo_envia_confirma_y_audita(conn):
    avisos_antes = contar(
        conn, "select count(*) as n from auditoria where actor='heartbeat' and accion='aviso'"
    )
    RecordatorioRepo(conn).crear("pagar", AHORA - timedelta(minutes=1))
    envios = Envios()
    assert correr_ciclo(conn, envios) == 1
    assert envios.mensajes == ["**Recordatorio:** pagar"]
    assert RecordatorioRepo(conn).vencidos(AHORA) == []
    assert contar(
        conn, "select count(*) as n from auditoria where actor='heartbeat' and accion='aviso'"
    ) == avisos_antes + 1
    ejec = conn.execute("select detalle, error from ejecuciones where tipo='heartbeat' order by id desc limit 1").fetchone()
    assert ejec["detalle"] == {"avisos": 1, "enviados": 1} and ejec["error"] is None
    assert correr_ciclo(conn, envios) == 0  # segundo latido: nada nuevo


def test_si_el_envio_falla_se_reintenta_en_el_siguiente_ciclo(conn):
    RecordatorioRepo(conn).crear("pagar", AHORA - timedelta(minutes=1))
    assert correr_ciclo(conn, Envios(falla=True)) == 0
    assert len(RecordatorioRepo(conn).vencidos(AHORA)) == 1  # sigue pendiente
    ejec = conn.execute("select error from ejecuciones where tipo='heartbeat' order by id desc limit 1").fetchone()
    assert ejec["error"] == "envío incompleto"

    envios = Envios()
    assert correr_ciclo(conn, envios) == 1
    assert envios.mensajes == ["**Recordatorio:** pagar"]


def test_en_pausa_no_envia_nada(conn):
    RecordatorioRepo(conn).crear("pagar", AHORA - timedelta(minutes=1))
    EstadoSistemaRepo(conn).set_pausado(True)
    envios = Envios()
    assert correr_ciclo(conn, envios) == 0 and envios.mensajes == []
    assert len(RecordatorioRepo(conn).vencidos(AHORA)) == 1


def test_un_ciclo_vacio_no_deja_registro(conn):
    antes = contar(conn, "select count(*) as n from ejecuciones where tipo='heartbeat'")
    assert correr_ciclo(conn, Envios()) == 0
    assert contar(conn, "select count(*) as n from ejecuciones where tipo='heartbeat'") == antes


def test_un_aviso_de_proceso_se_envia_una_sola_vez_entre_ciclos(conn):
    crear_con_fecha(conn, AHORA + timedelta(hours=22))
    envios = Envios()
    assert correr_ciclo(conn, envios) == 1
    assert correr_ciclo(conn, envios) == 0
    assert len(envios.mensajes) == 1
