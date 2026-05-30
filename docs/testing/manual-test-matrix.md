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
> **Welche TC-IDs gehören in welchen Testlauf?** Siehe [`release-test-profiles.md`](release-test-profiles.md) (PR-Smoke, RC-Smoke, Security-RC, Mobile-PWA-RC, Ops-RC, Major-Release). Lauf-Ergebnisse werden in [`runs/`](runs/) abgelegt, nicht hier.

---

## Inhaltsverzeichnis

- [Setup-Block](#setup-block)
- [Status-Legende](#status-legende)
- [TC-ID-Schema](#tc-id-schema)
- [Browser- & Mobile-Konventionen](#browser--und-mobile-konventionen)
- [SEKTION A — Anwender-Smoke](#sektion-a--anwender-smoke)
 - [Vormittag (VORM)](#vormittag-vorm)
 - [Mittag (MITT)](#mittag-mitt)
 - [Nachmittag (NACH)](#nachmittag-nach)
 - [Abend (ABEND)](#abend-abend)
 - [Krise (CRIS)](#krise-cris)
 - [Streetwork-Offline (OFFL)](#streetwork-offline-offl)
 - [Mobile-Smoke (MOBI)](#mobile-smoke-mobi)
- [SEKTION B — Anwender-Komplett (systematisch)](#sektion-b--anwender-komplett-systematisch)
 - [AUTH — Authentifizierung](#auth--authentifizierung)
 - [MFA — Multi-Faktor-Authentifizierung](#mfa--multi-faktor-authentifizierung)
 - [ACCT — Account & Profil](#acct--account-und-profil)
 - [SUDO — Sudo-Mode](#sudo--sudo-mode)
 - [PWA — Progressive Web App](#pwa--progressive-web-app)
 - [CLIENT — Klient:innen-Management](#client--klientinnen-management)
 - [CASE — Fallmanagement](#case--fallmanagement)
 - [EPI — Episoden](#epi--episoden)
 - [GOAL — Goals & Milestones](#goal--goals-und-milestones)
 - [EVT — Events / Dokumentation](#evt--events-und-dokumentation)
 - [ATT — Anhänge / Datei-Vault](#att--anhaenge-und-datei-vault)
 - [WI — WorkItems / Aufgaben](#wi--workitems-und-aufgaben)
 - [DEL — Lösch-Anträge](#del--loesch-antraege)
 - [RET — Aufbewahrungsrichtlinien](#ret--aufbewahrungsrichtlinien)
 - [SRCH — Suche](#srch--suche)
 - [ZS — Zeitstrom](#zs--zeitstrom)
 - [HOV — Übergabe](#hov--uebergabe)
 - [STAT — Statistik & Reporting](#stat--statistik-und-reporting)
 - [AUDIT — Audit-Log](#audit--audit-log)
 - [DSGVO — DSGVO-Paket](#dsgvo--dsgvo-paket)
 - [OFFL — Offline-Modus](#offl--offline-modus)
 - [SYS — Sys / Health / Monitoring](#sys--sys--health--monitoring)
- [SEKTION C — Auditor-DSGVO/Security](#sektion-c--auditor-dsgvosecurity)
 - [DSGVO Art. 5 — Grundsätze](#dsgvo-art-5--grundsaetze)
 - [DSGVO Art. 7 — Einwilligung](#dsgvo-art-7--einwilligung)
 - [DSGVO Art. 15 — Auskunftsrecht](#dsgvo-art-15--auskunftsrecht)
 - [DSGVO Art. 16 — Berichtigung](#dsgvo-art-16--berichtigung)
 - [DSGVO Art. 17 — Löschung](#dsgvo-art-17--loeschung)
 - [DSGVO Art. 18 — Einschränkung](#dsgvo-art-18--einschraenkung)
 - [DSGVO Art. 20 — Datenübertragbarkeit](#dsgvo-art-20--datenuebertragbarkeit)
 - [DSGVO Art. 25 — Privacy by Design](#dsgvo-art-25--privacy-by-design)
 - [DSGVO Art. 30 — Verarbeitungsverzeichnis](#dsgvo-art-30--verarbeitungsverzeichnis)
 - [DSGVO Art. 32 — Sicherheit der Verarbeitung](#dsgvo-art-32--sicherheit-der-verarbeitung)
 - [DSGVO Art. 33-34 — Meldepflichten bei Datenpannen](#dsgvo-art-33-34--meldepflichten-bei-datenpannen)
 - [DSGVO Art. 35 — DSFA](#dsgvo-art-35--dsfa)
 - [Security: RLS-Penetration](#security-rls-penetration)
 - [Security: MFA-Härtung](#security-mfa-haertung)
 - [Security: Audit-Log-Integrität](#security-audit-log-integritaet)
 - [Security: Verschlüsselung & Key-Rotation](#security-verschluesselung-und-key-rotation)
 - [Security: HTTP-Header](#security-http-header)
- [SEKTION D — Entwickler-Probes (LOKAL/SSH)](#sektion-d--entwickler-probes-lokalssh)
- [Anhang A — Browser/Mobile-Matrix](#anhang-a--browsermobile-matrix)
- [Anhang B — Bekannte Risiken & Test-Lücken](#anhang-b--bekannte-risiken-und-test-luecken)
- [Anhang C — E2E-Coverage-Bilanz](#anhang-c--e2e-coverage-bilanz)
- [Anhang D — Test-Daten-Cheatsheet](#anhang-d--test-daten-cheatsheet)

---

## Setup-Block

> **Einmalig pro Test-Tag, nicht pro Case.** Sobald du eingeloggt bist und deinen Browser/Mobile bereit hast, gehst du Cases einfach durch — ohne den Setup zu wiederholen. Cases verweisen in „Voraussetzung" nur noch auf **Daten-** oder **Workflow-Voraussetzungen** (z.B. „Klient:in mit Pseudonym X existiert"), nicht auf den Infra-Setup.

### Test-Umgebung: dev.anlaufstelle.app (Standard)

Tester:innen arbeiten gegen **[https://dev.anlaufstelle.app](https://dev.anlaufstelle.app)** — die öffentliche Demo-Instanz. Sie ist prod-ähnlich konfiguriert (Settings-Modul `devlive` erbt `prod`, nur Email-Backend auf Console), läuft auf Hetzner mit Caddy + Let's Encrypt + ClamAV.

**Was du auf dev hast:**

| Was | Verfügbar? |
|-----|------------|
| HTTPS + gültiges Zertifikat | ✓ |
| ClamAV (Virus-Scan funktioniert mit EICAR) | ✓ |
| Persistente DB (Daten überleben Test-Pausen) | ✓ |
| Standard-Seed-Logins (s.u.) | ✓ |
| MFA, Audit-Log, DSGVO-Paket | ✓ |
| Email-Versand (Pwd-Reset, Notifications) | ✗ Console-Only — Tobias liest die Logs |
| 2 Facilities für RLS-Tests | abhängig von dev-Stand — bei Bedarf Tobias fragen |

### Standard-Logins (Pwd `anlaufstelle2026` für alle)

| Username | Rolle | Facility | Typische Verwendung |
|----------|-------|----------|----------------------|
| `admin` | ADMIN | 1 | Admin-Workflows, Audit-Log, DSGVO-Paket |
| `leitung` | LEAD | 1 | Cases schließen, Retention, Statistik |
| `fachkraft` | STAFF | 1 | Standard-Sozialarbeit (CRUD, Events, Dokumentation) |
| `assistenz` | ASSISTANT | 1 | Niedrigste Rolle, RBAC-Negativtests |
| `admin_2`, `leitung_2`, `fachkraft_2`, `assistenz_2` | je 1 | 2 | Cross-Facility-/RLS-Tests (falls 2. Facility geseedet) |

> ⚠️ **Geteilte Accounts — Konflikt-Hinweise**
> - Logins werden geteilt: parallele Tester:innen sollen sich abstimmen, wer wann mit welchem Account testet.
> - **MFA:** Wenn Tester:in A MFA auf einem Account aktiviert, sind alle anderen ohne TOTP-App ausgeschlossen. Konvention: **MFA auf dev nur durch Tobias setzen lassen** (oder gemeinsamer Backup-Codes-Speicher per 1Password/Bitwarden).
> - **Datenstand:** Tester:innen sehen die Daten der vorherigen Sessions. Vor einem strukturierten Test-Lauf Tobias um `make seed`-Reset bitten.
> - **Löschungen:** Soft-Delete-Anträge sind reversibel (Trash-Frist 30 Tage), aber andere Tester:innen sehen die gelöschten Datensätze nicht mehr.

### Test-Umgebung: lokal (nur für 🔧 LOKAL/SSH-Cases)

Die Cases in **Sektion D** (LOKAL/SSH) benötigen **direkten Server-Zugriff** (z.B. `manage.py shell`, `psql`, Backdate-SQL, `enforce_retention`-Cron). Die jeweils aktuelle Zahl steht im generierten Index ([`test-matrix-index.md`](test-matrix-index.md)). Cases sind im Header mit `🔧 LOKAL/SSH` markiert und werden:

- **lokal** auf Tobias' Maschine durchgeführt (Setup unten), oder
- per **SSH auf dev-Server** (`ssh anlaufstelle@dev.anlaufstelle.app`, dann `docker compose exec web python manage.py …`)

Lokales Setup nur falls 🔧 LOKAL/SSH-Cases anstehen:

| Schritt | Befehl |
|---------|--------|
| Repo aktuell | `git pull` |
| DB & Container | `sudo docker compose up -d` |
| Seed mit 2 Facilities | `make seed FACILITIES=2` |
| Dev-Server starten | `make runserver-e2e` (Port 8844, HTTP) oder `make runserver` (Port 8000) |
| Migrationen | `make migrate` |

### Browser, Mobile, MFA, ClamAV (gilt für dev und lokal)

- **Browser:** Drei Privat-Fenster — Chromium (Default), Firefox, Safari/WebKit. Pro Fenster ein Login.
- **Mobile:** Chromium DevTools → Device-Mode → iPhone 15. Oder echtes Gerät über `https://dev.anlaufstelle.app`.
- **TOTP-App** (für MFA-Cases): KeePassXC, Aegis, Google Authenticator, 1Password.
- **EICAR-Datei** (für Virus-Scan-Cases): EICAR-Test-String als `eicar.com` lokal ablegen — `X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*`. ClamAV auf dev und lokal aktiv.

### Konvention für „Voraussetzung" pro Case

Die Setup-Schritte oben gelten **implizit** und werden **nicht** pro Case wiederholt. Das Feld „Voraussetzung" enthält nur:

- **Daten-Voraussetzungen** — z.B. „Klient:in mit Pseudonym X existiert", „Backdate-Daten via Tobias eingespielt"
- **Vorhergehende Test-Cases** — z.B. „SMK-A-VORM-02 (anonyme:r Klient:in vorhanden)"
- **Modus-Voraussetzungen** — z.B. „Sudo-Mode aktiv"

---

## Status-Legende

> **Wichtig:** Die `Status:`-Zeilen pro Case sind **Default-Werte** im Testkatalog und sollen im Repo immer `☐ Offen` bleiben. Echte Lauf-Status werden in einem separaten Run-Log unter [`runs/`](runs/) erfasst — Vorlage in [`run-template.md`](run-template.md). Direkt in der Matrix Status zu setzen würde den Katalog für jeden Lauf neu schreiben und Audit-Drift erzeugen (#898).

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
| CLIENT | Klient:innen | AUDIT | Audit-Log |
| CASE | Fälle | DSGVO | DSGVO-Paket |
| EPI | Episoden | OFFL | Offline-Modus |
| GOAL | Goals & Milestones | SYS | Sys / Health / Monitoring |
| EVT | Events (Dokumentation) | A11Y | Accessibility (WCAG-Stichproben) |
| ATT | Anhänge | SETUP | Einrichtungs-/Konfigurationsassistent |
| WI | WorkItems | COMP | Betriebs-/Compliance-Dashboard |
| DEL | Lösch-Anträge | PRIV | Datenschutz-Review (Freitext) |
| | | REPORT | Datenschutzfreundliche externe Berichte |

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

## SEKTION A — Anwender-Smoke

> **Zielgruppe:** Sozialarbeiter:innen im Pilotbetrieb. Sprache bewusst frei von Fachjargon — kein „RLS", „HTMX", „CSP". Stattdessen: „Daten anderer Einrichtung", „Live-Aktualisierung", „Sicherheitsregeln".
>
> **Gliederung:** Tagesablauf einer Fachkraft mit gesamten ~12 End-to-End-Workflows. Jeder Workflow ist mehrschrittig und hat **Pause-Punkte** zum Abhaken zwischendurch.
>
> Diese Sektion wird beim Pilotbetrieb (mit echten Anwender:innen) durchgespielt. Bei Auffälligkeiten werden Issues mit Label `pilot-feedback` angelegt.

### Vormittag (VORM)

#### SMK-A-VORM-01 — Tagesstart

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/F/S | ⚪ | `test_workflow_complete.py`, `test_dashboard.py` |


**Vorbereitung:**
- Privates Browser-Fenster, kein aktiver Login.
- TOTP-Authenticator bereit (z.B. Handy mit App).

**Ablauf:**

1. ☐ `http://localhost:8844/` aufrufen → Login-Seite erscheint.
2. ☐ Anmelden mit `fachkraft` / `anlaufstelle2026`.
3. ☐ Zweite Sicherheitsstufe: 6-stelligen Code aus TOTP-App eingeben.
4. ☐ **Live-Aktualisierung:** Der „Zeitstrom" zeigt die jüngsten Aktivitäten der Einrichtung.
5. ☐ Im Hauptmenü auf **„Übergabe"** klicken — die Übergabe der Vorschicht lesen.
6. ☐ Wichtige offene Punkte mental oder auf Papier notieren.
7. ☐ Im Menü auf **„Aufgaben"** wechseln — die eigene Inbox öffnet sich.
8. ☐ Filter „Heute fällig" aktivieren, alle eigenen offenen Aufgaben sichten.
9. ☐ Ein Aufgaben-Element öffnen (z.B. ein offener Beratungstermin).

**Sicherheitsregeln im Hintergrund:**
- Anmeldungen werden protokolliert (Auditierbarkeit).
- Sitzung läuft automatisch nach 30 Minuten Inaktivität ab.

**Erwartung:**
- Du bist eingeloggt, hast die Übergabe gelesen und kennst die heutigen Aufgaben.

**Status:** ☐ Offen

---

#### SMK-A-VORM-02 — Erstkontakt anonym

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/F/S | ✓ | `test_min_contact_stage_anonymous.py` |

**Voraussetzung:** SMK-A-VORM-01

**Vorbereitung:**
- Eingeloggt als `fachkraft`. Eine Person betritt die Anlaufstelle, möchte anonym bleiben.

**Ablauf:**

1. ☐ Im Menü „Klient:innen" → **„Neue:r Klient:in"** klicken.
2. ☐ Stage **„anonym"** wählen — Pseudonymsfeld bleibt leer/optional.
3. ☐ Bei „Alters-Cluster" zutreffende Spanne auswählen (z.B. 25–34).
4. ☐ Speichern → Klient:in wird mit zufälliger interner Kennung angelegt.
5. ☐ Sofort ein **„Event"** (Beratungsgespräch) anlegen.
6. ☐ Dokumenten-Typ **„Beratung"** wählen — die passenden Felder erscheinen.
7. ☐ Inhaltsfelder ausfüllen, Sensitivitätsstufe **„mittel"** belassen.
8. ☐ Speichern → Event erscheint sofort in der Klient:innen-Timeline.

**Sicherheitsregeln im Hintergrund:**
- Pseudonyme statt Klarnamen schützen die Anonymität (Datenminimierung).
- Sensitivitätsstufe steuert, wer das Event später im „Zeitstrom" sieht.

**Erwartung:**
- Anonyme:r Klient:in existiert mit einem dokumentierten Beratungsgespräch.

**Status:** ☐ Offen

---

#### SMK-A-VORM-03 — Klient:in identifizieren (Stage-Wechsel)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/F | ⚪ | `test_contact_stage.py` |

**Voraussetzung:** SMK-A-VORM-02 (anonyme:r Klient:in existiert)

**Vorbereitung:**
- Klient:in aus VORM-02 sucht erneut die Anlaufstelle auf und gibt nun einen Namen an.

**Ablauf:**

1. ☐ Über die Suche oder Klient:innen-Liste die Person aus VORM-02 öffnen.
2. ☐ Auf **„Bearbeiten"** klicken.
3. ☐ Pseudonym eintragen (z.B. „Vornamen-Initial + Geburts-Jahr").
4. ☐ Stage von **„anonym"** auf **„identifiziert"** ändern.
5. ☐ Speichern.
6. ☐ Detail-View prüfen: Stage-Wechsel ist sichtbar.

**Sicherheitsregeln im Hintergrund:**
- Stage-Wechsel wird im Audit-Protokoll vermerkt (Wer hat wann was geändert).

**Erwartung:**
- Klient:in erscheint nun mit Pseudonym in der Liste, Stage „identifiziert".

**Status:** ☐ Offen

---

### Mittag (MITT)

#### SMK-A-MITT-01 — WorkItem-Triage

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/F/S | ✓ | `test_workitem_ui.py` |

**Voraussetzung:** SMK-A-VORM-01

**Vorbereitung:**
- Inbox enthält mehrere offene Aufgaben (Seed liefert Beispiele).

**Ablauf:**

1. ☐ Aufgaben-Inbox öffnen.
2. ☐ Drei abgearbeitete Aufgaben anhaken (Status auf **„erledigt"**).
3. ☐ Dabei den **Live-Tausch** beobachten: Status ändert sich ohne Seiten-Reload.
4. ☐ Eine offene Aufgabe **markieren** (Checkbox links).
5. ☐ Eine zweite Aufgabe markieren.
6. ☐ Auf **„Bulk-Aktion: Zuweisen"** klicken → an Lead-Kollegin reassignen.
7. ☐ Bestätigen.
8. ☐ Ein hochpriorisiertes Element öffnen → Status auf **„in Arbeit"**.

**Sicherheitsregeln im Hintergrund:**
- Bulk-Aktionen funktionieren nur innerhalb der eigenen Einrichtung.

**Erwartung:**
- Inbox ist aufgeräumt, drei erledigt, zwei reassignt, eine in Bearbeitung.

**Status:** ☐ Offen

---

#### SMK-A-MITT-02 — Suche & Verlauf

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/F/S | ✓ | `test_clients_search.py`, `test_fuzzy_search.py` |

**Voraussetzung:** SMK-A-VORM-01

**Vorbereitung:**
- Eine bekannte Klient:in aus dem Seed (z.B. Pseudonym mit dem Anfangsbuchstaben „M").

**Ablauf:**

1. ☐ In das Suchfeld oben rechts „M" tippen.
2. ☐ Live-Vorschläge erscheinen — auf einen passenden Treffer klicken.
3. ☐ Klient:innen-Detailansicht öffnet sich.
4. ☐ Timeline runterscrollen — alle Beratungs-Events sind chronologisch sichtbar.
5. ☐ Ein Event aus dem Vormonat anklicken → Detail öffnet sich.
6. ☐ Inhalte lesen, dann „Zurück zur Klient:in".
7. ☐ Über „Ähnliche Suche" einen Tippfehler ausprobieren („Mlle" statt „Mella") — System findet trotzdem den richtigen Treffer.

**Sicherheitsregeln im Hintergrund:**
- Suche filtert automatisch nur Klient:innen der eigenen Einrichtung.

**Erwartung:**
- Du hast den vollständigen Verlauf der Klient:in eingesehen, inkl. einem alten Event.

**Status:** ☐ Offen

---

### Nachmittag (NACH)

#### SMK-A-NACH-01 — Case-Episode abschließen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | leitung | C/F/S | ⚪ | `test_workflow_complete.py`, `test_cases.py` |

**Voraussetzung:** SMK-A-VORM-01 (mit `leitung`-Login statt `fachkraft`)

**Vorbereitung:**
- Existierender Fall (Case) mit aktiver Episode, mehreren Goals und Milestones.

**Ablauf:**

1. ☐ Im Menü „Fälle" → den entsprechenden Fall öffnen.
2. ☐ Aktiven Goals scrollen, ein Milestone als **„erreicht"** ankreuzen.
3. ☐ Ein zweites Milestone ebenfalls als erreicht markieren.
4. ☐ Goal als **„abgeschlossen"** markieren, sobald alle Milestones erledigt sind.
5. ☐ Auf **„Episode schließen"** klicken.
6. ☐ Outcome wählen (z.B. „erfolgreich", „abgebrochen") und kurze Notiz.
7. ☐ Speichern.
8. ☐ Falls Fall komplett abgeschlossen: **„Fall schließen"** mit Begründung.

**Sicherheitsregeln im Hintergrund:**
- Nur Lead-Rolle darf Fälle schließen.
- Schließvorgang wird auditiert.

**Erwartung:**
- Episode ist geschlossen, Goals abgehakt, ggf. Fall ebenfalls geschlossen.

**Status:** ☐ Offen

---

#### SMK-A-NACH-02 — Quick-Statistik & CSV-Export

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | leitung | C/F/S || `test_export_statistics.py`, `test_statistics_dashboard.py` |

**Voraussetzung:** SMK-A-VORM-01 (mit `leitung`-Login)

**Vorbereitung:**
- Eingeloggt als `leitung`.

**Ablauf:**

1. ☐ Im Menü auf **„Statistik"** klicken.
2. ☐ Dashboard mit Diagrammen lädt (Anzahl Klient:innen, Events, Trends).
3. ☐ Filter auf **„Aktuelles Quartal"** setzen.
4. ☐ Diagramme aktualisieren sich (Live-Tausch).
5. ☐ Auf **„CSV-Export"** klicken → Datei wird heruntergeladen.
6. ☐ CSV in Tabellenkalkulation öffnen → Spalten korrekt, Werte plausibel.

**Sicherheitsregeln im Hintergrund:**
- CSV enthält nur Daten der eigenen Einrichtung.
- Export wird auditiert.

**Erwartung:**
- CSV liegt im Download-Ordner, Inhalt entspricht der Bildschirm-Statistik.

**Status:** ☐ Offen

---

### Abend (ABEND)

#### SMK-A-ABEND-01 — Datei-Upload und Logout

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/F/S | ✓ | `test_attachment_versioning_stage_b.py`, `test_logout_cleanup.py` |

**Voraussetzung:** SMK-A-VORM-02 (Klient:in mit Event vorhanden)

**Vorbereitung:**
- Ein PDF (z.B. Beratungs-Notiz, max. 25 MB) und die EICAR-Datei (SETUP-07) bereithalten.

**Ablauf:**

1. ☐ Das Event aus VORM-02 öffnen.
2. ☐ Auf **„Anhang hinzufügen"** klicken → das harmlose PDF auswählen.
3. ☐ Upload-Status erscheint, kurz danach **„Virusprüfung erfolgreich"**.
4. ☐ Anhang ist in der Liste sichtbar, herunterladbar.
5. ☐ Erneut **„Anhang hinzufügen"** → diesmal die EICAR-Test-Datei.
6. ☐ Upload wird **abgewiesen** mit Hinweis „Datei enthält Schadcode".
7. ☐ Im Menü oben rechts auf **„Abmelden"** klicken.
8. ☐ Login-Seite erscheint, lokale Daten sind gelöscht (in den Browser-Entwicklertools sichtbar).

**Sicherheitsregeln im Hintergrund:**
- Alle Uploads werden auf Schadcode geprüft (ClamAV).
- Beim Abmelden wird der lokale Speicher des Browsers geleert (`Clear-Site-Data: storage`).

**Erwartung:**
- Sauberer Logout, lokale Browser-Daten sind weg.

**Status:** ☐ Offen

---

### Krise (CRIS)

#### SMK-A-CRIS-01 — Krisen-Eskalation

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/F/S | ✓ ||

**Voraussetzung:** SMK-A-VORM-01

**Vorbereitung:**
- Eine Klient:in in akuter Krisensituation kommt in die Einrichtung.

**Ablauf:**

1. ☐ Klient:in suchen oder neu anlegen (Stage „identifiziert").
2. ☐ Schnell-Erfassung **„Quick-Capture"** öffnen.
3. ☐ Dokumenten-Typ **„Krise"** oder **„Notfall"** wählen.
4. ☐ Sensitivitätsstufe **„hoch"** setzen.
5. ☐ Kurze Beschreibung der Krise eintragen.
6. ☐ Speichern.
7. ☐ Aufgabe **„Nachsorge: Krise XYZ"** erstellen, Priorität **„hoch"**, Lead-Kollegin als Verantwortliche.
8. ☐ Lead bekommt das WorkItem in ihrer Inbox.

**Sicherheitsregeln im Hintergrund:**
- Hochsensitive Events sind im allgemeinen Zeitstrom unsichtbar — nur Lead/Admin sehen Inhalte.

**Erwartung:**
- Krisenfall ist dokumentiert, Lead-Kollegin hat Aufgabe in Inbox.

**Status:** ☐ Offen

---

### Streetwork-Offline (OFFL)

#### SMK-A-OFFL-01 — Streetwork ohne Internet

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/S | ✓ | `test_offline_apis.py`, `test_offline_login_bootstrap.py` |


**Vorbereitung:**
- Smartphone oder Tablet mit Browser, mobil ins WLAN.

**Ablauf:**

1. ☐ Mit `fachkraft` einloggen, vollständig laden lassen.
2. ☐ Zur eingebauten Klient:innen-Übersicht navigieren — Daten werden lokal gespeichert.
3. ☐ Im DevTools → Network → **„Offline"** aktivieren (Netz simuliert weg).
4. ☐ Eine bekannte Klient:in über das Offline-Menü öffnen (`/offline/clients/<id>/`).
5. ☐ Detailansicht öffnet sich aus lokalem Cache.
6. ☐ Ein neues Event lokal anlegen — wird im lokalen Speicher abgelegt.
7. ☐ Network → **„Online"** wieder aktivieren.
8. ☐ Sync-Vorgang sollte automatisch starten — Konflikte (falls vorhanden) auf der Konflikt-Seite reviewen.

**Sicherheitsregeln im Hintergrund:**
- Offline-Daten sind im lokalen Speicher verschlüsselt (mit dem Passwort der Anmeldung).
- Beim nächsten Passwort-Wechsel verfallen die offline-Daten automatisch.

**Erwartung:**
- Du hast offline gearbeitet, beim Wieder-Online sind die Daten synchron.

**Status:** ☐ Offen

---

### Mobile-Smoke (MOBI)

#### SMK-A-MOBI-01 — Mobile Vormittag (komplette Tagesstart-Sequenz)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C | ✓ | `test_mobile.py`, `test_layout.py` |


**Vorbereitung:**
- Browser auf iPhone-Viewport umgestellt.

**Ablauf:**

1. ☐ Komplette Sequenz aus SMK-A-VORM-01 durchspielen — Login, MFA, Übergabe, WorkItems.
2. ☐ Achten auf: Touch-Targets ≥ 44px, kein horizontales Scrollen, Menü-Hamburger funktioniert.
3. ☐ Die Klient:innen-Liste wird als Card-Layout (kein Tabellen-Scroll) gerendert.
4. ☐ Die Übergabe ist auf Mobil lesbar (Stats-Grid passt sich an).
5. ☐ WorkItem-Inbox: Status-Toggle per Tap funktioniert, Bulk-Aktionen erreichbar.

**Erwartung:**
- Voller Tagesstart-Workflow auf iPhone-Viewport ohne Layout-Brüche.

**Status:** ☐ Offen

---

#### SMK-A-MOBI-02 — Mobile Nachmittag (Case-Update + Foto-Upload)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C | ✓ ||

**Voraussetzung:** Case mit Episode aus NACH-01.

**Vorbereitung:**
- Foto in der Mobile-Galerie (oder Test-Bild auf iPhone-Emulator).

**Ablauf:**

1. ☐ Case auf Mobile öffnen.
2. ☐ Goal/Milestone-Toggle per Tap.
3. ☐ Episode mit Outcome schließen.
4. ☐ Auf einem Event innerhalb des Cases einen Anhang hinzufügen.
5. ☐ Beim Datei-Picker **„Foto aufnehmen"** wählen (mobile-spezifisch).
6. ☐ Foto wird hochgeladen, Virus-Check passiert.
7. ☐ Anhang erscheint in der Liste mit Vorschau (PNG/JPEG).

**Erwartung:**
- Case-Update + Foto-Upload auf Mobile funktioniert ohne Layout-Probleme.

**Status:** ☐ Offen

---

## SEKTION B — Anwender-Komplett (systematisch)

> **Zielgruppe:** Tobias als Entwickler/Tester. Systematisches Durchgehen aller Bereiche vor jedem Major-Release.
>
> **Format:** Pro Bereich ein `<details open>`-Block mit Header (Routen, View-Klassen, E2E-Coverage, Spezial-Setup) und allen Cases. Bereiche in dieser Reihenfolge:
> AUTH → MFA → ACCT → SUDO → PWA → CLIENT → CASE → EPI → GOAL → EVT → ATT → WI → DEL → RET → SRCH → ZS → HOV → STAT → AUDIT → DSGVO → OFFL → SYS.

<details open>
<summary><strong>🔐 AUTH — Authentifizierung (10 Cases)</strong></summary>

**Routen:** `/login/`, `/logout/`, `/password-change/`, `/password-reset/`, `/password-reset/done/`, `/password-reset/<uidb64>/<token>/`, `/password-reset/complete/` 
**Views:** `src/core/views/auth.py` (`CustomLoginView`, `CustomLogoutView`, `RateLimitedPasswordResetView`, `CustomPasswordChangeView`) 
**Services:** `src/core/services/login_lockout.py` (`is_locked`, `unlock`, Schwelle 10/15min), `src/core/services/password.py` (12-Zeichen-Initial-Generator), `src/core/services/audit_hash.py` (`hmac_hash_email`), `src/core/services/offline_keys.py` 
**Middleware:** `core.middleware.password_change.ForcePasswordChangeMiddleware` 
**Settings:** `AUTH_PASSWORD_VALIDATORS` (min_length=12, BSI/NIST), `SESSION_COOKIE_AGE=1800`, `SESSION_SAVE_EVERY_REQUEST=False`, `AUDIT_HASH_KEY`, Audit-Action-Choices in `src/core/models/audit.py` 
**Signals:** `src/core/signals/audit.py` (LOGIN/LOGOUT/LOGIN_FAILED via Django-Auth-Signals) 
**E2E-Coverage:** `test_auth_roles.py`, `test_password_reset.py`, `test_logout_cleanup.py`, `test_security_hardening.py` 
**Spezial-Setup:**
- Login-Lockout-Test braucht 10 sequenzielle Fehlversuche innerhalb von 15 Min — IP-Rate-Limit (5/m) muss vorher umgangen werden (z.B. Cookie-Reset zwischen Bursts oder Limit per Settings hochsetzen).
- Pwd-Reset-Test braucht `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend` (dev/e2e-Default) oder MailHog für Token-Capture.
- Seed-User: `admin`, `thomas` (Leitung), `miriam` (Fachkraft), `lena` (Assistenz) — Passwort `anlaufstelle2026`. Quelle: `src/core/seed/constants.py`.

---

### TC-ID: ENT-AUTH-01 — Erfolgreicher Login mit korrektem Passwort

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | fachkraft (`miriam`) | C/F/S | ✓ | `test_auth_roles.py` |


**Vorbereitung:**
- Privates Browser-Fenster öffnen, kein aktiver Login.
- `make seed` ausgeführt, sodass `miriam` mit `anlaufstelle2026` existiert.

**Schritte:**
1. `https://localhost:8844/login/` aufrufen.
2. Username `miriam`, Passwort `anlaufstelle2026` eingeben.
3. Submit klicken.

**Erwartetes Ergebnis:**
- HTTP 302 Redirect auf `/` (Zeitstrom).
- AuditLog enthält Eintrag `action=login` mit `user=miriam`, befüllter `ip_address`, aktuellem `timestamp`, `facility` gesetzt.
- Session-Cookie `sessionid` ist gesetzt mit `Secure`, `HttpOnly`, `SameSite=Lax`.
- `request.session["mfa_verified"]` ist `False` gesetzt (siehe `CustomLoginView.form_valid`), MFA-Middleware leitet erst weiter, wenn Device existiert.
- Im Header sichtbar: Username/Rolle der angemeldeten Person.

**DSGVO/Security-Note:**
- Erfolgreicher Login wird auditiert (Art. 32 TOMs); Audit-Signal in `src/core/signals/audit.py`.
- Session-Timeout 30 Min aus `SESSION_COOKIE_AGE=1800`, ggf. überschrieben durch `facility.settings.session_timeout_minutes`.

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-02 — Login mit falschem Passwort (1× Versuch, kein Lockout)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | fachkraft (`miriam`) | C/F/S | ⚪ | `test_auth_roles.py` |


**Vorbereitung:**
- Privates Fenster, kein aktiver Login.
- AuditLog vor dem Test mit `LOGIN_FAILED`-Count notieren (Baseline).

**Schritte:**
1. `https://localhost:8844/login/` aufrufen.
2. Username `miriam`, Passwort `falsch123!` eingeben.
3. Submit klicken.

**Erwartetes Ergebnis:**
- HTTP 200, Login-Form wird mit Fehlermeldung `"Bitte geben Sie einen korrekten Benutzernamen und ein Passwort ein"` o.ä. neu gerendert.
- AuditLog hat **einen** neuen `LOGIN_FAILED`-Eintrag mit `user=miriam`, `facility=miriam.facility`, `ip_address` befüllt, `detail.username="miriam"`.
- Kein Session-Cookie für eingeloggten User (Anonymous-Session OK).
- `is_locked(miriam)` gibt weiterhin `False` zurück (1 < 10).

**DSGVO/Security-Note:**
- LOGIN_FAILED wird auditiert (Art. 32, Forensik bei Brute-Force).
- Antwortzeit/HTML soll nicht unterscheiden, ob User existiert oder nicht (Anti-Enumeration).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-03 — Login-Lockout nach 10 Fehlversuchen in 15 Min

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | fachkraft (`miriam`) | C || `test_security_hardening.py` |

**Voraussetzung:** IP-Rate-Limit (5/m) vorübergehend deaktiviert ODER `RATELIMIT_ENABLE=False` in Test-Settings.

**Vorbereitung:**
- Privates Fenster, kein aktiver Login.
- AuditLog für `miriam` leeren oder `LOGIN_UNLOCK` als Cutoff setzen.

**Schritte:**
1. 10× hintereinander auf `/login/` Username `miriam` + falsches Passwort eingeben (innerhalb von <15 Min).
2. Beim 11. Versuch das **korrekte** Passwort `anlaufstelle2026` eingeben.

**Erwartetes Ergebnis:**
- Versuche 1–10: jeweils `LOGIN_FAILED`-AuditLog-Eintrag.
- Versuch 11: trotz korrektem Passwort kein Login. Form-Fehler:
 `"Ihr Konto ist nach mehreren fehlgeschlagenen Versuchen temporär gesperrt. Bitte später erneut versuchen oder Administration kontaktieren."`
- Zusätzlicher AuditLog-Eintrag `LOGIN_FAILED` mit `detail.reason="locked"` und `detail.message="Login blockiert durch Account-Lockout"`.
- Kein `LOGIN`-Erfolgs-Eintrag, kein Session-Cookie.
- `is_locked(miriam)` gibt `True` zurück (Schwelle erreicht innerhalb 15-Minuten-Fenster).
- Admin kann via `unlock` / `LOGIN_UNLOCK`-Audit den Cutoff setzen, danach werden die alten FAILED-Einträge ignoriert.

**DSGVO/Security-Note:**
- Lockout-Konstanten: `LOCKOUT_THRESHOLD = 10`, `LOCKOUT_WINDOW = timedelta(minutes=15)` in `src/core/services/login_lockout.py`.
- Concurrency-Schutz via `transaction.atomic` + `User.objects.select_for_update` (Refs #737).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-04 — IP-Rate-Limit (5/min POST) — 6. Versuch HTTP 429

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | unauthentifiziert | C || `test_security_hardening.py` |

**Voraussetzung:** `django_ratelimit` aktiv (Default in dev/e2e).

**Vorbereitung:**
- Cache leeren, sodass IP-Bucket leer startet (z.B. Redis flushen oder LocMem-Cache neu).
- Privates Fenster, gleiche Quell-IP.

**Schritte:**
1. Innerhalb 60 Sekunden 5× POST auf `/login/` mit Username `miriam` + falschem Passwort.
2. 6. Versuch innerhalb derselben Minute durchführen.

**Erwartetes Ergebnis:**
- Versuche 1–5: HTTP 200, Login-Form mit Fehler.
- Versuch 6: `block=True` greift → HTTP 429 (Ratelimited)-Response (Django-Default-Page oder Custom-Page).
- Es entstehen **keine** zusätzlichen `LOGIN_FAILED`-Einträge für gesperrte Versuche, da die View gar nicht ausgeführt wird.
- Nach 60 Sekunden Wartezeit ist neue Anfrage wieder möglich.

**DSGVO/Security-Note:**
- Decorator: `@method_decorator(ratelimit(key="ip", rate="5/m", method="POST", block=True))` in `CustomLoginView.post`.
- Schützt vor klassischem Brute-Force von einer IP (Refs #598).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-05 — Username-Rate-Limit (10/h POST) — verteiltes Botnet

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | unauthentifiziert | C || `test_security_hardening.py` |

**Voraussetzung:** mindestens 2 Quell-IPs simulierbar (z.B. via `X-Forwarded-For` mit `TRUSTED_PROXY_HOPS=1`) oder Direkt-Cache-Manipulation.

**Vorbereitung:**
- Cache leeren.
- Username-Bucket-Key wird in `_login_username_key` lowercased + gestrippt — Variationen wie `Miriam` und ` miriam ` zählen auf denselben Bucket.

**Schritte:**
1. Mit IP A: 5 fehlgeschlagene Logins für `miriam`.
2. Mit IP A: 6. Versuch → IP-Limit greift (HTTP 429).
3. Mit IP B (eine Minute später, sodass IP-Bucket frei): weitere fehlgeschlagene Logins, insgesamt bis 10× kumuliert über IPs.
4. 11. Versuch über beliebige IP für `miriam`.

**Erwartetes Ergebnis:**
- Bei Versuch 11 (egal welche IP): HTTP 429 — Username-Bucket voll, `block=True`.
- Die Sperre gilt für `miriam` für 1 Stunde, andere Usernames sind nicht betroffen.
- `Miriam`, ` miriam ` und `miriam` zählen alle in denselben Bucket (Lowercased+Strip in `_login_username_key`).

**DSGVO/Security-Note:**
- Decorator: `@method_decorator(ratelimit(key=_login_username_key, rate="10/h", method="POST", block=True))`.
- Schützt vor verteilten Angriffen mit rotierenden IPs (Botnet) — echter User würde nach max. 10 falschen Eingaben in 1h auf Pwd-Reset gehen (Refs #598).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-06 — Logout-Cleanup mit Clear-Site-Data-Header

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | fachkraft (`miriam`) | C/F/S | ✓ | `test_logout_cleanup.py` |

**Voraussetzung:** aktiver Login.

**Vorbereitung:**
- Login als `miriam`.
- DevTools → Application → IndexedDB: prüfen, dass `offline-queue`/Client-Cache-Stores beschrieben sind (z.B. nach Klick auf einen Klienten).
- Network-Tab geöffnet.

**Schritte:**
1. Logout-Link/Button klicken (POST `/logout/`).
2. Im Network-Tab den Logout-Response inspizieren.
3. Application-Tab erneut prüfen (IndexedDB, LocalStorage, SessionStorage).

**Erwartetes Ergebnis:**
- HTTP 302 Redirect auf `/login/` (`next_page = "/login/"`).
- Response-Header enthält **`Clear-Site-Data: "storage"`** (siehe `CustomLogoutView.dispatch`).
- Browser räumt LocalStorage, SessionStorage, IndexedDB für die Origin auf — IndexedDB-Stores sind leer.
- Session-Cookie ist gelöscht (kein gültiger `sessionid` mehr).
- AuditLog enthält `LOGOUT`-Eintrag mit `user=miriam`, `facility`, `ip_address` (`on_user_logged_out`-Signal).

**DSGVO/Security-Note:**
- Clear-Site-Data räumt offline gespeicherte (verschlüsselte) Klienten-Daten beim Logout (DSGVO-Datenminimierung; verschlüsseltes IndexedDB-Material wäre ohne Pwd unbrauchbar, aber Cleanup ist explizit).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-07 — Session-Timeout nach 30 Min Inaktivität

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | fachkraft (`miriam`) | C || `test_security_hardening.py` |

**Voraussetzung:** Test-Setting `SESSION_COOKIE_AGE=10` (Sekunden) ODER Cookie manuell ablaufen lassen.

**Vorbereitung:**
- Login als `miriam`.
- Session-Cookie inspizieren: `Max-Age=1800` (oder Wert aus Test-Setting).
- Optional: facility.settings.session_timeout_minutes anders gesetzt → bestätigen, dass `CustomLoginView.form_valid` `request.session.set_expiry(timeout)` mit dem Facility-Wert aufruft.

**Schritte:**
1. 31 Min nichts klicken (oder Test-Cookie 11s ablaufen lassen).
2. Beliebige Seite z.B. `/clients/` aufrufen.

**Erwartetes Ergebnis:**
- HTTP 302 Redirect auf `/login/?next=/clients/` (Django-Default für `LOGIN_URL=/login/`).
- Vorherige Session ist invalide.
- `SESSION_SAVE_EVERY_REQUEST=False` — HTMX-Microrequests verlängern die Session NICHT (nur tatsächliche Änderungen). Inaktivitätsfenster bleibt 30 Min ab letzter Mutation.
- Nach Re-Login wird `next` korrekt aufgelöst.

**DSGVO/Security-Note:**
- Setting `SESSION_COOKIE_AGE=1800` in `src/anlaufstelle/settings/base.py`.
- `SESSION_SAVE_EVERY_REQUEST=False` reduziert DB-Write-Amplifikation, lässt Inaktivitäts-Timeout aber wirken (Refs #733).
- Login speichert ggf. `session.set_expiry(facility.settings.session_timeout_minutes * 60)` (override).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-08 — Passwort-Wechsel (Validatoren greifen)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | fachkraft (`miriam`) | C/F/S | ⚪ | `test_security_hardening.py` |

**Voraussetzung:** aktiver Login als `miriam`.

**Vorbereitung:**
- Vorher (Salt vor Wechsel) `User.offline_key_salt` lesen (z.B. via Shell oder `/auth/offline-key-salt/` POST). Notieren.
- AuditLog `OFFLINE_KEY_FETCH`-Count notieren.

**Schritte:**
1. `/password-change/` aufrufen.
2. Test A — Validierung (negativ):
 - Altes Pwd: `anlaufstelle2026`
 - Neues Pwd: `kurz` (zu kurz, < 12 Zeichen)
 - Submit → Form-Fehler `"Dieses Passwort ist zu kurz."` (MinimumLengthValidator, min_length=12).
3. Test B — Validierung (negativ): neues Pwd `123456789012` (rein-numerisch) → `NumericPasswordValidator` schlägt an.
4. Test C — Validierung (negativ): neues Pwd `password1234` (häufig) → `CommonPasswordValidator` schlägt an.
5. Test D — Validierung (negativ): neues Pwd ähnlich wie Username/Email → `UserAttributeSimilarityValidator` greift.
6. Test E — Erfolg: neues Pwd `Sicher2026!Stark` (>=12, gemischt, kein Häufiges) → Submit.

**Erwartetes Ergebnis:**
- A–D: HTTP 200, Form mit Validator-Fehlermeldung re-rendert.
- E: HTTP 302 Redirect auf `/` (`success_url="/"`).
- Nach E:
 - `user.must_change_password` ist auf `False` zurückgesetzt (siehe `CustomPasswordChangeView.form_valid`).
 - `user.offline_key_salt` ist auf `""` (leer) zurückgesetzt → wird beim nächsten `/auth/offline-key-salt/`-Aufruf neu generiert.
 - Session bleibt gültig (Django-Standard `update_session_auth_hash`).
- Validatoren-Reihenfolge laut `AUTH_PASSWORD_VALIDATORS` in `base.py`.

**DSGVO/Security-Note:**
- Min-Length=12 entspricht BSI/NIST für §203/Art.-9-Daten (Refs #789).
- Salt-Rotation invalidiert alte client-seitige IndexedDB-Schlüssel — alte verschlüsselte Records werden beim nächsten Login als „garbage" verworfen.

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-09 — Passwort-Reset per E-Mail (HMAC-Hash im AuditLog)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | unauthentifiziert | C/F/S | ⚪ | `test_password_reset.py` |

**Voraussetzung:** `EMAIL_BACKEND=console` (dev) oder MailHog erreichbar; `DJANGO_AUDIT_HASH_KEY` gesetzt (sonst SHA256-Fallback).

**Vorbereitung:**
- E-Mail von `miriam` ist befüllt (via Seed/Admin).
- AuditLog mit `PASSWORD_RESET_REQUESTED` vor dem Test inspizieren (Baseline).
- Console-Output / MailHog beobachten.

**Schritte:**
1. `/password-reset/` aufrufen.
2. E-Mail von `miriam` eingeben, Submit.
3. Generische „Wir haben Ihnen eine E-Mail geschickt"-Seite (`/password-reset/done/`) bestätigen.
4. Aus Console/MailHog den Reset-Link `/password-reset/<uidb64>/<token>/` öffnen.
5. Neues Passwort `Sicher2026!NeuPw` 2× eingeben, Submit → `/password-reset/complete/`.
6. Mit `miriam` und neuem Pwd erfolgreich einloggen.
7. Test mit **unbekannter** E-Mail wiederholen — Schritt 2 mit `niemand@example.com`.

**Erwartetes Ergebnis:**
- Schritt 2 (existierend): AuditLog enthält `PASSWORD_RESET_REQUESTED` mit `user=miriam`, `target_type="User"`, `target_id=miriam.pk`, `detail={"email_hash": "<hex>"}`. **Kein Klartext-E-Mail im Log.**
- Schritt 7 (unbekannt): AuditLog hat ebenfalls `PASSWORD_RESET_REQUESTED`-Eintrag mit `user=None`, `target_type=""`, `detail={"email_hash": "<hex>"}` — gleicher generischer Done-Screen, **keine Enumeration** möglich.
- Reset-Token ist gültig (Django-Default: 3 Tage), nach Pwd-Setzen `LOGIN`-AuditLog erfolgreich.
- Rate-Limit `5/m IP POST` auf `/password-reset/` greift bei Spam (HTTP 429 ab dem 6. Versuch).

**DSGVO/Security-Note:**
- Klartext-E-Mails im append-only AuditLog widersprächen DSGVO-Datenminimierung (Refs #791) → HMAC-Hash via `hmac_hash_email` in `services/audit_hash.py`.
- Gleiche E-Mail → gleicher Hash (Lookup für Forensik möglich), aber keine PII in 24-Monats-Retention.
- Anti-Enumeration: identische Response-Page egal ob E-Mail bekannt oder nicht (Decorator `try/except`-geschützt).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-10 — must_change_password-Flow (frischer User)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | beliebig (z.B. neue Fachkraft) | C/F/S | ⚪ | `test_security_hardening.py` |

**Voraussetzung:** neuer User mit `must_change_password=True` (z.B. via Admin-UI oder Invite-Flow).

**Vorbereitung:**
- User `neue_fk` mit Initial-Pwd (12 Zeichen aus `generate_initial_password`) und `must_change_password=True` anlegen.
- Privates Fenster.

**Schritte:**
1. `/login/` mit `neue_fk` + Initial-Pwd → Submit.
2. Sofort versuchen, `/clients/` direkt aufzurufen.
3. Auf `/password-change/` neues, regelkonformes Pwd setzen.
4. Erneut `/clients/` aufrufen.

**Erwartetes Ergebnis:**
- Schritt 1: Login erfolgreich (HTTP 302).
- Schritt 2: `ForcePasswordChangeMiddleware` redirected zu `/password-change/` (302), unabhängig vom angeforderten Pfad. EXEMPT_URLS bleiben erreichbar (`/login/`, `/logout/`, `/password-change/`, `/password-reset/`, `/static/`).
- Schritt 3: `CustomPasswordChangeView.form_valid` setzt `must_change_password=False` und leert `offline_key_salt`. Redirect auf `/`.
- Schritt 4: Anwendung normal nutzbar, kein Redirect mehr.

**DSGVO/Security-Note:**
- Mittelweg gegen frische Initial-Passwörter, die im Klartext (Onboarding-PDF, Admin-Mail) existieren — User muss sofort eigenes Pwd setzen.
- Initial-Generator: `generate_initial_password(length=12)` aus `services/password.py` (12 Zeichen, ASCII letters+digits, BSI-konform).

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>🔑 MFA — Zwei-Faktor-Authentifizierung (9 Cases)</strong></summary>

**Routen:** `/mfa/setup/`, `/mfa/verify/`, `/mfa/settings/`, `/mfa/disable/`, `/mfa/backup-codes/`, `/mfa/backup-codes/regenerate/` 
**Views:** `src/core/views/mfa.py` (`MFASetupView`, `MFAVerifyView`, `MFASettingsView`, `MFADisableView`, `MFABackupCodesView`, `MFARegenerateBackupCodesView`) 
**Services:** `src/core/services/mfa.py` (`generate_backup_codes`, `verify_backup_code`, `remaining_backup_codes`, SHA-256-Hash, 128-bit Entropie via `secrets.token_urlsafe(16)`) 
**Middleware:** `core.middleware.mfa.MFAEnforcementMiddleware` (EXEMPT_URLS für /login/, /logout/, /mfa/, /static/, /sw.js, /manifest.json, /auth/offline-key-salt/, /health/) 
**Settings:** `OTP_TOTP_ISSUER="Anlaufstelle"`; Apps: `django_otp`, `django_otp.plugins.otp_totp`, `django_otp.plugins.otp_static` 
**E2E-Coverage:** `test_mfa_setup_flow.py`, `test_mfa_backup_codes.py`, `test_security_hardening.py` 
**Spezial-Setup:**
- TOTP-Code-Generierung im Test: `pyotp` mit gleichem Secret wie QR-Code (Base32-decoded aus `device.bin_key`).
- Backup-Codes: 22-Zeichen URL-safe Base64 (case-sensitive!) ab Refs #790. Legacy-Format `xxxx-xxxx` (8 Hex + Dash) wird beim Verify toleriert.
- `MFADisableView` ist `RequireSudoModeMixin` — vor Disable muss Sudo-Mode aktiv sein.
- `request.user.is_mfa_enforced` = `True`, wenn `User.mfa_required=True` ODER `facility.settings.mfa_enforced_facility_wide=True`.

---

### TC-ID: ENT-MFA-01 — TOTP-Setup: QR scannen, Test-Code bestätigen, Aktivierung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`) | C/F/S | ✓ | `test_mfa_setup_flow.py` |

**Voraussetzung:** eingeloggt; bisher kein bestätigtes TOTPDevice.

**Vorbereitung:**
- Login als `miriam`.
- DB-Check: `TOTPDevice.objects.filter(user=miriam, confirmed=True).exists == False`.

**Schritte:**
1. `/mfa/settings/` aufrufen → Status „2FA nicht aktiv".
2. Auf „2FA einrichten" klicken oder direkt `/mfa/setup/` öffnen.
3. QR-Code wird als Data-URL-PNG angezeigt; alternativ Base32-Secret darunter sichtbar.
4. Authenticator-App (z.B. FreeOTP+) öffnen → Account hinzufügen → QR scannen ODER Secret manuell eintippen (Issuer "Anlaufstelle").
5. App liefert 6-stelligen Code; Code in das `token`-Input eingeben → Submit.

**Erwartetes Ergebnis:**
- HTTP 302 Redirect auf `/mfa/backup-codes/`.
- DB: `TOTPDevice` für `miriam` mit `confirmed=True`.
- Session: `request.session["mfa_verified"] = True`, `request.session["mfa_backup_codes"]` befüllt mit 10 Codes.
- AuditLog: zwei neue Einträge — `MFA_ENABLED` (`detail.event="mfa_setup_confirmed"`) und `BACKUP_CODES_GENERATED` (`detail.count=10`).
- Backup-Codes werden auf `/mfa/backup-codes/` einmalig angezeigt; Reload zeigt nichts mehr (Session-Pop).
- Rate-Limit `10/min/User` auf `MFASetupView.post` (gegen Brute-Force des Setup-Tokens).

**DSGVO/Security-Note:**
- TOTP-Secret ist 160-bit per django-otp-Default; QR-Code via `qrcode`-Lib gerendert, kein externer Service.
- Backup-Codes ab Refs #790: 128 Bit Entropie, in DB SHA-256-Hash truncated auf 16 Hex (Pre-Image-Schutz).

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-02 — Login mit MFA: Username/Pwd + TOTP-Verify

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`) | C/F/S | ✓ | `test_mfa_setup_flow.py` |

**Voraussetzung:** ENT-MFA-01 erfolgreich; bestätigtes TOTPDevice für `miriam`.

**Vorbereitung:**
- Logout aus aktiver Session.
- Authenticator-App geöffnet, Code für „Anlaufstelle" sichtbar.

**Schritte:**
1. `/login/` → `miriam` + `anlaufstelle2026` → Submit.
2. Folgender 302 → `MFAEnforcementMiddleware` leitet auf `/mfa/verify/` (Session ist nicht mfa_verified).
3. Auf `/mfa/verify/`: aktuellen 6-stelligen TOTP-Code eingeben → Submit.
4. Versuch B (negativ): falschen Code eingeben.

**Erwartetes Ergebnis:**
- Schritt 3: HTTP 302 auf `LOGIN_REDIRECT_URL=/`. Session `mfa_verified=True`. Anwendung nutzbar.
- Schritt 4: HTTP 200, Form mit Fehlermeldung `"Der Code ist ungültig. Bitte erneut versuchen."`. AuditLog `MFA_FAILED` mit `detail.event="mfa_token_invalid"`, `detail.mode="totp"`.
- Rate-Limit `5/min/User` auf `MFAVerifyView.post`. Bei 6+ Versuchen → HTTP 429.
- Eingegebene Token werden mit `.strip.replace(" ", "")` normalisiert (Toleranz für Leerzeichen).

**DSGVO/Security-Note:**
- Refs #683: Session-Hijack-Schutz — auch mit gestohlenem Session-Cookie braucht Angreifer den TOTP-Code.
- AuditLog für jeden fehlgeschlagenen Verify-Versuch (Forensik).

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-03 — Backup-Codes generieren (10×, SHA-256-gehasht)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`) | C/F/S | ⚪ | `test_mfa_backup_codes.py` |

**Voraussetzung:** ENT-MFA-01; `miriam` hat MFA aktiv.

**Vorbereitung:**
- Login + MFA-Verify.
- DB-Check: `StaticDevice` mit `name="backup"` für `miriam`, `StaticToken.objects.filter(device=...).count` notieren.

**Schritte:**
1. Auf `/mfa/backup-codes/` (über Setup-Flow oder Regenerate-Action) — 10 Codes anzeigen lassen.
2. Code-Format prüfen: 22 Zeichen URL-safe Base64 (alphanumerisch + `-` + `_`, case-sensitive).
3. DB-Inspektion: `StaticToken.token`-Feld pro Code prüfen.

**Erwartetes Ergebnis:**
- UI zeigt **genau 10** Codes als Liste/Card, mit Druck-/Copy-Hint.
- Code-Format: `^[A-Za-z0-9_-]{22}$`, jedes Codes 128 Bit Entropie (`secrets.token_urlsafe(16)`).
- DB: Pro Code ein `StaticToken`-Eintrag; **`token`-Feld enthält nicht den Klartext**, sondern SHA-256-Hex-Digest, truncated auf 16 Hex-Zeichen (`hashlib.sha256(code).hexdigest[:16]`).
- Bestehender `StaticDevice` für `miriam` wird wiederverwendet, alte Tokens werden gelöscht (`device.token_set.all.delete`).
- AuditLog: `BACKUP_CODES_GENERATED` (Setup) bzw. `BACKUP_CODES_REGENERATED` (Regenerate) mit `detail.count=10`.

**DSGVO/Security-Note:**
- Refs #790 (C-22): DB-Leak != Backup-Code-Kompromittierung (Pre-Image-Angriff gegen 128-Bit-Eingabe = 2^64 Trial, infeasibel).
- Codes werden NUR EINMAL angezeigt — Anzeige-Seite konsumiert `request.session.pop("mfa_backup_codes")`.

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-04 — Login mit Backup-Code (One-Time)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`) | C/F/S | ⚪ | `test_mfa_backup_codes.py` |

**Voraussetzung:** ENT-MFA-03; mind. 1 ungenutzter Backup-Code notiert.

**Vorbereitung:**
- Logout.
- TOTP-App nicht erreichbar simuliert (mental: User hat Phone verloren).

**Schritte:**
1. `/login/` mit `miriam` + Pwd → Submit → Redirect auf `/mfa/verify/`.
2. Auf `/mfa/verify/` Modus auf „Backup-Code" wechseln (Form-Field `mode=backup`).
3. Backup-Code eingeben (case-sensitive, 22 Zeichen) → Submit.

**Erwartetes Ergebnis:**
- HTTP 302 auf `LOGIN_REDIRECT_URL=/`.
- Session `mfa_verified=True`.
- DB: `StaticToken` für genutzten Code wurde via `match.delete` entfernt → `remaining_backup_codes(miriam) == 9`.
- AuditLog: `BACKUP_CODES_USED` mit `detail.remaining=9`.
- `device.set_last_used_timestamp(commit=False)` setzt Timestamp; `throttle_reset` wird ausgeführt.

**DSGVO/Security-Note:**
- Single-use ist Pflicht — der Match-Eintrag wird sofort aus DB gelöscht.
- Throttle-Mixin (django-otp) verhindert Backup-Code-Brute-Force (1s Delay nach Miss).

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-05 — Backup-Code-Reuse-Verbot

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`) | C || `test_mfa_backup_codes.py` |

**Voraussetzung:** ENT-MFA-04; ein Code wurde bereits einmal genutzt und in DB gelöscht.

**Vorbereitung:**
- User ist nach ENT-MFA-04 eingeloggt.
- Logout, dann erneut Login bis `/mfa/verify/`.

**Schritte:**
1. `/login/` → Pwd → `/mfa/verify/`.
2. Modus „Backup-Code" wählen.
3. **Denselben** schon verwendeten Backup-Code aus ENT-MFA-04 eingeben → Submit.

**Erwartetes Ergebnis:**
- HTTP 200, Form mit Fehler `"Der Code ist ungültig. Bitte erneut versuchen."`.
- Session bleibt `mfa_verified=False`.
- AuditLog: neuer `MFA_FAILED`-Eintrag mit `detail.event="mfa_token_invalid"`, `detail.mode="backup"`.
- `device.throttle_increment` wurde aufgerufen — wiederholte Misses verzögern weitere Verifies (django-otp ThrottlingMixin).
- `verify_backup_code` returnt `False`, da `device.token_set.filter(token__in=[hashed, token]).first` kein Match findet (Token wurde gelöscht).

**DSGVO/Security-Note:**
- Single-use ist Pflicht: Refs #790 — Match in EINER Query gegen Hash UND Cleartext (Legacy), aber Treffer wird direkt deleted.

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-06 — Backup-Codes regenerieren (alte ungültig)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`) | C/F/S | ⚪ | `test_mfa_backup_codes.py` |

**Voraussetzung:** ENT-MFA-03; bestehende Backup-Codes gespeichert (mind. 1 noch nicht verbraucht, notiert).

**Vorbereitung:**
- Login + MFA-Verify.
- AuditLog vor Test prüfen.
- Aktuellen TOTP-Code aus App parat haben.

**Schritte:**
1. `/mfa/settings/` aufrufen → Button „Codes neu erzeugen" oder Direkt-POST auf `/mfa/backup-codes/regenerate/`.
2. Im Regenerate-Form aktuellen TOTP-Code eingeben → Submit.
3. Auf `/mfa/backup-codes/` werden 10 **neue** Codes einmalig angezeigt.
4. Logout, dann neuer Login → `/mfa/verify/` → Modus „Backup-Code" → einen **alten** Code aus Schritt-Vor-Test eingeben.

**Erwartetes Ergebnis:**
- Schritt 2 (negativ ohne TOTP): „TOTP-Code fehlt oder ist ungültig.", Redirect auf `/mfa/settings/`, keine Regen.
- Schritt 2 (positiv): Redirect `/mfa/backup-codes/`. AuditLog: `BACKUP_CODES_REGENERATED` mit `detail.count=10`.
- Alle vorherigen `StaticToken`-Einträge sind gelöscht; 10 neue mit SHA-256-Hashes vorhanden.
- Schritt 4: alter Code wird **nicht** akzeptiert (HTTP 200 Form-Fehler, `MFA_FAILED`-Audit).
- Rate-Limit `5/min/User` auf `MFARegenerateBackupCodesView.post`.

**DSGVO/Security-Note:**
- Aktueller TOTP wird vor Regen geprüft (Defense-in-Depth gegen gestohlene Session).
- `generate_backup_codes` läuft `@transaction.atomic` — partielle Replacements unmöglich.

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-07 — MFA deaktivieren (mit Sudo-Mode)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`) | C/F/S | ⚪ | `test_mfa_setup_flow.py` |

**Voraussetzung:** ENT-MFA-01; `is_mfa_enforced=False` (kein User-Flag, kein facility-weiter Zwang).

**Vorbereitung:**
- Login + MFA-Verify.
- Sudo-Mode ist NICHT aktiv (Default nach Login, `sudo_until` fehlt in Session).

**Schritte:**
1. `/mfa/settings/` öffnen.
2. Button „2FA deaktivieren" klicken → POST auf `/mfa/disable/`.
3. Erste Reaktion: `RequireSudoModeMixin` redirected auf `/sudo/?next=/mfa/disable/`.
4. Auf `/sudo/` Pwd `anlaufstelle2026` eingeben → Submit.
5. Redirect zurück auf `/mfa/disable/` (POST).
6. MFA-Disable-Action wird ausgeführt.
7. Versuch B: User mit `is_mfa_enforced=True` versucht Disable.

**Erwartetes Ergebnis:**
- Schritt 6: `TOTPDevice.objects.filter(user=miriam).delete` löscht alle Devices, `request.session.pop("mfa_verified")`. Redirect `/mfa/settings/`. Status zeigt „2FA nicht aktiv". Success-Message: „Zwei-Faktor-Authentifizierung deaktiviert."
- AuditLog: `MFA_DISABLED` mit `detail.event="mfa_disabled"`.
- Rate-Limit `RATELIMIT_MUTATION` (Default: z.B. 30/h) auf `MFADisableView.post`.
- Versuch B: Error-Message „Zwei-Faktor-Authentifizierung ist für dein Konto verpflichtend.", Redirect auf `/mfa/settings/`, Devices unverändert. Kein AuditLog `MFA_DISABLED`.

**DSGVO/Security-Note:**
- `RequireSudoModeMixin` (Refs #683) erzwingt Re-Auth — gestohlene Session reicht nicht.
- Backup-Codes (`StaticDevice`) bleiben in DB — werden beim nächsten MFA-Setup überschrieben.

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-08 — `mfa_required=True` erzwingen (Setup-Redirect)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | leitung (`thomas`) | C || `test_mfa_setup_flow.py` |

**Voraussetzung:** `thomas` (LEAD), `mfa_required=True` per Admin-UI gesetzt; bisher kein TOTPDevice.

**Vorbereitung:**
- Admin setzt `User.mfa_required=True` für `thomas`.
- DB: `TOTPDevice.objects.filter(user=thomas, confirmed=True).exists == False`.

**Schritte:**
1. `/login/` mit `thomas` + Pwd → Submit.
2. `/clients/` direkt aufrufen.

**Erwartetes Ergebnis:**
- Schritt 1: Login OK (HTTP 302).
- Schritt 2: `MFAEnforcementMiddleware` greift (`is_mfa_enforced=True`, kein Device) → 302 Redirect auf `/mfa/setup/`.
- Solange `thomas` kein bestätigtes Device hat, sind alle Routes außer EXEMPT_URLS (`/login/`, `/logout/`, `/mfa/`, `/static/`, `/i18n/`, `/health/`, `/sw.js`, `/manifest.json`, `/auth/offline-key-salt/`, `/password-change/`, `/password-reset/`) gesperrt.
- Nach erfolgreichem Setup (analog ENT-MFA-01) wird der reguläre Flow fortgesetzt.

**DSGVO/Security-Note:**
- Property `User.is_mfa_enforced` aggregiert User- und Facility-Flags (`mfa_required=True ODER facility.settings.mfa_enforced_facility_wide=True`).

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-09 — `mfa_enforced_facility_wide=True` (alle User der Facility)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`), assistenz (`lena`), leitung (`thomas`) | C || `test_mfa_setup_flow.py` |

**Voraussetzung:** Facility-Settings `mfa_enforced_facility_wide=True` (über Admin-UI oder Shell gesetzt). Keiner der drei Test-User hat ein Device.

**Vorbereitung:**
- `facility.settings.mfa_enforced_facility_wide = True; facility.settings.save`.
- Alle User der Facility ohne TOTPDevice.

**Schritte:**
1. Login als `miriam` → Pwd → versucht `/clients/`.
2. Login als `lena` → Pwd → versucht `/`.
3. Login als `thomas` → Pwd → versucht `/cases/`.

**Erwartetes Ergebnis:**
- Bei allen drei: `MFAEnforcementMiddleware` redirected auf `/mfa/setup/` (Case 1 in `_required_redirect`).
- `is_mfa_enforced` kommt durch `facility.settings.mfa_enforced_facility_wide=True` zustande, auch wenn `User.mfa_required=False`.
- Ohne Setup keine App-Nutzung außer EXEMPT_URLS.
- Nach Setup landet jeder User regulär im Zielsystem.

**DSGVO/Security-Note:**
- Facility-weite Erzwingung ist Admin-Werkzeug für Care-Provider mit erhöhtem Schutzbedarf (z.B. Suchthilfe + Art. 9 DSGVO).
- Audit aller Setups: `MFA_ENABLED`-Eintrag pro User.

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>👤 ACCT — Account & Profil (5 Cases)</strong></summary>

**Routen:** `/account/`, `/i18n/setlang/`, `/auth/offline-key-salt/` 
**Views:** `src/core/views/account.py` (`AccountProfileView`), `src/core/views/auth.py` (`OfflineKeySaltView`, `set_user_language`) 
**Mixin:** `AssistantOrAboveRequiredMixin` (Account ist für Assistenz und höher) 
**Services:** `src/core/services/offline_keys.py` (`ensure_offline_key_salt`) 
**Modelfelder:** `User.preferred_language`, `User.phone`, `User.notes`, `User.offline_key_salt` 
**E2E-Coverage:** `test_account_profile.py` 
**Spezial-Setup:**
- Profil-Edit ist (Stand: aktueller Code) READ-ONLY-Dashboard mit Stats-Widget; Stamm-Pflege (Telefon/Notes) läuft über die Admin-UI (`/admin-mgmt/`) — TC-ACCT-03 entsprechend.
- Sprache wird beim Setlang-Endpoint sowohl in Session/Cookie (Django-Default) ALS AUCH auf `User.preferred_language` persistiert.
- Salt-Endpoint ist POST-only (kein GET), damit der Aufruf nicht in der Browser-Historie landet.

---

### TC-ID: ENT-ACCT-01 — Profil-Detail anzeigen (Username, E-Mail, Rolle, Facility)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Acct | fachkraft (`miriam`) | C/F/S | ✓ | `test_account_profile.py` |

**Voraussetzung:** eingeloggt, MFA-Status egal (für Profil keine Sondergates).

**Vorbereitung:**
- Login als `miriam`.
- AuditLog `LOGIN`-Eintrag erzeugt heute (für recent_events-Widget irrelevant, aber Datenbasis).
- Mind. 1 WorkItem assigned_to=`miriam` (aus Seed).

**Schritte:**
1. `/account/` aufrufen.
2. Profilbereich (Username, Display-Name, E-Mail, Rolle, Facility) prüfen.
3. Stats-Widget prüfen (`events_today`, `open_cases`, `my_open_tasks`, `total_open_tasks`).
4. „Zuletzt besucht"-Liste prüfen (RecentClientVisit, max 8).
5. „Letzte Ereignisse"-Liste (Created_by=user, max 10).
6. Offene Aufgaben + abgeschlossene (max 10 / 5).

**Erwartetes Ergebnis:**
- HTTP 200, Template `core/account/profile.html` rendert.
- Sichtbar: `miriam`, `Miriam Schmidt`, Rolle „Fachkraft", Facility-Name aus Seed.
- Stats-Counts entsprechen DB-Wahrheit (Filter auf `current_facility` und sichtbare Events `Event.objects.visible_to(user)`).
- Querysets sind `current_facility`-gescoped (Multi-Facility-Sicherheit).
- `AssistantOrAboveRequiredMixin` blockiert nichts für `miriam` (Rolle STAFF >= ASSISTANT).

**DSGVO/Security-Note:**
- Read-Only-Profilseite — keine Self-Service-Pflege sensitiver Felder hier (siehe ENT-ACCT-03).

**Status:** ☐ Offen

---

### TC-ID: ENT-ACCT-02 — Sprache wechseln DE↔EN (Session-Cookie + User-Pref)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Acct | fachkraft (`miriam`) | C/F/S | ⚪ | `test_account_profile.py` |

**Voraussetzung:** eingeloggt; Default-Sprache `de`.

**Vorbereitung:**
- Login als `miriam`, `User.preferred_language="de"`.
- Locale-Files `de` und `en` kompiliert (`make compile-messages`).

**Schritte:**
1. Im Header/Footer Sprachumschalter klicken (Form POST `/i18n/setlang/` mit `language=en`).
2. Beliebige Seite wie `/clients/` neu laden — UI-Strings auf Englisch.
3. DB: `User.preferred_language` lesen.
4. Logout, Re-Login.
5. Erneut `/clients/` aufrufen.

**Erwartetes Ergebnis:**
- Schritt 1: HTTP 302 (Django `set_language`-Default zu `next` oder `Referer`); Session-Sprach-Cookie (`django_language`) wird gesetzt.
- Schritt 2: UI auf Englisch (z.B. „Clients" statt „Klient*innen").
- Schritt 3: `User.preferred_language=="en"` (durch `User.objects.filter(pk=user.pk).update(preferred_language=language)` in `set_user_language`).
- Schritt 5: UI bleibt auf Englisch — Persistenz auf User-Modell wirkt nach Re-Login (UserLanguageMiddleware liest die User-Pref).
- Validierung: nur Sprachcodes aus `settings.LANGUAGES` (`de`, `en`) werden akzeptiert; `language=fr` wird ignoriert.

**DSGVO/Security-Note:**
- POST-only-Endpoint via `@require_POST` — keine CSRF-Bypass-Möglichkeit über GET.

**Status:** ☐ Offen

---

### TC-ID: ENT-ACCT-03 — Telefon-Nummer + Notes pflegen (Admin-UI)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Acct | admin → Pflege für `miriam` | C/F/S | ⚪ | — (manuell, Admin-UI) |

**Voraussetzung:** admin eingeloggt mit MFA-Verify (Admin braucht MFA-Pflicht in Prod).

**Vorbereitung:**
- Login als `admin`, ggf. MFA-Setup.
- Admin-UI `/admin-mgmt/` zugänglich.

**Schritte:**
1. `/admin-mgmt/core/user/` öffnen.
2. `miriam` auswählen.
3. Felder `phone` (z.B. `+49 30 1234567`) und `notes` (z.B. „Mo-Mi vor Ort, Do remote") befüllen → Save.
4. `/account/` als `miriam` aufrufen — Werte angezeigt? (UI-Komponente prüft, ob Phone/Notes überhaupt im Profil-Template sichtbar sind.)

**Erwartetes Ergebnis:**
- DB: `miriam.phone == "+49 30 1234567"`, `miriam.notes == "Mo-Mi vor Ort, Do remote"`.
- Audit-Trail: Standard-Django-Admin `LogEntry` (NICHT eigener AuditLog — nur `User_role_changed` etc. ist gehookt).
- Felder sind als CharField/TextField persistiert (siehe `User.phone`, `User.notes` in `src/core/models/user.py`).
- Im Profil ggf. nur lesend angezeigt (Self-Service-Edit nicht implementiert).

**DSGVO/Security-Note:**
- Phone/Notes sind Stammdaten — RLS (Refs ADR-001) sichert ohnehin facility-Scoping.
- Kein Self-Service: bewusste Org-Entscheidung, Mitarbeiter-Stammdaten via Leitung pflegen lassen.

**Status:** ☐ Offen

---

### TC-ID: ENT-ACCT-04 — Offline-Key-Salt-Endpoint (Rate-Limit 10/min/User)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Acct | fachkraft (`miriam`) | C || `test_account_profile.py` |

**Voraussetzung:** eingeloggt.

**Vorbereitung:**
- DB: `User.offline_key_salt` für `miriam` ist leer (Initial-Zustand).
- Cache leer (für Rate-Limit-Reset).

**Schritte:**
1. POST auf `/auth/offline-key-salt/` (mit CSRF-Token, ohne Body) durchführen — z.B. via Frontend-Bootstrap nach Login.
2. JSON-Response inspizieren: `{"salt": "<base64url>"}`.
3. Format prüfen: 16-Byte Base64URL ohne Padding.
4. Endpoint sofort nochmal aufrufen → Salt soll **identisch** sein (Lazy-Persist, keine Rotation).
5. 10× weitere POSTs in <1 min → 11. Versuch sollte HTTP 429 liefern.
6. AuditLog `OFFLINE_KEY_FETCH` prüfen.
7. GET-Versuch auf gleicher URL.

**Erwartetes Ergebnis:**
- Schritt 2: HTTP 200, JSON-Body, `salt`-Feld 22 Zeichen lang (16 Bytes Base64URL ohne Padding, vom `ensure_offline_key_salt`-Service).
- Schritt 3: `User.offline_key_salt` ist persistiert (DB).
- Schritt 4: gleicher Salt-Wert (kein Re-Generate).
- Schritt 5: 11. POST → HTTP 429 (Rate-Limit `10/m/User`, `block=True`, in `OfflineKeySaltView.post`).
- Schritt 6: pro Aufruf (auch innerhalb Rate-Limit) ein AuditLog `OFFLINE_KEY_FETCH` mit `target_obj=user`, `facility`, `detail.event="offline_key_salt_fetched"`.
- Schritt 7: HTTP 405 Method Not Allowed (View ist POST-only).

**DSGVO/Security-Note:**
- POST-only verhindert Eintrag in Browser-History.
- Salt ist 128 Bit (`secrets.token_bytes(16)`) — Brute-Force-resistente PBKDF2-Eingabe.
- AuditLog-Stream zeigt Mass-Fetches an (Forensik bei kompromittierter Session).

**Status:** ☐ Offen

---

### TC-ID: ENT-ACCT-05 — Offline-Key-Salt nach Pwd-Wechsel rotieren

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Acct | fachkraft (`miriam`) | C || `test_account_profile.py` |

**Voraussetzung:** ENT-ACCT-04 lief; `miriam.offline_key_salt` enthält einen Wert.

**Vorbereitung:**
- Aktuellen Salt-Wert (Salt_alt) notieren (DB-Read oder weitere POST-Antwort vor dem Pwd-Wechsel).

**Schritte:**
1. `/password-change/` aufrufen.
2. Altes Pwd + neues Pwd setzen (siehe ENT-AUTH-08 für Validatoren).
3. Submit → Redirect `/`.
4. Erneut POST `/auth/offline-key-salt/` durchführen.
5. Neues Salt mit Salt_alt vergleichen.

**Erwartetes Ergebnis:**
- Nach Schritt 3: `User.offline_key_salt = ""` (`CustomPasswordChangeView.form_valid` setzt explizit leer; `update_fields=["must_change_password", "offline_key_salt"]`).
- Schritt 4: `ensure_offline_key_salt(user)` generiert neuen 16-Byte-Salt → DB-Persist.
- Schritt 5: Salt_neu ≠ Salt_alt (mit überwältigender Wahrscheinlichkeit; kollisionsfrei für 128-Bit-Token).
- Alte client-seitige IndexedDB-Records werden beim nächsten Login als `garbage` verworfen — Frontend re-derived Schlüssel mit neuem Salt+Pwd.
- AuditLog: weiterer `OFFLINE_KEY_FETCH`-Eintrag.

**DSGVO/Security-Note:**
- Pwd-Wechsel rotiert auch Offline-Schlüssel — Schlüsselmaterial im Browser ist nicht mehr nutzbar, falls jemand dazwischen das alte Pwd erlangt hat (Refs `CustomPasswordChangeView.form_valid`-Kommentar).

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>🛡️ SUDO — Re-Authentication (4 Cases)</strong></summary>

**Routen:** `/sudo/` 
**Views:** `src/core/views/sudo_mode.py` (`SudoModeView`) 
**Services:** `src/core/services/sudo_mode.py` (`enter_sudo`, `is_in_sudo`, `clear_sudo`, `RequireSudoModeMixin`) 
**Settings:** `SUDO_MODE_ENABLED=True` (Prod/Dev), `SUDO_MODE_TTL_SECONDS=900` (15 Min) 
**Genutzt von:** `MFADisableView` (LoginRequired+RequireSudoMode); ggf. DSGVO-Export, Pseudonym-Daten-Download (Refs #683) 
**E2E-Coverage:** `test_security_hardening.py` (indirekt über MFA-Disable-Flow), Unit-Tests in `src/tests/test_sudo_mode.py` 
**Spezial-Setup:**
- In Tests deaktiviert: `settings/test.py` setzt `SUDO_MODE_ENABLED=False`. Für E2E-Tests bleibt es aktiv.
- AuditAction `SUDO_MODE_ENTERED` existiert; `SUDO_MODE_EXIT` existiert **nicht** im Code (kein expliziter Exit-Audit beim Logout — `clear_sudo` ist still). Entsprechend testen wir nur `SUDO_MODE_ENTERED`.

---

### TC-ID: ENT-SUDO-01 — Sudo-Mode betreten via Re-Auth

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sudo | fachkraft (`miriam`) | C/F/S | ⚪ | `test_security_hardening.py` |

**Voraussetzung:** eingeloggt mit MFA-Verify.

**Vorbereitung:**
- Login als `miriam`, MFA verifiziert.
- DB: `request.session.get("sudo_until")` ist `None` (frische Session).
- Sudo-pflichtige Aktion vorbereitet (z.B. „2FA deaktivieren" auf `/mfa/settings/`).

**Schritte:**
1. Auf `/mfa/settings/` „2FA deaktivieren" klicken → POST `/mfa/disable/`.
2. `RequireSudoModeMixin.dispatch` greift, da `is_in_sudo(request)==False` → 302 auf `/sudo/?next=/mfa/disable/`.
3. Auf `/sudo/`-Form Pwd `anlaufstelle2026` eingeben → Submit (POST).
4. Session-State und Redirect prüfen.

**Erwartetes Ergebnis:**
- Schritt 3: `authenticate(request, username="miriam", password="anlaufstelle2026")` returnt User; `enter_sudo(request)` setzt `session["sudo_until"] = int(time.time) + 900`.
- AuditLog: `SUDO_MODE_ENTERED` mit `user=miriam`, `target_type="User"`, `target_id=miriam.pk`, `detail.next="/mfa/disable/"`.
- HTTP 302 Redirect auf `safe_redirect_path("/mfa/disable/")`.
- Folge-POST auf `/mfa/disable/` ist jetzt erlaubt (`is_in_sudo==True`).
- Falsches Pwd → HTTP 403 mit gerendertem `auth/sudo_mode.html` und Error-Message „Passwort ist nicht korrekt.", **kein** `enter_sudo`-Aufruf.

**DSGVO/Security-Note:**
- Schutz gegen Session-Hijack (Refs #683): gestohlenes Cookie reicht nicht ohne aktuelles Pwd.
- `safe_redirect_path` blockiert offene Redirects auf externe URLs.
- Rate-Limit `5/m/User` auf `SudoModeView.post` (Brute-Force-Schutz).

**Status:** ☐ Offen

---

### TC-ID: ENT-SUDO-02 — Sudo-Timeout nach 15 Min → Re-Auth nötig

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sudo | fachkraft (`miriam`) | C || `test_security_hardening.py` |

**Voraussetzung:** ENT-SUDO-01; aktive Session mit Sudo-Mode.

**Vorbereitung:**
- Test-Setting `SUDO_MODE_TTL_SECONDS=2` (für schnellen Test) ODER `session["sudo_until"]` in der Session-DB manipulieren auf `now-1`.
- Sudo-pflichtige Aktion bereit.

**Schritte:**
1. Direkt nach Sudo-Entry erste sudo-pflichtige Aktion ausführen → erfolgreich.
2. 16 Min warten (oder mit gemockter `time.time` / Setting).
3. Erneut sudo-pflichtige Aktion versuchen.

**Erwartetes Ergebnis:**
- Schritt 1: erfolgreich (Sudo-Window noch offen).
- Schritt 3: `is_in_sudo(request)` returnt `False`, da `time.time >= session["sudo_until"]`.
- 302 Redirect auf `/sudo/?next=<originalpfad>` — User muss erneut sein Pwd eingeben.
- Nach erneuter Re-Auth: AuditLog hat einen ZWEITEN `SUDO_MODE_ENTERED`-Eintrag.
- Setting `SUDO_MODE_TTL_SECONDS` aus `os.environ.get("SUDO_MODE_TTL_SECONDS", "900")`.

**DSGVO/Security-Note:**
- 15-Min-Fenster begrenzt Angriffsfläche bei kurzzeitiger Pwd-Kompromittierung.

**Status:** ☐ Offen

---

### TC-ID: ENT-SUDO-03 — Sudo-pflichtige View ohne Sudo → Redirect /sudo/

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sudo | fachkraft (`miriam`) | C/F/S | ⚪ | `test_security_hardening.py` |

**Voraussetzung:** eingeloggt; `session["sudo_until"]` nicht gesetzt.

**Vorbereitung:**
- Login + MFA-Verify, kein Sudo-Mode.
- Sudo-Required-View ist `MFADisableView`.

**Schritte:**
1. Direkt POST/GET auf `/mfa/disable/` (z.B. via curl mit Session-Cookie oder Frontend-Klick).
2. Response inspizieren.
3. Auf `/sudo/?next=/mfa/disable/` landen, Form sehen.

**Erwartetes Ergebnis:**
- HTTP 302 Redirect: `Location: /sudo/?next=/mfa/disable/`.
- Hinweis: laut Code-Kommentar zur Aufgabe „403 + Redirect zu /sudo/" — der Code im `RequireSudoModeMixin.dispatch` macht **`return redirect(sudo_url)`** (302), keinen 403. Dieser Test-Case erwartet **302**, nicht 403 — damit bestätigen wir das tatsächliche Verhalten.
- `auth/sudo_mode.html` rendert mit Pwd-Form.
- Erst nach erfolgreicher Re-Auth (siehe ENT-SUDO-01) ist `/mfa/disable/` erreichbar.

**DSGVO/Security-Note:**
- Verhalten stimmt mit Service-Doc überein (`src/core/services/sudo_mode.py`): „Mixin RequireSudoModeMixin redirected zu /sudo/ mit ?next=".

**Status:** ☐ Offen

---

### TC-ID: ENT-SUDO-04 — AuditLog SUDO_MODE_ENTERED nach Re-Auth

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sudo | fachkraft (`miriam`) | C || `test_security_hardening.py` |

**Voraussetzung:** ENT-SUDO-01 lief.

**Vorbereitung:**
- AuditLog-Filter `action=SUDO_MODE_ENTERED` für `miriam` vor und nach Test bereitstellen.

**Schritte:**
1. Nach erfolgreicher Re-Auth (Schritt 3 in ENT-SUDO-01) AuditLog inspizieren.
2. Abfrage: `AuditLog.objects.filter(user=miriam, action="sudo_mode_entered").latest("timestamp")`.
3. (Negativ-Test) Logout durchführen — prüfen, dass **kein** SUDO_MODE_EXIT-Audit-Action existiert (die Action ist im Code nicht definiert).

**Erwartetes Ergebnis:**
- Schritt 2: ein neuer Eintrag mit:
 - `action="sudo_mode_entered"`
 - `user=miriam`
 - `facility=miriam.facility`
 - `target_type="User"`, `target_id=str(miriam.pk)`
 - `detail={"next": "<safe redirect path>"}`
 - `timestamp` ~ jetzt
- Schritt 3: `AuditLog.Action`-TextChoices enthält `SUDO_MODE_ENTERED`, **NICHT** `SUDO_MODE_EXIT`. Das ist eine bewusste Aktualisierung der Aufgabenstellung — der Code-Status ist Truth Source. `clear_sudo(request)` läuft still (z.B. beim Logout via Session-Pop), erzeugt **keinen** dedizierten Audit-Eintrag.

**DSGVO/Security-Note:**
- AuditLog-Eintrag ermöglicht Forensik bei kompromittierten Sessions: wann hat der User Sudo-Fenster geöffnet?
- Kein Exit-Audit ist OK — Sudo-Fenster ist zeitbeschränkt; Logout/Session-End räumt implizit auf.

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>📲 PWA — Progressive Web App (5 Cases)</strong></summary>

**Routen:** `/sw.js`, `/manifest.json`, `/offline/` 
**Views:** `src/core/views/pwa.py` (`ServiceWorkerView`, `ManifestView`, `OfflineFallbackView`) 
**Statisch:** `src/static/manifest.json`, `src/static/js/sw.js`, `src/templates/offline.html` 
**Cache-Versionierung:** `CACHE_NAME = "anlaufstelle-v9"` in `sw.js` 
**App-Shell:** `/static/css/styles.css`, Icons (192/512 PNG+SVG), `/offline/` 
**Strategien (laut sw.js):**
- Static-Assets: stale-while-revalidate (Refs #618)
- HTML/HTMX-Navigation: network-first → Cache-Fallback → `/offline/`-Template (Refs #701)
- POST/PUT auf URL_PATTERNS.QUEUE_PATTERNS bei Netzausfall: IndexedDB-Queue via `requestQueueAck` (Refs #573, #662)
- Multipart-POST: kein Queue, sofortiger 503 mit „Datei-Upload erfordert Internetverbindung"
- Attachment-/Export-Downloads: network-only (Refs #751)
**E2E-Coverage:** `test_pwa_offline.py` 
**Spezial-Setup:**
- Service-Worker registriert sich nur über HTTPS (oder localhost). E2E-Server auf `https://localhost:8844/` ist OK.
- Manifest muss von `/manifest.json` (Root-Scope!) geliefert werden, nicht aus `/static/`. Begründung im View-Docstring: Android Chrome akzeptiert sonst Scope `/` nicht.
- Service-Worker-Header: `Service-Worker-Allowed: /` für Root-Scope.

---

### TC-ID: ENT-PWA-01 — Manifest.json (gültiges JSON, name + icons)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Pwa | unauthentifiziert / beliebig | C/S | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** Statische Files vorhanden (`make collectstatic` für Prod, in dev/e2e direkt aus `src/static/`).

**Vorbereitung:**
- Server läuft auf `https://localhost:8844/`.

**Schritte:**
1. `https://localhost:8844/manifest.json` aufrufen (auch ohne Login, da MFA-EXEMPT).
2. Response-Header prüfen: `Content-Type: application/manifest+json`.
3. Body als JSON parsen.
4. Felder validieren: `name`, `short_name`, `id`, `start_url`, `scope`, `display`, `background_color`, `theme_color`, `icons`.
5. Icons-Liste: 4 Einträge (192/512 PNG, 192/512 SVG), `purpose=any` bzw. `any maskable`.

**Erwartetes Ergebnis:**
- HTTP 200, MIME `application/manifest+json`.
- Body parst als JSON ohne Fehler.
- Pflichtfelder vorhanden:
 - `"name": "Anlaufstelle"`, `"short_name": "Anlaufstelle"`
 - `"id": "/"`, `"start_url": "/"`, `"scope": "/"`
 - `"display": "standalone"`
 - `"background_color": "#f9fafb"`, `"theme_color": "#4f46e5"`
 - `"icons"` mit ≥ 4 Einträgen, mind. ein 192×192 und ein 512×512 PNG.
- Datei wird mit `lru_cache(maxsize=1)` aus dem Filesystem gelesen — Performance-Test: 2. Aufruf ohne Disk-I/O.

**DSGVO/Security-Note:**
- Manifest enthält keine PII; rein statische Konfiguration.
- Scope `/` setzt voraus, dass Manifest auf Root-Pfad ausgeliefert wird (Android Chrome strict).

**Status:** ☐ Offen

---

### TC-ID: ENT-PWA-02 — Service-Worker Registrierung + Cache-Strategie

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Pwa | beliebig | C/F/S | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** HTTPS; Browser unterstützt SW.

**Vorbereitung:**
- Privates Fenster, DevTools → Application → Service Workers offen.

**Schritte:**
1. Beliebige App-Seite aufrufen (z.B. `/login/`).
2. SW-Registrierung in Application-Tab beobachten.
3. Direkt `https://localhost:8844/sw.js` aufrufen → Header prüfen.
4. Application → Cache Storage → `anlaufstelle-v9` öffnen, App-Shell-URLs prüfen.
5. Reload-Test: zweimal Static-Asset (z.B. `/static/css/styles.css`) abrufen — Network-Tab beobachten (stale-while-revalidate).

**Erwartetes Ergebnis:**
- Schritt 2: SW ist `activated and is running`, Scope `/`.
- Schritt 3: HTTP 200, `Content-Type: application/javascript`, `Service-Worker-Allowed: /`.
- Schritt 4: Cache `anlaufstelle-v9` enthält App-Shell aus `APP_SHELL`:
 - `/static/css/styles.css`
 - `/static/icons/icon-192.png`, `/static/icons/icon-512.png`
 - `/static/icons/icon-192.svg`, `/static/icons/icon-512.svg`
 - `/offline/`
- Schritt 5: zweiter Reload servert aus Cache (Cache-Hit), gleichzeitig Hintergrund-Fetch der neuen Version → Cache-Update (Refs #618).
- Bei `caches.keys`-Wechsel werden alte CACHE_NAMEs (v8 etc.) im `activate`-Event gelöscht.

**DSGVO/Security-Note:**
- SW-Scope-Restriction (Service-Worker-Allowed) verhindert, dass aus `/static/`-Pfaden Root-Skripte registriert werden.
- App-Shell enthält keine PII; verschlüsselte Klienten-Daten leben in IndexedDB (separate Store), nicht im SW-Cache.

**Status:** ☐ Offen

---

### TC-ID: ENT-PWA-03 — Install-Prompt auf Chromium (Add to Home Screen)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Pwa | beliebig | C | ✓ | — (manuell) |

**Voraussetzung:** HTTPS (kein self-signed Cert in echtem Test, sonst Chromium verweigert PWA-Install — für E2E auf `https://localhost:8844/` ist Chromium toleranter, aber `beforeinstallprompt` triggert nicht zwingend; für formal vollständigen Test reale Domain mit Cert nötig).

**Vorbereitung:**
- Chromium oder Edge mit aktivem User-Engagement (mehrere Visits + 30s aktiv).
- DevTools → Application → Manifest prüfen: keine Errors.

**Schritte:**
1. App `/` öffnen, einige Sekunden interagieren.
2. Chrome-Menü → „App installieren" / Install-Icon in Adressleiste.
3. Bei mobilem Chrome: 3-Punkt-Menü → „Zum Startbildschirm hinzufügen".
4. Bestätigen.
5. App-Icon auf Home-Screen / im Launcher verifizieren.
6. Vom Icon starten — App öffnet im standalone-Modus (kein Browser-Chrome).

**Erwartetes Ergebnis:**
- Manifest-Validierung in DevTools zeigt keine Errors („App is installable").
- Install-Prompt erscheint (Chromium-Heuristiken erfüllt: Manifest valid, SW registriert, HTTPS, Engagement).
- Installierte App startet mit `display=standalone`, Theme-Color `#4f46e5`, Background `#f9fafb`.
- Start-URL `/` wird geöffnet.

**DSGVO/Security-Note:**
- Keine Install-Side-Effects, die PII speichern. App-Daten leben weiter in browserseitiger IndexedDB (Origin-gebunden) — DSGVO-Datenminimierung auf Logout via Clear-Site-Data (siehe ENT-AUTH-06).

**Status:** ☐ Offen

---

### TC-ID: ENT-PWA-04 — Offline-Page-Fallback

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Pwa | fachkraft (`miriam`) | C/F/S | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** SW registriert + aktiviert (siehe ENT-PWA-02); `/offline/` ist im App-Shell-Cache.

**Vorbereitung:**
- Login als `miriam`.
- DevTools → Application → Service Workers: „Offline" aktivieren ODER Network → Throttling → „Offline".
- Mind. einmal `/clients/` und `/cases/` online aufgerufen, damit der Cache-Fallback befüllt ist (Default-HTML-Cache greift gleich, wenn vorhanden).

**Schritte:**
1. DevTools auf „Offline" setzen.
2. Eine **bisher nicht** besuchte Route aufrufen, z.B. `/zeitstrom/?filter=foo`.
3. Eine bekannte HTML-Seite aufrufen (z.B. `/clients/`).
4. Klienten-Detail-URL aufrufen, die ohnehin Offline-Variante hat: `/clients/<pk>/`.
5. `/offline/` direkt aufrufen.

**Erwartetes Ergebnis:**
- Schritt 2: SW-Fetch-Listener probiert Netz (fail) → versucht Cache-Match (Miss) → liefert `OFFLINE_FALLBACK_URL` (`/offline/`) als Fallback. Response: HTTP 200 mit Inline-CSS-Offline-Page.
- Schritt 3: Cache-Match-Hit für `/clients/` → bekannte Seite (möglicherweise stale, aber sichtbar).
- Schritt 4: SW erkennt via `URL_PATTERNS.extractClientPk` einen Klienten-Pfad und redirected (`Response.redirect("/offline/clients/<pk>/", 302)`) zur dedizierten Offline-Klienten-Ansicht (rendert aus IndexedDB).
- Schritt 5: HTTP 200, Content-Type `text/html; charset=utf-8`, Body aus `render_to_string("offline.html")`.

**DSGVO/Security-Note:**
- Inline-CSS in `offline.html` notwendig, da SW im Offline-Fall nicht auf Static-Assets-Pipeline zugreifen kann (Refs #701).
- Offline-Klienten-Viewer rendert nur was lokal verschlüsselt vorliegt — kein Server-Round-Trip → keine ungewollte Re-Connect-Lecks.

**Status:** ☐ Offen

---

### TC-ID: ENT-PWA-05 — Update-Flow: neue SW-Version → User-Hinweis + Reload

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Pwa | beliebig | C/S | ⚪ | — (manuell, schwer zu automatisieren) |

**Voraussetzung:** bisheriger SW mit `CACHE_NAME="anlaufstelle-v9"` aktiv; Code-Bump auf `v10` durchgeführt (oder Mock).

**Vorbereitung:**
- Initial-Setup: laden, SW aktivieren, App-Shell-Cache `anlaufstelle-v9` aufbauen.
- Server auf neue Version deployen, in der `CACHE_NAME` auf `anlaufstelle-v10` erhöht ist (Pre-Test: in `sw.js` editieren, Server reloaden).

**Schritte:**
1. App neu laden (Hard-Reload sind nicht nötig; Browser prüft SW automatisch).
2. DevTools → Application → Service Workers: „waiting to activate"-Status für die neue SW-Version sehen.
3. „skipWaiting"-Verhalten: laut Code ruft `install`-Handler `self.skipWaiting` auf — alte SW wird sofort durch neue ersetzt.
4. `activate`-Handler löscht alte Caches via `caches.keys … filter(k !== CACHE_NAME).delete`.
5. App reloaden — beobachte Cache-Liste in DevTools (nur `anlaufstelle-v10` übrig).
6. Static-Assets werden bei Bedarf via stale-while-revalidate aus dem neuen Cache geliefert.

**Erwartetes Ergebnis:**
- Neue SW installiert + aktiviert sich automatisch (Refs `self.skipWaiting` + `self.clients.claim` im Code).
- Alte Cache-Stores (`anlaufstelle-v9`, ältere) sind entfernt.
- Nutzer-sichtbar: keine UI-Banner-Implementierung im aktuellen Code (Update-Toast wäre Custom-JS) — Reload reicht.
- Anmerkung: Aktuell **kein expliziter** „Update verfügbar"-Banner im Frontend — wenn die Aufgabe einen User-Hinweis fordert, ist das ein offener Punkt (manuell verifizierbar: kein Banner sichtbar, aber Update funktioniert silent).

**DSGVO/Security-Note:**
- Stale-while-revalidate (Refs #618) verhindert, dass alter Bug-Code über Cache-Lock festgehalten wird.
- Keine PII in SW-Cache, deshalb ist Cache-Wipe bei Version-Bump unkritisch.

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>👥 CLIENT — Klient:innen-Management (14 Cases)</strong></summary>

**Routen:** `/clients/`, `/clients/new/`, `/clients/<uuid>/`, `/clients/<uuid>/edit/`, `/clients/<uuid>/export/json/`, `/clients/<uuid>/export/pdf/`, `/clients/<uuid>/delete/`, `/clients/trash/`, `/clients/<uuid>/restore/`, `/api/clients/autocomplete/` 
**Views:** `src/core/views/clients.py` (`ClientListView`, `ClientCreateView`, `ClientDetailView`, `ClientUpdateView`, `ClientAutocompleteView`, `ClientDataExportJSONView`, `ClientDataExportPDFView`) + `src/core/views/client_deletion.py` (`ClientDeleteRequestView`, `ClientTrashView`, `ClientRestoreView`) 
**Services:** `src/core/services/clients.py` (`create_client`, `update_client`, `request_client_deletion`, `restore_client`, `track_client_visit`) 
**E2E-Coverage:** `test_clients_search.py`, `test_client_edit.py`, `test_client_deletion_workflow.py`, `test_client_autocomplete_recency.py`, `test_client_export.py`, `test_min_contact_stage_anonymous.py`, `test_contact_stage.py`, `test_fuzzy_search.py` 
**Spezial-Setup:** Cross-Facility-Tests benötigen `make seed FACILITIES=2` und 2 parallele Browser-Profile (`admin` in Facility 1, `admin_2` in Facility 2). DSGVO-Exporte erfordern Sudo-Re-Auth (Refs #683).

---

### TC-ID: ENT-CLIENT-01 — Klient:innen-Liste mit Pagination

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ✓ | `test_clients_search.py` |


**Vorbereitung:**
- Mit `fachkraft` / `anlaufstelle2026` einloggen.
- Seed mit `make seed` (Standard-Scale legt mind. 25 Klient:innen an).

**Schritte:**
1. `/clients/` aufrufen.
2. Header „Personen" sowie Filter (Suche, Stage, Altersgruppe) prüfen.
3. Pagination am Fuß sichten (max. 20/Seite Default).
4. Auf „Seite 2" klicken — HTMX tauscht das Tabellen-Partial.

**Erwartetes Ergebnis:**
- Liste rendert max. 20 Klient:innen pro Seite mit Pseudonym, Stage-Badge, Altersgruppe und Datum des letzten Kontakts.
- HTMX-Tausch ohne Full-Page-Reload (Network-Tab: nur Partial `core/clients/partials/table.html`).
- Sortierung alphabetisch nach `pseudonym`, „Letzter Kontakt"-Spalte annotiert via `Max("events__occurred_at")`.

**DSGVO/Security-Note:**
- Liste enthält nur Pseudonyme (Art. 5 Datenminimierung).
- Cross-Facility-Daten unsichtbar dank `Client.objects.for_facility` + RLS.

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-02 — Klient:in anlegen (Pseudonym, Altersgruppe, Stage)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ✓ | `test_clients_search.py::test_client_create_pseudonym_uniqueness` |


**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Eindeutiges Pseudonym vorbereiten (z.B. `Manuell-TC02-<random>`).

**Schritte:**
1. `/clients/new/` aufrufen — Headline „Neue Person".
2. Pseudonym `Manuell-TC02-<random>` eintragen.
3. Kontaktstufe auf „Identifiziert" lassen (Default).
4. Altersgruppe „18–26" wählen.
5. Notizen leer lassen.
6. Auf „Person anlegen" klicken.

**Erwartetes Ergebnis:**
- Redirect auf `/clients/<uuid>/` (Detail).
- Erfolgsmeldung „Person wurde angelegt." erscheint als Toast.
- Detail-Headline zeigt das eingegebene Pseudonym.
- Stage-Badge „Identifiziert", Altersgruppe „18–26" sichtbar.

**DSGVO/Security-Note:**
- `AuditLog` Action `CLIENT_CREATE` wird geschrieben (siehe `services/clients.py::create_client`).
- Activity-Eintrag „Person … angelegt" erscheint im Aktivitäts-Feed.

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-03 — Anonyme:r Klient:in: Event ohne Klient:in mit DocType-Mindeststufe

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ⚪ | `test_min_contact_stage_anonymous.py` |


**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/events/new/` aufrufen.
2. Im Dropdown „Dokumentationstyp" die Option „Kontakt" wählen.
3. Hinweistexte um das Klient:innen-Feld beobachten.
4. Auf „Beratungsgespräch" wechseln und Hinweis erneut prüfen.
5. Mit „Kontakt" und ohne Klient:innen-Auswahl absenden.

**Erwartetes Ergebnis:**
- Bei „Kontakt" erscheint Text „… anonym gespeichert" — Klient:in optional.
- Bei „Beratungsgespräch" erscheint Hinweis „Mindest-Kontaktstufe …" — Klient:in zwingend Stage `qualified`.
- Submit ohne Klient:in (DocumentType „Kontakt") legt Event mit `is_anonymous=True` an, Detail-Seite zeigt Badge „Anonym".

**DSGVO/Security-Note:**
- Anonyme Events haben keine `client`-FK und sind nicht über Personenfilter rückführbar (Art. 5 Datenminimierung).
- Refs #394, #472, #486.

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-04 — Klient:in bearbeiten + Stage hochstufen erzeugt Audit-Eintrag

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ✓ | `test_client_edit.py::test_edit_contact_stage_saves` |

**Voraussetzung:** vorhandene Person mit Stage `identified` (z.B. `Blitz-08`)

**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Eigene Test-Person anlegen (Pseudonym `TC04-<random>`, Stage `identified`), um Seed-Daten nicht zu mutieren.

**Schritte:**
1. `/clients/?q=TC04` aufrufen, Person anklicken.
2. „Bearbeiten" klicken — Edit-Formular lädt mit vorausgefüllten Werten.
3. Pseudonym leicht ändern (z.B. `TC04-edit-<random>`).
4. Kontaktstufe von „Identifiziert" auf „Qualifiziert" wechseln.
5. „Speichern" klicken.
6. Im Admin-Bereich (`/admin/core/auditlog/`) den letzten Eintrag prüfen.

**Erwartetes Ergebnis:**
- Redirect auf Detail-Seite `/clients/<uuid>/`.
- Erfolgs-Toast „Person wurde aktualisiert.".
- Detail zeigt neues Pseudonym + Badge „Qualifiziert".
- Zwei `AuditLog`-Einträge: `CLIENT_UPDATE` (mit `detail.changed_fields=["pseudonym","contact_stage"]`) und `STAGE_CHANGE` (mit `old_stage=identified`, `new_stage=qualified`).
- Activity-Feed zeigt „… qualifiziert" + „… aktualisiert".

**DSGVO/Security-Note:**
- Stage-Wechsel zu `qualified` ist begründungspflichtig (Art. 5/6 — neuer Verarbeitungsumfang). Audit-Eintrag erfüllt Rechenschaftspflicht (Art. 5 Abs. 2).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-05 — Detail-View mit Timeline, aktiven Cases, WorkItems

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ✓ | `test_clients_search.py::test_client_detail_event_timeline` |

**Voraussetzung:** Seed-Person mit Events + offenen Cases (z.B. `Stern-42`)

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/clients/?q=Stern-42` aufrufen, Treffer anklicken.
2. Detail-View beobachten — Headline `Stern-42`.
3. Sektionen prüfen: Stage-Badge, „Kontakt-Chronik", „Aktive Fälle", offene WorkItems, ggf. aktive Bans.
4. Erste Event-Karte aufklappen / anklicken.

**Erwartetes Ergebnis:**
- Headline = Pseudonym.
- Badge „Qualifiziert".
- Event-Liste sortiert absteigend nach `occurred_at`, mit Document-Type-Label.
- Sektion „Aktive Fälle" listet offene Cases mit `lead_user`.
- WorkItems sortiert nach Priorität (URGENT → IMPORTANT → NORMAL).

**DSGVO/Security-Note:**
- `track_client_visit` legt `RecentClientVisit`-Eintrag an (für Recency-Sortierung im Autocomplete).
- Bei `contact_stage=qualified` wird `AuditLog.VIEW_QUALIFIED` geschrieben.
- Events höherer Sensitivity sind über `Event.objects.visible_to(user)` für niedrigere Rollen unsichtbar (Refs #522).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-06 — Header-Suche per Pseudonym-Substring (HTMX-Typeahead)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ⚪ | `test_clients_search.py::test_client_list_search`, `test_fuzzy_search.py::test_global_dropdown_shows_similar` |


**Vorbereitung:**
- Mit `fachkraft` einloggen, auf Dashboard `/`.

**Schritte:**
1. Header-Suchfeld (`[data-testid='global-search-input']`) anklicken.
2. `Stern` eintippen (kein Enter).
3. HTMX-Dropdown mit Treffern beobachten.
4. Variante: Tippfehler `Schmitt` (vorher `Schmidt`-Person anlegen) → Sektion „Ähnliche Pseudonyme".

**Erwartetes Ergebnis:**
- Dropdown öffnet via HTMX nach ~150 ms Debounce.
- Sektion „Personen" zeigt Substring-Match (`Stern-42`).
- Bei Tippfehler erscheint zusätzliche Sektion „Ähnliche Pseudonyme" (pg_trgm Similarity > 0.3, Refs #536).
- Klick auf Treffer navigiert zu `/clients/<uuid>/`.

**DSGVO/Security-Note:**
- Suchergebnisse sind facility-gescoped (RLS) — Pseudonyme aus anderen Einrichtungen erscheinen nicht.

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-07 — Autocomplete im Event-Form (Recency-Sortierung)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ⚪ | `test_client_autocomplete_recency.py` |


**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/events/new/` aufrufen.
2. Klient:innen-Feld (`input[placeholder='Pseudonym eingeben...']`) anklicken — ohne Tippen.
3. Dropdown (`[role='listbox']`) erscheint mit `[role='option']`-Einträgen.
4. Reihenfolge mit `/api/clients/autocomplete/?q=` (Browser-Devtools) abgleichen.
5. `Stern` eintippen — Filter aktiv, Recency-Order bleibt.
6. Treffer per Klick übernehmen.

**Erwartetes Ergebnis:**
- Dropdown öffnet sofort beim Fokus (Alpine.js `@focus`-Handler).
- API liefert max. 30 Einträge sortiert nach `last_contact desc nulls_last`, dann `pseudonym`.
- Frontend-Reihenfolge identisch zur API.
- Klick auf Treffer befüllt Feld mit Pseudonym, Dropdown schließt.

**DSGVO/Security-Note:**
- Autocomplete ist rate-limited mit 30/Min/User (Refs #737, `block=True` → 429 statt schweigender Drop).
- `min_stage`-Param filtert Personen unterhalb der DocumentType-Mindeststufe (Refs #507).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-08 — JSON-Datenauskunft (LEAD+, Sudo-Re-Auth, Audit)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | leitung | C/F/S | ⚪ | `test_client_export.py::TestClientExportJSON` |

**Voraussetzung:** Sudo-Mode aktivierbar

**Vorbereitung:**
- Mit `leitung` / `anlaufstelle2026` einloggen.

**Schritte:**
1. `/clients/?q=Stern-42` aufrufen, Treffer öffnen.
2. „Datenauskunft"-Dropdown im Desktop-Header (`.hidden.md\\:flex`) anklicken.
3. Auf „JSON-Export" klicken.
4. Falls Sudo-Mode nicht aktiv → Re-Auth-Seite, Passwort eingeben, zurück.
5. Erneut „JSON-Export" klicken.
6. Download annehmen.

**Erwartetes Ergebnis:**
- Erster Klick ohne Sudo → Redirect auf Sudo-Form.
- Nach Re-Auth → Download startet, Filename-Pattern `datenauskunft_<pseudonym>.json`.
- JSON enthält `client`, `events`, `cases`, `episodes`, `goals`, `attachments_meta` (siehe `services/client_export.py`).
- `AuditLog`-Eintrag `EXPORT` mit `detail.format=JSON`, `target_type=Client-JSON`.

**DSGVO/Security-Note:**
- Art. 20 DSGVO Datenportabilität.
- Sudo-Mode (Refs #683) verhindert, dass eine gestohlene Session den Export auslösen kann.
- Rate-Limit 10/h/User schützt vor Massenexport.

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-09 — PDF-Datenauskunft (LEAD+, Layout-Prüfung)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | leitung | C/F/S | ⚪ | `test_client_export.py::TestClientExportPDF` |

**Voraussetzung:** Sudo-Mode aktiv

**Vorbereitung:**
- Mit `leitung` einloggen, Sudo-Mode bereits aktiviert.

**Schritte:**
1. `/clients/?q=Stern-42` aufrufen, Detail öffnen.
2. „Datenauskunft" → „PDF-Export" wählen.
3. PDF in Reader öffnen.

**Erwartetes Ergebnis:**
- Filename `datenauskunft_<pseudonym>.pdf`, Mime `application/pdf`.
- PDF enthält Kopf mit Einrichtungsname, Pseudonym, Erstellungsdatum.
- Sektionen: Stammdaten, Kontakt-Chronik, Fälle/Episoden, Wirkungsziele.
- Pseudonyme statt Klarnamen, keine Anhänge inline.
- `AuditLog` `EXPORT` mit `detail.format=PDF`, `target_type=Client-PDF`.

**DSGVO/Security-Note:**
- Art. 15 DSGVO Auskunftsrecht.
- Fachkraft-Direktzugriff auf `/clients/<uuid>/export/pdf/` → 403 (LeadOrAdminRequiredMixin).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-10 — Lösch-Antrag stellen (Vier-Augen-Workflow, AuditLog)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ⚪ | `test_client_deletion_workflow.py::test_staff_can_request_client_deletion`, `test_full_four_eyes_workflow` |


**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/clients/` aufrufen, beliebige Person öffnen.
2. Button mit `data-testid='client-delete-request-btn'` klicken.
3. Auf `/clients/<uuid>/delete/` Begründung in Textarea eingeben (z.B. „Manueller TC10").
4. „Löschantrag stellen" klicken.
5. Mit `leitung` parallel `/deletion-requests/` öffnen.
6. Antrag „Prüfen" → „Genehmigen".
7. Mit `fachkraft` ursprüngliche Detail-URL erneut aufrufen.

**Erwartetes Ergebnis:**
- Nach Antrag: Redirect auf Detail mit Toast „Löschantrag gestellt — Leitung wird benachrichtigt.".
- `DeletionRequest` mit `target_type=CLIENT`, `status=PENDING`, `requested_by=fachkraft`.
- Nach Genehmigung: `Client.is_deleted=True`, `AuditLog` `CLIENT_SOFT_DELETED`.
- Direktaufruf der Detail-URL nach Genehmigung → 404 (Detail-View filtert `is_deleted=False`).

**DSGVO/Security-Note:**
- Art. 17 DSGVO „Recht auf Löschung" mit Vier-Augen-Prinzip (Refs #626).
- Reviewer ≠ Antragsteller — sonst `ValidationError` „Reviewer darf nicht der Antragsteller sein".

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-11 — Trash-View nur für Admin

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | admin | C/F/S | ⚪ | `test_client_deletion_workflow.py::test_full_four_eyes_workflow` |

**Voraussetzung:** mind. 1 soft-deletete Person (vorher TC10 ausführen oder per Seed)

**Vorbereitung:**
- Mit `fachkraft` Direktzugriff auf `/clients/trash/` testen → erwartet 403.
- Mit `admin` einloggen.

**Schritte:**
1. `fachkraft`-Sitzung: `/clients/trash/` aufrufen → 403 prüfen.
2. `admin`-Sitzung: `/clients/trash/` aufrufen.
3. Liste sichten — soft-gelöschte Personen mit `deleted_at`, `deleted_by`.

**Erwartetes Ergebnis:**
- Fachkraft erhält 403 (`AdminRequiredMixin`).
- Admin sieht Tabelle mit Spalten Pseudonym, Gelöscht-am, Gelöscht-von.
- Sortierung absteigend nach `deleted_at`.
- Pro Eintrag „Wiederherstellen"-Button.

**DSGVO/Security-Note:**
- Trash respektiert Facility-Scoping (`Client.objects.for_facility(facility)`).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-12 — Wiederherstellung aus Trash innerhalb 30 Tage

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | admin | C/F/S | ⚪ | `test_client_deletion_workflow.py::test_full_four_eyes_workflow` |

**Voraussetzung:** soft-deletete Person verfügbar (z.B. nach TC10)

**Vorbereitung:**
- Mit `admin` einloggen.

**Schritte:**
1. `/clients/trash/` öffnen.
2. „Wiederherstellen"-Button bei der Test-Person klicken (POST `/clients/<uuid>/restore/`).
3. Redirect zur Detail-Seite verfolgen.
4. `/clients/` aufrufen — Person sollte wieder in Standard-Liste erscheinen.

**Erwartetes Ergebnis:**
- Redirect auf `/clients/<uuid>/` mit Toast „Person wiederhergestellt.".
- `Client.is_deleted=False`, `deleted_at=None`.
- `AuditLog` `CLIENT_RESTORED` mit `detail.pseudonym`.
- Eintrag verschwindet aus Trash, erscheint wieder in `ClientListView`.

**DSGVO/Security-Note:**
- Default-Frist: `Settings.client_trash_days=30` (anpassbar in Facility-Settings).
- Nach Ablauf: `enforce_retention` ruft `anonymize_client` automatisch auf (`anonymize_eligible_soft_deleted_clients`).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-13 — Cross-Facility-Verbot: admin_2 sieht Klient:in von admin nicht

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen / RLS | admin (F1) + admin_2 (F2) | C || — (manueller Cross-Facility-Test) |

**Voraussetzung:** `make seed FACILITIES=2` — zwei parallele Facilities mit getrennten Admins.

**Vorbereitung:**
- Browser-Profil 1: `admin` (Facility 1) eingeloggt.
- Browser-Profil 2 (Inkognito): `admin_2` (Facility 2) eingeloggt.

**Schritte:**
1. Profil 1: `/clients/` öffnen, Person z.B. `Stern-42` anklicken, UUID aus URL kopieren.
2. Profil 2: `https://localhost:8844/clients/<uuid-aus-F1>/` direkt aufrufen.
3. Profil 2: `/clients/<uuid>/edit/` direkt aufrufen.
4. Profil 2: `/api/clients/autocomplete/?q=Stern` aufrufen.

**Erwartetes Ergebnis:**
- Direkter Detail-Aufruf in Profil 2 → 404 (`get_object_or_404(..., facility=request.current_facility)`).
- Edit-URL → 404 vor Permission-Check (Facility-Scoping ist erste Hürde).
- Autocomplete liefert keine F1-Pseudonyme.

**DSGVO/Security-Note:**
- Pflicht-RLS-Test: Postgres `app.current_facility_id`-Session-Var + `FacilityScopedManager`.
- Verstoß gegen Facility-Boundary triggert `AuditLog` `FORBIDDEN` (sofern Code-Pfad erreicht).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-14 — Mobile-Liste auf iPhone-Viewport (Card-Layout, Touch-Targets)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C | ✓ ||


**Vorbereitung:**
- Chrome DevTools → Device-Toolbar → iPhone 14 (390 × 844).
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/clients/` öffnen.
2. Layout der Personen-Liste sichten (Cards statt Tabelle).
3. Tab-Reihenfolge per Tastatur (`Tab`) prüfen.
4. Auf eine Karte tippen → Detail.
5. Auf Detail-Seite Pull-to-Refresh oder Scroll-Verhalten prüfen.
6. Touch-Target-Größen messen (z.B. „Bearbeiten"-Link, Filter-Selects).

**Erwartetes Ergebnis:**
- Karten-Layout statt Tabelle (Tailwind-Breakpoints `sm:hidden`/`hidden sm:table`).
- Touch-Targets ≥ 44 × 44 px (WCAG 2.1 AA, manuell gemessen via Devtools).
- Keine horizontale Scroll-Leiste.
- Filter-Selects öffnen native Mobile-Dropdowns.

**DSGVO/Security-Note:**
- Keine zusätzlichen Daten auf Mobile sichtbar (gleiche Pseudonymisierung).

**Status:** ☐ Offen

---


</details>

---

<details open>
<summary><strong>📁 CASE — Fall-Lebenszyklus (12 Cases)</strong></summary>

**Routen:** `/cases/`, `/cases/new/`, `/cases/<uuid>/`, `/cases/<uuid>/edit/`, `/cases/<uuid>/close/`, `/cases/<uuid>/reopen/`, `/cases/<uuid>/assign-event/`, `/cases/<uuid>/remove-event/<uuid>/`, `/api/cases/for-client/` 
**Views:** `src/core/views/cases.py` (`CaseListView`, `CaseCreateView`, `CaseDetailView`, `CaseUpdateView`, `CaseCloseView`, `CaseReopenView`, `CaseAssignEventView`, `CaseRemoveEventView`, `CasesForClientView`) 
**Services:** `src/core/services/cases.py` (`create_case`, `update_case`, `close_case`, `reopen_case`, `assign_event_to_case`, `remove_event_from_case`) 
**E2E-Coverage:** `test_cases.py` (TestCaseCRUD, TestCasePermissions) 
**Spezial-Setup:** Cases sind `Pflichtfeld client` (Refs #748). Schließen/Wiedereröffnen erfordert LEAD+. Assistenz-Rolle hat 0 Zugriff (`StaffRequiredMixin`).

---

### TC-ID: ENT-CASE-01 — Fall-Liste mit Status-Filter (offen/geschlossen)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | fachkraft | C/F/S | ✓ | `test_cases.py::test_case_list_filter_by_status` |


**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/cases/` aufrufen — Headline „Fälle".
2. Status-Filter Dropdown auf „Offen" stellen.
3. HTMX-Tabellen-Tausch beobachten (`#case-table`).
4. Filter auf „Geschlossen" wechseln.
5. Suche `q=` mit Titel-Substring testen (z.B. ein Wort aus einem Seed-Titel).

**Erwartetes Ergebnis:**
- Liste zeigt Spalten Titel, Person, Lead, Status-Badge, Erstellt-am.
- HTMX tauscht Partial `core/cases/partials/table.html` ohne Full-Reload.
- Filter `status=open|closed` ist URL-persistent (`pagination_params` enthält Filter).
- Sortierung absteigend nach `created_at`.

**DSGVO/Security-Note:**
- Liste facility-gescoped (`Case.objects.for_facility`).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-02 — Neuen Fall für existierende Person anlegen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | fachkraft | C/F/S | ✓ | `test_cases.py::test_create_case` |


**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Eindeutigen Titel `TC02-Case-<random>` vorbereiten.

**Schritte:**
1. `/cases/new/` aufrufen.
2. Titel eintragen.
3. Beschreibung mit Testtext füllen.
4. `lead_user`-Select öffnen, Index 1 auswählen (= erste Lead-Option).
5. Pseudonym-Autocomplete fokussieren, ersten Treffer wählen.
6. „Fall erstellen" klicken.

**Erwartetes Ergebnis:**
- Redirect auf `/cases/<uuid>/` mit Toast „Fall wurde erstellt.".
- Detail-Headline = Titel.
- Person und Lead in Sidebar/Meta sichtbar, Status-Badge „Offen".
- `AuditLog` `CASE_CREATE`.

**DSGVO/Security-Note:**
- `client` ist Pflichtfeld (Refs #748). Ohne Person → `ValidationError` „Fälle müssen einer Person zugeordnet sein.".

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-03 — Fall + automatische Episode (separat angelegt nach Erstellung)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | fachkraft | C/F/S | ⚪ | `test_cases.py::TestEpisodes::test_create_episode` |


**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Schritte aus TC-ID ENT-CASE-02 (Fall anlegen) durchspielen.
2. Auf der Detail-Seite Link „Neue Episode" anklicken (`/cases/<uuid>/episodes/new/`).
3. Titel `TC03-Episode-<random>`, `started_at=2025-01-15`.
4. „Episode anlegen" submitten.

**Erwartetes Ergebnis:**
- Redirect zur Fall-Detail-Seite.
- Episoden-Sektion zeigt neuen Eintrag mit Status „aktiv" (kein `ended_at`).
- Implementation: Episode wird **nicht automatisch** beim Case-Anlegen erzeugt (siehe `services/cases.py::create_case` — kein Auto-Episode-Call). Manuell über Sub-Form anzulegen.

**DSGVO/Security-Note:**
- Keine zusätzliche PII; Episode erbt Facility-Scope vom Case (RLS via JOIN).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-04 — Detail-View mit Episoden, Goals, Events, Status-Badge

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | fachkraft | C/F/S | ✓ | `test_cases.py::test_case_detail_shows_info` |

**Voraussetzung:** Fall mit Episoden + Goals + Events vorhanden (Seed)

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/cases/` öffnen, ersten Fall klicken.
2. Detail-Layout sichten:
 - Headline = Titel.
 - Status-Badge (Offen/Geschlossen).
 - Meta-Info „Fallverantwortlich", „Erstellt am", „Person".
 - Sektionen Episoden, Goals, Events (zugeordnet/nicht zugeordnet).

**Erwartetes Ergebnis:**
- `select_related("client", "lead_user", "created_by")` in `CaseDetailView` — keine N+1-Queries (in Devtools-Performance-Profile sichtbar).
- `prefetch_related("milestones")` für Goals.
- Events-Sektion zeigt zugeordnete Events absteigend nach `occurred_at`.
- „Nicht zugeordnete Events" füllen sich mit Events derselben Person ohne `case`.

**DSGVO/Security-Note:**
- Events werden via `Event.objects.visible_to(user)` gefiltert (Sensitivity-Layer, Refs #522).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-05 — Fall bearbeiten (Titel, Beschreibung, Lead)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | fachkraft | C/F/S | ✓ | `test_cases.py::test_edit_case` |

**Voraussetzung:** eigener Test-Fall (TC02 zuerst)

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Auf Detail-Seite des Test-Falls „Bearbeiten" klicken.
2. Titel auf `TC05-Updated-<random>` ändern.
3. Beschreibung leicht modifizieren.
4. Lead auf anderen User wechseln.
5. „Speichern" klicken.

**Erwartetes Ergebnis:**
- Redirect auf `/cases/<uuid>/`.
- Toast „Fall wurde aktualisiert.".
- Headline = neuer Titel.
- `AuditLog` `CASE_UPDATE` mit `detail.changed_fields=["title","description","lead_user"]` (PII-frei).

**DSGVO/Security-Note:**
- Optimistic Locking (Refs #531): `expected_updated_at` wird beim POST geprüft. Bei Konflikt → Toast-Fehlermeldung.

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-06 — Fall schließen mit Begründung (LEAD+ erforderlich)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | leitung | C/F/S | ⚪ | `test_cases.py::test_close_case` |

**Voraussetzung:** offener Fall vorhanden

**Vorbereitung:**
- Mit `leitung` einloggen.

**Schritte:**
1. Eigenen Fall anlegen (analog TC02).
2. Detail-Seite öffnen.
3. „Schließen"-Button im Desktop-Header (`.hidden.md\\:flex`) klicken (POST `/cases/<uuid>/close/`).
4. Status-Badge prüfen.

**Erwartetes Ergebnis:**
- Redirect auf Detail-Seite.
- Toast „Fall wurde geschlossen.".
- Status-Badge wechselt von „Offen" auf „Geschlossen".
- „Wiedereröffnen"-Button erscheint.
- `AuditLog` `CASE_CLOSE`.
- `Case.closed_at` gesetzt auf `timezone.now`.

**DSGVO/Security-Note:**
- Fachkraft (`StaffRequiredMixin` reicht für CRUD, aber Close erfordert `LeadOrAdminRequiredMixin`) → Schließen-Button bei Fachkraft nicht sichtbar (Test in `test_staff_cannot_close_case`).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-07 — Geschlossenen Fall wiedereröffnen (LEAD+)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | leitung | C/F/S | ⚪ | `test_cases.py::test_reopen_case` |

**Voraussetzung:** geschlossener Fall (TC06 zuerst)

**Vorbereitung:**
- Mit `leitung` einloggen, geschlossener Fall vorhanden.

**Schritte:**
1. Detail-Seite des geschlossenen Falls öffnen.
2. „Wiedereröffnen" klicken (POST `/cases/<uuid>/reopen/`).
3. Status-Badge prüfen.

**Erwartetes Ergebnis:**
- Toast „Fall wurde wiedereröffnet.".
- Status-Badge wieder „Offen".
- `closed_at=None`.
- `AuditLog` `CASE_REOPEN`.
- „Schließen"-Button erscheint erneut.

**DSGVO/Security-Note:**
- Activity-Feed: „Fall … wiedereröffnet" (Refs `Activity.Verb.REOPENED`).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-08 — Event einem Fall zuordnen (HTMX)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle / Events | fachkraft | C/F/S | ⚪ ||

**Voraussetzung:** Fall + nicht zugeordnetes Event derselben Person

**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Person hat mind. 1 Event ohne `case`.

**Schritte:**
1. Fall-Detail öffnen.
2. Sektion „Nicht zugeordnete Events" — beliebigen Eintrag wählen.
3. „Zu Fall hinzufügen" klicken (POST `/cases/<uuid>/assign-event/` mit `event_id`).
4. HTMX-Tausch beobachten — Event wandert in Sektion „Zugeordnete Events".

**Erwartetes Ergebnis:**
- HTMX rendert Partial `core/cases/partials/event_list.html`.
- `Event.case_id = case.pk` gespeichert.
- Konsistenz: Anonyme Events gehen nur zu Cases ohne Client (`assign_event_to_case`-Validierung).
- Bei Mismatch (Person des Events ≠ Person des Falls) → ValidationError „Person des Ereignisses passt nicht …".

**DSGVO/Security-Note:**
- `get_visible_event_or_404` schützt vor Cross-Sensitivity-Zuordnung.

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-09 — Event aus Fall entfernen (HTMX)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle / Events | fachkraft | C/F/S | ⚪ ||

**Voraussetzung:** Fall mit zugeordnetem Event (TC08)

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Fall-Detail öffnen.
2. In Sektion „Zugeordnete Events" beim Event auf „Entfernen" klicken (POST `/cases/<uuid>/remove-event/<event_pk>/`).
3. HTMX-Swap beobachten.

**Erwartetes Ergebnis:**
- Partial wird neu gerendert, Event wandert zurück in „Nicht zugeordnete Events".
- `Event.case = None`.
- Event existiert weiter, kein Soft-Delete.

**DSGVO/Security-Note:**
- Keine zusätzliche Audit-Action (Service ist Reservierung für künftiges Audit, siehe Code-Kommentar).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-10 — „Cases-für-Klient:in"-API auf Detail-Page

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle / API | fachkraft | C |||

**Voraussetzung:** Person mit ≥ 2 offenen Fällen

**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Test-Person (z.B. `Stern-42`) anlegen oder Seed-Fall + 2. Fall manuell anlegen.

**Schritte:**
1. `/clients/?q=Stern-42` → Detail öffnen.
2. Sektion „Aktive Fälle" zählt offene Cases.
3. Optional: in Devtools `/api/cases/for-client/?client=<uuid>` direkt aufrufen.

**Erwartetes Ergebnis:**
- Detail listet alle offenen (`status=OPEN`) Fälle absteigend nach `created_at`.
- API JSON `[{"id": "<uuid>", "title": "..."}]`.
- Rate-Limit 30/Min (Refs `RATELIMIT_FREQUENT`-Pendant; hier `30/m`).

**DSGVO/Security-Note:**
- Endpoint facility-gescoped (`Case.objects.filter(facility=request.current_facility,...)`).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-11 — Update mit ungültigen Feldern → Toast statt Full-Page-Fehler

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | fachkraft | C/F/S | ⚪ ||

**Voraussetzung:** eigener Test-Fall

**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Fall im Edit-Modus.

**Schritte:**
1. Edit-Form öffnen.
2. Pflichtfeld Titel leeren.
3. „Speichern" klicken.
4. Browser-Devtools → Netzwerk-Tab beobachten.

**Erwartetes Ergebnis:**
- Form rendert mit Validierungsfehlern (Form-Errors zu „Titel: Dieses Feld ist erforderlich.").
- Wenn `expected_updated_at`-Konflikt simuliert (zwei Tabs gleichzeitig editieren): `ValidationError` aus `check_version_conflict` → Toast „Fall wurde von … geändert", Redirect zurück auf Edit-Seite.
- Kein 500-Error.

**DSGVO/Security-Note:**
- Optimistic Locking verhindert Lost-Updates bei zwei Bearbeiter:innen (Refs #531).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-12 — Mobile-Detail mit Tab-Navigation Episoden/Goals/Events

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | fachkraft | C | ✓ ||


**Vorbereitung:**
- Chrome Devtools → Device-Toolbar iPhone 14.
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/cases/` → ersten Fall öffnen.
2. Mobile-Layout sichten — Sektionen ggf. als Akkordeon oder Tabs.
3. Touch-Target der Aktionen prüfen („Bearbeiten", „Schließen").
4. Sticky-Header-Verhalten beim Scrollen testen.

**Erwartetes Ergebnis:**
- Sektionen Episoden, Goals, Events sind unter dem Header lesbar (kein horizontales Scrollen).
- Buttons ≥ 44 × 44 px.
- Wenn Tabs verwendet: aktiver Tab visuell hervorgehoben, Tab-Wechsel ohne Page-Reload.
- Status-Badge bleibt in Sticky-Header sichtbar.

**DSGVO/Security-Note:**
- Keine Layout-bedingten Änderungen an sichtbaren Pseudonymen / Sensitivity-Filtern.

**Status:** ☐ Offen

---


</details>

---

<details open>
<summary><strong>🔄 EPI — Episoden (5 Cases)</strong></summary>

**Routen:** `/cases/<case_pk>/episodes/new/`, `/cases/<case_pk>/episodes/<pk>/edit/`, `/cases/<case_pk>/episodes/<pk>/close/` 
**Views:** `src/core/views/case_episodes.py` (`EpisodeCreateView`, `EpisodeUpdateView`, `EpisodeCloseView`) 
**Services:** `src/core/services/episodes.py` (`create_episode`, `update_episode`, `close_episode`) 
**E2E-Coverage:** `test_cases.py::TestEpisodes` 
**Spezial-Setup:** Episoden sind nur für `Case.Status=OPEN` anlegbar. Idempotenz beim Schließen (zweimal close → no-op).

---

### TC-ID: ENT-EPI-01 — Episode anlegen (Sub-URL `/cases/<uuid>/episodes/new/`)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Episoden | fachkraft | C/F/S | ✓ | `test_cases.py::test_create_episode` |

**Voraussetzung:** offener Fall vorhanden

**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Fall via TC-ID ENT-CASE-02 anlegen oder existierenden offenen Fall öffnen.

**Schritte:**
1. Fall-Detail öffnen.
2. Link „Neue Episode" klicken — Redirect auf `/cases/<case_pk>/episodes/new/`.
3. Titel `TC01-Episode-<random>`, `started_at=2025-01-15` eintragen.
4. Beschreibung kurz füllen.
5. „Speichern" klicken.

**Erwartetes Ergebnis:**
- Redirect zurück zur Fall-Detail-Seite.
- Episoden-Sektion zeigt neuen Eintrag mit Status „aktiv" (`ended_at=None`).
- Toast „Episode wurde erstellt.".
- `Episode.created_by = current_user`.

**DSGVO/Security-Note:**
- `started_at` ist Pflichtfeld (DateField); bei leerem POST → Form-Error.
- Default falls Service direkt aufgerufen: `timezone.now.date`.

**Status:** ☐ Offen

---

### TC-ID: ENT-EPI-02 — Episode bearbeiten

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Episoden | fachkraft | C/F/S | ⚪ ||

**Voraussetzung:** Fall mit aktiver Episode (TC-ID ENT-EPI-01)

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Fall-Detail öffnen.
2. Episode in Liste anklicken / „Bearbeiten" daneben.
3. URL `/cases/<case_pk>/episodes/<pk>/edit/` öffnet Edit-Form.
4. Titel auf `TC02-Updated-<random>`, Beschreibung anpassen, `started_at` ändern.
5. „Speichern".

**Erwartetes Ergebnis:**
- Redirect zur Fall-Detail-Seite.
- Toast „Episode wurde aktualisiert.".
- Aktualisierte Werte in Liste sichtbar.
- `update_episode` validiert Allowlist (`title`, `description`, `started_at`, `ended_at`).

**DSGVO/Security-Note:**
- Mass-Assignment-Schutz im Service: ungültiges Feld → `ValueError`.

**Status:** ☐ Offen

---

### TC-ID: ENT-EPI-03 — Episode mit Outcome schließen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Episoden | fachkraft | C/F/S | ⚪ | `test_cases.py::test_close_episode` |

**Voraussetzung:** aktive Episode

**Vorbereitung:**
- Mit `fachkraft` einloggen, Fall + Episode angelegt.

**Schritte:**
1. Fall-Detail öffnen.
2. Bei der Episode Button „Abschließen" klicken (POST `/cases/<case_pk>/episodes/<pk>/close/`).
3. Status der Episode prüfen.

**Erwartetes Ergebnis:**
- Toast „Episode wurde abgeschlossen.".
- Episode-Status wechselt von „aktiv" auf „abgeschlossen".
- `ended_at = timezone.now.date` (Default falls nicht übergeben).
- Idempotenz: erneuter Klick auf „Abschließen" hat keinen Effekt (`if episode.ended_at is not None: return`).

**DSGVO/Security-Note:**
- Outcome-Modell (`core.models.outcome`) ist getrennt von Episode-Schließen — Outcomes hängen an `OutcomeGoal`, nicht an Episode-Closure.

**Status:** ☐ Offen

---

### TC-ID: ENT-EPI-04 — Permission: Assistenz darf Episode nicht anlegen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Episoden / Permissions | assistenz | C || `test_cases.py::test_assistant_cannot_access_cases` (verwandt) |

**Voraussetzung:** offener Fall mit bekannter UUID

**Vorbereitung:**
- Mit `admin` einloggen, UUID eines offenen Falls notieren.
- Mit `assistenz` einloggen.

**Schritte:**
1. `assistenz`-Sitzung: `/cases/<known-uuid>/episodes/new/` direkt aufrufen.
2. HTTP-Status prüfen.
3. Bonus: `/cases/` → 403 (verwandter Test).

**Erwartetes Ergebnis:**
- 403 Forbidden (`StaffRequiredMixin` auf `EpisodeCreateView`).
- Keine Episode angelegt.
- Kein Audit-Eintrag.

**DSGVO/Security-Note:**
- Assistenz-Rolle ist Read-only-Light (siehe `views/mixins.py`); Schreibrechte nur ab `fachkraft`.

**Status:** ☐ Offen

---

### TC-ID: ENT-EPI-05 — Mehrere Episoden pro Fall (parallel aktiv möglich)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Episoden | fachkraft | C/F/S | ⚪ ||

**Voraussetzung:** offener Fall

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Fall-Detail öffnen.
2. Episode A anlegen (`started_at=2025-01-01`, Titel `Phase A`).
3. Erneut „Neue Episode" — Episode B (`started_at=2025-02-15`, Titel `Phase B`).
4. Liste sortiert nach `started_at` desc — Phase B oben.
5. Beide Episoden sollen Status „aktiv" zeigen (kein automatischer Close von A).

**Erwartetes Ergebnis:**
- Beide Episoden gespeichert, beide aktiv (`ended_at=None`).
- Standardsortierung `Meta.ordering = ["-started_at"]`.
- Kein Code-Pfad zwingt zu seriellen Episoden — parallele Phasen sind fachlich erlaubt.

**DSGVO/Security-Note:**
- Wenn Episode-Anonymisierung greift (TC-ID ENT-CLIENT-10/12): `Episode.title="Episode (anonymisiert)"`, `description=""` (Bulk-Update via `services/clients.py::anonymize_client`).

**Status:** ☐ Offen

</details>

---

<details open>
<summary><strong>🎯 GOAL — Wirkungsziele & Meilensteine (7 Cases)</strong></summary>

**Routen:** `/cases/<case_pk>/goals/new/`, `/cases/<case_pk>/goals/<pk>/edit/`, `/cases/<case_pk>/goals/<pk>/toggle/`, `/cases/<case_pk>/goals/<goal_pk>/milestones/new/`, `/cases/<case_pk>/milestones/<pk>/toggle/`, `/cases/<case_pk>/milestones/<pk>/delete/` 
**Views:** `src/core/views/case_goals.py` (`GoalCreateView`, `GoalUpdateView`, `GoalToggleView`, `MilestoneCreateView`, `MilestoneToggleView`, `MilestoneDeleteView`) 
**Services:** `src/core/services/goals.py` (`create_goal`, `update_goal`, `achieve_goal`, `unachieve_goal`, `create_milestone`, `toggle_milestone`, `delete_milestone`) 
**E2E-Coverage:** `test_cases.py::TestGoalsAndMilestones` 
**Spezial-Setup:** Alle Endpoints sind HTMX-only und rendern `core/cases/partials/goals_section.html`. Rate-Limit `RATELIMIT_FREQUENT` (60/min/User).

---

### TC-ID: ENT-GOAL-01 — Wirkungsziel anlegen (HTMX-Inline-Form)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Wirkungsziele | fachkraft | C/F/S | ✓ | `test_cases.py::test_create_goal` |

**Voraussetzung:** offener Fall

**Vorbereitung:**
- Mit `fachkraft` einloggen, Fall öffnen.

**Schritte:**
1. Fall-Detail → Sektion `#goals-section` lokalisieren.
2. Inline-Input „Neues Wirkungsziel" mit Titel `TC01-Ziel-<random>` füllen.
3. „Hinzufügen" klicken — POST auf `/cases/<case_pk>/goals/new/`.
4. HTMX-Swap der Sektion abwarten.

**Erwartetes Ergebnis:**
- Sektion `goals-section` neu gerendert, neues Ziel erscheint.
- Ziel-Status: „offen" (nicht erreicht).
- `OutcomeGoal.created_by = current_user`.
- Kein Full-Page-Reload (Network-Tab: nur Partial).

**DSGVO/Security-Note:**
- Beschreibung optional. Empfehlung: keine PII / Klarnamen — Help-Text muss Sensitivity-Hinweis tragen (siehe Help-Text in `models/outcome.py` — derzeit kein Help-Text → Verbesserungsoption).

**Status:** ☐ Offen

---

### TC-ID: ENT-GOAL-02 — Wirkungsziel bearbeiten (HTMX-Update)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Wirkungsziele | fachkraft | C/F/S | ⚪ ||

**Voraussetzung:** vorhandenes Ziel (TC-ID ENT-GOAL-01)

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Sektion `goals-section` öffnen.
2. Ziel-Bearbeiten-Toggle klicken (Edit-Inline-Form).
3. Titel und Beschreibung anpassen.
4. „Speichern" — POST `/cases/<case_pk>/goals/<pk>/edit/`.

**Erwartetes Ergebnis:**
- HTMX-Swap rendert Sektion neu.
- Aktualisierte Felder sichtbar.
- `update_goal` setzt nur explizit übergebene Felder (None → unchanged).

**DSGVO/Security-Note:**
- Kein dediziertes AuditLog für Goal-Updates (Code prüfen: `services/goals.py::update_goal` schreibt kein AuditLog). Empfehlung als Verbesserung ab `services/goals.py:29`.

**Status:** ☐ Offen

---

### TC-ID: ENT-GOAL-03 — Goal-Toggle (erreicht/offen) via HTMX

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Wirkungsziele | fachkraft | C/F/S | ✓ ||

**Voraussetzung:** vorhandenes Ziel

**Vorbereitung:**
- Mit `fachkraft` einloggen, Fall mit Ziel öffnen.

**Schritte:**
1. Goal-Toggle-Button (Checkbox / Status-Badge) klicken — POST `/cases/<case_pk>/goals/<pk>/toggle/`.
2. Status-Wechsel beobachten („offen" → „erreicht").
3. Erneut klicken → zurück auf „offen".

**Erwartetes Ergebnis:**
- Erste Klick: `goal.is_achieved=True`, `goal.achieved_at=timezone.localdate`.
- Zweite Klick: `is_achieved=False`, `achieved_at=None` (`unachieve_goal`).
- HTMX-Partial-Refresh ohne Reload.
- Idempotenz: Wenn `is_achieved=True` und `achieve_goal` erneut aufgerufen → no-op (Service-Return).

**DSGVO/Security-Note:**
- Kein PII-Risiko; reine Status-Mutation.

**Status:** ☐ Offen

---

### TC-ID: ENT-GOAL-04 — Meilenstein anlegen (Sub von Goal)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Meilensteine | fachkraft | C/F/S | ✓ | `test_cases.py::test_create_milestone` |

**Voraussetzung:** vorhandenes Ziel

**Vorbereitung:**
- Mit `fachkraft` einloggen, Fall mit Ziel öffnen.

**Schritte:**
1. Im Ziel-Block Inline-Input `input[placeholder='Neuer Meilenstein']` lokalisieren.
2. Titel `TC04-MS-<random>` eingeben.
3. „+"-Button rechts daneben klicken — POST `/cases/<case_pk>/goals/<goal_pk>/milestones/new/`.
4. HTMX-Swap abwarten.

**Erwartetes Ergebnis:**
- Sektion `goals-section` rendert mit neuem Meilenstein als Listenpunkt.
- Default `is_completed=False`, `sort_order=0`.
- Visuell: kein `line-through`-Style.

**DSGVO/Security-Note:**
- Meilensteine erben Facility-Scope vom Goal → Case → Facility (RLS via Joins).

**Status:** ☐ Offen

---

### TC-ID: ENT-GOAL-05 — Meilenstein-Toggle via HTMX

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Meilensteine | fachkraft | C/F/S | ⚪ | `test_cases.py::test_toggle_milestone` |

**Voraussetzung:** vorhandener Meilenstein (TC-ID ENT-GOAL-04)

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Meilenstein-Button klicken (POST `/cases/<case_pk>/milestones/<pk>/toggle/`).
2. Visuelle Änderung beobachten — `line-through`-Klasse erscheint, Häkchen statt leerer Kreis.
3. Erneut klicken — wieder rückgängig.

**Erwartetes Ergebnis:**
- Erste Klick: `is_completed=True`, `completed_at=timezone.localdate`, `<span class="line-through">`.
- Zweite Klick: `is_completed=False`, `completed_at=None`.
- HTMX rendert Partial mit korrektem CSS-State.

**DSGVO/Security-Note:**
- Kein Audit-Trail (analog zu Goals — siehe Empfehlung TC-ID ENT-GOAL-02).

**Status:** ☐ Offen

---

### TC-ID: ENT-GOAL-06 — Meilenstein löschen (mit AuditLog)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Meilensteine | fachkraft | C/F/S | ⚪ ||

**Voraussetzung:** vorhandener Meilenstein

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Meilenstein-Lösch-Button klicken (POST `/cases/<case_pk>/milestones/<pk>/delete/`).
2. HTMX-Swap beobachten — Eintrag verschwindet aus Liste.
3. AuditLog im Admin prüfen (`/admin/core/auditlog/?action=MILESTONE_DELETE`).

**Erwartetes Ergebnis:**
- Meilenstein DB-deleted (Hard-Delete, kein Soft-Delete).
- `AuditLog` `MILESTONE_DELETE` mit `detail.title` und `detail.case_id`.
- Activity-Eintrag „Meilenstein '…' gelöscht".

**DSGVO/Security-Note:**
- Im Gegensatz zu Client/Case ist das Hard-Delete bei Meilensteinen akzeptabel (kein PII, leichtgewichtige Domänenobjekte).

**Status:** ☐ Offen

---

### TC-ID: ENT-GOAL-07 — Mehrere Goals + Meilensteine in Detail-View

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Wirkungsziele / Meilensteine | fachkraft | C/F/S | ✓ ||

**Voraussetzung:** leerer Fall (oder ausreichend Ziel-Slots)

**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Eigener Test-Fall.

**Schritte:**
1. Fall-Detail öffnen.
2. Drei Wirkungsziele anlegen: „Wohnung", „Job", „Schuldenberatung".
3. Pro Ziel zwei Meilensteine ergänzen.
4. Bei Ziel „Wohnung" einen Meilenstein toggeln (erledigt).
5. Bei Ziel „Job" das Goal selbst toggeln (erreicht).

**Erwartetes Ergebnis:**
- Drei Goal-Blöcke nebeneinander/untereinander, jeder mit eigener Meilenstein-Liste.
- `prefetch_related("milestones")` bewirkt: keine N+1-Queries (in Devtools-Performance prüfbar).
- Toggle-States bleiben nach Refresh erhalten.
- Sortierung Goals: `Meta.ordering = ["-created_at"]` (neueste zuerst).
- Sortierung Milestones: `Meta.ordering = ["sort_order"]`.

**DSGVO/Security-Note:**
- Bei Anonymisierung der Person bleiben Goals + Milestones inhaltlich erhalten (keine Mutation in `anonymize_client`); falls erforderlich, manueller Eingriff durch Leitung.

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>📝 EVT — Events / Dokumentation (10 Cases)</strong></summary>

**Routen:** `/events/new/`, `/events/<uuid>/`, `/events/<uuid>/edit/`, `/events/<uuid>/delete/`, `/api/events/fields/` 
**Views:** `src/core/views/events.py` (`EventCreateView`, `EventDetailView`, `EventUpdateView`, `EventDeleteView`, `EventFieldsPartialView`) 
**Services:** `src/core/services/events/crud.py` (`create_event`, `update_event`, `soft_delete_event`, `attach_files_to_new_event`), `src/core/services/event.py` (Re-Export-Stub Refs #777), `src/core/services/sensitivity.py` (`user_can_see_event`, `get_visible_event_or_404`, `remove_restricted_fields`), `src/core/services/quick_templates.py` 
**E2E-Coverage:** `test_quick_capture.py`, `test_fieldtemplate_default_value.py`, `test_min_contact_stage_anonymous.py` 
**Spezial-Setup:** Anonyme Events benötigen DocumentType ohne `min_contact_stage`. Sensitivity-Tests benötigen DocumentTypes der Stufen `normal` / `elevated` / `high`. Optimistic-Concurrency-Test braucht zwei parallele Browser-Tabs oder Inkognito-Fenster.

---

### TC-ID: ENT-EVT-01 — Event mit DocumentType anlegen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C/F/S | ✓ | `test_quick_capture.py` |

**Voraussetzung:** ENT-CLIENT-02 (mind. eine Klient:in mit Kontaktstufe `qualifiziert`).

**Vorbereitung:**
- Pseudonym/UUID einer geseedeten Klient:in notieren.
- Mindestens ein DocumentType („Beratung", Sensitivity `normal`) ist via Seed vorhanden.

**Schritte:**
1. `/events/new/` aufrufen.
2. Klient:in via Autocomplete-Feld (Pseudonym tippen) auswählen — `?client=<uuid>` wird in URL/Hidden-Input gespiegelt.
3. Aus Dropdown DocumentType „Beratung" wählen — HTMX-Request `GET /api/events/fields/?document_type=<id>` lädt dynamische Felder via `EventFieldsPartialView` (Status 200).
4. Pflichtfelder ausfüllen, `occurred_at` auf jetzt setzen.
5. Submit-Button „Speichern" klicken.

**Erwartetes Ergebnis:**
- Redirect 302 → `/events/<new-uuid>/`.
- Flash-Message „Kontakt wurde dokumentiert."
- AuditLog-Eintrag `event_create` mit `target_type=Event`, `detail.document_type="Beratung"`, `detail.is_anonymous=false`.
- `EventHistory`-Eintrag mit `action=CREATE`.
- Activity-Log mit `verb=CREATED` und Summary „Beratung für \<Pseudonym\>".
- Event erscheint in Klient-Timeline (`/clients/<uuid>/`) und Zeitstrom (`/`).

**DSGVO/Security-Note:**
- Sensitivity-Filter steuert Zeitstrom-Sichtbarkeit (Art. 5 DSGVO Zweckbindung).
- Beim POST greift `ratelimit(key=user, rate=RATELIMIT_MUTATION)`.

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-02 — Dynamische Felder via HTMX (EventFieldsPartialView)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C/F/S | ✓ | `test_fieldtemplate_default_value.py` |

**Voraussetzung:** mehrere DocumentTypes mit unterschiedlichen FieldTemplates (Text, Date, Select, File, Sensitivity-Override).

**Vorbereitung:**
- DocumentType A (3 Felder) und DocumentType B (5 Felder, davon eins mit `default_value`) müssen existieren.

**Schritte:**
1. `/events/new/` öffnen, Browser-Devtools → Netzwerk-Tab.
2. Erstes DocumentType A im Dropdown wählen.
3. HTMX-Request `GET /api/events/fields/?document_type=<A>` beobachten → Antwort 200, Partial `dynamic_fields.html` mit 3 Inputs.
4. DocumentType-Dropdown auf B wechseln.
5. Zweiter HTMX-Request `GET /api/events/fields/?document_type=<B>` → 200, 5 Inputs, Default-Wert sichtbar im entsprechenden Feld (`field.initial = ft.get_default_initial`).
6. Felder umschalten und prüfen, dass das `#dynamic-fields-target` durch HTMX-Swap ersetzt wird (kein voller Reload).

**Erwartetes Ergebnis:**
- HTMX-Swap ersetzt nur das Felder-Container-Div.
- Default-Werte des FieldTemplates werden vor-ausgefüllt.
- Bei Auswahl eines `HIGH`-DocumentType durch fachkraft (`STAFF`-Rolle) → 403 Forbidden, weil `user_can_see_document_type` False liefert (`PermissionDenied` in `EventFieldsPartialView.get`).

**DSGVO/Security-Note:**
- Sensitivity-Guard auf Partial-Endpoint verhindert, dass Assistant/Staff Feldlabels für höhere Stufen sehen (Refs #774).

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-03 — Quick-Capture via QuickTemplate

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C/F/S | ✓ | `test_quick_capture.py` |

**Voraussetzung:** QuickTemplate „Kurzkontakt" (DocumentType „Kontakt", Prefill für 2 Felder).

**Vorbereitung:**
- QuickTemplate per Seed/Admin angelegt, dem User zugänglich (`list_templates_for_user` liefert es).

**Schritte:**
1. `/events/new/?template=<quicktemplate-uuid>` aufrufen.
2. Prüfen: DocumentType „Kontakt" ist vorausgewählt; 2 Felder enthalten Prefill-Werte (`apply_template`).
3. Felder unverändert lassen, `occurred_at` setzen, Submit.
4. Detail-View des neuen Events öffnen.

**Erwartetes Ergebnis:**
- Form ist mit Template-Werten vor-befüllt.
- Event speichert sich mit Template-Werten als `data_json`.
- AuditLog `event_create`.
- Bei ungültigem `?template=<uuid>` (anderer Facility / Inactive) → Template wird ignoriert, Form lädt mit Default-DocumentType (kein Crash, kein Hint).

**DSGVO/Security-Note:**
- `get_template_for_user` filtert Templates auf Facility und Sensitivity-Sichtbarkeit (Mandantentrennung).

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-04 — Anonymes Event ohne Klient:in

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C | ⚪ | `test_min_contact_stage_anonymous.py` |

**Voraussetzung:** DocumentType „Notiz" mit `min_contact_stage=""` (leer → erlaubt anonym).

**Vorbereitung:**
- DocumentType „Notiz" ohne Mindest-Kontaktstufe vorhanden.

**Schritte:**
1. `/events/new/` aufrufen, Klient-Feld leer lassen.
2. DocumentType „Notiz" wählen.
3. Felder ausfüllen, Submit.

**Erwartetes Ergebnis:**
- Event wird mit `is_anonymous=True`, `client=NULL` gespeichert.
- AuditLog `event_create` mit `detail.is_anonymous=true`.
- Detail-View zeigt „Anonym" statt Pseudonym.
- **Negativer Pfad:** Wird DocumentType mit `min_contact_stage=qualifiziert` ohne Klient:in gewählt → ValidationError „Für diesen Dokumentationstyp muss eine Person ausgewählt werden, da eine Mindest-Kontaktstufe vorausgesetzt wird." Form rendert erneut mit Fehlermeldung.

**DSGVO/Security-Note:**
- Anonyme Events dürfen nicht an klientelbezogene Cases gehängt werden (`create_event` validiert: ValidationError „Anonyme Ereignisse dürfen nicht an klientelbezogene Fälle gehängt werden.").

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-05 — Event bearbeiten — EventHistory(UPDATE)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C | ⚪ ||

**Voraussetzung:** ENT-EVT-01 (bestehendes Event vom selben User).

**Vorbereitung:**
- Event-UUID des unter EVT-01 angelegten Events.
- User ist `created_by` (Assistant darf nur eigene Events editieren — `dispatch` prüft `is_staff_or_above` ODER `created_by == user`).

**Schritte:**
1. `/events/<uuid>/edit/` aufrufen.
2. Formular ist mit dekodierten Werten gefüllt (`safe_decrypt`).
3. Ein Textfeld ändern (z.B. „Notiz" auf „Folgekontakt").
4. Submit.

**Erwartetes Ergebnis:**
- Redirect 302 → `/events/<uuid>/`.
- Flash „Ereignis wurde aktualisiert."
- Neuer `EventHistory`-Eintrag mit `action=UPDATE`, `data_before` (alter Wert) und `data_after` (neuer Wert).
- `event.updated_at` aktualisiert.
- Detail-View zeigt neuen Wert.

**DSGVO/Security-Note:**
- `remove_restricted_fields` entfernt Felder oberhalb der User-Sensitivity vor Update; restriktive Felder bleiben unverändert per Re-Insert aus `event.data_json`.

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-06 — Optimistic-Concurrency-Konflikt (2 Tabs)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C/F/S | ⚪ ||

**Voraussetzung:** ENT-EVT-05 (existierendes Event).

**Vorbereitung:**
- Event-UUID, zwei parallele Browser-Sessions (Tab A + Tab B oder Inkognito).

**Schritte:**
1. Tab A: `/events/<uuid>/edit/` → Formular geladen, `expected_updated_at` (hidden) = T0.
2. Tab B: dieselbe URL → Formular geladen, `expected_updated_at` = T0.
3. Tab A: Feld ändern → Submit. Erfolg, Event-`updated_at` = T1.
4. Tab B: Anderes Feld ändern → Submit (mit veraltetem `expected_updated_at=T0`).
5. Tab B als HTMX-Request: HTTP 409 mit JSON `{error: "conflict", server_state: {data_json, updated_at, document_type_name}, client_expected: T0}`.
6. Tab B als Standard-Browser-Submit: Redirect → `/events/<uuid>/edit/` mit Fehler-Flash, Konflikt-Toast.

**Erwartetes Ergebnis:**
- `check_version_conflict` in `update_event` wirft `ValidationError`.
- Bei JSON/HTMX-Accept: 409 mit `filtered_server_data_json(user, event)` (sensitivity-gefiltert).
- Bei Browser: Flash-Error + Redirect.
- AuditLog: kein `event_update` für den Konflikt-Versuch.

**DSGVO/Security-Note:**
- `filtered_server_data_json` blendet Felder aus, die der Konflikt-Resolver-User nicht sehen darf — kein Leak höher klassifizierter Inhalte über die 409-Response (Refs #575).

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-07 — Event als HIGH-Sensitivity → nur Lead/Admin sieht Inhalt

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | leitung + fachkraft | C | ⚪ ||

**Voraussetzung:** DocumentType mit `sensitivity=high`.

**Vorbereitung:**
- DocumentType „Krisenintervention" mit `sensitivity=high` vorhanden.

**Schritte:**
1. Als `leitung` einloggen.
2. `/events/new/` → DocumentType „Krisenintervention" wählen → Event mit sensiblem Inhalt anlegen.
3. UUID notieren, Logout.
4. Als `fachkraft` (STAFF-Rolle, `ROLE_MAX_SENSITIVITY=1=ELEVATED`) einloggen.
5. `/events/<uuid>/` aufrufen.
6. Zeitstrom `/` aufrufen — prüfen, ob Event in Liste auftaucht.

**Erwartetes Ergebnis:**
- Schritt 5: HTTP 404 (nicht 403!) — `get_visible_event_or_404` liefert Http404 statt PermissionDenied, damit die Existenz nicht geleakt wird.
- Schritt 6: Event taucht im Zeitstrom NICHT auf (`Event.objects.visible_to(user)` filtert).
- Als `leitung` (LEAD-Rolle): Event ist sichtbar, Detail-View rendert vollständig.

**DSGVO/Security-Note:**
- 404 statt 403 verhindert Metadaten-Leak (Pseudonym, DocumentType-Name) an niedriger eingestufte Rollen.
- `ROLE_MAX_SENSITIVITY`: ASSISTANT=0, STAFF=1, LEAD/ADMIN=2.

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-08 — Event mit Case-Zuordnung anlegen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C | ⚪ ||

**Voraussetzung:** ENT-CLIENT-02 (Klient:in qualifiziert), Case zur Klient:in vorhanden.

**Vorbereitung:**
- Klient-UUID, Case-UUID; `case.client_id == client.pk`.

**Schritte:**
1. `/events/new/?client=<client-uuid>` aufrufen.
2. Im Case-Dropdown den vorhandenen Case auswählen (Form lädt Cases via `cases_for_client`-API).
3. DocumentType, Felder ausfüllen, Submit.

**Erwartetes Ergebnis:**
- Event speichert sich mit `case_id=<case.pk>`.
- Activity-Log mit Verbindung zum Case.
- **Negativer Pfad:** Anderer Klient + Case einer fremden Klient:in → ValidationError „Person des Ereignisses passt nicht zur Person des Falls."
- **Negativer Pfad:** Anonymes Event + Case → ValidationError (siehe EVT-04).

**DSGVO/Security-Note:**
- Cross-Facility-Schutz: `case.facility_id != facility.pk` → ValidationError „Fall gehört nicht zur selben Einrichtung wie das Ereignis."

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-09 — Event löschen → Lösch-Antrag bei qualifiziertem Client

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C | ⚪ | — (siehe DEL-Block) |

**Voraussetzung:** ENT-EVT-01 (Event mit `client.contact_stage=QUALIFIED`).

**Vorbereitung:**
- Event von eigener User-Hand (oder Lead-Login).

**Schritte:**
1. `/events/<uuid>/delete/` aufrufen → Confirm-Page.
2. Begründung eintragen, Bestätigen.

**Erwartetes Ergebnis:**
- Wenn `client.contact_stage == QUALIFIED`: KEIN Direct-Delete, sondern `DeletionRequest` (Status PENDING) wird angelegt → Flash „Löschantrag wurde gestellt und muss von einer Leitung genehmigt werden." Redirect → `/`.
- Wenn `client.contact_stage != QUALIFIED` ODER anonymes Event: `soft_delete_event` direkt → Flash „Ereignis wurde gelöscht.", AuditLog `delete`.
- StaffRequiredMixin: Assistants haben hier keinen Zugriff (403 oder Redirect to login je nach Mixin).
- `dispatch`-Check: Staff darf nur eigene Events löschen, Lead/Admin alles.

**DSGVO/Security-Note:**
- Vier-Augen-Prinzip auf qualifizierten Daten — siehe DEL-Block für Approve/Reject-Workflow.

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-10 — Event-Detail blendet Felder oberhalb der Rolle aus

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C | ⚪ ||

**Voraussetzung:** DocumentType mit gemischten Sensitivities (z.B. DocumentType `normal` mit FieldTemplates `normal`/`elevated`/`high`).

**Vorbereitung:**
- Event als Lead angelegt mit Werten in allen drei Feld-Sensitivities.

**Schritte:**
1. Als `fachkraft` (STAFF) einloggen.
2. `/events/<uuid>/` aufrufen.

**Erwartetes Ergebnis:**
- Felder mit `field_template.sensitivity=high` werden NICHT gerendert (oder als „Eingeschränkt" maskiert).
- Felder `normal` und `elevated` sichtbar.
- Effektive Sensitivity = max(doc_type, field) → `effective_sensitivity` aus `services/sensitivity.py`.
- Im Bearbeiten-Form: HIGH-Felder fehlen via `remove_restricted_fields`; beim Save bleiben die Original-Werte erhalten (Re-Insert aus `event.data_json`).

**DSGVO/Security-Note:**
- Ausgeblendete Felder werden NICHT geleert oder überschrieben — Lead/Admin sieht weiterhin den ursprünglichen Wert.

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>📎 ATT — Datei-Anhänge / File-Vault (9 Cases)</strong></summary>

**Routen:** `/attachments/`, `/events/<event-uuid>/attachments/<attachment-uuid>/download/` 
**Views:** `src/core/views/attachments.py` (`AttachmentListView`, `AttachmentDownloadView`) 
**Services:** `src/core/services/file_vault.py` (`store_encrypted_file`, `get_decrypted_file_stream`, `soft_delete_attachment_chain`, `_enforce_allowed_file_types`, `_enforce_magic_bytes`, `_run_virus_scan`), `src/core/services/virus_scan.py`, `src/core/services/encryption.py` (Fernet/MultiFernet) 
**E2E-Coverage:** `test_attachment_versioning_stage_b.py`, `test_file_vault.py`, `test_crypto_session.py` 
**Spezial-Setup:** ClamAV-Daemon muss erreichbar sein (`CLAMAV_ENABLED=True`). EICAR-String als Datei vorbereiten: `echo -n 'X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*' > eicar.txt`. DocumentType muss FILE-FieldTemplate haben.

---

### TC-ID: ENT-ATT-01 — PDF-Upload an Event

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C/F/S | ✓ | `test_file_vault.py` |

**Voraussetzung:** DocumentType mit FILE-FieldTemplate „Anhang".

**Vorbereitung:**
- Test-PDF (kleiner 1 MB) bereithalten.

**Schritte:**
1. `/events/new/` öffnen, DocumentType wählen.
2. PDF im File-Input des Felds „Anhang" auswählen.
3. Form ausfüllen, Submit.
4. Detail-View → Anhang-Liste prüfen.

**Erwartetes Ergebnis:**
- `EventAttachment`-Record mit `is_current=True`, `entry_id=<uuid>`, `sort_order=0`.
- Datei liegt als `<uuid>.enc` unter `MEDIA_ROOT/<facility-id>/` (Fernet-verschlüsselt).
- `original_filename_encrypted` ist Fernet-Token (kein Klartext-Filename auf Disk).
- `event.data_json[<slug>] = {"__files__": True, "entries": [{"id": <uuid>, "sort": 0}]}`.
- Im Detail-View: Filename, Größe, MIME sichtbar.
- ClamAV-Scan VOR Encryption (Reihenfolge in `store_encrypted_file`: Whitelist → ClamAV → Magic-Bytes → Encrypt).

**DSGVO/Security-Note:**
- Original-Filename verschlüsselt persistiert (Art. 32 DSGVO).
- Disk-Cleanup bei DB-Fehler in `store_encrypted_file` via `output_path.unlink(missing_ok=True)` (#662).

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-02 — Datei-Download (entschlüsselt im Streaming)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C/F/S | ✓ | `test_file_vault.py` |

**Voraussetzung:** ENT-ATT-01 (vorhandener Anhang).

**Vorbereitung:**
- Event-UUID + Attachment-UUID notieren.

**Schritte:**
1. `/events/<event-uuid>/` öffnen.
2. Auf Anhang-Link klicken (`/events/<event-uuid>/attachments/<att-uuid>/download/`).
3. Bei PDF: Inline-Anzeige im Browser (`Content-Disposition: inline`).
4. Mit `?download=1` aufrufen → erzwungener Download (`Content-Disposition: attachment`).

**Erwartetes Ergebnis:**
- Antwort 200 mit korrektem `Content-Type` (z.B. `application/pdf`).
- `Content-Length` = `attachment.file_size`.
- AuditLog `download` mit `target_type=EventAttachment`, `target_id=<att-uuid>`, `detail={event_id, field}`.
- Datei wird via `decrypt_file_stream` chunkweise dekodiert (kein Memory-Spike bei großen Dateien).
- Inline-Whitelist: `image/jpeg`, `image/png`, `image/gif`, `image/webp`, `application/pdf`, `text/plain`. Andere Typen (z.B. `text/html`, `image/svg+xml`) → forced `attachment` (XSS-Schutz, Issue #508).

**DSGVO/Security-Note:**
- Wenn Disk-Datei fehlt: HTTP 404 (logger.error), nicht halb-übertragene Connection-Reset.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-03 — ClamAV-positiv (EICAR-String) → Upload abgewiesen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C | ⚪ | `test_file_vault.py` |

**Voraussetzung:** ClamAV läuft (`CLAMAV_ENABLED=True`), DocumentType mit FILE-Feld.

**Vorbereitung:**
- EICAR-Datei `eicar.txt` mit Standard-Testsignatur erzeugen.

**Schritte:**
1. `/events/new/` → DocumentType wählen.
2. EICAR-Datei im FILE-Feld auswählen.
3. Submit.

**Erwartetes Ergebnis:**
- Form rendert mit ValidationError „Datei wurde von Virenscanner abgewiesen: Eicar-Test-Signature".
- KEIN `EventAttachment` angelegt, KEINE `.enc`-Datei auf Disk (rollback durch fehlgeschlagene Validation, bevor `encrypt_file` läuft).
- AuditLog `security_violation` mit `detail={reason: "virus_detected", filename: "eicar.txt", signature: "Eicar-Test-Signature"}`.
- Atomare Transaktion: Auch das Event wird NICHT angelegt (Rollback durch `transaction.atomic` in `EventCreateView.post`, Refs #584).

**DSGVO/Security-Note:**
- Fail-closed: `VirusScannerUnavailableError` (z.B. ClamAV down) → Upload ebenfalls abgewiesen, AuditLog mit `reason="virus_scanner_unavailable"` (Issue #524).
- Scanner läuft VOR Encryption — ein verschlüsselter Virus auf Disk wäre sonst nicht mehr scanbar.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-04 — ClamAV-negativ (saubere Datei) → Upload erfolgreich

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C | ⚪ | `test_file_vault.py` |

**Voraussetzung:** ClamAV läuft, harmlose Test-Datei (z.B. PDF mit Beispieltext).

**Schritte:**
1. `/events/new/` → DocumentType wählen.
2. Saubere PDF im FILE-Feld auswählen.
3. Submit.

**Erwartetes Ergebnis:**
- `scan_file` liefert `ScanResult(clean=True, infected=False)`.
- Upload läuft durch: Magic-Bytes-Check OK → Encryption → DB-Record.
- KEIN `security_violation`-AuditLog.
- Event + Attachment erscheinen in Detail-View.

**DSGVO/Security-Note:**
- Bei `CLAMAV_ENABLED=False` (Dev/Test ohne Daemon): Bypass mit `ScanResult(clean=True, infected=False)` ohne ClamAV-Kontakt — die Whitelist und Magic-Bytes-Checks bleiben trotzdem aktiv.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-05 — Datei-Versioning (Stufe B Multi-Entry)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C | ⚪ | `test_attachment_versioning_stage_b.py` |

**Voraussetzung:** ENT-ATT-01 (existierender Anhang).

**Vorbereitung:**
- Event-UUID, Anhang `v1.pdf` bereits hochgeladen (`entry_id=E1`, `sort_order=0`).

**Schritte:**
1. `/events/<uuid>/edit/` → Bearbeiten-Form.
2. Im FILE-Feld eine REPLACE-Aktion auf `entry_id=E1` durchführen (Hidden-Input `<slug>__replace__<E1>=v2.pdf`).
3. Submit.
4. Detail-View → Versionshistorie prüfen.
5. Optional: weitere Datei `v3.pdf` als ADD anhängen → neuer `entry_id=E2`.
6. Optional: REMOVE auf `entry_id=E1` → CSV `<slug>__remove=E1`.

**Erwartetes Ergebnis:**
- Nach REPLACE: Alter Attachment-Record `is_current=False`, `superseded_by=<v2-pk>`, `superseded_at=<now>`. Neuer Record `is_current=True`, `entry_id=E1` (übernommen), `sort_order=0` (übernommen).
- Nach ADD: neuer `entry_id=E2`, `sort_order = max(existing) + 1`.
- Nach REMOVE: `soft_delete_attachment_chain(event, E1, user)` setzt `deleted_at=now` auf alle Versionen der Kette E1.
- `event.data_json[<slug>]["entries"]` enthält nur noch nicht-gelöschte Heads.
- Disk-Datei der alten Version bleibt liegen (Versionshistorie) bis Event-Anonymize/Delete.

**DSGVO/Security-Note:**
- Versionshistorie ist Audit-relevant — physisches Löschen erst beim Event-Delete via `delete_event_attachments`.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-06 — MIME-Whitelist:.exe → Ablehnung + AuditLog

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C | ⚪ ||

**Voraussetzung:** `Settings.allowed_file_types` enthält pdf, jpg, png, docx (kein exe).

**Vorbereitung:**
- Test-Datei `harmless.exe` (z.B. eine umbenannte Text-Datei).

**Schritte:**
1. `/events/new/` → DocumentType + FILE-Feld.
2. `harmless.exe` auswählen.
3. Submit.

**Erwartetes Ergebnis:**
- Form-Validation (`DynamicEventDataForm.clean`) lehnt ab: „Dateityp.exe nicht erlaubt. Erlaubt: pdf, jpg, png, docx".
- Falls über Form vorbei direkt der Service aufgerufen wird (programmatisch): `_enforce_allowed_file_types` wirft `ValidationError` und schreibt AuditLog `security_violation` mit `reason="extension_not_allowed"`, `detail={extension: "exe", allowed: [...]}`.
- HTTP-Status: bleibt auf der Form (200 mit Fehler), nicht 415 — das Test-Schema beschreibt 415 für direkten Service-Bypass.
- Fail-closed: Bei leerer/fehlender Settings-Row → Default-Whitelist `DEFAULT_ALLOWED_FILE_TYPES` aus `core.constants` (Refs #771).

**DSGVO/Security-Note:**
- Doppelte Validierung (Form + Service) — direkter Service-Aufruf umgeht Form, Service-Layer ist letzte Instanz (Refs #610).

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-07 — Größenlimit: > Settings.max_file_size_mb → Ablehnung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C | ⚪ ||

**Voraussetzung:** `Settings.max_file_size_mb=10` (oder Default 10 MB), DocumentType mit FILE-Feld.

**Vorbereitung:**
- Test-Datei größer 10 MB (z.B. 15 MB Random-Bytes).

**Schritte:**
1. `/events/new/` → 15-MB-Datei auswählen.
2. Submit.

**Erwartetes Ergebnis:**
- Form-Validation wirft Fehler: „Datei zu groß (15 MB). Maximum: 10 MB".
- KEIN Upload, KEIN AuditLog.
- HTTP 200 mit Form-Error (nicht 413 — 413 wäre nur bei nginx-Layer-Limit).
- Fail-closed: Settings.DoesNotExist → `DEFAULT_MAX_FILE_SIZE_MB=10` aus Constants.

**DSGVO/Security-Note:**
- Größenlimit pro Facility konfigurierbar — Multi-Tenant-Setting.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-08 — Crypto: Fernet/MultiFernet — Round-Trip + Key-Rotation

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft ||| `test_crypto_session.py` |

**Voraussetzung:** `ENCRYPTION_KEYS` mit 2 Keys (Comma-separated).

**Vorbereitung:**
- 2 Attachment-Uploads auf demselben Event nacheinander (User A bei T0, User B bei T1).

**Schritte:**
1. User A: Datei hochladen.
2. Key-Rotation: zweiten Key in `ENCRYPTION_KEYS` als ersten setzen, alten dahinter.
3. User B: Datei hochladen → wird mit neuem Key verschlüsselt.
4. User A: alten Anhang erneut downloaden.
5. User B: neuen Anhang downloaden.

**Erwartetes Ergebnis:**
- Beide Downloads liefern Original-Bytes.
- `MultiFernet` versucht Decryption mit allen Keys (alter Key entschlüsselt alten Anhang, neuer Key den neuen).
- Encryption verwendet immer den ERSTEN Key (rotation-friendly).
- `safe_decrypt` mit `default=""` fängt `InvalidToken` ab, wenn ein Key gänzlich entfernt wurde.
- `get_fernet`-Cache (lru_cache) wird bei `override_settings` per `setting_changed`-Signal invalidiert.

**DSGVO/Security-Note:**
- Key-Rotation ohne Re-Encryption-Migration möglich (MultiFernet).
- Filename-Verschlüsselung (`encrypt_field`) verwendet dieselbe Fernet-Instanz wie Disk-Encryption.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-09 — Zentrale Anhang-Übersicht `/attachments/`

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C/F/S | ✓ ||

**Voraussetzung:** mehrere Events mit Anhängen (mind. 5).

**Schritte:**
1. `/attachments/` aufrufen.
2. Antwort: HTML-Liste der letzten 200 Anhänge der Facility, sortiert nach `-created_at`.
3. Filter `?document_type=<id>` setzen → nur Anhänge dieses DocumentType.
4. Filter `?client=<uuid>` setzen → nur Anhänge dieser Klient:in.
5. HTMX-Request (Header `HX-Request: true`) → liefert nur Partial `attachment_table.html`.

**Erwartetes Ergebnis:**
- Sensitivity-Filter VOR Slicing: nur Anhänge, deren `event.document_type.sensitivity` UND `field_template.sensitivity` in der Allow-List der Rolle liegen.
- Cap auf 200 Einträge — größere Mengen via Filter erreichbar.
- Spalten: Filename (entschlüsselt via `get_original_filename`), Dateigröße (`format_file_size`), DocumentType-Name, Pseudonym oder „—" bei anonym.
- Soft-deleted Events ausgeblendet (`event__is_deleted=False`).
- `select_related` für Event/Client/FieldTemplate/CreatedBy → keine N+1.

**DSGVO/Security-Note:**
- Sensitivity-Filter in einer einzigen Query (`Q(field_template__sensitivity="") | Q(field_template__sensitivity__in=allowed)`) — vermeidet Loop-basierte Filterung (Memory-effizient bei großen Facilities).

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>📋 WI — WorkItems / Inbox (10 Cases)</strong></summary>

**Routen:** `/workitems/`, `/workitems/new/`, `/workitems/<uuid>/`, `/workitems/<uuid>/edit/`, `/workitems/bulk-status/`, `/workitems/bulk-priority/`, `/workitems/bulk-assign/`, `/api/workitems/<uuid>/status/` 
**Views:** `src/core/views/workitems.py` (`WorkItemInboxView`, `WorkItemDetailView`), `src/core/views/workitem_actions.py` (`WorkItemCreateView`, `WorkItemUpdateView`, `WorkItemStatusUpdateView`), `src/core/views/workitem_bulk.py` (`WorkItemBulkStatusView`, `WorkItemBulkPriorityView`, `WorkItemBulkAssignView`) 
**Services:** `src/core/services/workitems.py` (`create_workitem`, `update_workitem`, `update_workitem_status`, `duplicate_recurring_workitem`, `bulk_update_workitem_status`, `bulk_update_workitem_priority`, `bulk_assign_workitems`), `src/core/services/locking.py` (`check_version_conflict`) 
**E2E-Coverage:** `test_workitem_ui.py`, `test_workitem_edit.py`, `test_workitem_due_filter.py`, `test_workitems_deletion.py` 
**Spezial-Setup:** Inbox-Cap = `WORKITEM_INBOX_CAP` (Constants). Mobile-Inbox via Viewport-Resize (Playwright `iPhone 12`).

---

### TC-ID: ENT-WI-01 — Inbox-Default-View (offen + zugewiesen)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C/F/S | ✓ | `test_workitem_ui.py` |

**Voraussetzung:** mind. 5 WorkItems der Facility (Mix offen/in_progress/done).

**Schritte:**
1. `/workitems/` aufrufen.
2. Drei Listen sichtbar: „Offen", „In Bearbeitung", „Erledigt (letzte 7 Tage)".

**Erwartetes Ergebnis:**
- „Offen": `status=OPEN` UND (`assigned_to=user` ODER `assigned_to IS NULL`) — eigene + nicht zugewiesene.
- „In Bearbeitung": dito mit `status=IN_PROGRESS`.
- „Erledigt": `status IN (DONE, DISMISSED)` UND `updated_at >= now-7d`.
- Sortierung: `due_date_bucket` (overdue=0, today=1, future=2, none=9), dann `due_date`, dann `priority_order` (URGENT=0, IMPORTANT=1, NORMAL=2), dann `-created_at`.
- Cap pro Liste: `WORKITEM_INBOX_CAP`. `*_has_more`-Flag bei Überlauf.
- HTMX-Request → Partial `inbox_content.html` (nur Inhalt, kein Layout).

**DSGVO/Security-Note:**
- Facility-Scoping über `WorkItem.objects.for_facility(facility)`.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-02 — WorkItem anlegen (Titel, Priorität, Assignee)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C/F/S | ✓ | `test_workitem_ui.py` |


**Schritte:**
1. `/workitems/new/` aufrufen.
2. Titel „Folgegespräch vereinbaren", Beschreibung, Item-Type, Priorität „important", Assignee = leitung, Due-Date in 7 Tagen.
3. Optional Klient:in zuweisen (`?client=<uuid>`).
4. Submit.

**Erwartetes Ergebnis:**
- Redirect 302 → `/workitems/`.
- Flash „Aufgabe wurde erstellt."
- WorkItem-Record mit `created_by=user`, `assigned_to=leitung`, `status=OPEN`.
- AuditLog `workitem_create` mit `target_type=WorkItem`, `target_id=<uuid>`.
- Activity-Log mit `verb=CREATED`, Summary „Aufgabe: Folgegespräch vereinbaren".
- Inbox zeigt das WorkItem in „Offen".

**DSGVO/Security-Note:**
- StaffRequiredMixin auf Create — Assistants sehen nur die Inbox, dürfen aber keine eigenen Aufgaben anlegen.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-03 — WorkItem bearbeiten

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C | ⚪ | `test_workitem_edit.py` |

**Voraussetzung:** ENT-WI-02 (existierendes WorkItem vom selben User).

**Schritte:**
1. `/workitems/<uuid>/edit/` aufrufen.
2. Titel und Beschreibung ändern, Priorität von „important" auf „urgent" setzen.
3. Submit.

**Erwartetes Ergebnis:**
- `update_workitem` schreibt geänderte Felder. AuditLog `workitem_update` mit `detail.changed_fields=["title","description","priority"]` (kein PII-Wert).
- Activity-Log mit `verb=UPDATED`.
- Permission: Lead/Admin, `created_by` oder `assigned_to` (`can_user_mutate_workitem`). Andere → 403 Forbidden.
- Optimistic-Locking via `expected_updated_at` aus Hidden-Input.

**DSGVO/Security-Note:**
- Auch Edit ist auf StaffRequiredMixin — Assistants haben Read-only auf Detail-View, aber Edit-Button erscheint nur bei `can_edit=True` (Refs #753).

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-04 — Status-Toggle via HTMX (open → in_progress → done)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C/F/S | ✓ | `test_workitem_ui.py` |

**Voraussetzung:** WorkItem `status=OPEN`, ohne `assigned_to`.

**Schritte:**
1. Inbox `/workitems/` → Karte des WorkItems.
2. Status-Button „Starten" klickt POST `/api/workitems/<uuid>/status/` mit `status=in_progress` (HTMX).
3. Erneut Status-Button → POST `status=done`.

**Erwartetes Ergebnis:**
- Schritt 2: `update_workitem_status` lockt Row via `select_for_update`, setzt `status=IN_PROGRESS` UND `assigned_to=user` (Auto-Assign), HTMX-Response 200 mit Partial `item_card.html`.
- Schritt 3: `status=DONE`, `completed_at=now`, Activity `COMPLETED`. Wenn `recurrence != NONE` → Folge-WorkItem via `duplicate_recurring_workitem` (siehe WI-09).
- Idempotenz-Guard: Doppel-Klick auf denselben Status → no-op (kein doppelter Activity-Eintrag, Refs #129/#733).
- Permission: `can_user_mutate_workitem` (Lead/Admin/Creator/Assignee). Andere → 403.
- Ungültiger Status → 400 Bad Request.

**DSGVO/Security-Note:**
- Concurrency-sicheres Update — keine Race auf zwei Tabs.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-05 — Bulk-Status: 3 WorkItems → done

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C | ⚪ | `test_workitem_ui.py` |

**Voraussetzung:** 3 WorkItems offen, alle vom User mutierbar.

**Schritte:**
1. `/workitems/` → Checkboxen aktivieren bei 3 Items.
2. Bulk-Aktion „Erledigt" wählen, POST `/workitems/bulk-status/` mit `workitem_ids[]=<3 uuids>`, `status=done`.

**Erwartetes Ergebnis:**
- `bulk_update_workitem_status` setzt `status=DONE`, `completed_at=now` für alle 3 Items.
- 3 AuditLog-Einträge `workitem_update` mit `detail.changed_fields=["status"]`, `detail.bulk=true`.
- Flash „3 Aufgaben aktualisiert."
- Bei wiederkehrenden Items (recurrence != NONE): `duplicate_recurring_workitem` läuft pro Item (Idempotenz via `recurrence_duplicated_at`-Marker, Refs #596).
- **Negativer Pfad:** Wenn nur 1 von 3 Items nicht mutierbar (z.B. fremdes Lead-Item) → HTTP 403 für gesamten Bulk-Call (kein Partial-Update, Refs #583).
- Rate-Limit: `RATELIMIT_BULK_ACTION`.

**DSGVO/Security-Note:**
- Pro-Item-Ownership-Check, damit Bulk nicht mehr erlaubt als Single-Route.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-06 — Bulk-Priorität: 3 WorkItems → urgent

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C | ⚪ ||

**Voraussetzung:** 3 WorkItems mit Priorität `normal`, alle mutierbar.

**Schritte:**
1. Inbox → 3 Items selektieren.
2. POST `/workitems/bulk-priority/` mit `priority=urgent`.

**Erwartetes Ergebnis:**
- `bulk_update_workitem_priority`: alle 3 Items auf `urgent`.
- 3 AuditLog-Einträge `workitem_update`, `changed_fields=["priority"]`.
- Sortierung in Inbox: `priority_order=URGENT=0` → 3 Items wandern an die Spitze ihres `due_date_bucket`.
- Ungültige Priorität → 400 Bad Request.

**DSGVO/Security-Note:**
- Wie WI-05: pro-Item-Ownership.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-07 — Bulk-Assign: 3 WorkItems → leitung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | leitung | C | ⚪ ||

**Voraussetzung:** 3 unzugewiesene WorkItems.

**Schritte:**
1. Inbox → 3 Items selektieren.
2. POST `/workitems/bulk-assign/` mit `assigned_to=<leitung-user-id>`.

**Erwartetes Ergebnis:**
- `bulk_assign_workitems`: alle 3 Items mit `assigned_to=leitung`.
- 3 AuditLog-Einträge `workitem_update`, `changed_fields=["assigned_to"]`.
- Bei `assigned_to=""` (leer) → Assignment wird entfernt (`assignee_or_none=None`).
- **Negativer Pfad:** Unbekannte User-ID → 400 „Unbekannte Benutzerin/Benutzer".
- **Negativer Pfad:** User aus anderer Facility → 400 (User-Filter `facility=request.current_facility`).

**DSGVO/Security-Note:**
- Cross-Facility-Schutz im Assignee-Lookup.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-08 — Filter: Fälligkeit (heute, überfällig, Woche, ohne Frist)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C/F/S | ✓ | `test_workitem_due_filter.py` |

**Voraussetzung:** WorkItems mit verschiedenen `due_date`-Werten:
- 1 überfällig (gestern, status=OPEN)
- 1 heute fällig
- 1 in 3 Tagen
- 1 in 14 Tagen
- 1 ohne Frist

**Schritte:**
1. `/workitems/` aufrufen.
2. URL-Param `?due=overdue` → nur überfällige (gestern).
3. `?due=today` → nur heute.
4. `?due=week` → heute + nächste 7 Tage (heute + 3-Tage-Item).
5. `?due=none` → nur ohne Frist.

**Erwartetes Ergebnis:**
- `_apply_filters` validiert `due` gegen `DUE_FILTER_CHOICES`. Ungültige Werte werden ignoriert.
- `overdue`: `due_date < today AND status IN (OPEN, IN_PROGRESS)` (erledigte überfällige fallen raus).
- `today`: `due_date == today`.
- `week`: `today <= due_date <= today+7d`.
- `none`: `due_date IS NULL`.
- HTMX-Request mit Filtern → Partial mit reduzierter Liste.

**DSGVO/Security-Note:**
- Keine PII-Leak im Filter.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-09 — Recurrence: Wiederkehrendes WorkItem (täglich/wöchentlich)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C | ⚪ ||

**Voraussetzung:** WorkItem mit `recurrence=WEEKLY`, `due_date=today`, `status=OPEN`.

**Schritte:**
1. WorkItem im Inbox auf `done` setzen.
2. Inbox erneut laden.

**Erwartetes Ergebnis:**
- `update_workitem_status` mit `new_status=DONE` triggert `duplicate_recurring_workitem`.
- Neues WorkItem entsteht: gleiche Felder (`title`, `description`, `priority`, `assigned_to`, `client`, `item_type`, `recurrence`), neue `due_date = today + 7d` (für WEEKLY), `status=OPEN`.
- `remind_at`-Offset bleibt erhalten (relativ zur due_date).
- Activity-Log mit Verb `CREATED`, Summary „Wiederkehrende Folgeaufgabe: \<title\>".
- **Idempotenz (Refs #596):** Quelle bekommt `recurrence_duplicated_at=now`. Ein zweites DONE→OPEN→DONE-Toggle erzeugt KEIN drittes Item.
- Recurrence-Optionen: NONE, WEEKLY, MONTHLY (calendar-aware via `_add_months`, 31.01. + 1 Monat = 28./29.02.), QUARTERLY, YEARLY.

**DSGVO/Security-Note:**
- Original-Klient:in wird mit übernommen — kein Re-Linking nötig.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-10 — Mobile-Inbox auf iPhone-Viewport

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C/S | ✓ ||

**Voraussetzung:** WorkItems vorhanden, Playwright mit `iPhone 12`-Device-Profil.

**Schritte:**
1. Browser-Viewport auf 390x844 (iPhone 12) setzen.
2. `/workitems/` aufrufen.
3. Tab-Switch zwischen „Offen / In Bearbeitung / Erledigt" (Single-Column-Layout auf Mobile).
4. Status-Button auf einer Karte tippen (HTMX-Toggle).
5. Filter-Drawer öffnen.

**Erwartetes Ergebnis:**
- Layout: Single-Column statt 3-Spalten-Grid (Tailwind-`md:grid-cols-3`).
- Tap-Targets > 44px (iOS-HIG).
- Status-Toggle funktioniert per Tap, Karte updated via HTMX-Swap (kein Full-Reload).
- Keine horizontalen Scrollbars.
- Filter-Drawer schließt nach Anwendung (Alpine.js).

**DSGVO/Security-Note:**
- Streetwork-Use-Case: Mobile-Inbox muss offline-fähige Reads liefern (siehe Offline-Cluster).

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>🗑️ DEL — Lösch-Anträge / Vier-Augen (5 Cases)</strong></summary>

**Routen:** `/deletion-requests/`, `/deletion-requests/<uuid>/review/` 
**Views:** `src/core/views/event_deletion.py` (`DeletionRequestListView`, `DeletionRequestReviewView`), `src/core/views/client_deletion.py` (Verzweigung über `target_type`) 
**Services:** `src/core/services/events/deletion.py` (`request_deletion`, `approve_deletion`, `reject_deletion`), `src/core/services/clients.py` (`approve_client_deletion`, `reject_client_deletion`), `src/core/services/events/crud.py` (`soft_delete_event`) 
**E2E-Coverage:** `test_workitems_deletion.py`, `test_client_deletion_workflow.py` 
**Spezial-Setup:** Vier-Augen-Test braucht 2 separate Sessions (Antragsteller + Reviewer). DeletionRequest existiert pro `target_type` (Event vs. Client).

---

### TC-ID: ENT-DEL-01 — Lösch-Antrag stellen (fachkraft auf qualifiziertes Event)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DeletionRequests | fachkraft | C | ⚪ | `test_workitems_deletion.py` |

**Voraussetzung:** ENT-EVT-01 (Event mit `client.contact_stage=QUALIFIED`, `created_by=user`).

**Schritte:**
1. `/events/<uuid>/delete/` aufrufen.
2. Begründung eingeben: „Doppelt erfasst, siehe Event #abc.".
3. Bestätigen.

**Erwartetes Ergebnis:**
- `EventDeleteView.post`: `event.client.contact_stage == QUALIFIED` → kein Direct-Delete, sondern `request_deletion(event, user, reason)` legt `DeletionRequest` (status=PENDING) an.
- Flash: „Löschantrag wurde gestellt und muss von einer Leitung genehmigt werden."
- Redirect → `/`.
- Event bleibt sichtbar (kein Soft-Delete).
- **Idempotenz (#530):** Zweiter Antrag auf dasselbe Event mit existierendem PENDING → derselbe Record wird zurückgegeben, kein Duplikat.

**DSGVO/Security-Note:**
- Vier-Augen-Prinzip auf qualifizierten Daten — Antrag muss von Lead/Admin reviewed werden.

**Status:** ☐ Offen

---

### TC-ID: ENT-DEL-02 — Antrags-Liste anzeigen (LEAD+)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DeletionRequests | leitung | C | ⚪ | `test_workitems_deletion.py` |

**Voraussetzung:** DEL-01 (mind. 1 PENDING-Request).

**Schritte:**
1. `/deletion-requests/` aufrufen.

**Erwartetes Ergebnis:**
- `LeadOrAdminRequiredMixin` — fachkraft/assistenz bekommen 403 oder Redirect.
- Drei Listen: PENDING, APPROVED, REJECTED.
- Pro Eintrag: Antragsteller, Datum, Reason, Target-Type (Event/Client), Link zu Review-Page.
- `select_related("requested_by", "reviewed_by")` → keine N+1.
- Listen sind als `list(...)` evaluiert für `|length`-Tag ohne extra COUNT (Refs #640).

**DSGVO/Security-Note:**
- Mandantentrennung via `for_facility`.

**Status:** ☐ Offen

---

### TC-ID: ENT-DEL-03 — Antrag genehmigen (LEAD, andere Person als Antragsteller)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DeletionRequests | leitung | C | ⚪ | `test_client_deletion_workflow.py` |

**Voraussetzung:** DEL-01 (PENDING-Request von `fachkraft`).

**Schritte:**
1. `/deletion-requests/<dr-uuid>/review/` aufrufen.
2. Review-Page zeigt Event-Detail (oder Client-Detail bei `target_type=Client`).
3. POST mit `action=approve`.

**Erwartetes Ergebnis:**
- `approve_deletion(dr, reviewer)` (atomar):
 - `soft_delete_event(event, reviewer)` → `event.is_deleted=True`, `event.data_json={}`, alle Attachments via `delete_event_attachments` (Disk-Cleanup), `EventHistory(action=DELETE, data_before=<redacted>)`, AuditLog `delete`.
 - `dr.status=APPROVED`, `dr.reviewed_by=reviewer`, `dr.reviewed_at=now`.
- Flash „Löschantrag wurde genehmigt."
- Redirect → `/` (Event) bzw. `/deletion-requests/` (Client).
- Bei `target_type=Client`: `approve_client_deletion` (eigener Service-Pfad in `services/clients.py`).
- Rate-Limit `RATELIMIT_MUTATION` auf POST.

**DSGVO/Security-Note:**
- `build_redacted_delete_history` redact PII vor Persistierung — Audit-Trail bleibt, ohne den ursprünglichen Inhalt zu rekonstruieren.

**Status:** ☐ Offen

---

### TC-ID: ENT-DEL-04 — Antrag ablehnen mit Begründung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DeletionRequests | leitung | C | ⚪ ||

**Voraussetzung:** DEL-01 (PENDING-Request).

**Schritte:**
1. `/deletion-requests/<dr-uuid>/review/` aufrufen.
2. POST mit `action=reject`.

**Erwartetes Ergebnis:**
- `reject_deletion(dr, reviewer)`: `dr.status=REJECTED`, `dr.reviewed_by=reviewer`, `dr.reviewed_at=now`.
- KEIN `soft_delete_event`.
- Flash „Löschantrag wurde abgelehnt."
- Event bleibt vollständig erhalten.
- Antragsteller erfährt das Ergebnis über die Liste (kein Mail-Notify im aktuellen Stand).

**DSGVO/Security-Note:**
- Ablehnung lässt das Event vollständig — keine Datenmanipulation durch Reject-Pfad.

**Status:** ☐ Offen

---

### TC-ID: ENT-DEL-05 — Vier-Augen-Verbot: Antragsteller versucht Selbst-Genehmigung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DeletionRequests | leitung | C | ⚪ | `test_client_deletion_workflow.py` |

**Voraussetzung:** DeletionRequest, bei dem `requested_by=leitung` (selbe Person).

**Vorbereitung:**
- Lead hat selbst einen Lösch-Antrag gestellt (z.B. via `/events/<uuid>/delete/` als Lead, weil das Event qualifizierten Klienten betrifft).

**Schritte:**
1. Als `leitung` (= Antragsteller) auf `/deletion-requests/<dr-uuid>/review/` POST mit `action=approve`.

**Erwartetes Ergebnis:**
- `DeletionRequestReviewView.post`: `dr.requested_by == request.user` → Flash-Error „Sie können Ihren eigenen Löschantrag nicht genehmigen."
- Redirect → `/deletion-requests/<dr-uuid>/review/` (kein Status-Change).
- DeletionRequest bleibt PENDING.
- KEIN `soft_delete_event`.
- Aufgabe muss durch ZWEITEN Lead/Admin genehmigt werden.
- Hinweis: Im Code wird der Check via Flash + Redirect umgesetzt, NICHT via 403 — das Test-Schema im Plan beschreibt 403 als Erwartung; das tatsächliche Verhalten ist „weiche" Ablehnung mit Redirect. Test sollte beide akzeptieren oder die Implementierung sollte auf 403 umgestellt werden.

**DSGVO/Security-Note:**
- Zentrales Compliance-Feature: Vier-Augen-Prinzip ist DSGVO Art. 5 (Integrität) und Art. 32 (Sicherheit der Verarbeitung).

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>📦 RET — Aufbewahrungsrichtlinien (2 Cases)</strong></summary>

**Routen:** `/retention/`, `/api/retention/<uuid>/approve/`, `/api/retention/<uuid>/hold/`, `/retention/bulk-approve/`, `/retention/bulk-defer/`, `/retention/bulk-reject/` 
**Views:** `src/core/views/retention.py` 
**Services:** `src/core/services/retention.py` 
**Management-Commands:** `enforce_retention`, `reencrypt_fields` 
**E2E-Coverage:** `test_retention_dashboard.py` 
**Spezial-Setup:** Backdate-Daten via SQL (`UPDATE core_event SET created_at = NOW - INTERVAL '400 days'`); `enforce_retention --simulate-date=` für Trockenlauf.

---

### TC-ID: ENT-RET-01 — Retention-Dashboard öffnen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | leitung | C/F/S | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** Backdate-Daten vorhanden (Events/Clients mit `created_at` älter als Retention-Schwelle)

**Vorbereitung:**
- Mit `leitung` einloggen.
- DB-Skript für Backdate ausgeführt.

**Schritte:**
1. `/retention/` aufrufen.
2. Tabs „Ablaufende", „Holds", „Historie" der Reihe nach anklicken.
3. Spaltenüberschriften und Sortierung prüfen.

**Erwartetes Ergebnis:**
- Liste der Daten mit ablaufender Frist (Events > 90/365/3650 Tage je Sensitivität).
- Holds-Tab listet aktive Sperren.
- Historie-Tab zeigt vergangene Approvals/Rejections inkl. Akteur:in und Zeitstempel.

**DSGVO/Security-Note:**
- Aufbewahrungsfristen aus `settings.retention_*_days` (Art. 5 Speicherbegrenzung, Art. 17 Löschpflicht).

**Status:** ☐ Offen

---

### TC-ID: ENT-RET-03 — Bulk-Defer: 3 Einträge zurückstellen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | leitung | C/F/S | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** ENT-RET-01, mindestens 3 ablaufende Einträge

**Vorbereitung:**
- `/retention/` geöffnet, Tab „Ablaufende".

**Schritte:**
1. 3 Einträge markieren.
2. „Bulk-Defer" klicken, Begründung eingeben („Klärung mit Jugendamt offen").
3. Defer-Dauer (z.B. 30 Tage) wählen, bestätigen.
4. Eintrag-Detail prüfen → Defer-Counter sehen.

**Erwartetes Ergebnis:**
- 3 Einträge fallen aus „Ablaufende" raus.
- `defer_count` pro Eintrag um 1 erhöht.
- Neue Frist = alte Frist + 30 Tage.
- Begründung in Audit gespeichert.

**DSGVO/Security-Note:**
- Defer dokumentiert Aufschub-Grund (Art. 5 Abs. 2 Rechenschaftspflicht).

**Status:** ☐ Offen

---


</details>

<details open>
<summary><strong>🔎 SRCH — Suche (6 Cases)</strong></summary>

**Routen:** `/search/`, Header-Typeahead via HTMX (`/search/typeahead/`) 
**Views:** `src/core/views/search.py` 
**Services:** `src/core/services/search.py` 
**E2E-Coverage:** `test_clients_search.py`, `test_filter_persistence_q.py`, `test_fuzzy_search.py`

---

### TC-ID: ENT-SRCH-01 — Globale Header-Suche (HTMX-Typeahead)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Suche | fachkraft | C | ⚪ | `test_clients_search.py` |

**Voraussetzung:** mit Seed-Daten (≥ 5 Klient:innen, ≥ 3 Cases, ≥ 3 Events).

**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Beliebige Seite mit Header offen.

**Schritte:**
1. Im Header-Suchfeld 3 Zeichen eines Pseudonym-Prefix tippen (z.B. „abe").
2. Auf das Typeahead-Dropdown achten.
3. Eines der Suchergebnisse klicken.

**Erwartetes Ergebnis:**
- Dropdown öffnet sich nach 300ms HTMX-Debounce.
- Top-3 Treffer pro Typ (Klient, Case, Event) gruppiert sichtbar.
- Klick navigiert zur Detail-Seite des Treffers.
- Keine Cross-Facility-Treffer.

**DSGVO/Security-Note:**
- Typeahead respektiert RLS (Art. 32 — Datenintegrität).

**Status:** ☐ Offen

---

### TC-ID: ENT-SRCH-02 — Erweiterte Suche `/search/` mit Volltext + Filter

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Suche | fachkraft | C/F/S | ⚪ | `test_clients_search.py` |


**Vorbereitung:**
- `/search/` aufrufen.

**Schritte:**
1. Suchbegriff eingeben (z.B. „Jugendhilfe").
2. Filter „Typ = Case" + „Stage = aktiv" wählen.
3. „Suchen" klicken.
4. Treffer prüfen.

**Erwartetes Ergebnis:**
- Volltext-Treffer in Notizen/Tags/Pseudonym werden gefunden.
- Filter werden korrekt angewendet (nur Cases mit Stage „aktiv").
- Trefferzahl im Header („12 Ergebnisse").
- Sortierung nach Relevanz (Default).

**DSGVO/Security-Note:**
- Volltextsuche umfasst nur Felder mit angemessener Vertraulichkeitsstufe (Art. 5 lit. f Vertraulichkeit).

**Status:** ☐ Offen

---

### TC-ID: ENT-SRCH-03 — Fuzzy-Search: Tippfehler trotzdem Treffer

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Suche | fachkraft | C | ⚪ | `test_fuzzy_search.py` |

**Voraussetzung:** Klient mit Pseudonym „Schwalbe" vorhanden.

**Vorbereitung:**
- `/search/` geöffnet.

**Schritte:**
1. Pseudonym mit Tippfehler tippen: „Schwlabe".
2. Suchen.
3. Trefferliste prüfen.

**Erwartetes Ergebnis:**
- „Schwalbe" erscheint mit „≈ Match" oder Edit-Distance-Hinweis.
- Mindestens ein Fuzzy-Treffer (PostgreSQL `pg_trgm` oder Levenshtein).
- Score < 1.0 sichtbar.

**DSGVO/Security-Note:**
- Fuzzy darf keine Cross-Facility-Treffer liefern (RLS-Einhaltung).

**Status:** ☐ Offen

---

### TC-ID: ENT-SRCH-04 — Filter-Persistence: `?q=…&stage=…` bleibt bei Pagination

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Suche | fachkraft | C | ⚪ | `test_filter_persistence_q.py` |

**Voraussetzung:** ≥ 30 passende Treffer (für Pagination)

**Vorbereitung:**
- `/search/?q=Beratung&stage=aktiv` aufrufen.

**Schritte:**
1. Auf Seite 1 prüfen: URL hält Parameter.
2. „Nächste Seite" klicken.
3. URL prüfen.
4. Über „Zurück" blättern.

**Erwartetes Ergebnis:**
- URL bleibt: `/search/?q=Beratung&stage=aktiv&page=2`.
- Treffer auf Seite 2 entsprechen weiterhin dem Filter.
- Filter-Inputs zeigen weiterhin „Beratung" / „aktiv".

**DSGVO/Security-Note:**
- Suchparameter dürfen nicht mit personenbezogenen Daten in Server-Logs landen (interne Konvention: Pseudonyme statt Klarnamen).

**Status:** ☐ Offen

---

### TC-ID: ENT-SRCH-05 — Mobile-Suche (iPhone-Viewport)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Suche | fachkraft | C | ⚪ | `test_clients_search.py` |

**Voraussetzung:** Browser-DevTools mit iPhone-13-Profil (390×844).

**Vorbereitung:**
- Mobile-Viewport gesetzt.
- Eingeloggt.

**Schritte:**
1. Header-Burger-Menü öffnen.
2. „Suche" antippen.
3. Suchbegriff tippen.
4. Treffer aufrufen.

**Erwartetes Ergebnis:**
- Suchfeld nimmt volle Breite ein.
- Touch-Targets ≥ 44px.
- Tastatur überdeckt Treffer nicht (scroll-into-view).
- Treffer-Detail responsive.

**DSGVO/Security-Note:**
- Auf Mobilgeräten kein Caching der Suche im Browser-History (Cache-Control: no-store für `/search/`).

**Status:** ☐ Offen

---

### TC-ID: ENT-SRCH-06 — Cross-Facility-Verbot: Nur eigene Facility (RLS)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Suche | fachkraft | C | ⚪ | `test_clients_search.py` |

**Voraussetzung:** Zwei Facilities A/B mit je eigenen Klient:innen; Pseudonym „Mustermann" existiert in beiden.

**Vorbereitung:**
- Mit Fachkraft aus Facility A einloggen.

**Schritte:**
1. `/search/?q=Mustermann` aufrufen.
2. Trefferanzahl prüfen.
3. Mit `Facility-Switcher` zu Facility B wechseln (sofern Rolle erlaubt).
4. Erneut suchen.

**Erwartetes Ergebnis:**
- In Facility A nur Treffer aus Facility A (1 Treffer).
- In Facility B nur Treffer aus B.
- Direktes Aufrufen einer fremden UUID liefert 404.
- Audit: kein Daten-Leak in Logs.

**DSGVO/Security-Note:**
- RLS verhindert facilityübergreifende Sichtbarkeit (Art. 32 — Mandantentrennung). Belegt durch `src/tests/test_rls.py`.

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>📰 ZS — Zeitstrom (5 Cases)</strong></summary>

**Routen:** `/` (Home/Zeitstrom), `/zeitstrom/feed/` (HTMX-Partial) 
**Views:** `src/core/views/zeitstrom.py` 
**Services:** `src/core/services/feed.py` 
**E2E-Coverage:** `test_zeitstrom_enrichment.py`, `test_zeitstrom_events.py`, `test_zeitstrom_filter_bug.py`

---

### TC-ID: ENT-ZS-01 — Zeitstrom-Feed (Home `/`, neueste Aktivitäten)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Zeitstrom | fachkraft | C | ⚪ | `test_zeitstrom_events.py` |

**Voraussetzung:** ≥ 10 Aktivitäten in den letzten 7 Tagen.

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/` aufrufen.
2. Feed durchscrollen.
3. Auf Eintragsdetail klicken (z.B. Event).

**Erwartetes Ergebnis:**
- Liste mit Aktivitäten in absteigender Zeitordnung.
- Pro Eintrag: Icon, Akteur:in, Aktion, Bezugsobjekt, Zeit (relative Anzeige „vor 2 h").
- Klick öffnet Detail des Bezugsobjekts.

**DSGVO/Security-Note:**
- Feed enthält keine Klarnamen, nur Pseudonyme (Art. 4 Nr. 5).

**Status:** ☐ Offen

---

### TC-ID: ENT-ZS-02 — Pagination des Feeds (HTMX-Polling/Infinite-Scroll)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Zeitstrom | fachkraft | C | ⚪ | `test_zeitstrom_events.py` |

**Voraussetzung:** ≥ 50 Aktivitäten

**Vorbereitung:**
- `/` geöffnet.

**Schritte:**
1. Bis zum Ende scrollen.
2. Auf „Mehr laden" oder Auto-Polling warten.
3. Netzwerk-Tab beobachten.

**Erwartetes Ergebnis:**
- HTMX-Request an `/zeitstrom/feed/?page=2` löst aus.
- Neue Einträge werden ans Ende angehängt.
- Kein Full-Page-Reload.
- Bei Live-Polling: alle 30 s Request, neue Einträge oben hinzufügen.

**DSGVO/Security-Note:**
- Polling-Frequenz nicht zu kurz (Performance + Logging-Volumen).

**Status:** ☐ Offen

---

### TC-ID: ENT-ZS-03 — Filter nach Activity-Typ

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Zeitstrom | fachkraft | C | ⚪ | `test_zeitstrom_filter_bug.py` |

**Voraussetzung:** ENT-ZS-01

**Vorbereitung:**
- Feed offen.

**Schritte:**
1. Filter-Chip „Events" wählen.
2. Filter-Chip „Cases" wählen (multi-select oder umschalten).
3. Filter zurücksetzen.

**Erwartetes Ergebnis:**
- URL aktualisiert sich (`?type=event` o. ä.).
- Liste zeigt nur passende Aktivitäten.
- Reset-Button stellt Default-Ansicht wieder her.

**DSGVO/Security-Note:**
- Filter ändert nicht die Sichtbarkeitsregeln, nur die Anzeigemenge.

**Status:** ☐ Offen

---

### TC-ID: ENT-ZS-04 — Sensitivity-Filter: Niedrige Rolle sieht keine sensitiven Events

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Zeitstrom | assistenz | C | ⚪ | `test_zeitstrom_enrichment.py` |

**Voraussetzung:** Sensitives Event (`sensitivity='qualified'`) im Feed.

**Vorbereitung:**
- Mit `assistenz` einloggen.
- Vergleichbar dazu mit `leitung` einloggen, um Diff zu prüfen.

**Schritte:**
1. Mit `assistenz`: `/` aufrufen.
2. Feed durchsuchen — gibt es das sensitive Event?
3. Mit `leitung`: identische Aktion.
4. Direktaufruf der Event-UUID als `assistenz`.

**Erwartetes Ergebnis:**
- `assistenz` sieht das sensitive Event NICHT im Feed (oder nur als Stub „Eintrag verborgen").
- `leitung` sieht es vollständig.
- Direktaufruf als `assistenz`: 403/404.

**DSGVO/Security-Note:**
- Need-to-Know-Prinzip (Art. 5 lit. f Vertraulichkeit, Art. 32). Klassifikation `qualified` = nur Leitung+.

**Status:** ☐ Offen

---

### TC-ID: ENT-ZS-05 — Partial-Refresh: Live-Update bei neuem Event ohne Reload

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Zeitstrom | fachkraft | C | ⚪ | `test_zeitstrom_events.py` |

**Voraussetzung:** ENT-ZS-01

**Vorbereitung:**
- Tab A: `/` als `fachkraft`.
- Tab B: Event-Create als `leitung`.

**Schritte:**
1. In Tab B neues Event anlegen.
2. In Tab A 30 s warten (oder manuelles HTMX-Trigger).
3. Feed in Tab A beobachten.

**Erwartetes Ergebnis:**
- HTMX-Polling/Trigger lädt neuen Eintrag.
- Eintrag erscheint oben mit Highlight (z.B. kurzes Aufblitzen).
- Kein Full-Reload, Scroll-Position bleibt.

**DSGVO/Security-Note:**
- Live-Updates respektieren Sensitivity-Regeln (vgl. ENT-ZS-04).

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>🤝 HOV — Übergabe (5 Cases)</strong></summary>

**Routen:** `/uebergabe/`, `/uebergabe/print/` 
**Views:** `src/core/views/handover.py` 
**Services:** `src/core/services/handover.py` 
**E2E-Coverage:** `test_handover.py`

---

### TC-ID: ENT-HOV-01 — Übergabe-View `/uebergabe/` mit Stats-Grid

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Übergabe | fachkraft | C | ✓ | `test_handover.py` |

**Voraussetzung:** mit ≥ 3 offenen Cases, ≥ 5 Events des laufenden Tages.

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/uebergabe/` aufrufen.
2. Stats-Grid (offene Cases, geplante Events, akute Hinweise) prüfen.
3. Sektionen lesen.

**Erwartetes Ergebnis:**
- Stats-Grid zeigt Zahlen für „Offene Cases", „Termine heute", „Eilige Hinweise".
- Sektion „Heute angekommen" / „Heute abzuschließen" / „Wichtige Hinweise" befüllt.
- Letzte Aktualisierung-Zeitstempel sichtbar.

**DSGVO/Security-Note:**
- Anzeige aus Sicht der eigenen Facility (RLS); Pseudonyme statt Klarnamen.

**Status:** ☐ Offen

---

### TC-ID: ENT-HOV-02 — Filter: nach Schicht (Vormittag/Nachmittag), Rolle

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Übergabe | leitung | C | ✓ | `test_handover.py` |

**Voraussetzung:** ENT-HOV-01, Cases mit Schichtzuordnung.

**Vorbereitung:**
- `/uebergabe/` offen.

**Schritte:**
1. Filter „Schicht = Vormittag" wählen.
2. Stats neu laden lassen (HTMX-Refresh).
3. Filter „Rolle = fachkraft" zusätzlich aktivieren.
4. Filter zurücksetzen.

**Erwartetes Ergebnis:**
- Liste reduziert sich auf Vormittagsschicht.
- Mit zusätzlichem Rollenfilter: nur Cases von Fachkräften.
- URL-Parameter (`?schicht=vormittag&role=fachkraft`) bleiben erhalten.
- Reset stellt vollständige Übersicht wieder her.

**DSGVO/Security-Note:**
- Filter sind Anzeigeoptionen — keine Umgehung der Berechtigungen.

**Status:** ☐ Offen

---

### TC-ID: ENT-HOV-03 — Print-CSS: `Strg+P` rendert druckfertiges Layout

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Übergabe | fachkraft | C/F/S | ⚪ | `test_handover.py` |

**Voraussetzung:** ENT-HOV-01

**Vorbereitung:**
- `/uebergabe/` offen.

**Schritte:**
1. `Strg+P` drücken (oder Print-Button klicken).
2. Druckvorschau prüfen.
3. Alternativ `/uebergabe/print/` direkt aufrufen.

**Erwartetes Ergebnis:**
- Druckvorschau ohne Header/Sidebar/Buttons.
- Kompaktes Layout mit klaren Abschnitten.
- Schwarzweiß-tauglich, kein Hintergrund-Cyan.
- Seitenumbruch sinnvoll.
- Kopfzeile mit Datum und Facility-Name.

**DSGVO/Security-Note:**
- Druckausgabe enthält keine Klarnamen — nur Pseudonyme + Schichtnotizen. Hinweis im Footer „Vertraulich — DSGVO".

**Status:** ☐ Offen

---

### TC-ID: ENT-HOV-04 — Mobile-Übergabe (essentielle Info auf iPhone)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Übergabe | fachkraft | C | ✓ | `test_handover.py` |

**Voraussetzung:** Browser-DevTools mit iPhone-Profil (390×844). Stand-by-Workflow (Bereitschaft).

**Vorbereitung:**
- Mobile-Viewport.
- Eingeloggt als `fachkraft`.

**Schritte:**
1. `/uebergabe/` aufrufen.
2. Stats-Grid + Top-3-Hinweise sichtbar?
3. Akkordeons aufklappen (Cases / Hinweise).
4. „Anrufen"-Link an Klient prüfen (sofern vorhanden) — `tel:` öffnet Wählvorgang.

**Erwartetes Ergebnis:**
- Stats-Grid stapelt sich vertikal.
- Touch-Targets ≥ 44px.
- Akkordeon mit weicher Animation.
- Eilige Hinweise „über der Falte".
- `tel:`-Link funktioniert (Stand-by-Anruf möglich).

**DSGVO/Security-Note:**
- Mobile-Caching deaktiviert (`Cache-Control: no-store`) für `/uebergabe/`.

**Status:** ☐ Offen

---

### TC-ID: ENT-HOV-05 — Empty-State: Keine offenen Cases → freundliche Meldung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Übergabe | fachkraft | C | ✓ | `test_handover.py` |

**Voraussetzung:** Frische Facility ohne Cases (oder alle Cases geschlossen).

**Vorbereitung:**
- Test-Facility mit leerem Zustand vorbereiten (`make seed` Variante mit `--scale=empty` o. ä.).

**Schritte:**
1. `/uebergabe/` aufrufen.
2. Inhalte beobachten.

**Erwartetes Ergebnis:**
- Statt leerer Liste: freundliche Empty-State-Karte („Keine offenen Cases — schöne Schicht!").
- Illustration oder Emoji (kontextabhängig).
- CTA: „Neuen Case anlegen" (sofern Rolle berechtigt).

**DSGVO/Security-Note:**
- Empty-State enthält keine Hinweise auf andere Facilities.

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>📊 STAT — Statistik (8 Cases)</strong></summary>

**Routen:** `/statistics/`, `/statistics/chart-data/`, `/statistics/export/csv/`, `/statistics/export/pdf/`, `/statistics/export/jugendamt/` 
**Views:** `src/core/views/statistics.py` 
**Services:** `src/core/services/statistics.py`, `src/core/services/export.py` 
**Management-Commands:** `create_statistics_snapshots` 
**E2E-Coverage:** `test_export_statistics.py`, `test_statistics_charts.py`, `test_statistics_dashboard.py`, `test_statistics_snapshot.py`

---

### TC-ID: ENT-STAT-01 — Statistik-Dashboard `/statistics/`

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_statistics_dashboard.py` |

**Voraussetzung:** Seed-Daten mit verteiltem Eventaufkommen über mehrere Monate.

**Vorbereitung:**
- Mit `leitung` einloggen.

**Schritte:**
1. `/statistics/` aufrufen.
2. KPI-Karten (Cases gesamt, neue Klient:innen, Events-Anzahl) lesen.
3. Charts-Sektion einsehen.

**Erwartetes Ergebnis:**
- KPI-Karten gefüllt mit korrekten Zahlen für aktuelle Periode.
- Charts (Bar/Line) gerendert mit Chart.js.
- Tabs für Zeitraum (Tag/Woche/Monat/Quartal/Jahr).

**DSGVO/Security-Note:**
- Statistik nur aggregiert, keine Einzelfall-Identifikation (Art. 89 Statistikprivileg).

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-02 — Charts (Bar/Line) — Filter Q1/Q2/Q3/Q4

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C/F/S | ⚪ | `test_statistics_charts.py` |

**Voraussetzung:** ENT-STAT-01, Daten über alle 4 Quartale verteilt.

**Vorbereitung:**
- `/statistics/` offen.

**Schritte:**
1. Quartal-Filter „Q1" wählen.
2. Charts re-rendern beobachten.
3. Q2/Q3/Q4 nacheinander durchklicken.
4. „Ganzes Jahr" zurücksetzen.

**Erwartetes Ergebnis:**
- Charts aktualisieren sich pro Quartal (Bar zeigt Monate, Line zeigt Verlauf).
- Achsenbeschriftung sinnvoll (Jan/Feb/März für Q1).
- Tooltip beim Hover zeigt exakte Werte.
- Browser-Übergreifend (C/F/S) konsistent.

**DSGVO/Security-Note:**
- Charts liefern aggregierte Werte, kein Reverse-Engineering möglich (k-Anonymität in Praxis).

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-04 — CSV-Export `/statistics/export/csv/` (LEAD+)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_export_statistics.py` |

**Voraussetzung:** ENT-STAT-01

**Vorbereitung:**
- `/statistics/` offen, Filter gesetzt (z.B. Q1/2026).

**Schritte:**
1. „CSV-Export" klicken.
2. Datei herunterladen.
3. In Tabellenkalkulation (LibreOffice) öffnen.
4. Zeilenanzahl + Spaltenüberschriften prüfen.

**Erwartetes Ergebnis:**
- Datei `statistics-2026-Q1.csv` (oder ähnlich).
- UTF-8 mit BOM für Excel-Kompatibilität.
- Spalten: Periode, Metrik, Wert, Facility.
- Zeilenanzahl entspricht Filter.

**DSGVO/Security-Note:**
- Export nur Aggregate. Filename nicht personenbezogen. CSV-Inhalt enthält keine Klarnamen oder UUIDs einzelner Klient:innen.

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-05 — PDF-Export `/statistics/export/pdf/`

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_export_statistics.py` |

**Voraussetzung:** ENT-STAT-01

**Vorbereitung:**
- `/statistics/` offen, Filter Q1/2026.

**Schritte:**
1. „PDF-Export" klicken.
2. Download abwarten.
3. PDF in PDF-Viewer öffnen.
4. Inhalte prüfen.

**Erwartetes Ergebnis:**
- PDF mit Logo-Header, Facility-Name, Berichtszeitraum.
- KPI-Tabelle + Charts (statisch eingebettet als PNG/Vector).
- Footer mit Druckdatum + Aktor:in.
- Maschinenlesbare Metadaten (Title, Author).

**DSGVO/Security-Note:**
- PDF-Metadaten enthalten keinen Klarnamen, nur Username/Rolle (z.B. „Erstellt durch leitung@facility-A").

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-06 — Jugendamt-Export (strukturierter Bericht)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_export_statistics.py` |

**Voraussetzung:** Daten für Berichtsperiode (z.B. Halbjahr).

**Vorbereitung:**
- `/statistics/` offen.

**Schritte:**
1. „Jugendamt-Export" wählen.
2. Berichtsperiode auswählen.
3. Format (CSV/PDF) wählen.
4. Download.

**Erwartetes Ergebnis:**
- Strukturierter Bericht nach kommunalem Schema (z.B. §31/§32 SGB VIII Kennzahlen).
- Kategorien wie „Erstkontakte", „Beratungen", „Weitervermittlungen".
- Periode korrekt.

**DSGVO/Security-Note:**
- Übermittlung an Behörde rechtsgrundlagengestützt (§ 79a SGB VIII / § 35a SGB I); Bericht enthält nur Aggregate.

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-07 — Chart-Data-API `/statistics/chart-data/` als JSON

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_statistics_charts.py` |

**Voraussetzung:** ENT-STAT-01

**Vorbereitung:**
- `/statistics/` offen, DevTools Network-Tab.

**Schritte:**
1. Filter ändern → Chart re-rendert.
2. Im Network-Tab den Request `/statistics/chart-data/?...` finden.
3. Response prüfen (JSON-Struktur).
4. Direkt aufrufen ohne Auth (Logout) — sollte 302/403 zurückgeben.

**Erwartetes Ergebnis:**
- JSON-Response mit `labels`, `datasets`, `meta`.
- Chart.js konsumiert ohne Transformation.
- Ohne Auth: Login-Redirect.

**DSGVO/Security-Note:**
- API erfordert Authentifizierung; CSRF-geschützt; respektiert Facility-Scope.

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-08 — Rollen-Sicht: assistenz darf Stat nicht öffnen → 403

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | assistenz | C | ⚪ | `test_statistics_dashboard.py` |


**Vorbereitung:**
- Mit `assistenz` einloggen.

**Schritte:**
1. Direktaufruf `/statistics/` als `assistenz`.
2. Direktaufruf `/statistics/export/csv/`.
3. Mit `fachkraft` erneut versuchen (sofern Policy = LEAD+).

**Erwartetes Ergebnis:**
- `assistenz`: 403 oder Redirect mit Fehlermeldung „Nicht berechtigt".
- `fachkraft` ggf. ebenfalls 403, da Statistik LEAD+ (laut Header).
- Audit-Eintrag „access_denied".

**DSGVO/Security-Note:**
- Need-to-Know (Art. 32). Statistik nur für Leitung/Admin → minimiert Profilrisiko.

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-09 — Performance: > 1000 Events → Stat lädt < 3 s (Smoke)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_statistics_dashboard.py` |

**Voraussetzung:** Seed mit `--scale=large` (≥ 1000 Events).

**Vorbereitung:**
- Browser-DevTools mit Performance-Tab.
- Cache leer (Hard-Reload).

**Schritte:**
1. `/statistics/` öffnen, Performance-Aufnahme starten.
2. Time-to-Interactive ablesen.
3. Filter wechseln (Q1 → Q2) → erneut messen.

**Erwartetes Ergebnis:**
- Initial-Load < 3 s (Time to Interactive).
- Filterwechsel < 1 s (HTMX-Partial via Snapshot statt Live-Aggregat).
- Kein N+1 in Slow-Query-Log (PostgreSQL).

**DSGVO/Security-Note:**
- Performance via Snapshots (`StatisticsSnapshot`-Tabelle), nicht via Live-Aggregat über Klientendaten.

**Status:** ☐ Offen

---


</details>

<details open>
<summary><strong>🔍 AUDIT — Audit-Log (5 Cases)</strong></summary>

**Routen:** `/audit/`, `/audit/<uuid>/` 
**Views:** `src/core/views/audit.py` (AuditLogListView, AuditLogDetailView) 
**Services:** `src/core/services/audit.py`, `src/core/services/audit_hash.py` 
**Models:** `src/core/models/audit.py` (AuditLog, append-only, immutable) 
**E2E-Coverage:** `test_audit.py`, `test_audit_detail.py` 
**Spezial-Setup:** Append-Only-Probe via `python manage.py shell`. HMAC-Key aus Settings (`AUDIT_HMAC_KEY`).

---

### TC-ID: ENT-AUDIT-01 — Audit-Log Liste mit Pagination + Filter

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Audit | admin | C/F/S | ⚪ | `test_audit.py` |

**Voraussetzung:** einige Aktionen geloggt (Login, Klient anlegen, Event löschen).

**Vorbereitung:**
- Mit admin einloggen.
- In zweiter Browser-Session als fachkraft einloggen, einen Klient anlegen und ein Event löschen — damit Audit-Einträge entstehen.

**Schritte:**
1. `/audit/` aufrufen.
2. Pagination prüfen (max. 50 Einträge pro Seite).
3. Filter nach Action-Type, User, Datumsbereich anwenden (HTMX-Partial-Update).
4. Sortierung der Spalte `timestamp` prüfen (absteigend = neueste zuerst).

**Erwartetes Ergebnis:**
- Liste mit Spalten User, Action, Timestamp, IP, Target-ID, Facility.
- Pagination via `?page=N`, max. 50 Einträge/Seite.
- Filter funktionieren als HTMX-Partial (kein Full-Page-Reload).
- Sortierung absteigend nach `timestamp` (neueste zuerst).

**DSGVO/Security-Note:**
- Audit-Log nur für ADMIN sichtbar (Art. 32 Zweckbindung, RLS).
- Keine Klartext-Emails in Action-Targets sichtbar (HMAC-Hash).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUDIT-02 — Detail-View zeigt Diff vorher/nachher

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Audit | admin | C/F/S | ⚪ | `test_audit_detail.py` |

**Voraussetzung:** mindestens ein UPDATE-Eintrag im Audit-Log (z.B. Klient bearbeitet).

**Vorbereitung:**
- Als fachkraft einen Klient bearbeiten (Vorname ändern). Damit entsteht ein UPDATE-Audit mit `before`/`after`-Snapshot.

**Schritte:**
1. `/audit/` aufrufen, UPDATE-Eintrag suchen, anklicken.
2. Detail-View `/audit/<uuid>/` öffnen.
3. Alle Felder prüfen: User, Action, Timestamp, IP, User-Agent, Target-Model, Target-ID, Facility, `before`-Snapshot, `after`-Snapshot, HMAC-Signatur.
4. Diff-Darstellung prüfen (rot/grün, geänderte Felder hervorgehoben).

**Erwartetes Ergebnis:**
- Alle Felder lesbar, Snapshots als JSON oder strukturierte Tabelle.
- Diff zeigt nur geänderte Felder farblich hervorgehoben.
- HMAC-Signatur sichtbar (Validierungs-Indikator falls implementiert).

**DSGVO/Security-Note:**
- Snapshot enthält keine Klartext-Emails (HMAC-Hash an deren Stelle).
- Detail-View ist nur ADMIN zugänglich (Re-Auth/Sudo nicht erforderlich, da bereits ADMIN).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUDIT-03 — Filter nach Action-Type

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Audit | admin | C/F/S | ⚪ | `test_audit.py` |

**Voraussetzung:** Audit-Einträge mit verschiedenen Action-Types vorhanden (LOGIN, EXPORT, DELETE, CREATE, UPDATE).

**Vorbereitung:**
- Login als verschiedene User, einen Export auslösen, einen Datensatz löschen.

**Schritte:**
1. `/audit/` aufrufen.
2. Filter „Action-Type = LOGIN" anwenden — nur LOGIN-Events sichtbar.
3. Filter wechseln auf EXPORT — nur EXPORT-Events sichtbar.
4. Filter wechseln auf DELETE — nur DELETE-Events sichtbar.
5. Filter zurücksetzen (leer) — alle Events wieder sichtbar.

**Erwartetes Ergebnis:**
- Filter setzt Query-Parameter `?action=LOGIN` (HTMX-Partial).
- Liste zeigt nur Events des gewählten Action-Type.
- Combinierte Filter (Action + User + Datumsbereich) reduzieren weiter.

**DSGVO/Security-Note:**
- Filter ändern nicht die Sichtbarkeit fremder Facilities (RLS bleibt aktiv).
- Filter-Werte aus geschlossenem Choice-Set, kein freier String-Input (Injection-Schutz).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUDIT-05 — HMAC-Email-Probe (kein Klartext)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Audit | admin | C/F/S | ⚪ | `test_audit.py` |

**Voraussetzung:** `AUDIT_HMAC_KEY` in Settings gesetzt.

**Vorbereitung:**
- Test-User mit bekannter Email vorhanden (z.B. `fachkraft@example.com`).

**Schritte:**
1. Pwd-Reset für `fachkraft@example.com` über `/accounts/password_reset/` auslösen.
2. Als admin in `/audit/` den frischen LOGIN/PASSWORD_RESET-Eintrag öffnen.
3. Detail-View des Eintrags inspizieren.
4. Im `target`-Feld nach „fachkraft@example.com" suchen — darf NICHT vorhanden sein.
5. Stattdessen einen HMAC-Hash (Hex-String, 32+ Zeichen) finden.
6. In zweiter Probe: zweimal denselben Email-Hash erzeugen (zwei Pwd-Resets) → Hash deterministisch (gleicher Wert).

**Erwartetes Ergebnis:**
- Klartext-Email niemals in Audit-Snapshot, nur HMAC-Hash.
- Hash deterministisch bei gleicher Email + gleichem Key.
- Hash unbrauchbar zur Recovery (one-way).

**DSGVO/Security-Note:**
- **DSGVO Art. 32 (Pseudonymisierung):** Email als personenbezogenes Datum darf nicht in Audit-Log dauerhaft gespeichert werden.
- HMAC + geheimer Key = pseudonyme Korrelation möglich (z.B. „alle Aktionen User X"), aber kein Klartext-Recovery aus Audit-Backup.
- Bei `AUDIT_HMAC_KEY`-Rotation werden alte Korrelationen unbrauchbar (gewollt).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUDIT-06 — Pagination + Sortierung bei > 1000 Einträgen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Audit | admin | C/F/S | ⚪ | `test_audit.py` |

**Voraussetzung:** > 1000 AuditLog-Einträge in DB (per Seed/Skript).

**Vorbereitung:**
- Skript: `for i in range(1100): AuditLog.objects.create(...)` oder `make seed --scale large`.

**Schritte:**
1. `/audit/` aufrufen.
2. Erste Seite: 50 Einträge, oberster Eintrag = neuester Timestamp.
3. Pagination: Seite 2, 3, …, letzte Seite. Performance prüfen (< 500 ms pro Page).
4. Tiefe Pagination: `?page=22` direkt anspringen.
5. Letzte Seite: ältester Eintrag, Anzahl ggf. < 50.
6. Pagination-Counter: „Seite 22 von 22, 1100 Einträge gesamt".

**Erwartetes Ergebnis:**
- Sortierung absteigend (neueste zuerst) konsistent über alle Seiten.
- Pagination-Performance konstant (DB-Index auf `timestamp` greift).
- Kein N+1-Query-Problem (Debug-Toolbar prüfen).

**DSGVO/Security-Note:**
- Pagination verhindert vollständigen Audit-Dump in einem Request (DoS-Schutz).
- Audit-Liste hat keine „CSV-Export aller Einträge"-Funktion (Zweckbindung).

**Status:** ☐ Offen

---


</details>

<details open>
<summary><strong>📜 DSGVO — DSGVO-Paket (8 Cases)</strong></summary>

**Routen:** `/dsgvo/`, `/dsgvo/download/<slug>/` 
**Views:** `src/core/views/dsgvo.py` (DSGVOPackageView, DSGVODocumentDownloadView) 
**Services:** `src/core/services/dsgvo_package.py` 
**Command:** `src/core/management/commands/generate_dsgvo_package.py` 
**E2E-Coverage:** `test_dsgvo_package.py` 
**Spezial-Setup:** Sudo-Mode aktiv (Re-Auth innerhalb 15 Min). Markdown-Templates in `src/templates/dsgvo/`.

---

### TC-ID: ENT-DSGVO-01 — DSGVO-Paket öffnen (Admin + Sudo)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** Admin eingeloggt, Sudo-Token gültig (frische Re-Auth).

**Vorbereitung:**
- Als admin einloggen.
- `/sudo/` aufrufen, Passwort erneut eingeben → Sudo-Token gesetzt (15 Min gültig).

**Schritte:**
1. `/dsgvo/` aufrufen.
2. Übersicht aller 5 Templates sichtbar: Art. 13/14, Art. 28, Art. 30, Art. 32, Art. 35.
3. Pro Template: Titel, Kurzbeschreibung, Download-Button, letzter Generierungs-Timestamp.
4. Footer-Hinweis: „Templates rechtlich geprüft, keine Rechtsberatung".

**Erwartetes Ergebnis:**
- Liste aller 5 DSGVO-Templates mit Download-Links.
- Sudo-Indikator („Sudo aktiv, läuft ab um HH:MM") sichtbar.
- Templates dynamisch generiert (nicht statisch im Repo, sondern aus Facility-Daten).

**DSGVO/Security-Note:**
- **Re-Auth/Sudo (Art. 32):** Hochsensible Operation, deshalb zusätzliche Authentifizierung.
- Audit-Log-Eintrag „DSGVO_PACKAGE_VIEWED" pro Aufruf.

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-02 — Re-Auth-Loop ohne Sudo

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** Admin eingeloggt, Sudo-Token abgelaufen oder nicht gesetzt.

**Vorbereitung:**
- Als admin einloggen.
- Falls Sudo aktiv: 15 Min warten oder Cookie/Session-Sudo-Key löschen.

**Schritte:**
1. `/dsgvo/` aufrufen ohne aktiven Sudo.
2. Erwarte 403 oder Redirect zu `/sudo/?next=/dsgvo/`.
3. Auf `/sudo/` Passwort erneut eingeben.
4. Nach erfolgreicher Re-Auth automatischer Redirect zurück zu `/dsgvo/`.
5. Nach 15 Min ohne Aktivität: erneuter Aufruf → wieder Re-Auth-Loop.

**Erwartetes Ergebnis:**
- Ohne Sudo kein Zugriff auf `/dsgvo/` und `/dsgvo/download/<slug>/`.
- Redirect-Kette: `/dsgvo/` → `/sudo/?next=/dsgvo/` → nach Pwd-Eingabe → `/dsgvo/`.
- Sudo-Token TTL = 15 Min (in Settings konfiguriert).

**DSGVO/Security-Note:**
- **Sudo schützt vor Session-Hijacking:** Selbst mit gestohlenem Session-Cookie keine DSGVO-Doku-Downloads ohne Pwd.
- Failed-Sudo-Versuche werden in AuditLog (`SUDO_FAILED`) geloggt.
- Brute-Force-Limit: max. 5 Versuche/IP/Stunde.

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-03 — Verarbeitungsverzeichnis Art. 30 download

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** ENT-DSGVO-01 erfolgreich.

**Vorbereitung:**
- `/dsgvo/` mit aktivem Sudo geöffnet.

**Schritte:**
1. Auf „Art. 30 — Verarbeitungsverzeichnis" Download klicken.
2. Datei `verarbeitungsverzeichnis-art-30.md` (oder PDF) wird heruntergeladen.
3. Datei öffnen — Markdown-Struktur:
 - Verantwortlicher (Facility-Name, Adresse, Email aus Facility-Settings)
 - Datenschutzbeauftragter (DPO aus Settings)
 - Verarbeitungstätigkeiten (Klientenverwaltung, Fallakte, …)
 - Kategorien betroffener Personen + Daten
 - Empfänger, Drittländer, Löschfristen, TOMs-Verweis
4. Generierungs-Timestamp im Footer.

**Erwartetes Ergebnis:**
- Markdown rendert korrekt, alle Platzhalter ersetzt (kein `{{ facility_name }}` mehr).
- Inhalt facility-spezifisch (eigene Adresse, eigener DPO).
- Audit-Log-Eintrag `DSGVO_EXPORT` mit slug=`verarbeitungsverzeichnis-art-30`.

**DSGVO/Security-Note:**
- **Art. 30 ist Pflicht-Doku:** Jeder Verantwortliche muss ein Verarbeitungsverzeichnis führen.
- Template ist juristisch geprüft, aber facility-spezifische Felder müssen im Admin gepflegt werden (Hinweis-Banner).

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-04 — DSFA-Template Art. 35 download

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** ENT-DSGVO-01 erfolgreich.

**Vorbereitung:**
- `/dsgvo/` mit aktivem Sudo geöffnet.

**Schritte:**
1. Auf „Art. 35 — DSFA (Datenschutz-Folgenabschätzung)" Download klicken.
2. Datei `dsfa-art-35.md` herunterladen.
3. Inhalt prüfen:
 - Beschreibung der Verarbeitung
 - Notwendigkeit + Verhältnismäßigkeit
 - Risiken für Betroffene (Diskriminierung, Identitätsdiebstahl, …)
 - Abhilfemaßnahmen (TOMs, Pseudonymisierung, RLS)
 - Bewertung (Risiko hoch/mittel/niedrig)
4. Verweis auf Art. 35 Abs. 7 (Pflichtfelder).

**Erwartetes Ergebnis:**
- DSFA-Template strukturiert nach Art. 35 Abs. 7.
- Sozialarbeit-spezifische Risiken vorausgefüllt (besonders schutzbedürftige Personen, Gesundheitsdaten).
- Audit-Eintrag mit slug=`dsfa-art-35`.

**DSGVO/Security-Note:**
- **Art. 35 ist Pflicht für Sozialdaten:** Hohes Risiko für Rechte und Freiheiten (Kategorie „besonders schutzbedürftige Personen").
- DPO-Konsultation in Template als Schritt verankert.

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-05 — AV-Vertrag Art. 28 download

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** ENT-DSGVO-01 erfolgreich.

**Vorbereitung:**
- `/dsgvo/` mit aktivem Sudo geöffnet.

**Schritte:**
1. Auf „Art. 28 — AV-Vertrag (Auftragsverarbeitung)" klicken.
2. Datei `av-vertrag-art-28.md` herunterladen.
3. Inhalt prüfen:
 - Auftraggeber (Verantwortlicher) + Auftragsverarbeiter (z.B. Hosting-Provider)
 - Gegenstand, Dauer, Art der Verarbeitung
 - Pflichten des Auftragsverarbeiters (Art. 28 Abs. 3 a-h)
 - Subunternehmer-Klausel
 - Weisungsrecht
 - Haftung + Vertragsstrafen
4. Unterschriftsfelder beider Parteien.

**Erwartetes Ergebnis:**
- AV-Vertrag strukturiert nach Art. 28 Abs. 3.
- Eigener Hosting-Provider (z.B. Hetzner) als Beispiel-Auftragsverarbeiter eingetragen.
- Subunternehmer-Liste mit Zustimmungsklausel.

**DSGVO/Security-Note:**
- **Art. 28 ist Pflicht bei Auftragsverarbeitung:** Hosting, Backup, Email-Provider etc. brauchen AV-Vertrag.
- Template-Hinweis: „Vertrag ist juristisch zu prüfen, kein Rechtsberatung".

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-06 — TOMs-Template Art. 32 download

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** ENT-DSGVO-01 erfolgreich.

**Vorbereitung:**
- `/dsgvo/` mit aktivem Sudo geöffnet.

**Schritte:**
1. Auf „Art. 32 — Technische und organisatorische Maßnahmen (TOMs)" klicken.
2. Datei `toms-art-32.md` herunterladen.
3. Inhalt prüfen:
 - Vertraulichkeit (Zutritts-, Zugangs-, Zugriffskontrolle)
 - Integrität (Eingabe-, Weitergabekontrolle)
 - Verfügbarkeit (Verfügbarkeits-, Wiederherstellbarkeitskontrolle)
 - Belastbarkeit (Pen-Tests, Notfallplan)
 - Pseudonymisierung + Verschlüsselung (HMAC-Hash, AES-GCM, RLS)
4. Konkrete App-Maßnahmen vorausgefüllt (Argon2id, MFA, Audit-Log, Backup-Verschlüsselung).

**Erwartetes Ergebnis:**
- TOMs-Template enthält alle Schutzziele aus Art. 32 Abs. 1.
- App-spezifische Maßnahmen automatisch eingetragen (Argon2id, RLS, Sudo, …).
- Verweis auf Backup-Verschlüsselung, ClamAV, CSP.

**DSGVO/Security-Note:**
- **Art. 32 verpflichtet zu „Stand der Technik":** Argon2id (statt PBKDF2), AES-GCM (statt CBC), TLS 1.3.
- TOMs-Doku ist Pflicht bei Audit durch Aufsichtsbehörde.

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-07 — Informationspflichten Art. 13/14 download

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** ENT-DSGVO-01 erfolgreich.

**Vorbereitung:**
- `/dsgvo/` mit aktivem Sudo geöffnet.

**Schritte:**
1. Auf „Art. 13/14 — Informationspflichten" klicken.
2. Datei `informationspflichten-art-13-14.md` herunterladen.
3. Inhalt prüfen:
 - Identität + Kontakt des Verantwortlichen
 - Kontaktdaten DPO
 - Zwecke der Verarbeitung + Rechtsgrundlage (z.B. SGB VIII § 61)
 - Empfänger / Empfängerkategorien
 - Drittlandtransfers (i.d.R. „nein")
 - Speicherdauer (Aktenführung-Fristen aus SGB)
 - Betroffenenrechte (Art. 15-22, Beschwerderecht Art. 77)
4. Zwei Varianten: Erhebung beim Betroffenen (Art. 13) vs. Dritter (Art. 14).

**Erwartetes Ergebnis:**
- Beide Varianten klar getrennt.
- Sozialdaten-spezifische Rechtsgrundlagen (SGB VIII, BDSG).
- Klartext, betroffenenfreundliche Sprache.

**DSGVO/Security-Note:**
- **Art. 13 = Datenerhebung beim Betroffenen, Art. 14 = bei Dritten** (z.B. Jugendamt meldet Klient an Anlaufstelle).
- Information muss zum Zeitpunkt der Erhebung gegeben werden.

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-08 — AuditLog-Eintrag pro Download (Audit-Spur)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** mindestens ein DSGVO-Download durchgeführt.

**Vorbereitung:**
- Mindestens 5 verschiedene DSGVO-Downloads (alle Templates) als admin.

**Schritte:**
1. `/audit/` aufrufen.
2. Filter „Action-Type = EXPORT" oder „DSGVO_EXPORT" anwenden.
3. Pro Download ein Audit-Eintrag mit:
 - User (admin-Username)
 - Action = `DSGVO_EXPORT`
 - Target = Slug (z.B. `verarbeitungsverzeichnis-art-30`)
 - Timestamp
 - IP + User-Agent
4. Detail-View `/audit/<uuid>/` zeigt vollständige Metadaten.

**Erwartetes Ergebnis:**
- Pro Download genau 1 Audit-Eintrag.
- Slug eindeutig pro Template (5 verschiedene Slugs für 5 Templates).
- Audit-Log auch für DSGVO-Doku-Aufruf selbst (Meta-Audit).

**DSGVO/Security-Note:**
- **Audit-Spur fürs Audit:** Wer hat wann welche DSGVO-Doku heruntergeladen — relevant bei Aufsichts-Audit.
- Häufung von Downloads = Indiz für Audit-Vorbereitung oder Datenleck-Verdacht.
- Append-Only schützt diese Meta-Audits ebenfalls.

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>📡 OFFL — Offline & PWA (12 Cases)</strong></summary>

**Routen:** `/offline/`, `/offline/clients/<uuid>/`, `/offline/conflicts/`, `/api/offline/bundle/client/<uuid>/`, `/auth/offline-key-salt/`, `/manifest.json`, `/sw.js` 
**Views:** `src/core/views/offline.py` (OfflineBootstrapView, OfflineBundleView, OfflineClientDetailView, OfflineConflictListView) 
**Services:** `src/core/services/offline.py`, `src/core/services/offline_keys.py` 
**E2E-Coverage:** `test_offline_apis.py`, `test_offline_login_bootstrap.py`, `test_offline_store.py`, `test_pwa_offline.py` 
**Spezial-Setup:** Service Worker im Browser, IndexedDB, DevTools → Application-Tab. Streetwork-Geräte simulieren (mobile Viewport 375x667).

---

### TC-ID: ENT-OFFL-01 — Login-Bootstrap lädt Offline-Bundle in IndexedDB

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_offline_login_bootstrap.py` |

**Voraussetzung:** fachkraft-Account, mindestens 5 Klient zugeordnet.

**Vorbereitung:**
- Chrome DevTools öffnen, Tab „Application" → IndexedDB. Vor Login: leer.

**Schritte:**
1. Login als fachkraft via `/login/`.
2. Nach erfolgreichem Login: DevTools → Application → IndexedDB → Datenbank `anlaufstelle-offline` aufgeklappt.
3. Object-Stores prüfen: `clients`, `cases`, `events`, `meta`.
4. Daten-Anzahl prüfen — entspricht der Anzahl der zugewiesenen Klient (RLS + Caseworker-Filter).
5. Network-Tab: Bootstrap-Call `GET /api/offline/bootstrap/` mit Status 200.
6. Lease-Eintrag in `meta`-Store: `expires_at` in der Zukunft (z.B. +7 Tage).

**Erwartetes Ergebnis:**
- IndexedDB nach Login mit verschlüsselten Daten gefüllt (AES-GCM).
- Anzahl entspricht Caseworker-Zuordnung, keine fremden Klient.
- Bootstrap-Trigger in Login-Flow integriert (kein manueller Reload nötig).

**DSGVO/Security-Note:**
- **AES-GCM-Verschlüsselung in IndexedDB (Art. 32):** Bei verlorenem Gerät keine Klartext-Klientendaten lesbar.
- Schlüssel ist password-derived (PBKDF2), nicht im Klartext gespeichert.
- Lease-Mechanismus erzwingt regelmäßige Re-Auth.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-02 — Offline-Client-Bundle als JSON

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_offline_apis.py` |

**Voraussetzung:** fachkraft eingeloggt, mindestens 1 Klient zugewiesen.

**Vorbereitung:**
- Klient-UUID notieren (z.B. aus `/clients/`).

**Schritte:**
1. `/api/offline/bundle/client/<uuid>/` direkt aufrufen.
2. Response: `Content-Type: application/json`.
3. JSON-Struktur prüfen:
 - `client`: Stammdaten (Name, Geburtsdatum, …)
 - `cases`: Liste der Fälle
 - `events`: Termine
 - `notes`: Notizen
 - `documents_meta`: Metadaten Dokumente (ohne Binärinhalt)
 - `_version`: Sync-Token / Timestamp
4. Versuch mit fremder UUID (nicht zugewiesen) → 404 oder 403.

**Erwartetes Ergebnis:**
- JSON vollständig, alle relations vorgeladen (kein N+1).
- Document-Binärinhalte NICHT inkludiert (zu groß, separater Endpoint).
- Sync-Token für Konflikt-Erkennung enthalten.

**DSGVO/Security-Note:**
- API-Authentifizierung via Session-Cookie + CSRF.
- Cross-User/Cross-Facility-Probe: 404 (RLS).
- Bundle ist „Stand zum Zeitpunkt des Calls" (snapshot).

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-03 — Offline-Client-Detail (lokaler Scaffold)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** ENT-OFFL-01 erfolgreich, IndexedDB gefüllt.

**Vorbereitung:**
- DevTools → Network → „Offline" aktivieren.

**Schritte:**
1. Bei aktivem Offline-Modus: `/offline/clients/<uuid>/` aufrufen.
2. Seite rendert komplett aus IndexedDB (kein Netz-Call).
3. Anzeige: Stammdaten, Fall-Liste, Notizen.
4. Edit-Button verfügbar — Notiz hinzufügen.
5. Notiz wird lokal gespeichert (Pending-Sync-Marker im IndexedDB-Store `pending_writes`).

**Erwartetes Ergebnis:**
- Seite lädt offline ohne Netzfehler.
- Service-Worker liefert HTML-Scaffold + JS für Hydration aus Cache.
- Lokaler Edit funktioniert, Pending-Marker sichtbar (z.B. „⏳ Wird beim nächsten Sync übertragen").

**DSGVO/Security-Note:**
- Daten-Decryption via password-derived Key (im Memory, nicht persistiert).
- Bei Browser-Schließen → Key weg, beim nächsten Login Re-Decrypt.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-04 — Service-Worker Registration

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** frischer Browser-Profile (kein vorhandener SW).

**Vorbereitung:**
- Chrome → DevTools → Application → Service Workers (vor Login: leer).

**Schritte:**
1. `/` aufrufen (Login-Seite oder Landing).
2. JavaScript registriert SW: `navigator.serviceWorker.register('/sw.js')`.
3. DevTools → Application → Service Workers: Status `activated`, Source `/sw.js`.
4. Cache Storage: `anlaufstelle-static-v<N>`, `anlaufstelle-runtime-v<N>` Buckets sichtbar.
5. SW-Update-Test: Code-Änderung in `sw.js` → Reload → Update-Event.

**Erwartetes Ergebnis:**
- SW erfolgreich registriert (Status `activated`).
- Pre-Cache statischer Assets (CSS, JS, Manifest, Offline-Fallback-HTML).
- Runtime-Cache für API-Bundles.

**DSGVO/Security-Note:**
- SW läuft nur auf HTTPS (oder localhost) — Same-Origin-Policy.
- Kein Caching sensibler API-Responses ohne Auth-Check (Conditional-Caching).

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-05 — Offline-Fallback-Seite

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** ENT-OFFL-04 erfolgreich, SW aktiv.

**Vorbereitung:**
- DevTools → Network → „Offline" aktivieren.

**Schritte:**
1. `/clients/` aufrufen während offline (Online-Seite, kein Offline-Scaffold).
2. SW fängt Request ab, liefert `/offline/`-Fallback-HTML aus Cache.
3. Fallback-Seite zeigt: „Du bist offline. Verfügbare Klient-Daten findest du unter Offline-Bereich."
4. Link „Offline-Klient öffnen" zu `/offline/clients/<uuid>/` für gecachten Klient.
5. Online wieder aktivieren → Reload → normale `/clients/`-Seite lädt.

**Erwartetes Ergebnis:**
- Fallback-Seite klar erkennbar, kein „weißer Bildschirm".
- Hinweise auf verfügbare Offline-Funktionen.
- SW unterscheidet zwischen API-404 und Netzwerk-Fehler.

**DSGVO/Security-Note:**
- Fallback-Seite enthält keine sensiblen Daten (statischer Inhalt).
- Pending-Writes-Counter sichtbar („3 Notizen warten auf Sync").

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-06 — Manifest.json gültig (PWA-Installation)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** fachkraft eingeloggt.

**Vorbereitung:**
- Chrome auf Mobile-Device (oder DevTools Device-Mode 375x667).

**Schritte:**
1. `/manifest.json` direkt aufrufen.
2. JSON-Struktur prüfen: `name`, `short_name`, `start_url`, `display: standalone`, `background_color`, `theme_color`, `icons` (mehrere Auflösungen 192/512), `scope`.
3. Chrome zeigt „Zum Startbildschirm hinzufügen"-Banner an.
4. Banner akzeptieren — App-Icon im Homescreen.
5. App vom Homescreen starten — öffnet im Standalone-Mode (kein Browser-UI).
6. Lighthouse PWA-Audit: alle Pflicht-Checks grün.

**Erwartetes Ergebnis:**
- Manifest valide nach W3C-Spec.
- PWA-Installation auf Android + iOS möglich.
- Lighthouse-PWA-Score > 90.

**DSGVO/Security-Note:**
- Standalone-Mode versteckt URL-Bar — Phishing-Risiko (mitigiert durch fest verdrahtete Anlaufstelle-Domain).
- Manifest enthält keine personenbezogenen Daten (statisch).

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-07 — Sync-Konflikt-Liste

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_offline_store.py` |

**Voraussetzung:** mindestens 2 Sync-Konflikte erzeugt (gleicher Datensatz offline + online geändert).

**Vorbereitung:**
- Klient-Notiz offline ändern auf Gerät A.
- Parallel selben Klient online ändern (z.B. via Web-UI).
- Gerät A wieder online → Sync triggert Konflikt.

**Schritte:**
1. `/offline/conflicts/` aufrufen.
2. Liste der Konflikte mit Spalten: Klient, Feld, lokaler Wert, Server-Wert, Zeitstempel beider Versionen.
3. Pro Konflikt Buttons: „Lokal übernehmen", „Server übernehmen", „Manuell mergen".
4. Filter nach Klient oder Feld.
5. Counter im Header: „2 ungelöste Konflikte".

**Erwartetes Ergebnis:**
- Konflikte klar dargestellt, beide Versionen lesbar.
- Sortierung nach Zeitstempel (neueste zuerst).
- Badge mit Konflikt-Counter sichtbar (HTMX-Refresh).

**DSGVO/Security-Note:**
- Konflikt-Daten in Audit-Log (`SYNC_CONFLICT_DETECTED`).
- Beide Versionen werden vorübergehend gespeichert bis Auflösung.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-08 — Sync-Konflikt-Review (Merge-Entscheidung)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_offline_store.py` |

**Voraussetzung:** ENT-OFFL-07 erfolgreich, mindestens 1 offener Konflikt.

**Vorbereitung:**
- `/offline/conflicts/` mit > 0 Konflikten geöffnet.

**Schritte:**
1. Auf einen Konflikt klicken → Detail-Ansicht.
2. Nebeneinander beide Versionen (Diff-Style, rot/grün).
3. „Server übernehmen" wählen → lokaler Wert verworfen, Server-Wert in IndexedDB übernommen.
4. Konflikt aus Liste entfernt, Counter dekrementiert.
5. Audit-Log-Eintrag `SYNC_CONFLICT_RESOLVED` mit Entscheidung (server-wins/local-wins/merge).
6. Bei „Manuell mergen" → Editor mit beiden Werten, freier Text.

**Erwartetes Ergebnis:**
- Auflösung persistiert auf Server + lokal.
- Audit-Log dokumentiert Entscheidung + User.
- Keine doppelten Auflösungen möglich (Idempotenz).

**DSGVO/Security-Note:**
- **Art. 5 (Richtigkeit):** Konflikt-Auflösung ist nachvollziehbar dokumentiert.
- Bei manuellem Merge: User trägt Verantwortung, AuditLog enthält finalen Text.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-09 — Offline-Crypto: Salt + PBKDF2 + AES-GCM

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_offline_apis.py` |

**Voraussetzung:** fachkraft eingeloggt.

**Vorbereitung:**
- DevTools → Network-Tab geöffnet.

**Schritte:**
1. Login als fachkraft.
2. Network-Tab: Call `GET /auth/offline-key-salt/` mit Status 200.
3. Response-Body: JSON mit `salt` (Base64-String, 16+ Bytes), `iterations` (z.B. 600.000), `hash_algo` (z.B. sha256).
4. JS-Code prüfen: PBKDF2 mit Pwd + Salt + Iterations → Key-Material.
5. Key wird via Web-Crypto-API in AES-GCM-Encrypter verwandelt (256-bit).
6. IndexedDB-Daten manuell inspizieren: nur ciphertext + iv pro Eintrag, kein Klartext.

**Erwartetes Ergebnis:**
- Salt facility-spezifisch oder user-spezifisch (nicht global).
- PBKDF2-Iterations ≥ OWASP-Empfehlung (600.000 für SHA-256).
- AES-GCM mit 256-bit Key, IV pro Eintrag random.
- Keine Klartext-Daten in IndexedDB.

**DSGVO/Security-Note:**
- **Art. 32 (Verschlüsselung at rest):** Browser-Speicher ist „at rest" — daher AES-GCM Pflicht.
- Salt + Pwd → Key-Derivation erfüllt „Stand der Technik".
- IV-Wiederverwendung wäre Sicherheitslücke, daher random pro Eintrag.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-10 — Salt-Rotation nach Pwd-Wechsel

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_offline_apis.py` |

**Voraussetzung:** ENT-OFFL-09 erfolgreich, IndexedDB mit verschlüsselten Daten gefüllt.

**Vorbereitung:**
- IndexedDB-Snapshot (Eintrag mit ciphertext kopieren).

**Schritte:**
1. Pwd-Wechsel via `/accounts/password_change/` durchführen.
2. Im Backend wird neuer Salt generiert und gespeichert (`offline_keys.rotate_salt`).
3. Logout, dann erneuter Login mit neuem Pwd.
4. Salt-Endpoint liefert neuen Salt → neuer abgeleiteter Key.
5. IndexedDB-Decrypt-Versuch alter Eintrag mit neuem Key → Fehler (alte Daten nicht entschlüsselbar).
6. Offline-Bootstrap lädt Daten neu mit neuem Key (Re-Bootstrap).
7. Audit-Log-Eintrag `OFFLINE_KEY_ROTATED`.

**Erwartetes Ergebnis:**
- Alte lokale Daten unentschlüsselbar nach Pwd-Wechsel (gewollt: Schutz bei kompromittiertem Pwd).
- Re-Bootstrap automatisch nach erstem Login mit neuem Pwd.
- Keine Datenverlust-Gefahr (Server-Daten bleiben).

**DSGVO/Security-Note:**
- **Defense bei Pwd-Kompromittierung:** Neuer Salt invalidiert alle alten Offline-Caches auf gestohlenen Geräten.
- Pending-Writes vor Pwd-Wechsel müssen gesynct werden, sonst Daten-Verlust.
- UI-Hinweis: „Bitte erst Sync abwarten, dann Pwd ändern".

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-11 — Cache-Lease abgelaufen → Re-Bootstrap

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_offline_apis.py` |

**Voraussetzung:** ENT-OFFL-01 erfolgreich, Lease in `meta`-Store gesetzt.

**Vorbereitung:**
- DevTools → IndexedDB → `meta` → `lease.expires_at` manuell auf Vergangenheit setzen (z.B. `2020-01-01`).

**Schritte:**
1. App neu laden / Login-Status prüfen.
2. Code prüft beim Start: Lease abgelaufen?
3. Bei Ablauf: Lokale Daten werden invalidiert (oder als „stale" markiert).
4. UI-Banner: „Cache abgelaufen, bitte neu laden".
5. Bootstrap-Call wird automatisch oder per Klick ausgelöst.
6. Neuer Lease gesetzt mit neuem `expires_at`.

**Erwartetes Ergebnis:**
- Abgelaufener Lease blockiert Offline-Zugriff (oder warnt prominent).
- Re-Bootstrap lädt frische Daten.
- Pending-Writes werden VOR Invalidation gesynct (Daten-Verlust-Schutz).

**DSGVO/Security-Note:**
- **Lease ist Datenminimierung-Mechanismus (Art. 5):** Daten verbleiben nicht unbegrenzt offline.
- Default-Lease-TTL z.B. 7 Tage — Streetwork-Realismus, aber kein Lifetime-Cache.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-12 — Streetwork-Workflow (End-to-End-Smoke)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** fachkraft mit zugewiesenen Klient.

**Vorbereitung:**
- Mobile Device (oder Chrome DevTools Mobile-Mode 375x667).
- Network ON.

**Schritte:**
1. **Online (Büro):** Login als fachkraft. Bootstrap lädt Bundle (ENT-OFFL-01).
2. **Offline (Außendienst):** DevTools-Network → „Offline".
3. `/offline/clients/<uuid>/` öffnen → Daten sichtbar.
4. Notiz hinzufügen: „Beratungstermin durchgeführt, Klient stabil."
5. Pending-Marker erscheint (⏳).
6. Status-Update: Termin als „durchgeführt" markieren.
7. **Online wieder (zurück im Büro):** DevTools-Network → wieder online.
8. SW erkennt Network → triggert Sync der Pending-Writes.
9. Pending-Marker verschwinden, Notiz + Status auf Server.
10. Audit-Log: 2 Einträge (`NOTE_CREATED`, `EVENT_UPDATED`) mit Sync-Origin = `offline_sync`.

**Erwartetes Ergebnis:**
- Vollständiger Streetwork-Zyklus funktioniert offline → online.
- Keine Daten verloren.
- Sync ist idempotent (Re-Trigger erzeugt keine Duplikate).

**DSGVO/Security-Note:**
- **Streetwork-Realität:** Sozialarbeiter dokumentieren oft im Außendienst ohne Netz.
- Pending-Writes sind verschlüsselt in IndexedDB (AES-GCM).
- Audit-Log markiert Sync-Origin für forensische Nachvollziehbarkeit.

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>⚙️ SYS — System & Operations (6 Cases)</strong></summary>

**Routen:** `/health/`, `/csp-report/`, `/robots.txt`, `/manifest.json`, `/?lang=de` 
**Views:** `src/core/views/health.py`, `src/core/views/csp_report.py`, `src/core/views/robots.py`, `src/core/views/pwa.py` 
**E2E-Coverage:** `test_security_hardening.py` 
**Spezial-Setup:** Health prüft externe Dienste (ClamAV, Redis, Backup-Status). CSP-Report-Endpoint mit Rate-Limit.

---

### TC-ID: ENT-SYS-01 — Health-Endpoint liefert JSON

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sys | öffentlich (Monitoring) | C/F/S | ⚪ | `test_security_hardening.py` |

**Voraussetzung:** alle Container laufen (Postgres, Redis, ClamAV).

**Vorbereitung:**
- Backup-Job mindestens 1x ausgeführt (Cron oder manuell).

**Schritte:**
1. `/health/` ohne Auth aufrufen.
2. Response-Code `200`.
3. `Content-Type: application/json`.
4. JSON-Struktur prüfen:
 - `status: "healthy"`
 - `db: "ok"`
 - `redis: "ok"` oder `redis_status: "connected"`
 - `clamav: "ok"` oder `clamav_status: "running"`
 - `last_backup_age_hours: <number>` (z.B. 6)
 - `version: "0.9.1"`
 - `timestamp: ISO-8601`
5. `SECURE_REDIRECT_EXEMPT` prüfen — Endpoint erreichbar ohne HTTPS-Redirect.

**Erwartetes Ergebnis:**
- Endpoint öffentlich, ohne Login.
- JSON für Monitoring-Tools (Uptime-Robot, Prometheus-Probe).
- Antwort < 500 ms.

**DSGVO/Security-Note:**
- **Bewusst öffentlich** für Monitoring (kein Auth-Wall).
- Antwort enthält keine personenbezogenen Daten, keine User-Counts.
- Verhindert Information-Leak: keine Stack-Traces, keine internen Hostnames.

**Status:** ☐ Offen

---

### TC-ID: ENT-SYS-02 — ClamAV-Down → Health zeigt unhealthy

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sys | öffentlich | C/F/S | ⚪ | `test_security_hardening.py` |


**Vorbereitung:**
- ClamAV-Container stoppen: `sudo docker compose stop clamav`.

**Schritte:**
1. `/health/` aufrufen.
2. Response-Code: `503` (Service Unavailable) oder `200` mit `status: "degraded"`.
3. JSON-Body: `clamav: "unreachable"` oder `clamav_status: "down"`.
4. Andere Felder bleiben grün (DB, Redis).
5. ClamAV wieder starten: `sudo docker compose start clamav`.
6. Nach 30s Reload — wieder `healthy`.

**Erwartetes Ergebnis:**
- Health-Check erkennt ClamAV-Ausfall korrekt.
- Status-Code-Differenzierung (503 vs. 200) je nach Konfiguration.
- Recovery automatisch nach Container-Restart (kein App-Reload nötig).

**DSGVO/Security-Note:**
- **Virus-Scan ist DSGVO-relevant (Art. 32):** Kein Upload ohne aktiven Scanner.
- Bei ClamAV-Down: Datei-Uploads müssen blockiert werden (Fail-Secure).

**Status:** ☐ Offen

---

### TC-ID: ENT-SYS-03 — Robots.txt mit Disallow für sensible Pfade

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sys | öffentlich | C/F/S | ⚪ | `test_security_hardening.py` |


**Vorbereitung:**
- Keine.

**Schritte:**
1. `/robots.txt` aufrufen.
2. Response-Code 200, `Content-Type: text/plain`.
3. Inhalt prüfen — `Disallow:`-Einträge für:
 - `/admin/`
 - `/clients/`
 - `/cases/`
 - `/dsgvo/`
 - `/audit/`
 - `/api/`
 - `/sudo/`
 - `/login/` (optional)
4. `Allow:` ggf. für `/`, `/about/`, `/impressum/`, `/datenschutz/`.
5. `Sitemap:`-Eintrag (falls vorhanden).

**Erwartetes Ergebnis:**
- Robots.txt deckt alle authentifizierten Bereiche per Disallow.
- Suchmaschinen-Indizierung sensibler Pfade verhindert.
- Statisch oder via View generiert.

**DSGVO/Security-Note:**
- **Suchmaschinen-Hygiene:** Auch wenn Auth-Wall greift, sollten Login-URLs nicht in Google-Index.
- Defense-in-Depth: ergänzt `noindex`-Header und Auth.
- Achtung: Robots.txt ist „freiwillige Empfehlung", kein Sicherheits-Mechanismus.

**Status:** ☐ Offen

---

### TC-ID: ENT-SYS-04 — CSP-Report-Endpoint akzeptiert nur application/csp-report

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sys | öffentlich | C/F/S | ⚪ | `test_security_hardening.py` |

**Voraussetzung:** CSP-Header in Responses gesetzt mit `report-uri /csp-report/`.

**Vorbereitung:**
- `curl` zur Hand, oder Playwright mit `request.fetch`.

**Schritte:**
1. POST `/csp-report/` mit `Content-Type: application/csp-report` und valider JSON-Payload (`{"csp-report": {"document-uri": "...", "violated-directive": "..."}}`) → Status 204.
2. POST mit `Content-Type: application/json` (falsch) → Status 400 oder 415.
3. POST ohne Body → 400.
4. Rate-Limit-Test: 11 POSTs in unter 1 Minute von gleicher IP → 11. Request liefert 429 (Too Many Requests).
5. Browser-Test: CSP-Verletzung absichtlich auslösen (Inline-Script auf Test-Seite) → automatischer POST von Browser → 204.
6. Logging-Verzeichnis prüfen: CSP-Reports in Logs (z.B. `/var/log/anlaufstelle/csp.log`).

**Erwartetes Ergebnis:**
- Endpoint akzeptiert NUR `application/csp-report`.
- Rate-Limit 10 Requests/min/IP greift.
- Reports werden geloggt, aber nicht in DB persistiert (DoS-Schutz).
- Kein PII in CSP-Reports (URLs sind öffentlich).

**DSGVO/Security-Note:**
- **CSP-Reports sind Sicherheitsfeature:** XSS-Versuche werden gemeldet.
- Rate-Limit verhindert DoS via Report-Flood.
- Keine personenbezogenen Daten in Reports → kein DSGVO-Konflikt.

**Status:** ☐ Offen

---

### TC-ID: ENT-SYS-05 — Locale-Wechsel via URL-Parameter

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sys | öffentlich | C/F/S | ⚪ ||

**Voraussetzung:** beide Locale-Dateien (de, en) kompiliert.

**Vorbereitung:**
- Locale-Files: `make compilemessages` oder Container-Restart.

**Schritte:**
1. `/?lang=de` aufrufen → Login-Seite auf Deutsch (z.B. „Anmelden", „Passwort").
2. `/?lang=en` aufrufen → Login-Seite auf Englisch („Sign in", „Password").
3. `Accept-Language: en-US`-Header senden ohne `?lang=` → automatisch Englisch.
4. `Accept-Language: de-DE` → Deutsch.
5. Locale-Cookie wird gesetzt (`django_language` oder ähnlich).
6. Eingeloggte User-Profile-Setting überschreibt Browser-Default.

**Erwartetes Ergebnis:**
- Locale-Wechsel funktioniert via URL, Cookie und User-Setting.
- Reihenfolge: User-Setting > Cookie > URL > Accept-Language > Default (de).
- Keine doppelten Strings in i18n (alle Templates verwenden `{% trans %}` oder `{% blocktrans %}`).

**DSGVO/Security-Note:**
- Locale-Cookie ist „functional" — kein Consent nötig (Art. 6 Abs. 1 lit. f).
- Keine PII in Locale-Cookie.

**Status:** ☐ Offen

---

### TC-ID: ENT-SYS-06 — 404/500-Error-Pages gestyled, keine Stack-Traces

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sys | öffentlich | C/F/S | ⚪ | `test_security_hardening.py` |

**Voraussetzung:** mit `DEBUG=False` (Prod-ähnlich).

**Vorbereitung:**
- `make run-prod` oder `DJANGO_SETTINGS_MODULE=anlaufstelle.settings.prod`.

**Schritte:**
1. `/this-page-does-not-exist-12345/` aufrufen → 404-Seite.
2. Inhalt prüfen: gestyltes Template (Tailwind), Anlaufstelle-Logo, freundliche Fehlermeldung, „Zurück zur Startseite"-Link.
3. Quelltext: kein Stack-Trace, kein Hostname, keine Pfad-Leaks.
4. Künstlichen 500er erzeugen: `/test/raise-500/` (falls Test-Endpoint existiert) oder DB-Connection killen.
5. 500-Seite: ähnlich gestyled, „Internal Server Error", Hinweis auf Status-Page (falls vorhanden).
6. Audit-Log oder Sentry-Eintrag prüfen — Fehler intern erfasst.
7. Prod-Mode: `DEBUG = False` → keine Django-Yellow-Page.

**Erwartetes Ergebnis:**
- 404 + 500 sind eigene Templates (`templates/404.html`, `templates/500.html`).
- Keine Stack-Traces im Browser sichtbar.
- Sentry/Logging fängt Fehler intern ab.
- Konsistentes Design mit Rest der App.

**DSGVO/Security-Note:**
- **Information-Leak-Schutz:** Stack-Traces, DB-Queries, Settings dürfen nie in Browser-Output.
- Bei 500: nicht generisch („Es ist ein Fehler aufgetreten"), sondern mit Korrelations-ID für Support.
- Korrelations-ID enthält keine PII (UUID).

**Status:** ☐ Offen

</details>

---

### SETUP — Einrichtungs-/Konfigurationsassistent

> **Forward-looking Bereich (Refs #908).** Feature wird in #917 implementiert und ist aktuell **-blocked** (M0 Custom Admin UI + M2 Config-Loader). Cases werden mit dem Feature ergänzt. Vorgesehenes Schema gemäß Codex-Audit §4.5:
>
> - `ENT-SETUP-01` — Neue Facility per Assistent vollständig anlegen (End-to-End ohne Shell)
> - `ENT-SETUP-02` — Dokumentationsbibliothek auswählen (Template-Übernahme und Anpassung)
> - `ENT-SETUP-03` — Riskante Einstellung wird gewarnt (Guardrails)
> - `ENT-SETUP-04` — Setup schreibt AuditLog (Nachvollziehbarkeit)

---

### COMP — Betriebs-/Compliance-Dashboard

> **Forward-looking Bereich (Refs #908).** Feature wird in #919 implementiert. Baut auf #902 (`check_db_roles`-Kommando) auf. Cases werden mit dem Feature ergänzt. Vorgesehenes Schema:
>
> - `ENT-COMP-01` — DB-Rollencheck zeigt sichere App-Rolle (`NOSUPERUSER`, kein `BYPASSRLS`)
> - `ENT-COMP-02` — Backup veraltet erzeugt Warnung
> - `ENT-COMP-03` — ClamAV down ist critical
> - `ENT-COMP-04` — Retention-Job überfällig ist warning/critical

> **Verwandtes LOKAL/SSH-Pendant:** Die `D.OPS`-Cases (#903) prüfen dieselben Zustände manuell per `psql` / SSH; das Dashboard zeigt sie für Betreiber:innen ohne Server-Zugriff.

---

### PRIV — Datenschutz-Review (Freitext)

> **Forward-looking Bereich (Refs #908).** Feature wird in #918 implementiert. Cases werden mit dem Feature ergänzt. Vorgesehenes Schema:
>
> - `ENT-PRIV-01` — Freitext-Review listet riskante Inhalte (`Client.notes`, `Case.description`, `Episode.description`, `WorkItem.description`)
> - `ENT-PRIV-02` — Review respektiert Rollen-Sichtbarkeit (kein neuer Leak)
> - `ENT-PRIV-03` — Review-Aktion wird auditiert

---

### REPORT — Datenschutzfreundliche externe Berichte

> **Forward-looking Bereich (Refs #908).** Feature wird in #921 implementiert. Cases werden mit dem Feature ergänzt. Vorgesehenes Schema:
>
> - `ENT-REPORT-01` — Externer Bericht unterdrückt kleine Gruppen (K-Anonymity)
> - `ENT-REPORT-02` — Externer Bericht enthält keine Pseudonym-Rankings

---

### A11Y — Accessibility (WCAG-Stichproben)

> **Bereich (Refs #912).** Manuelle Stichproben — **kein** systematischer WCAG 2.1 AA-Audit ( gesperrt; siehe #470), kein axe-core / Pa11y. Ziel: Regressionen in häufig genutzten Flows erkennen, nicht Compliance-Zertifizierung.

<details open>

### TC-ID: ENT-A11Y-01 — Tastaturbedienung Hauptnavigation

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C/F |||

**Schritte:**
1. Auf Dashboard einloggen.
2. Ohne Maus, nur per `Tab` durch die Hauptnavigation tabben.
3. Pro Navigationspunkt: `Enter` aktiviert den Link.
4. `Shift+Tab` führt zurück.
5. `Escape` schließt offene Dropdown-Menüs.

**Erwartetes Ergebnis:**
- Jeder Navigationspunkt ist per Tab erreichbar.
- Tab-Reihenfolge entspricht der visuellen Reihenfolge (oben→unten, links→rechts).
- Keine Fokus-Falle: man kann auch wieder rauf-tabben.

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-02 — Sichtbarer Fokus auf interaktiven Elementen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C/F/S |||

**Schritte:**
1. Per Tab durch ein Formular (z.B. Event anlegen).
2. Pro Element prüfen: sichtbarer Fokus-Ring vorhanden?
3. Auch bei Buttons, Links, Form-Feldern, Toggles.

**Erwartetes Ergebnis:**
- Jedes fokussierte Element zeigt einen erkennbaren Fokus-Indikator.
- Kontrast Fokus-Indikator zum Hintergrund ist deutlich (≥ 3:1).
- Kein `outline: none` ohne Ersatz-Indikator.

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-03 — Fokus-Reihenfolge in Formularen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C |||

**Schritte:**
1. Event-Edit-Formular öffnen mit dynamisch generierten Feldern.
2. Per Tab durchgehen.
3. Reihenfolge: oben→unten, niemals Sprünge.
4. Auch HTMX-nachgeladene Felder müssen nach Sortierung tabbar sein.

**Erwartetes Ergebnis:**
- Tab-Reihenfolge folgt der visuellen Layout-Reihenfolge.
- Nach HTMX-Swap: neuer Inhalt ist in Tab-Reihenfolge integriert.

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-04 — Modal/Dialog Fokusfalle

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C/F |||

**Schritte:**
1. Bestätigungsdialog öffnen (z.B. Klient soft-deleten).
2. Per Tab durch die Dialog-Buttons.
3. Tab nach letztem Button: springt zum ersten zurück (Fokusfalle).
4. `Escape` schließt den Dialog, Fokus geht zurück zum Auslöser-Element.

**Erwartetes Ergebnis:**
- Tab-Fokus bleibt im offenen Dialog.
- Escape funktioniert.
- Hintergrund-Inhalt ist nicht per Tab erreichbar, solange der Dialog offen ist.

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-05 — Screen-Reader-Labels für Icon-Buttons

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C |||

**Schritte:**
1. DevTools-Accessibility-Tree öffnen oder VoiceOver/NVDA aktivieren.
2. Icon-Buttons in Tabellen (Edit/Delete/Show) untersuchen.
3. Jedes Icon hat ein `aria-label`, `title` oder sichtbaren Text.

**Erwartetes Ergebnis:**
- Kein Icon-Button ohne semantisches Label.
- Screen-Reader liest sinnvolle Aktionen, nicht „Button-svg".

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-06 — Fehlermeldungen mit Formularfeldern verknüpft

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C |||

**Schritte:**
1. Event-Form absenden mit leerem Pflichtfeld.
2. Validierungsfehler wird angezeigt.
3. DevTools: Fehler-Element ist per `aria-describedby` mit dem Feld verknüpft.
4. Feld selbst hat `aria-invalid="true"`.

**Erwartetes Ergebnis:**
- Screen-Reader liest beim Fokussieren des Felds die Fehlermeldung.
- Fehler-Text ist visuell beim Feld, nicht nur global im Toast.

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-07 — Farbkontrast Badges/Alerts/Buttons

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C |||

**Schritte:**
1. DevTools-Contrast-Checker auf Badges (Status, Priorität).
2. Auf Alert-Toast-Hintergründe (success/warning/error).
3. Auf Primary-Buttons.

**Erwartetes Ergebnis:**
- Text/Hintergrund-Kontrast für normale Schriftgröße ≥ 4.5:1 (WCAG AA).
- Für große Schrift (≥ 18.66px bold) ≥ 3:1.

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-08 — Reduced-Motion-Verhalten

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C/F |||

**Schritte:**
1. OS-Setting `prefers-reduced-motion: reduce` aktivieren (macOS: Bedienungshilfen, Windows: Vereinfachte Darstellung, DevTools: Rendering-Panel).
2. App-Animationen (Modal-Slide-In, Toast-Fade, HTMX-Transitions) auslösen.

**Erwartetes Ergebnis:**
- Animationen sind reduziert oder ausgeschaltet.
- Keine schnellen Flicker oder Parallax-Effekte.
- Funktionale Bewegung (Scroll-to-Element) bleibt erlaubt.

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-09 — Mobile Zoom 200 % ohne Layoutbruch

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C | ✓ ||

**Schritte:**
1. Mobile-Viewport (375×812, iPhone-SE-Size) in DevTools setzen.
2. Browser-Zoom auf 200 % (Strg/Cmd+Plus mehrmals).
3. Dashboard, Klient-Liste, Event-Form durchgehen.

**Erwartetes Ergebnis:**
- Kein horizontales Scrollen (Reflow funktioniert).
- Buttons und Links sind weiterhin tap-fähig (Touch-Target ≥ 44×44 px).
- Keine abgeschnittenen Texte.

**Status:** ☐ Offen

</details>

---

## SEKTION C — Auditor-DSGVO/Security

> **Zielgruppe:** Externe:r DSGVO-Auditor:in oder interne Compliance-Prüfung. Maximale Tiefe mit Verweis auf konkrete DSGVO-Artikel, Migrationen, Services, Settings.
>
> **Konvention:** Jeder Case enthält zusätzlich:
> - **DSGVO-Artikel-Zitat** (kurz, im Klartext)
> - **Code-Referenz** (Datei + Funktion/Zeilen-Bereich)
> - **Migrations-Referenz** (falls RLS/Schema-relevant)
> - **Erwarteter Audit-Eintrag** (Action-Type + Felder)

### DSGVO Art. 5 — Grundsätze

#### AUD-DSGVO-Art5-01 — Zweckbindung des Audit-Logs

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S || `src/tests/test_audit_signals.py`, `src/tests/test_audit_view.py` |

**DSGVO-Artikel-Zitat:** *Art. 5 Abs. 1 lit. b — Personenbezogene Daten müssen für festgelegte, eindeutige und legitime Zwecke erhoben werden.*

**Code-Referenz:**
- `src/core/models/audit.py` (`AuditLog` — append-only, 30+ Action-Types)
- `src/core/services/audit.py` (Logging-Helfer)


**Schritte:**
1. Mit `admin` einloggen, `/audit/` öffnen.
2. Filter nach `Action`-Typen anwenden — bestätigen, dass jeder Eintrag einen klar definierten Zweck hat (LOGIN, EXPORT, DELETE, …).
3. Im Quellcode `src/core/models/audit.py` die `Action`-Choices prüfen — alle dokumentieren den Verarbeitungszweck.
4. Stichprobe: 5 zufällige Einträge aus `/audit/` öffnen → jeder Eintrag dokumentiert wer/was/wann/warum.

**Erwartetes Ergebnis:**
- Jeder Audit-Eintrag hat einen sprechenden Action-Typ und ein Zielobjekt.
- Keine generischen Logs ohne Zweck (z.B. „debug" oder „misc").

**Erwarteter Audit-Eintrag:** dieser Test selbst erzeugt nur LOGIN/VIEW-Einträge.

**Status:** ☐ Offen

---

#### AUD-DSGVO-Art5-02 — Datenminimierung im Anonym-Modus

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | fachkraft | C/F/S || `src/tests/test_min_contact_stage_anonymous.py`, `src/tests/test_k_anonymization.py` |

**DSGVO-Artikel-Zitat:** *Art. 5 Abs. 1 lit. c — Daten müssen dem Zweck angemessen, erheblich und auf das notwendige Maß beschränkt sein.*

**Code-Referenz:**
- `src/core/services/k_anonymization.py` (k=5 Default)
- `src/core/models/client.py` (`Client.stage` mit `anonymous`/`identified`/`qualified`)

**Voraussetzung:** SMK-A-VORM-02

**Schritte:**
1. Anonyme:n Klient:in über VORM-02 anlegen (kein Pseudonym).
2. Detail-View prüfen: keine direkt identifizierenden Felder (Name, Geb-Datum, Adresse) sichtbar/setzbar.
3. JSON-Export der Klient:in (über Lead+ Sudo): nur Alters-Cluster, Stage, ID — keine Klarnamen.
4. Im Quellcode `services/k_anonymization.py` prüfen: k=5 Default für Anonymisierung.

**Erwartetes Ergebnis:**
- Im Anonym-Modus werden nur Alters-Cluster (z.B. „25-34") statt exakter Geburtsdaten gespeichert.
- Keine Klarnamen-Felder vorhanden.

**Status:** ☐ Offen

---

### DSGVO Art. 7 — Einwilligung

#### AUD-DSGVO-Art7-01 — Einwilligungs-Template (organisatorisch)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S |||

**DSGVO-Artikel-Zitat:** *Art. 7 — Bedingungen für die Einwilligung. Nachweis und Widerruflichkeit müssen sichergestellt sein.*

**Code-Referenz:**
- `src/core/services/dsgvo_package.py`
- `src/core/management/commands/generate_dsgvo_package.py`

**Hinweis:** Self-Service-Einwilligung in der App ist **nicht implementiert** (Memory: organisatorisch über Mitarbeiter:in/Leitung).

**Voraussetzung:** Sudo-Mode aktiv (`/sudo/`)

**Schritte:**
1. Mit `admin` einloggen, Sudo-Mode betreten.
2. `/dsgvo/` öffnen — DSGVO-Paket-View.
3. Einwilligungs-Template (Information für Klient:innen, Art. 13/14) als Markdown herunterladen.
4. Inhalt prüfen: Hinweise auf Zweck, Speicherdauer, Widerruflichkeit, Empfänger:innen.

**Erwartetes Ergebnis:**
- Template ist verfügbar, Facility-spezifische Platzhalter sind ersetzt.
- Verteilung an Klient:innen erfolgt **organisatorisch** (Mitarbeiter:in händigt aus, dokumentiert in Akte).

**Erwarteter Audit-Eintrag:** `EXPORT` mit Slug `informationspflichten`.

**Status:** ☐ Offen

---

### DSGVO Art. 15 — Auskunftsrecht

#### AUD-DSGVO-Art15-01 — Datenauskunft als JSON-Export

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | leitung | C/F/S || `test_client_export.py`, `src/tests/test_client_export.py` |

**DSGVO-Artikel-Zitat:** *Art. 15 — Betroffene Person hat Recht auf Auskunft über alle gespeicherten personenbezogenen Daten.*

**Code-Referenz:**
- `src/core/views/clients.py` (`ClientDataExportJSONView`)
- `src/core/services/client_export.py`

**Voraussetzung:** Sudo-Mode aktiv, eine identifizierte Klient:in mit Events.

**Schritte:**
1. Mit `leitung` einloggen, Sudo-Mode betreten.
2. Klient:in aus Liste wählen, Detail öffnen.
3. Auf **„Datenauskunft (JSON)"** klicken → Download startet.
4. JSON öffnen und prüfen:
 - Alle Klient:innen-Stammdaten enthalten.
 - Alle Events mit Inhalten enthalten.
 - Alle Cases / Episoden / Goals enthalten.
 - Alle Anhänge (Metadaten, nicht Binary) enthalten.
5. AuditLog `/audit/` filtern auf `Action=EXPORT` → Eintrag für diesen Export sichtbar.

**Erwartetes Ergebnis:**
- JSON-Datei ist maschinenlesbar, vollständig (Schema-Probe: keine `null`-Felder bei vorhandenen Daten).
- AuditLog-Eintrag mit User=leitung, Target=Client-UUID, Action=EXPORT.

**Erwarteter Audit-Eintrag:** `EXPORT` mit `target_id=<client_uuid>`, `format=json`.

**Status:** ☐ Offen

---

#### AUD-DSGVO-Art15-02 — Datenauskunft als PDF

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | leitung | C/F/S || `test_client_export.py` |

**DSGVO-Artikel-Zitat:** *Art. 15 — Auskunft in lesbarer Form.*

**Code-Referenz:** `src/core/views/clients.py` (`ClientDataExportPDFView`)

**Voraussetzung:** Sudo-Mode aktiv.

**Schritte:**
1. Wie AUD-DSGVO-Art15-01, aber **„PDF"** statt JSON.
2. PDF öffnen → Inhalt enthält Klient:innen-Stammdaten + Events + Cases.
3. Layout: Kopfzeile mit Facility-Name, Fußzeile mit Datum + „Generiert am".

**Erwartetes Ergebnis:**
- PDF ist menschenlesbar, alle Klient:innen-Daten enthalten.
- AuditLog: `EXPORT` mit `format=pdf`.

**Status:** ☐ Offen

---

### DSGVO Art. 16 — Berichtigung

#### AUD-DSGVO-Art16-01 — Berichtigung über Mitarbeiter:in (organisatorisch)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | fachkraft | C/F/S || `test_client_edit.py` |

**DSGVO-Artikel-Zitat:** *Art. 16 — Recht auf Berichtigung unrichtiger personenbezogener Daten.*

**Hinweis:** Self-Service-Korrektur ist **nicht implementiert** (Memory: organisatorisch). Korrektur erfolgt durch Mitarbeiter:in/Leitung mit Audit-Spur.

**Code-Referenz:**
- `src/core/views/clients.py` (`ClientUpdateView`)
- `src/core/services/audit_signals.py` (Mutations-Logging)

**Voraussetzung:** Klient:in mit Pseudonym vorhanden.

**Schritte:**
1. Mit `fachkraft` einloggen, Klient:in öffnen.
2. **„Bearbeiten"** klicken, Pseudonym ändern (z.B. Tippfehler korrigieren).
3. Speichern.
4. AuditLog filtern auf `Action=CLIENT_UPDATED` → Eintrag mit altem + neuem Wert.

**Erwartetes Ergebnis:**
- Berichtigung erfolgreich.
- Audit-Spur dokumentiert vorher/nachher.

**Erwarteter Audit-Eintrag:** `CLIENT_UPDATED` mit `changed_fields=['pseudonym']`, `before=…`, `after=…`.

**Status:** ☐ Offen

---

### DSGVO Art. 17 — Löschung

#### AUD-DSGVO-Art17-01 — 4-Augen-Lösch-Antrag

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | fachkraft + leitung | C/F/S || `test_client_deletion_workflow.py` |

**DSGVO-Artikel-Zitat:** *Art. 17 — Recht auf Löschung („Recht auf Vergessenwerden").*

**Code-Referenz:**
- `src/core/views/clients.py` (`ClientDeleteRequestView`)
- `src/core/services/clients.py` (request_deletion)
- `src/core/views/deletion_requests.py` (`DeletionRequestReviewView`)

**Voraussetzung:** eine Klient:in vorhanden, beide Rollen verfügbar.

**Schritte (in zwei Profilen):**
1. **Profil A — `fachkraft`:** Klient:in öffnen → **„Löschung beantragen"** → Begründung eintragen → Absenden.
2. AuditLog: `DELETION_REQUESTED` mit `requested_by=fachkraft`.
3. **Profil B — `leitung`:** `/deletion-requests/` öffnen → Eintrag sichtbar.
4. Auf **„Genehmigen"** klicken → Soft-Delete erfolgt.
5. AuditLog: `DELETION_APPROVED` mit `approved_by=leitung`, `approved_by != requested_by`.
6. **Profil A — `fachkraft`:** Versuchen, eigenen Antrag zu genehmigen → 403, AuditLog `FORBIDDEN`.

**Erwartetes Ergebnis:**
- Vier-Augen-Prinzip wird erzwungen.
- Klient:in landet im Trash (`/clients/trash/`).

**Erwarteter Audit-Eintrag:** `DELETION_REQUESTED`, `DELETION_APPROVED`.

**Status:** ☐ Offen

---

#### AUD-DSGVO-Art17-02 — Trash-Frist und Wiederherstellung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S || `test_client_deletion_workflow.py` |

**DSGVO-Artikel-Zitat:** *Art. 17 — Löschung muss erfolgen, sofern keine Aufbewahrungspflicht entgegensteht.*

**Code-Referenz:**
- `src/core/models/settings.py` (`client_trash_days = 30`)
- `src/core/views/clients.py` (`ClientTrashView`, `ClientRestoreView`)

**Voraussetzung:** AUD-DSGVO-Art17-01 (Klient:in im Trash).

**Schritte:**
1. Mit `admin` einloggen.
2. `/clients/trash/` öffnen → soft-gelöschte Klient:in sichtbar.
3. Stichprobe: Klient:in **wiederherstellen** vor Ablauf (innerhalb 30 Tage).
4. AuditLog: `CLIENT_RESTORED` mit `restored_by=admin`.

**Erwartetes Ergebnis:**
- Wiederherstellung möglich innerhalb 30 Tagen.
- Nach 30 Tagen: automatische Anonymisierung (siehe AUD-DSGVO-Art17-03).

**Erwarteter Audit-Eintrag:** `CLIENT_RESTORED`.

**Status:** ☐ Offen

---

### DSGVO Art. 18 — Einschränkung

> Alle Tests zu „DSGVO Art. 18 — Einschränkung“ sind in [SEKTION D](#sektion-d--entwickler-probes-lokalssh) gelistet (LOKAL/SSH-Probes).

---

### DSGVO Art. 20 — Datenübertragbarkeit

#### AUD-DSGVO-Art20-01 — Maschinenlesbarer Export

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | leitung | C/F/S || `test_client_export.py` |

**DSGVO-Artikel-Zitat:** *Art. 20 — Daten in einem strukturierten, gängigen und maschinenlesbaren Format erhalten.*

**Code-Referenz:** `src/core/services/client_export.py`

**Voraussetzung:** AUD-DSGVO-Art15-01 (JSON-Export liegt vor).

**Schritte:**
1. Heruntergeladenes JSON in einem JSON-Schema-Validator (z.B. `jq` oder Online-Tool) öffnen.
2. Schema-Probe: Top-Level-Felder `client`, `events[]`, `cases[]`, `attachments[]`.
3. Test-Skript: `jq '.events | length' export.json` → Anzahl entspricht Events der Klient:in.
4. Test-Skript: `jq '.client.pseudonym,.client.stage' export.json` → Werte aus UI.

**Erwartetes Ergebnis:**
- JSON ist syntaktisch korrekt und schema-konform.
- Inhalte sind wiederverwertbar (Re-Import in Test-Tool funktioniert).

**Status:** ☐ Offen

---

### DSGVO Art. 25 — Privacy by Design


---

#### AUD-DSGVO-Art25-02 — MFA-Pflicht für privilegierte Rollen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S || `src/tests/test_mfa_login.py` |

**DSGVO-Artikel-Zitat:** *Art. 25 — Voreinstellungen so wählen, dass nur erforderliche Daten verarbeitet werden.*

**Code-Referenz:** `src/core/middleware/mfa.py` (`MFAEnforcementMiddleware`), `src/core/models/user.py` (`mfa_required`)

**Voraussetzung:** Admin-User ohne aktiviertes MFA.

**Schritte:**
1. Settings: `mfa_required=True` für Admin in DB setzen.
2. Mit Admin einloggen → Redirect zu `/mfa/setup/` (Pflicht-Setup).
3. Versuch, vor MFA-Setup auf andere URL zu navigieren → Redirect zurück zu Setup.
4. Nach MFA-Setup: alle Views erreichbar.
5. Logout + erneuter Login → MFA-Verify-Schritt zwingend.

**Erwartetes Ergebnis:**
- Privilegierte Rollen können keine Aktionen ohne MFA durchführen.

**Status:** ☐ Offen

---

### DSGVO Art. 30 — Verarbeitungsverzeichnis

> Alle Tests zu „DSGVO Art. 30 — Verarbeitungsverzeichnis“ sind in [SEKTION D](#sektion-d--entwickler-probes-lokalssh) gelistet (LOKAL/SSH-Probes).

---

### DSGVO Art. 32 — Sicherheit der Verarbeitung

#### AUD-DSGVO-Art32-01 — Sicherheits-HTTP-Header

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance || C || `src/tests/test_security_hardening.py`, `src/tests/test_csp_report.py` |

**DSGVO-Artikel-Zitat:** *Art. 32 — Geeignete TOMs zur Gewährleistung der Sicherheit (Verschlüsselung, Vertraulichkeit, Integrität, Verfügbarkeit, Belastbarkeit).*

**Code-Referenz:**
- `src/anlaufstelle/settings/prod.py` (HSTS, CSRF, X-Frame, …)
- `src/anlaufstelle/settings/base.py` (CSP)

**Voraussetzung:** Prod-ähnliche Konfiguration (`make runserver-prod` oder via Caddy).

**Schritte:**
1. `curl -I https://localhost:8443/login/` (oder vergleichbar gegen Prod-Mirror).
2. Header prüfen:
 - `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
 - `Content-Security-Policy: default-src 'self'; …` (kein `unsafe-eval`)
 - `X-Frame-Options: DENY`
 - `X-Content-Type-Options: nosniff`
 - `Referrer-Policy: strict-origin-when-cross-origin`
 - Cookies: `Secure; HttpOnly; SameSite=Strict` (CSRF) bzw. `SameSite=Lax` (Session)

**Erwartetes Ergebnis:** Alle Header gesetzt, Werte korrekt.

**Status:** ☐ Offen

---

#### AUD-DSGVO-Art32-03 — TLS-in-Transit (Caddy)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance || C |||

**DSGVO-Artikel-Zitat:** *Art. 32 — Vertraulichkeit der Übertragung.*

**Code-Referenz:** `deploy/Caddyfile`, Settings `SECURE_PROXY_SSL_HEADER`

**Voraussetzung:** Prod-ähnliche Konfiguration mit Caddy als Reverse-Proxy.

**Schritte:**
1. `curl -v https://anlaufstelle-prod-mirror.example/` → TLS 1.3, gültiges Zertifikat (Let's Encrypt).
2. `curl -v http://anlaufstelle-prod-mirror.example/` → 301 Redirect auf HTTPS.
3. Test mit `testssl.sh` (extern): keine kritischen Findings, Cipher-Suites mindestens TLS_AES_128_GCM_SHA256 + TLS_AES_256_GCM_SHA384.

**Erwartetes Ergebnis:**
- HTTPS erzwungen, TLS ≥ 1.2, A-Rating bei testssl.sh / SSL-Labs.

**Status:** ☐ Offen

---

### DSGVO Art. 33-34 — Meldepflichten bei Datenpannen


---

#### AUD-DSGVO-Art33-34-02 — Notification-Trigger

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S |||

**DSGVO-Artikel-Zitat:** *Art. 34 — Benachrichtigung der betroffenen Person bei hohem Risiko.*

**Code-Referenz:** `src/core/services/breach_detection.py` (Notify-Hooks)

**Hinweis:** Tatsächliche E-Mail-Versand-Konfiguration muss organisatorisch geprüft werden (DSB benachrichtigt Aufsichtsbehörde, nicht die App).

**Schritte:**
1. Breach-Detection auslösen (siehe AUD-DSGVO-Art33-34-01).
2. Mail-Backend prüfen: Console-Mail oder MailHog enthält Admin-Benachrichtigung.
3. Inhalt: Vorfall-Beschreibung, betroffene User-Anzahl, Empfehlung.

**Erwartetes Ergebnis:**
- Admin-Benachrichtigung wird ausgelöst.
- Organisatorischer Folge-Workflow (DSB → Aufsicht) ist außerhalb der App.

**Status:** ☐ Offen

---

### DSGVO Art. 35 — DSFA

#### AUD-DSGVO-Art35-01 — DSFA-Template-Download

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S || `test_dsgvo_package.py` |

**DSGVO-Artikel-Zitat:** *Art. 35 — Datenschutz-Folgenabschätzung bei voraussichtlich hohem Risiko.*

**Code-Referenz:** `src/core/services/dsgvo_package.py` (DSFA-Template)

**Voraussetzung:** Sudo-Mode.

**Schritte:**
1. `/dsgvo/` öffnen, **„DSFA"** herunterladen.
2. Template prüfen:
 - Beschreibung der Verarbeitung.
 - Notwendigkeit + Verhältnismäßigkeit.
 - Risiken für Betroffene (Re-Identifikation, Stigmatisierung).
 - Geplante Abhilfemaßnahmen (k-Anonymität, Sensitivity-Filter, RLS).
 - Vorab-Konsultation der Aufsichtsbehörde, falls Risiko nicht reduzierbar.

**Erwartetes Ergebnis:**
- DSFA-Template ist vollständig, Facility-spezifische Werte eingesetzt.

**Erwarteter Audit-Eintrag:** `EXPORT` mit `slug=dsfa`.

**Status:** ☐ Offen

---

### Security: RLS-Penetration

#### AUD-SEC-RLS-01 — Cross-Facility Klient:innen-Probe

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin + admin_2 | C || `src/tests/test_rls.py`, `src/tests/test_scope.py` |

**Code-Referenz:**
- `src/core/middleware/facility_scope.py`
- `src/core/migrations/0047_postgres_rls_setup.py`

**Voraussetzung:** zwei Browser-Profile.

**Schritte:**
1. **Profil A — `admin`:** Eine Klient:in aufrufen, UUID aus URL kopieren.
2. **Profil B — `admin_2`:** URL `/clients/<uuid>/` mit der UUID aus Profil A öffnen.
3. Erwartung: 404 (Klient:in „nicht gefunden") — nicht 403.
4. AuditLog: kein Audit-Eintrag (Datensatz existiert für Profil B nicht — RLS).

**Erwartetes Ergebnis:**
- 404 ohne Datenleak.
- Konsistent über alle Modelle: Client, Case, Event, WorkItem.

**Status:** ☐ Offen

---

#### AUD-SEC-RLS-02 — Cross-Facility Case-Probe

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin + admin_2 | C || `src/tests/test_rls.py` |

**Schritte:** Wie AUD-SEC-RLS-01, aber mit Case statt Client.

**Erwartetes Ergebnis:** 404, kein Datenleak.

**Status:** ☐ Offen

---

#### AUD-SEC-RLS-03 — Cross-Facility Event-Probe

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin + admin_2 | C || `src/tests/test_rls.py` |

**Schritte:** Wie AUD-SEC-RLS-01, aber mit Event.

**Status:** ☐ Offen

---

#### AUD-SEC-RLS-04 — Cross-Facility WorkItem-Probe

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin + admin_2 | C || `src/tests/test_rls.py` |

**Schritte:** Wie AUD-SEC-RLS-01, aber mit WorkItem.

**Status:** ☐ Offen

---

#### AUD-SEC-RLS-06 — SQL-Injection-Probe gegen RLS

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | fachkraft | C || `src/tests/test_security_hardening.py` |

**Schritte:**
1. Suche-Query mit SQL-Pattern: `'; DROP TABLE core_client;--`.
2. Erwartung: Django ORM parametrisiert → keine SQL-Injection möglich.
3. AuditLog: keine ungewöhnlichen Aktionen.
4. Pen-Test: `sqlmap` gegen `/search/?q=…` (außerhalb dieser Matrix, separates Audit).

**Status:** ☐ Offen

---

### Security: MFA-Härtung

#### AUD-SEC-MFA-01 — Backup-Code-Reuse-Verbot

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | fachkraft | C/F/S || `test_mfa_backup_codes.py` |

**Code-Referenz:** `src/core/services/mfa.py` (`StaticToken`, One-Time-Use)

**Voraussetzung:** Aktiviertes MFA, Backup-Codes generiert.

**Schritte:**
1. Login → MFA-Verify → einen Backup-Code eingeben (statt TOTP).
2. Login erfolgt.
3. Logout, erneuter Login → denselben Backup-Code erneut versuchen.
4. Erwartung: Fehler „Code bereits verwendet".
5. AuditLog: `BACKUP_CODE_USED` für 1. Versuch, `BACKUP_CODE_REUSE_DENIED` für 2. Versuch (oder MFA_FAILED).

**Erwartetes Ergebnis:**
- Backup-Codes sind One-Time-Use.

**Status:** ☐ Offen

---

#### AUD-SEC-MFA-02 — MFA-Lockout

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | fachkraft | C/F/S |||

**Schritte:**
1. Aktiviertes MFA, Login mit korrektem Pwd.
2. MFA-Verify: 5× falscher Code in Folge.
3. Erwartung: MFA-Lockout (zusätzlich zu Login-Lockout).
4. Audit: `MFA_FAILED` × 5, dann ggf. `MFA_LOCKED`.

**Erwartetes Ergebnis:**
- MFA-Lockout schützt vor Brute-Force-Angriffen auf den 6-stelligen Code.

**Status:** ☐ Offen

---

### Security: Audit-Log-Integrität


---

#### AUD-SEC-AUDIT-03 — HMAC-Email-Hash statt Klartext

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin | C/F/S || `src/tests/test_audit_view.py` |

**Code-Referenz:** `src/core/views/auth.py` (`RateLimitedPasswordResetView`)

**Schritte:**
1. Pwd-Reset auslösen mit unbekannter E-Mail (z.B. `unbekannt@example.com`).
2. AuditLog filtern auf `Action=PASSWORD_RESET_REQUESTED`.
3. Eintrag prüfen: `email_hash` (HMAC-SHA-256) statt Klartext-Email.
4. Vergleich: Klartext-Email ist nirgends im Audit-Log gespeichert.

**Erwartetes Ergebnis:**
- HMAC-Hash sichtbar, Klartext-Email nicht.
- Audit-Log darf bei Datenleck keine Re-Identifikation ermöglichen.

**Status:** ☐ Offen

---

### Security: Verschlüsselung und Key-Rotation

> Alle Tests zu „Security: Verschlüsselung und Key-Rotation“ sind in [SEKTION D](#sektion-d--entwickler-probes-lokalssh) gelistet (LOKAL/SSH-Probes).

---

### Security: HTTP-Header

> Alle Tests zu „Security: HTTP-Header“ sind in [SEKTION D](#sektion-d--entwickler-probes-lokalssh) gelistet (LOKAL/SSH-Probes).

---

### Security: Ops-/Self-Hosting-Härtung

> Alle Tests zu „Security: Ops-/Self-Hosting-Härtung“ — DB-Rollen, Backup, Restore, Media-Volume, Migrations-Drift, Retention-Cron, Healthcheck — sind in [SEKTION D](#sektion-d--entwickler-probes-lokalssh) unter `D.OPS` gelistet. DSGVO-Bezug: Art. 5 Abs. 1 lit. e (Speicherbegrenzung) für `DEV-OPS-07`, Art. 32 (TOM) für `DEV-OPS-01`/`DEV-OPS-03`. Tracking-Issue: #903.

---

## SEKTION D — Entwickler-Probes (LOKAL/SSH)

> **Zielgruppe:** Tobias / Server-Admin. Diese Tests erfordern direkten Zugriff auf den Server (`docker compose exec web python manage.py …`, `psql`, lokale `manage.py`-Befehle). Auf [`dev.anlaufstelle.app`](https://dev.anlaufstelle.app) nur durch Tobias oder per SSH durchführbar. Sie verifizieren Schema-Constraints (`on_delete`-Verhalten, RLS-Force, Encryption-at-Rest, Hash-Ketten) und Betriebsfähigkeit (DB-Rollen, Backup/Restore, Retention-Cron), die in Anwender-Tests nicht prüfbar sind. Aktuelle Fallzahl: siehe [`test-matrix-index.md`](test-matrix-index.md).

### D.CLIENT

#### DEV-CLIENT-15 — PROTECT: Klient mit aktivem Fall lässt sich nicht direkt löschen

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen / Kaskade | admin ||||

**Voraussetzung:** Klient:in mit mindestens einem `Case` (Status egal — entscheidend ist die FK-Beziehung).

**Code-Referenz:**
- `src/core/models/case.py:29-38` — `Case.client on_delete=PROTECT` mit explizitem help_text: „PROTECT verhindert versehentliches Löschen einer Person mit aktiven Fällen."

**Schritte:**
1. `python manage.py shell`.
2. `from django.db.models.deletion import ProtectedError`.
3. `Client.objects.get(id=<uuid>).delete` direkt aufrufen.
4. Den Fall zuerst löschen (`Case.objects.filter(client_id=<uuid>).delete`), dann erneut `Client.delete`.
5. UI-Probe (optional): Versuch über Admin / DeletionRequest-Workflow — Fehlermeldung an User-Oberfläche prüfen.

**Erwartetes Ergebnis:**
- Schritt 3 wirft `ProtectedError` mit Verweis auf den blockierenden Case.
- Schritt 4 funktioniert: nach Case-Löschung lässt sich der Klient löschen.
- Über die UI führt der reguläre Pfad zu einem `DeletionRequest` (Soft-Delete), nicht zum Hard-Delete — der PROTECT-Constraint ist die letzte Verteidigungslinie.

**DSGVO/Security-Note:**
- Schützt vor unbeabsichtigtem Verlust von Fallhistorie (Art. 5 Abs. 1 lit. d Richtigkeit / lit. e Speicherbegrenzung) und vor Audit-Lücken (Art. 30 Verarbeitungsverzeichnis).

**Status:** ☐ Offen

### D.CASE

#### DEV-CASE-13 — CASCADE: Fall löschen entfernt Goals und Milestones

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle / Kaskade | admin ||||

**Voraussetzung:** Fall mit ≥ 3 `OutcomeGoal` und je Goal ≥ 2 `Milestone` (Seed oder manuell anlegen).

**Code-Referenz:**
- `src/core/models/outcome.py:14` — `OutcomeGoal.case on_delete=CASCADE`.
- `src/core/models/outcome.py:40` — `Milestone.goal on_delete=CASCADE`.

**Schritte:**
1. `python manage.py shell` öffnen.
2. Case-UUID notieren, IDs der Goals/Milestones zählen (`Case.objects.get(...).goals.count`, `goal.milestones.count`).
3. `Case.objects.get(id=<uuid>).delete` ausführen.
4. Erneut `OutcomeGoal.objects.filter(case_id=<uuid>).count` und `Milestone.objects.filter(goal__case_id=<uuid>).count`.
5. AuditLog prüfen: erfasst Lösch-Kette oder mindestens den Case-Delete?

**Erwartetes Ergebnis:**
- Alle abhängigen Goals + Milestones sind weg (Count = 0).
- Keine `IntegrityError`; PostgreSQL räumt per CASCADE auf.
- AuditLog: mindestens ein Eintrag für den Case-Delete (Cascade-Nebenwirkungen ggf. nicht protokolliert — Lücke dokumentieren).

**DSGVO/Security-Note:**
- Direktes `Case.delete` umgeht den Vier-Augen-Workflow. In Produktion nur über `DeletionRequest`-Service erlaubt — dieser Test prüft das Schema, nicht den User-Pfad.

**Status:** ☐ Offen

---

#### DEV-CASE-14 — SET_NULL: Fall löschen löst Events ab, behält sie aber

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle / Kaskade | admin ||||

**Voraussetzung:** Fall mit ≥ 2 zugeordneten `Event`s (`event.case = <fall>`).

**Code-Referenz:**
- `src/core/models/event.py:38-40` — `Event.case on_delete=SET_NULL, null=True`.

**Schritte:**
1. Shell: Event-UUIDs notieren, `Event.objects.filter(case_id=<uuid>).values_list('id', 'case_id')` ausgeben.
2. `Case.objects.get(id=<uuid>).delete`.
3. Erneut `Event.objects.filter(id__in=[…]).values_list('id', 'case_id')`.
4. UI-Gegenprobe: Klient-Detail öffnen → Timeline zeigt die Events weiterhin (ohne Fall-Verlinkung).

**Erwartetes Ergebnis:**
- Events existieren weiter, `event.case_id` ist nun `NULL`.
- Keine Daten-Verluste an den Events (Texte, Anhänge bleiben).
- Klient-Timeline zeigt die Events ohne Fall-Badge.

**DSGVO/Security-Note:**
- Wichtig für Art. 5 Abs. 1 lit. e: Dokumentations-Inhalte (Beratungsverlauf) dürfen nicht durch Fall-Bereinigung verloren gehen.

**Status:** ☐ Offen

### D.DEL

#### DEV-DEL-06 — AuditLog: DELETION_REQUESTED + DELETION_APPROVED/REJECTED

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DeletionRequests | leitung + fachkraft ||||

**Voraussetzung:** DEL-01 + DEL-03 + DEL-04 durchgeführt.

**Schritte:**
1. `/audit/?action=delete` aufrufen (AuditLogListView).
2. Suchen nach den Lifecycle-Events:
 - DEL-01 (Antrag): Aktuell schreibt `request_deletion` KEINEN dedizierten `deletion_requested`-AuditLog — der Code legt nur einen `DeletionRequest`-DB-Record an. Audit-Trail entsteht erst bei Approve/Reject.
 - DEL-03 (Approve → Soft-Delete): AuditLog `delete` mit `target_type=Event`, `detail={document_type, client_pseudonym, occurred_at}` (via `soft_delete_event`).
 - DEL-04 (Reject): KEIN AuditLog im Reject-Pfad — der Status-Change am DeletionRequest ist selbst der Audit-Trail (DeletionRequest-Tabelle ist append-only-artig).

**Erwartetes Ergebnis:**
- AuditLog für Approve = action `delete` (von `soft_delete_event`).
- AuditLog für Reject = nicht in `AuditLog`-Tabelle, sondern im `DeletionRequest.status`/`reviewed_*`-Feld.
- Die im Plan-Beschreib genannten Action-Namen `DELETION_REQUESTED`/`DELETION_APPROVED`/`DELETION_REJECTED` existieren NICHT im aktuellen `AuditLog.Action`-Enum. Realer Stand: Lifecycle wird über `DeletionRequest`-Records + `delete`-AuditLog rekonstruiert.
- **Lücke (zu klären):** Falls explizite Audit-Einträge gewünscht sind, ist eine Code-Erweiterung in `request_deletion` / `approve_deletion` / `reject_deletion` nötig.

**DSGVO/Security-Note:**
- Aktueller Audit-Trail genügt Art. 5 (Rechenschaftspflicht), weil `DeletionRequest`-Tabelle die Antragshistorie abbildet (Antragsteller, Reviewer, Zeitpunkte, Reason).

**Status:** ☐ Offen

### D.RET

#### DEV-RET-02 — Bulk-Approve: 5 ablaufende Einträge genehmigen

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | leitung | C/F/S | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** ENT-RET-01, mindestens 5 ablaufende Einträge im Tab „Ablaufende"

**Vorbereitung:**
- `/retention/` geöffnet, Tab „Ablaufende".
- 5 Checkboxen markiert.

**Schritte:**
1. „Bulk-Approve" klicken.
2. Bestätigungsdialog mit „Genehmigen" bestätigen.
3. Anschließend `python manage.py enforce_retention` ausführen.
4. Auf Detail eines genehmigten Eintrags navigieren.

**Erwartetes Ergebnis:**
- 5 Einträge verlassen den Tab „Ablaufende" und erscheinen in „Historie" als „Genehmigt".
- Nach Cron-Lauf: Anonymisierung sichtbar (Pseudonym ersetzt, sensitive Felder geleert).
- Audit-Eintrag pro Datensatz mit Aktor:in `leitung`.

**DSGVO/Security-Note:**
- Auto-Anonymisierung statt Hard-Delete für statistische Verwertbarkeit (Art. 4 Nr. 5 Pseudonymisierung).

**Status:** ☐ Offen

---

#### DEV-RET-04 — Bulk-Reject: 2 Einträge ablehnen → Hard-Delete in Sicht

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | leitung | C/F/S | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** ENT-RET-01, mindestens 2 ablaufende Einträge

**Vorbereitung:**
- `/retention/` geöffnet, Tab „Ablaufende".

**Schritte:**
1. 2 Einträge markieren.
2. „Bulk-Reject" klicken, Bestätigung.
3. Auf nächsten Cron-Lauf via `python manage.py enforce_retention` warten.
4. Anschließend in Admin/DB nach IDs der Einträge suchen.

**Erwartetes Ergebnis:**
- Einträge wandern in „Historie" als „Abgelehnt — wird gelöscht".
- Nach Cron-Lauf: Hard-Delete (Datensatz nicht mehr in DB, nur Audit-Stub bleibt).
- Audit-Eintrag dokumentiert „Hard-Delete genehmigt durch leitung".

**DSGVO/Security-Note:**
- Hard-Delete = Art. 17 Recht auf Löschung. Audit-Stub minimal (nur Aktion, Aktor:in, Zeitpunkt — keine personenbezogenen Daten).

**Status:** ☐ Offen

---

#### DEV-RET-05 — Hold auf einzelnen Eintrag setzen → Auto-Löschung blockiert

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | leitung | C | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** ENT-RET-01, ein ablaufender Eintrag mit Frist < 7 Tage

**Vorbereitung:**
- `/retention/` geöffnet, Tab „Ablaufende".

**Schritte:**
1. Auf Detail eines ablaufenden Eintrags klicken.
2. „Hold setzen" wählen, Begründung („Laufendes Verfahren") eingeben.
3. Hold-Dauer wählen (z.B. „unbegrenzt").
4. `python manage.py enforce_retention` ausführen.
5. Eintrag-Status prüfen.

**Erwartetes Ergebnis:**
- Eintrag wechselt in Tab „Holds".
- POST an `/api/retention/<uuid>/hold/` erfolgreich (HTTP 200).
- Cron-Lauf überspringt Eintrag (kein Anonymize/Delete).
- Audit-Log: „Hold gesetzt durch leitung".

**DSGVO/Security-Note:**
- Hold = berechtigtes Interesse (z.B. laufende Ermittlung) sticht Löschpflicht (Art. 17 Abs. 3 lit. b/e).

**Status:** ☐ Offen

---

#### DEV-RET-06 — Hold dismissen → Frist läuft normal weiter

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | leitung | C | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** ENT-RET-05 abgeschlossen — Eintrag im Hold-Tab

**Vorbereitung:**
- `/retention/` geöffnet, Tab „Holds".

**Schritte:**
1. Eintrag aus Hold-Tab öffnen.
2. „Hold aufheben" klicken, Begründung („Verfahren abgeschlossen").
3. `python manage.py enforce_retention` ausführen.
4. Eintrag-Status prüfen.

**Erwartetes Ergebnis:**
- Eintrag wechselt zurück nach „Ablaufende" oder direkt in „Historie/Anonymisiert", falls Frist bereits überschritten.
- Audit dokumentiert „Hold dismissed durch leitung".

**DSGVO/Security-Note:**
- Nach Wegfall des Hold-Grundes greift Löschpflicht wieder (Art. 17 Abs. 1).

**Status:** ☐ Offen

---

#### DEV-RET-07 — retention_anonymous_days = 90 → Anonyme Klient:in nach 90 Tagen

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | admin | C | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** Settings `retention_anonymous_days = 90`; Anonymous-Klient:in (kein Echtname, nur Pseudonym) seit 91 Tagen.

**Vorbereitung:**
- DB-Backdate: `UPDATE core_client SET created_at = NOW - INTERVAL '91 days' WHERE sensitivity = 'anonymous';`
- `python manage.py enforce_retention --dry-run`.

**Schritte:**
1. Dry-Run-Output prüfen.
2. `python manage.py enforce_retention` (live).
3. Klient-Detail aufrufen.

**Erwartetes Ergebnis:**
- Dry-Run listet anonymen Klient als „würde anonymisiert".
- Nach Live-Run: Pseudonym auf Hash-Prefix gekürzt, Notizen geleert.
- Audit-Eintrag „auto-anonymized (anonymous_days=90)".

**DSGVO/Security-Note:**
- Niedrigste Sensitivität = kürzeste Frist (Datenminimierung Art. 5 Abs. 1 lit. c).

**Status:** ☐ Offen

---

#### DEV-RET-08 — retention_identified_days = 365 → Identifiziert nach 1 Jahr

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | admin | C | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** Settings `retention_identified_days = 365`; identifizierte:r Klient:in seit 366 Tagen.

**Vorbereitung:**
- DB-Backdate für `sensitivity='identified'`.
- Cron noch nicht gelaufen.

**Schritte:**
1. `/retention/` Tab „Ablaufende" — Klient sollte erscheinen.
2. Bulk-Approve auswählen.
3. `python manage.py enforce_retention`.
4. Klient-Detail prüfen.

**Erwartetes Ergebnis:**
- Klient erscheint im Tab „Ablaufende" mit Hinweis „Frist: 365 Tage".
- Nach Approve + Cron: Anonymisierung (Echtname → Pseudonym, Adresse geleert).
- Verknüpfte Events bleiben mit anonymisiertem Klient-Bezug.

**DSGVO/Security-Note:**
- Mittlere Sensitivität = Standardfrist 1 Jahr (Art. 5 Abs. 1 lit. e Speicherbegrenzung).

**Status:** ☐ Offen

---

#### DEV-RET-09 — retention_qualified_days = 3650 → Qualifiziert (Pflicht) 10 Jahre

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | admin | C | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** Settings `retention_qualified_days = 3650`; qualifizierte:r Klient:in (z.B. Hilfeplan §27 SGB VIII) seit 1 Jahr.

**Vorbereitung:**
- Klient mit `sensitivity='qualified'` angelegt.
- Cron läuft täglich.

**Schritte:**
1. `/retention/` Tab „Ablaufende" prüfen → Klient darf NICHT erscheinen.
2. DB-Backdate auf 3651 Tage.
3. `/retention/` erneut prüfen.
4. `python manage.py enforce_retention --dry-run`.

**Erwartetes Ergebnis:**
- Vor Backdate: Klient nicht in „Ablaufende" (Frist 10 Jahre).
- Nach Backdate: Klient erscheint zur Anonymisierungs-Freigabe.
- Dry-Run dokumentiert geplante Aktion mit Frist-Berechnung.

**DSGVO/Security-Note:**
- Höchste Sensitivität = längste Pflicht-Aufbewahrung (Schnittstelle SGB VIII §62 ff., AO §147 Abs. 3 — überlagert Art. 17 DSGVO als spezialgesetzliche Pflicht).

**Status:** ☐ Offen

---

#### DEV-RET-10 — `enforce_retention --dry-run` zeigt Aktionen ohne Schreiben

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | admin (CLI) || ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** ENT-RET-07/08/09 (mehrere ablaufende Datensätze in DB)

**Vorbereitung:**
- Shell-Zugang mit Django-Env.
- DB-Snapshot vor Lauf (`pg_dump`).

**Schritte:**
1. `python manage.py enforce_retention --dry-run` ausführen.
2. Output lesen (geplante Aktionen pro Tabelle).
3. DB-Snapshot mit aktueller DB vergleichen.

**Erwartetes Ergebnis:**
- Stdout listet pro Datensatz: ID, Typ, geplante Aktion (Anonymize/Delete), Begründung.
- DB unverändert (kein Schreibvorgang).
- Exit-Code 0.

**DSGVO/Security-Note:**
- Trockenlauf = Vorab-Kontrolle vor unwiderruflicher Anonymisierung/Löschung (Risikofolgenabschätzung Art. 35).

**Status:** ☐ Offen

---

#### DEV-RET-11 — Event-Sensitivity beeinflusst Aufbewahrung (HIGH vs. NORMAL)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | admin | C |||

**Voraussetzung:** Zwei Events am gleichen Klienten — eines mit `sensitivity=NORMAL`, eines mit `sensitivity=HIGH` (über DocumentType-Mindeststufe gesteuert).

**Code-Referenz:**
- `src/core/services/retention.py` — Frist-Berechnung pro Sensitivity.
- `src/core/models/event.py` — `sensitivity`-Feld + Default-Logik.

**Schritte:**
1. DB-Backdate beide Events um genau das Maximum der NORMAL-Frist (z.B. `retention_event_normal_days`).
2. `python manage.py enforce_retention --dry-run` ausführen.
3. Output filtern auf die beiden Event-IDs.
4. Backdate +1 Tag, erneut `--dry-run`.

**Erwartetes Ergebnis:**
- NORMAL-Event erscheint zur Anonymisierung, HIGH-Event nicht (längere Frist).
- Falls Code nur eine einheitliche Frist kennt: TC dokumentiert die Lücke, Backlog-Eintrag mit Verweis auf SGB VIII §62 ff.
- AuditLog enthält keine Anonymisierungs-Aktion (Dry-Run).

**DSGVO/Security-Note:**
- Art. 5 Abs. 1 lit. e (Speicherbegrenzung) — Sensitivität rechtfertigt unterschiedliche Aufbewahrungsdauern; Pflichtfristen (SGB VIII §62 Abs. 3, AO §147) überlagern Art. 17.

**Status:** ☐ Offen

### D.STAT

#### DEV-STAT-03 — Snapshot via `python manage.py create_statistics_snapshots`

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | admin (CLI) || ⚪ | `test_statistics_snapshot.py` |

**Voraussetzung:** Mindestens 1 Monat Aktivitätshistorie

**Vorbereitung:**
- Shell mit Django-Env.

**Schritte:**
1. `python manage.py create_statistics_snapshots` ausführen.
2. Output prüfen.
3. In `/statistics/` neuen Snapshot-Eintrag suchen.
4. Erneut ausführen — Idempotenz prüfen.

**Erwartetes Ergebnis:**
- Stdout dokumentiert erstellten Snapshot pro Facility/Periode.
- DB enthält neuen Eintrag in `core_statisticssnapshot`.
- Wiederholter Lauf legt keine Duplikate an.

**DSGVO/Security-Note:**
- Snapshot speichert nur Aggregat-Werte (Art. 5 Datenminimierung).

**Status:** ☐ Offen

---

#### DEV-STAT-10 — Mobile-Stats (responsive Charts mit Chart.js)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_statistics_charts.py` |

**Voraussetzung:** Browser-DevTools mit iPhone-Profil. ENT-STAT-01.

**Vorbereitung:**
- Mobile-Viewport.

**Schritte:**
1. `/statistics/` aufrufen.
2. Charts beobachten beim Drehen (Portrait/Landscape).
3. Tap auf Chart-Datapoint → Tooltip prüfen.
4. Filter-Selectoren auf Mobile bedienen.

**Erwartetes Ergebnis:**
- Charts skalieren auf Viewport-Breite.
- Achsenbeschriftung lesbar (ggf. rotiert).
- Tooltip auf Tap sichtbar (Touch-tauglich).
- Filter-Dropdown nutzt natives Mobile-UI.
- KPI-Karten stapeln sich vertikal.

**DSGVO/Security-Note:**
- Mobile-Cache: `Cache-Control: no-store` für Stat-Seite, da Aggregate auf Geräte-Ebene nicht persistiert werden sollen.

**Status:** ☐ Offen

### D.AUDIT

#### DEV-AUDIT-04 — Append-Only-Probe (DSGVO-Beleg Art. 5)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Audit | admin (Shell) || ⚪ ||

**Voraussetzung:** mindestens ein AuditLog-Eintrag in der DB.

**Vorbereitung:**
- SSH/Terminal-Zugang. `python manage.py shell` startbar.

**Schritte:**
1. `python manage.py shell` ausführen.
2. `from core.models.audit import AuditLog`
3. `entry = AuditLog.objects.first`
4. `entry.action = "TAMPERED"; entry.save` versuchen → erwartet `ValueError` oder `IntegrityError`.
5. `entry.delete` versuchen → erwartet `ValueError` oder gleiche Exception.
6. DB-Direktzugriff: `psql` → `UPDATE core_auditlog SET action='X' WHERE id=...;` → Trigger blockiert (PG-Exception).

**Erwartetes Ergebnis:**
- Schritt 4: ValueError mit Message wie „AuditLog ist append-only, save nicht erlaubt".
- Schritt 5: ValueError beim delete.
- Schritt 6: PostgreSQL-Trigger wirft Exception, UPDATE/DELETE rollt zurück.

**DSGVO/Security-Note:**
- **DSGVO-Beleg Art. 5 (Integrität & Vertraulichkeit):** Audit-Log ist immutable und damit gerichtsfest.
- Append-Only ist mehrlagig: Django-Model-Override + PostgreSQL-Trigger (Defense-in-Depth).
- Auch Superuser/Admin-User kann Audit-Log nicht manipulieren.

**Status:** ☐ Offen

---

#### DEV-AUDIT-07 — Cross-Facility-Isolation (RLS)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Audit | admin (zwei Facilities) | C/F/S | ⚪ | `test_audit.py` |

**Voraussetzung:** zwei Facilities mit je eigenen Admin-Usern und je eigenen Audit-Einträgen.

**Vorbereitung:**
- Facility A: Admin-A loggt ein, erzeugt 5 Audit-Einträge.
- Facility B: Admin-B loggt ein, erzeugt 5 Audit-Einträge.

**Schritte:**
1. Als Admin-A in Facility A einloggen.
2. `/audit/` aufrufen — nur die 5 Audit-Einträge der Facility A sichtbar.
3. Versuch: `/audit/<uuid-von-facility-B>/` direkt aufrufen → 404 (RLS blockt).
4. Als Admin-B einloggen, gleicher Test umgekehrt.
5. SQL-Probe: `psql` ohne `SET app.current_facility_id` → 0 Audit-Einträge sichtbar (RLS-Default-Deny).

**Erwartetes Ergebnis:**
- Admin sieht ausschließlich Audit-Einträge der eigenen Facility.
- Direkt-URL auf fremde Facility → 404.
- RLS auf DB-Ebene aktiv (Defense-in-Depth, nicht nur Django-Filter).

**DSGVO/Security-Note:**
- **DSGVO Art. 32 (Mandantentrennung):** Audit-Log ist facility-gescoped via RLS.
- Auch ein kompromittierter Admin-Account kann nicht über Facility-Grenzen lesen.
- AuditLog-Tabelle in `JOIN_TABLES` der RLS-Migration `0047_postgres_rls_setup.py` registriert.

**Status:** ☐ Offen

### D.DSGVO

#### DEV-DSGVO-Art17-03 — Auto-Anonymisierung mit k-Anonymität

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S || `src/tests/test_k_anonymization.py`, `src/tests/test_anonymize_residue.py` |

**DSGVO-Artikel-Zitat:** *Art. 17 — Tatsächliche Löschung oder Anonymisierung nach Ablauf der Aufbewahrungsfrist.*

**Code-Referenz:**
- `src/core/retention/anonymization.py` (`anonymize_clients`)
- `src/core/services/k_anonymization.py` (k=5 Default)
- `src/core/management/commands/enforce_retention.py`

**Voraussetzung:** Backdate-Daten (Klient:in mit `created_at` vor 400 Tagen).

**Schritte:**
1. SQL: `UPDATE core_client SET created_at = NOW - INTERVAL '400 days' WHERE id = '<uuid>'`.
2. `python manage.py enforce_retention --dry-run` ausführen → zeigt geplante Anonymisierungen.
3. `python manage.py enforce_retention` ausführen.
4. Klient:in in DB prüfen: `pseudonym` ist auf k-anon-Cluster gesetzt, alle direkt identifizierenden Felder leer.
5. Events der Klient:in: Sensitive Inhalte gelöscht oder generalisiert.
6. AuditLog: `CLIENT_ANONYMIZED` mit Cluster-Hinweis.

**Erwartetes Ergebnis:**
- k-Anonymität: Klient:in ist mit ≥ 4 anderen ununterscheidbar (k=5).
- Audit-Spur dokumentiert Anonymisierung.

**Erwarteter Audit-Eintrag:** `CLIENT_ANONYMIZED` mit `anonymization_run_id`.

**Status:** ☐ Offen

---

#### DEV-DSGVO-Art18-01 — Hold-Mechanismus blockiert Auto-Löschung

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | leitung | C/F/S || `test_retention_dashboard.py` |

**DSGVO-Artikel-Zitat:** *Art. 18 — Recht auf Einschränkung der Verarbeitung (z.B. bei Streit um Richtigkeit).*

**Code-Referenz:**
- `src/core/views/retention.py` (`RetentionHoldView`, `RetentionDismissHoldView`)
- `src/core/models/legalhold.py` (`LegalHold`)

**Voraussetzung:** Backdate-Daten, Retention-Dashboard offen.

**Schritte:**
1. Mit `leitung` einloggen, `/retention/` öffnen.
2. Eintrag mit ablaufender Frist auswählen → **„Hold setzen"** → Begründung („Klient:in widerspricht Löschung").
3. `python manage.py enforce_retention --dry-run` → der Eintrag erscheint **nicht** in der Anonymisierungs-Liste.
4. Hold dismissen → der Eintrag erscheint wieder.

**Erwartetes Ergebnis:**
- Hold blockiert Auto-Löschung dauerhaft, bis er aufgehoben wird.
- AuditLog: `RETENTION_HOLD_SET` und `RETENTION_HOLD_DISMISSED`.

**Status:** ☐ Offen

---

#### DEV-DSGVO-Art25-01 — RLS aktiv vor App-Logik

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin ||| `src/tests/test_rls.py` |

**DSGVO-Artikel-Zitat:** *Art. 25 — Datenschutz durch Technik („Privacy by Design").*

**Code-Referenz:**
- `src/core/migrations/0047_postgres_rls_setup.py`
- `src/core/middleware/facility_scope.py`
- `src/tests/test_rls.py` (`EXPECTED_TABLES`)

**Migrations-Referenz:** `0047_postgres_rls_setup`, ggf. Folge-Migrationen für neue Tabellen.


**Schritte:**
1. PostgreSQL Connect: `sudo docker compose exec db psql -U postgres anlaufstelle`.
2. SQL: `SELECT tablename FROM pg_tables WHERE schemaname='public' AND rowsecurity=true;`
3. Verifizieren: alle Tabellen aus `EXPECTED_TABLES` (in `src/tests/test_rls.py`) sind RLS-aktiviert.
4. SQL: `SELECT tablename, forcerowsecurity FROM pg_tables WHERE rowsecurity=true;` — alle haben `FORCE ROW LEVEL SECURITY`.
5. SQL ohne `app.current_facility_id`: `SET app.current_facility_id TO ''; SELECT count(*) FROM core_client;` → 0 (keine Daten ohne Facility-Kontext).

**Erwartetes Ergebnis:**
- RLS aktiv auf 19+ Tabellen.
- Ohne gesetzten `app.current_facility_id` keine Daten sichtbar — selbst für Superuser auf SQL-Ebene (FORCE).

**Status:** ☐ Offen

---

#### DEV-DSGVO-Art30-01 — Verarbeitungsverzeichnis-Template

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S || `test_dsgvo_package.py` |

**DSGVO-Artikel-Zitat:** *Art. 30 — Verzeichnis von Verarbeitungstätigkeiten.*

**Code-Referenz:**
- `src/core/management/commands/generate_dsgvo_package.py`
- `src/core/services/dsgvo_package.py`

**Voraussetzung:** Sudo-Mode aktiv.

**Schritte:**
1. `python manage.py generate_dsgvo_package` ausführen → erzeugt Markdown-Templates.
2. Mit `admin` einloggen, Sudo-Mode betreten, `/dsgvo/` öffnen.
3. **„Verarbeitungsverzeichnis"** herunterladen.
4. Inhalt prüfen:
 - Verarbeitungszwecke benannt (Soziale Beratung, Falldokumentation).
 - Rechtsgrundlage (Art. 6 Abs. 1 lit. e — öffentliches Interesse).
 - Datenkategorien aufgeführt.
 - Empfänger benannt (intern/extern).
 - Speicherdauer aus `settings.retention_*_days`.
 - TOMs verlinkt (siehe Art. 32-Template).

**Erwartetes Ergebnis:**
- Template ist vollständig, Facility-spezifisch gerendert.

**Erwarteter Audit-Eintrag:** `EXPORT` mit `slug=verarbeitungsverzeichnis`.

**Status:** ☐ Offen

---

#### DEV-DSGVO-Art32-02 — Encryption-at-Rest (Fernet)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance |||| `src/tests/test_encryption.py` |

**DSGVO-Artikel-Zitat:** *Art. 32 — Verschlüsselung als TOM.*

**Code-Referenz:** `src/core/services/encryption.py` (`MultiFernet`, Key-Rotation)

**Voraussetzung:** ENCRYPTION_KEY gesetzt.

**Schritte:**
1. Event mit Inhalt anlegen (Klartext „Vertraulich-XYZ").
2. PostgreSQL Connect: `sudo docker compose exec db psql -U postgres anlaufstelle`.
3. SQL: `SELECT encrypted_data FROM core_event WHERE id = '<uuid>'`.
4. Verifizieren: Inhalt ist **base64-Fernet-Token**, nicht Klartext.
5. In Django-Shell: Event abrufen und `event.data` prüfen → Klartext sichtbar (Decrypt funktioniert).

**Erwartetes Ergebnis:**
- DB-Spalte enthält verschlüsselten Token.
- App-Layer entschlüsselt korrekt.

**Status:** ☐ Offen

---

#### DEV-DSGVO-Art32-04 — Passwort-Policy & Login-Lockout

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance || C/F/S || `src/tests/test_auth.py` |

**DSGVO-Artikel-Zitat:** *Art. 32 — Schutz vor Brute-Force und schwachen Passwörtern.*

**Code-Referenz:**
- `src/anlaufstelle/settings/base.py` (`AUTH_PASSWORD_VALIDATORS`)
- `src/core/services/login_lockout.py`


**Schritte:**
1. Pwd-Wechsel-Versuch mit schwachem Passwort `12345678` → Fehler (zu kurz, < 12 Zeichen).
2. Pwd-Wechsel mit `password1234` → Fehler (Common-Password).
3. Pwd-Wechsel mit `Anlaufstelle2026!` → Erfolg.
4. Login mit `fachkraft` + falschem Pwd 11× hintereinander.
5. Nach 10. Versuch → AuditLog `LOGIN_LOCKED`, weitere Versuche → 429.
6. Admin entsperrt: `python manage.py shell -c "from core.services.login_lockout import unlock; unlock('fachkraft')"`.

**Erwartetes Ergebnis:**
- 12-Zeichen-Pflicht greift.
- Common-Password-Liste greift.
- Nach 10 Fehlversuchen: Lockout für 15 Min.

**Status:** ☐ Offen

---

#### DEV-DSGVO-Art33-34-01 — Breach-Detection-Command

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin ||| `src/tests/test_breach_detection.py`, `src/tests/test_breach_webhook_ssrf.py` |

**DSGVO-Artikel-Zitat:** *Art. 33 — Meldung an Aufsichtsbehörde binnen 72h. Art. 34 — Benachrichtigung der Betroffenen bei hohem Risiko.*

**Code-Referenz:**
- `src/core/services/breach_detection.py`
- `src/core/management/commands/detect_breaches.py`

**Voraussetzung:** mind. 11 fehlgeschlagene Logins eines Users.

**Schritte:**
1. 11 falsche Login-Versuche mit `fachkraft` (auslösen Lockout).
2. `python manage.py detect_breaches --since=1h` ausführen.
3. Output prüfen: Anomalie wird gemeldet (Login-Burst).
4. Webhook-Konfiguration prüfen: SSRF-Whitelist greift (kein `http://localhost`-Webhook).

**Erwartetes Ergebnis:**
- Anomalie erkannt und protokolliert.
- Webhook-Notification ausgelöst (sofern konfiguriert).

**Erwarteter Audit-Eintrag:** `BREACH_DETECTED` mit `category=login_burst`.

**Status:** ☐ Offen

### D.SEC

#### DEV-SEC-RLS-05 — `app.current_facility_id`-Tampering

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security |||| `src/tests/test_rls.py` |

**Code-Referenz:** `src/core/middleware/facility_scope.py`


**Schritte:**
1. PostgreSQL Connect.
2. SQL: `SET app.current_facility_id TO '<facility-2-uuid>'; SELECT count(*) FROM core_client;`
3. Erwartung: nur Klient:innen aus Facility 2 sichtbar — gemäß RLS-Policy (basierend auf Session-Variable).
4. Test: Facility-1-Admin mit `SET app.current_facility_id TO '<facility-2-uuid>'` → kann er Facility-2-Daten sehen?
 - **Erwartung:** RLS-Policy sollte Owner-Check über `users.facility_id == app.current_facility_id` erzwingen — ggf. nicht direkt möglich, prüfen.

**Status:** ☐ Offen

---

#### DEV-SEC-RLS-07 — Cross-Facility DeletionRequest unsichtbar (Mandantentrennung)

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | leitung (F1) + leitung_2 (F2) | C |||

**Voraussetzung:** zwei Facilities mit jeweils mindestens einem offenen `DeletionRequest`.

**Code-Referenz:**
- `src/core/views/deletion.py` — Listen/Genehmigen-Views.
- `src/core/services/deletion.py` — `DeletionRequest.objects.for_facility(...)`.
- `src/core/middleware/facility_scope.py` — `request.current_facility`.

**Schritte:**
1. F1: als `leitung` einloggen, in F1 ein neues `DeletionRequest` für einen F1-Klienten anlegen (`/clients/<uuid>/request-deletion/`).
2. F2: als `leitung_2` einloggen.
3. `/deletion-requests/` aufrufen → Liste prüfen.
4. Direkt-URL des F1-Antrags aufrufen (`/deletion-requests/<F1-uuid>/`) — versuchen zu genehmigen.
5. Optional in PostgreSQL: ohne `app.current_facility_id` `SELECT count(*) FROM core_deletionrequest;` → 0.

**Erwartetes Ergebnis:**
- Liste zeigt nur F2-Anträge, kein F1-Eintrag.
- Direkt-URL liefert 404 (oder leere QuerySet-Antwort), niemals 200 mit fremden Daten.
- AuditLog protokolliert keinen Zugriff auf F1-Datensatz durch F2-User.

**DSGVO/Security-Note:**
- Art. 5 Abs. 1 lit. f (Integrität & Vertraulichkeit) + Art. 32 (TOM). RLS-Layer auf `core_deletionrequest` muss FORCE-aktiv sein, App-Filter zusätzlich.

**Status:** ☐ Offen

---

#### DEV-SEC-MFA-03 — Recovery-Flow

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | fachkraft + admin | C/F/S |||

**Schritte:**
1. Fachkraft hat alle Backup-Codes verloren + TOTP-App weg.
2. Admin entfernt MFA via `python manage.py shell` oder Admin-UI.
3. Fachkraft loggt sich neu ein → wird zu MFA-Setup geführt (falls `mfa_required=True`).
4. AuditLog: `MFA_DISABLED_BY_ADMIN`.

**Erwartetes Ergebnis:**
- Admin-Recovery möglich, vollständig auditiert.

**Status:** ☐ Offen

---

#### DEV-SEC-AUDIT-01 — Append-Only-Probe

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin ||| `src/tests/test_audit_signals.py`, `src/tests/test_audit_trigger.py` |

**Code-Referenz:** `src/core/models/audit.py` (`AuditLog.save` raises on update, `delete` raises)

**Schritte:**
1. `python manage.py shell` öffnen.
2. `from core.models import AuditLog`
3. `log = AuditLog.objects.first`
4. `log.action = 'TAMPERED'; log.save` → erwarte `ValueError` (Append-Only).
5. `log.delete` → erwarte `ValueError`.
6. Direkt-SQL: `UPDATE core_auditlog SET action='X' WHERE id='<uuid>'` — falls DB-Trigger vorhanden: blockiert. Falls nur App-Layer: SQL umgeht App-Schutz, dann RLS+DB-Trigger ergänzen.

**Erwartetes Ergebnis:**
- App-Layer verhindert UPDATE/DELETE.
- Falls DB-Trigger vorhanden: auch SQL-Direkt-Tampering blockiert.

**Status:** ☐ Offen

---

#### DEV-SEC-AUDIT-02 — Hash-Kette intakt

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin ||| `src/tests/test_audit_service.py` |

**Code-Referenz:** `src/core/services/audit_hash.py`

**Schritte:**
1. `python manage.py shell` öffnen.
2. `from core.services.audit_hash import verify_chain`
3. `verify_chain` → erwartet `True` (alle Einträge konsistent).
4. Direkt-SQL: ein älteres `hash_self`-Feld manipulieren.
5. `verify_chain` erneut → erwartet `False` mit Position des Bruchs.

**Erwartetes Ergebnis:**
- Hash-Kette erkennt nachträgliche Manipulationen.

**Status:** ☐ Offen

---

#### DEV-SEC-ENC-01 — Re-Encrypt-Command

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin ||| `src/tests/test_encryption.py` |

**Code-Referenz:**
- `src/core/services/encryption.py` (MultiFernet-Rotation)
- `src/core/management/commands/reencrypt_fields.py`

**Voraussetzung:** ENCRYPTION_KEYS mit 2 Schlüsseln (alt+neu).

**Schritte:**
1. `ENCRYPTION_KEYS=NEW_KEY,OLD_KEY` setzen (neuer Key zuerst → für Encrypt).
2. `python manage.py reencrypt_fields --dry-run` → zeigt geplante Verschlüsselungs-Updates.
3. `python manage.py reencrypt_fields` → re-encrypt aller verschlüsselten Felder mit NEW_KEY.
4. ENCRYPTION_KEYS auf `NEW_KEY` reduzieren (OLD_KEY entfernen).
5. App neu starten → Daten weiterhin lesbar.

**Erwartetes Ergebnis:**
- Key-Rotation ohne Datenverlust.
- Audit: `ENCRYPTION_REENCRYPTED`.

**Status:** ☐ Offen

---

#### DEV-SEC-HEAD-01 — Header-Smoke gegen Prod-Mirror

> 🔧 **LOKAL/SSH erforderlich.** Dieser Case benötigt Server-Zugriff (`docker compose exec web python manage.py …`, `psql`, oder lokale `manage.py`-Befehle). **Auf dev.anlaufstelle.app nur durch Tobias / per SSH auf dev-Server durchführbar.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security |||| `src/tests/test_security_hardening.py` |

**Schritte:**
1. `curl -sI https://anlaufstelle-prod-mirror.example/` → alle Sicherheits-Header gesetzt.
2. `curl -sI -H "Origin: http://evil.example" https://...` → CORS-Antwort prüfen (kein `Access-Control-Allow-Origin: *`).
3. Browser-DevTools → Security-Panel → kein „mixed content".
4. Optional: securityheaders.com gegen Prod (extern).

**Erwartetes Ergebnis:**
- A+ bei securityheaders.com.
- Keine offenen CORS-Lecks.

**Status:** ☐ Offen

---

### D.OPS

> Refs #903 ( + §4.6): Ops-/Self-Hosting-Härtung. DSGVO-relevant über Art. 5 (Speicherbegrenzung) und Art. 32 (TOM) — werden aus Sektion C verlinkt, leben aber als LOKAL/SSH-Probes hier.

#### DEV-OPS-01 — DB-Rollen-Check (App NOSUPERUSER, kein BYPASSRLS; Admin BYPASSRLS)

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin ||||

**Code-Referenz:**
- [`docker-compose.dev.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.dev.yml) — Bootstrap/App/Admin-Rollen
- [`deploy/postgres-init/01-app-role.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/postgres-init/01-app-role.sh)
- Prod-Pfad: #902 (docker-compose.prod auf gleiches Modell)

**Schritte:**
1. `docker compose exec db psql -U $POSTGRES_USER $POSTGRES_DB`
2. `\du` ausführen — App-Rolle und Admin-Rolle prüfen.
3. App-Rolle: `Attributes` enthält **nicht** `Superuser`, **nicht** `Bypass RLS`.
4. Admin-Rolle: `Attributes` enthält `Bypass RLS` (für Maintenance/Migrationen).
5. Optional per SQL: `SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname IN ('<app>', '<admin>');`.

**Erwartetes Ergebnis:**
- App-Rolle: `rolsuper=f`, `rolbypassrls=f`.
- Admin-Rolle: `rolbypassrls=t`.
- Bei Abweichung: Healthcheck/Compliance-Dashboard (#919) muss `critical` melden.

**Status:** ☐ Offen

---

#### DEV-OPS-02 — Backup-Frische (jünger als 24h)

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin ||||

**Code-Referenz:** [`deploy/backup.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/backup.sh), [`docs/ops-runbook.md`](https://github.com/anlaufstelle/app/blob/main/docs/ops-runbook.md)

**Schritte:**
1. SSH auf Host: `ssh anlaufstelle@dev.anlaufstelle.app`.
2. Backup-Verzeichnis prüfen (per Runbook): `ls -lh /var/backups/anlaufstelle/ | head`.
3. Neuestes Backup: `find /var/backups/anlaufstelle/ -mtime -1 -type f`.
4. Optional: Backup-Cron-Log einsehen (z.B. `journalctl -u anlaufstelle-backup` oder `/var/log/syslog`).

**Erwartetes Ergebnis:**
- Mindestens ein Backup jünger als 24 h.
- Dateigröße plausibel (≥ Vortagswert ± 30 %).
- Compliance-Dashboard (#919) zeigt `ok`.

**Status:** ☐ Offen

---

#### DEV-OPS-03 — Restore-Test gegen frische DB

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin ||||

**Code-Referenz:** [`deploy/restore.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/restore.sh) (falls vorhanden), Runbook „Disaster Recovery".

**Voraussetzung:** dedizierte Test-DB (z.B. `anlaufstelle_restore_test`), niemals gegen Produktiv-DB.

**Schritte:**
1. Frisches Backup auswählen (z.B. `*.sql.gz` aus DEV-OPS-02).
2. Leere Test-DB anlegen.
3. Backup einspielen (`pg_restore` oder `psql < dump.sql`).
4. Smoke-Query: `SELECT count(*) FROM core_client;`, `SELECT count(*) FROM core_event;`.
5. Optional Django-Side: `DATABASE_URL=...test_db python manage.py check`.

**Erwartetes Ergebnis:**
- Restore läuft ohne Fehler.
- Zählungen entsprechen Erwartung.
- Datum/Zeit des letzten erfolgreichen Restore-Tests wird im Compliance-Dashboard angezeigt.
- **DSGVO-Beleg:** Art. 32 Abs. 1 lit. c (Wiederherstellbarkeit).

**Status:** ☐ Offen

---

#### DEV-OPS-04 — Media-Volume Persistenz

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin ||||

**Code-Referenz:** [`docker-compose.prod.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.prod.yml) — Volume-Konfiguration für `src/media/`.

**Schritte:**
1. In App: Datei hochladen (z.B. via Klient-Detail → Attachment).
2. SSH: Datei im Host-Volume sichtbar (`docker compose exec web ls /app/src/media/...`).
3. `docker compose restart web` ausführen.
4. Datei in App weiterhin abrufbar (Download-Link funktioniert).
5. `docker compose down && docker compose up -d`.
6. Datei nach erneutem Up immer noch da.

**Erwartetes Ergebnis:**
- Anhänge überleben Container-Restart und Stack-Restart.
- Volume ist im Compose als persistenter Named-Volume oder Bind-Mount konfiguriert.

**Status:** ☐ Offen

---

#### DEV-OPS-05 — collectstatic nach Image-Pull erfolgreich

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin ||||

**Code-Referenz:** [`deploy/deploy-dev.sh`](https://github.com/anlaufstelle/app/blob/main/deploy/deploy-dev.sh) oder Entrypoint im App-Container.

**Schritte:**
1. Neues Image pullen: `docker compose pull web`.
2. Stack hochfahren: `docker compose up -d`.
3. Statics prüfen: `curl -I https://dev.anlaufstelle.app/static/css/styles.css` → 200.
4. Whitenoise-Manifest existiert: `docker compose exec web ls staticfiles/staticfiles.json` oder Pendant.
5. App lädt im Browser ohne fehlende Assets (DevTools-Network).

**Erwartetes Ergebnis:**
- Statics werden nach Pull automatisch eingesammelt (Entrypoint oder Compose-Command).
- Keine 404-Spam in Caddy-Logs.

**Status:** ☐ Offen

---

#### DEV-OPS-06 — Migrations-Drift (`migrate --check`)

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin ||| `src/tests/test_migrations.py` (falls vorhanden) |

**Schritte:**
1. SSH: `docker compose exec web python manage.py migrate --check` → Exit-Code 0.
2. `docker compose exec web python manage.py makemigrations --check --dry-run` → keine ausstehenden Migrationen.
3. Optional `django_migrations`-Tabelle einsehen — neuestes Datum stimmt mit Code-Stand überein.

**Erwartetes Ergebnis:**
- Beide Checks Exit 0.
- Bei Drift: Deploy ist unvollständig — neu deployen oder manuell `migrate` ausführen.

**Status:** ☐ Offen

---

#### DEV-OPS-07 — Retention-Cron Output

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops | admin ||| `src/tests/test_retention_*.py` |

**Code-Referenz:**
- [`src/core/management/commands/enforce_retention.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/enforce_retention.py)
- Cron/systemd-Timer-Konfiguration im Runbook.

**Schritte:**
1. SSH: Cron-Log einsehen — `enforce_retention` lief in den letzten 24 h.
2. Letzter erfolgreicher Lauf: Audit-Eintrag `RETENTION_EXECUTED` in AuditLog vorhanden.
3. `docker compose exec web python manage.py enforce_retention --dry-run` → zeigt aktuelle Pipeline.
4. Bei Fehlerfall: Audit-Eintrag `RETENTION_FAILED` mit Begründung.

**Erwartetes Ergebnis:**
- Cron läuft, Audit ist vollständig.
- Compliance-Dashboard (#919) zeigt Datum des letzten erfolgreichen Laufs.
- **DSGVO-Beleg:** Art. 5 Abs. 1 lit. e (Speicherbegrenzung).

**Status:** ☐ Offen

---

#### DEV-OPS-08 — Healthcheck unterscheidet ok/degraded/critical

> 🔧 **LOKAL/SSH erforderlich.**

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Ops |||| `src/tests/test_health_endpoint.py` (falls vorhanden) |

**Code-Referenz:** Health-View (siehe `ENT-SYS-01`, `ENT-SYS-02`).

**Schritte:**
1. `curl -s https://dev.anlaufstelle.app/health/` → JSON mit Status `ok`.
2. ClamAV-Container stoppen: `docker compose stop clamav`.
3. Health-Endpoint erneut → erwartet `degraded` (App läuft, aber ClamAV down).
4. DB-Container stoppen: `docker compose stop db`.
5. Health-Endpoint erneut → erwartet `critical` (App nicht funktionsfähig).
6. Container wieder hochfahren, Status zurück auf `ok`.

**Erwartetes Ergebnis:**
- Drei Stufen sind unterscheidbar.
- Externes Monitoring (UptimeRobot o.ä.) kann auf `degraded` warnen statt erst auf hartem 5xx.

**Status:** ☐ Offen

---

Übersicht: pro Bereich, in welchen Browsern und auf Mobile getestet werden muss. `✓` = Pflicht, `⚪` = Stichprobe, `—` = nicht relevant.

| Bereich | Chromium | Firefox | Safari/WebKit | Mobile (iPhone) |
|---------|----------|---------|---------------|------------------|
| AUTH | ✓ | ✓ | ✓ | ⚪ |
| MFA | ✓ | ✓ | ✓ | ⚪ |
| ACCT | ✓ | ⚪ | ⚪ | ⚪ |
| SUDO | ✓ | ⚪ | ⚪ ||
| PWA | ✓ | ⚪ | ✓ | ✓ |
| CLIENT | ✓ | ⚪ | ⚪ | ✓ |
| CASE | ✓ | ⚪ | ⚪ | ⚪ |
| EPI | ✓ | ⚪ | ⚪ | ⚪ |
| GOAL | ✓ | ⚪ | ⚪ | ⚪ |
| EVT | ✓ | ✓ | ⚪ | ✓ |
| ATT | ✓ | ✓ | ⚪ | ✓ |
| WI | ✓ | ⚪ | ⚪ | ✓ |
| DEL | ✓ | ⚪ | ⚪ ||
| RET | ✓ | ⚪ | ⚪ | ⚪ |
| SRCH | ✓ | ⚪ | ⚪ | ✓ |
| ZS | ✓ | ⚪ | ⚪ | ✓ |
| HOV | ✓ | ⚪ | ⚪ | ✓ |
| STAT | ✓ | ⚪ | ⚪ | ⚪ |
| AUDIT | ✓ | ⚪ | ⚪ ||
| DSGVO | ✓ | ⚪ | ⚪ ||
| OFFL | ✓ | ⚪ | ✓ | ✓ |
| SYS | ✓ | ⚪ | ⚪ ||
| HTMX-Toasts (siehe Anhang B) | ✓ | ✓ | ⚪ | ⚪ |

**Konvention:** Jeder Bereich wird **mindestens** in Chromium komplett durchgespielt. Firefox/Safari/Mobile-Stichproben (`⚪`) bedeuten: einmal pro Release prüfen, nicht jeden Test-Lauf.

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

- Profile A + Profile B (zwei verschiedene Browser/Profile) öffnen denselben Klient:in-Datensatz im Edit-Modus.
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

- Mehrere `hx-boost`-Navigationen durchführen (z.B. Klient → Case → Episode).
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

> **Aktuelle Coverage-Bilanz:** [`test-matrix-index.md`](test-matrix-index.md) — wird per `python scripts/build_test_matrix_index.py` aus dieser Matrix generiert. Die Zahlen pro Sektion (gesamt, mit/ohne E2E, Quote) sind dort sektionsweise tabelliert.

Methodik:

- **„Doppelt abgedeckt":** Pro Case die `E2E`-Spalte prüfen — falls nicht `—`, gilt der Case als doppelt abgedeckt.
- **Manuell-only:** Cases mit `—` in der `E2E`-Spalte.
- **Datenbasis für Folge-Tickets:** Manuell-only-Cases mit hoher Frequenz (jeder Release-Lauf) sind Kandidaten für Automatisierung. Tickets im Issue-Tracker mit Label `automate-manual-test` anlegen — die Liste wird voraussichtlich mit #916 und #909 ergänzt.

> Hinweis: Bis #909 (auto-befüllter Anhang C) umgesetzt ist, sind die per-Bereich-Zahlen nicht in dieser Datei gepflegt — nur die Sektions-Gesamtzahlen im Index sind aktuell.

---

## Anhang D — Test-Daten-Cheatsheet

Aus `src/core/management/commands/seed.py` extrahiert: was wird wie geseedet, in welcher Skalierung.

### D.1 — Standard-Logins

Passwort für alle Seed-User: `anlaufstelle2026`

| Username | Rolle | Facility | Verwendung |
|----------|-------|----------|------------|
| `admin` | ADMIN | 1 | Volle Rechte, Audit, DSGVO-Paket |
| `leitung` (Seed-Variante: `thomas`) | LEAD | 1 | Cases schließen, Retention, Statistik |
| `fachkraft` (Seed-Variante: `miriam`) | STAFF | 1 | Standard-Beratung, Klient/Event-CRUD |
| `assistenz` (Seed-Variante: `lena`) | ASSISTANT | 1 | Niedrigste Rolle, RBAC-Negativtests |
| `admin_2`, `leitung_2`, `fachkraft_2`, `assistenz_2` | je 1 | 2 | Cross-Facility-/RLS-Tests (`make seed FACILITIES=2`) |

> **Hinweis:** Die genauen Seed-Usernamen können je nach `seed.py`-Variante abweichen (`admin`/`thomas`/`miriam`/`lena` vs. `admin`/`leitung`/`fachkraft`/`assistenz`). Vor Test-Lauf kurz `python manage.py shell -c "from django.contrib.auth import get_user_model; print(list(get_user_model.objects.values_list('username', flat=True)))"` ausführen.

### D.2 — Seed-Skalierung

| Skalierung | Klient:innen | Events | Cases | WorkItems | Aufruf |
|------------|--------------|--------|-------|-----------|--------|
| Small (Default) | ~10 | ~20 | ~5 | ~10 | `make seed` |
| Medium | ~50 | ~100 | ~20 | ~50 | `make seed SCALE=medium` |
| Large (Last-Smoke) | ~1000 | ~5000 | ~200 | ~500 | `make seed SCALE=large` |

Quelle: `src/core/management/commands/seed.py` und Helper-Funktionen `seed_clients_small/bulk`, `seed_events_small/bulk`, etc.

### D.3 — Stamm-Daten (Document-Types, Activities, FieldTemplates)

Aus `seed.py` werden geseedet:
- **Document-Types:** „Beratung", „Krise", „Verlaufsbericht", „Übergabe", „Anonymes Erstgespräch", … (8–12 Typen)
- **Activities:** „Telefonat", „Hausbesuch", „Anlaufstelle", „Streetwork", „E-Mail", „Schriftverkehr", …
- **FieldTemplates:** Pro Document-Type 5–15 dynamische Felder (Datum, Sensitivität, Kategorie, Freitext, Multi-Choice, …)
- **Settings:** Default-Aufbewahrungsfristen, MFA-Pflicht, k-Anonymität, Retention-Auto-Approve.
- **TimeFilters:** „Heute", „Diese Woche", „Letzten 30 Tage", „Quartal", …

### D.4 — Spezial-Daten

- **Deletion-Requests:** 2–3 offene + 1 genehmigter Beispiel-Antrag.
- **Retention-Proposals:** 2–3 ablaufende Einträge (für Bulk-Approve-Test).
- **AuditLog-Snapshot:** 50–100 Einträge aus Seed-Vorgang.

### D.5 — Reset-Workflow für sauberen Test-Lauf

```bash
# DB-Reset + Re-Seed
make reset-db
make seed

# Oder manuell:
sudo docker compose down
sudo docker compose up -d db
sudo docker compose exec db psql -U postgres -c 'DROP DATABASE anlaufstelle'
sudo docker compose exec db psql -U postgres -c 'CREATE DATABASE anlaufstelle'
make migrate
make seed
```

---

**Letzte Aktualisierung:** 2026-05-09 · Pflege durch: Tobias Nix · Issue: #864
