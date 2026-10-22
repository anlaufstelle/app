# Screenshots — Anlaufstelle

> **[Deutsche Version](screenshots.md)** · back to the [README](../README.en.md)

A full tour of Anlaufstelle. All images come from the E2E environment prepared
by `make docs-screens` and show pseudonymised sample data only. The seeded
environment carries two facilities, each with its own catalogue: the main
facility (standard catalogue / drop-in centre) and "Zweigstelle Nord"
(package `beratungsstelle`, an open counselling service —
[`docs/packages-beratungsstelle.md`](packages-beratungsstelle.md), German only).
`--scale=large` adds three more facilities with the packages `awareness`,
`jugendarbeit` and `frauenhaus`.

Regenerate the images:

```bash
make docs-screens
```

Prerequisites: a running Docker database, installed Python dependencies and
Playwright with Chromium, and free port 8844. The target holds the global
`CI_LOCK`, runs **`seed --flush --scale=medium` (clears the E2E database)** and
writes DE/EN desktop and mobile images to `docs/screenshots/`.
Wait for an existing E2E server to finish, or stop your own run in its terminal;
the target does not terminate other servers.

## Daily work & documentation

### Timeline — the digital logbook
![Timeline](screenshots/zeitstrom-en.webp)

### Log an event
![Log an event](screenshots/ereignis-anlegen-en.webp)

### Log an event — counselling-service package
![Log an event (counselling service)](screenshots/ereignis-anlegen-beratungsstelle-en.webp)

Type selection for short counselling / counselling session / crisis session /
accompaniment from the counselling-service package (Zweigstelle Nord).

### Client list
![Client list](screenshots/personenliste-en.webp)

### Client history
![Client history](screenshots/personendetail-en.webp)

### Search — the full results page
![Search](screenshots/suche-en.webp)

## Case work

### Case file with episodes and goals
![Case file](screenshots/fallakte-en.webp)

### Shift handover
![Handover](screenshots/uebergabe-en.webp)

### Work hub — role-based landing
![Work hub](screenshots/arbeitszentrale-en.webp)

## Reporting & data protection

### Statistics
![Statistics](screenshots/statistiken-en.webp)

### Dataset reports — ready-made exports as cards
![Dataset reports](screenshots/statistik-berichte-en.webp)

### Privacy-friendly external report
![External report](screenshots/statistik-extern-en.webp)

### GDPR data package
![GDPR package](screenshots/dsgvo-paket-en.webp)

## Area overviews

### Privacy — data-subject rights, deadlines and records
![Privacy area](screenshots/datenschutz-uebersicht-en.webp)

### Administration — forms, fields, data import
![Administration area](screenshots/verwaltung-uebersicht-en.webp)

## Access

### Sign in
![Sign in](screenshots/login.webp)

## Mobile

| Timeline (mobile) | Work hub (mobile) |
|:---:|:---:|
| ![Timeline mobile](screenshots/zeitstrom-mobil-en.webp) | ![Work hub mobile](screenshots/arbeitszentrale-mobil-en.webp) |

<!-- translation-source: docs/screenshots.md -->
<!-- translation-version: v0.99.8 -->
<!-- translation-date: 2026-09-18 -->
<!-- source-hash: a0d31601483beda9cde161da29d264656acbc373 -->
