from uuid import uuid4

from asistente.db.repos.trazas import TrazaRepo, _apto_para_jsonb


def test_registra_y_lee_los_pasos_en_orden(conn_trazas):
    repo = TrazaRepo(conn_trazas)
    tid = uuid4()
    repo.registrar_paso(tid, 1, "llm", "openai/gpt-oss-120b", duracion_ms=120, tokens_in=50, tokens_out=10,
                        entrada={"mensajes": 2}, salida={"tool_calls": ["crear_evento"]})
    repo.registrar_paso(tid, 2, "tool", "crear_evento", duracion_ms=15, entrada={"nombre": "X"},
                        salida={"resumen": "creado"})

    pasos = repo.para(tid)
    assert [p["orden"] for p in pasos] == [1, 2]
    assert pasos[0]["tipo"] == "llm" and pasos[0]["tokens_in"] == 50
    assert pasos[1]["tipo"] == "tool" and pasos[1]["salida"] == {"resumen": "creado"}


def test_solo_se_ven_los_pasos_de_esa_traza(conn_trazas):
    repo = TrazaRepo(conn_trazas)
    a, b = uuid4(), uuid4()
    repo.registrar_paso(a, 1, "llm", "modelo")
    repo.registrar_paso(b, 1, "llm", "modelo")
    assert len(repo.para(a)) == 1 and len(repo.para(b)) == 1


def test_un_error_se_guarda_sin_salida(conn_trazas):
    repo = TrazaRepo(conn_trazas)
    tid = uuid4()
    repo.registrar_paso(tid, 1, "tool", "crear_evento", entrada={"nombre": "X"}, error="ya existe")
    (paso,) = repo.para(tid)
    assert paso["error"] == "ya existe" and paso["salida"] is None


def test_disponible_es_true_una_vez_aplicada_la_migracion(conn_trazas):
    assert TrazaRepo(conn_trazas).disponible() is True


# ---------- recorte de datos grandes o anidados, antes de llegar a la base ----------


def test_un_texto_largo_se_recorta():
    resultado = _apto_para_jsonb("x" * 2000)
    assert len(resultado) < 2000 and resultado.endswith("…(truncado)")


def test_un_diccionario_con_muchos_campos_se_recorta():
    resultado = _apto_para_jsonb({f"campo{i}": i for i in range(30)})
    assert len(resultado) == 21 and "_truncado" in resultado  # 20 campos + la marca


def test_una_lista_larga_se_recorta():
    resultado = _apto_para_jsonb(list(range(30)))
    assert len(resultado) == 21 and resultado[-1] == "…+10 más"


def test_valores_simples_no_se_tocan():
    assert _apto_para_jsonb(None) is None
    assert _apto_para_jsonb(42) == 42
    assert _apto_para_jsonb(True) is True


def test_un_objeto_cualquiera_se_convierte_a_texto():
    from datetime import date
    assert _apto_para_jsonb(date(2026, 9, 22)) == "2026-09-22"
