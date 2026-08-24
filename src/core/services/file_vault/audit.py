"""AuditLog-Sinks fuer File-Vault-Mutationen und Sicherheitsverletzungen.

Schmaler Wrapper um :func:`core.services.audit.audit_security_violation`,
der den ``target_type="Attachment"`` und die ``target_id`` aus dem
Owner vereinheitlicht (``None`` bei Pre-Save-Rejects ohne Owner-PK).
Die Fingerprint-API haelt den Dateinamen in allen Pfaden aus dem append-only
Log heraus. Refs #610 / #901 / #1751 / #1738 / #1960.
"""

from __future__ import annotations

from core.services.audit import audit_event, audit_security_violation, hmac_hash_value


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


def filename_fingerprint(filename: str) -> dict[str, str]:
    """Die datenminimierte Namensspur einer Datei fuers ``detail`` (Refs #1751/#1960).

    ``{"filename_hash": <HMAC>, "filename_ext": <Typmarker>}`` — genau das Paar,
    das :func:`log_attachment_violation` seit #1751 schreibt, jetzt als eigener
    Einstieg. Der physische Loeschpfad (``ATTACHMENT_DELETE``, Refs #1874)
    protokolliert denselben Vorgangstyp aus einem View heraus und darf den
    Klartextnamen genauso wenig ins append-only Log schreiben. Ohne gemeinsamen
    Helfer haette der zweite Schreibpunkt die Minimierung nachgebaut — und der
    Nachbau ist genau die Stelle, an der sie beim naechsten Mal fehlt.
    """
    return {"filename_hash": hmac_hash_value(filename), "filename_ext": _extension(filename)}


def filename_audit_fields(filename: str) -> dict[str, str]:
    """Rueckwaertskompatibler Name fuer :func:`filename_fingerprint`.

    Der Purge-Pfad aus #1874 importiert diesen Namen bereits. Upload,
    Soft-Delete und Security-Reject teilen ab #1960 die explizit benannte
    Fingerprint-API; beide Einstiege bleiben byte-identisch.
    """
    return filename_fingerprint(filename)


def _owner_reference(owner):
    """``(owner_type, owner_id)`` fuer das Detail-Schema der Anhang-Domaene.

    ADR-039 §5 vereinheitlicht das Detail auf ``owner_type``/``owner_id``
    (statt des frueheren ``event_id``). Bewusst **defensiv** statt fail-loud:
    dieser Sink laeuft ausschliesslich im Fehlerpfad eines abgewiesenen
    Uploads — ein ``AttributeError`` beim Ermitteln der Owner-Metadaten wuerde
    die forensische Spur des eigentlichen Verstosses zerstoeren. Fehlt der
    Owner (Pre-Save-Reject) oder laesst er sich nicht bestimmen, bleiben beide
    Werte ``None``; der Schluessel selbst ist immer vorhanden, damit
    Auswertungen ein stabiles Schema sehen.
    """
    if owner is None:
        return None, None
    meta = getattr(owner, "_meta", None)
    owner_type = getattr(meta, "model_name", None) if meta is not None else None
    pk = getattr(owner, "pk", None)
    return owner_type, (str(pk) if pk else None)


def audit_attachment_upload(owner, field_template, attachment, staged, user, *, mode: str):
    """Auditiere einen erfolgreichen Add-/Replace-Write am Vault-Choke-Point."""
    from core.models import AuditLog

    owner_type, owner_id = _owner_reference(owner)
    return audit_event(
        AuditLog.Action.ATTACHMENT_UPLOAD,
        user=user,
        facility=owner.facility,
        target_type="Attachment",
        target_id=str(owner.pk),
        detail={
            "owner_type": owner_type,
            "owner_id": owner_id,
            "field": field_template.slug,
            "attachment_id": str(attachment.pk),
            "entry_id": str(attachment.entry_id),
            "mode": mode,
            **filename_fingerprint(staged.original_name),
            "file_size": staged.file_size,
            "mime": staged.detected_mime or staged.content_type or "application/octet-stream",
        },
    )


def audit_attachment_delete(owner, user, *, entry_id, field: str, versions: int):
    """Auditiere den erfolgreichen Soft-Delete genau einer Versionskette."""
    from core.models import AuditLog

    owner_type, owner_id = _owner_reference(owner)
    return audit_event(
        AuditLog.Action.ATTACHMENT_DELETE,
        user=user,
        facility=owner.facility,
        target_type="Attachment",
        target_id=str(owner.pk),
        detail={
            "owner_type": owner_type,
            "owner_id": owner_id,
            "entry_id": str(entry_id),
            "field": field,
            "versions": versions,
        },
    )


def audit_attachment_bulk_delete(owner, user, *, chains: int, reason: str):
    """Auditiere einen fachlichen Massenpfad als genau eine Sammelzeile."""
    from core.models import AuditLog

    return audit_event(
        AuditLog.Action.ATTACHMENT_DELETE,
        user=user,
        facility=owner.facility,
        target_type="Attachment",
        target_id=str(owner.pk),
        detail={"chains": chains, "bulk": True, "reason": reason},
    )


def log_attachment_violation(facility, user, owner, *, reason, filename, **extra):
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

    Refs #1738 (ADR-039 §5): ``owner`` ist ein ``Event``, ``Client`` oder
    ``Case`` (oder ``None`` beim Pre-Save-Reject). ``target_type`` bleibt
    ``"Attachment"``, ``target_id`` bleibt die Owner-PK; das Detail traegt
    zusaetzlich ``owner_type``/``owner_id`` — ohne sie waere aus einem
    ``SECURITY_VIOLATION`` nicht mehr ableitbar, an welcher Entitaet der
    Upload versucht wurde.
    """
    target_id = owner.pk if getattr(owner, "pk", None) else None
    owner_type, owner_id = _owner_reference(owner)
    return audit_security_violation(
        facility,
        user,
        target_type="Attachment",
        target_id=target_id,
        reason=reason,
        **filename_fingerprint(filename),
        owner_type=owner_type,
        owner_id=owner_id,
        **extra,
    )
