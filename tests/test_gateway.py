from asistente.gateway import cadena_de_modelos


def test_el_principal_va_primero():
    assert cadena_de_modelos("a", ["b", "c"]) == ["a", "b", "c"]


def test_un_respaldo_igual_al_principal_no_se_repite():
    assert cadena_de_modelos("a", ["a", "b"]) == ["a", "b"]


def test_respaldos_repetidos_entre_si_solo_cuentan_una_vez():
    assert cadena_de_modelos("a", ["b", "b", "c"]) == ["a", "b", "c"]


def test_sin_respaldos_la_cadena_es_solo_el_principal():
    assert cadena_de_modelos("a", []) == ["a"]


def test_un_respaldo_vacio_se_ignora():
    assert cadena_de_modelos("a", ["", "b"]) == ["a", "b"]
