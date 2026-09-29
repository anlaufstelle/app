"""Die Fall- und Ziel-Dienste ziehen die Einrichtungsgrenze der handelnden Person selbst.

Ueber HTTP scopen die Views den Fall bereits, in Produktion traegt
zusaetzlich die Zeilensicherheit der Datenbank mit; im Test ist sie nicht
aktiv, der Dienst ist also die einzige Grenze. Eine Fachkraft aus einer
anderen Einrichtung und eine Fachkraft ohne Einrichtung (fail-closed) werden
mit derselben Meldung abgewiesen — und zwar vor jeder Zeilensperre: nichts
wird ueber die Mandantengrenze hinweg gesperrt, nichts angelegt oder
geaendert, es entsteht keine Audit- und keine Zeitstrom-Zeile. Die Gegenprobe
mit einer Fachkraft der eigenen Einrichtung schreibt wie bisher.

Beobachtet wird das Verhalten: Meldung, Datenbestand, Protokollzeilen und die
tatsaechlich gesperrten Tabellen (am ausgefuehrten SQL, nicht am Wortlaut).
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import Activity, AuditLog, Case, User
from core.services.case import update_case
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_FALL_FREMD = "Fall gehört zu einer anderen Einrichtung."
_AKTEURE = ["fremde_fachkraft", "fachkraft_ohne_einrichtung"]


@pytest.fixture
def fremde_fachkraft(second_facility):
    return User.objects.create_user(
        username="fremdfachkraft-falldienst", role=User.Role.LEAD, facility=second_facility, is_staff=True
    )


@pytest.fixture
def fachkraft_ohne_einrichtung():
    return User.objects.create_user(
        username="ohneeinrichtung-falldienst", role=User.Role.LEAD, facility=None, is_staff=True
    )


def _spuren():
    return AuditLog.objects.count(), Activity.objects.count()


def _abgewiesen_ohne_sperre(aufruf):
    """Ruft den Dienst und prueft Meldung, fehlende Spuren und fehlende Sperren."""
    spuren = _spuren()
    with mitschnitt() as m, pytest.raises(ValidationError) as excinfo:
        aufruf()
    assert excinfo.value.messages == [_FALL_FREMD]
    assert _spuren() == spuren
    assert m.sperrfolge() == []


class TestUpdateCase:
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_fremder_akteur_wird_vor_jeder_sperre_abgewiesen(self, request, akteur, case_open):
        user = request.getfixturevalue(akteur)
        fall = Case.objects.get(pk=case_open.pk)

        _abgewiesen_ohne_sperre(lambda: update_case(fall, user, title="Fremd", description="Fremd"))

        frisch = Case.objects.get(pk=case_open.pk)
        assert (frisch.title, frisch.description) == ("Offener Fall", "")
        assert not AuditLog.objects.filter(action=AuditLog.Action.CASE_UPDATE, target_id=str(case_open.pk)).exists()

    def test_eigene_einrichtung_schreibt(self, case_open, staff_user):
        update_case(Case.objects.get(pk=case_open.pk), staff_user, title="Neu")

        assert Case.objects.get(pk=case_open.pk).title == "Neu"
        assert AuditLog.objects.filter(action=AuditLog.Action.CASE_UPDATE, target_id=str(case_open.pk)).count() == 1
