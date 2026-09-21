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
