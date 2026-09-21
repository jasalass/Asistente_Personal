from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from pydantic import ValidationError

from asistente.agenda.consulta import consultar, formatear
from asistente.agenda.feriados import Feriados
from asistente.agent.tools import construir_registro
from asistente.db.models import (
    AccionExcepcion,
    EventoActualizacion,
    EventoNuevo,
    ProcesoEstado,
    ProcesoNuevo,
)
from asistente.db.repos.agenda import EventoRepo
from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.db.repos.procesos import ProcesoRepo
from asistente.db.repos.recordatorios import RecordatorioRepo
from asistente.heartbeat.checks import recolectar
from asistente.security.tool_registry import ToolArgsInvalid, ToolError
from tests.agenda.test_agenda_pura import FERIADO_LUNES, LUNES, FeriadosFalsos

TZ = ZoneInfo("America/Santiago")
AHORA = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)  # lunes 09:00 en Chile (UTC-3)


def local(dia, hora, minuto=0) -> datetime:
    return datetime.combine(dia, time(hora, minuto), tzinfo=TZ)


def nuevo(**kw) -> EventoNuevo:
    base = {"nombre": "Matemática Aplicada", "dias_semana": [1], "hora": time(20, 30)}
    return EventoNuevo(**{**base, **kw})


# ---------- repositorio ----------


def test_crear_obtener_y_listar(conn_agenda):
    repo = EventoRepo(conn_agenda)
    e = repo.crear(nuevo(dias_semana=[3, 1], descripcion="Sala 204", duracion_min=90))
    assert e.dias_semana == [1, 3] and e.aviso_min_antes == 60 and e.suspender_feriados is True
    assert repo.obtener(e.id) == e
    assert [x.id for x in repo.listar(solo_activos=True)] == [e.id]
    assert e.id in [x.id for x in repo.buscar_por_nombre("MATEM")]
    assert repo.buscar_por_nombre("%") == []


def test_actualizar_valida_y_no_toca_lo_demas(conn_agenda):
    repo = EventoRepo(conn_agenda)
    e = repo.crear(nuevo(descripcion="Sala 204"))
    q = repo.actualizar(e.id, EventoActualizacion(hora=time(19, 0), dias_semana=[5, 1]))
    assert q.hora == time(19, 0) and q.dias_semana == [1, 5] and q.descripcion == "Sala 204"
    with pytest.raises(ValidationError):  # el fin de vigencia no puede ser anterior al inicio
        repo.actualizar(
            e.id, EventoActualizacion(vigente_desde=date(2026, 10, 1), vigente_hasta=date(2026, 9, 1))
        )
    assert repo.actualizar(e.id, EventoActualizacion(activo=False)).activo is False


def test_la_ultima_decision_sobre_una_fecha_reemplaza_a_la_anterior(conn_agenda):
    repo = EventoRepo(conn_agenda)
    e = repo.crear(nuevo())
    repo.registrar_excepcion(e.id, FERIADO_LUNES, AccionExcepcion.OMITIR, "paro")
    repo.registrar_excepcion(e.id, FERIADO_LUNES, AccionExcepcion.MANTENER, None)
    exc = repo.excepciones(FERIADO_LUNES, FERIADO_LUNES)
    assert exc == {(e.id, FERIADO_LUNES): (AccionExcepcion.MANTENER, None)}
    assert repo.excepciones(LUNES, LUNES) == {}  # solo el rango pedido


def test_la_base_rechaza_eventos_incoherentes(conn_agenda):
    with pytest.raises(psycopg.errors.CheckViolation), conn_agenda.transaction():
        conn_agenda.execute(
            "insert into eventos_agenda (nombre, dias_semana, hora) values ('x', '{}', '08:00')"
        )
    with pytest.raises(psycopg.errors.CheckViolation), conn_agenda.transaction():
        conn_agenda.execute(
            "insert into eventos_agenda (nombre, dias_semana, hora) values ('x', '{8}', '08:00')"
        )


def test_el_rol_no_puede_borrar_eventos_ni_excepciones(conn_agenda):
    repo = EventoRepo(conn_agenda)
    e = repo.crear(nuevo())
    repo.registrar_excepcion(e.id, LUNES, AccionExcepcion.OMITIR)
    for sentencia in ("delete from eventos_agenda", "delete from excepciones_agenda"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn_agenda.transaction():
            conn_agenda.execute(sentencia)


# ---------- la agenda del día ----------


def test_la_agenda_junta_todo_y_lo_ordena_por_hora(conn_agenda):
    EventoRepo(conn_agenda).crear(nuevo(duracion_min=90, descripcion="Sala 204"))
    RecordatorioRepo(conn_agenda).crear("llamar al banco", local(LUNES, 9))
    procesos = ProcesoRepo(conn_agenda)
    procesos.crear(ProcesoNuevo(nombre="Visa", proxima_accion="retirar documentos",
                                proxima_accion_fecha=local(LUNES, 18)))
    procesos.crear(ProcesoNuevo(nombre="Declaración de renta", fecha_limite=LUNES))
    procesos.crear(ProcesoNuevo(nombre="Ya cerrado", estado=ProcesoEstado.COMPLETADO,
                                proxima_accion_fecha=local(LUNES, 10)))

    (dia,) = consultar(conn_agenda, LUNES, 1, TZ, FeriadosFalsos())
    assert [it.texto for it in dia.items] == [
        "09:00 Recordatorio: llamar al banco",
        "18:00 Visa: retirar documentos",
        "20:30 Matemática Aplicada (90 min) — Sala 204",
        "Fecha límite de Declaración de renta",  # lo de todo el día, al final
    ]


def test_un_recordatorio_ya_enviado_se_muestra_como_avisado(conn_agenda):
    r = RecordatorioRepo(conn_agenda)
    hecho = r.crear("ya pasó", local(LUNES, 8))
    r.marcar_enviado(hecho.id)
    (dia,) = consultar(conn_agenda, LUNES, 1, TZ, FeriadosFalsos())
    assert dia.items[0].texto.endswith("(ya avisado)") and dia.items[0].estado == "avisado"


def test_en_un_feriado_el_evento_aparece_suspendido_y_el_dia_marcado(conn_agenda):
    EventoRepo(conn_agenda).crear(nuevo())
    salida = formatear(consultar(conn_agenda, FERIADO_LUNES, 1, TZ, Feriados()))
    (titulo, items), = salida.items()
    assert titulo.startswith("lunes 12/10 (feriado: ") and "Encuentro" in titulo
    assert items == [f"20:30 Matemática Aplicada — SUSPENDIDO (feriado: {Feriados().nombre(FERIADO_LUNES)})"]


def test_un_dia_sin_nada_viene_vacio_y_el_rango_se_acota(conn_agenda):
    EventoRepo(conn_agenda).crear(nuevo())
    salida = formatear(consultar(conn_agenda, date(2026, 9, 22), 3, TZ, FeriadosFalsos()))
    assert list(salida) == ["martes 22/09", "miércoles 23/09", "jueves 24/09"]
    assert all(v == [] for v in salida.values())
    assert len(consultar(conn_agenda, LUNES, 99, TZ, FeriadosFalsos())) == 14  # tope de 14 días


def test_la_semana_muestra_cada_lunes(conn_agenda):
    EventoRepo(conn_agenda).crear(nuevo())
    salida = formatear(consultar(conn_agenda, LUNES, 14, TZ, FeriadosFalsos()))
    con_clase = [dia for dia, items in salida.items() if items]
    assert con_clase == ["lunes 21/09", "lunes 28/09"]


# ---------- tools del agente ----------


@pytest.fixture
def reg(conn_agenda):
    return construir_registro(conn_agenda, TZ, AHORA, Feriados())


def test_todos_los_lunes_crea_un_evento_semanal_con_sus_proximas_fechas(reg):
    e = reg.invoke("crear_evento", {"nombre": "MATEMATICA APLICADA_006V", "dias": ["lunes"], "hora": "20:30"})
    assert e["dias_semana"] == [1] and e["aviso_min_antes"] == 60 and e["suspender_feriados"] is True
    proximas = e["proximas"]
    assert proximas[0] == "lunes 21/09 20:30" and proximas[1] == "lunes 28/09 20:30"
    # El asistente puede anticipar que el 12/10 no habrá clase por feriado:
    assert proximas[3].startswith("lunes 12/10 20:30 SUSPENDIDA (feriado: ")


def test_la_herramienta_devuelve_la_confirmacion_redactada_por_el_codigo(reg):
    e = reg.invoke("crear_evento", {"nombre": "Clases de Matemática", "dias": ["lunes"], "hora": "20:30"})
    assert e["resumen"] == (
        "Clases de Matemática: los lunes a las 20:30; aviso 60 min antes; "
        "se suspende en feriados; sin fecha de inicio ni de término"
    )
    assert "advertencia" not in e


def test_regresion_un_inicio_de_vigencia_inventado_no_pasa_desapercibido(reg):
    # Caso real: el usuario dijo solo "los lunes a las 20:30" y el modelo agregó desde=23/09,
    # lo que dejó fuera la clase de hoy (lunes 21/09) sin que nadie lo notara.
    e = reg.invoke("crear_evento", {"nombre": "Clases", "dias": ["lunes"], "hora": "20:30", "desde": "2026-09-23"})
    assert e["proximas"][0] == "lunes 28/09 20:30"  # hoy queda fuera...
    assert "vigente desde el 23/09/2026" in e["resumen"]  # ...pero ahora se dice explícitamente
    assert "solo aplica desde el 23/09/2026" in e["advertencia"] and "hoy tampoco" in e["advertencia"]


def test_una_vigencia_que_empieza_hoy_no_genera_advertencia(reg):
    e = reg.invoke("crear_evento", {"nombre": "Clases", "dias": ["lunes"], "hora": "20:30", "desde": "2026-09-21"})
    assert "advertencia" not in e and e["proximas"][0] == "lunes 21/09 20:30"


def test_al_actualizar_tambien_se_muestra_lo_que_quedo(reg):
    reg.invoke("crear_evento", {"nombre": "Clases", "dias": ["lunes"], "hora": "20:30"})
    e = reg.invoke("actualizar_evento", {"evento": "clases", "aviso_min_antes": 15, "hasta": "2026-12-15"})
    assert "aviso 15 min antes" in e["resumen"] and "vigente hasta el 15/12/2026" in e["resumen"]


def test_si_la_hora_de_hoy_ya_pasó_la_primera_fecha_es_la_siguiente(conn_agenda):
    tarde = datetime(2026, 9, 22, 0, 0, tzinfo=UTC)  # lunes 21:00 locales: la clase ya empezó
    reg = construir_registro(conn_agenda, TZ, tarde, Feriados())
    e = reg.invoke("crear_evento", {"nombre": "Clase", "dias": ["lunes"], "hora": "20:30"})
    assert e["proximas"][0] == "lunes 28/09 20:30"


def test_dias_con_tilde_mayusculas_y_varios(reg):
    e = reg.invoke("crear_evento", {"nombre": "Taller", "dias": ["Miércoles", "VIERNES"], "hora": "18:00",
                                    "duracion_min": 60, "aviso_min_antes": 15, "suspender_feriados": False,
                                    "desde": "2026-09-01", "hasta": "2026-12-15", "descripcion": "Lab 3"})
    assert e["dias_semana"] == [3, 5] and e["aviso_min_antes"] == 15 and e["suspender_feriados"] is False
    assert e["vigente_desde"] == "2026-09-01" and e["vigente_hasta"] == "2026-12-15"


def test_errores_del_modelo_se_explican(reg):
    with pytest.raises(ToolError, match="no es un día"):
        reg.invoke("crear_evento", {"nombre": "x", "dias": ["lunez"], "hora": "08:00"})
    with pytest.raises(ToolError, match="vigente_hasta"):
        reg.invoke("crear_evento", {"nombre": "x", "dias": ["lunes"], "hora": "08:00",
                                    "desde": "2026-10-01", "hasta": "2026-09-01"})
    for args in ({"nombre": "x", "hora": "08:00"}, {"nombre": "x", "dias": ["lunes"], "hora": "8:00"},
                 {"nombre": "x", "dias": [], "hora": "08:00"}, {"nombre": "x", "dias": ["lunes"]}):
        with pytest.raises(ToolArgsInvalid):
            reg.invoke("crear_evento", args)


def test_no_se_duplican_los_eventos(reg):
    reg.invoke("crear_evento", {"nombre": "Clase de yoga", "dias": ["martes"], "hora": "19:00"})
    with pytest.raises(ToolError, match="Ya existe el evento 'Clase de yoga' \\(activo\\)"):
        reg.invoke("crear_evento", {"nombre": "clase DE YOGA", "dias": ["jueves"], "hora": "19:00"})
    reg.invoke("actualizar_evento", {"evento": "yoga", "activo": False})
    with pytest.raises(ToolError, match="pausado"):
        reg.invoke("crear_evento", {"nombre": "Clase de yoga", "dias": ["jueves"], "hora": "19:00"})


def test_cambiar_hora_y_dias_por_nombre(reg):
    reg.invoke("crear_evento", {"nombre": "Reunión de equipo", "dias": ["lunes"], "hora": "10:00"})
    e = reg.invoke("actualizar_evento", {"evento": "reunión", "hora": "11:30", "dias": ["martes", "jueves"]})
    assert e["hora"] == "11:30:00" and e["dias_semana"] == [2, 4]
    assert e["proximas"][0] == "martes 22/09 11:30"


def test_omitir_una_fecha_la_marca_suspendida(reg):
    reg.invoke("crear_evento", {"nombre": "Matemática", "dias": ["lunes"], "hora": "20:30"})
    e = reg.invoke("actualizar_evento", {"evento": "matemática", "omitir_fecha": "2026-09-28",
                                         "motivo": "prueba en otra sala"})
    assert e["proximas"][1] == "lunes 28/09 20:30 SUSPENDIDA (omitida: prueba en otra sala)"
    assert e["proximas"][0] == "lunes 21/09 20:30"  # las demás siguen igual


def test_mantener_una_fecha_de_feriado(reg):
    reg.invoke("crear_evento", {"nombre": "Matemática", "dias": ["lunes"], "hora": "20:30"})
    e = reg.invoke("actualizar_evento", {"evento": "matemática", "mantener_fecha": "2026-10-12"})
    assert "lunes 12/10 20:30" in e["proximas"]  # ya no figura como suspendida
    assert not any("SUSPENDIDA" in p for p in e["proximas"])


def test_la_misma_fecha_no_puede_omitirse_y_mantenerse(reg):
    reg.invoke("crear_evento", {"nombre": "Matemática", "dias": ["lunes"], "hora": "20:30"})
    with pytest.raises(ToolError, match="a la vez"):
        reg.invoke("actualizar_evento", {"evento": "matemática", "omitir_fecha": "2026-10-05",
                                         "mantener_fecha": "2026-10-05"})


def test_hay_que_indicar_el_evento_de_una_sola_forma(reg):
    reg.invoke("crear_evento", {"nombre": "Matemática", "dias": ["lunes"], "hora": "20:30"})
    for args in ({"activo": False}, {"evento": "x", "id": "00000000-0000-0000-0000-000000000000"}):
        with pytest.raises(ToolArgsInvalid):
            reg.invoke("actualizar_evento", args)
    with pytest.raises(ToolError, match="No encontré"):
        reg.invoke("actualizar_evento", {"evento": "no existe zzz", "activo": False})


def test_pausar_un_evento_lo_saca_de_la_agenda(reg):
    reg.invoke("crear_evento", {"nombre": "Matemática", "dias": ["lunes"], "hora": "20:30"})
    assert reg.invoke("listar_agenda", {})["lunes 21/09"] == ["20:30 Matemática"]
    reg.invoke("actualizar_evento", {"evento": "matemática", "activo": False})
    assert reg.invoke("listar_agenda", {})["lunes 21/09"] == []


def test_listar_agenda_usa_hoy_por_defecto_y_acepta_rango(reg):
    reg.invoke("crear_evento", {"nombre": "Matemática", "dias": ["lunes"], "hora": "20:30"})
    reg.invoke("crear_recordatorio", {"texto": "llamar al banco", "fecha": "2026-09-21T09:00:00"})
    assert reg.invoke("listar_agenda", {}) == {
        "lunes 21/09": ["09:00 Recordatorio: llamar al banco", "20:30 Matemática"]
    }
    semana = reg.invoke("listar_agenda", {"desde": "2026-10-12", "dias": 2})
    assert list(semana) == [
        f"lunes 12/10 (feriado: {Feriados().nombre(FERIADO_LUNES)})", "martes 13/10",
    ]
    assert "SUSPENDIDO" in semana[next(iter(semana))][0]


# ---------- avisos del heartbeat ----------


def enviar_como_el_runner(conn, aviso):
    aviso.confirmar(conn)
    AuditoriaRepo(conn).registrar("heartbeat", "aviso", {"clave": aviso.clave})


def en(hora, minuto=0, dia=LUNES) -> datetime:
    """Un instante dado en hora local de Chile."""
    return local(dia, hora, minuto).astimezone(UTC)


def test_avisa_antes_del_evento_una_sola_vez(conn_agenda):
    EventoRepo(conn_agenda).crear(nuevo(descripcion="Sala 204"))
    sin = FeriadosFalsos()
    assert recolectar(conn_agenda, en(19, 0), TZ, feriados=sin) == []  # todavía falta más de una hora
    (aviso,) = recolectar(conn_agenda, en(19, 45), TZ, feriados=sin)
    assert aviso.texto == "**Matemática Aplicada** empieza en 45 min (20:30). Sala 204"
    enviar_como_el_runner(conn_agenda, aviso)
    assert recolectar(conn_agenda, en(20, 0), TZ, feriados=sin) == []  # ya avisado
    assert recolectar(conn_agenda, en(20, 30), TZ, feriados=sin) == []  # ya empezó: no tiene sentido


def test_el_aviso_es_a_la_hora_indicada_no_antes(conn_agenda):
    EventoRepo(conn_agenda).crear(nuevo(aviso_min_antes=15))
    sin = FeriadosFalsos()
    assert recolectar(conn_agenda, en(19, 59), TZ, feriados=sin) == []
    assert len(recolectar(conn_agenda, en(20, 15), TZ, feriados=sin)) == 1


def test_sin_aviso_previo_no_avisa(conn_agenda):
    EventoRepo(conn_agenda).crear(nuevo(aviso_min_antes=0))
    assert recolectar(conn_agenda, en(20, 0), TZ, feriados=FeriadosFalsos()) == []


def test_un_evento_de_madrugada_avisa_aunque_sea_horario_silencioso(conn_agenda):
    EventoRepo(conn_agenda).crear(nuevo(nombre="Vuelo", hora=time(7, 0)))
    (aviso,) = recolectar(conn_agenda, en(6, 15), TZ, feriados=FeriadosFalsos())  # 06:15, antes de las 08:00
    assert "empieza en 45 min (07:00)" in aviso.texto


def test_en_feriado_no_avisa_pero_cuenta_por_la_manana_que_no_hay(conn_agenda):
    EventoRepo(conn_agenda).crear(nuevo())
    feriado = {"2026_09_21": "Día de prueba"}
    sin_aviso_previo = recolectar(conn_agenda, en(20, 0), TZ, feriados=FeriadosFalsos(**feriado))
    # A las 20:00 (después de la mañana y antes de las 20:30) informa de la suspensión, no avisa la clase:
    assert [a.texto for a in sin_aviso_previo] == ["Hoy no hay **Matemática Aplicada** (feriado: Día de prueba)."]

    (por_la_manana,) = recolectar(conn_agenda, en(9, 0), TZ, feriados=FeriadosFalsos(**feriado))
    enviar_como_el_runner(conn_agenda, por_la_manana)
    assert recolectar(conn_agenda, en(10, 0), TZ, feriados=FeriadosFalsos(**feriado)) == []  # una sola vez


def test_la_suspension_no_se_informa_cuando_el_evento_ya_paso_ni_de_noche(conn_agenda):
    EventoRepo(conn_agenda).crear(nuevo())
    f = FeriadosFalsos(**{"2026_09_21": "Día de prueba"})
    assert recolectar(conn_agenda, en(21, 0), TZ, feriados=f) == []  # después de las 20:30
    assert recolectar(conn_agenda, en(6, 0), TZ, feriados=f) == []  # horario silencioso


def test_con_el_feriado_real_del_12_de_octubre(conn_agenda):
    EventoRepo(conn_agenda).crear(nuevo())
    (aviso,) = recolectar(conn_agenda, en(9, 0, FERIADO_LUNES), TZ, feriados=Feriados())
    assert aviso.texto.startswith("Hoy no hay **Matemática Aplicada** (feriado: ") and "Encuentro" in aviso.texto
    # ...y esa clase, aunque el evento exista, no genera aviso previo:
    antes_de_la_clase = recolectar(conn_agenda, en(19, 45, FERIADO_LUNES), TZ, feriados=Feriados())
    assert all("empieza en" not in a.texto for a in antes_de_la_clase)


def test_una_fecha_mantenida_en_feriado_si_avisa(conn_agenda):
    repo = EventoRepo(conn_agenda)
    e = repo.crear(nuevo())
    repo.registrar_excepcion(e.id, FERIADO_LUNES, AccionExcepcion.MANTENER)
    (aviso,) = recolectar(conn_agenda, en(19, 45, FERIADO_LUNES), TZ, feriados=Feriados())
    assert "empieza en 45 min" in aviso.texto


def test_un_evento_pausado_no_avisa(conn_agenda):
    repo = EventoRepo(conn_agenda)
    e = repo.crear(nuevo())
    repo.actualizar(e.id, EventoActualizacion(activo=False))
    assert recolectar(conn_agenda, en(19, 45), TZ, feriados=FeriadosFalsos()) == []


def test_otro_dia_de_la_semana_no_avisa(conn_agenda):
    EventoRepo(conn_agenda).crear(nuevo())
    martes = LUNES + timedelta(days=1)
    assert recolectar(conn_agenda, en(19, 45, martes), TZ, feriados=FeriadosFalsos()) == []
