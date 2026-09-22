"""Zustandsvertrag der Fall-Statuswechsel (Refs #2288).

``close_case``/``reopen_case`` bekommen ihre Fallinstanz von der View, die sie
VOR dem Statuswechsel gelesen hat. Zwischen Lesen und Speichern liegt ein
Fenster: eine parallele Anfrage kann den Fall in dieser Zeit bearbeiten, in den
Papierkorb legen oder endgueltig tilgen. Ein voller ``save()`` auf der alten
Instanz schreibt danach die alten Inhalte **und** die alten Zustandsmerkmale
(``is_deleted``, ``deleted_at``, ``anonymized_at``) zurueck — der getilgte Fall
steht wieder da, als waere nichts gewesen.

Die Guards stellen das Fenster deterministisch nach: zwei Instanzen derselben
Zeile, die zweite ist die „alte". Kein Nebenlauf, kein Timing — der Fehler ist
ein Schreibfehler, keine Renn-Bedingung. Den echten Zweiverbindungs-Nachweis
traegt ``test_goal_status_redaction_guard`` (Refs #2295).

Getilgt wird ueber die ECHTEN Services (``soft_delete_case`` ->
``anonymize_case_now``, ``approve_client_deletion``), nicht ueber
``queryset.update()``: geprueft werden soll der Zustand, den der Produktivpfad
tatsaechlich hinterlaesst.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import Case
from core.services.case import (
    anonymize_case_now,
    close_case,
    reopen_case,
    soft_delete_case,
    update_case,
)


def _zweite_instanz(case):
    """Die ZWEITE Instanz derselben Zeile — der Stand von vor dem Fenster."""
    return Case.objects.get(pk=case.pk)


@pytest.mark.django_db
class TestFallstatusSchreibtGetilgtesNichtZurueck:
    """Ein Statuswechsel darf keine Tilgung rueckgaengig machen."""

    def test_close_case_stellt_den_anonymisierten_fall_nicht_wieder_her(self, case_open, staff_user, admin_user):
        """Der Kern des Befunds: alte Instanz + voller Save = auferstandener Fall."""
        alte_instanz = _zweite_instanz(case_open)
        alter_titel = alte_instanz.title

        soft_delete_case(case_open, staff_user)
        anonymize_case_now(case_open, admin_user)
        getilgter_titel = Case.objects.values_list("title", flat=True).get(pk=case_open.pk)
        assert alter_titel not in getilgter_titel, "Aufbau kaputt: die Tilgung hat den Titel nicht ersetzt"

        with pytest.raises(ValidationError):
            close_case(alte_instanz, staff_user)

        frisch = Case.objects.get(pk=case_open.pk)
        assert frisch.title == getilgter_titel
        assert frisch.description == ""
        assert frisch.data_json == {}
        assert frisch.is_deleted is True
        assert frisch.deleted_at is not None
        assert frisch.anonymized_at is not None
        assert frisch.status == Case.Status.OPEN

    def test_reopen_case_stellt_den_anonymisierten_fall_nicht_wieder_her(self, case_closed, staff_user, admin_user):
        """Spiegel des Schliessens — der Wiedereroeffnungs-Pfad ist derselbe Save."""
        alte_instanz = _zweite_instanz(case_closed)
        alter_titel = alte_instanz.title

        soft_delete_case(case_closed, staff_user)
        anonymize_case_now(case_closed, admin_user)
        getilgter_titel = Case.objects.values_list("title", flat=True).get(pk=case_closed.pk)
        assert alter_titel not in getilgter_titel, "Aufbau kaputt: die Tilgung hat den Titel nicht ersetzt"

        with pytest.raises(ValidationError):
            reopen_case(alte_instanz, staff_user)

        frisch = Case.objects.get(pk=case_closed.pk)
        assert frisch.title == getilgter_titel
        assert frisch.is_deleted is True
        assert frisch.anonymized_at is not None
        assert frisch.status == Case.Status.CLOSED
        assert frisch.closed_at is not None

    def test_reopen_case_holt_den_fall_nicht_aus_dem_papierkorb(self, case_closed, staff_user):
        """Papierkorb ohne Tilgung: ``is_deleted`` allein ist schon die Grenze.

        Der Fall ist hier noch nicht redigiert — es geht ausschliesslich um das
        Zustandsmerkmal. Ein Statuswechsel ist kein Wiederherstellen; dafuer
        gibt es ``restore_case`` mit eigener Pruefung und eigener Audit-Zeile.
        """
        alte_instanz = _zweite_instanz(case_closed)
        soft_delete_case(case_closed, staff_user)

        with pytest.raises(ValidationError):
            reopen_case(alte_instanz, staff_user)

        frisch = Case.objects.get(pk=case_closed.pk)
        assert frisch.is_deleted is True
        assert frisch.deleted_at is not None
        assert frisch.status == Case.Status.CLOSED

    def test_close_case_schreibt_nicht_an_die_akte_einer_geloeschten_person(
        self, case_open, client_identified, staff_user, admin_user
    ):
        """Die Person im Papierkorb zieht ihre Fallakte mit.

        Der Fall selbst ist unberuehrt — die Grenze kommt allein von der
        Person (``cases_with_live_client`` zieht sie in der View, der Service
        muss sie selbst ziehen).
        """
        from core.services.client import approve_client_deletion, request_client_deletion

        alte_instanz = _zweite_instanz(case_open)
        antrag = request_client_deletion(client_identified, staff_user, reason="Widerruf")
        approve_client_deletion(antrag, admin_user)
        client_identified.refresh_from_db()
        assert client_identified.is_deleted is True, "Aufbau kaputt: die Person liegt nicht im Papierkorb"

        with pytest.raises(ValidationError):
            close_case(alte_instanz, staff_user)

        frisch = Case.objects.get(pk=case_open.pk)
        assert frisch.status == Case.Status.OPEN
        assert frisch.closed_at is None


@pytest.mark.django_db
class TestFallstatusUeberschreibtParalleleInhalteNicht:
    """Ein Statuswechsel schreibt Status und Zeitspalte — sonst nichts."""

    def test_close_case_ueberschreibt_den_parallel_geaenderten_titel_nicht(self, case_open, staff_user):
        alte_instanz = _zweite_instanz(case_open)
        update_case(case_open, staff_user, title="Parallel vergebener Titel", description="Parallel ergaenzt")

        close_case(alte_instanz, staff_user)

        frisch = Case.objects.get(pk=case_open.pk)
        assert frisch.title == "Parallel vergebener Titel"
        assert frisch.description == "Parallel ergaenzt"
        assert frisch.status == Case.Status.CLOSED
        assert frisch.closed_at is not None

    def test_reopen_case_ueberschreibt_den_parallel_geaenderten_titel_nicht(self, case_closed, staff_user):
        alte_instanz = _zweite_instanz(case_closed)
        update_case(case_closed, staff_user, title="Parallel vergebener Titel")

        reopen_case(alte_instanz, staff_user)

        frisch = Case.objects.get(pk=case_closed.pk)
        assert frisch.title == "Parallel vergebener Titel"
        assert frisch.status == Case.Status.OPEN
        assert frisch.closed_at is None
