from datetime import date, datetime
from zoneinfo import ZoneInfo

from asistente.db.models import Prioridad, Proceso, ProcesoEstado
from asistente.procesos.redaccion import describir_proceso, formatear_lista

TZ = ZoneInfo("America/Santiago")


def proceso(**kw) -> Proceso:
    base = {
        "id": "00000000-0000-0000-0000-000000000001",
        "nombre": "Renovar pasaporte",
        "descripcion": None,
        "estado": ProcesoEstado.ACTIVO,
        "prioridad": Prioridad.MEDIA,
        "proxima_accion": None,
        "proxima_accion_fecha": None,
        "esperando_a": None,
        "bloqueo_detalle": None,
        "fecha_limite": None,
        "etiquetas": [],
        "frecuencia_chequeo_dias": None,
        "ultimo_chequeo": None,
        "creado_en": datetime(2026, 1, 1, tzinfo=TZ),
        "actualizado_en": datetime(2026, 1, 1, tzinfo=TZ),
    }
    return Proceso(**{**base, **kw})


def test_lo_minimo_es_nombre_y_estado():
    assert describir_proceso(proceso(), TZ) == "Renovar pasaporte — activo."


def test_en_espera_muestra_a_quien():
    p = proceso(estado=ProcesoEstado.EN_ESPERA, esperando_a="Registro Civil")
    assert describir_proceso(p, TZ) == "Renovar pasaporte — en espera (de Registro Civil)."


def test_bloqueado_muestra_el_detalle():
    p = proceso(estado=ProcesoEstado.BLOQUEADO, bloqueo_detalle="falta el pago")
    assert describir_proceso(p, TZ) == "Renovar pasaporte — bloqueado: falta el pago."


def test_proxima_accion_con_y_sin_fecha():
    sin_fecha = proceso(proxima_accion="retirar documentos")
    assert describir_proceso(sin_fecha, TZ) == "Renovar pasaporte — activo. próximo: retirar documentos."

    con_fecha = proceso(
        proxima_accion="retirar documentos",
        proxima_accion_fecha=datetime(2026, 9, 22, 18, 0, tzinfo=TZ),
    )
    assert describir_proceso(con_fecha, TZ) == (
        "Renovar pasaporte — activo. próximo: retirar documentos (22/09 18:00)."
    )


def test_fecha_limite_y_prioridad_no_media():
    p = proceso(fecha_limite=date(2026, 10, 1), prioridad=Prioridad.ALTA)
    assert describir_proceso(p, TZ) == "Renovar pasaporte — activo. fecha límite 01/10. prioridad alta."


def test_prioridad_media_no_se_menciona():
    assert "prioridad" not in describir_proceso(proceso(prioridad=Prioridad.MEDIA), TZ)


def test_formatear_lista_vacia():
    assert formatear_lista([], TZ) == "No tienes procesos abiertos."


def test_formatear_lista_una_linea_por_proceso_en_el_orden_dado():
    a, b = proceso(nombre="A"), proceso(nombre="B", estado=ProcesoEstado.BLOQUEADO)
    texto = formatear_lista([a, b], TZ)
    assert texto == "• A — activo.\n• B — bloqueado."
