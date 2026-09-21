import pytest
from pydantic import ValidationError

from asistente.bloqueo import BloqueoInstancia
from asistente.config import get_settings

# Clave propia de los tests: el bot real (si está corriendo) usa otra y no interfiere.
CLAVE_TEST = (1095324755, 999)


@pytest.fixture
def dsn():
    try:
        return get_settings().database_url.get_secret_value()
    except ValidationError:
        pytest.skip("Sin configuración de base de datos (.env)")


@pytest.fixture
def bloqueos(dsn):
    creados: list[BloqueoInstancia] = []

    def crear() -> BloqueoInstancia:
        b = BloqueoInstancia(dsn, clave=CLAVE_TEST)
        creados.append(b)
        return b

    yield crear
    for b in creados:
        b.liberar()


def test_solo_una_instancia_puede_tomar_el_bloqueo(bloqueos):
    primera, segunda = bloqueos(), bloqueos()
    assert primera.intentar() is True
    assert segunda.intentar() is False
    assert segunda.vigente() is False


def test_al_liberar_otra_instancia_puede_tomarlo(bloqueos):
    primera, segunda = bloqueos(), bloqueos()
    assert primera.intentar()
    assert not segunda.intentar()
    primera.liberar()
    assert segunda.intentar() is True


def test_vigente_refleja_si_seguimos_teniendo_el_bloqueo(bloqueos):
    b = bloqueos()
    assert b.vigente() is False  # antes de tomarlo
    assert b.intentar()
    assert b.vigente() is True
    b.liberar()
    assert b.vigente() is False


def test_si_muere_la_conexion_ya_no_es_vigente_y_otro_lo_puede_tomar(bloqueos):
    b, otro = bloqueos(), bloqueos()
    assert b.intentar()
    b._conn.close()  # simula el corte de la conexión (o un reinicio del pooler)
    assert b.vigente() is False
    assert otro.intentar() is True


def test_los_bloqueos_con_distinta_clave_no_se_estorban(dsn):
    a, b = BloqueoInstancia(dsn, clave=(1095324755, 998)), BloqueoInstancia(dsn, clave=(1095324755, 997))
    try:
        assert a.intentar() and b.intentar()
    finally:
        a.liberar()
        b.liberar()


def test_esperar_termina_cuando_el_otro_lo_suelta(bloqueos):
    titular, espera = bloqueos(), bloqueos()
    assert titular.intentar()
    dormidas = []

    def dormir(segundos):
        dormidas.append(segundos)
        titular.liberar()  # mientras la segunda espera, la primera termina

    espera.esperar(espera_s=7, dormir=dormir)
    assert dormidas == [7] and espera.vigente()


def test_esperar_con_limite_falla_si_el_otro_no_lo_suelta(bloqueos):
    titular, espera = bloqueos(), bloqueos()
    assert titular.intentar()
    with pytest.raises(TimeoutError):
        espera.esperar(intentos=3, dormir=lambda s: None)
    assert titular.vigente()  # el titular no se vio afectado


def test_esperar_sin_competencia_no_espera(bloqueos):
    b = bloqueos()
    b.esperar(dormir=lambda s: pytest.fail("no debería esperar"))
    assert b.vigente()
