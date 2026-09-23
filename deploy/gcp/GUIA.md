# Desplegar en GCP (nivel gratis para siempre)

Una sola VM `e2-micro`, dentro del nivel sin costo permanente de Google Cloud (no el crédito de
prueba de 90 días, que es aparte). El bot corre igual que en tu PC: un proceso, con `systemd` en
vez del script de PowerShell haciendo de supervisor.

**Limite real del free tier**: 1 instancia `e2-micro` **solo** en `us-west1`, `us-central1` o
`us-east1`; 30 GB-mes de disco estándar; 1 GB de salida de datos al mes. Fuera de esas regiones se
cobra como una VM normal. No hay fecha de vencimiento, pero es tu tarjeta la que respalda la cuenta:
pon una alerta de presupuesto (paso 2) para enterarte si algo se sale del límite.

## 0. Prerrequisitos

- Cuenta de Google Cloud con facturación habilitada (piden tarjeta aunque no se cobre nada dentro
  del límite gratis).
- `gcloud` CLI instalado en tu PC ([cloud.google.com/sdk](https://cloud.google.com/sdk)) y
  autenticado (`gcloud init`).
- El repo es privado: necesitas una **deploy key** de solo lectura para clonarlo desde la VM (paso 4).

## 1. Crear el proyecto y la VM

```powershell
gcloud projects create asistente-personal-xxxx  # el sufijo debe ser único a nivel global
gcloud config set project asistente-personal-xxxx
gcloud services enable compute.googleapis.com

gcloud compute instances create asistente `
  --zone=us-central1-a `
  --machine-type=e2-micro `
  --image-family=ubuntu-2404-lts-amd64 `
  --image-project=ubuntu-os-cloud `
  --boot-disk-size=30GB `
  --boot-disk-type=pd-standard
```

No hace falta abrir ningún puerto: el bot solo hace conexiones salientes (Discord, Groq, Tavily,
Supabase). Las reglas por defecto del proyecto ya permiten SSH de entrada; no agregues nada más.

## 2. Alerta de presupuesto (para dormir tranquilo)

Consola → Facturación → Presupuestos y alertas → crear uno de, por ejemplo, US$1 y US$5. El costo
esperado es US$0 dentro del límite; la alerta es para enterarte rápido si algo se te escapó (un
disco más grande, otra VM olvidada encendida, etc.), no porque se espere gastar.

## 3. Preparar el sistema en la VM

```powershell
gcloud compute ssh asistente --zone=us-central1-a
```

Ya dentro de la VM:

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git

sudo useradd --system --create-home --home-dir /opt/asistente --shell /usr/sbin/nologin asistente
```

## 4. Clonar el repo (con una deploy key de solo lectura)

En tu PC, genera una llave dedicada a esto (no reutilices tu llave SSH personal):

```powershell
ssh-keygen -t ed25519 -C "asistente-gcp" -f deploy_key -N ""
```

Sube `deploy_key.pub` en GitHub → tu repo → Settings → Deploy keys → Add deploy key (sin permiso de
escritura). La llave privada (`deploy_key`, sin extensión) viaja a la VM por `scp`, nunca por chat:

```powershell
gcloud compute scp deploy_key asistente@asistente:/tmp/deploy_key --zone=us-central1-a
```

De vuelta en la VM:

```bash
sudo mkdir -p /opt/asistente/.ssh
sudo mv /tmp/deploy_key /opt/asistente/.ssh/id_ed25519
sudo chown -R asistente:asistente /opt/asistente/.ssh
sudo chmod 700 /opt/asistente/.ssh && sudo chmod 600 /opt/asistente/.ssh/id_ed25519
sudo -u asistente ssh-keyscan github.com | sudo -u asistente tee -a /opt/asistente/.ssh/known_hosts

sudo -u asistente git clone git@github.com:jasalass/Asistente_Personal.git /opt/asistente/app
```

## 5. Instalar el asistente

```bash
cd /opt/asistente/app
sudo -u asistente python3 -m venv /opt/asistente/.venv
sudo -u asistente /opt/asistente/.venv/bin/pip install .
```

## 6. Copiar el `.env`

Desde tu PC, con tu `.env` real (nunca lo pegues en la terminal de la VM ni lo subas a git):

```powershell
gcloud compute scp .env asistente@asistente:/tmp/.env --zone=us-central1-a
```

En la VM:

```bash
sudo mv /tmp/.env /opt/asistente/.env
sudo chown asistente:asistente /opt/asistente/.env
sudo chmod 600 /opt/asistente/.env
```

`pydantic-settings` lo lee solo desde el directorio de trabajo del proceso — por eso el `systemd`
unit fija `WorkingDirectory=/opt/asistente`, no `/opt/asistente/app`. Cópialo ahí, no dentro de `app/`.

## 7. Instalar el servicio de systemd

```bash
sudo cp /opt/asistente/app/deploy/gcp/asistente.service /etc/systemd/system/asistente.service
sudo systemctl daemon-reload
sudo systemctl enable --now asistente
```

Verificar:

```bash
sudo systemctl status asistente
journalctl -u asistente -f
```

Deberías ver las mismas líneas que en tu PC: `Instancia única confirmada`, `Conectado como
asistente#6137`, `Heartbeat activo`, `Vigía de temas activo`. Cuando pase, apaga el asistente en tu
PC: el bloqueo de Postgres no deja que dos instancias respondan a la vez, pero solo una vale la pena
tener corriendo.

## 8. Actualizar cuando haya cambios

```bash
cd /opt/asistente/app
sudo -u asistente git pull
sudo -u asistente /opt/asistente/.venv/bin/pip install .   # solo si cambiaron las dependencias
sudo systemctl restart asistente
```

Hay un corte breve mientras reinicia (a diferencia de un despliegue con dos instancias en paralelo);
para un asistente personal no es un problema.

## 9. Despliegue automático (GitHub Actions)

Al hacer push a `main`, un workflow (`.github/workflows/deploy.yml`) hace lo mismo que el paso 8, pero
solo. Dos identidades separadas, cada una con lo mínimo:

- **Una cuenta de servicio de GCP**, que solo puede abrir un túnel hacia la VM (IAP). No puede entrar
  por SSH ni hacer nada dentro de ella.
- **Una llave SSH dedicada**, que entra como un usuario `deploy` sin shell propio: su
  `authorized_keys` fuerza un único comando (`deploy/gcp/deploy.sh`), pase lo que pase del otro lado.

El puerto 22 **nunca se expone a internet**: el túnel de IAP es el único camino de entrada, y solo
GCP decide quién puede abrirlo.

### 9.1 Restringir SSH a solo IAP

```bash
gcloud services enable iap.googleapis.com
gcloud compute firewall-rules create allow-iap-ssh \
  --network=default --direction=INGRESS --action=ALLOW --rules=tcp:22 \
  --source-ranges=35.235.240.0/20
gcloud compute firewall-rules delete default-allow-ssh   # ya no hace falta: todo entra por IAP
```

### 9.2 Cuenta de servicio que solo abre el túnel

```bash
PROYECTO=$(gcloud config get-value project)
gcloud iam service-accounts create gha-deploy --display-name="GitHub Actions (solo túnel IAP)"
gcloud projects add-iam-policy-binding "$PROYECTO" \
  --member="serviceAccount:gha-deploy@${PROYECTO}.iam.gserviceaccount.com" \
  --role="roles/iap.tunnelResourceAccessor"
gcloud projects add-iam-policy-binding "$PROYECTO" \
  --member="serviceAccount:gha-deploy@${PROYECTO}.iam.gserviceaccount.com" \
  --role="roles/compute.viewer"
gcloud iam service-accounts keys create gha-deploy-key.json \
  --iam-account="gha-deploy@${PROYECTO}.iam.gserviceaccount.com"
```

`gha-deploy-key.json` es un secreto: pega su contenido completo en el secret de GitHub `GCP_SA_KEY`
(paso 9.4) y después bórralo de tu disco (`rm gha-deploy-key.json`). No lo commitees ni lo pegues en
el chat.

> Es una llave de larga duración: suficiente para un proyecto personal. Si más adelante quieres algo
> más estricto (sin llaves, credenciales que expiran solas), lo siguiente sería Workload Identity
> Federation — pídemelo cuando quieras dar ese paso.

### 9.3 Usuario `deploy` con un único comando permitido

En tu PC, una llave dedicada a esto (no reutilices la del paso 4):

```powershell
ssh-keygen -t ed25519 -C "github-actions-deploy" -f deploy_ssh_key -N ""
```

En la VM:

```bash
sudo useradd --system --create-home --home-dir /opt/deploy --shell /usr/sbin/nologin deploy
sudo mkdir -p /opt/deploy/.ssh
```

Pega el contenido de `deploy_ssh_key.pub` en `/opt/deploy/.ssh/authorized_keys`, **con este prefijo**
(reemplaza solo la parte `ssh-ed25519 AAAA...` por la tuya):

```
command="sudo /opt/asistente/deploy.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA...tu-llave... github-actions-deploy
```

El `nologin` no importa: el `command=` forzado corre igual, sin pasar por ningún shell.

```bash
sudo chown -R deploy:deploy /opt/deploy/.ssh
sudo chmod 700 /opt/deploy/.ssh && sudo chmod 600 /opt/deploy/.ssh/authorized_keys

sudo cp /opt/asistente/app/deploy/gcp/deploy.sh /opt/asistente/deploy.sh
sudo chown root:root /opt/asistente/deploy.sh && sudo chmod 700 /opt/asistente/deploy.sh

echo 'deploy ALL=(root) NOPASSWD: /opt/asistente/deploy.sh' | sudo tee /etc/sudoers.d/deploy-restringido
sudo chmod 440 /etc/sudoers.d/deploy-restringido
```

`deploy` no puede hacer nada más que correr exactamente ese script, como root, sin contraseña. Ni
shell, ni otros comandos, ni argumentos propios.

### 9.4 Secretos y variables en GitHub

Repo → Settings → Secrets and variables → Actions:

| Nombre | Tipo | Valor |
|---|---|---|
| `GCP_SA_KEY` | Secret | Contenido completo de `gha-deploy-key.json` |
| `DEPLOY_SSH_KEY` | Secret | Contenido completo de `deploy_ssh_key` (la privada, sin `.pub`) |
| `GCP_PROJECT_ID` | Variable | El id de tu proyecto (`gcloud config get-value project`) |
| `GCP_ZONE` | Variable | `us-central1-a` (o la que hayas usado) |
| `VM_NAME` | Variable | `asistente` |

Además crea un **environment** llamado `produccion` (Settings → Environments) — el workflow lo
referencia; sin él, GitHub lo trata como si no existiera ninguna restricción extra, pero crearlo te
deja agregar más adelante una aprobación manual antes de desplegar, si alguna vez la quieres.

Después de esto, cada push a `main` despliega solo. Revisa la pestaña **Actions** del repo para ver
el resultado; si algo falla, el log del paso "Desplegar" muestra la salida real de `deploy.sh`
(incluye el hash del commit desplegado al final, para confirmar que quedó la versión correcta).

## Diferencias con el supervisor de Windows (`ejecutar_asistente.ps1`)

- El script de PowerShell reintenta con espera creciente (15 s hasta 5 min); `systemd` reintenta
  cada 15 s y se rinde después de 5 intentos en 10 minutos (`StartLimitBurst`). No es idéntico, pero
  cumple lo mismo: no reinicia en un bucle infinito si algo está realmente roto.
- Los avisos de arranque/apagado en `#avisos` (con el nombre del equipo) siguen funcionando igual;
  vas a ver el hostname de la VM en vez del de tu PC.
