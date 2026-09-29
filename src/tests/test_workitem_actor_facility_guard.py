"""Aufgabendienste ziehen die Einrichtungsgrenze der handelnden Person selbst.

Ueber HTTP laden die Views einrichtungsgebunden, in Produktion traegt
zusaetzlich die Zeilensicherheit; im Test ist sie nicht aktiv, der Dienst ist
also die einzige Grenze. Eine Fachkraft einer anderen Einrichtung und eine
Person ohne Einrichtung (fail-closed) werden abgewiesen, bevor irgendeine
Zeile gesperrt oder geschrieben wird: keine Aufgabe, keine Aenderung, keine
Audit- und keine Zeitstrom-Zeile, keine Zeilensperre. Die Gegenprobe mit einer
Fachkraft der eigenen Einrichtung zeigt, dass der Normalpfad unveraendert
schreibt.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import Activity, AuditLog, User, WorkItem
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_FREMD = "Aufgabe gehört zu einer anderen Einrichtung."

AKTEURE = ["fremde_fachkraft", "fachkraft_ohne_einrichtung"]


@pytest.fixture
def fremde_fachkraft(second_facility):
    return User.objects.create_user(
        username="fremdfachkraft-aufgabe",
        role=User.Role.LEAD,
        facility=second_facility,
        is_staff=True,
    )


@pytest.fixture
def fachkraft_ohne_einrichtung():
    return User.objects.create_user(
        username="ohneeinrichtung-aufgabe",
        role=User.Role.LEAD,
        facility=None,
        is_staff=True,
    )


@pytest.fixture
def akteur(request):
    return request.getfixturevalue(request.param)


def _zaehler():
    return WorkItem.objects.count(), AuditLog.objects.count(), Activity.objects.count()


def _assert_refused(aufruf, meldung=_FREMD):
    """Abgewiesen mit ``meldung`` — ohne Zeilensperre und ohne geschriebene Zeile."""
    vorher = _zaehler()
    with mitschnitt() as m, pytest.raises(ValidationError) as excinfo:
        aufruf()
    assert excinfo.value.messages == [meldung]
    assert m.sperrfolge() == [], f"Sperre vor der Absage: {m.sperrfolge()}"
    assert _zaehler() == vorher


def _aufgabe(facility, user, *, client=None, title="Aufgabe", **extra):
    return WorkItem.objects.create(facility=facility, created_by=user, client=client, title=title, **extra)


class TestAnlegen:
    @pytest.mark.parametrize("akteur", AKTEURE, indirect=True)
    @pytest.mark.parametrize("mit_person", [True, False], ids=["mit_person", "ohne_person"])
    def test_abgewiesen(self, facility, client_identified, akteur, mit_person):
        from core.services.case import create_workitem

        person = client_identified if mit_person else None
        _assert_refused(lambda: create_workitem(facility, akteur, client=person, title="Neu"))
        assert not WorkItem.objects.filter(title="Neu").exists()

    @pytest.mark.parametrize("mit_person", [True, False], ids=["mit_person", "ohne_person"])
    def test_gegenprobe_eigene_einrichtung(self, facility, client_identified, staff_user, mit_person):
        from core.services.case import create_workitem

        person = client_identified if mit_person else None
        vorher = _zaehler()
        workitem = create_workitem(facility, staff_user, client=person, title="Neu")
        assert workitem.facility_id == facility.pk
        assert _zaehler() == (vorher[0] + 1, vorher[1] + 1, vorher[2] + 1)
