"""Duplikat-Merge fuer Personen (Refs #1876, ADR-045).

Doppelt angelegte Personen (Pseudonym leicht anders geschrieben, Neuanlage
nach erfolgloser Suche) lassen sich hier nachtraeglich zusammenfuehren. Die
drei Produktentscheide der ADR bestimmen die Form:

* **Vier-Augen gestaffelt wie die Loeschung** (ADR-044 P1c): Ist EINE der
  beiden Personen ``QUALIFIED``, braucht der Merge Antrag + Genehmigung durch
  eine Zweitperson (:func:`request_client_merge` /
  :func:`approve_client_merge`). Sind beide ``IDENTIFIED``, darf die Leitung
  direkt zusammenfuehren (:func:`merge_clients`).
* **Quelle wird Tombstone im Papierkorb**: Soft-Delete + ``merged_into``-
  Verweis in derselben Transaktion. Die normale Papierkorb-Frist
  (``client_trash_days``) gilt mit; danach redigiert der bestehende
  Retention-Lauf (``anonymize_eligible_soft_deleted_clients``) den Tombstone —
  inklusive der quell-bezogenen Metadaten der GEWANDERTEN Ereignisse, die die
  Kaskade ueber das ``moved_events``-Inventar der ``CLIENT_MERGED``-Zeile
  nachholt (``_redact_merged_source_references``, die eigenen Queries der
  Kaskade sammeln ueber die Quelle und faenden 0 Zeilen). Der
  ``merged_into``-Verweis ueberlebt die Redaktion (PROTECT, Art.-15-Spur).
  Bis zum Fristablauf ist der Merge per :func:`unmerge_client` umkehrbar.
* **Kontaktstufe: hoechste gewinnt**: Das Ziel landet auf ``QUALIFIED``,
  sobald eine der beiden dort war. Alles andere waere eine verdeckte
  Herabstufung — genau die verbietet ``update_client`` fuer Nicht-Leitung
  (Security N2).

Datenwanderung — alle sechs eingehenden FKs von ``core.Client``:

===================  ==========  ====================================================
Modell               Verhalten   Warum
===================  ==========  ====================================================
Case (PROTECT)       wandert     Faelle gehoeren der Person; ERST Faelle, DANN
                                 Ereignisse (Invariante ``Event.client ==
                                 Event.case.client``, ``services/events/crud.py``).
Event (SET_NULL)     wandert     inkl. soft-geloeschter — der Papierkorb eines
                                 Ereignisses folgt seiner Person.
WorkItem (SET_NULL)  wandert     Aufgaben zur Person bleiben relevant.
RecentClientVisit    wandert     mit Dedup: ``unique_together (user, client)`` —
(CASCADE)                        juengstes ``visited_at`` gewinnt, ``is_favorite``
                                 per OR (:func:`_merge_recent_visits`).
Attachment           teilweise   NUR die Anhaenge uebernommener Datei-Slugs wandern
(CASCADE)                        (Marker-Invariante I-M1, ADR-039 §7: kein
                                 ``__files__``-Marker ohne passende Zeile). Nicht
                                 uebernommene Datei-Werte behaelt die Quelle — sie
                                 fallen mit deren Redaktion ("Ziel behaelt seine
                                 Werte", auf Dateien angewandt).
Rectification-       bleibt      Ein Art.-16-Antrag dokumentiert Aussagen ueber die
Request (CASCADE)                Werte GENAU DIESES Datensatzes; umgehaengt truege
                                 er Behauptungen an eine Akte, gegen die er nie
                                 gestellt wurde (zudem kollidierte der partielle
                                 Unique auf (facility, target_client, field_path,
                                 PENDING)). Der Loeschpfad laesst sie identisch
                                 stehen; die Redaktion des Tombstones leert die
                                 Werte (``_redact_rectification_requests``).
===================  ==========  ====================================================

FK-lose Verweise:

* ``OfflineClientRevocation.client_pk`` — der Merge widerruft die Offline-
  Bundles der QUELLE (:func:`revoke_offline_client_bundles`, Muster #1760):
  bereits gequeuete Offline-Ereignisse zur Quelle laufen danach in die
  Dead-Letter-UI (``CLIENT_WRITE_REVOKED``) — bewusst KEIN Replay-Remap,
  dasselbe Verhalten wie bei Loeschungen (ADR-045, Risiken).
* ``DeletionRequest.target_id`` / ``LegalHold.target_id`` / AuditLog-
  ``target_id`` — bleiben auf der Quelle stehen: das append-only-Protokoll
  und die Antrags-/Hold-Metadaten sind ueber den Tombstone erreichbar
  (ADR-045, Entscheid 2). Ein PENDING-Antrag oder aktiver Hold auf einer der
  beiden Personen blockiert den Merge stattdessen vorab
  (:func:`_validate_merge`).

Audit: EINE ``CLIENT_MERGED``-Zeile mit ``target_obj`` = Quelle. Zwingend
GETRENNT von ``CLIENT_SOFT_DELETED`` und ohne ``DELETION_APPROVED``-Zeile:
``detect_mass_client_destruction`` zaehlt beide ab 20 im Kurzfenster — eine
Aufraeum-Kampagne ueber Alt-Duplikate ist Konsolidieren, kein Vernichten, und
darf keinen Breach-Alarm ausloesen (die Vier-Augen-Evidenz liegt in der
``DeletionRequest``-Zeile plus ``deletion_request`` im ``detail``).

Warum dieser Service NICHT unter dem Redaktions-Paritaets-Guard
(``TestKAnonBridgeRedactionParity``) steht, obwohl er dieselbe Tabellenmenge
beruehrt: der Merge REDIGIERT nichts — er verschiebt Zuordnungen. Die Tilgung
des Tombstones laeuft nach Fristablauf durch ``anonymize_client`` selbst, und
genau dessen Helfer-Menge pinnt der Guard bereits.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import Activity, Attachment, AuditLog, Case, Client, DocumentType, Event, RecentClientVisit
from core.models.workitem import DeletionRequest, WorkItem
from core.retention.legal_holds import client_has_active_hold
from core.services.audit import audit_client_event, audit_event
from core.services.client.main import get_pending_deletion_request, update_client_stage
from core.services.dashboard import log_activity
from core.services.dynamic_data import clean_dynamic_data
from core.services.targets import TARGET_CLIENT, target_q

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Staffelung und Antrag (ADR-045, Entscheid 1)
# ---------------------------------------------------------------------------


def merge_requires_four_eyes(source, target) -> bool:
    """Braucht diese Zusammenfuehrung den Vier-Augen-Antrag?

    Gestaffelt wie der Fall-Loeschpfad (ADR-044 P1c): sobald EINE der beiden
    Personen auf der Stufe „Mit Zusatzangaben" (``QUALIFIED``) steht,
    entscheidet eine zweite Person. Ein Merge ist faktisch die Loeschung des
    einen Pseudonyms — die Loeschschwelle der hoeher geschuetzten Seite gilt.
    """
    qualified = Client.ContactStage.QUALIFIED
    return source.contact_stage == qualified or target.contact_stage == qualified


def get_pending_merge_request(client):
    """Der laufende (PENDING) Merge-Antrag mit dieser Person als QUELLE — sonst ``None``.

    Muster ``get_pending_deletion_request`` (Refs #1736): eine Quelle fuer alle
    Anzeigeflaechen. ``merge_into`` wird mitgeladen, weil jede Flaeche das Ziel
    nennt.
    """
    return (
        DeletionRequest.objects.filter(
            target_q(TARGET_CLIENT, [client.pk]),
            facility_id=client.facility_id,
            status=DeletionRequest.Status.PENDING,
            merge_into__isnull=False,
        )
        .select_related("requested_by", "merge_into")
        .first()
    )


def request_client_merge(source, target, user, reason):
    """Stellt einen Vier-Augen-Merge-Antrag — nur der Antrag.

    Duenne Huelle um :func:`create_or_get_client_merge_request` fuer alle
    Aufrufer, die den Unterschied „neu angelegt" vs. „lief schon" nicht
    brauchen.
    """
    return create_or_get_client_merge_request(source, target, user, reason)[0]


@transaction.atomic
def create_or_get_client_merge_request(source, target, user, reason):
    """Wie :func:`request_client_merge`, aber ``(antrag, created)``.

    Refs #2015 (Re-Review): Muster ``create_or_get_client_deletion_request`` —
    ein paralleler Zweitantrag muss die Idempotenz-Antwort bekommen, nicht
    eine Erfolgsmeldung fuer etwas, das nie passiert ist.

    Traeger ist ein ``DeletionRequest`` mit gesetztem ``merge_into`` — die
    Quelle ist ``target_id``, wie beim Loeschantrag (den sie faktisch enthaelt).
    Der partielle Unique auf (facility, target_type, target_id, PENDING) sorgt
    dafuer, dass pro Person immer nur EIN destruktiver Antrag laeuft — Loeschung
    ODER Merge, nie beides.

    Idempotent wie ``request_client_deletion`` (Muster #932): laeuft bereits
    ein PENDING-Merge-Antrag auf DASSELBE Ziel, wird er ohne zweites
    Audit-Event zurueckgegeben. Ein PENDING-Antrag auf ein ANDERES Ziel (oder
    ein Loeschantrag) blockiert stattdessen (:func:`_validate_merge`).

    Bewusst auch fuer zwei ``IDENTIFIED``-Personen erlaubt: der Antragsweg ist
    strenger als der direkte (eine Zweitperson mit ``can_confirm_deletion``
    entscheidet) — er umgeht also kein Schutzniveau.
    """
    # Art.-18-Gate (Refs #2045): der Merge-ANTRAG war bis zur Fix-Runde 2
    # ungegatet. Anders als ein Art.-17-Loeschantrag ist die Zusammenfuehrung
    # kein Betroffenenrecht, sondern eine Aktenfuehrungs-Entscheidung der
    # Einrichtung — sie darf waehrend bestrittener Richtigkeit nicht einmal
    # beantragt werden. Beide Seiten, wie beim Direkt-Merge.
    #
    # Re-Review 2: Die erste Fassung sperrte Quelle und Ziel EINZELN in
    # semantischer Reihenfolge. Zwei invertierte Antraege (A->B und B->A)
    # nahmen die beiden Client-Zeilen damit ueber Kreuz und konnten
    # Client<->Client deadlocken. ``_locked_clients`` nimmt beide in EINEM
    # ``select_for_update`` in PK-Reihenfolge — dieselbe Ordnung wie
    # Direkt-Merge, Genehmigung und Unmerge — und gatet sie auf den frisch
    # gesperrten Zeilen.
    #
    # Die ``atomic``-Klammer liegt am FUNKTIONSKOPF (Cross-Review-Runde 4).
    # Eine Klammer nur um ``_locked_clients`` endete vor Lookup, Validierung,
    # Create und Audit — beim Direktaufruf fielen die Sperren also VOR der
    # Mutation. Damit war beides offen, was sie schuetzen sollen: die
    # Einschraenkung konnte dazwischen gesetzt werden, und zwei parallele
    # Aufrufe sahen beide ``existing=None``. Muster und Reihenfolge sind jetzt
    # identisch zu ``create_or_get_client_deletion_request``: sperren, lesen,
    # entscheiden, schreiben — alles in EINER Transaktion.
    source, target = _locked_clients(source, target)
    existing = get_pending_merge_request(source)
    if existing is not None and existing.merge_into_id == target.pk:
        return existing, False
    _validate_merge(source, target)
    try:
        # Savepoint um den Insert: in Postgres macht ein ``IntegrityError`` die
        # UMSCHLIESSENDE Transaktion unbrauchbar, ohne ihn koennte der
        # Re-Lookup unten nicht mehr laufen.
        with transaction.atomic():
            dr = DeletionRequest.objects.create(
                facility=source.facility,
                target_type=DeletionRequest.TargetType.CLIENT,
                target_id=source.pk,
                reason=reason,
                requested_by=user,
                merge_into=target,
            )
    except IntegrityError:
        # Backstop hinter der Zeilensperre (Muster #2015): Der Verlierer eines
        # echten Rennens wartet oben auf der Quell-Sperre und findet den
        # Antrag regulaer beim Lookup — dieser Zweig sollte praktisch nie
        # greifen. Er faengt, was die Sperre nicht deckt (etwa ein
        # Loeschantrag, der denselben partiellen Unique belegt) und uebersetzt
        # es in dieselbe idempotente Antwort statt in einen 500er. Passt der
        # gefundene Antrag nicht zu DIESEM Ziel, ist es ein echter Konflikt und
        # der Fehler bleibt.
        existing = get_pending_merge_request(source)
        if existing is None or existing.merge_into_id != target.pk:
            raise
        return existing, False
    audit_event(
        action=AuditLog.Action.DELETION_REQUESTED,
        user=user,
        facility=source.facility,
        target_type="DeletionRequest",
        target_id=str(dr.pk),
        # Refs #1093: ``reason`` nicht ins detail duplizieren — er lebt in
        # DeletionRequest.reason (ueber target_id=dr.pk erreichbar).
        detail={"target_client": str(source.pk), "merge_into": str(target.pk)},
    )
    return dr, True


@transaction.atomic
def approve_client_merge(deletion_request, reviewer):
    """Genehmigt einen Merge-Antrag und fuehrt die Zusammenfuehrung aus.

    Guards wie ``approve_client_deletion`` (Refs #1053/#1376): Reviewer darf
    nicht Antragsteller:in sein, braucht ``can_confirm_deletion``, und der
    Antrag wird per ``select_for_update()`` neu geladen und erneut geprueft
    (TOCTOU) — sonst fuehrten zwei zeitgleiche Genehmigungen den Merge doppelt
    aus.

    Schreibt ``CLIENT_MERGED`` mit ``deletion_request`` im ``detail`` — und
    bewusst KEIN ``DELETION_APPROVED``: jene Zeile zaehlt
    ``detect_mass_client_destruction``, und eine Aufraeum-Kampagne ueber
    Alt-Duplikate darf keinen Breach-Alarm ausloesen (Modul-Docstring).
    Dasselbe Muster faehrt der Personen-Loeschpfad (nur die Domaenen-Zeile,
    anders als der Ereignis-/Fall-Pfad).
    """
    if deletion_request.requested_by_id == reviewer.pk:
        raise ValidationError(_("Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip)."))
    if not reviewer.can_confirm_deletion:
        raise ValidationError(_("Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip)."))

    deletion_request = DeletionRequest.objects.select_for_update().get(
        pk=deletion_request.pk, facility=deletion_request.facility
    )
    if deletion_request.merge_into_id is None:
        raise ValidationError(_("Dies ist kein Zusammenführungs-Antrag."))
    if deletion_request.status != DeletionRequest.Status.PENDING:
        raise ValidationError(_("Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend)."))

    source = Client.objects.get(pk=deletion_request.target_id, facility=deletion_request.facility)
    target = Client.objects.get(pk=deletion_request.merge_into_id, facility=deletion_request.facility)
    source, target = _locked_clients(source, target)
    target = _execute_merge(source, target, reviewer, deletion_request=deletion_request)

    deletion_request.status = DeletionRequest.Status.APPROVED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()
    return target


@transaction.atomic
def reject_client_merge(deletion_request, reviewer):
    """Lehnt einen Merge-Antrag ab — Status→REJECTED, nichts wandert.

    PENDING-Re-Check unter Lock wie ``reject_case_deletion`` (und aus dessen
    Grund): dieser Weg schreibt eine ``DELETION_REJECTED``-Zeile ins
    append-only AuditLog — ohne Guard liesse sich ein bereits genehmigter
    Antrag auf REJECTED zurueckdrehen, waehrend die Quelle zusammengefuehrt
    bleibt, und der Widerspruch waere dauerhaft protokolliert.
    """
    if deletion_request.requested_by_id == reviewer.pk:
        raise ValidationError(_("Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip)."))
    deletion_request = DeletionRequest.objects.select_for_update().get(
        pk=deletion_request.pk, facility=deletion_request.facility
    )
    if deletion_request.merge_into_id is None:
        raise ValidationError(_("Dies ist kein Zusammenführungs-Antrag."))
    if deletion_request.status != DeletionRequest.Status.PENDING:
        raise ValidationError(_("Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend)."))
    deletion_request.status = DeletionRequest.Status.REJECTED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()
    audit_event(
        action=AuditLog.Action.DELETION_REJECTED,
        user=reviewer,
        facility=deletion_request.facility,
        target_type="DeletionRequest",
        target_id=str(deletion_request.pk),
        detail={
            "target_client": str(deletion_request.target_id),
            "merge_into": str(deletion_request.merge_into_id),
        },
    )


@transaction.atomic
def merge_clients(source, target, user):
    """Direkter Merge ohne Antrag — nur Leitung, nur zwei ``IDENTIFIED``-Personen.

    Beide Bedingungen sind Produktentscheide (ADR-045, Entscheid 1) und liegen
    hier im Service (SSoT), nicht nur in der View: die Staffelung wird auf den
    frisch gesperrten Zeilen geprueft, damit eine parallele Hoeherstufung das
    Vier-Augen-Gebot nicht unterlaeuft.
    """
    if not user.is_lead_or_admin:
        raise ValidationError(_("Nur die Leitung darf Personen direkt zusammenführen."))
    source, target = _locked_clients(source, target)
    if merge_requires_four_eyes(source, target):
        raise ValidationError(
            _("Für Personen mit Zusatzangaben ist ein Zusammenführungs-Antrag mit Bestätigung nötig.")
        )
    return _execute_merge(source, target, user)


# ---------------------------------------------------------------------------
# Validierung und Ausfuehrung
# ---------------------------------------------------------------------------


def _locked_clients(source, target):
    """Beide Personen-Zeilen per ``select_for_update`` sperren und frisch laden.

    EIN Statement fuer beide Zeilen (kein Deadlock-Fenster zwischen zwei
    Einzel-Locks); die Rueckgabe ersetzt die moeglicherweise stalen Instanzen
    der Aufruferin.
    """
    from core.services.client.restriction import assert_processing_allowed

    # Art.-18-Gate (Refs #2045) am gemeinsamen Chokepoint der drei
    # Merge-Einstiege (Direkt-Merge, Antrags-Genehmigung, Unmerge). Ein Merge
    # schreibt Faelle, Ereignisse und Anhaenge einer Person auf eine andere um
    # — die weitreichendste inhaltliche Aenderung, die das System kennt, und
    # damit das Gegenteil der blossen Speicherung nach Art. 18 Abs. 2.
    #
    # Geprueft werden BEIDE Seiten: eine eingeschraenkte Quelle darf nicht
    # aufgeloest, eine eingeschraenkte Zielakte nicht befuellt werden.
    #
    # NICHT gegatet ist ``reject_client_merge``: eine Ablehnung bricht den
    # Vorgang ab, statt Daten zu verarbeiten. Sie zu blockieren liesse einen
    # offenen Antrag an einer eingeschraenkten Person unaufloesbar haengen.

    # ``order_by("pk")`` ist Pflicht, nicht Kosmetik (Cross-Review-Runde 2):
    # ohne deterministische Reihenfolge koennen zwei gleichzeitige Merges mit
    # vertauschtem Quell-/Zielpaar dieselben zwei Zeilen ueber Kreuz sperren.
    locked = {c.pk: c for c in Client.objects.select_for_update().filter(pk__in=[source.pk, target.pk]).order_by("pk")}
    for client in locked.values():
        # Auf der bereits gesperrten, frisch gelesenen Zeile — kein zweiter
        # Lock noetig.
        assert_processing_allowed(client.pk)
    try:
        return locked[source.pk], locked[target.pk]
    except KeyError:  # Personen-Zeilen werden redigiert, nie geloescht
        raise ValidationError(_("Person nicht gefunden.")) from None


def _validate_merge(source, target, *, ignore_request=None):
    """Alle Vorbedingungen einer Zusammenfuehrung — als deutsche ``ValidationError``.

    ``ignore_request`` blendet im Genehmigungs-Pfad den GERADE genehmigten
    Antrag aus dem PENDING-Check aus (er ist die Legitimation, kein Konflikt).

    Der Legal-Hold-Check ist bewusst strenger als bei den Soft-Delete-Pfaden
    (die pruefen Holds erst bei der Tilgung): ein Hold friert eine Akte als
    Beweismittel ein, und der Merge strukturiert BEIDE Akten um — auf der
    Quellseite verloere ein Personen-Hold sogar still die Deckung ueber deren
    Ereignisse (``client_has_active_hold`` findet sie nach der Wanderung nicht
    mehr). Spoliations-Schutz analog #1066.
    """
    if source.pk == target.pk:
        raise ValidationError(_("Eine Person kann nicht mit sich selbst zusammengeführt werden."))
    if source.facility_id != target.facility_id:
        raise ValidationError(_("Beide Personen müssen zur selben Einrichtung gehören."))
    for person in (source, target):
        if person.is_deleted:
            raise ValidationError(_("Personen im Papierkorb können nicht zusammengeführt werden."))
        if not person.is_active:
            raise ValidationError(_("Anonymisierte Personen können nicht zusammengeführt werden."))
        if person.merged_into_id is not None:
            raise ValidationError(_("Diese Person ist bereits zusammengeführt."))
        if client_has_active_hold(person):
            raise ValidationError(
                _(
                    "Für eine der beiden Personen besteht eine Aufbewahrungssperre "
                    "(Legal Hold) — Zusammenführen nicht möglich."
                )
            )
        pending = get_pending_deletion_request(person)
        if pending is not None and (ignore_request is None or pending.pk != ignore_request.pk):
            raise ValidationError(
                _("Für eine der beiden Personen läuft bereits ein Lösch- oder Zusammenführungs-Antrag.")
            )
    # Ketten-Guard (Review-Befund zu ADR-045, Entscheid 2): Traegt die QUELLE
    # einen noch unredigierten Tombstone (A→B liegt im Undo-Fenster), wuerde
    # B→C das Undo-Versprechen still brechen — ``unmerge_client(A)`` traefe
    # mit seinen Inventar-Updates (Filter ``client=target``) 0 Zeilen, weil
    # die Daten laengst bei C liegen, und A kaeme als leeres, aktives Duplikat
    # zurueck. Erst aufheben oder die Redaktion des Tombstones abwarten
    # (``anonymized_at`` gesetzt — dessen Undo ist dann ohnehin abgewiesen, die
    # Kette darf weiterwachsen). Das ZIEL darf Tombstones tragen: deren
    # Inventar bleibt beim Ziel, LIFO-Undo traegt (Testbeleg
    # ``TestChainMerge``).
    if source.merged_sources.filter(is_active=True).exists():
        raise ValidationError(
            _(
                "Diese Person trägt eine noch rückgängig machbare Zusammenführung — "
                "erst aufheben oder den Ablauf der Papierkorb-Frist abwarten."
            )
        )


def _assert_event_case_consistency(*clients):
    """Invariante ``Event.client == Event.case.client`` (``services/events/crud.py``).

    Abschluss-Check der Transaktion: schlaegt er an, rollt ``atomic`` alles
    zurueck — eine halb gewanderte Akte darf nie committen.
    """
    for person in clients:
        if Event.objects.filter(case__client=person).exclude(client=person).exists():
            raise ValidationError(
                _("Interner Fehler: Ereignis- und Fall-Zuordnung sind inkonsistent — Zusammenführung abgebrochen.")
            )


def _merge_recent_visits(source, target) -> None:
    """Besuchs-Zeilen der Quelle zum Ziel — mit Dedup auf ``unique_together (user, client)``.

    Hat dieselbe Nutzerin beide besucht, kollidierte ein blindes UPDATE:
    stattdessen gewinnt das juengste ``visited_at``, ``is_favorite`` per OR,
    und die Quell-Zeile faellt. Durchweg ``QuerySet.update()`` statt
    ``save()`` — den ``visited_at``-Stempel setzt seit #2068 der throttelnde
    Manager-Upsert; hier wandert der BESTEHENDE Stempel mit, denn niemand hat
    die Person gerade besucht.
    """
    kept_by_user = {v.user_id: v for v in RecentClientVisit.objects.filter(client=target)}
    for visit in RecentClientVisit.objects.filter(client=source):
        kept = kept_by_user.get(visit.user_id)
        if kept is None:
            RecentClientVisit.objects.filter(pk=visit.pk).update(client=target)
            continue
        RecentClientVisit.objects.filter(pk=kept.pk).update(
            visited_at=max(visit.visited_at, kept.visited_at),
            is_favorite=kept.is_favorite or visit.is_favorite,
        )
        visit.delete()


def _is_empty_value(value) -> bool:
    """„Leer" im Sinne der Uebernahme-Regel (Ziel behaelt, Quelle fuellt Luecken)."""
    if value is None or value == "" or value == [] or value == {}:
        return True
    if isinstance(value, dict) and value.get("__files__") is True:
        return not value.get("entries")
    return False


def _adopt_missing_data(source, target):
    """Zusatzangaben der Quelle NUR in leere Ziel-Felder uebernehmen.

    Produktentscheid (ADR-045, Entscheid 3): das Ziel behaelt seine Werte;
    Quelle-Werte fuellen ausschliesslich leere/fehlende Felder. Hat das Ziel
    keinen Dokumentationstyp, uebernimmt es den der Quelle (derselbe
    Leere-Felder-Grundsatz, auf den Typ angewandt). Alles laeuft durch
    ``clean_dynamic_data`` — Slugs, die der effektive Typ nicht kennt,
    fallen weg (E11-Whitelist), verschluesselte Werte wandern als Token
    (gleiche Facility = gleicher Schluessel).

    Fuer uebernommene FILE-Slugs wandern die im Marker referenzierten
    ``Attachment``-Zeilen mit (Marker-Invariante I-M1, ADR-039 §7) — praezise
    ueber die Entry-IDs, nicht pauschal: superseded-Historie und nicht
    uebernommene Datei-Werte bleiben bei der Quelle und fallen mit deren
    Redaktion.

    Returns: ``(adopted_slugs, moved_attachment_ids, document_type_adopted)``
    — das Audit-/Undo-Inventar (Slugs und UUIDs, nie Werte).
    """
    effective_type = target.document_type or source.document_type
    document_type_adopted = target.document_type_id is None and source.document_type_id is not None
    if effective_type is None:
        return [], [], False
    source_data = source.data_json or {}
    target_data = dict(target.data_json or {})
    candidates = {
        slug: value
        for slug, value in source_data.items()
        if not _is_empty_value(value) and _is_empty_value(target_data.get(slug))
    }
    if not candidates and not document_type_adopted:
        return [], [], False
    cleaned = clean_dynamic_data(
        target.facility,
        effective_type,
        {**target_data, **candidates},
        entity_type=DocumentType.EntityType.CLIENT,
        entity_type_error=_("Der Dokumentationstyp ist nicht für Personen vorgesehen."),
    )
    adopted = sorted(slug for slug in candidates if slug in cleaned)
    moved_attachment_ids: list[str] = []
    for slug in adopted:
        marker = cleaned[slug]
        if isinstance(marker, dict) and marker.get("__files__") is True:
            entry_ids = [entry.get("id") for entry in marker.get("entries", []) if entry.get("id")]
            if entry_ids:
                Attachment.objects.filter(pk__in=entry_ids, client=source).update(client=target)
                moved_attachment_ids.extend(str(pk) for pk in entry_ids)
    target.document_type = effective_type
    target.data_json = cleaned
    # ``updated_at`` explizit — ``auto_now`` schreibt bei ``update_fields``
    # sonst nicht (Muster ``update_client``, Refs #1789-Folge).
    target.save(update_fields=["document_type", "data_json", "updated_at"])
    return adopted, moved_attachment_ids, document_type_adopted


def _execute_merge(source, target, user, *, deletion_request=None):
    """Gemeinsamer Kern beider Merge-Wege — erwartet bereits gesperrte Zeilen.

    Reihenfolge: erst Faelle, dann Ereignisse (Invariante ``Event.client ==
    Event.case.client``), Abschluss-Check am Ende. Das Undo-Inventar (welche
    Zeilen wanderten) wird VOR der Wanderung erhoben und landet im
    ``CLIENT_MERGED``-``detail`` — ``unmerge_client`` liest es von dort.
    """
    _validate_merge(source, target, ignore_request=deletion_request)

    # Banner-Wirkung VOR der Wanderung messen (Muster ``anonymize_client``,
    # Refs #1917): danach findet ``client_has_ban_banner_entries`` die
    # Ban-Events der Quelle nicht mehr — sie haengen schon am Ziel.
    from core.services.system.ban_revision import (
        bump_ban_revision_for_client_change,
        client_has_ban_banner_entries,
    )

    had_banner_entries = client_has_ban_banner_entries(source)

    moved_case_ids = [str(pk) for pk in Case.objects.filter(client=source).values_list("pk", flat=True)]
    moved_event_ids = [str(pk) for pk in Event.objects.filter(client=source).values_list("pk", flat=True)]
    moved_workitem_ids = [str(pk) for pk in WorkItem.objects.filter(client=source).values_list("pk", flat=True)]

    Case.objects.filter(client=source).update(client=target)
    Event.objects.filter(client=source).update(client=target)
    _assert_event_case_consistency(source, target)
    WorkItem.objects.filter(client=source).update(client=target)
    _merge_recent_visits(source, target)
    adopted_slugs, moved_attachment_ids, document_type_adopted = _adopt_missing_data(source, target)

    # Kontaktstufe: hoechste gewinnt (ADR-045, Entscheid 3). Nur aufwaerts —
    # eine Herabstufung kann hier nie entstehen, und die Hochstufung wird wie
    # jede andere als ``STAGE_CHANGE`` am Ziel protokolliert.
    target_stage_before = target.contact_stage
    if source.contact_stage == Client.ContactStage.QUALIFIED and target.contact_stage != Client.ContactStage.QUALIFIED:
        target.contact_stage = Client.ContactStage.QUALIFIED
        target.save(update_fields=["contact_stage", "updated_at"])
        update_client_stage(target, target_stage_before, target.contact_stage, user)

    source.merged_into = target
    source.save(update_fields=["merged_into"])
    source.soft_delete(user=user)

    # Offline-Widerruf der Quelle (Refs #1760): Geraete-Bundles der Quelle
    # muessen purgen; gequeuete Offline-Schreibzugriffe laufen in die
    # Dead-Letter-UI (``CLIENT_WRITE_REVOKED``) — bewusst kein Replay-Remap.
    from core.services.system import revoke_offline_client_bundles

    revoke_offline_client_bundles(source)
    bump_ban_revision_for_client_change(source, had_banner_entries=had_banner_entries)

    extra = {"deletion_request": str(deletion_request.pk)} if deletion_request is not None else {}
    audit_client_event(
        source,
        user,
        AuditLog.Action.CLIENT_MERGED,
        merged_into=str(target.pk),
        moved_events=moved_event_ids,
        moved_cases=moved_case_ids,
        moved_workitems=moved_workitem_ids,
        moved_attachments=moved_attachment_ids,
        adopted_slugs=adopted_slugs,
        document_type_adopted=document_type_adopted,
        target_stage_before=target_stage_before,
        **extra,
    )
    # Nur das ZIEL-Pseudonym in den Zeitstrom: die Zeile haengt am Ziel und
    # wird von DESSEN Redaktion erfasst (``_redact_activities``). Das
    # Quell-Pseudonym stuende hier laenger als die Quelle selbst (Refs #1751).
    log_activity(
        facility=target.facility,
        actor=user,
        verb=Activity.Verb.UPDATED,
        target=target,
        action=Activity.Action.CLIENT_MERGED,
    )
    logger.info(
        "client_merged",
        extra={
            "source_id": str(source.pk),
            "target_id": str(target.pk),
            "user_id": getattr(user, "pk", None),
        },
    )
    return target


# ---------------------------------------------------------------------------
# Undo bis Fristablauf (ADR-045, Entscheid 2)
# ---------------------------------------------------------------------------


def _merge_inventory(source) -> dict:
    """Das ``detail`` der juengsten ``CLIENT_MERGED``-Zeile dieser Quelle.

    Das append-only AuditLog ist hier bewusst zugleich der Undo-Speicher: die
    Rechenschafts-Zeile (WAS wanderte) und das Umkehr-Inventar sind dieselbe
    Information — ein zweiter Speicherort koennte nur divergieren. Das Audit-
    Pruning liegt mit seinen Fristen weit jenseits der Papierkorb-Frist, in
    der ein Undo ueberhaupt moeglich ist.
    """
    row = (
        AuditLog.objects.filter(
            facility=source.facility,
            action=AuditLog.Action.CLIENT_MERGED,
            target_type="Client",
            target_id=str(source.pk),
        )
        .order_by("-timestamp")
        .first()
    )
    if row is None or row.detail.get("merged_into") != str(source.merged_into_id):
        raise ValidationError(_("Zu dieser Zusammenführung fehlt der Audit-Nachweis — Aufheben nicht möglich."))
    return row.detail


@transaction.atomic
def unmerge_client(source, user):
    """Hebt eine Zusammenfuehrung auf, solange die Quelle unredigiert im Papierkorb liegt.

    Minimal-Undo mit klaren Grenzen (ADR-045, Entscheid 2):

    * Faelle, Ereignisse, Aufgaben und uebernommene Anhaenge wandern anhand
      des Inventars zurueck. Ereignisse, die NACH dem Merge am Ziel entstanden,
      bleiben dort — ausser sie haengen an einem zurueckwandernden Fall (die
      Invariante ``Event.client == Event.case.client`` geht vor: das Ereignis
      folgt seinem Fall).
    * Uebernommene Slugs werden am Ziel entfernt (die Quelle traegt die
      Originale unveraendert); ein mit-uebernommener Dokumentationstyp faellt
      zurueck auf ``None``. Zwischenzeitliche Ziel-Edits GENAU DIESER Felder
      gehen dabei verloren — dokumentierte Grenze des Minimal-Undo.
    * Die Besuchs-Zeilen (``RecentClientVisit``) werden NICHT rekonstruiert —
      der Dedup hat ihre Quell-Werte verworfen; das Widget baut sich beim
      naechsten Besuch neu auf.
    * Die Kontaktstufe des Ziels faellt auf ihren Vor-Merge-Wert zurueck, wenn
      die Hochstufung vom Merge kam — zusammen mit dem Entfernen der
      uebernommenen Zusatzangaben ist das die sachlich richtige Einstufung,
      kein verdeckter Downgrade (N2: nur Leitung darf diesen Weg gehen).

    Nach der Redaktion des Tombstones ist nichts mehr umkehrbar — die
    Stammdaten der Quelle sind getilgt, ein „Restore" ergaebe eine leere
    Huelle; der Versuch wird abgewiesen.
    """
    if not user.is_lead_or_admin:
        raise ValidationError(_("Nur die Leitung darf eine Zusammenführung aufheben."))
    if source.merged_into_id is None:
        raise ValidationError(_("Diese Person ist nicht zusammengeführt."))
    target = Client.objects.get(pk=source.merged_into_id, facility=source.facility)
    source, target = _locked_clients(source, target)
    if source.merged_into_id != target.pk:
        raise ValidationError(_("Diese Person ist nicht zusammengeführt."))
    # Refs #2085: Gefragt wird der terminale Stempel, nicht das Anzeigefeld.
    # Bis dahin stand hier ``if not source.is_active`` — das trug nur, weil
    # ``_redact_client_identity`` beide Felder in derselben Transaktion setzt.
    # Ein terminaler Zustand, dessen Schutz an einem nebenbei mitgesetzten
    # Anzeigefeld haengt, ist genau die implizite Kopplung, die spaeter bricht.
    # ``restore_client`` und ``restore_case`` fragen dasselbe Praedikat.
    # Der K-Anon-Pfad setzt zwar ebenfalls ``is_active=False``, ohne
    # ``anonymized_at`` zu stempeln — er erreicht Merge-Tombstones aber nicht
    # (Eintrittsbedingung ``total_events > 0``, ``retention/anonymization.py``),
    # die Verengung schliesst hier also keine Luecke auf.
    if source.anonymized_at is not None:
        raise ValidationError(
            _("Die Quelle wurde bereits endgültig anonymisiert — die Zusammenführung ist nicht mehr umkehrbar.")
        )
    # Abwehr in der Tiefe zum Ketten-Guard in ``_validate_merge`` (fuer Alt-/
    # Reparaturzustaende, die nicht ueber die Service-Wege entstanden sind):
    # ist das Ziel seinerseits Merge-Tombstone, liegen die gewanderten Daten
    # nicht mehr dort — die Inventar-Updates traefen 0 Zeilen und stellten die
    # Quelle als leeres Duplikat wieder her. LIFO gilt: erst die juengere
    # Zusammenfuehrung aufheben.
    if target.merged_into_id is not None:
        raise ValidationError(
            _(
                "Die Ziel-Person wurde ihrerseits zusammengeführt — "
                "zuerst deren Zusammenführung aufheben (jüngste zuerst)."
            )
        )
    inventory = _merge_inventory(source)
    moved_cases = inventory.get("moved_cases", [])
    moved_events = inventory.get("moved_events", [])
    moved_workitems = inventory.get("moved_workitems", [])
    moved_attachments = inventory.get("moved_attachments", [])
    adopted_slugs = inventory.get("adopted_slugs", [])
    document_type_adopted = inventory.get("document_type_adopted", False)
    target_stage_before = inventory.get("target_stage_before")

    from core.services.system.ban_revision import (
        bump_ban_revision_for_client_change,
        client_has_ban_banner_entries,
    )

    had_banner_entries = client_has_ban_banner_entries(target)

    if moved_cases:
        Case.objects.filter(pk__in=moved_cases, client=target).update(client=source)
    event_filter = Q()
    if moved_events:
        event_filter |= Q(pk__in=moved_events, case__isnull=True)
    if moved_cases:
        event_filter |= Q(case_id__in=moved_cases)
    if event_filter:
        Event.objects.filter(client=target).filter(event_filter).update(client=source)
    _assert_event_case_consistency(source, target)
    if moved_workitems:
        WorkItem.objects.filter(pk__in=moved_workitems, client=target).update(client=source)
    if moved_attachments:
        Attachment.objects.filter(pk__in=moved_attachments, client=target).update(client=source)

    if adopted_slugs or document_type_adopted:
        data = dict(target.data_json or {})
        for slug in adopted_slugs:
            data.pop(slug, None)
        if document_type_adopted:
            target.document_type = None
            data = {}
        target.data_json = data
        target.save(update_fields=["document_type", "data_json", "updated_at"])

    if target_stage_before == Client.ContactStage.IDENTIFIED and target.contact_stage == Client.ContactStage.QUALIFIED:
        target.contact_stage = Client.ContactStage.IDENTIFIED
        target.save(update_fields=["contact_stage", "updated_at"])
        update_client_stage(target, Client.ContactStage.QUALIFIED, Client.ContactStage.IDENTIFIED, user)

    source.merged_into = None
    source.save(update_fields=["merged_into"])
    # Mixin-``restore()`` direkt — der Service-``restore_client`` weist
    # Merge-Tombstones gerade ab, damit es KEINEN Restore ohne Rueckwanderung
    # gibt; dieser Pfad ist die eine legitime Ausnahme.
    source.restore()

    bump_ban_revision_for_client_change(target, had_banner_entries=had_banner_entries)

    audit_client_event(
        source,
        user,
        AuditLog.Action.CLIENT_RESTORED,
        unmerged_from=str(target.pk),
    )
    log_activity(
        facility=source.facility,
        actor=user,
        verb=Activity.Verb.UPDATED,
        target=target,
        action=Activity.Action.CLIENT_UNMERGED,
    )
    logger.info(
        "client_unmerged",
        extra={
            "source_id": str(source.pk),
            "target_id": str(target.pk),
            "user_id": getattr(user, "pk", None),
        },
    )
    return source
