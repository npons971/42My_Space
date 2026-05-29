PROJECT_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))
VENV_DIR    := $(PROJECT_DIR)/.venv
PYTHON      := $(VENV_DIR)/bin/python
PIP         := $(VENV_DIR)/bin/pip

.PHONY: install run clean re fclean venv uninstall

venv:
	@test -d $(VENV_DIR) || (echo "[42msg] Création du venv..." && python3 -m venv $(VENV_DIR))
	@echo "[42msg] Mise à jour de pip dans le venv..."
	@$(PIP) install --upgrade pip --quiet

install: venv
	@echo "[42msg] Installation des dépendances dans le venv..."
	@$(PIP) install --no-user -r $(PROJECT_DIR)/requirements.txt
	@echo "[42msg] Configuration de l'alias..."
	@if ! grep -Fq "alias 42msg=" $(HOME)/.zshrc 2>/dev/null; then \
		echo "alias 42msg='cd $(PROJECT_DIR) && $(PYTHON) -m ftmsg'" >> $(HOME)/.zshrc; \
		echo "[42msg] Alias ajouté dans $(HOME)/.zshrc"; \
	else \
		echo "[42msg] Alias 42msg déjà présent dans $(HOME)/.zshrc"; \
	fi

relay: venv
	@echo "[42msg] Lancement du relais local..."
	@$(PYTHON) relay_server.py

run:
	@test -d $(VENV_DIR) || (echo "Lance 'make install' d'abord" && exit 1)
	@$(PYTHON) -m ftmsg

run-login:
	@test -d $(VENV_DIR) || (echo "Lance 'make install' d'abord" && exit 1)
	@$(PYTHON) -m ftmsg --login $(LOGIN)

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
