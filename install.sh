#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
ZSHRC_FILE="${HOME}/.zshrc"
ALIAS_LINE="alias 42msg='cd ${PROJECT_DIR} && ${PYTHON_BIN} -m ftmsg'"

echo "[42msg] Project dir: ${PROJECT_DIR}"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "[42msg] Venv introuvable. Lance d'abord: make install" >&2
  exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[42msg] Python du venv introuvable: ${PYTHON_BIN}" >&2
  exit 1
fi

touch "${ZSHRC_FILE}"
if ! grep -Fq "alias 42msg=" "${ZSHRC_FILE}"; then
  echo "${ALIAS_LINE}" >> "${ZSHRC_FILE}"
  echo "[42msg] Alias ajouté dans ${ZSHRC_FILE}"
else
  echo "[42msg] Alias 42msg déjà présent dans ${ZSHRC_FILE}"
fi

echo "[42msg] Installation terminée. Recharge ton shell: source ~/.zshrc"
