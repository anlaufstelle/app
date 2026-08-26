"""Mutation-Followup-Tests für ``core.services.offline`` — Serializer-Helfer.

Refs #930. Sub-File aus ``test_mutation_followup_offline``;
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
    User,
    WorkItem,
)
from core.services.compliance import SENSITIVITY_RANK
from core.services.system import (
    _serialize_case,
    _serialize_document_type,
    _serialize_event,
    _serialize_field_template,
    _serialize_workitem,
    build_facility_offline_bundle,
)

# ``_serialize_assignable_users`` ist (anders als die uebrigen Helfer) nicht
# ueber ``core.services.system`` re-exportiert — Direktimport aus dem Submodul,
# wie ``test_field_template_ranges`` es fuer ``_serialize_field_template`` tut.
from core.services.system.offline import _serialize_assignable_users
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
            # Refs #1109 (F-07): Optimistic-Lock-Token fürs Offline-Replay.
            "updated_at",
            "document_type_pk",
            "document_type_name",
            "created_by_display",
            "case_pk",
            "episode_pk",
            "is_anonymous",
            "data_fields",
            # Refs #1111: steuert die Edit-Affordanz im Offline-Viewer.
            "can_edit",
        }

    def test_can_edit_true_for_staff_on_foreign_event(
        self, facility, client_identified, doc_type_contact, staff_user, assistant_user
    ):
        """Refs #1111: Staff+ darf jedes sichtbare Event bearbeiten — auch
        ein von einem anderen Nutzer angelegtes."""
        event = _make_event(facility, client_identified, doc_type_contact, staff_user, created_by=assistant_user)
        out = _serialize_event(staff_user, event)
        assert out["can_edit"] is True

    def test_can_edit_true_for_assistant_on_own_event(
        self, facility, client_identified, doc_type_contact, assistant_user
    ):
        event = _make_event(facility, client_identified, doc_type_contact, assistant_user, created_by=assistant_user)
        out = _serialize_event(assistant_user, event)
        assert out["can_edit"] is True

    def test_can_edit_false_for_assistant_on_foreign_event(
        self, facility, client_identified, doc_type_contact, assistant_user, staff_user
    ):
        """Mutation des ``and``/``or`` im can_edit-Ausdruck: ein Assistant darf
        fremde Events NICHT bearbeiten (spiegelt ``EventUpdateView.dispatch``)."""
        event = _make_event(facility, client_identified, doc_type_contact, staff_user, created_by=staff_user)
        out = _serialize_event(assistant_user, event)
        assert out["can_edit"] is False


# ---------------------------------------------------------------------------
# _serialize_case — pro Feld
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSerializeCase:
    # Refs #1355: ``_serialize_case`` filtert ``description`` jetzt nach der
    # Rolle des Bundle-Nutzers (wie ``_serialize_event``s can_edit). Diese
    # Shape-Tests nutzen durchgehend ``staff_user`` (is_staff_or_above=True),
    # damit description nie weggefiltert wird und die reine Serialisierungs-
    # Form geprüft bleibt; das Gate selbst hat eigene Tests weiter unten.
    def test_pk_stringified(self, facility, client_identified, staff_user):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
        )
        out = _serialize_case(staff_user, c)
        assert out["pk"] == str(c.pk)

    def test_title_and_description_passthrough(self, facility, client_identified, staff_user):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Mein Fall",
            description="Beschreibung X",
            created_by=staff_user,
        )
        out = _serialize_case(staff_user, c)
        assert out["title"] == "Mein Fall"
        assert out["description"] == "Beschreibung X"

    def test_description_blanked_for_non_staff(self, facility, client_identified, assistant_user, staff_user):
        """Refs #1355: description ist STAFF_PLUS-only online (CaseDetailView,
        cases.py:70) — fuer Nicht-Staff liefert die Serialisierung einen
        leeren String, NICHT Key-Omission (Schema-Stabilitaet)."""
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            description="Sollte nicht offline zu Assistenz gelangen",
            created_by=staff_user,
        )
        out = _serialize_case(assistant_user, c)
        assert out["description"] == ""
        assert "description" in out

    def test_status_and_display_both_set(self, facility, client_identified, staff_user):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            status=Case.Status.CLOSED,
            closed_at=timezone.now(),
            created_by=staff_user,
        )
        out = _serialize_case(staff_user, c)
        assert out["status"] == "closed"
        assert out["status_display"] == "Geschlossen"

    def test_created_at_is_isoformat(self, facility, client_identified, staff_user):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
        )
        out = _serialize_case(staff_user, c)
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
        out = _serialize_case(staff_user, c)
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
        out = _serialize_case(staff_user, c)
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
        out = _serialize_case(staff_user, c)
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
        out = _serialize_case(staff_user, c)
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
        out = _serialize_case(staff_user, c)
        assert out["lead_user_display"] == "Lea Direktorin"

    def test_case_serialized_keys_complete(self, facility, client_identified, staff_user):
        c = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="t",
            created_by=staff_user,
        )
        out = _serialize_case(staff_user, c)
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
        out = _serialize_workitem(staff_user, wi)
        assert set(out.keys()) == {
            "pk",
            "title",
            "description",
            "status",
            "priority",
            "item_type",
            "due_date",
            # Refs #1398: Offline-Edit-Metadaten — Konflikt-Token +
            # editierbare WorkItemForm-Felder + Edit-Berechtigung.
            "updated_at",
            "remind_at",
            "recurrence",
            "assigned_to_pk",
            "can_edit",
        }

    def test_workitem_pk_stringified(self, facility, client_identified, staff_user):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="x",
        )
        assert _serialize_workitem(staff_user, wi)["pk"] == str(wi.pk)

    def test_workitem_title_and_description(self, facility, client_identified, staff_user):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="MyTitle",
            description="MyDesc",
        )
        out = _serialize_workitem(staff_user, wi)
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
        out = _serialize_workitem(staff_user, wi)
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
        out = _serialize_workitem(staff_user, wi)
        assert out["priority"] == "urgent"

    def test_workitem_item_type_passthrough(self, facility, client_identified, staff_user):
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="x",
            item_type=WorkItem.ItemType.HINT,
        )
        out = _serialize_workitem(staff_user, wi)
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
        out = _serialize_workitem(staff_user, wi)
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
        out = _serialize_workitem(staff_user, wi)
        assert out["due_date"] == when.isoformat()


# ---------------------------------------------------------------------------
# _serialize_document_type / _serialize_field_template
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSerializeDocumentType:
    # Refs #1111: ``_serialize_document_type`` filtert seine Felder jetzt nach
    # Feld-Sensitivity für den Bundle-Nutzer (wie ``_serialize_event``). Diese
    # Shape-Tests nutzen ``lead_user`` (sieht alle Sensitivities) damit nie ein
    # Feld weggefiltert wird und die reine Serialisierungs-Form geprüft bleibt.
    def test_dt_keys_complete(self, facility, lead_user):
        dt = _make_doc_type(facility, name="DT", icon="ico", color="red")
        out = _serialize_document_type(lead_user, dt)
        assert set(out.keys()) == {
            "pk",
            "name",
            # Refs #1397: Aktiv-Flag fuer den Offline-Create-Dropdown-Filter.
            "is_active",
            "sensitivity",
            # Refs #1518 (#1499): Mindest-Kontaktstufe fuer den Offline-Create-
            # Picker-Vorfilter (weicher Filter „ohne Person").
            "min_contact_stage",
            "icon",
            "color",
            "fields",
        }

    def test_dt_is_active_true_for_active_doctype(self, facility, lead_user):
        """Refs #1397: Der Offline-Create-Dropdown filtert per ``is_active``
        auf aktive Typen — ``_make_doc_type`` legt per Modell-Default (Refs
        ``DocumentType.is_active``) einen aktiven Typ an."""
        dt = _make_doc_type(facility, name="Active")
        assert _serialize_document_type(lead_user, dt)["is_active"] is True

    def test_dt_pk_stringified(self, facility, lead_user):
        dt = _make_doc_type(facility, name="X")
        assert _serialize_document_type(lead_user, dt)["pk"] == str(dt.pk)

    def test_dt_name(self, facility, lead_user):
        dt = _make_doc_type(facility, name="N")
        assert _serialize_document_type(lead_user, dt)["name"] == "N"

    def test_dt_sensitivity_passes_through_value_not_display(self, facility, lead_user):
        """Mutation ``doc_type.sensitivity`` → ``.get_sensitivity_display()``
        wuerde "Hoch" statt "high" liefern."""
        dt = _make_doc_type(facility, name="HD", sensitivity=DocumentType.Sensitivity.HIGH)
        out = _serialize_document_type(lead_user, dt)
        assert out["sensitivity"] == "high"

    def test_dt_icon_empty_default(self, facility, lead_user):
        """Mutation ``dt.icon or ""`` → ``dt.icon`` wuerde None oder ""
        durchreichen — wir prueffen explizit "" bei leerem icon."""
        dt = _make_doc_type(facility, name="NoIcon", icon="")
        assert _serialize_document_type(lead_user, dt)["icon"] == ""

    def test_dt_color_empty_default(self, facility, lead_user):
        dt = _make_doc_type(facility, name="NoColor", color="")
        assert _serialize_document_type(lead_user, dt)["color"] == ""

    def test_dt_icon_passthrough(self, facility, lead_user):
        dt = _make_doc_type(facility, name="Ico", icon="bi-bell")
        assert _serialize_document_type(lead_user, dt)["icon"] == "bi-bell"

    def test_dt_color_passthrough(self, facility, lead_user):
        dt = _make_doc_type(facility, name="Col", color="#abc")
        assert _serialize_document_type(lead_user, dt)["color"] == "#abc"

    def test_dt_fields_sorted_by_sort_order(self, facility, lead_user):
        """Mutation ``order_by("sort_order")`` → unsortiert wuerde Reihenfolge
        kippen, wenn man die Eintraege in umgekehrter Order erstellt."""
        dt = _make_doc_type(facility, name="Ord")
        ft_b = _make_field_template(facility, name="B")
        ft_a = _make_field_template(facility, name="A")
        # B mit sort_order=0 EINGETRAGEN, A mit sort_order=1 — erwartet:
        # Reihenfolge im Output ist [B, A].
        _attach(dt, ft_b, sort_order=0)
        _attach(dt, ft_a, sort_order=1)
        out = _serialize_document_type(lead_user, dt)
        slugs = [f["slug"] for f in out["fields"]]
        assert slugs == [ft_b.slug, ft_a.slug]

    def test_dt_fields_empty_list_when_no_fields(self, facility, lead_user):
        dt = _make_doc_type(facility, name="Empty")
        assert _serialize_document_type(lead_user, dt)["fields"] == []

    def test_dt_fields_filtered_by_user_sensitivity(self, facility, staff_user, assistant_user, lead_user, admin_user):
        """Refs #1111: Ein HIGH-Feld in einem NORMAL-Typ darf nur in der
        Feld-Liste auftauchen, wenn der Nutzer es sehen darf. Staff (max
        ELEVATED) bekommt es NICHT, Lead schon — gleiche Grenze wie die
        Wert-Filterung in ``_visible_data_fields``.

        Refs #1998 (Leak-Grenze): ``effective_sensitivity`` ist ``max(Typ,
        Feld)``. Das sind DREI unterscheidbare Faelle, und ein Test, der nur
        einen davon trifft, laesst die Grenze offen. Alle drei stehen deshalb
        hier zusammen:

        * **A — Feld-Override HOEHER als der Typ** (Refs #1111, bestand
          bereits). Pinnt das ZWEITE Argument.
        * **B — Feld OHNE eigenen Override, Typ HIGH.** Die Gate-Stufe kann
          nur noch aus ``doc_type.sensitivity`` kommen; faellt das erste
          Argument weg (oder wird das Gate wahr), verliesse das komplette
          Feldschema eines HIGH-Typs — Feldnamen, Hilfetexte,
          Auswahloptionen — den Server Richtung IndexedDB an Rollen, die den
          Typ online nicht einmal sehen duerfen.
        * **C — Feld-Override NIEDRIGER als der Typ** (Fix-Runde 1, vom
          adversarialen Verify nachgereicht). A und B pinnen nur, DASS beide
          Argumente ankommen, nicht WIE sie verrechnet werden: die Mutante
          ``user_can_see_field(user, field.sensitivity or doc_type.sensitivity)``
          — Override ERSETZT die Typ-Stufe statt ``max`` — ueberlebte A und B
          und die gesamte Offline-Batterie. Real leakbar: ein HIGH-Typ mit
          einem ausdruecklich auf "normal" gesetzten Feld liefe damit fuer
          STAFF/ASSISTANT auf Stufe NORMAL statt HIGH und gaebe genau das
          Feldschema heraus, das online verborgen ist. Ein Feld-Override kann
          eine Stufe nur ANHEBEN, nie senken.
        """
        # Richtung A (Refs #1111): Feld-Override HOEHER als der Typ.
        dt = _make_doc_type(facility, name="Gemischt", sensitivity=DocumentType.Sensitivity.NORMAL)
        ft_normal = _make_field_template(facility, name="Offen")
        ft_high = _make_field_template(facility, name="Vertraulich", sensitivity="high")
        _attach(dt, ft_normal, sort_order=0)
        _attach(dt, ft_high, sort_order=1)

        staff_slugs = {f["slug"] for f in _serialize_document_type(staff_user, dt)["fields"]}
        lead_slugs = {f["slug"] for f in _serialize_document_type(lead_user, dt)["fields"]}
        assert ft_normal.slug in staff_slugs
        assert ft_high.slug not in staff_slugs
        assert {ft_normal.slug, ft_high.slug} <= lead_slugs

        # Richtung B (Refs #1998): TYP-Stufe HIGH, Feld OHNE eigenen Override.
        dt_high = _make_doc_type(facility, name="Streng", sensitivity=DocumentType.Sensitivity.HIGH)
        ft_inherit = _make_field_template(facility, name="Geerbt")
        _attach(dt_high, ft_inherit, sort_order=0)
        # Vorbedingung explizit: das Feld traegt KEINE eigene Sensitivity, die
        # Gate-Stufe kann also nur aus ``doc_type.sensitivity`` kommen. Ohne
        # diese Zusicherung koennte der Test spaeter aus dem falschen Grund
        # gruen bleiben (Feld-Override statt Typ-Vererbung).
        assert ft_inherit.sensitivity == ""

        # Staff (max ELEVATED) und Assistenz (max NORMAL) sehen das Schema NICHT …
        assert _serialize_document_type(staff_user, dt_high)["fields"] == []
        assert _serialize_document_type(assistant_user, dt_high)["fields"] == []
        # … Lead (max HIGH) schon. Diese Zeile haelt den Test nicht-vakuum:
        # ohne sie waere ein „Feld gar nicht verknuepft"-Fehler ununterscheidbar
        # von der gepinnten Filterwirkung.
        assert [f["slug"] for f in _serialize_document_type(lead_user, dt_high)["fields"]] == [ft_inherit.slug]

        # Richtung C (Refs #1998, Fix-Runde 1): TYP-Stufe HIGH, Feld mit
        # ausdruecklich NIEDRIGEREM Override. ``max`` gewinnt, der Override
        # senkt NICHT — sonst waere „sensitivity=normal" am Feld ein
        # Generalschluessel fuer das Schema eines HIGH-Typs.
        dt_high_c = _make_doc_type(facility, name="Streng mit Ausnahme", sensitivity=DocumentType.Sensitivity.HIGH)
        ft_lower = _make_field_template(facility, name="Scheinbar harmlos", sensitivity=DocumentType.Sensitivity.NORMAL)
        _attach(dt_high_c, ft_lower, sort_order=0)
        # Vorbedingung: der Override ist WIRKLICH gesetzt und WIRKLICH
        # niedriger als die Typ-Stufe — nur dann unterscheidet sich dieser
        # Fall ueberhaupt von Richtung B.
        assert ft_lower.sensitivity == DocumentType.Sensitivity.NORMAL
        assert SENSITIVITY_RANK[ft_lower.sensitivity] < SENSITIVITY_RANK[dt_high_c.sensitivity]

        assert _serialize_document_type(staff_user, dt_high_c)["fields"] == []
        assert _serialize_document_type(assistant_user, dt_high_c)["fields"] == []
        # Beide HIGH-faehigen Rollen sehen es — haelt auch Richtung C nicht-vakuum.
        assert [f["slug"] for f in _serialize_document_type(lead_user, dt_high_c)["fields"]] == [ft_lower.slug]
        assert [f["slug"] for f in _serialize_document_type(admin_user, dt_high_c)["fields"]] == [ft_lower.slug]


# ---------------------------------------------------------------------------
# _serialize_assignable_users — Mandantengrenze des Staff-Rosters
# ---------------------------------------------------------------------------

#: Rollen, die ``_serialize_assignable_users`` als zuweisbar fuehrt. Bewusst
#: hier gespiegelt (nicht aus dem Service importiert): der Test soll auffallen,
#: wenn die Produktivliste stillschweigend waechst.
_ASSIGNABLE_ROLES = (
    User.Role.FACILITY_ADMIN,
    User.Role.LEAD,
    User.Role.STAFF,
    User.Role.ASSISTANT,
)


@pytest.mark.django_db
class TestSerializeAssignableUsers:
    """Refs #1998 (KRITISCH): ``assignable_users`` ist die einzige Liste im
    Offline-Bundle, die *Personal* statt Klientel traegt — und damit die
    Stelle, an der ein fehlender ``facility``-Filter Klarnamen fremder
    Einrichtungen auf ein Endgeraet schreibt, wo sie bis zum TTL-Ablauf
    liegen bleiben. Die Mandantengrenze war bislang nur implizit gepinnt
    (alle bestehenden Tests legen ihre Nutzer:innen in EINER Facility an),
    ein entfernter Filter waere also gruen durchgelaufen."""

    def _make_foreign_users(self, second_facility) -> list[User]:
        """Je Rolle ein AKTIVES Konto in der zweiten Einrichtung."""
        return [
            User.objects.create_user(
                username=f"fremd-{role}",
                role=role,
                facility=second_facility,
                is_active=True,
            )
            for role in _ASSIGNABLE_ROLES
        ]

    def test_roster_excludes_active_assignable_users_of_other_facility(
        self, facility, second_facility, staff_user, lead_user
    ):
        foreign = self._make_foreign_users(second_facility)

        # Vorbedingung 1: Die Fremd-Konten sind AKTIV und in zuweisbarer
        # Rolle — sie wuerden also nicht schon vom ``is_active``- oder
        # ``role__in``-Filter abgeraeumt. Ohne diese Zusicherung koennte der
        # Test aus dem falschen Grund gruen sein.
        assert all(u.is_active for u in foreign)
        assert {u.role for u in foreign} == set(_ASSIGNABLE_ROLES)

        # Vorbedingung 2 (Selbstschutz gegen einen vakuum-gruenen Test):
        # ``user`` traegt KEINE RLS-Policy (nur facility-gebundene Fachtabellen
        # haben eine). Ohne den ``facility=facility``-Filter im Service waeren
        # die Fremd-Konten also tatsaechlich sichtbar — der Ausschluss unten
        # misst den Filter und nicht eine zweite, unsichtbare Schranke.
        reachable_without_facility_filter = set(
            User.objects.filter(is_active=True, role__in=list(_ASSIGNABLE_ROLES)).values_list("pk", flat=True)
        )
        assert {u.pk for u in foreign} <= reachable_without_facility_filter

        pks = {row["pk"] for row in _serialize_assignable_users(facility)}

        for u in foreign:
            assert str(u.pk) not in pks, f"Cross-Tenant-Leak: {u.username} ({u.role}) im Roster von {facility}"
        # Exakte Menge statt blosser Nicht-Enthalten-Pruefung: so faellt auch
        # ein Filter auf, der die Grenze nur teilweise zieht (etwa ueber die
        # Organisation statt die Einrichtung).
        assert pks == {str(staff_user.pk), str(lead_user.pk)}

    def test_facility_bundle_roster_excludes_other_facility(self, facility, second_facility, staff_user, lead_user):
        """Dieselbe Grenze end-to-end durch den Bundle-Bauer — der Helfer ist
        privat, das Bundle ist das, was den Server verlaesst."""
        foreign = self._make_foreign_users(second_facility)

        bundle = build_facility_offline_bundle(staff_user, facility)
        pks = {row["pk"] for row in bundle["assignable_users"]}

        assert pks, "Roster darf fuer Staff+ nicht leer sein (sonst pinnt der Test nichts)"
        for u in foreign:
            assert str(u.pk) not in pks, f"Cross-Tenant-Leak im Facility-Bundle: {u.username}"
        assert pks == {str(staff_user.pk), str(lead_user.pk)}


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
        is_required: bool = False,
        min_value: int | None = None,
        max_value: int | None = None,
        help_text: str = "",
        choices=None,
    ):
        from types import SimpleNamespace

        return SimpleNamespace(
            slug=slug,
            name=name,
            field_type=field_type,
            sensitivity=sensitivity,
            is_encrypted=is_encrypted,
            is_required=is_required,
            min_value=min_value,
            max_value=max_value,
            help_text=help_text,
            # ``FieldTemplate.choices`` ist eine Property, die (value, label)-
            # Tupel der aktiven Optionen liefert — hier als Attribut gestubbt.
            choices=choices or [],
        )

    def test_keys_complete(self):
        out = _serialize_field_template(self._make_ft_stub())
        assert set(out.keys()) == {
            "slug",
            "name",
            "field_type",
            "sensitivity",
            "is_encrypted",
            # Refs #1111: Render-Metadaten fürs Offline-Edit-Formular.
            "is_required",
            # Refs #1914: Offline-Zahlenfelder tragen dieselben Grenzen.
            "min_value",
            "max_value",
            "help_text",
            "options",
        }

    def test_is_required_passthrough_true(self):
        out = _serialize_field_template(self._make_ft_stub(is_required=True))
        assert out["is_required"] is True

    def test_is_required_passthrough_false(self):
        out = _serialize_field_template(self._make_ft_stub(is_required=False))
        assert out["is_required"] is False

    def test_help_text_passthrough(self):
        out = _serialize_field_template(self._make_ft_stub(help_text="Hilfe"))
        assert out["help_text"] == "Hilfe"

    def test_help_text_none_becomes_empty_string(self):
        """Mutation ``field_template.help_text or ""`` → ``.help_text`` wuerde
        None durchreichen und im JSON ``null`` erzeugen."""
        out = _serialize_field_template(self._make_ft_stub(help_text=None))
        assert out["help_text"] == ""

    def test_options_empty_when_no_choices(self):
        out = _serialize_field_template(self._make_ft_stub(choices=[]))
        assert out["options"] == []

    def test_options_mapped_to_value_label_dicts(self):
        """Die Optionen werden als ``{"value", "label"}`` serialisiert, damit
        der Offline-Viewer SELECT/MULTI_SELECT rendern kann."""
        out = _serialize_field_template(self._make_ft_stub(choices=[("gut", "Gut"), ("schlecht", "Schlecht")]))
        assert out["options"] == [
            {"value": "gut", "label": "Gut"},
            {"value": "schlecht", "label": "Schlecht"},
        ]

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
