# Monitoring-Guide — der `/health/`-Endpoint

> **Zweck:** Den `/health/`-Endpoint operativ verstehen — welche Felder er liefert, welche HTTP-Status-Codes er setzt, was jeder Wert bedeutet und wie man Uptime-/Monitoring-Tools anbindet. [coolify-deployment.md](coolify-deployment.md) sagt nur „Monitoring-Alerts einrichten" — dieses Dokument liefert die fehlende Substanz.
>
> **Code ist die Wahrheitsquelle.** Alle Feldnamen, Schwellen und Status-Codes sind gegen [`src/core/views/health.py`](../src/core/views/health.py) (View) und [`src/anlaufstelle/urls.py`](../src/anlaufstelle/urls.py) (Route `path("health/", HealthView.as_view(), name="health")`) verifiziert. Stand: v0.14.0-Roadmap (Refs #1071 Block D).

---

## Inhaltsverzeichnis

1. [Überblick](#1-überblick)
2. [Zwei Antwort-Tiefen: Liveness vs. Detail](#2-zwei-antwort-tiefen-liveness-vs-detail)
3. [HTTP-Status-Codes](#3-http-status-codes)
4. [Das `status`-Feld](#4-das-status-feld)
5. [Feld-Referenz](#5-feld-referenz)
6. [Beispiel-Antworten](#6-beispiel-antworten)
7. [Anbindung an Monitoring-Tools](#7-anbindung-an-monitoring-tools)
8. [Rate-Limit, Caching, Schwellen](#8-rate-limit-caching-schwellen)
9. [Abgrenzung: `/health/` vs. Compliance-Dashboard](#9-abgrenzung-health-vs-compliance-dashboard)

---

## 1. Überblick

`GET /health/` ist ein **auth-freier** JSON-Endpoint für Liveness- und Health-Monitoring. Er ist auch im Maintenance-Mode erreichbar ([ops-runbook §6.5a](ops-runbook.md): `/health/` steht auf der Whitelist). Die Antwort ist immer JSON; der HTTP-Status spiegelt die Liveness, das `status`-Feld die feinere Gesundheits-Bewertung.

Implementiert als `HealthView` (`django.views.View`), rate-limitiert auf **120 Requests/Minute pro IP** ([`health.py`](../src/core/views/health.py) L148).

---

## 2. Zwei Antwort-Tiefen: Liveness vs. Detail

Der Endpoint liefert **zwei unterschiedlich umfangreiche Payloads** — abhängig davon, ob der Aufrufer als „intern" autorisiert ist (`_detail_authorized`, [`health.py`](../src/core/views/health.py) L39–50, Refs #1024 A7.1):

| Aufrufer | Bekommt | Warum |
|---|---|---|
| **Anonym** (Default für externes Monitoring) | Schlanker **Liveness**-Payload: `status`, `database`, `virus_scanner`/`clamav`, `encryption_key`. **Keine** Detailfelder. | Recon-Härtung: `version`, `disk_free_pct` etc. verraten Angreifern Infrastruktur-Details. Außerdem kein teurer SMTP-Handshake/Filesystem-Scan pro Poll. |
| **Intern / Token** | Liveness **plus** Detailfelder: `version`, `smtp`, `last_backup_age_hours`, `disk_free_pct`, `stale_jobs`. | Operatives Monitoring will die Details. |

**Als „intern" gilt ein Aufrufer, wenn** entweder

- der Header `X-Health-Token` exakt dem konfigurierten `DJANGO_HEALTH_DETAIL_TOKEN` entspricht (Konstantzeit-Vergleich, [`settings/base.py`](../src/anlaufstelle/settings/base.py) L40 → `HEALTH_DETAIL_TOKEN`), **oder**
- der Request eine authentifizierte Session trägt (`request.user.is_authenticated`).

```bash
# Anonyme Liveness (externes Monitoring):
curl -s https://$DOMAIN/health/

# Detail-Payload für internes Monitoring (Token-Header):
curl -s -H "X-Health-Token: $DJANGO_HEALTH_DETAIL_TOKEN" https://$DOMAIN/health/
```

> Ist `DJANGO_HEALTH_DETAIL_TOKEN` **nicht** gesetzt, gibt es **keinen** Token-Pfad — Detailfelder sehen dann nur eingeloggte Sessions. Externes Monitoring bleibt auf dem Liveness-Payload.

---

## 3. HTTP-Status-Codes

Nur **zwei** Codes — gesetzt ausschließlich durch die zwei *kritischen* Checks (DB + Encryption-Key):

| HTTP | Wann | Code-Beleg |
|---|---|---|
| **200 OK** | DB erreichbar **und** Encryption-Key-Roundtrip ok. Gilt auch bei `status: "degraded"` (siehe [§4](#4-das-status-feld)). | [`health.py`](../src/core/views/health.py) L176 (`http_status = 200 if db_ok`) |
| **503 Service Unavailable** | DB nicht erreichbar **oder** Encryption-Key-Roundtrip fehlgeschlagen. | L176 (`else 503`) + L200 (`http_status = 503` bei `encryption_key == "error"`) |

> **Wichtig fürs Alerting:** Ein `degraded`-Zustand (z. B. ClamAV weg, Backup zu alt, Disk knapp) bleibt **HTTP 200**. Wer nur den HTTP-Code überwacht, **verpasst** degraded-Zustände — dafür **muss** das JSON-`status`-Feld ausgewertet werden ([§7](#7-anbindung-an-monitoring-tools)).

---

## 4. Das `status`-Feld

Das Top-Level-`status` ist die **feinere** Gesundheits-Bewertung. Drei Werte, hierarchisch (eine schärfere Stufe überschreibt nie eine mildere nach unten):

| `status` | Bedeutung | Auslöser | HTTP |
|---|---|---|---|
| `"ok"` | Alles im grünen Bereich. | Startwert, solange kein Check etwas anderes setzt. | 200 |
| `"degraded"` | Service funktioniert, aber eine **nicht-kritische** Komponente ist beeinträchtigt — zeitnah ansehen. | ClamAV nicht erreichbar **oder** SMTP unreachable **oder** Backup älter als 48 h **oder** Disk-frei < 10 %. | **200** |
| `"error"` | Service effektiv unbenutzbar — **sofort** handeln. | DB nicht erreichbar **oder** Encryption-Key-Roundtrip fehlgeschlagen. | 503 |

Mechanik (aus [`health.py`](../src/core/views/health.py)): `status` startet auf `"ok"`; ein fehlgeschlagener kritischer Check setzt `"error"` + HTTP 503; ein beeinträchtigter unkritischer Check setzt `"degraded"` **nur, wenn status noch `"ok"` ist** (`if … and status == "ok"`) — ein bestehendes `"error"` wird also nie zu `"degraded"` heruntergestuft.

---

## 5. Feld-Referenz

Reihenfolge wie im Code. „Sichtbarkeit": **immer** = auch im anonymen Liveness-Payload; **Detail** = nur für interne/Token-Aufrufer.

### 5.1 Immer enthaltene Felder (Liveness)

| Feld | Typ | Werte | Bedeutung | Setzt Status |
|---|---|---|---|---|
| `status` | string | `ok` / `degraded` / `error` | Gesamtbewertung (siehe [§4](#4-das-status-feld)). ||
| `database` | string | `connected` / `unavailable` | Ergebnis von `SELECT 1` gegen die DB ([`_check_database`](../src/core/views/health.py) L53–60). | `unavailable` → **`error` + HTTP 503** |
| `virus_scanner` | string | `connected` / `unavailable` / `disabled` | ClamAV-Daemon-Ping, wenn `CLAMAV_ENABLED`. `disabled`, wenn ClamAV aus. | `unavailable` → **`degraded`** |
| `clamav` | string | `ok` / `error` / `disabled` | Redundanter Klartext-Indikator zu `virus_scanner` (gleiche Quelle). | (gleicher Auslöser wie `virus_scanner`) |
| `encryption_key` | string | `ok` / `error` | Fernet-Encrypt/Decrypt-**Roundtrip** ([`_check_encryption_key`](../src/core/views/health.py) L63–79). `error` = Schlüssel passt nicht zu den Daten → keine verschlüsselten Felder lesbar. | `error` → **`error` + HTTP 503** |

> `virus_scanner` **und** `clamav` kommen aus demselben Ping — `virus_scanner` ist der menschenlesbare Verbindungsstatus, `clamav` der ok/error-Kurzindikator. Bei Scanner-Ausfall trifft die harte Fail-closed-Entscheidung der Upload-Pfad im File-Vault, **nicht** der Health-Endpoint — daher nur `degraded`, nicht `error`.

### 5.2 Detail-Felder (nur intern/Token)

| Feld | Typ | Werte | Bedeutung | Setzt Status |
|---|---|---|---|---|
| `version` | string | z. B. `v0.14.0` / `dev` | App-Version aus der ENV `APP_VERSION` (Fallback `dev`). ||
| `smtp` | object | `{"status": "...", "latency_ms"?: int}` | SMTP-CONNECT-Test ([`_check_smtp`](../src/core/views/health.py) L82–104). | siehe unten |
| `smtp.status` | string | `ok` / `unreachable` / `disabled` | `disabled` = Console/Locmem-Backend oder leerer `EMAIL_HOST`. `unreachable` = Server antwortet nicht (Timeout 2 s) → Token-Invites/Passwort-Reset scheitern lautlos. | `unreachable` → **`degraded`** |
| `smtp.latency_ms` | int | nur bei `ok` | CONNECT-Latenz in Millisekunden. ||
| `last_backup_age_hours` | float \| null | z. B. `12.4` / `null` | Alter des jüngsten `*.sql.gz.enc` in `BACKUP_DIR` ([`_check_backup_age`](../src/core/views/health.py) L107–117). `null` = kein Backup gefunden / `BACKUP_DIR` fehlt. | `> 48` → **`degraded`** |
| `disk_free_pct` | float \| null | z. B. `42.0` / `null` | Freier Speicher auf `MEDIA_ROOT` in Prozent ([`_check_disk_free_pct`](../src/core/views/health.py) L137–145). `null` = Pfad fehlt. | `< 10` → **`degraded`** |
| `stale_jobs` | array\<string\> | z. B. `[]` / `["breach_scan_last_run"]` | Keys der Hintergrundjobs (Backup, Retention, Snapshots, Breach-Scan, MV-Refresh) mit Status `critical` laut [`cron_job_checks`](../src/core/services/compliance/__init__.py) ([`_check_stale_jobs`](../src/core/views/health.py)). Refs #1335. | nicht-leer → **`degraded`** |

> **`stale_jobs` degradet nur bei `critical`, nicht bei `unknown`.** Ein Job, der auf einer frischen Installation noch nie gelaufen ist (`unknown`), fehlt bewusst in `stale_jobs` — sonst wäre jede frische Instanz sofort `degraded`. `warning`-Jobs (leicht überfällig) tauchen ebenfalls nicht auf; die volle Ampel je Job zeigt das Compliance-Dashboard ([§9](#9-abgrenzung-health-vs-compliance-dashboard)). Voraussetzung für ein sinnvolles Signal ist ein eingerichteter Scheduler ([ops-runbook §3](ops-runbook.md#3-cron-jobs)) — ohne ihn bleiben die Jobs dauerhaft `unknown`, nicht `critical`, und `stale_jobs` bleibt leer, obwohl nichts läuft.

> **`last_backup_age_hours` zählt nur Welt-B-Backups (`*.sql.gz.enc`).** Der Glob in `_check_backup_age` ist `*.sql.gz.enc` — Welt-A-Dumps (`dump-*.pgc.enc`, das Format auf `dev.anlaufstelle.app`) werden hier **nicht** erfasst und liefern `null`. Für die zwei Backup-Welten siehe [ops-runbook §6.6](ops-runbook.md#66-backup-restore-drill-refs-720-739). `null` setzt **keinen** degraded-Status — eine Lücke, wenn Backups nur in Welt A laufen ([§9](#9-abgrenzung-health-vs-compliance-dashboard) verweist auf das Compliance-Dashboard als robusteren Backup-Alters-Check).

---

## 6. Beispiel-Antworten

**Anonyme Liveness, alles ok (HTTP 200):**

```json
{
  "status": "ok",
  "database": "connected",
  "virus_scanner": "connected",
  "clamav": "ok",
  "encryption_key": "ok"
}
```

**Intern/Token, alles ok (HTTP 200):**

```json
{
  "status": "ok",
  "database": "connected",
  "virus_scanner": "connected",
  "clamav": "ok",
  "encryption_key": "ok",
  "version": "v0.14.0",
  "smtp": {"status": "ok", "latency_ms": 38},
  "last_backup_age_hours": 9.7,
  "disk_free_pct": 41.3,
  "stale_jobs": []
}
```

**Degraded — Backup zu alt + SMTP weg (HTTP 200, intern):**

```json
{
  "status": "degraded",
  "database": "connected",
  "virus_scanner": "connected",
  "clamav": "ok",
  "encryption_key": "ok",
  "version": "v0.14.0",
  "smtp": {"status": "unreachable"},
  "last_backup_age_hours": 73.2,
  "disk_free_pct": 38.0,
  "stale_jobs": []
}
```

**Degraded — Scheduler ausgefallen (HTTP 200, intern):**

```json
{
  "status": "degraded",
  "database": "connected",
  "virus_scanner": "connected",
  "clamav": "ok",
  "encryption_key": "ok",
  "version": "v0.14.0",
  "smtp": {"status": "ok", "latency_ms": 38},
  "last_backup_age_hours": 9.7,
  "disk_free_pct": 41.3,
  "stale_jobs": ["backup_age", "breach_scan_last_run"]
}
```

**Error — DB weg (HTTP 503):**

```json
{
  "status": "error",
  "database": "unavailable",
  "virus_scanner": "disabled",
  "clamav": "disabled",
  "encryption_key": "ok"
}
```

> Die exakte Feld-Auswahl/Reihenfolge kann je nach Konfiguration (ClamAV an/aus, Token gesetzt/nicht) variieren — maßgeblich bleibt [`health.py`](../src/core/views/health.py).

---

## 7. Anbindung an Monitoring-Tools

### 7.1 Zwei Überwachungsebenen

1. **Liveness (HTTP-Code):** Reicht für „lebt der Service?". Externe Uptime-Tools (UptimeRobot, Better Stack, Healthchecks.io, Pingdom …) prüfen einfach `GET https://$DOMAIN/health/` auf **HTTP 200**. Ein 503 oder Timeout → Alarm.
2. **Health (JSON-`status`):** Nötig, um `degraded` zu sehen (bleibt HTTP 200!). Das Tool muss den **Response-Body** parsen und auf `"status": "degraded"`/`"error"` matchen.

### 7.2 Container-/Compose-Healthcheck

`/health/` ist der natürliche Docker-Healthcheck (siehe [coolify-deployment.md](coolify-deployment.md), Hinweiskasten ClamAV). Beispiel:

```yaml
healthcheck:
  test: ["CMD", "curl", "-fsS", "https://localhost/health/"]
  interval: 30s
  timeout: 5s
  retries: 3
  start_period: 40s
```

`curl -f` failt bei HTTP ≥ 400 → der Container wird bei DB-/Key-Ausfall (503) `unhealthy`. **Achtung:** `degraded` (200) macht den Container **nicht** unhealthy — das ist gewollt (Last-Balancer soll den Pod bei ClamAV-Ausfall nicht rauswerfen), bedeutet aber, dass degraded-Zustände **separat** überwacht werden müssen.

### 7.3 Externes Tool mit Body-Matching (empfohlen)

Tools mit Keyword-/JSON-Assertion so konfigurieren, dass sie

- den Detail-Token mitschicken: Header `X-Health-Token: <DJANGO_HEALTH_DETAIL_TOKEN>` (sonst kein Backup-/Disk-/SMTP-Signal),
- auf `"status":"ok"` als **erwarteten** Body bestehen → jeder `degraded`/`error` löst Alarm aus,
- zusätzlich auf HTTP 200 prüfen.

So fangen `degraded`-Frühwarnungen (Backup-Lag, Disk knapp, SMTP weg, ClamAV weg) auf, bevor sie zu `error` eskalieren.

### 7.4 Was zusätzlich überwachen?

`/health/` deckt App-Liveness + die genannten Komponenten ab. **Nicht** abgedeckt und separat per Host-Monitoring (Coolify/Hetzner/Node-Exporter) zu überwachen:

- **RAM/CPU des Hosts** (`/health/` misst nur Disk-frei auf `MEDIA_ROOT`).
- **Off-Site-Backup-Status** — `scripts/ops/backup.sh` signalisiert Off-Site-Fehler über Exit-Code/State-File/optionalen Sentry-Hook, **nicht** über `/health/` ([ops-runbook §6.6a](ops-runbook.md#66a-off-site-backup-sync-refs-738)).
- **Cron-/Timer-Erfolg** der Hintergrundjobs (Backup, Retention, Breach-Scan) im Detail — `/health/` zeigt mit `stale_jobs` nur ein grobes `critical`-Signal (Refs #1335), die volle Ampel je Job (inkl. `warning`/`unknown`) liefert erst das Compliance-Dashboard ([§9](#9-abgrenzung-health-vs-compliance-dashboard)).

---

## 8. Rate-Limit, Caching, Schwellen

| Aspekt | Wert | Quelle |
|---|---|---|
| Rate-Limit | **120 GET/min pro IP**, `block=True` (überschüssige Requests → HTTP 429) | [`health.py`](../src/core/views/health.py) L148 (`ratelimit` Decorator) |
| Detail-Cache-TTL | **15 s** — SMTP-CONNECT, Disk-Scan, Backup-Scan, Stale-Jobs-Scan werden gecacht, damit häufiges Polling nicht jedes Mal einen SMTP-Handshake + Filesystem-/DB-Scan auslöst | L36 (`_DETAIL_CACHE_TTL_SECONDS`) |
| Backup-Warn-Schwelle | **48 h** → `degraded` | L29 (`BACKUP_WARN_HOURS`) |
| Disk-Warn-Schwelle | **< 10 %** frei → `degraded` | L30 (`DISK_WARN_PCT`) |
| SMTP-Timeout | **2 s** CONNECT | L31 (`SMTP_TIMEOUT_SECONDS`) |
| Stale-Jobs-Schwelle | jeder Job mit `critical` → `degraded` (Schwellwert je Job in [`services/compliance/cron.py`](../src/core/services/compliance/cron.py)/[`backup.py`](../src/core/services/compliance/backup.py)) | `_check_stale_jobs` (Refs #1335) |

> **Poll-Intervall sinnvoll wählen:** Bei 120/min ist ein Poll alle 30–60 s reichlich. Die 15-s-Cache-TTL bedeutet, dass Detailfelder bei sehr schnellem Polling bis zu 15 s alt sein können — für Backup-Alter/Disk-frei irrelevant.

---

## 9. Abgrenzung: `/health/` vs. Compliance-Dashboard

`/health/` ist der **Realtime-Liveness/Komponenten**-Check für Maschinen. Für die **Compliance-/Governance-Sicht** (inkl. eines robusteren Backup-Alters- und Restore-Test-Checks) gibt es das **Compliance-Dashboard** `/system/compliance/` (super_admin, Refs #919): es aggregiert 11 Checks (DB-Rollen, **Backup-Alter**, **Restore-Test-Alter**, ClamAV + Signatur, Retention-Cron, MFA-Quote, Migrationen, Versionen, kritische Audit-Events) mit `ok`/`warning`/`critical`/`unknown` — siehe [coolify-deployment.md „Nach Go-Live"](coolify-deployment.md) und [ops-runbook §6.6](ops-runbook.md#66-backup-restore-drill-refs-720-739).

**Faustregel:** `/health/` für Uptime-/Pager-Alerting (Sekunden/Minuten), `/system/compliance/` für die periodische Betriebs-/Audit-Durchsicht (Tage). Für Totalverlust-Wiederherstellung: [disaster-recovery.md](disaster-recovery.md).
