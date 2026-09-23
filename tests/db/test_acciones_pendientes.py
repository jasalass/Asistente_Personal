from datetime import UTC, datetime, timedelta

from asistente.db.repos.acciones_pendientes import AccionesPendientesRepo


def test_crear_y_obtener(conn_aprobaciones):
    repo = AccionesPendientesRepo(conn_aprobaciones)
    fila = repo.crear("enviar_email", {"a": "b@c.cl"}, "hash123")
    assert fila.tool == "enviar_email" and fila.args == {"a": "b@c.cl"}
    assert fila.estado == "pendiente" and fila.resuelta_en is None
    assert repo.obtener(fila.id) == fila


def test_obtener_uno_inexistente_es_none(conn_aprobaciones):
    import uuid
    assert AccionesPendientesRepo(conn_aprobaciones).obtener(uuid.uuid4()) is None


def test_marcar_ejecutada_guarda_el_resultado(conn_aprobaciones):
    repo = AccionesPendientesRepo(conn_aprobaciones)
    fila = repo.crear("enviar_email", {}, "h")
    resuelta = repo.marcar_ejecutada(fila.id, "12345", {"ok": True})
    assert resuelta.estado == "ejecutada" and resuelta.resultado == {"ok": True}
    assert resuelta.resuelto_por == "12345" and resuelta.resuelta_en is not None


def test_marcar_aprobada_con_error_no_queda_como_ejecutada(conn_aprobaciones):
    repo = AccionesPendientesRepo(conn_aprobaciones)
    fila = repo.crear("enviar_email", {}, "h")
    resuelta = repo.marcar_aprobada_con_error(fila.id, "12345", "SMTP caído")
    assert resuelta.estado == "aprobada" and resuelta.resultado == {"error": "SMTP caído"}


def test_marcar_rechazada(conn_aprobaciones):
    repo = AccionesPendientesRepo(conn_aprobaciones)
    fila = repo.crear("enviar_email", {}, "h")
    resuelta = repo.marcar_rechazada(fila.id, "12345")
    assert resuelta.estado == "rechazada" and resuelta.resultado is None


def test_una_fila_ya_resuelta_no_se_puede_resolver_de_nuevo(conn_aprobaciones):
    # Evita que un doble clic (o dos aprobaciones en carrera) ejecuten la tool dos veces.
    repo = AccionesPendientesRepo(conn_aprobaciones)
    fila = repo.crear("enviar_email", {}, "h")
    assert repo.marcar_rechazada(fila.id, "12345") is not None
    assert repo.marcar_ejecutada(fila.id, "99999", {"ok": True}) is None
    assert repo.obtener(fila.id).estado == "rechazada"  # no lo pisó el segundo intento


def test_vencidas_solo_trae_las_pendientes_pasado_su_expira_en(conn_aprobaciones):
    repo = AccionesPendientesRepo(conn_aprobaciones)
    ahora = datetime.now(UTC)
    vieja = repo.crear("a", {}, "h1")
    conn_aprobaciones.execute(
        "update acciones_pendientes set expira_en = %s where id = %s",
        (ahora - timedelta(hours=1), vieja.id),
    )
    nueva = repo.crear("b", {}, "h2")  # expira_en por defecto: en 24h, no vencida
    resuelta = repo.crear("c", {}, "h3")
    conn_aprobaciones.execute(
        "update acciones_pendientes set expira_en = %s, estado = 'rechazada' where id = %s",
        (ahora - timedelta(hours=1), resuelta.id),
    )

    vencidas = {f.id for f in repo.vencidas(ahora)}
    assert vencidas == {vieja.id}
    assert nueva.id not in vencidas and resuelta.id not in vencidas


def test_marcar_expirada(conn_aprobaciones):
    repo = AccionesPendientesRepo(conn_aprobaciones)
    fila = repo.crear("a", {}, "h")
    repo.marcar_expirada(fila.id)
    assert repo.obtener(fila.id).estado == "expirada"


def test_pendientes_ids_solo_trae_las_que_siguen_pendientes(conn_aprobaciones):
    repo = AccionesPendientesRepo(conn_aprobaciones)
    a = repo.crear("a", {}, "h1")
    b = repo.crear("b", {}, "h2")
    repo.marcar_rechazada(b.id, "1")
    assert repo.pendientes_ids() == [a.id]
