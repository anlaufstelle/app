PYTHON ?= .venv/bin/python
E2E_WORKERS ?= 2

.PHONY: dev setup db tailwind migrate run run-http ssl-cert seed ci lint typecheck test test-e2e test-focus test-parallel test-e2e-parallel test-e2e-smoke check deps-lock deps-check maintenance-on maintenance-off deploy-dev dev-bootstrap dev-logs dev-shell dev-seed dev-backup dev-status clean test-matrix-index test-matrix-index-check verify-matrix-drift verify-release-test-guard mutation mutation-report ci-coverage docs-screens release-gates release-preflight release-verify-public verify-vendor-js-sync sync-vendor-js

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
		postgres:18-alpine

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

# Doku-Screenshots (DE+EN, Desktop+Mobile, WebP) gegen einen frischen E2E-Server.
# Schreibt nach docs/screenshots/. Braucht die Docker-DB (ggf. `sudo docker compose up -d`).
DSM_E2E := DJANGO_SETTINGS_MODULE=anlaufstelle.settings.e2e
docs-screens:
	-pkill -f 'gunicorn.*8844' 2>/dev/null || true
	$(DSM_E2E) $(PYTHON) src/manage.py migrate --run-syncdb
	$(DSM_E2E) $(PYTHON) src/manage.py seed --flush --scale=medium
	$(DSM_E2E) $(PYTHON) src/manage.py collectstatic --noinput
	$(DSM_E2E) $(PYTHON) -m gunicorn anlaufstelle.wsgi:application \
		--bind 127.0.0.1:8844 --workers 2 --threads 2 --chdir src --timeout 120 & echo $$! > /tmp/docs-screens.pid
	@sleep 5
	-$(DSM_E2E) $(PYTHON) src/manage.py screenshot --base-url http://127.0.0.1:8844
	-kill $$(cat /tmp/docs-screens.pid) 2>/dev/null || true
	@echo "✓ Screenshots in docs/screenshots/"

# CI lokal
# Refs #860: scripts/ mitgescannt — die check_*.py-Helfer landeten sonst nur
# im pre-commit-Hook, nicht im 'make ci' / Pre-Push-Pfad.
lint:
	$(PYTHON) -m ruff check src/ scripts/
	$(PYTHON) -m ruff format --check src/ scripts/

# mypy auf core/services strikt, Rest mit permissiver Baseline (Refs #741).
# Erweiterung modulweise via [[tool.mypy.overrides]] in pyproject.toml.
typecheck:
	$(PYTHON) -m mypy src/core/services src/core/forms

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

ci: lint check deps-check verify-matrix-drift verify-release-test-guard verify-vendor-js-sync typecheck test-parallel

# Lokale Coverage-HTML: praktisch zum gezielten Lücken-Suchen.
# CI nutzt --cov-fail-under in test.yml; dieses Target rendert
# zusätzlich einen HTML-Report unter htmlcov/.
ci-coverage:
	$(PYTHON) -m pytest -m "not e2e" --cov=core --cov-report=term-missing --cov-report=html

# Verifiziert, dass alle in docs/testing/manual-test-matrix.md
# referenzierten Test-Files in src/tests/ oder src/tests/e2e/ existieren.
# Refs #922.
verify-matrix-drift:
	$(PYTHON) scripts/verify_test_matrix_drift.py

# Guard (Refs #1137): kein ausgelieferter Test (src/tests/) darf hart auf
# Pfade verweisen, die der Public-/Stage-Release-Snapshot strippt
# (dev-ops/, scripts/dev/, docs/ai/, CLAUDE.md, …) — sonst fällt der Test erst
# auf der public Stage-CI mit FileNotFoundError um. Exclude-Liste als Single
# Source aus dev-ops/release/verify-leak.sh. Frühes Dev-Gate vor pytest.
verify-release-test-guard:
	$(PYTHON) scripts/verify_release_test_guard.py
# Drift-Guard fuer vendored JS-Libs (Refs #1076): vergleicht die in
# package.json gepinnte Version mit dem Versions-String im eingecheckten
# src/static/js/*.min.js. Reiner String-Vergleich — kein node/npm noetig.
verify-vendor-js-sync:
	$(PYTHON) scripts/verify_vendor_js_sync.py

# Vendored JS-Libs aus node_modules/ neu kopieren (Refs #1076). Nach einem
# Dependabot-Bump: erst 'npm ci', dann dieses Target, dann src/static/js/
# committen. node_modules/ ist gitignored.
sync-vendor-js:
	$(PYTHON) scripts/sync_vendor_js.py

# Mutation-Testing für core/services + core/forms (Refs #922 / #923).
# Konfiguration in pyproject.toml [tool.mutmut].
# Erwartete Laufzeit: 30-60 Minuten — daher nightly per Cron, nicht PR-Pflicht.
# scripts/dev/run_mutmut.py umgeht den ``set_start_method``-Konflikt aus
# mutmut 3.5 (Refs #930).
mutation:
	$(PYTHON) scripts/dev/run_mutmut.py run

# Ergebnisse des letzten Mutation-Runs anzeigen (textuell, nicht-interaktiv).
mutation-report:
	$(PYTHON) -m mutmut results

# ── Release-Helfer (dev-only, Refs #1078/#1051): Skripte liegen unter
#    dev-ops/release/ und sind nicht im Public-Snapshot — analog `make mutation`.
# Lokale Replikation der Stage/App-only-CI-Gates (W3/W4-Lehre)
release-gates:
	bash dev-ops/release/release-gates.sh

# Read-only-Preflight vor Release-Beginn
release-preflight:
	bash dev-ops/release/release-preflight.sh

# Public-Verifikation nach App-Push: make release-verify-public TAG=vX.Y.Z
release-verify-public:
	bash dev-ops/release/release-verify-public.sh "$(TAG)"

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

# Manual-Test-Matrix-Index neu generieren + Anhang C der Matrix mit Per-
# Bereich-Coverage befüllen (Refs #909, #916).
test-matrix-index:
	$(PYTHON) scripts/build_test_matrix_index.py

# CI-Check: failt, wenn der Index oder Anhang C nicht mehr zur Matrix passen.
# Ergänzt zu Pre-Commit-Hooks oder einem optionalen ci-Schritt.
test-matrix-index-check:
	$(PYTHON) scripts/build_test_matrix_index.py --check

# Generated artefacts loswerden (Refs #896).
# Nicht angefasst: src/media/ (Datenverlustrisiko) und .venv/.
clean:
	@echo "Räume generierte Artefakte auf…"
	@find . -type d -name __pycache__ -not -path './.venv/*' -not -path './node_modules/*' -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -not -path './.venv/*' -not -path './node_modules/*' -delete 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache .mypy_cache 2>/dev/null || true
	@rm -rf src/staticfiles 2>/dev/null || true
	@echo "  ✓ __pycache__ entfernt"
	@echo "  ✓ *.pyc entfernt"
	@echo "  ✓ .pytest_cache / .ruff_cache / .mypy_cache entfernt"
	@echo "  ✓ src/staticfiles entfernt"
	@echo "  • src/media bleibt unberührt (Datenverlustrisiko — manuell prüfen)"

# Alles zusammen
dev: db migrate run

# === dev.anlaufstelle.app deploy-Targets (Refs #671) ===
# DEV_HOST darf in .env.deploy (gitignored) gesetzt werden, damit
# nicht jedes Make-Aufruf das Argument mitschleppt.
-include .env.deploy
DEV_HOST ?= <ssh-user>@dev.anlaufstelle.app

# Erstmaliges Server-Hardening (idempotent): laeuft als root und legt den
# anlaufstelle-User an. Ab dem zweiten Aufruf laeuft es als anlaufstelle@.
dev-bootstrap:
	scp dev-ops/deploy/bootstrap.sh root@$(word 2,$(subst @, ,$(DEV_HOST))):/root/bootstrap.sh
	ssh root@$(word 2,$(subst @, ,$(DEV_HOST))) bash /root/bootstrap.sh

# Hauptdeploy: sync compose+caddy+deploy/+dev-ops/deploy/, dann pull/migrate/up.
deploy-dev:
	DEV_HOST=$(DEV_HOST) ./dev-ops/deploy/deploy-dev.sh

# Live-Logs vom web- und caddy-Container.
dev-logs:
	ssh $(DEV_HOST) 'cd /opt/anlaufstelle && docker compose -f docker-compose.dev.yml --env-file .env.dev logs -f --tail=200 web caddy'

# Django-Shell auf dev (interaktiv).
dev-shell:
	ssh -t $(DEV_HOST) 'cd /opt/anlaufstelle && docker compose -f docker-compose.dev.yml --env-file .env.dev exec web python manage.py shell'

# Demo-Daten einspielen (idempotent). Args via SEED_ARGS, z.B.
#   make dev-seed SEED_ARGS="--flush --scale=medium"
# Connection als POSTGRES_ADMIN_USER (BYPASSRLS), damit seed in
# RLS-geschuetzte Tabellen schreiben kann ohne app.current_facility_id-
# Bootstrap-Henne-Ei. Refs #863.
#
# --entrypoint python: docker-entrypoint.sh ist seit Refs #802 hartcodiert auf
# `exec gunicorn` und ignoriert das uebergebene Kommando — ohne Override startet
# `run web python manage.py seed` nur den Webserver und seedet nie. -T + </dev/null
# entkoppeln den Job vom STDIN (Refs #976); kein `ssh -t`, sonst Haenger ohne TTY.
SEED_ARGS ?=
dev-seed:
	ssh $(DEV_HOST) 'cd /opt/anlaufstelle && \
	  set -a && . ./.env.dev && set +a && \
	  docker compose -f docker-compose.dev.yml --env-file .env.dev run --rm -T \
	    --entrypoint python \
	    -e POSTGRES_USER="$$POSTGRES_ADMIN_USER" \
	    -e POSTGRES_PASSWORD="$$POSTGRES_ADMIN_PASSWORD" \
	    web manage.py seed $(SEED_ARGS) </dev/null'

# Manueller Backup-Snapshot (Cron macht das eigenstaendig).
dev-backup:
	ssh -t $(DEV_HOST) 'sudo /opt/anlaufstelle/dev-ops/deploy/backup.sh'

# Compose-Status + Healthcheck.
dev-status:
	ssh $(DEV_HOST) 'cd /opt/anlaufstelle && docker compose -f docker-compose.dev.yml --env-file .env.dev ps'
	curl -sS -I https://dev.anlaufstelle.app/health/ | head -5
