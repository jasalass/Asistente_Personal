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
