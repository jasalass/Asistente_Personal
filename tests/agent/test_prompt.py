from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from asistente.agent.prompt import construir_prompt

TZ = ZoneInfo("America/Santiago")


def test_incluye_la_fecha_de_cada_dia_de_la_semana():
    # Caso real: "el jueves tengo dentista" -> los tres modelos de Groq calcularon mal la fecha
    # dejándolo a su cuenta. 2026-09-23 12:00 UTC es miércoles 23/09 en Chile (UTC-3).
    prompt = construir_prompt(datetime(2026, 9, 23, 12, 0, tzinfo=UTC), TZ)
    assert "miércoles 2026-09-23" in prompt  # hoy mismo, desfase 0
    assert "jueves 2026-09-24" in prompt  # mañana
    assert "martes 2026-09-29" in prompt  # el próximo, no el de esta semana (ya pasó)


def test_si_hoy_es_ese_dia_la_fecha_es_hoy_no_dentro_de_una_semana():
    prompt = construir_prompt(datetime(2026, 9, 21, 12, 0, tzinfo=UTC), TZ)  # lunes
    assert "lunes 2026-09-21" in prompt
