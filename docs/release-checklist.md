# Release-Checkliste

Checkliste fuer Releases der Anlaufstelle-Anwendung.

Stand: v0.10.2 (2026-04-28)

## 1. Pre-Release

- [ ] CI gruen auf `main` (Test, Check, Audit — siehe [test.yml](../.github/workflows/test.yml))
- [ ] Coverage nicht gesunken (Report in CI: `pytest --cov=core --cov-report=term-missing`)
- [ ] `CHANGELOG.md` aktualisiert — `[Unreleased]` in `[X.Y.Z] - YYYY-MM-DD` umwandeln
- [ ] `version` in `pyproject.toml` auf `X.Y.Z` gesetzt
- [ ] Keine offenen Issues mit Label `critical` oder `high`
- [ ] Migrations-Kompatibilitaet pruefen: Vorwaerts-Migration sicher? Kein Datenverlust bei `migrate --plan`?

### 1.1 Workflow-Sichtbarkeit zwischen Dev / Stage / App

Die 3-Repo-Pipeline (`tobiasnix/anlaufstelle` privat → `anlaufstelle/stage`
privat → `anlaufstelle/app` public) syncen `.github/workflows/` 1:1 mit dem
Release-Sync.

| Workflow | Dev (privat) | Stage (privat) | App (public) | Bemerkung |
|---|---|---|---|---|
| `test.yml`, `lint.yml`, `e2e.yml` | aktiv | aktiv | aktiv | Standard-CI |
| `release.yml` | nur auf `v*`-Tag | nur auf `v*`-Tag | nur auf `v*`-Tag | baut + published Image |
| `codeql.yml` | **skipped** | **skipped** | **aktiv** | Job-Level-Guard `if: github.event.repository.private == false` — auf privaten Repos wäre GitHub Advanced Security kostenpflichtig, im public App-Repo läuft CodeQL kostenfrei und meldet ans Security-Tab. Refs [#687](https://github.com/tobiasnix/anlaufstelle/issues/687). |

**Beim Sync nichts patchen:** Der Guard ist absichtlich workflow-intern,
damit dieselbe Datei in allen drei Repos liegt und nur GitHub das jeweils
relevante Verhalten bestimmt. Wenn eines Tages Dev/Stage GHAS bekommen,
genügt es, den `if:` zu entfernen — kein 3-Wege-Diff nötig.

### 1.2 Doc-Sync

Versionsabhängige Dokumentation **vor** dem Tag (Schritt 2) aktualisieren — sonst veraltet sie zwischen Releases unbemerkt.

**Immer prüfen:**

- [ ] [`SECURITY.md`](../SECURITY.md) — „Unterstützte Versionen"-Tabelle: aktuelle Minor-Reihe (`X.Y.x`) als unterstützt markieren, alte Reihen auf „Nicht unterstützt" setzen
- [ ] [`docs/threat-model.md`](threat-model.md) — Header-Zeile `Version: vX.Y.x · Letzte Revision: YYYY-MM-DD` aktualisieren
- [ ] [`docs/release-checklist.md`](release-checklist.md) — `Stand: vX.Y.Z (YYYY-MM-DD)` ganz oben aktualisieren
- [ ] [`README.md`](../README.md) + [`README.en.md`](../README.en.md) — Pre-Release-Banner und `translation-version` (wird im Version-Bump-Commit Schritt 2 mit-comittet)

**Bei Feature-/Pfad-Änderungen im Release zusätzlich prüfen:**

- [ ] [`docs/admin-guide.md`](admin-guide.md) — neue Konfig-Optionen, Deploy-Schritte, Env-Vars
- [ ] [`docs/user-guide.md`](user-guide.md) — neue UI-Flows, geänderte Berechtigungsmatrix
- [ ] [`docs/ops-runbook.md`](ops-runbook.md) — neue Cron-Jobs, Migrations-Hinweise, Notfall-Prozeduren
- [ ] [`docs/security-notes.md`](security-notes.md) — neue bewusste Security-Design-Entscheidungen oder Audit-Referenzen
- [ ] [`docs/faq.md`](faq.md) — neue/geänderte FAQ-Antworten (synchron mit [Issue #474](https://github.com/tobiasnix/anlaufstelle/issues/474) halten)

> **Tipp:** `git log <PREV_TAG>..HEAD --oneline -- docs/ SECURITY.md README*.md` zeigt alle Doc-relevanten Commits seit dem letzten Release.

## 2. Release

```bash
# Tag setzen und pushen — loest release.yml aus
git tag vX.Y.Z
git push origin vX.Y.Z
```

- [ ] Git-Tag `vX.Y.Z` gesetzt (muss mit `v` in `pyproject.toml` uebereinstimmen)
- [ ] Tag gepusht — Release-Workflow ([release.yml](../.github/workflows/release.yml)) baut Docker-Image
- [ ] GitHub Actions: Build erfolgreich, Image unter `ghcr.io/tobiasnix/anlaufstelle:vX.Y.Z` und `:latest` verfuegbar
- [ ] Image auf Staging deployen (`docker compose pull && docker compose up -d`)
- [ ] Staging-Smoke-Test:
  - [ ] `GET /health/` liefert `{"status": "ok", "version": "vX.Y.Z"}`
  - [ ] Login als Admin funktioniert
  - [ ] Klient anlegen, bearbeiten, loeschen
  - [ ] ClamAV-Smoke-Check ([#524](https://github.com/tobiasnix/anlaufstelle/issues/524)) — Upload-Scanner erreichbar:
    ```bash
    curl https://<domain>/health/ | jq '.clamav'
    # Erwartet: "ok" (OK-Wert)
    ```
    Bei `error`/`disabled` wird jeder Datei-Upload fail-closed abgewiesen — vor Rollout klaeren, ob das gewollt ist.
  - [ ] RLS-Aktiv-Check ([#542](https://github.com/tobiasnix/anlaufstelle/issues/542)) — Row-Level-Security auf allen `core_*`-Tabellen aktiv:
    ```sql
    -- per psql in Produktions-DB (readonly-User reicht):
    SELECT relname FROM pg_class
    WHERE relname LIKE 'core_%' AND relrowsecurity = true
    ORDER BY relname;
    -- Erwartet: 16 Zeilen. Fehlen Tabellen, wurde migration nicht sauber ausgerollt.
    ```
  - [ ] Offline-Key-Salt-Endpoint: `GET /auth/offline-key-salt/` mit gueltiger Session liefert `200`, ohne Session `401`.
  - [ ] Token-Invite-E-Mail-Test: ersten Admin per `python manage.py setup_facility` einladen und pruefen, dass die Invite-E-Mail tatsaechlich zugestellt wird (SMTP-Konfig, SPF/DKIM, Spam-Ordner).
- [ ] Migrations liefen fehlerfrei (Entrypoint fuehrt `migrate --noinput` automatisch aus)

## 3. Post-Release (Produktion)

- [ ] Datenbank-Backup vor Deploy erstellen
- [ ] Deploy ausfuehren:
  ```bash
  docker compose -f docker-compose.prod.yml pull
  docker compose -f docker-compose.prod.yml up -d
  ```
- [ ] Health-Check: `GET /health/` erreichbar, Status `ok`, Version `vX.Y.Z`
- [ ] Smoke-Test: Login als Admin, Dashboard laden
- [ ] ClamAV-Prod-Check ([#524](https://github.com/tobiasnix/anlaufstelle/issues/524)): `curl https://<domain>/health/ | jq '.clamav'` liefert OK-Wert (sonst Datei-Uploads fail-closed).
- [ ] RLS-Prod-Check ([#542](https://github.com/tobiasnix/anlaufstelle/issues/542)): SQL-Query aus Staging-Smoke-Test gegen Produktions-DB ausfuehren — `18` `core_*`-Tabellen mit `relrowsecurity = true`.
- [ ] Sentry-Fehlerrate pruefen (falls konfiguriert) — keine neuen Fehler nach Deploy
- [ ] Backup nach erfolgreichem Deploy verifizieren (Integritaet, Wiederherstellbarkeit)

## Rollback

Falls Probleme nach dem Deploy auftreten:

```bash
# Auf vorheriges Image zuruecksetzen
docker compose -f docker-compose.prod.yml pull ghcr.io/tobiasnix/anlaufstelle:vPREVIOUS
docker compose -f docker-compose.prod.yml up -d
```

Achtung: Migrations-Rollback ist nicht automatisch — bei destruktiven Migrationen manuell `migrate <app> <previous_migration>` ausfuehren.
