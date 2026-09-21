from datetime import UTC, date, datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from asistente.agenda.feriados import Feriados
from asistente.agenda.ocurrencias import (
    advertencia_de_vigencia,
    describir,
    nombre_dia,
    ocurrencia,
    proximas,
)
from asistente.agent.tools import construir_registro, dias_a_numeros
from asistente.db.models import AccionExcepcion, Evento, EventoNuevo
from asistente.security.tool_registry import ToolDenied, ToolError

LUNES = date(2026, 9, 21)
FERIADO_LUNES = date(2026, 10, 12)  # Día del Encuentro de Dos Mundos (cae lunes en 2026)


class FeriadosFalsos:
    def __init__(self, **por_fecha):
        self._por_fecha = {date.fromisoformat(k.replace("_", "-")): v for k, v in por_fecha.items()}

    def nombre(self, fecha):
        return self._por_fecha.get(fecha)


def hacer_evento(**kw) -> Evento:
    datos = {
        "id": uuid4(), "nombre": "Matemática Aplicada", "descripcion": None, "dias_semana": [1],
        "hora": time(20, 30), "duracion_min": None, "aviso_min_antes": 60,
        "suspender_feriados": True, "vigente_desde": None, "vigente_hasta": None, "activo": True,
        "creado_en": datetime(2026, 9, 21, tzinfo=UTC), "actualizado_en": datetime(2026, 9, 21, tzinfo=UTC),
    }
    return Evento(**{**datos, **kw})


# ---------- feriados de Chile ----------


def test_feriados_legales_de_chile_2026():
    f = Feriados()
    assert f.nombre(date(2026, 10, 12)) is not None  # lunes: afecta a los eventos de los lunes
    assert f.nombre(date(2026, 9, 18)) is not None and f.nombre(date(2026, 12, 25)) is not None
    assert f.nombre(date(2026, 6, 29)) is not None
    assert f.nombre(LUNES) is None and f.nombre(date(2026, 9, 22)) is None


def test_los_feriados_traen_su_nombre_en_espanol():
    assert "Encuentro" in Feriados().nombre(FERIADO_LUNES)


# ---------- ocurrencias ----------


def test_solo_ocurre_los_dias_indicados():
    e, sin = hacer_evento(), FeriadosFalsos()
    assert ocurrencia(e, LUNES, {}, sin) is not None
    assert ocurrencia(e, date(2026, 9, 22), {}, sin) is None  # martes


def test_todos_los_lunes_son_todos_los_lunes():
    e, sin = hacer_evento(), FeriadosFalsos()
    lunes = [date(2026, 9, d) for d in (21, 28)] + [date(2026, 10, d) for d in (5, 12, 19, 26)]
    assert all(ocurrencia(e, d, {}, sin) is not None for d in lunes)
    assert sum(ocurrencia(e, date(2026, 10, d), {}, sin) is not None for d in range(1, 32)) == 4


def test_varios_dias():
    e = hacer_evento(dias_semana=[1, 3, 5])
    assert [ocurrencia(e, date(2026, 9, d), {}, FeriadosFalsos()) is not None for d in range(21, 28)] == [
        True, False, True, False, True, False, False,
    ]


def test_se_suspende_en_feriado_y_dice_por_que():
    o = ocurrencia(hacer_evento(), FERIADO_LUNES, {}, Feriados())
    assert o.suspendida and o.motivo.startswith("feriado: ") and "Encuentro" in o.motivo


def test_si_no_se_suspende_en_feriados_ocurre_igual():
    o = ocurrencia(hacer_evento(suspender_feriados=False), FERIADO_LUNES, {}, Feriados())
    assert o is not None and not o.suspendida


def test_omitir_una_fecha():
    e = hacer_evento()
    exc = {(e.id, LUNES): (AccionExcepcion.OMITIR, "paro de estudiantes")}
    o = ocurrencia(e, LUNES, exc, FeriadosFalsos())
    assert o.suspendida and o.motivo == "omitida: paro de estudiantes"
    assert not ocurrencia(e, date(2026, 9, 28), exc, FeriadosFalsos()).suspendida  # solo esa fecha


def test_mantener_una_fecha_aunque_sea_feriado():
    e = hacer_evento()
    exc = {(e.id, FERIADO_LUNES): (AccionExcepcion.MANTENER, None)}
    o = ocurrencia(e, FERIADO_LUNES, exc, Feriados())
    assert o is not None and not o.suspendida


def test_vigencia_e_inactivos():
    e = hacer_evento(vigente_desde=date(2026, 9, 28), vigente_hasta=date(2026, 10, 5))
    sin = FeriadosFalsos()
    assert ocurrencia(e, LUNES, {}, sin) is None  # antes del inicio
    assert ocurrencia(e, date(2026, 9, 28), {}, sin) is not None
    assert ocurrencia(e, date(2026, 10, 5), {}, sin) is not None  # último día incluido
    assert ocurrencia(e, date(2026, 10, 12), {}, sin) is None  # después del fin
    assert ocurrencia(hacer_evento(activo=False), LUNES, {}, sin) is None


def test_las_proximas_incluyen_las_suspendidas():
    e = hacer_evento()
    sig = proximas(e, LUNES, {}, Feriados(), cuantas=4)
    assert [(o.fecha, o.suspendida) for o in sig] == [
        (date(2026, 9, 21), False), (date(2026, 9, 28), False),
        (date(2026, 10, 5), False), (date(2026, 10, 12), True),
    ]


def test_el_inicio_usa_la_zona_del_usuario():
    o = ocurrencia(hacer_evento(), LUNES, {}, FeriadosFalsos())
    inicio = o.inicio(ZoneInfo("America/Santiago"))
    assert inicio.hour == 20 and inicio.minute == 30
    # En septiembre Chile ya está en horario de verano (UTC-3): 20:30 locales = 23:30 UTC.
    assert inicio.astimezone(UTC) == datetime(2026, 9, 21, 23, 30, tzinfo=UTC)


def test_nombre_del_dia():
    assert nombre_dia(LUNES) == "lunes" and nombre_dia(date(2026, 9, 27)) == "domingo"


# ---------- la confirmación la redacta el código, no el modelo ----------


def test_describir_muestra_todo_lo_que_quedo_configurado():
    assert describir(hacer_evento(nombre="Clases de Matemática")) == (
        "Clases de Matemática: los lunes a las 20:30; aviso 60 min antes; "
        "se suspende en feriados; sin fecha de inicio ni de término"
    )


def test_describir_varios_dias_plurales_y_opciones():
    e = hacer_evento(
        dias_semana=[3, 5, 6], duracion_min=90, aviso_min_antes=0, suspender_feriados=False,
        descripcion="Sala 204",
    )
    assert describir(e) == (
        "Matemática Aplicada: los miércoles, viernes y sábados a las 20:30; dura 90 min; "
        "sin aviso previo; ocurre aunque sea feriado; sin fecha de inicio ni de término; Sala 204"
    )
    assert "los domingos" in describir(hacer_evento(dias_semana=[7]))


def test_describir_incluye_la_vigencia_para_que_no_pase_desapercibida():
    e = hacer_evento(vigente_desde=date(2026, 9, 23), vigente_hasta=date(2026, 12, 15))
    assert "vigente desde el 23/09/2026 hasta el 15/12/2026" in describir(e)
    assert "vigente desde el 23/09/2026" in describir(hacer_evento(vigente_desde=date(2026, 9, 23)))
    assert "vigente hasta el 15/12/2026" in describir(hacer_evento(vigente_hasta=date(2026, 12, 15)))


def test_advertencia_si_la_vigencia_deja_fuera_lo_de_hoy():
    inicio_futuro = hacer_evento(vigente_desde=date(2026, 9, 23))
    aviso = advertencia_de_vigencia(inicio_futuro, LUNES)
    assert "solo aplica desde el 23/09/2026" in aviso and "hoy tampoco" in aviso
    assert advertencia_de_vigencia(hacer_evento(vigente_desde=LUNES), LUNES) is None  # empieza hoy: bien
    assert advertencia_de_vigencia(hacer_evento(), LUNES) is None
    terminado = advertencia_de_vigencia(hacer_evento(vigente_hasta=date(2026, 9, 1)), LUNES)
    assert "ya terminó el 01/09/2026" in terminado


def test_los_parametros_de_vigencia_piden_fecha_explicita_del_usuario():
    esquema = construir_registro(None, TZ).definiciones()
    props = {d["function"]["name"]: d["function"]["parameters"]["properties"] for d in esquema}
    for herramienta in ("crear_evento", "actualizar_evento"):
        for campo in ("desde", "hasta"):
            assert "usuario" in props[herramienta][campo]["description"]
    assert "SOLO" in props["crear_evento"]["desde"]["description"]


# ---------- modelo ----------


def test_los_dias_se_ordenan_sin_repetir_y_no_pueden_faltar():
    e = EventoNuevo(nombre="x", dias_semana=[5, 1, 5, 3], hora=time(8, 0))
    assert e.dias_semana == [1, 3, 5]
    for dias in ([], [0], [8]):
        with pytest.raises(ValidationError):
            EventoNuevo(nombre="x", dias_semana=dias, hora=time(8, 0))


def test_el_fin_de_vigencia_no_puede_ser_anterior_al_inicio():
    with pytest.raises(ValidationError):
        EventoNuevo(nombre="x", dias_semana=[1], hora=time(8, 0),
                    vigente_desde=date(2026, 10, 1), vigente_hasta=date(2026, 9, 1))


# ---------- nombres de días ----------


@pytest.mark.parametrize(
    ("nombres", "numeros"),
    [(["lunes"], [1]), (["Miércoles", "MIERCOLES"], [3]), (["viernes", "lunes"], [1, 5]),
     (["sábado", "sabado", "domingo"], [6, 7])],
)
def test_dias_con_o_sin_tilde_y_en_cualquier_caso(nombres, numeros):
    assert dias_a_numeros(nombres) == numeros


def test_un_dia_inventado_se_informa_al_modelo():
    with pytest.raises(ToolError, match="no es un día"):
        dias_a_numeros(["lunes", "funes"])


# ---------- las tools del vigía solo existen cuando se habilitan ----------


TZ = ZoneInfo("America/Santiago")


def test_las_tools_del_vigia_no_se_muestran_ni_se_pueden_usar_hasta_habilitarlas():
    reg = construir_registro(None, TZ)
    visibles = {d["function"]["name"] for d in reg.definiciones()}
    assert "habilitar_vigia" in visibles
    assert not visibles & {"listar_temas", "crear_tema", "actualizar_tema"}
    with pytest.raises(ToolDenied, match="no está habilitada"):
        reg.invoke("crear_tema", {"nombre": "x", "query_busqueda": "consulta"})

    habilitadas = reg.activar_grupo("vigia")
    assert set(habilitadas) == {"listar_temas", "crear_tema", "actualizar_tema"}
    despues = {d["function"]["name"] for d in reg.definiciones()}
    assert {"listar_temas", "crear_tema", "actualizar_tema"} <= despues


def test_habilitar_vigia_devuelve_las_instrucciones_del_vigia():
    reg = construir_registro(None, TZ)
    r = reg.invoke("habilitar_vigia", {})
    assert set(r["habilitadas"]) == {"listar_temas", "crear_tema", "actualizar_tema"}
    assert "vigía de temas" in r["instrucciones"] and "crear_tema" in r["instrucciones"]
    assert {"crear_tema", "actualizar_tema"} <= {d["function"]["name"] for d in reg.definiciones()}


def test_el_grupo_se_activa_solo_por_el_tiempo_de_ese_registro():
    reg = construir_registro(None, TZ)
    reg.invoke("habilitar_vigia", {})
    otro = construir_registro(None, TZ)  # el mensaje siguiente construye un registro nuevo
    assert "crear_tema" not in {d["function"]["name"] for d in otro.definiciones()}


def test_un_grupo_desconocido_falla():
    with pytest.raises(ValueError):
        construir_registro(None, TZ).activar_grupo("no-existe")


def test_el_prompt_base_ya_no_carga_las_instrucciones_del_vigia():
    from asistente.agent.prompt import construir_prompt

    prompt = construir_prompt(datetime(2026, 9, 21, 12, 0, tzinfo=UTC), TZ)
    assert "Skill: agenda" in prompt and "Skill: procesos" in prompt
    assert "Skill: vigía de temas" not in prompt
