# Skill: niveles de autoridad

Cada herramienta tiene un nivel: `auto` (la ejecutas sola), `propone` (queda pendiente y el dueño
la aprueba o rechaza con un botón en Discord) o `prohibido` (nunca se ejecuta; eso no se toca desde
aquí). El dueño puede subir o bajar el nivel de cualquier herramienta que no sea `prohibido`,
diciéndotelo por chat.

- "que [herramienta] pida mi aprobación antes" → `configurar_nivel_tool` con `nivel: "propone"`.
- "que [herramienta] vuelva a ser automática" / "ya no hace falta que apruebes X" →
  `configurar_nivel_tool` con `nivel: "predeterminado"` (vuelve al nivel que trae el código, no
  necesariamente a `auto`).
- "qué herramientas piden aprobación" / "cómo están configurados los niveles" → `listar_niveles_tool`.
- Usa el nombre exacto de la herramienta tal como lo ves en tu propia lista de funciones (por
  ejemplo `cancelar_recordatorio`, no "cancelar un recordatorio"). Si no estás seguro, revisa con
  `listar_niveles_tool` antes de intentar cambiarla.
- Si el dueño pide subir de nivel algo que ya está prohibido por el sistema, dile que eso no se
  puede cambiar desde el chat: no insistas ni lo intentes con otra frase.

Cuando llamas una herramienta que está en `propone`, no se ejecuta: queda pendiente y el resultado
trae `"estado": "pendiente_de_aprobacion"`. No digas que ya está hecho — dile al usuario que quedó
esperando su aprobación en Discord (el mensaje con los botones Aprobar/Rechazar lo manda el
sistema, no tú). Si rechaza o no responde a tiempo (24 h), no se ejecuta y se avisa solo.
