from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración desde variables de entorno. Falla al arrancar si falta un secreto."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: SecretStr
    tavily_api_key: SecretStr
    database_url: SecretStr  # conexión directa a Postgres con el rol de mínimos privilegios
    discord_token: SecretStr

    discord_owner_id: int
    discord_guild_id: int
    discord_channel_ids: Annotated[frozenset[int], NoDecode]

    timezone: str = "America/Santiago"

    @field_validator("discord_channel_ids", mode="before")
    @classmethod
    def _parse_channel_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return frozenset(int(x) for x in v.split(",") if x.strip())
        return v

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"Zona horaria inválida: {v}") from e
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
