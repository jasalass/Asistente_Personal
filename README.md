# Asistente Personal

Asistente personal multi-agente que actúa como "chief of staff": lleva el seguimiento de procesos
y proyectos, recuerda cosas que le cuentas por chat y vigila temas de interés. Proyecto personal,
de un solo usuario, inspirado en la arquitectura de [OpenClaw](https://github.com/openclaw/openclaw)
(gateway único, heartbeat proactivo, skills en markdown) pero con la seguridad como prioridad.

> **Estado: en construcción.** Hoy existen la base segura (configuración, permisos, base de datos y
> repositorios), el **agente** (LLM en Groq + tools de procesos, memorias y recordatorios) y el **bot de
> Discord** que los conecta, un **heartbeat** que te avisa por su cuenta y un **vigía de temas** que
> busca novedades en la web (Tavily) y las resume.

## Qué hace (objetivo)

- **Segundo cerebro**: guarda y recuerda procesos, notas y recordatorios que le cuentas por chat.
- **Chequeo proactivo (heartbeat)**: cada cierto tiempo revisa qué requiere atención y solo te
  escribe si hay algo.
- **Vigía de temas**: busca novedades de los temas que sigues (Tavily), las resume parafraseadas
  y con link a la fuente, y no repite artículos ya vistos.
- **Interfaz**: Discord, un canal por función.
- **LLM**: Groq. Agente: `openai/gpt-oss-120b`; resúmenes: `openai/gpt-oss-20b` (Llama 3.x ya no está
  en el catálogo de Groq; ambos modelos se pueden cambiar con `MODELO_AGENTE` / `MODELO_RESUMEN`).
  **Datos**: Supabase (Postgres).

## Niveles de autoridad

| Nivel | Qué puede hacer |
|---|---|
| `auto` | Leer, resumir, clasificar, recordar, investigar |
| `propone` | Enviar emails, mover o cancelar eventos: espera tu OK explícito |
| `prohibido` | Gastar dinero, comunicarse con terceros sin revisión previa |

## Seguridad

- **Solo tú**: el bot ignora a cualquier usuario, servidor o canal que no esté en la allowlist
  (`security/allowlist.py`). Se identifica por ID numérico, no por nombre.
- **Tools con default-deny**: solo se ejecuta lo registrado en el `ToolRegistry`, y el nivel de
  autoridad lo decide el código, no el prompt (`security/tool_registry.py`).
- **Aprobaciones atadas al payload**: una aprobación vale solo para la acción exacta (hash SHA-256);
  si el contenido cambia, se rechaza (`security/approvals.py`).
- **Base de datos sin API pública**: conexión directa a Postgres con un rol de mínimos privilegios
  (`asistente_bot`). No usa la Data API de Supabase ni la service key.
- **Auditoría append-only**: `auditoria` y `ejecuciones` solo admiten `INSERT` (por permisos y por
  trigger). El rol tampoco puede hacer `TRUNCATE` ni crear tablas.
- **Memoria con procedencia**: lo de origen externo (web, emails) nunca se guarda sin tu confirmación.
- **Secretos solo en variables de entorno**; nunca se muestran en `repr`/logs de la configuración y
  una clave vacía falla al arrancar.
- **Kill switch**: la tabla `estado_sistema` permite pausar el sistema; si falta la fila, asume pausa.
- **Argumentos de tools validados**: lo que genera el LLM se valida con Pydantic antes de tocar la
  base; los campos inventados se rechazan y los mensajes de error no repiten el valor recibido.
- **Reglas del agente fuera del código de negocio**: `workspace/` (personalidad, reglas y skills en
  markdown) es de solo lectura para el agente; ninguna tool escribe ahí.
- **Auditoría de cada tool**: cada llamada del agente (ok, denegada, con error o propuesta) queda en
  `auditoria`, y cada mensaje en `ejecuciones` con tokens y duración.

## Requisitos

- Python 3.11 o superior
- Una cuenta de [Supabase](https://supabase.com) (plan gratuito)
- Claves de [Groq](https://console.groq.com) y [Tavily](https://tavily.com) (planes gratuitos)
- Una aplicación de bot en el [portal de desarrolladores de Discord](https://discord.com/developers/applications)

## Puesta en marcha

### 1. Instalar

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # en Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Crear la base en Supabase

1. Crea un proyecto nuevo (región cercana a ti) con estas opciones de seguridad:
   **Data API: desactivada**, **Automatically expose new tables: desactivado**,
   **Automatic RLS: activado**.
2. En **SQL Editor**, ejecuta en orden:
   - [`supabase/migrations/0001_nucleo.sql`](supabase/migrations/0001_nucleo.sql)
   - [`supabase/migrations/0002_rol_bot.sql`](supabase/migrations/0002_rol_bot.sql)
   - [`supabase/migrations/0003_vigia.sql`](supabase/migrations/0003_vigia.sql) (tablas del vigía de
     temas; sin ella el vigía queda desactivado y lo avisa en el log al arrancar)
   - [`supabase/migrations/0004_agenda.sql`](supabase/migrations/0004_agenda.sql) (eventos recurrentes
     y sus excepciones por fecha)
3. Ejecuta aparte, con una contraseña larga y aleatoria (no se guarda en el repo):
   ```sql
   alter role asistente_bot with password 'TU_CONTRASEÑA';
   ```
4. En **Connect → Session pooler** copia la cadena de conexión. Reemplaza el usuario por
   `asistente_bot.<project-ref>` y la contraseña por la que acabas de crear. Usa el
   **Session pooler**, no la conexión directa (en el plan gratuito esta es solo IPv6).

### 3. Configurar el entorno

Copia `.env.example` a `.env` y complétalo. **Nunca** lo subas al repositorio (ya está en `.gitignore`).

| Variable | Qué es |
|---|---|
| `GROQ_API_KEY` | Clave de Groq |
| `TAVILY_API_KEY` | Clave de Tavily |
| `DATABASE_URL` | Cadena del Session pooler con el usuario `asistente_bot.<project-ref>` |
| `DISCORD_TOKEN` | Pestaña **Bot** del portal de Discord → *Reset Token* (no la Public Key) |
| `DISCORD_OWNER_ID` | Tu ID numérico de usuario |
| `DISCORD_GUILD_ID` | ID numérico de tu servidor |
| `DISCORD_CHANNEL_IDS` | IDs numéricos de los canales permitidos, separados por coma |
| `DISCORD_CANAL_AVISOS_ID` | Canal (uno de los anteriores) donde el heartbeat publica avisos. Sin él, no avisa |
| `DISCORD_CANAL_VIGIA_ID` | Canal (uno de los anteriores) para el vigía de temas (`#vigia-temas`). Sin él, el vigía no corre |
| `VIGIA_INTERVALO_S`, `VIGIA_MAX_BUSQUEDAS_DIA` | Opcionales: cada cuánto revisa qué temas tocan (600) y tope diario de búsquedas en Tavily (30) |
| `TIMEZONE` | Zona horaria IANA (por defecto `America/Santiago`) |
| `HEARTBEAT_INTERVALO_S`, `AVISO_HORA_INICIO`, `AVISO_HORA_FIN` | Opcionales: cada cuántos segundos late (60), y horario diurno de avisos de procesos (8 a 21) |

Los IDs de Discord se copian con el **Modo desarrollador** activado (Ajustes → Avanzado) y clic
derecho → *Copiar ID*.

### 4. Verificar

```powershell
# Comprueba conexión y permisos del rol contra tu base real (no deja datos)
$env:PYTHONPATH = "src"; python scripts/check_db.py

# Tests (los de base de datos usan tu Supabase con rollback; sin .env se omiten)
pytest

# Lint
ruff check .
```

`check_db.py` debe terminar en `Todo bien`. Confirma, entre otras cosas, que el rol puede escribir en
`procesos` pero **no** modificar `auditoria` ni hacer `TRUNCATE`.

### 5. Probar el agente de verdad

```powershell
# Conversación real con Groq contra tu base (todo se revierte; consume ~30k tokens de tu cuota)
$env:PYTHONPATH = "src"; $env:PYTHONIOENCODING = "utf-8"; python scripts/check_agent.py
```

Envía cinco mensajes (crear un proceso, actualizarlo con un recordatorio, listar pendientes, guardar
una preferencia y un intento de sacarle las claves) y muestra qué tools usó el agente y qué quedó en
la base antes de revertir. Sirve para detectar regresiones al cambiar prompts, skills o tools.

### 6. Verificar Discord y arrancar el asistente

En el [portal de desarrolladores](https://discord.com/developers/applications) → tu app → **Bot**:
activa **Message Content Intent** y desactiva **Public Bot**. Invita el bot con solo los permisos
*Ver canales*, *Enviar mensajes*, *Insertar enlaces* y *Leer el historial* (nunca Administrador).

```powershell
# Comprueba token, intent, presencia en tu servidor y permisos por canal (no publica nada)
$env:PYTHONPATH = "src"; python scripts/check_discord.py

# Arranca el asistente (queda corriendo; Ctrl+C para detenerlo)
python -m asistente
```

Escríbele en cualquiera de los canales permitidos. Comandos (solo tú): `/pausa`, `/reanudar`,
`/estado`, `/uso` (tokens de hoy) y `/vigia` (revisa los temas ahora).

- El bot **ignora sin responder** a cualquier otro usuario, servidor, canal o mensaje directo; ni
  siquiera muestra "escribiendo…".
- Sus respuestas **no pueden mencionar** a nadie (`@everyone`, roles ni usuarios).
- Atiende **un mensaje a la vez** y recuerda los últimos 3 intercambios de cada canal, solo en memoria
  (se pierde al reiniciar; lo importante queda en la base a través de las tools).
- Mientras el proceso esté apagado, no responde ni avisa: corre en tu PC hasta que lo despleguemos.

### Heartbeat: avisos proactivos

Cada 60 segundos el bot revisa la base y publica en `DISCORD_CANAL_AVISOS_ID`. **No usa el LLM**:
son consultas SQL y mensajes con plantilla, así que no gasta cuota de Groq ni puede ser manipulado.

| Qué avisa | Cuándo |
|---|---|
| Recordatorios | A su hora exacta (también de madrugada). Si llegan tarde, indica para cuándo eran |
| Próxima acción con fecha de un proceso | 24 h antes, 2 h antes y una vez cuando ya pasó |
| Fecha límite de un proceso | 3 días antes, 1 día antes, el mismo día y cuando venció |
| Chequeo periódico | Cada `frecuencia_chequeo_dias` días, para los procesos que la tengan definida |

- Los avisos de procesos solo salen en horario diurno (por defecto 08:00–21:00 hora local).
- Cada aviso se envía **una sola vez**: queda registrado en `auditoria` y esa misma marca evita
  repetirlo. Se registra solo si Discord confirmó el envío; si falla, se reintenta al ciclo siguiente.
- Con `/pausa` no envía nada. Los procesos completados o cancelados no generan avisos.

### Agenda: eventos recurrentes y "qué tengo hoy"

Le dices, por ejemplo, *"los lunes tengo clases de Matemática a las 20:30"* y crea un **evento semanal**:
"todos los lunes" son todos los lunes. `listar_agenda` responde "qué tengo hoy / mañana / esta semana"
(hasta 14 días) juntando eventos, recordatorios, próximas acciones y fechas límite de tus procesos,
ordenados por hora y con los feriados marcados.

- **Feriados de Chile** (librería `holidays`): por defecto un evento **se suspende en feriados**. Al
  crearlo el asistente te muestra las próximas fechas, incluidas las suspendidas ("lunes 12/10 20:30
  SUSPENDIDA, feriado: Día del Encuentro de Dos Mundos"). Se puede desactivar por evento.
- **Excepciones por fecha**: `omitir_fecha` ("ese lunes no hay clases") o `mantener_fecha` ("ese lunes sí
  hay, aunque sea feriado"). También vigencia opcional (inicio y fin de semestre).
- **Avisos** (heartbeat, sin gastar tokens): 60 minutos antes de cada evento (configurable, o ninguno),
  a su hora aunque sea de madrugada, y por la mañana te cuenta si algo de hoy queda suspendido.
- Límites: los feriados son los legales; no incluyen decretos de última hora ni los recesos de tu
  universidad o trabajo, para eso están las excepciones. Los eventos se pausan (`activo: false`), no se borran.

### Instancia única y avisos de estado

Solo **un** proceso del asistente puede estar activo a la vez (un bloqueo de sesión en Postgres). Si
arrancas uno mientras hay otro corriendo (tu PC y un servidor, por ejemplo), el nuevo **se queda en
espera** sin conectarse a Discord y toma el control solo si el primero cae, en menos de 30 s. Si un
proceso pierde el bloqueo (corte de la conexión a la base), se detiene con error para que su supervisor
lo reinicie: es preferible caer a responder por duplicado.

- Requiere una conexión de **sesión** a la base (el Session pooler de Supabase lo es).
- Al arrancar y al apagarse con normalidad, el bot publica un aviso en `#avisos` con el nombre del
  equipo (`Asistente en línea en <equipo>`). Una caída brusca no puede avisar; se nota porque el aviso
  de arranque aparece de nuevo cuando el supervisor lo reinicia.
- Para **actualizar** el bot en un servidor sin duplicar, basta con arrancar la versión nueva: espera
  a que termine la vieja.

### Consumo y límites de Groq

El plan gratuito de Groq limita **cada modelo** a **8.000 tokens por minuto y 200.000 por día** (mira
los tuyos en `console.groq.com/settings/limits`). Un mensaje de chat gasta unos 6.000 a 9.000 tokens
(2 o 3 llamadas al modelo, ~2.900 tokens fijos cada una), así que el agente en `gpt-oss-120b` alcanza
para unas 25 a 35 conversaciones al día.

- `/uso` muestra lo consumido hoy según los registros del asistente (chat y vigía, por separado).
- **Respaldo entre modelos:** si el principal agota su cupo (`MODELO_AGENTE`), el chat pasa solo al
  `MODELO_RESPALDO` (por defecto `gpt-oss-20b`, otro cupo de 200K) y avisa en el mensaje que respondió
  el de respaldo, porque es menos fiable. Cuando Groq pide esperar más de 30 s (cupo diario) no se
  pierde tiempo reintentando. Solo Groq: no se usan otros proveedores.
- Menos llamadas por mensaje: los procesos y temas se indican por **nombre** (sin buscar antes), las
  tools detectan duplicados por su cuenta y `actualizar_proceso` acepta una `nota` en la misma llamada.
- El vigía usa el modelo chico y deja registrados sus tokens en cada corrida.
- **Herramientas bajo demanda:** las del vigía (y sus instrucciones) no viajan en cada llamada al
  modelo: `habilitar_vigia` las carga solo cuando hablas de temas. Ahorra ~4.000 caracteres fijos por
  llamada a costa de una llamada extra cuando las necesitas.

### Vigía de temas

Le pides por chat, por ejemplo: *"vigílame las novedades de inteligencia artificial en salud, todos
los días a las 8"*. El agente crea el tema (`crear_tema`, `listar_temas`, `actualizar_tema`) y el
vigía publica en `#vigia-temas` un embed por artículo: **título, resumen y link a la fuente**.
`/vigia` revisa todos los temas activos ahora, sin esperar su horario.

- **Frecuencia** por tema: `diaria`, `cada_x_dias` (con `intervalo_dias`) o `dias_especificos` (con
  `dias_semana`, 1 = lunes a 7 = domingo), a su `hora_preferida`, como máximo una vez al día.
- **Sin repetidos**: las URLs se normalizan (sin `utm_*`, `www.`, fragmentos ni http/https) y cada
  artículo se marca como visto **solo después de publicarse**. Si algo falla a medias (Tavily, Groq o
  Discord), el tema se reintenta y lo ya publicado no se repite.
- **Resúmenes parafraseados, nunca copiados**: además de pedirlo en el prompt, el código rechaza
  cualquier resumen que comparta 8 palabras seguidas con el original; reintenta indicándole la frase
  que copió y, si vuelve a fallar, publica solo título y link. Se resume el **extracto** que entrega
  Tavily (~1.000 caracteres), no el artículo completo.
- **El contenido web se trata como no confiable**: lo resume una llamada aparte, sin tools y sin acceso
  a tus datos, que solo devuelve `{"resumen": ...}`. **El link nunca lo produce el modelo**: sale del
  buscador y se valida (http/https público, sin credenciales ni IPs privadas). A título y resumen se
  les quitan enlaces, markdown y menciones. El agente de chat **nunca ve** el contenido de los artículos.
- **Presupuesto**: máximo de búsquedas por día (30 por defecto) para cuidar tu cupo mensual de Tavily,
  y respeta `/pausa`.

## Estructura

```
├── supabase/migrations/     # SQL versionado: esquema y rol de mínimos privilegios
├── workspace/               # comportamiento del agente en markdown (solo lectura para él)
│   ├── SOUL.md              # personalidad y tono
│   ├── AGENTS.md            # reglas de operación, autoridad y honestidad
│   └── skills/              # instrucciones por dominio (procesos.md)
├── scripts/
│   ├── check_db.py          # verificación de conexión y permisos
│   ├── check_agent.py       # conversación real con Groq + base (se revierte)
│   └── check_discord.py     # token, intents y permisos del bot (no publica nada)
├── src/asistente/
│   ├── config.py            # configuración con Pydantic (secretos ocultos)
│   ├── security/            # allowlist, registro de tools, aprobaciones
│   ├── llm/                 # interfaz LLM, cliente de Groq, respaldo entre modelos y contador de tokens
│   ├── agent/               # tools, bucle de tool calling, prompt y servicio por mensaje
│   ├── discord_bot/         # bot, despachador con allowlist, historial, comandos de pausa
│   ├── heartbeat/           # reglas de avisos (SQL + plantillas) y ciclo de envío
│   ├── vigia/               # búsqueda (Tavily), calendario, URLs, resumidor aislado y ciclo
│   ├── agenda/              # feriados, ocurrencias de eventos (suspensión, excepciones) y consulta
│   ├── gateway.py           # arranque: `python -m asistente`
│   └── db/                  # conexión, modelos y repositorios
│       └── repos/           # procesos, memorias, recordatorios, auditoría, kill switch
└── tests/                   # unitarios (LLM simulado) y de integración contra la base
```

## Roadmap

- [x] Núcleo de seguridad: configuración, allowlist, registro de tools, aprobaciones
- [x] Esquema de datos y rol de mínimos privilegios
- [x] Repositorios de datos (procesos, memorias, recordatorios, auditoría, kill switch)
- [x] Capa del LLM (Groq) y tools del agente registradas con su nivel de autoridad
- [x] Bot de Discord con allowlist, memoria conversacional en RAM y `/pausa` `/reanudar` `/estado`
- [ ] Persistencia de aprobaciones (`acciones_pendientes`) y botones Aprobar/Rechazar en Discord
- [x] Heartbeat: chequeo proactivo de procesos y recordatorios
- [x] Vigía de temas (Tavily) con resumen parafraseado, link y deduplicación
- [x] Agenda: eventos recurrentes con feriados, excepciones por fecha, `listar_agenda` y avisos previos
- [ ] Brief matutino en `#brief` con la agenda del día
- [ ] Google Calendar / Gmail, Microsoft Graph
- [ ] Acciones acotadas con guardrails (whitelist, modo "propone, no ejecuta", auditoría)
