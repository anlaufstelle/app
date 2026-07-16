"""Audit-coverage regression tests (Refs #532).

Verify that:
- update_client / update_case / update_workitem services write AuditLog entries
  with action=*_UPDATE and a `changed_fields` detail payload (no PII values)
- Settings save via the admin layer writes a SETTINGS_CHANGE audit entry
"""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import AuditLog, Case
from core.services.case import update_case, update_workitem
from core.services.client import update_client
from core.services.system import log_settings_change, snapshot_settings, update_settings


@pytest.mark.django_db
class TestClientUpdateAudit:
    def test_update_client_writes_audit_log(self, client_identified, staff_user):
        before = AuditLog.objects.filter(action=AuditLog.Action.CLIENT_UPDATE).count()
        update_client(client_identified, staff_user, notes="Geändert")
        after = AuditLog.objects.filter(action=AuditLog.Action.CLIENT_UPDATE).count()
        assert after == before + 1

    def test_update_client_audit_has_changed_field_names(self, client_identified, staff_user):
        update_client(client_identified, staff_user, notes="Neue Notiz")
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_UPDATE,
            target_id=str(client_identified.pk),
        ).latest("timestamp")
        assert "changed_fields" in entry.detail
        assert "notes" in entry.detail["changed_fields"]
        # No PII value in detail payload
        assert "Neue Notiz" not in str(entry.detail)

    def test_update_client_no_audit_when_nothing_changed(self, client_identified, staff_user):
        before = AuditLog.objects.filter(action=AuditLog.Action.CLIENT_UPDATE).count()
        update_client(client_identified, staff_user, notes=client_identified.notes)
        after = AuditLog.objects.filter(action=AuditLog.Action.CLIENT_UPDATE).count()
        assert after == before


@pytest.mark.django_db
class TestCaseUpdateAudit:
    def test_update_case_writes_audit_log(self, facility, client_identified, staff_user):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Original",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        before = AuditLog.objects.filter(action=AuditLog.Action.CASE_UPDATE).count()
        update_case(case, staff_user, title="Geändert")
        after = AuditLog.objects.filter(action=AuditLog.Action.CASE_UPDATE).count()
        assert after == before + 1

    def test_update_case_audit_has_changed_field_names(self, facility, client_identified, staff_user):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Original",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        update_case(case, staff_user, title="Neuer Titel")
        entry = AuditLog.objects.filter(action=AuditLog.Action.CASE_UPDATE, target_id=str(case.pk)).latest("timestamp")
        assert "title" in entry.detail.get("changed_fields", [])
        assert "Neuer Titel" not in str(entry.detail)


@pytest.mark.django_db
class TestWorkItemUpdateAudit:
    def test_update_workitem_writes_audit_log(self, sample_workitem, staff_user):
        before = AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_UPDATE).count()
        update_workitem(sample_workitem, staff_user, title="Geändert")
        after = AuditLog.objects.filter(action=AuditLog.Action.WORKITEM_UPDATE).count()
        assert after == before + 1

    def test_update_workitem_audit_has_changed_field_names(self, sample_workitem, staff_user):
        update_workitem(sample_workitem, staff_user, title="Neuer Titel")
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.WORKITEM_UPDATE,
            target_id=str(sample_workitem.pk),
        ).latest("timestamp")
        assert "title" in entry.detail.get("changed_fields", [])
        assert "Neuer Titel" not in str(entry.detail)


@pytest.mark.django_db
class TestSettingsChangeAudit:
    def test_update_settings_writes_audit_log(self, settings_obj, staff_user):
        before = AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).count()
        update_settings(settings_obj, staff_user, session_timeout_minutes=42)
        after = AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).count()
        assert after == before + 1

    def test_log_settings_change_only_when_diff(self, settings_obj, staff_user):
        snap = snapshot_settings(settings_obj)
        before = AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).count()
        # No change → no audit entry
        log_settings_change(settings_obj, staff_user, snap)
        after_unchanged = AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).count()
        assert after_unchanged == before
        # Mutate and log
        settings_obj.session_timeout_minutes = (settings_obj.session_timeout_minutes or 30) + 5
        settings_obj.save()
        log_settings_change(settings_obj, staff_user, snap)
        after_changed = AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).count()
        assert after_changed == before + 1

    def test_settings_audit_does_not_leak_values(self, settings_obj, staff_user):
        update_settings(settings_obj, staff_user, facility_full_name="Geheime Einrichtung XYZ")
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.SETTINGS_CHANGE,
            target_id=str(settings_obj.pk),
        ).latest("timestamp")
        assert "Geheime Einrichtung XYZ" not in str(entry.detail)
        assert "facility_full_name" in entry.detail.get("changed_fields", [])
        # Security N8 (Refs #1445): free-text/PII-Feld darf auch nicht ueber die
        # neue changes-Map leaken — facility_full_name steht nicht auf der
        # value-safe Allowlist, also kein Wert im detail.
        assert "facility_full_name" not in entry.detail.get("changes", {})

    def test_settings_change_audit_contains_old_and_new_values(self, settings_obj, staff_user):
        """Security N8 (Refs #1445): destruktive Fristen-Aenderungen muessen
        forensisch mit Alt-/Neu-Wert rekonstruierbar sein."""
        update_settings(settings_obj, staff_user, retention_identified_days=400)
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.SETTINGS_CHANGE,
            target_id=str(settings_obj.pk),
        ).latest("timestamp")
        assert entry.detail["changes"]["retention_identified_days"] == {"old": 365, "new": 400}

    @pytest.mark.parametrize(
        ("field", "new_value"),
        [
            # Refs #893: verhaltensrelevante Felder, die vor der Audit-
            # Erweiterung 2026-05-15 nicht im Diff erschienen sind.
            ("client_trash_days", 60),
            ("auditlog_retention_months", 36),
            ("mfa_enforced_facility_wide", True),
            ("retention_auto_approve_after_defer", True),
            ("retention_max_defer_count", 5),
            ("retention_use_k_anonymization", True),
            ("k_anonymity_threshold", 10),
            ("search_trigram_threshold", 0.5),
        ],
    )
    def test_compliance_fields_are_audited(self, settings_obj, staff_user, field, new_value):
        """DSGVO-/MFA-/Anonymisierungs-Settings muessen im Audit-Diff auftauchen."""
        # Falls der Default schon dem new_value entspricht, ueberspringen wir den Case.
        if getattr(settings_obj, field) == new_value:
            pytest.skip(f"Default fuer {field} entspricht bereits new_value={new_value}")
        update_settings(settings_obj, staff_user, **{field: new_value})
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.SETTINGS_CHANGE,
            target_id=str(settings_obj.pk),
        ).latest("timestamp")
        assert field in entry.detail.get("changed_fields", []), (
            f"Feld '{field}' wurde geaendert, aber nicht auditiert. detail={entry.detail!r}"
        )


@pytest.mark.django_db
class TestOptimisticLockingSettings:
    """Tests for optimistic locking on Settings updates (Refs #531)."""

    def test_optimistic_locking_settings_conflict(self, settings_obj, staff_user):
        """A stale ``expected_updated_at`` must raise ValidationError."""
        stale = "2000-01-01T00:00:00+00:00"
        before_count = AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).count()
        with pytest.raises(ValidationError):
            update_settings(
                settings_obj,
                staff_user,
                session_timeout_minutes=99,
                expected_updated_at=stale,
            )
        settings_obj.refresh_from_db()
        assert settings_obj.session_timeout_minutes != 99
        # No audit entry must have been written on a failed update.
        after_count = AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).count()
        assert after_count == before_count

    def test_optimistic_locking_settings_success_with_current_timestamp(self, settings_obj, staff_user):
        settings_obj.refresh_from_db()
        current = settings_obj.updated_at.isoformat()
        update_settings(
            settings_obj,
            staff_user,
            session_timeout_minutes=77,
            expected_updated_at=current,
        )
        settings_obj.refresh_from_db()
        assert settings_obj.session_timeout_minutes == 77

    def test_optimistic_locking_settings_none_disables_check(self, settings_obj, staff_user):
        update_settings(
            settings_obj,
            staff_user,
            session_timeout_minutes=55,
            expected_updated_at=None,
        )
        settings_obj.refresh_from_db()
        assert settings_obj.session_timeout_minutes == 55


@pytest.mark.django_db
class TestUpdateViewAuditCoverage:
    """Regression über den VIEW-Pfad (Refs #1613).

    Der Bug: ``ModelForm(instance=…)`` mutiert die Instanz bereits in
    ``_post_clean()`` — bevor der Service den Diff bildet. Ein Diff gegen
    die (schon mutierte) Instanz sieht ``neu == neu`` und schreibt nie
    ``*_UPDATE``. Diese Tests fahren daher bewusst ``POST → View → Form →
    Service → DB`` (nicht den Service direkt — das maskierte den Bug
    bisher, weil dort die Instanz unberührt ankam).
    """

    def _workitem_update_url(self, workitem):
        return reverse("core:workitem_update", kwargs={"pk": workitem.pk})

    def test_workitem_update_view_writes_audit_on_fk_change(
        self, client, staff_user, lead_user, sample_workitem, client_identified
    ):
        """FK-Feld-Wechsel (``assigned_to``: None → lead_user) über den View
        muss eine ``WORKITEM_UPDATE``-Zeile schreiben.

        ``assigned_to`` ist ein ``Meta.fields``-Feld → wird von
        ``construct_instance`` mutiert; ``client`` bleibt bewusst unverändert
        (sonst würde der Nicht-Meta-Feld-Pfad die Lücke maskieren).
        """
        client.force_login(staff_user)
        before = AuditLog.objects.filter(
            action=AuditLog.Action.WORKITEM_UPDATE, target_id=str(sample_workitem.pk)
        ).count()
        response = client.post(
            self._workitem_update_url(sample_workitem),
            {
                "item_type": sample_workitem.item_type,
                "title": sample_workitem.title,
                "priority": sample_workitem.priority,
                "client": str(client_identified.pk),
                "assigned_to": str(lead_user.pk),
            },
        )
        assert response.status_code == 302
        after = AuditLog.objects.filter(
            action=AuditLog.Action.WORKITEM_UPDATE, target_id=str(sample_workitem.pk)
        ).count()
        assert after == before + 1
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.WORKITEM_UPDATE, target_id=str(sample_workitem.pk)
        ).latest("timestamp")
        assert "assigned_to" in entry.detail.get("changed_fields", [])
        sample_workitem.refresh_from_db()
        assert sample_workitem.assigned_to_id == lead_user.pk

    def test_workitem_update_view_no_audit_on_identical_post(
        self, client, staff_user, sample_workitem, client_identified
    ):
        """Negativfall: ein identischer POST darf KEINE neue Zeile schreiben
        (Schutz gegen „einfach immer auditieren" als Pseudo-Fix)."""
        client.force_login(staff_user)
        before = AuditLog.objects.filter(
            action=AuditLog.Action.WORKITEM_UPDATE, target_id=str(sample_workitem.pk)
        ).count()
        response = client.post(
            self._workitem_update_url(sample_workitem),
            {
                "item_type": sample_workitem.item_type,
                "title": sample_workitem.title,
                "priority": sample_workitem.priority,
                "client": str(client_identified.pk),
            },
        )
        assert response.status_code == 302
        after = AuditLog.objects.filter(
            action=AuditLog.Action.WORKITEM_UPDATE, target_id=str(sample_workitem.pk)
        ).count()
        assert after == before

    def test_client_update_view_writes_audit_without_stage_change(self, client, staff_user, client_identified):
        """Klienten-Update OHNE Stage-Wechsel muss ``CLIENT_UPDATE`` schreiben.

        Bewusst ohne Stage-Wechsel: sonst überlebt ``STAGE_CHANGE`` (View
        captured ``old_stage`` vor ``is_valid``) und maskiert den Befund, dass
        ``CLIENT_UPDATE`` über den View nie geschrieben wird.
        """
        client.force_login(staff_user)
        url = reverse("core:client_update", kwargs={"pk": client_identified.pk})
        before = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_UPDATE, target_id=str(client_identified.pk)
        ).count()
        stage_before = AuditLog.objects.filter(
            action=AuditLog.Action.STAGE_CHANGE, target_id=str(client_identified.pk)
        ).count()
        response = client.post(
            url,
            {
                "pseudonym": client_identified.pseudonym,
                "contact_stage": client_identified.contact_stage,
                "age_cluster": client_identified.age_cluster,
                "notes": "Geänderte Notiz",
            },
        )
        assert response.status_code == 302
        after = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_UPDATE, target_id=str(client_identified.pk)
        ).count()
        stage_after = AuditLog.objects.filter(
            action=AuditLog.Action.STAGE_CHANGE, target_id=str(client_identified.pk)
        ).count()
        assert after == before + 1
        # Kein Stage-Wechsel → STAGE_CHANGE darf den Befund nicht maskieren.
        assert stage_after == stage_before
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_UPDATE, target_id=str(client_identified.pk)
        ).latest("timestamp")
        assert "notes" in entry.detail.get("changed_fields", [])
        assert "Geänderte Notiz" not in str(entry.detail)

    def test_client_update_view_no_audit_on_identical_post(self, client, staff_user, client_identified):
        """Negativfall Klient: identischer POST ⇒ keine neue ``CLIENT_UPDATE``-Zeile."""
        client.force_login(staff_user)
        url = reverse("core:client_update", kwargs={"pk": client_identified.pk})
        before = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_UPDATE, target_id=str(client_identified.pk)
        ).count()
        response = client.post(
            url,
            {
                "pseudonym": client_identified.pseudonym,
                "contact_stage": client_identified.contact_stage,
                "age_cluster": client_identified.age_cluster,
                "notes": client_identified.notes,
            },
        )
        assert response.status_code == 302
        after = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_UPDATE, target_id=str(client_identified.pk)
        ).count()
        assert after == before


@pytest.mark.django_db
class TestUpdateRouteAuditEnrollment:
    """Guard gegen Wiederholung — Registry der sieben ``*_update``-Routen (Refs #1613).

    Jede AUDITIERTE Route muss über einen echten HTTP-POST
    (View→Form→Service→DB) tatsächlich eine ``*_UPDATE``-AuditLog-Zeile
    schreiben (prüft TB6, nicht Code-Syntax — deshalb kein AST-/Grep-Guard,
    der ``episode_update`` sofort als False-Positive flaggen würde).

    Die drei nicht-auditierten Routen sind bewusst dokumentiert und
    out-of-scope (Follow-up): ``episode_update`` ruft ``form.save()`` direkt,
    ``event_update``/``goal_update`` haben gar keine ``EVENT_UPDATE``/
    ``GOAL_UPDATE``-Action. Der Guard macht diese Lücke sichtbar, ohne sie
    hier zu fixen.
    """

    #: name → auditiert? Vollständige Registry der sieben Edit-Routen.
    AUDITED_ROUTES = {"case_update", "client_update", "workitem_update", "workitem_status_update"}
    UNAUDITED_ROUTES = {"episode_update", "event_update", "goal_update"}

    def test_case_update_route_writes_audit(self, client, staff_user, case_open, client_identified):
        client.force_login(staff_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.CASE_UPDATE, target_id=str(case_open.pk)).count()
        response = client.post(
            reverse("core:case_update", kwargs={"pk": case_open.pk}),
            {
                "title": "Geänderter Falltitel",
                "description": case_open.description or "",
                "client": str(client_identified.pk),
            },
        )
        assert response.status_code == 302
        after = AuditLog.objects.filter(action=AuditLog.Action.CASE_UPDATE, target_id=str(case_open.pk)).count()
        assert after == before + 1

    def test_workitem_status_update_route_writes_audit(self, client, staff_user, sample_workitem):
        client.force_login(staff_user)
        before = AuditLog.objects.filter(
            action=AuditLog.Action.WORKITEM_UPDATE, target_id=str(sample_workitem.pk)
        ).count()
        response = client.post(
            reverse("core:workitem_status_update", kwargs={"pk": sample_workitem.pk}),
            {"status": "in_progress"},
        )
        assert response.status_code in (200, 302)
        after = AuditLog.objects.filter(
            action=AuditLog.Action.WORKITEM_UPDATE, target_id=str(sample_workitem.pk)
        ).count()
        assert after == before + 1

    def test_unaudited_update_routes_have_no_update_action(self):
        """Macht die Design-Lücke sichtbar (Follow-up), ohne sie zu fixen:
        für die drei Out-of-Scope-Routen existiert bewusst keine passende
        ``*_UPDATE``-Action in ``AuditLog.Action``."""
        action_values = {a.value for a in AuditLog.Action}
        assert "episode_update" not in action_values
        assert "event_update" not in action_values
        assert "goal_update" not in action_values

    def test_registry_covers_exactly_the_seven_edit_routes(self):
        """Alle sieben Registry-Routen müssen in ``core.urls`` auflösbar sein —
        Schutz gegen eine umbenannte/entfernte Edit-Route, die den Guard
        still leerlaufen ließe."""
        from django.urls import NoReverseMatch

        kwargs_by_route = {
            "case_update": {"pk": "00000000-0000-0000-0000-000000000000"},
            "client_update": {"pk": "00000000-0000-0000-0000-000000000000"},
            "workitem_update": {"pk": "00000000-0000-0000-0000-000000000000"},
            "workitem_status_update": {"pk": "00000000-0000-0000-0000-000000000000"},
            "episode_update": {
                "case_pk": "00000000-0000-0000-0000-000000000000",
                "pk": "00000000-0000-0000-0000-000000000000",
            },
            "event_update": {"pk": "00000000-0000-0000-0000-000000000000"},
            "goal_update": {
                "case_pk": "00000000-0000-0000-0000-000000000000",
                "pk": "00000000-0000-0000-0000-000000000000",
            },
        }
        assert set(kwargs_by_route) == self.AUDITED_ROUTES | self.UNAUDITED_ROUTES
        for name, kwargs in kwargs_by_route.items():
            try:
                reverse(f"core:{name}", kwargs=kwargs)
            except NoReverseMatch:  # pragma: no cover
                pytest.fail(f"Edit-Route 'core:{name}' ist nicht (mehr) auflösbar.")
