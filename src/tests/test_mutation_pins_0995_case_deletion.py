"""Pins fuer die Einrichtungsgrenze der Fall-Loeschung (``core.services.case.deletion``).

Quelle: Mutations-Delta des Checkpoints release-0.99.4, 3 Mutanten.
Gruppen:

* ``soft_delete_case`` — das Vorlesen des Personenbezugs fuer das
  Art.-18-Gate verliert ``pk`` (das Gate prueft dann die Person eines
  beliebigen Falls der Einrichtung); ein Akteur ohne ``facility_id`` wird
  fail-closed mit der Einrichtungsmeldung abgewiesen, nicht mit
  ``AttributeError``.
* ``_move_case_to_trash`` — fehlt die Fallzeile in der Einrichtung, nennt
  die Absage die Einrichtungsgrenze (kein leerer Meldungstext).

Der Nachbarfall einer freien Person ist der neuere (Standardsortierung
``-created_at``), der zu loeschende gehoert einer Person mit
Verarbeitungseinschraenkung. Zusaetzlich halten die Pins fest, welche Zeilen
gelesen bzw. gesperrt werden (``tests._row_lock_helpers``: PostgreSQL plant die
mitgeschnittene Anweisung; massgeblich sind Tabelle und Filterwerte je Spalte,
nicht Alias, Quoting, Projektion oder Literalform): das Vorlesen an ``pk`` und
Einrichtung, das Art.-18-Gate an der beobachteten Sperre der Personenzeile.
"""

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Case, Client
from core.services.case.deletion import CASE_OTHER_FACILITY_MESSAGE, soft_delete_case
from core.services.client.restriction import PROCESSING_RESTRICTED_MESSAGE
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db


def _fall(facility, client, user, *, alter_tage):
    fall = Case.objects.create(facility=facility, client=client, title="Fall", status=Case.Status.OPEN, created_by=user)
    Case.objects.filter(pk=fall.pk).update(created_at=timezone.now() - timedelta(days=alter_tage))
    fall.refresh_from_db()
    return fall


@pytest.fixture
def eingeschraenkte_person(facility, staff_user):
    person = Client.objects.create(facility=facility, pseudonym="Eingeschraenkt", created_by=staff_user)
    Client.objects.filter(pk=person.pk).update(
        processing_restricted=True,
        processing_restriction_reason=Client.RestrictionReason.ACCURACY_DISPUTED,
        processing_restricted_at=timezone.now(),
    )
    return person


@pytest.fixture
def freie_person(facility, staff_user):
    return Client.objects.create(facility=facility, pseudonym="Frei", created_by=staff_user)


class TestSoftDeleteCase:
    """``soft_delete_case``: Einrichtungsgrenze und Personenbezug des Art.-18-Gates."""

    def test_fall_einer_eingeschraenkten_person_neben_neuerem_freien_fall(
        self, facility, eingeschraenkte_person, freie_person, staff_user
    ):
        """x_soft_delete_case__mutmut_17: ohne ``pk`` las das Vorlesen die
        Person des neueren Falls — das Gate pruefte die freie Person und der
        Fall der eingeschraenkten Person landete im Papierkorb. Beobachtet:
        Absage mit der Art.-18-Meldung, der Fall bleibt, gesperrt wird nur die
        Personenzeile des adressierten Falls."""
        ziel = _fall(facility, eingeschraenkte_person, staff_user, alter_tage=5)
        _fall(facility, freie_person, staff_user, alter_tage=0)
        with mitschnitt() as abfragen, pytest.raises(ValidationError) as exc:
            soft_delete_case(ziel, staff_user)
        assert exc.value.messages == [str(PROCESSING_RESTRICTED_MESSAGE)]
        assert Case.objects.filter(pk=ziel.pk, is_deleted=False).exists()
        personensperren = abfragen.sperren("core_client")
        assert personensperren
        assert all(a.traegt(id=[eingeschraenkte_person.pk]) for a in personensperren)

    def test_vorlesen_traegt_pk_und_einrichtung(self, case_open, staff_user):
        """x_soft_delete_case__mutmut_17: das Vorlesen der Fallzeile filtert auf
        ``pk`` und die Einrichtung der handelnden Person."""
        with mitschnitt() as abfragen:
            soft_delete_case(case_open, staff_user)
        assert any(
            a.traegt(id=[case_open.pk], facility_id=[staff_user.facility_id]) for a in abfragen.lesungen("core_case")
        )

    @pytest.mark.parametrize("akteur", [None, AnonymousUser()], ids=["none", "anonym"])
    def test_akteur_ohne_einrichtung_wird_abgewiesen(self, case_open, akteur):
        """x_soft_delete_case__mutmut_6: ohne ``facility_id``-Attribut warf der
        Akteur ``AttributeError`` statt der fail-closed-Absage."""
        with pytest.raises(ValidationError) as exc:
            soft_delete_case(case_open, akteur)
        assert exc.value.messages == [str(CASE_OTHER_FACILITY_MESSAGE)]
        assert Case.objects.filter(pk=case_open.pk, is_deleted=False).exists()


class TestMoveCaseToTrash:
    """``_move_case_to_trash`` behind ``soft_delete_case``: the refusal when the case row left the facility."""

    def test_fallzeile_fehlt_in_der_einrichtung(self, case_open, other_facility, staff_user):
        """x__move_case_to_trash__mutmut_8: without the message text the
        refusal lost the facility boundary. The case row moves to another
        facility directly after the person lock of ``soft_delete_case``, that
        is after its scoped read and before the case lock. Observed: the
        refusal names the facility boundary and the case stays out of the
        trash."""
        with mitschnitt() as queries, pytest.raises(ValidationError) as exc:
            queries.nach_sperre(
                "core_client",
                lambda: Case.objects.filter(pk=case_open.pk).update(facility=other_facility),
            )
            soft_delete_case(case_open, staff_user)
        assert exc.value.messages == [str(CASE_OTHER_FACILITY_MESSAGE)]
        assert queries.offene_schranken() == []
        assert Case.objects.filter(pk=case_open.pk, is_deleted=False).exists()
