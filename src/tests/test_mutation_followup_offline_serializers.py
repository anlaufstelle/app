"""Mutation-Followup-Tests für ``core.services.offline`` — Serializer-Helfer.

Refs Welle 7 (#930). Sub-File aus ``test_mutation_followup_offline``;
enthält die Test-Klassen für die einzelnen Serializer-Helfer
(``_serialize_event``, ``_serialize_case``, ``_serialize_workitem``,
``_serialize_document_type``, ``_serialize_field_template``). Pro Feld
ein Test — Mutmut entfernt typischerweise einzelne Key/Value-Paare.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.models import (
    Case,
    DocumentType,
    Episode,
    Event,
    WorkItem,
)
from core.services.system import (
    _serialize_case,
    _serialize_document_type,
    _serialize_event,
    _serialize_field_template,
    _serialize_workitem,
)
from tests._mutation_followup_offline_helpers import (
    _attach,
    _make_doc_type,
    _make_event,
    _make_field_template,
)

# ---------------------------------------------------------------------------
# _serialize_event — Felder einzeln verifizieren (Mutmut killt einzelne Keys)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSerializeEvent:
    """Pro Feld ein Test — Mutmut entfernt typischerweise einzelne Key/Value-
    Paare und das fällt nur auf, wenn jedes Feld einzeln verifiziert ist."""

    def test_pk_field_is_stringified(self, facility, client_identified, doc_type_contact, staff_user):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert out["pk"] == str(event.pk)

    def test_occurred_at_field_is_isoformat(self, facility, client_identified, doc_type_contact, staff_user):
        when = timezone.now()
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=when,
        )
        out = _serialize_event(staff_user, event)
        # Mutation ``isoformat()`` → ``__str__()`` oder Removal würde failen.
        assert out["occurred_at"] == when.isoformat()

    def test_document_type_pk_is_stringified(self, facility, client_identified, doc_type_contact, staff_user):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert out["document_type_pk"] == str(doc_type_contact.pk)

    def test_document_type_name_is_set(self, facility, client_identified, doc_type_contact, staff_user):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert out["document_type_name"] == doc_type_contact.name

    def test_created_by_display_uses_full_name_when_set(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        staff_user.first_name = "Max"
        staff_user.last_name = "Muster"
        staff_user.save()
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert out["created_by_display"] == "Max Muster"

    def test_created_by_display_falls_back_to_username(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation ``or event.created_by.username`` → leerer String würde failen.

        ``staff_user`` hat per Fixture keinen full_name → Fallback greift.
        """
        assert staff_user.get_full_name() == ""  # Vorbedingung
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert out["created_by_display"] == staff_user.username

    def test_created_by_display_empty_when_user_none(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation des ``if event.created_by else ""``-Fallbacks: None-User
        muss leeren String liefern, nicht AttributeError."""
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            created_by=staff_user,
        )
        # Setze created_by nach Erstellung auf None (Direct-Update damit save()
        # nicht reset).
        Event.objects.filter(pk=event.pk).update(created_by=None)
        event.refresh_from_db()
        out = _serialize_event(staff_user, event)
        assert out["created_by_display"] == ""

    def test_case_pk_is_none_when_no_case(self, facility, client_identified, doc_type_contact, staff_user):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        # Mutation ``None`` → ``""`` würde diese Variante killen.
        assert out["case_pk"] is None

    def test_case_pk_set_when_event_has_case(self, facility, client_identified, doc_type_contact, staff_user):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Case",
            created_by=staff_user,
        )
        event = _make_event(facility, client_identified, doc_type_contact, staff_user, case=case)
        out = _serialize_event(staff_user, event)
        assert out["case_pk"] == str(case.pk)

    def test_episode_pk_is_none_when_no_episode(self, facility, client_identified, doc_type_contact, staff_user):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert out["episode_pk"] is None

    def test_episode_pk_set_when_event_has_episode(self, facility, client_identified, doc_type_contact, staff_user):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="C",
            created_by=staff_user,
        )
        episode = Episode.objects.create(
            case=case,
            title="Ep",
            started_at=timezone.now().date(),
            created_by=staff_user,
        )
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            case=case,
            episode=episode,
        )
        out = _serialize_event(staff_user, event)
        assert out["episode_pk"] == str(episode.pk)

    def test_is_anonymous_passes_through_true(self, facility, client_identified, doc_type_contact, staff_user):
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            is_anonymous=True,
        )
        out = _serialize_event(staff_user, event)
        assert out["is_anonymous"] is True

    def test_is_anonymous_default_false(self, facility, client_identified, doc_type_contact, staff_user):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert out["is_anonymous"] is False

    def test_data_fields_key_exists_even_for_empty_data(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        # data_fields immer dict — Mutation ``[]`` → ``None`` würde scheitern.
        assert out["data_fields"] == {}
        assert isinstance(out["data_fields"], dict)

    def test_serialized_keys_complete(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation eines Key-Strings (``case_pk`` → ``casepk``) wird durch
        Vergleich der erwarteten Schluesselmenge gefangen."""
        event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        out = _serialize_event(staff_user, event)
        assert set(out.keys()) == {
            "pk",
            "occurred_at",
            "document_type_pk",
            "document_type_name",
            "created_by_display",
            "case_pk",
            "episode_pk",
            "is_anonymous",
            "data_fields",
        }


# ---------------------------------------------------------------------------
# _serialize_case — pro Feld
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSerializeCase:
    def test_pk_stringified(self, facility, client_identified, staff_user):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
        )
        out = _serialize_case(c)
        assert out["pk"] == str(c.pk)

    def test_title_and_description_passthrough(self, facility, client_identified, staff_user):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Mein Fall",
            description="Beschreibung X",
            created_by=staff_user,
        )
        out = _serialize_case(c)
        assert out["title"] == "Mein Fall"
        assert out["description"] == "Beschreibung X"

    def test_status_and_display_both_set(self, facility, client_identified, staff_user):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            status=Case.Status.CLOSED,
            closed_at=timezone.now(),
            created_by=staff_user,
        )
        out = _serialize_case(c)
        assert out["status"] == "closed"
        assert out["status_display"] == "Geschlossen"

    def test_created_at_is_isoformat(self, facility, client_identified, staff_user):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
        )
        out = _serialize_case(c)
        assert out["created_at"] == c.created_at.isoformat()

    def test_closed_at_none_for_open_case(self, facility, client_identified, staff_user):
        """Mutation ``case.closed_at.isoformat() if case.closed_at else None``
        → leerer String wuerde failen."""
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        out = _serialize_case(c)
        assert out["closed_at"] is None

    def test_closed_at_isoformat_when_set(self, facility, client_identified, staff_user):
        when = timezone.now()
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            status=Case.Status.CLOSED,
            closed_at=when,
            created_by=staff_user,
        )
        out = _serialize_case(c)
        # closed_at wird in DB ggf. mit µs-Aufloesung gespeichert → über das
        # Modell lesen statt direkt ``when.isoformat()`` vergleichen.
        c.refresh_from_db()
        assert out["closed_at"] == c.closed_at.isoformat()

    def test_lead_user_display_empty_when_none(self, facility, client_identified, staff_user):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
            lead_user=None,
        )
        out = _serialize_case(c)
        assert out["lead_user_display"] == ""

    def test_lead_user_display_falls_back_to_username(self, facility, client_identified, staff_user, lead_user):
        """Mutation ``or lead_user.username``-Fallback: Lead ohne full_name
        muss username liefern."""
        assert lead_user.get_full_name() == ""
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
            lead_user=lead_user,
        )
        out = _serialize_case(c)
        assert out["lead_user_display"] == lead_user.username

    def test_lead_user_display_uses_full_name(self, facility, client_identified, staff_user, lead_user):
        lead_user.first_name = "Lea"
        lead_user.last_name = "Direktorin"
        lead_user.save()
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
            lead_user=lead_user,
        )
        out = _serialize_case(c)
        assert out["lead_user_display"] == "Lea Direktorin"

    def test_case_serialized_keys_complete(self, facility, client_identified, staff_user):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
        )
        out = _serialize_case(c)
        assert set(out.keys()) == {
            "pk",
            "title",
            "description",
            "status",
            "status_display",
            "created_at",
            "closed_at",
            "lead_user_display",
        }


# ---------------------------------------------------------------------------
# _serialize_workitem — pro Feld
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSerializeWorkitem:
    def test_workitem_all_keys_present(self, facility, client_identified, staff_user):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.IMPORTANT,
            title="T",
            description="D",
        )
        out = _serialize_workitem(wi)
        assert set(out.keys()) == {
            "pk",
            "title",
            "description",
            "status",
            "priority",
            "item_type",
            "due_date",
        }

    def test_workitem_pk_stringified(self, facility, client_identified, staff_user):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="x",
        )
        assert _serialize_workitem(wi)["pk"] == str(wi.pk)

    def test_workitem_title_and_description(self, facility, client_identified, staff_user):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="MyTitle",
            description="MyDesc",
        )
        out = _serialize_workitem(wi)
        assert out["title"] == "MyTitle"
        assert out["description"] == "MyDesc"

    def test_workitem_status_passthrough(self, facility, client_identified, staff_user):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="x",
            status=WorkItem.Status.IN_PROGRESS,
        )
        out = _serialize_workitem(wi)
        # Mutation ``workitem.status`` → ``workitem.get_status_display()``
        # würde "In Bearbeitung" liefern.
        assert out["status"] == "in_progress"

    def test_workitem_priority_passthrough(self, facility, client_identified, staff_user):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="x",
            priority=WorkItem.Priority.URGENT,
        )
        out = _serialize_workitem(wi)
        assert out["priority"] == "urgent"

    def test_workitem_item_type_passthrough(self, facility, client_identified, staff_user):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="x",
            item_type=WorkItem.ItemType.HINT,
        )
        out = _serialize_workitem(wi)
        assert out["item_type"] == "hint"

    def test_workitem_due_date_none(self, facility, client_identified, staff_user):
        """Mutation ``workitem.due_date.isoformat() if workitem.due_date else None``
        → leerer String würde failen."""
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="x",
            due_date=None,
        )
        out = _serialize_workitem(wi)
        assert out["due_date"] is None

    def test_workitem_due_date_isoformat_when_set(self, facility, client_identified, staff_user):
        when = timezone.now().date()
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="x",
            due_date=when,
        )
        out = _serialize_workitem(wi)
        assert out["due_date"] == when.isoformat()


# ---------------------------------------------------------------------------
# _serialize_document_type / _serialize_field_template
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSerializeDocumentType:
    def test_dt_keys_complete(self, facility):
        dt = _make_doc_type(facility, name="DT", icon="ico", color="red")
        out = _serialize_document_type(dt)
        assert set(out.keys()) == {
            "pk",
            "name",
            "category",
            "sensitivity",
            "icon",
            "color",
            "fields",
        }

    def test_dt_pk_stringified(self, facility):
        dt = _make_doc_type(facility, name="X")
        assert _serialize_document_type(dt)["pk"] == str(dt.pk)

    def test_dt_name_and_category(self, facility):
        dt = _make_doc_type(facility, name="N")
        out = _serialize_document_type(dt)
        assert out["name"] == "N"
        assert out["category"] == "contact"

    def test_dt_sensitivity_passes_through_value_not_display(self, facility):
        """Mutation ``doc_type.sensitivity`` → ``.get_sensitivity_display()``
        wuerde "Hoch" statt "high" liefern."""
        dt = _make_doc_type(facility, name="HD", sensitivity=DocumentType.Sensitivity.HIGH)
        out = _serialize_document_type(dt)
        assert out["sensitivity"] == "high"

    def test_dt_icon_empty_default(self, facility):
        """Mutation ``dt.icon or ""`` → ``dt.icon`` wuerde None oder ""
        durchreichen — wir prueffen explizit "" bei leerem icon."""
        dt = _make_doc_type(facility, name="NoIcon", icon="")
        assert _serialize_document_type(dt)["icon"] == ""

    def test_dt_color_empty_default(self, facility):
        dt = _make_doc_type(facility, name="NoColor", color="")
        assert _serialize_document_type(dt)["color"] == ""

    def test_dt_icon_passthrough(self, facility):
        dt = _make_doc_type(facility, name="Ico", icon="bi-bell")
        assert _serialize_document_type(dt)["icon"] == "bi-bell"

    def test_dt_color_passthrough(self, facility):
        dt = _make_doc_type(facility, name="Col", color="#abc")
        assert _serialize_document_type(dt)["color"] == "#abc"

    def test_dt_fields_sorted_by_sort_order(self, facility):
        """Mutation ``order_by("sort_order")`` → unsortiert wuerde Reihenfolge
        kippen, wenn man die Eintraege in umgekehrter Order erstellt."""
        dt = _make_doc_type(facility, name="Ord")
        ft_b = _make_field_template(facility, name="B")
        ft_a = _make_field_template(facility, name="A")
        # B mit sort_order=0 EINGETRAGEN, A mit sort_order=1 — erwartet:
        # Reihenfolge im Output ist [B, A].
        _attach(dt, ft_b, sort_order=0)
        _attach(dt, ft_a, sort_order=1)
        out = _serialize_document_type(dt)
        slugs = [f["slug"] for f in out["fields"]]
        assert slugs == [ft_b.slug, ft_a.slug]

    def test_dt_fields_empty_list_when_no_fields(self, facility):
        dt = _make_doc_type(facility, name="Empty")
        assert _serialize_document_type(dt)["fields"] == []


class TestSerializeFieldTemplate:
    """``_serialize_field_template`` ist DB-frei testbar via SimpleNamespace."""

    def _make_ft_stub(
        self,
        *,
        slug: str = "s",
        name: str = "N",
        field_type: str = "text",
        sensitivity: str = "",
        is_encrypted: bool = False,
    ):
        from types import SimpleNamespace

        return SimpleNamespace(
            slug=slug,
            name=name,
            field_type=field_type,
            sensitivity=sensitivity,
            is_encrypted=is_encrypted,
        )

    def test_keys_complete(self):
        out = _serialize_field_template(self._make_ft_stub())
        assert set(out.keys()) == {
            "slug",
            "name",
            "field_type",
            "sensitivity",
            "is_encrypted",
        }

    def test_sensitivity_none_becomes_empty_string(self):
        """Mutation ``field_template.sensitivity or ""`` → ``.sensitivity``
        wuerde None durchreichen."""
        out = _serialize_field_template(self._make_ft_stub(sensitivity=None))
        assert out["sensitivity"] == ""

    def test_sensitivity_passthrough_when_set(self):
        out = _serialize_field_template(self._make_ft_stub(sensitivity="high"))
        assert out["sensitivity"] == "high"

    def test_is_encrypted_passthrough_true(self):
        out = _serialize_field_template(self._make_ft_stub(is_encrypted=True))
        assert out["is_encrypted"] is True

    def test_is_encrypted_passthrough_false(self):
        out = _serialize_field_template(self._make_ft_stub(is_encrypted=False))
        assert out["is_encrypted"] is False

    def test_name_and_slug_and_field_type(self):
        out = _serialize_field_template(self._make_ft_stub(slug="my-slug", name="My", field_type="number"))
        assert out["slug"] == "my-slug"
        assert out["name"] == "My"
        assert out["field_type"] == "number"
