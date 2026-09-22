"""Service layer for Case CRUD and event assignment."""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import Activity, AuditLog, Case, DocumentType
from core.services.audit import apply_allowlisted_update, audit_event, check_update_allowlist
from core.services.case.state_contract import lock_case_for_status_change
from core.services.compliance import user_can_see_event
from core.services.dashboard import log_activity
from core.services.dynamic_data import clean_dynamic_data
from core.services.security import check_version_conflict

logger = logging.getLogger(__name__)

_CASE_DOCUMENT_TYPE_ERROR = _("Der Dokumentationstyp ist nicht für Fälle vorgesehen.")


def cases_with_live_client():
    """Lebende Faelle lebender Personen (Refs #1777, seit #1818 auch am Fall).

    **Die Loeschgrenze der Fall-Domaene verlief zunaechst nur ueber die
    Person.** ``Case`` erbte zwar ``SoftDeletableModel``, aber kein
    Produktionspfad setzte ``is_deleted`` je auf einem Fall — es gab keinen
    Fall-Loeschworkflow (ADR-039 §6). Seit dem Fall-Loeschpfad (#1818,
    [ADR-044]) gibt es ihn, und der Filter am Fall selbst ist nicht mehr
    wirkungslos, sondern die Papierkorb-Grenze:

    * ``is_deleted=True`` — Fall im Papierkorb (#1818); Detail, Bearbeiten,
      Episoden, Ziele und die Fallliste antworten damit wie bei einer
      widerrufenen Person,
    * ``client__is_deleted=True`` — Person im Papierkorb (#1737),
    * ``client__is_active=False`` — Person anonymisiert (#1771; muss NICHT im
      Papierkorb liegen, die K-Anon-Bridge laesst ``is_deleted=False``).

    Ohne diesen Filter bleibt der Fall der letzte offene Zugang zum Bestand
    einer widerrufenen Person — lesend (Titel, Beschreibung, ``data_json``,
    Anhaenge) und ueber Bearbeiten/Episoden/Ziele auch schreibend, waehrend
    Personen-Detail und -Bearbeiten laengst 404 antworten.

    Liefert ein ``FacilityScopedQuerySet``; ``.for_facility(...)`` und
    ``select_related(...)`` bleiben also anwendbar, und
    :func:`~core.services.scoping.get_scoped_object` nimmt es direkt entgegen.
    Beide Haelften der Grenze sind in ``tests/test_cases_trash_invariant.py``
    gepinnt. Der Papierkorb-Zugang selbst (Liste, Wiederherstellen) fragt
    bewusst **nicht** ueber diesen Helfer ab — er ist der eine Ort, an dem ein
    geloeschter Fall sichtbar sein muss.
    """
    return Case.objects.filter(is_deleted=False, client__is_deleted=False, client__is_active=True)


def get_case_or_none(facility, case_id):
    """Lookup a case by PK scoped to *facility* (Refs #1813 T4).

    Analog zu :func:`core.services.client.main.get_client_or_none`: liefert
    ``None`` statt eine Exception, wenn der Fall nicht existiert, die ID keine
    gültige UUID ist, der Fall einer anderen Facility gehört, oder seine
    Person widerrufen ist (Papierkorb/anonymisiert — ``cases_with_live_client``,
    Refs #1777). Der Live-Client-Filter ist hier bewusst mit drin: ein Aufrufer,
    der aus dieser Rückgabe eine Person ableitet (z. B. ``?case=``-Vorbelegung
    in ``EventCreateView``), darf über einen fremden/widerrufenen Fall kein
    Existenz-Orakel bekommen.

    Gedacht für View-GET-Handler, die einen vorselektierten Fall aus der
    URL/Query laden wollen, ohne bei fehlendem/ungültigem Parameter eine 404
    zu werfen.
    """
    if not case_id:
        return None
    try:
        return cases_with_live_client().get(pk=case_id, facility=facility)
    except (Case.DoesNotExist, ValueError, ValidationError):
        # Django wirft bei ungültigen UUID-Strings ValidationError (nicht
        # ValueError) — beide Fälle werden als "not found" behandelt.
        return None


@transaction.atomic
def create_case(facility, user, client, title, description="", lead_user=None, document_type=None, data_json=None):
    """Create a new Case with status OPEN.

    M2 P5 (Refs #1058): ``document_type``/``data_json`` laufen durch
    :func:`~core.services.dynamic_data.clean_dynamic_data` — Facility-Scope,
    ``entity_type=case`` und die Slug-Whitelist gelten schon beim Anlegen,
    nicht erst beim Update.
    Beide Parameter sind explizit benannt (kein ``**data``-Durchreichen an
    ``Case(...)``), daher gibt es hier — anders als bei ``create_client`` —
    keine ``document_type_id=<pk>``-Umgehung zu schliessen.
    """
    if client is None:
        # Refs #748: Fälle sind fachlich Vorgänge zu konkreten Personen.
        raise ValidationError(_("Fälle müssen einer Person zugeordnet sein."))
    if client.facility_id != facility.pk:
        raise ValueError(_("Person gehört nicht zur Einrichtung."))
    from core.services.client.restriction import assert_processing_allowed

    # Art.-18-Gate (Refs #2045): ein neuer Fall ist neue inhaltliche
    # Verarbeitung, nicht Speicherung des Bestands.
    assert_processing_allowed(client, lock=True)
    if lead_user and lead_user.facility_id != facility.pk:
        raise ValueError(_("Fallverantwortlicher gehört nicht zur Einrichtung."))

    data_json = clean_dynamic_data(
        facility,
        document_type,
        data_json,
        entity_type=DocumentType.EntityType.CASE,
        entity_type_error=_CASE_DOCUMENT_TYPE_ERROR,
    )
    case = Case(
        facility=facility,
        client=client,
        title=title,
        description=description,
        status=Case.Status.OPEN,
        created_by=user,
        lead_user=lead_user,
        document_type=document_type,
        data_json=data_json,
    )
    case.save()
    audit_event(
        AuditLog.Action.CASE_CREATE,
        user=user,
        facility=facility,
        target_obj=case,
    )
    return case


@transaction.atomic
def update_case(case, user, *, expected_updated_at=None, **fields):
    """Update mutable fields on a case (title, description, lead_user,
    document_type, data_json).

    Writes an AuditLog entry with the names of changed fields (no PII values).

    ``expected_updated_at`` enables optimistic locking (Refs #531) — when the
    DB-side ``updated_at`` differs, a ``ValidationError`` is raised.

    Ursprungs-Pinning (Refs #1786): ``client`` ist **kein** Update-Feld mehr.
    Die Personenzuordnung eines Falls entsteht beim Anlegen und ist danach
    unveränderlich — ein nachträglicher Wechsel würde den kompletten
    Fallverlauf (Titel, Beschreibung, ``data_json``, Anhänge, zugeordnete
    Ereignisse) einer anderen Person zuschlagen, ohne dass eine der
    bestehenden Grenzen (Facility-Scope, Widerrufsstatus) das bemerkt.
    Strikt: JEDER ``client``-Kwarg fliegt raus, auch der unveränderte Wert —
    die Mass-Assignment-Prüfung darf nicht vom Diff abhängen.
    """
    from core.services.client.restriction import assert_processing_allowed

    # Art.-18-Gate (Refs #2045) VOR dem Versions-Guard: die Rechtslage gilt
    # unabhaengig davon, ob das Formular aktuell war (Muster ``update_client``).
    assert_processing_allowed(case.client_id, lock=True)
    check_version_conflict(case, expected_updated_at)
    # Allowlist (Refs #734, #1786): Verhindert Mass-Assignment durch
    # versehentlich durchgereichte Felder. Läuft ZUERST — die
    # Mass-Assignment-Prüfung darf nicht vom Diff abhängen (Muster
    # ``update_client``, P4b).
    allowed = {"title", "description", "lead_user", "document_type", "data_json"}
    check_update_allowlist(fields, allowed)

    # M2 P5 (Refs #1058): Dynamikdaten gegen den *effektiven* Typ pruefen —
    # also gegen den neu uebergebenen, sonst gegen den bestehenden. Wird nur
    # der Typ gewechselt, laeuft das bestehende ``data_json`` erneut durch die
    # Whitelist des NEUEN Typs: Slugs des alten Typs fallen weg (E11-Merge-
    # Policy), statt als unsichtbarer Ballast im JSONB liegenzubleiben.
    if "document_type" in fields or "data_json" in fields:
        # ``.get(key, default)`` liefert bei vorhandenem Key auch ein explizites
        # ``None`` zurueck — ein bewusstes Leeren des Typs bleibt also erhalten.
        effective_type = fields.get("document_type", case.document_type)
        raw_data = fields.get("data_json", case.data_json)
        fields["data_json"] = clean_dynamic_data(
            case.facility,
            effective_type,
            raw_data,
            entity_type=DocumentType.EntityType.CASE,
            entity_type_error=_CASE_DOCUMENT_TYPE_ERROR,
        )

    # Refs #1613 (M2 P8 nachgezogen): Diff gegen den DB-Stand, NICHT gegen die
    # uebergebene Instanz. Seit P8 baut ``CaseUpdateView`` das Formular mit
    # ``CaseForm(instance=case)`` (noetig fuer die Inaktiv-Ausnahme des
    # ``document_type``-Querysets) — ``_post_clean()`` hat die Instanz dann
    # bereits mutiert, ein ``getattr(case, key) != value`` saehe hier
    # ``neu == neu`` und schriebe nie ``CASE_UPDATE`` (Threat-Model TB6,
    # Repudiation). Gleiches Muster wie ``update_client``/``update_workitem``.
    # Nur Feldnamen ins Audit, keine PII-Werte.
    # Nur die uebergebenen Spalten schreiben (Refs #1789-Folge): ein voller
    # Save wuerde die parallel laufende Redaktions-Kaskade (Queryset-Update
    # ohne ``updated_at``-Bump) mit dem stalen Klartext ueberschreiben.
    changed_fields = apply_allowlisted_update(case, fields, allowed_fields=allowed)

    if changed_fields:
        audit_event(
            AuditLog.Action.CASE_UPDATE,
            user=user,
            facility=case.facility,
            target_obj=case,
            detail={"changed_fields": changed_fields},
        )
    return case


@transaction.atomic
def close_case(case, user):
    """Close a case + AuditLog + Activity.

    Art.-18-Gate (Refs #2045): Fall-Operationen sind fachliche Nutzung der
    Personendaten, nicht blosse Speicherung. Das Gate liegt seit Refs #2288 im
    Zustandsvertrag (:mod:`core.services.case.state_contract`) — zusammen mit
    der Personensperre, die es ohnehin nimmt.

    Refs #2288: Geschrieben wird die FRISCH unter Sperre gelesene Zeile, und
    von ihr nur der Status samt Zeitspalte. Die uebergebene Instanz stammt aus
    einem Lesevorgang von vor dem Klick; ein voller ``save()`` auf ihr schrieb
    Titel, Beschreibung, ``data_json`` und die Loeschmerkmale einer
    zwischenzeitlichen Tilgung zurueck. Sie bekommt am Ende den neuen Stand
    und bleibt das zurueckgegebene Objekt (Muster ``anonymize_case_now``).
    """
    locked = lock_case_for_status_change(case)
    locked.status = Case.Status.CLOSED
    locked.closed_at = timezone.now()
    locked.save(update_fields=["status", "closed_at", "updated_at"])
    audit_event(
        AuditLog.Action.CASE_CLOSE,
        user=user,
        facility=locked.facility,
        target_obj=locked,
    )
    log_activity(
        facility=locked.facility,
        actor=user,
        verb=Activity.Verb.UPDATED,
        target=locked,
        action=Activity.Action.CASE_CLOSED,
    )
    case.refresh_from_db()
    return case


@transaction.atomic
def reopen_case(case, user):
    """Reopen a previously closed case + AuditLog + Activity.

    Spiegel von :func:`close_case` — einschliesslich des Zustandsvertrags
    (Refs #2288). Ein Statuswechsel ist kein Wiederherstellen: eine Akte im
    Papierkorb kommt ueber :func:`~core.services.case.deletion.restore_case`
    zurueck, mit eigener Pruefung und eigener Audit-Zeile, nicht ueber das
    Nullen von ``is_deleted`` durch einen vollen Save.
    """
    locked = lock_case_for_status_change(case)
    locked.status = Case.Status.OPEN
    locked.closed_at = None
    locked.save(update_fields=["status", "closed_at", "updated_at"])
    audit_event(
        AuditLog.Action.CASE_REOPEN,
        user=user,
        facility=locked.facility,
        target_obj=locked,
    )
    log_activity(
        facility=locked.facility,
        actor=user,
        verb=Activity.Verb.REOPENED,
        target=locked,
        action=Activity.Action.CASE_REOPENED,
    )
    case.refresh_from_db()
    return case


@transaction.atomic
def assign_event_to_case(case, event, user):
    """Assign an event to a case + AuditLog. Both must belong to the same facility.

    Additional invariants:
    - Anonymous events may only be attached to cases without a client.
    - Clientel of the event must match the case's clientel when both are set.
    - The user must be permitted to see the event (service-layer fallback for
      callers that bypass the view-level :func:`get_visible_event_or_404`).

    Refs #1959: ``user`` trug bis hierher den Vermerk „reserved for future
    audit trail" — die Luecke ist geschlossen. Die Zuordnung entscheidet, in
    welchem Fallzusammenhang ein Kontakt steht; ohne Eintrag war sie
    nachtraeglich niemandem zuzuordnen (Threat-Model TB6, Repudiation).

    ``target_obj`` ist das **Ereignis**: die Zeile beschreibt, was mit dem
    Ereignis geschah, und bleibt so auch nach einem spaeteren Loesen
    zuordenbar. Ein erneutes Zuordnen desselben Falls ist ein No-Op und
    schreibt keine zweite Zeile.
    """
    from core.services.client.restriction import assert_processing_allowed_for

    # Art.-18-Gate (Refs #2045): geprueft werden BEIDE Personenbezuege — der
    # des Falls und der des Ereignisses. Sie koennen auseinanderfallen (etwa
    # ein anonymes Ereignis an einem personenlosen Fall), und eine
    # Einschraenkung auf einer der beiden Seiten verbietet die Zuordnung.
    #
    # BEIDE Zeilen in EINER Sperre, PK-geordnet (Refs #2268): die erste
    # Fassung sperrte erst die Fallperson, dann die Ereignisperson — also in
    # der fachlichen statt in der PK-Reihenfolge. Fuer zwei Personen A < B
    # hielt Fall(B)/Ereignis(A) damit B und verlangte A, waehrend ein
    # gleichzeitiges Zusammenfuehren A->B A hielt und B verlangte: ein
    # Sperrzyklus, den PostgreSQL mit ``40P01`` aufloest. Die fachlichen
    # Pruefungen — Einrichtung, Sichtbarkeit, „unterschiedliche Personen" —
    # folgen unveraendert NACH dem Sperren.
    assert_processing_allowed_for([case.client_id, event.client_id], lock=True)
    if event.facility_id != case.facility_id:
        raise ValueError(_("Ereignis gehört nicht zur selben Einrichtung wie der Fall."))
    if user is not None and not user_can_see_event(user, event):
        raise ValidationError(_("Ereignis ist für diese Rolle nicht sichtbar."))
    if case.client_id is not None:
        if event.is_anonymous or event.client_id is None:
            raise ValidationError(
                _("Anonyme Ereignisse können nicht an einen Fall mit zugeordneter Person gehängt werden.")
            )
        if event.client_id != case.client_id:
            raise ValidationError(_("Person des Ereignisses passt nicht zur Person des Falls."))
    previous_case_id = event.case_id
    if previous_case_id == case.pk:
        return event
    event.case = case
    event.save()
    audit_event(
        AuditLog.Action.CASE_EVENT_ASSIGNED,
        user=user,
        facility=case.facility,
        target_obj=event,
        # Nur UUIDs ins detail (Refs #1751): der Falltitel ist Freitext, den die
        # Anonymisierung ueberschreibt — im append-only Log ueberlebte er sie.
        # ``previous_case_id`` steht nur beim Umhaengen: das loest das Ereignis
        # aus dem alten Fall, ohne dass eine CASE_EVENT_REMOVED-Zeile
        # entstuende — ohne den Schluessel bliebe der Abgang unbelegt. Beide
        # Zweige sind literale Dicts, damit der statische Schluessel-Guard
        # (``TestAuditDetailKeyInventory``, Refs #1944) sie sieht.
        detail={"case_id": str(case.pk), "previous_case_id": str(previous_case_id)}
        if previous_case_id is not None
        else {"case_id": str(case.pk)},
    )
    return event


@transaction.atomic
def remove_event_from_case(event, user):
    """Remove an event from its case + AuditLog.

    Refs #1959: ``user`` ist neu — der Service hatte ihn gar nicht, eine
    Audit-Zeile ohne Akteur waere als Nachweis wertlos. Ein Ereignis ohne Fall
    zu loesen ist ein No-Op und schreibt nichts. ``case_id`` benennt den Fall,
    aus dem das Ereignis geloest wurde; ohne ihn waere nach dem Schreiben nicht
    mehr feststellbar, welche Akte betroffen war.
    """
    from core.services.client.restriction import assert_processing_allowed

    # Art.-18-Gate (Refs #2045): das Loesen aendert den Fallzusammenhang eines
    # Kontakts — inhaltliche Aenderung, nicht Speicherung.
    assert_processing_allowed(event.client_id, lock=True)
    previous_case = event.case
    if previous_case is None:
        return event
    event.case = None
    event.save()
    audit_event(
        AuditLog.Action.CASE_EVENT_REMOVED,
        user=user,
        facility=previous_case.facility,
        target_obj=event,
        detail={"case_id": str(previous_case.pk)},
    )
    return event
