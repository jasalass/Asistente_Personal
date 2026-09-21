"""Instancia única: solo un proceso del asistente puede estar activo a la vez.

Si corrieran dos (por ejemplo tu PC y un servidor), ambos responderían cada mensaje y ambos
enviarían los avisos. Se usa un bloqueo de sesión de Postgres (advisory lock): vive mientras dure la
conexión, así que si el proceso muere el bloqueo se libera solo y otra instancia puede tomarlo.

Requiere una conexión de sesión (el Session pooler de Supabase lo es; el modo transacción no).
"""

import logging
import time
from collections.abc import Callable

import psycopg

log = logging.getLogger(__name__)

# Par de enteros de 32 bits que identifica este bloqueo entre todos los de la base.
CLAVE = (1095324755, 1)


class BloqueoInstancia:
    def __init__(self, dsn: str, clave: tuple[int, int] = CLAVE) -> None:
        self._dsn = dsn
        self._clave = clave
        self._conn: psycopg.Connection | None = None

    def intentar(self) -> bool:
        """Intenta tomar el bloqueo sin esperar. True si quedó tomado."""
        conn = psycopg.connect(self._dsn, autocommit=True, connect_timeout=10)
        try:
            tomado = conn.execute("select pg_try_advisory_lock(%s, %s)", self._clave).fetchone()[0]
        except psycopg.Error:
            conn.close()
            raise
        if tomado:
            self._conn = conn
        else:
            conn.close()
        return bool(tomado)

    def esperar(
        self,
        *,
        espera_s: float = 30.0,
        intentos: int | None = None,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        """Se queda en espera hasta ser la única instancia. `intentos` limita la espera (tests)."""
        n = 0
        while True:
            try:
                if self.intentar():
                    break
                motivo = "Otra instancia del asistente está activa"
            except psycopg.OperationalError:
                # Sin red o base inalcanzable (arranque sin internet, cambio de wifi): se espera.
                motivo = "Sin conexión a la base de datos"
            n += 1
            if intentos is not None and n >= intentos:
                raise TimeoutError(motivo)
            log.warning("%s: en espera (reintento en %s s)", motivo, espera_s)
            dormir(espera_s)
        log.info("Instancia única confirmada")

    def vigente(self) -> bool:
        """¿Sigue siendo nuestro el bloqueo? Falso si la conexión murió o el pooler la reinició."""
        if self._conn is None:
            return False
        try:
            fila = self._conn.execute(
                "select exists (select 1 from pg_locks where locktype = 'advisory' "
                "and classid::bigint = %s and objid::bigint = %s and objsubid = 2 "
                "and pid = pg_backend_pid())",
                self._clave,
            ).fetchone()
        except psycopg.Error:
            return False
        return bool(fila[0])

    def conservar(self) -> bool | None:
        """¿Seguimos siendo la única instancia? Distingue tres casos, que NO son lo mismo:

        - True: el bloqueo sigue siendo nuestro (o lo recuperamos tras una caída de la conexión).
        - False: otra instancia lo tiene ahora. Aquí sí hay que detenerse para no duplicar.
        - None: no se puede saber (sin red). No es motivo para apagarse: sin base tampoco se puede
          trabajar, y al volver la conexión esta misma comprobación decide.

        Un corte de internet o un cambio de wifi tumbaba el bot por confundir None con False.
        """
        if self.vigente():
            return True
        self._descartar()  # la sesión murió (Postgres ya soltó el bloqueo): se reintenta tomarlo
        try:
            recuperado = self.intentar()
        except psycopg.Error:
            return None
        if recuperado:
            log.info("Se perdió la conexión a la base y se recuperó el bloqueo de instancia única")
        return recuperado

    def _descartar(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except psycopg.Error:
                pass
            self._conn = None

    def liberar(self) -> None:
        self._descartar()  # al cerrar la sesión, Postgres suelta el bloqueo
