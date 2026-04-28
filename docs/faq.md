# FAQ — Zentrale Wissensdatenbank

Sammlung häufig gestellter Fragen zur Konfiguration, Datenschutz und Betrieb der Anlaufstelle.
Sortiert nach Onboarding-Reihenfolge: Erstkonfiguration → Tägliche Arbeit → Rollen & Datenschutz → Betrieb.

### Inhaltsverzeichnis

**A. Erste Schritte & Konfiguration**
1. [Wie stelle ich den Standard-Dokumentationstyp ein?](#1-wie-stelle-ich-den-standard-dokumentationstyp-ein)
2. [Wie richte ich 2FA ein?](#2-wie-richte-ich-2fa-ein)
3. [Authenticator-App zeigt „Code ungültig" — was tun?](#3-authenticator-app-zeigt-code-ungültig--was-tun)
4. [Ich habe mein Handy verloren — wie komme ich wieder rein?](#4-ich-habe-mein-handy-verloren--wie-komme-ich-wieder-rein)

**B. Tägliche Arbeit**
5. [Wie funktioniert der Zeitstrom?](#5-wie-funktioniert-der-zeitstrom)
6. [Wie funktioniert die Übergabe (Schichtübergabe)?](#6-wie-funktioniert-die-übergabe-schichtübergabe)
7. [Wie lade ich eine Datei an ein Ereignis an?](#7-wie-lade-ich-eine-datei-an-ein-ereignis-an)
8. [Kann ich offline arbeiten, wenn ich im Einsatz unterwegs bin?](#8-kann-ich-offline-arbeiten-wenn-ich-im-einsatz-unterwegs-bin)
9. [Die Suche findet meinen Klienten nicht, obwohl der Name fast passt.](#9-die-suche-findet-meinen-klienten-nicht-obwohl-der-name-fast-passt)
10. [Wie lege ich eine Schnell-Vorlage (Quick-Template) an?](#10-wie-lege-ich-eine-schnell-vorlage-quick-template-an)

**C. Rollen & Datenschutz**
11. [Wie funktionieren Zugriffsberechtigungen?](#11-wie-funktionieren-zugriffsberechtigungen)
12. [Was bedeutet die Sensitivitätsstufe?](#12-was-bedeutet-die-sensitivitätsstufe-niedrigmittelhoch)
13. [Wie funktioniert das Löschsystem (4-Augen-Prinzip)?](#13-wie-funktioniert-das-löschsystem-4-augen-prinzip)
14. [Was hat KEINEN Löschmechanismus?](#14-was-hat-keinen-löschmechanismus)

**D. Administration & Betrieb**
15. [Wie funktionieren Aufbewahrungsfristen?](#15-wie-funktionieren-aufbewahrungsfristen)
16. [Welche automatisierten Scripts gibt es?](#16-welche-automatisierten-scripts-gibt-es)

**E. Fehlermeldungen**
17. [Was bedeutet „Datensatz wurde zwischenzeitlich geändert"?](#17-was-bedeutet-datensatz-wurde-zwischenzeitlich-geändert)

---

## A. Erste Schritte & Konfiguration

### 1. Wie stelle ich den Standard-Dokumentationstyp ein?

**Administration → Einstellungen → Feld „Standard-Dokumentationstyp"** auswählen. Der gewählte Typ wird beim Öffnen von „Neuer Kontakt" automatisch vorausgewählt und die dynamischen Felder direkt geladen.

**Relevante Dateien:**
- [`src/core/models/settings.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/settings.py) — `default_document_type` ForeignKey
- [`src/core/views/events.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/events.py) — `EventCreateView.get()` liest den Default

---

### 2. Wie richte ich 2FA ein?

**Benutzermenü → Zwei-Faktor-Authentifizierung** (URL: `/mfa/settings/`) → **2FA einrichten**. Einen QR-Code mit einer Authenticator-App scannen (Google Authenticator, Microsoft Authenticator, Authy, FreeOTP+, 1Password, Bitwarden, Proton Pass) und den angezeigten 6-stelligen Code zur Bestätigung eintippen.

Ausführliche Anleitung inkl. manuelle Eingabe: [User-Guide § 1 — Zwei-Faktor-Authentifizierung](user-guide.md#zwei-faktor-authentifizierung-2fa). Admin-seitige Erzwingung: [Admin-Guide § 2.7](admin-guide.md#27-zwei-faktor-authentifizierung-2fa).

**Relevante Dateien:**
- [`src/core/views/mfa.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/mfa.py) — `MFASetupView`, `MFAVerifyView`, `MFASettingsView`

---

### 3. Authenticator-App zeigt „Code ungültig" — was tun?

TOTP-Codes sind 30 Sekunden gültig; auf beiden Seiten (Server und Telefon) muss die Systemzeit stimmen. Die häufigsten Ursachen:

| Ursache | Erkennung | Lösung |
|---------|-----------|--------|
| **Code bereits abgelaufen** | Code wechselt in der App, während Sie tippen | Den neuen Code eingeben |
| **Zeit-Drift auf dem Telefon** | App-Einstellungen → „Zeitkorrektur für Codes" / „Time sync" | In der App einmal Zeit neu synchronisieren |
| **Zeit-Drift auf dem Server** | Bestätigt durch Administrator | Administrator: NTP-Sync prüfen ([`docs/ops-runbook.md`](https://github.com/tobiasnix/anlaufstelle/blob/main/docs/ops-runbook.md)) |
| **Secret manuell falsch eingegeben** | QR-Code wurde nicht gescannt, sondern Zeichenkette getippt | **Base32** ohne Leerzeichen eingeben; in der App Typ „TOTP / zeitbasiert", 30 Sekunden, 6 Ziffern wählen. Am besten QR-Code erneut scannen. |
| **Alter Setup-Versuch** | Mehrere ungültige Versuche kurz nach Einrichtung | Einrichtung unter `/mfa/settings/` abbrechen, erneut starten — dabei den QR-Code **aus dem frischen Formular** scannen |

Wenn keine der Punkte hilft, wenden Sie sich an Ihren Administrator — fehlgeschlagene Versuche sind im `AuditLog` unter `MFA_FAILED` protokolliert.

---

### 4. Ich habe mein Handy verloren — wie komme ich wieder rein?

Aktuell gibt es **keinen Self-Service-Recovery-Pfad**. Bitten Sie einen Administrator, Ihr TOTP-Gerät zu löschen:

1. Admin-Bereich → **OTP → TOTP-Geräte**.
2. Gerät des betroffenen Users auswählen → löschen.
3. Beim nächsten Login werden Sie automatisch auf die Neu-Einrichtung (`/mfa/setup/`) geleitet, sofern 2FA für Ihr Konto/Ihre Einrichtung verpflichtend ist.

Einmalige Backup-Codes als Self-Service-Recovery sind geplant: [Issue #588](https://github.com/tobiasnix/anlaufstelle/issues/588).

**Relevante Dateien:**
- [`src/core/models/user.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/user.py) — `User.is_mfa_enforced`
- [`src/core/views/mfa.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/mfa.py) — `MFADisableView`, `MFASetupView`

---

## B. Tägliche Arbeit

### 5. Wie funktioniert der Zeitstrom?

Der Zeitstrom ist die **Startseite** der Anwendung (`/`). Er zeigt einen chronologischen Aktivitäts-Feed für den aktuellen Tag und ist die zentrale Tagesansicht (früher als separates Dashboard, Aktivitätslog und Timeline getrennt).

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

**Relevante Dateien:**
- [`src/core/views/zeitstrom.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/zeitstrom.py) — `ZeitstromView`, `ZeitstromFeedPartialView`
- [`src/core/services/feed.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/feed.py) — `build_feed_items()`, `enrich_events_with_preview()`
- [`src/core/services/handover.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/handover.py) — `build_handover_summary()`

---

### 6. Wie funktioniert die Übergabe (Schichtübergabe)?

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

### 7. Wie lade ich eine Datei an ein Ereignis an?

Beim Anlegen oder Bearbeiten eines Ereignisses gibt es ein **Datei-Upload-Feld**. Die Datei wird vor der Ablage automatisch per **ClamAV auf Viren geprüft** und anschließend **verschlüsselt gespeichert** (AES-GCM im Encrypted File Vault).

**Regeln auf einen Blick:**

| Aspekt | Wert |
|--------|------|
| Maximale Dateigröße | Standard 10 MiB pro Datei (Admin kann erhöhen) |
| Virenscan | ClamAV, automatisch vor der Speicherung |
| Verschlüsselung | AES-GCM, Schlüssel in der Einrichtungs-Konfiguration |
| Offline-Modus | Datei-Anhänge sind offline nicht möglich |

**Wenn der Upload abgelehnt wird:** Meldet das System die Datei als infiziert, ist sie mit hoher Wahrscheinlichkeit tatsächlich befallen — **nicht einfach erneut hochladen**. Informieren Sie Ihre IT/Administration und entfernen Sie die Datei aus Ihrem Arbeitsordner.

---

### 8. Kann ich offline arbeiten, wenn ich im Einsatz unterwegs bin?

Ja — seit v0.10 gibt es einen sicheren **Offline-Modus** für Streetwork und mobile Einsätze.

**Ablauf:**
1. **Vor dem Verlassen des Büros:** Klientel in den Offline-Cache laden (Schaltfläche in der Klientel-Liste).
2. **Unterwegs:** Ereignisse erfassen, Klienteldaten ansehen und bearbeiten — auch ohne Netz.
3. **Zurück im Büro:** Synchronisieren, damit lokale Änderungen in die Datenbank übernommen werden.

**Sicherheit:** Alle Offline-Daten liegen **verschlüsselt im Browser** (IndexedDB). Der Schlüssel wird aus Ihrem Passwort abgeleitet — der Browser allein kann die Daten nicht entschlüsseln.

**Wichtig — Daten können verloren gehen:** Bei **Logout, Passwort-Änderung oder Schließen des Tabs** werden alle Offline-Daten unlesbar. Daher gilt: **Erst synchronisieren, dann ausloggen!**

**Einschränkung:** Datei-Anhänge (siehe FAQ #7) sind offline nicht möglich — das ist eine bewusste Sicherheitsentscheidung.

---

### 9. Die Suche findet meinen Klienten nicht, obwohl der Name fast passt.

Die Suche ist **tippfehler-tolerant** (Fuzzy Search): „Muller" findet auch „Müller", „Tomas" auch „Thomas". Sie müssen den Namen nicht exakt kennen.

**Konfiguration:** Die Ähnlichkeits-Schwelle (Wertebereich 0.0–1.0, Default ca. 0.3) ist **pro Einrichtung einstellbar**:
- Niedrigere Werte → mehr Treffer, auch ungenauere
- Höhere Werte → nur sehr ähnliche Treffer

**Wenn ein erwarteter Treffer fehlt:** Sprechen Sie Ihre Administration an — sie kann den Schwellwert absenken.

---

### 10. Wie lege ich eine Schnell-Vorlage (Quick-Template) an?

Schnell-Vorlagen beschleunigen das Erfassen wiederkehrender Dokumentationen. Aktuell werden sie **ausschließlich durch Admins im Django-Admin-Bereich** gepflegt (siehe [Admin-Guide § 2.8](admin-guide.md#28-schnell-vorlagen-quick-templates)).

**Wenn Sie ein Muster entdecken, das Sie immer wieder tippen** (z.B. „Kurzkontakt am Tresen ohne Anliegen"), sprechen Sie Ihre Administration an — sie kann dafür eine Vorlage hinterlegen, die Ihnen im Erfassungsformular als Ein-Klick-Option zur Verfügung steht.

---

## C. Rollen & Datenschutz

### 11. Wie funktionieren Zugriffsberechtigungen?

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
| **Sensitivitätsstufe** | Zusätzlich zur Rolle steuern `DocumentType.sensitivity` und `FieldTemplate.sensitivity` den Feldzugriff. `FieldTemplate.sensitivity` überschreibt den DocumentType-Level nach oben (→ [FAQ #12](#12-was-bedeutet-die-sensitivitätsstufe-niedrigmittelhoch)) |
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

### 12. Was bedeutet die Sensitivitätsstufe (niedrig/mittel/hoch)?

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

### 13. Wie funktioniert das Löschsystem (4-Augen-Prinzip)?

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

### 14. Was hat KEINEN Löschmechanismus?

- **Clients** — kein manueller Löschantrag, aber automatische Anonymisierung durch `enforce_retention`
- **Cases / Episodes** — kein Löschmechanismus
- **User-Accounts** — kein Löschmechanismus
- **AuditLog-Einträge** — immutabel, bewusst nicht löschbar
- **EventHistory** — kaskadiert nur bei Hard-Delete

---

## D. Administration & Betrieb

### 15. Wie funktionieren Aufbewahrungsfristen?

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

### 16. Welche automatisierten Scripts gibt es?

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

## E. Fehlermeldungen

### 17. Was bedeutet „Datensatz wurde zwischenzeitlich geändert"?

Jemand anderes (oder Sie selbst in einem anderen Tab) hat denselben Datensatz parallel bearbeitet und bereits gespeichert. Um zu verhindern, dass Änderungen stillschweigend überschrieben werden (**Optimistic Locking**), blockt das System Ihren Speicherversuch.

**Lösung:**
1. Seite neu laden — damit sehen Sie die aktuellste Version.
2. Prüfen, welche Änderungen die andere Person eingetragen hat.
3. Ihre eigenen Änderungen erneut eintragen und speichern.

So gehen weder Ihre noch die parallelen Änderungen verloren.

---

*Konsolidiert aus [#105](https://github.com/tobiasnix/anlaufstelle/issues/105), [#429](https://github.com/tobiasnix/anlaufstelle/issues/429), [#471](https://github.com/tobiasnix/anlaufstelle/issues/471), [#506](https://github.com/tobiasnix/anlaufstelle/issues/506). Alle Inhalte am 05.04.2026 gegen den Code verifiziert. v0.10-Ergänzungen ([#589](https://github.com/tobiasnix/anlaufstelle/issues/589)): File Vault, Offline-Modus, Fuzzy Search, Quick-Templates, Optimistic Locking.*

