"""Pins fuer die Sperrfolge der Ereignis-Bearbeitung (``core.services.events.crud``).

Quelle: Mutations-Delta des Checkpoints release-0.99.4, 9 Mutanten.
Gruppen:

* ``lock_event_for_edit`` — Vorlesen der Personenbindung (verliert ``pk``
  oder die Einrichtung), Ereignissperre (verliert ``pk`` oder die Person),
  Abgleich der Instanz an die gesperrte Person (entfaellt oder nullt) und
  der Meldungstext beider Absagen.
* ``_event_form_state_unchanged`` — ein leerer oder nicht-textueller
  Fingerabdruck steigt ohne Abfrage aus.

Eine entfallene Bedingung faellt nur auf, wenn eine zweite Zeile passt. Die
Pins legen deshalb ein zweites Ereignis an, das in der Standardsortierung
(``-occurred_at``) vor dem bearbeiteten steht — derselben oder einer anderen
Person —, und halten zusaetzlich fest, welche Zeilen die Abfragen lesen bzw.
sperren (``tests._row_lock_helpers``: PostgreSQL plant die mitgeschnittene
Anweisung; massgeblich sind Tabelle, Sperrart ``FOR UPDATE`` und die
Filterwerte je Spalte, nicht Alias, Quoting, Projektion, Reihenfolge oder
Literalform). Welche Person das Art.-18-Gate prueft, zeigt die beobachtete
Sperre der Personenzeile. Wettlaeufe werden deterministisch direkt nach dieser
Sperre nachgestellt — zwischen dem ungesperrten Vorlesen und der
Ereignissperre, unabhaengig davon, ueber welchen Helfer die Produktion sperrt.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Client, Event
from core.services.case.state_contract import EVENT_WRITE_GONE_MESSAGE
from core.services.events.crud import (
    EVENT_EDIT_DELETED_MESSAGE,
    _event_form_state_unchanged,
    lock_event_for_edit,
    update_event,
)
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db


def _ereignis(facility, client, doc_type, user, *, vor_tagen):
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=timezone.now() - timedelta(days=vor_tagen),
        data_json={},
        created_by=user,
    )


def _in_den_papierkorb(event):
    Event._base_manager.filter(pk=event.pk).update(is_deleted=True)


@pytest.fixture
def andere_person(facility, staff_user):
    return Client.objects.create(facility=facility, pseudonym="Andere-Person", created_by=staff_user)


class TestLockEventForEditVorlesen:
    """``lock_event_for_edit``: das ungesperrte Vorlesen der Personenbindung."""

    def test_neueres_ereignis_einer_anderen_person_bestimmt_die_sperre_nicht(
        self, facility, client_identified, andere_person, doc_type_contact, staff_user
    ):
        """x_lock_event_for_edit__mutmut_5: ohne ``pk`` las das Vorlesen die
        Person des neuesten Ereignisses der Einrichtung, das Gate sperrte die
        falsche Person und die Ereignissperre meldete GONE. Beobachtet: die
        Sperre gelingt, gesperrt wird nur die Personenzeile des bearbeiteten
        Ereignisses."""
        eigenes = _ereignis(facility, client_identified, doc_type_contact, staff_user, vor_tagen=3)
        _ereignis(facility, andere_person, doc_type_contact, staff_user, vor_tagen=0)
        with mitschnitt() as abfragen:
            lock_event_for_edit(eigenes)
        personensperren = abfragen.sperren("core_client")
        assert personensperren
        assert all(a.traegt(id=[client_identified.pk]) for a in personensperren)

    def test_fremde_einrichtung_im_speicher_bricht_vor_der_personensperre_ab(self, sample_event, other_facility):
        """x_lock_event_for_edit__mutmut_6 (ohne Einrichtung las und sperrte
        das Vorlesen die Person der Zeile, bevor die Sperre abwies), _10
        (Meldung ``None``). Beobachtet: Absage mit der Schreibform, die
        Personenzeile bleibt ungesperrt."""
        sample_event.facility_id = other_facility.pk
        with mitschnitt() as abfragen, pytest.raises(ValidationError) as exc:
            lock_event_for_edit(sample_event)
        assert exc.value.messages == [str(EVENT_WRITE_GONE_MESSAGE)]
        assert abfragen.sperren("core_client") == []

    def test_vorlesen_traegt_pk_und_einrichtung(self, sample_event):
        """x_lock_event_for_edit__mutmut_5 (``pk`` faellt weg), _6
        (``facility_id`` faellt weg)."""
        with mitschnitt() as abfragen:
            lock_event_for_edit(sample_event)
        assert any(
            a.traegt(id=[sample_event.pk], facility_id=[sample_event.facility_id])
            for a in abfragen.lesungen("core_event")
        )


class TestLockEventForEditSperre:
    """``lock_event_for_edit``: die Ereignissperre mit Person in der Bedingung."""

    def test_papierkorb_eines_neueren_ereignisses_derselben_person_sperrt_nicht(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """x_lock_event_for_edit__mutmut_31: ohne ``pk`` pruefte die Sperre
        das neueste Ereignis der Person und wies wegen dessen Papierkorb ab."""
        eigenes = _ereignis(facility, client_identified, doc_type_contact, staff_user, vor_tagen=3)
        _in_den_papierkorb(_ereignis(facility, client_identified, doc_type_contact, staff_user, vor_tagen=0))
        lock_event_for_edit(eigenes)

    def test_eigener_papierkorb_neben_lebendem_neuerem_ereignis_wird_abgewiesen(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """x_lock_event_for_edit__mutmut_31: ohne ``pk`` pruefte die Sperre
        das neuere, lebende Ereignis — das geloeschte waere bearbeitbar."""
        eigenes = _ereignis(facility, client_identified, doc_type_contact, staff_user, vor_tagen=3)
        _ereignis(facility, client_identified, doc_type_contact, staff_user, vor_tagen=0)
        _in_den_papierkorb(eigenes)
        with pytest.raises(ValidationError) as exc:
            lock_event_for_edit(eigenes)
        assert exc.value.messages == [str(EVENT_EDIT_DELETED_MESSAGE)]

    def test_ereignis_wechselt_nach_der_personensperre_die_person(self, sample_event, andere_person):
        """x_lock_event_for_edit__mutmut_33 (ohne Person in der Bedingung
        wird das umgehaengte Ereignis trotzdem gesperrt), _38 (Meldung
        ``None``): ein paralleles Zusammenfuehren haengt das Ereignis nach der
        Personensperre an eine andere, ungesperrte Person."""
        with mitschnitt() as abfragen, pytest.raises(ValidationError) as exc:
            abfragen.nach_sperre(
                "core_client",
                lambda: Event._base_manager.filter(pk=sample_event.pk).update(client=andere_person),
            )
            lock_event_for_edit(sample_event)
        assert exc.value.messages == [str(EVENT_WRITE_GONE_MESSAGE)]
        assert abfragen.offene_schranken() == []

    def test_sperre_traegt_pk_einrichtung_und_person(self, sample_event):
        """x_lock_event_for_edit__mutmut_31 (``pk`` faellt weg), _33
        (``client_id`` faellt weg)."""
        with mitschnitt() as abfragen:
            lock_event_for_edit(sample_event)
        sperren = abfragen.sperren("core_event")
        assert sperren
        assert all(a.sperrart == "UPDATE" for a in sperren)
        assert all(
            a.traegt(
                id=[sample_event.pk],
                facility_id=[sample_event.facility_id],
                client_id=[sample_event.client_id],
            )
            for a in sperren
        )


class TestLockEventForEditAbgleich:
    """``lock_event_for_edit``: die Instanz uebernimmt die gesperrte Person."""

    def test_veraltete_instanz_traegt_danach_die_gesperrte_person(self, sample_event, andere_person):
        """x_lock_event_for_edit__mutmut_40 (Abgleich entfaellt, die Instanz
        behaelt die alte Person), _41 (die Instanz verliert die Person): die
        Anhangs-Vorsperre leitet ihr Ziel aus ``event.client_id`` ab."""
        Event._base_manager.filter(pk=sample_event.pk).update(client=andere_person)
        lock_event_for_edit(sample_event)
        assert sample_event.client_id == andere_person.pk


class TestEventFormStateUnchanged:
    """``_event_form_state_unchanged``: early exit without a query, at the helper and through ``update_event``."""

    @pytest.mark.parametrize("form_state", ["", 123, ["x"]], ids=["empty", "number", "list"])
    def test_empty_or_non_text_fingerprint_exits_without_a_query(
        self, sample_event, form_state, django_assert_num_queries
    ):
        """x__event_form_state_unchanged__mutmut_1: ``and`` instead of ``or``
        keeps the result ``False`` but drops the early exit, so the locked
        fingerprint read runs. The promise "no query at all" is only
        observable at the helper itself: the comparison over ``update_event``
        below runs both paths through the same helper, and an emptiness or
        type check moved behind the read would add the same query to both.
        Hence the private binding here, as the exception next to that
        comparison."""
        with django_assert_num_queries(0):
            assert _event_form_state_unchanged(sample_event, form_state) is False

    @pytest.mark.parametrize("fingerabdruck", ["", 123, ["x"]], ids=["leer", "zahl", "liste"])
    def test_leerer_oder_nicht_textueller_fingerabdruck_fragt_nicht_ab(
        self, sample_event, staff_user, fingerabdruck, django_assert_num_queries
    ):
        """x__event_form_state_unchanged__mutmut_1: ``and`` instead of ``or``.
        The version conflict stands either way, but without the early exit
        the locked fingerprint read would run. Observed through
        ``update_event``: the conflict path with such a fingerprint issues
        exactly as many queries as the same path without a fingerprint, which
        exits early under both conditions."""
        rendered_at = sample_event.updated_at.isoformat()
        Event._base_manager.filter(pk=sample_event.pk).update(updated_at=timezone.now() + timedelta(minutes=1))
        with CaptureQueriesContext(connection) as without_fingerprint, pytest.raises(ValidationError) as baseline:
            update_event(sample_event, staff_user, {}, expected_updated_at=rendered_at)
        assert baseline.value.code == "version_conflict"
        with django_assert_num_queries(len(without_fingerprint)), pytest.raises(ValidationError) as exc:
            update_event(
                sample_event, staff_user, {}, expected_updated_at=rendered_at, expected_form_state=fingerabdruck
            )
        assert exc.value.code == "version_conflict"
