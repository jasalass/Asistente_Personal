#!/usr/bin/env bash
# Lo ejecuta GitHub Actions por SSH, a través del usuario "deploy" (comando forzado en su
# authorized_keys, ver GUIA.md paso 9). Vive fuera de /opt/asistente/app a propósito: si un pull
# rompe el checkout, este script no se ve afectado.
set -euo pipefail

cd /opt/asistente/app
sudo -u asistente git pull --ff-only
sudo -u asistente /opt/asistente/.venv/bin/pip install .
systemctl restart asistente

echo "Desplegado: $(sudo -u asistente git -C /opt/asistente/app rev-parse --short HEAD)"
