# Operations Runbook

Betriebshandbuch fuer Anlaufstelle. Ergaenzt das [Admin-Handbuch](admin-guide.md) um operative Prozeduren fuer Deployment, Rollback, Cron-Jobs, Log-Analyse und Troubleshooting.

**Stack:** Django 6.0 / Gunicorn / PostgreSQL 18 / Caddy 2 / Docker Compose

---

## Inhaltsverzeichnis

1. [Deployment-Ablauf](#1-deployment-ablauf)
2. [Rollback-Prozedur](#2-rollback-prozedur)
3. [Cron-Jobs](#3-cron-jobs)
4. [Log-Analyse](#4-log-analyse)
5. [Troubleshooting](#5-troubleshooting)
6. [Notfall-Prozeduren](#6-notfall-prozeduren)
7. [ClamAV-Virenscan](#7-clamav-virenscan)
8. [Dependencies aktualisieren](#8-dependencies-aktualisieren)
9. [Row Level Security (RLS)](#9-row-level-security-rls)
10. [Invite-Token-Hygiene](#10-invite-token-hygiene)
11. [Statistics Materialized View](#11-statistics-materialized-view)
12. [Trust-Boundary](#12-trust-boundary-refs-841)
13. [PostgreSQL-Major-Upgrade (16 → 18)](#13-postgresql-major-upgrade-16--18)

---

## 1. Deployment-Ablauf

### 1.1 Pre-Deploy-Checks

```bash
# Aktuelles Backup erstellen
./scripts/ops/backup.sh

# Backup-Datei verifizieren (nicht leer)
ls -lh backups/daily/

# Changelog auf Breaking Changes pruefen
# Release Notes lesen (neue Env-Vars, geaenderte Migrationen)

# Health-Check des laufenden Systems
curl -sf https://$DOMAIN/health/ | python3 -m json.tool

# Aktuelles Image-Tag notieren (fuer Rollback)
docker compose -f docker-compose.prod.yml images web

# Pflicht-Env-Vars des 3-Rollen-PostgreSQL-Modells pruefen (Refs #902, ADR-005/017):
# Der Bootstrap-Superuser ``postgres`` legt damit die App-Rolle (NOSUPERUSER,
# NOBYPASSRLS) und die Admin-Rolle (NOSUPERUSER, BYPASSRLS) an
# (deploy/postgres-init/01-app-role.sh). Fehlen sie oder stehen noch auf
# change-me-*, schlagen DB-Init bzw. der Migrate-Job fehl.
# Quelle: .env.example, docs/coolify-deployment.md.
for var in POSTGRES_BOOTSTRAP_PASSWORD POSTGRES_ADMIN_USER POSTGRES_ADMIN_PASSWORD; do
    val=$(grep -E "^${var}=" .env | cut -d= -f2-)
    case "$val" in
        ""|change-me*) echo "✗ $var fehlt oder steht noch auf Default"; ;;
        *)             echo "✓ $var gesetzt"; ;;
    esac
done
```

### 1.2 Deploy-Schritte

Refs #802 (C-34): Migrationen laufen als separater One-Shot-Job **vor** dem Rolling-Restart. Der Web-Entrypoint macht nur noch `collectstatic` + `gunicorn` — keine Migrationen mehr.

```bash
# 1. Neues Image ziehen
docker compose -f docker-compose.prod.yml pull web

# 2. Migrationen als One-Shot-Job ausfuehren (advisory lock haelt
#    parallele Jobs zurueck, lange RunPython blockiert keine Worker).
docker compose -f docker-compose.prod.yml run --rm \
    --entrypoint=/app/docker-migrate.sh web

# 3. Stack neu starten (web + caddy, DB bleibt).
#    Entrypoint laeuft jetzt nur noch collectstatic + gunicorn.
docker compose -f docker-compose.prod.yml up -d web caddy
```

**Erwartete Downtime:** ~3-5 Sekunden (nur Gunicorn-Restart). Migrationen laufen vorab im One-Shot-Job — der Web-Container faehrt erst hoch, wenn das Schema bereit ist. Bei mehreren Web-Replicas wartet keine mehr auf den Migrate-Lock.

> **Ownership-Normalisierung (Refs #1085):** Der One-Shot-Job fuehrt nach `migrate` zusaetzlich `normalize_db_ownership` aus. Da Migrationen als Admin-Rolle laufen (BYPASSRLS, Refs #863), entstehen frisch erstellte Tabellen admin-owned — `REASSIGN OWNED` uebertraegt sie auf den DB-Owner (App-Rolle), sonst bekaeme die App-Runtime auf einem frischen Cluster `permission denied`. Idempotent; auf Bestands-/Dev-Clustern (migrate laeuft als Owner) ein No-op. Ein Fehler bricht den Deploy fail-fast ab, bevor neue Web-Replicas live gehen.

### 1.3 Post-Deploy-Verifizierung

```bash
# Container-Status
docker compose -f docker-compose.prod.yml ps

# Migrations-Log pruefen
docker compose -f docker-compose.prod.yml logs web --tail=50

# Health-Check
curl -sf https://$DOMAIN/health/
# Erwartete Antwort: {"status": "ok", "database": "connected", "version": "..."}

# Migrations-Status verifizieren
docker compose -f docker-compose.prod.yml exec web \
  python manage.py showmigrations | grep '\[ \]'
# Keine ausstehenden Migrationen → leere Ausgabe

# Django Deployment-Check
docker compose -f docker-compose.prod.yml exec web \
  python manage.py check --deploy
```

---

## 2. Rollback-Prozedur

### 2.1 Image zuruecksetzen (ohne DB-Rollback)

Wenn die neue Version Anwendungsfehler hat, aber die Migrationen rueckwaertskompatibel sind:

```bash
# 1. In docker-compose.prod.yml das Image-Tag pinnen
#    image: ghcr.io/anlaufstelle/app:v1.2.3   (vorherige Version)

# 2. Stack neu starten
docker compose -f docker-compose.prod.yml up -d web

# 3. Verifizieren
curl -sf https://$DOMAIN/health/
```

### 2.2 Migration zurueckrollen

Wenn eine Migration Daten oder Schema inkompatibel veraendert hat:

```bash
# 1. Migrations-Status ansehen
docker compose -f docker-compose.prod.yml exec web \
  python manage.py showmigrations core

# 2. Auf eine bestimmte Migration zurueckrollen
#    Beispiel: zurueck auf Migration 0042
docker compose -f docker-compose.prod.yml exec web \
  python manage.py migrate core 0042

# 3. Danach altes Image deployen (siehe 2.1)
```

**Wichtig:** Nicht alle Migrationen sind rueckwaerts-kompatibel. `RunPython`-Operationen ohne `reverse_code` koennen nicht zurueckgerollt werden. Vor einem Update immer das Changelog auf irreversible Migrationen pruefen.

### 2.3 Vollstaendiger Rollback (DB-Restore)

Letzter Ausweg, wenn Migration-Rollback nicht moeglich:

```bash
# 1. Stack stoppen
docker compose -f docker-compose.prod.yml down

# 2. Backup wiederherstellen — DB plus optional Medien
./scripts/ops/restore.sh \
    backups/daily/anlaufstelle_YYYY-MM-DD_HHMMSS.sql.gz.enc \
    backups/daily/anlaufstelle_YYYY-MM-DD_HHMMSS_media.tar.gz.enc

# 3. Altes Image-Tag in docker-compose.prod.yml setzen

# 4. Stack starten
docker compose -f docker-compose.prod.yml up -d

# 5. Verifizieren
curl -sf https://$DOMAIN/health/
```

**Hinweis Medien (Refs #720):** Das `media:`-Volume in
[`docker-compose.prod.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.prod.yml)
persistiert `MEDIA_ROOT` ueber Container-Recreates hinweg. Beim
DB-Rollback muessen ggf. Medien (`*_media.tar.gz.enc`) mitwiederhergestellt
werden, wenn das Backup einen anderen Stand abbildet als die aktuelle
`media:`-Volume. Bei reinem Image-Rollback ohne DB-Aenderung bleibt
`media:` typischerweise gueltig — dann kein Medien-Restore noetig.

---

## 3. Cron-Jobs

### 3.1 Uebersicht

| Job | Empfohlene Zeit | Zweck |
|-----|----------------|-------|
| `backup.sh` | Taeglich 02:00 | Verschluesseltes DB- und Medien-Backup mit Rotation; optionaler Off-Site-Sync (Refs #720, #738) |
| `detect_breaches` | Stuendlich:30 | Heuristik-basierte Breach-Detection (failed-login-burst / mass-export / mass-delete) → AuditLog SECURITY_VIOLATION + optionaler Webhook (Refs #685) |
| `enforce_retention` | Taeglich 03:00 | Abgelaufene Events soft-loeschen, Clients anonymisieren |
| `create_statistics_snapshots` | Monatlich 1. Tag 04:00 | Monats-Aggregate sichern bevor Events geloescht werden |
| `refresh_statistics_view` | Stuendlich:15 | Materialized View `core_statistics_event_flat` aktualisieren (Statistik-Dashboard) |
| Invite-Token-Audit | Woechentlich So 05:00 | Verwaiste Invite-User-Konten aufspueren (siehe [10](#10-invite-token-hygiene)) |
| Health-Check | Alle 5 Minuten | Verfuegbarkeit pruefen |

### 3.2 Crontab-Eintraege

```cron
# Anlaufstelle – Operative Cron-Jobs
# In /etc/crontab oder via `crontab -e` auf dem Host einrichten

# Backup (taeglich 02:00, vor Retention)
0 2 * * * cd /opt/anlaufstelle && ./scripts/ops/backup.sh >> /var/log/anlaufstelle-backup.log 2>&1

# Retention-Durchsetzung (taeglich 03:00)
0 3 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T web python manage.py enforce_retention >> /var/log/anlaufstelle-retention.log 2>&1

# Statistik-Snapshots (monatlich am 1. um 04:00)
0 4 1 * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T web python manage.py create_statistics_snapshots >> /var/log/anlaufstelle-snapshots.log 2>&1

# Materialized-View-Refresh (stuendlich zur 15. Minute, Details siehe Abschnitt 11)
15 * * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T web python manage.py refresh_statistics_view >> /var/log/anlaufstelle-mv.log 2>&1

# Invite-Token-Audit (woechentlich So 05:00, Details siehe Abschnitt 10)
0 5 * * 0 cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT username, email, date_joined FROM core_user WHERE last_login IS NULL AND NOT (password LIKE 'pbkdf2_%') AND date_joined < now() - interval '7 days';" >> /var/log/anlaufstelle-invites.log 2>&1

# Health-Check (alle 5 Minuten)
*/5 * * * * curl -sf https://DOMAIN/health/ > /dev/null || echo "Anlaufstelle health check failed at $(date)" >> /var/log/anlaufstelle-health.log
```

**Reihenfolge beachten:** Backup (02:00) → Retention (03:00) → Snapshots (04:00, monatlich) → MV-Refresh (stuendlich). Backup muss vor Retention laufen, damit geloeschte Daten im Backup enthalten sind. Der MV-Refresh nutzt `CONCURRENTLY` und blockiert laufende Reader nicht.

### 3.3 Dev (systemd-Timer)

Auf `dev.anlaufstelle.app` laufen die Jobs **nicht** per Host-Crontab, sondern als
systemd-Timer, die [`deploy/install-timers.sh`](https://github.com/anlaufstelle/app/blob/main/dev-ops/deploy/install-timers.sh)
installiert — aufgerufen bei **jedem** Deploy durch [`deploy/deploy-dev.sh`](https://github.com/anlaufstelle/app/blob/main/dev-ops/deploy/deploy-dev.sh)
(idempotent). Bewusst kein Compose-Sidecar (geplantes One-Command-Startup, siehe #794).

> **Hinweis (Refs #980):** Frueher installierte `bootstrap.sh` die Timer inline. Da `bootstrap.sh` nur beim Erst-Provisioning laeuft, kamen nachtraeglich ergaenzte Timer nie auf den laufenden Host (Backup-Cron blieb aus). Die Installation liegt jetzt in `install-timers.sh` und laeuft bei jedem `deploy-dev.sh`.

| Timer | OnCalendar | Command |
|-------|-----------|---------|
| `anlaufstelle-backup.timer` | `*-*-* 02:00` | `dev-ops/deploy/backup.sh` |
| `anlaufstelle-retention.timer` | `*-*-* 03:00` | `… exec -T web python manage.py enforce_retention` |
| `anlaufstelle-snapshots.timer` | `*-*-01 04:00` | `… exec -T web python manage.py create_statistics_snapshots` |
| `anlaufstelle-breach.timer` | `*-*-* *:30` | `… exec -T web python manage.py detect_breaches` |
| `anlaufstelle-mv-refresh.timer` | `*-*-* *:15` | `… exec -T web python manage.py refresh_statistics_view` |

**Aktivierung auf bereits laufenden Servern:** Ein regulaerer `deploy-dev.sh`-Lauf
installiert die Timer mit. Einmalig sofort nachziehen geht auch direkt:

```bash
sudo bash /opt/anlaufstelle/dev-ops/deploy/install-timers.sh  # idempotent, als root
systemctl list-timers "anlaufstelle-*"                # 5 Timer mit NEXT-Zeit
systemctl start anlaufstelle-mv-refresh.service        # einmal manuell anstossen
journalctl -u anlaufstelle-mv-refresh.service -n 20
```

Den Lauf-Status je Job zeigt zusaetzlich das Compliance-Dashboard (`/system/compliance/`,
Kategorie „Hintergrundjobs") — `unknown`/`warning`/`critical`, wenn ein Timer nicht laeuft.

### 3.4 Manuelle Ausfuehrung

```bash
# Retention Testlauf (kein Loeschen)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py enforce_retention --dry-run

# Retention ausfuehren
docker compose -f docker-compose.prod.yml exec web \
  python manage.py enforce_retention

# Nur eine Einrichtung
docker compose -f docker-compose.prod.yml exec web \
  python manage.py enforce_retention --facility "Beratungsstelle Nord"

# Snapshots Testlauf
docker compose -f docker-compose.prod.yml exec web \
  python manage.py create_statistics_snapshots --dry-run

# Snapshots fuer alle vorhandenen Monate nachholen (Ersteinrichtung)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py create_statistics_snapshots --backfill

# Snapshot fuer bestimmten Monat
docker compose -f docker-compose.prod.yml exec web \
  python manage.py create_statistics_snapshots --year 2026 --month 3
```

---

## 4. Log-Analyse

### 4.1 Log-Zugriff

```bash
# Alle Container-Logs (letzte 200 Zeilen)
docker compose -f docker-compose.prod.yml logs --tail=200

# Web-Logs live verfolgen
docker compose -f docker-compose.prod.yml logs -f web

# Caddy-Logs (Reverse Proxy / TLS)
docker compose -f docker-compose.prod.yml logs -f caddy

# Datenbank-Logs
docker compose -f docker-compose.prod.yml logs -f db

# Logs seit bestimmtem Zeitpunkt
docker compose -f docker-compose.prod.yml logs --since="2026-03-30T08:00:00" web
```

### 4.2 JSON-Logging-Format

Mit `LOG_FORMAT=json` in `.env` gibt Django strukturierte JSON-Logs aus:

```json
{
  "ts": "2026-03-30T10:15:22.123456+00:00",
  "level": "INFO",
  "logger": "core",
  "module": "views",
  "message": "...",
  "request_id": "abc123",
  "user_id": 42,
  "facility_id": 1,
  "exception": "Traceback ..."
}
```

| Feld | Beschreibung |
|------|-------------|
| `ts` | ISO-8601-Zeitstempel (UTC) |
| `level` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `logger` | Logger-Name (`django`, `core`, `gunicorn.error`) |
| `module` | Python-Modul |
| `request_id` | Request-ID (falls vorhanden) |
| `user_id` | Eingeloggter Benutzer (falls vorhanden) |
| `facility_id` | Aktive Einrichtung (falls vorhanden) |
| `exception` | Vollstaendiger Traceback bei Fehlern |

**JSON-Logs filtern (mit jq):**

```bash
# Nur Fehler
docker compose -f docker-compose.prod.yml logs web --no-log-prefix 2>&1 \
  | grep '^{' | jq 'select(.level == "ERROR")'

# Fehler eines bestimmten Benutzers
docker compose -f docker-compose.prod.yml logs web --no-log-prefix 2>&1 \
  | grep '^{' | jq 'select(.user_id == 42)'

# Letzte Exceptions
docker compose -f docker-compose.prod.yml logs web --no-log-prefix 2>&1 \
  | grep '^{' | jq 'select(.exception != null) | {ts, message, exception}'
```

### 4.3 Typische Fehlermuster

| Log-Muster | Bedeutung | Aktion |
|------------|-----------|--------|
| `OperationalError: could not connect to server` | DB nicht erreichbar | DB-Container pruefen, siehe [5.1](#51-db-verbindung-fehlgeschlagen) |
| `ENCRYPTION_KEY must be set` | Env-Var fehlt | `.env` pruefen |
| `relation "core_*" does not exist` | Migrationen nicht gelaufen | `python manage.py migrate` |
| `OSError: write error` | Festplatte voll | Speicherplatz freigeben, siehe [5.5](#55-speicherplatz-voll) |
| `[CRITICAL] WORKER TIMEOUT` | Gunicorn-Worker-Timeout | Siehe [5.4](#54-gunicorn-timeout) |

---

## 5. Troubleshooting

### 5.1 DB-Verbindung fehlgeschlagen

**Symptom:** `OperationalError: could not connect to server` oder Health-Check meldet `"database": "unavailable"`.

```bash
# 1. DB-Container-Status
docker compose -f docker-compose.prod.yml ps db

# 2. DB-Healthcheck
docker inspect $(docker compose -f docker-compose.prod.yml ps -q db) | grep -A5 Health

# 3. Direkte Verbindung testen
docker compose -f docker-compose.prod.yml exec db \
  pg_isready -U $POSTGRES_USER

# 4. DB-Logs pruefen
docker compose -f docker-compose.prod.yml logs db --tail=50

# 5. DB-Container neu starten
docker compose -f docker-compose.prod.yml restart db

# 6. Warten bis healthy, dann Web neu starten
docker compose -f docker-compose.prod.yml restart web
```

### 5.2 Migration-Lock

**Symptom:** Deploy haengt bei `migrate`, kein Fortschritt in den Logs.

Der Entrypoint nutzt `pg_advisory_lock(1)` um parallele Migrationen zu verhindern. Ein Lock bleibt haengen, wenn ein vorheriger Migrate-Prozess abgestuerzt ist.

```bash
# 1. Haengende Locks anzeigen
docker compose -f docker-compose.prod.yml exec db \
  psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT pid, granted, objid FROM pg_locks WHERE locktype = 'advisory';"

# 2. Lock manuell freigeben
docker compose -f docker-compose.prod.yml exec db \
  psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT pg_advisory_unlock(1);"

# 3. Web-Container neu starten
docker compose -f docker-compose.prod.yml restart web
```

### 5.3 TLS-Zertifikat

**Symptom:** Browser zeigt Zertifikatsfehler, HTTPS nicht erreichbar.

```bash
# 1. Caddy-Logs pruefen
docker compose -f docker-compose.prod.yml logs caddy --tail=50

# 2. Haeufige Ursachen:
#    - DNS zeigt nicht auf Server-IP:
nslookup $DOMAIN

#    - Ports 80/443 blockiert:
curl -v http://$DOMAIN 2>&1 | head -20

#    - Let's Encrypt Rate Limit (max 5/Woche/Domain):
#      → 1 Woche warten oder Staging-CA nutzen

# 3. Caddy TLS-Daten zuruecksetzen (erzwingt Neubeantragung)
docker compose -f docker-compose.prod.yml exec caddy \
  caddy reload --config /etc/caddy/Caddyfile
```

### 5.4 Gunicorn-Timeout

**Symptom:** `[CRITICAL] WORKER TIMEOUT (pid: NNN)` in den Logs, Requests brechen mit 502 ab.

```bash
# 1. Aktuelle Konfiguration pruefen
docker compose -f docker-compose.prod.yml exec web \
  printenv | grep GUNICORN

# Defaults: GUNICORN_WORKERS=3, GUNICORN_TIMEOUT=30

# 2. Timeout erhoehen (in .env)
#    GUNICORN_TIMEOUT=60

# 3. Worker-Anzahl anpassen (Faustregel: 2 * CPU-Kerne + 1)
#    GUNICORN_WORKERS=5

# 4. Neustart
docker compose -f docker-compose.prod.yml restart web
```

**Root Cause suchen:** Lang laufende Requests deuten auf langsame DB-Queries. Django Debug Toolbar (nur Dev) oder `EXPLAIN ANALYZE` nutzen.

### 5.5 Speicherplatz voll

```bash
# Docker-Speicherverbrauch
docker system df

# Groesste Volumes
docker system df -v | grep -E 'VOLUME|pgdata|caddy'

# Alte Images und Build-Cache bereinigen
docker system prune -f

# Alte Backup-Dateien pruefen
du -sh /opt/anlaufstelle/backups/*/
```

### 5.6 Web-Container startet nicht (Restart-Loop)

```bash
# 1. Exit-Code und Logs
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs web --tail=100

# 2. Haeufige Fehler
#    "ImproperlyConfigured" → Fehlende Env-Vars in .env
#    "ENCRYPTION_KEY must be set" → ENCRYPTION_KEY in .env fehlt
#    "connection refused" → DB noch nicht ready

# 3. Env-Vars verifizieren
docker compose -f docker-compose.prod.yml exec web printenv | sort

# 4. Django-Check manuell ausfuehren
docker compose -f docker-compose.prod.yml exec web \
  python manage.py check --deploy
```

---

## 6. Notfall-Prozeduren

### 6.1 Schnellstop (gesamter Stack)

```bash
docker compose -f docker-compose.prod.yml down
```

**Nur Web stoppen (DB laeuft weiter):**

```bash
docker compose -f docker-compose.prod.yml stop web caddy
```

### 6.2 Sofort-Backup vor kritischem Eingriff

```bash
# Schnelles Backup (unverschluesselt, fuer sofortige Wiederherstellung)
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U $POSTGRES_USER -Fc $POSTGRES_DB \
  > /tmp/notfall_$(date +%Y%m%d_%H%M%S).dump

# Verschluesseltes Backup via Skript
./scripts/ops/backup.sh
```

### 6.3 Datenbank-Notfall-Zugriff

```bash
# Interaktive psql-Shell
docker compose -f docker-compose.prod.yml exec db \
  psql -U $POSTGRES_USER $POSTGRES_DB

# Einzel-Query ausfuehren
docker compose -f docker-compose.prod.yml exec db \
  psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT count(*) FROM core_event WHERE is_deleted = false;"
```

### 6.4 Passwort zuruecksetzen

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py changepassword <benutzername>
```

### 6.5 Sentry (optionales Error-Tracking)

Falls `SENTRY_DSN` in `.env` konfiguriert ist, werden unbehandelte Exceptions automatisch an Sentry gemeldet. Konfiguration:

```dotenv
SENTRY_DSN=https://...@sentry.io/...
SENTRY_TRACES_SAMPLE_RATE=0.1
```

### 6.5b DSGVO Art. 33/34 — Breach-Notification (Refs #685)

Anlaufstelle muss Datenschutzverletzungen innerhalb **72 Stunden** an die Aufsichtsbehoerde melden (Art. 33) und betroffene Personen direkt informieren, wenn ein hohes Risiko vorliegt (Art. 34). Der Code unterstuetzt das durch:

1. **Detection** — `python manage.py detect_breaches` laeuft stuendlich (Cron) und schreibt fuer jedes Finding einen `AuditLog.Action.SECURITY_VIOLATION`-Eintrag.
2. **Heuristiken** (alle konfigurierbar via Settings):
 - `failed_login_burst` — > `BREACH_FAILED_LOGIN_THRESHOLD` (Default 20) Fehlversuche pro User in `BREACH_DETECTION_WINDOW_MINUTES` (Default 60 min)
 - `mass_export` — > `BREACH_EXPORT_THRESHOLD` (Default 10) Exports pro User im Fenster
 - `mass_delete` — > `BREACH_DELETE_THRESHOLD` (Default 50) Deletes facility-weit im Fenster
3. **Optionaler Webhook** — `BREACH_NOTIFICATION_WEBHOOK_URL` env-Var; bekommt POST mit JSON-Payload (kind, count, threshold, audit_id) bei jedem neuen Finding.
4. **Deduplikation** — innerhalb von 24h wird derselbe Tatbestand (kind + user) nur einmal in `AuditLog` gemeldet.

#### Manueller Prozess bei SECURITY_VIOLATION

1. **T+0** — `AuditLog`-Detail pruefen, betroffene User/Daten identifizieren.
2. **T+24h** — Vorgang an [DPO/Datenschutzbeauftragten](https://github.com/anlaufstelle/app/blob/main/SECURITY.md) eskalieren, Risiko-Einschaetzung erstellen.
3. **T+72h** — Meldung an Aufsichtsbehoerde (Bayern: BayLDA; andere Bundeslaender analog) + ggf. Betroffenenmeldung.
4. **Post-Incident** — Lessons-Learned + Anpassung der Heuristik-Schwellen.

Cron-Eintrag (siehe § 3 Cron-Jobs):

```cron
30 * * * * cd /opt/anlaufstelle && \
    .venv/bin/python src/manage.py detect_breaches \
    --settings=anlaufstelle.settings.prod \
    >> /var/log/anlaufstelle-breach-detection.log 2>&1
```

### 6.5a Maintenance-Mode (Refs #700)

Vor und waehrend Migrations/Deploys den Stack auf 503 schalten, damit
User eine konsistente Custom-Page sehen statt Connection-Errors.

```bash
# Aktivieren (File-Flag wird angelegt)
make maintenance-on

# Deploy / Migration ausfuehren
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d

# Deaktivieren (File-Flag entfernen)
make maintenance-off
```

`make maintenance-on/off` nutzt `$MAINTENANCE_FLAG_FILE` (Env-Var) oder Default `/tmp/anlaufstelle.maintenance`. Whitelist:

- `/health/` und `/static/...` bleiben erreichbar (Loadbalancer / Reverse-Proxy)
- `MAINTENANCE_ALLOW_IPS` (komma-separiert) erlaubt Ops-Zugriff via `X-Forwarded-For` — z.B. fuer den eigenen Admin-Laptop waehrend des Wartungsfensters

`Retry-After`-Header (Default 600s) hilft Browsern + Loadbalancern, sinnvoll zu reagieren. Die Flag-Datei wird pro Worker max alle `MAINTENANCE_CACHE_TTL` Sekunden geprueft (Default 5s) — Per-Request-Kosten praktisch null wenn aus.

### 6.6a Off-Site-Backup-Sync (Refs #738)

Lokale Backups in `backups/daily/` ueberleben Container-Recreates, aber nicht den vollstaendigen Verlust des Prod-Hosts (Hardware-Defekt, Ransomware, Provider-Ausfall). `backup.sh` synct optional nach jeder Rotation in ein Off-Site-Ziel.

```bash
# .env: Off-Site-Ziel konfigurieren (eines von drei Formaten)
BACKUP_OFFSITE_TARGET=rclone:hetzner-bucket:anlaufstelle/backups
# oder
BACKUP_OFFSITE_TARGET=s3://anlaufstelle-backups/prod
# oder
BACKUP_OFFSITE_TARGET=backup-user@offsite.example.com:/backups/anlaufstelle
```

**Empfohlen:** Object-Lock / Write-Once-Policy am Bucket aktivieren — Ransomware kann verschluesselte Backups dann nicht ueberschreiben.

**Failure-Mode** (Refs #797): Wenn der Off-Site-Sync fehlschlaegt (Netz weg, Credentials ungueltig, Disk voll am Off-Site-Host), loggt `backup.sh` einen `ERROR`. Ein **State-File** `$BACKUP_STATE_DIR/.offsite_state` (Default: neben den Backups) zaehlt aufeinanderfolgende Fehler. Beim **zweiten** Fehler in Folge endet das Skript mit Exit-Code 1 — der Cron-/Coolify-Job wird damit rot und der Operator sieht den Vorfall sofort, ohne die Logs scannen zu muessen. Erfolgreiche Laeufe setzen den Counter zurueck.

**Sentry-Hook (optional):** Wenn `SENTRY_DSN` UND ein `BACKUP_SENTRY_HOOK`-Skript gesetzt sind, ruft `backup.sh` den Hook bei jedem Off-Site-Fehler auf (Argumente: Beschreibung, Off-Site-Ziel). Der Hook ist freie Operator-Wahl — z.B. `sentry-cli send-event` oder ein eigenes `curl`-Skript. Ohne Hook bleibt es bei Log + Exit-Code.

### 6.6 Backup-Restore-Drill (Refs #720, #739)

Verifiziert, dass das aktuellste Backup vollstaendig wiederherstellbar ist und die Verteidigungslinien (RLS, AuditLog-Immutability-Trigger) erhalten bleiben. **Empfehlung: quartalsweise per Cron + Alert-Mail bei Fehlschlag.**

Das Skript ist auf das **`dev-ops/deploy/backup.sh`**-Format ausgerichtet, das auf `dev.anlaufstelle.app` tatsaechlich laeuft (Refs #981): `pg_dump --format=custom` → `pg_restore`, AES-256-CBC, Quelle `$BACKUP_DIR/dump-*.pgc.enc` (Default `/var/backups/anl`), Stack `docker-compose.dev.yml`, Restore als Postgres-Superuser (`postgres`, via Local-Socket-Trust im db-Container — nötig für `CREATE DATABASE`, bypassed zugleich RLS). Das alte `scripts/ops/backup.sh`/`scripts/ops/restore.sh`-Schema (`backups/daily/*.sql.gz.enc`, plain SQL) ist davon unberuehrt.

**Kompatibilitaet Backup-Format × Restore-Pfad** — die beiden Backup-Welten sind *nicht* austauschbar; ein Drill gegen das jeweils fremde Format schlaegt fehl:

| Backup-Quelle | Dump-Format | Verschluesselung | Datei(en) | Restore-Tool | Restore-Drill |
|---|---|---|---|---|---|
| `dev-ops/deploy/backup.sh` (laeuft auf `dev.anlaufstelle.app`) | `pg_dump -Fc` (custom) | AES-256-CBC (kein HMAC) | `dump-*.pgc.enc` | `pg_restore` | ✓ `restore-drill.sh` ist genau hierauf ausgelegt (Glob `dump-*.pgc.enc`) |
| `scripts/ops/backup.sh` (Prod-Schema) | plain SQL + gzip | AES-256-CBC **+ HMAC-SHA256**-Sidecar (Refs #1024) | `*.sql.gz.enc` + `.hmac` | `psql` (`scripts/ops/restore.sh`) | ✗ nicht vom Drill abgedeckt — eigener Verify-Pfad (HMAC-Check vor Decrypt) |

Warum kein Cross-Restore: Der Drill sucht per Glob `dump-*.pgc.enc` und pipet in `pg_restore` — ein plain-SQL-Backup (`*.sql.gz.enc`) wird so gar nicht gefunden und ist auch kein gueltiges `pg_restore`-Archiv. Umgekehrt scheitert `scripts/ops/restore.sh` an einem `dump-*.pgc.enc` am fehlenden `.hmac`-Sidecar (`backup_verify_hmac`) und am Binaerformat. Wer den Prod-Pfad drillen will, nutzt `scripts/ops/backup.sh --verify` (HMAC + Probe-Decrypt), nicht `restore-drill.sh`.

Die Dump-Dateien sind `0600` und gehoeren root — daher **als root** ausfuehren:

```bash
sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh
```

| Schritt | Pruefung |
|---|---|
| 0 | Neuestes `dump-*.pgc.enc` in `$BACKUP_DIR` finden |
| 1 | Frische Wegwerf-DB (`anlaufstelle_drill_<pid>`) anlegen |
| 2 | Backup decrypten + `pg_restore` (custom format) in die Wegwerf-DB |
| 3 | Stichproben pro Tabelle (`core_facility`, `core_client`, `core_event`, `core_auditlog`, `core_workitem`) — Counts pruefen |
| 4 | RLS-Policy-Check — `SELECT COUNT(*) FROM pg_class WHERE relname LIKE 'core_%' AND relrowsecurity = true` muss `>= 18` ergeben (konservativer Mindestwert im Skript; tatsaechliche Anzahl liegt hoeher, siehe Abschnitt 9) |
| 5 | `auditlog_immutable`-Trigger existiert UND blockt Raw UPDATE |
| 6 | Cleanup: Wegwerf-DB drop (via trap) |
| 7 | **Bei vollem Erfolg:** `mark_restore_verified` im web-Container → Compliance-Marker |

Output: ein `OK` / `FAIL`-Eintrag pro Schritt. Exit-Code != 0 bei jedem `FAIL`. Bei `FAIL` sofort auf Backup-Integritaet pruefen — Trigger-Check fehlgeschlagen (Schritt 5) ist kritisch, weil die AuditLog-Immutability dann nach Restore nicht mehr greift.

> **Benigne Ausnahme in Schritt 2:** Das Dump enthält ein `REFRESH MATERIALIZED VIEW core_statistics_event_flat`, das beim Restore an `FORCE ROW LEVEL SECURITY` auf `core_event` scheitert (`pg_restore: errors ignored on restore: 1`). Das betrifft nur den Statistik-Cache, nicht die Nutzdaten — der mv-refresh-Timer baut ihn stündlich neu. Der Drill meldet das als `OK … MV-Refresh übersprungen (benigne)` und schlägt nur bei **anderen** Fehlern fehl.

**Compliance-Marker (Refs #919):** Schritt 7 schreibt bei Erfolg automatisch einen `RESTORE_VERIFIED`-AuditLog-Eintrag (`manage.py mark_restore_verified`), den das Compliance-Dashboard (`/system/compliance/`) als Alter-Indikator nutzt:

- ≤ 90 Tage → `ok`
- ≤ 180 Tage → `warning`
- älter → `critical` (DSGVO Art. 32 Abs. 1 lit. c verletzt — Wiederherstellbarkeit nicht mehr nachgewiesen).

Ohne je gelaufenen Drill bleibt das Dashboard auf `unknown`. Manuell nachziehen (z.B. nach einem dokumentierten Out-of-Band-Restore) geht weiterhin:

```bash
docker compose -f docker-compose.dev.yml --env-file .env.dev exec web \
    python manage.py mark_restore_verified --note "Out-of-Band-Restore $(date +%Y-%m-%d)"
```

**Cron-Vorschlag:**

```cron
# Quartalsweise (1. Monat im Quartal, 03:30 nach Backup um 02:00):
30 3 1 1,4,7,10 * cd /opt/anlaufstelle && bash scripts/ops/restore-drill.sh \
    >> /var/log/anlaufstelle-restore-drill.log 2>&1 \
    || mail -s "Restore-Drill FAIL" ops@example.com < /var/log/anlaufstelle-restore-drill.log
```

#### Manueller Sentinel-Drill fuer Medien-Volume-Persistenz

Ergaenzt `restore-drill.sh` um den Container-Recreate-Aspekt:

```bash
# 1. Sentinel-Datei in MEDIA_ROOT anlegen
docker compose -f docker-compose.prod.yml exec web \
    sh -c 'echo "drill-$(date +%s)" > /data/media/.restore-drill'

# 2. Stack neu erzeugen (Containers loeschen — Volume bleibt)
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d

# 3. Sentinel ueberprueft Volume-Persistenz
docker compose -f docker-compose.prod.yml exec web \
    cat /data/media/.restore-drill
# -> erwarteter Output: drill-<timestamp>
```

Sentinel danach loeschen: `rm /data/media/.restore-drill` im Container.

---

## 7. ClamAV-Virenscan

Jeder Datei-Upload in den Encrypted File Vault wird **vor** der Verschlüsselung
gegen einen ClamAV-Daemon gescannt ([Issue #524](https://github.com/anlaufstelle/app/issues/524)).
Die Produktions-Compose-Datei [`docker-compose.prod.yml`](../docker-compose.prod.yml)
startet den Scanner automatisch mit:

```yaml
clamav:
  image: clamav/clamav:stable
  volumes:
    - clamav-db:/var/lib/clamav
```

### 7.1 Konfiguration

Env-Vars (Defaults siehe [`src/anlaufstelle/settings/base.py`](../src/anlaufstelle/settings/base.py)):

```dotenv
CLAMAV_ENABLED=true        # Default in prod.py
CLAMAV_HOST=clamav         # Service-Name im Compose-Netzwerk
CLAMAV_PORT=3310
CLAMAV_TIMEOUT=30          # Sekunden
```

In Entwicklung/Test ist der Scan deaktiviert (`CLAMAV_ENABLED=false`), damit
keine ClamAV-Instanz erforderlich ist.

### 7.2 Fail-closed

Bei aktivem Scan und nicht erreichbarem Daemon wird jeder Upload mit einer
`ValidationError` abgewiesen und eine `AuditLog`-Zeile mit
`Action.SECURITY_VIOLATION` (`reason=virus_scanner_unavailable`) geschrieben.
Funde werden mit `reason=virus_detected` und der gemeldeten Signatur geloggt.

### 7.3 Healthcheck

`GET /health/` liefert zusätzlich `"virus_scanner": "connected"|"unavailable"|"disabled"`
sowie den Alias `"clamav": "ok"|"error"|"disabled"` (Refs #798).
Bei aktivem Scanner und unerreichbarem Daemon wird der Gesamtstatus auf
`"degraded"` gesetzt, der HTTP-Status bleibt 200 (die harte Sperre erfolgt
beim Upload, nicht am Healthcheck). Der Container-Healthcheck im Dockerfile
liest den JSON-`status` und meldet bei `degraded` ungesund — Coolify/k8s sehen
den Vorfall, der Last-Balancer reisst den Pod aber nicht raus.

### 7.4 Datenbank-Updates (Signaturen)

Das offizielle `clamav/clamav:stable`-Image aktualisiert die Signaturen
automatisch über `freshclam` im Container. Persistenz erfolgt im
benannten Volume `clamav-db`. Kein zusätzlicher Cron-Job notwendig.

### 7.5 Manueller Check

```bash
# Daemon erreichbar?
docker compose -f docker-compose.prod.yml exec web \
  python -c "from core.services.virus_scan import ping; print(ping())"

# EICAR-Test gegen den laufenden Scanner (manuell, nur zur Verifikation):
docker compose -f docker-compose.prod.yml exec clamav \
  sh -c 'echo "X5O!P%@AP[4\\PZX54(P^)7CC)7}\$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!\$H+H*" | clamdscan -'
```

---

## 8. Dependencies aktualisieren

Für reproduzierbare Builds nutzt das Projekt [pip-tools](https://github.com/jazzband/pip-tools).
Die direkten Abhängigkeiten stehen in `requirements.in` / `requirements-dev.in`,
die vollständig gepinnten Lock-Files `requirements.txt` / `requirements-dev.txt`
werden daraus generiert und committed.

### 8.1 Neue oder geänderte Abhängigkeit einpflegen

```bash
# 1. Abhängigkeit in requirements.in (Runtime) oder requirements-dev.in (Dev/Test)
#    eintragen — mit offener Range, z.B. "Django>=5.1,<5.2".

# 2. Lock-Files neu erzeugen (pip-tools muss installiert sein):
make deps-lock

# 3. Diff prüfen (welche transitiven Pakete kamen hinzu, welche Versionen wurden
#    angehoben?), auf bekannte Breaking Changes sichten.
git diff requirements.txt requirements-dev.txt

# 4. Lokal verifizieren
pip install -r requirements-dev.txt
make ci

# 5. .in- und .txt-Dateien zusammen committen (atomar).
git add requirements.in requirements-dev.in requirements.txt requirements-dev.txt
git commit -m "chore: bump <paket> auf <version>"
```

### 8.2 CI-Drift-Check

Der GitHub-Workflow [`test.yml`](../.github/workflows/test.yml) enthält einen
`lock-check`-Job, der `pip-compile` für beide Lock-Files ausführt und bei
Drift fehlschlägt — so kann niemand versehentlich `requirements.in` ändern,
ohne das Lock-File nachzuziehen.

Lokale Variante (ohne Commit):

```bash
make deps-check
```

### 8.3 Sicherheits-Updates (pip-audit)

Der `audit`-Job in CI läuft `pip-audit` gegen `requirements.txt`. Meldet der
Job CVEs, folgendes Vorgehen:

```bash
# 1. requirements.in: betroffenes Paket auf Mindestversion anheben
# 2. make deps-lock
# 3. make ci + manuelle Verifikation
# 4. Commit mit "security:"-Prefix, Referenz aufs Advisory
```

### 8.4 SBOM (Software Bill of Materials)

Der `audit`-Job erzeugt zusätzlich eine SBOM im CycloneDX-JSON-Format
(`sbom.json`) und lädt sie als Workflow-Artefakt hoch. Aufbewahrung: 90 Tage.

Die SBOM wird für Compliance-Anforderungen benötigt (öffentliche
Förderung, öffentliche Beschaffung, Lieferanten-Audits) und listet alle direkten und
transitiven Python-Dependencies mit Versionen, Lizenzen und CVE-Status zum
Build-Zeitpunkt.

```bash
# SBOM des letzten erfolgreichen Test-Workflow-Runs herunterladen
gh run download --name sbom-cyclonedx --dir /tmp/sbom-latest

# Inhalt prüfen
jq '.bomFormat, .specVersion, (.components | length)' /tmp/sbom-latest/sbom.json
```

Lokale Generierung (ohne CI):

```bash
pip install pip-audit
pip-audit -r requirements.txt --format cyclonedx-json -o sbom.json
```

### 8.5 Troubleshooting

| Problem | Ursache | Lösung |
|---------|---------|--------|
| `deps-check` schlägt fehl, obwohl nichts geändert wurde | Unterschiedliche pip-tools-Version zwischen Dev und CI | `pip install --upgrade pip-tools` und `make deps-lock` neu ausführen |
| `pip-compile` findet keine passende Version | Zu strenge Range-Constraint | Range in `.in` lockern, z.B. `>=X,<Y+1` |
| Transitive Dependency zieht vulnerable Version | Direkte Dependency fixiert alte Version | Direkte Dependency selbst einpflegen und anheben |

---

## 9. Row Level Security (RLS)

Seit v0.10.0 sind **22 facility-gescopte Tabellen** per PostgreSQL-RLS gegen
Cross-Facility-Leaks abgesichert (Defense-in-Depth unterhalb der Django-
Scoping-Schicht). Jede Policy vergleicht `facility_id` gegen die Session-
Variable `app.current_facility_id`, die von der
[`FacilityScopeMiddleware`](../src/core/middleware/facility_scope.py) pro
Request via `set_config(..., is_local=false)` gesetzt wird.

**Fail-closed:** Ist die Variable nicht gesetzt oder leer (`NULL`),
liefern die Policies **keine Zeilen** — auch nicht fuer den Tabellen-
eigentuemer (`FORCE ROW LEVEL SECURITY`). Bypass nur fuer Superuser-DB-
Rollen; der Django-DB-User darf deshalb in Produktion **kein** Superuser
sein.

**CI-Garantie (Refs #718):**
[`src/tests/test_rls_functional.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_rls_functional.py)
laedt eine dedizierte Postgres-Rolle ``rls_test_role`` (``NOSUPERUSER``)
und verifiziert per ``SET ROLE`` Cross-Tenant-0-Rows fuer Client, Event,
AuditLog und Activity. Damit ist der RLS-Schutz seit v0.11 funktional
in CI getestet — nicht nur die Existenz der Policies. Wenn der
Produktions-DB-User versehentlich Superuser wird, schlaegt der Cross-
Tenant-Test in CI **nicht** fehl (er nutzt die explizite Test-Rolle),
aber die Smoke-Query aus § 9.2 unten muss in Prod liefern: ``rolsuper =
false`` fuer den Django-DB-User.

### DB-Rollenmodell (Goldstandard)

**Dies ist die kanonische Beschreibung des PostgreSQL-Rollenmodells** — andere
Deployment-Dokumente verweisen hierher, statt sie zu wiederholen. Quelle der
Wahrheit ist das Init-Script
[`deploy/postgres-init/01-app-role.sh`](../deploy/postgres-init/01-app-role.sh)
(Refs #863,
#902,
[ADR-005](adr/005-facility-scoping-and-rls.md), [ADR-017](adr/017-deployment-topology.md)).

Das offizielle `postgres:18`-Image macht den per `POSTGRES_USER` des Containers
angelegten Bootstrap-Login automatisch zum Superuser. Dieser kann sich selbst
nicht entrechten — deshalb laeuft der Container mit `POSTGRES_USER=postgres` als
Bootstrap, und das Init-Script legt **zwei** entrechtete Application-Rollen an:

| Rolle (`.env`-Variable) | Beispiel-Name | Attribute | Wofuer |
|---|---|---|---|
| `postgres` (Bootstrap, `POSTGRES_BOOTSTRAP_PASSWORD`) | `postgres` | SUPERUSER | Nur DB-Init + Notfall-Wartung. **Nicht** fuer Runtime/Operator-Tasks. |
| `POSTGRES_USER` (App) | `anlaufstelle` | `NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB`, ist DB-Owner | Django-Runtime. RLS-Policies greifen scharf (`FORCE ROW LEVEL SECURITY`). |
| `POSTGRES_ADMIN_USER` (Admin/Wartung) | `anlaufstelle_admin` | `NOSUPERUSER BYPASSRLS NOCREATEROLE NOCREATEDB`, Mitglied der App-Rolle | `migrate`, `seed`, Retention-/Anonymisierungs-Cron. |

Eigenschaften, die das Init-Script code-treu garantiert:

- Beide Rollen sind **NOSUPERUSER** — kein Pfad zur Rechte-Eskalation.
- Die App-Rolle ist **NOBYPASSRLS** und DB-Owner; durch `FORCE ROW LEVEL
 SECURITY` greifen die Policies auch fuer den Owner (Fail-closed, § 9 oben).
- Die Admin-Rolle ist **BYPASSRLS** (fuer Migrationen/Wartung) und wird per
 `GRANT <app> TO <admin>` Mitglied der App-Rolle — noetig fuer `DROP POLICY`
 / `ALTER TABLE` in Migrationen, weil `BYPASSRLS` allein nicht fuer DDL reicht.
- Die Admin-Rolle erhaelt `GRANT SET ON PARAMETER session_replication_role`
 (PostgreSQL 15+) — sonst scheitert die DSGVO-Art.-17-Anonymisierung in Prod
 (Details § 9.5).

Operator-/Wartungs-Tasks connecten als Admin-Rolle ueber einen ENV-Override
(`POSTGRES_USER`/`POSTGRES_PASSWORD` auf die Admin-Werte); der Migrate-Job
[`docker-migrate.sh`](../docker-migrate.sh) macht das automatisch. Das
Rollen-Gate `check_db_roles` prueft die stabile App-Rolle ueber
`POSTGRES_APP_USER` (Refs #1017), damit der ENV-Override es nicht verwirrt.

**Pflicht-`.env`-Variablen** (Vorlage [`.env.example`](../.env.example)):
`POSTGRES_BOOTSTRAP_PASSWORD`, `POSTGRES_ADMIN_USER`, `POSTGRES_ADMIN_PASSWORD`
(zusaetzlich zu den App-Variablen). Der Pre-Deploy-Check in § 1.1 verifiziert,
dass sie gesetzt und nicht mehr auf `change-me-*` stehen.

**Verifikation** nach dem ersten Hochfahren — das Gate prueft beide Rollen:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py check_db_roles
# erwartet: App-Rolle  rolsuper=False, rolbypassrls=False
#           Admin-Rolle rolsuper=False, rolbypassrls=True   (Exit-Code 0)
```

### 9.1 Symptome eines RLS-Problems

- UI zeigt leere Listen, obwohl in der DB Zeilen vorhanden sind.
- Queries via ORM liefern plotzlich 0 Rows.
- In den Logs tauchen keine Exceptions auf — RLS filtert silent.
- `audit`-Tabellen bleiben leer fuer neue Facility-Zuweisungen.

### 9.2 Diagnose in `psql`

```sql
-- Aktuelle Session-Variable pruefen (NULL = Middleware hat nicht gesetzt)
SELECT current_setting('app.current_facility_id', true);

-- Liste aller RLS-aktivierten Tabellen
SELECT relname, relrowsecurity, relforcerowsecurity
FROM pg_class
WHERE relname LIKE 'core_%' AND relrowsecurity;

-- Policies einer Tabelle anzeigen
SELECT polname, polcmd, pg_get_expr(polqual, polrelid) AS using_clause
FROM pg_policy
WHERE polrelid = 'core_client'::regclass;
```

### 9.3 Manuelle Debug-Queries mit Facility-Kontext

Einzelne Debug-Queries gegen die DB ausfuehren, **ohne** RLS zu umgehen
gesetzt wird nur fuer die laufende Transaktion:

```sql
BEGIN;
SET LOCAL app.current_facility_id = '<facility-uuid-oder-pk>';
SELECT count(*) FROM core_client;
-- ... weitere Queries ...
COMMIT;  -- oder ROLLBACK, SET LOCAL endet mit der Transaktion
```

**Vorsicht:** Niemals `SET app.current_facility_id` (ohne `LOCAL`) in
einer Shared-Admin-Connection verwenden — der Wert bleibt sonst fuer
nachfolgende Queries dieser Session gueltig.

### 9.4 Haeufige Ursachen

| Befund | Ursache | Fix |
|--------|---------|-----|
| `current_setting(...)` liefert `NULL` | Request kam ohne Auth-User durch (z.B. Management-Command, Cron) | Bewusstes Setzen via `set_config` oder `SET LOCAL` vor Query |
| Variable ist gesetzt, aber Liste trotzdem leer | User ist auf falsche Facility gescopt | `SELECT facility_id FROM core_user WHERE id=<user_id>` pruefen |
| Migration `0047` nicht angewandt | `showmigrations core` zeigt `[ ]` | `python manage.py migrate core 0047` |
| Admin-Query ueber `./manage.py shell` liefert 0 Rows | Shell-Session setzt die Variable nicht | Vor Queries `connection.cursor().execute("SET app.current_facility_id = %s", [fid])` |

Refs #542,
#586.

### 9.5 Wartungsrolle: `SET ON PARAMETER session_replication_role` (A1.2)

Die DSGVO-Art.-17-Anonymisierung (`Client.anonymize`) und das Audit-Pruning
umgehen den Append-Only-Trigger transaktional via `bypass_replication_triggers()`,
das `session_replication_role = replica` setzt. Das ist ein **SUPERUSER-Parameter**;
die Wartungsrolle (`anlaufstelle_admin`, `NOSUPERUSER BYPASSRLS`, unter der seit
A1.1 der Retention-/Breach-Cron laeuft) darf ihn ab PostgreSQL 15 nur mit
explizitem `GRANT SET ON PARAMETER` setzen. Ohne den Grant scheitert eine echte
Anonymisierung in Prod mit `permission denied to set parameter
session_replication_role` (Refs
#1016 A1.2,
#1021).

**Fresh DBs:** [`deploy/postgres-init/01-app-role.sh`](../deploy/postgres-init/01-app-role.sh)
setzt den Grant automatisch beim ersten DB-Provisioning.

**Bestands-DBs (vor dem A1.2-Fix provisioniert):** Der Grant fehlt und muss
**einmalig als Superuser** nachgezogen werden (idempotent):

```sql
-- als Superuser (postgres), in der Anwendungs-DB:
GRANT SET ON PARAMETER session_replication_role TO anlaufstelle_admin;
```

**Verifikation** (Grant vorhanden + funktional):

```sql
-- 1. ACL-Eintrag pruefen -> erwartet u.a. 'anlaufstelle_admin=s/...'
SELECT parname, array_to_string(paracl, ',') AS acl
FROM pg_parameter_acl WHERE parname = 'session_replication_role';

-- 2. funktional als Wartungsrolle (session-lokal, harmlos):
SET ROLE anlaufstelle_admin;
SET session_replication_role = replica;   -- muss 'SET' liefern, kein permission denied
RESET session_replication_role;
RESET ROLE;
```

**Status:** `dev.anlaufstelle.app` ist bereits versorgt (Grant vorhanden +
funktional verifiziert 2026-06-03). **Prod muss den Grant einmalig nachziehen**,
falls die DB vor dem A1.2-Fix angelegt wurde — am besten direkt nach dem
naechsten Deploy als Teil der Post-Deploy-Verifizierung (§ 1.3).

---

## 10. Invite-Token-Hygiene

Der Invite-Flow ([`src/core/services/security/invite.py`](../src/core/services/security/invite.py))
nutzt Djangos `default_token_generator` — **keine** persistente Token-Tabelle.
Der Setup-Link reitet auf `password_reset_confirm` und ist an
`PASSWORD_RESET_TIMEOUT` (Default: 3 Tage) gebunden. Nach Ablauf wird der
Token vom Signer stillschweigend abgelehnt; es gibt **keine** Datenbank-
eintraege, die aufzuraeumen waeren.

**Was stattdessen bereinigt werden muss:** User-Konten, die per Invite
angelegt wurden und deren Empfaenger den Link nie einloeste — diese Konten
bleiben mit unusable Password liegen.

### 10.1 Stale Invite-User identifizieren

```sql
-- Alle User-Konten, die nie eingeloggt waren und kein nutzbares Passwort
-- gesetzt haben, aelter als 7 Tage. Ein Management-Command existiert
-- bewusst nicht — manuelle Sichtung vor dem Loeschen ist Pflicht
-- (DSGVO Art. 6 / 17).
SELECT id, username, email, date_joined, facility_id
FROM core_user
WHERE last_login IS NULL
  AND NOT (password LIKE 'pbkdf2_%')  -- unusable = set_unusable_password()
  AND date_joined < now() - interval '7 days'
ORDER BY date_joined;
```

### 10.2 Setup-Link erneut versenden

```bash
# Django-Shell: neuen Invite-Link an einen bestehenden User senden
docker compose -f docker-compose.prod.yml exec web \
  python manage.py shell -c "
from core.models import User
from core.services.invite import send_invite_email
send_invite_email(User.objects.get(username='<username>'))
"
```

### 10.3 Verwaiste Konten loeschen

Nach Ruecksprache mit der Einrichtungs-Leitung:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py shell -c "
from core.models import User
User.objects.filter(username='<username>').delete()
"
```

**Cron-Empfehlung:** Wochen-Audit als reine Report-Query (siehe Abschnitt
[3.2](#32-crontab-eintraege)) — kein automatisches Loeschen, da ein nicht
zugestellter Invite auch ein Mail-Problem sein kann.

Refs #528.

---

## 11. Statistics Materialized View

Seit v0.10.0 aggregiert die Materialized View `core_statistics_event_flat`
alle Event-Fakten fuer das Statistik-Dashboard vor. Der Refresh laeuft per
Management-Command und nutzt `REFRESH MATERIALIZED VIEW CONCURRENTLY`, damit
laufende Leser nicht blockiert werden
([`refresh_statistics_view.py`](../src/core/management/commands/refresh_statistics_view.py)).

### 11.1 Manueller Refresh

```bash
# Concurrent Refresh (Default — non-blocking)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py refresh_statistics_view

# Blocking Refresh erzwingen (fallback ohne UNIQUE-Index)
docker compose -f docker-compose.prod.yml exec web \
  python manage.py refresh_statistics_view --no-concurrent
```

Faellt `CONCURRENTLY` fehl (z.B. weil der UNIQUE-Index nach einer
Schema-Migration fehlt), logged das Command eine Warnung und wiederholt
den Refresh ohne `CONCURRENTLY` — die View bleibt also konsistent, aber
Reader blockieren fuer die Dauer des Refreshs.

### 11.2 Cron-Empfehlung

Stuendlich zur 15. Minute (siehe [3.2](#32-crontab-eintraege)). Bei hoher
Event-Frequenz auf alle 15 Minuten verdichtbar; bei low-traffic auch
alle 6 Stunden ausreichend. Das Dashboard toleriert leichten Staleness.

### 11.3 Abgrenzung zu Snapshots

| Artefakt | Zweck | Befehl | Frequenz |
|----------|-------|--------|----------|
| `core_statistics_event_flat` (MV) | Aktuelle Laufzeit-Aggregate fuer Dashboard | `refresh_statistics_view` | Stuendlich |
| `core_statisticssnapshot` (Tabelle) | Persistente Monats-Aggregate (ueberdauert Event-Retention) | `create_statistics_snapshots` | Monatlich |

Beide Jobs sind unabhaengig — Snapshots bleiben auch ohne MV korrekt,
die MV kann auch ohne Snapshots refreshed werden.

### 11.4 Health-Check

```sql
-- Letzten Refresh-Zeitpunkt nachsehen
SELECT schemaname, matviewname, last_refresh
FROM pg_stat_user_tables
JOIN pg_matviews ON relname = matviewname
WHERE matviewname = 'core_statistics_event_flat';

-- Zeilenzahl pruefen (sollte ungefaehr core_event count entsprechen)
SELECT count(*) FROM core_statistics_event_flat;
```

Refs #544.

---

## 12. Trust-Boundary (Refs #841)

### 12.1 SECURE_PROXY_SSL_HEADER

`settings/prod.py:48` setzt:

```python
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
```

Damit erkennt Django HTTPS auch hinter dem Caddy-Proxy. Voraussetzung: **Caddy strippt eingehende `X-Forwarded-Proto`-Header vom Client und setzt sie selbst** auf den tatsaechlichen Verbindungsstatus.

**Wichtig:** Wenn die App **direkt** exponiert wird (ohne Reverse-Proxy), MUSS dieses Setting entfernt werden. Sonst kann ein Angreifer `X-Forwarded-Proto: https` selbst setzen — `request.is_secure()` liefert dann `True`, obwohl die Verbindung Klartext ist; HSTS-/Secure-Cookie-Logik wird ausgehebelt.

Caddy in `Caddyfile` ist so konfiguriert, dass es diesen Header beim Forwarden ueberschreibt:

```
reverse_proxy web:8000 {
    header_up X-Forwarded-Proto {scheme}
}
```

### 12.2 TRUSTED_PROXY_HOPS

Ergaenzend setzt `.env` (Default `1`):

```
TRUSTED_PROXY_HOPS=1
```

Erklaerung in `.env.example`. Bei CDN+Caddy auf `2` setzen, sonst greift der IP-Spoof-Schutz aus [`signals.audit.get_client_ip`](https://github.com/anlaufstelle/app/blob/main/src/core/signals/audit.py) den falschen Eintrag.

---

## 13. PostgreSQL-Major-Upgrade (16 → 18)

Refs #1039. PostgreSQL-Major-Versionen haben **inkompatible Datenverzeichnisse** — ein In-Place-Start des neuen Images auf dem alten Volume ist unmöglich. Gewählter und lokal durchgespielter Pfad: **dump → frisches PG18-Cluster → restore**. (`pg_upgrade` verworfen: das Alpine-Image enthält nur eine Binär-Major-Version, und PG18-`initdb` aktiviert Data-Checksums per Default — ein 16er-Cluster ohne Checksums wäre dafür erst zu konvertieren.)

**Geändertes Image-Layout ab 18:** `PGDATA` liegt unter `/var/lib/postgresql/18/docker`, das Image deklariert `VOLUME /var/lib/postgresql`. Die Compose-Dateien mounten seit #1039 deshalb das Elternverzeichnis (`…:/var/lib/postgresql` statt `…/data`). Startet man den neuen Stand versehentlich gegen ein altes 16er-Volume, bricht der Entrypoint **kontrolliert ohne Datenverlust** ab („there appears to be PostgreSQL data in: /var/lib/postgresql" + Hinweistext).

### 13.1 Prod/Staging — Daten erhalten (Wartungsfenster)

Staging identisch mit `docker-compose.staging.yml`, `.env.staging` und Volume `*_pgdata_staging`. Reihenfolge ist sicherheitskritisch: **Rollen vor Daten** — das Init-Script legt App-/Admin-Rolle im frischen Cluster an, bevor der Restore Ownership/Grants wiederherstellt (ADR-020).

```bash
# 0. Maintenance-Mode aktivieren (§ 6) + Web stoppen — DB bleibt fuer den Dump erreichbar
docker compose -f docker-compose.prod.yml stop web

# 1. Finales verschluesseltes Backup (§ 1.1) + unverschluesselter Arbeits-Dump.
#    Dump als Admin-Rolle (BYPASSRLS) ueber den Container-Socket — wie backup.sh.
./scripts/ops/backup.sh
docker compose -f docker-compose.prod.yml exec -T db \
    sh -c 'pg_dump -U "$ADMIN_DB_USER" -Fc -Z 6 "$POSTGRES_DB"' > /tmp/upgrade-pg16.dump

# 2. DB stoppen, Volume-Tarball als byte-genauen Rollback-Anker ziehen
#    (Volume-Name pruefen: docker volume ls | grep pgdata)
docker compose -f docker-compose.prod.yml stop db
docker run --rm -v anlaufstelle_pgdata:/from -v "$PWD":/to alpine \
    tar czf /to/pgdata-pg16.tar.gz -C /from .

# 3. Alten DB-Container + Volume entfernen — erst NACH Schritt 1+2!
docker compose -f docker-compose.prod.yml rm -f db
docker volume rm anlaufstelle_pgdata

# 4. Neuen Release-Stand einspielen (postgres:18-alpine + neuer Mount)
git pull   # bzw. Release-Paket

# 5. Nur DB hochfahren: frisches initdb (PG 18, Checksums on) + Init-Script
#    legt App-/Admin-Rolle an. Log muss fehlerfrei sein.
docker compose -f docker-compose.prod.yml up -d db
docker compose -f docker-compose.prod.yml logs db | grep -icE 'error|fatal'   # 0

# 6. Restore als Bootstrap-Superuser via Container-Socket (bypasst RLS/FORCE-RLS,
#    wie scripts/ops/restore-drill.sh; --clean/--if-exists ist re-run-sicher)
docker compose -f docker-compose.prod.yml exec -T db \
    sh -c 'pg_restore -U postgres -d "$POSTGRES_DB" --clean --if-exists' < /tmp/upgrade-pg16.dump

# 7. Verifizieren: Rollen-Gate, RLS aktiv, Stichproben
docker compose -f docker-compose.prod.yml run --rm -T \
    --entrypoint python web manage.py check_db_roles        # Exit 0
docker compose -f docker-compose.prod.yml exec -T db sh -c \
    'psql -U postgres -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity"'   # > 0

# 8. Migrationen als One-Shot (i. d. R. No-op) + Stack hoch + Health (§ 1.2/1.3)
docker compose -f docker-compose.prod.yml run --rm --entrypoint=/app/docker-migrate.sh web
docker compose -f docker-compose.prod.yml up -d web caddy
curl -sf https://$DOMAIN/health/ | python3 -m json.tool

# 9. Maintenance-Mode aus. Arbeits-Dump enthaelt KLARTEXT-Daten — sofort loeschen;
#    Tarball nach Bewaehrungsfrist (z. B. 1 Woche) entsorgen.
rm /tmp/upgrade-pg16.dump
```

Meldet `pg_restore` eine Warnung bei `REFRESH MATERIALIZED VIEW core_statistics_event_flat`, sind die Daten trotzdem vollständig — danach einmal den MV-Refresh ausführen (§ 11).

### 13.2 Rollback

Primär über den Volume-Tarball (exakter 16er-Byte-Stand), alternativ verschlüsseltes Backup (§ 6):

```bash
# 1. Compose-Stand zurueckdrehen: postgres:16-alpine + Mount …:/var/lib/postgresql/data
git checkout <vorheriger-stand> -- docker-compose.prod.yml

# 2. 18er-Volume entfernen, 16er-Bytes wiederherstellen
docker compose -f docker-compose.prod.yml rm -f db
docker volume rm anlaufstelle_pgdata && docker volume create anlaufstelle_pgdata
docker run --rm -v anlaufstelle_pgdata:/to -v "$PWD":/from alpine \
    tar xzf /from/pgdata-pg16.tar.gz -C /to

# 3. Stack hochfahren + Health
docker compose -f docker-compose.prod.yml up -d
```

### 13.3 dev.anlaufstelle.app — Daten wegwerfbar

`pgdata_dev` enthält nur Demo-Seed (Refs #1039): kein Restore nötig.

```bash
cd /opt/anlaufstelle
docker compose -f docker-compose.dev.yml --env-file .env.dev stop db web
docker compose -f docker-compose.dev.yml --env-file .env.dev rm -f db
docker volume rm anlaufstelle_pgdata_dev
# Neuen Stand deployen (make deploy-dev vom Operator-Rechner): frisches
# 18er-Cluster + Rollen via Init-Script, Migrate-Job, Stack-Start.
# Danach Demo-Daten: make dev-seed SEED_ARGS="--flush --scale=medium"
```

Der komplette Pfad (Init-Rollen vor Daten, Superuser-Restore, `check_db_roles` Exit 0, FORCE-RLS aktiv, App-Rolle ohne Bypass, MV-Refresh, Rollback auf 16) wurde im Zuge von #1039 lokal in Docker durchgespielt.

---

## Kurzreferenz

```text
Backup erstellen          ./scripts/ops/backup.sh
Backup wiederherstellen   ./scripts/ops/restore.sh <datei.sql.gz.enc>
Health-Check              curl -sf https://$DOMAIN/health/
Container-Status          docker compose -f docker-compose.prod.yml ps
Live-Logs                 docker compose -f docker-compose.prod.yml logs -f web
Migrations-Status         docker compose -f docker-compose.prod.yml exec web python manage.py showmigrations
Django Deploy-Check       docker compose -f docker-compose.prod.yml exec web python manage.py check --deploy
Retention Testlauf        docker compose -f docker-compose.prod.yml exec web python manage.py enforce_retention --dry-run
Snapshot Testlauf         docker compose -f docker-compose.prod.yml exec web python manage.py create_statistics_snapshots --dry-run
MV-Refresh                docker compose -f docker-compose.prod.yml exec web python manage.py refresh_statistics_view
RLS-Session-Var pruefen   psql -c "SELECT current_setting('app.current_facility_id', true);"
Stack stoppen             docker compose -f docker-compose.prod.yml down
Stack starten             docker compose -f docker-compose.prod.yml up -d
Lock-Files regenerieren   make deps-lock
Lock-File-Drift prüfen    make deps-check
```
