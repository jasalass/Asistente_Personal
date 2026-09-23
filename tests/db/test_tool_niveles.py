from asistente.db.repos.tool_niveles import NivelesRepo


def test_sin_overrides_esta_vacio(conn_aprobaciones):
    assert NivelesRepo(conn_aprobaciones).obtener_todos() == {}


def test_fijar_y_leer(conn_aprobaciones):
    repo = NivelesRepo(conn_aprobaciones)
    repo.fijar("cancelar_recordatorio", "propone")
    assert repo.obtener_todos() == {"cancelar_recordatorio": "propone"}


def test_fijar_de_nuevo_reemplaza_el_valor(conn_aprobaciones):
    repo = NivelesRepo(conn_aprobaciones)
    repo.fijar("cancelar_recordatorio", "propone")
    repo.fijar("cancelar_recordatorio", "auto")
    assert repo.obtener_todos() == {"cancelar_recordatorio": "auto"}


def test_quitar_borra_el_override(conn_aprobaciones):
    repo = NivelesRepo(conn_aprobaciones)
    repo.fijar("cancelar_recordatorio", "propone")
    repo.quitar("cancelar_recordatorio")
    assert repo.obtener_todos() == {}


def test_quitar_uno_que_no_existia_no_falla(conn_aprobaciones):
    NivelesRepo(conn_aprobaciones).quitar("no_existe")  # no debe lanzar


def test_disponible_es_true_una_vez_aplicada_la_migracion(conn_aprobaciones):
    assert NivelesRepo(conn_aprobaciones).disponible() is True
