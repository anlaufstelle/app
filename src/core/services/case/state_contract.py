"""Zustandsvertrag der Statuswechsel an Fall, Ziel und Meilenstein (Refs #2288, #2295).

**Ein Statuswechsel ist die kleinste Schreiboperation der Fallakte — und war
die einzige, die die ganze Akte schrieb.** ``close_case``/``reopen_case`` und
die drei Ziel-/Meilensteinfunktionen bekamen ihre Instanz von der View, die sie
vor dem Klick gelesen hatte, setzten ein Flag und riefen ``save()`` ohne
``update_fields``. Faellt in das Fenster dazwischen eine Bearbeitung, ein
Soft-Delete oder die Tilgung (``redact_case_content``/``redact_case_goals`` —
beide auch der Kaskadenschritt der Personen-Anonymisierung), dann schreibt der
volle Save die alten Freitexte **und** die alten Zustandsmerkmale
(``is_deleted``, ``deleted_at``, ``anonymized_at``) zurueck: der getilgte Fall
steht wieder da.

**``update_fields`` allein ist keine Zustandspruefung.** Es haelt die Spalten
klein, beantwortet aber nicht die Frage, ob an dieser Akte ueberhaupt noch
etwas geaendert werden darf. Ohne die Pruefung bekaeme eine bereits getilgte
Akte weiterhin Statuswechsel, Audit-Zeilen und Zeitstrom-Eintraege — eine
Akte, die es fachlich nicht mehr gibt.

Der Vertrag, den dieses Modul durchsetzt (jede Bedingung auf dem FRISCH und
unter Sperre gelesenen Stand, nicht auf der uebergebenen Instanz):

======================================  ===================================
Zustand                                 Antwort
======================================  ===================================
Person nach Art. 18 eingeschraenkt      ``PROCESSING_RESTRICTED_MESSAGE``
Person im Papierkorb/anonymisiert       ``CLIENT_WRITE_REVOKED_MESSAGE``
Fall im Papierkorb/anonymisiert         ``STATUS_CHANGE_REDACTED_MESSAGE``
Zeile weg oder Fall umgehaengt          ``STATUS_CHANGE_GONE_MESSAGE``
sonst                                   die gesperrte, frische Zeile
======================================  ===================================

**Sperrreihenfolge** ist die kanonische aus ADR-050 D5.5 und Refs #2268:
Personenzeile zuerst — und zwar ueber den gemeinsamen, PK-geordneten Helfer
:func:`~core.services.client.locking.lock_clients`, den das Art.-18-Gate mit
``lock=True`` ohnehin nimmt —, danach der Fall, danach das Ziel, zuletzt der
Meilenstein. Absteigend entlang der Hierarchie, nie gegen sie: eine zweite
Ordnung waere der ABBA-Zyklus, den ``40P01`` aufloest. Die Personensperre
bleibt die EINE ``core_client``-Sperre dieser Pfade; die Zustandsspalten der
Person werden danach auf der bereits gesperrten Zeile gelesen.

**Voraussetzung der Ordnung.** Die ``core_client``-Sperre nimmt nicht dieses
Modul, sondern ``assert_processing_allowed(..., lock=True)``; die
Widerrufsspalten der Person werden danach auf genau dieser gesperrten Zeile
gelesen. Das Gate kehrt aber **vor** dem Sperren zurueck, wenn ein
Betroffenenrechte-Kontext offen ist (die beiden internen Ausstiege der
Restriktions-Schnittstelle fuer Berichtigung und Loeschworkflow) — dann faellt
die erste Stufe weg und der Fall
wuerde ohne vorangehende Personensperre genommen. Keine der fuenf
Statusfunktionen wird heute aus einem dieser Kontexte gerufen (Produktivcode:
nur die vier Views). Wer das aendert, muss die Personensperre hier
ausdruecklich nachziehen, sonst geht die Ordnung still verloren.

**Derselbe Vertrag fuer die uebrigen Schreibvorgaenge an der Akte** (Refs
#2329): Anlage und Bearbeitung eines Wirkungsziels
(:func:`lock_case_for_write`, :func:`lock_goal_for_write`) und die
Fallzuordnung eines Ereignisses (:func:`lock_event_for_case_link`, Sperrfolge
Person -> Fall -> Ereignis). Sie melden sich mit eigenen Texten, weil dort kein
Status gewechselt wird; ein Ereignis im Papierkorb ist fuer die Zuordnung
dasselbe wie eine getilgte Akte.

Die Zustandspruefung liegt bewusst im Service und nicht (nur) in der View:
``cases_with_live_client`` zieht die Grenze fuer den HTTP-Pfad, aber der
Service ist die Single Source of Truth — Offline-Replay, Commands und
kuenftige Aufrufer muessen dieselbe Grenze passieren (Muster
``soft_delete_case``).
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.models import Case, Client, Event
from core.models.outcome import Milestone, OutcomeGoal

#: Die Akte ist getilgt oder liegt im Papierkorb. Bewusst EINE Meldung fuer
#: beide Zustaende (Muster ``CLIENT_WRITE_REVOKED_MESSAGE``): welcher von
#: beiden vorliegt, aendert fuer die Schreibende nichts, und die
#: Unterscheidung waere ein zusaetzliches Detail ueber eine geloeschte Akte.
#: Der Weg zurueck ist das Wiederherstellen (``restore_case``) mit eigener
#: Pruefung und eigener Audit-Zeile — nicht ein Statuswechsel.
STATUS_CHANGE_REDACTED_MESSAGE = _(
    "Diese Fallakte wurde gelöscht oder anonymisiert — ihr Status kann nicht mehr geändert werden."
)

#: Die Zeile ist waehrend der Sperrwartezeit verschwunden oder der Fall haengt
#: inzwischen an einer ANDEREN Person (paralleles Zusammenfuehren). Deren
#: Zeile haben wir nie gesperrt; bewusst Abbruch statt Nachsperren
#: (Sperr-Inversion, Muster ``_lock_case_chain``). Ein zweiter Anlauf liest den
#: neuen Bezug und sperrt in der richtigen Reihenfolge.
STATUS_CHANGE_GONE_MESSAGE = _(
    "Diese Fallakte steht für eine Statusänderung nicht mehr zur Verfügung — bitte die Seite neu laden."
)


#: Gegenstuecke der beiden Meldungen fuer Schreibvorgaenge, die KEIN
#: Statuswechsel sind — Zielanlage und -bearbeitung, Fallzuordnung eines
#: Ereignisses (Refs #2329). Derselbe Vertrag, nur ohne das Wort „Status“,
#: das dort in die Irre fuehrte.
CASE_WRITE_REDACTED_MESSAGE = _(
    "Diese Fallakte wurde gelöscht oder anonymisiert — sie kann nicht mehr geändert werden."
)
CASE_WRITE_GONE_MESSAGE = _("Diese Fallakte hat sich inzwischen geändert — bitte die Seite neu laden.")

#: Das Ereignis liegt im Papierkorb. Seine Inhalte sind dort bereits geleert
#: (``soft_delete_event``); eine Zuordnungsaenderung waere eine Aenderung an
#: einem geloeschten Datensatz. Der Weg zurueck ist das Wiederherstellen.
EVENT_WRITE_REDACTED_MESSAGE = _(
    "Dieses Ereignis wurde gelöscht — seine Fallzuordnung kann nicht mehr geändert werden."
)
#: Das Ereignis ist waehrend der Sperrwartezeit verschwunden, hat die Person
#: gewechselt oder haengt inzwischen an einem anderen Fall.
EVENT_WRITE_GONE_MESSAGE = _("Dieses Ereignis hat sich inzwischen geändert — bitte die Seite neu laden.")


def _present(row, message=STATUS_CHANGE_GONE_MESSAGE):
    """Die Zeile — oder die Absage, wenn sie waehrend der Sperrwartezeit ging.

    Der eine Ort, an dem aus einem ``None`` der Sperrabfragen die Meldung wird:
    verschwundene Zeile, fremde Einrichtung oder ein Fall, den ein paralleles
    Zusammenfuehren inzwischen an eine ANDERE Person gehaengt hat.
    """
    if row is None:
        raise ValidationError(message)
    return row


def _raise_if_clients_revoked(client_ids, facility_id) -> None:
    """Widerrufszustand der Personen — gelesen auf den bereits gesperrten Zeilen.

    Eine zweite ``select_for_update``-Form daneben waere eine zweite Ordnung
    (Refs #2268), deshalb ein gewoehnliches Lesen unter der Sperre. Fehlt eine
    Zeile ganz, gilt sie als widerrufen (fail-closed).
    """
    from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE

    if not client_ids:
        return
    live = set(
        Client.objects.filter(pk__in=client_ids, facility_id=facility_id, is_deleted=False, is_active=True).values_list(
            "pk", flat=True
        )
    )
    if live != set(client_ids):
        raise ValidationError(CLIENT_WRITE_REVOKED_MESSAGE)


def _locked_case(
    case_pk,
    client_id,
    facility_id,
    *,
    redacted_message=STATUS_CHANGE_REDACTED_MESSAGE,
    gone_message=STATUS_CHANGE_GONE_MESSAGE,
) -> Case:
    """Sperrt Person -> Fall und liefert die frische, gepruefte Fallzeile.

    ``facility_id`` scopt beide Abfragen auf die Einrichtung des Falls — die
    Einrichtung ist unveraenderlich, der Filter also gratis, und er haelt die
    Zugriffe innerhalb der Mandantengrenze (Muster ``_lock_case_chain``).
    """
    from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE
    from core.services.client.restriction import assert_processing_allowed

    # 1. Personenzeile ZUERST — ueber das Art.-18-Gate, das dafuer den
    #    gemeinsamen PK-geordneten Sperrhelfer nimmt. Die Reihenfolge und die
    #    Meldung bleiben damit die der uebrigen Schreibpfade.
    assert_processing_allowed(client_id, lock=True)
    # 2. Widerrufszustand der Person auf der bereits gesperrten Zeile. Eine
    #    zweite ``select_for_update``-Form daneben waere eine zweite Ordnung
    #    (Refs #2268), deshalb ein gewoehnliches Lesen unter der Sperre. Fehlt
    #    die Zeile ganz, gilt sie als widerrufen (fail-closed).
    revoked = (
        Client.objects.filter(pk=client_id, facility_id=facility_id).values_list("is_deleted", "is_active").first()
    )
    is_deleted, is_active = revoked or (True, False)
    if is_deleted or not is_active:
        raise ValidationError(CLIENT_WRITE_REVOKED_MESSAGE)
    # 3. Fallzeile sperren — der Personenbezug steht IN der Bedingung, damit
    #    ein zwischenzeitlicher Wechsel nicht als Treffer durchgeht.
    locked = _present(
        Case.objects.filter(pk=case_pk, client_id=client_id, facility_id=facility_id).select_for_update().first(),
        gone_message,
    )
    if locked.is_deleted or locked.anonymized_at is not None:
        raise ValidationError(redacted_message)
    return locked


def lock_case_for_status_change(case) -> Case:
    """Der Fall fuer einen Statuswechsel — frisch, gesperrt und geprueft.

    Der Personenbezug wird ungesperrt vorgelesen: die Personenzeile muss VOR
    der Fallzeile gesperrt werden, ihre PK steht aber nur am Fall (Muster
    :func:`~core.services.case.deletion._lock_case_chain`). Was sich zwischen
    Vorlesen und Sperren daran aendert, faengt die Gegenpruefung in
    :func:`_locked_case` ab.
    """
    client_id, facility_id = _present(
        Case.objects.filter(pk=case.pk, facility_id=case.facility_id).values_list("client_id", "facility_id").first()
    )
    return _locked_case(case.pk, client_id, facility_id)


def lock_goal_for_status_change(goal) -> OutcomeGoal:
    """Das Wirkungsziel fuer einen Statuswechsel — Person -> Fall -> Ziel."""
    case_pk, client_id, facility_id = _present(
        OutcomeGoal.objects.filter(pk=goal.pk).values_list("case_id", "case__client_id", "case__facility_id").first()
    )
    _locked_case(case_pk, client_id, facility_id)
    return _present(OutcomeGoal.objects.filter(pk=goal.pk, case_id=case_pk).select_for_update().first())


def lock_milestone_for_status_change(milestone) -> Milestone:
    """Der Meilenstein — Person -> Fall -> Ziel -> Meilenstein.

    Die Zielzeile wird mitgesperrt, obwohl der Statuswechsel sie nicht
    schreibt: sie ist die Stufe zwischen Fall und Meilenstein, und eine
    uebersprungene Stufe ist die naechste Reihenfolge, die niemand mit den
    uebrigen vergleicht.
    """
    goal_pk, case_pk, client_id, facility_id = _present(
        Milestone.objects.filter(pk=milestone.pk)
        .values_list("goal_id", "goal__case_id", "goal__case__client_id", "goal__case__facility_id")
        .first()
    )
    _locked_case(case_pk, client_id, facility_id)
    _present(OutcomeGoal.objects.filter(pk=goal_pk, case_id=case_pk).select_for_update().only("pk").first())
    return _present(Milestone.objects.filter(pk=milestone.pk, goal_id=goal_pk).select_for_update().first())


def lock_case_for_write(case) -> Case:
    """Der Fall fuer einen inhaltlichen Schreibvorgang an seiner Akte (Refs #2329).

    Derselbe Vertrag wie :func:`lock_case_for_status_change` — Person -> Fall,
    frisch, gesperrt, geprueft —, nur mit den Meldungen fuer Vorgaenge, die
    keinen Status wechseln (etwa das Anlegen eines Wirkungsziels).
    """
    client_id, facility_id = _present(
        Case.objects.filter(pk=case.pk, facility_id=case.facility_id).values_list("client_id", "facility_id").first(),
        CASE_WRITE_GONE_MESSAGE,
    )
    return _locked_case(
        case.pk,
        client_id,
        facility_id,
        redacted_message=CASE_WRITE_REDACTED_MESSAGE,
        gone_message=CASE_WRITE_GONE_MESSAGE,
    )


def lock_goal_for_write(goal) -> OutcomeGoal:
    """Das Wirkungsziel fuer eine Bearbeitung seiner Freitexte — Person -> Fall -> Ziel.

    Die Freitexte eines Ziels ersetzt die Tilgung (``redact_case_goals``);
    wer sie danach noch schreiben duerfte, holte Inhalt an eine Akte zurueck,
    die es fachlich nicht mehr gibt (Refs #2329).
    """
    case_pk, client_id, facility_id = _present(
        OutcomeGoal.objects.filter(pk=goal.pk).values_list("case_id", "case__client_id", "case__facility_id").first(),
        CASE_WRITE_GONE_MESSAGE,
    )
    _locked_case(
        case_pk,
        client_id,
        facility_id,
        redacted_message=CASE_WRITE_REDACTED_MESSAGE,
        gone_message=CASE_WRITE_GONE_MESSAGE,
    )
    return _present(
        OutcomeGoal.objects.filter(pk=goal.pk, case_id=case_pk).select_for_update().first(),
        CASE_WRITE_GONE_MESSAGE,
    )


def lock_event_for_case_link(event, target_case=None) -> tuple[Event, Case | None, Case | None]:
    """Das Ereignis fuer eine Aenderung seiner Fallzuordnung (Refs #2329).

    Liefert ``(ereignis, zielfall, bisheriger_fall)`` — alle drei frisch,
    gesperrt und geprueft; ``zielfall`` ist ``None`` beim Loesen,
    ``bisheriger_fall`` ist ``None``, wenn das Ereignis an keinem Fall hing.

    **Zum Vorgang gehoeren BEIDE Faelle.** Das Umhaengen loest das Ereignis
    aus seiner bisherigen Akte; liegt die im Papierkorb oder ist sie getilgt,
    waere das eine Aenderung an ihr. Der Vertrag gilt deshalb fuer den
    abgebenden Fall genauso wie fuer den aufnehmenden.

    **Sperrfolge** ``core_client -> core_case -> core_event`` (ADR-050 D5.5):

    1. alle beteiligten Personen — die des Ereignisses und die beider Faelle —
       in EINER PK-geordneten Sperre ueber das Art.-18-Gate
       (:func:`~core.services.client.restriction.assert_processing_allowed_for`,
       Refs #2268); beim Zuordnen danach die fachliche Pruefung „anonymes
       Ereignis" / „andere Person"; dann der Widerrufszustand auf den
       gesperrten Zeilen;
    2. die Fallzeilen in PK-Ordnung, jede mit ihrem vorgelesenen Personenbezug
       in der Gegenpruefung;
    3. zuletzt die Ereigniszeile — mit Person UND bisheriger Zuordnung in der
       Bedingung: wer sie waehrend der Sperrwartezeit geaendert hat, hat
       Zeilen betroffen, die wir nicht gesperrt haben. Bewusst Abbruch statt
       Nachsperren (Sperr-Inversion, Muster ``_lock_case_chain``).

    Die Bezuege werden vorher ungesperrt gelesen, weil die Personenzeilen VOR
    Fall und Ereignis gesperrt werden muessen, ihre PKs aber nur dort stehen.

    Dieselbe Voraussetzung wie beim Statuswechsel (Moduldocstring): in einem
    offenen Betroffenenrechte-Kontext sperrt das Gate nicht. Beide Aufrufer
    werden heute nur aus Views gerufen.
    """
    from core.services.client.restriction import assert_processing_allowed_for

    facility_id, client_id, is_anonymous, previous_case_id, previous_case_client_id = _present(
        Event.objects.filter(pk=event.pk, facility_id=event.facility_id)
        .values_list("facility_id", "client_id", "is_anonymous", "case_id", "case__client_id")
        .first(),
        EVENT_WRITE_GONE_MESSAGE,
    )
    expected_case_clients = {}
    if previous_case_id is not None:
        expected_case_clients[previous_case_id] = previous_case_client_id
    if target_case is not None:
        expected_case_clients[target_case.pk] = _present(
            Case.objects.filter(pk=target_case.pk, facility_id=facility_id).values_list("client_id", flat=True).first(),
            CASE_WRITE_GONE_MESSAGE,
        )
    client_ids = {client_id, *expected_case_clients.values()} - {None}

    # 1. Personen
    assert_processing_allowed_for(sorted(client_ids), lock=True)
    if target_case is not None:
        # Die fachliche Zurueckweisung faellt NACH den Personensperren und VOR
        # dem Widerrufszustand: die Bezuege sind hier die vorgelesenen, und
        # Schritt 3 bestaetigt sie auf der gesperrten Ereigniszeile (die
        # Person steht dort in der Bedingung). Haengt ein paralleles
        # Zusammenfuehren das Ereignis waehrend der Sperrwartezeit um, bleibt
        # die Antwort damit die fachliche („unterschiedliche Personen") und
        # nicht die ueber den Tombstone der Quelle.
        if is_anonymous or client_id is None:
            raise ValidationError(
                _("Anonyme Ereignisse können nicht an einen Fall mit zugeordneter Person gehängt werden.")
            )
        if client_id != expected_case_clients[target_case.pk]:
            raise ValidationError(_("Person des Ereignisses passt nicht zur Person des Falls."))
    _raise_if_clients_revoked(client_ids, facility_id)
    # 2. Faelle
    cases = {
        row.pk: row
        for row in Case.objects.filter(pk__in=list(expected_case_clients), facility_id=facility_id)
        .select_for_update()
        .order_by("pk")
    }
    for case_pk, expected_client_id in expected_case_clients.items():
        row = cases.get(case_pk)
        if row is None or row.client_id != expected_client_id:
            raise ValidationError(CASE_WRITE_GONE_MESSAGE)
        if row.is_deleted or row.anonymized_at is not None:
            raise ValidationError(CASE_WRITE_REDACTED_MESSAGE)
    # 3. Ereignis
    locked_event = _present(
        Event.objects.filter(pk=event.pk, facility_id=facility_id, client_id=client_id, case_id=previous_case_id)
        .select_for_update()
        .first(),
        EVENT_WRITE_GONE_MESSAGE,
    )
    if locked_event.is_deleted:
        raise ValidationError(EVENT_WRITE_REDACTED_MESSAGE)
    target = cases[target_case.pk] if target_case is not None else None
    return locked_event, target, cases.get(previous_case_id)


def lock_milestone_for_write(milestone) -> Milestone:
    """Der Meilenstein fuer seine Loeschung — Person -> Fall -> Ziel -> Meilenstein (Refs #2358).

    Derselbe Vertrag und dieselbe Sperrfolge wie
    :func:`lock_milestone_for_status_change`, nur mit den Meldungen fuer
    Schreibvorgaenge, die keinen Status wechseln (Muster
    :func:`lock_goal_for_write`). Die Loeschung eines Meilensteins aendert den
    Inhalt der Akte und schreibt Audit- und Zeitstrom-Zeile; an einer Akte im
    Papierkorb, einer getilgten Akte oder der Akte einer Person im Papierkorb
    waere das eine Aenderung an einem Datensatz, den es fachlich nicht mehr
    gibt. Die Anlage eines Meilensteins nimmt :func:`lock_goal_for_write` —
    einen Meilenstein, der gesperrt werden koennte, gibt es dort noch nicht.
    """
    goal_pk, case_pk, client_id, facility_id = _present(
        Milestone.objects.filter(pk=milestone.pk)
        .values_list("goal_id", "goal__case_id", "goal__case__client_id", "goal__case__facility_id")
        .first(),
        CASE_WRITE_GONE_MESSAGE,
    )
    _locked_case(
        case_pk,
        client_id,
        facility_id,
        redacted_message=CASE_WRITE_REDACTED_MESSAGE,
        gone_message=CASE_WRITE_GONE_MESSAGE,
    )
    _present(
        OutcomeGoal.objects.filter(pk=goal_pk, case_id=case_pk).select_for_update().only("pk").first(),
        CASE_WRITE_GONE_MESSAGE,
    )
    return _present(
        Milestone.objects.filter(pk=milestone.pk, goal_id=goal_pk).select_for_update().first(),
        CASE_WRITE_GONE_MESSAGE,
    )
