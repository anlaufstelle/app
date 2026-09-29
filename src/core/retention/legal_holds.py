"""LegalHold-Lifecycle (#744).

Aus ``services/retention.py`` ausgekoppelt — eigenes Thema (Sperre
gegen Loeschung; orthogonal zu Vorschlaegen und Enforcement).
"""

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import Activity, AuditLog, Case, Client, Event, EventHistory, LegalHold, RetentionProposal
from core.services.audit import audit_retention_decision
from core.services.events.history_diff import is_redaction_marker

#: Meldung, wenn die handelnde Person nicht zur Einrichtung des Holds gehoert
#: bzw. nicht zu der, in der er angelegt werden soll — Wortlaut wie in den
#: uebrigen Diensten mit Einrichtungsgrenze (Refs #2374).
LEGAL_HOLD_OTHER_FACILITY_MESSAGE = _("Aufbewahrungssperre gehört zu einer anderen Einrichtung.")

#: Meldung, wenn der Hold seit dem Laden der Uebersicht bereits aufgehoben
#: wurde (Stil ``PROPOSAL_ALREADY_DECIDED_MESSAGE``, Refs #2374).
LEGAL_HOLD_ALREADY_DISMISSED_MESSAGE = _(
    "Diese Aufbewahrungssperre wurde zwischenzeitlich bereits aufgehoben — bitte die Übersicht neu laden."
)


def _row_content_is_gone(row) -> bool:
    """Hat DIESE Fassung ihre Werte verloren? (Finding B)

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


def _assert_not_merge_tombstone(client):
    """Weist eine Aufbewahrungssperre auf einen Merge-Tombstone ab (Refs #2144).

    Red-Team B-11: Auf der bereits gesperrten Personenzeile wurde
    ``merged_into_id`` nie gelesen. Ein Hold auf die **Quelle** einer
    vollzogenen Zusammenfuehrung war damit still zulaessig — und deckte nichts:
    die Ereignisse der Quelle tragen seit dem Merge ``client_id`` des Ziels
    (``merge.py``, ``update(client=target)``), waehrend alle Deckungsmengen
    (``collect_doomed_events``, :func:`exclude_clients_with_active_holds`) nur
    auf den Tombstone schauen. Das Verspaetungsmerkmal meldete zusaetzlich
    ``history_already_redacted=False``, weil die Quelle gar keine Ereigniszeile
    mehr hat (``any(()) == False``) — ein irrefuehrendes „puenktlich" auf einem
    Hold ohne Deckung.

    **Abweisen statt umlenken:** Die Gegenrichtung ist seit ADR-045 genauso
    hart (``_validate_merge`` verweigert den Merge unter Hold und die
    Zusammenfuehrung einer bereits gemergten Quelle). Ein Hold friert eine Akte
    als Beweismittel ein; wer ihn anordnet, soll die Kette selbst nachvollziehen
    und ihn bewusst auf die Zielperson setzen, statt ihn stillschweigend
    verschoben zu bekommen. Fail-loud wie ADR-050 D2 fordert.

    Bewusst NICHT abgewiesen wird ein Hold auf eine Person im **Papierkorb**
    (``is_deleted=True``) — genau das ist die Zusage „ein Hold haelt beide
    Stufen auf" (Papierkorb-Frist UND Tilgung, Refs #2123) — und auch keiner auf
    eine bereits **redigierte** Person (``anonymized_at``): der traegt das
    bestehende Verspaetungsmerkmal ``history_already_redacted=True`` und ist
    damit dokumentiert, nicht still.
    """
    if client.merged_into_id is None:
        return
    # ``facility_id`` mitgefiltert (Zweitgutachten zu #2144, Befund 5): ``merged_into`` wird zwar nur
    # innerhalb derselben Einrichtung gesetzt, aber jede Lesung in diesem Modul
    # traegt ihren Einrichtungs-Scope — Konvention statt Verlass auf eine
    # Invariante an anderer Stelle.
    ziel_pseudonym = (
        Client.objects.filter(pk=client.merged_into_id, facility_id=client.facility_id)
        .values_list("pseudonym", flat=True)
        .first()
    )
    raise ValidationError(
        _("Diese Person wurde mit %(pseudonym)s zusammengeführt – die Aufbewahrungssperre gehört auf die Zielperson.")
        % {"pseudonym": ziel_pseudonym or "?"}
    )


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
    # Refs #2144: Der Zustand der Zeile wird jetzt auch GELESEN, nicht nur
    # gesperrt — auf der gesperrten Zeile, damit „ist ein Tombstone" eine
    # stabile Aussage ist und nicht der Stand von vorhin.
    _assert_not_merge_tombstone(client)
    return _client_history_is_incomplete(facility, target_id)


def _lock_case_and_check_history(facility, target_id):
    """Sperrt Person, Fall und Ereigniszeilen — der Anker des Fall-Holds (Refs #2122).

    Bis hierher nahm ``create_hold_for_target`` fuer ``target_type="Case"``
    **gar keine** Sperre (``else: already_redacted = False``). Ein Fall-Hold
    zaehlt aber in :func:`client_has_active_hold` mit und blockiert damit alle
    drei Anonymisierungs-Einstiege — ohne gemeinsames Sperrobjekt konnte er
    neben einer laufenden Anonymisierung her committen und kam zu spaet, genau
    die Luecke, die #2108 fuer Personen- und #2107 fuer Ereignis-Holds
    geschlossen hat (Befund im Docstring von ``_lock_client_event_rows``).

    **Kanonische Ordnung** ``core_client`` -> ``core_case`` -> ``core_event``:
    Die Personenzeile zuerst, weil sie die erste Stufe der repo-weiten Ordnung
    ist und weil genau sie der Sofortpfad, der Nacht-Batch, die K-Anon-Bridge
    und der Ereignis-Sweep als Erstes nehmen — sie ist der gemeinsame
    Koordinationspunkt. Der Fall danach (er haengt an der Person), die
    Ereigniszeilen zuletzt, in ``pk``-Ordnung wie in
    ``_lock_client_event_rows``. Die Gegenordnung waere der ABBA-Zyklus, den
    ADR-050 D5.5 ausschliesst.

    ``target_id`` ist ein freies Feld ohne Fremdschluessel: ein Fall, den diese
    Einrichtung nicht kennt, ist kein Fehler — es gibt dann nur nichts zu
    koordinieren (Muster :func:`_lock_event_and_check_redaction`).

    Rueckgabe: ob die Historie der Person schon unvollstaendig ist — dieselbe
    Aussage wie beim Personen-Hold, denn ein Fall-Hold sichert ueber
    ``_redact_cases_and_episodes``/``_delete_owned_attachments_for_client``
    genau das, was die Personen-Anonymisierung tilgt.
    """
    case = Case.objects.filter(facility=facility, pk=target_id).values("client_id").first()
    if case is None:
        return False
    client_id = case["client_id"]
    person = (
        Client.objects.filter(facility=facility, pk=client_id)
        .select_for_update()
        .only("pk", "facility", "merged_into")
        .first()
    )
    # Refs #2144: Faelle haben keinen eigenen Tombstone — sie wandern beim
    # Merge per ``update(client=target)`` mit. Ihre PERSON kann aber einer
    # sein; ein Fall, der noch auf den Tombstone zeigt, ist derselbe stille
    # Deckungsverlust wie beim Personen-Hold. Der Papierkorb-Zustand des Falls
    # (``is_deleted``/``anonymized_at``) bleibt bewusst zulaessig: der Fall-Hold
    # im Papierkorb ist genau der Halt, auf dem Refs #2123 aufbaut.
    if person is not None:
        _assert_not_merge_tombstone(person)
    Case.objects.filter(facility=facility, pk=target_id).select_for_update().only("pk").first()
    # Die Ereigniszeilen der Person mit unter dieselbe Sperre: ein
    # Ereignis-Hold koordiniert ueber ``core_event``, und der Sofortpfad haelt
    # beide Stufen (``_lock_client_event_rows``).
    list(Event.objects.filter(facility=facility, client_id=client_id).order_by("pk").select_for_update().only("pk"))
    return _client_history_is_incomplete(facility, client_id)


def _lock_event_and_check_redaction(facility, target_id):
    """Sperrt die Ereigniszeile und meldet, ob ihre Historie schon leer ist (ADR-050 D2).

    Der **explizite Koordinationspunkt** zwischen Hold-Anlage und
    EventHistory-Sweep. Beide sperren dieselbe Zeile in ``core_event``, bevor
    sie handeln — ohne sie koordinierte die Datenbank hier gar nichts:
    ``LegalHold.target_id`` ist ein freies UUID-Feld ohne Fremdschluessel, und
    eine Hold-Pruefung im Sweep allein schloesse das Rennen nicht, weil unter
    ``READ COMMITTED`` ein Hold zwischen dessen ``SELECT`` und ``COMMIT``
    entstehen kann.

    **Die Personenzeile kommt zuerst** (Refs #2144/#2123). Die Ereigniszeile
    allein reicht nicht, weil ein Ereignis-Hold
    nicht nur ueber sein Ereignis wirkt: ``client_has_active_hold`` zaehlt ihn
    fuer die ganze **Person** mit, ``exclude_cases_with_active_holds`` fuer
    deren **Faelle**, und ``owner_has_active_hold`` prueft ihn deshalb auch beim
    Purge eines Personen- oder Fall-Anhangs. Jene Pfade sperren ``core_client``
    (und ggf. ``core_case``), nie die einzelne Ereigniszeile — ohne diesen
    Anker gab es fuer genau diese transitive Menge kein gemeinsames
    Sperrobjekt, und ein Ereignis-Hold konnte zwischen Hold-Lesung und Commit
    des Purge/der Tilgung dazwischenrutschen.

    Mit dieser Stufe hat **jede** Hold-Anlage (Ereignis, Person, Fall,
    Aktivitaet) ``core_client`` als erstes Glied und serialisiert damit ueber
    dieselbe Zeile wie Sofortpfad, Nacht-Batch, K-Anon-Bridge, Ereignis-Sweep,
    Approve und Anhang-Purge. Die Ordnung ``core_client`` -> ``core_event``
    bleibt die kanonische (ADR-050 D5.5) — dieselbe Richtung, die
    ``_lock_target_rows_for_approval`` (``proposals.py``),
    ``_lock_event_and_recheck_hold`` (``enforcement.py``) und
    ``purge_attachment_chain`` nehmen; ein ABBA-Zyklus entsteht nicht.

    **Inversions-Schutz:** die Bindung wird auf der gesperrten Ereigniszeile
    revalidiert. Haengt ein paralleler Merge das Ereignis waehrend der
    Sperrwartezeit an eine ANDERE Person, haetten wir deren Zeile nie gesperrt.
    Bewusst Abbruch statt Nachsperren (Muster
    ``_lock_target_rows_for_approval``, ``_lock_case_chain``); die anordnende
    Person laedt neu und legt den Hold auf dem frischen Stand an.

    Die Redaktions-Lesung liegt bewusst **nach** der Sperre: erst danach ist
    „bereits redigiert" eine stabile Aussage und nicht der Stand von vorhin.
    """
    # Bindung ungesperrt vorlesen: die Personenzeile steht in der kanonischen
    # Ordnung vor der Ereigniszeile, ihre pk steht aber nur am Ereignis.
    row = Event.objects.filter(facility=facility, pk=target_id).values("client_id").first()
    if row is None:
        # Kein Ereignis dieser Einrichtung — es gibt nichts zu koordinieren.
        # Der Hold bleibt trotzdem zulaessig: ``target_id`` ist ein freies Feld.
        return False
    client_id = row["client_id"]
    if client_id is not None:
        # ``client_id`` ist ``None`` bei anonymen Ereignissen — dort gibt es
        # keine Personenzeile und nichts zu koordinieren.
        Client.objects.filter(facility=facility, pk=client_id).select_for_update().only("pk").first()
    event = Event.objects.filter(facility=facility, pk=target_id).select_for_update().first()
    if event is None:
        return False
    if event.client_id != client_id:
        from core.retention.proposals import TARGET_BINDING_CHANGED_MESSAGE

        raise ValidationError(TARGET_BINDING_CHANGED_MESSAGE)
    return _history_is_incomplete(event)


def resolve_activity_client_id(facility, target_type_id, target_id):
    """Personenbezug des Ziels einer Aktivitaet — oder ``None`` (Refs #2124).

    ``Activity`` haengt nur generisch am Ziel (``target_type``/``target_id``,
    kein Fremdschluessel) und hat weder ``client_id`` noch ``case_id``. Fuer die
    kanonische Sperr-Reihenfolge braucht aber sowohl die Hold-Anlage als auch
    der Sweep dieselbe Antwort auf „welche Personenzeile gehoert dazu?" — sonst
    naehmen beide unterschiedliche Anker, und der gemeinsame Koordinationspunkt
    waere wieder weg. Deshalb hier, einmal, fuer beide Aufrufer.

    Client-Ziel: ``target_id`` **ist** die Personen-pk (keine Query noetig).
    Ereignis-/Fall-Ziel: deren ``client_id``. Alles andere (WorkItem, Milestone,
    Facility …) hat keinen Anker — dann bleibt es bei der Aktivitaetszeile.
    """
    model = ContentType.objects.get_for_id(target_type_id).model_class()
    if model is None:
        return None
    if model is Client:
        return target_id
    if model in (Event, Case):
        return model.objects.filter(facility=facility, pk=target_id).values_list("client_id", flat=True).first()
    return None


def _lock_activity_and_check(facility, target_id):
    """Sperrt Personenzeile des Ziels und Aktivitaetszeile (Refs #2124).

    Bis hierher fiel ``target_type="Activity"`` in den ``else``-Zweig:
    ``already_redacted = False``, **keine** Sperre, kein Sperrobjekt. Der
    Aktivitaeten-Sweep loeschte gleichzeitig per Bulk-``DELETE`` ohne
    Zeilensperre — es gab also gar nichts, woran die beiden koordinieren
    konnten. Dieselbe Wurzel wie #2107 (Ereignis-Sweep) und #2122 (Fall-Hold),
    nur an der letzten offenen Stelle.

    **Kanonische Ordnung**: ``core_activity`` ist das **letzte** Glied
    (``core_client`` -> ``core_case`` -> ``core_event`` ->
    ``core_retentionproposal`` -> ``core_eventhistory`` -> ``core_auditlog`` ->
    ``core_activity``, ADR-050 D5.5 Nachtrag zu #2124). Aktivitaeten sind
    Protokollzeilen; kein Pfad sperrt nach einer Aktivitaetszeile noch eine
    andere Tabelle. Die Personenzeile des Ziels kommt deshalb zuerst — sie ist
    dieselbe erste Stufe, die alle uebrigen Hold-Zweige nehmen.

    ``already_redacted`` bleibt ``False``: Aktivitaeten haben keine
    Ereignis-Historie, es gibt kein Verspaetungsmerkmal zu melden. Und eine
    unbekannte ``target_id`` bleibt zulaessig — ``LegalHold.target_id`` ist ein
    freies Feld ohne Fremdschluessel, genau wie in den drei uebrigen Zweigen
    (Muster :func:`_lock_event_and_check_redaction`).
    """
    row = Activity.objects.filter(facility=facility, pk=target_id).values("target_type_id", "target_id").first()
    if row is None:
        # Keine Aktivitaet dieser Einrichtung — es gibt nichts zu koordinieren.
        return False
    client_id = resolve_activity_client_id(facility, row["target_type_id"], row["target_id"])
    if client_id is not None:
        Client.objects.filter(facility=facility, pk=client_id).select_for_update().only("pk").first()
    Activity.objects.filter(facility=facility, pk=target_id).select_for_update().only("pk").first()
    return False


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
    ``target_type="Client"`` die Personenzeile und — seit Refs #2122 — fuer
    ``target_type="Case"`` Person, Fall und Ereigniszeilen in der kanonischen
    Ordnung ``core_client`` -> ``core_case`` -> ``core_event``, BEVOR die
    ``LegalHold``-Zeile entsteht — es sind die gemeinsamen
    Koordinationspunkte mit dem
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
        # Alle Hold-Ziele mit Historien-Bezug bekommen denselben
        # Koordinationspunkt und dasselbe Verspaetungsmerkmal (Cross-Review
        # Finding 5). Refs #2122: ``Case`` gehoert dazu — der Fall-Hold
        # blockiert ueber ``exclude_clients_with_active_holds`` dieselbe
        # Personen-Anonymisierung wie ein Personen-Hold und braucht deshalb
        # denselben Anker. Refs #2124: ``Activity`` hat seither ebenfalls einen
        # Anker (Personenzeile des Ziels -> Aktivitaetszeile), traegt aber
        # weiterhin KEIN Verspaetungsmerkmal — Aktivitaeten haben keine
        # Ereignis-Historie, ueber deren Vollstaendigkeit etwas zu sagen waere.
        if target_type == "Event":
            already_redacted = _lock_event_and_check_redaction(facility, target_id)
        elif target_type == "Client":
            already_redacted = _lock_client_and_check_history(facility, target_id)
        elif target_type == "Case":
            already_redacted = _lock_case_and_check_history(facility, target_id)
        elif target_type == "Activity":
            # Refs #2124: auch die Aktivitaet bekommt ihren Anker — bis dahin
            # der einzige Zweig ohne Sperre.
            already_redacted = _lock_activity_and_check(facility, target_id)
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
        if target_type in ("Event", "Client", "Case"):
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
    """Hebt einen Legal Hold auf und stellt den gehaltenen Vorschlag auf ``pending`` zurueck.

    Refs #2374: Die Einrichtungsgrenze der handelnden Person wird vor jeder
    Sperre geprueft (fail-closed ohne Einrichtung), danach wird die Hold-Zeile
    gesperrt und innerhalb dieser Einrichtung neu gelesen. Die mitgegebene
    Instanz kann aus einer Uebersicht von vor Minuten stammen: ist der Hold
    inzwischen aufgehoben, endet der Aufruf mit einer Meldung. Akteur und
    Zeitpunkt der ersten Aufhebung bleiben dann stehen, es entsteht keine
    zweite Audit-Zeile — und ein Vorschlag, den inzwischen ein NEUER Hold
    haelt, wird nicht zurueckgestellt.
    """
    actor_facility_id = getattr(user, "facility_id", None)
    if actor_facility_id is None or actor_facility_id != hold.facility_id:
        raise ValidationError(LEGAL_HOLD_OTHER_FACILITY_MESSAGE)
    locked = LegalHold.objects.select_for_update().filter(pk=hold.pk, facility_id=actor_facility_id).first()
    if locked is None:
        # Die Zeile liegt in der Datenbank nicht (mehr) in der Einrichtung der
        # handelnden Person — entschieden wird an der Zeile, nicht am Speicher.
        raise ValidationError(LEGAL_HOLD_OTHER_FACILITY_MESSAGE)
    if locked.dismissed_at is not None:
        raise ValidationError(LEGAL_HOLD_ALREADY_DISMISSED_MESSAGE)
    locked.dismissed_at = timezone.now()
    locked.dismissed_by = user
    locked.save(update_fields=["dismissed_at", "dismissed_by"])
    # Die Instanz des Aufrufers mitziehen: die View arbeitet danach mit ihr weiter.
    hold.dismissed_at = locked.dismissed_at
    hold.dismissed_by = locked.dismissed_by

    # Revert associated proposal to pending
    RetentionProposal.objects.filter(
        facility_id=locked.facility_id,
        target_type=locked.target_type,
        target_id=locked.target_id,
        status=RetentionProposal.Status.HELD,
    ).update(status=RetentionProposal.Status.PENDING)

    audit_retention_decision(
        hold.facility,
        target_type=locked.target_type,
        target_id=locked.target_id,
        action=AuditLog.Action.LEGAL_HOLD,
        category="legal_hold_dismissed",
        user=user,
        hold_id=str(locked.pk),
        reason=locked.reason,
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

    ``LegalHold.target_type`` ist ein freies ``CharField`` ohne ``choices``
    (ADR-044 §4); als Werte vergeben ``create_hold_for_target`` und die
    Aufrufer heute ``Event``/``Client``/``Case``/``Activity``. Ein Anhang ist
    nie selbst Hold-Ziel. Die Sperre wird deshalb ueber den Owner
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
