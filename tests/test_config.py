import pytest
from pydantic import ValidationError

from asistente.config import Settings

BASE = {
    "groq_api_key": "clave-groq-secreta",
    "tavily_api_key": "t",
    "database_url": "postgresql://asistente_bot.ref:pw@host:5432/postgres",
    "discord_token": "d",
    "discord_owner_id": 1,
    "discord_guild_id": 2,
}


def test_canales_desde_string_csv():
    s = Settings(_env_file=None, **BASE, discord_channel_ids="10, 20,30")
    assert s.discord_channel_ids == frozenset({10, 20, 30})
    assert s.timezone == "America/Santiago"


def test_modelos_respaldo_por_defecto_son_dos():
    s = Settings(_env_file=None, **BASE, discord_channel_ids="10")
    assert s.modelos_respaldo == ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]


def test_modelos_respaldo_desde_string_csv_y_en_orden():
    s = Settings(
        _env_file=None, **BASE, discord_channel_ids="10", modelos_respaldo="uno, dos ,tres"
    )
    assert s.modelos_respaldo == ["uno", "dos", "tres"]


def test_modelos_respaldo_vacio_desactiva_el_respaldo():
    s = Settings(_env_file=None, **BASE, discord_channel_ids="10", modelos_respaldo="")
    assert s.modelos_respaldo == []


def test_secretos_no_se_filtran_en_repr():
    s = Settings(_env_file=None, **BASE, discord_channel_ids="10")
    assert "clave-groq-secreta" not in repr(s)
    assert "clave-groq-secreta" not in str(s)
    assert s.groq_api_key.get_secret_value() == "clave-groq-secreta"


def test_falta_un_secreto_falla_al_arrancar():
    datos = {k: v for k, v in BASE.items() if k != "groq_api_key"}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **datos, discord_channel_ids="10")


@pytest.mark.parametrize(
    "campo", ["groq_api_key", "tavily_api_key", "database_url", "discord_token"]
)
def test_secreto_vacio_falla_al_arrancar(campo):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**BASE, campo: "  "}, discord_channel_ids="10")


def test_los_errores_de_validacion_no_muestran_secretos():
    # Un error a nivel de modelo (canal fuera de la lista) es el que imprimía el input completo.
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None, **BASE, discord_channel_ids="10", discord_canal_avisos_id=99)
    assert "clave-groq-secreta" not in str(e.value)
    assert "clave-groq" not in repr(e.value)


def test_canal_de_avisos_debe_estar_entre_los_permitidos():
    ok = Settings(_env_file=None, **BASE, discord_channel_ids="10,20", discord_canal_avisos_id=20)
    assert ok.discord_canal_avisos_id == 20
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **BASE, discord_channel_ids="10,20", discord_canal_avisos_id=99)


def test_variable_vacia_cuenta_como_no_definida(monkeypatch):
    monkeypatch.setenv("DISCORD_CANAL_AVISOS_ID", "")
    s = Settings(_env_file=None, **BASE, discord_channel_ids="10")
    assert s.discord_canal_avisos_id is None


def test_horario_de_avisos_invertido_falla():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, **BASE, discord_channel_ids="10", aviso_hora_inicio=21, aviso_hora_fin=8
        )


def test_timezone_invalida():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **BASE, discord_channel_ids="10", timezone="Marte/Olympus")
