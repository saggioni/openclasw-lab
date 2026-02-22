#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/opt/openclaw-router"
APP_DIR="$APP_ROOT/app"
DATA_DIR="/var/lib/openclaw-router"
LOG_DIR="/var/log/openclaw-router"
SERVICE_NAME="openclaw-router"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Execute como root." >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/7] Instalando dependências do Ubuntu..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv sqlite3 curl ca-certificates

echo "[2/7] Criando diretórios persistentes..."
mkdir -p "$APP_DIR" "$DATA_DIR" "$LOG_DIR"
chmod 700 "$APP_ROOT"
chmod 700 "$DATA_DIR"
chmod 755 "$LOG_DIR"

echo "[3/7] Copiando aplicação..."
install -m 0644 "$REPO_DIR/router/router.py" "$APP_DIR/router.py"

if [[ ! -f "$APP_ROOT/.env" ]]; then
  echo "[4/7] Criando .env inicial (ajuste suas chaves antes de usar)..."
  install -m 0600 "$REPO_DIR/.env.example" "$APP_ROOT/.env"
else
  echo "[4/7] .env já existe, preservando."
fi

# Garante ownership root (ambiente de laboratório)
chown -R root:root "$APP_ROOT" "$DATA_DIR" "$LOG_DIR"

# Cria DB vazio se ainda não existir; o app cria schema ao iniciar
if [[ ! -f "$DATA_DIR/state.db" ]]; then
  echo "[5/7] Criando banco SQLite inicial..."
  install -m 0600 /dev/null "$DATA_DIR/state.db"
fi

# Instala unit file
echo "[6/7] Instalando serviço systemd..."
install -m 0644 "$REPO_DIR/deploy/systemd/openclaw-router.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

cat <<MSG

[7/7] Instalação concluída.

Próximos passos:
1. Edite $APP_ROOT/.env e configure GEMINI_API_KEY_FREE / GEMINI_API_KEY_PAID e nomes de modelos.
2. Inicie o serviço: systemctl start $SERVICE_NAME
3. Verifique status: systemctl status $SERVICE_NAME --no-pager
4. Teste local:
   curl -sS http://127.0.0.1:8787/healthz

Logs:
  journalctl -u $SERVICE_NAME -f

MSG
