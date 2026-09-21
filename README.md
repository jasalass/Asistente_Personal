# Asistente Personal

Asistente personal multi-agente que actúa como "chief of staff": lleva el seguimiento de procesos
y proyectos, recuerda cosas que le cuentas por chat y vigila temas de interés. Proyecto personal,
de un solo usuario, inspirado en la arquitectura de [OpenClaw](https://github.com/openclaw/openclaw)
(gateway único, heartbeat proactivo, skills en markdown) pero con la seguridad como prioridad.

> **Estado: en construcción.** Hoy existen la base segura (configuración, permisos, base de datos y
> repositorios) y el **agente** (LLM en Groq + tools de procesos, memorias y recordatorios). **Todavía
> no hay bot de Discord ni heartbeat**: no hay nada que "arrancar" como servicio aún. Lo que sí puedes
> correr son las verificaciones (incluida una conversación real con el agente) y los tests.

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
| `TIMEZONE` | Zona horaria IANA (por defecto `America/Santiago`) |

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

## Estructura

```
├── supabase/migrations/     # SQL versionado: esquema y rol de mínimos privilegios
├── workspace/               # comportamiento del agente en markdown (solo lectura para él)
│   ├── SOUL.md              # personalidad y tono
│   ├── AGENTS.md            # reglas de operación, autoridad y honestidad
│   └── skills/              # instrucciones por dominio (procesos.md)
├── scripts/
│   ├── check_db.py          # verificación de conexión y permisos
│   └── check_agent.py       # conversación real con Groq + base (se revierte)
├── src/asistente/
│   ├── config.py            # configuración con Pydantic (secretos ocultos)
│   ├── security/            # allowlist, registro de tools, aprobaciones
│   ├── llm/                 # interfaz LLM y cliente de Groq (reintentos por límite de uso)
│   ├── agent/               # tools, bucle de tool calling, prompt y servicio por mensaje
│   └── db/                  # conexión, modelos y repositorios
│       └── repos/           # procesos, memorias, recordatorios, auditoría, kill switch
└── tests/                   # unitarios (LLM simulado) y de integración contra la base
```

## Roadmap

- [x] Núcleo de seguridad: configuración, allowlist, registro de tools, aprobaciones
- [x] Esquema de datos y rol de mínimos privilegios
- [x] Repositorios de datos (procesos, memorias, recordatorios, auditoría, kill switch)
- [x] Capa del LLM (Groq) y tools del agente registradas con su nivel de autoridad
- [ ] Persistencia de aprobaciones (`acciones_pendientes`) y memoria conversacional entre mensajes
- [ ] Bot de Discord (con allowlist y botones de aprobación)
- [ ] Heartbeat: chequeo proactivo de procesos y recordatorios
- [ ] Vigía de temas (Tavily) con resumen parafraseado, link y deduplicación
- [ ] Google Calendar / Gmail, Microsoft Graph
- [ ] Acciones acotadas con guardrails (whitelist, modo "propone, no ejecuta", auditoría)
