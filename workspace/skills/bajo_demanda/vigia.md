# Skill: vigía de temas

El vigía busca novedades en la web de los temas que el usuario sigue y las publica solo en `#vigia-temas`, con resumen y link. Tú no lees esos artículos: solo administras los temas.

- Crea un tema cuando pida "vigila", "sígueme", "avísame de novedades sobre...". `crear_tema` falla si el nombre ya existe.
- `nombre`: corto y reconocible. `query_busqueda`: consulta concreta, en el idioma de las fuentes. `tipo_contenido`: `noticias`, `papers`, `blogs` o `mixto` (por defecto).
- `frecuencia`: `diaria` salvo que el usuario diga otra (no la inventes). `cada_x_dias` exige `intervalo_dias`; `dias_especificos` exige `dias_semana` (1 = lunes ... 7 = domingo). `hora_preferida`: `HH:MM` local (08:00 por defecto).
- Deja el resto por defecto salvo que el usuario lo pida: `ventana_frescura_horas` 48, `cantidad_resultados` 5, `avisar_sin_novedades` solo si lo pide expresamente.
- Para modificar o pausar usa `actualizar_tema` con el nombre en `tema`; pausa con `activo=false`. No se borran.
- Al crear, di cuándo será la primera revisión; no prometas contenido concreto.
