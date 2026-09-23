from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from asistente.uso import texto_uso

TZ = ZoneInfo("America/Santiago")
# Un día del futuro: los registros reales no interfieren con los números exactos del test.
AHORA = datetime(2030, 6, 15, 18, 0, tzinfo=UTC)


def insertar(conn, tipo, tokens_in, tokens_out, cuando):
    conn.execute(
        "insert into ejecuciones (tipo, inicio, tokens_in, tokens_out) values (%s, %s, %s, %s)",
        (tipo, cuando, tokens_in, tokens_out),
    )


def test_suma_los_tokens_de_hoy_por_tipo_y_ignora_otros_dias(conn):
    hoy = AHORA - timedelta(hours=3)
    insertar(conn, "mensaje", 1000, 500, hoy)
    insertar(conn, "mensaje", 2000, 100, hoy)
    insertar(conn, "vigia", 100, 50, hoy)
    insertar(conn, "mensaje", 99999, 99999, AHORA - timedelta(days=2))  # otro día: no cuenta
    insertar(conn, "heartbeat", 5000, 5000, hoy)  # no usa el modelo de chat: no se muestra

    texto = texto_uso(conn, AHORA, TZ, 200_000)
    assert "Chat: 3.600 tokens en 2 mensajes (2 % de 200.000)" in texto
    assert "Vigía: 150 tokens en 1 corrida (0 % de 200.000)" in texto
    assert "heartbeat" not in texto.lower()


def test_un_dia_sin_registros_muestra_ceros(conn):
    texto = texto_uso(conn, AHORA + timedelta(days=400), TZ, 200_000)
    assert "Chat: 0 tokens en 0 mensajes (0 % de 200.000)" in texto
    assert "Vigía: 0 tokens en 0 corridas" in texto


def test_aclara_lo_que_no_incluye(conn):
    assert "pruebas manuales" in texto_uso(conn, AHORA, TZ, 200_000)


# ---------- desglose por modelo (necesita traza_pasos, migración 0005) ----------


def test_desglosa_por_modelo_porque_cada_uno_tiene_su_propio_cupo(conn_trazas):
    from uuid import uuid4

    from asistente.db.repos.trazas import TrazaRepo

    hoy = AHORA - timedelta(hours=3)
    insertar(conn_trazas, "mensaje", 150_000, 0, hoy)
    trazas = TrazaRepo(conn_trazas)
    tid = uuid4()
    trazas.registrar_paso(tid, 1, "llm", "openai/gpt-oss-120b", tokens_in=100_000, tokens_out=0, ts=hoy)
    trazas.registrar_paso(tid, 2, "llm", "openai/gpt-oss-20b", tokens_in=50_000, tokens_out=0, ts=hoy)

    texto = texto_uso(conn_trazas, AHORA, TZ, 200_000)
    # Nunca "125 %": son dos cupos de 200K separados, no uno solo de 200K compartido.
    assert "openai/gpt-oss-120b: 100.000 tokens (50 % de 200.000)" in texto
    assert "openai/gpt-oss-20b: 50.000 tokens (25 % de 200.000)" in texto
    assert "125 %" not in texto


def test_sin_pasos_de_llm_no_se_muestra_el_desglose(conn_trazas):
    assert "Por modelo" not in texto_uso(conn_trazas, AHORA, TZ, 200_000)
