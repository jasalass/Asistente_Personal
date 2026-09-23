from zoneinfo import ZoneInfo

import pytest

from asistente.agent.tools import GRUPO_AUTORIDAD, construir_registro
from asistente.security.tool_registry import ToolError

TZ = ZoneInfo("America/Santiago")


def con_autoridad(conn):
    """Como construir_registro, pero con el grupo de niveles ya habilitado (bajo demanda)."""
    reg = construir_registro(conn, TZ)
    reg.activar_grupo(GRUPO_AUTORIDAD)
    return reg


@pytest.fixture
def reg(conn_aprobaciones):
    return con_autoridad(conn_aprobaciones)


def test_las_tools_de_nivel_estan_ocultas_hasta_habilitarlas(conn_aprobaciones):
    from asistente.security.tool_registry import ToolDenied

    reg = construir_registro(conn_aprobaciones, TZ)
    with pytest.raises(ToolDenied):
        reg.invoke("listar_niveles_tool", {})
    reg.invoke("habilitar_configuracion_de_niveles", {})
    assert reg.invoke("listar_niveles_tool", {})["crear_evento"] == "auto"


def test_listar_niveles_muestra_todas_en_auto_por_defecto(reg):
    niveles = reg.invoke("listar_niveles_tool", {})
    assert niveles["cancelar_recordatorio"] == "auto"
    assert niveles["crear_evento"] == "auto"


def test_configurar_a_propone_cambia_el_nivel_efectivo_y_queda_guardado(reg):
    r = reg.invoke("configurar_nivel_tool", {"tool": "cancelar_recordatorio", "nivel": "propone"})
    assert r == {"tool": "cancelar_recordatorio", "nivel": "propone"}
    assert reg.invoke("listar_niveles_tool", {})["cancelar_recordatorio"] == "propone"


def test_el_override_sobrevive_a_un_nuevo_registro_desde_la_misma_conexion(conn_aprobaciones):
    reg1 = con_autoridad(conn_aprobaciones)
    reg1.invoke("configurar_nivel_tool", {"tool": "cancelar_recordatorio", "nivel": "propone"})
    reg2 = con_autoridad(conn_aprobaciones)
    assert reg2.invoke("listar_niveles_tool", {})["cancelar_recordatorio"] == "propone"


def test_volver_a_predeterminado_borra_el_override(conn_aprobaciones):
    reg1 = con_autoridad(conn_aprobaciones)
    reg1.invoke("configurar_nivel_tool", {"tool": "cancelar_recordatorio", "nivel": "propone"})
    reg1.invoke("configurar_nivel_tool", {"tool": "cancelar_recordatorio", "nivel": "predeterminado"})
    reg2 = con_autoridad(conn_aprobaciones)
    assert reg2.invoke("listar_niveles_tool", {})["cancelar_recordatorio"] == "auto"


def test_no_se_puede_configurar_una_tool_inexistente(reg):
    with pytest.raises(ToolError, match="No existe"):
        reg.invoke("configurar_nivel_tool", {"tool": "borrar_todo", "nivel": "auto"})


def test_sin_la_migracion_aplicada_se_avisa_en_vez_de_fallar_feo(conn):
    from asistente.db.repos.tool_niveles import NivelesRepo

    if NivelesRepo(conn).disponible():
        pytest.skip("La migración 0006 ya está aplicada en esta base; nada que probar aquí")
    reg = con_autoridad(conn)  # conn normal, sin garantizar que 0006 esté aplicada
    with pytest.raises(ToolError, match="0006_aprobaciones"):
        reg.invoke("configurar_nivel_tool", {"tool": "cancelar_recordatorio", "nivel": "propone"})
