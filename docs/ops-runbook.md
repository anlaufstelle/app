# Operations Runbook

Betriebshandbuch fuer Anlaufstelle. Ergaenzt das [Admin-Handbuch](admin-guide.md) um operative Prozeduren fuer Deployment, Rollback, Cron-Jobs, Log-Analyse und Troubleshooting.

**Stack:** Django 5.1 / Gunicorn / PostgreSQL 16 / Caddy 2 / Docker Compose

---

## Inhaltsverzeichnis

1. [Deployment-Ablauf](#1-deployment-ablauf)
2. [Rollback-Prozedur](#2-rollback-prozedur)
3. [Cron-Jobs](#3-cron-jobs)
4. [Log-Analyse](#4-log-analyse)
5. [Troubleshooting](#5-troubleshooting)
6. [Notfall-Prozeduren](#6-notfall-prozeduren)

---

## 1. Deployment-Ablauf

### 1.1 Pre-Deploy-Checks

```bash
# Aktuelles Backup erstellen
./scripts/backup.sh

# Backup-Datei verifizieren (nicht leer)
ls -lh backups/daily/

# Changelog auf Breaking Changes pruefen
# Release Notes lesen (neue Env-Vars, geaenderte Migrationen)

# Health-Check des laufenden Systems
curl -sf https://$DOMAIN/health/ | python3 -m json.tool

# Aktuelles Image-Tag notieren (fuer Rollback)
docker compose -f docker-compose.prod.yml images web
```

### 1.2 Deploy-Schritte

```bash
# 1. Neues Image ziehen
docker compose -f docker-compose.prod.yml pull web

# 2. Stack neu starten (web + caddy, DB bleibt)
docker compose -f docker-compose.prod.yml up -d web caddy

# Entrypoint fuehrt automatisch aus:
#   - pg_advisory_lock(1) → migrate --noinput → pg_advisory_unlock(1)
#   - collectstatic --noinput
#   - gunicorn start
```

**Erwartete Downtime:** ~10-30 Sekunden (Migrationen + Gunicorn-Start).

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

# 2. Backup wiederherstellen
./scripts/restore.sh backups/daily/anlaufstelle_YYYY-MM-DD_HHMMSS.sql.gz.enc

# 3. Altes Image-Tag in docker-compose.prod.yml setzen

# 4. Stack starten
docker compose -f docker-compose.prod.yml up -d

# 5. Verifizieren
curl -sf https://$DOMAIN/health/
```

---

## 3. Cron-Jobs

### 3.1 Uebersicht

| Job | Empfohlene Zeit | Zweck |
|-----|----------------|-------|
| `enforce_retention` | Taeglich 03:00 | Abgelaufene Events soft-loeschen, Clients anonymisieren |
| `create_statistics_snapshots` | Monatlich 1. Tag 04:00 | Monats-Aggregate sichern bevor Events geloescht werden |
| `backup.sh` | Taeglich 02:00 | Verschluesseltes DB-Backup mit Rotation |
| Health-Check | Alle 5 Minuten | Verfuegbarkeit pruefen |

### 3.2 Crontab-Eintraege

```cron
# Anlaufstelle – Operative Cron-Jobs
# In /etc/crontab oder via `crontab -e` auf dem Host einrichten

# Backup (taeglich 02:00, vor Retention)
0 2 * * * cd /opt/anlaufstelle && ./scripts/backup.sh >> /var/log/anlaufstelle-backup.log 2>&1

# Retention-Durchsetzung (taeglich 03:00)
0 3 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T web python manage.py enforce_retention >> /var/log/anlaufstelle-retention.log 2>&1

# Statistik-Snapshots (monatlich am 1. um 04:00)
0 4 1 * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T web python manage.py create_statistics_snapshots >> /var/log/anlaufstelle-snapshots.log 2>&1

# Health-Check (alle 5 Minuten)
*/5 * * * * curl -sf https://DOMAIN/health/ > /dev/null || echo "Anlaufstelle health check failed at $(date)" >> /var/log/anlaufstelle-health.log
```

**Reihenfolge beachten:** Backup (02:00) → Retention (03:00) → Snapshots (04:00). Backup muss vor Retention laufen, damit geloeschte Daten im Backup enthalten sind.

### 3.3 Manuelle Ausfuehrung

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
./scripts/backup.sh
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

---

## Kurzreferenz

```text
Backup erstellen          ./scripts/backup.sh
Backup wiederherstellen   ./scripts/restore.sh <datei.sql.gz.enc>
Health-Check              curl -sf https://$DOMAIN/health/
Container-Status          docker compose -f docker-compose.prod.yml ps
Live-Logs                 docker compose -f docker-compose.prod.yml logs -f web
Migrations-Status         docker compose -f docker-compose.prod.yml exec web python manage.py showmigrations
Django Deploy-Check       docker compose -f docker-compose.prod.yml exec web python manage.py check --deploy
Retention Testlauf        docker compose -f docker-compose.prod.yml exec web python manage.py enforce_retention --dry-run
Snapshot Testlauf         docker compose -f docker-compose.prod.yml exec web python manage.py create_statistics_snapshots --dry-run
Stack stoppen             docker compose -f docker-compose.prod.yml down
Stack starten             docker compose -f docker-compose.prod.yml up -d
```
