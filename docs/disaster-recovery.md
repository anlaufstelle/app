# Disaster-Recovery — Totalverlust-Wiederherstellung

> **Abgrenzung zum Restore-Drill:** Der quartalsweise [Restore-Drill](ops-runbook.md#66-backup-restore-drill-refs-720-739) beweist den *Happy Path* — „lässt sich das neueste Backup in eine Wegwerf-DB **auf demselben, laufenden Host** zurückspielen?". Dieses Dokument deckt den **Totalverlust** ab: Der Prod-Host ist weg (Hardware-Defekt, Ransomware, Provider-Ausfall, versehentliches `docker volume rm` ohne Tarball). Es beschreibt das Beschaffen eines Off-Site-Backups, die Wiederbeschaffung der Schlüssel, den Aufbau auf einem **fabrikfrischen Server** und konkrete RTO/RPO-Ziele.
>
> **Code ist die Wahrheitsquelle.** Jede Operation hier ist gegen die realen Skripte verifiziert: [`scripts/ops/backup.sh`](../scripts/ops/backup.sh), [`scripts/ops/restore.sh`](../scripts/ops/restore.sh), [`scripts/ops/restore-drill.sh`](../scripts/ops/restore-drill.sh), `dev-ops/deploy/backup.sh` (dev-only), `dev-ops/deploy/bootstrap.sh` (dev-only), [`deploy/postgres-init/01-app-role.sh`](../deploy/postgres-init/01-app-role.sh) und [`docker-compose.prod.yml`](../docker-compose.prod.yml). Stand: v0.14.0-Roadmap (Refs #1071 Block D).

---

## Inhaltsverzeichnis

1. [RTO / RPO — Zielwerte](#1-rto--rpo--zielwerte)
2. [Die zwei Backup-Welten](#2-die-zwei-backup-welten)
3. [Was ein Recovery braucht — Inventar](#3-was-ein-recovery-braucht--inventar)
4. [Schlüssel- und Secret-Wiederbeschaffung](#4-schlüssel--und-secret-wiederbeschaffung)
5. [Off-Site-Backup beschaffen](#5-off-site-backup-beschaffen)
6. [Wiederherstellung auf einem fabrikfrischen Host](#6-wiederherstellung-auf-einem-fabrikfrischen-host)
7. [Smoke-Verifikation nach Restore](#7-smoke-verifikation-nach-restore)
8. [Offene Punkte (noch nicht verdrahtet)](#8-offene-punkte-noch-nicht-verdrahtet)

---

## 1. RTO / RPO — Zielwerte

| Kennzahl | Zielwert | Begründung / Verankerung im Code |
|---|---|---|
| **RPO** (max. tolerierter Datenverlust) | **≤ 24 h** | Der Backup-Timer läuft täglich um **02:00** (`dev-ops/deploy/install-timers.sh`, dev-only: `OnCalendar="*-*-* 02:00:00"`). Zwischen zwei Läufen entstandene Daten sind bei Totalverlust verloren. Wer engere Ziele braucht, erhöht die Backup-Frequenz **und** die Off-Site-Sync-Frequenz. |
| **RPO Off-Site** (Datenverlust bei Host-Totalverlust) | **≤ 24 h**, sofern Off-Site-Sync aktiv | `scripts/ops/backup.sh` synct **nach jeder Rotation** in `BACKUP_OFFSITE_TARGET`. Ohne konfiguriertes Off-Site-Ziel ist der RPO bei Host-Totalverlust **unendlich** (lokale Backups gehen mit dem Host verloren) — siehe [§8](#8-offene-punkte-noch-nicht-verdrahtet). |
| **RTO** (Zeit bis Service wieder online auf frischem Host) | **Richtwert 2–4 h** | Setzt sich zusammen aus: Server provisionieren + `bootstrap.sh` (~20–30 min) · Off-Site-Backup herunterladen (datenmengenabhängig) · DB- + Medien-Restore (~10–30 min bei mittlerer Datenmenge) · Smoke-Verifikation (~15 min). **Kein vertraglich zugesichertes SLA** — Richtwert für einen geübten Operator mit verfügbaren Schlüsseln. |
| **RTO ohne Schlüssel** | **∞ (nicht wiederherstellbar)** | Ohne `ENCRYPTION_KEY`/`ENCRYPTION_KEYS` sind die verschlüsselten Feld-/Datei-Inhalte **dauerhaft unlesbar**; ohne `BACKUP_ENCRYPTION_KEY` ist das Backup selbst nicht entschlüsselbar. Die Schlüssel-Eskrow-Strategie ([§4](#4-schlüssel--und-secret-wiederbeschaffung)) ist damit der kritischste RTO-Faktor. |

> **Die RTO-Annahme steht und fällt mit zwei Voraussetzungen:** (a) ein aktuelles Off-Site-Backup ist erreichbar und (b) die Schlüssel sind aus einem **vom Prod-Host getrennten** Speicher wiederbeschaffbar. Sind beide erfüllt, ist Recovery Routine. Fehlt eines, ist es kein Recovery mehr, sondern Datenverlust.

---

## 2. Die zwei Backup-Welten

Das Projekt hat **zwei getrennte, nicht austauschbare** Backup-/Restore-Pfade. Welcher für ein Recovery relevant ist, hängt davon ab, welcher auf der verlorenen Instanz lief. Die Details (inkl. Kompatibilitätsmatrix) stehen in [ops-runbook.md §6.6](ops-runbook.md#66-backup-restore-drill-refs-720-739) — hier nur die Recovery-relevante Kurzfassung:

| | **Welt A — `dev-ops/deploy/backup.sh`** | **Welt B — `scripts/ops/backup.sh`** |
|---|---|---|
| Läuft auf | `dev.anlaufstelle.app` (Hetzner, plain Docker Compose) | Prod-Schema (`docker-compose.prod.yml`) |
| Dump-Format | `pg_dump -Fc` (custom) | plain SQL + `gzip` |
| Verschlüsselung | AES-256-CBC (`openssl enc -pbkdf2`) | AES-256-CBC **+ HMAC-SHA256-Sidecar** ([`_backup_common.sh`](../scripts/ops/_backup_common.sh), Refs #1024) |
| Dateien | `dump-*.pgc.enc` | `*.sql.gz.enc` **+** `*.sql.gz.enc.hmac` |
| Medien-Backup | — (nur DB) | `*_media.tar.gz.enc` (+ `.hmac`), `tar` aus dem `web`-Container |
| Restore-Tool | `pg_restore` | `psql` über [`scripts/ops/restore.sh`](../scripts/ops/restore.sh) |
| Restore-Drill | [`restore-drill.sh`](../scripts/ops/restore-drill.sh) zielt **genau hierauf** | nicht vom Drill abgedeckt — Verify via `backup.sh --verify` |
| Ablage (Default) | `$BACKUP_DIR` = `/var/backups/anl` | `<projekt>/backups/{daily,weekly,monthly}` |
| Off-Site-Sync | — (kein Off-Site im Skript) | `BACKUP_OFFSITE_TARGET` (rclone / `s3://` / scp) |

> **Konsequenz für Recovery:** Ein `dump-*.pgc.enc` wird mit `pg_restore` zurückgespielt, ein `*.sql.gz.enc` mit `psql` (bzw. `restore.sh`) — und nur die Welt-B-Backups tragen einen Medien-Tarball **und** ein Off-Site-Ziel. Ein Cross-Restore schlägt fehl (anderes Format, fehlender HMAC-Sidecar). Vor dem Restore also zuerst feststellen, **welche Dateinamen** im Off-Site-Speicher liegen.

---

## 3. Was ein Recovery braucht — Inventar

Ein vollständiges Recovery rekonstruiert **drei** Dinge. Fehlt eines, ist die Wiederherstellung unvollständig oder unmöglich:

1. **Die verschlüsselten Backup-Artefakte** (Off-Site, siehe [§5](#5-off-site-backup-beschaffen)):
 - DB-Dump (`*.sql.gz.enc` + `.hmac` **oder** `dump-*.pgc.enc`)
 - Medien-Tarball (`*_media.tar.gz.enc` + `.hmac`) — **nur Welt B**. Ohne ihn fehlen alle Datei-Anhänge aus dem Encrypted File Vault.
2. **Die Secrets/Schlüssel** ([§4](#4-schlüssel--und-secret-wiederbeschaffung)) — vor allem `ENCRYPTION_KEY(S)` und `BACKUP_ENCRYPTION_KEY`.
3. **Die Deployment-Artefakte** — der Git-Stand bzw. das Release-Paket (Compose-Files, `deploy/postgres-init/`, `scripts/ops/`) und das Container-Image (`ghcr.io/anlaufstelle/app:<tag>`). Der Code ist öffentlich (AGPL) und damit immer wiederbeschaffbar — er ist **nicht** der Engpass.

---

## 4. Schlüssel- und Secret-Wiederbeschaffung

Alle Secrets leben zur Laufzeit in der `.env` des Prod-Hosts (`chmod 600`). Bei Totalverlust ist diese Datei weg — die Werte müssen aus einem **getrennten** Speicher kommen. Maßgeblich ist [`.env.example`](../.env.example); die folgende Tabelle priorisiert nach Recovery-Kritikalität.

### 4.1 Kritisch — ohne diese ist Recovery unmöglich

| Variable | Rolle im Recovery | Code-Beleg |
|---|---|---|
| `BACKUP_ENCRYPTION_KEY` | Entschlüsselt das Backup selbst (`openssl enc -d`) **und** leitet den HMAC-Verify-Key ab. **Ohne ihn ist das Backup wertloser Chiffretext.** | [`backup.sh`](../scripts/ops/backup.sh) L37/L132, [`restore.sh`](../scripts/ops/restore.sh) L69, [`_backup_common.sh`](../scripts/ops/_backup_common.sh) (HMAC-Key-Ableitung) |
| `ENCRYPTION_KEY` **oder** `ENCRYPTION_KEYS` | Fernet-Key(s) für die **Feld- und Datei-Verschlüsselung** in der DB/im Vault. Ein wiederhergestelltes Backup ist ohne den passenden Key lesbar als Tabellenstruktur, aber alle verschlüsselten Inhalte bleiben unentschlüsselbar. Bei Key-Rotation müssen **alle je benutzten** Keys in `ENCRYPTION_KEYS` stehen (MultiFernet, erster Key = aktiv). | [`settings/base.py`](../src/anlaufstelle/settings/base.py) L278–279, Pflichtprüfung in [`settings/prod.py`](../src/anlaufstelle/settings/prod.py) L139 |

> **MultiFernet-Falle:** Wurde der Feld-Key je rotiert, enthält das Backup Werte, die mit **mehreren** Generationen verschlüsselt sind. `ENCRYPTION_KEYS` muss dann **lückenlos alle** alten Keys enthalten, sonst sind ältere Datensätze nach dem Restore unlesbar. Die Key-Historie gehört deshalb mit ins Eskrow.

### 4.2 Wichtig — ohne diese startet der Stack nicht sauber

| Variable | Rolle | Code-Beleg |
|---|---|---|
| `POSTGRES_BOOTSTRAP_PASSWORD` | Passwort des Bootstrap-Superusers `postgres` im frischen Cluster; das Init-Script legt damit die App-/Admin-Rolle an. | [`docker-compose.prod.yml`](../docker-compose.prod.yml) L12 (`POSTGRES_PASSWORD: ${POSTGRES_BOOTSTRAP_PASSWORD}`) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | App-Login (NOSUPERUSER, NOBYPASSRLS) → im Container `APP_DB_USER`/`APP_DB_PASSWORD`. Muss beim Restore wieder denselben Rollennamen ergeben, sonst passen Ownership/Grants aus dem Dump nicht. | Compose L14–15, [`01-app-role.sh`](../deploy/postgres-init/01-app-role.sh) |
| `POSTGRES_ADMIN_USER` / `POSTGRES_ADMIN_PASSWORD` | Admin-Rolle (NOSUPERUSER, BYPASSRLS) für Migrationen/Seed/Retention → `ADMIN_DB_USER`/`ADMIN_DB_PASSWORD`. | Compose L16–17 |
| `DJANGO_SECRET_KEY` | Session-/CSRF-/Signing-Key. Ein **neuer** Wert ist technisch zulässig (invalidiert nur bestehende Sessions/Signaturen); für saubere Forensik den alten bevorzugen. | [`settings/prod.py`](../src/anlaufstelle/settings/prod.py) L38 |
| `DJANGO_AUDIT_HASH_KEY` | HMAC-Key für pseudonymisierte AuditLog-Einträge. Ein neuer Wert macht **alte** Audit-Hashes nicht mehr nachvollziehbar — für DSGVO-Beweiskraft den alten wiederbeschaffen. | [`.env.example`](../.env.example) L14 |

> **`POSTGRES_USER`/`POSTGRES_ADMIN_USER` sind keine Geheimnisse, aber load-bearing.** Der Rollenname muss identisch zum verlorenen Setup sein, weil der Dump `OWNER`/`GRANT`-Klauseln auf genau diese Rollennamen enthält ([`01-app-role.sh`](../deploy/postgres-init/01-app-role.sh) macht den App-User zum DB-Owner). Andere Passwörter sind ok; andere **Rollennamen** brechen den Restore.

### 4.3 Optional

`SENTRY_DSN`, `EMAIL_HOST*`, `BACKUP_OFFSITE_TARGET`, `CLAMAV_*`, `ALLOWED_HOSTS`, `DOMAIN`, `TRUSTED_PROXY_HOPS` — für den Betrieb nötig, aber aus `.env.example` + bekannter Infrastruktur rekonstruierbar; kein Datenverlust-Risiko.

### 4.4 Wo die Schlüssel liegen sollten — Eskrow

> **Stand: noch nicht verdrahtet — siehe [§8](#8-offene-punkte-noch-nicht-verdrahtet).** Es gibt **kein** automatisches Key-Escrow im Code. Organisatorisch **muss** mindestens `BACKUP_ENCRYPTION_KEY` + die vollständige `ENCRYPTION_KEYS`-Historie an einem vom Prod-Host **physisch/logisch getrennten** Ort liegen (Passwort-Manager des Trägers, versiegelter Umschlag im Safe, o. Ä.). Ein Schlüssel, der nur in der `.env` desselben Hosts liegt, der verloren geht, schützt nicht vor Totalverlust — er **garantiert** den Datenverlust.

---

## 5. Off-Site-Backup beschaffen

Off-Site-Sync ist **nur in Welt B** (`scripts/ops/backup.sh`) implementiert und **nur aktiv, wenn `BACKUP_OFFSITE_TARGET` gesetzt war** (siehe [ops-runbook §6.6a](ops-runbook.md#66a-off-site-backup-sync-refs-738) und [§8](#8-offene-punkte-noch-nicht-verdrahtet)). Das Skript unterstützt drei Zielformate; das Retrieval ist jeweils die Umkehrung des `copy`/`sync`:

```bash
# Variante rclone (S3/B2/R2/Storj/...) — BACKUP_OFFSITE_TARGET=rclone:remote:bucket/path
rclone copy remote:bucket/path/daily ./restore-src --include "*.enc" --include "*.hmac"

# Variante S3 — BACKUP_OFFSITE_TARGET=s3://bucket/path
aws s3 sync s3://bucket/path/daily ./restore-src --exclude "*" --include "*.enc" --include "*.hmac"

# Variante scp — BACKUP_OFFSITE_TARGET=user@host:/path
scp "user@host:/path/*.enc" "user@host:/path/*.hmac" ./restore-src/
```

**Immer DB- *und* (bei Welt B) Medien-Artefakt samt `.hmac`-Sidecar holen.** Ohne den Sidecar verweigert `restore.sh` den Restore (`backup_verify_hmac` bricht ab — [`_backup_common.sh`](../scripts/ops/_backup_common.sh) L33). Das jüngste DB-Backup heißt `anlaufstelle_<timestamp>.sql.gz.enc`, der dazugehörige Medien-Tarball `anlaufstelle_<timestamp>_media.tar.gz.enc`.

> **Object-Lock prüfen:** [ops-runbook §6.6a](ops-runbook.md#66a-off-site-backup-sync-refs-738) empfiehlt eine Write-Once-/Object-Lock-Policy am Bucket. Beim Ransomware-Szenario ist das der Unterschied zwischen „Backups noch da" und „Backups mitverschlüsselt".

---

## 6. Wiederherstellung auf einem fabrikfrischen Host

Szenario: leerer Server (Hetzner CX22 o. vergleichbar, Debian 13), nichts vom alten Host außer den beschafften Backup-Artefakten ([§5](#5-off-site-backup-beschaffen)) und den Secrets ([§4](#4-schlüssel--und-secret-wiederbeschaffung)).

Dieser Ablauf entspricht dem regulären Provisioning (`dev-ops/deploy/bootstrap.sh`, dev-only) plus Restore. Er beschreibt das **Welt-B/Prod**-Schema (`docker-compose.prod.yml` + `scripts/ops/restore.sh`); für das `dev.anlaufstelle.app`-Schema (Welt A, `pg_restore`) gilt der Restore-Befehl aus dem Skript-Header von `dev-ops/deploy/backup.sh` (dev-only) bzw. [ops-runbook §2.3](ops-runbook.md#23-vollstaendiger-rollback-db-restore).

### Schritt 1 — Host härten + Docker installieren

```bash
# bootstrap.sh installiert: docker-ce, ufw + fail2ban + unattended-upgrades,
# Service-User 'anlaufstelle', SSH-Hardening, ufw-docker, Log-Rotation,
# Verzeichnis-Layout /opt/anlaufstelle + /var/backups/anl.
scp dev-ops/deploy/bootstrap.sh root@<neuer-host>:/root/
ssh root@<neuer-host> bash /root/bootstrap.sh
```

> `bootstrap.sh` installiert **nicht** die Hintergrundjob-Timer und legt **keine** `.env` an — das macht regulär `deploy-dev.sh`/`install-timers.sh` bzw. der Operator von Hand.

### Schritt 2 — Code/Release + `.env` einspielen

```bash
# Repo bzw. Release-Paket nach /opt/anlaufstelle (enthält Compose-Files,
# deploy/postgres-init/, scripts/ops/).
git clone https://github.com/anlaufstelle/app.git /opt/anlaufstelle   # oder Release-Tarball

# .env aus den eskrowten Secrets rekonstruieren (§4). chmod 600 ist Pflicht.
cd /opt/anlaufstelle
cp .env.example .env && chmod 600 .env
# -> POSTGRES_*, ENCRYPTION_KEY(S), BACKUP_ENCRYPTION_KEY, DJANGO_*, ALLOWED_HOSTS, DOMAIN eintragen
```

### Schritt 3 — Frisches DB-Cluster mit korrekten Rollen hochfahren

```bash
# Nur die DB starten. Das Init-Script 01-app-role.sh legt im frischen Cluster
# die App-Rolle (NOSUPERUSER NOBYPASSRLS) + Admin-Rolle (NOSUPERUSER BYPASSRLS)
# an — KRITISCH: Rollen müssen existieren, BEVOR der Dump Ownership/Grants
# wiederherstellt (ADR-020, ADR-005).
docker compose -f docker-compose.prod.yml up -d db
docker compose -f docker-compose.prod.yml logs db | grep -icE 'error|fatal'   # erwartet: 0
```

> Das Init-Script läuft **nur bei einem leeren Datenverzeichnis** (`/docker-entrypoint-initdb.d`). Auf einem fabrikfrischen Host ist das Volume leer — die Rollen werden also angelegt. (Wer auf ein nicht-leeres Volume restored, muss die Rollen von Hand anlegen.)

### Schritt 4 — DB-Backup entschlüsseln + wiederherstellen

```bash
# restore.sh prüft den HMAC VOR dem Entschlüsseln, entschlüsselt, entpackt
# und spielt via psql in $POSTGRES_DB ein. Fragt interaktiv (ja/nein), weil
# es die Ziel-DB überschreibt. .hmac-Sidecar muss neben der .enc liegen.
./scripts/ops/restore.sh ./restore-src/anlaufstelle_<timestamp>.sql.gz.enc \
                         ./restore-src/anlaufstelle_<timestamp>_media.tar.gz.enc
```

`restore.sh` erledigt in einem Lauf: HMAC-Verify (DB) → `openssl enc -d | gunzip | psql` → Medien-Inhalt löschen → HMAC-Verify (Medien) → `tar -x` in `/data` des `web`-Containers ([`restore.sh`](../scripts/ops/restore.sh) L65–93). Der `BACKUP_ENCRYPTION_KEY` aus der `.env` wird automatisch geladen.

> **Welt A (dev / `pg_restore`-Format):** Statt `restore.sh` der Notfall-Befehl aus dem Skript-Header:
> ```bash
> openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY \
>   -in dump-<timestamp>.pgc.enc \
>   | docker compose -f docker-compose.dev.yml --env-file .env.dev exec -T db \
>     pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists
> ```

### Schritt 5 — Migrationen + Stack hochfahren

```bash
# Migrate als One-Shot. docker-migrate.sh verifiziert vorab via check_db_roles,
# dass App-Rolle = NOSUPERUSER/NOBYPASSRLS und Admin = BYPASSRLS (Refs #1002).
docker compose -f docker-compose.prod.yml run --rm --entrypoint=/app/docker-migrate.sh web

# Vollständigen Stack hochfahren (web, caddy, ggf. clamav).
docker compose -f docker-compose.prod.yml up -d

# Hintergrundjob-Timer (Backup/Retention/Snapshots/Breach/MV-Refresh) installieren,
# sonst läuft das nächste Backup nicht (Refs #980).
sudo bash dev-ops/deploy/install-timers.sh
```

Danach weiter mit der [Smoke-Verifikation](#7-smoke-verifikation-nach-restore).

---

## 7. Smoke-Verifikation nach Restore

Reihenfolge: erst Infrastruktur-Checks (DB-Rollen, RLS), dann Health-Endpoint, dann Stichproben. Bricht ein früher Check, hat es keinen Sinn, weiterzulaufen.

```bash
# 1) DB-Rollen-Gate — App-Rolle ohne Superuser/BYPASSRLS, Admin mit BYPASSRLS.
docker compose -f docker-compose.prod.yml run --rm --entrypoint python web manage.py check_db_roles   # Exit 0

# 2) RLS aktiv? (Sicherheits-Safety-Net muss nach Restore noch greifen.)
docker compose -f docker-compose.prod.yml exec -T db sh -c \
  'psql -U postgres -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity"'   # > 0

# 3) Health-Endpoint (Liveness + Detail). Erwartet HTTP 200 + "status":"ok".
curl -sf https://$DOMAIN/health/ | python3 -m json.tool

# 4) Migrationsstand vollständig?
docker compose -f docker-compose.prod.yml exec web python manage.py showmigrations | grep -c '\[ \]'   # erwartet: 0

# 5) Daten-Stichprobe — sind Nutzdaten wirklich da?
docker compose -f docker-compose.prod.yml exec -T db \
  psql -U $POSTGRES_USER -d $POSTGRES_DB -tAc "SELECT count(*) FROM core_facility;"
```

**Verschlüsselungs-Roundtrip — der entscheidende Check.** Der Health-Endpoint führt intern einen Fernet-Encrypt/Decrypt-Roundtrip aus und meldet `"encryption_key": "ok"` nur, wenn `ENCRYPTION_KEY(S)` zur wiederhergestellten Datenlage passt ([`health.py`](../src/core/views/health.py) `_check_encryption_key`, L62–78). Ist hier `"error"`, kippt der Gesamtstatus auf `error`/HTTP 503 — **die wahrscheinlichste Ursache nach einem Restore ist ein fehlender/falscher (rotierter) Feld-Schlüssel** ([§4.1](#41-kritisch--ohne-diese-ist-recovery-unmöglich)). Zusätzlich stichprobenhaft einen Datensatz mit verschlüsseltem Feld in der App öffnen (z. B. eine Klient:innen-Detailseite), um zu sehen, dass Klartext entschlüsselt wird.

Felder/Codes des Endpoints im Detail: [monitoring-guide.md](monitoring-guide.md).

**Restore dokumentieren (DSGVO Art. 32 Abs. 1 lit. c):** Nach erfolgreichem Recovery den Wiederherstellungs-Nachweis ins AuditLog schreiben — analog zum Restore-Drill:

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py mark_restore_verified --note "Disaster-Recovery auf frischem Host $(date -u +%Y-%m-%d)"
```

Das setzt den Compliance-Check „Restore-Test" im Dashboard (`/system/compliance/`) auf grün ([ops-runbook §6.6](ops-runbook.md#66-backup-restore-drill-refs-720-739), Refs #919).

---

## 8. Offene Punkte (noch nicht verdrahtet)

Ehrliche Bestandsaufnahme — diese Teile sind **organisatorisch**, nicht im Code gelöst (Stand v0.14.0):

- **`offen` — Key-Escrow ist nicht automatisiert.** Es gibt keinen Code-Pfad, der `BACKUP_ENCRYPTION_KEY`/`ENCRYPTION_KEYS` an einen getrennten Ort spiegelt. Die Schlüssel liegen ausschließlich in der `.env` des Prod-Hosts. **Ohne ein gepflegtes, host-getrenntes Eskrow ist ein Totalverlust = endgültiger Datenverlust** ([§4.4](#44-wo-die-schlüssel-liegen-sollten--eskrow)). → Organisatorisch festlegen: wer hält die Schlüssel wo, und wie wird die Key-Rotation dort nachgezogen.

- **`offen` — Off-Site nur für Welt B, und nur wenn konfiguriert.** `dev-ops/deploy/backup.sh` (Welt A, das auf `dev.anlaufstelle.app` läuft) hat **keinen** Off-Site-Sync. `scripts/ops/backup.sh` (Welt B) hat einen, aber nur aktiv bei gesetztem `BACKUP_OFFSITE_TARGET`. Ist keines konfiguriert, überlebt **kein** Backup den Host-Totalverlust. → Vor Produktivbetrieb `BACKUP_OFFSITE_TARGET` setzen **und** das erste Off-Site-Retrieval einmal proben.

- **`offen` — `restic` ist installiert, aber ungenutzt.** `dev-ops/deploy/bootstrap.sh` (dev-only) L30 installiert `restic`, doch **kein** Skript verwendet es. Off-Site läuft (in Welt B) über rclone/aws/scp, nicht über restic. Die restic-Installation ist derzeit toter Ballast bzw. Vorbereitung für ein noch nicht gebautes restic-basiertes Off-Site/Eskrow. → Entweder einen restic-Pfad bauen oder die Installation aus dem Bootstrap entfernen.

- **`offen` — Recovery auf fabrikfrischem Host ist nicht end-to-end geprobt.** Der [Restore-Drill](ops-runbook.md#66-backup-restore-drill-refs-720-739) testet Restore **auf dem laufenden Host** in eine Wegwerf-DB; das PG-16→18-Upgrade ([ops-runbook §13](ops-runbook.md#13-postgresql-major-upgrade-16--18)) testet dump→frisches-Cluster→restore lokal. Der **vollständige** DR-Pfad aus [§6](#6-wiederherstellung-auf-einem-fabrikfrischen-host) (frischer Server + Off-Site-Download + Schlüssel-aus-Eskrow + Restore) ist in dieser Form **noch nicht als Drill durchgespielt**. → Einmal jährlich als „echten" DR-Drill auf einem Wegwerf-Server üben und das Ergebnis hier/in einem Issue festhalten.

- **RTO/RPO sind Richtwerte, kein SLA.** Die Zahlen in [§1](#1-rto--rpo--zielwerte) sind aus den Cron-Frequenzen und realistischen Restore-Dauern abgeleitet, nicht gemessen und nicht vertraglich zugesichert. Ein DR-Drill ([oben](#8-offene-punkte-noch-nicht-verdrahtet)) würde sie validieren.

---

## Siehe auch

- [ops-runbook.md §6.6 — Backup-Restore-Drill](ops-runbook.md#66-backup-restore-drill-refs-720-739) (Happy-Path, Kompatibilitätsmatrix der zwei Welten)
- [ops-runbook.md §6.6a — Off-Site-Backup-Sync](ops-runbook.md#66a-off-site-backup-sync-refs-738)
- [ops-runbook.md §13 — PostgreSQL-Major-Upgrade (16→18)](ops-runbook.md#13-postgresql-major-upgrade-16--18) (dump→frisches-Cluster→restore, derselbe Mechanismus wie DR)
- [monitoring-guide.md — `/health/`-Felder und Status-Codes](monitoring-guide.md)
- [coolify-deployment.md — Environment-Variablen](coolify-deployment.md)
- [.env.example — kanonische Secret-Liste](../.env.example)
