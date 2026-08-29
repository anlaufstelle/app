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
from core.services.audit import (
    apply_allowlisted_update,
    audit_client_event,
    audit_event,
    check_update_allowlist,
)
from core.services.client.restriction import assert_processing_allowed
from core.services.client.targets import client_activity_targets_q, client_legal_hold_targets_q
from core.services.dashboard import log_activity
from core.services.dynamic_data import clean_dynamic_data
from core.services.events.history_diff import build_anonymization_marker
from core.services.security import check_version_conflict
from core.services.system import bypass_replication_triggers

logger = logging.getLogger(__name__)


_REDACTED_HISTORY_MARKER = build_anonymization_marker()
_CLIENT_DOCUMENT_TYPE_ERROR = _("Der Dokumentationstyp ist nicht für Personen vorgesehen.")


def _normalize_document_type_kwarg(data: dict) -> DocumentType | None:
    """Normalisiert beide FK-Schreibweisen auf EIN ``document_type``-Objekt.

    ``create_client`` reicht ``**data`` unveraendert an ``Client(...)`` durch,
    und Django akzeptiert dort neben ``document_type=<obj>`` auch
    ``document_type_id=<pk>``. Ein Guard, der nur ``data["document_type"]``
    liest, waere fuer die zweite Schreibweise blind — ein facility-fremder oder
    ereignis-gebundener Typ landete dann an der Zeile und fiele erst spaeter
    (FK/RLS/Verschluesselung) und ohne fachliche Meldung auf.

    Die ``*_id``-Variante wird daher hier aufgeloest und durch das Objekt
    ersetzt, sodass :func:`~core.services.dynamic_data.clean_dynamic_data`
    **genau einen** Pfad zu pruefen hat. Unbekannte oder syntaktisch ungueltige
    PKs werden als ``ValidationError`` abgewiesen (fail-closed), ebenso das
    gleichzeitige Setzen beider Schreibweisen — das waere mehrdeutig, weil die
    Reihenfolge der ``kwargs`` entschiede, welcher Wert gewinnt.
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


def _redact_client_identity(client) -> None:
    """Ersetze Klartext-Felder am Client durch anonyme Marker.

    M2 P4b (Refs #1058, Entscheid E7): ``data_json`` traegt seit P4b freie
    Feldwerte (auch verschluesselte) und wird mit geleert; ``document_type``
    faellt auf ``NULL`` zurueck, damit der ``PROTECT``-Verweis anonymisierte
    Bestaende nicht dauerhaft an einen Konfigurationstyp kettet (der Typ waere
    sonst nie loeschbar, obwohl die Daten weg sind).

    Refs #2013: ``anonymized_at`` wird hier gesetzt und nicht erst in
    :func:`anonymize_client` — Muster ``_redact_case_content``. Der Stempel
    gehoert an den Ort, der den Inhalt tatsaechlich entfernt. Ein bereits
    gesetzter Stempel bleibt stehen: er datiert die **erste** Tilgung, spaetere
    Laeufe faenden ohnehin nichts mehr vor.
    """
    client.pseudonym = f"Gelöscht-{str(client.pk)[:8]}"
    client.notes = ""
    client.age_cluster = client.AgeCluster.UNKNOWN
    # Refs #2047: Symmetrie zu ``age_cluster``. Der Hard-Pfad zerstoerte
    # bisher genau EINEN der beiden Quasi-Identifikatoren — eine Asymmetrie
    # ohne Begruendung: die Analogie zu ``k_anonymize_client`` traegt sie
    # nicht, denn die Primitive laesst BEIDE Felder stehen (sie braucht sie
    # fuer die Aequivalenzklasse), waehrend der Hard-Pfad gar keine
    # k-Zusicherung gibt und deshalb auch keinen der beiden braucht.
    # ``IDENTIFIED`` ist der informationsaermste Wert der Werteliste.
    client.contact_stage = client.ContactStage.IDENTIFIED
    client.is_active = False
    client.data_json = {}
    client.document_type = None
    client.anonymized_at = client.anonymized_at or timezone.now()
    client.save(
        update_fields=[
            "pseudonym",
            "notes",
            "age_cluster",
            "contact_stage",
            "is_active",
            "data_json",
            "document_type",
            "anonymized_at",
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


#: Chunk-Groesse des Generalisierungs-Laufs. Bewusst klein gehalten: der Lauf
#: sitzt in derselben Transaktion wie die uebrige Kaskade, ein einzelnes
#: ``bulk_update`` ueber zehntausende Zeilen haelt sonst unnoetig lange Locks.
_TIMESTAMP_BUCKET_CHUNK = 500


def _generalize_event_timestamps(event_ids: list) -> None:
    """Generalisiert ``Event.occurred_at`` auf den Monatsersten 00:00 (Refs #2047).

    **Warum ueberhaupt.** Nach der Tilgung blieb je Tombstone die vollstaendige,
    personenverknuepfte Timeline aus exakten Zeitpunkten × Dokumentationstyp ×
    Fallstruktur stehen. Die k-Anonymitaets-Zusicherung deckt sie nicht ab: sie
    attestiert ``(age_cluster, contact_stage)``, nicht das zeitlich
    hochdimensionale Muster. Mit Aussenwissen („Krisenintake am 03.05. gegen
    halb drei") bleibt ein Single-out bei Einrichtungsgroessen plausibel — der
    Datensatz waere damit pseudonymisiert, nicht anonym.

    **Warum Monat und nicht Tag.** Die Statistik aggregiert auf Monatsebene
    (``TruncMonth``), der Informationsverlust ist dort also null; gegenueber
    Tagesgenauigkeit ist der Monatsbucket zugleich deutlich robuster gegen
    Single-out ueber Aussenwissen. Die Zeile selbst bleibt vollstaendig
    erhalten — dies ist eine Generalisierung, keine Loeschung.

    **Warum lokal und nicht in UTC.** Die Bucket-Grenze liegt im lokalen
    Kalender (``TIME_ZONE``). Ein Ereignis am 1. Maerz um 00:30 Berliner Zeit
    liegt in UTC noch im Februar; ein UTC-Bucket schoebe es in den Vormonat und
    verfaelschte genau die Monatsstatistik, deren Erhalt die Abwaegung traegt.

    **Warum ``bulk_update`` statt ``save()``.** ``Event.save()`` laeuft durch
    das ``pre_save``-Signal, das ``search_text`` neu berechnet; ohne
    ``search_text`` in ``update_fields`` persistierte der Neuwert nicht (die
    #1092-Falle). Der Zeitstempel hat mit dem Suchtext nichts zu tun — ein
    reines ``UPDATE`` auf die eine Spalte ist der praezisere Eingriff und
    laesst ``updated_at`` bewusst unberuehrt: die inhaltliche Aenderung der
    Zeile war die Redaktion, nicht dieser Bucket.

    Idempotent: ein bereits gebucketeter Wert faellt aus der Aenderungsmenge.
    """
    from core.models.event import Event

    pending: list = []
    for event in Event.objects.filter(pk__in=event_ids).only("pk", "occurred_at").iterator():
        if event.occurred_at is None:
            continue
        local = timezone.localtime(event.occurred_at)
        bucket = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if bucket == local:
            continue
        event.occurred_at = bucket
        pending.append(event)
        if len(pending) >= _TIMESTAMP_BUCKET_CHUNK:
            Event.objects.bulk_update(pending, ["occurred_at"])
            pending.clear()
    if pending:
        Event.objects.bulk_update(pending, ["occurred_at"])


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


def _redact_legal_holds(client, event_ids: list) -> None:
    """Redigiere ``LegalHold.reason`` fuer alle Hold-Ziele der Person (Refs #1751, #1818, #1990).

    ``reason`` ist bestimmungsgemaess Freitext mit Fall-/Klientenkontext und
    kannte bisher **keinen** Redaktions- oder Pruning-Pfad: abgelaufene und
    aufgehobene Holds blieben als Zeilen fuer immer stehen.

    Redigiert wird nur der Freitext — Ziel, Fristen, Ersteller und
    Aufhebungs-Metadaten bleiben fuer den Nachweis-Trail erhalten (die Zeile
    belegt weiterhin, DASS eine Aufbewahrungspflicht bestand).

    Die Zielmenge stammt seit #1990 aus
    :func:`core.services.client.targets.client_legal_hold_targets_q` — demselben
    Builder, den die Art.-15-Auskunft
    (:func:`core.services.client.export._gather_legal_holds`) benutzt. Der
    **Fall**-Zweig (``target_type="Case"``, ADR-044/Refs #1818) stand vorher nur
    hier und wurde ueber ein eigenes ``case_ids``-Argument hereingereicht; die
    Auskunft kannte ihn nicht. Der Parameter entfaellt deshalb: die Fall-IDs
    leitet der Builder aus ``client.cases`` ab — dieselbe Menge, die
    :func:`_redact_cases_and_episodes` zurueckgab, aber aus EINER Quelle fuer
    beide Seiten. Der ``Activity``-Zweig kam mit demselben Ticket dazu.

    Hinweis zur Reichweite: ein **aktiver** Hold blockiert die Anonymisierung
    an beiden Retention-Einstiegen — ``anonymize_eligible_soft_deleted_clients``
    (Refs #1066) und der K-Anon-Bridge ``anonymize_clients`` (Refs #1756) —,
    sodass in der Praxis nur aufgehobene/abgelaufene Holds hier ankommen. Der
    Filter unterscheidet trotzdem nicht nach Aktivitaet: waere ein aktiver Hold
    vorhanden, waere bereits die Anonymisierung selbst der Fehler — nicht
    diese Redaktion.
    """
    from core.models.retention import LegalHold

    targets = client_legal_hold_targets_q(client, event_ids)
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
    """Setze Activity-Kontext der Person defensiv auf einen sicheren Marker.

    Seit #1972 speichern Activities keinen Anzeigetext mehr, sondern nur einen
    geschlossenen Aktionscode. Das Zuruecksetzen ist fuer den regulaeren Pfad
    daher nicht mehr noetig, bleibt aber als Defense-in-Depth: sollte ein
    kuenftiger Schema-Schritt doch Kontextwerte ergaenzen, ueberlebt davon nach
    der Anonymisierung nichts ausser Verb, Ziel-Referenz und Redaktionsmarker.

    Der gemeinsame Ziel-Builder loest Fall- und WorkItem-IDs ueber den Client
    auf. Damit bleibt die Signatur stabil und **beide** Anonymisierungspfade
    profitieren ohne zusaetzliche Verdrahtung.

    Refs #1067.
    """
    targets = client_activity_targets_q(client, event_ids)
    Activity.objects.for_facility(client.facility).filter(targets).update(
        context={"schema": 1, "action": Activity.Action.REDACTED.value}
    )


def _redact_merged_source_references(client) -> None:
    """Redigiere die Quell-Spuren der GEWANDERTEN Ereignisse eines Merge-Tombstones (Refs #1876).

    Nach einem Merge (ADR-045) sammelt ``_event_ids_for_client(source)`` 0
    Zeilen — alle Ereignisse haengen laengst am Ziel. Die Kaskade oben traefe
    damit genau die Metadaten nicht mehr, die sie ohne Merge redigiert
    (#1751-Linie): Activity-Kontext der Ereignis-Zeilen (Zeitstrom,
    ``retention_activities_days`` = Default 365 Tage bzw. bis zur Redaktion
    des ZIELS), ``RetentionProposal.details["pseudonym"]``,
    ``DeletionRequest.reason`` und abgelaufene ``LegalHold.reason`` mit
    Event-Ziel — alle mit dem Klartext-Pseudonym der Quelle.

    Die Ereignis-IDs kommen aus dem ``moved_events``-Inventar der
    ``CLIENT_MERGED``-Audit-Zeile — bewusst DERSELBE Ort wie das
    Undo-Inventar (ADR-045 §2): Rechenschafts-Spur und Umkehr-Inventar sind
    dieselbe Information. Fehlt die Zeile (Alt-/Reparaturzustand), faellt die
    Mit-Redaktion aus statt den Retention-Lauf zu reissen — geloggt, damit der
    Zustand sichtbar wird; die Zeilen fallen spaetestens mit der Redaktion des
    Ziels.

    Redigiert wird NICHT beim Merge selbst: im Undo-Fenster muessen die
    Zeilen intakt bleiben (``unmerge_client`` stellt den Zustand wieder her).
    Und es wird NUR quell-bezogen redigiert, begrenzt auf zwei Achsen:

    * **Nur Metadaten, keine Ereignis-Inhalte des Ziels** — ``EventHistory``,
      ``data_json`` und Anhaenge der gewanderten Ereignisse gehoeren jetzt zur
      lebenden Akte des Ziels und fallen erst mit dessen eigener Redaktion.
    * **Nur Zeilen bis zum Merge-Zeitpunkt** (``timestamp`` der Audit-Zeile):
      was danach zu einem gewanderten Ereignis entsteht, traegt den Kontext
      des ZIELS (der Retention-Scan schreibt jetzt dessen Pseudonym, ein
      neuer Antrag begruendet sich gegen dessen Akte — ein PENDING-Antrag
      darf seine Vier-Augen-Begruendung nicht verlieren).

    Die Marker sind dieselben wie in den ``_redact_*``-Helfern oben; die
    Meta-Spuren (Antrags-Status, Hold-Fristen, Vorschlags-Nachweis, Verb +
    Zeitpunkt im Zeitstrom) bleiben wie dort erhalten.
    """
    if client.merged_into_id is None:
        return
    row = (
        AuditLog.objects.filter(
            facility=client.facility,
            action=AuditLog.Action.CLIENT_MERGED,
            target_type="Client",
            target_id=str(client.pk),
        )
        .order_by("-timestamp")
        .first()
    )
    # Dieselbe Konsistenz-Bedingung wie ``_merge_inventory`` (merge.py) — aber
    # degradierend statt abbrechend: eine fehlende/fremde Inventar-Zeile darf
    # den Retention-Lauf nicht reissen, die Redaktion der Person selbst laeuft.
    if row is None or row.detail.get("merged_into") != str(client.merged_into_id):
        logger.warning(
            "client_merge_inventory_missing",
            extra={"client_id": str(client.pk)},
        )
        return
    moved_event_ids = row.detail.get("moved_events") or []
    if not moved_event_ids:
        return
    merged_at = row.timestamp

    from django.contrib.contenttypes.models import ContentType

    from core.models.event import Event
    from core.models.retention import LegalHold, RetentionProposal

    Activity.objects.for_facility(client.facility).filter(
        target_type=ContentType.objects.get_for_model(Event),
        target_id__in=moved_event_ids,
        created_at__lte=merged_at,
    ).update(context={"schema": 1, "action": Activity.Action.REDACTED.value})
    DeletionRequest.objects.filter(
        target_type=DeletionRequest.TargetType.EVENT,
        target_id__in=moved_event_ids,
        created_at__lte=merged_at,
    ).update(reason="[Anonymisiert]")
    LegalHold.objects.for_facility(client.facility).filter(
        target_type="Event",
        target_id__in=moved_event_ids,
        created_at__lte=merged_at,
    ).update(reason="[Anonymisiert]")
    proposals = RetentionProposal.objects.filter(
        target_type=RetentionProposal.TargetType.EVENT,
        target_id__in=moved_event_ids,
        proposed_at__lte=merged_at,
    )
    for proposal in proposals.iterator():
        details = proposal.details or {}
        if "pseudonym" not in details:
            continue
        proposal.details = {**details, "pseudonym": "[Anonymisiert]"}
        proposal.save(update_fields=["details"])


@transaction.atomic
def anonymize_client(client, user=None):
    """DSGVO-konforme Aggregat-Anonymisierung eines Klienten (Refs #715, #743).

    Klartext-Felder werden entfernt, der Datensatz bleibt für Statistik-
    Aggregate erhalten (Events behalten ``client_id`` per ``SET_NULL``-FK).
    Anonymisierung erfasst alle Aggregat-Bestandteile, die sonst eine
    Re-Identifikation ermöglichen würden:

    - ``Client``-Felder (pseudonym, notes, age_cluster, contact_stage,
      is_active) — ``contact_stage`` seit Refs #2047 symmetrisch zu
      ``age_cluster``
    - ``Event.occurred_at`` aller Ereignisse der Person wird auf den
      Monatsersten 00:00 im lokalen Kalender generalisiert (Refs #2047) —
      die Timeline exakter Zeitpunkte war der letzte hochdimensionale
      Quasi-Identifikator am Tombstone
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
      Events und ``LegalHold.reason`` für Client-, Event-, Fall- und
      Aktivitäts-Ziele (Refs #1751, #1818, #1990) — auch hier bleibt die
      Nachweis-Metadatenspur unangetastet. Die Zielmenge kommt seit #1990 aus
      demselben Builder wie die Art.-15-Auskunft
      (``client_legal_hold_targets_q``); der ``Activity``-Zweig deckt Holds
      auf den Aktivitäten dieser Person (Ziel-ID = ``Activity.pk``).
    - ``Activity.context`` für Client-, Event-, WorkItem- und Case-Targets —
      seit #1972 bereits PII-frei; der Reset auf den Redaktionsmarker bleibt
      als Defense-in-Depth für künftige Schema-Erweiterungen.
    - Bei einem **Merge-Tombstone** (``merged_into`` gesetzt, ADR-045)
      zusätzlich die quell-bezogenen Metadaten der zum Ziel GEWANDERTEN
      Ereignisse (Activity-Kontext als Defense-in-Depth, ``RetentionProposal``-Pseudonym,
      ``DeletionRequest.reason``, ``LegalHold.reason``) — über das
      ``moved_events``-Inventar der ``CLIENT_MERGED``-Audit-Zeile und begrenzt
      auf Zeilen bis zum Merge-Zeitpunkt
      (:func:`_redact_merged_source_references`, Refs #1876).
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
    # Refs #2047: die Zeitachse ist der letzte hochdimensionale Quasi-
    # Identifikator am Tombstone. Nach der Inhalts-Redaktion, damit ein
    # Fehlschlag dort nicht schon Zeitstempel verschoben hat.
    _generalize_event_timestamps(event_ids)
    _redact_event_history(event_ids)
    _redact_deletion_requests(event_ids)
    _redact_client_deletion_request(client)
    _redact_case_deletion_requests(case_ids)
    _redact_rectification_requests(client)
    _redact_retention_proposals(event_ids)
    _redact_legal_holds(client, event_ids)
    _redact_activities(client, event_ids)
    # Merge-Tombstones (ADR-045): die vier Schritte davor sammeln ueber die
    # QUELLE — nach der Wanderung 0 Zeilen. Die quell-bezogenen Metadaten der
    # gewanderten Ereignisse holt dieser Schritt ueber das Audit-Inventar nach.
    _redact_merged_source_references(client)
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
    :func:`~core.services.dynamic_data.clean_dynamic_data` — Facility-Scope,
    ``entity_type=client`` und die Slug-Whitelist gelten schon beim Anlegen, nicht erst beim Update.
    :func:`_normalize_document_type_kwarg` faengt dabei auch die
    ``document_type_id=<pk>``-Schreibweise ab, die sonst am Guard vorbei direkt
    an ``Client(...)`` durchgereicht wuerde.
    """
    document_type = _normalize_document_type_kwarg(data)
    data["data_json"] = clean_dynamic_data(
        facility,
        document_type,
        data.get("data_json"),
        entity_type=DocumentType.EntityType.CLIENT,
        entity_type_error=_CLIENT_DOCUMENT_TYPE_ERROR,
    )
    client = Client(facility=facility, created_by=user, **data)
    client.save()
    log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=client,
        action=Activity.Action.CLIENT_CREATED,
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


def _bump_ban_revision_before_client_save(client, changed_fields) -> None:
    """Keep the ban projection write between assignment and client save."""
    if "pseudonym" not in changed_fields:
        return
    from core.services.system.ban_revision import bump_ban_revision_for_client_change

    bump_ban_revision_for_client_change(client)


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

    Das Art.-18-Gate (Refs #2045) steht VOR dem Versions-Guard: an einer
    eingeschraenkten Person ist der Schreibzugriff unabhaengig davon
    unzulaessig, ob das Formular aktuell war, und die Konfliktmeldung („bitte
    neu laden") wuerde zu einem erneuten Versuch einladen, statt die Rechtslage
    zu nennen. Es liest den Zustand unter ``select_for_update`` — die Sperre
    haelt bis zum Commit dieser (atomaren) Funktion und schliesst damit das
    Fenster zwischen Pruefung und Write; ``apply_allowlisted_update`` sperrt
    dieselbe Zeile danach nur noch erneut. Den Ausstieg fuer den
    Berichtigungs-Flow oeffnet ein modul-privater Kontext, kein Argument
    dieser Funktion (Cross-Review-Befund 4).
    """
    assert_processing_allowed(client, lock=True)
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
    check_update_allowlist(fields, allowed)

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
        fields["data_json"] = clean_dynamic_data(
            client.facility,
            effective_type,
            raw_data,
            entity_type=DocumentType.EntityType.CLIENT,
            entity_type_error=_CLIENT_DOCUMENT_TYPE_ERROR,
        )

    # Refs #1613: Diff gegen den DB-Stand, NICHT gegen die übergebene Instanz.
    # ``ClientForm(instance=client)`` hat die Instanz in ``_post_clean()``
    # bereits mutiert — ein ``getattr(client, key) != value`` sähe hier
    # ``neu == neu`` und schriebe nie ``CLIENT_UPDATE`` (TB6). Nur Feldnamen
    # ins Audit, keine PII-Werte.
    # Refs #1917: Der optionale Hook bewahrt die bestehende Reihenfolge
    # (setattr -> Ban-Revision -> save) fuer echte Pseudonym-Aenderungen.
    changed_fields = apply_allowlisted_update(
        client,
        fields,
        allowed_fields=allowed,
        before_save=_bump_ban_revision_before_client_save,
    )
    new_stage = client.contact_stage
    update_client_stage(client, old_stage, new_stage, user)

    if old_stage != new_stage and new_stage == Client.ContactStage.QUALIFIED:
        log_activity(
            facility=client.facility,
            actor=user,
            verb=Activity.Verb.QUALIFIED,
            target=client,
            action=Activity.Action.CLIENT_QUALIFIED,
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
        action=Activity.Action.CLIENT_UPDATED,
        client_update_kind="renamed" if "pseudonym" in changed_fields else None,
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
    ``CaseForm``, ``WorkItemForm``). Drei Grenzen, in dieser Reihenfolge:

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
    # 3. **Einschraenkung** (Art. 18 DSGVO, Refs #2045) — reversibel, deshalb
    #    eine eigene Meldung und ein eigenes Gate. Eine eingeschraenkte Person
    #    an ein neues Ereignis, einen Fall oder eine Aufgabe zu haengen ist
    #    neue inhaltliche Verarbeitung, nicht blosse Speicherung.
    assert_processing_allowed(client)
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

    Refs #1876: Auch ein PENDING-Merge-Antrag (gesetztes ``merge_into``) wird
    gefunden — gewollt: pro Person läuft immer nur EIN destruktiver Antrag
    (Löschung ODER Zusammenführung), und jede Fläche, die hier fragt, soll
    beide blocken. Nur Merge-Anträge liefert ``get_pending_merge_request``.
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


def pending_merge_client_ids(facility, clients) -> set:
    """PKs derjenigen ``clients``, für die ein PENDING-**Merge**-Antrag läuft.

    Refs #1876: Teilmenge von :func:`pending_deletion_client_ids` (derselbe
    Antragstyp, gesetztes ``merge_into``) — die Personenliste soll die
    Antragsart beim Namen nennen statt jede Zusammenführung als „Löschung
    beantragt" auszuweisen.
    """
    ids = [c.pk for c in clients]
    if not ids:
        return set()
    return set(
        DeletionRequest.objects.for_facility(facility)
        .filter(
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id__in=ids,
            status=DeletionRequest.Status.PENDING,
            merge_into__isnull=False,
        )
        .values_list("target_id", flat=True)
    )


def request_client_deletion(client, user, reason):
    """Stellt einen Vier-Augen-Löschantrag für eine Person — nur der Antrag.

    Duenne Huelle um :func:`create_or_get_client_deletion_request` fuer alle
    Aufrufer, die den Unterschied „neu angelegt" vs. „lief schon" nicht
    brauchen. Wer ihn braucht (die Views, Refs #2015), ruft direkt die
    Tupel-Variante.
    """
    return create_or_get_client_deletion_request(client, user, reason)[0]


@transaction.atomic
def create_or_get_client_deletion_request(client, user, reason):
    """Wie :func:`request_client_deletion`, aber ``(antrag, created)``.

    Refs #2015 (Re-Review): Die Unterscheidung ist kein Komfort, sondern die
    Bedingung dafuer, dass ein paralleler Zweitantrag die richtige Antwort
    bekommt. Seit dem Zeilenlock (Refs #2017) laeuft der Verlierer eines
    echten Rennens naemlich gar nicht mehr in den ``IntegrityError``: Er
    wartet auf der Sperre und bekommt danach hier den bestehenden Antrag
    **regulaer** zurueck. Ohne Flag meldete die View ihm „Loeschantrag
    gestellt" — eine Erfolgsmeldung fuer etwas, das nie passiert ist.
    ``created=False`` traegt dieselbe Aussage wie der ``IntegrityError``:
    es gibt bereits einen laufenden Antrag.

    Idempotent: wenn bereits ein PENDING-Antrag existiert, wird dieser
    zurückgegeben (analog zu Event-Pfad in services/event.request_deletion).
    Im Idempotenz-Fall wird KEIN neues Audit-Event geschrieben
    (Refs #932) — der DELETION_REQUESTED-Eintrag existiert bereits vom
    ersten Aufruf.

    Aufruf: Fachkraft+ aus dem UI. Genehmigung erfolgt durch Leitung
    via :func:`approve_client_deletion`.

    Refs #2017: Ein Antrag entsteht nur gegen eine **lebende** Person. Die
    beiden Ausschluesse spiegeln die Neuvalidierung in
    :func:`approve_client_deletion`:

    * ``merged_into`` gesetzt — ein Merge-Tombstone ist kein Antragsziel; die
      leere Huelle liegt bereits im Papierkorb, und die Genehmigung wuerde ihr
      ``deleted_at`` neu setzen, also die Frist und damit das Undo-Fenster der
      Zusammenfuehrung still verschieben.
    * ``is_deleted`` — dasselbe Argument ohne Merge: ein zweiter Weg in
      denselben Papierkorb, der beim Genehmigen die Frist zurueckdreht.

    Beides wird auf der **frisch gesperrten** Zeile geprueft (Cross-Review
    Lane 2), nicht auf der uebergebenen Instanz: zwischen dem Lookup der View
    und diesem Aufruf kann eine Zusammenfuehrung committen, und der Guard
    liefe dann auf einem veralteten Objekt ins Leere. Der Lock ist derselbe
    wie in ``approve_client_deletion``/``_locked_clients`` — Antrag,
    Genehmigung und Merge serialisieren damit auf derselben Zeile.

    Der View-Filter ``is_deleted=False`` ersetzt diesen Guard nicht: der
    Service ist die SSoT, und er liest vor dem Rennen.
    """
    client = Client.objects.select_for_update().get(pk=client.pk, facility=client.facility_id)
    if client.merged_into_id is not None:
        raise ValidationError(_("Diese Person ist bereits zusammengeführt."))
    if client.is_deleted:
        raise ValidationError(_("Diese Person liegt bereits im Papierkorb."))
    existing = get_pending_deletion_request(client)
    if existing is not None:
        return existing, False
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
    return dr, True


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
    # Refs #1876: Ein Merge-Antrag (gesetztes ``merge_into``) darf nicht als
    # blosser Loeschantrag genehmigt werden — die Quelle landete im
    # Papierkorb, ohne dass irgendetwas zum Ziel wandert. Genehmigung nur
    # ueber ``approve_client_merge`` (services/client/merge.py).
    if deletion_request.merge_into_id is not None:
        raise ValidationError(
            _("Dies ist ein Zusammenführungs-Antrag — er wird über den Zusammenführungs-Weg entschieden.")
        )
    if deletion_request.status != DeletionRequest.Status.PENDING:
        raise ValidationError("Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend).")

    # Refs #2017: Der Zielzustand wird unter dem Lock NEU gelesen. Zwischen
    # Antrag und Genehmigung koennen Minuten bis Tage liegen — genug Zeit fuer
    # eine Zusammenfuehrung oder einen anderen Weg in den Papierkorb. Der Lock
    # serialisiert diese Genehmigung zugleich gegen ``_locked_clients`` im
    # Merge-Pfad: wer zuerst kommt, sieht der jeweils andere danach frisch.
    client = Client.objects.select_for_update().get(pk=deletion_request.target_id, facility=deletion_request.facility)
    if client.merged_into_id is not None:
        raise ValidationError(_("Diese Person ist bereits zusammengeführt."))
    if client.is_deleted:
        # Paritaet zu ``_move_case_to_trash``: ein zweiter Soft-Delete setzte
        # ``deleted_at`` neu und verlaengerte die Aufbewahrung um eine volle Frist.
        raise ValidationError(_("Diese Person liegt bereits im Papierkorb."))
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
        action=Activity.Action.CLIENT_TRASHED,
    )
    return client


@transaction.atomic
def reject_client_deletion(deletion_request, reviewer):
    """Lehnt einen Client-Löschantrag ab — Status→REJECTED, kein Soft-Delete.

    Refs #2012: derselbe Lock + PENDING-Re-Check wie in
    :func:`approve_client_deletion` und in den Pendants
    ``reject_case_deletion``/``reject_client_merge``. Ohne ihn ueberschreibt
    ein zeitgleicher Reject einen bereits genehmigten Antrag auf REJECTED,
    waehrend die Person im Papierkorb liegen bleibt. Anders als in jenen
    beiden Pfaden entsteht dabei nicht einmal eine ``DELETION_REJECTED``-Zeile
    (dieser Weg auditiert den Reject nicht) — der Widerspruch waere also eine
    voellig spurlose Statuskorruption.
    """
    if deletion_request.requested_by_id == reviewer.pk:
        raise ValidationError("Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip).")
    # Refs #2012: facility-scoped Lookup wahrt die RLS-Scopung, der Lock haelt
    # die Zeile bis zum Commit dieser Transaktion.
    deletion_request = DeletionRequest.objects.select_for_update().get(
        pk=deletion_request.pk, facility=deletion_request.facility
    )
    # Refs #1876: Merge-Antraege werden ueber ``reject_client_merge``
    # abgelehnt — jener Weg schreibt die ``DELETION_REJECTED``-Zeile mit
    # Merge-Kontext; hier fiele sie still unter den Tisch.
    if deletion_request.merge_into_id is not None:
        raise ValidationError(
            _("Dies ist ein Zusammenführungs-Antrag — er wird über den Zusammenführungs-Weg entschieden.")
        )
    if deletion_request.status != DeletionRequest.Status.PENDING:
        raise ValidationError(_("Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend)."))
    deletion_request.status = DeletionRequest.Status.REJECTED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()


@transaction.atomic
def restore_client(client, user):
    """Holt eine soft-deletete Person aus dem Papierkorb zurück.

    Refs #1876: Merge-Tombstones (gesetztes ``merged_into``) sind hier
    ausgeschlossen — ein blosses Restore liesse die Quelle ohne ihre
    gewanderten Fälle/Ereignisse auferstehen (Duplikat UND Datenverlust in
    einem). Der konsistente Rückweg ist ``unmerge_client``.
    """
    if client.merged_into_id is not None:
        raise ValidationError(
            _(
                "Diese Person wurde mit einer anderen zusammengeführt — "
                "Wiederherstellen nur über das Aufheben der Zusammenführung."
            )
        )
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

    Refs #2013: ``anonymized_at__isnull=True`` ist die Abbruchbedingung —
    Muster ``anonymize_eligible_soft_deleted_cases``. ``anonymize_client``
    aendert ``deleted_at`` nicht, der Tombstone erfuellt ``is_deleted=True,
    deleted_at<cutoff`` also weiter. Ohne den Filter faende ihn JEDER
    naechtliche Lauf erneut, redigierte den bereits leeren Rest noch einmal und
    schriebe jedes Mal eine neue ``CLIENT_ANONYMIZED``-Zeile in das
    append-only, HMAC-verkettete AuditLog — unbegrenztes Wachstum, dazu ein
    dauerhaft von Null verschiedenes und damit wertloses „Anonymisiert
    N"-Ops-Signal und Ban-Revision-Churn. Bei ueberlappenden Laeufen
    duplizierten sich die Zeilen zusaetzlich.

    Returns: Anzahl anonymisierter Datensätze.
    """
    cutoff = timezone.now() - timedelta(days=settings_obj.client_trash_days)
    qs = exclude_clients_with_active_holds(
        Client.objects.for_facility(facility).filter(
            is_deleted=True, deleted_at__lt=cutoff, anonymized_at__isnull=True
        ),
        facility,
    )
    if dry_run:
        return qs.count()

    # Refs #2013 (Cross-Review): Der Filter oben allein macht den Lauf nur
    # gegen SPAETERE Laeufe idempotent, nicht gegen einen GLEICHZEITIGEN.
    # Zwei ueberlappende ``enforce_retention``-Prozesse (Cron-Ueberlauf,
    # manueller Lauf neben dem naechtlichen) lesen dieselbe Zeile, bevor der
    # erste committet, und wuerden beide redigieren und beide eine
    # ``CLIENT_ANONYMIZED``-Zeile schreiben — genau die Duplikate, die das
    # Ticket nennt. Deshalb je Datensatz: eigene Transaktion, Zeilensperre,
    # und der Marker-Check ein zweites Mal AUF DER GESPERRTEN Zeile. Der
    # Verlierer sieht dann den bereits gesetzten Stempel und ueberspringt.
    #
    # Die PK-Liste wird vorab materialisiert: der Lauf veraendert genau die
    # Spalte, nach der die Queryset-Bedingung filtert.
    count = 0
    for client_pk in list(qs.values_list("pk", flat=True)):
        with transaction.atomic():
            client = (
                Client.objects.select_for_update()
                .filter(pk=client_pk, anonymized_at__isnull=True, is_deleted=True)
                .first()
            )
            if client is None:
                # Ein paralleler Lauf war schneller (oder die Person wurde
                # zwischenzeitlich wiederhergestellt) — nichts zu tun.
                continue
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
