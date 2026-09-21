import asyncio
import json
from contextlib import contextmanager
from datetime import UTC, datetime, time, timedelta
from json import dumps
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from pydantic import ValidationError

from asistente.agent.tools import construir_registro
from asistente.db.models import Frecuencia, TemaActualizacion, TemaNuevo
from asistente.db.repos.sistema import EstadoSistemaRepo
from asistente.db.repos.temas import ArticuloRepo, TemaRepo
from asistente.llm.base import LLMNoDisponible, LLMRespuesta
from asistente.security.tool_registry import ToolArgsInvalid, ToolError
from asistente.vigia.runner import SIN_RESUMEN, ciclo
from asistente.vigia.tavily import BusquedaFallida, Resultado

TZ = ZoneInfo("America/Santiago")
AHORA = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)  # 09:00 en Chile
RESUMEN_OK = "Las autoridades chilenas alistan una estrategia para usar sistemas de IA en centros de salud estatales."


# ---------- repositorios ----------


def nuevo(**kw) -> TemaNuevo:
    base = {"nombre": "IA en salud", "query_busqueda": "inteligencia artificial salud"}
    return TemaNuevo(**{**base, **kw})


def test_crear_y_obtener_tema(conn_vigia):
    repo = TemaRepo(conn_vigia)
    t = repo.crear(nuevo(hora_preferida=time(7, 30), cantidad_resultados=3))
    assert t.activo and t.ultima_ejecucion is None and t.hora_preferida == time(7, 30)
    assert repo.obtener(t.id) == t
    assert [x.id for x in repo.listar(solo_activos=True)] == [t.id]


def test_listar_activos_y_buscar_por_nombre(conn_vigia):
    repo = TemaRepo(conn_vigia)
    a = repo.crear(nuevo())
    b = repo.crear(nuevo(activo=False))
    assert {t.id for t in repo.listar()} >= {a.id, b.id}
    assert [t.id for t in repo.listar(solo_activos=True)] == [a.id]
    assert a.id in [t.id for t in repo.buscar_por_nombre("SALUD")]
    assert repo.buscar_por_nombre("%") == []


def test_actualizar_valida_coherencia_y_no_toca_lo_demas(conn_vigia):
    repo = TemaRepo(conn_vigia)
    t = repo.crear(nuevo(cantidad_resultados=4))
    with pytest.raises(ValidationError):  # cada_x_dias exige intervalo
        repo.actualizar(t.id, TemaActualizacion(frecuencia=Frecuencia.CADA_X_DIAS))
    q = repo.actualizar(t.id, TemaActualizacion(frecuencia=Frecuencia.CADA_X_DIAS, intervalo_dias=3))
    assert q.frecuencia is Frecuencia.CADA_X_DIAS and q.intervalo_dias == 3 and q.cantidad_resultados == 4
    assert repo.actualizar(t.id, TemaActualizacion(activo=False)).activo is False


def test_la_base_tambien_rechaza_temas_incoherentes(conn_vigia):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn_vigia.execute(
            "insert into temas_seguimiento (nombre, query_busqueda, frecuencia) "
            "values ('x', 'consulta', 'cada_x_dias')"
        )


def test_articulos_solo_se_registran_una_vez(conn_vigia):
    t = TemaRepo(conn_vigia).crear(nuevo())
    art = ArticuloRepo(conn_vigia)
    assert art.registrar(t.id, "https://a.com/1", "Uno") is True
    assert art.registrar(t.id, "https://a.com/1", "Uno de nuevo") is False
    assert art.vistos(t.id, ["https://a.com/1", "https://a.com/2"]) == {"https://a.com/1"}


def test_el_rol_no_puede_borrar_ni_modificar_lo_visto(conn_vigia):
    t = TemaRepo(conn_vigia).crear(nuevo())
    ArticuloRepo(conn_vigia).registrar(t.id, "https://a.com/1", "Uno")
    for sentencia in ("delete from articulos_vistos", "update articulos_vistos set titulo = 'x'",
                      "delete from temas_seguimiento"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn_vigia.transaction():
            conn_vigia.execute(sentencia)


# ---------- tools del agente ----------


def test_tools_de_temas(conn_vigia):
    reg = construir_registro(conn_vigia, TZ)
    reg.invoke("habilitar_vigia", {})
    t = reg.invoke("crear_tema", {"nombre": "Papers IA", "query_busqueda": "large language models",
                                  "tipo_contenido": "papers", "hora_preferida": "07:30",
                                  "frecuencia": None, "avisar_sin_novedades": None})
    assert t["tipo_contenido"] == "papers" and t["hora_preferida"] == "07:30:00"
    assert t["id"] in [x["id"] for x in reg.invoke("listar_temas", {"solo_activos": True})]

    q = reg.invoke("actualizar_tema", {"id": t["id"], "activo": False})
    assert q["activo"] is False
    with pytest.raises(ToolError, match="intervalo_dias"):
        reg.invoke("actualizar_tema", {"id": t["id"], "frecuencia": "cada_x_dias"})
    with pytest.raises(ToolError):
        reg.invoke("actualizar_tema", {"id": "00000000-0000-0000-0000-000000000000", "activo": True})


def test_temas_por_nombre_y_sin_duplicados(conn_vigia):
    reg = construir_registro(conn_vigia, TZ)
    reg.invoke("habilitar_vigia", {})
    # Nombre único: los temas reales de la base siguen existiendo (solo se desactivan en el test).
    marca = uuid4().hex[:8]
    nombre = f"Tema de prueba {marca}"
    t = reg.invoke("crear_tema", {"nombre": nombre, "query_busqueda": "inteligencia artificial salud"})
    with pytest.raises(ToolError, match="Ya existe el tema .* \\(activo"):
        reg.invoke("crear_tema", {"nombre": nombre.upper(), "query_busqueda": "otra consulta"})

    pausado = reg.invoke("actualizar_tema", {"tema": marca, "activo": False})
    assert pausado["id"] == t["id"] and pausado["activo"] is False
    with pytest.raises(ToolError, match="pausado"):  # sugiere actualizar en vez de duplicar
        reg.invoke("crear_tema", {"nombre": nombre, "query_busqueda": "otra consulta"})
    with pytest.raises(ToolError, match="No encontré"):
        reg.invoke("actualizar_tema", {"tema": "no existe zzz", "activo": True})
    with pytest.raises(ToolArgsInvalid):
        reg.invoke("actualizar_tema", {"activo": True})


def test_las_tools_de_temas_rechazan_argumentos_invalidos(conn_vigia):
    reg = construir_registro(conn_vigia, TZ)
    reg.invoke("habilitar_vigia", {})
    for args in ({"nombre": "x", "query_busqueda": "ab"},  # consulta muy corta
                 {"nombre": "x", "query_busqueda": "consulta", "cantidad_resultados": 99},
                 {"nombre": "x", "query_busqueda": "consulta", "dias_semana": [9]},
                 {"nombre": "x", "query_busqueda": "consulta", "ultima_ejecucion": "2026-01-01"}):
        with pytest.raises(ToolArgsInvalid):
            reg.invoke("crear_tema", args)


# ---------- ejecución del vigía ----------


class FakeBuscador:
    def __init__(self, resultados=(), falla=None):
        self.resultados, self.falla, self.llamadas = list(resultados), falla, 0

    def buscar(self, tema, ahora):
        self.llamadas += 1
        if self.falla:
            raise BusquedaFallida(self.falla)
        return list(self.resultados)


class LLMResumidor:
    def __init__(self, resumen=RESUMEN_OK, cae=False):
        self.resumen, self.cae, self.llamadas = resumen, cae, 0

    def chat(self, messages, tools=None, *, json=False):
        self.llamadas += 1
        if self.cae:
            raise LLMNoDisponible("sin cuota")
        # `json` es aquí el parámetro del protocolo (tapa al módulo), por eso se usa `dumps`.
        return LLMRespuesta(
            contenido=dumps({"resumen": self.resumen}), tokens_in=100, tokens_out=50
        )


class Publicaciones:
    def __init__(self, falla_en=None):
        self.items, self.falla_en = [], falla_en

    async def __call__(self, pub):
        if self.falla_en is not None and len(self.items) + 1 == self.falla_en:
            raise ConnectionError("Discord caído")
        self.items.append(pub)


def res(n, url=None, titulo=None):
    return Resultado(titulo=titulo or f"Artículo {n}", url=url or f"https://sitio{n}.com/nota",
                     contenido=f"contenido de prueba número {n} con palabras variadas", publicado=None)


def correr(conn, *, buscador, llm=None, publicar=None, forzar=True, **kw):
    @contextmanager
    def abrir():
        yield conn

    publicar = publicar if publicar is not None else Publicaciones()
    hechos = asyncio.run(
        ciclo(buscador=buscador, llm=llm or LLMResumidor(), publicar=publicar, tz=TZ,
              ahora=kw.pop("ahora", AHORA), abrir=abrir, forzar=forzar, **kw)
    )
    return hechos, publicar


def test_flujo_completo_publica_y_deduplica(conn_vigia):
    t = TemaRepo(conn_vigia).crear(nuevo(nombre="IA en salud"))
    buscador = FakeBuscador([
        res(1),
        res(2, url="https://www.sitio1.com/nota/?utm_source=x"),  # mismo artículo que el 1
        res(3, url="http://localhost/admin"),  # enlace inseguro
        res(4),
    ])
    (hecho,), pubs = correr(conn_vigia, buscador=buscador)
    r = hecho[1]
    assert (r.encontrados, r.nuevos, r.publicados, r.error) == (4, 2, 2, None)
    assert [p.titulo for p in pubs.items] == ["Artículo 1", "Artículo 4"]
    p = pubs.items[0]
    assert p.url == "https://sitio1.com/nota" and p.descripcion == RESUMEN_OK
    assert p.pie.startswith("IA en salud · sitio1.com")
    assert TemaRepo(conn_vigia).obtener(t.id).ultima_ejecucion == AHORA

    # segunda pasada: nada nuevo, nada repetido
    _, pubs2 = correr(conn_vigia, buscador=buscador)
    assert pubs2.items == []


def test_solo_publica_hasta_cantidad_resultados_y_deja_el_resto_para_la_proxima(conn_vigia):
    TemaRepo(conn_vigia).crear(nuevo(cantidad_resultados=1))
    buscador = FakeBuscador([res(1), res(2)])
    _, p1 = correr(conn_vigia, buscador=buscador)
    _, p2 = correr(conn_vigia, buscador=buscador)
    assert [p.titulo for p in p1.items] == ["Artículo 1"]
    assert [p.titulo for p in p2.items] == ["Artículo 2"]


def test_si_no_hay_resumen_valido_publica_igual_con_aviso(conn_vigia):
    TemaRepo(conn_vigia).crear(nuevo())
    llm = LLMResumidor(resumen="corto")  # fuera de esquema, dos veces
    _, pubs = correr(conn_vigia, buscador=FakeBuscador([res(1)]), llm=llm)
    assert pubs.items[0].descripcion == SIN_RESUMEN and pubs.items[0].url == "https://sitio1.com/nota"


def test_el_titulo_publicado_no_puede_mencionar_ni_llevar_markdown(conn_vigia):
    TemaRepo(conn_vigia).crear(nuevo())
    malo = res(1, titulo="@everyone [click](http://evil.com) **urgente**")
    _, pubs = correr(conn_vigia, buscador=FakeBuscador([malo]))
    t = pubs.items[0].titulo
    assert "@" not in t and "evil" not in t and "*" not in t


def test_si_discord_falla_no_se_marca_visto_y_se_reintenta(conn_vigia):
    t = TemaRepo(conn_vigia).crear(nuevo())
    buscador = FakeBuscador([res(1), res(2)])
    (hecho,), _ = correr(conn_vigia, buscador=buscador, publicar=Publicaciones(falla_en=2))
    assert hecho[1].error == "publicación fallida" and hecho[1].publicados == 1
    assert TemaRepo(conn_vigia).obtener(t.id).ultima_ejecucion is None  # se reintentará

    _, pubs2 = correr(conn_vigia, buscador=buscador)
    assert [p.titulo for p in pubs2.items] == ["Artículo 2"]  # el 1 no se repite
    assert TemaRepo(conn_vigia).obtener(t.id).ultima_ejecucion == AHORA


def test_si_tavily_falla_no_publica_ni_da_por_ejecutado_el_tema(conn_vigia):
    t = TemaRepo(conn_vigia).crear(nuevo())
    (hecho,), pubs = correr(conn_vigia, buscador=FakeBuscador(falla="Tavily respondió 429"))
    assert hecho[1].error == "Tavily respondió 429" and pubs.items == []
    assert TemaRepo(conn_vigia).obtener(t.id).ultima_ejecucion is None
    ejec = conn_vigia.execute(
        "select error, detalle from ejecuciones where tipo = 'vigia' order by id desc limit 1"
    ).fetchone()
    assert ejec["error"] == "Tavily respondió 429" and ejec["detalle"]["tema"] == "IA en salud"


def test_si_el_resumidor_cae_no_publica_ni_marca_visto(conn_vigia):
    t = TemaRepo(conn_vigia).crear(nuevo())
    buscador = FakeBuscador([res(1)])
    (hecho,), pubs = correr(conn_vigia, buscador=buscador, llm=LLMResumidor(cae=True))
    assert hecho[1].error == "resumidor no disponible" and pubs.items == []
    assert ArticuloRepo(conn_vigia).vistos(t.id, ["https://sitio1.com/nota"]) == set()
    _, pubs2 = correr(conn_vigia, buscador=buscador)  # al volver el servicio, se publica
    assert len(pubs2.items) == 1


def test_sin_novedades_solo_avisa_si_el_tema_lo_pide(conn_vigia):
    repo = TemaRepo(conn_vigia)
    repo.crear(nuevo(nombre="Callado"))
    _, callado = correr(conn_vigia, buscador=FakeBuscador([]))
    assert callado.items == []

    conn_vigia.execute("update temas_seguimiento set activo = false")
    repo.crear(nuevo(nombre="Hablador", avisar_sin_novedades=True))
    _, hablador = correr(conn_vigia, buscador=FakeBuscador([]))
    assert [p.titulo for p in hablador.items] == ["Hablador: sin novedades"] and hablador.items[0].url is None


def test_respeta_el_calendario_salvo_que_se_fuerce(conn_vigia):
    t = TemaRepo(conn_vigia).crear(nuevo())
    TemaRepo(conn_vigia).marcar_ejecucion(t.id, AHORA - timedelta(hours=1))  # ya corrió hoy
    buscador = FakeBuscador([res(1)])
    hechos, _ = correr(conn_vigia, buscador=buscador, forzar=False)
    assert hechos == [] and buscador.llamadas == 0
    hechos, _ = correr(conn_vigia, buscador=buscador, forzar=True)
    assert len(hechos) == 1 and buscador.llamadas == 1


def test_en_pausa_no_busca_nada(conn_vigia):
    TemaRepo(conn_vigia).crear(nuevo())
    EstadoSistemaRepo(conn_vigia).set_pausado(True)
    buscador = FakeBuscador([res(1)])
    hechos, pubs = correr(conn_vigia, buscador=buscador)
    assert hechos == [] and buscador.llamadas == 0 and pubs.items == []


def test_el_presupuesto_diario_limita_las_busquedas(conn_vigia):
    repo = TemaRepo(conn_vigia)
    for i in range(3):
        repo.crear(nuevo(nombre=f"Tema {i}"))
    usadas = conn_vigia.execute(
        "select count(*) as n from ejecuciones where tipo = 'vigia' and inicio >= %s",
        (AHORA.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0),),
    ).fetchone()["n"]
    hechos, _ = correr(conn_vigia, buscador=FakeBuscador([]), max_busquedas_dia=usadas + 2, max_por_ciclo=10)
    assert len(hechos) == 2
    hechos, _ = correr(conn_vigia, buscador=FakeBuscador([]), max_busquedas_dia=usadas + 2, max_por_ciclo=10)
    assert hechos == []  # presupuesto agotado


def test_tope_de_temas_por_ciclo(conn_vigia):
    for i in range(4):
        TemaRepo(conn_vigia).crear(nuevo(nombre=f"Tema {i}"))
    hechos, _ = correr(conn_vigia, buscador=FakeBuscador([]), max_busquedas_dia=1000, max_por_ciclo=3)
    assert len(hechos) == 3


def test_la_ejecucion_registra_los_tokens_gastados(conn_vigia):
    TemaRepo(conn_vigia).crear(nuevo())
    llm = LLMResumidor()
    correr(conn_vigia, buscador=FakeBuscador([res(1), res(2), res(3)]), llm=llm)
    ejec = conn_vigia.execute(
        "select tokens_in, tokens_out from ejecuciones where tipo = 'vigia' order by id desc limit 1"
    ).fetchone()
    assert llm.llamadas == 3
    assert (ejec["tokens_in"], ejec["tokens_out"]) == (300, 150)


def test_el_registro_de_ejecucion_deja_el_resumen(conn_vigia):
    TemaRepo(conn_vigia).crear(nuevo())
    correr(conn_vigia, buscador=FakeBuscador([res(1), res(2)]))
    ejec = conn_vigia.execute(
        "select detalle, error from ejecuciones where tipo = 'vigia' order by id desc limit 1"
    ).fetchone()
    assert ejec["error"] is None
    assert ejec["detalle"] == {"tema": "IA en salud", "encontrados": 2, "nuevos": 2, "publicados": 2}
    assert json.dumps(ejec["detalle"])
