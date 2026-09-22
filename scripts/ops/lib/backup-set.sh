#!/usr/bin/env bash
# Gemeinsame Satz-/Manifestpruefung fuer backup.sh --verify und restore.sh
# (Refs #2290, #2292).
#
# Ein Backup-Satz besteht aus dem verschluesselten DB-Dump, dem signierten
# Manifest und den darin gebundenen Artefakten (Medien-Archiv, Audit-HWM).
# Verify und Restore muessen exakt dieselbe Bindung pruefen — sonst bestaetigt
# ``--verify`` einen Satz, den der Restore anschliessend ablehnt (oder, schlimmer,
# einen zusammengewuerfelten Satz aus DB von heute und Medien von gestern).
#
# Erwartet, dass ``_backup_common.sh`` bereits geladen ist (backup_verify_hmac).

declare -A BACKUP_SET_COUNTS=()
BACKUP_SET_TIMESTAMP=""
BACKUP_SET_DB_SHA256=""
BACKUP_SET_MEDIA_SHA256=""
BACKUP_SET_HWM_SHA256=""

# Pflichttabellen des Zeilenzahl-Manifests (identisch zu backup.sh).
BACKUP_SET_TABLES=(core_facility core_client core_event core_workitem core_auditlog core_attachment)

# backup_set_manifest_path <db-dump> — Manifestpfad des Satzes.
backup_set_manifest_path() {
    printf '%s.manifest\n' "${1%.sql.gz.enc}"
}

# backup_set_media_path <db-dump> — Medien-Archiv des Satzes (Namenskonvention).
backup_set_media_path() {
    printf '%s_media.tar.gz.enc\n' "${1%.sql.gz.enc}"
}

# backup_set_timestamp_from_db_file <db-dump> — Satzkennung aus dem Dateinamen.
backup_set_timestamp_from_db_file() {
    local name="${1##*/}"
    name="${name#anlaufstelle_}"
    printf '%s\n' "${name%.sql.gz.enc}"
}

# backup_set_load <manifest> — Manifest authentifizieren und einlesen.
# Ein fehlendes, leeres oder unvollstaendiges Manifest ist ein Fehler: ohne
# signierte Satzbindung ist der Satz nicht pruefbar.
backup_set_load() {
    local manifest="$1" key value extra tbl
    BACKUP_SET_COUNTS=()
    BACKUP_SET_TIMESTAMP=""
    BACKUP_SET_DB_SHA256=""
    BACKUP_SET_MEDIA_SHA256=""
    BACKUP_SET_HWM_SHA256=""

    if [[ ! -f "$manifest" ]]; then
        echo "FEHLER: Pflicht-Manifest des Backup-Satzes fehlt: ${manifest}" >&2
        return 1
    fi
    if [[ ! -s "$manifest" ]]; then
        echo "FEHLER: Backup-Manifest ist leer: ${manifest}" >&2
        return 1
    fi
    backup_verify_hmac "$manifest" || return 1

    while IFS='=' read -r key value extra; do
        if [[ -n "$extra" || -z "$key" ]]; then
            echo "FEHLER: Backup-Manifest ungueltig: ${manifest}" >&2
            return 1
        fi
        case "$key" in
            table_core_*)
                if [[ ! "$value" =~ ^[0-9]+$ ]]; then
                    echo "FEHLER: Backup-Manifest ungueltig: ${manifest}" >&2
                    return 1
                fi
                BACKUP_SET_COUNTS["${key#table_}"]="$value"
                ;;
            backup_timestamp) BACKUP_SET_TIMESTAMP="$value" ;;
            db_sha256) BACKUP_SET_DB_SHA256="$value" ;;
            media_sha256) BACKUP_SET_MEDIA_SHA256="$value" ;;
            hwm_sha256) BACKUP_SET_HWM_SHA256="$value" ;;
            *)
                echo "FEHLER: Unbekannter Manifest-Eintrag '${key}': ${manifest}" >&2
                return 1
                ;;
        esac
    done < "$manifest"

    for tbl in "${BACKUP_SET_TABLES[@]}"; do
        if [[ ! -v "BACKUP_SET_COUNTS[$tbl]" ]]; then
            echo "FEHLER: Backup-Manifest enthaelt ${tbl} nicht: ${manifest}" >&2
            return 1
        fi
    done
    if [[ -z "$BACKUP_SET_TIMESTAMP" ]]; then
        echo "FEHLER: Backup-Manifest ohne Satzkennung: ${manifest}" >&2
        return 1
    fi
    if [[ ! "$BACKUP_SET_DB_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
        echo "FEHLER: Backup-Manifest ohne gueltigen Dump-Hash: ${manifest}" >&2
        return 1
    fi
}

# backup_set_verify_artifacts <db-dump> [<medien-archiv>] [<hwm-datei>]
# Prueft die Bindung der Artefakte an das zuvor geladene Manifest: Satzkennung,
# Dump-Hash, Medien-Archiv samt Sidecar und Hash sowie — sofern gebunden — den
# Audit-Anker. Kein Fallback auf ein „juengstes" Artefakt.
backup_set_verify_artifacts() {
    local db_file="$1" media_file="${2:-}" hwm_file="${3:-}" file_timestamp

    file_timestamp="$(backup_set_timestamp_from_db_file "$db_file")"
    if [[ "$BACKUP_SET_TIMESTAMP" != "$file_timestamp" ]]; then
        echo "FEHLER: Manifest-Satzkennung passt nicht zum Dump (${BACKUP_SET_TIMESTAMP} != ${file_timestamp})." >&2
        return 1
    fi
    if [[ "$(sha256sum "$db_file" | awk '{print $1}')" != "$BACKUP_SET_DB_SHA256" ]]; then
        echo "FEHLER: Manifest-Dump-Hash passt nicht zum gewaehlten Satz: ${db_file}" >&2
        return 1
    fi

    if [[ -n "$BACKUP_SET_MEDIA_SHA256" ]]; then
        if [[ ! "$BACKUP_SET_MEDIA_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
            echo "FEHLER: Manifest-Medien-Hash ist ungueltig." >&2
            return 1
        fi
        if [[ -z "$media_file" || ! -f "$media_file" ]]; then
            echo "FEHLER: Satz unvollstaendig: Medien-Archiv fehlt." >&2
            return 1
        fi
        if [[ ! -f "${media_file}.hmac" ]]; then
            echo "FEHLER: Satz unvollstaendig: Medien-Sidecar fehlt: ${media_file}.hmac" >&2
            return 1
        fi
        if [[ "$(sha256sum "$media_file" | awk '{print $1}')" != "$BACKUP_SET_MEDIA_SHA256" ]]; then
            echo "FEHLER: Manifest-Medien-Hash passt nicht zum gewaehlten Satz: ${media_file}" >&2
            return 1
        fi
    fi

    # Der Audit-Anker wird nur geprueft, wenn der Aufrufer einen Pfad nennt:
    # ein Zielhost ohne konfigurierten Anker (DJANGO_AUDIT_HWM_PATH leer) bringt
    # keinen mit, und der Restore entscheidet darueber selbst.
    if [[ -n "$BACKUP_SET_HWM_SHA256" && -n "$hwm_file" ]]; then
        if [[ ! "$BACKUP_SET_HWM_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
            echo "FEHLER: Manifest-HWM-Hash ist ungueltig." >&2
            return 1
        fi
        if [[ ! -s "$hwm_file" ]]; then
            echo "FEHLER: Satz unvollstaendig: Audit-Anker des Satzes fehlt." >&2
            return 1
        fi
        if [[ "$(sha256sum "$hwm_file" | awk '{print $1}')" != "$BACKUP_SET_HWM_SHA256" ]]; then
            echo "FEHLER: Manifest-HWM-Hash passt nicht zum gewaehlten Satz: ${hwm_file}" >&2
            return 1
        fi
    fi
}
