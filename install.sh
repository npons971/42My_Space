#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/npons972/42My_Space.git"
INSTALL_DIR="${HOME}/.local/share/42msg"
VENV_DIR="${INSTALL_DIR}/.venv"
PYTHON="${VENV_DIR}/bin/python"
BIN_DIR="${HOME}/.local/bin"
WRAPPER="${BIN_DIR}/42msg"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[42msg]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[42msg]${NC} $1"; }
log_error() { echo -e "${RED}[42msg]${NC} $1" >&2; }

command -v git >/dev/null 2>&1 || { log_error "git est requis mais non installé."; exit 1; }
command -v python3 >/dev/null 2>&1 || { log_error "python3 est requis mais non installé."; exit 1; }

if [ -d "${INSTALL_DIR}" ]; then
    log_warn "Répertoire ${INSTALL_DIR} existe déjà. Mise à jour..."
    cd "${INSTALL_DIR}"
    git pull --quiet
else
    log_info "Clonage du dépôt dans ${INSTALL_DIR}..."
    git clone --quiet "${REPO_URL}" "${INSTALL_DIR}"
fi

cd "${INSTALL_DIR}"

if [ ! -d "${VENV_DIR}" ]; then
    log_info "Création du venv Python..."
    python3 -m venv "${VENV_DIR}"
fi

log_info "Installation des dépendances..."
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip
"${VENV_DIR}/bin/pip" install --quiet -r requirements.txt

log_info "Création du wrapper ${WRAPPER}..."
mkdir -p "${BIN_DIR}"
cat > "${WRAPPER}" <<'EOF'
#!/usr/bin/env bash
# 42msg wrapper — lance le client TUI depuis le venv local
INSTALL_DIR="${HOME}/.local/share/42msg"
PYTHON="${INSTALL_DIR}/.venv/bin/python"
if [ ! -x "${PYTHON}" ]; then
    echo "[42msg] Venv introuvable. Relance l'installation." >&2
    exit 1
fi
exec "${PYTHON}" -m ftmsg "$@"
EOF
chmod +x "${WRAPPER}"

# Ensure ~/.local/bin is in PATH
if ! echo "$PATH" | tr ':' '\n' | grep -Fxq "${BIN_DIR}"; then
    case "${SHELL:-}" in
        */zsh)
            RC="${HOME}/.zshrc"
            ;;
        */bash)
            RC="${HOME}/.bashrc"
            ;;
        *)
            RC="${HOME}/.profile"
            ;;
    esac
    if [ -f "${RC}" ] && ! grep -Fq 'export PATH="$HOME/.local/bin:$PATH"' "${RC}" 2>/dev/null; then
        log_info "Ajout de ${BIN_DIR} dans le PATH (${RC})..."
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "${RC}"
    fi
fi

log_info "Installation terminée !"
echo ""
echo -e "  Lance: ${GREEN}42msg${NC}"
echo -e "  Ou:    ${GREEN}42msg --login mon_pseudo${NC}"
echo ""
if ! command -v 42msg >/dev/null 2>&1; then
    log_warn "Relance ton shell ou tape: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
