"""Verkettete HMAC-Integritaet fuer den AuditLog (Refs #1070).

Macht den append-only ``AuditLog`` *tamper-evident*: jede Zeile traegt einen
``entry_hash = HMAC(key, prev_hash || canonical(row))``, wobei ``prev_hash``
der ``entry_hash`` der vorherigen Zeile derselben Facility-Kette ist. Wer eine
mittlere Zeile lautlos veraendert oder entfernt — direkt in der DB, am
``save()``-/``delete()``-Guard und am Immutability-Trigger vorbei
(``session_replication_role = replica`` / Owner-Zugriff) — bricht damit den
Hash bzw. die Verkettung, ohne den geheimen ``AUDIT_HASH_KEY`` zu kennen.

Geltungsbereich der Kette ist die **Facility** (Zeilen mit ``facility=NULL``
bilden eine gemeinsame „System"-Kette). Beim Insert serialisiert ein
Postgres-Advisory-Xact-Lock pro Facility die Schreiber, sodass ``prev_hash``
luecken- und kollisionsfrei vergeben wird.

Schluessel: wiederverwendet ``_get_audit_hash_key()`` aus :mod:`.hash`
(separat von ``SECRET_KEY``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

from django.db import connection

from core.services.audit.hash import _get_audit_hash_key


def _canonical_payload(row) -> str:
    """Stabile, sortierte JSON-Serialisierung der semantischen Zeilenfelder.

    ``default=str`` serialisiert UUIDs/Datetimes; ``sort_keys`` + kompakte
    Separatoren machen die Ausgabe deterministisch und unabhaengig von der
    Dict-Einfuegereihenfolge. Identisch beim Schreiben (In-Memory-Instanz) und
    beim Verify (aus der DB geladene Zeile).
    """
    return json.dumps(
        {
            "timestamp": row.timestamp.isoformat() if row.timestamp else "",
            "action": str(row.action),
            "user_id": row.user_id,
            "facility_id": row.facility_id,
            "target_type": row.target_type,
            "target_id": row.target_id,
            # ``GenericIPAddressField`` hat ``empty_strings_allowed=False`` →
            # die DB persistiert "" als NULL. Auf NULL normalisieren, damit der
            # Write-Hash (In-Memory "") und der Verify-Hash (DB NULL) gleich sind.
            "ip_address": row.ip_address or None,
            "detail": row.detail,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def compute_entry_hash(row, prev_hash: str) -> str:
    """``HMAC_SHA256(key, prev_hash || canonical(row))`` als Hex-Digest."""
    message = ((prev_hash or "") + _canonical_payload(row)).encode("utf-8")
    return hmac.new(_get_audit_hash_key(), message, hashlib.sha256).hexdigest()


@contextmanager
def _chain_read_visibility(facility_id):
    """Macht die System-Ketten-Zeilen (``facility=NULL``) fuer den Ketten-Read
    sichtbar.

    NULL-facility-Zeilen sind unter der RLS-USING-Policy nur fuer
    ``app.is_super_admin='true'`` sichtbar (Migration 0085). Ein nicht-super
    Schreiber unter der NOBYPASSRLS-App-Rolle (z.B. LOGIN_FAILED fuer einen
    unbekannten User) saehe den Ketten-Tail sonst NICHT und wuerde
    faelschlich ``prev_hash=""`` setzen — die System-Kette risse.

    Wir heben die GUC daher transient (``SET LOCAL``, Transaktions-scoped) nur
    fuer den Predecessor-Read an und setzen sie im ``finally`` zurueck. Gelesen
    werden ausschliesslich Hash-/Ordnungs-Metadaten der eigenen Kette — keine
    PII. No-op fuer gesetzte Facility (eigener Read immer sichtbar) und auf
    Nicht-PostgreSQL.
    """
    if connection.vendor != "postgresql" or facility_id is not None:
        yield
        return
    with connection.cursor() as cur:
        cur.execute("SELECT current_setting('app.is_super_admin', true)")
        previous = cur.fetchone()[0] or ""
        cur.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        try:
            yield
        finally:
            cur.execute("SELECT set_config('app.is_super_admin', %s, true)", [previous])


def _latest_link(facility_id):
    """``(timestamp, entry_hash)`` der juengsten Zeile der Facility-Kette."""
    from core.models import AuditLog

    with _chain_read_visibility(facility_id):
        return (
            AuditLog.objects.filter(facility_id=facility_id)
            .order_by("-timestamp", "-id")
            .values_list("timestamp", "entry_hash")
            .first()
        )


def assign_chain_fields(instance) -> None:
    """Setzt ``prev_hash``/``entry_hash`` (und sichert streng monoton steigende
    Zeitstempel pro Facility) fuer eine neue ``AuditLog``-Zeile.

    MUSS innerhalb derselben ``transaction.atomic()`` laufen, die den INSERT
    ausfuehrt — so wird der Advisory-Xact-Lock ueber Read+Write gehalten und
    konkurrierende Schreiber derselben Kette serialisieren. Auf Nicht-
    PostgreSQL (SQLite-Tests) entfaellt nur der Lock; die Kette wird trotzdem
    deterministisch gebildet.
    """
    if connection.vendor == "postgresql":
        lock_key = f"audit_chain_{instance.facility_id if instance.facility_id is not None else 0}"
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [lock_key])

    latest = _latest_link(instance.facility_id)
    prev_hash = (latest[1] if latest else "") or ""

    # Strenge Monotonie pro Facility: kollidiert der Zeitstempel mit dem
    # juengsten Bestand (gleiche Mikrosekunde), um 1 us anheben. Damit ist
    # ``(timestamp, id)`` eine totale Ordnung, die der Insert-Reihenfolge
    # entspricht — Verify liest in derselben Reihenfolge.
    if latest and latest[0] and instance.timestamp and instance.timestamp <= latest[0]:
        instance.timestamp = latest[0] + timedelta(microseconds=1)

    instance.prev_hash = prev_hash
    instance.entry_hash = compute_entry_hash(instance, prev_hash)


@dataclass
class ChainResult:
    """Ergebnis von :func:`verify_chain` fuer eine Facility-Kette."""

    ok: bool
    rows_checked: int
    first_break_id: str | None = None
    reason: str = ""


def _checkpoint_boundaries(rows) -> set[str]:
    """Sammelt die legitimen Prune-Grenzen (``boundary_hash`` +
    ``boundary_hashes``) — aber NUR aus Checkpoint-Zeilen, die selbst
    AUTHENTIFIZIERT sind.

    Der Immutability-Trigger (Migration 0024) schuetzt nur UPDATE/DELETE. Ein
    DB-Angreifer ohne ``AUDIT_HASH_KEY`` koennte sonst eine mittlere Zeile
    loeschen und einen GEFAELSCHTEN Checkpoint einschieben (``entry_hash`` NULL
    oder Schrott, ``detail.boundary_hashes=[<hash der geloeschten Zeile>]``), der
    den dann danglenden ``prev_hash`` des Nachfolgers als legitime Prune-Grenze
    tarnt — und damit die Tamper-Evidenz aushebelt. Da er den Schluessel nicht
    kennt, kann er fuer den Checkpoint keinen gueltigen ``entry_hash`` berechnen.
    Wir vertrauen den Grenzen einer Checkpoint-Zeile daher nur, wenn ihr
    ``entry_hash`` vorhanden ist UND ``compute_entry_hash(cp, cp.prev_hash)`` via
    :func:`hmac.compare_digest` exakt reproduziert. Refs #1070.
    """
    from core.models import AuditLog

    boundaries: set[str] = set()
    for row in rows:
        if row.action != AuditLog.Action.AUDIT_PRUNE_CHECKPOINT:
            continue
        # Nur authentifizierte Checkpoints legitimieren Luecken — ein gefaelschter
        # (NULL/falscher entry_hash) wird ignoriert, die maskierte Loeschung faellt
        # dann beim Verkettungs-Check auf.
        if not row.entry_hash:
            continue
        expected = compute_entry_hash(row, row.prev_hash or "")
        if not hmac.compare_digest(expected, row.entry_hash):
            continue
        detail = row.detail or {}
        single = detail.get("boundary_hash")
        if single:
            boundaries.add(single)
        for bh in detail.get("boundary_hashes") or []:
            if bh:
                boundaries.add(bh)
    return boundaries


def verify_chain(facility) -> ChainResult:
    """Verifiziert die HMAC-Kette einer Facility und meldet den ersten Bruch.

    Pro ueberlebender Zeile (in ``(timestamp, id)``-Reihenfolge):

    1. **Integritaet** — ``entry_hash`` wird aus dem GESPEICHERTEN ``prev_hash``
       + Canonical neu berechnet und verglichen. Erkennt In-Place-Manipulation
       beliebiger semantischer Felder (auch wenn die Verkettung intakt bleibt).
    2. **Verkettung** — ``prev_hash`` muss auf den ``entry_hash`` der
       vorherigen ueberlebenden Zeile zeigen (Kettenstart: ``""``). Eine
       Diskontinuitaet ist nur legitim, wenn ``prev_hash`` eine von einem
       Checkpoint protokollierte Prune-Grenze ist — sonst wurde eine Zeile
       geloescht/eingeschoben.

    ``facility`` darf eine ``Facility``-Instanz, ein PK oder ``None`` (System-
    Kette) sein. Vergleich via :func:`hmac.compare_digest`.
    """
    from core.models import AuditLog, Facility

    facility_id = facility.pk if isinstance(facility, Facility) else facility
    rows = list(AuditLog.objects.filter(facility_id=facility_id).order_by("timestamp", "id"))
    boundaries = _checkpoint_boundaries(rows)

    prev_entry: str | None = None
    checked = 0
    for row in rows:
        if not row.entry_hash:
            # Noch nicht verkettete Bestandszeile (vor ``backfill_audit_chain``)
            # — ueberspringen und Kette ab der naechsten Zeile neu ansetzen.
            prev_entry = None
            continue
        checked += 1
        expected = compute_entry_hash(row, row.prev_hash or "")
        if not hmac.compare_digest(expected, row.entry_hash):
            return ChainResult(False, checked, str(row.pk), "entry_hash mismatch (in-place tampering)")

        pv = row.prev_hash or ""
        linked = pv == prev_entry if prev_entry is not None else pv == ""
        if not linked and pv not in boundaries:
            return ChainResult(False, checked, str(row.pk), "prev_hash linkage break (row deleted/inserted)")
        prev_entry = row.entry_hash

    return ChainResult(True, checked)
