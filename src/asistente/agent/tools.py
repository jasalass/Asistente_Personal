"""Tools del agente. Todas son nivel AUTO: solo leen y escriben en la base propia.

Nada acá contacta a terceros ni gasta dinero; eso, cuando exista, se registra como PROPONE.
"""

import unicodedata
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from asistente.agenda.consulta import consultar, formatear
from asistente.agenda.feriados import CalendarioFeriados, Feriados
from asistente.agenda.nombres import validar_nombre
from asistente.agenda.ocurrencias import (
    DIAS,
    advertencia_de_vigencia,
    describir,
    nombre_dia,
    proximas,
)
from asistente.agent.prompt import WORKSPACE
from asistente.db.connection import Conn
from asistente.db.models import (
    AccionExcepcion,
    EventoActualizacion,
    EventoNuevo,
    EventoTipo,
    MemoriaOrigen,
    Prioridad,
    ProcesoActualizacion,
    ProcesoEstado,
    ProcesoNuevo,
    TemaActualizacion,
    TemaNuevo,
)
from asistente.db.repos.agenda import EventoRepo
from asistente.db.repos.auditoria import AuditoriaRepo
from asistente.db.repos.memorias import MemoriaRepo
from asistente.db.repos.procesos import ProcesoRepo
from asistente.db.repos.recordatorios import RecordatorioRepo
from asistente.db.repos.temas import TemaRepo
from asistente.security.tool_registry import Level, ToolError, ToolRegistry, ToolSpec

_CERRADOS = (ProcesoEstado.COMPLETADO, ProcesoEstado.CANCELADO)

GRUPO_VIGIA = "vigia"
_TOOLS_DEL_VIGIA = frozenset({"listar_temas", "crear_tema", "actualizar_tema"})


def leer_skill(nombre: str) -> str:
    """Instrucciones bajo demanda (workspace/skills/bajo_demanda), de solo lectura."""
    return (WORKSPACE / "skills" / "bajo_demanda" / f"{nombre}.md").read_text(encoding="utf-8")

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


class _RefProceso(_SinNulos):
    """Un proceso se puede indicar por id o por nombre: así no hace falta una búsqueda previa."""

    id: UUID | None = Field(default=None, description="Id del proceso, si ya lo conoces")
    proceso: str | None = Field(default=None, min_length=1, description="Nombre (o parte) del proceso")

    @model_validator(mode="after")
    def _uno_solo(self) -> "_RefProceso":
        if (self.id is None) == (self.proceso is None):
            raise ValueError("indica el proceso con 'id' o con 'proceso' (solo uno)")
        return self


class _RefTema(_SinNulos):
    id: UUID | None = Field(default=None, description="Id del tema, si ya lo conoces")
    tema: str | None = Field(default=None, min_length=1, description="Nombre (o parte) del tema")

    @model_validator(mode="after")
    def _uno_solo(self) -> "_RefTema":
        if (self.id is None) == (self.tema is None):
            raise ValueError("indica el tema con 'id' o con 'tema' (solo uno)")
        return self


class ActualizarProcesoArgs(_RefProceso):
    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    descripcion: str | None = None
    estado: ProcesoEstado | None = None
    prioridad: Prioridad | None = None
    proxima_accion: str | None = None
    proxima_accion_fecha: FechaHoraLocal | None = None
    esperando_a: str | None = None
    bloqueo_detalle: str | None = None
    fecha_limite: date | None = None
    etiquetas: list[str] | None = None
    frecuencia_chequeo_dias: int | None = Field(default=None, gt=0)
    nota: str | None = Field(
        default=None, description="Lo que pasó; se agrega al historial en la misma llamada"
    )


class ListarProcesosArgs(_SinNulos):
    estados: list[ProcesoEstado] | None = Field(
        default=None, description="Filtrar por estado. Sin valor: todos."
    )
    texto: str | None = Field(default=None, min_length=1, description="Buscar por parte del nombre")
    limite: int = Field(default=20, ge=1, le=50)


class AgregarNotaArgs(_RefProceso):
    nota: str = Field(min_length=1)
    tipo: EventoTipo = Field(
        default=EventoTipo.NOTA, description="'nota' o 'accion_hecha' (algo que ya se hizo)"
    )


class HistorialArgs(_RefProceso):
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


class ActualizarTemaArgs(_RefTema, TemaActualizacion):
    hora_preferida: HoraLocal | None = None  # type: ignore[assignment]


class CrearRecordatorioArgs(_SinNulos):
    texto: str = Field(min_length=1, max_length=500)
    fecha: FechaHoraLocal


class CancelarRecordatorioArgs(_SinNulos):
    id: UUID | None = Field(default=None, description="Id del recordatorio, si ya lo conoces")
    recordatorio: str | None = Field(default=None, min_length=1, description="Texto (o parte) del recordatorio")

    @model_validator(mode="after")
    def _uno_solo(self) -> "CancelarRecordatorioArgs":
        if (self.id is None) == (self.recordatorio is None):
            raise ValueError("indica el recordatorio con 'id' o con 'recordatorio' (solo uno)")
        return self


class CrearEventoArgs(_SinNulos):
    nombre: str = Field(
        min_length=1,
        max_length=120,
        description="Nombre corto (máx. 60), ej. 'DSY1104 Desarrollo Fullstack II'. Profesor y sala van en descripcion",
    )
    dias: list[str] = Field(min_length=1, description="lunes, martes, miércoles... (uno o varios)")
    hora: HoraLocal
    duracion_min: int | None = Field(default=None, ge=1, le=1440)
    aviso_min_antes: int | None = Field(
        default=None, ge=0, le=1440, description="Minutos antes para avisar; 60 si no se indica, 0 = sin aviso"
    )
    suspender_feriados: bool | None = Field(
        default=None, description="true por defecto; false solo si ocurre aunque sea feriado"
    )
    desde: date | None = Field(
        default=None,
        description="SOLO si el usuario dio una fecha de inicio; si no, omitir (aplica desde hoy). YYYY-MM-DD",
    )
    hasta: date | None = Field(
        default=None, description="SOLO si el usuario dio una fecha de término; si no, omitir. YYYY-MM-DD"
    )
    descripcion: str | None = Field(default=None, max_length=500, description="Lugar, sala, enlace...")


class _RefEvento(_SinNulos):
    id: UUID | None = Field(default=None, description="Id del evento, si ya lo conoces")
    evento: str | None = Field(default=None, min_length=1, description="Nombre (o parte) del evento")

    @model_validator(mode="after")
    def _uno_solo(self) -> "_RefEvento":
        if (self.id is None) == (self.evento is None):
            raise ValueError("indica el evento con 'id' o con 'evento' (solo uno)")
        return self


class ActualizarEventoArgs(_RefEvento):
    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    dias: list[str] | None = Field(default=None, min_length=1)
    hora: HoraLocal | None = None
    duracion_min: int | None = Field(default=None, ge=1, le=1440)
    aviso_min_antes: int | None = Field(default=None, ge=0, le=1440)
    suspender_feriados: bool | None = None
    desde: date | None = Field(default=None, description="Solo si el usuario lo pidió. YYYY-MM-DD")
    hasta: date | None = Field(default=None, description="Solo si el usuario lo pidió. YYYY-MM-DD")
    descripcion: str | None = Field(default=None, max_length=500)
    activo: bool | None = None
    omitir_fecha: date | None = Field(default=None, description="Ese día no hay evento (YYYY-MM-DD)")
    mantener_fecha: date | None = Field(
        default=None, description="Ese día sí hay evento, aunque sea feriado (YYYY-MM-DD)"
    )
    motivo: str | None = Field(default=None, max_length=200)


class ListarAgendaArgs(_SinNulos):
    desde: date | None = Field(default=None, description="Primer día (YYYY-MM-DD); hoy si no se indica")
    dias: int = Field(default=1, ge=1, le=14, description="Cuántos días mostrar")


def _sin_tildes(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto.casefold()) if unicodedata.category(c) != "Mn"
    )


_NUMERO_DE_DIA = {_sin_tildes(d): i for i, d in enumerate(DIAS, start=1)}


def dias_a_numeros(nombres: list[str]) -> list[int]:
    """'lunes' -> 1 ... 'domingo' -> 7 (ISO). Acepta con o sin tilde y en cualquier caso."""
    numeros = []
    for n in nombres:
        numero = _NUMERO_DE_DIA.get(_sin_tildes(n.strip()))
        if numero is None:
            raise ToolError(f"'{n}' no es un día de la semana (lunes, martes, miércoles...).")
        numeros.append(numero)
    return sorted(set(numeros))


def construir_registro(
    conn: Conn,
    tz: ZoneInfo,
    ahora: datetime | None = None,
    feriados: CalendarioFeriados | None = None,
) -> ToolRegistry:
    """Registro con todas las tools atadas a una conexión (una unidad de trabajo)."""
    procesos, memorias, recordatorios = ProcesoRepo(conn), MemoriaRepo(conn), RecordatorioRepo(conn)
    temas, eventos = TemaRepo(conn), EventoRepo(conn)
    feriados = feriados or Feriados()

    def hoy() -> date:
        return (ahora or datetime.now(tz)).astimezone(tz).date()

    def con_zona(dt: datetime | None) -> datetime | None:
        """Toma la hora tal como se dijo, en la zona del usuario, y descarta cualquier desfase.

        Los modelos suelen agregar el desfase estándar de Chile (-04:00) aunque en verano sea
        -03:00, lo que corría todas las fechas una hora. El desfase que traiga no es confiable.
        """
        return dt.replace(tzinfo=tz) if dt is not None else None

    def fecha_hora(valor: str | None) -> datetime | None:
        return con_zona(datetime.fromisoformat(valor)) if valor is not None else None

    def elegir(candidatos: list, texto: str, cerrado, que: str, herramienta: str):
        """Resuelve un nombre a un único elemento, o explica qué falta. Prefiere los abiertos."""
        exactos = [c for c in candidatos if c.nombre.casefold() == texto.casefold()]
        candidatos = exactos or candidatos
        candidatos = [c for c in candidatos if not cerrado(c)] or candidatos
        if not candidatos:
            raise ToolError(f"No encontré ningún {que} que coincida con '{texto}'. Usa {herramienta}.")
        if len(candidatos) > 1:
            opciones = "; ".join(f"{c.nombre} (id {c.id})" for c in candidatos)
            raise ToolError(f"Varios {que}s coinciden con '{texto}': {opciones}. Repite con el id.")
        return candidatos[0]

    def resolver_proceso(id=None, proceso=None):
        if id is not None:
            p = procesos.obtener(id)
            if p is None:
                raise ToolError("No existe un proceso con ese id. Usa listar_procesos.")
            return p
        return elegir(
            procesos.buscar_por_nombre(proceso, limite=8), proceso,
            lambda p: p.estado in _CERRADOS, "proceso", "listar_procesos",
        )

    def resolver_tema(id=None, tema=None):
        if id is not None:
            t = temas.obtener(id)
            if t is None:
                raise ToolError("No existe un tema con ese id. Usa listar_temas.")
            return t
        return elegir(
            temas.buscar_por_nombre(tema, limite=8), tema, lambda t: not t.activo, "tema", "listar_temas"
        )

    def listar_procesos(estados=None, limite=20, texto=None):
        lista = procesos.buscar_por_nombre(texto, limite) if texto else procesos.listar(estados, limite)
        return [p.model_dump(mode="json") for p in lista]

    def crear_proceso(**campos):
        nombre = campos["nombre"]
        for p in procesos.buscar_por_nombre(nombre, limite=8):
            if p.nombre.casefold() == nombre.casefold() and p.estado not in _CERRADOS:
                raise ToolError(
                    f"Ya existe el proceso abierto '{p.nombre}' (id {p.id}). "
                    "Actualízalo en lugar de crear otro."
                )
        campos["proxima_accion_fecha"] = fecha_hora(campos.get("proxima_accion_fecha"))
        return procesos.crear(ProcesoNuevo(**campos)).model_dump(mode="json")

    def actualizar_proceso(id=None, proceso=None, **campos):
        pid = resolver_proceso(id, proceso).id
        nota = campos.pop("nota", None)
        for k in _TEXTO_BORRABLE:
            if campos.get(k) == "":
                campos[k] = None
        if "proxima_accion_fecha" in campos:
            campos["proxima_accion_fecha"] = fecha_hora(campos["proxima_accion_fecha"])
        actualizado = procesos.actualizar(pid, ProcesoActualizacion(**campos))
        if nota:
            procesos.agregar_evento(pid, EventoTipo.NOTA, nota)
        return actualizado.model_dump(mode="json")

    def agregar_nota_proceso(nota, id=None, proceso=None, tipo=EventoTipo.NOTA):
        pid = resolver_proceso(id, proceso).id
        return procesos.agregar_evento(pid, tipo, nota).model_dump(mode="json")

    def ver_historial_proceso(id=None, proceso=None, limite=10):
        pid = resolver_proceso(id, proceso).id
        return [e.model_dump(mode="json") for e in procesos.eventos(pid, limite)]

    def guardar_memoria(contenido, etiquetas=()):
        m = memorias.guardar(contenido, MemoriaOrigen.USUARIO, etiquetas)
        return m.model_dump(mode="json")

    def buscar_memorias(consulta, limite=5):
        return [m.model_dump(mode="json") for m in memorias.buscar(consulta, limite)]

    def crear_recordatorio(texto, fecha):
        return recordatorios.crear(texto, fecha_hora(fecha)).model_dump(mode="json")

    def crear_tema(**campos):
        for t in temas.buscar_por_nombre(campos["nombre"], limite=8):
            if t.nombre.casefold() == campos["nombre"].casefold():
                estado = "activo" if t.activo else "pausado"
                raise ToolError(
                    f"Ya existe el tema '{t.nombre}' ({estado}, id {t.id}). Usa actualizar_tema."
                )
        if "hora_preferida" in campos:
            campos["hora_preferida"] = time.fromisoformat(campos["hora_preferida"])
        return temas.crear(TemaNuevo(**campos)).model_dump(mode="json")

    def listar_temas(solo_activos=False):
        return [t.model_dump(mode="json") for t in temas.listar(solo_activos=solo_activos)]

    def actualizar_tema(id=None, tema=None, **campos):
        tid = resolver_tema(id, tema).id
        if "hora_preferida" in campos:
            campos["hora_preferida"] = time.fromisoformat(campos["hora_preferida"])
        try:
            return temas.actualizar(tid, TemaActualizacion(**campos)).model_dump(mode="json")
        except ValidationError as e:
            # Solo el motivo: p. ej. "cada_x_dias requiere intervalo_dias".
            raise ToolError("; ".join(err["msg"] for err in e.errors())) from None

    def resolver_evento(id=None, evento=None):
        if id is not None:
            e = eventos.obtener(id)
            if e is None:
                raise ToolError("No existe un evento con ese id. Usa listar_agenda.")
            return e
        return elegir(
            eventos.buscar_por_nombre(evento, limite=8), evento, lambda e: not e.activo,
            "evento", "listar_agenda",
        )

    def con_proximas(e) -> dict:
        """El evento más sus próximas ocurrencias (con las suspendidas), para confirmar con precisión."""
        inicio_dia = hoy()
        excepciones = eventos.excepciones(inicio_dia, inicio_dia + timedelta(days=120))
        ahora_local = (ahora or datetime.now(tz)).astimezone(tz)
        futuras = [
            o for o in proximas(e, inicio_dia, excepciones, feriados, cuantas=5)
            if o.inicio(tz) > ahora_local
        ][:4]
        # Compacto: cada llamada al modelo reenvía estos resultados, y con 4 eventos seguidos el
        # exceso de campos (fechas de creación, nulos) ayudó a pasar el límite por minuto.
        resultado = e.model_dump(mode="json", exclude_none=True, exclude={"creado_en", "actualizado_en"})
        resultado["resumen"] = describir(e)  # lo que quedó guardado, redactado por el código
        if aviso := advertencia_de_vigencia(e, inicio_dia):
            resultado["advertencia"] = aviso
        resultado["proximas"] = [
            f"{nombre_dia(o.fecha)} {o.fecha:%d/%m} {e.hora:%H:%M}"
            + (f" SUSPENDIDA ({o.motivo})" if o.suspendida else "")
            for o in futuras
        ]
        return resultado

    def cancelar_recordatorio(id=None, recordatorio=None):
        if id is not None:
            r = recordatorios.obtener(id)
            if r is None:
                raise ToolError("No existe un recordatorio con ese id. Usa listar_agenda.")
        else:
            candidatos = recordatorios.buscar_por_texto(recordatorio)
            exactos = [c for c in candidatos if c.texto.casefold() == recordatorio.casefold()]
            candidatos = exactos or candidatos
            candidatos = [c for c in candidatos if not c.enviado] or candidatos  # prefiere los pendientes
            if not candidatos:
                raise ToolError(f"No encontré ningún recordatorio que coincida con '{recordatorio}'.")
            if len(candidatos) > 1:
                opciones = "; ".join(
                    f"{c.texto} ({c.fecha.astimezone(tz):%d/%m %H:%M}, id {c.id})" for c in candidatos
                )
                raise ToolError(f"Varios recordatorios coinciden: {opciones}. Repite con el id.")
            r = candidatos[0]
        recordatorios.eliminar(r.id)
        cuando = f"{r.fecha.astimezone(tz):%d/%m %H:%M}"
        # Se borra la fila; queda constancia de qué era para poder reconstruirlo si fue un error.
        AuditoriaRepo(conn).registrar(
            "agente", "recordatorio_cancelado", {"texto": r.texto, "fecha": cuando, "enviado": r.enviado}
        )
        return {"cancelado": {"texto": r.texto, "fecha": cuando, "ya_avisado": r.enviado}}

    def exigir_agenda() -> None:
        if not eventos.disponible():
            raise ToolError(
                "La agenda de eventos aún no está disponible: falta aplicar la migración 0004_agenda.sql."
            )

    def nombre_valido(nombre: str) -> str:
        try:
            return validar_nombre(nombre)
        except ValueError as e:
            raise ToolError(str(e)) from None

    def crear_evento(**campos):
        exigir_agenda()
        nombre = nombre_valido(campos["nombre"])  # corto y legible, lo pida o no el modelo
        for e in eventos.buscar_por_nombre(nombre, limite=8):
            if e.nombre.casefold() == nombre.casefold():
                estado = "activo" if e.activo else "pausado"
                raise ToolError(f"Ya existe el evento '{e.nombre}' ({estado}). Usa actualizar_evento.")
        datos = {
            "nombre": nombre,
            "descripcion": campos.get("descripcion"),
            "dias_semana": dias_a_numeros(campos["dias"]),
            "hora": time.fromisoformat(campos["hora"]),
            "duracion_min": campos.get("duracion_min"),
            "aviso_min_antes": campos.get("aviso_min_antes", 60),
            "suspender_feriados": campos.get("suspender_feriados", True),
            "vigente_desde": campos.get("desde"),
            "vigente_hasta": campos.get("hasta"),
        }
        try:
            return con_proximas(eventos.crear(EventoNuevo(**datos)))
        except ValidationError as e:
            raise ToolError("; ".join(err["msg"] for err in e.errors())) from None

    def actualizar_evento(id=None, evento=None, **campos):
        exigir_agenda()
        ev = resolver_evento(id, evento)
        omitir, mantener = campos.pop("omitir_fecha", None), campos.pop("mantener_fecha", None)
        motivo = campos.pop("motivo", None)
        if "nombre" in campos:
            campos["nombre"] = nombre_valido(campos["nombre"])
        if omitir is not None and omitir == mantener:
            raise ToolError("La misma fecha no puede omitirse y mantenerse a la vez.")
        if "dias" in campos:
            campos["dias_semana"] = dias_a_numeros(campos.pop("dias"))
        if "hora" in campos:
            campos["hora"] = time.fromisoformat(campos["hora"])
        for corto, largo in (("desde", "vigente_desde"), ("hasta", "vigente_hasta")):
            if corto in campos:
                campos[largo] = campos.pop(corto)
        try:
            actualizado = eventos.actualizar(ev.id, EventoActualizacion(**campos)) or ev
        except ValidationError as e:
            raise ToolError("; ".join(err["msg"] for err in e.errors())) from None
        if omitir is not None:
            eventos.registrar_excepcion(ev.id, omitir, AccionExcepcion.OMITIR, motivo)
        if mantener is not None:
            eventos.registrar_excepcion(ev.id, mantener, AccionExcepcion.MANTENER, motivo)
        return con_proximas(actualizado)

    def listar_agenda(desde=None, dias=1):
        return formatear(consultar(conn, desde or hoy(), dias, tz, feriados))

    def habilitar_vigia():
        """Las tools y las instrucciones del vigía se cargan solo si hacen falta: ahorra tokens."""
        habilitadas = registro.activar_grupo(GRUPO_VIGIA)
        return {"habilitadas": habilitadas, "instrucciones": leer_skill("vigia")}

    registro = ToolRegistry()
    for nombre, descripcion, handler, params in [
        (
            "listar_procesos",
            "Lista los procesos por prioridad, o los que coincidan con 'texto' en el nombre.",
            listar_procesos,
            ListarProcesosArgs,
        ),
        (
            "listar_agenda",
            (
                "Agenda de uno o más días (hasta 14): eventos, recordatorios y procesos con fecha, "
                "con los feriados. Úsala para 'qué tengo hoy/mañana/esta semana'."
            ),
            listar_agenda,
            ListarAgendaArgs,
        ),
        (
            "cancelar_recordatorio",
            "Cancela (borra) un recordatorio, por 'id' o por parte de su texto.",
            cancelar_recordatorio,
            CancelarRecordatorioArgs,
        ),
        (
            "crear_evento",
            (
                "Crea un evento semanal recurrente (clases, reuniones fijas): 'todos los lunes' = "
                "dias ['lunes']. Se suspende en feriados y avisa 60 min antes salvo que se indique."
            ),
            crear_evento,
            CrearEventoArgs,
        ),
        (
            "actualizar_evento",
            (
                "Modifica o pausa (activo=false) un evento ('id' o 'evento'), o cambia una fecha: "
                "omitir_fecha (ese día no hay) o mantener_fecha (ese día sí, aunque sea feriado)."
            ),
            actualizar_evento,
            ActualizarEventoArgs,
        ),
        (
            "crear_proceso",
            "Registra un proceso o trámite nuevo. Falla si ya hay uno abierto con ese nombre.",
            crear_proceso,
            CrearProcesoArgs,
        ),
        (
            "actualizar_proceso",
            "Modifica un proceso (estado, próxima acción, espera...) por 'id' o 'proceso'. '' borra un campo de texto.",
            actualizar_proceso,
            ActualizarProcesoArgs,
        ),
        (
            "agregar_nota_proceso",
            "Agrega una nota o acción hecha al historial de un proceso ('id' o 'proceso').",
            agregar_nota_proceso,
            AgregarNotaArgs,
        ),
        (
            "ver_historial_proceso",
            "Últimos eventos de un proceso ('id' o 'proceso').",
            ver_historial_proceso,
            HistorialArgs,
        ),
        (
            "guardar_memoria",
            "Guarda un dato o preferencia suelta. No para horarios, fechas ni procesos.",
            guardar_memoria,
            GuardarMemoriaArgs,
        ),
        (
            "buscar_memorias",
            "Busca en las memorias por texto.",
            buscar_memorias,
            BuscarMemoriasArgs,
        ),
        (
            "crear_recordatorio",
            "Crea un recordatorio para una fecha y hora.",
            crear_recordatorio,
            CrearRecordatorioArgs,
        ),
        (
            "habilitar_vigia",
            (
                "Habilita las herramientas del vigía de temas (crear, listar y pausar temas que se "
                "siguen en la web) y devuelve sus instrucciones. Llámala antes de gestionar temas."
            ),
            habilitar_vigia,
            None,
        ),
        (
            "listar_temas",
            "Lista los temas que sigue el vigía.",
            listar_temas,
            ListarTemasArgs,
        ),
        (
            "crear_tema",
            "Crea un tema que el vigía busca y publica en #vigia-temas. Falla si el nombre ya existe.",
            crear_tema,
            CrearTemaArgs,
        ),
        (
            "actualizar_tema",
            "Modifica o pausa (activo=false) un tema ('id' o 'tema'). Los temas no se borran.",
            actualizar_tema,
            ActualizarTemaArgs,
        ),
    ]:
        grupo = GRUPO_VIGIA if nombre in _TOOLS_DEL_VIGIA else None
        registro.register(ToolSpec(nombre, Level.AUTO, descripcion, handler, params, grupo))
    return registro
