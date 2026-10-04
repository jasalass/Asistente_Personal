import json

from asistente.respaldo import TABLAS, escribir, exportar


def test_exporta_todas_las_tablas_de_datos_personales(conn):
    datos = exportar(conn)
    assert set(datos["tablas"]) == set(TABLAS)
    assert "creado" in datos


def test_escribe_un_json_legible_con_fechas_como_texto(conn, tmp_path):
    from asistente.db.models import MemoriaOrigen
    from asistente.db.repos.memorias import MemoriaRepo

    MemoriaRepo(conn).guardar("dato de prueba", MemoriaOrigen.USUARIO, ["prueba"])
    destino = escribir(exportar(conn), tmp_path)
    cargado = json.loads(destino.read_text(encoding="utf-8"))
    assert any(m["contenido"] == "dato de prueba" for m in cargado["tablas"]["memorias"])
