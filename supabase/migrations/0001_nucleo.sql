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
