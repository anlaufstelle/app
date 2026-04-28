# FAQ — Zentrale Wissensdatenbank

Sammlung häufig gestellter Fragen zur Konfiguration, Datenschutz und Betrieb der Anlaufstelle.
Sortiert nach Onboarding-Reihenfolge: Erstkonfiguration → Tägliche Arbeit → Rollen & Datenschutz → Betrieb.

### Inhaltsverzeichnis

**A. Erste Schritte & Konfiguration**
1. [Wie stelle ich den Standard-Dokumentationstyp ein?](#1-wie-stelle-ich-den-standard-dokumentationstyp-ein)

**B. Tägliche Arbeit**
2. [Wie funktioniert der Zeitstrom?](#2-wie-funktioniert-der-zeitstrom)
3. [Wie funktioniert die Übergabe (Schichtübergabe)?](#3-wie-funktioniert-die-übergabe-schichtübergabe)

**C. Rollen & Datenschutz**
4. [Wie funktionieren Zugriffsberechtigungen?](#4-wie-funktionieren-zugriffsberechtigungen)
5. [Was bedeutet die Sensitivitätsstufe?](#5-was-bedeutet-die-sensitivitätsstufe-niedrigmittelhoch)
6. [Wie funktioniert das Löschsystem (4-Augen-Prinzip)?](#6-wie-funktioniert-das-löschsystem-4-augen-prinzip)
7. [Was hat KEINEN Löschmechanismus?](#7-was-hat-keinen-löschmechanismus)

**D. Administration & Betrieb**
8. [Wie funktionieren Aufbewahrungsfristen?](#8-wie-funktionieren-aufbewahrungsfristen)
9. [Welche automatisierten Scripts gibt es?](#9-welche-automatisierten-scripts-gibt-es)

---

## A. Erste Schritte & Konfiguration

### 1. Wie stelle ich den Standard-Dokumentationstyp ein?

**Administration → Einstellungen → Feld „Standard-Dokumentationstyp"** auswählen. Der gewählte Typ wird beim Öffnen von „Neuer Kontakt" automatisch vorausgewählt und die dynamischen Felder direkt geladen.

**Relevante Dateien:**
- [`src/core/models/settings.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/settings.py) — `default_document_type` ForeignKey
- [`src/core/views/events.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/events.py) — `EventCreateView.get()` liest den Default

---

## B. Tägliche Arbeit

### 2. Wie funktioniert der Zeitstrom?

Der Zeitstrom ist die **Startseite** der Anwendung (`/`). Er zeigt einen chronologischen Aktivitäts-Feed für den aktuellen Tag — eine Kombination aus Dashboard, Aktivitätslog und Timeline.

#### Welche Daten zeigt der Zeitstrom?

Vier Quellen werden zu einem Feed zusammengeführt:

| Quelle | Was wird angezeigt |
|--------|--------------------|
| **Ereignisse** (Events) | Dokumentationseinträge mit Vorschaufeldern |
| **Aktivitäten** (Activities) | System-Operationen (erstellt, bearbeitet, gelöscht …) |
| **Arbeitsaufträge** (WorkItems) | Aufgaben und Hinweise mit Priorität und Status |
| **Hausverbote** (Bans) | Aktive Hausverbote — zusätzlich als rotes Banner oben |

Alles wird nach Zeitpunkt absteigend sortiert (max. 200 Einträge pro Typ).

#### Wie funktioniert die Schichtfilterung?

- Oben stehen **TimeFilter-Tabs** (z.B. Frühdienst 08:00–16:00, Nachtdienst 22:00–08:00).
- Beim Klick wird der Feed per **HTMX** auf das gewählte Zeitfenster eingeschränkt.
- Nachtschichten mit Mitternachts-Überlappung (z.B. 22:00–08:00) werden korrekt behandelt.
- Passt die aktuelle Uhrzeit zu einer Schicht, wird diese **automatisch vorausgewählt**.

#### Was ist die Schichtübergabe?

Wenn ein Schichtfilter aktiv ist, erscheint ein aufklappbarer **Schichtübergabe-Block** mit:
- Statistiken (Anzahl Ereignisse, Aktivitäten, neue Aufgaben …)
- Highlights: Krisen-Ereignisse, neue Hausverbote, dringende Aufgaben

#### Was steht in der Sidebar?

Die rechte Spalte zeigt die **5 dringendsten offenen Arbeitsaufträge** (Status OPEN/IN_PROGRESS), sortiert nach Priorität → Fälligkeitsdatum → Erstelldatum.

#### Wie funktioniert die HTMX-Interaktion?

Filter-Änderungen (Schicht, Typ, Dokumentationstyp) lösen einen HTMX-Request aus. Nur der Feed-Container wird ersetzt — kein Full-Page-Reload.

#### Was ist Zeitstrom 2.0?

Ein **experimentelles** alternatives UI unter `/zeitstrom-v2/`:

| | Zeitstrom | Zeitstrom 2.0 |
|---|-----------|---------------|
| **Darstellung** | Karten-Feed | Tabelle mit Zeilen |
| **Zeitraum** | Ein Tag (Datumsnavigation) | Alle Ereignisse (Datumsbereich-Filter) |
| **Primärfilter** | Schicht-Tabs | Volltextsuche (Klientel-Pseudonym) |
| **Sekundärfilter** | Dokumentationstyp | Dokumentationstyp + Datumsbereich |
| **Paginierung** | Nein (max. 200) | Ja (50 pro Seite) |
| **Zweck** | Tagesübersicht / Schichtübergabe | Suche / Audit-Trail |

**Relevante Dateien:**
- [`src/core/views/zeitstrom.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/zeitstrom.py) — `ZeitstromView`, `ZeitstromFeedPartialView`
- [`src/core/services/feed.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/feed.py) — `build_feed_items()`, `enrich_events_with_preview()`
- [`src/core/services/handover.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/handover.py) — `build_handover_summary()`
- [`src/core/views/zeitstrom_v2.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/zeitstrom_v2.py) — `ZeitstromV2View` (Experiment)

---

### 3. Wie funktioniert die Übergabe (Schichtübergabe)?

Die **Übergabe** ist ein Dashboard für den Schichtwechsel — ersetzt das analoge A4-Übergabebuch.

**Schichten** (konfigurierbar pro Einrichtung in Administration → Zeitfilter):

| Schicht | Zeitraum | Besonderheit |
|---------|----------|-------------|
| Frühdienst | 08:00–16:00 | Standard-Schicht |
| Spätdienst | 16:00–22:00 | — |
| Nachtdienst | 22:00–08:00 | Mitternachts-Wraparound |

**Was zeigt die Seite?**

| Bereich | Inhalt | Zeitscope |
|---------|--------|-----------|
| **Statistik-Grid** | Kontakte, Aktivitäten, neue Aufgaben, neue Klienten | Nur innerhalb der Schicht |
| **Wichtige Vorkommnisse** | Krisengespräche (gelb), Hausverbote (rot), dringende Aufgaben (blau) | Nur innerhalb der Schicht |
| **Offene Aufgaben** | Sortiert nach Priorität (dringend → wichtig → normal), max. 10 | Einrichtungsweit, alle Zeiten |
| **Event-Aufschlüsselung** | Kontakte nach Dokumentationstyp mit farbigen Badges | Nur innerhalb der Schicht |

**Automatisches Verhalten:**
- Öffnet man die Übergabe **heute**, wird automatisch die **vorherige Schicht** vorausgewählt
- Datums-Navigation (vor/zurück) + Tab-Wechsel zwischen Schichten und „Ganzer Tag"
- Nachtschichten über Mitternacht werden korrekt via `covers_time()`-Logik behandelt
- Kompakte Version erscheint auch im Zeitstrom, wenn ein Schichtfilter aktiv ist

**Relevante Dateien:**
- [`src/core/views/handover.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/handover.py) — `HandoverView`
- [`src/core/services/handover.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/handover.py) — `build_handover_summary()`
- [`src/core/models/time_filter.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/time_filter.py) — `TimeFilter` (Schichtdefinition)
- [`src/templates/core/handover/index.html`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/templates/core/handover/index.html) — Haupttemplate

---

## C. Rollen & Datenschutz

### 4. Wie funktionieren Zugriffsberechtigungen?

Zugriffsberechtigungen werden über drei Ebenen gesteuert: **Rolle**, **Einrichtung** und **Sensitivitätsstufe**. Zusammen bestimmen sie, welcher User welche Daten sehen und welche Aktionen ausführen darf.

#### Die vier Rollen

Jeder User hat genau eine Rolle. Die Rollen bilden eine aufsteigende Hierarchie — höhere Rollen umfassen alle Rechte der niedrigeren:

| Rolle | DB-Wert | Beschreibung |
|-------|---------|-------------|
| **Assistenz** | `assistant` | Grundzugriff: Kontakte anlegen, Zeitstrom und Klientel einsehen |
| **Fachkraft** | `staff` | Vollzugriff auf Fallarbeit: Klienten, Fälle, Episoden, Ziele anlegen und bearbeiten |
| **Leitung** | `lead` | Zusätzlich: Fälle schließen, Statistiken, Löschanträge genehmigen, Datenexport |
| **Administrator** | `admin` | Vollzugriff: Audit-Log, DSGVO-Paket, Administrationsoberfläche |

Die Rolle wird direkt auf dem User-Modell gespeichert (`User.role`). Die View-Schicht prüft Rollen über Properties wie `user.is_staff_or_above` oder `user.is_lead_or_admin`.

#### Einrichtungs-Scoping (Datenisolation)

Jeder User gehört zu genau einer **Einrichtung** (Facility). Datenisolation ist automatisch:

- Die Middleware `FacilityScopeMiddleware` setzt pro Request `request.current_facility`
- Alle Datenmodelle (Klientel, Kontakte, Fälle, Arbeitsaufträge, Audit-Logs …) verwenden den `FacilityScopedManager`
- Queries werden über `.for_facility(facility)` gefiltert — ein User sieht **ausschließlich Daten seiner eigenen Einrichtung**
- Es gibt keine einrichtungsübergreifende Sicht, auch nicht für Administratoren

#### Zugriffstabelle: Wer darf was?

| Bereich | Aktion | Mindestrolle |
|---------|--------|--------------|
| Zeitstrom, Übergabe | Anzeigen | Assistenz |
| Suche | Volltextsuche | Assistenz |
| Klientel | Anzeigen, Suchen | Assistenz |
| Klientel | Anlegen, Bearbeiten | Fachkraft |
| Klientel | Datenexport (JSON/PDF) | Leitung |
| Kontakte (Events) | Anlegen, Anzeigen | Assistenz |
| Kontakte | Bearbeiten | Assistenz (eigene) / Fachkraft (alle) |
| Kontakte | Löschen | Fachkraft (eigene) / Leitung (alle) |
| Löschanträge | Anzeigen, Genehmigen/Ablehnen | Leitung |
| Fälle, Episoden, Ziele | Anzeigen, Anlegen, Bearbeiten | Fachkraft |
| Fälle | Schließen, Wiederöffnen | Leitung |
| Arbeitsaufträge | Anzeigen | Assistenz |
| Arbeitsaufträge | Anlegen, Bearbeiten | Fachkraft |
| Arbeitsaufträge | Status ändern | Ersteller, Zugewiesener oder Leitung |
| Statistiken & Exports | Anzeigen, CSV/PDF/Jugendamt-Export | Leitung |
| Audit-Log | Anzeigen | Administrator |
| DSGVO-Paket | Generieren, Herunterladen | Administrator |
| Administration | Zugang | Administrator |

#### Sichtbarkeit in der Oberfläche

Navigation und Aktions-Buttons passen sich automatisch der Rolle an:

- **Sidebar/Navigation:** „Fälle" erscheint erst ab Fachkraft, „Statistiken" und „Löschanträge" ab Leitung, „Audit-Log", „DSGVO" und „Administration" nur für Administratoren
- **Erstellen-Menü:** „Neuer Klient", „Neuer Arbeitsauftrag" und „Neuer Fall" erst ab Fachkraft sichtbar
- **Detail-Seiten:** „Bearbeiten"- und „Löschen"-Buttons je nach Rolle und Eigentümerschaft ein-/ausgeblendet

#### Feingranulare Sonderregeln

Neben der Grundrolle gibt es situationsabhängige Berechtigungen:

| Regel | Beschreibung |
|-------|-------------|
| **Eigentümer-Berechtigung** | Assistenz darf eigene Kontakte bearbeiten, auch wenn „Bearbeiten" sonst ab Fachkraft gilt |
| **Zuweisungs-Berechtigung** | Arbeitsauftrag-Status kann vom Ersteller, Zugewiesenen oder der Leitung geändert werden |
| **Sensitivitätsstufe** | Zusätzlich zur Rolle steuern `DocumentType.sensitivity` und `FieldTemplate.sensitivity` den Feldzugriff. `FieldTemplate.sensitivity` überschreibt den DocumentType-Level nach oben (→ [FAQ #5](#5-was-bedeutet-die-sensitivitätsstufe-niedrigmittelhoch)) |
| **Passwort-Pflicht** | Bei gesetztem `must_change_password`-Flag wird der User vor jeder Aktion zum Passwortwechsel gezwungen |
| **Session-Timeout** | Konfigurierbar pro Einrichtung (Standard: 30 Min.), nach Ablauf automatischer Logout |

#### Technische Steuerungsparameter

| Parameter | Wo | Wirkung |
|-----------|----|--------|
| `User.role` | User-Modell | Bestimmt die Grundrolle |
| `User.facility` | User-Modell (FK) | Ordnet den User einer Einrichtung zu |
| `request.current_facility` | Middleware | Wird pro Request gesetzt, steuert alle Queries |
| `FacilityScopedManager` | Model-Manager | Filtert Querysets automatisch nach Einrichtung |
| `AdminRequiredMixin` | View-Mixin | Erlaubt nur Administratoren |
| `LeadOrAdminRequiredMixin` | View-Mixin | Erlaubt Leitung und Administrator |
| `StaffRequiredMixin` | View-Mixin | Erlaubt Fachkraft, Leitung, Administrator |
| `AssistantOrAboveRequiredMixin` | View-Mixin | Erlaubt alle authentifizierten Rollen |
| `DocumentType.sensitivity` | Dokumentationstyp | Steuert Feld-Sichtbarkeit nach Rolle |
| `FieldTemplate.sensitivity` | Feldvorlage | Feld-Level Sichtbarkeits-Override (leer = erbt vom Dokumentationstyp) |
| `FieldTemplate.is_encrypted` | Feldvorlage | Steuert nur Verschlüsselung at rest, nicht Sichtbarkeit |
| `FacilitySettings.session_timeout_minutes` | Einrichtungs-Settings | Session-Dauer pro Einrichtung |

**Relevante Dateien:**
- [`src/core/models/user.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/user.py) — `Role`-Enum, Rollen-Properties (`is_admin`, `is_staff_or_above` …)
- [`src/core/views/mixins.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/mixins.py) — Zugriffs-Mixins für Views
- [`src/core/middleware/facility_scope.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/middleware/facility_scope.py) — Einrichtungs-Scoping per Middleware
- [`src/core/models/managers.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/managers.py) — `FacilityScopedManager` für automatische Query-Filterung
- [`src/core/middleware/password_change.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/middleware/password_change.py) — Passwort-Pflicht-Middleware
- [`src/templates/base.html`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/templates/base.html) — Navigations-Sichtbarkeit nach Rolle

---

### 5. Was bedeutet die Sensitivitätsstufe (niedrig/mittel/hoch)?

Die **Sensitivitätsstufe** steuert, welche Benutzerrolle welche Dokumentationseinträge und Felder sehen darf. Konfiguration pro `DocumentType`.

| Stufe | DB-Wert | Wer darf sehen |
|-------|---------|----------------|
| **Normal** | `normal` | Alle Rollen (Assistenz, Fachkraft, Leitung, Admin) |
| **Erhöht** | `elevated` | Fachkraft, Leitung, Admin |
| **Hoch** | `high` | Nur Leitung und Admin |

**Besonderheit:** Jedes Feld kann über `FieldTemplate.sensitivity` eine eigene Sichtbarkeitsstufe erhalten, die den DocumentType-Level nach oben überschreibt. Verschlüsselung (`is_encrypted`) ist davon unabhängig und steuert nur die Datenverschlüsselung at rest.

**UI-Verhalten:**
- **Detail-Ansicht:** Eingeschränkte Felder zeigen `[Eingeschränkt]` statt dem Wert
- **Bearbeiten-Formular:** Eingeschränkte Felder werden komplett ausgeblendet
- **POST-Schutz:** Beim Speichern werden eingeschränkte Feldwerte nicht überschrieben

**Relevante Dateien:**
- [`src/core/models/document_type.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/document_type.py) — `Sensitivity`-Choices
- [`src/core/services/sensitivity.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/sensitivity.py) — Zentrale Logik für Rollen-/Feldprüfung

### 6. Wie funktioniert das Löschsystem (4-Augen-Prinzip)?

Die Entscheidung ob direkt gelöscht oder ein Löschantrag erstellt wird, hängt von der **Kontaktstufe des Klienten** ab:

| Kontaktstufe | Löschung | Genehmigung nötig? |
|--------------|----------|---------------------|
| **Qualifiziert** | Löschantrag → Leitung/Admin muss genehmigen | Ja (4-Augen) |
| **Identifiziert** | Sofort Soft-Delete | Nein |
| **Anonym** (`is_anonymous`) | Sofort Soft-Delete | Nein |
| **Kein Client** | Sofort Soft-Delete | Nein |

**Ablauf bei qualifizierten Klienten:**
1. Staff kann nur **eigene** Events löschen, Lead/Admin kann **alle** löschen
2. System erstellt `DeletionRequest` mit Status `pending`
3. Lead/Admin reviewed — DB-Constraint `deletion_request_different_reviewer` erzwingt: **Reviewer ≠ Antragsteller**
4. Approve → Event wird soft-deleted + anonymisiert | Reject → Event bleibt

**Was passiert bei einer Löschung?**

| Was | Verhalten |
|-----|-----------|
| `event.is_deleted` | → `True` |
| `event.data_json` | → `{}` (personenbezogene Daten gelöscht) |
| Event-Record in DB | bleibt erhalten (Soft-Delete) |
| EventHistory | Neuer Eintrag mit `_redacted: True` |
| AuditLog | Neuer immutabler Eintrag |

**Relevante Dateien:**
- [`src/core/models/workitem.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/workitem.py) — `DeletionRequest`
- [`src/core/services/event.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/event.py) — `soft_delete_event`, `request_deletion`, `approve_deletion`, `reject_deletion`
- [`src/core/views/events.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/events.py) — `EventDeleteView`, `DeletionRequestReviewView`

### 7. Was hat KEINEN Löschmechanismus?

- **Clients** — kein manueller Löschantrag, aber automatische Anonymisierung durch `enforce_retention`
- **Cases / Episodes** — kein Löschmechanismus
- **User-Accounts** — kein Löschmechanismus
- **AuditLog-Einträge** — immutabel, bewusst nicht löschbar
- **EventHistory** — kaskadiert nur bei Hard-Delete

---

## D. Administration & Betrieb

### 8. Wie funktionieren Aufbewahrungsfristen?

Konfigurierbar pro Facility in **Administration → Einstellungen**:

| Kontaktstufe | Default | Setting |
|--------------|---------|---------|
| Anonym | 90 Tage | `retention_anonymous_days` |
| Identifiziert | 365 Tage | `retention_identified_days` |
| Qualifiziert | 3.650 Tage (10 Jahre) | `retention_qualified_days` |
| Activity-Logs | 365 Tage | `retention_activities_days` |

Zusätzlich: Per-DocumentType-Override via `DocumentType.retention_days`.

**Automatische Durchsetzung:** Das Management Command `enforce_retention` läuft täglich per Cron und löscht/anonymisiert abgelaufene Daten.

**Relevante Dateien:**
- [`src/core/models/settings.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/settings.py) — Retention-Felder
- [`src/core/management/commands/enforce_retention.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/management/commands/enforce_retention.py) — `--dry-run`, `--facility`

### 9. Welche automatisierten Scripts gibt es?

#### Cron-Schedule (Host-Level)

```
02:00 UTC  backup.sh                     ← Backup zuerst
03:00 UTC  enforce_retention             ← dann Retention
04:00 UTC  create_statistics_snapshots   ← dann Snapshots (monatlich am 1.)
*/5 min    Health Check (curl /health/)
```

**Reihenfolge kritisch:** Backup → Retention → Snapshots

#### Management Commands

| Command | Zweck | Ausführung |
|---------|-------|------------|
| [`enforce_retention`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/management/commands/enforce_retention.py) | Aufbewahrungsfristen durchsetzen | Cron täglich |
| [`create_statistics_snapshots`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/management/commands/create_statistics_snapshots.py) | Monatliche Statistik-Snapshots | Cron monatlich |
| [`setup_facility`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/management/commands/setup_facility.py) | Ersteinrichtung einer Einrichtung | Manuell |
| [`generate_dsgvo_package`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/management/commands/generate_dsgvo_package.py) | DSGVO-Dokumentation generieren | Manuell |
| [`reencrypt_fields`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/management/commands/reencrypt_fields.py) | Key-Rotation für verschlüsselte Felder | Manuell |
| [`seed`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/management/commands/seed.py) | Demo-/Testdaten erzeugen | Nur Dev |

**Dokumentation:** [`docs/ops-runbook.md`](https://github.com/tobiasnix/anlaufstelle/blob/main/docs/ops-runbook.md)

---

*Konsolidiert aus [#105](https://github.com/tobiasnix/anlaufstelle/issues/105), [#429](https://github.com/tobiasnix/anlaufstelle/issues/429), [#471](https://github.com/tobiasnix/anlaufstelle/issues/471), [#506](https://github.com/tobiasnix/anlaufstelle/issues/506). Alle Inhalte am 05.04.2026 gegen den Code verifiziert.*

