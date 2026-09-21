# Skill: procesos

Un **proceso** es algo que el usuario lleva en el tiempo y que hoy no anota en ningún lado
(un trámite, una compra grande, un proyecto, una gestión con alguien).

## Cuándo crear uno
Cuando el usuario describe algo en curso o por hacer que tiene varios pasos o depende de otros.
Antes de crearlo, busca por nombre con `buscar_procesos` para no duplicar.

## Estados
- `idea`: aún no empieza. `activo`: en marcha. `en_espera`: depende de alguien o algo externo.
- `bloqueado`: no puede avanzar y hay un problema (explícalo en `bloqueo_detalle`).
- `completado` / `cancelado`: cerrado. Nunca se borra; queda el historial.

## Cómo llenar los campos
- `esperando_a`: de quién o de qué depende ("Registro Civil", "respuesta del banco").
- `proxima_accion`: el siguiente paso concreto, en una frase.
- `prioridad`: `media` salvo que el usuario indique urgencia o poca importancia.
- `frecuencia_chequeo_dias`: solo si el usuario pide que le recuerdes revisarlo cada cierto tiempo.

## Al actualizar
1. `buscar_procesos` para obtener el id.
2. `actualizar_proceso` solo con los campos que cambian.
3. Si el usuario cuenta algo que pasó, agrégalo con `agregar_nota_proceso`
   (`accion_hecha` si es algo que ya se hizo).
4. Para borrar un campo de texto, envía cadena vacía.

## Recordatorios
Cuando el usuario pida que le avises o recuerdes algo en una fecha, **siempre** usa
`crear_recordatorio` con la fecha y hora exactas. No lo escribas dentro de `proxima_accion` ni de una
nota: un texto no avisa a nadie. Si además cambia el proceso, son dos herramientas distintas.
`proxima_accion` describe el paso a seguir, no el aviso.

## Memorias vs procesos
Preferencias, datos personales o contexto suelto van a `guardar_memoria`. Lo que tiene estado y
próximos pasos va a un proceso.
