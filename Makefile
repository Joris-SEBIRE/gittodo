PYTHON ?= /opt/homebrew/bin/python3.13
VENV := .venv
APP := build/GitTodo.app
INSTALLED := /Applications/GitTodo.app

.PHONY: venv print run app install restart stop uninstall clean

venv: $(VENV)/bin/python

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --quiet --upgrade pip
	$(VENV)/bin/python -m pip install --quiet -r requirements.txt

print: venv          ## Affiche en texte ce que le menu contiendrait
	PYTHONPATH=src $(VENV)/bin/python -m gittodo --print

run: venv            ## Lance depuis les sources (Ctrl-C pour arrêter)
	PYTHONPATH=src $(VENV)/bin/python -m gittodo

app:                 ## Construit build/GitTodo.app
	./scripts/build_app.sh $(APP)

install: app stop    ## Installe dans /Applications et lance
	rm -rf $(INSTALLED)
	ditto $(APP) $(INSTALLED)
	open $(INSTALLED)

restart: stop        ## Relance l'app installée
	open $(INSTALLED)

stop:                ## Arrête toute instance (osascript déclencherait une demande d'autorisation)
	-@pkill -f -- '-m gittodo' >/dev/null 2>&1 || true

uninstall: stop     ## Retire l'app, le LaunchAgent, l'état local et le cache des photos
	-@launchctl unload -w ~/Library/LaunchAgents/fr.jsebire.gittodo.plist >/dev/null 2>&1 || true
	rm -rf $(INSTALLED) ~/Library/LaunchAgents/fr.jsebire.gittodo.plist
	rm -rf ~/Library/Application\ Support/GitTodo ~/Library/Caches/GitTodo

clean:
	rm -rf build $(VENV) src/gittodo/__pycache__
