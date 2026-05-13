# dev-Deployment (`dev.anlaufstelle.app`)

Live-Dev-Deployment fuer die oeffentliche Demo-Umgebung. Plain Docker
Compose auf Hetzner CX22 (Debian 13). Manueller Deploy-Trigger
(`make deploy-dev`), kein Auto-Push aus CI.

Architektur-Grundlage: [ADR-017 — Deployment-Topologie](adr/017-deployment-topology.md).
Plain Docker Compose ist primaerer Pfad und gleichzeitig Self-Host-Referenz
fuer Trager (Fachkonzept §1009).

Stage und Feature-Branch-Subdomains sind **nicht** Teil dieses Setups —
hierfuer eigene Plaene.

## Architektur

```
Internet ─► DNS (Strato) ─► Hetzner CX22
                              │
                              ├─ caddy (80/443) Reverse-Proxy + LE-TLS
                              ├─ web (8000) Gunicorn + Django
                              ├─ db (5432) PostgreSQL 16
                              └─ clamav (3310) Virenscan (Pflicht in prod-Settings)
```

- Image-Quelle: `ghcr.io/anlaufstelle/app:main`, gebaut von
  [`.github/workflows/dev-image.yml`](./.github/workflows/dev-image.yml)
  bei jedem Push auf `main`.
- Settings-Modul: [`anlaufstelle.settings.devlive`](./src/anlaufstelle/settings/devlive.py)
  (erbt `prod.py`, ueberschreibt nur `EMAIL_BACKEND` auf Console).
- Persistenz: Docker-Volumes `pgdata_dev`, `media_dev`,
  `caddy_data_dev`, `caddy_config_dev`.
- Backup: [`deploy/backup.sh`](./deploy/backup.sh) mit
  `pg_dump -Fc -Z 6 | openssl enc -aes-256-cbc`, abgelegt in
  `/var/backups/anl`, 14 Tage Retention.

## Voraussetzungen

| Was | Wer | Wann |
|---|---|---|
| Hetzner Cloud Server (CX22, Debian 13, IPv4+IPv6) | Operator | einmalig |
| DNS-Records `dev.anlaufstelle.app` (A + AAAA) | Operator | einmalig |
| Reverse-DNS (PTR) auf `dev.anlaufstelle.app` | Operator | einmalig (IPv4 + IPv6 in Hetzner-Console) |
| GitHub PAT mit `read:packages`-Scope | Operator | bei jeder PAT-Rotation |
| `.env.dev` auf dem Server | Operator | einmalig + bei ENV-Aenderungen |

## Server-Setup (einmalig)

### 1. Server bestellen

Hetzner Cloud → CX22 → Debian 13 → Standort Nuernberg/Falkenstein →
IPv4+IPv6 → SSH-Key beim Anlegen hinterlegen.

### 2. DNS bei Strato

| Typ | Name | Wert |
|---|---|---|
| A | dev | `<Hetzner-IPv4>` |
| AAAA | dev | `<Hetzner-IPv6>::1` |

### 3. Reverse-DNS (Hetzner Cloud Console)

Server → Networking → IPv4 + IPv6 → PTR auf `dev.anlaufstelle.app`.

### 4. Bootstrap

Vom Operator-Laptop (oder Sandbox):

```bash
make dev-bootstrap # idempotent — laeuft als root, einmalig
```

Das Skript installiert Docker, UFW, fail2ban, unattended-upgrades, legt
den Service-User `anlaufstelle` mit propagierten SSH-Keys an, haerten
SSHD (kein PasswordAuth, kein root-Login), ufw default-deny + 22/80/443,
ufw-docker fuer die Bridge-Luecke, Docker-Daemon Log-Rotation.

### 5. PAT auf den Server

```bash
# einmalig:
ssh root@dev.anlaufstelle.app 'echo <PAT> > /root/.ghcr_pat && chmod 600 /root/.ghcr_pat'
ssh anlaufstelle@dev.anlaufstelle.app \
  'cat /root/.ghcr_pat | docker login ghcr.io -u <github-user> --password-stdin'
```

(Operator weiss den GitHub-Username, deshalb hier kein fester Wert.)

### 6. `.env.dev` befuellen

Vorlage: [`.env.dev.example`](./.env.dev.example) → kopieren nach
`/opt/anlaufstelle/.env.dev`, alle leeren Werte fuellen, `chmod 600`.

Pflicht-Felder: `DJANGO_SECRET_KEY`, `ENCRYPTION_KEY`, `POSTGRES_PASSWORD`,
`BACKUP_ENCRYPTION_KEY`, `DJANGO_AUDIT_HASH_KEY`. Generierung:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))" # SECRET_KEY, AUDIT_HASH_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" # ENCRYPTION_KEY
openssl rand -base64 32 # BACKUP_ENCRYPTION_KEY
```

`ENCRYPTION_KEY` und `BACKUP_ENCRYPTION_KEY` **muessen** nach dem
Anlegen in einen Passwortmanager. Verlust = unentschluesselbare
Daten/Backups.

### 7. Initial-Deploy

```bash
make deploy-dev # synct compose-files, pullt:main-image, migrate
make dev-seed # einmalig: Demo-Daten + Login-Konten anlegen
```

Verifikation Schritt 7 (siehe unten) durchgehen.

## Tagesgeschaeft

| Befehl | Zweck |
|---|---|
| `make deploy-dev` | Pull aktuelles `:main`-Image, migrate, restart |
| `make dev-status` | `docker compose ps` + `curl /health/` |
| `make dev-logs` | Live-Logs `web` + `caddy` |
| `make dev-shell` | Django-Shell in `web` (interaktiv) |
| `make dev-backup` | Manueller Backup-Snapshot |

## Rollback

Image-Tag `:main` zeigt immer auf den letzten Build. Fuer Rollback auf
einen frueheren Stand:

1. Auf GitHub → Packages → `anlaufstelle` → gewuenschten `:main-<sha>`-Tag
   ausfindig machen.
2. Auf dem Server `docker-compose.dev.yml` editieren:

   ```yaml
   web:
     image: ghcr.io/anlaufstelle/app:main-<sha>
   ```

3. `docker compose -f docker-compose.dev.yml --env-file.env.dev up -d web`.

Migrationen sind nicht rollbar — falls die problematische Migration
beteiligt ist, Restore vom Backup (siehe naechster Abschnitt) noetig.

## Restore aus Backup

```bash
# am Server
cd /opt/anlaufstelle
source.env.dev # macht BACKUP_ENCRYPTION_KEY verfuegbar

openssl enc -d -aes-256-cbc -pbkdf2 \
    -pass env:BACKUP_ENCRYPTION_KEY \
    -in /var/backups/anl/dump-YYYYMMDDTHHMMSSZ.pgc.enc |
docker compose -f docker-compose.dev.yml --env-file.env.dev exec -T db \
    pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists -v
```

**Restore-Probe (Pflicht nach Initial-Deploy):** einmal in einen leeren
Wegwerf-Container restoren, `SELECT count(*) FROM core_client;` pruefen,
Container wegwerfen. Damit ist sichergestellt, dass Backup +
`BACKUP_ENCRYPTION_KEY` zusammenpassen.

## Verifikations-Checkliste

```bash
# DNS
dig dev.anlaufstelle.app A +short # → Hetzner-IPv4
dig dev.anlaufstelle.app AAAA +short # → Hetzner-IPv6
dig -x <ip> +short # → dev.anlaufstelle.app

# TLS + Health
curl -I https://dev.anlaufstelle.app/health/
# erwartet: HTTP/2 200, Strict-Transport-Security, X-Robots-Tag: noindex

curl https://dev.anlaufstelle.app/robots.txt
# erwartet: User-agent: * / Disallow: /

# Server-Haerte
nmap dev.anlaufstelle.app -p- # nur 22/80/443
ssh root@dev.anlaufstelle.app # → Permission denied

# RLS-Pflicht: DB-User kein Superuser, NOBYPASSRLS, Admin-User getrennt
ssh anlaufstelle@dev.anlaufstelle.app \
  'cd /opt/anlaufstelle && docker compose -f docker-compose.dev.yml --env-file.env.dev \
   exec db psql -U postgres -tAc "SELECT usename, usesuper, usebypassrls FROM pg_user WHERE usename IN ('\''anlaufstelle'\'', '\''anlaufstelle_admin'\'') ORDER BY usename"'
# erwartet:
# anlaufstelle|f|f
# anlaufstelle_admin|f|t
```

## DB-User-Modell (ADR-005 Update 2026-05-09)

Drei DB-User-Rollen:

| User | Rechte | Wofür |
|---|---|---|
| `postgres` | Bootstrap-Superuser | Init-Script, Notfall-Wartung. **Nicht** für Runtime/Operator-Tasks. |
| `anlaufstelle` | NOSUPERUSER, NOBYPASSRLS | Django-Runtime. RLS-Policies greifen scharf. |
| `anlaufstelle_admin` | NOSUPERUSER, BYPASSRLS | `seed`, `migrate`, künftige Wartungs-Tasks. Wird vom Init-Script angelegt. |

Operator-Tasks connecten als `anlaufstelle_admin` über ENV-Override:

```bash
docker compose -f docker-compose.dev.yml --env-file.env.dev run --rm \
    -e POSTGRES_USER="$POSTGRES_ADMIN_USER" \
    -e POSTGRES_PASSWORD="$POSTGRES_ADMIN_PASSWORD" \
    web python manage.py <command>
```

Die Make-Targets `make dev-seed` und der `migrate`-Schritt in [`deploy/deploy-dev.sh`](./deploy/deploy-dev.sh) machen das automatisch.

## Sentry / Monitoring (optional)

`SENTRY_DSN` in `.env.dev` setzen, dann
`docker compose.. up -d web`. Ohne DSN ist Sentry inaktiv —
prod.py prueft das nicht.

Externer Healthcheck-Watchdog (UptimeRobot, healthchecks.io) ist
**nicht Teil dieses Setups**. Manuelles `make dev-status` reicht fuer
dev. Spaeter eigener Plan.

## Multi-Stage spaeter (stage / Feature-Branches)

Das Pattern fuer kommende Stages ist in [ADR-017](adr/017-deployment-topology.md)
festgehalten. Kurzfassung:

- Pro Stage ein eigener Compose-Stack (`docker-compose.stage.yml`,
  `docker-compose.feature-<name>.yml`) neben dem heutigen `docker-compose.dev.yml`.
- Eigene Volumes pro Stage (`pgdata_stage`, `media_stage`, …) — Compose-Project-Name
  isoliert die Stacks automatisch.
- **Solange nur dev existiert (heute):** Caddy laeuft als Service im dev-Stack —
  minimal, kein zusaetzlicher Reverse-Proxy-Stack.
- **Sobald >1 Stage gleichzeitig laeuft:** Caddy wird in einen eigenen
  `docker-compose.proxy.yml`-Stack ausgegliedert mit zentralem `Caddyfile`
  und `import sites.d/*.caddy`. Jeder Stage-Stack legt seinen Site-Block
  in `sites.d/` ab. Das ist ein eigener PR/Plan, **nicht** Teil dieses
  Runbooks.
- Auto-PR-Preview ist nicht vorgesehen — Feature-Branch-Subdomains werden
  bewusst manuell angelegt (siehe ADR-017 § Consequences).

## Bezuege

- ADR: [ADR-017 — Deployment-Topologie](adr/017-deployment-topology.md)
- Issue: 
- Settings: [`src/anlaufstelle/settings/devlive.py`](./src/anlaufstelle/settings/devlive.py)
- Workflow: [`.github/workflows/dev-image.yml`](./.github/workflows/dev-image.yml)
- Compose: [`docker-compose.dev.yml`](./docker-compose.dev.yml)
- Caddy: [`Caddyfile.dev`](./Caddyfile.dev)
- Skripte: [`deploy/`](./deploy/)
- Bestehender prod-Hintergrund (RLS): [`docs/ops-runbook.md`](ops-runbook.md)
- Alternative Plattform (Coolify): [`docs/coolify-deployment.md`](coolify-deployment.md)
