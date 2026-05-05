# Anlaufstelle – Admin-Handbuch

Dieses Handbuch richtet sich an IT-Administratoren sozialer Einrichtungen, die Anlaufstelle installieren, konfigurieren und betreiben.

---

## Inhaltsverzeichnis

1. [Installation (Docker Compose)](#1-installation-docker-compose)
2. [Erstkonfiguration](#2-erstkonfiguration)
   - 2.5 [Dokumentationstypen konfigurieren](#25-dokumentationstypen-konfigurieren)
   - 2.6 [Auswahloptionen verwalten](#26-auswahloptionen-verwalten-feldvorlagen)
   - 2.6b [Fuzzy-Suche (pg_trgm)](#26b-fuzzy-suche-pg_trgm)
   - 2.7 [Zwei-Faktor-Authentifizierung (2FA)](#27-zwei-faktor-authentifizierung-2fa)
   - 2.8 [Schnell-Vorlagen (Quick-Templates)](#28-schnell-vorlagen-quick-templates)
   - 2.9 [Encrypted File Vault & Virus-Scanning](#29-encrypted-file-vault--virus-scanning)
   - 2.10 [Offline-Modus & Streetwork (M6A)](#210-offline-modus--streetwork-m6a)
3. [Backup und Wiederherstellung](#3-backup-und-wiederherstellung)
4. [Updates](#4-updates)
5. [Monitoring](#5-monitoring)
   - 5.4 [CSP-Debugging](#54-csp-debugging)
6. [Troubleshooting](#6-troubleshooting)
7. [DSGVO-Hinweise](#7-dsgvo-hinweise)
   - 7.8 [Optimistic Locking](#78-optimistic-locking)
   - 7.9 [Row Level Security (RLS)](#79-row-level-security-rls)
8. [Statistik-Snapshots & Materialized View](#8-statistik-snapshots--materialized-view)

---

## 1. Installation (Docker Compose)

> **Alternative: Coolify auf Hetzner CX22** — Für das empfohlene Deployment via [Coolify](https://coolify.io/) (inkl. TLS, Backups und ClamAV) siehe den separaten Leitfaden [`docs/coolify-deployment.md`](https://github.com/tobiasnix/anlaufstelle/blob/main/docs/coolify-deployment.md). Die folgenden Docker-Compose-Anleitungen gelten weiterhin für manuelle Deployments.

### Voraussetzungen

- Docker Engine 24 oder neuer
- Docker Compose v2 (als Plugin: `docker compose`)
- Öffentlich erreichbarer Server mit DNS-Eintrag für Ihre Domain
- Ports 80 und 443 müssen von außen erreichbar sein

### Schritt 1: Dateien herunterladen

```bash
git clone https://github.com/anlaufstelle/app.git
cd app
```

Alternativ: Nur die benötigten Produktionsdateien herunterladen:

```bash
curl -O https://raw.githubusercontent.com/anlaufstelle/app/main/docker-compose.prod.yml
curl -O https://raw.githubusercontent.com/anlaufstelle/app/main/Caddyfile
```

### Schritt 2: Umgebungsvariablen konfigurieren

Erstellen Sie eine `.env`-Datei im selben Verzeichnis wie `docker-compose.prod.yml`:

```bash
cp .env.example .env   # falls vorhanden, sonst manuell anlegen
```

Minimale `.env` für den Produktionsbetrieb:

```dotenv
# Domain (muss DNS-Eintrag haben)
DOMAIN=anlaufstelle.meine-einrichtung.de

# Django
DJANGO_SECRET_KEY=<langer-zufaelliger-string>
DJANGO_SETTINGS_MODULE=anlaufstelle.settings.prod
ALLOWED_HOSTS=anlaufstelle.meine-einrichtung.de

# Datenbank
POSTGRES_DB=anlaufstelle
POSTGRES_USER=anlaufstelle
POSTGRES_PASSWORD=<sicheres-datenbankpasswort>

# Feldverschlüsselung (Pflicht in Produktion)
# Empfohlen: Plural-Form für Rotation; der erste Key ist Write-Key, weitere sind Read-Only.
ENCRYPTION_KEYS=<fernet-schluessel-1>
```

**Verschlüsselungsschlüssel generieren:**

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Secret Key generieren:**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

> **Wichtig:** Speichern Sie `ENCRYPTION_KEYS` und `DJANGO_SECRET_KEY` sicher (z. B. in einem Passwortmanager oder Secret-Management-System). Ohne die Schlüssel sind verschlüsselte Felddaten nicht mehr lesbar.

#### Vollständige Umgebungsvariablen-Referenz

Alle ENV-Variablen, die die Anwendung zur Laufzeit auswertet (siehe [`src/anlaufstelle/settings/base.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/anlaufstelle/settings/base.py) und [`prod.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/anlaufstelle/settings/prod.py)):

**Django & Hosts**

| Name | Default | Beschreibung |
|---|---|---|
| `DJANGO_SECRET_KEY` | — (Pflicht in prod) | Signierung von Sessions/CSRF. Mit `secrets.token_urlsafe(50)` generieren. |
| `DJANGO_SETTINGS_MODULE` | — | In Produktion `anlaufstelle.settings.prod`. |
| `ALLOWED_HOSTS` | — (Pflicht in prod) | Komma-separierte Hostnamen, z. B. `anlaufstelle.example.de`. |
| `TRUSTED_PROXY_HOPS` | `1` | Anzahl vertrauenswürdiger Proxies vor der App (X-Forwarded-For-Auswertung). `0` = kein Proxy, `1` = nur Caddy, `2` = CDN + Caddy. |

**Datenbank (PostgreSQL)**

| Name | Default | Beschreibung |
|---|---|---|
| `POSTGRES_DB` | `anlaufstelle` | Datenbankname. |
| `POSTGRES_USER` | `anlaufstelle` | DB-Benutzer. |
| `POSTGRES_PASSWORD` | `anlaufstelle` | DB-Passwort (in Produktion sicher setzen!). |
| `POSTGRES_HOST` | `localhost` (via Compose: `db`) | DB-Host. |
| `POSTGRES_PORT` | `5432` | DB-Port. |

**Feldverschlüsselung (MultiFernet-Rotation)**

| Name | Default | Beschreibung |
|---|---|---|
| `ENCRYPTION_KEYS` | — | Komma-separierte Liste von Fernet-Keys. Der **erste** Key ist Write-Key (neue Daten werden damit verschlüsselt), alle weiteren sind Read-Only (Decrypt-Fallback für Rotation). |
| `ENCRYPTION_KEY` | — | Legacy-Single-Key (Einzelkey). Mindestens eine der beiden Variablen muss in Produktion gesetzt sein, sonst verweigert die App den Start. |

**Virus-Scan (ClamAV, [#524](https://github.com/tobiasnix/anlaufstelle/issues/524))**

| Name | Default (prod) | Beschreibung |
|---|---|---|
| `CLAMAV_ENABLED` | `true` | Aktiviert den Virenscan vor Upload-Verschlüsselung. Fail-closed: Ist der Daemon nicht erreichbar, wird der Upload abgewiesen. |
| `CLAMAV_HOST` | `clamav` | Hostname des ClamAV-Daemons (Service-Name im Compose-Netzwerk). |
| `CLAMAV_PORT` | `3310` | TCP-Port des clamd-Sockets. |
| `CLAMAV_TIMEOUT` | `30` | Timeout in Sekunden pro Scan-Aufruf. |

**Logging**

| Name | Default | Beschreibung |
|---|---|---|
| `LOG_FORMAT` | `text` | `json` aktiviert strukturiertes Logging über [`core.logging.JsonFormatter`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/logging.py) — empfohlen für Produktion mit Log-Aggregation. |

**Sentry (optional)**

| Name | Default | Beschreibung |
|---|---|---|
| `SENTRY_DSN` | — | Wenn gesetzt, wird Sentry initialisiert (PII wird **nicht** gesendet, `send_default_pii=False`). |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.1` | Sample-Rate für Performance-Traces (0.0–1.0). |

**E-Mail (SMTP, Produktion)**

| Name | Default | Beschreibung |
|---|---|---|
| `EMAIL_HOST` | `localhost` | SMTP-Host für Password-Reset- und Invite-Mails. |
| `EMAIL_PORT` | `587` | SMTP-Port. |
| `EMAIL_HOST_USER` | — | SMTP-Benutzer. |
| `EMAIL_HOST_PASSWORD` | — | SMTP-Passwort. |
| `EMAIL_USE_TLS` | `True` | STARTTLS aktivieren. |
| `DEFAULT_FROM_EMAIL` | `noreply@anlaufstelle.app` | Absenderadresse. |

**Sonstiges**

| Name | Default | Beschreibung |
|---|---|---|
| `MEDIA_ROOT` | `<BASE_DIR>/media` | Ablage verschlüsselter Dateianhänge (siehe [§ 2.9](#29-encrypted-file-vault--virus-scanning)). |

### Schritt 3: Caddyfile prüfen

Die mitgelieferte `Caddyfile` übernimmt automatisch TLS-Zertifikate via Let's Encrypt:

```
{$DOMAIN} {
    reverse_proxy web:8000
    ...
}
```

Keine Anpassung nötig, solange `DOMAIN` in der `.env` gesetzt ist.

### Schritt 4: Stack starten

```bash
docker compose -f docker-compose.prod.yml up -d
```

Beim ersten Start:
- PostgreSQL-Datenbank wird initialisiert
- Django-Migrationen werden automatisch ausgeführt
- Caddy beantragt ein TLS-Zertifikat

Status prüfen:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs web
```

### Schritt 5: Gesundheitscheck

```bash
curl https://anlaufstelle.meine-einrichtung.de/health/
```

Erwartete Antwort:

```json
{"status": "ok", "database": "connected", "version": "dev"}
```

---

## 2. Erstkonfiguration

### 2.1 Einrichtung und Admin-Benutzer anlegen

Führen Sie das interaktive Setup-Skript aus:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py setup_facility
```

Das Skript fragt interaktiv nach:

1. **Name der Organisation** – z. B. `Diakonie Musterstadt e.V.`
2. **Name der Einrichtung** – z. B. `Beratungsstelle Nord`
3. **Admin-Benutzername** – Standard: `admin`
4. **Admin-Passwort** (zweimal eingeben)

Anschließend sind Organisation, Einrichtung, Standardeinstellungen und der erste Admin-Benutzer angelegt.

> **Hinweis:** Sollte eine Organisation oder Einrichtung mit dem angegebenen Namen bereits existieren, wird sie wiederverwendet. Ein bestehender Benutzername wird nicht überschrieben.

### 2.2 Django-Adminoberfläche

Die Adminoberfläche ist erreichbar unter:

```
https://anlaufstelle.meine-einrichtung.de/admin/
```

Hier können Sie verwalten:

| Bereich | Pfad im Admin | Beschreibung |
|---|---|---|
| Organisationen | Core → Organisationen | Träger (Mandantenebene) |
| Einrichtungen | Core → Einrichtungen | Standorte einer Organisation |
| Einstellungen | Core → Einstellungen | Konfiguration pro Einrichtung |
| Benutzer | Core → Benutzer | Benutzerkonten und Rollen |
| Dokumentationstypen | Core → Dokumentationstypen | Konfigurierbare Vorlagen |
| Zeitfenster | Core → Zeitfenster | Benannte Zeiträume für Auswertungen |
| Audit-Log | Core → Audit-Logs | Unveränderliches Protokoll |

### 2.3 Einstellungen pro Einrichtung

Im Admin unter **Core → Einstellungen** können Sie für jede Einrichtung konfigurieren:

| Feld | Standardwert | Beschreibung |
|---|---|---|
| Vollständiger Name | – | Wird in Berichten angezeigt |
| Standard-Dokumentationstyp | – | Vorausgewählter Typ bei neuem Eintrag |
| Session-Timeout (Minuten) | 30 | Automatischer Logout nach Inaktivität |
| Aufbewahrung anonym (Tage) | 90 | Löschfrist für anonyme Kontakte |
| Aufbewahrung identifiziert (Tage) | 365 | Löschfrist für identifizierte Kontakte |
| Aufbewahrung qualifiziert (Tage) | 3650 | Löschfrist nach Fallabschluss |
| 2FA einrichtungsweit verpflichtend | false | Erzwingt TOTP-2FA für **alle** Benutzer dieser Einrichtung (siehe [§ 2.7](#27-zwei-faktor-authentifizierung-2fa)) |

### 2.4 Weitere Benutzer anlegen

Im Admin unter **Core → Benutzer → Benutzer hinzufügen**:

- **Benutzername:** Anmeldename (keine Klarnamen)
- **E-Mail:** Pflicht für den Invite-Flow (siehe unten)
- **Anzeigename:** Name für die Benutzeroberfläche
- **Rolle:** Eine der vier Rollen (siehe unten)
- **Einrichtung:** Zuweisung zu einer Einrichtung

#### Token-Invite-Flow (Refs [#528](https://github.com/tobiasnix/anlaufstelle/issues/528))

Neue Konten werden **ohne** Klartext-Passwort angelegt. Stattdessen versendet die Anwendung eine **Einladungs-E-Mail** mit einem personalisierten Setup-Link an die hinterlegte E-Mail-Adresse. Der Link führt den neuen Nutzer auf das Standard-Password-Reset-Formular, wo er eigenhändig ein Passwort setzt.

1. Admin legt Nutzer mit E-Mail im Admin an.
2. System ruft [`send_invite_email`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/invite.py) auf und generiert Token (Djangos `default_token_generator`, basierend auf `uidb64` + Token-Hash).
3. Nutzer empfängt Mail, klickt Link, setzt Passwort und wird eingeloggt.

**Token-Gültigkeit:** Django-Default ist `PASSWORD_RESET_TIMEOUT = 259200` Sekunden (3 Tage). In der Anlaufstelle kann die Frist über die Django-Setting auf bis zu 7 Tage erhöht werden — der Token-Generator invalidiert den Token außerdem automatisch, sobald der Nutzer sein erstes Passwort gesetzt hat.

**Setup-Link erneut senden:** Ist die Mail nicht angekommen oder der Token abgelaufen, kann der Admin in der Benutzer-Detailansicht über „Setup-Link erneut senden" (bzw. den analogen „Einladung erneut senden"-Button) einen neuen Token ausstellen und eine frische Mail verschicken lassen.

**Fallback ohne E-Mail:** Wird ein Nutzer **ohne** E-Mail-Adresse angelegt, erzeugt der Admin zur Abwärtskompatibilität einmalig ein Klartext-Initialpasswort, das in der Admin-Oberfläche nach dem Speichern angezeigt wird. Dieser Weg ist **unsicher** und sollte nur als Notlösung verwendet werden — besser: E-Mail nachtragen und Einladung neu versenden.

> **Hinweis:** `must_change_password` wird beim Invite-Flow automatisch gesetzt, ist beim Token-Flow aber redundant — das Passwort wird ohnehin im Setup-Schritt vom Nutzer selbst gewählt.

#### Rollenbeschreibung

| Rolle | Bezeichnung | Berechtigungen |
|---|---|---|
| `admin` | Administrator | Vollzugriff auf alle Bereiche und Einstellungen |
| `lead` | Leitung | Auswertungen, Löschanträge genehmigen, alle Fälle einsehen |
| `staff` | Fachkraft | Kontakte, Fälle, Dokumentation erfassen und bearbeiten |
| `assistant` | Assistenz | Eingeschränkte Erfassung, kein Zugriff auf qualifizierte Daten |

### 2.5 Dokumentationstypen konfigurieren

Unter **Core → Dokumentationstypen** legen Sie fest, welche Arten von Dokumentation in der Einrichtung erfasst werden können. Jeder Typ besteht aus:

- **Name und Beschreibung**
- **Feldvorlagen** (Freitextfelder, Auswahlfelder etc.)
- **Löschfrist** in Tagen (überschreibt die globale Einrichtungseinstellung, falls gesetzt)

#### Kategorie

Die Kategorie gruppiert Dokumentationstypen für Filter und die **Statistik-Seite**:

| Kategorie | Bedeutung | Beispiel |
|-----------|-----------|----------|
| **Kontakt** | Direkte Kontakte mit Klientel | Beratungsgespräch, Krisengespräch |
| **Leistung** | Erbrachte Leistungen | Spritzentausch, Begleitung |
| **Verwaltung** | Administrative Vorgänge | Hausverbot, Vermittlung |
| **Notiz** | Freie Notizen | Beobachtungen, Vermerke |

> **Hinweis:** Die Kategorie ist eine **reine UI-Gruppierung** für Statistik-Anzeige und Admin-Filter. Sie beeinflusst weder den Jugendamt-Export noch die Bann-/Krisen-Logik — dafür ist der **Systemtyp** maßgeblich (siehe unten).
>
> Der Tripel `(Einrichtung, Name, Kategorie)` ist eindeutig: Pro Einrichtung kann derselbe Dokumentationstyp-Name nur einmal pro Kategorie existieren. Gleiche Namen unter unterschiedlichen Kategorien (z.B. „Notiz" als Verwaltung *und* als Notiz) sind erlaubt.

#### Sensibilitätsstufe

Die Sensibilitätsstufe steuert, welche Rollen auf Einträge dieses Typs zugreifen können:

| Stufe | Zugriff | Verwendung |
|-------|---------|------------|
| **Normal** | Alle Rollen (inkl. Assistenz) | Allgemeine Kontakte, Leistungen |
| **Erhöht** | Fachkraft, Leitung, Admin | Beratungsgespräche, medizinische Versorgung |
| **Hoch** | Leitung und Admin | Krisengespräche, besonders sensible Daten |

#### Systemtyp

Der Systemtyp verknüpft einen Dokumentationstyp mit interner Anwendungslogik. Er wird bei der Erstellung gesetzt und ist danach **nicht mehr änderbar**.

Aktuell hat der Systemtyp zwei Funktionen:

**1. UI-Logik** (nur Hausverbot und Krisengespräch):

| Systemtyp | Auswirkung |
|-----------|------------|
| **Hausverbot** | Aktiviert Hausverbot-Banner auf der Klientelseite, eigener Filter im Zeitstrom, Zählung und Highlight in der Übergabe |
| **Krisengespräch** | Wird als Highlight in der Übergabe angezeigt (letzte Krisen-Events) |

**2. Jugendamt-Export** (Zuordnung zu Berichtskategorien):

| Systemtyp | Export-Kategorie |
|-----------|-----------------|
| **Kontakt** | Kontakte |
| **Beratungsgespräch** | Beratung |
| **Krisengespräch** | Beratung |
| **Medizinische Versorgung** | Versorgung |
| **Spritzentausch** | Versorgung |
| **Begleitung** | Vermittlung |
| **Vermittlung** | Vermittlung |
| **Notiz** | *(wird ausgeschlossen)* |

> **Hinweis:** Nicht jeder Dokumentationstyp benötigt einen Systemtyp. Typen ohne Systemtyp haben keine spezielle interne Logik und werden vom Jugendamt-Export ausgeschlossen, funktionieren aber ganz normal für die Dokumentation.
>
> Systemtypen wie `note`, `contact`, `counseling` oder `medical` haben **keine sichtbare UI-Logik** (anders als `ban` und `crisis`). Sie dienen ausschließlich als Whitelist für den Jugendamt-Export — die Kategorie alleine entscheidet nicht, ob ein Eintrag exportiert wird.

#### Mindest-Kontaktstufe

Legt fest, welche Kontaktstufe ein Klientel mindestens haben muss, damit ein Ereignis dieses Typs erstellt werden kann. Beispiel: Beratungsgespräche erfordern mindestens „Qualifiziert", weil die Identität des Klientel bekannt sein muss.

#### Feldebenen-Sensibilität (`FieldTemplate.sensitivity`)

Neben der Sensibilität des Dokumenttyps kann jedes einzelne Feld einer Feldvorlage eine **eigene** Sensibilitätsstufe erhalten (`FieldTemplate.sensitivity`). Effektiv gilt für die Sichtbarkeit des Feldes das **Maximum** aus Dokumenttyp- und Feld-Sensibilität — ein als `HIGH` markiertes Feld bleibt für Fachkräfte unsichtbar, auch wenn der Dokumenttyp selbst nur `NORMAL` ist.

**Entkopplung Verschlüsselung ↔ Sichtbarkeit** (Refs [#356](https://github.com/tobiasnix/anlaufstelle/issues/356)): Die beiden Flags `is_encrypted` (Ruhe-Verschlüsselung des Werts in der Datenbank) und `sensitivity` (Sichtbarkeit im UI) sind **unabhängig** voneinander konfigurierbar:

- Ein Feld kann verschlüsselt auf der Platte liegen, aber für `NORMAL`-Sensibilität im UI sichtbar sein (z. B. Kontaktdaten, die alle Rollen sehen dürfen, die aber nicht im Klartext gespeichert werden sollen).
- Ein nicht-verschlüsseltes Feld kann trotzdem auf Leitung/Admin beschränkt werden (z. B. statistische Marker, die sensitiv sind, aber nicht verschlüsselt gespeichert werden müssen).

#### Löschschutz bei bestehenden Daten

Felder, zu denen bereits Werte in Ereignissen existieren, können **nicht ohne Weiteres gelöscht** werden. Beim Versuch, ein Feld im Admin zu entfernen, greift ein Schutzmechanismus, der auf gespeicherte Werte prüft. Wird das Feld dennoch benötigt zu entfernen, muss vorher eine Daten-Migration laufen, die die Werte aufräumt oder in ein anderes Feld überführt.

> **Pragmatische Alternative:** Statt zu löschen, das Feld im Dokumenttyp-Mapping deaktivieren — bestehende Daten bleiben erhalten, neue Ereignisse bekommen das Feld nicht mehr angeboten.

### 2.6 Auswahloptionen verwalten (Feldvorlagen)

Unter **Core → Feldvorlagen** können Sie die Optionen von Auswahl- und Mehrfachauswahl-Feldern (Select / Multi-Select) bearbeiten. Die Optionen werden im Feld **Options json** als JSON-Array gespeichert.

> **Gilt nur für Select / Multi-Select.** Für andere Feldtypen (Text, Textbereich, Zahl, Datum, Uhrzeit, Ja/Nein, Datei) wird `options_json` nicht ausgewertet — das Feld bitte leer lassen (`[]`).

#### Default-Werte (`default_value`)

Im Feld **Default-Wert** einer Feldvorlage kann ein Vorgabewert hinterlegt werden, der beim **Neu-Anlegen** eines Ereignisses vorgeblendet wird. Beim **Bearbeiten** eines bestehenden Ereignisses hat der gespeicherte Wert immer Vorrang.

| Feldtyp | Format | Beispiel |
|---|---|---|
| Text / Textbereich | beliebiger String | `Standard-Notiz` |
| Zahl | Ganzzahl | `15` |
| Datum | ISO-Format `YYYY-MM-DD` | `2026-01-01` |
| Uhrzeit | ISO-Format `HH:MM` oder `HH:MM:SS` | `09:30` |
| Ja/Nein | `true` oder `false` | `true` |
| Auswahl | Slug einer aktiven Option | `beratung` |
| Mehrfachauswahl | Komma-getrennte Liste aktiver Options-Slugs | `beratung, essen` |
| Datei | nicht unterstützt | — |

Vorrangregel beim Neu-Anlegen: **Quick-Template > Default-Wert > leer**. Ungültige Werte werden in `FieldTemplate.clean()` beim Speichern im Admin abgelehnt.

**Schema einer Option:**

```json
[
  {"slug": "beratung", "label": "Beratung", "is_active": true},
  {"slug": "essen", "label": "Essen", "is_active": true}
]
```

| Feld | Beschreibung |
|---|---|
| `slug` | Technischer Bezeichner (unveränderlich nach Erstellung) |
| `label` | Anzeigename in Formularen und Exporten |
| `is_active` | `true` = wählbar, `false` = deaktiviert |

#### Option deaktivieren statt löschen

Wenn eine Option nicht mehr benötigt wird, setzen Sie `is_active` auf `false` statt die Option zu entfernen:

```json
{"slug": "sachspenden", "label": "Sachspenden", "is_active": false}
```

**Auswirkungen einer Deaktivierung:**

| Bereich | Verhalten |
|---|---|
| Neues Ereignis erfassen | Option wird **nicht** angeboten |
| Bestehendes Ereignis bearbeiten | Option bleibt sichtbar mit Kennzeichnung *„(deaktiviert)"*, Wert geht nicht verloren |
| CSV-Export | Label wird weiterhin korrekt aufgelöst |
| Statistik | Bestehende Werte fließen weiterhin in Auswertungen ein |

> **Wichtig:** Entfernen Sie Optionen nicht aus dem JSON, wenn bereits Ereignisse mit diesem Wert existieren. Nutzen Sie stattdessen `"is_active": false`. So bleiben historische Daten konsistent und Exporte vollständig.

### 2.6b Fuzzy-Suche (pg_trgm)

Die globale Suche nach Klientel (Pseudonymen) nutzt zusätzlich zur exakten Teilstring-Suche eine **Trigramm-basierte Fuzzy-Suche** — tolerant gegenüber Tippfehlern und phonetischen Varianten (z. B. „Schmidt" ↔ „Schmitt"). Implementierung: [`src/core/services/search.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/search.py), Funktion `search_similar_clients`.

**Schwelle pro Einrichtung:** Unter **Core → Einstellungen → *Einrichtung*** → Feld **„Fuzzy-Search-Schwelle"** (`Settings.search_trigram_threshold`):

| Wertebereich | Default | Wirkung |
|---|---|---|
| `0.0`–`1.0` | `0.3` | Mindest-Ähnlichkeit für Treffer. Kleinere Werte liefern **mehr**, aber **ungenauere** Treffer; größere Werte sind strenger. |

**Empfehlung:** Bei zu vielen Fehltreffern schrittweise erhöhen (0.35, 0.4); bei zu wenigen Treffern schrittweise senken (0.25, 0.2).

#### Voraussetzung: `pg_trgm`-Extension

Die PostgreSQL-Extension `pg_trgm` muss aktiviert sein. Beim Standard-Deployment wird sie automatisch per Django-Migration eingerichtet. Falls sie (z. B. nach einem manuellen DB-Restore) fehlt, manuell nachholen:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

Referenzen: [#536](https://github.com/tobiasnix/anlaufstelle/issues/536), [#581](https://github.com/tobiasnix/anlaufstelle/issues/581).

### 2.7 Zwei-Faktor-Authentifizierung (2FA)

Die Anlaufstelle unterstützt TOTP-basierte 2FA über [`django-otp`](https://django-otp-official.readthedocs.io/). Jeder Benutzer kann 2FA selbst aktivieren (`/mfa/settings/`); zusätzlich gibt es zwei Verpflichtungs-Ebenen.

**Benutzerseitige Doku:** [User-Guide § 1 — Zwei-Faktor-Authentifizierung](user-guide.md#zwei-faktor-authentifizierung-2fa).

#### Erzwingung konfigurieren

| Ebene | Feld | Ort im Admin | Wirkung |
|---|---|---|---|
| **Einzeluser** | `User.mfa_required` | Core → Benutzer → *User* → Feld „MFA erforderlich" | 2FA verpflichtend für diesen User, Deaktivieren gesperrt |
| **Einrichtungsweit** | `Settings.mfa_enforced_facility_wide` | Core → Einstellungen → *Einrichtung* → Feld „2FA einrichtungsweit verpflichtend" | 2FA verpflichtend für **alle** User dieser Einrichtung |

Die Auswertung erfolgt über [`User.is_mfa_enforced`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/user.py) — User gilt als verpflichtet, wenn **eine** der beiden Ebenen zutrifft. Die MFA-Middleware blockiert den Zugriff auf geschützte Bereiche, bis ein TOTP-Code in der Session verifiziert wurde.

#### 2FA für einen Benutzer zurücksetzen

Wenn eine Mitarbeiterin ihr Authenticator-Gerät verliert, muss ein Administrator das TOTP-Gerät löschen, damit sie neu einrichten kann:

1. **Admin → OTP → TOTP-Geräte** aufrufen.
2. Das Gerät des betroffenen Users auswählen und löschen.
3. User informieren: nach dem nächsten Login wird automatisch auf `/mfa/setup/` umgeleitet (bei `is_mfa_enforced=True`) oder der User kann 2FA freiwillig neu einrichten.

Seit v0.10.1 gibt es zusätzlich **Backup-Codes als zweiten Faktor** für genau diesen Recovery-Fall (Refs [#588](https://github.com/tobiasnix/anlaufstelle/issues/588)). Bei der 2FA-Einrichtung erhält der User 10 einmalig nutzbare Codes, die er ausgedruckt oder im Passwort-Manager hinterlegen sollte — am Login-2FA-Prompt kann er statt eines TOTP-Codes einen Backup-Code eingeben. Verbrauchte Codes werden invalidiert und im AuditLog (`MFA_BACKUP_CODE_USED`) protokolliert. Sind alle 10 Codes verbraucht oder verloren, bleibt der Admin-Reset oben der Fallback.

#### Account-Lockout

Nach **10 fehlgeschlagenen Login-Versuchen** wird das Konto automatisch gesperrt (Login-Service liest die Schwelle aus [`src/core/services/login_lockout.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/login_lockout.py)). Der gesperrte User sieht eine Hinweis-Seite und kann sich nicht mehr anmelden, bis ein Admin entsperrt:

1. **Admin → Core → Benutzer** → betroffenen User auswählen.
2. Im User-Profil unter „Account-Status" auf **Sperre aufheben** klicken.
3. Cleanup wird im AuditLog als `LOGIN_UNLOCK` protokolliert (das `LOGIN_FAILED`-Log selbst ist dank `auditlog_immutable`-DB-Trigger unveränderbar).

Sperre, Entsperre und alle Versuche während der Sperrphase werden im AuditLog mitprotokolliert — nutzen Sie den Filter „Anmeldung fehlgeschlagen" / „Sperre aufgehoben" für eine retroaktive Auswertung.

#### Audit-Spur

Alle 2FA-Vorgänge werden im `AuditLog` protokolliert (Actions: `MFA_ENABLED`, `MFA_DISABLED`, `MFA_FAILED`). Nutzen Sie den Filter im Admin unter **Core → Audit-Logs**, um fehlgeschlagene Verifikationsversuche oder Aktivierungs-/Deaktivierungsereignisse auszuwerten.

#### Relevante Dateien

- Modelle: [`src/core/models/user.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/user.py), [`src/core/models/settings.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/settings.py)
- Views: [`src/core/views/mfa.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/mfa.py)
- Middleware: [`src/core/middleware/`](https://github.com/tobiasnix/anlaufstelle/tree/main/src/core/middleware)

### 2.8 Schnell-Vorlagen (Quick-Templates)

**Schnell-Vorlagen** sind vorbefüllte Dokumentvorlagen für wiederkehrende Muster (z. B. „Beratungsgespräch 30 Min", „Standard-Check-in"). Fachkräfte wenden sie per Klick auf der Seite „Neuer Kontakt" an.

**Benutzerseitige Doku:** [User-Guide § 3 — Schnell-Vorlagen](user-guide.md#schnell-vorlagen-quick-templates).

#### Vorlagen verwalten

Vorlagen werden im Django-Admin unter **Core → Quick-Templates** gepflegt.

| Feld | Beschreibung |
|---|---|
| `facility` | Mandantenisolation — jede Vorlage gehört zu genau einer Einrichtung |
| `document_type` | Dokumentationstyp, der beim Anwenden vorausgewählt wird |
| `name` | Anzeigename auf dem Button (z. B. „Beratungsgespräch 30 Min") |
| `prefilled_data` | JSON-Objekt `{slug: wert}` — mappt Feld-Slugs auf Standardwerte |
| `sort_order` | Reihenfolge der Buttons auf „Neuer Kontakt" |
| `is_active` | Nur aktive Vorlagen erscheinen zur Anwendung |
| `created_by` | User, der die Vorlage angelegt hat (nur informativ) |

#### `prefilled_data` — Filterregeln

Der Service-Layer wendet vor dem Speichern und erneut beim Anwenden eine **Whitelist** an ([`src/core/services/quick_templates.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/quick_templates.py)). Werte bleiben nur erhalten, wenn:

- der Slug zum gewählten Dokumentationstyp gehört,
- die **effektive Sensitivität** des Feldes `NORMAL` ist (kein Prefill für `ELEVATED`/`HIGH`),
- das Feld nicht verschlüsselt ist (`is_encrypted=False`) und kein `FILE`-Feld ist,
- bei `SELECT`/`MULTI_SELECT` der Wert einer **aktiven** Option des aktuellen FieldTemplates entspricht.

Dadurch sind Vorlagen **selbstheilend**: Wird eine Auswahl-Option später deaktiviert, müssen Vorlagen nicht migriert werden — der veraltete Wert wird beim nächsten Anwenden stillschweigend verworfen.

#### Rollen- und Sensitivitäts-Sichtbarkeit

Schnell-Vorlagen werden pro User über [`user_can_see_document_type`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/sensitivity.py) gefiltert. Eine Assistenz, die `ELEVATED`/`HIGH`-Dokumentationstypen nicht sehen darf, sieht dafür auch keine Vorlagen. So taucht keine Vorlage in der Button-Liste auf, die der User anschließend gar nicht anwenden könnte.

#### Operative Hinweise

- Vorlagen sind ein **Bequemlichkeits-Layer**, keine Datenquelle — das Ereignis wird weiter explizit vom User gespeichert, alle Felder bleiben editierbar.
- Anwenden einer Vorlage **überschreibt bestehende Werte nicht**; gefüllt werden nur leere Felder.
- Aktuell gibt es **keine Custom-UI** für die Vorlagen-Verwaltung außerhalb des Django-Admin. Nur User mit der Rolle `admin` können Vorlagen pflegen.

#### Relevante Dateien

- Modell: [`src/core/models/quick_template.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/quick_template.py)
- Service: [`src/core/services/quick_templates.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/quick_templates.py)
- View-Integration: [`src/core/views/events.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/events.py) (`EventCreateView`)
- Tracking-Issue: [#494](https://github.com/tobiasnix/anlaufstelle/issues/494)

### 2.9 Encrypted File Vault & Virus-Scanning

Dateianhänge (Fotos, Scans, Dokumente) an Ereignissen werden in einem **verschlüsselten Vault** abgelegt: vor dem Schreiben in `MEDIA_ROOT` auf Viren geprüft und anschließend chunk-weise per **Fernet** (AES-128-CBC mit HMAC-SHA256, [`cryptography.fernet`](https://cryptography.io/en/latest/fernet/)) mit dem `ENCRYPTION_KEYS`-Schlüsselmaterial verschlüsselt. Implementierung: [`encrypt_file()` in `src/core/services/encryption.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/encryption.py). Refs [#524](https://github.com/tobiasnix/anlaufstelle/issues/524).

#### Upload-Flow

1. Fachkraft wählt Datei im Event-Formular aus ([`src/core/forms/events.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/forms/events.py)).
2. Server prüft Dateityp und Größe (siehe `allowed_file_types` und `max_file_size_mb` in den Einrichtungseinstellungen — Default **10 MB**).
3. **ClamAV-Scan** vor Verschlüsselung:
   - Standard: fail-closed. Ist der Daemon nicht erreichbar (`CLAMAV_ENABLED=true`, aber kein TCP-Connect möglich), wird der Upload **abgelehnt**.
   - Erkannter Virus → Upload wird verworfen, ein Audit-Log-Eintrag wird geschrieben.
4. Inhalt wird chunk-weise mit Fernet (MultiFernet) verschlüsselt (Write-Key = erster Eintrag aus `ENCRYPTION_KEYS`) und in `MEDIA_ROOT` gespeichert.
5. Download erfolgt ausschließlich über die geschützte Django-View (kein direkter Webserver-Zugriff auf `MEDIA_ROOT`).

#### ClamAV-Service (`docker-compose.prod.yml`)

Das Produktions-Compose enthält einen eigenen `clamav`-Container mit Healthcheck (`clamdcheck.sh`). Der Web-Dienst wartet auf `service_healthy`, bevor er startet. Referenz: [`docker-compose.prod.yml`](https://github.com/tobiasnix/anlaufstelle/blob/main/docker-compose.prod.yml). Konfigurierbar über `CLAMAV_ENABLED` / `CLAMAV_HOST` / `CLAMAV_PORT` / `CLAMAV_TIMEOUT` (siehe [ENV-Referenz in § 1](#vollständige-umgebungsvariablen-referenz)).

#### Healthcheck

Der `/health/`-Endpoint prüft in Produktion zusätzlich zur Datenbank die Erreichbarkeit des ClamAV-Daemons. Ist ClamAV aktiviert, aber nicht erreichbar, signalisiert der Endpoint einen Fehlerzustand (HTTP 503) — so erkennen externe Monitore auch „Uploads sind aktuell nicht möglich".

#### Schlüssel-Rotation (`ENCRYPTION_KEYS`)

MultiFernet akzeptiert eine Liste von Schlüsseln. Um zu rotieren:

1. **Neuen Schlüssel generieren** und als **ersten** Eintrag in `ENCRYPTION_KEYS` einfügen: `ENCRYPTION_KEYS=neu,alt`. Neu geschriebene Daten werden mit `neu` verschlüsselt, vorhandene mit `alt` lassen sich weiterhin lesen.
2. Optional: `python manage.py reencrypt_fields` laufen lassen, damit bestehende Felder auf den neuen Key umgeschlüsselt werden (siehe [`src/core/management/commands/reencrypt_fields.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/management/commands/reencrypt_fields.py)).
3. Nach erfolgreichem Umschlüsseln den alten Schlüssel aus der Liste entfernen.

> **Wichtig:** Niemals den alten Key **ersetzen** statt **ergänzen** — sonst werden alle noch nicht umgeschlüsselten Bestandsdateien unlesbar.

#### Upload-Limit

- Per Einrichtung: `Settings.max_file_size_mb` (Default **10 MB**) — siehe [`src/core/models/settings.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/settings.py).
- Global: Djangos `DATA_UPLOAD_MAX_MEMORY_SIZE` gilt als zusätzliche harte Grenze. Erhöhung via ENV bzw. Settings-Override, falls größere Uploads erlaubt sein sollen.

#### Sichere Downloads (RFC 5987)

Alle File-Downloads werden über den zentralen Helper [`safe_download_response`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/utils/downloads.py) ausgeliefert. Der Helper setzt `Content-Disposition` mit RFC-5987-kodierten Dateinamen (Unicode-sicher, kein Reverse-Path-Traversal) und verhindert Browser-MIME-Sniffing.

### 2.10 Offline-Modus & Streetwork (M6A)

Für Außeneinsätze (z. B. Streetwork) bietet die Anlaufstelle einen **sicheren Offline-Modus** mit **client-seitiger** Ende-zu-Ende-Verschlüsselung aller auf dem Gerät zwischengespeicherten Daten. Refs [#573](https://github.com/tobiasnix/anlaufstelle/issues/573), [#576](https://github.com/tobiasnix/anlaufstelle/issues/576).

#### Kryptographie-Design

| Aspekt | Wert |
|---|---|
| Algorithmus | **AES-GCM-256** (WebCrypto-native) |
| Storage | **IndexedDB** (nur AES-Chiffretext, **niemals** Klartext) |
| Key-Ableitung | **PBKDF2** — 600 000 Iterationen, SHA-256 |
| KDF-Input | Benutzer-Passwort + `User.offline_key_salt` (16 Byte, pro User, serverseitig gespeichert) |
| Key-Lebenszeit | Nur im Memory (`CryptoKey` mit `extractable: false`) |

Quelle: [`src/static/js/crypto.js`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/static/js/crypto.js), [`src/core/services/offline_keys.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/offline_keys.py).

#### Konsequenzen für Admins

- **Key-Verlust ist nicht reparierbar:** Vergisst ein Nutzer sein Passwort, sind alle auf seinem Gerät lokal gespeicherten Offline-Daten **dauerhaft unlesbar**. Ein Admin-Reset der Offline-Daten ist technisch **nicht möglich** — der Schlüssel existiert nur im Browser des Nutzers.
- **Vor Passwort-Änderung synchronisieren:** Nutzer müssen Offline-Daten mit dem Server synchronisieren, **bevor** sie das Passwort ändern oder sich ausloggen. Bei Passwortwechsel wird das Salt rotiert (neuer Schlüssel).
- **Tab-Close, Logout, Passwort-Wechsel** → In-Memory-Key wird verworfen → gespeicherte Chiffretexte sind ohne erneute Anmeldung nicht mehr zu entschlüsseln.

#### Browser-Voraussetzungen

- WebCrypto-API (`crypto.subtle`) und IndexedDB müssen verfügbar sein.
- Unterstützt: aktuelle Versionen von Firefox, Chrome, Edge, Safari.
- Nicht unterstützt: Legacy-Browser ohne moderne Crypto-API (der Offline-Modus wird in dem Fall ausgegraut).

#### Streetwork-Stufen

- **Stufe 2 (Read-Cache):** Ausgewählte Klientel-Dossiers werden vor dem Streetwork-Einsatz lokal verschlüsselt gecacht, damit sie offline eingesehen werden können.
- **Stufe 3 (Offline-Edit):** Ereignisse und Notizen können offline erfasst werden und synchronisieren beim nächsten Online-Gang. Konflikte zwischen Offline-Edit und zwischenzeitlicher Server-Änderung werden per **Side-by-Side-Diff** dem Nutzer zur manuellen Auflösung präsentiert.

---

## 3. Backup und Wiederherstellung

### 3.1 Datenbank-Backup

Die gesamte Anwendungsdaten liegen in PostgreSQL. Erstellen Sie regelmäßige Dumps:

```bash
docker compose -f docker-compose.prod.yml exec db \
  pg_dump -U anlaufstelle anlaufstelle \
  > backup_$(date +%Y%m%d_%H%M%S).sql
```

Mit Kompression (empfohlen):

```bash
docker compose -f docker-compose.prod.yml exec db \
  pg_dump -U anlaufstelle -Fc anlaufstelle \
  > backup_$(date +%Y%m%d_%H%M%S).dump
```

> **Wichtig:** Sichern Sie die `.env`-Datei (insbesondere `ENCRYPTION_KEY`) separat und an einem anderen sicheren Ort. Ohne den Schlüssel sind verschlüsselte Felder nach einer Wiederherstellung nicht lesbar.

### 3.2 Automatisches Backup einrichten

Empfohlenes Cron-Beispiel für tägliche Backups um 03:00 Uhr (auf dem Host):

```cron
0 3 * * * cd /opt/anlaufstelle && \
  docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U anlaufstelle -Fc anlaufstelle \
  > /mnt/backup/anlaufstelle/backup_$(date +\%Y\%m\%d).dump
```

Ältere Backups nach 30 Tagen löschen:

```cron
0 4 * * * find /mnt/backup/anlaufstelle/ -name "*.dump" -mtime +30 -delete
```

### 3.3 Wiederherstellung

1. Stack stoppen:

```bash
docker compose -f docker-compose.prod.yml down
```

2. Datenbank-Volume löschen (Vorsicht: löscht alle Daten):

```bash
docker volume rm anlaufstelle_pgdata
```

3. Stack neu starten (erstellt leere Datenbank):

```bash
docker compose -f docker-compose.prod.yml up -d db
```

4. Backup einspielen:

```bash
# SQL-Dump:
docker compose -f docker-compose.prod.yml exec -T db \
  psql -U anlaufstelle anlaufstelle < backup_20260101_030000.sql

# Oder komprimiertes Format:
docker compose -f docker-compose.prod.yml exec -T db \
  pg_restore -U anlaufstelle -d anlaufstelle backup_20260101.dump
```

5. Web-Dienst starten:

```bash
docker compose -f docker-compose.prod.yml up -d
```

6. Gesundheitscheck durchführen (siehe [Abschnitt 5](#5-monitoring)).

---

## 4. Updates

### 4.1 Neues Image ziehen und Stack aktualisieren

```bash
# Neuestes Image herunterladen
docker compose -f docker-compose.prod.yml pull

# Stack neu starten (kurze Downtime)
docker compose -f docker-compose.prod.yml up -d
```

Der `web`-Dienst führt beim Start automatisch ausstehende Datenbankmigrationen durch. Prüfen Sie anschließend die Logs:

```bash
docker compose -f docker-compose.prod.yml logs web --tail=50
```

### 4.2 Vor einem Update

- Erstellen Sie immer ein aktuelles Datenbank-Backup (siehe [Abschnitt 3.1](#31-datenbank-backup)).
- Lesen Sie das Changelog zu Breaking Changes und erforderlichen Konfigurationsänderungen.

### 4.3 Rollback

Falls nach einem Update Probleme auftreten:

```bash
# Auf ein bestimmtes Image-Tag zurückwechseln
# In docker-compose.prod.yml: image: ghcr.io/anlaufstelle/app:v1.2.3
docker compose -f docker-compose.prod.yml up -d

# Datenbank-Backup von vor dem Update einspielen (falls nötig)
```

---

## 5. Monitoring

### 5.1 Health-Endpoint

Anlaufstelle stellt einen öffentlichen Health-Endpoint bereit:

```
GET /health/
```

**Antwort bei normalem Betrieb (HTTP 200):**

```json
{
  "status": "ok",
  "database": "connected",
  "version": "dev"
}
```

**Antwort bei Datenbankfehler (HTTP 503):**

```json
{
  "status": "error",
  "database": "unavailable",
  "version": "dev"
}
```

Der Endpoint erfordert keine Authentifizierung und ist für externe Monitoring-Systeme geeignet.

### 5.2 Monitoring-Integration

**Uptime Kuma / Healthchecks.io:**

```
https://anlaufstelle.meine-einrichtung.de/health/
Erwarteter HTTP-Status: 200
Erwarteter Antwort-Inhalt: "status": "ok"
Prüfintervall: 1 Minute
```

**curl-basierter Cron-Check:**

```bash
#!/bin/bash
RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
  https://anlaufstelle.meine-einrichtung.de/health/)
if [ "$RESPONSE" != "200" ]; then
  echo "Anlaufstelle health check failed: HTTP $RESPONSE" | \
    mail -s "ALERT: Anlaufstelle down" admin@meine-einrichtung.de
fi
```

### 5.3 Container-Status

```bash
# Übersicht aller Dienste
docker compose -f docker-compose.prod.yml ps

# Logs live verfolgen
docker compose -f docker-compose.prod.yml logs -f

# Nur Web-Logs
docker compose -f docker-compose.prod.yml logs -f web

# Ressourcenverbrauch
docker stats
```

### 5.4 CSP-Debugging

Die Content-Security-Policy (CSP) wird **zentral in Django** über [`django-csp`](https://django-csp.readthedocs.io/) gesetzt (siehe [`src/anlaufstelle/settings/base.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/anlaufstelle/settings/base.py), `CONTENT_SECURITY_POLICY`). Die frühere redundante CSP-Konfiguration im Caddyfile wurde entfernt — nur so ist sichergestellt, dass App- und Reverse-Proxy-Policy nicht auseinanderlaufen.

**Inline-Skripte sind nicht erlaubt.** Alle JavaScript-Logik liegt in externen Dateien unter `src/static/js/`, eingebunden per `<script src=…>` oder über Nonce-Aware-Template-Tags.

**`script-src` global ohne `'unsafe-eval'`.** Mit der Migration auf den `@alpinejs/csp`-Build (v0.10.2) ist `'unsafe-eval'` aus der globalen Policy entfernt. Alle Alpine-Komponenten sind als `Alpine.data()`-Komponenten in [`src/static/js/alpine-components.js`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/static/js/alpine-components.js) registriert; Architektur-Tests verbieten Inline-`x-data="{...}"` und komplexe Expressions (Ternaries, `||`/`&&`, Method-Calls, Object-Literale) in Alpine-/HTMX-Direktiven.

**Ausnahme `/admin-mgmt/*` (Django-Admin):** django-unfold lädt einen eigenen Alpine-Build, der für die Cmd+K-Suche `new AsyncFunction()`-basierte Auswertung nutzt und damit ohne `'unsafe-eval'` nicht initialisiert. Die [`AdminCSPRelaxMiddleware`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/middleware/) ergänzt `'unsafe-eval'` deshalb **per Request nur für Admin-Routen** — diese sind durch MFA-Gate und Rolle `admin` zusätzlich geschützt. Außerhalb des Admins bleibt die strenge globale Policy aktiv.

**Typische Fehlerbilder im Browser-Console:**

- `Refused to execute inline script because it violates the following Content Security Policy directive` — Inline-`<script>`-Block im Template. Auslagern in eine statische JS-Datei oder über ein Nonce-Aware-Template-Tag einbinden.
- `Refused to load the script … because it violates … directive: "script-src 'self'"` — externes Script-CDN wird nicht unterstützt; alle Skripte müssen aus `self` stammen.
- `Refused to evaluate a string as JavaScript because 'unsafe-eval' is not an allowed source` — auf normalen Routen erwartet (Architektur-Bruch); im Admin-Bereich Hinweis darauf, dass die Relax-Middleware nicht greift (Route-Pattern in [`AdminCSPRelaxMiddleware`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/middleware/) prüfen).

Bei CSP-Fehlern nach einem Update: Browser-Console auf **konkret blockierte URL/Quelle** prüfen und entscheiden, ob die Quelle ins Template verschoben oder die CSP-Richtlinie angepasst werden muss.

---

## 6. Troubleshooting

### Problem: Web-Dienst startet nicht

**Symptom:** `docker compose ps` zeigt `web` als `Restarting` oder `Exit 1`.

**Vorgehen:**

```bash
docker compose -f docker-compose.prod.yml logs web
```

Häufige Ursachen:

| Fehlermeldung | Lösung |
|---|---|
| `ENCRYPTION_KEY must be set in production` | `ENCRYPTION_KEY` in `.env` setzen (siehe [Abschnitt 1.2](#schritt-2-umgebungsvariablen-konfigurieren)) |
| `connection refused` (Datenbank) | Prüfen ob `db`-Dienst läuft: `docker compose ps db` |
| `django.db.utils.OperationalError` | Datenbankzugangsdaten in `.env` prüfen |
| `ImproperlyConfigured` | Umgebungsvariablen unvollständig – Logs für Details lesen |

### Problem: TLS-Zertifikat wird nicht ausgestellt

**Symptom:** Browser zeigt Zertifikatsfehler, Caddy-Logs zeigen ACME-Fehler.

```bash
docker compose -f docker-compose.prod.yml logs caddy
```

Häufige Ursachen:
- DNS-Eintrag für `DOMAIN` zeigt noch nicht auf die Server-IP
- Ports 80/443 sind durch Firewall oder Hosting-Provider blockiert
- Let's Encrypt Rate Limit erreicht (max. 5 Zertifikate pro Domain pro Woche)

### Problem: Login schlägt fehl

**Symptom:** Benutzer kann sich nicht anmelden.

```bash
# Audit-Log auf fehlgeschlagene Logins prüfen
docker compose -f docker-compose.prod.yml exec web \
  python manage.py shell -c "
from core.models import AuditLog
for e in AuditLog.objects.filter(action='login_failed').order_by('-timestamp')[:10]:
    print(e.timestamp, e.user, e.ip_address)
"
```

Oder im Admin unter **Core → Audit-Logs**, gefiltert nach Aktion „Anmeldung fehlgeschlagen".

**Passwort zurücksetzen:**

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py changepassword <benutzername>
```

### Problem: Datenbankverbindung schlägt fehl

```bash
# Direkte DB-Verbindung testen
docker compose -f docker-compose.prod.yml exec db \
  psql -U anlaufstelle -c "\l"

# Datenbank-Healthcheck-Status
docker inspect anlaufstelle-db-1 | grep -A5 Health
```

### Problem: Speicherplatz voll

```bash
# Docker-Speicherverbrauch analysieren
docker system df

# Alte Images und ungenutzte Volumes bereinigen
docker system prune -f

# Datenbank-Volume-Größe
docker system df -v | grep pgdata
```

### Diagnose-Befehle auf einen Blick

```bash
# Alle Container-Logs der letzten 100 Zeilen
docker compose -f docker-compose.prod.yml logs --tail=100

# Django-Migrations-Status
docker compose -f docker-compose.prod.yml exec web \
  python manage.py showmigrations

# Django-Konfiguration prüfen
docker compose -f docker-compose.prod.yml exec web \
  python manage.py check --deploy
```

---

## 7. DSGVO-Hinweise

Anlaufstelle wurde nach dem Prinzip **Privacy by Design** (Art. 25 DSGVO) entwickelt. Dieses Kapitel beschreibt die datenschutzrelevanten technischen Maßnahmen und die administrativen Pflichten.

> **Haftungsausschluss:** Die folgenden technischen Maßnahmen unterstützen die DSGVO-Konformität, **ersetzen aber keine rechtliche Beratung**. Der Betreiber ist verantwortlich für die Durchführung einer Datenschutz-Folgenabschätzung (Art. 35 DSGVO), den Abschluss eines Auftragsverarbeitungsvertrags mit dem Hosting-Anbieter (Art. 28 DSGVO) und die Dokumentation der Verarbeitungstätigkeiten (Art. 30 DSGVO). Die Software wurde keinem formalen Sicherheitsaudit durch Dritte unterzogen.

### 7.1 Verschlüsselung

**Feldverschlüsselung (Fernet/AES-128):**

Sensible Felder in der Datenbank werden mit dem `ENCRYPTION_KEY` verschlüsselt gespeichert. Dies gilt als zusätzliche Schutzmaßnahme über die allgemeine Datenbankzugriffskontrolle hinaus.

**Sichtbarkeit vs. Verschlüsselung:** Die Verschlüsselung (`is_encrypted`) und die Sichtbarkeitsstufe (`sensitivity`) eines Feldes sind unabhängig voneinander konfigurierbar. Ein verschlüsseltes Feld kann für alle Rollen sichtbar sein, und ein nicht-verschlüsseltes Feld kann auf bestimmte Rollen eingeschränkt werden.

- Der Schlüssel wird über die Umgebungsvariable `ENCRYPTION_KEY` bereitgestellt.
- In der Produktion verweigert die Anwendung den Start, wenn kein Schlüssel gesetzt ist.
- Ein Schlüsselverlust bedeutet den dauerhaften Verlust der verschlüsselten Daten.

**Schlüsselrotation:** Eine automatische Schlüsselrotation ist aktuell nicht implementiert. Sichern Sie den Schlüssel redundant und getrennt vom Datenbank-Backup.

**Transportverschlüsselung:** Caddy erzwingt HTTPS mit HSTS (`max-age=31536000`). HTTP wird automatisch auf HTTPS weitergeleitet.

### 7.2 Pseudonymisierung

Die Anwendung speichert **keine Klarnamen** in der Datenbank. Klienteldaten werden pseudonymisiert erfasst:

- Klientel werden über interne IDs referenziert.
- Anzeigenamen in der Oberfläche sind konfigurierbare Pseudonyme.
- Qualifizierte (identifizierbare) Daten sind nur für berechtigte Rollen (Leitung, Admin) sichtbar.

### 7.3 Löschfristen und `enforce_retention`

Löschfristen werden pro Einrichtung in den Einstellungen konfiguriert (siehe [Abschnitt 2.3](#23-einstellungen-pro-einrichtung)).

Das Management-Kommando `enforce_retention` setzt die konfigurierten Fristen durch, indem es abgelaufene Datensätze soft-löscht:

| Strategie | Einstellung | Standard |
|---|---|---|
| Anonyme Kontakte | `retention_anonymous_days` | 90 Tage |
| Identifizierte Kontakte | `retention_identified_days` | 365 Tage |
| Qualifizierte Fälle (nach Abschluss) | `retention_qualified_days` | 3650 Tage (10 Jahre) |

**Manueller Aufruf:**

```bash
# Testlauf (kein Löschen)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py enforce_retention --dry-run

# Ausführen
docker compose -f docker-compose.prod.yml exec web \
  python manage.py enforce_retention

# Nur eine bestimmte Einrichtung
docker compose -f docker-compose.prod.yml exec web \
  python manage.py enforce_retention --facility "Beratungsstelle Nord"
```

**Als täglicher Cron einrichten (empfohlen):**

```cron
0 2 * * * cd /opt/anlaufstelle && \
  docker compose -f docker-compose.prod.yml exec -T web \
  python manage.py enforce_retention >> /var/log/anlaufstelle-retention.log 2>&1
```

Jede Ausführung, die Datensätze löscht, wird automatisch im Audit-Log protokolliert.

#### Retention-Dashboard

Unter [`/retention/`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/views/retention.py) steht ein **Retention-Dashboard** bereit, über das Leitung und Admin Löschvorschläge (generiert durch `enforce_retention`) effizient abarbeiten können. Refs [#514](https://github.com/tobiasnix/anlaufstelle/issues/514), [#515](https://github.com/tobiasnix/anlaufstelle/issues/515).

| Bulk-Aktion | Wirkung |
|---|---|
| **Approve** | Vorschlag wird zur Löschung freigegeben. Beim nächsten Retention-Run wird der Datensatz tatsächlich gelöscht (bzw. k-anonymisiert, siehe unten). |
| **Defer** | Vorschlag wird um eine konfigurierbare Frist zurückgestellt (Default 30 Tage) und erscheint im **nächsten Retention-Run erneut**. Nach Überschreiten von `retention_max_defer_count` (Default 2) wird entweder erzwungen entschieden oder — bei `retention_auto_approve_after_defer=True` — automatisch freigegeben. |
| **Reject** | Vorschlag wird dauerhaft verworfen (Datensatz bleibt erhalten). |

#### Legal Hold

Einzelne Datensätze können per **Legal Hold** vor automatischer Löschung geschützt werden (z. B. bei laufenden Verfahren, Prüfverfahren oder Auskunftsersuchen). Das Flag wird am Datensatz bzw. an einem `LegalHold`-Eintrag geführt und vom Retention-Job respektiert — ein Datensatz mit aktivem Legal Hold wird **nicht** zur Löschung vorgeschlagen, auch wenn die Aufbewahrungsfrist abgelaufen ist.

Legal Holds werden im Retention-Dashboard sowie im Django-Admin verwaltet (siehe [`src/core/models/retention.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/retention.py)).

#### K-Anonymisierung statt Hard-Delete

Als Alternative zur Hart-Löschung kann pro Einrichtung die **K-Anonymisierung** aktiviert werden (Refs [#535](https://github.com/tobiasnix/anlaufstelle/issues/535)).

| Feld in `Settings` | Default | Beschreibung |
|---|---|---|
| `retention_use_k_anonymization` | `False` | Aktiviert den Ersatz von Hard-Delete durch K-Anonymisierung. |
| `k_anonymity_threshold` | `5` | Mindest-Gruppengröße pro Bucket. Höhere Werte → stärkere Anonymisierung, weniger Detail. |

Wirkung: Statt den Datensatz zu löschen, werden identifizierende Merkmale aggregiert bzw. pseudonymisiert, sodass statistische Auswertungen über den historischen Zeitraum **erhalten bleiben**, ohne dass Einzelpersonen re-identifiziert werden können. DSGVO-konforme Alternative zur endgültigen Löschung, wenn weiter ausgewertet werden soll.

### 7.4 Audit-Log

Das Audit-Log ist **append-only** und unveränderlich. Es protokolliert automatisch:

| Ereignis | Auslöser |
|---|---|
| Anmeldung / Abmeldung | Jeder Login/Logout |
| Fehlgeschlagene Anmeldung | Falsches Passwort |
| Qualifizierte Daten eingesehen | Zugriff auf identifizierbare Klienteldaten |
| Export | Datenexport durch Benutzer |
| Löschung | Manuell oder durch `enforce_retention` |
| Stufenwechsel | Änderung des Kontaktstatus eines Klientel |
| Einstellungen geändert | Änderungen an Einrichtungseinstellungen |

**Im Admin einsehen:**

Unter **Core → Audit-Logs** können Logs nach Aktion, Einrichtung, Benutzer und Zeitraum gefiltert werden. Nur Administratoren haben Zugriff.

**Aufbewahrung des Audit-Logs:** Das Audit-Log selbst unterliegt keiner automatischen Löschfrist innerhalb der Anwendung. Gemäß Ihrer internen Dokumentationspflicht (z. B. nach Empfehlung des BSI oder Ihrer Datenschutz-Folgenabschätzung) sollten Sie eine externe Archivierungsstrategie festlegen.

### 7.5 Löschanträge (4-Augen-Prinzip)

Löschanträge für Klienteldaten werden im Vier-Augen-Prinzip bearbeitet: Ein Antrag muss von Leitung oder Admin genehmigt werden, bevor Daten endgültig gelöscht werden. Dies schützt vor versehentlicher oder unberechtigter Löschung.

### 7.6 Betroffenenrechte (Art. 15–20 DSGVO)

Für die Bearbeitung von Anfragen betroffener Personen stehen folgende administrative Möglichkeiten zur Verfügung:

| Recht | Maßnahme |
|---|---|
| Auskunft (Art. 15) | Audit-Log und Klienteldaten im Admin einsehen, ggf. exportieren |
| Berichtigung (Art. 16) | Felder im Admin direkt bearbeiten |
| Löschung (Art. 17) | Löschantrag über die Anwendung stellen (4-Augen-Prinzip) |
| Datenportabilität (Art. 20) | Export-Funktion in der Anwendung |

### 7.7 Empfohlene organisatorische Maßnahmen

- Führen Sie ein **Verzeichnis der Verarbeitungstätigkeiten** (Art. 30 DSGVO), das die Nutzung von Anlaufstelle als Verarbeitungstätigkeit beschreibt.
- Stellen Sie sicher, dass der Hosting-Anbieter einen **Auftragsverarbeitungsvertrag (AVV)** nach Art. 28 DSGVO unterzeichnet.
- Führen Sie eine **Datenschutz-Folgenabschätzung (DSFA)** nach Art. 35 DSGVO durch, falls Ihre Einrichtung besonders sensible Daten (z. B. Gesundheitsdaten, Daten schutzbedürftiger Personengruppen) verarbeitet.
- Beschränken Sie den Admin-Zugang auf das notwendige Minimum (**Least Privilege**).
- Aktivieren Sie regelmäßige Passwortänderungen durch Setzen von `must_change_password = True` bei neu angelegten Benutzern.
- Lagern Sie Backups und `ENCRYPTION_KEY` an getrennten, gesicherten Orten.

### 7.8 Optimistic Locking

Zum Schutz vor **stillen Überschreibungen** bei parallelem Bearbeiten desselben Datensatzes (zwei Mitarbeitende haben den gleichen Client/Fall gleichzeitig offen und speichern nacheinander) setzt die Anlaufstelle **Optimistic Locking** auf Service-Ebene ein. Refs [#531](https://github.com/tobiasnix/anlaufstelle/issues/531).

**Betroffene Modelle:** Client, Case, WorkItem, Settings, Event.

**Mechanik** ([`src/core/services/locking.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/services/locking.py)):

- Jedes Formular rendert das aktuelle `updated_at` als Hidden-Field.
- Beim Speichern prüft der Helper `check_version_conflict(instance, expected_updated_at)`, ob der Datensatz in der Zwischenzeit verändert wurde.
- Bei Konflikt wird eine `ValidationError` geworfen; der View leitet den Nutzer zurück mit der Meldung:
  > *„Der Datensatz wurde zwischenzeitlich bearbeitet. Bitte laden Sie die Seite neu."*

**Administrative Hinweise:**
- Es gibt keinen Admin-Schalter, um das Locking abzuschalten — der Schutz ist systemweit aktiv.
- Wenn Nutzer die Konfliktmeldung gehäuft sehen: Prozess prüfen (Mehrfachbearbeitung trennen, Workflows anpassen) statt das Feature zu umgehen.

### 7.9 Row Level Security (RLS)

Zusätzlich zum ORM-seitigen Facility-Scoping ist **PostgreSQL Row-Level-Security** auf **18 facility-scoped Tabellen** als **Defense-in-Depth** aktiviert. Ein fehlerhafter ORM-Query, der das Facility-Scoping vergisst, liefert dank RLS trotzdem keine fremden Daten. Refs [#542](https://github.com/tobiasnix/anlaufstelle/issues/542), [#586](https://github.com/tobiasnix/anlaufstelle/issues/586).

#### Funktionsweise

- Die Middleware [`FacilityScopeMiddleware`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/middleware/facility_scope.py) setzt pro Request die Postgres-Session-Variable `app.current_facility_id` via `SELECT set_config('app.current_facility_id', <id>, false)` (Session-Scope, nicht Transaction-Scope).
- Die RLS-Policies (Migration [`0047_postgres_rls_setup`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/migrations/0047_postgres_rls_setup.py)) filtern jede Zeile über `facility_id = current_setting('app.current_facility_id', true)`.
- **Fail-closed:** Ist die Variable leer oder nicht gesetzt, liefert `current_setting(..., true)` NULL, der Vergleich schlägt fehl und es werden **keine Zeilen** zurückgegeben.
- Die Middleware öffnet den DB-Cursor **nur für authentifizierte Requests**; anonyme Routen (Login, Health-Check, statische Dateien) bleiben unbeeinflusst.
- Bei jedem Request wird der Wert frisch gesetzt (auch leer), damit Connection-Pooling keinen stehengebliebenen Wert aus einem früheren Request leakt.

#### Debugging in `psql`

```sql
-- Aktuellen Facility-Scope einer Session prüfen
SELECT current_setting('app.current_facility_id', true);

-- Variable manuell für eine Debug-Session setzen
SELECT set_config('app.current_facility_id', '<facility-uuid>', false);
```

Ohne gesetzte Variable liefern die geschützten Tabellen in `psql` sichtbar **keine Zeilen** — das ist gewollt.

---

## 8. Statistik-Snapshots & Materialized View

### Überblick: zwei Ebenen

Statistik-Auswertungen in der Anlaufstelle nutzen **zwei unterschiedliche Beschleunigungs-Ebenen**:

1. **Materialized View** (`core_statistics_event_flat`) — aggregiert die aktuellen Event-Daten vor, damit die Statistik-Seite nicht bei jedem Aufruf alle Events neu scannen muss. Refs [#544](https://github.com/tobiasnix/anlaufstelle/issues/544).
2. **Statistik-Snapshots** — monatliche, persistierte Aggregate, die **vor** der automatischen Löschung alter Events gesichert werden. Dadurch bleiben historische Auswertungen auch nach DSGVO-Löschung korrekt.

### Materialized View aktualisieren

Die Materialized View wird **nicht live** mit jedem `INSERT` aktualisiert — sie muss periodisch via Management-Command neu aufgebaut werden:

```bash
# Standard (non-blocking, nutzt CONCURRENTLY wenn möglich)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py refresh_statistics_view

# Blockierend (falls kein UNIQUE-Index — Legacy-Schemas)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py refresh_statistics_view --no-concurrent
```

Implementierung: [`src/core/management/commands/refresh_statistics_view.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/management/commands/refresh_statistics_view.py).

**Empfohlener Cron-Rhythmus:**

```cron
# Stündlich (nahe-echtzeit) — für stark genutzte Statistik-Seiten
0 * * * * cd /opt/anlaufstelle && \
  docker compose -f docker-compose.prod.yml exec -T web \
  python manage.py refresh_statistics_view \
  >> /var/log/anlaufstelle-statistics.log 2>&1

# Alternativ: täglich nachts (für einmalige Berichte) — nur einmal umstellen
# 0 1 * * * …
```

> **Hinweis:** `CONCURRENTLY` blockiert lesende Zugriffe nicht, setzt aber einen UNIQUE-Index auf der Materialized View voraus (wird per Migration angelegt). Der Command fällt bei Fehlern automatisch auf einen non-concurrent Refresh zurück.

### Was sind Statistik-Snapshots?

Anlaufstelle berechnet Statistiken (Dashboard, Halbjahresberichte, Jugendamt-PDFs) standardmäßig aus der Event-Tabelle (über die Materialized View aggregiert). Wenn die automatische Datenlöschung (`enforce_retention`) alte Events entfernt, würden diese aus den Statistiken verschwinden.

**Statistik-Snapshots** sichern monatliche Aggregate, bevor Events gelöscht werden. Die Auswertungen nutzen eine Hybrid-Logik: gespeicherte Snapshots für vergangene Monate, Live-Daten (aus der Materialized View) für den aktuellen Monat.

### Automatische Sicherung

Snapshots werden automatisch erstellt, wenn `enforce_retention` Events löscht — die betroffenen Monate werden unmittelbar vor der Löschung gesichert.

### Periodischer Cron-Job (empfohlen)

Zusätzlich empfiehlt es sich, Snapshots regelmäßig per Cron zu erstellen:

```bash
# Monatlich am 1. um 02:00 — sichert den Vormonat
0 2 1 * * cd /pfad/zur/app && python manage.py create_statistics_snapshots
```

### Ersteinrichtung (Backfill)

Bei der Ersteinrichtung oder nachträglichen Aktivierung können Snapshots für alle vorhandenen Monate erzeugt werden:

```bash
python manage.py create_statistics_snapshots --backfill
```

**Hinweis:** Der Backfill erfasst nur noch vorhandene Events — bereits gelöschte Daten können rückwirkend nicht mehr rekonstruiert werden.

### Weitere Optionen

```bash
# Vorschau (keine Änderungen)
python manage.py create_statistics_snapshots --dry-run

# Nur eine bestimmte Einrichtung
python manage.py create_statistics_snapshots --facility "Meine Einrichtung"

# Bestimmter Monat
python manage.py create_statistics_snapshots --year 2026 --month 2
```

### Einschränkungen

- **CSV-Export:** Der CSV-Export enthält weiterhin nur vorhandene Events (keine Snapshot-Daten), da er Einzelzeilen exportiert.
- **Top-Klientel:** Die Rangliste der aktivsten Klientel wird immer live berechnet und kann sich nach einer Löschung verändern.
- **Eindeutige Klientel:** Die Zählung über mehrere Monate ist eine Näherung (Summe statt exakte Distinct-Zählung).

### Admin-Oberfläche

Snapshots sind im Django-Admin unter **Statistik-Snapshots** einsehbar (nur Lesezugriff). Dort können Sie prüfen, welche Monate gesichert wurden und wann die letzte Aktualisierung stattfand.
