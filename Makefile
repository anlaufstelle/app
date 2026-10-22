PYTHON ?= .venv/bin/python
E2E_WORKERS ?= 2

# Unit-Testlauf-Baustein von `ci` (Refs #1953): Default test-parallel;
# `ci-release` setzt test-parallel-cov, damit die Suite im Release-/
# Checkpoint-Modus EINMAL laeuft und dabei das Coverage-Artefakt fuer
# `SKIP_UNIT=1 make release-gates` erzeugt — statt zweier Voll-Laeufe
# (test-parallel + Coverage-Gate) pro Gate-Pass.
CI_TEST_TARGET ?= test-parallel

# One host-wide lock for all worktrees sharing CPU, E2E database and port 8844.
# Set CI_LOCK consistently for every session when overriding the portable default.
CI_LOCK ?= $(or $(XDG_RUNTIME_DIR),$(TMPDIR),/tmp)/anlaufstelle-ci.lock
# 4 h — am laengsten bekannten Gate-Lauf ausgerichtet. Zu kurz hiesse stiller
# Fehlschlag mitten im Lauf, zu lang hiesse haengende Sessions.
CI_LOCK_TIMEOUT ?= 14400
export CI_LOCK CI_LOCK_TIMEOUT

# Schwelle des JS-Advisory-Gates ``audit-js`` (Refs #1968). Der Default bleibt
# blockierend bei ``moderate``; die Variable ist die Notbremse fuer den einen
# Fall, in dem das Gate sonst unaufloesbar waere: eine frisch veroeffentlichte
# ``moderate``-Advisory OHNE verfuegbaren Fix blockiert ab ihrer
# Veroeffentlichung jeden Commit. ``make ci AUDIT_LEVEL=high`` haelt den Gate-
# Lauf dann am Laufen, ohne dass jemand das Rezept patchen muss.
AUDIT_LEVEL ?= moderate

.PHONY: dev setup db tailwind migrate run run-http ssl-cert seed ci ci-locked test-e2e-locked test-e2e-halves-locked test-e2e-slice test-e2e-slice-locked verify-fast build lint typecheck test test-js test-e2e test-e2e-halves test-focus test-parallel test-e2e-parallel test-e2e-smoke test-a11y test-a11y-locked check deps-lock deps-check audit-js maintenance-on maintenance-off deploy-dev dev-bootstrap dev-logs dev-shell dev-seed dev-backup dev-status deploy-demo demo-seed demo-status demo-logs clean test-matrix-index test-matrix-index-check verify-matrix-drift verify-translation-versions verify-release-test-guard mutation mutation-report worktree worktree-rm ci-coverage docs-screens release-gates release-preflight release-verify-public release-verify-tags verify-vendor-js-sync verify-agent-docs-sync sync-vendor-js verify-css-fresh ci-release ci-release-locked test-parallel-cov test-e2e-parallel-locked changelog-check release-stamp release-stamp-check mutation-delta mutation-skipped dco-check

.PHONY: i18n-check i18n-update

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
		-p 127.0.0.1:5432:5432 \
		postgres:18-alpine

db-stop:
	docker stop anlaufstelle-db && docker rm anlaufstelle-db

# Tailwind CSS kompilieren (v4-CLI, Refs #1480)
tailwind:
	npx @tailwindcss/cli -i src/static/css/input.css -o src/static/css/styles.css --watch

tailwind-build:
	npx @tailwindcss/cli -i src/static/css/input.css -o src/static/css/styles.css --minify

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

# Selbstsigniertes Zertifikat generieren.
# Fuer LAN-Tests von Mobilgeraeten/PWA die eigene LAN-IP explizit setzen:
#   SSL_HOST_IP=192.168.x.y make ssl-cert
# Kein Default (kein realer Host im Repo) — ohne SSL_HOST_IP deckt das
# Zertifikat nur localhost/127.0.0.1/0.0.0.0 ab.
SSL_HOST_IP ?=
ssl-cert:
	@mkdir -p certs
	@san="DNS:localhost,IP:127.0.0.1,IP:0.0.0.0"; \
	if [ -n "$(SSL_HOST_IP)" ]; then \
		san="$$san,IP:$(SSL_HOST_IP)"; \
		echo "LAN-IP im Zertifikat: $(SSL_HOST_IP)"; \
	else \
		echo "Hinweis: SSL_HOST_IP nicht gesetzt — Zertifikat nur fuer localhost/127.0.0.1/0.0.0.0."; \
		echo "         Fuer LAN-/Mobil-Tests: SSL_HOST_IP=192.168.x.y make ssl-cert"; \
	fi; \
	openssl req -x509 -newkey rsa:2048 -keyout certs/dev-key.pem -out certs/dev-cert.pem \
		-days 365 -nodes -subj "/CN=localhost" \
		-addext "subjectAltName=$$san"
	@echo "Zertifikat erstellt: certs/dev-cert.pem + certs/dev-key.pem"

seed:
	$(PYTHON) src/manage.py seed

# Reorder-sicherer i18n-Vollabgleich (Refs #1915) — Teil von `make ci`/
# `make ci-locked` (Refs #1933): erkennt neue/entfernte/semantisch geaenderte
# msgids gegen den echten Quellbaum, ohne PO-Reorder oder Schreibzugriff.
# Insbesondere faengt es einen msgid-Verlust ab, der nur zur Laufzeit
# referenziert wird (z.B. eine Formular-Fehlermeldung) und von keinem der
# uebrigen Kataloggates (fuzzy/leer/Version, .mo-Frische) gesehen wird.
i18n-check:
	$(PYTHON) scripts/i18n_catalog.py check

i18n-update:
	$(PYTHON) scripts/i18n_catalog.py update

# Shared preparation and owned server lifecycle for manual E2E and screenshots.
# The lock is held by this shell until the server exits, including seed --flush.
DSM_E2E := DJANGO_SETTINGS_MODULE=anlaufstelle.settings.e2e
define E2E_SERVER_SESSION
	@set -eu; \
	exec 9>"$(CI_LOCK)"; flock -w $(CI_LOCK_TIMEOUT) 9; \
	$(PYTHON) -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 8844)); s.close()'; \
	$(DSM_E2E) $(PYTHON) src/manage.py migrate --run-syncdb; \
	$(DSM_E2E) $(PYTHON) src/manage.py seed --flush --scale=medium; \
	$(DSM_E2E) $(PYTHON) src/manage.py collectstatic --noinput; \
	$(DSM_E2E) $(PYTHON) -m gunicorn anlaufstelle.wsgi:application \
		--bind 127.0.0.1:8844 --workers 2 --threads 2 --chdir src --timeout 120 & \
	server_pid=$$!; \
	trap 'kill "$$server_pid" 2>/dev/null || true; wait "$$server_pid" 2>/dev/null || true' EXIT; \
	trap 'exit 130' INT; trap 'exit 143' TERM; \
	if [ "$(1)" = "screenshots" ]; then \
		sleep 5; kill -0 "$$server_pid"; \
		$(DSM_E2E) $(PYTHON) src/manage.py screenshot --base-url http://127.0.0.1:8844; \
	else wait "$$server_pid"; fi
endef

.PHONY: e2e-server
# Foreground manual server; stop with Ctrl-C to release the global lock.
e2e-server:
	$(call E2E_SERVER_SESSION,manual)

# Generate DE/EN desktop/mobile WebP screenshots with the same global lock.
docs-screens:
	$(call E2E_SERVER_SESSION,screenshots)
	@echo "Screenshots in docs/screenshots/"

# CI lokal
# Refs #860: scripts/ mitgescannt — die check_*.py-Helfer landeten sonst nur
# im pre-commit-Hook, nicht im 'make ci' / Pre-Push-Pfad.
lint:
	$(PYTHON) -m ruff check src/ scripts/
	$(PYTHON) -m ruff format --check src/ scripts/

dco-check: RANGE ?= origin/main..HEAD
dco-check:
	@if [ -f scripts/dev/check_dco.py ]; then $(PYTHON) scripts/dev/check_dco.py $(RANGE); else echo "dco-check: not available in the public source tree (maintainer tool)"; fi

# mypy auf core/services strikt, Rest mit permissiver Baseline (Refs #741).
# Erweiterung modulweise via [[tool.mypy.overrides]] in pyproject.toml.
typecheck:
	$(PYTHON) -m mypy src/core/services src/core/forms src/core/packages src/core/management/commands

# Maintenance-Mode (Refs #700). Aktiviert per File-Flag — bei nicht
# gesetztem MAINTENANCE_FLAG_FILE-Env-Var wird /tmp/anlaufstelle.maintenance
# als sinnvoller Default genutzt.
MAINTENANCE_FLAG ?= $(or $(MAINTENANCE_FLAG_FILE),/tmp/anlaufstelle.maintenance)

maintenance-on:
	@touch $(MAINTENANCE_FLAG) && echo "Maintenance-Mode AKTIV ($(MAINTENANCE_FLAG))"

maintenance-off:
	@rm -f $(MAINTENANCE_FLAG) && echo "Maintenance-Mode aus ($(MAINTENANCE_FLAG))"

test: verify-css-fresh
	$(PYTHON) -m pytest -m "not e2e"

# Demo uses its own server session and port under the shared host lock.
# Literal make keeps -n from executing flock via recursive-make handling (Refs #2531).
.PHONY: test-e2e-demo
test-e2e-demo:
	flock -w $(CI_LOCK_TIMEOUT) "$(CI_LOCK)" make tailwind-build
	flock -w $(CI_LOCK_TIMEOUT) "$(CI_LOCK)" env E2E_DEMO_MODE=1 E2E_SEED_SCALE=demo E2E_PORT_BASE=8854 $(PYTHON) -m pytest src/tests/e2e/test_demo_scenario_switch.py src/tests/e2e/test_system_compliance_maintenance_results.py::TestDemoCards -m e2e --browser chromium --timeout=120 --timeout-method=thread -x -v -rs

# --timeout-method=thread ist fuer die E2E-Laeufe Pflicht (Refs #1606): Stirbt
# Chromium unter CPU-Knappheit weg, dreht die Playwright-Sync-API in einem
# Greenlet frei — ausserhalb jeder Python-Ebene, die die Default-``signal``-
# Methode (SIGALRM im Main-Thread) je erreicht. Die ``thread``-Methode laeuft in
# einem eigenen Timer-Thread, dumpt die Stacks und beendet den Prozess hart
# (``os._exit(1)``); so wird der Endlos-Dreher wenigstens zum Fehlschlag statt
# zum Haenger. Gilt seriell wie parallel — der Hang haengt an der CPU-Knappheit,
# nicht an xdist (Passkey-Roundtrip #1606 hing seriell nur nicht, weil der
# Release-Host 8 Kerne hat).
# Prerequisite ``tailwind-build`` (Refs #1712): E2E lief bisher gegen ein
# ggf. veraltetes styles.css, wenn seit der letzten Template-Klassenaenderung
# nicht manuell neu gebaut wurde (Assertions gegen sichtbare Klassen/Layout
# faellig-still). Beseitigt die Falle an der Wurzel statt sie zu dokumentieren.
test-e2e: tailwind-build
	$(PYTHON) -m pytest -m e2e --browser chromium --timeout=120 --timeout-method=thread -v

# Zwei-Haelften-Fallback fuer RAM-limitierte Hosts (Refs #1787): zwei serielle
# pytest-Sessions statt einer, jede mit eigenem Server+Seed und eigenem
# Speicher-Budget. Greift, wenn der Voll-Lauf trotz Browser-Recycling
# (modul-scoped ``browser``-Fixture) noch ins Host-OOM laeuft.
#
# Since Refs #1976 the cut comes from scripts/e2e_slice.py -- the same
# splitter the CI matrix (.github/workflows/e2e.yml) uses for its slices. The
# local fallback and CI therefore share the split key: same file grouping,
# same ordering dependencies, and a failed CI slice can be replayed here 1:1
# (`make test-e2e-slice-locked SLICES=8 INDEX=3`). The cut formerly computed
# here ("first/second half of the sorted list") was deterministic but skewed
# -- 335 against 492 tests (+47 %), because file names say nothing about the
# number of tests inside.
#
# Das Rezept ist EINE Shell (``; \``), damit ``set -e`` ueber alle Zeilen gilt
# und die zweite Haelfte nicht den Exit-Code der ersten maskiert.
#
# Die Leer-Guards sind kein Zierrat: ``pytest`` OHNE Dateiargumente sammelt die
# **gesamte** Suite ein. Ein fehlgeschlagener oder leerer Schnitt liefe damit
# still als doppelter Voll-Lauf durch -- genau die Situation, gegen die dieses
# Target gebaut ist, nur unbemerkt und mit doppelter Laufzeit.
test-e2e-halves: tailwind-build
	set -e; \
	h1=$$($(PYTHON) scripts/e2e_slice.py --slices 2 --index 1); \
	h2=$$($(PYTHON) scripts/e2e_slice.py --slices 2 --index 2); \
	[ -n "$$h1" ] || { echo "test-e2e-halves: erste Haelfte leer"; exit 1; }; \
	[ -n "$$h2" ] || { echo "test-e2e-halves: zweite Haelfte leer"; exit 1; }; \
	$(PYTHON) -m pytest $$h1 -m e2e --browser chromium --timeout=120 --timeout-method=thread -v; \
	$(PYTHON) -m pytest $$h2 -m e2e --browser chromium --timeout=120 --timeout-method=thread -v

# Replay a single slice of the CI cut locally (Refs #1976):
#
#     make test-e2e-slice-locked SLICES=8 INDEX=3
#
# SLICES defaults to the slice count of the CI matrix, so the number in the
# failed job name ("E2E-Scheibe 3") fits without conversion. Which files sit
# in which slice is shown by `python scripts/e2e_slice.py --slices 8
# --summary`. As with the halves: no file argument means a full run, hence the
# emptiness guard.
SLICES ?= 8
test-e2e-slice: tailwind-build
	set -e; \
	[ -n "$(INDEX)" ] || { echo "test-e2e-slice: INDEX=<1..$(SLICES)> angeben"; exit 1; }; \
	files=$$($(PYTHON) scripts/e2e_slice.py --slices $(SLICES) --index $(INDEX)); \
	[ -n "$$files" ] || { echo "test-e2e-slice: leerer Schnitt"; exit 1; }; \
	$(PYTHON) -m pytest $$files -m e2e --browser chromium --timeout=120 --timeout-method=thread -v

test-focus:
	$(PYTHON) -m pytest -m "not e2e" -x $(T)

test-parallel: verify-css-fresh
	$(PYTHON) -m pytest -m "not e2e" -n auto -x

# Wie test-parallel, zusaetzlich mit Coverage-Messung + 96-%-Schwelle (Spiegel
# des release-gates-Coverage-Gates inkl. --ignore, Refs #1953). Erzeugt
# .coverage im Repo-Root — das Artefakt, das `SKIP_UNIT=1 make release-gates`
# per scripts/dev/check_coverage_artifact.py prueft, statt die Suite fuer die
# Messung ein zweites Mal zu fahren.
# Der Provenienz-Stempel bindet das Artefakt an den INHALT von HEAD
# (HEAD^{tree}), nicht nur an die Zeit: geschrieben wird er NUR nach gruenem
# Lauf (Make bricht bei rotem pytest vor der Stempel-Zeile ab; der alte
# Stempel ist dann bereits geloescht) und NUR bei sauberem src/ — ein rotes
# oder auf fremdem Stand erzeugtes .coverage kann das SKIP_UNIT-Gate damit
# nie passieren.
test-parallel-cov:
	@rm -f .coverage.stamp
	$(PYTHON) -m pytest -m "not e2e" --ignore=src/tests/e2e -n auto -x --cov=core --cov-report=term-missing --cov-fail-under=96
	@if [ -z "$$(git status --porcelain -- src/)" ]; then \
		git rev-parse 'HEAD^{tree}' > .coverage.stamp; \
	else \
		echo "test-parallel-cov: src/ nicht sauber — kein Provenienz-Stempel (SKIP_UNIT-Gate verlangt dann den Voll-Lauf)"; \
	fi

# Vitest-Suite deterministisch/non-watch ausfuehren (Refs #1932). ``npm test``
# entspricht ``vitest run`` (siehe package.json ``scripts.test``) — kein
# Watch-Modus, Exit-Code spiegelt den Testausgang 1:1. Klare, gezielte
# Fehlermeldung statt des kryptischen ``sh: vitest: not found``, wenn
# node_modules/ (analog dem ``.venv``-Symlink je Worktree) oder das Lockfile
# fehlen — kein automatisches ``npm ci`` hier, dieselbe Konvention wie bei
# ``verify-css-fresh``/``sync-vendor-js`` (node_modules-Stand ist Vorbedingung,
# kein Seiteneffekt des Gates).
test-js:
	@if [ ! -f package-lock.json ]; then \
		echo "test-js: package-lock.json fehlt — kein deterministischer Node-Stand moeglich." >&2; \
		exit 1; \
	fi
	@if [ ! -x node_modules/.bin/vitest ]; then \
		echo "test-js: node_modules/ fehlt oder ist unvollstaendig — zuerst 'npm ci' ausfuehren." >&2; \
		exit 1; \
	fi
	npm test

# JavaScript-Advisory-Gate (Refs #1968). Gegenstueck zu ``pip-audit`` auf der
# Python-Seite — fuer JS gab es bis dahin gar keins, weshalb Advisories in
# ``nanoid``/``postcss``/``undici`` mehrere Releases lang unbemerkt im Lockfile
# standen.
#
# Bewusst HIER und nicht nur in ``.github/workflows/test.yml``: der Workflow
# haengt am Actions-Guthaben des Dev-Repos und ruht faktisch; ``make ci`` ist
# das reale Tor. ``npm audit`` liest ausschliesslich package-lock.json —
# node_modules/ ist nicht noetig, eine Netzverbindung zur Registry schon.
#
# Schwelle ``moderate``, nicht ``high``: die Advisory, die dieses Gate
# ausgeloest hat (PostCSS, sourceMappingURL), war selbst nur ``moderate`` —
# ein High-Only-Gate liesse genau die Klasse durch, die den Befund erzeugt hat.
# Die Schwelle haengt an ``AUDIT_LEVEL`` (Default oben), damit eine fix-lose
# Advisory nicht jeden Commit blockiert, bis ein Patch erscheint.
# Kein ``--omit=dev``: saemtliche Node-Pakete des Projekts sind
# devDependencies (Tailwind-CLI + Vitest-Toolchain), das Gate waere sonst leer.
audit-js:
	@if [ ! -f package-lock.json ]; then \
		echo "audit-js: package-lock.json fehlt — ohne Lockfile ist kein Advisory-Abgleich moeglich." >&2; \
		exit 1; \
	fi
	npm audit --audit-level=$(AUDIT_LEVEL)

# --timeout-method=thread wie bei ``test-e2e`` (Refs #1606): harter Kill des
# Workers gegen den Greenlet-Endlos-Dreher bei totem Chromium — unter -n 2 auf
# 2 Kernen der reproduzierte Haenger.
# Prerequisite ``tailwind-build`` (Refs #1712/#1953): dieselbe Stale-CSS-Falle
# wie bei ``test-e2e`` — das parallele Target lief bisher als einziges ohne
# CSS-Rebuild und konnte gegen veraltete styles.css false-gruen/false-rot laufen.
test-e2e-parallel: tailwind-build
	$(PYTHON) -m pytest -m e2e --browser chromium -n $(E2E_WORKERS) --dist loadfile --timeout=120 --timeout-method=thread -v

test-e2e-smoke:
	$(PYTHON) -m pytest -m "e2e and smoke" --browser chromium -v

# WebKit-Smoke der Offline-Kernsuite (Refs #1418) — seltener, SERIELL (RAM-Limit,
# bewusst kein -n) auf Safari/WebKit, wo die riskanten Feld-Eigenheiten liegen:
# IndexedDB-ITP-Eviction, aggressiver SW-Lebenszyklus, navigator.locks fuer den
# Sync-Orchestrator, storage.persist. NICHT im Default-Gate (`make ci` laesst e2e aus).
# Einmalige Voraussetzung:  playwright install webkit --with-deps
# Ergaenzender manueller iOS/Android-Durchlauf: docs/testing/manual-test-matrix.md Anhang G (#1319).
test-e2e-webkit-smoke:
	$(PYTHON) -m pytest -m "e2e and offline_smoke" --browser webkit -v

# axe-core-WCAG-Gate (Refs #1059). Prerequisite ``tailwind-build`` wie bei
# ``test-e2e`` (Refs #1712): axe misst u.a. Farbkontraste am gerenderten
# Stylesheet — gegen ein veraltetes styles.css waere jeder Kontrast-Befund
# (und jedes Ausbleiben eines Befunds) wertlos.
# Bewusst SERIELL (kein -n): die Suite haengt am gemeinsamen E2E-Server, und
# das optionale Report-Artefakt (AXE_REPORT_PATH) wird read-modify-write
# geschrieben.
# Voll-Artefakt aller Impact-Stufen fuer den Audit-Fan-out:
#   make test-a11y-locked AXE_REPORT_PATH=/pfad/report.json
test-a11y: tailwind-build
	$(PYTHON) -m pytest -m "e2e and a11y" --browser chromium --timeout=120 --timeout-method=thread -v

check:
	$(PYTHON) src/manage.py check
	$(PYTHON) src/manage.py makemigrations --check --dry-run

ci: lint check changelog-check deps-check audit-js verify-matrix-drift test-matrix-index-check verify-translation-versions i18n-check verify-release-test-guard verify-vendor-js-sync verify-agent-docs-sync typecheck verify-css-fresh test-js $(CI_TEST_TARGET)

# Serialisierte Varianten der ressourcenschweren Gates (Refs #1840).
# Wer einen vollen Gate-Lauf startet, nimmt DIESE Targets — sonst kollidieren
# zwei Sessions am selben Host-RAM, an derselben E2E-Datenbank und an Port 8844.
# Genau das ist am 2026-08-11 passiert: beide Sessions hielten ein flock, nur
# eben nicht dasselbe. Wrapper statt flock direkt in `ci`, damit die bestehenden
# Targets unberuehrt bleiben und lockfreie Laeufe moeglich sind, wo sie richtig
# sind (CI-Runner, Einzelhost).
ci-locked:
	flock -w $(CI_LOCK_TIMEOUT) $(CI_LOCK) $(MAKE) ci

test-e2e-locked:
	flock -w $(CI_LOCK_TIMEOUT) $(CI_LOCK) $(MAKE) test-e2e

test-a11y-locked:
	flock -w $(CI_LOCK_TIMEOUT) $(CI_LOCK) $(MAKE) test-a11y

test-e2e-halves-locked:
	flock -w $(CI_LOCK_TIMEOUT) $(CI_LOCK) $(MAKE) test-e2e-halves

test-e2e-slice-locked:
	flock -w $(CI_LOCK_TIMEOUT) $(CI_LOCK) $(MAKE) test-e2e-slice SLICES=$(SLICES) INDEX=$(INDEX)

# Release-/Checkpoint-Modus (Refs #1953): identisches `ci`, aber der
# Unit-Testlauf traegt die Coverage-Messung gleich mit (test-parallel-cov).
# Danach ersetzt `SKIP_UNIT=1 make release-gates` den zweiten Voll-Lauf durch
# den Artefakt-Check — EIN Unit-Lauf pro Gate-Pass statt zwei.
ci-release:
	$(MAKE) ci CI_TEST_TARGET=test-parallel-cov

ci-release-locked:
	flock -w $(CI_LOCK_TIMEOUT) $(CI_LOCK) $(MAKE) ci-release

test-e2e-parallel-locked:
	flock -w $(CI_LOCK_TIMEOUT) $(CI_LOCK) $(MAKE) test-e2e-parallel

# Schneller Gate: statische Prüfung + Unit-Ebene, ohne E2E/Browser.
# Enge Feedback-Schleife + Pre-Commit. Beziehung: ci ⊇ verify-fast
# (ci ergänzt deps-check + die drei Drift-Guards). Refs #1401.
verify-fast: lint check typecheck test-parallel

# Deploybare Artefakte deterministisch erzeugen: minifiziertes CSS + statische
# Dateien. Settings überschreibbar (BUILD_SETTINGS=…), Default = prod wie im
# Release-Pfad. Refs #1401.
BUILD_SETTINGS ?= anlaufstelle.settings.prod
build: tailwind-build
	DJANGO_SETTINGS_MODULE=$(BUILD_SETTINGS) $(PYTHON) src/manage.py collectstatic --noinput

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

# EN-Doku-Sync gegen den ECHTEN Baum (translation-version + source-hash).
# Ergaenzt src/tests/test_translation_source_hash_guard.py, das bewusst nur
# tmp-Fixtures prueft — #1869 (stale Stempel nach E4-Nacharbeit) blieb dadurch
# lokal unsichtbar; der reale Check lief nur im (deaktivierten) GH-Workflow.
# Refs #1869.
verify-translation-versions:
	$(PYTHON) scripts/check_translation_versions.py

# CHANGELOG-Hygiene-Gate (Refs #1953): haelt `[Unreleased]` kontinuierlich
# sauber (Kategorie-Vokabular, keine Duplikate, Issue-Refs) — statt des
# Batch-Aufraeumens am Release-Cut (Befund beim v0.81-Cut: Duplikat-Paare,
# fehlende Sektionen). Leeres/fehlendes `[Unreleased]` ist gruen (frischer
# Post-Release-Stand).
changelog-check:
	$(PYTHON) scripts/check_changelog_unreleased.py

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

# Frische-Guard fuer das untrackte Build-Artefakt src/static/css/styles.css
# (Refs #1712): baut Tailwind minifiziert in eine Temp-Datei und byte-
# vergleicht (cmp) gegen das ausgecheckte styles.css. Faengt den Fall, dass
# Template-Klassen geaendert wurden, aber 'make tailwind-build' vor dem Commit
# vergessen wurde — sonst laeuft CI/E2E lokal grau gegen ein stale Stylesheet.
verify-css-fresh:
	@tmp=$$(mktemp --suffix=.css) && \
	npx @tailwindcss/cli -i src/static/css/input.css -o "$$tmp" --minify >/dev/null 2>&1; \
	if [ ! -f src/static/css/styles.css ] || ! cmp -s "$$tmp" src/static/css/styles.css; then \
		rm -f "$$tmp"; \
		echo "src/static/css/styles.css ist stale — make tailwind-build ausführen" >&2; \
		exit 1; \
	fi; \
	rm -f "$$tmp"; \
	echo "✓ styles.css ist frisch"

# Drift-Guard (Refs #1403): AGENTS.md ist die tool-neutrale SSOT der Agent-
# Konventionen; CLAUDE.md verweist darauf und dupliziert sie nicht (12.11).
#
# Public-Tree-Verhalten: build-release strippt AGENTS.md + CLAUDE.md, das
# Guard-Skript (scripts/, nicht scripts/dev/) bleibt aber im Snapshot. Ohne
# die zu pruefenden Dateien wuerde `make ci` im Public-/Stage-Tree strukturell
# failen. Darum hier ein sauberer Skip mit Hinweis, wenn AGENTS.md ODER das
# Skript fehlt (Doku: docs/dev/release-exclusions.md).
verify-agent-docs-sync:
	@if [ -f AGENTS.md ] && [ -f scripts/dev/verify_agent_docs_sync.py ]; then \
		$(PYTHON) scripts/dev/verify_agent_docs_sync.py; \
	else \
		echo "verify-agent-docs-sync: uebersprungen — AGENTS.md und/oder scripts/dev/verify_agent_docs_sync.py fehlen (Public-/Release-Tree; Verhalten dokumentiert in docs/dev/release-exclusions.md)."; \
	fi

# Vendored JS-Libs aus node_modules/ neu kopieren (Refs #1076). Nach einem
# Dependabot-Bump: erst 'npm ci', dann dieses Target, dann src/static/js/
# committen. node_modules/ ist gitignored.
sync-vendor-js:
	$(PYTHON) scripts/sync_vendor_js.py

# Mutation-Testing für core/services + core/forms (Refs #922 / #923).
# Konfiguration in pyproject.toml [tool.mutmut].
# Full measurements take several hours; the nightly workflow is paused (ADR-029).
# Maintainer-/dev-only: scripts/dev/run_mutmut.py wird aus dem Public-Snapshot
# gestrippt (analog die Release-Helfer unten), dieses Target läuft nur im Dev-Repo.
# Der Wrapper umgeht den ``set_start_method``-Konflikt aus mutmut 3.5 (Refs #930).
mutation:
	@if [ -f scripts/dev/run_mutmut.py ]; then $(PYTHON) scripts/dev/run_mutmut.py run; else echo "mutation: not available in the public source tree (maintainer tool)"; fi

# Delta-Mutation (Refs #1953, Maintainer-Entscheid 2026-08-19): nur die seit
# BASE geaenderten Dateien der Mutations-Scopes mutieren — laeuft am
# Meilenstein-Checkpoint im Hintergrund statt im Cut-Pfad. Der Voll-Lauf
# bleibt 1x vor v1.0 Pflicht (docs/testing/mutation-testing.md § Score-Ziele).
mutation-delta:
ifneq ($(wildcard scripts/dev/run_mutmut.py),)
	@[ -n "$(BASE)" ] || { echo "mutation-delta: BASE=<tag/ref> required"; exit 2; }
endif
	@if [ -f scripts/dev/run_mutmut.py ]; then $(PYTHON) scripts/dev/run_mutmut.py run --since "$(BASE)"; \
	else echo "mutation-delta: not available in the public source tree (maintainer tool)"; fi

# Ergebnisse des letzten Mutation-Runs anzeigen (textuell, nicht-interaktiv).
mutation-report:
	@if [ -f scripts/dev/run_mutmut.py ]; then $(PYTHON) -m mutmut results; else echo "mutation-report: not available in the public source tree (maintainer tool)"; fi

# Von mutmut strukturell uebersprungene Funktionen ausweisen (Refs #2098).
# Maintainer-/dev-only: Das Skript wird aus dem Public-Snapshot gestrippt.
# Braucht keinen Mutationslauf; der Bericht liest ausschliesslich den Quelltext.
mutation-skipped:
	@if [ -f scripts/dev/mutation_skipped_functions.py ]; then $(PYTHON) scripts/dev/mutation_skipped_functions.py; else echo "mutation-skipped: not available in the public source tree (maintainer tool)"; fi

# Versions-Stempel des Doc-Sync §1.2 (Refs #1953): schreibt alle "Immer"-
# Stellen (SECURITY.md-Tabelle, threat-model-Header, README-Banner,
# compose-APP_VERSION-Fallbacks, Stand-Zeile, …) idempotent auf VERSION —
# ersetzt die Handarbeit, `check_translation_versions.py` bleibt die
# unabhaengige Nachkontrolle.
release-stamp:
	@[ -n "$(VERSION)" ] || { echo "release-stamp: VERSION=X.Y.Z fehlt"; exit 2; }
	$(PYTHON) scripts/dev/release_stamp.py --version "$(VERSION)"

release-stamp-check:
	@[ -n "$(VERSION)" ] || { echo "release-stamp-check: VERSION=X.Y.Z fehlt"; exit 2; }
	$(PYTHON) scripts/dev/release_stamp.py --version "$(VERSION)" --check

# Worktree-Workflow (dev-only, Refs #1240): ein Worktree pro Feature als
# Geschwister-Ordner ../anlaufstelle-<NAME>. scripts/dev/worktree.sh wird —
# wie run_mutmut.py — aus dem Public-Snapshot gestrippt. Doku:
# docs/ai/agent-workflow.md § "Worktrees für parallele Agents".
#   make worktree NAME=<slug> [BRANCH=<branch>] [WT_FLAGS=--own-deps]
worktree:
	bash scripts/dev/worktree.sh new "$(NAME)" $(BRANCH) $(WT_FLAGS)

# Worktree wieder entfernen (git worktree remove + prune).
#   make worktree-rm NAME=<slug>
worktree-rm:
	bash scripts/dev/worktree.sh rm "$(NAME)"

# ShellCheck uses the locked development tool from the Python environment.
# A bare interpreter name (PYTHON=python3) has no directory of its own; then
# PATH stays untouched instead of gaining the working directory.
SHELLCHECK_PATH_PREFIX = $(if $(filter ./,$(dir $(PYTHON))),,$(abspath $(dir $(PYTHON))):)
.PHONY: shellcheck
shellcheck:
	PATH="$(SHELLCHECK_PATH_PREFIX)$$PATH" $(PYTHON) scripts/check_shell_scripts.py

# ── Release-Helfer (dev-only, Refs #1078/#1051): Skripte liegen unter
#    dev-ops/release/ und sind nicht im Public-Snapshot — analog `make mutation`.
# Local replication of the stage/app-only release gates.
release-gates:
	$(PYTHON) scripts/check_hand_check_archive.py
	bash dev-ops/release/release-gates.sh

# Read-only-Preflight vor Release-Beginn
release-preflight:
	bash dev-ops/release/release-preflight.sh

# Public-Verifikation nach App-Push: make release-verify-public TAG=vX.Y.Z
release-verify-public:
	bash dev-ops/release/release-verify-public.sh "$(TAG)"

# Tag-Push-Gate (Refs #1800): Tag auf allen drei Remotes? make release-verify-tags TAG=vX.Y.Z
release-verify-tags:
	bash dev-ops/release/release-verify-tags.sh "$(TAG)"

# Dependencies: Lock-Files aus requirements*.in neu erzeugen (pip-tools).
# Nach Änderungen an requirements.in oder requirements-dev.in ausführen.
deps-lock:
	$(PYTHON) -m piptools compile --generate-hashes --no-strip-extras --resolver=backtracking \
		--output-file=requirements.txt requirements.in
	$(PYTHON) -m piptools compile --generate-hashes --allow-unsafe --no-strip-extras --resolver=backtracking \
		--output-file=requirements-dev.txt requirements-dev.in

# Verify regenerated dependency locks without leaving generated files behind.
# Compilation failures and content drift are distinct non-zero outcomes.
deps-check:
	@cleanup() { \
			if [ -f requirements.txt.bak ]; then mv requirements.txt.bak requirements.txt; fi; \
			if [ -f requirements-dev.txt.bak ]; then mv requirements-dev.txt.bak requirements-dev.txt; fi; \
		}; \
	trap cleanup EXIT HUP INT TERM; \
	cp requirements.txt requirements.txt.bak || exit $$?; \
	cp requirements-dev.txt requirements-dev.txt.bak || exit $$?; \
	drift=0; \
	$(PYTHON) -m piptools compile --generate-hashes --quiet --no-strip-extras --resolver=backtracking \
		--output-file=requirements.txt requirements.in >/dev/null; \
	status=$$?; \
	if [ $$status -ne 0 ]; then \
		echo "pip-tools compile for requirements.in failed with exit code $$status." >&2; \
		exit $$status; \
	fi; \
	$(PYTHON) -m piptools compile --generate-hashes --allow-unsafe --quiet --no-strip-extras --resolver=backtracking \
		--output-file=requirements-dev.txt requirements-dev.in >/dev/null; \
	status=$$?; \
	if [ $$status -ne 0 ]; then \
		echo "pip-tools compile for requirements-dev.in failed with exit code $$status." >&2; \
		exit $$status; \
	fi; \
		diff -u requirements.txt.bak requirements.txt || drift=1; \
		diff -u requirements-dev.txt.bak requirements-dev.txt || drift=1; \
		cleanup; \
		trap - EXIT HUP INT TERM; \
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
# DEV_HOST (user@host des Deploy-Ziels) MUSS in .env.deploy (gitignored) oder als
# Env-Var gesetzt werden; der Default ist nur ein Platzhalter — der konkrete
# Deploy-User/-Host gehoert nicht in den oeffentlichen Snapshot. Refs #1226.
-include .env.deploy
DEV_HOST ?= deploy@your-server.example

# Erstmaliges Server-Hardening (idempotent): laeuft als root und legt den
# Deploy-User an. Ab dem zweiten Aufruf laeuft es als der konfigurierte Deploy-User.
dev-bootstrap:
	scp dev-ops/deploy/bootstrap.sh root@$(word 2,$(subst @, ,$(DEV_HOST))):/root/bootstrap.sh
	ssh root@$(word 2,$(subst @, ,$(DEV_HOST))) bash /root/bootstrap.sh

# Hauptdeploy (maintainer-/dev-only): sync compose+caddy+deploy/+dev-ops/deploy/,
# dann pull/migrate/up. Die dev-ops/deploy/-Skripte sind aus dem Public-Snapshot
# gestrippt — diese Deploy-Targets laufen nur im Dev-Repo gegen eigene Hosts.
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
	  docker compose -f docker-compose.dev.yml --env-file .env.dev run --rm -T \
	    maintenance seed $(SEED_ARGS) </dev/null'

# Manueller Backup-Snapshot (Cron macht das eigenstaendig).
dev-backup:
	ssh -t $(DEV_HOST) 'sudo /opt/anlaufstelle/dev-ops/deploy/backup.sh'

# Compose-Status + Healthcheck.
dev-status:
	ssh $(DEV_HOST) 'cd /opt/anlaufstelle && docker compose -f docker-compose.dev.yml --env-file .env.dev ps'
	curl -sS -I https://dev.anlaufstelle.app/health/ | head -5

# === demo.anlaufstelle.app deploy-Targets (Refs #1062) ===
# Eigene VPS, entkoppelt von dev (#971). Image wird AUF DEM SERVER gebaut.
# DEMO_HOST (user@host) via .env.deploy (gitignored, s.o.) oder Env setzen; der
# Default ist nur ein Platzhalter — konkreter Deploy-User/-Host nicht public. Refs #1232.
DEMO_HOST ?= deploy@your-server.example

# Hauptdeploy: build context syncen, Image bauen, migrate, up, Reset-Timer.
deploy-demo:
	DEMO_HOST=$(DEMO_HOST) ./dev-ops/deploy/deploy-demo.sh

# Demo-Daten (idempotent). SEED_ARGS, z.B. SEED_ARGS="--flush --scale=medium".
demo-seed:
	ssh $(DEMO_HOST) 'cd /opt/anlaufstelle && \
	  docker compose -f docker-compose.demo.yml --env-file .env.demo run --rm -T \
	    maintenance seed $(SEED_ARGS) </dev/null'

# Compose-Status + Healthcheck.
demo-status:
	ssh $(DEMO_HOST) 'cd /opt/anlaufstelle && docker compose -f docker-compose.demo.yml --env-file .env.demo ps'
	curl -sS -I https://demo.anlaufstelle.app/health/ | head -5

# Live-Logs.
demo-logs:
	ssh $(DEMO_HOST) 'cd /opt/anlaufstelle && docker compose -f docker-compose.demo.yml --env-file .env.demo logs -f --tail=200 web caddy'

# Contributor target overview without counts that drift with the test suite.
.PHONY: help
help:
	@echo "ci: lint, Django checks, changelog/dependency/audit/drift/test-matrix-index-check/translation/i18n/release/vendor/agent/CSS guards, mypy, JS and unit tests"
	@echo "typecheck: mypy on services, forms, packages and management commands"
	@echo "test-e2e-smoke: E2E flows marked smoke"
	@echo "dco-check / mutation*: maintainer tools; availability is checked in public trees"
	@echo "CI_LOCK: shared host lock; default XDG_RUNTIME_DIR, TMPDIR or /tmp + /anlaufstelle-ci.lock"

# File-based unit slices use the same collection and coverage path as CI (Refs #2598).
UNIT_SLICES ?= 3
UNIT_WORKERS ?= 2
UNIT_COVERAGE_DIR ?= unit-coverage
.PHONY: test-unit-slice test-unit-combine test-unit-weights

test-unit-slice:
	COVERAGE_FILE=.coverage.$(INDEX) $(PYTHON) scripts/ci/test_slices.py --slices $(UNIT_SLICES) --index $(INDEX) --log unit-durations-$(INDEX).txt --run -- -n $(UNIT_WORKERS) -x --dist worksteal --timeout=600 --timeout-method=thread --durations=0 --durations-min=0 --cov=core --cov-report= --cov-fail-under=0

test-unit-combine:
	$(PYTHON) scripts/ci/test_slices.py --coverage-dir $(UNIT_COVERAGE_DIR) --slices $(UNIT_SLICES) --test-result "$(TEST_RESULT)"
	$(PYTHON) -m coverage combine $(foreach number,$(shell seq 1 $(UNIT_SLICES)),$(UNIT_COVERAGE_DIR)/unit-coverage-$(number))
	$(PYTHON) -m coverage report --fail-under=96

test-unit-weights:
	$(PYTHON) scripts/ci/test_slices.py --durations $(DURATIONS)
