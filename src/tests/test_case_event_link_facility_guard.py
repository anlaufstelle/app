"""Refs #2364: Fallzuordnung und Loesen ziehen die Einrichtungsgrenze selbst.

Zwei Zusagen:

(a) ``assign_event_to_case`` und ``remove_event_from_case`` pruefen die
    Einrichtung der aufrufenden Person gegen die von Fall und Ereignis — nicht
    nur Fall gegen Ereignis. Ueber HTTP scopen die Views bereits, in Produktion
    traegt zusaetzlich RLS; im Test ist RLS nicht aktiv, der Service ist also
    die einzige Grenze. Eine Person ohne Einrichtung wird ebenfalls
    abgewiesen (fail-closed). Abgewiesen wird vor jeder Sperre und jedem
    Schreibzugriff: keine Zuordnungsaenderung, keine Audit-Zeile, keine
    Zeitstrom-Zeile.

(b) ``CaseRemoveEventView`` loest ein Ereignis nur von dem Fall, den die URL
    benennt. Die URL eines anderen Falls derselben Einrichtung ergibt 404,
    das Ereignis bleibt am bisherigen Fall, es entsteht keine Audit-Zeile.
"""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import Activity, AuditLog, Case, Event, User

pytestmark = pytest.mark.django_db

_FALL_FREMD = "Fall gehört zu einer anderen Einrichtung."
_EREIGNIS_FREMD = "Ereignis gehört zu einer anderen Einrichtung."


@pytest.fixture
def foreign_staff(second_facility):
    return User.objects.create_user(
        username="fremdfachkraft-fall",
        role=User.Role.LEAD,
        facility=second_facility,
        is_staff=True,
    )


@pytest.fixture
def facilityless_staff():
    return User.objects.create_user(
        username="ohneeinrichtung-fall",
        role=User.Role.LEAD,
        facility=None,
        is_staff=True,
    )


def _zaehler():
    return AuditLog.objects.count(), Activity.objects.count()


def _assert_refused(callable_, meldung, event_pk, erwarteter_fall_id):
    vorher = _zaehler()
    with pytest.raises(ValidationError) as excinfo:
        callable_()
    assert excinfo.value.messages == [meldung]
    assert Event.objects.get(pk=event_pk).case_id == erwarteter_fall_id
    assert _zaehler() == vorher


class TestAssignRefusesForeignCaller:
    def test_fremde_einrichtung(self, case_open, sample_event, foreign_staff):
        from core.services.case import assign_event_to_case

        _assert_refused(
            lambda: assign_event_to_case(case_open, sample_event, foreign_staff),
            _FALL_FREMD,
            sample_event.pk,
            None,
        )

    def test_ohne_einrichtung(self, case_open, sample_event, facilityless_staff):
        from core.services.case import assign_event_to_case

        _assert_refused(
            lambda: assign_event_to_case(case_open, sample_event, facilityless_staff),
            _FALL_FREMD,
            sample_event.pk,
            None,
        )

    def test_umhaengen_durch_fremde_einrichtung(
        self, facility, case_open, client_identified, sample_event, foreign_staff, staff_user
    ):
        from core.services.case import assign_event_to_case

        assign_event_to_case(case_open, sample_event, staff_user)
        zweiter = Case.objects.create(
            facility=facility, client=client_identified, title="Zweiter", status=Case.Status.OPEN
        )
        _assert_refused(
            lambda: assign_event_to_case(zweiter, Event.objects.get(pk=sample_event.pk), foreign_staff),
            _FALL_FREMD,
            sample_event.pk,
            case_open.pk,
        )


class TestRemoveRefusesForeignCaller:
    def test_fremde_einrichtung(self, case_open, sample_event, foreign_staff, staff_user):
        from core.services.case import assign_event_to_case, remove_event_from_case

        assign_event_to_case(case_open, sample_event, staff_user)
        _assert_refused(
            lambda: remove_event_from_case(Event.objects.get(pk=sample_event.pk), foreign_staff),
            _EREIGNIS_FREMD,
            sample_event.pk,
            case_open.pk,
        )

    def test_ohne_einrichtung(self, case_open, sample_event, facilityless_staff, staff_user):
        from core.services.case import assign_event_to_case, remove_event_from_case

        assign_event_to_case(case_open, sample_event, staff_user)
        _assert_refused(
            lambda: remove_event_from_case(Event.objects.get(pk=sample_event.pk), facilityless_staff),
            _EREIGNIS_FREMD,
            sample_event.pk,
            case_open.pk,
        )


class TestOwnFacilityStillPasses:
    """Gegen-Check: die Grenze darf den regulaeren Weg nicht zumauern."""

    def test_assign_und_remove(self, case_open, sample_event, staff_user):
        from core.services.case import assign_event_to_case, remove_event_from_case

        assign_event_to_case(case_open, sample_event, staff_user)
        assert Event.objects.get(pk=sample_event.pk).case_id == case_open.pk
        remove_event_from_case(Event.objects.get(pk=sample_event.pk), staff_user)
        assert Event.objects.get(pk=sample_event.pk).case_id is None
        assert AuditLog.objects.filter(action=AuditLog.Action.CASE_EVENT_ASSIGNED).count() == 1
        assert AuditLog.objects.filter(action=AuditLog.Action.CASE_EVENT_REMOVED).count() == 1

    def test_mismatch_meldung_unveraendert(self, case_open, other_facility, staff_user):
        """Fall und Ereignis aus verschiedenen Einrichtungen: Klasse und Wortlaut wie bisher."""
        from django.utils import timezone

        from core.models import DocumentType
        from core.services.case import assign_event_to_case

        other_doc = DocumentType.objects.create(facility=other_facility, name="Kontakt")
        other_event = Event.objects.create(
            facility=other_facility, document_type=other_doc, occurred_at=timezone.now(), data_json={}
        )
        with pytest.raises(ValueError, match="Ereignis gehört nicht zur selben Einrichtung wie der Fall."):
            assign_event_to_case(case_open, other_event, staff_user)


class TestRemoveViewBindsToUrlCase:
    def test_anderer_fall_in_der_url_ergibt_404(
        self, client, facility, client_identified, case_open, sample_event, staff_user
    ):
        from core.services.case import assign_event_to_case

        assign_event_to_case(case_open, sample_event, staff_user)
        anderer = Case.objects.create(
            facility=facility, client=client_identified, title="Anderer Fall", status=Case.Status.OPEN
        )
        client.force_login(staff_user)
        vorher = _zaehler()
        response = client.post(
            reverse("core:case_remove_event", kwargs={"pk": anderer.pk, "event_pk": sample_event.pk}),
        )
        assert response.status_code == 404
        assert Event.objects.get(pk=sample_event.pk).case_id == case_open.pk
        assert _zaehler() == vorher

    def test_ereignis_ohne_fall_ergibt_404(self, client, case_open, sample_event, staff_user):
        client.force_login(staff_user)
        vorher = _zaehler()
        response = client.post(
            reverse("core:case_remove_event", kwargs={"pk": case_open.pk, "event_pk": sample_event.pk}),
        )
        assert response.status_code == 404
        assert Event.objects.get(pk=sample_event.pk).case_id is None
        assert _zaehler() == vorher

    def test_richtiger_fall_loest_weiterhin(self, client, case_open, sample_event, staff_user):
        from core.services.case import assign_event_to_case

        assign_event_to_case(case_open, sample_event, staff_user)
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_remove_event", kwargs={"pk": case_open.pk, "event_pk": sample_event.pk}),
        )
        assert response.status_code == 200
        assert Event.objects.get(pk=sample_event.pk).case_id is None
