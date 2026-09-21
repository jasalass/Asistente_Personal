import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from asistente.db.models import TemaTipo
from asistente.vigia.tavily import BusquedaFallida, TavilyBuscador, _rango
from tests.vigia.test_urls_y_programacion import AHORA, hacer_tema

CLAVE = "tvly-clave-super-secreta"


def buscador(handler) -> TavilyBuscador:
    return TavilyBuscador(CLAVE, cliente=httpx.Client(transport=httpx.MockTransport(handler)))


def respuesta(resultados, status=200):
    return httpx.Response(status, json={"results": resultados})


def rfc(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


def test_arma_la_peticion_para_noticias():
    visto = {}

    def handler(req: httpx.Request):
        visto["json"], visto["auth"] = json.loads(req.content), req.headers["authorization"]
        return respuesta([])

    buscador(handler).buscar(hacer_tema(tipo_contenido=TemaTipo.NOTICIAS), AHORA)
    assert visto["auth"] == f"Bearer {CLAVE}"
    assert visto["json"]["topic"] == "news" and visto["json"]["time_range"] == "week"
    assert visto["json"]["max_results"] == 10 and visto["json"]["query"] == "inteligencia artificial"
    assert "include_domains" not in visto["json"]


def test_papers_se_limitan_a_dominios_academicos():
    visto = {}

    def handler(req):
        visto["json"] = json.loads(req.content)
        return respuesta([])

    buscador(handler).buscar(hacer_tema(tipo_contenido=TemaTipo.PAPERS), AHORA)
    assert visto["json"]["topic"] == "general"
    assert "arxiv.org" in visto["json"]["include_domains"]


@pytest.mark.parametrize(
    ("horas", "rango"), [(12, "day"), (24, "day"), (25, "week"), (168, "week"), (200, "month"), (900, "year")]
)
def test_rango_de_tiempo_cubre_la_ventana(horas, rango):
    assert _rango(horas) == rango


def test_parsea_y_filtra_por_frescura():
    reciente = AHORA - timedelta(hours=5)
    vieja = AHORA - timedelta(days=10)
    crudos = [
        {"url": "https://a.com/1", "title": "Reciente", "content": "texto", "published_date": rfc(reciente)},
        {"url": "https://a.com/2", "title": "Vieja", "content": "texto", "published_date": rfc(vieja)},
        {"url": "https://a.com/3", "title": "Sin fecha", "content": "texto"},
        {"url": "", "title": "Sin url"},
        {"url": "https://a.com/5", "title": ""},
        "basura",
    ]
    res = buscador(lambda r: respuesta(crudos)).buscar(hacer_tema(), AHORA)
    assert [r.titulo for r in res] == ["Reciente", "Sin fecha"]
    assert res[0].publicado == reciente.replace(microsecond=0) and res[1].publicado is None


def test_fechas_iso_y_basura():
    crudos = [
        {"url": "https://a.com/1", "title": "ISO", "published_date": (AHORA - timedelta(hours=1)).isoformat()},
        {"url": "https://a.com/2", "title": "Rota", "published_date": "ayer por la tarde"},
    ]
    res = buscador(lambda r: respuesta(crudos)).buscar(hacer_tema(), AHORA)
    assert [r.titulo for r in res] == ["ISO", "Rota"] and res[1].publicado is None


@pytest.mark.parametrize("status", [401, 429, 432, 500])
def test_errores_http_no_filtran_la_clave(status):
    with pytest.raises(BusquedaFallida) as e:
        buscador(lambda r: httpx.Response(status, text=f"error {CLAVE}")).buscar(hacer_tema(), AHORA)
    assert str(status) in str(e.value) and CLAVE not in str(e.value)


def test_error_de_red_no_filtra_la_clave():
    def handler(req):
        raise httpx.ConnectError(f"no conecta {req.headers['authorization']}")

    with pytest.raises(BusquedaFallida) as e:
        buscador(handler).buscar(hacer_tema(), AHORA)
    assert CLAVE not in str(e.value) and CLAVE not in repr(e.value.__cause__)


def test_respuesta_ilegible():
    with pytest.raises(BusquedaFallida):
        buscador(lambda r: httpx.Response(200, text="<html>no es json</html>")).buscar(hacer_tema(), AHORA)


def test_fecha_sin_zona_se_asume_utc():
    crudos = [{"url": "https://a.com/1", "title": "t", "published_date": "2026-09-21T10:00:00"}]
    (r,) = buscador(lambda req: respuesta(crudos)).buscar(hacer_tema(), AHORA)
    assert r.publicado == datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
