"""Loeschworkflow fuer Faelle — Papierkorb, Frist, Tilgung (Refs #1818, ADR-044).

Spiegelt den Personen-Workflow aus :mod:`core.services.client.main`
(``request_client_deletion`` … ``anonymize_eligible_soft_deleted_clients``).
Die Produktentscheide der ADR bestimmen die Abweichungen:

* **P1c** — Vier-Augen nur, wenn die Person auf der Stufe ``QUALIFIED`` steht
  (:func:`case_requires_four_eyes`); sonst der direkte Weg
  (:func:`soft_delete_case`). Muster: ``EventDeleteView.post``.
* **P2b** — keine eigene Frist: der Papierkorb laeuft ueber
  ``Settings.client_trash_days``.
* **P3a** — nach Fristablauf wird **redigiert**, nicht hart geloescht. Ein
  ``delete()`` risse per CASCADE Episoden, Wirkungsziele und Meilensteine mit
  und machte die Statistik-Zeitreihen unstetig.
* **P4** — Legal Hold auf Fall, Ereignis des Falls oder Person sperrt die
  Tilgung (:func:`core.retention.legal_holds.exclude_cases_with_active_holds`).
* **P5** — Ereignisse ueberleben; sie verlieren bei der Tilgung nur ihre
  Zuordnung und bleiben in der Personenakte sichtbar.
* **P6** — auch offene Faelle sind loeschbar; ``Case.Status`` spielt keine Rolle.

Die Redaktions-Helfer :func:`redact_case_content` und :func:`redact_case_goals`
liegen hier und werden vom Personen-Pfad mitbenutzt (dort als
``_redact_cases_and_episodes``/``_redact_case_goals``). Zwei Kopien derselben
Redaktion waeren genau die Drift, die #1756 gekostet hat: beide Pfade muessen
denselben Rest hinterlassen, sonst haengt das Ergebnis davon ab, ob eine Person
oder ein einzelner Fall geloescht wurde.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import Activity, AuditLog, Case, Client, DeletionRequest
from core.retention.legal_holds import case_has_active_hold, exclude_cases_with_active_holds
from core.services.audit import audit_event
from core.services.dashboard import log_activity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Antrag und Staffelung (P1c)
# ---------------------------------------------------------------------------


def case_requires_four_eyes(case) -> bool:
    """Braucht dieser Fall den Vier-Augen-Antrag? (P1c)

    Gestaffelt nach ``Client.contact_stage`` — dieselbe Grenze, die
    ``EventDeleteView.post`` fuer Ereignisse zieht: bei einer Person der Stufe
    „Mit Zusatzangaben" (``QUALIFIED``) entscheidet eine zweite Person, sonst
    loescht die Fachkraft direkt. Der Fall ist an genau eine Person gebunden
    (Pflicht-FK), die Staffelung hat also immer eine Quelle.
    """
    return case.client.contact_stage == Client.ContactStage.QUALIFIED


def get_pending_case_deletion_request(case):
    """Der laufende (PENDING) Loeschantrag dieses Falls — oder ``None``.

    Muster ``get_pending_deletion_request`` (Personen-Pfad, Refs #1736): eine
    Quelle fuer alle Anzeigeflaechen. ``requested_by`` wird mitgeladen, weil
    jede von ihnen die antragstellende Person nennt.
    """
    return (
        DeletionRequest.objects.filter(
            facility_id=case.facility_id,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=case.pk,
            status=DeletionRequest.Status.PENDING,
        )
        .select_related("requested_by")
        .first()
    )


def pending_deletion_case_ids(facility, cases) -> set:
    """PKs derjenigen ``cases``, fuer die ein PENDING-Loeschantrag laeuft.

    Fuer das Badge in der Fallliste — bewusst nur fuer die bereits paginierte
    Seite: eine Query je Seite statt einer je Zeile.
    """
    ids = [case.pk for case in cases]
    if not ids:
        return set()
    return set(
        DeletionRequest.objects.for_facility(facility)
        .filter(target_type=DeletionRequest.TargetType.CASE, target_id__in=ids, status=DeletionRequest.Status.PENDING)
        .values_list("target_id", flat=True)
    )


def request_case_deletion(case, user, reason):
    """Stellt einen Vier-Augen-Loeschantrag fuer einen Fall — nur der Antrag.

    Duenne Huelle um :func:`create_or_get_case_deletion_request` fuer alle
    Aufrufer, die den Unterschied „neu angelegt" vs. „lief schon" nicht
    brauchen.
    """
    return create_or_get_case_deletion_request(case, user, reason)[0]


def create_or_get_case_deletion_request(case, user, reason):
    """Wie :func:`request_case_deletion`, aber ``(antrag, created)``.

    Refs #2015 (Re-Review): Muster ``create_or_get_client_deletion_request``.
    Zwischen dem Vorab-Check der View und diesem Aufruf kann ein paralleler
    Request denselben Antrag anlegen; der Zweite faellt dann in den
    Idempotenz-Zweig unten und braucht die Idempotenz-Antwort statt einer
    Erfolgsmeldung fuer etwas, das nie passiert ist.

    Idempotent: laeuft bereits ein PENDING-Antrag, wird dieser zurueckgegeben
    und **kein** zweites Audit-Event geschrieben (Muster #932) — die
    ``DELETION_REQUESTED``-Zeile existiert vom ersten Aufruf.
    """
    existing = get_pending_case_deletion_request(case)
    if existing is not None:
        return existing, False
    dr = DeletionRequest.objects.create(
        facility=case.facility,
        target_type=DeletionRequest.TargetType.CASE,
        target_id=case.pk,
        reason=reason,
        requested_by=user,
    )
    audit_event(
        action=AuditLog.Action.DELETION_REQUESTED,
        user=user,
        facility=case.facility,
        target_type="DeletionRequest",
        target_id=str(dr.pk),
        # Refs #1093: ``reason`` nicht ins detail duplizieren — er lebt in
        # DeletionRequest.reason (ueber target_id=dr.pk erreichbar).
        detail={"target_case": str(case.pk)},
    )
    return dr, True


# ---------------------------------------------------------------------------
# Papierkorb
# ---------------------------------------------------------------------------


def _move_case_to_trash(case, user, **detail):
    """Gemeinsamer Kern beider Loeschwege (direkt und genehmigt).

    Setzt ``is_deleted`` ueber die Mixin-Methode (die zugleich ``deleted_at``
    fuellt — ohne das liefe der Fall nie in den Fristablauf), schreibt die
    ``CASE_SOFT_DELETED``-Zeile und den Zeitstrom-Eintrag.
    """
    if case.is_deleted:
        raise ValidationError(_("Der Fall liegt bereits im Papierkorb."))
    case.soft_delete(user=user)
    audit_event(
        AuditLog.Action.CASE_SOFT_DELETED,
        user=user,
        facility=case.facility,
        target_obj=case,
        detail=detail or None,
    )
    log_activity(
        facility=case.facility,
        actor=user,
        verb=Activity.Verb.DELETED,
        target=case,
        action=Activity.Action.CASE_TRASHED,
    )
    return case


@transaction.atomic
def soft_delete_case(case, user):
    """Direkter Loeschweg ohne Vier-Augen-Antrag (P1c, nicht-qualifizierte Person).

    Der Aufrufer (View) entscheidet ueber :func:`case_requires_four_eyes`,
    welcher der beiden Wege gilt; **beide** Guards liegen zusaetzlich hier, weil
    der Service die Single Source of Truth ist — das gilt seit dem Re-Review
    auch fuer den Art.-18-Sperrvermerk (Refs #2045), der zuvor nur ungesperrt in
    der View stand und damit weder den Direktaufruf noch das TOCTOU-Fenster
    abdeckte. Genau dieser Widerspruch zwischen SSoT-Zusage und tatsaechlicher
    Guard-Lage war der Befund.

    Ein Kontext-Ausstieg wie beim Ereignis ist hier NICHT noetig: der gepruefte
    Weg laeuft ueber ``approve_case_deletion`` -> ``_move_case_to_trash`` und
    beruehrt diese Funktion nie. Die Trennung ist also strukturell, nicht
    parametrisch.
    """
    from core.services.client.restriction import assert_processing_allowed

    assert_processing_allowed(case.client_id, lock=True)
    if case_requires_four_eyes(case):
        raise ValidationError(_("Für Fälle einer Person mit Zusatzangaben ist ein Löschantrag mit Bestätigung nötig."))
    return _move_case_to_trash(case, user, trigger="direct")


@transaction.atomic
def approve_case_deletion(deletion_request, reviewer):
    """Genehmigt einen Fall-Loeschantrag und legt den Fall in den Papierkorb.

    Vier-Augen-Prinzip: der Reviewer darf nicht der Antragsteller sein und
    braucht das Recht ``can_confirm_deletion``. Beide Guards liegen im Service
    (SSoT), nicht nur in der View.

    TOCTOU-Schutz (Muster #1376 I9): der Antrag wird innerhalb der Transaktion
    per ``select_for_update()`` neu geladen und der Status erneut geprueft —
    sonst loesten zwei zeitgleiche Genehmigungen desselben (noch PENDING
    gelesenen) Antrags beide den Soft-Delete aus.
    """
    if deletion_request.requested_by_id == reviewer.pk:
        raise ValidationError(_("Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip)."))
    if not reviewer.can_confirm_deletion:
        raise ValidationError(_("Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip)."))

    deletion_request = DeletionRequest.objects.select_for_update().get(
        pk=deletion_request.pk, facility=deletion_request.facility
    )
    if deletion_request.status != DeletionRequest.Status.PENDING:
        raise ValidationError(_("Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend)."))

    case = Case.objects.get(pk=deletion_request.target_id, facility=deletion_request.facility)
    _move_case_to_trash(case, reviewer, deletion_request=str(deletion_request.pk))

    deletion_request.status = DeletionRequest.Status.APPROVED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()
    # Zusaetzlich zur ``CASE_SOFT_DELETED``-Zeile, wie im Ereignis-Pfad
    # (``services/events/deletion.approve_deletion``) und anders als im
    # Personen-Pfad: die generische ``DELETION_APPROVED``-Zeile macht die
    # Antrags-Entscheidung als eigenen Schritt sichtbar (Art. 5 Abs. 2) — und
    # sie ist es, die ``detect_mass_client_destruction`` zaehlt.
    audit_event(
        action=AuditLog.Action.DELETION_APPROVED,
        user=reviewer,
        facility=deletion_request.facility,
        target_type="DeletionRequest",
        target_id=str(deletion_request.pk),
        detail={"target_case": str(deletion_request.target_id)},
    )
    return case


@transaction.atomic
def reject_case_deletion(deletion_request, reviewer):
    """Lehnt einen Fall-Loeschantrag ab — Status→REJECTED, kein Soft-Delete.

    Der Fall bleibt unveraendert erreichbar; ein erneuter Antrag ist moeglich
    (die Unique-Constraint greift nur fuer PENDING-Zeilen).

    Derselbe PENDING-Re-Check wie in :func:`approve_case_deletion` — anders als
    im Personen-Pfad (``reject_client_deletion``), und aus einem Grund, den es
    dort nicht gibt: dieser Weg schreibt zusaetzlich eine
    ``DELETION_REJECTED``-Zeile in das append-only AuditLog. Ohne den Guard
    liesse sich ein bereits genehmigter Antrag auf REJECTED zurueckdrehen,
    waehrend der Fall im Papierkorb liegen bleibt — der widerspruechliche
    Zustand waere damit nicht bloss transient, sondern dauerhaft protokolliert.
    Heute deckt ``DeletionRequestReviewView`` das per ``status=PENDING``-Scope
    ab; der Service ist die Single Source of Truth (dasselbe Argument wie an
    jeder anderen Stelle dieses Moduls).
    """
    if deletion_request.requested_by_id == reviewer.pk:
        raise ValidationError(_("Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip)."))
    deletion_request = DeletionRequest.objects.select_for_update().get(
        pk=deletion_request.pk, facility=deletion_request.facility
    )
    if deletion_request.status != DeletionRequest.Status.PENDING:
        raise ValidationError(_("Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend)."))
    deletion_request.status = DeletionRequest.Status.REJECTED
    deletion_request.reviewed_by = reviewer
    deletion_request.reviewed_at = timezone.now()
    deletion_request.save()
    # Rechenschaft ueber ABGELEHNTE Loeschversuche (Art. 5 Abs. 2) — Muster
    # Ereignis-Pfad. Kein ``CASE_SOFT_DELETED``: nur die Genehmigung loescht.
    audit_event(
        action=AuditLog.Action.DELETION_REJECTED,
        user=reviewer,
        facility=deletion_request.facility,
        target_type="DeletionRequest",
        target_id=str(deletion_request.pk),
        detail={"target_case": str(deletion_request.target_id)},
    )


@transaction.atomic
def restore_case(case, user):
    """Holt einen Fall aus dem Papierkorb zurueck — mit Episoden und Zielen.

    Verlustfrei, weil der Papierkorb nur ``is_deleted`` setzt: Episoden,
    Wirkungsziele, Anhaenge und die Ereignis-Zuordnung bleiben unangetastet,
    bis die Frist ablaeuft.

    Nach der Tilgung ist genau das nicht mehr wahr: ``anonymized_at`` markiert
    einen Fall, dessen Inhalt unwiderruflich weg ist. Ein Restore holte dann
    eine inhaltsleere Huelle zurueck in ``cases_with_live_client()`` — also in
    Fallliste, Detail und Statistik. Die Person hat gegen denselben Fehlgriff
    ihr ``is_active=False`` als Backstop; der Fall braucht den Guard hier.
    """
    if not case.is_deleted:
        raise ValidationError(_("Der Fall liegt nicht im Papierkorb."))
    if case.anonymized_at is not None:
        raise ValidationError(_("Der Fall wurde endgültig anonymisiert und kann nicht wiederhergestellt werden."))
    case.restore()
    audit_event(
        AuditLog.Action.CASE_RESTORED,
        user=user,
        facility=case.facility,
        target_obj=case,
    )
    return case


# ---------------------------------------------------------------------------
# Redaktion (P3a) — geteilt mit dem Personen-Anonymisierungspfad
# ---------------------------------------------------------------------------


def redact_case_content(case) -> None:
    """Leert Titel, Beschreibung und Dynamikdaten eines Falls samt Episoden.

    M2 P5 (Refs #1058, Entscheid E7): ``data_json`` traegt freie (auch
    verschluesselte) Feldwerte und wird mitgeleert; ``document_type`` faellt auf
    ``NULL`` zurueck, damit der ``PROTECT``-Verweis anonymisierte Bestaende
    nicht dauerhaft an einen Konfigurationstyp kettet.

    Das Leeren von ``data_json`` erledigt zugleich die Marker-Bereinigung der
    Invariante I-M1 (ADR-039 §7) fuer den physischen Anhang-Loeschpfad.

    ``anonymized_at`` wird hier gesetzt und nicht erst in :func:`anonymize_case`:
    diese Funktion ist die geteilte Redaktion, die auch der Personen-Pfad
    aufruft (``_redact_cases_and_episodes``). Der Stempel gehoert an den Ort,
    der den Inhalt tatsaechlich entfernt — sonst traegt ihn nur der Fall-Weg
    und die Faelle einer anonymisierten Person blieben als „nur im Papierkorb"
    markiert. Ein bereits gesetzter Stempel bleibt stehen: er datiert die
    **erste** Tilgung, spaetere Laeufe finden ohnehin nichts mehr vor.
    """
    from core.models.episode import Episode

    case.title = f"[Anonymisiert {case.created_at:%Y-%m-%d}]"
    case.description = ""
    case.data_json = {}
    case.document_type = None
    case.anonymized_at = case.anonymized_at or timezone.now()
    case.save(update_fields=["title", "description", "data_json", "document_type", "anonymized_at"])
    Episode.objects.filter(case_id=case.pk).update(
        title="Episode (anonymisiert)",
        description="",
    )


def redact_case_goals(case_ids: list) -> None:
    """Anonymisiert Wirkungsziele + Meilensteine der Faelle (Refs #1751, Punkt 1).

    ``OutcomeGoal.title``/``description`` und ``Milestone.title`` sind Freitexte
    am Fall („Wohnung finden fuer …"). Die Fortschritts-Marker
    (``is_achieved``/``is_completed``, Datumsfelder) bleiben als anonyme
    Kennzahl erhalten.
    """
    from core.models.outcome import Milestone, OutcomeGoal

    if not case_ids:
        return
    goal_ids = list(OutcomeGoal.objects.filter(case_id__in=case_ids).values_list("pk", flat=True))
    if not goal_ids:
        return
    OutcomeGoal.objects.filter(pk__in=goal_ids).update(
        title="Wirkungsziel (anonymisiert)",
        description="",
    )
    Milestone.objects.filter(goal_id__in=goal_ids).update(title="Meilenstein (anonymisiert)")


def _redact_case_activities(case) -> None:
    """Setzt den PII-freien Kontext als Defense-in-Depth zurueck (#1972)."""
    from django.contrib.contenttypes.models import ContentType

    Activity.objects.for_facility(case.facility).filter(
        target_type=ContentType.objects.get_for_model(Case),
        target_id=case.pk,
    ).update(context={"schema": 1, "action": Activity.Action.REDACTED.value})


def _redact_case_deletion_request(case) -> None:
    """Redigiert den Freitext ``reason`` des Fall-Loeschantrags (Muster #1091).

    Antrags-Meta (status/requested_by/reviewed_by/timestamps) bleibt fuer den
    Vier-Augen-Nachweis erhalten.
    """
    DeletionRequest.objects.filter(
        target_type=DeletionRequest.TargetType.CASE,
        target_id=case.pk,
    ).update(reason="[Anonymisiert]")


def _detach_events(case) -> int:
    """Loest die Ereignisse vom Fall (P5) und meldet ihre Zahl.

    ``Event.case`` ist zwar ``SET_NULL``, aber die Kaskade feuert nie: die
    Tilgung ist eine Redaktion, die Fall-Zeile bleibt stehen (P3a). Die
    Zuordnung wird deshalb ausdruecklich geloest — die Ereignisse bleiben
    vollstaendig in der Personenakte sichtbar, sie verlieren nur den Rahmen.
    Erst hier, nicht schon beim Soft-Delete: sonst waere ein Wiederherstellen
    aus dem Papierkorb verlustbehaftet.
    """
    from core.models.event import Event

    return Event.objects.filter(case_id=case.pk).update(case=None)


@transaction.atomic
def anonymize_case(case, user=None, *, trigger):
    """Endgueltige Tilgung eines Falls im Papierkorb (P3a) — ohne Zugangs-Guards.

    Reihenfolge wie im Personen-Pfad (Refs #1782): erst alle DB-Redaktionen,
    **zuletzt** die physische Datei-Loeschung. Der ``.enc``-Unlink ist nicht
    rollback-bar — laeuft er vor einer noch ausstehenden Redaktion und bricht
    diese ab, bleibt eine Zeile ohne Datei zurueck (Download 500 statt 404).

    ``delete_attachments_for(case)`` und das Leeren von ``case.data_json``
    (in :func:`redact_case_content`) liegen im selben ``atomic`` — Invariante
    I-M1, ADR-039 §7: kein ``__files__``-Marker ohne Zeile.

    Der Fall bleibt danach soft-geloescht: anonymisiert, nicht auferstanden.
    """
    from core.services.file_vault import delete_attachments_for

    deleted_at = case.deleted_at
    redact_case_content(case)
    redact_case_goals([case.pk])
    _redact_case_activities(case)
    _redact_case_deletion_request(case)
    _detach_events(case)
    delete_attachments_for(case)

    audit_event(
        AuditLog.Action.CASE_ANONYMIZED,
        user=user,
        facility=case.facility,
        target_obj=case,
        detail={"trigger": trigger, "deleted_at": str(deleted_at)},
    )
    return case


def _lock_case_chain(facility_id, case_pk):
    """Sperrt ``core_client`` -> ``core_case`` -> die Ereigniszeilen des Falls (Refs #2123).

    Der Anker der Fall-Tilgung, Gegenstueck zu
    :func:`core.services.client.main._lock_client_event_rows` und
    :func:`core.retention.legal_holds._lock_case_and_check_history`. Die
    Reihenfolge ist die kanonische aus ADR-050 D5.5: die Personenzeile zuerst
    (sie ist die erste Stufe, die Sofortpfad, Nacht-Batch, K-Anon-Bridge,
    Ereignis-Sweep UND die Fall-Hold-Anlage nehmen), der Fall danach, die
    Ereigniszeilen zuletzt in ``pk``-Ordnung. Die Gegenordnung waere der
    ABBA-Zyklus, den D5.5 ausschliesst.

    Ohne diesen Anker las die Fall-Tilgung ``case_has_active_hold`` voellig
    ungesperrt: ``anonymize_case`` oeffnet seine ``atomic`` erst danach, ein
    waehrend der Redaktion angeordneter Fall-Hold kam also zu spaet und der
    Fall war schon irreversibel redigiert (Spoliation, Muster #2108).

    ``None`` heisst „nicht (mehr) zu tilgen": die Zeile ist verschwunden, ist
    einer anderen Einrichtung zugeordnet — oder ein paralleler Merge hat den
    Fall waehrend der Sperrwartezeit an eine ANDERE Person gehaengt. Deren
    Zeile haben wir nie gesperrt und ihren Hold damit nie verlaesslich gesehen;
    bewusst Abbruch statt Nachsperren (Sperr-Inversion, Muster
    ``_lock_event_and_recheck_hold``). Ein spaeterer Lauf bzw. ein zweiter
    Anlauf liest den neuen Bezug und sperrt in der richtigen Reihenfolge.
    """
    from core.models.event import Event

    # Personenbezug ungesperrt vorlesen: die Personenzeile muss VOR der
    # Fallzeile gesperrt werden, ihre pk steht aber nur am Fall.
    client_id = Case.objects.filter(pk=case_pk, facility_id=facility_id).values_list("client_id", flat=True).first()
    if client_id is None:
        return None
    Client.objects.filter(pk=client_id, facility_id=facility_id).select_for_update().only("pk").first()
    locked = Case.objects.filter(pk=case_pk, facility_id=facility_id).select_for_update().first()
    if locked is None or locked.client_id != client_id:
        return None
    list(Event.objects.filter(facility_id=facility_id, case_id=case_pk).order_by("pk").select_for_update().only("pk"))
    return locked


def anonymize_case_now(case, user):
    """Tilgt einen Fall im Papierkorb sofort — auf Admin-Anforderung.

    Zwei harte Voraussetzungen, beide als deutsche ``ValidationError`` (der
    Aufrufer ist eine View mit Meldungsflaeche), Muster
    ``anonymize_client_now`` (Refs #1737):

    (a) **Nur aus dem Papierkorb.** Sonst entstuende an der Vier-Augen-
        Genehmigung vorbei ein Direktpfad in die unwiderrufliche Tilgung.
    (b) **Kein aktiver Legal Hold** — auf dem Fall, einem seiner Ereignisse
        oder der Person (P4). Eine manuelle Aktion darf einen Hold nicht
        aushebeln (Spoliation).
    """
    # Refs #2123: ALLE Vorbedingungen liegen jetzt hinter der Sperrkette und
    # werden auf der GESPERRTEN Fall-Instanz gelesen — erst dann sind „im
    # Papierkorb", „noch nicht getilgt" und „kein Hold" stabile Aussagen und
    # nicht der Stand von vorhin. ``anonymize_case`` traegt selbst
    # ``@transaction.atomic``; das ist hier ein Savepoint, kein zweiter Commit.
    with transaction.atomic():
        locked = _lock_case_chain(case.facility_id, case.pk)
        if locked is None or not locked.is_deleted:
            # Zeile verschwunden, fremde Einrichtung oder waehrend der
            # Sperrwartezeit wiederhergestellt/umgehaengt — dieselbe Antwort wie
            # fuer einen Fall ausserhalb des Papierkorbs (Muster
            # ``anonymize_client_now``).
            raise ValidationError(_("Nur Fälle im Papierkorb können anonymisiert werden."))
        if locked.anonymized_at is not None:
            raise ValidationError(_("Der Fall wurde bereits endgültig anonymisiert."))
        if case_has_active_hold(locked):
            raise ValidationError(
                _("Für diesen Fall besteht eine Aufbewahrungssperre (Legal Hold) — Anonymisierung nicht möglich.")
            )
        anonymize_case(locked, user=user, trigger="manual")
        # Die uebergebene Instanz stammt aus einem Lesevorgang von VOR der
        # Sperrwartezeit, der Aufrufer arbeitet nach der Tilgung aber mit IHR
        # weiter (View-Meldung, ``restore_case``-Guard). Sie bekommt deshalb den
        # terminalen Stand der gesperrten Zeile — und bleibt, wie vor Refs #2123,
        # das zurueckgegebene Objekt.
        case.refresh_from_db()
        return case


def anonymize_eligible_soft_deleted_cases(facility, settings_obj, *, dry_run=False) -> int:
    """Tilgt Faelle, deren Papierkorb-Frist abgelaufen ist. Aufruf aus ``enforce_retention``.

    P2(b): die Frist ist ``Settings.client_trash_days`` — bewusst **keine**
    eigene ``case_trash_days``. Ein zweiter Fristbegriff muesste in
    Datenschutzerklaerung und DSFA erklaert werden, ohne dass ihn irgendetwas
    fachlich traegt: der Papierkorb ist in beiden Faellen die Frist zwischen
    „geloescht" und „unwiderruflich".

    P4: Faelle mit aktivem Legal Hold sind ausgeschlossen — die Tilgung wird
    dadurch nur **aufgeschoben**, ein spaeterer Lauf holt sie nach.

    ``anonymized_at__isnull=True`` ist die Abbruchbedingung: der Fall bleibt
    nach der Tilgung soft-geloescht und erfuellt ``deleted_at__lt=cutoff``
    weiter. Ohne den Filter faende ihn JEDER naechtliche Lauf erneut, redigierte
    den bereits leeren Rest noch einmal und schriebe jedes Mal eine neue
    ``CASE_ANONYMIZED``-Zeile in das append-only, HMAC-verkettete AuditLog —
    unbegrenztes Wachstum, und der Zaehler „Anonymized N trash-expired case(s)"
    bliebe dauerhaft von Null verschieden und damit als Betriebssignal
    wertlos.

    Returns: Anzahl der getilgten Faelle.
    """
    cutoff = timezone.now() - timedelta(days=settings_obj.client_trash_days)
    qs = exclude_cases_with_active_holds(
        Case.objects.for_facility(facility).filter(is_deleted=True, deleted_at__lt=cutoff, anonymized_at__isnull=True),
        facility,
    )
    if dry_run:
        return qs.count()

    # Refs #2123 (Muster ``anonymize_eligible_soft_deleted_clients``, #2013/#2108):
    # Der Hold-Ausschluss oben lief EINMAL beim Queryset-Bau. Ueber einen Lauf
    # mit vielen Faellen liegen dazwischen Minuten — ein in dieser Zeit
    # angeordneter Hold erreichte die bereits gesammelten Faelle nicht mehr.
    # Deshalb je Fall: eigene Transaktion, Sperrkette in kanonischer Ordnung,
    # und alle Bedingungen ein zweites Mal AUF DER GESPERRTEN Zeile. Die
    # pk-Liste wird vorab materialisiert — der Lauf veraendert genau die
    # Spalten, nach denen die Queryset-Bedingung filtert.
    count = 0
    deferred = 0
    for case_pk in list(qs.values_list("pk", flat=True)):
        with transaction.atomic():
            locked = _lock_case_chain(facility.pk, case_pk)
            if locked is None or not locked.is_deleted or locked.anonymized_at is not None:
                # Ein paralleler Lauf war schneller, der Fall wurde
                # wiederhergestellt oder haengt inzwischen an einer anderen
                # Person — nichts zu tun.
                continue
            if case_has_active_hold(locked):
                # Nur AUFGESCHOBEN: ein spaeterer Lauf holt die Tilgung nach,
                # sobald der Hold aufgehoben oder abgelaufen ist.
                deferred += 1
                continue
            anonymize_case(locked, user=None, trigger="trash_days_expired")
            count += 1
    if deferred:
        # Der Batch hat keine Meldungsflaeche; „nicht getilgt" und „nicht
        # angeschaut" muessen fuer die Spoliationsfrage trotzdem
        # unterscheidbar bleiben (dieselbe Zaehl-Semantik wie ``deferred`` am
        # Ereignis-Sweep). Die Signatur bleibt ``int`` — die Aufrufer
        # (``enforce_retention``/``_RetentionTotals``) zaehlen getilgte Faelle.
        logger.info(
            "Fall-Tilgung: %s Fall/Faelle wegen aktiver Aufbewahrungssperre zurueckgestellt (facility=%s)",
            deferred,
            facility.pk,
        )
    return count
