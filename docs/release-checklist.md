# Release-Checkliste

Checkliste fuer Releases der Anlaufstelle-Anwendung.

## 1. Pre-Release

- [ ] CI gruen auf `main` (Test, Check, Audit — siehe [test.yml](../.github/workflows/test.yml))
- [ ] Coverage nicht gesunken (Report in CI: `pytest --cov=core --cov-report=term-missing`)
- [ ] `CHANGELOG.md` aktualisiert — `[Unreleased]` in `[X.Y.Z] - YYYY-MM-DD` umwandeln
- [ ] `version` in `pyproject.toml` auf `X.Y.Z` gesetzt
- [ ] Keine offenen Issues mit Label `critical` oder `high`
- [ ] Migrations-Kompatibilitaet pruefen: Vorwaerts-Migration sicher? Kein Datenverlust bei `migrate --plan`?

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
