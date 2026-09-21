"""Nombres de eventos cortos y legibles, sin depender de que el modelo los redacte bien.

Los horarios académicos llegan como "INGENIERÍA EN INFORMÁTICA (DESARROLLO DE SOFTWARE) DSY1104
DESARROLLO FULLSTACK II": el nombre de la carrera no es parte del nombre de la clase, y todo en
mayúsculas es ilegible. Se normaliza aquí, a la hora de guardar, para que valga siempre.
"""

import re

MAX_NOMBRE = 60

# Código de asignatura: 2 a 5 letras y 3 a 5 dígitos (DSY1104, INU3100, MAT101).
_CODIGO = re.compile(r"\b[A-ZÁÉÍÓÚÑ]{2,5}\d{3,5}\b")
_PALABRA = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+")
_ROMANO = re.compile(r"^(I{1,3}|IV|V|VI{1,3}|IX|X)$")  # niveles I a X: "Inglés Elemental II"
_MENORES = frozenset(
    {"de", "del", "la", "las", "el", "los", "en", "y", "a", "e", "o", "u", "para", "con", "por", "al"}
)
_MARGEN_ENCABEZADO = 12  # un "encabezado" antes del código debe tener algo de largo para descartarse


def _mayormente_mayusculas(texto: str) -> bool:
    letras = [c for c in texto if c.isalpha()]
    return len(letras) >= 4 and sum(c.isupper() for c in letras) / len(letras) >= 0.6


def _desde_el_codigo(nombre: str) -> str:
    """'INGENIERÍA EN INFORMÁTICA (DESARROLLO DE SOFTWARE) DSY1104 DESARROLLO...' -> 'DSY1104 DESARROLLO...'."""
    m = _CODIGO.search(nombre)
    if m and m.start() >= _MARGEN_ENCABEZADO:
        return nombre[m.start():]
    return nombre


def _a_titulo(nombre: str) -> str:
    primera = True

    def palabra(m: re.Match[str]) -> str:
        nonlocal primera
        p = m.group(0)
        es_primera, primera = primera, False
        if any(c.isdigit() for c in p):
            return p  # códigos y secciones (DSY1104, 006V) se respetan tal cual
        if _ROMANO.match(p):
            return p
        if not es_primera and p.lower() in _MENORES:
            return p.lower()
        return p[:1].upper() + p[1:].lower()

    return _PALABRA.sub(palabra, nombre)


def normalizar_nombre(nombre: str) -> str:
    """Espacios limpios, sin encabezado de carrera antes del código y en formato título."""
    nombre = re.sub(r"\s+", " ", nombre).strip(" -–—:·|")
    nombre = _desde_el_codigo(nombre)
    if _mayormente_mayusculas(nombre):
        nombre = _a_titulo(nombre)
    return nombre


def validar_nombre(nombre: str) -> str:
    """El nombre normalizado, o ValueError explicando qué corregir si sigue siendo demasiado largo."""
    limpio = normalizar_nombre(nombre)
    if not limpio:
        raise ValueError("El nombre del evento no puede estar vacío.")
    if len(limpio) > MAX_NOMBRE:
        raise ValueError(
            f"El nombre es demasiado largo ({len(limpio)} caracteres; máximo {MAX_NOMBRE}). Usa uno "
            "corto, por ejemplo 'código + asignatura'. El profesor, la sala y la sede van en "
            "'descripcion'."
        )
    return limpio
