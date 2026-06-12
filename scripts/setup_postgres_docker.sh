#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${POSTGRES_CONTAINER_NAME:-radio1190-postgres}"
DATA_DIR="${POSTGRES_DATA_DIR:-${PWD}/data/postgres}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:16-alpine}"
POSTGRES_DB="${POSTGRES_DB:-radio1190_archive}"
POSTGRES_USER="${POSTGRES_USER:-radio1190}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-radio1190}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker Desktop, start it, then rerun this script." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed, but the Docker daemon is not running." >&2
  echo "Start Docker Desktop and wait until it says Docker is running, then rerun this script." >&2
  exit 1
fi

mkdir -p "${DATA_DIR}"

if docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
  docker start "${CONTAINER_NAME}" >/dev/null
else
  docker run -d \
    --name "${CONTAINER_NAME}" \
    -e POSTGRES_DB="${POSTGRES_DB}" \
    -e POSTGRES_USER="${POSTGRES_USER}" \
    -e POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
    -p "${POSTGRES_PORT}:5432" \
    -v "${DATA_DIR}:/var/lib/postgresql/data" \
    "${POSTGRES_IMAGE}" >/dev/null
fi

echo "Waiting for Postgres to accept connections..."
until docker exec "${CONTAINER_NAME}" pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; do
  sleep 1
done

DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/${POSTGRES_DB}"
cat <<EOF
Postgres is running.

Container: ${CONTAINER_NAME}
Data dir:   ${DATA_DIR}
URL:        ${DATABASE_URL}

Add this to .env or /etc/radio1190-archive.env:
DATABASE_URL=${DATABASE_URL}
EOF
