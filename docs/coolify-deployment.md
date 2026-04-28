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
- `CLAMAV_ENABLED=true` — bleibt Default, deaktivieren nur wenn kein ClamAV-Container läuft

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

## Nach Go-Live

- Gesundheitsprüfung: `curl https://anlaufstelle.app/health/` → `{"status":"ok",...}`
- Sentry-Events in den ersten 24h prüfen
- Backup-Job erstmalig manuell triggern und restore auf Staging testen
- Monitoring-Alerts (Uptime + Disk + RAM) einrichten

## Referenzen

- Ops-Runbook: [`docs/ops-runbook.md`](ops-runbook.md)
- Release-Checkliste: [`docs/release-checklist.md`](release-checklist.md)
- Security-Review: [`SECURITY.md`](../SECURITY.md)
