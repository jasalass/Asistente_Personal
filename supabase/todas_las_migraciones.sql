-- ===== 0001_nucleo.sql =====
-- Núcleo: procesos, memoria, aprobaciones, auditoría y estado del sistema.
-- RLS activado sin políticas: solo accede el backend con la service key.

create extension if not exists "pgcrypto";

-- ---------- enums ----------
create type proceso_estado as enum
  ('idea', 'activo', 'en_espera', 'bloqueado', 'completado', 'cancelado');
create type proceso_prioridad as enum ('baja', 'media', 'alta');
create type evento_tipo as enum ('nota', 'cambio_estado', 'accion_hecha', 'chequeo_agente');
create type memoria_origen as enum ('usuario', 'agente', 'externo');
create type accion_estado as enum
  ('pendiente', 'aprobada', 'rechazada', 'expirada', 'ejecutada');

-- ---------- utilidades ----------
create or replace function set_actualizado_en() returns trigger as $$
begin
  new.actualizado_en = now();
  return new;
end;
$$ language plpgsql;

create or replace function bloquear_modificacion() returns trigger as $$
begin
  raise exception 'La tabla % es append-only', tg_table_name;
end;
$$ language plpgsql;

-- ---------- procesos ----------
create table procesos (
  id                      uuid primary key default gen_random_uuid(),
  nombre                  text not null,
  descripcion             text,
  estado                  proceso_estado not null default 'activo',
  prioridad               proceso_prioridad not null default 'media',
  proxima_accion          text,
  proxima_accion_fecha    timestamptz,
  esperando_a             text,
  bloqueo_detalle         text,
  fecha_limite            date,
  etiquetas               text[] not null default '{}',
  frecuencia_chequeo_dias int check (frecuencia_chequeo_dias is null or frecuencia_chequeo_dias > 0),
  ultimo_chequeo          timestamptz,
  creado_en               timestamptz not null default now(),
  actualizado_en          timestamptz not null default now()
);
create trigger procesos_actualizado before update on procesos
  for each row execute function set_actualizado_en();
create index procesos_estado_idx on procesos (estado);

create table proceso_eventos (
  id          uuid primary key default gen_random_uuid(),
  proceso_id  uuid not null references procesos(id) on delete cascade,
  tipo        evento_tipo not null,
  contenido   text not null,
  creado_en   timestamptz not null default now()
);
create index proceso_eventos_proceso_idx on proceso_eventos (proceso_id, creado_en desc);

-- ---------- memorias (notas libres con búsqueda) ----------
create table memorias (
  id          uuid primary key default gen_random_uuid(),
  contenido   text not null,
  origen      memoria_origen not null,
  etiquetas   text[] not null default '{}',
  busqueda    tsvector generated always as (to_tsvector('spanish', contenido)) stored,
  creado_en   timestamptz not null default now()
);
create index memorias_busqueda_idx on memorias using gin (busqueda);

-- ---------- recordatorios ----------
create table recordatorios (
  id          uuid primary key default gen_random_uuid(),
  texto       text not null,
  fecha       timestamptz not null,
  enviado     boolean not null default false,
  creado_en   timestamptz not null default now()
);
create index recordatorios_pendientes_idx on recordatorios (fecha) where not enviado;

-- ---------- aprobaciones ----------
create table acciones_pendientes (
  id            uuid primary key default gen_random_uuid(),
  tool          text not null,
  args          jsonb not null,
  payload_hash  text not null,
  estado        accion_estado not null default 'pendiente',
  creada_en     timestamptz not null default now(),
  expira_en     timestamptz not null default (now() + interval '24 hours'),
  resuelta_en   timestamptz,
  resuelto_por  text
);
create index acciones_pendientes_estado_idx on acciones_pendientes (estado, expira_en);

-- ---------- observabilidad y auditoría (append-only) ----------
create table ejecuciones (
  id           bigint generated always as identity primary key,
  tipo         text not null,               -- mensaje | heartbeat | vigia
  inicio       timestamptz not null default now(),
  duracion_ms  int,
  tokens_in    int,
  tokens_out   int,
  error        text,
  detalle      jsonb
);

create table auditoria (
  id       bigint generated always as identity primary key,
  ts       timestamptz not null default now(),
  actor    text not null,                   -- owner | agente | heartbeat
  accion   text not null,
  detalle  jsonb
);

create trigger ejecuciones_append_only before update or delete on ejecuciones
  for each row execute function bloquear_modificacion();
create trigger auditoria_append_only before update or delete on auditoria
  for each row execute function bloquear_modificacion();

-- ---------- estado del sistema (kill switch) ----------
create table estado_sistema (
  id              boolean primary key default true check (id),  -- fila única
  pausado         boolean not null default false,
  actualizado_en  timestamptz not null default now()
);
insert into estado_sistema (pausado) values (false);
create trigger estado_sistema_actualizado before update on estado_sistema
  for each row execute function set_actualizado_en();

-- ---------- RLS ----------
alter table procesos            enable row level security;
alter table proceso_eventos     enable row level security;
alter table memorias            enable row level security;
alter table recordatorios       enable row level security;
alter table acciones_pendientes enable row level security;
alter table ejecuciones         enable row level security;
alter table auditoria           enable row level security;
alter table estado_sistema      enable row level security;

-- ===== 0002_rol_bot.sql =====
-- Rol de mínimos privilegios para el bot (conexión directa a Postgres, sin Data API).
-- La contraseña NO va en este archivo. Después de aplicar la migración, ejecutar aparte:
--   alter role asistente_bot with password '<contraseña larga>';

create role asistente_bot login nosuperuser nocreatedb nocreaterole noinherit;
grant usage on schema public to asistente_bot;

-- Tablas de datos de trabajo: lectura y escritura completas (sin TRUNCATE).
grant select, insert, update, delete on
  procesos, proceso_eventos, memorias, recordatorios, acciones_pendientes
  to asistente_bot;

-- Kill switch: se lee y se actualiza, pero no se borra ni se agregan filas.
grant select, update on estado_sistema to asistente_bot;

-- Auditoría y observabilidad: append-only también a nivel de permisos.
grant select, insert on ejecuciones, auditoria to asistente_bot;
grant usage, select on sequence ejecuciones_id_seq, auditoria_id_seq to asistente_bot;

-- RLS está activado sin políticas; el rol necesita políticas explícitas y acotadas.
create policy bot_all on procesos            for all to asistente_bot using (true) with check (true);
create policy bot_all on proceso_eventos     for all to asistente_bot using (true) with check (true);
create policy bot_all on memorias            for all to asistente_bot using (true) with check (true);
create policy bot_all on recordatorios       for all to asistente_bot using (true) with check (true);
create policy bot_all on acciones_pendientes for all to asistente_bot using (true) with check (true);

create policy bot_select on estado_sistema for select to asistente_bot using (true);
create policy bot_update on estado_sistema for update to asistente_bot using (true) with check (true);

create policy bot_select on ejecuciones for select to asistente_bot using (true);
create policy bot_insert on ejecuciones for insert to asistente_bot with check (true);
create policy bot_select on auditoria   for select to asistente_bot using (true);
create policy bot_insert on auditoria   for insert to asistente_bot with check (true);

-- ===== 0003_vigia.sql =====
-- Vigía de temas: seguimiento periódico de temas con deduplicación de artículos.

create type tema_tipo_contenido as enum ('noticias', 'papers', 'blogs', 'mixto');
create type tema_frecuencia as enum ('diaria', 'cada_x_dias', 'dias_especificos');

create table temas_seguimiento (
  id                      uuid primary key default gen_random_uuid(),
  nombre                  text not null,
  query_busqueda          text not null,
  tipo_contenido          tema_tipo_contenido not null default 'mixto',
  frecuencia              tema_frecuencia not null default 'diaria',
  intervalo_dias          int check (intervalo_dias is null or intervalo_dias > 0),
  dias_semana             int[] check (dias_semana is null or dias_semana <@ array[1,2,3,4,5,6,7]),
  hora_preferida          time not null default '08:00',
  ventana_frescura_horas  int not null default 48 check (ventana_frescura_horas > 0),
  cantidad_resultados     int not null default 5 check (cantidad_resultados between 1 and 10),
  avisar_sin_novedades    boolean not null default false,
  activo                  boolean not null default true,
  ultima_ejecucion        timestamptz,
  creado_en               timestamptz not null default now(),
  actualizado_en          timestamptz not null default now(),
  constraint frecuencia_coherente check (
    frecuencia = 'diaria'
    or (frecuencia = 'cada_x_dias' and intervalo_dias is not null)
    or (frecuencia = 'dias_especificos' and dias_semana is not null and cardinality(dias_semana) > 0)
  )
);
comment on column temas_seguimiento.dias_semana is 'ISO: 1 = lunes ... 7 = domingo';
create trigger temas_actualizado before update on temas_seguimiento
  for each row execute function set_actualizado_en();

create table articulos_vistos (
  id                uuid primary key default gen_random_uuid(),
  tema_id           uuid not null references temas_seguimiento(id) on delete cascade,
  url               text not null,               -- URL normalizada (sin tracking ni fragmento)
  titulo            text not null,
  fecha_encontrado  timestamptz not null default now(),
  unique (tema_id, url)
);
create index articulos_vistos_tema_idx on articulos_vistos (tema_id, fecha_encontrado desc);

-- RLS activado y permisos mínimos para el rol del bot.
alter table temas_seguimiento enable row level security;
alter table articulos_vistos  enable row level security;

-- Los temas se desactivan (activo = false), no se borran; los artículos vistos son de solo alta.
grant select, insert, update on temas_seguimiento to asistente_bot;
grant select, insert on articulos_vistos to asistente_bot;

create policy bot_select on temas_seguimiento for select to asistente_bot using (true);
create policy bot_insert on temas_seguimiento for insert to asistente_bot with check (true);
create policy bot_update on temas_seguimiento for update to asistente_bot using (true) with check (true);
create policy bot_select on articulos_vistos for select to asistente_bot using (true);
create policy bot_insert on articulos_vistos for insert to asistente_bot with check (true);

-- ===== 0004_agenda.sql =====
-- Agenda: eventos recurrentes semanales (clases, reuniones fijas) con excepciones por fecha.
-- Los feriados no se guardan: se calculan al consultar, y cada evento decide si se suspende en ellos.

create type excepcion_accion as enum ('omitir', 'mantener');

create table eventos_agenda (
  id                  uuid primary key default gen_random_uuid(),
  nombre              text not null,
  descripcion         text,                                  -- lugar, sala, enlace...
  dias_semana         int[] not null
                      check (cardinality(dias_semana) > 0 and dias_semana <@ array[1,2,3,4,5,6,7]),
  hora                time not null,                         -- hora local del usuario
  duracion_min        int check (duracion_min is null or duracion_min between 1 and 1440),
  aviso_min_antes     int check (aviso_min_antes is null or aviso_min_antes between 0 and 1440)
                      default 60,                            -- null o 0 = sin aviso
  suspender_feriados  boolean not null default true,
  vigente_desde       date,
  vigente_hasta       date,
  activo              boolean not null default true,
  creado_en           timestamptz not null default now(),
  actualizado_en      timestamptz not null default now(),
  constraint vigencia_coherente check (
    vigente_desde is null or vigente_hasta is null or vigente_hasta >= vigente_desde
  )
);
comment on column eventos_agenda.dias_semana is 'ISO: 1 = lunes ... 7 = domingo';
create trigger eventos_agenda_actualizado before update on eventos_agenda
  for each row execute function set_actualizado_en();
create index eventos_agenda_activos_idx on eventos_agenda (activo);

-- Una excepción cambia lo que pasa en UNA fecha: omitir la ocurrencia o mantenerla aunque sea feriado.
create table excepciones_agenda (
  id          uuid primary key default gen_random_uuid(),
  evento_id   uuid not null references eventos_agenda(id) on delete cascade,
  fecha       date not null,
  accion      excepcion_accion not null,
  motivo      text,
  creado_en   timestamptz not null default now(),
  unique (evento_id, fecha)
);

-- RLS activado y permisos mínimos para el rol del bot: los eventos se desactivan, no se borran.
alter table eventos_agenda      enable row level security;
alter table excepciones_agenda  enable row level security;

grant select, insert, update on eventos_agenda to asistente_bot;
grant select, insert, update on excepciones_agenda to asistente_bot;

create policy bot_select on eventos_agenda for select to asistente_bot using (true);
create policy bot_insert on eventos_agenda for insert to asistente_bot with check (true);
create policy bot_update on eventos_agenda for update to asistente_bot using (true) with check (true);
create policy bot_select on excepciones_agenda for select to asistente_bot using (true);
create policy bot_insert on excepciones_agenda for insert to asistente_bot with check (true);
create policy bot_update on excepciones_agenda for update to asistente_bot using (true) with check (true);

-- ===== 0005_trazas.sql =====
-- Traza paso a paso de una ejecución del agente: cada llamada al LLM y cada tool que corrió,
-- en orden, con su duración y su resultado. Append-only, como ejecuciones y auditoria.
-- Se correlaciona con ejecuciones a través de detalle->>'traza' (un uuid), no de una FK: la fila
-- de ejecuciones se escribe recién al final del mensaje, y los pasos se van insertando durante.

create table traza_pasos (
  id           bigint generated always as identity primary key,
  traza_id     uuid not null,
  orden        int not null,
  ts           timestamptz not null default clock_timestamp(),
  tipo         text not null check (tipo in ('llm', 'tool', 'atajo')),
  nombre       text not null,               -- modelo (llm) o nombre de la tool
  duracion_ms  int,
  tokens_in    int,
  tokens_out   int,
  entrada      jsonb,                       -- argumentos o resumen de lo que se envió, recortado
  salida       jsonb,                       -- resultado o resumen, recortado
  error        text
);
create index traza_pasos_traza_idx on traza_pasos (traza_id, orden);

create trigger traza_pasos_append_only before update or delete on traza_pasos
  for each row execute function bloquear_modificacion();

alter table traza_pasos enable row level security;

grant select, insert on traza_pasos to asistente_bot;
grant usage, select on sequence traza_pasos_id_seq to asistente_bot;

create policy bot_select on traza_pasos for select to asistente_bot using (true);
create policy bot_insert on traza_pasos for insert to asistente_bot with check (true);

-- ===== 0006_aprobaciones.sql =====
-- Activa el pilar "propone y espera OK": persistencia real de acciones_pendientes (ya existía la
-- tabla desde 0001, pero nada la usaba) y niveles de autoridad configurables por el dueño desde el
-- chat, sin tocar código. El nivel que trae el código es siempre el techo: una tool prohibida no se
-- puede volver auto/propone por configuración (eso solo lo cambia un despliegue nuevo).

alter table acciones_pendientes add column resultado jsonb;

create table tool_niveles (
  tool           text primary key,
  nivel          text not null check (nivel in ('auto', 'propone')),
  actualizado_en timestamptz not null default now()
);
create trigger tool_niveles_actualizado before update on tool_niveles
  for each row execute function set_actualizado_en();

alter table tool_niveles enable row level security;

grant select, insert, update, delete on tool_niveles to asistente_bot;

create policy bot_all on tool_niveles for all to asistente_bot using (true) with check (true);

