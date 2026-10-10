# FAQ — Zentrale Wissensdatenbank

Sammlung häufig gestellter Fragen zur Konfiguration, Datenschutz und Betrieb der Anlaufstelle.
Sortiert nach Onboarding-Reihenfolge: Erstkonfiguration → Tägliche Arbeit → Rollen & Datenschutz → Betrieb.

### Inhaltsverzeichnis

**A. Erste Schritte & Konfiguration**
1. [Wie stelle ich den Standard-Dokumentationstyp ein?](#1-wie-stelle-ich-den-standard-dokumentationstyp-ein)
1a. [Für welche Arbeitsfelder passt Anlaufstelle — und welche Pakete werden mitgeliefert?](#1a-für-welche-arbeitsfelder-passt-anlaufstelle--und-welche-pakete-werden-mitgeliefert)
2. [Wie richte ich 2FA ein?](#2-wie-richte-ich-2fa-ein)
3. [Authenticator-App zeigt „Code ungültig" — was tun?](#3-authenticator-app-zeigt-code-ungültig--was-tun)
4. [Ich habe mein Handy verloren — wie komme ich wieder rein?](#4-ich-habe-mein-handy-verloren--wie-komme-ich-wieder-rein)
4a. [Mein Konto ist nach mehreren Login-Fehlversuchen gesperrt — was tun?](#4a-mein-konto-ist-nach-mehreren-login-fehlversuchen-gesperrt--was-tun)

**B. Tägliche Arbeit**
5. [Wie funktioniert der Zeitstrom?](#5-wie-funktioniert-der-zeitstrom)
5a. [Was zeigt mir die Arbeitszentrale (Startseite `/start/`)?](#5a-was-zeigt-mir-die-arbeitszentrale-startseite-start)
6. [Wie funktioniert die Übergabe (Schichtübergabe)?](#6-wie-funktioniert-die-übergabe-schichtübergabe)
7. [Was ist der Unterschied zwischen „Hinweis" und „Aufgabe"?](#7-was-ist-der-unterschied-zwischen-hinweis-und-aufgabe)
8. [Was bedeutet die „Wiedervorlage" bei einer Aufgabe?](#8-was-bedeutet-die-wiedervorlage-bei-einer-aufgabe)
9. [Wie lade ich eine Datei an ein Ereignis an?](#9-wie-lade-ich-eine-datei-an-ein-ereignis-an)
10. [Kann ich offline arbeiten, wenn ich im Einsatz unterwegs bin?](#10-kann-ich-offline-arbeiten-wenn-ich-im-einsatz-unterwegs-bin)
11. [Die Suche findet die Person nicht, obwohl der Name fast passt.](#11-die-suche-findet-die-person-nicht-obwohl-der-name-fast-passt)
12. [Wie lege ich eine Schnell-Vorlage (Quick-Template) an?](#12-wie-lege-ich-eine-schnell-vorlage-quick-template-an)

**C. Rollen & Datenschutz**
13. [Wie funktionieren Zugriffsberechtigungen?](#13-wie-funktionieren-zugriffsberechtigungen)
13a. [Was ist Sudo-Mode (Re-Auth-Fenster)?](#13a-was-ist-sudo-mode-re-auth-fenster)
14. [Was bedeutet die Sensitivitätsstufe?](#14-was-bedeutet-die-sensitivitätsstufe-normalerhöhthoch)
15. [Wie funktioniert das Löschsystem (4-Augen-Prinzip)?](#15-wie-funktioniert-das-löschsystem-4-augen-prinzip)
16. [Was hat KEINEN Löschmechanismus?](#16-was-hat-keinen-löschmechanismus)

**D. Administration & Betrieb**
17. [Wie funktionieren Aufbewahrungsfristen?](#17-wie-funktionieren-aufbewahrungsfristen)
18. [Welche automatisierten Scripts gibt es?](#18-welche-automatisierten-scripts-gibt-es)
18a. [Wo finde ich eine Anleitung zu den Berichten (Reports)?](#18a-wo-finde-ich-eine-anleitung-zu-den-berichten-reports)
18b. [Wie importiere ich Personen oder Ereignisse aus einer CSV-Datei?](#18b-wie-importiere-ich-personen-oder-ereignisse-aus-einer-csv-datei)
18c. [Warum gibt es unter Statistik drei Reiter — und warum zeigen sie unterschiedliche Zahlen?](#18c-warum-gibt-es-unter-statistik-drei-reiter--und-warum-zeigen-sie-unterschiedliche-zahlen)

**E. Fehlermeldungen**
19. [Was bedeutet „Der Datensatz wurde zwischenzeitlich bearbeitet. Bitte laden Sie die Seite neu."?](#19-was-bedeutet-der-datensatz-wurde-zwischenzeitlich-bearbeitet-bitte-laden-sie-die-seite-neu)

---

## A. Erste Schritte & Konfiguration

### 1. Wie stelle ich den Standard-Dokumentationstyp ein?

**Verwaltung → Einstellungen → Allgemein und Komfort → Feld „Standard-Dokumentationstyp"** auswählen. Der gewählte Typ wird beim Öffnen von „Kontakt dokumentieren" automatisch vorausgewählt und die dynamischen Felder direkt geladen.

**Relevante Dateien:**
- [`src/core/models/settings.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py) — `default_document_type` ForeignKey
- [`src/core/views/events.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/events.py) — `EventCreateView.get()` liest den Default

---

### 1a. Für welche Arbeitsfelder passt Anlaufstelle — und welche Pakete werden mitgeliefert?

Anlaufstelle passt überall dort, wo Menschen **ohne Termin** und **ohne Klarnamen** kommen und die Mehrzahl der Kontakte anonym oder pseudonym bleibt. Der Arbeitsablauf ist *Kontakt → kurze Dokumentation → (optional) Fallarbeit → Statistik*; Kommunikation und Terminierung laufen außerhalb des Systems. Es passt schlecht, wenn Klarnamens-Akten, Behördenkorrespondenz oder Live-Kommunikationskanäle der Kern der Arbeit sind.

Neben dem Standardkatalog werden vier **Domänen-Pakete** mitgeliefert, die Sie bei der Einrichtung einer neuen Einrichtung über `setup_facility --package <name>` einspielen:

- `beratungsstelle` — offene Sprechstunden, anonyme Kurzberatung, Formularhilfe
- `awareness` — Awareness-Teams auf Festivals, in Klubs und bei Großveranstaltungen
- `frauenhaus` — Frauenhaus und Gewalt-Fachberatung (Vorbedingung: Quick-Exit)
- `jugendarbeit` — mobile und offene Jugendarbeit, Jugendzentren

Ein Domänen-Paket **ersetzt** den Standardkatalog, es wird nicht zusätzlich geladen — laden Sie es deshalb nur gegen eine frische Einrichtung.

**Relevante Dateien:**
- [`docs/fachkonzept-anlaufstelle.md`](https://github.com/anlaufstelle/app/blob/main/docs/fachkonzept-anlaufstelle.md) — § 3 „Zielgruppen" mit Fit-/Anti-Fit-Übersicht
- [`docs/packages.md`](https://github.com/anlaufstelle/app/blob/main/docs/packages.md) — Paket-Mechanik und mitgelieferter Katalog
- [`docs/admin-guide.md`](https://github.com/anlaufstelle/app/blob/main/docs/admin-guide.md) — Einrichtung einer neuen Einrichtung

---

### 2. Wie richte ich 2FA ein?

Kurz: **Name am Fuß der Sidebar (mobil: Mehr → Profil) → Mein Profil → 2FA verwalten** (`/mfa/settings/`) → **2FA einrichten**, QR-Code mit einer Authenticator-App scannen, 6-stelligen Code bestätigen.

Die **vollständige Schritt-für-Schritt-Anleitung** (getestete Apps, manuelle Secret-Eingabe, Login-Ablauf, Backup-Codes, Deaktivieren) steht kanonisch im **[User-Guide § 1 — Zwei-Faktor-Authentifizierung](user-guide.md#zwei-faktor-authentifizierung-2fa)**. Admin-seitige Erzwingung: [Admin-Guide § 2.7](admin-guide.md#27-zwei-faktor-authentifizierung-2fa). Diese FAQ deckt darüber hinaus nur die typischen Fehlerfälle ab (siehe nächste Fragen).

**Relevante Dateien:**
- [`src/core/views/mfa.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/mfa.py) — `MFASetupView`, `MFAVerifyView`, `MFASettingsView`

---

### 3. Authenticator-App zeigt „Code ungültig" — was tun?

TOTP-Codes sind 30 Sekunden gültig; auf beiden Seiten (Server und Telefon) muss die Systemzeit stimmen. Die häufigsten Ursachen:

| Ursache | Erkennung | Lösung |
|---------|-----------|--------|
| **Code bereits abgelaufen** | Code wechselt in der App, während Sie tippen | Den neuen Code eingeben |
| **Zeit-Drift auf dem Telefon** | App-Einstellungen → „Zeitkorrektur für Codes" / „Time sync" | In der App einmal Zeit neu synchronisieren |
| **Zeit-Drift auf dem Server** | Bestätigt durch Administrator | Administrator: NTP-Sync prüfen ([`docs/ops-runbook.md`](https://github.com/anlaufstelle/app/blob/main/docs/ops-runbook.md)) |
| **Secret manuell falsch eingegeben** | QR-Code wurde nicht gescannt, sondern Zeichenkette getippt | **Base32** ohne Leerzeichen eingeben; in der App Typ „TOTP / zeitbasiert", 30 Sekunden, 6 Ziffern wählen. Am besten QR-Code erneut scannen. |
| **Alter Setup-Versuch** | Mehrere ungültige Versuche kurz nach Einrichtung | Einrichtung unter `/mfa/settings/` abbrechen, erneut starten — dabei den QR-Code **aus dem frischen Formular** scannen |

Wenn keine der Punkte hilft, wenden Sie sich an Ihren Administrator — fehlgeschlagene Versuche sind im `AuditLog` unter `MFA_FAILED` protokolliert.

---

### 4. Ich habe mein Handy verloren — wie komme ich wieder rein?

**Erste Wahl: Backup-Code verwenden.** Einen gültigen, unbenutzten Backup-Code am 2FA-Login eingeben. Danach unter `/mfa/settings/` **Neues Gerät einrichten** wählen — auch bei verpflichtender 2FA. Sudo verlangt Passwort plus aktuellen TOTP- oder weiteren unbenutzten Backup-Code. Neuen QR-Code scannen, neuen TOTP-Code bestätigen und die sicher gespeicherten neuen Backup-Codes quittieren; erst diese Quittung ersetzt die bisherigen bestätigten TOTP-Geräte. Passkeys bleiben erhalten. Ein Backup-Code-Login allein wechselt kein Gerät. Vor der Härtung ausgestellte Codes und Codes vor einer `SECRET_KEY`-Rotation sind ungültig; neue Codes lassen sich mit der funktionierenden Authenticator-App erzeugen, sonst hilft der Admin-Reset.

**Wenn kein nutzbarer Faktor und keine gültigen Backup-Codes mehr vorhanden sind:** Bitten Sie die Anwendungsbetreuung Ihrer Einrichtung, Ihren zweiten Faktor zurückzusetzen (für Konten der Systemadministration: eine andere Systemadministration).

1. Die Anwendungsbetreuung öffnet **Verwaltung → Zugänge** → Ihren Zugang → unter „Sicherheitsaktionen" **Zweiten Faktor zurücksetzen** und bestätigt im Sudo-Modus. Die Systemadministration nutzt im Django-Admin unter **Anlaufstelle Core → Benutzer** die gleichnamige Aktion.
2. Entfernt werden alle Authenticator-Apps, Passkeys und Backup-Codes Ihres Zugangs; alle Ihre Sitzungen enden. Ihr Passwort bleibt gültig. Im AuditLog entsteht ein Eintrag `MFA_RESET_BY_ADMIN`.
3. Ist 2FA für Sie verpflichtend, werden Sie bei der nächsten Anmeldung auf die Neu-Einrichtung (`/mfa/setup/`) geleitet — bei der Rollenpflicht mit Übergangsfrist erst nach deren Ablauf. Sonst richten Sie 2FA unter `/mfa/settings/` freiwillig neu ein.

Eine Anwendungsbetreuung kann nur Zugänge der eigenen Einrichtung und kein Konto der Systemadministration zurücksetzen, das eigene Konto auf diesem Weg nicht. Einzelheiten: [Admin-Guide § 2.7](admin-guide.md#2fa-für-einen-benutzer-zurücksetzen).

**Relevante Dateien:**
- [`src/core/models/user.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/user.py) — `User.is_mfa_enforced`
- [`src/core/views/mfa.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/mfa.py) — `MFADisableView`, `MFASetupView`, Backup-Code-Verifikation
- [`src/core/services/user_access.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/user_access.py) — `reset_user_mfa` (Zurücksetzen durch die Administration)

---

### 4a. Mein Konto ist nach mehreren Login-Fehlversuchen gesperrt — was tun?

Nach **10 fehlgeschlagenen Anmeldungen innerhalb eines gleitenden 15-Minuten-Fensters** wird das Konto gesperrt — gezählt je Paar aus Konto und Quell-IP-Adresse (Schwelle und Fenster in [`src/core/services/security/login_lockout.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/login_lockout.py)). Die Systemadministration (`super_admin`) ist von dieser Kontosperre ausgenommen; Ratelimits und 2FA bleiben davon unberührt. Fehlversuche einer fremden IP-Adresse sperren Ihre Anmeldung von Ihrer eigenen Adresse also nicht. Auch das richtige Passwort wird in der Sperrphase abgewiesen: Die Login-Seite zeigt den Formularfehler „Ihr Konto ist nach mehreren fehlgeschlagenen Versuchen temporär gesperrt. …", protokolliert als `LOGIN_FAILED` mit `reason: "locked"`.

Die Sperre **endet von selbst**, sobald weniger als 10 gezählte Fehlversuche in den letzten 15 Minuten liegen — das kann früher sein als 15 Minuten nach dem letzten Fehlversuch; ohne neue gezählte Fehlversuche endet sie spätestens 15 Minuten nach dem letzten. Gezählt werden Versuche mit falschem Passwort und, während der Sperre, auch mit dem richtigen. Davon getrennt sind die Ratelimits der Anmeldung (5 Versuche pro Minute je IP-Adresse, 10 pro Stunde je Benutzername, feste Zeitfenster): Darüber antwortet die Anmeldung mit HTTP 429, bis das Zeitfenster abgelaufen ist — keine Entsperrung hebt das auf. Eine 429-Antwort zählt nicht als Fehlversuch und schreibt keinen Audit-Eintrag. Nach zehn Fehlversuchen im selben Stundenfenster erscheint deshalb meist die 429-Antwort (eine leere Seite); ob nach Ende des Stundenfensters noch der Formularfehler der Sperre kommt, hängt davon ab, ob dann noch zehn gezählte Fehlversuche in den letzten 15 Minuten liegen. Ratelimits und 2FA gelten auch für die Systemadministration (`super_admin`).

**Selbst entsperren (auf der Login-Seite):**

1. **Konto entsperren per E-Mail** — schickt einen 30 Minuten gültigen Entsperr-Link an die hinterlegte E-Mail-Adresse.
2. **Backup-Code einlösen** — bei eingerichteter 2FA mit Benutzername und einem Backup-Code.
3. **Passwort vergessen?** — ein über den Reset-Link neu gesetztes Passwort hebt die Sperre ebenfalls auf.

**Entsperren durch die Administration:**

1. Die Anwendungsbetreuung Ihrer Einrichtung öffnet **Verwaltung → Zugänge** → Ihren Zugang → unter „Sicherheitsaktionen" **Account-Sperre aufheben** (mit Sudo-Bestätigung) — das ist der Regelweg.
2. Die Systemadministration entsperrt einrichtungsübergreifend unter **Systembereich → Sperrkonten**; im Django-Admin (**Anlaufstelle Core → Benutzer**) gibt es dafür nur noch eine Listenaktion als Ausweichweg.
3. Ist niemand mit Zugang zur Verwaltung erreichbar, entsperrt der Betrieb am Server per `unlock <username>` ([Admin-Guide § 2.7](admin-guide.md#account-lockout)).

Jede Entsperrung wird im AuditLog als `LOGIN_UNLOCK` protokolliert.

**Hinweis für Admins:** Die `LOGIN_FAILED`-Einträge im AuditLog sind durch einen DB-Trigger (`auditlog_immutable`) unveränderbar — der Cleanup erfolgt deshalb über einen `LOGIN_UNLOCK`-Eintrag, nicht durch Löschen.

**Relevante Dateien:**
- [`src/core/services/security/login_lockout.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/login_lockout.py) — `is_locked()`, `unlock()`, Schwellenwert
- [`src/core/views/auth.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/auth.py) — Lockout-Check und Ratelimits beim Login (`CustomLoginView`)

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

Oberhalb des Feeds steht dauerhaft **Aktueller Dienst** mit Kontakten, neuen Aufgaben, neuen Personen und wichtigen Vorkommnissen der laufenden Schicht. Die Box folgt der aktuellen Uhrzeit, nicht der Filterauswahl des Feeds, und erscheint nur während einer eingerichteten laufenden Schicht. Die separate [Übergabe](#6-wie-funktioniert-die-übergabe-schichtübergabe) dient dem Schichtwechsel.

#### Was steht in der Sidebar?

In der rechten Seitenspalte steht die Team-Fokusbox **„Team-Aufgaben mit Handlungsbedarf“** mit bis zu **8 Einträgen**, unabhängig von der Zuweisung. Sie gruppiert nach **Überfällig**, **Heute fällig**, **Dringend · Wichtig** und **In Bearbeitung**; jede Aufgabe erscheint nur in der höchsten zutreffenden Druckstufe. Innerhalb der Gruppen wird nach Fälligkeit, Priorität und Erstellzeit sortiert. Offene normale Aufgaben ohne Frist bleiben über **Zur Aufgabenübersicht** erreichbar.

#### Wo finde ich die Kontakte-Liste, und was kann sie nicht?

**Kontakte** steht direkt unter **Zeitstrom** in der Seitenleiste, mobil unter **Mehr → Kontakte** (`/events/`). Der lokale Kontakte-Link im Zeitstrom übernimmt Tag, Dokumentationstyp und Schicht. Ab Assistenz können Sie über mehrere Tage filtern und auch Erfassungen anderer Mitarbeitender sehen; Sichtbarkeit und Sensibilitätsgrenze entsprechen dem Zeitstrom, Fallfilter und Fallanzeige beginnen erst ab Fachkraft.

Die Liste durchsucht nur **Pseudonym und Ereignis-Suchtext** ab zwei Zeichen, keine verschlüsselten oder wirksam erhöht/hoch eingestuften Feldwerte, Falltitel oder Dateiinhalte. Personen und Fälle werden ausgewählt, ohne Autocomplete-Suche oder Suchvorschläge. Feldwerte, Export und Massenaktionen fehlen in der Liste; Listen- und Filteraufrufe erzeugen keinen Audit-Eintrag. Filter bleiben in der Adresse, beim Seitenwechsel und über Zurück/Vorwärts erhalten. Anleitung: [User-Guide „Kontakte-Liste“](user-guide.md#kontakte-liste).

#### Wie funktioniert die HTMX-Interaktion?

Filter-Änderungen (Schicht, Typ, Dokumentationstyp) lösen einen HTMX-Request aus. Nur der Feed-Container wird ersetzt — kein Full-Page-Reload.

**Relevante Dateien:**
- [`src/core/views/zeitstrom.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/zeitstrom.py) — `ZeitstromView`, `ZeitstromFeedPartialView`
- [`src/core/services/events/feed.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/events/feed.py) — `build_feed_items()`, `enrich_events_with_preview()`
- [`src/core/services/case/handover.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/case/handover.py) — `build_handover_summary()`

---

### 5a. Was zeigt mir die Arbeitszentrale (Startseite `/start/`)?

Die **Arbeitszentrale** (`/start/`) ist die Landingpage der **Leitung** (Refs #920, Zuschnitt #1813) — nicht zu verwechseln mit dem **Zeitstrom** (`/`, der chronologische Tages-Feed, siehe [Frage 5](#5-wie-funktioniert-der-zeitstrom)). Sie trägt den Titel „Leitungs-Arbeitszentrale" und verdichtet die Aufsichts-Kennzahlen der Einrichtung zu Karten. Alle Daten sind auf die aktuelle Einrichtung (`current_facility`) gescoped.

**Nur die Leitung sieht diese Seite.** Alle anderen Rollen werden beim Aufruf von `/start/` auf ihren eigenen Einstieg umgeleitet — die früher dort gezeigten Karten leben auf der jeweiligen Zielseite:

| Rolle | Was `/start/` tut |
|-------|-------------------|
| **Leitung** (`lead`) | Zeigt die Arbeitszentrale: Die Karten **Löschanträge**, **Löschvorschläge** und **Statistik**; daneben die Statuszeile **Speicher** mit Belegung und Link zur Seite **Dateien** |
| **Fachkraft / Assistenz** (`staff`, `assistant`) | Weiterleitung auf den **Zeitstrom** (`/`) — heutige Kontakte, eigene offene Aufgaben und zuletzt bearbeitete Personen stehen dort als Cockpit-Block über dem Feed |
| **Anwendungsbetreuung** (`facility_admin`) | Weiterleitung auf die **Verwaltung** (`/verwaltung/`) — dort stehen u. a. User ohne MFA und die Settings-Warnungen |
| **Systemadministration** (`super_admin`) | Weiterleitung auf den **Systembereich** (`/system/`) mit der einrichtungsübergreifenden Sicht |

Entsprechend erscheint auch der Sidebar-Eintrag „Arbeitszentrale" ausschließlich bei der Leitung. Die Weiche erfolgt strikt über `user.role`; es gibt kein eigenes Rechte-Konzept außer dem Login- bzw. Rollen-Gate.

**Relevante Dateien:**
- [`src/core/views/dashboard.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/dashboard.py) — `RoleDashboardView` (Rollen-Weiche mit den drei Redirects)
- [`src/core/services/dashboard/main.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/dashboard/main.py) — `lead_dashboard_context()` (die übrigen Kontext-Funktionen versorgen Zeitstrom und Verwaltung)
- [`src/templates/core/dashboard/role_lead.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/core/dashboard/role_lead.html) — das einzige Arbeitszentrale-Template

---

### 6. Wie funktioniert die Übergabe (Schichtübergabe)?

Die **Übergabe** ist ein Dashboard für den Schichtwechsel — ersetzt das analoge A4-Übergabebuch.

**Schichten** (pro Einrichtung im Django-Admin unter **Anlaufstelle Core → Zeitfilter**, durch die Anwendungsbetreuung mit bestätigtem Sudo-Modus):

| Schicht | Zeitraum | Besonderheit |
|---------|----------|-------------|
| Frühdienst | 08:00–16:00 | — |
| Spätdienst | 16:00–22:00 | — |
| Nachtdienst | 22:00–08:00 | Mitternachts-Wraparound |

**Was zeigt die Seite?**

| Bereich | Inhalt | Zeitscope |
|---------|--------|-----------|
| **Statistik-Grid** | Kontakte, neue Aufgaben, neue Personen | Nur innerhalb der Schicht |
| **Wichtige Vorkommnisse** | Krisengespräche (gelb), Hausverbote (rot), dringende Aufgaben (blau) | Nur innerhalb der Schicht |
| **Übergabenotiz** | Notiz der letzten abgeschlossenen Schicht und Eingabe für die laufende Schicht; ohne Schichten gestern/heute | Eigene Schichtzeiträume |
| **Event-Aufschlüsselung** | Kontakte nach Dokumentationstyp mit farbigen Badges | Nur innerhalb der Schicht |

**Automatisches Verhalten:**
- Öffnet man die Übergabe **heute**, wird automatisch der **zuletzt abgeschlossene Dienst** mit seinem Schichtdatum vorausgewählt — auch nach Mitternacht und beim Wechsel vom Nachtdienst zum Frühdienst
- Datums-Navigation (vor/zurück) + Tab-Wechsel zwischen Schichten und „Ganzer Tag"
- Nachtschichten über Mitternacht werden korrekt via `covers_time()`-Logik behandelt
- Im Zeitstrom zeigt **Aktueller Dienst** dauerhaft die laufende Schicht; die Übergabe selbst enthält keine allgemeine Aufgabenliste. Dafür gibt es die Team-Fokusbox und die Aufgabenübersicht.
- Hinweise zu Bearbeitungsfrist, Verschlüsselung und Aufbewahrung der Notiz stehen im [Benutzerhandbuch](user-guide.md#übergabenotiz).

**Relevante Dateien:**
- [`src/core/views/zeitstrom.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/zeitstrom.py) — Zeitstrom-View mit Übergabe-Modus
- [`src/core/services/zeitstrom_filters.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/zeitstrom_filters.py) — Vorwahl von Dienst und Schichtdatum
- [`src/core/services/case/handover.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/case/handover.py) — `build_handover_summary()`
- [`src/core/models/time_filter.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/time_filter.py) — `TimeFilter` (Schichtdefinition)
- [`src/templates/core/handover/partials/_summary_full.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/core/handover/partials/_summary_full.html) — vollständige Übergabe-Zusammenfassung

---

### 7. Was ist der Unterschied zwischen „Hinweis" und „Aufgabe"?

**Inhaltlich:** Eine **Aufgabe** ist etwas, das aktiv erledigt werden soll (mit Status, Priorität, Frist). Ein **Hinweis** ist eher informativ — eine Notiz für das Team, ohne klare „muss-ich-tun"-Verpflichtung.

**Technisch:** Beide sind derselbe Datentyp (`WorkItem`) und unterscheiden sich nur durch das Feld `item_type`. Es gibt **keinen funktionalen Unterschied** in Status, Wiederholung, Berechtigungen oder Benachrichtigungen — die Trennung ist rein eine Anzeige- und Filter-Hilfe für Nutzer:innen.

**Wo zeigt sich der Unterschied?**

| Stelle | Verhalten |
|--------|-----------|
| **Inbox-Filter** | Dropdown „Typ" filtert auf Hinweis oder Aufgabe |
| **Listen / Detail** | Anzeige-Badge mit dem gewählten Typ |
| **Default beim Anlegen** | „Aufgabe" |

**Relevante Dateien:**
- [`src/core/models/workitem.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/workitem.py) — `ItemType.HINT` / `ItemType.TASK`
- [`src/core/views/workitems.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/workitems.py) — Filter in `WorkItemInboxView`

---

### 8. Was bedeutet die „Wiedervorlage" bei einer Aufgabe?

Die **Wiedervorlage** (Feld `remind_at`) ist ein **optionales Frühwarn-Datum**, getrennt von der Fälligkeit (`due_date`). Sinngemäß: *„Vor dem Fälligkeitstermin (oder spätestens an ihm) soll die Aufgabe wieder ins Blickfeld rücken."*

**Verhalten:**

| Aspekt | Wert |
|--------|------|
| **Pflichtfeld?** | Nein — optional, Default leer |
| **Verhältnis zu `due_date`** | Wiedervorlage muss **≤ Fälligkeit** sein (gleicher Tag erlaubt) |
| **Sichtbarkeit** | Bis zum Wiedervorlage-Datum unauffällig in der Inbox; **am Vortag** gelbes Badge „Morgen fällig", **am Tag selbst und danach bis zum Abschluss** orange Badge „Heute fällig" |
| **Erledigte Items** | Kein Badge mehr |
| **Wiederholung** | Bei automatisch erzeugten Folgeaufgaben bleibt der Offset zwischen `remind_at` und `due_date` erhalten |

**Wichtig:** „Aufpoppen" heißt **ausschließlich UI-Badge** in der Inbox/Liste, sobald die Seite aufgerufen wird — es gibt **keine E-Mail, keine Push-Nachricht, keinen Hintergrund-Job**.

**Beispiel:** Eine Aufgabe ist fällig am 30.04., Wiedervorlage am 28.04. Bis einschließlich 26.04. ist sie unauffällig in der Inbox. Am 27.04. erscheint das gelbe „Morgen fällig“-Badge; ab 28.04. bleibt das orange „Heute fällig“-Badge bis zum Abschluss sichtbar.

**Relevante Dateien:**
- [`src/core/models/workitem.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/workitem.py) — Feld `remind_at`
- [`src/core/utils/dates.py`](https://github.com/anlaufstelle/app/blob/main/src/core/utils/dates.py) — `describe_remind_at()` (Badge-Logik)
- [`src/core/forms/workitems.py`](https://github.com/anlaufstelle/app/blob/main/src/core/forms/workitems.py) — Validierung `remind_at ≤ due_date`
- [`src/core/services/case/workitems.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/case/workitems.py) — Offset-Erhalt bei Wiederholung

---

### 9. Wie lade ich eine Datei an ein Ereignis an?

Beim Anlegen oder Bearbeiten eines Ereignisses gibt es ein **Datei-Upload-Feld**. Die Datei wird vor der Ablage automatisch per **ClamAV auf Viren geprüft** und anschließend **verschlüsselt gespeichert** (Fernet/AES-128 im Encrypted File Vault).

**Regeln auf einen Blick:**

| Aspekt | Wert |
|--------|------|
| Maximale Dateigröße | Standard 10 MiB pro Datei; Anwendungsbetreuung kann bis zur Betreibergrenze `FILE_VAULT_MAX_UPLOAD_BYTES` erhöhen (Vorgabe: 50 MiB); das strengere Limit gilt |
| Virenscan | ClamAV, automatisch vor der Speicherung |
| Verschlüsselung | Fernet/AES-128; installationsweiter Schlüsselring `ENCRYPTION_KEYS` (alternativ `ENCRYPTION_KEY`), gemeinsam für alle Einrichtungen; keine getrennten Einrichtungsschlüssel |
| Offline-Modus | Datei-Anhänge sind offline nicht möglich |

**Wenn der Upload abgelehnt wird:** Meldet das System die Datei als infiziert, ist sie mit hoher Wahrscheinlichkeit tatsächlich befallen — **nicht einfach erneut hochladen**. Informieren Sie Ihre IT/Administration und entfernen Sie die Datei aus Ihrem Arbeitsordner.

---

### 10. Kann ich offline arbeiten, wenn ich im Einsatz unterwegs bin?

Ja — es gibt einen sicheren **Offline-Modus** für Streetwork und mobile Einsätze.

**Ablauf:**
1. **Vor dem Verlassen des Büros:** Personen in den Offline-Cache laden (Schaltfläche in der Personen-Liste).
2. **Unterwegs:** Ereignisse erfassen, Personendaten ansehen und bearbeiten — auch ohne Netz.
3. **Zurück im Büro:** Synchronisieren, damit lokale Änderungen in die Datenbank übernommen werden.

**Sicherheit:** Alle Offline-Daten liegen **verschlüsselt im Browser** (IndexedDB). Der Schlüssel wird aus Ihrem Passwort abgeleitet und als nicht exportierbarer Schlüssel im Browser gespeichert. Innerhalb der Sitzungsdauer öffnet die App die Daten auch offline ohne erneute Anmeldung — wer in dieser Zeit Ihr entsperrtes Gerät benutzt, kann sie lesen. Datensatz-Kennungen und technische Zeitstempel, etwa der letzte Abgleich, liegen unverschlüsselt daneben.

**Wichtig — Daten können verloren gehen:** Bei **Logout** und **Passwort-Änderung** löscht die App Schlüssel und Offline-Daten, auch noch nicht übertragene Einträge. Nach **längerer Inaktivität als der Sitzungsdauer** entfernt sie den Schlüssel bei der nächsten Prüfung (beim Öffnen, bei der Tab-Rückkehr oder regelmäßig im offenen Tab). Liegt noch nicht übertragene Arbeit vor, bleibt der verschlüsselte Bestand erhalten und wird nach erneuter Anmeldung mit demselben Passwort wieder lesbar; sonst wird auch der Offline-Bestand gelöscht. Das bloße **Schließen des Tabs** oder ein Neustart löscht dagegen nichts — der Schlüssel wird beim erneuten Öffnen innerhalb der Session-Lebenszeit wiederhergestellt (Streetwork-Design, #1065/#1324). Daher gilt: **Erst synchronisieren, dann ausloggen!**

**Erfassung überall offline (#1499):** Sie können offline auch **ohne vorher geöffnetes Dossier** erfassen — über das **„+"-Menü** („Kontakt dokumentieren"/„Aufgabe erstellen") öffnet sich an der gewohnten Adresse (`/events/new/` / `/workitems/new/`) eine echte Erfassungsmaske. **Kontakte ohne Personenbezug** (anonyme Kontakte) und **Standalone-Aufgaben** lassen sich damit jetzt **auch offline neu beginnen** (Auswahl „— ohne Person —"); erfordert der Dokumentationstyp eine Kontaktstufe, ordnen Sie eine „mitgenommene" Person zu. Eine Sackgasse ohne geöffnetes Dossier gibt es nicht (#1483/#1485).

**Kann ich offline auch meine Aufgaben und den Zeitstrom sehen? (#1499):** Ja. Neben der Personenliste (`/clients/`) zeigen auch die **Aufgabenliste** (`/workitems/`) und der **Zeitstrom** als Startseite (`/`) offline denselben lokalen Ausschnitt wie online — zusammengestellt aus Ihren offline mitgenommenen Personen, dazu **anonyme bzw. personlose Einträge** (Standalone-Aufgaben, Kontakte ohne Personenbezug), klar markiert. Der Zeitstrom ist bewusst als **„lokale Chronik"** gekennzeichnet: Er zeigt nur die offline verfügbaren Vorgänge, nicht die vollständige Historie der Einrichtung. Die Seite **`/offline/`** ist damit **kein Einstiegspunkt mehr**, sondern nur noch ein technischer Fallback („Keine lokale Ansicht verfügbar") — er erscheint nur, wenn für die aufgerufene Adresse keine lokale Ansicht vorliegt.

**Einschränkungen:** Datei-Anhänge (siehe FAQ #9) sind offline nicht möglich — das ist eine bewusste Sicherheitsentscheidung. Die Aufgaben-Erfassung ist Fachkräften vorbehalten (Assistenz sieht offline statt der Aufgaben-Maske einen Hinweis). Ist für die Offline-Erfassung noch nichts vorbereitet (Einrichtung nie online geöffnet, leerer Cache, App noch nicht aktualisiert), zeigt die Maske einen kurzen „einmal online öffnen"-Hinweis.

---

### 11. Die Suche findet die Person nicht, obwohl der Name fast passt.

Die Suche ist **tippfehler-tolerant** (Fuzzy Search): „Muller" findet auch „Müller", „Tomas" auch „Thomas". Sie müssen den Namen nicht exakt kennen.

**Konfiguration:** Die Ähnlichkeits-Schwelle (Wertebereich 0.0–1.0, Default ca. 0.3) ist **pro Einrichtung einstellbar**:
- Niedrigere Werte → mehr Treffer, auch ungenauere
- Höhere Werte → nur sehr ähnliche Treffer

**Wenn ein erwarteter Treffer fehlt:** Sprechen Sie Ihre Administration an — sie kann den Schwellwert absenken.

---

### 12. Wie lege ich eine Schnell-Vorlage (Quick-Template) an?

Schnell-Vorlagen beschleunigen das Erfassen wiederkehrender Dokumentationen. Aktuell werden sie **ausschließlich durch Admins im Django-Admin-Bereich** gepflegt (siehe [Admin-Guide § 2.8](admin-guide.md#28-schnell-vorlagen-quick-templates)).

**Wenn Sie ein Muster entdecken, das Sie immer wieder tippen** (z.B. „Kurzkontakt am Tresen ohne Anliegen"), sprechen Sie Ihre Administration an — sie kann dafür eine Vorlage hinterlegen, die Ihnen im Erfassungsformular als Ein-Klick-Option zur Verfügung steht.

---

## C. Rollen & Datenschutz

### 13. Wie funktionieren Zugriffsberechtigungen?

Zugriffsberechtigungen werden über drei Ebenen gesteuert: **Rolle**, **Einrichtung** und **Sensitivitätsstufe**. Zusammen bestimmen sie, welcher User welche Daten sehen und welche Aktionen ausführen darf.

#### Die fünf Rollen

> **Zielgruppe dieses Abschnitts:** die **technische** Sicht — DB-Werte, `User`-Properties und das RLS-/Facility-Scoping hinter dem Rollenmodell. Die fachliche Begründung steht im [Fachkonzept (Role — Die Rolle)](fachkonzept-anlaufstelle.md#role--die-rolle), die praxisnahe „Was darf wer?"-Sicht im [User-Guide § 9](user-guide.md#9-rollen-und-berechtigungen), das Anlegen/Verwalten im [Admin-Guide § 2.4](admin-guide.md#rollenbeschreibung).

Jeder User hat genau eine Rolle. Vier Rollen sind strikt facility-gebunden und bilden eine aufsteigende Hierarchie — höhere Rollen umfassen alle Rechte der niedrigeren. Die fünfte Rolle (`super_admin`) wirkt facility-übergreifend und ist außerhalb der Hierarchie:

| Rolle | DB-Wert | Anzeigename | Beschreibung |
|-------|---------|-------------|-------------|
| **Assistenz** | `assistant` | Assistenz | Grundzugriff: Kontakte anlegen, Zeitstrom und Personen einsehen |
| **Fachkraft** | `staff` | Fachkraft | Vollzugriff auf Fallarbeit: Personen, Fälle, Episoden, Ziele anlegen und bearbeiten |
| **Leitung** | `lead` | Leitung | Zusätzlich: Fälle schließen, Statistiken, Löschanträge genehmigen, Datenexport |
| **Anwendungsbetreuung** | `facility_admin` | Anwendungsbetreuung | Vollzugriff einer Einrichtung: Audit-Log (Facility), DSGVO-Paket, Benutzer- und Typ-Konfiguration. Strikt facility-gebunden. |
| **Systemadministration** | `super_admin` | Systemadministration | Facility-übergreifend. `/system/`-Bereich, Bootstrap-Tools, Pre-Auth-AuditLogs. Wird über `manage.py create_super_admin` angelegt — nicht über das normale UI. |

Die Rolle wird direkt auf dem User-Modell gespeichert (`User.role`). Die View-Schicht prüft Rollen über Properties wie `user.is_staff_or_above`, `user.is_facility_admin` oder `user.is_super_admin`.

#### Einrichtungs-Scoping (Datenisolation)

Jeder User mit Rolle `assistant` / `staff` / `lead` / `facility_admin` gehört zu genau einer **Einrichtung** (Facility). Datenisolation ist automatisch:

- Die Middleware `FacilityScopeMiddleware` setzt pro Request `request.current_facility`
- Alle Datenmodelle (Personen, Kontakte, Fälle, Arbeitsaufträge, Audit-Logs …) verwenden den `FacilityScopedManager`
- Queries werden über `.for_facility(facility)` gefiltert — ein User sieht **ausschließlich Daten seiner eigenen Einrichtung**
- Es gibt keine einrichtungsübergreifende Sicht, auch nicht für die Anwendungsbetreuung (`facility_admin`)

**Ausnahme `super_admin`:** Die Rolle Systemadministration arbeitet bewusst facility-übergreifend. Sie sieht Daten ausschließlich über den separaten `/system/`-Bereich (nicht über das normale Fach-UI). Technisch gibt es zwei Schichten:

1. **Anwendungsschicht:** Die `FacilityScopeMiddleware` setzt für `super_admin` keine implizite Facility — der User wählt im `/system/`-Bereich explizit aus.
2. **Datenbankschicht:** Eine zusätzliche Postgres-Session-Variable `app.is_super_admin='true'` wird als OR-Branch in jede RLS-Policy eingewoben (Migration 0085). Das Always-Reset-Pattern in der `FacilityScopeMiddleware` setzt diese Variable für jeden Request neu (oder löscht sie), damit kein Pool-Connection-Leak entsteht. Details: [ADR-005 Update 2026-05-10](adr/005-facility-scoping-and-rls.md), [ADR-018](adr/018-rollenmodell-superadmin.md).

**DSGVO-Rechenschaftspflicht:** Jeder Aufruf einer `/system/`-View schreibt einen `SYSTEM_VIEW`-AuditLog-Eintrag. Das System-UI zeigt einen permanenten Banner: „Sie greifen auf Daten facility-übergreifend zu — dieser Zugriff wird im Audit-Log protokolliert."

#### Zugriffstabelle: Wer darf was?

| Bereich | Aktion | Mindestrolle |
|---------|--------|--------------|
| Zeitstrom, Übergabe | Anzeigen | Assistenz |
| Kontakte-Liste | Anzeigen, Filtern, auch fremde Erfassungen; Sensibilität wie Zeitstrom | Assistenz |
| Kontakte-Liste | Fallfilter und Fallanzeige | Fachkraft |
| Suche | Volltextsuche | Assistenz |
| Personen | Anzeigen, Suchen | Assistenz |
| Personen | Anlegen, Bearbeiten | Fachkraft |
| Personen | Datenexport (JSON/PDF) | Leitung |
| Kontakte (Events) | Anlegen, Anzeigen | Assistenz |
| Kontakte | Bearbeiten | Assistenz (eigene) / Fachkraft (alle) |
| Kontakte | Löschen | Fachkraft (eigene) / Leitung (alle) |
| Löschanträge | Anzeigen | Leitung/Anwendungsbetreuung, Personen mit Löschbestätigung; eigene offene Anträge auch für Antragstellende |
| Löschanträge | Genehmigen/Ablehnen | Gesondertes Recht „Löschbestätigung“, eigene Anträge ausgeschlossen |
| Fälle, Episoden, Ziele | Anzeigen, Anlegen, Bearbeiten | Fachkraft |
| Fälle | Schließen, Wiederöffnen | Leitung |
| Arbeitsaufträge | Anzeigen | Assistenz |
| Arbeitsaufträge | Anlegen, Bearbeiten | Fachkraft |
| Arbeitsaufträge | Status ändern | Ersteller, Zugewiesener oder Leitung/Anwendungsbetreuung; nicht zugewiesene Teamaufgaben: alle ab Assistenz |
| Statistiken & Exports | Anzeigen, CSV/PDF/Beispiel-Sachbericht-Export | Leitung |
| Audit-Log (eigene Einrichtung, `/audit/`) | Anzeigen | Anwendungsbetreuung (`facility_admin`) |
| DSGVO-Paket | Generieren, Herunterladen | Anwendungsbetreuung (`facility_admin`) |
| Administration (`/admin-mgmt/`) | Zugang | Anwendungsbetreuung (`facility_admin`) |
| Systembereich (`/system/`) | Anzeigen, Bootstrap, Pre-Auth-AuditLogs | Systemadministration (`super_admin`) |

#### Sichtbarkeit in der Oberfläche

Navigation und Aktions-Buttons passen sich automatisch der Rolle an:

- **Sidebar/Navigation** (Ziel-IA, #1813):
  - „Arbeitszentrale" ausschließlich für die Leitung (siehe [Frage 5a](#5a-was-zeigt-mir-die-arbeitszentrale-startseite-start))
  - „Fälle" erscheint erst ab Fachkraft
  - „Statistik" ab Leitung
  - Bereich „Datenschutz" (`/datenschutz/`) erscheint, sobald mindestens einer seiner Reiter sichtbar ist — „Löschanträge" für Leitung/Anwendungsbetreuung, Personen mit Löschbestätigung und Antragstellende mit eigenen offenen Anträgen, „Audit-Log" und „DSGVO-Paket" für die Anwendungsbetreuung
  - Bereich „Verwaltung" (`/verwaltung/`) für die Anwendungsbetreuung
  - „Systembereich" (`/system/`) und „Administration ↗" (Django-Admin) ausschließlich für die Systemadministration — die Anwendungsbetreuung erreicht den Django-Admin über eine eigens markierte Zeile auf der Verwaltungs-Übersicht
- **Erstellen-Menü:** „Person anlegen", „Aufgabe erstellen" und „Fall anlegen" erst ab Fachkraft sichtbar
- **Detail-Seiten:** „Bearbeiten"- und „Löschen"-Buttons je nach Rolle und Eigentümerschaft ein-/ausgeblendet
- **Banner im `/system/`-Bereich:** Permanenter Hinweis auf facility-übergreifenden Zugriff und Audit-Protokollierung (`SYSTEM_VIEW`)

#### Feingranulare Sonderregeln

Neben der Grundrolle gibt es situationsabhängige Berechtigungen:

| Regel | Beschreibung |
|-------|-------------|
| **Eigentümer-Berechtigung** | Assistenz darf eigene Kontakte bearbeiten, auch wenn „Bearbeiten" sonst ab Fachkraft gilt |
| **Zuweisungs-Berechtigung** | Arbeitsauftrag-Status kann vom Ersteller, Zugewiesenen oder der Leitung/Anwendungsbetreuung geändert werden; bei nicht zugewiesenen Teamaufgaben von allen ab Assistenz |
| **Sensitivitätsstufe** | Zusätzlich zur Rolle steuern `DocumentType.sensitivity` und `FieldTemplate.sensitivity` den Feldzugriff. `FieldTemplate.sensitivity` überschreibt den DocumentType-Level nach oben (→ [FAQ #14](#14-was-bedeutet-die-sensitivitätsstufe-normalerhöhthoch)) |
| **Passwort-Pflicht** | Bei gesetztem `must_change_password`-Flag wird der User vor jeder Aktion zum Passwortwechsel gezwungen |
| **Session-Timeout** | Konfigurierbar pro Einrichtung (Standard: 30 Min.), nach Ablauf **ohne Aktivität** automatischer Logout |
| **Maximale Sitzungsdauer** | Zweite, aktivitätsunabhängige Grenze pro Einrichtung (Standard: 12 Std. ab Anmeldung); danach ist eine neue Anmeldung nötig, auch bei durchgehender Nutzung |
| **Account-Sperre (Lockout)** | 10 fehlgeschlagene Logins innerhalb von 15 Minuten von derselben IP-Adresse sperren den Account; die Sperre endet von selbst, sobald weniger als 10 davon in den letzten 15 Minuten liegen (ohne neue Fehlversuche spätestens 15 Minuten nach dem letzten). `super_admin` ist von dieser Kontosperre ausgenommen (Ratelimits und 2FA bleiben unberührt). Entsperren: Selbsthilfe auf der Login-Seite (E-Mail-Link, Backup-Code, Passwort-Reset), `facility_admin` unter Verwaltung → Zugänge, `super_admin` unter Systembereich → Sperrkonten, als letzter Weg `manage.py unlock <username>` (CLI) — siehe [FAQ #4a](#4a-mein-konto-ist-nach-mehreren-login-fehlversuchen-gesperrt--was-tun) |
| **Zurücksetzen des zweiten Faktors** | `facility_admin` für Zugänge der eigenen Einrichtung (ohne `super_admin`-Konten), `super_admin` für alle Konten; nie das eigene Konto, immer mit Sudo, protokolliert als `MFA_RESET_BY_ADMIN` — siehe [FAQ #4](#4-ich-habe-mein-handy-verloren--wie-komme-ich-wieder-rein) |

#### Technische Steuerungsparameter

| Parameter | Wo | Wirkung |
|-----------|----|--------|
| `User.role` | User-Modell | Bestimmt die Grundrolle (eine von 5: `super_admin`, `facility_admin`, `lead`, `staff`, `assistant`) |
| `User.facility` | User-Modell (FK) | Ordnet den User einer Einrichtung zu (NULL nur bei `super_admin`) |
| `User.is_super_admin` | Property | Gibt `True` zurück, wenn `role == super_admin` |
| `User.is_facility_admin` | Property | Gibt `True` zurück, wenn `role == facility_admin` |
| `request.current_facility` | Middleware | Wird pro Request gesetzt, steuert alle Queries |
| `FacilityScopedManager` | Model-Manager | Filtert Querysets automatisch nach Einrichtung |
| `SuperAdminRequiredMixin` | View-Mixin | Erlaubt nur Systemadministration (`/system/`-Bereich) |
| `FacilityAdminRequiredMixin` | View-Mixin | Erlaubt Anwendungsbetreuung der eigenen Einrichtung |
| `LeadOrAdminRequiredMixin` | View-Mixin | Erlaubt Leitung und Anwendungsbetreuung |
| `StaffRequiredMixin` | View-Mixin | Erlaubt Fachkraft, Leitung, Anwendungsbetreuung |
| `AssistantOrAboveRequiredMixin` | View-Mixin | Erlaubt alle authentifizierten facility-gebundenen Rollen |
| `app.is_super_admin` (Postgres-Session-Var) | Datenbank | OR-Branch in allen RLS-Policies — `super_admin` darf facility-übergreifend lesen (Migration 0085) |
| `DocumentType.sensitivity` | Dokumentationstyp | Steuert Feld-Sichtbarkeit nach Rolle |
| `FieldTemplate.sensitivity` | Feldvorlage | Feld-Level Sichtbarkeits-Override (leer = erbt vom Dokumentationstyp) |
| `FieldTemplate.is_encrypted` | Feldvorlage | Steuert nur Verschlüsselung at rest, nicht Sichtbarkeit |
| `FacilitySettings.session_timeout_minutes` | Einrichtungs-Settings | Inaktivitäts-Fenster pro Einrichtung (Default 30 Min) |
| `FacilitySettings.session_absolute_hours` | Einrichtungs-Settings | Absolute Obergrenze einer Sitzung ab Login, unabhängig von Aktivität (Default 12 h) |
| `AuditLog.action == SYSTEM_VIEW` | AuditLog | Protokolliert jeden Aufruf einer `/system/`-View (Rechenschaftspflicht) |

**Relevante Dateien:**
- [`src/core/models/user.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/user.py) — `Role`-Enum mit 5 Werten, Properties (`is_super_admin`, `is_facility_admin`, `is_staff_or_above` …)
- [`src/core/views/mixins.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/mixins.py) — Zugriffs-Mixins inkl. `SuperAdminRequiredMixin` und `FacilityAdminRequiredMixin`
- [`src/core/views/system/`](https://github.com/anlaufstelle/app/tree/main/src/core/views/system) — `/system/`-Bereich für `super_admin`
- [`src/core/middleware/facility_scope.py`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/facility_scope.py) — Einrichtungs-Scoping + Always-Reset für `app.is_super_admin`
- [`src/core/models/managers.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/managers.py) — `FacilityScopedManager` für automatische Query-Filterung
- [`src/core/management/commands/create_super_admin.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/create_super_admin.py) — Bootstrap-Befehl
- [`src/core/management/commands/unlock.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/unlock.py) — CLI-Recovery für gesperrte Accounts
- [`src/core/middleware/password_change.py`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/password_change.py) — Passwort-Pflicht-Middleware
- [`src/templates/base.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/base.html) — Navigations-Sichtbarkeit nach Rolle

---

### 13a. Was ist Sudo-Mode (Re-Auth-Fenster)?

**Sudo-Mode** ist ein zeitlich begrenztes Bestätigungsfenster für geschützte Aktionen. Sie geben Ihr **aktuelles Passwort erneut** ein; bei bestätigtem TOTP-Gerät zusätzlich einen aktuellen TOTP- oder unbenutzten Backup-Code. Nach erfolgreicher Bestätigung gilt das Fenster in dieser Sitzung für standardmäßig **15 Minuten** (`SUDO_MODE_TTL_SECONDS`).

#### Was wird geschützt?

| Bereich | Geschützte Aktionen |
|---------|---------------------|
| **Personen-Datenexport** | JSON- und PDF-Auskunft (Art. 15/20 DSGVO); [Umfang: Admin-Handbuch § 7.6](admin-guide.md#umfang-der-auskunft-art-15) |
| **Einstellungen** | Öffnen und Speichern der Einrichtungs-Einstellungen |
| **Zugänge** | Anlegen, Bearbeiten, Setup-Link versenden, Sperre aufheben und zweiten Faktor zurücksetzen |
| **Eigene zweite Faktoren** | 2FA deaktivieren, Passkey löschen; TOTP-Einrichtung und Passkey-Registrierung, wenn bereits ein bestätigter Faktor besteht |
| **Django-Admin** (`/admin-mgmt/`) | Zugang zu den Verwaltungsseiten |
| **Systembereich** | Wartungsmodus, Konto-Sperre aufheben, Audit-Log exportieren |

Das **DSGVO-Dokumentationspaket** (Übersicht und Download öffentlicher Vorlagen) verlangt kein Sudo-Fenster. Auch normale Fallarbeit wie Kontakte anlegen, Aufgaben bearbeiten oder die Personen-Detailansicht öffnen verlangt keines. Rollen- und Datenzugriffsrechte gelten weiterhin; Sudo vergibt keine zusätzlichen Rechte.

#### Wozu — was ist das Bedrohungsmodell?

Sudo erschwert den Missbrauch einer entwendeten oder unbeaufsichtigten Sitzung bei den **oben genannten Aktionen**. Die Sitzung allein reicht dort außerhalb eines gültigen Sudo-Fensters nicht aus: Zusätzlich werden das aktuelle Passwort und bei bestätigtem TOTP-Gerät ein zweiter Faktor geprüft. Während eines bereits bestätigten Fensters kann die Sitzung diese Schranke passieren.

Sudo ergänzt die normale Sitzungsanmeldung und die jeweiligen Rollenprüfungen.

#### Wie läuft der Flow ab?

1. Eine berechtigte Person öffnet zum Beispiel den JSON-Datenexport einer Personenakte.
2. Ohne gültiges Sudo-Fenster leitet die Anwendung auf `/sudo/` weiter; `next` enthält die Adresse des gewünschten Exports.
3. Die Seite **Re-Authentifizierung erforderlich** fragt **Passwort** ab und bei bestätigtem TOTP-Gerät zusätzlich **Bestätigungscode (2FA)**.
4. Der Server prüft das Passwort mit `request.user.check_password()` und die Aktivität des Zugangs; gegebenenfalls prüft er anschließend den TOTP- oder Backup-Code.
5. Bei Erfolg setzt er `session['sudo_until']` entsprechend der konfigurierten Dauer, protokolliert `SUDO_MODE_ENTERED` und leitet zum Export zurück.
6. Weitere geschützte Aktionen in derselben Sitzung können bis zum Ende des Fensters ohne erneute Bestätigung ausgeführt werden. Danach folgt bei der nächsten geschützten Aktion wieder die Abfrage.

Falsches Passwort oder falscher Zweitfaktor: HTTP 403 mit sichtbarem Formular und Audit-Eintrag `SUDO_MODE_FAILED`. Erlaubt sind **5 POST-Versuche pro Minute und Zugang**; eine Überschreitung liefert HTTP 429. Sudo-Fehlversuche zählen nicht als fehlgeschlagene Anmeldungen für die Konto-Sperre.

#### Konfiguration

| Parameter | Default | Wirkung |
|-----------|---------|---------|
| `SUDO_MODE_ENABLED` | `True` | In Produktion erzwungen: ein falscher Wert verhindert den Start. Tests deaktivieren es standardmäßig für RBAC-Prüfungen |
| `SUDO_MODE_TTL_SECONDS` | `900` (15 min) | Dauer des Re-Auth-Fensters |

**Relevante Dateien:**
- [`src/core/services/security/sudo_mode.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/sudo_mode.py) — `enter_sudo`, `is_in_sudo`, `clear_sudo`, `RequireSudoModeMixin`
- [`src/core/views/sudo_mode.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/sudo_mode.py) — `SudoModeView` (Re-Auth-Form), Rate-Limit
- [`src/templates/auth/sudo_mode.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/auth/sudo_mode.html) — Form-Template
- [`src/core/views/dsgvo.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/dsgvo.py), [`src/core/views/clients.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/clients.py), [`src/core/views/mfa.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/mfa.py) — Verwendung des Mixins

Refs #683.

---

### 14. Was bedeutet die Sensitivitätsstufe (Normal/Erhöht/Hoch)?

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
- [`src/core/models/document_type.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/document_type.py) — `Sensitivity`-Choices
- [`src/core/services/compliance/sensitivity.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/compliance/sensitivity.py) — Zentrale Logik für Rollen-/Feldprüfung

### 15. Wie funktioniert das Löschsystem (4-Augen-Prinzip)?

Die Entscheidung ob direkt gelöscht oder ein Löschantrag erstellt wird, hängt von der **Kontaktstufe der Person** ab:

| Kontaktstufe | Löschung | Genehmigung nötig? |
|--------------|----------|---------------------|
| **Mit Zusatzangaben** | Löschantrag → zweite Person mit Recht „Löschbestätigung“ muss genehmigen | Ja (4-Augen) |
| **Nur Pseudonym** | Sofort Soft-Delete | Nein |
| **Anonym** (`is_anonymous`) | Sofort Soft-Delete | Nein |
| **Kein Client** | Sofort Soft-Delete | Nein |

**Ablauf bei Personen der Stufe „Mit Zusatzangaben":**
1. Staff kann nur **eigene** Events löschen, Lead/Admin kann **alle** löschen
2. System erstellt `DeletionRequest` mit Status `pending`
3. Eine zweite Person mit Recht „Löschbestätigung“ prüft den Antrag — DB-Constraint `deletion_request_different_reviewer` erzwingt: **Reviewer ≠ Antragsteller**
4. Approve → Event wird soft-deleted + anonymisiert | Reject → Event bleibt

Das Recht „Löschbestätigung“ ist rollenunabhängig. Beim Anlegen eines Zugangs ist es für Leitung und Anwendungsbetreuung vorbelegt und abwählbar, für andere Rollen nicht; das erste Konto der Ersteinrichtung trägt es. Eigene Anträge dürfen weder genehmigt noch abgelehnt werden. Zur Vergabe siehe [Admin-Handbuch § 7.5](admin-guide.md#75-löschanträge-4-augen-prinzip).

**Was passiert bei einer Löschung?**

| Was | Verhalten |
|-----|-----------|
| `event.is_deleted` | → `True` |
| `event.data_json` | → `{}` (personenbezogene Daten gelöscht) |
| Event-Record in DB | bleibt erhalten (Soft-Delete) |
| EventHistory | Neuer Eintrag mit `_redacted: True` |
| AuditLog | Neuer immutabler Eintrag |

**Dieselbe Staffelung gilt für Fälle** (Refs #1818, [ADR-044](adr/044-fall-loeschpfad.md)): Fachkräfte löschen einen Fall aus der Fallakte heraus; steht die Person auf „Mit Zusatzangaben", entsteht ein Löschantrag über dieselbe Löschanträge-Liste, sonst wandert der Fall direkt in den **Fall-Papierkorb** (`/cases/trash/`, Anwendungsbetreuung). Von dort holt ihn „Wiederherstellen" vollständig zurück — mit Episoden, Wirkungszielen und Dateianhängen —, bis die Papierkorb-Frist `client_trash_days` abläuft und er automatisch anonymisiert wird (Titel, Beschreibung, Zusatzfelder, Episoden, Ziele und Dateien fallen, die Fall-Zeile bleibt als anonymes Aggregat stehen). Zugeordnete **Kontakte bleiben bei der Person** und blockieren die Löschung nicht; sie verlieren ihre Fallzuordnung erst bei der endgültigen Tilgung.

**Und für Zusammenführungen** (Refs #1876, [ADR-045](adr/045-personen-merge.md)): Doppelt angelegte Personen führt die Akte über **⋮ → Mit Duplikat zusammenführen** zusammen — dieselbe Staffelung: Trägt eine der beiden Personen „Mit Zusatzangaben", entsteht ein Antrag über dieselbe Löschanträge-Liste (dort als eigene Art **Zusammenführung → Ziel** ausgewiesen), sonst führt die Leitung direkt zusammen. Ereignisse, Fälle, Aufgaben und Merkliste wandern zur Ziel-Akte, die Kontaktstufe steigt auf die höhere der beiden; die aufgelöste Akte liegt mit dem Vermerk „zusammengeführt in …" im Personen-Papierkorb und bietet dort **Zusammenführung aufheben** statt „Wiederherstellen" an, bis `client_trash_days` abläuft und der Aufbewahrungslauf sie wie jede gelöschte Person redigiert (der Verweis auf das Ziel bleibt). Die Art.-15-Auskunft weist den Vorgang auf beiden Seiten aus (Abschnitt „Zusammenführungen" bzw. Stammdaten-Zeile „Zusammengeführt in").

**Relevante Dateien:**
- [`src/core/models/workitem.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/workitem.py) — `DeletionRequest`
- [`src/core/services/events/crud.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/events/crud.py) — `soft_delete_event`; [`src/core/services/events/deletion.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/events/deletion.py) — `request_deletion`, `approve_deletion`, `reject_deletion`
- [`src/core/views/events.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/events.py) — `EventDeleteView`; [`src/core/views/event_deletion.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/event_deletion.py) — `DeletionRequestReviewView`
- [`src/core/services/case/deletion.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/case/deletion.py) — `soft_delete_case`, `request_case_deletion`, `approve_case_deletion`, `restore_case`, `anonymize_case`
- [`src/core/views/case_deletion.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/case_deletion.py) — `CaseDeleteRequestView`, `CaseTrashView`, `CaseRestoreView`, `CaseAnonymizeView`
- [`src/core/services/client/merge.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/client/merge.py) — `merge_clients`, `request_client_merge`, `approve_client_merge`, `unmerge_client`
- [`src/core/views/client_merge.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/client_merge.py) — `ClientMergeView`, `ClientUnmergeView`, `ClientSimilarHintView`

### 16. Was hat KEINEN Löschmechanismus?

Nicht alle Datentypen bieten eine einzelne Löschaktion in der Oberfläche. Davon zu unterscheiden sind die automatischen Wartungsläufe:

- **Episoden und Wirkungsziele** — kein eigener Löschweg; ihre Inhalte werden mit der Anonymisierung des Falls bzw. der Person redigiert.
- **Meilensteine** — ab Fachkraft über **Meilenstein löschen** im Fall löschbar. Zustands- und Bearbeitungssperren der Akte gelten auch hier.
- **Zugänge** — kein allgemeiner Löschweg in der fachlichen Oberfläche; die Anwendungsbetreuung kann Zugänge deaktivieren.
- **AuditLog-Einträge** — im Benutzerbetrieb unveränderlich. Der Wartungslauf `enforce_retention` entfernt Einträge nach `auditlog_retention_months` (Vorgabe: 24 Monate; 0 deaktiviert das Pruning). Ausgenommen bleiben Sicherheitsverletzungen, Aufbewahrungslauf-Nachweise, Audit-Pruning-Prüfpunkte und Berichts-Exporte mit Berichtszeitraum. Personen- und DSGVO-Exporte ohne Berichtszeitraum sind nicht von dieser Ausnahme erfasst.
- **Ereignis-Änderungshistorie (EventHistory)** — zweistufige Aufbewahrung: Alte Inhalte werden nach `eventhistory_detail_months` redigiert (Vorgabe: 24 Monate), die jüngste Fassung eines lebenden Ereignisses bleibt erhalten. Bei gelöschten Ereignissen oder anonymisierten Personen werden Inhalte unabhängig von dieser Altersfrist redigiert. Die leeren Chronologie-Zeilen werden nach weiteren `eventhistory_chronicle_months` ab Redaktion gelöscht (Vorgabe: 24 Monate). 0 deaktiviert jeweils die altersbezogene Stufe; aktive Aufbewahrungssperren an Ereignis, Person oder Fall stellen die Wartung zurück. Gleichzeitige Bearbeitungen können ebenfalls einen späteren Lauf nötig machen.

**Personen und Fälle** haben einen manuellen Löschweg mit Papierkorb (siehe [Frage 15](#15-wie-funktioniert-das-löschsystem-4-augen-prinzip)) sowie automatische Anonymisierung durch `enforce_retention`.

---

## D. Administration & Betrieb

### 17. Wie funktionieren Aufbewahrungsfristen?

Konfigurierbar pro Einrichtung unter **Verwaltung → Einstellungen**:

| Kontaktstufe | Default | Setting |
|--------------|---------|---------|
| Anonym | 90 Tage | `retention_anonymous_days` |
| Nur Pseudonym | 365 Tage | `retention_identified_days` |
| Mit Zusatzangaben | 3.650 Tage (10 Jahre) | `retention_qualified_days` |
| Activity-Logs | 365 Tage | `retention_activities_days` |

Zusätzlich kann `DocumentType.retention_days` eine **weitere Frist für Ereignis-Typen** festlegen. Sie ersetzt die kontaktstufenbezogenen Fristen nicht: Ein Ereignis kann über jeden zutreffenden Aufbewahrungsweg ablaufen; eine längere Typ-Frist verlängert daher die andere Frist nicht. Die Sensibilität des Typs legt keine Frist automatisch fest. Für Personen- und Fall-Typen greift diese Typ-Frist nicht — ihre Daten und Anhänge folgen dem Lebenszyklus der Person bzw. des Falls. Die **Papierkorb-Frist** `client_trash_days` (Default 30 Tage) gilt für gelöschte **Personen und Fälle** gleichermaßen — bewusst kein zweiter Fristbegriff ([ADR-044](adr/044-fall-loeschpfad.md) § 2).

**Automatische Durchsetzung:** `enforce_retention` löscht anonym und „Nur Pseudonym“ nach Fristablauf. Für „Mit Zusatzangaben“ legt der Standardlauf Vorschläge an; erst eine andere Person mit „Löschbestätigung“ gibt die Löschung frei. Zurückstellen schützt bis zum Datum, Ablehnen bis zu einer neuen Vorlage frühestens nach der Wiedervorlagefrist (Default 365 Tage); Auto-Freigabe bleibt Opt-in.

Ohne Fall beginnt die Frist ab Ereignisdatum, offen ab letzter fachlicher Fallaktivität (Fallback Anlage), geschlossen ab Fallabschluss. Die Dokumentationstyp-Frist heißt **Höchstfrist (verkürzt nur)**; der früheste Stichtag gewinnt. Hold und Art. 18 schützen Ereignisse, Personen-Anonymisierung und Historien-Redaktion im Lauf. Leitung/Anwendungsbetreuung setzen Holds direkt in Personen-, Fall- und Ereignisakten, die Systemliste bietet Aufheben.

Ohne Ereignis gilt für Personen die Inaktivitätsfrist `retention_identified_days` ab letzter Änderung. Abgeschlossene Lösch-, Zusammenführungs- und Herabstufungsanträge verfallen nach `deletion_request_retention_days` (Default 1095 Tage), offene Anträge bleiben. Audit-Zeilen ohne Einrichtung verfallen installationsweit nach `AUDITLOG_SYSTEM_RETENTION_MONTHS` (Default 24 Monate, 0 = aus); die dokumentierten dauerhaften Ausnahmen bleiben geschützt. Audit-Zeilen mit Einrichtung zu Zielen unter aktivem Hold bleiben erhalten. Monatsaggregate bleiben unbefristet, auch bei manueller Ereignislöschung. Personenstatus und Papierkorb nennen noch offene Historienfassungen nach Anonymisierung.

**Relevante Dateien:**
- [`src/core/models/settings.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py) — Retention-Felder
- [`src/core/management/commands/enforce_retention.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/enforce_retention.py) — `--dry-run`, `--facility`

### 18. Welche automatisierten Scripts gibt es?

#### Cron-Dienst und Host-Backup

Die Anwendungsjobs laufen im **`cron`-Dienst** des Produktionsstacks (Supercronic). Maßgeblich ist die [Jobtabelle im Ops-Runbook § 3.1](ops-runbook.md#31-uebersicht), abgeglichen mit [`deploy/crontab`](https://github.com/anlaufstelle/app/blob/main/deploy/crontab). Der Dienst nutzt die konfigurierbare Zeitzone `TZ`, standardmäßig **Europe/Berlin** — die Zeitangaben sind keine festen UTC-Zeiten.

Das **verschlüsselte Backup** ist eine gesonderte Host-Aufgabe. Der Betreiber muss es einrichten und zeitlich vor dem Aufbewahrungslauf einplanen; es wird nicht durch den `cron`-Dienst erzeugt. Einrichtung und Reihenfolge stehen im [Ops-Runbook § 3.3](ops-runbook.md#33-host-cron-backup--restliche-host-jobs).

#### Management Commands

| Command | Zweck | Ausführung |
|---------|-------|------------|
| [`enforce_retention`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/enforce_retention.py) | Aufbewahrungsfristen durchsetzen | Cron täglich |
| [`create_statistics_snapshots`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/create_statistics_snapshots.py) | Monatliche Statistik-Snapshots | Cron monatlich |
| [`setup_facility`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/setup_facility.py) | Ersteinrichtung einer Einrichtung | Manuell |
| [`generate_dsgvo_package`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/generate_dsgvo_package.py) | DSGVO-Dokumentation generieren | Manuell |
| [`reencrypt_fields`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/reencrypt_fields.py) | Key-Rotation für verschlüsselte Felder | Manuell |
| [`inventory_report_bindings`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/inventory_report_bindings.py) | Inventar der Berichtszuordnung einer Einrichtung: welcher Berichtsschlüssel steckt in welchem Backend, welche Dokumentationstypen und Feldvorlagen tragen ihn — und welche in keinem Bericht erscheinen | Manuell |
| [`seed`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/seed.py) | Demo-/Testdaten erzeugen | Nur Dev |

**Dokumentation:** [`docs/ops-runbook.md`](https://github.com/anlaufstelle/app/blob/main/docs/ops-runbook.md)

### 18a. Wo finde ich eine Anleitung zu den Berichten (Reports)?

Die Doku liegt auf drei Ebenen — je nachdem, ob Sie Berichte **erzeugen**, **betreiben** oder **neue Berichtsstandards entwickeln** wollen.

#### 1. Berichte erzeugen (Leitung / Administration)

[User-Guide § 7 „Statistik und Export"](user-guide.md#7-statistik-und-export) — die Schritt-für-Schritt-Anleitung an der Oberfläche:

| Abschnitt | Inhalt |
|---|---|
| [Die drei Reiter im Überblick](https://github.com/anlaufstelle/app/blob/main/docs/user-guide.md#die-drei-reiter-im-überblick) | Aufgaben, gemeinsamer Zeitraum und Gründe für abweichende Zahlen |
| Statistik-Dashboard | Zeitraumwahl, Jahresnavigation, Trend-Charts, Dokumentationstyp-Filter |
| Ereignisliste / PDF-Export | Personenbezogene Ereignis-CSV in der Übersicht; Berichts-PDFs in den Datensatz-Berichten |
| Beispiel-Sachbericht | Vorlage im festen Layout — ausdrücklich **kein** verbindlicher Behördennachweis |
| **Datensatz-Berichte (CSV / JSON / XML / PDF)** | Reiter „Datensatz-Berichte" → Berichtskarte wählen → Vorschau → Download; Vollständigkeitshinweise + K-Anonymität |
| Allgemeine Kennzahlen | Karte in den Datensatz-Berichten; Live-Auswertung mit Kleinstfall-Unterdrückung |
| [Freigaben](https://github.com/anlaufstelle/app/blob/main/docs/user-guide.md#freigaben-leitung--admin) | Markierte Datei-Herausgaben und Überschneidungswarnung |

**Der wichtigste Stolperstein: zwei Datenquellen.** Das Beispiel-Sachbericht-PDF liest Monats-Aggregate mit Live-Fallback; Vorschau und CSV/JSON/XML sowie die anderen Berichte rechnen live. Monats-Aggregate können Ereigniszahlen nach Löschfristen erhalten, unterschiedliche Personen werden jedoch live über den Gesamtzeitraum gezählt, nicht monatlich addiert. Quelle und Grundgesamtheit stehen am Bericht, Formatunterschiede an der Berichtskarte.

#### 2. Berichte betreiben (Administration)

[Admin-Guide § 8 „Statistik-Snapshots & Materialized View"](admin-guide.md#8-statistik-snapshots--materialized-view) — Snapshot-Erzeugung, Refresh-Intervall (stündlich vs. nächtlich) und der Abschnitt **„Herausgabe von Berichten: was Sie wissen müssen"**.

#### 3. Eigene Berichts-Backends entwickeln

[`docs/reporting-plugins.md`](reporting-plugins.md) — die vollständige Entwickler-Anleitung:

- **Architektur + API-Referenz:** `DatasetBackend`, `DatasetResult`, `FieldSpec`, `CompletenessIssue`
- **„Eigenes Backend in fünf Schritten":** Modul registrieren → Spalten deklarieren → Datensatz bauen → K-Anonymität sicherstellen → Tests schreiben
- **Discovery-Regeln** und die mitgelieferten Beispiel-Backends
- **K-Anonymität** über den `prepare_export`-Hook inkl. der bekannten Fallstricke (Summen über Feldwerte, Mehrfachnennungen, Zusicherung je Artefakt statt artefaktübergreifend)
- **Export-Formate** (CSV-Metadatenzeile, JSON, XML), **Completeness**, Grenzen und eine **Test-Checkliste** für neue Backends

Die Design-Entscheidung dahinter: [ADR-038 — Reporting-Backends als Plugin-System](adr/038-reporting-plugin-backends.md).

**Relevante Dateien:**
- [`src/core/reporting/registry.py`](https://github.com/anlaufstelle/app/blob/main/src/core/reporting/registry.py) — Registrierung + Discovery der Backends
- [`src/core/reporting/base.py`](https://github.com/anlaufstelle/app/blob/main/src/core/reporting/base.py) — `DatasetBackend`, `DatasetResult`, `FieldSpec`
- [`src/core/reporting/export.py`](https://github.com/anlaufstelle/app/blob/main/src/core/reporting/export.py) — CSV-/JSON-/XML-Serialisierung
- [`src/core/reporting/backends/`](https://github.com/anlaufstelle/app/tree/main/src/core/reporting/backends) — mitgelieferte Beispiel-Backends
- [`src/core/views/statistics.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/statistics.py) — Dashboard, Vorschau, Download-Routen

> **Englisch:** User-Guide, Admin-Guide und die Plugin-Anleitung gibt es auch unter [`docs/en/`](en/) — diese FAQ nicht.

### 18b. Wie importiere ich Personen oder Ereignisse aus einer CSV-Datei?

Der CSV-Import ist ausschließlich für die Rolle **Anwendungsbetreuung** verfügbar. [Admin-Handbuch § 2.12 „Import-Assistent"](admin-guide.md#212-import-assistent) erklärt Dateiformat und Grenzen sowie die Schritte Datei, Zuordnung, Probelauf, Übernahme und Abbruch. Prüfen Sie die Fehlerliste vollständig, bevor Sie einen fehlerfreien Lauf übernehmen.

### 18c. Warum gibt es unter Statistik drei Reiter — und warum zeigen sie unterschiedliche Zahlen?

Die drei Reiter erfüllen unterschiedliche Aufgaben:

- **Übersicht:** interner Blick auf Kennzahlen, Diagramme und Tabellen ohne Kleinstfall-Unterdrückung; Top-Personen werden mit Pseudonym gezeigt. Die **Ereignisliste (CSV, intern, mit Pseudonymen)** enthält personenbezogene Einzelereignisse.
- **Datensatz-Berichte:** fachliche Auswertungen mit Vorschau und CSV/JSON/XML/PDF, darunter **Allgemeine Kennzahlen** und **Summe von Zahlenfeldern**. Kleinstfallzahlen werden unterdrückt, Personen-Pseudonyme fehlen; daraus folgt allein keine Anonymitätsgarantie.
- **Freigaben:** markierte Datei-Herausgaben mit Berichtszeitraum und Warnungen bei nicht identischen, überlappenden Zeiträumen desselben Berichts. Vorschauen und Weitergaben außerhalb der Anwendung fehlen.

Ein Reiterwechsel behält den Zeitraum. Freigaben zeigt Herausgaben unabhängig davon; der Dokumentationstyp-Filter wirkt nur in der Übersicht und deren Ereignisliste.

Zahlen können durch **Unterdrückung**, unterschiedliche **Grundgesamtheiten** und **Datenquellen** abweichen. Live-Abfragen sehen nur den aktuellen Bestand. Monats-Aggregate können gelöschte Ereigniszahlen erhalten; das Beispiel-Sachbericht-PDF nutzt sie mit Live-Fallback, seine Vorschau und anderen Formate rechnen live. Unterschiedliche Personen werden live über den Gesamtzeitraum gezählt, nicht aus Monatswerten addiert. Beachten Sie Quelle und Grundgesamtheit je Bericht und Format.

Die Freigabenanzeige hat 50 Einträge je Seite; die Warnprüfung berücksichtigt alle noch protokollierten passenden Herausgaben desselben Artefakts. Identische Zeiträume lösen keine Warnung aus, können aber bei verändertem Bestand andere Inhalte haben. Verschiedene Empfänger sind nur dann organisatorisch weniger riskant, wenn keine Zusammenführung stattfindet. Die Historie ist kein vollständiger Nachweis aller Weitergaben.

Entscheidungshilfe: [Benutzerhandbuch § 7 „Die drei Reiter im Überblick“](https://github.com/anlaufstelle/app/blob/main/docs/user-guide.md#die-drei-reiter-im-überblick). Details: [Freigaben](https://github.com/anlaufstelle/app/blob/main/docs/user-guide.md#freigaben-leitung--admin). Regeln zur Herausgabe: [Admin-Handbuch § 8](https://github.com/anlaufstelle/app/blob/main/docs/admin-guide.md#herausgabe-von-berichten-was-sie-wissen-müssen).

---

## E. Fehlermeldungen

### 19. Was bedeutet „Der Datensatz wurde zwischenzeitlich bearbeitet. Bitte laden Sie die Seite neu."?

Jemand anderes (oder Sie selbst in einem anderen Tab) hat denselben Datensatz parallel bearbeitet und bereits gespeichert. Um zu verhindern, dass Änderungen stillschweigend überschrieben werden (**Optimistic Locking**), blockt das System Ihren Speicherversuch.

**Lösung:**
1. Seite neu laden — damit sehen Sie die aktuellste Version.
2. Prüfen, welche Änderungen die andere Person eingetragen hat.
3. Ihre eigenen Änderungen erneut eintragen und speichern.

So gehen weder Ihre noch die parallelen Änderungen verloren.

---

*Konsolidiert aus #105, #429, #471, #506. Erstverifikation der Inhalte am 29.04.2026 gegen v0.10.2. v0.10.0-Ergänzungen (#589): File Vault, Offline-Modus, Fuzzy Search, Quick-Templates, Optimistic Locking. Ergänzung 25.04.2026: WorkItem-Hinweis-vs-Aufgabe und Wiedervorlage (FAQ #7, #8). Ergänzung 29.04.2026 (#703): MFA-Backup-Codes als Self-Service-Recovery (FAQ #4) und Account-Lockout nach 10 Fehlversuchen (FAQ #4a) — beide zeitgleich eingeführt. Die Krypto-, Lockout- und Offline-Angaben wurden am 2026-07-13 im Doku-Review gegen den Code nachgeprüft (Refs #1551).*

## Nachtrag: Offline-Schlüsseldauer und Widerrufs-Gate

**Offline-Schlüssel und Widerrufsprüfung** (#2528). Der aus Passwort und Server-Salt abgeleitete non-extractable `CryptoKey` bleibt in der Browser-IndexedDB gespeichert und übersteht Tab-, Browser- und Geräteneustart. „Non-extractable“ verhindert den Export über WebCrypto; der Schutz des gespeicherten Schlüssels gegen Zugriff auf das Browserprofil hängt an **Geräteverschlüsselung und Bildschirmsperre**, die für Offline-Geräte verpflichtend sind. Beim Ableiten wird eine absolute Frist eingefroren: `session_absolute_hours` der Einrichtung (Vorgabe 12 Stunden). Aktivität und Navigation verlängern sie nicht. Die Anwendung prüft beim Laden, vor Entschlüsselung/Speicherung/Replay, bei Tab-Rückkehr und im 60-Sekunden-Intervall. Am Ablauf entzieht sie den Schlüssel und zeigt „Offline-Schlüssel abgelaufen — bitte online anmelden“; ungesendete Arbeit bleibt verschlüsselt erhalten und wird nach erneutem Online-Login mit unverändertem Passwort und Salt wieder lesbar. Das Leerlauffenster gilt zusätzlich. Fehlende oder ungültige Ablaufdaten sperren den Zugriff; Bundles ohne gültiges `expiresAt` gelten als abgelaufen. Ein beobachteter Rücksprung der Geräteuhr sperrt den Schlüssel; eine manipulierbare Geräteuhr bleibt dennoch ein Restrisiko. Bei ausgeschalteter Anwendung erfolgt kein Schlüsselentzug per Fernlöschung.

Meldet die Widerrufsliste einen HTTP-/Netz-/Verarbeitungsfehler (auch 500/429) oder liefert sie eine ungültige Antwortstruktur, bleiben Queue- und Edit-Replay ausgesetzt: „Synchronisation wartet auf Widerrufsprüfung“. Der Sperrzustand wird in der lokalen IndexedDB gespeichert. Zusätzlich braucht jeder Replay-Lauf eine eigene frische, strukturell gültige, erfolgreich angewandte und persistierte Widerrufsprüfung; eine bereits erteilte Freigabe erlaubt keinen weiteren Lauf und keinen anderen Tab, auch wenn Metadatenschreiben und Ersatzlöschung gleichzeitig scheitern. Fehlende oder unlesbare Metadaten sperren den Replay; auch nach einem Lesefehler ist eine neue erfolgreiche Prüfung nötig. Der nächste Sync-Lauf prüft erneut, ebenso ein direktes Speichern aus der Offline-Akte. Bei fehlgeschlagener Prüfung bleibt ungesendete Arbeit verschlüsselt erhalten. Die bestehenden Idempotenz-Schlüssel bleiben erhalten, und die Server-Gates (422 für widerrufene Personen, 409 für Statuskonflikte) bleiben verbindlich. Geräteverschlüsselung ersetzt diese Server-Prüfungen nicht.
