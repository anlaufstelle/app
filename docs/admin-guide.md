# Anlaufstelle – Admin-Handbuch

Dieses Handbuch richtet sich an IT-Administratoren sozialer Einrichtungen, die Anlaufstelle installieren, konfigurieren und betreiben.

---

## Inhaltsverzeichnis

1. [Installation (Docker Compose)](#1-installation-docker-compose)
2. [Erstkonfiguration](#2-erstkonfiguration)
   - 2.5 [Dokumentationstypen konfigurieren](#25-dokumentationstypen-konfigurieren)
   - 2.6 [Auswahloptionen verwalten](#26-auswahloptionen-verwalten-feldvorlagen)
3. [Backup und Wiederherstellung](#3-backup-und-wiederherstellung)
4. [Updates](#4-updates)
5. [Monitoring](#5-monitoring)
6. [Troubleshooting](#6-troubleshooting)
7. [DSGVO-Hinweise](#7-dsgvo-hinweise)
8. [Statistik-Snapshots](#8-statistik-snapshots)

---

## 1. Installation (Docker Compose)

### Voraussetzungen

- Docker Engine 24 oder neuer
- Docker Compose v2 (als Plugin: `docker compose`)
- Öffentlich erreichbarer Server mit DNS-Eintrag für Ihre Domain
- Ports 80 und 443 müssen von außen erreichbar sein

### Schritt 1: Dateien herunterladen

```bash
git clone https://github.com/anlaufstelle/app.git
cd anlaufstelle
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
ENCRYPTION_KEY=<fernet-schluessel>
```

**Verschlüsselungsschlüssel generieren:**

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Secret Key generieren:**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

> **Wichtig:** Speichern Sie `ENCRYPTION_KEY` und `DJANGO_SECRET_KEY` sicher (z. B. in einem Passwortmanager oder Secret-Management-System). Ohne den `ENCRYPTION_KEY` sind verschlüsselte Felddaten nicht mehr lesbar.

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

### 2.4 Weitere Benutzer anlegen

Im Admin unter **Core → Benutzer → Benutzer hinzufügen**:

- **Benutzername:** Anmeldename (keine Klarnamen)
- **Anzeigename:** Name für die Benutzeroberfläche
- **Rolle:** Eine der vier Rollen (siehe unten)
- **Einrichtung:** Zuweisung zu einer Einrichtung
- **Passwort muss geändert werden:** Empfohlen bei neuen Konten

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

> **Hinweis:** Die Kategorie wird auf der Statistik-Seite zur Gruppierung nach Dokumentationstyp verwendet. Für den Jugendamt-Export ist dagegen der **Systemtyp** maßgeblich (siehe unten).

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

#### Mindest-Kontaktstufe

Legt fest, welche Kontaktstufe ein Klientel mindestens haben muss, damit ein Ereignis dieses Typs erstellt werden kann. Beispiel: Beratungsgespräche erfordern mindestens „Qualifiziert", weil die Identität des Klientel bekannt sein muss.

### 2.6 Auswahloptionen verwalten (Feldvorlagen)

Unter **Core → Feldvorlagen** können Sie die Optionen von Auswahl- und Mehrfachauswahl-Feldern (Select / Multi-Select) bearbeiten. Die Optionen werden im Feld **Options json** als JSON-Array gespeichert.

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

---

## 8. Statistik-Snapshots

### Was sind Statistik-Snapshots?

Anlaufstelle berechnet Statistiken (Dashboard, Halbjahresberichte, Jugendamt-PDFs) standardmäßig live aus der Event-Tabelle. Wenn die automatische Datenlöschung (`enforce_retention`) alte Events entfernt, würden diese aus den Statistiken verschwinden.

**Statistik-Snapshots** sichern monatliche Aggregate, bevor Events gelöscht werden. Die Auswertungen nutzen eine Hybrid-Logik: gespeicherte Snapshots für vergangene Monate, Live-Daten für den aktuellen Monat.

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
