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
