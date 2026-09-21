from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from asistente.db.models import (
    EventoTipo,
    Prioridad,
    ProcesoActualizacion,
    ProcesoEstado,
    ProcesoNuevo,
)
from asistente.db.repos.procesos import ProcesoRepo


@pytest.fixture
def repo(conn):
    return ProcesoRepo(conn)


def test_crear_y_obtener(repo):
    p = repo.crear(
        ProcesoNuevo(nombre="Renovar pasaporte", etiquetas=["tramite"], prioridad=Prioridad.ALTA)
    )
    assert p.estado is ProcesoEstado.ACTIVO
    assert p.etiquetas == ["tramite"]
    assert repo.obtener(p.id) == p
    assert repo.obtener(uuid4()) is None


def test_crear_registra_evento_inicial(repo):
    p = repo.crear(ProcesoNuevo(nombre="X"))
    eventos = repo.eventos(p.id)
    assert [e.tipo for e in eventos] == [EventoTipo.NOTA]


def test_eventos_de_una_misma_transaccion_salen_del_mas_nuevo_al_mas_viejo(repo):
    p = repo.crear(ProcesoNuevo(nombre="X"))
    for n in ("uno", "dos", "tres"):
        repo.agregar_evento(p.id, EventoTipo.NOTA, n)
    assert [e.contenido for e in repo.eventos(p.id)] == ["tres", "dos", "uno", "Proceso creado"]


def test_actualizar_solo_toca_lo_informado(repo):
    p = repo.crear(ProcesoNuevo(nombre="X", proxima_accion="llamar", esperando_a="Juan"))
    q = repo.actualizar(p.id, ProcesoActualizacion(proxima_accion="escribir"))
    assert q.proxima_accion == "escribir"
    assert q.esperando_a == "Juan"
    assert q.actualizado_en >= p.actualizado_en


def test_actualizar_none_explicito_borra_el_valor(repo):
    p = repo.crear(ProcesoNuevo(nombre="X", esperando_a="Juan"))
    q = repo.actualizar(p.id, ProcesoActualizacion(esperando_a=None))
    assert q.esperando_a is None


def test_cambio_de_estado_queda_en_el_historial(repo):
    p = repo.crear(ProcesoNuevo(nombre="X"))
    repo.actualizar(p.id, ProcesoActualizacion(estado=ProcesoEstado.BLOQUEADO))
    eventos = repo.eventos(p.id)
    cambio = [e for e in eventos if e.tipo is EventoTipo.CAMBIO_ESTADO]
    assert len(cambio) == 1
    assert cambio[0].contenido == "activo -> bloqueado"


def test_mismo_estado_no_genera_evento(repo):
    p = repo.crear(ProcesoNuevo(nombre="X"))
    repo.actualizar(p.id, ProcesoActualizacion(estado=ProcesoEstado.ACTIVO))
    assert all(e.tipo is not EventoTipo.CAMBIO_ESTADO for e in repo.eventos(p.id))


def test_actualizar_inexistente(repo):
    assert repo.actualizar(uuid4(), ProcesoActualizacion(nombre="Y")) is None


def test_actualizacion_rechaza_nulos_obligatorios_y_campos_desconocidos():
    with pytest.raises(ValidationError):
        ProcesoActualizacion(nombre=None)
    with pytest.raises(ValidationError):
        ProcesoActualizacion(id=str(uuid4()))
    with pytest.raises(ValidationError):
        ProcesoActualizacion(estado="casi_listo")


def test_listar_filtra_por_estado_y_ordena_por_prioridad(repo):
    baja = repo.crear(ProcesoNuevo(nombre="baja", prioridad=Prioridad.BAJA))
    alta = repo.crear(ProcesoNuevo(nombre="alta", prioridad=Prioridad.ALTA))
    hecho = repo.crear(ProcesoNuevo(nombre="hecho", estado=ProcesoEstado.COMPLETADO))

    activos = [p.id for p in repo.listar([ProcesoEstado.ACTIVO])]
    assert alta.id in activos and baja.id in activos and hecho.id not in activos
    assert activos.index(alta.id) < activos.index(baja.id)
    assert hecho.id in [p.id for p in repo.listar()]


def test_buscar_por_nombre_escapa_comodines(repo):
    a = repo.crear(ProcesoNuevo(nombre="Renovar pasaporte"))
    repo.crear(ProcesoNuevo(nombre="Otra cosa"))
    assert [p.id for p in repo.buscar_por_nombre("PASAPORTE")] == [a.id]
    assert repo.buscar_por_nombre("%") == []


def test_pendientes_de_chequeo(repo):
    ahora = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    semanal = repo.crear(ProcesoNuevo(nombre="semanal", frecuencia_chequeo_dias=7))
    sin_freq = repo.crear(ProcesoNuevo(nombre="sin frecuencia"))
    cerrado = repo.crear(
        ProcesoNuevo(
            nombre="cerrado", frecuencia_chequeo_dias=1, estado=ProcesoEstado.COMPLETADO
        )
    )

    ids = {p.id for p in repo.pendientes_de_chequeo(ahora)}
    assert semanal.id in ids  # nunca chequeado
    assert sin_freq.id not in ids and cerrado.id not in ids

    repo.marcar_chequeado(semanal.id, ahora)
    assert semanal.id not in {p.id for p in repo.pendientes_de_chequeo(ahora)}
    assert semanal.id not in {
        p.id for p in repo.pendientes_de_chequeo(ahora + timedelta(days=6))
    }
    assert semanal.id in {p.id for p in repo.pendientes_de_chequeo(ahora + timedelta(days=7))}
