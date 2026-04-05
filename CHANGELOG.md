# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.9.1] - 2026-04-05

### Added

- **Standardsprache** persistent im Nutzerprofil speichern (DE/EN)
- **Analytics Charts** — Trend-Diagramme im Statistik-Dashboard mit monatlicher Aufschlüsselung nach Dokumentationstyp, inkl. User-Guide (DE + EN)
- **Sentry-Integration** — automatische Fehlererfassung in Produktion
- **JSON-Logging** — strukturiertes Logging für Produktionsumgebung
- **Coverage-Infrastruktur** — pytest-cov mit CI-Gates für Testabdeckung
- **Test-Parallelisierung** — pytest-xdist mit Worker-Isolation + Smoke-Marker

### Fixed

- CSP `unsafe-eval` für Alpine.js — behebt kaputtes Frontend
- Kontakt ohne Klientel wird automatisch als anonym markiert
- Anonym-Checkbox entfernt — Anonymität aus fehlender Klientel ableiten
- Chart.js Registry-Konflikt bei HTMX-Swap behoben
- E2E-Tests für xdist-Parallelisierung stabilisiert
- Autocomplete-E2E-Tests: Debounce-Race-Condition & nicht-deterministische Seed-Reihenfolge behoben

### Changed

- **Produktionshärtung** — CSP-Header, Docker-Konfiguration
- **Go-Live-Vorbereitung** — Runbook, Checkliste, Staging-Pipeline, E2E-Workflow
- **Testabdeckung** erweitert: Scope, RBAC-Matrix, Deletion-Requests, Management-Commands
- **Seed-Daten** finalisiert: realistische Tagesverteilung, Heute-Logik, Mitarbeiter-Zuordnung

## [0.9.0] - 2026-03-28

Initial public release.
