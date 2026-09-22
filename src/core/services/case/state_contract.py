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

Die Zustandspruefung liegt bewusst im Service und nicht (nur) in der View:
``cases_with_live_client`` zieht die Grenze fuer den HTTP-Pfad, aber der
Service ist die Single Source of Truth — Offline-Replay, Commands und
kuenftige Aufrufer muessen dieselbe Grenze passieren (Muster
``soft_delete_case``).
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.models import Case, Client
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


def _present(row):
    """Die Zeile — oder die Absage, wenn sie waehrend der Sperrwartezeit ging.

    Der eine Ort, an dem aus einem ``None`` der Sperrabfragen die Meldung wird:
    verschwundene Zeile, fremde Einrichtung oder ein Fall, den ein paralleles
    Zusammenfuehren inzwischen an eine ANDERE Person gehaengt hat.
    """
    if row is None:
        raise ValidationError(STATUS_CHANGE_GONE_MESSAGE)
    return row


def _locked_case(case_pk, client_id) -> Case:
    """Sperrt Person -> Fall und liefert die frische, gepruefte Fallzeile."""
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
    is_deleted, is_active = Client.objects.filter(pk=client_id).values_list("is_deleted", "is_active").first() or (
        True,
        False,
    )
    if is_deleted or not is_active:
        raise ValidationError(CLIENT_WRITE_REVOKED_MESSAGE)
    # 3. Fallzeile sperren — der Personenbezug steht IN der Bedingung, damit
    #    ein zwischenzeitlicher Wechsel nicht als Treffer durchgeht.
    locked = _present(Case.objects.filter(pk=case_pk, client_id=client_id).select_for_update().first())
    if locked.is_deleted or locked.anonymized_at is not None:
        raise ValidationError(STATUS_CHANGE_REDACTED_MESSAGE)
    return locked


def lock_case_for_status_change(case) -> Case:
    """Der Fall fuer einen Statuswechsel — frisch, gesperrt und geprueft.

    Der Personenbezug wird ungesperrt vorgelesen: die Personenzeile muss VOR
    der Fallzeile gesperrt werden, ihre PK steht aber nur am Fall (Muster
    :func:`~core.services.case.deletion._lock_case_chain`). Was sich zwischen
    Vorlesen und Sperren daran aendert, faengt die Gegenpruefung in
    :func:`_locked_case` ab.
    """
    client_id = _present(Case.objects.filter(pk=case.pk).values_list("client_id", flat=True).first())
    return _locked_case(case.pk, client_id)


def lock_goal_for_status_change(goal) -> OutcomeGoal:
    """Das Wirkungsziel fuer einen Statuswechsel — Person -> Fall -> Ziel."""
    case_pk, client_id = _present(
        OutcomeGoal.objects.filter(pk=goal.pk).values_list("case_id", "case__client_id").first()
    )
    _locked_case(case_pk, client_id)
    return _present(OutcomeGoal.objects.filter(pk=goal.pk, case_id=case_pk).select_for_update().first())


def lock_milestone_for_status_change(milestone) -> Milestone:
    """Der Meilenstein — Person -> Fall -> Ziel -> Meilenstein.

    Die Zielzeile wird mitgesperrt, obwohl der Statuswechsel sie nicht
    schreibt: sie ist die Stufe zwischen Fall und Meilenstein, und eine
    uebersprungene Stufe ist die naechste Reihenfolge, die niemand mit den
    uebrigen vergleicht.
    """
    goal_pk, case_pk, client_id = _present(
        Milestone.objects.filter(pk=milestone.pk)
        .values_list("goal_id", "goal__case_id", "goal__case__client_id")
        .first()
    )
    _locked_case(case_pk, client_id)
    _present(OutcomeGoal.objects.filter(pk=goal_pk, case_id=case_pk).select_for_update().only("pk").first())
    return _present(Milestone.objects.filter(pk=milestone.pk, goal_id=goal_pk).select_for_update().first())
