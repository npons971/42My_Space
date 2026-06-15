PROJECT_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))
VENV_DIR    := $(PROJECT_DIR)/.venv
PYTHON      := $(VENV_DIR)/bin/python
UV          := $(shell command -v uv)

.PHONY: install run clean re fclean venv uninstall

venv:
	@test -d $(VENV_DIR) || (echo "[42msg] Création du venv avec uv..." && cd $(PROJECT_DIR) && uv venv)

install: venv
	@echo "[42msg] Installation des dépendances avec uv..."
	@cd $(PROJECT_DIR) && uv pip install -e .
	@echo "[42msg] Configuration de l'alias..."
	@# Supprime l'ancien alias s'il existe (pour éviter les doublons ou les anciennes versions)
	@sed -i '/alias 42msg=/d' $(HOME)/.zshrc 2>/dev/null || true
	@echo "alias 42msg='(cd $(PROJECT_DIR) && git pull --quiet --rebase 2>/dev/null && uv pip install --quiet -e . 2>/dev/null); env PYTHONPATH=$(PROJECT_DIR) $(PYTHON) -m ftmsg --relay wss://four2my-space.onrender.com'" >> $(HOME)/.zshrc
	@echo "[42msg] Alias ajouté dans $(HOME)/.zshrc"

relay: venv
	@echo "[42msg] Lancement du relais local..."
	@cd $(PROJECT_DIR) && uv run relay_server.py

run:
	@test -d $(VENV_DIR) || (echo "Lance 'make install' d'abord" && exit 1)
	@cd $(PROJECT_DIR) && uv run -m ftmsg --relay wss://four2my-space.onrender.com

clean:
	@rm -rf $(VENV_DIR)
	@rm -rf __pycache__ */__pycache__ */*/__pycache__
	@find $(PROJECT_DIR) -name '*.pyc' -delete
	@echo "Nettoyage terminé"

fclean: clean
	@rm -rf $(HOME)/.42msg
	@echo "Suppression des données utilisateur"

uninstall: fclean
	@echo "[42msg] Suppression de l'alias dans $(HOME)/.zshrc..."
	@if grep -Fq "alias 42msg=" $(HOME)/.zshrc 2>/dev/null; then \
		sed -i '/alias 42msg=/d' $(HOME)/.zshrc; \
		echo "[42msg] Alias retiré. Recharge ton shell: source ~/.zshrc"; \
	else \
		echo "[42msg] Alias 42msg non trouvé dans $(HOME)/.zshrc"; \
	fi
	@echo "[42msg] Désinstallation terminée"

re: clean install
