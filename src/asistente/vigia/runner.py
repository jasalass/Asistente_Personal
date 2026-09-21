import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from asistente.db.connection import Conn, transaccion
from asistente.db.models import Tema
from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.db.repos.sistema import EstadoSistemaRepo
from asistente.db.repos.temas import ArticuloRepo, TemaRepo
from asistente.llm.base import LLM, LLMNoDisponible
from asistente.llm.combinadores import ContadorLLM
from asistente.vigia.programacion import toca
from asistente.vigia.resumen import resumir, sanear
from asistente.vigia.tavily import Buscador, BusquedaFallida
from asistente.vigia.urls import dominio, normalizar_url

log = logging.getLogger(__name__)

SIN_RESUMEN = "(Resumen no disponible. Abre el enlace para leer el artículo.)"

AbrirConexion = Callable[[], AbstractContextManager[Conn]]


@dataclass(frozen=True)
class Publicacion:
    """Lo que se muestra en Discord. Neutral: no depende de discord.py."""

    titulo: str
    descripcion: str
    url: str | None = None
    pie: str = ""


Publicar = Callable[[Publicacion], Awaitable[None]]


@dataclass
class ResultadoTema:
    encontrados: int = 0
    nuevos: int = 0
    publicados: int = 0
    error: str | None = None


async def ejecutar_tema(
    tema: Tema,
    *,
    buscador: Buscador,
    llm: LLM,
    publicar: Publicar,
    abrir: AbrirConexion,
    ahora: datetime,
) -> ResultadoTema:
    """Un tema: busca, descarta lo visto, resume, publica y marca como visto tras publicar.

    Si algo falla a medias (Tavily, Groq o Discord), el tema NO se da por ejecutado y se
    reintenta en el siguiente ciclo; lo ya publicado no se repite porque quedó marcado.
    """
    inicio = time.monotonic()
    res = ResultadoTema()
    llm = ContadorLLM(llm)  # para dejar registrado cuántos tokens gastó cada corrida

    async def en_hilo(fn, *args):
        return await asyncio.to_thread(fn, *args)

    try:
        resultados = await en_hilo(buscador.buscar, tema, ahora)
    except BusquedaFallida as e:
        res.error = str(e)
        resultados = []
    res.encontrados = len(resultados)

    candidatos = {}
    for r in resultados:
        url_norm = normalizar_url(r.url)
        if url_norm is not None and url_norm not in candidatos:
            candidatos[url_norm] = r

    def vistos() -> set[str]:
        with abrir() as conn:
            return ArticuloRepo(conn).vistos(tema.id, candidatos)

    def marcar_visto(url_norm: str, titulo: str) -> None:
        with abrir() as conn:
            ArticuloRepo(conn).registrar(tema.id, url_norm, titulo)

    if res.error is None:
        ya = await en_hilo(vistos)
        nuevos = [(u, r) for u, r in candidatos.items() if u not in ya]
        nuevos = nuevos[: tema.cantidad_resultados]
        res.nuevos = len(nuevos)

        for url_norm, r in nuevos:
            try:
                resumen = await en_hilo(resumir, llm, r.titulo, r.contenido)
            except LLMNoDisponible:
                res.error = "resumidor no disponible"
                break
            fecha = f" · {r.publicado.astimezone():%d/%m}" if r.publicado else ""
            pub = Publicacion(
                titulo=sanear(r.titulo)[:256],
                descripcion=resumen or SIN_RESUMEN,
                url=r.url,
                pie=f"{tema.nombre} · {dominio(r.url)}{fecha}",
            )
            try:
                await publicar(pub)
            except Exception:  # un fallo de Discord no debe tumbar el resto del ciclo
                log.exception("No se pudo publicar un artículo del tema %s", tema.nombre)
                res.error = "publicación fallida"
                break
            await en_hilo(marcar_visto, url_norm, r.titulo)
            res.publicados += 1

        if res.error is None and res.nuevos == 0 and tema.avisar_sin_novedades:
            try:
                await publicar(
                    Publicacion(
                        titulo=f"{tema.nombre}: sin novedades",
                        descripcion="No encontré artículos nuevos desde la última revisión.",
                        pie=tema.nombre,
                    )
                )
            except Exception:
                log.exception("No se pudo publicar el aviso de sin novedades")
                res.error = "publicación fallida"

    def cerrar() -> None:
        with abrir() as conn:
            if res.error is None:
                TemaRepo(conn).marcar_ejecucion(tema.id, ahora)
            AuditoriaRepo(conn).registrar_ejecucion(
                "vigia",
                duracion_ms=int((time.monotonic() - inicio) * 1000),
                tokens_in=llm.tokens_in,
                tokens_out=llm.tokens_out,
                error=res.error,
                detalle={
                    "tema": tema.nombre,
                    "encontrados": res.encontrados,
                    "nuevos": res.nuevos,
                    "publicados": res.publicados,
                },
            )

    await en_hilo(cerrar)
    return res


async def ciclo(
    *,
    buscador: Buscador,
    llm: LLM,
    publicar: Publicar,
    tz: ZoneInfo,
    ahora: datetime,
    abrir: AbrirConexion = transaccion,
    max_busquedas_dia: int = 30,
    max_por_ciclo: int = 3,
    forzar: bool = False,
) -> list[tuple[Tema, ResultadoTema]]:
    """Corre los temas que tocan. `forzar` ignora el calendario, pero no el presupuesto diario."""
    desde_medianoche = ahora.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    def elegir() -> list[Tema]:
        with abrir() as conn:
            if EstadoSistemaRepo(conn).pausado():
                return []
            temas = TemaRepo(conn).listar(solo_activos=True)
            a_correr = temas if forzar else [t for t in temas if toca(t, ahora, tz)]
            usadas = conn.execute(
                "select count(*) as n from ejecuciones where tipo = 'vigia' and inicio >= %s",
                (desde_medianoche,),
            ).fetchone()["n"]
            return a_correr[: min(max_por_ciclo, max(0, max_busquedas_dia - usadas))]

    resultados = []
    for tema in await asyncio.to_thread(elegir):
        res = await ejecutar_tema(
            tema, buscador=buscador, llm=llm, publicar=publicar, abrir=abrir, ahora=ahora
        )
        resultados.append((tema, res))
    return resultados


async def vigia(
    publicar: Publicar,
    *,
    buscador: Buscador,
    llm: LLM,
    tz: ZoneInfo,
    intervalo_s: int,
    max_busquedas_dia: int,
) -> None:
    """Bucle infinito. Ningún error lo termina."""
    log.info("Vigía de temas activo (revisa cada %s s)", intervalo_s)
    while True:
        try:
            await ciclo(
                buscador=buscador,
                llm=llm,
                publicar=publicar,
                tz=tz,
                ahora=datetime.now(tz),
                max_busquedas_dia=max_busquedas_dia,
            )
        except Exception:
            log.exception("Error en el ciclo del vigía")
        await asyncio.sleep(intervalo_s)
