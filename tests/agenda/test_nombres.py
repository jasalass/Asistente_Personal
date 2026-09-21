import pytest

from asistente.agenda.nombres import MAX_NOMBRE, normalizar_nombre, validar_nombre

CARRERA = "INGENIERÍA EN INFORMÁTICA (DESARROLLO DE SOFTWARE)"


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        # Los cuatro casos reales de un horario pegado: sin el nombre de la carrera y en formato título.
        (f"{CARRERA} DSY1104 DESARROLLO FULLSTACK II", "DSY1104 Desarrollo Fullstack II"),
        (f"{CARRERA} DSY1107 DESARROLLO CLOUD NATIVE I", "DSY1107 Desarrollo Cloud Native I"),
        (f"{CARRERA} INU3100 INGLÉS ELEMENTAL I", "INU3100 Inglés Elemental I"),
        ("Clases de MATEMATICA APLICADA_006V", "Clases de Matematica Aplicada_006V"),
    ],
)
def test_nombres_reales_de_un_horario(crudo, esperado):
    assert normalizar_nombre(crudo) == esperado


@pytest.mark.parametrize(
    "nombre", ["Reunión de equipo", "DSY1104 Desarrollo Fullstack II", "Yoga de los martes", "Vuelo a Lima"]
)
def test_lo_que_ya_esta_bien_no_se_toca(nombre):
    assert normalizar_nombre(nombre) == nombre


def test_un_codigo_al_inicio_se_respeta():
    assert normalizar_nombre("MAT101 ÁLGEBRA LINEAL") == "MAT101 Álgebra Lineal"


def test_los_numeros_romanos_de_nivel_se_conservan_pero_no_las_palabras_parecidas():
    assert normalizar_nombre("INGLÉS ELEMENTAL III") == "Inglés Elemental III"
    assert normalizar_nombre("INGENIERÍA CIVIL DIV MIX") == "Ingeniería Civil Div Mix"  # no son romanos


def test_las_palabras_cortas_van_en_minuscula_salvo_al_inicio():
    assert normalizar_nombre("EL DESARROLLO DE LA WEB Y LOS DATOS") == "El Desarrollo de la Web y los Datos"


def test_espacios_y_bordes_se_limpian():
    assert normalizar_nombre("  Reunión    de   equipo - ") == "Reunión de equipo"


def test_es_idempotente():
    for crudo in (f"{CARRERA} DSY1104 DESARROLLO FULLSTACK II", "Clases de MATEMATICA APLICADA_006V", "YOGA"):
        una_vez = normalizar_nombre(crudo)
        assert normalizar_nombre(una_vez) == una_vez


def test_el_encabezado_solo_se_descarta_si_es_largo():
    # Un prefijo corto antes del código no es un "encabezado de carrera": se conserva.
    assert normalizar_nombre("Taller DSY1104 de repaso") == "Taller DSY1104 de repaso"


def test_un_nombre_demasiado_largo_se_rechaza_con_indicaciones():
    largo = "Reunión semanal de coordinación con todos los equipos de producto y de ingeniería"
    with pytest.raises(ValueError) as e:
        validar_nombre(largo)
    assert str(MAX_NOMBRE) in str(e.value) and "descripcion" in str(e.value)


def test_el_limite_se_mide_despues_de_normalizar():
    # Crudo son 82 caracteres, pero sin la carrera queda corto y es válido.
    crudo = f"{CARRERA} DSY1104 DESARROLLO FULLSTACK II"
    assert len(crudo) > MAX_NOMBRE
    assert validar_nombre(crudo) == "DSY1104 Desarrollo Fullstack II"


def test_un_nombre_vacio_se_rechaza():
    for vacio in ("", "   ", " - "):
        with pytest.raises(ValueError, match="vacío"):
            validar_nombre(vacio)
