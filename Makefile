PYTHON ?= .venv/bin/python
E2E_WORKERS ?= 2

.PHONY: dev setup db tailwind migrate run run-http ssl-cert seed ci lint typecheck test test-e2e test-focus test-parallel test-e2e-parallel test-e2e-smoke check deps-lock deps-check maintenance-on maintenance-off

# Erstmalige Einrichtung: .env aus .env.example erzeugen und Keys generieren
setup:
	@if [ -f .env ]; then echo ".env existiert bereits."; else \
		cp .env.example .env && \
		KEY=$$($(PYTHON) -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") && \
		sed -i "s/^ENCRYPTION_KEY=$$/ENCRYPTION_KEY=$$KEY/" .env && \
		echo ".env erstellt. Bitte die übrigen Werte anpassen."; \
	fi
	@if [ ! -f certs/dev-cert.pem ]; then $(MAKE) ssl-cert; fi

# PostgreSQL als Docker-Container
db:
	docker run -d --name anlaufstelle-db \
		-e POSTGRES_DB=anlaufstelle \
		-e POSTGRES_USER=anlaufstelle \
		-e POSTGRES_PASSWORD=anlaufstelle \
		-p 5432:5432 \
		postgres:16-alpine

db-stop:
	docker stop anlaufstelle-db && docker rm anlaufstelle-db

# Tailwind CSS kompilieren
tailwind:
	npx tailwindcss -i src/static/css/input.css -o src/static/css/styles.css --watch

tailwind-build:
	npx tailwindcss -i src/static/css/input.css -o src/static/css/styles.css --minify

# Django
migrate:
	$(PYTHON) src/manage.py migrate

# Dev-Server (gunicorn + HTTPS auf Port 8443)
run:
	@if [ ! -f certs/dev-cert.pem ]; then $(MAKE) ssl-cert; fi
	@pids=$$(lsof -ti :8443 2>/dev/null); \
	if [ -n "$$pids" ]; then \
		echo "⚠  Port 8443 belegt (PIDs: $$pids) — beende alte Prozesse…"; \
		kill $$pids 2>/dev/null; sleep 1; \
	fi
	DJANGO_SETTINGS_MODULE=anlaufstelle.settings.dev \
	$(PYTHON) -m gunicorn anlaufstelle.wsgi:application \
		--bind 0.0.0.0:8443 \
		--certfile $(CURDIR)/certs/dev-cert.pem \
		--keyfile $(CURDIR)/certs/dev-key.pem \
		--workers 2 --threads 2 \
		--chdir src --reload

# Fallback: Django runserver ohne HTTPS
run-http:
	@pids=$$(lsof -ti :8000 2>/dev/null); \
	if [ -n "$$pids" ]; then \
		echo "⚠  Port 8000 belegt (PIDs: $$pids) — beende alte Prozesse…"; \
		kill $$pids 2>/dev/null; sleep 1; \
	fi
	$(PYTHON) src/manage.py runserver 0.0.0.0:8000

# Selbstsigniertes Zertifikat generieren
# LAN-IP fürs Testen von Mobilgeräten/PWA: SSL_HOST_IP=192.168.x.y make ssl-cert
SSL_HOST_IP ?= 192.168.1.193
ssl-cert:
	@mkdir -p certs
	openssl req -x509 -newkey rsa:2048 -keyout certs/dev-key.pem -out certs/dev-cert.pem \
		-days 365 -nodes -subj "/CN=localhost" \
		-addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:0.0.0.0,IP:$(SSL_HOST_IP)"
	@echo "Zertifikat erstellt: certs/dev-cert.pem + certs/dev-key.pem (LAN-IP: $(SSL_HOST_IP))"

seed:
	$(PYTHON) src/manage.py seed

# CI lokal
lint:
	$(PYTHON) -m ruff check src/
	$(PYTHON) -m ruff format --check src/

# mypy auf core/services strikt, Rest mit permissiver Baseline (Refs #741).
# Erweiterung modulweise via [[tool.mypy.overrides]] in pyproject.toml.
typecheck:
	$(PYTHON) -m mypy src/core/services

# Maintenance-Mode (Refs #700). Aktiviert per File-Flag — bei nicht
# gesetztem MAINTENANCE_FLAG_FILE-Env-Var wird /tmp/anlaufstelle.maintenance
# als sinnvoller Default genutzt.
MAINTENANCE_FLAG ?= $(or $(MAINTENANCE_FLAG_FILE),/tmp/anlaufstelle.maintenance)

maintenance-on:
	@touch $(MAINTENANCE_FLAG) && echo "Maintenance-Mode AKTIV ($(MAINTENANCE_FLAG))"

maintenance-off:
	@rm -f $(MAINTENANCE_FLAG) && echo "Maintenance-Mode aus ($(MAINTENANCE_FLAG))"

test:
	$(PYTHON) -m pytest -m "not e2e"

test-e2e:
	$(PYTHON) -m pytest -m e2e --browser chromium -v

test-focus:
	$(PYTHON) -m pytest -m "not e2e" -x $(T)

test-parallel:
	$(PYTHON) -m pytest -m "not e2e" -n auto -x

test-e2e-parallel:
	$(PYTHON) -m pytest -m e2e --browser chromium -n $(E2E_WORKERS) --dist loadfile -v

test-e2e-smoke:
	$(PYTHON) -m pytest -m "e2e and smoke" --browser chromium -v

check:
	$(PYTHON) src/manage.py check
	$(PYTHON) src/manage.py makemigrations --check --dry-run

ci: lint check test-parallel

# Dependencies: Lock-Files aus requirements*.in neu erzeugen (pip-tools).
# Nach Änderungen an requirements.in oder requirements-dev.in ausführen.
deps-lock:
	$(PYTHON) -m piptools compile --no-strip-extras --resolver=backtracking \
		--output-file=requirements.txt requirements.in
	$(PYTHON) -m piptools compile --no-strip-extras --resolver=backtracking \
		--output-file=requirements-dev.txt requirements-dev.in

# Verifiziert, dass requirements*.txt aktuell zu requirements*.in ist.
# Schlägt fehl, wenn ein Regen erforderlich wäre — wird in CI genutzt.
# Ansatz: Datei sichern, regenerieren, mit git-diff vergleichen, Original wiederherstellen.
deps-check:
	@cp requirements.txt requirements.txt.bak && \
		cp requirements-dev.txt requirements-dev.txt.bak && \
		$(PYTHON) -m piptools compile --quiet --no-strip-extras --resolver=backtracking \
			--output-file=requirements.txt requirements.in >/dev/null && \
		$(PYTHON) -m piptools compile --quiet --no-strip-extras --resolver=backtracking \
			--output-file=requirements-dev.txt requirements-dev.in >/dev/null && \
		drift=0; \
		diff -u requirements.txt.bak requirements.txt || drift=1; \
		diff -u requirements-dev.txt.bak requirements-dev.txt || drift=1; \
		mv requirements.txt.bak requirements.txt && \
		mv requirements-dev.txt.bak requirements-dev.txt && \
		if [ $$drift -ne 0 ]; then \
			echo "Lock-Files sind nicht aktuell — 'make deps-lock' ausführen."; \
			exit 1; \
		fi

# Alles zusammen
dev: db migrate run
