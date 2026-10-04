-- Recordatorios que se repiten cada semana (todos los lunes, etc.). Día ISO: 1 = lunes ... 7 = domingo.
-- NULL = de una sola vez, como hasta ahora. Los permisos de la tabla ya existen desde 0002.

alter table recordatorios add column repite_dias int[];
alter table recordatorios add constraint recordatorios_repite_dias_valido check (
  repite_dias is null or (cardinality(repite_dias) between 1 and 7 and repite_dias <@ array[1,2,3,4,5,6,7])
);
