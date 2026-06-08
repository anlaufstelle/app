#!/usr/bin/env bash
# Gemeinsame Krypto-Helfer für backup.sh / restore.sh (A4.3, Refs #1024).
#
# Encrypt-then-MAC: AES-256-CBC (openssl enc) bietet keine Authentizität — ein
# Angreifer mit Schreibzugriff auf den Backup-Storage könnte ein Backup
# manipulieren (Bit-Flips, Austausch), ohne dass der Restore es merkt. Ein
# detached HMAC-SHA256 über die fertige ``.enc``-Datei erlaubt restore.sh, die
# Integrität VOR dem Entschlüsseln zu prüfen.
#
# Der HMAC-Key wird aus ``BACKUP_ENCRYPTION_KEY`` abgeleitet (Domain-Separation
# enc/mac), damit Verschlüsselung und Authentifizierung nicht denselben Schlüssel
# teilen. Hinweis: der abgeleitete Key erscheint kurz in der openssl-Argumentliste
# (ps-sichtbar) — auf einem dedizierten, single-tenant Backup-Host ein
# akzeptabler Trade-off; der primäre BACKUP_ENCRYPTION_KEY bleibt in der Env.

# Abgeleiteter HMAC-Key (Hex) aus BACKUP_ENCRYPTION_KEY + Domain-Tag.
_backup_hmac_key() {
    printf '%s' "anlaufstelle-backup-hmac-v1:${BACKUP_ENCRYPTION_KEY}" \
        | openssl dgst -sha256 -hex | awk '{print $NF}'
}

# backup_write_hmac <file> — schreibt HMAC-SHA256 von <file> nach <file>.hmac.
backup_write_hmac() {
    local file="$1" key
    key="$(_backup_hmac_key)"
    openssl dgst -sha256 -hmac "$key" "$file" | awk '{print $NF}' > "${file}.hmac"
}

# backup_verify_hmac <file> — prüft <file> gegen <file>.hmac. Return 1 bei
# fehlender Sidecar-Datei oder Mismatch (Manipulation/Beschädigung).
backup_verify_hmac() {
    local file="$1" key expected actual
    if [[ ! -f "${file}.hmac" ]]; then
        echo "FEHLER: HMAC-Sidecar fehlt (${file}.hmac) — Integrität nicht prüfbar. Abbruch." >&2
        return 1
    fi
    key="$(_backup_hmac_key)"
    expected="$(cat "${file}.hmac")"
    actual="$(openssl dgst -sha256 -hmac "$key" "$file" | awk '{print $NF}')"
    if [[ "$expected" != "$actual" ]]; then
        echo "FEHLER: HMAC-Verifikation fehlgeschlagen — ${file} ist manipuliert oder beschädigt. Abbruch." >&2
        return 1
    fi
}
