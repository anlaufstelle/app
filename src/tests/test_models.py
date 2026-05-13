"""Model constraint and logic tests."""

import pytest
from django.db import IntegrityError
from django.utils import timezone

from core.models import AuditLog, Case, Client, Event, EventHistory, Settings


@pytest.mark.django_db
def test_client_unique_pseudonym_per_facility(facility, staff_user):
    """UniqueConstraint (facility, pseudonym) raises IntegrityError on duplicate."""
    Client.objects.create(
        facility=facility,
        pseudonym="DUPE-01",
        contact_stage=Client.ContactStage.IDENTIFIED,
        created_by=staff_user,
    )
    with pytest.raises(IntegrityError):
        Client.objects.create(
            facility=facility,
            pseudonym="DUPE-01",
            contact_stage=Client.ContactStage.QUALIFIED,
            created_by=staff_user,
        )


@pytest.mark.django_db
def test_client_contact_stage_choices():
    """Only IDENTIFIED and QUALIFIED are valid ContactStage choices."""
    choices = [c[0] for c in Client.ContactStage.choices]
    assert set(choices) == {"identified", "qualified"}
    assert Client.ContactStage.IDENTIFIED == "identified"
    assert Client.ContactStage.QUALIFIED == "qualified"


@pytest.mark.django_db
def test_case_status_choices():
    """Only OPEN and CLOSED are valid Status choices."""
    choices = [c[0] for c in Case.Status.choices]
    assert set(choices) == {"open", "closed"}
    assert Case.Status.OPEN == "open"
    assert Case.Status.CLOSED == "closed"


@pytest.mark.django_db
def test_case_closed_at_nullable(facility, client_identified, staff_user):
    """closed_at field accepts a datetime value when status is CLOSED."""
    now = timezone.now()
    case = Case.objects.create(
        facility=facility,
        client=client_identified,
        title="Test-Fall",
        status=Case.Status.CLOSED,
        closed_at=now,
        created_by=staff_user,
    )
    case.refresh_from_db()
    assert case.closed_at is not None
    assert case.status == Case.Status.CLOSED


@pytest.mark.django_db
def test_event_is_deleted_default_false(sample_event):
    """New event has is_deleted=False by default."""
    assert sample_event.is_deleted is False


@pytest.mark.django_db
def test_event_ordering(facility, client_identified, doc_type_contact, staff_user):
    """Events are ordered by -occurred_at (most recent first)."""
    t1 = timezone.now() - timezone.timedelta(hours=2)
    t2 = timezone.now() - timezone.timedelta(hours=1)
    t3 = timezone.now()

    e1 = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=t1,
        data_json={},
        created_by=staff_user,
    )
    e2 = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=t2,
        data_json={},
        created_by=staff_user,
    )
    e3 = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=t3,
        data_json={},
        created_by=staff_user,
    )

    events = list(Event.objects.filter(id__in=[e1.id, e2.id, e3.id]))
    assert events[0].id == e3.id
    assert events[1].id == e2.id
    assert events[2].id == e1.id


@pytest.mark.django_db
def test_user_role_properties(admin_user, lead_user, staff_user, assistant_user):
    """Verifiziere is_facility_admin/is_lead_or_admin/is_staff_or_above/is_assistant_or_above.

    Pruefung der vier facility-gebundenen Rollen (FACILITY_ADMIN, LEAD,
    STAFF, ASSISTANT). Refs #867: Die Lower-Properties (``is_lead_or_admin``,
    ``is_staff_or_above``, ``is_assistant_or_above``) referenzieren explizit
    ``FACILITY_ADMIN`` — NICHT ``SUPER_ADMIN``. Der Super-Admin lebt
    ausserhalb dieser Hierarchie und nur im ``/system/``-Bereich.
    """
    # FACILITY_ADMIN
    assert admin_user.is_facility_admin is True
    assert admin_user.is_super_admin is False
    assert admin_user.is_lead_or_admin is True
    assert admin_user.is_staff_or_above is True
    assert admin_user.is_assistant_or_above is True

    # LEAD
    assert lead_user.is_facility_admin is False
    assert lead_user.is_super_admin is False
    assert lead_user.is_lead_or_admin is True
    assert lead_user.is_staff_or_above is True
    assert lead_user.is_assistant_or_above is True

    # STAFF
    assert staff_user.is_facility_admin is False
    assert staff_user.is_super_admin is False
    assert staff_user.is_lead_or_admin is False
    assert staff_user.is_staff_or_above is True
    assert staff_user.is_assistant_or_above is True

    # ASSISTANT
    assert assistant_user.is_facility_admin is False
    assert assistant_user.is_super_admin is False
    assert assistant_user.is_lead_or_admin is False
    assert assistant_user.is_staff_or_above is False
    assert assistant_user.is_assistant_or_above is True


@pytest.mark.django_db
def test_is_super_admin_property():
    """Refs #867: SUPER_ADMIN steht *ausserhalb* der Facility-Hierarchie.

    Ein User mit ``role=SUPER_ADMIN`` liefert:

    * ``is_super_admin`` True (einziger True-Branch),
    * ``is_facility_admin`` False (verschiedene Rollen),
    * ``is_lead_or_admin`` False — die Property listet nur FACILITY_ADMIN +
      LEAD; super_admin ist NICHT enthalten,
    * ``is_staff_or_above`` False (analog),
    * ``is_assistant_or_above`` False (analog).

    Damit ist sichergestellt, dass alle facility-gescopten Mixins
    (``AssistantOrAboveRequiredMixin``, ``StaffRequiredMixin``,
    ``LeadOrAdminRequiredMixin``, ``FacilityAdminRequiredMixin``) den
    Super-Admin am Zutritt zu Facility-Views hindern.
    """
    from core.models import User

    super_admin = User.objects.create_user(
        username="props_super",
        role=User.Role.SUPER_ADMIN,
        facility=None,
    )

    assert super_admin.is_super_admin is True
    assert super_admin.is_facility_admin is False
    # Schluessel-Invariante (Refs #867): super_admin ist NICHT in den
    # Lower-Properties.
    assert super_admin.is_lead_or_admin is False
    assert super_admin.is_staff_or_above is False
    assert super_admin.is_assistant_or_above is False


@pytest.mark.django_db
def test_legacy_is_admin_property_removed(admin_user):
    """Refs #867: Das alte ``is_admin``-Property existiert nach dem
    Rename auf ``is_facility_admin`` nicht mehr.

    Wir greifen direkt aufs Klassen-Dict zu (``hasattr`` allein wuerde
    durch Django-AbstractUser-Mechanismen u.U. False liefern), um
    sicherzustellen, dass kein altes Property im User-Model uebrig
    geblieben ist. Schutz gegen schleichende Re-Adds bei zukuenftigen
    Refactors.
    """
    from core.models import User

    # Es gibt kein ``is_admin`` als (custom) Property auf der User-Klasse.
    assert "is_admin" not in vars(User), "Property 'is_admin' soll seit Refs #867 entfernt sein."
    # Defensive Zweitprobe: Direkter Zugriff auf der Instanz darf das alte
    # Attribut nicht liefern. Falls Django ein Default-Attribut ``is_admin``
    # erfindet, soll dieser Test laut werden, weil dann RBAC-Logik wieder
    # geraten werden koennte.
    assert not hasattr(admin_user, "is_admin"), (
        "Instanz hat ein 'is_admin'-Attribut — unerwartet seit Refs #867. "
        "Bitte pruefen, ob ein Property/AbstractUser-Mixin reaktiviert wurde."
    )


@pytest.mark.django_db
def test_settings_singleton_per_facility(facility, organization):
    """OneToOne constraint raises IntegrityError when creating two Settings for same facility."""

    Settings.objects.create(facility=facility)
    with pytest.raises(IntegrityError):
        Settings.objects.create(facility=facility)


@pytest.mark.django_db
def test_settings_trigram_threshold_validator_rejects_out_of_range(facility):
    """Validator verhindert Werte außerhalb 0.0–1.0 (Refs #581)."""
    from django.core.exceptions import ValidationError

    s = Settings(facility=facility, search_trigram_threshold=-0.1)
    with pytest.raises(ValidationError):
        s.full_clean()

    s.search_trigram_threshold = 1.5
    with pytest.raises(ValidationError):
        s.full_clean()

    s.search_trigram_threshold = 0.5
    s.full_clean()  # must not raise


@pytest.mark.django_db
def test_settings_trigram_threshold_db_constraint(facility):
    """DB-CheckConstraint fängt Direct-Insert außerhalb 0.0–1.0 ab (Refs #581)."""
    with pytest.raises(IntegrityError):
        Settings.objects.create(facility=facility, search_trigram_threshold=-0.5)


@pytest.mark.django_db
def test_audit_log_ordering(facility, admin_user):
    """AuditLog entries are ordered by -timestamp (most recent first)."""
    log1 = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN,
    )
    log2 = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGOUT,
    )

    logs = list(AuditLog.objects.filter(id__in=[log1.id, log2.id]))
    # Most recently created (log2) should come first
    assert logs[0].id == log2.id
    assert logs[1].id == log1.id


@pytest.mark.django_db
def test_document_type_field_sort_order(doc_type_contact):
    """DocumentTypeField entries are ordered by sort_order ascending."""
    fields = list(doc_type_contact.fields.all())
    sort_orders = [f.sort_order for f in fields]
    assert sort_orders == sorted(sort_orders)
    assert sort_orders[0] == 0
    assert sort_orders[1] == 1


@pytest.mark.django_db
def test_event_history_update_raises_error(sample_event, staff_user):
    """Updating an existing EventHistory record raises ValueError."""
    history = EventHistory.objects.create(
        event=sample_event,
        changed_by=staff_user,
        action=EventHistory.Action.CREATE,
        data_after={"dauer": 15},
    )
    history.action = EventHistory.Action.UPDATE
    with pytest.raises(ValueError, match="append-only"):
        history.save()


@pytest.mark.django_db
def test_event_history_delete_raises_error(sample_event, staff_user):
    """Deleting an EventHistory record raises ValueError."""
    history = EventHistory.objects.create(
        event=sample_event,
        changed_by=staff_user,
        action=EventHistory.Action.CREATE,
        data_after={"dauer": 15},
    )
    with pytest.raises(ValueError, match="append-only"):
        history.delete()


@pytest.mark.django_db(transaction=True)
class TestEventHistoryDBTrigger:
    """DB-Trigger verhindert UPDATE und DELETE auf EventHistory."""

    def test_db_trigger_prevents_update(self, sample_event, admin_user):
        from django.db import connection

        history = EventHistory.objects.create(
            event=sample_event,
            changed_by=admin_user,
            action=EventHistory.Action.CREATE,
            data_after={"test": "data"},
        )
        with pytest.raises(Exception), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE core_eventhistory SET action = 'update' WHERE id = %s",
                [str(history.pk)],
            )

    def test_db_trigger_prevents_delete(self, sample_event, admin_user):
        from django.db import connection

        history = EventHistory.objects.create(
            event=sample_event,
            changed_by=admin_user,
            action=EventHistory.Action.CREATE,
            data_after={"test": "data"},
        )
        with pytest.raises(Exception), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM core_eventhistory WHERE id = %s",
                [str(history.pk)],
            )


@pytest.mark.django_db
def test_audit_log_create_succeeds(facility, admin_user):
    """Creating a new AuditLog entry succeeds."""
    entry = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN,
    )
    assert AuditLog.objects.filter(pk=entry.pk).exists()


@pytest.mark.django_db
def test_audit_log_update_raises_error(facility, admin_user):
    """Updating an existing AuditLog entry raises ValueError."""
    entry = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN,
    )
    entry.action = AuditLog.Action.LOGOUT
    with pytest.raises(ValueError, match="append-only"):
        entry.save()


@pytest.mark.django_db
def test_audit_log_delete_raises_error(facility, admin_user):
    """Deleting an AuditLog entry raises ValueError."""
    entry = AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN,
    )
    with pytest.raises(ValueError, match="append-only"):
        entry.delete()
