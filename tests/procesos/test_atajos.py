from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from asistente.db.models import Prioridad, Proceso, ProcesoEstado
from asistente.procesos.atajos import ConsultaDeProcesos, detectar_consulta, redactar

TZ = ZoneInfo("America/Santiago")


@pytest.mark.parametrize(
    "frase",
    [
        "como van mis procesos?",
        "Cómo van mis trámites",
        "mis procesos",
        "mis tramites",
        "que procesos tengo",
        "resumen de procesos",
        "resumen de tramites",
        "lista de procesos",
    ],
)
def test_frases_reconocidas_como_abiertos(frase):
    c = detectar_consulta(frase)
    assert c is not None and c.etiqueta == "abiertos"
    assert set(c.estados) == {
        ProcesoEstado.IDEA, ProcesoEstado.ACTIVO, ProcesoEstado.EN_ESPERA, ProcesoEstado.BLOQUEADO,
    }


@pytest.mark.parametrize(
    ("frase", "estado"),
    [
        ("que procesos estan bloqueados", ProcesoEstado.BLOQUEADO),
        ("que procesos bloqueados tengo", ProcesoEstado.BLOQUEADO),
        ("procesos bloqueados", ProcesoEstado.BLOQUEADO),
        ("que procesos estan activos", ProcesoEstado.ACTIVO),
        ("que procesos estan en espera", ProcesoEstado.EN_ESPERA),
        ("que tramites estan completados", ProcesoEstado.COMPLETADO),
        ("procesos cancelados", ProcesoEstado.CANCELADO),
    ],
)
def test_frases_filtradas_por_estado(frase, estado):
    c = detectar_consulta(frase)
    assert c is not None and c.estados == (estado,)


@pytest.mark.parametrize(
    "frase",
    [
        "cancela el proceso del pasaporte",  # es una orden, no una consulta
        "que tengo hoy",  # es el atajo de agenda
        "crea un proceso para renovar el pasaporte",
        "como va el proceso del pasaporte",  # menciona uno específico: decide el modelo
        "que procesos tengo urgentes",  # "urgentes" no es un estado válido
        "",
        "   ",
        "hola",
        "como van mis procesos " + "x" * 60,
    ],
)
def test_todo_lo_demas_lo_decide_el_modelo(frase):
    assert detectar_consulta(frase) is None


# ---------- el texto se compone con lo guardado, sin paráfrasis ----------


def proceso(**kw) -> Proceso:
    base = {
        "id": "00000000-0000-0000-0000-000000000001", "nombre": "Renovar pasaporte",
        "descripcion": None, "estado": ProcesoEstado.ACTIVO, "prioridad": Prioridad.MEDIA,
        "proxima_accion": None, "proxima_accion_fecha": None, "esperando_a": None, "bloqueo_detalle": None,
        "fecha_limite": None, "etiquetas": [], "frecuencia_chequeo_dias": None, "ultimo_chequeo": None,
        "creado_en": datetime(2026, 1, 1, tzinfo=TZ), "actualizado_en": datetime(2026, 1, 1, tzinfo=TZ),
    }
    return Proceso(**{**base, **kw})


def test_sin_procesos_el_mensaje_usa_la_etiqueta():
    assert redactar([], ConsultaDeProcesos((), "abiertos"), TZ) == "No tienes procesos abiertos."
    consulta = ConsultaDeProcesos((ProcesoEstado.BLOQUEADO,), "bloqueados")
    assert redactar([], consulta, TZ) == "No tienes procesos bloqueados."


def test_con_procesos_se_usa_formatear_lista():
    texto = redactar([proceso()], ConsultaDeProcesos((ProcesoEstado.ACTIVO,), "activos"), TZ)
    assert texto == "• Renovar pasaporte — activo."
