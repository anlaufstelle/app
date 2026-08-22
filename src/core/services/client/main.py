"""Service layer for client-related business logic."""

import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.constants import RECENT_VISIT_MAX_AGE_DAYS, RECENT_VISIT_MAX_PER_USER
from core.models import AuditLog, Client, DocumentType
from core.models.activity import Activity
from core.models.recent_client_visit import RecentClientVisit
from core.models.workitem import DeletionRequest
from core.retention.legal_holds import client_has_active_hold, exclude_clients_with_active_holds
from core.services.audit import audit_client_event, audit_event
from core.services.dashboard import log_activity
from core.services.dynamic_data import validate_data_json
from core.services.security import check_version_conflict
from core.services.system import bypass_replication_triggers

logger = logging.getLogger(__name__)


_REDACTED_HISTORY_MARKER = {"_redacted": True, "anonymized": True}


def _check_client_document_type(facility, document_type):
    """Prueft den Personen-``DocumentType`` (M2 P4b, Refs #1058).

    Zwei Bedingungen, beide als deutsche ``ValidationError`` (der Aufrufer ist
    ein Formular):

    (a) **Facility-Scope** — ein Typ einer fremden Einrichtung waere ein
        Cross-Tenant-Leck (der Slug-Satz stammte aus fremder Konfiguration).
    (b) **Entitaetstyp** — nur ``entity_type=client``. Ein Ereignis-Typ traegt
        Feldsemantik, die an einem Personen-Datensatz keine Bedeutung hat.

    ``None`` (kein Typ) ist zulaessig und wird unveraendert durchgereicht.
    """
    if document_type is None:
        return None
    if document_type.facility_id != facility.pk:
        raise ValidationError(_("Der Dokumentationstyp gehört nicht zu dieser Einrichtung."))
    if document_type.entity_type != DocumentType.EntityType.CLIENT:
        raise ValidationError(_("Der Dokumentationstyp ist nicht für Personen vorgesehen."))
    return document_type


def _normalize_document_type_kwarg(data: dict) -> DocumentType | None:
    """Normalisiert beide FK-Schreibweisen auf EIN ``document_type``-Objekt.

    ``create_client`` reicht ``**data`` unveraendert an ``Client(...)`` durch,
    und Django akzeptiert dort neben ``document_type=<obj>`` auch
    ``document_type_id=<pk>``. Ein Guard, der nur ``data["document_type"]``
    liest, waere fuer die zweite Schreibweise blind — ein facility-fremder oder
    ereignis-gebundener Typ landete dann an der Zeile und fiele erst spaeter
    (FK/RLS/Verschluesselung) und ohne fachliche Meldung auf.

    Die ``*_id``-Variante wird daher hier aufgeloest und durch das Objekt
    ersetzt, sodass :func:`_check_client_document_type` **genau einen** Pfad zu
    pruefen hat. Unbekannte oder syntaktisch ungueltige PKs werden als
    ``ValidationError`` abgewiesen (fail-closed), ebenso das gleichzeitige
    Setzen beider Schreibweisen — das waere mehrdeutig, weil die Reihenfolge
    der ``kwargs`` entschiede, welcher Wert gewinnt.
    """
    if "document_type_id" not in data:
        return data.get("document_type")

    raw_pk = data.pop("document_type_id")
    if data.get("document_type") is not None:
        raise ValidationError(_("Dokumentationstyp bitte nur einmal angeben (Objekt oder ID, nicht beides)."))
    if raw_pk is None:
        data["document_type"] = None
        return None
    try:
        document_type = DocumentType.objects.filter(pk=raw_pk).first()
    except (ValidationError, ValueError, TypeError):
        # Django wirft bei ungueltigen UUID-Strings ValidationError, nicht
        # ValueError (gleiche Falle wie in ``get_client_or_none``).
        document_type = None
    if document_type is None:
        raise ValidationError(_("Der Dokumentationstyp existiert nicht."))
    data["document_type"] = document_type
    return document_type


def _clean_client_dynamic_data(facility, document_type, data_json) -> dict:
    """Guard + Whitelist fuer die Dynamikdaten einer Person (M2 P4b, Refs #1058).

    Prueft erst den Typ (:func:`_check_client_document_type`) und filtert dann
    ``data_json`` mit :func:`~core.services.dynamic_data.validate_data_json`
    gegen dessen Feld-Slugs. **Der Rueckgabewert ist verbindlich** — die
    Whitelist verwirft fremde Keys, und nur wer das Ergebnis speichert,
    verhindert Mass-Assignment ins JSONB.

    Ohne Typ gibt es keine Whitelist: dann duerfen auch keine Dynamikdaten
    gesetzt werden (statt sie still zu verschlucken).
    """
    document_type = _check_client_document_type(facility, document_type)
    if document_type is None:
        if data_json:
            raise ValidationError(_("Ohne Dokumentationstyp können keine dynamischen Daten gespeichert werden."))
        return {}
    return validate_data_json(document_type, data_json)


def _redact_client_identity(client) -> None:
    """Ersetze Klartext-Felder am Client durch anonyme Marker.

    M2 P4b (Refs #1058, Entscheid E7): ``data_json`` traegt seit P4b freie
    Feldwerte (auch verschluesselte) und wird mit geleert; ``document_type``
    faellt auf ``NULL`` zurueck, damit der ``PROTECT``-Verweis anonymisierte
    Bestaende nicht dauerhaft an einen Konfigurationstyp kettet (der Typ waere
    sonst nie loeschbar, obwohl die Daten weg sind).
    """
    client.pseudonym = f"Gelöscht-{str(client.pk)[:8]}"
    client.notes = ""
    client.age_cluster = client.AgeCluster.UNKNOWN
    client.is_active = False
    client.data_json = {}
    client.document_type = None
    client.save(
        update_fields=[
            "pseudonym",
            "notes",
            "age_cluster",
            "is_active",
            "data_json",
            "document_type",
        ]
    )


def _redact_cases_and_episodes(client) -> list:
    """Anonymisiere alle Cases + ihre Episoden. Returns case_ids für Folgeschritte.

    Refs #1818: die eigentliche Redaktion liegt seit dem Fall-Loeschpfad in
    :func:`core.services.case.deletion.redact_case_content` — sie ist dort der
    Kern der Fristablauf-Tilgung (ADR-044, P3a) und hier der Kaskadenschritt
    der Personen-Anonymisierung. **Eine** Implementierung, weil beide Pfade
    denselben Rest hinterlassen muessen: sonst haengt das Ergebnis davon ab, ob
    eine ganze Person oder ein einzelner Fall geloescht wurde.
    """
    from core.models.case import Case
    from core.services.case.deletion import redact_case_content

    cases = Case.objects.filter(client=client)
    case_ids = list(cases.values_list("pk", flat=True))
    for case in cases:
        redact_case_content(case)
    return case_ids


def _redact_case_goals(case_ids: list) -> None:
    """Anonymisiere Wirkungsziele + Meilensteine der Faelle (Refs #1751, Punkt 1).

    Delegiert seit #1818 an :func:`core.services.case.deletion.redact_case_goals`
    (dieselbe Begruendung wie bei :func:`_redact_cases_and_episodes`). Der
    private Name bleibt hier stehen, weil der Paritaets-Guard
    ``test_bridge_calls_every_hard_path_redactor`` die Kaskadenschritte des
    Hard-Pfads an ihm abliest.
    """
    from core.services.case.deletion import redact_case_goals

    redact_case_goals(case_ids)


def _redact_workitems(client) -> None:
    """Anonymisiere alle WorkItems des Klienten (auch DONE/DISMISSED)."""
    client.work_items.all().update(
        title="Aufgabe (anonymisiert)",
        description="",
    )


def _event_ids_for_client(client) -> list:
    """PKs aller Ereignisse des Klienten — reine Lese-Operation.

    Refs #1782: bis dahin fielen die ``event_ids`` als Nebenprodukt von
    :func:`_delete_event_attachments_for_client` an. Genau das zwang den
    Datei-Loeschschritt an den ANFANG der Kaskade, weil alle nachgelagerten
    Redaktionen die IDs brauchen. Getrennt eingesammelt darf das Loeschen ans
    Transaktionsende — dorthin, wo ein Rollback keine Dateiluecke mehr
    hinterlaesst.

    Auch die K-Anon-Bridge (``core/retention/anonymization.py``) nutzt diesen
    Helfer, statt die Query ein zweites Mal zu schreiben.
    """
    from core.models.event import Event

    return list(Event.objects.filter(client=client).values_list("pk", flat=True))


def _delete_event_attachments_for_client(client, event_ids: list) -> None:
    """Loescht Attachments der uebergebenen Ereignisse (Dateien + DB-Zeilen).

    Refs #1782: ``event_ids`` kommt jetzt von aussen (siehe
    :func:`_event_ids_for_client`) statt hier nebenbei zu entstehen — der
    Aufrufer bestimmt damit den Zeitpunkt der physischen Loeschung.
    """
    from core.models.event import Event
    from core.services.file_vault import delete_attachments_for

    if not event_ids:
        return
    for event in Event.objects.filter(pk__in=event_ids).iterator():
        delete_attachments_for(event)


def _delete_owned_attachments_for_client(client, case_ids: list) -> None:
    """Loescht personen- und falleigene Anhaenge (Dateien + DB-Zeilen).

    M24 (Refs #1738, ADR-039 §7): Seit dem Owner-Umbau haengt ein Anhang an
    genau einem Owner — Ereignis, **Person** oder **Fall**. Die beiden neuen
    Owner-Sorten haben keinen Vorlaeufer-Loeschpfad: es gibt keinen
    Fall-Loeschworkflow (ADR-039 §6) und der Personen-Soft-Delete raeumt keine
    Dateien ab. Ohne diesen Helfer ueberlebten die verschluesselten Dateien die
    Anonymisierung dauerhaft — ``cleanup_orphan_storage_files`` verschont
    Dateien MIT Record, also faenden sie auch keine Waisen-Raster (Art. 17).

    Gegenstueck zu :func:`_delete_event_attachments_for_client`, bewusst als
    **zweiter** Helfer: der Ereignis-Zweig ist in der K-Anon-Bridge entbehrlich
    (dort sind Ereignis-Anhaenge per Eintrittsbedingung schon weg), dieser hier
    ist in **beiden** Pfaden Pflicht. Seit #1782 stehen beide unmittelbar
    nebeneinander am Transaktionsende.

    ``case_ids`` stammt aus :func:`_redact_cases_and_episodes` — die Faelle
    sind zu diesem Zeitpunkt bereits redigiert (``data_json = {}``), womit die
    Marker-Bereinigung der Invariante I-M1 im selben ``atomic`` erledigt ist.
    """
    from core.models.case import Case
    from core.services.file_vault import delete_attachments_for

    delete_attachments_for(client)
    if not case_ids:
        return
    for case in Case.objects.filter(pk__in=case_ids).iterator():
        delete_attachments_for(case)


def _redact_live_events(client) -> None:
    """Leere ``data_json`` + ``search_text`` der noch nicht soft-deleteten Events.

    Refs #1089: ``anonymize_client`` redigierte bisher nur ``EventHistory``, ließ
    aber die Live-``Event``-Zeilen mit Klartext stehen. Beim Trash-Expiry-Pfad
    (``anonymize_eligible_soft_deleted_clients``) sind die Events evtl. noch nicht
    soft-deletet — ohne diese Redaktion bleibt Klartext-Residue stehen. Die Events
    bleiben als Statistik-Aggregat erhalten (``client_id``/``occurred_at``/
    ``document_type``), nur der Freitext-Inhalt geht raus.

    Nur Live-Events (``is_deleted=False``): bereits soft-deletete Events haben ihr
    ``data_json`` schon im Soft-Delete-Pfad geleert; deren ``search_text``-Leck im
    Retention-Soft-Delete ist ein eigener Befund (#1092) und wird hier bewusst
    nicht angefasst.

    ``search_text`` MUSS in ``update_fields`` stehen — sonst persistiert Django den
    vom ``pre_save``-Signal (``_refresh_event_search_text``) berechneten Leerwert
    nicht (genau die Falle, die #1092 im Retention-Pfad auslöst).
    """
    from core.models.event import Event

    for event in Event.objects.filter(client=client, is_deleted=False).iterator():
        event.data_json = {}
        event.save(update_fields=["data_json", "search_text", "updated_at"])


def _redact_event_history(event_ids: list) -> None:
    """Anonymisiere EventHistory-Eintraege per Trigger-Bypass.

    Refs #905: ``EventHistory`` ist append-only via Migration 0012 — ein
    direktes ``UPDATE`` wuerde blocken. ``bypass_replication_triggers``
    deaktiviert den Trigger nur fuer die Dauer dieses Aufrufs.
    """
    from core.models.event_history import EventHistory

    if not event_ids:
        return
    history_qs = EventHistory.objects.filter(event_id__in=event_ids)
    if not history_qs.exists():
        return
    with bypass_replication_triggers():
        history_qs.update(
            data_before=_REDACTED_HISTORY_MARKER,
            data_after=_REDACTED_HISTORY_MARKER,
        )


def _redact_deletion_requests(event_ids: list) -> None:
    """Anonymisiere DeletionRequest.reason fuer Event-Targets dieses Klienten.

    Antrags-Meta (status/requested_by/reviewed_by/timestamps) bleiben fuer
    den Vier-Augen-Audit-Trail erhalten — nur der freitext ``reason``
    wird redigiert.
    """
    if not event_ids:
        return
    DeletionRequest.objects.filter(
        target_type="Event",
        target_id__in=event_ids,
    ).update(reason="[Anonymisiert]")


def _redact_client_deletion_request(client) -> None:
    """Redigiere reason des Client-Target-Loeschantrags dieses Klienten (Refs #1091).

    Der Vier-Augen-Antrag aus ``request_client_deletion`` hat
    ``target_type="Client"`` und wird vom Event-only-Helper
    ``_redact_deletion_requests`` nicht erfasst. Antrags-Meta
    (status/requested_by/reviewed_by/timestamps) bleibt fuer den
    Audit-Trail erhalten — nur der Freitext ``reason`` wird redigiert.
    """
    DeletionRequest.objects.filter(
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=client.pk,
    ).update(reason="[Anonymisiert]")


def _redact_case_deletion_requests(case_ids: list) -> None:
    """Redigiere ``reason`` der FALL-Loeschantraege dieser Person (Refs #1818).

    Der Redaktions-Faecher kannte bis ADR-044 nur zwei Zieltypen:
    :func:`_redact_deletion_requests` (``target_type="Event"``) und
    :func:`_redact_client_deletion_request` (``target_type="Client"``). Mit dem
    Fall-Loeschpfad ist ``target_type="Case"`` ein drittes zulaessiges Ziel —
    ohne diesen Helfer ueberlebte die Begruendung eines Fall-Loeschantrags
    („Fall doppelt angelegt, Betreuung laeuft bei Frau Meier") die
    **vollstaendige** Anonymisierung der Person, also genau die Luecke, die
    #1091 fuer den Client- und #1751 fuer die uebrigen Freitexte geschlossen
    haben.

    Der Fall-Pfad selbst redigiert seinen Antrag schon
    (``services/case/deletion._redact_case_deletion_request``); hier geht es um
    den Weg ueber die Person, der die Faelle mitnimmt, ohne den Fall-Pfad zu
    betreten. Antrags-Meta (status/requested_by/reviewed_by/timestamps) bleibt
    wie ueberall als Vier-Augen-Nachweis stehen.
    """
    if not case_ids:
        return
    DeletionRequest.objects.filter(
        target_type=DeletionRequest.TargetType.CASE,
        target_id__in=case_ids,
    ).update(reason="[Anonymisiert]")


def _redact_rectification_requests(client) -> None:
    """Redigiere die Wertpaare/Freitexte der Art.-16-Antraege dieser Person (Refs #1792).

    ``RectificationRequest`` (ADR-041) haelt vier Traeger von Klartext:
    ``old_value`` (der urspruengliche Feldwert der Person), ``new_value`` (der
    Wunschwert), ``reason`` (die fachliche Begruendung, die den strittigen
    Sachverhalt benennt) und ``review_note``. ``target_client`` ist zwar
    CASCADE — das greift aber nur beim HARTEN Loeschen; die Anonymisierung ist
    ein Soft-Pfad und liesse die Zeile sonst mitsamt Inhalt stehen.

    Geleert wird auf ``NULL``, nicht auf den ``[Anonymisiert]``-Marker des
    Loeschantrags: alle vier Spalten sind ``JSONField(null=True)`` und tragen
    per Vertrag ausschliesslich ``encrypt_field``-Marker-Dicts. Ein
    Klartext-Sentinel waere dort ein Formatbruch. ``NULL`` ist lesesicher —
    ``safe_decrypt`` reicht es durch, ``decode_rectification_text`` liefert
    ``""``, und ``reencrypt_fields`` uebergeht die Zeile.

    Antrags-Meta (status, field_path, requested_by/reviewed_by, Zeitstempel)
    bleibt als Vier-Augen-Nachweis erhalten — exakt wie beim Loeschantrag.
    """
    from core.models import RectificationRequest

    RectificationRequest.objects.filter(target_client=client).update(
        old_value=None,
        new_value=None,
        reason=None,
        review_note=None,
    )


def _redact_retention_proposals(event_ids: list) -> None:
    """Redigiere das Klartext-Pseudonym in ``RetentionProposal.details`` (Refs #1751, Punkt 3).

    ``build_proposal_details`` schreibt Pseudonym und Kontaktstufe des
    Klienten in das JSON-Feld ``details``. ``cleanup_stale_proposals`` raeumt
    nur Vorschlaege **geloeschter** Events ab — ein Klient kann aber mit noch
    lebenden Events anonymisiert werden (Papierkorb-Ablauf), dessen
    Vorschlaege behielten dann das Original-Pseudonym.

    Nur der Freitext geht raus: ``occurred_at``, ``document_type`` und
    ``contact_stage`` bleiben, weil sie der Nachweis-Trail des
    Loeschvorschlags braucht — ``contact_stage`` ist ein Enum-Bucket ohne
    Personenbezug und bleibt aus demselben Grund stehen, aus dem
    ``k_anonymize_client`` ihn an der Client-Zeile behaelt.
    """
    from core.models.retention import RetentionProposal

    if not event_ids:
        return
    proposals = RetentionProposal.objects.filter(
        target_type=RetentionProposal.TargetType.EVENT,
        target_id__in=event_ids,
    )
    for proposal in proposals.iterator():
        details = proposal.details or {}
        if "pseudonym" not in details:
            continue
        proposal.details = {**details, "pseudonym": "[Anonymisiert]"}
        proposal.save(update_fields=["details"])


def _redact_legal_holds(client, event_ids: list, case_ids: list | None = None) -> None:
    """Redigiere ``LegalHold.reason`` fuer Client-, Event- und Fall-Ziele (Refs #1751, #1818).

    ``reason`` ist bestimmungsgemaess Freitext mit Fall-/Klientenkontext und
    kannte bisher **keinen** Redaktions- oder Pruning-Pfad: abgelaufene und
    aufgehobene Holds blieben als Zeilen fuer immer stehen.

    Redigiert wird nur der Freitext — Ziel, Fristen, Ersteller und
    Aufhebungs-Metadaten bleiben fuer den Nachweis-Trail erhalten (die Zeile
    belegt weiterhin, DASS eine Aufbewahrungspflicht bestand).

    Der **Fall**-Zweig (``target_type="Case"``, Refs #1818) kam mit ADR-044
    dazu: seit ``create_hold_for_target`` einen Fall-Hold erzeugen kann, faellt
    dessen ``reason`` sonst durch beide bisherigen Zweige und bliebe nach der
    Personen-Anonymisierung dauerhaft im Klartext stehen — ``LegalHold.reason``
    ist laut eigenem Docstring bestimmungsgemaess Freitext mit
    Fall-/Klientenkontext und kennt keinen zweiten Pruning-Pfad.

    Hinweis zur Reichweite: ein **aktiver** Hold blockiert die Anonymisierung
    an beiden Retention-Einstiegen — ``anonymize_eligible_soft_deleted_clients``
    (Refs #1066) und der K-Anon-Bridge ``anonymize_clients`` (Refs #1756) —,
    sodass in der Praxis nur aufgehobene/abgelaufene Holds hier ankommen. Der
    Filter unterscheidet trotzdem nicht nach Aktivitaet: waere ein aktiver Hold
    vorhanden, waere bereits die Anonymisierung selbst der Fehler — nicht
    diese Redaktion.
    """
    from django.db.models import Q

    from core.models.retention import LegalHold

    targets = Q(target_type="Client", target_id=client.pk)
    if event_ids:
        targets |= Q(target_type="Event", target_id__in=event_ids)
    if case_ids:
        targets |= Q(target_type="Case", target_id__in=case_ids)
    LegalHold.objects.for_facility(client.facility).filter(targets).update(reason="[Anonymisiert]")


def _delete_recent_visits(client) -> None:
    """Loesche die ``RecentClientVisit``-Zeilen dieser Person (Refs #1761).

    Die Zeile traegt kein eigenes PII-Feld (nur FKs + ``visited_at``), ist also
    kein Klartext-Leck — aber sie ist gespeicherte Information *ueber die
    Person* (wer hat sie wann angesehen) und speist das „zuletzt besucht"-
    Widget (``AccountProfileView``), das nach der Anonymisierung sonst weiter
    einen Eintrag zeigte, jetzt unter dem Ersatz-Pseudonym. Anders als die
    ``_redact_*``-Helfer wird hier **geloescht** statt redigiert: es gibt kein
    Feld, dessen Erhalt einen Nachweis- oder Aggregat-Zweck haette (im
    Gegensatz zu ``DeletionRequest``/``LegalHold``, wo die Meta-Spur bleibt).

    Bewusst ueber ALLE Nutzer:innen hinweg, nicht nur die handelnde — der
    Besuchseintrag einer Kollegin traegt denselben Bezug.
    """
    RecentClientVisit.objects.filter(client=client).delete()


def _revoke_offline_bundles(client) -> None:
    """Widerrufe bereits ausgelieferte Offline-Bundles dieser Person (Refs #1760).

    Die Redaktions-Kaskade oben raeumt den **Server** auf. Ein Bundle, das schon
    im IndexedDB eines Aussendienst-Geraets liegt, erreicht sie nicht: es traegt
    bis zu 48 h (``BUNDLE_TTL_SECONDS``) Klartext weiter, und die bestehende
    Revalidierung purgt ihn nicht — die anonymisierte Person liefert weiterhin
    **200**, und bei offenen ungesyncten Aenderungen wird das frische Bundle
    sogar bewusst NICHT ueber den lokalen Stand geschrieben.

    Der Widerruf laeuft daher als eigener, personen-genauer Kanal in DERSELBEN
    Transaktion wie die Kaskade. Duenner Wrapper, damit der ableitungsbasierte
    Paritaets-Guard (``TestKAnonBridgeRedactionParity``) den Aufruf wie jeden
    ``_redact_*``-Helfer erfasst und die K-Anon-Bridge nicht zurueckfaellt.
    """
    from core.services.system import revoke_offline_client_bundles

    revoke_offline_client_bundles(client)


def _redact_activities(client, event_ids: list) -> None:
    """Redigiere ``Activity.summary`` fuer Client-, Event-, WorkItem- und Case-Targets.

    Mehrere Pfade schreiben das Klartext-Pseudonym in den Zeitstrom:
    ``create_client``/``update_client``/``approve_client_deletion``
    (Target = Client) sowie ``create_event`` („… für <pseudonym>",
    Target = Event). ``create_workitem``/``update_workitem``/
    ``update_workitem_status`` schreiben zudem Activities mit Target =
    WorkItem und der Titel im ``summary`` („Aufgabe: <titel>"), der
    Klienten-PII (z.B. Pseudonym) tragen kann (Refs #1090). Verb +
    Zeitpunkt bleiben fuer den Feed erhalten, nur der Freitext wird
    ersetzt.

    Refs #1751 (Punkt 2): ``close_case``/``reopen_case`` („Fall
    ‚<titel>' geschlossen") und ``delete_milestone`` („Meilenstein
    ‚<titel>' gelöscht") schreiben mit Target = **Case**. Diese Zeilen
    fielen durch den bisherigen Drei-Typen-Filter und blieben bis zur
    ``retention_activities_days``-Frist (Default 365 Tage) stehen —
    deutlich laenger als die Papierkorb-Frist (Default 30 Tage), nach
    der die Anonymisierung greift.

    Die ``case_ids`` werden hier selbst aufgeloest (wie die
    ``workitem_ids``), damit die Signatur stabil bleibt und **beide**
    Anonymisierungspfade ohne zusaetzliche Verdrahtung profitieren.

    Refs #1067.
    """
    from django.contrib.contenttypes.models import ContentType
    from django.db.models import Q

    from core.models.case import Case
    from core.models.event import Event
    from core.models.workitem import WorkItem

    targets = Q(
        target_type=ContentType.objects.get_for_model(Client),
        target_id=client.pk,
    )
    if event_ids:
        targets |= Q(
            target_type=ContentType.objects.get_for_model(Event),
            target_id__in=event_ids,
        )
    workitem_ids = list(client.work_items.values_list("pk", flat=True))
    if workitem_ids:
        targets |= Q(
            target_type=ContentType.objects.get_for_model(WorkItem),
            target_id__in=workitem_ids,
        )
    case_ids = list(Case.objects.filter(client=client).values_list("pk", flat=True))
    if case_ids:
        targets |= Q(
            target_type=ContentType.objects.get_for_model(Case),
            target_id__in=case_ids,
        )
    Activity.objects.for_facility(client.facility).filter(targets).update(summary="[Anonymisiert]")


@transaction.atomic
def anonymize_client(client, user=None):
    """DSGVO-konforme Aggregat-Anonymisierung eines Klienten (Refs #715, #743).

    Klartext-Felder werden entfernt, der Datensatz bleibt für Statistik-
    Aggregate erhalten (Events behalten ``client_id`` per ``SET_NULL``-FK).
    Anonymisierung erfasst alle Aggregat-Bestandteile, die sonst eine
    Re-Identifikation ermöglichen würden:

    - ``Client``-Felder (pseudonym, notes, age_cluster, is_active)
    - ``Case.title`` / ``Case.description`` / ``Case.data_json`` /
      ``Case.document_type`` (M2 P5, Refs #1058) aller verknüpften Fälle
    - ``Episode.title`` / ``Episode.description`` aller Episoden dieser Fälle
    - ``OutcomeGoal.title`` / ``OutcomeGoal.description`` / ``Milestone.title``
      aller Wirkungsziele dieser Fälle (Refs #1751)
    - alle Workitems (auch DONE/DISMISSED): title, description
    - ``Event.data_json``/``search_text`` aller noch nicht soft-deleteten
      Events des Klienten (Refs #1089) — die Live-Zeile bliebe sonst mit
      Klartext stehen, etwa im Trash-Expiry-Pfad vor dem Soft-Delete.
    - ``EventHistory.data_before``/``data_after`` für alle Events des
      Klienten — append-only-Trigger (Migration 0012) wird transaktional
      umgangen, sonst blockt das ``UPDATE``.
    - ``Attachment``: Disk-Files + DB-Zeilen via
      :func:`core.services.file_vault.delete_attachments_for` — seit M24
      (Refs #1738) fuer **alle drei** Owner-Sorten: Ereignis-Anhaenge ueber
      :func:`_delete_event_attachments_for_client`, personen- und falleigene
      ueber :func:`_delete_owned_attachments_for_client`. Beide laufen seit
      Refs #1782 gebuendelt als LETZTER Schritt (Begruendung am Aufruf).
    - ``DeletionRequest.reason`` für Event-Targets, für den
      Client-Target-Löschantrag dieses Klienten (Refs #1091) und für die
      Fall-Löschanträge ihrer Fälle (Refs #1818) — Antrags-Meta
      (status/requested_by/...) bleibt für den 4-Augen-Audit-Trail erhalten.
    - ``RectificationRequest.old_value``/``new_value``/``reason``/
      ``review_note`` der Art.-16-Berichtigungsanträge dieser Person
      (Refs #1792) — dieselbe Trennung: Werte raus, Antrags-Meta bleibt.
    - ``RetentionProposal.details["pseudonym"]`` der Löschvorschläge dieser
      Events und ``LegalHold.reason`` für Client-, Event- und Fall-Ziele
      (Refs #1751, #1818) — auch hier bleibt die Nachweis-Metadatenspur
      unangetastet.
    - ``Activity.summary`` für Client-, Event-, WorkItem- und Case-Targets —
      der Zeitstrom trägt sonst das Klartext-Pseudonym weiter (Refs #1067),
      die WorkItem-Target-Activity zudem den Aufgaben-Titel (Refs #1090) und
      die Case-Target-Activity den Fall-/Meilenstein-Titel (Refs #1751).
    - ``RecentClientVisit``-Zeilen der Person werden **gelöscht** (Refs #1761)
      — sonst listete das „zuletzt besucht"-Widget die anonymisierte Person
      weiter.
    - Bereits ausgelieferte **Offline-Bundles** werden über einen
      personen-genauen Revoke-Eintrag entwertet (Refs #1760) — der Server-
      seitige Teil der Kaskade erreicht das IndexedDB der Geräte sonst nicht.

    Refs #905: Public API stabil; intern delegiert an ``_redact_*``-
    Helper, die einzeln testbar sind.
    """
    # Refs #1917 (Fix-Runde): Banner-Wirkung VOR der Kaskade festhalten —
    # ``_redact_live_events`` leert die Event-``data_json``, danach wäre die
    # Person nie mehr als Banner-Eintrag erkennbar und der Bump fiele
    # fälschlich aus. Der Bump selbst folgt als letzter Schritt (s. u.).
    from core.services.system.ban_revision import (
        bump_ban_revision_for_client_change,
        client_has_ban_banner_entries,
    )

    had_banner_entries = client_has_ban_banner_entries(client)
    _redact_client_identity(client)
    case_ids = _redact_cases_and_episodes(client)
    _redact_case_goals(case_ids)
    _redact_workitems(client)
    # Nur LESEN (Refs #1782) — die physische Loeschung dieser Ereignis-Anhaenge
    # steht am Ende dieser Funktion.
    event_ids = _event_ids_for_client(client)
    _redact_live_events(client)
    _redact_event_history(event_ids)
    _redact_deletion_requests(event_ids)
    _redact_client_deletion_request(client)
    _redact_case_deletion_requests(case_ids)
    _redact_rectification_requests(client)
    _redact_retention_proposals(event_ids)
    _redact_legal_holds(client, event_ids, case_ids)
    _redact_activities(client, event_ids)
    _delete_recent_visits(client)
    _revoke_offline_bundles(client)
    # Refs #1344/#1738/#1782: EIN Datei-Zeitpunkt, ganz am Ende der Kaskade.
    # Der ``.enc``-Unlink ist nicht rollback-bar — jede physische Loeschung, die
    # vor einer noch ausstehenden Redaktion laeuft, kann eine Dateiluecke
    # hinterlassen: die Transaktion rollt die Attachment-Zeilen zurueck, die
    # Dateien bleiben weg, und die wiederhergestellte Zeile zeigt ins Leere
    # (Download 500 statt 404).
    #
    # Bis #1782 lief der Ereignis-Zweig frueh, weil seine ``event_ids`` die
    # nachgelagerten Redaktionen speisten; die IDs sammelt jetzt
    # ``_event_ids_for_client`` getrennt ein. Damit steht der gesamte physische
    # Block hier: schlaegt eine Redaktion fehl, ist NICHTS geloescht — weder
    # Zeilen noch Dateien — und der naechste Lauf faengt sauber von vorne an.
    #
    # Bleibendes Restrisiko: schlaegt ein Unlink INNERHALB dieses Blocks fehl,
    # sind die bereits entfernten Dateien weg. Das ist die Loeschrichtung (kein
    # Datenschutz-, sondern ein Nachweis-Restrisiko) und betrifft nur noch
    # dieses eine, letzte Fenster.
    _delete_event_attachments_for_client(client, event_ids)
    _delete_owned_attachments_for_client(client, case_ids)

    # Refs #1917: Anonymisierung ersetzt das Pseudonym und leert die
    # ``data_json`` der Live-Events — Hausverbote dieser Person verschwinden
    # damit aus dem Banner. Bump bewusst als LETZTER Schritt der (atomaren)
    # Kaskade, mit der VOR der Redaktion gemessenen Banner-Wirkung: nur wenn
    # die Person im Banner stand, gibt es für irgendeine Rolle etwas zu
    # sehen — dormante Bans bleiben unbeobachtbar (Trigger-Seitenkanal).
    bump_ban_revision_for_client_change(client, had_banner_entries=had_banner_entries)

    logger.info(
        "client_anonymized",
        extra={"client_id": str(client.pk), "user_id": getattr(user, "pk", None)},
    )


@transaction.atomic
def create_client(facility, user, **data):
    """Create a client with activity and audit logging.

    M2 P4b (Refs #1058): ``document_type``/``data_json`` laufen durch
    :func:`_clean_client_dynamic_data` — Facility-Scope, ``entity_type=client``
    und die Slug-Whitelist gelten schon beim Anlegen, nicht erst beim Update.
    :func:`_normalize_document_type_kwarg` faengt dabei auch die
    ``document_type_id=<pk>``-Schreibweise ab, die sonst am Guard vorbei direkt
    an ``Client(...)`` durchgereicht wuerde.
    """
    document_type = _normalize_document_type_kwarg(data)
    data["data_json"] = _clean_client_dynamic_data(facility, document_type, data.get("data_json"))
    client = Client(facility=facility, created_by=user, **data)
    client.save()
    log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=client,
        summary=f"Person {client.pseudonym} angelegt",
    )
    audit_client_event(client, user, AuditLog.Action.CLIENT_CREATE)
    return client


def update_client_stage(client, old_stage, new_stage, user):
    """Log a stage change if the contact stage has changed."""
    if old_stage == new_stage:
        return
    # Refs #1093: kein client_pseudonym ins detail (= target_id-Dublette).
    audit_client_event(
        client,
        user,
        AuditLog.Action.STAGE_CHANGE,
        old_stage=old_stage,
        new_stage=new_stage,
    )


@transaction.atomic
def update_client(client, user, *, old_stage=None, expected_updated_at=None, **fields):
    """Update a client with activity logging and stage-change auditing.

    Accepts a dict of allowed fields (pseudonym, contact_stage, age_cluster, notes,
    document_type, data_json). Handles AuditLog for stage changes and Activity for
    qualification transitions.

    old_stage: If provided, used as the previous contact_stage for stage-change
    detection.  When called from a ModelForm view, the form's _post_clean may
    already have mutated the instance, so the caller should capture the stage
    before form.is_valid().

    expected_updated_at: Optional optimistic-locking guard (Refs #531). When set,
    the DB-side ``updated_at`` must match — otherwise a ``ValidationError`` is
    raised so the caller can surface a conflict message.
    """
    check_version_conflict(client, expected_updated_at)
    if old_stage is None:
        old_stage = client.contact_stage

    # Security N2: Die Vier-Augen-Löschung qualifizierter (Art.-9-naher)
    # Dokumentation (``EventDeleteView`` → ``request_deletion``) greift nur,
    # solange der Klient ``QUALIFIED`` ist. Ein Herabstufen QUALIFIED→IDENTIFIED
    # würde diese Eintrittsbedingung aushebeln — danach löschte eine einzelne
    # Kraft das Event allein. Nur Leitung/Admin darf daher herabstufen; der
    # Guard sitzt im Service-Layer (SSOT) und deckt so auch Nicht-Formular-
    # Aufrufer (z.B. Offline-Replay) ab, nicht nur ``ClientUpdateView``.
    new_contact_stage = fields.get("contact_stage", old_stage)
    if (
        old_stage == Client.ContactStage.QUALIFIED
        and new_contact_stage != Client.ContactStage.QUALIFIED
        and not user.is_lead_or_admin
    ):
        raise ValidationError(_("Nur die Leitung darf eine qualifizierte Person herabstufen."))

    # Allowlist (Refs #734): Verhindert Mass-Assignment durch versehentlich
    # durchgereichte Felder (z.B. ``facility``, ``id``, ``created_at``). Liste
    # spiegelt die Felder von ``ClientForm`` wider. Läuft ZUERST — die
    # Mass-Assignment-Prüfung darf nicht vom Diff abhängen.
    allowed = {"pseudonym", "contact_stage", "age_cluster", "notes", "document_type", "data_json"}
    for key in fields:
        if key not in allowed:
            raise ValueError(f"Feld '{key}' darf nicht aktualisiert werden.")

    # M2 P4b (Refs #1058): Dynamikdaten gegen den *effektiven* Typ pruefen —
    # also gegen den neu uebergebenen, sonst gegen den bestehenden. Wird nur der
    # Typ gewechselt, laeuft das bestehende ``data_json`` erneut durch die
    # Whitelist des NEUEN Typs: Slugs des alten Typs fallen weg (E11-Merge-
    # Policy), statt als unsichtbarer Ballast im JSONB liegenzubleiben.
    if "document_type" in fields or "data_json" in fields:
        # ``.get(key, default)`` liefert bei vorhandenem Key auch ein explizites
        # ``None`` zurueck — ein bewusstes Leeren des Typs bleibt also erhalten.
        effective_type = fields.get("document_type", client.document_type)
        raw_data = fields.get("data_json", client.data_json)
        fields["data_json"] = _clean_client_dynamic_data(client.facility, effective_type, raw_data)

    # Refs #1613: Diff gegen den DB-Stand, NICHT gegen die übergebene Instanz.
    # ``ClientForm(instance=client)`` hat die Instanz in ``_post_clean()``
    # bereits mutiert — ein ``getattr(client, key) != value`` sähe hier
    # ``neu == neu`` und schriebe nie ``CLIENT_UPDATE`` (TB6). Nur Feldnamen
    # ins Audit, keine PII-Werte.
    from core.services.audit.diff import changed_fields_vs_db

    changed_fields = changed_fields_vs_db(client, fields)
    for key, value in fields.items():
        setattr(client, key, value)
    # Refs #1917: das Hausverbots-Banner zeigt das Pseudonym — eine echte
    # Umbenennung einer Person MIT Ban-Events muss die Revision bumpen
    # (EXISTS-Guard im Helfer; ohne Ban-Events kein Signal).
    if "pseudonym" in changed_fields:
        from core.services.system.ban_revision import bump_ban_revision_for_client_change

        bump_ban_revision_for_client_change(client)
    # Nur die uebergebenen Spalten schreiben (Refs #1789-Folge): ein voller
    # Save wuerde parallel committete, token-unsichtbare Writes (Soft-Delete,
    # Redaktions-Kaskade — beide bumpen ``updated_at`` nicht) mit dem stalen
    # Instanz-Stand ueberschreiben. ``updated_at`` explizit, da ``auto_now``
    # bei ``update_fields`` sonst nicht schreibt.
    client.save(update_fields=[*fields.keys(), "updated_at"])

    new_stage = client.contact_stage
    update_client_stage(client, old_stage, new_stage, user)

    if old_stage != new_stage and new_stage == Client.ContactStage.QUALIFIED:
        log_activity(
            facility=client.facility,
            actor=user,
            verb=Activity.Verb.QUALIFIED,
            target=client,
            summary=f"{client.pseudonym} qualifiziert",
        )

    if changed_fields:
        audit_client_event(
            client,
            user,
            AuditLog.Action.CLIENT_UPDATE,
            changed_fields=changed_fields,
        )

    log_activity(
        facility=client.facility,
        actor=user,
        verb=Activity.Verb.UPDATED,
        target=client,
        summary=f"Person {client.pseudonym} aktualisiert",
    )

    return client


#: Ablehnungsgrund fuer Schreibzugriffe auf widerrufene Personen (Refs #1771).
#: Bewusst EINE Meldung fuer beide Zustaende (Papierkorb, anonymisiert): welcher
#: der beiden vorliegt, ist fuer die Schreibende folgenlos — beide sind
#: endgueltig, und die Unterscheidung waere nur ein zusaetzliches Detail ueber
#: eine bereits geloeschte Person.
CLIENT_WRITE_REVOKED_MESSAGE = _(
    "Diese Person wurde gelöscht oder anonymisiert — es können keine Daten mehr zu ihr gespeichert werden."
)


def resolve_writable_client(facility, client_id, *, missing_error):
    """Loest eine Personen-UUID fuer einen SCHREIBZUGRIFF auf (Refs #1771).

    Der gemeinsame Kern der drei ``clean_client``-Methoden (``EventMetaForm``,
    ``CaseForm``, ``WorkItemForm``). Zwei Grenzen, in dieser Reihenfolge:

    1. **Facility-Scope** (Refs #819/#1423) — eine fremde oder unbekannte UUID
       ist von aussen ununterscheidbar „existiert nicht"; der Aufrufer liefert
       die Meldung (``missing_error``), damit die gewachsenen Formular-Texte
       unveraendert bleiben.
    2. **Widerruf** — ``is_deleted=True`` (Papierkorb) ODER ``is_active=False``
       (anonymisiert). Der ``FacilityScopedManager`` filtert **keinen** von
       beiden; ohne diesen Schritt schreibt ein Offline-Replay oder ein stales
       Formular frischen Klartext an einen bereits redigierten Datensatz.

    ``is_active=False`` ist dabei der Zustand, den ein reines
    ``is_deleted``-Gate nicht faengt: die K-Anon-Bridge
    (:func:`core.retention.anonymization.anonymize_clients`) anonymisiert
    Personen, deren Ereignisse alle soft-geloescht sind, **ohne** sie in den
    Papierkorb zu legen.

    Beide Faelle sind ein ``ValidationError`` am Formularfeld. Ueber den
    bestehenden 422-Replay-Contract (:func:`core.views._json_contracts
    ._invalid_form_response`) landet ein Offline-Replay damit als
    ``localStatus: "dead"`` in der Dead-Letter-UI — nicht als 409/429/5xx, die
    ``offline-queue.js`` erneut ausspielen wuerde.
    """
    scoped = Client.objects.for_facility(facility) if facility else Client.objects.all()
    try:
        client = scoped.get(pk=client_id)
    except Client.DoesNotExist as exc:
        raise ValidationError(missing_error) from exc
    if client.is_deleted or not client.is_active:
        raise ValidationError(CLIENT_WRITE_REVOKED_MESSAGE)
    return client


def get_client_or_none(facility, client_id):
    """Lookup a client by PK scoped to *facility*.

    Returns None if the client doesn't exist, the ID isn't a valid UUID, or
    the client belongs to a different facility. Gedacht für View-GET-Handler,
    die einen vorselektierten Client aus der URL/Query laden wollen, ohne bei
    fehlendem/ungültigem Parameter eine 404 zu werfen — stattdessen wird das
    Formular unverändert angezeigt.

    Refs #598.
    """
    if not client_id:
        return None
    try:
        return Client.objects.get(pk=client_id, facility=facility)
    except (Client.DoesNotExist, ValueError, ValidationError):
        # Django wirft bei ungültigen UUID-Strings ValidationError (nicht
        # ValueError) — beide Fälle werden als "not found" behandelt.
        return None


def track_client_visit(user, client, facility):
    """Record that a user visited a client detail page.

    Upserts a ``RecentClientVisit`` row und prunt anschliessend die Historie
    dieser Nutzer:in — zweistufig:

    1. **Anzahl** — nur die ``RECENT_VISIT_MAX_PER_USER`` juengsten Besuche
       bleiben (Refs #642).
    2. **Alter** — Zeilen aelter als ``RECENT_VISIT_MAX_AGE_DAYS`` fallen
       zusaetzlich raus (Refs #1761). Ohne diese Frist lebte eine Besuchsspur
       unbegrenzt weiter, solange dieselbe Person in den Top N blieb; das
       widerspricht der Datensparsamkeit, auch wenn die Zeile selbst keinen
       Klartext traegt (nur FKs + ``visited_at``).

    Beide Schritte sind bewusst an ``user`` gescopt: der Prune haengt am
    Schreibpfad dieser Nutzer:in (kein Cron), und der Index
    ``(user, facility, -visited_at)`` traegt beide Abfragen. Die Zeilen einer
    **anonymisierten** Person raeumt nicht dieser Pfad ab, sondern
    :func:`_delete_recent_visits` im Anonymisierungs-Service — sofort und ueber
    alle Nutzer:innen hinweg.
    """
    RecentClientVisit.objects.update_or_create(
        user=user,
        client=client,
        defaults={"facility": facility},
    )
    # Prune (Anzahl): Subquery-Variante spart einen Roundtrip gegenüber
    # "erst SELECT LIMIT N holen, dann DELETE" — Refs #642.
    keep_qs = (
        RecentClientVisit.objects.filter(user=user).order_by("-visited_at").values("pk")[:RECENT_VISIT_MAX_PER_USER]
    )
    RecentClientVisit.objects.filter(user=user).exclude(pk__in=keep_qs).delete()
    # Prune (Alter): Refs #1761.
    age_cutoff = timezone.now() - timedelta(days=RECENT_VISIT_MAX_AGE_DAYS)
    RecentClientVisit.objects.filter(user=user, visited_at__lt=age_cutoff).delete()


# --- Vier-Augen-Lösch-Workflow für Personen (Refs #626) ---


def get_pending_deletion_request(client):
    """Der laufende (PENDING) Löschantrag einer Person — sonst ``None``.

    Refs #1736 (A): eine Quelle für alle Anzeigeflächen (Detail-Banner/-Badge,
    Antragsseite, Idempotenz-Zweig der Antrags-View). ``requested_by`` wird
    mitgeladen, weil jede dieser Flächen die antragstellende Person nennt.
    """
    return (
        DeletionRequest.objects.filter(
            facility_id=client.facility_id,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client.pk,
            status=DeletionRequest.Status.PENDING,
        )
        .select_related("requested_by")
        .first()
    )


def pending_deletion_client_ids(facility, clients) -> set:
    """PKs derjenigen ``clients``, für die ein PENDING-Löschantrag läuft.

    Refs #1736 (A): Badge in der Personenliste. Bewusst nur für die bereits
    paginierte Seite — eine Query je Seite statt einer je Zeile.
    """
    ids = [c.pk for c in clients]
    if not ids:
        return set()
    return set(
        DeletionRequest.objects.for_facility(facility)
        .filter(target_type=DeletionRequest.TargetType.CLIENT, target_id__in=ids, status=DeletionRequest.Status.PENDING)
        .values_list("target_id", flat=True)
    )


def request_client_deletion(client, user, reason):
    """Stellt einen Vier-Augen-Löschantrag für eine Person.

    Idempotent: wenn bereits ein PENDING-Antrag existiert, wird dieser
    zurückgegeben (analog zu Event-Pfad in services/event.request_deletion).
    Im Idempotenz-Fall wird KEIN neues Audit-Event geschrieben
    (Refs #932) — der DELETION_REQUESTED-Eintrag existiert bereits vom
    ersten Aufruf.

    Aufruf: Fachkraft+ aus dem UI. Genehmigung erfolgt durch Leitung
    via :func:`approve_client_deletion`.
    """
    existing = get_pending_deletion_request(client)
    if existing is not None:
        return existing
    dr = DeletionRequest.objects.create(
        facility=client.facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=client.pk,
        reason=reason,
        requested_by=user,
    )
    audit_event(
        action=AuditLog.Action.DELETION_REQUESTED,
        user=user,
        facility=client.facility,
        target_type="DeletionRequest",
        target_id=str(dr.pk),
        # Refs #1093: reason nicht ins detail duplizieren — er lebt in
        # DeletionRequest.reason (via target_id=dr.pk erreichbar).
        detail={"target_client": str(client.pk)},
    )
    return dr


@transaction.atomic
def approve_client_deletion(deletion_request, reviewer):
    """Genehmigt einen Client-Löschantrag und führt den Soft-Delete aus.

    Setzt ``is_deleted=True`` (Mixin-Methode), schreibt AuditLog und
    schließt den DeletionRequest. Nach Ablauf der Papierkorb-Frist
    (``Settings.client_trash_days``) anonymisiert ``enforce_retention``
    den Datensatz automatisch.

    TOCTOU-Fix (Refs #1376, I9): analog ``approve_deletion`` (Event-Pfad) —
    der Antrag wird innerhalb der Transaktion per ``select_for_update()``
    neu geladen und der Status erneut geprueft, damit zwei zeitgleiche
    Approve-Aufrufe fuer denselben (noch PENDING gelesenen) Antrag nicht
    beide den Soft-Delete ausloesen.
    """
    if deletion_request.requested_by_id == reviewer.pk:
        raise ValidationError("Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip).")
    # Refs #1053: Genehmiger-Pool wird über das Recht kuratiert (SSoT hier,
    # nicht nur in der View).
    if not reviewer.can_confirm_deletion:
        raise ValidationError("Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip).")

    # Refs #1376 (I9): facility-scoped Lookup wahrt die RLS-Scopung, der Lock
    # haelt die Zeile bis zum Commit dieser Transaktion.
    deletion_request = DeletionRequest.objects.select_for_update().get(
        pk=deletion_request.pk, facility=deletion_request.facility
    )
    if deletion_request.status != DeletionRequest.Status.PENDING:
        raise ValidationError("Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend).")

    client = Client.objects.get(pk=deletion_request.target_id, facility=deletion_request.facility)
    client.soft_delete(user=reviewer)

    deletion_request.status = DeletionRequest.Status.APPROVED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()

    # Refs #1093: detail traegt nur die DeletionRequest-PK. Pseudonym
    # (= target_id), Antragsteller und reason leben strukturiert in der
    # DeletionRequest-Zeile — keine Klienten-PII-Dublette im append-only-
    # AuditLog (loest die Spannung Art. 17 vs. Art. 5(2) write-time auf).
    audit_client_event(
        client,
        reviewer,
        AuditLog.Action.CLIENT_SOFT_DELETED,
        deletion_request=str(deletion_request.pk),
    )
    log_activity(
        facility=client.facility,
        actor=reviewer,
        verb=Activity.Verb.DELETED,
        target=client,
        summary=f"Person {client.pseudonym} in den Papierkorb verschoben",
    )
    return client


@transaction.atomic
def reject_client_deletion(deletion_request, reviewer):
    """Lehnt einen Client-Löschantrag ab — Status→REJECTED, kein Soft-Delete."""
    if deletion_request.requested_by_id == reviewer.pk:
        raise ValidationError("Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip).")
    deletion_request.status = DeletionRequest.Status.REJECTED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()


@transaction.atomic
def restore_client(client, user):
    """Holt eine soft-deletete Person aus dem Papierkorb zurück."""
    if not client.is_deleted:
        raise ValidationError("Person ist nicht im Papierkorb.")
    client.restore()
    # Refs #1093: kein Pseudonym ins detail (= target_id-Dublette).
    audit_client_event(
        client,
        user,
        AuditLog.Action.CLIENT_RESTORED,
    )


@transaction.atomic
def anonymize_client_now(client, user):
    """Anonymisiert eine Person im Papierkorb sofort — auf Admin-Anforderung.

    Refs #1737 (Option B): Bisher war die Anonymisierung ausschliesslich der
    naechtliche ``enforce_retention``-Lauf nach Ablauf von
    ``Settings.client_trash_days``. Eine Loesch-Blockade durch einen Datensatz
    im Papierkorb (``document_type``/``data_json`` bleiben beim Soft-Delete
    erhalten) liess sich deshalb nur durch Wiederherstellen — also eine
    kurzzeitige Datenresurrektion — oder 30 Tage Warten aufloesen. Der Weg ist
    zugleich fuer Art.-17-Sofortloeschungen nuetzlich.

    Zwei harte Voraussetzungen, beide als deutsche ``ValidationError`` (der
    Aufrufer ist eine View mit Meldungs-Fläche):

    (a) **Nur aus dem Papierkorb.** Ein Direktpfad an der Vier-Augen-Genehmigung
        vorbei entstuende sonst durch die Hintertuer.
    (b) **Kein aktiver Legal Hold** — weder auf der Person noch auf einem ihrer
        Ereignisse. Exakt die Ausschluesse der beiden Retention-Einstiege
        (Refs #1066): eine manuelle Aktion darf einen Hold nicht aushebeln
        (Spoliation). Seit #1756 ueber den gemeinsamen
        :func:`core.retention.legal_holds.client_has_active_hold` statt einer
        eigenen Kopie der Regel.

    Der Datensatz bleibt danach soft-geloescht — anonymisiert, nicht auferstanden.
    """
    if not client.is_deleted:
        raise ValidationError(_("Nur Personen im Papierkorb können anonymisiert werden."))

    if client_has_active_hold(client):
        raise ValidationError(
            _("Für diese Person besteht eine Aufbewahrungssperre (Legal Hold) — Anonymisierung nicht möglich.")
        )

    anonymize_client(client, user=user)
    audit_client_event(
        client,
        user,
        AuditLog.Action.CLIENT_ANONYMIZED,
        trigger="manual",
        deleted_at=str(client.deleted_at),
    )
    return client


def anonymize_eligible_soft_deleted_clients(facility, settings_obj, *, dry_run=False):
    """Anonymisiert soft-deletete Personen, deren Papierkorb-Frist abgelaufen ist.

    Aufruf aus enforce_retention. Findet alle Clients in dieser Facility
    mit ``is_deleted=True`` und ``deleted_at`` älter als
    ``settings_obj.client_trash_days``, ruft :func:`anonymize_client` auf.

    Refs #1066: Klienten mit aktivem Legal Hold — direkt (``target_type
    "Client"``) oder auf einem ihrer Events — sind ausgeschlossen, analog
    zur Event-Retention (:mod:`core.retention.enforcement`). Sonst würde
    das Löschen des Eltern-Klienten einen Event-Hold aushebeln
    (Spoliation): Anonymisierung wird aufgeschoben, bis der Hold
    aufgehoben/abgelaufen ist; ein späterer Lauf holt sie nach. Refs #1756:
    Der Ausschluss liegt seither in
    :func:`core.retention.legal_holds.exclude_clients_with_active_holds` —
    die K-Anon-Bridge (``anonymize_clients``) nutzt denselben Helfer, damit
    beide Anonymisierungs-Einstiege nicht erneut auseinanderlaufen.

    Returns: Anzahl anonymisierter Datensätze.
    """
    cutoff = timezone.now() - timedelta(days=settings_obj.client_trash_days)
    qs = exclude_clients_with_active_holds(
        Client.objects.for_facility(facility).filter(is_deleted=True, deleted_at__lt=cutoff),
        facility,
    )
    if dry_run:
        return qs.count()

    count = 0
    for client in qs:
        anonymize_client(client, user=None)
        audit_client_event(
            client,
            None,
            AuditLog.Action.CLIENT_ANONYMIZED,
            trigger="trash_days_expired",
            deleted_at=str(client.deleted_at),
        )
        count += 1
    return count
