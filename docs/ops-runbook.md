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
7. [ClamAV-Virenscan](#7-clamav-virenscan)
8. [Dependencies aktualisieren](#8-dependencies-aktualisieren)
9. [Row Level Security (RLS)](#9-row-level-security-rls)
10. [Invite-Token-Hygiene](#10-invite-token-hygiene)
11. [Statistics Materialized View](#11-statistics-materialized-view)

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

# 2. Backup wiederherstellen — DB plus optional Medien
./scripts/restore.sh \
    backups/daily/anlaufstelle_YYYY-MM-DD_HHMMSS.sql.gz.enc \
    backups/daily/anlaufstelle_YYYY-MM-DD_HHMMSS_media.tar.gz.enc

# 3. Altes Image-Tag in docker-compose.prod.yml setzen

# 4. Stack starten
docker compose -f docker-compose.prod.yml up -d

# 5. Verifizieren
curl -sf https://$DOMAIN/health/
```

**Hinweis Medien (Refs #720):** Das `media:`-Volume in
[`docker-compose.prod.yml`](https://github.com/tobiasnix/anlaufstelle/blob/main/docker-compose.prod.yml)
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
| `backup.sh` | Taeglich 02:00 | Verschluesseltes DB- und Medien-Backup mit Rotation; optionaler Off-Site-Sync (Refs [#720](https://github.com/tobiasnix/anlaufstelle/issues/720), [#738](https://github.com/tobiasnix/anlaufstelle/issues/738)) |
| `enforce_retention` | Taeglich 03:00 | Abgelaufene Events soft-loeschen, Clients anonymisieren |
| `create_statistics_snapshots` | Monatlich 1. Tag 04:00 | Monats-Aggregate sichern bevor Events geloescht werden |
| `refresh_statistics_view` | Stuendlich :15 | Materialized View `core_statistics_event_flat` aktualisieren (Statistik-Dashboard) |
| Invite-Token-Audit | Woechentlich So 05:00 | Verwaiste Invite-User-Konten aufspueren (siehe [10](#10-invite-token-hygiene)) |
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

# Materialized-View-Refresh (stuendlich zur 15. Minute, Details siehe Abschnitt 11)
15 * * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T web python manage.py refresh_statistics_view >> /var/log/anlaufstelle-mv.log 2>&1

# Invite-Token-Audit (woechentlich So 05:00, Details siehe Abschnitt 10)
0 5 * * 0 cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT username, email, date_joined FROM core_user WHERE last_login IS NULL AND NOT (password LIKE 'pbkdf2_%') AND date_joined < now() - interval '7 days';" >> /var/log/anlaufstelle-invites.log 2>&1

# Health-Check (alle 5 Minuten)
*/5 * * * * curl -sf https://DOMAIN/health/ > /dev/null || echo "Anlaufstelle health check failed at $(date)" >> /var/log/anlaufstelle-health.log
```

**Reihenfolge beachten:** Backup (02:00) → Retention (03:00) → Snapshots (04:00, monatlich) → MV-Refresh (stuendlich). Backup muss vor Retention laufen, damit geloeschte Daten im Backup enthalten sind. Der MV-Refresh nutzt `CONCURRENTLY` und blockiert laufende Reader nicht.

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

### 6.6a Off-Site-Backup-Sync (Refs [#738](https://github.com/tobiasnix/anlaufstelle/issues/738))

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

**Failure-Mode:** Wenn der Off-Site-Sync fehlschlaegt (Netz weg, Credentials ungueltig, Disk voll am Off-Site-Host), loggt `backup.sh` einen `ERROR` und beendet das Skript trotzdem mit Exit-Code 0 — das lokale Backup darf nicht abgewertet werden, weil das Off-Site-Ziel kurzfristig nicht erreichbar ist. Das Cron-Wrapper-Log (`/var/log/anlaufstelle-backup.log`) muss daher monitored werden, damit eine wiederholte ERROR-Meldung nicht uebersehen wird.

### 6.6 Backup-Restore-Drill (Refs [#720](https://github.com/tobiasnix/anlaufstelle/issues/720), [#739](https://github.com/tobiasnix/anlaufstelle/issues/739))

Verifiziert, dass das aktuellste Backup vollstaendig wiederherstellbar ist und alle Verteidigungslinien (RLS, AuditLog-Trigger, Medien-Volume) erhalten bleiben. **Empfehlung: quartalsweise per Cron + Alert-Mail bei Fehlschlag.**

```bash
./scripts/restore-drill.sh
```

Das Skript laeuft 7 Schritte gegen das neueste DB- und Medien-Backup in `backups/daily/`:

| Schritt | Pruefung |
|---|---|
| 1 | Frische Temp-DB (`anlaufstelle_drill_<pid>`) anlegen |
| 2 | Neuestes `*.sql.gz.enc` decrypten + restoren |
| 3 | Stichproben pro Tabelle (`core_facility`, `core_client`, `core_event`, `core_auditlog`, `core_workitem`) — Counts pruefen |
| 4 | RLS-Policy-Check — `SELECT COUNT FROM pg_class WHERE relrowsecurity = true` muss `>= 18` ergeben |
| 5 | `auditlog_immutable`-Trigger existiert UND blockt Raw UPDATE |
| 6 | Neuestes `*_media.tar.gz.enc` enthaelt mindestens 1 Eintrag |
| 7 | Cleanup: Temp-DB drop |

Output: ein `OK` / `FAIL`-Eintrag pro Schritt. Exit-Code != 0 bei jedem `FAIL`. Bei `FAIL` sofort auf Backup-Integritaet pruefen — Trigger-Check fehlgeschlagen (Schritt 5) ist kritisch, weil die AuditLog-Immutability dann nach Restore nicht mehr greift.

**Cron-Vorschlag:**

```cron
# Quartalsweise (1. Monat im Quartal, 03:30 nach Backup um 02:00):
30 3 1 1,4,7,10 * cd /opt/anlaufstelle && ./scripts/restore-drill.sh \
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
gegen einen ClamAV-Daemon gescannt ([Issue #524](https://github.com/tobiasnix/anlaufstelle/issues/524)).
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

`GET /health/` liefert zusätzlich `"virus_scanner": "connected"|"unavailable"|"disabled"`.
Bei aktivem Scanner und unerreichbarem Daemon wird der Gesamtstatus auf
`"degraded"` gesetzt, der HTTP-Status bleibt 200 (die harte Sperre erfolgt
beim Upload, nicht am Healthcheck).

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

Die SBOM wird für Compliance-Anforderungen benötigt (NLnet-Förderung,
öffentliche Beschaffung, Lieferanten-Audits) und listet alle direkten und
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

Seit v0.10.0 sind **19 facility-gescopte Tabellen** per PostgreSQL-RLS gegen
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

**CI-Garantie (Refs [#718](https://github.com/tobiasnix/anlaufstelle/issues/718)):**
[`src/tests/test_rls_functional.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/tests/test_rls_functional.py)
laedt eine dedizierte Postgres-Rolle ``rls_test_role`` (``NOSUPERUSER``)
und verifiziert per ``SET ROLE`` Cross-Tenant-0-Rows fuer Client, Event,
AuditLog und Activity. Damit ist der RLS-Schutz seit v0.11 funktional
in CI getestet — nicht nur die Existenz der Policies. Wenn der
Produktions-DB-User versehentlich Superuser wird, schlaegt der Cross-
Tenant-Test in CI **nicht** fehl (er nutzt die explizite Test-Rolle),
aber die Smoke-Query aus § 9.2 unten muss in Prod liefern: ``rolsuper =
false`` fuer den Django-DB-User.

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

Einzelne Debug-Queries gegen die DB ausfuehren, **ohne** RLS zu umgehen —
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

Refs [#542](https://github.com/tobiasnix/anlaufstelle/issues/542),
[#586](https://github.com/tobiasnix/anlaufstelle/issues/586).

---

## 10. Invite-Token-Hygiene

Der Invite-Flow ([`src/core/services/invite.py`](../src/core/services/invite.py))
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

Refs [#528](https://github.com/tobiasnix/anlaufstelle/issues/528).

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

Refs [#544](https://github.com/tobiasnix/anlaufstelle/issues/544).

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
MV-Refresh                docker compose -f docker-compose.prod.yml exec web python manage.py refresh_statistics_view
RLS-Session-Var pruefen   psql -c "SELECT current_setting('app.current_facility_id', true);"
Stack stoppen             docker compose -f docker-compose.prod.yml down
Stack starten             docker compose -f docker-compose.prod.yml up -d
Lock-Files regenerieren   make deps-lock
Lock-File-Drift prüfen    make deps-check
```
