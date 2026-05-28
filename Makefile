PROJECT_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))
VENV_DIR   := $(PROJECT_DIR)/.venv
PYTHON     := $(VENV_DIR)/bin/python

.PHONY: install run clean re fclean

install:
	@bash $(PROJECT_DIR)/install.sh

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

re: clean install
