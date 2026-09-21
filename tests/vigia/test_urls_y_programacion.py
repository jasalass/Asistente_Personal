from datetime import UTC, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from asistente.db.models import Frecuencia, Tema
from asistente.vigia.programacion import toca
from asistente.vigia.urls import dominio, es_url_publica, normalizar_url

TZ = ZoneInfo("America/Santiago")
AHORA = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)  # 09:00 en Chile


# ---------- URLs ----------


@pytest.mark.parametrize(
    "url",
    ["https://example.com/nota", "http://noticias.cl/a?x=1", "https://8.8.8.8/x"],
)
def test_urls_publicas_validas(url):
    assert es_url_publica(url)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "javascript:alert(1)",
        "ftp://example.com/x",
        "file:///etc/passwd",
        "http://localhost/admin",
        "http://127.0.0.1:8000/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://intranet/wiki",
        "http://servidor.local/x",
        "https://usuario:clave@example.com/",
        "https://example.com/con espacio",
        "https://" + "a" * 2100 + ".com",
    ],
)
def test_urls_peligrosas_o_invalidas_se_rechazan(url):
    assert not es_url_publica(url)


def test_normalizar_unifica_variantes_de_la_misma_nota():
    base = "https://noticias.cl/nota-1"
    variantes = [
        "http://www.noticias.cl/nota-1/",
        "https://noticias.cl/nota-1?utm_source=x&utm_medium=y",
        "https://NOTICIAS.cl/nota-1#comentarios",
        "https://noticias.cl/nota-1?fbclid=abc",
    ]
    assert {normalizar_url(v) for v in [base, *variantes]} == {base}


def test_normalizar_conserva_los_parametros_que_identifican_el_contenido():
    assert normalizar_url("https://x.com/ver?id=5&utm_campaign=z") == "https://x.com/ver?id=5"
    assert normalizar_url("https://x.com/ver?b=2&a=1") == normalizar_url("https://x.com/ver?a=1&b=2")
    assert normalizar_url("https://x.com/ver?id=5") != normalizar_url("https://x.com/ver?id=6")


def test_normalizar_rechaza_lo_inseguro_y_conserva_la_raiz():
    assert normalizar_url("http://localhost/x") is None
    assert normalizar_url("https://example.com/") == "https://example.com/"


def test_dominio():
    assert dominio("https://www.Ejemplo.com/a") == "ejemplo.com"


# ---------- calendario ----------


def hacer_tema(**kw) -> Tema:
    datos = {
        "id": uuid4(), "nombre": "IA", "query_busqueda": "inteligencia artificial",
        "tipo_contenido": "mixto", "frecuencia": Frecuencia.DIARIA, "intervalo_dias": None,
        "dias_semana": None, "hora_preferida": time(8, 0), "ventana_frescura_horas": 48,
        "cantidad_resultados": 5, "avisar_sin_novedades": False, "activo": True,
        "ultima_ejecucion": None, "creado_en": AHORA, "actualizado_en": AHORA,
    }
    return Tema(**{**datos, **kw})


def test_diaria_corre_una_vez_por_dia():
    assert toca(hacer_tema(), AHORA, TZ)  # nunca corrió
    assert not toca(hacer_tema(ultima_ejecucion=AHORA - timedelta(hours=1)), AHORA, TZ)  # ya hoy
    assert toca(hacer_tema(ultima_ejecucion=AHORA - timedelta(days=1)), AHORA, TZ)


def test_no_corre_antes_de_su_hora_preferida():
    temprano = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)  # 07:00 en Chile
    assert not toca(hacer_tema(), temprano, TZ)
    assert toca(hacer_tema(hora_preferida=time(6, 30)), temprano, TZ)


def test_tema_inactivo_no_corre():
    assert not toca(hacer_tema(activo=False), AHORA, TZ)


def test_cada_x_dias():
    tema = lambda dias: hacer_tema(
        frecuencia=Frecuencia.CADA_X_DIAS, intervalo_dias=3, ultima_ejecucion=AHORA - timedelta(days=dias)
    )
    assert not toca(tema(2), AHORA, TZ)
    assert toca(tema(3), AHORA, TZ)
    assert toca(hacer_tema(frecuencia=Frecuencia.CADA_X_DIAS, intervalo_dias=3), AHORA, TZ)


def test_dias_especificos():
    hoy = AHORA.astimezone(TZ).isoweekday()
    manana = hoy % 7 + 1
    assert toca(hacer_tema(frecuencia=Frecuencia.DIAS_ESPECIFICOS, dias_semana=[hoy]), AHORA, TZ)
    assert not toca(hacer_tema(frecuencia=Frecuencia.DIAS_ESPECIFICOS, dias_semana=[manana]), AHORA, TZ)


def test_la_fecha_del_dia_se_mide_en_hora_local_no_utc():
    # 02:00 UTC del 22 = 23:00 del 21 en Chile: la corrida de las 09:00 del 21 sigue siendo "hoy".
    tarde = datetime(2026, 9, 22, 2, 0, tzinfo=UTC)
    assert not toca(hacer_tema(ultima_ejecucion=AHORA), tarde, TZ)


def test_los_modelos_de_tema_validan_coherencia():
    with pytest.raises(ValueError):
        hacer_tema(frecuencia=Frecuencia.CADA_X_DIAS, intervalo_dias=None)
    with pytest.raises(ValueError):
        hacer_tema(frecuencia=Frecuencia.DIAS_ESPECIFICOS, dias_semana=[])
    with pytest.raises(ValueError):
        hacer_tema(frecuencia=Frecuencia.DIAS_ESPECIFICOS, dias_semana=[8])
