# Skill: vigía de temas

El **vigía** busca novedades en la web sobre los temas que el usuario quiere seguir y las publica
solo, en `#vigia-temas`, con un resumen y el enlace a la fuente. Tú no lees ni resumes esos
artículos: solo administras la lista de temas.

## Cuándo crear un tema
Cuando el usuario pide "vigila", "sígueme", "avísame de novedades sobre..." algo. Antes, revisa con
`listar_temas` que no exista ya uno parecido.

## Cómo llenar los campos
- `nombre`: corto, para que el usuario lo reconozca ("IA en salud").
- `query_busqueda`: la consulta de búsqueda, concreta y en el idioma de las fuentes que le interesan.
- `tipo_contenido`: `noticias`, `papers`, `blogs` o `mixto` (por defecto).
- `frecuencia`: `diaria` por defecto. `cada_x_dias` exige `intervalo_dias`; `dias_especificos` exige
  `dias_semana` (1 = lunes ... 7 = domingo). No inventes una frecuencia: si el usuario no la dijo, usa
  la diaria.
- `hora_preferida`: formato `HH:MM`, hora local. Por defecto 08:00.
- `ventana_frescura_horas`: cuán reciente debe ser lo que se publique (48 por defecto).
- `cantidad_resultados`: máximo de artículos por revisión (1 a 10, 5 por defecto).
- `avisar_sin_novedades`: solo `true` si el usuario quiere que le avisen cuando no hay nada nuevo.

## Al modificar o pausar
Usa `listar_temas` para obtener el id y luego `actualizar_tema`. Para pausar un tema usa
`activo=false`; para reactivarlo, `activo=true`. Los temas no se borran.

## Después de crear un tema
Dile al usuario que el vigía publicará en `#vigia-temas` y cuándo será la primera revisión. No
prometas contenido concreto: no sabes qué van a encontrar.
