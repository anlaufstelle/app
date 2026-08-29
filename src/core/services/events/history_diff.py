"""Diff- und Maskierungs-Logik fuer EventHistory-Eintraege (Refs #1162).

Sicherheitskritischer Service-Layer: berechnet aus einem ``EventHistory``-
Eintrag die anzuzeigenden Diff-Felder, entschluesselt sie fuer berechtigte
Rollen und maskiert fuer die Rolle nicht sichtbare (``restricted``) Werte.

Vorrang der Darstellung (Refs #1961):

    restricted  >  Datei-Marker  >  berechtigt ? Klartext : encrypted
                >  Roh-/Formatierungswert

``restricted`` greift ausschliesslich, wenn ein ``user`` uebergeben wird und
:func:`core.services.compliance.user_can_see_field` False liefert (die
effektive Feld-Sensitivity uebersteigt das Rollen-Maximum). Ohne ``user`` ist
nie etwas restricted, verschluesselte Werte bleiben dann aber fail-closed
maskiert.

Der Templatetag ``core.templatetags.history_tags.compute_diff`` ist nur noch
ein duenner Wrapper um :func:`compute_event_diff`.
"""

from core.services.compliance import user_can_see_field
from core.services.dynamic_data import is_file_marker
from core.services.file_vault.encryption import safe_decrypt

ATTACHMENT_PLACEHOLDER = "[Anhang]"
ENCRYPTED_PLACEHOLDER = "●●●●●"
RESTRICTED_PLACEHOLDER = "[Eingeschränkt]"
# ADR-050 D6 (Refs #2065): Anzeigewert einer altersredigierten Fassung. Bewusst
# nicht „– (gelöscht)" wie beim Loeschpfad: die Fassung ist nicht geloescht, ihr
# INHALT ist es — das Ereignis kann weiterleben.
REDACTED_VALUE_TEXT = "– (redigiert)"


def _build_slug_info_from_metadata(field_metadata):
    """Convert stored field_metadata to slug_info format."""
    return {
        slug: {
            "name": meta["name"],
            "is_encrypted": meta.get("is_encrypted", False),
            "sensitivity": meta.get("sensitivity", ""),
        }
        for slug, meta in field_metadata.items()
    }


def _build_slug_info(event):
    """Build a dict mapping slugs to {name, is_encrypted, sensitivity} for an event's document type."""
    slug_info = {}
    for dtf in event.document_type.fields.select_related("field_template"):
        ft = dtf.field_template
        slug_info[ft.slug] = {
            "name": ft.name,
            "is_encrypted": ft.is_encrypted,
            "sensitivity": ft.sensitivity,
        }
    return slug_info


def _display_value(value, is_encrypted, *, restricted=False, may_decrypt=False):
    """Return a display value using the security precedence from #1961."""
    if restricted:
        return RESTRICTED_PLACEHOLDER
    if is_file_marker(value):
        return ATTACHMENT_PLACEHOLDER
    if is_encrypted:
        if not may_decrypt:
            return ENCRYPTED_PLACEHOLDER
        value = safe_decrypt(value, default=ENCRYPTED_PLACEHOLDER)
    if value is None:
        return "–"
    if isinstance(value, bool):
        return "Ja" if value else "Nein"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _resolve_label(slug, slug_info):
    """Resolve a slug to a human-readable label."""
    info = slug_info.get(slug)
    if info:
        return info["name"]
    return slug.replace("-", " ").title()


def _is_encrypted(slug, slug_info):
    """Check whether a slug corresponds to an encrypted field."""
    info = slug_info.get(slug)
    return info["is_encrypted"] if info else False


def _is_restricted(slug, slug_info, user, doc_type_sensitivity):
    """Check whether a slug is restricted for the given user based on sensitivity."""
    if user is None:
        return False
    info = slug_info.get(slug)
    field_sensitivity = info["sensitivity"] if info else ""
    return not user_can_see_field(user, doc_type_sensitivity, field_sensitivity)


def _resolve_slug_info(entry):
    """Prefer frozen metadata snapshot; fall back to live FieldTemplates for legacy entries.

    Refs #824 (C-57): ``build_event_detail_context`` haengt einmalig
    ``_slug_info`` an alle Entries — der Aufrufer spart so pro Entry einen
    Field-Query.
    """
    if entry.field_metadata:
        return _build_slug_info_from_metadata(entry.field_metadata)
    if getattr(entry, "_slug_info", None):
        return entry._slug_info
    return _build_slug_info(entry.event)


def _field_masking(slug, slug_info, user, doc_type_sensitivity):
    """Return ``(encrypted, restricted, may_decrypt)`` for a single slug."""
    encrypted = _is_encrypted(slug, slug_info)
    restricted = _is_restricted(slug, slug_info, user, doc_type_sensitivity)
    may_decrypt = user is not None and not restricted
    return encrypted, restricted, may_decrypt


#: Das Flag, an dem JEDE Redaktion erkennbar ist. Produzenten schreiben es
#: ausschliesslich ueber die beiden Fabriken unten — der Architektur-Guard
#: ``test_marker_forms_have_exactly_one_producer`` pinnt das.
REDACTION_FLAG = "_redacted"


def build_field_redaction_marker(slugs):
    """Der Marker fuer eine Spalte, deren Werte gefallen sind (ADR-050 D1.2).

    ``{"_redacted": True, "fields": [...]}`` — die Feldnamen bleiben, damit
    nachvollziehbar ist, *was* es gab, nicht *welche Werte*. Geschrieben vom
    Loeschpfad (Refs #714) und vom Alters-Sweep.

    Sortiert, damit derselbe Inhalt immer dieselbe Zeile ergibt; die Auskunft
    sortiert beim Lesen ohnehin (``_serialize_history_payload``).
    """
    return {REDACTION_FLAG: True, "fields": sorted(slugs)}


def build_anonymization_marker():
    """Der Marker der Art.-17-/K-Anon-Kaskade.

    ``{"_redacted": True, "anonymized": True}`` — hier fallen die Werte
    **ohne** Feldnamen, weil die ganze Person getilgt wird. ``anonymized``
    unterscheidet ihn vom Loeschmarker oben und ist deshalb das Merkmal, an dem
    die Verspaetungspruefung eine Art.-17-Redaktion auch auf einer DELETE-Zeile
    erkennt (``core.retention.legal_holds``) — aber nur zusammen mit dem Flag:
    ``anonymized`` ist kein reservierter Feld-Slug.
    """
    return {REDACTION_FLAG: True, "anonymized": True}


def is_redaction_marker(payload):
    """Traegt *payload* den Redaktions-Marker statt echter Werte? (ADR-050 D1.2)

    Vier Schreiber setzen ihn: der Loeschpfad
    (:func:`core.services.events.fields.build_redacted_delete_history`), die
    Art.-17-Kaskade (``_redact_event_history``) und seit ADR-050 der
    Alters-Sweep — jeweils in der Form
    ``{"_redacted": true, "fields": [<Slugs genau dieser Spalte>]}``. Die
    Art.-17-Variante fuehrt statt ``fields`` ein ``anonymized``-Merkmal und
    nennt also gar keine Slugs; beide Formen werden hier gleich erkannt.
    """
    return isinstance(payload, dict) and bool(payload.get(REDACTION_FLAG))


def _marker_slugs(payload):
    """Slug-Liste einer redigierten Spalte — je Spalte die eigene (D1.2)."""
    return list(payload.get("fields", []))


def _redacted_display_value(encrypted, restricted, may_decrypt, text):
    """Anzeigewert eines Feldes, dessen Wert nicht mehr gespeichert ist.

    Die Sensitivitaets-Maskierung geht der Redaktions-Meldung bewusst VOR: wer
    das Feld ohnehin nicht sehen duerfte, erfaehrt ueber seinen Zustand auch
    nichts (Vorrang der Darstellung, Refs #1961).
    """
    if restricted:
        return RESTRICTED_PLACEHOLDER
    if encrypted and not may_decrypt:
        return ENCRYPTED_PLACEHOLDER
    return text


def _diff_create(data_after, slug_info, user, doc_type_sensitivity):
    """Build diff entries for a CREATE action: [{label, value}].

    ADR-050 D6: Der ``_redacted``-Zweig fehlte hier, weil bisher nur
    ``_diff_delete`` je auf eine redigierte Zeile traf. Der Alters-Sweep
    redigiert auch die CREATE-Zeilen **lebender** Ereignisse — ohne diesen
    Zweig stuenden ``_Redacted`` und ``Fields`` als Feld-Labels auf der
    Detailseite.
    """
    if is_redaction_marker(data_after):
        return [
            {
                "label": _resolve_label(slug, slug_info),
                "value": _redacted_display_value(
                    *_field_masking(slug, slug_info, user, doc_type_sensitivity),
                    REDACTED_VALUE_TEXT,
                ),
            }
            for slug in _marker_slugs(data_after)
        ]
    fields = []
    for slug, value in data_after.items():
        encrypted, restricted, may_decrypt = _field_masking(slug, slug_info, user, doc_type_sensitivity)
        fields.append(
            {
                "label": _resolve_label(slug, slug_info),
                "value": _display_value(
                    value,
                    encrypted,
                    restricted=restricted,
                    may_decrypt=may_decrypt,
                ),
            }
        )
    return fields


def _comparison_value(value, encrypted, may_decrypt):
    """Normalize permitted encrypted values before update comparison.

    Fernet emits a fresh token on every save. Comparing ciphertext would make
    an unchanged field look modified. Restricted and no-user paths deliberately
    retain the raw comparison and therefore never decrypt as a side effect.
    """
    if encrypted and may_decrypt and not is_file_marker(value):
        return safe_decrypt(value, default=ENCRYPTED_PLACEHOLDER)
    return value


def _diff_update_redacted(data_before, data_after, slug_info, user, doc_type_sensitivity):
    """UPDATE-Diff, wenn mindestens eine der beiden Spalten redigiert ist (D6).

    Beide Spalten werden **einzeln** betrachtet: D1.2 gibt jeder ihre eigene
    Slug-Liste, und D1.1 laesst ``NULL`` ``NULL`` — ein gemeinsamer Marker
    verlore die Unterscheidung, welche Felder vorher und welche nachher
    existierten. Es wird hier **nicht** auf Gleichheit gefiltert wie im
    Normalfall: zwei redigierte Werte sind nicht vergleichbar, und ein
    stillschweigendes Weglassen behauptete „unveraendert", wo nur nichts mehr
    gespeichert ist.
    """
    before_redacted = is_redaction_marker(data_before)
    after_redacted = is_redaction_marker(data_after)
    before_slugs = _marker_slugs(data_before) if before_redacted else list(data_before.keys())
    after_slugs = _marker_slugs(data_after) if after_redacted else list(data_after.keys())

    fields = []
    for slug in dict.fromkeys(before_slugs + after_slugs):
        encrypted, restricted, may_decrypt = _field_masking(slug, slug_info, user, doc_type_sensitivity)

        def _side(value, redacted, *, encrypted=encrypted, restricted=restricted, may_decrypt=may_decrypt):
            if redacted:
                return _redacted_display_value(encrypted, restricted, may_decrypt, REDACTED_VALUE_TEXT)
            return _display_value(value, encrypted, restricted=restricted, may_decrypt=may_decrypt)

        fields.append(
            {
                "label": _resolve_label(slug, slug_info),
                "old_value": _side(data_before.get(slug) if not before_redacted else None, before_redacted),
                "new_value": _side(data_after.get(slug) if not after_redacted else None, after_redacted),
                "changed": True,
            }
        )
    return fields


def _diff_update(data_before, data_after, slug_info, user, doc_type_sensitivity):
    """Build diff entries for an UPDATE action: [{label, old_value, new_value, changed}]."""
    if is_redaction_marker(data_before) or is_redaction_marker(data_after):
        return _diff_update_redacted(data_before, data_after, slug_info, user, doc_type_sensitivity)
    fields = []
    all_keys = list(dict.fromkeys(list(data_before.keys()) + list(data_after.keys())))
    for slug in all_keys:
        old_val = data_before.get(slug)
        new_val = data_after.get(slug)
        encrypted, restricted, may_decrypt = _field_masking(slug, slug_info, user, doc_type_sensitivity)
        old_comparison = _comparison_value(old_val, encrypted, may_decrypt)
        new_comparison = _comparison_value(new_val, encrypted, may_decrypt)
        if old_comparison == new_comparison:
            continue
        old_was_decrypted = encrypted and may_decrypt and not is_file_marker(old_val)
        new_was_decrypted = encrypted and may_decrypt and not is_file_marker(new_val)
        fields.append(
            {
                "label": _resolve_label(slug, slug_info),
                "old_value": _display_value(
                    old_comparison if old_was_decrypted else old_val,
                    encrypted and not old_was_decrypted,
                    restricted=restricted,
                    may_decrypt=may_decrypt,
                ),
                "new_value": _display_value(
                    new_comparison if new_was_decrypted else new_val,
                    encrypted and not new_was_decrypted,
                    restricted=restricted,
                    may_decrypt=may_decrypt,
                ),
                "changed": True,
            }
        )
    return fields


def _redacted_delete_value(encrypted, restricted, may_decrypt):
    """Display value for a single field of a redacted soft-delete (no original values stored)."""
    return _redacted_display_value(encrypted, restricted, may_decrypt, "– (gelöscht)")


def _diff_delete(data_before, slug_info, user, doc_type_sensitivity):
    """Build diff entries for a DELETE action: [{label, value}].

    Redacted soft-deletes store only field names (no values); legacy/full
    deletes still carry the original values.
    """
    fields = []
    if is_redaction_marker(data_before):
        for slug in _marker_slugs(data_before):
            encrypted, restricted, may_decrypt = _field_masking(slug, slug_info, user, doc_type_sensitivity)
            fields.append(
                {
                    "label": _resolve_label(slug, slug_info),
                    "value": _redacted_delete_value(encrypted, restricted, may_decrypt),
                }
            )
    else:
        for slug, value in data_before.items():
            encrypted, restricted, may_decrypt = _field_masking(slug, slug_info, user, doc_type_sensitivity)
            fields.append(
                {
                    "label": _resolve_label(slug, slug_info),
                    "value": _display_value(
                        value,
                        encrypted,
                        restricted=restricted,
                        may_decrypt=may_decrypt,
                    ),
                }
            )
    return fields


def compute_event_diff(entry, user=None):
    """Compute diff information for an EventHistory entry.

    Args:
        entry: EventHistory instance.
        user: Optional User instance. When set, fields the user may not see
              based on sensitivity are masked with ``[Eingeschränkt]``.

    Returns a dict with:
      - action: 'create' | 'update' | 'delete'
      - fields: list of dicts with keys depending on action
        CREATE:  [{label, value}]
        UPDATE:  [{label, old_value, new_value, changed}]
        DELETE:  [{label, value}]
    """
    slug_info = _resolve_slug_info(entry)
    doc_type_sensitivity = entry.event.document_type.sensitivity
    action = entry.action
    data_before = entry.data_before or {}
    data_after = entry.data_after or {}

    if action == "create":
        fields = _diff_create(data_after, slug_info, user, doc_type_sensitivity)
    elif action == "update":
        fields = _diff_update(data_before, data_after, slug_info, user, doc_type_sensitivity)
    elif action == "delete":
        fields = _diff_delete(data_before, slug_info, user, doc_type_sensitivity)
    else:
        fields = []

    return {
        "action": action,
        "fields": fields,
    }
