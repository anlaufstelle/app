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
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Client, WorkItem
from core.services.case.workitems import update_workitem, workitem_form_state_token
from tests._row_lock_helpers import Zugriff, mitschnitt

pytestmark = pytest.mark.django_db

WORKITEM_TABLE = "core_workitem"


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


def _form_rendered_before_parallel_write(workitem, **parallel_changes):
    """Version token and form fingerprint as rendered, then a parallel write on the row.

    The parallel write always moves ``updated_at``, so ``update_workitem`` runs
    into the version conflict and decides it through the form fingerprint.
    """
    expected_updated_at = workitem.updated_at.isoformat()
    form_state = workitem_form_state_token(workitem)
    WorkItem.objects.filter(pk=workitem.pk).update(updated_at=timezone.now() + timedelta(minutes=1), **parallel_changes)
    return expected_updated_at, form_state


def _assert_row_locks_behind_facility_boundary(queries, workitem):
    """Row locks on the work item table address its row, behind the facility boundary.

    Every lock on the table is ``FOR UPDATE`` on the ``pk`` of the addressed
    work item. At least one of them also carries its facility, and every read
    or lock on the table up to that lock carries ``pk`` and facility as well:
    accesses on the ``pk`` alone (version token check, change diff) only
    follow it, when the row is locked and known to belong to the facility.
    How many reads and locks the service issues stays open, so an additional
    lock on the same row or a merged read remains valid.
    """
    scope = {"id": [workitem.pk], "facility_id": [workitem.facility_id]}
    locks = queries.sperren(WORKITEM_TABLE)
    assert locks
    assert all(lock.sperrart == "UPDATE" for lock in locks), locks
    assert all(lock.traegt(id=[workitem.pk]) for lock in locks), locks
    accesses = [Zugriff(query, WORKITEM_TABLE) for query in queries if query.liest(WORKITEM_TABLE)]
    boundary = next(
        (index for index, access in enumerate(accesses) if access.sperrart == "UPDATE" and access.traegt(**scope)),
        None,
    )
    assert boundary is not None, accesses
    assert all(access.traegt(**scope) for access in accesses[:boundary]), accesses


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
    """``_locked_client_id`` as ``update_workitem`` uses it: the work item lock hits the addressed row."""

    def test_liefert_die_person_der_adressierten_aufgabe(self, eigene_und_nachbar, staff_user, client_identified):
        """x__locked_client_id__mutmut_8: without ``pk`` the locked read
        returned the person of the newer neighbouring work item, so
        ``update_workitem`` refused the edit as a changed person assignment.
        Observed: the edit that repeats the unchanged person lands, and the
        work item keeps its person."""
        eigene, _ = eigene_und_nachbar
        update_workitem(eigene, staff_user, title="New title", client=client_identified)
        eigene.refresh_from_db()
        assert eigene.title == "New title"
        assert eigene.client_id == client_identified.pk

    def test_aufgabensperre_traegt_pk_und_einrichtung(self, eigene_und_nachbar, staff_user):
        """x__locked_client_id__mutmut_8: without ``pk`` the work item lock
        reads the newest work item of the facility. Observed on the plain edit
        (no version conflict, so the fingerprint read does not run): every
        row lock on the work item addresses its ``pk``, the lock scoped to
        ``pk`` and facility precedes any access on the ``pk`` alone, and the
        edit lands."""
        eigene, _ = eigene_und_nachbar
        with mitschnitt() as abfragen:
            update_workitem(eigene, staff_user, title="New title")
        _assert_row_locks_behind_facility_boundary(abfragen, eigene)
        eigene.refresh_from_db()
        assert eigene.title == "New title"


class TestFormStateUnchanged:
    """``_form_state_unchanged`` on the conflict path of ``update_workitem``: the fingerprint reads the own row."""

    def test_reiner_versionskonflikt_wird_neben_einer_nachbaraufgabe_gerettet(self, eigene_und_nachbar, staff_user):
        """x__form_state_unchanged__mutmut_8: only ``updated_at`` of the own
        work item changed in parallel; without ``pk`` the rescue compared
        against the form values of the newer neighbouring work item and
        refused the edit."""
        eigene, _ = eigene_und_nachbar
        rendered_at, form_state = _form_rendered_before_parallel_write(eigene)
        update_workitem(
            eigene, staff_user, expected_updated_at=rendered_at, expected_form_state=form_state, title="New title"
        )
        eigene.refresh_from_db()
        assert eigene.title == "New title"

    def test_parallele_feldaenderung_bleibt_konflikt_neben_gleichlautender_nachbaraufgabe(
        self, facility, client_identified, staff_user
    ):
        """x__form_state_unchanged__mutmut_8: the newer neighbouring work item
        carries exactly the rendered form values, the own one was renamed in
        parallel. Without ``pk`` that counted as unchanged and the edit would
        silently overwrite the parallel change. Observed: the version conflict
        stands and the parallel title stays."""
        eigene = _aufgabe(facility, client_identified, staff_user, alter_tage=5, titel="Gleich")
        _aufgabe(facility, client_identified, staff_user, alter_tage=0, titel="Gleich")
        rendered_at, form_state = _form_rendered_before_parallel_write(eigene, title="Changed in parallel")
        with pytest.raises(ValidationError) as exc:
            update_workitem(
                eigene, staff_user, expected_updated_at=rendered_at, expected_form_state=form_state, title="New title"
            )
        assert exc.value.code == "version_conflict"
        eigene.refresh_from_db()
        assert eigene.title == "Changed in parallel"

    def test_fingerabdruck_lesung_traegt_pk_und_einrichtung(self, eigene_und_nachbar, staff_user):
        """x__form_state_unchanged__mutmut_8: without ``pk`` the fingerprint
        read locks and compares the newest work item of the facility. Observed
        on the version conflict path: every row lock on the work item, the
        fingerprint read included, addresses its ``pk``, the lock scoped to
        ``pk`` and facility precedes any access on the ``pk`` alone, and the
        rescued edit lands."""
        eigene, _ = eigene_und_nachbar
        rendered_at, form_state = _form_rendered_before_parallel_write(eigene)
        with mitschnitt() as abfragen:
            update_workitem(
                eigene, staff_user, expected_updated_at=rendered_at, expected_form_state=form_state, title="New title"
            )
        _assert_row_locks_behind_facility_boundary(abfragen, eigene)
        eigene.refresh_from_db()
        assert eigene.title == "New title"
