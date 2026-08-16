"""Versionierter JSON-Facility-Export fuer DSGVO Art. 20 (Datenportabilitaet), M4 Strang B.

Baut ein vollstaendiges, selbststaendiges JSON-Abbild einer Einrichtung —
Konfiguration (Dokumentationstypen, Feldvorlagen, Feldgruppen, Feldbedingungen)
plus das fachliche Datenmodell (Personen, Ereignisse, Faelle, Episoden,
Wirkungsziele, Arbeitsauftraege). Schema-Version 1: additive Aenderungen
bleiben v1, ein Breaking Change braucht v2 (zentrale Doku folgt in P10,
Refs #1060).

**Entschluesselt statt Marker** (E14, Refs #1060-Plan): der Export ist selbst
der Art.-20-Datenmitnahme-Pfad — ein Fernet-Token waere keine portable
Auskunft. Dasselbe Muster wie ``core.services.client.export._serialize_event``.

**Nur aktive Datensaetze** (E14): ``is_deleted=False`` bei allen
soft-deletable Entities, ``is_current=True`` + ``deleted_at IS NULL`` bei
Attachments. Der Papierkorb ist nicht Teil des Exports — DSGVO Art. 20 zielt
auf den aktuellen Datenbestand, nicht auf geloeschte Zwischenstaende.

**Die Papierkorb-Grenze verlaeuft ueber die PERSON, nicht ueber das eigene
``is_deleted`` von Case/Event/Episode/Goal/WorkItem** (Befund 1+2,
Adversarial-Review Refs #1060): der Personen-Loeschpfad ist
der Client-Soft-Delete (``approve_client_deletion``,
``core/services/client/main.py:938``) — Case/Episode/WorkItem setzen ihr
eigenes ``is_deleted`` in Produktion nie direkt (gepinnt in
``test_cases_trash_invariant``); NUR **Event** hat eigene Loeschpfade
(``soft_delete_event``, Retention-Purge), dessen Eigen-Filter ist also
load-bearing. Ein Filter, der nur das jeweils
eigene Feld prueft, ist fuer die uebrigen ein No-Op: eine Person im Papierkorb wuerde
sonst aus ``entities.clients`` verschwinden, waehrend ihre Cases/Events/
Episoden/Ziele/WorkItems (entschluesselte ``data_json``, Anhang-Metadaten)
unter jetzt dangling ``client_id``/``case_id``-Verweisen im Export bleiben.
Siehe ``_build_entities`` fuer die konkreten Filter je Entity.

**k_anonymized-Clients bleiben drin** (Delta 14, Plan-Update 2026-08-05): ein
Ausschluss ergaebe dangling FKs (Events/Cases/WorkItems zeigen weiter auf die
Person) ohne PII-Gewinn — die Person ist bereits generalisiert, ihr Fehlen im
Export waere nur ein Loch in der Datenmitnahme.

**Attachments: nur Metadaten** (Issue-Vorgabe, E5): kein Binaerinhalt. Der
SHA-256 wird on-the-fly ueber den entschluesselten Stream berechnet
(``get_decrypted_file_stream``) — ``Attachment`` speichert selbst keinen Hash.
Fehlt die Datei auf Disk, wird ``sha256: null`` statt eines Absturzes
geliefert (Betriebs-Realitaet: Cleanup-Laeufe, manuelle Eingriffe).
"""

from __future__ import annotations

import hashlib
import logging
import struct
from typing import Any

from django.db.models import Prefetch, Q
from django.utils import timezone

from core.models import Case as CaseModel
from core.models import (
    Client,
    DocumentType,
    Episode,
    Event,
    FieldCondition,
    FieldGroup,
    FieldTemplate,
    Milestone,
    OutcomeGoal,
    WorkItem,
)
from core.models.attachment import Attachment, owner_field_name
from core.models.document_type_field import DocumentTypeField
from core.services.file_vault import (
    EncryptionError,
    EncryptionKeyMissing,
    get_decrypted_file_stream,
    is_encrypted_value,
    safe_decrypt,
)
from core.utils.formatting import isoformat_or_none as _isoformat

logger = logging.getLogger(__name__)

#: Schema-Version des Facility-Exports — additive Aenderungen bleiben v1,
#: ein Breaking Change (Feld entfernt/umbenannt/Bedeutung geaendert) braucht v2.
SCHEMA_VERSION = 1


def _uuid_str(pk: Any) -> str | None:
    """FK-PK als UUID-String, ``None`` bei fehlendem Verweis (SET_NULL-Waise)."""
    return str(pk) if pk is not None else None


def _decrypt_data_json(data_json: dict | None) -> dict:
    """``data_json`` mit entschluesselten Werten fuer alle ``is_encrypted``-Slugs.

    Datei-Marker (``__file__``/``__files__``) sind keine Verschluesselungs-
    Huellen (:func:`is_encrypted_value` prueft explizit auf ``__encrypted__``)
    und werden unveraendert durchgereicht — sie referenzieren Attachment-Zeilen,
    deren Metadaten im ``attachments``-Zweig der jeweiligen Entity liegen.
    """
    if not data_json:
        return {}
    return {key: (safe_decrypt(value) if is_encrypted_value(value) else value) for key, value in data_json.items()}


def _attachment_sha256(attachment: Attachment) -> str | None:
    """SHA-256 des entschluesselten Anhang-Inhalts, on-the-fly gestreamt.

    ``None`` bei fehlender/korrupter Datei auf Disk (Betriebs-Realitaet statt
    Gesamtexport-Absturz, Befund 4 Refs #1060) — Peak-RAM bleibt O(CHUNK), der
    Klartext wird nie komplett gepuffert. Deckt ``OSError`` (Datei fehlt,
    ``get_attachment_path``), ``EncryptionError`` (kaputter
    Format-Header/Truncation/Chunk-Bindungsfehler, ``decrypt_file_stream``)
    und ``struct.error`` ab (auf 1-4 Bytes hinter dem Versionsbyte
    abgeschnittene Datei: ``decrypt_file_stream`` liest den Chunk-Zaehler
    ungeprueft per ``struct.unpack`` — S5-Restluecke Refs #1060).

    ``EncryptionKeyMissing`` (Fehlkonfiguration: ``ENCRYPTION_KEY``/``_KEYS``
    fehlt) wird bewusst NICHT gefangen und propagiert — analog
    ``safe_decrypt``-Praezedenz (:func:`core.services.file_vault.safe_decrypt`)
    darf eine fehlende Schluesselkonfiguration nicht als harmloses
    ``sha256: null`` maskiert werden. Muss zuerst gefangen/weitergereicht
    werden, da ``EncryptionKeyMissing`` von ``EncryptionError`` erbt.
    """
    hasher = hashlib.sha256()
    try:
        for chunk in get_decrypted_file_stream(attachment):
            hasher.update(chunk)
    except EncryptionKeyMissing:
        raise
    except (OSError, EncryptionError, struct.error):
        logger.warning("Facility-Export: Anhang-Datei unlesbar/korrupt (attachment_id=%s)", attachment.pk)
        return None
    return hasher.hexdigest()


def _serialize_attachment(attachment: Attachment, *, with_file_hashes: bool) -> dict:
    """Anhang-**Metadaten** (kein Binaerinhalt, Issue-Vorgabe M4/#1060)."""
    return {
        "id": str(attachment.pk),
        "owner": attachment.owner_type,
        "original_filename": safe_decrypt(attachment.original_filename_encrypted),
        "size": attachment.file_size,
        "content_type": attachment.detected_mime or attachment.mime_type,
        "created_at": attachment.created_at.isoformat(),
        "sha256": _attachment_sha256(attachment) if with_file_hashes else None,
    }


def _gather_attachments(facility, owner, *, with_file_hashes: bool) -> list[dict]:
    """Aktuelle, nicht soft-deletete Anhaenge eines Owners (Ereignis/Person/Fall).

    ``facility`` wird zusaetzlich zum Owner-FK gefiltert (Defense-in-Depth,
    ADR-039 §2 haelt ``attachment.facility == owner.facility`` ohnehin als
    Invariante fest).
    """
    owner_field = owner_field_name(owner)
    attachments = (
        Attachment.objects.for_facility(facility)
        .filter(is_current=True, deleted_at__isnull=True, **{owner_field: owner})
        .select_related("field_template")
        .order_by("sort_order", "created_at", "pk")
    )
    return [_serialize_attachment(a, with_file_hashes=with_file_hashes) for a in attachments]


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


def _serialize_document_type(document_type: DocumentType) -> dict:
    fields = DocumentTypeField.objects.filter(document_type=document_type).order_by("sort_order", "pk")
    return {
        "id": str(document_type.pk),
        "name": document_type.name,
        "slug": document_type.slug,
        "entity_type": document_type.entity_type,
        "sensitivity": document_type.sensitivity,
        "min_contact_stage": document_type.min_contact_stage,
        "system_type": document_type.system_type,
        "tally_enabled": document_type.tally_enabled,
        # E3 (Refs #1849): der Berichtsschluessel ersetzt ``statistics_category``
        # an der Vorlage und kommt am Typ neu hinzu — er ist Teil der
        # Konfiguration und muss in einem Vollexport rekonstruierbar sein.
        "report_key": document_type.report_key,
        "retention_days": document_type.retention_days,
        "icon": document_type.icon,
        "color": document_type.color,
        "description": document_type.description,
        "is_active": document_type.is_active,
        "sort_order": document_type.sort_order,
        "fields": [
            {
                "id": str(field.pk),
                "field_template_id": str(field.field_template_id),
                "field_group_id": _uuid_str(field.field_group_id),
                "sort_order": field.sort_order,
            }
            for field in fields
        ],
    }


def _serialize_field_template(field_template: FieldTemplate) -> dict:
    return {
        "id": str(field_template.pk),
        "name": field_template.name,
        "slug": field_template.slug,
        "field_type": field_template.field_type,
        "min_value": field_template.min_value,
        "max_value": field_template.max_value,
        "is_required": field_template.is_required,
        "is_encrypted": field_template.is_encrypted,
        "sensitivity": field_template.sensitivity,
        "options_json": field_template.options_json,
        "default_value": field_template.default_value,
        "report_key": field_template.report_key,
        "help_text": field_template.help_text,
        "is_active": field_template.is_active,
    }


def _serialize_field_group(field_group: FieldGroup) -> dict:
    return {
        "id": str(field_group.pk),
        "document_type_id": str(field_group.document_type_id),
        "name": field_group.name,
        "slug": field_group.slug,
        "sort_order": field_group.sort_order,
        "is_collapsible": field_group.is_collapsible,
    }


def _serialize_field_condition(field_condition: FieldCondition) -> dict:
    return {
        "id": str(field_condition.pk),
        "target_field_id": str(field_condition.target_field_id),
        "depends_on_field_id": str(field_condition.depends_on_field_id),
        "operator": field_condition.operator,
        "value": field_condition.value,
        "group_index": field_condition.group_index,
    }


def _build_configuration(facility) -> dict:
    """Vollstaendige Konfiguration der Einrichtung — inklusive inaktiver Typen/Felder.

    ``is_active`` ist ein UI-Sichtbarkeitsschalter, keine Papierkorb-Markierung
    (DocumentType/FieldTemplate sind nicht ``SoftDeletableModel``) — die
    Konfiguration wird deshalb ungefiltert exportiert, ``is_active`` steht im
    jeweiligen Eintrag zur Information.
    """
    document_types = DocumentType.objects.for_facility(facility).order_by("sort_order", "name", "pk")
    field_templates = FieldTemplate.objects.for_facility(facility).order_by("name", "pk")
    field_groups = FieldGroup.objects.filter(document_type__facility=facility).order_by(
        "document_type_id", "sort_order", "pk"
    )
    field_conditions = FieldCondition.objects.filter(target_field__document_type__facility=facility).order_by(
        "group_index", "pk"
    )
    return {
        "document_types": [_serialize_document_type(dt) for dt in document_types],
        "field_templates": [_serialize_field_template(ft) for ft in field_templates],
        "field_groups": [_serialize_field_group(fg) for fg in field_groups],
        "field_conditions": [_serialize_field_condition(fc) for fc in field_conditions],
    }


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------


def _serialize_client(client: Client, facility, *, with_file_hashes: bool) -> dict:
    return {
        "id": str(client.pk),
        "pseudonym": client.pseudonym,
        "contact_stage": client.contact_stage,
        "age_cluster": client.age_cluster,
        "notes": client.notes,
        "document_type_id": _uuid_str(client.document_type_id),
        "data_json": _decrypt_data_json(client.data_json),
        "is_active": client.is_active,
        "k_anonymized": client.k_anonymized,
        # Refs #1060-Plan (Delta 14): einzige gespeicherte Kennung fuer eine
        # vollstaendige Art.-17-Anonymisierung ist ``is_active=False`` — siehe
        # ``core.services.client.main._redact_client_identity``. Abgeleitet,
        # kein eigenes Modellfeld.
        "is_anonymized": not client.is_active,
        "created_at": client.created_at.isoformat(),
        "updated_at": client.updated_at.isoformat(),
        "created_by_id": _uuid_str(client.created_by_id),
        "attachments": _gather_attachments(facility, client, with_file_hashes=with_file_hashes),
    }


def _serialize_event(event: Event, facility, *, with_file_hashes: bool) -> dict:
    return {
        "id": str(event.pk),
        "client_id": _uuid_str(event.client_id),
        "case_id": _uuid_str(event.case_id),
        "episode_id": _uuid_str(event.episode_id),
        "document_type_id": str(event.document_type_id),
        "occurred_at": event.occurred_at.isoformat(),
        "data_json": _decrypt_data_json(event.data_json),
        "is_anonymous": event.is_anonymous,
        "created_by_id": _uuid_str(event.created_by_id),
        "created_at": event.created_at.isoformat(),
        "updated_at": event.updated_at.isoformat(),
        "attachments": _gather_attachments(facility, event, with_file_hashes=with_file_hashes),
    }


def _serialize_case(case: CaseModel, facility, *, with_file_hashes: bool) -> dict:
    return {
        "id": str(case.pk),
        "client_id": str(case.client_id),
        "document_type_id": _uuid_str(case.document_type_id),
        "title": case.title,
        "description": case.description,
        "status": case.status,
        "data_json": _decrypt_data_json(case.data_json),
        "lead_user_id": _uuid_str(case.lead_user_id),
        "created_by_id": _uuid_str(case.created_by_id),
        "created_at": case.created_at.isoformat(),
        "updated_at": case.updated_at.isoformat(),
        "closed_at": _isoformat(case.closed_at),
        "attachments": _gather_attachments(facility, case, with_file_hashes=with_file_hashes),
    }


def _serialize_episode(episode: Episode) -> dict:
    return {
        "id": str(episode.pk),
        "case_id": str(episode.case_id),
        "title": episode.title,
        "description": episode.description,
        "started_at": _isoformat(episode.started_at),
        "ended_at": _isoformat(episode.ended_at),
        "created_by_id": _uuid_str(episode.created_by_id),
        "created_at": episode.created_at.isoformat(),
    }


def _serialize_milestone(milestone: Milestone) -> dict:
    return {
        "id": str(milestone.pk),
        "title": milestone.title,
        "is_completed": milestone.is_completed,
        "completed_at": _isoformat(milestone.completed_at),
        "sort_order": milestone.sort_order,
        "created_at": milestone.created_at.isoformat(),
    }


def _serialize_goal(goal: OutcomeGoal) -> dict:
    return {
        "id": str(goal.pk),
        "case_id": str(goal.case_id),
        "title": goal.title,
        "description": goal.description,
        "is_achieved": goal.is_achieved,
        "achieved_at": _isoformat(goal.achieved_at),
        "created_by_id": _uuid_str(goal.created_by_id),
        "created_at": goal.created_at.isoformat(),
        "milestones": [_serialize_milestone(m) for m in goal.milestones.all()],
    }


def _serialize_workitem(work_item: WorkItem) -> dict:
    return {
        "id": str(work_item.pk),
        "client_id": _uuid_str(work_item.client_id),
        "created_by_id": _uuid_str(work_item.created_by_id),
        "assigned_to_id": _uuid_str(work_item.assigned_to_id),
        "item_type": work_item.item_type,
        "status": work_item.status,
        "priority": work_item.priority,
        "title": work_item.title,
        "description": work_item.description,
        "created_at": work_item.created_at.isoformat(),
        "updated_at": work_item.updated_at.isoformat(),
        "completed_at": _isoformat(work_item.completed_at),
        "due_date": _isoformat(work_item.due_date),
        "remind_at": _isoformat(work_item.remind_at),
        "recurrence": work_item.recurrence,
    }


def _build_entities(facility, *, with_file_hashes: bool) -> dict:
    """Nur aktive Entities — der Papierkorb bleibt aussen vor (E14).

    Zwei unabhaengige Filter-Ebenen, nicht eine:

    1. Das jeweils EIGENE ``is_deleted``: fuer **Event** load-bearing
       (``soft_delete_event`` in ``core/services/events/crud.py`` und der
       Retention-Purge in ``core/retention/enforcement.py`` setzen es); fuer
       Case/Episode/WorkItem in Produktion aktuell ein No-Op (kein Aufrufer
       setzt es direkt), bleibt als Defense-in-Depth stehen.
    2. Die tatsaechlich wirksame Grenze: der Loeschzustand der PERSON
       (Befund 1+2, Refs #1060). ``live_client`` schliesst Zeilen aus, deren
       Client im Papierkorb liegt; ``client__isnull=True`` bleibt explizit
       erlaubt, weil Event/WorkItem ``client`` als SET_NULL fuehren — anonyme
       Ereignisse und nicht zugeordnete Arbeitsauftraege haben nie eine Person
       und duerfen nicht an ihr haengen bleiben.

    ``Episode``/``OutcomeGoal``/``Milestone`` tragen keinen eigenen
    ``facility``-FK (transitiv ueber ``case``/``goal`` gescopt, wie RLS via
    JOIN-Policy) — hier entsprechend ueber ``case__facility=facility`` gefiltert.
    """
    live_client = Q(client__isnull=True) | Q(client__is_deleted=False)

    clients = Client.objects.for_facility(facility).filter(is_deleted=False).order_by("pseudonym", "pk")
    events = (
        Event.objects.for_facility(facility).filter(is_deleted=False).filter(live_client).order_by("-occurred_at", "pk")
    )
    # Bewusst NICHT ``cases_with_live_client()`` (core/services/case/cases.py):
    # die schliesst zusaetzlich ``client__is_active=False`` (Art.-17-anonymisierte
    # Personen) aus. Deren Client-Zeile bleibt (markiert ``is_anonymized``) im
    # Export — ihre bereits redigierten Faelle sollen der Vollstaendigkeit
    # halber mit exportiert werden, ohne dangling FK zu hinterlassen. Nur der
    # Papierkorb-Zustand (``is_deleted``) ist hier die Grenze, nicht "aktiv".
    cases = (
        CaseModel.objects.for_facility(facility)
        .filter(is_deleted=False, client__is_deleted=False)
        .order_by("-created_at", "pk")
    )
    episodes = (
        Episode.objects.filter(case__facility=facility, is_deleted=False, case__client__is_deleted=False)
        .select_related("case")
        .order_by("-started_at", "pk")
    )
    goals = (
        OutcomeGoal.objects.filter(case__facility=facility, case__client__is_deleted=False)
        .select_related("case")
        .prefetch_related(Prefetch("milestones", queryset=Milestone.objects.order_by("sort_order", "pk")))
        .order_by("-created_at", "pk")
    )
    work_items = (
        WorkItem.objects.for_facility(facility)
        .filter(is_deleted=False)
        .filter(live_client)
        .order_by("-created_at", "pk")
    )

    return {
        "clients": [_serialize_client(c, facility, with_file_hashes=with_file_hashes) for c in clients],
        "events": [_serialize_event(e, facility, with_file_hashes=with_file_hashes) for e in events],
        "cases": [_serialize_case(c, facility, with_file_hashes=with_file_hashes) for c in cases],
        "episodes": [_serialize_episode(ep) for ep in episodes],
        "goals": [_serialize_goal(g) for g in goals],
        "work_items": [_serialize_workitem(wi) for wi in work_items],
    }


def _serialize_facility(facility) -> dict:
    return {
        "id": str(facility.pk),
        "name": facility.name,
        "organization": {
            "id": str(facility.organization_id),
            "name": facility.organization.name,
        },
    }


def build_facility_export(facility, *, with_file_hashes: bool = True) -> dict:
    """Baut den versionierten JSON-Facility-Export (Schema v1) fuer ``facility``.

    ``with_file_hashes=False`` ueberspringt das On-the-fly-Streaming/Hashing
    der Anhang-Inhalte (``sha256`` bleibt in jedem Eintrag als ``null`` stehen,
    Schluessel bleibt vorhanden — stabile Schema-Gestalt unabhaengig vom Flag)
    fuer schnellere Exporte grosser Einrichtungen, auf Kosten der
    Integritaets-Pruefbarkeit der referenzierten Dateien.

    Reine Lesefunktion ohne RLS-Bypass-Kenntnis — der aufrufende Management-
    Command traegt die Fail-Loud-Pruefung (:func:`core.services.system.has_rls_bypass_context`),
    damit dieser Service auch aus einem bereits privilegierten Kontext heraus
    (Tests, zukuenftige Aufrufer) ohne Umweg nutzbar bleibt.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": timezone.now().isoformat(),
        "facility": _serialize_facility(facility),
        "configuration": _build_configuration(facility),
        "entities": _build_entities(facility, with_file_hashes=with_file_hashes),
    }
