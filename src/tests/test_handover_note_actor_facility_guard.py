"""Die Uebergabenotiz zieht die Einrichtungsgrenze der handelnden Person selbst.

``can_write_notes`` prueft nur die Rolle. Ueber HTTP liefert die View die
Einrichtung aus der Sitzung und weist Konten ohne Einrichtung ab, in
Produktion traegt zusaetzlich die Zeilensicherheit der Datenbank mit; im
Test ist sie nicht aktiv, der Dienst ist also die einzige Grenze. Fuer alle
drei Zweige — Neuanlage, Aenderung, Leeren (= Loeschen) — gilt: eine
Fachkraft aus einer anderen Einrichtung und eine Fachkraft ohne Einrichtung
werden vor jeder Zeilensperre abgewiesen, kein Notizdatensatz entsteht,
aendert sich oder verschwindet, und es entsteht keine Audit-Zeile.
"""

from datetime import date, datetime, time

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import AuditLog, ShiftHandoverNote, TimeFilter, User
from core.services.case.handover_note import Shift, decrypt_note, save_note
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_NOTIZ_FREMD = "Übergabenotiz gehört zu einer anderen Einrichtung."
_AKTEURE = ["fremde_fachkraft", "fachkraft_ohne_einrichtung"]


def _berlin(hour):
    return datetime(2026, 6, 15, hour, 0).replace(tzinfo=timezone.get_current_timezone())


@pytest.fixture
def schicht(facility):
    fruehdienst = TimeFilter.objects.create(
        facility=facility, label="Frühdienst", start_time=time(6, 0), end_time=time(14, 0), sort_order=0
    )
    return Shift(date(2026, 6, 15), fruehdienst)


@pytest.fixture
def fremde_fachkraft(second_facility):
    return User.objects.create_user(
        username="fremdfachkraft-notiz", role=User.Role.STAFF, facility=second_facility, is_staff=True
    )


@pytest.fixture
def fachkraft_ohne_einrichtung():
    return User.objects.create_user(
        username="ohneeinrichtung-notiz", role=User.Role.STAFF, facility=None, is_staff=True
    )


@pytest.fixture
def bestand(facility, schicht, staff_user):
    return save_note(facility, schicht, staff_user, "Bestand", now=_berlin(7))


def _stand():
    return (
        list(ShiftHandoverNote.objects.order_by("pk").values_list("pk", "note", "updated_by_id", "updated_at")),
        AuditLog.objects.count(),
    )


def _abgewiesen_ohne_sperre(aufruf):
    stand = _stand()
    with mitschnitt() as m, pytest.raises(ValidationError) as excinfo:
        aufruf()
    assert excinfo.value.messages == [_NOTIZ_FREMD]
    assert _stand() == stand
    assert m.sperren("core_shifthandovernote") == []
    assert m.sperrfolge() == []


class TestFremderAkteur:
    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_neuanlage(self, request, akteur, facility, schicht):
        user = request.getfixturevalue(akteur)

        _abgewiesen_ohne_sperre(lambda: save_note(facility, schicht, user, "Fremd", now=_berlin(8)))

        assert not ShiftHandoverNote.objects.exists()

    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_aenderung(self, request, akteur, facility, schicht, bestand):
        user = request.getfixturevalue(akteur)

        _abgewiesen_ohne_sperre(lambda: save_note(facility, schicht, user, "Fremd", now=_berlin(8)))

        assert decrypt_note(ShiftHandoverNote.objects.get(pk=bestand.pk)) == "Bestand"

    @pytest.mark.parametrize("akteur", _AKTEURE)
    def test_leeren(self, request, akteur, facility, schicht, bestand):
        user = request.getfixturevalue(akteur)

        _abgewiesen_ohne_sperre(lambda: save_note(facility, schicht, user, "", now=_berlin(8)))

        assert ShiftHandoverNote.objects.filter(pk=bestand.pk).exists()

    @pytest.mark.parametrize("akteur", ["staff_user", "fachkraft_ohne_einrichtung"])
    def test_fehlende_einrichtung_der_schicht_wird_abgewiesen(self, request, akteur, schicht):
        """Fail-closed auch dann, wenn beide Seiten keine Einrichtung tragen."""
        user = request.getfixturevalue(akteur)

        _abgewiesen_ohne_sperre(lambda: save_note(None, schicht, user, "Ohne Einrichtung", now=_berlin(8)))

        assert not ShiftHandoverNote.objects.exists()


class TestEigeneEinrichtung:
    def test_legt_an_aendert_und_leert(self, facility, schicht, staff_user, lead_user):
        note = save_note(facility, schicht, staff_user, "Erste Notiz", now=_berlin(7))
        assert decrypt_note(note) == "Erste Notiz"

        geaendert = save_note(facility, schicht, lead_user, "Korrigiert", now=_berlin(8))
        assert geaendert.pk == note.pk
        assert decrypt_note(ShiftHandoverNote.objects.get(pk=note.pk)) == "Korrigiert"

        assert save_note(facility, schicht, staff_user, "", now=_berlin(9)) is None
        assert not ShiftHandoverNote.objects.exists()
        assert list(
            AuditLog.objects.filter(action__startswith="handover_note")
            .order_by("timestamp")
            .values_list("action", flat=True)
        ) == [
            AuditLog.Action.HANDOVER_NOTE_CREATED,
            AuditLog.Action.HANDOVER_NOTE_UPDATED,
            AuditLog.Action.HANDOVER_NOTE_DELETED,
        ]
