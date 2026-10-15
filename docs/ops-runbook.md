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
13. [PostgreSQL-Major-Upgrade](#13-postgresql-major-upgrade)

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
# (deploy/postgres-init/01-app-role.sh). Fehlt ein Passwort, steht es noch auf
# change-me-* oder hat es weniger als 32 Zeichen, bricht das Init-Skript beim
# Anlegen des Clusters ab. Quelle: .env.example (liefert die Passwörter leer aus).
ENV_FILE="${ENV_FILE:-.env}"
# Last active entry of a variable, read like Compose reads .env: a quoted value ends at
# the closing quote (double quotes: \" \\ \$ \n are escapes; single quotes: only \'),
# anything after the closing quote is ignored; an unquoted value ends at the first " #".
# An unterminated quote or a value that starts with "#" counts as empty.
env_value() {
    local line value out="" rest char next closed=0
    line=$(grep -E "^[[:space:]]*(export[[:space:]]+)?${1}[[:space:]]*=" "$ENV_FILE" | tail -n 1)
    value=${line#*=}
    value=${value#"${value%%[![:space:]]*}"}
    case "$value" in
        \"*)
            rest=${value#\"}
            while [ -n "$rest" ]; do
                char=${rest:0:1}; rest=${rest:1}
                if [ "$char" = '"' ]; then closed=1; break; fi
                if [ "$char" = '\' ] && [ -n "$rest" ]; then
                    next=${rest:0:1}; rest=${rest:1}
                    case "$next" in
                        '"' | '\' | '$') out+=$next ;;
                        n) out+=$'\n' ;;
                        *) out+=$char$next ;;
                    esac
                else
                    out+=$char
                fi
            done
            [ "$closed" = 1 ] || out="" ;;
        \'*)
            rest=${value#\'}
            while [ -n "$rest" ]; do
                char=${rest:0:1}; rest=${rest:1}
                if [ "$char" = "'" ]; then closed=1; break; fi
                if [ "$char" = '\' ] && [ "${rest:0:1}" = "'" ]; then
                    out+="'"; rest=${rest:1}
                else
                    out+=$char
                fi
            done
            [ "$closed" = 1 ] || out="" ;;
        \#*) out="" ;;
        *)
            out=${value%% \#*}
            out=${out%"${out##*[![:space:]]}"} ;;
    esac
    printf '%s' "$out"
}
for var in POSTGRES_PASSWORD POSTGRES_ADMIN_PASSWORD POSTGRES_BOOTSTRAP_PASSWORD; do
    val=$(env_value "$var")
    case "$val" in
        change-me*) echo "✗ $var steht noch auf dem Platzhalter" ;;
        *) if [ "${#val}" -ge 32 ]; then
               echo "✓ $var gesetzt (${#val} Zeichen)"
           else
               echo "✗ $var fehlt oder hat weniger als 32 Zeichen"
           fi ;;
    esac
done
if [ -n "$(env_value POSTGRES_ADMIN_USER)" ]; then
    echo "✓ POSTGRES_ADMIN_USER gesetzt"
else
    echo "✗ POSTGRES_ADMIN_USER fehlt"
fi
```


> **Einzelschlüssel abgekündigt (#2240):** Meldet `python manage.py check --deploy` die Warnung `core.W005`, läuft die Instanz noch mit `ENCRYPTION_KEY` statt mit dem Schlüsselring `ENCRYPTION_KEYS`. Das Update ist davon nicht betroffen; vor der Entfernung nach Version 1.0 den Wert unverändert nach `ENCRYPTION_KEYS` übernehmen und `ENCRYPTION_KEY` entfernen — Anleitung im [Admin-Guide, Abschnitt „Einzelschlüssel → Schlüsselring migrieren“](admin-guide.md#vollständige-umgebungsvariablen-referenz).

### 1.2 Deploy-Schritte

Refs #802 (C-34): Migrationen laufen als separater One-Shot-Job **vor** dem Rolling-Restart. Der Web-Entrypoint macht nur noch `collectstatic` + `gunicorn` — keine Migrationen mehr.

> **Erstinbetriebnahme statt Deploy?** Dieser Abschnitt beschreibt den *laufenden* Deploy. Für die **erste** Installation gibt es [`scripts/initial-setup.sh`](../scripts/initial-setup.sh) (Refs #793): DB starten und auf Bereitschaft warten → Migrate-One-Shot → Stack hochfahren → `check --deploy` → `check_db_roles` → Super-Admin-Bootstrap. Idempotent und fail-loud; Anleitung in [docs/admin-guide.md § 1 Schritt 4](admin-guide.md).

```bash
cd /opt/anlaufstelle

# 0. Zielstand wählen: Repository auf den Release-Tag der Zielversion bringen und
#    APP_VERSION (bei Digest-Pinning gemeinsam mit APP_IMAGE_DIGEST) in der .env
#    passend setzen; Digest und Signatur vorher prüfen (§ 8.4a). Ohne neuen Wert
#    zieht der Pull im nächsten Schritt dasselbe Image erneut.
git fetch --tags && git checkout vX.Y.Z
grep -E '^APP_(VERSION|IMAGE_DIGEST)=' .env

# Vollständige Compose-Dateiliste in fester Reihenfolge (admin-guide.md § 1).
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)

# 1. Hintergrundjobs anhalten (cron nutzt Anwendungscode und Schema) und prüfen,
#    dass kein Wartungs-One-Shot mehr läuft (Ausgabe muss leer sein).
docker compose "${compose_files[@]}" stop cron
docker compose "${compose_files[@]}" ps --all --format '{{.Name}} {{.Status}}' maintenance

# 2. Neue Images ziehen: web, cron und maintenance nutzen dasselbe App-Image.
docker compose "${compose_files[@]}" pull web cron maintenance

# 3. Migrationen als One-Shot-Job ausführen (advisory lock hält
#    parallele Jobs zurück, lange RunPython blockiert keine Worker).
docker compose "${compose_files[@]}" run --rm \
    --entrypoint=/app/docker-migrate.sh maintenance

# 4. Stack neu starten (web + cron + caddy, DB bleibt).
#    Entrypoint laeuft jetzt nur noch collectstatic + gunicorn.
docker compose "${compose_files[@]}" up -d web cron caddy
```

**Hintergrundjobs und Schema:** `cron` ist ein eigener Dienst mit Anwendungscode aus demselben Image; ein `up -d web caddy` ersetzt ihn nicht, und er liefe weiter mit altem Code und alten Umgebungswerten gegen die neue Datenbank. Deshalb wird `cron` vor der Migration angehalten (Schritt 1) und mit dem neuen Image wieder gestartet (Schritt 4). Das Update nicht in die Zeitfenster der Nachtjobs legen (Sidecar-Zeitplan: § 3.2; Backup 02:00, Retention 03:00 bis Session-Bereinigung 05:00), weil ein beim Anhalten laufender Job unterbrochen werden kann.

**Erwartete Downtime:** ~3-5 Sekunden (nur Gunicorn-Restart). Migrationen laufen vorab im One-Shot-Job — der Web-Container faehrt erst hoch, wenn das Schema bereit ist. Bei mehreren Web-Replicas wartet keine mehr auf den Migrate-Lock.

> **Ownership-Normalisierung (Refs #1085, #1026):** Der One-Shot-Job fuehrt nach `migrate` weiterhin `normalize_db_ownership` aus. Der **DB-Owner ist die Admin-Rolle** selbst; da `migrate` als eben diese Rolle laeuft, ist `REASSIGN OWNED BY current_user TO db_owner` ein **No-op** (`current_user == db_owner`). Die App-Runtime bekommt ihre Tabellen-Rechte nicht mehr ueber Ownership, sondern per DML-Grant (`ALTER DEFAULT PRIVILEGES … TO app` im Init-Script, § 9). Der Schritt bleibt als idempotentes Sicherheitsnetz; ein Fehler bricht den Deploy fail-fast ab, bevor neue Web-Replicas live gehen.

### 1.3 Post-Deploy-Verifizierung

```bash
cd /opt/anlaufstelle

# Vollständige Compose-Dateiliste in fester Reihenfolge (admin-guide.md § 1).
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)

# Container-Status
docker compose "${compose_files[@]}" ps

# Start-Log des Web-Containers (collectstatic, check --deploy, gunicorn). Hier steht
# keine Migrationsausgabe: der Web-Entrypoint migriert nicht (§ 1.2). Die Ausgabe des
# One-Shots aus § 1.2 Schritt 3 erscheint nur im Terminal (der Container wird mit
# --rm entfernt); den Stand belegt die Prüfung der Migrationen weiter unten.
docker compose "${compose_files[@]}" logs web --tail=50

# Health-Check
curl -sf https://$DOMAIN/health/
# Erwartete anonyme Antwort: {"status": "ok"}

# Migrations-Status verifizieren
docker compose "${compose_files[@]}" exec web \
  python manage.py showmigrations | grep '\[ \]'
# Keine ausstehenden Migrationen → leere Ausgabe

# Django Deployment-Check
docker compose "${compose_files[@]}" exec web \
  python manage.py check --deploy

# web und cron müssen dasselbe Image laufen (gleiche Image-ID in beiden Zeilen)
docker inspect --format '{{.Name}} {{.Config.Image}} {{.Image}}' \
  $(docker compose "${compose_files[@]}" ps -q web cron)

# Lesender Wartungs-Smoke mit der Admin-Rolle (ändert nichts)
docker compose "${compose_files[@]}" run --rm maintenance check_db_roles
docker compose "${compose_files[@]}" run --rm maintenance enforce_retention --dry-run
```

Beim tatsächlichen Löschen von Ereignissen „Mit Zusatzangaben“ verlangt `enforce_retention` zwei belegte Freigaben oder die nachweisliche Auto-Freigabe-Ausnahme (Opt-in, standardmäßig aus). Unvollständige Freigabebelege führen zu Überspringen und Meldung; die Vorschlagskarte zeigt „Freigabebelege unvollständig – wird nicht gelöscht“. `--dry-run` zählt Kandidaten und ersetzt diese Laufzeitprüfung nicht.

### 1.4 Compose-Stack-Abgleich

Refs #2086. Die Compose-Stacks fahren dasselbe Drei-Rollen-PostgreSQL-Modell (kanonische Beschreibung: [§ 9 „DB-Rollenmodell (Goldstandard)"](#db-rollenmodell-goldstandard)). Der Abgleich ist Soll **und** Ist — driftet ein Stack, faellt das Verhalten dort still auseinander, ohne dass ein Test es meldet:

| Stack | `deploy/postgres-init` gemountet | `db` bootstrappt als | `web` verbindet als | `maintenance` | `cron` | Env-Vorlage |
|---|---|---|---|---|---|---|
| `docker-compose.prod.yml` | ja | `postgres` (Bootstrap-Superuser) | App-Rolle `${POSTGRES_USER}` (Allowlist) | ja | ja | `.env.example` |


Invarianten, die fuer **jeden** Stack gelten:

- Der `db`-Service laedt **kein** `env_file`, sondern setzt seinen `environment:`-Block ueber Compose-Interpolation — sonst waere `POSTGRES_USER` die App-Rolle und damit der Bootstrap-Superuser.
- Jeder Stack setzt `REQUIRE_ADMIN_DB_ROLE: "true"`; der Pfad ohne separate Admin-Rolle (App-Rolle wird selbst Owner) ist damit ausgeschlossen.
- Die App-Rolle ist nirgends Owner und nirgends `SUPERUSER`/`BYPASSRLS`.
- `web` laedt **kein** `env_file`, sondern eine explizite Runtime-Allowlist (Refs #1962). Unter den **Anwendungsdiensten** sehen Admin-/Bootstrap-Secrets nur `maintenance` und `cron`; der `db`-Service bekommt sie zwangslaeufig ebenfalls (`POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `ADMIN_DB_PASSWORD`) — er legt die Rollen ja an. Er erhaelt aber ausschliesslich die dafuer noetigen Werte und keine Anwendungs-Secrets (`DJANGO_SECRET_KEY`, `ENCRYPTION_KEY*`).


---

## 2. Rollback-Prozedur

### 2.1 Image zuruecksetzen (ohne DB-Rollback)

Wenn die neue Version Anwendungsfehler hat, aber die Migrationen rueckwaertskompatibel sind:

```bash
cd /opt/anlaufstelle

# 1. In der .env die vorherige Version eintragen (nicht in docker-compose.prod.yml
#    editieren: die versionierte Datei kollidiert sonst beim nächsten Update):
#      APP_VERSION=vX.Y.Z               (vorherige Version; ob der Tag pullbar ist:
#                                        docker manifest inspect ghcr.io/anlaufstelle/app:vX.Y.Z)
#      APP_IMAGE_DIGEST=@sha256:…       (Digest dieser Version) oder Zeile leeren;
#                                        ein gesetzter Digest wird sonst an den alten Tag gehängt
#    Wurde das Repository mit ausgecheckt: ebenfalls auf den vorherigen Tag zurück
#    (git checkout vX.Y.Z), damit Compose-Datei und deploy/crontab zum Image passen.

# Vollständige Compose-Dateiliste in fester Reihenfolge (admin-guide.md § 1).
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
# 2. web UND cron neu erzeugen (beide nutzen dasselbe Image; maintenance
#    zieht beim nächsten Aufruf automatisch dieselbe Version)
docker compose "${compose_files[@]}" up -d web cron

# 3. Verifizieren: gleiche Image-ID für web und cron, Health
docker inspect --format '{{.Name}} {{.Config.Image}} {{.Image}}' \
  $(docker compose "${compose_files[@]}" ps -q web cron)
curl -sf https://$DOMAIN/health/
```

**Voraussetzung: Schema-Kompatibilität.** Der Image-Rollback ändert nur den Code. `web`, `cron` und `maintenance` arbeiten danach mit dem alten Code gegen den Schemastand, den die Migrationen der neueren Version hinterlassen haben; das ist nur zulässig, wenn der alte Code mit diesem Schema umgehen kann (Rückwärtskompatibilität der Migrationen, Changelog prüfen). Andernfalls zuerst § 2.2 bzw. § 2.3.

### 2.2 Migration zurueckrollen

Wenn eine Migration Daten oder Schema inkompatibel verändert hat. `migrate` läuft im Dienst `maintenance` (Schema-Owner, Admin-Rolle); unter der App-Rolle von `web` bricht der Rollback mit „must be owner of relation …“ ab:

```bash
cd /opt/anlaufstelle

# Vollständige Compose-Dateiliste in fester Reihenfolge (admin-guide.md § 1).
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)

# 0. Hintergrundjobs anhalten, solange das Schema nicht zum Code passt
docker compose "${compose_files[@]}" stop cron

# 1. Migrations-Status ansehen
docker compose "${compose_files[@]}" exec web \
  python manage.py showmigrations core

# 2. Auf eine bestimmte Migration zurueckrollen
#    Beispiel: zurueck auf Migration 0042
docker compose "${compose_files[@]}" run --rm maintenance \
  migrate core 0042

# 3. Danach altes Image deployen und cron wieder starten (siehe 2.1)
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
cd /opt/anlaufstelle

# Vollständige Compose-Dateiliste in fester Reihenfolge (admin-guide.md § 1).
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)

# 1. Hintergrundjobs anhalten; db und web bleiben fuer Restore und Medien noetig
docker compose "${compose_files[@]}" stop cron
docker compose "${compose_files[@]}" up -d db web

# 2. Backup wiederherstellen — DB plus optional Medien.
#    restore.sh fuellt eine frische Wiederherstellungs-Datenbank, verifiziert sie
#    gegen das signierte Manifest und schaltet erst danach unter Schreibruhe um;
#    der bisherige Stand bleibt als <POSTGRES_DB>_vor_restore_<Zeitstempel> liegen.
#    Ohne --yes (oder RESTORE_CONFIRM=ja) fragt das Skript interaktiv nach "ja";
#    nicht-interaktiv (Cron, Pipe, kein Terminal) bricht es vor jeder Aenderung ab.
#    Ein Satz ohne signiertes Manifest wird nicht eingespielt. Bindet der Satz
#    einen Audit-Anker, verwendet es den eigenen Pfad oder den Compose-Default.
#    Nur --skip-audit-anchor auf einem Host ohne eigenen Pfad lässt den Anker aus.
#    Die Backup-Dateien sind 0600 und gehören dem Konto von backup.sh (im
#    empfohlenen Setup root): restore.sh als dieser Eigentümer starten (sudo);
#    Lesen über die Gruppe reicht nicht. Die .env liest das Skript selbst.
sudo ./scripts/ops/restore.sh --yes \
    backups/daily/anlaufstelle_YYYY-MM-DD_HHMMSS.sql.gz.enc \
    backups/daily/anlaufstelle_YYYY-MM-DD_HHMMSS_media.tar.gz.enc

# 3. Vorherige Version in der .env setzen (APP_VERSION, bei Digest-Pinning auch
#    APP_IMAGE_DIGEST; siehe 2.1) — nicht in docker-compose.prod.yml editieren

# 4. Migrationen der gewählten Version als One-Shot ausführen (restore.sh startet
#    weder web noch cron und migriert nicht; stammt der Stand aus einer anderen
#    Schema-Version als das Image, holt dieser Schritt ihn nach)
docker compose "${compose_files[@]}" run --rm \
    --entrypoint=/app/docker-migrate.sh maintenance

# 5. Stack starten (restore.sh laesst web und cron gestoppt)
docker compose "${compose_files[@]}" up -d

# 6. Verifizieren
curl -sf https://$DOMAIN/health/

# 7. Erst nach erfolgreicher Abnahme den gesicherten Vorstand entfernen
docker compose "${compose_files[@]}" exec -T db \
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
| `verify_audit_chain` | Taeglich 03:30 (nach Retention) | Sidecar | HMAC-Integritaetskette des AuditLog verifizieren; Exit-Code != 0 bei Tamper-Verdacht (Refs #1070). Ein vollstaendig gruener Lauf schreibt den Last-Run-Marker `AUDIT_CHAIN_VERIFIED` — sichtbar als Zeile „Audit-Ketten-Prüfung" unter `/system/` und im Compliance-Dashboard (Refs #1872). Ein Tamper-Fund schreibt **keinen** Marker, die Ampel laeuft also auf ueberfaellig |
| `create_statistics_snapshots` | Monatlich 1. Tag 04:00 | Sidecar | Monats-Aggregate je Einrichtung neu berechnen; durch einen Retention-Lauf geloeschte Ereignisse bleiben als Uebertrag (`_deleted`) im Monatsaggregat erhalten |
| `cleanup_orphan_storage_files` | Taeglich 04:30 (nach Snapshots) | Sidecar | Verwaiste `.enc`-Dateien ohne `Attachment`- **und ohne offenen `ImportJob`-Record** loeschen (owner-unabhaengig: Ereignis-, Personen- und Fall-Anhaenge; CSV-Import-Uploads unter `<facility>/imports/` sind ueber `ImportJob.storage_filename` geschuetzt und zählen bei der Kontingent-Durchsetzung mit, solange der Lauf offen ist — `commit_import`/`abort_import` leeren das Feld, finale Laeufe schuetzen nicht). Pro Lauf werden standardmaessig hoechstens 10.000 Dateien geprueft; `--budget N` setzt ein positives eigenes Limit, der gespeicherte Fortsetzungspunkt setzt den naechsten Lauf fair fort. `--dry-run` meldet nur Loeschkandidaten und veraendert weder Dateien noch Fortsetzungspunkt. Die Ausgabe nennt gepruefte und getrennt uebersprungene Dateien, Loeschkandidaten, tatsaechliche Loeschungen, Fehler, den Fortsetzungsort und den naechsten Start beziehungsweise den vollstaendigen Baumdurchlauf. MUSS als Admin-DB-Rolle (BYPASSRLS) laufen, sonst Fail-Loud — ohne Bypass saehe der Lauf zudem 0 `ImportJob`-Zeilen und loeschte die offenen Uploads aller Einrichtungen (Refs #662, #1554, #1738, #1060, #2180, #2376) |
| `clearsessions` | Taeglich 05:00 | Sidecar | Abgelaufene Zeilen aus `django_session` entfernen (Speicherbegrenzung nach Art. 5 Abs. 1 lit. e DSGVO) |
| `detect_breaches` | Stuendlich :30 | Sidecar | Heuristik-basierte Breach-Detection (acht Arten, [§ 6.5b](#65b-dsgvo-art-3334--breach-notification-refs-685)) → AuditLog SECURITY_VIOLATION + optionaler Webhook (Refs #685) |
| `refresh_statistics_view` | Stuendlich :15 | Sidecar | Materialized View `core_statistics_event_flat` aktualisieren (Statistik-Dashboard) |
| Invite-Token-Audit | Woechentlich So 05:00 | Host | Verwaiste Invite-User-Konten aufspueren (siehe [10](#10-invite-token-hygiene)); reine `psql`-Abfrage, der App-Container hat keinen psql-Client |
| Health-Check | Alle 5 Minuten | Host | Verfuegbarkeit pruefen (externes Monitoring, gehoert nicht in den ueberwachten Stack) |

### 3.2 Cron-Sidecar (Standard)

Der `cron`-Service in
[`docker-compose.prod.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.prod.yml)
faehrt dasselbe App-Image wie `web`, startet darin aber statt Gunicorn den
Cron-Daemon **supercronic** und arbeitet die Job-Definitionen aus
[`deploy/crontab`](https://github.com/anlaufstelle/app/blob/main/deploy/crontab)
ab (read-only in den Container gemountet). Nach dem Stack-Start mit der
vollständigen Compose-Dateiliste ([§ 1.2](#12-deploy-schritte)) laufen
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
Anwendungskommandos schreiben nach erfolgreichem Lauf einen Marker ins
AuditLog, den das Compliance-Dashboard (`/system/compliance/`, Kategorie
„Hintergrundjobs") und `stale_jobs` im [`/health/`-Payload](monitoring-guide.md)
auswerten: `RETENTION_RUN_COMPLETED` (`enforce_retention`),
`AUDIT_CHAIN_VERIFIED` (`verify_audit_chain`), `SNAPSHOT_RUN_COMPLETED`
(`create_statistics_snapshots`), `BREACH_SCAN_COMPLETED` (`detect_breaches`) und
`MV_REFRESH_COMPLETED` (`refresh_statistics_view`). Djangos Standardkommando
`clearsessions` und `cleanup_orphan_storage_files` schreiben keinen Marker; ihr
Start und Exit-Code stehen nur im supercronic-Log. Das Host-Monitoring sollte deshalb auf Fehlschlaege in
den `cron`-Logs alarmieren — insbesondere auf `verify_audit_chain`, das bei
Tamper-Verdacht mit Exit-Code != 0 endet.

Erfolgsmarker für Snapshots, Retention und Breach-Scan setzen jetzt einen erfolgreichen installationsweiten Lauf voraus: `job_run.scope=installation`, `job_run.status=success`, keine übersprungenen Einrichtungen oder technischen Fehler. Ein `--facility`-Lauf frischt den globalen Erfolg nicht auf; beim gefilterten Breach-Scan fehlen auch die Systemheuristiken. Auch im Snapshot gespeicherte Backendfehler (`backend_data._errors`) und technische Retentionfehler beim Statistikübertrag erzeugen keinen Erfolgsmarker; das betroffene Ereignis bleibt durch Rollback erhalten, andere Ziele werden weiterverarbeitet. Das gilt auch bei Backendfehlern im ersten Monatsaufbau oder beim Neuaufbau einer bereits fehlerhaften Monatszeile während des Statistikübertrags: Ereignisinhalt, vorhandener Snapshot und dessen Zeitstempel bleiben unverändert; ein späterer erfolgreicher Lauf kann den Übertrag nachholen. Auch Dateilöschfehler bei Ereignisanhängen und veralteten Importaufträgen zählen als technische Fehler: Sie verhindern den globalen Erfolgsmarker und führen nach Bearbeitung der übrigen Ziele zum Fehlerabschluss. Das betroffene Ereignis mit seinen Anhangzeilen beziehungsweise der offene Importauftrag mit seiner Dateireferenz bleibt für einen späteren Löschversuch erhalten; wiederholte Dateifehler melden keinen Bereinigungserfolg. Fachlicher Aufschub wegen Retention-Schutz zählt nicht als technischer Fehler. Einrichtungen ohne Settings bei Snapshot/Retention erzeugen ebenfalls keinen Erfolgsmarker. Ein Snapshotlauf muss für jede Einrichtung den fälligen Vormonat abdecken; gezielte historische Einzelmonate und Backfills ohne diesen Monat frischen den globalen Erfolg nicht auf. Die Command-Ausgabe nennt Abdeckungszähler; Snapshot- und technische Retentionfehler enden nach Bearbeitung der übrigen Ziele mit Exit ungleich 0. Dry-Run und Retention-Propose schreiben ebenfalls keinen Erfolgsmarker. Historische Marker ohne expliziten Nachweis werden ignoriert: bis zum nächsten vollständigen Lauf kann die Karte unbekannt anzeigen oder behält das Alter des letzten nachgewiesenen Voll-Laufs. Das Health-Detail übernimmt dieselben Altersprüfungen; es nennt kritische Jobschlüssel, während unbekannter Status allein eine frische Installation nicht degradiert.

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

**Dateirechte:** `backup.sh` setzt die Rechte unabhängig von der umask des
Aufrufers (Cron, `sudo`): Dump, Medienarchiv, Audit-Anker und die
`.hmac`-Signaturen sind `0600`, nur das Manifest ist `0640`, vom Skript angelegte
Verzeichnisse `0750`; Eigentümer ist das Konto, unter dem das Skript läuft
(empfohlen: root). Andere (weder Eigentümer noch Gruppe) bekommen nie ein Recht.
Vor der Veröffentlichung prüft es, dass keine Datei des Satzes ein Recht für
Andere und außer dem Manifest keine ein Gruppenrecht trägt (Meldung
`File modes checked (backup set): … only the manifest readable by the group, none
accessible to others.`); sonst bricht es mit Exit 1 ab und veröffentlicht den
Satz nicht.
Die Weekly-/Monthly-Kopien entstehen in einem Zwischenverzeichnis, werden dort
mit derselben Prüfung kontrolliert und erst danach (Manifest zuletzt) in
`weekly/` bzw. `monthly/` verschoben; scheitert die Prüfung, wird von der Kopie
nichts veröffentlicht, frühere Kopien bleiben unberührt (der bereits
veröffentlichte Daily-Satz bleibt bestehen). Die Gruppe darf das Manifest lesen,
damit der `web`-Container (UID/GID 1000) es für die Backup-Anzeige in `/health/`,
Systemübersicht und Compliance auswertet; die übrigen Dateien prüft die Anzeige
nur auf Vorhandensein, ihr Inhalt bleibt dem Eigentümer vorbehalten. Das setzt das
Verzeichnis-Setup aus dem Admin-Handbuch voraus: `BACKUP_HOST_DIR` mit `root:1000`
und Modus `2750` (die Gruppe 1000 erben dann die neuen Unterverzeichnisse und
Dateien). Fehlt die
Gruppe 1000 am Verzeichnis, bleibt die Anzeige auf „fehlt/unbekannt“. Ein bereits
vorhandenes `BACKUP_HOST_DIR` behält seinen Modus.

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
# Rolle postgres über den lokalen Socket im db-Container (ohne Passwort), Datenbank
# anlaufstelle = Standardwert von POSTGRES_DB (bei abweichendem Namen ersetzen: die
# Root-Crontab liest keine .env). Jedes % muss in der Crontab als \% stehen.
0 5 * * 0 cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T db psql -U postgres -d anlaufstelle -c "SELECT username, email, date_joined FROM core_user WHERE last_login IS NULL AND NOT (password LIKE 'pbkdf2_\%') AND date_joined < now() - interval '7 days';" >> /var/log/anlaufstelle-invites.log 2>&1

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
<summary>Vollstaendige Host-Crontab — Alternative, wenn der `cron`-Sidecar nicht laeuft</summary>

Relevant nur, wenn im Compose-Stack der `cron`-Dienst bewusst nicht gestartet
wird; laeuft er, fuehren diese Zeilen jeden Job doppelt aus. Die Zeilen setzen
den Compose-Stack und den Dienst `maintenance` voraus. Wer ohne Compose
orchestriert (z. B. k8s), uebertraegt dieselben Kommandos mit der Admin-Rolle
in die eigene Umgebung.
**Grenze (Audit-Anker):** `backup.sh` liest den Anker ausschließlich per
`docker compose exec -T cron cat`. Ist `cron` gestoppt oder die Ankerdatei
fehlend, unlesbar oder leer, bricht das Backup mit Exit-Code 1 ab und
veröffentlicht keinen neuen Satz. Das Backup-Beispiel dieser Alternative
braucht deshalb einen laufenden `cron`-Sidecar; die übrigen Jobs sind davon
nicht betroffen.
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
# Rolle postgres über den lokalen Socket im db-Container (ohne Passwort), Datenbank
# anlaufstelle = Standardwert von POSTGRES_DB (bei abweichendem Namen ersetzen: die
# Root-Crontab liest keine .env). Jedes % muss in der Crontab als \% stehen.
0 5 * * 0 cd /opt/anlaufstelle && docker compose -f docker-compose.prod.yml exec -T db psql -U postgres -d anlaufstelle -c "SELECT username, email, date_joined FROM core_user WHERE last_login IS NULL AND NOT (password LIKE 'pbkdf2_\%') AND date_joined < now() - interval '7 days';" >> /var/log/anlaufstelle-invites.log 2>&1

# Health-Check (alle 5 Minuten)
*/5 * * * * curl -sf https://DOMAIN/health/ > /dev/null || echo "Anlaufstelle health check failed at $(date)" >> /var/log/anlaufstelle-health.log
```

</details>

**Reihenfolge beachten:** Backup (02:00) → Retention (03:00) → Audit-Ketten-Verifikation (03:30) → Snapshots (04:00, monatlich) → Orphan-Cleanup (04:30) → Session-Bereinigung (05:00) → Breach-Detection/MV-Refresh (stuendlich). Backup muss vor Retention laufen, damit geloeschte Daten im Backup enthalten sind. Die Audit-Ketten-Verifikation laeuft nach der Retention, damit die von `prune_auditlog` frisch geschriebenen Checkpoint-Grenzen bereits in der Kette stehen. Der MV-Refresh nutzt `CONCURRENTLY` und blockiert laufende Reader nicht. Genau diese Reihenfolge bildet der Sidecar-Zeitplan in `deploy/crontab` ab — deshalb ist die `TZ`-Einstellung aus [§3.2](#32-cron-sidecar-standard) betriebsrelevant und nicht Kosmetik.


### 3.5 Manuelle Ausfuehrung

Die Neuberechnung setzt den Übertrag (`_deleted`) dieser Version voraus; Bestände aus Versionen vor Einführung dieses Übertrags werden nicht rekonstruiert.

Diese Kommandos laufen im One-Shot-Dienst `maintenance`, nicht per `exec web`: Nur `maintenance` erhält die Admin-Rolle (BYPASSRLS, Schema-Owner). Unter der App-Rolle von `web` bricht `enforce_retention` ab, `create_statistics_snapshots` überspringt jede Einrichtung („No settings … skipping“) und endet trotzdem mit Exit 0, und `refresh_statistics_view` scheitert an der fehlenden Owner-Berechtigung. In Cron-Zeilen und Skripten ohne TTY `run --rm -T maintenance …` verwenden. Harmlose Kommandos (`showmigrations`, `check --deploy`, `printenv`) bleiben in `web`.

```bash
# Retention Testlauf (kein Loeschen)
docker compose -f docker-compose.prod.yml run --rm maintenance \
  enforce_retention --dry-run

# Retention ausfuehren
docker compose -f docker-compose.prod.yml run --rm maintenance \
  enforce_retention

# Nur eine Einrichtung
docker compose -f docker-compose.prod.yml run --rm maintenance \
  enforce_retention --facility "Beratungsstelle Nord"

# Snapshots Testlauf
docker compose -f docker-compose.prod.yml run --rm maintenance \
  create_statistics_snapshots --dry-run

# Snapshots fuer alle vorhandenen Monate nachholen (Ersteinrichtung)
docker compose -f docker-compose.prod.yml run --rm maintenance \
  create_statistics_snapshots --backfill

# Snapshot fuer bestimmten Monat
docker compose -f docker-compose.prod.yml run --rm maintenance \
  create_statistics_snapshots --year 2026 --month 3

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
# und separat als "Datei-Waise(n) übersprungen" gemeldet, NICHT geloescht: der
# data_json-Marker ist die einzige Bruecke zur verschluesselten Attachment-
# Zeile. (Die Regel gilt fuer Ereignisse, Personen und Faelle gleichermassen:
# Datei-Felder sind an allen drei Typen zulaessig, Refs #1738.)
# Diese Datei-Bereinigung laeuft ueber die Attachment-Loeschpfade bzw.
# cleanup_orphan_storage_files (nach dem Loeschen der Attachment-Zeile), nicht
# ueber diesen Command — die uebersprungene Zaehlung gilt also nicht als
# erledigte Datei-Minimierung.
docker compose -f docker-compose.prod.yml run --rm maintenance \
  purge_orphan_field_data --dry-run   # Vorschau
docker compose -f docker-compose.prod.yml run --rm maintenance \
  purge_orphan_field_data             # ausfuehren

# Historienzeilen mit Redaktionszeitpunkt, aber ohne Redaktionsvermerk heilen
# (Refs #2188, Bezug #2177). Einmalig nach dem Update, wenn Schluessel-
# rotation (reencrypt_fields) und Aufbewahrungslauf frueher gleichzeitig
# gelaufen sein koennen: solche Zeilen gelten als redigiert, tragen aber
# wieder verschluesselte Werte, und kein Aufbewahrungslauf fasst sie mehr an.
# Ohne Schalter ein Trockenlauf (zaehlt je Einrichtung, aendert nichts,
# Exit-Code 0 auch bei Funden); --apply heilt unter Zeilensperre und schreibt
# je Zeile einen Nachweis (EVENT_HISTORY_REDACTED, Kategorie
# eventhistory_repaired). Zeilen unter Legal Hold werden gezaehlt, aber
# zurueckgestellt. MUSS mit der Admin-DB-Rolle laufen (BYPASSRLS und
# Tabelleneigentuemer) — sonst fail-loud.
docker compose -f docker-compose.prod.yml run --rm maintenance \
  repair_eventhistory_redaction             # Trockenlauf
docker compose -f docker-compose.prod.yml run --rm maintenance \
  repair_eventhistory_redaction --apply     # heilen

# Dieselbe Pruefung fuer Ereignisse, Personen, Faelle und Berichtigungs-
# antraege (Refs #2188): sie findet data_json eines geloeschten Ereignisses,
# einer anonymisierten Person oder eines anonymisierten Falls sowie die vier
# Wertspalten der Berichtigungsantraege einer anonymisierten Person, die wieder
# alte Werte tragen (z. B. in einem wiederhergestellten Bestand).
# --target all prueft Person, Berichtigungsantrag, Fall, Ereignis und
# Historie in dieser Reihenfolge (einzeln: --target client|rectification|
# case|event|eventhistory; ohne --target nur die Historie). --apply leert
# data_json bzw. die Wertspalten des Antrags wie die regulaere Redaktion bzw.
# Anonymisierung, laesst Loesch-/Anonymisierungs-, Antrags- und
# Pruefzeitpunkt stehen und schreibt je Zeile einen Nachweis (DELETE,
# Kategorie event_repaired, client_repaired, case_repaired bzw.
# rectification_repaired). Die Verbindung MUSS jede gepruefte Tabelle
# besitzen, bei Antraegen auch core_client — sonst fail-loud vor dem ersten
# Lesen.
docker compose -f docker-compose.prod.yml run --rm maintenance \
  repair_eventhistory_redaction --target all            # Trockenlauf
docker compose -f docker-compose.prod.yml run --rm maintenance \
  repair_eventhistory_redaction --target all --apply    # heilen

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
#
# Zielverzeichnis: der Export laeuft im Container als appuser (UID 1000). Das
# Verzeichnis muss UID 1000 gehoeren, sonst endet der Lauf nach der bestandenen
# Rollenpruefung mit "Permission denied". Legt Docker ein fehlendes Host-
# Verzeichnis selbst an, gehoert es root und ist ebenfalls nicht beschreibbar.
# Die Datei gehoert danach UID 1000 (Modus 0644 im Verzeichnis 0700): Lesen und
# Entfernen als anderes Konto mit sudo.
sudo install -d -m 0700 -o 1000 -g 1000 ./exports
docker compose -f docker-compose.prod.yml run --rm \
  -v "$PWD/exports:/exports" maintenance export_facility \
  --facility "Beratungsstelle Nord" --output /exports/facility-export.json --indent 2

# Inventar der Berichtszuordnung (Refs #1848): listet fuer EINE Einrichtung
# auf, welcher Berichtsschluessel in welchem Berichts-Backend steckt (Typ- und
# Feld-Ebene) und welche Dokumentationstypen und Feldvorlagen ihn tragen —
# bzw. welche keinen tragen und damit in keinem Bericht erscheinen. Reine
# Auskunft, veraendert keine Daten. MUSS mit der Admin-DB-Rolle laufen
# (BYPASSRLS) — sonst fail-loud (§ 9 DB-Rollenmodell): unter der App-Rolle
# ohne Facility-Kontext saehe ein leeres/gefiltertes Inventar wie eine
# tadellos konfigurierte Einrichtung aus.
docker compose -f docker-compose.prod.yml run --rm maintenance \
  inventory_report_bindings \
  --facility "Beratungsstelle Nord"   # Textreport auf stdout
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
# --package und --no-default-package schließen sich aus.
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

Die mitgelieferten Compose-Dateien verwenden für jeden Dienst den Treiber `json-file` mit `max-size: "10m"` und `max-file: "5"`; damit belegt das Container-Log je Dienst höchstens ungefähr 50 MB. `docker compose logs` zeigt nur diesen aktuellen und rotierten Bestand, ältere Container-Logzeilen werden verworfen. Abweichende Grenzen lassen sich in `compose.override.yml` je Dienst mit einem eigenen `logging`-Block setzen; anschließend muss der betroffene Container mit `docker compose … up -d` neu angelegt werden.

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
Logger `security.csp`) und stehen (#1943)
in **beiden** Formaten — auch im Default `LOG_FORMAT=text`, dort als
`feld="wert"`-Paare hinter der Nachricht. Zwei Meldungstypen sind dabei
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
| `django.db.utils.OperationalError: failed to resolve host 'db'` bzw. `connection failed: connection to server at … failed: Connection refused` | DB-Container gestoppt bzw. noch nicht bereit | DB-Container pruefen, siehe [5.1](#51-db-verbindung-fehlgeschlagen) |
| `ImproperlyConfigured: ENCRYPTION_KEY oder ENCRYPTION_KEYS muss in Produktion gesetzt sein` | Schluessel fehlt in der `.env` | `.env` pruefen |
| `relation "core_*" does not exist` | Migrationen nicht gelaufen | Migrations-One-Shot aus [1.2](#12-deploy-schritte) Schritt 3 ausfuehren (nicht `migrate` im `web`-Container: dort fehlt die Owner-Rolle) |
| `OSError: write error` | Festplatte voll | Speicherplatz freigeben, siehe [5.5](#55-speicherplatz-voll) |
| `[CRITICAL] WORKER TIMEOUT` | Gunicorn-Worker-Timeout | Siehe [5.4](#54-gunicorn-timeout) |

---

## 5. Troubleshooting

### 5.1 DB-Verbindung fehlgeschlagen

**Symptom:** Der Web-Log zeigt `django.db.utils.OperationalError` — `failed to resolve host 'db'` bei gestopptem DB-Container, `connection failed: connection to server at … Connection refused`, solange PostgreSQL keine Verbindungen annimmt. Seiten und `/health/` antworten dann mit HTTP 500 (HTML-Fehlerseite, kein JSON), und der `web`-Container wird `unhealthy`.

```bash
# 1. DB-Container-Status
docker compose -f docker-compose.prod.yml ps db

# 2. DB-Healthcheck
docker inspect $(docker compose -f docker-compose.prod.yml ps -q db) | grep -A5 Health

# 3. Nimmt PostgreSQL Verbindungen an? (Exit 0 = ja; braucht keinen Rollennamen)
docker compose -f docker-compose.prod.yml exec -T db pg_isready

# 4. DB-Logs pruefen
docker compose -f docker-compose.prod.yml logs db --tail=50

# 5. DB-Container neu starten
docker compose -f docker-compose.prod.yml restart db

# 6. Warten bis healthy, dann Web neu starten
docker compose -f docker-compose.prod.yml restart web
```

### 5.2 Migration-Lock

**Symptom:** Der Migrations-One-Shot aus [1.2](#12-deploy-schritte) Schritt 3 haengt nach der Rollenpruefung, kein Fortschritt in der Ausgabe.

`docker-migrate.sh` nimmt vor `migrate` einen Session-Advisory-Lock (`pg_advisory_lock(1)`), damit nie zwei Migrationen parallel laufen. Der Lock gehoert der Datenbank-Sitzung des Jobs und faellt mit ihr weg. Ein zweiter Job wartet, bis der Halter fertig ist oder seine Sitzung endet. Der Entrypoint von `web` migriert nicht und nimmt den Lock nicht.

```bash
cd /opt/anlaufstelle

# 1. Laeuft der Halter noch? Ein laufender Migrations-One-Shot ist kein Defekt:
#    abwarten (lange RunPython-Schritte sind normal). Die Liste muss leer sein,
#    bevor irgendetwas beendet wird.
docker compose -f docker-compose.prod.yml ps --all --format '{{.Name}} {{.Status}}' maintenance

# 2. Halter und Wartende anzeigen (Rolle postgres ueber den lokalen Socket;
#    granted = t ist der Halter, f ein Wartender)
docker compose -f docker-compose.prod.yml exec -T db sh -c 'psql -U postgres -d "$POSTGRES_DB"' <<'SQL'
SELECT l.pid, l.granted, a.usename, a.state, left(a.query, 50) AS query
FROM pg_locks l JOIN pg_stat_activity a USING (pid)
WHERE l.locktype = 'advisory'
ORDER BY l.granted DESC, a.query_start;
SQL

# 3. Nur wenn kein Migrations-One-Shot mehr laeuft (Schritt 1 leer), die
#    verwaiste Halter-Sitzung beenden; <pid> ist die pid der Zeile mit granted = t.
#    pg_advisory_unlock(1) aus einer anderen Sitzung hilft nicht: es meldet
#    "you don't own a lock" und gibt nichts frei.
docker compose -f docker-compose.prod.yml exec -T db \
  sh -c 'psql -U postgres -d "$POSTGRES_DB" -tAc "SELECT pg_terminate_backend(<pid>);"'
```

Ein wartender Job laeuft danach von selbst weiter. Ist keiner mehr uebrig, den Migrations-One-Shot aus [1.2](#12-deploy-schritte) Schritt 3 erneut starten; ein Neustart von `web` loest keine Migration aus.

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

# 3. Konfiguration neu laden. Das liest nur das Caddyfile neu ein; ein Zertifikat
#    erneuert oder beantragt dieser Befehl nicht. Zertifikate und ACME-Konto
#    liegen im Volume caddy_data; Caddy beantragt und erneuert sie selbst.
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

# 4. web mit der neuen Umgebung neu erzeugen. `restart web` startet den vorhandenen
#    Container mit seiner alten Umgebung neu; neue GUNICORN_*-Werte aus der .env
#    wirken erst nach `up -d`, mit der vollstaendigen Dateiliste.
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d web
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
# Vollständige Compose-Dateiliste in fester Reihenfolge (admin-guide.md § 1).
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d redis web cron
```

**Fallback (unverändert): `DatabaseCache`.**
Ist `REDIS_URL` leer oder nicht gesetzt, nutzt `prod.py` den shared `DatabaseCache`. Die Cache-Tabelle `anlaufstelle_cache` wird per Migration (`0092`) angelegt und läuft im normalen One-Shot-Migrate-Job (`docker-migrate.sh`, siehe [1.2](#12-deploy-schritte)) automatisch mit hoch — **kein** separater `createcachetable`-Schritt nötig. Der Fallback ist funktional gleichwertig (ebenfalls prozessübergreifend, `RATELIMIT_USE_CACHE=default`), kostet aber pro Rate-Limit-Zähler einen DB-Roundtrip. **Redis ist hier also eine Performance-, keine Korrektheits-Entscheidung.**

Redis bewusst abschalten (z. B. sehr kleines Deployment, jedes MB RAM zählt):

```bash
# in .env — leer, nicht auskommentiert:
REDIS_URL=
```

```bash
# Vollständige Compose-Dateiliste in fester Reihenfolge (admin-guide.md § 1).
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d --scale redis=0
```

**Externes Redis** statt des mitgelieferten: `REDIS_URL` in der `.env` auf den eigenen Endpunkt zeigen lassen (`redis://:passwort@redis.internal:6379/1`) und dessen Credentials selbst in diese URL eintragen. Ein gesetztes `REDIS_PASSWORD` wirkt bei expliziter `REDIS_URL` nur noch auf den ungenutzten mitgelieferten Service. Der Compose-Wert ist nur ein Fallback für nicht gesetzte Variablen; der mitgelieferte Service kann dann wie oben auf 0 skaliert werden.

**Welches Backend läuft gerade?** Der `/health/`-Endpoint gibt es für interne/Token-Caller aus:

```bash
curl -sf -H "X-Health-Token: $HEALTH_DETAIL_TOKEN" https://$DOMAIN/health/ \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["cache"])'
# {'backend': 'django.core.cache.backends.redis.RedisCache', 'status': 'ok'}
```

Das ist kein Kosmetik-Feld: Ist `REDIS_URL` gesetzt, im Image aber kein `redis`-Paket vorhanden (typisch, wenn ein **älteres** `APP_VERSION`-Image mit dem neuen Compose-File gefahren wird), fällt `prod.py` absichtlich auf den `DatabaseCache` zurück und schreibt eine Warnung ins Container-Log — der Stack bleibt lauffähig, läuft aber langsamer. `cache.backend` macht diesen Zustand sichtbar; der Payload zeigt nie die `LOCATION` und damit auch kein eingebettetes Passwort. `cache.status = error` (Backend konfiguriert, aber nicht erreichbar) setzt den Gesamtstatus auf `degraded`, weil dann auch der Rate-Limit-Schutz wirkungslos ist.

Bei Redis-Verbindungsfehlern im vorgeschalteten Rate-Limit bleibt `/health/` als kontrollierte JSON-Antwort erreichbar: anonym nur `status=degraded`, mit gültigem Health-Token zusätzlich `cache.status=error` und Diagnosefelder. Die teuren Detailprüfungen bleiben hinter dem bestehenden Token-/Session-Gate. Nach Erholung des Backends greift wieder das normale Limit von 120 GET-Anfragen pro Minute und IP. Diese Ausnahme gilt ausschließlich für Health; die Fehlerbehandlung der Login-/Sudo-Limits wird nicht geändert.

**Kein separater `ratelimit`-Cache-Alias.** Die ursprüngliche Planung sah einen zweiten Alias auf Redis-DB `/2` vor. Umgesetzt ist er nicht: `django-ratelimit` hängt an `RATELIMIT_USE_CACHE`, und kein Code liest `caches["ratelimit"]` — der Alias wäre tote Konfiguration und müsste im `DatabaseCache`-Fallback zusätzlich eine zweite Cache-Tabelle (also eine weitere Migration) tragen. DB-Index `/2` bleibt für eine spätere Trennung frei.

### 5.5 Speicherplatz voll

```bash
# Docker-Speicherverbrauch
docker system df

# Groesste Volumes (Namen tragen den Projektpräfix, z. B. anlaufstelle_media)
docker system df -v | grep -E 'VOLUME|pgdata|media|clamav-db|caddy'

# Alte Images und Build-Cache bereinigen
docker system prune -f

# Alte Backup-Dateien pruefen (Backup-Verzeichnis ist nur fuer das Backup-Konto lesbar)
sudo du -sh /opt/anlaufstelle/backups/*/

# Belegung der Anhaenge je Einrichtung: MEDIA_ROOT liegt im benannten Volume media
# (/media/media im Container), nicht im Host-Dateisystem
docker compose -f docker-compose.prod.yml exec -T web sh -c 'du -sh /media/media/*/'
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
#    "ImproperlyConfigured" → Fehlende oder ungueltige Env-Vars in .env
#    "ENCRYPTION_KEY oder ENCRYPTION_KEYS muss in Produktion gesetzt sein"
#                         → Schluessel in .env fehlt
#    "OperationalError … Connection refused" bzw. "failed to resolve host 'db'"
#                         → DB noch nicht bereit bzw. gestoppt (siehe 5.1)

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
# 1. Check the current Caddy value
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

**Gegenmassnahme:** `docker-compose.prod.yml` mountet `/tmp` im `web`-Container bereits als **tmpfs** (RAM) — noetig, weil der Container `read_only` laeuft (Refs #1374). `FILE_UPLOAD_TEMP_DIR=/tmp` ist dort explizit im `environment:`-Block der Compose-Datei gesetzt und pinnt Djangos Upload-Puffer nachweislich auf dieses tmpfs, statt sich auf Pythons `TMPDIR`-Ermittlungsreihenfolge zu verlassen (ein spaeter versehentlich gesetztes `TMPDIR` wuerde den impliziten Fallback sonst unbemerkt von tmpfs weg auf ein persistentes Volume umlenken). Der Tempdatei-Inhalt existiert also nur fluechtig im RAM und ist beim Container-Neustart weg — kein Aufraeum-Cronjob noetig.


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
Initialpasswort ueber den Django-Admin (unten). 

Die Live-Dev- und
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

Bevorzugt das Backup-Skript: Es sichert verschlüsselt mit signiertem Manifest
und läuft mit der Admin-Rolle (BYPASSRLS), die der Dump braucht.

```bash
cd /opt/anlaufstelle
./scripts/ops/backup.sh
```

Nur wenn das Skript nicht zur Verfügung steht, ein schneller Dump ohne
Verschlüsselung. `pg_dump` arbeitet mit `row_security=off` und bricht unter der
App-Rolle (`POSTGRES_USER`: NOBYPASSRLS, kein Owner) an den Tabellen mit FORCE
ROW LEVEL SECURITY ab; gerade im Notfall gäbe es dann keinen Dump. Der Befehl
läuft deshalb als Bootstrap-Superuser `postgres` über den lokalen Socket im
`db`-Container (ohne Passwort, wie `backup.sh` und `restore.sh`). Der
Datenbankname kommt aus der Umgebung des Containers (`$POSTGRES_DB`); die `.env`
muss in der Host-Shell nicht geladen sein.

```bash
cd /opt/anlaufstelle
install -d -m 0700 backups/emergency
(umask 077; docker compose -f docker-compose.prod.yml exec -T db \
  sh -c 'pg_dump -U postgres -Fc "$POSTGRES_DB"' \
  > backups/emergency/notfall_$(date +%Y%m%d_%H%M%S).dump)

# Lesbarkeit des Dumps prüfen (Inhaltsverzeichnis, kein Restore)
docker compose -f docker-compose.prod.yml exec -T db pg_restore --list \
  < backups/emergency/notfall_<Zeitstempel>.dump | head
```

Der Dump ist **unverschlüsselt** und enthält Daten nach Art. 9 DSGVO. Deshalb
nicht in `/tmp` ablegen, sondern in einem nur für den ausführenden Benutzer
lesbaren Verzeichnis (`umask 077`, Verzeichnis `0700`), und nach dem Eingriff
verschlüsselt ablegen oder sicher löschen. `./scripts/ops/backup.sh` rotiert
nur `daily/`, `weekly/` und `monthly/`; das Unterverzeichnis `emergency/` bleibt
unberührt. Weicht `BACKUP_HOST_DIR` in der `.env` von `./backups` ab, das
Verzeichnis entsprechend anpassen.

### 6.3 Datenbank-Notfall-Zugriff

Rolle: Bootstrap-Superuser `postgres` über den lokalen Socket im `db`-Container
(ohne Passwort). **Nicht** die App-Rolle verwenden: Sie unterliegt Row Level
Security und sieht ohne gesetzten `app.current_facility_id`-Kontext in allen
Facility-Tabellen 0 Zeilen, ein `SELECT count(*)` meldet dann still `0` statt
des echten Bestands (Hintergrund und Diagnose: [§ 9.2](#92-diagnose-in-psql)).
Weil `postgres` RLS umgeht, nur lesend und gezielt arbeiten; Änderungen gehören
in die Anwendung oder in die Wartungskommandos.

```bash
# Interaktive psql-Shell
docker compose -f docker-compose.prod.yml exec db \
  sh -c 'psql -U postgres -d "$POSTGRES_DB"'

# Einzel-Query ausführen
docker compose -f docker-compose.prod.yml exec -T db \
  sh -c 'psql -U postgres -d "$POSTGRES_DB" -c "SELECT count(*) FROM core_event WHERE is_deleted = false;"'
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

Die Meldepflicht nach Art. 33/34 DSGVO trifft den **Verantwortlichen**, also den Traeger der Instanz — nicht die Software und nicht das Projekt. Der Traeger meldet Datenschutzverletzungen innerhalb **72 Stunden** an die zustaendige Aufsichtsbehoerde (Art. 33) und informiert betroffene Personen direkt, wenn ein hohes Risiko vorliegt (Art. 34). Die Software unterstuetzt das durch:

1. **Detection** — `python manage.py detect_breaches` laeuft stuendlich (Cron) und schreibt fuer jedes Finding einen `AuditLog.Action.SECURITY_VIOLATION`-Eintrag.
2. **Heuristiken** (alle konfigurierbar via Settings; Quelle: `src/core/services/compliance/breach_detection.py`, Defaults in `src/anlaufstelle/settings/base.py`). Je Art gilt ein **kurzes Fenster** (`BREACH_DETECTION_WINDOW_MINUTES`, Default 60 min) und, soweit unten genannt, ein **Langzeitfenster** gegen „low and slow“ (`BREACH_DETECTION_LONG_WINDOW_MINUTES`, Default 1440 min) mit hoeherer Schwelle (`*_LONG`). Je Subjekt entsteht hoechstens ein Finding je Art, das kurze Fenster hat Vorrang.

   | Art (`kind`) | Zaehlt | Kurz (Default) | Lang (Default) | Subjekt |
   |---|---|---|---|---|
   | `failed_login_burst` | fehlgeschlagene Logins | `BREACH_FAILED_LOGIN_THRESHOLD` (20) | `…_LONG` (60) | je Benutzer |
   | `mfa_failure_burst` (Refs #2033) | fehlgeschlagene Zweitfaktor-Pruefungen; `MFA_FAILED` (TOTP) und `WEBAUTHN_FAILED` (Passkey) zaehlen **gemeinsam**, sonst liesse sich die Schwelle durch Wechsel des Verfahrens unterlaufen | `BREACH_MFA_FAILURE_THRESHOLD` (10) | `…_LONG` (30) | je Benutzer |
   | `mass_export` | Exports | `BREACH_EXPORT_THRESHOLD` (10) | `…_LONG` (30) | je Benutzer |
   | `mass_delete` | Loeschungen (`DELETE`) | `BREACH_DELETE_THRESHOLD` (50) | `…_LONG` (150) | Einrichtung |
   | `mass_client_destruction` | Personen-Vernichtung (`CLIENT_SOFT_DELETED`, `CLIENT_ANONYMIZED`, `DELETION_APPROVED`), die `mass_delete` nicht sieht | `BREACH_CLIENT_DESTRUCTION_THRESHOLD` (20) | `…_LONG` (60) | Einrichtung |
   | `distributed_login_attack` | distinkte Quell-IPs gegen **einen** Account (Distributed-Bruteforce) | `BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD` (10) | — | je Benutzer |
   | `anonymous_login_burst` | fehlgeschlagene Logins gegen **unbekannte** Benutzernamen, je Quell-IP | `BREACH_ANON_LOGIN_IP_THRESHOLD` (20) | — | Quell-IP |
   | `anonymous_login_flood` | dasselbe, installationsweites Gesamtvolumen | `BREACH_ANON_LOGIN_TOTAL_THRESHOLD` (100) | — | Installation |

   Die MFA-Schwelle liegt bewusst unter der von `failed_login_burst`: wer am zweiten Faktor scheitert, hat den ersten bereits — die ernstere Angreifer-Position, und OTP-Fehler sind per Design selten.
3. **Optionaler Webhook** — `BREACH_NOTIFICATION_WEBHOOK_URL` env-Var; bekommt POST mit JSON-Payload (kind, count, threshold, audit_id) bei jedem neuen Finding.
4. **Deduplikation** — innerhalb von 24h wird derselbe Tatbestand nur einmal in `AuditLog` gemeldet: je Art und Benutzer, bei den anonymen Findings je Art und Quell-IP.
5. **Die Detection warnt, sie sperrt nicht** — jede Heuristik ist Monitor-only: ein Treffer schreibt eine `SECURITY_VIOLATION`-Zeile und feuert ggf. den Webhook, er entzieht **keinen** Zugang und aendert kein Passwort. Deshalb sind False Positives eingeplant und tolerierbar: Eine Nutzerin mit einer Tippfehler-Serie am Authenticator oder ein Passkey, den der Browser wiederholt ablehnt, kann `mfa_failure_burst` ausloesen (die 10er-Schwelle liegt bewusst niedrig) — die Folge ist ein pruefbarer Eintrag, nie ein Aussperr-Effekt. Das harte Aussperren bleibt dem davon unabhaengigen Account-Lockout (10 Fehl-**logins** in 15 min, `manage.py unlock <username>`) und den Ratelimits vorbehalten. Praktisch heisst das bei der Triage in § „Manueller Prozess": zuerst die Art des Findings lesen. Bei Findings mit Benutzer (`failed_login_burst`, `mfa_failure_burst`, `mass_export`, `distributed_login_attack`) pruefen, ob die Zeitreihe der Fehlversuche je Benutzer wie ein Mensch aussieht (wenige Minuten, eine Quell-IP, danach Erfolg) oder wie ein Automat (gleichmaessig ueber Stunden, kein Erfolg, ggf. mehrere IPs). Einrichtungsweite Findings (`mass_delete`, `mass_client_destruction`) tragen keinen Benutzer: dort die `AuditLog`-Eintraege der Einrichtung im Fenster nach Benutzer und Zielobjekten aufschluesseln. Die anonymen Findings (`anonymous_login_burst`, `anonymous_login_flood`) tragen weder Benutzer noch Einrichtung, nur die Quell-IP bzw. das Gesamtvolumen: Sie zeigen Credential-Stuffing oder Benutzernamen-Raten; solange kein Login gelingt, ist kein Konto betroffen — Quell-IPs am Reverse-Proxy sperren. Wer die Schwelle dauerhaft anhebt, verliert genau das Signal aus Szenario A7 (geduldiges Raten des zweiten Faktors mit gestohlener Passwort-Session) — Schwellen daher lieber pro Instanz nachjustieren als die Heuristik abschalten.

#### Manueller Prozess bei SECURITY_VIOLATION

1. **T+0** — `AuditLog`-Detail pruefen, betroffene Benutzer/Daten identifizieren und den Vorgang an die verantwortliche Stelle des Traegers weitergeben.
2. **T+24h** — Vorgang an den **Datenschutzbeauftragten des Traegers** (bzw. die dafuer benannte Datenschutzstelle) eskalieren, Risiko-Einschaetzung erstellen. Das Projekt ist dafuer nicht der Adressat: Eine Datenpanne der eigenen Instanz wird beim Traeger gemeldet, nicht ueber den Schwachstellenkanal des Software-Projekts.
3. **T+72h** — Meldung des Traegers an die zustaendige Aufsichtsbehoerde (z. B. Landesdatenschutzbeauftragte fuer oeffentliche Stellen, Landesaufsicht fuer nicht-oeffentliche Stellen; welche Behoerde zustaendig ist, haengt vom Traeger und vom Bundesland ab) + ggf. Betroffenenmeldung.
4. **Post-Incident** — Lessons-Learned + Anpassung der Heuristik-Schwellen.

Einrichtung: Der `cron`-Sidecar fuehrt `detect_breaches` ab Werk stuendlich aus ([§ 3.1](#31-uebersicht), [§ 3.2](#32-cron-sidecar-standard)); es ist **kein** zusaetzlicher Cron-Eintrag noetig, eine zweite Zeile liesse den Job doppelt laufen. Nur ohne Sidecar steht die Zeile in der Host-Crontab-Alternative aus [§ 3.3](#33-host-cron-backup--restliche-host-jobs). Einen Lauf sofort ausserhalb des Zeitplans anstossen:

```bash
docker compose -f docker-compose.prod.yml run --rm -T maintenance detect_breaches
```

### 6.5c Security Advisory — Meldung, Bewertung, Veroeffentlichung (Refs #1895)

Gegenstueck zu § 6.5b: dort geht es um eine **Datenschutzverletzung in einer betriebenen Instanz** (DSGVO Art. 33/34), hier um eine **Schwachstelle in der Software selbst**. Die oeffentliche Zusage dazu steht in [SECURITY.md](../SECURITY.md) (Meldewege, SLA, Support-Regel); dieser Abschnitt beschreibt den Ablauf dahinter.

**Vorbedingung:** „Private vulnerability reporting" muss auf `anlaufstelle/app` aktiviert sein (Repo → *Settings* → *Advanced Security* → *Private vulnerability reporting* → *Enable*, oder `gh api -X PUT repos/anlaufstelle/app/private-vulnerability-reporting`). Die Einstellung gibt es nur fuer **oeffentliche** Repositories. Solange es aus ist, laeuft der GHSA-Formular-Link in `SECURITY.md` ins Leere und **nur der E-Mail-Weg traegt** — Statuspruefung:

```bash
gh api repos/anlaufstelle/app/private-vulnerability-reporting   # {"enabled": true} erwartet
```

| # | Schritt | Wer | Zielwert |
|---|---------|-----|----------|
| 1 | **Eingang** — Meldung per GHSA-Formular oder E-Mail; Eingang bestaetigen, Reproduktionsschritte nachfordern | Maintainer ([MAINTAINERS.md](../MAINTAINERS.md)) | ≤ 5 Werktage |
| 2 | **Bewertung** — reproduzieren, CVSS-v3.1-Basis-Score, Einstufung kritisch/hoch/mittel/niedrig, Ergebnis an die meldende Person | Maintainer | ≤ 10 Werktage |
| 3 | **GHSA-Entwurf** anlegen (*Security* → *Advisories* → *New draft security advisory* auf `anlaufstelle/app`), betroffene + gepatchte Versionen eintragen, Credit nach Wunsch der meldenden Person; bei kritisch/hoch CVE-ID ueber GitHub anfordern | Maintainer | mit Schritt 2 |
| 4 | **Fix** — failing test first, Patch-Release der **aktuellen** Minor-Reihe (Support-Regel in `SECURITY.md`: keine Backports), Commit mit `sec:`-Prefix und Verweis aufs Advisory | Maintainer | kritisch/hoch: 14–30 Tage |
| 5 | **Veroeffentlichung** — Advisory publizieren (Patch + 7 Tage Schonfrist), `Security`-Eintrag im [CHANGELOG](../CHANGELOG.md) mit GHSA-/CVE-Nummer, Hall-of-Fame-Nennung | Maintainer | Patch + 7 Tage |
| 6 | **Kein Fix** — Ablehnung begruenden (Out-of-Scope-Liste in `SECURITY.md`), Vorgang gegenueber der meldenden Person sauber abschliessen | Maintainer | ≤ 10 Werktage |

**Vertretung:** Es ist keine Vertretung benannt; bei Ausfall der Hauptperson ruht der Vorgang (gleichlautend [SECURITY.md](../SECURITY.md), [MAINTAINERS.md](../MAINTAINERS.md)).

**Abgrenzung:** Ein GHSA beschreibt eine Schwachstelle **im Anlaufstelle-Code**. Fuer verwundbare Abhaengigkeiten gilt § 8.3 (`pip-audit`/Dependabot) — dort existiert das Upstream-Advisory bereits, ein eigenes GHSA waere Doppelmeldung. Ein eigenes Advisory ist nur faellig, wenn die Anwendung die Upstream-Luecke exponiert und Betreiber deshalb handeln muessen.

### 6.5a Maintenance-Mode (Refs #700)

Vor und waehrend Migrationen/Deploys den Stack auf 503 schalten, damit
User eine konsistente Custom-Page sehen statt Connection-Errors. Der Block
zeigt nur den Rahmen; die Update-Schritte (Zielstand waehlen, `cron` anhalten,
Migrations-One-Shot) stehen in [§ 1.2](#12-deploy-schritte).

```bash
# Vollständige Compose-Dateiliste in fester Reihenfolge (admin-guide.md § 1).
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)

# Aktivieren (File-Flag im persistenten Web-Volume anlegen)
docker compose "${compose_files[@]}" exec -T web touch /media/anlaufstelle.maintenance

# Update ausfuehren (Schritte 0 bis 4 aus § 1.2): cron anhalten, Images ziehen,
# Migrations-One-Shot, Stack neu erzeugen. Ohne den One-Shot laeuft der neue Code
# gegen das alte Schema.
docker compose "${compose_files[@]}" stop cron
docker compose "${compose_files[@]}" pull web cron maintenance
docker compose "${compose_files[@]}" run --rm \
    --entrypoint=/app/docker-migrate.sh maintenance
docker compose "${compose_files[@]}" up -d web cron caddy

# Deaktivieren (dieselbe File-Flag im neu angelegten Web-Container entfernen)
docker compose "${compose_files[@]}" exec -T web rm -f /media/anlaufstelle.maintenance
```

Der Produktions-Stack setzt `MAINTENANCE_FLAG_FILE` ohne weitere Konfiguration auf `/media/anlaufstelle.maintenance`. Der Pfad liegt im benannten `media`-Volume, ist für `appuser` beschreibbar und übersteht deshalb das Neuanlegen von `web`. Ein bereits in `.env` gesetzter Wert überschreibt den Standard weiterhin, muss aber ein beschreibbarer, persistenter **Container-Pfad** sein. Alternativ schaltet eine Systemadministration den Modus unter `/system/maintenance/` ein oder aus; die Seite schreibt dieselbe Datei.


Whitelist:

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

#### Off-Site-Aufbewahrung

Die Einrichtung legt am Ziel eine **Höchstfrist von 12 Monaten ab Erstellung des Sicherungsstands** fest (eine kürzere Frist ist möglich) und dokumentiert die konfigurierte Frist im **Löschkonzept und in den Informationspflichten**. Gelöschte Daten können bis zum tatsächlichen Ablauf der Sicherungen darin überleben. `backup.sh` setzt diese Höchstfrist nicht durch: Ohne wirksame Zielregel bleiben Off-Site-Kopien unbegrenzt erhalten.

Die Lifecycle-Regel wird **am Ziel** eingerichtet und kontrolliert:

- **S3 Lifecycle:** Ablauf für alle Backup-Präfixe (`daily/`, `weekly/`, `monthly/`) konfigurieren; bei Versionierung auch nicht aktuelle Objektversionen und Löschmarker berücksichtigen. Object-Lock-Fristen müssen mit der Höchstfrist vereinbar sein.
- **Storage-Box-Snapshots:** Snapshot-Aufbewahrung und Ablauf der zugrunde liegenden Dateien getrennt konfigurieren; abgelaufene Dateien dürfen nicht über länger erhaltene Snapshots die Höchstfrist überschreiten.
- **Separater Prune-Zugang auf einem anderen Host:** Für Ziele ohne geeigneten Lifecycle löscht ein unabhängig betriebener Job die abgelaufenen vollständigen Sätze. Seine Löschzugangsdaten liegen nicht auf dem App-Host.

Die Regel umfasst Dump, Medienarchiv, Manifest, HMAC-Sidecars und Audit-Anker sowie Versionen, Snapshots und weitere Kopien. Den tatsächlichen Ablauf am Ziel nachweisen; ein erfolgreicher Upload ist kein Löschbeleg. Der App-Host bekommt keine Löschrechte für die Off-Site-Sätze: Bei einer Ransomware-Kompromittierung sollen weder die Wiederherstellungspunkte noch der Anker-Verlauf vom Angreifer entfernt werden können. Object Lock ergänzt diese Trennung, ersetzt aber keine Ablaufregel.

**Bezug zur lokalen Rotation:** `backup.sh` löscht lokal ab 8/29/91 vollen Tagen (täglich/wöchentlich/monatlich, `find -mtime +7/+28/+90`) erst beim nächsten erfolgreichen Rotationslauf. Schon ein fehlgeschlagener Off-Site-Abgleich setzt diese Rotation aus. Diese Altersgrenzen sind keine garantierten Höchstfristen und bewirken keine Löschung am Off-Site-Ziel; die dortige 12-Monats-Grenze muss die Zielkonfiguration unabhängig durchsetzen.

### 6.6 Backup-Restore-Drill (Refs #720, #739)

Verifiziert, dass das aktuellste Backup vollstaendig wiederherstellbar ist und die Verteidigungslinien (RLS, AuditLog-Immutability-Trigger) erhalten bleiben. Die konkrete quartalsweise Host-Cron-Empfehlung steht unten.

Das Skript deckt seit OPS-05 (Refs #1336) **beide** ausgelieferten Backup-Formate ab. Die Wahl erfolgt per Flag `--dev`/`--prod` oder `$DRILL_FORMAT`; ohne Angabe wird das Format anhand der vorhandenen Backup-Dateien auto-erkannt (Default `dev`, rueckwaerts-kompatibel — der bestehende dev-Timer laeuft unveraendert). Liegen **beide** Formate gleichzeitig vor, bricht der Drill ab und verlangt eine explizite Wahl:

- **`--prod`** — `scripts/ops/backup.sh`, der an Self-Hoster ausgelieferte kanonische Pfad: plain SQL + `gzip`, HMAC-Sidecars, Zeilenzahl-Manifest und zeitgleicher HWM → `psql`, Quelle `$BACKUP_HOST_DIR/daily/anlaufstelle_*.sql.gz.enc` (Default `<Repo>/backups/daily`), Stack `docker-compose.prod.yml`. Manifest und HMAC-Sidecars werden VOR dem Entschluesseln geprueft.

Beide Formate spielen als Postgres-Superuser (`postgres`, via Local-Socket-Trust im db-Container) ein — noetig fuer `CREATE DATABASE`, bypassed zugleich RLS/FORCE-RLS beim Restore.

**Kompatibilitaet Backup-Format × Restore-Pfad** — die beiden Backup-Welten sind *nicht* austauschbar; ein Drill gegen das jeweils fremde Format schlaegt fehl. Der Drill waehlt aber pro Format den passenden Pfad:

| Backup-Quelle | Dump-Format | Verschluesselung | Datei(en) | Restore-Tool | Restore-Drill |
|---|---|---|---|---|---|
| `scripts/ops/backup.sh` (Prod-Schema) | plain SQL + gzip | AES-256-CBC **+ HMAC-SHA256**-Sidecars (Refs #1024) | DB + Medien samt Sidecars, `*.manifest` samt Sidecar, verpflichtender zeitgleicher HWM | `psql` (`scripts/ops/restore.sh`) | ✓ `restore-drill.sh --prod --timestamp <Zeitstempel>` (Manifest-/HMAC-Check vor Decrypt); `backup.sh --verify` bleibt leichtgewichtige Alternative |


Warum kein Cross-Restore: Der `--dev`-Zweig sucht per Glob `dump-*.pgc.enc` und pipet in `pg_restore` — ein plain-SQL-Backup (`*.sql.gz.enc`) wird so gar nicht gefunden und ist auch kein gueltiges `pg_restore`-Archiv. Der `--prod`-Zweig sucht `anlaufstelle_*.sql.gz.enc`, prueft den `.hmac`-Sidecar und spielt via `gunzip | psql` ein — ein `dump-*.pgc.enc` traegt keinen Sidecar (`backup_verify_hmac` bricht ab) und ist Binaerformat. Ein Lauf gegen das jeweils fremde Format schlaegt also fehl. Wer keinen vollen Drill fahren will, nutzt fuer den Prod-Pfad `scripts/ops/backup.sh --verify` (Satzauswahl ueber das signierte Manifest, Satzbindung wie beim Restore, HMAC, Probe-Restore des Dumps in eine temporaere Datenbank und ein Leer-Guard ueber vier RLS-Tabellen: `core_client`, `core_event`, `core_workitem` und `core_auditlog` duerfen im Restore nicht leer sein, wenn die Live-DB Zeilen hat). Das ist **kein** Zeilenabgleich gegen Manifest oder Live-DB; die Ausgabe „Verification summary“ nennt am Ende, was geprueft wurde und was nicht. Den exakten Abgleich der sechs Manifesttabellen liefert nur der Drill. Der Probeimport nutzt `psql -v ON_ERROR_STOP=1 --single-transaction`: auch ein später SQL-Fehler beendet die Prüfung mit Exit ungleich 0. Vorbereitung, beide Guard-Abfragen, Medienprüfung und Cleanup der temporären Verify-Datenbank müssen ebenfalls gelingen; erst danach erscheint die Abschlusszusammenfassung. Bei fehlgeschlagenem Cleanup nennt die Ausgabe die verbleibende Verify-Datenbank. Fehlt das Manifest oder gehoert das Medienarchiv zu einem anderen Satz, endet der Lauf mit Exit != 0.

**Manifest ist Pflicht:** Ohne das signierte Manifest gibt es keine Satzbindung
(Zeitstempel, Dump-/Medien-Hashes) und keinen Zeilenzahl-Abgleich; `restore.sh`
bricht dann vor jeder Aenderung ab („the mandatory manifest of the backup set is
missing“). Es gibt keine Option, diese Pruefung zu umgehen. `backup.sh --verify`
und `restore-drill.sh --prod` verlangen das Manifest ebenso. Fehlt es, den
vollstaendigen Satz beschaffen (Dump, Medienarchiv und Manifest, jeweils mit
`.hmac`-Sidecar; [Disaster-Recovery §5](disaster-recovery.md#5-off-site-backup-beschaffen)).

`backup.sh` legt die Dateien mit `0600` an (nur das Manifest `0640`, Verzeichnisse `0750`, kein Recht fuer Andere); sie gehoeren dem Konto, unter dem es laeuft, im empfohlenen Setup root — der Drill liest sie daher **als root**. Ohne Flag erkennt das Skript das Format automatisch; explizit per `--dev`/`--prod` (oder `DRILL_FORMAT=dev|prod`). Der Prod-Zweig liest `BACKUP_HOST_DIR` ausschließlich aus der `.env` im Compose-Verzeichnis; ein gleichnamiger Wert im `sudo env`-Praefix wird beim Laden der Datei ueberschrieben und ist daher kein Override. Liegt die Installation nicht unter dem Default `/opt/anlaufstelle`, muss `COMPOSE_DIR` auf das Verzeichnis mit `.env` und `docker-compose.prod.yml` zeigen:

```bash
sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh           # Format auto-erkennen
sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh --prod --timestamp 2026-09-03_020000  # Prod-Satz explizit waehlen
sudo env BACKUP_TIMESTAMP=2026-09-03_020000 bash /opt/anlaufstelle/scripts/ops/restore-drill.sh --prod  # Gleichwertige Satzwahl per Umgebungsvariable
sudo env COMPOSE_DIR=/srv/anlaufstelle bash /srv/anlaufstelle/scripts/ops/restore-drill.sh --prod  # Installation ausserhalb von /opt/anlaufstelle
sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh --dev     # Dev-Format erzwingen (dump-*.pgc.enc)
```

`--timestamp` und `BACKUP_TIMESTAMP` sind nur an das Prod-Format gebunden erlaubt; der Wert muss dem Format `JJJJ-MM-TT_HHMMSS` entsprechen.

| Schritt | Pruefung |
|---|---|
| 0 | Backup im format-spezifischen Verzeichnis finden; bei `--prod --timestamp <Zeitstempel>` oder `--prod` mit `BACKUP_TIMESTAMP=<Zeitstempel>` exakt diesen Satz waehlen, sonst den neuesten. Das zeitgleiche Manifest samt HMAC authentifizieren, Satzkennung sowie DB-/Medien-/HWM-Hashes pruefen und die Referenzzahlen fuer sechs Tabellen laden. Ohne Manifest bricht der Prod-Drill ab. Enthält das authentifizierte Manifest einen `media_sha256`, bricht der Drill bei fehlendem Medienarchiv, fehlendem Sidecar oder falschem Hash bereits vor Anlage der Wegwerf-DB ab; `restore.sh` bricht in denselben Fällen vor dem Import ab. |
| 1 | Frische Wegwerf-DB (`anlaufstelle_drill_<pid>`) anlegen |
| 2 | Backup decrypten + einspielen — `--prod` prueft zuerst den HMAC-Sidecar, dann `gunzip \| psql -v ON_ERROR_STOP=1 --single-transaction` (plain SQL); `--dev` `pg_restore` (custom format) |
| 2b | Bei `--prod`, auch mit dem Compose-Default: nach Schritt 3b die zum gewaehlten DB-Backup-Zeitstempel gehoerende `audit_hwm-<Zeitstempel>.json` per Stream in den gehaerteten Drill-Container schreiben, die HMAC-Signatur laden und die restaurierte Kette prüfen; fehlend, abweichend oder manipuliert → `FAIL`, die weiteren Restore-Pruefungen laufen trotzdem weiter. Die Bezeichnung 2b bleibt für das Prüfblatt stabil. Im dev-Format wird der Schritt als `SKIP` geführt; der Drill endet dann mit **Exit-Code 2** (bestanden mit Lücken) statt mit 0 ([§6.7](#67-audit-high-water-mark-sichern-und-pruefen-refs-2022)) |
| 3 | Stichproben pro Manifesttabelle (`core_facility`, `core_client`, `core_event`, `core_workitem`, `core_auditlog`, `core_attachment`) — Counts abfragbar? |
| 3b | Im Prod-Format exakter Zeilenabgleich dieser sechs Tabellen zwischen Wegwerf-DB und authentifiziertem Manifest; jede Abweichung ist `FAIL`. Nur der Dev-Zweig vergleicht weiterhin exakt gegen die Live-DB. Dieser Schritt läuft vor der schreibenden Audit-Kettenprüfung und dem Compliance-Marker. |
| 4 | RLS-Policy-Check — `SELECT COUNT(*) FROM pg_class WHERE relname LIKE 'core_%' AND relrowsecurity = true` muss `>= 18` ergeben (konservativer Mindestwert im Skript; tatsaechliche Anzahl liegt hoeher, siehe Abschnitt 9) |
| 5 | `auditlog_immutable`-Trigger existiert UND blockt Raw UPDATE. Sind Audit-Soll im Manifest und Ist in `core_auditlog` beide 0, ist die Prüfung `NICHT PRUEFBAR`: Das belegt weder einen defekten Trigger noch dessen Funktion. Der Drill bleibt `OFFEN`, endet mit Exit-Code 1 und führt Schritt 7 nicht aus. |
| 6 | Nur `--prod`: Zum gewählten DB-Backup gehörendes Medienarchiv per HMAC prüfen, in einem Durchlauf in ein Host-Tempverzeichnis probeentpacken und das dabei erzeugte Tar-Listing mit der Probeentpackung vergleichen; Ausgabe `Medien: N Dateien, M Bytes, HMAC ok`. Vorher müssen dort ungefähr Archivgröße × 2 frei sein; `TMPDIR=/pfad/mit/platz` lenkt den Schritt auf ein anderes Dateisystem. Ein fehlendes passendes Archiv ist nur dann ein zulässiger DB-only-Fall mit `INFO`, wenn das authentifizierte Manifest keinen Medienhash enthält. Tempverzeichnis und Wegwerf-DB werden via Trap aufgeräumt. |
| 7 | **Bei vollem Erfolg (alle Schritte inkl. 3b und 6):** `mark_restore_verified` im web-Container → Compliance-Marker |

Output: ein `OK`, `FAIL`, `SKIP` oder `NICHT PRUEFBAR` pro Schritt. Exit-Codes: **0** = alle Pflichtschritte ausgefuehrt und gruen; **1** = mindestens ein `FAIL` oder `NICHT PRUEFBAR`; **2** = alle ausgefuehrten Schritte gruen, aber mindestens ein Pflichtschritt uebersprungen (Abschlusszeile `DRILL MIT LUECKEN … kein vollstaendiger Nachweis`); ein solcher Lauf schreibt **keinen** Compliance-Nachweis — denselben Code nutzt das Skript fuer Aufruffehler (unbekannte Option, ungueltiger Zeitstempel). Nur Exit-Code 0 ist ein vollstaendiger Nachweis; die Alarmregel `Exit != 0` deckt damit auch den fehlenden Ankernachweis ab (Refs #2316). Bei `FAIL` sofort auf Backup-Integritaet pruefen — Trigger-Check fehlgeschlagen (Schritt 5) ist kritisch, weil die AuditLog-Immutability dann nach Restore nicht mehr greift.

> **Benigne Ausnahme beim Restore (Schritt 2):** Das Dump enthält ein `REFRESH MATERIALIZED VIEW core_statistics_event_flat`, das beim Dev-Restore an `FORCE ROW LEVEL SECURITY` auf `core_event` scheitern kann (`pg_restore: errors ignored on restore: 1`). Das betrifft nur den Statistik-Cache. Der `--dev`-Drill toleriert ausschließlich diesen Fall. Im Prod-Zweig beendet `psql -v ON_ERROR_STOP=1 --single-transaction` jeden SQL-Fehler. Vollständigkeit belegt dort zusätzlich der exakte Manifestabgleich in Schritt 3b.

**Compliance-Marker (Refs #919):** Schritt 7 schreibt bei Erfolg automatisch einen `RESTORE_VERIFIED`-AuditLog-Eintrag (`manage.py mark_restore_verified`), den das Compliance-Dashboard (`/system/compliance/`) als Alter-Indikator nutzt:

- ≤ 90 Tage → `ok`
- ≤ 180 Tage → `warning`
- älter → `critical` (DSGVO Art. 32 Abs. 1 lit. c verletzt — Wiederherstellbarkeit nicht mehr nachgewiesen).

Ohne je gelaufenen Drill bleibt das Dashboard auf `unknown`. Manuell nachziehen (z.B. nach einem dokumentierten Out-of-Band-Restore) geht weiterhin:

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance \
    mark_restore_verified --note "Out-of-Band-Restore $(date +%Y-%m-%d)"
```

**Host-Cron-Vorschlag (keine automatische Installation):** Die Backup-Dateien
sind `0600` (nur das Manifest `0640`, kein Recht fuer Andere) und gehoeren dem Backup-Konto (empfohlen: root); den Einzeiler
deshalb in der **Root-Crontab des Hosts** eintragen, nicht im `cron`-Sidecar und nicht in einer unprivilegierten
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
    sh -c 'echo "drill-$(date +%s)" > /media/media/.restore-drill'

# 2. Stack neu erzeugen (Containers loeschen — Volume bleibt)
docker compose -f docker-compose.prod.yml down
# Vollständige Compose-Dateiliste in fester Reihenfolge (admin-guide.md § 1).
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)
docker compose "${compose_files[@]}" up -d

# 3. Sentinel ueberprueft Volume-Persistenz
docker compose -f docker-compose.prod.yml exec web \
    cat /media/media/.restore-drill
# -> erwarteter Output: drill-<timestamp>
```

Sentinel danach loeschen: `rm /media/media/.restore-drill` im Container.

---

### 6.7 Audit-High-Water-Mark sichern und pruefen (Refs #2022)

Die Audit-Kettenprüfung erkennt einen fehlenden zuvor verankerten Tail nur,
wenn der Anker außerhalb der Datenbank erhalten bleibt. Prod, Staging, Dev und
Demo verwenden dafür das eigene Named Volume `audit-anchor`:

```dotenv
DJANGO_AUDIT_HWM_PATH=/data/audit_hwm.json
```

Dies ist auch bei leerer oder fehlender Variable der Compose-Default. `web`
mountet das Volume unter `/data` **nur lesend**, `maintenance` und
gegebenenfalls `cron` **beschreibbar**. Das Medienvolume unter `/media` bleibt
separat; eigene Ankerpfade müssen innerhalb des Anker-Volumes liegen. Das Image
bereitet den Mount-Punkt mit UID/GID 1000 vor. Ein neuer Stack legt das Volume
beim ersten Start an; vor dem ersten Backup den Anker wie unten initialisieren.

**Wechsel einer bestehenden Instanz:** Vor der Umstellung alle Anker schreibenden
Jobs anhalten und die bisherige signierte Datei separat sichern. Mit dem neuen
Image und Compose-Vertrag das Volume anlegen, die Datei im
`maintenance`-Dienst unter UID/GID 1000 in eine temporäre Datei im neuen
Ankerverzeichnis übernehmen, mit `HighWaterMarkStore.load()` prüfen und erst
danach per Rename als `audit_hwm.json` aktivieren. Größe und SHA-256 mit der
Quelle vergleichen. In `.env` `DJANGO_AUDIT_HWM_PATH=/data/audit_hwm.json` sowie
`MAINTENANCE_FLAG_FILE=/media/anlaufstelle.maintenance` setzen. Für Medienwurzeln
unter dem bisherigen Mount `/data/…` nur das Präfix durch `/media/…` ersetzen
und den Restpfad erhalten: Der bisherige Standard `/data/media` wird zu
`MEDIA_ROOT=/media/media`; eine individuelle Wurzel `/data/vault` wird zu
`MEDIA_ROOT=/media/vault`. Falls `MEDIA_ROOT_CONTAINER` gesetzt ist, ebenfalls
auf dasselbe tatsächliche Verzeichnis umstellen. Vor dem Upgrade einen
Bestandsanhang öffnen und nach dem Upgrade prüfen, dass derselbe Bestandsanhang
lädt. Das bestehende Medienvolume wird unter `/media` eingehängt, seine Inhalte bleiben erhalten; der bisherige Standardanker ist dort als `/media/audit_hwm.json` erreichbar. Ein vorhandenes Wartungsflag liegt nach dem Umhängen ebenfalls unter `/media`. Die Container neu erstellen
und erst nach erfolgreicher Prüfung Jobs freigeben. Kein neuer Erstlauf darf
einen vorhandenen Anker ersetzen; das gesicherte Original behalten. Dies ist
ein Betreiber-Schritt, keine Datenbankmigration.

**Vertrauensgrenze:** Der ro-Mount verhindert Schreiben durch den Webprozess
auf dieses Volume. Er schützt nicht gegen Host-/Docker-Zugriff oder die
Wartungs-/Owner-Rolle; `web` besitzt weiterhin den Audit-HMAC-Schlüssel. Die
Karte „Letztes Backup“ prüft die Ankerkopie neben dem Backupmanifest auf Existenz
und eine Dateigröße größer null, **nicht** den laufenden `AUDIT_HWM_PATH` und nicht die
Signatur. Ein ro-Mount allein beweist keine unveränderte Historie.

Den ersten Anker sofort nach der Einrichtung und **vor dem ersten Backup**
erzeugen; nicht bis zum naechsten Sidecar-Termin warten:

```bash
docker compose -f docker-compose.prod.yml run --rm -T maintenance verify_audit_chain
```

Nur ein vollstaendig gruener Lauf schreibt den Anker. Bei einem `FAIL` zuerst die
Audit-Kette klaeren und kein Backup als vollstaendig verankert behandeln.

Ein gruener `verify_audit_chain`-Lauf schreibt dort Zaehler und letzten Hash je Kette. Die Datei ist mit `DJANGO_AUDIT_HASH_KEY` HMAC-signiert und enthält Zählstände, Hash-Anker und gegebenenfalls eingegebene Begründungen für stillgelegte Ketten. `scripts/ops/backup.sh` exportiert sie per `docker compose -f docker-compose.prod.yml exec -T cron cat …` als `backups/daily/audit_hwm-<Zeitstempel>.json`, rotiert sie mit DB-/Medienbackups und nimmt sie in alle drei Off-Site-Wege (rclone, S3, SCP) auf. Fehlt die Datei, ist sie leer oder kann `cron` sie nicht lesen, bricht das Skript mit Exit-Code 1 ab, bevor es den Satz veröffentlicht. Auch ohne Hostvariable verwendet es den Compose-Default.

`scripts/ops/restore-drill.sh --prod` verwendet denselben Default und streamt
die Ankerkopie des gewählten Backup-Zeitstempels nach `/tmp` des
Wartungscontainers. `HighWaterMarkStore.load()` prüft ihre Signatur; danach
prüft `verify_audit_chain --require-anchor` die restaurierte Kette. Fehlender
oder ungültiger Anker ergibt `FAIL`, Exit-Code 1 und keinen Compliance-Nachweis.
Die übrigen Prüfungen laufen weiter. Das dev-Format führt keinen Anker mit und
bleibt ein Lauf mit Lücken (Exit-Code 2). `restore.sh` verwendet ebenfalls den
Default; auf einem Host ohne eigenen Pfad erlaubt allein der ausdrücklich
gewählte Schalter `--skip-audit-anchor` einen Restore ohne Ankerprüfung und
-import. Ein solcher Lauf belegt keine verankerte Audit-Kette.


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
gegen einen ClamAV-Daemon gescannt (Issue #524).
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
CLAMAV_TIMEOUT=15          # Sekunden; MUSS < GUNICORN_TIMEOUT (Refs #1283)
```

In Entwicklung/Test ist der Scan deaktiviert (`CLAMAV_ENABLED=false`), damit
keine ClamAV-Instanz erforderlich ist.

### 7.2 Fail-closed

Bei aktivem Scan und nicht erreichbarem Daemon wird jeder Upload mit einer
`ValidationError` abgewiesen und eine `AuditLog`-Zeile mit
`Action.SECURITY_VIOLATION` (`reason=virus_scanner_unavailable`) geschrieben.
Funde werden mit `reason=virus_detected` und der gemeldeten Signatur geloggt.

### 7.3 Healthcheck

`GET /health/` liefert Token- bzw. internen Aufrufern zusätzlich `"virus_scanner": "connected"|"unavailable"|"disabled"`
sowie den Alias `"clamav": "ok"|"error"|"disabled"` (Refs #798).
Anonyme Aufrufer sehen nur `status`; bei aktivem Scanner und unerreichbarem
Daemon wird der Gesamtstatus auf `"degraded"` gesetzt, der HTTP-Status bleibt 200 (die harte Sperre erfolgt
beim Upload, nicht am Healthcheck). Der Container-Healthcheck im Dockerfile
liest den JSON-`status` und meldet bei `degraded` ungesund. Der Produktions-Compose
ersetzt diesen Image-Check: Nur `error` beziehungsweise HTTP 503 macht `web`
ungesund, damit weder `caddy` (`service_healthy`) noch `initial-setup.sh` an einer
nicht-kritischen Störung scheitern. `degraded` bleibt HTTP 200 und muss über den
Token-Detailpfad, das Compliance-Dashboard und externes Monitoring überwacht werden
(siehe [Monitoring-Guide](monitoring-guide.md)).

### 7.4 Datenbank-Updates (Signaturen)

Das benannte Volume `clamav-db` persistiert den Signaturstand. `freshclam` im
`clamav`-Container lädt Signatur-Updates per DNS/HTTPS und braucht dafür
ausgehenden Netzzugang. `docker-compose.prod.yml` hängt `clamav` deshalb an
zwei Netze: das interne Netz `internal` (Verbindung mit `web`; `internal: true`,
ohne Zugang nach außen) und das separate Netz `clamav-egress` mit Zugang nach
außen. Kein weiterer Dienst ist an `clamav-egress` angeschlossen. Läuft der
Stack hinter einer Egress-Filterung, müssen die ClamAV-Mirror-Domains
(`database.clamav.net`) von der Firewall des Hosts freigegeben sein; ohne
Zugang friert der Signaturstand ein.


Logs und Stand prüfen:

```bash
docker compose -f docker-compose.prod.yml exec clamav tail -n 30 /var/log/clamav/freshclam.log
docker compose -f docker-compose.prod.yml exec clamav clamdscan --version
```

Ist das Signaturalter während des Erstlaufs noch nicht ermittelbar, bleibt der
Compliance-Status `unknown` statt eine Warnung auszulösen. Der Check zeigt den
ersten Befehl oben als Handlungshinweis. Bleibt `unknown` nach dem Anlauf
bestehen, den Befehl ausführen und das Protokoll auf DNS-, Download- oder
Mirror-Fehler prüfen; eine als veraltet gemeldete Signatur weist auf einen
blockierten Egress hin.

### 7.5 Manueller Check

```bash
# Daemon erreichbar?
docker compose -f docker-compose.prod.yml exec web \
  python manage.py shell -c "from core.services.file_vault.virus_scan import ping; print(ping())"

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
#    eintragen — mit offener Range, z.B. "Django>=6.1.1,<6.2".

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

**Dauerhaftes Release-Artefakt (Refs #1895):** Das CI-Artefakt verfaellt nach 90 Tagen; der Nachweis, welche Komponenten ein Release enthaelt, soll aber dauerhaft moeglich bleiben. Der Release-Build erzeugt deshalb zusaetzlich eine **dauerhafte** SBOM `sbom-vX.Y.Z.json` (CycloneDX 1.6) aus den Lock-Files des gebauten Trees — offline, ohne Netzabruf und byte-identisch bei Wiederholung. Umfang: alle Python-Distributionen aus `requirements.txt` (direkt **und** transitiv) plus alle npm-Pakete aus `package-lock.json`. Die vier vendorten Frontend-Libs (htmx, Alpine-CSP, Dexie, Chart.js) sind darin als `required` gefuehrt, obwohl sie im Lockfile als `dev` stehen — sie werden ausgeliefert (`src/static/js/`) und laufen im Browser der Nutzerinnen. Build-/Testwerkzeuge bleiben `optional`. Der Maintainer haengt die Datei beim Anlegen des GitHub-Release als **Release-Asset** an die Release-Page von [`anlaufstelle/app`](https://github.com/anlaufstelle/app/releases). Betreiber laden sie dort herunter; fehlt das Asset an einem Release, ist die SBOM ueber den Kontakt in [SECURITY.md](../SECURITY.md) erhaeltlich.

```bash
# Release-SBOM der installierten Version herunterladen
gh release download vX.Y.Z -R anlaufstelle/app --pattern 'sbom-v*.json' --dir ./sbom-release
```

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


### 8.5 Troubleshooting

| Problem | Ursache | Lösung |
|---------|---------|--------|
| `deps-check` schlägt fehl, obwohl nichts geändert wurde | Unterschiedliche pip-tools-Version zwischen Dev und CI | `pip install --upgrade pip-tools` und `make deps-lock` neu ausführen |
| `pip-compile` findet keine passende Version | Zu strenge Range-Constraint | Range in `.in` lockern, z.B. `>=X,<Y+1` |
| Transitive Dependency zieht vulnerable Version | Direkte Dependency fixiert alte Version | Direkte Dependency selbst einpflegen und anheben |

---

## 9. Row Level Security (RLS)

PostgreSQL-RLS ist auf allen facility-gescopten Tabellen (derzeit
**29**, per Test ableitungsbasiert erzwungen —
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
eigentuemer (`FORCE ROW LEVEL SECURITY`). Ausgenommen sind nur Superuser-
und `BYPASSRLS`-Rollen (so ist die Admin-Rolle angelegt) sowie Sessions mit
gesetztem `app.is_super_admin` (OR-Zweig der Policies); der Django-DB-User der
Laufzeit darf deshalb in Produktion weder Superuser sein noch `BYPASSRLS` tragen.

**Direkte statt Join-Policy auf `core_attachment` (Refs
#1738,
[ADR-039](adr/039-owner-generische-dateianhaenge.md)):** Anhang-Zeilen
tragen als `core_attachment` (Migration `0131`) eine eigene `facility_id` und die
uebliche **direkte** Policy
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
Produktions-DB-User versehentlich Superuser wird oder `BYPASSRLS` erhaelt,
schlaegt der Cross-Tenant-Test in CI **nicht** fehl (er nutzt die explizite
Test-Rolle); das fangen `check_db_roles` und der Health-Endpoint (naechster
Absatz) in Prod ab.

**Dauerueberwachung (Refs
#793):** Der
Health-Endpoint fuehrt genau diese Pruefung jetzt bei **jedem** Aufruf auf der
laufenden Verbindung aus (``SELECT rolsuper, rolbypassrls … WHERE rolname =
current_user``). Umgeht die Runtime-Rolle RLS, faellt ``status`` dauerhaft auf
``degraded`` (HTTP bleibt 200) und Token-Caller sehen die Felder
``db_user_is_superuser``/``db_role_posture``; das Detail steht zusaetzlich als
``WARNING`` im Log (Logger ``core.views.health``). Damit faellt ein
fehlkonfigurierter Cluster im Monitoring auf, ohne dass jemand die Rollen
manuell abfragen muss — Feld-Referenz:
[docs/monitoring-guide.md](monitoring-guide.md). Abgrenzung: ``check_db_roles``
prueft, dass App-/Admin-Rolle mit den richtigen Attributen im Katalog
**existieren**; der Health-Endpoint prueft, als **wer** die Anwendung
tatsaechlich verbindet.

### DB-Rollenmodell (Goldstandard)

**Dies ist die kanonische Beschreibung des PostgreSQL-Rollenmodells** — andere
Deployment-Dokumente verweisen hierher, statt sie zu wiederholen. Sie gilt fuer
**alle** ausgelieferten Deployment-Stacks;
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

Operator-/Wartungs-Tasks laufen im opt-in Service `maintenance`; die
vollstaendige `.env` laden `maintenance` **und** der `cron`-Sidecar
([§ 3.2](#32-cron-sidecar-standard)), beide ueberschreiben dort
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
| `POSTGRES_APP_USER` | `environment:` von **`web`**, **`maintenance`**, **`cron`** | `check_db_roles` bzw. `src/core/services/compliance/db_roles.py` — **prueft** unter diesem Namen |

Fehlt der zweite Name, faellt `check_db_roles` auf den Datenbank-Benutzer der
Verbindung zurueck (`src/core/services/compliance/db_roles.py`). Im
`maintenance`-Service ist das die Admin-Rolle: das Gate prueft dann die falsche
Rolle als App-Rolle und meldet einen Defekt, der keiner ist, oder uebersieht
einen echten.

**Bestandscluster: der Compose-Fix wirkt nicht rueckwirkend.** Das Init-Script
ist **INIT-ONLY** und laeuft ausschliesslich beim Anlegen eines leeren `PGDATA`.
Auf einem bereits existierenden Volume (z.B. `<projekt>_pgdata`) legt es weder
neue Rollen an noch gleicht es bestehende an die Env an. Zwei Wege:

1. **Frisches Volume** — Dump/Restore nach
   [§ 13.1](#131-ablauf-mit-datenerhalt-wartungsfenster). Der einfachere,
   bereits durchgespielte Weg; das Init-Script baut die Zielrollen dabei selbst auf.
2. **In-Place-Umbau** nach
   [§ 9.6](#96-rollout-der-runtime-owner-rollentrennung-auf-laufenden-installationen-a14).
   **Achtung — ein reiner Ownership-Umzug reicht dafuer nicht:** auf einem Cluster,
   der ohne Init-Script hochkam, existiert gar keine Admin-Rolle, und die App-Rolle
   ist dort die **Bootstrap-Rolle**, die sich nicht entrechten laesst
   (`The bootstrap superuser must have the SUPERUSER attribute`). § 9.6
   diagnostiziert die Ausgangslage und beschreibt je Fall den vollstaendigen Weg
   inklusive Rollen-Neuanlage; die Passwortrotation allein
   (`scripts/ops/rotate-db-passwords.sh`, § „DB-Passwortrotation" unten) legt
   **keine** Rollen an.

**Bis einer dieser beiden Wege gefahren ist, bleibt die alte Superuser-Situation
auf einem laufenden Server live** — der Repo-Fix allein aendert daran
nichts. `/health/` macht es sichtbar, sperrt es aber nicht: Bei einer
SUPERUSER-/BYPASSRLS-Runtime-Rolle meldet der Endpunkt `status: degraded`
(`HealthView` in `src/core/views/health.py`) und bleibt bei HTTP 200. Der
Compose-Healthcheck von `web` lässt jeden Status außer `error` durch
(`docker-compose.prod.yml:292–294`), `caddy` wartet nur auf `service_healthy`
(`:526–528`). Der Container wird also trotzdem `healthy`, und `caddy` routet. Die
Sperre ist allein das Gate `check_db_roles` (`docker-migrate.sh:53`, Migrate-Job)
und die Verifikation unten; `/health/` ist Frühwarnung für den Uptime-Monitor.

**Verifikation** nach dem ersten Hochfahren — das Gate prueft beide Rollen:

Der Befehl gehört in den Dienst `maintenance`: `web` bekommt kein `POSTGRES_ADMIN_USER`; in `web` aufgerufen prüft er deshalb nur die App-Rolle, warnt und endet trotzdem mit Exit 0, die Admin-Rolle und die Ownership bleiben ungeprüft.

```bash
docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles
# erwartet: App-Rolle  rolsuper=False, rolbypassrls=False
#           Admin-Rolle rolsuper=False, rolbypassrls=True   (Exit-Code 0)
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
cd /opt/anlaufstelle
./scripts/ops/rotate-db-passwords.sh
```


**Compose-Dateiliste:** Das Skript baut die Dateiliste wie `scripts/initial-setup.sh`
auf: die Basisdatei (`COMPOSE_FILE`), danach `compose.legal.yml` und
`compose.override.yml`, soweit sie neben der Basisdatei liegen, und gibt die
verwendete Liste beim Start aus (`Compose files: …`). Jeder `stop`-, `up`-
und `run`-Schritt einschließlich des Rollbacks nutzt dieselbe Liste. Das ist
nötig, weil Compose `compose.override.yml` nicht mehr selbst lädt, sobald ein
`-f` gesetzt ist, und weil `compose.legal.yml` Umgebungsvariablen und Volume für
Impressum und Datenschutzerklärung nur an `web` setzt: Ein nur aus der
Basisdatei neu erzeugter `web`-Container verlöre beides, und die Links im Footer
verschwänden still. Andere Host-Skripte erzeugen keine Container neu:
`initial-setup.sh` nutzt dieselbe Liste; `backup.sh`, `restore.sh` und
`restore-drill.sh` stoppen höchstens Dienste oder starten One-Shots
(`run --rm maintenance`).

Die Rotation ist **kein Deployment**: alle `compose run`-/`up`-Schritte nutzen
`--pull never` und damit exakt die bereits verifizierten lokalen Images. So kann
weder ein bewegtes Tag den Code mitten im Wartungsfenster wechseln noch ein
Registry-Ausfall Rotation oder Rollback blockieren. Ein neues Image immer vor
dem Wartungsfenster separat deployen und pruefen.

**Voraussetzungen:** Wartungsfenster ankündigen; sicherstellen, dass gerade kein
Backup-/Restore-Job laeuft; einen aktuellen, verifizierten Sicherungsstand haben;
`.env` muss mode `600` (oder strenger) besitzen. Das Skript braucht Docker-
Zugriff und `openssl`, gibt aber **kein** Passwort auf stdout/stderr oder in
Prozessargumenten aus. Es erzeugt drei voneinander unabhaengige Zufallswerte und
setzt intern als Bootstrap-Superuser eine Transaktion nach diesem Muster ab:

```sql
BEGIN;
ALTER ROLE "anlaufstelle"       PASSWORD '<neues App-Passwort>';
ALTER ROLE "anlaufstelle_admin" PASSWORD '<neues Admin-Passwort>';
ALTER ROLE "postgres"           PASSWORD '<neues Bootstrap-Passwort>';
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
| `cron` | Admin (`POSTGRES_ADMIN_PASSWORD`) | Zusammen mit `web` stoppen/starten, damit kein Retention-/Audit-Job mit altem Login neu verbindet; nur, wo der Stack den Dienst enthält. |
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
[db-password-rotation] Jetzt Passwort-Escrow und Notfallunterlagen aktualisieren (Ops-Runbook DB-Rotation).
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
| Migration `0047` nicht angewandt | `showmigrations core` zeigt `[ ]` | Migrate-Job laufen lassen (§ 1.2): `docker compose "${compose_files[@]}" run --rm --entrypoint=/app/docker-migrate.sh maintenance` |
| Admin-Query ueber `./manage.py shell` liefert 0 Rows | Shell-Session setzt die Variable nicht | In `transaction.atomic()` vor den Queries `connection.cursor().execute("SELECT set_config('app.current_facility_id', %s, true)", [fid])` (gilt nur bis Transaktionsende) |

Refs #542,
#586.

### 9.5 Append-Only-Trigger-Bypass via Owner-DDL (A1.4)

Die DSGVO-Art.-17-Anonymisierung (`Client.anonymize`), das Audit-Pruning
(`prune_auditlog`) sowie `backfill_audit_chain`/`reencrypt_fields` müssen die
Append-Only-Trigger (`auditlog_immutable`, `eventhistory_no_update`/`_no_delete`)
für ihre legitimen Schreibzugriffe kurz umgehen. Das läuft (Refs
#1026) **nicht**
über `session_replication_role = replica` (`ENABLE ALWAYS`-Trigger feuern dabei
trotzdem), sondern `bypass_replication_triggers()`
deaktiviert die Trigger gezielt per **Owner-DDL** `ALTER TABLE … DISABLE TRIGGER`
und stellt sie per `ENABLE ALWAYS` wieder her — alles in **einem**
`transaction.atomic()` (SIGKILL-sicher).

Konsequenz für den Betrieb: Der Wartungspfad braucht **Owner-Rechte**. Die
Management-Commands `enforce_retention`, `backfill_audit_chain`,
`reencrypt_fields` und `load_package` (M2, Refs
#1058 — RLS-Bypass-Guard
bricht sonst fail-loud ab) müssen daher als **Owner-/Admin-Rolle**
(`POSTGRES_ADMIN_USER`) laufen — das ist ueber den dedizierten
`maintenance`-Service bereits so eingerichtet (Retention-Job im `cron`-Sidecar, [§ 3.2](#32-cron-sidecar-standard)). Läuft ein solcher
Command versehentlich als App-Rolle, bricht der Bypass mit `must be owner of
table core_eventhistory` ab (`core_eventhistory` steht in
`_APPEND_ONLY_TRIGGERS` vorn, #2065) — Fail-loud, kein stiller
Audit-Verlust.

**Die Art.-17-Sofortaktion ist kein Wartungspfad** (Refs
#2085). Die
Papierkorb-Aktion „Jetzt endgültig anonymisieren" läuft **interaktiv im
Webprozess** und damit unter der App-Rolle — dort gibt es keinen
Owner-DDL-Weg und kein Wartungsfenster. Liefe sie ungebremst in den
Bypass, scheiterte das erste `ALTER TABLE … DISABLE TRIGGER`, die
`@transaction.atomic`-Klammer risse die **gesamte** Anonymisierung samt
Nachweiseintrag zurück, und die Nutzerin sähe einen **HTTP 500** — die Person
bliebe unverändert stehen, das Löschverlangen unerfüllt. Auf Entwicklungs- und
Testsystemen fiel das nie auf, weil dort das Einzelrollen-Modell läuft und
dieselbe Verbindung Owner ist.

Der Pfad ist **zweigeteilt** (#2085), und die App-Rolle bekommt **kein**
neues Recht:

| Was | Wann | Wer |
|---|---|---|
| Pseudonym, Notizen, Zusatzfelder, Fall-, Episoden- und Live-Ereignisinhalte, Zeitstempel-Generalisierung, Anhänge | **sofort im Request** | App-Rolle (gewöhnliches DML) |
| **Ältere Fassungen der Ereignis-Dokumentation** (`core_eventhistory`) | **regulär mit dem nächsten `enforce_retention`-Lauf** (Cron, in der Voreinstellung 03:00); was ein Legal Hold oder eine gleichzeitige Bearbeitung zurückhält, holt ein späterer Lauf nach | Admin-/Owner-Rolle im `maintenance`-Service |

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
> die **älteren Fassungen** der Ereignis-Dokumentation dieser Person noch mit
> ihren Werten in der Datenbank. Der nächste Lauf (in der Voreinstellung
> 03:00 Uhr) tilgt sie regulär; Fassungen unter einem aktiven Legal Hold stellt
> er zurück, Datensätze, die gerade eine andere Bearbeitung sperrt, überspringt
> er — beides holt ein späterer Lauf nach. Oberfläche und Handbuch sagen das
> ausdrücklich. Wer eine Art.-17-Tilgung beschleunigen will, fährt danach
> einmal von Hand (ein Versuch, keine Vollständigkeitsgarantie — der Lauf weist
> Zurückgestelltes in seiner Ausgabe aus und ist bei Bedarf zu wiederholen):
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
sie laufen unter der Migrationsrolle und werden von
`assert_migration_role_bypasses_rls(schema_editor)` als erster Zeile abgesichert
(#1746).
Der `facility_id`-Backfill der Attachment-Migration `0130`
(#1738) nutzt diesen
Guard — laeuft `migrate` versehentlich als App-Rolle, bricht er fail-loud ab,
statt still **0 Zeilen** zu backfillen und die Migration als erfolgreich zu
melden. `docker-migrate.sh` laeuft deshalb ausschliesslich im Maintenance-Service.

Ein `GRANT SET ON PARAMETER session_replication_role` wird **nicht
benötigt** und steht nicht im Init-Script. Ein vorhandenes Recht kann
entzogen werden (optional, Least Privilege):

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

**Der naechtliche Retention-Lauf ist ein REGELMAESSIGER Bypass-Aufrufer**
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
  Anwendung ohnehin schreibt. `_APPEND_ONLY_TRIGGERS` fuehrt
  `core_auditlog` nicht zuerst — das waere ein ABBA-Zyklus gegen jeden mutierenden Request.
  Personen- und Ereigniszeilen einer Charge werden per
  `SELECT … FOR UPDATE SKIP LOCKED` gesperrt, **bevor** der Trigger-Bypass
  oeffnet: sonst hielte der Lauf die Tabellensperre und wartete auf eine fremde
  Zeilensperre. Dieselben Zeilen sperrt die Legal-Hold-Anlage, bevor sie
  schreibt — daher haelt ein Hold auf **Ereignis oder Person** den Sweep
  zuverlaessig auf und wird nicht von ihm ueberholt. Bricht eine Charge dennoch mit
  `deadlock detected` ab, wird sie idempotent mit Backoff wiederholt; der Lauf
  faellt nicht aus.

Betriebsregeln daraus:

- Die Bypass-Commands `enforce_retention` (Retention-Job im `cron`-Sidecar, § 3.2),
  `backfill_audit_chain` und `reencrypt_fields` in **Wartungs-/Nebenzeiten** mit
  geringer Schreiblast fahren — nicht zur Stoßzeit. Für die **interaktive**
  Art.-17-Aktion gilt das nicht: sie nimmt im Webprozess gar keine DDL-Sperre
  mehr (s. o.), ihre Historien-Redaktion läuft im nächtlichen Lauf mit.
- Für die Schlüsselentfernung Feld- und Dateirotation samt Backup-Ablauf nach dem folgenden Wartungsweg durchführen; ein erfolgreicher `reencrypt_fields`-Lauf allein reicht nicht.
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

#### Dateirotation und Altschlüssel entfernen

Ein Ring `ENCRYPTION_KEYS` gilt für **die gesamte Installation**, nicht je Einrichtung. Den neuen Schlüssel zuerst voranstellen, alte Schlüssel zunächst behalten und alle schlüsselnutzenden Prozesse mit dem neuen Ring neu erstellen. Ein bloßes `docker compose restart` übernimmt geänderte `.env`-Werte nicht; für die ausgelieferten Dienste gilt `docker compose "${compose_files[@]}" up -d --force-recreate web cron` (Dateireihe `compose_files` wie beim Update aufgebaut, also mit `compose.legal.yml`/`compose.override.yml`, falls vorhanden). Neue Wartungscontainer lesen die aktuelle Konfiguration. Keine Schlüssel in Shell-Protokolle oder Berichte schreiben.

**Verbindliche Reihenfolge** (#2526):

1. **Feldrotation:** `docker compose -f docker-compose.prod.yml run --rm maintenance reencrypt_fields`. Bei Warnungen über übersprungene Zeilen oder TOTP-Retries wiederholen, bis der Lauf ohne sie endet; Fehler zuerst beheben. Ein Exit 0 trotz Konkurrenzwarnung reicht nicht.
2. **Dateirotation:** `docker compose -f docker-compose.prod.yml run --rm maintenance reencrypt_files`. Alle referenzierten Attachment-Versionen einschließlich soft-gelöschter und ersetzter Versionen werden durchlaufen. `--dry-run` prüft ohne Chiffrat-/Manifeständerung; `--batch-size 200` steuert die DB-Charge, nicht die Klartextpufferung. `--facility <UUID>` begrenzt auf eine Einrichtung und ist kein Nachweis installationsweiter Entfernbarkeit.
3. **Ungefilterte Primär-only-Prüfung:** `docker compose -f docker-compose.prod.yml run --rm maintenance reencrypt_files --verify`, **ohne `--facility`**. Jede referenzierte Version wird vollständig nur mit dem ersten Schlüssel authentifiziert, einschließlich der im Dateiformat vorgesehenen Chunk-Bindung. Ein fehlender Schlüssel, eine fehlende referenzierte Datei, alte oder beschädigte Chiffrate führen zu Exit ≠ 0 mit UUID/Grund bzw. Laufdiagnose. Altschlüssel behalten, Ursache beheben und Rotation/Prüfung wiederholen. Die Prüfung verändert weder Chiffrate noch Manifest; sie schreibt jedoch eine Audit-Sammelzeile.
4. **Nicht erfasste Dateien klären und Backups ablaufen lassen:** ImportJob-Zwischendateien unter `imports/` sind keine Attachments und werden weder umgeschlüsselt noch geprüft. Offene Importe vor der Entfernung über den regulären Importweg abschließen oder abbrechen; auch vorhandene Import-Metadaten und verschlüsselte Reste auf ihre Schlüsselabhängigkeit prüfen. Verschlüsselte `.rotate-*.tmp`-Staging-Reste ohne verbliebene DB-Zeile werden nicht global gesammelt; die Vollprüfung meldet sie nicht. Solche Reste gezielt im Wartungsfenster prüfen und nach geklärter Zuordnung bereinigen, niemals als Beleg einer erfolgreichen Rotation verwenden. Bis alle noch benötigten Chiffrate unabhängig vom Altschlüssel sind, bleibt dieser erhalten. Danach müssen **alle lokalen und Off-Site-Backup-Sätze mit alten Chiffraten tatsächlich abgelaufen sein**: lokal längste Stufe `-mtime +90` (91 volle Tage, Löschung erst beim nächsten erfolgreichen Rotationslauf); Off-Site nach der am Ziel konfigurierten Höchstfrist, einschließlich Versionen/Objektsperren und weiterer Kopien. Der aktuelle `backup.sh`-Sync ist keine Fernlöschung; ohne wirksame Ziel-Retention gibt es keinen belegten Ablauf. Rotation verändert bestehende Backups nicht. Für noch aufbewahrte ältere Sätze ihre Schlüssel im getrennten Eskrow behalten ([Disaster-Recovery § 4](disaster-recovery.md#4-schlüssel--und-secret-wiederbeschaffung)).
5. **Altschlüssel entfernen:** Erst nach diesen Nachweisen aus `ENCRYPTION_KEYS` nehmen. Bei weiter vorhandenen alten Backup-Sätzen oder ungeklärten Import-/Staging-Abhängigkeiten nicht entfernen. Zeitablauf allein ersetzt nicht die Kontrolle der tatsächlich noch vorhandenen Sätze.
6. **Neustart mit neuer Konfiguration:** alle schlüsselnutzenden Prozesse neu erstellen (`docker compose "${compose_files[@]}" up -d --force-recreate web cron`; eigene zusätzliche Prozesse entsprechend). Ohne Neustart können alte Prozesse noch den bisherigen Ring verwenden.
7. **Erneut ungefiltert prüfen:** `docker compose -f docker-compose.prod.yml run --rm maintenance reencrypt_files --verify`, ohne `--facility`, jetzt mit dem gekürzten Ring. Exit 0, keine Fehler und weiterhin fachlich lesbare Feldwerte/Anhänge sowie funktionierende MFA sind die Abnahmebedingungen. Die Dateiprüfung allein beweist keine Feld-/Import-/Backup-Lesbarkeit. Bei Fehlern den benötigten Ring wiederherstellen, Prozesse erneut mit ihm starten und Ursache beheben.

**Abbruch und Konkurrenz:** Nur verschlüsselte Daten gelangen in die temporäre Datei im selben Verzeichnis, keine temporären Klartexte. Der Dienst synchronisiert die Datei, ersetzt atomar mit `os.replace` und synchronisiert das Verzeichnis. Ein Abbruch vor dem Austausch lässt das Original bestehen; nach dem Austausch kann bereits die neue Datei bestehen, obwohl Manifest/Audit noch fehlen. Nach Behebung der Ursache dasselbe Kommando wiederholen: bereits primäre Dateien bleiben byte-identisch, jede tatsächliche Datei wird erneut authentifiziert. Das Manifest `MEDIA_ROOT/.reencrypt-files.jsonl` ist **Fortschritt, kein Integritätsbeweis**; eine zerrissene letzte Zeile wird repariert, ungültige vollständige Datensätze führen zum harten Fehler. Ein installationsweiter Lauf-Lock weist parallele Rotationsläufe ab; Zeilen-/Dateisperren schützen den kurzen Austausch gegenüber kooperierenden Löschpfaden. Bei einer inzwischen gelöschten DB-Zeile wird kein Anhang wiederhergestellt; geänderte Dateireferenzen/Dateistände werden nicht blind überschrieben. Das ist keine Garantie gegen beliebige externe Dateimanipulation oder Hardwarefehler. Für die Schlüsselabnahme Schreibzugriffe und schreibende Hintergrundjobs anhalten, damit Prüfung und Entscheidung denselben Bestand betreffen.

**Rolle und Audit:** `reencrypt_files` verlangt `ANLAUFSTELLE_PROCESS_ROLE=maintenance` und eine PostgreSQL-Rolle mit SUPERUSER oder BYPASSRLS; der Wartungsservice liefert diesen Kontext. Ein App-`super_admin` ersetzt ihn nicht; Aufruf im `web`-Container scheitert vor Datei-/Manifeständerungen. Autorisierte Rückläufe versuchen eine `FILE_REENCRYPTION_COMPLETED`-Sammelzeile mit Zählern, Dauer, Optionen und Fehlerstatus zu schreiben, ohne Dateinamen/Inhalte. Ein Kill kann die abschließende Sammelzeile verhindern; fehlendes Audit beweist weder Erfolg noch unveränderte Dateien. Ein Audit-Schreibfehler ist ein harter Command-Fehler: Schlüssel behalten und Prüfung wiederholen.

**Verifikation** (App-Rolle kann Trigger NICHT abschalten, Owner-Rolle schon):

```sql
-- App-Rolle (kein Owner) -> muss scheitern:
SET ROLE anlaufstelle;      -- POSTGRES_USER
ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable;  -- ERROR: must be owner
RESET ROLE;
-- Trigger-Ruhezustand -> 'A' (ENABLE ALWAYS):
SELECT tgname, tgenabled FROM pg_trigger
WHERE tgname IN ('auditlog_immutable','eventhistory_no_update','eventhistory_no_delete');
```

### 9.6 Rollout der Runtime-/Owner-Rollentrennung auf laufenden Installationen (A1.4)

**Fresh DBs** ziehen die Trennung automatisch auf
([`deploy/postgres-init/01-app-role.sh`](../deploy/postgres-init/01-app-role.sh):
Admin wird Owner, App bekommt nur DML). Eine **bereits laufende Installation mit
fehlerhafter Rollenkonfiguration** (Ownership noch bei der App-Rolle oder keine
separate Admin-Rolle) braucht einen **einmaligen, Maintainer-geführten** Umbau —
kurze Downtime empfohlen (keine schreibenden Requests währenddessen).

> **Die einfachere, bereits durchgespielte Alternative:** Dump → frisches Volume →
> Restore nach [§ 13.1](#131-ablauf-mit-datenerhalt-wartungsfenster). Dabei
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
SELECT pg_get_userbyid(datdba) AS db_owner FROM pg_database WHERE datname = current_database();
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
REASSIGN OWNED BY anlaufstelle TO anlaufstelle_admin;   -- Tabellen/Sequenzen/Views
-- 2. App-Rolle: Ownership weg, nur noch DML.
GRANT USAGE ON SCHEMA public TO anlaufstelle;
ALTER DEFAULT PRIVILEGES FOR ROLE anlaufstelle_admin IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anlaufstelle;
ALTER DEFAULT PRIVILEGES FOR ROLE anlaufstelle_admin IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO anlaufstelle;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anlaufstelle;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anlaufstelle;
-- 3. Trigger haerten (macht sonst die Migration 0107 beim naechsten Deploy):
--    kann entfallen, wenn direkt danach `docker-migrate.sh` laeuft.
```

Danach **zuerst § 9.6.4** fahren (grünes `check_db_roles`, bevor `web` wieder
startet), dann normal deployen — `docker-migrate.sh` wendet Migration `0107` an,
falls noch nicht geschehen — und mit § 9.5 verifizieren.

#### 9.6.2 Fall B — Admin-Rolle fehlt, App-Rolle ist nicht die Bootstrap-Rolle

Hier lässt sich die App-Rolle an Ort und Stelle entrechten. Als
**Bootstrap-Superuser**, in der Anwendungs-DB (Beispielnamen wie in
`.env.example`):

```sql
-- 0. Vorher: web, cron und maintenance stoppen (keine App-Schreibzugriffe).
--    Ein Passwortwechsel beendet BESTEHENDE Sessions nicht — verbliebene
--    Verbindungen der Altrolle ausdruecklich kappen:
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname = current_database() AND usename = 'anlaufstelle' AND pid <> pg_backend_pid();
BEGIN;   -- alles Folgende ist transaktional; bei einem Fehler bleibt nichts halb stehen
-- 1. Fehlende Admin-/Owner-Rolle anlegen. Passwort >= 32 Zeichen.
CREATE ROLE anlaufstelle_admin WITH LOGIN NOSUPERUSER BYPASSRLS NOCREATEROLE NOCREATEDB
    PASSWORD '<neues-admin-passwort>';
GRANT ALL ON SCHEMA public TO anlaufstelle_admin;
-- 2. Ownership Datenbank + alle Objekte auf die Admin-Rolle uebertragen.
ALTER DATABASE anlaufstelle OWNER TO anlaufstelle_admin;
GRANT ALL PRIVILEGES ON DATABASE anlaufstelle TO anlaufstelle_admin;
REASSIGN OWNED BY anlaufstelle TO anlaufstelle_admin;
-- 3. App-Rolle entrechten — DAS ist der Schritt, den ein reiner
--    Ownership-Umzug nicht leistet.
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
`POSTGRES_BOOTSTRAP_PASSWORD`** — der Produktiv-Stack verlangt alle drei
(`settings/prod.py` lehnt einen `maintenance`-Prozess ohne sie ab, und das
Init-Script prüft sie). Alle drei ≥ 32 Zeichen, kein `change-me`-Präfix.
Abschluss mit § 9.6.4.

#### 9.6.3 Fall C — die App-Rolle ist die Bootstrap-Rolle

**Die Bootstrap-Rolle kann nicht entrechtet werden.** PostgreSQL lehnt das
ausdrücklich ab (verifiziert auf PostgreSQL 18.4):

```text
ALTER ROLE anlaufstelle NOSUPERUSER;
ERROR:  permission denied to alter role
DETAIL:  The bootstrap superuser must have the SUPERUSER attribute.
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
> ERROR:  cannot reassign ownership of objects owned by role anlaufstelle
>         because they are required by the database system
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
--    Ein Passwortwechsel beendet BESTEHENDE Sessions nicht — verbliebene
--    Verbindungen der alten Bootstrap-/App-Rolle ausdruecklich kappen:
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname = current_database() AND usename = 'anlaufstelle' AND pid <> pg_backend_pid();

-- Variablen. ACHTUNG: hinter einem \set darf KEIN Kommentar stehen. psql
-- fasst ihn nicht als Kommentar auf, sondern als weitere Argumente und haengt
-- sie an den Wert an — aus "anlaufstelle  -- alte Rolle" wird dann der
-- Rollenname "anlaufstelle--alteRolle", und JEDE Ownership-Schleife unten
-- findet null Objekte, ohne dass irgendetwas fehlschlaegt.
\set ON_ERROR_STOP on
\set old_role anlaufstelle
\set app_role anlaufstelle_app
\set admin_role anlaufstelle_admin
-- dbname: an den Wert von POSTGRES_DB anpassen (Name der Datenbank).
\set dbname anlaufstelle

-- Kontrollausgabe VOR dem Umbau: hier muessen exakt die vier Namen stehen,
-- ohne angehaengten Text. Stimmt etwas nicht, abbrechen.
\echo 'old_role  =[':old_role']'
\echo 'app_role  =[':app_role']'
\echo 'admin_role=[':admin_role']'
\echo 'dbname    =[':dbname']'

BEGIN;

-- 1. App-Rolle anlegen, falls sie fehlt, und danach Attribute + Passwort
--    unbedingt setzen. Diese Form ist idempotent: sie laeuft auch nach einem
--    abgebrochenen frueheren Versuch durch, bei dem die Rolle schon existiert.
SELECT format('CREATE ROLE %I', :'app_role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_role')
\gexec
ALTER ROLE :"app_role" WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB
    PASSWORD '<neues-app-passwort>';

-- 2. Admin-/Owner-Rolle, gleiches Muster.
SELECT format('CREATE ROLE %I', :'admin_role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'admin_role')
\gexec
ALTER ROLE :"admin_role" WITH LOGIN NOSUPERUSER BYPASSRLS NOCREATEROLE NOCREATEDB
    PASSWORD '<neues-admin-passwort>';
GRANT ALL ON SCHEMA public TO :"admin_role";

-- 3. Datenbank-Ownership.
ALTER DATABASE :"dbname" OWNER TO :"admin_role";
GRANT ALL PRIVILEGES ON DATABASE :"dbname" TO :"admin_role";

-- 4. Objekt-Ownership selektiv umziehen (Ersatz fuer REASSIGN OWNED BY).
--    4a) Relationen: Tabellen, Sequenzen, Views, Materialized Views, Foreign Tables.
SELECT format('ALTER %s %I.%I OWNER TO %I',
              CASE c.relkind WHEN 'S' THEN 'SEQUENCE'
                             WHEN 'v' THEN 'VIEW'
                             WHEN 'm' THEN 'MATERIALIZED VIEW'
                             WHEN 'f' THEN 'FOREIGN TABLE'
                             ELSE 'TABLE' END,
              n.nspname, c.relname, :'admin_role')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relowner = (SELECT oid FROM pg_roles WHERE rolname = :'old_role')
   AND c.relkind IN ('r','p','S','v','m','f')
   AND n.nspname NOT IN ('pg_catalog','information_schema')
   AND n.nspname NOT LIKE 'pg\_toast%'
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
 ORDER BY c.relkind DESC
\gexec
--    4b) Routinen (Funktionen, Prozeduren, Aggregate).
SELECT format('ALTER ROUTINE %I.%I(%s) OWNER TO %I',
              n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), :'admin_role')
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE p.proowner = (SELECT oid FROM pg_roles WHERE rolname = :'old_role')
   AND n.nspname NOT IN ('pg_catalog','information_schema')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
\gexec
--    4c) Eigene Typen und Domains (Tabellen-Rowtypes folgen ihrer Tabelle).
SELECT format('ALTER %s %I.%I OWNER TO %I',
              CASE t.typtype WHEN 'd' THEN 'DOMAIN' ELSE 'TYPE' END,
              n.nspname, t.typname, :'admin_role')
  FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
 WHERE t.typowner = (SELECT oid FROM pg_roles WHERE rolname = :'old_role')
   AND t.typtype IN ('e','d','c','r','b') AND t.typrelid = 0 AND t.typelem = 0
   AND n.nspname NOT IN ('pg_catalog','information_schema')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e')
\gexec
--    4d) Eigene Schemata. 'public' gehoert ab PG15 pg_database_owner und folgt
--        damit automatisch dem neuen DB-Owner — dann faellt hier nichts an.
SELECT format('ALTER SCHEMA %I OWNER TO %I', n.nspname, :'admin_role')
  FROM pg_namespace n
 WHERE n.nspowner = (SELECT oid FROM pg_roles WHERE rolname = :'old_role')
   AND n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
\gexec

-- 5. Neue App-Rolle: nur DML.
GRANT CONNECT ON DATABASE :"dbname" TO :"app_role";
GRANT USAGE ON SCHEMA public TO :"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"admin_role" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"admin_role" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_role";

-- 6. Die alte Bootstrap-Rolle ist ab jetzt reine Bootstrap-/Notfallrolle:
--    frisches, langes Passwort setzen und nirgends mehr als Runtime nutzen.
ALTER ROLE :"old_role" PASSWORD '<neues-bootstrap-passwort>';
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
   AND c.relowner = (SELECT oid FROM pg_roles WHERE rolname = :'old_role')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
UNION ALL
SELECT 'routinen', count(*)
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema')
   AND p.proowner = (SELECT oid FROM pg_roles WHERE rolname = :'old_role')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
UNION ALL
SELECT 'typen', count(*)
  FROM pg_type ty JOIN pg_namespace n ON n.oid = ty.typnamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema')
   AND ty.typtype IN ('e','d','c','r','b') AND ty.typrelid = 0 AND ty.typelem = 0
   AND ty.typowner = (SELECT oid FROM pg_roles WHERE rolname = :'old_role')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d
                    WHERE d.classid = 'pg_type'::regclass AND d.objid = ty.oid AND d.deptype = 'e')
UNION ALL
SELECT 'schemata', count(*)
  FROM pg_namespace n
 WHERE n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
   AND n.nspowner = (SELECT oid FROM pg_roles WHERE rolname = :'old_role')
UNION ALL
-- Extension-Mitglieder werden vom Umbau BEWUSST nicht angefasst (s.o.). Sie
-- stehen hier getrennt, damit die Inventur nicht faelschlich "alles bei der
-- Admin-Rolle" meldet: dieser Wert ist erwartungsgemaess > 0.
SELECT 'extension-objekte (by design)', count(*)
  FROM pg_depend d
  LEFT JOIN pg_class c ON d.classid = 'pg_class'::regclass AND c.oid = d.objid
  LEFT JOIN pg_proc  p ON d.classid = 'pg_proc'::regclass  AND p.oid = d.objid
  LEFT JOIN pg_type ty ON d.classid = 'pg_type'::regclass  AND ty.oid = d.objid
 WHERE d.deptype = 'e'
   AND COALESCE(c.relowner, p.proowner, ty.typowner) = (SELECT oid FROM pg_roles WHERE rolname = :'old_role');
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
> ALTER ROLE anlaufstelle NOLOGIN;   -- vor dem Hauptblock, eigene Transaktion
> -- ... Hauptblock ...
> ALTER ROLE anlaufstelle LOGIN;     -- danach wieder freigeben
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
> Superuser nicht. `/health/` würde das als `degraded` anzeigen; der Container
> bliebe trotzdem `healthy`.

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
# erwartet: App-Rolle  rolsuper=False, rolbypassrls=False
#           Admin-Rolle rolsuper=False, rolbypassrls=True   (Exit-Code 0)
```

Exit ≠ 0 heisst: der Umbau ist unvollständig — `web` **nicht** starten, sondern
die Meldung abarbeiten. Erst danach deployen; der Migrate-One-Shot führt dasselbe
Gate erneut aus. `/health/` zeigt die tatsächliche Verbindungs-Rolle laufend als
`degraded` an, sperrt den Start aber nicht — `check_db_roles` muss deshalb
vor dem Deploy erfolgreich gewesen sein.

Das Gate prüft (#2086)
nicht nur, dass die App-Rolle **nicht** Owner ist, sondern dass die
Admin-Rolle es **ist**. Ein Objekt, das nach einem halb gefahrenen Umbau noch der
alten Bootstrap-Rolle gehört, fällt damit auf.

**Fail-loud-Gate (Refs #1026):**
`check_db_roles` prüft **zusätzlich zur Attribut-Prüfung die Ownership**.
Solange der Umzug oben noch nicht gelaufen ist (App-Rolle ist noch DB-/Tabellen-
Owner), meldet das Gate `FAIL … Ownership-Umzug … § 9.6` und bricht mit **Exit 1**
ab — `docker-migrate.sh` stoppt den Deploy, bevor neue Web-Replicas live gehen.
Das verhindert ein stilles Fail-open, bei dem der Deploy trotz noch
App-owned Tabellen grün würde (das Attributprofil App/Admin allein bleibt beim
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
  AND NOT (password LIKE 'pbkdf2_%')  -- unusable = set_unusable_password()
  AND date_joined < now() - interval '7 days'
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
User.objects.filter(username='<username>').delete()
"
```

**Cron-Empfehlung:** Wochen-Audit als reine Report-Query (Host-Job, siehe
[§ 3.3](#33-host-cron-backup--restliche-host-jobs)) — kein automatisches Loeschen, da ein nicht
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
docker compose -f docker-compose.prod.yml run --rm maintenance \
  refresh_statistics_view

# Blocking Refresh erzwingen (fallback ohne UNIQUE-Index)
docker compose -f docker-compose.prod.yml run --rm maintenance \
  refresh_statistics_view --no-concurrent
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
| `core_statisticssnapshot` (Tabelle) | Persistente Monats-Aggregate; durch Retention geloeschte Ereignisse zaehlen als Uebertrag (`_deleted`) weiter mit | `create_statistics_snapshots` | Monatlich |

Beide Jobs sind unabhaengig — Snapshots bleiben auch ohne MV korrekt,
die MV kann auch ohne Snapshots refreshed werden.

### 11.4 Health-Check

PostgreSQL protokolliert keinen Refresh-Zeitpunkt für Materialized Views. Den
letzten erfolgreichen Refresh belegt der Marker `MV_REFRESH_COMPLETED`, den
`refresh_statistics_view` nach jedem Lauf in den Audit-Log schreibt. Er erscheint
im Compliance-Dashboard als Karte „Statistik-View-Refresh“; im `/health/`-Detail
(Token-Aufruf) listet `stale_jobs` den Schlüssel `mv_refresh_last_run`, sobald
der letzte Lauf zu lange her ist.

Direkt in der Datenbank (Rolle `postgres` im `db`-Container, siehe § 6.3; nur
lesend):

```bash
docker compose -f docker-compose.prod.yml exec -T db \
  sh -c 'psql -U postgres -d "$POSTGRES_DB"' <<'SQL'
-- Letzter Refresh laut Marker
SELECT max("timestamp") AS letzter_refresh
FROM core_auditlog
WHERE action = 'mv_refresh_completed';

-- Zeilenzahl pruefen (sollte ungefaehr core_event count entsprechen)
SELECT count(*) FROM core_statistics_event_flat;
SQL
```

Refs #544.

---

## 12. Trust-Boundary (Refs #841)

### 12.1 SECURE_PROXY_SSL_HEADER

[`src/anlaufstelle/settings/prod.py`](../src/anlaufstelle/settings/prod.py) setzt `SECURE_PROXY_SSL_HEADER`:

```python
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
```

Damit erkennt Django HTTPS auch hinter dem Caddy-Proxy. Voraussetzung: **Caddy strippt eingehende `X-Forwarded-Proto`-Header vom Client und setzt sie selbst** auf den tatsaechlichen Verbindungsstatus.

**Wichtig:** Wenn die App **direkt** exponiert wird (ohne Reverse-Proxy), MUSS dieses Setting entfernt werden. Sonst kann ein Angreifer `X-Forwarded-Proto: https` selbst setzen — `request.is_secure()` liefert dann `True`, obwohl die Verbindung Klartext ist; HSTS-/Secure-Cookie-Logik wird ausgehebelt.

In `Caddyfile` wird `X-Forwarded-Proto` **nicht explizit** gesetzt — die blanke Direktive genuegt:

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
| `core.W001` | `hops >= 3` | Heuristik: mehr als CDN + Caddy ist selten echt (#1375) |
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
- **Zwei typische Ursachen, wenn `web` nie healthy wird** (Refs #2077): entweder fehlt der Loopback in `ALLOWED_HOSTS` — dann antwortet der Healthcheck mit `DisallowedHost`-400 —, oder `SECURE_REDIRECT_EXEMPT` greift nicht: `SECURE_SSL_REDIRECT=True` beantwortet den Healthcheck-Aufruf `http://localhost:8000/health/` sonst mit einem 301 auf `https://`, und der Healthcheck-Client kann lokal kein TLS. `web` wird dann nie healthy und `caddy` (`condition: service_healthy`) startet gar nicht erst. Beide Haelften sind in `settings/prod.py` und den `.env*.example`-Vorlagen gesetzt; bei einer aelteren, von Hand gepflegten `.env` sind sie nachzutragen. Weitere Ursachen sind ein Datenbankfehler oder ein gescheiterter Verschlüsselungs-Roundtrip (der Health-Endpoint antwortet dann mit 503); Näheres steht in den Logs von `web`. Schnelltest im Container: `python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health/').status)"` muss `200` liefern, nicht 301 oder 400.
- **Fail-closed:** Bei mehreren oeffentlichen Hosts, reiner Wildcard-Config oder ausschliesslich Loopback-Eintraegen gibt es keine eindeutige Antwort; Prod startet dann nicht und nennt `EXTERNAL_ORIGIN` im Abbruch. Bewusst kein Raten: eine falsch geratene Origin macht jeden Token-Link unbrauchbar.
- **Nicht verschaerft wurde die `ALLOWED_HOSTS`-Pruefung selbst** — Subdomain-Muster bleiben erlaubt, sie sind fuer die Linkbildung jetzt schlicht ohne Bedeutung.

Bei einem Domainwechsel ist `EXTERNAL_ORIGIN` mitzuziehen: bereits versendete Links zeigen weiter auf die alte Adresse und laufen ueber deren Gueltigkeitsdauer (`DJANGO_PASSWORD_RESET_TIMEOUT` bzw. `DJANGO_INVITE_TOKEN_TIMEOUT`) aus.

---

## 13. PostgreSQL-Major-Upgrade

PostgreSQL-Major-Versionen haben **inkompatible Datenverzeichnisse** — ein In-Place-Start des neuen Images auf dem alten Volume ist unmöglich. Der Ablauf für jeden Sprung von Major N auf N+1 ist **dump → frisches Cluster → restore**. (`pg_upgrade` ist nicht vorgesehen: das Alpine-Image enthält nur eine Binär-Major-Version, und neuere `initdb`-Versionen aktivieren Data-Checksums per Default — ein Cluster ohne Checksums wäre dafür erst zu konvertieren.)

**Image-Layout:** Das `postgres`-Image deklariert `VOLUME /var/lib/postgresql`, `PGDATA` liegt unter `/var/lib/postgresql/<N>/docker`. Die Compose-Dateien mounten deshalb das Elternverzeichnis (`…:/var/lib/postgresql` statt `…/data`). Startet man ein neues Image versehentlich gegen ein Volume der alten Major, bricht der Entrypoint **kontrolliert ohne Datenverlust** ab („there appears to be PostgreSQL data in: /var/lib/postgresql“ samt Hinweistext).

### 13.1 Ablauf mit Datenerhalt (Wartungsfenster)

Reihenfolge ist sicherheitskritisch: **Rollen vor Daten** — das Init-Script legt App- und Admin-Rolle im frischen Cluster an, bevor der Restore Ownership und Grants wiederherstellt (ADR-020). Die Befehle stehen in zwei Blöcken: **Vorbereiten** (Schritte 0–3, nichts wird gelöscht) und **Austauschen** (Schritte 4–10). Der Austausch-Block entfernt das Volume nur, wenn der Arbeits-Dump als nicht leer und lesbar bestätigt ist (`dump_ok=yes`); alle anderen Schritte beendest du bei einem Fehler selbst, bevor du den nächsten ausführst. Beide Blöcke laufen in **derselben** Shell-Sitzung (Variablen `dump`, `pg_volume`, `dump_ok`).

Arbeits-Dump und Volume-Tarball enthalten **Klartext-Daten** (Art. 9 DSGVO). Sie liegen in einem Verzeichnis, das nur der ausführende Benutzer lesen darf (`0700`), nicht in `/tmp`.

```bash
cd /opt/anlaufstelle

# Vollständige Compose-Dateiliste in fester Reihenfolge (admin-guide.md § 1).
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)

# Arbeitsverzeichnis (nur für den ausführenden Benutzer lesbar) und Volume.
umask 077
install -d -m 0700 upgrade-work
dump="$PWD/upgrade-work/pre-upgrade.dump"
pg_volume=$(docker volume ls -q \
    --filter label=com.docker.compose.project=anlaufstelle-prod \
    --filter label=com.docker.compose.volume=pgdata)
echo "Volume: ${pg_volume:-NICHT GEFUNDEN}"   # genau ein Name, z. B. anlaufstelle-prod_pgdata

# 0. Maintenance-Mode aktivieren (§ 6.5a). web und cron laufen weiter: backup.sh
#    liest die Medien über web und den Audit-Anker über cron, beide Dienste
#    müssen für das Backup laufen.
docker compose "${compose_files[@]}" exec -T web touch /media/anlaufstelle.maintenance

# 1. Finales verschlüsseltes Backup (§ 1.1, Restore § 6.6); db, web und cron
#    müssen laufen.
./scripts/ops/backup.sh

# 2. Schreibpfade stilllegen (die DB bleibt erreichbar) — web UND cron, denn
#    der cron-Sidecar schreibt sonst weiter (Retention, MV-Refresh, Breach-Scan)
#    und diese Schreibvorgänge fehlten im Dump. Danach den unverschlüsselten
#    Arbeits-Dump ziehen. Er läuft als Admin-Rolle (BYPASSRLS) über den
#    Container-Socket wie backup.sh; im db-Container heißt die Variable
#    ADMIN_DB_USER. Die Prüfung setzt dump_ok=yes nur, wenn die Datei nicht
#    leer ist und pg_restore ihr Inhaltsverzeichnis lesen kann.
docker compose "${compose_files[@]}" stop web cron
docker compose "${compose_files[@]}" exec -T db \
    sh -c 'pg_dump -U "$ADMIN_DB_USER" -Fc -Z 6 "$POSTGRES_DB"' > "$dump"
dump_ok=
[[ -s "$dump" ]] \
    && docker compose "${compose_files[@]}" exec -T db pg_restore --list < "$dump" > /dev/null \
    && dump_ok=yes
echo "dump_ok=${dump_ok:-nein}"   # muss "yes" sein, sonst Schritt 4 nicht ausführen

# 3. DB stoppen, Volume-Tarball als byte-genauen Rollback-Anker ziehen.
docker compose "${compose_files[@]}" stop db
docker run --rm -v "$pg_volume":/from -v "$PWD/upgrade-work":/to alpine \
    tar czf /to/pgdata-pre-upgrade.tar.gz -C /from .
ls -l upgrade-work/
```

```bash
cd /opt/anlaufstelle

# Vollständige Compose-Dateiliste in fester Reihenfolge (admin-guide.md § 1).
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)

# 4. Alten DB-Container + Volume entfernen — nur mit bestätigtem Dump, nur nach
#    Schritt 1–3.
if [[ "$dump_ok" == yes && -n "$pg_volume" ]]; then
    docker compose "${compose_files[@]}" rm -f db
    docker volume rm "$pg_volume"
else
    echo "ABBRUCH: Dump nicht bestätigt oder Volume unbekannt — nichts gelöscht." >&2
fi
```

Weiter nur, wenn Schritt 4 kein „ABBRUCH“ meldet:

```bash
cd /opt/anlaufstelle

# Vollständige Compose-Dateiliste in fester Reihenfolge (admin-guide.md § 1).
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)

dump="$PWD/upgrade-work/pre-upgrade.dump"

# 5. Neuen Release-Stand einspielen (neues postgres:<N+1>-alpine-Image
#    und passender Mount)
git pull   # bzw. Release-Paket

# 6. Nur DB hochfahren: frisches initdb (Checksums on) + Init-Script legt
#    App-/Admin-Rolle an. Warten, bis der endgültige Server auf TCP antwortet
#    (während der Initialisierung läuft nur ein vorläufiger Server ohne
#    TCP-Port); erst dann ist der Restore möglich. Das Log muss fehlerfrei sein.
docker compose "${compose_files[@]}" up -d db
docker compose "${compose_files[@]}" exec -T db sh -c 'until pg_isready -h 127.0.0.1 -U postgres -q; do sleep 1; done'
docker compose "${compose_files[@]}" logs db | grep -icE 'error|fatal'   # 0

# 7. Restore als Bootstrap-Superuser via Container-Socket (bypasst RLS/FORCE-RLS,
#    wie scripts/ops/restore-drill.sh; --clean/--if-exists ist re-run-sicher)
docker compose "${compose_files[@]}" exec -T db \
    sh -c 'pg_restore -U postgres -d "$POSTGRES_DB" --clean --if-exists' < "$dump"

# 8. Verifizieren: Rollen-Gate, RLS aktiv, Stichproben
docker compose "${compose_files[@]}" run --rm -T maintenance check_db_roles  # Exit 0
docker compose "${compose_files[@]}" exec -T db sh -c \
    'psql -U postgres -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity"'   # > 0

# 9. Migrationen als One-Shot (i. d. R. No-op) + Stack hoch (web UND cron) + Health (§ 1.2/1.3)
docker compose "${compose_files[@]}" run --rm --entrypoint=/app/docker-migrate.sh maintenance
docker compose "${compose_files[@]}" up -d web cron caddy
curl -sf https://$DOMAIN/health/ | python3 -m json.tool

# 10. Maintenance-Mode aus. Der Arbeits-Dump enthält KLARTEXT-Daten — sofort
#     löschen; den Tarball nach der Aufbewahrungsfrist (z. B. 1 Woche) entsorgen.
docker compose "${compose_files[@]}" exec -T web rm -f /media/anlaufstelle.maintenance
rm -f "$dump"
```

Meldet `pg_restore` eine Warnung bei `REFRESH MATERIALIZED VIEW core_statistics_event_flat`, sind die Daten trotzdem vollständig — danach einmal den MV-Refresh ausführen ([§ 11](#11-statistics-materialized-view)).

### 13.2 Rollback

Primär über den Volume-Tarball (exakter Byte-Stand der alten Major), alternativ über das verschlüsselte Backup (§ 2.3, Restore nach § 6.6). Der Tarball liegt in `upgrade-work/` (§ 13.1, Schritt 3). Das Beispiel nimmt an, dass `pg_volume` aus § 13.1 gesetzt ist; sonst vorher neu ermitteln.

```bash
cd /opt/anlaufstelle

# Vollständige Compose-Dateiliste in fester Reihenfolge (admin-guide.md § 1).
compose_files=(-f docker-compose.prod.yml)
[[ ! -f compose.legal.yml ]] || compose_files+=(-f compose.legal.yml)
[[ ! -f compose.override.yml ]] || compose_files+=(-f compose.override.yml)

# 1. Compose-Stand zurückdrehen: Image der alten Major + der damalige Mount
git checkout <vorheriger-stand> -- docker-compose.prod.yml

# 2. Volume der neuen Major entfernen, die alten Bytes wiederherstellen
docker compose "${compose_files[@]}" stop web cron
docker compose "${compose_files[@]}" rm -sf db
docker volume rm "$pg_volume" && docker volume create "$pg_volume"
docker run --rm -v "$pg_volume":/to -v "$PWD/upgrade-work":/from alpine \
    tar xzf /from/pgdata-pre-upgrade.tar.gz -C /to

# 3. Stack hochfahren + Health
docker compose "${compose_files[@]}" up -d
```

### 13.3 Vor dem Major-Sprung prüfen

- **Release-Notes der Ziel-Major** auf inkompatible Änderungen (Datenformat, entfernte Funktionen, geänderte Defaults — z. B. Data-Checksums per Default).
- **Image-Layout/`VOLUME`-Pfad** des neuen `postgres:<N>-alpine`: Ändert er sich, die Mounts in allen Compose-Dateien nachziehen.
- **Verfügbarkeit von `pg_dump`/`pg_restore` der Ziel-Major** im Image (Cross-Version-Dump-Kompatibilität).
- **Plattenplatz** für Arbeits-Dump und Volume-Tarball neben dem laufenden Volume.

---

## Kurzreferenz

```text
Backup erstellen          ./scripts/ops/backup.sh
Backup wiederherstellen   sudo ./scripts/ops/restore.sh <datei.sql.gz.enc>
Health-Check              curl -sf https://$DOMAIN/health/
Container-Status          docker compose -f docker-compose.prod.yml ps
Live-Logs                 docker compose -f docker-compose.prod.yml logs -f web
Migrations-Status         docker compose -f docker-compose.prod.yml exec web python manage.py showmigrations
Django Deploy-Check       docker compose -f docker-compose.prod.yml exec web python manage.py check --deploy
Retention Testlauf        docker compose -f docker-compose.prod.yml run --rm maintenance enforce_retention --dry-run
Snapshot Testlauf         docker compose -f docker-compose.prod.yml run --rm maintenance create_statistics_snapshots --dry-run
MV-Refresh                docker compose -f docker-compose.prod.yml run --rm maintenance refresh_statistics_view
DB-Rollen/RLS pruefen     docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles
Stack stoppen             docker compose -f docker-compose.prod.yml down
Stack starten             docker compose "${compose_files[@]}" up -d   (Dateiliste: § 1.2)
Lock-Files regenerieren   make deps-lock
Lock-File-Drift prüfen    make deps-check
```
