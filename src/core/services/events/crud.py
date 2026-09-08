"""Event-CRUD-Pfade (Refs #777).

``create_event``, ``update_event``, ``soft_delete_event`` und die
Event-spezifische Update-Payload-Ergaenzung ``merge_update_payload``.

Aufgeteilt aus dem alten ``services/event.py`` (#777).

M24/AP2 (Refs #1738, ADR-039): Die Attachment-Schreiber
``finalize_staged_files`` (Refs #1345) und ``apply_attachment_changes`` sind
nach :mod:`core.services.attachments.crud` umgezogen — sie waren nie
event-spezifisch, seit ADR-039 haengen Anhaenge auch an Person und Fall. Hier
bleiben nur duenne, delegierende Aliasse mit dem alten ``event``-Parameter,
damit ``core.services.events``-Importe und die Event-Views unveraendert
gueltig bleiben.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import Activity, AuditLog, Client, DocumentType, Event, EventHistory, FieldTemplate
from core.services.attachments import crud as attachments_crud
from core.services.audit import audit_event, changed_data_json_slugs
from core.services.compliance import user_can_see_document_type
from core.services.dashboard import log_activity
from core.services.dynamic_data import check_document_type
from core.services.events.fields import (
    _snapshot_field_metadata,
    _validate_data_json,
    build_field_template_lookup,
    build_redacted_delete_history,
    compute_event_search_text,
    stage_index,
)
from core.services.file_vault import delete_attachments_for


def finalize_staged_files(event, user, staged_by_slug, document_type):
    """Alt-Einstiegspunkt fuer den Event-Pfad — delegiert owner-generisch.

    M24/AP2 (Refs #1738): Die Implementierung ist nach
    :func:`core.services.attachments.crud.finalize_staged_files` umgezogen
    (owner-generisch: Ereignis, Person, Fall). Dieser duenne Alias haelt den
    Bestands-Importpfad (``core.services.events``) und die Event-Views
    unveraendert gueltig.
    """
    return attachments_crud.finalize_staged_files(event, user, staged_by_slug, document_type)


def apply_attachment_changes(event, user, request_post, request_files, file_fields, document_type, staged_by_key=None):
    """Alt-Einstiegspunkt fuer den Event-Pfad — delegiert owner-generisch.

    M24/AP2 (Refs #1738): Implementierung in
    :func:`core.services.attachments.crud.apply_attachment_changes`.
    Refs #1956: ``staged_by_key`` (Scan/Verschluesselung vor der Transaktion)
    wird durchgereicht.
    """
    return attachments_crud.apply_attachment_changes(
        event, user, request_post, request_files, file_fields, document_type, staged_by_key
    )


def _validate_case_assignment(facility, case, client, is_anonymous):
    """Refs #1160: Fall-Zuordnung pruefen (Facility-Scope, Person-Match,
    Anonymitaets-Ausschluss). Wirkungs-identisch zum frueheren Inline-Block."""
    if case is None:
        return
    if case.facility_id != facility.pk:
        raise ValidationError(_("Fall gehört nicht zur selben Einrichtung wie das Ereignis."))
    if case.client_id is not None and client is not None and case.client_id != client.pk:
        raise ValidationError(_("Person des Ereignisses passt nicht zur Person des Falls."))
    if case.client_id is not None and (client is None or is_anonymous):
        raise ValidationError(_("Anonyme Ereignisse dürfen nicht an Fälle mit zugeordneter Person gehängt werden."))


def _validate_contact_stage(document_type, client, is_anonymous):
    """Refs #1160: Mindest-Kontaktstufen-Regeln des DocumentType pruefen.

    Wirkungs-identisch zum frueheren Inline-Block — die Reihenfolge der
    Checks (anonym -> fehlende Person -> Stufe zu niedrig) bleibt erhalten.
    """
    if not document_type.min_contact_stage:
        return
    if is_anonymous:
        raise ValidationError(
            _(
                "Anonyme Kontakte sind bei diesem Dokumentationstyp nicht erlaubt, "
                "da eine Mindest-Kontaktstufe vorausgesetzt wird."
            )
        )
    if client is None:
        raise ValidationError(
            _(
                "Für diesen Dokumentationstyp muss eine Person ausgewählt werden, "
                "da eine Mindest-Kontaktstufe vorausgesetzt wird."
            )
        )
    required = stage_index(document_type.min_contact_stage)
    actual = stage_index(client.contact_stage)
    if actual < required:
        raise ValidationError(
            _(
                "Person muss mindestens die Kontaktstufe "
                "'%(required_stage)s' "
                "haben, aktuelle Stufe ist "
                "'%(actual_stage)s'."
            )
            % {
                # Refs #1805: beide Seiten als Anzeige-Label. Vorher stand
                # hier der Rohwert ("... Kontaktstufe 'qualified' ...") neben dem
                # deutschen Label der Ist-Stufe — englischer Enum-Wert und
                # deutsches Label in einem Satz.
                "required_stage": Client.ContactStage(document_type.min_contact_stage).label,
                "actual_stage": client.get_contact_stage_display(),
            }
        )


@transaction.atomic
def create_event(
    facility,
    user,
    document_type,
    occurred_at,
    data_json,
    client=None,
    is_anonymous=False,
    case=None,
    idempotency_key=None,
    idempotency_payload_hash=None,
    created_via=Event.CreatedVia.STANDARD,
):
    """Create an event + EventHistory(CREATE).

    Art.-18-Gate (Refs #2045): Ein neues Ereignis an einer eingeschraenkten
    Person ist neue inhaltliche Verarbeitung — Art. 18 Abs. 2 erlaubt nur noch
    die Speicherung des Bestands. Der Guard sitzt HIER und nicht (nur) im
    Formular, weil ``create_event`` auch der CSV-Import und der
    Offline-Replay durchlaufen; das Formular-Gate
    (``resolve_writable_client``) ist die freundliche Vorabmeldung, dieses die
    Grenze.

    ``idempotency_key`` und ``idempotency_payload_hash`` (Review R5/R6, #1909):
    Der normalisierte Schlüssel und der SHA-256-Fingerprint des Requests werden
    als persistenter DB-Backstop mitgeschrieben. Constraint und Hash fangen
    Duplikate sowie Payload-Kollisionen auch bei Cache-Ausfall ab.
    """
    from core.services.client.restriction import assert_processing_allowed

    assert_processing_allowed(client, lock=True)
    # Refs #1058 (Review-Haertung, Defense-in-Depth): analog zum Guard in
    # ``create_client``/``create_case`` (core.services.client.main /
    # core.services.case.cases). Alle heutigen Aufrufer (Views/Offline-Replay)
    # filtern bereits auf entity_type=event; dieser Guard schuetzt kuenftige
    # Direkt-Aufrufer, die den View-Filter umgehen wuerden.
    document_type = check_document_type(
        facility,
        document_type,
        entity_type=DocumentType.EntityType.EVENT,
        entity_type_error=_("Der Dokumentationstyp ist nicht für Ereignisse vorgesehen."),
        facility_error=ValueError("DocumentType gehört nicht zur Facility"),
    )
    if client and client.facility_id != facility.pk:
        raise ValueError("Client gehört nicht zur Facility")
    _validate_case_assignment(facility, case, client, is_anonymous)

    if user is not None and not user_can_see_document_type(user, document_type):
        raise PermissionDenied(_("Diese Dokumentation darf von Ihrer Rolle nicht erstellt werden."))

    if client is None and not is_anonymous and not document_type.min_contact_stage:
        is_anonymous = True

    _validate_contact_stage(document_type, client, is_anonymous)

    data_json = _validate_data_json(document_type, data_json)

    event = Event(
        facility=facility,
        client=client,
        document_type=document_type,
        occurred_at=occurred_at,
        data_json=data_json,
        search_text=compute_event_search_text(data_json, document_type),
        is_anonymous=is_anonymous,
        created_by=user,
        case=case,
        idempotency_key=idempotency_key,
        idempotency_payload_hash=idempotency_payload_hash,
        created_via=created_via,
    )
    event.save()
    EventHistory.objects.create(
        event=event,
        changed_by=user,
        action=EventHistory.Action.CREATE,
        data_after=data_json,
        field_metadata=_snapshot_field_metadata(document_type),
    )
    # Refs #1917: Hausverbots-Revision in DERSELBEN Transaktion bumpen —
    # funktionslokaler Import (Zyklus: services.system importiert breit).
    from core.services.system.ban_revision import bump_ban_revision_for_event_change

    bump_ban_revision_for_event_change(facility, document_type, {}, data_json)
    log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=event,
        action=Activity.Action.EVENT_CREATED,
    )
    audit_event(
        AuditLog.Action.EVENT_CREATE,
        user=user,
        facility=facility,
        target_obj=event,
        detail={"document_type": document_type.name, "is_anonymous": is_anonymous},
    )
    return event


@transaction.atomic
def update_event(event, user, data_json, expected_updated_at=None, require_version_token=False, **kwargs):
    """Update an event + EventHistory(UPDATE).

    Refs #734: nutzt zentrale ``check_version_conflict`` statt eigenem
    ``str(updated_at)``-Vergleich.

    ``require_version_token`` (Refs #1338): wird 1:1 an
    ``check_version_conflict`` durchgereicht. ``EventUpdateView`` setzt es
    auf das Ergebnis von ``_wants_json_response`` — JSON-/Offline-Replay-
    Clients müssen dadurch einen Token mitschicken, während der klassische
    HTML-Formular-Pfad beim Default ``False`` bleibt (kein Verhaltensbruch).

    Allowlist (Refs #1789): ``**kwargs`` bleibt in der Signatur, die Menge der
    erlaubten Felder ist aber **leer**. ``update_event`` war die letzte der
    vier Update-Services ohne Mass-Assignment-Prüfung (Restpunkt aus #1786):
    jedes durchgereichte Feld landete blind im ``setattr``. Kein heutiger
    Aufrufer nutzt Kwargs — ``EventUpdateView.post`` übergibt ausschließlich
    ``data_json``. Ein künftiges Update-Feld muss deshalb bewusst in
    ``allowed`` eingetragen werden, statt still durchzurutschen.
    """
    from core.services.client.restriction import assert_processing_allowed
    from core.services.security import check_version_conflict

    # Art.-18-Gate (Refs #2045): Das Bearbeiten eines bestehenden Ereignisses
    # ist inhaltliche Aenderung. Der Guard liegt im Service und nicht nur in
    # ``EventUpdateView.dispatch``, weil derselbe Weg den Offline-Replay und
    # die Anhang-Aenderungen traegt. VOR dem Versions-Guard: die Rechtslage
    # gilt unabhaengig davon, ob das Formular aktuell war.
    assert_processing_allowed(event.client_id, lock=True)
    check_version_conflict(event, expected_updated_at, require_token=require_version_token)
    # Läuft ZUERST (Muster ``update_case``/``update_workitem``): die
    # Mass-Assignment-Prüfung darf weder vom Diff noch von der
    # ``data_json``-Validierung abhängen — ein abgewiesener Kwarg darf
    # keinen Teil-Write hinterlassen.
    allowed: set[str] = set()
    for key in kwargs:
        if key not in allowed:
            raise ValueError(f"Feld '{key}' darf nicht aktualisiert werden.")

    data_json = _validate_data_json(event.document_type, data_json)
    # Refs #1958: Der Slug-Diff MUSS vor dem ``save()`` unten stehen — danach
    # traegt die DB-Zeile bereits den neuen Stand, und ``changed_fields`` bliebe
    # fuer immer leer (Threat-Model-Befund „Bearbeitung ohne Audit-Spur").
    # Gelesen wird der DB-Stand unter ``select_for_update`` (Default
    # ``lock=True``), nicht ``event.data_json``: zwischen Diff und Save passt
    # damit kein fremder Schreibvorgang, und Nicht-Formular-Aufrufer
    # (Offline-Replay) koennen die Instanz bereits mit der neuen Payload
    # anliefern.
    changed_slugs = changed_data_json_slugs(event, data_json)
    data_before = event.data_json.copy() if event.data_json else {}
    event.data_json = data_json
    event.search_text = compute_event_search_text(data_json, event.document_type)
    # Solange ``allowed`` leer ist, laeuft diese Schleife nie — sie bleibt als
    # Write-Pfad stehen, damit das Eintragen eines Feldes in ``allowed`` eine
    # Ein-Zeilen-Aenderung ist (Symmetrie zu den drei Geschwister-Services).
    for k, v in kwargs.items():  # pragma: no cover
        setattr(event, k, v)
    event.save()
    EventHistory.objects.create(
        event=event,
        changed_by=user,
        action=EventHistory.Action.UPDATE,
        data_before=data_before,
        data_after=data_json,
        field_metadata=_snapshot_field_metadata(event.document_type),
    )
    # Refs #1917: Banner-relevantes Delta (aktiv/bis → Stratum 0, Grund →
    # Doc-Type-Stratum) in derselben Transaktion signalisieren.
    from core.services.system.ban_revision import bump_ban_revision_for_event_change

    bump_ban_revision_for_event_change(event.facility, event.document_type, data_before, data_json)
    # Refs #1958: Audit nur bei echtem Delta (Muster ``update_client`` /
    # ``update_case``) — ein Formular-Submit ohne Aenderung ist kein
    # rechenschaftspflichtiger Vorgang und wuerde das append-only Log mit
    # Leerzeilen fluten. Die ``EventHistory``-Zeile oben faellt dagegen
    # UNBEDINGT an: sie ist der fachliche Bearbeitungsverlauf (wer hat wann
    # gespeichert) und traegt mit ``data_before``/``data_after`` ohnehin den
    # vollstaendigen Stand; das AuditLog ist der minimierte
    # Rechenschaftsnachweis und fuehrt bewusst nur Feldnamen.
    #
    # Bewusst als LETZTER Schritt von ``update_event``: ``AuditLog.save()`` nimmt
    # den per-Einrichtung-Advisory-Lock der Hashkette, der erst beim Commit der
    # umgebenden Transaktion faellt (Refs #1345/#1956) — im View-Pfad also erst
    # nach ``apply_attachment_changes``, das im selben ``atomic`` folgt. Je
    # spaeter er genommen wird, desto kuerzer serialisiert er die uebrigen
    # audit-schreibenden Vorgaenge derselben Einrichtung. Der Virenscan der
    # Anhaenge liegt seit Refs #1956 als Staging VOR der Transaktion und damit
    # ausserhalb dieses Fensters.
    if changed_slugs:
        audit_event(
            AuditLog.Action.EVENT_UPDATE,
            user=user,
            facility=event.facility,
            target_obj=event,
            # Nur Slugs, nie Werte oder Freitexte (Datenminimierung im
            # append-only Log, Refs #1944).
            detail={"document_type": event.document_type.name, "changed_fields": changed_slugs},
        )
    return event


def decrypt_event_text_data(event):
    """Refs #1160: Klartext-``data_json`` eines Events fuer Form-Vorbelegung.

    Liefert ein ``{slug: wert}``-Dict, in dem File-Marker (legacy ``__file__``
    und Stufe-B ``__files__``) uebersprungen und alle uebrigen Werte via
    :func:`safe_decrypt` (Default ``""``) entschluesselt sind. Aus
    ``EventUpdateView.get``/``post`` herausgezogen — beide Stellen bauten
    bisher dieselbe Schleife (Duplikat, Refs #1160 R1b).
    """
    from core.services.file_vault import safe_decrypt

    result = {}
    for key, value in (event.data_json or {}).items():
        if isinstance(value, dict) and (value.get("__file__") or value.get("__files__")):
            continue
        result[key] = safe_decrypt(value, default="")
    return result


def merge_update_payload(event, merged, restricted_keys, document_type):
    """Refs #1160: Update-Payload um Felder ergaenzen, die das Formular nicht traegt.

    Aus ``EventUpdateView.post`` herausgezogen (R1b). Mutiert ``merged`` in-place
    und liefert es zurueck:

    - **Restricted-Felder** (vom User nicht sichtbar, vorher per
      ``remove_restricted_fields`` aus dem Form entfernt) werden mit ihrem
      Original-Wert aus ``event.data_json`` wieder eingesetzt — sonst wuerde der
      Update sie loeschen.
    - **FILE-Felder** behalten ihren bestehenden Marker (beide Formate), damit
      ``apply_attachment_changes`` ihn anschliessend anpassen kann.
    """
    event_data = event.data_json or {}

    # Re-insert restricted fields with original values
    for key in restricted_keys:
        if key in event_data:
            merged[key] = event_data[key]

    # Für FILE-Felder: bestehenden Marker (beide Formate) erstmal beibehalten —
    # apply_attachment_changes passt ihn anschliessend an.
    field_templates = build_field_template_lookup(document_type)
    for slug, ft in field_templates.items():
        if ft.field_type == FieldTemplate.FieldType.FILE:
            existing_marker = event_data.get(slug)
            if isinstance(existing_marker, dict) and (
                existing_marker.get("__file__") or existing_marker.get("__files__")
            ):
                merged[slug] = existing_marker
    return merged


#: Meldung des Legal-Hold-Gates am manuellen Loeschpfad (ADR-050 M-9).
LEGAL_HOLD_BLOCKS_DELETE_MESSAGE = _(
    "Für dieses Ereignis besteht eine Aufbewahrungssperre (Legal Hold) — "
    "es kann nicht gelöscht werden, solange die Sperre gilt."
)


def _lock_client_row(client_id):
    """Sperrt die Personenzeile — erste Stufe der kanonischen Ordnung (ADR-050 D2).

    Bis zum Re-Review entstand diese Sperre nur als **Nebenwirkung** von
    ``assert_processing_allowed(lock=True)``. Der gepruefte Vier-Augen-Weg
    laeuft aber im benannten Loeschworkflow-Kontext, in dem das Art.-18-Gate
    sofort zurueckkehrt — Art. 17 gilt waehrend einer Art.-18-Einschraenkung
    weiter. Mit dem Gate entfiel dort auch die Sperre, und der
    Koordinationspunkt gegen einen parallelen Personen-Hold fehlte
    ausgerechnet im gepruefen Loeschweg.

    Sie wird deshalb hier eigenstaendig genommen: der Loeschpfad braucht sie als
    Koordinationspunkt, unabhaengig davon, ob das Art.-18-Gate greift. Doppeltes
    Sperren derselben Zeile in derselben Transaktion ist folgenlos.

    ``client_id`` ist ``None`` bei personenlosen (anonymen) Ereignissen — dort
    gibt es keine Personenzeile und nichts zu koordinieren.
    """
    if client_id is None:
        return
    Client.objects.filter(pk=client_id).select_for_update().only("pk").first()


def _lock_event_row(event):
    """Sperrt die Ereigniszeile — der Koordinationspunkt aus ADR-050 D2.

    Muss VOR der Hold-Lesung stehen (Cross-Review Finding 3). Ohne sie war die
    M-9-Pruefung gegenueber einer konkurrierenden Hold-Anlage TOCTOU-anfaellig:
    ``assert_processing_allowed(lock=True)`` sperrt nur die **Client**-Zeile,
    und die Sperre auf der Ereigniszeile entstand erst beim spaeteren
    ``event.save()``. Dazwischen konnte eine Hold-Transaktion dieselbe Zeile
    sperren, ihren Hold committen — und vom Loeschvorgang ueberholt werden, der
    den Hold nie gesehen hat. ``transaction.atomic()`` allein schliesst dieses
    Rennen nicht, weil ``READ COMMITTED`` jede Anweisung neu liest.

    Es ist dieselbe Zeile, die ``create_hold_for_target`` und der
    EventHistory-Sweep sperren; damit ist das Rennen in beide Richtungen
    definiert entschieden. Kanonische Ordnung: ``core_client`` (Art.-18-Gate)
    -> ``core_event`` -> Hold-Lesung.
    """
    Event.objects.filter(pk=event.pk).select_for_update().only("pk").first()


#: Meldung, wenn ein paralleler Merge das Ereignis waehrend der Sperrwartezeit
#: an eine andere Person gehaengt hat (ADR-050 D2).
CLIENT_BINDING_CHANGED_MESSAGE = _(
    "Die Personenzuordnung dieses Ereignisses wurde zwischenzeitlich geändert — "
    "bitte erneut öffnen und die Löschung wiederholen."
)


def _assert_current_client_binding(event):
    """Revalidiert den Event->Client-Bezug NACH der Ereignissperre.

    Der Aufrufer haelt das Ereignis womoeglich seit Minuten im Speicher (der
    gepruefte Vier-Augen-Weg laedt es ungesperrt), und die Client-Sperre oben
    galt genau diesem — moeglicherweise veralteten — Bezug. Gewinnt parallel
    ein Merge und haengt das Ereignis an eine andere Person
    (``services/client/merge.py``), dann haette der Loeschvorgang die Zeile der
    NEUEN Person nie gesperrt und ihren Hold nie gesehen.

    Bewusst **Abbruch statt Nachsperren**: die neue Personenzeile jetzt noch zu
    sperren, waere eine Sperr-Inversion (``Client`` steht in der kanonischen
    Ordnung vor ``Event``, und das Ereignis ist bereits gesperrt) — genau der
    ABBA-Zyklus, den ADR-050 D5.5 ausschliesst. Der Vorgang bricht deshalb
    sauber ab und ist wiederholbar; beim naechsten Versuch liest der Aufrufer
    den neuen Bezug und sperrt in der richtigen Reihenfolge.
    """
    current_client_id = Event.objects.filter(pk=event.pk).values_list("client_id", flat=True).first()
    if current_client_id != event.client_id:
        raise ValidationError(CLIENT_BINDING_CHANGED_MESSAGE)


def _assert_no_active_hold(event):
    """M-9 (ADR-050 D2): Der manuelle Loeschpfad prueft den Hold — bisher gar nicht.

    ``soft_delete_event`` leerte Payload und Anhaenge unmittelbar; **nur** die
    Retention-Query schloss gehaltene Ereignisse aus. Ein Hold sichert
    Beweismittel; ein Loeschweg, der ihn nicht kennt, ist die Hintertuer um
    genau diese Sicherung (Spoliation, Muster Refs #1066).

    Fail-loud statt stillem Ueberspringen: der manuelle Pfad hat eine
    handelnde Person und eine Meldungs-Flaeche — sie muss erfahren, warum
    nichts passiert ist.

    Der Hold auf die **Person** zaehlt mit (Refs #1376, I10): er schuetzt
    deren Ereignisse schon im Retention-Pfad, und ein manueller Klick darf
    nicht mehr duerfen als der Cron.
    """
    from core.retention.legal_holds import has_active_hold

    if has_active_hold(event.facility, "Event", event.pk):
        raise ValidationError(LEGAL_HOLD_BLOCKS_DELETE_MESSAGE)
    if event.client_id and has_active_hold(event.facility, "Client", event.client_id):
        raise ValidationError(LEGAL_HOLD_BLOCKS_DELETE_MESSAGE)


@transaction.atomic
def soft_delete_event(event, user, *, source=None):
    """Soft-Delete + EventHistory(DELETE) + AuditLog.

    Art.-18-Gate (Refs #2045) — seit dem Re-Review IM Service statt ungesperrt
    in der View davor: der Service ist direkt aufrufbar, und ein View-Check vor
    einem atomaren Service ist ein TOCTOU-Fenster. Die Sperre auf der
    Client-Zeile haelt hier bis zum Commit.

    Der GEPRUEFTE Vier-Augen-Weg (``approve_event_deletion``) ruft denselben
    Service und oeffnet dafuer den benannten Loeschworkflow-Kontext — Art. 17
    gilt waehrend der Einschraenkung weiter. Blockiert bleibt die
    Einzelentscheidung ohne zweite Person. Der Strichlisten-Undo passiert das
    Gate ohne Kontext, weil seine Ereignisse anonym sind (``client`` ist
    ``None``) und damit nie einen Personenbezug tragen.
    """
    from core.services.client.restriction import assert_processing_allowed

    assert_processing_allowed(event.client_id, lock=True)
    # Reihenfolge tragend (ADR-050 D2): erst Personen-, dann Ereigniszeile
    # sperren, DANN den Hold lesen — sonst bleibt zwischen Lesung und Wirkung
    # ein Fenster offen. Beide Sperren stehen hier eigenstaendig und nicht als
    # Nebenwirkung des Art.-18-Gates, das der gepruefte Vier-Augen-Weg
    # ueberspringt.
    _lock_client_row(event.client_id)
    _lock_event_row(event)
    _assert_current_client_binding(event)
    _assert_no_active_hold(event)
    history_payload = build_redacted_delete_history(event)
    # Refs #1917: VOR dem Wipe bumpen — nur ein aktiver Ban erzeugt ein
    # Existenz-Delta; das Löschen eines schlafenden Hausverbots bleibt für
    # alle Rollen unbeobachtbar (Seitenkanal-Auflage).
    from core.services.system.ban_revision import bump_ban_revision_for_event_change

    bump_ban_revision_for_event_change(event.facility, event.document_type, event.data_json, {})
    event.is_deleted = True
    # Refs #2157: Zeitpunkt UND Ausloeser des Papierkorb-Eintrags. ``user`` ist
    # hier immer die Person, die die Loeschentscheidung traegt — beim gepruef-
    # ten Vier-Augen-Weg der Reviewer (``approve_deletion``), beim Strichlisten-
    # Undo die zuruecknehmende Person, sonst die loeschende Fachkraft. Der
    # Systempfad (Aufbewahrungslauf) laeuft hier bewusst vorbei und stempelt
    # ``deleted_by=None`` (``retention/enforcement.py``).
    event.deleted_at = timezone.now()
    event.deleted_by = user
    event.data_json = {}
    delete_attachments_for(event)
    # ``update_fields`` statt eines vollen ``save()``: die Instanz stammt
    # womoeglich aus einem aelteren Lesevorgang, und ein Voll-Save schriebe
    # jedes Feld daraus zurueck — inklusive eines ``client_id``, den ein
    # paralleler Merge inzwischen geaendert hat. ``search_text`` MUSS in der
    # Liste stehen, sonst persistiert Django den vom ``pre_save``-Signal
    # berechneten Leerwert nicht und der Klartext-PII bliebe im Suchindex
    # stehen (Refs #1092) — dieselbe Liste wie im Retention-Loeschpfad.
    event.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "data_json", "search_text", "updated_at"])
    EventHistory.objects.create(
        event=event,
        changed_by=user,
        action=EventHistory.Action.DELETE,
        data_before=history_payload,
        field_metadata=_snapshot_field_metadata(event.document_type),
    )
    audit_detail = {
        "document_type": event.document_type.name,
        # L13 (Refs #1375): neutrale client_id statt Klartext-Pseudonym —
        # Data-Min-Residuum, konsistent zum gehärteten Client-Löschpfad
        # (Refs #1093). Das append-only-AuditLog konserviert das Pseudonym
        # sonst dauerhaft, obwohl der Löschzweck es nicht braucht.
        "client_id": (str(event.client_id) if event.client_id else None),
        "occurred_at": str(event.occurred_at),
    }
    if source is not None:
        audit_detail["source"] = source
    audit_event(
        AuditLog.Action.DELETE,
        user=user,
        facility=event.facility,
        target_obj=event,
        detail=audit_detail,
    )
    log_activity(
        facility=event.facility,
        actor=user,
        verb=Activity.Verb.DELETED,
        target=event,
        action=Activity.Action.EVENT_DELETED,
    )
