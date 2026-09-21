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


def test_timezone_invalida():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **BASE, discord_channel_ids="10", timezone="Marte/Olympus")
