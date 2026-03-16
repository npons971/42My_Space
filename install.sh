#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
PYTHON_BIN="${PYTHON:-python3}"
ZSHRC_FILE="${HOME}/.zshrc"
ALIAS_LINE="alias 42msg='cd ${PROJECT_DIR} && source ${VENV_DIR}/bin/activate && python -m ftmsg'"

echo "[42msg] Project dir: ${PROJECT_DIR}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "[42msg] Python introuvable: ${PYTHON_BIN}" >&2
  exit 1
fi

echo "[42msg] Création du venv..."
"${PYTHON_BIN}" -m venv "${VENV_DIR}"

echo "[42msg] Installation des dépendances..."
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${PROJECT_DIR}/requirements.txt"

touch "${ZSHRC_FILE}"
if ! grep -Fq "alias 42msg=" "${ZSHRC_FILE}"; then
  echo "${ALIAS_LINE}" >> "${ZSHRC_FILE}"
  echo "[42msg] Alias ajouté dans ${ZSHRC_FILE}"
else
  echo "[42msg] Alias 42msg déjà présent dans ${ZSHRC_FILE}"
fi

echo "[42msg] Installation terminée. Recharge ton shell: source ~/.zshrc"
