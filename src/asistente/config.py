from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración desde variables de entorno. Falla al arrancar si falta un secreto."""

    # Una variable vacía cuenta como no definida: las opcionales quedan en None y las
    # obligatorias fallan con "campo requerido" en vez de un error de conversión confuso.
    # hide_input_in_errors: sin esto, un error de validación imprime el diccionario de entrada
    # (con el inicio de las claves) en tracebacks y logs.
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_ignore_empty=True, hide_input_in_errors=True
    )

    groq_api_key: SecretStr
    tavily_api_key: SecretStr
    database_url: SecretStr  # conexión directa a Postgres con el rol de mínimos privilegios
    discord_token: SecretStr

    discord_owner_id: int
    discord_guild_id: int
    discord_channel_ids: Annotated[frozenset[int], NoDecode]

    # Canal donde el heartbeat publica avisos. Sin él, el heartbeat queda desactivado.
    discord_canal_avisos_id: int | None = None
    # Canal del vigía de temas (#vigia-temas). Sin él, el vigía queda desactivado.
    discord_canal_vigia_id: int | None = None
    vigia_intervalo_s: int = Field(default=600, ge=60)  # cada cuánto mira qué temas tocan
    vigia_max_busquedas_dia: int = Field(default=30, ge=1)  # protege el cupo mensual de Tavily
    heartbeat_intervalo_s: int = Field(default=60, ge=30)
    aviso_hora_inicio: int = Field(default=8, ge=0, le=23)  # horario diurno, hora local
    aviso_hora_fin: int = Field(default=21, ge=1, le=24)

    timezone: str = "America/Santiago"

    # Llama 3.x ya no está en el catálogo de Groq; estos pasaron la prueba de tool calling.
    modelo_agente: str = "openai/gpt-oss-120b"
    modelo_resumen: str = "openai/gpt-oss-20b"
    # Respaldos del chat, en orden, cuando el principal (o el anterior de la lista) agota su cupo
    # diario. Cada modelo tiene su propio cupo de 200K tokens/día en el plan gratuito de Groq, así
    # que cada uno que se agrega es cupo extra, no solo tolerancia a fallos. Vacío = sin respaldo.
    modelos_respaldo: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
    )
    # Solo para /uso: el tope diario de tokens por modelo en el plan gratuito de Groq.
    groq_limite_diario_tokens: int = 200_000

    @field_validator("groq_api_key", "tavily_api_key", "database_url", "discord_token")
    @classmethod
    def _secreto_no_vacio(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value().strip():
            raise ValueError("no puede estar vacío")
        return v

    @field_validator("discord_channel_ids", mode="before")
    @classmethod
    def _parse_channel_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return frozenset(int(x) for x in v.split(",") if x.strip())
        return v

    @field_validator("modelos_respaldo", mode="before")
    @classmethod
    def _parse_modelos_respaldo(cls, v: object) -> object:
        if isinstance(v, str):
            return [m.strip() for m in v.split(",") if m.strip()]
        return v

    @model_validator(mode="after")
    def _canal_de_avisos_permitido(self) -> "Settings":
        for nombre, canal in (
            ("DISCORD_CANAL_AVISOS_ID", self.discord_canal_avisos_id),
            ("DISCORD_CANAL_VIGIA_ID", self.discord_canal_vigia_id),
        ):
            if canal is not None and canal not in self.discord_channel_ids:
                raise ValueError(f"{nombre} debe estar en DISCORD_CHANNEL_IDS")
        if self.aviso_hora_inicio >= self.aviso_hora_fin:
            raise ValueError("AVISO_HORA_INICIO debe ser menor que AVISO_HORA_FIN")
        return self

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
