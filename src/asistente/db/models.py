from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProcesoEstado(StrEnum):
    IDEA = "idea"
    ACTIVO = "activo"
    EN_ESPERA = "en_espera"
    BLOQUEADO = "bloqueado"
    COMPLETADO = "completado"
    CANCELADO = "cancelado"


class Prioridad(StrEnum):
    BAJA = "baja"
    MEDIA = "media"
    ALTA = "alta"


class EventoTipo(StrEnum):
    NOTA = "nota"
    CAMBIO_ESTADO = "cambio_estado"
    ACCION_HECHA = "accion_hecha"
    CHEQUEO_AGENTE = "chequeo_agente"


class MemoriaOrigen(StrEnum):
    USUARIO = "usuario"
    AGENTE = "agente"
    EXTERNO = "externo"


class ProcesoNuevo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=200)
    descripcion: str | None = None
    estado: ProcesoEstado = ProcesoEstado.ACTIVO
    prioridad: Prioridad = Prioridad.MEDIA
    proxima_accion: str | None = None
    proxima_accion_fecha: datetime | None = None
    esperando_a: str | None = None
    bloqueo_detalle: str | None = None
    fecha_limite: date | None = None
    etiquetas: list[str] = Field(default_factory=list)
    frecuencia_chequeo_dias: int | None = Field(default=None, gt=0)


class Proceso(ProcesoNuevo):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    ultimo_chequeo: datetime | None
    creado_en: datetime
    actualizado_en: datetime


class ProcesoActualizacion(BaseModel):
    """Solo los campos editables. Lo no informado no se toca; None explícito borra el valor."""

    model_config = ConfigDict(extra="forbid")

    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    descripcion: str | None = None
    estado: ProcesoEstado | None = None
    prioridad: Prioridad | None = None
    proxima_accion: str | None = None
    proxima_accion_fecha: datetime | None = None
    esperando_a: str | None = None
    bloqueo_detalle: str | None = None
    fecha_limite: date | None = None
    etiquetas: list[str] | None = None
    frecuencia_chequeo_dias: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _no_nulos_obligatorios(self) -> "ProcesoActualizacion":
        for campo in ("nombre", "estado", "prioridad", "etiquetas"):
            if campo in self.model_fields_set and getattr(self, campo) is None:
                raise ValueError(f"{campo} no puede ser nulo")
        return self


class ProcesoEvento(BaseModel):
    id: UUID
    proceso_id: UUID
    tipo: EventoTipo
    contenido: str
    creado_en: datetime


class Memoria(BaseModel):
    id: UUID
    contenido: str
    origen: MemoriaOrigen
    etiquetas: list[str]
    creado_en: datetime


class Recordatorio(BaseModel):
    id: UUID
    texto: str
    fecha: datetime
    enviado: bool
    creado_en: datetime
