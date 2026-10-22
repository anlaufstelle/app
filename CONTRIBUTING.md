> **[English version / Englische Version](CONTRIBUTING.en.md)**

# Contributing to Anlaufstelle

[![Lint](https://github.com/anlaufstelle/app/actions/workflows/lint.yml/badge.svg)](https://github.com/anlaufstelle/app/actions/workflows/lint.yml)
[![Test](https://github.com/anlaufstelle/app/actions/workflows/test.yml/badge.svg)](https://github.com/anlaufstelle/app/actions/workflows/test.yml)
[![E2E](https://github.com/anlaufstelle/app/actions/workflows/e2e.yml/badge.svg)](https://github.com/anlaufstelle/app/actions/workflows/e2e.yml)

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)
[![Django 6.1](https://img.shields.io/badge/django-6.1-green.svg)](https://www.djangoproject.com/)
[![PostgreSQL 18](https://img.shields.io/badge/postgresql-18-blue.svg)](https://www.postgresql.org/)
[![HTMX](https://img.shields.io/badge/htmx-%E2%9C%93-blue.svg)](https://htmx.org/)
[![Alpine.js](https://img.shields.io/badge/alpine.js-%E2%9C%93-blue.svg)](https://alpinejs.dev/)
[![Tailwind CSS](https://img.shields.io/badge/tailwindcss-%E2%9C%93-blue.svg)](https://tailwindcss.com/)

[![code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)
[![Last Commit](https://img.shields.io/github/last-commit/anlaufstelle/app)](https://github.com/anlaufstelle/app/commits/main)
[![Open Issues](https://img.shields.io/github/issues/anlaufstelle/app)](https://github.com/anlaufstelle/app/issues)

Willkommen! Diese Anleitung erklärt, wie du die Entwicklungsumgebung einrichtest, wie der Code strukturiert ist und wie du Änderungen beiträgst.

---

## Inhaltsverzeichnis

1. [5-Minuten-Quick-Start (Docker)](#5-minuten-quick-start-docker)
2. [Entwicklungsumgebung einrichten](#entwicklungsumgebung-einrichten)
3. [Make-Targets](#make-targets)
4. [Coding Conventions](#coding-conventions)
5. [Plugin-Entwicklung](#plugin-entwicklung)
6. [Tests](#tests)
7. [Pull-Request-Prozess](#pull-request-prozess)
8. [Lizenz von Beiträgen](#lizenz-von-beiträgen)
9. [Architektur-Überblick](#architektur-überblick)

---

## 5-Minuten-Quick-Start (Docker)

Der schnellste Weg zu einer laufenden Anlaufstelle mit Beispieldaten — für den ersten Blick,
für ein Bug-Repro und für den Einstieg vor dem ersten Pull Request. Voraussetzung ist
ausschließlich **Docker mit Docker Compose**; Python, Node und PostgreSQL brauchst du dafür
noch nicht.

```bash
git clone https://github.com/anlaufstelle/app.git
cd app
docker compose up      # ohne Docker-Gruppenrechte: sudo docker compose up
```

Das lokale Compose bringt Datenbank und Anwendung mit, wendet die Migrationen an und lädt beim
**ersten** Start die Seed-Daten (nur in eine leere Datenbank — ein zweiter Start seedet nicht
erneut). Der einmalige Image-Bau dauert etwa eine Minute; danach ist die Anmeldung binnen rund
20 Sekunden unter **[http://localhost:8000/login/](http://localhost:8000/login/)** erreichbar.

> **Keine Vorbereitung nötig.** Du legst weder eine `.env` an noch exportierst du einen
> `ENCRYPTION_KEY` — das lokale Setup erzeugt sich seinen Schlüssel beim ersten Start selbst und
> legt ihn in einem eigenen Volume ab, damit verschlüsselte Entwicklungsdaten über Neustarts
> hinweg lesbar bleiben. Hintergrund und Abgrenzung zum Produktionspfad:
> [ADR-049](docs/adr/049-one-command-local-bootstrap.md).
>
> **Nur lokal erreichbar:** Der Webdienst bindet bewusst auf `127.0.0.1`, es gibt also keinen
> Zugriff aus dem LAN. Das ist Absicht — die Seed-Konten haben ein öffentlich dokumentiertes
> Passwort.

**Anmelden.** Alle Seed-Konten teilen sich das Passwort `anlaufstelle2026`:

| Login | Rolle | Wofür |
|---|---|---|
| `admin` | Anwendungsbetreuung (`facility_admin`) | Formular-Konfiguration, Zugänge, Einstellungen, Datenimport |
| `emma` | Leitung (`lead`) | Statistik, Berichte, DSGVO-Workflows |
| `miriam` | Fachkraft (`staff`) | Personen, Fälle, Dokumentation im Alltag |
| `superadmin` | Systemadministration (`super_admin`) | `/system/`, installationsweite Sicht |

Die vollständige Liste der sieben Logins und die Datenmengen je Profil stehen unter
[Seed-Daten laden](#entwicklungsumgebung-einrichten). Der Seed ist fail-closed über
`SEED_ALLOWED` abgesichert und läuft ausschließlich in lokalen/Demo-Settings; das
Produktions-Compose seedet **nie** und kennt die Variable nicht — dort läuft die
Ersteinrichtung über `manage.py create_super_admin`.

**Aufräumen:** `docker compose down` stoppt den Stack, `docker compose down -v` wirft
zusätzlich die Datenbank weg (der nächste Start seedet dann wieder frisch). Logs:
`docker compose logs -f web`.

**Danach.** Für echte Entwicklung — Tests, Linting, Tailwind-Watch, Debugger — brauchst du die
lokale Toolchain aus dem nächsten Abschnitt. Wie das System aufgebaut ist:
[docs/architecture.md](docs/architecture.md).

---

## Entwicklungsumgebung einrichten

### Voraussetzungen

- **Python 3.14** (empfohlen: via [pyenv](https://github.com/pyenv/pyenv))
- **PostgreSQL 18** (oder Docker, s. u.)
- **Node.js 24+** (für Tailwind CSS)
- **Docker** (optional, für die Datenbank)

### Schritt-für-Schritt

**1. Repository klonen**

```bash
git clone https://github.com/anlaufstelle/app.git
cd app
```

**2. Python-Umgebung einrichten**

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # enthält Runtime + Test/Lint-Tools
# Alternativ nur Runtime (z.B. für Prod-Docker-Build):
# pip install -r requirements.txt
```

> **Lock-Files:** `requirements.txt` / `requirements-dev.txt` sind generierte
> Lock-Files mit gepinnten transitiven Abhängigkeiten (via
> [pip-tools](https://github.com/jazzband/pip-tools)). Direkte Abhängigkeiten
> stehen in `requirements.in` / `requirements-dev.in`. Nach einer Änderung
> dort: `make deps-lock` ausführen. Details:
> [docs/ops-runbook.md § 8](docs/ops-runbook.md#8-dependencies-aktualisieren).

**3. Datenbank starten**

Mit Docker (empfohlen):

```bash
make db
```

Das startet einen PostgreSQL-18-Container mit folgenden Zugangsdaten:

| Variable  | Wert          |
|-----------|---------------|
| DB-Name   | anlaufstelle  |
| User      | anlaufstelle  |
| Passwort  | anlaufstelle  |
| Port      | 5432          |

Der Port ist dabei ausschließlich über `127.0.0.1:5432` erreichbar. Einen Zugriff aus dem LAN musst du bewusst über ein eigenes Docker- oder Firewall-Setup freigeben.

Alternativ kann eine lokal installierte PostgreSQL-Instanz verwendet werden. Die Verbindung wird dann über die Variablen `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST` und `POSTGRES_PORT` gesetzt (Defaults: `anlaufstelle`, `anlaufstelle`, leer, `localhost`, `5432`; unter den Entwicklungs-Settings fällt ein leeres Passwort auf `anlaufstelle` zurück). Eine Verbindungs-URL (`DATABASE_URL`) liest die Anwendung nicht.

**4. Umgebungsvariablen konfigurieren**

Lege eine `.env`-Datei im Projektverzeichnis an (oder exportiere die Variablen). Mit dem Docker-Container aus Schritt 3 genügen die beiden Schlüssel; die Datenbank-Variablen brauchst du nur für eine abweichende Instanz:

```bash
DJANGO_SECRET_KEY=dev-secret-key-bitte-aendern
ENCRYPTION_KEY=<Fernet-Schlüssel, siehe unten>

# Nur für eine eigene PostgreSQL-Instanz (Beispiel mit abweichenden Werten):
POSTGRES_HOST=db.example.org
POSTGRES_PORT=5433
POSTGRES_DB=meine_db
POSTGRES_USER=mein_nutzer
POSTGRES_PASSWORD=mein-passwort
```

`DEBUG` gibt es nicht als Umgebungsvariable: Das Settings-Profil entscheidet. `manage.py` startet mit `anlaufstelle.settings.dev` (`DEBUG = True`); die Produktions-Settings setzen `DEBUG = False`. Ohne `DJANGO_SECRET_KEY` setzen die Entwicklungs-Settings einen unsicheren Dev-Wert, die Produktions-Settings verlangen ihn. Ohne `ENCRYPTION_KEY` laufen die Entwicklungs-Settings mit lauter Warnung im Klartextmodus (sensible Felder werden unverschlüsselt gespeichert).

Einen `ENCRYPTION_KEY` erzeugen:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**5. Migrationen ausführen**

```bash
make migrate
```

**6. Seed-Daten laden** (optional, für lokale Entwicklung)

```bash
make seed                              # Standard: small
python src/manage.py seed --scale medium   # mehr Daten inkl. Fallmanagement
python src/manage.py seed --scale large    # Lasttest-Volumen
python src/manage.py seed --scale solo     # eine Einrichtung, 500 Ereignisse über 1000 Tage
python src/manage.py seed --flush          # vorhandene Daten vorher löschen
```

> **Umgebungs-Guard:** `seed` läuft nur, wenn die Settings `SEED_ALLOWED = True`
> setzen (dev/test/e2e/devlive). Unter `prod`-Settings bricht das Command mit
> `CommandError` ab — Demo-Logins und `--flush` sind dort tabu; die
> Ersteinrichtung läuft über `manage.py create_super_admin` (Refs #1040).

**Dokumentationstypen kommen aus einem Paket:** Der Katalog (9 Dokumentationstypen,
25 Feldvorlagen, Feldgruppen und Zuordnungen) steht deklarativ in
[`src/core/packages/default.yaml`](src/core/packages/default.yaml) und wird vom Seed
über den Paket-Loader (`core.packages`) eingespielt — derselbe Weg wie
`manage.py load_package --file … --facility …`. Das Einspielen ist **idempotent**:
gefunden wird über den `slug` (nicht den Namen), Properties werden bei jedem Lauf
zurücksynchronisiert (früher `get_or_create` — Defaults griffen nur beim Anlegen),
und ein zweiter Lauf meldet alles als `unverändert`.

Der Standardkatalog bietet `Kontakt`, `Spritzentausch`, `Vermittlung` und `Notiz` mit `tally_enabled: true` in der Strichliste an. Beim Spritzentausch führt `+1` wegen der beiden Zahlen-Pflichtfelder zur kompakten Eingabe von Ausgabe und Rückgabe; beide Stückzahlen haben im Katalog `min_value: 0`, negative Werte werden in keinem Schreibpfad angenommen. Die übrigen drei Typen haben keine Pflichtwerte und werden direkt gezählt.

> **Katalog ändern:** Werte in `default.yaml` anpassen, nicht in `core/seed/doc_types.py`.
> Bestehende `slug`-Werte sind **unveränderbar** (Migration 0117/#1710 + DB-Trigger) —
> ein geänderter Slug legt in jeder Einrichtung, die das Objekt trägt, ein Duplikat an statt zu
> aktualisieren. `system_type` (nur noch `ban`/`crisis`) immer mitführen, wo einer
> steht, bei `select`/`multi_select` immer den
> `options`-Schlüssel; **keinen** `conditions`-Schlüssel ergänzen (auch nicht `[]` —
> das würde per UI gepflegte Feldbedingungen löschen). Die **letzte** Gruppe eines
> Dokumentationstyps nie aus dem Katalog entfernen: der Seed räumt verwaiste Gruppen
> nur bei Typen auf, für die das Paket noch mindestens eine Gruppe deklariert — erst
> die Felder aus der Gruppe herausnehmen (`group_slug` streichen), die leere Gruppe
> aber stehen lassen.

#### Was der Loader-Seed einer bestehenden Einrichtung bewirkt

Der Seed ist kein reines „anlegen, falls nicht vorhanden", sondern ein
Rücksync gegen `default.yaml`. Was das im Betrieb bedeutet:

| Betroffen | Wirkung beim nächsten Seed-Lauf |
|---|---|
| `name`, `sensitivity`, `min_contact_stage` eines Katalog-Typs | wird auf den Katalogwert **zurückgesetzt** |
| `icon`, `color`, `sort_order` eines Katalog-Typs | wird auf den Katalogwert **zurückgesetzt** — aber nur, wenn das Paket den Schlüssel nennt; fehlt er, bleibt der Wert der Einrichtung stehen |
| `system_type` eines Katalog-Typs | wird auf den Paketwert gesetzt; ein Typ ohne Systemtyp darf einen bekommen, ein gesetzter Systemtyp kann weder wechseln noch entfallen (Konflikt, s. u.) |
| `report_key` eines Katalog-Typs | wird nur angewendet, wenn das Paket den Schlüssel nennt; fehlt er, bleibt der Bestand unangetastet. Leer → gesetzt ist erlaubt, gesetzt → anders ist ein Konflikt. |
| `entity_type` eines Katalog-Typs | unveränderlich: weicht der vorhandene Typ ab, bricht der Lauf ab (Konflikt, s. u.) |
| `sort_order` von Gruppen **und** Feldzuordnungen — auch bei Typen ohne Gruppen | wird auf die Katalogposition zurückgesetzt; eine per Oberfläche umsortierte Feldliste bleibt also nicht stehen |
| `default_value` einer Katalog-Feldvorlage | wird auf den Paketwert (im Katalog durchgehend leer) **zurückgesetzt** — ein per Config-UI gepflegter Vorbelegungswert überlebt den Seed **nicht**. Paketformat v1 kann „nicht anfassen" für dieses Feld nicht ausdrücken (anders als `conditions`). |
| `min_value`, `max_value` einer Katalog-Zahlenfeldvorlage | werden auf die optionalen Paketwerte zurückgesetzt. Die Migration ergänzt nur die Konfigurationsspalten; vorhandene `data_json`-Werte in Ereignissen, Personen und Fällen bleiben unverändert. Vor einer Bereichsverschärfung das schreibfreie Inventar `python src/manage.py inventory_number_ranges --facility <UUID-oder-Name>` mit Admin-DB-Rolle ausführen. |
| `tally_enabled` eines Katalog-Typs | Ein explizites `true`/`false` wird auf den Paketwert zurückgesetzt. Fehlt der Schlüssel, bleibt ein in der Admin-Oberfläche gepflegtes Opt-in am Bestandstyp erhalten; beim Anlegen gilt `false`. |
| Felder, die eine Einrichtung einem Katalog-Typ **zusätzlich** zugeordnet hat | bleiben unangetastet (der Loader fasst nur an, was im Paket steht) |
| `retention_days`, `description`, `is_active` (Typ) bzw. `is_active` (Vorlage) | nicht Teil von Schema v1 — werden weder gesetzt noch zurückgesetzt |

Der Schlüssel `category` am Typ wird nicht mehr angewendet: Das Feld gibt es seit
[ADR-040](docs/adr/040-documenttype-category-entfernt.md) nicht mehr; der Loader ignoriert den
Schlüssel und meldet eine Warnung im Report. Maßgeblich ist `_apply_document_type` in
[`src/core/packages/loader.py`](src/core/packages/loader.py); die Schlüssel des Pakets
beschreibt [docs/packages.md](docs/packages.md).

Drei Konfliktfälle brechen den **gesamten** Seed-Lauf ab (fail-loud, Alles-oder-nichts —
es wird nichts angewendet, auch nicht für die übrigen Typen):

* **Eigene Feldvorlage auf einem Katalog-Slug.** Hat eine Einrichtung z. B. `dauer` selbst
  als Textfeld angelegt, kollidiert das mit `field_type`/`is_encrypted` aus dem Paket —
  beide sind nach dem Anlegen unveränderlich. Auflösung: die eigene Vorlage auf einen
  anderen Slug bringen (neu anlegen + Daten migrieren) oder den Katalog-Slug ändern.
* **Abweichender unveränderlicher Wert.** `entity_type`, ein gesetzter `system_type` und ein
  gesetzter `report_key` eines vorhandenen Typs lassen sich nicht ändern; weicht das Paket
  ab (oder lässt es den `system_type` weg), meldet der Loader einen Konflikt.
* **Namens-Kollision nach einer Umbenennung.** Wurde „Kontakt" per UI in „Erstkontakt"
  umbenannt und danach ein *neuer* Typ „Kontakt" angelegt (der bekommt den Slug
  `kontakt-2`), will der Seed den Typ mit dem Slug `kontakt` wieder in „Kontakt"
  zurückbenennen — das verletzt `UniqueConstraint(facility, name)`. Ab da
  scheitert **jeder** weitere Seed-Lauf, bis manuell aufgeräumt ist. Auflösung: den
  betreiber-eigenen Typ umbenennen oder den Namen im Katalog anpassen.

#### Scale-Profile im Überblick

Die im Demo-Panel beworbenen Demo-Konten haben keine E-Mail-Adresse, weil die geteilte Demo keine Mails versendet; den Passwort-Reset prüft man auf dev mit Mail-Konsole bzw. Mailpit.

| Daten | `small` (Default) | `medium` | `large` | `solo` | `demo` |
|---|---|---|---|---|---|
| Einrichtungen | 1 | 2 | 5 | 1 | 6 (Szenario-Katalog, Refs #2279) |
| Pakete je Einrichtung (Refs #2148) | Hauptstelle: `default` | + Zweigstelle Nord: `beratungsstelle` | + Zweigstelle Süd: `awareness`, Außenstelle Ost: `jugendarbeit`, Außenstelle West: `frauenhaus` | Hauptstelle: `default` | `core.seed.scenarios.SCENARIOS`: Kontaktladen Wärmestube `default`, Beratungsstelle Falkenweg `beratungsstelle`, Beratungspraxis Lindenhof (Solo) `beratungsstelle`, Awareness-Team Nachtfalter `awareness`, Jugendtreff Wilhelmshöhe `jugendarbeit`, Frauenhaus Amalie `frauenhaus` |
| Users (gesamt) | 7 (1 super_admin + 6 facility-User) | 13 (1 super_admin + 2×6) | 31 (1 super_admin + 5×6) | 7 (1 super_admin + 6 facility-User) | 33 (1 super_admin + 5×6 Team + 2 Solo) |
| Users / Einrichtung | 6 (`admin`/`emma`/`miriam`/`markus`/`lena`/`felix`) | 6 | 6 | 6 | 6 (Solo-Facility: 2 — Fachkraft + Vertretung) |
| Clients / Einrichtung | 7 | 40 | 500 | 30 | 18 (Solo: 9) |
| Events / Einrichtung | 25 | 750 | 10.000 | 500 | 120 (Solo: 60) |
| Cases | 3 | 12 | 50 | 10 | 6 (Solo: 4) |
| Episoden | — | 20 | 80 | 15 | 8 (Solo: 4) |
| Wirkungsziele | — | 15 | 60 | 10 | 6 (Solo: 3) |
| Meilensteine / Ziel | — | 3 | 4 | 3 | 3 (Solo: 2) |
| WorkItems | 5 | 25 | 100 | 20 | 12 (Solo: 8) |
| Quick-Vorlagen / Einrichtung | 6 | 6 | 6 | 6 | 6 |
| DeletionRequests | — | 5 | 15 | 4 | 2 (Solo: 1) |
| RetentionProposals | 4 | 6 | 12 | 6 | 3 (Solo: 2) |
| Dateianhänge (ca.) | 1–2 (50 %) | ~15 (25 %) | ~80 (10 %) | Anteil 25 % (Beratungs-Ereignisse) | ~5 (30 %) (Solo: ~3) |
| Zeitraum | 80 Tage | 365 Tage | 3 Jahre | 1000 Tage | 270 Tage |
| Zeitfenster je Einrichtung | Hauptstelle 3 | Hauptstelle 3, Nord 3 | Hauptstelle 3, Nord 3, Süd 2, Ost 2, West 3 | Hauptstelle 3 | Kontaktladen 3, Falkenweg 3, Lindenhof 0, Nachtfalter 2, Wilhelmshöhe 2, Amalie 3 |

> **Hinweis:** `small` enthält kein Fallmanagement (keine Episoden, Ziele). Für die Entwicklung am Fallmanagement `medium` verwenden.
>
> **`demo`** (Refs #2279) stellt alle sechs Demo-Szenarien (einschließlich Solo-Betrieb) gleichzeitig bereit, unabhängig vom Datenvolumen der übrigen Profile — Quelle ist ausschließlich `core.seed.scenarios.SCENARIOS`. Wird von `dev-ops/deploy/demo-reseed.sh` (stündlicher Reset auf `demo.anlaufstelle.app`) verwendet, ist aber auch lokal nutzbar (`python src/manage.py seed --scale demo`).

Seed-Zugangsdaten: Passwort `anlaufstelle2026`, 7 Logins (Username → Rolle): `superadmin` → `super_admin` (keine `facility`-Zuordnung), `admin` → `facility_admin`, `emma` → `lead`, `miriam` → `staff`, `markus` → `staff`, `lena` → `assistant`, `felix` → `assistant`. Alle außer `superadmin` hängen an der Default-Einrichtung.

> **Production:** In Produktion gibt es **kein** Default-Passwort und keinen Default-`super_admin`. Die Erstinstallation läuft über `manage.py create_super_admin` (interaktiv, ohne Default). Details: `docs/dev/dev-deployment.md` § Production-Bootstrap (dev-only) und [docs/admin-guide.md § 2.1 Erstinstallation](docs/admin-guide.md). Lockout-Recovery: `manage.py unlock <username>`.

**7. Node-Abhängigkeiten installieren** (für Tailwind CSS)

```bash
npm install
```

**8. Entwicklungsserver starten**

In zwei Terminals parallel:

```bash
# Terminal 1: Django-Server
make run

# Terminal 2: Tailwind im Watch-Modus
make tailwind
```

Der Server ist unter `https://localhost:8443` erreichbar (selbstsigniertes Zertifikat — Browserwarnung akzeptieren). Fallback ohne HTTPS: `make run-http` (Port 8000).

---

## Make-Targets

**Gemeinsamer Host-Lock:** Die gesperrten Targets brauchen `flock` (util-linux),
das auf macOS nicht standardmäßig installiert ist. `CI_LOCK` verwendet
`XDG_RUNTIME_DIR/anlaufstelle-ci.lock`, ersatzweise `TMPDIR/anlaufstelle-ci.lock`,
sonst `/tmp/anlaufstelle-ci.lock`. Das Verzeichnis muss existieren. Alle
Sitzungen mit gemeinsamer E2E-Datenbank oder gemeinsamen Serverports müssen
denselben absoluten Pfad verwenden; bei unterschiedlichen Laufzeitverzeichnissen
`CI_LOCK` einheitlich setzen. Make exportiert `CI_LOCK` und `CI_LOCK_TIMEOUT`
(Default 14400 Sekunden) an Unterprozesse einschließlich Mutations-Cleanup;
direkte Mutationsrunner-Aufrufe verwenden dieselbe Default-Kette.

| Target           | Beschreibung                                                      |
|------------------|-------------------------------------------------------------------|
| `make db`        | PostgreSQL-18-Container starten                                   |
| `make db-stop`   | PostgreSQL-Container stoppen und entfernen                        |
| `make migrate`   | Django-Migrationen ausführen                                      |
| `make run`       | Dev-Server starten (gunicorn + HTTPS auf `0.0.0.0:8443`)        |
| `make run-http`  | Fallback: Django runserver ohne HTTPS (`0.0.0.0:8000`)           |
| `make seed`      | Seed-Daten in die Datenbank laden                                 |
| `make tailwind`  | Tailwind CSS im Watch-Modus kompilieren                           |
| `make tailwind-build` | Tailwind CSS für Produktion minifiziert kompilieren          |
| `make lint`      | Code mit Ruff prüfen und Formatierung kontrollieren               |
| `make typecheck` | mypy auf `core/services` (strikt) + Baseline-Check (Refs #741) |
| `make test`      | Unit- und Integrationstests ausführen (ohne E2E)                  |
| `make test-unit-slice INDEX=1` | Eine von drei nach gemessener Dateilaufzeit gewichteten Unit-Scheiben, mit zwei Workern und Coverage ohne Einzelschwelle; derselbe Schnitt wie in `test.yml`. Ergebnis: `.coverage.1` und `unit-durations-1.txt`. |
| `make test-unit-combine TEST_RESULT=success` | Alle drei Dateien unter `unit-coverage/unit-coverage-N/.coverage.N` prüfen und zusammenführen; 96 % Coverage auf der Vereinigung erzwingen. `TEST_RESULT` muss den tatsächlichen Gesamtstatus der drei Läufe wiedergeben. |
| `make test-unit-weights DURATIONS="unit-durations-1.txt unit-durations-2.txt unit-durations-3.txt"` | Gewichtsdatei `scripts/ci/test_weights.json` aus vollständigen `--durations=0 --durations-min=0`-Logs erneuern. `python scripts/ci/test_slices.py --summary` zeigt Dateien, Tests und Gewichtssummen. |
| `make test-js`   | Vitest-Suite (JS, `npm test`) deterministisch/non-watch ausführen — Teil von `make ci` (Refs #1932) |
| `make test-e2e`  | End-to-End-Tests mit Playwright ausführen                         |
| `make test-e2e-halves` | E2E in zwei seriellen Hälften (RAM-limitierte Hosts, Refs #1787); den Schnitt liefert `scripts/e2e_slice.py` — derselbe Schlüssel wie in der CI-Matrix (Refs #1976) |
| `make test-e2e-slice-locked SLICES=8 INDEX=3` | Eine einzelne Scheibe des CI-Schnitts lokal nachfahren, unter dem CI-Lock (auch eine Scheibe belegt Port 8844 und `anlaufstelle_e2e`; ungelockt als `make test-e2e-slice`, nur wenn sicher kein anderer E2E-Lauf läuft) — die Nummer stammt aus dem Job-Namen (`E2E-Scheibe 3`); `python scripts/e2e_slice.py --slices 4 --summary` zeigt die Verteilung (Refs #1976) |
| `make check`     | Django-Systemcheck und Migrations-Konsistenz prüfen               |
| `make i18n-check` | `.po`-Vollabgleich gegen den Quellbaum (neue/entfernte/semantisch geänderte msgids) — Teil von `make ci` (Refs #1933) |
| `make ci`        | Vollständige CI-Pipeline lokal: `lint` + `check` + Guards (deps/matrix/release-test/vendor-js/agent-docs) + `audit-js` + `i18n-check` + `typecheck` + `test-js` + `test-parallel` |
| `make test-focus T=<pfad>` | Einzelne Testdatei mit Fail-Fast                         |
| `make test-parallel` | Unit- und Integrationstests parallel (pytest-xdist)            |
| `make test-e2e-parallel` | E2E-Tests parallel (Default 2 Worker, konfigurierbar)     |
| `make test-e2e-smoke` | Nur Smoke-markierte E2E-Tests (~2-3 min)                    |
| `make deps-lock` | Lock-Files aus `requirements*.in` neu erzeugen (pip-tools)        |
| `make deps-check` | Prüft, ob Lock-Files aktuell zu `.in` sind (Drift-Detektion)     |
| `make audit-js`  | `npm audit` gegen `package-lock.json` — blockierendes JS-Advisory-Gate ab Schweregrad `AUDIT_LEVEL` (Default `moderate`), Teil von `make ci`; braucht Zugang zur npm-Registry (Refs #1968) |
| `make dev`       | Datenbank starten, migrieren und Server starten (kombiniert)      |
| `make clean`     | Generierte Artefakte löschen (`__pycache__`, `*.pyc`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `src/staticfiles`) — `src/media/` bleibt unangetastet (Datenverlustrisiko) |

Vor jedem Commit sollte `make ci` lokal erfolgreich durchlaufen.

### Pre-Commit-Hooks (optional)

Für die schnelle Drift-Detektion vor dem Commit ist eine [`.pre-commit-config.yaml`](.pre-commit-config.yaml) hinterlegt (Refs #820, #860). Sie prüft `ruff` (lint + format), `makemigrations --check`, `mypy core/services`, den Translation-Version-Header und automatisches `pip-compile` bei `requirements*.in`-Änderungen.

```bash
.venv/bin/pip install pre-commit
pre-commit install                       # einmalig: commit-stage Hooks
pre-commit install --hook-type pre-push  # einmalig: pre-push Schnell-CI (Refs #860)
pre-commit run --all-files               # alle commit-stage Hooks gegen das Repo
```

**Zwei Stufen:**

- **Commit-Stage:** Ruff lint+format, `makemigrations --check`, `mypy`, Translation-Version, `pip-compile` bei Lock-File-Drift. Läuft in unter 5 s.
- **Pre-Push:** `make lint && make deps-check && make check` — der Solo-Maintainer-Ersatz für Required Status Checks. Branch Protection mit Required Status Checks greift bei direktem `git push` auf `main` nicht; der pre-push-Hook fängt deshalb genau das ab, was sonst rote CI nach Push produzieren würde (Lock-Drift, Format-Drift, Migrations-Drift). Läuft in ~10 s. Tests bleiben in CI.

> **venv vor JEDEM Push aktivieren** (Lehre aus dem v0.89.0-Handoff): Der `pre-push`-Hook ruft `pre-commit` auf. Ohne aktiviertes venv bricht **jeder** Push mit `pre-commit not found. Did you forget to activate your virtualenv?` ab. Bei einem **Tag**-Push ist das besonders tückisch — git meldet danach nur `failed to push some refs`, der Tag bleibt still lokal, und auffallen kann das erst Schritte später am Tag-Push-Gate (`release-verify-tags.sh`). Also `. .venv/bin/activate` (oder einmalig `PATH="$PWD/.venv/bin:$PATH" git push …`) — **nicht** `--no-verify`, das umgeht die Schnell-CI.

Wo die GitHub-Actions-Workflows laufen, ist ihr Ergebnis die letztgültige Quelle der Wahrheit: Beitragende erhalten sie an ihrem Pull Request im öffentlichen Repository. Ob die Workflows im internen Entwicklungszweig laufen, ist eine Einstellung der Maintainer und nicht im Repository festgelegt; dort gilt `make ci` als verbindliches Tor ([ADR-029](docs/adr/029-local-only-ci.md)). Der pre-push-Hook reduziert nur die Wahrscheinlichkeit roter CI nach dem Push.

### Port-Übersicht und Prozess-Hygiene

| Port | Prozess | Gestartet von | Zweck |
|------|---------|---------------|-------|
| **8443** | gunicorn (HTTPS) | `make run` / `make dev` | Dev-Server (Standard) |
| **8844** | gunicorn (HTTP) | `make test-e2e` / E2E-conftest | E2E-Testserver Worker 0 (temporär) |
| **8845+** | gunicorn (HTTP) | `make test-e2e-parallel` | E2E-Testserver Worker 1+ (temporär) |
| **8000** | Django runserver | `make run-http` | Fallback ohne HTTPS |
| **5432** | PostgreSQL | `make db` (Docker) | Datenbank |

**Kollisionsschutz:** `make run` und `make run-http` beenden automatisch alte Prozesse auf ihrem Port, bevor sie starten. Der E2E-conftest räumt Port 8844 ebenfalls auf.

**Troubleshooting bei langsamer App:**

```bash
# Doppelte Server-Prozesse finden
ps aux | grep -E 'gunicorn|runserver' | grep -v grep

# Bestimmten Port freigeben
lsof -ti :8443 | xargs kill
```

Ein Server auf Port 8844 gehört zum jeweiligen E2E-Lauf. Den eigenen Lauf im
zugehörigen Terminal beenden oder den fremden Lauf abwarten; nicht alle
Gunicorn-Prozesse gemeinsam beenden.

---

## Coding Conventions

### Python / Django

- **Python 3.14** mit vollständigen Type Hints wo sinnvoll (Codebasis bleibt 3.13-kompatibel, siehe `requires-python`).
- **Django 6.1+** — Class-based Views bevorzugt, Funktions-Views nur für einfache Fälle.
- Business-Logik gehört in `core/services/`, nicht in Views oder Models.
- Models sind aufgeteilt: ein Model (oder eng verwandte Models) pro Datei unter `core/models/`.
- Rollen-Zugriffsschutz über Mixins aus `core/views/mixins.py` — verfügbar: `SuperAdminRequiredMixin` (nur `/system/`), `FacilityAdminRequiredMixin` (Admin der eigenen Facility), `LeadOrAdminRequiredMixin`, `StaffRequiredMixin`, `AssistantOrAboveRequiredMixin`.
- Nutzergerichtete deutsche Texte folgen der verbindlichen [Sprachleitlinie](docs/sprachleitlinie.md).
- Keine neuen Abhängigkeiten ohne vorherige Absprache einführen.

### Facility-Scoping & Row Level Security

Jedes neue facility-gescopte Model muss auf **beiden** Verteidigungslinien abgesichert sein:

1. **Django-Layer (erste Linie):**
   - `facility = models.ForeignKey(Facility, ...)` am Model
   - `objects = FacilityScopedManager()` (aus [`src/core/models/managers.py`](src/core/models/managers.py))
   - Views/Services filtern via `.for_facility(request.current_facility)`
2. **PostgreSQL-RLS (zweite Linie, Defense-in-Depth):**
   - Neue Migration nach dem Muster von [`src/core/migrations/0047_postgres_rls_setup.py`](src/core/migrations/0047_postgres_rls_setup.py): Tabelle zu `DIRECT_TABLES` hinzufügen (oder `JOIN_TABLES`, falls kein direktes `facility_id`-Feld vorhanden ist). Die Migration setzt `ENABLE + FORCE ROW LEVEL SECURITY` plus eine `facility_isolation`-Policy.
   - `EXPECTED_TABLES` in [`src/tests/test_rls.py`](src/tests/test_rls.py) ist **ableitungsbasiert** (#1096): Tabellen mit direktem `facility`-FK (DIRECT) leitet die Test-Suite automatisch aus der Model-Registry ab — ein neues solches Model erscheint ohne Handgriff in `EXPECTED_TABLES`, und der RLS-Coverage-Guard (`TestRLSCoverageGuard`) failt, bis die RLS-Migration **und** die PII-Klassifikation existieren. Nur **JOIN-gescopte** Tabellen ohne direkten FK brauchen einen Hand-Eintrag in der `JOIN_SCOPED_TABLES`-Konstante; die Auth-Grenzen-Ausnahme (`core_user`) steht in `NOT_RLS_SCOPED`.
   - Der Architektur-Guard `TestRLSJoinScopeArchitectureGuard` (#2001) schließt die Blindstelle der Ableitung: Referenziert ein Model ohne eigenen `facility`-FK per FK/O2O eine DIRECT-Tabelle, muss seine Tabelle in `JOIN_SCOPED_TABLES` stehen. Sonst failt `pytest -m architecture` mit dem Hinweis auf das Migrationsmuster `0047_postgres_rls_setup.py` — auch wenn weder Migration noch Liste die neue Tabelle bislang kennen. Die Ableitung ist bewusst auf die unmittelbare Kante zu einem DIRECT-Model begrenzt; bei tieferen Ketten (JOIN → JOIN → DIRECT) bleibt der Hand-Eintrag anhand der vollständigen Facility-Kette Pflicht.
   - Für die RLS-freien Tabellen (`core_user`, `core_facility`, `core_organization`) fehlt dieser zweite Backstop per Definition. Dort greift stattdessen der Architektur-Guard `TestTenantModelScopeGuard` in [`src/tests/test_architecture_guards_models.py`](src/tests/test_architecture_guards_models.py) (#1658): ein `User.objects`/`Facility.objects`/`Organization.objects`-Zugriff in `core/views/`, `core/forms/` oder `core/services/` ohne Facility-Scope failt die CI. Bewusst globale Zugriffe (Pre-Auth-Lookups, Super-Admin-Kennzahlen) gehören mit Begründung in die Allowlist der jeweiligen Scan-Sektion; Begründung der Guard-vs.-Manager-Entscheidung in [ADR-005](docs/adr/005-facility-scoping-and-rls.md).

Details: [docs/ops-runbook.md § 9](docs/ops-runbook.md). RLS greift in Produktion nur, wenn der Django-DB-User **kein** Superuser ist (siehe `docs/dev/dev-deployment.md` (dev-only), primaerer Pfad nach [ADR-017](docs/adr/017-deployment-topology.md)).

#### Datenmigrationen auf RLS-Tabellen (Refs #1746)

Jede `RunPython`-Datenmigration, die eine RLS-Tabelle liest oder schreibt, ruft als **erste Zeile** den Guard `assert_migration_role_bypasses_rls(schema_editor)` aus [`src/core/utils/migration_guards.py`](src/core/utils/migration_guards.py):

```python
from django.db import migrations

from core.utils.migration_guards import assert_migration_role_bypasses_rls


def backfill(apps, schema_editor):
    assert_migration_role_bypasses_rls(schema_editor)
    Client = apps.get_model("core", "Client")
    ...


class Migration(migrations.Migration):
    dependencies = [("core", "0122_backfill_backend_data")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
```

**Warum:** Migrationen laufen laut [ADR-020](docs/adr/020-three-role-postgres-model.md) als Admin-Rolle (`POSTGRES_ADMIN_USER`, `NOSUPERUSER BYPASSRLS`) — dort greift die `facility_isolation`-Policy nicht. Läuft eine Migration wegen einer Fehlkonfiguration unter der NOBYPASSRLS-App-Rolle, trifft sie ohne gesetztes `app.current_facility_id` **still 0 Zeilen**: die Migration gilt als erfolgreich, der Backfill fehlt. Der Guard macht daraus einen harten Abbruch (`MigrationRoleError`) samt Rollennamen und Fix-Hinweis. Kein `SET ROLE`/GUC-Turnen in der Migration — die Session-GUC `app.is_super_admin` ist **kein** akzeptierter Ersatz (bewusste Abgrenzung zu `has_rls_bypass_context`, das sie für Wartungs-/Cron-Pfade zulässt).

Der Guard ergänzt das Deploy-Gate `current_connection_bypasses_rls` aus [`docker-migrate.sh`](docker-migrate.sh) (#1375): jenes prüft den Migrate-**Job** einmal vorab und greift nur im Container-Deploy, dieser sitzt **in** der Migration und schützt jeden Aufrufweg (manuelles `manage.py migrate`, Restore-Drill, Self-Hosting ohne das Skript). Der Guard steht in **jeder** `RunPython`-Funktion, die eine RLS-Tabelle schreibt — auch in den bestehenden Migrationen (#2114). Welche das sind, leitet der Architektur-Test [`src/tests/test_migration_rls_guard_coverage.py`](src/tests/test_migration_rls_guard_coverage.py) zur Laufzeit aus `pg_class.relrowsecurity` ab und erzwingt ihn so für jede neue Datenmigration; Ausnahmen brauchen eine Begründung in der Ausnahmeliste des Tests. Tests: [`src/tests/test_migration_rls_guard.py`](src/tests/test_migration_rls_guard.py) (Wirkung), `test_migration_rls_guard_coverage.py` (Abdeckung).

### Linting und Formatierung

Das Projekt verwendet [Ruff](https://docs.astral.sh/ruff/) für Linting und Formatierung:

```bash
# Prüfen
make lint

# Automatisch korrigieren
python -m ruff check src/ --fix
python -m ruff format src/
```

Die Ruff-Konfiguration befindet sich in `pyproject.toml`.

### Templates und Frontend

- Templates liegen unter `src/templates/`.
- HTMX für dynamische Interaktionen, Alpine.js für leichtgewichtige UI-Logik.
- Tailwind CSS für Styling — keine eigenen CSS-Klassen anlegen, soweit möglich.
- Barrierefreiheit (WCAG 2.1 AA) beachten.
- Neue Objektseiten folgen der Konvention für Kontext-Kopf und Rücklinke.

#### Vendored JS-Libraries aktualisieren (Refs #1076)

Es gibt **keinen Frontend-Bundler** — vier Libraries werden als vorgebaute `*.min.js` unter [`src/static/js/`](src/static/js/) eingecheckt (vendored) und per `{% static %}` geladen. Damit sie trotzdem im Dependabot-/CVE-Radar liegen, sind sie als **exakt gepinnte** `devDependencies` in [`package.json`](package.json) geführt:

| Library | npm-Paket | vendored Datei | dist-Quelle in `node_modules/` |
|---|---|---|---|
| htmx | `htmx.org` | `htmx.min.js` | `dist/htmx.min.js` |
| Alpine.js (CSP-Build) | `@alpinejs/csp` | `alpine-csp.min.js` | `dist/cdn.min.js` |
| Dexie | `dexie` | `dexie.min.js` | `dist/dexie.min.js` |
| Chart.js | `chart.js` | `chart.min.js` | `dist/chart.umd.js` *(bereits minifiziert)* |

> Achtung: Das CSP-Build von Alpine ist das **eigene** Paket `@alpinejs/csp`, nicht `alpinejs`. Chart.js liefert im npm-`dist` keine eigene `*.min.js`; der UMD-Build `chart.umd.js` ist bereits minifiziert.

**Update-Ablauf** (z. B. nach einem Dependabot-Bump, der nur `package.json`/`package-lock.json` ändert):

```bash
npm ci                                # node_modules/ auf package-lock-Stand bringen
make sync-vendor-js                   # dist-Builds nach src/static/js/ kopieren
git add package.json package-lock.json src/static/js
```

**Verifikation:** `make ci` führt den Drift-Guard `make verify-vendor-js-sync` aus ([`scripts/verify_vendor_js_sync.py`](scripts/verify_vendor_js_sync.py)). Er vergleicht die in `package.json` gepinnte Version mit dem Versions-String im eingecheckten `*.min.js` und failt bei Drift — so kann das Re-Vendoring nicht vergessen werden. Der Guard ist reiner String-Vergleich (kein node/npm nötig). Zusätzlich E2E-Smoke (`make test-e2e-smoke`), da die Offline-Flows an Dexie hängen.

#### HTMX & Live-Regions (Refs #811)

Damit HTMX-Erfolgsmeldungen Screen-Reader-Nutzer*innen erreichen, gilt:

- Es existiert genau eine **stabile Live-Region** in [`base.html`](src/templates/base.html): `#flash-messages` mit `role="status" aria-live="polite" aria-atomic="true"`.
- HTMX-Antworten, die einen Erfolg ankündigen sollen, schwingen entweder per `hx-target="#flash-messages" hx-swap="innerHTML"` oder per `hx-swap-oob="innerHTML:#flash-messages"`. Den Wrapper-`<div>` selbst **nie** per `outerHTML` ersetzen — sonst wird die Live-Region neu instanziiert und der Announcement-Trigger verloren.
- Wenn ein Bulk-Endpoint einen vollständigen Reload erzwingen muss (z. B. WorkItem-Bulk, Retention-Bulk), läuft das über `HX-Redirect` — die Folge-Page rendert das Django-`messages`-Framework wieder in `#flash-messages`.

#### URL-Schema: HTMX-Fragmente vs. JSON-APIs (Refs #848)

Endpunkte werden nach Response-Typ getrennt — Templates referenzieren beide ausschließlich über `{% url 'name' %}`:

- **HTML-Fragmente (HTMX-Partials):** Pfad `/partials/<feature>/<action>/`. Beispiel: `partials/clients/autocomplete/`, `partials/retention/<uuid:pk>/approve/`. Antwort ist immer HTML, gerendert mit `partials/`-Template.
- **JSON-APIs:** Pfad `/api/v1/<feature>/<action>/`. Beispiel: `api/v1/offline/bundle/client/<uuid:pk>/`. Antwort ist JSON, von Service-Workern oder JS-`fetch()`-Calls konsumiert.

Neue Endpunkte gehören entsprechend in eine der beiden Pfad-Gruppen. URL-Namen bleiben kurz und feature-spezifisch (`client_autocomplete`, `offline_bundle`); Pfad-Prefixe wechseln nur dann, wenn die Response-Form wechselt. Direkte `fetch("/api/...")`-Aufrufe in JS dürfen nur unter `/api/v1/` gehen — HTMX-Partials werden niemals als JSON konsumiert.

### Übersetzungen (i18n)

- **EN im selben Commit nachziehen (verbindlich, Refs #1215).** Wer übersetzbare Strings ändert — Django-`{% trans %}`/`.po` unter [`src/locale/`](src/locale/) oder die gespiegelten EN-Dokumente (`*.en.md`, [`docs/en/`](docs/en/)) — zieht die englische Entsprechung im **selben Commit** nach, nicht in einem nachgelagerten Sync-Commit. Konsistent mit der i18n-als-eigener-Commit-Regel in `AGENTS.md` § Git & Commits (dev-only): DE und EN gehören in *einen* `chore(i18n):`-Commit, getrennt vom Feature.
- **Stempel als Backstop:** [`scripts/check_translation_versions.py`](scripts/check_translation_versions.py) erzwingt für die EN-Dokumente einen `translation-version`-Header == aktuelle Minor-Version (Pre-Commit-Hook und Release-Prüfung, hartes Gate, #1078). Der Stempel fängt Drift zum Release ab; die „im selben Commit"-Regel verhindert, dass Drift überhaupt entsteht.
- **Stempel-Konvention (Refs #1552):** Jedes EN-Dokument trägt am Dateiende die Marker `translation-source` (Pfad der DE-Quelle), `translation-version`, `translation-date` und `source-hash`. Beim EN-Sync setzt du `source-hash` auf `git hash-object <DE-Quelle>` (Blob-Hash der zugehörigen deutschen Datei) und aktualisierst `translation-date` auf das Sync-Datum. Der Guard ruft selbst `git hash-object` der Quelle auf und **failt hart**, sobald der gestempelte `source-hash` abweicht (DE-Quelle seit dem letzten EN-Sync geändert) — kein Warn-Modus. Kurz-Hashes (≥ 7 Hex-Zeichen) werden als Präfix akzeptiert; ein fehlender `source-hash`-Marker gilt als Fehler.

#### Pflege-Rhythmus der `.po`-Kataloge (Refs #1745)

Beide Kataloge — [`src/locale/de/LC_MESSAGES/django.po`](src/locale/de/LC_MESSAGES/django.po) und [`src/locale/en/LC_MESSAGES/django.po`](src/locale/en/LC_MESSAGES/django.po) — werden **gemeinsam** gepflegt, in einem `chore(i18n):`-Commit:

- **Wann regenerieren:** `python src/manage.py makemessages -l de -l en --no-obsolete`, ausgeführt im **Repo-Root** (dort erwartet der Katalog seine `src/…`-Referenzpfade; `LOCALE_PATHS` zeigt auf `src/locale/`). Spätestens am Ende eines Meilenstein- oder Sammel-Laufs und vor jedem Release-Cut — nicht erst, wenn der Rückstand Monate beträgt. Einzelne neue Strings dürfen weiterhin von Hand als Eintrag ergänzt werden; das ersetzt den Lauf nicht, verhindert aber deutschen Fallback in der Zwischenzeit.
- **Kleinständerungen:** Für einen einzelnen neuen übersetzbaren String darf der bestehende Block in **beiden** Katalogen manuell ergänzt werden. Übernehme `msgid`, aktuelle `#:`-Referenz(en), `msgstr` (DE identisch, EN übersetzt) und etwaige `msgctxt`/Plural-/Format-Flags. Danach `msgfmt --check-format` und `python src/manage.py compilemessages` ausführen. Das ist der schnelle Pflegeweg, kein Ersatz für die periodische Vollständigkeitsprüfung.
- **Vollabgleich ohne Reorder-Churn:** `python scripts/i18n_catalog.py check` (= `make i18n-check`) extrahiert DE und EN in einer temporären Repo-Kopie und meldet neue bzw. entfernte `msgid`s, ohne Dateien zu verändern. Fachlicher Drift endet mit Exit-Code 1; reine Referenz-/Positionsänderungen bleiben informativ und grün. Neue oder geänderte Einträge zuerst in **beiden** Katalogen übersetzen bzw. für DE als Identität ergänzen, dann `python scripts/i18n_catalog.py update` ausführen. `update` übernimmt denselben Vollabgleich, behält dabei die bestehende Reihenfolge aller unveränderten PO-Blöcke bei und validiert beide PO-/MO-Paare vollständig, bevor es schreibt; ein Schreibfehler stellt bereits ersetzte Dateien wieder her. Der Report trennt neue (`+`), entfernte (`-`) und semantisch geänderte (`~`) Einträge vom Referenzrauschen; neue Einträge werden am Katalogende angefügt. **Verbindliches Gate (#1933):** `make i18n-check` ist fail-fast Teil von `make ci`/`make ci-locked` und fängt insbesondere den Verlust eines nur zur Laufzeit referenzierten `msgid` ab (z. B. eine Formular-Fehlermeldung) — ein Fall, den die übrigen Kataloggates (fuzzy/leer, Versions-Stempel, `.mo`-Frische) nicht abdecken, weil der Eintrag dort gar nicht mehr existiert statt nur falsch befüllt zu sein.
- **Kein Übersetzungsaufruf INNERHALB eines f-String-Ausdrucks.** `f"{gettext('Bericht')}"` extrahieren nur neuere `xgettext`-Versionen (gemessen: 0.23.1 ja, 0.21 nein); die CI-Toolchain (`ubuntu-24.04`, `gettext 0.21`) sieht den Aufruf nicht und zählt den f-String als ein einziges Stringliteral. Der Katalog-Abgleich urteilt dann je nach Arbeitsplatz anders — und schlimmer: `make i18n-update` würde die dort stehende `msgid` samt englischer Übersetzung still aus dem Katalog löschen. Deshalb: Aufruf **vor** den f-String ziehen und die Variable einsetzen (`report_label = gettext("Bericht")` … `f"{report_label}"`). Guard: [`src/tests/test_i18n_fstring_extraction_guard.py`](src/tests/test_i18n_fstring_extraction_guard.py) (Refs #1933).
- **DE ist Quellsprache ([ADR-027](docs/adr/027-i18n-strategy.md)).** Jeder Eintrag ist eine Identitäts-Übersetzung (`msgstr == msgid`); die Auflösung nach einem Lauf ist deshalb mechanisch: `msgattrib --clear-fuzzy --empty --clear-previous <po> | msgen`. Eine deutsche `msgstr`, die von ihrer `msgid` abweicht, ist ein Bug (meist ein msgmerge-Fuzzy-Fehlmatch) — genau darauf zielt der Duplikat-Guard in [`scripts/check_translations.py`](scripts/check_translations.py) (Refs #1348).
- **EN wird übersetzt, nicht fuzzy gelassen.** `msgfmt` verwirft fuzzy-Einträge beim Kompilieren — die englische Oberfläche fällt dort auf Deutsch zurück. [`src/tests/test_i18n_catalog.py`](src/tests/test_i18n_catalog.py) verbietet sowohl `#, fuzzy` als auch leere `msgstr`. Terminologie aus [`docs/en/glossary.md`](docs/en/glossary.md) und dem Bestand des Katalogs übernehmen.
- **Kompilate committen:** nach jedem Lauf `python src/manage.py compilemessages`; die `.mo`-Dateien liegen im Repo (das Deployment baut sie nicht).
- **Baselines nur senken.** Die Schwellen in `scripts/check_translations.py` stehen (#1745) für **beide** Sprachen auf 0 fuzzy / 0 unübersetzt. Wer sie anhebt, macht den nächsten Drift unsichtbar; der Weg über die Schwelle ist übersetzen, nicht nachjustieren.

### Conventional Commits

Commit-Messages folgen dem [Conventional Commits](https://www.conventionalcommits.org/)-Standard:

```
<typ>(<scope>): <kurze Beschreibung>

[optionaler Body]

Refs #<issue-nummer>
```

**Typen:**

| Typ        | Verwendung                                      |
|------------|-------------------------------------------------|
| `feat`     | Neues Feature                                   |
| `fix`      | Bugfix                                          |
| `test`     | Tests hinzufügen oder anpassen                  |
| `docs`     | Dokumentation                                   |
| `chore`    | Wartungsarbeiten, Konfiguration, Dependencies   |
| `refactor` | Refactoring ohne Verhaltensänderung             |
| `style`    | Formatierung, kein Logik-Unterschied            |

**Beispiele:**

```
feat(clients): add duplicate-detection on import

fix(events): prevent deletion of locked events

test(security): add field-sensitivity E2E tests

Refs #42
```

Commits sind atomar: eine logische Änderung pro Commit. Direkt nach jeder Aufgabe zu pushen.

---

## Plugin-Entwicklung

Zwei Dinge lassen sich erweitern, **ohne** den Kern anzufassen: **Berichts-Backends** (Code,
ein Modul) und **Konfigurationspakete** (Daten, eine YAML-Datei). Beide brauchen weder eine
Migration noch eine neue URL, einen View oder einen Eintrag in der AuthZ-Matrix. Alles andere
— Modelle, Views, Middleware — ist kein Plugin-System, sondern normale Kernentwicklung nach
den Regeln oben.

### Berichts-Backends (Reporting-Plugins)

Ein Berichts-Backend kapselt die Fachlogik **eines** Berichtsstandards hinter einer festen
Schnittstelle. Panel-Vorschau, CSV-/JSON-/XML-Export und die Snapshot-Speicherung sprechen nur
diese Schnittstelle an und wissen nichts über den einzelnen Bericht. Praktische Folge: **ein
neuer Bericht ist eine Datei.**

Ein Modul unter [`src/core/reporting/backends/`](src/core/reporting/backends/) mit einer
`DatasetBackend`-Unterklasse und dem `@register_backend`-Decorator erscheint automatisch als
Karte auf dem Reiter „Datensatz-Berichte" und in allen drei Exportformaten:

```python
# src/core/reporting/backends/mein_bericht.py
from django.utils.translation import gettext_lazy as _

from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend


@register_backend("mein-bericht")  # Slug: ^[a-z0-9]+([-_][a-z0-9]+)*$, praktisch unveränderlich
class MeinBackend(DatasetBackend):
    name = _("Mein Bericht")  # Anzeigename — immer gettext_lazy
    description = _("Was dieser Bericht zählt, in einem Satz.")
    snapshot_enabled = False  # True = Roh-Aggregat wird monatlich persistiert

    def get_field_specs(self) -> list[FieldSpec]:
        return [FieldSpec("label", _("Merkmal")), FieldSpec("count", _("Anzahl"))]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult: ...  # liefert ROHE Zahlen

    def prepare_export(
        self, facility, result: DatasetResult
    ) -> DatasetResult: ...  # K-Anonymität, bevor irgendetwas das Haus verlässt
```

Fünf Punkte, an denen erfahrungsgemäß etwas schiefgeht:

1. **`build_dataset()` ist roh, `prepare_export()` ist der Schutzwall.** Alles, was ausgeliefert
   wird — Download **und** Panel-Vorschau —, läuft vorher durch `prepare_export()`. Wer Zahlen
   produziert, die extern zirkulieren, muss diesen Hook implementieren; die zentrale
   Suppressions-Maschinerie ist zu benutzen statt nachzubauen. Der Snapshot-Pfad ruft ihn
   bewusst **nicht** (interner Rohbestand) — `snapshot_enabled` ist damit ein
   Datenschutz-Vertrag, kein Feature-Schalter.
2. **Labels sind `gettext_lazy`**, nie deutscher Klartext — sie landen als CSV-Kopfzeile und
   JSON-Label auch in der englischen Oberfläche.
3. **Registrierung passiert zur Importzeit, ausschließlich über den Decorator.** Es gibt keine
   Konfigurationsliste. Discovery ist lazy (erst beim ersten `get_backends()`), damit die
   Importkette zyklusfrei bleibt; ein defektes Modul ist ein lauter `ImproperlyConfigured`,
   keine still unvollständige Registry.
4. **Der Slug ist praktisch unveränderlich** — er steckt in Query-Parametern, Dateinamen und
   bei `snapshot_enabled` in den Schlüsseln von `StatisticsSnapshot.backend_data`.
5. **Die Zuordnung läuft über `report_key`**, nie über eine Kategorie
   ([ADR-042](docs/adr/042-report-key-berichtszuordnung.md)). Genau **eine** dokumentierte
   Ausnahme: `ban-overview` bindet als hochsensibler Verhaltens-Bericht bewusst über
   `system_type` (`TypBindung(bindet="system_type")`) — Hausverbote sollen nicht über einen frei
   wählbaren Berichtsschlüssel in fremde Auswertungen geraten. Wer ausgerechnet dieses Modul als
   Vorlage kopiert, muss `bindet` auf `report_key` zurückstellen; die Registry-Auskunft
   („erscheint in …") und das Schlüssel-Vokabular ignorieren `system_type`-Bindungen sonst
   stillschweigend.

**Als Vorlage kopieren, nicht von Null anfangen.** Unter
[`src/core/reporting/backends/`](src/core/reporting/backends/) liegen neun mitgelieferte
Backends — alle sind **Beispiele**, keine verbindlichen Berichtsstandards, und jedes zeigt
einen anderen Zuschnitt: `example` (Minimalfall), `sample-report-1` (Vollfall mit Snapshot),
`monthly-trend` (Zeitreihe), `client-metrics` (exakte Partition), `case-metrics` (unabhängige
Skalare), `ban-overview` (hochsensibler Sonderfall), `needle-exchange-balance` (Summen über
Feldwerte), `counseling-hours` (Kaskade Zeile → Gesamt), `service-distribution`
(Mehrfachnennung). Such dir den Fall, der deinem am nächsten kommt, und kopiere **dieses**
Modul.

Sichtbarkeit prüfen:

```bash
python src/manage.py shell -c "from core.reporting.registry import get_backends; print([b.slug for b in get_backends()])"
```

**Vollständige Referenz** — `DatasetBackend`/`DatasetResult`/`FieldSpec`/`CompletenessIssue`,
„Eigenes Backend in fünf Schritten", Discovery-Regeln, Snapshot-Vertrag, K-Anonymität,
Exportformate und die Test-Checkliste für neue Backends:
[docs/reporting-plugins.md](docs/reporting-plugins.md)
(EN: [docs/en/reporting-plugins.md](docs/en/reporting-plugins.md)).
Architektur-Entscheidungen: [ADR-038](docs/adr/038-reporting-plugin-backends.md),
[ADR-042](docs/adr/042-report-key-berichtszuordnung.md),
[ADR-023](docs/adr/023-k-anonymization-statistik.md).

### Konfigurationspakete (YAML)

Ein Konfigurationspaket beschreibt Feldvorlagen, Dokumentationstypen, Feldgruppen,
Feldzuordnungen und Feldbedingungen einer Einrichtung **deklarativ**. Pakete machen
Formular-Konfiguration teilbar: versionierbar, reviewbar, zwischen Trägern austauschbar — und
**idempotent** einspielbar. Der mitgelieferte Standardkatalog
[`src/core/packages/default.yaml`](src/core/packages/default.yaml) ist selbst ein Paket und
läuft durch denselben Loader wie jedes fremde.

```bash
python src/manage.py load_package --file paket.yaml --facility <uuid> --dry-run
python src/manage.py load_package --default --facility <uuid>      # Standardkatalog nachziehen
```

Vier Regeln, die im Alltag zählen:

1. **Der Slug ist die Identität**, nicht der Name. Slugs sind nach dem Anlegen in Modell und
   DB-Trigger unveränderlich; ein geänderter Slug legt in **jeder** Einrichtung, die das Objekt trägt, ein
   Duplikat an, statt zu aktualisieren.
2. **Unbekannte Schlüssel sind auf jeder Ebene ein Fehler** — ein Tippfehler wie `sort_oder`
   soll auffallen, statt still zu verpuffen. Doppelte YAML-Schlüssel ebenso.
3. **Der Loader ist ein Rücksync, kein „anlegen, falls nicht vorhanden".** Er schreibt
   Paketwerte auf Bestandszeilen zurück; welche Felder das betrifft und welche er nie anfasst,
   steht in der Tabelle unter
   [Was der Loader-Seed einer bestehenden Einrichtung bewirkt](#was-der-loader-seed-einer-bestehenden-einrichtung-bewirkt).
   Deshalb: gegen Bestand **immer** erst `--dry-run`.
4. **Alles-oder-nichts.** Ein Konflikt bricht den gesamten Lauf ab; es wird nichts angewendet.

**Formatreferenz, Feld-für-Feld-Semantik, Loader-Buckets und die Pflegeregeln, die weh tun:**
[docs/packages.md](docs/packages.md) · [ADR-037](docs/adr/037-konfigurationspakete-dynamische-felder.md)
(Paketformat) · [ADR-036](docs/adr/036-globale-block-sequenz-feldordnung.md) (Feldordnung) ·
[ADR-035](docs/adr/035-evaluator-semantik-bedingte-feldlogik.md) (Operatoren der Feldbedingungen).
Die Gegenrichtung — eine bestehende Einrichtung vollständig als versioniertes JSON herausgeben
(`manage.py export_facility`) — beschreibt
[docs/facility-export-schema.md](docs/facility-export-schema.md).

---

## Tests

### Test-Driven Development (Unit/Service)

Die [Assertion-Konventionen](docs/testing/assertion-conventions.md) beschreiben verbindlich, wie Tests Verhalten präzise und mutationsfest prüfen.

Seit 2026-05-20 ist Test-Driven Development für die Unit-/Service-Schicht verpflichtend. Reihenfolge **vor** jeder Service-, Form-, Model- oder CBV-Änderung:

1. **Red** — pytest-Test in der passenden Datei unter `src/tests/` schreiben, der das gewünschte Verhalten beschreibt, aber heute fehlschlägt. Test mit `pytest -x` ausführen und prüfen, dass er mit dem erwarteten `AssertionError` rot wird.
2. **Green** — minimale Implementation in `src/core/...`, bis genau dieser Test grün wird. Keine zusätzlichen Features, keine vorgezogene Verallgemeinerung.
3. **Refactor** — Code (und ggf. Test) aufräumen, während die Suite grün bleibt. `pytest -x` nach jedem Cleanup-Schritt.

**Kein „falsch grün" — Assertion-Stärke schützen** (Refs #1150): Ein roter Test bedeutet im Default, dass die **Root Cause im Code** zu fixen ist — nicht der Test. Eine **bestehende Assertion abzuschwächen** (lockern, entfernen, ans tatsächliche kaputte Verhalten nachziehen) ist ein **harter Review-Stopp**: zulässig nur, wenn sich die *Anforderung* geändert hat (nicht der Code), und dann mit expliziter Begründung im Commit-Body. Bei **Bugfixes** zuerst einen **failing-test-first** schreiben, der den Bug reproduziert, *bevor* der Code angefasst wird (Skill `superpowers:systematic-debugging`). Hintergrund: 96 % Coverage misst *ausgeführte*, nicht *geprüfte* Zeilen — eine abgeschwächte Assertion bleibt grün und fällt erst im Mutation-Run auf.

Beispiel (Service-Schicht, Hash-Helfer in `core/services/audit/hash.py`, vereinfacht dargestellt; der echte Helfer `hmac_hash_value` löst den Schlüssel zusätzlich über einen Fallback auf):

```python
# Red — src/tests/test_hash_value_example.py
def test_hash_value_is_stable(settings):
    settings.AUDIT_HASH_KEY = "test-key"

    from core.services.audit.hash import hmac_hash_value

    assert hmac_hash_value("anlauf-2026-0001") == hmac_hash_value("anlauf-2026-0001")
    assert hmac_hash_value("anlauf-2026-0001") != hmac_hash_value("anlauf-2026-0002")
```

```bash
pytest src/tests/test_hash_value_example.py -x
# → ImportError, solange der Helfer fehlt → erwartetes Red.
```

```python
# Green — src/core/services/audit/hash.py (vereinfacht)
import hashlib
import hmac

from django.conf import settings


def hmac_hash_value(value: str) -> str:
    key = settings.AUDIT_HASH_KEY.encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()
```

```bash
pytest src/tests/test_hash_value_example.py -x
# → 1 passed → Green.
```

**Refactor:** etwa Auslagerung der Key-Auflösung in einen Helper, sobald ein zweiter Hash-Anwendungsfall existiert — Test bleibt grün.

**Geltungsbereich** (TDD-Pflicht):

- Service-, Form-, Model-, Helper-, CBV-/View-Unit-Tests, also alles unter `src/tests/` außerhalb von `src/tests/e2e/`.

**Ausnahmen** (manuell-first / TDD-neutral):

- **E2E-Tests** in `src/tests/e2e/` sind Playwright-getrieben — erst manuell durchklicken (siehe `### End-to-End-Tests (Playwright)`), dann Tests aus der Beobachtung schreiben.
- Django-Migrations-Generierung, Squash-Migrations, Tooling-/Konfig-Patches (CI-Schwellen, Whitelists, `pyproject.toml`).
- Reine Markdown-/Doku-Änderungen.
- One-Shot-Hygiene-Skripte ohne Wiederverwendung.

Querverweis: die Testkonventionen im folgenden Abschnitt [Unit- und Integrationstests](#unit--und-integrationstests).

### Unit- und Integrationstests

```bash
make test
# entspricht: python -m pytest -m "not e2e"
```

Tests liegen unter `src/tests/`. Die drei größten Familien haben eigene Unterpakete mit Familien-`conftest.py` (Refs #1839): `src/tests/reporting/` (Berichts-Backends), `src/tests/file_vault/` (Datei-Tresor) und `src/tests/offline/` (Offline-Bundle/-Shells); alle übrigen Unit-Tests liegen flach in `src/tests/`. Neue Tests kommen in die passende Datei oder in eine neue Datei nach dem Muster `test_<feature>.py` — in das Familien-Unterpaket, wenn es eines gibt. Fixtures, die eine ganze Familie braucht, gehören in deren `conftest.py`; suiteweite Fixtures in `src/tests/conftest.py`. Die Mutations-Pin-Dateien (`test_mutation_followup_*.py`, `test_mutation_pins_*.py`) bleiben bewusst flach — ihre Node-IDs zeigen auf konkrete Mutanten.

**Fixtures:** Suiteweite Fixtures zuerst in `src/tests/conftest.py`; erst wenn eine ganze Familie sie braucht (≥ 2 Dateien), gehören sie in deren `conftest.py`. Sonderfälle leiten die dateilokale Fixture gleichen Namens ab (`def facility(facility)`), statt die Root-Fixture zu kopieren. Für Request-Objekte `pytest-django`-`rf` verwenden.

**Wertetabellen:** Gleiche Prüflogik × verschiedene Eingaben gehört in `@pytest.mark.parametrize` mit sprechenden `ids=` — Muster: `test_boundary_*`, `_authz_expectations.py`, `_entity_expectations.py`.

**Factories:** Neue Tests, die `Event`, `DocumentType` oder `Client` per `.objects.create(...)` anlegen, verwenden die Wrapper aus `src/tests/_factories.py` (`make_event`, `make_document_type`, `make_client`), sobald mindestens die je Modell festen Pflichtfelder explizit gesetzt werden — abweichende Fälle (fehlendes `client`/`created_by` bei nullable Feldern, `**kwargs`-Weiterreichung) bleiben bei direktem `Model.objects.create(...)`. Die Factories legen bewusst genau ein Objekt an und setzen keine Defaults für variierende Felder — wer ein Feld braucht, übergibt es explizit über `**overrides`, damit kein Testverhalten still von einem Factory-Default abhängt. Neue Wrapper für weitere Modelle nur nach demselben Muster und nach Rücksprache mit der Projektbetreuung ergänzen.

**Parallel:** `make test-parallel` nutzt pytest-xdist für parallele Ausführung auf allen verfügbaren CPU-Kernen. `make ci` nutzt automatisch die parallele Variante.

### End-to-End-Tests (Playwright)

```bash
make test-e2e
# entspricht: python -m pytest -m e2e --browser chromium
```

E2E-Tests liegen unter `src/tests/e2e/`. Sie sind mit `@pytest.mark.e2e` markiert und werden bei `make ci` automatisch ausgeschlossen, da sie einen laufenden Server mit Seed-Daten benötigen.

**Datenbank:** E2E-Tests nutzen eine eigene Datenbank (`anlaufstelle_e2e`), die automatisch erstellt, geflusht und mit Seed-Daten befüllt wird. Dev-Daten in `anlaufstelle` bleiben unangetastet. Details: [docs/e2e-architecture.md](docs/e2e-architecture.md).

**Wichtig:** Jedes neue Feature muss E2E-Tests enthalten, die zusammen mit dem Feature entwickelt werden — nicht nachträglich. Neue E2E-Test-Dateien werden nach dem Muster `test_<stream_oder_feature>.py` benannt.

Fixtures für Login, Server-Setup u. ä. liegen in `src/tests/e2e/conftest.py`; gemeinsame Helfer in `src/tests/e2e/_helpers.py` (`do_real_login`, `wait_for_active_service_worker`, `SEED_PASSWORD`, `django_shell`). Seeds binden die Ziel-Facility an den Admin-User (UUID-Primärschlüssel) — nie `order_by("pk").first()`. Ausführliches Runbook mit Checklisten und Troubleshooting: [docs/e2e-runbook.md](docs/e2e-runbook.md).

**Parallele E2E-Tests:** `make test-e2e-parallel` startet pro xdist-Worker einen eigenen gunicorn-Prozess auf eigenem Port mit eigener Datenbank. Default: 2 Worker (`E2E_WORKERS=4 make test-e2e-parallel` für mehr). `--dist loadfile` hält Tests aus derselben Datei auf demselben Worker.

**Smoke-Tests:** `make test-e2e-smoke` führt nur mit `@pytest.mark.smoke` markierte E2E-Tests aus (~40 kritische Flows, ~2-3 min). Ideal zur schnellen Validierung nach Feature-Implementierung.

### Test-Schichten-Modell beim Entwickeln

Während der Entwicklung nie die volle Suite laufen lassen — in Schichten vorgehen, jeweils mit Fail-Fast (`pytest -x`):

1. **Fokus:** `pytest src/tests/test_<betroffene_datei>.py -x` — nur Tests im geänderten Bereich (bei Familien-Unterpaketen `pytest src/tests/<familie>/test_<datei>.py -x`, ganze Familie: `pytest src/tests/<familie> -x`). E2E fokussiert: `pytest src/tests/e2e/test_<feature>.py -x`.
2. **Gruppe:** Betroffene Testdateien zusammen: `pytest src/tests/test_a.py src/tests/test_b.py -x`.
3. **Parallel:** `make test-parallel` — volle Unit-Suite parallel, vor dem Commit.
4. **Smoke:** `make test-e2e-smoke` — ~40 kritische E2E-Flows (~2-3 min), nach Feature fertig.
5. **E2E:** `make test-e2e-parallel` — volle E2E-Suite parallel, vor Push.

**Fail-Fast immer:** Tests mit `pytest -x` ausführen. Bei Fehlern fixen, prüfen ob verwandte Tests denselben Root Cause teilen, erst dann wieder Gesamtlauf. Re-Runs: `pytest --lf -x` (nur zuletzt fehlgeschlagene) oder `pytest --ff -x` (fehlgeschlagene zuerst).

**Wait-Strategie (E2E):** `domcontentloaded` oder `wait_for_url()` — **niemals** `networkidle`.

### Vollständige CI-Pipeline lokal

```bash
make ci
# entspricht: lint + check + Guards (deps/matrix/release-test/vendor-js/agent-docs) + audit-js + i18n-check + typecheck + test-js + test-parallel
```

Diese Pipeline muss vor jedem Pull Request lokal grün sein.

**JS-Gate (`make test-js`, Refs #1932):** führt `npm test` (= `vitest run`, non-watch, deterministisch) gegen die Offline-/Queue-/Watcher-Suite unter [`tests/js/`](tests/js/) aus und ist fail-fast Teil von `make ci`/`make ci-locked`. Fehlt `node_modules/` (kein vorheriges `npm ci`) oder `package-lock.json`, scheitert das Target mit einer klaren Fehlermeldung statt eines kryptischen `vitest: not found`.

**JS-Advisory-Gate (`make audit-js`, Refs #1968):** führt `npm audit --audit-level=$(AUDIT_LEVEL)` gegen `package-lock.json` aus — das Gegenstück zu `pip-audit` auf der Python-Seite, das es für JavaScript vorher nicht gab. Es hängt bewusst in `make ci` und nicht nur im GitHub-Workflow: Ob die Workflows im internen Entwicklungszweig laufen, ist eine Einstellung der Maintainer; dort gilt `make ci` als verbindliches Tor ([ADR-029](docs/adr/029-local-only-ci.md)), am öffentlichen Repository führt der Test-Workflow `npm audit` zusätzlich am Pull Request aus.

**Damit braucht die Gate-Kette Netzzugang.** `npm audit` liest zwar nur das Lockfile (kein `npm ci`, kein `node_modules/` nötig), fragt die Advisory-Daten aber bei der npm-Registry ab. Auf einem Host ohne Registry-Zugang bricht `make ci` in `audit-js` ab — und zwar direkt nach `deps-check`, also bevor ein einziger Test läuft. Einzelne Gates lassen sich in dem Fall weiter gezielt fahren (`make lint`, `make typecheck`, `make test-parallel`).

Schwelle `moderate` statt `high`, weil die Meldung, die das Gate ausgelöst hat, selbst nur `moderate` war. Der Wert steht als `AUDIT_LEVEL ?= moderate` im Makefile und ist überschreibbar: `make ci AUDIT_LEVEL=high` bzw. `make audit-js AUDIT_LEVEL=high`. Das ist die Notbremse für den einen Fall, den das Gate sonst nicht auflösen kann — eine frisch veröffentlichte `moderate`-Advisory **ohne verfügbaren Fix** blockiert ab ihrer Veröffentlichung jeden Commit. Der Default bleibt blockierend bei `moderate`; ein Hochsetzen gehört in die PR-Beschreibung, nicht ins Makefile.

Sämtliche Node-Pakete des Projekts sind `devDependencies` (Tailwind-CLI + Vitest-Toolchain) — deshalb kein `--omit=dev`, das Gate wäre sonst leer. Sie werden allerdings auch im Container-Bau installiert: die Node-Stufe des [`Dockerfile`](Dockerfile) ruft `npm ci` ohne `--omit=dev`; ausgeführt wird dort nur die Tailwind-CLI, und ins Endimage wandert allein die erzeugte `styles.css`.

### Mutation-Testing (mutmut)

Mutation-Testing prüft, wie viele synthetische Code-Mutationen die Test-Suite tatsächlich erkennt. Konfiguration in `pyproject.toml` (`[tool.mutmut]`). Erwartete Laufzeit: 3–6 h auf `core.services` + `core.forms`. Daher nicht PR-Pflicht, sondern punktuell pro Wellen-Issue.

```bash
make mutation           # mutmut run via scripts/dev/run_mutmut.py
make mutation-report    # Survivors-Liste, nicht-interaktiv
```

`make mutation`, `make mutation-report`, `make mutation-delta BASE=<ref>` und
`make mutation-skipped` sind Maintainer-/dev-only-Werkzeuge. Im öffentlichen
Baum melden sie bei fehlenden gestrippten Skripten
`not available in the public source tree (maintainer tool)`; das ist kein
Mutationsnachweis. Es gibt kein verlässliches Resume: Ein Neustart von
`mutmut run` beginnt eine neue Messung und verliert den Fortschritt.

Für längere Runs auf einer Sandbox, die OOM-Killer / Idle-Killer mitbringt, gibt es `scripts/dev/run_mutmut_watchdog.sh` (dev-only):

```bash
# Standard: 0 zusätzliche Neustarts, Stillstandsschwelle 5 min, 2 mutmut-Worker
scripts/dev/run_mutmut_watchdog.sh

# Aggressiveres Profil
MAX_RESTARTS=5 STALL_THRESHOLD=600 MUTMUT_MAX_CHILDREN=4 \
    scripts/dev/run_mutmut_watchdog.sh
```

Der Watchdog erkennt stillstehende oder gestorbene Master-Prozesse. Standardmäßig endet er ohne Neustart (`MAX_RESTARTS=0`); ausdrücklich aktivierte Neustarts beginnen eine neue Messung und verlieren den Fortschritt. Exit 0, sobald `mutants/mutmut-stats.json` während der Watchdog-Session geschrieben wurde. Aufruf-Hintergrund: Refs #937.

---

## Pull-Request-Prozess

1. **Branch erstellen** — von `main` branchen, sprechenden Branch-Namen verwenden:
   ```bash
   git checkout -b feature/kurze-beschreibung
   # oder
   git checkout -b fix/was-repariert-wird
   ```

2. **Entwickeln** — kleine, atomare Commits; Issue-Nummern referenzieren (`Refs #N`).

3. **Lokal verifizieren:**
   ```bash
   make ci          # Lint, Check, Tests
   make test-e2e    # E2E-Tests
   ```
   Außerdem manuell im Browser prüfen, dass die Änderung wie erwartet funktioniert.

4. **Pull Request öffnen:**
   - Titel im Conventional-Commits-Stil (`feat: ...`, `fix: ...`)
   - Beschreibung: Was wurde geändert und warum? Welche Issues werden geschlossen? Die Vorlage [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md) ist dabei verbindlich (siehe „PR-Body-Pflicht" unten).
   - Screenshot oder Demo, wenn UI-Änderungen enthalten sind
   - Verlinkung des zugehörigen GitHub-Issues

5. **Review:** Mindestens ein Approval erforderlich. Feedback sachlich und konstruktiv.

6. **Übernahme:** Sobald CI grün ist und ein Approval vorliegt, übernimmt der Maintainer deine Commits. Externe Pull Requests werden nicht direkt auf `main` gemergt, sondern über den Release-Spiegel übernommen (siehe unten).

### Code of Conduct

Alle Mitwirkenden verpflichten sich auf den [Contributor Covenant 2.1](CODE_OF_CONDUCT.md). Belästigungsfälle melden Mitwirkende vertraulich an `kontakt@anlaufstelle.app` (Refs #836).

### Wie dein PR gemergt wird (Release-Spiegel)

Der `main`-Branch dieses Repositories ist ein **Release-Spiegel**: Er wird bei jedem Release
aus dem internen Entwicklungszweig aufgebaut (dabei werden rein interne
Entwicklungswerkzeuge entfernt). Externe Pull Requests werden deshalb nicht direkt auf
`main` gemergt, sondern:

1. Das Review findet ganz normal hier am PR statt.
2. Nach Freigabe übernehmen wir deine Commits in den Entwicklungszweig — dein
   Git-Author-Feld (und damit deine Urheberschaft) bleibt erhalten.
3. Mit dem nächsten Release erscheint deine Änderung auf `main`; dein PR wird dann mit
   Verweis auf das Release geschlossen und im CHANGELOG genannt.

Dass ein PR „geschlossen" statt „gemergt" angezeigt wird, ist also keine Ablehnung — es ist
die Mechanik des Release-Spiegels.

---

## Lizenz von Beiträgen

### Inbound = Outbound (AGPL-3.0)

Dieses Projekt ist unter der [GNU AGPL v3](LICENSE) lizenziert. Mit dem Einreichen eines
Beitrags (Pull Request, Patch, Doku-Änderung) lizenzierst du deinen Beitrag unter derselben
Lizenz („inbound = outbound", vgl. auch [GitHub Terms of Service, D.6](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service#6-contributions-under-repository-license)).
Du behältst dein Urheberrecht — es findet keine Rechteübertragung statt, und wir verlangen
kein Contributor License Agreement (CLA).

### Developer Certificate of Origin (DCO)

Mit jedem Commit bestätigst du das [Developer Certificate of Origin 1.1](https://developercertificate.org/):
dass du das Recht hast, den Beitrag unter der Projektlizenz einzureichen — weil er von dir
stammt oder aus kompatibel lizenzierten Quellen mit intakten Lizenzhinweisen übernommen wurde.

Dazu signierst du jeden Commit mit deinem Namen und einer gültigen E-Mail-Adresse:

    git commit -s

Das fügt eine Zeile `Signed-off-by: Vorname Nachname <email@example.org>` an die
Commit-Message an. Pull Requests ohne Sign-off können wir nicht übernehmen; ein vergessenes
Sign-off lässt sich mit `git commit --amend -s` bzw. `git rebase --signoff` nachholen.

**Lokale Prüfung vor dem PR:** Prüfe alle vorgesehenen PR-Commits mit:

    make dco-check RANGE=origin/main..HEAD

Der Check ist ein lokales Maintainer-/dev-only-Werkzeug. Im öffentlichen Baum
meldet das Target seine Nichtverfügbarkeit; stattdessen die `Signed-off-by`-
Zeilen mit `git log --format=full origin/main..HEAD` prüfen. Die
Nichtverfügbarkeitsmeldung ist keine bestandene DCO-Prüfung. Es gibt keinen
serverseitigen Zwang; ob die
GitHub-DCO-App aktiviert wird, entscheidet der Maintainer.

**Fremdmaterial:** Übernimm keinen Code und keine Texte aus Quellen, deren Lizenz nicht
AGPL-kompatibel ist. Kennzeichne Übernahmen (Quelle + Lizenz) im Commit oder PR.

**KI-Unterstützung:** Beiträge dürfen mit KI-Werkzeugen erstellt sein (dieses Projekt nutzt
sie selbst). Für die Rechte am Ergebnis bürgst du per Sign-off wie für jeden anderen
Beitrag — prüfe KI-Output entsprechend sorgfältig.

**PR-Body-Pflicht:** Für Pull Requests ist die Vorlage [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md) verbindlich — Zweck/Refs, Änderungen, Verifikation und die DCO-Bestätigung gehören in den PR-Body. Jeder Commit trägt `Signed-off-by: Name <E-Mail>`; fehlt der Sign-off, fragen wir nach und übernehmen den PR erst mit vollständigem Sign-off. Ein CI-Zwang für den Sign-off besteht in dieser Fassung bewusst nicht.

### Namensnennung

Deine Urheberschaft bleibt erhalten: Beim Übernehmen deiner Commits in den internen
Entwicklungszweig bleibt dein Git-Author-Feld (bzw. ein `Co-authored-by:`-Trailer) bestehen.
Der öffentliche `main`-Branch ist ein Release-Spiegel (s. o., „Wie dein PR gemergt wird");
dort werden externe Beiträge im [CHANGELOG](CHANGELOG.md) des jeweiligen Releases namentlich
genannt.

---

## Architektur-Überblick

> **Ausführlicher Wegweiser:** [docs/architecture.md](docs/architecture.md) — Schichtung
> (Views → Services → Models), Sicherheitsarchitektur, No-Code-Konfigurationskern,
> Offline-PWA und Test-Landschaft, jeweils mit Verweis auf die zuständige ADR. Der Abschnitt
> hier bleibt der Kurzeinstieg für Rollen, Verzeichnisstruktur und die Designentscheidungen,
> die man beim ersten Pull Request kennen muss.

### Rollen

Anlaufstelle kennt fünf Rollen — vier facility-gebunden, eine facility-übergreifend:

| Rolle | DB-Wert | Scope | Beschreibung |
|-------|---------|-------|--------------|
| Systemadministration | `super_admin` | facility-übergreifend (`/system/`) | Hosting, Bootstrap, Pre-Auth-AuditLogs. Wird über `manage.py create_super_admin` angelegt. |
| Anwendungsbetreuung | `facility_admin` | eine Einrichtung | Vollzugriff in der eigenen Facility (Audit-Log, DSGVO-Paket, Benutzerverwaltung). |
| Leitung | `lead` | eine Einrichtung | Leitungsebene, erweiterte Auswertungen, Löschanträge genehmigen. |
| Fachkraft | `staff` | eine Einrichtung | Kernarbeit mit Personen und Events. |
| Assistenz | `assistant` | eine Einrichtung | Eingeschränkter Zugriff, unterstützende Aufgaben. |

Architektur-Entscheidung: [ADR-018](docs/adr/018-rollenmodell-superadmin.md). Details zum RLS-Bypass für `super_admin`: [ADR-005 Update 2026-05-10](docs/adr/005-facility-scoping-and-rls.md).

### Projektstruktur

```
src/
  manage.py
  anlaufstelle/          # Django-Projekteinstellungen (settings, urls, wsgi)
  core/
    models/              # Ein Model (oder eng verwandte Models) pro Datei
      organization.py    # Organization, Facility
      user.py            # User (erweitert AbstractUser)
      client.py          # Client
      document_type.py   # DocumentType
      field_template.py  # FieldTemplate
      document_type_field.py  # DocumentTypeField
      event.py           # Event
      event_history.py   # EventHistory
      workitem.py        # WorkItem, DeletionRequest
      time_filter.py     # TimeFilter
      case.py            # Case
      episode.py         # Episode
      outcome.py         # OutcomeGoal, Milestone
      audit.py           # AuditLog
      settings.py        # Settings
    views/               # Class-based Views, aufgeteilt nach Funktionsbereich
      zeitstrom.py       # ZeitstromView (Startseite) und seine Teilansichten
      clients.py         # Client CRUD
      events.py          # Event CRUD + Löschworkflow
      workitems.py       # WorkItem CRUD
      cases.py           # Case, Episode, Goal, Milestone
      search.py          # Volltextsuche
      statistics.py      # Statistiken + Exporte
      audit.py           # AuditLogListView
      auth.py            # Login/Logout/Passwort
      account.py         # Benutzerprofil
      health.py          # HealthView
      mixins.py          # Rollen-Mixins
      pwa.py             # Service Worker
    services/            # Business-Logik (Verschlüsselung, Retention, …)
  templates/             # Django-Templates (HTMX-Partials eingeschlossen)
  static/
    css/
      input.css          # Tailwind-Eingabedatei
      styles.css         # Kompiliertes CSS (nicht committen)
  tests/
    conftest.py          # Suiteweite Fixtures
    test_<feature>.py    # Unit-/Integrationstests (flach)
    reporting/           # Familien-Unterpaket mit eigener conftest.py (Refs #1839)
    file_vault/          # dito (Datei-Tresor)
    offline/             # dito (Offline-Bundle/-Shells)
    e2e/                 # Playwright E2E-Tests
      conftest.py        # Shared Fixtures
      test_<feature>.py  # Tests pro Feature
```

### Wichtige Designentscheidungen

- **Verschlüsselung:** Sensible Felder werden auf Anwendungsebene verschlüsselt (`core/services/`). Der `ENCRYPTION_KEY` ist Pflicht in Produktion.
- **Audit-Log:** Alle sicherheitsrelevanten Aktionen werden in `AuditLog` geschrieben — über die typed Helper aus `core/services/audit/` (direkte `AuditLog.objects.create(...)` blockt ein Architekturtest). Jeder `detail`-Schlüssel muss in `core/services/audit/detail_policy.py` mit Begründung deklariert sein; ein neuer Schlüssel scheitert im Testlauf. Klartext-Freitext gehört nicht hinein — stattdessen `hmac_hash_value` oder die `target_id` verwenden (Refs #1944).
- **Retention:** Das Management-Command `enforce_retention` setzt Löschfristen durch.
- **HTMX-First:** Interaktive UI-Elemente werden bevorzugt über HTMX-Partials realisiert, um JavaScript minimal zu halten.
- **Service-Schicht:** Views delegieren Logik an Services — das erleichtert Tests und hält Views schlank.
