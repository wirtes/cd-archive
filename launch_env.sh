#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "Source this script instead of running it:"
  echo "  . scripts/use_server_env.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PATH="${VENV_PATH:-${APP_DIR}/.venv}"
ENV_FILE="${ENV_FILE:-/etc/radio1190-archive.env}"

if [[ ! -f "${VENV_PATH}/bin/activate" ]]; then
  echo "Virtual environment not found: ${VENV_PATH}"
  echo "Create it with:"
  echo "  python3 -m venv .venv"
  echo "  . .venv/bin/activate"
  echo "  python -m pip install -r requirements.txt"
  return 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Environment file not found: ${ENV_FILE}"
  return 1
fi

cd "${APP_DIR}" || return 1
. "${VENV_PATH}/bin/activate"
set -a
. "${ENV_FILE}"
set +a

echo "Loaded ${VENV_PATH} and ${ENV_FILE}"
