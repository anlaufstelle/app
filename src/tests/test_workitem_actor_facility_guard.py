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


SAMMELDIENSTE = ["status", "prioritaet", "zuweisung"]


def _sammelaufruf(dienst, items, user, zustaendig):
    from core.services.case import bulk_assign_workitems, bulk_update_workitem_priority, bulk_update_workitem_status

    if dienst == "status":
        return bulk_update_workitem_status(items, user, WorkItem.Status.DONE)
    if dienst == "prioritaet":
        return bulk_update_workitem_priority(items, user, WorkItem.Priority.URGENT)
    return bulk_assign_workitems(items, user, zustaendig)


def _stand(items):
    return {
        pk: (status, priority, assigned_to_id)
        for pk, status, priority, assigned_to_id in WorkItem.objects.filter(pk__in=[w.pk for w in items])
        .order_by("pk")
        .values_list("pk", "status", "priority", "assigned_to_id")
    }


class TestSammeldienste:
    """Alle drei Sammeldienste teilen den Sperrhelfer — die Grenze gilt fuer den ganzen Stapel."""

    @pytest.mark.parametrize("akteur", AKTEURE, indirect=True)
    @pytest.mark.parametrize("dienst", SAMMELDIENSTE)
    def test_lokaler_stapel_fremder_akteur(self, facility, client_identified, staff_user, lead_user, akteur, dienst):
        items = [
            _aufgabe(facility, staff_user, client=client_identified, title="Mit Person"),
            _aufgabe(facility, staff_user, title="Ohne Person"),
        ]
        vorher = _stand(items)
        _assert_refused(lambda: _sammelaufruf(dienst, items, akteur, lead_user))
        assert _stand(items) == vorher

    @pytest.mark.parametrize("dienst", SAMMELDIENSTE)
    def test_mischstapel_mit_fremder_aufgabe(
        self, facility, second_facility, client_identified, staff_user, lead_user, dienst
    ):
        lokal = _aufgabe(facility, staff_user, client=client_identified, title="Lokal")
        fremd = _aufgabe(second_facility, None, title="Fremd")
        items = [lokal, fremd]
        vorher = _stand(items)
        _assert_refused(lambda: _sammelaufruf(dienst, items, staff_user, lead_user))
        assert _stand(items) == vorher

    @pytest.mark.parametrize("dienst", SAMMELDIENSTE)
    def test_gegenprobe_eigene_einrichtung(self, facility, client_identified, staff_user, lead_user, dienst):
        items = [
            _aufgabe(facility, staff_user, client=client_identified, title="Mit Person"),
            _aufgabe(facility, staff_user, title="Ohne Person"),
        ]
        vorher = _stand(items)
        audit_vorher = AuditLog.objects.count()
        assert _sammelaufruf(dienst, items, staff_user, lead_user) == 2
        nachher = _stand(items)
        assert all(nachher[pk] != vorher[pk] for pk in vorher)
        assert AuditLog.objects.count() == audit_vorher + 2


class TestBearbeiten:
    """Echte, unverfaelschte Aufgabe der eigenen Einrichtung — fremd ist nur die handelnde Person."""

    @pytest.mark.parametrize("akteur", AKTEURE, indirect=True)
    @pytest.mark.parametrize("mit_person", [True, False], ids=["mit_person", "ohne_person"])
    def test_abgewiesen(self, facility, client_identified, staff_user, akteur, mit_person):
        from core.services.case import update_workitem

        person = client_identified if mit_person else None
        workitem = _aufgabe(facility, staff_user, client=person, title="Alt")
        _assert_refused(lambda: update_workitem(workitem, akteur, title="Neu"))
        assert WorkItem.objects.get(pk=workitem.pk).title == "Alt"

    def test_gegenprobe_eigene_einrichtung(self, facility, client_identified, staff_user):
        from core.services.case import update_workitem

        workitem = _aufgabe(facility, staff_user, client=client_identified, title="Alt")
        vorher = _zaehler()
        update_workitem(workitem, staff_user, title="Neu")
        assert WorkItem.objects.get(pk=workitem.pk).title == "Neu"
        assert _zaehler() == (vorher[0], vorher[1] + 1, vorher[2] + 1)
