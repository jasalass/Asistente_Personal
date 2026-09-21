from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from asistente.agent.tools import construir_registro
from asistente.security.tool_registry import ToolArgsInvalid, ToolError

TZ = ZoneInfo("America/Santiago")


@pytest.fixture
def reg(conn):
    return construir_registro(conn, TZ)


def crear(reg, **campos):
    return reg.invoke("crear_proceso", {"nombre": "Renovar pasaporte", **campos})


def test_crear_proceso_ignora_nulos_del_modelo(reg):
    p = crear(reg, estado="en_espera", esperando_a="Registro Civil", descripcion=None, prioridad=None)
    assert p["estado"] == "en_espera" and p["esperando_a"] == "Registro Civil"
    assert p["prioridad"] == "media" and p["descripcion"] is None


def test_crear_proceso_rechaza_estado_inventado_y_campos_extra(reg):
    with pytest.raises(ToolArgsInvalid):
        crear(reg, estado="casi_listo")
    with pytest.raises(ToolArgsInvalid):
        crear(reg, id="cualquiera")


def test_error_de_validacion_no_incluye_el_valor_recibido(reg):
    with pytest.raises(ToolArgsInvalid) as e:
        crear(reg, estado="valor-secreto-123")
    assert "valor-secreto-123" not in str(e.value)


def test_actualizar_solo_lo_informado_y_null_no_borra(reg):
    p = crear(reg, esperando_a="Juan", proxima_accion="llamar")
    q = reg.invoke("actualizar_proceso", {"id": p["id"], "proxima_accion": "escribir", "esperando_a": None})
    assert q["proxima_accion"] == "escribir" and q["esperando_a"] == "Juan"


def test_actualizar_con_cadena_vacia_borra_el_campo(reg):
    p = crear(reg, esperando_a="Juan")
    q = reg.invoke("actualizar_proceso", {"id": p["id"], "esperando_a": ""})
    assert q["esperando_a"] is None


def test_actualizar_estado_deja_historial(reg):
    p = crear(reg)
    reg.invoke("actualizar_proceso", {"id": p["id"], "estado": "bloqueado", "bloqueo_detalle": "falta foto"})
    tipos = [e["tipo"] for e in reg.invoke("ver_historial_proceso", {"id": p["id"]})]
    assert "cambio_estado" in tipos


def test_proceso_inexistente_es_tool_error(reg):
    with pytest.raises(ToolError):
        reg.invoke("actualizar_proceso", {"id": "00000000-0000-0000-0000-000000000000", "nombre": "x"})
    with pytest.raises(ToolError):
        reg.invoke("agregar_nota_proceso", {"id": "00000000-0000-0000-0000-000000000000", "nota": "x"})


# ---------- por nombre, sin búsqueda previa ----------


def test_actualizar_por_nombre_sin_conocer_el_id(reg):
    p = crear(reg, nombre="Renovar pasaporte")
    q = reg.invoke("actualizar_proceso", {"proceso": "pasaporte", "estado": "en_espera"})
    assert q["id"] == p["id"] and q["estado"] == "en_espera"


def test_actualizar_con_nota_es_una_sola_llamada_y_deja_ambos_cambios(reg):
    p = crear(reg, nombre="Visa de estudiante", estado="en_espera")
    q = reg.invoke(
        "actualizar_proceso",
        {"proceso": "visa", "estado": "activo", "nota": "Llegó el certificado del banco"},
    )
    assert q["estado"] == "activo" and "nota" not in q
    tipos_y_textos = [(e["tipo"], e["contenido"]) for e in reg.invoke("ver_historial_proceso", {"id": p["id"]})]
    assert ("nota", "Llegó el certificado del banco") in tipos_y_textos
    assert ("cambio_estado", "en_espera -> activo") in tipos_y_textos


def test_actualizar_sin_nota_no_agrega_eventos_de_nota(reg):
    p = crear(reg, nombre="Sin nota")
    reg.invoke("actualizar_proceso", {"id": p["id"], "prioridad": "alta"})
    assert [e["tipo"] for e in reg.invoke("ver_historial_proceso", {"id": p["id"]})] == ["nota"]  # solo "creado"


def test_nota_e_historial_por_nombre(reg):
    p = crear(reg, nombre="Compra de notebook")
    reg.invoke("agregar_nota_proceso", {"proceso": "notebook", "nota": "Cotizé tres modelos"})
    eventos = reg.invoke("ver_historial_proceso", {"proceso": "Compra de notebook"})
    assert eventos[0]["contenido"] == "Cotizé tres modelos" and eventos[0]["proceso_id"] == p["id"]


def test_nombre_ambiguo_lista_las_opciones_y_pide_el_id(reg):
    a = crear(reg, nombre="Pasaporte de Ana")
    b = crear(reg, nombre="Pasaporte de Luis")
    with pytest.raises(ToolError) as e:
        reg.invoke("actualizar_proceso", {"proceso": "pasaporte", "prioridad": "alta"})
    msg = str(e.value)
    assert a["id"] in msg and b["id"] in msg and "id" in msg
    # con el nombre exacto sí se resuelve
    q = reg.invoke("actualizar_proceso", {"proceso": "pasaporte de ana", "prioridad": "alta"})
    assert q["id"] == a["id"] and q["prioridad"] == "alta"


def test_nombre_sin_coincidencias(reg):
    with pytest.raises(ToolError, match="No encontré"):
        reg.invoke("actualizar_proceso", {"proceso": "no existe zzz", "estado": "activo"})


def test_entre_uno_abierto_y_uno_cerrado_se_prefiere_el_abierto(reg):
    crear(reg, nombre="Trámite viejo", estado="completado")
    abierto = crear(reg, nombre="Trámite nuevo")
    q = reg.invoke("actualizar_proceso", {"proceso": "trámite", "proxima_accion": "llamar"})
    assert q["id"] == abierto["id"]


def test_hay_que_indicar_el_proceso_de_una_sola_forma(reg):
    p = crear(reg)
    for args in ({"estado": "activo"}, {"id": p["id"], "proceso": "x", "estado": "activo"}):
        with pytest.raises(ToolArgsInvalid):
            reg.invoke("actualizar_proceso", args)


def test_crear_un_proceso_abierto_duplicado_falla_pero_uno_cerrado_no_estorba(reg):
    crear(reg, nombre="Declaración de renta")
    with pytest.raises(ToolError, match="Ya existe"):
        crear(reg, nombre="declaración DE RENTA")
    hecho = crear(reg, nombre="Otra cosa", estado="completado")
    assert crear(reg, nombre=hecho["nombre"])["id"] != hecho["id"]


def test_buscar_y_listar(reg):
    p = crear(reg, nombre="Compra de notebook")
    assert p["id"] in [x["id"] for x in reg.invoke("buscar_procesos", {"texto": "notebook"})]
    assert p["id"] in [x["id"] for x in reg.invoke("listar_procesos", {"estados": ["activo"]})]


def test_nota_queda_en_el_historial(reg):
    p = crear(reg)
    reg.invoke("agregar_nota_proceso", {"id": p["id"], "nota": "Pagué el arancel", "tipo": "accion_hecha"})
    eventos = reg.invoke("ver_historial_proceso", {"id": p["id"]})
    assert eventos[0]["contenido"] == "Pagué el arancel" and eventos[0]["tipo"] == "accion_hecha"


def test_fecha_sin_zona_se_interpreta_en_hora_local(reg):
    r = reg.invoke("crear_recordatorio", {"texto": "llamar", "fecha": "2026-10-01T09:00:00"})
    fecha = datetime.fromisoformat(r["fecha"])
    assert fecha == datetime(2026, 10, 1, 9, 0, tzinfo=TZ)
    assert fecha.astimezone(UTC).hour in (12, 13)  # Chile: UTC-3 o UTC-4 según horario de verano


@pytest.mark.parametrize("sufijo", ["-04:00", "-03:00", "Z", "+09:00"])
def test_el_desfase_que_manda_el_modelo_se_ignora(reg, sufijo):
    # Regresión: el modelo mandaba -04:00 en verano (Chile -03:00) y la fecha se corría 1 hora.
    r = reg.invoke("crear_recordatorio", {"texto": "x", "fecha": f"2026-10-15T13:00:00{sufijo}"})
    assert datetime.fromisoformat(r["fecha"]) == datetime(2026, 10, 15, 13, 0, tzinfo=TZ)


def test_el_desfase_tambien_se_ignora_en_los_procesos(reg):
    p = crear(reg, proxima_accion_fecha="2026-10-15T13:00:00-04:00")
    assert datetime.fromisoformat(p["proxima_accion_fecha"]) == datetime(2026, 10, 15, 13, 0, tzinfo=TZ)
    q = reg.invoke(
        "actualizar_proceso", {"id": p["id"], "proxima_accion_fecha": "2026-10-16T09:30:00-04:00"}
    )
    assert datetime.fromisoformat(q["proxima_accion_fecha"]) == datetime(2026, 10, 16, 9, 30, tzinfo=TZ)


def test_memoria_se_guarda_como_del_usuario_y_se_busca(reg):
    m = reg.invoke("guardar_memoria", {"contenido": "Prefiere el café sin azúcar", "etiquetas": ["gustos"]})
    assert m["origen"] == "usuario"
    assert [x["id"] for x in reg.invoke("buscar_memorias", {"consulta": "cafe"})] == [m["id"]]


def test_el_esquema_para_el_llm_incluye_todas_las_tools(reg):
    nombres = {d["function"]["name"] for d in reg.definiciones()}
    assert nombres == {
        "listar_procesos", "buscar_procesos", "crear_proceso", "actualizar_proceso",
        "agregar_nota_proceso", "ver_historial_proceso", "guardar_memoria",
        "buscar_memorias", "crear_recordatorio", "listar_temas", "crear_tema", "actualizar_tema",
    }
