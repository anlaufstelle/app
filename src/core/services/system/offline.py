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
from core.models import DocumentType, Event, User, WorkItem
from core.services.compliance import allowed_sensitivities_for_user, user_can_see_field
from core.services.config import iter_fields_in_block_order
from core.services.dynamic_data import orphan_gating_sensitivity, resolve_orphan_field_templates
from core.services.file_vault import safe_decrypt
from core.utils.formatting import isoformat_or_none

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
# v1->v2 (#1529/#1499): ``client.last_contact`` neu im Bundle -> ein
# Alt-v1-Bundle wird beim naechsten Read/Listing per Schema-Mismatch gepurged.
BUNDLE_SCHEMA_VERSION = 2

# Refs #1518 (#1499): eigene Schema-Version fuer das personenlose Facility-Meta-
# Bundle. Bewusst UNABHAENGIG von ``BUNDLE_SCHEMA_VERSION`` — Klient- und
# Facility-Bundle evolvieren getrennt, ein Layout-Wechsel im einen darf den
# Cache des anderen nicht invalidieren.
# v1->v2 (#1912): enger, policy-abgeleiteter Tally-Snapshot samt bestaetigtem
# Serverstand und autoritativem Live-Fenster. Alt-v1 wird im Offline-Store
# fail-closed verworfen.
FACILITY_BUNDLE_SCHEMA_VERSION = 2


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
        "min_value": field_template.min_value,
        "max_value": field_template.max_value,
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
        # #1713: Reihenfolge = globale Block-Sequenz (gruppierte Felder innerhalb
        # ihres Gruppenblocks, freie Felder an ihrer Blockposition), damit der
        # Offline-Viewer dieselbe Feldordnung zeigt wie das Online-Formular
        # (``grouped_fields`` flach ausgerollt) — ein flaches ``order_by(
        # "sort_order")`` würde gruppierte Felder unter neuer Semantik scrambeln.
        "fields": [
            _serialize_field_template(dtf.field_template)
            for dtf in iter_fields_in_block_order(doc_type)
            if dtf.field_template.is_active
            and user_can_see_field(user, doc_type.sensitivity, dtf.field_template.sensitivity or "")
        ],
    }


def _field_gate_sensitivity(field_templates, orphan_templates, slug) -> str:
    """Effektive Gate-Sensitivity eines Slugs (Refs #1891, Extraktion).

    SICHERHEITSBEFUND (Refs #1725): verwaiste Slugs (Wert in ``data_json``,
    aber nicht mehr am Typ) werden facility-weit aufgelöst und bei fehlendem
    Template konservativ HIGH gegatet — sonst fiele ihre Sensitivity auf
    ``""`` (=NORMAL) und ein einst als elevated/high eingestufter Wert
    landete lesbar in IndexedDB.
    """
    ft = field_templates.get(slug)
    if ft is not None:
        return ft.sensitivity or ""
    return orphan_gating_sensitivity(orphan_templates.get(slug))


def _offline_field_value(value: Any) -> Any:
    """Die drei sich ausschließenden Wert-Transformationen (Refs #1891).

    Läuft NUR für Werte, die das Sensitivity-Gate bereits passiert haben —
    insbesondere wird ``safe_decrypt`` nie für gestrippte Felder aufgerufen
    (gepinnt in ``TestVisibleDataFieldsGateMatrix``).

    - Attachment markers are metadata (filename, content-type) — keep them
      so the offline UI can indicate "Datei vorhanden", but never ship the
      file bytes offline. Refs #786 (C-18): Stage-B-Multifile (``__files__``)
      wird genauso minimiert wie Stage-A — nur "Datei vorhanden" + Anzahl,
      KEINE internen Attachment-IDs oder Sortier-Indizes.
    - Encrypted fields (AES at rest on the server) are unusable offline
      without the Fernet key. Decrypt here so the browser bundle is
      readable — the bundle itself is re-encrypted client-side via
      crypto_session before being written to IndexedDB.
    """
    if not isinstance(value, dict):
        return value
    if value.get("__file__"):
        return {"__file__": True, "name": value.get("name", "")}
    if value.get("__files__"):
        return {"__files__": True, "count": _multifile_entry_count(value.get("entries") or [])}
    if value.get("__encrypted__"):
        return safe_decrypt(value)
    return value


def _multifile_entry_count(entries) -> int:
    """Zählt nur valide Einträge (dict mit ``id``) eines Multifile-Markers."""
    return sum(1 for e in entries if isinstance(e, dict) and e.get("id"))


def _visible_data_fields(user, event) -> dict[str, Any]:
    """Return the event's data_json restricted to fields the user may see.

    Refs #1891: Gate-Prädikat (``_field_gate_sensitivity`` +
    ``user_can_see_field``) und Wert-Transformation (``_offline_field_value``)
    sind getrennt — das Gate entscheidet VOR jeder Transformation, ob ein
    Wert das Bundle überhaupt verlassen darf (Leak-Grenze der Offline-Bundles).
    """
    if not event.data_json:
        return {}
    doc_type = event.document_type
    # Map slug → FieldTemplate so we can look up per-field sensitivity.
    field_templates = {
        dtf.field_template.slug: dtf.field_template for dtf in doc_type.fields.select_related("field_template")
    }
    # Refs #1725: Auflösung der verwaisten Slugs — gleiche Auflösung wie
    # ``build_event_detail_context`` und der Konflikt-Diff (Details am
    # Gate-Helfer ``_field_gate_sensitivity``).
    orphan_slugs = [slug for slug in event.data_json if slug not in field_templates]
    orphan_templates = resolve_orphan_field_templates(doc_type, orphan_slugs)
    result: dict[str, Any] = {}
    for slug, value in event.data_json.items():
        field_sensitivity = _field_gate_sensitivity(field_templates, orphan_templates, slug)
        if not user_can_see_field(user, doc_type.sensitivity, field_sensitivity):
            continue
        result[slug] = _offline_field_value(value)
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
        "updated_at": isoformat_or_none(event.updated_at),
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
        "closed_at": isoformat_or_none(case.closed_at),
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
        "due_date": isoformat_or_none(workitem.due_date),
        # Refs #1398: Offline-Edit-Metadaten — Konflikt-Token (updated_at, wie
        # Events F-07) + die restlichen editierbaren WorkItemForm-Felder.
        "updated_at": isoformat_or_none(workitem.updated_at),
        "remind_at": isoformat_or_none(workitem.remind_at),
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


def _bundle_document_types(user, facility, events) -> list[DocumentType]:
    """DocType-Katalog des Client-Bundles, fertig sortiert (Refs #1891, Extraktion).

    Ship the full ACTIVE document-type catalogue so offline-create offers the
    same choices as online (EventMetaForm: ``for_facility`` + ``is_active``),
    PLUS any type referenced by the cached events even if since deactivated —
    so existing event values still render offline (union). Refs #1397.

    Refs #1525 (Review MEDIUM, wie #1518/Facility-Bundle): denselben DocType-
    EBENEN-Sensitivity-Filter anwenden, den das Online-``EventMetaForm``
    fuehrt (forms/events.py: ``sensitivity__in=allowed_sensitivities_for_user``).
    Ohne ihn wuerde NAME/Metadaten eines HIGH/ELEVATED-DocType offline an
    niedrigere Rollen leaken — Verstoss gegen die Bundle-ist-Derivat-
    Invariante. Die per-Event referenzierten Typen bleiben ungefiltert:
    ``events`` lief bereits durch ``Event.objects.visible_to(user)``
    (dieselbe ``allowed_sensitivities_for_user``-Grenze), sodass die Union
    keinen zusaetzlichen Leak oeffnen kann.

    M2 (Refs #1058, F-1): zusaetzlich ``entity_type=EVENT`` —
    ohne den Filter koennte ein client-/case-Typ im Offline-Create-Picker
    auftauchen; ein darauf basierender Replay-POST auf ``event_create``
    wuerde vom (bereits entity_type-gefilterten) ``EventMetaForm`` als
    ungueltige Wahl verworfen (422/Dead-Letter) — dieselbe Bundle-ist-
    Derivat-Verletzung wie ein fehlender Sensitivity-Filter, nur als
    Correctness- statt Leak-Vektor. Die Event-Union bleibt bewusst
    ungefiltert: Events tragen ohnehin nur event-Typen (P1-Invariante).

    Refs #1498: das Union-Dict haengt referenzierte (ggf. inaktive) Typen
    NACHTRAEGLICH ans Ende — deshalb explizit nach (sort_order, name)
    sortieren, analog ``DocumentType.Meta.ordering`` und ``EventMetaForm``,
    damit ein per Event nachgezogener Typ korrekt einsortiert wird.
    """
    doc_types = {
        dt.pk: dt
        for dt in DocumentType.objects.for_facility(facility)
        .filter(is_active=True, entity_type=DocumentType.EntityType.EVENT)
        .filter(sensitivity__in=allowed_sensitivities_for_user(user))
        .order_by("sort_order", "name")
    }
    for ev in events:
        if ev.document_type_id not in doc_types:
            doc_types[ev.document_type_id] = ev.document_type
    return sorted(doc_types.values(), key=lambda dt: (dt.sort_order, dt.name))


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

    doc_types = _bundle_document_types(user, facility, events)

    # Notes are a HIGH-sensitivity free-text field, and full case detail
    # (any status, description) is STAFF_PLUS-only online too
    # (CaseListView/CaseDetailView, cases.py:70) — one flag gates both so the
    # offline bundle can never show more than the user would see online
    # (ADR-022, Refs #1355).
    is_staff_or_above = user.is_staff_or_above if hasattr(user, "is_staff_or_above") else False

    # Lokaler Import: ``core.services.case`` zieht ueber den Loesch-Service
    # ``core.services.dashboard`` nach und schliesst auf Modulebene einen
    # Import-Zyklus ueber ``core.services.events``.
    from core.services.case import cases_with_live_client

    # Refs #1818: ueber ``cases_with_live_client()`` statt ueber
    # ``CaseModel.objects`` — das Bundle ist ein Derivat der Online-Sicht, und
    # der Helfer ist deren Choke-Point. Ein Fall im Papierkorb ist online in
    # Liste, Akte, Episoden und Zielen weg; ohne denselben Filter verliesse
    # sein Freitext (Titel, Beschreibung) das System auf ein Endgeraet und
    # bliebe dort bis zum naechsten Sync bzw. bis zum TTL-Ablauf lesbar —
    # genau das Gegenteil des Loeschversprechens. Die Personen-Haelfte des
    # Helfers ist hier Defense-in-Depth (der Bundle-Endpoint laesst eine
    # widerrufene Person schon nicht durch), die Fall-Haelfte ist wirksam.
    cases = (
        cases_with_live_client()
        .for_facility(facility)
        .filter(client=client)
        .select_related("lead_user")
        .order_by("-created_at")
    )
    if not is_staff_or_above:
        cases = cases.filter(status=CaseModel.Status.OPEN)
    workitems = (
        WorkItem.objects.for_facility(facility)
        .filter(client=client)
        .filter(status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS])
        .select_related("assigned_to", "created_by")
        .order_by("-created_at")
    )

    # Contact metadata follows role visibility without the bundle's lookback limit.
    last_contact = (
        Event.objects.for_facility(facility)
        .visible_to(user)
        .filter(client=client, is_deleted=False)
        .aggregate(last_contact=Max("occurred_at"))["last_contact"]
    )

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
            "last_contact": isoformat_or_none(last_contact),
        },
        "cases": [_serialize_case(user, c) for c in cases],
        "workitems": [_serialize_workitem(user, w) for w in workitems],
        # Refs #1398: zuweisbare Nutzer:innen nur fuer Staff+ (nur sie legen
        # WorkItems an/weisen zu) — begrenzt die Roster-Exposition im Offline-Cache.
        "assignable_users": _serialize_assignable_users(facility) if is_staff_or_above else [],
        "events": [_serialize_event(user, ev) for ev in events],
        # ``doc_types`` kommt fertig sortiert aus ``_bundle_document_types``
        # (Union + Sortier-Begruendung dort, Refs #1498).
        "document_types": [_serialize_document_type(user, dt) for dt in doc_types],
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
    # M2 (Refs #1058, F-1): zusaetzlich ``entity_type=EVENT`` —
    # exakt dieselbe Begruendung wie im Client-Bundle oben (build_client_
    # offline_bundle): ohne den Filter koennte ein client-/case-Typ im
    # personenlosen Offline-Create-Picker landen und einen Replay-POST auf
    # ``event_create`` in die 422/Dead-Letter-Falle laufen lassen.
    doc_types = (
        DocumentType.objects.for_facility(facility)
        .filter(is_active=True, entity_type=DocumentType.EntityType.EVENT)
        .filter(sensitivity__in=allowed_sensitivities_for_user(user))
        .order_by("sort_order", "name")
    )

    is_staff_or_above = getattr(user, "is_staff_or_above", False)

    # #1912: Der Offline-Tally-Vertrag wird NICHT aus dem breiten Create-
    # Katalog oben abgeleitet. ``build_tally_summary`` ist derselbe zentrale
    # Policy-/Zaehlerpfad wie das Online-Panel und liefert ausschliesslich fuer
    # diese Rolle geeignete Typen. Die an den DocumentType-Objekten
    # vorgeprefetchten Policy-Zuordnungen reichen fuer die schmale
    # Feldserialisierung aus; optionale/versteckte/sensitive Felder,
    # Bedingungen und Ablehnungsgruende verlassen den Server nicht.
    from core.services.events import build_tally_summary, evaluate_tally_eligibility

    tally_summary = build_tally_summary(facility, user)
    generated_at = timezone.now()
    tally_rows = []
    for row in tally_summary["rows"]:
        eligibility = evaluate_tally_eligibility(row["document_type"], user)
        # Defense-in-depth gegen einen kuenftigen Drift zwischen Summary und
        # Einzeltyp-Policy: ein nicht mehr geeigneter Typ wird nie serialisiert.
        if not eligibility.eligible or eligibility.mode is None:
            continue
        tally_rows.append(
            {
                "document_type_id": str(row["document_type"].pk),
                "document_type_name": row["document_type"].name,
                "mode": eligibility.mode.value,
                "fields": [
                    {
                        "slug": assignment.field_template.slug,
                        "name": assignment.field_template.name,
                        "min_value": assignment.field_template.min_value,
                        "max_value": assignment.field_template.max_value,
                    }
                    for assignment in eligibility.required_number_fields
                ],
                "confirmed_count": row["count"],
            }
        )

    time_filter = tally_summary["time_filter"]

    return {
        "schema_version": FACILITY_BUNDLE_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "ttl": BUNDLE_TTL_SECONDS,
        "expires_at": (generated_at + timedelta(seconds=BUNDLE_TTL_SECONDS)).isoformat(),
        "document_types": [_serialize_document_type(user, dt) for dt in doc_types],
        # Refs #1398/#1518: zuweisbare Nutzer:innen nur fuer Staff+ (nur sie
        # legen WorkItems an/weisen zu) — begrenzt die Roster-Exposition und
        # speist den Staff+-Gate der Offline-WorkItem-Shell.
        "assignable_users": _serialize_assignable_users(facility) if is_staff_or_above else [],
        "tally": {
            "snapshot_at": generated_at.isoformat(),
            "shift_date": tally_summary["shift_date"].isoformat(),
            "time_filter": (
                {"id": str(time_filter.pk), "label": time_filter.label} if time_filter is not None else None
            ),
            "window_start": tally_summary["window_start"].isoformat(),
            "window_end": tally_summary["window_end"].isoformat(),
            "rows": tally_rows,
        },
    }
