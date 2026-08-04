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

> **Erstinbetriebnahme statt Deploy?** Dieser Abschnitt beschreibt den *laufenden* Deploy. Für die **erste** Installation gibt es [`scripts/initial-setup.sh`](../scripts/initial-setup.sh) (Refs #793): DB starten und auf Bereitschaft warten → Migrate-One-Shot → Stack hochfahren → `check --deploy` → `check_db_roles` → Super-Admin-Bootstrap. Idempotent und fail-loud; Anleitung in [docs/admin-guide.md § 1 Schritt 4](admin-guide.md).

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

> **Ownership-Normalisierung (Refs #1085, #1026):** Der One-Shot-Job fuehrt nach `migrate` weiterhin `normalize_db_ownership` aus. Seit A1.4 ist der **DB-Owner die Admin-Rolle** selbst; da `migrate` als eben diese Rolle laeuft, ist `REASSIGN OWNED BY current_user TO db_owner` ein **No-op** (`current_user == db_owner`). Die App-Runtime bekommt ihre Tabellen-Rechte nicht mehr ueber Ownership, sondern per DML-Grant (`ALTER DEFAULT PRIVILEGES … TO app` im Init-Script, § 9). Der Schritt bleibt als idempotentes Sicherheitsnetz; ein Fehler bricht den Deploy fail-fast ab, bevor neue Web-Replicas live gehen.

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

#### Sonderfall: Attachment-Kette `0129`–`0131` (owner-generische Dateianhaenge)

Milestone 24 (#1738,
[ADR-039](adr/039-owner-generische-dateianhaenge.md)) baut die Anhang-Tabelle um.
Drei Migrationen gehoeren zwingend zusammen — **`0130` und `0131` duerfen nie in
getrennten Releases ausgeliefert werden**:

| Migration | Was sie tut |
|---|---|
| `0129_rename_eventattachment_to_attachment` | Reines `RenameModel` (`core_eventattachment` → `core_attachment`). Policies, RLS-Flags und GRANTs haengen an der Tabellen-OID und **ueberleben** das Rename — hier wird nichts an der Policy angefasst. |
| `0130_attachment_owner_facility` | Neue Spalte `facility_id` (nullable → Backfill aus `event.facility` unter der **Admin-/Owner-Rolle**, BYPASSRLS → NOT NULL), `event_id` wird nullable, `client_id`/`case_id` kommen dazu, CheckConstraint `attachment_exactly_one_owner`, Index. **Reverse-Guard als letzte Operation** (rueckwaerts also als erste). |
| `0131_rls_attachment` | Policy-Swap auf `core_attachment`: **direkte** facility-Klausel statt Join ueber `core_event`, inkl. `is_super_admin`-Branch. Der Reverse stellt die Join-Policy wieder her und laesst RLS bewusst **aktiv**; er traegt einen eigenen Rollback-Guard. |

> ⚠️ **Rollback-Einschraenkung.** `migrate core 0128` (bzw. der Einzelschritt
> `migrate core 0130`) ist **nur zulaessig, solange keine personen- oder
> falleigenen Anhaenge existieren**. Beide Rollback-Guards brechen sonst mit
> `RuntimeError` ab — bewusst: unter der wiederhergestellten Join-Policy waeren
> diese Zeilen policy-verdeckt (unsichtbar, aber vorhanden). Mit dem ersten
> personen-/falleigenen Anhang ist der Rollback der Kette damit endgueltig zu.
> Bleibt nur der vollstaendige DB-Restore (§ 2.3) — **Backup vor der Migration
> ist Pflicht** (DDL, keine Datei-Moves, kein Medien-Downtime-Fenster).

**Post-Deploy-Checks** (als Admin-Rolle in `psql`, siehe § 9.2):

```sql
-- 1. Policy sitzt direkt auf der facility-Spalte (nicht mehr als Join ueber
--    core_event) UND traegt den is_super_admin-Bypass-Branch:
SELECT polname, pg_get_expr(polqual, polrelid) AS using_clause
FROM pg_policy
WHERE polrelid = 'core_attachment'::regclass;
-- erwartet: current_setting('app.current_facility_id', true) gegen facility_id
--           plus OR-Zweig auf current_setting('app.is_super_admin', true)
--           KEIN "EXISTS (SELECT ... FROM core_event ...)"

-- 2. Backfill lueckenlos (muss 0 liefern):
SELECT count(*) FROM core_attachment WHERE facility_id IS NULL;
```

Liefert Check 2 mehr als `0` oder zeigt Check 1 noch die Join-Klausel, ist die
Kette **nicht** vollstaendig durchgelaufen — nicht weiterdeployen, Zustand
klaeren.

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

> Dieses Kapitel ist die **autoritative Liste der Hintergrundjobs**; das [Admin-Handbuch](admin-guide.md) verweist hierauf.

> **⚠️ Betreiber-Aktion bei Updates von < 0.23:** Der Stack liefert die Hintergrundjobs
> seit #794 als **`cron`-Sidecar**
> mit ([§3.2](#32-cron-sidecar-standard)) — vorher mussten sie per Host-Crontab
> eingerichtet werden. Wer die Eintraege aus dem alten §3.2 bereits im Host-Cron
> stehen hat, laesst sie nach dem Update **doppelt** laufen. Diese Eintraege vor dem
> ersten Start des Sidecars entfernen; **einzige Ausnahme ist das Backup**, das
> weiterhin Host-seitig laeuft ([§3.3](#33-host-cron-backup--restliche-host-jobs)).
> Migrationsschritte: [§3.3](#33-host-cron-backup--restliche-host-jobs).

> **⚠️ Das Backup laeuft weiterhin NICHT automatisch.** Der Sidecar deckt alle Jobs
> ab, die als `manage.py`-Kommando im Container laufen. `backup.sh` faehrt dagegen
> `docker compose exec` (pg_dump im db-Container, tar im web-Container) und
> braeuchte dafuer den Docker-Socket im Sidecar — das waere root-aequivalent auf dem
> Host und wuerde die Runtime-Haertung des gesamten Stacks (`cap_drop: ALL`,
> `no-new-privileges`, `read_only`) entwerten. Backup bleibt daher **ein**
> Host-Crontab-Eintrag, siehe [§3.3](#33-host-cron-backup--restliche-host-jobs).
> Ohne ihn hat eine Installation **kein Backup** — ohne Fehlermeldung, nur Stille.
> Den Lauf-Status je Job zeigt das Compliance-Dashboard
> ([`/system/compliance/`](admin-guide.md), Kategorie „Hintergrundjobs", nur
> Super-Admins) — grob aggregiert auch der [`/health/`-Endpoint](monitoring-guide.md)
> (`stale_jobs`, `last_backup_age_hours`).

### 3.1 Uebersicht

Spalte **Wo**: `Sidecar` = laeuft ab Werk im `cron`-Container ([§3.2](#32-cron-sidecar-standard)),
`Host` = muss vom Betreiber im Host-Cron eingerichtet werden ([§3.3](#33-host-cron-backup--restliche-host-jobs)).

| Job | Empfohlene Zeit | Wo | Zweck |
|-----|----------------|----|-------|
| `backup.sh` | Taeglich 02:00 | **Host** | Verschluesseltes DB- und Medien-Backup mit Rotation; optionaler Off-Site-Sync (Refs #720, #738) |
| `enforce_retention` | Taeglich 03:00 | Sidecar | Abgelaufene Events soft-loeschen, Clients anonymisieren |
| `verify_audit_chain` | Taeglich 03:30 (nach Retention) | Sidecar | HMAC-Integritaetskette des AuditLog verifizieren; Exit-Code != 0 bei Tamper-Verdacht (Refs #1070) |
| `create_statistics_snapshots` | Monatlich 1. Tag 04:00 | Sidecar | Monats-Aggregate sichern bevor Events geloescht werden |
| `cleanup_orphan_storage_files` | Taeglich 04:30 (nach Snapshots) | Sidecar | Verwaiste `.enc`-Dateien ohne `Attachment`-Record loeschen (owner-unabhaengig: Ereignis-, Personen- und Fall-Anhaenge); MUSS als Admin-DB-Rolle (BYPASSRLS) laufen, sonst Fail-Loud (Refs #662, #1554, #1738) |
| `detect_breaches` | Stuendlich:30 | Sidecar | Heuristik-basierte Breach-Detection (failed-login-burst / mass-export / mass-delete) → AuditLog SECURITY_VIOLATION + optionaler Webhook (Refs #685) |
| `refresh_statistics_view` | Stuendlich:15 | Sidecar | Materialized View `core_statistics_event_flat` aktualisieren (Statistik-Dashboard) |
| Invite-Token-Audit | Woechentlich So 05:00 | Host | Verwaiste Invite-User-Konten aufspueren (siehe [10](#10-invite-token-hygiene)); reine `psql`-Abfrage, der App-Container hat keinen psql-Client |
| Health-Check | Alle 5 Minuten | Host | Verfuegbarkeit pruefen (externes Monitoring, gehoert nicht in den ueberwachten Stack) |

### 3.2 Cron-Sidecar (Standard)

Der `cron`-Service in
[`docker-compose.prod.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.prod.yml)
faehrt dasselbe App-Image wie `web`, startet darin aber statt Gunicorn den
Cron-Daemon **supercronic** und arbeitet die Job-Definitionen aus
[`deploy/crontab`](https://github.com/anlaufstelle/app/blob/main/deploy/crontab)
ab (read-only in den Container gemountet). Nach `docker compose up -d` laufen
Retention, Audit-Ketten-Verifikation, Snapshots, Orphan-Cleanup,
Breach-Detection und MV-Refresh ohne weitere Einrichtung.

**Warum supercronic und nicht `cron`/`crond`:** klassische Cron-Daemons
daemonisieren sich weg (Container haette keinen Vordergrundprozess), loggen nach
Syslog statt stdout (kein `docker compose logs`) und brauchen setuid-Bits, die
mit `no-new-privileges` + `cap_drop: ALL` kollidieren. supercronic ist ein
statisch gelinktes Go-Binary, das genau diese Punkte adressiert. Es kommt
versions- und sha256-gepinnt ins Image
([`deploy/fetch_supercronic.py`](https://github.com/anlaufstelle/app/blob/main/deploy/fetch_supercronic.py),
im Docker-Builder verifiziert).

**DB-Rolle:** Alle Jobs im Sidecar laufen als **Admin-Rolle** (BYPASSRLS) — der
Service setzt dafuer `POSTGRES_USER=${POSTGRES_ADMIN_USER}`. Ausserhalb eines
HTTP-Requests ist keine `app.current_facility_id`-GUC gesetzt; als
RLS-gefilterte App-Rolle brechen `enforce_retention`, `verify_audit_chain` und
`cleanup_orphan_storage_files` fail-loud ab (§9 DB-Rollenmodell). Deshalb
braucht die `.env` zwingend `POSTGRES_ADMIN_USER`/`POSTGRES_ADMIN_PASSWORD`.

**Zeitzone:** Der Service setzt `TZ` (Default `Europe/Berlin`, passend zu
`settings.TIME_ZONE`). Ohne `TZ` liefe der Container in UTC und „03:00" waere je
nach Jahreszeit 04:00/05:00 Ortszeit — die zugesagte Reihenfolge
(Host-Backup 02:00 **vor** Retention 03:00) waere dann nicht mehr garantiert.
Abweichende Zeitzone: `TZ=...` in der `.env`.

```bash
# Laeuft der Sidecar?
docker compose -f docker-compose.prod.yml ps cron

# Was hat er zuletzt getan? (supercronic loggt jeden Start/Exit nach stdout)
docker compose -f docker-compose.prod.yml logs --tail=100 cron

# Einen Job sofort ausserhalb des Zeitplans anstossen (gleiche Rolle wie im Sidecar):
docker compose -f docker-compose.prod.yml exec cron python manage.py refresh_statistics_view
```

**Ueberwachung:** supercronic hat keinen Health-Endpunkt — ein laufender
Container beweist nicht, dass die Jobs *wirken*. Der Wirksamkeits-Nachweis
kommt aus der App: jeder Job schreibt nach erfolgreichem Lauf einen
`*_RUN_COMPLETED`-Marker ins AuditLog, den das Compliance-Dashboard
(`/system/compliance/`, Kategorie „Hintergrundjobs") und `stale_jobs` im
[`/health/`-Payload](monitoring-guide.md) auswerten. Zusaetzlich sollte das
Host-Monitoring auf Fehlschlaege in den `cron`-Logs alarmieren — insbesondere
auf `verify_audit_chain`, das bei Tamper-Verdacht mit Exit-Code != 0 endet.

**Aenderungen am Zeitplan** gehoeren in `deploy/crontab` (Repo, versioniert);
danach `docker compose -f docker-compose.prod.yml restart cron`. Der Guard
`src/tests/test_ops_cron_sidecar.py` haelt die Datei mit der Tabelle in §3.1
synchron.

### 3.3 Host-Cron: Backup + restliche Host-Jobs

Zwei Jobs bleiben Host-seitig, weil sie im Sidecar nicht laufen koennen:

- **`backup.sh`** — braucht `docker compose exec` (pg_dump im db-Container, tar
 im web-Container) und damit den Docker-Socket. Ein Socket-Mount im Sidecar
 waere root-aequivalent auf dem Host.
- **Invite-Token-Audit / Health-Check** — `psql`-Abfrage bzw. externer
 `curl`-Check; ersteres hat der App-Container nicht an Bord, letzteres soll
 bewusst *ausserhalb* des ueberwachten Stacks laufen.

```cron
# Anlaufstelle – Host-Cron (Backup + Hygiene). Alles Weitere macht der
# cron-Sidecar aus §3.2 — diese Eintraege NICHT dupliziert einrichten!
# In /etc/crontab oder via `crontab -e` auf dem Host einrichten

# Backup (taeglich 02:00, vor der Retention im Sidecar um 03:00)
0 2 * * * cd /opt/anlaufstelle && ./scripts/ops/backup.sh >> /var/log/anlaufstelle-backup.log 2>&1

# Invite-Token-Audit (woechentlich So 05:00, Details siehe Abschnitt 10)
0 5 * * 0 cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT username, email, date_joined FROM core_user WHERE last_login IS NULL AND NOT (password LIKE 'pbkdf2_%') AND date_joined < now() - interval '7 days';" >> /var/log/anlaufstelle-invites.log 2>&1

# Health-Check (alle 5 Minuten)
*/5 * * * * curl -sf https://DOMAIN/health/ > /dev/null || echo "Anlaufstelle health check failed at $(date)" >> /var/log/anlaufstelle-health.log
```

#### Migration von der alten Host-Crontab (Betreiber-Aktion)

Installationen vor Release 0.23 haben alle Jobs im Host-Cron. Nach dem Update
laufen die vom Sidecar uebernommenen Jobs sonst doppelt — fachlich meist
harmlos (die Kommandos sind wiederholbar), aber es entstehen doppelte
Breach-Webhooks, doppelte Audit-Marker und unnoetige Last. Deshalb:

```bash
# 1. Aktuelle Crontab sichern
crontab -l > ~/crontab.vor-0.23.bak

# 2. Alle Zeilen ENTFERNEN, die eines dieser Kommandos aufrufen:
#      enforce_retention · verify_audit_chain · create_statistics_snapshots
#      cleanup_orphan_storage_files · detect_breaches · refresh_statistics_view
crontab -e

# 3. Backup-, Invite-Audit- und Health-Check-Zeile stehen lassen (siehe Block oben).

# 4. Stack aktualisieren und Sidecar hochfahren
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d

# 5. Verifizieren: Sidecar laeuft, Zeitplan geladen
docker compose -f docker-compose.prod.yml logs cron | head -30
```

Am Folgetag im Compliance-Dashboard (`/system/compliance/`, „Hintergrundjobs")
pruefen, dass die Marker frisch sind — das ist der Beleg, dass die Jobs
tatsaechlich durchlaufen.

<details>
<summary>Alte Host-Crontab (vollstaendig) — nur noch fuer Setups OHNE Sidecar</summary>

Relevant, wenn der Stack ohne `docker-compose.prod.yml` betrieben wird (z. B.
Coolify, k8s) und deshalb kein `cron`-Service existiert.

```cron
# Anlaufstelle – Operative Cron-Jobs (Legacy: ohne cron-Sidecar)
# In /etc/crontab oder via `crontab -e` auf dem Host einrichten

# Backup (taeglich 02:00, vor Retention)
0 2 * * * cd /opt/anlaufstelle && ./scripts/ops/backup.sh >> /var/log/anlaufstelle-backup.log 2>&1

# Retention-Durchsetzung (taeglich 03:00)
# MUSS mit der Admin-DB-Rolle (BYPASSRLS) laufen, sonst bricht das Kommando
# fail-loud ab (§9 DB-Rollenmodell), statt faelschlich 0 verarbeitete Zeilen
# als RETENTION_RUN_COMPLETED zu melden.
0 3 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T -e POSTGRES_USER=$POSTGRES_ADMIN_USER -e POSTGRES_PASSWORD=$POSTGRES_ADMIN_PASSWORD web python manage.py enforce_retention >> /var/log/anlaufstelle-retention.log 2>&1

# Audit-Ketten-Verifikation (taeglich 03:30, nach Retention; Refs #1070)
# MUSS mit der Admin-DB-Rolle (BYPASSRLS) laufen, sonst bricht das Kommando
# fail-loud ab (§9 DB-Rollenmodell) statt faelschlich "intakt" zu melden.
30 3 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T -e POSTGRES_USER=$POSTGRES_ADMIN_USER -e POSTGRES_PASSWORD=$POSTGRES_ADMIN_PASSWORD web python manage.py verify_audit_chain >> /var/log/anlaufstelle-audit-verify.log 2>&1

# Statistik-Snapshots (monatlich am 1. um 04:00)
0 4 1 * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T web python manage.py create_statistics_snapshots >> /var/log/anlaufstelle-snapshots.log 2>&1

# Orphan-Storage-Cleanup (taeglich 04:30, nach den Snapshots; verwaiste
# .enc-Dateien ohne Attachment-Record entfernen — owner-unabhaengig, also
# Ereignis-, Personen- und Fall-Anhaenge, Refs #662/#1554/#1738)
# MUSS mit der Admin-DB-Rolle (BYPASSRLS) laufen, sonst bricht das Kommando
# fail-loud ab (§9 DB-Rollenmodell): als RLS-gefilterte App-Rolle saehe der Lauf
# 0 registrierte Attachments und wuerde JEDE .enc-Datei als Orphan loeschen.
30 4 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T -e POSTGRES_USER=$POSTGRES_ADMIN_USER -e POSTGRES_PASSWORD=$POSTGRES_ADMIN_PASSWORD web python manage.py cleanup_orphan_storage_files >> /var/log/anlaufstelle-orphan-cleanup.log 2>&1

# Breach-Detection-Scan (stuendlich zur 30. Minute, Refs #685)
30 * * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T web python manage.py detect_breaches >> /var/log/anlaufstelle-breach.log 2>&1

# Materialized-View-Refresh (stuendlich zur 15. Minute, Details siehe Abschnitt 11)
15 * * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T web python manage.py refresh_statistics_view >> /var/log/anlaufstelle-mv.log 2>&1

# Invite-Token-Audit (woechentlich So 05:00, Details siehe Abschnitt 10)
0 5 * * 0 cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT username, email, date_joined FROM core_user WHERE last_login IS NULL AND NOT (password LIKE 'pbkdf2_%') AND date_joined < now() - interval '7 days';" >> /var/log/anlaufstelle-invites.log 2>&1

# Health-Check (alle 5 Minuten)
*/5 * * * * curl -sf https://DOMAIN/health/ > /dev/null || echo "Anlaufstelle health check failed at $(date)" >> /var/log/anlaufstelle-health.log
```

```

</details>

**Reihenfolge beachten:** Backup (02:00) → Retention (03:00) → Audit-Ketten-Verifikation (03:30) → Snapshots (04:00, monatlich) → Breach-Detection/MV-Refresh (stuendlich). Backup muss vor Retention laufen, damit geloeschte Daten im Backup enthalten sind. Die Audit-Ketten-Verifikation laeuft nach der Retention, damit die von `prune_auditlog` frisch geschriebenen Checkpoint-Grenzen bereits in der Kette stehen. Der MV-Refresh nutzt `CONCURRENTLY` und blockiert laufende Reader nicht. Genau diese Reihenfolge bildet der Sidecar-Zeitplan in `deploy/crontab` ab — deshalb ist die `TZ`-Einstellung aus [§3.2](#32-cron-sidecar-standard) betriebsrelevant und nicht Kosmetik.

### 3.4 Dev (systemd-Timer)

Auf `dev.anlaufstelle.app` laufen die Jobs **nicht** per Host-Crontab, sondern als
systemd-Timer, die `dev-ops/deploy/install-timers.sh` (dev-only)
installiert — aufgerufen bei **jedem** Deploy durch `dev-ops/deploy/deploy-dev.sh` (dev-only)
(idempotent). Der Dev-Stack laeuft ueber `docker-compose.dev.yml` und bekommt
den `cron`-Sidecar aus [§3.2](#32-cron-sidecar-standard) bewusst **nicht**:
beides parallel wuerde jeden Job doppelt ausfuehren, und die Timer sind hier
zusaetzlich der Testfall fuer den Host-Scheduler-Pfad, den Coolify-/k8s-Setups
ohne unser Compose-File nutzen.

> **Hinweis (Refs #980):** Frueher installierte `bootstrap.sh` die Timer inline. Da `bootstrap.sh` nur beim Erst-Provisioning laeuft, kamen nachtraeglich ergaenzte Timer nie auf den laufenden Host (Backup-Cron blieb aus). Die Installation liegt jetzt in `install-timers.sh` und laeuft bei jedem `deploy-dev.sh`.

| Timer | OnCalendar | Command |
|-------|-----------|---------|
| `anlaufstelle-backup.timer` | `*-*-* 02:00` | `dev-ops/deploy/backup.sh` |
| `anlaufstelle-retention.timer` | `*-*-* 03:00` | `… exec -T web python manage.py enforce_retention` |
| `anlaufstelle-audit-verify.timer` | `*-*-* 03:30` | `… exec -T web python manage.py verify_audit_chain` |
| `anlaufstelle-snapshots.timer` | `*-*-01 04:00` | `… exec -T web python manage.py create_statistics_snapshots` |
| `anlaufstelle-orphan-cleanup.timer` | `*-*-* 04:30` | `… exec -T web python manage.py cleanup_orphan_storage_files` |
| `anlaufstelle-breach.timer` | `*-*-* *:30` | `… exec -T web python manage.py detect_breaches` |
| `anlaufstelle-mv-refresh.timer` | `*-*-* *:15` | `… exec -T web python manage.py refresh_statistics_view` |

**Aktivierung auf bereits laufenden Servern:** Ein regulaerer `deploy-dev.sh`-Lauf
installiert die Timer mit. Einmalig sofort nachziehen geht auch direkt:

```bash
sudo bash /opt/anlaufstelle/dev-ops/deploy/install-timers.sh # idempotent, als root
systemctl list-timers "anlaufstelle-*" # 7 Timer mit NEXT-Zeit
systemctl start anlaufstelle-mv-refresh.service # einmal manuell anstossen
journalctl -u anlaufstelle-mv-refresh.service -n 20
```

Den Lauf-Status je Job zeigt zusaetzlich das Compliance-Dashboard (`/system/compliance/`,
Kategorie „Hintergrundjobs") — `unknown`/`warning`/`critical`, wenn ein Timer nicht laeuft.

> **Hinweis:** `install-timers.sh` installiert diese 7 Timer. Die Wartungs-Timer laufen ueber `run-as-admin.sh` mit der Admin-DB-Rolle (BYPASSRLS) — inklusive `cleanup_orphan_storage_files` (Refs #662, #1554), das andernfalls fail-loud abbricht (§9 DB-Rollenmodell).

### 3.5 Manuelle Ausfuehrung

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

# Verwaiste dynamische Feldwerte bereinigen (Refs #1725)
# Entfernt data_json-Keys, deren Slug nicht (mehr) am Dokumentationstyp haengt,
# bei Personen/Faellen/Ereignissen. Zugeordnete (auch verschluesselte) Werte
# bleiben unangetastet; Datensaetze ohne Typ werden nur gezaehlt. MUSS mit der
# Admin-DB-Rolle laufen (BYPASSRLS) — sonst fail-loud (§ 9 DB-Rollenmodell).
#
# Verwaiste FILE-Marker werden bei ALLEN drei Entitaeten bewusst UEBERSPRUNGEN
# und separat als "Datei-Waise(n) uebersprungen" gemeldet, NICHT geloescht: der
# data_json-Marker ist die einzige Bruecke zur verschluesselten Attachment-
# Zeile. (Bis M24 traf die Regel faktisch nur Ereignisse — Datei-Felder waren an
# Personen-/Fall-Typen gesperrt; seit Refs #1738 koennen auch Personen und
# Faelle FILE-Waisen tragen und werden genauso verschont.)
# Diese Datei-Bereinigung laeuft ueber die Attachment-Loeschpfade bzw.
# cleanup_orphan_storage_files (nach dem Loeschen der Attachment-Zeile), nicht
# ueber diesen Command — die uebersprungene Zaehlung gilt also nicht als
# erledigte Datei-Minimierung.
docker compose -f docker-compose.prod.yml exec \
 -e POSTGRES_USER=$POSTGRES_ADMIN_USER -e POSTGRES_PASSWORD=$POSTGRES_ADMIN_PASSWORD \
 web python manage.py purge_orphan_field_data --dry-run # Vorschau
docker compose -f docker-compose.prod.yml exec \
 -e POSTGRES_USER=$POSTGRES_ADMIN_USER -e POSTGRES_PASSWORD=$POSTGRES_ADMIN_PASSWORD \
 web python manage.py purge_orphan_field_data # ausfuehren
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
 "exception": "Traceback..."
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
# - DNS zeigt nicht auf Server-IP:
nslookup $DOMAIN

# - Ports 80/443 blockiert:
curl -v http://$DOMAIN 2>&1 | head -20

# - Let's Encrypt Rate Limit (max 5/Woche/Domain):
# → 1 Woche warten oder Staging-CA nutzen

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
# GUNICORN_TIMEOUT=60

# 3. Worker-Anzahl anpassen (Faustregel: 2 * CPU-Kerne + 1)
# GUNICORN_WORKERS=5

# 4. Neustart
docker compose -f docker-compose.prod.yml restart web
```

**Root Cause suchen:** Lang laufende Requests deuten auf langsame DB-Queries. Django Debug Toolbar (nur Dev) oder `EXPLAIN ANALYZE` nutzen.

### 5.4a Cache-Backend / Multi-Worker (Refs #795)

Sobald `GUNICORN_WORKERS > 1` läuft (siehe [5.4](#54-gunicorn-timeout)), brauchen `django-ratelimit` (Login/Sudo/Health-Bruteforce-Schutz) und das Health-Detail-Caching einen **prozessübergreifenden** Cache. Andernfalls (Django-Default `LocMemCache`) hält jeder Worker seinen eigenen Zähler und die effektiven Rate-Limits ver-N-fachen sich mit der Worker-Zahl.

**Standard: der mitgelieferte `redis`-Service.**
`docker-compose.prod.yml` startet einen Redis im internen Netz und setzt für `web` und `cron` `REDIS_URL=redis://redis:6379/1`; `settings/prod.py` schaltet damit auf Djangos eingebautes `RedisCache` um (kein `django-redis` nötig). Eigenschaften des Service:

- **Kein Host-Port, kein Passwort.** Der Cache hängt ausschließlich im `internal`-Netz; die Netzgrenze ist die Zugriffskontrolle. Wer den Port doch veröffentlicht, betreibt einen offenen, unauthentifizierten Redis.
- **Keine Persistenz** (`--save ""`, `--appendonly no`, kein Volume). Im Cache liegen nur regenerierbare Daten mit kurzer TTL. Persistenz wäre hier ein Datenschutz-*Minus*: sie schriebe Rate-Limit-Schlüssel (u. a. Client-IPs, User-IDs) auf die Platte, wo keine Retention sie erfasst. Ein Redis-Neustart kostet nur einen kalten Cache.
- **Speicher gedeckelt** (`--maxmemory 256mb`, `allkeys-lru`), damit der Container nicht ins `mem_limit`-OOM läuft.

**Fallback (unverändert): `DatabaseCache`.**
Ist `REDIS_URL` leer oder nicht gesetzt, nutzt `prod.py` den shared `DatabaseCache`. Die Cache-Tabelle `anlaufstelle_cache` wird per Migration (`0092`) angelegt und läuft im normalen One-Shot-Migrate-Job (`docker-migrate.sh`, siehe [1.2](#12-deploy-schritte)) automatisch mit hoch — **kein** separater `createcachetable`-Schritt nötig. Der Fallback ist funktional gleichwertig (ebenfalls prozessübergreifend, `RATELIMIT_USE_CACHE=default`), kostet aber pro Rate-Limit-Zähler einen DB-Roundtrip. **Redis ist hier also eine Performance-, keine Korrektheits-Entscheidung.**

Redis bewusst abschalten (z. B. sehr kleines Deployment, jedes MB RAM zählt):

```bash
# in .env — leer, nicht auskommentiert:
REDIS_URL=
```

```bash
docker compose -f docker-compose.prod.yml up -d --scale redis=0
```

**Externes Redis** statt des mitgelieferten: einfach `REDIS_URL` in der `.env` auf den eigenen Endpunkt zeigen lassen (`redis://redis.internal:6379/1`); der Compose-Wert ist nur ein Fallback für nicht gesetzte Variablen. Der mitgelieferte Service kann dann wie oben auf 0 skaliert werden.

**Welches Backend läuft gerade?** Der `/health/`-Endpoint gibt es für interne/Token-Caller aus:

```bash
curl -sf -H "X-Health-Token: $HEALTH_DETAIL_TOKEN" https://$DOMAIN/health/ \
 | python3 -c 'import json,sys; print(json.load(sys.stdin)["cache"])'
# {'backend': 'django.core.cache.backends.redis.RedisCache', 'status': 'ok'}
```

Das ist kein Kosmetik-Feld: Ist `REDIS_URL` gesetzt, im Image aber kein `redis`-Paket vorhanden (typisch, wenn ein **älteres** `APP_VERSION`-Image mit dem neuen Compose-File gefahren wird), fällt `prod.py` absichtlich auf den `DatabaseCache` zurück und schreibt eine Warnung ins Container-Log — der Stack bleibt lauffähig, läuft aber langsamer. `cache.backend` macht diesen Zustand sichtbar. `cache.status = error` (Backend konfiguriert, aber nicht erreichbar) setzt den Gesamtstatus auf `degraded`, weil dann auch der Rate-Limit-Schutz wirkungslos ist.

**Kein separater `ratelimit`-Cache-Alias.** Die ursprüngliche Planung sah einen zweiten Alias auf Redis-DB `/2` vor. Umgesetzt ist er nicht: `django-ratelimit` hängt an `RATELIMIT_USE_CACHE`, und kein Code liest `caches["ratelimit"]` — der Alias wäre tote Konfiguration und müsste im `DatabaseCache`-Fallback zusätzlich eine zweite Cache-Tabelle (also eine weitere Migration) tragen. DB-Index `/2` bleibt für eine spätere Trennung frei.

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
# "ImproperlyConfigured" → Fehlende Env-Vars in .env
# "ENCRYPTION_KEY must be set" → ENCRYPTION_KEY in .env fehlt
# "connection refused" → DB noch nicht ready

# 3. Env-Vars verifizieren
docker compose -f docker-compose.prod.yml exec web printenv | sort

# 4. Django-Check manuell ausfuehren
docker compose -f docker-compose.prod.yml exec web \
 python manage.py check --deploy
```

### 5.7 Upload-Groessenlimit (Caddy `request_body`)

**Symptom:** `413 Request Entity Too Large` beim Datei-Upload (Anhaenge/Replace), obwohl der Upload unterhalb des in der App konfigurierten Limits liegt.

Refs #1363 (N10 + Review-Nachschlag): Caddy weist Request-Bodies oberhalb von `1100MiB` bereits am Edge ab (Defense-in-Depth gegen Disk-Fill/OOM, bevor Django/Gunicorn den Body ueberhaupt sieht). Das App-seitige Hart-Limit ist `FILE_VAULT_MAX_UPLOAD_BYTES` (Default 50 MiB, `settings/base.py`) — aber Datei-Upload-Felder duerfen laut `FILE_VAULT_MAX_UPLOAD_FILES` (Default 20, `MultipleFileInput`/Refs #622) **mehrere Dateien pro Feld** enthalten. Der Caddy-Cap ist deshalb auf den Worst-Case eines vollen Multi-File-Felds dimensioniert: `FILE_VAULT_MAX_UPLOAD_BYTES * FILE_VAULT_MAX_UPLOAD_FILES` (= 1000 MiB bei Default) + 10 % Marge fuer Multipart-Overhead (Boundaries, Feld-Header, CSRF-Token, weitere Formularfelder im selben Request). Ein nur auf EINE Datei dimensionierter Cap (die urspruengliche `55MiB`) weist legitime, bereits unterstuetzte Multi-File-Uploads am Edge ab, obwohl jede einzelne Datei alle App-seitigen Groessen-/Typ-Checks bestehen wuerde.

**Restrisiko (bewusst dokumentiert, nicht gefixt):** Ein `DocumentType` kann — nur konfigurierbar durch `facility_admin`/`super_admin` — **mehrere** FILE-Felder tragen; jedes traegt unabhaengig bis zu `FILE_VAULT_MAX_UPLOAD_FILES` Dateien. Der theoretische Worst-Case je Request waechst dann um weitere `FILE_VAULT_MAX_UPLOAD_BYTES * FILE_VAULT_MAX_UPLOAD_FILES` pro zusaetzlichem FILE-Feld. Deployments mit vielfeldigen Datei-Dokumenttypen sollten den Caddy-Cap entsprechend hochskalieren (kein automatischer Abgleich).

```bash
# 1. Aktuellen Caddy-Wert pruefen (alle vier Varianten: Caddyfile,
# Caddyfile.dev, Caddyfile.demo, Caddyfile.staging)
grep -A2 request_body Caddyfile*

# 2. App-seitiges Limit pruefen (falls per Env ueberschrieben)
docker compose -f docker-compose.prod.yml exec web \
 printenv | grep FILE_VAULT_MAX_UPLOAD
```

Ein Test-Guard haelt beide Settings synchron zum statischen Caddy-Wert (Entwickler-Repo: `src/tests/test_architecture_guards_ops.py::TestCaddyUploadBodyLimit`) — schlaegt fehl, sobald `FILE_VAULT_MAX_UPLOAD_BYTES`/`FILE_VAULT_MAX_UPLOAD_FILES` geaendert werden, ohne den Caddy-Cap nachzuziehen.

**WICHTIG — Maintainer-Handoff:** Der Caddy-Wert ist statisch im Caddyfile hinterlegt und folgt **nicht** automatisch, wenn `FILE_VAULT_MAX_UPLOAD_BYTES` **oder** `FILE_VAULT_MAX_UPLOAD_FILES` per Env ueberschrieben wird. Bei Aenderung eines der beiden App-Limits (oder bei einem Deployment mit mehrfeldigen Datei-Dokumenttypen, s. Restrisiko oben) den Wert im jeweiligen Caddyfile manuell nachziehen und Caddy neu laden:

```bash
docker compose -f docker-compose.prod.yml exec caddy \
 caddy reload --config /etc/caddy/Caddyfile
```

**Rollout-Hinweis:** Diese Aenderung selbst braucht nach dem Deploy ebenfalls einen Caddy-Reload (obiger Befehl, Dateipfad je nach Compose-Variante anpassen) — ein reiner Web-Container-Neustart laedt keine neue Caddyfile-Config.

### 5.7a Upload-Temp-Verzeichnis (Klartext-Zwischenpuffer, Refs #1661)

**Hintergrund:** Uploads oberhalb von `FILE_UPLOAD_MAX_MEMORY_SIZE` (Default 2,5 MB) puffert Django als **Klartext-Tempdatei** auf Platte, bevor sie von `core/services/file_vault/storage.py` gescannt und verschluesselt werden. Ohne explizites `FILE_UPLOAD_TEMP_DIR` faellt Django auf `tempfile.gettempdir()` zurueck (i. d. R. `/tmp`). Das ist nur ein kurzes Zeitfenster, aber unverschluesselter Klienten-Content liegt fuer diesen Moment auf Disk.

**Gegenmassnahme:** `docker-compose.prod.yml`/`staging.yml`/`demo.yml`/`dev.yml` mounten `/tmp` im `web`-Container bereits als **tmpfs** (RAM) — noetig, weil der Container `read_only` laeuft (Refs #1374). `FILE_UPLOAD_TEMP_DIR=/tmp` ist dort explizit im `environment:`-Block der jeweiligen Compose-Datei gesetzt und pinnt Djangos Upload-Puffer nachweislich auf dieses tmpfs, statt sich auf Pythons `TMPDIR`-Ermittlungsreihenfolge zu verlassen (ein spaeter versehentlich gesetztes `TMPDIR` wuerde den impliziten Fallback sonst unbemerkt von tmpfs weg auf ein persistentes Volume umlenken). Der Tempdatei-Inhalt existiert also nur fluechtig im RAM und ist beim Container-Neustart weg — kein Aufraeum-Cronjob noetig.

```bash
# Verifizieren, dass /tmp im web-Container tatsaechlich ein tmpfs ist
docker compose -f docker-compose.prod.yml exec web mount | grep "on /tmp"
# erwartet: "tmpfs on /tmp type tmpfs (rw,...)"

# Aktuell gesetzten Wert pruefen
docker compose -f docker-compose.prod.yml exec web printenv FILE_UPLOAD_TEMP_DIR
```

**Fallback ohne tmpfs:** Auf einem Bare-Metal-Deploy oder einer individuell orchestrierten Umgebung ohne tmpfs-Mount (kein Docker Compose, eigenes Kubernetes-Manifest, o. ae.) bewirkt `FILE_UPLOAD_TEMP_DIR` allein nichts, wenn der referenzierte Pfad kein tmpfs ist — der Klartext-Puffer landet dann auf demselben Storage wie der Rest des Dateisystems. Optionen fuer Betreiber ohne tmpfs:

1. **Eigenes tmpfs mounten** und `FILE_UPLOAD_TEMP_DIR` darauf zeigen lassen (bevorzugt) — z. B. `mount -t tmpfs -o size=512M,mode=1777 tmpfs /path/to/upload-tmp` und `FILE_UPLOAD_TEMP_DIR=/path/to/upload-tmp` setzen.
2. **Verschluesseltes Volume** (LUKS/dm-crypt o. ae.) als Temp-Ziel verwenden, falls RAM-Kapazitaet fuer grosse Uploads knapp ist.
3. **Variable unset lassen** und das Restrisiko bewusst akzeptieren — das kurze Klartext-Fenster bleibt dann auf dem regulaeren (unverschluesselten) Dateisystem-Storage bestehen. `python manage.py check --deploy` warnt in diesem Fall (`core.W002`) beim Start, damit die Entscheidung sichtbar getroffen wird statt still zu passieren.

Ein voller RAM-Buffer statt Disk-Streaming (`FILE_UPLOAD_MAX_MEMORY_SIZE` sehr hoch setzen) ist **kein** Ersatz: das wuerde grosse Uploads komplett im Prozessspeicher halten (DoS-Risiko bei mehreren parallelen Uploads) statt das Zeitfenster zu schliessen.

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

# Verschluesseltes Backup via Skript./scripts/ops/backup.sh
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
2. **T+24h** — Vorgang an [DPO/Datenschutzbeauftragten](https://github.com/anlaufstelle/app/blob/main/SECURITY.md) eskalieren, Risiko-Einschaetzung erstellen. Faellt die verantwortliche Hauptperson aus, uebernimmt die benannte **Vertretung** die Eskalation und Meldung ([MAINTAINERS.md](../MAINTAINERS.md), [GOVERNANCE.md § Eskalationspfad](../GOVERNANCE.md)) — dieselbe Vertretung, die die CRA-24-h-Fruehwarnfrist absichert; solange sie nicht benannt ist, bleibt dies ein offener Bus-Faktor-1-Posten.
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

Das Skript deckt seit OPS-05 (Refs #1336) **beide** ausgelieferten Backup-Formate ab. Die Wahl erfolgt per Flag `--dev`/`--prod` oder `$DRILL_FORMAT`; ohne Angabe wird das Format anhand der vorhandenen Backup-Dateien auto-erkannt (Default `dev`, rueckwaerts-kompatibel — der bestehende dev-Timer laeuft unveraendert). Liegen **beide** Formate gleichzeitig vor, bricht der Drill ab und verlangt eine explizite Wahl:

- **`--dev`** — `dev-ops/deploy/backup.sh`, das auf `dev.anlaufstelle.app` tatsaechlich laeuft (Refs #981): `pg_dump --format=custom` → `pg_restore`, AES-256-CBC, Quelle `$BACKUP_DIR/dump-*.pgc.enc` (Default `/var/backups/anl`), Stack `docker-compose.dev.yml`.
- **`--prod`** — `scripts/ops/backup.sh`, der an Self-Hoster ausgelieferte kanonische Pfad: plain SQL + `gzip` + A4.3-HMAC-Sidecar → `psql`, Quelle `backups/daily/anlaufstelle_*.sql.gz.enc`, Stack `docker-compose.prod.yml`. Der HMAC-Sidecar wird VOR dem Entschluesseln geprueft.

Beide Formate spielen als Postgres-Superuser (`postgres`, via Local-Socket-Trust im db-Container) ein — noetig fuer `CREATE DATABASE`, bypassed zugleich RLS/FORCE-RLS beim Restore.

**Kompatibilitaet Backup-Format × Restore-Pfad** — die beiden Backup-Welten sind *nicht* austauschbar; ein Drill gegen das jeweils fremde Format schlaegt fehl. Der Drill waehlt aber pro Format den passenden Pfad:

| Backup-Quelle | Dump-Format | Verschluesselung | Datei(en) | Restore-Tool | Restore-Drill |
|---|---|---|---|---|---|
| `dev-ops/deploy/backup.sh` (laeuft auf `dev.anlaufstelle.app`) | `pg_dump -Fc` (custom) | AES-256-CBC (kein HMAC) | `dump-*.pgc.enc` | `pg_restore` | ✓ `restore-drill.sh --dev` (Glob `dump-*.pgc.enc`) |
| `scripts/ops/backup.sh` (Prod-Schema) | plain SQL + gzip | AES-256-CBC **+ HMAC-SHA256**-Sidecar (Refs #1024) | `*.sql.gz.enc` + `.hmac` | `psql` (`scripts/ops/restore.sh`) | ✓ `restore-drill.sh --prod` (HMAC-Check vor Decrypt, Refs #1336); `backup.sh --verify` bleibt leichtgewichtige Alternative |

Warum kein Cross-Restore: Der `--dev`-Zweig sucht per Glob `dump-*.pgc.enc` und pipet in `pg_restore` — ein plain-SQL-Backup (`*.sql.gz.enc`) wird so gar nicht gefunden und ist auch kein gueltiges `pg_restore`-Archiv. Der `--prod`-Zweig sucht `anlaufstelle_*.sql.gz.enc`, prueft den `.hmac`-Sidecar und spielt via `gunzip | psql` ein — ein `dump-*.pgc.enc` traegt keinen Sidecar (`backup_verify_hmac` bricht ab) und ist Binaerformat. Ein Lauf gegen das jeweils fremde Format schlaegt also fehl. Wer keinen vollen Drill fahren will, nutzt fuer den Prod-Pfad `scripts/ops/backup.sh --verify` (HMAC + Probe-Decrypt + Zeilenabgleich gegen die Live-DB).

Die Backup-Dateien sind `0600` und gehoeren root — daher **als root** ausfuehren. Ohne Flag erkennt das Skript das Format automatisch; explizit per `--dev`/`--prod` (oder `DRILL_FORMAT=dev|prod`):

```bash
sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh # Format auto-erkennen
sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh --prod # Prod-Format erzwingen (*.sql.gz.enc)
sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh --dev # Dev-Format erzwingen (dump-*.pgc.enc)
```

| Schritt | Pruefung |
|---|---|
| 0 | Neuestes Backup im format-spezifischen Verzeichnis finden (`--dev`: `dump-*.pgc.enc`; `--prod`: `anlaufstelle_*.sql.gz.enc`) |
| 1 | Frische Wegwerf-DB (`anlaufstelle_drill_<pid>`) anlegen |
| 2 | Backup decrypten + einspielen — `--prod` prueft zuerst den HMAC-Sidecar, dann `gunzip \| psql` (plain SQL); `--dev` `pg_restore` (custom format) |
| 3 | Stichproben pro Tabelle (`core_facility`, `core_client`, `core_event`, `core_auditlog`, `core_workitem`) — Counts abfragbar? |
| 3b | **Vollstaendigkeits-Guard (fail-closed):** Zeilenabgleich der kritischen RLS-Tabellen (`core_client`, `core_event`, `core_workitem`, `core_auditlog`) zwischen Wegwerf-DB und Live-DB (`$POSTGRES_DB`) — `Live>0` aber `Restore=0` faerbt rot. Faengt den unvollstaendigen `--prod`-Restore ab, den der `psql`-Exit-Code (ohne `ON_ERROR_STOP`) verschluckt (Refs #1336) |
| 4 | RLS-Policy-Check — `SELECT COUNT(*) FROM pg_class WHERE relname LIKE 'core_%' AND relrowsecurity = true` muss `>= 18` ergeben (konservativer Mindestwert im Skript; tatsaechliche Anzahl liegt hoeher, siehe Abschnitt 9) |
| 5 | `auditlog_immutable`-Trigger existiert UND blockt Raw UPDATE |
| 6 | Cleanup: Wegwerf-DB drop (via trap) |
| 7 | **Bei vollem Erfolg (alle Schritte inkl. 3b):** `mark_restore_verified` im web-Container → Compliance-Marker |

Output: ein `OK` / `FAIL`-Eintrag pro Schritt. Exit-Code != 0 bei jedem `FAIL`. Bei `FAIL` sofort auf Backup-Integritaet pruefen — Trigger-Check fehlgeschlagen (Schritt 5) ist kritisch, weil die AuditLog-Immutability dann nach Restore nicht mehr greift.

> **Benigne Ausnahme beim Restore (Schritt 2):** Das Dump enthält ein `REFRESH MATERIALIZED VIEW core_statistics_event_flat`, das beim Restore an `FORCE ROW LEVEL SECURITY` auf `core_event` scheitert (`--dev`: `pg_restore: errors ignored on restore: 1`). Das betrifft nur den Statistik-Cache, nicht die Nutzdaten — der mv-refresh-Timer baut ihn stündlich neu. Der `--dev`-Drill meldet das als `OK … MV-Refresh übersprungen (benigne)` und schlägt nur bei **anderen** Fehlern fehl. Im `--prod`-Zweig laeuft `psql` bewusst ohne `ON_ERROR_STOP` (derselbe MV-Refresh-Fehler wird toleriert), wodurch der Exit-Code allein keine Vollstaendigkeit belegt — den fail-closed-Beweis liefert der Zeilenabgleich in **Schritt 3b**, bevor der Restore als verifiziert markiert wird.

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
# Quartalsweise (1. Monat im Quartal, 03:30 nach Backup um 02:00). Ohne Flag
# wird das Format auto-erkannt; auf einem Self-Hoster mit Prod-Backups explizit
# --prod setzen, damit ein fehlendes Backup nicht still auf --dev zurueckfaellt:
30 3 1 1,4,7,10 * cd /opt/anlaufstelle && bash scripts/ops/restore-drill.sh --prod \
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
CLAMAV_ENABLED=true # Default in prod.py
CLAMAV_HOST=clamav # Service-Name im Compose-Netzwerk
CLAMAV_PORT=3310
CLAMAV_TIMEOUT=15 # Sekunden; MUSS < GUNICORN_TIMEOUT (Refs #1283)
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
 python -c "from core.services.virus_scan import ping; print(ping)"

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
# eintragen — mit offener Range, z.B. "Django>=5.1,<5.2".

# 2. Lock-Files neu erzeugen (pip-tools muss installiert sein):
make deps-lock

# 3. Diff prüfen (welche transitiven Pakete kamen hinzu, welche Versionen wurden
# angehoben?), auf bekannte Breaking Changes sichten.
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

Seit v0.10.0 ist PostgreSQL-RLS auf allen facility-gescopten Tabellen (derzeit
**25**, per Test ableitungsbasiert erzwungen —
[`test_rls.py`](../src/tests/test_rls.py)) gegen Cross-Facility-Leaks
abgesichert (Defense-in-Depth unterhalb der Django-Scoping-Schicht). Die
Tabellenmenge waechst mit jedem neuen facility-gescopten Model; eine feste
Zahl hier veraltet deshalb regelmaessig — die Ableitung in `test_rls.py` ist
die Wahrheitsquelle. Jede Policy vergleicht `facility_id` gegen die Session-
Variable `app.current_facility_id`, die von der
[`FacilityScopeMiddleware`](../src/core/middleware/facility_scope.py) pro
Request via `set_config(..., is_local=false)` gesetzt wird.

**Fail-closed:** Ist die Variable nicht gesetzt oder leer (`NULL`),
liefern die Policies **keine Zeilen** — auch nicht fuer den Tabellen-
eigentuemer (`FORCE ROW LEVEL SECURITY`). Bypass nur fuer Superuser-DB-
Rollen; der Django-DB-User darf deshalb in Produktion **kein** Superuser
sein.

**Direkte statt Join-Policy auf `core_attachment` (Refs
#1738,
[ADR-039](adr/039-owner-generische-dateianhaenge.md)):** Dateianhaenge hingen bis
Milestone 24 als `core_eventattachment` ohne eigene `facility_id` am Ereignis;
ihre Policy war ein Join ueber `core_event`. Seit Migration `0131` traegt
`core_attachment` eine eigene `facility_id` und die uebliche **direkte** Policy
(inkl. `is_super_admin`-Branch). Gewinn: eine Anhang-Zeile ohne Ereignis war
unter der Join-Policy policy-**los**; jetzt greift die Isolation unabhaengig vom
Owner (Ereignis, Person oder Fall). Die Tabellenmenge aendert sich dadurch
nicht — nur Name und Bauart der Policy. Post-Deploy-Check und
Rollback-Einschraenkung der Kette `0129`–`0131`: [§ 2.2](#22-migration-zurueckrollen).

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

**Daueruberwachung statt Einmal-Smoke-Query (Refs
#793):** Der
Health-Endpoint fuehrt genau diese Pruefung jetzt bei **jedem** Aufruf auf der
laufenden Verbindung aus (``SELECT rolsuper, rolbypassrls … WHERE rolname =
current_user``). Umgeht die Runtime-Rolle RLS, faellt ``status`` dauerhaft auf
``degraded`` (HTTP bleibt 200) und Token-Caller sehen die Felder
``db_user_is_superuser``/``db_role_posture``; das Detail steht zusaetzlich als
``WARNING`` im Log (Logger ``core.views.health``). Damit faellt ein
fehlkonfigurierter Cluster im Monitoring auf, ohne dass jemand die Smoke-Query
manuell fahren muss — Feld-Referenz:
[docs/monitoring-guide.md](monitoring-guide.md). Abgrenzung: ``check_db_roles``
prueft, dass App-/Admin-Rolle mit den richtigen Attributen im Katalog
**existieren**; der Health-Endpoint prueft, als **wer** die Anwendung
tatsaechlich verbindet.

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
| `POSTGRES_USER` (App) | `anlaufstelle` | `NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB`, **kein Owner** — nur DML (`SELECT/INSERT/UPDATE/DELETE`) + Sequenz-/Schema-`USAGE` | Django-Runtime. RLS-Policies greifen scharf (`FORCE ROW LEVEL SECURITY`). |
| `POSTGRES_ADMIN_USER` (Admin/Wartung/**Owner**) | `anlaufstelle_admin` | `NOSUPERUSER BYPASSRLS NOCREATEROLE NOCREATEDB`, **DB-/Objekt-Owner** | `migrate`, `seed`, Retention-/Anonymisierungs-Cron. Als Owner umgeht die Wartung die Append-Only-Trigger per DDL (A1.4, § 9.5). |

Eigenschaften, die das Init-Script code-treu garantiert:

- Beide Rollen sind **NOSUPERUSER** — kein Pfad zur Rechte-Eskalation.
- Die App-Rolle ist **NOBYPASSRLS** und **nicht Owner** (A1.4, Refs
  #1026); sie kann die
  Append-Only-Trigger daher weder per `ALTER TABLE … DISABLE TRIGGER`
  abschalten noch — mangels `SET PARAMETER`-Grant — per
  `session_replication_role` umgehen. `FORCE ROW LEVEL SECURITY` greift
  zusätzlich (Fail-closed, § 9 oben).
- Die Admin-Rolle ist **BYPASSRLS** (fuer Migrationen/Wartung) und **Owner** der
  Datenbank + aller Objekte — nötig für `DROP POLICY`/`ALTER TABLE` in
  Migrationen (BYPASSRLS allein reicht nicht für DDL) und für den
  Append-Only-Trigger-Bypass im Wartungspfad. `ALTER DEFAULT PRIVILEGES FOR ROLE
  admin … TO app` grantet jeder migrierten Tabelle/Sequenz automatisch die
  App-DML-Rechte.

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
# erwartet: App-Rolle rolsuper=False, rolbypassrls=False
# Admin-Rolle rolsuper=False, rolbypassrls=True (Exit-Code 0)
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

Einzelne Debug-Queries gegen die DB ausfuehren, **ohne** RLS zu umgehen —
gesetzt wird nur fuer die laufende Transaktion:

```sql
BEGIN;
SET LOCAL app.current_facility_id = '<facility-uuid-oder-pk>';
SELECT count(*) FROM core_client;
--... weitere Queries...
COMMIT; -- oder ROLLBACK, SET LOCAL endet mit der Transaktion
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

### 9.5 Append-Only-Trigger-Bypass via Owner-DDL (A1.4)

Die DSGVO-Art.-17-Anonymisierung (`Client.anonymize`), das Audit-Pruning
(`prune_auditlog`) sowie `backfill_audit_chain`/`reencrypt_fields` müssen die
Append-Only-Trigger (`auditlog_immutable`, `eventhistory_no_update`/`_no_delete`)
für ihre legitimen Schreibzugriffe kurz umgehen. Seit A1.4 (Refs
#1026) läuft das **nicht**
mehr über `session_replication_role = replica` (das feuert einen
`ENABLE ALWAYS`-Trigger nicht mehr still), sondern `bypass_replication_triggers()`
deaktiviert die Trigger gezielt per **Owner-DDL** `ALTER TABLE … DISABLE TRIGGER`
und stellt sie per `ENABLE ALWAYS` wieder her — alles in **einem**
`transaction.atomic()` (SIGKILL-sicher).

Konsequenz für den Betrieb: Der Wartungspfad braucht **Owner-Rechte**. Die
Management-Commands `enforce_retention`, `backfill_audit_chain`,
`reencrypt_fields` und `load_package` (M2, Refs
#1058 — RLS-Bypass-Guard
bricht sonst fail-loud ab) müssen daher als **Owner-/Admin-Rolle**
(`POSTGRES_ADMIN_USER`) laufen — das ist über den ENV-Override
(`POSTGRES_USER`/`POSTGRES_PASSWORD` auf die Admin-Werte) bereits so
eingerichtet (Retention-Cron § 6). Läuft ein solcher
Command versehentlich als App-Rolle, bricht der Bypass mit `must be owner of
table core_auditlog` ab — Fail-loud, kein stiller Audit-Verlust.

Dieselbe Rollen-Voraussetzung gilt fuer **RunPython-Backfills auf RLS-Tabellen**:
sie laufen unter der Migrationsrolle und werden seit
#1746 von
`assert_migration_role_bypasses_rls(schema_editor)` als erster Zeile abgesichert.
Der `facility_id`-Backfill der Attachment-Migration `0130`
(#1738) nutzt diesen
Guard — laeuft `migrate` versehentlich als App-Rolle, bricht er fail-loud ab,
statt still **0 Zeilen** zu backfillen und die Migration als erfolgreich zu
melden. `docker-migrate.sh` setzt den Admin-Override bereits automatisch.

Das frühere `GRANT SET ON PARAMETER session_replication_role` wird **nicht mehr
benötigt** und ist aus dem Init-Script entfernt. Auf Bestands-DBs kann es als
Aufräum-Schritt entzogen werden (optional, Least Privilege):

```sql
-- als Superuser (postgres), in der Anwendungs-DB, optional:
REVOKE SET ON PARAMETER session_replication_role FROM anlaufstelle_admin;
```

**Lock-Verhalten & Wartungsfenster (wichtig).** `ALTER TABLE … DISABLE/ENABLE
TRIGGER` nimmt einen `ShareRowExclusiveLock` auf die Tabelle und **hält ihn bis
zum Commit** der umschließenden Transaktion. Dieser Lock konfligiert mit dem
`RowExclusiveLock` gewöhnlicher `INSERT`s — solange ein Bypass-Lauf offen ist,
**blockieren parallele Audit-/EventHistory-`INSERT`s** (nahezu jeder mutierende
Request). Das frühere `session_replication_role = replica` nahm diesen
Tabellen-Lock nicht; die Owner-DDL-Umstellung (A1.4) ist hier eine bewusste
Regression zugunsten der Sicherheit. Bei `Client.anonymize()` kommt ein
**Lock-Upgrade-Deadlock-Risiko** hinzu (erst `RowExclusiveLock` durch die
EventHistory-`INSERT`s, dann `ShareRowExclusiveLock` durch das `DISABLE`).

Betriebsregeln daraus:

- Die Bypass-Commands `enforce_retention` (Retention-Cron, § 6),
  `backfill_audit_chain`, `reencrypt_fields` und die DSGVO-Anonymisierung
  (`Client.anonymize()`) in **Wartungs-/Nebenzeiten** mit geringer Schreiblast
  fahren — nicht zur Stoßzeit.
- Einen **`lock_timeout` als Rollen-/Session-Default** für die Wartungs-/Owner-
  Rolle setzen, damit ein blockiertes `ALTER TABLE` nicht unbegrenzt wartet,
  sondern fail-loud abbricht und retry-bar bleibt:

  ```sql
 -- als Superuser (postgres): begrenzt die Lock-Wartezeit der Wartungsläufe.
 ALTER ROLE anlaufstelle_admin SET lock_timeout = '5s';
 ```

  Bewusst **nicht** im Bypass-Code hartkodiert (`SET LOCAL lock_timeout`): ein
  fester Wert würde ohne Betriebsprofil legitime, u. U. synchron im Request
  laufende `anonymize`-Läufe in spurious Hard-Fails drehen, und ein belastbarer
  Regressionstest bräuchte zwei konkurrierende Sessions (auf dem CI flaky). Der
  Timeout ist daher operativ steuerbar (ADR-033 § Consequences).

**Verifikation** (App-Rolle kann Trigger NICHT abschalten, Owner-Rolle schon):

```sql
-- App-Rolle (kein Owner) -> muss scheitern:
SET ROLE anlaufstelle; -- POSTGRES_USER
ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable; -- ERROR: must be owner
RESET ROLE;
-- Trigger-Ruhezustand -> 'A' (ENABLE ALWAYS):
SELECT tgname, tgenabled FROM pg_trigger
WHERE tgname IN ('auditlog_immutable','eventhistory_no_update','eventhistory_no_delete');
```

### 9.6 Rollout der Runtime-/Owner-Rollentrennung auf Bestandsinstallationen (A1.4)

**Fresh DBs** ziehen die Trennung automatisch auf
([`deploy/postgres-init/01-app-role.sh`](../deploy/postgres-init/01-app-role.sh):
Admin wird Owner, App bekommt nur DML). **Bestandsinstallationen** (App ist noch
DB-Owner) brauchen einen **einmaligen, Maintainer-geführten** Ownership-Umzug —
kurze Downtime empfohlen (keine schreibenden Requests währenddessen). Ablauf als
**Bootstrap-Superuser** (`postgres`), in der Anwendungs-DB:

```sql
-- 0. Vorher: web-Service stoppen / in Wartung (keine App-Schreibzugriffe).
-- 1. Ownership Datenbank + alle Objekte auf die Admin-Rolle uebertragen.
ALTER DATABASE anlaufstelle OWNER TO anlaufstelle_admin;
REASSIGN OWNED BY anlaufstelle TO anlaufstelle_admin; -- Tabellen/Sequenzen/Views
-- 2. App-Rolle: Ownership weg, nur noch DML.
GRANT USAGE ON SCHEMA public TO anlaufstelle;
ALTER DEFAULT PRIVILEGES FOR ROLE anlaufstelle_admin IN SCHEMA public
 GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anlaufstelle;
ALTER DEFAULT PRIVILEGES FOR ROLE anlaufstelle_admin IN SCHEMA public
 GRANT USAGE, SELECT ON SEQUENCES TO anlaufstelle;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anlaufstelle;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anlaufstelle;
-- 3. Trigger haerten (macht sonst die Migration 0107 beim naechsten Deploy):
-- kann entfallen, wenn direkt danach `docker-migrate.sh` laeuft.
```

Danach normal deployen (`docker-migrate.sh` wendet Migration `0107` an, falls
noch nicht geschehen) und mit § 9.5-Verifikation prüfen.

**Fail-loud-Gate (Refs #1026):**
`check_db_roles` prüft seit A1.4 **zusätzlich zur Attribut-Prüfung die Ownership**.
Solange der Umzug oben noch nicht gelaufen ist (App-Rolle ist noch DB-/Tabellen-
Owner), meldet das Gate `FAIL … Ownership-Umzug … § 9.6` und bricht mit **Exit 1**
ab — `docker-migrate.sh` stoppt den Deploy, bevor neue Web-Replicas live gehen.
Das verhindert den früheren stillen Fail-open, bei dem der Deploy trotz noch
App-owned Tabellen grün wurde (das Attributprofil App/Admin allein bleibt beim
Ownership-Umzug ja gleich). Nach durchgeführtem Umzug (Admin ist Owner) ist das
Gate wieder grün.

> **Reihenfolge bei Bestandsinstallationen mit ausstehendem Umzug (Refs
> #1738):** Erst den
> Ownership-Umzug oben fahren, **dann** deployen. Die Attachment-Kette `0129`–`0131`
> braucht beides — Owner-Rechte fuer die DDL (`DROP`/`CREATE POLICY`, `ALTER TABLE`)
> und BYPASSRLS fuer den `facility_id`-Backfill in `0130`. Laeuft `migrate` noch
> unter der App-Rolle, stoppt bereits `check_db_roles` den Deploy (Exit 1); ein
> halb angewandter Zustand zwischen `0130` und `0131` entsteht dadurch nicht.

> **Hinweis `normalize_db_ownership`:** Der One-Shot-Migrate-Job ruft weiterhin
> `normalize_db_ownership` — im neuen Modell ein **No-op**, weil `migrate` als
> Admin = DB-Owner läuft (`current_user == db_owner`). Er bleibt als idempotentes
> Sicherheitsnetz erhalten.

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
 AND NOT (password LIKE 'pbkdf2_%') -- unusable = set_unusable_password
 AND date_joined < now - interval '7 days'
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
User.objects.filter(username='<username>').delete
"
```

**Cron-Empfehlung:** Wochen-Audit als reine Report-Query (siehe Abschnitt
[3.2](#32-cron-sidecar-standard)) — kein automatisches Loeschen, da ein nicht
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

Stuendlich zur 15. Minute (siehe [3.2](#32-cron-sidecar-standard)). Bei hoher
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

In `Caddyfile` (und den Varianten `Caddyfile.dev`, `Caddyfile.demo`, `Caddyfile.staging`) wird `X-Forwarded-Proto` **nicht explizit** gesetzt — die blanke Direktive genuegt:

```
reverse_proxy web:8000
```

Caddys `reverse_proxy` setzt `X-Forwarded-Proto` (sowie `X-Forwarded-For`/`X-Forwarded-Host`) per Default selbst auf den tatsaechlichen Verbindungsstatus und ueberschreibt dabei einen vom Client mitgeschickten Wert. `SECURE_PROXY_SSL_HEADER` verlaesst sich also auf dieses Caddy-Default-Verhalten, **nicht** auf eine explizite `header_up`-Direktive. (Ein zusaetzliches `header_up X-Forwarded-Proto {scheme}` waere redundant.)

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
# Dump als Admin-Rolle (BYPASSRLS) ueber den Container-Socket — wie backup.sh../scripts/ops/backup.sh
docker compose -f docker-compose.prod.yml exec -T db \
 sh -c 'pg_dump -U "$POSTGRES_ADMIN_USER" -Fc -Z 6 "$POSTGRES_DB"' > /tmp/upgrade-pg16.dump

# 2. DB stoppen, Volume-Tarball als byte-genauen Rollback-Anker ziehen
# (Volume-Name pruefen: docker volume ls | grep pgdata)
docker compose -f docker-compose.prod.yml stop db
docker run --rm -v anlaufstelle_pgdata:/from -v "$PWD":/to alpine \
 tar czf /to/pgdata-pg16.tar.gz -C /from.

# 3. Alten DB-Container + Volume entfernen — erst NACH Schritt 1+2!
docker compose -f docker-compose.prod.yml rm -f db
docker volume rm anlaufstelle_pgdata

# 4. Neuen Release-Stand einspielen (postgres:18-alpine + neuer Mount)
git pull # bzw. Release-Paket

# 5. Nur DB hochfahren: frisches initdb (PG 18, Checksums on) + Init-Script
# legt App-/Admin-Rolle an. Log muss fehlerfrei sein.
docker compose -f docker-compose.prod.yml up -d db
docker compose -f docker-compose.prod.yml logs db | grep -icE 'error|fatal' # 0

# 6. Restore als Bootstrap-Superuser via Container-Socket (bypasst RLS/FORCE-RLS,
# wie scripts/ops/restore-drill.sh; --clean/--if-exists ist re-run-sicher)
docker compose -f docker-compose.prod.yml exec -T db \
 sh -c 'pg_restore -U postgres -d "$POSTGRES_DB" --clean --if-exists' < /tmp/upgrade-pg16.dump

# 7. Verifizieren: Rollen-Gate, RLS aktiv, Stichproben
docker compose -f docker-compose.prod.yml run --rm -T \
 --entrypoint python web manage.py check_db_roles # Exit 0
docker compose -f docker-compose.prod.yml exec -T db sh -c \
 'psql -U postgres -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity"' # > 0

# 8. Migrationen als One-Shot (i. d. R. No-op) + Stack hoch + Health (§ 1.2/1.3)
docker compose -f docker-compose.prod.yml run --rm --entrypoint=/app/docker-migrate.sh web
docker compose -f docker-compose.prod.yml up -d web caddy
curl -sf https://$DOMAIN/health/ | python3 -m json.tool

# 9. Maintenance-Mode aus. Arbeits-Dump enthaelt KLARTEXT-Daten — sofort loeschen;
# Tarball nach Bewaehrungsfrist (z. B. 1 Woche) entsorgen.
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

### 13.4 Künftige Major-Upgrades (18 → 19 und später) — Vorausverweis

> **Platzhalter (Refs #1039).** Der oben beschriebene **dump → frisches Cluster → restore**-Pfad ist das **generische Muster für jeden** PostgreSQL-Major-Sprung, nicht nur 16→18. Künftige Upgrades (18→19 usw.) folgen denselben Schritten aus §13.1/§13.2; anzupassen sind im Wesentlichen nur die Versionsnummern, das `postgres:<N>-alpine`-Image und ggf. der `PGDATA`-Mountpfad, falls ein neues Image-Layout das Verzeichnis erneut verschiebt (wie der Wechsel auf `/var/lib/postgresql/18/docker` bei 18).

Vor dem nächsten Major-Sprung prüfen:

- **Release-Notes der Ziel-Major** auf inkompatible Änderungen (Datenformat, entfernte Funktionen, geänderte Defaults — bei 18 z. B. Data-Checksums per Default).
- **Image-Layout/`VOLUME`-Pfad** des neuen `postgres:<N>-alpine` — bei Änderung die Mounts in allen Compose-Dateien (`prod`/`staging`/`dev`) nachziehen, analog #1039.
- **Verfügbarkeit von `pg_dump`/`pg_restore` der Ziel-Major** im Image (Cross-Version-Dump-Kompatibilität).

Diese Notiz ist bewusst knapp gehalten; der ausführliche, lokal durchgespielte Ablauf bleibt in §13.1–§13.3 und wird beim tatsächlichen nächsten Upgrade unter einem neuen Issue konkretisiert.

---

## Kurzreferenz

```text
Backup erstellen./scripts/ops/backup.sh
Backup wiederherstellen./scripts/ops/restore.sh <datei.sql.gz.enc>
Health-Check curl -sf https://$DOMAIN/health/
Container-Status docker compose -f docker-compose.prod.yml ps
Live-Logs docker compose -f docker-compose.prod.yml logs -f web
Migrations-Status docker compose -f docker-compose.prod.yml exec web python manage.py showmigrations
Django Deploy-Check docker compose -f docker-compose.prod.yml exec web python manage.py check --deploy
Retention Testlauf docker compose -f docker-compose.prod.yml exec web python manage.py enforce_retention --dry-run
Snapshot Testlauf docker compose -f docker-compose.prod.yml exec web python manage.py create_statistics_snapshots --dry-run
MV-Refresh docker compose -f docker-compose.prod.yml exec web python manage.py refresh_statistics_view
RLS-Session-Var pruefen psql -c "SELECT current_setting('app.current_facility_id', true);"
Stack stoppen docker compose -f docker-compose.prod.yml down
Stack starten docker compose -f docker-compose.prod.yml up -d
Lock-Files regenerieren make deps-lock
Lock-File-Drift prüfen make deps-check
```
