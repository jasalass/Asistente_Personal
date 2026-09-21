"""Un corte de red (cambio de wifi, caída del DNS) es transitorio: no debe apagar ni ensuciar nada."""

import asyncio
import logging
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest

from asistente.heartbeat import runner as heartbeat
from asistente.vigia import runner as vigia

TZ = ZoneInfo("America/Santiago")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ejecutar_asistente.ps1"


async def _durante(coro, segundos):
    try:
        await asyncio.wait_for(coro, timeout=segundos)
    except TimeoutError:
        pass  # los bucles son infinitos: se los deja correr un rato y se los corta


def _errores_con_traceback(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR and r.exc_info]


# ---------- heartbeat ----------


def test_el_heartbeat_sin_red_deja_una_linea_y_sigue_intentando(monkeypatch, caplog):
    llamadas = []

    async def ciclo_sin_red(*args, **kwargs):
        llamadas.append(1)
        raise psycopg.OperationalError("could not translate host name")

    async def enviar(texto):
        pass

    monkeypatch.setattr(heartbeat, "ciclo", ciclo_sin_red)
    with caplog.at_level("WARNING", logger="asistente.heartbeat.runner"):
        asyncio.run(_durante(
            heartbeat.latido(enviar, tz=TZ, intervalo_s=0.01, hora_inicio=8, hora_fin=21), 0.2))
    assert len(llamadas) >= 3  # el bucle no murió
    assert "sin conexión a la base" in caplog.text
    assert _errores_con_traceback(caplog) == []  # sin tracebacks cada minuto


def test_un_error_de_programa_en_el_heartbeat_si_deja_traceback(monkeypatch, caplog):
    async def ciclo_roto(*args, **kwargs):
        raise RuntimeError("bug real")

    async def enviar(texto):
        pass

    monkeypatch.setattr(heartbeat, "ciclo", ciclo_roto)
    with caplog.at_level("WARNING", logger="asistente.heartbeat.runner"):
        asyncio.run(_durante(
            heartbeat.latido(enviar, tz=TZ, intervalo_s=0.01, hora_inicio=8, hora_fin=21), 0.1))
    assert _errores_con_traceback(caplog)  # lo que no es la red no se esconde


# ---------- vigía ----------


def test_el_vigia_sin_red_deja_una_linea_y_sigue_intentando(monkeypatch, caplog):
    llamadas = []

    async def ciclo_sin_red(*args, **kwargs):
        llamadas.append(1)
        raise psycopg.OperationalError("sin red")

    async def publicar(pub):
        pass

    monkeypatch.setattr(vigia, "ciclo", ciclo_sin_red)
    with caplog.at_level("WARNING", logger="asistente.vigia.runner"):
        asyncio.run(_durante(
            vigia.vigia(publicar, buscador=None, llm=None, tz=TZ, intervalo_s=0.01, max_busquedas_dia=10), 0.2))
    assert len(llamadas) >= 3
    assert "sin conexión a la base" in caplog.text
    assert _errores_con_traceback(caplog) == []


# ---------- supervisor de Windows ----------


@pytest.mark.skipif(sys.platform != "win32", reason="El supervisor es un script de PowerShell")
class TestSupervisor:
    def ejecutar(self, codigo_python: str, *extra: str):
        comando = (
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "  # para leer bien los acentos
            f"& '{SCRIPT}' -Python '{sys.executable}' -Argumentos @('-c','{codigo_python}') "
            f"-EsperaSegundos 1 {' '.join(extra)}; exit $LASTEXITCODE"
        )
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command", comando],
            capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace", check=False,
        )

    def test_si_termina_con_error_lo_reinicia_hasta_el_maximo(self):
        r = self.ejecutar("raise SystemExit(3)", "-MaxReinicios 2")
        assert r.returncode == 3
        assert "Reinicio #1" in r.stdout and "máximo de reinicios" in r.stdout

    def test_si_termina_con_normalidad_no_lo_reinicia(self):
        r = self.ejecutar("raise SystemExit(0)")
        assert r.returncode == 0
        assert "No se reinicia" in r.stdout and "Reinicio" not in r.stdout
