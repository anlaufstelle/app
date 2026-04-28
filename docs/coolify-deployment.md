# Coolify-Deployment (Hetzner CX22)

Leitfaden für das erste Produktiv-Deployment auf einem selbstgehosteten Coolify
auf Hetzner CX22 (2 vCPU, 4 GB RAM, 40 GB SSD, ~€5/Monat). Refs [#554](https://github.com/tobiasnix/anlaufstelle/issues/554).

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
- **Git-Repo:** `https://github.com/tobiasnix/anlaufstelle`
- **Branch:** `main`
- **Compose-File:** `docker-compose.prod.yml`
- **Domain:** `anlaufstelle.app` (Coolify kümmert sich um TLS via Traefik)

> **ClamAV-Service:** `docker-compose.prod.yml` definiert einen Service `clamav`
> (Image `clamav/clamav:stable`, Volume `clamav-db`, Healthcheck via `clamdcheck.sh`).
> Coolify startet ihn beim Compose-Deploy automatisch mit — der `web`-Service
> verbindet via `CLAMAV_HOST=clamav` und wartet per `depends_on: service_healthy`
> auf die Signaturdatenbank. Refs [#524](https://github.com/tobiasnix/anlaufstelle/issues/524).
>
> - **Separater ClamAV-Host:** `CLAMAV_HOST`/`CLAMAV_PORT` in den ENVs auf den
>   externen Host setzen, `CLAMAV_ENABLED=true` belassen.
> - **Kompletter Verzicht:** `CLAMAV_ENABLED=false` (z.B. minimaler Dev-Server).
>   **Nicht für produktive Einrichtungen empfohlen** — Upload-Malware-Scanning entfällt.
> - **Healthcheck:** `curl https://anlaufstelle.app/health/` liefert u.a. `clamav: ok`
>   bzw. `clamav: error`, solange `CLAMAV_ENABLED=true`.

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

Coolify zieht das Image automatisch aus `ghcr.io/tobiasnix/anlaufstelle:latest`.

### 6. Initial-Setup

Einmalig auf dem Server in der Web-Container-Shell:

```bash
python manage.py migrate
python manage.py setup_facility   # Admin-User + Facility anlegen
```

Der Admin erhält eine Einladungs-E-Mail mit Setup-Link (Token-Invite-Flow, [#528](https://github.com/tobiasnix/anlaufstelle/issues/528)).

### 7. 2FA aktivieren

Nach erstem Login sollte der Admin unter `/mfa/settings/` sofort TOTP einrichten
(Refs [#521](https://github.com/tobiasnix/anlaufstelle/issues/521)). Für Einrichtungen
mit hohem Sicherheitsbedarf in `Settings.mfa_enforced_facility_wide = True` setzen.

### 7.5 Offline-Modus (Streetwork)

Der Offline-Modus (M6A, Refs [#573](https://github.com/tobiasnix/anlaufstelle/issues/573))
ist ein reines Client-Feature — **keine Server-ENVs, keine zusätzliche Infrastruktur nötig**.

**Voraussetzung an Endgeräte:** Mitarbeiter-Geräte brauchen einen modernen Browser
mit **IndexedDB**- und **WebCrypto**-Support (aktuelle Chromium-, Firefox-, Safari-
und Edge-Versionen erfüllen das).

**Admins sollten Mitarbeiter vor Rollout auf drei Punkte hinweisen:**

1. **Vor Offline-Einsatz Klientel-Cache füllen** — Onboarding-Schritt am Arbeitsplatz,
   damit die benötigten Datensätze lokal verschlüsselt verfügbar sind.
2. **Nach Rückkehr synchronisieren, bevor der Logout erfolgt** — sonst bleiben
   Änderungen nur lokal liegen und gehen beim Cache-Verlust verloren.
3. **Passwort-Verlust = Datenverlust:** Bei vergessenem Passwort sind die offline
   gespeicherten, lokal verschlüsselten Daten **unrettbar** — Recovery-Flows für
   Offline-Daten sind nicht möglich.

## Nach Go-Live

- Gesundheitsprüfung: `curl https://anlaufstelle.app/health/` → `{"status":"ok",...}`
- ClamAV-Verbindung prüfen: `curl https://anlaufstelle.app/health/` → `clamav: ok`
  (sonst Service-Logs von `clamav` in Coolify checken, Signatur-Download kann
  nach Kaltstart bis zu 5 Minuten dauern).
- RLS aktiv prüfen: per `psql` in der App-DB
  `SELECT relrowsecurity FROM pg_class WHERE relname='core_client';` → `t`.
- Sentry-Events in den ersten 24h prüfen
- Backup-Job erstmalig manuell triggern und restore auf Staging testen
- Monitoring-Alerts (Uptime + Disk + RAM) einrichten

## Referenzen

- Ops-Runbook: [`docs/ops-runbook.md`](ops-runbook.md)
- Release-Checkliste: [`docs/release-checklist.md`](release-checklist.md)
- Security-Review: [`SECURITY.md`](../SECURITY.md)
