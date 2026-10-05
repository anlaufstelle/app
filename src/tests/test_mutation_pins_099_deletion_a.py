"""Pin-Tests der Welle L (0.99) — Vollmutations-Survivors in
``core.services.case.deletion`` (Batch ``deletion-a``).

Refs #2174.
Refs #2193.

Quelle: Vollmutations-Lauf auf main@80aba384 (frischer Mutantenbaum), Batch
``deletion-a`` (67 Survivor-Mutanten in fuenf Funktionen: ``x_approve_case_deletion``
23, ``x_reject_case_deletion`` 16, ``x_anonymize_eligible_soft_deleted_cases`` 10,
``x_soft_delete_case`` 9, ``x_restore_case`` 9). Batch ``deletion-b`` (die
restlichen Funktionen desselben Moduls: ``case_requires_four_eyes``,
``get_pending_case_deletion_request``, ``pending_deletion_case_ids``,
``request_case_deletion``, ``create_or_get_case_deletion_request``,
``redact_case_content``, ``redact_case_goals``, ``_redact_case_activities``,
``_redact_case_deletion_request``, ``_detach_events``, ``anonymize_case``,
``_lock_case_chain``, ``anonymize_case_now``) gehoert einem anderen
Batch-Agenten und wird hier NICHT angefasst.

Vier wiederkehrende Mutationsachsen in diesem Batch:

* **Vier-Augen-/Trash-Guard-Meldungen auf ``None``** (``ValidationError(None)``
  bzw. ``_(None)`` — beide werten zur Laufzeit identisch aus, siehe
  ``core.utils.translation``): die jeweilige ``if``-Bedingung bleibt
  unveraendert (kein Bypass), nur der Meldungstext wird woertlich ``"None"``
  statt des deutschen Fehlertexts. ``skip`` bleiben reine Gross-/Klein-
  schreibungs- bzw. ``"XX...XX"``-Wrap-Varianten derselben Strings (reine
  i18n-Textvarianten ohne Hash-/Schluessel-/Vergleichsrolle, docs/testing/
  mutation-testing.md).
* **``pk``/``facility`` aus einem ``select_for_update().get(...)``- oder
  ``Case.objects.get(...)``-Lookup entfernt** — Muster identisch zu
  ``test_mutation_pins_097_events_deletion.py`` (``x_approve_deletion``/
  ``x_reject_deletion``, Welle J): faellt ``pk``, entscheidet nur noch die
  Einrichtung (bei mehr als einem Antrag/Fall: ``MultipleObjectsReturned``
  statt der gezielten Aktion); faellt ``facility``, kann ein Antrag/Fall
  einer FREMDEN Einrichtung getroffen werden. Prio 1 — genau die Mandanten-
  grenze, die das Modul an drei Stellen als SSoT beansprucht.
* **Audit-Feldwerte auf ``None``/Text-Varianten** (``user``, ``target_type``,
  ``reviewed_at``) in den ``audit_event(...)``-Aufrufen: die Aktion selbst
  (Genehmigung/Ablehnung/Wiederherstellung) findet weiterhin korrekt statt,
  nur das Attributions-/Klassifikations-Detail der AuditLog-Zeile geht
  verloren oder wird verfaelscht. ``target_type`` hat laut Grep-Nachweis
  (``detect_mass_client_destruction``/``_detect_facility_action_burst``,
  ``core/services/compliance/breach_detection.py``) KEINEN Leser, der danach
  filtert — trotzdem zaehlt der Wert als Audit-Detail und
  wird deshalb exakt gepinnt, nicht als ``skip`` behandelt. ``user=None``
  bleibt Prio 2 (die Zeile bleibt unter der Einrichtung sichtbar, nur ohne
  Akteur); ``facility=None`` auf der ``CASE_RESTORED``-Zeile ist Prio 1 — die
  Zeile faellt aus jeder facility-gescopten Audit-Ansicht heraus und ist
  damit fuer die zustaendige Einrichtung praktisch unterdrueckt.
* **``deferred``-Zaehler in ``anonymize_eligible_soft_deleted_cases``**
  (Start-/Akkumulationswert, Log-Argumente): der Zaehler fliesst NUR in die
  ``logger.info(...)``-Zeile (kein Rueckgabewert, keine Persistenz) — anders
  als reine Text-/Gross-Klein-Varianten DERSELBEN Zeile (``skip``, docs/
  testing/mutation-testing.md: „Logging … sind in der Regel irrelevant")
  aendern die Wert-Mutanten aber, WAS gemeldet wird (falscher/verschwiegener
  Hold-Rueckstau), und werden deshalb als Prio-2-Testluecke gepinnt.

``x_anonymize_eligible_soft_deleted_cases__mutmut_13`` (``is_deleted=True``
aus dem Ausgangs-Filter entfernt) ist die einzige ``equivalent``-Einstufung
dieses Batches: die Schleife prueft ``not locked.is_deleted`` ein zweites Mal
AUF DER GESPERRTEN Zeile (Kommentar „alle Bedingungen ein zweites Mal", Refs
#2123) — im ECHTEN Lauf ist das Ausgangsfilter also redundant. Beobachtbar
bleibt der Mutant nur im ``dry_run=True``-Vorschau-Pfad, der VOR der Schleife
zurueckkehrt und den Recheck deshalb nie sieht — dort ist er eine echte
Testluecke (Prio 2, siehe ``TestAnonymizeEligibleSoftDeletedCasesDryRunFilter``
unten) und NICHT equivalent fuer den kompletten Batch.

``x_soft_delete_case__mutmut_1/_2/_4/_5`` (``assert_processing_allowed``:
Client-Argument -> ``None`` bzw. ``lock`` weg/``None``/``False``) sind Prio 1
ohne jede Redundanz: der Aufruf in ``soft_delete_case`` ist die EINZIGE
Art.-18-Pruefung auf diesem Pfad (Grep-Nachweis: kein zweiter Aufruf in
``deletion.py`` oder ``core/models/case.py``); ``None`` gilt laut Docstring
von ``assert_processing_allowed`` explizit als „kein Personenbezug und damit
nie eingeschraenkt" — ein glatter Bypass, kein Grenzfall.

Konventionen wie ``test_mutation_pins_097_events_deletion.py``: eine
Testklasse je Familie, jeder Test nennt seine Mutanten in der Docstring samt
der Abweichung, Zusicherungen vergleichen EXAKT (``==``/``is``), nie per
Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten und liefert bei
gemeinsamen Wortstaemmen sonst falsch-positive Treffer); ``pytest.mark.
django_db`` modulweit, keine Fakes — es laufen echte Model-Objekte durch die
echten Services; kein Import aus ``tests.test_rls_functional``.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import AuditLog, Case, DeletionRequest
from core.services.case.deletion import (
    anonymize_eligible_soft_deleted_cases,
    approve_case_deletion,
    reject_case_deletion,
    restore_case,
    soft_delete_case,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _case(facility, client, user, *, title, **extra):
    return Case.objects.create(facility=facility, client=client, title=title, created_by=user, **extra)


def _dr(facility, case, user, *, reason):
    return DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.CASE,
        target_id=case.pk,
        reason=reason,
        requested_by=user,
    )


#: ``FOR UPDATE``-Statements verraten die Zeilensperre; Muster wie
#: ``test_mutation_pins_097_case_rest.py`` (nicht importiert — eigene Kopie,
#: Pin-Dateien bleiben bewusst voneinander unabhaengig).
_LOCK_RE = re.compile(r'FROM "(?P<table>core_\w+)"[^;]*FOR UPDATE', re.IGNORECASE | re.DOTALL)


def _locking_statements(captured, table):
    hits = []
    for query in captured.captured_queries:
        match = _LOCK_RE.search(query["sql"])
        if match and match.group("table") == table:
            hits.append(query["sql"])
    return hits


def _pk_literal(obj_or_pk):
    pk = getattr(obj_or_pk, "pk", obj_or_pk)
    return str(pk).replace("-", "")


# ---------------------------------------------------------------------------
# approve_case_deletion
# ---------------------------------------------------------------------------


class TestApproveCaseDeletionGuardMessages:
    """Vier-Augen-Meldungen: die Bedingung bleibt, nur der Text wird ``None``."""

    def test_reviewer_gleich_antragsteller_meldet_den_richtigen_text(self, facility, client_qualified, staff_user):
        """Pins ``x_approve_case_deletion__mutmut_2``/``_3``: ``ValidationError(None)``
        bzw. ``ValidationError(_(None))`` statt des deutschen Vier-Augen-Texts."""
        case = _case(facility, client_qualified, staff_user, title="Antragsteller-Fehler")
        dr = _dr(facility, case, staff_user, reason="Selbstgenehmigung")

        with pytest.raises(ValidationError) as exc:
            approve_case_deletion(dr, staff_user)

        assert exc.value.messages == ["Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip)."]

    def test_reviewer_ohne_recht_meldet_den_richtigen_text(
        self, facility, client_qualified, staff_user, assistant_user
    ):
        """Pins ``x_approve_case_deletion__mutmut_8``/``_9``: dieselbe ``None``-
        Abweichung fuer die ``can_confirm_deletion``-Meldung."""
        case = _case(facility, client_qualified, staff_user, title="Kein Genehmiger-Recht")
        dr = _dr(facility, case, staff_user, reason="Ohne Recht")
        assert assistant_user.can_confirm_deletion is False

        with pytest.raises(ValidationError) as exc:
            approve_case_deletion(dr, assistant_user)

        assert exc.value.messages == ["Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip)."]


class TestApproveCaseDeletionLookupZieltreue:
    """Muster ``test_mutation_pins_097_events_deletion.py``: ``pk``/``facility``
    in den beiden ``get()``-Lookups sind die einzige Mandantengrenze."""

    def test_approve_laedt_genau_den_uebergebenen_antrag_nach(self, facility, client_qualified, staff_user, lead_user):
        """Pins ``x_approve_case_deletion__mutmut_16``: ohne ``pk`` im Sperr-
        Lookup entscheidet allein die Einrichtung, welcher Antrag genehmigt
        wird — bei zwei offenen Antraegen derselben Einrichtung fliegt
        ``MultipleObjectsReturned`` statt der gezielten Genehmigung."""
        case_a = _case(facility, client_qualified, staff_user, title="Bleibt offen")
        case_b = _case(facility, client_qualified, staff_user, title="Wird genehmigt")
        dr_a = _dr(facility, case_a, staff_user, reason="Bleibt offen")
        dr_b = _dr(facility, case_b, staff_user, reason="Wird genehmigt")
        assert dr_a.pk != dr_b.pk

        approve_case_deletion(dr_b, lead_user)

        dr_b.refresh_from_db()
        assert dr_b.status == DeletionRequest.Status.APPROVED
        case_b.refresh_from_db()
        assert case_b.is_deleted is True

        dr_a.refresh_from_db()
        assert dr_a.status == DeletionRequest.Status.PENDING
        case_a.refresh_from_db()
        assert case_a.is_deleted is False

    def test_approve_lookup_bleibt_an_die_antrags_einrichtung_gebunden(
        self, facility, other_facility, client_qualified, staff_user, lead_user
    ):
        """Pins ``x_approve_case_deletion__mutmut_17``: faellt die ``facility``-
        Scopung des Sperr-Lookups weg, genuegt die PK — der Antrag wird ohne
        Mandantengrenze nachgeladen. Das Original findet unter der fremden
        Einrichtung nichts und bricht mit ``DoesNotExist`` ab.

        Refs #2373: Die Einrichtung der handelnden Person wird seitdem schon VOR
        der Sperre gegen die Instanz geprueft. Damit der Lookup selbst erreicht
        wird, liegt die DB-Zeile in der fremden Einrichtung und die Instanz im
        Speicher zeigt auf die Einrichtung der handelnden Person — der Mutant
        laedt dann die fremde Zeile und scheitert erst an der Pruefung unter
        der Sperre (``ValidationError`` statt ``DoesNotExist``)."""
        case = _case(facility, client_qualified, staff_user, title="Mandantengrenze")
        dr = _dr(other_facility, case, staff_user, reason="Art.-17-Antrag")
        # Nur die IN-MEMORY-Instanz zeigt auf die Einrichtung der handelnden
        # Person; die DB-Zeile bleibt in der fremden. Genau diese Instanz
        # liefert dem Lookup seinen ``facility``-Filter.
        dr.facility = facility

        with pytest.raises(DeletionRequest.DoesNotExist):
            approve_case_deletion(dr, lead_user)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        case.refresh_from_db()
        assert case.is_deleted is False

    def test_approve_loescht_genau_den_beantragten_fall(self, facility, client_qualified, staff_user, lead_user):
        """Pins ``x_approve_case_deletion__mutmut_27``: ohne ``pk=target_id``
        beim Fall-Lookup entscheidet nur noch die Einrichtung — bei zwei
        Faellen derselben Einrichtung fliegt ``MultipleObjectsReturned``."""
        case_a = _case(facility, client_qualified, staff_user, title="Bleibt stehen")
        case_b = _case(facility, client_qualified, staff_user, title="Wird geloescht")
        dr = _dr(facility, case_b, staff_user, reason="Art.-17-Antrag")

        approve_case_deletion(dr, lead_user)

        assert Case.objects.get(pk=case_b.pk).is_deleted is True
        assert Case.objects.get(pk=case_a.pk).is_deleted is False

    def test_approve_loest_das_ziel_nur_innerhalb_der_antrags_einrichtung_auf(
        self, facility, foreign_case, staff_user, lead_user
    ):
        """Pins ``x_approve_case_deletion__mutmut_28``: ohne ``facility`` im
        Fall-Lookup kann ein Antrag der Einrichtung A einen Fall der
        Einrichtung B soft-loeschen."""
        assert foreign_case.facility_id != facility.pk
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CASE,
            target_id=foreign_case.pk,
            reason="Einrichtungsfremdes Ziel",
            requested_by=staff_user,
        )

        with pytest.raises(Case.DoesNotExist):
            approve_case_deletion(dr, lead_user)

        foreign_case.refresh_from_db()
        assert foreign_case.is_deleted is False
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING


class TestApproveCaseDeletionAuditTrail:
    def test_reviewed_at_wird_gesetzt(self, facility, client_qualified, staff_user, lead_user):
        """Pins ``x_approve_case_deletion__mutmut_38``: ``reviewed_at`` bleibt
        ``None`` statt den Genehmigungszeitpunkt zu tragen."""
        case = _case(facility, client_qualified, staff_user, title="Zeitstempel")
        dr = _dr(facility, case, staff_user, reason="Zeitstempel")
        before = timezone.now()

        approve_case_deletion(dr, lead_user)

        dr.refresh_from_db()
        assert dr.reviewed_at is not None
        assert dr.reviewed_at >= before
        assert dr.reviewed_by_id == lead_user.pk

    def test_audit_zeile_traegt_den_reviewer(self, facility, client_qualified, staff_user, lead_user):
        """Pins ``x_approve_case_deletion__mutmut_40``: ``audit_event`` bekommt
        ``user=None`` statt des Reviewers — die ``DELETION_APPROVED``-Zeile
        verliert ihre Zuschreibung (Art. 5 Abs. 2)."""
        case = _case(facility, client_qualified, staff_user, title="Zuschreibung")
        dr = _dr(facility, case, staff_user, reason="Zuschreibung")

        approve_case_deletion(dr, lead_user)

        entry = AuditLog.objects.get(action=AuditLog.Action.DELETION_APPROVED, target_id=str(dr.pk))
        assert entry.user_id == lead_user.pk

    def test_audit_zeile_traegt_den_exakten_zieltyp(self, facility, client_qualified, staff_user, lead_user):
        """Pins ``x_approve_case_deletion__mutmut_42`` (``None``), ``_48``
        (Kwarg weggelassen, faellt ebenfalls auf ``None``), ``_51``
        (``"XXDeletionRequestXX"``), ``_52``/``_53`` (klein-/grossgeschrieben):
        ``target_type`` ist Audit-Detail, kein reiner
        Anzeigetext — exakt gepinnt statt als i18n-``skip`` behandelt."""
        case = _case(facility, client_qualified, staff_user, title="Zieltyp")
        dr = _dr(facility, case, staff_user, reason="Zieltyp")

        approve_case_deletion(dr, lead_user)

        entry = AuditLog.objects.get(action=AuditLog.Action.DELETION_APPROVED, target_id=str(dr.pk))
        assert entry.target_type == "DeletionRequest"


# ---------------------------------------------------------------------------
# reject_case_deletion
# ---------------------------------------------------------------------------


class TestRejectCaseDeletionGuardMessage:
    def test_reviewer_gleich_antragsteller_meldet_den_richtigen_text(self, facility, client_qualified, staff_user):
        """Pins ``x_reject_case_deletion__mutmut_2``/``_3``: ``ValidationError(None)``
        bzw. ``ValidationError(_(None))`` statt des deutschen Vier-Augen-Texts."""
        case = _case(facility, client_qualified, staff_user, title="Antragsteller-Fehler")
        dr = _dr(facility, case, staff_user, reason="Selbstablehnung")

        with pytest.raises(ValidationError) as exc:
            reject_case_deletion(dr, staff_user)

        assert exc.value.messages == ["Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip)."]

    def test_reviewer_ohne_recht_meldet_den_richtigen_text(
        self, facility, client_qualified, staff_user, assistant_user
    ):
        """Pins ``x_reject_case_deletion__mutmut_8``/``_9`` und tötet
        ``_10``/``_11``/``_12``: Verlust und Verfälschungen der Meldung des
        Bestätigungsrecht-Guards."""
        case = _case(facility, client_qualified, staff_user, title="Kein Ablehnungsrecht")
        dr = _dr(facility, case, staff_user, reason="Ohne Recht")

        with pytest.raises(ValidationError) as exc:
            reject_case_deletion(dr, assistant_user)

        assert exc.value.messages == ["Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip)."]


class TestRejectCaseDeletionLookupZieltreue:
    def test_reject_stempelt_genau_den_uebergebenen_antrag(self, facility, client_qualified, staff_user, lead_user):
        """Pins ``x_reject_case_deletion__mutmut_16``: ohne ``pk`` im Sperr-
        Lookup wird irgendein Antrag der Einrichtung abgelehnt — bei zwei
        offenen Antraegen fliegt ``MultipleObjectsReturned``."""
        case_a = _case(facility, client_qualified, staff_user, title="Bleibt offen")
        case_b = _case(facility, client_qualified, staff_user, title="Wird abgelehnt")
        dr_a = _dr(facility, case_a, staff_user, reason="Bleibt offen")
        dr_b = _dr(facility, case_b, staff_user, reason="Wird abgelehnt")
        assert dr_a.pk != dr_b.pk

        reject_case_deletion(dr_b, lead_user)

        dr_b.refresh_from_db()
        assert dr_b.status == DeletionRequest.Status.REJECTED
        dr_a.refresh_from_db()
        assert dr_a.status == DeletionRequest.Status.PENDING

    def test_reject_lookup_bleibt_an_die_antrags_einrichtung_gebunden(
        self, facility, other_facility, client_qualified, staff_user, lead_user
    ):
        """Pins ``x_reject_case_deletion__mutmut_17``: faellt ``facility`` im
        Sperr-Lookup weg, laesst sich ein Antrag ablehnen, dessen Einrichtung
        nicht die des uebergebenen Objekts ist.

        Refs #2374: Die Einrichtung der handelnden Person wird seitdem schon VOR
        der Sperre gegen die Instanz geprueft (wie bei der Genehmigung). Damit
        der Lookup selbst erreicht wird, liegt die DB-Zeile in der fremden
        Einrichtung und die Instanz im Speicher zeigt auf die Einrichtung der
        handelnden Person — der Mutant laedt dann die fremde Zeile und scheitert
        erst an der Pruefung unter der Sperre (``ValidationError`` statt
        ``DoesNotExist``)."""
        case = _case(facility, client_qualified, staff_user, title="Mandantengrenze")
        dr = _dr(other_facility, case, staff_user, reason="Art.-17-Antrag")
        # Nur die IN-MEMORY-Instanz zeigt auf die Einrichtung der handelnden
        # Person; die DB-Zeile bleibt in der fremden.
        dr.facility = facility

        with pytest.raises(DeletionRequest.DoesNotExist):
            reject_case_deletion(dr, lead_user)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING


class TestRejectCaseDeletionAuditTrail:
    def test_reviewed_at_wird_gesetzt(self, facility, client_qualified, staff_user, lead_user):
        """Pins ``x_reject_case_deletion__mutmut_26``: ``reviewed_at`` bleibt
        ``None`` statt den Ablehnungszeitpunkt zu tragen."""
        case = _case(facility, client_qualified, staff_user, title="Zeitstempel")
        dr = _dr(facility, case, staff_user, reason="Zeitstempel")
        before = timezone.now()

        reject_case_deletion(dr, lead_user)

        dr.refresh_from_db()
        assert dr.reviewed_at is not None
        assert dr.reviewed_at >= before

    def test_audit_zeile_traegt_den_reviewer(self, facility, client_qualified, staff_user, lead_user):
        """Pins ``x_reject_case_deletion__mutmut_28``: ``audit_event`` bekommt
        ``user=None`` statt des Reviewers."""
        case = _case(facility, client_qualified, staff_user, title="Zuschreibung")
        dr = _dr(facility, case, staff_user, reason="Zuschreibung")

        reject_case_deletion(dr, lead_user)

        entry = AuditLog.objects.get(action=AuditLog.Action.DELETION_REJECTED, target_id=str(dr.pk))
        assert entry.user_id == lead_user.pk

    def test_audit_zeile_traegt_den_exakten_zieltyp(self, facility, client_qualified, staff_user, lead_user):
        """Pins ``x_reject_case_deletion__mutmut_30`` (``None``), ``_36``
        (Kwarg weggelassen), ``_39`` (``"XXDeletionRequestXX"``), ``_40``/
        ``_41`` (klein-/grossgeschrieben)."""
        case = _case(facility, client_qualified, staff_user, title="Zieltyp")
        dr = _dr(facility, case, staff_user, reason="Zieltyp")

        reject_case_deletion(dr, lead_user)

        entry = AuditLog.objects.get(action=AuditLog.Action.DELETION_REJECTED, target_id=str(dr.pk))
        assert entry.target_type == "DeletionRequest"


# ---------------------------------------------------------------------------
# soft_delete_case
# ---------------------------------------------------------------------------


class TestSoftDeleteCaseGuardsProcessingRestriction:
    def test_sperrt_die_personenzeile_und_prueft_die_richtige_person(self, facility, client_identified, staff_user):
        """Pins ``x_soft_delete_case__mutmut_1`` (Client-Argument -> ``None``,
        laut Docstring von ``assert_processing_allowed`` damit „nie
        eingeschraenkt" — ein glatter Bypass), ``_2`` (``lock=None``), ``_4``
        (Kwarg weggelassen, Default ``False``) und ``_5`` (``lock=False``):
        das Original setzt GENAU EINE ``FOR UPDATE``-Sperre auf die Zeile
        DIESER Person; jede der vier Abweichungen liefert entweder keine
        Sperr-Anweisung oder keine Sperr-Anweisung auf der richtigen Person."""
        case = _case(facility, client_identified, staff_user, title="Direktloeschung")

        with CaptureQueriesContext(connection) as captured:
            soft_delete_case(case, staff_user)

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]
        case.refresh_from_db()
        assert case.is_deleted is True


class TestSoftDeleteCaseGuardMessage:
    def test_qualifizierte_person_meldet_den_richtigen_text(self, facility, client_qualified, staff_user):
        """Pins ``x_soft_delete_case__mutmut_7``/``_8``: ``ValidationError(None)``
        bzw. ``ValidationError(_(None))`` statt des deutschen Vier-Augen-Hinweises."""
        case = _case(facility, client_qualified, staff_user, title="Qualifiziert")

        with pytest.raises(ValidationError) as exc:
            soft_delete_case(case, staff_user)

        assert exc.value.messages == [
            "Für Fälle einer Person mit Zusatzangaben ist ein Löschantrag mit Bestätigung nötig."
        ]


# ---------------------------------------------------------------------------
# restore_case
# ---------------------------------------------------------------------------


class TestRestoreCaseGuardMessage:
    def test_nicht_im_papierkorb_meldet_den_richtigen_text(self, case_open, staff_user):
        """Pins ``x_restore_case__mutmut_2``/``_3``: ``ValidationError(None)``
        bzw. ``ValidationError(_(None))`` statt des deutschen Papierkorb-Hinweises."""
        with pytest.raises(ValidationError) as exc:
            restore_case(case_open, staff_user)

        assert exc.value.messages == ["Der Fall liegt nicht im Papierkorb."]


class TestRestoreCaseAuditTrail:
    def test_audit_zeile_traegt_akteur_und_einrichtung(self, case_trashed, staff_user):
        """Pins ``x_restore_case__mutmut_14`` (``user=None`` — die Zeile
        bleibt unter der Einrichtung sichtbar, verliert nur den Akteur, Prio
        2) und ``_15`` (``facility=None`` — die Zeile faellt aus jeder
        facility-gescopten Audit-Ansicht heraus, Prio 1)."""
        restore_case(case_trashed, staff_user)

        entry = AuditLog.objects.get(action=AuditLog.Action.CASE_RESTORED, target_id=str(case_trashed.pk))
        assert entry.user_id == staff_user.pk
        assert entry.facility_id == case_trashed.facility_id


# ---------------------------------------------------------------------------
# anonymize_eligible_soft_deleted_cases
# ---------------------------------------------------------------------------


class TestAnonymizeEligibleSoftDeletedCasesDryRunFilter:
    def test_dry_run_zaehlt_nur_faelle_die_wirklich_im_papierkorb_liegen(
        self, facility, client_identified, staff_user, settings_obj
    ):
        """Pins ``x_anonymize_eligible_soft_deleted_cases__mutmut_13``: ohne
        ``is_deleted=True`` im Ausgangsfilter zaehlt der ``dry_run``-Vorschau-
        Pfad auch einen Fall mit, der NICHT im Papierkorb liegt, aber (z.B.
        durch eine direkte DB-Korrektur) noch ein altes ``deleted_at`` traegt.
        Im ECHTEN Lauf faengt der per-Fall-Recheck (``not locked.is_deleted``,
        Refs #2123) das ab — der ``dry_run``-Pfad kehrt aber VOR der Schleife
        zurueck und sieht diesen Recheck nie; dort ist die Mutante eine echte
        Testluecke, nicht equivalent."""
        case = _case(
            facility,
            client_identified,
            staff_user,
            title="Aktiv, altes deleted_at",
            is_deleted=False,
        )
        old = timezone.now() - timedelta(days=settings_obj.client_trash_days + 5)
        Case.objects.filter(pk=case.pk).update(deleted_at=old)

        assert anonymize_eligible_soft_deleted_cases(facility, settings_obj, dry_run=True) == 0

    def test_echter_lauf_laesst_denselben_fall_unberuehrt(self, facility, client_identified, staff_user, settings_obj):
        """Regressions-Gegenstueck zum vorigen Pin: belegt den Recheck selbst
        (kein Mutant dieses Batches, aber Voraussetzung fuer die
        ``equivalent``-Einstufung des echten Laufs)."""
        case = _case(
            facility,
            client_identified,
            staff_user,
            title="Aktiv, altes deleted_at",
            is_deleted=False,
        )
        old = timezone.now() - timedelta(days=settings_obj.client_trash_days + 5)
        Case.objects.filter(pk=case.pk).update(deleted_at=old)

        count = anonymize_eligible_soft_deleted_cases(facility, settings_obj)

        assert count == 0
        case.refresh_from_db()
        assert case.title == "Aktiv, altes deleted_at"
        assert case.anonymized_at is None


class TestAnonymizeEligibleSoftDeletedCasesDeferredSignal:
    def test_keine_zurueckstellungs_meldung_ohne_gehaltene_faelle(self, facility, settings_obj, caplog):
        """Pins ``x_anonymize_eligible_soft_deleted_cases__mutmut_22``: der
        Startwert ``0`` wird zu ``1`` — ohne einen einzigen faelligen Fall
        (die Schleife laeuft null Mal) loggt der Lauf trotzdem eine
        Rueckstellungsmeldung, weil ``if deferred:`` faelschlich wahr wird."""
        with caplog.at_level(logging.INFO, logger="core.services.case.deletion"):
            count = anonymize_eligible_soft_deleted_cases(facility, settings_obj)

        assert count == 0
        assert not any("zurueckgestellt" in r.getMessage() for r in caplog.records)

    def test_zurueckstellungs_zaehler_und_facility_stimmen_exakt(
        self, facility, client_identified, staff_user, settings_obj, caplog
    ):
        """Pins ``x_anonymize_eligible_soft_deleted_cases__mutmut_43``
        (Zuweisung ``deferred = 1`` statt Akkumulation), ``_44``
        (``deferred -= 1``), ``_45`` (``deferred += 2``), ``_58``
        (``deferred`` -> ``None`` im Log-Argument) und ``_59``
        (``facility.pk`` -> ``None`` im Log-Argument): zwei Faelle muessen die
        Meldung mit dem exakten Zaehler ``2`` und der exakten ``facility``-PK
        tragen.

        ``case_has_active_hold`` wird gemockt statt ueber einen echten
        ``LegalHold`` erzwungen: ein VOR dem Lauf angelegter Hold wuerde die
        Faelle schon aus dem Ausgangsfilter (``exclude_cases_with_active_holds``,
        Queryset-Bau) herausnehmen — die Schleife saehe sie dann nie, und
        ``deferred`` bliebe 0. Der Mock modelliert stattdessen genau den vom
        Docstring beschriebenen Wettlauf (Refs #2123): ein Hold, der ERST NACH
        der qs-Materialisierung ankommt und nur noch der per-Fall-Recheck AUF
        DER GESPERRTEN Zeile sieht."""
        old = timezone.now() - timedelta(days=settings_obj.client_trash_days + 5)
        case_a = _case(facility, client_identified, staff_user, title="Hold A", is_deleted=True, deleted_at=old)
        case_b = _case(facility, client_identified, staff_user, title="Hold B", is_deleted=True, deleted_at=old)

        with mock.patch("core.services.case.deletion.case_has_active_hold", return_value=True) as patched_hold:
            with caplog.at_level(logging.INFO, logger="core.services.case.deletion"):
                count = anonymize_eligible_soft_deleted_cases(facility, settings_obj)

        assert patched_hold.call_count == 2
        assert count == 0
        messages = [r.getMessage() for r in caplog.records if "zurueckgestellt" in r.getMessage()]
        assert len(messages) == 1, messages
        assert messages[0] == (
            f"Fall-Tilgung: 2 Fall/Faelle wegen aktiver Aufbewahrungssperre zurueckgestellt (facility={facility.pk})"
        )
        case_a.refresh_from_db()
        case_b.refresh_from_db()
        assert case_a.anonymized_at is None
        assert case_b.anonymized_at is None
