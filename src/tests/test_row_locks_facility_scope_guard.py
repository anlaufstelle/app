"""Zeilensperren in Aufgaben-, Vorschlags- und Durchsetzungsdiensten sind an die Einrichtung gebunden (Refs #2377).

Fünf Sperr-Lookups luden eine Zeile nur über den Primärschlüssel:

* ``core.services.case.workitems._locked_client_id`` und ``_form_state_unchanged``
  (Aufgabenzeile, Einrichtung der Aufgabe),
* ``core.retention.proposals._lock_target_rows_for_approval`` (Personenzeile des
  Ziel-Ereignisses, Einrichtung des Vorschlags),
* ``core.retention.enforcement._lock_event_and_recheck_hold`` (Personen- und
  Ereigniszeile, Einrichtung des Durchsetzungslaufs).

Über HTTP tragen View-Scoping und Datenbank-Zeilensicherheit; im Test ist die
Zeilensicherheit nicht aktiv, der Dienst ist also die einzige Grenze. Geprüft
wird zweierlei: eine Zeile einer fremden Einrichtung wird weder geliefert noch
weiterverarbeitet, und jede ``FOR UPDATE``-Abfrage der Funktion trägt den
Einrichtungsfilter — eine fremde Zeile wird also auch nicht gesperrt.

Eine Ereigniszeile kann nicht auf eine Person einer anderen Einrichtung zeigen
(zusammengesetzter Fremdschlüssel ``core_event_client_facility_fk``). Für die
Personensperren am Ziel-Ereignis bleibt deshalb nur der strukturelle Nachweis
über die Abfragen; eine zwischenzeitlich fehlende Personenzeile fangen die
bestehenden Bindungs-Nachprüfungen der Funktionen ab.
"""

from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Client, Event, RetentionProposal, WorkItem
from core.retention.enforcement import _lock_event_and_recheck_hold
from core.retention.proposals import _lock_target_rows_for_approval, approve_proposal
from core.services.case.workitems import (
    _form_state_unchanged,
    _locked_client_id,
    workitem_form_state_token,
)

pytestmark = pytest.mark.django_db


def _sperr_abfragen(queries):
    return [q["sql"] for q in queries if "FOR UPDATE" in q["sql"]]


def _assert_jede_sperre_gescopet(queries, erwartete_anzahl):
    sperren = _sperr_abfragen(queries)
    assert len(sperren) == erwartete_anzahl, sperren
    for sql in sperren:
        assert '"facility_id"' in sql, sql


@pytest.fixture
def fremde_person(second_facility, staff_user):
    return Client.objects.create(
        facility=second_facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Fremd-Sperre-01",
        created_by=staff_user,
    )


@pytest.fixture
def ereignis(facility, client_identified, doc_type_contact, staff_user):
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=400),
        data_json={"dauer": 5},
        created_by=staff_user,
    )


def _vorschlag(facility, event):
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=event.pk,
        retention_category="identified",
        deletion_due_at=timezone.localdate() - timedelta(days=1),
        status=RetentionProposal.Status.PENDING,
    )


def _aufgabe(facility, client, user):
    return WorkItem.objects.create(
        facility=facility,
        client=client,
        created_by=user,
        item_type=WorkItem.ItemType.TASK,
        status=WorkItem.Status.OPEN,
        priority=WorkItem.Priority.NORMAL,
        title="Rückruf",
    )


# ---------------------------------------------------------------------------
# core.retention.proposals._lock_target_rows_for_approval
# ---------------------------------------------------------------------------


class TestVorschlagZielzeilen:
    def test_genehmigung_bleibt_moeglich(self, facility, lead_user, ereignis):
        vorschlag = _vorschlag(facility, ereignis)
        approve_proposal(vorschlag, lead_user)
        vorschlag.refresh_from_db()
        assert vorschlag.status == RetentionProposal.Status.APPROVED

    def test_jede_sperre_traegt_den_einrichtungsfilter(self, facility, ereignis, client_identified):
        vorschlag = _vorschlag(facility, ereignis)
        with transaction.atomic(), CaptureQueriesContext(connection) as ctx:
            assert _lock_target_rows_for_approval(vorschlag) == client_identified.pk
        _assert_jede_sperre_gescopet(ctx.captured_queries, 2)


# ---------------------------------------------------------------------------
# core.retention.enforcement._lock_event_and_recheck_hold
# ---------------------------------------------------------------------------


class TestDurchsetzungSperre:
    def test_ereignis_einer_fremden_einrichtung_wird_nicht_geliefert(self, second_facility, ereignis):
        with transaction.atomic():
            assert _lock_event_and_recheck_hold(second_facility, ereignis.pk) == (None, False)

    def test_fremdes_ereignis_wird_auch_nicht_gesperrt(self, second_facility, ereignis):
        with transaction.atomic(), CaptureQueriesContext(connection) as ctx:
            _lock_event_and_recheck_hold(second_facility, ereignis.pk)
        assert _sperr_abfragen(ctx.captured_queries) == []

    def test_eigenes_ereignis_wird_gesperrt_geliefert(self, facility, ereignis):
        with transaction.atomic(), CaptureQueriesContext(connection) as ctx:
            gesperrt, zurueckgestellt = _lock_event_and_recheck_hold(facility, ereignis.pk)
        assert gesperrt == ereignis
        assert zurueckgestellt is False
        _assert_jede_sperre_gescopet(ctx.captured_queries, 2)


# ---------------------------------------------------------------------------
# core.services.case.workitems._locked_client_id / _form_state_unchanged
# ---------------------------------------------------------------------------


@pytest.fixture
def fremde_aufgabe(second_facility, fremde_person, second_facility_user):
    return _aufgabe(second_facility, fremde_person, second_facility_user)


@pytest.fixture
def eigene_aufgabe(facility, client_identified, staff_user):
    return _aufgabe(facility, client_identified, staff_user)


class TestAufgabenSperre:
    def test_fremde_zeile_liefert_keine_person(self, facility, fremde_aufgabe):
        """Eine Instanz, deren ``facility_id`` im Speicher auf eine andere
        Einrichtung zeigt, öffnet die fremde Zeile nicht."""
        fremde_aufgabe.facility_id = facility.pk
        with transaction.atomic():
            assert _locked_client_id(fremde_aufgabe) is None

    def test_fremde_zeile_gilt_nicht_als_unveraendert(self, facility, fremde_aufgabe):
        token = workitem_form_state_token(fremde_aufgabe)
        fremde_aufgabe.facility_id = facility.pk
        with transaction.atomic():
            assert _form_state_unchanged(fremde_aufgabe, token) is False

    def test_eigene_zeile_unveraendert(self, eigene_aufgabe, client_identified):
        token = workitem_form_state_token(eigene_aufgabe)
        with transaction.atomic(), CaptureQueriesContext(connection) as ctx:
            assert _locked_client_id(eigene_aufgabe) == client_identified.pk
            assert _form_state_unchanged(eigene_aufgabe, token) is True
        _assert_jede_sperre_gescopet(ctx.captured_queries, 2)
