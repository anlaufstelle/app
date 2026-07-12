"""Serialization of client data for the offline-read-cache (Refs #574, #572).

The bundle is a *derivative* of the server-side state: it is built by running
the same ``visible_to(user)`` and ``user_can_see_field`` gates that the online
views apply, so an offline cache can never leak data the user would not see
online. Field-level sensitivity is respected by dropping restricted keys from
the ``data_fields`` dict before the payload leaves the server. Cases are
gated the same way (Refs #1355): non-staff only receive ``status=OPEN``
cases with ``description`` blanked, mirroring what ``CaseListView``/
``CaseDetailView`` and the client-detail case list already restrict online.

The bundle is intentionally small — metadata + visible field values — so that
the Dexie-encrypted payload in the browser stays below a few hundred kB per
client even for heavy users.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Max
from django.utils import timezone

from core.models import Case as CaseModel
from core.models import Client, DocumentType, Event, User, WorkItem
from core.services.compliance import allowed_sensitivities_for_user, user_can_see_field
from core.services.file_vault import safe_decrypt

# Include at most this many events per client, or all events within the
# lookback window — whichever is smaller. Caps the offline bundle size.
MAX_EVENTS_PER_BUNDLE = 50
LOOKBACK_DAYS = 90

# Time the client may render the offline bundle before forcing a re-sync.
BUNDLE_TTL_SECONDS = 48 * 3600

# Schema version embedded in the bundle. Increment whenever the bundle layout
# changes in a non-backwards-compatible way so the client can purge stale
# caches after an app upgrade. MUSS mit ``BUNDLE_SCHEMA_VERSION`` in
# ``src/static/js/offline-store.js`` synchron bleiben (kein Build-Sync).
# v1->v2 (SI-1, #1529/#1499): ``client.last_contact`` neu im Bundle -> ein
# Alt-v1-Bundle wird beim naechsten Read/Listing per Schema-Mismatch gepurged.
BUNDLE_SCHEMA_VERSION = 2

# Refs #1518 (#1499): eigene Schema-Version fuer das personenlose Facility-Meta-
# Bundle. Bewusst UNABHAENGIG von ``BUNDLE_SCHEMA_VERSION`` — Klient- und
# Facility-Bundle evolvieren getrennt, ein Layout-Wechsel im einen darf den
# Cache des anderen nicht invalidieren.
FACILITY_BUNDLE_SCHEMA_VERSION = 1


def _serialize_field_template(field_template) -> dict[str, Any]:
    return {
        "slug": field_template.slug,
        "name": field_template.name,
        "field_type": field_template.field_type,
        "sensitivity": field_template.sensitivity or "",
        "is_encrypted": field_template.is_encrypted,
        # Refs #1111: Render-Metadaten, damit der Offline-Viewer ein
        # Edit-Formular OHNE Serverkontakt aufbauen kann — Pflichtfeld-Marker,
        # Hilfetext und (für SELECT/MULTI_SELECT) die auswählbaren Optionen als
        # ``value``/``label``-Paare. Es verlässt nur Schema (keine PII): die
        # Werte selbst bleiben in ``_visible_data_fields`` sensitivity-gefiltert.
        "is_required": field_template.is_required,
        "help_text": field_template.help_text or "",
        "options": [{"value": value, "label": label} for value, label in field_template.choices],
    }


def _serialize_document_type(user, doc_type) -> dict[str, Any]:
    return {
        "pk": str(doc_type.pk),
        "name": doc_type.name,
        # Refs #1397: Der Offline-Create-Dropdown bietet nur AKTIVE Typen an
        # (wie EventMetaForm); inaktiv-referenzierte Typen bleiben im Bundle
        # nur zum Rendern bestehender Events.
        "is_active": doc_type.is_active,
        "category": doc_type.category,
        "sensitivity": doc_type.sensitivity,
        # Refs #1518 (#1499): Mindest-Kontaktstufe (leer = keine) fuer den
        # weichen Picker-Vorfilter der Offline-Create-Shell — Typen mit einer
        # Mindeststufe sind „ohne Person" (anonym) nicht waehlbar. Der harte
        # Server-Validator bleibt beim Replay die letzte Instanz. ``None`` wird
        # wie icon/color zu "" normalisiert (JSON-clean, JS-falsy).
        "min_contact_stage": doc_type.min_contact_stage or "",
        "icon": doc_type.icon or "",
        "color": doc_type.color or "",
        # Refs #1111: Nur Felder serialisieren, deren Wert der Nutzer per
        # Sensitivity sehen/bearbeiten darf — dieselbe Grenze wie die
        # Wert-Filterung in ``_visible_data_fields``. So verlässt kein
        # zusätzliches Schema (Optionen/Hilfetext) für gesperrte Felder den
        # Server und der Offline-Editor rendert nur editierbare Felder.
        "fields": [
            _serialize_field_template(dtf.field_template)
            for dtf in doc_type.fields.select_related("field_template").order_by("sort_order")
            if user_can_see_field(user, doc_type.sensitivity, dtf.field_template.sensitivity or "")
        ],
    }


def _visible_data_fields(user, event) -> dict[str, Any]:
    """Return the event's data_json restricted to fields the user may see."""
    if not event.data_json:
        return {}
    doc_type = event.document_type
    # Map slug → FieldTemplate so we can look up per-field sensitivity.
    field_templates = {
        dtf.field_template.slug: dtf.field_template for dtf in doc_type.fields.select_related("field_template")
    }
    result: dict[str, Any] = {}
    for slug, value in event.data_json.items():
        ft = field_templates.get(slug)
        field_sensitivity = ft.sensitivity if ft else ""
        if not user_can_see_field(user, doc_type.sensitivity, field_sensitivity):
            continue
        # Attachment markers are metadata (filename, content-type) — keep them
        # so the offline UI can indicate "Datei vorhanden", but never ship the
        # file bytes offline. Refs #786 (C-18): Stage-B-Multifile (``__files__``)
        # wird genauso minimiert wie Stage-A — nur "Datei vorhanden" + Anzahl,
        # KEINE internen Attachment-IDs oder Sortier-Indizes.
        if isinstance(value, dict) and value.get("__file__"):
            result[slug] = {"__file__": True, "name": value.get("name", "")}
            continue
        if isinstance(value, dict) and value.get("__files__"):
            entries = value.get("entries") or []
            result[slug] = {
                "__files__": True,
                "count": sum(1 for e in entries if isinstance(e, dict) and e.get("id")),
            }
            continue
        # Encrypted fields (AES at rest on the server) are unusable offline
        # without the Fernet key. Decrypt here so the browser bundle is
        # readable — the bundle itself is re-encrypted client-side via
        # crypto_session before being written to IndexedDB.
        if isinstance(value, dict) and value.get("__encrypted__"):
            result[slug] = safe_decrypt(value)
        else:
            result[slug] = value
    return result


def _serialize_event(user, event) -> dict[str, Any]:
    return {
        "pk": str(event.pk),
        "occurred_at": event.occurred_at.isoformat(),
        # Optimistic-Lock-Token (Refs #1109, F-07): Der Offline-Replay schickt
        # diesen Wert als ``expected_updated_at`` zurück, damit der
        # serverseitige Konflikt-Check (``check_version_conflict``) auch für
        # offline entstandene Edits greift. Ohne ihn bliebe der Token leer und
        # der Check würde übersprungen → stilles Last-Write-Wins.
        "updated_at": event.updated_at.isoformat() if event.updated_at else None,
        "document_type_pk": str(event.document_type_id),
        "document_type_name": event.document_type.name,
        "created_by_display": (
            event.created_by.get_full_name() or event.created_by.username if event.created_by else ""
        ),
        "case_pk": str(event.case_id) if event.case_id else None,
        "episode_pk": str(event.episode_id) if event.episode_id else None,
        "is_anonymous": event.is_anonymous,
        "data_fields": _visible_data_fields(user, event),
        # Refs #1111: Spiegelt die Edit-Berechtigung aus ``EventUpdateView``
        # (Staff+ darf alles, Assistant nur eigene Events) in den Snapshot, damit
        # der Offline-Viewer die Edit-Affordanz nur dort zeigt, wo der Replay
        # auch durchginge — sonst stranden Assistant-Edits in einem 403.
        "can_edit": bool(
            getattr(user, "is_staff_or_above", False)
            or (event.created_by_id is not None and event.created_by_id == getattr(user, "pk", None))
        ),
    }


def _serialize_case(user, case) -> dict[str, Any]:
    # Refs #1355: description is STAFF_PLUS-only online (CaseDetailView,
    # cases.py:70) — non-staff still get the key (schema-stable) but blanked,
    # same idiom as _serialize_event's can_edit.
    is_staff_or_above = getattr(user, "is_staff_or_above", False)
    return {
        "pk": str(case.pk),
        "title": case.title,
        "description": case.description if is_staff_or_above else "",
        "status": case.status,
        "status_display": case.get_status_display(),
        "created_at": case.created_at.isoformat(),
        "closed_at": case.closed_at.isoformat() if case.closed_at else None,
        "lead_user_display": (case.lead_user.get_full_name() or case.lead_user.username if case.lead_user else ""),
    }


def _serialize_workitem(user, workitem) -> dict[str, Any]:
    # Refs #1398: ``can_edit`` spiegelt die Online-Mutations-Policy — der
    # Edit-View ist ``StaffRequired`` UND ``can_user_mutate_workitem``
    # (Lead/Admin, Ersteller:in, Zugewiesene:r oder unzugewiesene Teamaufgabe).
    # Assistenz kann WorkItems nicht mutieren. Funktions-lokaler Import
    # vermeidet einen Service→View-Import-Zirkel.
    from core.views.workitems import can_user_mutate_workitem

    can_edit = bool(getattr(user, "is_staff_or_above", False) and can_user_mutate_workitem(user, workitem))
    return {
        "pk": str(workitem.pk),
        "title": workitem.title,
        "description": workitem.description,
        "status": workitem.status,
        "priority": workitem.priority,
        "item_type": workitem.item_type,
        "due_date": workitem.due_date.isoformat() if workitem.due_date else None,
        # Refs #1398: Offline-Edit-Metadaten — Konflikt-Token (updated_at, wie
        # Events F-07) + die restlichen editierbaren WorkItemForm-Felder.
        "updated_at": workitem.updated_at.isoformat() if workitem.updated_at else None,
        "remind_at": workitem.remind_at.isoformat() if workitem.remind_at else None,
        "recurrence": workitem.recurrence,
        "assigned_to_pk": str(workitem.assigned_to_id) if workitem.assigned_to_id else None,
        "can_edit": can_edit,
    }


def _serialize_assignable_users(facility) -> list[dict[str, Any]]:
    # Refs #1398: dieselbe Menge wie das ``WorkItemForm.assigned_to``-Queryset —
    # aktive Facility-Nutzer:innen der zuweisbaren Rollen. Nur pk + Anzeigename
    # (Staff-Roster, KEINE Klientel-PII; ADR-022-Notiz). Wird nur fuer Staff+ ins
    # Bundle gelegt (nur sie koennen WorkItems anlegen/zuweisen).
    # Refs #1526: gegen ``WorkItemForm.assigned_to`` (forms/workitems.py)
    # verifiziert — beide filtern auf FACILITY_ADMIN/LEAD/STAFF/ASSISTANT,
    # kein Drift. ASSISTANT ist bewusst enthalten (Refs #1125: Assistenz ist
    # als Zuweisungsziel zulaessig). Ein Regressionstest
    # (``test_assignable_users_role_set_matches_workitem_form_no_drift``)
    # vergleicht beide Rollenmengen direkt gegen das echte Formular-Queryset,
    # damit ein kuenftiger Drift in einer der beiden Stellen auffliegt.
    users = User.objects.filter(
        facility=facility,
        is_active=True,
        role__in=[User.Role.FACILITY_ADMIN, User.Role.LEAD, User.Role.STAFF, User.Role.ASSISTANT],
    ).order_by("username")
    return [{"pk": str(u.pk), "name": u.get_full_name() or u.username} for u in users]


def build_client_offline_bundle(user, facility, client) -> dict[str, Any]:
    """Assemble a server-filtered snapshot of a single client.

    All role-based and field-level visibility gates are applied here so the
    resulting dict is safe to hand to the caller (it will be re-encrypted in
    the browser before hitting IndexedDB, but the server-side filter is the
    authoritative boundary, not the client-side crypto).
    """

    cutoff = timezone.now() - timedelta(days=LOOKBACK_DAYS)
    events_qs = (
        Event.objects.for_facility(facility)
        .visible_to(user)
        .filter(client=client, is_deleted=False, occurred_at__gte=cutoff)
        .select_related("document_type", "created_by", "case", "episode")
        .order_by("-occurred_at")[:MAX_EVENTS_PER_BUNDLE]
    )
    events = list(events_qs)

    # Ship the full ACTIVE document-type catalogue so offline-create offers the
    # same choices as online (EventMetaForm: ``for_facility`` + ``is_active``),
    # PLUS any type referenced by the cached events even if since deactivated —
    # so existing event values still render offline (union). Refs #1397.
    # Refs #1525 (Review MEDIUM, wie #1518/Facility-Bundle): denselben DocType-
    # EBENEN-Sensitivity-Filter anwenden, den das Online-``EventMetaForm``
    # fuehrt (forms/events.py: ``sensitivity__in=allowed_sensitivities_for_user``).
    # Ohne ihn wuerde NAME/Metadaten eines HIGH/ELEVATED-DocType offline an
    # niedrigere Rollen leaken — Verstoss gegen die Bundle-ist-Derivat-
    # Invariante. Die per-Event referenzierten Typen unten bleiben ungefiltert:
    # ``events_qs`` lief bereits durch ``Event.objects.visible_to(user)``
    # (dieselbe ``allowed_sensitivities_for_user``-Grenze), sodass die Union
    # keinen zusaetzlichen Leak oeffnen kann.
    doc_types = {
        dt.pk: dt
        for dt in DocumentType.objects.for_facility(facility)
        .filter(is_active=True)
        .filter(sensitivity__in=allowed_sensitivities_for_user(user))
    }
    for ev in events:
        if ev.document_type_id not in doc_types:
            doc_types[ev.document_type_id] = ev.document_type

    # Notes are a HIGH-sensitivity free-text field, and full case detail
    # (any status, description) is STAFF_PLUS-only online too
    # (CaseListView/CaseDetailView, cases.py:70) — one flag gates both so the
    # offline bundle can never show more than the user would see online
    # (ADR-022, Refs #1355).
    is_staff_or_above = user.is_staff_or_above if hasattr(user, "is_staff_or_above") else False

    cases = (
        CaseModel.objects.for_facility(facility)
        .filter(client=client)
        .select_related("lead_user")
        .order_by("-created_at")
    )
    if not is_staff_or_above:
        cases = cases.filter(status=CaseModel.Status.OPEN)
    workitems = (
        WorkItem.objects.filter(client=client, facility=facility)
        .filter(status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS])
        .select_related("assigned_to", "created_by")
        .order_by("-created_at")
    )

    # SI-1 (#1529, #1499): ``last_contact`` fuer die Offline-Personenliste —
    # BEWUSST cutoff- UND is_deleted-FREI, exakt die Online-Spalten-Annotation
    # aus ``ClientListView`` (views/clients.py: ``Max("events__occurred_at")``),
    # damit der offline gezeigte Wert dem Online-Listenwert 1:1 entspricht.
    # NICHT aus der oben gefilterten 90-Tage-``events``-Liste ableiten: die
    # waere fuer >90d zurueckliegende oder geloeschte Kontakte falsch. Der
    # ``Max``-Join ueber die reverse ``events``-Relation ignoriert jeden
    # Manager-Filter (raw SQL join) — genau wie die Online-Annotation, die
    # deshalb ebenfalls geloeschte Events einschliesst. ``None`` (kontaktloser
    # Klient) bleibt JSON-clean ``None``, sonst ISO-8601-String.
    last_contact = Client.objects.filter(pk=client.pk).aggregate(
        last_contact=Max("events__occurred_at"),
    )["last_contact"]

    generated_at = timezone.now()

    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "ttl": BUNDLE_TTL_SECONDS,
        "expires_at": (generated_at + timedelta(seconds=BUNDLE_TTL_SECONDS)).isoformat(),
        "client": {
            "pk": str(client.pk),
            "pseudonym": client.pseudonym,
            "contact_stage": client.contact_stage,
            "contact_stage_display": client.get_contact_stage_display(),
            "age_cluster": client.age_cluster,
            "age_cluster_display": client.get_age_cluster_display(),
            "notes": client.notes if is_staff_or_above else "",
            "is_active": client.is_active,
            "last_contact": last_contact.isoformat() if last_contact else None,
        },
        "cases": [_serialize_case(user, c) for c in cases],
        "workitems": [_serialize_workitem(user, w) for w in workitems],
        # Refs #1398: zuweisbare Nutzer:innen nur fuer Staff+ (nur sie legen
        # WorkItems an/weisen zu) — begrenzt die Roster-Exposition im Offline-Cache.
        "assignable_users": _serialize_assignable_users(facility) if is_staff_or_above else [],
        "events": [_serialize_event(user, ev) for ev in events],
        "document_types": [_serialize_document_type(user, dt) for dt in doc_types.values()],
    }


def build_facility_offline_bundle(user, facility) -> dict[str, Any]:
    """Assemble a *person-less* snapshot of a facility's offline-create metadata.

    Refs #1518 (#1499). The facility bundle mirrors the client bundle but
    carries NO client roster and NO per-person data (events/cases/work items).
    It ships exactly what an offline "+"-create shell needs to author an event
    or work item cold-offline — before any specific client has been taken
    offline: the ACTIVE document-type catalogue (with field schema) plus the
    assignable-user roster for work items.

    The same field-level sensitivity boundary applies as in the client bundle:
    ``_serialize_document_type`` runs each field through ``user_can_see_field``,
    so a restricted field's schema (options/help text) never leaves the server
    (Bundle-ist-Derivat-Invariante). ``assignable_users`` is a staff roster (no
    client PII) and — mirroring the client builder — is only shipped to Staff+
    (only they can create/assign work items); Assistants receive an empty list,
    which the offline WorkItem shell uses as its create-gate.
    """

    # Personenlos: nur der aktive Offline-Create-Katalog (wie EventMetaForm),
    # ohne Event-Vereinigung (kein Klient ⇒ keine referenzierten Alt-Typen).
    # Refs #1518 (Review MEDIUM): denselben DocType-EBENEN-Sensitivity-Filter
    # anwenden, den das Online-``EventMetaForm`` fuehrt (forms/events.py:
    # ``sensitivity__in=allowed_sensitivities_for_user(user)``). Ohne ihn wuerde
    # NAME/Metadaten eines HIGH/ELEVATED-DocType offline an niedrigere Rollen
    # leaken und in SI-4s Create-Picker auftauchen — Verstoss gegen die
    # Bundle-ist-Derivat-Invariante. So traegt das Bundle nur die *erstellbaren*
    # Typen (echtes Derivat des Online-Create-Formulars). Der bestehende
    # ``user_can_see_field``-Filter in ``_serialize_document_type`` bleibt die
    # zweite (Feld-EBENEN-)Grenze.
    doc_types = (
        DocumentType.objects.filter(facility=facility, is_active=True)
        .filter(sensitivity__in=allowed_sensitivities_for_user(user))
        .order_by("sort_order", "name")
    )

    is_staff_or_above = getattr(user, "is_staff_or_above", False)

    generated_at = timezone.now()

    return {
        "schema_version": FACILITY_BUNDLE_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "ttl": BUNDLE_TTL_SECONDS,
        "expires_at": (generated_at + timedelta(seconds=BUNDLE_TTL_SECONDS)).isoformat(),
        "document_types": [_serialize_document_type(user, dt) for dt in doc_types],
        # Refs #1398/#1518: zuweisbare Nutzer:innen nur fuer Staff+ (nur sie
        # legen WorkItems an/weisen zu) — begrenzt die Roster-Exposition und
        # speist den Staff+-Gate der Offline-WorkItem-Shell (SI-5).
        "assignable_users": _serialize_assignable_users(facility) if is_staff_or_above else [],
    }
