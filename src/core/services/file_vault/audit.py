"""AuditLog-Sink fuer File-Vault-Sicherheitsverletzungen.

Schmaler Wrapper um :func:`core.services.audit.audit_security_violation`,
der den ``target_type="EventAttachment"`` und die ``target_id`` aus dem
Event vereinheitlicht (``None`` bei Pre-Save-Rejects ohne Event-PK).
Detail-Payload bleibt schema-stabil: ``reason``, ``filename_hash``,
``filename_ext`` plus optionale Extras (signature/extension/declared/
detected/error).
Refs #610 / #901 / #1751.
"""

from __future__ import annotations

from core.services.audit import audit_security_violation, hmac_hash_value


def _extension(filename: str) -> str:
    """Kleingeschriebene Dateiendung als Typmarker, sonst ``""``.

    Refs #1755 (Review): Ohne Begrenzung landete bei Dateinamen ohne echte
    Endung der komplette Namensrest nach dem letzten Punkt im append-only
    ``SECURITY_VIOLATION``-``detail`` — genau die Klienten-PII, die die
    Hash-Minimierung (Refs #1751 P5) tilgen soll (``"Attest Dr.Meier"`` →
    ``"meier"``, ``".Bericht-Max-Mustermann"`` → der ganze Name). Ein echter
    Typmarker ist kurz und rein alphanumerisch; alles darueber (lange oder
    zeichenhaltige Reste) ist eher ein Namensfragment und wird verworfen. Die
    Laengenschranke (<= 4) deckt alle ``DEFAULT_ALLOWED_FILE_TYPES``
    (pdf/jpg/jpeg/png/docx/odt) ab; sie ist bewusst enger als der zunaechst
    vorgeschlagene Richtwert (<= 8), damit auch das kurze Beispiel ``"meier"``
    (5 Zeichen) nicht mehr durchrutscht.
    """
    name = filename or ""
    if "." not in name:
        return ""
    ext = name.rsplit(".", 1)[-1]
    if ext.isalnum() and len(ext) <= 4:
        return ext.lower()
    return ""


def log_attachment_violation(facility, user, event, *, reason, filename, **extra):
    """Schreibe einen ``SECURITY_VIOLATION``-AuditLog fuer einen File-Vault-Reject.

    Refs #1751 (Punkt 5): der **Original-Dateiname wird nicht mehr im
    Klartext** protokolliert. Hochgeladene Dateien heissen im Feld regelmaessig
    nach der betroffenen Person („Bericht-Max-Mustermann.pdf"); das AuditLog
    ist append-only (nachtraegliche Redaktion unmoeglich) und
    ``SECURITY_VIOLATION`` steht zusaetzlich in
    :data:`core.retention.audit_pruning.PRUNE_EXEMPT_ACTIONS` — dieser Name
    ueberlebte damit jede Aufbewahrungsfrist **und** jede Anonymisierung.

    Was die Forensik braucht, bleibt erhalten:

    * ``filename_hash`` — stabiler HMAC; Wiederholungsversuche derselben
      Datei bleiben korrelierbar, der Klartext ist nicht rekonstruierbar.
    * ``filename_ext`` — die Endung als Typmarker (der eigentliche
      Tatbestand bei ``extension_not_allowed``), ohne Personenbezug.

    Der Parameter heisst bewusst weiterhin ``filename``: die Aufrufer in
    ``policy.py`` uebergeben den echten Namen, die Minimierung passiert
    zentral hier — so kann keine Aufrufstelle sie versehentlich umgehen.
    """
    target_id = event.pk if getattr(event, "pk", None) else None
    return audit_security_violation(
        facility,
        user,
        target_type="EventAttachment",
        target_id=target_id,
        reason=reason,
        filename_hash=hmac_hash_value(filename),
        filename_ext=_extension(filename),
        **extra,
    )
