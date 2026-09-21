from datetime import datetime
from zoneinfo import ZoneInfo

from asistente.db.models import Frecuencia, Tema


def toca(tema: Tema, ahora: datetime, tz: ZoneInfo) -> bool:
    """¿Le corresponde correr hoy a este tema? Como máximo una vez por día local."""
    if not tema.activo:
        return False
    local = ahora.astimezone(tz)
    if local.time() < tema.hora_preferida:
        return False
    hoy = local.date()
    ultima = tema.ultima_ejecucion.astimezone(tz).date() if tema.ultima_ejecucion else None
    if ultima is not None and ultima >= hoy:
        return False

    if tema.frecuencia is Frecuencia.DIARIA:
        return True
    if tema.frecuencia is Frecuencia.CADA_X_DIAS:
        return ultima is None or (hoy - ultima).days >= (tema.intervalo_dias or 1)
    return local.isoweekday() in (tema.dias_semana or [])  # dias_especificos
