"""Pins fuer die Einrichtungs- und Zeilenbindung der Aufgabenbearbeitung (``core.services.case.workitems``).

Quelle: Mutations-Delta des Checkpoints release-0.99.4, 3 Mutanten.
Gruppen:

* ``update_workitem`` — das ungesperrte Vorlesen des Personenbezugs verliert
  ``pk``; das Art.-18-Gate sperrt dann die Person einer beliebigen Aufgabe.
* ``_locked_client_id`` — die Aufgabensperre verliert ``pk`` und liefert die
  Person der ersten Aufgabe der Einrichtung.
* ``_form_state_unchanged`` — die Fingerabdruck-Lesung verliert ``pk`` und
  vergleicht gegen die Formularfelder einer Nachbaraufgabe.

Alle drei fallen nur auf, wenn eine zweite Aufgabe derselben Einrichtung
existiert. Die Nachbaraufgabe ist deshalb die neuere (Standardsortierung
``-created_at`` stellt sie nach vorn) und gehoert je nach Pin einer anderen
Person oder traegt dieselben Formularwerte. Zusaetzlich halten die Pins fest,
welche Zeilen die Abfragen lesen bzw. sperren (``tests._row_lock_helpers``:
PostgreSQL plant die mitgeschnittene Anweisung; massgeblich sind gesperrte
Tabelle, Sperrart und die Filterwerte je Spalte, nicht Alias, Quoting,
Projektion, Reihenfolge oder Literalform). Welche Person das Art.-18-Gate
prueft, zeigt die beobachtete Sperre der Personenzeile, nicht der Aufrufpfad.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Client, WorkItem
from core.services.case.workitems import (
    _form_state_unchanged,
    _locked_client_id,
    update_workitem,
    workitem_form_state_token,
)
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db


def _aufgabe(facility, client, user, *, alter_tage, titel="Aufgabe"):
    aufgabe = WorkItem.objects.create(
        facility=facility,
        client=client,
        created_by=user,
        item_type=WorkItem.ItemType.TASK,
        status=WorkItem.Status.OPEN,
        title=titel,
    )
    WorkItem.objects.filter(pk=aufgabe.pk).update(created_at=timezone.now() - timedelta(days=alter_tage))
    aufgabe.refresh_from_db()
    return aufgabe


@pytest.fixture
def andere_person(facility, staff_user):
    return Client.objects.create(facility=facility, pseudonym="Andere-Person", created_by=staff_user)


@pytest.fixture
def eigene_und_nachbar(facility, client_identified, andere_person, staff_user):
    """Eigene (aeltere) Aufgabe und eine neuere Nachbaraufgabe einer anderen Person."""
    eigene = _aufgabe(facility, client_identified, staff_user, alter_tage=5, titel="Eigene")
    nachbar = _aufgabe(facility, andere_person, staff_user, alter_tage=0, titel="Nachbar")
    return eigene, nachbar


class TestUpdateWorkitem:
    """``update_workitem``: das Vorlesen des Personenbezugs vor dem Art.-18-Gate."""

    def test_gate_sperrt_nur_die_person_der_adressierten_aufgabe(
        self, eigene_und_nachbar, staff_user, client_identified
    ):
        """x_update_workitem__mutmut_9: ohne ``pk`` liest das Vorlesen die
        Person der neueren Nachbaraufgabe, das Gate sperrt die falsche Person
        und die Aufgabensperre weist mit 'Personenzuordnung ... geaendert' ab.
        Beobachtet: die Aenderung landet, gesperrt wird nur die Personenzeile
        der adressierten Aufgabe."""
        eigene, _ = eigene_und_nachbar
        with mitschnitt() as abfragen:
            update_workitem(eigene, staff_user, title="Neu")
        eigene.refresh_from_db()
        assert eigene.title == "Neu"
        personensperren = abfragen.sperren("core_client")
        assert personensperren
        assert all(a.traegt(id=[client_identified.pk]) for a in personensperren)

    def test_vorlesen_traegt_pk_und_einrichtung(self, sample_workitem, staff_user):
        """x_update_workitem__mutmut_9: das ungesperrte Vorlesen der Aufgabe
        filtert auf ``pk`` und Einrichtung (eine Aufgabe allein — die Abfrage,
        nicht ihr Ergebnis, traegt den Nachweis)."""
        with mitschnitt() as abfragen:
            update_workitem(sample_workitem, staff_user, title="Neu")
        assert any(
            a.traegt(id=[sample_workitem.pk], facility_id=[sample_workitem.facility_id])
            for a in abfragen.lesungen("core_workitem")
        )


class TestLockedClientId:
    """``_locked_client_id``: die Aufgabensperre trifft die adressierte Zeile."""

    def test_liefert_die_person_der_adressierten_aufgabe(self, eigene_und_nachbar, client_identified):
        """x__locked_client_id__mutmut_8: ohne ``pk`` kaeme die Person der
        neueren Nachbaraufgabe zurueck."""
        eigene, _ = eigene_und_nachbar
        assert _locked_client_id(eigene) == client_identified.pk

    def test_aufgabensperre_traegt_pk_und_einrichtung(self, eigene_und_nachbar):
        """x__locked_client_id__mutmut_8: ``FOR UPDATE`` auf genau die
        adressierte Aufgabe in ihrer Einrichtung."""
        eigene, _ = eigene_und_nachbar
        with mitschnitt() as abfragen:
            _locked_client_id(eigene)
        sperren = abfragen.sperren("core_workitem")
        assert sperren
        assert all(a.sperrart == "UPDATE" for a in sperren)
        assert all(a.traegt(id=[eigene.pk], facility_id=[eigene.facility_id]) for a in sperren)


class TestFormStateUnchanged:
    """``_form_state_unchanged``: der Fingerabdruck wird an der eigenen Zeile gemessen."""

    def test_reiner_versionskonflikt_wird_neben_einer_nachbaraufgabe_gerettet(self, eigene_und_nachbar, staff_user):
        """x__form_state_unchanged__mutmut_8: parallel aenderte sich nur
        ``updated_at`` der eigenen Aufgabe; ohne ``pk`` verglich die Rettung
        gegen die Formularwerte der neueren Nachbaraufgabe und wies ab."""
        eigene, _ = eigene_und_nachbar
        stand = eigene.updated_at.isoformat()
        token = workitem_form_state_token(eigene)
        WorkItem.objects.filter(pk=eigene.pk).update(updated_at=timezone.now() + timedelta(minutes=1))
        assert _form_state_unchanged(eigene, token) is True
        update_workitem(eigene, staff_user, expected_updated_at=stand, expected_form_state=token, title="Neu")
        eigene.refresh_from_db()
        assert eigene.title == "Neu"

    def test_parallele_feldaenderung_bleibt_konflikt_neben_gleichlautender_nachbaraufgabe(
        self, facility, client_identified, staff_user
    ):
        """x__form_state_unchanged__mutmut_8: die neuere Nachbaraufgabe traegt
        genau die gerenderten Formularwerte, die eigene wurde parallel
        umbenannt — ohne ``pk`` galt das als unveraendert und die parallele
        Aenderung waere still ueberschrieben worden."""
        eigene = _aufgabe(facility, client_identified, staff_user, alter_tage=5, titel="Gleich")
        _aufgabe(facility, client_identified, staff_user, alter_tage=0, titel="Gleich")
        token = workitem_form_state_token(eigene)
        WorkItem.objects.filter(pk=eigene.pk).update(title="Parallel geaendert")
        assert _form_state_unchanged(eigene, token) is False

    def test_fingerabdruck_lesung_traegt_pk_und_einrichtung(self, eigene_und_nachbar):
        """x__form_state_unchanged__mutmut_8: ``FOR UPDATE`` auf genau die
        adressierte Aufgabe in ihrer Einrichtung."""
        eigene, _ = eigene_und_nachbar
        with mitschnitt() as abfragen:
            _form_state_unchanged(eigene, workitem_form_state_token(eigene))
        sperren = abfragen.sperren("core_workitem")
        assert sperren
        assert all(a.sperrart == "UPDATE" for a in sperren)
        assert all(a.traegt(id=[eigene.pk], facility_id=[eigene.facility_id]) for a in sperren)
