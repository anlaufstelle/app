"""LegalHold-Lifecycle (#744).

Aus ``services/retention.py`` ausgekoppelt — eigenes Thema (Sperre
gegen Loeschung; orthogonal zu Vorschlaegen und Enforcement).
"""

from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, Case, Client, Event, EventHistory, LegalHold, RetentionProposal
from core.services.audit import audit_retention_decision
from core.services.events.history_diff import is_redaction_marker


def _row_content_is_gone(row) -> bool:
    """Hat DIESE Fassung ihre Werte verloren? (Fix-Runde 2, Finding B)

    ``redacted_at`` allein reicht nicht: die Art.-17-/K-Anon-Kaskade
    (``_redact_event_history``) ersetzt beide Payloads durch
    ``{"_redacted": True, "anonymized": True}``, **stempelt aber nichts** — den
    Zeitstempel gibt es erst seit ADR-050. Fuer solche Alt-Redaktionen lieferte
    die Pruefung ``false``, obwohl die Inhalte getilgt sind.

    Die Abgrenzung nach unten ist genauso wichtig wie die Erkennung: seit Refs
    #714 schreibt **jeder** Loeschpfad eine DELETE-Zeile, deren ``data_before``
    den Marker ``{"_redacted": True, "fields": [...]}`` traegt. Das ist der
    normale Loeschmarker und bedeutet gerade **nicht**, dass fruehere Fassungen
    fehlen — CREATE und UPDATE stehen dann noch vollstaendig da. Wuerde er als
    Redaktion zaehlen, truege jeder Hold auf ein frisch geloeschtes Ereignis
    faelschlich das Verspaetungsmerkmal, und die Aussage verloere ihren Wert.

    Unterschieden wird deshalb so:

    * ``redacted_at`` gesetzt — der Sweep war hier (eindeutig);
    * ``data_after`` traegt einen Marker — eine DELETE-Zeile hat gar kein
      ``data_after`` (``soft_delete_event`` schreibt nur ``data_before``), also
      ist das immer eine Redaktion;
    * ``data_before`` traegt einen Marker auf einer **Nicht**-DELETE-Zeile;
    * ``anonymized`` **im Marker** — das Merkmal der Art.-17-Kaskade, das der
      Loeschpfad nie schreibt; es entlarvt die Redaktion auch auf einer
      DELETE-Zeile. Es zaehlt nur zusammen mit dem Redaktionsflag: der Slug ist
      nicht reserviert, und ein gleichnamiges Nutzdatenfeld ist kein Beleg.
    """
    if row.redacted_at is not None:
        return True
    for payload in (row.data_before, row.data_after):
        # ``anonymized`` zaehlt NUR zusammen mit dem Redaktionsflag: der Slug ist
        # nicht reserviert, ein Dokumentationstyp darf ein gewoehnliches
        # Ja/Nein-Feld dieses Namens fuehren. Allein gewertet, machte ein
        # befuelltes Nutzdatenfeld jeden spaeteren Hold faelschlich „verspaetet".
        if is_redaction_marker(payload) and payload.get("anonymized"):
            return True
    if is_redaction_marker(row.data_after):
        return True
    return row.action != EventHistory.Action.DELETE and is_redaction_marker(row.data_before)


def _history_is_incomplete(event) -> bool:
    """Sind frühere Fassungen dieses Ereignisses nicht mehr vollstaendig verfuegbar?

    Das ist die Frage, die das Verspaetungsmerkmal beantworten muss (ADR-050 D2)
    — **nicht** „gibt es gerade eine redigierte Zeile". Der Unterschied wurde im
    Cross-Review sichtbar: Stufe 2 (D8/M-7) loescht genau die redigierten
    Zeilen. Haengt das Merkmal an ihnen, meldet ein danach angelegter Hold
    ``false``, obwohl die Inhalte laengst unwiederbringlich fort sind — der
    stille Hold, den D2 ausdruecklich verbietet.

    Vier Faelle, in dieser Reihenfolge:

    1. **Eine redigierte Zeile existiert** — Stufe 1 hat gegriffen, sichtbar.
    2. **Gar keine Fassung mehr, Ereignis geloescht** — Stufe 2 hat geraeumt.
       Die Einschraenkung auf geloeschte Ereignisse ist noetig, weil ein
       Ereignis auch ohne jede Historie entstehen kann (Seed-Daten schreiben
       keine ``EventHistory``); ein lebendes Ereignis ohne Fassungen hat nichts
       verloren.
    3. **Fassungen ohne CREATE-Zeile** — jedes ueber ``create_event`` angelegte
       Ereignis beginnt mit genau einer CREATE-Fassung. Fehlt sie, waehrend
       andere Fassungen stehen, hat Stufe 2 sie entfernt. Dieser Fall trifft
       das **lebende** Ereignis, dessen aeltere Fassungen nach D3 redigiert und
       spaeter geloescht wurden, waehrend der Schutzrand (D4) unredigiert
       stehen blieb — er faellt sonst durch alle anderen Bedingungen.
    4. Sonst: nichts fehlt, der Hold ist puenktlich.
    """
    rows = list(
        EventHistory.objects.filter(event_id=event.pk).only("action", "data_before", "data_after", "redacted_at")
    )
    if any(_row_content_is_gone(row) for row in rows):
        return True
    if not rows:
        return bool(event.is_deleted)
    return not any(row.action == EventHistory.Action.CREATE for row in rows)


def _client_history_is_incomplete(facility, client_id) -> bool:
    """Dasselbe fuer alle Ereignisse einer Person (ADR-050 D2, Cross-Review Finding 5).

    Ein Personen-Hold sichert die Historie **aller** ihrer Ereignisse; das
    Verspaetungsmerkmal muss deshalb auch dann stehen, wenn nur eines davon
    schon getilgt ist. ``any`` bricht beim ersten Treffer ab, und die
    Hold-Anlage ist eine seltene Verwaltungsaktion — die Schleife ueber die
    Ereignisse der Person ist hier die lesbarere Loesung als eine
    zusammengesetzte Mengenabfrage, die dieselbe Drei-Fall-Logik ein zweites
    Mal ausdruecken muesste.
    """
    events = Event.objects.filter(facility=facility, client_id=client_id).only("pk", "is_deleted")
    return any(_history_is_incomplete(event) for event in events.iterator())


def _lock_client_and_check_history(facility, target_id):
    """Sperrt die Personenzeile — der Koordinationspunkt des Personen-Holds.

    Gegenstueck zu :func:`_lock_event_and_check_redaction`. Bis zum
    Cross-Review war der Personen-Hold nur best-effort: der Sweep pruefte ihn
    zwar, aber ohne gemeinsames Sperrobjekt konnte eine Hold-Transaktion nach
    der Pruefung committen und vom Sweep ueberholt werden. Die Zusage in
    Oberflaeche und Handbuch — „ein Hold auf Ereignis **oder Person** haelt
    beide Stufen auf" — traegt erst mit dieser Sperre.

    ``core_client`` ist zugleich die erste Stufe der kanonischen Ordnung
    ``Client -> Event -> EventHistory -> AuditLog``, die auch das Art.-18-Gate
    und der manuelle Loeschpfad nehmen.
    """
    client = Client.objects.filter(facility=facility, pk=target_id).select_for_update().first()
    if client is None:
        return False
    return _client_history_is_incomplete(facility, target_id)


def _lock_event_and_check_redaction(facility, target_id):
    """Sperrt die Ereigniszeile und meldet, ob ihre Historie schon leer ist (ADR-050 D2).

    Der **explizite Koordinationspunkt** zwischen Hold-Anlage und
    EventHistory-Sweep. Beide sperren dieselbe Zeile in ``core_event``, bevor
    sie handeln — ohne sie koordinierte die Datenbank hier gar nichts:
    ``LegalHold.target_id`` ist ein freies UUID-Feld ohne Fremdschluessel, und
    eine Hold-Pruefung im Sweep allein schloesse das Rennen nicht, weil unter
    ``READ COMMITTED`` ein Hold zwischen dessen ``SELECT`` und ``COMMIT``
    entstehen kann.

    Die Redaktions-Lesung liegt bewusst **nach** der Sperre: erst danach ist
    „bereits redigiert" eine stabile Aussage und nicht der Stand von vorhin.
    """
    event = Event.objects.filter(facility=facility, pk=target_id).select_for_update().first()
    if event is None:
        # Kein Ereignis dieser Einrichtung — es gibt nichts zu koordinieren.
        # Der Hold bleibt trotzdem zulaessig: ``target_id`` ist ein freies Feld.
        return False
    return _history_is_incomplete(event)


def create_hold_for_target(facility, *, target_type, target_id, user, reason, expires_at=None):
    """Legt einen Legal Hold OHNE Retention-Vorschlag an (Refs #1818, ADR-044 §5).

    ``LegalHold.target_type`` ist ein freies ``CharField`` ohne ``choices`` —
    ``"Case"`` war also immer speicherbar. Was fehlte, war der **Erzeugungsweg**:
    :func:`create_legal_hold` setzt einen ``RetentionProposal`` voraus, und
    ``RetentionProposal.TargetType`` kennt nur ``EVENT``. Ein Fall-Hold war damit
    ausschliesslich per ORM-Konsole anlegbar.

    Bewusst NICHT geloest, indem ``RetentionProposal`` einen ``CASE``-Zweig
    bekommt: ein Vorschlag traegt ``deletion_due_at`` und
    ``retention_category`` und entsteht aus den Fristen des
    Ereignis-Enforcements (:mod:`core.retention.enforcement`). Fuer Faelle gibt
    es keine solche Frist — ihr Loeschweg ist der Vier-Augen-Antrag plus die
    Papierkorb-Frist. Ein Pseudo-Vorschlag nur als Traeger des Holds haette ein
    zweites, leerlaufendes Vorschlagsobjekt in die Retention-Ansicht gespuelt.

    Der Vorschlags-gebundene Weg bleibt :func:`create_legal_hold` und setzt
    zusaetzlich den Vorschlag auf ``HELD``; beide schreiben dieselbe
    ``LEGAL_HOLD``-Audit-Zeile.

    **Transaktional und sperrend seit ADR-050 D2** (Refs #2065). Fuer
    ``target_type="Event"`` wird die Ereigniszeile gesperrt, fuer
    ``target_type="Client"`` die Personenzeile, BEVOR die ``LegalHold``-Zeile
    entsteht — beides sind die gemeinsamen Koordinationspunkte mit dem
    EventHistory-Sweep, der je Charge dieselben Zeilen in derselben Ordnung
    sperrt. Trifft der Hold auf eine bereits redigierte
    Historie, wird er **nicht abgelehnt**: er bindet weiterhin Ereigniszeile,
    Anhaenge und die Anonymisierung der Person. Er wird aber als **verspaetet
    gekennzeichnet** — am zurueckgegebenen Objekt
    (``hold.history_already_redacted``) und als Merkmal an der
    ``LEGAL_HOLD``-Audit-Zeile. Ein stiller Hold, der Vollstaendigkeit
    suggeriert, waere die schlechtere Antwort: die Spoliationsfrage muss
    spaeter beantwortbar sein.
    """
    with transaction.atomic():
        # Beide Hold-Ziele mit Historien-Bezug bekommen denselben
        # Koordinationspunkt und dasselbe Verspaetungsmerkmal (Cross-Review
        # Finding 5). ``Case``/``Activity`` haben keine Ereignis-Historie —
        # dort gibt es nichts zu koordinieren und nichts zu melden.
        if target_type == "Event":
            already_redacted = _lock_event_and_check_redaction(facility, target_id)
        elif target_type == "Client":
            already_redacted = _lock_client_and_check_history(facility, target_id)
        else:
            already_redacted = False
        hold = LegalHold.objects.create(
            facility=facility,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            expires_at=expires_at,
            created_by=user,
        )
        detail = {
            "reason": reason,
            "expires_at": str(expires_at) if expires_at else None,
            "hold_id": str(hold.pk),
        }
        if target_type in ("Event", "Client"):
            # Immer gesetzt, nicht nur im Verspaetungsfall: die Abwesenheit
            # eines Schluessels waere mehrdeutig — „nicht redigiert" oder „nicht
            # geprueft"? Fuer die Spoliationsfrage ist das der Unterschied.
            detail["history_already_redacted"] = already_redacted
        audit_retention_decision(
            facility,
            target_type=target_type,
            target_id=target_id,
            action=AuditLog.Action.LEGAL_HOLD,
            category="legal_hold_created",
            user=user,
            **detail,
        )
    hold.history_already_redacted = already_redacted
    return hold


def create_legal_hold(proposal, user, reason, expires_at=None):
    """Create a legal hold and set the proposal to held status."""
    hold = create_hold_for_target(
        proposal.facility,
        target_type=proposal.target_type,
        target_id=proposal.target_id,
        user=user,
        reason=reason,
        expires_at=expires_at,
    )
    proposal.status = RetentionProposal.Status.HELD
    proposal.save(update_fields=["status"])
    return hold


@transaction.atomic
def dismiss_legal_hold(hold, user):
    """Dismiss a legal hold and revert the proposal to pending."""
    hold.dismissed_at = timezone.now()
    hold.dismissed_by = user
    hold.save(update_fields=["dismissed_at", "dismissed_by"])

    # Revert associated proposal to pending
    RetentionProposal.objects.filter(
        facility=hold.facility,
        target_type=hold.target_type,
        target_id=hold.target_id,
        status=RetentionProposal.Status.HELD,
    ).update(status=RetentionProposal.Status.PENDING)

    audit_retention_decision(
        hold.facility,
        target_type=hold.target_type,
        target_id=hold.target_id,
        action=AuditLog.Action.LEGAL_HOLD,
        category="legal_hold_dismissed",
        user=user,
        hold_id=str(hold.pk),
        reason=hold.reason,
    )


def has_active_hold(facility, target_type, target_id):
    """Check if a target has an active (not dismissed, not expired) legal hold.

    Uses ``timezone.localdate()`` (Europe/Berlin) rather than the naive,
    server-local ``date.today()`` so the active/expired boundary stays in
    lockstep with ``LegalHold.is_active`` and the dashboard SQL filter near
    midnight (UTC vs. Berlin); see #1191 (Refs #1192).
    """
    return (
        LegalHold.objects.filter(
            facility=facility,
            target_type=target_type,
            target_id=target_id,
            dismissed_at__isnull=True,
        )
        .exclude(
            expires_at__lt=timezone.localdate(),
        )
        .exists()
    )


def _active_hold_ids(queryset, target_type):
    """Das Aktiv-Praedikat — die EINE Stelle, an der es steht.

    „Nicht aufgehoben, nicht abgelaufen", mit ``timezone.localdate()``
    (Europe/Berlin) statt des naiven UTC-Datums, im Gleichschritt mit
    ``LegalHold.is_active`` und dem Dashboard-Filter (Refs #1192). Beide
    oeffentlichen Einstiege unten setzen darauf auf, statt das Praedikat zu
    wiederholen — eine zweite Kopie liefe auseinander, wie #1756 es fuer die
    Anonymisierungs-Einstiege gezeigt hat.
    """
    return set(
        queryset.filter(target_type=target_type, dismissed_at__isnull=True)
        .exclude(expires_at__lt=timezone.localdate())
        .values_list("target_id", flat=True)
    )


def get_active_hold_target_ids(facility, target_type="Event"):
    """Return set of target_ids with active legal holds for a facility."""
    return _active_hold_ids(LegalHold.objects.filter(facility=facility), target_type)


def get_active_hold_target_ids_for_facilities(facility_ids, target_type="Event"):
    """Wie oben, aber ueber mehrere Einrichtungen (Refs #2065).

    Gebraucht von Pfaden, die nicht je Einrichtung laufen: der
    EventHistory-Sweep partitioniert seine Chargen facility-agnostisch, und die
    Bestandsmigration laeuft installationsweit. Dasselbe Aktiv-Praedikat.
    """
    if not facility_ids:
        return set()
    return _active_hold_ids(LegalHold.objects.filter(facility_id__in=facility_ids), target_type)


def exclude_clients_with_active_holds(queryset, facility):
    """Nimmt Klienten mit aktivem Legal Hold aus *queryset* (Refs #1066, #1756, #1818).

    Ausgeschlossen wird, wer selbst unter einem Hold steht
    (``target_type="Client"``), mindestens ein Event unter Hold hat
    (``target_type="Event"``) **oder** mindestens einen **Fall** unter Hold hat
    (``target_type="Case"``) — sonst risse die Loeschung des Eltern-Klienten
    das gehaltene Kind mit (Spoliation). Nicht aufgehobene, nicht
    abgelaufene Holds zaehlen; die Aktiv-Grenze zieht
    :func:`get_active_hold_target_ids` (``timezone.localdate``).

    Der **Fall**-Zweig kam mit ADR-044 (Refs #1818, P4) dazu und ist die
    Gegenrichtung zu :func:`exclude_cases_with_active_holds`: seit dort ein
    Personen-Hold den Fall sperrt, muss hier ein Fall-Hold die Person sperren.
    Die Personen-Anonymisierung redigiert ueber
    ``_redact_cases_and_episodes``/``_redact_case_goals`` genau die
    Fall-Inhalte, die der Hold sichern soll, und loescht ueber
    ``_delete_owned_attachments_for_client`` die fall-eigenen Anhaenge
    physisch (nicht rollback-bar). Ohne diesen Zweig waere der Personen-Weg
    die Hintertuer um den Fall-Hold — an allen drei Einstiegen
    (Papierkorb-Frist, Admin-Sofort-Tilgung, K-Anon-Bridge), weil alle drei
    ueber diesen Helfer laufen.

    Die Anonymisierung wird damit nur **aufgeschoben**: ein spaeterer
    Retention-Lauf holt sie nach, sobald der Hold aufgehoben oder abgelaufen
    ist. Die Zeile bleibt bis dahin unveraendert — sie zaehlt insbesondere
    weiter zu ihrer k-Anonymitaets-Aequivalenzklasse, weil sie mit echten
    Quasi-Identifikatoren real existiert (siehe ``count_clients_in_bucket``).

    Gemeinsamer Helfer statt Kopie: der Trash-Expiry-Pfad
    (:func:`core.services.client.anonymize_eligible_soft_deleted_clients`) und
    die K-Anon-Bridge (:func:`core.retention.anonymization.anonymize_clients`)
    muessen exakt dieselbe Menge ausnehmen — #1756 war genau die Drift
    zwischen beiden.
    """
    return (
        queryset.exclude(pk__in=get_active_hold_target_ids(facility, "Client"))
        .exclude(events__pk__in=get_active_hold_target_ids(facility, "Event"))
        .exclude(cases__pk__in=get_active_hold_target_ids(facility, "Case"))
    )


def exclude_cases_with_active_holds(queryset, facility):
    """Nimmt Faelle mit aktivem Legal Hold aus *queryset* (Refs #1818, P4).

    Gegenstueck zu :func:`exclude_clients_with_active_holds`, mit **drei**
    Ausschluss-Zweigen statt zwei:

    * der Fall selbst steht unter Hold (``target_type="Case"`` — seit ADR-044
      zulaessig),
    * mindestens ein **Ereignis des Falls** steht unter Hold. Die Tilgung
      loest die Zuordnung (P5); welcher Fall ein gehaltenes Ereignis
      einrahmte, ist selbst Beweismittel — der Hold darf sich nicht dadurch
      aushebeln lassen, dass man den Rahmen entfernt (Spoliation, Muster
      #1066),
    * die **Person** des Falls steht unter Hold. Ohne diesen Zweig waere der
      Fall-Loeschweg die Hintertuer um genau die Sperre, die
      ``exclude_clients_with_active_holds`` zieht: die Personen-Anonymisierung
      redigiert dieselben Fall-Inhalte (``_redact_cases_and_episodes``) und ist
      bei einem Personen-Hold blockiert. ADR-039 §6 sagt dasselbe von der
      Datei-Seite her („der Client-Legal-Hold schuetzt transitiv").

    Bewusst NICHT ueber :func:`client_has_active_hold` gebaut: der Helfer
    schliesst zusaetzlich Personen aus, von denen **irgendein** Ereignis unter
    Hold steht — auch eines, das mit diesem Fall nichts zu tun hat. Fuer die
    Personen-Anonymisierung ist das richtig (sie fasst alle Ereignisse an), fuer
    die Fall-Redaktion waere es Ueberdeckung: sie laesst Ereignisse unberuehrt.

    Wie beim Personen-Pfad wird nur **aufgeschoben** — ein spaeterer
    Retention-Lauf holt die Tilgung nach, sobald der Hold aufgehoben oder
    abgelaufen ist.
    """
    return (
        queryset.exclude(pk__in=get_active_hold_target_ids(facility, "Case"))
        .exclude(events__pk__in=get_active_hold_target_ids(facility, "Event"))
        .exclude(client_id__in=get_active_hold_target_ids(facility, "Client"))
    )


def case_has_active_hold(case):
    """Einzel-Pruefung derselben Regel wie :func:`exclude_cases_with_active_holds`.

    Fuer die manuelle Sofort-Tilgung
    (:func:`core.services.case.deletion.anonymize_case_now`), die den Ausschluss
    als Meldung statt als Mengen-Filter braucht. Bewusst **auf** dem
    Mengen-Filter aufgesetzt statt daneben nachgebaut — dieselbe Lehre wie
    #1756 beim Personen-Pfad.
    """
    return not exclude_cases_with_active_holds(
        Case.objects.filter(pk=case.pk),
        case.facility,
    ).exists()


def owner_has_active_hold(owner):
    """Aktiver Legal Hold, der die Anhaenge eines Owners schuetzt (Refs #1874).

    Gebraucht vom **physischen** Anhang-Loeschpfad
    (:func:`core.services.attachments.crud.purge_attachment_chain`). Der
    bisherige UI-Entfernen-Pfad fragte gar nicht nach Holds — er brauchte es
    auch nicht: ``soft_delete_attachment_chain`` setzt nur ``deleted_at``, die
    verschluesselte Datei und die Zeile bleiben bitgleich erhalten. Ein Legal
    Hold sichert Beweismittel; genau das leistet ein Soft-Delete noch. Ein
    Purge dagegen macht die Datei unwiederbringlich — er ist der erste
    Anhang-Pfad, an dem ein Hold ueberhaupt etwas zu verhindern hat.

    ``LegalHold.target_type`` kennt nur ``Event``/``Client``/``Activity``; ein
    Anhang ist nie selbst Hold-Ziel. Die Sperre wird deshalb ueber den Owner
    aufgeloest:

    * **Ereignis** — eigener Event-Hold ODER (ueber die Person) der
      Personen-Zweig unten,
    * **Person** — der Personen-Zweig,
    * **Fall** — eigener Case-Hold ODER der Personen-Zweig ueber ``case.client``.
      Bis #1818 gab es nur die zweite Haelfte, weil ``Case`` kein Hold-Ziel sein
      konnte; seit ADR-044 kann er es (P4), und ein Hold auf genau diesem Fall
      muss dann auch seine Anhaenge schuetzen — sonst waere der Purge-Weg die
      Luecke im frisch eingefuehrten Hold. Der Personen-Zweig bleibt bewusst
      **zusaetzlich** stehen (rein additive Erweiterung): er sperrt schon heute
      breiter, naemlich auch bei einem gehaltenen Ereignis derselben Person.

    Der Personen-Zweig ist bewusst :func:`client_has_active_hold` und kein
    Eigenbau: dieselbe Menge, die schon Anonymisierung und Trash-Ablauf
    zurueckhaelt (Refs #1756) — inklusive „mindestens ein Ereignis der Person
    steht unter Hold". Ein personen-eigener Anhang mit gehaltenem Ereignis
    waere sonst loeschbar, waehrend die Anonymisierung derselben Person
    blockiert ist.

    Ein personenloses Ereignis (``client`` ist ``NULL``) hat keinen
    Personen-Zweig — dort zaehlt nur der eigene Event-Hold.
    """
    model_name = owner._meta.model_name
    if model_name == "event":
        if has_active_hold(owner.facility, "Event", owner.pk):
            return True
        client = owner.client
    elif model_name == "client":
        client = owner
    elif model_name == "case":
        if has_active_hold(owner.facility, "Case", owner.pk):
            return True
        client = owner.client
    else:
        raise ValueError(f"Kein zulaessiger Attachment-Owner: {owner._meta.label}")
    return client is not None and client_has_active_hold(client)


def client_has_active_hold(client):
    """Einzel-Pruefung derselben Regel wie :func:`exclude_clients_with_active_holds`.

    Fuer die manuelle Sofort-Anonymisierung
    (:func:`core.services.client.anonymize_client_now`), die den Ausschluss als
    Meldung statt als Mengen-Filter braucht. Bewusst **auf** dem Mengen-Filter
    aufgesetzt statt daneben nachgebaut: die drei Anonymisierungs-Einstiege
    (Trash-Ablauf, Retention-Bridge, manuelle Aktion) duerfen nicht erneut
    auseinanderlaufen (Refs #1756).
    """
    return not exclude_clients_with_active_holds(
        Client.objects.filter(pk=client.pk),
        client.facility,
    ).exists()
