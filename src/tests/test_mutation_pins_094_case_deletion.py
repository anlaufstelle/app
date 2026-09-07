"""Mutations-Pins zum 0.94-Checkpoint — ``core.services.case.deletion``.

Quelle: ``src/core/services/case/deletion.py`` (Loeschworkflow fuer Faelle,
Refs #1818/ADR-044, Sperrkette Refs #2123). Gepinnt wird das Prio-1-Set der
Survivor-Triage des Checkpoint-Deltas: 20 Mutanten in sieben Funktionen, die
allesamt **stille** Fehlfunktionen erzeugen — falscher Antrag im
Vier-Augen-Workflow, facility-/fallfremde Redaktion, eine Sperrkette, die die
falschen (oder gar keine) Zeilen sperrt, ein Nacht-Batch, der nach dem ersten
Uebersprung abbricht, und ein Zaehler, der das Tilgungsvolumen untertreibt.

Konventionen wie ``src/tests/test_mutation_pins_091_cut.py``: Zusicherungen
vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten); Sprachkontext wird wo noetig per
``translation.override("de")`` festgenagelt; ``pytest.mark.django_db`` nur, wo
die DB wirklich gebraucht wird (hier: ueberall, die Pfade sind reine
ORM-Schreibwege); KEINE Imports aus ``test_rls_functional`` (modulweiter Skip
unter ``MUTANT_UNDER_TEST`` wuerde jeden Pin entwerten); keine gefakten
Fixtures — jeder Test legt echte Model-Zeilen an. Jeder Test nennt seine
Mutanten in der Docstring und benennt den Wert, der beim Mutanten abweicht.

Eine Testklasse je Funktion:

* :class:`TestGetPendingCaseDeletionRequestFiltertVollstaendig` — mutmut 6/7/8
* :class:`TestPendingDeletionCaseIdsBleibtBeimZieltyp` — mutmut 11
* :class:`TestRedactCaseContentSchreibtNurDieRedaktionsfelder` — mutmut 8
* :class:`TestRedactCaseActivitiesBleibtAmEigenenFall` — mutmut 5
* :class:`TestRedactCaseDeletionRequestBleibtAmEigenenAntrag` — mutmut 4/5
* :class:`TestLockCaseChainSperrtDieRichtigenZeilen` — mutmut 8/17/24/26/32/33/35
* :class:`TestAnonymizeEligibleSoftDeletedCasesBatchSemantik` — mutmut 36/37/41/46/54
"""

import re
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Activity, Case, Client, DocumentType, Event, LegalHold
from core.models.workitem import DeletionRequest
from core.services.case import deletion as deletion_mod
from core.services.case.deletion import (
    _lock_case_chain,
    _redact_case_activities,
    _redact_case_deletion_request,
    anonymize_eligible_soft_deleted_cases,
    get_pending_case_deletion_request,
    pending_deletion_case_ids,
    redact_case_content,
    restore_case,
)
from core.services.dashboard import log_activity

#: UUID-Literale, wie psycopg sie in ``connection.queries`` zurueckmeldet:
#: ``last_executed_query`` gibt das mogrifizierte Statement zurueck, die
#: Parameter stehen also inline — und zwar als ``'<32 Hexziffern>'::uuid``
#: (ohne Bindestriche, daher der Vergleich gegen ``UUID.hex``). Facility-,
#: Case- und Client-PKs sind alle ``UUIDField`` (``core/models/organization.py``,
#: ``core/models/case.py``, ``core/models/client.py``) — die Menge der im
#: ``WHERE`` genannten UUIDs ist damit ein exakter Abzug der Filterbedingung.
_UUID_RE = re.compile(r"'([0-9a-fA-F]{32})'::uuid")


def _person(facility, staff_user, pseudonym):
    """Echte Personenzeile — kein Fixture-Fake."""
    return Client.objects.create(
        facility=facility,
        pseudonym=pseudonym,
        contact_stage=Client.ContactStage.IDENTIFIED,
        created_by=staff_user,
    )


def _fall(facility, person, staff_user, titel, *, created_at=None):
    fall = Case.objects.create(
        facility=facility,
        client=person,
        title=titel,
        status=Case.Status.OPEN,
        created_by=staff_user,
    )
    if created_at is not None:
        # ``created_at`` ist ``auto_now_add`` — die Iterations-/``.first()``-
        # Reihenfolge (``Case.Meta.ordering = ["-created_at"]``) wird deshalb
        # nachtraeglich festgenagelt, damit die Pins nicht von der
        # Anlage-Mikrosekunde abhaengen.
        Case.objects.filter(pk=fall.pk).update(created_at=created_at)
        fall.refresh_from_db()
    return fall


def _im_papierkorb(fall, settings_obj, *, tage_ueber_frist=1):
    """Setzt den Fall faellig in den Papierkorb (P2b: ``client_trash_days``)."""
    Case.objects.filter(pk=fall.pk).update(
        is_deleted=True,
        deleted_at=timezone.now() - timedelta(days=settings_obj.client_trash_days + tage_ueber_frist),
    )
    fall.refresh_from_db()
    return fall


def _for_update_sql(captured, tabelle):
    """Das eine ``SELECT … FOR UPDATE`` auf ``tabelle`` aus dem Mitschnitt."""
    treffer = [
        query["sql"]
        for query in captured.captured_queries
        if "FOR UPDATE" in query["sql"].upper() and f'"{tabelle}"' in query["sql"]
    ]
    assert len(treffer) == 1, f"Erwartet genau eine Sperr-Query auf {tabelle}, gefunden: {treffer!r}"
    return treffer[0]


def _uuids(sql):
    return {wert.lower() for wert in _UUID_RE.findall(sql)}


# ============================================================================
# core.services.case.deletion.get_pending_case_deletion_request
# ============================================================================


@pytest.mark.django_db
class TestGetPendingCaseDeletionRequestFiltertVollstaendig:
    """Der laufende Antrag eines Falls — alle drei Bezugsfilter tragen.

    ``DeletionRequest.target_id`` ist ein blankes ``UUIDField`` ohne FK und
    ohne DB-Constraint auf ``target_type`` (``core/models/workitem.py``).
    Einrichtung, Zieltyp und Ziel-ID sind deshalb die einzige Trennung
    zwischen den Antraegen dreier Modelle in einer Tabelle. Faellt einer der
    drei Filter weg, liefert ``.first()`` einen fremden Antrag — mit fremdem
    Antragsteller, fremder Begruendung und fremdem Freigabestand.

    Hinweis zur Zuordnung: im Mutantenbaum entfaellt bei ``mutmut_7`` der
    ``target_type``- und bei ``mutmut_8`` der ``target_id``-Filter (verifiziert
    an ``mutants/src/core/services/case/deletion.py``). Die Docstrings folgen
    dem Mutantenbaum.
    """

    def test_antrag_einer_fremden_einrichtung_zaehlt_nicht(
        self, facility, second_facility, client_identified, staff_user
    ):
        """Pin: ``x_get_pending_case_deletion_request__mutmut_6`` — ``facility_id=case.facility_id`` entfaellt.

        ``facility_id`` ist laut Migration 0047 Defense-in-Depth zur
        RLS-Policy; im Test wird ``app.current_facility_id`` nie gesetzt, der
        ORM-Filter ist hier also die einzige Mandantentrennung. Die Zeile wird
        gezielt inkonsistent gemacht (Antrag zeigt auf einen Fall der
        Einrichtung A, traegt aber Einrichtung B) — genau die Konstellation,
        die der Filter abfangen soll.

        Original: ``None`` (kein Antrag der eigenen Einrichtung).
        Mutant: der facility-fremde Antrag wird geliefert (``is not None``).
        """
        fall = _fall(facility, client_identified, staff_user, "Fall mit Antrag")
        antrag = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=fall.pk,
            status=DeletionRequest.Status.PENDING,
            reason="Betroffenenwunsch",
            requested_by=staff_user,
        )
        DeletionRequest.objects.filter(pk=antrag.pk).update(facility=second_facility)

        assert get_pending_case_deletion_request(fall) is None

    def test_jeder_fall_bekommt_seinen_eigenen_antrag(self, facility, client_identified, staff_user):
        """Pin: ``x_get_pending_case_deletion_request__mutmut_8`` — ``target_id=case.pk`` entfaellt.

        Zwei gleichzeitig offene Vier-Augen-Antraege in derselben Einrichtung
        sind Alltag, kein Corner-Case. ``DeletionRequest.Meta.ordering`` ist
        ``["-created_at"]``; die ``created_at``-Stempel werden deshalb
        festgenagelt, damit der juengere Antrag (``antrag_b``) deterministisch
        der ``.first()``-Treffer eines filterlosen Querysets ist.

        Original: ``get_pending(...fall_a).pk == antrag_a.pk``.
        Mutant: fuer ``fall_a`` kommt ``antrag_b`` zurueck (falscher
        Antragsteller/Grund) — die pk-Gleichheit schlaegt fehl.
        """
        fall_a = _fall(facility, client_identified, staff_user, "Fall A")
        fall_b = _fall(facility, client_identified, staff_user, "Fall B")
        jetzt = timezone.now()
        antrag_a = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=fall_a.pk,
            status=DeletionRequest.Status.PENDING,
            reason="Antrag A",
            requested_by=staff_user,
        )
        antrag_b = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=fall_b.pk,
            status=DeletionRequest.Status.PENDING,
            reason="Antrag B",
            requested_by=staff_user,
        )
        DeletionRequest.objects.filter(pk=antrag_a.pk).update(created_at=jetzt - timedelta(hours=2))
        DeletionRequest.objects.filter(pk=antrag_b.pk).update(created_at=jetzt)

        assert get_pending_case_deletion_request(fall_a).pk == antrag_a.pk
        assert get_pending_case_deletion_request(fall_b).pk == antrag_b.pk

    def test_personenantrag_mit_gleicher_ziel_id_zaehlt_nicht(self, facility, client_identified, staff_user):
        """Pin: ``x_get_pending_case_deletion_request__mutmut_7`` — ``target_type=CASE`` entfaellt.

        ``target_id`` traegt je nach ``target_type`` eine Event-, Client- oder
        Case-UUID; ohne den Typfilter wuerde ein Personen-Loeschantrag mit
        derselben Ziel-ID als Fall-Antrag angezeigt und im
        Vier-Augen-Workflow behandelt.

        Original: ``None`` (der Fall selbst hat keinen Antrag).
        Mutant: liefert den ``CLIENT``-Antrag (``is not None``).
        """
        fall = _fall(facility, client_identified, staff_user, "Fall ohne eigenen Antrag")
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=fall.pk,
            status=DeletionRequest.Status.PENDING,
            reason="Personen-Antrag, ID-Kollision",
            requested_by=staff_user,
        )

        assert get_pending_case_deletion_request(fall) is None


# ============================================================================
# core.services.case.deletion.pending_deletion_case_ids
# ============================================================================


@pytest.mark.django_db
class TestPendingDeletionCaseIdsBleibtBeimZieltyp:
    """Das Fallliste-Badge darf nur eigene Fall-Antraege melden."""

    def test_personenantrag_setzt_kein_fall_badge(self, facility, case_open, staff_user):
        """Pin: ``x_pending_deletion_case_ids__mutmut_11`` — ``target_type=CASE`` entfaellt.

        Ein PENDING-Antrag vom Typ ``CLIENT`` mit der Fall-UUID als
        ``target_id`` wuerde ohne Typfilter das „Loeschantrag laeuft"-Badge
        an einem Fall setzen, der gar keinen eigenen Antrag hat.

        Original: ``set()``.
        Mutant: ``{case_open.pk}``.
        """
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=case_open.pk,
            status=DeletionRequest.Status.PENDING,
            reason="Personen-Antrag, ID-Kollision",
            requested_by=staff_user,
        )

        assert pending_deletion_case_ids(facility, [case_open]) == set()


# ============================================================================
# core.services.case.deletion.redact_case_content
# ============================================================================


@pytest.mark.django_db
class TestRedactCaseContentSchreibtNurDieRedaktionsfelder:
    """Die Redaktion ist ein Teil-Save, kein Voll-Save.

    ``Case.updated_at`` ist ``auto_now=True``. Mit expliziter
    ``update_fields``-Liste ruft Django ``pre_save`` nur fuer die genannten
    Felder auf — ``updated_at`` bleibt stehen und die Redaktion schreibt genau
    fuenf Spalten. Ohne die Liste wird die komplette In-Memory-Instanz
    zurueckgeschrieben: der Anonymisierungslauf haelt beim Redigieren keine
    Row-Locks, ein parallel geaendertes Feld waere damit still ueberschrieben
    (Lost-Update).
    """

    def test_updated_at_bleibt_stehen(self, facility, client_identified, staff_user):
        """Pin: ``x_redact_case_content__mutmut_8`` — ``update_fields=[…5 Felder…]`` → ``update_fields=None``.

        Original: ``updated_at`` unveraendert (nicht in ``update_fields``).
        Mutant: Voll-Save loest ``auto_now`` aus, ``updated_at`` springt auf
        jetzt — der Vergleich mit dem vorher gelesenen Stempel schlaegt fehl.
        """
        fall = _fall(facility, client_identified, staff_user, "Fall mit Zeitstempel")
        fall.refresh_from_db()
        vorher = fall.updated_at

        redact_case_content(fall)

        fall.refresh_from_db()
        assert fall.updated_at == vorher
        # Positiv-Kontrolle: die Redaktion hat wirklich stattgefunden.
        assert fall.description == ""
        assert fall.data_json == {}
        assert fall.document_type_id is None
        assert fall.anonymized_at is not None


# ============================================================================
# core.services.case.deletion._redact_case_activities
# ============================================================================


@pytest.mark.django_db
class TestRedactCaseActivitiesBleibtAmEigenenFall:
    """Die Aktivitaets-Redaktion (#1972) darf nur den getilgten Fall treffen."""

    def test_aktivitaet_eines_anderen_falls_bleibt_unangetastet(self, facility, client_identified, staff_user):
        """Pin: ``x__redact_case_activities__mutmut_5`` — ``target_id=case.pk`` entfaellt.

        Ohne den ``target_id``-Filter setzt jede Einzeltilgung den Kontext
        JEDER Case-Aktivitaet der Einrichtung auf ``REDACTED`` — die
        Aktivitaets-Historie aller anderen Faelle waere still ueberschrieben.

        Original: ``kontext_b`` bleibt ``{"schema": 1, "action": "case_closed"}``.
        Mutant: ``kontext_b`` wird zu ``{"schema": 1, "action": "redacted"}``.
        """
        fall_a = _fall(facility, client_identified, staff_user, "Fall A (wird getilgt)")
        fall_b = _fall(facility, client_identified, staff_user, "Fall B (bleibt)")
        aktivitaet_a = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=fall_a,
            action=Activity.Action.CASE_CLOSED,
        )
        aktivitaet_b = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=fall_b,
            action=Activity.Action.CASE_CLOSED,
        )
        kontext_b_vorher = Activity.objects.get(pk=aktivitaet_b.pk).context

        _redact_case_activities(fall_a)

        assert Activity.objects.get(pk=aktivitaet_b.pk).context == kontext_b_vorher
        # Positiv-Kontrolle: der eigene Fall IST redigiert.
        assert Activity.objects.get(pk=aktivitaet_a.pk).context == {
            "schema": 1,
            "action": Activity.Action.REDACTED.value,
        }


# ============================================================================
# core.services.case.deletion._redact_case_deletion_request
# ============================================================================


@pytest.mark.django_db
class TestRedactCaseDeletionRequestBleibtAmEigenenAntrag:
    """Muster ``TestDeletionRequestRedactionKeepsItsTargetType`` fuer den Fall-Pfad.

    Der Freitext ``reason`` wird redigiert (Muster #1091) — aber nur der des
    eigenen Antrags. ``DeletionRequest`` traegt Antraege aller drei Zieltypen
    in einer Tabelle, ``target_id`` ist ein blankes ``UUIDField``: Zieltyp UND
    Ziel-ID muessen beide filtern.
    """

    def test_fremdtyp_mit_gleicher_ziel_id_bleibt_lesbar(self, facility, client_identified, staff_user):
        """Pin: ``x__redact_case_deletion_request__mutmut_4`` — ``target_type=CASE`` entfaellt.

        Original: ``koeder.reason == "Fremdtyp, ID-Kollision"``.
        Mutant: der Koeder wird mitredigiert (``"[Anonymisiert]"``).
        """
        fall = _fall(facility, client_identified, staff_user, "Fall mit Antrag")
        eigener = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=fall.pk,
            status=DeletionRequest.Status.PENDING,
            reason="Fall doppelt angelegt",
            requested_by=staff_user,
        )
        koeder = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=fall.pk,
            status=DeletionRequest.Status.PENDING,
            reason="Fremdtyp, ID-Kollision",
            requested_by=staff_user,
        )

        _redact_case_deletion_request(fall)

        eigener.refresh_from_db()
        koeder.refresh_from_db()
        assert eigener.reason == "[Anonymisiert]"
        assert koeder.reason == "Fremdtyp, ID-Kollision"

    def test_antrag_eines_anderen_falls_bleibt_lesbar(self, facility, client_identified, staff_user):
        """Pin: ``x__redact_case_deletion_request__mutmut_5`` — ``target_id=case.pk`` entfaellt.

        Ohne den ``target_id``-Filter wuerde jede Fall-Tilgung den
        Begruendungstext SAEMTLICHER Fall-Loeschantraege ueberschreiben.

        Original: ``fremder.reason == "Anderer Fall, muss stehen bleiben"``.
        Mutant: auch der fremde Antrag traegt ``"[Anonymisiert]"``.
        """
        fall = _fall(facility, client_identified, staff_user, "Fall mit Antrag")
        anderer_fall = _fall(facility, client_identified, staff_user, "Anderer Fall")
        eigener = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=fall.pk,
            status=DeletionRequest.Status.PENDING,
            reason="Fall doppelt angelegt",
            requested_by=staff_user,
        )
        fremder = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=anderer_fall.pk,
            status=DeletionRequest.Status.PENDING,
            reason="Anderer Fall, muss stehen bleiben",
            requested_by=staff_user,
        )

        _redact_case_deletion_request(fall)

        eigener.refresh_from_db()
        fremder.refresh_from_db()
        assert eigener.reason == "[Anonymisiert]"
        assert fremder.reason == "Anderer Fall, muss stehen bleiben"


# ============================================================================
# core.services.case.deletion._lock_case_chain
# ============================================================================


@pytest.mark.django_db
class TestLockCaseChainSperrtDieRichtigenZeilen:
    """Der Anker der Fall-Tilgung (Refs #2123, ADR-050 D5.5).

    Die vorhandenen Sequenz-Pins (``test_sofortpfad_sperrt_in_kanonischer_
    ordnung``) pruefen per Tabellennamen-Regex nur die REIHENFOLGE der
    ``FOR UPDATE``-Statements. WELCHE Zeilen gesperrt werden, sah bislang
    kein Test — genau dort sitzen die hier gepinnten Mutanten: eine Sperrkette,
    die den falschen Fall, die falsche Person oder gar keine Ereigniszeile
    erwischt, laesst das TOCTOU-Fenster fuer einen waehrend der Redaktion
    angeordneten Hold offen (Spoliation, Muster #2108).
    """

    def _zwei_faelle(self, facility, staff_user):
        """Zwei Faelle derselben Einrichtung mit verschiedenen Personen.

        ``fall_falsch`` ist der juengere und damit unter
        ``Case.Meta.ordering = ["-created_at"]`` der ``.first()``-Treffer eines
        Querysets ohne ``pk``-Filter.
        """
        jetzt = timezone.now()
        person_falsch = _person(facility, staff_user, "Anker-Person falsch")
        person_ziel = _person(facility, staff_user, "Anker-Person Ziel")
        fall_ziel = _fall(
            facility, person_ziel, staff_user, "Zu sperrender Fall", created_at=jetzt - timedelta(hours=2)
        )
        fall_falsch = _fall(facility, person_falsch, staff_user, "Unbeteiligter Fall", created_at=jetzt)
        return fall_ziel, fall_falsch, person_ziel

    def test_sperrt_genau_den_angeforderten_fall(self, facility, staff_user):
        """Pin: ``x__lock_case_chain__mutmut_8`` und ``…__mutmut_24`` — je ein ``pk=case_pk`` entfaellt.

        mutmut_8 nimmt den Personenbezug (``client_id``) aus einem BELIEBIGEN
        Fall der Einrichtung, mutmut_24 sperrt eine BELIEBIGE Fallzeile.
        Beide fallen in dieser Konstellation ueber den
        ``locked.client_id != client_id``-Vergleich.

        Original: ``fall_ziel`` (``ergebnis.pk == fall_ziel.pk``).
        Beide Mutanten: ``None`` — der faellige Fall wuerde still nicht
        getilgt bzw. der falsche Fall gesperrt.
        """
        fall_ziel, fall_falsch, _person_ziel = self._zwei_faelle(facility, staff_user)

        ergebnis = _lock_case_chain(facility.pk, fall_ziel.pk)

        assert ergebnis is not None
        assert ergebnis.pk == fall_ziel.pk
        assert ergebnis.pk != fall_falsch.pk

    def test_personenzeile_wird_mit_ihrer_pk_gesperrt(self, facility, staff_user):
        """Pin: ``x__lock_case_chain__mutmut_17`` — ``pk=client_id`` am ``Client``-Lock entfaellt.

        Die Personenzeile ist die Kernsicherung gegen Spoliation: ein
        waehrend der Redaktion angeordneter Hold auf der ECHTEN Person wird
        nur blockiert, wenn genau deren Zeile gesperrt ist. Der Rueckgabewert
        aendert sich dabei nicht — gepinnt wird deshalb die WHERE-Bedingung
        des mitgeschnittenen Statements: die Menge der genannten UUIDs wird
        EXAKT verglichen.

        Original: ``{person.pk, facility.pk}``.
        Mutant: ``{facility.pk}`` — die Sperre traefe eine beliebige Person.
        """
        fall_ziel, _fall_falsch, person_ziel = self._zwei_faelle(facility, staff_user)

        with CaptureQueriesContext(connection) as captured:
            _lock_case_chain(facility.pk, fall_ziel.pk)

        sql = _for_update_sql(captured, "core_client")
        assert _uuids(sql) == {person_ziel.pk.hex, facility.pk.hex}

    def test_ereigniszeilen_werden_fallgenau_gesperrt(self, facility, staff_user):
        """Pin: ``x__lock_case_chain__mutmut_32``, ``…__mutmut_33``, ``…__mutmut_35``.

        mutmut_32 setzt ``facility_id=None`` (die Sperre laeuft leer),
        mutmut_33 setzt ``case_id=None`` (sperrt hoechstens entkoppelte
        Ereignisse), mutmut_35 laesst den ``case_id``-Filter ganz weg (sperrt
        ALLE Ereigniszeilen der Einrichtung — im Nacht-Batch ein
        Lock-Contention-Risiko gegen jeden parallelen Ereignis-Schreibweg).
        Keiner der drei aendert den Rueckgabewert; gepinnt wird die
        WHERE-Bedingung des Statements.

        Original: ``{facility.pk, fall.pk}``.
        mutmut_32: ``{fall.pk}`` (``facility_id IS NULL``).
        mutmut_33 und mutmut_35: ``{facility.pk}``.
        """
        fall_ziel, _fall_falsch, person_ziel = self._zwei_faelle(facility, staff_user)
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Sperr-Typ",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        Event.objects.create(
            facility=facility,
            client=person_ziel,
            case=fall_ziel,
            document_type=doc_type,
            occurred_at=timezone.now(),
            created_by=staff_user,
        )

        with CaptureQueriesContext(connection) as captured:
            _lock_case_chain(facility.pk, fall_ziel.pk)

        sql = _for_update_sql(captured, "core_event")
        assert _uuids(sql) == {facility.pk.hex, fall_ziel.pk.hex}

    def test_umgehaengter_fall_bricht_sauber_ab(self, facility, staff_user, monkeypatch):
        """Pin: ``x__lock_case_chain__mutmut_26`` — ``locked is None or …`` → ``locked is None and …``.

        Der dokumentierte Abbruchgrund: ein paralleler Merge haengt den Fall
        waehrend der Sperrwartezeit an eine ANDERE Person. Deren Zeile wurde
        nie gesperrt, ihr Hold also nie verlaesslich gesehen — bewusst
        Abbruch statt Nachsperren (Sperr-Inversion). Das Umhaengen wird an der
        realistischen Naht ausgeloest: waehrend der Personen-Sperre, also
        genau zwischen den beiden Case-Lesevorgaengen.

        Original: ``None`` (``locked.client_id != client_id`` greift).
        Mutant: ``locked is None`` ist False, ``and`` kurzschliesst auf False —
        die Funktion liefert den umgehaengten Fall zurueck und die Tilgung
        liefe ohne Anker auf der neuen Person weiter.
        """
        person_alt = _person(facility, staff_user, "Person alt")
        person_neu = _person(facility, staff_user, "Person neu")
        fall = _fall(facility, person_alt, staff_user, "Fall im Merge")

        echtes_filter = Client.objects.filter
        ausgeloest = {"done": False}

        def _merge_dann_filtern(*args, **kwargs):
            if not ausgeloest["done"]:
                ausgeloest["done"] = True
                Case.objects.filter(pk=fall.pk).update(client=person_neu)
            return echtes_filter(*args, **kwargs)

        monkeypatch.setattr(Client.objects, "filter", _merge_dann_filtern)

        ergebnis = _lock_case_chain(facility.pk, fall.pk)

        assert ausgeloest["done"] is True, "Das Merge-Fenster wurde nicht geoeffnet — Aufbau kaputt"
        assert ergebnis is None

    def test_ohne_merge_liefert_die_sperrkette_den_fall(self, facility, staff_user):
        """Positiv-Kontrolle zu :meth:`test_umgehaengter_fall_bricht_sauber_ab`.

        Ohne die Naht liefert dieselbe Sperrkette den Fall — der ``None``-Pin
        oben pinnt also den Abbruch, nicht eine generell tote Funktion.
        """
        person = _person(facility, staff_user, "Person stabil")
        fall = _fall(facility, person, staff_user, "Fall stabil")

        ergebnis = _lock_case_chain(facility.pk, fall.pk)

        assert ergebnis is not None
        assert ergebnis.pk == fall.pk


# ============================================================================
# core.services.case.deletion.anonymize_eligible_soft_deleted_cases
# ============================================================================


@pytest.mark.django_db
class TestAnonymizeEligibleSoftDeletedCasesBatchSemantik:
    """Der Nacht-Batch: Re-Check unter Sperre, Uebersprung statt Abbruch, ehrlicher Zaehler.

    Alle Pins dieser Klasse brauchen ZWEI faellige Faelle in einem Lauf — genau
    das hatte bislang kein Test. Die Naht fuer die Renn-Szenarien ist der
    modulglobale ``_lock_case_chain``: er wird je Fall INNERHALB der eigenen
    Transaktion gerufen, also nach der Materialisierung der pk-Liste. Der
    Wrapper delegiert an das Original — die Sperrkette selbst bleibt echt.
    """

    def _zwei_faellige_faelle(self, facility, settings_obj, staff_user):
        person_a = _person(facility, staff_user, "Batch-Person A")
        person_b = _person(facility, staff_user, "Batch-Person B")
        fall_a = _fall(facility, person_a, staff_user, "Batch-Fall A")
        fall_b = _fall(facility, person_b, staff_user, "Batch-Fall B")
        return _im_papierkorb(fall_a, settings_obj), _im_papierkorb(fall_b, settings_obj)

    def _erster_durchlauf(self, monkeypatch, aktion):
        """Haengt ``aktion(case_pk)`` vor den ERSTEN ``_lock_case_chain``-Aufruf."""
        echte_kette = deletion_mod._lock_case_chain
        gesehen = {}

        def _wrapper(facility_id, case_pk):
            if "erster" not in gesehen:
                gesehen["erster"] = case_pk
                aktion(case_pk)
            return echte_kette(facility_id, case_pk)

        monkeypatch.setattr(deletion_mod, "_lock_case_chain", _wrapper)
        return gesehen

    def test_zaehlt_jeden_getilgten_fall(self, facility, settings_obj, staff_user):
        """Pin: ``x_anonymize_eligible_soft_deleted_cases__mutmut_54`` — ``count += 1`` → ``count = 1``.

        ``count`` ist kein Diagnosewert, sondern das von
        ``enforce_retention``/``_RetentionTotals`` konsumierte Betriebssignal
        („Anonymized N trash-expired case(s)"). Eine Untertreibung verschleiert
        das tatsaechliche Tilgungsvolumen.

        Original: ``2``.
        Mutant: ``1`` (beide Faelle werden getilgt, gemeldet wird einer).
        """
        fall_a, fall_b = self._zwei_faellige_faelle(facility, settings_obj, staff_user)

        assert anonymize_eligible_soft_deleted_cases(facility, settings_obj) == 2

        fall_a.refresh_from_db()
        fall_b.refresh_from_db()
        assert fall_a.anonymized_at is not None
        assert fall_b.anonymized_at is not None

    def test_waehrenddessen_wiederhergestellter_fall_wird_uebersprungen(
        self, facility, settings_obj, staff_user, monkeypatch
    ):
        """Pin: ``…__mutmut_36`` (``or`` → ``and``) und ``…__mutmut_37`` (erstes ``or`` → ``and``).

        Der Restore-Race: zwischen Materialisierung der pk-Liste und der
        Zeilensperre holt eine Fachkraft den Fall aus dem Papierkorb zurueck.
        Der Re-Check ``not locked.is_deleted`` muss ihn dann ueberspringen.

        Original: der wiederhergestellte Fall bleibt unberuehrt, der zweite
        wird getilgt, ``count == 1``.
        mutmut_36: ``not is_deleted and anonymized_at is not None`` ist bei
        einem restaurierten Fall ``True and False`` = ``False`` — der Fall
        faellt durch und wird redigiert (``anonymized_at is not None``,
        ``count == 2``).
        mutmut_37: ``(locked is None and not locked.is_deleted) or
        locked.anonymized_at is not None`` — bei nicht-``None`` ``locked``
        bleibt nur der ``anonymized_at``-Test uebrig, die
        ``is_deleted``-Pruefung ist ganz weg; identischer Ausgang.
        """
        fall_a, fall_b = self._zwei_faellige_faelle(facility, settings_obj, staff_user)
        faelle = {fall_a.pk: fall_a, fall_b.pk: fall_b}

        gesehen = self._erster_durchlauf(
            monkeypatch,
            lambda case_pk: restore_case(Case.objects.get(pk=case_pk), staff_user),
        )

        count = anonymize_eligible_soft_deleted_cases(facility, settings_obj)

        assert "erster" in gesehen, "Das Restore-Fenster wurde nicht geoeffnet — Aufbau kaputt"
        wiederhergestellt = faelle[gesehen["erster"]]
        rest = faelle[next(pk for pk in faelle if pk != gesehen["erster"])]
        wiederhergestellt.refresh_from_db()
        rest.refresh_from_db()
        assert wiederhergestellt.is_deleted is False
        assert wiederhergestellt.anonymized_at is None
        assert rest.anonymized_at is not None
        assert count == 1

    def test_nicht_mehr_faelliger_fall_stoppt_den_lauf_nicht(self, facility, settings_obj, staff_user, monkeypatch):
        """Pin: ``x_anonymize_eligible_soft_deleted_cases__mutmut_41`` — ``continue`` → ``break`` im Recheck-Zweig.

        Der Re-Check trifft einen Fall, den ein paralleler Lauf schon
        anonymisiert hat. ``break`` beendet die gesamte Schleife: alle danach
        folgenden, tatsaechlich faelligen Faelle blieben still ungetilgt
        (Verstoss gegen die Speicherbegrenzungspflicht, ohne Fehlermeldung).

        Original: ``count == 1``, der zweite Fall traegt ``anonymized_at``.
        Mutant: ``count == 0``, der zweite Fall bleibt unberuehrt
        (``anonymized_at is None``).
        """
        fall_a, fall_b = self._zwei_faellige_faelle(facility, settings_obj, staff_user)
        faelle = {fall_a.pk: fall_a, fall_b.pk: fall_b}
        stempel = timezone.now()

        gesehen = self._erster_durchlauf(
            monkeypatch,
            lambda case_pk: Case.objects.filter(pk=case_pk).update(anonymized_at=stempel),
        )

        count = anonymize_eligible_soft_deleted_cases(facility, settings_obj)

        assert "erster" in gesehen, "Das Recheck-Fenster wurde nicht geoeffnet — Aufbau kaputt"
        rest = faelle[next(pk for pk in faelle if pk != gesehen["erster"])]
        rest.refresh_from_db()
        assert rest.anonymized_at is not None
        assert count == 1

    def test_gehaltener_fall_stoppt_den_lauf_nicht(self, facility, settings_obj, staff_user, monkeypatch):
        """Pin: ``x_anonymize_eligible_soft_deleted_cases__mutmut_46`` — ``continue`` → ``break`` im Hold-Zweig.

        Der Hold-Zweig stellt eine Tilgung nur ZURUECK. ``break`` machte daraus
        einen Abbruch des gesamten Laufs. Der vorhandene Race-Test
        (``test_hold_zwischen_sammeln_und_verarbeiten_ueberspringt_den_fall``)
        trifft den gehaltenen Fall zufaellig als LETZTES Element und deckt den
        Unterschied deshalb nicht auf; hier wird der Hold gezielt auf den
        ZUERST iterierten Fall gelegt.

        Original: ``count == 1``, der freie Fall traegt ``anonymized_at``.
        Mutant: ``count == 0``, der freie Fall bleibt unberuehrt.
        """
        fall_a, fall_b = self._zwei_faellige_faelle(facility, settings_obj, staff_user)
        faelle = {fall_a.pk: fall_a, fall_b.pk: fall_b}

        def _hold_anordnen(case_pk):
            LegalHold.objects.create(
                facility=facility,
                target_type="Case",
                target_id=case_pk,
                reason="Anordnung waehrend des Laufs",
                created_by=staff_user,
            )

        gesehen = self._erster_durchlauf(monkeypatch, _hold_anordnen)

        count = anonymize_eligible_soft_deleted_cases(facility, settings_obj)

        assert "erster" in gesehen, "Das Hold-Fenster wurde nicht geoeffnet — Aufbau kaputt"
        gehalten = faelle[gesehen["erster"]]
        frei = faelle[next(pk for pk in faelle if pk != gesehen["erster"])]
        gehalten.refresh_from_db()
        frei.refresh_from_db()
        assert gehalten.anonymized_at is None
        assert frei.anonymized_at is not None
        assert count == 1
