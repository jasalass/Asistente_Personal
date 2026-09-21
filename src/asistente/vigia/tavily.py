from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx

from asistente.db.models import Tema, TemaTipo

URL_BUSQUEDA = "https://api.tavily.com/search"

DOMINIOS_PAPERS = [
    "arxiv.org", "pubmed.ncbi.nlm.nih.gov", "biorxiv.org", "medrxiv.org", "semanticscholar.org",
    "nature.com", "science.org", "sciencedirect.com", "ieee.org", "acm.org",
]


class BusquedaFallida(Exception):
    """La búsqueda no se pudo completar (cuota, red o servicio). Nunca incluye la clave."""


@dataclass(frozen=True)
class Resultado:
    titulo: str
    url: str
    contenido: str  # extracto que entrega el buscador, no el artículo completo
    publicado: datetime | None


class Buscador(Protocol):
    def buscar(self, tema: Tema, ahora: datetime) -> list[Resultado]: ...


def _rango(ventana_horas: int) -> str:
    """El menor rango de Tavily que cubre la ventana pedida."""
    if ventana_horas <= 24:
        return "day"
    if ventana_horas <= 24 * 7:
        return "week"
    if ventana_horas <= 24 * 31:
        return "month"
    return "year"


def _fecha(valor: Any) -> datetime | None:
    if not valor or not isinstance(valor, str):
        return None
    try:
        dt = parsedate_to_datetime(valor)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(valor)
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class TavilyBuscador:
    def __init__(
        self, api_key: str, *, cliente: httpx.Client | None = None, timeout: float = 30.0
    ) -> None:
        self._api_key = api_key
        self._cliente = cliente or httpx.Client(timeout=timeout)

    def _payload(self, tema: Tema) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": tema.query_busqueda,
            # Pedir de más compensa lo que se descarte por viejo o ya visto; el costo es el mismo.
            "max_results": min(10, tema.cantidad_resultados * 2),
            "search_depth": "basic",
            "time_range": _rango(tema.ventana_frescura_horas),
            "topic": "news" if tema.tipo_contenido is TemaTipo.NOTICIAS else "general",
        }
        if tema.tipo_contenido is TemaTipo.PAPERS:
            payload["include_domains"] = DOMINIOS_PAPERS
        return payload

    def buscar(self, tema: Tema, ahora: datetime) -> list[Resultado]:
        try:
            r = self._cliente.post(
                URL_BUSQUEDA,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=self._payload(tema),
            )
        except httpx.HTTPError as e:
            raise BusquedaFallida(f"Tavily no respondió ({type(e).__name__})") from None
        if r.status_code != 200:
            raise BusquedaFallida(f"Tavily respondió {r.status_code}")
        try:
            crudos = r.json().get("results", [])
        except ValueError:
            raise BusquedaFallida("Tavily devolvió una respuesta ilegible") from None

        limite = ahora - timedelta(hours=tema.ventana_frescura_horas)
        resultados = []
        for x in crudos:
            if not isinstance(x, dict) or not x.get("url") or not x.get("title"):
                continue
            publicado = _fecha(x.get("published_date"))
            if publicado is not None and publicado < limite:
                continue  # sin fecha se conserva: la deduplicación evita repetirlo
            resultados.append(
                Resultado(
                    titulo=str(x["title"]).strip(),
                    url=str(x["url"]).strip(),
                    contenido=str(x.get("content") or "").strip(),
                    publicado=publicado,
                )
            )
        return resultados
