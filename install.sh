#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/opt/openclaw-router"
APP_DIR="$APP_ROOT/app"
DATA_DIR="/var/lib/openclaw-router"
LOG_DIR="/var/log/openclaw-router"
SERVICE_NAME="openclaw-router"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/7] Installing Ubuntu dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv sqlite3 curl ca-certificates

echo "[2/7] Creating persistent directories..."
mkdir -p "$APP_DIR" "$DATA_DIR" "$LOG_DIR"
chmod 700 "$APP_ROOT"
chmod 700 "$DATA_DIR"
chmod 755 "$LOG_DIR"

echo "[3/7] Copying application files..."
install -m 0644 "$REPO_DIR/router/router.py" "$APP_DIR/router.py"

if [[ ! -f "$APP_ROOT/.env" ]]; then
  echo "[4/7] Creating initial .env file (set your keys before use)..."
  install -m 0600 "$REPO_DIR/.env.example" "$APP_ROOT/.env"
else
  echo "[4/7] .env already exists, preserving it."
fi

# Keep root ownership for a lab environment.
chown -R root:root "$APP_ROOT" "$DATA_DIR" "$LOG_DIR"

# Create an empty DB if it does not exist yet; the app creates the schema on startup.
if [[ ! -f "$DATA_DIR/state.db" ]]; then
  echo "[5/7] Creating initial SQLite database file..."
  install -m 0600 /dev/null "$DATA_DIR/state.db"
fi

# Install systemd unit file.
echo "[6/7] Installing systemd service..."
install -m 0644 "$REPO_DIR/deploy/systemd/openclaw-router.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

cat <<MSG

[7/7] Installation complete.

Next steps:
1. Edit $APP_ROOT/.env and configure GEMINI_API_KEY_FREE / GEMINI_API_KEY_PAID and model names.
2. Start the service: systemctl start $SERVICE_NAME
3. Check status: systemctl status $SERVICE_NAME --no-pager
4. Local health check:
   curl -sS http://127.0.0.1:8787/healthz

Logs:
  journalctl -u $SERVICE_NAME -f

MSG
