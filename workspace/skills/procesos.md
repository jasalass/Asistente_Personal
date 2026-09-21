# Skill: procesos

Un proceso es algo en curso que el usuario no anota en otro lado (trámite, compra, proyecto, gestión).

- Crea uno cuando describe algo con varios pasos o que depende de otros. `crear_proceso` falla si ya hay uno abierto con ese nombre.
- Estados: `idea`, `activo`, `en_espera` (depende de alguien o algo), `bloqueado` (hay un problema: explícalo en `bloqueo_detalle`), `completado`, `cancelado`. Nunca se borran.
- `esperando_a`: de quién o qué depende. `proxima_accion`: el siguiente paso, en una frase. `prioridad`: `media` salvo urgencia o poca importancia. `frecuencia_chequeo_dias`: solo si pide que le recuerdes revisarlo.
- Para modificar usa `actualizar_proceso` con el nombre en `proceso` (no hace falta buscar antes) y solo los campos que cambian. Si además cuenta algo que pasó, ponlo en su campo `nota` (una sola llamada). `agregar_nota_proceso` es para notas sueltas o para marcar una `accion_hecha`.
- **Recordatorios:** si pide que le avises en una fecha, usa siempre `crear_recordatorio` con la fecha y hora exactas. Escribirlo en `proxima_accion` o en una nota no avisa a nadie. Si además cambia el proceso, son dos herramientas.
- Preferencias o datos sueltos van a `guardar_memoria`; lo que tiene estado y próximos pasos, a un proceso.
