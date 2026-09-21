from datetime import date, time

import pytest

from asistente.agenda.atajos import ConsultaDeAgenda, detectar_consulta, redactar
from asistente.agenda.consulta import DiaAgenda, ItemAgenda

LUNES = date(2026, 9, 21)


@pytest.mark.parametrize(
    ("frase", "desde", "dias"),
    [
        ("que tengo hoy?", LUNES, 1),
        ("Qué tengo hoy", LUNES, 1),
        ("y hoy?", LUNES, 1),  # así lo escribió el usuario
        ("qué tengo para hoy", LUNES, 1),
        ("agenda de hoy", LUNES, 1),
        ("mi agenda hoy", LUNES, 1),
        ("que tengo mañana?", date(2026, 9, 22), 1),
        ("y mañana?", date(2026, 9, 22), 1),
        ("qué tengo para mañana", date(2026, 9, 22), 1),
        ("que tengo pasado mañana", date(2026, 9, 23), 1),
        ("que tengo esta semana", LUNES, 7),
        ("qué tengo la semana", LUNES, 7),
        ("que tengo la proxima semana", date(2026, 9, 28), 7),
        ("que tengo los próximos 5 días", LUNES, 5),
        ("Q tengo hoy??", LUNES, 1),
    ],
)
def test_frases_de_consulta_reconocidas(frase, desde, dias):
    c = detectar_consulta(frase, LUNES)
    assert c is not None and (c.desde, c.dias) == (desde, dias)


@pytest.mark.parametrize(
    ("frase", "esperada"),
    [
        ("que tengo el lunes", date(2026, 9, 21)),  # hoy es lunes: hoy mismo
        ("que tengo el martes", date(2026, 9, 22)),
        ("qué tengo este viernes", date(2026, 9, 25)),
        ("que tengo el miércoles", date(2026, 9, 23)),
        ("que tengo el domingo", date(2026, 9, 27)),
    ],
)
def test_un_dia_de_la_semana_es_su_proxima_ocurrencia(frase, esperada):
    assert detectar_consulta(frase, LUNES).desde == esperada


@pytest.mark.parametrize(
    "frase",
    [
        "borra lo de hoy",
        "recuérdame mañana a las 9 llamar al banco",
        "que tengo que estudiar hoy",  # contiene "que tengo" pero es otra cosa
        "ya no tengo ingles, eliminalo",
        "cancela la clase de mañana",
        "que tengo hoy? y borra ingles",  # consulta + orden: decide el modelo
        "tengo clases mañana?",
        "que tengo los proximos 15 dias",  # fuera del máximo de 14
        "que tengo los proximos 0 dias",
        "",
        "   ",
        "hola",
        "que tengo hoy " + "x" * 80,  # demasiado largo para ser una consulta corta
    ],
)
def test_todo_lo_demas_lo_decide_el_modelo(frase):
    assert detectar_consulta(frase, LUNES) is None


# ---------- el texto se compone con lo guardado, sin paráfrasis ----------


def dia(fecha, *textos, feriado=None):
    return DiaAgenda(fecha, feriado, [ItemAgenda(time(8, 0), "evento", t) for t in textos])


def test_un_dia_con_eventos_se_muestra_tal_cual_esta_guardado():
    detalle = "08:01 DSY1107 Desarrollo Cloud Native I (79 min) — Profesor: IGNACIO ANDRES CUTURRUFO GONZALEZ, Sala TP2"
    texto = redactar([dia(date(2026, 9, 22), detalle)], ConsultaDeAgenda(date(2026, 9, 22), 1, "mañana"), LUNES)
    assert texto == f"**Mañana, martes 22/09**\n• {detalle}"
    assert "CUTURRUFO GONZALEZ" in texto  # el modelo lo había convertido en "Caturrufo"


def test_los_titulos_son_relativos_para_hoy_manana_y_pasado_manana():
    dias = [dia(date(2026, 9, 21), "a"), dia(date(2026, 9, 22), "b"), dia(date(2026, 9, 23), "c"), dia(date(2026, 9, 25), "d")]
    texto = redactar(dias, ConsultaDeAgenda(LUNES, 7, "esta semana"), LUNES)
    assert "**Hoy, lunes 21/09**" in texto and "**Mañana, martes 22/09**" in texto
    assert "**Pasado mañana, miércoles 23/09**" in texto and "**Viernes 25/09**" in texto


def test_en_una_semana_se_omiten_los_dias_vacios():
    dias = [dia(date(2026, 9, 21), "20:30 Clase"), dia(date(2026, 9, 22)), dia(date(2026, 9, 23))]
    texto = redactar(dias, ConsultaDeAgenda(LUNES, 3, "esta semana"), LUNES)
    assert texto.count("**") == 2 and "martes" not in texto and "miércoles" not in texto


def test_sin_nada_agendado():
    assert redactar([dia(LUNES)], ConsultaDeAgenda(LUNES, 1, "hoy"), LUNES) == "No tienes nada agendado para hoy."
    assert redactar([dia(LUNES), dia(date(2026, 9, 22))], ConsultaDeAgenda(LUNES, 7, "esta semana"), LUNES) == (
        "No tienes nada agendado para esta semana."
    )


def test_un_dia_feriado_se_marca_y_se_explica_aunque_este_vacio():
    feriado = date(2026, 10, 12)
    con_evento = redactar([dia(feriado, "20:30 Clase — SUSPENDIDO (feriado: X)", feriado="Encuentro de Dos Mundos")],
                          ConsultaDeAgenda(feriado, 1, "el lunes"), LUNES)
    assert "(feriado: Encuentro de Dos Mundos)" in con_evento and "SUSPENDIDO" in con_evento
    vacio = redactar([dia(feriado, feriado="Encuentro de Dos Mundos")], ConsultaDeAgenda(feriado, 1, "el lunes"), LUNES)
    assert vacio == "No tienes nada agendado para el lunes (feriado: Encuentro de Dos Mundos)."
