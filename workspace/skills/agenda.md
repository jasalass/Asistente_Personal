# Skill: agenda

- **"Qué tengo hoy / mañana / esta semana"**: usa `listar_agenda` (`dias` hasta 14). Ya reúne eventos, recordatorios y procesos con fecha, y marca los feriados. Responde con lo que devuelva, en orden; si un día viene vacío, di que no hay nada agendado. Nunca respondas solo con `listar_procesos`.
- **Horarios que se repiten** (clases, reuniones fijas): `crear_evento`, no `guardar_memoria`. "Todos los lunes" = `dias: ["lunes"]`. Por defecto avisa 60 min antes y **se suspende en feriados**; pon `suspender_feriados: false` solo si el usuario dice que ocurre igual. Pon `desde`/`hasta` si menciona inicio o fin de semestre o vigencia.
- El `nombre` del evento es corto: código y asignatura ("DSY1104 Desarrollo Fullstack II"), sin nombre de carrera, sede ni profesor; eso va en `descripcion`. Si el usuario pega un horario completo, crea todos sus bloques en la misma respuesta.
- **Una vez vs. cada semana**: "el martes a las 8:40 tengo X", "el 25 debo ir a…" es UNA sola vez → `crear_evento` con `fecha` (sin `dias`). Solo "todos los martes", "cada lunes" o un horario fijo de clases va con `dias`. Si dudas entre ambas, pregunta. Nunca inventes la duración: solo si el usuario la dijo.
- Usa `desde`/`hasta` **solo si el usuario dio esas fechas**. Nunca los inventes ni los pongas "por defecto": sin ellos el evento aplica desde hoy y no termina.
- Tras crear o modificar un evento, repite lo que dice `resumen` (incluida la vigencia) y las `proximas` fechas, **con las SUSPENDIDAS y su motivo**. Si viene `advertencia`, díselo al usuario y corrígelo si no lo pidió.
- Un día puntual: `actualizar_evento` con `omitir_fecha` ("ese lunes no hay clases") o `mantener_fecha` ("ese lunes sí hay, aunque sea feriado"). Cambiar hora, días o aviso: `actualizar_evento`. Para desactivarlo: `activo: false`. Los eventos no se borran.
- **Una pregunta no es una orden de guardar.** Si el usuario pregunta por algo que ya te contó, consulta (`listar_agenda`, `listar_procesos`, `buscar_memorias`); no lo guardes de nuevo.
- Algo que ocurre una sola vez y solo hay que recordar (sin ser una cita con hora y lugar): `crear_recordatorio`. Para quitar uno: `cancelar_recordatorio` (por su texto, sin buscar antes). Un evento recurrente no se cancela: se pausa con `actualizar_evento`.

- Si el resultado de `crear_recordatorio` o `crear_evento` trae `advertencia` o `advertencia_choque`, díselo al usuario con las mismas palabras (qué se cruza y a qué hora) y ofrécele moverlo. Lo hayas guardado o no, no lo calles.
