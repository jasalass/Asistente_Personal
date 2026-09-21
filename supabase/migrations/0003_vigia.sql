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
