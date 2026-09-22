# Operations Runbook

Betriebshandbuch fuer Anlaufstelle. Ergaenzt das [Admin-Handbuch](admin-guide.md) um operative Prozeduren fuer Deployment, Rollback, Cron-Jobs, Log-Analyse und Troubleshooting.

**Stack:** Django 6.1 / Gunicorn / PostgreSQL 18 / Caddy 2 / Docker Compose

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
# Quelle: .env.example.
# Fuer den Staging-Stack gilt derselbe Check gegen .env.staging statt .env
# (ENV_FILE=.env.staging, Vorlage .env.staging.example) — Refs #2086.
ENV_FILE="${ENV_FILE:-.env}"
for var in POSTGRES_BOOTSTRAP_PASSWORD POSTGRES_ADMIN_USER POSTGRES_ADMIN_PASSWORD; do
    val=$(grep -E "^${var}=" "$ENV_FILE" | cut -d= -f2-)
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
    --entrypoint=/app/docker-migrate.sh maintenance

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
# Erwartete anonyme Antwort: {"status": "ok"}

# Migrations-Status verifizieren
docker compose -f docker-compose.prod.yml exec web \
  python manage.py showmigrations | grep '\[ \]'
# Keine ausstehenden Migrationen → leere Ausgabe

# Django Deployment-Check
docker compose -f docker-compose.prod.yml exec web \
  python manage.py check --deploy
```

### 1.4 Compose-Stack-Abgleich

Refs #2086. Alle vier Deployment-Stacks fahren dasselbe Drei-Rollen-PostgreSQL-Modell (kanonische Beschreibung: [§ 9 „DB-Rollenmodell (Goldstandard)"](#db-rollenmodell-goldstandard)). Der Abgleich ist Soll **und** Ist — driftet ein Stack, faellt das Verhalten dort still auseinander, ohne dass ein Test es meldet:

| Stack | `deploy/postgres-init` gemountet | `db` bootstrappt als | `web` verbindet als | `maintenance` | `cron` | Env-Vorlage |
|---|---|---|---|---|---|---|
| `docker-compose.prod.yml` | ja | `postgres` (Bootstrap-Superuser) | App-Rolle `${POSTGRES_USER}` (Allowlist) | ja | ja | `.env.example` |
| `docker-compose.dev.yml` | ja | `postgres` | App-Rolle (Allowlist) | ja | nein (systemd-Timer, § 3.4) | `.env.dev.example` |
| `docker-compose.demo.yml` | ja | `postgres` | App-Rolle (Allowlist) | ja | nein (stuendlicher Reset) | `.env.demo.example` |
| `docker-compose.staging.yml` | ja (seit #2086) | `postgres` (seit #2086) | App-Rolle (Allowlist, seit #2086) | ja (seit #2086) | ja (seit #2086) | `.env.staging.example` (seit #2086) |

Invarianten, die fuer **jeden** Stack gelten:

- Der `db`-Service laedt **kein** `env_file`, sondern setzt seinen `environment:`-Block ueber Compose-Interpolation — sonst waere `POSTGRES_USER` die App-Rolle und damit der Bootstrap-Superuser.
- Jeder Stack setzt `REQUIRE_ADMIN_DB_ROLE: "true"`; der Pfad ohne separate Admin-Rolle (App-Rolle wird selbst Owner) ist damit ausgeschlossen.
- Die App-Rolle ist nirgends Owner und nirgends `SUPERUSER`/`BYPASSRLS`.
- `web` laedt **kein** `env_file`, sondern eine explizite Runtime-Allowlist (Refs #1962). Unter den **Anwendungsdiensten** sehen Admin-/Bootstrap-Secrets nur `maintenance` und `cron`; der `db`-Service bekommt sie zwangslaeufig ebenfalls (`POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `ADMIN_DB_PASSWORD`) — er legt die Rollen ja an. Er erhaelt aber ausschliesslich die dafuer noetigen Werte und keine Anwendungs-Secrets (`DJANGO_SECRET_KEY`, `ENCRYPTION_KEY*`).

- Auf **staging** verifiziert zusaetzlich der `web`-Healthcheck bei jedem Start und danach laufend die **tatsaechliche** Verbindungsrolle (`SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user`). Der reine HTTP-Check taugte dafuer nicht: `/health/` meldet bei bypassender Rolle zwar `degraded`, antwortet aber weiter mit HTTP 200, und das Detailfeld `db_role_posture` steht nur im Token-/Session-Payload. Refs #2086.
- Auf **staging** ist daran auch die **Exposition** gebunden, nicht nur die Anzeige: `caddy` haengt mit `depends_on: {condition: service_healthy, restart: true}` an `web` (Compose >= v2.17 — ohne `restart: true` liesse Compose einen bereits laufenden Caddy nach einem `web`-Update unberuehrt weiterrouten), und `Caddyfile.staging` prueft den Upstream **aktiv** auf `db_role_posture` und schickt dafuer `X-Health-Token`. `DJANGO_HEALTH_DETAIL_TOKEN` ist auf diesem Stack deshalb Pflicht — und zwar haerter als "sonst 502": der `{$…}`-Platzhalter wird beim **Adaptieren** der Caddyfile ersetzt, ohne Wert bricht die Adaption ab und **`caddy` startet gar nicht** (verifiziert gegen `caddy:2-alpine` v2.11.4). Die Diagnose steht dann in `docker compose -f docker-compose.staging.yml logs caddy`. **Betriebsregel:** nach jeder Aenderung an `web` den ganzen Stack anfassen (`docker compose -f docker-compose.staging.yml up -d`), nicht nur `up -d web` — sonst wird Caddy nicht neu gestartet und das Gate greift nicht.

Bis #2086 war `docker-compose.staging.yml` die einzige Ausnahme: ohne Init-Mount, mit `env_file` auf `db` **und** `web`. Der Webprozess verband dort als Bootstrap-Superuser, `FORCE ROW LEVEL SECURITY` war wirkungslos, und Staging validierte genau die Fehlerklasse nicht, fuer die es gedacht ist. Der Guard `TestStagingStackRoleSeparation` in `src/tests/test_architecture_guards_ops.py` pinnt den neuen Zustand.

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

#### Sonderfall: Attachment-Kette `0129`–`0131` (owner-generische Dateianhaenge) + Haertung `0133`

Milestone 24 (#1738,
[ADR-039](adr/039-owner-generische-dateianhaenge.md)) baut die Anhang-Tabelle um.
Drei Migrationen gehoeren zwingend zusammen — **`0130` und `0131` duerfen nie in
getrennten Releases ausgeliefert werden**; `0133` reicht die Composite-FK-Haertung
nach und ist unabhaengig davon rueckrollbar:

| Migration | Was sie tut |
|---|---|
| `0129_rename_eventattachment_to_attachment` | Reines `RenameModel` (`core_eventattachment` → `core_attachment`). Policies, RLS-Flags und GRANTs haengen an der Tabellen-OID und **ueberleben** das Rename — hier wird nichts an der Policy angefasst. |
| `0130_attachment_owner_facility` | Neue Spalte `facility_id` (nullable → Backfill aus `event.facility` unter der **Admin-/Owner-Rolle**, BYPASSRLS → NOT NULL), `event_id` wird nullable, `client_id`/`case_id` kommen dazu, CheckConstraint `attachment_exactly_one_owner`, Index. **Reverse-Guard als letzte Operation** (rueckwaerts also als erste). |
| `0131_rls_attachment` | Policy-Swap auf `core_attachment`: **direkte** facility-Klausel statt Join ueber `core_event`, inkl. `is_super_admin`-Branch. Der Reverse stellt die Join-Policy wieder her und laesst RLS bewusst **aktiv**; er traegt einen eigenen Rollback-Guard. |
| `0133_attachment_composite_fk` | Nachgereichte Haertung (#1780): UNIQUE-Begleitindizes `(id, facility_id)` auf `core_event`/`core_client`/`core_case` + je ein zusammengesetzter FK `core_attachment (owner_fk, facility_id)`. **Kann an Bestandsdaten scheitern** — ein Vorwaerts-Preflight meldet divergente Zeilen (Owner in Einrichtung A, Zeile mit `facility_id` von B) je Owner-Sorte mit Anzahl, bevor das `ALTER TABLE` laeuft. Solche Zeilen vor dem erneuten Migrieren angleichen oder samt `.enc`-Datei entfernen. Der Reverse ist vollstaendig und **ohne** Rollback-Guard (er nimmt nur Haertung zurueck). |

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
# 1. Hintergrundjobs anhalten; db und web bleiben fuer Restore und Medien noetig
docker compose -f docker-compose.prod.yml stop cron
docker compose -f docker-compose.prod.yml up -d db web

# 2. Backup wiederherstellen — DB plus optional Medien.
#    restore.sh fuellt eine frische Wiederherstellungs-Datenbank, verifiziert sie
#    gegen das signierte Manifest und schaltet erst danach unter Schreibruhe um;
#    der bisherige Stand bleibt als <POSTGRES_DB>_vor_restore_<Zeitstempel> liegen.
#    Ohne --yes (oder RESTORE_CONFIRM=ja) bricht der Lauf vor jeder Aenderung ab.
./scripts/ops/restore.sh --yes \
    backups/daily/anlaufstelle_YYYY-MM-DD_HHMMSS.sql.gz.enc \
    backups/daily/anlaufstelle_YYYY-MM-DD_HHMMSS_media.tar.gz.enc

# 3. Altes Image-Tag in docker-compose.prod.yml setzen

# 4. Stack starten (restore.sh laesst web und cron gestoppt)
docker compose -f docker-compose.prod.yml up -d

# 5. Verifizieren
curl -sf https://$DOMAIN/health/

# 6. Erst nach erfolgreicher Abnahme den gesicherten Vorstand entfernen
docker compose -f docker-compose.prod.yml exec -T db \
    psql -U "${POSTGRES_SUPERUSER:-postgres}" -d postgres \
    -c 'DROP DATABASE "anlaufstelle_vor_restore_<Zeitstempel>";'
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

> **⚠️ Das Backup laeuft weiterhin NICHT automatisch.** Der Sidecar deckt alle Jobs
> ab, die als `manage.py`-Kommando im Container laufen. `backup.sh` faehrt dagegen
> `docker compose -f docker-compose.prod.yml exec` (pg_dump im db-Container, tar im web-Container) und
> braeuchte dafuer den Docker-Socket im Sidecar — das waere root-aequivalent auf dem
> Host und wuerde die Runtime-Haertung des gesamten Stacks (`cap_drop: ALL`,
> `no-new-privileges`, `read_only`) entwerten. Backup bleibt daher **ein**
> Host-Crontab-Eintrag, siehe [§3.3](#33-host-cron-backup--restliche-host-jobs).
> Ohne ihn hat eine Installation **kein Backup** — ohne Fehlermeldung, nur Stille;
> `initial-setup.sh` warnt, wenn es keinen solchen Eintrag findet.
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
| `verify_audit_chain` | Taeglich 03:30 (nach Retention) | Sidecar | HMAC-Integritaetskette des AuditLog verifizieren; Exit-Code != 0 bei Tamper-Verdacht (Refs #1070). Ein vollstaendig gruener Lauf schreibt den Last-Run-Marker `AUDIT_CHAIN_VERIFIED` — sichtbar als Zeile „Audit-Ketten-Pruefung" unter `/system/` und im Compliance-Dashboard (Refs #1872). Ein Tamper-Fund schreibt **keinen** Marker, die Ampel laeuft also auf ueberfaellig |
| `create_statistics_snapshots` | Monatlich 1. Tag 04:00 | Sidecar | Monats-Aggregate sichern bevor Events geloescht werden |
| `cleanup_orphan_storage_files` | Taeglich 04:30 (nach Snapshots) | Sidecar | Verwaiste `.enc`-Dateien ohne `Attachment`- **und ohne offenen `ImportJob`-Record** loeschen (owner-unabhaengig: Ereignis-, Personen- und Fall-Anhaenge; CSV-Import-Uploads unter `<facility>/imports/` sind ueber `ImportJob.storage_filename` geschuetzt und zählen bei der Kontingent-Durchsetzung mit, solange der Lauf offen ist — `commit_import`/`abort_import` leeren das Feld, finale Laeufe schuetzen nicht); MUSS als Admin-DB-Rolle (BYPASSRLS) laufen, sonst Fail-Loud — ohne Bypass saehe der Lauf zudem 0 `ImportJob`-Zeilen und loeschte die offenen Uploads aller Einrichtungen (Refs #662, #1554, #1738, #1060, #2180) |
| `clearsessions` | Taeglich 05:00 | Sidecar | Abgelaufene Zeilen aus `django_session` entfernen (Speicherbegrenzung nach Art. 5 Abs. 1 lit. e DSGVO) |
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
ab (read-only in den Container gemountet). Nach
`docker compose -f docker-compose.prod.yml up -d` laufen
Retention, Audit-Ketten-Verifikation, Snapshots, Orphan-Cleanup,
Session-Bereinigung, Breach-Detection und MV-Refresh ohne weitere Einrichtung.

**Warum supercronic und nicht `cron`/`crond`:** klassische Cron-Daemons
daemonisieren sich weg (Container haette keinen Vordergrundprozess), loggen nach
Syslog statt stdout (kein `docker compose -f docker-compose.prod.yml logs`) und brauchen setuid-Bits, die
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
Das facility-unabhaengige Django-Kommando `clearsessions` benoetigt den
RLS-Bypass nicht, laeuft aber unter derselben Service-Rolle mit.

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
Container beweist nicht, dass die Jobs *wirken*. Die instrumentierten
Anwendungskommandos schreiben nach erfolgreichem Lauf einen
`*_RUN_COMPLETED`-Marker ins AuditLog, den das Compliance-Dashboard
(`/system/compliance/`, Kategorie „Hintergrundjobs") und `stale_jobs` im
[`/health/`-Payload](monitoring-guide.md) auswerten. Djangos Standardkommando
`clearsessions` schreibt keinen solchen Marker; sein Start und Exit-Code stehen
nur im supercronic-Log. Das Host-Monitoring sollte deshalb auf Fehlschlaege in
den `cron`-Logs alarmieren — insbesondere auf `verify_audit_chain`, das bei
Tamper-Verdacht mit Exit-Code != 0 endet.

**Aenderungen am Zeitplan** gehoeren in `deploy/crontab` (Repo, versioniert);
danach `docker compose -f docker-compose.prod.yml restart cron`. Der Guard
`src/tests/test_ops_cron_sidecar.py` haelt die Datei mit der Tabelle in §3.1
synchron.

### 3.3 Host-Cron: Backup + restliche Host-Jobs

Zwei Jobs bleiben Host-seitig, weil sie im Sidecar nicht laufen koennen:

- **`backup.sh`** — braucht `docker compose -f docker-compose.prod.yml exec` (pg_dump im db-Container, tar
  im web-Container) und damit den Docker-Socket. Ein Socket-Mount im Sidecar
  waere root-aequivalent auf dem Host.
- **Invite-Token-Audit / Health-Check** — `psql`-Abfrage bzw. externer
  `curl`-Check; ersteres hat der App-Container nicht an Bord, letzteres soll
  bewusst *ausserhalb* des ueberwachten Stacks laufen.

**Backup-Pfade:** `BACKUP_HOST_DIR` bezeichnet das Basisverzeichnis auf dem
Host (Default `<Repo>/backups`; relative Werte wie `./backups` werden
gegen den Repo-Root aufgeloest). `scripts/ops/backup.sh` schreibt darunter
nach `daily/`, `weekly/` und `monthly/`; der Prod-Zweig von
`restore-drill.sh` liest aus demselben `daily/`-Verzeichnis.
`BACKUP_DIR` ist ausschließlich das read-only Mount-Ziel im
`web`-Container (Default `/backups`) und wird von beiden Host-Skripten
bewusst ignoriert. Compose mountet `BACKUP_HOST_DIR` nach `BACKUP_DIR`,
damit `/health/` dieselben Dateien sieht.

**Veroeffentlichung eines Satzes:** Alle Artefakte eines Laufs entstehen in
`daily/.in-progress-<Zeitstempel>/` und werden erst nach dem vollstaendigen
Lauf ins Tagesverzeichnis verschoben — das signierte Manifest als letztes.
Das Manifest ist damit der Erfolgsmarker: Die Systemuebersicht und der
Compliance-Check „Letztes Backup" werten ausschliesslich Saetze aus, deren
Manifest samt der darin referenzierten Dateien (Dump, HMAC-Sidecars, bei
gebundenem `media_sha256` das Medienarchiv, bei gebundenem `hwm_sha256` der
Audit-Anker) vorliegt. Ein abgebrochener Lauf laesst den Zustand auf
„fehlt/unbekannt" stehen statt auf „aktuell". Ein liegengebliebenes
`.in-progress-*`-Verzeichnis stammt von einem hart abgebrochenen Lauf und
kann geloescht werden.

**Voraussetzungen fuer den Cron-Eintrag:** Die folgenden Beispiele sind
ausdruecklich fuer `sudo crontab -e`, also die fuenf Zeitfelder der
**Root-Crontab**. Root wird empfohlen, weil `backup.sh` den Docker-Socket
benoetigt. Nicht unveraendert nach `/etc/crontab` kopieren; dort waere nach den
fuenf Zeitfeldern ein sechstes Feld fuer das Ausfuehrungskonto erforderlich.
Vorher Host-Zeitzone und Cron-Dienst einrichten sowie die Logdateien mit
passenden Rechten anlegen:

```bash
sudo timedatectl set-timezone Europe/Berlin
sudo systemctl enable --now cron
sudo sh -c '[ -e /var/log/anlaufstelle-backup.log ] || install -o root -g adm -m 0640 /dev/null /var/log/anlaufstelle-backup.log'
sudo sh -c '[ -e /var/log/anlaufstelle-invites.log ] || install -o root -g adm -m 0640 /dev/null /var/log/anlaufstelle-invites.log'
sudo sh -c '[ -e /var/log/anlaufstelle-health.log ] || install -o root -g adm -m 0640 /dev/null /var/log/anlaufstelle-health.log'
```

Fuer alle `anlaufstelle-*.log` unter `/var/log` eine Logrotate-Regel mit
regelmaessiger Rotation, Komprimierung und begrenzter Aufbewahrung einrichten;
nach Aenderungen `sudo logrotate --debug /etc/logrotate.conf` pruefen.

```cron
# Anlaufstelle – Host-Cron (Backup + Hygiene). Alles Weitere macht der
# cron-Sidecar aus §3.2 — diese Eintraege NICHT dupliziert einrichten!
# Via `sudo crontab -e` in der Root-Crontab einrichten (fuenf Zeitfelder)

# Backup (taeglich 02:00, vor der Retention im Sidecar um 03:00)
0 2 * * * cd /opt/anlaufstelle && ./scripts/ops/backup.sh >> /var/log/anlaufstelle-backup.log 2>&1

# Invite-Token-Audit (woechentlich So 05:00, Details siehe Abschnitt 10)
0 5 * * 0 cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT username, email, date_joined FROM core_user WHERE last_login IS NULL AND NOT (password LIKE 'pbkdf2_%') AND date_joined < now() - interval '7 days';" >> /var/log/anlaufstelle-invites.log 2>&1

# Health-Check (alle 5 Minuten)
*/5 * * * * curl -sf https://DOMAIN/health/ > /dev/null || echo "Anlaufstelle health check failed at $(date)" >> /var/log/anlaufstelle-health.log
```

Die Host-Cron-Eintraege oben laufen in der **Systemzeitzone des Hosts** (nicht
in der Sidecar-`TZ`-Variable aus [§3.2](#32-cron-sidecar-standard)) — weicht sie
von `Europe/Berlin` ab (z. B. UTC-Hosts), faellt `0 2 * * *` je nach Jahreszeit
auf 03:00 oder 04:00 Ortszeit und die zugesagte Reihenfolge „Backup vor
Retention (03:00)" kippt oder wird zum Gleichstand; Host-TZ auf `Europe/Berlin`
setzen oder die Host-Cron-Zeit entsprechend vorziehen.

<details>
<summary>Vollstaendige Host-Crontab — Alternative fuer Setups OHNE Sidecar</summary>

Relevant, wenn der Stack ohne `docker-compose.prod.yml` betrieben wird (z. B.
k8s, eigene Orchestrierung) und deshalb kein `cron`-Service existiert.
Auch diese Alternative setzt den laufenden Cron-Dienst, die richtige
Host-Zeitzone, vorab angelegte beschreibbare Logdateien und eine Logrotate-Regel
voraus. Das Beispiel gehoert ausschliesslich in `sudo crontab -e`.

```cron
# Anlaufstelle – Operative Cron-Jobs (Alternative: ohne cron-Sidecar)
# Via `sudo crontab -e` in der Root-Crontab einrichten (fuenf Zeitfelder)

# Backup (taeglich 02:00, vor Retention)
0 2 * * * cd /opt/anlaufstelle && ./scripts/ops/backup.sh >> /var/log/anlaufstelle-backup.log 2>&1

# Retention-Durchsetzung (taeglich 03:00)
# MUSS mit der Admin-DB-Rolle (BYPASSRLS) laufen, sonst bricht das Kommando
# fail-loud ab (§9 DB-Rollenmodell), statt faelschlich 0 verarbeitete Zeilen
# als RETENTION_RUN_COMPLETED zu melden.
0 3 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance enforce_retention >> /var/log/anlaufstelle-retention.log 2>&1

# Audit-Ketten-Verifikation (taeglich 03:30, nach Retention; Refs #1070)
# MUSS mit der Admin-DB-Rolle (BYPASSRLS) laufen, sonst bricht das Kommando
# fail-loud ab (§9 DB-Rollenmodell) statt faelschlich "intakt" zu melden.
30 3 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance verify_audit_chain >> /var/log/anlaufstelle-audit-verify.log 2>&1

# Statistik-Snapshots (monatlich am 1. um 04:00)
0 4 1 * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance create_statistics_snapshots >> /var/log/anlaufstelle-snapshots.log 2>&1

# Orphan-Storage-Cleanup (taeglich 04:30, nach den Snapshots; verwaiste
# .enc-Dateien ohne Attachment-Record entfernen — owner-unabhaengig, also
# Ereignis-, Personen- und Fall-Anhaenge, Refs #662/#1554/#1738)
# MUSS mit der Admin-DB-Rolle (BYPASSRLS) laufen, sonst bricht das Kommando
# fail-loud ab (§9 DB-Rollenmodell): als RLS-gefilterte App-Rolle saehe der Lauf
# 0 registrierte Attachments und wuerde JEDE .enc-Datei als Orphan loeschen.
30 4 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance cleanup_orphan_storage_files >> /var/log/anlaufstelle-orphan-cleanup.log 2>&1

# Abgelaufene Django-Sitzungen bereinigen (taeglich 05:00)
0 5 * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance clearsessions >> /var/log/anlaufstelle-clearsessions.log 2>&1

# Breach-Detection-Scan (stuendlich zur 30. Minute, Refs #685)
30 * * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance detect_breaches >> /var/log/anlaufstelle-breach.log 2>&1

# Materialized-View-Refresh (stuendlich zur 15. Minute, Details siehe Abschnitt 11)
15 * * * * cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml run --rm -T maintenance refresh_statistics_view >> /var/log/anlaufstelle-mv.log 2>&1

# Invite-Token-Audit (woechentlich So 05:00, Details siehe Abschnitt 10)
0 5 * * 0 cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT username, email, date_joined FROM core_user WHERE last_login IS NULL AND NOT (password LIKE 'pbkdf2_%') AND date_joined < now() - interval '7 days';" >> /var/log/anlaufstelle-invites.log 2>&1

# Health-Check (alle 5 Minuten)
*/5 * * * * curl -sf https://DOMAIN/health/ > /dev/null || echo "Anlaufstelle health check failed at $(date)" >> /var/log/anlaufstelle-health.log
```

```

</details>

**Reihenfolge beachten:** Backup (02:00) → Retention (03:00) → Audit-Ketten-Verifikation (03:30) → Snapshots (04:00, monatlich) → Orphan-Cleanup (04:30) → Session-Bereinigung (05:00) → Breach-Detection/MV-Refresh (stuendlich). Backup muss vor Retention laufen, damit geloeschte Daten im Backup enthalten sind. Die Audit-Ketten-Verifikation laeuft nach der Retention, damit die von `prune_auditlog` frisch geschriebenen Checkpoint-Grenzen bereits in der Kette stehen. Der MV-Refresh nutzt `CONCURRENTLY` und blockiert laufende Reader nicht. Genau diese Reihenfolge bildet der Sidecar-Zeitplan in `deploy/crontab` ab — deshalb ist die `TZ`-Einstellung aus [§3.2](#32-cron-sidecar-standard) betriebsrelevant und nicht Kosmetik.

### 3.4 Dev (systemd-Timer)

Auf `dev.anlaufstelle.app` laufen die Jobs **nicht** per Host-Crontab, sondern als
systemd-Timer, die `dev-ops/deploy/install-timers.sh` (dev-only)
installiert — aufgerufen bei **jedem** Deploy durch `dev-ops/deploy/deploy-dev.sh` (dev-only)
(idempotent). Der Dev-Stack laeuft ueber `docker-compose.dev.yml` und bekommt
den `cron`-Sidecar aus [§3.2](#32-cron-sidecar-standard) bewusst **nicht**:
beides parallel wuerde jeden Job doppelt ausfuehren, und die Timer sind hier
zusaetzlich der Testfall fuer den Host-Scheduler-Pfad, den k8s-Setups
oder Setups mit eigener Orchestrierung ohne unser Compose-File nutzen.

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

# Teilfehler (Refs #2118): ein Fehler in einer Einrichtung oder einem Monat
# reisst den Lauf nicht mehr ab. Jede betroffene Einrichtung/jeder Monat
# erscheint als Fehlerzeile im Log (Host-Cron: /var/log/anlaufstelle-
# snapshots.log; Sidecar: `docker compose -f docker-compose.prod.yml logs cron` bzw. Anwendungs-Log) und
# auf stderr. Der Lauf selbst endet danach mit Exit-Code != 0 und einer
# Zusammenfassung — ein rot endender Cron-Lauf bedeutet also "mindestens ein
# Snapshot fehlt", nicht "alles fehlgeschlagen". Nach Behebung der Ursache den
# betroffenen Monat gezielt per --year/--month nachholen (Beispiel oben).

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
docker compose -f docker-compose.prod.yml run --rm maintenance \
  purge_orphan_field_data --dry-run # Vorschau
docker compose -f docker-compose.prod.yml run --rm maintenance \
  purge_orphan_field_data # ausfuehren

# Facility-Export (Refs #1060, M4 / DSGVO Art. 20): kompletter fachlicher
# Datenbestand EINER Einrichtung als versioniertes JSON (Schema v1, Referenz
# docs/facility-export-schema.md). MUSS mit der Admin-DB-Rolle laufen
# (BYPASSRLS) — sonst fail-loud statt stillschweigend leerer Listen.
#
# ACHTUNG Datenschutz: die Ausgabedatei enthaelt ENTSCHLUESSELTE Klartext-
# Werte (inkl. Art.-9-Inhalten) — wie ein DB-Backup behandeln: nur auf
# verschluesselte Ziele schreiben, Uebergabe nur an Berechtigte, danach
# sicher loeschen. Laufzeit skaliert mit der Anhangsmenge (SHA-256 wird
# ueber die entschluesselten Inhalte gestreamt); --no-file-hashes
# ueberspringt das Hashing (sha256: null).
mkdir -p./exports && chmod 700./exports
docker compose -f docker-compose.prod.yml run --rm \
  -v "$PWD/exports:/exports" maintenance export_facility \
  --facility "Beratungsstelle Nord" --output /exports/facility-export.json --indent 2

# Inventar der Berichtszuordnung (Refs #1848): listet fuer EINE Einrichtung
# auf, welcher Berichtsschluessel in welchem Berichts-Backend steckt (Typ- und
# Feld-Ebene) und welche Dokumentationstypen und Feldvorlagen ihn tragen
# bzw. welche keinen tragen und damit in keinem Bericht erscheinen. Reine
# Auskunft, veraendert keine Daten. MUSS mit der Admin-DB-Rolle laufen
# (BYPASSRLS) — sonst fail-loud (§ 9 DB-Rollenmodell): unter der App-Rolle
# ohne Facility-Kontext saehe ein leeres/gefiltertes Inventar wie eine
# tadellos konfigurierte Einrichtung aus.
docker compose -f docker-compose.prod.yml run --rm maintenance \
  inventory_report_bindings \
  --facility "Beratungsstelle Nord" # Textreport auf stdout
docker compose -f docker-compose.prod.yml run --rm \
  -v "$PWD/exports:/exports" maintenance inventory_report_bindings \
  --facility "Beratungsstelle Nord" --format json --output /exports/report-bindings.json

# Ersteinrichtung einer Einrichtung (Refs #1883, #2134): interaktiv Organisation,
# Einrichtung, Einstellungen, gewaehlten Katalog und ersten facility_admin anlegen.
# MUSS mit der Admin-DB-Rolle laufen (BYPASSRLS) — der Command schreibt in
# core_settings und (mit Standardkatalog) in core_documenttype/-fieldtemplate/
# -documenttypefield/-fieldgroup/-fieldcondition, allesamt FORCE-RLS-Tabellen
# mit einer USING-only-Policy, die in PostgreSQL auch den INSERT prueft. Der
# Guard greift VOR der ersten Eingabeaufforderung, damit niemand erst ein
# Passwort tippt und dann am Schreibzugriff scheitert. Der ganze Lauf ist EINE
# Transaktion: ein Abbruch laesst keine halb angelegte Einrichtung zurueck.
# Ohne Option gilt --package default. Ein Domaenen-Paket wird mit
# --package <name> als ERSATZ gegen die frische Einrichtung geladen.
# --package und --no-default-package schliessen sich aus.
# --no-default-package laesst den Katalog weg (Einrichtung startet ohne
# Dokumentationstypen und ist erst nach load_package dokumentierbar).
# Traegt die Einrichtung schon Dokumentationstypen oder Feldvorlagen, bleibt
# der Katalog UNANGETASTET — Ersteinrichtung, kein Katalog-Update (dafuer der
# naechste Befehl).
docker compose -f docker-compose.prod.yml run --rm maintenance setup_facility

# Neue Einrichtung direkt mit einem mitgelieferten Domaenen-Paket:
docker compose -f docker-compose.prod.yml run --rm maintenance \
  setup_facility --package awareness

# Standardkatalog in eine BESTEHENDE Einrichtung nachziehen (Refs #1883):
# fachlich identisch zu "load_package --file <…>/default.yaml", nur ohne
# Container-Pfad. Loescht nie etwas, schreibt aber Katalog-Eigenschaften von
# Bestandszeilen auf den Paket-Stand zurueck (Ruecksync-Tabelle in
# docs/packages.md) — deshalb erst mit --dry-run ansehen. Wiederholbar,
# gleiche Rollen-Voraussetzung.
docker compose -f docker-compose.prod.yml run --rm maintenance \
  load_package --default --facility <uuid> --dry-run

# Mitgeliefertes Domaenen-Paket nur gegen eine katalogfreie Einrichtung.
# Fremde Typ-/Feld-Slugs brechen auch den Dry-Run ab; der Doppellauf
# desselben Pakets bleibt erlaubt.
docker compose -f docker-compose.prod.yml run --rm maintenance \
  load_package --package awareness --facility <uuid> --dry-run
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
| `logger` | Logger-Name (`django`, `core`, `gunicorn.error`, `security.csp`) |
| `module` | Python-Modul |
| `request_id` | Request-ID (falls vorhanden) |
| `user_id` | Eingeloggter Benutzer (falls vorhanden) |
| `facility_id` | Aktive Einrichtung (falls vorhanden) |
| `exception` | Vollstaendiger Traceback bei Fehlern |
| `csp_violation` | CSP-Verstoss-Details als Objekt (`blocked-uri`, `document-uri`, `violated-directive` …), nur auf `security.csp` |
| `csp_violations_total` | Nur auf der Zeile `csp_violation_truncated`: wie viele Verstoesse die Meldung enthielt |
| `csp_violations_dropped` | Nur auf der Zeile `csp_violation_truncated`: wie viele davon **nicht** geloggt wurden (Deckel: 10 pro Anfrage) |
| `remote_addr` | Client-IP der Meldung (proxy-bewusst ueber `TRUSTED_PROXY_HOPS`) |
| `user_agent` | User-Agent der Meldung (Steuerzeichen entfernt, auf 512 Zeichen gekappt) |

Die letzten fuenf Felder stammen aus dem CSP-Report-Endpoint (`/csp-report/`,
Logger `security.csp`) und stehen seit #1943
in **beiden** Formaten — auch im Default `LOG_FORMAT=text`, dort als
`feld="wert"`-Paare hinter der Nachricht. Davor war von jeder Meldung nur
die nackte Zeile `csp_violation` sichtbar. Zwei Meldungstypen sind dabei
betriebsrelevant:

- `csp_violation` — ein einzelner Verstoss.
- `csp_violation_truncated` — eine Anfrage enthielt mehr als 10 Verstoesse;
  die ersten 10 sind geloggt, der Rest verworfen. Haeuft sich diese Zeile,
  ist das entweder eine grob gebrochene CSP oder ein Flooding-Versuch: die
  `remote_addr` gegen das Rate-Limit (10 Anfragen/min/IP) halten.

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

### 5.4a Cache-Backend / Multi-Worker (Refs #795, #2002)

Sobald `GUNICORN_WORKERS > 1` läuft (siehe [5.4](#54-gunicorn-timeout)), brauchen `django-ratelimit` (Login/Sudo/Health-Bruteforce-Schutz) und das Health-Detail-Caching einen **prozessübergreifenden** Cache. Andernfalls (Django-Default `LocMemCache`) hält jeder Worker seinen eigenen Zähler und die effektiven Rate-Limits ver-N-fachen sich mit der Worker-Zahl.

**Standard: der mitgelieferte `redis`-Service.**
`docker-compose.prod.yml` startet einen Redis im internen Netz und setzt für `web`, `maintenance` und `cron` `REDIS_URL=redis://redis:6379/1`; `settings/prod.py` schaltet damit auf Djangos eingebautes `RedisCache` um (kein `django-redis` nötig). Eigenschaften des Service:

- **Kein Host-Port, optionales `requirepass` als zweite Schranke** (Refs #2002). Das `internal`-Netz bleibt die erste Zugriffskontrolle. Setzt man `REDIS_PASSWORD` in der `.env`, startet Compose den Service mit `--requirepass` und bettet die Credentials automatisch in die drei `REDIS_URL`-Defaults ein; in der `.env` ist nur `REDIS_PASSWORD` zu setzen. Bleibt die Variable leer oder ungesetzt, laufen Redis und die bisherige Default-URL unverändert ohne Passwort weiter, und der Start-Check `core.W004` weist darauf hin. Die zweite Schranke ist relevant, weil der Cache Rate-Limit-Schlüssel einschließlich Client-IPs hält.
- **Keine Persistenz** (`--save ""`, `--appendonly no`, kein Volume). Im Cache liegen nur regenerierbare Daten mit kurzer TTL. Persistenz wäre hier ein Datenschutz-*Minus*: sie schriebe Rate-Limit-Schlüssel (u. a. Client-IPs, User-IDs) auf die Platte, wo keine Retention sie erfasst. Ein Redis-Neustart kostet nur einen kalten Cache.
- **Speicher gedeckelt** (`--maxmemory 256mb`, `allkeys-lru`), damit der Container nicht ins `mem_limit`-OOM läuft.

Für `REDIS_PASSWORD` nur URL-sichere Zeichen verwenden: `@`, `:`, `/` und `#` zerlegen sonst die eingebettete URL. Ein passender Wert entsteht zum Beispiel mit `openssl rand -hex 32`. Eine syntaktisch ungültige Redis-URL bricht den Start mit `core.E004` und einem verständlichen Hinweis ab; die Meldung gibt die URL nicht aus, weil sie ein Passwort enthalten kann.

Der Env-Weg ist kein Docker-Secret: Im `redis`-Container steht das Passwort als `--requirepass` in der Prozess-Kommandozeile und damit unter `/proc`; in der Docker-Containerkonfiguration der Konsumenten ist es Teil von `REDIS_URL`. Beides ist über `docker inspect` bzw. für Personen mit Docker-Daemon-Zugriff sichtbar. Docker-Zugriff ist ohnehin root-äquivalent und muss entsprechend eng begrenzt werden.

Zum Passwortwechsel den Wert ändern und Redis plus die dauerhaft laufenden Konsumenten neu erzeugen; der Cache enthält nur regenerierbare Daten. `maintenance` ist profilgebunden und übernimmt den neuen Wert bei seinem nächsten Ad-hoc-Start:

```bash
docker compose -f docker-compose.prod.yml up -d redis web cron
```

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

**Externes Redis** statt des mitgelieferten: `REDIS_URL` in der `.env` auf den eigenen Endpunkt zeigen lassen (`redis://:passwort@redis.internal:6379/1`) und dessen Credentials selbst in diese URL eintragen. Ein gesetztes `REDIS_PASSWORD` wirkt bei expliziter `REDIS_URL` nur noch auf den ungenutzten mitgelieferten Service. Der Compose-Wert ist nur ein Fallback für nicht gesetzte Variablen; der mitgelieferte Service kann dann wie oben auf 0 skaliert werden.

**Welches Backend läuft gerade?** Der `/health/`-Endpoint gibt es für interne/Token-Caller aus:

```bash
curl -sf -H "X-Health-Token: $HEALTH_DETAIL_TOKEN" https://$DOMAIN/health/ \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["cache"])'
# {'backend': 'django.core.cache.backends.redis.RedisCache', 'status': 'ok'}
```

Das ist kein Kosmetik-Feld: Ist `REDIS_URL` gesetzt, im Image aber kein `redis`-Paket vorhanden (typisch, wenn ein **älteres** `APP_VERSION`-Image mit dem neuen Compose-File gefahren wird), fällt `prod.py` absichtlich auf den `DatabaseCache` zurück und schreibt eine Warnung ins Container-Log — der Stack bleibt lauffähig, läuft aber langsamer. `cache.backend` macht diesen Zustand sichtbar; der Payload zeigt nie die `LOCATION` und damit auch kein eingebettetes Passwort. `cache.status = error` (Backend konfiguriert, aber nicht erreichbar) setzt den Gesamtstatus auf `degraded`, weil dann auch der Rate-Limit-Schutz wirkungslos ist.

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

# Belegung der Anhaenge je Einrichtung (Groesse des MEDIA_ROOT-Unterordners)
du -sh /opt/anlaufstelle/media/*/
```

> **Monitoring ersetzt kein Kontingent** (#2129). Der Disk-Check in `/health/` (`HEALTH_DISK_REQUIRED`) meldet, **dass** der Datenträger volläuft — er verhindert es nicht und kennt keine Einrichtung. Im Mehr-Mandanten-Betrieb reicht das nicht: Eine einzige Einrichtung kann den gemeinsamen `MEDIA_ROOT` mit lauter legitimen Uploads füllen, und die Folge (Upload- und Backup-Fehler) trifft dann alle. Setzen Sie deshalb ein Speicherkontingent je Einrichtung:
>
> - `FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB` (ENV, Default **2048**) — Backstop für jede Einrichtung ohne eigenes Kontingent; `0` schaltet ihn ab.
> - `Settings.max_storage_mb` je Einrichtung (Verwaltung → Einstellungen, Gruppe „Dateien") — wirksam ist immer das striktere der beiden Limits.
>
> Der **Papierkorb zählt mit**: Ein entfernter Anhang belegt seinen Platz bis zur physischen Tilgung. Auch Quelldateien offener CSV-Importe zählen bei der Durchsetzung mit, bis der Lauf übernommen oder abgebrochen wird; die Verwaltungs-Übersicht bleibt dagegen Anhänge-basiert. Läuft eine Einrichtung voll, ist das ersatzlose Löschen alter Anhänge (Leitung/Anwendungsbetreuung) der schnellste Hebel — die Verwaltungs-Übersicht weist den Papierkorb-Anteil gesondert aus. Details: [Admin-Handbuch § 2.9 „Speicherkontingente"](admin-guide.md#speicherkontingente).

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

**Restrisiko (bewusst dokumentiert, nicht gefixt):** Ein `DocumentType` kann — nur konfigurierbar durch `facility_admin`/`super_admin` — **mehrere** FILE-Felder tragen; jedes traegt unabhaengig bis zu `FILE_VAULT_MAX_UPLOAD_FILES` Dateien. Der theoretische Worst-Case je Request waechst dann um weitere `FILE_VAULT_MAX_UPLOAD_BYTES * FILE_VAULT_MAX_UPLOAD_FILES` pro zusaetzlichem FILE-Feld. Deployments mit vielfeldigen Datei-Dokumentationstypen sollten den Caddy-Cap entsprechend hochskalieren (kein automatischer Abgleich).

```bash
# 1. Aktuellen Caddy-Wert pruefen (alle vier Varianten: Caddyfile,
# Caddyfile.dev, Caddyfile.demo, Caddyfile.staging)
grep -A2 request_body Caddyfile*

# 2. App-seitiges Limit pruefen (falls per Env ueberschrieben)
docker compose -f docker-compose.prod.yml exec web \
  printenv | grep FILE_VAULT_MAX_UPLOAD
```

Ein Test-Guard haelt beide Settings synchron zum statischen Caddy-Wert (Entwickler-Repo: `src/tests/test_architecture_guards_ops.py::TestCaddyUploadBodyLimit`) — schlaegt fehl, sobald `FILE_VAULT_MAX_UPLOAD_BYTES`/`FILE_VAULT_MAX_UPLOAD_FILES` geaendert werden, ohne den Caddy-Cap nachzuziehen.

**WICHTIG — Maintainer-Handoff:** Der Caddy-Wert ist statisch im Caddyfile hinterlegt und folgt **nicht** automatisch, wenn `FILE_VAULT_MAX_UPLOAD_BYTES` **oder** `FILE_VAULT_MAX_UPLOAD_FILES` per Env ueberschrieben wird. Bei Aenderung eines der beiden App-Limits (oder bei einem Deployment mit mehrfeldigen Datei-Dokumentationstypen, s. Restrisiko oben) den Wert im jeweiligen Caddyfile manuell nachziehen und Caddy neu laden:

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

### 5.8 SMTP-Versand pruefen

Der Produktionsbetrieb nutzt Djangos SMTP-Backend, seit Django 6.1 ueber
dessen `MAILERS`-Einstellung konfiguriert. Die Namen der Umgebungsvariablen
sind davon **nicht** betroffen — eine bestehende `.env` bleibt unveraendert
gueltig. Technisch ist keiner der
`EMAIL_*`-Werte Pflicht — ohne Angabe gelten `EMAIL_HOST=localhost`, Port
`587`, STARTTLS (`EMAIL_USE_TLS=True`) und der Absender
`noreply@anlaufstelle.app`. Betrieblich, also fuer tatsaechliche Zustellung,
muss `EMAIL_HOST` in `.env` auf das erreichbare Relay zeigen; `EMAIL_PORT` und
`EMAIL_USE_TLS` nur setzen, wenn sie vom Default abweichen sollen.
`EMAIL_USE_TLS=True` verhandelt **STARTTLS** auf dem konfigurierten Port. Fuer
implizites TLS/SMTPS (klassisch Port 465) stattdessen `EMAIL_USE_SSL='True'`,
`EMAIL_USE_TLS='False'` und `EMAIL_PORT=465` setzen — ein Relay mit
ausschliesslich Port 465 laesst sich damit anbinden. Beide Schalter zugleich auf
`True` sind ausgeschlossen: der Start bricht dann mit einer Meldung ab. Verlangt der Relay Authentifizierung, sind
zusaetzlich `EMAIL_HOST_USER` und `EMAIL_HOST_PASSWORD` noetig. Fuer gueltige
Einladungs- und Reset-Links muss ausserdem `EXTERNAL_ORIGIN` die oeffentliche
Basis-URL enthalten. Der Absender soll fuer SPF, DKIM und DMARC autorisiert
sein.

**Deploy-Check `mail.E001` (Django 6.1, #2326):** `python manage.py check --deploy`
— der Web-Container fuehrt ihn bei jedem Start aus, siehe
`docker-entrypoint.sh` — bricht mit `mail.E001` ab, wenn der Standard-Mailer
ein Entwicklungs-Backend ist (Konsole, Datei, Arbeitsspeicher, Dummy). Die
Produktiv-Einstellungen nutzen immer das SMTP-Backend; der Start setzt also
ein SMTP-*Backend* voraus, nicht einen erreichbaren Server. Fehlt ein
erreichbares Relay, startet die Anwendung, aber Einladungs- und Reset-Mails
scheitern beim Versand: Warnung in **Verwaltung → Zugaenge**, `/health/`-Feld
`smtp` = `unreachable`, Verbindungsfehler im Web-Log; Notfall-Weg ist das
Initialpasswort ueber den Django-Admin (unten). Die Live-Dev- und
Demo-Einstellungen (`devlive`, `demo`) schreiben Mails ohne `EMAIL_HOST` in
das Container-Log und schalten nur dafuer `mail.E001` stumm — auf
Produktivhosts ist das nicht vorgesehen.

Vor dem ersten Onboarding die Beispieladresse ersetzen und Djangos
Standard-Testmail aus dem laufenden Web-Container senden:

```bash
docker compose -f docker-compose.prod.yml exec -T web \
  python manage.py sendtestemail pruefadresse@example.org
```

Exit-Code 0 belegt die Uebergabe an das SMTP-Backend, nicht die Zustellung.
Zusaetzlich Eingang, Spam/Quarantaene und im Roh-Header `spf=pass`, `dkim=pass`
sowie `dmarc=pass` pruefen. Bei einer Exception oder einem Exit-Code ungleich 0
die Konfiguration korrigieren und die Web-Logs lesen:

```bash
docker compose -f docker-compose.prod.yml logs web --tail=100
```

Ergaenzend zeigt `/health/` mit authentifizierter Session oder `X-Health-Token`
(Admin-Handbuch [§ 5.1](admin-guide.md#51-health-endpoint)) im Feld `smtp` den
Wert `ok` oder `unreachable` — ein reiner TCP-Verbindungstest zu
`EMAIL_HOST`/`EMAIL_PORT`, kein Authentifizierungs- oder Zustellnachweis;
anonym bleibt das Feld unsichtbar.

In **Verwaltung → Zugaenge** erscheinen bei einem Backend-Fehler die Warnung
„Zugang wurde angelegt, die Einladung konnte aber nicht versendet werden“ oder
„Einladung konnte nicht versendet werden“. Der Zugang kann danach ueber
**Einladung erneut senden** erneut angestossen werden. Eine ausbleibende Mail
trotz Erfolgsmeldung ist beim SMTP-Provider beziehungsweise Empfaenger zu
untersuchen; als technischer Notfall-Fallback kann der Django-Admin ein Konto
ohne E-Mail mit einmalig angezeigtem Initialpasswort anlegen.

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
   - `mfa_failure_burst` (Refs #2033) — `BREACH_MFA_FAILURE_THRESHOLD` (Default 10) fehlgeschlagene Zweitfaktor-Pruefungen pro User im Fenster; `MFA_FAILED` (TOTP) und `WEBAUTHN_FAILED` (Passkey) zaehlen **gemeinsam**, sonst liesse sich die Schwelle durch Wechsel des Verfahrens unterlaufen. Langzeitfenster: `BREACH_MFA_FAILURE_THRESHOLD_LONG` (Default 30). Die Schwelle liegt bewusst unter der von `failed_login_burst`: wer am zweiten Faktor scheitert, hat den ersten bereits — die ernstere Angreifer-Position, und OTP-Fehler sind per Design selten.
3. **Optionaler Webhook** — `BREACH_NOTIFICATION_WEBHOOK_URL` env-Var; bekommt POST mit JSON-Payload (kind, count, threshold, audit_id) bei jedem neuen Finding.
4. **Deduplikation** — innerhalb von 24h wird derselbe Tatbestand (kind + user) nur einmal in `AuditLog` gemeldet.
5. **Die Detection warnt, sie sperrt nicht** — jede Heuristik ist Monitor-only: ein Treffer schreibt eine `SECURITY_VIOLATION`-Zeile und feuert ggf. den Webhook, er entzieht **keinen** Zugang und aendert kein Passwort. Deshalb sind False Positives eingeplant und tolerierbar: Eine Nutzerin mit einer Tippfehler-Serie am Authenticator oder ein Passkey, den der Browser wiederholt ablehnt, kann `mfa_failure_burst` ausloesen (die 10er-Schwelle liegt bewusst niedrig) — die Folge ist ein pruefbarer Eintrag, nie ein Aussperr-Effekt. Das harte Aussperren bleibt dem davon unabhaengigen Account-Lockout (10 Fehl-**logins** in 15 min, `manage.py unlock <username>`) und den Ratelimits vorbehalten. Praktisch heisst das bei der Triage in § „Manueller Prozess": zuerst pruefen, ob die Zeitreihe der Fehlversuche wie ein Mensch aussieht (wenige Minuten, eine Quell-IP, danach Erfolg) oder wie ein Automat (gleichmaessig ueber Stunden, kein Erfolg, ggf. mehrere IPs). Wer die Schwelle dauerhaft anhebt, verliert genau das Signal aus Szenario A7 (geduldiges Raten des zweiten Faktors mit gestohlener Passwort-Session) — Schwellen daher lieber pro Instanz nachjustieren als die Heuristik abschalten.

#### Manueller Prozess bei SECURITY_VIOLATION

1. **T+0** — `AuditLog`-Detail pruefen, betroffene User/Daten identifizieren.
2. **T+24h** — Vorgang an [DPO/Datenschutzbeauftragten](https://github.com/anlaufstelle/app/blob/main/SECURITY.md) eskalieren, Risiko-Einschaetzung erstellen. Faellt die verantwortliche Hauptperson aus, ruht die Eskalation: solange keine zweite Person Maintainer ist, ist keine **Vertretung** benannt ([MAINTAINERS.md](../MAINTAINERS.md), [GOVERNANCE.md § Eskalationspfad](../GOVERNANCE.md)) — ein offen ausgewiesener Bus-Faktor-1-Posten.
3. **T+72h** — Meldung an Aufsichtsbehoerde (Bayern: BayLDA; andere Bundeslaender analog) + ggf. Betroffenenmeldung.
4. **Post-Incident** — Lessons-Learned + Anpassung der Heuristik-Schwellen.

Cron-Eintrag (siehe § 3 Cron-Jobs):

```cron
30 * * * * cd /opt/anlaufstelle && \
    .venv/bin/python src/manage.py detect_breaches \
    --settings=anlaufstelle.settings.prod \
    >> /var/log/anlaufstelle-breach-detection.log 2>&1
```

### 6.5c Security Advisory — Meldung, Bewertung, Veroeffentlichung (Refs #1895)

Gegenstueck zu § 6.5b: dort geht es um eine **Datenschutzverletzung in einer betriebenen Instanz** (DSGVO Art. 33/34), hier um eine **Schwachstelle in der Software selbst**. Die oeffentliche Zusage dazu steht in [SECURITY.md](../SECURITY.md) (Meldewege, SLA, Support-Regel); dieser Abschnitt ist der interne Ablauf dahinter.

**Vorbedingung:** „Private vulnerability reporting" muss auf `anlaufstelle/app` aktiviert sein (Repo → *Settings* → *Advanced Security* → *Private vulnerability reporting* → *Enable*, oder `gh api -X PUT repos/anlaufstelle/app/private-vulnerability-reporting`). Die Einstellung gibt es nur fuer **oeffentliche** Repositories — die nicht-oeffentlichen Repositories der Release-Pipeline scheiden damit aus. Solange es aus ist, laeuft der GHSA-Formular-Link in `SECURITY.md` ins Leere und **nur der E-Mail-Weg traegt** — Statuspruefung:

```bash
gh api repos/anlaufstelle/app/private-vulnerability-reporting # {"enabled": true} erwartet
```

| # | Schritt | Wer | Zielwert |
|---|---------|-----|----------|
| 1 | **Eingang** — Meldung per GHSA-Formular oder E-Mail; Eingang bestaetigen, Reproduktionsschritte nachfordern | Maintainer, bei Ausfall die Vertretung ([MAINTAINERS.md](../MAINTAINERS.md)) | ≤ 5 Werktage |
| 2 | **Bewertung** — reproduzieren, CVSS-v3.1-Basis-Score, Einstufung kritisch/hoch/mittel/niedrig, Ergebnis an die meldende Person | Maintainer/Vertretung | ≤ 10 Werktage |
| 3 | **GHSA-Entwurf** anlegen (*Security* → *Advisories* → *New draft security advisory* auf `anlaufstelle/app`), betroffene + gepatchte Versionen eintragen, Credit nach Wunsch der meldenden Person; bei kritisch/hoch CVE-ID ueber GitHub anfordern | Maintainer/Vertretung | mit Schritt 2 |
| 4 | **Fix** — failing test first, Patch-Release der **aktuellen** Minor-Reihe (Support-Regel in `SECURITY.md`: keine Backports), Commit mit `sec:`-Prefix und Verweis aufs Advisory | Maintainer/Vertretung | kritisch/hoch: 14–30 Tage |
| 5 | **Veroeffentlichung** — Advisory publizieren (Patch + 7 Tage Schonfrist), `Security`-Eintrag im [CHANGELOG](../CHANGELOG.md) mit GHSA-/CVE-Nummer, Hall-of-Fame-Nennung | Maintainer/Vertretung | Patch + 7 Tage |
| 6 | **Kein Fix** — Ablehnung begruenden (Out-of-Scope-Liste in `SECURITY.md`), Vorgang gegenueber der meldenden Person sauber abschliessen | Maintainer/Vertretung | ≤ 10 Werktage |

**Abgrenzung:** Ein GHSA beschreibt eine Schwachstelle **im Anlaufstelle-Code**. Fuer verwundbare Abhaengigkeiten gilt § 8.3 (`pip-audit`/Dependabot) — dort existiert das Upstream-Advisory bereits, ein eigenes GHSA waere Doppelmeldung. Ein eigenes Advisory ist nur faellig, wenn die Anwendung die Upstream-Luecke exponiert und Betreiber deshalb handeln muessen.

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

Lokale Backups in `backups/daily/` ueberleben Container-Recreates, aber nicht den vollstaendigen Verlust des Prod-Hosts (Hardware-Defekt, Ransomware, Provider-Ausfall). `backup.sh` uebertraegt optional die vorhandenen Retention-Stufen in ein Off-Site-Ziel und rotiert lokal erst nach erfolgreichem Abgleich.

```bash
# .env: Off-Site-Ziel konfigurieren (eines von drei Formaten)
BACKUP_OFFSITE_TARGET=rclone:hetzner-bucket:anlaufstelle/backups
# oder
BACKUP_OFFSITE_TARGET=s3://anlaufstelle-backups/prod
# oder
BACKUP_OFFSITE_TARGET=backup-user@offsite.example.com:/backups/anlaufstelle
```

**Empfohlen:** Object-Lock / Write-Once-Policy am Bucket aktivieren — Ransomware kann verschluesselte Backups dann nicht ueberschreiben.

**Was am Ziel landet:** Vor der lokalen Rotation kopieren rclone und S3 den
gefilterten Bestand von `daily/`, `weekly/` und `monthly/` unter die jeweils
gleichnamigen Zielverzeichnisse. Ihre Filter umfassen `*.enc`, `*.hmac`,
`*.manifest` und `audit_hwm-*.json`. scp kopiert alle drei Verzeichnisse
rekursiv und ungefiltert unter `<ziel>/`. rclone nutzt `copy`, S3 `sync` ohne `--delete`; die
lokale Rotation wird daher nicht als Loeschung am Ziel gespiegelt. Aufbewahrung,
Loeschung und Object Lock sind als eigene **Ziel-Policy** einzurichten und zu
kontrollieren.

Credentials muessen dem Konto zur Verfuegung stehen, unter dem `backup.sh`
tatsaechlich laeuft. Fuer scp gehoeren dazu die SSH-Identitaet bzw. der geladene
SSH-Agent und der `known_hosts`-Eintrag dieses Kontos; fuer rclone bzw. S3 die
passende Konfiguration und Credentials. Welches Konto ausfuehrt, legt die
Installation fest.

**Failure-Mode** (Refs #797): Wenn der Off-Site-Sync fehlschlaegt (Netz weg, Credentials ungueltig, Disk voll am Off-Site-Host), loggt `backup.sh` einen `ERROR`. Ein **State-File** `$BACKUP_STATE_DIR/.offsite_state` (Default: neben den Backups) zaehlt aufeinanderfolgende Fehler. Beim **zweiten** Fehler in Folge endet das Skript mit Exit-Code 1 — der Cron-Job wird damit rot. Bereits beim ersten Fehler bleibt die lokale Rotation ausgesetzt, obwohl der Lauf bewusst noch mit Exit-Code 0 endet. Deshalb den **ersten Lauf nach der Einrichtung am Ziel selbst** pruefen: DB, DB-HMAC, Medienarchiv, Medien-HMAC, Manifest, Manifest-HMAC und Audit-HWM des Zeitstempels muessen am variantenrichtigen Pfad liegen. Der Exit-Code allein ist kein Erfolgsnachweis. Erfolgreiche Laeufe setzen den Counter zurueck.

**Sentry-Hook (optional):** Wenn `SENTRY_DSN` UND ein `BACKUP_SENTRY_HOOK`-Skript gesetzt sind, ruft `backup.sh` den Hook bei jedem Off-Site-Fehler auf (Argumente: Beschreibung, Off-Site-Ziel). Der Hook ist freie Operator-Wahl — z.B. `sentry-cli send-event` oder ein eigenes `curl`-Skript. Ohne Hook bleibt es bei Log + Exit-Code.

### 6.6 Backup-Restore-Drill (Refs #720, #739)

Verifiziert, dass das aktuellste Backup vollstaendig wiederherstellbar ist und die Verteidigungslinien (RLS, AuditLog-Immutability-Trigger) erhalten bleiben. Die konkrete quartalsweise Host-Cron-Empfehlung steht unten.

Das Skript deckt seit OPS-05 (Refs #1336) **beide** ausgelieferten Backup-Formate ab. Die Wahl erfolgt per Flag `--dev`/`--prod` oder `$DRILL_FORMAT`; ohne Angabe wird das Format anhand der vorhandenen Backup-Dateien auto-erkannt (Default `dev`, rueckwaerts-kompatibel — der bestehende dev-Timer laeuft unveraendert). Liegen **beide** Formate gleichzeitig vor, bricht der Drill ab und verlangt eine explizite Wahl:

- **`--dev`** — `dev-ops/deploy/backup.sh`, das auf `dev.anlaufstelle.app` tatsaechlich laeuft (Refs #981): `pg_dump --format=custom` → `pg_restore`, AES-256-CBC, Quelle `$DEV_BACKUP_DIR/dump-*.pgc.enc` (Default `/var/backups/anl`), Stack `docker-compose.dev.yml`.
- **`--prod`** — `scripts/ops/backup.sh`, der an Self-Hoster ausgelieferte kanonische Pfad: plain SQL + `gzip`, HMAC-Sidecars, Zeilenzahl-Manifest und zeitgleicher HWM → `psql`, Quelle `$BACKUP_HOST_DIR/daily/anlaufstelle_*.sql.gz.enc` (Default `<Repo>/backups/daily`), Stack `docker-compose.prod.yml`. Manifest und HMAC-Sidecars werden VOR dem Entschluesseln geprueft.

Beide Formate spielen als Postgres-Superuser (`postgres`, via Local-Socket-Trust im db-Container) ein — noetig fuer `CREATE DATABASE`, bypassed zugleich RLS/FORCE-RLS beim Restore.

**Kompatibilitaet Backup-Format × Restore-Pfad** — die beiden Backup-Welten sind *nicht* austauschbar; ein Drill gegen das jeweils fremde Format schlaegt fehl. Der Drill waehlt aber pro Format den passenden Pfad:

| Backup-Quelle | Dump-Format | Verschluesselung | Datei(en) | Restore-Tool | Restore-Drill |
|---|---|---|---|---|---|
| `dev-ops/deploy/backup.sh` (laeuft auf `dev.anlaufstelle.app`) | `pg_dump -Fc` (custom) | AES-256-CBC (kein HMAC) | `dump-*.pgc.enc` | `pg_restore` | ✓ `restore-drill.sh --dev` (Glob `dump-*.pgc.enc`) |
| `scripts/ops/backup.sh` (Prod-Schema) | plain SQL + gzip | AES-256-CBC **+ HMAC-SHA256**-Sidecars (Refs #1024) | DB + Medien samt Sidecars, `*.manifest` samt Sidecar, optional zeitgleicher HWM | `psql` (`scripts/ops/restore.sh`) | ✓ `restore-drill.sh --prod --timestamp <Zeitstempel>` (Manifest-/HMAC-Check vor Decrypt); `backup.sh --verify` bleibt leichtgewichtige Alternative |

Warum kein Cross-Restore: Der `--dev`-Zweig sucht per Glob `dump-*.pgc.enc` und pipet in `pg_restore` — ein plain-SQL-Backup (`*.sql.gz.enc`) wird so gar nicht gefunden und ist auch kein gueltiges `pg_restore`-Archiv. Der `--prod`-Zweig sucht `anlaufstelle_*.sql.gz.enc`, prueft den `.hmac`-Sidecar und spielt via `gunzip | psql` ein — ein `dump-*.pgc.enc` traegt keinen Sidecar (`backup_verify_hmac` bricht ab) und ist Binaerformat. Ein Lauf gegen das jeweils fremde Format schlaegt also fehl. Wer keinen vollen Drill fahren will, nutzt fuer den Prod-Pfad `scripts/ops/backup.sh --verify` (HMAC + Probe-Decrypt + Zeilenabgleich gegen die Live-DB).

**Versionsgrenze:** `restore.sh` akzeptiert aus Kompatibilitaetsgruenden ein
Altbackup ohne Manifest. Der aktuelle Prod-Drill verlangt das Manifest dagegen
zwingend und bricht in Schritt 0 mit `Zeilenzahl-Manifest fehlt` ab. Ein solcher
Altsatz kann den heutigen Vollstaendigkeitsnachweis daher nicht erbringen.

Die Backup-Dateien sind `0600` und gehoeren root — daher **als root** ausfuehren. Ohne Flag erkennt das Skript das Format automatisch; explizit per `--dev`/`--prod` (oder `DRILL_FORMAT=dev|prod`). Der Prod-Zweig liest `BACKUP_HOST_DIR` ausschließlich aus der `.env` im Compose-Verzeichnis; ein gleichnamiger Wert im `sudo env`-Praefix wird beim Laden der Datei ueberschrieben und ist daher kein Override. Liegt die Installation nicht unter dem Default `/opt/anlaufstelle`, muss `COMPOSE_DIR` auf das Verzeichnis mit `.env` und `docker-compose.prod.yml` zeigen:

```bash
sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh # Format auto-erkennen
sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh --prod --timestamp 2026-09-03_020000 # Prod-Satz explizit waehlen
sudo env BACKUP_TIMESTAMP=2026-09-03_020000 bash /opt/anlaufstelle/scripts/ops/restore-drill.sh --prod # Gleichwertige Satzwahl per Umgebungsvariable
sudo env COMPOSE_DIR=/srv/anlaufstelle bash /srv/anlaufstelle/scripts/ops/restore-drill.sh --prod # Installation ausserhalb von /opt/anlaufstelle
sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh --dev # Dev-Format erzwingen (dump-*.pgc.enc)
```

`--timestamp` und `BACKUP_TIMESTAMP` sind nur an das Prod-Format gebunden erlaubt; der Wert muss dem Format `JJJJ-MM-TT_HHMMSS` entsprechen.

| Schritt | Pruefung |
|---|---|
| 0 | Backup im format-spezifischen Verzeichnis finden; bei `--prod --timestamp <Zeitstempel>` oder `--prod` mit `BACKUP_TIMESTAMP=<Zeitstempel>` exakt diesen Satz waehlen, sonst den neuesten. Das zeitgleiche Manifest samt HMAC authentifizieren, Satzkennung sowie DB-/Medien-/HWM-Hashes pruefen und die Referenzzahlen fuer sechs Tabellen laden. Ohne Manifest bricht der Prod-Drill ab. Enthält das authentifizierte Manifest einen `media_sha256`, bricht der Drill bei fehlendem Medienarchiv, fehlendem Sidecar oder falschem Hash bereits vor Anlage der Wegwerf-DB ab; `restore.sh` bricht in denselben Fällen vor dem Import ab. |
| 1 | Frische Wegwerf-DB (`anlaufstelle_drill_<pid>`) anlegen |
| 2 | Backup decrypten + einspielen — `--prod` prueft zuerst den HMAC-Sidecar, dann `gunzip \| psql -v ON_ERROR_STOP=1 --single-transaction` (plain SQL); `--dev` `pg_restore` (custom format) |
| 2b | Nur `--prod` und bei gesetztem `DJANGO_AUDIT_HWM_PATH`: nach Schritt 3b die zum gewaehlten DB-Backup-Zeitstempel gehoerende `audit_hwm-<Zeitstempel>.json` per Stream in den gehaerteten Drill-Container schreiben, die HMAC-Signatur laden und die restaurierte Kette prüfen; fehlend, abweichend oder manipuliert → `FAIL`, die weiteren Restore-Pruefungen laufen trotzdem weiter. Die Bezeichnung 2b bleibt für das Prüfblatt stabil. Ist die Variable nicht gesetzt, wird der Schritt mit `INFO` uebersprungen und der Drill kann mit Exit-Code 0 enden ([§6.7](#67-audit-high-water-mark-sichern-und-pruefen-refs-2022)) |
| 3 | Stichproben pro Manifesttabelle (`core_facility`, `core_client`, `core_event`, `core_workitem`, `core_auditlog`, `core_attachment`) — Counts abfragbar? |
| 3b | Im Prod-Format exakter Zeilenabgleich dieser sechs Tabellen zwischen Wegwerf-DB und authentifiziertem Manifest; jede Abweichung ist `FAIL`. Nur der Dev-Zweig vergleicht weiterhin exakt gegen die Live-DB. Dieser Schritt läuft vor der schreibenden Audit-Kettenprüfung und dem Compliance-Marker. |
| 4 | RLS-Policy-Check — `SELECT COUNT(*) FROM pg_class WHERE relname LIKE 'core_%' AND relrowsecurity = true` muss `>= 18` ergeben (konservativer Mindestwert im Skript; tatsaechliche Anzahl liegt hoeher, siehe Abschnitt 9) |
| 5 | `auditlog_immutable`-Trigger existiert UND blockt Raw UPDATE. Sind Audit-Soll im Manifest und Ist in `core_auditlog` beide 0, ist die Prüfung `NICHT PRUEFBAR`: Das belegt weder einen defekten Trigger noch dessen Funktion. Der Drill bleibt `OFFEN`, endet mit Exit-Code 1 und führt Schritt 7 nicht aus. |
| 6 | Nur `--prod`: Zum gewählten DB-Backup gehörendes Medienarchiv per HMAC prüfen, in einem Durchlauf in ein Host-Tempverzeichnis probeentpacken und das dabei erzeugte Tar-Listing mit der Probeentpackung vergleichen; Ausgabe `Medien: N Dateien, M Bytes, HMAC ok`. Vorher müssen dort ungefähr Archivgröße × 2 frei sein; `TMPDIR=/pfad/mit/platz` lenkt den Schritt auf ein anderes Dateisystem. Ein fehlendes passendes Archiv ist nur dann ein zulässiger DB-only-Fall mit `INFO`, wenn das authentifizierte Manifest keinen Medienhash enthält. Tempverzeichnis und Wegwerf-DB werden via Trap aufgeräumt. |
| 7 | **Bei vollem Erfolg (alle Schritte inkl. 3b und 6):** `mark_restore_verified` im web-Container → Compliance-Marker |

Output: ein `OK`, `FAIL` oder `NICHT PRUEFBAR` pro ausgeführtem Schritt; optionale übersprungene Prüfungen melden `INFO`. Exit-Code != 0 bei jedem `FAIL` und bei `NICHT PRUEFBAR`. Bei `FAIL` sofort auf Backup-Integritaet pruefen — Trigger-Check fehlgeschlagen (Schritt 5) ist kritisch, weil die AuditLog-Immutability dann nach Restore nicht mehr greift.

> **Benigne Ausnahme beim Restore (Schritt 2):** Das Dump enthält ein `REFRESH MATERIALIZED VIEW core_statistics_event_flat`, das beim Dev-Restore an `FORCE ROW LEVEL SECURITY` auf `core_event` scheitern kann (`pg_restore: errors ignored on restore: 1`). Das betrifft nur den Statistik-Cache. Der `--dev`-Drill toleriert ausschließlich diesen Fall. Im Prod-Zweig beendet `psql -v ON_ERROR_STOP=1 --single-transaction` jeden SQL-Fehler. Vollständigkeit belegt dort zusätzlich der exakte Manifestabgleich in Schritt 3b.

**Compliance-Marker (Refs #919):** Schritt 7 schreibt bei Erfolg automatisch einen `RESTORE_VERIFIED`-AuditLog-Eintrag (`manage.py mark_restore_verified`), den das Compliance-Dashboard (`/system/compliance/`) als Alter-Indikator nutzt:

- ≤ 90 Tage → `ok`
- ≤ 180 Tage → `warning`
- älter → `critical` (DSGVO Art. 32 Abs. 1 lit. c verletzt — Wiederherstellbarkeit nicht mehr nachgewiesen).

Ohne je gelaufenen Drill bleibt das Dashboard auf `unknown`. Manuell nachziehen (z.B. nach einem dokumentierten Out-of-Band-Restore) geht weiterhin:

```bash
docker compose -f docker-compose.dev.yml --env-file .env.dev exec web \
    python manage.py mark_restore_verified --note "Out-of-Band-Restore $(date +%Y-%m-%d)"
```

**Host-Cron-Vorschlag (keine automatische Installation):** Die Backup-Dateien
sind `0600` und gehoeren root; den Einzeiler deshalb in der **Root-Crontab des
Hosts** eintragen, nicht im `cron`-Sidecar und nicht in einer unprivilegierten
Benutzer-Crontab. Er laeuft am ersten Tag jedes Quartals um 03:30 Uhr nach dem
02:00-Uhr-Backup. Das Flag `--prod` bleibt gesetzt: ohne Angabe erkennt das
Skript das Format automatisch und fiele bei fehlendem Prod-Backup still auf
`--dev` zurueck. `ops@example.com` ersetzen und vorab sicherstellen, dass der
Host-Befehl `mail` an das Betriebs-Postfach zustellen kann.

```cron
30 3 1 1,4,7,10 * cd /opt/anlaufstelle && bash scripts/ops/restore-drill.sh --prod >> /var/log/anlaufstelle-restore-drill.log 2>&1 || mail -s "Restore-Drill FAIL" ops@example.com < /var/log/anlaufstelle-restore-drill.log
```

#### Manueller Sentinel-Drill fuer Medien-Volume-Persistenz

Der automatische Schritt 6 prüft die Integrität und Probeentpackbarkeit des Medien-**Backups**. Dieser separate Sentinel-Drill prüft ausschließlich, ob das laufende Medien-**Volume** einen Container-Recreate übersteht; er ersetzt den Backup-Test nicht.

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

### 6.7 Audit-High-Water-Mark sichern und pruefen (Refs #2022)

Die Audit-Kettenpruefung kann Tail-Truncation und eine komplett geleerte Kette nur erkennen, wenn ihr letzter gruener Stand ausserhalb der Datenbank verankert bleibt. Dafuer in `.env` den empfohlenen Pfad im persistenten `/data`-Volume setzen:

```dotenv
DJANGO_AUDIT_HWM_PATH=/data/audit_hwm.json
```

Der Pfad liegt bewusst neben `MEDIA_ROOT=/data/media`: Er ist damit fuer `cron`/`maintenance` unter dem schreibgeschuetzten Container-Dateisystem persistent beschreibbar, wird aber nicht mit Nutzer-Uploads vermischt. Der `web`-Dienst liest die Datei nicht und braucht die Variable nicht in seiner expliziten Env-Allowlist.

Den ersten Anker sofort nach der Einrichtung und **vor dem ersten Backup**
erzeugen; nicht bis zum naechsten Sidecar-Termin warten:

```bash
docker compose -f docker-compose.prod.yml run --rm -T maintenance verify_audit_chain
```

Nur ein vollstaendig gruener Lauf schreibt den Anker. Bei einem `FAIL` zuerst die
Audit-Kette klaeren und kein Backup als vollstaendig verankert behandeln.

Ein gruener `verify_audit_chain`-Lauf schreibt dort Zaehler und letzten Hash je Kette. Die Datei ist mit `DJANGO_AUDIT_HASH_KEY` HMAC-signiert und enthaelt keine Klartext-Falldaten; eine zusaetzliche Verschluesselung ist deshalb nicht noetig. `scripts/ops/backup.sh` exportiert sie per `docker compose -f docker-compose.prod.yml exec -T cron cat …` als `backups/daily/audit_hwm-<Zeitstempel>.json`, rotiert sie mit DB-/Medienbackups und nimmt sie in alle drei Off-Site-Wege (rclone, S3, SCP) auf. Ist der Pfad nicht gesetzt oder die Datei noch nicht vorhanden, bleibt das DB-/Medienbackup gueltig, aber das Skript protokolliert den fehlenden HWM-Anker.

Ist `DJANGO_AUDIT_HWM_PATH` gesetzt, streamt `scripts/ops/restore-drill.sh --prod` die zum gewaehlten DB-Backup-Zeitstempel gehoerende HWM-Datei nach `/tmp` des gehaerteten Drill-Containers und laedt sie ueber `HighWaterMarkStore.load()`. Eine fehlende, zeitlich abweichende oder ungueltig signierte Datei erzeugt `FAIL` und damit Exit-Code 1; der Drill fuehrt die uebrigen Restore-, RLS- und Trigger-Pruefungen dennoch aus, damit ein zweiter Fehler nicht verdeckt wird. Ist die Variable nicht gesetzt, ueberspringt der Drill Schritt 2b mit einer `INFO`-Zeile; das allein laesst Exit-Code 0 und den Restore-Marker weiterhin zu.

Beim echten Recovery gilt eine strengere Reihenfolge: `cron` erfolgreich
stoppen und die HWM-Quelle auf Existenz pruefen. `restore.sh` prüft Signatur und
Struktur der Quelle im `maintenance`-Kontext, schreibt sie in eine temporäre
Datei im Zielverzeichnis, vergleicht Größe und SHA-256 mit der Quelle und
aktiviert sie per `mv -f`. Der zusätzliche Nachimport aus dem
Disaster-Recovery-Runbook verwendet dieselbe Tempdatei-, Vergleichs- und
Tauschfolge. Jeder Fehler bricht ab; erst danach darf
`verify_audit_chain` laufen und **erst nach dessen Erfolg** duerfen Jobs starten.
Ein fehlender Betriebsanker gilt der Software als Erstlauf, weshalb diese
Pruefung vor der Kettenpruefung zwingend ist. Der erste gruene Lauf schreibt die
Betriebsdatei fort; das Off-Site-Original deshalb separat und unveraendert aufbewahren. Die ausfuehrbare
Schrittfolge steht in [disaster-recovery.md § 6 Schritt 4b](disaster-recovery.md#schritt-4b--audit-hwm-zurückspielen-und-kette-prüfen).

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
liest den JSON-`status` und meldet bei `degraded` ungesund. Der Produktions-Compose
ersetzt diesen Image-Check: Nur `error` beziehungsweise HTTP 503 macht `web`
ungesund, damit weder `caddy` (`service_healthy`) noch `initial-setup.sh` an einer
nicht-kritischen Störung scheitern. `degraded` bleibt HTTP 200 und muss über den
Token-Detailpfad, das Compliance-Dashboard und externes Monitoring überwacht werden
(siehe [Monitoring-Guide](monitoring-guide.md)).

### 7.4 Datenbank-Updates (Signaturen)

Das benannte Volume `clamav-db` persistiert den vorhandenen Signaturstand. Im
ausgelieferten Prod-Stack haengt `clamav` jedoch ausschliesslich am isolierten
internen Netz und erreicht die Update-Server nicht; ein automatischer
`freshclam`-Erfolg ist daher **nicht** gegeben. Eine im Dashboard als veraltet
gemeldete Signatur ist kein voruebergehender Anlaufzustand. Logs und Stand
pruefen:

```bash
docker compose -f docker-compose.prod.yml exec clamav tail -n 30 /var/log/clamav/freshclam.log
docker compose -f docker-compose.prod.yml exec clamav clamdscan --version
```

Ist das Signaturalter waehrend des Erstlaufs noch nicht ermittelbar, bleibt der
Compliance-Status `unknown` statt eine Warnung auszuloesen. Der Check zeigt den
ersten Befehl oben als Handlungshinweis. Bleibt `unknown` nach dem Anlauf
bestehen, den Befehl ausfuehren und das Protokoll auf DNS-, Download- oder
Mirror-Fehler pruefen.

Bis ein ausgehender Update-Pfad oder ein interner Signatur-Mirror eingerichtet
ist, bleibt der Signaturstand eingefroren und der Betriebszustand offen.

### 7.5 Manueller Check

```bash
# Daemon erreichbar?
docker compose -f docker-compose.prod.yml exec web \
  python manage.py shell -c "from core.services.file_vault.virus_scan import ping; print(ping)"

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

**Dauerhaftes Release-Artefakt (Refs #1895):** Das CI-Artefakt verfaellt nach 90 Tagen — fuer ein aelteres Release war danach nicht mehr belegbar, welche Komponenten drinstecken. Genau dieser Nachweis soll dauerhaft moeglich bleiben. Der Release-Build erzeugt deshalb zusaetzlich eine **dauerhafte** SBOM `sbom-vX.Y.Z.json` (CycloneDX 1.6) aus den Lock-Files des gebauten Trees — offline, ohne Netzabruf und byte-identisch bei Wiederholung. Umfang: alle Python-Distributionen aus `requirements.txt` (direkt **und** transitiv) plus alle npm-Pakete aus `package-lock.json`. Die vier vendorten Frontend-Libs (htmx, Alpine-CSP, Dexie, Chart.js) sind darin als `required` gefuehrt, obwohl sie im Lockfile als `dev` stehen — sie werden ausgeliefert (`src/static/js/`) und laufen im Browser der Nutzerinnen. Build-/Testwerkzeuge bleiben `optional`. Bis `1.0` verbleibt das Artefakt beim Maintainer (es gibt bis dahin keine GitHub-Release-Page) und ist ueber den Kontakt in [SECURITY.md](../SECURITY.md) erhaeltlich; ab `1.0` haengt es am GitHub-Release.

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

### 8.4a Image-Signatur, Provenance und Digest-Pinning

Der Release-Push hängt an das Image-Manifest eine SBOM-Attestation und eine
Provenance-Attestation (`mode=max`) und signiert den Image-Digest mit Cosign
keyless. Die Signatur wird im Sigstore-Transparenzlog protokolliert; langlebige
Signaturschlüssel sind nicht erforderlich.

Das Projekt erzeugt damit drei unterschiedliche, einander ergänzende SBOMs:

| SBOM | Umfang und Aufbewahrung |
|------|-------------------------|
| CI-Artefakt aus dem `audit`-Job | Python-Abhängigkeiten; 90 Tage |
| Dauerhafte Release-SBOM `sbom-vX.Y.Z.json` | Python- und npm-Abhängigkeiten aus den Lock-Files; dauerhafter Nachweis der Abhängigkeiten je Release |
| Image-SBOM als Attestation | Zusätzlich Basis-Image-Layer und Systempakete am Image-Manifest |

Die Image-SBOM ersetzt die dauerhafte Release-SBOM nicht. Der
Multi-Platform-Push mit `docker/build-push-action@v7` erzeugte über den
Buildx-Container-Treiber bereits Mindest-Provenance (`mode=min`); der
Release-Workflow hebt sie auf `mode=max` an und ergänzt die SBOM-Attestation.

Betreiber können Digest, Keyless-Signatur und Attestationsdaten ohne Zugriff
auf das Quell-Repository prüfen:

```bash
# Digest des Tags auflösen
docker buildx imagetools inspect ghcr.io/anlaufstelle/app:vX.Y.Z --format '{{ .Manifest.Digest }}'
# Signatur prüfen (keyless)
cosign verify ghcr.io/anlaufstelle/app@sha256:… \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github.com/anlaufstelle/app/\.github/workflows/release\.yml@refs/tags/v'
# Attestations (SBOM/Provenance) am Manifest
docker buildx imagetools inspect ghcr.io/anlaufstelle/app:vX.Y.Z --format '{{ json .SBOM }}'
```

`APP_VERSION=vX.Y.Z` bleibt der lesbare Release-Tag. Mit
`APP_IMAGE_DIGEST=@sha256:…` bindet `docker-compose.prod.yml` das App-Image
zusätzlich unveränderlich an den geprüften Digest. PostgreSQL, Redis, ClamAV
und Caddy sind dort bereits per `@sha256` gepinnt; das App-Image war die einzige
Lücke. Bei jedem Versionswechsel müssen `APP_VERSION` und
`APP_IMAGE_DIGEST` gemeinsam aktualisiert werden, sonst startet der Stack trotz
neuem Tag weiterhin den alten Digest.

Ist das GHCR-Paket privat, benötigt der Demo-Host eine Anmeldung mit
`docker login ghcr.io`. Docker speichert diese Zugangsdaten dauerhaft in
`~/.docker/config.json` des Demo-Host-Benutzers; Dateirechte und Token-Scope
müssen entsprechend restriktiv sein.

### 8.5 Troubleshooting

| Problem | Ursache | Lösung |
|---------|---------|--------|
| `deps-check` schlägt fehl, obwohl nichts geändert wurde | Unterschiedliche pip-tools-Version zwischen Dev und CI | `pip install --upgrade pip-tools` und `make deps-lock` neu ausführen |
| `pip-compile` findet keine passende Version | Zu strenge Range-Constraint | Range in `.in` lockern, z.B. `>=X,<Y+1` |
| Transitive Dependency zieht vulnerable Version | Direkte Dependency fixiert alte Version | Direkte Dependency selbst einpflegen und anheben |

---

## 9. Row Level Security (RLS)

PostgreSQL-RLS ist auf allen facility-gescopten Tabellen (derzeit
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
AuditLog und Activity. Damit ist der RLS-Schutz funktional
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
Deployment-Dokumente verweisen hierher, statt sie zu wiederholen. Sie gilt fuer
**alle** Deployment-Stacks (prod, dev, demo und — seit
#2086 — auch staging);
den Ist-Abgleich je Stack fuehrt [§ 1.4](#14-compose-stack-abgleich). Quelle der
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
| `POSTGRES_ADMIN_USER` (Admin/Wartung/**Owner**) | `anlaufstelle_admin` | `NOSUPERUSER BYPASSRLS NOCREATEROLE NOCREATEDB`, **DB-/Objekt-Owner** | `migrate`, `seed`, Retention-/Anonymisierungs-Cron, Ersteinrichtung (`setup_facility`) und Paket-Import (`load_package`). Als Owner umgeht die Wartung die Append-Only-Trigger per DDL (A1.4, § 9.5). |

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

Operator-/Wartungs-Tasks laufen ausschliesslich im opt-in Service
`maintenance`; nur er laedt die vollstaendige `.env` und ueberschreibt dort
`POSTGRES_USER`/`POSTGRES_PASSWORD` mit der Admin-Rolle. `web` erhaelt dagegen
eine explizite Runtime-Allowlist und sieht weder Admin- noch Bootstrap-Secret
(Refs #1962). Der
Migrate-Job [`docker-migrate.sh`](../docker-migrate.sh) laeuft in diesem Service. Das
Rollen-Gate `check_db_roles` prueft die stabile App-Rolle ueber
`POSTGRES_APP_USER` (Refs #1017), damit der ENV-Override es nicht verwirrt.

**Pflicht-`.env`-Variablen** (Vorlage [`.env.example`](../.env.example)):
`POSTGRES_BOOTSTRAP_PASSWORD`, `POSTGRES_ADMIN_USER`, `POSTGRES_ADMIN_PASSWORD`
(zusaetzlich zu den App-Variablen). Der Pre-Deploy-Check in § 1.1 verifiziert,
dass sie gesetzt und nicht mehr auf `change-me-*` stehen.

**Namensvertrag — zwei Variablennamen fuer dieselbe Rolle.** Die App-Rolle wird
unter **zwei** Namen adressiert, und beide muessen aus `${POSTGRES_USER}`
gespeist werden:

| Variable | Wo gesetzt | Wer liest sie |
|---|---|---|
| `APP_DB_USER` | `environment:` des **`db`**-Service | [`deploy/postgres-init/01-app-role.sh`](../deploy/postgres-init/01-app-role.sh) — legt die Rolle unter diesem Namen **an** |
| `POSTGRES_APP_USER` | `environment:` von **`web`**, **`maintenance`**, **`cron`** | `check_db_roles` bzw. `src/core/services/compliance/db_roles.py` — **prueft** unter diesem Namen; derselbe Name ist Adressat konditionaler `GRANT`s |

Fehlt der zweite Name, faellt die Pruefung bzw. der `GRANT` still aus (kein
Fehler, kein Log) — der Defekt zeigt sich erst Monate spaeter im Betrieb.

**Bestandscluster: der Compose-Fix wirkt nicht rueckwirkend.** Das Init-Script
ist **INIT-ONLY** und laeuft ausschliesslich beim Anlegen eines leeren `PGDATA`.
Auf einem bereits existierenden Volume (z.B. `*_pgdata_staging`) legt es weder
neue Rollen an noch gleicht es bestehende an die Env an. Zwei Wege:

1. **Frisches Volume** — Dump/Restore nach
   [§ 13.1](#131-prodstaging--daten-erhalten-wartungsfenster). Der einfachere,
   bereits durchgespielte Weg; das Init-Script baut die Zielrollen dabei selbst auf.
2. **In-Place-Umbau** nach
   [§ 9.6](#96-rollout-der-runtime-owner-rollentrennung-auf-bestandsinstallationen-a14).
   **Achtung — ein reiner Ownership-Umzug reicht dafuer nicht:** auf einem Cluster,
   der ohne Init-Script hochkam, existiert gar keine Admin-Rolle, und die App-Rolle
   ist dort die **Bootstrap-Rolle**, die sich nicht entrechten laesst
   (`The bootstrap superuser must have the SUPERUSER attribute`). § 9.6
   diagnostiziert die Ausgangslage und beschreibt je Fall den vollstaendigen Weg
   inklusive Rollen-Neuanlage; die Passwortrotation allein
   (`scripts/ops/rotate-db-passwords.sh`, § „DB-Passwortrotation" unten) legt
   **keine** Rollen an.

**Bis einer dieser beiden Wege gefahren ist, bleibt die alte Superuser-Situation
auf einem laufenden Staging-Server live** — der Repo-Fix allein aendert daran
nichts. Der `web`-Healthcheck macht es allerdings sichtbar: der Container wird
dort nie `healthy`, und `caddy` gibt die Runtime nicht frei.

**Verifikation** nach dem ersten Hochfahren — das Gate prueft beide Rollen:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py check_db_roles
# erwartet: App-Rolle rolsuper=False, rolbypassrls=False
# Admin-Rolle rolsuper=False, rolbypassrls=True (Exit-Code 0)
```

Auf dem Staging-Stack ueber den `maintenance`-One-Shot (das Profil ist opt-in,
`--env-file` ist dort Pflicht):

```bash
docker compose -f docker-compose.staging.yml --env-file .env.staging \
    run --rm maintenance check_db_roles
```

### DB-Passwortrotation (alle drei Rollen)

Refs #1964. Eine Aenderung
der `.env` allein rotiert **kein** Passwort in einem bestehenden PostgreSQL-
Cluster: [`deploy/postgres-init/01-app-role.sh`](../deploy/postgres-init/01-app-role.sh)
laeuft nur beim ersten Initialisieren eines leeren Volumes und legt fehlende
Rollen an. Es bleibt bewusst init-only; ein `ALTER ROLE`-Zweig dort waere ein
halb wirksames Versprechen, weil er bei jedem Bestandsvolume uebersprungen wird.

Der kanonische, getestete Weg ist:

```bash
cd /opt/anlaufstelle./scripts/ops/rotate-db-passwords.sh

# dev/demo verwenden ihre eigene Compose-/Env-Datei:
COMPOSE_FILE=docker-compose.dev.yml ENV_FILE=.env.dev \./scripts/ops/rotate-db-passwords.sh
```

**Voraussetzungen:** Wartungsfenster ankündigen; sicherstellen, dass gerade kein
Backup-/Restore-Job laeuft; einen aktuellen, verifizierten Sicherungsstand haben;
`.env` muss mode `600` (oder strenger) besitzen. Das Skript braucht Docker-
Zugriff und `openssl`, gibt aber **kein** Passwort auf stdout/stderr oder in
Prozessargumenten aus. Es erzeugt drei voneinander unabhaengige Zufallswerte und
setzt intern als Bootstrap-Superuser eine Transaktion nach diesem Muster ab:

Die Rotation ist **kein Deployment**: alle `compose run`-/`up`-Schritte nutzen
`--pull never` und damit exakt die bereits verifizierten lokalen Images. So kann
weder ein bewegtes Tag den Code mitten im Wartungsfenster wechseln noch ein
Registry-Ausfall Rotation oder Rollback blockieren. Ein neues Image immer vor
dem Wartungsfenster separat deployen und pruefen.

```sql
BEGIN;
ALTER ROLE "anlaufstelle" PASSWORD '<neues App-Passwort>';
ALTER ROLE "anlaufstelle_admin" PASSWORD '<neues Admin-Passwort>';
ALTER ROLE "postgres" PASSWORD '<neues Bootstrap-Passwort>';
COMMIT;
```

Die Platzhalter sind **kein** Kopierbefehl. Passwoerter nie in Shell-History,
Ticket oder Runbook-Protokoll schreiben; das Skript uebergibt SQL und Login-
Geheimnisse nur ueber stdin. Ein manueller Notfalllauf muss dieselbe Reihenfolge
und eine einzige Transaktion verwenden.

#### Reihenfolge und vollstaendige Consumer-Liste

| Schritt/Consumer | Rolle | Was die Rotation tut |
|---|---|---|
| `web` | App (`POSTGRES_PASSWORD`) | Vor der DB-Aenderung stoppen; nach Env-Tausch mit neuem Passwort starten und Health abwarten. |
| `cron` | Admin (`POSTGRES_ADMIN_PASSWORD`) | Zusammen mit `web` stoppen/starten, damit kein Retention-/Audit-Job mit altem Login neu verbindet. In Dev/Demo ohne `cron` entfaellt nur dieser Schritt. |
| `maintenance` / [`docker-migrate.sh`](../docker-migrate.sh) | Admin | Nach DB-Recreate als kurzlebiger One-Shot mit der neuen Env ausfuehren. Das prueft `check_db_roles`, Migrationen und die Ownership-Normalisierung aus #1085. |
| `db` | Bootstrap (`POSTGRES_BOOTSTRAP_PASSWORD`) | Alle drei Rollen in einer Transaktion aendern; danach den Container mit unveraendertem Datenvolume **recreaten**, damit auch seine Container-Env nicht das alte Bootstrap-Secret behaelt. |
| [`scripts/ops/backup.sh`](../scripts/ops/backup.sh) | Admin | Kein Daemon-Neustart: das Host-Skript liest `.env` bei jedem neuen Lauf. Deshalb waehrend der Rotation nicht parallel starten. |
| Restore-Strecke (`restore.sh`, `restore-drill.sh`, [Disaster-Recovery](disaster-recovery.md)) | Bootstrap/Admin | Kein dauerhafter Consumer; jeder spaetere Lauf liest die neue `.env`. Eskrow/Notfallunterlagen muessen unmittelbar nachgezogen werden. |

Das Skript schreibt zuerst eine mode-identische Env-Datei im selben Verzeichnis,
stoppt dann die dauerhaften Consumer, rotiert die DB, testet alle **neuen**
Logins, tauscht `.env` atomar, recreated `db`, faehrt den echten Migrate-Pfad
und startet `web`/`cron`. Erst danach folgen drei Negativtests ueber den
Compose-Netzwerkhost `db` und damit PostgreSQLs SCRAM-Passwortauthentifizierung.
Der Loopback-Pfad im DB-Container ist dafuer unzulaessig, weil offizielle
PostgreSQL-Volumes dort eine `trust`-Regel besitzen koennen. Erwartete
Schlusszeilen (Rollennamen duerfen abweichen, Geheimnisse erscheinen nie):

```text
[db-password-rotation] Negativtest gruen: alter Login fuer Rolle anlaufstelle wird mit Auth-Fehler abgewiesen.
[db-password-rotation] Negativtest gruen: alter Login fuer Rolle anlaufstelle_admin wird mit Auth-Fehler abgewiesen.
[db-password-rotation] Negativtest gruen: alter Login fuer Rolle postgres wird mit Auth-Fehler abgewiesen.
[db-password-rotation] Rotation abgeschlossen. Backup-/Restore-Jobs lesen beim naechsten Lauf die neue Env-Datei.
```

Ein blosses „Verbindung fehlgeschlagen" zaehlt nicht: Der Negativtest akzeptiert
nur PostgreSQLs `password authentication failed`; zuvor wurden dieselben drei
Rollen mit den neuen Passwoertern erfolgreich getestet. Schlaegt irgendein
Schritt fehl, setzt der Exit-Trap DB-Passwoerter und `.env` auf die alten Werte
zurueck, recreated `db` bei Bedarf und startet die vorher gestoppten Consumer.
Eine Warnung **„Rollback war unvollstaendig"** ist ein harter Betriebsalarm:
keinen weiteren Neustart ausloesen, bis DB und `.env` manuell abgeglichen sind.

**Bootstrap-Entscheidung:** `postgres` bleibt `LOGIN`, wird aber mitrotiert und
bleibt ausserhalb von `web`, `cron` und normalen Operator-Jobs. `NOLOGIN` wird
bewusst **nicht** gesetzt: dieselbe Rolle ist der einzige kontrollierte Pfad fuer
eine spaetere Rotation und die fabrikfrische Restore-Strecke. Ohne einen zweiten
Superuser liesse sie sich nach `NOLOGIN` nicht aus der Anwendung heraus sicher
reaktivieren; das verschöbe statt verringerte das Notfallrisiko.

**Unmittelbarer Abschluss:** Die drei neuen DB-Passwoerter im getrennten
Passwort-Eskrow und in der DR-Kontaktkarte ersetzen, alten Stand als widerrufen
markieren und erst dann das Wartungsfenster schliessen. Ein alter Eskrow-Stand
laesst Backup-/Restore- und Notfallbefehle am vorhandenen Cluster genau dann
scheitern, wenn die lokale `.env` nicht mehr erreichbar ist.

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
(`POSTGRES_ADMIN_USER`) laufen — das ist ueber den dedizierten
`maintenance`-Service bereits so eingerichtet (Retention-Cron § 6). Läuft ein solcher
Command versehentlich als App-Rolle, bricht der Bypass mit `must be owner of
table core_eventhistory` ab (seit der Reihenfolge-Umkehr in
#2065 steht
`core_eventhistory` in `_APPEND_ONLY_TRIGGERS` vorn) — Fail-loud, kein stiller
Audit-Verlust.

**Die Art.-17-Sofortaktion ist kein Wartungspfad** (Refs
#2085). Die
Papierkorb-Aktion „Jetzt endgültig anonymisieren" läuft **interaktiv im
Webprozess** und damit unter der App-Rolle — dort gibt es keinen
Owner-DDL-Weg und kein Wartungsfenster. Bis 0.90 lief sie ungebremst in den
Bypass: das erste `ALTER TABLE … DISABLE TRIGGER` scheiterte, die
`@transaction.atomic`-Klammer riss die **gesamte** Anonymisierung samt
Nachweiseintrag zurück, und die Nutzerin sah einen **HTTP 500** — die Person
blieb unverändert stehen, das Löschverlangen unerfüllt. Auf Entwicklungs- und
Testsystemen fiel das nie auf, weil dort das Einzelrollen-Modell läuft und
dieselbe Verbindung Owner ist.

Seit #2085 ist der Pfad **zweigeteilt**, und die App-Rolle bekommt **kein**
neues Recht:

| Was | Wann | Wer |
|---|---|---|
| Pseudonym, Notizen, Zusatzfelder, Fall-, Episoden- und Live-Ereignisinhalte, Zeitstempel-Generalisierung, Anhänge | **sofort im Request** | App-Rolle (gewöhnliches DML) |
| **Frühere Fassungen der Ereignis-Dokumentation** (`core_eventhistory`) | **mit dem nächsten `enforce_retention`-Lauf** (Cron, 03:00) | Admin-/Owner-Rolle im `maintenance`-Service |

Der Nachlauf ist die vierte Kandidatenmenge des EventHistory-Sweeps aus
[ADR-050](adr/050-eventhistory-retention.md) (Nachweiskategorie
`eventhistory_anonymized_client`). Er findet die Zeilen am Stempel
`Client.anonymized_at`, den die Anonymisierung selbst setzt — es gibt also
keinen zweiten Zustand, der mit der Wirklichkeit driften könnte. Er erbt damit
alles, was der Sweep ohnehin leistet: **hold-aware** (ein nach der
Anonymisierung eintreffender Legal Hold stellt die Historie zurück),
**idempotent** (`redacted_at IS NULL` als Bedingung), gebatcht und mit
Nachweiszeile je Charge.

> **Betrieblich relevant:** Zwischen der Aktion und dem nächtlichen Lauf stehen
> die **früheren Fassungen** der Ereignis-Dokumentation dieser Person noch mit
> ihren Werten in der Datenbank — höchstens bis zum nächsten Lauf, in der
> Voreinstellung also bis 03:00 Uhr. Oberfläche und Handbuch sagen das
> ausdrücklich. Wer eine Art.-17-Tilgung sofort vollständig abschließen muss,
> fährt danach einmal von Hand:
>
> ```bash
> docker compose -f docker-compose.prod.yml run --rm maintenance enforce_retention
> ```
>
> Eine `SECURITY DEFINER`-Funktion, die der App-Rolle den Bypass für genau
> diesen Schritt öffnet, wurde in #2085 gebaut und wieder **verworfen**: Der
> Funktionsrumpf musste die Einrichtung aus `app.current_facility_id` lesen —
> eine Custom-GUC, die genau die aufrufende Rolle selbst setzen kann —, und er
> kannte den Spoliationsschutz aus `anonymize_client_now` nicht. Beide Wege
> wurden gegen PostgreSQL reproduziert. [ADR-033](adr/033-runtime-owner-role-separation.md)
> und ADR-050 M-8 bleiben damit unverändert in Kraft.

Dieselbe Rollen-Voraussetzung gilt fuer **RunPython-Backfills auf RLS-Tabellen**:
sie laufen unter der Migrationsrolle und werden seit
#1746 von
`assert_migration_role_bypasses_rls(schema_editor)` als erster Zeile abgesichert.
Der `facility_id`-Backfill der Attachment-Migration `0130`
(#1738) nutzt diesen
Guard — laeuft `migrate` versehentlich als App-Rolle, bricht er fail-loud ab,
statt still **0 Zeilen** zu backfillen und die Migration als erfolgreich zu
melden. `docker-migrate.sh` laeuft deshalb ausschliesslich im Maintenance-Service.

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

**Seit 0.89 ist der naechtliche Retention-Lauf ein REGELMAESSIGER Bypass-Aufrufer**
(ADR-050, Refs #2065):
der EventHistory-Sweep redigiert abgelaufene Historien-Fassungen und loescht
laengst redigierte. Drei Eigenschaften begrenzen die Wirkung auf das
Wartungsfenster:

- **Gebatcht.** Ein Bypass-Block je 500 Zeilen, nicht einer je Zeile wie in der
  Key-Rotation — die Zahl der `ShareRowExclusiveLock`-Intervalle sinkt um
  Groessenordnungen.
- **Nur `core_eventhistory`.** Der Sweep oeffnet ausschliesslich die beiden
  dortigen Trigger; auf `core_auditlog` nimmt er gar keine DDL-Sperre mehr,
  sondern nur das `RowExclusiveLock` seines eigenen Nachweis-`INSERT`s.
  Audit-`INSERT`s laufender Requests blockieren dadurch **nicht**.
- **Kanonische Sperr-Reihenfolge** `core_client` → `core_event` →
  `core_eventhistory` → `core_auditlog`, also genau die Ordnung, in der die
  Anwendung ohnehin schreibt. `_APPEND_ONLY_TRIGGERS` fuehrte bis 0.89
  `core_auditlog` zuerst — ein ABBA-Zyklus gegen jeden mutierenden Request.
  Personen- und Ereigniszeilen einer Charge werden per
  `SELECT … FOR UPDATE SKIP LOCKED` gesperrt, **bevor** der Trigger-Bypass
  oeffnet: sonst hielte der Lauf die Tabellensperre und wartete auf eine fremde
  Zeilensperre. Dieselben Zeilen sperrt die Legal-Hold-Anlage, bevor sie
  schreibt — daher haelt ein Hold auf **Ereignis oder Person** den Sweep
  zuverlaessig auf und wird nicht von ihm ueberholt. Bricht eine Charge dennoch mit
  `deadlock detected` ab, wird sie idempotent mit Backoff wiederholt; der Lauf
  faellt nicht aus.

Betriebsregeln daraus:

- Die Bypass-Commands `enforce_retention` (Retention-Cron, § 6),
  `backfill_audit_chain` und `reencrypt_fields` in **Wartungs-/Nebenzeiten** mit
  geringer Schreiblast fahren — nicht zur Stoßzeit. Für die **interaktive**
  Art.-17-Aktion gilt das nicht: sie nimmt im Webprozess gar keine DDL-Sperre
  mehr (s. o.), ihre Historien-Redaktion läuft im nächtlichen Lauf mit.
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
Admin wird Owner, App bekommt nur DML). Eine **bereits laufende Installation mit
fehlerhafter Rollenkonfiguration** (Ownership noch bei der App-Rolle oder keine
separate Admin-Rolle) braucht einen **einmaligen, Maintainer-geführten** Umbau —
kurze Downtime empfohlen (keine schreibenden Requests währenddessen).

> **Die einfachere, bereits durchgespielte Alternative:** Dump → frisches Volume →
> Restore nach [§ 13.1](#131-prodstaging--daten-erhalten-wartungsfenster). Dabei
> baut das Init-Script die Zielrollen von selbst korrekt auf, und es gibt keinen
> Sonderfall. Der In-Place-Umbau unten lohnt nur, wenn das Wartungsfenster für
> Dump/Restore zu lang wäre. Refs
> #2086.

#### 9.6.0 Zuerst diagnostizieren — es gibt drei verschiedene Ausgangslagen

Der Umbauweg hängt daran, **wer die Bootstrap-Rolle des Clusters ist**. Die
Bootstrap-Rolle ist die von `initdb` angelegte Rolle (`pg_authid.oid = 10`); sie
kann sich **nicht** selbst entrechten. Als Superuser in der Anwendungs-DB:

Die Beispielnamen unten sind **Platzhalter** — setze die realen Werte aus
`POSTGRES_USER` und `POSTGRES_ADMIN_USER` deiner Env-Datei ein.

```sql
-- Wer ist die Bootstrap-Rolle (kann NIE NOSUPERUSER werden)?
SELECT rolname AS bootstrap_rolle FROM pg_authid WHERE oid = 10;
-- Vollstaendiges Attributprofil der beteiligten Rollen.
SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolcreaterole, rolcreatedb
  FROM pg_roles
 WHERE rolname IN (current_user, 'anlaufstelle', 'anlaufstelle_admin');
-- Wem gehoert die Datenbank?
SELECT pg_get_userbyid(datdba) AS db_owner FROM pg_database WHERE datname = current_database;
-- Welche Anwendungsobjekte gehoeren WEDER App noch Admin? (> 0 heisst:
-- Umbau unvollstaendig — genau das uebersieht ein reiner Ownership-Umzug.)
SELECT pg_get_userbyid(c.relowner) AS owner, count(*)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r','p','S','v','m','f')
 GROUP BY 1 ORDER BY 2 DESC;
```

| Befund | Fall | Weg |
|---|---|---|
| App- **und** Admin-Rolle existieren mit korrektem Attributprofil, nur die Ownership liegt noch bei der App-Rolle | **A** | § 9.6.1 (nur Ownership-Umzug) |
| Keine Admin-Rolle, App-Rolle ist privilegiert, aber **nicht** die Bootstrap-Rolle | **B** | § 9.6.2 |
| Keine Admin-Rolle, und die App-Rolle **ist** die Bootstrap-Rolle (`oid = 10`) | **C** | § 9.6.3 — die App **muss** auf einen neuen Rollennamen umziehen |

> **Teilzustände:** Die drei Fälle beschreiben saubere Ausgangslagen. Ein
> abgebrochener früherer Umbau kann Mischformen hinterlassen („Admin existiert
> schon, App ist noch privilegiert", „Objekte gehören noch der Bootstrap-Rolle").
> Die Bausteine unten sind einzeln idempotent: die Rollenanlage legt nur an, was
> fehlt, und normalisiert Attribute und Passwort danach unbedingt;
> Ownership-Schleifen greifen nur noch nicht umgezogene Objekte; Grants sind
> wiederholbar. Der Fall-C-Block ist als Ganzes wiederholbar — auch nach einem
> Lauf, der mittendrin abgebrochen ist. Fahre in einem solchen Fall den Fall, der zum
> **Bootstrap-Befund** passt (B oder C), und lass die bereits erledigten
> Schritte einfach durchlaufen. Die letzte Query oben ist danach das Mass:
> sie darf nur noch die Admin-Rolle zeigen.

**Fall C tritt auf, wenn kein Init-Script gemountet ist:** Laedt `db` die volle
Env ohne [`deploy/postgres-init/`](../deploy/postgres-init/), legt das
Postgres-Image `${POSTGRES_USER}` selbst per `initdb --username="$POSTGRES_USER"`
an — die App-Rolle *ist* dann die Bootstrap-Rolle, und eine Admin-Rolle wird nie
erzeugt.

#### 9.6.1 Fall A — nur der Ownership-Umzug

Ablauf als **Bootstrap-Superuser**, in der Anwendungs-DB:

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

Danach **zuerst § 9.6.4** fahren (grünes `check_db_roles`, bevor `web` wieder
startet), dann normal deployen — `docker-migrate.sh` wendet Migration `0107` an,
falls noch nicht geschehen — und mit § 9.5 verifizieren.

#### 9.6.2 Fall B — Admin-Rolle fehlt, App-Rolle ist nicht die Bootstrap-Rolle

Hier lässt sich die App-Rolle an Ort und Stelle entrechten. Als
**Bootstrap-Superuser**, in der Anwendungs-DB (Beispielnamen wie in
`.env.staging.example`):

```sql
-- 0. Vorher: web, cron und maintenance stoppen (keine App-Schreibzugriffe).
-- Ein Passwortwechsel beendet BESTEHENDE Sessions nicht — verbliebene
-- Verbindungen der Altrolle ausdruecklich kappen:
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname = current_database AND usename = 'anlaufstelle' AND pid <> pg_backend_pid;
BEGIN; -- alles Folgende ist transaktional; bei einem Fehler bleibt nichts halb stehen
-- 1. Fehlende Admin-/Owner-Rolle anlegen. Passwort >= 32 Zeichen.
CREATE ROLE anlaufstelle_admin WITH LOGIN NOSUPERUSER BYPASSRLS NOCREATEROLE NOCREATEDB
    PASSWORD '<neues-admin-passwort>';
GRANT ALL ON SCHEMA public TO anlaufstelle_admin;
-- 2. Ownership Datenbank + alle Objekte auf die Admin-Rolle uebertragen.
ALTER DATABASE anlaufstelle OWNER TO anlaufstelle_admin;
GRANT ALL PRIVILEGES ON DATABASE anlaufstelle TO anlaufstelle_admin;
REASSIGN OWNED BY anlaufstelle TO anlaufstelle_admin;
-- 3. App-Rolle entrechten — DAS ist der Schritt, den ein reiner
-- Ownership-Umzug nicht leistet.
ALTER ROLE anlaufstelle NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
ALTER ROLE anlaufstelle PASSWORD '<neues-app-passwort>';
-- 4. App-Rolle: nur noch DML.
GRANT CONNECT ON DATABASE anlaufstelle TO anlaufstelle;
GRANT USAGE ON SCHEMA public TO anlaufstelle;
ALTER DEFAULT PRIVILEGES FOR ROLE anlaufstelle_admin IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anlaufstelle;
ALTER DEFAULT PRIVILEGES FOR ROLE anlaufstelle_admin IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO anlaufstelle;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anlaufstelle;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anlaufstelle;
COMMIT;
```

> **Falls `REASSIGN OWNED BY` auch hier abbricht** (die Rolle besitzt dann
> Extension- oder Systemobjekte): die selektiven Ownership-Schleifen aus
> § 9.6.3 Schritt 4 verwenden — sie funktionieren fuer jede Rolle.

Danach in der Env-Datei setzen: `POSTGRES_PASSWORD` (neues App-Passwort),
`POSTGRES_ADMIN_USER`/`POSTGRES_ADMIN_PASSWORD` (neue Admin-Rolle) **und
`POSTGRES_BOOTSTRAP_PASSWORD`** — der neue Staging-Stack verlangt alle drei
(`settings/prod.py` lehnt einen `maintenance`-Prozess ohne sie ab, und das
Init-Script prüft sie). Alle drei ≥ 32 Zeichen, kein `change-me`-Präfix.
Abschluss mit § 9.6.4.

#### 9.6.3 Fall C — die App-Rolle ist die Bootstrap-Rolle

**Die Bootstrap-Rolle kann nicht entrechtet werden.** PostgreSQL lehnt das
ausdrücklich ab (verifiziert auf PostgreSQL 18.4):

```text
ALTER ROLE anlaufstelle NOSUPERUSER;
ERROR: permission denied to alter role
DETAIL: The bootstrap superuser must have the SUPERUSER attribute.
```

Ein `ALTER ROLE … NOSUPERUSER` auf die vorhandene App-Rolle ist hier also **kein
gangbarer Weg** — die Anwendung muss auf einen **neuen Rollennamen** umziehen.
Die alte Rolle bleibt Superuser und übernimmt genau die Position, die im
Zielmodell `postgres` hat: Bootstrap und Notfall-Wartung, **nie** Runtime.

> **`REASSIGN OWNED BY` ist hier NICHT einsetzbar.** Auf der Bootstrap-Rolle
> bricht es ab, weil sie systemerforderliche Objekte besitzt — verifiziert auf
> PostgreSQL 18.4 mit einem bootstrap-initialisierten Cluster und dem
> Anlaufstelle-Objektbestand:
>
> ```text
> REASSIGN OWNED BY anlaufstelle TO anlaufstelle_admin;
> ERROR: cannot reassign ownership of objects owned by role anlaufstelle
> because they are required by the database system
> ```
>
> Der Umzug läuft deshalb **selektiv** über den Katalog: nur Anwendungsobjekte,
> und ausdrücklich **ohne** Extension-eigene Objekte (`pg_depend.deptype = 'e'`
> — z. B. der `gtrgm`-Typ aus `pg_trgm`). Ein pauschaler Umzug entfernt die
> Extension-Mitgliedschaft zwar nicht, setzt aber ein von der Extension
> verwaltetes Mitglied eigenmächtig auf einen abweichenden Owner; solche
> Objekte gehören der Extension und nicht dem Umbau. Indexe brauchen keinen eigenen Schritt, sie folgen
> ihrer Tabelle. Das folgende Skript ist mit `psql` zu fahren (`\gexec` ist
> eine psql-Anweisung) und läuft vollständig in **einer Transaktion** — auch
> `CREATE ROLE` und `ALTER DATABASE … OWNER TO` sind transaktional (geprüft).

```sql
-- 0. Vorher: web, cron und maintenance stoppen.
-- Ein Passwortwechsel beendet BESTEHENDE Sessions nicht — verbliebene
-- Verbindungen der alten Bootstrap-/App-Rolle ausdruecklich kappen:
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname = current_database AND usename = 'anlaufstelle' AND pid <> pg_backend_pid;

-- Variablen. ACHTUNG: hinter einem \set darf KEIN Kommentar stehen. psql
-- fasst ihn nicht als Kommentar auf, sondern als weitere Argumente und haengt
-- sie an den Wert an — aus "anlaufstelle -- alte Rolle" wird dann der
-- Rollenname "anlaufstelle--alteRolle", und JEDE Ownership-Schleife unten
-- findet null Objekte, ohne dass irgendetwas fehlschlaegt.
\set ON_ERROR_STOP on
\set old_role anlaufstelle
\set app_role anlaufstelle_app
\set admin_role anlaufstelle_admin
\set dbname anlaufstelle_staging

-- Kontrollausgabe VOR dem Umbau: hier muessen exakt die vier Namen stehen,
-- ohne angehaengten Text. Stimmt etwas nicht, abbrechen.
\echo 'old_role =[':old_role']'
\echo 'app_role =[':app_role']'
\echo 'admin_role=[':admin_role']'
\echo 'dbname =[':dbname']'

BEGIN;

-- 1. App-Rolle anlegen, falls sie fehlt, und danach Attribute + Passwort
-- unbedingt setzen. Diese Form ist idempotent: sie laeuft auch nach einem
-- abgebrochenen frueheren Versuch durch, bei dem die Rolle schon existiert.
SELECT format('CREATE ROLE %I',:'app_role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname =:'app_role')
\gexec
ALTER ROLE:"app_role" WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB
    PASSWORD '<neues-app-passwort>';

-- 2. Admin-/Owner-Rolle, gleiches Muster.
SELECT format('CREATE ROLE %I',:'admin_role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname =:'admin_role')
\gexec
ALTER ROLE:"admin_role" WITH LOGIN NOSUPERUSER BYPASSRLS NOCREATEROLE NOCREATEDB
    PASSWORD '<neues-admin-passwort>';
GRANT ALL ON SCHEMA public TO:"admin_role";

-- 3. Datenbank-Ownership.
ALTER DATABASE:"dbname" OWNER TO:"admin_role";
GRANT ALL PRIVILEGES ON DATABASE:"dbname" TO:"admin_role";

-- 4. Objekt-Ownership selektiv umziehen (Ersatz fuer REASSIGN OWNED BY).
-- 4a) Relationen: Tabellen, Sequenzen, Views, Materialized Views, Foreign Tables.
SELECT format('ALTER %s %I.%I OWNER TO %I',
              CASE c.relkind WHEN 'S' THEN 'SEQUENCE'
                             WHEN 'v' THEN 'VIEW'
                             WHEN 'm' THEN 'MATERIALIZED VIEW'
                             WHEN 'f' THEN 'FOREIGN TABLE'
                             ELSE 'TABLE' END,
              n.nspname, c.relname,:'admin_role')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
   AND c.relkind IN ('r','p','S','v','m','f')
   AND n.nspname NOT IN ('pg_catalog','information_schema')
   AND n.nspname NOT LIKE 'pg\_toast%'
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
 ORDER BY c.relkind DESC
\gexec
-- 4b) Routinen (Funktionen, Prozeduren, Aggregate).
SELECT format('ALTER ROUTINE %I.%I(%s) OWNER TO %I',
              n.nspname, p.proname, pg_get_function_identity_arguments(p.oid),:'admin_role')
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE p.proowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
   AND n.nspname NOT IN ('pg_catalog','information_schema')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
\gexec
-- 4c) Eigene Typen und Domains (Tabellen-Rowtypes folgen ihrer Tabelle).
SELECT format('ALTER %s %I.%I OWNER TO %I',
              CASE t.typtype WHEN 'd' THEN 'DOMAIN' ELSE 'TYPE' END,
              n.nspname, t.typname,:'admin_role')
  FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
 WHERE t.typowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
   AND t.typtype IN ('e','d','c','r','b') AND t.typrelid = 0 AND t.typelem = 0
   AND n.nspname NOT IN ('pg_catalog','information_schema')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e')
\gexec
-- 4d) Eigene Schemata. 'public' gehoert ab PG15 pg_database_owner und folgt
-- damit automatisch dem neuen DB-Owner — dann faellt hier nichts an.
SELECT format('ALTER SCHEMA %I OWNER TO %I', n.nspname,:'admin_role')
  FROM pg_namespace n
 WHERE n.nspowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
   AND n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
\gexec

-- 5. Neue App-Rolle: nur DML.
GRANT CONNECT ON DATABASE:"dbname" TO:"app_role";
GRANT USAGE ON SCHEMA public TO:"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE:"admin_role" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO:"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE:"admin_role" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO:"app_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO:"app_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO:"app_role";

-- 6. Die alte Bootstrap-Rolle ist ab jetzt reine Bootstrap-/Notfallrolle:
-- frisches, langes Passwort setzen und nirgends mehr als Runtime nutzen.
ALTER ROLE:"old_role" PASSWORD '<neues-bootstrap-passwort>';
COMMIT;
```

**Abschlussinventur direkt danach — über alle vier umgezogenen Klassen**, nicht
nur über Relationen. Für die vier Umzugsklassen wird `0` erwartet. Die fünfte
Zeile weist die **Extension-Mitglieder** aus, die der Umbau bewusst nicht
anfasst: dort ist ein Wert **> 0 der Normalfall** und kein Fehler.

```sql
SELECT 'relationen' AS klasse, count(*) AS bei_alter_rolle
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema')
   AND n.nspname NOT LIKE 'pg\_toast%'
   AND c.relkind IN ('r','p','S','v','m','f')
   AND c.relowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
UNION ALL
SELECT 'routinen', count(*)
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema')
   AND p.proowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
UNION ALL
SELECT 'typen', count(*)
  FROM pg_type ty JOIN pg_namespace n ON n.oid = ty.typnamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema')
   AND ty.typtype IN ('e','d','c','r','b') AND ty.typrelid = 0 AND ty.typelem = 0
   AND ty.typowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_type'::regclass AND d.objid = ty.oid AND d.deptype = 'e')
UNION ALL
SELECT 'schemata', count(*)
  FROM pg_namespace n
 WHERE n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
   AND n.nspowner = (SELECT oid FROM pg_roles WHERE rolname =:'old_role')
UNION ALL
-- Extension-Mitglieder werden vom Umbau BEWUSST nicht angefasst (s.o.). Sie
-- stehen hier getrennt, damit die Inventur nicht faelschlich "alles bei der
-- Admin-Rolle" meldet: dieser Wert ist erwartungsgemaess > 0.
SELECT 'extension-objekte (by design)', count(*)
  FROM pg_depend d
  LEFT JOIN pg_class c ON d.classid = 'pg_class'::regclass AND c.oid = d.objid
  LEFT JOIN pg_proc p ON d.classid = 'pg_proc'::regclass AND p.oid = d.objid
  LEFT JOIN pg_type ty ON d.classid = 'pg_type'::regclass AND ty.oid = d.objid
 WHERE d.deptype = 'e'
   AND COALESCE(c.relowner, p.proowner, ty.typowner) = (SELECT oid FROM pg_roles WHERE rolname =:'old_role');
```

> **Was die Extension-Zeile für die Wartung bedeutet.** Die Extension und alle
> ihre Objekte bleiben Eigentum der **alten Bootstrap-Rolle** — `pg_extension`
> führt sie weiter dort. Gemessen auf PostgreSQL 18.4 nach einem Fall-C-Umbau:
> `extension_owner = anlaufstelle`, 34 Extension-Objekte weiterhin bei dieser
> Rolle, alle Anwendungsobjekte bei der Admin-Rolle. Folge: Die Admin-Rolle
> (`NOSUPERUSER BYPASSRLS`) kann **keine Extension-Wartung** — `ALTER EXTENSION
> pg_trgm SET SCHEMA …` und `DROP EXTENSION pg_trgm` scheitern beide mit
> `must be owner of extension pg_trgm`. Ein `ALTER EXTENSION … OWNER TO` gibt es
> in PostgreSQL **nicht** (Syntaxfehler), der Besitz lässt sich also nicht
> nachträglich sauber übertragen.
>
> **Praktisch heisst das:** Für ein späteres `ALTER EXTENSION … UPDATE` (etwa
> nach einem Major-Upgrade) braucht es einen **Superuser** — die alte
> Bootstrap-Rolle oder die in § 9.6.3 angelegte Rolle `postgres`. Beide bleiben
> ohnehin erhalten; ihr Passwort gehört deshalb dokumentiert und darf nicht
> verlorengehen. Als Superuser läuft der Befehl durch (verifiziert). Für den
> laufenden Betrieb ändert sich nichts: Die App-Rolle nutzt die Extension
> weiter (`similarity()` geprüft), nur die Verwaltung der Extension bleibt beim
> Superuser.
> **Vorbedingung, die technisch nicht erzwungen wird:** Die vier Schleifen
> arbeiten auf dem Snapshot ihrer jeweiligen `SELECT`-Abfrage. Ein Objekt, das
> eine **parallele** Session unter der alten Rolle *nach* der Erhebung anlegt,
> steht nicht in der Befehlsliste und bleibt bei der alten Rolle. Das Stoppen
> der Dienste und `pg_terminate_backend` oben verhindern ein **Reconnect**
> nicht. Für das Wartungsfenster gilt deshalb: **kein konkurrierendes DDL.**
> Wer das erzwingen will, sperrt die alte Rolle vorher in einer **eigenen,
> committeten** Transaktion — innerhalb des Hauptblocks wirkt es nicht, weil
> andere Sessions die Änderung erst nach dem `COMMIT` sehen:
>
> ```sql
> ALTER ROLE anlaufstelle NOLOGIN; -- vor dem Hauptblock, eigene Transaktion
> --... Hauptblock...
> ALTER ROLE anlaufstelle LOGIN; -- danach wieder freigeben
> ```
>
> Die Inventur oben ist in jedem Fall das letzte Wort: zeigt sie irgendwo
> `> 0`, den Hauptblock einfach erneut fahren — er ist idempotent.

Danach die Env-Datei umstellen — das ist der Teil, den man leicht vergisst:

| Variable | neuer Wert |
|---|---|
| `POSTGRES_USER` | `anlaufstelle_app` (**geändert**) |
| `POSTGRES_PASSWORD` | `<neues-app-passwort>` |
| `POSTGRES_ADMIN_USER` | `anlaufstelle_admin` |
| `POSTGRES_ADMIN_PASSWORD` | `<neues-admin-passwort>` |
| `POSTGRES_BOOTSTRAP_PASSWORD` | `<neues-bootstrap-passwort>` (Passwort der **alten** Rolle) |

> **Warum nicht einfach den alten Namen behalten?** Weil genau dieser Name die
> Bootstrap-Rolle ist und dauerhaft `SUPERUSER` bleibt. Liefe die Runtime weiter
> unter ihm, wäre der Fix wirkungslos — `FORCE ROW LEVEL SECURITY` greift für
> Superuser nicht. Der `web`-Healthcheck des Staging-Stacks würde das ab
> #2086 auch bemerken
> und den Container nie `healthy` melden.

> **Die Rolle `postgres` ist nach Fall C nicht optional.** In einem so
> entstandenen Cluster gibt es keine Rolle dieses Namens. Für den
> `pg_isready`-Aufruf im `db`-Healthcheck wäre das egal (er authentifiziert
> nicht) — aber das ausgelieferte Rotationswerkzeug
> [`scripts/ops/rotate-db-passwords.sh`](../scripts/ops/rotate-db-passwords.sh)
> verbindet fest als `-U postgres` und rotiert fest `ALTER ROLE "postgres"`.
> Ohne die Rolle scheitert es an der Verbindung; legt man sie nur pro forma an,
> rotiert es das Passwort einer Rolle, die niemand benutzt — und **nicht** das
> der alten Bootstrap-Rolle, auf die die Env-Tabelle oben zeigt.
>
> Deshalb im Anschluss an Fall C:
>
> ```sql
> CREATE ROLE postgres WITH LOGIN SUPERUSER PASSWORD '<neues-bootstrap-passwort>';
> ```
>
> und **dasselbe** Passwort wie bei der alten Bootstrap-Rolle setzen, damit
> `POSTGRES_BOOTSTRAP_PASSWORD` für beide stimmt. Das Rotationswerkzeug rotiert
> dann `postgres`; das Passwort der alten Bootstrap-Rolle bleibt davon
> unberührt und muss bei jeder Rotation **von Hand** mitgezogen werden:
>
> ```sql
> ALTER ROLE anlaufstelle PASSWORD '<neues-bootstrap-passwort>';
> ```
>
> Ein Umbau des Werkzeugs auf einen konfigurierbaren Bootstrap-Rollennamen ist
> bewusst nicht Teil von
> #2086 — dafür gibt es
> ein Folge-Ticket. Der saubere Ausweg bleibt Dump/Restore in ein frisches
> Volume: dort heisst die Bootstrap-Rolle wieder `postgres`.

#### 9.6.4 Abschluss (für alle Fälle verbindlich)

Der Umbau gilt erst als abgeschlossen, wenn das Rollen-Gate grün ist —
**vor** dem Wiederanlauf von `web`:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles
# bzw. staging:
docker compose -f docker-compose.staging.yml --env-file .env.staging \
    run --rm maintenance check_db_roles
# erwartet: App-Rolle rolsuper=False, rolbypassrls=False
# Admin-Rolle rolsuper=False, rolbypassrls=True (Exit-Code 0)
```

Exit ≠ 0 heisst: der Umbau ist unvollständig — `web` **nicht** starten, sondern
die Meldung abarbeiten. Erst danach deployen; der Migrate-One-Shot führt dasselbe
Gate erneut aus, und auf staging verifiziert zusätzlich der `web`-Healthcheck
laufend die tatsächliche Verbindungs-Rolle.

Das Gate prüft seit #2086
nicht mehr nur, dass die App-Rolle **nicht** Owner ist, sondern dass die
Admin-Rolle es **ist**. Ein Objekt, das nach einem halb gefahrenen Umbau noch der
alten Bootstrap-Rolle gehört, fällt damit auf — vorher lief genau dieser Zustand
grün durch.

**Fail-loud-Gate (Refs #1026):**
`check_db_roles` prüft seit A1.4 **zusätzlich zur Attribut-Prüfung die Ownership**.
Solange der Umzug oben noch nicht gelaufen ist (App-Rolle ist noch DB-/Tabellen-
Owner), meldet das Gate `FAIL … Ownership-Umzug … § 9.6` und bricht mit **Exit 1**
ab — `docker-migrate.sh` stoppt den Deploy, bevor neue Web-Replicas live gehen.
Das verhindert den früheren stillen Fail-open, bei dem der Deploy trotz noch
App-owned Tabellen grün wurde (das Attributprofil App/Admin allein bleibt beim
Ownership-Umzug ja gleich). Nach durchgeführtem Umzug (Admin ist Owner) ist das
Gate wieder grün.

> **Reihenfolge bei ausstehendem Rollen-Umzug (Refs
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
nutzt einen eigenen `invite_token_generator` — **keine** persistente
Token-Tabelle. Der Setup-Link verwendet denselben Bestätigungs-View wie der
Passwort-Reset, ist durch ein eigenes `key_salt` aber davon getrennt und an
`INVITE_TOKEN_TIMEOUT` (Default: 3 Tage) gebunden. Nach Ablauf wird der Token
vom Signer stillschweigend abgelehnt; es gibt **keine** Datenbankeintraege, die
aufzuraeumen waeren.

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

Regulärer Weg: als `facility_admin` **Verwaltung → Zugänge** öffnen, das Konto
auswählen und unter „Sicherheitsaktionen“ **Einladung erneut senden**. Der Weg
ist auf die eigene Einrichtung begrenzt, erfordert Sudo-Re-Authentifizierung
und schreibt einen attribuierten `USER_INVITE_SENT`-Audit-Eintrag. Er steht nur
für ein noch nicht fertig eingerichtetes Konto mit E-Mail-Adresse zur
Verfügung.

Nur wenn die Fachoberfläche im Notfall nicht erreichbar ist, bleibt der direkte
technische Versand als Fallback:

```bash
# Django-Shell: neuen Invite-Link an einen bestehenden User senden
docker compose -f docker-compose.prod.yml exec web \
  python manage.py shell -c "
from core.models import User
from core.services.security import send_invite_email
send_invite_email(User.objects.get(username='<username>'))
"
```

Der Shell-Fallback besitzt weder den Facility-/Status-Guard noch die
Actor-Attribution der Fachoberfläche. Zielkonto und Einrichtung deshalb vorab
prüfen und den Eingriff gesondert dokumentieren.

### 10.3 Verwaiste Konten loeschen

Im regulären Betrieb wird ein verwaistes Konto unter **Verwaltung → Zugänge**
deaktiviert. Eine endgültige Löschung ist wegen möglicher Referenzen und der
Audit-Attribution kein normaler CRUD-Schritt. Nur nach Prüfung und Rücksprache
mit der Einrichtungs-Leitung bleibt der technische Sonderweg:

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

Die Materialized View `core_statistics_event_flat` aggregiert
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

Caddys `reverse_proxy` setzt `X-Forwarded-Proto` (sowie `X-Forwarded-For`/`X-Forwarded-Host`) per Default selbst. `SECURE_PROXY_SSL_HEADER` verlaesst sich also auf dieses Caddy-Default-Verhalten, **nicht** auf eine explizite `header_up`-Direktive. (Ein zusaetzliches `header_up X-Forwarded-Proto {scheme}` waere redundant.)

**Wovon „per Default" abhaengt (Refs #1963):** Ob Caddy einen eingehenden `X-Forwarded-*`-Wert uebernimmt oder verwirft, entscheidet allein die Liste `trusted_proxies` (§ 12.2). Kommt der Request von einem Peer, der **nicht** darin steht — der Normalfall, Caddy ist der Edge —, verwirft Caddy den mitgeschickten Wert und setzt alle drei Header selbst; `X-Forwarded-For` enthaelt dann genau **einen** Eintrag, naemlich die tatsaechliche Peer-IP. Steht der Peer dagegen in `trusted_proxies`, behaelt Caddy den vorhandenen Wert: bei `X-Forwarded-For` **haengt** es die Peer-IP rechts **an**, bei `X-Forwarded-Proto`/`-Host` uebernimmt es den vorgelagerten Wert unveraendert.

Fuer `SECURE_PROXY_SSL_HEADER` heisst das: Die Annahme traegt, solange kein vorgelagerter Proxy in `trusted_proxies` steht — und ansonsten nur, wenn dieser Proxy seinerseits `X-Forwarded-Proto` korrekt setzt. Wer die Liste befuellt, verlaengert damit die Vertrauenskette bis zum vorgelagerten Proxy.

### 12.2 TRUSTED_PROXY_HOPS und TRUSTED_PROXY_CIDRS

Ergaenzend setzt `.env` (Default `1`):

```
TRUSTED_PROXY_HOPS=1
```

Erklaerung in `.env.example`. Der Wert zaehlt von rechts: [`signals.audit.get_client_ip`](https://github.com/anlaufstelle/app/blob/main/src/core/signals/audit.py) nimmt den `TRUSTED_PROXY_HOPS`-ten `X-Forwarded-For`-Eintrag von rechts und faellt auf `REMOTE_ADDR` zurueck, wenn der Header weniger Eintraege hat.

**`hops > 1` ist ohne konfiguriertes Proxy-Vertrauen wirkungslos** (Refs #1963). Aus § 12.1 folgt: Ohne Eintrag in `trusted_proxies` kommt bei der App **immer genau ein** `X-Forwarded-For`-Eintrag an. `TRUSTED_PROXY_HOPS=2` findet dann keinen zweiten Eintrag und faellt auf `REMOTE_ADDR` zurueck — das ist Caddys Container-IP, also **ein einziger Wert fuer alle Clients**: ein gemeinsamer Ratelimit-Eimer, eine gemeinsame Lockout-Achse (N9), eine Audit-IP und eine Maintenance-Allowlist-Entscheidung fuer das ganze Internet (dieselbe Klasse Fehler wie Security N1). Umgekehrt gilt: Ist die Hop-Zahl hoeher als die Zahl der Proxys, denen Caddy tatsaechlich vertraut, waehlt der Resolver einen client-kontrollierten Eintrag — der Spoofing-Fall aus #1375.

Beide Werte gehoeren deshalb zusammen. Vor einem CDN/Loadbalancer:

```
# Bereiche des vorgelagerten Proxys — Leerzeichen-getrennt, CIDR oder nackte Adresse
TRUSTED_PROXY_CIDRS=203.0.113.0/24 2001:db8::/32
TRUSTED_PROXY_HOPS=2
```

Dieselbe Variable fuellt die Caddy-Direktive `trusted_proxies static` im globalen `servers`-Block der Caddyfiles (Passthrough im `caddy`-Service der Compose-Dateien) **und** `settings.TRUSTED_PROXY_CIDRS`. Damit gibt es genau eine Quelle; Edge und Anwendung koennen nicht auseinanderlaufen.

Abgesichert ist die Kombination durch zwei System-Checks in [`core/checks.py`](https://github.com/anlaufstelle/app/blob/main/src/core/checks.py):

| ID | Ausloeser | Bedeutung |
|----|-----------|-----------|
| `core.E001` | `TRUSTED_PROXY_HOPS` negativ/kein Integer | Start bricht ab |
| `core.W001` | `hops >= 3` | Heuristik: mehr als CDN + Caddy ist selten echt (unveraendert seit #1375) |
| `core.W003` | `hops > 1`, aber `TRUSTED_PROXY_CIDRS` leer | **neu** — Hop-Zahl greift ins Leere (siehe oben) |
| `core.E003` | Eintrag in `TRUSTED_PROXY_CIDRS` ist fuer Caddy kein IP-Bereich | **neu** — Start bricht ab; sonst verweigert **Caddy** den Start, und zwar erst nach dem als healthy gemeldeten `web`-Container |

`core.W001` behaelt seine mit #1375 veroeffentlichte Bedeutung; die beiden mit #1963 hinzugekommenen Pruefungen bekommen eigene IDs, damit eine bestehende Runbook-Tabelle nicht still etwas anderes anzeigt.

**Schreibweise (`core.E003`):** Der Wert wird von **Caddy** zerlegt, nicht von der Anwendung — der Caddyfile-Parser trennt Direktiven-Argumente ausschliesslich an Leerzeichen/Tabs. Ein Komma ist damit kein Trennzeichen (`203.0.113.0/24,198.51.100.0/24` ist fuer Caddy EIN, unbrauchbarer Bereich), und Netzmasken (`192.168.0.0/255.255.255.0`) kennt Caddy nicht. Der Check bildet genau Caddys Parser nach (Gos `netip.ParsePrefix`, inklusive fuehrender Nullen wie `/032`) — was hier durchgeht, startet den Reverse-Proxy; was hier abbricht, haette ihn sterben lassen.

`trusted_proxies_strict` steht zusaetzlich in jedem globalen `servers`-Block: ab Caddy 2.8 liest Caddy die Client-IP-Header damit von **rechts** statt von links und nimmt so den vom Proxy gesetzten statt des vom Client kontrollierten Eintrags — dieselbe Leserichtung, die auch `get_client_ip` verwendet. Das gepinnte Image ist `caddy:2-alpine` in Version 2.11.4 (Digest in `docker-compose.prod.yml`), die Direktive ist dort vorhanden.

### 12.3 EXTERNAL_ORIGIN (Refs #1967)

Die Produktionspruefung verbietet in `ALLOWED_HOSTS` nur den Match-all-Eintrag `*`; Subdomain-Muster wie `.example.org` bleiben ausdruecklich erlaubt (§ `settings/prod.py`). Zeigt in einem solchen Deployment ein weiterer erlaubter Host auf die Anwendung, ginge ein Token-Link mit **diesem** Host raus — der `Host`-Header des Requests waere damit Teil der Sicherheitsgrenze.

Die drei produktiven Linkquellen — Einladungs-Setup-Link ([`services/security/invite.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/invite.py)), Passwort-Reset-Mail ([`views/auth.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/auth.py)) und Entsperr-Mail ([`views/lockout_recovery.py`](https://github.com/anlaufstelle/app/blob/main/src/core/views/lockout_recovery.py)) — lesen den Request deshalb nicht mehr. Schema und Host stammen ausschliesslich aus `EXTERNAL_ORIGIN` ([`services/security/origin.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/origin.py)):

```
EXTERNAL_ORIGIN=https://anlaufstelle.example.de
```

- **Format:** Schema (`http`/`https`) + Host + optionaler Port. Kein Pfad, kein abschliessender Schraegstrich, keine Query. Ein fehlerhafter Wert bricht den Start ab, statt in einer bereits versendeten Mail aufzufallen.
- **Ableitung:** Fuehrt `ALLOWED_HOSTS` genau **einen** oeffentlichen Hostnamen, wird `https://<host>` daraus abgeleitet (gleiches Muster wie `OTP_WEBAUTHN_RP_ID`) — der Regelfall „eine Domain hinter Caddy" braucht die Variable nicht.
- **Loopback zaehlt nicht mit:** `localhost`, `127.0.0.1` und `::1` stehen in `ALLOWED_HOSTS` nur, damit der Container-Healthcheck (`http://localhost:8000/health/`) keinen `DisallowedHost`-400 bekommt — die mitgelieferten Deploy-Vorlagen fuehren sie deshalb neben der Domain. Fuer die Ableitung bleiben sie ausser Betracht; als Basis eines Token-Links kaemen sie ohnehin nie in Frage.
- **Wenn `web` nie healthy wird, gibt es genau zwei Ursachen** (Refs #2077): entweder fehlt der Loopback in `ALLOWED_HOSTS` — dann antwortet der Healthcheck mit `DisallowedHost`-400 —, oder `SECURE_REDIRECT_EXEMPT` greift nicht: `SECURE_SSL_REDIRECT=True` beantwortet den Healthcheck-Aufruf `http://localhost:8000/health/` sonst mit einem 301 auf `https://`, und der Healthcheck-Client kann lokal kein TLS. `web` wird dann nie healthy und `caddy` (`condition: service_healthy`) startet gar nicht erst. Beide Haelften sind seit #2077 in `settings/prod.py` und den `.env*.example`-Vorlagen gesetzt; bei einer aelteren, von Hand gepflegten `.env` sind sie nachzutragen. Schnelltest im Container: `python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health/').status)"` muss `200` liefern, nicht 301 oder 400.
- **Fail-closed:** Bei mehreren oeffentlichen Hosts, reiner Wildcard-Config oder ausschliesslich Loopback-Eintraegen gibt es keine eindeutige Antwort; Prod startet dann nicht und nennt `EXTERNAL_ORIGIN` im Abbruch. Bewusst kein Raten: eine falsch geratene Origin macht jeden Token-Link unbrauchbar.
- **Nicht verschaerft wurde die `ALLOWED_HOSTS`-Pruefung selbst** — Subdomain-Muster bleiben erlaubt, sie sind fuer die Linkbildung jetzt schlicht ohne Bedeutung.

Bei einem Domainwechsel ist `EXTERNAL_ORIGIN` mitzuziehen: bereits versendete Links zeigen weiter auf die alte Adresse und laufen ueber deren Gueltigkeitsdauer (`DJANGO_PASSWORD_RESET_TIMEOUT` bzw. `DJANGO_INVITE_TOKEN_TIMEOUT`) aus.

---

## 13. PostgreSQL-Major-Upgrade (16 → 18)

Refs #1039. PostgreSQL-Major-Versionen haben **inkompatible Datenverzeichnisse** — ein In-Place-Start des neuen Images auf dem alten Volume ist unmöglich. Gewählter und lokal durchgespielter Pfad: **dump → frisches PG18-Cluster → restore**. (`pg_upgrade` verworfen: das Alpine-Image enthält nur eine Binär-Major-Version, und PG18-`initdb` aktiviert Data-Checksums per Default — ein 16er-Cluster ohne Checksums wäre dafür erst zu konvertieren.)

**Geändertes Image-Layout ab 18:** `PGDATA` liegt unter `/var/lib/postgresql/18/docker`, das Image deklariert `VOLUME /var/lib/postgresql`. Die Compose-Dateien mounten seit #1039 deshalb das Elternverzeichnis (`…:/var/lib/postgresql` statt `…/data`). Startet man den neuen Stand versehentlich gegen ein altes 16er-Volume, bricht der Entrypoint **kontrolliert ohne Datenverlust** ab („there appears to be PostgreSQL data in: /var/lib/postgresql" + Hinweistext).

### 13.1 Prod/Staging — Daten erhalten (Wartungsfenster)

Staging identisch mit `docker-compose.staging.yml`, `.env.staging` und Volume `*_pgdata_staging`. Reihenfolge ist sicherheitskritisch: **Rollen vor Daten** — das Init-Script legt App-/Admin-Rolle im frischen Cluster an, bevor der Restore Ownership/Grants wiederherstellt (ADR-020). Fuer Staging gilt das erst ab #2086: davor mountete der Stack `deploy/postgres-init` gar nicht (§ 1.4).

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
docker compose -f docker-compose.prod.yml run --rm -T maintenance check_db_roles # Exit 0
docker compose -f docker-compose.prod.yml exec -T db sh -c \
    'psql -U postgres -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity"' # > 0

# 8. Migrationen als One-Shot (i. d. R. No-op) + Stack hoch + Health (§ 1.2/1.3)
docker compose -f docker-compose.prod.yml run --rm --entrypoint=/app/docker-migrate.sh maintenance
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
