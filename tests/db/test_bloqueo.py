import psycopg
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


# ---------- conservar(): un corte de red no es lo mismo que perder el bloqueo ----------


def test_conservar_es_true_mientras_el_bloqueo_sea_nuestro(bloqueos):
    b = bloqueos()
    assert b.intentar()
    assert b.conservar() is True


def test_tras_un_corte_de_conexion_se_recupera_el_bloqueo_si_nadie_lo_tomo(bloqueos, caplog):
    b = bloqueos()
    assert b.intentar()
    b._conn.close()  # el wifi se cayó: la conexión a la base murió y Postgres soltó el bloqueo
    assert b.vigente() is False
    with caplog.at_level("INFO", logger="asistente.bloqueo"):
        assert b.conservar() is True  # nadie más lo tomó: se vuelve a tomar
    assert b.vigente() is True
    assert "se recuperó el bloqueo" in caplog.text  # queda rastro en el log


def test_si_durante_el_corte_otra_instancia_lo_tomo_hay_que_detenerse(bloqueos):
    b, otra = bloqueos(), bloqueos()
    assert b.intentar()
    b._conn.close()
    assert otra.intentar()  # mientras tanto, otra instancia (p. ej. en la nube) se hizo cargo
    assert b.conservar() is False  # aquí SÍ hay que apagarse para no duplicar
    assert otra.vigente() is True


def test_sin_red_no_se_puede_saber_y_no_se_lanza_excepcion(bloqueos, monkeypatch):
    b = bloqueos()
    assert b.intentar()
    b._conn.close()

    def sin_red(*args, **kwargs):
        raise psycopg.OperationalError("could not translate host name")

    monkeypatch.setattr(psycopg, "connect", sin_red)
    assert b.conservar() is None  # ni True ni False: se sigue esperando
    assert b._conn is None


def test_al_volver_la_red_la_misma_comprobacion_decide(bloqueos, monkeypatch):
    b = bloqueos()
    assert b.intentar()
    b._conn.close()
    conectar = psycopg.connect
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: (_ for _ in ()).throw(psycopg.OperationalError("sin red")))
    assert b.conservar() is None
    monkeypatch.setattr(psycopg, "connect", conectar)  # vuelve internet
    assert b.conservar() is True


def test_esperar_tolera_arrancar_sin_conexion(bloqueos, monkeypatch):
    b = bloqueos()
    intentos = []
    original = b.intentar

    def intentar_con_fallas():
        intentos.append(1)
        if len(intentos) <= 2:
            raise psycopg.OperationalError("sin red")
        return original()

    monkeypatch.setattr(b, "intentar", intentar_con_fallas)
    dormidas = []
    b.esperar(espera_s=3, dormir=dormidas.append)  # antes esto lanzaba la excepción y el proceso moría
    assert dormidas == [3, 3] and b.vigente()


def test_esperar_con_limite_y_sin_red_falla_con_un_motivo_claro(bloqueos, monkeypatch):
    b = bloqueos()

    def sin_red():
        raise psycopg.OperationalError("sin red")

    monkeypatch.setattr(b, "intentar", sin_red)
    with pytest.raises(TimeoutError, match="Sin conexión a la base"):
        b.esperar(intentos=2, dormir=lambda s: None)


def test_esperar_sin_competencia_no_espera(bloqueos):
    b = bloqueos()
    b.esperar(dormir=lambda s: pytest.fail("no debería esperar"))
    assert b.vigente()
