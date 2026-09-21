import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = re.compile(r"^(utm_|mc_|fbclid$|gclid$|yclid$|igshid$|ref$|ref_|_hs|_ga$|source$)", re.IGNORECASE)
_MAX_LARGO = 2000


def es_url_publica(url: str) -> bool:
    """Solo se muestran enlaces http(s) a sitios públicos, sin credenciales embebidas.

    El enlace de un artículo viene de un buscador externo: no se confía en él a ciegas.
    """
    if not url or len(url) > _MAX_LARGO or any(c.isspace() for c in url):
        return False
    try:
        partes = urlsplit(url)
        host = partes.hostname
    except ValueError:
        return False
    if partes.scheme not in ("http", "https") or not host or partes.username or partes.password:
        return False
    try:
        return ipaddress.ip_address(host).is_global  # IP literal: solo si es pública
    except ValueError:
        pass
    return "." in host and host != "localhost" and not host.endswith((".local", ".internal"))


def normalizar_url(url: str) -> str | None:
    """Forma canónica para deduplicar: misma nota con distinto tracking = misma URL."""
    if not es_url_publica(url):
        return None
    partes = urlsplit(url.strip())
    host = (partes.hostname or "").lower().removeprefix("www.")
    puerto = f":{partes.port}" if partes.port and partes.port not in (80, 443) else ""
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(partes.query) if not _TRACKING.match(k))
    )
    camino = partes.path.rstrip("/") or "/"
    return urlunsplit(("https", f"{host}{puerto}", camino, query, ""))


def dominio(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")
