"""Service layer for Case CRUD and event assignment."""

import json
import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.translation import gettext_lazy as _

from core.models import Activity, AuditLog, Case, DocumentType
from core.services.audit import apply_allowlisted_update, audit_event, check_update_allowlist
from core.services.case.state_contract import (
    EVENT_WRITE_GONE_MESSAGE,
    _raise_if_clients_revoked,
    lock_case_for_status_change,
    lock_case_for_write,
    lock_event_for_case_link,
)
from core.services.compliance import user_can_see_event
from core.services.dashboard import log_activity
from core.services.dynamic_data import clean_dynamic_data
from core.services.dynamic_protection import lock_dynamic_protection
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
    # Einrichtungsgrenze der handelnden Person vor jeder Sperre (Muster
    # :func:`assign_event_to_case`); fail-closed ohne Einrichtung.
    if getattr(user, "facility_id", None) != facility.pk:
        raise ValidationError(_("Fall gehört zu einer anderen Einrichtung."))
    from core.services.client.restriction import assert_processing_allowed

    # Art.-18-Gate (Refs #2045): ein neuer Fall ist neue inhaltliche
    # Verarbeitung, nicht Speicherung des Bestands.
    lock_dynamic_protection(facility.pk)
    assert_processing_allowed(client, lock=True)
    # Widerruf der Person auf der eben gesperrten Zeile: die Instanz stammt
    # aus einem Lesevorgang vor der Sperre. Eine Person im Papierkorb (auch
    # die Quelle einer Zusammenfuehrung) oder eine anonymisierte Person
    # bekommt keinen neuen Fall. Gelesen, nicht erneut gesperrt — eine zweite
    # Sperrform daneben waere eine zweite Ordnung.
    _raise_if_clients_revoked([client.pk], facility.pk)
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


# Spalten, die das Bearbeiten-Formular zeigt oder mitschickt (Refs #2359) —
# ``attname``-Form, damit Instanz und ``values()`` dieselben Werte liefern.
# ``status``/``closed_at`` stehen bewusst NICHT darin: sie aendern nur
# :func:`close_case`/:func:`reopen_case`, und ein paralleler Statuswechsel ist
# kein Konflikt mit dem Formular.
CASE_FORM_STATE_FIELDS = (
    "title",
    "description",
    "lead_user_id",
    "document_type_id",
    "data_json",
)


def _case_form_state_token_from_values(pk, values):
    payload = json.dumps(
        {field: values.get(field) for field in CASE_FORM_STATE_FIELDS},
        sort_keys=True,
        default=str,
    )
    return salted_hmac("core.case.form_state", f"{pk}|{payload}", algorithm="sha256").hexdigest()


def case_form_state_token(case):
    """Fingerabdruck der Formularfelder zum Renderzeitpunkt (Refs #2359).

    Das Bearbeiten-Formular traegt ihn versteckt mit (Muster
    ``workitem_form_state_token``). Ein HMAC statt der Klartextwerte: das
    Formular soll keinen zweiten, manipulierbaren Satz Feldwerte mitschicken,
    und der Wert verraet nichts ueber den Inhalt. Wer ihn faelscht, erreicht
    hoechstens, was ein Neuladen mit anschliessendem Speichern ohnehin
    erlaubt.
    """
    values = {field: getattr(case, field) for field in CASE_FORM_STATE_FIELDS}
    return _case_form_state_token_from_values(case.pk, values)


def _case_form_state_unchanged(case, expected_form_state):
    """``True``, wenn seit dem Rendern kein Formularfeld geaendert wurde.

    Liest die Zeile unter Row-Lock (``check_version_conflict`` haelt ihn in
    derselben Transaktion bereits; ``select_for_update`` ist dann ein No-op).
    """
    if not expected_form_state or not isinstance(expected_form_state, str):
        return False
    current = Case._base_manager.select_for_update().filter(pk=case.pk).values(*CASE_FORM_STATE_FIELDS).first()
    if current is None:
        return False
    return constant_time_compare(_case_form_state_token_from_values(case.pk, current), expected_form_state)


@transaction.atomic
def update_case(case, user, *, expected_updated_at=None, expected_form_state=None, **fields):
    """Update mutable fields on a case (title, description, lead_user,
    document_type, data_json).

    Writes an AuditLog entry with the names of changed fields (no PII values).

    ``expected_updated_at`` enables optimistic locking (Refs #531) — when the
    DB-side ``updated_at`` differs, a ``ValidationError`` is raised.

    ``expected_form_state`` (Refs #2359): Fingerabdruck der Formularfelder zum
    Renderzeitpunkt (:func:`case_form_state_token`). Weicht nur
    ``updated_at`` ab, die Formularfelder in der Datenbank aber nicht — weil
    parallel nur geschlossen oder wiedereroeffnet wurde —, ist das kein
    Konflikt mit dem Formular: die Eingabe wird gespeichert, der parallele
    Status bleibt. Jede parallele Aenderung eines Formularfelds bleibt ein
    Konflikt. Ohne Fingerabdruck (Default) gilt der Versionsvertrag
    unveraendert.

    Die Rettung gilt nur fuer eine lebende Akte: VOR der Versionspruefung
    laeuft der Zustandsvertrag (:func:`~core.services.case.state_contract.lock_case_for_write`)
    auf der frisch gesperrten Zeile. Eine Akte im Papierkorb, eine getilgte
    Akte oder die Akte einer Person im Papierkorb lehnt er mit seiner Meldung
    ab — der Papierkorb aendert keine Formularspalte, nach „schliessen, dann
    in den Papierkorb" passte der Fingerabdruck sonst weiterhin.

    Ursprungs-Pinning (Refs #1786): ``client`` ist **kein** Update-Feld mehr.
    Die Personenzuordnung eines Falls entsteht beim Anlegen und ist danach
    unveränderlich — ein nachträglicher Wechsel würde den kompletten
    Fallverlauf (Titel, Beschreibung, ``data_json``, Anhänge, zugeordnete
    Ereignisse) einer anderen Person zuschlagen, ohne dass eine der
    bestehenden Grenzen (Facility-Scope, Widerrufsstatus) das bemerkt.
    Strikt: JEDER ``client``-Kwarg fliegt raus, auch der unveränderte Wert —
    die Mass-Assignment-Prüfung darf nicht vom Diff abhängen.
    """
    # Einrichtungsgrenze der handelnden Person zuerst (Muster
    # :func:`assign_event_to_case`): die Einrichtung einer Akte ist
    # unveraenderlich, die Pruefung braucht keinen frischen Stand und sperrt
    # nichts ueber die Mandantengrenze hinweg. Fail-closed: ohne Einrichtung
    # keine Bearbeitung.
    if getattr(user, "facility_id", None) != case.facility_id:
        raise ValidationError(_("Fall gehört zu einer anderen Einrichtung."))
    # Zustandsvertrag VOR dem Versions-Guard: Sperrfolge Person -> Fall, darin
    # das Art.-18-Gate (Refs #2045 — die Rechtslage gilt unabhaengig davon, ob
    # das Formular aktuell war, Muster ``update_client``), der Widerruf der
    # Person und der Loesch-/Tilgungszustand des Falls auf der frisch
    # gesperrten Zeile. Die Instanz stammt aus einem Lesevorgang vor der
    # Sperre; ohne diese Pruefung schriebe die Fingerabdruck-Rettung unten
    # neuen Inhalt in eine Akte, die parallel geschlossen und in den
    # Papierkorb gelegt wurde (der Papierkorb hebt ``updated_at`` nicht an und
    # aendert keine Formularspalte).
    lock_dynamic_protection(case.facility_id)
    locked = lock_case_for_write(case)
    try:
        check_version_conflict(case, expected_updated_at)
    except ValidationError as exc:
        # Nur der echte Versions-Mismatch darf per Fingerabdruck aufgeloest
        # werden — ein ungueltiger Token bleibt ein Fehler.
        if exc.code != "version_conflict" or not _case_form_state_unchanged(case, expected_form_state):
            raise
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
            old_data=locked.data_json,
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

    Davor die Einrichtungsgrenze der handelnden Person — vor jeder Sperre,
    fail-closed (Muster :func:`assign_event_to_case`).
    """
    if getattr(user, "facility_id", None) != case.facility_id:
        raise ValidationError(_("Fall gehört zu einer anderen Einrichtung."))
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

    Davor die Einrichtungsgrenze der handelnden Person — vor jeder Sperre,
    fail-closed (Muster :func:`assign_event_to_case`).
    """
    if getattr(user, "facility_id", None) != case.facility_id:
        raise ValidationError(_("Fall gehört zu einer anderen Einrichtung."))
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
    # Einrichtung zuerst: sie ist an Fall und Ereignis unveraenderlich, die
    # Pruefung braucht also keinen frischen Stand — und kein Sperren ueber die
    # Mandantengrenze hinweg.
    if event.facility_id != case.facility_id:
        raise ValueError(_("Ereignis gehört nicht zur selben Einrichtung wie der Fall."))
    # Refs #2364: und die aufrufende Person gehoert zu genau dieser
    # Einrichtung. Die Views scopen Fall und Ereignis bereits, in Produktion
    # traegt RLS mit — der Service ist aber die Single Source of Truth und
    # zieht die Grenze selbst, wie die Vier-Augen-Services (Refs #2338).
    # Fail-closed: ohne Einrichtung keine Zuordnung. Vor jeder Sperre, damit
    # nichts ueber die Mandantengrenze hinweg gesperrt wird.
    if getattr(user, "facility_id", None) != case.facility_id:
        raise ValidationError(_("Fall gehört zu einer anderen Einrichtung."))
    # Art.-18-Gate (Refs #2045) und Zustandsvertrag (Refs #2329) in EINEM
    # Schritt: geprueft werden ALLE Personenbezuege — der des Ereignisses und
    # der beider Faelle —, in EINER PK-geordneten Sperre (Refs #2268: zwei
    # Einzelsperren in fachlicher Reihenfolge waren ein Sperrzyklus mit einem
    # gleichzeitigen Zusammenfuehren). Danach die Faelle, zuletzt das Ereignis
    # selbst, jeweils frisch gelesen (:func:`lock_event_for_case_link`).
    #
    # Entschieden und geschrieben wird auf der FRISCHEN Zeile: die
    # uebergebene Instanz stammt aus einem Lesevorgang von vor dem Klick. Ein
    # voller ``save()`` auf ihr schrieb ``data_json`` und ``search_text`` von
    # damals zurueck — ueber eine zwischenzeitliche Tilgung, einen
    # Papierkorb-Schritt oder eine regulaere Bearbeitung hinweg.
    # Die Pruefungen „anonymes Ereignis" und „andere Person" laufen im
    # Vertrag selbst, direkt nach den Personensperren.
    locked, target, _previous = lock_event_for_case_link(event, case)
    # Refs #2533: apply the creation boundary to the freshly locked case.
    from core.services.events.case_assignment import assignable_cases

    if user is not None and not user_can_see_event(user, locked):
        raise ValidationError(_("Ereignis ist für diese Rolle nicht sichtbar."))
    if not assignable_cases(target.facility, user).filter(pk=target.pk).exists():
        raise ValidationError(_("Der Fall ist nicht mehr verfügbar oder nicht zugänglich."))
    previous_case_id = locked.case_id
    if previous_case_id == target.pk:
        event.refresh_from_db()
        return event
    locked.case = target
    # Nur die Zuordnung (und ihr Zeitstempel): die uebrigen Spalten gehoeren
    # der Bearbeitung und der Tilgung, nicht der Zuordnung.
    locked.save(update_fields=["case", "updated_at"])
    audit_event(
        AuditLog.Action.CASE_EVENT_ASSIGNED,
        user=user,
        facility=target.facility,
        target_obj=locked,
        # Nur UUIDs ins detail (Refs #1751): der Falltitel ist Freitext, den die
        # Anonymisierung ueberschreibt — im append-only Log ueberlebte er sie.
        # ``previous_case_id`` steht nur beim Umhaengen: das loest das Ereignis
        # aus dem alten Fall, ohne dass eine CASE_EVENT_REMOVED-Zeile
        # entstuende — ohne den Schluessel bliebe der Abgang unbelegt. Beide
        # Zweige sind literale Dicts, damit der statische Schluessel-Guard
        # (``TestAuditDetailKeyInventory``, Refs #1944) sie sieht.
        detail={"case_id": str(target.pk), "previous_case_id": str(previous_case_id)}
        if previous_case_id is not None
        else {"case_id": str(target.pk)},
    )
    event.refresh_from_db()
    return event


class EventNotOnExpectedCaseError(ValidationError):
    """Das Ereignis haengt (unter der Sperre gelesen) nicht am erwarteten Fall.

    Refs #2364: eigene Klasse, damit die View daraus dieselbe 404 macht wie
    bei ihrer ungesperrten Vorpruefung — alle uebrigen Absagen des
    Zustandsvertrags bleiben 409.
    """


@transaction.atomic
def remove_event_from_case(event, user, *, expected_case=None):
    """Remove an event from its case + AuditLog.

    ``expected_case`` (Refs #2364) bindet das Loesen an einen bestimmten Fall
    — die View reicht den Fall aus der URL durch. Verglichen wird mit der
    FRISCH GESPERRTEN Zeile: haengt das Ereignis inzwischen an einem anderen
    oder an keinem Fall, bricht der Service vor jedem Schreiben mit
    :class:`EventNotOnExpectedCaseError` ab. Die ungesperrte Pruefung der View
    allein liess ein Umhaengen zwischen Pruefung und Sperre durch — der POST
    fuer Fall A loeste das Ereignis dann aus Fall B. Ohne ``expected_case``
    bleibt das bisherige Verhalten (Loesen vom jeweils zugeordneten Fall).

    Refs #1959: ``user`` ist neu — der Service hatte ihn gar nicht, eine
    Audit-Zeile ohne Akteur waere als Nachweis wertlos. Ein Ereignis ohne Fall
    zu loesen ist ein No-Op und schreibt nichts. ``case_id`` benennt den Fall,
    aus dem das Ereignis geloest wurde; ohne ihn waere nach dem Schreiben nicht
    mehr feststellbar, welche Akte betroffen war.
    """
    # Art.-18-Gate (Refs #2045) und Zustandsvertrag (Refs #2329): das Loesen
    # aendert den Fallzusammenhang eines Kontakts — inhaltliche Aenderung,
    # nicht Speicherung — und die Akte, aus der es geloest wird. Sperrfolge,
    # frische Zeile und spaltengenaues Schreiben wie in
    # :func:`assign_event_to_case`.
    #
    # Refs #2364: Einrichtungsgrenze der aufrufenden Person zuerst — die
    # Einrichtung des Ereignisses ist unveraenderlich, die Pruefung braucht
    # also keinen frischen Stand und sperrt nichts ueber die Grenze hinweg.
    if getattr(user, "facility_id", None) != event.facility_id:
        raise ValidationError(_("Ereignis gehört zu einer anderen Einrichtung."))
    locked, _target, previous_case = lock_event_for_case_link(event)
    if expected_case is not None and locked.case_id != expected_case.pk:
        raise EventNotOnExpectedCaseError(EVENT_WRITE_GONE_MESSAGE)
    if previous_case is None:
        event.refresh_from_db()
        return event
    locked.case = None
    locked.save(update_fields=["case", "updated_at"])
    audit_event(
        AuditLog.Action.CASE_EVENT_REMOVED,
        user=user,
        facility=previous_case.facility,
        target_obj=locked,
        detail={"case_id": str(previous_case.pk)},
    )
    event.refresh_from_db()
    return event
