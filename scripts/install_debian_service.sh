#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-radio1190-archive}"
APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RUN_USER="${RUN_USER:-${SUDO_USER:-$(id -un)}}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
PORT="${PORT:-8190}"
HOST="${HOST:-0.0.0.0}"
DATA_DIR="${DATA_DIR:-/var/lib/${SERVICE_NAME}}"
ENV_FILE="${ENV_FILE:-/etc/${SERVICE_NAME}.env}"
DATABASE_URL="${DATABASE_URL:-postgresql://radio1190:radio1190@127.0.0.1:5432/radio1190_archive}"
COVER_DIR="${COVER_DIR:-${DATA_DIR}/covers}"
ARTIST_IMAGE_DIR="${ARTIST_IMAGE_DIR:-${DATA_DIR}/artist-images}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

mkdir -p "${COVER_DIR}" "${ARTIST_IMAGE_DIR}"
chown -R "${RUN_USER}:${RUN_USER}" "${DATA_DIR}" 2>/dev/null || true

cat > "${UNIT_PATH}" <<UNIT
[Unit]
Description=Radio 1190 Music Archive
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=-${ENV_FILE}
Environment=HOST=${HOST}
Environment=PORT=${PORT}
Environment=DATABASE_URL=${DATABASE_URL}
Environment=COVER_DIR=${COVER_DIR}
Environment=ARTIST_IMAGE_DIR=${ARTIST_IMAGE_DIR}
Environment=ENV_PATH=${ENV_FILE}
ExecStart=${PYTHON_BIN} ${APP_DIR}/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service"
systemctl status "${SERVICE_NAME}.service" --no-pager
