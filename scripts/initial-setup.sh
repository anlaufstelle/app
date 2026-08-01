#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Anlaufstelle — Erstinbetriebnahme nach `docker compose up` (Refs #793, C-25)
#
# Fuehrt die in docs/admin-guide.md § 1 (Schritt 4) und § 2.1 beschriebenen
# Erst-Schritte in einer idempotenten, fail-loud Reihenfolge aus:
#
#   1. Datenbank starten und auf Bereitschaft warten
#   2. Schema anlegen/aktualisieren (One-Shot-Job `docker-migrate.sh`)
#   3. Vollen Stack starten (web + caddy + clamav)
#   4. `manage.py check --deploy`     — Deployment-Settings
#   5. `manage.py check_db_roles`     — DB-Rollen-/Ownership-Posture
#   6. Super-Admin anlegen, falls noch keiner existiert (interaktiv)
#
# Idempotenz: jeder Schritt darf beliebig oft laufen. Migrationen sind dann
# No-ops, ein vorhandener Super-Admin wird uebersprungen statt ueberschrieben.
#
# ---------------------------------------------------------------------------
# Was dieses Skript BEWUSST NICHT tut
# ---------------------------------------------------------------------------
#
# * Kein `ALTER ROLE ... NOSUPERUSER`. Die Rollen-Topologie legt
#   `deploy/postgres-init/01-app-role.sh` beim ersten Hochfahren des
#   db-Containers automatisch an: App-Rolle `NOSUPERUSER NOBYPASSRLS`,
#   Admin-/Wartungsrolle `NOSUPERUSER BYPASSRLS` und zugleich DB-Owner
#   (ADR-020, ADR-033). Ein manuelles ALTER ROLE waere auf frischen Clustern
#   wirkungslos und auf Bestands-Clustern gefaehrlich — dort ist der
#   Ownership-Umzug ein eigener, Maintainer-gefuehrter Vorgang
#   (docs/ops-runbook.md § 9.6). Dieses Skript REPARIERT die Rollen also
#   nicht, es VERIFIZIERT sie fail-loud (Schritt 5).
#
# * Keine nicht-interaktive Passwortvergabe. ADR-018 verbietet
#   Default-Passwoerter; `create_super_admin` fragt das Passwort per
#   `getpass` ab. Benutzername und E-Mail lassen sich ueber ADMIN_USERNAME /
#   ADMIN_EMAIL vorbelegen, das Passwort bewusst nicht.
#
# * Kein `setup_facility`. Das Kommando ist voll interaktiv (es nimmt keine
#   Argumente entgegen) und nur der Single-Tenant-Fallback; empfohlen ist der
#   auditierbare Weg ueber die `/system/`-UI des Super-Admins (ADR-018).
#   Das Skript weist am Ende darauf hin.
#
# ---------------------------------------------------------------------------
# Verwendung
# ---------------------------------------------------------------------------
#
#   ./scripts/initial-setup.sh
#
# Env-Variablen (alle optional):
#
#   COMPOSE_FILE        Compose-Datei, relativ zum Repo-Root oder absolut
#                       (Default: docker-compose.prod.yml)
#   ADMIN_USERNAME      Benutzername des Super-Admins (sonst interaktiv)
#   ADMIN_EMAIL         E-Mail des Super-Admins (sonst interaktiv)
#   WAIT_TIMEOUT        Sekunden, die auf einen healthy Service gewartet wird
#                       (Default: 180)
#   SKIP_SUPER_ADMIN=1  Bootstrap-Schritt ueberspringen (z.B. bei einem
#                       Wiederholungslauf ohne TTY)
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# `scripts/` liegt EINE Ebene unter dem Repo-Root — anders als `scripts/ops/`,
# das zwei Ebenen tief liegt (dort fuehrte ein einfaches `dirname` zu einem
# ins Leere zeigenden COMPOSE_FILE, Refs #1336).
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
if [[ "$COMPOSE_FILE" != /* ]]; then
    COMPOSE_FILE="${PROJECT_DIR}/${COMPOSE_FILE}"
fi
WAIT_TIMEOUT="${WAIT_TIMEOUT:-180}"

# ---------------------------------------------------------------------------
# Ausgabe-Helfer
# ---------------------------------------------------------------------------

step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    WARN: %s\n' "$*" >&2; }

fail() {
    printf '\nFEHLER: %s\n' "$*" >&2
    exit 1
}

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

# `exec -T` (kein TTY) — fuer alle nicht-interaktiven manage.py-Aufrufe.
manage() { compose exec -T web python manage.py "$@"; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

step "0/6  Voraussetzungen pruefen"

command -v docker >/dev/null 2>&1 ||
    fail "'docker' nicht gefunden — Docker Engine installieren (docs/admin-guide.md § 1)."
docker compose version >/dev/null 2>&1 ||
    fail "'docker compose' (Compose v2) nicht verfuegbar — das Compose-Plugin fehlt."
[[ -f "$COMPOSE_FILE" ]] ||
    fail "Compose-Datei nicht gefunden: ${COMPOSE_FILE}
  Erwartet im Repo-Root — liegt initial-setup.sh unter <repo>/scripts/?
  Andere Datei waehlen: COMPOSE_FILE=docker-compose.staging.yml ./scripts/initial-setup.sh"
[[ -f "${PROJECT_DIR}/.env" ]] ||
    fail "Keine .env im Repo-Root (${PROJECT_DIR}).
  Zuerst .env.example kopieren und ausfuellen — insbesondere DJANGO_SECRET_KEY,
  ENCRYPTION_KEY sowie das Drei-Rollen-Modell (POSTGRES_BOOTSTRAP_PASSWORD,
  POSTGRES_ADMIN_USER, POSTGRES_ADMIN_PASSWORD). Siehe docs/admin-guide.md § 1."

info "Compose-Datei: ${COMPOSE_FILE}"

# ---------------------------------------------------------------------------
# Warte-Helfer
# ---------------------------------------------------------------------------

# wait_for_service <service> — wartet, bis der Container `healthy` meldet.
# Compose-Dateien ohne Healthcheck fuer den Service werden nicht blockiert:
# dann gilt ein laufender Container als bereit.
wait_for_service() {
    local service="$1"
    local waited=0
    local cid health

    while :; do
        cid="$(compose ps -q "$service" 2>/dev/null || true)"
        if [[ -n "$cid" ]]; then
            health="$(docker inspect \
                -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
                "$cid" 2>/dev/null || echo "unknown")"
            case "$health" in
                healthy) return 0 ;;
                none)
                    # Kein Healthcheck definiert -> "laeuft" ist das beste Signal.
                    if [[ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false)" == "true" ]]; then
                        return 0
                    fi
                    ;;
                unhealthy)
                    fail "Service '${service}' meldet 'unhealthy'.
  Logs ansehen: docker compose -f ${COMPOSE_FILE} logs ${service}"
                    ;;
            esac
        fi

        if ((waited >= WAIT_TIMEOUT)); then
            fail "Timeout (${WAIT_TIMEOUT}s): Service '${service}' wurde nicht bereit.
  Logs ansehen: docker compose -f ${COMPOSE_FILE} logs ${service}
  Laengere Wartezeit: WAIT_TIMEOUT=600 ./scripts/initial-setup.sh"
        fi
        sleep 3
        waited=$((waited + 3))
    done
}

# ---------------------------------------------------------------------------
# 1. Datenbank
# ---------------------------------------------------------------------------

step "1/6  Datenbank starten und auf Bereitschaft warten"
compose up -d db
wait_for_service db
info "Datenbank ist bereit."

# ---------------------------------------------------------------------------
# 2. Schema
# ---------------------------------------------------------------------------

step "2/6  Schema anlegen/aktualisieren (One-Shot-Job)"
info "docker-migrate.sh laeuft als BYPASSRLS-Admin-Rolle, verifiziert vorab die"
info "Rollen-Topologie (check_db_roles) und normalisiert die Ownership."
compose run --rm --entrypoint=/app/docker-migrate.sh web

# ---------------------------------------------------------------------------
# 3. Stack
# ---------------------------------------------------------------------------

step "3/6  Vollen Stack starten"
compose up -d
wait_for_service web
info "Anwendung laeuft."

# ---------------------------------------------------------------------------
# 4. Deployment-Settings
# ---------------------------------------------------------------------------

step "4/6  Deployment-Settings pruefen (manage.py check --deploy)"
# `docker-entrypoint.sh` fuehrt denselben Check bei jedem Web-Start
# fail-closed aus (Security N6). Hier laeuft er zusaetzlich, damit die
# Erstinbetriebnahme das Ergebnis sichtbar protokolliert.
manage check --deploy
info "Keine Deployment-Warnungen."

# ---------------------------------------------------------------------------
# 5. DB-Rollen-Posture
# ---------------------------------------------------------------------------

step "5/6  DB-Rollen-Posture verifizieren (manage.py check_db_roles)"
# Exit-Code-Semantik wie in docker-migrate.sh: 0 = ok, 1 = falsches
# Attributprofil oder fehlender Ownership-Umzug (fatal), 2 = Konfiguration
# unvollstaendig (Warnung, z.B. Legacy-Setup ohne separate Admin-Rolle).
roles_exit=0
manage check_db_roles || roles_exit=$?
case "$roles_exit" in
    0)
        info "OK — App-Rolle ohne SUPERUSER/BYPASSRLS, Ownership getrennt."
        ;;
    2)
        warn "Konfiguration unvollstaendig (Exit 2) — vermutlich fehlt POSTGRES_ADMIN_USER"
        warn "in der .env. Das Drei-Rollen-Modell ist fuer Produktion vorgesehen:"
        warn "docs/ops-runbook.md § 9 'DB-Rollenmodell'."
        ;;
    *)
        fail "check_db_roles meldet ein falsches DB-Rollenprofil (Exit ${roles_exit}).
  Row Level Security waere damit wirkungslos — die Facility-Isolation faellt
  still aus. Ursachen und Behebung: docs/ops-runbook.md § 9 (Rollenmodell)
  bzw. § 9.6 (Ownership-Umzug auf Bestandsinstallationen)."
        ;;
esac

# ---------------------------------------------------------------------------
# 6. Super-Admin
# ---------------------------------------------------------------------------

# Liefert Exit 0, wenn bereits ein `super_admin` existiert. `core_user` steht
# nicht unter RLS (Migration 0047), die App-Rolle sieht die Zeile also auch
# ohne Bypass-Kontext.
super_admin_exists() {
    local probe
    probe="$(manage shell -c \
        "from core.models import User; print('yes' if User.objects.filter(role=User.Role.SUPER_ADMIN).exists() else 'no')" \
        2>/dev/null | tr -d '\r' | tail -n1)"
    [[ "$probe" == "yes" ]]
}

step "6/6  Super-Admin anlegen (Bootstrap, ADR-018)"
if [[ "${SKIP_SUPER_ADMIN:-0}" == "1" ]]; then
    info "Uebersprungen (SKIP_SUPER_ADMIN=1)."
elif super_admin_exists; then
    info "Es existiert bereits ein Super-Admin — Schritt uebersprungen (idempotent)."
else
    if [[ ! -t 0 ]]; then
        fail "Kein interaktives Terminal, aber es existiert noch kein Super-Admin.
  'create_super_admin' fragt das Passwort per getpass ab — es gibt bewusst
  kein Default-Passwort (ADR-018). Entweder das Skript in einer interaktiven
  Shell starten oder mit SKIP_SUPER_ADMIN=1 laufen lassen und danach:
  docker compose -f ${COMPOSE_FILE} exec web python manage.py create_super_admin"
    fi
    admin_args=()
    if [[ -n "${ADMIN_USERNAME:-}" ]]; then
        admin_args+=(--username "$ADMIN_USERNAME")
    fi
    if [[ -n "${ADMIN_EMAIL:-}" ]]; then
        admin_args+=(--email "$ADMIN_EMAIL")
    fi
    # `${arr[@]+"${arr[@]}"}` expandiert unter `set -u` auch bei leerem Array
    # sauber zu gar nichts (statt zu einem leeren Argument).
    compose exec web python manage.py create_super_admin ${admin_args[@]+"${admin_args[@]}"}
fi

# ---------------------------------------------------------------------------
# Abschluss
# ---------------------------------------------------------------------------

cat <<EOF

Erstinbetriebnahme abgeschlossen.

Naechste Schritte:

  1. Als Super-Admin unter /login/ anmelden und im Bereich /system/ die erste
     Einrichtung sowie die erste Anwendungsbetreuung (facility_admin) anlegen.
     Der UI-Weg ist auditierbar (SYSTEM_VIEW-Eintraege) und deshalb empfohlen.
     Reiner Single-Tenant-Fallback (interaktiv):
       docker compose -f ${COMPOSE_FILE} exec web python manage.py setup_facility

  2. Zwei-Faktor-Authentifizierung unter /mfa/settings/ einrichten — der
     Bootstrap-Super-Admin ist ab Werk MFA-pflichtig.

  3. Backups einrichten und einen Restore-Test fahren:
     docs/admin-guide.md § 3, scripts/ops/backup.sh.

  4. Betriebszustand pruefen:
       curl -fsS https://<domain>/health/            -> {"status":"ok"}
       /system/compliance/ als Super-Admin aufrufen
EOF
