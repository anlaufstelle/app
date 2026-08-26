#!/bin/sh
# ============================================================================
# Anlaufstelle — One-Command-Bootstrap fuer das LOKALE Setup (Refs #1062)
#
# Wird ausschliesslich vom lokalen ``docker-compose.yml`` benutzt: der
# ``init``-Dienst ruft ``bootstrap`` auf, der ``web``-Dienst ``print-key``.
# Produktion, Staging, dev.anlaufstelle.app und die Demo fassen dieses Skript
# NICHT an — dort bleibt der getrennte Migrationspfad
# (``docker-migrate.sh``, docs/ops-runbook.md § 1.2) und die Ersteinrichtung
# ueber ``manage.py create_super_admin`` verbindlich. Siehe ADR-049.
#
# ---------------------------------------------------------------------------
# Warum nicht ``docker-migrate.sh``?
# ---------------------------------------------------------------------------
#
# Der One-Shot-Migrate-Job traegt zwei Deploy-Gates (``check_db_roles``,
# ``normalize_db_ownership``) fuer die Drei-Rollen-Topologie der Produktion.
# Das lokale Compose hat sie bewusst nicht: sein Postgres initialisiert
# ``POSTGRES_USER`` als Bootstrap-Superuser (Refs #1982). Das Gate liefe dort
# bei JEDEM Start in seine Warnung (Exit 2) und die Ownership-Normalisierung
# ist als No-op dokumentiert — beides waere im Erst-Start-Log eines neuen
# Mitwirkenden nur Rauschen. Ausserdem liegt das Skript im Image auf
# ``/app/docker-migrate.sh`` und wird vom ``./src:/app``-Bind-Mount verdeckt
# (genau der Befund aus Refs #1977).
#
# ---------------------------------------------------------------------------
# Unterkommandos
# ---------------------------------------------------------------------------
#
#   bootstrap    Schluessel bereitstellen, Schema migrieren, Demo-Daten
#                anlegen -- Letzteres nur, wenn die Datenbank noch kein
#                Benutzerkonto hat (``seed --only-if-empty``).
#   ensure-key   Nur die Schluesseldatei anlegen, falls sie fehlt. Der
#                Schluessel wird bewusst NICHT ausgegeben und auch nicht in die
#                Umgebung exportiert: gelesen wird er von ``settings/dev.py``
#                ueber ``LOCAL_ENCRYPTION_KEY_FILE``. Nur so sehen auch
#                ``docker compose exec``/``run``-Prozesse denselben Schluessel
#                -- sie erben die Container-Konfiguration, nicht die Umgebung
#                von PID 1 (Refs #1062).
#
# Env-Variablen (alle optional):
#
#   ENCRYPTION_KEY            Ist er gesetzt, gewinnt er: kein Dateizugriff,
#                             keine Erzeugung. Das ist der dokumentierte Weg,
#                             einen eigenen Schluessel mitzubringen.
#   LOCAL_ENCRYPTION_KEY_FILE Ablage des erzeugten Schluessels
#                             (Default: /home/appuser/encryption.key -- im
#                             lokalen Compose ein benanntes Volume, damit der
#                             Schluessel Neustarts ueberlebt und bereits
#                             verschluesselte Entwicklungsdaten lesbar
#                             bleiben). Dieselbe Variable liest
#                             ``settings/dev.py``.
#   SEED_SCALE                Datenmenge des Erst-Seeds (Default: small).
# ============================================================================
set -eu

KEY_FILE="${LOCAL_ENCRYPTION_KEY_FILE:-/home/appuser/encryption.key}"
SEED_SCALE="${SEED_SCALE:-small}"

# ---------------------------------------------------------------------------
# Schluessel
# ---------------------------------------------------------------------------

# Kein ausgeliefertes Fest-Geheimnis (Refs #1977): der Schluessel entsteht beim
# ersten Start zufaellig auf DIESER Maschine. Erzeugt wird ueber eine
# temporaere Datei plus ``os.link`` -- der Link scheitert, wenn die Zieldatei
# schon existiert, und genau das macht die Erzeugung gegen einen zweiten,
# gleichzeitig startenden Container unteilbar (ein Leser bekaeme sonst eine
# angelegte, aber noch leere Datei zu sehen).
ensure_key() {
    if [ -n "${ENCRYPTION_KEY:-}" ]; then
        return 0
    fi

    ANLAUFSTELLE_KEY_FILE="$KEY_FILE" python - <<'PY'
import os
import sys

from cryptography.fernet import Fernet

pfad = os.environ["ANLAUFSTELLE_KEY_FILE"]

if not os.path.exists(pfad):
    temp = f"{pfad}.{os.getpid()}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as datei:
            datei.write(Fernet.generate_key().decode())
        try:
            os.link(temp, pfad)
        except FileExistsError:
            # Ein parallel gestarteter Container war schneller — dessen
            # Schluessel gilt, unserer wird verworfen.
            pass
        else:
            sys.stderr.write(f"==> Lokalen Verschluesselungsschluessel erzeugt: {pfad}\n")
    finally:
        os.unlink(temp)

with open(pfad, encoding="utf-8") as datei:
    schluessel = datei.read().strip()

# Der Schluessel selbst bleibt in der Datei — ausgegeben wird nur, ob sie
# brauchbar ist. settings/dev.py liest sie ueber LOCAL_ENCRYPTION_KEY_FILE.
if not schluessel:
    sys.stderr.write(
        f"FEHLER: {pfad} ist leer — kein Verschluesselungsschluessel verfuegbar.\n"
        "Datei loeschen und den Stack neu starten, oder ENCRYPTION_KEY selbst setzen.\n"
    )
    raise SystemExit(1)
PY
}

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

bootstrap() {
    ensure_key

    printf '==> Schema migrieren\n' >&2
    python manage.py migrate --noinput

    # Idempotenz liegt im Command, nicht hier: ``--only-if-empty`` seedet nur,
    # wenn noch kein Benutzerkonto existiert (Refs #1062). Ein zweiter
    # ``docker compose up`` laesst den Bestand damit unberuehrt.
    printf '==> Demo-Daten anlegen, falls die Datenbank leer ist\n' >&2
    python manage.py seed --only-if-empty --scale "$SEED_SCALE"
}

case "${1:-bootstrap}" in
    bootstrap) bootstrap ;;
    ensure-key) ensure_key ;;
    *)
        printf 'Aufruf: %s [bootstrap|ensure-key]\n' "$0" >&2
        exit 64
        ;;
esac
