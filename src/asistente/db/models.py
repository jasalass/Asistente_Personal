from datetime import date, datetime, time
from enum import StrEnum
from typing import Annotated
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


class TemaTipo(StrEnum):
    NOTICIAS = "noticias"
    PAPERS = "papers"
    BLOGS = "blogs"
    MIXTO = "mixto"


class Frecuencia(StrEnum):
    DIARIA = "diaria"
    CADA_X_DIAS = "cada_x_dias"
    DIAS_ESPECIFICOS = "dias_especificos"


def _validar_frecuencia(m: "TemaNuevo") -> "TemaNuevo":
    if m.frecuencia is Frecuencia.CADA_X_DIAS and m.intervalo_dias is None:
        raise ValueError("cada_x_dias requiere intervalo_dias")
    if m.frecuencia is Frecuencia.DIAS_ESPECIFICOS and not m.dias_semana:
        raise ValueError("dias_especificos requiere dias_semana")
    return m


class TemaNuevo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=120)
    query_busqueda: str = Field(min_length=3, max_length=300)
    tipo_contenido: TemaTipo = TemaTipo.MIXTO
    frecuencia: Frecuencia = Frecuencia.DIARIA
    intervalo_dias: int | None = Field(default=None, gt=0, le=365)
    dias_semana: list[Annotated[int, Field(ge=1, le=7)]] | None = Field(
        default=None, description="1 = lunes ... 7 = domingo"
    )
    hora_preferida: time = time(8, 0)
    ventana_frescura_horas: int = Field(default=48, ge=1, le=24 * 30)
    cantidad_resultados: int = Field(default=5, ge=1, le=10)
    avisar_sin_novedades: bool = False
    activo: bool = True

    @model_validator(mode="after")
    def _coherente(self) -> "TemaNuevo":
        return _validar_frecuencia(self)


class Tema(TemaNuevo):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    ultima_ejecucion: datetime | None
    creado_en: datetime
    actualizado_en: datetime


class TemaActualizacion(BaseModel):
    """Campos editables de un tema. Lo no informado no se toca."""

    model_config = ConfigDict(extra="forbid")

    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    query_busqueda: str | None = Field(default=None, min_length=3, max_length=300)
    tipo_contenido: TemaTipo | None = None
    frecuencia: Frecuencia | None = None
    intervalo_dias: int | None = Field(default=None, gt=0, le=365)
    dias_semana: list[Annotated[int, Field(ge=1, le=7)]] | None = None
    hora_preferida: time | None = None
    ventana_frescura_horas: int | None = Field(default=None, ge=1, le=24 * 30)
    cantidad_resultados: int | None = Field(default=None, ge=1, le=10)
    avisar_sin_novedades: bool | None = None
    activo: bool | None = None


class AccionExcepcion(StrEnum):
    OMITIR = "omitir"  # ese día no ocurre
    MANTENER = "mantener"  # ese día ocurre aunque sea feriado


def _dias_ordenados(v: list[int] | None) -> list[int] | None:
    return sorted(set(v)) if v is not None else None


class EventoNuevo(BaseModel):
    """Evento semanal recurrente. `dias_semana`: 1 = lunes ... 7 = domingo."""

    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=120)
    descripcion: str | None = Field(default=None, max_length=500)
    dias_semana: list[Annotated[int, Field(ge=1, le=7)]] = Field(min_length=1)
    hora: time
    duracion_min: int | None = Field(default=None, ge=1, le=1440)
    aviso_min_antes: int | None = Field(default=60, ge=0, le=1440)  # None o 0 = sin aviso
    suspender_feriados: bool = True
    vigente_desde: date | None = None
    vigente_hasta: date | None = None
    activo: bool = True

    @model_validator(mode="after")
    def _coherente(self) -> "EventoNuevo":
        self.dias_semana = _dias_ordenados(self.dias_semana)
        if self.vigente_desde and self.vigente_hasta and self.vigente_hasta < self.vigente_desde:
            raise ValueError("vigente_hasta no puede ser anterior a vigente_desde")
        return self


class Evento(EventoNuevo):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    creado_en: datetime
    actualizado_en: datetime


class EventoActualizacion(BaseModel):
    """Campos editables. Lo no informado no se toca."""

    model_config = ConfigDict(extra="forbid")

    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    descripcion: str | None = Field(default=None, max_length=500)
    dias_semana: list[Annotated[int, Field(ge=1, le=7)]] | None = Field(default=None, min_length=1)
    hora: time | None = None
    duracion_min: int | None = Field(default=None, ge=1, le=1440)
    aviso_min_antes: int | None = Field(default=None, ge=0, le=1440)
    suspender_feriados: bool | None = None
    vigente_desde: date | None = None
    vigente_hasta: date | None = None
    activo: bool | None = None


class Recordatorio(BaseModel):
    id: UUID
    texto: str
    fecha: datetime
    enviado: bool
    creado_en: datetime
