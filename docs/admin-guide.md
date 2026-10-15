# Anlaufstelle – Admin-Handbuch

Dieses Handbuch richtet sich an IT-Administratoren sozialer Einrichtungen, die Anlaufstelle installieren, konfigurieren und betreiben.

---

## Inhaltsverzeichnis

1. [Installation (Docker Compose)](#1-installation-docker-compose)
2. [Erstkonfiguration](#2-erstkonfiguration)
   - [Pflichtangaben (Impressum/Datenschutz) einrichten](#pflichtangaben-impressumdatenschutz-einrichten)
   - 2.2c [Einrichtung stilllegen: archivieren statt löschen](#22c-einrichtung-stilllegen-archivieren-statt-löschen)
   - 2.5 [Dokumentationstypen konfigurieren](#25-dokumentationstypen-konfigurieren)
   - 2.6 [Auswahloptionen verwalten](#26-auswahloptionen-verwalten-feldvorlagen)
   - 2.6b [Fuzzy-Suche (pg_trgm)](#26b-fuzzy-suche-pg_trgm)
   - 2.7 [Zwei-Faktor-Authentifizierung (2FA)](#27-zwei-faktor-authentifizierung-2fa)
   - 2.8 [Schnell-Vorlagen (Quick-Templates)](#28-schnell-vorlagen-quick-templates)
   - 2.9 [Encrypted File Vault & Virus-Scanning](#29-encrypted-file-vault--virus-scanning)
   - 2.10 [Offline-Modus & Streetwork (M6A)](#210-offline-modus--streetwork-m6a)
   - 2.11 [CSV-Import und -Export: Formelschutz](#211-csv-import-und--export-formelschutz)
   - 2.12 [Import-Assistent](#212-import-assistent)
3. [Backup und Wiederherstellung](#3-backup-und-wiederherstellung)
4. [Updates](#4-updates)
   - 4.1 [Veröffentlichungen verfolgen](#41-veröffentlichungen-verfolgen)
5. [Monitoring](#5-monitoring)
   - 5.4 [CSP-Debugging](#54-csp-debugging)
   - 5.5 [Compliance-Dashboard](#55-compliance-dashboard)
   - 5.6 [Error-Tracking & Datenminimierung](#56-error-tracking--datenminimierung)
   - 5.7 [Breach-Detection-Scan](#57-breach-detection-scan)
6. [Troubleshooting](#6-troubleshooting)
7. [DSGVO-Hinweise](#7-dsgvo-hinweise)
   - 7.8 [Optimistic Locking](#78-optimistic-locking)
   - 7.9 [Row Level Security (RLS)](#79-row-level-security-rls)
8. [Statistik-Snapshots & Materialized View](#8-statistik-snapshots--materialized-view)

---

## 1. Installation (Docker Compose)

### Voraussetzungen

**Für die Installation zwingend:**

- Docker Engine 24 oder neuer und Docker Compose v2 (als Plugin: `docker compose`)
- `git` und `openssl`
- Docker wird auch für den unten beschriebenen Container-Generator des Fernet-Schlüssels benötigt; Host-`python3` ist nur für den optionalen Alternativbefehl nötig
- Root-/`sudo`-Rechte zum Anlegen des Installationsverzeichnisses und für die Host-Betriebseinrichtung
- öffentlich erreichbarer Server; DNS-Einträge für `DOMAIN` **und** `www.<DOMAIN>` müssen auf ihn zeigen
- von außen erreichbare Ports 80 und 443

**Für den laufenden Betrieb zwingend:** `bash` (nicht nur `sh`), `tar`, `gzip`/`gunzip`, `curl` sowie ein installierter und laufender Cron-Dienst für das Host-Backup. Optional sind `jq` für JSON-Auswertung, `mail` für lokale Alarmmails und — je nach Off-Site-Ziel — `rclone`, `aws` oder `scp`.

#### Hardware

Voraussetzungen:

- Docker Engine 24 oder neuer (siehe oben)
- eine HTTPS-fähige Domain; die mitgelieferte `Caddyfile` holt das Zertifikat automatisch über Let's Encrypt ([Schritt 3](#schritt-3-caddyfile-prüfen))

**Prozessor und Speicherplatz:** Für CPU, Plattenplatz und Zahl der Nutzenden gibt es keine gemessenen Mindestwerte. 1 vCPU und 10 GB freier Speicherplatz für Images, Datenbank, Virensignaturen und Anhänge sind ein ungemessener Richtwert für den Einstieg, keine Zusage für eine bestimmte Last; die lokalen Sicherungen unter `BACKUP_HOST_DIR` kommen hinzu ([§ 3](#3-backup-und-wiederherstellung)). Prüfen Sie Auslastung und Plattenbelegung im Betrieb.

**Arbeitsspeicher:** `docker-compose.prod.yml` begrenzt jeden Dienst mit `mem_limit`: `clamav` 3 GB, `db` 2 GB, `web` 2 GB, `cron` 1 GB, `redis` 512 MB, `caddy` 512 MB und der nur kurz laufende Wartungsdienst `maintenance` 1 GB. Diese Werte sind Obergrenzen, keine Reservierung; der tatsächliche Bedarf hängt vor allem vom Virenscanner ab, der seine Signaturdatenbank im Arbeitsspeicher hält. Messen Sie den Verbrauch nach dem ersten Start mit `docker stats` ([§ 5.3](#53-container-status)) und planen Sie den Arbeitsspeicher des Hosts danach.

### Schritt 1: Dateien herunterladen

```bash
sudo install -d -o "$(id -u)" -g "$(id -g)" /opt/anlaufstelle
git clone https://github.com/anlaufstelle/app.git /opt/anlaufstelle
cd /opt/anlaufstelle
```

Das Verzeichnis `/opt/anlaufstelle` ist im gesamten Handbuch das **Installationsverzeichnis**. Verwenden Sie den vollständigen Repository- oder Release-Tarball: Neben `docker-compose.prod.yml` und `Caddyfile` benötigt der Produktionsstack insbesondere `.env.example`, die PostgreSQL-Initialisierung, den Cron-Fahrplan sowie die Setup- und Betriebsskripte in ihrer Verzeichnisstruktur.

### Schritt 2: Umgebungsvariablen konfigurieren

> **Quotierungsregel:** Die `.env` wird sowohl von Docker Compose gelesen als auch von den Host-Backup-Skripten als Shell-Datei geladen. Setzen Sie deshalb **jeden Wert in einfache Anführungszeichen**, zum Beispiel `TRUSTED_PROXY_CIDRS='203.0.113.0/24 198.51.100.0/24'`. So bleiben Leerzeichen und `$` literal und werden von beiden Wegen gleich gelesen. Die unten erzeugten Zufallswerte enthalten keine einfachen Anführungszeichen.

Erstellen Sie eine `.env`-Datei im selben Verzeichnis wie `docker-compose.prod.yml`:

```bash
cp .env.example .env   # falls vorhanden, sonst manuell anlegen
```

Minimale `.env` für den Produktionsbetrieb:

```dotenv
# Domain (muss DNS-Eintrag haben)
DOMAIN='anlaufstelle.meine-einrichtung.de'

# Django
DJANGO_SECRET_KEY='<langer-zufaelliger-string>'
DJANGO_AUDIT_HASH_KEY='<separater-langer-zufaelliger-string>'
DJANGO_SETTINGS_MODULE='anlaufstelle.settings.prod'
# localhost/127.0.0.1 noetig fuer den Container-Healthcheck
# (http://localhost:8000/health/) — prod.py baut ALLOWED_HOSTS rein aus dieser
# Variable, ohne localhost wirft /health/ einen DisallowedHost-400.
ALLOWED_HOSTS='anlaufstelle.meine-einrichtung.de,localhost,127.0.0.1'

# Datenbank (Drei-Rollen-Modell, Refs #902)
POSTGRES_DB='anlaufstelle'
POSTGRES_USER='anlaufstelle'                         # App-Rolle (Django-Runtime): NOSUPERUSER, NOBYPASSRLS
POSTGRES_PASSWORD='<mindestens-32-zeichen>'
POSTGRES_ADMIN_USER='anlaufstelle_admin'             # Admin-Rolle (Migrationen/Seed/Retention): NOSUPERUSER, BYPASSRLS
POSTGRES_ADMIN_PASSWORD='<mindestens-32-zeichen>'
POSTGRES_BOOTSTRAP_PASSWORD='<mindestens-32-zeichen>'  # postgres-Bootstrap-Superuser (nur Rollen-Setup)

# Feldverschlüsselung (Pflicht in Produktion)
# Empfohlen: Plural-Form für Rotation; der erste Key ist Write-Key, weitere sind Read-Only.
ENCRYPTION_KEYS='<fernet-schluessel-1>'
```

Erzeugen Sie für **jedes** der drei Datenbankpasswörter einen eigenen Wert mit mindestens 32 Zeichen; `change-me`-Platzhalter werden abgewiesen. Das folgende Kommando liefert 48 Zeichen und wird dreimal ausgeführt:

```bash
openssl rand -hex 24
```

**Verschlüsselungsschlüssel generieren (ohne Host-Python):**

```bash
docker run --rm --entrypoint python ghcr.io/anlaufstelle/app:latest \
  -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`latest` ist hier ein Beispielwert für einen tatsächlich veröffentlichten Tag. Ob ein Tag aktuell pullbar ist, zeigt `docker manifest inspect ghcr.io/anlaufstelle/app:<tag>` (Exit-Code 0 = veröffentlicht) oder die [GHCR-Paketseite](https://github.com/anlaufstelle/app/pkgs/container/app). Release-Tags werden als Image in der Registry veröffentlicht, auch solche, die auf GitHub als Pre-Release gekennzeichnet sind. Den in `docker-compose.prod.yml` unter [`APP_VERSION`](#vollständige-umgebungsvariablen-referenz) gewählten Stand finden Sie dort; ohne Registry-Zugriff weichen Sie auf das im folgenden Abschnitt lokal gebaute Image aus. Der Entrypoint-Override ist erforderlich. Falls auf dem Host optional `python3` samt Modul `cryptography` installiert ist, erzeugt folgender Alternativbefehl denselben Schlüssel:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Image lokal aus dem Dockerfile bauen (ohne Registry-Zugriff):**

Wer denselben Stand wie `docker-compose.prod.yml` ohne Registry-Zugriff verwenden möchte, baut das Image aus dem mitgelieferten `Dockerfile` im Installationsverzeichnis:

```bash
docker build -t ghcr.io/anlaufstelle/app:local .
```

Setzen Sie anschließend `APP_VERSION='local'` in der `.env` und wiederholen Sie den Verschlüsselungsschlüssel-Schritt mit dem lokal gebauten Tag:

```bash
docker run --rm --entrypoint python ghcr.io/anlaufstelle/app:local \
  -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`DJANGO_SECRET_KEY` und `DJANGO_AUDIT_HASH_KEY` brauchen je einen eigenen zufälligen Wert aus mindestens 64 Zeichen; das folgende Kommando liefert 64 Base64-Zeichen aus 48 Byte Zufall und wird für beide Schlüssel **getrennt** ausgeführt — derselbe Befehl steht auch als Kommentar in `.env.example`:

**Secret Key generieren:**

```bash
openssl rand -base64 48 | tr -d '\n'
```

**Audit-Hash-Key generieren:**

```bash
openssl rand -base64 48 | tr -d '\n'
```

Der `DJANGO_AUDIT_HASH_KEY` sichert die Audit-Hashes — pseudonymisierte Einträge (z. B. E-Mails in Passwort-Reset-Audits) ebenso wie die HMAC-Integritätskette des Audit-Logs — und wird bewusst getrennt vom `DJANGO_SECRET_KEY` gehalten (siehe [`docs/disaster-recovery.md`](https://github.com/anlaufstelle/app/blob/main/docs/disaster-recovery.md)).

> **Wichtig:** Speichern Sie `ENCRYPTION_KEYS` und `DJANGO_SECRET_KEY` sicher (z. B. in einem Passwortmanager oder Secret-Management-System). Ohne die Schlüssel sind verschlüsselte Felddaten nicht mehr lesbar.

#### Vollständige Umgebungsvariablen-Referenz

Die folgenden Tabellen dokumentieren App-Settings und Betriebsvariablen (siehe [`src/anlaufstelle/settings/base.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py) und [`prod.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/prod.py); `base.py`/`prod.py` bleiben die maßgebliche Quelle).

**App-Setting vs. durchgereichte Einstellung:** Der `web`-Dienst lädt die `.env` bewusst nicht vollständig, sondern erhält die mit #2219 ausgelieferte explizite Allowlist. `maintenance` und `cron` laden dagegen die ganze Datei. Die Tabelle beschreibt den ausgelieferten Stand:

| Variable(n) | Stand |
|---|---|
| `DJANGO_SECRET_KEY`, `DJANGO_AUDIT_HASH_KEY`, `ALLOWED_HOSTS`, `EXTERNAL_ORIGIN`, `TRUSTED_PROXY_HOPS`, `TRUSTED_PROXY_CIDRS` | aus `.env` an `web` durchgereicht |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_PORT` | aus `.env` an `web` durchgereicht; die App-Rolle bleibt RLS-beschränkt |
| `ENCRYPTION_KEYS`/`ENCRYPTION_KEY`, `CLAMAV_ENABLED`, `CLAMAV_PORT`, `CLAMAV_TIMEOUT` | aus `.env` an `web` durchgereicht |
| `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE`, `EMAIL_*`, `DEFAULT_FROM_EMAIL` | aus `.env` an `web` durchgereicht |
| `SUPPORT_CONTACT` | aus `.env` an `web` durchgereicht; leer = allgemeiner Verweis auf die Anwendungsbetreuung der Einrichtung auf der Serverfehlerseite |
| `DJANGO_SETTINGS_MODULE`, `POSTGRES_HOST`, `CLAMAV_HOST` | `DJANGO_SETTINGS_MODULE` und `POSTGRES_HOST` sind in `docker-compose.prod.yml` fest auf `anlaufstelle.settings.prod` und `db` gesetzt; `CLAMAV_HOST` ist über `.env` konfigurierbar |
| `MEDIA_ROOT`, `BACKUP_DIR`, `GUNICORN_WORKERS`, `GUNICORN_TIMEOUT`, `REDIS_URL`, `SOURCE_CODE_URL`, `SOURCE_CODE_VERSION`, `DJANGO_AUDIT_HWM_PATH`, `DJANGO_HEALTH_BACKUP_REQUIRED`, `DJANGO_HEALTH_DISK_REQUIRED` | aus `.env` an `web` durchgereicht; ohne Eintrag gilt der Compose-Fallback aus der Spalte „Default“ der Tabellen unten |
| `REDIS_PASSWORD` | `redis` (Passwortschutz); Compose bettet den Wert in den `REDIS_URL`-Fallback von `web`, `cron` und `maintenance` ein, `web` erhält die Variable selbst nicht |
| `APP_VERSION`, `APP_IMAGE_DIGEST` | wählen nur das Image (`image:` in `docker-compose.prod.yml`); sie erreichen `web` nicht |
| `ANLAUFSTELLE_PROCESS_ROLE`, `DJANGO_LOAD_DOTENV`, `POSTGRES_APP_USER`, `FILE_UPLOAD_TEMP_DIR` | intern: in `docker-compose.prod.yml` fest gesetzt (`POSTGRES_APP_USER` aus `POSTGRES_USER` abgeleitet); nicht über `.env` steuern |
| `DJANGO_HEALTH_DETAIL_TOKEN`, `LOG_FORMAT`, `FILE_VAULT_MAX_UPLOAD_BYTES`, `FILE_VAULT_MAX_UPLOAD_FILES`, `FILE_VAULT_MAX_IMAGE_PIXELS`, `FILE_VAULT_MAX_ARCHIVE_BYTES`, `FILE_VAULT_MAX_ARCHIVE_RATIO`, `FILE_VAULT_MAX_ARCHIVE_ENTRIES`, `FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB`, `MAINTENANCE_FLAG_FILE`, `MAINTENANCE_ALLOW_IPS`, `MAINTENANCE_CACHE_TTL`, `MAINTENANCE_RETRY_AFTER`, `SUDO_MODE_TTL_SECONDS`, `DJANGO_INVITE_TOKEN_TIMEOUT`, `DJANGO_PASSWORD_RESET_TIMEOUT` | aus `.env` an `web` durchgereicht |
| `MFA_ENFORCED_ROLES_IMMEDIATE`, `MFA_ENFORCED_ROLES_GRACE`, `OTP_WEBAUTHN_RP_ID`, `OTP_WEBAUTHN_ALLOWED_ORIGINS`, `DJANGO_AUDIT_DETAIL_STRICT`, `OFFLINE_REVOCATION_SESSION_SCAN_LIMIT`, `REPORTING_BACKEND_STATEMENT_TIMEOUT_MS` | aus `.env` an `web` durchgereicht; ohne Eintrag in `.env` gelten die Defaults der Anwendung, eine ausdrücklich leere Rollenliste bleibt leer |
| `POSTGRES_ADMIN_USER`, `POSTGRES_ADMIN_PASSWORD`, `POSTGRES_BOOTSTRAP_PASSWORD` | nur `maintenance`/Initialisierung, bewusst **nicht** `web` |
| `BREACH_*` | `cron` und `maintenance` (über `env_file`) |
| `TZ` | `cron` (Compose-Fallback `Europe/Berlin`; Zeitzone des Cron-Fahrplans) und `maintenance` (über `env_file`); `web` erhält `TZ` nicht — die Anwendung rechnet unabhängig davon mit `TIME_ZONE = "Europe/Berlin"` |
| `BACKUP_HOST_DIR` | Host-Skripte und Volume-Mount; `BACKUP_DIR` ist das read-only Ziel in `web` |
| `LEGAL_IMPRINT_FILE`, `LEGAL_PRIVACY_FILE` | `web` nur mit dem zusätzlichen `compose.legal.yml`, wie unter [Pflichtangaben](#pflichtangaben-impressumdatenschutz-einrichten) gezeigt |

> Alle in dieser Tabelle `web` zugeordneten Variablen erreichen den Dienst über die ausgelieferte Allowlist; `APP_VERSION`/`APP_IMAGE_DIGEST` wirken nur auf die Image-Auswahl, `BACKUP_HOST_DIR` wirkt über Host-Skripte und Volume-Mount, `LEGAL_IMPRINT_FILE`/`LEGAL_PRIVACY_FILE` nur mit dem zusätzlichen `compose.legal.yml`. Eine `compose.override.yml` ist nur für zusätzliche, hier nicht genannte Variablennamen nötig.

Setzen Sie Konfigurationswerte in `.env` und erstellen Sie `web` mit dem vollständigen **Dateisatz** neu. Sobald ein Compose-Aufruf mindestens ein ausdrückliches `-f` enthält, lädt Compose `compose.override.yml` nicht automatisch. Deshalb bauen alle folgenden Recreate-Befehle die Liste in der festen Reihenfolge `docker-compose.prod.yml`, `compose.legal.yml` (falls eingerichtet), `compose.override.yml` (falls eingerichtet) auf:

```bash
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d --force-recreate web
```

**Django & Hosts**

| Name | Default | Beschreibung |
|---|---|---|
| `DJANGO_SECRET_KEY` | — (Pflicht in prod) | Signierung von Sessions/CSRF. Mit `openssl rand -base64 48 \| tr -d '\n'` generieren. |
| `DJANGO_AUDIT_HASH_KEY` | — (Pflicht in prod) | HMAC-Schlüssel für die Audit-Hashes: pseudonymisierte Einträge (z. B. Passwort-Reset-E-Mails) und die Integritätskette des Audit-Logs. Bewusst separat vom `DJANGO_SECRET_KEY` — ohne eigenen Schlüssel fiele der Audit-Hash sonst still auf `SHA256(SECRET_KEY)` zurück, was ein Secret-Key-Leak rückwirkend ausnutzbar machen würde; daher fail-closed (ohne Wert verweigert die App in Produktion den Start). Separat mit `openssl rand -base64 48 \| tr -d '\n'` generieren. Siehe [`docs/disaster-recovery.md`](https://github.com/anlaufstelle/app/blob/main/docs/disaster-recovery.md). |
| `DJANGO_SETTINGS_MODULE` | — | In Produktion `anlaufstelle.settings.prod`. |
| `ALLOWED_HOSTS` | — (Pflicht in prod) | Komma-separierte Hostnamen, z. B. `anlaufstelle.example.de`. |
| `EXTERNAL_ORIGIN` | abgeleitet aus `ALLOWED_HOSTS` | Feste Basis aller sicherheitsrelevanten Links — Einladungs-Setup-Link, Passwort-Reset-Link und Entsperr-Link. Format `https://host[:port]`, ohne Pfad und ohne abschließenden Schrägstrich; ein Wert mit Pfadanteil oder ohne Schema wird beim Start abgelehnt. Diese Links entstehen dadurch **nicht** mehr aus dem `Host`-Header des eingehenden Requests. Führt `ALLOWED_HOSTS` genau **einen** öffentlichen Hostnamen, leitet die Anwendung `https://<host>` selbst ab und die Variable kann entfallen — `localhost`, `127.0.0.1` und `::1` zählen dabei nicht mit, sie stehen dort nur für den Container-Healthcheck. Bei mehreren öffentlichen Hosts oder ausschließlich Subdomain-Mustern (`.example.de`) ist sie **Pflicht** — sonst verweigert die Anwendung den Start (#1967). |
| `TRUSTED_PROXY_HOPS` | `1` | Anzahl vertrauenswürdiger Proxies vor der App (X-Forwarded-For-Auswertung). `0` = kein Proxy, `1` = nur Caddy, `2` = CDN + Caddy. Ein Wert über `1` wirkt **nur zusammen mit** `TRUSTED_PROXY_CIDRS` — sonst sieht die Anwendung nie mehr als einen Eintrag und fällt auf die Proxy-IP zurück (#1963). |
| `TRUSTED_PROXY_CIDRS` | leer | Adressbereiche der Proxys, denen der Reverse-Proxy ein eingehendes `X-Forwarded-For` glauben darf — **nur** durch Leerzeichen getrennt (Kommas sind kein Trennzeichen, Netzmasken keine gültige Schreibweise), als CIDR (`203.0.113.0/24`, `2001:db8::/32`), als einzelne Adresse oder mit dem Schlüsselwort `private_ranges`. Leer lassen, solange Caddy der einzige Proxy ist: Caddy verwirft dann jeden vom Client mitgeschickten Wert und setzt den Header selbst. Vor einem CDN oder Loadbalancer ist die Variable **Voraussetzung** für `TRUSTED_PROXY_HOPS > 1`; derselbe Wert füllt die Caddy-Konfiguration und die Anwendung. Ein unbrauchbarer Bereich bricht den Start ab (#1963). |
| `DJANGO_HEALTH_DETAIL_TOKEN` | — | Token für den Header `X-Health-Token`; berechtigt Monitoring-Caller, die Detailfelder von `/health/` (`database`, `virus_scanner`/`clamav`, `encryption_key`, `db_user_is_superuser`/`db_role_posture`, `version`, `cache`, `smtp`, `last_backup_age_hours`/`backup`/`backup_required`, `disk_free_pct`/`disk`/`disk_required`, `stale_jobs`) abzurufen. Ohne Token liefert `/health/` nur `{"status": …}` (siehe [§ 5.1](#51-health-endpoint), #1375). |

**Datenbank (PostgreSQL — Drei-Rollen-Modell, #902)**

Anlaufstelle trennt drei DB-Rollen: einen hartkodierten `postgres`-Bootstrap-Superuser (legt beim ersten Start nur die App-Rollen an), die **App-Rolle** (`POSTGRES_USER`, `NOSUPERUSER NOBYPASSRLS` — Django-Runtime, RLS-geschützt) und die **Admin-Rolle** (`POSTGRES_ADMIN_USER`, `NOSUPERUSER BYPASSRLS` — Migrationen, Seed, Retention-Pruning). Die `.env`-Variablen `POSTGRES_ADMIN_USER`, `POSTGRES_ADMIN_PASSWORD` und `POSTGRES_BOOTSTRAP_PASSWORD` sind Pflicht.

| Name | Default | Beschreibung |
|---|---|---|
| `POSTGRES_DB` | — | Datenbankname (Pflicht: Compose reicht den Wert ohne Fallback durch; Beispiel in `.env.example`: `anlaufstelle`). |
| `POSTGRES_USER` | — | App-Rolle (Django-Runtime), `NOSUPERUSER NOBYPASSRLS` (Pflicht: Compose reicht den Wert ohne Fallback durch; Beispiel in `.env.example`: `anlaufstelle`). |
| `POSTGRES_PASSWORD` | — | Passwort der App-Rolle; Pflicht, in Produktion mindestens 32 Zeichen und kein `change-me`-Platzhalter. |
| `POSTGRES_ADMIN_USER` | — | Admin-Rolle für Migrationen/Seed/Retention, `NOSUPERUSER BYPASSRLS` (Pflicht, ohne Standardwert; Beispielwert in `.env.example`: `anlaufstelle_admin`). |
| `POSTGRES_ADMIN_PASSWORD` | — | Passwort der Admin-Rolle; mindestens 32 Zeichen und kein `change-me`-Platzhalter. |
| `POSTGRES_BOOTSTRAP_PASSWORD` | — | Passwort des `postgres`-Bootstrap-Superusers (nur initiales Rollen-Setup); mindestens 32 Zeichen und kein `change-me`-Platzhalter. |
| `POSTGRES_HOST` | `localhost` (via Compose: `db`) | DB-Host. |
| `POSTGRES_PORT` | `5432` | DB-Port. |

> **Tipp:** `docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles` verifiziert die Rollen-Topologie zur Laufzeit (Exit `0` = ok, `1` = falsches Attributprofil, `2` = Konfiguration unvollständig). Das [Compliance-Dashboard](#55-compliance-dashboard) nutzt dieselbe Prüfung, [`scripts/initial-setup.sh`](../scripts/initial-setup.sh) führt sie bei der Erstinbetriebnahme aus. Ergänzend prüft der [Health-Endpoint](#51-health-endpoint) fortlaufend die **tatsächlich verwendete** Verbindung (`db_role_posture`) — `check_db_roles` prüft, dass die Rollen mit den richtigen Attributen *existieren*, nicht, dass die Anwendung auch als die App-Rolle verbindet (Refs #793).

**Feldverschlüsselung (MultiFernet-Rotation)**

| Name | Default | Beschreibung |
|---|---|---|
| `ENCRYPTION_KEYS` | — | Komma-separierte Liste von Fernet-Keys. Der **erste** Key ist Write-Key (neue Daten werden damit verschlüsselt), alle weiteren sind Read-Only (Decrypt-Fallback für Rotation). |
| `ENCRYPTION_KEY` | — | Einzelschlüssel-Betrieb (Alternative zu `ENCRYPTION_KEYS`); **abgekündigt**, Schlüsselring bevorzugt. Mindestens eine der beiden Variablen muss in Produktion gesetzt sein, sonst verweigert die App den Start. |

**Einzelschlüssel → Schlüsselring migrieren:** Ist nur `ENCRYPTION_KEY` gesetzt, meldet `python manage.py check --deploy` die Warnung `core.W005` (#2240). Der Einzelschlüssel funktioniert unverändert weiter; nach Version 1.0 entfällt er, dann ist `ENCRYPTION_KEYS` die einzige unterstützte Variable. Umstellen, ohne Daten neu zu verschlüsseln:

1. Den bisherigen Wert von `ENCRYPTION_KEY` unverändert als ersten und einzigen Eintrag in `ENCRYPTION_KEYS` übernehmen: `ENCRYPTION_KEYS=<bisheriger-wert>`.
2. `ENCRYPTION_KEY` aus der `.env` entfernen.
3. Den Webdienst mit dem vollständigen Compose-Dateisatz (wie oben in diesem Abschnitt) neu erstellen und `python manage.py check --deploy` im Web-Container ausführen — `core.W005` erscheint nicht mehr.

Derselbe Schlüssel entschlüsselt danach alle vorhandenen Daten; eine spätere Rotation folgt dem Abschnitt „Schlüssel-Rotation (`ENCRYPTION_KEYS`)“.

**Virus-Scan (ClamAV, #524)**

| Name | Default (prod) | Beschreibung |
|---|---|---|
| `CLAMAV_ENABLED` | `true` | Aktiviert den Virenscan vor Upload-Verschlüsselung. Fail-closed: Ist der Daemon nicht erreichbar, wird der Upload abgewiesen. `false` ist ausschließlich für Entwicklung und Tests zulässig, nicht für Produktion. |
| `CLAMAV_HOST` | `clamav` | Hostname des ClamAV-Daemons (Service-Name im Compose-Netzwerk). Ein externer Scanner wird über `CLAMAV_HOST` und `CLAMAV_PORT` konfiguriert; `CLAMAV_HOST` wird über `.env` durchgereicht. |
| `CLAMAV_PORT` | `3310` | TCP-Port des clamd-Sockets. |
| `CLAMAV_TIMEOUT` | `15` | Wall-Clock-Timeout in Sekunden pro Scan-Aufruf; MUSS unter `GUNICORN_TIMEOUT` liegen, sonst killt der Worker-Timeout den Request statt fail-closed abzuweisen. |

**Logging**

| Name | Default | Beschreibung |
|---|---|---|
| `LOG_FORMAT` | `text` | `json` aktiviert strukturiertes Logging über [`core.logging.JsonFormatter`](https://github.com/anlaufstelle/app/blob/main/src/core/logging.py) — empfohlen für Produktion mit Log-Aggregation. |

**Sentry (optional)**

| Name | Default | Beschreibung |
|---|---|---|
| `SENTRY_DSN` | — | Aktiviert das Error-Tracking (opt-in — ohne DSN verlässt keinerlei Telemetrie die Installation). Gesetzt werden `send_default_pii=False` und `include_local_variables=False`; ein serverseitiger Scrubber entfernt Request-Bodies/Cookies/Query-Strings und maskiert sensible Felder, bevor ein Event die Installation verlässt. Mechanismus **und Grenzen** siehe [§ 5.6 Error-Tracking & Datenminimierung](#56-error-tracking--datenminimierung). |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.1` | Sample-Rate für Performance-Traces (0.0–1.0). |

**E-Mail (SMTP, Produktion)**

| Name | Default | Beschreibung |
|---|---|---|
| `EMAIL_HOST` | Compose: leer (Settings ohne Variable: `localhost`) | SMTP-Host für Password-Reset- und Invite-Mails. `docker-compose.prod.yml` reicht ohne Eintrag in `.env` einen **leeren** Wert durch: Dann scheitert jeder Mailversand, und `/health/` meldet im Feld `smtp` den Wert `disabled`, ohne den Gesamtstatus zu senken. Für den Betrieb also immer setzen. |
| `EMAIL_PORT` | `587` | SMTP-Port. |
| `EMAIL_HOST_USER` | — | SMTP-Benutzer. |
| `EMAIL_HOST_PASSWORD` | — | SMTP-Passwort. |
| `EMAIL_USE_TLS` | `True` | STARTTLS auf dem konfigurierten Port aushandeln (typisch Port 587). |
| `EMAIL_USE_SSL` | `False` | Implizites TLS/SMTPS (typisch Port 465). Schließt `EMAIL_USE_TLS` aus — beide zugleich auf `True` lässt die Anwendung bewusst nicht starten. |
| `DEFAULT_FROM_EMAIL` | `noreply@anlaufstelle.app` | Absenderadresse. |

Seit Django 6.1 konfiguriert die Anwendung den Mailversand intern über
Djangos `MAILERS`-Einstellung statt über die früheren `EMAIL_*`-Einstellungen.
**Für den Betrieb ändert sich nichts:** die oben genannten Umgebungsvariablen
heißen unverändert so und werden weiterhin aus der `.env` gelesen — eine
bestehende `.env` bleibt ohne Anpassung gültig.

**Django 6.1 setzt auf Produktivhosts ein SMTP-Backend voraus** (#2326):
Der Deploy-Check `python manage.py check --deploy`, den der Web-Container bei
jedem Start ausführt, bricht mit dem Fehler `mail.E001` ab, sobald der
Standard-Mailer ein reines Entwicklungs-Backend ist (Konsole, Datei,
Arbeitsspeicher, Dummy). Die Produktiv-Einstellungen verwenden immer das
SMTP-Backend; der Start hängt also nicht davon ab, dass ein Relay erreichbar
ist. **Ohne erreichbaren SMTP-Server** startet die Anwendung, aber jede
Einladungs- und Passwort-Reset-Mail scheitert beim Versand: In
**Verwaltung → Zugänge** erscheint die Warnung, dass die Einladung nicht
versendet werden konnte, `/health/` meldet im Feld `smtp` den Wert
`unreachable` (bei leerem `EMAIL_HOST` `disabled`), und es bleibt nur der technische Fallback über ein
Initialpasswort (siehe „SMTP-Voraussetzungen und Fehlersuche" im
[Token-Invite-Flow](#token-invite-flow-refs-528)). Ein Konsolen-Backend, das
Mails nur ins Container-Log schreibt, ist ausschließlich in den Live-Dev- und
Demo-Einstellungen vorgesehen und dort an ein leeres `EMAIL_HOST` gebunden.

**File-Vault Upload-Limits (#1268)**

Service-Layer-Obergrenzen als Defense-in-Depth (greifen unabhängig von der per-Einrichtung konfigurierbaren `Settings.max_file_size_mb`).

| Name | Default | Beschreibung |
|---|---|---|
| `FILE_VAULT_MAX_UPLOAD_BYTES` | `52428800` (50 MB) | Harte Obergrenze pro Datei, greift vor jeder Voll-Pufferung. |
| `FILE_VAULT_MAX_UPLOAD_FILES` | `20` | Maximale Datei-Anzahl pro File-Feld (Masse-Upload-Bremse). |
| `FILE_VAULT_MAX_IMAGE_PIXELS` | `40000000` | Maximale Gesamtpixelzahl über alle Frames/Seiten eines Rasterbild-Uploads (setzt zugleich `PIL.Image.MAX_IMAGE_PIXELS`); zusätzlich gelten feste Grenzen von 100 Frames/Seiten und ein geschätztes Bildpufferbudget von 256 MiB. |
| `FILE_VAULT_MAX_ARCHIVE_BYTES` | `209715200` (200 MB) | Zip-Bomb-Schutz: maximale Summe der unkomprimierten Einträge (aus dem Zip-Directory gelesen). |
| `FILE_VAULT_MAX_ARCHIVE_RATIO` | `100` | Zip-Bomb-Schutz: maximales Expansions-Verhältnis (unkomprimiert/komprimiert). |
| `FILE_VAULT_MAX_ARCHIVE_ENTRIES` | `10000` | Zip-Bomb-Schutz: maximale Anzahl Einträge (aus dem EOCD-Record gelesen). |
| `FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB` | `2048` (2 GB) | Betreiber-Backstop für das **Speicherkontingent je Einrichtung** (#2129) — keine Per-Datei-Grenze, sondern die Summe aller Anhänge einer Einrichtung inklusive Papierkorb. `0` schaltet den Backstop ab. Siehe [Speicherkontingente](#speicherkontingente). |

**Wartungsmodus (#700)**

| Name | Default | Beschreibung |
|---|---|---|
| `MAINTENANCE_FLAG_FILE` | `/media/anlaufstelle.maintenance` (Prod-Compose) | Beschreibbarer, persistenter Container-Pfad der Flag-Datei; solange sie existiert, antwortet die App mit HTTP 503. Ein eigener Wert in `.env` muss ebenfalls im Container erreichbar sein; bestehende Host-Pfade entfernen oder auf einen persistenten Container-Pfad umstellen. |
| `MAINTENANCE_ALLOW_IPS` | — | Komma-separierte IP-Liste für Ops-Zugriff während der Wartung. |
| `MAINTENANCE_CACHE_TTL` | `5` | Caching-Dauer (Sekunden) der Flag-Datei-Prüfung. |
| `MAINTENANCE_RETRY_AFTER` | `600` | Wert des `Retry-After`-Headers (Sekunden) bei aktivem Wartungsmodus. |

**Breach-Detection (#685)**

| Name | Default | Beschreibung |
|---|---|---|
| `BREACH_*` (Variablen-Familie) | siehe Runbook | Zeitfenster und Schwellen der heuristischen Anomalie-Erkennung (fehlgeschlagene Logins, Exporte, Löschungen, Langzeitfenster, verteilte Login-Angriffe) sowie `BREACH_NOTIFICATION_WEBHOOK_URL` (optionaler Eskalations-Webhook; leer = aus). Vollständige Liste samt Defaults: [Ops-Runbook § 6.5b](ops-runbook.md#65b-dsgvo-art-3334--breach-notification-refs-685). |

**Zwei-Faktor-Authentifizierung und Passkeys (siehe [§ 2.7](#27-zwei-faktor-authentifizierung-2fa))**

| Name | Default | Beschreibung |
|---|---|---|
| `MFA_ENFORCED_ROLES_IMMEDIATE` | `super_admin,facility_admin` | Kommaliste der Rollen, für die 2FA sofort verpflichtend ist. Leer = diese Ebene aus. |
| `MFA_ENFORCED_ROLES_GRACE` | `lead,staff,assistant` | Kommaliste der Rollen, für die 2FA nach einer Übergangsfrist von 30 Tagen ab der ersten Anmeldung verpflichtend ist. Leer = diese Ebene aus. Ein unbekannter Rollenname oder eine Rolle in beiden Listen bricht den Start mit `ImproperlyConfigured` ab. |
| `OTP_WEBAUTHN_RP_ID` | erster konkreter Host aus `ALLOWED_HOSTS` (sonst die Basis-Domain eines Wildcard-Eintrags) | Domain, an die Passkeys gebunden sind. Eine spätere Änderung macht registrierte Passkeys unbrauchbar. |
| `OTP_WEBAUTHN_ALLOWED_ORIGINS` | `https://<host>` für jeden konkreten Host aus `ALLOWED_HOSTS` | Kommaliste der zulässigen Ursprünge für Passkey-Prüfungen. Bei reinen Wildcard-Einträgen in `ALLOWED_HOSTS` Pflicht. |

**Sonstiges**

| Name | Default | Beschreibung |
|---|---|---|
| `MEDIA_ROOT` | Compose: `/media/media` (Settings ohne Variable: `<BASE_DIR>/media`) | Ablage verschlüsselter Dateianhänge (siehe [§ 2.9](#29-encrypted-file-vault--virus-scanning)). Im Stack liegt `/media` auf dem persistenten Volume `media`; ein eigener Wert muss unter `/media` liegen, denn außerhalb davon ist das Container-Dateisystem schreibgeschützt (`/tmp` ist flüchtiger Arbeitsspeicher). |
| `FILE_UPLOAD_TEMP_DIR` | Django-Default (`tempfile.gettempdir()`) | Verzeichnis für den Klartext-Zwischenpuffer von Uploads > `FILE_UPLOAD_MAX_MEMORY_SIZE`, bevor sie in den File-Vault verschlüsselt werden (siehe [§ 2.9](#29-encrypted-file-vault--virus-scanning)). In den mitgelieferten Compose-Dateien fest auf `/tmp` (tmpfs/RAM) gesetzt — [Ops-Runbook § 5.7a](ops-runbook.md). |
| `BACKUP_DIR` | `/backups` (Compose-Stack), sonst `<Repo>/backups` | Read-only Mount-Ziel im Container; Health-Endpoint und Backup-Zeile im Systembereich lesen daraus das Backup-Alter. Die Host-Skripte ignorieren diese Variable. |
| `BACKUP_HOST_DIR` | `./backups` | Basisverzeichnis auf dem Host; relative Werte werden gegen den Repository-Root aufgelöst. `backup.sh` schreibt darunter nach `daily/`, `weekly/` und `monthly/`; `restore-drill.sh` liest aus `daily/`. Compose mountet dieses Verzeichnis read-only nach `BACKUP_DIR`, damit der Container dieselben Dateien sieht. |
| `APP_VERSION` | Compose-Fallback: zuletzt gepinnter Release-Tag | Wählt den Release-Tag des App-Images, das `docker-compose.prod.yml` verwendet (z. B. `v1.2.3`). Ob ein Tag veröffentlicht ist, prüft `docker manifest inspect ghcr.io/anlaufstelle/app:<tag>` (Exit-Code 0 = veröffentlicht) oder die [GHCR-Paketseite](https://github.com/anlaufstelle/app/pkgs/container/app). Die Variable erreicht den `web`-Container nicht; `/health/` weist im `version`-Detailfeld (nur für Token-/authentifizierte Caller) den beim Image-Bau eingebackenen Wert aus, bei einem lokal gebauten Image `dev`. |
| `APP_IMAGE_DIGEST` | leer | Bindet das Image zusätzlich an einen unveränderlichen Digest; Schreibweise mit führendem `@`, z. B. `@sha256:…`. Compose hängt den Wert unverändert an den Tag aus `APP_VERSION` an (Ops-Runbook § 8.4a). |
| `GUNICORN_WORKERS` | `3` | Anzahl der Gunicorn-Worker im `web`-Dienst (gelesen von `docker-entrypoint.sh`). |
| `GUNICORN_TIMEOUT` | `30` | Worker-Timeout in Sekunden; muss größer als `CLAMAV_TIMEOUT` sein. |
| `REDIS_URL` | Compose: `redis://redis:6379/1` (mit gesetztem `REDIS_PASSWORD`: `redis://:<passwort>@redis:6379/1`) | Gemeinsamer Cache für Rate-Limits und Health-Detail-Caching. Ein eigener Wert zeigt auf ein externes Redis; ein ausdrücklich leerer Wert (`REDIS_URL=`) schaltet auf den Datenbank-Cache um. |
| `REDIS_PASSWORD` | leer | Passwort des mitgelieferten `redis`-Dienstes; nur URL-sichere Zeichen. Leer meldet `check --deploy` die Warnung `core.W004` (siehe [Schritt 4](#schritt-4-stack-starten)). |
| `SOURCE_CODE_URL` | `https://github.com/anlaufstelle/app` | Quellcode-Link in der Fußzeile (AGPL § 13). Forks und angepasste Installationen tragen hier ihren eigenen Quellcode-Spiegel ein. |
| `PROJECT_WEBSITE_URL` | `https://anlaufstelle.app` | Projektlink in der Demo und nach vollständiger Anmeldung sowie in der Hilfe; leer oder ein Schema außer `http`/`https` blendet ihn aus. Als Umgebungsvariable des App-Prozesses setzen. |
| `SOURCE_CODE_VERSION` | leer | Optionaler Stand (z. B. Commit-SHA) für den Versionsstempel der DSGVO-Dokumente; die Fußzeile zeigt weiterhin den Quellcode-Link, aber keine Source-Version. |
| `DJANGO_AUDIT_HWM_PATH` | Compose/`.env.example`: `/data/audit_hwm.json`; Settings ohne Variable: leer | Signierter Audit-Anker im eigenen Volume `audit-anchor`: `web` nur lesend, `cron`/`maintenance` beschreibbar. Leer oder nicht gesetzt verwendet im Stack den Default; ein eigener Pfad muss auf diesem Volume liegen. `backup.sh` bricht bei fehlendem, leerem oder unlesbarem Anker ab ([Ops-Runbook § 6.7](ops-runbook.md#67-audit-high-water-mark-sichern-und-pruefen-refs-2022)). |
| `DJANGO_HEALTH_BACKUP_REQUIRED` | `true` | `true`: ein fehlendes Backup-Signal senkt `/health/` auf `degraded`. `false` nur für Instanzen ohne Host-Backup; ein veraltetes Backup senkt den Status weiterhin. |
| `DJANGO_HEALTH_DISK_REQUIRED` | `true` | `true`: nicht messbarer freier Speicher auf `MEDIA_ROOT` senkt `/health/` auf `degraded`. `false` nur für Instanzen ohne messbaren `MEDIA_ROOT`; knapper Speicher senkt den Status weiterhin. |
| `SUDO_MODE_TTL_SECONDS` | `900` | Gültigkeitsdauer (Sekunden) des Re-Auth-Fensters für sensible Aktionen: 2FA deaktivieren, erneute 2FA-Einrichtung bei bestehendem Faktor, Passkey hinzufügen oder entfernen, Personen-Datenexport (JSON/PDF), **Verwaltung → Zugänge** (anlegen, bearbeiten, Einladung senden, entsperren, zweiten Faktor zurücksetzen), **Einstellungen** der Einrichtung, die Verwaltungsoberfläche `/admin-mgmt/` sowie im Systembereich Audit-Log-Export, Aufheben von Sperren und Wartungsmodus. |
| `DJANGO_AUDIT_DETAIL_STRICT` | `true` (Produktion) | `true` weist nicht freigegebene Audit-Detailschlüssel zurück; ein explizites `false` schreibt solche Schlüssel mit ERROR-Meldung. Die Wertprüfung für `recipient`/`purpose` bleibt unabhängig davon aktiv: höchstens 200 Zeichen, keine erkannten Kontaktdaten oder Namenspaare. |
| `OFFLINE_REVOCATION_SESSION_SCAN_LIMIT` | `10000` | Höchstzahl der nicht abgelaufenen Sitzungen der ganzen Installation, die durchsucht werden, wenn eine Anmeldung gegen eine Sperre, ein Rollenwechsel, eine Deaktivierung oder der erneute Einladungsversand an einen Zugang mit Initialpasswort die Sitzungen eines Kontos beendet. Erreicht die Suche die Grenze, bricht sie ab und meldet das im Log auf Stufe ERROR. Was mit nicht gefundenen Sitzungen des Kontos geschieht, hängt vom Anlass ab: Nach einer Anmeldung gegen eine Sperre und nach einem Rollenwechsel bleiben sie angemeldet (nach dem Rollenwechsel mit der neuen Rolle), bis sie durch Inaktivität oder die maximale Sitzungsdauer enden. Nach einer Deaktivierung und nach dem erneuten Einladungsversand, der das Initialpasswort unbrauchbar macht, enden sie trotzdem bei ihrer nächsten Anfrage, weil die Anmeldung ein inaktives Konto bzw. einen geänderten Passwort-Hash nicht mehr annimmt. Das Zurücksetzen des zweiten Faktors durchsucht keine Sitzungen und hängt von diesem Wert nicht ab. |
| `REPORTING_BACKEND_STATEMENT_TIMEOUT_MS` | `10000` | Zeitlimit (Millisekunden) je Datenbankabfrage eines Berichts-Backends; `0` schaltet das Limit ab. |

### Schritt 3: Caddyfile prüfen

Die mitgelieferte `Caddyfile` übernimmt automatisch TLS-Zertifikate via Let's Encrypt und enthält zwei Site-Blöcke:

```
{$DOMAIN} {
    reverse_proxy web:8000
    ...
}

www.{$DOMAIN} {
    redir https://{$DOMAIN}{uri} permanent
}
```

Keine Anpassung nötig, solange `DOMAIN` in der `.env` gesetzt ist und DNS sowohl für `DOMAIN` als auch für `www.<DOMAIN>` auf diesen Server zeigt.

### Schritt 4: Stack starten

**Empfohlen — ein Befehl:** Das mitgelieferte Skript [`scripts/initial-setup.sh`](../scripts/initial-setup.sh) führt die komplette Erstinbetriebnahme in der richtigen Reihenfolge aus (Refs #793):

```bash
set -a
. ./.env
set +a
BACKUP_HOST_DIR="${BACKUP_HOST_DIR:-./backups}"
sudo install -d -m 2750 -o root -g 1000 "$BACKUP_HOST_DIR"
./scripts/initial-setup.sh
```

Das Beispiel verwendet das empfohlene Backupkonto `root`, weil die Backups als root-Cron laufen. Die Gruppe mit GID 1000 gehört dem Container-Benutzer `appuser`; Modus `2750` gibt ihm nur Lese- und Durchgangsrecht und vererbt die Gruppe an neue Unterverzeichnisse. `backup.sh` legt neue Verzeichnisse mit `0750` an, Dump, Medienarchiv, Audit-Anker und Signaturen mit `0600`; nur das Manifest (`0640`) ist für diese Gruppe lesbar, damit die Anzeige „Letztes Backup“ es auswerten kann. Geben Sie Sicherungen niemals für alle Benutzer frei.

`initial-setup.sh` läuft danach **ohne `sudo`** als Betriebskonto. Mit #2219 prüft und verlangt es genau diesen Verzeichnisvertrag; es nimmt an einem vorhandenen Verzeichnis weder `chown` noch `chgrp` vor. Ein vorhandenes `BACKUP_HOST_DIR` mit Gruppe 1000 und Modus `2750` wird akzeptiert. Fehlt das Verzeichnis, legt das Skript es nur an, wenn das aufrufende Konto GID 1000 setzen darf (Gruppenmitglied oder root); andernfalls bricht es **vor dem ersten `up`** ab und nennt `sudo install -d -m 2750 -o <aufrufendes Konto> -g 1000 "$BACKUP_HOST_DIR"`; wer die Backups als root-Cron fährt, setzt dort `root` ein. Bei falscher Gruppe oder falschem Modus bricht es mit `sudo chgrp 1000 "$BACKUP_HOST_DIR" && sudo chmod 2750 "$BACKUP_HOST_DIR"` als Korrekturbefehl ab. In beiden Meldungen steht statt der Variablen der aufgelöste Pfad; ein relativer `BACKUP_HOST_DIR` wird gegen das Verzeichnis der Compose-Datei aufgelöst. Es legt das Verzeichnis nicht selbst mit root-Rechten an. Danach startet das Skript die Datenbank, wartet auf ihre Bereitschaft, legt das Schema per One-Shot-Job an, fährt den vollen Stack hoch und verifiziert Deployment-Settings sowie die DB-Rollen. Zum Schluss legt es — sofern noch keiner existiert — interaktiv den Super-Admin an (siehe [§ 2.1](#21-erstinstallation-super-admin-anlegen-bootstrap)).

Vorhandene `compose.legal.yml` und `compose.override.yml` im selben Verzeichnis wie `docker-compose.prod.yml` erkennt das Skript automatisch und hängt sie in dieser Reihenfolge an — identisch zur vollen Dateiliste aus der [Umgebungsvariablen-Referenz](#vollständige-umgebungsvariablen-referenz) und allen übrigen Recreate-Beispielen dieses Handbuchs. Die tatsächlich verwendete Dateiliste steht am Anfang der Ausgabe (`Compose-Dateien: …`); ohne eine der beiden Dateien läuft der Stack ausschließlich mit der Basisdatei. Der Start des Virenscanners lädt beim allerersten Mal die komplette Signaturdatenbank herunter; das kann rund drei Minuten dauern und bleibt dabei ohne Zwischenausgabe — der Stack hängt in dieser Zeit nicht, `web` wird erst danach als bereit gemeldet. Fortschritt prüfen: `docker compose -f docker-compose.prod.yml logs clamav`.

Das Skript ist **idempotent** (ein zweiter Lauf ist gefahrlos: Migrationen sind dann No-ops, ein vorhandener Super-Admin wird übersprungen) und **fail-loud** — bricht ein Schritt ab, endet es mit einem Exit-Code ≠ 0 und einer Erklärung. Optionale Umgebungsvariablen: `COMPOSE_FILE` (Default `docker-compose.prod.yml`), `ENV_FILE` (Default `.env`; wird an jeden `docker compose`-Aufruf als `--env-file` übergeben), `ADMIN_USERNAME`, `ADMIN_EMAIL`, `WAIT_TIMEOUT`, `SKIP_SUPER_ADMIN=1`. Ein Passwort nimmt es bewusst **nicht** entgegen — das fragt `create_super_admin` interaktiv ab ([ADR-018](adr/018-rollenmodell-superadmin.md)).

> **Warum die Rollen-Verifikation dazugehört:** Läuft die Datenbank-Rolle der Anwendung als PostgreSQL-Superuser, wird Row Level Security **still** umgangen — die Einrichtungs-Isolation fällt aus, ohne dass irgendetwas fehlschlägt. Das Skript prüft das fail-loud; zusätzlich meldet der Health-Endpoint diesen Zustand dauerhaft als `degraded` (siehe [§ 5.1](#51-health-endpoint)).

**Manuell (gleichwertig, für Schritt-für-Schritt-Kontrolle):** Django-Migrationen laufen **nicht** automatisch beim Start des `web`-Dienstes — sie sind ein separater One-Shot-Schritt (Refs #802). Legen Sie das Backup-Verzeichnis vorher wie im empfohlenen Weg oben mit `sudo install -d -m 2750 -o root -g 1000 "$BACKUP_HOST_DIR"` an: Fehlt es, erzeugt Docker es beim ersten Start als `root:root` mit Modus `0755`, und ein späterer Lauf von `initial-setup.sh` bricht an der Prüfung auf Gruppe 1000 und Modus `2750` ab. Bei der Erstinstallation daher in dieser Reihenfolge:

```bash
# 1. Datenbank (+ ClamAV) zuerst hochfahren
docker compose -f docker-compose.prod.yml up -d db clamav

# 2. Schema einmalig per One-Shot-Job anlegen
docker compose -f docker-compose.prod.yml run --rm \
    --entrypoint=/app/docker-migrate.sh maintenance

# 3. Vollen Stack starten (web + caddy)
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d

# 4. Sicherheits-Posture verifizieren
docker compose -f docker-compose.prod.yml exec web python manage.py check --deploy
docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles
```

Bleibt `REDIS_PASSWORD` leer (Vorlagen-Default), meldet `python manage.py check --deploy` die Warnung `core.W004` (Redis ohne Passwort). Sie ist in dieser Konfiguration erwartet und kein Fehler. Wer die Warnung vermeiden will, setzt `REDIS_PASSWORD`; Compose übergibt den Wert an Redis und bettet ihn für den Webdienst in `REDIS_URL` ein.

Details zum Migrate-Job (Ownership-Normalisierung, Advisory-Lock, erwartete Ausgabe) siehe die kanonische Quelle [`docs/ops-runbook.md` § 1.2](ops-runbook.md#12-deploy-schritte).

Beim ersten Start:
- PostgreSQL-Datenbank wird initialisiert
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

Erwartete (anonyme) Antwort — sie enthält **nur** das `status`-Feld (#1375):

```json
{"status": "ok"}
```

**Direkt nach der Installation normal:** Die MFA-Quote im Compliance-Dashboard bleibt kritisch, bis alle MFA-pflichtigen Konten ihren zweiten Faktor eingerichtet haben. Die Anzeige „Kritische Audit-Events (24h)“ kann nach einer frischen Ersteinrichtung ebenfalls warnen, weil die Ersteinrichtung selbst kritische Audit-Events erzeugt. Noch nie gelaufene Hintergrundjobs erscheinen zunächst als `unknown`: View-Refresh und Breach-Scan laufen erstmals zur nächsten Viertel- beziehungsweise halben Stunde, Retention/Audit-Prüfung/Orphan-Cleanup täglich ab 03:00 Uhr und Statistik-Snapshots am nächsten Monatsersten um 04:00 Uhr. Bleibt ein Zustand über seinen ersten planmäßigen Lauf hinaus bestehen, prüfen Sie `docker compose -f docker-compose.prod.yml logs cron`. Ein veralteter ClamAV-Signaturstand ist dagegen kein normaler Anlaufzustand. Die Karte „Admin-DB-Rolle“ zeigt das Ergebnis der letzten Rollenprüfung im Wartungsdienst; nach Schritt 5 von `scripts/initial-setup.sh` steht sie auf `ok` und nennt den Prüfzeitpunkt. `unknown` heißt, dass noch kein Prüfergebnis hinterlegt ist (siehe § 5.5).

---

## 2. Erstkonfiguration

### Pflichtangaben (Impressum/Datenschutz) einrichten

Selbst gehostete, öffentlich erreichbare Instanzen benötigen eigene Angaben nach § 5 DDG und eigene Informationen nach Art. 13 DSGVO. Anlaufstelle liefert dafür bewusst keine allgemeinen Mustertexte aus: Verantwortliche Stelle, Kontaktwege, Zwecke, Rechtsgrundlagen, Empfänger und Fristen hängen von der jeweiligen Einrichtung und ihrem Betrieb ab.

Legen Sie beide Texte als UTF-8-Dateien auf dem Host ab, zum Beispiel unter `./legal/impressum.md` und `./legal/datenschutz.md`. Setzen Sie anschließend die beiden Pfade aus Sicht des Web-Containers:

```dotenv
LEGAL_IMPRINT_FILE='/run/legal/impressum.md'
LEGAL_PRIVACY_FILE='/run/legal/datenschutz.md'
```

Da `docker-compose.prod.yml` nur ausdrücklich freigegebene Laufzeitvariablen an den Web-Container weitergibt, ergänzen Sie dafür eine lokale `compose.legal.yml` neben der `.env`:

```yaml
services:
  web:
    environment:
      LEGAL_IMPRINT_FILE: ${LEGAL_IMPRINT_FILE}
      LEGAL_PRIVACY_FILE: ${LEGAL_PRIVACY_FILE}
    volumes:
      - ./legal:/run/legal:ro
```

Starten Sie den Webdienst danach mit dem vollständigen [Dateisatz aus § 1](#vollständige-umgebungsvariablen-referenz) neu. `compose.legal.yml` ist hier eingerichtet; `compose.override.yml` wird aufgenommen, falls Sie es eingerichtet haben:

```bash
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d web
```

Jede Datei muss eine reguläre, nicht leere UTF-8-Datei von höchstens 256 KiB sein. Die Dateien unterstützen bewusst nur einen kleinen Markdown-Ausschnitt: Überschriften mit `#` bis `###`, Absätze und Leerzeilen, unsortierte Listen mit `-` oder `*`, Blockzitat-Zeilen mit `> `, Fetttext mit `**Text**`, Inline-Code mit Backticks sowie Links der Form `[Text](URL)`. Linktext ist auf 256 Zeichen und das Linkziel auf 2.048 Zeichen begrenzt; längere Konstrukte erscheinen als normaler Text. Linkziele sind ausschließlich mit `http:`, `https:` oder `mailto:` erlaubt; HTML und alles andere wird ebenfalls als Text ausgegeben. Damit können Rechtstexte keine Skripte oder Styles in die Anwendung einschleusen.

Ohne gesetzten Pfad sowie bei einer fehlenden, nicht regulären, leeren, zu großen, unlesbaren oder ungültig kodierten Datei antwortet die jeweilige Seite mit 404. Der Link wird anhand der Dateimetadaten nur für reguläre, nicht leere Dateien innerhalb des Größenlimits eingeblendet; der globale Seitenaufbau liest dafür keinen Rechtstext. Die höchstens einmal pro fehlerhafter Konfiguration ausgegebene Protokollwarnung nennt nur das betroffene Setting, nicht den konfigurierten Dateipfad. Prüfen Sie nach jeder Text- oder Mount-Änderung `/impressum/` und `/datenschutzerklaerung/` ohne Anmeldung. Der spezielle Demo-Modus verwendet unverändert seine eigenen Rechtstext-Templates und ignoriert diese beiden Dateien.

### 2.1 Erstinstallation: Super-Admin anlegen (Bootstrap)

**Vor jeder anderen Konfiguration** — als letzter Schritt von `./scripts/initial-setup.sh` — wird ein **Super-Admin** (`role=super_admin`, „Systemadministration") angelegt. Diese Rolle ist der einzige facility-übergreifende Account und betreibt die Installation. Sie wird **nicht** über das normale Admin-UI angelegt, sondern über einen dedizierten interaktiven Befehl ohne Default-Passwort:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py create_super_admin
```

> **Hinweis:** Wer [`scripts/initial-setup.sh`](../scripts/initial-setup.sh) genutzt hat ([§ 1, Schritt 4](#schritt-4-stack-starten)), hat diesen Schritt bereits erledigt — das Skript ruft denselben Befehl am Ende auf und überspringt ihn, falls schon ein Super-Admin existiert.

Das Skript fragt interaktiv nach:

1. **Benutzername** (frei wählbar — Empfehlung: nicht `admin`)
2. **E-Mail-Adresse** (Pflicht — wird für Recovery-Mails genutzt)
3. **Passwort** (zweimal eingeben — keinen Default, keinen Demo-Wert)

Nach erfolgreichem Abschluss existiert genau ein User mit Rolle `super_admin`. Mit diesem Account melden Sie sich anschließend unter `/login/` an. Die in Produktion sofort verlangte 2FA-Ersteinrichtung ist erst abgeschlossen, nachdem die Backup-Codes sicher verwahrt und über die Pflicht-Checkbox bestätigt wurden; ein vorzeitiges Verlassen führt beim nächsten Aufruf wieder zu `/mfa/setup/`. Danach legen Sie die erste Einrichtung und den ersten `facility_admin` an — im Django-Admin (`/admin-mgmt/`, **Anlaufstelle Core → Einrichtungen**) oder mit dem Setup-Skript aus [§ 2.2](#22-einrichtung-und-anwendungsbetreuung-anlegen). Beim ersten Aufruf von `/admin-mgmt/` verlangt der Sudo-Modus eine erneute Passwortbestätigung. Der `/system/`-Bereich zeigt Organisation und Einrichtungen nur an; angelegt wird dort nichts.

> **Wichtig — kein Default-Passwort:** Aus DSGVO- und Sicherheits-Gründen liefert die Anwendung **kein** Default-Passwort und keinen Default-Benutzernamen aus (anders als der frühere `make seed`-Workflow für Entwicklungs-Demos). Wer den Befehl überspringt, erhält **keinen** anmeldungsfähigen Account.

> **Hinweis:** Besteht bereits ein `super_admin`, bricht der Befehl ohne Änderung ab. Ein zweiter Super-Admin entsteht nur mit `--force` (`python manage.py create_super_admin --force`); bestehende Konten bleiben dabei unangetastet, ein bereits vergebener Benutzername wird abgelehnt.

#### Account-Sperre aufheben (CLI)

Ein `super_admin` wird durch fehlgeschlagene Logins nicht gesperrt: die Rolle ist vom Account-Lockout ausgenommen (siehe [§ 2.7 Account-Lockout](#account-lockout)), damit das Break-Glass-Konto nicht gezielt ausgesperrt werden kann. Gegen Brute-Force schützen die Ratelimits der Anmeldung (5 Versuche pro Minute und IP-Adresse, 10 pro Stunde und Benutzername); ist eines erreicht, hilft nur, das Zeitfenster abzuwarten — `unlock` hebt es nicht auf. Der folgende Befehl hebt die Sperre eines **facility-gebundenen** Kontos auf (`facility_admin`, `lead`, `staff`, `assistant`), wenn niemand mehr Zugriff auf die Verwaltung der Einrichtung hat, etwa der einzige gesperrte `facility_admin`:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance \
  unlock <username>
```

Der Befehl entsperrt den angegebenen User und schreibt einen `LOGIN_UNLOCK`-AuditLog-Eintrag. Mit dem Benutzernamen eines `super_admin` bewirkt er nichts: er meldet trotzdem „Account-Sperre aufgehoben“, schreibt aber keinen Audit-Eintrag, weil es nichts aufzuheben gibt. Für facility-gebundene User bleibt zusätzlich der UI-Pfad über die Anwendungsbetreuung möglich (siehe [§ 2.7 Account-Lockout](#account-lockout)).

### 2.2 Einrichtung und Anwendungsbetreuung anlegen

Nach dem Login als `super_admin` legen Sie die erste Einrichtung und einen `facility_admin` (Anwendungsbetreuung) für sie im Django-Admin an (`/admin-mgmt/`, **Anlaufstelle Core → Einrichtungen** bzw. **Anlaufstelle Core → Benutzer**). Alternativ steht das interaktive Setup-Skript zur Verfügung — geeignet für reine Single-Tenant-Installationen, in denen Systemadministration und Anwendungsbetreuung dieselbe Person sind:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance setup_facility
```

Ohne Option wird `default` gewählt. Ein mitgeliefertes Domänen-Paket wählen Sie beim Anlegen ausdrücklich aus:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance \
  setup_facility --package awareness
```

`--package` und `--no-default-package` schließen sich gegenseitig aus; ein unbekannter Paketname wird mit der sortierten Liste der tatsächlich mitgelieferten Pakete abgewiesen.

> ⚠️ **Rollen-Voraussetzung (#1883).** Der Befehl schreibt in Tabellen mit Row-Level-Security (Einstellungen, Dokumentationstypen, Feldvorlagen). Die Laufzeit-Rolle des `web`-Containers ist `NOBYPASSRLS` und hat außerhalb eines Web-Requests keinen Einrichtungs-Kontext; sie erhaelt bewusst keine Admin-Secrets. Der opt-in Service `maintenance` verbindet als BYPASSRLS-Rolle. Der Befehl prüft das vorab und bricht mit einer klaren Meldung ab, **bevor** die erste Eingabeaufforderung erscheint. Hintergrund: [Ops-Runbook § 9 DB-Rollenmodell](ops-runbook.md). Bei einer lokalen Entwicklungs-Datenbank, deren Rolle ohnehin Superuser ist, ist der Maintenance-Pfad überflüssig.

Das Skript fragt interaktiv nach:

1. **Name der Organisation** – z. B. `Diakonie Musterstadt e.V.`
2. **Name der Einrichtung** – z. B. `Beratungsstelle Nord`
3. **Admin-Benutzername** der Anwendungsbetreuung (`facility_admin`; Vorgabe `admin` kann überschrieben werden)
4. **Passwort der Anwendungsbetreuung** (zweimal eingeben)

Anschließend sind Organisation, Einrichtung, Standardeinstellungen, der **Standardkatalog der Dokumentationstypen**, die drei **Standardschichten** (Frühdienst, Spätdienst, Nachtdienst), zwei **Schnellvorlagen** am Typ „Kontakt“ und der erste `facility_admin`-Benutzer angelegt. Die Einrichtung ist damit sofort benutzbar: Ohne mindestens einen Dokumentationstyp bliebe die Typ-Auswahl beim Dokumentieren leer und es ließe sich kein einziger Kontakt erfassen.

> **Alles-oder-nichts:** Der Lauf ist eine einzige Datenbank-Transaktion. Bricht er ab (falsche DB-Rolle, unterbrochene Verbindung, Konflikt beim Katalog), bleibt **nichts** halb Angelegtes zurück — auch der Admin-Benutzer nicht. Der Befehl kann nach Behebung unverändert wiederholt werden.

> **Mindest-Kontaktstufe im Standardkatalog:** Der Dokumentationstyp „Beratungsgespräch“ verlangt mindestens die Kontaktstufe „Mit Zusatzangaben“ — eine frisch angelegte Person startet dagegen mit „Nur Pseudonym“. Der erste Dokumentationsversuch für diesen Typ wird deshalb abgelehnt, bis die Kontaktstufe an der Person angehoben wurde; das erneut angezeigte Formular nennt Ort und Weg der Abhilfe.

> **Wiederholungslauf lässt einen vorhandenen Katalog in Ruhe:** Trägt die Einrichtung bereits Dokumentationstypen oder Feldvorlagen (zweiter Lauf, im Django-Admin angelegte Einrichtung), spielt der Befehl den Standardkatalog **nicht** ein. Im Admin gepflegte Namen, Sortierungen, Farben, Symbole und Sichtbarkeiten bleiben damit unverändert; der Befehl sagt das am Ende und nennt `load_package --default` als den Weg, den Auslieferungsstand bewusst nachzuziehen.

> **Opt-out `--no-default-package`:** Wer den Standardkatalog nicht will (eigenes Konfigurationspaket, komplett eigene Typen), hängt `--no-default-package` an. Die Einrichtung startet dann **ohne** Dokumentationstypen und ist erst dokumentierbar, nachdem ein Paket geladen wurde (siehe [docs/packages.md](packages.md)). Der Befehl weist am Ende ausdrücklich darauf hin.

> ⚠️ **Domänen-Pakete sind Ersatz, nicht Zusatz.** Laden Sie ein Domänen-Paket mit `setup_facility --package <name>` gegen eine frische Einrichtung. `load_package --package <name>` bricht bei Typ- oder Feld-Slugs ab, die nicht zum gewählten Paket gehören — auch im Dry-Run. Ein erneuter Lauf desselben Pakets bleibt erlaubt. Der Standardkatalog ist der Bestandsfall: `--default` beziehungsweise `--package default` synchronisiert seine Paketwerte zurück.

> **Bereits bestehende Einrichtung nachrüsten:** Für eine Einrichtung, die es schon gibt — im Django-Admin unter **Anlaufstelle Core → Einrichtungen** angelegt oder mit `--no-default-package` bewusst leer gestartet — spielt derselbe Standardkatalog nachträglich ein:
>
> ```bash
> docker compose -f docker-compose.prod.yml run --rm maintenance \
>   load_package --default --facility <uuid> --dry-run
> ```
>
> `--dry-run` zeigt erst nur an, was passieren würde; ohne den Schalter wird angewendet. Der Lauf löscht nie etwas, **schreibt aber die Katalog-Eigenschaften bestehender Typen und Feldvorlagen auf den Auslieferungsstand zurück** (Name, Sortierung, Farbe, Symbol, Sichtbarkeit) — anders als die Ersteinrichtung, die einen vorhandenen Katalog gar nicht anfasst. Deshalb zuerst mit `--dry-run` ansehen; die vollständige Rücksync-Tabelle steht in [docs/packages.md § 4](packages.md). Die UUID der Einrichtung steht im Django-Admin unter **Anlaufstelle Core → Einrichtungen** in der Adresszeile der Detailseite.

> **Hinweis:** Sollte eine Organisation oder Einrichtung mit dem angegebenen Namen bereits existieren, wird sie wiederverwendet. Ein bestehender Benutzername wird nicht überschrieben.

> **Zweite Einrichtung im Django-Admin (#1970):** Weitere Einrichtungen legen Sie im laufenden Betrieb unter **Anlaufstelle Core → Einrichtungen → Einrichtung hinzufügen** an — das ist der einzige Weg an der Oberfläche und `super_admin` vorbehalten. Im Feld **„Paket"** wählen Sie den mitgelieferten Katalog; ohne Änderung ist `default` vorausgewählt. Das Speichern erzeugt **dieselbe paketbezogene Grundausstattung** wie `setup_facility --package <name>`: Einstellungssatz, gewählter Katalog, passende Zeitfenster und die für dieses Paket deklarierten Schnellvorlagen. Die neue Einrichtung ist damit sofort dokumentierbar; zu tun bleibt nur noch, ihr unter **Anlaufstelle Core → Benutzer** mindestens ein Konto zuzuordnen. Der Startbestand entsteht **genau einmal, beim Anlegen**: Beim späteren Ändern fehlt das Paketfeld, und ein Speichern (z. B. Umbenennen oder „Aktiv" umschalten) rührt den Bestand nicht an — auch dann nicht, wenn er leer ist. Im Admin angelegte, leer gebliebene Einrichtungen rüsten Sie deshalb ausdrücklich nach: den Standardkatalog mit `manage.py load_package --default --facility <uuid>` (Befehl samt Rollen-Override oben), Zeitfenster und Schnellvorlagen von Hand unter **Anlaufstelle Core → Zeitfilter** (technische Pflege) bzw. **Anlaufstelle Core → Quick-Templates**.

> **Organization als Branding-Hülse:** Die Organisation dient nur dem Träger-Branding (Logo, Name in Berichten). Es gibt keinen Org-Admin und keinen Cross-Facility-Effekt durch sie — facility-übergreifender Zugriff läuft ausschließlich über `super_admin` und `/system/`. Architektur-Entscheidung: [ADR-018](adr/018-rollenmodell-superadmin.md).

### 2.2b Django-Adminoberfläche

Die Adminoberfläche ist erreichbar unter:

```
https://anlaufstelle.meine-einrichtung.de/admin-mgmt/
```

Der Aufruf führt zunächst zur Re-Authentifizierung im Sudo-Modus. Bestätigen Sie dort das Passwort des angemeldeten Kontos; anschließend öffnet sich die Adminoberfläche.

Hier können Sie verwalten:

| Bereich | Pfad im Admin | Beschreibung |
|---|---|---|
| Organisationen | Anlaufstelle Core → Organisationen | Träger (Mandantenebene) |
| Einrichtungen | Anlaufstelle Core → Einrichtungen | Standorte einer Organisation |
| Einstellungen | Anlaufstelle Core → Einstellungen | Konfiguration pro Einrichtung |
| Benutzer | Anlaufstelle Core → Benutzer | Technischer Fallback; primäre Pflege unter Verwaltung → Zugänge |
| Dokumentationstypen | Anlaufstelle Core → Dokumentationstypen | Technischer Fallback; primäre Pflege unter Verwaltung → Dokumentationstypen |
| Zeitfilter | Anlaufstelle Core → Zeitfilter | Benannte Zeiträume für Auswertungen. **Ein Zeitfilter, zu dem noch Übergabenotizen gespeichert sind, lässt sich nicht löschen** (#2042) — der Admin meldet die Schutzverletzung. Andernfalls verschwänden die Notizen dieser Schicht still, an ihrer Aufbewahrungsfrist und am Audit-Log vorbei. Umbenennen und Umsortieren bleiben jederzeit möglich; zum Löschen warten Sie das Ende der Frist **Aufbewahrung Übergabenotizen (Tage)** ab oder deaktivieren das Fenster statt es zu entfernen |
| Audit-Log | Anlaufstelle Core → Audit-Logs | Unveränderliches Protokoll |

### 2.2c Einrichtung stilllegen: archivieren statt löschen

Schließt ein Standort, ist **Archivieren** der vorgesehene Weg, nicht das Löschen. Entfernen Sie unter **Anlaufstelle Core → Einrichtungen** den Haken bei **Aktiv** und speichern Sie. Nur `super_admin` darf das — die Einrichtungsverwaltung ist dieser Rolle vorbehalten.

**Was das Archivieren bewirkt (ab #2067):**

- Anmeldungen von Konten dieser Einrichtung werden mit der Meldung „Ihre Einrichtung ist archiviert" abgewiesen; im Audit-Log entsteht eine `LOGIN_FAILED`-Zeile mit `reason: facility_inactive`.
- **Bereits laufende Sitzungen verlieren den Zugriff sofort** — nicht erst beim nächsten Anmelden. Betroffene sehen eine Sperrseite mit Abmelden-Knopf; jeder Fachpfad antwortet mit HTTP 403.
- Auf Datenbankebene wird zusätzlich der Einrichtungs-Kontext (`app.current_facility_id`) geleert, sodass die Row-Level-Security-Regeln (siehe [§ 7.9](#79-row-level-security-rls)) auch dann keine Zeile liefern, wenn ein Pfad die Sperre umginge.
- **Die Daten bleiben vollständig erhalten.** Aufbewahrungsfristen, Löschanträge und das Audit-Log laufen unverändert weiter; ein erneutes Setzen von **Aktiv** stellt den Zugang wieder her.
- `super_admin` bleibt unbeschränkt — sonst wäre die Reaktivierung selbst gesperrt.

> **Warum kein Hard-Delete? Was dabei wirklich passiert.**
> Das endgültige Löschen einer Einrichtung ist **kein unterstützter Vorgang**. Es scheitert — und zwar zuverlässig:
>
> - `Facility` kaskadiert auf nahezu alle Fachtabellen, auch auf `core_auditlog`. Auf dieser Tabelle liegt der DB-Trigger `auditlog_immutable`, der jedes `UPDATE`/`DELETE` abweist. Die Löschkaskade läuft also **mitten** in eine Ablehnung.
> - **Der Abbruch ist sauber, nicht halbfertig.** Django umschließt die gesamte Kaskade — Signale, Fast-Deletes, Updates und alle Stapel — mit **einer** Transaktion. Das gilt auch aus `manage.py shell`: die Shell steht zwar im Autocommit, die Kaskade öffnet ihre Transaktion aber selbst. Der Triggerfehler rollt sie vollständig zurück. Sie sehen eine Fehlermeldung, es geht nichts verloren. Dasselbe gilt für ein einzelnes `DELETE` in `psql` — ein Statement ist eine Transaktion.
> - Zusätzlich blockiert ein `pre_delete`-Guard das Löschen, solange ein aktiver **Legal Hold** an der Einrichtung hängt.
>
> **Die eine Konstellation, in der doch Schaden entsteht:** ein selbst geschriebenes Aufräumskript, das die Fachtabellen **einzeln nacheinander** leert und dabei **zwischendurch committet** — etwa eine Schleife über mehrere `…objects.filter(facility=f).delete()`-Aufrufe in einer Shell ohne umschließende Transaktion. Dann sind die zuerst geleerten Tabellen bereits fest geschrieben, wenn der Lauf am Audit-Trigger stirbt: Ereignisse, Personen oder Anhänge sind weg, Protokoll und Einrichtung stehen noch. Diesen Zustand kann Ihnen niemand aus der Anwendung heraus reparieren; er braucht ein Backup ([§ 3.3](#33-wiederherstellung)). Schreiben Sie solche Skripte nicht — und wenn doch, dann in **eine** umschließende Transaktion.
>
> Ein weiterer Grund, `psql` nicht für diesen Zweck zu verwenden: ein direktes `DELETE` führt Djangos Python-Kaskade gar nicht aus. Die `pre_delete`-Guards (Legal Hold) greifen dort nicht, und die Aufräumlogik der Anwendung bleibt aus.
>
> Wollen Sie eine Einrichtung wirklich restlos entfernen, ist der Weg: archivieren, die Aufbewahrungsfristen ablaufen lassen (siehe [§ 7.3](#73-löschfristen-und-enforce_retention)) und anschließend ein Backup ziehen — nicht die Kaskade.

**Nutzer-Offboarding.** Das Archivieren sperrt den *Zugang*, räumt aber die Identitätsdaten ausgeschiedener Mitarbeitender nicht ab (Vor-/Nachname, Anzeigename, E-Mail, Telefon). Diese zweite Hälfte des Lebenszyklus wird gesondert umgesetzt: #2051 — Offboarding-Bereinigung für ausgeschiedene Mitarbeitende. Bis dahin deaktivieren Sie die betroffenen Konten unter **Verwaltung → Zugänge**; Benutzername und Kennung bleiben dabei bewusst erhalten, damit die Zuordnung im Audit-Log lesbar bleibt.

### 2.3 Einstellungen pro Einrichtung

Unter **Verwaltung → Einstellungen** (Gruppen „Allgemein und Komfort“, „Sicherheit“ sowie „Datenschutz und Fristen“) können Sie unter anderem die folgenden wichtigsten Felder konfigurieren; die vollständige Feldliste steht direkt im Einstellungsformular:

| Feld | Standardwert | Beschreibung |
|---|---|---|
| Vollständiger Name | – | Wird in Berichten angezeigt |
| Standard-Dokumentationstyp | – | Vorausgewählter Typ bei neuem Eintrag |
| Session-Timeout (Minuten) | 30 | Automatischer Logout nach Inaktivität |
| Maximale Sitzungsdauer (Stunden) | 12 | Obergrenze ab der Anmeldung, unabhängig von der Aktivität: Nach dieser Zeit endet die Sitzung in jedem Fall und es ist eine neue Anmeldung nötig. Zulässig 1–168; muss **größer** sein als das Session-Timeout (in Minuten gerechnet), sonst lehnt das Formular die Eingabe ab. Eine Änderung greift erst beim **nächsten** Login — bereits laufende Sitzungen behalten ihre beim Anmelden festgelegte Frist |
| Aufbewahrung anonym (Tage) | 90 | Löschfrist für anonyme Kontakte |
| Aufbewahrung identifiziert (Tage) | 365 | Löschfrist für Kontakte zu Personen der Stufe „Nur Pseudonym" |
| Aufbewahrung qualifiziert (Tage) | 3650 | Frist für Ereignisse „Mit Zusatzangaben“: ab Fallabschluss, ohne Fall ab Ereignisdatum, bei offenem Fall ab letzter fachlicher Fallaktivität (Fallback Anlage); Vorschlag und Freigabe vor Löschung (§ 7.3) |
| Aufbewahrung Übergabenotizen (Tage) | 90 | Löschfrist für die manuellen Schichtnotizen in der Übergabe (#2042). Bewusst kurz: eine Dienstnotiz ist kein Fallvorgang. Der Lauf löscht sie **endgültig** (kein Papierkorb) |
| 2FA-Pflicht für alle Nutzer | false | Erzwingt TOTP-2FA für **alle** Benutzer dieser Einrichtung (siehe [§ 2.7](#27-zwei-faktor-authentifizierung-2fa)) |
| Ziel für „Schnell verlassen" | `https://www.wikipedia.org/` | Adresse, die der Knopf **Schnell verlassen** und die Tastenkürzel `Strg`+`Umschalt`+`X` / `Umschalt`+`Esc` öffnen. Pflichtfeld, nur `http`/`https`. Eine unverfängliche, öffentliche Seite ohne Cookie-Banner wählen — sie steht danach im Verlauf des Geräts, und ein Consent-Dialog kostet genau die Sekunden, die die Aktion sparen soll. Das Ziel erfährt die Herkunft nicht (kein `Referer`) |
| Sichtschutz bei Fenster- oder Tab-Wechsel | false | Verdeckt den Inhalt, sobald das Fenster den Fokus verliert oder der Tab in den Hintergrund geht. Freigabe per Klick oder Taste, ohne Timer |

> **Empfehlung für Frauenhäuser, Gewalt-Fachberatung und Awareness-Teams (#2136):** Dort wird in Gegenwart gefährdender Personen bzw. an lauten, öffentlichen Orten dokumentiert. Schalten Sie den **Sichtschutz ein** und prüfen Sie die **Zielseite** auf Ihre Region hin — sie soll für Außenstehende zum Gerät passen und nicht als „Fluchtseite" lesbar sein. Der Knopf selbst ist immer aktiv und lässt sich nicht abschalten; die Tastenkürzel sind fest vergeben. Beide Felder werden im Audit-Log mit Alt- und Neuwert protokolliert (`SETTINGS_CHANGE`).
>
> **Ungespeicherte Eingaben gehen verloren**, wenn jemand die Aktion auslöst — die Abmeldung räumt wie immer die lokalen Entwürfe ab (`Clear-Site-Data`). Das ist im Nutzerhandbuch benannt und im Tooltip des Knopfs.
>
> **Grenzen, die Sie organisatorisch flankieren müssen** (ausführlich in [threat-model.md](threat-model.md), § „Restrisiken des Mitschauen-Schutzes“): Die Aktion ersetzt nur den **letzten** Verlaufseintrag — frühere Seitenaufrufe derselben Sitzung bleiben mit Pfad und **Seitentitel** in der Browser-Chronik, und der Titel einer Personenseite enthält das **Pseudonym**. Weisen Sie das Team an, den Verlauf am Schichtende zu löschen oder auf gemeinsam genutzten Geräten im privaten Fenster zu arbeiten. Außerdem wirkt der Knopf pro Fenster, bei einem Netzabriss im Moment des Auslösens endet die Server-Sitzung erst über Session-Timeout bzw. maximale Sitzungsdauer, und die Löschung des lokalen Offline-Schlüssels ist auf 500 ms gedeckelt — gelingt sie nicht, bleibt der Schlüssel bis zum nächsten Abmelden, zum Idle-Wipe oder zum Ablauf der 48-h-Offline-Berechtigung bestehen. Deshalb zusätzlich: zweite Fenster schließen und das Gerät nach dem Verlassen nicht unbeaufsichtigt lassen.

> **Grundausstattung einer neuen Einrichtung (#1970).** Neben dem Einstellungssatz entsteht beim Anlegen einer Einrichtung — im Django-Admin wie über `setup_facility` — ein knapper Startbestand, den Sie anschließend frei überschreiben können:
>
> | Bestand | Pflege im Admin | Wirkung, wenn er fehlt |
> |---|---|---|
> | Standardkatalog der Dokumentationstypen | Verwaltung → Dokumentationstypen | **Blockierend:** Ohne Ereignistyp bleibt die Typ-Auswahl in „Kontakt dokumentieren" leer, es lässt sich nichts dokumentieren |
> | Standardschichten Frühdienst (08–16), Spätdienst (16–22), Nachtdienst (22–08) | Anlaufstelle Core → Zeitfilter (technische Pflege) | Komfort: Der Zeitstrom wertet dann den vollen Tag aus statt ein Schichtfenster; Übergabe-Ansicht und Live-Zähler haben keinen Schichtbezug |
> | Schnellvorlagen „Kurzkontakt" und „Beratungskontakt" am Typ „Kontakt" | Anlaufstelle Core → Quick-Templates | Komfort: Wiederkehrende Kontakte werden von Hand ausgefüllt (siehe [§ 2.8](#28-schnell-vorlagen-quick-templates)) |
>
> **Einmalig beim Anlegen, nicht bei jedem Speichern:** Bringt eine Einrichtung im Moment des Anlegens bereits eigene Dokumentationstypen, Zeitfenster oder Schnellvorlagen mit, bleibt die jeweilige Gruppe **unangetastet** — geprüft wird je Gruppe, ein einziges vorhandenes Objekt genügt. Jedes spätere Speichern der Einrichtung im Admin lässt alle drei Gruppen ohnehin unberührt: Im Admin gepflegte Bezeichnungen und Sortierungen werden nie still auf den Auslieferungsstand zurückgesetzt, und gelöschte Standardschichten kommen nicht von selbst zurück. Nachziehen ist ausdrücklich: den Auslieferungsstand des Katalogs holt `manage.py load_package --default` (siehe [§ 2.2](#22-einrichtung-und-anwendungsbetreuung-anlegen)); Zeitfenster und Schnellvorlagen legen Sie unter **Anlaufstelle Core → Zeitfilter** (technische Pflege) bzw. **Anlaufstelle Core → Quick-Templates** selbst an. Einzige Ausnahme: Ein erneuter Lauf der Ersteinrichtung `setup_facility` mit **denselben** Namen für Organisation und Einrichtung stattet aus, was zu diesem Zeitpunkt leer ist — wer alle Zeitfenster gelöscht hat, bekommt dort die drei Standardschichten wieder; der Befehl schreibt zu jedem Bestand hin, ob er ihn angelegt oder unberührt gelassen hat.

> **Jede Einrichtung hat genau einen Einstellungssatz.** Die Zeile entsteht automatisch, sobald die Einrichtung angelegt wird — beim interaktiven `setup_facility`, beim Anlegen im Admin unter **Anlaufstelle Core → Einrichtungen** und (nachträglich, für Altbestände) bei jedem `migrate`-Lauf. Das ist keine Formsache: `enforce_retention` und `create_statistics_snapshots` **überspringen** eine Einrichtung ohne Einstellungssatz — für sie würde also keine Aufbewahrungsfrist durchgesetzt und es entstünden keine Statistik-Snapshots. Beide Jobs protokollieren einen solchen Fall auf `WARNING`-Ebene (`No settings for …, skipping.`); taucht die Meldung im Log auf, ist das ein Betriebsfehler und kein Rauschen.

### 2.4 Weitere Benutzer anlegen

Der reguläre Weg für die Anwendungsbetreuung ist **Verwaltung → Zugänge → Zugang anlegen** (`/verwaltung/zugaenge/`). Anlage und jede Änderung verlangen eine erneute Bestätigung im Sudo-Modus. Die Liste und alle Aktionen sind auf die eigene Einrichtung begrenzt; die Rolle `super_admin` kann dort weder gesehen noch vergeben werden ([ADR-048](adr/048-fachhoheit-zugaenge-hub.md)).

- **Benutzername:** Anmeldename (keine Klarnamen)
- **E-Mail:** Pflicht für den Invite-Flow (siehe unten)
- **Anzeigename:** Name für die Benutzeroberfläche
- **Rolle:** Eine der vier einrichtungsgebundenen Rollen (siehe unten)
- **2FA erforderlich:** erzwingt einen zweiten Faktor für dieses einzelne Konto
- **Löschbestätigung:** nimmt das Konto in den Genehmiger-Pool für Löschanträge auf

Bestehende Zugänge lassen sich dort bearbeiten, deaktivieren oder reaktivieren. Der eigene Administrationszugang kann weder deaktiviert noch herabgestuft werden. Die Fach-UI löscht Konten nicht endgültig: Deaktivieren ist der reguläre Lebenszyklus; eine endgültige Löschung bleibt ein geprüfter technischer Sonderfall. `super_admin`, Bootstrap und Notfälle verbleiben unter **Anlaufstelle Core → Benutzer** im Django-Admin; dort kann ein `super_admin` auch das Feld **2FA erforderlich** für diese Konten umschalten; das Feld liegt im selben Formular und ist damit auch beim regulären Bearbeiten eines Kontos der eigenen Einrichtung durch eine Anwendungsbetreuung sichtbar (zusätzlich zum Weg über Verwaltung → Zugänge). Jede solche Änderung erscheint im Audit-Log als `USER_ACCESS_UPDATED` mit `mfa_required` in den geänderten Feldern. Quick-Templates und Zeitfenster bleiben ebenfalls bewusst technische Admin-Funktionen.

#### Konflikt beim Bearbeiten eines Zugangs

Wurde ein Zugang seit dem Öffnen des Formulars geändert, wird das Speichern abgewiesen. Das gilt auch bei fehlendem oder ungültigem Formularstand. Ihre Eingaben bleiben im Formular erhalten; darüber erscheint der aktuelle Sicherheitsstand mit Rolle, Aktivstatus, 2FA-Pflicht, Löschbestätigung und den übrigen bearbeitbaren Feldern. Prüfen Sie diesen Stand und passen Sie Ihre Eingaben gegebenenfalls an. Erst ein bewusstes erneutes **Speichern** bestätigt Ihre Eingaben auf dieser aktuellen Grundlage; eine weitere zwischenzeitliche Änderung führt erneut zum Konflikt (#2547).

#### Token-Invite-Flow (Refs #528)

Neue Konten werden **ohne** Klartext-Passwort angelegt. Stattdessen versendet die Anwendung eine **Einladungs-E-Mail** mit einem personalisierten Setup-Link (`/invite/<uid>/<token>/`) an die hinterlegte E-Mail-Adresse. Der Link führt den neuen Nutzer auf dasselbe Passwort-Setzen-Formular wie der Passwort-Reset, wo er eigenhändig ein Passwort setzt.

1. Die Anwendungsbetreuung legt den Zugang mit E-Mail unter **Verwaltung → Zugänge** an.
2. System ruft [`send_invite_email`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/invite.py) auf und generiert einen Setup-Token (eigener `invite_token_generator`, basierend auf `uidb64` + Token-Hash).
3. Nutzer empfängt Mail, klickt Link und setzt sein Passwort. Ein automatischer Login findet dabei **nicht** statt — die Bestätigungsseite führt über „Zur Anmeldung“ auf die Anmeldemaske, wo er sich erstmals mit dem neuen Passwort anmeldet.

**Token-Gültigkeit (L4, Refs #1375):** Einladungs- und Passwort-Reset-Tokens haben **getrennte** Gültigkeiten:

- **Einladungen** laufen nach `INVITE_TOKEN_TIMEOUT` ab (Default **3 Tage** = 259200 s, per ENV `DJANGO_INVITE_TOKEN_TIMEOUT` anpassbar) — ein neu eingeladener Mitarbeitender braucht realistisch Tage, um den Link zu öffnen.
- **Passwort-Resets** laufen nach `PASSWORD_RESET_TIMEOUT` ab (Default **2 Stunden**, abweichend vom Django-Standardwert von 3 Tagen; per ENV `DJANGO_PASSWORD_RESET_TIMEOUT` anpassbar) — ein Reset-Link ist ein starkes Recovery-Primitiv und soll kurzlebig sein.

Beide Generatoren sind zudem über ein eigenes `key_salt` getrennt (ein Reset-Token gilt nicht auf `/invite/` und umgekehrt) und invalidieren den Token automatisch, sobald der Nutzer sein erstes Passwort gesetzt hat.

**Setup-Link erneut senden:** Ist die Mail nicht angekommen oder der Token abgelaufen, kann die Anwendungsbetreuung in der Zugangs-Detailansicht über **Einladung erneut senden** einen neuen Token ausstellen und eine frische Mail verschicken lassen. Der Button erscheint nur, solange die Einrichtung des Kontos aussteht. Für `super_admin`-Konten und technische Sonderfälle markiert die Systemadministration stattdessen die betreffenden Konten unter **Anlaufstelle Core → Benutzer** im Django-Admin und wählt die Aktion **Einladung erneut senden (Setup-Link)**. Die Aktion verarbeitet nur Konten mit hinterlegter E-Mail und noch ausstehender Einrichtung; ein gegebenenfalls noch nutzbares Initialpasswort wird dabei entwertet.

**SMTP-Voraussetzungen und Fehlersuche:** Technisch ist keiner dieser Werte
Pflicht — ohne Angabe startet die Anwendung im Compose-Stack mit leerem
`EMAIL_HOST`, Port `587`, STARTTLS aktiv (`EMAIL_USE_TLS=True`) und Absender
`noreply@anlaufstelle.app`. Mit leerem `EMAIL_HOST` scheitert jeder Versand,
und `/health/` meldet im Feld `smtp` den Wert `disabled`, ohne den
Gesamtstatus zu senken — ein Monitoring, das nur `status` auswertet, bemerkt
die fehlende Konfiguration also nicht. Betrieblich, also für tatsächliche Zustellung,
müssen Betreiber `EMAIL_HOST` auf ein erreichbares Relay setzen; `EMAIL_PORT`
und `EMAIL_USE_TLS` nur anpassen, wenn sie vom Default abweichen sollen.
`EMAIL_USE_TLS=True` verhandelt **STARTTLS** auf dem konfigurierten Port.
Für implizites TLS/SMTPS (klassisch Port 465) setzen Betreiber stattdessen
`EMAIL_USE_SSL='True'`, `EMAIL_USE_TLS='False'` und `EMAIL_PORT=465`; ein Relay
mit ausschließlich Port 465 lässt sich also anbinden. Beide Schalter zugleich
auf `True` sind ausgeschlossen: die Anwendung bricht den Start dann mit einer
entsprechenden Meldung ab. Verlangt der Relay eine Anmeldung,
sind zusätzlich `EMAIL_HOST_USER` und `EMAIL_HOST_PASSWORD` betrieblich
notwendig. `DEFAULT_FROM_EMAIL` muss eine für SPF, DKIM und DMARC autorisierte
Absenderdomain verwenden. Damit die Links in Einladungs- und Reset-Mails auf
die richtige öffentliche Instanz zeigen, muss außerdem `EXTERNAL_ORIGIN`
stimmen. Das [Operations-Runbook § 5.8](ops-runbook.md#58-smtp-versand-pruefen)
enthält den Testbefehl und den Log-Zugriff.

Erscheint beim Anlegen oder erneuten Senden die Meldung, dass die Einladung
nicht versendet werden konnte, SMTP-Werte und Web-Logs prüfen und danach
**Einladung erneut senden**. Eine Erfolgsmeldung belegt nur die Übergabe an das
Mail-Backend: Bleibt die Mail aus, zusätzlich Spam-Ordner, Quarantäne und beim
Provider die SPF-/DKIM-/DMARC-Ausrichtung prüfen. Mit angemeldeter Session oder
`X-Health-Token` zeigt zusätzlich `/health/` ([§ 5.1](#51-health-endpoint)) im Feld `smtp`, ob der
konfigurierte Server per TCP erreichbar ist (`ok`/`unreachable`) — das prüft
nur die Erreichbarkeit, nicht Authentifizierung oder tatsächliche Zustellung,
und bleibt anonym unsichtbar. Bis zur Behebung steht der folgende
Initialpasswort-Weg als technischer Notfall-Fallback zur Verfügung.

**Technischer Fallback ohne E-Mail:** Die Fach-UI verlangt immer eine E-Mail-Adresse. Nur der Django-Admin kann einen Nutzer **ohne** E-Mail-Adresse anlegen und zeigt dann einmalig ein Klartext-Initialpasswort. Dieser Weg ist **unsicher** und sollte nur als Notlösung verwendet werden — besser: E-Mail nachtragen und Einladung neu versenden.

> **Hinweis:** `must_change_password` wird bei **jeder** Neuanlage im Admin gesetzt. Schließt der Nutzer den Setup-Schritt der Einladung ab — oder später einen Passwort-Reset —, wird das Flag dabei automatisch wieder gelöscht (#1969); er hat sein Passwort in diesem Schritt ja selbst gewählt und muss es beim ersten Login nicht sofort erneut ändern. Erzwungen bleibt der Wechsel damit genau dort, wo er gebraucht wird: beim Fallback ohne E-Mail-Adresse, bei dem der Admin das Initialpasswort kennt.

#### Rollenbeschreibung

> **Zielgruppe dieses Abschnitts:** die **Bootstrap-/Verwaltungssicht** — welche Rolle Sie beim Anlegen von Benutzern vergeben und welche Bootstrap-Rechte daran hängen. Die fachliche Begründung steht im [Fachkonzept (Role — Die Rolle)](fachkonzept-anlaufstelle.md#role--die-rolle), die praktische „Was darf wer?"-Sicht im [User-Guide § 9](user-guide.md#9-rollen-und-berechtigungen), die technischen Details (DB-Werte, RLS-Scoping) in der [FAQ § C](faq.md#die-fünf-rollen).

Anlaufstelle kennt fünf Rollen — vier facility-gebunden, eine facility-übergreifend:

| Rolle | Bezeichnung | Scope | Berechtigungen |
|---|---|---|---|
| `super_admin` | Systemadministration | facility-übergreifend (`/system/`) | Bootstrap, Einrichtungen anlegen, Pre-Auth-AuditLogs, Backup-Einsicht. Wird über `manage.py create_super_admin` angelegt — **nicht** über das Admin-UI. Jeder Aufruf einer `/system/`-View erzeugt einen `SYSTEM_VIEW`-AuditLog-Eintrag. |
| `facility_admin` | Anwendungsbetreuung | eine Einrichtung | Vollzugriff in der eigenen Einrichtung: Benutzerverwaltung, Typ-/Sensitivitätskonfiguration, Audit-Log (`/audit/`), DSGVO-Paket. Kann **nicht** in andere Einrichtungen schauen. |
| `lead` | Leitung | eine Einrichtung | Auswertungen, Löschanträge genehmigen, alle Fälle der Einrichtung einsehen, Datenexport |
| `staff` | Fachkraft | eine Einrichtung | Kontakte, Fälle, Dokumentation erfassen und bearbeiten |
| `assistant` | Assistenz | eine Einrichtung | Eingeschränkte Erfassung; Personen beider Kontaktstufen einsehen, Zusatzfelder nur bei wirksamer Sensibilität „Normal“; keine internen Personennotizen |

> **Trennung System ↔ Anwendung:** `super_admin` betreut Hosting und Bootstrap, `facility_admin` betreut Konfiguration und Nutzer **innerhalb** einer Einrichtung. Diese Trennung ist DSGVO-relevant: Der Super-Admin sieht keine fachlichen Inhalte einer Einrichtung, ohne dass dies im AuditLog (`SYSTEM_VIEW`) auftaucht. Architektur-Entscheidung: [ADR-018](adr/018-rollenmodell-superadmin.md).

### 2.5 Dokumentationstypen konfigurieren

> **Ausgangslage:** Eine über `setup_facility` **oder** im Django-Admin unter **Anlaufstelle Core → Einrichtungen** ([§ 2.2](#22-einrichtung-und-anwendungsbetreuung-anlegen)) angelegte Einrichtung bringt den mitgelieferten **Standardkatalog** bereits mit — sie ist ohne weiteren Schritt dokumentierbar. Leer ist die Liste nur, wenn der Katalog bei der Ersteinrichtung per `--no-default-package` abgewählt wurde oder die Einrichtung aus einer Version vor #1970 stammt. Beides bleibt so, bis Sie es ausdrücklich ändern — ein Speichern der Einrichtung im Admin holt den Katalog **nicht** nach (das würde die Abwahl still übergehen). Führen Sie dann `manage.py load_package --default --facility <uuid>` aus (Befehl samt Rollen-Override in [§ 2.2](#22-einrichtung-und-anwendungsbetreuung-anlegen)). Aufbau, Feld-Referenz und Pflege-Regeln eines Konfigurationspakets — auch eines eigenen statt des mitgelieferten — beschreibt [docs/packages.md](packages.md). Die folgenden Abschnitte beschreiben, wie Sie den Bestand danach anpassen.

Unter **Verwaltung → Dokumentationstypen** (`/config/document-types/`) legen Sie fest, welche Arten von Dokumentation in der Einrichtung erfasst werden können. Die gleichnamige Ansicht unter **Anlaufstelle Core → Dokumentationstypen** im Django-Admin bleibt als technischer Fallback erhalten, ist aber nicht der reguläre Pflegeweg. Jeder Typ besteht aus:

- **Name und Beschreibung**
- **Feldvorlagen** (Freitextfelder, Auswahlfelder etc.)
- **Löschfrist** in Tagen (nur für Ereignis-Typen; zusätzlich zur Frist der Kontaktstufe, falls gesetzt — es gilt die zuerst ablaufende Frist, eine längere Typ-Frist verlängert die Kontaktstufen-Frist nicht)

> **Altersgruppen sind keine Feldvorlagen-Einstellung.** Die Altersgruppe an einer Person verwendet systemweit genau **Unter 18, 18–26, 27+ und Unbekannt**. Weder diese Verwaltungsansicht noch ein Konfigurationspaket kann die Grenzen oder Labels pro Einrichtung ändern. Das gleichnamige Auswahl-Zusatzfeld `alterscluster` des Standardpakets gehört dagegen zu einzelnen Ereignissen (auch anonymen Kontakten); seine Optionen ändern die Personen-Altersgruppe und die eingebauten Altersgruppen-Statistiken **nicht**. Hintergrund und Bedingungen für eine spätere Neubewertung: [ADR-047](adr/047-altersgruppen-systemweit-fix.md).

#### Eindeutigkeit des Namens

Das Paar `(Einrichtung, Name)` ist eindeutig: Pro Einrichtung kann jeder Dokumentationstyp-Name nur **einmal** vergeben werden. Der Versuch, einen zweiten Typ mit demselben Namen anzulegen, wird als Formularfehler abgewiesen.

> **Konfigurationspakete** (YAML) dürfen den Schlüssel `category` enthalten: Pakete bleiben gültig und ladbar, der Wert wird aber ignoriert und der Ladebericht weist ihn als Warnung aus. Siehe [docs/packages.md](packages.md).

#### Sensibilitätsstufe

Die Sensibilitätsstufe steuert, welche Rollen auf Einträge dieses Typs zugreifen können:

| Stufe | Zugriff | Verwendung |
|-------|---------|------------|
| **Normal** | Alle Rollen (inkl. Assistenz) | Allgemeine Kontakte, Leistungen |
| **Erhöht** | Fachkraft, Leitung, Anwendungsbetreuung | Beratungsgespräche, medizinische Versorgung |
| **Hoch** | Leitung und Anwendungsbetreuung | Krisengespräche, besonders sensible Daten |

#### Strichliste

Öffnen Sie unter **Verwaltung → Dokumentationstypen** den gewünschten Typ und wählen Sie **Bearbeiten**. Das Kontrollkästchen **„In Strichliste anbieten“** schaltet ihn ausdrücklich für die Strichliste im Zeitstrom frei; die Detailansicht zeigt anschließend den gespeicherten Status. Neue Typen sind standardmäßig nicht freigegeben. Die Freigabe wirkt nur bei aktiven **Ereignis**-Typen ohne Mindest-Kontaktstufe; bei Personen- oder Fall-Typen wird die Option ausgeblendet und beim Speichern deaktiviert.

- Hat der Typ keine Pflichtfelder, erzeugt `+1` direkt einen anonymen Kontakt.
- Bestehen alle Pflichtfelder aus Zahlen, öffnet `+1` eine kompakte Werteingabe; die eingegebenen Werte werden zusammen mit genau einem anonymen Kontakt gespeichert. Optionale Felder werden dabei nicht erfasst.
- Hat der Typ ein Pflichtfeld eines anderen Typs (etwa Text, Auswahl, Datum, Ja/Nein oder Datei), erscheint er nicht in der Strichliste.

Die Freigabe hebt keine Sichtbarkeitsregeln auf: Ein Typ erscheint nur für Rollen, die den Typ und alle seine Pflichtfelder sehen dürfen. Prüfen Sie daher auch die Sensibilitätsstufen der zugeordneten Feldvorlagen, bevor Sie einen Typ freigeben.

#### Systemtyp

Der Systemtyp verknüpft einen Dokumentationstyp mit interner Anwendungslogik. Er wird bei der Erstellung gesetzt und ist danach **nicht mehr änderbar**.

Der Systemtyp hat **eine** Funktion: **UI-Logik**. Entsprechend gibt es genau zwei Werte:

| Systemtyp | Auswirkung |
|-----------|------------|
| **Hausverbot** | Aktiviert Hausverbot-Banner auf der Personenseite, eigener Filter im Zeitstrom, Zählung und Highlight in der Übergabe; speist die Beispiel-Hausverbots-Übersicht |
| **Krisengespräch** | Wird als Highlight in der Übergabe angezeigt (letzte Krisen-Events) |

> **Hinweis:** Nicht jeder Dokumentationstyp benötigt einen Systemtyp — die meisten kommen ohne aus. Ob ein Typ in einem Bericht landet, sagt Ihnen die Auskunft „Erscheint in …" in Detail- und Formularansicht; der Systemtyp hat damit nichts zu tun.

> **Die Werteliste ist eingefroren** (RFC #1748, Entscheid 2026-08-03). Es kommen **keine neuen Systemtypen** hinzu: Ein Wert gehört nur dann hierher, wenn er Anwendungsverhalten steuert. Berichts-Zuordnungen laufen über den Berichtsschlüssel.

#### Berichtsschlüssel

Der **Berichtsschlüssel** (`report_key`) entscheidet, in welchem Bericht ein Dokumentationstyp erscheint — und in welcher Zeile. Er ist ein eigenes Auswahlfeld im Formular des Dokumentationstyps; **Feldvorlagen haben es ebenfalls** (dazu unten).

Die Auswahl kommt aus den Berichten selbst: Was ein Bericht auswertet, deklariert er im Code, und genau diese Schlüssel stehen zur Wahl. Sie wählen also, **welchen Schlüssel** Ihr Typ trägt — nicht, worauf ein Schlüssel abbildet. Ein eigener Typ „Sozialberatung" mit dem Schlüssel „Beratungsgespräch" landet in derselben Berichtszeile wie „Schuldnerberatung" mit demselben Schlüssel.

| Berichtsschlüssel | Zeile im Beispiel-Sachbericht |
|-----------|-----------------|
| **Kontakt** | Kontakte |
| **Beratungsgespräch** | Beratung |
| **Krisengespräch** | Beratung |
| **Medizinische Versorgung** | Versorgung |
| **Spritzentausch** | Versorgung |
| **Begleitung** | Vermittlung |
| **Vermittlung** | Vermittlung |
| **Gewaltvorfall** | Vorfälle |
| **Schutzmaßnahme** | Schutz |
| **Gruppenangebot** | Gruppenangebote |
| **Versorgung (Bedarf)** | Versorgung |
| **Gehört ausdrücklich in keinen Bericht** | *(ausgeschlossen — und **kein** Mangel)* |
| *(leer)* | *(erscheint in keinem Bericht — und wird als Lücke gemeldet)* |

> Die Schlüssel **Gewaltvorfall**, **Schutzmaßnahme**, **Gruppenangebot** und **Versorgung (Bedarf)** stammen aus den mitgelieferten Domänen-Paketen für Awareness-Arbeit, Frauenhaus und Jugendarbeit (Paket-Übersicht #2133). Sie stehen **jeder** Einrichtung zur Wahl, unabhängig davon, welches Paket geladen ist: Der Werteraum kommt aus dem Bericht, nicht aus dem Katalog. Neue Zeilen im Beispiel-Sachbericht erscheinen erst, sobald ein Typ mit dem passenden Schlüssel Ereignisse trägt; an den Zahlen bestehender Berichte ändert sich dadurch nichts.

Bei einer **teilweisen Berichtszuordnung** warnen die Bearbeitungsformulare für Dokumentationstyp und Feldvorlage vor noch fehlenden Typ- oder Feld-Schlüsseln an Ereignistypen. Bei Feldvorlagen nennen sie nur fehlende Zuordnungen aus Anforderungen, die diese Vorlage betreffen; eine vollständig erfüllte lokale Alternative unterdrückt weiterhin den Hinweis. Beispiel: Für die Spritzentausch-Bilanz braucht derselbe aktive Typ die aktiven Felder „Ausgabe gesamt“ (`ausgabe_stueck`) **und** „Rückgabe gesamt“ (`rueckgabe_stueck`). Ein vollständiger anderer Typ beseitigt den Hinweis für diesen Typ nicht. Alternative Anforderungen eines Berichts werden getrennt geprüft; eine vollständig erfüllte Alternative genügt für die Verfügbarkeit des Berichts und unterdrückt den Hinweis für diesen Typ, auch wenn andere Gruppen unvollständig bleiben. „Erscheint in“ nennt bei Typen verfügbare Berichte, deren Zähllogik diesen Typ berücksichtigt; bei getrennten Gruppen muss die Gruppe konfiguriert sein und der Typ ihre benötigten Felder mit einem eindeutigen aktiven Lesepfad tragen. Deaktivierte Typen bleiben gelistet, soweit sie zur Zählung beitragen. Die Spritzentausch-Bilanz zählt auch unvollständige Typen mit und summiert deren vorhandene verwertbare Zahlen; nur Ereignisse ohne verwertbare Zahl gelten dort als „ohne Werte“. In der Leistungs- und Krisenarten-Verteilung werden dagegen nur Ereignisse mit verwertbarem Auswahlwert gezählt; einem Typ ohne benötigtes Gruppenfeld bleibt der Hinweis auf das fehlende Feld, ohne dass die Verteilung unter „Erscheint in“ genannt wird. Die Warnung vor fehlenden Feldern bleibt für aktive unvollständige Typen bestehen. Bei Feldvorlagen zählt der tatsächliche Lesepfad: Eine aktive zugeordnete Vorlage erscheint bei einem Bericht, der ihre Werte für diesen Typ liest, auch wenn der Typ unvollständig oder deaktiviert ist. Die Detailseite des Typs zeigt ebenfalls die Warnungen. Beim Anlegen eines Typs stehen mögliche Berichte und fehlende Zuordnungen unter getrennten Überschriften. Ein fehlender Typ-Schlüssel wird nur bei leerer Zuordnung genannt; ein anders gesetzter Schlüssel, einschließlich „Gehört ausdrücklich in keinen Bericht“, ist für diese Typ-Alternative kein ergänzbarer Mangel. Die Hinweise blockieren das Speichern nicht und ändern keine Schlüssel; ein bereits gesetzter Schlüssel bleibt unveränderbar.

> **Der Berichtsschlüssel wirkt nicht auf die Übersicht.** Kennzahlen, Diagramme und Tabellen des Reiters „Übersicht" (Kontakte im Zeitverlauf, Dokumentationstypen, Altersgruppen) zählen die Ereignisse des Zeitraums **unabhängig vom Berichtsschlüssel** — auch solche, die kein Bericht auswertet. Ein Typ mit „Gehört ausdrücklich in keinen Bericht" fällt deshalb aus den Berichten heraus, die über den Berichtsschlüssel zuordnen (etwa dem Beispiel-Sachbericht), bleibt in der Übersicht aber sichtbar und zählt dort weiter mit. Auch der Reiter „Externer Bericht" kennt den Berichtsschlüssel nicht.
>
> **Zwei Dinge gehören dazu.** Erstens binden Berichte über **Anwendungsverhalten** weiterhin über den Systemtyp statt über den Berichtsschlüssel: Die Beispiel-Hausverbots-Übersicht zeigt Hausverbots-Ereignisse also auch dann, wenn der Typ „Gehört ausdrücklich in keinen Bericht" trägt — im mitgelieferten Standardkatalog ist genau das der Fall ([ADR-042](adr/042-report-key-berichtszuordnung.md), *Vollzug E4*). Zweitens liest die Übersicht für abgeschlossene Monate in der Regel aus den gespeicherten Monats-Auswertungen, während die Datensatz-Berichte je nach Bericht live rechnen; auch das kann Zahlen auseinanderlaufen lassen ([User-Guide § 7](user-guide.md#7-statistik-und-export)).
>
> Übersicht und Bericht dürfen im selben Zeitraum deshalb verschiedene Zahlen zeigen, ohne dass etwas falsch konfiguriert ist: Die Übersicht beantwortet „Was ist passiert?", die Berichte beantworten „Was geht in welchen Bericht?". Wollen Sie die Übersicht auf einen einzelnen Typ einschränken, nutzen Sie das Dropdown „Alle Dokumentationstypen" über den Diagrammen.

Der Beispiel-Sachbericht ist ein anpassbares Beispiel bzw. eine Vorlage — kein verbindlicher oder aktueller Behördennachweis.

> **Leer und „ausdrücklich ausgeschlossen" sind zwei verschiedene Dinge.** Der Bericht weist unter „Berichte" aus, wie viele Ereignisse er *nicht* zeigt. Gezählt werden dort **nur** Typen, die gar keine Aussage treffen (Schlüssel leer) — ein Typ mit „Gehört ausdrücklich in keinen Bericht" ist eine vollständige Aussage und erscheint **nicht** im Hinweis (#1819). Hausverbote und Notizen zählen nicht mit; eine Einrichtung, die Hausverbote dokumentiert, sieht den Hinweis deshalb nicht dauerhaft, ohne dass etwas falsch konfiguriert ist.
>
> Einen **dritten** Fall gibt es seither ebenfalls: Trägt ein Typ einen Schlüssel, den kein Bericht (mehr) kennt — etwa aus einem fremden Konfigurationspaket oder nach einem Tippfehler —, meldet der Bericht das gesondert. „Ich habe zugeordnet, es passiert nur nichts" ist ein anderer Befund als „ich habe nichts gesagt".

> **Nachtragen ja, ändern nein.** Ein leerer Berichtsschlüssel kann **jederzeit nachgetragen** werden — das ist der Reparaturweg, wenn ein Typ versehentlich in keinem Bericht auftauchte. Ein **gesetzter** Schlüssel ist danach fest (auch „Gehört ausdrücklich in keinen Bericht"): Berichte über vergangene Zeiträume sollen nicht rückwirkend andere Zahlen liefern. Das Formular weist beim Nachtragen darauf hin, dass der Schlüssel auf künftig erzeugte Berichte über **alle** Zeiträume wirkt.
>
> Beim **Anlegen** schlägt das Formular passende Werte vor: „Krisengespräch" als Systemtyp schlägt den Schlüssel „Krisengespräch" vor, „Hausverbot" schlägt „Gehört ausdrücklich in keinen Bericht" vor. Beides ist ein Vorschlag, kein Zwang — Systemtyp und Berichtsschlüssel dürfen bewusst auseinanderlaufen.

> **Falsch gesetzt? Der Weg führt über einen neuen Typ.** Ein gesetzter Berichtsschlüssel lässt sich nicht korrigieren — weder im Formular noch im Django-Admin noch über ein Konfigurationspaket. Wurde versehentlich der falsche Wert gewählt, legen Sie einen **neuen** Dokumentationstyp mit dem richtigen Schlüssel an und setzen den alten über das Feld **„Aktiv"** auf inaktiv. Beachten Sie dabei die Eindeutigkeit des Namens (siehe „Eindeutigkeit des Namens" weiter oben): Sie gilt auch gegenüber **inaktiven** Typen, der neue Typ braucht also einen anderen Namen — oder Sie benennen den alten zuvor um. Der alte Typ steht dann für neue Ereignisse nicht mehr zur Wahl; bereits erfasste Ereignisse bleiben unverändert bei ihm — und damit in der Berichtszeile, in der sie bisher gezählt wurden. Denken Sie zusätzlich an die **Schnell-Vorlagen** des alten Typs (§ 2.8): Sie verschwinden mit der Deaktivierung des Typs **nicht** von der Erfassungsseite und sind eigens zu deaktivieren. **Löschen ist kein Ersatz:** Ein Typ, zu dem Ereignisse existieren, lässt sich nicht löschen. Rechnen Sie damit, dass ein Berichtszeitraum, der über den Umstellungstag hinweggeht, beide Typen enthält — planen Sie den Wechsel deshalb möglichst auf eine Zeitraumgrenze.
>
> **Interim:** Diese Schritte sind heute von Hand auszuführen. Ein geführter Ablauf dafür ist als Vorschlag erfasst (#1888); an der Einmaligkeit des Schlüssels ändert er nichts.

**Berichtsschlüssel an Feldvorlagen.** Berichte, die **Feldwerte** auswerten (Minutensummen, Spritzensaldo, Verteilung der Auswahlfelder), brauchen zusätzlich zu wissen, **welches Feld** sie lesen sollen. Dafür trägt die Feldvorlage ihren eigenen Berichtsschlüssel (z. B. „Minuten"), und der Name des Feldes ist frei wählbar — ein Zahlenfeld „Gesprächsdauer" statt „Dauer" weist also weiterhin seine Minuten aus. Ein Freitextfeld „Statistik-Kategorie" gibt es an der Feldvorlage nicht; keine Auswertung würde es lesen.

Der Bericht **„Summe von Zahlenfeldern“** verwendet den Schlüssel `quantity_units` („Stückzahl“) für ein Zahlenfeld an Ereignis-Typen. Ein Feld, das bereits `ausgabe_stueck` trägt, lässt sich wegen der unveränderlichen gesetzten Berichtsschlüssel nicht auf `quantity_units` umstellen. Legen Sie dafür eine neue Feldvorlage mit dem Schlüssel `quantity_units` an; vorhandene Ereigniswerte werden dadurch nicht migriert.

> **Je Dokumentationstyp höchstens eine Feldvorlage je Berichtsschlüssel.** Zwei Felder mit dem Schlüssel „Minuten" am selben Typ hießen doppelt gezählte Minuten. Die Zuordnung wird deshalb abgewiesen — ebenso das nachträgliche Setzen eines Schlüssels, der an einem gemeinsamen Typ schon vergeben ist; entsteht die Doppelung trotzdem (z. B. über ein Konfigurationspaket), zählt der Bericht **keines** der beiden Felder und weist das aus. Dieselbe Feldvorlage an **verschiedenen** Typen ist unproblematisch, ebenso zwei gleich benannte Felder mit **verschiedenen** Schlüsseln.
>
> **Bewusste Lücke:** Trägt eine **Feldvorlage** einen Schlüssel, den kein Bericht kennt, sagt der Bericht das *nicht* eigens — anders als beim Dokumentationstyp. Der Grund ist, dass sich dort keine sinnvolle Zahl nennen ließe: Ein Feld mit unbekanntem Schlüssel liefert schlicht keine Werte, und wie viele es hätten sein sollen, weiß niemand. Sichtbar bleibt der Zustand über „Erscheint in …" an der Feldvorlage selbst — dort steht dann „Erscheint in keinem Bericht."

#### Bindungs-Inventar prüfen

Die Auskunft „Erscheint in …" beantwortet die Frage für **einen** Dokumentationstyp bzw. **eine** Feldvorlage. Für den Blick über die ganze Einrichtung — welcher Typ und welche Feldvorlage erscheinen in welchem Bericht, und welche in keinem — gibt es den Wartungsbefehl **`inventory_report_bindings`**:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance \
  inventory_report_bindings --facility <uuid-oder-name>
```

Mit `--format json --output <datei>` schreibt er dieselbe Auswertung maschinenlesbar. Er **ändert nichts an Ihrer Konfiguration** — er liest nur.

Bei **Dokumentationstypen** unterscheidet er dieselben vier Zustände wie der Vollständigkeitshinweis der Berichte (zugeordnet · ausdrücklich ausgeschlossen · unbekannter Schlüssel · keine Angabe); aufgeführt werden dabei nur Typen mit Entitätstyp **Ereignis** — an Personen- und Fall-Typen wirkt der Berichtsschlüssel ohnehin nicht (siehe „Mindest-Kontaktstufe"). Bei **Feldvorlagen** — dort ohne Einschränkung auf einen Entitätstyp — nennt er nur *gebunden* oder *ungebunden*: Die oben beschriebene bewusste Lücke gilt auch hier, ein unbekannter Feld-Schlüssel erscheint schlicht als ungebunden. Als gebunden gilt eine Feldvorlage außerdem schon dann, wenn ihr Schlüssel zu einem in Ihrer Einrichtung verfügbaren Bericht passt — auch wenn sie noch keinem Dokumentationstyp zugeordnet ist.

Sinnvoll vor allem, nachdem Sie viele Typen von Hand angelegt oder ein fremdes Konfigurationspaket eingespielt haben, und als Gegenprobe, bevor Sie einen Bericht erstmals nach außen geben.

> ⚠️ **Rollen-Voraussetzung.** Wie die übrigen Wartungskommandos (`load_package`, `purge_orphan_field_data`, `reencrypt_fields`) muss der Lauf mit der **Admin-DB-Rolle** erfolgen — nach demselben Muster wie die Läufe in [Ops-Runbook § 3.5](ops-runbook.md#35-manuelle-ausfuehrung); die Rollen selbst erklärt das DB-Rollenmodell in [Ops-Runbook § 9](ops-runbook.md#9-row-level-security-rls). Ohne sie bricht er mit einer klaren Meldung ab, statt einen leeren Report auszugeben — unter der Laufzeit-Rolle sähe eine Einrichtung ohne sichtbare Zeilen aus wie eine tadellos konfigurierte (Row-Level-Security).

#### Mindest-Kontaktstufe

Legt fest, welche Kontaktstufe eine Person mindestens haben muss, damit ein Ereignis dieses Typs erstellt werden kann. Beispiel: Beratungsgespräche erfordern mindestens „Mit Zusatzangaben", weil zusätzliche persönliche Angaben zur Person vorliegen müssen.

> **Nur für Ereignis-Typen wirksam (Refs #1058).** **Mindest-Kontaktstufe**, **Höchstfrist (verkürzt nur), `retention_days`**, **Systemtyp** und **Berichtsschlüssel** wirken ausschließlich an Dokumentationstypen mit Entitätstyp **Ereignis**: Die Kontaktstufen-Prüfung greift nur bei der Ereignis-Anlage, und der Aufbewahrungslauf löscht ausschließlich Ereignisse. Für **Personen** greifen stattdessen die einrichtungsweiten Regeln aus den Einstellungen: die Papierkorb-Frist (`client_trash_days`) und die Anonymisierung, sobald die Ereignis-Retention den Bestand einer Person abgeräumt hat. **Fälle haben keine eigene Aufbewahrungsfrist** — der Aufbewahrungslauf löscht keinen Fall von sich aus. Er greift nur, wenn ein Fall bereits **gelöscht** wurde: Dann läuft dessen Papierkorb nach `client_trash_days` ab und der Fall wird anonymisiert (Refs #1818, [ADR-044](adr/044-fall-loeschpfad.md) — ersetzt die Zusage aus [ADR-039](adr/039-owner-generische-dateianhaenge.md) § 6 ab). Ohne Löschung werden die Inhalte eines Falls weiterhin erst mit der Anonymisierung der zugehörigen Person redigiert. `retention_qualified_days` zählt bei geschlossenem Fall ab Abschluss, bei offenem Fall ab letzter fachlicher Fallaktivität (Fallback Anlage) und ohne Fall ab Ereignisdatum; gelöscht werden nach Vorschlag und Freigabe die Ereignisse, nicht der Fall (§ 7.3).
>
> Das Formular **blendet diese vier Felder aus**, sobald Sie als Entitätstyp **Person** oder **Fall** wählen (Refs #1757) — Sie füllen also keine Felder mehr aus, die nie greifen. Das Ausblenden ist rein optisch: bereits gesetzte Werte bleiben unverändert am Datensatz, und ein Feld mit Fehlermeldung bleibt sichtbar. Ohne JavaScript bleiben alle Felder sichtbar und tragen den erklärenden Hilfetext.

#### Feldebenen-Sensibilität (`FieldTemplate.sensitivity`)

Neben der Sensibilität des Dokumentationstyps kann jedes einzelne Feld einer Feldvorlage eine **eigene** Sensibilitätsstufe erhalten (`FieldTemplate.sensitivity`). Effektiv gilt für die Sichtbarkeit des Feldes das **Maximum** aus Dokumentationstyp- und Feld-Sensibilität — ein als `HIGH` markiertes Feld bleibt für Fachkräfte unsichtbar, auch wenn der Dokumentationstyp selbst nur `NORMAL` ist.

**Verschlüsselung und Sichtbarkeit haben getrennte Wirkungen** (Refs #356): `is_encrypted` steuert die Ruhe-Verschlüsselung des Feldwerts, `sensitivity` die Sichtbarkeit im UI; eine Feldvorlage mit eigener Stufe **Hoch** erzwingt jedoch Verschlüsselung:

- Ein Feld kann verschlüsselt auf der Platte liegen, aber für `NORMAL`-Sensibilität im UI sichtbar sein (z. B. Kontaktdaten, die alle Rollen sehen dürfen, die aber nicht im Klartext gespeichert werden sollen).
- Ein nicht-verschlüsseltes Feld kann trotzdem auf Leitung/Admin beschränkt werden (z. B. statistische Marker, die sensitiv sind, aber nicht verschlüsselt gespeichert werden müssen).

#### Schutzanzeige und Stufenwechsel

Im **Feld-Board** eines Dokumentationstyps zeigt jede Feldzeile die wirksame Stufe, die zugelassenen Rollen, **Verschlüsselt** oder **Klartext** sowie **Im Suchindex** oder **Nicht im Suchindex**. Bei Vererbung steht **wie Typ**, bei einer höheren Feldstufe **Feldvorlage:** mit der Eigenstufe; die Detailseite zählt höher eingestufte Felder. Die Feldvorlagen-Liste zeigt die Eigenstufe und Speicherung sowie den Indexstatus je zugeordnetem Typ; ohne Zuordnung gibt es keinen Suchindex.

Im Feld-Board lassen sich Gruppen und Felder auch per Tastatur mit **Nach oben** und **Nach unten** sortieren. Bei Feldern wählt **Verschieben nach** die Zielgruppe oder **Ohne Gruppe**; **Verschieben** bestätigt die Auswahl. Nach erfolgreicher Änderung kehrt der Fokus zum Bedienelement zurück und die neue Position wird angesagt. Ziehen bleibt zusätzlich möglich.

Ein Typ mit Stufe **Hoch** darf keine erbende Feldvorlage enthalten: Jede Zuordnung braucht eine ausdrückliche Feldstufe. Die Konfiguration und der Paket-Loader weisen Vererbung in Hoch-Typen ab. In den ausgelieferten Paketen erhalten Berichtsfelder in Hoch-Typen die Feldstufe **Erhöht** und bleiben für die Statistik unverschlüsselt; die übrigen Felder erhalten **Hoch** und werden verschlüsselt. Eine hohe **Typstufe** allein verschlüsselt nicht sämtliche Feldwerte.

Beim Speichern einer geänderten Typstufe oder einer geänderten Feldstufe/Verschlüsselung sowie beim Zuordnen oder Wiederzuordnen einer Feldvorlage werden die betroffenen Ereignisse, Personen und Fälle derselben Einrichtung neu gespeichert, einschließlich Datensätzen im Papierkorb. Dies gilt für die Verwaltungsformulare, den normalen Modell-Speicherpfad im Django-Admin und den Paket-Loader. Beim Schreiben dynamischer Werte oder ihres Suchindex halten die unterstützten Modell- und Bulk-Pfade innerhalb der unten beschriebenen Grenzen gemeinsame Schutzsperren bis zum Schreiben; Schutzwechsel sperren exklusiv und entscheiden erst aus dem gesperrten Datenbankzustand. Konfiguration, Feldverschlüsselung und Ereignis-Suchindex ändern sich in **derselben Transaktion**; scheitert das Neuschreiben, bleibt kein halber Wechsel zurück. Eine gemeinsam verwendete Feldvorlage wirkt dabei auf alle zugeordneten Typen der Einrichtung. Verschlüsselte Felder speichern auch **Nein (`False`)** und **0** verschlüsselt; neu verschlüsselte Boolean- und Ganzzahlwerte behalten beim Entschlüsseln ihren Datentyp. Bloßes Herabstufen entschlüsselt keine bereits verschlüsselten Werte; bleibt die Verschlüsselung der Vorlage aktiviert, bleiben auch neue Werte verschlüsselt.

**Grenzen der Modell-Speicheraufrufe:** Bei Ereignis, Person und Fall weist `save(update_fields=…)` einen Aufruf mit `document_type` oder `document_type_id` mit `ValueError` ab, wenn `data_json` fehlt; bei Ereignissen muss zusätzlich `search_text` enthalten sein. Die Abweisung gilt auch bei leerer Payload oder reinen Datei-Markern und erfolgt vor dem Schreiben. Ein vollständiges `save()` auf einer ohne zurückgestellte Felder geladenen Instanz oder ein Teilaufruf mit allen erforderlichen Spalten bleibt möglich. **Instanzen mit zurückgestellten Feldern (`defer`/`only`) werden bei `save()` ohne ausdrückliches `update_fields` mit `ValueError` vor jedem Feldzugriff und Schreiben abgewiesen.** Laden Sie die Instanz vollständig neu oder geben Sie die zu speichernden Felder ausdrücklich an; für einen Typwechsel gelten dabei die oben genannten erforderlichen Spalten. Die Manager von Ereignis, Person und Fall weisen `bulk_create(update_conflicts=True, …)` mit `ValueError` ab, auch bei vollständigen `update_fields`, positionalen Optionen und leeren Batches. Reine Bulk-Anlagen bleiben möglich; bei `ignore_conflicts=True` wird eine bereits vorhandene Zeile nicht aktualisiert. Der geschützte `bulk_update`-Pfad bleibt ebenfalls verfügbar. Eigene Schreibwege über Roh-SQL oder `QuerySet.update()` auf Typ, Feldwerten oder Suchtext umgehen die Modellhooks und sind von dieser Schutz-Zusage nicht erfasst. Massenänderungen an der Konfiguration (`DocumentType`, `FieldTemplate` oder ihren Zuordnungen per `QuerySet.update()`, `bulk_*` oder SQL) umgehen die Hoch-Typ-Regel, Umschlüsselung und Indexneubau der Einzel-Speicherpfade. Konfiguration deshalb nur über die Administration oder den Paket-Loader ändern; nach solchen Massenänderungen mit RLS-Bypass zuerst `python manage.py reencrypt_fields --dry-run` und `python manage.py rebuild_search_text --dry-run` prüfen, dann `python manage.py reencrypt_fields` und `python manage.py rebuild_search_text` ausführen. Diese Wartungsläufe ersetzen nicht die Validierung der Hoch-Typ-Regel.

Auch Dokumentationstypen, Feldvorlagen, Feldzuordnungen, Feldgruppen, Feldbedingungen und Quick-Templates weisen implizites `save()` auf per `defer`/`only` unvollständig geladenen Instanzen vor dem ersten nachladenden Feldzugriff ab; vollständig geladene Instanzen oder ausdrückliche `update_fields` bleiben zulässig.

Über die unterstützten Anwendungsschreibwege gepflegte Ereignisindizes enthalten nur unverschlüsselte Felder, die nach Typ- **und** Feldstufe wirksam **Normal** sind. Ein Wechsel auf Erhöht/Hoch über diese Anwendungswege entfernt die betroffenen Feldwerte aus dem Index, auch wenn sie für Berichte weiter im Klartext gespeichert werden. Die Pseudonymsuche bleibt im Rahmen der Rollenrechte erhalten.

Neue Vorher-/Nachher-Fassungen werden beim Anlegen nach der aktuellen Feldverschlüsselung geschützt. Ältere gespeicherte Fassungen werden weiterhin getrennt mit **`reencrypt_fields`** nachverschlüsselt: Maßgeblich sind verschlüsselte Feldvorlagen derselben Einrichtung oder der gespeicherte Verschlüsselungsstatus in den Historienmetadaten, auch bei nicht mehr zugeordneten Feldern. Redaktionsmarker und Datei-Metadaten bleiben erhalten; `--dry-run` schreibt nichts. Der Lauf kann wiederholt werden, ohne Werte doppelt zu verschlüsseln; vorhandene Chiffrate werden weiterhin rotiert. Ausführung mit der Admin-DB-Rolle: siehe § 2.9 „Schlüssel-Rotation“.

**Suchtext separat wiederaufbauen:** `rebuild_search_text` schreibt ausschließlich den abgeleiteten Ereignisindex, keine Feldwerte, Zeitstempel oder Historie. Ohne `--facility` verarbeitet es alle Einrichtungen, mit `--facility <UUID>` nur die genannte; `--dry-run` zeigt die Anzahl der Änderungen ohne zu schreiben. Ein weiterer Schreibdurchlauf meldet bei unveränderten Daten keine Indexänderungen. Wie die Feldrotation benötigt der Befehl einen RLS-Bypass-Kontext; im Produktivstack verwenden Sie den Wartungsservice:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance rebuild_search_text --dry-run
docker compose -f docker-compose.prod.yml run --rm maintenance rebuild_search_text --facility <UUID>
```

#### Löschschutz bei bestehenden Daten

Felder, zu denen bereits Werte in Ereignissen, Personen oder Fällen existieren, können **nicht ohne Weiteres gelöscht** werden. Beim Versuch, eine Feldvorlage zu entfernen, greift ein Schutzmechanismus, der auf gespeicherte Werte prüft. Wird das Feld dennoch benötigt zu entfernen, muss vorher eine Daten-Migration laufen, die die Werte aufräumt oder in ein anderes Feld überführt.

> **Pragmatische Alternative:** Statt zu löschen, die Feldvorlage deaktivieren (Schalter **Aktiv** an der Feldvorlage; die Zuordnung zum Dokumentationstyp hat kein eigenes Aktiv-Flag) — bestehende Daten bleiben erhalten, neue Ereignisse/Personen/Fälle bekommen das Feld nicht mehr angeboten.

##### Verwaiste Feldwerte („Nicht mehr zugeordnet")

Wird lediglich die **Zuordnung** einer Feldvorlage zu einem Dokumentationstyp entfernt (nicht die Feldvorlage selbst gelöscht), bleiben die bereits erfassten Werte unter ihrem Slug in den Datensätzen (`data_json`) gespeichert. Solche Werte gelten als **verwaist**: Sie werden bei Personen, Fällen und Ereignissen in der Detailansicht in einer abgesetzten Sektion **„Nicht mehr zugeordnete Felder"** angezeigt und im DSGVO-Auskunfts-Export (Art. 15) unter einem eigenen Abschnitt ausgewiesen — sie verschwinden also nicht unbemerkt. Sichtbarkeit und Verschlüsselung eines verwaisten Werts richten sich weiterhin nach der zugehörigen Feldvorlage (die durch den Löschschutz erhalten bleibt, solange Daten unter ihrem Slug existieren): Ein einst als „hoch"/„erhöht" eingestuftes Feld bleibt auch verwaist entsprechend eingeschränkt sichtbar.

Sollen verwaiste Werte endgültig **entfernt** werden (Datenminimierung, sobald sie fachlich nicht mehr gebraucht werden), dient der Wartungsbefehl **`purge_orphan_field_data`**. Er entfernt bei Personen, Fällen und Ereignissen genau die `data_json`-Schlüssel, die nicht (mehr) zur aktuellen Feldzuordnung ihres Typs gehören; zugeordnete Werte (auch verschlüsselte) bleiben unangetastet, Datensätze ohne Dokumentationstyp werden nur gezählt. `--dry-run` zeigt die Wirkung ohne zu schreiben. Wie die übrigen Wartungskommandos (z. B. `reencrypt_fields`, § 2.9) muss er mit der **Admin-DB-Rolle** laufen und bricht sonst mit einer klaren Meldung ab (Row-Level-Security). Ausführung: siehe `docs/ops-runbook.md` § 3.5.

**Feldtyp einer belegten Vorlage:** Sobald der Feld-Slug in gespeicherten Personen-, Fall- oder Ereignisdaten (einschließlich Papierkorb), einer Ereignishistorie oder einem Quick-Template vorkommt, ist nur ein kompatibler Wechsel zwischen **Text** und **Textbereich** erlaubt. Mehrzeilige oder nicht lesbare Textbereiche dürfen nicht auf einzeiligen Text wechseln. Das gilt auch für verschlüsselte Werte und Dateimarker sowie für gespeicherte leere Werte. Andere Typwechsel werden abgewiesen, ohne die vorhandenen Werte zu ändern. Deaktivieren Sie stattdessen die alte Vorlage und legen Sie eine neue an. Ohne solche Belegung bleibt der Feldtyp bearbeitbar; Paketaktualisierungen behalten ihre strengere Regel des unveränderlichen Feldtyps.

**Aktivierung und Berichtsschlüssel:** Je Dokumentationstyp darf höchstens eine aktive Feldvorlage denselben Berichtsschlüssel verwenden. Die Reaktivierung einer ersetzten Vorlage wird bei Konflikt abgewiesen; die Meldung nennt Dokumentationstyp und konkurrierende Vorlage. Auch eine administrative Sammelaktivierung prüft alle ausgewählten Vorlagen einschließlich Konflikten untereinander, bevor sie etwas aktiviert. Deaktivieren und konfliktfreie Reaktivierung bleiben möglich.

**Deaktivierte Feldvorlagen:** Inaktive Vorlagen werden in den Fachformularen für Ereignisse, Personen und Fälle sowie in Offline-Formdefinitionen nicht als Eingabefelder angeboten. Beim normalen Bearbeiten bleiben ihre gespeicherten Werte unverändert erhalten, einschließlich verschlüsselter Werte und Dateimarker; ihre bestehenden Sichtbarkeitsregeln gelten weiter. Nach konfliktfreier Reaktivierung ist das Feld wieder bearbeitbar. Bedingungen mit inaktiver Quelle oder inaktivem Ziel entfallen aus dem Eingabeformular. Entfallen alle Bedingungen eines aktiven Pflichtfelds, gilt es wieder als unbedingt erforderlich; ein inaktives Pflichtfeld blockiert das Speichern nicht. Berichte zählen nur aktive Felder; Werte deaktivierter Felder bleiben im Datensatz, in Auskunft und Export erhalten und werden nach konfliktfreier Reaktivierung wieder in Berichten gezählt. Genehmigte Berichtigungen können auch inaktive Personenfelder korrigieren; erst nach Prüfung des tatsächlich gespeicherten Zielwerts werden Antrag und Audit als genehmigt geschrieben.

### 2.6 Auswahloptionen verwalten (Feldvorlagen)

Unter **Verwaltung → Feldvorlagen** können Sie die Optionen von Auswahl- und Mehrfachauswahl-Feldern (Select / Multi-Select) bearbeiten. Die Optionen werden im Feld **Optionen (JSON)** als JSON-Array gespeichert.

**Slug-Format:** Options-Slugs bestehen aus Kleinbuchstaben `a–z`, Ziffern `0–9` und einzelnen Bindestrichen zwischen Zeichenfolgen, zum Beispiel `beratung`, `18-26` oder `beratung-2`. Ungültig sind `-beratung` (führender Bindestrich), `beratung--hilfe` (doppelte Bindestriche) und `beratung-` (abschließender Bindestrich). Options-Slugs müssen innerhalb eines Feldes eindeutig sein. Dokumentationstypen und Feldvorlagen erhalten ihren Slug automatisch aus dem Namen; Feldgruppen ebenfalls. Umlaute werden zu `ae`, `oe`, `ue` und `ss`, Unterstriche zu Bindestrichen, mehrfach aufeinanderfolgende Bindestriche zusammengezogen und der Slug auf höchstens 120 Zeichen gekürzt. Bei Kollisionen wird ein Suffix wie `-2` angehängt. Namen, aus denen kein Slug entsteht, werden abgewiesen; reine Ziffernnamen wie `18-26` sind gültig. Feldvorlagen dürfen außerdem keine reservierten Slugs fester Formularfelder verwenden. Slugs von Dokumentationstypen und Feldvorlagen sind je Einrichtung eindeutig; Feldgruppen sind je Dokumentationstyp eindeutig. Details zum Paketformat stehen in der [Paketdokumentation](packages.md).

Im Options-Editor können Sie Slugs direkt eingeben. Verwenden Sie nur `a–z`, `0–9` und einzelne Bindestriche.

**Verschlüsselte Auswahlfelder:** Bei neu gespeicherten Mehrfachauswahlen bleibt die Liste der Options-Slugs beim Verschlüsseln, Lesen und erneuten Speichern erhalten. Bereits ausgewählte deaktivierte Optionen bleiben im Bearbeitungsformular von Personen, Fällen und Ereignissen angehakt und können unverändert gespeichert werden; nicht ausgewählte deaktivierte Optionen werden dort nicht angeboten. Die Verschlüsselung ändert die rollenabhängige Sichtbarkeit nicht.

Die Detailansichten zeigen die Bezeichnungen einer Mehrfachauswahl jeweils auf einer eigenen Zeile. Der Ereignis-CSV-Export verwendet kommagetrennte Bezeichnungen; die Art.-15-PDF-Auskunft zeigt ebenfalls Bezeichnungen, während das Ereignis-JSON der Art.-20-Auskunft eine echte Liste der Options-Slugs enthält. Unbekannte Slugs ohne passende Bezeichnung bleiben als Rohwert lesbar. Bestehende Texttokens werden nicht nachträglich als Listen interpretiert; eine Umwandlung alter Daten erfolgt nicht. Refs #2520.

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
| `slug` | Technischer Bezeichner; innerhalb dieses Feldes eindeutig |
| `label` | Anzeigename in Formularen und Exporten |
| `is_active` | `true` = wählbar, `false` = deaktiviert |

Jedes Optionsobjekt darf nur `slug`, `label` und optional `is_active` enthalten; unbekannte Schlüssel werden abgewiesen.

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

> **Wichtig:** Wenn ein Options-Slug in auswertbaren gespeicherten Werten einschließlich Papierkorb und Ereignishistorie, Feldbedingungen, Schnellvorlagen oder einem weiterhin gültigen Default verwendet wird, weisen Konfigurationsformular, Modell-Save, Django-Admin und Paketlader sein Umbenennen oder Entfernen ab. Setzen Sie stattdessen `"is_active": false`; die Bezeichnung darf weiterhin geändert werden. Nicht verwendete Options-Slugs können entfernt werden. Ereignis-, Personen- und Fall-Schreibpfade einschließlich der geschützten Modell- und Bulkwege, Schnellvorlagen beim Modell-Save sowie Feldbedingungen über die Konfigurationsservices und den Modell-Save prüfen neue Auswahlreferenzen unter einer gemeinsamen Konfigurationssperre gegen den aktuellen Optionsbestand; unveränderte bestehende Auswahlwerte bleiben speicherbar. Direkte `QuerySet.update`- und Bulk-Schreibwege für Schnellvorlagen und Feldbedingungen sowie beliebige SQL-Updates sind davon nicht abgesichert; beschädigte verschlüsselte Werte können nicht als Verwendung ausgewertet werden.

#### Zahlenbereichsgrenzen (`Minimalwert`/`Maximalwert`)

Bei Feldvorlagen vom Typ **Zahl** lassen sich an derselben Stelle (**Verwaltung → Feldvorlagen**) optionale Grenzen setzen: **Minimalwert** und **Maximalwert**. Sie wirken **ausschließlich auf künftige Schreibvorgänge** — Neuanlage, Bearbeiten und Speichern über Formular oder Dienst-Schicht. Eine Verschärfung schreibt keinen einzigen bereits gespeicherten Wert um: Bestandsdaten außerhalb der neuen Grenze bleiben unverändert in der Datenbank stehen, bis sie fachlich korrigiert werden.

> **Vor jeder Verschärfung: schreibfreies Inventar ausführen.** Im Dienst `maintenance` (Admin-DB-Rolle):
>
> ```bash
> docker compose -f docker-compose.prod.yml run --rm maintenance inventory_number_ranges --facility <UUID-oder-Name>
> ```
>
> Das Kommando verändert nichts und listet je Fund nur Entitätsart (Personen/Fälle/Ereignisse), Dokumentationstyp, Feld-Slug und Anzahl — nie den gespeicherten Wert selbst. „Keine Werte außerhalb konfigurierter Zahlenbereiche gefunden" heißt: Der Bestand des betroffenen Feldes liegt bereits innerhalb der geplanten Grenze, die Verschärfung ist gefahrlos.

**Meldungen des Inventars:**

| Meldung | Bedeutung |
|---|---|
| `unter Minimalwert X` / `über Maximalwert X` | Ein gespeicherter Wert liegt außerhalb der geplanten Grenze. |
| `keine Zahl` | Sammelbefund für drei unterscheidbare, aber bewusst gleich gemeldete Fälle: ein leerer **Pflicht**wert, ein nicht-numerisch gespeicherter Wert, oder ein Entschlüsselungsfehler (beschädigtes/fremdes Chiffrat). Ein Entschlüsselungsfehler wird **immer** gemeldet, auch an einem optionalen Feld. |
| *(kein Eintrag)* | Ein leerer Wert an einem **optionalen** Feld ist kein Fund — „kein Wert" ist dort ein gültiger Zustand, keine Bereichsverletzung. |

Ein Fund ist kein Reparaturauftrag an das Inventar selbst: Es zählt nur, es schreibt nichts. Bereinigen Sie gemeldete Datensätze fachlich — öffnen Sie den betroffenen Datensatz und tragen Sie einen gültigen Wert ein bzw. leeren Sie ein optionales Feld —, bevor Sie die Grenze verschärfen.

#### Speicher-Blockverhalten bei bestehenden Bestandswerten

Ist eine Grenze verschärft, prüft der zentrale Validator **jeden** Schreibvorgang — auch das Bearbeiten eines Datensatzes, dessen Zahlenfeld gar nicht angefasst wurde: Ein Formular übermittelt beim Speichern immer alle aktuellen Feldwerte, nicht nur die tatsächlich geänderten. Liegt der gespeicherte Bestandswert außerhalb der neuen Grenze, weist das Formular ihn wie einen regulären Eingabefehler zurück (**„Dieser Wert muss größer oder gleich X sein."**/**„Dieser Wert muss kleiner oder gleich X sein."**, X = der konfigurierte Minimal-/Maximalwert), und das **gesamte** Speichern wird verweigert — auch wenn die eigentlich beabsichtigte Änderung ein ganz anderes Feld betraf. Das ist die vorgesehene Wirkung des Validators, kein Fehler: Er lässt sich nur auflösen, indem der betroffene Wert korrigiert (oder, sofern das Feld optional ist, geleert) wird. Betroffen ist jeweils nur der einzelne blockierte Datensatz — andere Datensätze desselben Feldes bleiben unabhängig davon bearbeitbar.

#### Grenze sicher zurücknehmen

Erweist sich eine Grenze als zu eng, nehmen Sie sie an derselben Stelle zurück: **Minimalwert**/**Maximalwert** an der Feldvorlage lockern oder leeren. Das wirkt sofort auf künftige Schreibvorgänge; zuvor blockierte Datensätze lassen sich wieder normal speichern, sobald ihr Bestandswert innerhalb der (jetzt weiteren) Grenze liegt — ohne dass dafür ein Wert nachträglich verändert werden müsste. Pflegen Sie die Grenze stattdessen über ein Konfigurationspaket, gilt dort ein eigenes Tri-State-Verhalten (fehlender Schlüssel lässt eine bestehende Grenze unangetastet, erst ein explizites `null` entfernt sie) — siehe [docs/packages.md](packages.md).

### 2.6b Fuzzy-Suche (pg_trgm)

Die globale Suche nach Personen (Pseudonymen) nutzt zusätzlich zur exakten Teilstring-Suche eine **Trigramm-basierte Fuzzy-Suche** — tolerant gegenüber Tippfehlern und phonetischen Varianten (z. B. „Schmidt" ↔ „Schmitt"). Implementierung: [`src/core/services/dashboard/search.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/dashboard/search.py), Funktion `search_similar_clients`.

**Schwelle pro Einrichtung:** Unter **Verwaltung → Einstellungen** (Gruppe „Allgemein und Komfort“) → Feld **„Fuzzy-Search-Schwelle"** (`Settings.search_trigram_threshold`):

| Wertebereich | Default | Wirkung |
|---|---|---|
| `0.0`–`1.0` | `0.3` | Mindest-Ähnlichkeit für Treffer. Kleinere Werte liefern **mehr**, aber **ungenauere** Treffer; größere Werte sind strenger. |

**Empfehlung:** Bei zu vielen Fehltreffern schrittweise erhöhen (0.35, 0.4); bei zu wenigen Treffern schrittweise senken (0.25, 0.2).

#### Voraussetzung: `pg_trgm`-Extension

Die PostgreSQL-Extension `pg_trgm` muss aktiviert sein. Beim Standard-Deployment wird sie automatisch per Django-Migration eingerichtet. Falls sie (z. B. nach einem manuellen DB-Restore) fehlt, manuell nachholen:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

Referenzen: #536, #581.

### 2.7 Zwei-Faktor-Authentifizierung (2FA)

**Audit (#2527).** Erfolgreiche TOTP- und Passkey-Verifikation erzeugt `MFA_VERIFIED` mit der festen Methode `totp` oder `passkey`; Fehlgeschlagene Faktorprüfungen behalten `MFA_FAILED` bzw. `WEBAUTHN_FAILED`. Der Backup-Code-Verbrauch behält genau seine bestehende `BACKUP_CODES_USED`-Zeile. Für angemeldete Konten erzeugt eine wegen ungültigem TOTP, unverifizierter Sitzung oder fehlendem TOTP-Gerät abgewiesene POST-Anfrage zum Erneuern der Backup-Codes `MFA_BACKUP_REGENERATE_DENIED` mit dem festen Grund `invalid_totp`, `unverified_session` oder `missing_totp`. Codes, TOTP-Secrets und Passkey-Assertions stehen nicht in diesen Audit-Details.

Die Anlaufstelle unterstützt TOTP-basierte 2FA über [`django-otp`](https://django-otp-official.readthedocs.io/), ergänzt um Passkeys und Backup-Codes. Jeder Benutzer kann 2FA selbst aktivieren (`/mfa/settings/`); zusätzlich gibt es drei Verpflichtungs-Ebenen.

**Benutzerseitige Doku:** [User-Guide § 1 — Zwei-Faktor-Authentifizierung](user-guide.md#zwei-faktor-authentifizierung-2fa).

#### Erzwingung konfigurieren

| Ebene | Feld | Ort im Admin | Wirkung |
|---|---|---|---|
| **Einzeluser** | `User.mfa_required` | Verwaltung → Zugänge → *Zugang* → Feld „2FA erforderlich" | 2FA sofort verpflichtend für diesen User, Deaktivieren gesperrt |
| **Einrichtungsweit** | `Settings.mfa_enforced_facility_wide` | Verwaltung → Einstellungen (`/verwaltung/einstellungen/`) → Feld „2FA-Pflicht für alle Nutzer" | 2FA sofort verpflichtend für **alle** User dieser Einrichtung |
| **Rollen-Default** | `MFA_ENFORCE_PRIVILEGED_ROLES` (Settings-Modul) + `MFA_ENFORCED_ROLES_IMMEDIATE` / `MFA_ENFORCED_ROLES_GRACE` (Umgebungsvariablen) | Deployment | 2FA verpflichtend allein über die Rolle — in Produktion ab Werk für alle fünf Rollen aktiv |

Die Auswertung erfolgt über [`User.is_mfa_enforced`](https://github.com/anlaufstelle/app/blob/main/src/core/models/user.py) — User gilt als verpflichtet, wenn **eine** der Ebenen zutrifft. Die MFA-Middleware blockiert den Zugriff auf geschützte Bereiche, bis der zweite Faktor (TOTP-Code, Passkey oder Backup-Code) in der Session verifiziert wurde.

**Rollen-Default über Umgebungsvariablen konfigurieren** (#1639). Unabhängig von der Einzeluser- und der einrichtungsweiten Erzwingung verlangt die Anwendung 2FA in Produktion bereits **allein über die Rolle**. Der Schalter `MFA_ENFORCE_PRIVILEGED_ROLES` ist keine Umgebungsvariable, sondern ein Wert des Settings-Moduls: `True` in `base.py` und damit in Produktion, `False` in den Entwicklungs- und Demo-Settings, in den E2E-Settings über `E2E_MFA_ENFORCE_ROLES` steuerbar. Welche Rollen er umfasst, steuern zwei per Env als **Kommaliste** überschreibbare Settings; `docker-compose.prod.yml` reicht beide an den `web`-Dienst durch (siehe [§ 1, Umgebungsvariablen-Referenz](#vollständige-umgebungsvariablen-referenz)):

| Setting | Default | Wirkung |
|---|---|---|
| `MFA_ENFORCED_ROLES_IMMEDIATE` | `super_admin,facility_admin` | 2FA **sofort** verpflichtend (kein Aufschub) — ohne zweiten Faktor sofortiger Redirect auf `/mfa/setup/` |
| `MFA_ENFORCED_ROLES_GRACE` | `lead,staff,assistant` | 2FA verpflichtend, aber erst **nach 30-tägiger Übergangsfrist** ab der ersten Anmeldung (Erinnerungsbanner ab 14 Resttagen); danach führt jede Anmeldung ohne zweiten Faktor auf `/mfa/setup/` |

Die Rolle **Leitung** (`lead`) ist per Default pflichtig (im Grace-Set) — wer Fachkraft/Assistenz erzwingt, erst recht die hierarchisch höhere Leitung. Gültige Rollennamen sind `super_admin`, `facility_admin`, `lead`, `staff`, `assistant`; eine **leere** Liste schaltet die jeweilige Ebene bewusst ab. Ein **unbekannter Rollenname** oder eine **Überschneidung** beider Listen bricht den Start fail-closed mit `ImproperlyConfigured` ab (keine stille Fehlkonfiguration).

#### 2FA für einen Benutzer zurücksetzen

Hat eine Person ihr Authenticator-Gerät verloren und keine Backup-Codes mehr, setzt die Anwendungsbetreuung oder die Systemadministration ihren zweiten Faktor in der Anwendung zurück (#2469):

- **Anwendungsbetreuung (`facility_admin`):** **Verwaltung → Zugänge** → *Zugang* → unter „Sicherheitsaktionen" **Zweiten Faktor zurücksetzen** (`/verwaltung/zugaenge/<id>/zweiter-faktor/`). Die Bestätigungsseite nennt die Folgen; erst das Absenden setzt zurück. Die Aktion erscheint nur, wenn der Zugang noch einen Faktor hat.
- **Systemadministration (`super_admin`):** Django-Admin (`/admin-mgmt/`) → **Anlaufstelle Core → Benutzer** → Konten auswählen → Aktion **Zweiten Faktor zurücksetzen (Authenticator-App, Passkeys, Backup-Codes)**. Jedes ausgewählte Konto wird einzeln geprüft; abgelehnte Konten meldet die Seite und überspringt sie.

Regeln (in [`src/core/services/user_access.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/user_access.py), `reset_user_mfa`):

- **Sudo-Pflicht:** Beide Wege verlangen ein laufendes Sudo-Fenster; ohne Sudo führt der Aufruf zuerst auf die Sudo-Seite (Passwort, bei eigenem TOTP-Gerät zusätzlich ein Code) und danach zurück zur Bestätigung.
- **Einrichtungsgrenze:** Eine Anwendungsbetreuung erreicht nur Konten der eigenen Einrichtung; ein Konto einer anderen Einrichtung ist für sie nicht auffindbar. Die Systemadministration erreicht jedes Konto.
- **Rollen-Hierarchie:** Eine Anwendungsbetreuung setzt kein `super_admin`-Konto zurück. Das eigene Konto lässt sich auf diesem Weg nicht zurücksetzen — dafür gilt das Deaktivieren unter `/mfa/settings/` mit seinen eigenen Regeln.
- **Wirkung:** Alle TOTP-Geräte (auch unbestätigte), alle Passkeys und alle Backup-Codes des Kontos werden entfernt, und alle Sitzungen des Kontos enden mit ihrer nächsten Anfrage — unabhängig von `OFFLINE_REVOCATION_SESSION_SCAN_LIMIT`, weil der gespeicherte Passwort-Hash neu geschrieben wird und Django jede Sitzung mit dem alten Hash abmeldet. Das Passwort bleibt gültig. Lassen sich die Sitzungen nicht sicher beenden, wird nichts entfernt und nichts protokolliert.
- **Neueinrichtung:** Ist 2FA für das Konto verpflichtend, führt die nächste Anmeldung auf `/mfa/setup/` — bei Einzel-, Einrichtungs- und Sofort-Rollenpflicht sofort, bei der Rollenpflicht mit Übergangsfrist erst nach deren Ablauf. Ohne Pflicht meldet sich die Person nur mit Passwort an, bis sie 2FA selbst wieder einrichtet.
- **Audit:** Jeder Reset schreibt genau einen Eintrag `MFA_RESET_BY_ADMIN` („2FA durch Administration zurückgesetzt") mit Zielkonto (Benutzer- und Zielfeld), handelnder Person, Einrichtung und Quell-IP. Das Detail enthält den Benutzernamen des Zielkontos (`username`), Kennung und Benutzernamen der handelnden Person (`actor_id`, `actor_username`) sowie die Anzahl der entfernten TOTP-Geräte, Passkeys und Backup-Codes (`totp_devices_removed`, `passkeys_removed`, `backup_codes_removed`), kein Schlüsselmaterial. Hatte das Konto keinen Faktor mehr, entsteht kein Eintrag.

Setzen Sie den zweiten Faktor nur zurück, wenn die Anfrage nachweislich von der Person selbst stammt.

Es gibt zusätzlich **Backup-Codes als zweiten Faktor** für genau diesen Recovery-Fall (Refs #588). Bei der 2FA-Einrichtung erhält der User 10 einmalig nutzbare Codes, die er ausgedruckt oder im Passwort-Manager hinterlegen sollte. Anschließend muss er über die Pflicht-Checkbox bestätigen, dass er die Codes sicher gespeichert hat; erst dann ist das TOTP-Gerät bestätigt und die Einrichtung abgeschlossen. Verlässt er die Seite vorher, führt der nächste Aufruf wieder zu `/mfa/setup/`. Am Login-2FA-Prompt kann er statt eines TOTP-Codes einen Backup-Code eingeben. Verbrauchte Codes werden invalidiert und im AuditLog (`BACKUP_CODES_USED`) protokolliert. Sind alle 10 Codes verbraucht oder verloren, bleibt das Zurücksetzen durch die Administration oben der Fallback.

`django-otp` drosselt TOTP- und Backup-Code-Prüfungen nach mehreren Fehlversuchen zunehmend. Während der daraus folgenden Wartezeit kann auch ein korrekter Code abgewiesen werden; vor einem Reset deshalb die Sperrfrist abwarten und nicht unmittelbar weiterprobieren.

**Wechsel des TOTP-Geräts** (#2529): Unter `/mfa/settings/` **Neues Gerät einrichten** wählen (`/mfa/change-device/`). Der Wechsel ist auch bei verpflichtender 2FA verfügbar. Die Sitzung muss bereits mit dem bestehenden zweiten Faktor bestätigt sein; ohne laufendes Sudo-Fenster folgt die erneute Bestätigung mit Passwort und aktuellem TOTP- oder unbenutztem Backup-Code (`SUDO_MODE_TTL_SECONDS`, Standard 15 Minuten). Dann den neuen QR-Code scannen, dessen TOTP-Code bestätigen, die neuen Backup-Codes sicher speichern und quittieren. Erst die Quittung entfernt alle bisher bestätigten TOTP-Geräte und bindet die Sitzung an das neue Gerät; vorhandene Passkeys bleiben erhalten. Der Abschluss schreibt `MFA_ENABLED` mit der Anzahl ersetzter TOTP-Geräte. Ein Backup-Code-Login allein wechselt kein Gerät. Beim freiwilligen **2FA deaktivieren** führt Sudo nun auf eine Bestätigungsseite; erst deren POST entfernt den Faktor. Die erstmalige Einrichtung bleibt ohne Sudo-Schritt; Passkey-Hinzufügen bleibt Sudo-geschützt.

**Backup-Code-Härtung:** Zehn Codes mit jeweils 128 Bit Zufall werden als 96-Bit-HMAC-Präfix gespeichert; der zweckgebundene Schlüssel wird aus `SECRET_KEY` abgeleitet. Bisher ausgestellte Codes (auch das frühere moderne Format mit SHA-Digest) sind ungültig. Vor Verlust des Geräts neue Codes mit aktuellem TOTP erzeugen und sicher verwahren; ohne nutzbaren Faktor bleibt der Admin-Reset. Auch eine `SECRET_KEY`-Rotation macht die Codes ungültig. Auf `/account/recovery/backup-code/` erhöhen anonyme Fehlversuche die Gerätedrossel nicht; stattdessen gelten 5/min je IP und 10/h je normalisiertem Benutzernamen. Die authentifizierte Gerätedrossel bleibt bestehen.

**Grenze des Sitzungsendes:** Der Scan durchsucht höchstens `OFFLINE_REVOCATION_SESSION_SCAN_LIMIT` (Standard 10.000) nicht abgelaufene Sitzungen der gesamten Installation. Bei Sperre und Rollenwechsel können nicht gefundene Sitzungen bis Inaktivitätsablauf oder absolutem Sitzungsmaximum fortbestehen; Deaktivierung und ein unbrauchbar gemachtes Initialpasswort greifen bei der nächsten Anfrage, der Admin-MFA-Reset ist scan-unabhängig. Eine Sperre rotiert den Passwort-Hash nicht, damit Passwort-Reset- und Einladungslinks erhalten bleiben. Ein Benutzer→Sitzungs-Index ist eine Idee für nach 1.0, keine vorhandene Funktion.

#### Account-Lockout

Die Sperre ([`src/core/services/security/login_lockout.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/login_lockout.py)) folgt diesen Regeln:

- **Schwelle und Fenster:** **10 fehlgeschlagene Anmeldungen innerhalb eines gleitenden 15-Minuten-Fensters** sperren das Konto. Gezählt wird je Paar aus Konto und Quell-IP: Fehlversuche von einer fremden IP-Adresse sperren die Anmeldung von der eigenen nicht.
- **Ende:** Die Sperre gilt, solange mindestens 10 gezählte Fehlversuche des Kontos von dieser Quell-IP in den letzten 15 Minuten liegen, und endet von selbst, sobald es weniger sind. Das kann deutlich früher sein als 15 Minuten nach dem letzten Fehlversuch: Verteilen sich zehn Fehlversuche über neun Minuten, endet die Sperre 15 Minuten nach dem ersten, also sechs Minuten nach dem letzten. Ohne neue gezählte Fehlversuche endet sie spätestens 15 Minuten nach dem letzten gezählten. Gezählt werden die `LOGIN_FAILED`-Einträge — falsches Passwort und, während der Sperre, auch das richtige (`reason: "locked"`); solche Versuche halten die Sperre aufrecht. Vom Ratelimit abgewiesene Anfragen (HTTP 429) erreichen die Prüfung nicht und zählen nicht. Fehlversuche vor einer Entsperrung (`LOGIN_UNLOCK`) zählen nicht mehr.
- **Anzeige und Protokoll:** Wer während der Sperre das richtige Passwort eingibt, bleibt auf der Login-Seite und sieht den Formularfehler „Ihr Konto ist nach mehreren fehlgeschlagenen Versuchen temporär gesperrt. …"; protokolliert wird das als `LOGIN_FAILED` mit `reason: "locked"`. Zugleich wird der Offline-Schlüssel des Kontos ungültig, und die Anwendung beendet die Sitzungen des Kontos, die sie in der Sitzungstabelle findet. Diese Suche ist auf `OFFLINE_REVOCATION_SESSION_SCAN_LIMIT` nicht abgelaufene Sitzungen der ganzen Installation begrenzt (Default 10 000). Liegt eine Sitzung des Kontos jenseits dieser Grenze, bleibt sie gültig, bis sie durch Inaktivität (Session-Timeout) oder die maximale Sitzungsdauer endet; das Log meldet den Abbruch auf Stufe ERROR. Eine eigene Sperr-Aktion im AuditLog gibt es nicht.
- **Ratelimits (unabhängig von der Sperre):** Die Anmeldung nimmt höchstens **5 Versuche pro Minute je IP-Adresse** und **10 Versuche pro Stunde je Benutzername** an ([`src/core/views/auth.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/auth.py), `CustomLoginView`); jede abgeschickte Anmeldung zählt, auch eine erfolgreiche. Die Zeitfenster sind fest, nicht gleitend: Das Stundenfenster endet zu einem festen Zeitpunkt, nicht eine Stunde nach dem ersten Versuch. Darüber antwortet die Anmeldung mit HTTP 429, bis das Zeitfenster abgelaufen ist; keine Entsperrung hebt ein Ratelimit auf. Eine 429-Antwort schreibt keinen Audit-Eintrag und zählt nicht als Fehlversuch der Sperre. Liegen zehn Fehlversuche im selben Stundenfenster, bekommt danach jede Anmeldung mit diesem Benutzernamen bis zum Ende des Fensters die 429-Antwort. Ob anschließend der Formularfehler der Sperre erscheint, hängt allein davon ab, ob dann noch zehn gezählte Fehlversuche in den letzten 15 Minuten liegen; endet das Stundenfenster mehr als 15 Minuten nach dem letzten gezählten Fehlversuch, ist die Sperre bereits vorbei.
- **Ausnahme `super_admin`:** Diese Rolle wird nie gesperrt (das Break-Glass-Konto soll nicht gezielt ausgesperrt werden können); gegen Brute-Force schützen dort allein die Ratelimits. Eine Entsperrung ist für sie wirkungslos und schreibt keinen Audit-Eintrag.

Die folgenden Recovery-Pfade gelten deshalb für facility-gebundene Konten:

**Pfad A — UI (für facility-gebundene User):** Die Anwendungsbetreuung (`facility_admin`) der Einrichtung kann den Account ihrer eigenen User entsperren:

1. **Verwaltung → Zugänge** → betroffenen User der eigenen Einrichtung auswählen.
2. Unter „Sicherheitsaktionen" auf **Account-Sperre aufheben** klicken.
3. Cleanup wird im AuditLog als `LOGIN_UNLOCK` protokolliert (das `LOGIN_FAILED`-Log selbst ist dank `auditlog_immutable`-DB-Trigger unveränderbar).

Die Aktion verlangt ein laufendes Sudo-Fenster. Die Systemadministration entsperrt einrichtungsübergreifend unter **System → Sperrkonten** (`/system/lockouts/`, ebenfalls mit Sudo). Im Django-Admin (**Anlaufstelle Core → Benutzer**) steht dieselbe Entsperrung nur noch als Listenaktion „Account-Sperre aufheben (Login freigeben)" als Ausweichweg bereit.

**Pfad B — CLI (für Recovery):** Wenn kein anderer User Zugriff auf Pfad A hat (gesperrter `facility_admin` und kein zweiter `facility_admin`), entsperrt der Hosting-Operator den Account direkt am Server:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance \
  unlock <username>
```

Der Befehl ist auf die facility-gebundenen Rollen anwendbar (`facility_admin`, `lead`, `staff`, `assistant`) und schreibt einen `LOGIN_UNLOCK`-AuditLog-Eintrag. Für einen `super_admin` ist er wirkungslos (Erfolgsmeldung ohne Audit-Eintrag), da diese Rolle nie gesperrt ist.

**Pfad C — Self-Service per E-Mail (Refs #869):** Hat der User eine E-Mail-Adresse hinterlegt, kann er sich auf der Login-Seite über die Links „Konto entsperren per E-Mail" oder „Passwort vergessen?" selbst entsperren — beide Pfade schreiben einen `LOGIN_UNLOCK`-AuditLog. Der dedizierte Entsperr-Link unter `/account/recovery/` verschickt einen 30-Minuten-gültigen, signierten Token (kein neues Modell, `TimestampSigner` mit Salt `lockout-recovery.v1`). Anti-Enumeration: Die Bestätigungsseite ist identisch, ob die E-Mail einem Konto zugeordnet ist oder nicht.

**Pfad D — Self-Service per MFA-Backup-Code:** Hat der User 2FA aktiviert und einen seiner Backup-Codes notiert, kann er sich unter `/account/recovery/backup-code/` mit Username + Code entsperren. Audit-Trail: `LOGIN_UNLOCK` (Trigger `backup_code`) + `BACKUP_CODES_USED` (Flow `lockout_recovery`). Jeder Code ist einmalig — der nächste Recovery-Vorgang verbraucht den nächsten Code.

Im AuditLog stehen jeder geprüfte Fehlversuch (`LOGIN_FAILED`, während der Sperre mit richtigem Passwort zusätzlich `reason: "locked"`) und jede Entsperrung (`LOGIN_UNLOCK`). Der Beginn der Sperre ergibt sich aus den `LOGIN_FAILED`-Einträgen und schreibt keinen eigenen Eintrag. Vom Ratelimit abgewiesene Anfragen (HTTP 429) erhalten eine gedrosselte `RATE_LIMITED`-Spur, auch während der Sperre. Nutzen Sie den Filter „Anmeldung fehlgeschlagen" / „Sperre aufgehoben" für eine retroaktive Auswertung. Der `LOGIN_UNLOCK`-Detail-Block enthält ein `trigger`-Feld: `admin` (Pfad A, System → Sperrkonten, Django-Admin-Aktion und CLI-Befehl aus Pfad B — die CLI schreibt keinen eigenen Wert), `password_reset` (Pfad C via Standard-Reset), `invite_setup` (Ersteinrichtung über den Einladungslink), `recovery_token` (Pfad C via dedizierter Link), `backup_code` (Pfad D); Entsperrungen per Passwort-Reset, Einladungslink, Recovery-Token oder Backup-Code tragen außerdem die Quell-IP am Audit-Eintrag.

#### Audit-Spur

Diese 2FA- und Sudo-Ereignisse schreiben je einen Eintrag im `AuditLog`:

- `MFA_ENABLED` — Abschluss der TOTP-Einrichtung mit der Quittung der Backup-Codes (`count` = Zahl der dabei ersetzten TOTP-Geräte).
- `MFA_DISABLED` — Deaktivieren unter `/mfa/settings/`, wenn dabei ein TOTP-Gerät oder Passkey entfernt wurde.
- `MFA_RESET_BY_ADMIN` — Zurücksetzen durch die Administration (siehe oben).
- `MFA_FAILED` — falscher TOTP- oder Backup-Code am 2FA-Login.
- `MFA_VERIFIED` — erfolgreiche TOTP- oder Passkey-Prüfung am 2FA-Login, mit Methode ohne Geheimnisse.
- `MFA_BACKUP_REGENERATE_DENIED` — abgewiesene Backup-Code-Erneuerung wegen unverifizierter Sitzung, fehlendem TOTP-Gerät oder ungültigem TOTP-Code.
- `BACKUP_CODES_GENERATED` — Backup-Codes bei der Einrichtung erzeugt; `BACKUP_CODES_REGENERATED` — neu erzeugt unter `/mfa/settings/`; `BACKUP_CODES_USED` — Backup-Code am 2FA-Login oder bei der Entsperrung eingelöst.
- `WEBAUTHN_REGISTERED`, `WEBAUTHN_REMOVED` — Passkey hinzugefügt bzw. entfernt; `WEBAUTHN_FAILED` — fehlgeschlagene Passkey-Prüfung.
- `SUDO_MODE_ENTERED`, `SUDO_MODE_FAILED` — Sudo-Bestätigung gelungen bzw. am Passwort oder zweiten Faktor gescheitert.

Vom Ratelimit abgewiesene Anfragen (HTTP 429) schreiben höchstens eine `RATE_LIMITED`-Zeile je Pfadklasse, bekanntem Konto und Einrichtung innerhalb von 60 Sekunden; anonyme Anfragen teilen den Bucket unabhängig von der Quell-IP. Nutzen Sie den Filter unter **Datenschutz → Audit-Log**, um fehlgeschlagene Verifikationsversuche oder Aktivierungs-/Deaktivierungsereignisse auszuwerten.

#### Relevante Dateien

- Modelle: [`src/core/models/user.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/user.py), [`src/core/models/settings.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py)
- Views: [`src/core/views/mfa.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/mfa.py)
- Middleware: [`src/core/middleware/`](https://github.com/anlaufstelle/app/tree/main/src/core/middleware)

### 2.8 Schnell-Vorlagen (Quick-Templates)

**Schnell-Vorlagen** sind vorbefüllte Dokumentvorlagen für wiederkehrende Muster (z. B. „Beratungsgespräch 30 Min", „Standard-Check-in"). Fachkräfte wenden sie per Klick auf der Seite „Kontakt dokumentieren" an.

**Benutzerseitige Doku:** [User-Guide § 3 — Schnell-Vorlagen](user-guide.md#schnell-vorlagen-quick-templates).

#### Vorlagen verwalten

Vorlagen werden im Django-Admin unter **Anlaufstelle Core → Quick-Templates** gepflegt.

| Feld | Beschreibung |
|---|---|
| `facility` | Mandantenisolation — jede Vorlage gehört zu genau einer Einrichtung |
| `document_type` | Dokumentationstyp, der beim Anwenden vorausgewählt wird |
| `name` | Anzeigename auf dem Button (z. B. „Beratungsgespräch 30 Min") |
| `prefilled_data` | JSON-Objekt `{slug: wert}` — mappt Feld-Slugs auf Standardwerte |
| `sort_order` | Reihenfolge der Buttons auf „Kontakt dokumentieren" |
| `is_active` | Nur aktive Vorlagen erscheinen zur Anwendung |
| `created_by` | User, der die Vorlage angelegt hat (nur informativ) |

#### `prefilled_data` — Filterregeln

Der Service-Layer wendet vor dem Speichern und erneut beim Anwenden eine **Whitelist** an ([`src/core/services/dashboard/quick_templates.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/dashboard/quick_templates.py)). Werte bleiben nur erhalten, wenn:

- der Slug zum gewählten Dokumentationstyp gehört,
- die **effektive Sensitivität** des Feldes `NORMAL` ist (kein Prefill für `ELEVATED`/`HIGH`),
- das Feld nicht verschlüsselt ist (`is_encrypted=False`) und kein `FILE`-Feld ist,
- bei `SELECT`/`MULTI_SELECT` der Wert einer **aktiven** Option des aktuellen FieldTemplates entspricht.

Dadurch sind Vorlagen **selbstheilend**: Wird eine Auswahl-Option später deaktiviert, müssen Vorlagen nicht migriert werden — der veraltete Wert wird beim nächsten Anwenden stillschweigend verworfen.

#### Rollen- und Sensitivitäts-Sichtbarkeit

Schnell-Vorlagen werden pro User über [`user_can_see_document_type`](https://github.com/anlaufstelle/app/blob/main/src/core/services/compliance/sensitivity.py) gefiltert. Eine Assistenz, die `ELEVATED`/`HIGH`-Dokumentationstypen nicht sehen darf, sieht dafür auch keine Vorlagen. So taucht keine Vorlage in der Button-Liste auf, die der User anschließend gar nicht anwenden könnte.

#### Operative Hinweise

- Vorlagen sind ein **Bequemlichkeits-Layer**, keine Datenquelle — das Ereignis wird weiter explizit vom User gespeichert, alle Felder bleiben editierbar.
- Anwenden einer Vorlage **überschreibt bestehende Werte nicht**; gefüllt werden nur leere Felder.
- Aktuell gibt es **keine Custom-UI** für die Vorlagen-Verwaltung außerhalb des Django-Admin. Nur User mit der Rolle `facility_admin` können Vorlagen pflegen (in der eigenen Einrichtung).

#### Relevante Dateien

- Modell: [`src/core/models/quick_template.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/quick_template.py)
- Service: [`src/core/services/dashboard/quick_templates.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/dashboard/quick_templates.py)
- View-Integration: [`src/core/views/events.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/events.py) (`EventCreateView`)
- Tracking-Issue: #494

### 2.9 Encrypted File Vault & Virus-Scanning

Dateianhänge (Fotos, Scans, Dokumente) an **Ereignissen, Personen und Fällen** ([ADR-039](adr/039-owner-generische-dateianhaenge.md), Refs #1738 — je Datei genau ein Owner) werden in einem **verschlüsselten Vault** abgelegt: vor dem Schreiben in `MEDIA_ROOT` auf Viren geprüft und anschließend chunk-weise per **Fernet** (AES-128-CBC mit HMAC-SHA256, [`cryptography.fernet`](https://cryptography.io/en/latest/fernet/)) mit dem `ENCRYPTION_KEYS`-Schlüsselmaterial verschlüsselt. Implementierung: [`encrypt_file()` in `src/core/services/file_vault/encryption.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/file_vault/encryption.py). Refs #524.

#### Upload-Flow

1. Fachkraft wählt Datei im Formular aus — Ereignis ([`src/core/forms/events.py`](https://github.com/anlaufstelle/app/blob/main/src/core/forms/events.py)), Person oder Fall ([`clients.py`](https://github.com/anlaufstelle/app/blob/main/src/core/forms/clients.py)/[`cases.py`](https://github.com/anlaufstelle/app/blob/main/src/core/forms/cases.py)).
2. Server prüft Dateityp und Größe (siehe `allowed_file_types` und `max_file_size_mb` in den Einrichtungseinstellungen — Default **10 MB**).
3. **ClamAV-Scan** vor Verschlüsselung:
   - Standard: fail-closed. Ist der Daemon nicht erreichbar (`CLAMAV_ENABLED=true`, aber kein TCP-Connect möglich), wird der Upload **abgelehnt**.
   - Erkannter Virus → Upload wird verworfen, ein Audit-Log-Eintrag wird geschrieben.
4a. **Inhalt und Endung müssen zur selben Familie gehören** (Refs #2128): Die Magic-Bytes-Prüfung lehnt eine Datei ab, deren Inhalt als Bild erkannt wird, die aber keine Bild-Endung trägt (`foto.docx`, `scan.odt`). Ohne diese Bindung genügte es, ein Foto `.docx` zu nennen und `image/jpeg` korrekt zu deklarieren — Metadaten-Bereinigung **und** Decompression-Bomb-Schutz hätten es dann durchgewunken.
4b. **Gesamtbudget für Rasterbilder** (Refs #2582): Vor dem Dekodieren aller Frames und der Metadaten-Bereinigung prüft der Upload die Gesamtpixelgrenze (standardmäßig **40 Millionen**), höchstens **100 Frames/Seiten** und ein geschätztes Pufferbudget von **256 MiB**. WebP, PNG/APNG, GIF und ICO werden bereits anhand ihrer Containerheader geprüft, bevor das Bildöffnen Pixel- oder Disposal-Puffer anlegen kann. TIFF-Seitenheader werden einzeln gezählt; APNG und WebP reservieren pro Frame eine volle Bildfläche, GIF berücksichtigt auch spätere Vergrößerungen der Bildfläche. Der Byteansatz berücksichtigt formatabhängig gleichzeitig lebende native Decoder-Canvases, Quellbilder, bereinigte Framekopien sowie Disposal-, Blend- und Übergabepuffer, jeweils mit mindestens vier Bytes je Pixel; die volle Reserve wird konservativ auf alle Frameflächen angewendet. Das ist eine konservative Zulassungsregel, **keine bewiesene Obergrenze für den gesamten Encoder-RAM**. Eine Überschreitung führt zu einer Fehlermeldung und einem `SECURITY_VIOLATION`-Audit-Eintrag. Zulässige kleine Mehrseitenbilder bleiben erlaubt; GIF bleibt weiterhin byte-identisch. Vom Betreiber zugelassene SVG-Dateien werden unverändert durchgereicht und nicht rasterisiert. Nicht lesbare Rasterheader führen zur Ablehnung, weil ihr Budget nicht nachgewiesen werden kann.
4. **Metadaten-Bereinigung** bei Bild-Uploads (Refs #2128): JPEG, PNG, WebP und TIFF werden neu kodiert und verlieren dabei EXIF (inklusive **GPS-Aufnahmeort**, Gerätemodell/-seriennummer, Aufnahmezeit, eingebettetes Vorschaubild), PNG-Textchunks (`tEXt`/`iTXt`) sowie XMP-/IPTC-Blöcke. Die **Orientierung bleibt erhalten** — sie wird in die Pixel gedreht, Hochformat-Fotos kippen also nicht; das ICC-Farbprofil bleibt ebenfalls erhalten. Maßgeblich ist der in Schritt 4a per Magic-Bytes **erkannte** Typ, nicht die Dateiendung. Alle Nicht-Bild-Dateien (PDF, DOCX, ODT …) bleiben byte-identisch. **Immer aktiv, kein Schalter** — die Anwendung ist kein Beweissicherungswerkzeug. Lässt sich eine Datei trotz passender Magic-Bytes nicht dekodieren, wird der Upload abgelehnt (fail-closed, Audit-Grund `image_undecodable`).

> **GIF und BMP werden bewusst nicht bereinigt.** Das ist kein Versehen: GIF89a kennt sowohl eine *Comment Extension* als auch eine *XMP-Application-Extension*, BMP v5 kann ein ICC-Profil einbetten — diese Blöcke **bleiben erhalten**. Ein Pillow-Re-Encode würde bei GIF die Farbpalette neu quantisieren und bei animierten Dateien Frame-Timing und Disposal-Verhalten verändern; das ist ein sichtbarer Eingriff ins Bild für einen Metadaten-Kanal, den die Praxis nicht nutzt (Beratungsfotos sind JPEG oder PNG). **Empfehlung:** Wer das ausschließen will, lässt `gif` und `bmp` aus `allowed_file_types` heraus.
5. Inhalt wird chunk-weise mit Fernet (MultiFernet) verschlüsselt (Write-Key = erster Eintrag aus `ENCRYPTION_KEYS`) und in `MEDIA_ROOT` gespeichert.
6. Download erfolgt ausschließlich über die geschützte Django-View (kein direkter Webserver-Zugriff auf `MEDIA_ROOT`).

> **Hinweis zu Dokument-Metadaten:** Die Bereinigung greift ausschließlich bei Bildern. Metadaten in PDF (`/Author`, `/Creator`) und in OOXML-Dateien (`docProps/core.xml`, `dc:creator`) bleiben unverändert — dort steht typischerweise der Klarname der **bearbeitenden Person**, nicht der betroffenen. Wer das ausschließen will, bereinigt die Dokumente vor dem Hochladen.

> **Klartext-Zwischenpuffer (Refs #1661):** Uploads oberhalb von `FILE_UPLOAD_MAX_MEMORY_SIZE` (Default 2,5 MB) puffert Django vor Schritt 3 als Klartext-Tempdatei auf Platte (`FILE_UPLOAD_TEMP_DIR`), bevor sie gescannt und verschlüsselt werden. In den mitgelieferten Compose-Dateien liegt dieser Puffer auf einem `/tmp`-tmpfs (RAM) statt auf persistentem Storage. Details, Verifikation und Fallback-Optionen ohne tmpfs: [Ops-Runbook § 5.7a](ops-runbook.md).

#### ClamAV-Service (`docker-compose.prod.yml`)

Das Produktions-Compose enthält einen eigenen `clamav`-Container mit dem Binary-Smoke-Healthcheck `clamdscan --version`. Der Web-Dienst wartet auf `service_healthy`, bevor er startet. Referenz: [`docker-compose.prod.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.prod.yml). Konfigurierbar über `CLAMAV_ENABLED` / `CLAMAV_HOST` / `CLAMAV_PORT` / `CLAMAV_TIMEOUT` (siehe [ENV-Referenz in § 1](#vollständige-umgebungsvariablen-referenz)).

#### Healthcheck

Der `/health/`-Endpoint prüft in Produktion zusätzlich zur Datenbank die Erreichbarkeit des ClamAV-Daemons. Ist ClamAV aktiviert, aber nicht erreichbar, steht im JSON-Payload `status: "degraded"`, der HTTP-Status bleibt **200**. Monitore müssen deshalb den Payload-Wert `status` auswerten; eine reine HTTP-Code-Prüfung erkennt „Uploads sind aktuell nicht möglich“ nicht.

#### Schlüssel-Rotation (`ENCRYPTION_KEYS`)

MultiFernet akzeptiert eine Liste von Schlüsseln. Um zu rotieren:

1. **Neuen Schlüssel generieren** und als **ersten** Eintrag in `ENCRYPTION_KEYS` einfügen: `ENCRYPTION_KEYS=neu,alt`. Neu geschriebene Daten werden mit `neu` verschlüsselt, vorhandene mit `alt` lassen sich weiterhin lesen.
2. Über den Wartungsservice `reencrypt_fields` ausführen und bei übersprungenen Zeilen oder TOTP-Retries wiederholen, bis keine Warnung mehr bleibt.
3. Danach `reencrypt_files` ausführen und anschließend **ungefiltert** `reencrypt_files --verify`: alle referenzierten Anhang-Versionen, auch gelöschte und ersetzte, müssen mit dem Primärschlüssel allein vollständig lesbar sein. Fehlerstatus bedeutet: Altschlüssel behalten, Ursache beheben, erneut rotieren und prüfen.
4. Lokale und Off-Site-Backups mit alten Chiffraten nach den geltenden Fristen ablaufen lassen; bis dahin die benötigten Schlüssel getrennt im Eskrow aufbewahren. Erst nach der vollständigen Prüfung und dem tatsächlichen Backup-Ablauf den Altschlüssel entfernen, alle schlüsselnutzenden Prozesse mit der neuen Konfiguration neu erstellen und ungefiltert `reencrypt_files --verify` erneut ausführen. Der vollständige Wartungsweg samt Grenzen steht im [Ops-Runbook § 9.5](ops-runbook.md#dateirotation-und-altschlüssel-entfernen).

> **Wichtig:** Den neuen Schlüssel zuerst voranstellen und die alten zunächst behalten. `--facility` beweist keine installationsweite Entfernbarkeit. Es gibt **einen Schlüsselring je Installation**, keine Einrichtungsschlüssel; auch eine erfolgreiche Dateiprüfung beweist weder die Rotation aller Datenbankfelder noch die Lesbarkeit alter Backups.
> ⚠️ **Rollen-Voraussetzung (Refs #1058).** `reencrypt_fields` liest und schreibt Tabellen mit Row-Level-Security. Die Laufzeit-Rolle des `web`-Containers ist `NOBYPASSRLS` und hat außerhalb eines Requests keinen Einrichtungs-Kontext — ein Aufruf dort sähe **0 Zeilen**. Deshalb immer ueber den Maintenance-Service aufrufen ([Ops-Runbook § 9.5](ops-runbook.md)):
>
> ```bash
> docker compose -f docker-compose.prod.yml run --rm maintenance reencrypt_fields --dry-run
> ```
>
> Der Command bricht mit einer klaren Fehlermeldung ab, wenn der Bypass-Kontext fehlt (fail-loud statt „0 rotiert, Erfolg gemeldet"; #1058).

**Was `reencrypt_fields` abdeckt** (Refs #1058):

| Datenbestand | Umfang |
|---|---|
| **`Event.data_json`** | **vollständig — inklusive soft-gelöschter Ereignisse** (#1724); je Zeile mit Row-Lock und Compare-and-Swap |
| `EventHistory.data_before` / `data_after` | vollständig (append-only, Update über den Replikations-Bypass); je Zeile mit Row-Lock und Compare-and-Swap auf den geänderten Spalten (#2177) |
| `Attachment.original_filename_encrypted` | vollständig — der verschlüsselte Dateiname von Anhängen an Ereignis, Person und Fall (#1738); je Zeile mit Row-Lock und Compare-and-Swap |
| **Dateiinhalte der Anhänge** (Dateien unter `MEDIA_ROOT`) | **nicht durch `reencrypt_fields` abgedeckt** — separat mit `reencrypt_files` rotieren und mit `--verify` prüfen (Schritt 3) |
| TOTP-Secrets (`otp_totp_totpdevice.key`) | vollständig, je Gerät mit Row-Lock und Compare-and-Swap |
| **`Client.data_json`** | **vollständig — inklusive soft-gelöschter Personen**; je Zeile mit Row-Lock und Compare-and-Swap |
| **`Case.data_json`** | **vollständig — inklusive soft-gelöschter Fälle**; je Zeile mit Row-Lock und Compare-and-Swap |
| `RectificationRequest.old_value` / `new_value` / `reason` / `review_note` | vollständig, je Zeile mit Row-Lock und Compare-and-Swap auf den geänderten Wertspalten |
| `ShiftHandoverNote.note` (Übergabenotiz) | vollständig, je Zeile mit Row-Lock und Compare-and-Swap (#2042) |

Auch **Personen** und **Fälle** tragen verschlüsselte Zusatzfelder in `data_json` ([ADR-037](adr/037-konfigurationspakete-dynamische-felder.md)). Der Lauf rotiert sie mit und heilt dabei zusätzlich Klartext-Reste in Feldern, die laut Dokumentationstyp verschlüsselt sein müssen.

Bei Ereignissen, Personen und Fällen werden **auch soft-gelöschte Datensätze** rotiert. Das ist Absicht: ein Datensatz im Papierkorb soll nicht dauerhaft unter einem ausrangierten Schlüssel liegen bleiben, nur weil er auf Löschung wartet (#1724).

> **Betriebshinweis:** Der Lauf schützt jede zu ändernde Zeile mit Row-Lock und Compare-and-Swap. Gewinnt eine parallele Fachänderung, überspringt die Rotation den veralteten Stand und warnt: „N Zeilen wegen paralleler Änderung übersprungen — Lauf wiederholen“. Die Rotation überschreibt dabei nie eine zwischenzeitliche Änderung — auch nicht bei Redaktions- oder Anonymisierungsläufen, die `updated_at` nicht fortschreiben. Das gilt auch für die **Ereignis-Historie** (#2177): Redigiert der nächtliche Aufbewahrungslauf oder eine Anonymisierung nach Art. 17 DSGVO eine Historienzeile, während die Rotation läuft, überspringt die Rotation diese Zeile und meldet sie als übersprungen — der Redaktionsstand bleibt. Bereits redigierte Zeilen sind ohnehin keine Rotationskandidaten mehr: Sie tragen keine verschlüsselten Werte, sondern nur noch den Redaktionsvermerk. Den Lauf dann wiederholen, bis er ohne diese Meldung endet; der alte Schlüssel bleibt bis zum Abschluss von Dateirotation, Vollprüfung und Backup-Ablauf im Ring (Schritte 3–4). Ein Wartungsfenster bleibt wegen der zusätzlichen Datenbanklast empfehlenswert, ist aber keine Voraussetzung mehr, um verlorene Updates zu verhindern.

#### Upload-Limit

- Per Einrichtung: `Settings.max_file_size_mb` (Default **10 MB**) — siehe [`src/core/models/settings.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py).
- Global je Datei: `FILE_VAULT_MAX_UPLOAD_BYTES` (Default **50 MB**, per `.env` änderbar). Es gilt der kleinere der beiden Werte.
- Je Anfrage: Caddy weist Anfragen über `request_body max_size` (**1100 MiB**, ausgelegt auf 20 Dateien zu 50 MB je Feld) mit HTTP 413 ab. Der Wert in der `Caddyfile` folgt einer geänderten Datei-Obergrenze nicht automatisch; nach einer Erhöhung von `FILE_VAULT_MAX_UPLOAD_BYTES` oder `FILE_VAULT_MAX_UPLOAD_FILES` ziehen Sie ihn nach (Ops-Runbook § 5.7).
- Djangos `DATA_UPLOAD_MAX_MEMORY_SIZE` (2,5 MB, in `prod.py` fest gesetzt) begrenzt nur Formulardaten ohne Dateien und ist keine Grenze für Anhänge.

#### Speicherkontingente

Das Upload-Limit oben gilt **pro Datei**. Refs #2129 ergänzt eine Obergrenze für die **Summe** aller Dateianhänge **je Einrichtung**. Anlass ist der Mehr-Mandanten-Betrieb: Ohne Kontingent kann eine Einrichtung mit lauter legitimen Uploads den gemeinsamen `MEDIA_ROOT` füllen — die Folge (Upload-Fehler, Backup-Fehler) trifft dann **alle** Einrichtungen der Instanz. Der Disk-Check in `/health/` meldet einen vollen Datenträger, verhindert ihn nicht und kennt keine Einrichtung.

| Stellschraube | Ort | Bedeutung |
|---|---|---|
| `Settings.max_storage_mb` | Verwaltung → Einstellungen, Gruppe „Dateien" | Kontingent **dieser** Einrichtung in MB. Leer = kein eigenes Kontingent. |
| `FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB` | ENV (Default **2048**) | Betreiber-Backstop für alle Einrichtungen ohne eigenes Kontingent. `0` = kein Backstop. |

Wirksam ist immer das **striktere** der beiden Limits. Sind beide leer bzw. `0`, ist die Einrichtung unbegrenzt.

**Was mitzählt.** Alle Anhänge der Einrichtung, **inklusive Papierkorb** und inklusive abgelöster Vorversionen: Ein entfernter Anhang ist nur aus der Fach-Sicht weg, die verschlüsselte Datei liegt bis zur physischen Tilgung weiter auf der Platte. Die Anzeige weist den Papierkorb-Anteil deshalb gesondert aus — sie bleibt bewusst auf Anhänge beschränkt und ist der Hebel, mit dem eine volle Einrichtung wieder Luft bekommt (ersatzloses Löschen, siehe [Dateien-Übersicht im Benutzerhandbuch](user-guide.md)). Bei der **Durchsetzung eines neuen CSV-Imports** zählen zusätzlich die Quelldateien aller bereits offenen CSV-Importe (`ImportJob`) mit, bis der jeweilige Lauf übernommen oder abgebrochen wurde. Deshalb kann ein neuer **Import** abgewiesen werden, obwohl die sichtbare Anhangsbelegung noch unter dem Limit liegt. Für **Dateianhänge** gilt das in zwei Stufen: Die Prüfung vor dem Virenscan sieht ausschließlich die Summe der Anhänge; der Speicherschritt unter der Einrichtungssperre rechnet dagegen mit derselben gemeinsamen Summe wie der Import — Anhänge **und** offene Importdateien. Deshalb kann auch ein **Anhang** erst im Speicherschritt abgewiesen werden, obwohl die angezeigte Anhangsbelegung noch unter dem Limit liegt.

**Was passiert beim Erreichen.** Der Anhangs- oder CSV-Upload wird im Vorab-Check abgelehnt, bevor er verschlüsselt wird; für Dateianhänge und CSV-Importdateien prüft zusätzlich der Speicherschritt unter der Einrichtungssperre ein zweites Mal (Überzug 0 Byte) — die Fachkraft sieht „Speicherkontingent der Einrichtung erschöpft: X von Y MB belegt (davon Z MB im Papierkorb)". Protokolliert wird das als eigene Audit-Aktion **„Speicherkontingent überschritten"** (`attachment_quota_exceeded`), bewusst **nicht** als Sicherheitsverletzung: Ein volles Kontingent ist ein Betriebszustand, kein Angriff.

**Anzeige und Warnung.** Der Verwaltungs-Hub (`/verwaltung/`) zeigt die Zeile „Speicher — X von Y MB belegt (davon Z MB im Papierkorb)", die Leitungs-Arbeitszentrale dieselbe Zeile unterhalb ihrer Karten. Ab **90 %** Füllstand erscheint zusätzlich ein Hinweis in den Konfigurations-Warnungen der Anwendungsbetreuung — und derselbe Satz als Hinweistext direkt unter der Speicher-Zeile der Leitung.

Mehrere Dateien **eines** Formulars zählen gemeinsam: Die Prüfung kennt die im selben Vorgang bereits verarbeiteten Dateien und lehnt diejenige ab, mit der das Kontingent reißen würde.

**Gleichzeitige Uploads.** Das Kontingent hält auch dann, wenn beliebig viele Uploads einer Einrichtung zur selben Zeit laufen (Refs #2178, #2259): Die Prüfung vor dem Virenscan beziehungsweise vor dem Verschlüsseln der Importdatei spart nur unnötige Arbeit; entschieden wird im Speicherschritt, unmittelbar bevor der Anhang oder der Importlauf eingetragen wird — dort wird die Summe unter einer kurzen Sperre je Einrichtung ein zweites Mal gelesen, und zwar über **Dateianhänge und offene CSV-Importdateien gemeinsam**. Der Überzug ist damit **0 Byte**, auch im Mischfall aus Anhang und Import. Virenscan und Verschlüsselung laufen weiterhin ohne jede Sperre; serialisiert ist allein der kurze Datenbankteil. Wird ein Upload erst dort abgewiesen, sieht die Fachkraft dieselbe Meldung wie sonst, es bleibt weder eine verschlüsselte Datei noch ein Eintrag zurück, und die Ablehnung steht als „Speicherkontingent überschritten" im Audit-Log.

#### Sichere Downloads (RFC 5987)

Alle File-Downloads werden über den zentralen Helper [`safe_download_response`](https://github.com/anlaufstelle/app/blob/main/src/core/utils/downloads.py) ausgeliefert. Der Helper setzt `Content-Disposition` mit RFC-5987-kodierten Dateinamen (Unicode-sicher, kein Reverse-Path-Traversal) und verhindert Browser-MIME-Sniffing.

### 2.10 Offline-Modus & Streetwork (M6A)

**Offline-Schlüssel und Widerrufsprüfung** (#2528). Der aus Passwort und Server-Salt abgeleitete non-extractable `CryptoKey` bleibt in der Browser-IndexedDB gespeichert und übersteht Tab-, Browser- und Geräteneustart. „Non-extractable“ verhindert den Export über WebCrypto; der Schutz des gespeicherten Schlüssels gegen Zugriff auf das Browserprofil hängt an **Geräteverschlüsselung und Bildschirmsperre**, die für Offline-Geräte verpflichtend sind. Beim Ableiten wird eine absolute Frist eingefroren: `session_absolute_hours` der Einrichtung (Vorgabe 12 Stunden). Aktivität und Navigation verlängern sie nicht. Die Anwendung prüft beim Laden, vor Entschlüsselung/Speicherung/Replay, bei Tab-Rückkehr und im 60-Sekunden-Intervall. Am Ablauf entzieht sie den Schlüssel und zeigt „Offline-Schlüssel abgelaufen — bitte online anmelden“; ungesendete Arbeit bleibt verschlüsselt erhalten und wird nach erneutem Online-Login mit unverändertem Passwort und Salt wieder lesbar. Das Leerlauffenster gilt zusätzlich. Fehlende oder ungültige Ablaufdaten sperren den Zugriff; Bundles ohne gültiges `expiresAt` gelten als abgelaufen. Ein beobachteter Rücksprung der Geräteuhr sperrt den Schlüssel; eine manipulierbare Geräteuhr bleibt dennoch ein Restrisiko. Bei ausgeschalteter Anwendung erfolgt kein Schlüsselentzug per Fernlöschung.

Meldet die Widerrufsliste einen HTTP-/Netz-/Verarbeitungsfehler (auch 500/429) oder liefert sie eine ungültige Antwortstruktur, bleiben Queue- und Edit-Replay ausgesetzt: „Synchronisation wartet auf Widerrufsprüfung“. Der Sperrzustand wird in der lokalen IndexedDB gespeichert. Zusätzlich braucht jeder Replay-Lauf eine eigene frische, strukturell gültige, erfolgreich angewandte und persistierte Widerrufsprüfung; eine bereits erteilte Freigabe erlaubt keinen weiteren Lauf und keinen anderen Tab, auch wenn Metadatenschreiben und Ersatzlöschung gleichzeitig scheitern. Fehlende oder unlesbare Metadaten sperren den Replay; auch nach einem Lesefehler ist eine neue erfolgreiche Prüfung nötig. Der nächste Sync-Lauf prüft erneut, ebenso ein direktes Speichern aus der Offline-Akte. Bei fehlgeschlagener Prüfung bleibt ungesendete Arbeit verschlüsselt erhalten. Die bestehenden Idempotenz-Schlüssel bleiben erhalten, und die Server-Gates (422 für widerrufene Personen, 409 für Statuskonflikte) bleiben verbindlich. Geräteverschlüsselung ersetzt diese Server-Prüfungen nicht.

Für Außeneinsätze (z. B. Streetwork) bietet die Anlaufstelle einen **sicheren Offline-Modus** mit **client-seitiger** Ende-zu-Ende-Verschlüsselung aller auf dem Gerät zwischengespeicherten Daten. Refs #573, #576.

#### Kryptographie-Design

| Aspekt | Wert |
|---|---|
| Algorithmus | **AES-GCM-256** (WebCrypto-native) |
| Storage | **IndexedDB**: Inhalte als AES-GCM-Chiffrat; Ordnungsmerkmale (Datensatz- und Personen-IDs, Bearbeitungsstatus, Zeitstempel, Ziel-Adressen der Sende-Warteschlange) im Klartext; der non-extractable Schlüssel liegt in einer eigenen IndexedDB (`anlaufstelle-crypto`) |
| Key-Ableitung | **PBKDF2** — 600 000 Iterationen, SHA-256 |
| KDF-Input | Benutzer-Passwort + `User.offline_key_salt` (16 Byte, pro User, serverseitig gespeichert) |
| Key-Lebenszeit | **Persistiert** als non-extractable `CryptoKey` (`extractable: false`) in einer eigenen IndexedDB (`anlaufstelle-crypto`) — überlebt Tab-Schließen und Reload; begrenzt durch Leerlaufzeit und absolute Höchstdauer ab Ableitung, siehe „Konsequenzen für Admins" |

Quelle: [`src/static/js/crypto.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/crypto.js) (client-seitige Ableitung/Speicherung/Idle-Wipe), [`src/core/signals/offline_invalidation.py`](https://github.com/anlaufstelle/app/blob/main/src/core/signals/offline_invalidation.py) (server-seitige Salt-Rotation bei Rollenwechsel/Deaktivierung).

#### Konsequenzen für Admins

- **Key-Verlust ist nicht reparierbar:** Vergisst ein Nutzer sein Passwort, sind alle auf seinem Gerät lokal gespeicherten Offline-Daten **dauerhaft unlesbar**. Ein Admin-Reset der Offline-Daten ist technisch **nicht möglich** — der Schlüssel existiert nur im Browser des Nutzers.
- **Vor Passwort-Änderung synchronisieren:** Nutzer müssen Offline-Daten mit dem Server synchronisieren, **bevor** sie das Passwort ändern oder sich ausloggen. Bei Passwortwechsel wird das Salt rotiert (neuer Schlüssel); derselbe Mechanismus rotiert den Salt automatisch bei Rollenwechsel oder Deaktivierung des Users (`offline_invalidation.py`), damit ein entzogenes Recht nicht offline fortbesteht.
- **Schlüssel wird verworfen bei:** Logout, Passwort-Änderung (Salt-Rotation), Rollenentzug/Deaktivierung (server-seitige Salt-Rotation) und Sitzungs-Ablauf (Idle-Key-Wipe, Default 30 Min) → gespeicherte Chiffretexte sind ohne erneute Anmeldung nicht mehr zu entschlüsseln. **Das bloße Schließen des Tabs verwirft den Schlüssel NICHT** — er wird beim Wiederöffnen innerhalb der Session-Lebenszeit aus der IndexedDB rekonstruiert (bewusstes Streetwork-Design, #1065/#1324).

#### Browser-Voraussetzungen

- WebCrypto-API (`crypto.subtle`) und IndexedDB müssen verfügbar sein.
- Unterstützt: aktuelle Versionen von Firefox, Chrome, Edge, Safari.
- Nicht unterstützt: veraltete Browser ohne moderne Crypto-API (der Offline-Modus wird in dem Fall ausgegraut).

#### Streetwork-Stufen

- **Stufe 2 (Read-Cache):** Ausgewählte Personen-Dossiers werden vor dem Streetwork-Einsatz lokal verschlüsselt gecacht, damit sie offline eingesehen werden können.
- **Stufe 3 (Offline-Edit):** Ereignisse und Notizen können offline erfasst werden und synchronisieren beim nächsten Online-Gang. Konflikte zwischen Offline-Edit und zwischenzeitlicher Server-Änderung werden per **Side-by-Side-Diff** dem Nutzer zur manuellen Auflösung präsentiert.

Neu verschlüsselte Mehrfachauswahlwerte bleiben beim Offline-Bearbeiten von Ereignissen und beim Übertragen nach der Wiederverbindung Listen; unverändertes Speichern erhält die ausgewählten Optionen. In der Konfliktprüfung steht jede ausgewählte Bezeichnung auf einer eigenen Zeile, einschließlich deaktivierter Optionen; fehlt eine passende Option oder Feldvorlage, erscheint der Slug. Rollen- und Feldschutz greifen vor dem Entschlüsseln der Serverwerte. Refs #2520.

### 2.11 CSV-Import und -Export: Formelschutz

Alle CSV-Exporte (Statistik-Export, Datensatz-Berichte, Audit-Log-Export) neutralisieren **Formel-Injection**: Eine Zelle, die mit `=`, `+`, `-`, `@`, Tabulator oder Zeilenumbruch beginnt, würde Excel oder LibreOffice beim Öffnen der Datei als **Formel** ausführen. Solche Zellen bekommen deshalb ein führendes Apostroph (`'`) — die Tabellenkalkulation zeigt den Originalwert an, führt ihn aber nicht aus. Refs #719.

Der Schutz greift auch, wenn vor dem Formelzeichen **unsichtbare Zeichen** stehen — Leerzeichen, geschütztes Leerzeichen (NBSP), Byte-Order-Mark oder Zero-Width-Zeichen. Tabellenkalkulationen verwerfen diese Zeichen beim Import und werten die Zelle danach trotzdem aus; `" =1+1"` wird deshalb ebenfalls apostrophiert. Der Originalwert bleibt vollständig erhalten, es wird nichts abgeschnitten. Das gilt für Werte- **und** Kopfzeilen — der Name einer Feldvorlage ist Freitext der Anwendungsbetreuung und damit derselbe Vektor.

> **Wichtig — eigene Exporte sind nicht round-trip-fest:** Der CSV-Import entfernt das Schutz-Apostroph **nicht** wieder. Importieren Sie eine zuvor exportierte Datei erneut, wird aus dem Wert `=Text` dauerhaft `'=Text`. Das ist kein Fehler und keine Fehlermeldung im Importlauf, sondern eine bewusste Grenze: Die Import-Engine vergleicht die **rohe** Dateizeile mit ihren Fehlermeldungen, um zu verhindern, dass Zellinhalte (potenziell Klientendaten) im Ergebnisprotokoll eines Laufs landen. Würde sie vorher entapostrophieren, träfe dieser Abgleich nicht mehr und ein Zellwert könnte ins Protokoll durchrutschen — Datenschutz geht hier vor Bequemlichkeit.
>
> **Praxis:** Nutzen Sie den CSV-Import für die **Übernahme aus Fremdsystemen**, nicht als Sicherung-und-Rückspielweg für eigene Daten. Für Letzteres ist die verschlüsselte Datenbank- und Mediensicherung mit `backup.sh` (siehe [§ 3](#3-backup-und-wiederherstellung)) der richtige Weg. Müssen Sie einen exportierten Wert doch wieder einlesen, entfernen Sie das führende `'` vorher in der Tabellenkalkulation.

Ändern sich während eines laufenden Imports der Dokumentationstyp oder zugeordnete Feldvorlagen, weist die Anwendung den Probelauf beziehungsweise die Übernahme ab und verweist zurück auf den Zuordnungsschritt. So werden entfernte oder deaktivierte Zielfelder nicht still ausgelassen. Für die Übernahme gilt immer die **zuletzt erfolgreich bestätigte Zuordnung** — auch wenn sie in einer anderen Browser-Sitzung bestätigt wurde.

Die Berichts-Exporte in **JSON und XML** sanitisieren bewusst nicht — dort gibt es keinen Formel-Kontext, und CSV-Escapes würden die maschinenlesbaren Werte verfälschen.

Relevante Dateien: [`src/core/services/system/export.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/system/export.py) (`_sanitize_csv_cell`), [`src/core/services/import_engine.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/import_engine.py).

### 2.12 Import-Assistent

Der Import-Assistent unter **Datenimport** (`/import/`) übernimmt CSV-Dateien in Personen oder Ereignisse. Er ist ausschließlich für die Rolle **Anwendungsbetreuung** sichtbar. Eine Datei darf höchstens 5 MB und 1.000 nicht leere Datenzeilen umfassen; die erste Zeile muss eindeutige, nicht leere Spaltenüberschriften enthalten. Unterstützt werden UTF-8 und Windows-1252 sowie Semikolon, Komma oder das Tab-Zeichen als Trennzeichen; Zeichensatz und Trennzeichen lassen sich automatisch erkennen oder ausdrücklich auswählen. Für Ereignisse ist ein aktiver Dokumentationstyp Pflicht.

Jeder Datensatz muss genau so viele Zellen wie die Kopfzeile enthalten; auch leere optionale Zellen brauchen ihr Trennzeichen. Zu viele oder zu wenige Zellen weisen die gesamte Datei ab, bevor Daten übernommen werden. Anführungszeichen dürfen ein Feld nur am Feldanfang öffnen; innerhalb gequoteter Felder müssen sie verdoppelt sein, und nach dem schließenden Anführungszeichen darf nur das Feld- oder Datensatzende folgen. Ungültige Quote-Stellungen und nicht geschlossene Felder weisen die Datei ebenfalls ab. Die Meldung nennt die physische Zeile und den logischen Datensatz (einschließlich Kopfzeile), niemals Zellinhalte. UTF-8 mit BOM sowie korrekt gequotete Trennzeichen und mehrzeilige Felder bleiben zulässig. Vorschau, Probelauf und Übernahme prüfen dieselbe vollständige Dateistruktur.

Der Assistent führt durch vier Schritte:

1. **Datei:** Wählen Sie Personen oder Ereignisse als Ziel, bei Ereignissen den Dokumentationstyp, und laden Sie die CSV-Datei hoch.
2. **Zuordnung:** Ordnen Sie jede benötigte Quellspalte einem Zielfeld zu. Nicht zugeordnete Spalten werden ignoriert; fünf Beispielwerte helfen bei der Auswahl. Für Personen ist das Pseudonym Pflicht, für Ereignisse sind Zeitpunkt und Personenpseudonym Pflicht. Aktive Zusatzfelder des gewählten Dokumentationstyps stehen ebenfalls zur Auswahl, Dateifelder jedoch nicht.
3. **Probelauf:** Der Assistent prüft sämtliche Zeilen, speichert aber noch keine Fachdaten. Die Übersicht nennt Datenzeilen, voraussichtlich angelegte Datensätze und Fehler; die Fehlerliste verwendet die Zeilennummer der Tabellenkalkulation einschließlich Kopfzeile. Korrigieren Sie eine fehlerhafte Datei und laden Sie sie neu hoch.
4. **Übernahme:** **Jetzt übernehmen** erscheint nur nach einem fehlerfreien Probelauf. Die Übernahme ist atomar: Tritt inzwischen ein Fehler auf, wird kein Datensatz gespeichert. Anschließend wird die hochgeladene Quelldatei gelöscht.

Mit **Import abbrechen** schließen Sie einen noch offenen Lauf und löschen dessen Quelldatei, ohne Fachdaten zu übernehmen. Upload, erfolgreiche Übernahme und Abbruch werden im Audit-Log protokolliert; die Importübersicht bewahrt Status, Zähler und Fehlerbericht des Laufs. Beachten Sie beim Vorbereiten der Datei außerdem den [Formelschutz und seine Round-trip-Grenze aus § 2.11](#211-csv-import-und--export-formelschutz).

---

## 3. Backup und Wiederherstellung

### 3.1 Verschlüsselte Datenbank- und Mediensicherung

Der kanonische Produktionsweg ist [`scripts/ops/backup.sh`](../scripts/ops/backup.sh). Führen Sie das Skript im festgelegten Installationsverzeichnis aus. Das Konto braucht Docker-Zugriff und Schreibrecht auf `BACKUP_HOST_DIR`; die folgenden Beispiele verwenden deshalb `sudo`:

```bash
cd /opt/anlaufstelle
sudo sh -c 'umask 0027 && ./scripts/ops/backup.sh'
```

Das Skript lädt die `.env`, sichert PostgreSQL **und** den verschlüsselten File-Vault (`MEDIA_ROOT`), komprimiert beide Archive, verschlüsselt sie mit AES-256-CBC und schreibt zu jedem Archiv einen HMAC-SHA256-Sidecar. Die Dateien landen unter `BACKUP_HOST_DIR` in `daily/`, `weekly/` und `monthly/`; die eingebaute Rotation bewahrt sie 7, 28 beziehungsweise 90 Tage auf.

Voraussetzungen in `.env`:

- `POSTGRES_DB` und `POSTGRES_USER` für die Datenbankverbindung,
- `POSTGRES_ADMIN_USER` und `POSTGRES_ADMIN_PASSWORD` für den vollständigen Dump,
- ein zufälliger, separat verwahrter `BACKUP_ENCRYPTION_KEY`,
- `BACKUP_HOST_DIR` als Basisverzeichnis auf dem Host; das Skript schreibt ausschließlich dorthin und ignoriert das nur als Container-Mount-Ziel verwendete `BACKUP_DIR` (siehe [ENV-Tabelle](#vollständige-umgebungsvariablen-referenz)),
- `DJANGO_AUDIT_HWM_PATH` als Ablage des Audit-Ankers; `.env.example` liefert `/data/audit_hwm.json` ab Werk aktiv aus.

**Pflichtschritt vor dem ersten Backup — Audit-Anker anlegen.** Der Audit-Anker ist die signierte Datei außerhalb der Datenbank, mit der sich das Abschneiden der jüngsten Einträge und eine geleerte Kette überhaupt erst erkennen lassen. `backup.sh` kann sie nur sichern, wenn sie existiert, und sie entsteht erst mit dem ersten grünen Lauf der Ketten-Verifikation. Fahren Sie deshalb nach dem Stack-Start diesen Lauf **zweimal nacheinander** und prüfen Sie das Ergebnis, bevor Sie das erste Backup erstellen:

```bash
cd /opt/anlaufstelle
docker compose -f docker-compose.prod.yml run --rm -T maintenance verify_audit_chain
docker compose -f docker-compose.prod.yml run --rm -T maintenance verify_audit_chain
docker compose -f docker-compose.prod.yml exec -T cron test -s /data/audit_hwm.json
```

Der zweite Lauf ist nötig, weil die System-Kette auf einer frisch eingerichteten Instanz beim ersten Lauf noch keine Zeile hat und deshalb nicht verankert wird; erst dieser Lauf schreibt ihren Erfolgsmarker, und der zweite verankert sie. Ein Backup nach nur einem Lauf trägt keinen Anker für die System-Kette und besteht den Pflichtanker-Lauf der Wiederherstellung nicht ([Disaster-Recovery § 6 Schritt 4b](disaster-recovery.md#schritt-4b--audit-hwm-zurückspielen-und-kette-prüfen)). Der letzte Befehl endet mit Exit-Code 0, sobald der Anker vorhanden ist. Ein vollständiger Backup-Satz besteht danach aus **sieben** Dateien: Datenbank-Dump und Medienarchiv mit je einem `.hmac`-Sidecar, das signierte `.manifest` samt `.hmac` und `audit_hwm-<Zeitstempel>.json`. Fehlt der Anker, ist er leer oder kann `cron` ihn nicht lesen, bricht das Backup mit Exit-Code 1 vor Veröffentlichung ab — und der Wiederherstellungsschritt „Audit-Anker zurückspielen und Kette prüfen" ([Disaster-Recovery § 6 Schritt 4b](disaster-recovery.md#schritt-4b--audit-hwm-zurückspielen-und-kette-prüfen)) ist mit einem solchen Satz nicht ausführbar.

Der Anker liegt im eigenen Volume, das `web` nur lesend einbindet. Diese Trennung schützt nicht gegen Host-/Docker-Zugriff oder die Wartungs-/Owner-Rolle. Das Volume wird beim ersten Start angelegt; für einen Wechsel einer bestehenden Instanz den signierten Anker erhalten und nach [Ops-Runbook § 6.7](ops-runbook.md#67-audit-high-water-mark-sichern-und-pruefen-refs-2022) übernehmen. Die Karte „Letztes Backup“ prüft Existenz und Dateigröße der Kopie neben dem Manifest; den laufenden Anker liest sie nicht.

Der root-Cron braucht Schreibrecht, während `appuser` im Container (UID/GID 1000) das Verzeichnis lesen und durchqueren muss, damit Health- und Compliance-Prüfungen die Sicherungen finden. Legen Sie das konfigurierte `BACKUP_HOST_DIR` deshalb vor dem Setup als `root:1000` mit Modus `2750` an, genau wie in [Schritt 4](#schritt-4-stack-starten). `initial-setup.sh` läuft ohne `sudo`, akzeptiert den korrekten Bestand und ändert dessen Eigentümer oder Gruppe nicht; bei Abweichungen bricht es vor dem ersten `up` mit dem dort dokumentierten Korrekturbefehl ab. `backup.sh` macht nur das Manifest für diese Gruppe lesbar (`0640`); Dump, Medienarchiv, Audit-Anker und Signaturen bleiben dem Eigentümer vorbehalten (`0600`).

> **RLS ist hier sicherheitskritisch:** `pg_dump` muss als `POSTGRES_ADMIN_USER` laufen. Die App-Rolle `POSTGRES_USER` ist `NOBYPASSRLS`; ein roher Dump mit dieser Rolle scheitert an `FORCE ROW LEVEL SECURITY` beziehungsweise wäre als Sicherung der einrichtungsbezogenen Daten nicht vollständig. Verwenden Sie deshalb weder einen eigenen `pg_dump -U anlaufstelle`-Befehl noch die App-Rolle als Ersatz für `backup.sh`.

Bewahren Sie `.env` beziehungsweise mindestens `BACKUP_ENCRYPTION_KEY` und die vollständige Historie von `ENCRYPTION_KEYS` getrennt vom Produktivhost auf. Ohne den Backup-Schlüssel lässt sich das Archiv nicht entschlüsseln; ohne die Feld-/Vault-Schlüssel bleiben die darin enthaltenen verschlüsselten Inhalte unlesbar. Für Schutz gegen Host-Totalverlust konfigurieren Sie zusätzlich ein Off-Site-Ziel; Einzelheiten stehen im [Ops-Runbook § 6.6a](ops-runbook.md#66a-off-site-backup-sync-refs-738).

Für Off-Site-Kopien zusätzlich eine Lifecycle-Regel am Ziel mit höchstens 12 Monaten einrichten, die Frist im Löschkonzept und in den Informationspflichten dokumentieren und den tatsächlichen Ablauf kontrollieren; `backup.sh` löscht dort nichts ([Off-Site-Aufbewahrung](ops-runbook.md#off-site-aufbewahrung)).

### 3.2 Automatisches Backup einrichten

Empfohlen ist die **root-Crontab** auf dem Host: Sie verwendet fünf Zeitfelder und das ausführende Konto hat Docker-Zugriff. Aktivieren Sie vorher den Cron-Dienst, prüfen Sie die Host-Zeitzone und legen Sie die Logdatei mit restriktiven Rechten an:

```bash
sudo systemctl enable --now cron
timedatectl status
sudo touch /var/log/anlaufstelle-backup.log
sudo chown root:adm /var/log/anlaufstelle-backup.log
sudo chmod 640 /var/log/anlaufstelle-backup.log
sudo crontab -e
```

Eintrag für tägliche Backups um 02:00 Uhr (vor den Retention-Läufen):

```cron
0 2 * * * cd /opt/anlaufstelle && umask 0027 && ./scripts/ops/backup.sh >> /var/log/anlaufstelle-backup.log 2>&1
```

In `/etc/crontab` wäre zusätzlich nach den fünf Zeitfeldern der Benutzer `root` erforderlich; kopieren Sie die Fünf-Feld-Zeile dort nicht unverändert hinein. Richten Sie für `/var/log/anlaufstelle-backup.log` außerdem eine wöchentliche Logrotation (zum Beispiel acht komprimierte Rotationen mit `copytruncate`) unter `/etc/logrotate.d/` ein.

`backup.sh` erzeugt alle Dateien eines Laufs zunächst in einem verborgenen Arbeitsverzeichnis unterhalb von `daily/` und verschiebt sie erst nach dem vollständigen Lauf in das Tagesverzeichnis — das signierte Manifest zuletzt. Ein abgebrochener Lauf hinterlässt deshalb kein Artefakt; die Anzeige „Letztes Backup" in der Systemübersicht und im Compliance-Bereich zählt ausschließlich vollständige Sätze (Manifest samt der darin genannten Dateien) und bleibt sonst auf „Fehlt".

Keinen zusätzlichen `find ... -delete`-Job einrichten: `backup.sh` rotiert Datenbank-, Medien- und HMAC-Dateien gemeinsam. Prüfen Sie regelmäßig das neueste Backup:

```bash
cd /opt/anlaufstelle
sudo ./scripts/ops/backup.sh --verify
```

Der Prüflauf wählt den jüngsten **Satz** über sein signiertes Manifest, erzwingt dessen Bindung (Satzkennung, Dump-Hash, gebundenes Medienarchiv samt Sidecar und Hash — dieselbe Prüfung wie beim Restore), validiert den HMAC vor dem Entschlüsseln, stellt den Datenbank-Dump in einer temporären Datenbank wieder her, vergleicht kritische RLS-Tabellen mit der Live-Datenbank und prüft das Medienarchiv **desselben Satzes**. Ohne Manifest, mit fehlendem oder fremdem Medienarchiv endet der Lauf mit einem Fehler statt mit einer Warnung. Der ausführlichere quartalsweise Restore-Drill ist im [Ops-Runbook § 6.6](ops-runbook.md#66-backup-restore-drill-refs-720-739) beschrieben.

### 3.3 Wiederherstellung

[`scripts/ops/restore.sh`](../scripts/ops/restore.sh) ist ausschließlich für die von `backup.sh` erzeugten Backup-Sätze bestimmt. Ein `pg_restore`-Archiv aus einem anderen Backup-Pfad ist damit nicht kompatibel. Ein Satz besteht aus Dateien mit demselben Zeitstempel, die alle im selben Verzeichnis liegen müssen:

- `anlaufstelle_<Zeitstempel>.manifest` mit `.manifest.hmac`: das signierte Manifest (Satzkennung, Hashes von Dump, Medienarchiv und Audit-Anker, Zeilenzahlen). **Pflicht**: Fehlt es, bricht `restore.sh` ab, bevor es irgendetwas einspielt; einen Weg am Manifest vorbei gibt es nicht.
- `anlaufstelle_<Zeitstempel>.sql.gz.enc` mit `.sql.gz.enc.hmac`: der verschlüsselte Datenbank-Dump.
- `anlaufstelle_<Zeitstempel>_media.tar.gz.enc` mit `.hmac`: das verschlüsselte Medienarchiv, sofern das Manifest eines bindet.
- `audit_hwm-<Zeitstempel>.json`: der verpflichtende Audit-Anker des Backup-Satzes.

Wer nur Dump, Medienarchiv und deren Sidecars kopiert, kann nicht wiederherstellen.

1. Planen Sie ein Wartungsfenster, sperren Sie den öffentlichen Zugriff und bereiten Sie eine leere Ziel-Datenbank mit denselben Rollen-Namen wie im gesicherten System vor. Die App-Rolle (`POSTGRES_USER`, `NOBYPASSRLS`) und die Admin-/Owner-Rolle (`POSTGRES_ADMIN_USER`, `BYPASSRLS`) müssen vor dem Einspielen existieren. Die vollständige, destruktive Prozedur für einen neuen Host steht im [Disaster-Recovery-Leitfaden § 6](disaster-recovery.md#6-wiederherstellung-auf-einem-fabrikfrischen-host).

2. Stellen Sie sicher, dass DB- und Web-Container für den Datenbank- beziehungsweise Medien-Restore laufen, während Caddy und Cron gestoppt bleiben.

3. Starten Sie den Restore im Repository-Root. Das zweite Argument ist optional, für eine vollständige Wiederherstellung mit Dateianhängen aber erforderlich. Die Backup-Dateien gehören dem Konto, unter dem `backup.sh` läuft (im empfohlenen Setup root), und sind außer dem Manifest nur für dieses lesbar (`0600`, auch die `.hmac`-Signaturen). Starten Sie `restore.sh` deshalb als dieser Eigentümer, im empfohlenen Setup mit `sudo`; ein Betriebskonto, das nur über die Gruppe liest, kann den Satz nicht prüfen und bricht ab. Das Skript liest die `.env` selbst, `sudo` verliert also keine Einstellungen:

```bash
set -a
. ./.env
set +a
BACKUP_HOST_DIR="${BACKUP_HOST_DIR:-./backups}"
sudo ./scripts/ops/restore.sh \
  "$BACKUP_HOST_DIR/daily/anlaufstelle_YYYY-MM-DD_HHMMSS.sql.gz.enc" \
  "$BACKUP_HOST_DIR/daily/anlaufstelle_YYYY-MM-DD_HHMMSS_media.tar.gz.enc"
```

Noch vor der Bestätigungsabfrage prüft das Skript zuerst das Manifest (HMAC), dann die Bindung des Satzes (Satzkennung, Dump-Hash, Medien-Hash samt vorhandenem Sidecar), danach den DB-HMAC und, falls das zweite Argument übergeben wurde, den Medien-HMAC. Ein Medienarchiv, das das Manifest nicht bindet, lehnt es ab. Den Audit-Anker des Satzes (`audit_hwm-<Zeitstempel>.json` neben dem Dump) prüft es mit dem konfigurierten Pfad oder dem Compose-Default: Fehlt die Kopie, ist sie leer oder stimmt ihr Hash nicht mit dem Manifest überein, bricht es ab. Nach dem Datenbank-Restore schreibt es den Anker in das eigene Volume zurück. Nur `--skip-audit-anchor` auf einem Host ohne eigenen Pfad lässt den Restore ausdrücklich ohne Ankerprüfung und -import weiterlaufen; der Lauf belegt dann keine verankerte Kette ([Disaster-Recovery § 6 Schritt 4b](disaster-recovery.md#schritt-4b--audit-hwm-zurückspielen-und-kette-prüfen)). Erst nach allen erfolgreichen Vorprüfungen und der Eingabe `ja` spielt es die Datenbank ein; unmittelbar vor der jeweiligen Nutzung wiederholt es die zugehörige Prüfung, anschließend ersetzt und entpackt es den Medienbestand. Der SQL-Dump wird bewusst über den lokalen Bootstrap-Superuser (`POSTGRES_SUPERUSER`, Default `postgres`) eingespielt: Nur dieser kann die enthaltenen Owner-/`FORCE RLS`-Anweisungen und Datenkopien zuverlässig wiederherstellen. Bricht das Medien-Entpacken ab, ist der Medienbestand unvollständig und der vollständige Medien-Restore muss wiederholt werden.

4. Führen Sie die Migrationen mit der Admin-Rolle aus und starten Sie anschließend den Stack:

```bash
docker compose -f docker-compose.prod.yml run --rm \
  --entrypoint=/app/docker-migrate.sh maintenance
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d
```

5. Prüfen Sie Datenbankrollen, Gesundheitszustand und Stichproben aus Datenbank **und** File-Vault. Siehe [Abschnitt 5](#5-monitoring), [Ops-Runbook § 9](ops-runbook.md#9-row-level-security-rls) und die [Smoke-Verifikation des Disaster-Recovery-Leitfadens](disaster-recovery.md#7-smoke-verifikation-nach-restore).

---

## 4. Updates

### 4.1 Veröffentlichungen verfolgen

Neue veröffentlichte Versionen erscheinen auf der [GitHub-Release-Seite](https://github.com/anlaufstelle/app/releases); deren [Atom-Feed](https://github.com/anlaufstelle/app/releases.atom) lässt sich ohne GitHub-Konto in einem Feed-Reader abonnieren. Mit einem GitHub-Konto kann unter **Beobachten → Benutzerdefiniert → Veröffentlichungen** ausschließlich dieser Hinweis abonniert werden. Prüfen Sie den Kanal empfohlen einmal pro Woche und immer vor einem geplanten Update. Lesen Sie vor jeder Aktualisierung im CHANGELOG des Zielstands besonders den Abschnitt **„Erforderliche Betreiber-Aktionen“** und arbeiten Sie ihn vor beziehungsweise während des Deployments ab.

Veröffentlichungen erscheinen auf der Release-Seite auch als Pre-Release (so gekennzeichnet); Release-Seite und Feed zeigen jeden veröffentlichten Stand, mit dem zugehörigen Image in der Registry.

### 4.2 Neues Image ziehen und Stack aktualisieren

Migrationen laufen als separater One-Shot-Job **vor** dem Neustart des `web`-Dienstes — dieser führt beim Start selbst keine Migrationen mehr aus (Refs #802). Details siehe die kanonische Quelle [`docs/ops-runbook.md` § 1.2](ops-runbook.md#12-deploy-schritte).

```bash
cd /opt/anlaufstelle

# 0. Zielstand wählen: Repository auf den Release-Tag der Zielversion bringen und
#    APP_VERSION (bei Digest-Pinning gemeinsam mit APP_IMAGE_DIGEST) in der .env
#    setzen; Digest und Signatur vorher prüfen (Ops-Runbook § 8.4a). Ohne neuen
#    Wert zieht der Pull dasselbe Image erneut.
git fetch --tags && git checkout vX.Y.Z
grep -E '^APP_(VERSION|IMAGE_DIGEST)=' .env

compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)

# 1. Hintergrundjobs anhalten (cron nutzt Anwendungscode und Schema) und prüfen,
#    dass kein Wartungs-One-Shot mehr läuft (Ausgabe muss leer sein)
docker compose "${compose_files[@]}" stop cron
docker compose "${compose_files[@]}" ps --all --format '{{.Name}} {{.Status}}' maintenance

# 2. Neue Images herunterladen (web, cron und maintenance nutzen dasselbe Image)
docker compose "${compose_files[@]}" pull web cron maintenance

# 3. Migrationen als One-Shot-Job ausführen
docker compose "${compose_files[@]}" run --rm \
    --entrypoint=/app/docker-migrate.sh maintenance

# 4. Stack neu starten (web + cron + caddy, DB bleibt; kurze Downtime)
docker compose "${compose_files[@]}" up -d web cron caddy
```

`cron` ist ein eigener Dienst mit Anwendungscode aus demselben Image. Ein `up -d web caddy` ersetzt ihn nicht: Retention-, Audit- und Statistikjobs liefen weiter mit altem Code gegen die neue Datenbank. Legen Sie das Update deshalb nicht in die Zeitfenster der Nachtjobs (Backup 02:00, Retention 03:00 bis Session-Bereinigung 05:00; Zeitplan: [Ops-Runbook § 3](ops-runbook.md#3-cron-jobs)).

Prüfen Sie anschließend, dass `web` und `cron` dasselbe Image laufen, führen Sie einen lesenden Wartungs-Smoke aus und prüfen Sie die Logs. Der Block baut den vollständigen Dateisatz selbst auf, damit auch der neu erzeugte `maintenance`-Container eine eingerichtete `compose.override.yml` berücksichtigt:

```bash
cd /opt/anlaufstelle

compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)

# Gleiche Image-ID in beiden Zeilen
docker inspect --format '{{.Name}} {{.Config.Image}} {{.Image}}' \
  $(docker compose "${compose_files[@]}" ps -q web cron)

# Lesend, mit der Admin-Rolle (ändert nichts)
docker compose "${compose_files[@]}" run --rm maintenance check_db_roles
docker compose "${compose_files[@]}" run --rm maintenance enforce_retention --dry-run

docker compose "${compose_files[@]}" logs web --tail=50
```

### 4.3 Vor einem Update

- Erstellen und verifizieren Sie immer eine aktuelle Datenbank- und Mediensicherung (siehe [Abschnitte 3.1–3.2](#31-verschlüsselte-datenbank--und-mediensicherung)).
- Lesen Sie das Changelog zu Breaking Changes und erforderlichen Konfigurationsänderungen.

### 4.4 Rollback

Falls nach einem Update Probleme auftreten:

```bash
cd /opt/anlaufstelle

# 1. In der .env die vorherige Version eintragen (nicht in docker-compose.prod.yml
#    editieren: die versionierte Datei kollidiert sonst beim nächsten Update):
#      APP_VERSION=vX.Y.Z          (vorherige Version; ob der Tag pullbar ist:
#                                   docker manifest inspect ghcr.io/anlaufstelle/app:vX.Y.Z)
#      APP_IMAGE_DIGEST=@sha256:…  (Digest dieser Version) oder Zeile leeren —
#                                   ein gesetzter Digest wird sonst an den alten Tag gehängt
#    Wurde das Repository mit ausgecheckt, ebenfalls auf den vorherigen Tag zurück
#    (git checkout vX.Y.Z), damit Compose-Datei und deploy/crontab zum Image passen.

compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
# 2. web UND cron neu erzeugen
docker compose "${compose_files[@]}" up -d web cron

# 3. Gleiche Image-ID für web und cron prüfen
docker inspect --format '{{.Name}} {{.Config.Image}} {{.Image}}' \
  $(docker compose "${compose_files[@]}" ps -q web cron)

# Datenbank-Backup der Vorversion einspielen (falls nötig)
```

**Voraussetzung: Schema-Kompatibilität.** Der Image-Rollback ändert nur den Code; `web`, `cron` und `maintenance` arbeiten danach mit dem alten Code gegen das Schema der neueren Version. Das ist nur zulässig, wenn die Migrationen rückwärtskompatibel sind (Changelog prüfen); andernfalls Migration zurückrollen oder die Sicherung einspielen ([Ops-Runbook § 2](ops-runbook.md#2-rollback-prozedur)).

---

## 5. Monitoring

### 5.1 Health-Endpoint

Anlaufstelle stellt einen öffentlichen Health-Endpoint bereit:

```
GET /health/
```

**Antwort bei normalem Betrieb (HTTP 200, anonym):**

```json
{"status": "ok"}
```

**Antwort bei Datenbank-/Encryption-Key-Fehler (HTTP 503, anonym):**

```json
{"status": "error"}
```

Der Endpoint erfordert keine Authentifizierung und ist für externe Monitoring-Systeme geeignet. Die anonyme Antwort enthält bewusst **nur** das `status`-Feld (Recon-Härtung). Anonym fließen DB- und Encryption-Key-Ausfälle (`error` + HTTP 503), ein nicht erreichbarer ClamAV-Scanner und eine RLS-umgehende DB-Runtime-Rolle (`degraded` + HTTP 200) ein. `smtp`, Backup, Disk, Cache und `stale_jobs` werden dagegen nur für authentifizierte Sessions oder Caller mit dem Header `X-Health-Token` (= `DJANGO_HEALTH_DETAIL_TOKEN`) geprüft; nur diese erhalten auch die Detailfelder. Die vollständige, autoritative Feld-Referenz steht im [Monitoring-Guide](monitoring-guide.md).

### 5.2 Monitoring-Integration

**Uptime Kuma / Healthchecks.io:**

```
https://anlaufstelle.meine-einrichtung.de/health/
Erwarteter HTTP-Status: 200
Erwarteter Antwort-Inhalt: "status": "ok"
Prüfintervall: 1 Minute
```

Dieses anonyme Liveness-Monitoring erkennt insbesondere **kein fehlendes oder veraltetes Backup** und keinen SMTP-Ausfall. `DJANGO_HEALTH_DETAIL_TOKEN` erreicht `web` über die ausgelieferte Allowlist. Senden Sie den starken Token als `X-Health-Token` und prüfen Sie zusätzlich den JSON-Wert `status`; alternativ überwachen Sie Backup und Mailweg separat.

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

Die Container-Logs sind in allen mitgelieferten Compose-Dateien begrenzt und rotieren automatisch; Werte, Wirkung und Anpassung beschreibt das [Betriebshandbuch § 4](ops-runbook.md#4-log-analyse).

### 5.4 CSP-Debugging

Die Content-Security-Policy (CSP) wird **zentral in Django** über [`django-csp`](https://django-csp.readthedocs.io/) gesetzt (siehe [`src/anlaufstelle/settings/base.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py), `CONTENT_SECURITY_POLICY`). Eine zweite CSP-Konfiguration im Caddyfile gibt es nicht — nur so ist sichergestellt, dass App- und Reverse-Proxy-Policy nicht auseinanderlaufen.

**Inline-Skripte sind nicht erlaubt.** Alle JavaScript-Logik liegt in externen Dateien unter `src/static/js/`, eingebunden per `<script src=…>` oder über Nonce-Aware-Template-Tags.

**`script-src` global ohne `'unsafe-eval'`.** Mit der Migration auf den `@alpinejs/csp`-Build (v0.10.2) ist `'unsafe-eval'` aus der globalen Policy entfernt. Alle Alpine-Komponenten sind als `Alpine.data()`-Komponenten in den Modulen unter [`src/static/js/alpine/`](https://github.com/anlaufstelle/app/tree/main/src/static/js/alpine) registriert; Architektur-Tests verbieten Inline-`x-data="{...}"` und komplexe Expressions (Ternaries, `||`/`&&`, Method-Calls, Object-Literale) in Alpine-/HTMX-Direktiven.

**Ausnahme `/admin-mgmt/*` (Django-Admin):** django-unfold lädt einen eigenen Alpine-Build, der für die Cmd+K-Suche `new AsyncFunction()`-basierte Auswertung nutzt und damit ohne `'unsafe-eval'` nicht initialisiert. Die [`AdminCSPRelaxMiddleware`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/) ergänzt `'unsafe-eval'` deshalb **per Request nur für Admin-Routen** — diese sind durch MFA-Gate und Rolle `facility_admin` (bzw. `super_admin`) zusätzlich geschützt. Außerhalb des Admins bleibt die strenge globale Policy aktiv.

**`base-uri 'none'` und `form-action 'self'`** (#1904): Die Richtlinie verbietet jedes `<base>`-Element — ein eingeschleustes `<base href>` würde sonst alle relativen Adressen der Seite auf eine fremde Herkunft umlenken — und lässt Formularziele, auch `formaction`-Schaltflächen, nur auf der eigenen Herkunft zu; Chromium prüft zusätzlich Weiterleitungen nach dem Absenden. Beide Direktiven gelten auf allen Seiten einschließlich der Fehler- und Wartungsseiten und bleiben auch im Admin-Bereich unverändert. Ein Verstoß erscheint in der Browser-Konsole (Chromium) als `Sending form data to '…' violates the following Content Security Policy directive: "form-action 'self'"` bzw. `Refused to set the document's base URI to '…' because it violates the following Content Security Policy directive: "base-uri 'none'"`, das Formular wird nicht abgeschickt, und der Browser meldet den Verstoß an `/csp-report/` (Log-Eintrag `csp_violation`). Braucht ein Betrieb ein Formular mit fremdem Ziel (etwa ein externes Anmelde- oder Zahlungsportal), ergänzt er dessen Herkunft bei `form-action` in `CONTENT_SECURITY_POLICY` in `settings/base.py` — nicht im Reverse-Proxy: ein zweiter CSP-Header dort schränkt die Richtlinie nur weiter ein, erweitert sie aber nie.
Für HTML-Antworten unter `/admin-mgmt/*` baut dieselbe Middleware außerdem `style-src` auf `'self' 'unsafe-inline'` um und entfernt dort Hash- oder Nonce-Quellen; Nicht-HTML-Antworten und alle übrigen Routen behalten die strenge globale Policy. Diese eng begrenzte Ausnahme ist nötig, weil django-unfold einen dynamisch aus der Farbkonfiguration erzeugten `<style>`-Block und Inline-`style`-Attribute ausliefert, die sich nicht mit einem festen Hash freigeben lassen und andernfalls Farbschema sowie Layout der Admin-Oberfläche verlieren.

**Typische Fehlerbilder im Browser-Console:**

- `Refused to execute inline script because it violates the following Content Security Policy directive` — Inline-`<script>`-Block im Template. Auslagern in eine statische JS-Datei oder über ein Nonce-Aware-Template-Tag einbinden.
- `Refused to load the script … because it violates … directive: "script-src 'self'"` — externes Script-CDN wird nicht unterstützt; alle Skripte müssen aus `self` stammen.
- `Refused to evaluate a string as JavaScript because 'unsafe-eval' is not an allowed source` — auf normalen Routen erwartet (Architektur-Bruch); im Admin-Bereich Hinweis darauf, dass die Relax-Middleware nicht greift (Route-Pattern in [`AdminCSPRelaxMiddleware`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/) prüfen).

Bei CSP-Fehlern nach einem Update: Browser-Console auf **konkret blockierte URL/Quelle** prüfen und entscheiden, ob die Quelle ins Template verschoben oder die CSP-Richtlinie angepasst werden muss.

### 5.5 Compliance-Dashboard

Der Superadmin-Bereich `/system/compliance/` (#919) bündelt typisierte Health-Checks in einem Dashboard: die drei DB-Rollen-Checks (über `check_db_roles`, siehe Datenbank-Variablen in [§ 1](#1-installation-docker-compose)), die Prüfung, dass kein Anwendungskonto Djangos `is_superuser`-Flag trägt („App-User kein Django-Superuser“; geprüft werden Benutzerkonten, nicht die Datenbankrollen), Backup-Alter, Restore-Verified-Alter, ClamAV-Ping + Signaturalter, Retention-Run-Alter, Audit-Log-Aufbewahrung, Frische der Hintergrundjobs (Audit-Ketten-Prüfung, Statistik-Snapshot, Breach-Scan, Materialized-View-Refresh), MFA-Quote (MFA-pflichtige Rollen), ausstehende Django-Migrationen, App-/Django-/Python-Versionen sowie kritische Audit-Events der letzten 24 Stunden.

Steht die AuditLog-Aufbewahrung in einer oder mehreren Einrichtungen auf `0` Monate, zeigt die Kategorie **Retention** eine aggregierte Warnung mit den betroffenen Einrichtungsnamen; beheben lässt sie sich unter **Verwaltung → Einstellungen → Datenschutz und Fristen**.

Jeder Check liefert einen Status `ok` / `warning` / `critical` / `unknown`; ein einzelner Check-Fehler kippt das Dashboard nicht. Der Bereich ist **Super-Admins** vorbehalten (`facility_admin` erhält `403`). Die Frische-Schwellen der Hintergrundjobs und des Restore-Markers sind im [Ops-Runbook](ops-runbook.md) dokumentiert.

**Admin-DB-Rolle: Ergebnis des Wartungsdienstes.** Die Admin-/Wartungsrolle lässt sich aus dem Web-Container heraus nicht prüfen — die Admin-Zugangsdaten liegen dort bewusst nicht vor (#1962: der `web`-Dienst bekommt eine ausdrückliche Environment-Allowlist ohne `POSTGRES_ADMIN_USER`/`POSTGRES_ADMIN_PASSWORD`). Geprüft wird deshalb im Wartungsdienst: `manage.py check_db_roles` läuft bei jedem Deploy (`docker-migrate.sh`) und bei der Erstinbetriebnahme (Schritt 5 von `scripts/initial-setup.sh`) und hinterlegt sein Ergebnis als Systemeintrag „DB-Rollen-Prüfung abgeschlossen“ (`DB_ROLES_VERIFIED`) im Audit-Log (#2415). Die Karte **Admin-DB-Rolle** liest den jüngsten Eintrag: `ok` mit Prüfzeitpunkt, wenn App-Rolle, Admin-Rolle und Ownership korrekt waren; `critical` mit den Befundcodes (etwa `app_role_bypassrls` oder `ownership`), wenn die Prüfung Fehler fand; `warning`, wenn nicht jede Rolle konfiguriert war oder die letzte erfolgreiche Prüfung älter als 30 Tage ist — wer seltener deployt, führt `docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles` bei Bedarf von Hand aus. Der Eintrag enthält nur das Ergebnis und feste Befundcodes, keine Rollennamen und keine Zugangsdaten. Liegt noch kein Eintrag vor, steht die Karte auf `unknown` (neutral, grau) und nennt denselben Befehl als Prüfweg (#2310). Das ist auch direkt nach der allerersten Migration so: `docker-migrate.sh` prüft die Rollen vor `migrate`, wenn die Audit-Tabelle noch fehlt, und lässt den Eintrag dann aus, ohne das Gate abzubrechen; den ersten Eintrag schreibt Schritt 5 von `scripts/initial-setup.sh`. Bleibt der Eintrag aus, zeigt die Karte weiter den vorigen Stand. Das geschieht, wenn das Schreiben scheitert (Meldung `DB_ROLES_VERIFIED marker not written …` auf stderr, etwa weil `docker-migrate.sh` die Rollen vor `migrate` prüft und die Audit-Tabelle noch das alte Schema hat), oder wenn `docker-migrate.sh` schon vorher mit `FATAL: Migrate-Connection umgeht RLS nicht …` abbricht und `check_db_roles` gar nicht startet. In beiden Fällen den oben genannten Befehl `docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles` erneut ausführen (nach `migrate` bzw. nach der Behebung): Befunde und eine unvollständige Konfiguration hinterlegt er auch dann, wenn die Admin-Rolle ihren RLS-Bypass verloren hat; nur ein `ok` setzt eine Verbindung mit RLS-Bypass voraus. Sind die Admin-Zugangsdaten im Web-Prozess vorhanden, prüft das Dashboard die Rolle direkt; ein falsches Attributprofil meldet es dann als `critical`.

### 5.6 Error-Tracking & Datenminimierung

Das Error-Tracking (Sentry-kompatibel) ist **vollständig opt-in**: Das SDK wird nur initialisiert, wenn `SENTRY_DSN` gesetzt ist ([`prod.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/prod.py)). Ohne DSN verlässt **keinerlei Telemetrie** die Installation — es gibt kein gehostetes Angebot dieses Projekts, die Datenresidenz liegt vollständig beim Betreiber. Ein Ausfall oder Fehlen des Trackers beeinträchtigt die Anwendung nicht; Fehler stehen weiterhin in den Server-Logs (siehe **Logging** in [§ 1](#1-installation-docker-compose)).

**Nur serverseitiges SDK.** Eingebunden ist ausschließlich das Server-SDK (`sentry-sdk[django]`), kein Browser-SDK. Session Replay, Feedback-Widgets, clientseitige Breadcrumbs und Browser-Profiling existieren im Projekt nicht — es kann also nichts davon unbemerkt mitlaufen. Genutzt werden nur Server-Error-Events und optionales Trace-Sampling (`SENTRY_TRACES_SAMPLE_RATE`).

**Was gesendet und was entfernt/maskiert wird.** Sobald das Tracking aktiv ist, greifen mehrere Schichten der Datenminimierung, bevor ein Event die Installation verlässt:

- `send_default_pii=False` — keine Cookies, keine Client-IP.
- `include_local_variables=False` — Stack-Frame-Locals werden nicht erfasst (ein Frame könnte z. B. eine entschlüsselte Notiz enthalten).
- Ein serverseitiger `before_send`-Hook entfernt Request-Bodies, Cookies und Query-Strings, maskiert sensible Header und Schlüssel (`Authorization`, Passwörter, Tokens …), zieht Exception- und Log-Messages sowie die aus Log-Zeilen gebauten **Breadcrumbs** durch einen Text-Scrubber (Emails/Tokens werden zu Platzhaltern) und maskiert frei befüllbare `extra`-Werte.
- Ein `before_send_transaction`-Hook wendet dasselbe Scrubbing auf **Performance-Transactions** an (diese umgehen `before_send` im SDK): Request-Kontext inklusive `query_string` (Suchbegriffe können Klient*innen-Namen sein), Span-Beschreibungen und -Daten sowie der Transaction-Name werden mitbehandelt.
- **URL-tragende Felder werden strukturell maskiert** (#2198). Betroffen sind `request.url`, die Header `Referer` und `HX-Current-Url`, der Transaction-Name sowie Pfade in Log-Messages, Span-Beschreibungen und Breadcrumb-Daten. Zwei Schritte: Der Query-String fällt weg — dort steht der Suchbegriff, und ein Suchbegriff ist oft ein Klient*innen-Name, der sonst über den `Referer` jeder Folgeseite mitgereist wäre. Und der Pfad wird gegen das Django-Route-Schema aufgelöst: aus `/invite/MTIz…/cx1a2b-…/` wird `/invite/<uidb64>/<token>/`. Die live nutzbaren Einladungs-, Passwort-Reset- und Konto-Wiederherstellungs-Token stehen damit nicht mehr im Tracker, die Route bleibt für die Fehlersuche lesbar. Dabei werden auch eine Subpath-Installation (`SCRIPT_NAME`/`FORCE_SCRIPT_NAME` — der Präfix wird zum Auflösen abgetrennt und danach wieder vorangestellt) und ein fehlender Schrägstrich am Ende berücksichtigt. Löst ein Pfad gar nicht auf (typisch bei `404`), greift eine Heuristik: Pfadsegmente ab 20 Zeichen aus Token-Zeichen werden zu `[scrubbed]`, sofern sie ein Token-Signal tragen — einen langen zusammenhängenden Zeichenlauf (Hash, Base64) oder den Doppelpunkt der signierten Wiederherstellungs-Token. Prozentkodierte Schreibweisen zählen dabei mit: ein Browser schickt den Link im `Referer` so, wie er in der Adresszeile steht (`%3A` statt `:`), deshalb werden Pfadsegmente vor der Prüfung kontrolliert dekodiert — ein kodierter Schrägstrich (`%2F`) bleibt dabei Teil des Segments und wird nicht zum Trenner. Steht eine URL mitten in einer Log-Zeile — etwa in der CSRF-403-Zeile, die Pfad und Referer zusammen protokolliert —, wird sie dort genauso behandelt; Satzzeichen um die URL herum bleiben stehen.
- **Aus Log-Zeilen geht nur das Muster hinaus, nicht der eingesetzte Wert** (#2198). Eine Log-Zeile besteht aus einer Vorlage (`"Backend %s übersprungen (facility=%s)"`) und den eingesetzten Werten. Das SDK überträgt beides — die Vorlage, die fertig zusammengesetzte Zeile und die Roh-Argumente — und baut aus jeder Log-Zeile zusätzlich eine Verlaufsspur (Breadcrumb) an spätere Ereignisse. Ob in einem eingesetzten Wert ein Personen- oder Einrichtungsname steckt, lässt sich einem Text nicht ansehen; deshalb gilt hier die harte Regel statt einer Mustererkennung: **die zusammengesetzte Zeile wird durch die Vorlage ersetzt und die Argumente werden verworfen** — in den Ereignisfeldern ebenso wie in der Verlaufsspur, deren Zusatzdaten auf Logger und Level eingedampft werden. Im Tracker steht dann `"Backend %s übersprungen (facility=%s)"` samt Logger-Namen und Schweregrad: genug, um die Stelle im Code zu finden, ohne einen einzigen eingesetzten Wert mitzunehmen. Auch die Zusatzdaten der Verlaufsspur werden dafür **neu gebaut** statt gefiltert — übernommen wird nur, was das Logging-System selbst vergibt (Logger-Name und Schweregrad); alles, was eine Log-Zeile über `extra` frei mitgeben kann, fällt weg.
- **Personenbezogene Schlüssel-Wert-Paare in freien Texten** (Fehlermeldungen, Span-Beschreibungen) werden zusätzlich maskiert (#2198): `user=`, `username=`, `login=`, `email=`, `name=` — auch mit Präfix (`facility_name=`), auch mehrwortig, auch in Anführungszeichen oder mit Doppelpunkt (`"user": "Anna Mustermann"`). Der Wert wird bis zum nächsten Trennzeichen oder bis zum nächsten Schlüssel entfernt, Format-Platzhalter (`user=%s`) bleiben stehen. Wortenden wie `superuser=`, `filename=` oder `hostname=` sind nicht betroffen. In strukturierten Feldern werden die Schlüssel `user`, `username`, `login`, `name`, `full_name`, `display_name`, `facility_name` und `pseudonym` **exakt** abgeglichen — die technischen Kennungen `user_id`, `user_pk` und `request_id` bleiben damit lesbar.
- **Klienten-IP und Proxy-Ketten** werden maskiert (#2198): `remote_addr` aus einem CSP-Verstoßbericht ebenso wie die Header `Forwarded` (RFC 7239), `X-Forwarded-For`, `X-Real-IP`, `True-Client-IP`, `CF-Connecting-IP` und `Via`. Das SDK filtert zwar einige davon selbst, den standardisierten `Forwarded`-Header aber nicht.

**Grenzen — offen benannt.** Die Scrubber arbeiten regel- und schlüsselbasiert (Best-Effort, Defense-in-Depth); sie ersetzen keine inhaltliche Prüfung jedes einzelnen Event-Feldes. Das gilt auch für die URL-Maskierung: Sie kennt die Routen dieser Anwendung, nicht die Bedeutung fremder Werte. Ein Geheimnis, das eine Log-Zeile als Fließtext statt als Pfad nennt, ein kurzes Pfadsegment auf einer Route, die sich nicht auflösen lässt, oder ein Klarname in einer selbst geschriebenen Fehlermeldung passieren weiterhin — ebenso wie Werte, die eine künftige Route im Pfad führt, ohne dass jemand daran gedacht hätte. Umgekehrt kann die Heuristik danebenliegen: ein tokenförmiges Segment auf einem nicht auflösenden Pfad wird maskiert, auch wenn es harmlos war — im Zweifel steht die vollständige Zeile weiterhin in den Server-Logs. Und ein Transaktionsname, den die Anwendung selbst setzt (`transaction_info.source` ist dann nicht `url`), bleibt unangetastet. Zwei SDK-Funktionen sind heute **aus** und deshalb nicht abgedeckt: die Sentry-eigene Log-Übertragung (`enable_logs`) und das Streaming einzelner Trace-Abschnitte — beide laufen am `before_send`-Hook vorbei und dürfen nur nach einer erneuten Prüfung eingeschaltet werden. Die wichtigste verbleibende Grenze folgt aus der Log-Regel: geschützt ist der **eingesetzte** Wert, nicht die Vorlage. Wer einen Namen direkt in den Text einer Log- oder Fehlermeldung schreibt (`"Anna Mustermann nicht gefunden"` statt `"%s nicht gefunden"`), umgeht damit jede Automatik — für die Entwicklung gilt deshalb: **keine Klarnamen in Log-Vorlagen und Fehlertexten**. Ebenso wenig erkennbar ist ein Name in einer freien Fehlermeldung ohne Schlüssel davor. Und: was hier beschrieben ist, gilt für das, was den Tracker erreicht. In der Server-Logdatei stehen dieselben Zeilen, jedoch mit maskierten URL-Pfaden (#2228): Der Query-Teil entfällt, ein Pfad erscheint als Route-Schema (`/invite/<uidb64>/<token>/`) oder verliert seine Token-Segmente, und die Zeile zu einem CSRF-403 nennt das Konto als `user_pk` statt als Login-Namen — auch `Referer` und `Origin` stehen dort nur noch ohne Query-Teil. Das gilt in **beiden** Ausgabeformaten (`LOG_FORMAT=text` und `LOG_FORMAT=json`) und auch für die Zeilen, die das Framework selbst zu abgewiesenen, verbotenen und nicht gefundenen Aufrufen schreibt. Nicht maskiert wird die Client-IP (`remote_addr`) eines CSP-Verstoßberichts: Sie ist die einzige Handhabe gegen Missbrauch dieser unangemeldeten Fläche und deshalb bewusst lesbar (siehe **Logs** weiter unten). Wer maximale Datensparsamkeit braucht, wählt eine der folgenden Stufen:

1. **`SENTRY_DSN` nicht setzen** — empfohlen für hochsensible Deployments, insbesondere auf älteren Ständen, deren SDK-Scrubber noch nicht alle oben genannten Schichten enthält: kein Error-Tracking-Prozessor, kein Datenabfluss; Fehler stehen weiterhin in den Server-Logs.
2. **`SENTRY_TRACES_SAMPLE_RATE=0`** gegen einen Tracker **innerhalb der eigenen Trust-Boundary** (z. B. selbst gehostet): schaltet Performance-Transactions ganz ab und beschränkt den Abfluss auf gescrubbte Error-Events an eine Instanz, die keine zusätzliche Drittpartei ist.

**Logs.** Die Server-Logs werden in **beiden** Formaten PII-gescrubbt — sowohl der Default `LOG_FORMAT=text` als auch `LOG_FORMAT=json` ziehen Messages, Tracebacks und die strukturierten Zusatzfelder durch denselben Scrubber (Mailadressen, Passwörter und Token-artige Werte werden zu Platzhaltern). **Nicht** gescrubbt werden zwei Felder, die technische Sicherheitsereignisse zuordenbar machen sollen und deshalb bewusst im Klartext stehen: die Client-IP (`remote_addr`) und der Browser-Kennstring (`user_agent`) einer CSP-Verstoßmeldung. Beide sind personenbeziehbar — wer eine kurze Log-Aufbewahrung braucht, plant sie dafür ein. Unverändert passieren außerdem die rein technischen Kennungen (`request_id`, `user_id`, `facility_id`, `user_pk`): Sie enthalten keinen Freitext, sondern verweisen nur auf einen Datensatz, der Scrubber hat dort nichts zu greifen. `user_pk` erscheint zum Beispiel in den Einladungs-Log-Zeilen, die bewusst weder Benutzernamen noch Mailadresse führen — ohne diese Kennung wäre der Eintrag keinem Konto mehr zuzuordnen. Für DSGVO-Kontexte mit Log-Aggregation empfiehlt sich weiterhin `LOG_FORMAT=json` (strukturiert, maschinenlesbar); die Datenminimierung ist aber nicht mehr an dieses Format gebunden.

**Selbst gehosteter Tracker (GlitchTip).** GlitchTip akzeptiert Standard-Sentry-DSNs, und dieses Projekt nutzt nur Kern-SDK-Features (Error-Events plus optionales Trace-Sampling). Diese Kombination wurde empirisch geprüft: Mit `sentry-sdk[django]` `2.64.0` gegen GlitchTip `6.2.1` (Docker-Image `glitchtip/glitchtip:6`) werden Server-Error-Events übertragen, plausibel geparst (Stacktrace, Tags, Request-Kontext) und als Issues angezeigt; Performance-Transactions (`SENTRY_TRACES_SAMPLE_RATE > 0`) werden ebenfalls **angenommen** und als Transaction-Gruppen einsortiert — sie werden also weder still verworfen noch lösen sie einen Fehler aus. Der serverseitige `before_send`/`before_send_transaction`-Scrubber greift dabei unverändert (keine Frame-Locals, gestrippte Query-Strings/Cookies/Request-Bodies); die weiter oben beschriebenen regel- und schlüsselbasierten Grenzen bleiben bestehen. Ausgeliefert wird derzeit `sentry-sdk[django]` `2.71.0` (`requirements.txt`); gegen diese und andere Versionskombinationen ist die Prüfung nicht wiederholt worden, und ein Fremdprojekt bleibt naturgemäß außerhalb der Kontrolle dieses Projekts.

**Gehosteter Sentry-Dienst.** Wer statt einer eigenen EU-/Self-Hosted-Instanz einen SaaS-Anbieter nutzt, muss vor Aktivierung Datenstandort, Aufbewahrungsfrist und mögliche Drittlandübermittlungen samt Garantien prüfen und einen AVV schließen. Anbieter und dessen Unterauftragnehmer gehören in § 6 der AVV-Vorlage im DSGVO-Paket.

So binden Sie einen selbst gehosteten Tracker an:

1. GlitchTip-Instanz betreiben und dort ein Projekt anlegen — GlitchTip zeigt dessen DSN im Format `https://<public_key>@<glitchtip-host>/<project_id>`.
2. Diesen Wert als `SENTRY_DSN` an die App geben — **keine Codeänderung nötig**. Optional `SENTRY_TRACES_SAMPLE_RATE` setzen (Default `0.1`; `0` schaltet Transactions ganz ab, siehe Eskalationsstufen oben).
3. Erwartung: Server-Error-Events erscheinen als Issues, optionales Trace-Sampling als Transaction-Gruppen. Es läuft ausschließlich das Server-SDK (kein Browser-SDK, kein Session Replay).

Betrieb der Tracker-Instanz (Setup, Backups, Retention, Upgrades) ist Betreiber-Infrastruktur und in der [GlitchTip-Dokumentation](https://glitchtip.com/documentation) beschrieben — hier **out of scope**; die Tracker-Datenbank gehört in dieselbe Trust-Boundary wie die App-Daten und braucht dieselben Zugriffs- und Löschregeln.

> **Kein Rechtsrat:** Dieser Abschnitt beschreibt technische Maßnahmen. Datenschutz-Folgenabschätzung und Verzeichnis der Verarbeitungstätigkeiten verbleiben beim Betreiber als Verantwortlichem.

### 5.7 Breach-Detection-Scan

Das Management-Kommando `detect_breaches` wertet das Audit-Log heuristisch auf Anomalien aus (fehlgeschlagene Login-Bursts, Massen-Exporte/-Löschungen, verteilte Login-Angriffe) und schreibt Treffer als Audit-Events; optional wird ein Webhook (`BREACH_NOTIFICATION_WEBHOOK_URL`) benachrichtigt. Die Fenster/Schwellen werden über die `BREACH_*`-Variablen gesteuert (siehe [ENV-Referenz in § 1](#vollständige-umgebungsvariablen-referenz) sowie [Ops-Runbook § 6.5b](ops-runbook.md)).

> **Bereits automatisiert (Refs #794):** Der mitgelieferte `cron`-Sidecar aus `docker-compose.prod.yml` führt diesen Job ab Werk aus. Das folgende Crontab-Beispiel gilt nur, wenn der `cron`-Dienst nicht läuft; es startet den Job über den `maintenance`-Dienst derselben Compose-Datei, der als einziger die Admin-Rolle (BYPASSRLS) erhält (ohne Compose in der eigenen Orchestrierung nachbilden: gleiches Image, Entrypoint `python manage.py`, Admin-Rolle). Wer beides einrichtet, lässt den Job doppelt laufen — Details: [Ops-Runbook § 3](ops-runbook.md#3-cron-jobs).

**Empfohlener Cron (stündlich, jeweils zur 30. Minute):**

```cron
30 * * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance detect_breaches >> /var/log/anlaufstelle-breach.log 2>&1
```

> **Hinweis:** Die vollständige, autoritative Liste aller Hintergrundjobs samt Zeitplan (Backup, Retention, Audit-Ketten-Verifikation, Statistik-Snapshots, Orphan-Cleanup, Breach-Scan, View-Refresh) steht im [Ops-Runbook § 3](ops-runbook.md#3-cron-jobs). Im mitgelieferten Compose-Stack laufen sie im `cron`-Sidecar (`deploy/crontab`), auf dem Dev-Server als systemd-Timer via `dev-ops/deploy/install-timers.sh` — in beiden Fällen der Breach-Scan stündlich zur 30. Minute.

---

## 6. Troubleshooting

### Problem: Web-Dienst startet nicht

**Symptom:** `docker compose -f docker-compose.prod.yml ps` zeigt `web` als `Restarting`/`Exit 1`, oder der Migrate-/Maintenance-Lauf bricht ab, bevor `web` überhaupt startet.

**Vorgehen:**

```bash
# Konfiguration erneut prüfen, ohne Migrationen auszuführen
docker compose -f docker-compose.prod.yml run --rm \
  --entrypoint sh maintenance -c 'python manage.py check --deploy'

# Falls web bereits angelegt wurde
docker compose -f docker-compose.prod.yml logs web
```

Bei einem Abbruch von `initial-setup.sh` steht die ursprüngliche Fehlermeldung bereits in dessen Terminalausgabe. Der erste Befehl oben prüft nur die aktuelle Django-Konfiguration; er startet bewusst keinen weiteren Migrationslauf.

Häufige Ursachen:

| Fehlermeldung | Lösung |
|---|---|
| `DJANGO_SECRET_KEY muss in Produktion gesetzt sein.` | `DJANGO_SECRET_KEY` in `.env` setzen (siehe [Abschnitt 1.2](#schritt-2-umgebungsvariablen-konfigurieren)) |
| `ENCRYPTION_KEY oder ENCRYPTION_KEYS muss in Produktion gesetzt sein.` | `ENCRYPTION_KEYS` oder `ENCRYPTION_KEY` in `.env` setzen (siehe [Abschnitt 1.2](#schritt-2-umgebungsvariablen-konfigurieren)) |
| `DJANGO_AUDIT_HASH_KEY muss in Produktion gesetzt sein` | `DJANGO_AUDIT_HASH_KEY` (separat vom Secret Key) in `.env` setzen (siehe [Abschnitt 1.2](#schritt-2-umgebungsvariablen-konfigurieren)) |
| `connection refused` (Datenbank) | Prüfen, ob `db` läuft: `docker compose -f docker-compose.prod.yml ps db` |
| `django.db.utils.OperationalError` / `password authentication failed` | Stimmen Rollen und `.env` nach einer Passwortänderung nicht mehr überein, Passwörter koordiniert mit `rotate-db-passwords.sh` rotieren. Bei einer Erstinstallation zuerst den Abschnitt [DB-Init abgebrochen](#problem-db-init-abgebrochen-rollen-fehlen) prüfen. |
| `ImproperlyConfigured` | Umgebungsvariablen unvollständig – Logs für Details lesen |

### Problem: `web` wird `unhealthy`, `caddy` startet nicht

Der Container-Healthcheck ruft `http://localhost:8000/health/` auf. Im Produktions-Compose lösen nur `error`/HTTP 503 (Datenbank oder Encryption-Key) sowie eine abgewiesene Probe wie `DisallowedHost` diesen Zustand aus; `degraded` nicht. Fehlt `localhost` in `ALLOWED_HOSTS`, weist Django diese interne Probe ab und `caddy` bleibt im Zustand `Created`.

```bash
docker compose -f docker-compose.prod.yml logs web | grep DisallowedHost
```

Ergänzen Sie `localhost,127.0.0.1` wie in [Schritt 2](#schritt-2-umgebungsvariablen-konfigurieren) gezeigt und erstellen Sie `web` anschließend neu.

### Problem: `cron` wird als `unhealthy` angezeigt

Der `cron`-Sidecar hat keinen HTTP-Endpunkt; sein Image-Healthcheck muss im Produktions-Compose deaktiviert sein. Prüfen Sie, ob Sie die aktuelle `docker-compose.prod.yml` verwenden, und erstellen Sie den Dienst neu:

```bash
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d --force-recreate cron
docker compose -f docker-compose.prod.yml ps cron
docker compose -f docker-compose.prod.yml logs cron
```

### Problem: ClamAV-Signatur ist veraltet

Ein Signaturstand älter als sieben Tage ist kein normaler Anlaufzustand. Prüfen Sie Update-Protokoll und installierte Signaturversion:

```bash
docker compose -f docker-compose.prod.yml exec clamav tail -n 30 /var/log/clamav/freshclam.log
docker compose -f docker-compose.prod.yml exec clamav clamdscan --version
```

Kann das Signaturalter noch nicht ermittelt werden, bleibt der Dashboard-Status
`unknown` — damit erzeugt der normale Erstlauf keine Fehlwarnung. Die Zeile
„ClamAV-Signatur“ zeigt dann den ersten Befehl oben als Handlungshinweis. Bleibt
der Zustand nach dem Anlauf bestehen, führen Sie ihn aus und prüfen Sie das
Protokoll auf DNS-, Download- oder Mirror-Fehler.

Der Signaturdownload setzt voraus, dass der `clamav`-Dienst einen funktionierenden ausgehenden Netzwerkpfad (Egress) zum Signaturserver beziehungsweise konfigurierten Mirror besitzt; dieser Pfad ist mit #2219 ausgeliefert. Das Update-Protokoll muss einen erfolgreichen Download zeigen. Wiederholte DNS-/Downloadfehler erfordern eine Prüfung dieses Egress-Pfads oder des Mirrors. Ein grüner `clamdscan --version`-Healthcheck belegt nur, dass das Binary startet, nicht dass die Signaturen aktuell sind; kontrollieren Sie den angezeigten Signaturstand zusätzlich im Compliance-Dashboard unter `/system/compliance/`.

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

Fehlgeschlagene Anmeldungen zeigt die Oberfläche: als Anwendungsbetreuung **Datenschutz → Audit-Log** (`/audit/`) öffnen und nach der Aktion „Anmeldung fehlgeschlagen“ filtern, als `super_admin` den Systembereich `/system/audit/`. Eine Abfrage per `exec web … shell` taugt dafür nicht: Ohne Einrichtungskontext liefert die Zeilensicherheit der Datenbank unter der App-Rolle keine Zeile, die Diagnose bliebe immer leer.

**Passwort zurücksetzen:**

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py changepassword <benutzername>
```

### Problem: Datenbankverbindung schlägt fehl

```bash
# TCP-Verbindung der App mit den tatsächlich konfigurierten Zugangsdaten testen
docker compose -f docker-compose.prod.yml exec web python manage.py shell -c \
  "from django.db import connection; connection.ensure_connection(); print(connection.connection.info.host)"

# Datenbank-Healthcheck-Status
docker inspect $(docker compose -f docker-compose.prod.yml ps -q db) | grep -A5 Health
```

### Problem: DB-Init abgebrochen (Rollen fehlen)

Lehnt `01-app-role.sh` beim ersten Start eines **noch leeren** Clusters ein zu kurzes Passwort oder einen Platzhalter ab, kann der neu gestartete DB-Container später trotzdem `healthy` erscheinen. Die App-/Admin-Rollen fehlen dann; PostgreSQL kann das beim Migrate-Lauf irreführend als `password authentication failed` melden.

```bash
# Ursprünglichen Init-Abbruch suchen
docker compose -f docker-compose.prod.yml logs db | grep 01-app-role

# Vorhandene Anwendungsrollen prüfen
docker compose -f docker-compose.prod.yml exec db \
  psql -U postgres -d anlaufstelle -c \
  "SELECT rolname FROM pg_roles WHERE rolname IN ('anlaufstelle', 'anlaufstelle_admin') ORDER BY rolname;"
```

Nur wenn dies der **erste, nachweislich noch leere Installationsversuch** ist: korrigieren Sie alle drei Passwörter auf mindestens 32 Zeichen, verwerfen Sie das unvollständig initialisierte Volume und beginnen Sie erneut:

```bash
docker compose -f docker-compose.prod.yml down -v
./scripts/initial-setup.sh
```

> **Datenverlust-Warnung:** `down -v` löscht die komplette Datenbank und alle Compose-Volumes dieses Stacks. Auf einer bereits befüllten Installation darf dieser Weg niemals verwendet werden; stoppen Sie dort und stellen Sie aus einem geprüften Backup wieder her beziehungsweise holen Sie fachkundige Unterstützung. Eine Passwortrotation repariert fehlende Rollen nicht.

### Problem: Speicherplatz voll

```bash
# Docker-Speicherverbrauch analysieren
docker system df

# Nicht mehr verwendete Container, Netzwerke und Images bereinigen (keine Volumes)
docker system prune -f

# Niemals pauschal --volumes ergänzen: pgdata/media können Instanzdaten enthalten.

# Datenbank-Volume-Größe
docker system df -v | grep pgdata
```

### Problem: Berichte zeigen „unterdrückt" oder Gedankenstriche statt Zahlen

**Symptom:** Statt Zahlen stehen im Bericht Gedankenstriche („—" in der Vorschau der Datensatz-Berichte) bzw. der Hinweis „unterdrückt" (externer Bericht, Sachbericht-PDF) — teilweise in nahezu allen Zeilen.

**Das ist in aller Regel kein Defekt.** Aggregate unterhalb der K-Anonymitäts-Schwelle der Einrichtung werden ausgeblendet, damit sich aus einem Bericht keine einzelnen Personen ableiten lassen. In einer Einrichtung mit wenig Bestand — im Testbetrieb, kurz nach der Ersteinrichtung oder bei einem sehr kurzen Zeitraum — liegen praktisch alle Werte unter der Schwelle. Dass dabei zusätzlich eine sichtbare Zelle, eine Summenzeile oder eine ganze Aufschlüsselung wegfällt, gehört zum Verfahren: sonst ließe sich der unterdrückte Wert zurückrechnen.

**Benutzerseitige Doku:** [User-Guide § 7 — Statistik und Export](user-guide.md#7-statistik-und-export) — Darstellung je Dateiformat und das Vorgehen am Bildschirm (Zeitraum vergrößern, leeren Zeitraum ausschließen). Für die Administration kommen drei Punkte hinzu:

1. **Die Schwelle ist ein eigenes Setting, kein Nebeneffekt der Aufbewahrung.** Sie steht je Einrichtung unter **Verwaltung → Einstellungen** (Gruppe „Datenschutz und Fristen“, Feld `k_anonymity_threshold`, Standard 5) und wirkt auf **jedes Berichts-Artefakt, das das Haus verlässt** — unabhängig davon, ob die K-Anonymisierung im Aufbewahrungslauf aktiviert ist. [§ 7.3](#73-löschfristen-und-enforce_retention) dokumentiert beide Felder gemeinsam im Retention-Kontext; sie hängen aber nicht voneinander ab.

2. **Die interne Statistik-Übersicht ist bewusst nicht unterdrückt** und kann deshalb helfen zu prüfen, ob überhaupt Daten vorhanden sind (nur Leitung/Anwendungsbetreuung). Die interne Ereignisliste enthält Pseudonyme und ist keine anonymisierte Statistik. Einen internen PDF-Modus gibt es nicht. Abweichende Zahlen gegenüber Datensatz-Berichten sind zu erwarten; Inhalte aus internen Sichten brauchen vor einer Weitergabe eine eigene Prüfung.

3. **Auch ohne sichtbare Unterdrückung bleibt eine Grenze:** Die Zusicherung gilt je Artefakt und je Zeitraum, nicht über mehrere Herausgaben hinweg — siehe [§ 8, „Herausgabe von Berichten: was Sie wissen müssen"](#8-statistik-snapshots--materialized-view).

> **Wichtig:** Das Herabsetzen der Schwelle ist eine Datenschutz-Entscheidung, kein Bedienschritt. Sie gilt für alle Berichte, die das Haus verlassen; dokumentieren Sie die Änderung entsprechend — im Audit-Log erscheint sie als „Einstellungen geändert" mit Alt- und Neu-Wert ([§ 7.4](#74-audit-log)).

### Problem: Nutzer melden einen Serverfehler mit Referenz-ID

**Symptom:** Eine Nutzerin meldet eine Fehlerseite mit einer Referenz wie `A1B2-C3D4-E5F6`, ggf. zusammen mit dem angezeigten Kontakthinweis.

Dieselbe Referenz-ID steht in der zugehörigen Server-Log-Zeile (strukturiertes Feld `request_id`), ohne Konto- oder Einrichtungsbezug:

```bash
docker compose -f docker-compose.prod.yml logs web | grep "<ID>"
```

Das funktioniert unabhängig vom `LOG_FORMAT`: Im Text-Log steht `request_id="<ID>"` hinter der Meldung, im JSON-Log (`LOG_FORMAT=json`) im Feld `request_id`.

Der angezeigte Kontakt stammt aus der Umgebungsvariable `SUPPORT_CONTACT` (leer = allgemeiner Verweis auf die Anwendungsbetreuung der Einrichtung, ohne toten Link). Ist zusätzlich Sentry eingerichtet (`SENTRY_DSN`), entsteht zum selben Vorfall in aller Regel auch ein Sentry-Event — die Referenz-ID der Fehlerseite ist damit aber **nicht** automatisch verknüpft; ordnen Sie beide über den Zeitpunkt und den in Sentry sichtbaren Request-Pfad einander zu.

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



### Auth-Alarme und Audit-Prüfung (#2527)

Bekannte, einrichtungslose `super_admin`-Konten erhalten ab zehn Fehlanmeldungen in 15 Minuten einen `SECURITY_VIOLATION`-Alarm mit `reason=super_admin_bruteforce`, sofort im Fehlanmeldepfad und als Heuristik im vollständigen `detect_breaches`-Lauf (planmäßig stündlich). Derselbe Alarm wird je Konto 24 Stunden dedupliziert. Es entsteht keine Kontosperre. Am normalen Login (`/login/`) erhalten weitere falsche Passwörter ab der Schwelle HTTP 429 mit einer progressiven Retry-Empfehlung von 1, 2, 4 und höchstens 8 Sekunden (`Retry-After`); der Server schläft nicht, und diese Empfehlung verhindert keinen Folgeversuch mit korrektem Passwort. Die bestehenden IP-/Benutzernamen-Ratelimits bleiben wirksam und können weiterhin auch korrekte Anmeldungen abweisen.

HTTP-429-Abweisungen schreiben `RATE_LIMITED` mit maskierter Pfadklasse und festem Grund (`request_rate` bzw. `super_admin_backoff`) sowie Nutzer und Einrichtung, soweit bereits bekannt. Unaufgelöste Pfade werden nur als `unresolved` erfasst; Body, Query und konkrete URL-Tokens bleiben aus den Details. Der Cache begrenzt die Spur auf eine Zeile je Pfadklasse und bekanntem Konto/Einrichtung in 60 Sekunden (anonym gemeinsam über alle Quell-IPs). Scheitert das Audit-Schreiben, bleibt die Antwort 429 und eine ebenso gedrosselte, inhaltsfreie Sicherheitszeile nennt Pfadklasse, Grund und bekannte Kennungen. Passkey-Fehler und abgewiesene Backup-Erneuerungen zählen zusätzlich als kritische Audit-Ereignisse; erfolgreiche Verifikation und gewöhnliche Drosselung lösen allein keine Compliance-Warnung aus.

## 7. DSGVO-Hinweise

Anlaufstelle wurde nach dem Prinzip **Privacy by Design** (Art. 25 DSGVO) entwickelt. Dieses Kapitel beschreibt die datenschutzrelevanten technischen Maßnahmen und die administrativen Pflichten.

> **Haftungsausschluss:** Die folgenden technischen Maßnahmen unterstützen die DSGVO-Konformität, **ersetzen aber keine rechtliche Beratung**. Der Betreiber ist verantwortlich für die Durchführung einer Datenschutz-Folgenabschätzung (Art. 35 DSGVO), den Abschluss eines Auftragsverarbeitungsvertrags mit dem Hosting-Anbieter (Art. 28 DSGVO) und die Dokumentation der Verarbeitungstätigkeiten (Art. 30 DSGVO). Die Software wurde keinem formalen Sicherheitsaudit durch Dritte unterzogen.

### 7.1 Verschlüsselung

**Feldverschlüsselung (Fernet/AES-128):**

Sensible Felder in der Datenbank werden mit dem `ENCRYPTION_KEY` verschlüsselt gespeichert. Dies gilt als zusätzliche Schutzmaßnahme über die allgemeine Datenbankzugriffskontrolle hinaus.

**Sichtbarkeit vs. Verschlüsselung:** Die Verschlüsselung (`is_encrypted`) und Sichtbarkeitsstufe (`sensitivity`) eines Feldes haben getrennte Wirkungen; eine eigene Feldstufe Hoch erzwingt jedoch Verschlüsselung. Ein verschlüsseltes Feld kann für alle Rollen sichtbar sein, und ein nicht-verschlüsseltes Feld kann auf bestimmte Rollen eingeschränkt werden.

- Der Schlüssel wird über die Umgebungsvariable `ENCRYPTION_KEY` bereitgestellt.
- In der Produktion verweigert die Anwendung den Start, wenn kein Schlüssel gesetzt ist.
- Ein Schlüsselverlust bedeutet den dauerhaften Verlust der verschlüsselten Daten.

**Schlüsselrotation:** Eine automatische Schlüsselrotation gibt es nicht. Die manuelle Rotation beschreibt [§ 2.9 „Schlüssel-Rotation“](#schlüssel-rotation-encryption_keys); sie schlüsselt mit `reencrypt_fields` Datenbankfelder und mit `reencrypt_files` Dateianhänge um; alte Schlüssel erst nach ungefilterter Vollprüfung und Ablauf der damit verschlüsselten Backups entfernen. Sichern Sie alle Schlüssel des Rings redundant und getrennt vom Datenbank-Backup.

**Transportverschlüsselung:** Caddy erzwingt HTTPS und leitet HTTP automatisch auf HTTPS weiter.

**Sicherheits-Header — maßgeblich ist die Anwendung:** `Strict-Transport-Security` (`max-age=31536000; includeSubDomains; preload`), `X-Content-Type-Options` (`nosniff`), `X-Frame-Options` (`DENY`) und `Referrer-Policy` (`strict-origin-when-cross-origin`) setzt ausschließlich die Anwendung — die Werte stehen in [`src/anlaufstelle/settings/prod.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/prod.py). Wer eine dieser Vorgaben ändern will (etwa die HSTS-Dauer), ändert sie dort. Im `Caddyfile` stehen sie bewusst **nicht** mehr: gesetzt an beiden Stellen kamen sie beim Client doppelt an, und eine Änderung an einer Stelle schien wirkungslos (#2314). Caddy setzt nur noch, was die Anwendung nicht setzt — derzeit `X-Robots-Tag`. Dieselbe Trennung gilt seit jeher für die Content-Security-Policy (§ 5.4). Damit gelten die Header auch dann, wenn Sie die Anwendung hinter einen eigenen Reverse-Proxy stellen. Die vier Header liegen auf **jeder** Antwort der Anwendung — auch auf ausgelieferten Dateien unter `/static/` und auf der Wartungsseite (HTTP 503), die beide nicht den vollen Verarbeitungsweg durchlaufen. Auch die Content-Security-Policy liegt auf der Wartungsseite — dieselbe Richtlinie wie auf jeder anderen Seite (#2339).

Von Caddy selbst erzeugte Fehlerantworten — etwa HTTP 413 bei einem zu großen Upload oder 502/504 bei nicht erreichbarer Anwendung — tragen diese Header nicht; das ist bewusst akzeptiert, weil eine zweite Headerquelle im Proxy den Einzelquellen-Vertrag aufheben würde und diese Antworten keine Anwendungsdaten enthalten.

### 7.2 Pseudonymisierung

Vorgesehen ist die Pseudonymisierung: Die Anwendung hat kein Pflichtfeld für Klarnamen, Personen werden unter einem Pseudonym geführt. Technisch verhindern kann sie Klarnamen nicht — Pseudonym, Notizen, Freitextfelder und Anhänge enthalten, was das Team einträgt oder hochlädt; auch Ereignisse ohne Person können über Freitext, Ort oder Anhänge personenbeziehbar sein. Entscheidend ist die Eingabepraxis der Einrichtung. Im Einzelnen:

- Personen werden über interne IDs referenziert.
- Anzeigenamen in der Oberfläche sind konfigurierbare Pseudonyme.
- Wer welche Daten sieht, richtet sich nicht nach der Kontaktstufe, sondern nach der Sensitivität (der höheren Stufe aus Dokumentationstyp und Feld) und der Rolle: Assistenz sieht „Normal", Fachkraft bis „Erhöht", Leitung und Anwendungsbetreuung alle Stufen. Die Personen-Detailseite ist ab Assistenz erreichbar, auch bei „Mit Zusatzangaben"; die Assistenz sieht dort etwa Kontakte samt Notiz, aber kein Krisengespräch. Die Kontaktstufe wirkt auf zulässige Dokumentationstypen, Vier-Augen-Löschung, das Protokoll beim Öffnen der Person (`VIEW_QUALIFIED`) und die Fristen.
- Die Systemadministration (`super_admin`) liest einrichtungsübergreifend über den `/system/`-Bereich und über die Verwaltungsoberfläche (`/admin-mgmt/`, nur mit Sudo), dort Personen, Fälle, Ereignisse, Aufgaben und Anhänge aller Einrichtungen. Die Row Level Security lässt diese Sitzungen über `app.is_super_admin` durch (§ 7.9). Diese Lesezugriffe protokolliert das AuditLog als `SYSTEM_VIEW` für alle registrierten Verwaltungsmodelle einschließlich Löschanträgen, Benutzern und Konfiguration; schlägt das Protokollieren fehl, wird die Ausgabe mit HTTP 403 gesperrt. Ereignisse und Ereignis-Historie zeigen im Admin ausschließlich Metadaten ohne Feldinhalte.

### 7.3 Löschfristen und `enforce_retention`

Löschfristen werden pro Einrichtung in den Einstellungen konfiguriert (siehe [Abschnitt 2.3](#23-einstellungen-pro-einrichtung)).

Das Management-Kommando `enforce_retention` setzt die konfigurierten Fristen durch. Je nach Datenart löscht es weich (Soft-Delete), hart, anonymisiert oder redigiert. Beim Soft-Delete eines Ereignisses bleibt nur die Ereigniszeile als gelöscht markiert stehen (`is_deleted`, `deleted_at`); ihre Inhalte entfernt schon dieser Schritt: Er leert `data_json` und `search_text`, löscht die Anhänge samt verschlüsselter Dateien und schreibt in die Ereignis-Historie einen `DELETE`-Eintrag, der nur die Feldnamen trägt, keine Werte. Die älteren Fassungen des Ereignisses in der Historie redigiert derselbe Lauf im anschließenden Historien-Schritt, ohne `eventhistory_detail_months` abzuwarten; zurückgestellt und erst von einem späteren Lauf redigiert werden dabei Fassungen, deren Ereignis, Person oder Fall unter Legal Hold steht, deren Personen- oder Ereigniszeile gerade eine andere Transaktion gesperrt hält (der Schritt wartet nicht auf fremde Sperren, sondern überspringt sie) oder deren Ereignis währenddessen einer anderen Person zugeordnet wurde; ein späterer Lauf findet sie über denselben Zustand wieder (Ereignis gelöscht, Fassung noch nicht redigiert), und die Zahl der betroffenen Ereignisse meldet der Lauf in seiner Ausgabe („Deferred … event(s) with held/locked history“) und im Audit-Eintrag des Laufs als `deferred_history_events`. Ab der Redaktion läuft `eventhistory_chronicle_months` (Tabelle unten). Die Person bleibt davon unberührt, solange sie noch ein nicht gelöschtes Ereignis hat; anonymisiert wird sie erst danach (Absatz unter der Tabelle). Bei anonymen Ereignissen gibt es keine Person:

| Datenart | Einstellung | Standard | Wirkung |
|---|---|---|---|
| Anonyme Ereignisse | `retention_anonymous_days` | 90 Tage | Soft-Delete; Stichtag ist der Ereigniszeitpunkt |
| Ereignisse von Personen der Stufe „Nur Pseudonym" | `retention_identified_days` | 365 Tage | Soft-Delete; Stichtag ist der Ereigniszeitpunkt |
| Ereignisse „Mit Zusatzangaben“ | `retention_qualified_days` | 3650 Tage | Frist ab Fallabschluss; ohne Fall ab Ereignisdatum; offen ab letzter fachlicher Fallaktivität (Fallback Anlage). Erst Vorschlag und Freigabe, dann Löschung |
| Ereignisse eines Dokumentationstyps mit eigener Frist | „Höchstfrist (verkürzt nur)“ (`retention_days`) am Dokumentationstyp | leer (keine Typ-Frist) | Soft-Delete; Stichtag ist der Ereigniszeitpunkt. Gilt zusätzlich zur Stufen-Frist, es greift die zuerst ablaufende |
| Aktivitäten (Zeitstrom) | `retention_activities_days` | 365 Tage | harte Löschung |
| Übergabenotizen (Schichtnotiz) | `retention_handover_notes_days` | 90 Tage | harte Löschung |
| Gelöschte Personen und Fälle im Papierkorb | `client_trash_days` | 30 Tage | Anonymisierung |
| Werte alter Ereignis-Fassungen | `eventhistory_detail_months` | 24 Monate | Redaktion; `0` schaltet die altersabhängige Redaktion ab |
| Bereits redigierte Ereignis-Fassungen | `eventhistory_chronicle_months` | 24 Monate nach der Redaktion | harte Löschung; `0` = unbegrenzt |
| Audit-Log mit Einrichtung | `auditlog_retention_months` | 24 Monate | harte Löschung; aktiver Ziel-Hold schützt; `0` = aus (§ 7.4) |
| Audit ohne Einrichtung | `AUDITLOG_SYSTEM_RETENTION_MONTHS` (Umgebungsvariable) | 24 Monate installationsweit | harte Löschung nach eigener Audit-Frist; dauerhafte Ausnahmen bleiben, kein Ziel-Hold-Schutz ohne Einrichtung; `0` = aus; nur beim nicht auf eine Einrichtung eingeschränkten Lauf |
| Abgeschlossene Lösch-, Zusammenführungs- und Herabstufungsanträge | `deletion_request_retention_days` | 1095 Tage ab Abschluss | harte Löschung; offene Anträge bleiben, Audit folgt seiner Frist |

Personen, deren Ereignisse danach alle gelöscht sind, anonymisiert derselbe Lauf. Ohne Ereignis gilt die Inaktivitätsfrist `retention_identified_days` nur für „Nur Pseudonym“ ohne offene Fälle. Der Anker ist der jüngste Zeitpunkt aus Personenänderung (`updated_at`), fachlicher Fallaktivität (`last_activity_at`), Aufgabenänderung und Anhangsänderung an Person oder Fall (Anlage, Ersetzung oder Entfernung). Personen „Mit Zusatzangaben“ ohne Ereignis sind von der automatischen Anonymisierung ausgenommen; der Vorschlagsweg unterstützt bisher nur Ereignisse, für diese Personen fehlt eine automatische Fristvollstreckung. Personen „Nur Pseudonym“ ohne Ereignis mit offenem Fall sind vom Inaktivitätszweig ausgenommen. Die Einrichtung prüft ereignislose Personen „Mit Zusatzangaben“ regelmäßig und stellt bei Bedarf einen Löschantrag; das Prüfintervall hält sie im Löschkonzept fest. Maßgeblich ist [`anonymization.py`](../src/core/retention/anonymization.py). Hold und Art. 18 schützen beide Wege. Die übrigen Felder der Gruppe „Datenschutz und Fristen" sind keine Fristen: `retention_auto_approve_after_defer` und `retention_max_defer_count` steuern den Umgang mit zurückgestellten Löschvorschlägen im Retention-Dashboard, `retention_use_k_anonymization` und `k_anonymity_threshold` die K-Anonymisierung (beide unten).

> **Die Übergabenotiz wird hart gelöscht, nicht soft-gelöscht** (Refs #2042) — wie die Aktivitäten. Maßgeblich ist das Datum der letzten Änderung (`updated_at`), nicht das Anlegen; die Schlüsselrotation schreibt diesen Zeitstempel bewusst nicht fort und verlängert die Frist deshalb nicht. Es gibt **keinen** Legal Hold für Übergabenotizen: Holds sichern Personen-, Fall- und Ereignisakten für ein laufendes Verfahren, die Schichtnotiz ist eine Dienstnotiz ohne Personenbezug im Datenmodell (siehe [security-notes.md](security-notes.md)). Jeder Lauf mit Treffern schreibt eine Audit-Zeile (`DELETE`, Kategorie `handover_notes`) mit Anzahl und Frist — nie mit Text.

**Manueller Aufruf:**

```bash
# Testlauf (kein Löschen)
docker compose -f docker-compose.prod.yml run --rm maintenance \
  enforce_retention --dry-run

# Ausführen
docker compose -f docker-compose.prod.yml run --rm maintenance \
  enforce_retention

# Nur eine bestimmte Einrichtung
docker compose -f docker-compose.prod.yml run --rm maintenance \
  enforce_retention --facility "Beratungsstelle Nord"
```

> **Bereits automatisiert (Refs #794):** Der mitgelieferte `cron`-Sidecar aus `docker-compose.prod.yml` führt diesen Job ab Werk aus. Das folgende Crontab-Beispiel gilt nur, wenn der `cron`-Dienst nicht läuft; es startet den Job über den `maintenance`-Dienst derselben Compose-Datei, der als einziger die Admin-Rolle (BYPASSRLS) erhält (ohne Compose in der eigenen Orchestrierung nachbilden: gleiches Image, Entrypoint `python manage.py`, Admin-Rolle). Wer beides einrichtet, lässt den Job doppelt laufen — Details: [Ops-Runbook § 3](ops-runbook.md#3-cron-jobs).

**Als täglicher Cron einrichten (empfohlen; 03:00, nach dem Backup um 02:00):**

```cron
0 3 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance enforce_retention >> /var/log/anlaufstelle-retention.log 2>&1
```

Jede Ausführung, die Datensätze löscht, wird automatisch im Audit-Log protokolliert.

#### Retention-Dashboard

Unter [`/retention/`](https://github.com/anlaufstelle/app/blob/main/src/core/views/retention.py) steht ein **Retention-Dashboard** bereit, über das Leitung und Admin Löschvorschläge abarbeiten. Refs #514, #515, #2425.

> **Standardlauf:** Anonym und „Nur Pseudonym“ werden automatisch gelöscht.
> Für „Mit Zusatzangaben“ entstehen Vorschläge; nur freigegebene Vorschläge
> erlauben die Löschung. Auch die Typ-Höchstfrist umgeht die Freigabe nicht.
> `--propose` bleibt als zusätzlicher Vorschlagslauf für alle Strategien erhalten.

| Aktion im Dashboard | Wirkung |
|---|---|
| **Freigeben** | Erste berechtigte Person: „1 von 2 Freigaben“; zweite andere berechtigte Person: endgültig frei. Nächster Lauf löscht, sofern Hold/Art. 18 nicht schützen |
| **Aufschieben** | Schützt bis `deferred_until`; danach wieder offen. Auto-Freigabe nach maximalen Zurückstellungen bleibt Opt-in |
| **Ablehnen** | Löscht nichts; neue Vorlage frühestens nach `retention_rejected_retry_days` (Vorgabe 365 Tage) |

`retention_open_case_inactive_days` (Vorgabe 365 Tage) steuert den Hinweis für
ruhende offene Fälle. Fallaktivität sind fachliche Änderungen an Fall,
Ereignissen, Aufgaben und Verlauf; Umschlüsselung zählt nicht.

#### Legal Hold

Leitung und Anwendungsbetreuung setzen und lösen einen Hold direkt in der
Personen-, Fall- und Ereignisakte („Legal Hold verwalten“), mit Grund,
optionalem Ablaufdatum und Audit-Spur. Auf Personen/Fälle gesetzte Holds
schützen auch deren Ereignisse. Die Systemadministration kann Holds in
`/system/legal-holds/` aufheben. Eine Vorschlagskarte bleibt ein zusätzlicher
Bedienweg; ein gesonderter Vorschlags-Cron ist nicht nötig.

Aktive Holds schützen auch Audit-Zeilen zu gesperrten Zielen. Art. 18 schützt
im Aufbewahrungslauf Ereignisse, Personen-Anonymisierung und Historien-
Redaktion. Beide Schutzarten werden unabhängig geprüft; die manuelle
Vier-Augen-Löschung als Art.-17-Weg bleibt gesondert geregelt (§ 7.5).

Den Vorschlag legt der Lauf an. Die manuelle Freigabe braucht zwei verschiedene aktive Personen der eigenen Einrichtung, jeweils mit Rolle Leitung oder Anwendungsbetreuung und dem Recht „Löschbestätigung“. Die erste Freigabe lässt den Vorschlag ausstehend (PENDING, „1 von 2 Freigaben“); erst die zweite andere Person gibt ihn endgültig frei (APPROVED). Mit nur einem berechtigten Konto bleibt es bei „1 von 2“; die erste Freigabe allein erlaubt keine Löschung. Zurückstellen, Legal Hold (auch aus der Personen-, Fall- oder Ereignisakte) und Ablehnung verwerfen die erste Freigabe mit Audit; bei einem erneuten manuellen Freigabeweg sind wieder zwei Freigaben nötig. Die Einrichtung benennt mindestens zwei berechtigte Konten. Die Compliance-Karte „Genehmiger für den Aufbewahrungslauf“ prüft Rolle und Anzahl (0 = kritisch, 1 = Warnung, mindestens 2 = ok). Die Karte „Genehmiger für Löschanträge“ zählt weiterhin aktive Löschantragsberechtigte unabhängig von ihrer Rolle. Ausnahme: Die Einrichtung kann die Auto-Freigabe nach mehrfacher Zurückstellung ausdrücklich einschalten (Opt-in, standardmäßig aus); nach der konfigurierten maximalen Zahl von Zurückstellungen und Ablauf der Wiedervorlage genehmigt der Lauf ohne zweite Person. Es zählen nur Zurückstellungen durch Personen mit dem Recht „Löschbestätigung“, wenn kein Aufschub desselben Vorschlags mehr läuft. Die Schwelle beträgt mindestens 1. Zurückstellen bleibt für Leitung und Anwendungsbetreuung auch ohne dieses Recht möglich. Hold und Art. 18 schützen auch diesen Weg.

Vor Inbetriebnahme Fristen und Auto-Freigabe (Standard aus) prüfen. Die neue Antragsfrist ist unter Verwaltung → Einstellungen sichtbar; die installationsweite Audit-Frist wird über die Umgebung gesetzt.

Der tatsächliche Löschlauf führt für Ereignisse „Mit Zusatzangaben“ einen freigegebenen Vorschlag (APPROVED) nur mit zwei belegten Freigaben oder der nachweislichen Auto-Ausnahme aus. Bei unvollständigen Belegen überspringt er das Ereignis und meldet es; die Karte zeigt „Freigabebelege unvollständig – wird nicht gelöscht“. Ein Testlauf (`--dry-run`) zählt Kandidaten und ersetzt diese Prüfung beim Löschen nicht.

#### Aufbewahrung der Ereignis-Historie

Jede Speicherung eines Ereignisses schreibt eine **Historien-Fassung**: wer wann welche Felder mit welchen Werten geändert hat. Damit sie die Löschfrist des Ereignisses selbst nicht überleben: Ein Ereignis, dessen `retention_identified_days` abgelaufen sind, wird geleert, und seine älteren Fassungen dürfen nicht jeden jemals gespeicherten Wortlaut behalten. `enforce_retention` behandelt sie deshalb in zwei Stufen ([ADR-050](adr/050-eventhistory-retention.md)).

**Stufe 1 — Redaktion.** Die Fassung bleibt bestehen, ihre *Werte* fallen weg. Was stehen bleibt: Zeitpunkt, handelnde Person, Aktion und die Namen der betroffenen Felder. Was verschwindet: die Inhalte. In der Ereignis-Detailansicht und in der Auskunft nach Art. 15 erscheint an ihrer Stelle „– (redigiert)". Zwei Auslöser:

- **Das Ereignis ist gelöscht.** Sobald ein Ereignis als gelöscht markiert ist — durch die Frist oder von Hand —, verlieren beim nächsten Lauf **alle** seine Fassungen ihre Werte. Das braucht keine Einstellung und lässt sich nicht abschalten: es ist die Durchsetzung der Frist, die für das Ereignis ohnehin schon gilt. Es erfasst auch Ereignisse ohne Person (anonyme Kontakte), für die es bisher überhaupt keinen Löschpfad der Historie gab.
- **Die Fassung ist alt.** Bei noch **lebenden** Ereignissen werden Fassungen redigiert, die älter als `eventhistory_detail_months` sind.

**Stufe 2 — Löschung der Chronologie.** Eine bereits redigierte Fassung wird endgültig gelöscht, wenn ihre Redaktion länger als `eventhistory_chronicle_months` zurückliegt. Gemessen wird ab dem Zeitpunkt der Redaktion, nicht ab der ursprünglichen Änderung — die Frage lautet „wie lange steht diese Zeile schon leer". Zu diesem Zeitpunkt fallen keine Inhalte mehr, nur noch die Chronologie selbst.

| Einstellung in `Settings` | Standard | Wirkung |
|---|---|---|
| `eventhistory_detail_months` | 24 Monate | Wie lange die **Werte** alter Fassungen lebender Ereignisse lesbar bleiben. `0` = **deaktiviert** (die Werte bleiben unbefristet lesbar). Berührt den Zweig „gelöschtes Ereignis" nicht. |
| `eventhistory_chronicle_months` | 24 Monate | Wie lange eine bereits redigierte Fassung danach noch aufbewahrt wird, bevor sie gelöscht wird. `0` = **deaktiviert**, dann wächst die Chronologie unbegrenzt — das ist eine Entscheidung für unbefristete Aufbewahrung und gehört dann ausdrücklich ins Verarbeitungsverzeichnis und ins Löschkonzept. |

Beide Vorgabewerte entsprechen `auditlog_retention_months`: Historie und Audit-Log beantworten dieselbe Frage („wer hat wann etwas geändert") und sollen deshalb gemeinsam enden. Mit den Vorgabewerten ergibt sich für die Fassung eines lebenden Ereignisses eine Gesamtlebensdauer von 24 Monaten Werte plus 24 Monaten Chronologie; für die eines gelöschten 24 Monate Chronologie ab der Löschung.

**Was Sie beim Ändern wissen sollten:**

- **Die jüngste Fassung eines lebenden Ereignisses wird nie redigiert**, unabhängig von ihrem Alter. So bleibt auf der Detailseite immer mindestens eine lesbare letzte Änderung stehen — auch bei einer Akte, die seit Jahren unangetastet ist.
- **Ein Legal Hold hält die Redaktion auf.** Steht das Ereignis (oder seine Person) unter Hold, bleiben seine Fassungen unverändert; der Lauf weist sie als zurückgestellt aus und holt sie nach, sobald der Hold aufgehoben oder abgelaufen ist.
- **Redaktion ist endgültig.** Alte Wortlaute sind danach nur noch aus einem Backup-Stand rekonstruierbar. Wer einen Berichtigungsstreit über eine Jahre alte Formulierung führen können muss, setzt `eventhistory_detail_months` entsprechend höher.
- **Nebeneffekt auf die Schlüsselrotation:** Redigierte Fassungen tragen keinen verschlüsselten Inhalt mehr und werden bei `reencrypt_fields` übersprungen. Die Dauer einer Schlüsselrotation richtet sich damit nach dem *unredigierten Fenster* statt nach der Gesamtgröße der Tabelle — sie bleibt konstant, statt jährlich zu wachsen.
- **Jede Charge hinterlässt einen Nachweis** im Audit-Log (Aktion „Ereignis-Historie redigiert") mit Anzahl, Fristgrenze und Chargengrenzen. Dieser Nachweis ist von `auditlog_retention_months` **ausgenommen** und bleibt dauerhaft erhalten.

Bei vielen Fassungen lebender Ereignisse kann ein Lauf länger dauern als gewohnt.

**Prüfung: Fassungen mit Redaktionszeitpunkt, aber mit Werten** (#2188). Die Schlüsselrotation (`reencrypt_fields`) sperrt die Zeilen, die sie zurückschreibt, und überschreibt deshalb keine gerade redigierte Fassung mit einer neu verschlüsselten Kopie ihrer alten Werte (#2177). Eine Fassung, die als redigiert gilt, aber wieder Inhalte trägt, holt kein Aufbewahrungslauf nach, weil jeder Lauf nur noch nicht redigierte Fassungen anfasst. Stammt der Datenbestand aus einer Wiederherstellung oder einer Rotation ohne diese Sperre, prüfen Sie ihn mit `repair_eventhistory_redaction`:

- **Zuerst der Trockenlauf** (Aufruf ohne Schalter): Er zählt je Einrichtung die betroffenen Fassungen und die davon wegen Legal Hold zurückgestellten, ändert nichts und endet auch bei Funden mit Exit-Code 0. Meldet er überall 0, ist nichts weiter zu tun.
- **Dann die Heilung mit `--apply`:** Die betroffenen Fassungen bekommen unter Zeilensperre den Redaktionsvermerk, den der Aufbewahrungslauf damals geschrieben hat (bei einer anonymisierten Person den Anonymisierungsvermerk). Der Zeitpunkt der ursprünglichen Redaktion bleibt stehen, die Frist der Stufe 2 verschiebt sich also nicht. Jede geheilte Fassung hinterlässt einen eigenen Nachweis im Audit-Log (Aktion „Ereignis-Historie redigiert", Kategorie `eventhistory_repaired`). Fassungen unter Legal Hold bleiben unverändert; ein erneuter Aufruf nach dem Ende des Holds heilt sie. Ein zweiter Lauf findet 0.
- **Nur mit der Admin-DB-Rolle** (Wartungsdienst, wie `enforce_retention`): Unter der App-Rolle bricht das Command ab, statt einen sauberen Bestand zu melden. Die Aufrufe stehen im [Betriebshandbuch § 3.5](ops-runbook.md#35-manuelle-ausfuehrung).

**Dieselbe Prüfung für Ereignisse, Personen und Fälle** (#2188). Die Schlüsselrotation schreibt auch die Zusatzfelder von Ereignissen, Personen und Fällen zurück. Läuft sie gleichzeitig mit einer Löschung oder Anonymisierung, bekommt ein gelöschtes Ereignis, eine anonymisierte Person oder ein anonymisierter Fall seine alten Werte nicht zurück, weil die Rotation die Zeile sperrt. Trägt ein Datensatz trotzdem wieder alte Werte (etwa in einem wiederhergestellten Bestand), gilt er weiter als gelöscht bzw. anonymisiert — und weil jeder Lauf nur noch nicht erledigte Datensätze anfasst, holt ihn keiner nach. Mit `repair_eventhistory_redaction --target all` prüft dasselbe Command diese drei Bestände und die Berichtigungsanträge anonymisierter Personen (siehe unten) zusätzlich zur Historie, in der Reihenfolge Person, Berichtigungsantrag, Fall, Ereignis, Historie; einzeln mit `--target client`, `rectification`, `case`, `event` oder `eventhistory`. Ohne `--target` prüft es nur die Historie.

- **Trockenlauf und `--apply` wie oben.** Die Ausgabe ist englisch und nennt je Bestand und Einrichtung `found`, `repaired`, `deferred (legal hold)` und `skipped`.
- **Die Heilung stellt genau den Zustand her, den Löschung bzw. Anonymisierung hinterlassen:** leere Zusatzfelder, beim Ereignis zusätzlich ein leerer Suchindex. Lösch- bzw. Anonymisierungszeitpunkt bleiben stehen. Geprüft wird auch das noch nicht gelöschte Ereignis einer anonymisierten Person, denn die Anonymisierung leert dessen Inhalt ebenfalls.
- **Berichtigungsanträge anonymisierter Personen** (`--target rectification`): Die Anonymisierung leert bei jedem Berichtigungsantrag der Person den alten Wert, den Wunschwert, die Begründung und den Prüfvermerk; eine Schlüsselrotation ohne Zeilensperre könnte diese Inhalte ebenso zurückschreiben. Geprüft werden Anträge, deren Person anonymisiert ist und die noch einen dieser Inhalte tragen; die Heilung leert sie wieder. Status, beantragende und prüfende Person sowie Antrags- und Prüfzeitpunkt bleiben stehen, wie die Anonymisierung sie lässt.
- **Legal Hold wie beim jeweiligen Löschweg:** Ein Ereignis bleibt bei einem Hold auf das Ereignis, seine Person oder seinen Fall unverändert; eine Person bei einem Hold auf sie, eines ihrer Ereignisse oder einen ihrer Fälle; ein Fall bei einem Hold auf ihn, eines seiner Ereignisse oder seine Person; ein Berichtigungsantrag bei einem Hold auf seine Person, eines ihrer Ereignisse oder einen ihrer Fälle.
- **Jede geheilte Zeile hinterlässt einen eigenen Nachweis** im Audit-Log (Aktion „Löschung“, Kategorie `event_repaired`, `client_repaired`, `case_repaired` bzw. `rectification_repaired`). Heilt ein Lauf in einer Einrichtung viele Zeilen auf einmal, kann die Erkennung von Massenlöschungen (`BREACH_DELETE_THRESHOLD`) anschlagen — das ist dann dieser Lauf.
- **Die Verbindung muss jede geprüfte Tabelle besitzen** (Admin-DB-Rolle). Ein RLS-Bypass allein genügt nicht; ohne Eigentum bricht das Command ab, bevor es eine Zeile liest. Für Berichtigungsanträge gehört dazu auch die Personentabelle, deren Anonymisierungsstand die Auswahl bestimmt.

#### K-Anonymisierung statt Hard-Delete

Als Alternative zur Hart-Löschung kann pro Einrichtung die **K-Anonymisierung** aktiviert werden (Refs #535).

| Feld in `Settings` | Default | Beschreibung |
|---|---|---|
| `retention_use_k_anonymization` | `False` | Aktiviert den Ersatz von Hard-Delete durch K-Anonymisierung. |
| `k_anonymity_threshold` | `5` | Mindest-Gruppengröße pro Bucket. Höhere Werte → stärkere Anonymisierung, weniger Detail. Das Formular nimmt Werte ab 2 an. Ein bereits gespeicherter kleinerer Wert rechnet weiter (`1` bleibt wirksam, `0` rechnet mit 5) und wird protokolliert. Für jeden wirksamen Wert unter 3 zeigen die Einstellungsseite, der externe Bericht und die Berichtsvorschau einen Risikohinweis; bei `1` nennt er, dass keine Zahl unterdrückt wird. |

Wirkung: Statt den Datensatz zu löschen, werden identifizierende Merkmale aggregiert bzw. pseudonymisiert, sodass statistische Auswertungen über den historischen Zeitraum **erhalten bleiben**, ohne dass Einzelpersonen re-identifiziert werden können. DSGVO-konforme Alternative zur endgültigen Löschung, wenn weiter ausgewertet werden soll.

### 7.4 Audit-Log

Das Audit-Log ist **append-only** und unveränderlich. Es protokolliert automatisch unter anderem die folgenden Aktionen. Die Tabelle ist eine Auswahl; die erste Spalte nennt die Beschriftung, unter der die Aktion im Filter **Aktion** des Audit-Logs steht, die vollständige Liste zeigt dieser Filter.

| Aktion (Filter) | Auslöser |
|---|---|
| Anmeldung, Abmeldung | Jeder Login/Logout |
| Anmeldung fehlgeschlagen | Falsches Passwort |
| Daten der Stufe „Mit Zusatzangaben" eingesehen | Aufruf der **Personen-Detailseite** (`/clients/<id>/`) einer Person dieser Stufe — nicht der Aufruf der Personenliste, eines Ereignisses, eines Falls oder eines Suchtreffers |
| Export | Datenexport durch Benutzer — auch Datenauskünfte zu einzelnen Personen und der Aufruf des externen Berichts am Bildschirm. **Herausgegebene Berichtsdateien** (CSV/PDF/JSON/XML mit Berichtszeitraum) sind eine Teilmenge davon: nur sie unterliegen nicht der Aufbewahrungsfrist und erscheinen in der [Freigabe-Übersicht](#8-statistik-snapshots--materialized-view) |
| Löschung | Manuell oder durch `enforce_retention` |
| Stufenwechsel | Änderung des Kontaktstatus einer Person |
| Einstellungen geändert | Änderungen an Einrichtungseinstellungen |
| Download | Herunterladen eines Anhangs aus dem Dateitresor |
| Ereignis aktualisiert | Inhaltliche Änderung eines Ereignisses; im Detail stehen nur Dokumentationstyp und geänderte Feldbezeichner |
| Episode aktualisiert | Änderung von Titel, Beschreibung oder Zeitraum einer Episode |
| Wirkungsziel aktualisiert | Änderung von Titel oder Beschreibung eines Wirkungsziels |
| Ereignis dem Fall zugeordnet | Neue oder geänderte Zuordnung eines Ereignisses zu einem Fall |
| Ereignis vom Fall gelöst | Entfernen der Zuordnung eines Ereignisses zu einem Fall |
| Anhang hochgeladen | Erfolgreiches Hinzufügen oder Ersetzen einer Datei an Person, Fall oder Ereignis |
| Anhang gelöscht | Erfolgreiches Löschen einer Anhang-Versionskette; beim Dokumentationstyp-Wechsel als eine Sammelzeile |

Die sieben Bearbeitungs-, Zuordnungs- und Anhangaktionen speichern **keine
Feldinhalte, Freitexte oder Klartext-Dateinamen**. Änderungszeilen nennen nur die
betroffenen Feldbezeichner; Anhang-Uploads verwenden einen HMAC-Fingerprint plus
sichere Endung und technische Metadaten. Sie gehören zur regulären Facharbeit und
sind deshalb nicht von der Aufbewahrungsfrist ausgenommen — für sie gelten die
unten beschriebenen standardmäßig 24 Monate.

> **Was das Audit-Log bewusst _nicht_ protokolliert:** gewöhnliche Listen- und Detailaufrufe (Personenliste, Ereignis, Fall, Aufgabe) und **jede Suchanfrage** — weder den Suchbegriff noch einen Zähler oder Zeitpunkt. Das ist eine bewusste Abwägung: Der Suchbegriff ist selbst personenbezogen und würde im append-only Log jede Anonymisierung überleben. Begründung, verworfene Alternativen (Hash, Zähler) und das dabei benannte Restrisiko stehen in [ADR-043](adr/043-suchraum-volltextsuche.md); das Verarbeitungsverzeichnis unter `/system/vvt/` sagt dasselbe an seinen TOM-Einträgen.

**Im Admin einsehen:**

Unter **Datenschutz → Audit-Log** (bzw. `/audit/`) können Logs nach Aktion, Benutzer und Zeitraum gefiltert werden; einen Einrichtungsfilter gibt es nicht. Zugriff hat die Anwendungsbetreuung (`facility_admin`) für die eigene Einrichtung. Pre-Auth-AuditLogs ohne Facility-Bezug (z. B. `LOGIN_FAILED` bei unbekanntem Username) sind ausschließlich für die Systemadministration (`super_admin`) im `/system/`-Bereich sichtbar — siehe [ADR-007 Update 2026-05-10](adr/007-auditlog-append-only.md).

> ⚠️ **Betreiberhinweis für eigene Auswertungen: Anhänge tragen zwei `target_type`-Strings** (Refs #1738, [ADR-039](adr/039-owner-generische-dateianhaenge.md)). Das Modell heißt `Attachment` (zuvor `EventAttachment`). Das Audit-Log ist append-only und über eine HMAC-Kette gesichert — **ältere Zeilen werden deshalb nie umgeschrieben**. Wer Anhang-Aktionen (Downloads, abgewiesene Uploads, Löschungen) auswertet, muss beide Strings abfragen:
>
> ```sql
> SELECT * FROM core_auditlog WHERE target_type IN ('Attachment', 'EventAttachment');
> ```
>
> Dasselbe gilt für das `detail`-Feld: Zeilen tragen `owner_type` + `owner_id` (Werte `event`/`client`/`case`), ältere Zeilen nur `event_id`. Download-Einträge zu **Ereignis**-Anhängen führen `event_id` zusätzlich weiter, damit bestehende Auswertungen nicht brechen. Ein Filter allein auf `Attachment` oder allein auf `detail->>'event_id'` liefert eine **stillschweigend unvollständige** Auswertung.

**Aufbewahrung des Audit-Logs:** Das Audit-Log hat eigene automatische Löschfristen, unabhängig von den Fach-Fristen. `prune_auditlog` berechnet das Alter jedes Eintrags ab `timestamp` und löscht abgelaufene Einträge, soweit keine der unten genannten dauerhaften Ausnahmen oder ein aktiver Ziel-Hold greift. Je Einrichtung gilt `auditlog_retention_months` (Vorgabe 24 Monate); Einträge ohne Einrichtung folgen separat `AUDITLOG_SYSTEM_RETENTION_MONTHS` (ebenfalls Vorgabe 24 Monate). Der nicht auf eine Einrichtung eingeschränkte `enforce_retention`-Lauf bereinigt auch diese Systemkette. Beide Fristen rechnen kalendergenau; `0` schaltet jeweils die automatische Audit-Löschung aus.

Die Fach-Fristen beginnen je Datenart an ihrem eigenen Anker (Tabelle in [§ 7.3](#73-löschfristen-und-enforce_retention)). Für Ereignisse der Stufe `qualified` beträgt die Stufen-Frist standardmäßig 3650 Tage: bei geschlossenem Fall ab `closed_at` (falls dieses fehlt, ab `last_activity_at`, ersatzweise `created_at`), ohne Fall ab `occurred_at`, bei offenem Fall ab `last_activity_at` (ersatzweise `created_at`). Auch offene und falllose Ereignisse können deshalb ohne Typ-Frist fällig werden. Der Standardlauf erzeugt für fällige Ereignisse „Mit Zusatzangaben“ einen Löschvorschlag und löscht erst nach Freigabe; Hold und Art. 18 werden weiterhin geprüft. Eine positive Typ-Höchstfrist (`DocumentType.retention_days`) zählt ab Ereignisdatum und kann die Stufen-Frist nur verkürzen; sie umgeht die Freigabe nicht.

Ein aktiver Legal Hold schützt auch Audit-Einträge **mit Einrichtung**, deren `target_type` und `target_id` eine geschützte Person (`Client`), einen geschützten Fall (`Case`) oder ein geschütztes Ereignis (`Event`) bezeichnen. Dazu zählen direkt gehaltene Ziele und die durch deren Personen-/Fall-/Ereignis-Beziehungen mitgeschützten Ziele gemäß [`audit_pruning.py`](../src/core/retention/audit_pruning.py). Der Hold schützt diese Zielmenge; andere Zieltypen, bloße Erwähnungen im Detail und Einträge ohne Einrichtung erhalten dadurch keinen Schutz vor der Audit-Frist. Nach Aufhebung oder Ablauf des Holds können die Einträge bei einem späteren Lauf gelöscht werden, wenn ihre Audit-Frist abgelaufen ist.

Audit-Spuren können daher vor den zugehörigen Fachdaten verschwinden. Auch eine Audit-Frist so lang wie die längste Fach-Frist deckt nicht zwingend die gesamte Lebensdauer der Fachdaten ab: Ein Ereignis wird in Jahr 0 erfasst, sein Fall in Jahr 5 abgeschlossen. Ohne frühere Typ-Frist und ohne Schutz oder Verzögerung wird es nach der zehnjährigen Stufen-Frist und Freigabe etwa in Jahr 15 gelöscht; eine zehnjährige Audit-Frist kann die nicht geschützte Audit-Spur der Erfassung bereits in Jahr 10 löschen. Lückenlose Audit-Aufbewahrung ohne Routinebereinigung verlangt `0` für die jeweilige Audit-Kette; ein Hold sichert nur seine Zielmenge. Andere Werte sind gegen die benötigte Nachweisdauer abzuwägen.

Die Einstellungsseite vergleicht die Audit-Frist näherungsweise als Monate × 30 Tage mit den Fach-Fristen (Stufen, Aktivitäten, Übergabenotizen, Papierkorb, abgeschlossene Anträge, Historie) und den Typ-Fristen aller Ereignis-Dokumentationstypen der Einrichtung, auch inaktiver. Ist sie kürzer als die längste dieser Fristen, erscheint ein Hinweis mit beiden Werten und der längsten Frist, bei einer Typ-Frist mit dem Namen des Typs. Als unbegrenzt geltende `0`-Fach-Fristen werden dabei berücksichtigt; die Stufen-Fristen müssen mindestens einen Tag betragen. Mit Audit-Frist `0` entfällt der Hinweis. Dieser Vergleich ist keine Zusage vollständiger Nachweise über die Lebensdauer der Fachdaten ([ADR-021, Nachtrag 2026-10-10](adr/021-retention-modell.md#nachtrag-2026-10-10-freigabe-und-geschlossene-fristlücken)).

| Einstellung in `Settings` | Standard | Wirkung |
|---|---|---|
| `auditlog_retention_months` | 24 Monate | Bei jedem `enforce_retention`-Lauf werden abgelaufene Einträge der Einrichtung gelöscht, sofern keine dauerhafte Ausnahme oder ein aktiver Ziel-Hold schützt (kalendergenaue Monatsrechnung, keine 30-Tage-Näherung). `0` = **deaktiviert**, dann wächst das Log unbegrenzt. |

Von dieser Frist **ausgenommen** sind fünf Klassen; sie bleiben unabhängig vom Alter erhalten (`PRUNE_EXEMPT_Q` in [`src/core/retention/audit_pruning.py`](https://github.com/anlaufstelle/app/blob/main/src/core/retention/audit_pruning.py)). Vier davon hängen an der Aktion (`PRUNE_EXEMPT_ACTIONS`):

- `SECURITY_VIOLATION` — Forensik-Nachweise der Breach-Detection,
- `RETENTION_RUN_COMPLETED` — der Marker, aus dem das Compliance-Dashboard den letzten erfolgreichen Retention-Lauf liest,
- `AUDIT_PRUNE_CHECKPOINT` — der Tombstone des Prunings selbst,
- `EVENT_HISTORY_REDACTED` — der Nachweis je Charge des Historien-Sweeps (siehe [§ 7.3 „Aufbewahrung der Ereignis-Historie"](#aufbewahrung-der-ereignis-historie)). Er belegt, dass die Unveränderlichkeit für genau diese Zeilen legitim geöffnet wurde; verschwände er vor den Zeilen, die er belegt, bliebe eine stille Änderung append-only-geschützter Daten zurück.

Hinzu kommt — **unabhängig von der Aktion** — die fünfte Klasse: Einträge über eine **herausgegebene Berichtsdatei mit Berichtszeitraum** (CSV/PDF/JSON/XML). Ohne diese Ausnahme täuschte die Freigabe-Übersicht Vollständigkeit vor. Sie enthalten Zeitraum, Format, die auslösende Person und die erfassten Empfänger-/Zweckangaben (heuristisch geprüfter Freitext) und werden dauerhaft aufbewahrt. Reine Bildschirm-Ansichten des externen Berichts und Auskünfte ohne Berichtszeitraum laufen zwar unter derselben Audit-Aktion, bleiben aber der Frist unterworfen — die Abgrenzung steht in [§ 8 „Herausgabe von Berichten"](#herausgabe-von-berichten-was-sie-wissen-müssen).

**Checkpoint-Mechanik:** Das Audit-Log ist über eine HMAC-Kette verkettet (jeder Eintrag verweist auf den vorherigen). Ein Pruning reißt diese Kette zwangsläufig auf. Damit die Integritätsprüfung diese Lücke nicht als Manipulation meldet, schreibt jeder Prune-Lauf einen `AUDIT_PRUNE_CHECKPOINT`-Eintrag, der die Kettengrenzen der gelöschten Einträge festhält und die Diskontinuität legitimiert. Die Kette selbst wird dabei **nie** umgeschrieben — das Log bleibt append-only.

> **Wichtig:** Die Frist wirkt nur, wenn `enforce_retention` tatsächlich läuft (siehe [§ 7.3](#73-löschfristen-und-enforce_retention)). Gelöschte Einträge sind endgültig weg — wer eine längere Nachweisführung braucht, legt eine **externe** Archivierungsstrategie fest (z. B. nach Empfehlung des BSI oder Ihrer Datenschutz-Folgenabschätzung) und exportiert das Log, bevor die Frist greift.
>
> **Datenschutz-Restrisiko:** Weil das Log append-only ist, wird es von der Anonymisierung einer Person **nicht** mitredigiert — Details und Gegenmaßnahmen in [`security-notes.md` § Audit-Log-Restdaten überleben die Art.-17-Löschung](security-notes.md#audit-log-restdaten-überleben-die-art-17-löschung-issue-1753).

### 7.5 Löschanträge (4-Augen-Prinzip)

Abgeschlossene Anträge bleiben für die konfigurierte Antragsfrist (Vorgabe 1095 Tage) sichtbar. Bei vorhandenen Ereignissen wird auch „Mit Zusatzangaben“ → „Nur Pseudonym“ über Antrag und zweite Person mit „Löschbestätigung“ freigegeben. Personenakte und Papierkorb zählen noch offene Historienfassungen nach Anonymisierung; Hold, Art. 18 oder gleichzeitige Bearbeitung können die reguläre Redaktion im Nachtlauf zurückstellen.

Löschanträge für Personendaten werden im Vier-Augen-Prinzip bearbeitet: Ein Antrag muss von einer **zweiten berechtigten Person** genehmigt werden, bevor Daten endgültig gelöscht werden. Dies schützt vor versehentlicher oder unberechtigter Löschung — auch durch privilegierte Rollen selbst.

**Genehmiger-Pool über das Recht „Löschbestätigung"** (Refs #1053): Wer genehmigen darf, wird nicht aus der Rolle abgeleitet, sondern über das einzeln vergebbare Benutzer-Recht *Löschbestätigung* (`can_confirm_deletion`) kuratiert. Das erste Konto aus `setup_facility` trägt das Recht. Beim Anlegen eines Zugangs unter **Verwaltung → Zugänge** und im Django-Admin steht „Löschbestätigung“ auf „Nach Rolle“: Leitung und Anwendungsbetreuung erhalten das Recht, andere Rollen nicht; „Ja“ und „Nein“ übersteuern die Vorbelegung. Sorgen Sie für **mindestens zwei aktive Personen** mit dem Recht, damit für jeden Antrag eine zweite Person bereitsteht; unter **Verwaltung → Zugänge** lässt es sich auch gezielt entziehen oder etwa an eine erfahrene Fachkraft vergeben. Das Compliance-Dashboard (`/system/compliance/`) zeigt je Einrichtung die Zahl der aktiven Genehmiger in der Karte „Genehmiger für Löschanträge“: keine = kritisch, genau eine = Warnung (deren eigene Anträge bleiben offen), ab zwei = in Ordnung. Eigene Anträge kann auch ein:e Rechteträger:in nie selbst genehmigen (View-, Service- und Datenbank-Ebene erzwingen Antragsteller ≠ Genehmiger).

Das unveränderliche Audit-Log hält das Recht von der Anlage an fest: Der Anlage-Eintrag (`USER_CREATED`) nennt den Anfangswert (`can_confirm_deletion` ja/nein) — bei Anlage über Verwaltung → Zugänge oder den Django-Admin mit der handelnden Person, bei der Ersteinrichtung mit `setup_facility` als Systemeintrag. Jeder spätere Wechsel (Vergabe oder Entzug) steht als eigener Eintrag `DELETION_CONFIRMER_CHANGED` mit altem und neuem Wert darin, jede Genehmigung ebenfalls. Existiert für einen offenen Antrag außer der antragstellenden Person keine aktive Person mit dem Recht, zeigt die Löschantragsliste eine deutliche Warnung („Kein möglicher Genehmiger") — vergeben Sie das Recht dann an eine weitere Person.

#### Wer den Reiter „Löschanträge" sieht (Refs #1736)

Löschanträge liegen als Reiter **„Löschanträge"** im Bereich **„Datenschutz"** (`/datenschutz/`). In der Sidebar steht nicht der einzelne Reiter, sondern der Bereichs-Eintrag „Datenschutz" — er erscheint, sobald mindestens einer seiner Reiter sichtbar ist. Die Sichtbarkeit des Reiters folgt nicht mehr der Rolle allein, sondern dem tatsächlichen Bedarf:

| Wer | Sieht den Reiter | Sieht in der Liste |
|---|---|---|
| Leitung, Anwendungsbetreuung | ja | **alle** Anträge der Einrichtung |
| Träger:in des Rechts *Löschbestätigung* (ohne Leitungsrolle) | ja | **alle** Anträge der Einrichtung |
| Antragstellende Person mit mindestens einem **offenen** eigenen Antrag | ja, solange der Antrag offen ist | **nur die eigenen** Anträge |
| Alle übrigen | nein | — |

Der letzte Fall ist der **Rückkanal**: Wer einen Antrag stellt, findet ihn danach über die Navigation und über einen Hinweisbanner am betroffenen Datensatz wieder und sieht seinen Status — ohne dadurch Zugriff auf fremde Begründungen und Ziel-Pseudonyme zu bekommen. Ist der Antrag entschieden, verschwindet der Reiter wieder — und mit ihm der Bereichs-Eintrag „Datenschutz", sofern kein anderer Reiter des Bereichs sichtbar bleibt.

Am Datensatz selbst ist ein laufender Antrag jetzt ebenfalls sichtbar (Kennzeichnung in der Personenliste, Banner mit Datum und antragstellender Person im Personen-Detail); die Aktion „Löschen beantragen" ist währenddessen deaktiviert, ein zweiter Antrag ist nicht möglich.

#### Papierkorb: „Jetzt endgültig anonymisieren" (Refs #1737)

Eine genehmigte Löschung legt die Person in den **Papierkorb** (`/clients/trash/`, Anwendungsbetreuung). Dort bleibt sie wiederherstellbar, bis die Papierkorb-Frist (`client_trash_days`, Standard 30 Tage) abläuft und `enforce_retention` sie automatisch anonymisiert.

Neben „Wiederherstellen" steht dort die Aktion **„Jetzt endgültig anonymisieren"**. Sie zieht die Anonymisierung vor, statt auf die Frist zu warten:

| Aspekt | Verhalten |
|---|---|
| **Wirkung** | **Unwiderruflich.** Pseudonym, Notizen, Zusatzfelder sowie Fall- und Ereignisinhalte werden entfernt; es bleibt ein anonymes Statistik-Aggregat. Eine Wiederherstellung ist danach **nicht** mehr möglich. Ein Bestätigungsdialog nennt das ausdrücklich. |
| **Was sofort passiert — und was mit dem nächtlichen Lauf** (Refs #2085) | **Sofort:** Pseudonym, Notizen, Zusatzfelder, Fall- und Episodeninhalte, die Inhalte der Ereignisse selbst, die Zeitstempel-Generalisierung und die Dateianhänge. Die Person trägt danach kein Personendatum mehr. **Mit dem nächsten Aufbewahrungslauf** (`enforce_retention`, in der Voreinstellung nachts um 3 Uhr): die **älteren Fassungen** der Ereignis-Dokumentation — also die Versionshistorie, die jede Speicherung eines Ereignisses anlegt. Grund ist die Rollentrennung der Datenbank: diese Fassungen sind schreibgeschützt, und der Webprozess hat die dafür nötigen Rechte bewusst **nicht** (siehe `ops-runbook.md` § 9.5). Erfolgsmeldung und Bestätigungsdialog sagen das ausdrücklich. Muss eine Tilgung sofort vollständig abgeschlossen sein, lässt die Systembetreuung den Aufbewahrungslauf einmal von Hand laufen. Ein **Legal Hold**, der in diesem Fenster eintrifft, stellt die Fassungen zurück — sie bleiben dann vollständig erhalten. |
| **Wozu** | (a) **Art.-17-Sofortlöschung**, wenn eine betroffene Person auf unverzüglicher Löschung besteht und 30 Tage Papierkorb nicht vertretbar sind. (b) **Auflösen von Konfigurations-Blockaden**: eine Person im Papierkorb behält ihren Dokumentationstyp und ihre Feldwerte und blockiert damit das Löschen dieses Typs bzw. der Feldvorlage. Bisher blieb nur Wiederherstellen (kurzzeitige Daten-Auferstehung) oder Warten. Die Blocker-Meldung der Konfigurations-Oberfläche weist den Papierkorb-Anteil jetzt aus und verlinkt dorthin. |
| **Grenze: Aufbewahrungssperre** | Besteht ein **aktiver Legal Hold** auf der Person oder auf einem ihrer Ereignisse, wird die Aktion mit einer Fehlermeldung abgewiesen — genau wie der nächtliche Retention-Lauf (Refs #1066, #1756). Eine manuelle Aktion darf eine Aufbewahrungssperre nicht aushebeln. Erst Hold aufheben, dann anonymisieren. |
| **Grenze: nur im Papierkorb** | Die Aktion wirkt ausschließlich auf bereits soft-gelöschte Personen. Der Vier-Augen-Antragsweg bleibt der einzige Weg **in** den Papierkorb — es gibt keinen Direktpfad an der Genehmigung vorbei. |
| **Spur** | Audit-Eintrag `CLIENT_ANONYMIZED` mit `trigger="manual"`, unterscheidbar vom automatischen Lauf. |

Der Datensatz bleibt danach soft-gelöscht (anonymisiert, nicht wiederhergestellt) und verschwindet aus dem Papierkorb-Bestand nicht — er trägt nur keine Personendaten mehr. **„Wiederherstellen“ steht für diese Zeile danach nicht mehr zur Verfügung** — die Zusage des Bestätigungsdialogs gilt wörtlich: Der Knopf verschwindet, und ein Direktaufruf der Wiederherstellungs-URL wird mit einer Meldung abgewiesen (Refs #2085). Damit verhält sich die Personen-Aktion wie die Fall-Aktion, die diesen Schutz ([ADR-044](adr/044-fall-loeschpfad.md)) ebenfalls hat. Die Erfolgsmeldung lautet deshalb „Person wurde anonymisiert. Frühere Fassungen ihrer Ereignis-Dokumentation tilgt regulär der nächtliche Aufbewahrungslauf; was eine Aufbewahrungssperre oder eine gleichzeitige Bearbeitung zurückhält, holt ein späterer Lauf nach.“ — sie behauptet bewusst nicht, dass in diesem Moment bereits alles fort ist, und sagt auch keine feste Frist zu: Der Lauf stellt Fassungen unter einem aktiven Legal Hold zurück und überspringt Datensätze, die gerade eine andere Bearbeitung sperrt (Refs #2425). Personen im Papierkorb lassen sich auch nicht per Direkt-URL bearbeiten (#1737) (`/clients/<id>/edit/` antwortet 404, wie die Detailansicht).

#### Fall-Papierkorb und die beiden Löschwege (Refs #1818)

Seit [ADR-044](adr/044-fall-loeschpfad.md) lässt sich ein **einzelner Fall** löschen — bis dahin kannte Art. 17 nur zwei Größen (ein Ereignis oder die ganze Person). Der Weg ist derselbe wie bei Personen, mit vier Unterschieden, die Sie kennen sollten:

| Frage | Antwort |
|---|---|
| **Wer löscht?** | Fachkräfte und aufwärts, aus der Fallakte heraus (Menü „Weitere Aktionen" → **Löschen**). |
| **Vier Augen oder direkt?** | **Gestaffelt nach der Kontaktstufe der Person** — steht sie auf „Mit Zusatzangaben", entsteht ein Löschantrag, der von einer zweiten Person mit dem Recht *Löschbestätigung* bestätigt werden muss; sonst wandert der Fall direkt in den Papierkorb. Dieselbe Staffelung gilt schon beim Löschen eines **Ereignisses** derselben Person. Bestätigt wird über dieselbe Liste **Datenschutz → Löschanträge** wie bei Personen und Ereignissen. |
| **Welche Frist?** | **Keine eigene.** Es gilt `client_trash_days` (§ 2.3) — bewusst kein zweiter Fristbegriff, der in Datenschutzerklärung und Folgenabschätzung erklärt und gepflegt werden müsste. |
| **Was passiert nach Fristablauf?** | Der Fall wird **redigiert, nicht hart gelöscht**: Titel, Beschreibung, Zusatzfelder, Episoden, Wirkungsziele, Meilensteine und die fall-eigenen Dateien (samt verschlüsselter Ablage) sind weg; die Fall-Zeile bleibt als anonymes Aggregat stehen. Ein Hard-Delete risse Episoden, Ziele und Meilensteine per Kaskade mit. |

Den **Papierkorb** (`/cases/trash/`) öffnet die Anwendungsbetreuung über die Kopfzeile der Fallliste; er steht wie der Personen-Papierkorb in keiner Navigation. Dort stehen je Zeile **„Wiederherstellen"** (holt Episoden, Ziele und Anhänge vollständig zurück) und **„Jetzt endgültig anonymisieren"** — dieselbe Vorzieh-Aktion wie bei Personen, mit denselben Grenzen: nur aus dem Papierkorb heraus, nicht zweimal, und **nicht** bei einer aktiven Aufbewahrungssperre (Legal Hold auf dem Fall, auf einem seiner Ereignisse oder auf der Person). Ein bereits endgültig anonymisierter Fall ist als solcher ausgewiesen und bietet kein „Wiederherstellen" mehr an — es holte nur eine leere Hülle zurück.

Drei Punkte, die im Betrieb regelmäßig gefragt werden:

- **Zugeordnete Kontakte bleiben bei der Person.** Sie blockieren die Löschung nicht und werden nicht mitgelöscht; ihre Fall-Zuordnung fällt erst bei der endgültigen Tilgung (bis dahin wäre ein Wiederherstellen verlustbehaftet).
- **Auch offene Fälle sind löschbar.** Ein irrtümlich angelegter Fall ist immer offen; ihn erst „abzuschließen" wäre eine falsche Aussage über einen Hilfeverlauf, den es nie gab.
- **Ein Fall im Papierkorb blockiert Konfigurationsänderungen** genau wie eine Person im Papierkorb: Er hält seinen Dokumentationstyp und seine Feldwerte fest. Die Lösch-Dialoge unter *Konfiguration* weisen diesen Anteil aus und verlinken den Fall-Papierkorb.

Spur im Protokoll: `CASE_SOFT_DELETED`, `CASE_RESTORED`, `CASE_ANONYMIZED` (mit `trigger="manual"` bzw. `"trash_days_expired"`), dazu die Antragsschritte `DELETION_REQUESTED`/`DELETION_APPROVED`/`DELETION_REJECTED`. Der **Falltitel steht in keinem Protokolleintrag** — das Log überlebt jede spätere Anonymisierung. Der nächtliche Aufbewahrungslauf meldet die zusätzliche Zeile `Anonymized N trash-expired case(s)`.

#### Personen-Zusammenführung: Anträge und Papierkorb-Tombstones (Refs #1876)

Seit [ADR-045](adr/045-personen-merge.md) lassen sich doppelt angelegte Personen **zusammenführen**. Für den Betrieb relevant:

- **Dieselbe Antragsliste, eigene Antragsart.** Ein Zusammenführungs-Antrag läuft über **Datenschutz → Löschanträge**, gestaffelt wie die Löschung: Trägt eine der beiden Personen „Mit Zusatzangaben", entscheidet eine zweite Person mit dem Recht *Löschbestätigung*; sind beide auf „Nur Pseudonym", darf die Leitung direkt zusammenführen. Die Liste weist Merge-Anträge als **Zusammenführung → Ziel** aus; die Prüfseite zeigt beide Akten mit Bestandszahlen. Pro Person läuft immer nur **ein** destruktiver Antrag — Löschung *oder* Zusammenführung.
- **Der Papierkorb kennt Tombstones.** Die aufgelöste Quelle liegt mit dem Vermerk „zusammengeführt in <Ziel>" unter `/clients/trash/`. Statt „Wiederherstellen" (das der Service abweist — ein bloßes Restore ließe die Akte ohne ihre gewanderten Daten auferstehen) steht dort **Zusammenführung aufheben**: Die gewanderten Ereignisse, Fälle, Aufgaben und übernommenen Anhänge kehren zurück, seit der Zusammenführung neu erfasste Einträge bleiben beim Ziel. Nach Ablauf von `client_trash_days` redigiert der normale Aufbewahrungslauf den Tombstone wie jede Papierkorb-Person; der Verweis auf das Ziel bleibt als Verbleibs-Nachweis stehen, aufheben lässt sich danach nichts mehr.
- **Grenze: Aufbewahrungssperre.** Besteht ein **aktiver Legal Hold** auf der Zielperson oder auf der Quelle — auch mittelbar über eines ihrer Ereignisse oder einen ihrer Fälle —, wird **Zusammenführung aufheben** mit einer Fehlermeldung abgewiesen (Refs #2200), genau wie das Zusammenführen selbst. Grund: beim Aufheben wandern die Ereignisse zur Quelle zurück, während der Hold auf der Zielperson stehen bleibt — er deckte sie danach nicht mehr, obwohl er weiter gilt. Der Hold wird bewusst **nicht** still auf die Quelle gespiegelt: eine Aufbewahrungssperre ist ein bewusster Beweismittel-Akt. Erst Hold aufheben oder ablaufen lassen, dann die Zusammenführung aufheben.
- **Offline-Geräte purgen die Quelle.** Der Merge widerruft die Offline-Bundles der aufgelösten Akte (personen-genau, wie bei einer Löschung). Offline bereits erfasste, noch nicht übertragene Ereignisse zur Quelle werden bewusst **nicht** automatisch auf das Ziel umgebucht — sie landen in der Dead-Letter-Liste der Offline-Konflikte zur Nachbearbeitung.
- **Kein Massenlösch-Alarm.** Eine Aufräum-Kampagne über Alt-Duplikate zählt nicht in die Massenlösch-Erkennung (`CLIENT_MERGED` ist eine eigene, nicht gezählte Vorgangsart) — es wird konsolidiert, nicht vernichtet.

Spur im Protokoll: `CLIENT_MERGED` (an der Quelle, mit Umkehr-Inventar im `detail`), beim Aufheben `CLIENT_RESTORED`; Antrag und Ablehnung nutzen die generischen `DELETION_REQUESTED`/`DELETION_REJECTED`, die Genehmigung schreibt bewusst **kein** `DELETION_APPROVED`.

### 7.6 Betroffenenrechte (Art. 15–20 DSGVO)

Für die Bearbeitung von Anfragen betroffener Personen stehen folgende administrative Möglichkeiten zur Verfügung:

| Recht | Maßnahme |
|---|---|
| Auskunft (Art. 15) | Datenauskunft als JSON oder PDF über die Personenansicht ziehen (Leitung oder Anwendungsbetreuung, Re-Authentifizierung erforderlich) — Umfang siehe unten |
| Berichtigung (Art. 16) | Berichtigungsantrag in der Personenakte stellen (Abschnitt „Berichtigung (Art. 16 DSGVO)" → „Berichtigung beantragen", ab Fachkraft); Leitung oder Anwendungsbetreuung prüft ihn unter **Berichtigungen** ([ADR-041](adr/041-berichtigungsworkflow-intern.md)). Die Verwaltungsoberfläche zeigt Personen, Fälle, Ereignisse und Aufgaben nur lesend |
| Löschung (Art. 17) | Löschantrag über die Anwendung stellen (4-Augen-Prinzip) |
| Einschränkung (Art. 18) | **Sperrvermerk** an der Personenakte setzen — siehe unten |
| Datenportabilität (Art. 20) | Export-Funktion in der Anwendung |

#### Einschränkung der Verarbeitung: der Sperrvermerk (Art. 18 DSGVO, Refs #2045)

Bestreitet eine begleitete Person die Richtigkeit ihrer Daten oder liegt einer der übrigen Tatbestände des Art. 18 Abs. 1 vor, setzen Sie in der Personenakte einen **Sperrvermerk**. Er wirkt so:

| Frage | Antwort |
|---|---|
| **Was bleibt erlaubt?** | Die **Speicherung** (Art. 18 Abs. 2). Der Datensatz wird nicht gelöscht, nicht versteckt und bleibt in Auskunft und Statistik. |
| **Was ist gesperrt?** | Die inhaltliche Arbeit: Die Person lässt sich nicht mehr bearbeiten und keinem neuen Kontakt, Fall oder keiner neuen Aufgabe zuordnen. |
| **Was ist ausgenommen?** | Der **Berichtigungsworkflow** (Art. 16, § 7.6). Er ist der Weg, auf dem die bestrittene Richtigkeit geprüft wird — eine Sperre, die ihn mitblockierte, wäre eine Sackgasse. |
| **Wer setzt?** | Fachkräfte und aufwärts, aus der Personenakte heraus. Beim Stellen eines Berichtigungsantrags wird der Vermerk zusätzlich **angeboten** (Opt-in, nicht vorbelegt). |
| **Wer hebt auf?** | Eine **zweite** berechtigte Person oder die **Leitung**. Wer den Vermerk gesetzt hat, hebt ihn nicht allein wieder auf. |
| **Sichtbarkeit** | Dauerhaftes Banner in der Personenakte mit Rechtsgrund, Zeitpunkt und setzender Person; in der Personenliste ein Kennzeichen „Eingeschränkt". |
| **Spur** | Audit-Einträge `PROCESSING_RESTRICTED` und `PROCESSING_RESTRICTION_LIFTED`, jeweils mit dem Rechtsgrund — nie mit dem strittigen Sachverhalt. |

**Nicht zu verwechseln mit dem Legal Hold** (§ 7.3): Der blockiert die *Löschung* (Art. 17 Abs. 3), der Sperrvermerk die *Nutzung*. Beide können unabhängig voneinander bestehen.

**Organisatorisch zu leisten:** Die Unterrichtung der betroffenen Person **vor** dem Aufheben (Art. 18 Abs. 3) erzwingt die Software nicht — halten Sie sie in Ihrem Auskunftsprozess fest.

#### Historien-Diff: Entschlüsselung für berechtigte Rollen

Die Ereignis-Detailseite zeigt unter **Änderungshistorie** die gespeicherten Vorher-/Nachher-Stände. Verschlüsselte Felder werden dort zur Anzeigezeit entschlüsselt, sofern die aktuelle Rolle das Feld nach Dokumentations- und Feld-Sensibilität sehen darf: Assistenz bis **Normal**, Fachkraft bis **Erhöht**, Leitung und Anwendungsbetreuung bis **Hoch**. Oberhalb der Rollengrenze steht weiterhin `[Eingeschränkt]`; ohne angemeldeten Nutzer bleibt ein verschlüsselter Wert fail-closed als `●●●●●` maskiert. Datei-Marker erscheinen immer als `[Anhang]`, nie als technische ID.

Die Darstellung gilt **rückwirkend für den gesamten Bestand**. Damit können berechtigte Rollen auch frühere Werte lesen, die später berichtigt oder entfernt wurden. Dieser Zugriff wurde vor der Umsetzung ausdrücklich für dieselben Rollen freigegeben, die die Werte bereits in Detail-/Bearbeitungsansicht und Art.-15-Auskunft sehen dürfen (#1961); berücksichtigen Sie ihn in Rollenvergabe, Datenschutz-Folgenabschätzung und Auskunftsprozess. Ein redigierter Lösch-Historieneintrag enthält keinen alten Wert und zeigt deshalb nur `– (gelöscht)`.

#### Umfang der Auskunft (Art. 15)

Die Auskunft wird in der Personenansicht als **JSON** (maschinenlesbar, Art. 20) oder als **PDF** (lesbares Dokument) erzeugt. Beide Formate decken denselben Umfang ab; das PDF fasst zusammen, lässt aber keine Kategorie aus. Auslösen dürfen die Rollen mit Export-Recht (Leitung, Anwendungsbetreuung); der Aufruf verlangt eine Re-Authentifizierung (Sudo-Mode), ist ratelimitiert und wird selbst im Audit-Log protokolliert.

Enthalten sind (Refs #1752):

| Kategorie | Inhalt |
|---|---|
| Stammdaten | Pseudonym, Kontaktstufe, Altersgruppe, **Notizen**, Aktiv-Status, K-Anonymisierungs-Status, Papierkorb-Status (gelöscht am/von), Erstell- und Änderungszeitpunkt, Zusatzfelder inkl. nicht mehr zugeordneter Felder (entschlüsselt) |
| Fälle | Titel, Beschreibung, Status, Fristen, Fallverantwortung, Papierkorb-Status, Zusatzfelder |
| Gelöschte Fälle | Fälle im Papierkorb als **eigener Abschnitt** (Refs #1818): dieselben Angaben plus „gelöscht am/von" und der Tilgungsstempel — bis zur endgültigen Anonymisierung sind sie gespeicherter Datenbestand und gehören in die Auskunft |
| Episoden | Titel, Beschreibung, Beginn/Ende je Fall |
| Wirkungsziele | Ziele samt Meilensteinen und Erreichungsstand |
| Ereignisse | Dokumentationstyp, Zeitpunkt, Fall-/Episoden-Bezug, Felddaten (entschlüsselt) |
| Gelöschte Ereignisse | Existenz der noch nicht endgültig entfernten Ereignisse (Papierkorb) samt **Löschzeitpunkt und auslösender Person** (Refs #2157) — bei einer Löschung durch den Aufbewahrungslauf bleibt die Person leer, weil dort niemand handelt |
| Änderungshistorie | Aktion, Zeitpunkt, bearbeitende Person **und die Inhalte vorher/nachher** — auch für gelöschte Ereignisse |
| Löschanträge | Anträge auf Löschung von Ereignissen, Fällen **und der Person selbst**, inkl. Begründung und 4-Augen-Prüfung |
| Berichtigungsanträge | Art.-16-Anträge zur Person (Refs #1796, [ADR-041](adr/041-berichtigungsworkflow-intern.md)) in **allen** Status (offen, genehmigt, abgelehnt): betroffenes Feld, bisheriger und gewünschter Wert (entschlüsselt), Begründung, Prüfvermerk, beantragende und prüfende Person, Zeitpunkte |
| Aufbewahrung | Aufbewahrungssperren (Legal Holds, Art. 17 Abs. 3 — **nicht** Art. 18) und offene Löschvorschläge samt Frist und Kategorie |
| Einschränkung der Verarbeitung | Sperrvermerk nach Art. 18 DSGVO (Refs #2045), sofern gesetzt: Rechtsgrund, Zeitpunkt und setzende Person |
| Aufgaben | Titel, Beschreibung, Typ, Status, Priorität, Zuständigkeit, Fristen |
| Aktivitäten | Zeitstrom-Einträge zur Person, ihren Fällen, Ereignissen und Aufgaben |
| Merkliste | Zeitpunkte, zu denen die Personenakte aufgerufen bzw. als Favorit markiert wurde |
| Zusammenführungen | Herkunft nach Duplikat-Merges (Refs #1876, [ADR-045](adr/045-personen-merge.md) §7): welche Akte wann in diese überführt wurde und ob sie schon endgültig anonymisiert ist; die Auskunft der aufgelösten Akte nennt umgekehrt „Zusammengeführt in" |
| Zugriffsprotokoll | Audit-Einträge zur Person, ihren Fällen und ihren Ereignissen: Aktion, Zeitpunkt, Bezug — einschließlich der Schritte der Vier-Augen-Workflows (Refs #2048): Löschanträge erscheinen unter der Kategorie „Löschantrag“ (beantragt/genehmigt/abgelehnt, aus dem Personen-, Fall- und Zusammenführungs-Pfad), Berichtigungsanträge unter „Berichtigung“ |
| Dateianhänge | **Metadaten** je Anhang der Person, ihrer Fälle und ihrer Ereignisse (Refs #1738): Feldname und Feld-Slug, entschlüsselter Dateiname, Dateigröße, MIME-Typ, Zeitpunkt und hochladende Person, sowie der Zustand in der Versionskette (aktuell ja/nein, ersetzt am, entfernt am) — **vollständige Kette inklusive ersetzter und entfernter Glieder** |

**Zur Struktur der Anhang-Angaben (JSON):** personen-eigene Anhänge stehen unter dem Top-Level-Schlüssel `attachments` — er ist **immer** vorhanden, ggf. als leere Liste. Fall- und Ereignis-Anhänge stehen als `attachments` **im jeweiligen Eintrag** unter `cases[i]` bzw. `events[i]` und erscheinen dort nur, wenn nicht leer (das Bestandsformat für Datensätze ohne Dateien bleibt damit unverändert); gelöschte Ereignisse (`deleted_events`) tragen sie ebenso. Lässt sich ein Dateiname nicht entschlüsseln, steht dort `[verschlüsselt]` — die Auskunft erfindet keinen Namen. Der MIME-Typ ist der beim Upload **verifizierte** Typ; nur für Bestandszeilen ohne Erkennung fällt er auf den vom Browser gemeldeten zurück. In den Zusatzfeldern selbst erscheint statt des technischen Datei-Markers der Platzhalter `[Dateianhang – Angaben unter „Dateianhänge"]`. **Im PDF** stehen die personen-eigenen Anhänge als eigener Abschnitt „Dateianhänge"; Fall- und Ereignis-Anhänge stehen als Spalte „Dateianhänge" in der jeweiligen Tabelle.

**Zur Struktur der gelöschten Fälle (JSON):** sie stehen unter dem Top-Level-Schlüssel `deleted_cases` — er ist **immer** vorhanden, ggf. als leere Liste; `cases` führt ab #1818 nur noch die **lebenden** Fälle. Beide Listen tragen je Eintrag `is_deleted`, `deleted_at`, `deleted_by` und `anonymized_at`. Der letzte Wert beantwortet die Frage, die `is_deleted` allein offen lässt: Ist er gesetzt, sind die Inhalte des Falls **unwiderruflich entfernt** (die Zeile bleibt bewusst stehen, § 7.5); ist er leer, liegt der Fall nur im Papierkorb und ist wiederherstellbar. Episoden und Wirkungsziele eines gelöschten Falls bleiben in **einer** Liste (`episodes`, `outcome_goals`) — sie sind über den Falltitel beiden Abschnitten zuzuordnen. **Im PDF** stehen die Papierkorb-Fälle als eigener Abschnitt „Fälle im Papierkorb" direkt hinter „Fälle".

**Zur Struktur der gelöschten Ereignisse (JSON):** sie stehen unter dem Top-Level-Schlüssel `deleted_events` — er ist **immer** vorhanden, ggf. als leere Liste; `events` führt nur die **lebenden** Ereignisse. Beide Listen tragen je Eintrag `is_deleted`, `deleted_at` und `deleted_by`. Der Ereignis-Verweis innerhalb der Änderungshistorie bleibt dagegen bewusst schmal (Dokumentationstyp, Zeitpunkt, Papierkorb-Kennzeichen) — er ordnet die Änderung nur zu; die Löschangaben stehen im Abschnitt `deleted_events` selbst. Ein `anonymized_at` gibt es hier bewusst **nicht**: anders als beim Fall werden die Inhalte eines Ereignisses schon beim Löschen entfernt, es gibt also keine zweite Frist bis zur Tilgung — was noch vorhanden ist, steht in der Änderungshistorie. **Ereignisse, deren Löschangaben nicht erfasst wurden, tragen bei beiden Feldern `null`**: die Angaben lassen sich nicht rückwirkend rekonstruieren (insbesondere nicht, wer gelöscht hat) — die Auskunft weist die Lücke aus, statt einen Wert zu erfinden. Ist `deleted_at` gesetzt und `deleted_by` leer, hat der Aufbewahrungslauf gelöscht (Fristablauf, keine handelnde Person). **Im PDF** trägt der Abschnitt „Gelöschte Ereignisse (Papierkorb)" dieselben zwei Spalten „Gelöscht am" und „Gelöscht von" wie der Abschnitt „Fälle im Papierkorb"; statt einer leeren Zelle steht dort „Aufbewahrungslauf" (Systemlöschung) bzw. „vor 0.96 nicht erfasst" (Löschangaben fehlen), damit die betroffene Person die drei Fälle unterscheiden kann.

**Zur Struktur der Zusammenführungen (JSON):** sie stehen unter dem Top-Level-Schlüssel `merged_from` — er ist **immer** vorhanden, ggf. als leere Liste. Je überführter Akte: `pseudonym`, `merged_at` und `anonymized`. Das Pseudonym kommt aus der **lebenden** Papierkorb-Zeile: solange die aufgelöste Akte unredigiert im Papierkorb liegt, steht es im Klartext; nach der Tilgung durch den Aufbewahrungslauf steht dort nur noch der redigierte Marker (`anonymized: true`) — die Auskunft trägt das Klartext-Pseudonym also exakt so lange wie der Papierkorb selbst. Umgekehrt trägt die Auskunft der aufgelösten Akte im `client`-Block das Feld `merged_into` (nur an Tombstones vorhanden; „Gelöscht am" ist dann zugleich der Zeitpunkt der Zusammenführung), und ein Zusammenführungs-Antrag ist in `deletion_requests` mit `merge_request: true` markiert. **Im PDF** stehen die Zusammenführungen als eigener Abschnitt „Zusammenführungen" am Ende, der Tombstone-Verweis als Stammdaten-Zeile „Zusammengeführt in". **Wichtig für Art.-15-Anfragen über die volle Historie:** Das Zugriffsprotokoll der aufgelösten Akte bleibt an deren Datensatz (append-only) und läuft **nicht** in den Export des Ziels — beantworten Sie eine solche Anfrage mit **beiden** Exporten (Ziel-Akte und Tombstone; letzterer bleibt über den Papierkorb bzw. die Akten-URL exportierbar, auch nach der Redaktion).

**Zur Struktur der Berichtigungsanträge (JSON):** sie stehen unter dem Top-Level-Schlüssel `rectification_requests` — er ist **immer** vorhanden, ggf. als leere Liste. Je Eintrag: `field_path` (technischer Feldbezeichner, `pseudonym`/`age_cluster`/`notes` oder `data_json.<slug>`) und `field_label` (lesbarer Feldname; bei einem inzwischen entfernten Zusatzfeld fällt es auf den rohen Pfad zurück), `status`, `old_value`/`new_value`, `reason`, `review_note`, `requested_by`/`reviewed_by`, `created_at`/`reviewed_at`. Werte werden entschlüsselt und lesbar ausgewiesen: eine Altersgruppe erscheint als „18–26" statt `18_26`, ein Auswahlfeld mit seinem Options-Label. Wurde die Person zwischenzeitlich anonymisiert, sind alle vier Wert-/Freitextspalten geleert (Refs #1792) — der Antrag bleibt als Vier-Augen-Nachweis mit leeren Werten stehen (im PDF „—"). Lässt sich ein Wert nicht entschlüsseln, steht dort `[verschlüsselt]`. **Im PDF** stehen die Anträge als eigener Abschnitt „Berichtigungsanträge" direkt hinter den Löschanträgen; die zugehörigen Audit-Zeilen (beantragt/genehmigt/abgelehnt) erscheinen im Zugriffsprotokoll unter der Kategorie „Berichtigung".

**Bewusst nicht enthalten — als manueller Ergänzungsschritt zu prüfen:**

1. **Namen der zugreifenden Beschäftigten im Zugriffsprotokoll.** Das Protokoll führt Aktion, Zeitpunkt und Bezug, aber keine Actor-Identität: „wer griff wann zu" ist zugleich ein Zugriffsprotokoll über Beschäftigte und damit ein Datum Dritter (Art. 15 Abs. 4 DSGVO). Dasselbe gilt für die Merkliste. Verlangt die betroffene Person auch diese Angaben, ist eine Abwägung im Einzelfall nötig — die Rohdaten stehen der Anwendungsbetreuung unter `/audit/` zur Verfügung.
2. **Binärinhalte** jeder Art (Dateien, Bilder). Die Auskunft benennt jede Datei, liefert sie aber nicht mit; die Datei selbst geben Sie bei Bedarf über den regulären Download aus der Detailansicht heraus.

Der PDF-Hinweiskasten benennt beide Punkte gegenüber der betroffenen Person ausdrücklich — die Auskunft behauptet keine Vollständigkeit, die sie nicht hat.

### 7.7 Empfohlene organisatorische Maßnahmen

- Führen Sie ein **Verzeichnis der Verarbeitungstätigkeiten** (Art. 30 DSGVO), das die Nutzung von Anlaufstelle als Verarbeitungstätigkeit beschreibt.
- Stellen Sie sicher, dass der Hosting-Anbieter einen **Auftragsverarbeitungsvertrag (AVV)** nach Art. 28 DSGVO unterzeichnet.
- Führen Sie eine **Datenschutz-Folgenabschätzung (DSFA)** nach Art. 35 DSGVO durch, falls Ihre Einrichtung besonders sensible Daten (z. B. Gesundheitsdaten, Daten schutzbedürftiger Personengruppen) verarbeitet.
- Beschränken Sie den Admin-Zugang auf das notwendige Minimum (**Least Privilege**).
- Erzwingen Sie einen Passwortwechsel dort, wo ein Passwort **außerhalb** der Anwendung bekannt ist: Beim Fallback ohne E-Mail-Adresse setzt die Neuanlage `must_change_password` selbst, für ein nachträglich von Hand vergebenes Passwort aktivieren Sie das Feld **Passwort muss geändert werden** in der Benutzer-Bearbeitung. Bei Konten **mit** E-Mail-Adresse ersetzt der Einladungsweg diese Maßnahme — der Nutzer wählt sein Passwort dort selbst, und der abgeschlossene Setup-Schritt löscht das Kennzeichen wieder (§ 2.4, #1969).
- Lagern Sie Backups und `ENCRYPTION_KEY` an getrennten, gesicherten Orten.

### 7.8 Optimistic Locking

Zum Schutz vor **stillen Überschreibungen** bei parallelem Bearbeiten desselben Datensatzes (zwei Mitarbeitende haben den gleichen Client/Fall gleichzeitig offen und speichern nacheinander) setzt die Anlaufstelle **Optimistic Locking** auf Service-Ebene ein. Refs #531.

**Betroffene Modelle:** Client, Case, WorkItem, Settings, Event.

**Mechanik** ([`src/core/services/security/locking.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/locking.py)):

- Jedes Formular rendert das aktuelle `updated_at` als Hidden-Field.
- Beim Speichern prüft der Helper `check_version_conflict(instance, expected_updated_at)`, ob der Datensatz in der Zwischenzeit verändert wurde.
- Bei Konflikt wird eine `ValidationError` geworfen. In den klassischen HTML-Bearbeitungsmasken für Personen, Fälle und Ereignisse wird nichts gespeichert; bei weiterhin zulässigem Zugriff bleibt das Formular an die eigene Eingabe gebunden. Ein separater Abschnitt „Aktueller Stand“ zeigt ausschließlich die für die Rolle zulässigen Serverwerte und markiert Abweichungen. Erst nach Prüfen oder Kopieren bewusst erneut speichern: Die Formularwerte ersetzen dann die entsprechenden Serverwerte auf der angezeigten Grundlage; eine weitere zwischenzeitliche Änderung führt erneut zum Konflikt (#2539).
- Andere Speicher- und Uploadfehler zeigen zulässige Texte ebenfalls wieder an, erneuern aber den Versionsnachweis nicht. Dateien erneut auswählen; betroffene Dateifelder werden genannt. Entfällt der Zugriff auf den Datensatz, gilt weiterhin die Zugangsabweisung. JSON-/Offline-Replay-Konflikte behalten ihren bisherigen Antwortvertrag.

**Administrative Hinweise:**
- Es gibt keinen Admin-Schalter, um das Locking abzuschalten — der Schutz ist systemweit aktiv.
- Wenn Nutzer die Konfliktmeldung gehäuft sehen: Prozess prüfen (Mehrfachbearbeitung trennen, Workflows anpassen) statt das Feature zu umgehen.

### 7.9 Row Level Security (RLS)

Zusätzlich zum ORM-seitigen Facility-Scoping ist **PostgreSQL Row-Level-Security** auf **allen facility-scoped Tabellen** (per Test ableitungsbasiert erzwungen) als **Defense-in-Depth** aktiviert. Ein fehlerhafter ORM-Query, der das Facility-Scoping vergisst, liefert dank RLS trotzdem keine fremden Daten. Refs #542, #586.

#### Funktionsweise

- Die Middleware [`FacilityScopeMiddleware`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/facility_scope.py) setzt pro Request zwei Postgres-Session-Variablen in einem Aufruf: `app.current_facility_id` (ID der Einrichtung der angemeldeten Person) und `app.is_super_admin` (`'true'` für Sitzungen der Systemadministration, sonst leer), jeweils via `set_config(…, false)` (Session-Scope, nicht Transaction-Scope).
- Die RLS-Policies (Migration [`0047_postgres_rls_setup`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0047_postgres_rls_setup.py)) filtern jede Zeile über `facility_id::text = current_setting('app.current_facility_id', true)`. Migration [`0085_rls_superadmin_bypass`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0085_rls_superadmin_bypass.py) ergänzt alle diese Policies um den Zweig `OR current_setting('app.is_super_admin', true) = 'true'`: Sitzungen der Systemadministration umgehen damit die Einrichtungsisolation (ihre Lesezugriffe protokolliert das AuditLog als `SYSTEM_VIEW`, § 7.2).
- **Fail-closed nur ohne Superadmin-Zweig:** Ist `app.current_facility_id` leer oder nicht gesetzt **und** steht `app.is_super_admin` nicht auf `'true'`, liefert `current_setting(..., true)` einen leeren Wert beziehungsweise NULL, der Vergleich trifft keine Zeile und es werden **keine Zeilen** zurückgegeben. Steht `app.is_super_admin` auf `'true'`, gibt die Policy alle Zeilen frei, auch bei leerem Facility-Scope; das ist der normale Fall der Systemadministration.
- Die Policies filtern die App-Rolle (`POSTGRES_USER`, `NOBYPASSRLS`). Die Admin-Rolle (`POSTGRES_ADMIN_USER`, `BYPASSRLS`) für Migrationen, Wartung und Aufbewahrungslauf sowie der Bootstrap-Superuser umgehen sie.
- Die Middleware setzt beide Variablen bei **jedem** Request neu, auch bei anonymen: Dort sind beide leer. So kann Connection-Pooling keinen stehengebliebenen Wert aus einem früheren Request weitergeben.
- Bei einer archivierten Einrichtung leert die Middleware den Facility-Scope nur für Requests, die sie sperrt (Antwort 403). Nicht gesperrt sind Requests der Systemadministration und die Pfade aus `ARCHIVED_EXEMPT_URLS` (`/login/`, `/logout/`, `/static/`, `/i18n/`, `/health/`, `/sw.js`, `/manifest.json`); für ein angemeldetes Fachkonto der archivierten Einrichtung trägt `app.current_facility_id` dort weiter die ID seiner Einrichtung.

#### Debugging in `psql`

```sql
-- Aktuellen Facility-Scope einer Session prüfen
SELECT current_setting('app.current_facility_id', true);

-- Variable manuell für eine Debug-Session setzen
SELECT set_config('app.current_facility_id', '<facility-uuid>', false);
```

Mit der App-Rolle und ohne gesetzte Variablen liefern die geschützten Tabellen in `psql` sichtbar **keine Zeilen** — das ist gewollt. Unter der Admin-Rolle oder dem Bootstrap-Superuser greift RLS nicht; dort sehen Sie alle Zeilen.

---

## 8. Statistik-Snapshots & Materialized View

### Überblick: zwei Ebenen

Statistik-Auswertungen in der Anlaufstelle nutzen **zwei unterschiedliche Beschleunigungs-Ebenen**:

1. **Materialized View** (`core_statistics_event_flat`) — aggregiert die aktuellen Event-Daten vor, damit die Statistik-Seite nicht bei jedem Aufruf alle Events neu scannen muss. Refs #544.
2. **Statistik-Snapshots** — monatliche, persistierte Aggregate, die die von der automatischen Löschung erfassten Events weiter mitzählen. Dadurch zählen historische Auswertungen auch Events, die der Aufbewahrungslauf gelöscht hat.

### Materialized View aktualisieren

Die Materialized View wird **nicht live** mit jedem `INSERT` aktualisiert — sie muss periodisch via Management-Command neu aufgebaut werden:

```bash
# Standard (non-blocking, nutzt CONCURRENTLY wenn möglich)
docker compose -f docker-compose.prod.yml run --rm maintenance \
  refresh_statistics_view

# Blockierend (falls kein UNIQUE-Index vorhanden)
docker compose -f docker-compose.prod.yml run --rm maintenance \
  refresh_statistics_view --no-concurrent
```

Implementierung: [`src/core/management/commands/refresh_statistics_view.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/refresh_statistics_view.py).

> **Bereits automatisiert (Refs #794):** Der mitgelieferte `cron`-Sidecar aus `docker-compose.prod.yml` führt diesen Job ab Werk aus. Das folgende Crontab-Beispiel gilt nur, wenn der `cron`-Dienst nicht läuft; es startet den Job über den `maintenance`-Dienst derselben Compose-Datei, der als einziger die Admin-Rolle (BYPASSRLS) erhält (ohne Compose in der eigenen Orchestrierung nachbilden: gleiches Image, Entrypoint `python manage.py`, Admin-Rolle). Wer beides einrichtet, lässt den Job doppelt laufen — Details: [Ops-Runbook § 3](ops-runbook.md#3-cron-jobs).

**Empfohlener Cron-Rhythmus:**

```cron
# Stündlich (nahe-echtzeit) — für stark genutzte Statistik-Seiten
0 * * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance refresh_statistics_view >> /var/log/anlaufstelle-statistics.log 2>&1

# Alternativ: täglich nachts (für einmalige Berichte) — nur einmal umstellen
# 0 1 * * * …
```

> **Hinweis:** `CONCURRENTLY` blockiert lesende Zugriffe nicht, setzt aber einen UNIQUE-Index auf der Materialized View voraus (wird per Migration angelegt). Der Command fällt bei Fehlern automatisch auf einen non-concurrent Refresh zurück.

### Was sind Statistik-Snapshots?

Anlaufstelle berechnet Statistiken (Dashboard, Halbjahresberichte, Beispiel-Sachbericht-PDFs) standardmäßig aus der Event-Tabelle (über die Materialized View aggregiert). Wenn die automatische Datenlöschung (`enforce_retention`) alte Events entfernt, würden diese aus den Statistiken verschwinden.

**Statistik-Snapshots** halten monatliche Aggregate fest, die auch vom Aufbewahrungslauf gelöschte Events weiter zählen. Die Auswertungen nutzen eine Hybrid-Logik: gespeicherte Snapshots für vergangene Monate, Live-Daten (aus der Materialized View) für den aktuellen Monat.

> **Wichtig: Der Snapshot-Schutz gilt nicht für jeden Bericht.** Die Übersicht liest Ereigniszahlen hybrid aus Monats-Aggregaten mit Live-Fallback; Personenkennzahlen bleiben live. Die Übersicht hat keinen PDF-Export. Im Reiter **„Datensatz-Berichte“** liest nur das PDF des **Beispiel-Sachberichts** hybrid aus Monats-Aggregaten, mit Live-Fallback für fehlende Snapshots und Teilmonate; unterschiedliche Personen werden auch dort live über den Gesamtzeitraum gezählt. Vorschau, CSV/JSON/XML und alle Formate der anderen Berichte rechnen live. Nach einer Bereinigung durch `enforce_retention` können Live-Zahlen daher niedriger sein, bis auf null. Die Wirkung hängt an der jeweiligen Grundgesamtheit. **Bewahren Sie herausgegebene Dateien selbst auf:** Sie lassen sich später nicht notwendig identisch reproduzieren. Beachten Sie den Quellenausweis je Format und die Regeln unter [„Herausgabe von Berichten“](#herausgabe-von-berichten-was-sie-wissen-müssen).

### Automatische Sicherung

Snapshots werden automatisch erstellt, wenn `enforce_retention` Events löscht: In derselben Transaktion, in der der Lauf ein Event löscht, rechnet er es dem Aggregat seines Monats hinzu, auch dem laufenden Monat — bricht der Lauf ab, fehlt kein bereits gelöschtes Event im Aggregat, und überschneiden sich zwei Läufe, zählt jedes Event genau einmal. Der Löschlauf erweitert den Übertrag gelöschter Events und berechnet den Monat in derselben Transaktion aus dem Übertrag und den noch vorhandenen Events neu; so werden auch nach dem bisherigen Snapshot nacherfasste Events gezählt, ohne bereits gezählte Beiträge zu verdoppeln. Jede Neuberechnung eines Monats mit `create_statistics_snapshots` zählt die noch vorhandenen Events und addiert die gelöschten. Kann der Lauf ein Event nicht in das Aggregat übernehmen (etwa weil ein Berichtsmodul einen Fehler meldet), löscht er es nicht, schreibt den Fehler als `ERROR` mit der Event-ID ins Anwendungs-Log und versucht es beim nächsten Lauf erneut — bis zur Behebung verzögert sich die Löschung. Auch der Ereignis-Löschdienst (einschließlich Vier-Augen-Freigabe und Strichlisten-Rücknahme) überträgt gelöschte Events in das Monatsaggregat; sie zählen nach einer Neuberechnung weiter. Die Kachel „Statistik“ in der Arbeitszentrale der Leitung nennt als „letzter Snapshot“ den jüngsten abgeschlossenen Monat.

Die Neuberechnung setzt den Übertrag (`_deleted`) dieser Version voraus; Bestände aus Versionen vor Einführung dieses Übertrags werden nicht rekonstruiert.

### Periodischer Cron-Job (empfohlen)

Zusätzlich empfiehlt es sich, Snapshots regelmäßig per Cron zu erstellen:

> **Bereits automatisiert (Refs #794):** Der mitgelieferte `cron`-Sidecar aus `docker-compose.prod.yml` führt diesen Job ab Werk aus. Das folgende Crontab-Beispiel gilt nur, wenn der `cron`-Dienst nicht läuft; es startet den Job über den `maintenance`-Dienst derselben Compose-Datei, der als einziger die Admin-Rolle (BYPASSRLS) erhält (ohne Compose in der eigenen Orchestrierung nachbilden: gleiches Image, Entrypoint `python manage.py`, Admin-Rolle). Wer beides einrichtet, lässt den Job doppelt laufen — Details: [Ops-Runbook § 3](ops-runbook.md#3-cron-jobs).

```cron
# Monatlich am 1. um 04:00 (wie im Sidecar) — sichert Vormonat und historische Monate
0 4 1 * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance create_statistics_snapshots >> /var/log/anlaufstelle-snapshots.log 2>&1
```

### Ersteinrichtung (Backfill)

Bei der Ersteinrichtung oder nachträglichen Aktivierung können Snapshots für alle vorhandenen Monate erzeugt werden:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance create_statistics_snapshots --backfill
```

**Hinweis:** Der Backfill berücksichtigt vergangene Monate aus der Vereinigung von Monaten mit noch vorhandenen Events und gespeicherten Snapshot-Monaten, einschließlich Monaten mit ausschließlich übertragenen gelöschten Events. Bereits gelöschte Daten ohne einen solchen Übertrag können rückwirkend nicht rekonstruiert werden. Auch der reguläre Lauf ohne Monatsangabe berechnet diese vergangenen Monate sowie den Vormonat neu und berücksichtigt dadurch Nacherfassungen und Änderungen. Der ausgewiesene „Stand“ (`updated_at`) ändert sich nur, wenn sich bei der Neuberechnung die veröffentlichten Aggregate oder Backend-Payloads ändern; reine Übertrag-Buchhaltung verschiebt ihn nicht. Personen-Untergrenzen werden über Segmente mit dem Maximum statt einer Summe zusammengeführt, damit kleine Zellen weiterhin unterdrückt bleiben.

### Weitere Optionen

Alle Aufrufe laufen im Dienst `maintenance`. Als App-Rolle (`exec web`) sähe der Lauf keine Einrichtungseinstellungen, übersprang jede Einrichtung („No settings … skipping“) und endete trotzdem mit Exit 0.

```bash
# Vorschau (keine Änderungen)
docker compose -f docker-compose.prod.yml run --rm maintenance create_statistics_snapshots --dry-run

# Nur eine bestimmte Einrichtung
docker compose -f docker-compose.prod.yml run --rm maintenance create_statistics_snapshots --facility "Meine Einrichtung"

# Bestimmter Monat
docker compose -f docker-compose.prod.yml run --rm maintenance create_statistics_snapshots --year 2026 --month 2
```

### Einschränkungen

- **CSV-Export:** Der CSV-Export enthält weiterhin nur vorhandene Events (keine Snapshot-Daten), da er Einzelzeilen exportiert.
- **Top-Personen:** Die Rangliste der aktivsten Personen wird immer live berechnet und kann sich nach einer Löschung verändern.
- **Eindeutige Personen:** Die Zahl wird über den gesamten Berichtszeitraum **live** eindeutig gezählt und nicht aus den Monats-Aggregaten summiert (#1770). Für Zeiträume, deren Events nach Ablauf der Löschfristen entfernt wurden, fällt sie deshalb niedriger aus als die übrigen Zahlen desselben Berichts; die Artefakte weisen das an der Zahl aus.

### Herausgabe von Berichten: was Sie wissen müssen

Datensatz-Berichte unterdrücken Kleinstfallzahlen. Der Schutz gilt **je Artefakt und je Zeitraum** und ist allein keine Anonymitätsgarantie, insbesondere nicht über mehrere Herausgaben hinweg. Die interne Ereignisliste enthält dagegen personenbezogene Pseudonyme. Für Berichtsdateien gelten folgende Betriebsregeln.

**1. Sachbericht-PDF und Live-Export desselben Zeitraums nicht an denselben Empfänger geben.**

Nur das Beispiel-Sachbericht-PDF liest hybrid aus **Monats-Aggregaten** mit Live-Fallback; seine unterschiedlichen Personen werden live gezählt. Seine Vorschau und CSV/JSON/XML sowie alle Formate der anderen Datensatz-Berichte rechnen **live**. Unterschiede zwischen gespeicherten Ereigniszahlen und aktuellem Bestand können Informationen über inzwischen gelöschte Datensätze offenlegen. Auch jede für sich unterdrückte Datei schützt deshalb nicht automatisch vor Rückschlüssen aus der gemeinsamen Auswertung.

Jedes Artefakt weist deshalb **Quelle und Stand** aus („Datenquelle: Monats-Aggregate", „Stand der Monats-Aggregate: …"). Prüfen Sie diese Zeile, bevor Sie zwei Berichte in dieselbe Mappe legen.

**2. Berichtszeiträume nicht unter einem Monat wählen — und Überschneidungen im Blick behalten.**

Werden für dieselbe Einrichtung Berichte über Teilzeiträume **und** über deren Vereinigung herausgegeben (zwölf Monatsberichte plus ein Jahresbericht), lässt sich jede in einem Teilbericht unterdrückte Zelle aus der Summe der übrigen exakt zurückrechnen — auch dann, wenn in **keinem** der beteiligten Berichte eine einzige Zelle unterdrückt wurde. Das ist eine Eigenschaft des Verfahrens, kein Fehler: k-Anonymität ist eine Eigenschaft *einer* Tabelle über *einer* Grundgesamtheit.

Die **Freigabe-Übersicht** (Statistik → Reiter „Freigaben", Rolle: Leitung/Anwendungsbetreuung) listet die bereits herausgegebenen Berichtszeiträume aus der Audit-Spur und markiert Überschneidungen. Sie verhindert nichts — sie zeigt Ihnen, wo Sie hinsehen müssen. Damit die Meldung bewertbar bleibt, erfassen Sie beim Export die optionalen Felder **Empfänger** und **Zweck**; prüfen Sie auch eine mögliche Zusammenführung bei verschiedenen Empfängern. Verschiedene Empfänger verringern das Risiko nur unter der organisatorischen Annahme, dass sie die Dateien nicht zusammenführen; die Anwendung garantiert dies nicht.

> **Aufbewahrung — drei Klassen, nicht zwei.** Unter der Audit-Aktion „Export" laufen drei verschiedene Dinge, und nur das erste ist eine Herausgabe im Sinne dieser Übersicht:
>
> | Klasse | Beispiel | Aufbewahrung | In der Übersicht? |
> |---|---|---|---|
> | **Herausgegebene Berichtsdatei** | CSV-/PDF-/JSON-/XML-Download mit Berichtszeitraum | **unbefristet** (nicht von `auditlog_retention_months` gekappt) | ja |
> | Bericht am **Bildschirm** angesehen | Protokollierte HTML-/JSON-Bildschirmabrufe des externen Berichts; die Adresse leitet ohne Export-Audit zur Berichtskarte weiter | normale Frist | nein |
> | Auskunft/Download **ohne** Berichtszeitraum | Datenauskunft zu einer Person (Art. 15), DSGVO-Dokumente | normale Frist | nein |
>
> Ein Berichtszeitraum allein genügt also **nicht** — es muss auch eine Datei herausgegeben worden sein. Ohne die unbefristete Aufbewahrung der ersten Klasse täuschte die Übersicht Vollständigkeit vor; ohne die Befristung der zweiten wüchse das Audit-Log mit jedem Seitenaufruf unbegrenzt.
>
> Die unbefristet aufbewahrten Einträge enthalten Zeitraum, Format, Ihre Empfänger-/Zweckangaben und die auslösende Person. Zweckbindung: Nachweis herausgegebener Statistiken.
>
> **Empfänger und Zweck sind Freitextfelder.** Je Feld sind höchstens 200 Zeichen erlaubt; erkannte E-Mail-/Telefonmuster und typische Namenspaare werden vor der Herausgabe mit HTTP 400 abgewiesen, auch bei `DJANGO_AUDIT_DETAIL_STRICT=false`. Diese Heuristik erkennt nicht jede personenbezogene Angabe (z. B. bleiben institutionelle Präfixe wie „Stadt Musterstadt“ erlaubt). Tragen Sie die **Organisation** und einen sachlichen Zweck ein, keine Klientendaten oder Namen von Ansprechpersonen: akzeptierte Angaben bleiben im unveränderlichen Audit-Log.

**3. Die Übersicht hat zwei organisatorische Lücken.**

**Erstens: sie beginnt mit der Einführung dieser Protokollierung.** Eine Herausgabe ist an einer Markierung im Audit-Log erkennbar. Herausgaben ohne diese Markierung stehen weiterhin im Audit-Log, lassen sich dort aber nicht als Herausgabe erkennen — sie erscheinen nicht in der Übersicht, sind nicht von der Frist ausgenommen und lösen keine Überschneidungswarnung aus. **Nachtragen ist technisch ausgeschlossen:** das Audit-Log ist unveränderlich und über eine Prüfsummen-Kette gegen nachträgliche Änderungen gesichert; ein Nachtragen wäre von einer Manipulation nicht zu unterscheiden. Die Seite weist darauf hin.

**Zweitens: sie endet beim Download.** Eine weitergeleitete PDF-Datei, ein abfotografierter Bildschirm oder ein per Hand abgeschriebener Wert erscheinen dort nicht. Das ist eine organisatorische Grenze.

**Zeitraumprüfung ohne Mengengrenze:** Die Überschneidungswarnung berücksichtigt alle markierten Herausgaben derselben Artefaktdefinition in dieser Einrichtung, deren Berichtszeitraum den angezeigten berührt; identische Zeiträume lösen keine Warnung aus. Die Anzeige bleibt auf 50 Einträge je Seite begrenzt, die Warnzahl bezieht sich auf die aktuelle Seite. Ältere passende Herausgaben warnen auch jenseits von 500 Einträgen; eine handelnde Person zeigt diese Berichtssicht nicht.

### Admin-Oberfläche

Snapshots sind im Django-Admin unter **Statistik-Snapshots** einsehbar (nur Lesezugriff). Dort können Sie prüfen, welche Monate gesichert wurden und wann die letzte Aktualisierung stattfand.
