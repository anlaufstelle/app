"""Audit-coverage regression tests (Refs #532, #1613, #1957).

Verify that:
- update_client / update_case / update_workitem services write AuditLog entries
  with action=*_UPDATE and a `changed_fields` detail payload (no PII values)
- Settings save via the admin layer writes a SETTINGS_CHANGE audit entry
- jede der sieben ``*_update``-Routen eine passende ``AuditLog.Action`` hat
  (Registry-Guard ``TestUpdateRouteAuditEnrollment``)
"""

from typing import ClassVar

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Case, Event, EventHistory
from core.services.audit.diff import changed_data_json_slugs
from core.services.case import update_case, update_workitem
from core.services.client import update_client
from core.services.events import update_event
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

    Jede Route muss über einen echten HTTP-POST (View→Form→Service→DB)
    tatsächlich eine ``*_UPDATE``-AuditLog-Zeile schreiben (prüft TB6, nicht
    Code-Syntax — deshalb kein AST-/Grep-Guard, der ``episode_update`` sofort
    als False-Positive flaggen würde).

    Seit Refs #1957 trägt ``AuditLog.Action`` für **alle** sieben Routen eine
    passende Aktion; die frühere Dreier-Lücke (``episode_update``,
    ``event_update``, ``goal_update``) ist damit im Vokabular geschlossen.
    ``UNAUDITED_ROUTES`` bleibt als leerer Slot stehen: eine künftige
    Edit-Route darf dort nur mit ausgeschriebener Begründung landen.

    Die **Schreibpfade** folgen getrennt: ``event_update`` steht seit
    #1958 und trägt
    deshalb keinen Marker mehr. Offen bleiben ``episode_update`` und
    ``goal_update`` in
    #1959; solange sie
    fehlen, tragen genau diese **zwei** Routen-Tests ``xfail(strict=True)``. Der
    Marker ist bewusst strikt: sobald der Schreibpfad steht, wird der Test
    XPASS und fällt auf — der jeweilige Schreibpfad-Commit entfernt ihn.
    """

    #: Vollständige Registry der sieben Edit-Routen.
    AUDITED_ROUTES: ClassVar[set[str]] = {
        "case_update",
        "client_update",
        "workitem_update",
        "workitem_status_update",
        "episode_update",
        "event_update",
        "goal_update",
    }
    #: Bewusst leer (Refs #1957) — der Slot bleibt als Begründungszwang stehen.
    UNAUDITED_ROUTES: ClassVar[set[str]] = set()

    #: Route → erwarteter ``AuditLog.Action``-Wert. ``workitem_status_update``
    #: teilt sich die Aktion mit ``workitem_update`` (derselbe Service).
    ACTION_BY_ROUTE: ClassVar[dict[str, str]] = {
        "case_update": "case_update",
        "client_update": "client_update",
        "workitem_update": "workitem_update",
        "workitem_status_update": "workitem_update",
        "episode_update": "episode_update",
        "event_update": "event_update",
        "goal_update": "goal_update",
    }

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

    def test_event_update_route_writes_audit(self, client, staff_user, sample_event):
        client.force_login(staff_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.EVENT_UPDATE, target_id=str(sample_event.pk)).count()
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {"dauer": "30", "notiz": "Aktualisierte Notiz"},
        )
        assert response.status_code == 302
        sample_event.refresh_from_db()
        assert str(sample_event.data_json["dauer"]) == "30"
        after = AuditLog.objects.filter(action=AuditLog.Action.EVENT_UPDATE, target_id=str(sample_event.pk)).count()
        assert after == before + 1

    def test_event_update_route_no_audit_on_identical_post(self, client, staff_user, sample_event):
        """Negativfall Ereignis: identischer POST ⇒ keine ``EVENT_UPDATE``-Zeile.

        Läuft **ohne** ``xfail`` — der Test hält schon vor dem Schreibpfad
        (#1958) fest, dass „einfach immer auditieren" kein zulässiger Fix ist.
        """
        client.force_login(staff_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.EVENT_UPDATE, target_id=str(sample_event.pk)).count()
        response = client.post(
            reverse("core:event_update", kwargs={"pk": sample_event.pk}),
            {"dauer": str(sample_event.data_json["dauer"]), "notiz": sample_event.data_json["notiz"]},
        )
        assert response.status_code == 302
        after = AuditLog.objects.filter(action=AuditLog.Action.EVENT_UPDATE, target_id=str(sample_event.pk)).count()
        assert after == before

    @staticmethod
    def _episode_post_data(episode, **overrides):
        data = {
            "title": episode.title,
            "description": episode.description or "",
            "started_at": episode.started_at.isoformat(),
            "ended_at": episode.ended_at.isoformat() if episode.ended_at else "",
        }
        data.update(overrides)
        return data

    @pytest.mark.xfail(
        strict=True,
        reason="Episoden-Update-Service folgt in #1959 — Marker dort entfernen (XPASS = fertig).",
    )
    def test_episode_update_route_writes_audit(self, client, staff_user, case_open, episode):
        client.force_login(staff_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.EPISODE_UPDATE, target_id=str(episode.pk)).count()
        response = client.post(
            reverse("core:episode_update", kwargs={"case_pk": case_open.pk, "pk": episode.pk}),
            self._episode_post_data(episode, title="Geänderte Episode"),
        )
        assert response.status_code == 302
        episode.refresh_from_db()
        assert episode.title == "Geänderte Episode"
        after = AuditLog.objects.filter(action=AuditLog.Action.EPISODE_UPDATE, target_id=str(episode.pk)).count()
        assert after == before + 1

    def test_episode_update_route_no_audit_on_identical_post(self, client, staff_user, case_open, episode):
        """Negativfall Episode: identischer POST ⇒ keine ``EPISODE_UPDATE``-Zeile."""
        client.force_login(staff_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.EPISODE_UPDATE, target_id=str(episode.pk)).count()
        response = client.post(
            reverse("core:episode_update", kwargs={"case_pk": case_open.pk, "pk": episode.pk}),
            self._episode_post_data(episode),
        )
        assert response.status_code == 302
        after = AuditLog.objects.filter(action=AuditLog.Action.EPISODE_UPDATE, target_id=str(episode.pk)).count()
        assert after == before

    @pytest.mark.xfail(
        strict=True,
        reason="Audit-Write in update_goal folgt in #1959 — Marker dort entfernen (XPASS = fertig).",
    )
    def test_goal_update_route_writes_audit(self, client, staff_user, case_open, outcome_goal):
        client.force_login(staff_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.GOAL_UPDATE, target_id=str(outcome_goal.pk)).count()
        response = client.post(
            reverse("core:goal_update", kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk}),
            {"title": "Geändertes Wirkungsziel", "description": outcome_goal.description or ""},
        )
        assert response.status_code == 200
        outcome_goal.refresh_from_db()
        assert outcome_goal.title == "Geändertes Wirkungsziel"
        after = AuditLog.objects.filter(action=AuditLog.Action.GOAL_UPDATE, target_id=str(outcome_goal.pk)).count()
        assert after == before + 1

    def test_goal_update_route_no_audit_on_identical_post(self, client, staff_user, case_open, outcome_goal):
        """Negativfall Wirkungsziel: identischer POST ⇒ keine ``GOAL_UPDATE``-Zeile."""
        client.force_login(staff_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.GOAL_UPDATE, target_id=str(outcome_goal.pk)).count()
        response = client.post(
            reverse("core:goal_update", kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk}),
            {"title": outcome_goal.title, "description": outcome_goal.description or ""},
        )
        assert response.status_code == 200
        after = AuditLog.objects.filter(action=AuditLog.Action.GOAL_UPDATE, target_id=str(outcome_goal.pk)).count()
        assert after == before

    def test_every_edit_route_has_a_matching_update_action(self):
        """Umkehrung des früheren Lücken-Tests (Refs #1957): für **jede** der
        sieben Edit-Routen existiert eine passende ``*_UPDATE``-Action.

        Vorher hielt dieser Slot fest, dass ``episode_update``/``event_update``/
        ``goal_update`` bewusst *keine* Action haben. Die Anforderung hat sich
        mit #1957 umgekehrt — das Vokabular ist jetzt vollständig, und der Test
        schützt es gegen ein stilles Zurückfallen."""
        action_values = {a.value for a in AuditLog.Action}
        missing = sorted({action for action in self.ACTION_BY_ROUTE.values() if action not in action_values})
        assert not missing, f"Edit-Routen ohne passende AuditLog.Action (Refs #1957): {missing}"
        assert not self.UNAUDITED_ROUTES, (
            "Eine Edit-Route ist als nicht-auditiert eingetragen — das braucht eine "
            f"ausgeschriebene Begründung im Klassen-Docstring: {sorted(self.UNAUDITED_ROUTES)}"
        )

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
        assert set(self.ACTION_BY_ROUTE) == self.AUDITED_ROUTES | self.UNAUDITED_ROUTES
        for name, kwargs in kwargs_by_route.items():
            try:
                reverse(f"core:{name}", kwargs=kwargs)
            except NoReverseMatch:  # pragma: no cover
                pytest.fail(f"Edit-Route 'core:{name}' ist nicht (mehr) auflösbar.")


@pytest.mark.django_db
class TestChangedDataJsonSlugs:
    """Vertrag des Slug-Diffs für ``data_json`` (Refs #1958).

    Pendant zu ``changed_fields_vs_db`` für die dynamischen Formularfelder:
    verglichen wird der **DB-Stand** gegen die gleich zu schreibende Payload,
    Fernet-Token auf beiden Seiten entschlüsselt, Datei-Marker ausgenommen.
    """

    def _event(self, facility, doc_type, staff_user, data_json):
        return Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json=data_json,
            created_by=staff_user,
        )

    def test_nur_der_geaenderte_slug_wird_gemeldet(self, facility, doc_type_contact, staff_user):
        event = self._event(facility, doc_type_contact, staff_user, {"dauer": 15, "notiz": "Vorher"})
        assert changed_data_json_slugs(event, {"dauer": 15, "notiz": "Nachher"}, lock=False) == ["notiz"]

    def test_identische_payload_meldet_nichts(self, facility, doc_type_contact, staff_user):
        event = self._event(facility, doc_type_contact, staff_user, {"dauer": 15, "notiz": "Gleich"})
        assert changed_data_json_slugs(event, {"dauer": 15, "notiz": "Gleich"}, lock=False) == []

    def test_verschluesseltes_feld_mit_gleichem_klartext_gilt_als_unveraendert(
        self, facility, doc_type_crisis, staff_user
    ):
        """Fernet-Rauschen: derselbe Klartext ergibt bei jedem ``save()`` ein
        neues Token. Ohne ``safe_decrypt`` auf beiden Seiten gälte **jedes**
        verschlüsselte Feld bei jeder Bearbeitung als geändert."""
        from core.services.file_vault import is_encrypted_value

        event = self._event(facility, doc_type_crisis, staff_user, {"notiz-krise": "Sehr geheime Information"})
        event.refresh_from_db()
        assert is_encrypted_value(event.data_json["notiz-krise"]), "Vorbedingung: DB-Stand liegt als Token"

        assert changed_data_json_slugs(event, {"notiz-krise": "Sehr geheime Information"}, lock=False) == []
        assert changed_data_json_slugs(event, {"notiz-krise": "Anderer Text"}, lock=False) == ["notiz-krise"]

    def test_zugang_und_abgang_eines_slugs_gelten_als_aenderung(self, facility, doc_type_contact, staff_user):
        """Im Ticket nicht spezifiziert, hier bewusst festgelegt: ein Slug, der
        nur auf einer Seite steht, ist eine Inhaltsänderung (Feld erstmals
        gefüllt bzw. aus dem Typ gefallen) — kein Sonderfall „unverändert"."""
        event = self._event(facility, doc_type_contact, staff_user, {"dauer": 15})
        assert changed_data_json_slugs(event, {"notiz": "Neu"}, lock=False) == ["dauer", "notiz"]

    def test_leerer_string_ist_nicht_dasselbe_wie_ein_fehlender_slug(self, facility, doc_type_contact, staff_user):
        """Ein geleertes Feld (``""``) bleibt im Payload stehen — es darf nicht
        mit dem Abgang des Slugs verwechselt werden, sonst verschwindet die
        häufigste echte Änderung (Feld geleert) aus dem Audit.

        Die drei Übergänge pinnen zugleich, dass ``_ABWESEND`` ein **eigenes**
        Objekt sein muss: mit ``""`` als Sentinel wären „Key fehlt" und „Key
        trägt ``''``" ununterscheidbar, und die beiden hinteren Fälle fielen
        stumm aus dem Audit.
        """
        # Wert → "": der Slug steht auf beiden Seiten.
        geleert = self._event(facility, doc_type_contact, staff_user, {"notiz": "Vorher"})
        assert changed_data_json_slugs(geleert, {"notiz": ""}, lock=False) == ["notiz"]

        # "" → Key fehlt (Slug aus dem Dokumentationstyp gefallen).
        abgang = self._event(facility, doc_type_contact, staff_user, {"notiz": ""})
        assert changed_data_json_slugs(abgang, {}, lock=False) == ["notiz"]

        # Key fehlt → "" (Slug neu am Typ, leer gespeichert).
        zugang = self._event(facility, doc_type_contact, staff_user, {"dauer": 15})
        assert changed_data_json_slugs(zugang, {"dauer": 15, "notiz": ""}, lock=False) == ["notiz"]

    def test_datei_marker_bleiben_aussen_vor(self, facility, doc_type_contact, staff_user):
        """Anhänge auditiert ein eigenes Teilstück — ein Marker-Wechsel darf
        hier keinen Feld-Diff erzeugen (beide Marker-Formate)."""
        event = self._event(
            facility,
            doc_type_contact,
            staff_user,
            {"notiz": "Gleich", "anhang": {"__file__": True, "attachment_id": "alt"}},
        )
        neu = {"notiz": "Gleich", "anhang": {"__files__": True, "entries": [{"id": "neu", "sort": 0}]}}
        assert changed_data_json_slugs(event, neu, lock=False) == []

    def test_fehlende_zeile_meldet_alle_slugs(self, facility, doc_type_contact):
        """Offline-Replay vor dem Insert: ohne DB-Zeile gilt alles als neu
        (Symmetrie zu ``changed_fields_vs_db``)."""
        ghost = Event(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
        )
        assert changed_data_json_slugs(ghost, {"dauer": 5, "notiz": "x"}, lock=False) == ["dauer", "notiz"]


@pytest.mark.django_db
class TestEventUpdateAudit:
    """``update_event`` schreibt ``EVENT_UPDATE`` mit Feldnamen-Diff (Refs #1958)."""

    def _entries(self, event):
        return AuditLog.objects.filter(action=AuditLog.Action.EVENT_UPDATE, target_id=str(event.pk))

    def test_update_event_schreibt_genau_eine_zeile_mit_slug(self, sample_event, staff_user):
        update_event(sample_event, staff_user, {"dauer": 15, "notiz": "Neue Notiz"})
        entries = self._entries(sample_event)
        assert entries.count() == 1
        entry = entries.get()
        assert entry.detail["changed_fields"] == ["notiz"]
        assert entry.target_type == "Event"
        assert entry.facility_id == sample_event.facility_id
        assert entry.user_id == staff_user.pk

    def test_detail_traegt_den_dokumentationstyp(self, sample_event, staff_user):
        update_event(sample_event, staff_user, {"dauer": 30, "notiz": "Testnotiz"})
        assert self._entries(sample_event).get().detail["document_type"] == sample_event.document_type.name

    def test_detail_enthaelt_keinen_feldwert(self, sample_event, staff_user):
        update_event(sample_event, staff_user, {"dauer": 15, "notiz": "Klartext-Befund-Probe"})
        assert "Klartext-Befund-Probe" not in str(self._entries(sample_event).get().detail)

    def test_identischer_payload_schreibt_kein_audit_aber_historie(self, sample_event, staff_user):
        """Bewusste Asymmetrie: ``EventHistory`` ist der lückenlose
        Bearbeitungsverlauf und wird unbedingt geschrieben, das AuditLog nur
        bei echtem Delta (Muster ``update_client``/``update_case``)."""
        historie_vorher = EventHistory.objects.filter(event=sample_event).count()
        update_event(sample_event, staff_user, {"dauer": 15, "notiz": "Testnotiz"})
        assert self._entries(sample_event).count() == 0
        assert EventHistory.objects.filter(event=sample_event).count() == historie_vorher + 1

    def test_verschluesseltes_feld_ohne_klartext_aenderung_fehlt_im_diff(
        self, facility, doc_type_crisis, staff_user, client_identified
    ):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Sehr geheime Information"},
            created_by=staff_user,
        )
        update_event(event, staff_user, {"notiz-krise": "Sehr geheime Information"})
        assert self._entries(event).count() == 0

    def test_verschluesseltes_feld_meldet_nur_den_slug_nie_den_wert(
        self, facility, doc_type_crisis, staff_user, client_identified
    ):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Alter Krisentext"},
            created_by=staff_user,
        )
        update_event(event, staff_user, {"notiz-krise": "Neuer Krisentext"})
        entry = self._entries(event).get()
        assert entry.detail["changed_fields"] == ["notiz-krise"]
        assert "Krisentext" not in str(entry.detail)

    def test_offline_replay_mit_verbrauchtem_token_schreibt_keine_zweite_zeile(self, sample_event, staff_user):
        """Refs #1338: Der JSON-/Offline-Pfad erzwingt einen Versions-Token.

        Ein zweimal eingespieltes Offline-Bundle trägt denselben, inzwischen
        verbrauchten Token — ``check_version_conflict`` bricht ab, bevor
        ``update_event`` irgendetwas schreibt, und das append-only Log bekommt
        keine Dublette.

        Der Zwischenschritt setzt den DB-Stand per ``QuerySet.update`` auf den
        Alt-Wert zurück (kein ``auto_now``, der Token bleibt also verbraucht).
        Ohne ihn bliebe die Wiederholung schon am Leer-Diff stumm und der Test
        bewiese nicht, was er behauptet: dass die **Versionsprüfung** die
        zweite Zeile verhindert.
        """
        token = sample_event.updated_at
        payload = {"dauer": 15, "notiz": "Aus dem Offline-Bundle"}

        update_event(sample_event, staff_user, payload, expected_updated_at=token, require_version_token=True)
        assert self._entries(sample_event).count() == 1

        Event.objects.filter(pk=sample_event.pk).update(data_json={"dauer": 15, "notiz": "Testnotiz"})

        with pytest.raises(ValidationError) as excinfo:
            update_event(sample_event, staff_user, payload, expected_updated_at=token, require_version_token=True)
        assert excinfo.value.code == "version_conflict"
        assert self._entries(sample_event).count() == 1
