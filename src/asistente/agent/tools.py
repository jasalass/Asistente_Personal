"""Tools del agente. Todas son nivel AUTO: solo leen y escriben en la base propia.

Nada acá contacta a terceros ni gasta dinero; eso, cuando exista, se registra como PROPONE.
"""

from datetime import date, datetime, time
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from asistente.db.connection import Conn
from asistente.db.models import (
    EventoTipo,
    MemoriaOrigen,
    Prioridad,
    ProcesoActualizacion,
    ProcesoEstado,
    ProcesoNuevo,
    TemaActualizacion,
    TemaNuevo,
)
from asistente.db.repos.memorias import MemoriaRepo
from asistente.db.repos.procesos import ProcesoRepo
from asistente.db.repos.recordatorios import RecordatorioRepo
from asistente.db.repos.temas import TemaRepo
from asistente.security.tool_registry import Level, ToolError, ToolRegistry, ToolSpec

# Campos de texto que el LLM puede vaciar enviando "" (un null suele significar "no lo mencioné").
_TEXTO_BORRABLE = ("descripcion", "proxima_accion", "esperando_a", "bloqueo_detalle")

# Groq valida los argumentos contra el esquema en su servidor, y los formatos `date-time` y `time`
# de JSON Schema exigen desfase horario o segundos ("08:00" se rechazaba con un 400). Por eso el
# modelo ve strings con un patrón, y el sistema los interpreta y les aplica la zona del usuario.
# El desfase se acepta pero se descarta: el que agrega el modelo no es confiable (ver `con_zona`).
FechaHoraLocal = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})?$",
        description="Fecha y hora local ISO 8601, ej. 2026-10-15T13:00:00",
    ),
]
HoraLocal = Annotated[
    str,
    Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="Hora local HH:MM, ej. 08:00"),
]


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
    proxima_accion_fecha: FechaHoraLocal | None = None  # type: ignore[assignment]


class ActualizarProcesoArgs(_SinNulos):
    id: UUID
    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    descripcion: str | None = Field(default=None, description="Envía '' para borrarlo")
    estado: ProcesoEstado | None = None
    prioridad: Prioridad | None = None
    proxima_accion: str | None = Field(default=None, description="Envía '' para borrarla")
    proxima_accion_fecha: FechaHoraLocal | None = None
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


class CrearTemaArgs(_SinNulos, TemaNuevo):
    hora_preferida: HoraLocal | None = None  # type: ignore[assignment]
    avisar_sin_novedades: bool | None = Field(  # type: ignore[assignment]
        default=None,
        description="Solo true si el usuario pidió expresamente que le avisen cuando no hay novedades",
    )


class ListarTemasArgs(_SinNulos):
    solo_activos: bool = False


class ActualizarTemaArgs(_SinNulos, TemaActualizacion):
    id: UUID
    hora_preferida: HoraLocal | None = None  # type: ignore[assignment]


class CrearRecordatorioArgs(_SinNulos):
    texto: str = Field(min_length=1, max_length=500)
    fecha: FechaHoraLocal


def construir_registro(conn: Conn, tz: ZoneInfo) -> ToolRegistry:
    """Registro con todas las tools atadas a una conexión (una unidad de trabajo)."""
    procesos, memorias, recordatorios = ProcesoRepo(conn), MemoriaRepo(conn), RecordatorioRepo(conn)
    temas = TemaRepo(conn)

    def con_zona(dt: datetime | None) -> datetime | None:
        """Toma la hora tal como se dijo, en la zona del usuario, y descarta cualquier desfase.

        Los modelos suelen agregar el desfase estándar de Chile (-04:00) aunque en verano sea
        -03:00, lo que corría todas las fechas una hora. El desfase que traiga no es confiable.
        """
        return dt.replace(tzinfo=tz) if dt is not None else None

    def fecha_hora(valor: str | None) -> datetime | None:
        return con_zona(datetime.fromisoformat(valor)) if valor is not None else None

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
        campos["proxima_accion_fecha"] = fecha_hora(campos.get("proxima_accion_fecha"))
        return procesos.crear(ProcesoNuevo(**campos)).model_dump(mode="json")

    def actualizar_proceso(id, **campos):
        proceso_o_error(id)
        for k in _TEXTO_BORRABLE:
            if campos.get(k) == "":
                campos[k] = None
        if "proxima_accion_fecha" in campos:
            campos["proxima_accion_fecha"] = fecha_hora(campos["proxima_accion_fecha"])
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
        return recordatorios.crear(texto, fecha_hora(fecha)).model_dump(mode="json")

    def crear_tema(**campos):
        if "hora_preferida" in campos:
            campos["hora_preferida"] = time.fromisoformat(campos["hora_preferida"])
        return temas.crear(TemaNuevo(**campos)).model_dump(mode="json")

    def listar_temas(solo_activos=False):
        return [t.model_dump(mode="json") for t in temas.listar(solo_activos=solo_activos)]

    def actualizar_tema(id, **campos):
        if temas.obtener(id) is None:
            raise ToolError("No existe un tema con ese id. Usa listar_temas primero.")
        if "hora_preferida" in campos:
            campos["hora_preferida"] = time.fromisoformat(campos["hora_preferida"])
        try:
            return temas.actualizar(id, TemaActualizacion(**campos)).model_dump(mode="json")
        except ValidationError as e:
            # Solo el motivo: p. ej. "cada_x_dias requiere intervalo_dias".
            raise ToolError("; ".join(err["msg"] for err in e.errors())) from None

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
        (
            "listar_temas",
            "Lista los temas que el vigía sigue (búsqueda periódica de novedades en la web).",
            listar_temas,
            ListarTemasArgs,
        ),
        (
            "crear_tema",
            (
                "Crea un tema para que el vigía busque novedades periódicamente y las publique "
                "en #vigia-temas. No lee ni devuelve los artículos."
            ),
            crear_tema,
            CrearTemaArgs,
        ),
        (
            "actualizar_tema",
            (
                "Modifica un tema del vigía (frecuencia, búsqueda, cantidad...) o lo pausa con "
                "activo=false. Los temas no se borran."
            ),
            actualizar_tema,
            ActualizarTemaArgs,
        ),
    ]:
        registro.register(ToolSpec(nombre, Level.AUTO, descripcion, handler, params))
    return registro
