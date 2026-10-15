"""Tests for Case CRUD, event assignment, and navigation."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import Case, DocumentType, Event
from core.services.case import (
    assign_event_to_case,
    close_case,
    create_case,
    remove_event_from_case,
    reopen_case,
    update_case,
)


@pytest.mark.django_db
@pytest.mark.parametrize("boundary", ["closed", "high"])
def test_case_assignment_rejects_unassignable_case(case_open, sample_event, staff_user, facility, boundary):
    if boundary == "closed":
        Case.objects.filter(pk=case_open.pk).update(status=Case.Status.CLOSED, closed_at=timezone.now())
    else:
        case_type = DocumentType.objects.create(
            facility=facility,
            name="Restricted case",
            entity_type=DocumentType.EntityType.CASE,
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        Case.objects.filter(pk=case_open.pk).update(document_type=case_type)
    # The caller's stale instance must not bypass the locked row's boundary.
    with pytest.raises(ValidationError, match="Der Fall ist nicht mehr verfügbar oder nicht zugänglich"):
        assign_event_to_case(case_open, sample_event, staff_user)
    sample_event.refresh_from_db()
    assert sample_event.case_id is None


@pytest.mark.django_db
def test_case_assignment_allows_high_case_for_lead(case_open, sample_event, lead_user, facility):
    case_type = DocumentType.objects.create(
        facility=facility,
        name="Restricted case",
        entity_type=DocumentType.EntityType.CASE,
        sensitivity=DocumentType.Sensitivity.HIGH,
    )
    Case.objects.filter(pk=case_open.pk).update(document_type=case_type)
    assign_event_to_case(case_open, sample_event, lead_user)
    sample_event.refresh_from_db()
    assert sample_event.case_id == case_open.pk


@pytest.mark.django_db
@pytest.mark.parametrize("boundary", ["closed", "high", "open"])
def test_case_detail_assignment_selection_respects_boundary(
    client, staff_user, case_open, sample_event, facility, boundary
):
    if boundary == "closed":
        Case.objects.filter(pk=case_open.pk).update(status=Case.Status.CLOSED, closed_at=timezone.now())
    elif boundary == "high":
        case_type = DocumentType.objects.create(
            facility=facility,
            name="Restricted case",
            entity_type=DocumentType.EntityType.CASE,
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        Case.objects.filter(pk=case_open.pk).update(document_type=case_type)
    client.force_login(staff_user)
    response = client.get(reverse("core:case_detail", kwargs={"pk": case_open.pk}))
    assert response.status_code == 200
    assert (b'name="event_id"' in response.content) == (boundary == "open")


@pytest.mark.django_db
class TestCaseService:
    def test_create_case(self, facility, staff_user, client_identified):
        case = create_case(
            facility=facility,
            user=staff_user,
            client=client_identified,
            title="Testfall",
            description="Beschreibung",
        )
        assert case.pk is not None
        assert case.status == Case.Status.OPEN
        assert case.created_by == staff_user
        assert case.client == client_identified
        assert case.facility == facility

    def test_create_case_with_lead_user(self, facility, staff_user, lead_user, client_identified):
        case = create_case(
            facility=facility,
            user=staff_user,
            client=client_identified,
            title="Mit Leitung",
            lead_user=lead_user,
        )
        assert case.lead_user == lead_user

    def test_create_case_validates_client_facility(self, facility, staff_user, other_facility):
        from core.models import Client

        other_client = Client.objects.create(
            facility=other_facility,
            pseudonym="Anderer-01",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        with pytest.raises(ValueError, match="Person"):
            create_case(
                facility=facility,
                user=staff_user,
                client=other_client,
                title="Falsch",
            )

    def test_update_case(self, facility, staff_user, case_open):
        updated = update_case(case_open, staff_user, title="Neuer Titel")
        assert updated.title == "Neuer Titel"

    def test_update_case_rejects_unknown_field(self, facility, staff_user, case_open):
        with pytest.raises(ValueError, match="Feld"):
            update_case(case_open, staff_user, status="closed")

    def test_update_case_rejects_client_change(self, case_open, client_qualified, staff_user):
        """#1786: Personenzuordnung ist nach dem Anlegen unveraenderlich (Mass-Assignment-Guard)."""
        with pytest.raises(ValueError, match="client"):
            update_case(case_open, staff_user, client=client_qualified)

    def test_update_case_rejects_same_client_value(self, case_open, staff_user):
        """Auch der unveraenderte Wert wird abgelehnt — client ist kein Update-Feld mehr."""
        with pytest.raises(ValueError, match="client"):
            update_case(case_open, staff_user, client=case_open.client)

    def test_update_case_erhaelt_parallele_redaktion(self, case_open, staff_user, lead_user):
        """#1789-Folge (Review-Befund): Die Redaktions-Kaskade schreibt
        title/description per Queryset-Update ohne ``updated_at``-Bump — ein
        staler Save, der nur andere Felder aktualisiert, darf den Klartext
        nicht re-publizieren."""
        Case.objects.filter(pk=case_open.pk).update(title="Fall (anonymisiert)", description="")
        update_case(case_open, staff_user, lead_user=lead_user)
        refreshed = Case.objects.get(pk=case_open.pk)
        assert refreshed.title == "Fall (anonymisiert)"
        assert refreshed.description == ""
        assert refreshed.lead_user == lead_user

    def test_close_case(self, facility, staff_user, case_open):
        from core.models import AuditLog

        closed = close_case(case_open, staff_user)
        assert closed.status == Case.Status.CLOSED
        assert closed.closed_at is not None
        log = AuditLog.objects.get(target_id=str(case_open.pk), action=AuditLog.Action.CASE_CLOSE)
        assert log.user == staff_user
        assert log.target_type == "Case"

    def test_reopen_case(self, facility, staff_user, case_closed):
        from core.models import AuditLog

        reopened = reopen_case(case_closed, staff_user)
        assert reopened.status == Case.Status.OPEN
        assert reopened.closed_at is None
        log = AuditLog.objects.get(target_id=str(case_closed.pk), action=AuditLog.Action.CASE_REOPEN)
        assert log.user == staff_user
        assert log.target_type == "Case"

    def test_assign_event_to_case(self, facility, staff_user, case_open, sample_event):
        result = assign_event_to_case(case_open, sample_event, staff_user)
        result.refresh_from_db()
        assert result.case == case_open

    def test_assign_event_validates_facility(self, facility, staff_user, case_open, other_facility):
        from core.models import DocumentType

        other_doc = DocumentType.objects.create(facility=other_facility, name="Kontakt")
        other_event = Event.objects.create(
            facility=other_facility,
            document_type=other_doc,
            occurred_at=timezone.now(),
            data_json={},
        )
        with pytest.raises(ValueError, match="Einrichtung"):
            assign_event_to_case(case_open, other_event, staff_user)

    def test_remove_event_from_case(self, facility, staff_user, case_open, sample_event):
        assign_event_to_case(case_open, sample_event, staff_user)
        remove_event_from_case(sample_event, staff_user)
        sample_event.refresh_from_db()
        assert sample_event.case is None


@pytest.mark.django_db
class TestCaseEventAssignmentAudit:
    """Zuordnen und Loesen von Ereignissen sind auditiert (Refs #1959).

    Die Zuordnung entscheidet, in welchem Fallzusammenhang ein Kontakt steht —
    fachlich eine Aussage ueber die Person, technisch ein Schreibvorgang auf
    ``Event.case``. Bis #1959 hinterliess sie keine Spur; ``assign_event_to_case``
    trug den ``user`` sogar schon als Parameter, ausdruecklich „reserved for
    future audit trail", und ``remove_event_from_case`` hatte ihn gar nicht.

    ``target_obj`` ist das **Ereignis**, nicht der Fall: die Zeile beschreibt,
    was mit dem Ereignis geschah, und bleibt so auch nach dem Loesen
    zuordenbar. Der Fall steht als ``case_id`` im ``detail``.
    """

    def test_assign_writes_audit_on_the_event(self, facility, staff_user, case_open, sample_event):
        from core.models import AuditLog

        assign_event_to_case(case_open, sample_event, staff_user)

        log = AuditLog.objects.get(action=AuditLog.Action.CASE_EVENT_ASSIGNED)
        assert log.user == staff_user
        assert log.facility == facility
        assert log.target_type == "Event"
        assert log.target_id == str(sample_event.pk)
        assert log.detail == {"case_id": str(case_open.pk)}

    def test_assign_to_the_same_case_is_idempotent(self, facility, staff_user, case_open, sample_event):
        """Erneutes Zuordnen desselben Falls schreibt keine zweite Zeile."""
        from core.models import AuditLog

        assign_event_to_case(case_open, sample_event, staff_user)
        assign_event_to_case(case_open, sample_event, staff_user)

        assert AuditLog.objects.filter(action=AuditLog.Action.CASE_EVENT_ASSIGNED).count() == 1

    def test_reassignment_names_the_previous_case(
        self, facility, staff_user, case_open, sample_event, client_identified
    ):
        """Ein Umhaengen loest das Ereignis aus dem alten Fall, ohne dass eine
        ``CASE_EVENT_REMOVED``-Zeile entstuende — ohne ``previous_case_id``
        waere der Abgang aus dem alten Fall unbelegt."""
        from core.models import AuditLog

        second = create_case(
            facility=facility,
            user=staff_user,
            client=client_identified,
            title="Zweiter Fall",
        )
        assign_event_to_case(case_open, sample_event, staff_user)
        assign_event_to_case(second, sample_event, staff_user)

        log = AuditLog.objects.filter(action=AuditLog.Action.CASE_EVENT_ASSIGNED).latest("timestamp")
        assert log.detail == {"case_id": str(second.pk), "previous_case_id": str(case_open.pk)}

    def test_remove_writes_audit_naming_the_previous_case(self, facility, staff_user, case_open, sample_event):
        from core.models import AuditLog

        assign_event_to_case(case_open, sample_event, staff_user)
        remove_event_from_case(sample_event, staff_user)

        log = AuditLog.objects.get(action=AuditLog.Action.CASE_EVENT_REMOVED)
        assert log.user == staff_user
        assert log.facility == facility
        assert log.target_type == "Event"
        assert log.target_id == str(sample_event.pk)
        assert log.detail == {"case_id": str(case_open.pk)}

    def test_remove_without_case_writes_nothing(self, facility, staff_user, sample_event):
        """Ein Ereignis ohne Fall zu loesen ist ein No-Op — keine Zeile."""
        from core.models import AuditLog

        remove_event_from_case(sample_event, staff_user)

        assert not AuditLog.objects.filter(action=AuditLog.Action.CASE_EVENT_REMOVED).exists()

    def test_detail_carries_no_freetext(self, facility, staff_user, case_open, sample_event):
        """Weder Fall- noch Ereignistitel gehoeren ins append-only Log (Refs #1751)."""
        from core.models import AuditLog

        assign_event_to_case(case_open, sample_event, staff_user)

        log = AuditLog.objects.get(action=AuditLog.Action.CASE_EVENT_ASSIGNED)
        assert case_open.title not in str(log.detail)


@pytest.mark.django_db
class TestOptimisticLockingCase:
    """Tests for optimistic locking on Case updates (Refs #531)."""

    def test_optimistic_locking_case_conflict(self, case_open, staff_user):
        stale = "2000-01-01T00:00:00+00:00"
        with pytest.raises(ValidationError):
            update_case(
                case_open,
                staff_user,
                title="Parallel-Titel",
                expected_updated_at=stale,
            )
        case_open.refresh_from_db()
        assert case_open.title != "Parallel-Titel"

    def test_optimistic_locking_case_success_with_current_timestamp(self, case_open, staff_user):
        case_open.refresh_from_db()
        current = case_open.updated_at.isoformat()
        update_case(
            case_open,
            staff_user,
            title="OK-Titel",
            expected_updated_at=current,
        )
        case_open.refresh_from_db()
        assert case_open.title == "OK-Titel"

    def test_optimistic_locking_case_none_disables_check(self, case_open, staff_user):
        update_case(
            case_open,
            staff_user,
            title="Legacy-Titel",
            expected_updated_at=None,
        )
        case_open.refresh_from_db()
        assert case_open.title == "Legacy-Titel"


@pytest.mark.django_db
class TestCaseListView:
    def test_case_list_renders(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_list"))
        assert response.status_code == 200
        assert "Fälle" in response.content.decode()

    def test_case_list_search(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_list"), {"q": "Offener"})
        assert response.status_code == 200
        assert "Offener Fall" in response.content.decode()

    def test_case_list_filter_status(self, client, staff_user, case_open, case_closed):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_list"), {"status": "open"})
        content = response.content.decode()
        assert "Offener Fall" in content
        assert "Geschlossener Fall" not in content

    def test_case_list_htmx_partial(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:case_list"),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        # Should return partial, not full page
        assert "<!DOCTYPE" not in response.content.decode()

    def test_case_list_auth_required(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:case_list"))
        assert response.status_code == 403


@pytest.mark.django_db
class TestCaseCreateView:
    def test_case_create_form_renders(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_create"))
        assert response.status_code == 200
        assert "Fall anlegen" in response.content.decode()

    def test_case_create_post(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_create"),
            {
                "title": "Neuer Testfall",
                "description": "Beschreibung",
                "client": str(client_identified.pk),
            },
        )
        assert response.status_code == 302
        assert Case.objects.filter(title="Neuer Testfall").exists()

    def test_case_create_with_client_preselection(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_create") + f"?client={client_identified.pk}")
        assert response.status_code == 200
        assert client_identified.pseudonym in response.content.decode()

    def test_case_create_auth_required(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:case_create"))
        assert response.status_code == 403


@pytest.mark.django_db
class TestCaseDetailView:
    def test_case_detail_renders(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_detail", kwargs={"pk": case_open.pk}))
        assert response.status_code == 200
        assert "Offener Fall" in response.content.decode()

    def test_case_detail_facility_scoping(self, client, staff_user, other_facility):
        from core.models import Client

        other_client = Client.objects.create(
            facility=other_facility,
            pseudonym="Andere-Person-01",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        other_case = Case.objects.create(
            facility=other_facility,
            client=other_client,
            title="Anderer Fall",
            status=Case.Status.OPEN,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:case_detail", kwargs={"pk": other_case.pk}))
        assert response.status_code == 404


@pytest.mark.django_db
class TestCaseUpdateView:
    def test_case_update_form_renders(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_update", kwargs={"pk": case_open.pk}))
        assert response.status_code == 200

    def test_case_update_post(self, client, staff_user, case_open, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", kwargs={"pk": case_open.pk}),
            {
                "title": "Aktualisierter Titel",
                "description": "Neue Beschreibung",
                "client": str(client_identified.pk),
            },
        )
        assert response.status_code == 302
        case_open.refresh_from_db()
        assert case_open.title == "Aktualisierter Titel"


@pytest.mark.django_db
class TestCaseCloseView:
    def test_close_case_as_lead(self, client, lead_user, case_open):
        client.force_login(lead_user)
        response = client.post(reverse("core:case_close", kwargs={"pk": case_open.pk}))
        assert response.status_code == 302
        case_open.refresh_from_db()
        assert case_open.status == Case.Status.CLOSED

    def test_close_case_forbidden_for_staff(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.post(reverse("core:case_close", kwargs={"pk": case_open.pk}))
        assert response.status_code == 403


@pytest.mark.django_db
class TestCaseReopenView:
    def test_reopen_case_as_lead(self, client, lead_user, case_closed):
        client.force_login(lead_user)
        response = client.post(reverse("core:case_reopen", kwargs={"pk": case_closed.pk}))
        assert response.status_code == 302
        case_closed.refresh_from_db()
        assert case_closed.status == Case.Status.OPEN

    def test_reopen_case_forbidden_for_staff(self, client, staff_user, case_closed):
        client.force_login(staff_user)
        response = client.post(reverse("core:case_reopen", kwargs={"pk": case_closed.pk}))
        assert response.status_code == 403


@pytest.mark.django_db
class TestCaseAssignEventView:
    def test_assign_event_to_case(self, client, staff_user, case_open, sample_event):
        from core.models import AuditLog

        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_assign_event", kwargs={"pk": case_open.pk}),
            {"event_id": str(sample_event.pk)},
        )
        assert response.status_code == 200
        sample_event.refresh_from_db()
        assert sample_event.case == case_open
        # Refs #1959: der Akteur muss bis zur Schreibstelle durchgereicht sein —
        # eine Zeile ohne ``user`` waere als Nachweis wertlos.
        log = AuditLog.objects.get(action=AuditLog.Action.CASE_EVENT_ASSIGNED)
        assert log.user == staff_user
        assert log.target_id == str(sample_event.pk)

    def test_assign_event_validates_facility(self, client, staff_user, case_open, other_facility):
        from core.models import DocumentType

        other_doc = DocumentType.objects.create(facility=other_facility, name="Kontakt")
        other_event = Event.objects.create(
            facility=other_facility,
            document_type=other_doc,
            occurred_at=timezone.now(),
            data_json={},
        )
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_assign_event", kwargs={"pk": case_open.pk}),
            {"event_id": str(other_event.pk)},
        )
        # Event from another facility should 404
        assert response.status_code == 404


@pytest.mark.django_db
class TestCaseRemoveEventView:
    def test_remove_event_from_case(self, client, staff_user, case_open, sample_event):
        from core.models import AuditLog

        sample_event.case = case_open
        sample_event.save()
        client.force_login(staff_user)
        response = client.post(
            reverse(
                "core:case_remove_event",
                kwargs={"pk": case_open.pk, "event_pk": sample_event.pk},
            ),
        )
        assert response.status_code == 200
        sample_event.refresh_from_db()
        assert sample_event.case is None
        # Refs #1959: dieselbe Durchreichung wie beim Zuordnen — der Service
        # hatte bis dahin gar keinen ``user``-Parameter.
        log = AuditLog.objects.get(action=AuditLog.Action.CASE_EVENT_REMOVED)
        assert log.user == staff_user
        assert log.detail["case_id"] == str(case_open.pk)


@pytest.mark.django_db
class TestCasesForClientView:
    def test_cases_for_client_returns_json(self, client, staff_user, case_open, client_identified):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:cases_for_client"),
            {"client": str(client_identified.pk)},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Offener Fall"

    def test_cases_for_client_empty(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:cases_for_client"))
        assert response.status_code == 200
        assert response.json() == []
