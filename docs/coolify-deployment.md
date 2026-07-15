# Coolify-Deployment (Hetzner CX22)

> ℹ️ **Alternative, kein primaerer Pfad.** Primaerer Deployment-Pfad ist
> Plain Docker Compose ([ADR-017](adr/017-deployment-topology.md),
> [docs/dev-deployment.md](dev/dev-deployment.md)) — verankert in
> Fachkonzept §1009 (`docker compose up` als harte Anforderung).
> Diese Coolify-Anleitung bleibt erhalten als **unterstuetzte Alternative**
> fuer Trager, die Coolify bereits einsetzen, oder fuer kuenftige
> Bedarfsfaelle, die Plain Compose nachweislich nicht abdeckt — sie ist
> nicht abgekuendigt, sondern nicht-primaer.

Leitfaden für das erste Produktiv-Deployment auf einem selbstgehosteten Coolify
auf Hetzner CX22 (2 vCPU, 4 GB RAM, 40 GB SSD, ~€5/Monat). Refs #554.

## Voraussetzungen

- Hetzner-Account (CX22 gebucht, Ubuntu 22.04 LTS oder 24.04 LTS)
- Domain `anlaufstelle.app` mit DNS-A-Record auf die Server-IP
- SSH-Zugang zum Server

## Schritte

### 1. Server grundhärten

```bash
ssh root@<server-ip>
apt update && apt upgrade -y
ufw allow 22,80,443/tcp && ufw enable
# SSH-Keys statt Passwort, dann sshd_config PermitRootLogin prohibit-password
```

### 2. Coolify installieren

Offizieller One-Liner:

```bash
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash
```

Initial-Admin anlegen über `http://<server-ip>:8000`.

### 3. Projekt anlegen

- **Typ:** Docker Compose
- **Git-Repo:** `https://github.com/anlaufstelle/app`
- **Branch:** `main`
- **Compose-File:** `docker-compose.prod.yml`
- **Domain:** `anlaufstelle.app` (Coolify kümmert sich um TLS via Traefik)

> **ClamAV-Service:** `docker-compose.prod.yml` definiert einen Service `clamav`
> (Image `clamav/clamav:stable`, Volume `clamav-db`, Healthcheck via
> `clamdscan --version`; Refs #938).
> Coolify startet ihn beim Compose-Deploy automatisch mit — der `web`-Service
> verbindet via `CLAMAV_HOST=clamav` und wartet per `depends_on: service_healthy`
> auf die Signaturdatenbank. Konfiguration und Betrieb (Fail-closed, Signatur-
> Updates, EICAR-Test) sind kanonisch in [docs/ops-runbook.md § 7](ops-runbook.md)
> beschrieben. Refs #524.
>
> - **Separater ClamAV-Host:** `CLAMAV_HOST`/`CLAMAV_PORT` in den ENVs auf den
> externen Host setzen, `CLAMAV_ENABLED=true` belassen.
> - **Kompletter Verzicht:** `CLAMAV_ENABLED=false` (z.B. minimaler Dev-Server).
> **Nicht für produktive Einrichtungen empfohlen** — Upload-Malware-Scanning entfällt.
> - **Healthcheck:** `curl https://anlaufstelle.app/health/` liefert u.a. `clamav: ok`
> bzw. `clamav: error`, solange `CLAMAV_ENABLED=true`. Das Feld ist ein Alias
> auf das kanonische `virus_scanner` (Werte `connected`/`unavailable`/`disabled`).
> Bei Scanner-Ausfall wird `status: degraded` gesetzt — der Container-Healthcheck
> im Dockerfile liefert dann ungesund, das HTTP bleibt aber 200, damit der
> Last-Balancer den Pod nicht direkt rauswirft.

### 4. Environment-Variablen

In Coolify unter *Environment Variables* nach Muster aus [`.env.example`](../.env.example):

**Pflicht:**
- `DJANGO_SECRET_KEY` — frisch generiert (`python -c "import secrets; print(secrets.token_urlsafe(50))"`)
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- `ENCRYPTION_KEY` oder `ENCRYPTION_KEYS` — Fernet-Key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
- `ALLOWED_HOSTS=anlaufstelle.app,www.anlaufstelle.app`
- `DOMAIN=anlaufstelle.app`
- `BACKUP_ENCRYPTION_KEY` — separater Fernet-Key für Backups
- `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` — SMTP
- `TRUSTED_PROXY_HOPS=1` (Caddy/Traefik → App)

**Optional:**
- `SENTRY_DSN` — für Error-Tracking
- `LOG_FORMAT=json` — strukturiertes Logging
- `CLAMAV_ENABLED=true` — Default. Zum aktiven Scannen muss zusätzlich ein
 ClamAV-Container erreichbar sein (siehe Hinweiskasten in Schritt 3). Nur
 deaktivieren, wenn bewusst kein ClamAV betrieben wird.
- `CLAMAV_HOST` — default `clamav` (Service-Name aus `docker-compose.prod.yml`).
 Bei externem ClamAV auf dessen Hostname/IP setzen.
- `CLAMAV_PORT` — default `3310` (clamd TCP-Port).
- `CLAMAV_TIMEOUT` — Timeout in Sekunden für Scan-Requests (optional).

### 5. Ersten Deploy anstoßen

Coolify zieht das Image automatisch aus `ghcr.io/anlaufstelle/app:${APP_VERSION}`
([`docker-compose.prod.yml`](../docker-compose.prod.yml)). Es gibt **kein**
`:latest`-Tag — ausgeliefert wird immer die per `APP_VERSION` gepinnte
Release-Version (Default `v0.22.0`, siehe [`docker-compose.prod.yml`](../docker-compose.prod.yml)).

### 6. Initial-Setup

Einmalig nach dem ersten Deploy, per SSH auf dem Server:

```bash
# Migrationen laufen NICHT im Entrypoint, sondern als separater One-Shot-Job
# vor dem Rolling-Restart (docker-migrate.sh, Refs #802/C-34) — prueft vorab
# via check_db_roles das DB-Rollenprofil (Refs #1002).
docker compose -f docker-compose.prod.yml run --rm --entrypoint=/app/docker-migrate.sh web

# Super-Admin anlegen (Pflicht-Schritt, kein Default-Passwort, ADR-018)
docker compose -f docker-compose.prod.yml exec web \
    python manage.py create_super_admin
```

Der Super-Admin (`role=super_admin`) legt danach über die `/system/`-UI die erste
Einrichtung sowie die erste Anwendungsbetreuung (`facility_admin`) an — empfohlen,
weil über die UI auditierbar (`SYSTEM_VIEW`-Einträge). Für reine
Single-Tenant-Installationen, in denen System- und Anwendungsbetreuung dieselbe
Person sind, gibt es als interaktiven CLI-Fallback `python manage.py setup_facility`.
Details: [ADR-018](adr/018-rollenmodell-superadmin.md),
[docs/dev/dev-deployment.md § Production-Bootstrap](dev/dev-deployment.md),
[docs/admin-guide.md § 2.2](admin-guide.md).

> **⚠️ PostgreSQL-Rolle: NOSUPERUSER ist Pflicht.** Der in `POSTGRES_USER` konfigurierte DB-User darf **kein** PostgreSQL-Superuser sein — sonst wird Row Level Security (Migration [`0047_postgres_rls_setup.py`](../src/core/migrations/0047_postgres_rls_setup.py)) per Postgres-Default **bypasst** und das Facility-Isolations-Safety-Net ist wirkungslos.
>
> Das Init-Script [`deploy/postgres-init/01-app-role.sh`](../deploy/postgres-init/01-app-role.sh) legt die Rollen automatisch korrekt an (Refs #902). **Das vollstaendige, code-treue Rollenmodell** (Bootstrap-/App-/Admin-Rolle, Attribute, Membership, `session_replication_role`-Grant) ist kanonisch in **[docs/ops-runbook.md § 9 „DB-Rollenmodell"](ops-runbook.md)** beschrieben — hier nur das Coolify-Wesentliche.
>
> Voraussetzung: `POSTGRES_BOOTSTRAP_PASSWORD`, `POSTGRES_ADMIN_USER` und `POSTGRES_ADMIN_PASSWORD` zusaetzlich zu den App-Variablen in den Coolify-ENVs setzen (Muster: [`.env.example`](../.env.example)). Prüfen nach dem ersten Hochfahren:
>
> ```bash
> docker compose exec web python manage.py check_db_roles
> ```
>
> Erwartet: App-Rolle `rolsuper=False, rolbypassrls=False` und Admin-Rolle `rolsuper=False, rolbypassrls=True` (Exit-Code 0).

### 7. 2FA aktivieren

Nach erstem Login sollte der Admin unter `/mfa/settings/` sofort TOTP einrichten
(Refs #521). Für Einrichtungen
mit hohem Sicherheitsbedarf in `Settings.mfa_enforced_facility_wide = True` setzen.

### 7.5 Offline-Modus (Streetwork)

Der Offline-Modus (M6A, Refs #573)
ist ein reines Client-Feature — **keine Server-ENVs, keine zusätzliche Infrastruktur nötig**.

**Voraussetzung an Endgeräte:** Mitarbeiter-Geräte brauchen einen modernen Browser
mit **IndexedDB**- und **WebCrypto**-Support (aktuelle Chromium-, Firefox-, Safari-
und Edge-Versionen erfüllen das).

**Admins sollten Mitarbeiter vor Rollout auf drei Punkte hinweisen:**

1. **Vor Offline-Einsatz Personen-Cache füllen** — Onboarding-Schritt am Arbeitsplatz,
 damit die benötigten Datensätze lokal verschlüsselt verfügbar sind.
2. **Nach Rückkehr synchronisieren, bevor der Logout erfolgt** — sonst bleiben
 Änderungen nur lokal liegen und gehen beim Cache-Verlust verloren.
3. **Passwort-Verlust = Datenverlust:** Bei vergessenem Passwort sind die offline
 gespeicherten, lokal verschlüsselten Daten **unrettbar** — Recovery-Flows für
 Offline-Daten sind nicht möglich.

## Caddy-Konfiguration

Die mitgelieferte [`Caddyfile`](../Caddyfile) (Refs #801) enthält:

- **www-Redirect** — `www.{$DOMAIN}` wird permanent (`301`) auf den Apex umgeleitet, damit Backlinks und Cookies eindeutig auf eine Origin gehen.
- **HTTPS + HSTS** — Caddy beantragt automatisch Let's-Encrypt-Zertifikate, HSTS mit `max-age=31536000` ist gesetzt.
- **Access-Log** — JSON-Log nach `/var/log/caddy/access.log` mit Rotation (10 MiB, 10 Files, 30 Tage). Persistiert über das `caddy_logs`-Volume in [`docker-compose.prod.yml`](../docker-compose.prod.yml). Auswertung z. B. via `docker compose exec caddy cat /var/log/caddy/access.log | jq`.
- **CSP** — Content-Security-Policy wird **ausschließlich in Django** über `django-csp` gesetzt (`anlaufstelle.settings.base.CONTENT_SECURITY_POLICY`); im Caddyfile bewusst nicht doppelt, damit App- und Proxy-Policy nicht auseinanderlaufen.

### Optionaler Rate-Limit

Caddy 2 unterstützt Rate-Limits **nur** über das nicht offizielle Modul [caddy-ratelimit](https://github.com/mholt/caddy-ratelimit). Wer Brute-Force-Schutz auf Reverse-Proxy-Ebene möchte, baut ein eigenes Caddy-Image mit dem Modul (`xcaddy build --with github.com/mholt/caddy-ratelimit`) und ergänzt im `{$DOMAIN}`-Block z. B.:

```caddy
rate_limit {
    zone login_zone {
        key {http.request.remote_host}
        events 10
        window 1m
    }
    match {
        path /login/* /accounts/* /api/*
    }
}
```

Im Default-Setup ist die Rate-Limit-Stanza im `Caddyfile` als Kommentar dokumentiert. Defense-in-Depth: Django selbst hat einen Login-Throttle (siehe `core/views/auth.py`), die Caddy-Stufe ist optional.

### Staging

`Caddyfile.staging` ist strukturgleich, hat aber zusätzlich einen Hinweis auf `tls internal` — falls die Stage-Domain nicht öffentlich auflösbar ist (interner Reverse-Proxy oder CDN davor), kann Caddy auf seine eigene CA wechseln statt LE.

## Nach Go-Live

- Gesundheitsprüfung: `curl https://anlaufstelle.app/health/` → `{"status":"ok",...}`
- ClamAV-Verbindung prüfen: `curl https://anlaufstelle.app/health/` → `clamav: ok`
 (sonst Service-Logs von `clamav` in Coolify checken, Signatur-Download kann
 nach Kaltstart bis zu 5 Minuten dauern).
- RLS aktiv prüfen: per `psql` in der App-DB
 `SELECT relrowsecurity FROM pg_class WHERE relname='core_client';` → `t`.
- RLS-Wirksamkeit prüfen: `docker compose exec web python manage.py check_db_roles` (Refs #902) — verifiziert App-Rolle = `NOSUPERUSER NOBYPASSRLS` und Admin-Rolle = `NOSUPERUSER BYPASSRLS`. Exit-Code 0 = ok.
- **Compliance-Dashboard** (Refs #919): als `super_admin` `/system/compliance/` aufrufen. Aggregiert 11 Checks (DB-Rollen, Backup-Alter, Restore-Test, ClamAV-Erreichbarkeit + Signatur, Retention-Cron, MFA-Quote, Migrationen, Versionen, kritische Audit-Events der letzten 24h) mit `ok`/`warning`/`critical`/`unknown`-Status, Detail und Handlungsempfehlung. Pflichtcheck nach dem ersten Restore-Test (siehe unten).
- Sentry-Events in den ersten 24h prüfen
- Backup-Job erstmalig manuell triggern und restore auf Staging testen
- **Restore-Test dokumentieren** (Refs #919): nach jedem erfolgreichen Restore-Test
 `docker compose exec web python manage.py mark_restore_verified --note "Restore aus YYYY-MM-DD-Backup gegen anlaufstelle_restore_test"` ausführen. Schreibt einen `RESTORE_VERIFIED`-AuditLog-Eintrag, den das Compliance-Dashboard als Alter-Indikator nutzt (ok ≤90 Tage, warning ≤180, critical älter).
- Monitoring-Alerts (Uptime + Disk + RAM) einrichten

## Referenzen

- Ops-Runbook: [`docs/ops-runbook.md`](ops-runbook.md)
- Release-Checkliste: `docs/release-checklist.md` (dev-only)
- Security-Review: [`SECURITY.md`](../SECURITY.md)
