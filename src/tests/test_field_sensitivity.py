"""Tests für rollenbasierte Feldsensitivität in Event-Views (Issue #113)."""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Case, DocumentType, DocumentTypeField, Event, FieldTemplate, User


@pytest.fixture
def doc_type_high(facility):
    """DocumentType mit HIGH-Sensibilität und einem verschlüsselten Feld."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        sensitivity=DocumentType.Sensitivity.HIGH,
        name="Hochsensibel",
    )
    ft_secret = FieldTemplate.objects.create(
        facility=facility,
        name="Geheimfeld",
        field_type=FieldTemplate.FieldType.TEXT,
        is_encrypted=True,
        sensitivity="high",
    )
    ft_normal = FieldTemplate.objects.create(
        facility=facility,
        name="Normalfeld",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_secret, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_normal, sort_order=1)
    return doc_type


@pytest.fixture
def doc_type_elevated(facility):
    """DocumentType mit ELEVATED-Sensibilität."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        name="ErhoehtSensibel",
    )
    ft = FieldTemplate.objects.create(
        facility=facility,
        name="ErhoehtFeld",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)
    return doc_type


@pytest.fixture
def event_high(facility, doc_type_high, staff_user):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type_high,
        occurred_at=timezone.now(),
        data_json={"geheimfeld": "secret-value", "normalfeld": "normal-value"},
        created_by=staff_user,
    )


@pytest.fixture
def event_elevated(facility, doc_type_elevated, staff_user):
    return Event.objects.create(
        facility=facility,
        document_type=doc_type_elevated,
        occurred_at=timezone.now(),
        data_json={"erhoehtfeld": "elevated-value"},
        created_by=staff_user,
    )


@pytest.mark.django_db
class TestEventDetailFieldSensitivity:
    """EventDetailView zeigt Felder rollenabhängig an."""

    def test_admin_sees_all_fields(self, client, admin_user, event_high):
        client.force_login(admin_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_high.pk}))
        content = response.content.decode()
        assert "secret-value" in content
        assert "normal-value" in content
        assert "[Eingeschränkt]" not in content

    def test_lead_sees_all_fields(self, client, lead_user, event_high):
        client.force_login(lead_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_high.pk}))
        content = response.content.decode()
        assert "secret-value" in content
        assert "normal-value" in content

    def test_staff_cannot_see_high_sensitivity(self, client, staff_user, event_high):
        """Staff gets 404 for a HIGH-sensitivity event — existence must not leak."""
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_high.pk}))
        assert response.status_code == 404

    def test_staff_can_see_elevated(self, client, staff_user, event_elevated):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_elevated.pk}))
        content = response.content.decode()
        assert "elevated-value" in content
        assert "[Eingeschränkt]" not in content

    def test_assistant_cannot_see_elevated(self, client, assistant_user, event_elevated):
        """Assistant gets 404 for an ELEVATED event — existence must not leak."""
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event_elevated.pk}))
        assert response.status_code == 404

    def test_assistant_can_see_normal(self, client, assistant_user, sample_event):
        """sample_event has NORMAL sensitivity doc type."""
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": sample_event.pk}))
        content = response.content.decode()
        assert "Testnotiz" in content
        assert "[Eingeschränkt]" not in content

    def test_encrypted_field_restricted_for_staff(self, client, staff_user, facility):
        """Even on NORMAL doc type, an encrypted field is HIGH → hidden for staff."""
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="NormalMitEncrypted",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        ft_enc = FieldTemplate.objects.create(
            facility=facility,
            name="EncField",
            field_type=FieldTemplate.FieldType.TEXT,
            is_encrypted=True,
            sensitivity="high",
        )
        ft_plain = FieldTemplate.objects.create(
            facility=facility,
            name="PlainField",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_enc, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_plain, sort_order=1)
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"encfield": "enc-val", "plainfield": "plain-val"},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
        content = response.content.decode()
        assert "plain-val" in content
        assert "enc-val" not in content
        assert "[Eingeschränkt]" in content

    def test_encrypted_without_sensitivity_visible_for_staff(self, client, staff_user, facility):
        """Encrypted field WITHOUT sensitivity override → staff CAN see it.

        Proves that is_encrypted alone no longer restricts visibility.
        """
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="NormalEncNoSens",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        ft_enc = FieldTemplate.objects.create(
            facility=facility,
            name="EncNoRestrict",
            field_type=FieldTemplate.FieldType.TEXT,
            is_encrypted=True,
            # sensitivity="" (default) — keine Sichtbarkeitsbeschränkung
        )
        ft_plain = FieldTemplate.objects.create(
            facility=facility,
            name="PlainNoRestrict",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_enc, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_plain, sort_order=1)
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"encnorestrict": "enc-visible", "plainnorestrict": "plain-visible"},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
        content = response.content.decode()
        # Staff sees both field labels — is_encrypted alone doesn't restrict visibility
        assert "PlainNoRestrict" in content
        assert "EncNoRestrict" in content
        assert "[Eingeschränkt]" not in content

    def test_sensitivity_high_without_encrypted_restricted_for_staff(self, client, staff_user, facility):
        """Non-encrypted field WITH sensitivity='high' → staff CANNOT see it.

        Proves that sensitivity alone controls visibility, independent of encryption.
        """
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="NormalMitHighField",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        ft_high = FieldTemplate.objects.create(
            facility=facility,
            name="HighNoEncrypt",
            field_type=FieldTemplate.FieldType.TEXT,
            is_encrypted=False,
            sensitivity="high",
        )
        ft_plain = FieldTemplate.objects.create(
            facility=facility,
            name="PlainControl",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_high, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_plain, sort_order=1)
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"highnoencrypt": "secret-plain-val", "plaincontrol": "visible-val"},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
        content = response.content.decode()
        assert "visible-val" in content  # plain field visible
        assert "secret-plain-val" not in content  # high-sensitivity field hidden
        assert "[Eingeschränkt]" in content


@pytest.mark.django_db
class TestEventUpdateFieldSensitivity:
    """EventUpdateView blendet sensitive Felder im Formular aus."""

    def test_admin_sees_all_form_fields(self, client, admin_user, event_high):
        client.force_login(admin_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": event_high.pk}))
        content = response.content.decode()
        assert "Geheimfeld" in content
        assert "Normalfeld" in content

    def test_staff_update_high_sensitivity_returns_404(self, client, staff_user, event_high):
        """Staff is blocked from the edit route for a HIGH event — 404, not empty form."""
        client.force_login(staff_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": event_high.pk}))
        assert response.status_code == 404

    def test_assistant_update_elevated_returns_404(self, client, assistant_user, event_elevated):
        """Assistant is blocked from the edit route for an ELEVATED event — 404."""
        event_elevated.created_by = assistant_user
        event_elevated.save()
        client.force_login(assistant_user)
        response = client.get(reverse("core:event_update", kwargs={"pk": event_elevated.pk}))
        assert response.status_code == 404

    def test_staff_post_on_high_event_returns_404_and_preserves_data(self, client, staff_user, event_high):
        """Staff POST to the edit route of a HIGH event is 404 and leaves data untouched."""
        original_data = event_high.data_json.copy()
        client.force_login(staff_user)
        response = client.post(
            reverse("core:event_update", kwargs={"pk": event_high.pk}),
            {},
        )
        assert response.status_code == 404
        event_high.refresh_from_db()
        assert event_high.data_json == original_data


@pytest.mark.django_db
class TestEventVisibleToManager:
    """Event.objects.visible_to(user) is the central manager method that
    filters events by the user's role-based sensitivity rank. (Issue #522)
    """

    def test_admin_sees_all_events(self, admin_user, event_high, event_elevated, sample_event):
        ids = set(Event.objects.visible_to(admin_user).values_list("pk", flat=True))
        assert event_high.pk in ids
        assert event_elevated.pk in ids
        assert sample_event.pk in ids

    def test_lead_sees_all_events(self, lead_user, event_high, event_elevated, sample_event):
        ids = set(Event.objects.visible_to(lead_user).values_list("pk", flat=True))
        assert event_high.pk in ids
        assert event_elevated.pk in ids
        assert sample_event.pk in ids

    def test_staff_does_not_see_high_events(self, staff_user, event_high, event_elevated, sample_event):
        ids = set(Event.objects.visible_to(staff_user).values_list("pk", flat=True))
        assert event_high.pk not in ids
        assert event_elevated.pk in ids
        assert sample_event.pk in ids

    def test_assistant_does_not_see_elevated_or_high(self, assistant_user, event_high, event_elevated, sample_event):
        ids = set(Event.objects.visible_to(assistant_user).values_list("pk", flat=True))
        assert event_high.pk not in ids
        assert event_elevated.pk not in ids
        assert sample_event.pk in ids

    def test_chainable_with_filter(self, staff_user, event_elevated):
        """visible_to() returns a chainable QuerySet (not a list)."""
        qs = Event.objects.visible_to(staff_user).filter(pk=event_elevated.pk)
        assert qs.count() == 1


@pytest.mark.django_db
class TestClientDetailHidesEventsBySensitivity:
    """ClientDetailView event timeline must filter by sensitivity (#522)."""

    def test_assistant_does_not_see_high_event_in_client_detail(
        self, client, assistant_user, facility, doc_type_high, client_identified, staff_user
    ):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(assistant_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        content = response.content.decode()
        assert "Hochsensibel" not in content

    def test_staff_sees_high_event_in_client_detail(
        self, client, lead_user, facility, doc_type_high, client_identified, staff_user
    ):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(lead_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        content = response.content.decode()
        assert "Hochsensibel" in content


@pytest.mark.django_db
class TestCaseDetailHidesEventsBySensitivity:
    """Case detail event list must filter by sensitivity (#522).

    CaseDetailView is StaffRequiredMixin, so the leak is between staff (max
    ELEVATED) and lead/admin (max HIGH). A staff user must not see HIGH events
    in the case timeline.
    """

    def test_staff_does_not_see_high_event_in_case_detail(
        self, client, staff_user, facility, doc_type_high, client_identified
    ):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Testfall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            case=case,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:case_detail", kwargs={"pk": case.pk}))
        content = response.content.decode()
        assert "Hochsensibel" not in content

    def test_lead_sees_high_event_in_case_detail(
        self, client, lead_user, facility, doc_type_high, client_identified, staff_user
    ):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Testfall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            case=case,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(lead_user)
        response = client.get(reverse("core:case_detail", kwargs={"pk": case.pk}))
        content = response.content.decode()
        assert "Hochsensibel" in content


@pytest.mark.django_db
class TestZeitstromHidesEventsBySensitivity:
    """Zeitstrom feed must filter events by sensitivity (#522)."""

    def test_assistant_does_not_see_elevated_event_in_zeitstrom(
        self, client, assistant_user, facility, doc_type_elevated, client_identified, staff_user
    ):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_elevated,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(assistant_user)
        response = client.get(reverse("core:zeitstrom"))
        content = response.content.decode()
        assert "ErhoehtSensibel" not in content


@pytest.mark.django_db
class TestHandoverHidesEventsBySensitivity:
    """Handover summary highlights and stats must filter by sensitivity (#522)."""

    def test_handover_summary_hides_high_events_for_staff(self, facility, staff_user, doc_type_high, client_identified):
        from core.services.case import build_handover_summary

        # Mark the doc type as system_type='crisis' so it shows up in highlights
        doc_type_high.system_type = "crisis"
        doc_type_high.save()
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        summary = build_handover_summary(facility, timezone.localdate(), None, staff_user)
        crisis_highlights = [h for h in summary["highlights"] if h["type"] == "crisis"]
        assert len(crisis_highlights) == 0
        assert summary["stats"]["events_total"] == 0

    def test_handover_summary_shows_high_events_for_lead(
        self, facility, lead_user, staff_user, doc_type_high, client_identified
    ):
        from core.services.case import build_handover_summary

        doc_type_high.system_type = "crisis"
        doc_type_high.save()
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_high,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        summary = build_handover_summary(facility, timezone.localdate(), None, lead_user)
        crisis_highlights = [h for h in summary["highlights"] if h["type"] == "crisis"]
        assert len(crisis_highlights) == 1
        assert summary["stats"]["events_total"] == 1


@pytest.mark.django_db
class TestEventVisibilityService:
    @pytest.mark.parametrize(
        ("user_fixture", "sensitivity", "expected"),
        [
            ("assistant_user", DocumentType.Sensitivity.NORMAL, True),
            ("staff_user", DocumentType.Sensitivity.ELEVATED, True),
            ("lead_user", DocumentType.Sensitivity.HIGH, True),
            ("admin_user", DocumentType.Sensitivity.HIGH, True),
            ("assistant_user", DocumentType.Sensitivity.ELEVATED, False),
        ],
        ids=[
            "assistant-normal-boundary",
            "staff-elevated-boundary",
            "lead-high-boundary",
            "facility-admin-high-boundary",
            "assistant-elevated-counterexample",
        ],
    )
    def test_event_and_document_type_visibility_at_role_boundary(
        self,
        request,
        facility,
        staff_user,
        user_fixture,
        sensitivity,
        expected,
    ):
        from core.services.compliance import user_can_see_document_type, user_can_see_event

        user = request.getfixturevalue(user_fixture)
        document_type = DocumentType.objects.create(
            facility=facility,
            name=f"Sichtbarkeit {user_fixture} {sensitivity}",
            sensitivity=sensitivity,
        )
        event = Event.objects.create(
            facility=facility,
            document_type=document_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )

        assert user_can_see_event(user, event) is expected
        assert user_can_see_document_type(user, document_type) is expected

    def test_unlisted_role_fails_closed_for_elevated_event(self, super_admin_user, event_elevated):
        from core.services.compliance import ROLE_MAX_SENSITIVITY, user_can_see_event

        assert ROLE_MAX_SENSITIVITY.get(User.Role.SUPER_ADMIN, 0) == 0
        assert user_can_see_event(super_admin_user, event_elevated) is False


@pytest.mark.django_db
class TestUnbekannteSensitivityGates:
    """Unbekannte Sensitivity-Werte gaten fail-closed (Refs #1991).

    Die Listen-Wahrheit (``Event.objects.visible_to`` ueber
    ``allowed_sensitivities_for_user``) war seit jeher fail-closed: ein Wert,
    den ``SENSITIVITY_RANK`` nicht kennt, steht in KEINER ``allowed``-Liste und
    faellt fuer jede Rolle heraus. Die Einzelfall-Wahrheit rechnete dagegen mit
    ``SENSITIVITY_RANK.get(value, 0)`` — Rang 0 also, wie ``normal``, sichtbar
    fuer alle. Ein neuer Sensitivity-Wert (Migration, Paket) oder eine korrupte
    Zeile haette Datensaetze aus jeder Liste genommen und sie ueber
    Detail-/Erfassungspfade trotzdem freigegeben.
    """

    UNBEKANNT = "kritisch"

    @staticmethod
    def _force_sensitivity(instance, value):
        """``choices`` sind Formular-Validierung, keine DB-Beschraenkung."""
        type(instance).objects.filter(pk=instance.pk).update(sensitivity=value)
        instance.refresh_from_db()
        return instance

    def test_unbekannter_wert_versteckt_das_ereignis_in_liste_und_einzelpruefung(
        self, facility, admin_user, staff_user, doc_type_elevated
    ):
        from core.services.compliance import user_can_see_document_type, user_can_see_event

        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_elevated,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        self._force_sensitivity(doc_type_elevated, self.UNBEKANNT)
        event.refresh_from_db()

        # Auch die hoechste Rolle sieht den Datensatz nicht — genau wie bei der
        # Listen-Wahrheit, die den unbekannten Wert nie in ``allowed`` fuehrt.
        assert event.pk not in set(Event.objects.visible_to(admin_user).values_list("pk", flat=True))
        assert user_can_see_event(admin_user, event) is False
        assert user_can_see_document_type(admin_user, doc_type_elevated) is False

    def test_unbekannter_feldwert_versteckt_das_feld_fuer_jede_rolle(
        self, admin_user, assistant_user, lead_user, staff_user
    ):
        from core.services.compliance import user_can_see_field

        for user in (assistant_user, staff_user, lead_user, admin_user):
            assert user_can_see_field(user, DocumentType.Sensitivity.NORMAL, self.UNBEKANNT) is False, (
                f"{user.role} sieht ein Feld mit unbekannter Sensitivity"
            )

    def test_leerer_feldwert_erbt_weiterhin_vom_dokumentationstyp(self, assistant_user, lead_user):
        """Gegenrichtung: ``""`` ist Vererbung, kein unbekannter Wert."""
        from core.services.compliance import effective_sensitivity, user_can_see_field

        assert effective_sensitivity(DocumentType.Sensitivity.NORMAL, "") == 0
        assert effective_sensitivity(DocumentType.Sensitivity.ELEVATED, "") == 1
        assert effective_sensitivity(DocumentType.Sensitivity.HIGH, "") == 2
        assert user_can_see_field(assistant_user, DocumentType.Sensitivity.NORMAL, "") is True
        assert user_can_see_field(assistant_user, DocumentType.Sensitivity.ELEVATED, "") is False
        assert user_can_see_field(lead_user, DocumentType.Sensitivity.HIGH, "") is True

    def test_unbekannter_rang_liegt_ueber_jedem_rollen_maximum(self):
        """Der Ersatzrang muss JEDE Rolle ausschliessen, nicht nur die untersten.

        ``max(SENSITIVITY_RANK.values())`` (also ``high``) waere zu niedrig:
        Leitung und Anwendungsbetreuung duerfen ``high`` sehen und bekaemen den
        unbekannten Wert weiterhin ausgeliefert. Die Listen-Query versteckt ihn
        dagegen auch vor ihnen.
        """
        from core.services.compliance.sensitivity import (
            ROLE_MAX_SENSITIVITY,
            SENSITIVITY_RANK,
            UNKNOWN_SENSITIVITY_RANK,
            effective_sensitivity,
            sensitivity_rank,
        )

        unbekannt = effective_sensitivity(self.UNBEKANNT)
        assert unbekannt > max(SENSITIVITY_RANK.values())
        assert unbekannt > max(ROLE_MAX_SENSITIVITY.values())
        # Der Feld-Override zieht denselben Rang hoch, nicht nur der Typ.
        assert effective_sensitivity(DocumentType.Sensitivity.NORMAL, self.UNBEKANNT) == unbekannt
        # ``sensitivity_rank`` selbst kennt KEINE Vererbungs-Semantik: ``""``
        # ist auch dort ein unbekannter Wert. Die Sonderbehandlung liegt bei
        # den Aufrufern (``effective_sensitivity``, ``compute_event_search_text``).
        assert sensitivity_rank("") == UNKNOWN_SENSITIVITY_RANK
        assert effective_sensitivity("") == UNKNOWN_SENSITIVITY_RANK

    def test_unbekannte_feld_sensitivity_landet_nicht_im_suchindex(
        self, facility, admin_user, assistant_user, lead_user, staff_user, client_identified
    ):
        """Dritte Sichtbarkeits-Wahrheit: der Suchindex (Refs #1991, Fix-Runde 1).

        ``compute_event_search_text`` entscheidet ueber eine Mengen-
        Zugehoerigkeit (``sensitivity in {elevated, high}``) statt ueber den
        Rang — ein unbekannter Wert faellt durch beide Zweige und wird
        indexiert. ``Event.search_text`` ist Klartext, und
        ``event_search_queryset`` durchsucht ihn per ``icontains``; das
        Doc-Type-Gate der Suche greift auf EREIGNIS-Ebene und schuetzt ein
        einzelnes Feld nicht. Der Inhalt eines Felds, das
        ``user_can_see_field`` fuer JEDE Rolle sperrt, waere damit als
        Substring-Orakel samt Trefferkarte fuer jede Rolle lesbar.
        """
        from core.services.compliance import user_can_see_field
        from core.services.dashboard.search import event_search_queryset

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Suchindex-Typ",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        ft_unbekannt = FieldTemplate.objects.create(
            facility=facility,
            name="Sonderfeld",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        ft_normal = FieldTemplate.objects.create(
            facility=facility,
            name="Normalfeld Suche",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_unbekannt, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_normal, sort_order=1)
        self._force_sensitivity(ft_unbekannt, self.UNBEKANNT)

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={
                ft_unbekannt.slug: "Geheimwort-Suchindex",
                ft_normal.slug: "Fundwort-Suchindex",
            },
            created_by=staff_user,
        )
        event.refresh_from_db()

        assert "Geheimwort-Suchindex" not in event.search_text
        for user in (assistant_user, staff_user, lead_user, admin_user):
            assert user_can_see_field(user, doc_type.sensitivity, ft_unbekannt.sensitivity) is False
            assert not event_search_queryset(facility, user, "Geheimwort-Suchindex").exists(), (
                f"{user.role} findet den Wert eines fuer niemanden sichtbaren Felds ueber die Suche"
            )

        # Gegenprobe (Verhaltensneutralitaet): das gewoehnliche Feld bleibt
        # indexiert und auffindbar — der Fix schneidet nur Unbekanntes weg.
        assert "Fundwort-Suchindex" in event.search_text
        assert event_search_queryset(facility, assistant_user, "Fundwort-Suchindex").exists()
