# Disaster-Recovery — Totalverlust-Wiederherstellung

> **Abgrenzung zum Restore-Drill:** Der quartalsweise [Restore-Drill](ops-runbook.md#66-backup-restore-drill-refs-720-739) beweist den *Happy Path* — „lässt sich das neueste Backup in eine Wegwerf-DB **auf demselben, laufenden Host** zurückspielen?". Dieses Dokument deckt den **Totalverlust** ab: Der Prod-Host ist weg (Hardware-Defekt, Ransomware, Provider-Ausfall, versehentliches `docker volume rm` ohne Tarball). Es beschreibt das Beschaffen eines Off-Site-Backups, die Wiederbeschaffung der Schlüssel, den Aufbau auf einem **fabrikfrischen Server** und konkrete RTO/RPO-Ziele.
>
> **Code ist die Wahrheitsquelle.** Jede Operation hier ist gegen die realen Skripte verifiziert: [`scripts/ops/backup.sh`](../scripts/ops/backup.sh), [`scripts/ops/restore.sh`](../scripts/ops/restore.sh), [`scripts/ops/restore-drill.sh`](../scripts/ops/restore-drill.sh), `dev-ops/deploy/bootstrap.sh` (dev-only, Server-Provisioning in [§6](#6-wiederherstellung-auf-einem-fabrikfrischen-host)), [`deploy/postgres-init/01-app-role.sh`](../deploy/postgres-init/01-app-role.sh) und [`docker-compose.prod.yml`](../docker-compose.prod.yml). Stand: v0.14.0-Roadmap (Refs #1071 Block D).

---

## Inhaltsverzeichnis

1. [RTO / RPO — Zielwerte](#1-rto--rpo--zielwerte)
2. [Backup-Format](#2-backup-format)
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

## 2. Backup-Format

[`scripts/ops/backup.sh`](../scripts/ops/backup.sh) sichert das Prod-Schema (`docker-compose.prod.yml`). Details (inkl. Restore-Drill) stehen in [ops-runbook.md §6.6](ops-runbook.md#66-backup-restore-drill-refs-720-739) — hier nur die Recovery-relevante Kurzfassung:

| | **`scripts/ops/backup.sh`** |
|---|---|
| Dump-Format | plain SQL + `gzip` |
| Verschlüsselung | AES-256-CBC **+ HMAC-SHA256-Sidecar** ([`_backup_common.sh`](../scripts/ops/_backup_common.sh), Refs #1024) |
| Dateien | `*.sql.gz.enc` **+** `*.sql.gz.enc.hmac` |
| Medien-Backup | `*_media.tar.gz.enc` (+ `.hmac`), `tar` aus dem `web`-Container |
| Restore-Tool | `psql` über [`scripts/ops/restore.sh`](../scripts/ops/restore.sh) |
| Restore-Drill | [`restore-drill.sh --prod`](../scripts/ops/restore-drill.sh) (seit #1336: HMAC-Check + `gunzip \| psql` + Zeilenabgleich gegen Live-DB); `backup.sh --verify` bleibt leichtgewichtige Alternative |
| Ablage (Default) | `<projekt>/backups/{daily,weekly,monthly}` |
| Off-Site-Sync | `BACKUP_OFFSITE_TARGET` (rclone / `s3://` / scp) |

---

## 3. Was ein Recovery braucht — Inventar

Ein vollständiges Recovery rekonstruiert **drei** Dinge. Fehlt eines, ist die Wiederherstellung unvollständig oder unmöglich:

1. **Die verschlüsselten Backup-Artefakte** (Off-Site, siehe [§5](#5-off-site-backup-beschaffen)):
   - DB-Dump (`*.sql.gz.enc` + `.hmac`)
   - Medien-Tarball (`*_media.tar.gz.enc` + `.hmac`). Ohne ihn fehlen alle Datei-Anhänge aus dem Encrypted File Vault.
2. **Die Secrets/Schlüssel** ([§4](#4-schlüssel--und-secret-wiederbeschaffung)) — vor allem `ENCRYPTION_KEY(S)` und `BACKUP_ENCRYPTION_KEY`.
3. **Die Deployment-Artefakte** — der Git-Stand bzw. das Release-Paket (Compose-Files, `deploy/postgres-init/`, `scripts/ops/`) und das Container-Image (`ghcr.io/anlaufstelle/app:<APP_VERSION-Tag>`). Der Code ist öffentlich (AGPL) und damit immer wiederbeschaffbar — er ist **nicht** der Engpass.

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

> **`POSTGRES_USER`/`POSTGRES_ADMIN_USER` sind keine Geheimnisse, aber load-bearing.** Der Rollenname muss identisch zum verlorenen Setup sein, weil der Dump `OWNER`/`GRANT`-Klauseln auf genau diese Rollennamen enthält (seit A1.4 ist die **Admin-Rolle** DB-/Objekt-Owner, die App-Rolle hat nur DML — [`01-app-role.sh`](../deploy/postgres-init/01-app-role.sh)). Andere Passwörter sind ok; andere **Rollennamen** brechen den Restore.
>
> **Container-Grenze (Refs #1962):**
> Nach dem Wiederherstellen bleiben Admin-/Bootstrap-Passwort zwar in der
> Betreiber-`.env`, werden aber nie an `web` durchgereicht. DB-Init bekommt sie
> explizit; Migrationen und Verifikation laufen als kurzlebiger
> `maintenance`-Service. Ein Restore darf diese Trennung nicht durch einen
> manuellen `-e … web`-Override rueckgaengig machen.
>
> **Nach jeder DB-Passwortrotation (Refs #1964):**
> [`scripts/ops/rotate-db-passwords.sh`](../scripts/ops/rotate-db-passwords.sh)
> ersetzt `POSTGRES_PASSWORD`, `POSTGRES_ADMIN_PASSWORD` und
> `POSTGRES_BOOTSTRAP_PASSWORD` gemeinsam. Noch im selben Wartungsfenster alle
> drei Werte im host-getrennten Eskrow/Passwortmanager ersetzen und den alten
> Satz als widerrufen markieren. Sonst scheitert die Notfall-Strecke am
> vorhandenen Cluster mit einem veralteten Login, obwohl das Backup selbst
> intakt ist. Das Ausführungs- und Negativtest-Protokoll darf nur Rollenname,
> Zeitpunkt und Exit-Code enthalten — niemals einen alten oder neuen Wert.

### 4.3 Optional

`SENTRY_DSN`, `EMAIL_HOST*`, `BACKUP_OFFSITE_TARGET`, `CLAMAV_*`, `ALLOWED_HOSTS`, `DOMAIN`, `TRUSTED_PROXY_HOPS` — für den Betrieb nötig, aber aus `.env.example` + bekannter Infrastruktur rekonstruierbar; kein Datenverlust-Risiko.

### 4.4 Wo die Schlüssel liegen sollten — Eskrow

> **Stand: noch nicht verdrahtet — siehe [§8](#8-offene-punkte-noch-nicht-verdrahtet).** Es gibt **kein** automatisches Key-Escrow im Code. Organisatorisch **müssen** mindestens `BACKUP_ENCRYPTION_KEY`, die vollständige `ENCRYPTION_KEYS`-Historie und der **aktuelle** Satz `POSTGRES_PASSWORD`/`POSTGRES_ADMIN_PASSWORD`/`POSTGRES_BOOTSTRAP_PASSWORD` an einem vom Prod-Host **physisch/logisch getrennten** Ort liegen (Passwort-Manager des Trägers, versiegelter Umschlag im Safe, o. Ä.). Ein Schlüssel, der nur in der `.env` desselben Hosts liegt, der verloren geht, schützt nicht vor Totalverlust — er **garantiert** den Datenverlust. Die DB-Passwörter sind auf einem fabrikfrischen Cluster zwar technisch neu wählbar; für Diagnose, Rotation und Recovery am vorhandenen Cluster muss der Eskrow aber dem aktuellen Login-Stand entsprechen.

---

## 5. Off-Site-Backup beschaffen

Off-Site-Sync ist mit `scripts/ops/backup.sh` implementiert und **nur aktiv, wenn `BACKUP_OFFSITE_TARGET` gesetzt war** (siehe [ops-runbook §6.6a](ops-runbook.md#66a-off-site-backup-sync-refs-738) und [§8](#8-offene-punkte-noch-nicht-verdrahtet)). Das Skript unterstützt drei Zielformate; das Retrieval ist jeweils die Umkehrung des `copy`/`sync`:

```bash
# Variante rclone (S3/B2/R2/Storj/...) — BACKUP_OFFSITE_TARGET=rclone:remote:bucket/path
rclone copy remote:bucket/path/daily ./restore-src --include "*.enc" --include "*.hmac"

# Variante S3 — BACKUP_OFFSITE_TARGET=s3://bucket/path
aws s3 sync s3://bucket/path/daily ./restore-src --exclude "*" --include "*.enc" --include "*.hmac"

# Variante scp — BACKUP_OFFSITE_TARGET=user@host:/path
scp "user@host:/path/*.enc" "user@host:/path/*.hmac" ./restore-src/
```

**Immer DB- *und* Medien-Artefakt samt `.hmac`-Sidecar holen.** Ohne den Sidecar verweigert `restore.sh` den Restore (`backup_verify_hmac` bricht ab — [`_backup_common.sh`](../scripts/ops/_backup_common.sh) L33). Das jüngste DB-Backup heißt `anlaufstelle_<timestamp>.sql.gz.enc`, der dazugehörige Medien-Tarball `anlaufstelle_<timestamp>_media.tar.gz.enc`.

> **Object-Lock prüfen:** [ops-runbook §6.6a](ops-runbook.md#66a-off-site-backup-sync-refs-738) empfiehlt eine Write-Once-/Object-Lock-Policy am Bucket. Beim Ransomware-Szenario ist das der Unterschied zwischen „Backups noch da" und „Backups mitverschlüsselt".

---

## 6. Wiederherstellung auf einem fabrikfrischen Host

Szenario: leerer Server (Hetzner CX22 o. vergleichbar, Debian 13), nichts vom alten Host außer den beschafften Backup-Artefakten ([§5](#5-off-site-backup-beschaffen)) und den Secrets ([§4](#4-schlüssel--und-secret-wiederbeschaffung)).

Dieser Ablauf entspricht dem regulären Provisioning (`dev-ops/deploy/bootstrap.sh`, dev-only) plus Restore mit `docker-compose.prod.yml` + `scripts/ops/restore.sh`.

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

[`restore.sh`](../scripts/ops/restore.sh) erledigt in einem Lauf: HMAC-Verify der DB und, falls übergeben, des Medienarchivs → Bestätigungsabfrage → erneuter DB-HMAC-Verify → `openssl enc -d | gunzip | psql` → erneuter Medien-HMAC-Verify → Medien-Inhalt löschen → Medienarchiv per `tar -x` in `/data` des `web`-Containers entpacken. Damit sind beide Sidecars geprüft, bevor die erste destruktive Aktion beginnt, und das Prüf-Nutzungs-Fenster bleibt klein. Bricht `tar -x` ab, ist der Medienbestand unvollständig; Ursache beheben und den vollständigen Medien-Restore wiederholen. Der `BACKUP_ENCRYPTION_KEY` aus der `.env` wird automatisch geladen.

### Schritt 5 — Migrationen + Stack hochfahren

```bash
# Migrate als One-Shot. docker-migrate.sh verifiziert vorab via check_db_roles,
# dass App-Rolle = NOSUPERUSER/NOBYPASSRLS und Admin = BYPASSRLS (Refs #1002).
docker compose -f docker-compose.prod.yml run --rm --entrypoint=/app/docker-migrate.sh maintenance

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
docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles   # Exit 0

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

- **`entschieden` — keine personelle Escrow-Trennung im Solo-Maintainer-Modell.** #1477 war ein Governance-Issue und ist geschlossen; solange keine Vertretung benannt ist, gibt es keine Übergabe an eine zweite Person. Es gibt weiterhin keinen Code-Pfad, der `BACKUP_ENCRYPTION_KEY`/`ENCRYPTION_KEYS` an einen getrennten Ort spiegelt. Für Recovery und Drill ist deshalb die vom Maintainer verwahrte, vom Prod-Host getrennte `.env`-Kopie die Schlüsselquelle. Fehlt diese Kopie nach einem Host-Totalverlust, bleiben die Backups endgültig unlesbar ([§4.4](#44-wo-die-schlüssel-liegen-sollten--eskrow)).

- **`offen` — Off-Site nur wenn konfiguriert.** `scripts/ops/backup.sh` hat Off-Site-Sync, aber nur aktiv bei gesetztem `BACKUP_OFFSITE_TARGET`. Ist keines konfiguriert, überlebt **kein** Backup den Host-Totalverlust. → Vor Produktivbetrieb `BACKUP_OFFSITE_TARGET` setzen **und** das erste Off-Site-Retrieval einmal proben.

- **`offen` — `restic` ist installiert, aber ungenutzt.** `dev-ops/deploy/bootstrap.sh` (dev-only) L30 installiert `restic`, doch **kein** Skript verwendet es. Off-Site läuft über rclone/aws/scp, nicht über restic. Die restic-Installation ist derzeit toter Ballast bzw. Vorbereitung für ein noch nicht gebautes restic-basiertes Off-Site/Eskrow. → Entweder einen restic-Pfad bauen oder die Installation aus dem Bootstrap entfernen.

- **`offen` — Recovery auf fabrikfrischem Host ist nicht end-to-end geprobt.** Der [Restore-Drill](ops-runbook.md#66-backup-restore-drill-refs-720-739) testet Restore **auf dem laufenden Host** in eine Wegwerf-DB; das PG-16→18-Upgrade ([ops-runbook §13](ops-runbook.md#13-postgresql-major-upgrade-16--18)) testet dump→frisches-Cluster→restore lokal. Der **vollständige** DR-Pfad aus [§6](#6-wiederherstellung-auf-einem-fabrikfrischen-host) (frischer Server + Off-Site-Download + Schlüssel aus der Maintainer-`.env`-Kopie + Restore) ist in dieser Form **noch nicht als Drill durchgespielt**. → Einmal jährlich als „echten" DR-Drill auf einem Wegwerf-Server üben und das Ergebnis hier/in einem Issue festhalten.

- **RTO/RPO sind Richtwerte, kein SLA.** Die Zahlen in [§1](#1-rto--rpo--zielwerte) sind aus den Cron-Frequenzen und realistischen Restore-Dauern abgeleitet, nicht gemessen und nicht vertraglich zugesichert. Ein DR-Drill ([oben](#8-offene-punkte-noch-nicht-verdrahtet)) würde sie validieren.

### 8.1 Drill-Protokoll (vorbereitet, Durchführung offen)

Dieses Drehbuch bereitet den einmaligen Welt-B-/Prod-Drill auf einem
fabrikfrischen Wegwerf-Host vor. Die Durchführung ist nicht Teil dieses
Dokumentationsstands; sie erfolgt gebündelt durch den Maintainer vor 1.0. Der
Produktivhost wird dabei weder als Restore-Ziel noch als Testdatenbank benutzt.

#### Vorbedingungen

- Der Zielhost ist frisch, isoliert und ausdrücklich zur anschließenden
  Vernichtung freigegeben. Root-Zugang, ausreichend Speicher und ein
  Zeitfenster ohne produktiven DNS-/Load-Balancer-Traffic sind vorhanden.
- Der zur Backup-Version passende Release-Stand samt
  `docker-compose.prod.yml`, `deploy/postgres-init/` und `scripts/ops/` liegt
  vor. Die Host-Härtung und Installation aus [§6 Schritt 1](#schritt-1--host-härten--docker-installieren)
  können von einem dafür berechtigten System ausgeführt werden.
  `dev-ops/deploy/bootstrap.sh` und `dev-ops/deploy/install-timers.sh` sind
  dev-only und im Release-Tarball nicht enthalten
  (`scripts/verify_release_test_guard.py`); sie müssen für den Drill separat
  aus dem Repository bereitgestellt werden. Ist das nicht möglich, werden
  Host-Härtung und Timer-Installation im Protokoll als NICHT PRÜFBAR vermerkt.
- `BACKUP_OFFSITE_TARGET` war auf der Quellinstallation gesetzt, der Zugang zum
  Off-Site-Ziel funktioniert ohne den verlorenen Prod-Host, und ein
  zusammengehöriger Welt-B-Satz ist vorhanden: DB-`*.sql.gz.enc` samt `.hmac`,
  Medien-`*_media.tar.gz.enc` samt `.hmac` und die jüngste
  `audit_hwm-*.json`.
- Die vom Maintainer verwahrte, host-getrennte `.env`-Kopie enthält mindestens
  die in [§4](#4-schlüssel--und-secret-wiederbeschaffung) genannten Schlüssel,
  die unveränderten DB-Rollennamen und Zugangsdaten. Die rekonstruierte `.env`
  trennt außerdem die Host- und Container-Sicht wie `.env.example`:
  `BACKUP_HOST_DIR=./backups` bezeichnet auf dem Host `<Repo>/backups`,
  `BACKUP_DIR=/backups` ausschließlich das read-only Mount-Ziel im Container.
  Bei einem abweichenden Host-Pfad muss `BACKUP_HOST_DIR` den tatsächlich
  bereitgestellten Ort nennen. Es gibt derzeit **kein** personell getrenntes
  Escrow: #1477 ist als
  Governance-Entscheidung für das Solo-Maintainer-Modell geschlossen. Im
  Protokoll steht deshalb als Schlüsselquelle „Maintainer-`.env`-Kopie", nicht
  „Escrow".
- Für den HWM-Nachweis war `DJANGO_AUDIT_HWM_PATH` bereits **vor Erstellung des
  Quellbackups** gesetzt (empfohlen: `/data/audit_hwm.json`), sodass
  `scripts/ops/backup.sh` die signierte Datei tatsächlich offsite übertragen
  hat. Die rekonstruierte `.env` enthält außerdem den ursprünglichen
  `DJANGO_AUDIT_HASH_KEY`. Ohne gesetzten HWM-Pfad gibt es kein HWM-Artefakt;
  Schritt 2b des Prod-Drills wird dann nicht ausgeführt. Das ist kein
  Signaturnachweis, und dieser Drill darf nicht als vollständig grün
  protokolliert werden. Fehlt `DJANGO_AUDIT_HWM_PATH` in der
  **rekonstruierten** `.env`, wird Schritt 2b lediglich mit `INFO` übersprungen;
  für diesen Drill die Variable deshalb setzen, damit die Signaturprüfung
  wirklich läuft. Andernfalls gilt der HWM-Nachweis als NICHT PRÜFBAR, nicht
  als bestanden.
- Eine gültige Health-Detail-Autorisierung und ein Browser-Testkonto sind
  verfügbar. Secrets, Passwörter, vollständige `.env`-Inhalte und
  personenbezogene Stichprobendaten werden nie ins Drill-Protokoll kopiert.

#### Schrittfolge

1. Startzeit, Zielhost, Release-Stand, Zeitpunkt des gewählten Backups und
   dessen Off-Site-Herkunft in der Vorlage unten erfassen. Daraus werden RTO
   und RPO am Ende berechnet.
2. [§6 Schritt 1 bis 3](#6-wiederherstellung-auf-einem-fabrikfrischen-host)
   in der dokumentierten Reihenfolge abarbeiten: Host härten, passenden
   Release-Stand einspielen, `.env` mit Modus `0600` aus der
   Maintainer-Kopie rekonstruieren und das frische DB-Cluster starten. Keine
   Schlüsselwerte in die Shell-History oder das Protokoll kopieren.
3. Für die Off-Site-Rückholung genau **eine** der drei aus [§5](#5-off-site-backup-beschaffen)
   abgeleiteten Varianten verwenden. Den Beispiel-Remote durch das gesetzte
   `BACKUP_OFFSITE_TARGET` ersetzen. Host-Backup und Prod-Drill verwenden
   denselben Ort `${BACKUP_HOST_DIR:-./backups}/daily`; `BACKUP_DIR` bezeichnet
   nur das Container-Ziel und wird von beiden Host-Skripten ignoriert. Die
   folgenden Befehle laden `BACKUP_HOST_DIR` aus der rekonstruierten `.env` und
   setzen den dokumentierten Default. Genau eine Rückholvariante anschließend
   in derselben Shell ausführen:

   ```bash
   cd /opt/anlaufstelle
   set -a
   source .env
   set +a
   BACKUP_HOST_DIR="${BACKUP_HOST_DIR:-./backups}"
   mkdir -p "$BACKUP_HOST_DIR/daily"
   ```

   Variante rclone:

   ```bash
   rclone copy remote:bucket/path/daily "$BACKUP_HOST_DIR/daily" --include "*.enc" --include "*.hmac" --include "audit_hwm-*.json"
   ```

   Variante S3:

   ```bash
   aws s3 sync s3://bucket/path/daily "$BACKUP_HOST_DIR/daily" --exclude "*" --include "*.enc" --include "*.hmac" --include "audit_hwm-*.json"
   ```

   Variante scp:

   ```bash
   scp "user@host:/path/*.enc" "user@host:/path/*.hmac" "user@host:/path/audit_hwm-*.json" "$BACKUP_HOST_DIR/daily/"
   ```

   DB-/Medien-Dateien und ihre `.hmac`-Sidecars müssen denselben
   Backup-Zeitstempel tragen. Die HWM-Datei folgt dem separaten Namen
   `audit_hwm-<timestamp>.json`.
4. [§6 Schritt 4 und 5](#schritt-4--db-backup-entschlüsseln--wiederherstellen)
   fortsetzen: Welt-B-DB und Medien über `restore.sh` aus dem gerade befüllten
   Verzeichnis wiederherstellen, Migrationen ausführen und den Stack samt
   Hintergrundjobs starten. `restore.sh` überträgt den Medien-Tarball per
   `docker compose exec web`; deshalb vor seinem Aufruf den noch nicht
   öffentlich über Caddy erreichbaren Web-Container als Transportziel starten:

   ```bash
   docker compose -f docker-compose.prod.yml up -d web
   ```

   `web` wartet über `depends_on` auf einen gesunden `clamav`; dessen erster
   Signatur-Download kann auf einem frischen Host mehrere Minuten dauern und
   zählt zur gemessenen RTO.

   Danach den Restore ausführen. Den Platzhalter durch den in Schritt 3
   geprüften gemeinsamen Zeitstempel ersetzen (die folgende Zeit ist nur ein
   kopierbares Formatbeispiel):

   ```bash
   ./scripts/ops/restore.sh "$BACKUP_HOST_DIR/daily/anlaufstelle_2026-09-03_020000.sql.gz.enc" \
                            "$BACKUP_HOST_DIR/daily/anlaufstelle_2026-09-03_020000_media.tar.gz.enc"
   ```

5. Die fünf Infrastruktur- und Datenprüfungen aus
   [§7](#7-smoke-verifikation-nach-restore) in der dort vorgegebenen Reihenfolge
   ausführen. Beim Health-Aufruf mit Detail-Autorisierung müssen mindestens
   `status: ok` und `encryption_key: ok` erscheinen. Danach im Browser einen
   wiederhergestellten Datensatz mit verschlüsseltem Feld öffnen und den
   lesbaren Klartext bestätigen, ohne ihn im Protokoll wiederzugeben.
6. Anschließend die vollständige [§6.6-Verifikation](ops-runbook.md#66-backup-restore-drill-refs-720-739)
   als Root gegen das Prod-Format fahren. Dieser Aufruf ist unverändert aus dem
   Skript-Header und dem Operations-Runbook übernommen. Der Drill liest die
   rekonstruierte `.env`, löst denselben Host-Pfad
   `${BACKUP_HOST_DIR:-./backups}/daily` auf und ignoriert das Container-Ziel
   `BACKUP_DIR`. Seine Banner-Zeile muss deshalb das in Schritt 3 befüllte
   Host-Verzeichnis nennen:

   ```bash
   sudo bash /opt/anlaufstelle/scripts/ops/restore-drill.sh --prod
   ```

   Der Lauf muss neben HMAC, Tabellen-Stichproben, Vollständigkeitsvergleich,
   RLS und `auditlog_immutable` ausdrücklich
   `Schritt 2b: Audit-HWM-Signatur verifiziert` melden. Schritt 2b lädt die jüngste
   `audit_hwm-*.json` über `HighWaterMarkStore.load()`; eine fehlende Datei,
   eine manipulierte Signatur oder ein falscher `DJANGO_AUDIT_HASH_KEY` ist ein
   `FAIL`. Der HWM-Schritt wird nur bei konfiguriertem
   `DJANGO_AUDIT_HWM_PATH` ausgeführt. Diese Konfiguration bleibt eine harte
   Vorbedingung und darf nicht durch ein grünes Ergebnis der übrigen Schritte
   ersetzt werden.
7. Endzeit, Exit-Code, jede `OK`-/`FAIL`-Zeile und Abweichungen protokollieren.
   Bei einem `FAIL` bleibt das Gesamtergebnis **OFFEN**; keine einzelne
   erfolgreiche Stichprobe in „grün" umdeuten.

#### Erfolgskriterien

Der Fresh-Host-Drill ist nur **GRÜN**, wenn alle folgenden Aussagen belegt sind:

- Off-Site-Rückholung gelang ohne Zugriff auf den ursprünglichen Prod-Host;
  DB, Medien, beide HMAC-Sidecars und Audit-HWM kamen vollständig unter dem
  Host-Pfad `${BACKUP_HOST_DIR:-./backups}/daily` an.
- `restore.sh`, Migrationen und Stack-Start endeten erfolgreich; DB-Rollen und
  RLS entsprechen [§7](#7-smoke-verifikation-nach-restore).
- Health meldet `status: ok` und `encryption_key: ok`; eine verschlüsselte
  Datenstichprobe ist im Browser lesbar und die erwartete Facility-Anzahl ist
  vorhanden.
- `restore-drill.sh --prod` endet mit Exit-Code 0, meldet keine `FAIL`-Zeile und
  nennt in seiner Banner-Zeile dasselbe Host-Verzeichnis wie Schritt 3. Er
  bestätigt insbesondere Schritt 2b (HWM-Signatur), Schritt 3b
  (Vollständigkeit), Schritt 4 (RLS), Schritt 5 (`auditlog_immutable`) und
  Schritt 7 (`mark_restore_verified`).
- Gemessene RTO und RPO liegen innerhalb der Richtwerte aus [§1](#1-rto--rpo--zielwerte),
  oder jede Überschreitung ist mit Ursache und Folgearbeit dokumentiert.

#### Rückbau

1. Zuerst das bereinigte Protokoll und benötigte Logs außerhalb des
   Wegwerf-Hosts sichern. Keine `.env`, Schlüssel, Tokens, Passwörter oder
   vollständigen Mail-/Setup-Links übernehmen.
2. Sicherstellen, dass kein DNS-Eintrag und kein Load-Balancer mehr auf den
   Zielhost zeigt. Danach den Stack mit dem im Operations-Runbook
   dokumentierten Befehl stoppen:

   ```bash
   cd /opt/anlaufstelle
   docker compose -f docker-compose.prod.yml down
   ```

3. Den ausdrücklich als Wegwerf-System freigegebenen Host samt Volumes über
   die Provider-Konsole löschen und ausschließlich für den Drill erzeugte
   Zugänge widerrufen. Bei einem fehlgeschlagenen Lauf darf der Host nur
   isoliert für die Ursachenanalyse aufbewahrt werden; anschließend gilt
   derselbe Rückbau. Der Produktivhost und sein Off-Site-Bestand bleiben
   unverändert.

#### Protokoll-Vorlage

```text
Issue: https://github.com/anlaufstelle/app/issues/2024
Datum / durchführende Person:
Zielhost / Provider (keine Zugangsdaten):
Release-Tag und Image-Digest:
Drill-Start / Service wieder erreichbar / Drill-Ende:

Backup-Quelle und Off-Site-Verfahren (rclone / S3 / scp):
Backup-Zeitstempel:
DB + DB-HMAC vorhanden: JA / NEIN
Medien + Medien-HMAC vorhanden: JA / NEIN
Audit-HWM vorhanden: JA / NEIN
DJANGO_AUDIT_HWM_PATH war am Quellhost gesetzt: JA / NEIN
DJANGO_AUDIT_HWM_PATH in rekonstruierter .env gesetzt: JA / NEIN
Schlüsselquelle: Maintainer-.env-Kopie (kein Escrow)

§6 Schritt 1 Host-Härtung: OK / FAIL — Notiz:
§6 Schritt 2 Release + .env: OK / FAIL — Notiz:
§6 Schritt 3 DB-Rollen-Init: OK / FAIL — Notiz:
§6 Schritt 4 DB-/Medien-Restore: OK / FAIL — Notiz:
§6 Schritt 5 Migrationen + Stack + Jobs: OK / FAIL — Notiz:

§7 DB-Rollen: OK / FAIL — Nachweis:
§7 RLS: OK / FAIL — Anzahl:
§7 Health status / encryption_key: OK / FAIL — Werte:
§7 offene Migrationen: OK / FAIL — Anzahl:
§7 Daten-/Verschlüsselungsstichprobe: OK / FAIL — keine Inhalte notieren

Vom Drill gemeldetes BACKUP_HOST_DIR/daily:
restore-drill.sh --prod Exit-Code:
Schritt 1 Wegwerf-DB angelegt: OK / FAIL
Schritt 2 HMAC/Restore: OK / FAIL
Schritt 2b Audit-HWM-Signatur: OK / FAIL / NICHT PRÜFBAR
Schritt 3 Tabellen-Stichproben: OK / FAIL
Schritt 3b Vollständigkeits-Guard: OK / FAIL
Schritt 4 RLS: OK / FAIL
Schritt 5 auditlog_immutable: OK / FAIL
Schritt 7 RESTORE_VERIFIED: OK / FAIL

Gemessene RPO / Ziel ≤ 24 h:
Gemessene RTO / Richtwert 2–4 h:
Gesamtergebnis: GRÜN / OFFEN
Gefundene Reibung:
Folgearbeiten mit Issue-Links:
Rückbau abgeschlossen am / durch:
```

---

## Siehe auch

- [ops-runbook.md §6.6 — Backup-Restore-Drill](ops-runbook.md#66-backup-restore-drill-refs-720-739) (Happy-Path, Kompatibilitätsmatrix der zwei Welten)
- [ops-runbook.md §6.6a — Off-Site-Backup-Sync](ops-runbook.md#66a-off-site-backup-sync-refs-738)
- [ops-runbook.md §13 — PostgreSQL-Major-Upgrade (16→18)](ops-runbook.md#13-postgresql-major-upgrade-16--18) (dump→frisches-Cluster→restore, derselbe Mechanismus wie DR)
- [monitoring-guide.md — `/health/`-Felder und Status-Codes](monitoring-guide.md)
- [.env.example — kanonische Secret-Liste](../.env.example)
