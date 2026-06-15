#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/npons971/42My_Space.git"
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
command -v uv >/dev/null 2>&1 || { log_error "uv est requis mais non installé. Installe-le via: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
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
    log_info "Création du venv Python avec uv..."
    uv venv
fi

log_info "Installation des dépendances avec uv..."
if ! uv pip install -e .; then
    log_error "Échec de l'installation des dépendances."
    log_error "Si l'erreur concerne pynacl, installe les paquets système:"
    log_error "  sudo apt-get install python3-dev libffi-dev libsodium-dev build-essential"
    exit 1
fi

log_info "Vérification de l'installation..."
if ! uv run python -c "import ftmsg; import textual; import nacl; print('OK')" 2>/dev/null; then
    log_error "Les dépendances ne sont pas correctement installées dans le venv."
    log_error "Tentative de réinstallation forcée..."
    uv pip install --force-reinstall -e . || {
        log_error "Réinstallation forcée échouée aussi."
        exit 1
    }
fi

log_info "Création du wrapper ${WRAPPER}..."
mkdir -p "${BIN_DIR}"
cat > "${WRAPPER}" <<'EOF'
#!/usr/bin/env bash
# 42msg wrapper — lance le client TUI depuis le venv local
INSTALL_DIR="${HOME}/.local/share/42msg"
if [ ! -d "${INSTALL_DIR}/.venv" ]; then
    echo "[42msg] Venv introuvable. Relance l'installation." >&2
    exit 1
fi
cd "${INSTALL_DIR}" || exit 1

# Tentative de mise à jour (silencieuse, non bloquante)
if [ -d "${INSTALL_DIR}/.git" ]; then
    (
        git pull --quiet --rebase 2>/dev/null
        uv pip install --quiet -e . 2>/dev/null
    ) &>/dev/null &
fi

uv run -m ftmsg --relay wss://four2my-space.onrender.com "$@"
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
