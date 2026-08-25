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
   - 2.11 [CSV-Import und -Export: Formelschutz](#211-csv-import-und--export-formelschutz)
3. [Backup und Wiederherstellung](#3-backup-und-wiederherstellung)
4. [Updates](#4-updates)
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

> **Alternative: Coolify auf Hetzner CX22** — Für das empfohlene Deployment via [Coolify](https://coolify.io/) (inkl. TLS, Backups und ClamAV) siehe den separaten Leitfaden [`docs/coolify-deployment.md`](https://github.com/anlaufstelle/app/blob/main/docs/coolify-deployment.md). Die folgenden Docker-Compose-Anleitungen gelten weiterhin für manuelle Deployments.

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
DJANGO_AUDIT_HASH_KEY=<separater-langer-zufaelliger-string>
DJANGO_SETTINGS_MODULE=anlaufstelle.settings.prod
ALLOWED_HOSTS=anlaufstelle.meine-einrichtung.de

# Datenbank (Drei-Rollen-Modell, Refs #902)
POSTGRES_DB=anlaufstelle
POSTGRES_USER=anlaufstelle                         # App-Rolle (Django-Runtime): NOSUPERUSER, NOBYPASSRLS
POSTGRES_PASSWORD=<sicheres-datenbankpasswort>
POSTGRES_ADMIN_USER=anlaufstelle_admin             # Admin-Rolle (Migrationen/Seed/Retention): NOSUPERUSER, BYPASSRLS
POSTGRES_ADMIN_PASSWORD=<sicheres-admin-passwort>
POSTGRES_BOOTSTRAP_PASSWORD=<sicheres-bootstrap-passwort>  # postgres-Bootstrap-Superuser (nur Rollen-Setup)

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

**Audit-Hash-Key generieren:**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

Der `DJANGO_AUDIT_HASH_KEY` sichert die Audit-Hashes — pseudonymisierte Einträge (z. B. E-Mails in Passwort-Reset-Audits) ebenso wie die HMAC-Integritätskette des Audit-Logs — und wird bewusst getrennt vom `DJANGO_SECRET_KEY` gehalten (siehe [`docs/disaster-recovery.md`](https://github.com/anlaufstelle/app/blob/main/docs/disaster-recovery.md)).

> **Wichtig:** Speichern Sie `ENCRYPTION_KEYS` und `DJANGO_SECRET_KEY` sicher (z. B. in einem Passwortmanager oder Secret-Management-System). Ohne die Schlüssel sind verschlüsselte Felddaten nicht mehr lesbar.

#### Vollständige Umgebungsvariablen-Referenz

Die betriebsrelevanten ENV-Variablen, die die Anwendung zur Laufzeit auswertet (siehe [`src/anlaufstelle/settings/base.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py) und [`prod.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/prod.py); `base.py`/`prod.py` bleiben die maßgebliche Quelle):

**Django & Hosts**

| Name | Default | Beschreibung |
|---|---|---|
| `DJANGO_SECRET_KEY` | — (Pflicht in prod) | Signierung von Sessions/CSRF. Mit `secrets.token_urlsafe(50)` generieren. |
| `DJANGO_AUDIT_HASH_KEY` | — (Pflicht in prod) | HMAC-Schlüssel für die Audit-Hashes: pseudonymisierte Einträge (z. B. Passwort-Reset-E-Mails) und die Integritätskette des Audit-Logs. Bewusst separat vom `DJANGO_SECRET_KEY` — ohne eigenen Schlüssel fiele der Audit-Hash sonst still auf `SHA256(SECRET_KEY)` zurück, was ein Secret-Key-Leak rückwirkend ausnutzbar machen würde; daher fail-closed (ohne Wert verweigert die App in Produktion den Start). Generieren: `python -c "import secrets; print(secrets.token_urlsafe(64))"`. Siehe [`docs/disaster-recovery.md`](https://github.com/anlaufstelle/app/blob/main/docs/disaster-recovery.md). |
| `DJANGO_SETTINGS_MODULE` || In Produktion `anlaufstelle.settings.prod`. |
| `ALLOWED_HOSTS` | — (Pflicht in prod) | Komma-separierte Hostnamen, z. B. `anlaufstelle.example.de`. |
| `EXTERNAL_ORIGIN` | abgeleitet aus `ALLOWED_HOSTS` | Feste Basis aller sicherheitsrelevanten Links — Einladungs-Setup-Link, Passwort-Reset-Link und Entsperr-Link. Format `https://host[:port]`, ohne Pfad und ohne abschließenden Schrägstrich; ein Wert mit Pfadanteil oder ohne Schema wird beim Start abgelehnt. Diese Links entstehen dadurch **nicht** mehr aus dem `Host`-Header des eingehenden Requests. Führt `ALLOWED_HOSTS` genau **einen** öffentlichen Hostnamen, leitet die Anwendung `https://<host>` selbst ab und die Variable kann entfallen — `localhost`, `127.0.0.1` und `::1` zählen dabei nicht mit, sie stehen dort nur für den Container-Healthcheck. Bei mehreren öffentlichen Hosts oder ausschließlich Subdomain-Mustern (`.example.de`) ist sie **Pflicht** — sonst verweigert die Anwendung den Start (#1967). |
| `TRUSTED_PROXY_HOPS` | `1` | Anzahl vertrauenswürdiger Proxies vor der App (X-Forwarded-For-Auswertung). `0` = kein Proxy, `1` = nur Caddy, `2` = CDN + Caddy. Ein Wert über `1` wirkt **nur zusammen mit** `TRUSTED_PROXY_CIDRS` — sonst sieht die Anwendung nie mehr als einen Eintrag und fällt auf die Proxy-IP zurück (#1963). |
| `TRUSTED_PROXY_CIDRS` | leer | Adressbereiche der Proxys, denen der Reverse-Proxy ein eingehendes `X-Forwarded-For` glauben darf — **nur** durch Leerzeichen getrennt (Kommas sind kein Trennzeichen, Netzmasken keine gültige Schreibweise), als CIDR (`203.0.113.0/24`, `2001:db8::/32`), als einzelne Adresse oder mit dem Schlüsselwort `private_ranges`. Leer lassen, solange Caddy der einzige Proxy ist: Caddy verwirft dann jeden vom Client mitgeschickten Wert und setzt den Header selbst. Vor einem CDN oder Loadbalancer ist die Variable **Voraussetzung** für `TRUSTED_PROXY_HOPS > 1`; derselbe Wert füllt die Caddy-Konfiguration und die Anwendung. Ein unbrauchbarer Bereich bricht den Start ab (#1963). |
| `DJANGO_HEALTH_DETAIL_TOKEN` || Token für den Header `X-Health-Token`; berechtigt Monitoring-Caller, die Detailfelder von `/health/` (`database`, `virus_scanner`/`clamav`, `encryption_key`, `db_user_is_superuser`/`db_role_posture`, `version`, `smtp`, Backup-Alter, Disk-frei, `stale_jobs`) abzurufen. Ohne Token liefert `/health/` nur `{"status": …}` (siehe [§ 5.1](#51-health-endpoint), #1375). |

> **Migration bestehender Installationen:** Wer bisher genau einen öffentlichen Hostnamen in `ALLOWED_HOSTS` führt — gern zusammen mit `localhost`/`127.0.0.1` für den Healthcheck, muss nichts tun: `EXTERNAL_ORIGIN` wird daraus abgeleitet. Wer mehrere öffentliche Hosts (z. B. mit und ohne `www.`) oder ein Subdomain-Muster einträgt, ergänzt die Variable **vor** dem Update in der `.env`; andernfalls startet die Anwendung nach dem Update nicht mehr und nennt im Log genau diese Variable. Der Wert ist die Adresse, unter der die Einrichtung die Anwendung tatsächlich aufruft — sie steht anschließend in jeder Einladungs-, Passwort-Reset- und Entsperr-Mail.

**Datenbank (PostgreSQL — Drei-Rollen-Modell, #902)**

Seit v0.12 trennt Anlaufstelle drei DB-Rollen: einen hartkodierten `postgres`-Bootstrap-Superuser (legt beim ersten Start nur die App-Rollen an), die **App-Rolle** (`POSTGRES_USER`, `NOSUPERUSER NOBYPASSRLS` — Django-Runtime, RLS-geschützt) und die **Admin-Rolle** (`POSTGRES_ADMIN_USER`, `NOSUPERUSER BYPASSRLS` — Migrationen, Seed, Retention-Pruning). **Breaking-Change für Self-Hoster:** Die drei neuen `.env`-Variablen sind Pflicht.

| Name | Default | Beschreibung |
|---|---|---|
| `POSTGRES_DB` | `anlaufstelle` | Datenbankname. |
| `POSTGRES_USER` | `anlaufstelle` | App-Rolle (Django-Runtime), `NOSUPERUSER NOBYPASSRLS`. |
| `POSTGRES_PASSWORD` | `anlaufstelle` | Passwort der App-Rolle (in Produktion sicher setzen!). |
| `POSTGRES_ADMIN_USER` | `anlaufstelle_admin` | Admin-Rolle für Migrationen/Seed/Retention, `NOSUPERUSER BYPASSRLS`. |
| `POSTGRES_ADMIN_PASSWORD` || Passwort der Admin-Rolle. |
| `POSTGRES_BOOTSTRAP_PASSWORD` || Passwort des `postgres`-Bootstrap-Superusers (nur initiales Rollen-Setup). |
| `POSTGRES_HOST` | `localhost` (via Compose: `db`) | DB-Host. |
| `POSTGRES_PORT` | `5432` | DB-Port. |

> **Tipp:** `python manage.py check_db_roles` verifiziert die Rollen-Topologie zur Laufzeit (Exit `0` = ok, `1` = falsches Attributprofil, `2` = Konfiguration unvollständig). Das [Compliance-Dashboard](#55-compliance-dashboard) nutzt dieselbe Prüfung, [`scripts/initial-setup.sh`](../scripts/initial-setup.sh) führt sie bei der Erstinbetriebnahme aus. Ergänzend prüft der [Health-Endpoint](#51-health-endpoint) fortlaufend die **tatsächlich verwendete** Verbindung (`db_role_posture`) — `check_db_roles` prüft, dass die Rollen mit den richtigen Attributen *existieren*, nicht, dass die Anwendung auch als die App-Rolle verbindet (Refs #793).

**Feldverschlüsselung (MultiFernet-Rotation)**

| Name | Default | Beschreibung |
|---|---|---|
| `ENCRYPTION_KEYS` || Komma-separierte Liste von Fernet-Keys. Der **erste** Key ist Write-Key (neue Daten werden damit verschlüsselt), alle weiteren sind Read-Only (Decrypt-Fallback für Rotation). |
| `ENCRYPTION_KEY` || Legacy-Single-Key (Einzelkey). Mindestens eine der beiden Variablen muss in Produktion gesetzt sein, sonst verweigert die App den Start. |

**Virus-Scan (ClamAV, #524)**

| Name | Default (prod) | Beschreibung |
|---|---|---|
| `CLAMAV_ENABLED` | `true` | Aktiviert den Virenscan vor Upload-Verschlüsselung. Fail-closed: Ist der Daemon nicht erreichbar, wird der Upload abgewiesen. |
| `CLAMAV_HOST` | `clamav` | Hostname des ClamAV-Daemons (Service-Name im Compose-Netzwerk). |
| `CLAMAV_PORT` | `3310` | TCP-Port des clamd-Sockets. |
| `CLAMAV_TIMEOUT` | `15` | Wall-Clock-Timeout in Sekunden pro Scan-Aufruf; MUSS unter `GUNICORN_TIMEOUT` liegen, sonst killt der Worker-Timeout den Request statt fail-closed abzuweisen. |

**Logging**

| Name | Default | Beschreibung |
|---|---|---|
| `LOG_FORMAT` | `text` | `json` aktiviert strukturiertes Logging über [`core.logging.JsonFormatter`](https://github.com/anlaufstelle/app/blob/main/src/core/logging.py) — empfohlen für Produktion mit Log-Aggregation. |

**Sentry (optional)**

| Name | Default | Beschreibung |
|---|---|---|
| `SENTRY_DSN` || Aktiviert das Error-Tracking (opt-in — ohne DSN verlässt keinerlei Telemetrie die Installation). Gesetzt werden `send_default_pii=False` und `include_local_variables=False`; ein serverseitiger Scrubber entfernt Request-Bodies/Cookies/Query-Strings und maskiert sensible Felder, bevor ein Event die Installation verlässt. Mechanismus **und Grenzen** siehe [§ 5.6 Error-Tracking & Datenminimierung](#56-error-tracking--datenminimierung). |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.1` | Sample-Rate für Performance-Traces (0.0–1.0). |

**E-Mail (SMTP, Produktion)**

| Name | Default | Beschreibung |
|---|---|---|
| `EMAIL_HOST` | `localhost` | SMTP-Host für Password-Reset- und Invite-Mails. |
| `EMAIL_PORT` | `587` | SMTP-Port. |
| `EMAIL_HOST_USER` || SMTP-Benutzer. |
| `EMAIL_HOST_PASSWORD` || SMTP-Passwort. |
| `EMAIL_USE_TLS` | `True` | STARTTLS aktivieren. |
| `DEFAULT_FROM_EMAIL` | `noreply@anlaufstelle.app` | Absenderadresse. |

**File-Vault Upload-Limits (#1268)**

Service-Layer-Obergrenzen als Defense-in-Depth (greifen unabhängig von der per-Einrichtung konfigurierbaren `Settings.max_file_size_mb`).

| Name | Default | Beschreibung |
|---|---|---|
| `FILE_VAULT_MAX_UPLOAD_BYTES` | `52428800` (50 MB) | Harte Obergrenze pro Datei, greift vor jeder Voll-Pufferung. |
| `FILE_VAULT_MAX_UPLOAD_FILES` | `20` | Maximale Datei-Anzahl pro File-Feld (Masse-Upload-Bremse). |
| `FILE_VAULT_MAX_IMAGE_PIXELS` | `40000000` | Decompression-Bomb-Schutz: maximale Pixelzahl eines Bild-Uploads (setzt zugleich `PIL.Image.MAX_IMAGE_PIXELS`). |
| `FILE_VAULT_MAX_ARCHIVE_BYTES` | `209715200` (200 MB) | Zip-Bomb-Schutz: maximale Summe der unkomprimierten Einträge (aus dem Zip-Directory gelesen). |
| `FILE_VAULT_MAX_ARCHIVE_RATIO` | `100` | Zip-Bomb-Schutz: maximales Expansions-Verhältnis (unkomprimiert/komprimiert). |
| `FILE_VAULT_MAX_ARCHIVE_ENTRIES` | `10000` | Zip-Bomb-Schutz: maximale Anzahl Einträge (aus dem EOCD-Record gelesen). |

**Wartungsmodus (#700)**

| Name | Default | Beschreibung |
|---|---|---|
| `MAINTENANCE_FLAG_FILE` | — (leer = aus) | Pfad einer Flag-Datei; solange sie existiert, antwortet die App mit HTTP 503. Ohne Wert ist die Middleware No-Op. |
| `MAINTENANCE_ALLOW_IPS` || Komma-separierte IP-Liste für Ops-Zugriff während der Wartung. |
| `MAINTENANCE_CACHE_TTL` | `5` | Caching-Dauer (Sekunden) der Flag-Datei-Prüfung. |
| `MAINTENANCE_RETRY_AFTER` | `600` | Wert des `Retry-After`-Headers (Sekunden) bei aktivem Wartungsmodus. |

**Breach-Detection (#685)**

| Name | Default | Beschreibung |
|---|---|---|
| `BREACH_*` (Variablen-Familie) | siehe Runbook | Zeitfenster und Schwellen der heuristischen Anomalie-Erkennung (fehlgeschlagene Logins, Exporte, Löschungen, Langzeitfenster, verteilte Login-Angriffe) sowie `BREACH_NOTIFICATION_WEBHOOK_URL` (optionaler Eskalations-Webhook; leer = aus). Vollständige Liste samt Defaults: [Ops-Runbook § 6.5b](ops-runbook.md). |

**Sonstiges**

| Name | Default | Beschreibung |
|---|---|---|
| `MEDIA_ROOT` | `<BASE_DIR>/media` | Ablage verschlüsselter Dateianhänge (siehe [§ 2.9](#29-encrypted-file-vault--virus-scanning)). |
| `FILE_UPLOAD_TEMP_DIR` | Django-Default (`tempfile.gettempdir()`) | Verzeichnis für den Klartext-Zwischenpuffer von Uploads > `FILE_UPLOAD_MAX_MEMORY_SIZE`, bevor sie in den File-Vault verschlüsselt werden (siehe [§ 2.9](#29-encrypted-file-vault--virus-scanning)). In den mitgelieferten Compose-Dateien fest auf `/tmp` (tmpfs/RAM) gesetzt — [Ops-Runbook § 5.7a](ops-runbook.md). |
| `BACKUP_DIR` | `/backups` (Compose-Stack), sonst `<Repo>/backups` | Ablageverzeichnis der verschlüsselten DB-Backups (`.sql.gz.enc`) **aus Sicht des Containers**; Health-Endpoint und die Backup-Zeile im Systembereich lesen daraus das Backup-Alter. |
| `BACKUP_HOST_DIR` | `./backups` | Dasselbe Verzeichnis **auf dem Host** — dorthin schreibt `scripts/ops/backup.sh`. `docker-compose.prod.yml` mountet es read-only nach `BACKUP_DIR`. Passen die beiden Werte nicht zusammen, meldet der Systembereich dauerhaft „Kein Backup gefunden“, obwohl der Backup-Cron läuft. |
| `APP_VERSION` | `dev` | Versions-String, den `/health/` im `version`-Detailfeld ausweist (nur für Token-/authentifizierte Caller). |
| `SUDO_MODE_TTL_SECONDS` | `900` | Gültigkeitsdauer (Sekunden) des Re-Auth-Fensters für sensible Aktionen (MFA-Disable, DSGVO-Export). |

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

**Empfohlen — ein Befehl:** Das mitgelieferte Skript [`scripts/initial-setup.sh`](../scripts/initial-setup.sh) führt die komplette Erstinbetriebnahme in der richtigen Reihenfolge aus (Refs #793):

```bash
./scripts/initial-setup.sh
```

Es startet die Datenbank und wartet auf ihre Bereitschaft, legt das Schema per One-Shot-Job an, fährt den vollen Stack hoch und **verifiziert anschließend die Sicherheits-Posture**: `manage.py check --deploy` (Deployment-Settings) und `manage.py check_db_roles` (DB-Rollen und Ownership). Zum Schluss legt es — sofern noch keiner existiert — interaktiv den Super-Admin an (siehe [§ 2.1](#21-erstinstallation-super-admin-anlegen-bootstrap)).

Das Skript ist **idempotent** (ein zweiter Lauf ist gefahrlos: Migrationen sind dann No-ops, ein vorhandener Super-Admin wird übersprungen) und **fail-loud** — bricht ein Schritt ab, endet es mit einem Exit-Code ≠ 0 und einer Erklärung. Optionale Umgebungsvariablen: `COMPOSE_FILE` (Default `docker-compose.prod.yml`), `ADMIN_USERNAME`, `ADMIN_EMAIL`, `WAIT_TIMEOUT`, `SKIP_SUPER_ADMIN=1`. Ein Passwort nimmt es bewusst **nicht** entgegen — das fragt `create_super_admin` interaktiv ab ([ADR-018](adr/018-rollenmodell-superadmin.md)).

> **Warum die Rollen-Verifikation dazugehört:** Läuft die Datenbank-Rolle der Anwendung als PostgreSQL-Superuser, wird Row Level Security **still** umgangen — die Einrichtungs-Isolation fällt aus, ohne dass irgendetwas fehlschlägt. Das Skript prüft das fail-loud; zusätzlich meldet der Health-Endpoint diesen Zustand dauerhaft als `degraded` (siehe [§ 5.1](#51-health-endpoint)).

**Manuell (gleichwertig, für Schritt-für-Schritt-Kontrolle):** Django-Migrationen laufen **nicht** automatisch beim Start des `web`-Dienstes — sie sind ein separater One-Shot-Schritt (Refs #802). Bei der Erstinstallation daher in dieser Reihenfolge:

```bash
# 1. Datenbank (+ ClamAV) zuerst hochfahren
docker compose -f docker-compose.prod.yml up -d db clamav

# 2. Schema einmalig per One-Shot-Job anlegen
docker compose -f docker-compose.prod.yml run --rm \
    --entrypoint=/app/docker-migrate.sh maintenance

# 3. Vollen Stack starten (web + caddy)
docker compose -f docker-compose.prod.yml up -d

# 4. Sicherheits-Posture verifizieren
docker compose -f docker-compose.prod.yml exec web python manage.py check --deploy
docker compose -f docker-compose.prod.yml exec web python manage.py check_db_roles
```

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

Erwartete (anonyme) Antwort — seit #1375 enthält sie **nur** das `status`-Feld:

```json
{"status": "ok"}
```

---

## 2. Erstkonfiguration

### 2.1 Erstinstallation: Super-Admin anlegen (Bootstrap)

**Vor jeder anderen Konfiguration** — direkt nach dem ersten `docker compose up` — wird ein **Super-Admin** (`role=super_admin`, „Systemadministration") angelegt. Diese Rolle ist der einzige facility-übergreifende Account und betreibt die Installation. Sie wird **nicht** über das normale Admin-UI angelegt, sondern über einen dedizierten interaktiven Befehl ohne Default-Passwort:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py create_super_admin
```

> **Hinweis:** Wer [`scripts/initial-setup.sh`](../scripts/initial-setup.sh) genutzt hat ([§ 1, Schritt 4](#schritt-4-stack-starten)), hat diesen Schritt bereits erledigt — das Skript ruft denselben Befehl am Ende auf und überspringt ihn, falls schon ein Super-Admin existiert.

Das Skript fragt interaktiv nach:

1. **Benutzername** (frei wählbar — Empfehlung: nicht `admin`)
2. **E-Mail-Adresse** (Pflicht — wird für Recovery-Mails genutzt)
3. **Passwort** (zweimal eingeben — keinen Default, keinen Demo-Wert)
4. **Anzeigename**

Nach erfolgreichem Abschluss existiert genau ein User mit Rolle `super_admin`. Mit diesem Account melden Sie sich anschließend unter `/login/` an und legen die erste Einrichtung und den ersten `facility_admin` an — im Django-Admin (`/admin-mgmt/`, **Core → Einrichtungen**) oder mit dem Setup-Skript aus [§ 2.2](#22-einrichtung-und-anwendungsbetreuung-anlegen). Der `/system/`-Bereich zeigt Organisation und Einrichtungen nur an; angelegt wird dort nichts.

> **Wichtig — kein Default-Passwort:** Aus DSGVO- und Sicherheits-Gründen liefert die Anwendung **kein** Default-Passwort und keinen Default-Benutzernamen aus (anders als der frühere `make seed`-Workflow für Entwicklungs-Demos). Wer den Befehl überspringt, erhält **keinen** anmeldungsfähigen Account.

> **Hinweis:** Der Befehl ist idempotent in dem Sinn, dass ein bestehender `super_admin` nicht überschrieben wird. Soll ein zweiter Super-Admin angelegt werden, wird der Befehl erneut ausgeführt — bestehende Accounts bleiben unangetastet.

#### Account-Sperre aufheben (CLI)

Wird ein `super_admin` durch fehlgeschlagene Logins gesperrt (Standard: 10 Versuche, siehe [§ 2.7 Account-Lockout](#account-lockout)) und es gibt **keinen** zweiten Super-Admin, kann die Sperre nur per CLI aufgehoben werden — kein anderer User hat Zugriff auf das Bootstrap-Tool:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py unlock <username>
```

Der Befehl entsperrt den angegebenen User, schreibt einen `LOGIN_UNLOCK`-AuditLog-Eintrag und ist auf alle Rollen anwendbar (auch `facility_admin`, `lead`, `staff`, `assistant`). Für facility-gebundene User bleibt zusätzlich der UI-Pfad über die Anwendungsbetreuung möglich (siehe [§ 2.7 Account-Lockout](#account-lockout)).

### 2.2 Einrichtung und Anwendungsbetreuung anlegen

Nach dem Login als `super_admin` legen Sie die erste Einrichtung und einen `facility_admin` (Anwendungsbetreuung) für sie im Django-Admin an (`/admin-mgmt/`, **Core → Einrichtungen** bzw. **Core → Benutzer**). Alternativ steht das interaktive Setup-Skript zur Verfügung — geeignet für reine Single-Tenant-Installationen, in denen Systemadministration und Anwendungsbetreuung dieselbe Person sind:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance setup_facility
```

> ⚠️ **Rollen-Voraussetzung (#1883).** Der Befehl schreibt in Tabellen mit Row-Level-Security (Einstellungen, Dokumentationstypen, Feldvorlagen). Die Laufzeit-Rolle des `web`-Containers ist `NOBYPASSRLS` und hat außerhalb eines Web-Requests keinen Einrichtungs-Kontext; sie erhaelt bewusst keine Admin-Secrets. Der opt-in Service `maintenance` verbindet als BYPASSRLS-Rolle. Der Befehl prüft das vorab und bricht mit einer klaren Meldung ab, **bevor** die erste Eingabeaufforderung erscheint. Hintergrund: [Ops-Runbook § 9 DB-Rollenmodell](ops-runbook.md). Bei einer lokalen Entwicklungs-Datenbank, deren Rolle ohnehin Superuser ist, ist der Maintenance-Pfad überflüssig.

Das Skript fragt interaktiv nach:

1. **Name der Organisation** – z. B. `Diakonie Musterstadt e.V.`
2. **Name der Einrichtung** – z. B. `Beratungsstelle Nord`
3. **Benutzername der Anwendungsbetreuung** (`facility_admin`)
4. **Passwort der Anwendungsbetreuung** (zweimal eingeben)

Anschließend sind Organisation, Einrichtung, Standardeinstellungen, der **Standardkatalog der Dokumentationstypen**, die drei **Standardschichten** (Frühdienst, Spätdienst, Nachtdienst), zwei **Schnellvorlagen** am Typ „Kontakt“ und der erste `facility_admin`-Benutzer angelegt. Die Einrichtung ist damit sofort benutzbar: Ohne mindestens einen Dokumentationstyp bliebe die Typ-Auswahl beim Dokumentieren leer und es ließe sich kein einziger Kontakt erfassen.

> **Alles-oder-nichts:** Der Lauf ist eine einzige Datenbank-Transaktion. Bricht er ab (falsche DB-Rolle, unterbrochene Verbindung, Konflikt beim Katalog), bleibt **nichts** halb Angelegtes zurück — auch der Admin-Benutzer nicht. Der Befehl kann nach Behebung unverändert wiederholt werden.

> **Wiederholungslauf lässt einen vorhandenen Katalog in Ruhe:** Trägt die Einrichtung bereits Dokumentationstypen oder Feldvorlagen (zweiter Lauf, im Django-Admin angelegte Einrichtung), spielt der Befehl den Standardkatalog **nicht** ein. Im Admin gepflegte Namen, Sortierungen, Farben, Symbole und Sichtbarkeiten bleiben damit unverändert; der Befehl sagt das am Ende und nennt `load_package --default` als den Weg, den Auslieferungsstand bewusst nachzuziehen.

> **Opt-out `--no-default-package`:** Wer den Standardkatalog nicht will (eigenes Konfigurationspaket, komplett eigene Typen), hängt `--no-default-package` an. Die Einrichtung startet dann **ohne** Dokumentationstypen und ist erst dokumentierbar, nachdem ein Paket geladen wurde (siehe [docs/packages.md](packages.md)). Der Befehl weist am Ende ausdrücklich darauf hin.

> **Bereits bestehende Einrichtung nachrüsten:** Für eine Einrichtung, die es schon gibt — im Django-Admin unter **Core → Einrichtungen** angelegt oder mit `--no-default-package` bewusst leer gestartet — spielt derselbe Standardkatalog nachträglich ein:
>
> ```bash
> docker compose -f docker-compose.prod.yml run --rm maintenance \
>   load_package --default --facility <uuid> --dry-run
> ```
>
> `--dry-run` zeigt erst nur an, was passieren würde; ohne den Schalter wird angewendet. Der Lauf löscht nie etwas, **schreibt aber die Katalog-Eigenschaften bestehender Typen und Feldvorlagen auf den Auslieferungsstand zurück** (Name, Sortierung, Farbe, Symbol, Sichtbarkeit) — anders als die Ersteinrichtung, die einen vorhandenen Katalog gar nicht anfasst. Deshalb zuerst mit `--dry-run` ansehen; die vollständige Rücksync-Tabelle steht in [docs/packages.md § 4](packages.md). Die UUID der Einrichtung steht im Django-Admin unter **Core → Einrichtungen** in der Adresszeile der Detailseite.

> **Hinweis:** Sollte eine Organisation oder Einrichtung mit dem angegebenen Namen bereits existieren, wird sie wiederverwendet. Ein bestehender Benutzername wird nicht überschrieben.

> **Zweite Einrichtung im Django-Admin (#1970):** Weitere Einrichtungen legen Sie im laufenden Betrieb unter **Core → Einrichtungen → Einrichtung hinzufügen** an — das ist der einzige Weg an der Oberfläche und `super_admin` vorbehalten. Das Speichern erzeugt **dieselbe Grundausstattung** wie `setup_facility`: Einstellungssatz, Standardkatalog der Dokumentationstypen, Standardschichten und Schnellvorlagen. Die neue Einrichtung ist damit sofort dokumentierbar; zu tun bleibt nur noch, ihr unter **Core → Benutzer** mindestens ein Konto zuzuordnen. Der Startbestand entsteht **genau einmal, beim Anlegen**: Ein späteres Speichern derselben Einrichtung (z. B. beim Umbenennen oder beim Umschalten von „Aktiv“) rührt ihren Bestand nicht an — auch dann nicht, wenn er leer ist. Das ist Absicht, denn leer kann eine Entscheidung sein: Eine per `--no-default-package` bewusst katalogfrei eingerichtete Einrichtung bekäme sonst beim ersten Umbenennen still den kompletten Standardkatalog. Vor dieser Version im Admin angelegte, leer gebliebene Einrichtungen rüsten Sie deshalb ausdrücklich nach: den Katalog mit `manage.py load_package --default --facility <uuid>` (Befehl samt Rollen-Override oben), Zeitfenster und Schnellvorlagen von Hand unter **Core → Zeitfenster** bzw. **Core → Quick-Templates**.

> **Organization als Branding-Hülse:** Die Organisation dient nur dem Träger-Branding (Logo, Name in Berichten). Es gibt keinen Org-Admin und keinen Cross-Facility-Effekt durch sie — facility-übergreifender Zugriff läuft ausschließlich über `super_admin` und `/system/`. Architektur-Entscheidung: [ADR-018](adr/018-rollenmodell-superadmin.md).

### 2.2b Django-Adminoberfläche

Die Adminoberfläche ist erreichbar unter:

```
https://anlaufstelle.meine-einrichtung.de/admin-mgmt/
```

Hier können Sie verwalten:

| Bereich | Pfad im Admin | Beschreibung |
|---|---|---|
| Organisationen | Core → Organisationen | Träger (Mandantenebene) |
| Einrichtungen | Core → Einrichtungen | Standorte einer Organisation |
| Einstellungen | Core → Einstellungen | Konfiguration pro Einrichtung |
| Benutzer | Core → Benutzer | Technischer Fallback; primäre Pflege unter Verwaltung → Zugänge |
| Dokumentationstypen | Core → Dokumentationstypen | Technischer Fallback; primäre Pflege unter Verwaltung → Dokumentationstypen |
| Zeitfenster | Core → Zeitfenster | Benannte Zeiträume für Auswertungen |
| Audit-Log | Core → Audit-Logs | Unveränderliches Protokoll |

### 2.3 Einstellungen pro Einrichtung

Im Admin unter **Core → Einstellungen** können Sie für jede Einrichtung konfigurieren:

| Feld | Standardwert | Beschreibung |
|---|---|---|
| Vollständiger Name || Wird in Berichten angezeigt |
| Standard-Dokumentationstyp || Vorausgewählter Typ bei neuem Eintrag |
| Session-Timeout (Minuten) | 30 | Automatischer Logout nach Inaktivität |
| Maximale Sitzungsdauer (Stunden) | 12 | Obergrenze ab der Anmeldung, unabhängig von der Aktivität: Nach dieser Zeit endet die Sitzung in jedem Fall und es ist eine neue Anmeldung nötig. Zulässig 1–168; muss **größer** sein als das Session-Timeout (in Minuten gerechnet), sonst lehnt das Formular die Eingabe ab. Eine Änderung greift erst beim **nächsten** Login — bereits laufende Sitzungen behalten ihre beim Anmelden festgelegte Frist |
| Aufbewahrung anonym (Tage) | 90 | Löschfrist für anonyme Kontakte |
| Aufbewahrung identifiziert (Tage) | 365 | Löschfrist für Kontakte zu Personen der Stufe „Nur Pseudonym" |
| Aufbewahrung qualifiziert (Tage) | 3650 | Löschfrist nach Fallabschluss (Stufe „Mit Zusatzangaben") |
| 2FA einrichtungsweit verpflichtend | false | Erzwingt TOTP-2FA für **alle** Benutzer dieser Einrichtung (siehe [§ 2.7](#27-zwei-faktor-authentifizierung-2fa)) |

> **Grundausstattung einer neuen Einrichtung (#1970).** Neben dem Einstellungssatz entsteht beim Anlegen einer Einrichtung — im Django-Admin wie über `setup_facility` — ein knapper Startbestand, den Sie anschließend frei überschreiben können:
>
> | Bestand | Pflege im Admin | Wirkung, wenn er fehlt |
> |---|---|---|
> | Standardkatalog der Dokumentationstypen | Verwaltung → Dokumentationstypen | **Blockierend:** Ohne Ereignistyp bleibt die Typ-Auswahl in „Neuer Eintrag" leer, es lässt sich nichts dokumentieren |
> | Standardschichten Frühdienst (08–16), Spätdienst (16–22), Nachtdienst (22–08) | Core → Zeitfenster | Komfort: Der Zeitstrom wertet dann den vollen Tag aus statt ein Schichtfenster; Übergabe-Ansicht und Live-Zähler haben keinen Schichtbezug |
> | Schnellvorlagen „Kurzkontakt" und „Beratungskontakt" am Typ „Kontakt" | Core → Quick-Templates | Komfort: Wiederkehrende Kontakte werden von Hand ausgefüllt (siehe [§ 2.8](#28-schnell-vorlagen-quick-templates)) |
>
> **Einmalig beim Anlegen, nicht bei jedem Speichern:** Bringt eine Einrichtung im Moment des Anlegens bereits eigene Dokumentationstypen, Zeitfenster oder Schnellvorlagen mit, bleibt die jeweilige Gruppe **unangetastet** — geprüft wird je Gruppe, ein einziges vorhandenes Objekt genügt. Jedes spätere Speichern der Einrichtung im Admin lässt alle drei Gruppen ohnehin unberührt: Im Admin gepflegte Bezeichnungen und Sortierungen werden nie still auf den Auslieferungsstand zurückgesetzt, und gelöschte Standardschichten kommen nicht von selbst zurück. Nachziehen ist ausdrücklich: den Auslieferungsstand des Katalogs holt `manage.py load_package --default` (siehe [§ 2.2](#22-einrichtung-und-anwendungsbetreuung-anlegen)); Zeitfenster und Schnellvorlagen legen Sie unter **Core → Zeitfenster** bzw. **Core → Quick-Templates** selbst an. Einzige Ausnahme: Ein erneuter Lauf der Ersteinrichtung `setup_facility` mit **denselben** Namen für Organisation und Einrichtung stattet aus, was zu diesem Zeitpunkt leer ist — wer alle Zeitfenster gelöscht hat, bekommt dort die drei Standardschichten wieder; der Befehl schreibt zu jedem Bestand hin, ob er ihn angelegt oder unberührt gelassen hat.

> **Jede Einrichtung hat genau einen Einstellungssatz.** Die Zeile entsteht automatisch, sobald die Einrichtung angelegt wird — beim interaktiven `setup_facility`, beim Anlegen im Admin unter **Core → Einrichtungen** und (nachträglich, für Altbestände) bei jedem `migrate`-Lauf. Das ist keine Formsache: `enforce_retention` und `create_statistics_snapshots` **überspringen** eine Einrichtung ohne Einstellungssatz — für sie würde also keine Aufbewahrungsfrist durchgesetzt und es entstünden keine Statistik-Snapshots. Beide Jobs protokollieren einen solchen Fall auf `WARNING`-Ebene (`No settings for …, skipping.`); taucht die Meldung im Log auf, ist das ein Betriebsfehler und kein Rauschen.

### 2.4 Weitere Benutzer anlegen

Der reguläre Weg für die Anwendungsbetreuung ist **Verwaltung → Zugänge → Zugang anlegen** (`/verwaltung/zugaenge/`). Anlage und jede Änderung verlangen eine erneute Bestätigung im Sudo-Modus. Die Liste und alle Aktionen sind auf die eigene Einrichtung begrenzt; die Rolle `super_admin` kann dort weder gesehen noch vergeben werden ([ADR-048](adr/048-fachhoheit-zugaenge-hub.md)).

- **Benutzername:** Anmeldename (keine Klarnamen)
- **E-Mail:** Pflicht für den Invite-Flow (siehe unten)
- **Anzeigename:** Name für die Benutzeroberfläche
- **Rolle:** Eine der vier einrichtungsgebundenen Rollen (siehe unten)
- **MFA erforderlich:** erzwingt einen zweiten Faktor für dieses einzelne Konto
- **Löschbestätigung:** nimmt das Konto in den Genehmiger-Pool für Löschanträge auf

Bestehende Zugänge lassen sich dort bearbeiten, deaktivieren oder reaktivieren. Der eigene Administrationszugang kann weder deaktiviert noch herabgestuft werden. Die Fach-UI löscht Konten nicht endgültig: Deaktivieren ist der reguläre Lebenszyklus; eine endgültige Löschung bleibt ein geprüfter technischer Sonderfall. `super_admin`, Bootstrap und Notfälle verbleiben unter **Core → Benutzer** im Django-Admin. Quick-Templates und Zeitfenster bleiben ebenfalls bewusst technische Admin-Funktionen.

#### Token-Invite-Flow (Refs #528)

Neue Konten werden **ohne** Klartext-Passwort angelegt. Stattdessen versendet die Anwendung eine **Einladungs-E-Mail** mit einem personalisierten Setup-Link (`/invite/<uid>/<token>/`) an die hinterlegte E-Mail-Adresse. Der Link führt den neuen Nutzer auf dasselbe Passwort-Setzen-Formular wie der Passwort-Reset, wo er eigenhändig ein Passwort setzt.

1. Die Anwendungsbetreuung legt den Zugang mit E-Mail unter **Verwaltung → Zugänge** an.
2. System ruft [`send_invite_email`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/invite.py) auf und generiert einen Setup-Token (eigener `invite_token_generator`, basierend auf `uidb64` + Token-Hash).
3. Nutzer empfängt Mail, klickt Link und setzt sein Passwort. Ein automatischer Login findet dabei **nicht** statt — die Bestätigungsseite führt über „Zur Anmeldung“ auf die Anmeldemaske, wo er sich erstmals mit dem neuen Passwort anmeldet.

**Token-Gültigkeit (L4, Refs #1375):** Einladungs- und Passwort-Reset-Tokens haben **getrennte** Gültigkeiten:

- **Einladungen** laufen nach `INVITE_TOKEN_TIMEOUT` ab (Default **3 Tage** = 259200 s, per ENV `DJANGO_INVITE_TOKEN_TIMEOUT` anpassbar) — ein neu eingeladener Mitarbeitender braucht realistisch Tage, um den Link zu öffnen.
- **Passwort-Resets** laufen nach `PASSWORD_RESET_TIMEOUT` ab (Default **2 Stunden**, früher Django-Default 3 Tage; per ENV `DJANGO_PASSWORD_RESET_TIMEOUT` anpassbar) — ein Reset-Link ist ein starkes Recovery-Primitiv und soll kurzlebig sein.

Beide Generatoren sind zudem über ein eigenes `key_salt` getrennt (ein Reset-Token gilt nicht auf `/invite/` und umgekehrt) und invalidieren den Token automatisch, sobald der Nutzer sein erstes Passwort gesetzt hat.

**Setup-Link erneut senden:** Ist die Mail nicht angekommen oder der Token abgelaufen, kann die Anwendungsbetreuung in der Zugangs-Detailansicht über **Einladung erneut senden** einen neuen Token ausstellen und eine frische Mail verschicken lassen. Der Button erscheint nur, solange die Einrichtung des Kontos aussteht.

**Technischer Legacy-Fallback ohne E-Mail:** Die Fach-UI verlangt immer eine E-Mail-Adresse. Nur der Django-Admin kann zur Abwärtskompatibilität noch einen Nutzer **ohne** E-Mail-Adresse anlegen und zeigt dann einmalig ein Klartext-Initialpasswort. Dieser Weg ist **unsicher** und sollte nur als Notlösung verwendet werden — besser: E-Mail nachtragen und Einladung neu versenden.

> **Hinweis:** `must_change_password` wird bei **jeder** Neuanlage im Admin gesetzt. Schließt der Nutzer den Setup-Schritt der Einladung ab — oder später einen Passwort-Reset, wird das Flag dabei automatisch wieder gelöscht (#1969); er hat sein Passwort in diesem Schritt ja selbst gewählt und muss es beim ersten Login nicht sofort erneut ändern. Erzwungen bleibt der Wechsel damit genau dort, wo er gebraucht wird: beim Fallback ohne E-Mail-Adresse, bei dem der Admin das Initialpasswort kennt.

#### Rollenbeschreibung

> **Zielgruppe dieses Abschnitts:** die **Bootstrap-/Verwaltungssicht** — welche Rolle Sie beim Anlegen von Benutzern vergeben und welche Bootstrap-Rechte daran hängen. Die fachliche Begründung steht im [Fachkonzept (Role — Die Rolle)](fachkonzept-anlaufstelle.md#role--die-rolle), die praktische „Was darf wer?"-Sicht im [User-Guide § 9](user-guide.md#9-rollen-und-berechtigungen), die technischen Details (DB-Werte, RLS-Scoping) in der [FAQ § C](faq.md#die-fünf-rollen).

Anlaufstelle kennt fünf Rollen — vier facility-gebunden, eine facility-übergreifend:

| Rolle | Bezeichnung | Scope | Berechtigungen |
|---|---|---|---|
| `super_admin` | Systemadministration | facility-übergreifend (`/system/`) | Bootstrap, Einrichtungen anlegen, Pre-Auth-AuditLogs, Backup-Einsicht. Wird über `manage.py create_super_admin` angelegt — **nicht** über das Admin-UI. Jeder Aufruf einer `/system/`-View erzeugt einen `SYSTEM_VIEW`-AuditLog-Eintrag. |
| `facility_admin` | Anwendungsbetreuung | eine Einrichtung | Vollzugriff in der eigenen Einrichtung: Benutzerverwaltung, Typ-/Sensitivitätskonfiguration, Audit-Log (`/audit/`), DSGVO-Paket. Kann **nicht** in andere Einrichtungen schauen. |
| `lead` | Leitung | eine Einrichtung | Auswertungen, Löschanträge genehmigen, alle Fälle der Einrichtung einsehen, Datenexport |
| `staff` | Fachkraft | eine Einrichtung | Kontakte, Fälle, Dokumentation erfassen und bearbeiten |
| `assistant` | Assistenz | eine Einrichtung | Eingeschränkte Erfassung, kein Zugriff auf Personendaten der Stufe „Mit Zusatzangaben" |

> **Trennung System ↔ Anwendung:** `super_admin` betreut Hosting und Bootstrap, `facility_admin` betreut Konfiguration und Nutzer **innerhalb** einer Einrichtung. Diese Trennung ist DSGVO-relevant: Der Super-Admin sieht keine fachlichen Inhalte einer Einrichtung, ohne dass dies im AuditLog (`SYSTEM_VIEW`) auftaucht. Architektur-Entscheidung: [ADR-018](adr/018-rollenmodell-superadmin.md).

### 2.5 Dokumentationstypen konfigurieren

> **Ausgangslage:** Eine über `setup_facility` **oder** im Django-Admin unter **Core → Einrichtungen** ([§ 2.2](#22-einrichtung-und-anwendungsbetreuung-anlegen)) angelegte Einrichtung bringt den mitgelieferten **Standardkatalog** bereits mit — sie ist ohne weiteren Schritt dokumentierbar. Leer ist die Liste nur, wenn der Katalog bei der Ersteinrichtung per `--no-default-package` abgewählt wurde oder die Einrichtung aus einer Version vor #1970 stammt. Beides bleibt so, bis Sie es ausdrücklich ändern — ein Speichern der Einrichtung im Admin holt den Katalog **nicht** nach (das würde die Abwahl still übergehen). Führen Sie dann `manage.py load_package --default --facility <uuid>` aus (Befehl samt Rollen-Override in [§ 2.2](#22-einrichtung-und-anwendungsbetreuung-anlegen)). Aufbau, Feld-Referenz und Pflege-Regeln eines Konfigurationspakets — auch eines eigenen statt des mitgelieferten — beschreibt [docs/packages.md](packages.md). Die folgenden Abschnitte beschreiben, wie Sie den Bestand danach anpassen.

Unter **Verwaltung → Dokumentationstypen** (`/config/document-types/`) legen Sie fest, welche Arten von Dokumentation in der Einrichtung erfasst werden können. Die gleichnamige Ansicht unter **Core → Dokumentationstypen** im Django-Admin bleibt als technischer Fallback erhalten, ist aber nicht der reguläre Pflegeweg. Jeder Typ besteht aus:

- **Name und Beschreibung**
- **Feldvorlagen** (Freitextfelder, Auswahlfelder etc.)
- **Löschfrist** in Tagen (überschreibt die globale Einrichtungseinstellung, falls gesetzt)

> **Altersgruppen sind keine Feldvorlagen-Einstellung.** Die Altersgruppe an einer Person verwendet systemweit genau **Unter 18, 18–26, 27+ und Unbekannt**. Weder diese Verwaltungsansicht noch ein Konfigurationspaket kann die Grenzen oder Labels pro Einrichtung ändern. Das gleichnamige Auswahl-Zusatzfeld `alterscluster` des Standardpakets gehört dagegen zu einzelnen Ereignissen (auch anonymen Kontakten); seine Optionen ändern die Personen-Altersgruppe und die eingebauten Altersgruppen-Statistiken **nicht**. Hintergrund und Bedingungen für eine spätere Neubewertung: [ADR-047](adr/047-altersgruppen-systemweit-fix.md).

#### Eindeutigkeit des Namens

Das Paar `(Einrichtung, Name)` ist eindeutig: Pro Einrichtung kann jeder Dokumentationstyp-Name nur **einmal** vergeben werden. Der Versuch, einen zweiten Typ mit demselben Namen anzulegen, wird als Formularfehler abgewiesen.

> **Geändert 2026-08-04 (#1749, [ADR-040](adr/040-documenttype-category-entfernt.md)):** Das Feld **Kategorie** (Kontakt/Leistung/Verwaltung/Notiz) ist **entfallen** — samt Formularfeld, Listen-Filter und den „Kategorie"-Spalten in Statistik-Dashboard und Berichts-PDF. Es war eine reine Anzeige-Gruppierung ohne Wirkung auf Export oder Bann-/Krisen-Logik; dafür war und ist der **Systemtyp** maßgeblich (siehe unten). Mit der Kategorie ist auch ihre Rolle im Eindeutigkeits-Schlüssel entfallen: Bisher war derselbe Name in zwei Kategorien erlaubt (z. B. „Notiz" als Verwaltung *und* als Notiz), jetzt nicht mehr. Beim Update prüft die Migration das vorab und **bricht ab**, wenn eine Einrichtung solche Doppelnamen führt — benennen Sie sie vorher um; ein automatisches Zusammenführen wäre ein Datenentscheid, den nur Sie treffen können.
>
> In **Konfigurationspaketen** (YAML) darf der Schlüssel `category` stehen bleiben: Pakete bleiben gültig und ladbar, der Wert wird aber ignoriert und der Ladebericht weist ihn als Warnung aus. Siehe [docs/packages.md](packages.md).

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

> **Geändert 2026-08-12 (#1849, [ADR-042](adr/042-report-key-berichtszuordnung.md)):** Die **Berichtszuordnung hat ein eigenes Feld** bekommen — den **Berichtsschlüssel** (siehe nächster Abschnitt). Der Systemtyp steuert nur noch Anwendungslogik. Wer bisher einen Typ über den Systemtyp in den Beispiel-Sachbericht gebracht hat, findet ihn dort unverändert: das Update hat allen Bestandstypen den passenden Berichtsschlüssel gegeben. **Der hartkodierte Slug-Zweitschlüssel ist entfallen** — ein Typ ohne Berichtsschlüssel erscheint in keinem Bericht, auch wenn sein Slug einer der Paket-Slugs ist.

Der Systemtyp hat **eine** Funktion: **UI-Logik**. Entsprechend gibt es genau zwei Werte:

| Systemtyp | Auswirkung |
|-----------|------------|
| **Hausverbot** | Aktiviert Hausverbot-Banner auf der Personenseite, eigener Filter im Zeitstrom, Zählung und Highlight in der Übergabe; speist die Beispiel-Hausverbots-Übersicht |
| **Krisengespräch** | Wird als Highlight in der Übergabe angezeigt (letzte Krisen-Events) |

> **Hinweis:** Nicht jeder Dokumentationstyp benötigt einen Systemtyp — die meisten kommen ohne aus. Ob ein Typ in einem Bericht landet, sagt Ihnen die Auskunft „Erscheint in …" in Detail- und Formularansicht; der Systemtyp hat damit nichts zu tun.

> **Geändert 2026-08-12 (#1850, [ADR-042](adr/042-report-key-berichtszuordnung.md)):** Die Auswahl hatte bis zu diesem Update **neun** Werte; die übrigen sieben (Kontakt, Medizinische Versorgung, Spritzentausch, Begleitung, Beratungsgespräch, Vermittlung, Notiz) waren reine Berichts-Zuordnungen und sind seit dem Berichtsschlüssel wirkungslos. Sie sind jetzt **entfernt**. An Bestandstypen wurde der Systemtyp dabei geleert — die Berichtszuordnung steht seit dem vorigen Update im Berichtsschlüssel und bleibt unverändert; **an den Zahlen Ihrer Berichte ändert sich nichts.** Was Sie sehen: Im Formular stehen nur noch „Hausverbot" und „Krisengespräch" zur Wahl, und in der Detailansicht eines betroffenen Typs steht beim Systemtyp jetzt „–".

> **Die Werteliste ist eingefroren** ([RFC #1748](https://github.com/anlaufstelle/app/issues/1748), Entscheid 2026-08-03). Es kommen **keine neuen Systemtypen** hinzu: Ein Wert gehört nur dann hierher, wenn er Anwendungsverhalten steuert. Berichts-Zuordnungen laufen über den Berichtsschlüssel.

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
| **Gehört ausdrücklich in keinen Bericht** | *(ausgeschlossen — und **kein** Mangel)* |
| *(leer)* | *(erscheint in keinem Bericht — und wird als Lücke gemeldet)* |

> **Der Berichtsschlüssel wirkt nicht auf die Übersicht.** Kennzahlen, Diagramme und Tabellen des Reiters „Übersicht" (Kontakte im Zeitverlauf, Dokumentationstypen, Altersgruppen) zählen die Ereignisse des Zeitraums **unabhängig vom Berichtsschlüssel** — auch solche, die kein Bericht auswertet. Ein Typ mit „Gehört ausdrücklich in keinen Bericht" fällt deshalb aus den Berichten heraus, die über den Berichtsschlüssel zuordnen (etwa dem Beispiel-Sachbericht), bleibt in der Übersicht aber sichtbar und zählt dort weiter mit. Auch der Reiter „Externer Bericht" kennt den Berichtsschlüssel nicht.
>
> **Zwei Dinge gehören dazu.** Erstens binden Berichte über **Anwendungsverhalten** weiterhin über den Systemtyp statt über den Berichtsschlüssel: Die Beispiel-Hausverbots-Übersicht zeigt Hausverbots-Ereignisse also auch dann, wenn der Typ „Gehört ausdrücklich in keinen Bericht" trägt — im mitgelieferten Standardkatalog ist genau das der Fall ([ADR-042](adr/042-report-key-berichtszuordnung.md), *Vollzug E4*). Zweitens liest die Übersicht für abgeschlossene Monate in der Regel aus den gespeicherten Monats-Auswertungen, während die Datensatz-Berichte je nach Bericht live rechnen; auch das kann Zahlen auseinanderlaufen lassen ([User-Guide § 7](user-guide.md#7-statistik-und-export)).
>
> Übersicht und Bericht dürfen im selben Zeitraum deshalb verschiedene Zahlen zeigen, ohne dass etwas falsch konfiguriert ist: Die Übersicht beantwortet „Was ist passiert?", die Berichte beantworten „Was geht in welchen Bericht?". Wollen Sie die Übersicht auf einen einzelnen Typ einschränken, nutzen Sie das Dropdown „Alle Dokumentationstypen" über den Diagrammen.

Der Beispiel-Sachbericht ist ein anpassbares Beispiel bzw. eine Vorlage — kein verbindlicher oder aktueller Behördennachweis.

> **Leer und „ausdrücklich ausgeschlossen" sind zwei verschiedene Dinge.** Der Bericht weist unter „Berichte" aus, wie viele Ereignisse er *nicht* zeigt. Gezählt werden dort **nur** Typen, die gar keine Aussage treffen (Schlüssel leer) — ein Typ mit „Gehört ausdrücklich in keinen Bericht" ist eine vollständige Aussage und erscheint **nicht** im Hinweis (#1819). Bis zum Update zählten Hausverbote und Notizen mit; eine Einrichtung, die Hausverbote dokumentiert, sah den Hinweis dauerhaft, ohne dass etwas falsch konfiguriert war.
>
> Einen **dritten** Fall gibt es seither ebenfalls: Trägt ein Typ einen Schlüssel, den kein Bericht (mehr) kennt — etwa aus einem fremden Konfigurationspaket oder nach einem Tippfehler, meldet der Bericht das gesondert. „Ich habe zugeordnet, es passiert nur nichts" ist ein anderer Befund als „ich habe nichts gesagt".

> **Nachtragen ja, ändern nein.** Ein leerer Berichtsschlüssel kann **jederzeit nachgetragen** werden — das ist der Reparaturweg, wenn ein Typ versehentlich in keinem Bericht auftauchte. Ein **gesetzter** Schlüssel ist danach fest (auch „Gehört ausdrücklich in keinen Bericht"): Berichte über vergangene Zeiträume sollen nicht rückwirkend andere Zahlen liefern. Das Formular weist beim Nachtragen darauf hin, dass der Schlüssel auf künftig erzeugte Berichte über **alle** Zeiträume wirkt.
>
> Beim **Anlegen** schlägt das Formular passende Werte vor: „Krisengespräch" als Systemtyp schlägt den Schlüssel „Krisengespräch" vor, „Hausverbot" schlägt „Gehört ausdrücklich in keinen Bericht" vor. Beides ist ein Vorschlag, kein Zwang — Systemtyp und Berichtsschlüssel dürfen bewusst auseinanderlaufen.

> **Falsch gesetzt? Der Weg führt über einen neuen Typ.** Ein gesetzter Berichtsschlüssel lässt sich nicht korrigieren — weder im Formular noch im Django-Admin noch über ein Konfigurationspaket. Wurde versehentlich der falsche Wert gewählt, legen Sie einen **neuen** Dokumentationstyp mit dem richtigen Schlüssel an und setzen den alten über das Feld **„Aktiv"** auf inaktiv. Beachten Sie dabei die Eindeutigkeit des Namens (siehe „Eindeutigkeit des Namens" weiter oben): Sie gilt auch gegenüber **inaktiven** Typen, der neue Typ braucht also einen anderen Namen — oder Sie benennen den alten zuvor um. Der alte Typ steht dann für neue Ereignisse nicht mehr zur Wahl; bereits erfasste Ereignisse bleiben unverändert bei ihm — und damit in der Berichtszeile, in der sie bisher gezählt wurden. Denken Sie zusätzlich an die **Schnell-Vorlagen** des alten Typs (§ 2.8): Sie verschwinden mit der Deaktivierung des Typs **nicht** von der Erfassungsseite und sind eigens zu deaktivieren. **Löschen ist kein Ersatz:** Ein Typ, zu dem Ereignisse existieren, lässt sich nicht löschen. Rechnen Sie damit, dass ein Berichtszeitraum, der über den Umstellungstag hinweggeht, beide Typen enthält — planen Sie den Wechsel deshalb möglichst auf eine Zeitraumgrenze.
>
> **Interim:** Diese Schritte sind heute von Hand auszuführen. Ein geführter Ablauf dafür ist als Vorschlag erfasst (#1888); an der Einmaligkeit des Schlüssels ändert er nichts.

**Berichtsschlüssel an Feldvorlagen.** Berichte, die **Feldwerte** auswerten (Minutensummen, Spritzensaldo, Verteilung der Auswahlfelder), brauchen zusätzlich zu wissen, **welches Feld** sie lesen sollen. Bis zum Update ging das nur über den Feld-Slug: Wer sein Zahlenfeld „Gesprächsdauer" statt „Dauer" nannte, bekam einen Bericht, der dauerhaft null Minuten auswies — ohne jede Rückmeldung. Seit dem Update trägt die Feldvorlage ihren eigenen Berichtsschlüssel (z. B. „Minuten"), und der Name ist frei wählbar. Das frühere Freitextfeld **„Statistik-Kategorie"** an der Feldvorlage ist im selben Zug entfallen: Es wurde von keiner Auswertung gelesen.

> **Je Dokumentationstyp höchstens eine Feldvorlage je Berichtsschlüssel.** Zwei Felder mit dem Schlüssel „Minuten" am selben Typ hießen doppelt gezählte Minuten. Die Zuordnung wird deshalb abgewiesen — ebenso das nachträgliche Setzen eines Schlüssels, der an einem gemeinsamen Typ schon vergeben ist; entsteht die Doppelung trotzdem (z. B. über ein Konfigurationspaket), zählt der Bericht **keines** der beiden Felder und weist das aus. Dieselbe Feldvorlage an **verschiedenen** Typen ist unproblematisch, ebenso zwei gleich benannte Felder mit **verschiedenen** Schlüsseln.
>
> **Bewusste Lücke:** Trägt eine **Feldvorlage** einen Schlüssel, den kein Bericht kennt, sagt der Bericht das *nicht* eigens — anders als beim Dokumentationstyp. Der Grund ist, dass sich dort keine sinnvolle Zahl nennen ließe: Ein Feld mit unbekanntem Schlüssel liefert schlicht keine Werte, und wie viele es hätten sein sollen, weiß niemand. Sichtbar bleibt der Zustand über „Erscheint in …" an der Feldvorlage selbst — dort steht dann „Erscheint in keinem Bericht."

#### Bindungs-Inventar prüfen

Die Auskunft „Erscheint in …" beantwortet die Frage für **einen** Dokumentationstyp bzw. **eine** Feldvorlage. Für den Blick über die ganze Einrichtung — welcher Typ und welche Feldvorlage erscheinen in welchem Bericht, und welche in keinem — gibt es den Wartungsbefehl **`inventory_report_bindings`**:

```bash
docker compose -f docker-compose.prod.yml exec \
  -e POSTGRES_USER=$POSTGRES_ADMIN_USER \
  -e POSTGRES_PASSWORD=$POSTGRES_ADMIN_PASSWORD \
  web python manage.py inventory_report_bindings --facility <uuid-oder-name>
```

Mit `--format json --output <datei>` schreibt er dieselbe Auswertung maschinenlesbar. Er **ändert nichts an Ihrer Konfiguration** — er liest nur.

Bei **Dokumentationstypen** unterscheidet er dieselben vier Zustände wie der Vollständigkeitshinweis der Berichte (zugeordnet · ausdrücklich ausgeschlossen · unbekannter Schlüssel · keine Angabe); aufgeführt werden dabei nur Typen mit Entitätstyp **Ereignis** — an Personen- und Fall-Typen wirkt der Berichtsschlüssel ohnehin nicht (siehe „Mindest-Kontaktstufe"). Bei **Feldvorlagen** — dort ohne Einschränkung auf einen Entitätstyp — nennt er nur *gebunden* oder *ungebunden*: Die oben beschriebene bewusste Lücke gilt auch hier, ein unbekannter Feld-Schlüssel erscheint schlicht als ungebunden. Als gebunden gilt eine Feldvorlage außerdem schon dann, wenn ihr Schlüssel zu einem in Ihrer Einrichtung verfügbaren Bericht passt — auch wenn sie noch keinem Dokumentationstyp zugeordnet ist.

Sinnvoll vor allem, nachdem Sie viele Typen von Hand angelegt oder ein fremdes Konfigurationspaket eingespielt haben, und als Gegenprobe, bevor Sie einen Bericht erstmals nach außen geben.

> ⚠️ **Rollen-Voraussetzung.** Wie die übrigen Wartungskommandos (`load_package`, `purge_orphan_field_data`, `reencrypt_fields`) muss der Lauf mit der **Admin-DB-Rolle** erfolgen — nach demselben Muster wie die Läufe in [Ops-Runbook § 3.5](ops-runbook.md#35-manuelle-ausfuehrung); die Rollen selbst erklärt das DB-Rollenmodell in [Ops-Runbook § 9](ops-runbook.md#9-row-level-security-rls). Ohne sie bricht er mit einer klaren Meldung ab, statt einen leeren Report auszugeben — unter der Laufzeit-Rolle sähe eine Einrichtung ohne sichtbare Zeilen aus wie eine tadellos konfigurierte (Row-Level-Security).

#### Mindest-Kontaktstufe

Legt fest, welche Kontaktstufe eine Person mindestens haben muss, damit ein Ereignis dieses Typs erstellt werden kann. Beispiel: Beratungsgespräche erfordern mindestens „Mit Zusatzangaben", weil zusätzliche persönliche Angaben zur Person vorliegen müssen.

> **Nur für Ereignis-Typen wirksam (Refs #1058).** **Mindest-Kontaktstufe**, **Aufbewahrungsfrist (`retention_days`)**, **Systemtyp** und **Berichtsschlüssel** wirken ausschließlich an Dokumentationstypen mit Entitätstyp **Ereignis**: Die Kontaktstufen-Prüfung greift nur bei der Ereignis-Anlage, und der Aufbewahrungslauf löscht ausschließlich Ereignisse. Für **Personen** greifen stattdessen die einrichtungsweiten Regeln aus den Einstellungen: die Papierkorb-Frist (`client_trash_days`) und die Anonymisierung, sobald die Ereignis-Retention den Bestand einer Person abgeräumt hat. **Fälle haben keine eigene Aufbewahrungsfrist** — der Aufbewahrungslauf löscht keinen Fall von sich aus. Er greift nur, wenn ein Fall bereits **gelöscht** wurde: Dann läuft dessen Papierkorb nach `client_trash_days` ab und der Fall wird anonymisiert (Refs #1818, [ADR-044](adr/044-fall-loeschpfad.md) — löst die frühere Zusage aus [ADR-039](adr/039-owner-generische-dateianhaenge.md) § 6 ab). Ohne Löschung werden die Inhalte eines Falls weiterhin erst mit der Anonymisierung der zugehörigen Person redigiert. `retention_qualified_days` knüpft zwar an den Abschluss eines Falls an, löscht aber dessen Ereignisse, nicht den Fall.
>
> Das Formular **blendet diese vier Felder aus**, sobald Sie als Entitätstyp **Person** oder **Fall** wählen (Refs #1757) — Sie füllen also keine Felder mehr aus, die nie greifen. Das Ausblenden ist rein optisch: bereits gesetzte Werte bleiben unverändert am Datensatz, und ein Feld mit Fehlermeldung bleibt sichtbar. Ohne JavaScript bleiben alle Felder sichtbar und tragen wie bisher den erklärenden Hilfetext.

#### Feldebenen-Sensibilität (`FieldTemplate.sensitivity`)

Neben der Sensibilität des Dokumentationstyps kann jedes einzelne Feld einer Feldvorlage eine **eigene** Sensibilitätsstufe erhalten (`FieldTemplate.sensitivity`). Effektiv gilt für die Sichtbarkeit des Feldes das **Maximum** aus Dokumentationstyp- und Feld-Sensibilität — ein als `HIGH` markiertes Feld bleibt für Fachkräfte unsichtbar, auch wenn der Dokumentationstyp selbst nur `NORMAL` ist.

**Entkopplung Verschlüsselung ↔ Sichtbarkeit** (Refs #356): Die beiden Flags `is_encrypted` (Ruhe-Verschlüsselung des Werts in der Datenbank) und `sensitivity` (Sichtbarkeit im UI) sind **unabhängig** voneinander konfigurierbar:

- Ein Feld kann verschlüsselt auf der Platte liegen, aber für `NORMAL`-Sensibilität im UI sichtbar sein (z. B. Kontaktdaten, die alle Rollen sehen dürfen, die aber nicht im Klartext gespeichert werden sollen).
- Ein nicht-verschlüsseltes Feld kann trotzdem auf Leitung/Admin beschränkt werden (z. B. statistische Marker, die sensitiv sind, aber nicht verschlüsselt gespeichert werden müssen).

#### Löschschutz bei bestehenden Daten

Felder, zu denen bereits Werte in Ereignissen, Personen oder Fällen existieren, können **nicht ohne Weiteres gelöscht** werden. Beim Versuch, eine Feldvorlage zu entfernen, greift ein Schutzmechanismus, der auf gespeicherte Werte prüft. Wird das Feld dennoch benötigt zu entfernen, muss vorher eine Daten-Migration laufen, die die Werte aufräumt oder in ein anderes Feld überführt.

> **Pragmatische Alternative:** Statt zu löschen, das Feld im Dokumentationstyp-Mapping deaktivieren — bestehende Daten bleiben erhalten, neue Ereignisse/Personen/Fälle bekommen das Feld nicht mehr angeboten.

##### Verwaiste Feldwerte („Nicht mehr zugeordnet")

Wird lediglich die **Zuordnung** einer Feldvorlage zu einem Dokumentationstyp entfernt (nicht die Feldvorlage selbst gelöscht), bleiben die bereits erfassten Werte unter ihrem Slug in den Datensätzen (`data_json`) gespeichert. Solche Werte gelten als **verwaist**: Sie werden bei Personen, Fällen und Ereignissen in der Detailansicht in einer abgesetzten Sektion **„Nicht mehr zugeordnete Felder"** angezeigt und im DSGVO-Auskunfts-Export (Art. 15) unter einem eigenen Abschnitt ausgewiesen — sie verschwinden also nicht unbemerkt. Sichtbarkeit und Verschlüsselung eines verwaisten Werts richten sich weiterhin nach der zugehörigen Feldvorlage (die durch den Löschschutz erhalten bleibt, solange Daten unter ihrem Slug existieren): Ein einst als „hoch"/„erhöht" eingestuftes Feld bleibt auch verwaist entsprechend eingeschränkt sichtbar.

Sollen verwaiste Werte endgültig **entfernt** werden (Datenminimierung, sobald sie fachlich nicht mehr gebraucht werden), dient der Wartungsbefehl **`purge_orphan_field_data`**. Er entfernt bei Personen, Fällen und Ereignissen genau die `data_json`-Schlüssel, die nicht (mehr) zur aktuellen Feldzuordnung ihres Typs gehören; zugeordnete Werte (auch verschlüsselte) bleiben unangetastet, Datensätze ohne Dokumentationstyp werden nur gezählt. `--dry-run` zeigt die Wirkung ohne zu schreiben. Wie die übrigen Wartungskommandos (z. B. `reencrypt_fields`, § 2.9) muss er mit der **Admin-DB-Rolle** laufen und bricht sonst mit einer klaren Meldung ab (Row-Level-Security). Ausführung: siehe `docs/ops-runbook.md` § 3.5.

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
| Datei | nicht unterstützt ||

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

#### Zahlenbereichsgrenzen (`Minimalwert`/`Maximalwert`)

Bei Feldvorlagen vom Typ **Zahl** lassen sich an derselben Stelle (**Core → Feldvorlagen**) optionale Grenzen setzen: **Minimalwert** und **Maximalwert**. Sie wirken **ausschließlich auf künftige Schreibvorgänge** — Neuanlage, Bearbeiten und Speichern über Formular oder Dienst-Schicht. Eine Verschärfung schreibt keinen einzigen bereits gespeicherten Wert um: Bestandsdaten außerhalb der neuen Grenze bleiben unverändert in der Datenbank stehen, bis sie fachlich korrigiert werden.

> **Vor jeder Verschärfung: schreibfreies Inventar ausführen.** Mit der Admin-DB-Rolle:
>
> ```bash
> python src/manage.py inventory_number_ranges --facility <UUID-oder-Name>
> ```
>
> Das Kommando verändert nichts und listet je Fund nur Entitätsart (Personen/Fälle/Ereignisse), Dokumentationstyp, Feld-Slug und Anzahl — nie den gespeicherten Wert selbst. „Keine Werte außerhalb konfigurierter Zahlenbereiche gefunden" heißt: Der Bestand des betroffenen Feldes liegt bereits innerhalb der geplanten Grenze, die Verschärfung ist gefahrlos.

**Meldungen des Inventars:**

| Meldung | Bedeutung |
|---|---|
| `unter Minimalwert X` / `über Maximalwert X` | Ein gespeicherter Wert liegt außerhalb der geplanten Grenze. |
| `keine Zahl` | Sammelbefund für drei unterscheidbare, aber bewusst gleich gemeldete Fälle: ein leerer **Pflicht**wert, ein nicht-numerisch gespeicherter Wert, oder ein Entschlüsselungsfehler (beschädigtes/fremdes Chiffrat). Ein Entschlüsselungsfehler wird **immer** gemeldet, auch an einem optionalen Feld. |
| *(kein Eintrag)* | Ein leerer Wert an einem **optionalen** Feld ist kein Fund — „kein Wert" ist dort ein gültiger Zustand, keine Bereichsverletzung. |

Ein Fund ist kein Reparaturauftrag an das Inventar selbst: Es zählt nur, es schreibt nichts. Bereinigen Sie gemeldete Datensätze fachlich — öffnen Sie den betroffenen Datensatz und tragen Sie einen gültigen Wert ein bzw. leeren Sie ein optionales Feld, bevor Sie die Grenze verschärfen.

#### Speicher-Blockverhalten bei bestehenden Bestandswerten

Ist eine Grenze verschärft, prüft der zentrale Validator **jeden** Schreibvorgang — auch das Bearbeiten eines Datensatzes, dessen Zahlenfeld gar nicht angefasst wurde: Ein Formular übermittelt beim Speichern immer alle aktuellen Feldwerte, nicht nur die tatsächlich geänderten. Liegt der gespeicherte Bestandswert außerhalb der neuen Grenze, weist das Formular ihn wie einen regulären Eingabefehler zurück (**„Dieser Wert muss größer oder gleich X sein."**/**„Dieser Wert muss kleiner oder gleich X sein."**, X = der konfigurierte Minimal-/Maximalwert), und das **gesamte** Speichern wird verweigert — auch wenn die eigentlich beabsichtigte Änderung ein ganz anderes Feld betraf. Das ist die vorgesehene Wirkung des Validators, kein Fehler: Er lässt sich nur auflösen, indem der betroffene Wert korrigiert (oder, sofern das Feld optional ist, geleert) wird. Betroffen ist jeweils nur der einzelne blockierte Datensatz — andere Datensätze desselben Feldes bleiben unabhängig davon bearbeitbar.

#### Grenze sicher zurücknehmen

Erweist sich eine Grenze als zu eng, nehmen Sie sie an derselben Stelle zurück: **Minimalwert**/**Maximalwert** an der Feldvorlage lockern oder leeren. Das wirkt sofort auf künftige Schreibvorgänge; zuvor blockierte Datensätze lassen sich wieder normal speichern, sobald ihr Bestandswert innerhalb der (jetzt weiteren) Grenze liegt — ohne dass dafür ein Wert nachträglich verändert werden müsste. Pflegen Sie die Grenze stattdessen über ein Konfigurationspaket, gilt dort ein eigenes Tri-State-Verhalten (fehlender Schlüssel lässt eine bestehende Grenze unangetastet, erst ein explizites `null` entfernt sie) — siehe [docs/packages.md](packages.md).

### 2.6b Fuzzy-Suche (pg_trgm)

Die globale Suche nach Personen (Pseudonymen) nutzt zusätzlich zur exakten Teilstring-Suche eine **Trigramm-basierte Fuzzy-Suche** — tolerant gegenüber Tippfehlern und phonetischen Varianten (z. B. „Schmidt" ↔ „Schmitt"). Implementierung: [`src/core/services/search.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/search.py), Funktion `search_similar_clients`.

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

Referenzen: #536, #581.

### 2.7 Zwei-Faktor-Authentifizierung (2FA)

Die Anlaufstelle unterstützt TOTP-basierte 2FA über [`django-otp`](https://django-otp-official.readthedocs.io/). Jeder Benutzer kann 2FA selbst aktivieren (`/mfa/settings/`); zusätzlich gibt es zwei Verpflichtungs-Ebenen.

**Benutzerseitige Doku:** [User-Guide § 1 — Zwei-Faktor-Authentifizierung](user-guide.md#zwei-faktor-authentifizierung-2fa).

#### Erzwingung konfigurieren

| Ebene | Feld | Ort im Admin | Wirkung |
|---|---|---|---|
| **Einzeluser** | `User.mfa_required` | Verwaltung → Zugänge → *Zugang* → Feld „MFA erforderlich" | 2FA verpflichtend für diesen User, Deaktivieren gesperrt |
| **Einrichtungsweit** | `Settings.mfa_enforced_facility_wide` | Core → Einstellungen → *Einrichtung* → Feld „2FA einrichtungsweit verpflichtend" | 2FA verpflichtend für **alle** User dieser Einrichtung |
| **Rollen-Default** | `MFA_ENFORCE_PRIVILEGED_ROLES` (+ `MFA_ENFORCED_ROLES_IMMEDIATE` / `MFA_ENFORCED_ROLES_GRACE`) | Umgebungsvariablen (Deployment) | 2FA verpflichtend allein über die Rolle — in Produktion ab Werk aktiv |

Die Auswertung erfolgt über [`User.is_mfa_enforced`](https://github.com/anlaufstelle/app/blob/main/src/core/models/user.py) — User gilt als verpflichtet, wenn **eine** der Ebenen zutrifft. Die MFA-Middleware blockiert den Zugriff auf geschützte Bereiche, bis ein TOTP-Code in der Session verifiziert wurde.

**Rollen-Default über Umgebungsvariablen konfigurieren** (#1639). Unabhängig von der Einzeluser- und der einrichtungsweiten Erzwingung verlangt die Anwendung 2FA in Produktion bereits **allein über die Rolle** (`MFA_ENFORCE_PRIVILEGED_ROLES=True`, Default in Produktion; in dev/test/e2e/demo bewusst aus). Welche Rollen das umfasst, steuern zwei per Env als **Kommaliste** überschreibbare Settings:

| Setting | Default | Wirkung |
|---|---|---|
| `MFA_ENFORCED_ROLES_IMMEDIATE` | `super_admin,facility_admin` | 2FA **sofort** verpflichtend (kein Aufschub) — ohne zweiten Faktor sofortiger Redirect auf `/mfa/setup/` |
| `MFA_ENFORCED_ROLES_GRACE` | `lead,staff,assistant` | 2FA verpflichtend, aber erst **nach 30-tägiger Übergangsfrist** (Erinnerungsbanner ab 14 Resttagen), damit Bestandsnutzende nicht ausgesperrt werden |

Die Rolle **Leitung** (`lead`) ist per Default pflichtig (im Grace-Set) — wer Fachkraft/Assistenz erzwingt, erst recht die hierarchisch höhere Leitung. Gültige Rollennamen sind `super_admin`, `facility_admin`, `lead`, `staff`, `assistant`; eine **leere** Liste schaltet die jeweilige Ebene bewusst ab. Ein **unbekannter Rollenname** oder eine **Überschneidung** beider Listen bricht den Start fail-closed mit `ImproperlyConfigured` ab (keine stille Fehlkonfiguration).

#### 2FA für einen Benutzer zurücksetzen

Wenn eine Mitarbeiterin ihr Authenticator-Gerät verliert, muss ein Administrator das TOTP-Gerät löschen, damit sie neu einrichten kann:

1. **Admin → OTP → TOTP-Geräte** aufrufen.
2. Das Gerät des betroffenen Users auswählen und löschen.
3. User informieren: nach dem nächsten Login wird automatisch auf `/mfa/setup/` umgeleitet (bei `is_mfa_enforced=True`) oder der User kann 2FA freiwillig neu einrichten.

Seit v0.10.1 gibt es zusätzlich **Backup-Codes als zweiten Faktor** für genau diesen Recovery-Fall (Refs #588). Bei der 2FA-Einrichtung erhält der User 10 einmalig nutzbare Codes, die er ausgedruckt oder im Passwort-Manager hinterlegen sollte — am Login-2FA-Prompt kann er statt eines TOTP-Codes einen Backup-Code eingeben. Verbrauchte Codes werden invalidiert und im AuditLog (`MFA_BACKUP_CODE_USED`) protokolliert. Sind alle 10 Codes verbraucht oder verloren, bleibt der Admin-Reset oben der Fallback.

#### Account-Lockout

Nach **10 fehlgeschlagenen Login-Versuchen** wird das Konto automatisch gesperrt (Login-Service liest die Schwelle aus [`src/core/services/security/login_lockout.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/login_lockout.py)). Der gesperrte User sieht eine Hinweis-Seite und kann sich nicht mehr anmelden. Es gibt vier Recovery-Pfade:

**Pfad A — UI (für facility-gebundene User):** Die Anwendungsbetreuung (`facility_admin`) der Einrichtung kann den Account ihrer eigenen User entsperren:

1. **Verwaltung → Zugänge** → betroffenen User der eigenen Einrichtung auswählen.
2. Unter „Sicherheitsaktionen" auf **Account-Sperre aufheben** klicken.
3. Cleanup wird im AuditLog als `LOGIN_UNLOCK` protokolliert (das `LOGIN_FAILED`-Log selbst ist dank `auditlog_immutable`-DB-Trigger unveränderbar).

**Pfad B — CLI (für Recovery, insbesondere Super-Admins):** Wenn kein anderer User im System Zugriff hat (Single-Super-Admin-Sperre, oder gesperrter `facility_admin` und kein zweiter `facility_admin`), entsperrt der Hosting-Operator den Account direkt am Server:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py unlock <username>
```

Der Befehl ist auf alle Rollen anwendbar (`super_admin`, `facility_admin`, `lead`, `staff`, `assistant`), schreibt einen `LOGIN_UNLOCK`-AuditLog-Eintrag und ist die einzige Recovery-Option für gesperrte Super-Admins.

**Pfad C — Self-Service per E-Mail (Refs #869):** Hat der User eine E-Mail-Adresse hinterlegt, kann er sich auf der Login-Seite über die Links „Konto entsperren per E-Mail" oder „Passwort vergessen?" selbst entsperren — beide Pfade schreiben einen `LOGIN_UNLOCK`-AuditLog. Der dedizierte Entsperr-Link unter `/account/recovery/` verschickt einen 30-Minuten-gültigen, signierten Token (kein neues Modell, `TimestampSigner` mit Salt `lockout-recovery.v1`). Anti-Enumeration: Die Bestätigungsseite ist identisch, ob die E-Mail einem Konto zugeordnet ist oder nicht.

**Pfad D — Self-Service per MFA-Backup-Code:** Hat der User 2FA aktiviert und einen seiner Backup-Codes notiert, kann er sich unter `/account/recovery/backup-code/` mit Username + Code entsperren. Audit-Trail: `LOGIN_UNLOCK` (Trigger `backup_code`) + `BACKUP_CODES_USED` (Flow `lockout_recovery`). Jeder Code ist einmalig — der nächste Recovery-Vorgang verbraucht den nächsten Code.

Sperre, Entsperre und alle Versuche während der Sperrphase werden im AuditLog mitprotokolliert — nutzen Sie den Filter „Anmeldung fehlgeschlagen" / „Sperre aufgehoben" für eine retroaktive Auswertung. Der `LOGIN_UNLOCK`-Detail-Block enthält ein `trigger`-Feld: `admin` (Admin-Action), `cli` (CLI-Command), `password_reset` (Pfad C via Standard-Reset), `recovery_token` (Pfad C via dedizierter Link), `backup_code` (Pfad D).

#### Audit-Spur

Alle 2FA-Vorgänge werden im `AuditLog` protokolliert (Actions: `MFA_ENABLED`, `MFA_DISABLED`, `MFA_FAILED`). Nutzen Sie den Filter im Admin unter **Core → Audit-Logs**, um fehlgeschlagene Verifikationsversuche oder Aktivierungs-/Deaktivierungsereignisse auszuwerten.

#### Relevante Dateien

- Modelle: [`src/core/models/user.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/user.py), [`src/core/models/settings.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py)
- Views: [`src/core/views/mfa.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/mfa.py)
- Middleware: [`src/core/middleware/`](https://github.com/anlaufstelle/app/tree/main/src/core/middleware)

### 2.8 Schnell-Vorlagen (Quick-Templates)

**Schnell-Vorlagen** sind vorbefüllte Dokumentvorlagen für wiederkehrende Muster (z. B. „Beratungsgespräch 30 Min", „Standard-Check-in"). Fachkräfte wenden sie per Klick auf der Seite „Kontakt dokumentieren" an.

**Benutzerseitige Doku:** [User-Guide § 3 — Schnell-Vorlagen](user-guide.md#schnell-vorlagen-quick-templates).

#### Vorlagen verwalten

Vorlagen werden im Django-Admin unter **Core → Quick-Templates** gepflegt.

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

Der Service-Layer wendet vor dem Speichern und erneut beim Anwenden eine **Whitelist** an ([`src/core/services/quick_templates.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/quick_templates.py)). Werte bleiben nur erhalten, wenn:

- der Slug zum gewählten Dokumentationstyp gehört,
- die **effektive Sensitivität** des Feldes `NORMAL` ist (kein Prefill für `ELEVATED`/`HIGH`),
- das Feld nicht verschlüsselt ist (`is_encrypted=False`) und kein `FILE`-Feld ist,
- bei `SELECT`/`MULTI_SELECT` der Wert einer **aktiven** Option des aktuellen FieldTemplates entspricht.

Dadurch sind Vorlagen **selbstheilend**: Wird eine Auswahl-Option später deaktiviert, müssen Vorlagen nicht migriert werden — der veraltete Wert wird beim nächsten Anwenden stillschweigend verworfen.

#### Rollen- und Sensitivitäts-Sichtbarkeit

Schnell-Vorlagen werden pro User über [`user_can_see_document_type`](https://github.com/anlaufstelle/app/blob/main/src/core/services/sensitivity.py) gefiltert. Eine Assistenz, die `ELEVATED`/`HIGH`-Dokumentationstypen nicht sehen darf, sieht dafür auch keine Vorlagen. So taucht keine Vorlage in der Button-Liste auf, die der User anschließend gar nicht anwenden könnte.

#### Operative Hinweise

- Vorlagen sind ein **Bequemlichkeits-Layer**, keine Datenquelle — das Ereignis wird weiter explizit vom User gespeichert, alle Felder bleiben editierbar.
- Anwenden einer Vorlage **überschreibt bestehende Werte nicht**; gefüllt werden nur leere Felder.
- Aktuell gibt es **keine Custom-UI** für die Vorlagen-Verwaltung außerhalb des Django-Admin. Nur User mit der Rolle `facility_admin` können Vorlagen pflegen (in der eigenen Einrichtung).

#### Relevante Dateien

- Modell: [`src/core/models/quick_template.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/quick_template.py)
- Service: [`src/core/services/quick_templates.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/quick_templates.py)
- View-Integration: [`src/core/views/events.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/events.py) (`EventCreateView`)
- Tracking-Issue: #494

### 2.9 Encrypted File Vault & Virus-Scanning

Dateianhänge (Fotos, Scans, Dokumente) an **Ereignissen, Personen und Fällen** ([ADR-039](adr/039-owner-generische-dateianhaenge.md), Refs #1738 — je Datei genau ein Owner) werden in einem **verschlüsselten Vault** abgelegt: vor dem Schreiben in `MEDIA_ROOT` auf Viren geprüft und anschließend chunk-weise per **Fernet** (AES-128-CBC mit HMAC-SHA256, [`cryptography.fernet`](https://cryptography.io/en/latest/fernet/)) mit dem `ENCRYPTION_KEYS`-Schlüsselmaterial verschlüsselt. Implementierung: [`encrypt_file()` in `src/core/services/encryption.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/encryption.py). Refs #524.

#### Upload-Flow

1. Fachkraft wählt Datei im Formular aus — Ereignis ([`src/core/forms/events.py`](https://github.com/anlaufstelle/app/blob/main/src/core/forms/events.py)), Person oder Fall ([`clients.py`](https://github.com/anlaufstelle/app/blob/main/src/core/forms/clients.py)/[`cases.py`](https://github.com/anlaufstelle/app/blob/main/src/core/forms/cases.py)).
2. Server prüft Dateityp und Größe (siehe `allowed_file_types` und `max_file_size_mb` in den Einrichtungseinstellungen — Default **10 MB**).
3. **ClamAV-Scan** vor Verschlüsselung:
   - Standard: fail-closed. Ist der Daemon nicht erreichbar (`CLAMAV_ENABLED=true`, aber kein TCP-Connect möglich), wird der Upload **abgelehnt**.
   - Erkannter Virus → Upload wird verworfen, ein Audit-Log-Eintrag wird geschrieben.
4. Inhalt wird chunk-weise mit Fernet (MultiFernet) verschlüsselt (Write-Key = erster Eintrag aus `ENCRYPTION_KEYS`) und in `MEDIA_ROOT` gespeichert.
5. Download erfolgt ausschließlich über die geschützte Django-View (kein direkter Webserver-Zugriff auf `MEDIA_ROOT`).

> **Klartext-Zwischenpuffer (Refs #1661):** Uploads oberhalb von `FILE_UPLOAD_MAX_MEMORY_SIZE` (Default 2,5 MB) puffert Django vor Schritt 3 als Klartext-Tempdatei auf Platte (`FILE_UPLOAD_TEMP_DIR`), bevor sie gescannt und verschlüsselt werden. In den mitgelieferten Compose-Dateien liegt dieser Puffer auf einem `/tmp`-tmpfs (RAM) statt auf persistentem Storage. Details, Verifikation und Fallback-Optionen ohne tmpfs: [Ops-Runbook § 5.7a](ops-runbook.md).

#### ClamAV-Service (`docker-compose.prod.yml`)

Das Produktions-Compose enthält einen eigenen `clamav`-Container mit Healthcheck (`clamdcheck.sh`). Der Web-Dienst wartet auf `service_healthy`, bevor er startet. Referenz: [`docker-compose.prod.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.prod.yml). Konfigurierbar über `CLAMAV_ENABLED` / `CLAMAV_HOST` / `CLAMAV_PORT` / `CLAMAV_TIMEOUT` (siehe [ENV-Referenz in § 1](#vollständige-umgebungsvariablen-referenz)).

#### Healthcheck

Der `/health/`-Endpoint prüft in Produktion zusätzlich zur Datenbank die Erreichbarkeit des ClamAV-Daemons. Ist ClamAV aktiviert, aber nicht erreichbar, signalisiert der Endpoint einen Fehlerzustand (HTTP 503) — so erkennen externe Monitore auch „Uploads sind aktuell nicht möglich".

#### Schlüssel-Rotation (`ENCRYPTION_KEYS`)

MultiFernet akzeptiert eine Liste von Schlüsseln. Um zu rotieren:

1. **Neuen Schlüssel generieren** und als **ersten** Eintrag in `ENCRYPTION_KEYS` einfügen: `ENCRYPTION_KEYS=neu,alt`. Neu geschriebene Daten werden mit `neu` verschlüsselt, vorhandene mit `alt` lassen sich weiterhin lesen.
2. Optional: `python manage.py reencrypt_fields` laufen lassen, damit bestehende Felder auf den neuen Key umgeschlüsselt werden (siehe [`src/core/management/commands/reencrypt_fields.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/reencrypt_fields.py)). Ein `--dry-run` zeigt vorab, wie viele Datensätze betroffen wären, ohne zu schreiben. **Der Lauf muss mit der Admin-/Owner-Rolle erfolgen** — siehe Kasten unten.
3. Nach erfolgreichem Umschlüsseln den alten Schlüssel aus der Liste entfernen.

> **Wichtig:** Niemals den alten Key **ersetzen** statt **ergänzen** — sonst werden alle noch nicht umgeschlüsselten Bestandsdateien unlesbar.

> ⚠️ **Rollen-Voraussetzung (Refs #1058).** `reencrypt_fields` liest und schreibt Tabellen mit Row-Level-Security. Die Laufzeit-Rolle des `web`-Containers ist `NOBYPASSRLS` und hat außerhalb eines Requests keinen Einrichtungs-Kontext — ein Aufruf dort sähe **0 Zeilen**. Deshalb immer ueber den Maintenance-Service aufrufen ([Ops-Runbook § 9.5](ops-runbook.md)):
>
> ```bash
> docker compose run --rm maintenance reencrypt_fields --dry-run
> ```
>
> Der Command bricht seit #1058 mit einer klaren Fehlermeldung ab, wenn der Bypass-Kontext fehlt (fail-loud statt „0 rotiert, Erfolg gemeldet"). Bei älteren Ständen ohne diesen Guard gilt: **Schritt 3 niemals nach einem Lauf ausführen, der überall `0` meldet** — sonst steht der Alt-Schlüssel nicht mehr in der Liste, obwohl nichts rotiert wurde.

**Was `reencrypt_fields` abdeckt** (Refs #1058):

| Datenbestand | Umfang |
|---|---|
| **`Event.data_json`** | **vollständig — inklusive soft-gelöschter Ereignisse** (#1724) |
| `EventHistory.data_before` / `data_after` | vollständig (append-only, Update über den Replikations-Bypass) |
| `Attachment.original_filename_encrypted` | vollständig — Anhänge an Ereignis, Person und Fall (das Modell hieß bis #1738 `EventAttachment`) |
| TOTP-Secrets (`otp_totp_totpdevice.key`) | vollständig, je Gerät mit Row-Lock und Compare-and-Swap |
| **`Client.data_json`** | **vollständig — inklusive soft-gelöschter Personen** |
| **`Case.data_json`** | **vollständig — inklusive soft-gelöschter Fälle** |

Seit den entitätsübergreifenden dynamischen Feldern ([ADR-037](adr/037-konfigurationspakete-dynamische-felder.md)) tragen auch **Personen** und **Fälle** verschlüsselte Zusatzfelder in `data_json`. Der Lauf rotiert sie mit und heilt dabei zusätzlich Klartext-Reste in Feldern, die laut Dokumentationstyp verschlüsselt sein müssen.

Bei Ereignissen, Personen und Fällen werden **auch soft-gelöschte Datensätze** rotiert. Das ist Absicht: ein Datensatz im Papierkorb soll nicht dauerhaft unter einem ausrangierten Schlüssel liegen bleiben, nur weil er auf Löschung wartet. Die frühere Lücke bei soft-gelöschten Ereignissen (`is_deleted=False`-Filter) ist seit #1724 geschlossen.

> **Betriebshinweis:** Der Lauf nimmt für Ereignisse, Personen, Fälle und Anhänge **keinen** Row-Lock (anders als beim TOTP-Rewrap). Bei paralleler Bearbeitung während der Rotation ist ein verlorenes Update theoretisch möglich — die Rotation deshalb im Wartungsfenster fahren, nicht im laufenden Betrieb.

#### Upload-Limit

- Per Einrichtung: `Settings.max_file_size_mb` (Default **10 MB**) — siehe [`src/core/models/settings.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py).
- Global: Djangos `DATA_UPLOAD_MAX_MEMORY_SIZE` gilt als zusätzliche harte Grenze. Erhöhung via ENV bzw. Settings-Override, falls größere Uploads erlaubt sein sollen.

#### Sichere Downloads (RFC 5987)

Alle File-Downloads werden über den zentralen Helper [`safe_download_response`](https://github.com/anlaufstelle/app/blob/main/src/core/utils/downloads.py) ausgeliefert. Der Helper setzt `Content-Disposition` mit RFC-5987-kodierten Dateinamen (Unicode-sicher, kein Reverse-Path-Traversal) und verhindert Browser-MIME-Sniffing.

### 2.10 Offline-Modus & Streetwork (M6A)

Für Außeneinsätze (z. B. Streetwork) bietet die Anlaufstelle einen **sicheren Offline-Modus** mit **client-seitiger** Ende-zu-Ende-Verschlüsselung aller auf dem Gerät zwischengespeicherten Daten. Refs #573, #576.

#### Kryptographie-Design

| Aspekt | Wert |
|---|---|
| Algorithmus | **AES-GCM-256** (WebCrypto-native) |
| Storage | **IndexedDB** (nur AES-Chiffretext, **niemals** Klartext) |
| Key-Ableitung | **PBKDF2** — 600 000 Iterationen, SHA-256 |
| KDF-Input | Benutzer-Passwort + `User.offline_key_salt` (16 Byte, pro User, serverseitig gespeichert) |
| Key-Lebenszeit | **Persistiert** als non-extractable `CryptoKey` (`extractable: false`) in einer eigenen IndexedDB (`anlaufstelle-crypto`) — überlebt Tab-Schließen und Reload; gekoppelt an das Server-Session-Alter (Idle-Key-Wipe), siehe „Konsequenzen für Admins" |

Quelle: [`src/static/js/crypto.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/crypto.js) (client-seitige Ableitung/Speicherung/Idle-Wipe), [`src/core/signals/offline_invalidation.py`](https://github.com/anlaufstelle/app/blob/main/src/core/signals/offline_invalidation.py) (server-seitige Salt-Rotation bei Rollenwechsel/Deaktivierung).

#### Konsequenzen für Admins

- **Key-Verlust ist nicht reparierbar:** Vergisst ein Nutzer sein Passwort, sind alle auf seinem Gerät lokal gespeicherten Offline-Daten **dauerhaft unlesbar**. Ein Admin-Reset der Offline-Daten ist technisch **nicht möglich** — der Schlüssel existiert nur im Browser des Nutzers.
- **Vor Passwort-Änderung synchronisieren:** Nutzer müssen Offline-Daten mit dem Server synchronisieren, **bevor** sie das Passwort ändern oder sich ausloggen. Bei Passwortwechsel wird das Salt rotiert (neuer Schlüssel); derselbe Mechanismus rotiert den Salt automatisch bei Rollenwechsel oder Deaktivierung des Users (`offline_invalidation.py`), damit ein entzogenes Recht nicht offline fortbesteht.
- **Schlüssel wird verworfen bei:** Logout, Passwort-Änderung (Salt-Rotation), Rollenentzug/Deaktivierung (server-seitige Salt-Rotation) und Sitzungs-Ablauf (Idle-Key-Wipe, Default 30 Min) → gespeicherte Chiffretexte sind ohne erneute Anmeldung nicht mehr zu entschlüsseln. **Das bloße Schließen des Tabs verwirft den Schlüssel NICHT** — er wird beim Wiederöffnen innerhalb der Session-Lebenszeit aus der IndexedDB rekonstruiert (bewusstes Streetwork-Design, #1065/#1324).

#### Browser-Voraussetzungen

- WebCrypto-API (`crypto.subtle`) und IndexedDB müssen verfügbar sein.
- Unterstützt: aktuelle Versionen von Firefox, Chrome, Edge, Safari.
- Nicht unterstützt: Legacy-Browser ohne moderne Crypto-API (der Offline-Modus wird in dem Fall ausgegraut).

#### Streetwork-Stufen

- **Stufe 2 (Read-Cache):** Ausgewählte Personen-Dossiers werden vor dem Streetwork-Einsatz lokal verschlüsselt gecacht, damit sie offline eingesehen werden können.
- **Stufe 3 (Offline-Edit):** Ereignisse und Notizen können offline erfasst werden und synchronisieren beim nächsten Online-Gang. Konflikte zwischen Offline-Edit und zwischenzeitlicher Server-Änderung werden per **Side-by-Side-Diff** dem Nutzer zur manuellen Auflösung präsentiert.

### 2.11 CSV-Import und -Export: Formelschutz

Alle CSV-Exporte (Statistik-Export, Datensatz-Berichte, Audit-Log-Export) neutralisieren **Formel-Injection**: Eine Zelle, die mit `=`, `+`, `-`, `@`, Tabulator oder Zeilenumbruch beginnt, würde Excel oder LibreOffice beim Öffnen der Datei als **Formel** ausführen. Solche Zellen bekommen deshalb ein führendes Apostroph (`'`) — die Tabellenkalkulation zeigt den Originalwert an, führt ihn aber nicht aus. Refs #719.

Seit #1945 greift der Schutz auch, wenn vor dem Formelzeichen **unsichtbare Zeichen** stehen — Leerzeichen, geschütztes Leerzeichen (NBSP), Byte-Order-Mark oder Zero-Width-Zeichen. Tabellenkalkulationen verwerfen diese Zeichen beim Import und werten die Zelle danach trotzdem aus; `" =1+1"` wird deshalb ebenfalls apostrophiert. Der Originalwert bleibt vollständig erhalten, es wird nichts abgeschnitten. Das gilt für Werte- **und** Kopfzeilen — der Name einer Feldvorlage ist Freitext der Anwendungsbetreuung und damit derselbe Vektor.

> **Wichtig — eigene Exporte sind nicht round-trip-fest:** Der CSV-Import entfernt das Schutz-Apostroph **nicht** wieder. Importieren Sie eine zuvor exportierte Datei erneut, wird aus dem Wert `=Text` dauerhaft `'=Text`. Das ist kein Fehler und keine Fehlermeldung im Importlauf, sondern eine bewusste Grenze: Die Import-Engine vergleicht die **rohe** Dateizeile mit ihren Fehlermeldungen, um zu verhindern, dass Zellinhalte (potenziell Klientendaten) im Ergebnisprotokoll eines Laufs landen. Würde sie vorher entapostrophieren, träfe dieser Abgleich nicht mehr und ein Zellwert könnte ins Protokoll durchrutschen — Datenschutz geht hier vor Bequemlichkeit.
>
> **Praxis:** Nutzen Sie den CSV-Import für die **Übernahme aus Fremdsystemen**, nicht als Sicherung-und-Rückspielweg für eigene Daten. Für Letzteres ist das Datenbank-Backup (siehe [§ 3](#3-backup-und-wiederherstellung)) der richtige Weg. Müssen Sie einen exportierten Wert doch wieder einlesen, entfernen Sie das führende `'` vorher in der Tabellenkalkulation.

Die Berichts-Exporte in **JSON und XML** sanitisieren bewusst nicht — dort gibt es keinen Formel-Kontext, und CSV-Escapes würden die maschinenlesbaren Werte verfälschen.

Relevante Dateien: [`src/core/services/system/export.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/system/export.py) (`_sanitize_csv_cell`), [`src/core/services/import_engine.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/import_engine.py).

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

Migrationen laufen als separater One-Shot-Job **vor** dem Neustart des `web`-Dienstes — dieser führt beim Start selbst keine Migrationen mehr aus (Refs #802). Details siehe die kanonische Quelle [`docs/ops-runbook.md` § 1.2](ops-runbook.md#12-deploy-schritte).

```bash
# 1. Neuestes Image herunterladen
docker compose -f docker-compose.prod.yml pull web

# 2. Migrationen als One-Shot-Job ausführen
docker compose -f docker-compose.prod.yml run --rm \
    --entrypoint=/app/docker-migrate.sh maintenance

# 3. Stack neu starten (web + caddy, DB bleibt; kurze Downtime)
docker compose -f docker-compose.prod.yml up -d web caddy
```

Prüfen Sie anschließend die Logs:

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

**Antwort bei normalem Betrieb (HTTP 200, anonym):**

```json
{"status": "ok"}
```

**Antwort bei Datenbank-/Encryption-Key-Fehler (HTTP 503, anonym):**

```json
{"status": "error"}
```

Der Endpoint erfordert keine Authentifizierung und ist für externe Monitoring-Systeme geeignet. Die anonyme Antwort enthält seit #1375 bewusst **nur** das `status`-Feld (Recon-Härtung): Subsystem-Ausfälle bleiben anonym über `status`/HTTP-Code sichtbar (`degraded` bei Scanner-/SMTP-Problemen sowie bei einer RLS-umgehenden DB-Runtime-Rolle, `error` + 503 bei DB-/Key-Ausfall), aber alle Detailfelder (`database`, `virus_scanner`/`clamav`, `encryption_key`, `db_user_is_superuser`/`db_role_posture`, `version`, `smtp`, `last_backup_age_hours`, `disk_free_pct`, `stale_jobs`) liefert `/health/` nur an authentifizierte Sessions oder Caller mit dem Header `X-Health-Token` (= `DJANGO_HEALTH_DETAIL_TOKEN`). Die vollständige, autoritative Feld-Referenz (alle Felder, Werte, Status-Codes und Schwellen) steht im [Monitoring-Guide](monitoring-guide.md).

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

Die Content-Security-Policy (CSP) wird **zentral in Django** über [`django-csp`](https://django-csp.readthedocs.io/) gesetzt (siehe [`src/anlaufstelle/settings/base.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py), `CONTENT_SECURITY_POLICY`). Die frühere redundante CSP-Konfiguration im Caddyfile wurde entfernt — nur so ist sichergestellt, dass App- und Reverse-Proxy-Policy nicht auseinanderlaufen.

**Inline-Skripte sind nicht erlaubt.** Alle JavaScript-Logik liegt in externen Dateien unter `src/static/js/`, eingebunden per `<script src=…>` oder über Nonce-Aware-Template-Tags.

**`script-src` global ohne `'unsafe-eval'`.** Mit der Migration auf den `@alpinejs/csp`-Build (v0.10.2) ist `'unsafe-eval'` aus der globalen Policy entfernt. Alle Alpine-Komponenten sind als `Alpine.data()`-Komponenten in [`src/static/js/alpine-components.js`](https://github.com/anlaufstelle/app/blob/main/src/static/js/alpine-components.js) registriert; Architektur-Tests verbieten Inline-`x-data="{...}"` und komplexe Expressions (Ternaries, `||`/`&&`, Method-Calls, Object-Literale) in Alpine-/HTMX-Direktiven.

**Ausnahme `/admin-mgmt/*` (Django-Admin):** django-unfold lädt einen eigenen Alpine-Build, der für die Cmd+K-Suche `new AsyncFunction()`-basierte Auswertung nutzt und damit ohne `'unsafe-eval'` nicht initialisiert. Die [`AdminCSPRelaxMiddleware`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/) ergänzt `'unsafe-eval'` deshalb **per Request nur für Admin-Routen** — diese sind durch MFA-Gate und Rolle `facility_admin` (bzw. `super_admin`) zusätzlich geschützt. Außerhalb des Admins bleibt die strenge globale Policy aktiv.

**Typische Fehlerbilder im Browser-Console:**

- `Refused to execute inline script because it violates the following Content Security Policy directive` — Inline-`<script>`-Block im Template. Auslagern in eine statische JS-Datei oder über ein Nonce-Aware-Template-Tag einbinden.
- `Refused to load the script … because it violates … directive: "script-src 'self'"` — externes Script-CDN wird nicht unterstützt; alle Skripte müssen aus `self` stammen.
- `Refused to evaluate a string as JavaScript because 'unsafe-eval' is not an allowed source` — auf normalen Routen erwartet (Architektur-Bruch); im Admin-Bereich Hinweis darauf, dass die Relax-Middleware nicht greift (Route-Pattern in [`AdminCSPRelaxMiddleware`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/) prüfen).

Bei CSP-Fehlern nach einem Update: Browser-Console auf **konkret blockierte URL/Quelle** prüfen und entscheiden, ob die Quelle ins Template verschoben oder die CSP-Richtlinie angepasst werden muss.

### 5.5 Compliance-Dashboard

Der Superadmin-Bereich `/system/compliance/` (#919) bündelt typisierte Health-Checks in einem Dashboard: die drei DB-Rollen-Checks (über `check_db_roles`, siehe Datenbank-Variablen in [§ 1](#1-installation-docker-compose)), Backup-Alter, Restore-Verified-Alter, ClamAV-Ping + Signaturalter, Retention-Run-Alter, Frische der Hintergrundjobs (Statistik-Snapshot, Breach-Scan, Materialized-View-Refresh), MFA-Quote (MFA-pflichtige Rollen), ausstehende Django-Migrationen, App-/Django-/Python-Versionen sowie kritische Audit-Events der letzten 24 Stunden.

Jeder Check liefert einen Status `ok` / `warning` / `critical` / `unknown`; ein einzelner Check-Fehler kippt das Dashboard nicht. Der Bereich ist **Super-Admins** vorbehalten (`facility_admin` erhält `403`). Die Frische-Schwellen der Hintergrundjobs und des Restore-Markers sind im [Ops-Runbook](ops-runbook.md) dokumentiert.

### 5.6 Error-Tracking & Datenminimierung

Das Error-Tracking (Sentry-kompatibel) ist **vollständig opt-in**: Das SDK wird nur initialisiert, wenn `SENTRY_DSN` gesetzt ist ([`prod.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/prod.py)). Ohne DSN verlässt **keinerlei Telemetrie** die Installation — es gibt kein gehostetes Angebot dieses Projekts, die Datenresidenz liegt vollständig beim Betreiber. Ein Ausfall oder Fehlen des Trackers beeinträchtigt die Anwendung nicht; Fehler stehen weiterhin in den Server-Logs (siehe **Logging** in [§ 1](#1-installation-docker-compose)).

**Nur serverseitiges SDK.** Eingebunden ist ausschließlich das Server-SDK (`sentry-sdk[django]`), kein Browser-SDK. Session Replay, Feedback-Widgets, clientseitige Breadcrumbs und Browser-Profiling existieren im Projekt nicht — es kann also nichts davon unbemerkt mitlaufen. Genutzt werden nur Server-Error-Events und optionales Trace-Sampling (`SENTRY_TRACES_SAMPLE_RATE`).

**Was gesendet und was entfernt/maskiert wird.** Sobald das Tracking aktiv ist, greifen mehrere Schichten der Datenminimierung, bevor ein Event die Installation verlässt:

- `send_default_pii=False` — keine Cookies, keine Client-IP.
- `include_local_variables=False` — Stack-Frame-Locals werden nicht erfasst (ein Frame könnte z. B. eine entschlüsselte Notiz enthalten).
- Ein serverseitiger `before_send`-Hook entfernt Request-Bodies, Cookies und Query-Strings, maskiert sensible Header und Schlüssel (`Authorization`, Passwörter, Tokens …), zieht Exception- und Log-Messages sowie die aus Log-Zeilen gebauten **Breadcrumbs** durch einen Text-Scrubber (Emails/Tokens werden zu Platzhaltern) und maskiert frei befüllbare `extra`-Werte.
- Ein `before_send_transaction`-Hook wendet dasselbe Scrubbing auf **Performance-Transactions** an (diese umgehen `before_send` im SDK): Request-Kontext inklusive `query_string` (Suchbegriffe können Klient*innen-Namen sein), Span-Beschreibungen und -Daten sowie der Transaction-Name werden mitbehandelt.

**Grenzen — offen benannt.** Die Scrubber arbeiten regel- und schlüsselbasiert (Best-Effort, Defense-in-Depth); sie ersetzen keine inhaltliche Prüfung jedes einzelnen Event-Feldes. Wer maximale Datensparsamkeit braucht, wählt eine der folgenden Stufen:

1. **`SENTRY_DSN` nicht setzen** — empfohlen für hochsensible Deployments, insbesondere auf älteren Ständen, deren SDK-Scrubber noch nicht alle oben genannten Schichten enthält: kein Error-Tracking-Prozessor, kein Datenabfluss; Fehler stehen weiterhin in den Server-Logs.
2. **`SENTRY_TRACES_SAMPLE_RATE=0`** gegen einen Tracker **innerhalb der eigenen Trust-Boundary** (z. B. selbst gehostet): schaltet Performance-Transactions ganz ab und beschränkt den Abfluss auf gescrubbte Error-Events an eine Instanz, die keine zusätzliche Drittpartei ist.

**Logs.** Die Server-Logs werden in **beiden** Formaten PII-gescrubbt — sowohl der Default `LOG_FORMAT=text` als auch `LOG_FORMAT=json` ziehen Messages, Tracebacks und die strukturierten Zusatzfelder durch denselben Scrubber (Mailadressen, Passwörter und Token-artige Werte werden zu Platzhaltern). **Nicht** gescrubbt werden zwei Felder, die technische Sicherheitsereignisse zuordenbar machen sollen und deshalb bewusst im Klartext stehen: die Client-IP (`remote_addr`) und der Browser-Kennstring (`user_agent`) einer CSP-Verstoßmeldung. Beide sind personenbeziehbar — wer eine kurze Log-Aufbewahrung braucht, plant sie dafür ein. Unverändert passieren außerdem die rein technischen Kennungen (`request_id`, `user_id`, `facility_id`, `user_pk`): Sie enthalten keinen Freitext, sondern verweisen nur auf einen Datensatz, der Scrubber hat dort nichts zu greifen. `user_pk` erscheint zum Beispiel in den Einladungs-Log-Zeilen, die bewusst weder Benutzernamen noch Mailadresse führen — ohne diese Kennung wäre der Eintrag keinem Konto mehr zuzuordnen. Für DSGVO-Kontexte mit Log-Aggregation empfiehlt sich weiterhin `LOG_FORMAT=json` (strukturiert, maschinenlesbar); die Datenminimierung ist aber nicht mehr an dieses Format gebunden.

**Selbst gehosteter Tracker (GlitchTip).** GlitchTip akzeptiert Standard-Sentry-DSNs, und dieses Projekt nutzt nur Kern-SDK-Features (Error-Events plus optionales Trace-Sampling). Diese Kombination wurde empirisch geprüft: Mit `sentry-sdk[django]` `2.64.0` gegen GlitchTip `6.2.1` (Docker-Image `glitchtip/glitchtip:6`) werden Server-Error-Events übertragen, plausibel geparst (Stacktrace, Tags, Request-Kontext) und als Issues angezeigt; Performance-Transactions (`SENTRY_TRACES_SAMPLE_RATE > 0`) werden ebenfalls **angenommen** und als Transaction-Gruppen einsortiert — sie werden also weder still verworfen noch lösen sie einen Fehler aus. Der serverseitige `before_send`/`before_send_transaction`-Scrubber greift dabei unverändert (keine Frame-Locals, gestrippte Query-Strings/Cookies/Request-Bodies); die weiter oben beschriebenen regel- und schlüsselbasierten Grenzen bleiben bestehen. Andere Versionskombinationen sind nicht getestet, und ein Fremdprojekt bleibt naturgemäß außerhalb der Kontrolle dieses Projekts.

So binden Sie einen selbst gehosteten Tracker an:

1. GlitchTip-Instanz betreiben und dort ein Projekt anlegen — GlitchTip zeigt dessen DSN im Format `https://<public_key>@<glitchtip-host>/<project_id>`.
2. Diesen Wert als `SENTRY_DSN` an die App geben — **keine Codeänderung nötig**. Optional `SENTRY_TRACES_SAMPLE_RATE` setzen (Default `0.1`; `0` schaltet Transactions ganz ab, siehe Eskalationsstufen oben).
3. Erwartung: Server-Error-Events erscheinen als Issues, optionales Trace-Sampling als Transaction-Gruppen. Es läuft ausschließlich das Server-SDK (kein Browser-SDK, kein Session Replay).

Betrieb der Tracker-Instanz (Setup, Backups, Retention, Upgrades) ist Betreiber-Infrastruktur und in der [GlitchTip-Dokumentation](https://glitchtip.com/documentation) beschrieben — hier **out of scope**; die Tracker-Datenbank gehört in dieselbe Trust-Boundary wie die App-Daten und braucht dieselben Zugriffs- und Löschregeln.

> **Kein Rechtsrat:** Dieser Abschnitt beschreibt technische Maßnahmen. Datenschutz-Folgenabschätzung und Verzeichnis der Verarbeitungstätigkeiten verbleiben beim Betreiber als Verantwortlichem.

### 5.7 Breach-Detection-Scan

Das Management-Kommando `detect_breaches` wertet das Audit-Log heuristisch auf Anomalien aus (fehlgeschlagene Login-Bursts, Massen-Exporte/-Löschungen, verteilte Login-Angriffe) und schreibt Treffer als Audit-Events; optional wird ein Webhook (`BREACH_NOTIFICATION_WEBHOOK_URL`) benachrichtigt. Die Fenster/Schwellen werden über die `BREACH_*`-Variablen gesteuert (siehe [ENV-Referenz in § 1](#vollständige-umgebungsvariablen-referenz) sowie [Ops-Runbook § 6.5b](ops-runbook.md)).

> **Bereits automatisiert (Refs #794):** Der mitgelieferte `cron`-Sidecar aus `docker-compose.prod.yml` führt diesen Job ab Werk aus. Das folgende Crontab-Beispiel gilt nur für Deployments **ohne** dieses Compose-File (Coolify, k8s, eigene Orchestrierung). Wer beides einrichtet, lässt den Job doppelt laufen — Details und Migrationsschritte: [Ops-Runbook § 3](ops-runbook.md#3-cron-jobs).

**Empfohlener Cron (stündlich, jeweils zur 30. Minute):**

```cron
30 * * * * cd /opt/anlaufstelle && \
  docker compose -f docker-compose.prod.yml exec -T web \
  python manage.py detect_breaches \
  >> /var/log/anlaufstelle-breach.log 2>&1
```

> **Hinweis:** Die vollständige, autoritative Liste aller Hintergrundjobs samt Zeitplan (Backup, Retention, Audit-Ketten-Verifikation, Statistik-Snapshots, Orphan-Cleanup, Breach-Scan, View-Refresh) steht im [Ops-Runbook § 3](ops-runbook.md#3-cron-jobs). Im mitgelieferten Compose-Stack laufen sie im `cron`-Sidecar (`deploy/crontab`), auf dem Dev-Server als systemd-Timer via `dev-ops/deploy/install-timers.sh` — in beiden Fällen der Breach-Scan stündlich zur 30. Minute.

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
| `DJANGO_AUDIT_HASH_KEY muss in Produktion gesetzt sein` | `DJANGO_AUDIT_HASH_KEY` (separat vom Secret Key) in `.env` setzen (siehe [Abschnitt 1.2](#schritt-2-umgebungsvariablen-konfigurieren)) |
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

### Problem: Berichte zeigen „unterdrückt" oder Gedankenstriche statt Zahlen

**Symptom:** Statt Zahlen stehen im Bericht Gedankenstriche („—" in der Vorschau der Datensatz-Berichte) bzw. der Hinweis „unterdrückt" (externer Bericht, Sachbericht-PDF) — teilweise in nahezu allen Zeilen.

**Das ist in aller Regel kein Defekt.** Aggregate unterhalb der K-Anonymitäts-Schwelle der Einrichtung werden ausgeblendet, damit sich aus einem Bericht keine einzelnen Personen ableiten lassen. In einer Einrichtung mit wenig Bestand — im Testbetrieb, kurz nach der Ersteinrichtung oder bei einem sehr kurzen Zeitraum — liegen praktisch alle Werte unter der Schwelle. Dass dabei zusätzlich eine sichtbare Zelle, eine Summenzeile oder eine ganze Aufschlüsselung wegfällt, gehört zum Verfahren: sonst ließe sich der unterdrückte Wert zurückrechnen.

**Benutzerseitige Doku:** [User-Guide § 7 — Statistik und Export](user-guide.md#7-statistik-und-export) — Darstellung je Dateiformat und das Vorgehen am Bildschirm (Zeitraum vergrößern, leeren Zeitraum ausschließen). Für die Administration kommen drei Punkte hinzu:

1. **Die Schwelle ist ein eigenes Setting, kein Nebeneffekt der Aufbewahrung.** Sie steht je Einrichtung im Admin unter **Core → Einstellungen** (Feld `k_anonymity_threshold`, Standard 5) und wirkt auf **jedes Berichts-Artefakt, das das Haus verlässt** — unabhängig davon, ob die K-Anonymisierung im Aufbewahrungslauf aktiviert ist. [§ 7.3](#73-löschfristen-und-enforce_retention) dokumentiert beide Felder gemeinsam im Retention-Kontext; sie hängen aber nicht voneinander ab.

2. **Zwei Ansichten sind bewusst nicht unterdrückt** und taugen deshalb zur Gegenprobe, ob überhaupt Daten vorhanden sind: die **Statistik-Übersicht am Bildschirm** und der PDF-Export im internen Modus (`?internal=1`, mit „INTERN"-Banner; beides nur Leitung/Administration). Dass die Übersicht andere Zahlen zeigt als der Bericht, ist erwartet. Was aus diesen internen Ansichten weitergegeben wird, verlässt die Zusicherung.

3. **Auch ohne sichtbare Unterdrückung bleibt eine Grenze:** Die Zusicherung gilt je Artefakt und je Zeitraum, nicht über mehrere Herausgaben hinweg — siehe [§ 8, „Herausgabe von Berichten: was Sie wissen müssen"](#8-statistik-snapshots-materialized-view).

> **Wichtig:** Das Herabsetzen der Schwelle ist eine Datenschutz-Entscheidung, kein Bedienschritt. Sie gilt für alle Berichte, die das Haus verlassen; dokumentieren Sie die Änderung entsprechend — im Audit-Log erscheint sie als „Einstellungen geändert" mit Alt- und Neu-Wert ([§ 7.4](#74-audit-log)).

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

Die Anwendung speichert **keine Klarnamen** in der Datenbank. Personendaten werden pseudonymisiert erfasst:

- Personen werden über interne IDs referenziert.
- Anzeigenamen in der Oberfläche sind konfigurierbare Pseudonyme.
- Daten der Kontaktstufe „Mit Zusatzangaben" (identifizierbar) sind nur für berechtigte Rollen (Leitung, Anwendungsbetreuung) sichtbar. Die Systemadministration (`super_admin`) sieht solche Daten nur über den `/system/`-Bereich, jeder solche Zugriff wird im AuditLog (`SYSTEM_VIEW`) protokolliert.

### 7.3 Löschfristen und `enforce_retention`

Löschfristen werden pro Einrichtung in den Einstellungen konfiguriert (siehe [Abschnitt 2.3](#23-einstellungen-pro-einrichtung)).

Das Management-Kommando `enforce_retention` setzt die konfigurierten Fristen durch, indem es abgelaufene Datensätze soft-löscht:

| Strategie | Einstellung | Standard |
|---|---|---|
| Anonyme Kontakte | `retention_anonymous_days` | 90 Tage |
| Kontakte der Stufe „Nur Pseudonym" | `retention_identified_days` | 365 Tage |
| Fälle der Stufe „Mit Zusatzangaben" (nach Abschluss) | `retention_qualified_days` | 3650 Tage (10 Jahre) |

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

> **Bereits automatisiert (Refs #794):** Der mitgelieferte `cron`-Sidecar aus `docker-compose.prod.yml` führt diesen Job ab Werk aus. Das folgende Crontab-Beispiel gilt nur für Deployments **ohne** dieses Compose-File (Coolify, k8s, eigene Orchestrierung). Wer beides einrichtet, lässt den Job doppelt laufen — Details und Migrationsschritte: [Ops-Runbook § 3](ops-runbook.md#3-cron-jobs).

**Als täglicher Cron einrichten (empfohlen):**

```cron
0 2 * * * cd /opt/anlaufstelle && \
  docker compose -f docker-compose.prod.yml exec -T web \
  python manage.py enforce_retention >> /var/log/anlaufstelle-retention.log 2>&1
```

Jede Ausführung, die Datensätze löscht, wird automatisch im Audit-Log protokolliert.

#### Retention-Dashboard

Unter [`/retention/`](https://github.com/anlaufstelle/app/blob/main/src/core/views/retention.py) steht ein **Retention-Dashboard** bereit, über das Leitung und Admin Löschvorschläge (generiert durch `enforce_retention`) effizient abarbeiten können. Refs #514, #515.

| Bulk-Aktion | Wirkung |
|---|---|
| **Approve** | Vorschlag wird zur Löschung freigegeben. Beim nächsten Retention-Run wird der Datensatz tatsächlich gelöscht (bzw. k-anonymisiert, siehe unten). |
| **Defer** | Vorschlag wird um eine konfigurierbare Frist zurückgestellt (Default 30 Tage) und erscheint im **nächsten Retention-Run erneut**. Nach Überschreiten von `retention_max_defer_count` (Default 2) wird entweder erzwungen entschieden oder — bei `retention_auto_approve_after_defer=True` — automatisch freigegeben. |
| **Reject** | Vorschlag wird dauerhaft verworfen (Datensatz bleibt erhalten). |

#### Legal Hold

Einzelne Datensätze können per **Legal Hold** vor automatischer Löschung geschützt werden (z. B. bei laufenden Verfahren, Prüfverfahren oder Auskunftsersuchen). Das Flag wird am Datensatz bzw. an einem `LegalHold`-Eintrag geführt und vom Retention-Job respektiert — ein Datensatz mit aktivem Legal Hold wird **nicht** zur Löschung vorgeschlagen, auch wenn die Aufbewahrungsfrist abgelaufen ist.

Legal Holds werden im Retention-Dashboard sowie im Django-Admin verwaltet (siehe [`src/core/models/retention.py`](https://github.com/anlaufstelle/app/blob/main/src/core/models/retention.py)).

#### K-Anonymisierung statt Hard-Delete

Als Alternative zur Hart-Löschung kann pro Einrichtung die **K-Anonymisierung** aktiviert werden (Refs #535).

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
| Daten der Stufe „Mit Zusatzangaben" eingesehen | Aufruf der **Personen-Detailseite** (`/clients/<id>/`) einer Person dieser Stufe — nicht der Aufruf der Personenliste, eines Ereignisses, eines Falls oder eines Suchtreffers |
| Export | Datenexport durch Benutzer — auch Datenauskünfte zu einzelnen Personen und der Aufruf des externen Berichts am Bildschirm. **Herausgegebene Berichtsdateien** (CSV/PDF/JSON/XML mit Berichtszeitraum) sind eine Teilmenge davon: nur sie unterliegen nicht der Aufbewahrungsfrist und erscheinen in der [Freigabe-Übersicht](#8-statistik-snapshots--materialized-view) |
| Löschung | Manuell oder durch `enforce_retention` |
| Stufenwechsel | Änderung des Kontaktstatus einer Person |
| Einstellungen geändert | Änderungen an Einrichtungseinstellungen |
| Datei-Download | Herunterladen eines Anhangs aus dem Dateitresor |
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

Unter **Core → Audit-Logs** (bzw. `/audit/`) können Logs nach Aktion, Einrichtung, Benutzer und Zeitraum gefiltert werden. Zugriff hat die Anwendungsbetreuung (`facility_admin`) für die eigene Einrichtung. Pre-Auth-AuditLogs ohne Facility-Bezug (z. B. `LOGIN_FAILED` bei unbekanntem Username) sind ausschließlich für die Systemadministration (`super_admin`) im `/system/`-Bereich sichtbar — siehe [ADR-007 Update 2026-05-10](adr/007-auditlog-append-only.md).

> ⚠️ **Betreiberhinweis für eigene Auswertungen: Anhänge tragen zwei `target_type`-Strings** (Refs #1738, [ADR-039](adr/039-owner-generische-dateianhaenge.md)). Mit Milestone 24 wurde das Modell `EventAttachment` zu `Attachment` umbenannt. Das Audit-Log ist append-only und über eine HMAC-Kette gesichert — **Alt-Zeilen werden deshalb nie migriert**. Wer Anhang-Aktionen (Downloads, abgewiesene Uploads, Löschungen) auswertet, muss beide Strings abfragen:
>
> ```sql
> SELECT * FROM core_auditlog WHERE target_type IN ('Attachment', 'EventAttachment');
> ```
>
> Dasselbe gilt für das `detail`-Feld: neue Zeilen tragen `owner_type` + `owner_id` (Werte `event`/`client`/`case`), Alt-Zeilen nur `event_id`. Download-Einträge zu **Ereignis**-Anhängen führen `event_id` zusätzlich weiter, damit Bestandsauswertungen nicht brechen. Ein Filter allein auf `Attachment` oder allein auf `detail->>'event_id'` liefert eine **stillschweigend unvollständige** Auswertung.

**Aufbewahrung des Audit-Logs:** Das Audit-Log **hat** eine eigene automatische Löschfrist innerhalb der Anwendung — sie ist bewusst länger als alle Fach-Fristen, damit Forensik nach einem spät bemerkten Vorfall noch Spuren findet ([ADR-021](adr/021-retention-modell.md)).

| Einstellung in `Settings` | Standard | Wirkung |
|---|---|---|
| `auditlog_retention_months` | 24 Monate | Bei jedem `enforce_retention`-Lauf werden Einträge gelöscht, die älter als diese Frist sind (kalendergenaue Monatsrechnung, keine 30-Tage-Näherung). `0` = **deaktiviert**, dann wächst das Log unbegrenzt. |

Von dieser Frist **ausgenommen** sind vier Klassen; sie bleiben unabhängig vom Alter erhalten (`PRUNE_EXEMPT_Q` in [`src/core/retention/audit_pruning.py`](https://github.com/anlaufstelle/app/blob/main/src/core/retention/audit_pruning.py)). Drei davon hängen an der Aktion (`PRUNE_EXEMPT_ACTIONS`):

- `SECURITY_VIOLATION` — Forensik-Nachweise der Breach-Detection,
- `RETENTION_RUN_COMPLETED` — der Marker, aus dem das Compliance-Dashboard den letzten erfolgreichen Retention-Lauf liest,
- `AUDIT_PRUNE_CHECKPOINT` — der Tombstone des Prunings selbst.

Hinzu kommt — **unabhängig von der Aktion** — die vierte Klasse: Einträge über eine **herausgegebene Berichtsdatei mit Berichtszeitraum** (CSV/PDF/JSON/XML). Ohne diese Ausnahme täuschte die Freigabe-Übersicht Vollständigkeit vor. Sie enthalten Zeitraum, Format, die auslösende Person und die erfassten Empfänger-/Zweckangaben (ungeprüfter Freitext) und werden dauerhaft aufbewahrt. Reine Bildschirm-Ansichten des externen Berichts und Auskünfte ohne Berichtszeitraum laufen zwar unter derselben Audit-Aktion, bleiben aber der Frist unterworfen — die Abgrenzung steht in [§ 8 „Herausgabe von Berichten"](#herausgabe-von-berichten-was-sie-wissen-müssen).

**Checkpoint-Mechanik:** Das Audit-Log ist über eine HMAC-Kette verkettet (jeder Eintrag verweist auf den vorherigen). Ein Pruning reißt diese Kette zwangsläufig auf. Damit die Integritätsprüfung diese Lücke nicht als Manipulation meldet, schreibt jeder Prune-Lauf einen `AUDIT_PRUNE_CHECKPOINT`-Eintrag, der die Kettengrenzen der gelöschten Einträge festhält und die Diskontinuität legitimiert. Die Kette selbst wird dabei **nie** umgeschrieben — das Log bleibt append-only.

> **Wichtig:** Die Frist wirkt nur, wenn `enforce_retention` tatsächlich läuft (siehe [§ 7.3](#73-löschfristen-und-enforce_retention)). Gelöschte Einträge sind endgültig weg — wer eine längere Nachweisführung braucht, legt eine **externe** Archivierungsstrategie fest (z. B. nach Empfehlung des BSI oder Ihrer Datenschutz-Folgenabschätzung) und exportiert das Log, bevor die Frist greift.
>
> **Datenschutz-Restrisiko:** Weil das Log append-only ist, wird es von der Anonymisierung einer Person **nicht** mitredigiert — Details und Gegenmaßnahmen in [`security-notes.md` § Audit-Log-Restdaten überleben die Art.-17-Löschung](security-notes.md#audit-log-restdaten-überleben-die-art-17-löschung-issue-1753).

### 7.5 Löschanträge (4-Augen-Prinzip)

Löschanträge für Personendaten werden im Vier-Augen-Prinzip bearbeitet: Ein Antrag muss von einer **zweiten berechtigten Person** genehmigt werden, bevor Daten endgültig gelöscht werden. Dies schützt vor versehentlicher oder unberechtigter Löschung — auch durch privilegierte Rollen selbst.

**Genehmiger-Pool über das Recht „Löschbestätigung"** (Refs #1053): Wer genehmigen darf, wird nicht aus der Rolle abgeleitet, sondern über das einzeln vergebbare Benutzer-Recht *Löschbestätigung* (`can_confirm_deletion`) kuratiert. Bei der Einführung erhalten bestehende Leitungen und Anwendungsbetreuungen das Recht automatisch; danach kann die Anwendungsbetreuung es unter **Verwaltung → Zugänge** gezielt vergeben oder entziehen — z. B. an eine erfahrene Fachkraft, damit auch bei einer einzelnen Leitung immer eine zweite Person verfügbar ist. Eigene Anträge kann auch ein:e Rechteträger:in nie selbst genehmigen (View-, Service- und Datenbank-Ebene erzwingen Antragsteller ≠ Genehmiger).

Jede Vergabe und jeder Entzug des Rechts sowie jede Genehmigung werden im unveränderlichen Audit-Log protokolliert. Existiert für einen offenen Antrag außer der antragstellenden Person keine aktive Person mit dem Recht, zeigt die Löschantragsliste eine deutliche Warnung („Kein möglicher Genehmiger") — vergeben Sie das Recht dann an eine weitere Person.

#### Wer den Reiter „Löschanträge" sieht (Refs #1736)

Löschanträge liegen als Reiter **„Löschanträge"** im Bereich **„Datenschutz"** (`/datenschutz/`). In der Sidebar steht nicht der einzelne Reiter, sondern der Bereichs-Eintrag „Datenschutz" — er erscheint, sobald mindestens einer seiner Reiter sichtbar ist. Die Sichtbarkeit des Reiters folgt nicht mehr der Rolle allein, sondern dem tatsächlichen Bedarf:

| Wer | Sieht den Reiter | Sieht in der Liste |
|---|---|---|
| Leitung, Anwendungsbetreuung | ja | **alle** Anträge der Einrichtung |
| Träger:in des Rechts *Löschbestätigung* (ohne Leitungsrolle) | ja | **alle** Anträge der Einrichtung |
| Antragstellende Person mit mindestens einem **offenen** eigenen Antrag | ja, solange der Antrag offen ist | **nur die eigenen** Anträge |
| Alle übrigen | nein ||

Der letzte Fall ist der **Rückkanal**: Wer einen Antrag stellt, findet ihn danach über die Navigation und über einen Hinweisbanner am betroffenen Datensatz wieder und sieht seinen Status — ohne dadurch Zugriff auf fremde Begründungen und Ziel-Pseudonyme zu bekommen. Ist der Antrag entschieden, verschwindet der Reiter wieder — und mit ihm der Bereichs-Eintrag „Datenschutz", sofern kein anderer Reiter des Bereichs sichtbar bleibt.

Am Datensatz selbst ist ein laufender Antrag jetzt ebenfalls sichtbar (Kennzeichnung in der Personenliste, Banner mit Datum und antragstellender Person im Personen-Detail); die Aktion „Löschen beantragen" ist währenddessen deaktiviert, ein zweiter Antrag ist nicht möglich.

#### Papierkorb: „Jetzt endgültig anonymisieren" (Refs #1737)

Eine genehmigte Löschung legt die Person in den **Papierkorb** (`/clients/trash/`, Anwendungsbetreuung). Dort bleibt sie wiederherstellbar, bis die Papierkorb-Frist (`client_trash_days`, Standard 30 Tage) abläuft und `enforce_retention` sie automatisch anonymisiert.

Neben „Wiederherstellen" steht dort die Aktion **„Jetzt endgültig anonymisieren"**. Sie zieht die Anonymisierung vor, statt auf die Frist zu warten:

| Aspekt | Verhalten |
|---|---|
| **Wirkung** | **Unwiderruflich.** Pseudonym, Notizen, Zusatzfelder sowie Fall- und Ereignisinhalte werden entfernt; es bleibt ein anonymes Statistik-Aggregat. Eine Wiederherstellung ist danach **nicht** mehr möglich. Ein Bestätigungsdialog nennt das ausdrücklich. |
| **Wozu** | (a) **Art.-17-Sofortlöschung**, wenn eine betroffene Person auf unverzüglicher Löschung besteht und 30 Tage Papierkorb nicht vertretbar sind. (b) **Auflösen von Konfigurations-Blockaden**: eine Person im Papierkorb behält ihren Dokumentationstyp und ihre Feldwerte und blockiert damit das Löschen dieses Typs bzw. der Feldvorlage. Bisher blieb nur Wiederherstellen (kurzzeitige Daten-Auferstehung) oder Warten. Die Blocker-Meldung der Konfigurations-Oberfläche weist den Papierkorb-Anteil jetzt aus und verlinkt dorthin. |
| **Grenze: Aufbewahrungssperre** | Besteht ein **aktiver Legal Hold** auf der Person oder auf einem ihrer Ereignisse, wird die Aktion mit einer Fehlermeldung abgewiesen — genau wie der nächtliche Retention-Lauf (Refs #1066, #1756). Eine manuelle Aktion darf einen Sperrvermerk nicht aushebeln. Erst Hold aufheben, dann anonymisieren. |
| **Grenze: nur im Papierkorb** | Die Aktion wirkt ausschließlich auf bereits soft-gelöschte Personen. Der Vier-Augen-Antragsweg bleibt der einzige Weg **in** den Papierkorb — es gibt keinen Direktpfad an der Genehmigung vorbei. |
| **Spur** | Audit-Eintrag `CLIENT_ANONYMIZED` mit `trigger="manual"`, unterscheidbar vom automatischen Lauf. |

Der Datensatz bleibt danach soft-gelöscht (anonymisiert, nicht wiederhergestellt) und verschwindet aus dem Papierkorb-Bestand nicht — er trägt nur keine Personendaten mehr. Personen im Papierkorb lassen sich seit #1737 auch nicht mehr per Direkt-URL bearbeiten (`/clients/<id>/edit/` antwortet 404, wie die Detailansicht).

#### Fall-Papierkorb und die beiden Löschwege (Refs #1818)

Seit [ADR-044](adr/044-fall-loeschpfad.md) lässt sich ein **einzelner Fall** löschen — bis dahin kannte Art. 17 nur zwei Größen (ein Ereignis oder die ganze Person). Der Weg ist derselbe wie bei Personen, mit vier Unterschieden, die Sie kennen sollten:

| Frage | Antwort |
|---|---|
| **Wer löscht?** | Fachkräfte und aufwärts, aus der Fallakte heraus (Menü „Weitere Aktionen" → **Löschen**). |
| **Vier Augen oder direkt?** | **Gestaffelt nach der Kontaktstufe der Person** — steht sie auf „Mit Zusatzangaben", entsteht ein Löschantrag, der von einer zweiten Person mit dem Recht *Löschbestätigung* bestätigt werden muss; sonst wandert der Fall direkt in den Papierkorb. Dieselbe Staffelung gilt schon beim Löschen eines **Ereignisses** derselben Person. Bestätigt wird über dieselbe Liste **Datenschutz → Löschanträge** wie bei Personen und Ereignissen. |
| **Welche Frist?** | **Keine eigene.** Es gilt `client_trash_days` (§ 2.3) — bewusst kein zweiter Fristbegriff, der in Datenschutzerklärung und Folgenabschätzung erklärt und gepflegt werden müsste. |
| **Was passiert nach Fristablauf?** | Der Fall wird **redigiert, nicht hart gelöscht**: Titel, Beschreibung, Zusatzfelder, Episoden, Wirkungsziele, Meilensteine und die fall-eigenen Dateien (samt verschlüsselter Ablage) sind weg; die Fall-Zeile bleibt als anonymes Aggregat stehen. Ein Hard-Delete risse Episoden, Ziele und Meilensteine per Kaskade mit. |

Den **Papierkorb** (`/cases/trash/`) öffnet die Anwendungsbetreuung über die Kopfzeile der Fallliste; er steht wie der Personen-Papierkorb in keiner Navigation. Dort stehen je Zeile **„Wiederherstellen"** (holt Episoden, Ziele und Anhänge vollständig zurück) und **„Jetzt endgültig anonymisieren"** — dieselbe Vorzieh-Aktion wie bei Personen, mit denselben Grenzen: nur aus dem Papierkorb heraus, nicht zweimal, und **nicht** bei einem aktiven Sperrvermerk (Legal Hold auf dem Fall, auf einem seiner Ereignisse oder auf der Person). Ein bereits endgültig anonymisierter Fall ist als solcher ausgewiesen und bietet kein „Wiederherstellen" mehr an — es holte nur eine leere Hülle zurück.

Drei Punkte, die im Betrieb regelmäßig gefragt werden:

- **Zugeordnete Kontakte bleiben bei der Person.** Sie blockieren die Löschung nicht und werden nicht mitgelöscht; ihre Fall-Zuordnung fällt erst bei der endgültigen Tilgung (bis dahin wäre ein Wiederherstellen verlustbehaftet).
- **Auch offene Fälle sind löschbar.** Ein irrtümlich angelegter Fall ist immer offen; ihn erst „abzuschließen" wäre eine falsche Aussage über einen Hilfeverlauf, den es nie gab.
- **Ein Fall im Papierkorb blockiert Konfigurationsänderungen** genau wie eine Person im Papierkorb: Er hält seinen Dokumentationstyp und seine Feldwerte fest. Die Lösch-Dialoge unter *Konfiguration* weisen diesen Anteil aus und verlinken den Fall-Papierkorb.

Spur im Protokoll: `CASE_SOFT_DELETED`, `CASE_RESTORED`, `CASE_ANONYMIZED` (mit `trigger="manual"` bzw. `"trash_days_expired"`), dazu die Antragsschritte `DELETION_REQUESTED`/`DELETION_APPROVED`/`DELETION_REJECTED`. Der **Falltitel steht in keinem Protokolleintrag** — das Log überlebt jede spätere Anonymisierung. Der nächtliche Aufbewahrungslauf meldet die zusätzliche Zeile `Anonymized N trash-expired case(s)`.

#### Personen-Zusammenführung: Anträge und Papierkorb-Tombstones (Refs #1876)

Seit [ADR-045](adr/045-personen-merge.md) lassen sich doppelt angelegte Personen **zusammenführen**. Für den Betrieb relevant:

- **Dieselbe Antragsliste, eigene Antragsart.** Ein Zusammenführungs-Antrag läuft über **Datenschutz → Löschanträge**, gestaffelt wie die Löschung: Trägt eine der beiden Personen „Mit Zusatzangaben", entscheidet eine zweite Person mit dem Recht *Löschbestätigung*; sind beide auf „Nur Pseudonym", darf die Leitung direkt zusammenführen. Die Liste weist Merge-Anträge als **Zusammenführung → Ziel** aus; die Prüfseite zeigt beide Akten mit Bestandszahlen. Pro Person läuft immer nur **ein** destruktiver Antrag — Löschung *oder* Zusammenführung.
- **Der Papierkorb kennt Tombstones.** Die aufgelöste Quelle liegt mit dem Vermerk „zusammengeführt in <Ziel>" unter `/clients/trash/`. Statt „Wiederherstellen" (das der Service abweist — ein bloßes Restore ließe die Akte ohne ihre gewanderten Daten auferstehen) steht dort **Zusammenführung aufheben**: Die gewanderten Ereignisse, Fälle, Aufgaben und übernommenen Anhänge kehren zurück, seit der Zusammenführung neu erfasste Einträge bleiben beim Ziel. Nach Ablauf von `client_trash_days` redigiert der normale Aufbewahrungslauf den Tombstone wie jede Papierkorb-Person; der Verweis auf das Ziel bleibt als Verbleibs-Nachweis stehen, aufheben lässt sich danach nichts mehr.
- **Offline-Geräte purgen die Quelle.** Der Merge widerruft die Offline-Bundles der aufgelösten Akte (personen-genau, wie bei einer Löschung). Offline bereits erfasste, noch nicht übertragene Ereignisse zur Quelle werden bewusst **nicht** automatisch auf das Ziel umgebucht — sie landen in der Dead-Letter-Liste der Offline-Konflikte zur Nachbearbeitung.
- **Kein Massenlösch-Alarm.** Eine Aufräum-Kampagne über Alt-Duplikate zählt nicht in die Massenlösch-Erkennung (`CLIENT_MERGED` ist eine eigene, nicht gezählte Vorgangsart) — es wird konsolidiert, nicht vernichtet.

Spur im Protokoll: `CLIENT_MERGED` (an der Quelle, mit Umkehr-Inventar im `detail`), beim Aufheben `CLIENT_RESTORED`; Antrag und Ablehnung nutzen die generischen `DELETION_REQUESTED`/`DELETION_REJECTED`, die Genehmigung schreibt bewusst **kein** `DELETION_APPROVED`.

### 7.6 Betroffenenrechte (Art. 15–20 DSGVO)

Für die Bearbeitung von Anfragen betroffener Personen stehen folgende administrative Möglichkeiten zur Verfügung:

| Recht | Maßnahme |
|---|---|
| Auskunft (Art. 15) | Datenauskunft als JSON oder PDF über die Personenansicht ziehen (Leitung oder Anwendungsbetreuung, Re-Authentifizierung erforderlich) — Umfang siehe unten |
| Berichtigung (Art. 16) | Felder als Anwendungsbetreuung im Admin-UI direkt bearbeiten |
| Löschung (Art. 17) | Löschantrag über die Anwendung stellen (4-Augen-Prinzip) |
| Datenportabilität (Art. 20) | Export-Funktion in der Anwendung |

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
| Gelöschte Ereignisse | Existenz und Zeitpunkt der noch nicht endgültig entfernten Ereignisse (Papierkorb) |
| Änderungshistorie | Aktion, Zeitpunkt, bearbeitende Person **und die Inhalte vorher/nachher** — auch für gelöschte Ereignisse |
| Löschanträge | Anträge auf Löschung von Ereignissen **und der Person selbst**, inkl. Begründung und 4-Augen-Prüfung |
| Berichtigungsanträge | Art.-16-Anträge zur Person (Refs #1796, [ADR-041](adr/041-berichtigungsworkflow-intern.md)) in **allen** Status (offen, genehmigt, abgelehnt): betroffenes Feld, bisheriger und gewünschter Wert (entschlüsselt), Begründung, Prüfvermerk, beantragende und prüfende Person, Zeitpunkte |
| Aufbewahrung | Sperrvermerke (Legal Holds, Art. 18) und offene Löschvorschläge samt Frist und Kategorie |
| Aufgaben | Titel, Beschreibung, Typ, Status, Priorität, Zuständigkeit, Fristen |
| Aktivitäten | Zeitstrom-Einträge zur Person, ihren Fällen, Ereignissen und Aufgaben |
| Merkliste | Zeitpunkte, zu denen die Personenakte aufgerufen bzw. als Favorit markiert wurde |
| Zusammenführungen | Herkunft nach Duplikat-Merges (Refs #1876, [ADR-045](adr/045-personen-merge.md) §7): welche Akte wann in diese überführt wurde und ob sie schon endgültig anonymisiert ist; die Auskunft der aufgelösten Akte nennt umgekehrt „Zusammengeführt in" |
| Zugriffsprotokoll | Audit-Einträge zur Person und ihren Ereignissen: Aktion, Zeitpunkt, Bezug |
| Dateianhänge | **Metadaten** je Anhang der Person, ihrer Fälle und ihrer Ereignisse (Refs #1738): Feldname und Feld-Slug, entschlüsselter Dateiname, Dateigröße, MIME-Typ, Zeitpunkt und hochladende Person, sowie der Zustand in der Versionskette (aktuell ja/nein, ersetzt am, entfernt am) — **vollständige Kette inklusive ersetzter und entfernter Glieder** |

**Zur Struktur der Anhang-Angaben (JSON):** personen-eigene Anhänge stehen unter dem Top-Level-Schlüssel `attachments` — er ist **immer** vorhanden, ggf. als leere Liste. Fall- und Ereignis-Anhänge stehen als `attachments` **im jeweiligen Eintrag** unter `cases[i]` bzw. `events[i]` und erscheinen dort nur, wenn nicht leer (das Bestandsformat für Datensätze ohne Dateien bleibt damit unverändert); gelöschte Ereignisse (`deleted_events`) tragen sie ebenso. Lässt sich ein Dateiname nicht entschlüsseln, steht dort `[verschlüsselt]` — die Auskunft erfindet keinen Namen. Der MIME-Typ ist der beim Upload **verifizierte** Typ; nur für Bestandszeilen ohne Erkennung fällt er auf den vom Browser gemeldeten zurück. In den Zusatzfeldern selbst erscheint statt des technischen Datei-Markers der Platzhalter `[Dateianhang – Angaben unter „Dateianhänge"]`. **Im PDF** stehen die personen-eigenen Anhänge als eigener Abschnitt „Dateianhänge"; Fall- und Ereignis-Anhänge stehen als Spalte „Dateianhänge" in der jeweiligen Tabelle.

**Zur Struktur der gelöschten Fälle (JSON):** sie stehen unter dem Top-Level-Schlüssel `deleted_cases` — er ist **immer** vorhanden, ggf. als leere Liste; `cases` führt ab #1818 nur noch die **lebenden** Fälle. Beide Listen tragen je Eintrag `is_deleted`, `deleted_at`, `deleted_by` und `anonymized_at`. Der letzte Wert beantwortet die Frage, die `is_deleted` allein offen lässt: Ist er gesetzt, sind die Inhalte des Falls **unwiderruflich entfernt** (die Zeile bleibt bewusst stehen, § 7.5); ist er leer, liegt der Fall nur im Papierkorb und ist wiederherstellbar. Episoden und Wirkungsziele eines gelöschten Falls bleiben in **einer** Liste (`episodes`, `outcome_goals`) — sie sind über den Falltitel beiden Abschnitten zuzuordnen. **Im PDF** stehen die Papierkorb-Fälle als eigener Abschnitt „Fälle im Papierkorb" direkt hinter „Fälle".

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

**Mechanik** ([`src/core/services/locking.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/locking.py)):

- Jedes Formular rendert das aktuelle `updated_at` als Hidden-Field.
- Beim Speichern prüft der Helper `check_version_conflict(instance, expected_updated_at)`, ob der Datensatz in der Zwischenzeit verändert wurde.
- Bei Konflikt wird eine `ValidationError` geworfen; der View leitet den Nutzer zurück mit der Meldung:
  > *„Der Datensatz wurde zwischenzeitlich bearbeitet. Bitte laden Sie die Seite neu."*

**Administrative Hinweise:**
- Es gibt keinen Admin-Schalter, um das Locking abzuschalten — der Schutz ist systemweit aktiv.
- Wenn Nutzer die Konfliktmeldung gehäuft sehen: Prozess prüfen (Mehrfachbearbeitung trennen, Workflows anpassen) statt das Feature zu umgehen.

### 7.9 Row Level Security (RLS)

Zusätzlich zum ORM-seitigen Facility-Scoping ist **PostgreSQL Row-Level-Security** auf **allen facility-scoped Tabellen** (derzeit 27, per Test ableitungsbasiert erzwungen) als **Defense-in-Depth** aktiviert. Ein fehlerhafter ORM-Query, der das Facility-Scoping vergisst, liefert dank RLS trotzdem keine fremden Daten. Refs #542, #586.

#### Funktionsweise

- Die Middleware [`FacilityScopeMiddleware`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/facility_scope.py) setzt pro Request die Postgres-Session-Variable `app.current_facility_id` via `SELECT set_config('app.current_facility_id', <id>, false)` (Session-Scope, nicht Transaction-Scope).
- Die RLS-Policies (Migration [`0047_postgres_rls_setup`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0047_postgres_rls_setup.py)) filtern jede Zeile über `facility_id = current_setting('app.current_facility_id', true)`.
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

1. **Materialized View** (`core_statistics_event_flat`) — aggregiert die aktuellen Event-Daten vor, damit die Statistik-Seite nicht bei jedem Aufruf alle Events neu scannen muss. Refs #544.
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

Implementierung: [`src/core/management/commands/refresh_statistics_view.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/refresh_statistics_view.py).

> **Bereits automatisiert (Refs #794):** Der mitgelieferte `cron`-Sidecar aus `docker-compose.prod.yml` führt diesen Job ab Werk aus. Das folgende Crontab-Beispiel gilt nur für Deployments **ohne** dieses Compose-File (Coolify, k8s, eigene Orchestrierung). Wer beides einrichtet, lässt den Job doppelt laufen — Details und Migrationsschritte: [Ops-Runbook § 3](ops-runbook.md#3-cron-jobs).

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

Anlaufstelle berechnet Statistiken (Dashboard, Halbjahresberichte, Beispiel-Sachbericht-PDFs) standardmäßig aus der Event-Tabelle (über die Materialized View aggregiert). Wenn die automatische Datenlöschung (`enforce_retention`) alte Events entfernt, würden diese aus den Statistiken verschwinden.

**Statistik-Snapshots** sichern monatliche Aggregate, bevor Events gelöscht werden. Die Auswertungen nutzen eine Hybrid-Logik: gespeicherte Snapshots für vergangene Monate, Live-Daten (aus der Materialized View) für den aktuellen Monat.

> **Wichtig: Der Snapshot-Schutz gilt nicht für jeden Bericht.** Gesichert werden die **Monats-Aggregate** — aus ihnen speisen sich die Statistik-Übersicht (Kennzahlen, Tabellen, Zeitverlauf), ihr PDF-Export und die Sachbericht-PDFs. Von den Berichten des Reiters **„Datensatz-Berichte"** ist dagegen allein der **Beispiel-Sachbericht** snapshot-fähig, und auch bei ihm nur das **PDF**: seine CSV-/JSON-/XML-Dateien rechnen — wie die aller übrigen Berichte dieses Reiters — bei **jedem** Aufruf neu aus dem aktuellen Datenbestand. Sobald `enforce_retention` einen vergangenen Zeitraum bereinigt hat, fallen deren Zahlen für diesen Zeitraum niedriger aus als beim ersten Abruf — bis auf null, und ohne Fehlermeldung, weil das kein Fehler ist. Wie stark, hängt am einzelnen Bericht: Er wertet Ereignisse, Fälle oder Personen aus und ändert sich erst, wenn die Datensätze **seiner** Grundgesamtheit gelöscht bzw. anonymisiert werden; welche das ist, benennt er über der Vorschau selbst. **Wer solche Dateien an Träger oder Kostenträger weitergibt, bewahrt die erzeugte Datei selbst auf** — identisch reproduzieren lässt sie sich später nicht. Woher ein Bericht liest, steht jeweils am Bericht selbst („Datenquelle: …"); was bei der Weitergabe sonst zu beachten ist, steht unten unter [„Herausgabe von Berichten"](#herausgabe-von-berichten-was-sie-wissen-müssen).

### Automatische Sicherung

Snapshots werden automatisch erstellt, wenn `enforce_retention` Events löscht — die betroffenen Monate werden unmittelbar vor der Löschung gesichert.

### Periodischer Cron-Job (empfohlen)

Zusätzlich empfiehlt es sich, Snapshots regelmäßig per Cron zu erstellen:

> **Bereits automatisiert (Refs #794):** Der mitgelieferte `cron`-Sidecar aus `docker-compose.prod.yml` führt diesen Job ab Werk aus. Das folgende Crontab-Beispiel gilt nur für Deployments **ohne** dieses Compose-File (Coolify, k8s, eigene Orchestrierung). Wer beides einrichtet, lässt den Job doppelt laufen — Details und Migrationsschritte: [Ops-Runbook § 3](ops-runbook.md#3-cron-jobs).

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
- **Top-Personen:** Die Rangliste der aktivsten Personen wird immer live berechnet und kann sich nach einer Löschung verändern.
- **Eindeutige Personen:** Die Zahl wird über den gesamten Berichtszeitraum **live** eindeutig gezählt und nicht aus den Monats-Aggregaten summiert (#1770). Für Zeiträume, deren Events nach Ablauf der Löschfristen entfernt wurden, fällt sie deshalb niedriger aus als die übrigen Zahlen desselben Berichts; die Artefakte weisen das an der Zahl aus.

### Herausgabe von Berichten: was Sie wissen müssen

Statistik-Artefakte sind k-anonymisiert (Werte unter der Schwelle werden unterdrückt). Diese Zusicherung gilt **je Artefakt und je Zeitraum** — nicht über mehrere Herausgaben hinweg. Daraus folgen drei Betriebsregeln.

**1. Sachbericht-PDF und Live-Export desselben Zeitraums nicht an denselben Empfänger geben.**

Die PDFs lesen aus den **Monats-Aggregaten** (Snapshots), der externe Bericht und die CSV-/JSON-/XML-Exporte fragen **live** die Ereignisse ab. Beide Wege sind gewollt: die Aggregate überdauern die Löschfristen, die Live-Abfrage zählt über den Gesamtzeitraum genauer. Genau deshalb sind sie aber nicht identisch — und **die Differenz beider Zahlen ist ein Fingerabdruck dessen, was zwischenzeitlich gelöscht wurde**. Wer beide Artefakte desselben Zeitraums nebeneinanderlegt, kann daraus auf einzelne gelöschte Datensätze schließen; k-Anonymität schützt davor nicht, weil beide Artefakte für sich vollständig anonym sind.

Jedes Artefakt weist deshalb **Quelle und Stand** aus („Datenquelle: Monats-Aggregate", „Stand der Monats-Aggregate: …"). Prüfen Sie diese Zeile, bevor Sie zwei Berichte in dieselbe Mappe legen.

**2. Berichtszeiträume nicht unter einem Monat wählen — und Überschneidungen im Blick behalten.**

Werden für dieselbe Einrichtung Berichte über Teilzeiträume **und** über deren Vereinigung herausgegeben (zwölf Monatsberichte plus ein Jahresbericht), lässt sich jede in einem Teilbericht unterdrückte Zelle aus der Summe der übrigen exakt zurückrechnen — auch dann, wenn in **keinem** der beteiligten Berichte eine einzige Zelle unterdrückt wurde. Das ist eine Eigenschaft des Verfahrens, kein Fehler: k-Anonymität ist eine Eigenschaft *einer* Tabelle über *einer* Grundgesamtheit.

Die **Freigabe-Übersicht** (Statistik → Reiter „Freigaben", Rolle: Leitung/Einrichtungsverwaltung) listet die bereits herausgegebenen Berichtszeiträume aus der Audit-Spur und markiert Überschneidungen. Sie verhindert nichts — sie zeigt Ihnen, wo Sie hinsehen müssen. Damit die Meldung bewertbar bleibt, erfassen Sie beim Export die optionalen Felder **Empfänger** und **Zweck**; überlappende Zeiträume an *verschiedene* Empfänger sind unkritisch, an denselben nicht.

> **Aufbewahrung — drei Klassen, nicht zwei.** Unter der Audit-Aktion „Export" laufen drei verschiedene Dinge, und nur das erste ist eine Herausgabe im Sinne dieser Übersicht:
>
> | Klasse | Beispiel | Aufbewahrung | In der Übersicht? |
> |---|---|---|---|
> | **Herausgegebene Berichtsdatei** | CSV-/PDF-/JSON-/XML-Download mit Berichtszeitraum | **unbefristet** (nicht von `auditlog_retention_months` gekappt) | ja |
> | Bericht am **Bildschirm** angesehen | Aufruf des externen Berichts im Browser — auch mit Zeitraum, auch bei jedem Neuladen | normale Frist | nein |
> | Auskunft/Download **ohne** Berichtszeitraum | Datenauskunft zu einer Person (Art. 15), DSGVO-Dokumente | normale Frist | nein |
>
> Ein Berichtszeitraum allein genügt also **nicht** — es muss auch eine Datei herausgegeben worden sein. Ohne die unbefristete Aufbewahrung der ersten Klasse täuschte die Übersicht Vollständigkeit vor; ohne die Befristung der zweiten wüchse das Audit-Log mit jedem Seitenaufruf unbegrenzt.
>
> Die unbefristet aufbewahrten Einträge enthalten Zeitraum, Format, Ihre Empfänger-/Zweckangaben und die auslösende Person. Zweckbindung: Nachweis herausgegebener Statistiken.
>
> **Empfänger und Zweck sind Freitextfelder.** Die Anwendung prüft ihren Inhalt nicht und kann ihn später nicht mehr ändern (das Audit-Log ist unveränderlich). Tragen Sie deshalb die **Organisation** ein, nicht den Namen einer Ansprechperson — sonst steht ein Personenname dauerhaft im Log.

**3. Die Übersicht hat zwei bekannte Lücken.**

**Erstens: sie beginnt mit ihrer Einführung.** Die Markierung, an der eine Herausgabe erkennbar ist, gibt es erst seit diesem Release. Herausgaben aus der Zeit davor stehen weiterhin im Audit-Log, lassen sich dort aber nicht als Herausgabe erkennen — sie erscheinen nicht in der Übersicht, sind nicht von der Frist ausgenommen und lösen keine Überschneidungswarnung aus. **Nachtragen ist technisch ausgeschlossen:** das Audit-Log ist unveränderlich und über eine Prüfsummen-Kette gegen nachträgliche Änderungen gesichert; ein Nachtragen wäre von einer Manipulation nicht zu unterscheiden. Rechnen Sie in den ersten Monaten nach dem Update also damit, dass ältere Herausgaben fehlen — die Seite weist darauf hin.

**Zweitens: sie endet beim Download.** Eine weitergeleitete PDF-Datei, ein abfotografierter Bildschirm oder ein per Hand abgeschriebener Wert erscheinen dort nicht. Das ist eine organisatorische Grenze.

### Admin-Oberfläche

Snapshots sind im Django-Admin unter **Statistik-Snapshots** einsehbar (nur Lesezugriff). Dort können Sie prüfen, welche Monate gesichert wurden und wann die letzte Aktualisierung stattfand.
