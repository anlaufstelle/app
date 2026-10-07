# Manuelle Test-Matrix — Anlaufstelle

**Stand:** 2026-05-09 · **Version:** 1.0 · **Geltungsbereich:** Anlaufstelle ≥ v0.10  
**GitHub-Issue:** #864

> Diese Datei ist die **Single-Source-of-Truth** für manuelles Testen. Sie ist ein **paralleler Artefakt** zur automatisierten E2E-Suite (~280 Tests in `src/tests/e2e/`) — kein Code-Klon. Manuelle Tests sind erforderlich für (a) DSGVO-Audit-Sign-Off, (b) Pilotbetrieb mit echten Sozialarbeiter:innen und (c) Release-Candidate-Verifikation auf Browser-/Mobile-Spezifika.
>
> Drei Sektionen für drei Zielgruppen:
> - **Sektion A** — Anwender-Smoke (Sozialarbeiter:in, Klartext-Workflows)
> - **Sektion B** — Entwickler-Komplett (Tobias, alle Bereiche systematisch)
> - **Sektion C** — Auditor-DSGVO/Security (Compliance, RLS-Penetration)
>
> **Welche TC-IDs gehören in welchen Testlauf?** Siehe `release-test-profiles.md` (PR-Smoke, RC-Smoke, Security-RC, Mobile-PWA-RC, Ops-RC, Major-Release). Lauf-Ergebnisse werden in `runs/` abgelegt, nicht hier.

---

## Inhaltsverzeichnis

- [Setup-Block](#setup-block)
- [Status-Legende](#status-legende)
- [TC-ID-Schema](#tc-id-schema)
- [Browser- & Mobile-Konventionen](#browser--und-mobile-konventionen)
- **Sektionen** (ausgelagert, je eine Datei):
  - [SEKTION A — Anwender-Smoke](manual-test-matrix-a.md)
  - [SEKTION B — Anwender-Komplett (systematisch)](manual-test-matrix-b.md)
  - [SEKTION C — Auditor-DSGVO/Security](manual-test-matrix-c.md)
  - [SEKTION D — Entwickler-Probes (LOKAL/SSH)](manual-test-matrix-d.md)
- [Browser- und Mobile-Konventionen](#browser--und-mobile-konventionen)
- [Anhang B — Bekannte Risiken und Test-Lücken](#anhang-b--bekannte-risiken-und-test-lücken)
- [Anhang C — E2E-Coverage-Bilanz](#anhang-c--e2e-coverage-bilanz)
- [Anhang D — Test-Daten-Cheatsheet](#anhang-d--test-daten-cheatsheet)
- [Anhang E — Performance-Budgets](#anhang-e--performance-budgets)
- [Anhang F — Sprachen×Rollen×Einrichtungen Cross-Coverage-Grid](#anhang-f--sprachenrolleneinrichtungen-cross-coverage-grid)
- [Anhang G — Offline-Feld-Durchlauf iOS/Safari (WebKit)](#anhang-g--offline-feld-durchlauf-iossafari-webkit)

---

## Setup-Block

> **Einmalig pro Test-Tag, nicht pro Case.** Sobald du eingeloggt bist und deinen Browser/Mobile bereit hast, gehst du Cases einfach durch — ohne den Setup zu wiederholen. Cases verweisen in „Voraussetzung" nur noch auf **Daten-** oder **Workflow-Voraussetzungen** (z.B. „Person mit Pseudonym X existiert"), nicht auf den Infra-Setup.

### Test-Umgebung (Standard)

Tests laufen gegen eine erreichbare Test-/Demo-Instanz mit HTTPS, ClamAV, persistenter DB und den Standard-Seed-Logins. Die Seed-Logins und das Seed-Passwort sind öffentliche Demo-Defaults — siehe [`CONTRIBUTING.md`](../../CONTRIBUTING.md) (`make seed`).


### Test-Umgebung: lokal (nur für 🔧 LOKAL/SSH-Cases)

Die Cases in **Sektion D** (LOKAL/SSH) benötigen **direkten Server-Zugriff** (z.B. `manage.py shell`, `psql`, Backdate-SQL, `enforce_retention`-Cron). Die jeweils aktuelle Zahl steht im generierten Index (`test-matrix-index.md`). Cases sind im Header mit `🔧 LOKAL/SSH` markiert und werden:

- **lokal** auf Tobias' Maschine durchgeführt (Setup unten), oder
- per **SSH auf dev-Server** (`ssh <ssh-user>@dev.anlaufstelle.app`, dann `docker compose exec web python manage.py …`)

Lokales Setup nur falls 🔧 LOKAL/SSH-Cases anstehen:

| Schritt | Befehl |
|---------|--------|
| Repo aktuell | `git pull` |
| DB & Container | `sudo docker compose up -d` |
| Seed mit 2 Facilities | `.venv/bin/python src/manage.py seed --flush --scale medium` (small=1, **medium=2**, large=5 Einrichtungen; ein `FACILITIES`-Argument existiert nicht — Refs #973) |
| Dev-Server starten | `make run` (gunicorn, HTTPS, Port 8443) oder `make run-http` (Port 8000). Für den E2E-Server (Port 8844) gibt es kein Make-Target — manuelle gunicorn-Sequenz: [`docs/e2e-runbook.md`](../e2e-runbook.md) §6. |
| Migrationen | `make migrate` |

### Browser, Mobile, MFA, ClamAV (gilt für dev und lokal)

- **Browser:** Drei Privat-Fenster — Chromium (Default), Firefox, Safari/WebKit. Pro Fenster ein Login.
- **Mobile:** Chromium DevTools → Device-Mode → iPhone 15. Oder echtes Gerät über `https://dev.anlaufstelle.app`.
- **TOTP-App** (für MFA-Cases): KeePassXC, Aegis, Google Authenticator, 1Password.
- **EICAR-Datei** (für Virus-Scan-Cases): EICAR-Test-String als `eicar.com` lokal ablegen — `X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*`. ClamAV auf dev und lokal aktiv.

### Konvention für „Voraussetzung" pro Case

Die Setup-Schritte oben gelten **implizit** und werden **nicht** pro Case wiederholt. Das Feld „Voraussetzung" enthält nur:

- **Daten-Voraussetzungen** — z.B. „Person mit Pseudonym X existiert", „Backdate-Daten via Tobias eingespielt"
- **Vorhergehende Test-Cases** — z.B. „SMK-A-VORM-02 (anonymes Ereignis ohne Person vorhanden)"
- **Modus-Voraussetzungen** — z.B. „Sudo-Mode aktiv"

---

## Status-Legende

> **Wichtig:** Die `Status:`-Zeilen pro Case sind **Default-Werte** im Testkatalog und sollen im Repo immer `☐ Offen` bleiben. Echte Lauf-Status werden in einem separaten Run-Log unter `runs/` erfasst — Vorlage in `run-template.md`. Direkt in der Matrix Status zu setzen würde den Katalog für jeden Lauf neu schreiben und Audit-Drift erzeugen (#898).

Status-Symbole gelten gleichermassen im Run-Log:

| Symbol | Bedeutung |
|--------|-----------|
| ☐ | **Offen** — noch nicht getestet (Default im Katalog) |
| ✅ | **Pass** — Test bestanden |
| ❌ | **Fail** — Test fehlgeschlagen, Issue eröffnen und referenzieren (`❌ Fail #1234`) |
| `N/A` | **Not Applicable** — Case in dieser Umgebung nicht zutreffend (z.B. ClamAV-Test ohne Container) |
| 🚧 | **Blockiert** — Voraussetzung nicht erfüllbar, blockiert durch Issue X |
| ⏭ | **Skipped** — bewusst übersprungen (z.B. Mobile-Stichprobe in dieser Runde) |

**Konvention für Fail:** Im Run-Log wird der Eintrag zu `❌ Fail [#ISSUE](https://github.com/anlaufstelle/app/issues/ISSUE)`. Issue-Body enthält Reproduktion + erwartetes vs. tatsächliches Verhalten.

---

## TC-ID-Schema

| Sektion | Schema | Beispiel |
|---------|--------|----------|
| **A** — Anwender-Smoke | `SMK-A-<Tagesabschnitt>-<NN>` | `SMK-A-VORM-01`, `SMK-A-MITT-02`, `SMK-A-MOBI-01` |
| **B** — Entwickler-Komplett | `ENT-<BEREICH>-<NN>` | `ENT-AUTH-03`, `ENT-CLIENT-12`, `ENT-RET-07` |
| **C** — Auditor-DSGVO/Security | `AUD-DSGVO-Art<N>-<NN>` oder `AUD-SEC-<TOPIC>-<NN>` | `AUD-DSGVO-Art17-01`, `AUD-SEC-RLS-04` |

**Bereichscodes (Sektion B):**

| Code | Bereich | Code | Bereich |
|------|---------|------|---------|
| AUTH | Authentifizierung | RET | Aufbewahrungsrichtlinien |
| MFA | Multi-Faktor-Auth | SRCH | Suche |
| ACCT | Account & Profil | ZS | Zeitstrom |
| SUDO | Sudo-Mode | HOV | Übergabe (Handover) |
| PWA | Progressive Web App | STAT | Statistik & Reporting |
| CLIENT | Personen | AUDIT | Audit-Log |
| CASE | Fälle | DSGVO | DSGVO-Paket |
| EPI | Episoden | OFFL | Offline-Modus |
| GOAL | Goals & Milestones | SYS | Sys / Health / Monitoring |
| EVT | Events (Dokumentation) | A11Y | Accessibility (WCAG-Stichproben) |
| ATT | Anhänge | SETUP | Einrichtungs-/Konfigurationsassistent |
| WI | WorkItems | COMP | Betriebs-/Compliance-Dashboard |
| DEL | Lösch-Anträge | PRIV | Datenschutz-Review (Freitext) |
| I18N | Sprachen (DE/EN) | REPORT | Datenschutzfreundliche externe Berichte |
| CONFIG | Formular-Konfiguration (DocumentTypes/FieldTemplates/FieldGroups) | COND | Bedingte Feldlogik (Regel-Editor + Live-Auswertung) |
| CLIENT-DYN | Dynamische Zusatzfelder an der Person | CASE-DYN | Dynamische Zusatzfelder am Fall |
| IMPORT | CSV-Import-Wizard (Datenportabilität, M4) | RECT | Art.-16-Berichtigungsworkflow (Datenportabilität, M4) |

**Forward-looking Bereiche (Refs #908):** `SETUP`, `COMP`, `PRIV`, `REPORT` und `A11Y` sind aufgenommen, damit die jeweiligen Feature-Issues (#917 Einrichtungsassistent, #919 Compliance-Dashboard, #918 Freitext-Review, #921 Externe Berichte) ihre Cases unter einheitlichem Schema ablegen können, sobald das Feature implementiert ist. A11Y (Refs #912) bekommt einen eigenen Cases-Block.

---

## Browser- und Mobile-Konventionen

Jeder Case in der Tabellen-Kopfzeile hat zwei Spalten zum Browser-/Mobile-Scope:

**Browser:**
- `C` — nur Chromium reicht
- `C/F` — Chromium + Firefox
- `C/S` — Chromium + Safari/WebKit
- `C/F/S` — alle drei (Pflicht für sicherheitskritische und HTMX-intensive Cases)

**Mobile:**
- `✓` — Pflicht (Streetwork-/Mobile-Workflow)
- `⚪` — Stichprobe (einmal pro Release prüfen)
- `—` — nicht relevant (z.B. DSGVO-Paket-Download, Audit-Log)

**E2E-Spalte:** Verweis auf abdeckende Pytest-Datei in `src/tests/` oder `src/tests/e2e/`. `—` heißt manuell-only.

---

## Sektionen

> Der Testkatalog ist nach Sektionen A–D in vier Dateien ausgelagert (Refs #1071 Block B) — die Datei war mit ~9.000 Zeilen zu groß für Review und Navigation. Front-matter (Setup, Legende, TC-ID-Schema, Konventionen) und die Anhänge bleiben hier im Hub. Die kompakte Gesamtübersicht steht in `test-matrix-index.md`.

| Sektion | Zielgruppe | Datei |
|---------|-----------|-------|
| **A** — Anwender-Smoke | Sozialarbeiter:innen (Pilotbetrieb) | [`manual-test-matrix-a.md`](manual-test-matrix-a.md) |
| **B** — Anwender-Komplett | systematisch, alle Bereiche | [`manual-test-matrix-b.md`](manual-test-matrix-b.md) |
| **C** — Auditor-DSGVO/Security | Compliance, RLS-Penetration | [`manual-test-matrix-c.md`](manual-test-matrix-c.md) |
| **D** — Entwickler-Probes | LOKAL/SSH-Ops | [`manual-test-matrix-d.md`](manual-test-matrix-d.md) |

---

## Anhang B — Bekannte Risiken und Test-Lücken

Liste der manuell besonders relevanten Edge-Cases — **nicht** durch automatisierte Tests gedeckt, gehören in jeden manuellen Durchlauf:

### B.1 — HTMX-Toast-Verhalten bei 4xx/5xx

Out-of-Band-Swap-Verhalten beim Fehler-Toast prüfen:
- Validierungsfehler (HTMX 422) → Toast erscheint, Form-Felder behalten Werte.
- Server-Fehler (HTMX 5xx) → freundlicher Fehler-Toast statt Stack-Trace.
- Network-Fehler (HTMX `htmx:responseError`) → Retry-Hinweis.

### B.2 — Pagination-Edge-Cases

URL-Parameter-Manipulation:
- `?page=999999` → leere Seite mit Hinweis statt 500.
- `?page=-1` → Fallback auf Seite 1 oder 400 (Bad Request).
- `?page=abc` → Fallback auf Seite 1 oder 400.
- `?page=` (leer) → wie Default.

### B.3 — Modal/Dialog-Stack

- Delete-Confirm-Modal innerhalb Detail-Modal: Z-Index korrekt, Schließen-Button funktioniert nur den obersten Stack-Eintrag.
- ESC schließt obersten Stack-Eintrag, nicht den ganzen Stack.

### B.4 — Concurrency: zwei Sessions, gleicher Datensatz

- Profile A + Profile B (zwei verschiedene Browser/Profile) öffnen denselben Personen-Datensatz im Edit-Modus.
- Beide bearbeiten ein anderes Feld, Profile A speichert zuerst.
- Profile B speichert: erwartet **Konflikt-Toast** mit „Datensatz wurde inzwischen geändert" und Optionen „Reload" / „Überschreiben".

### B.5 — Sortier-Stabilität bei identischen Timestamps

Wenn zwei Events exakt denselben `created_at`-Timestamp haben (z.B. via Bulk-Import):
- Sekundärsortierung nach UUID oder ID (deterministisch).
- Pagination liefert keine Duplikate / fehlt keine Einträge.

### B.6 — Sprache mitten im Workflow

- Im Edit-Modus die Sprache von DE auf EN umschalten (über `/account/`-Settings).
- Form-Felder werden neu gerendert mit englischen Labels.
- Eingegebene Werte bleiben erhalten (kein Datenverlust).

### B.7 — Browser-Back nach POST

Nach erfolgreichem Submit eines Forms: Browser-Back-Button drücken.
- Erwartung: keine Re-Submission, freundlicher Hinweis oder Redirect.
- Bei `POST → 303 → GET` (Post-Redirect-Get-Pattern): Back führt zur GET-Seite, nicht zum Form mit Re-Submit-Warnung.

### B.8 — Session-Timeout während Upload

- Upload eines 20-MB-Anhangs starten.
- Vor Upload-Ende Session-Cookie manuell löschen (DevTools → Application).
- Erwartung: Upload bricht mit klarer Fehlermeldung („Session abgelaufen, bitte neu einloggen") ab — **nicht** stiller Fail.

### B.9 — HX-Boost und Browser-History

- Mehrere `hx-boost`-Navigationen durchführen (z.B. Person → Case → Episode).
- Browser-Back-Button durchklicken: jede vorherige Seite ist im History-Stack.
- Forward funktioniert ebenfalls.

### B.10 — Print-CSS für Übergabe und Statistik

- `Strg+P` auf Übergabe-Seite (`/uebergabe/`): druckfertige Layout (kein Header, keine Buttons).
- `Strg+P` auf Statistik-Dashboard: Charts werden gedruckt (Canvas → PDF).

### B.11 — File-Upload-Größenlimit-Grenzfall

- Datei mit **genau** dem Limit (z.B. 25 MB) hochladen → Erfolg.
- Datei mit Limit + 1 Byte → klare Fehlermeldung, kein Generic-500.

### B.12 — Retention-Cron-Race mit manueller Approve

- `enforce_retention` läuft in Cron, gleichzeitig manueller Bulk-Approve in UI.
- Erwartung: keine Doppel-Anonymisierung; Lock-Mechanismus oder DB-Constraint verhindert Race.

### B.13 — Tooltip / aria-describedby

- Hover über Buttons mit Tooltip → Tooltip erscheint nach < 500ms, verschwindet beim Mouse-Out.
- Screen-Reader-User: Tooltip-Text via `aria-describedby` zugänglich (Stichprobe — kein voller WCAG-Audit).

### B.14 — Zeitzonen-Edge-Case

- Event in UTC-Mitternacht (00:00) anlegen.
- Anzeige im UI: korrekt in Europe/Berlin (z.B. `02:00` Sommerzeit, `01:00` Winterzeit).

---

## Anhang C — E2E-Coverage-Bilanz

> **Aktuelle Coverage-Bilanz:** Die Per-Bereich-Tabelle unten wird per `python scripts/build_test_matrix_index.py` aus dieser Matrix generiert (Refs #909). Sektionsweite Zahlen plus Listen für Manuell-only, LOKAL/SSH und Security/DSGVO-ohne-E2E stehen im `test-matrix-index.md`.

Methodik:

- **„Doppelt abgedeckt":** Pro Case die `E2E`-Spalte prüfen — falls nicht `—`, gilt der Case als doppelt abgedeckt.
- **Manuell-only:** Cases mit `—` in der `E2E`-Spalte.
- **Datenbasis für Folge-Tickets:** Manuell-only-Cases mit hoher Frequenz (jeder Release-Lauf) sind Kandidaten für Automatisierung. Tickets im Issue-Tracker mit Label `automate-manual-test` anlegen.

<!-- ANHANG-C:START -->
**Per-Bereich-Statistik (auto-generiert):**

| Sektion | Bereich | Cases | mit E2E | Manuell-only | E2E-Quote |
|---------|---------|------:|--------:|-------------:|----------:|
| A | Tagesablauf | 12 | 11 | 1 | 92 % |
| B | Accessibility | 12 | 1 | 11 | 8 % |
| B | Acct | 5 | 4 | 1 | 80 % |
| B | Attachments | 16 | 13 | 3 | 81 % |
| B | Audit | 5 | 5 | 0 | 100 % |
| B | Aufbewahrung | 2 | 2 | 0 | 100 % |
| B | Auth | 11 | 5 | 6 | 45 % |
| B | Berichtigungen | 8 | 5 | 3 | 62 % |
| B | Compliance | 4 | 0 | 4 | 0 % |
| B | DSGVO | 10 | 10 | 0 | 100 % |
| B | Datenimport | 9 | 6 | 3 | 67 % |
| B | DeletionRequests | 6 | 6 | 0 | 100 % |
| B | Episoden | 4 | 4 | 0 | 100 % |
| B | Episoden / Permissions | 1 | 1 | 0 | 100 % |
| B | Events | 12 | 11 | 1 | 92 % |
| B | Fälle | 9 | 9 | 0 | 100 % |
| B | Fälle / API | 1 | 1 | 0 | 100 % |
| B | Fälle / Events | 2 | 2 | 0 | 100 % |
| B | Fälle / Zusatzfelder | 1 | 0 | 1 | 0 % |
| B | I18N | 5 | 1 | 4 | 20 % |
| B | Klient:innen | 13 | 13 | 0 | 100 % |
| B | Klient:innen / DeletionRequests | 1 | 1 | 0 | 100 % |
| B | Klient:innen / RLS | 1 | 0 | 1 | 0 % |
| B | Klient:innen / Retention | 1 | 1 | 0 | 100 % |
| B | Klient:innen / Zusatzfelder | 3 | 0 | 3 | 0 % |
| B | Konfiguration | 20 | 0 | 20 | 0 % |
| B | Konfiguration / Audit | 1 | 0 | 1 | 0 % |
| B | Konfiguration / Bedingte Feldlogik | 3 | 3 | 0 | 100 % |
| B | Konfiguration / Events | 3 | 0 | 3 | 0 % |
| B | Konfiguration / Fälle | 1 | 0 | 1 | 0 % |
| B | Konfiguration / Klient:innen | 1 | 0 | 1 | 0 % |
| B | Konfiguration / RLS | 1 | 0 | 1 | 0 % |
| B | Konfiguration / Retention | 1 | 1 | 0 | 100 % |
| B | MFA | 9 | 9 | 0 | 100 % |
| B | Meilensteine | 3 | 3 | 0 | 100 % |
| B | Offline | 18 | 13 | 5 | 72 % |
| B | Pwa | 6 | 4 | 2 | 67 % |
| B | Statistik | 15 | 14 | 1 | 93 % |
| B | Suche | 6 | 6 | 0 | 100 % |
| B | Sudo | 4 | 0 | 4 | 0 % |
| B | Sys | 6 | 1 | 5 | 17 % |
| B | Wirkungsziele | 3 | 3 | 0 | 100 % |
| B | Wirkungsziele / Meilensteine | 1 | 1 | 0 | 100 % |
| B | WorkItems | 10 | 10 | 0 | 100 % |
| B | Zeitstrom | 5 | 5 | 0 | 100 % |
| B | Übergabe | 5 | 5 | 0 | 100 % |
| C | Compliance | 16 | 8 | 8 | 50 % |
| C | Security | 22 | 1 | 21 | 5 % |
| D | Audit | 2 | 1 | 1 | 50 % |
| D | Aufbewahrung | 10 | 8 | 2 | 80 % |
| D | Compliance | 7 | 2 | 5 | 29 % |
| D | DeletionRequests | 1 | 0 | 1 | 0 % |
| D | Fälle / Kaskade | 2 | 0 | 2 | 0 % |
| D | Klient:innen / Kaskade | 1 | 0 | 1 | 0 % |
| D | Konfigurationspakete | 3 | 0 | 3 | 0 % |
| D | Ops | 10 | 1 | 9 | 10 % |
| D | Security | 8 | 0 | 8 | 0 % |
| D | Statistik | 2 | 2 | 0 | 100 % |

**Sektion-Totals:**

| Sektion | Cases | mit E2E | Manuell-only | E2E-Quote |
|---------|------:|--------:|-------------:|----------:|
| A | 12 | 11 | 1 | 92 % |
| B | 264 | 179 | 85 | 68 % |
| C | 38 | 9 | 29 | 24 % |
| D | 46 | 14 | 32 | 30 % |
| **Gesamt** | **360** | **213** | **147** | **59 %** |

> Auto-generiert per `python scripts/build_test_matrix_index.py` (#909).
<!-- ANHANG-C:END -->

---

## Anhang D — Test-Daten-Cheatsheet

Aus `src/core/management/commands/seed.py` extrahiert: was wird wie geseedet, in welcher Skalierung.

### D.1 — Standard-Logins

Die Konten und Rollen stehen in der Tabelle [Standard-Logins](#standard-logins-pwd-anlaufstelle2026-für-alle). Für zwei Einrichtungen `.venv/bin/python src/manage.py seed --flush --scale medium` verwenden; die zweite Einrichtung heißt „Zweigstelle Nord“, ihr Admin-Konto `admin_1`.

### D.2 — Seed-Skalierung

Die Zielwerte gelten für einen frischen Seed nach `--flush`. Angaben jeweils **je Einrichtung / insgesamt**; Pakete und Zusatzdaten unterscheiden sich je Einrichtung.

| Skalierung | Einrichtungen | Personen | Events | Cases | WorkItems | Aufruf |
|------------|---------------|--------------|--------|-------|-----------|--------|
| Small (Default) | 1 | 7 / 7 | 25 / 25 | 3 / 3 | 5 / 5 | `.venv/bin/python src/manage.py seed --flush --scale small` |
| Medium | 2 | 40 / 80 | 750 / 1.500 | 12 / 24 | 25 / 50 | `.venv/bin/python src/manage.py seed --flush --scale medium` |
| Large (Last-Smoke) | 5 | 500 / 2.500 | 10.000 / 50.000 | 50 / 250 | 100 / 500 | `.venv/bin/python src/manage.py seed --flush --scale large` |

`make seed` ruft nur `.venv/bin/python src/manage.py seed` auf (Default `small`, ohne `--flush`); Make-Variablen für Skalierung oder Einrichtungszahl werden nicht weitergereicht. `solo` und `demo` sind weitere gültige Profile; `empty` ist kein Profil.

Quelle: `src/core/management/commands/seed.py` und Helper-Funktionen `seed_clients_small/bulk()`, `seed_events_small/bulk()`, etc.

### D.3 — Stamm-Daten (Document-Types, Activities, FieldTemplates)

Aus `seed.py` werden geseedet:
- **Document-Types (Hauptstelle, Paket `default`):** Kontakt, Krisengespräch, Medizinische Versorgung, Spritzentausch, Begleitung, Beratungsgespräch, Vermittlung, Notiz, Hausverbot. Weitere Einrichtungen nutzen andere Pakete aus `FACILITY_PACKAGES`.
- **Activities:** typisierte Aktivitätseinträge zu Personen, Ereignissen, Fällen und Aufgaben; kein Katalog von Gesprächsarten.
- **FieldTemplates:** Felder und Typ-Zuordnungen aus dem jeweiligen YAML-Paket; im Standardpaket etwa Dauer, Notiz, Leistungen, Altersgruppe und Anhang.
- **Settings:** Default-Aufbewahrungsfristen, MFA-Pflicht, k-Anonymität, Retention-Auto-Approve.
- **TimeFilters (Hauptstelle):** Frühdienst, Spätdienst, Nachtdienst; die anderen Einrichtungen übernehmen die Komfortobjekte ihres Pakets.

### D.4 — Spezial-Daten

- **Deletion-Requests:** `small` erzeugt keine; `medium` hat einen Zielwert von 5, `large` von 15 je Einrichtung, mit gemischten Statuswerten.
- **Retention-Proposals:** Zielwerte je Einrichtung: `small` 4, `medium` 6, `large` 12; nicht alle sind offen oder bereits fällig.
- **AuditLog:** Der Seed garantiert keine bestimmte Anzahl; größere Audit-Datenmengen für Pagination-Proben gesondert vorbereiten.

### D.5 — Reset-Workflow für sauberen Test-Lauf

Nur eine eigens vorbereitete lokale Handtest-Datenbank zurücksetzen, nachdem alle sie nutzenden Prozesse beendet sind. Die Datenbank `anlaufstelle_handtest` muss bereits existieren; Verbindung und DB-Rolle vorher prüfen. `flush` entfernt die Daten, behält aber das Schema. Niemals gegen Produktion oder die gemeinsam genutzte E2E-Datenbank ausführen.

Aus dem Repository mit aktivierter virtueller Umgebung:

```bash
POSTGRES_DB=anlaufstelle_handtest python src/manage.py migrate
POSTGRES_DB=anlaufstelle_handtest python src/manage.py flush --noinput
POSTGRES_DB=anlaufstelle_handtest python src/manage.py seed
```

---

## Anhang E — Performance-Budgets

> Refs #913 (§4.8): Richtwerte, ab wann eine Seite als „zu langsam" gilt — auch wenn der Case funktional `✅ Pass` ist. Verletzungen werden im Run-Log unter „Performance-Beobachtungen" als `❌ Performance-Fail` mit Issue-Link festgehalten. Vorlage: `run-template.md` Abschnitt 4.

### Budget-Tabelle

| Bereich | Budget | Datenstand | Gemessen am |
|---|---|---|---|
| Dashboard/Home | TTI < 2 s | Medium-Seed (`.venv/bin/python src/manage.py seed --flush --scale medium`) | Chrome DevTools-Lighthouse oder DjDT Footer-Zeit |
| Client-Detail | < 1 s Serverzeit bei ~100 Events | dev-Daten, eine typische Person | DjDT, Tab „Time" |
| Event-Edit mit 10 Attachments | keine N+1-Queries; < 500 ms Serverzeit | manuell vorbereitetes Event | DjDT, Tab „SQL" + „Time" |
| Suche (`?q=...`) | < 500 ms bei 2.500 Personen insgesamt | Large-Seed | DjDT, Tab „Time" |
| AuditLog-Liste | < 500 ms pro Seite bei ~10.000 Einträgen | Large-Seed plus zusätzlicher Audit-Bulk | DjDT, Tab „SQL" (Pagination-Query darf einen Index nutzen) |
| Retention-Dashboard | < 2 s bei ~1.000 Proposals | gesonderter Setup-Script | DjDT „Time" |
| Statistik-Dashboard | < 3 s bei Large-Seed (50.000 Events insgesamt) | Large-Seed | DjDT „Time" + Chart-Render im Browser |

### Wo messen?

- **Lokal (dev-Server, Debug Toolbar aktiv):** primärer Messplatz. Footer-Zeit zeigt View-Time + Template-Render.
- **Stage (`stage.anlaufstelle.app`):** für Realitätscheck nach Schema-Änderungen; Debug Toolbar dort nicht verfügbar, deshalb Chrome DevTools Performance Tab + `/health/`-Roundtrip als Smoke.
- **Nicht in CI** — Performance ist umgebungsabhängig (Disk, CPU). CI prüft Query-Counts per pytest-Markern (`@pytest.mark.django_db` + `CaptureQueriesContext`), nicht Wallclock-Zeiten.

### Query-Count-Tests für bekannte N+1-Risiken

Bereits gegen Regression abgesichert:

- `TestBuildAttachmentContextQueryCount` ([src/tests/test_attachment_versioning_stage_b.py](https://github.com/anlaufstelle/app/blob/main/src/tests/test_attachment_versioning_stage_b.py)) — 1 SELECT statt N (Refs #894).
- `TestEventDetailContextQueryCount` (gleiche Datei) — konstante Query-Anzahl bei wachsender Versionskette (Refs #662).
- `TestApplyAttachmentChangesQueryCount` (gleiche Datei) — Bulk-Remove ohne N+1 (Refs #782).

Neue N+1-Verdachtsfälle bekommen einen analog gebauten Query-Count-Test in der jeweils zuständigen `test_*.py`-Datei. Pattern: `CaptureQueriesContext` um den Code-Pfad, Anzahl Queries gegen erwartetes Limit asserten, Code-Pointer im Docstring auf das Issue.

### Run-Log-Status „Performance-Fail"

Im Run-Template (`run-template.md` Abschnitt 4) ist eine eigene Performance-Tabelle. Wird ein Budget verletzt, vermerken:

- TC-ID + Budget-Eintrag
- Gemessener Wert
- Datenstand + Hardware (z.B. „Stage-Container, Hetzner CPX21")
- Issue-Link (neu eröffnen mit Label `perf`)

Im Profil **Major-Release** (`release-test-profiles.md` §6) ist die volle Budget-Tabelle Pflicht. In den anderen Profilen Stichprobe (z.B. Dashboard + Client-Detail).

---

## Anhang F — Sprachen×Rollen×Einrichtungen Cross-Coverage-Grid

> **Zweck (Refs #973):** Master-Referenz für die drei Querschnitts-Dimensionen. Statt jede Route × Rolle × Sprache × Facility als eigenen TC-ID zu duplizieren, fasst dieses Grid die **Anwendbarkeit** je Routengruppe zusammen — der exhaustive Lauf prüft pro Gruppe den Rollen-Vektor (erlaubt/Deny), beide Sprachen (DE/EN) und (bei datenführenden Routen) die Facility-2-Isolation. Verifiziert per HTTP-Authz-Probe (9 Seed-Accounts × alle Routen) + Playwright-Stichproben.

### Rollen-Applicability-Legende (Mixin → Routengruppe)

Quelle: [`src/core/views/mixins.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/mixins.py) + [`src/core/urls.py`](https://github.com/anlaufstelle/app/blob/main/src/core/urls.py).

| Mixin | Erlaubte Rollen | Routengruppe (Beispiele) |
|-------|-----------------|--------------------------|
| `SuperAdminRequiredMixin` | nur `super_admin` | `/system/*` (audit, organization, lockouts, maintenance, retention, vvt, legal-holds, compliance) |
| `FacilityAdminRequiredMixin` | nur `facility_admin` | `/audit/`, `/audit/<pk>/` |
| `LeadOrAdminRequiredMixin` | `facility_admin`, `lead` | Retention, Deletion-Requests, Statistik, Case-close/reopen, Client-Trash/Restore/Delete |
| `StaffRequiredMixin` | `facility_admin`, `lead`, `staff` | Client-create/update, Case-CRUD, Event-create/update, Episoden/Goals |
| `AssistantOrAboveRequiredMixin` | + `assistant` | Client-list/detail, Event-detail, Zeitstrom/Handover, Suche, WorkItem-inbox/detail |

> `super_admin` (facility=None) ist **bewusst aus dem Facility-App-Bereich ausgesperrt** (403 auf Zeitstrom/Clients/etc.) und lebt in `/system/*`. `/account/` ist für alle angemeldeten Rollen einschließlich `super_admin` zugänglich; ohne Einrichtung bleiben die einrichtungsgebundenen Widgets leer.

### Cross-Coverage-Grid

Legende: ✅ erlaubt (200) · ⛔ Deny erwartet (403/redirect) · 🔒 Sudo-Redirect · 404 = facility-fremdes Objekt nicht sichtbar (RLS-Isolation OK). sA=super_admin, FA=facility_admin, Ld=lead, St=staff, As=assistant.

| Routengruppe | sA | FA | Ld | St | As | DE/EN | Facility-2-Isolation |
|--------------|----|----|----|----|----|-------|----------------------|
| Auth / Login / Pwd-Reset / MFA / Sudo | ✅ | ✅ | ✅ | ✅ | ✅ | DE+EN | n/a |
| Account `/account/` | ✅ | ✅ | ✅ | ✅ | ✅ | DE+EN | eigenes Profil; Widgets je Einrichtung |
| Zeitstrom / Dashboard / Handover | ⛔ | ✅ | ✅ | ✅ | ✅ | DE+EN | scoped |
| Personen-Liste/-Detail / Suche | ⛔ | ✅ | ✅ | ✅ | ✅ | DE+EN | fac2→404 ✅ |
| Personen create/update | ⛔ | ✅ | ✅ | ✅ | ⛔ | DE+EN | fac2→404 ✅ |
| Fälle / Episoden / Goals (CRUD) | ⛔ | ✅ | ✅ | ✅ | ⛔ | DE+EN | fac2→404 ✅ |
| Fall close/reopen, Client-Trash | ⛔ | ✅ | ✅ | ⛔ | ⛔ | DE+EN | scoped |
| Events create/update | ⛔ | ✅ | ✅ | ✅ | ⛔ | DE+EN | fac2→404 ✅ |
| Attachments / Dateien | ⛔ | ✅ | ✅ | ✅ | ✅ | DE+EN | fac2→404 ✅ |
| WorkItems inbox/detail | ⛔ | ✅ | ✅ | ✅ | ✅ | DE+EN | fac2→404 ✅ |
| WorkItems create | ⛔ | ✅ | ✅ | ✅ | ⛔ | DE+EN | scoped |
| Retention / Deletion-Requests / Statistik | ⛔ | ✅ | ✅ | ⛔ | ⛔ | DE+EN | scoped |
| Client-Export / DSGVO-Paket | ⛔ | 🔒 | 🔒 | ⛔ | ⛔ | DE+EN | scoped |
| Audit-Log `/audit/` | ⛔ | ✅ | ⛔ | ⛔ | ⛔ | DE+EN | je facility-eigene Liste ✅ |
| System `/system/*` | ✅ | ⛔ | ⛔ | ⛔ | ⛔ | DE+EN | facility-übergreifend (geloggt) |

**Befund-Zusammenfassung der Live-Probe:** Rollen-Gating entspricht durchweg den Mixins; Cross-Facility-Isolation greift (Facility-2-User erhalten 404 auf alle Facility-1-Objekte); sensible Exporte sind sudo-gated. Vollständige Zellen-Matrix im Run-Log `runs/2026-05-25-major-release-v0.12.0.md` §3.X.

---

## Anhang G — Offline-Feld-Durchlauf iOS/Safari (WebKit)

> **Zweck (Refs #1418):** Die automatisierte Offline-E2E-Suite läuft ausschließlich unter **Chromium** — die realistischste Feld-Plattform der PWA ist aber **iOS/Safari/WebKit**, und genau dort liegen die kritischen Eigenheiten (IndexedDB-ITP-Eviction, aggressiver Service-Worker-Lebenszyklus, `navigator.locks`-Verfügbarkeit, `storage.persist()`-Grant). Dieser Durchlauf schließt die Lücke mit (a) dem seltenen **WebKit-Smoke** `make test-e2e-webkit-smoke` und (b) einem **manuellen iOS-Pass**. Schritt-für-Schritt-Grundlage: das Living-Doc #1319.

### G.0 — Automatisierter WebKit-Smoke (vor dem manuellen Pass)

Einmalige Einrichtung (Root/`apt` für die WebKit-Systembibliotheken):

```bash
playwright install webkit --with-deps   # einmalig
make tailwind-build                      # styles.css muss gebaut sein, sonst SW-cache-404
make test-e2e-webkit-smoke               # SERIELL (RAM-Limit), Marker: "e2e and offline_smoke"
```

Deckt die Offline-Kernsuite (`test_pwa_offline`, `test_sync_orchestrator`, `test_offline_deadletter_ui`) unter WebKit ab. Grün ⇒ Service-Worker-Lebenszyklus, Web-Locks-Sync-Orchestrator und Dead-Letter-Kern funktionieren auf der WebKit-Engine. **Nicht** Teil von `make ci` (bewusst seltener Lauf, `make ci` lässt `e2e` aus).

### G.1 — Voraussetzungen (iOS)

- **Echtes iPhone/iPad** mit Safari (Chrome/Firefox auf iOS nutzen ebenfalls die WebKit-Engine).
- Zugriff über **HTTPS** — zwingend `https://dev.anlaufstelle.app` (kein LAN-`http://…`, sonst kein WebCrypto-Schlüssel).
- **Frisch anmelden, Passwort wirklich eintippen** (AES-GCM-Key wird per PBKDF2 aus dem Passwort abgeleitet; eine nur per Cookie wiederhergestellte Sitzung hat keinen Schlüssel).
- **Rolle ohne 2FA-Pflicht** (`miriam`/staff, `emma`/lead); `super_admin`/`facility_admin` laufen zweistufig über TOTP — auf dem Pfad entsteht der Offline-Schlüssel nicht zuverlässig.

### G.2 — Kern-Durchlauf (entlang #1319 § B — Telefon)

- [ ] **PWA installieren:** Safari → Teilen → **Zum Home-Bildschirm**.
- [ ] **Person(en) offline mitnehmen:** Personenliste → **„Offline mitnehmen"** → Badge **„Lokal verfügbar"**.
- [ ] **Detailseite einmal online öffnen** (die Personenliste ist offline **nicht** verfügbar — zuverlässiger Offline-Einstieg ist `/clients/<pk>/`).
- [ ] **Flugmodus an.**
- [ ] **Personenseite neu laden / erneut öffnen** → Offline-Ansicht aus dem verschlüsselten Cache (gelbes Offline-Banner).
- [ ] **Ereignis erfassen/bearbeiten** → „… wird beim nächsten Online-Kontakt synchronisiert."; Banner zählt „1 nicht synchronisiert".
- [ ] **WorkItem-Status ändern** (Übernehmen/Erledigt/Als nicht relevant schließen) offline → Klick landet in der Queue (Refs #1419).
- [ ] **Flugmodus aus** → Queue synchronisiert **automatisch**; der Zähler geht auf 0.
- [ ] **Konfliktfall:** dieselbe Aufgabe/dasselbe Ereignis zwischenzeitlich auf einem zweiten Gerät ändern → nach Reconnect erscheint der Konflikt in **`/offline/conflicts/`** und ist per **„Erneut anwenden"** gegen den gezeigten Server-Stand auflösbar (kein stilles Überschreiben).

### G.3 — WebKit-spezifische Beobachtungspunkte (das eigentliche #1418-Ziel)

| Risiko | Was prüfen | Erwartung |
|---|---|---|
| **IndexedDB-ITP-Eviction** | Nach „Offline mitnehmen": wurde `storage.persist()` gewährt? (Der Offline-Arbeitsplatz zeigt den Persist-/Speicher-Status, Refs #1412.) Ohne Grant kann Safari (ITP) die IndexedDB nach ~7 Tagen ohne Interaktion löschen. | Persist-Grant **oder** sichtbarer „Browser kann Offline-Daten verwerfen"-Hinweis (#1356); kein stiller Verlust. |
| **SW-Lebenszyklus (aggressiv)** | App aus dem App-Switcher wischen, einige Minuten warten, **Kaltstart** über die Home-Kachel **und** über die Detail-URL. | Detail-URL rendert offline aus dem Cache; der Kachel-Kaltstart landet ggf. auf der generischen „Sie sind offline"-Seite (dokumentiert — deshalb Detailseite als Einstieg). Kein Absturz, kein Titel-Hijack (#1416). |
| **`navigator.locks` (Web-Locks)** | Der Sync-Orchestrator (#1383) baut auf Web-Locks (Safari ≥ 15.4). Zwei Tabs/Fenster derselben App offline erfassen, dann online. | Genau **ein** koordinierter Sync-Lauf, keine Doppel-Anlagen. Fehlt die API (ältere iOS): Verhalten notieren (Degradation, kein Crash). |
| **`storage.persist()`-Grant** | Erst-„Mitnehmen" fragt einmalig `persist()` an (kein Re-Prompt); nach PWA-Installation darf erneut gefragt werden. | Ergebnis wird gecacht; Verweigerung ergänzt einen dezenten Hinweis. |
| **Datei-Upload offline** | Im Offline-Viewer eine Datei anhängen. | Klare Meldung „Datei-Upload erfordert Internetverbindung" (Uploads bleiben bewusst online-only). |

### G.4 — Ergebnis festhalten

Wie beim Rest der Matrix: der Lauf-Status wird **nicht** hier, sondern im Run-Log unter `runs/` erfasst (Vorlage `run-template.md`). Ein Fehlschlag wird `❌ Fail [#ISSUE]` mit Reproduktion + erwartetem vs. tatsächlichem Verhalten. Einordnung im Profil **Mobile-PWA-RC** (`release-test-profiles.md`).

---

**Letzte Aktualisierung:** 2026-07-08 · Pflege durch: Tobias Nix · Issues: #864, #973, #1418
