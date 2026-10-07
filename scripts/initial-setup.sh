#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# Anlaufstelle — first-time setup, starts the stack itself (Refs #793, C-25)
#
# Runs the first-time steps described in docs/admin-guide.md § 1 (step 4) and
# § 2.1 in an idempotent, fail-loud order:
#
#   1. Start the database and wait until it is ready
#   2. Create/update the schema (one-shot job `docker-migrate.sh`)
#   3. Start the full stack (db, redis, web, cron, caddy, clamav)
#   4. `manage.py check --deploy`     — deployment settings
#   5. `manage.py check_db_roles`     — DB role/ownership posture
#   6. Create a super admin if none exists yet (interactive)
#
# Idempotency: every step may run any number of times. Migrations are then
# no-ops, an existing super admin is skipped instead of overwritten.
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
# * Kein `setup_facility`. Das Kommando fragt Organisation, Einrichtung,
#   Benutzername und Passwort interaktiv ab und ist nur der Single-Tenant-
#   Fallback; empfohlen ist der UI-Weg als Super-Admin im Django-Admin
#   (`/admin-mgmt/`, ADR-018) — der einzige Laufzeit-Anlagepfad fuer eine
#   Einrichtung (`core/admin/organization.py`). Zudem braucht das Kommando die
#   Admin-DB-Rolle (BYPASSRLS), die dieses Skript bewusst nicht in die Umgebung
#   des Web-Containers hebt. Das Skript weist am Ende mit dem vollstaendigen
#   Aufruf darauf hin.
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
#   ENV_FILE            Env-Datei, relativ zum Repo-Root oder absolut
#                       (Default: .env). Wird IMMER als `--env-file` an
#                       `docker compose` durchgereicht — fuer `.env` ist das
#                       ein No-op, fuer den Staging-Stack Pflicht: dort
#                       kommen auch die DB-Rollen-Secrets ueber
#                       Compose-Interpolation (Refs #1370, #2086).
#                       Zusaetzlich wird STAGING_ENV_FILE auf denselben Pfad
#                       exportiert, damit auch der service-level `env_file:`
#                       von maintenance/cron mitzieht — `--env-file` allein
#                       ersetzt ihn NICHT (Refs #2086).
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
# Refs #2309: docs/admin-guide.md fuehrt `compose.legal.yml` (Pflichtangaben)
# und `compose.override.yml` (weitere lokale Anpassungen) als vorgesehenen
# Erweiterungsweg ein und baut in JEDEM Recreate-Beispiel die volle
# Dateiliste `-f docker-compose.prod.yml -f compose.legal.yml -f
# compose.override.yml` auf. Eine vor der Erstinbetriebnahme angelegte
# Zusatzdatei blieb beim Erststart bislang wirkungslos, weil dieses Skript
# nur COMPOSE_FILE einsetzte. Deshalb: beide Dateien automatisch im selben
# Verzeichnis wie COMPOSE_FILE erkennen und in fester Reihenfolge anhaengen.
COMPOSE_FILES=("$COMPOSE_FILE")
compose_extra_dir="$(dirname "$COMPOSE_FILE")"
for compose_extra_file in "${compose_extra_dir}/compose.legal.yml" "${compose_extra_dir}/compose.override.yml"; do
    if [[ -f "$compose_extra_file" ]]; then
        COMPOSE_FILES+=("$compose_extra_file")
    fi
done
ENV_FILE="${ENV_FILE:-.env}"
if [[ "$ENV_FILE" != /* ]]; then
    ENV_FILE="${PROJECT_DIR}/${ENV_FILE}"
fi
# Refs #2086: `--env-file` steuert bei Compose NUR die Interpolation. Services
# mit service-level `env_file:` (staging: maintenance/cron) laden davon
# unbeeindruckt ihren eigenen Pfad. Der wird dort als
# `${STAGING_ENV_FILE:-.env.staging}` geschrieben; die exportierte Shell-
# Variable schlaegt die --env-file-Werte und zieht ihn auf dieselbe Datei.
# Fuer Stacks ohne diese Variable ist der Export folgenlos.
export STAGING_ENV_FILE="$ENV_FILE"
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

compose() { local compose_flags=() compose_file; for compose_file in "${COMPOSE_FILES[@]}"; do compose_flags+=(-f "$compose_file"); done; docker compose "${compose_flags[@]}" --env-file "$ENV_FILE" "$@"; }

# `exec -T` (kein TTY) — fuer alle nicht-interaktiven manage.py-Aufrufe.
manage() { compose exec -T web python manage.py "$@"; }

# Kompatibler Befehlsname fuer die Setup-Guards. Nur der Rollencheck muss in
# den privilegierten maintenance-Service; alle anderen Checks bleiben in web.
manage.py() {
    if [[ "${1:-}" == "check_db_roles" ]]; then
        compose run --rm maintenance "$@"
    else
        manage "$@"
    fi
}

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
  Andere Datei waehlen: COMPOSE_FILE=docker-compose.staging.yml ENV_FILE=.env.staging scripts/initial-setup.sh"
[[ -f "$ENV_FILE" ]] ||
    fail "Env-Datei nicht gefunden: ${ENV_FILE}
  Zuerst die passende Vorlage kopieren und ausfuellen (.env.example fuer prod,
  .env.staging.example fuer den Staging-Stack) — insbesondere DJANGO_SECRET_KEY,
  ENCRYPTION_KEY sowie das Drei-Rollen-Modell (POSTGRES_BOOTSTRAP_PASSWORD,
  POSTGRES_ADMIN_USER, POSTGRES_ADMIN_PASSWORD). Siehe docs/admin-guide.md § 1.
  Andere Datei waehlen: ENV_FILE=.env.staging scripts/initial-setup.sh"

# Refs #2086: Stacks, die den Health-Token an caddy durchreichen (heute:
# staging), brauchen ihn NICHT-LEER. Caddy ersetzt `{$DJANGO_HEALTH_DETAIL_TOKEN}`
# schon beim Adaptieren der Caddyfile; ohne Wert bricht die Adaption ab und der
# Reverse-Proxy startet gar nicht erst. Ohne diesen Preflight saehe man davon
# nur einen Stack, der nicht hochkommt — die Ursache stuende ausschliesslich in
# `docker compose logs caddy`.
# Die Pruefung haengt bewusst an der Compose-Datei statt am Stack-Namen: fuer
# prod ist sie ein No-op, ein spaeterer Stack mit demselben Muster ist
# automatisch mitgeschuetzt.
# Wert einer Variablen so aus der Env-Datei lesen, wie Compose ihn sieht.
# Die Regeln sind nicht geraten, sondern mit `docker compose config` gemessen
# (Refs #2086, Fix-Runde 6):
#   - fuehrende Leerzeichen und ein `export `-Praefix sind erlaubt,
#   - bei mehrfacher Definition gewinnt die LETZTE,
#   - umschliessende Anfuehrungszeichen gehoeren nicht zum Wert.
# Eine erste Fassung dieser Pruefung liess `=""`, `=''`, reinen Whitespace und
# `="   "` als "gesetzt" durch (caddy startet damit nicht) und wies umgekehrt
# `export VAR=wert` sowie eingerueckte Zeilen faelschlich ab.
env_file_value() {
    local name="$1" file="$2" line value
    # `|| true`: fehlt die Zeile ganz, liefert grep Exit 1 — unter `set -o
    # pipefail` haette das die Zuweisung und damit das ganze Skript OHNE
    # Meldung abgebrochen. Der leere Wert soll in die Pruefung laufen.
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${name}=" "$file" | tail -n1 || true)"
    [[ -n "$line" ]] || return 0
    value="${line#*=}"
    if [[ ${#value} -ge 2 && "$value" == '"'*'"' ]]; then
        value="${value:1:${#value}-2}"
    elif [[ ${#value} -ge 2 && "$value" == "'"*"'" ]]; then
        value="${value:1:${#value}-2}"
    fi
    printf '%s' "$value"
}

# Compose rendert hier bewusst nur den Caddy-Service. Damit entscheidet die
# tatsaechliche, per YAML aufgeloeste Service-Umgebung statt eines zufaelligen
# Variablennamens an anderer Stelle der Compose-Datei.
caddy_requires_health_token() {
    local caddy_config
    caddy_config="$(compose config 2>/dev/null)" ||
        fail "Caddy-Service aus der Compose-Datei konnte nicht ausgewertet werden: ${COMPOSE_FILE}"
    awk '
        $0 == "  caddy:" { in_caddy = 1; next }
        in_caddy && /^  [^[:space:]]/ { in_caddy = 0 }
        in_caddy && $0 == "    environment:" { in_environment = 1; next }
        in_environment && /^    [^[:space:]]/ { in_environment = 0 }
        in_environment && /^      DJANGO_HEALTH_DETAIL_TOKEN:/ { found = 1 }
        END { exit(found ? 0 : 1) }
    ' <<<"$caddy_config"
}

if caddy_requires_health_token; then
    health_token="$(env_file_value DJANGO_HEALTH_DETAIL_TOKEN "$ENV_FILE")"
    # Reiner Whitespace ist kein brauchbares Token — genauso behandeln wie leer.
    [[ -n "${health_token//[[:space:]]/}" ]] ||
        fail "DJANGO_HEALTH_DETAIL_TOKEN fehlt oder ist leer in ${ENV_FILE}.
  Dieser Stack reicht den Wert an caddy durch; ohne ihn adaptiert Caddy seine
  Konfiguration nicht und der Reverse-Proxy startet gar nicht erst.
  Wert erzeugen: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
fi

info "Compose-Dateien: ${COMPOSE_FILES[*]}"
info "Env-Datei:        ${ENV_FILE}"

# Ein fester Top-Level-Compose-Name trennt neue Dev-/Prod-Volumes. Bei einem
# Upgrade kann dadurch ein unter dem frueheren, von Compose normalisierten
# Verzeichnisnamen angelegtes pgdata-Volume aus dem aktiven Projekt fallen.
# Nur dieser eindeutige Kandidat ist ein sinnvoller Upgrade-Hinweis; Volumes
# anderer Stacks duerfen auf einer Erstinstallation keine Warnung ausloesen.
# Erst vollstaendig einlesen, dann filtern: ein frueh endender Konsument (awk … exit)
# liesse den Producer unter pipefail mit SIGPIPE (141) sterben — auf langsamen
# Runnern reproduzierbar.
compose_config_output="$(compose config 2>/dev/null)"
active_project_name="$(awk '$1 == "name:" && !found { name = $2; found = 1 } END { print name }' <<<"$compose_config_output")"
if [[ -n "$active_project_name" ]]; then
    compose_directory_name="$(basename "$(cd "$(dirname "$COMPOSE_FILE")" && pwd)")"
    legacy_project_name="$(printf '%s' "$compose_directory_name" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-' | sed 's/^[^a-z0-9]*//')"
    active_pgdata_volume="${active_project_name}_pgdata"
    legacy_pgdata_volume="${legacy_project_name}_pgdata"
    active_pgdata_found=0
    legacy_pgdata_found=0
    while IFS= read -r volume_name; do
        [[ "$volume_name" != "$active_pgdata_volume" ]] || active_pgdata_found=1
        [[ -z "$legacy_project_name" || "$volume_name" != "$legacy_pgdata_volume" ]] || legacy_pgdata_found=1
    done < <(docker volume ls --format '{{.Name}}' 2>/dev/null || true)
    if [[ "$active_pgdata_found" == 0 && "$legacy_pgdata_found" == 1 ]]; then
        printf -v upgrade_hint 'Möglicherweise Bestandsinstallation: Volume `%s` gefunden, das aktive Projekt `%s` hat noch kein pgdata-Volume. Handelt es sich um ein Upgrade, `COMPOSE_PROJECT_NAME=%s` in der .env eintragen; bei einer Erstinstallation ignorieren.' "$legacy_pgdata_volume" "$active_project_name" "$legacy_project_name"
        warn "$upgrade_hint"
    fi
fi

# Der read-only Mount im web-Container wird von appuser (UID/GID 1000)
# traversiert. Das Backupkonto bleibt Eigentuemer und damit schreibberechtigt;
# fuer appuser gibt die Gruppe nur Lesen/Durchqueren frei. Keine Weltrechte.
backup_host_dir="$(env_file_value BACKUP_HOST_DIR "$ENV_FILE")"
backup_host_dir="${backup_host_dir:-./backups}"
if [[ "$backup_host_dir" != /* ]]; then
    compose_dir="$(cd "$(dirname "$COMPOSE_FILE")" && pwd)"
    backup_host_dir="${compose_dir}/${backup_host_dir}"
fi
backup_owner="$(id -un)"
if [[ -d "$backup_host_dir" ]]; then
    read -r backup_group backup_mode < <(stat -c '%g %a' "$backup_host_dir") ||
        fail "Rechte des Backup-Verzeichnisses konnten nicht gelesen werden: ${backup_host_dir}"
    if [[ "$backup_group" != "1000" || "$backup_mode" != "2750" ]]; then
        fail "Backup-Verzeichnis hat nicht Gruppe 1000 mit Modus 2750: ${backup_host_dir}
  Korrigieren:
  sudo chgrp 1000 \"${backup_host_dir}\" && sudo chmod 2750 \"${backup_host_dir}\""
    fi
else
    can_set_app_group=0
    if [[ "$(id -u)" == "0" ]] || [[ " $(id -G) " == *" 1000 "* ]]; then
        can_set_app_group=1
    fi
    if [[ "$can_set_app_group" != "1" ]]; then
        fail "Backup-Verzeichnis fehlt und das Betriebskonto darf Gruppe 1000 nicht setzen: ${backup_host_dir}
  Vor dem erneuten Setup ausfuehren:
  sudo install -d -m 2750 -o \"${backup_owner}\" -g 1000 \"${backup_host_dir}\""
    fi
    install -d -m 2750 -o "$backup_owner" -g 1000 "$backup_host_dir" ||
        fail "Backup-Verzeichnis konnte nicht mit Modus 2750 fuer Backupkonto und appuser angelegt werden: ${backup_host_dir}"
fi
info "Backup-Verzeichnis vorbereitet: ${backup_host_dir} (Modus 2750, fuer appuser UID 1000 lesbar)."

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
  Logs ansehen: docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} logs ${service}"
                    ;;
            esac
        fi

        if ((waited >= WAIT_TIMEOUT)); then
            fail "Timeout (${WAIT_TIMEOUT}s): Service '${service}' wurde nicht bereit.
  Logs ansehen: docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} logs ${service}
  Laengere Wartezeit: WAIT_TIMEOUT=600 scripts/initial-setup.sh"
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
compose run --rm --entrypoint=/app/docker-migrate.sh maintenance

# ---------------------------------------------------------------------------
# 3. Stack
# ---------------------------------------------------------------------------

step "3/6  Vollen Stack starten"
compose up -d
# Refs #2312: die Erstladung der Virensignaturen kann beim allerersten Start
# mehrere Minuten ohne Zwischenausgabe dauern — ohne diesen Hinweis wirkt der
# Lauf wie ein Haenger statt wie ein normaler, nur laengerer Warteschritt.
info "Warte auf Virenscanner (Signatur-Erstladung kann beim allerersten Start mehrere Minuten dauern) und auf 'web'..."
wait_for_service web
info "Anwendung laeuft."

# ---------------------------------------------------------------------------
# 4. Deployment-Settings
# ---------------------------------------------------------------------------

step "4/6  Deployment-Settings pruefen (manage.py check --deploy)"
# `docker-entrypoint.sh` fuehrt denselben Check bei jedem Web-Start
# fail-closed aus (Security N6). Hier laeuft er zusaetzlich, damit die
# Erstinbetriebnahme das Ergebnis sichtbar protokolliert.
deploy_exit=0
deploy_output="$(
    manage.py check --deploy 2>&1
)" || deploy_exit=$?
printf '%s\n' "$deploy_output"
if [[ "$deploy_exit" != "0" ]]; then
    fail "Deployment-Settings-Check fehlgeschlagen (Exit ${deploy_exit}); Diagnose siehe oben."
fi
deploy_issue_count="$(sed -n 's/^System check identified \([0-9][0-9]*\) issue.*/\1/p' <<<"$deploy_output" | tail -n1)"
if [[ -n "$deploy_issue_count" && "$deploy_issue_count" != "0" ]]; then
    warn "${deploy_issue_count} Deployment-Warnungen gemeldet — Ausgabe oben pruefen."
else
    info "Keine Deployment-Warnungen."
fi

# ---------------------------------------------------------------------------
# 5. DB-Rollen-Posture
# ---------------------------------------------------------------------------

step "5/6  DB-Rollen-Posture verifizieren (manage.py check_db_roles)"
# Exit-Code-Semantik wie in docker-migrate.sh: 0 = ok, 1 = falsches
# Attributprofil oder fehlender Ownership-Umzug (fatal), 2 = Konfiguration
# unvollstaendig (Warnung, z.B. Legacy-Setup ohne separate Admin-Rolle).
roles_exit=0
manage.py check_db_roles || roles_exit=$?
case "$roles_exit" in
    0)
        info "OK — DB-Rollenprofil ueber den maintenance-Service verifiziert."
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

# Refs #2021: Backup bleibt ein Host-Job. Das Erstsetup sieht deshalb sowohl
# in der Crontab des aufrufenden Users als auch — soweit ohne Passwortabfrage
# erreichbar — in der Root-Crontab nach. Fehler/fehlende Rechte sind ein
# Warnsignal, aber kein Grund, die ansonsten erfolgreiche Einrichtung
# nachtraeglich abzubrechen.
backup_cron_status() {
    local user_crontab="" root_crontab="" active_crontab=""
    local line script_path saw_missing=0 saw_foreign=0
    if command -v crontab >/dev/null 2>&1; then
        user_crontab="$(crontab -l 2>/dev/null || true)"
    fi
    if command -v sudo >/dev/null 2>&1; then
        root_crontab="$(sudo -n crontab -l -u root 2>/dev/null || true)"
    fi
    active_crontab="$(printf '%s\n%s\n' "$user_crontab" "$root_crontab" | grep -v '^[[:space:]]*#' || true)"
    while IFS= read -r line; do
        if [[ "$line" == *"cd ${PROJECT_DIR} && ./scripts/ops/backup.sh"* ]]; then
            [[ -f "${PROJECT_DIR}/scripts/ops/backup.sh" && -x "${PROJECT_DIR}/scripts/ops/backup.sh" ]] && return 0
            saw_missing=1
            continue
        fi
        if [[ "$line" =~ (^|[[:space:]])(/[^[:space:]]*/scripts/ops/backup\.sh)([[:space:]]|$) ]]; then
            script_path="${BASH_REMATCH[2]}"
            if [[ ! -f "$script_path" || ! -x "$script_path" ]]; then
                saw_missing=1
                continue
            fi
            [[ "$script_path" == "${PROJECT_DIR}/scripts/ops/backup.sh" ]] && return 0
            saw_foreign=1
        fi
    done <<<"$active_crontab"
    [[ "$saw_foreign" == "1" ]] && return 2
    [[ "$saw_missing" == "1" ]] && return 1
    return 3
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
  docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} exec web python manage.py create_super_admin"
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

  1. Als Super-Admin unter /login/ anmelden und die erste Einrichtung sowie die
     erste Anwendungsbetreuung (facility_admin) im Django-Admin anlegen:
     /admin-mgmt/ -> Core -> Einrichtungen bzw. Core -> Benutzer (nur
     super_admin). Der Bereich /system/ zeigt Organisation und Einrichtungen
     nur an; angelegt wird dort nichts.
     Das Speichern stattet die neue Einrichtung gleich aus: Standardkatalog der
     Dokumentationstypen, die drei Standardschichten und zwei Schnellvorlagen.
     Sie ist damit sofort dokumentierbar — zu tun bleibt nur, ihr unter
     Core -> Benutzer mindestens ein Konto zuzuordnen.
     Reiner Single-Tenant-Fallback (interaktiv, MUSS mit der Admin-DB-Rolle
     laufen — sonst verweigert die Row-Level-Security den Schreibzugriff):
       docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} run --rm maintenance setup_facility
     Der Befehl legt dabei auch den Standardkatalog der Dokumentationstypen an,
     damit sofort dokumentiert werden kann (--no-default-package schaltet ihn
     ab). Eine Einrichtung, die schon Dokumentationstypen hat, laesst er
     unangetastet — nachziehen mit: load_package --default --facility <uuid>.

  2. Zwei-Faktor-Authentifizierung unter /mfa/settings/ einrichten — der
     Bootstrap-Super-Admin ist ab Werk MFA-pflichtig.

  3. Backups einrichten und einen Restore-Test fahren:
     docs/admin-guide.md § 3, scripts/ops/backup.sh.

EOF

cron_status=0
backup_cron_status || cron_status=$?
case "$cron_status" in
    0) info "Host-Cron-Eintrag fuer backup.sh gefunden." ;;
    1) warn "Cron-Ziel fuer backup.sh nicht gefunden oder nicht ausfuehrbar — Eintrag auf ${PROJECT_DIR}/scripts/ops/backup.sh korrigieren." ;;
    2) warn "Cron-Eintrag zeigt auf fremde Installation — Eintrag auf ${PROJECT_DIR}/scripts/ops/backup.sh korrigieren." ;;
    *) warn "Kein Host-Cron-Eintrag fuer backup.sh gefunden — Backups nach docs/ops-runbook.md § 3.3 einrichten." ;;
esac

cat <<EOF

  4. Betriebszustand pruefen:
       curl -fsS https://<domain>/health/            -> {"status":"ok"}
       /system/compliance/ als Super-Admin aufrufen
EOF
