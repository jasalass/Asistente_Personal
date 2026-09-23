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
