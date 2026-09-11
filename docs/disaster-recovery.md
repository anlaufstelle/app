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
| **RPO** (max. tolerierter Datenverlust) | **≤ 24 h** | Der einzurichtende Host-Cron aus [ops-runbook § 3.3](ops-runbook.md#33-host-cron-backup--restliche-host-jobs) läuft täglich um **02:00**. Zwischen zwei Läufen entstandene Daten sind bei Totalverlust verloren. Wer engere Ziele braucht, erhöht die Backup-Frequenz **und** die Off-Site-Sync-Frequenz. |
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

Ein vollständiges Recovery rekonstruiert **vier** Dinge. Fehlt eines, ist die Wiederherstellung unvollständig oder unmöglich:

1. **Die verschlüsselten Backup-Artefakte** (Off-Site, siehe [§5](#5-off-site-backup-beschaffen)):
   - DB-Dump (`*.sql.gz.enc` + `.hmac`)
   - Medien-Tarball (`*_media.tar.gz.enc` + `.hmac`). Ohne ihn fehlen alle Datei-Anhänge aus dem Encrypted File Vault.
2. **Die Secrets/Schlüssel** ([§4](#4-schlüssel--und-secret-wiederbeschaffung)) — vor allem `ENCRYPTION_KEY(S)` und `BACKUP_ENCRYPTION_KEY`.
3. **Der externe Audit-Anker** `audit_hwm-<timestamp>.json`, der genau zum gewählten DB-/Medien-Satz gehört. Ohne ihn kann die wiederhergestellte Audit-Kette nicht gegen ihren vor dem Ausfall gesicherten Stand geprüft werden.
4. **Die Deployment-Artefakte** — der Git-Stand bzw. das Release-Paket (Compose-Files, `deploy/postgres-init/`, `scripts/ops/`) und das Container-Image (`ghcr.io/anlaufstelle/app:<APP_VERSION-Tag>`). Der Code ist öffentlich (AGPL) und damit immer wiederbeschaffbar — er ist **nicht** der Engpass.

---

## 4. Schlüssel- und Secret-Wiederbeschaffung

Alle Secrets leben zur Laufzeit in der `.env` des Prod-Hosts (`chmod 600`). Bei Totalverlust ist diese Datei weg — die Werte müssen aus einem **getrennten** Speicher kommen. Maßgeblich ist [`.env.example`](../.env.example); die folgende Tabelle priorisiert nach Recovery-Kritikalität.

### 4.1 Kritisch — ohne diese ist Recovery unmöglich

| Variable | Rolle im Recovery | Code-Beleg |
|---|---|---|
| `BACKUP_ENCRYPTION_KEY` | Entschlüsselt das Backup selbst (`openssl enc -d`) **und** leitet den HMAC-Verify-Key ab. **Ohne ihn ist das Backup wertloser Chiffretext.** | Pflichtprüfung `: "${BACKUP_ENCRYPTION_KEY:?...}"` in [`backup.sh`](../scripts/ops/backup.sh) und [`restore.sh`](../scripts/ops/restore.sh); HMAC-Key-Ableitung in [`_backup_common.sh`](../scripts/ops/_backup_common.sh) |
| `ENCRYPTION_KEY` **oder** `ENCRYPTION_KEYS` | Fernet-Key(s) für die **Feld- und Datei-Verschlüsselung** in der DB/im Vault. Ein wiederhergestelltes Backup ist ohne den passenden Key lesbar als Tabellenstruktur, aber alle verschlüsselten Inhalte bleiben unentschlüsselbar. Bei Key-Rotation müssen **alle je benutzten** Keys in `ENCRYPTION_KEYS` stehen (MultiFernet, erster Key = aktiv). | Einstellungen `ENCRYPTION_KEY`/`ENCRYPTION_KEYS` in [`settings/base.py`](../src/anlaufstelle/settings/base.py), Produktionsprüfung `_validate_fernet_keys()` in [`settings/prod.py`](../src/anlaufstelle/settings/prod.py) |

> **MultiFernet-Falle:** Wurde der Feld-Key je rotiert, enthält das Backup Werte, die mit **mehreren** Generationen verschlüsselt sind. `ENCRYPTION_KEYS` muss dann **lückenlos alle** alten Keys enthalten, sonst sind ältere Datensätze nach dem Restore unlesbar. Die Key-Historie gehört deshalb mit ins Eskrow.
>
> **Diagnose bei fehlgeschlagener HMAC-Prüfung:** Der Test kann die Ursachen nicht unterscheiden. Möglich sind ein falscher `BACKUP_ENCRYPTION_KEY` (der HMAC-Key wird daraus abgeleitet), ein manipuliertes oder beschädigtes Artefakt oder ein `.hmac`-Sidecar aus einem anderen Backup-Satz. Den Satz deshalb nicht verwerfen, bevor Schlüssel und Dateizuordnung geprüft sind.

### 4.2 Wichtig — ohne diese startet der Stack nicht sauber

| Variable | Rolle | Code-Beleg |
|---|---|---|
| `POSTGRES_DB` | Name der Ziel-Datenbank. Backup, Restore, Compose und die Rollenprüfung müssen denselben Namen verwenden. | Pflichtprüfung `: "${POSTGRES_DB:?...}"` in [`backup.sh`](../scripts/ops/backup.sh) und [`restore.sh`](../scripts/ops/restore.sh) |
| `POSTGRES_BOOTSTRAP_PASSWORD` | Passwort des Bootstrap-Superusers `postgres` im frischen Cluster; das Init-Script legt damit die App-/Admin-Rolle an. | Compose-Variable `POSTGRES_PASSWORD: ${POSTGRES_BOOTSTRAP_PASSWORD}` in [`docker-compose.prod.yml`](../docker-compose.prod.yml) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | App-Login (NOSUPERUSER, NOBYPASSRLS) → im Container `APP_DB_USER`/`APP_DB_PASSWORD`. Muss beim Restore wieder denselben Rollennamen ergeben, sonst passen Ownership/Grants aus dem Dump nicht. | Compose-Variablen `APP_DB_USER`/`APP_DB_PASSWORD` in [`docker-compose.prod.yml`](../docker-compose.prod.yml), Rollenerzeugung in [`01-app-role.sh`](../deploy/postgres-init/01-app-role.sh) |
| `POSTGRES_ADMIN_USER` / `POSTGRES_ADMIN_PASSWORD` | Admin-Rolle (NOSUPERUSER, BYPASSRLS) für Migrationen/Seed/Retention → `ADMIN_DB_USER`/`ADMIN_DB_PASSWORD`. | Compose-Variablen `ADMIN_DB_USER`/`ADMIN_DB_PASSWORD` in [`docker-compose.prod.yml`](../docker-compose.prod.yml), Rollenerzeugung in [`01-app-role.sh`](../deploy/postgres-init/01-app-role.sh) |
| `DJANGO_SECRET_KEY` | Session-/CSRF-/Signing-Key. Ein **neuer** Wert ist technisch zulässig (invalidiert bestehende Sessions und Signaturen); den alten Wert nach Möglichkeit wiederbeschaffen. | Produktionsprüfung `DJANGO_SECRET_KEY` in [`settings/prod.py`](../src/anlaufstelle/settings/prod.py) |
| `DJANGO_AUDIT_HASH_KEY` | HMAC-Key für pseudonymisierte AuditLog-Einträge, die Audit-Ketten und die Signatur des externen HWM. Ein neuer Wert macht alte Audit-Hashes, die Ketten und den gesicherten HWM unverifizierbar. | Audit-Kettenfunktion `_chain_hmac()` und HWM-Funktion `_sign_payload()` in `src/core/services/audit/` |
| `DJANGO_AUDIT_HWM_PATH` | Persistenter Pfad der HWM-Betriebsdatei, empfohlen `/data/audit_hwm.json`. Er muss vor dem ersten Backup gesetzt sein. | Einstellung `AUDIT_HWM_PATH` in [`settings/base.py`](../src/anlaufstelle/settings/base.py) |
| `BACKUP_HOST_DIR` / `BACKUP_DIR` | Host-Ablage und read-only Container-Mount der Backups. `BACKUP_HOST_DIR` muss den wiederbeschafften Satz enthalten; `BACKUP_DIR` muss zum Compose-Mount passen. | Variablen `BACKUP_HOST_DIR` in [`backup.sh`](../scripts/ops/backup.sh) und `BACKUP_DIR` in [`settings/base.py`](../src/anlaufstelle/settings/base.py) |
| `MEDIA_ROOT` / `MEDIA_ROOT_CONTAINER` | App-Pfad und Skriptpfad für die Medien. Beide müssen auf dasselbe persistente Verzeichnis zeigen; der Archivwurzelname muss dessen Basename entsprechen. | Einstellung `MEDIA_ROOT` in [`settings/prod.py`](../src/anlaufstelle/settings/prod.py) und Variable `MEDIA_ROOT_CONTAINER` in [`backup.sh`](../scripts/ops/backup.sh)/[`restore.sh`](../scripts/ops/restore.sh) |
| `EXTERNAL_ORIGIN` | Öffentliche Basis-URL für Links. Fehlt sie, wird sie nur bei genau einem öffentlichen Host aus `ALLOWED_HOSTS` abgeleitet; bei mehreren öffentlichen Hosts muss sie ausdrücklich gesetzt sein. | Ableitung und Pflichtprüfung `EXTERNAL_ORIGIN` in [`settings/prod.py`](../src/anlaufstelle/settings/prod.py); `.env.example` zeigt Haupt- und `www`-Host |

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

`SENTRY_DSN`, `EMAIL_HOST*`, `CLAMAV_*` und `TRUSTED_PROXY_HOPS` sind aus `.env.example` und der bekannten Infrastruktur rekonstruierbar. `ALLOWED_HOSTS` und `DOMAIN` müssen dabei die Loopback-Namen für den Container-Healthcheck beibehalten. `BACKUP_OFFSITE_TARGET` ist rekonstruierbar, sein Zugangsmaterial jedoch ein eigenes Recovery-Artefakt nach § 4.4.

### 4.4 Wo die Schlüssel liegen sollten — Eskrow

> **Stand: noch nicht verdrahtet — siehe [§8](#8-offene-punkte-noch-nicht-verdrahtet).** Es gibt **kein** automatisches Key-Escrow im Code. Organisatorisch **müssen** mindestens `BACKUP_ENCRYPTION_KEY`, die vollständige `ENCRYPTION_KEYS`-Historie, `DJANGO_AUDIT_HASH_KEY`, `DJANGO_SECRET_KEY` und der **aktuelle** Satz `POSTGRES_PASSWORD`/`POSTGRES_ADMIN_PASSWORD`/`POSTGRES_BOOTSTRAP_PASSWORD` an einem vom Prod-Host **physisch/logisch getrennten** Ort liegen (Passwort-Manager des Trägers, versiegelter Umschlag im Safe, o. Ä.). `DJANGO_SECRET_KEY` ist im Gegensatz zu den Verschlüsselungs- und Audit-Schlüsseln ersetzbar, beendet dann aber bestehende Sessions und macht alte Signaturen ungültig. Ein Schlüssel, der nur in der `.env` desselben Hosts liegt, der verloren geht, schützt nicht vor Totalverlust — er **garantiert** den Datenverlust. Die DB-Passwörter sind auf einem fabrikfrischen Cluster zwar technisch neu wählbar; für Diagnose, Rotation und Recovery am vorhandenen Cluster muss der Eskrow aber dem aktuellen Login-Stand entsprechen.
>
> Zum getrennt gesicherten Recovery-Material gehört außerdem der **Zugang zum Off-Site-Ziel**: bei scp die SSH-Identität des ausführenden Kontos samt `known_hosts`, bei rclone die passende `rclone.conf`, bei S3 die benötigten AWS-Credentials. Die Rückholung muss ohne den verlorenen Prod-Host möglich sein.

---

## 5. Off-Site-Backup beschaffen

Off-Site-Sync ist mit `scripts/ops/backup.sh` implementiert und **nur aktiv, wenn `BACKUP_OFFSITE_TARGET` gesetzt war** (siehe [ops-runbook §6.6a](ops-runbook.md#66a-off-site-backup-sync-refs-738) und [§8](#8-offene-punkte-noch-nicht-verdrahtet)). Das Zugangsmaterial muss dem Konto zur Verfügung stehen, unter dem `backup.sh` tatsächlich läuft; bei scp umfasst das insbesondere SSH-Schlüssel bzw. Agent und `known_hosts`.

Vor dem Backup gilt eine definierte **Schreibruhe**: keine fachlichen Schreibzugriffe, Uploads, Importe, Retention oder sonstigen Hintergrundjobs, bis DB-Dump, Medienarchiv und HWM erzeugt sind. Ein gemeinsamer Dateizeitstempel beweist keine inhaltliche Konsistenz dieser nacheinander erstellten Artefakte.

Das Skript unterstützt drei Zielformate. Es überträgt bei rclone und S3 die Dateien des aktuellen Laufs nach `<ziel>/daily`; scp legt sie direkt unter `<ziel>/` ab. `weekly/` und `monthly/` werden nicht übertragen. rclone verwendet `copy`, S3 `sync` ohne `--delete`, und scp kopiert nur den aktuellen Satz: Die lokale Rotation wird daher nicht am Ziel gespiegelt. **Off-Site-Retention und Löschschutz sind eine Policy des Zielsystems**, nicht Verhalten des Backup-Skripts. Das Retrieval ist je Variante:

```bash
# Variante rclone (S3/B2/R2/Storj/...) — BACKUP_OFFSITE_TARGET=rclone:remote:bucket/path
rclone copy remote:bucket/path/daily ./restore-src --include "*.enc" --include "*.hmac"

# Variante S3 — BACKUP_OFFSITE_TARGET=s3://bucket/path
aws s3 sync s3://bucket/path/daily ./restore-src --exclude "*" --include "*.enc" --include "*.hmac"

# Variante scp — BACKUP_OFFSITE_TARGET=user@host:/path
scp "user@host:/path/*.enc" "user@host:/path/*.hmac" ./restore-src/
```

**Immer DB- *und* Medien-Artefakt samt `.hmac`-Sidecar holen.** Ohne den Sidecar verweigert `restore.sh` den Restore (`backup_verify_hmac` bricht ab — [`_backup_common.sh`](../scripts/ops/_backup_common.sh) L33). Das jüngste DB-Backup heißt `anlaufstelle_<timestamp>.sql.gz.enc`, der dazugehörige Medien-Tarball `anlaufstelle_<timestamp>_media.tar.gz.enc`.

Nach jedem Backup den **Off-Site-Bestand selbst** abgleichen: alle fünf Dateien des gewählten Zeitstempels (DB, DB-HMAC, Medien, Medien-HMAC, Audit-HWM) müssen am erwarteten Zielpfad vorhanden sein. Der Exit-Code allein genügt nicht; insbesondere endet der erste Off-Site-Fehler bewusst noch mit Exit-Code 0. Regelmäßig zusätzlich prüfen, dass Ziel-Retention und Object Lock die vereinbarte Aufbewahrung tatsächlich abbilden.

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

`restore.sh` leert die Ziel-Datenbank nicht, und der Dump enthält kein `--clean`. Ein Wiederanlauf ist deshalb nur in eine **frisch angelegte, leere Ziel-DB** zulässig. Auch Exit-Code 0 belegt keine Vollständigkeit. Vor jedem erneuten Versuch die Ziel-DB verwerfen und leer neu anlegen:

```bash
docker compose -f docker-compose.prod.yml exec -T db sh -c \
  'dropdb -U postgres --if-exists "$POSTGRES_DB" && createdb -U postgres -O "$ADMIN_DB_USER" "$POSTGRES_DB"'
```

Vor dem Medien-Restore `MEDIA_ROOT`, `MEDIA_ROOT_CONTAINER`, den persistenten Mount und den Wurzelnamen des Medienarchivs abgleichen. `MEDIA_ROOT` und `MEDIA_ROOT_CONTAINER` müssen auf dasselbe App-Verzeichnis zeigen; jede Abweichung hält den Restore an.

```bash
# restore.sh prüft den HMAC VOR dem Entschlüsseln, entschlüsselt, entpackt
# und spielt via psql in $POSTGRES_DB ein. Fragt interaktiv (ja/nein), weil
# es die Ziel-DB überschreibt. .hmac-Sidecar muss neben der .enc liegen.
./scripts/ops/restore.sh ./restore-src/anlaufstelle_<timestamp>.sql.gz.enc \
                         ./restore-src/anlaufstelle_<timestamp>_media.tar.gz.enc
```

[`restore.sh`](../scripts/ops/restore.sh) erledigt in einem Lauf: HMAC-Verify der DB und, falls übergeben, des Medienarchivs → Bestätigungsabfrage → erneuter DB-HMAC-Verify → `openssl enc -d | gunzip | psql` → erneuter Medien-HMAC-Verify → Medien-Inhalt löschen → Medienarchiv per `tar -x` in `/data` des `web`-Containers entpacken. Damit sind beide Sidecars geprüft, bevor die erste destruktive Aktion beginnt, und das Prüf-Nutzungs-Fenster bleibt klein. **Der bestehende Medien-Inhalt wird vor dem Entpacken gelöscht.** Auf einem Bestandssystem vorher separat sichern; der Restore stellt ausschließlich den Stand des Archivs her. Auf dem hier beschriebenen fabrikfrischen Host ist die Löschung folgenlos. Bricht `tar -x` ab, ist der Medienbestand unvollständig; Ursache beheben, die Ziel-DB erneut leer anlegen und den vollständigen DB-/Medien-Restore wiederholen. Der `BACKUP_ENCRYPTION_KEY` aus der `.env` wird automatisch geladen.

Nach dem Restore die DB-/Medien-Konsistenz prüfen: Jede wiederhergestellte Anhangzeile muss eine Datei am tatsächlichen App-`MEDIA_ROOT` besitzen. Dateiname und Größe müssen den DB-Metadaten entsprechen; die Tabelle enthält keine Prüfsumme. Fehlt eine Datei, ist der Restore unvollständig.

### Schritt 4b — Audit-HWM zurückspielen und Kette prüfen

Bevor irgendein Hintergrundjob startet, das zum gewählten Restore-Satz `<timestamp>` gehörende `audit_hwm-<timestamp>.json` an `DJANGO_AUDIT_HWM_PATH` ablegen. Nicht pauschal die jüngste HWM-Datei verwenden. Das Off-Site-Original unverändert aufbewahren:

```bash
docker compose -f docker-compose.prod.yml stop cron
docker compose -f docker-compose.prod.yml run --rm --entrypoint sh -T maintenance \
  -c 'cat > "$DJANGO_AUDIT_HWM_PATH"' \
  < ./restore-src/audit_hwm-<timestamp>.json
docker compose -f docker-compose.prod.yml run --rm -T maintenance verify_audit_chain
```

Nur ein vollständig grüner Lauf ist zulässig. Er prüft den zurückgespielten Anker und schreibt anschließend die **Betriebsdatei** fort; deshalb bleibt das zurückgeholte Original separat und unverändert erhalten.

### Schritt 5 — Migrationen + Stack hochfahren

```bash
# Migrate als One-Shot. docker-migrate.sh verifiziert vorab via check_db_roles,
# dass App-Rolle = NOSUPERUSER/NOBYPASSRLS und Admin = BYPASSRLS (Refs #1002).
docker compose -f docker-compose.prod.yml run --rm --entrypoint=/app/docker-migrate.sh maintenance

# Vollständigen Stack samt mitgeliefertem cron-Sidecar hochfahren.
docker compose -f docker-compose.prod.yml up -d
```

Retention, Audit-Verifikation, Snapshots, Bereinigung, Breach-Erkennung und MV-Refresh laufen ab Werk im `cron`-Sidecar aus `deploy/crontab`. Zusätzlich ist **nur der Host-Cron für `scripts/ops/backup.sh`** nach [ops-runbook § 3.3](ops-runbook.md#33-host-cron-backup--restliche-host-jobs) einzurichten. Keine weiteren Host-Cronzeilen für die Sidecar-Jobs anlegen, sonst laufen sie doppelt.

Danach weiter mit der [Smoke-Verifikation](#7-smoke-verifikation-nach-restore).

---

## 7. Smoke-Verifikation nach Restore

Reihenfolge: erst Infrastruktur-Checks (DB-Rollen, RLS), dann Health-Endpoint, dann Stichproben. Vor den Shell-Prüfungen die rekonstruierte `.env` laden. Nach einem frühen `FAIL` grundsätzlich anhalten. Eine diagnostische Fortsetzung ist zulässig, wenn sie ausdrücklich als Abweichung protokolliert wird; das Gesamtergebnis bleibt dann **OFFEN**.

```bash
cd /opt/anlaufstelle
set -a
source .env
set +a

# 1) DB-Rollen-Gate — App-Rolle ohne Superuser/BYPASSRLS, Admin mit BYPASSRLS.
docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles   # Exit 0

# 2) RLS aktiv? (Sicherheits-Safety-Net muss nach Restore noch greifen.)
docker compose -f docker-compose.prod.yml exec -T db sh -c \
  'psql -U postgres -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity"'   # > 0

# 3) Health-Liveness. Erwartet HTTP 200 + {"status":"ok"}.
curl -sf https://$DOMAIN/health/ | python3 -m json.tool

# 4) Migrationsstand vollständig?
docker compose -f docker-compose.prod.yml exec web python manage.py showmigrations | grep -c '\[ \]'   # erwartet: 0

# 5) Daten-Stichprobe — sind Nutzdaten wirklich da?
docker compose -f docker-compose.prod.yml exec -T db \
  psql -U $POSTGRES_USER -d $POSTGRES_DB -tAc "SELECT count(*) FROM core_facility;"
```

**Health-Detail nur per Session:** Im ausgelieferten Prod-Stack erreicht `DJANGO_HEALTH_DETAIL_TOKEN` den `web`-Container nicht. Den Detail-Payload deshalb in einer angemeldeten Browser-Session als `super_admin` prüfen. Der dortige Fernet-Roundtrip verschlüsselt und entschlüsselt nur einen **frischen** Wert; `"encryption_key": "ok"` beweist nicht, dass der wiederhergestellte Bestand mit allen benötigten Schlüsselgenerationen lesbar ist.

Für jede benötigte Generation in `ENCRYPTION_KEYS` mindestens einen bereits vor dem Backup damit verschlüsselten Datensatz in der App öffnen und lesbaren Klartext bestätigen. Zusätzlich einen echten Vault-Anhang herunterladen, öffnen und seinen Dateinamen sowie seine Byte-Größe mit den wiederhergestellten DB-Metadaten abgleichen. Die Tabelle führt keine Prüfsumme; der echte Download ist daher unverzichtbar.

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
  Host-Härtung und Docker-Installation werden mit den Werkzeugen der
  jeweiligen Installation ausgeführt und im Protokoll belegt. Der
  mitgelieferte `cron`-Sidecar übernimmt die Anwendungsjobs; nur der
  Backup-Host-Cron wird nach dem erfolgreichen Restore eingerichtet.
- `BACKUP_OFFSITE_TARGET` war auf der Quellinstallation gesetzt, der Zugang zum
  Off-Site-Ziel funktioniert ohne den verlorenen Prod-Host, und ein
  zusammengehöriger Welt-B-Satz ist vorhanden: DB-`*.sql.gz.enc` samt `.hmac`,
  Medien-`*_media.tar.gz.enc` samt `.hmac` und die zum selben Zeitstempel
  gehörende `audit_hwm-<timestamp>.json`.
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
- Vor dem Quellbackup wurden Referenzzahlen für die in den Erfolgskriterien
  genannten Tabellen erhoben und die fachlichen Schreibzugriffe sowie
  schreibenden Hintergrundjobs bis zum Abschluss von DB-Dump, Medienarchiv und
  HWM angehalten. Ein gleicher Dateizeitstempel allein ist kein
  Konsistenznachweis.
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
- Falls noch kein HWM existiert, ihn **vor dem Quellbackup** bewusst erzeugen:
  `docker compose -f docker-compose.prod.yml run --rm -T maintenance verify_audit_chain`.
  Der Lauf muss vollständig grün sein, erst dann ist der Anker geschrieben.
- Ein `super_admin`-Browser-Testkonto für den Session-Zugriff auf den
  Health-Detail-Payload ist verfügbar. Secrets, Passwörter, vollständige `.env`-Inhalte und
  personenbezogene Stichprobendaten werden nie ins Drill-Protokoll kopiert.
- Die Off-Site-Rückholung wurde von einem **dritten Rechner** ohne Zutun des
  Prod-Hosts getestet; Zugangsmaterial und `known_hosts` stammen aus dem
  getrennt gesicherten Recovery-Bestand.

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

   DB-/Medien-Dateien, ihre `.hmac`-Sidecars und die HWM-Datei müssen denselben
   Backup-Zeitstempel tragen. Vor dem Weitergehen genau diesen Fünfersatz
   festhalten; Dateinamensgleichheit ersetzt weder Schreibruhe noch den späteren
   Inhaltsabgleich.
4. [§6 Schritt 4](#schritt-4--db-backup-entschlüsseln--wiederherstellen)
   fortsetzen: Ziel-DB frisch und leer anlegen, Pfade abgleichen und Welt-B-DB
   sowie Medien über `restore.sh` aus dem gerade befüllten Verzeichnis
   wiederherstellen. `restore.sh` überträgt den Medien-Tarball per
   `docker compose -f docker-compose.prod.yml exec web`; deshalb vor seinem Aufruf den noch nicht
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

   Danach jede Anhangzeile gegen Datei, Dateiname und Größe am App-`MEDIA_ROOT`
   abgleichen. Anschließend [§6 Schritt 4b](#schritt-4b--audit-hwm-zurückspielen-und-kette-prüfen)
   mit `audit_hwm-2026-09-03_020000.json` aus genau diesem Satz ausführen. Erst
   nach grüner Audit-Kettenprüfung Migrationen und Stack nach §6 Schritt 5
   starten.

5. Die fünf Infrastruktur- und Datenprüfungen aus
   [§7](#7-smoke-verifikation-nach-restore) in der dort vorgegebenen Reihenfolge
   ausführen. Beim Health-Aufruf in der angemeldeten `super_admin`-Session müssen
   mindestens `status: ok` und `encryption_key: ok` erscheinen. Danach je
   benötigter Schlüsselgeneration einen wiederhergestellten Datensatz öffnen
   und einen echten Vault-Anhang herunterladen; nur Metadaten und Ergebnis,
   keine Inhalte protokollieren.
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
   `Schritt 2b: Audit-HWM-Signatur verifiziert` melden. Schritt 2b bindet die
   `audit_hwm-<timestamp>.json` desselben Restore-Satzes ein und lädt sie über
   `HighWaterMarkStore.load()`; eine fehlende oder abweichende Datei, eine
   manipulierte Signatur oder ein falscher `DJANGO_AUDIT_HASH_KEY` ist ein
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
- Die Tabellenzahlen entsprechen dem **vor dem Backup** erhobenen Referenzstand.
  Der Drill-Schritt 3b vergleicht nur vier Tabellen mit einer Live-DB und ist
  auf einem frischen Host kein Vollständigkeitsnachweis.
- Health meldet `status: ok` und `encryption_key: ok`; eine verschlüsselte
  Datenstichprobe je benötigter Schlüsselgeneration ist im Browser lesbar. Ein
  echter Vault-Anhang lässt sich herunterladen; Dateiname und Größe stimmen mit
  den DB-Metadaten überein. Jedes `degraded` bleibt ein `FAIL`.
- Jede Anhangzeile besitzt ihre Datei am App-`MEDIA_ROOT`; fehlt eine Datei, ist
  der Restore unvollständig.
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
Referenzstand vor Backup (Tabelle = Zeilen):
Schreibruhe von / bis:
DB + DB-HMAC vorhanden: JA / NEIN
Medien + Medien-HMAC vorhanden: JA / NEIN
Audit-HWM vorhanden: JA / NEIN
Off-Site-Rückholung auf drittem Rechner ohne Prod-Host: OK / FAIL
DJANGO_AUDIT_HWM_PATH war am Quellhost gesetzt: JA / NEIN
DJANGO_AUDIT_HWM_PATH in rekonstruierter .env gesetzt: JA / NEIN
Schlüsselquelle: Maintainer-.env-Kopie (kein Escrow)

§6 Schritt 1 Host-Härtung: OK / FAIL — Notiz:
§6 Schritt 2 Release + .env: OK / FAIL — Notiz:
§6 Schritt 3 DB-Rollen-Init: OK / FAIL — Notiz:
§6 Schritt 4 DB-/Medien-Restore: OK / FAIL — Notiz:
§6 Schritt 4b HWM-Rückspielung + Kettenprüfung: OK / FAIL — Notiz:
§6 Schritt 5 Migrationen + Stack + Jobs: OK / FAIL — Notiz:

§7 DB-Rollen: OK / FAIL — Nachweis:
§7 RLS: OK / FAIL — Anzahl:
§7 Health status / encryption_key: OK / FAIL — Werte:
§7 offene Migrationen: OK / FAIL — Anzahl:
§7 Datenstichprobe je Schlüsselgeneration: OK / FAIL — keine Inhalte notieren
§7 Vault-Download, Dateiname und Größe: OK / FAIL — keine Inhalte notieren
§7 DB-/Medien-Vollständigkeit am App-Pfad: OK / FAIL

Vom Drill gemeldetes BACKUP_HOST_DIR/daily:
restore-drill.sh --prod Exit-Code:
Schritt 1 Wegwerf-DB angelegt: OK / FAIL
Schritt 2 HMAC/Restore: OK / FAIL
Schritt 2b Audit-HWM-Signatur: OK / FAIL / NICHT PRÜFBAR
Schritt 3 Tabellen-Stichproben: OK / FAIL
Schritt 3b Vier-Tabellen-Abgleich gegen Live-DB: OK / FAIL
Schritt 4 RLS: OK / FAIL
Schritt 5 auditlog_immutable: OK / FAIL
Schritt 6 Medien-HMAC und Probeentpackung: OK / FAIL
Schritt 7 RESTORE_VERIFIED: OK / FAIL

Erfolgskriterium Off-Site-Rückholung: OK / FAIL
Erfolgskriterium Referenz-Zeilenabgleich: OK / FAIL
Erfolgskriterium Health + Schlüsselgenerationen + Vault-Download: OK / FAIL
Erfolgskriterium DB-/Medien-Konsistenz: OK / FAIL
Erfolgskriterium vollständiger Restore-Drill: OK / FAIL
Erfolgskriterium RTO/RPO: OK / FAIL

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
