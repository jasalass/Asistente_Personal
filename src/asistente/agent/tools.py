"""Tools del agente. Todas son nivel AUTO: solo leen y escriben en la base propia.

Nada acá contacta a terceros ni gasta dinero; eso, cuando exista, se registra como PROPONE.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from asistente.db.connection import Conn
from asistente.db.models import (
    EventoTipo,
    MemoriaOrigen,
    Prioridad,
    ProcesoActualizacion,
    ProcesoEstado,
    ProcesoNuevo,
)
from asistente.db.repos.memorias import MemoriaRepo
from asistente.db.repos.procesos import ProcesoRepo
from asistente.db.repos.recordatorios import RecordatorioRepo
from asistente.security.tool_registry import Level, ToolError, ToolRegistry, ToolSpec

# Campos de texto que el LLM puede vaciar enviando "" (un null suele significar "no lo mencioné").
_TEXTO_BORRABLE = ("descripcion", "proxima_accion", "esperando_a", "bloqueo_detalle")


class _SinNulos(BaseModel):
    """Los modelos suelen mandar null en los parámetros que no usan: se tratan como ausentes.

    Los parámetros que no existen se rechazan, para que el modelo se corrija en vez de alucinar.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _quitar_nulos(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            return {k: v for k, v in datos.items() if v is not None}
        return datos


class CrearProcesoArgs(_SinNulos, ProcesoNuevo):
    pass


class ActualizarProcesoArgs(_SinNulos):
    id: UUID
    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    descripcion: str | None = Field(default=None, description="Envía '' para borrarlo")
    estado: ProcesoEstado | None = None
    prioridad: Prioridad | None = None
    proxima_accion: str | None = Field(default=None, description="Envía '' para borrarla")
    proxima_accion_fecha: datetime | None = None
    esperando_a: str | None = Field(default=None, description="Envía '' para borrarlo")
    bloqueo_detalle: str | None = Field(default=None, description="Envía '' para borrarlo")
    fecha_limite: date | None = None
    etiquetas: list[str] | None = None
    frecuencia_chequeo_dias: int | None = Field(default=None, gt=0)


class ListarProcesosArgs(_SinNulos):
    estados: list[ProcesoEstado] | None = Field(
        default=None, description="Filtrar por estado. Sin valor: todos."
    )
    limite: int = Field(default=20, ge=1, le=50)


class BuscarProcesosArgs(_SinNulos):
    texto: str = Field(min_length=1, description="Parte del nombre del proceso")


class AgregarNotaArgs(_SinNulos):
    id: UUID
    nota: str = Field(min_length=1)
    tipo: EventoTipo = Field(
        default=EventoTipo.NOTA, description="'nota' o 'accion_hecha' (algo que ya se hizo)"
    )


class HistorialArgs(_SinNulos):
    id: UUID
    limite: int = Field(default=10, ge=1, le=30)


class GuardarMemoriaArgs(_SinNulos):
    contenido: str = Field(min_length=1, max_length=2000)
    etiquetas: list[str] = Field(default_factory=list)


class BuscarMemoriasArgs(_SinNulos):
    consulta: str = Field(min_length=1)
    limite: int = Field(default=5, ge=1, le=15)


class CrearRecordatorioArgs(_SinNulos):
    texto: str = Field(min_length=1, max_length=500)
    fecha: datetime = Field(description="Fecha y hora ISO 8601, en hora local del usuario")


def construir_registro(conn: Conn, tz: ZoneInfo) -> ToolRegistry:
    """Registro con todas las tools atadas a una conexión (una unidad de trabajo)."""
    procesos, memorias, recordatorios = ProcesoRepo(conn), MemoriaRepo(conn), RecordatorioRepo(conn)

    def con_zona(dt: datetime | None) -> datetime | None:
        return dt.replace(tzinfo=tz) if dt is not None and dt.tzinfo is None else dt

    def proceso_o_error(pid: UUID):
        p = procesos.obtener(pid)
        if p is None:
            raise ToolError("No existe un proceso con ese id. Usa buscar_procesos primero.")
        return p

    def listar_procesos(estados=None, limite=20):
        return [p.model_dump(mode="json") for p in procesos.listar(estados, limite)]

    def buscar_procesos(texto):
        return [p.model_dump(mode="json") for p in procesos.buscar_por_nombre(texto)]

    def crear_proceso(**campos):
        campos["proxima_accion_fecha"] = con_zona(campos.get("proxima_accion_fecha"))
        return procesos.crear(ProcesoNuevo(**campos)).model_dump(mode="json")

    def actualizar_proceso(id, **campos):
        proceso_o_error(id)
        for k in _TEXTO_BORRABLE:
            if campos.get(k) == "":
                campos[k] = None
        if "proxima_accion_fecha" in campos:
            campos["proxima_accion_fecha"] = con_zona(campos["proxima_accion_fecha"])
        actualizado = procesos.actualizar(id, ProcesoActualizacion(**campos))
        return actualizado.model_dump(mode="json")

    def agregar_nota_proceso(id, nota, tipo=EventoTipo.NOTA):
        proceso_o_error(id)
        return procesos.agregar_evento(id, tipo, nota).model_dump(mode="json")

    def ver_historial_proceso(id, limite=10):
        proceso_o_error(id)
        return [e.model_dump(mode="json") for e in procesos.eventos(id, limite)]

    def guardar_memoria(contenido, etiquetas=()):
        m = memorias.guardar(contenido, MemoriaOrigen.USUARIO, etiquetas)
        return m.model_dump(mode="json")

    def buscar_memorias(consulta, limite=5):
        return [m.model_dump(mode="json") for m in memorias.buscar(consulta, limite)]

    def crear_recordatorio(texto, fecha):
        return recordatorios.crear(texto, con_zona(fecha)).model_dump(mode="json")

    registro = ToolRegistry()
    for nombre, descripcion, handler, params in [
        (
            "listar_procesos",
            "Lista los procesos del usuario, ordenados por prioridad. Úsala para 'qué tengo pendiente'.",
            listar_procesos,
            ListarProcesosArgs,
        ),
        (
            "buscar_procesos",
            "Busca procesos por parte del nombre. Úsala SIEMPRE antes de actualizar para obtener el id.",
            buscar_procesos,
            BuscarProcesosArgs,
        ),
        (
            "crear_proceso",
            "Registra un proceso, trámite o proyecto nuevo que el usuario está llevando.",
            crear_proceso,
            CrearProcesoArgs,
        ),
        (
            "actualizar_proceso",
            "Modifica campos de un proceso existente (estado, próxima acción, a quién se espera...).",
            actualizar_proceso,
            ActualizarProcesoArgs,
        ),
        (
            "agregar_nota_proceso",
            "Agrega una nota o una acción realizada al historial de un proceso.",
            agregar_nota_proceso,
            AgregarNotaArgs,
        ),
        (
            "ver_historial_proceso",
            "Muestra los últimos eventos de un proceso.",
            ver_historial_proceso,
            HistorialArgs,
        ),
        (
            "guardar_memoria",
            "Guarda un dato o preferencia del usuario que no encaja en un proceso.",
            guardar_memoria,
            GuardarMemoriaArgs,
        ),
        (
            "buscar_memorias",
            "Busca en las memorias guardadas por texto.",
            buscar_memorias,
            BuscarMemoriasArgs,
        ),
        (
            "crear_recordatorio",
            "Crea un recordatorio para una fecha y hora concretas.",
            crear_recordatorio,
            CrearRecordatorioArgs,
        ),
    ]:
        registro.register(ToolSpec(nombre, Level.AUTO, descripcion, handler, params))
    return registro
