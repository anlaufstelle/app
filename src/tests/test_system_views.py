"""Tests for the installation-wide ``/system/``-area (Refs #867).

Decken folgende Aspekte ab:

* Zugriffsschutz: anonym -> Login-Redirect, facility-Admin -> 403,
  super_admin -> 200.
* Banner: Cross-Facility-Hinweis ist in der Dashboard-Antwort enthalten.
* Cross-Facility-Audit: super_admin sieht NULL-Facility-AuditLogs.
* SYSTEM_VIEW-Audit-Schreibzugriff: jeder System-View-Aufruf protokolliert
  den Zugriff mit ``facility=None`` und ``action=SYSTEM_VIEW``.
* Tier 2 (Refs #875, #876, #877): Cross-Facility-Retention-Uebersicht,
  VVT (Verzeichnis Verarbeitungstaetigkeiten), Cross-Facility-Legal-Holds.
"""

import uuid
from datetime import date, timedelta

import pytest
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog
from core.models.retention import LegalHold, RetentionProposal


@pytest.mark.django_db
class TestSystemDashboardAccess:
    """``GET /system/`` — Zugriffsschutz nach Rolle."""

    def test_anonymous_redirects_to_login(self, client):
        """Anonymer Zugriff muss zum Login redirecten (LoginRequiredMixin)."""
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        """Refs #867: facility_admin ist NICHT super_admin -> 403.

        Zentrales Trenn-Kriterium: nur ``role=SUPER_ADMIN`` darf in
        ``/system/``. Selbst ``facility_admin`` (mit ``is_superuser=True``
        im Test-Fixture) wird abgewiesen.
        """
        client.force_login(admin_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 403

    def test_lead_forbidden(self, client, lead_user):
        client.force_login(lead_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 403

    def test_staff_forbidden(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 403

    def test_assistant_forbidden(self, client, assistant_user):
        client.force_login(assistant_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 403

    def test_super_admin_can_access_dashboard(self, client, super_admin_user):
        """Super-Admin -> 200, Banner mit Cross-Facility-Hinweis sichtbar."""
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 200
        # Cross-Facility-Banner aus ``core/system/_banner.html`` enthaelt
        # den deutschen Schluesselbegriff "facility-übergreifend".
        content_text = response.content.decode("utf-8", errors="replace")
        assert "facility-übergreifend" in content_text, (
            "Cross-Facility-Banner fehlt in der Dashboard-Antwort. "
            "Pruefe, ob ``core/system/_banner.html`` ins Template eingebunden ist."
        )


@pytest.mark.django_db
class TestSystemAuditListAccess:
    """``GET /system/audit/`` — Zugriffsschutz und NULL-Facility-Visibility."""

    def test_anonymous_redirects_to_login(self, client):
        response = client.get(reverse("core:system_audit_list"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_audit_list"))
        assert response.status_code == 403

    def test_super_admin_sees_null_facility_audit(self, client, super_admin_user, facility, admin_user):
        """Refs #867: SYSTEM-Audits mit ``facility=NULL`` (Pre-Auth oder
        SYSTEM_VIEW) sind im Cross-Facility-Audit-Log sichtbar.

        Die Sichtbarkeit kommt im Test-Setup nicht aus RLS (DB-User =
        Superuser, bypass), sondern aus dem View, der ohne ``for_facility``-
        Filter abfragt. In Produktion greift zusaetzlich der RLS-Bypass-
        Branch ``app.is_super_admin='true'``.
        """
        # Pre-Auth-Style: NULL-Facility-Audit (z.B. failed login by
        # unknown user). Wir nutzen Raw-SQL, weil Manager.create()
        # ohne facility den Service-Workflow nicht abbildet.
        marker = "system-null-audit-" + uuid.uuid4().hex[:8]
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO core_auditlog (id, facility_id, user_id, action, "
                "target_type, target_id, detail, ip_address, timestamp) "
                "VALUES (%s, NULL, %s, %s, '', %s, '{}', NULL, NOW())",
                [uuid.uuid4(), admin_user.pk, "login_failed", marker],
            )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_list"))
        assert response.status_code == 200

        page_obj = response.context["page_obj"]
        target_ids = [entry.target_id for entry in page_obj.object_list]
        assert marker in target_ids, (
            f"Super-Admin sieht NULL-Facility-Audit nicht im /system/audit/-Listing. Targets: {target_ids}"
        )


@pytest.mark.django_db
class TestSystemViewAuditTrail:
    """Refs #867: jeder System-View-Aufruf schreibt einen
    ``AuditLog.Action.SYSTEM_VIEW``-Eintrag mit ``facility=None``.

    Damit ist die DSGVO-Rechenschaftspflicht ueber facility-uebergreifende
    Lese-Zugriffe erfuellt — der super_admin hat zwar Bypass-Rechte, aber
    jeder einzelne Zugriff ist auditiert.
    """

    def test_dashboard_get_writes_system_view_audit(self, client, super_admin_user):
        """``GET /system/`` legt einen SYSTEM_VIEW-Audit mit
        ``facility=NULL`` und korrektem ``user`` an.
        """
        before = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 200

        after = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        assert after == before + 1, f"SYSTEM_VIEW-Audit nicht geschrieben. Vorher: {before}, nachher: {after}."

        latest = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).order_by("-timestamp").first()
        assert latest.facility is None, (
            "SYSTEM_VIEW-Audit muss facility=None tragen — System-Event ohne Facility-Bezug."
        )
        assert latest.user_id == super_admin_user.pk
        # ``target_type`` traegt den View-Klassennamen — Audit erlaubt
        # Differenzierung zwischen Dashboard, AuditList, etc.
        assert latest.target_type == "SystemDashboardView", (
            f"target_type sollte 'SystemDashboardView' sein, erhalten {latest.target_type!r}."
        )

    def test_audit_list_get_writes_system_view_audit(self, client, super_admin_user):
        """``GET /system/audit/`` schreibt ebenfalls einen SYSTEM_VIEW-Audit."""
        before = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_list"))
        assert response.status_code == 200

        after = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        assert after == before + 1

        latest = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).order_by("-timestamp").first()
        assert latest.target_type == "SystemAuditLogListView"

    def test_no_audit_for_unauthorized_access(self, client, admin_user):
        """Wenn der Zugriffsschutz greift (facility_admin -> 403), darf
        KEIN SYSTEM_VIEW-Audit geschrieben werden — sonst koennten
        unautorisierte Probings die Audit-Tabelle aufblasen.
        """
        before = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        client.force_login(admin_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 403

        after = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        assert after == before, (
            f"Unautorisierter 403-Zugriff darf keinen SYSTEM_VIEW-Audit schreiben. Vorher: {before}, nachher: {after}."
        )


@pytest.mark.django_db
class TestSystemOrganizationAccess:
    """``GET /system/organization/`` — Schmaler Smoke-Test analog zu
    Dashboard. Voraussetzungen + 200/403-Branches."""

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_organization"))
        assert response.status_code == 403

    def test_super_admin_ok(self, client, super_admin_user, organization):
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_organization"))
        assert response.status_code == 200
        # Banner ist auch hier eingebunden.
        content = response.content.decode("utf-8", errors="replace")
        assert "facility-übergreifend" in content


@pytest.mark.django_db
class TestSystemAuditDetailAccess:
    """``GET /system/audit/<pk>/`` — Detail-Sicht eines AuditLog-Eintrags."""

    def test_facility_admin_forbidden(self, client, admin_user, facility):
        entry = AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.LOGIN,
        )
        client.force_login(admin_user)
        response = client.get(reverse("core:system_audit_detail", kwargs={"pk": entry.pk}))
        assert response.status_code == 403

    def test_super_admin_can_view_any_facility_entry(
        self, client, super_admin_user, facility, second_facility, admin_user
    ):
        """Super-Admin sieht AuditLogs *aller* Einrichtungen — keine
        Facility-Einschraenkung im View.
        """
        # Eintrag in zweiter Facility (nicht der des super_admin — er hat
        # keine).
        entry = AuditLog.objects.create(
            facility=second_facility,
            user=admin_user,
            action=AuditLog.Action.LOGIN,
        )
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_detail", kwargs={"pk": entry.pk}))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Tier 2: Cross-Facility-Retention (Refs #875)
# ---------------------------------------------------------------------------


@pytest.fixture
def retention_proposal_pending_overdue(facility, sample_event):
    """PENDING-Proposal in Facility, deletion_due_at in der Vergangenheit."""
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=sample_event.pk,
        deletion_due_at=date.today() - timedelta(days=5),
        status=RetentionProposal.Status.PENDING,
        retention_category="anonymous",
    )


@pytest.fixture
def retention_proposal_pending_future(facility, client_identified, staff_user, doc_type_contact):
    """PENDING-Proposal in Facility, deletion_due_at in der Zukunft."""
    from core.models import Event

    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=200),
        data_json={"dauer": 5},
        created_by=staff_user,
    )
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=event.pk,
        deletion_due_at=date.today() + timedelta(days=10),
        status=RetentionProposal.Status.PENDING,
        retention_category="identified",
    )


@pytest.fixture
def retention_proposal_second_facility(second_facility, second_facility_user):
    """PENDING-Proposal in second_facility — fuer Cross-Facility-Test."""
    return RetentionProposal.objects.create(
        facility=second_facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=uuid.uuid4(),
        deletion_due_at=date.today() + timedelta(days=20),
        status=RetentionProposal.Status.APPROVED,
        retention_category="qualified",
    )


@pytest.mark.django_db
class TestSystemRetentionAccess:
    """``GET /system/retention/`` — Zugriffsschutz."""

    def test_anonymous_redirects_to_login(self, client):
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 403

    def test_super_admin_can_access(self, client, super_admin_user, facility):
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 200
        # Banner + Subnav-Link "Retention".
        content = response.content.decode("utf-8", errors="replace")
        assert "facility-übergreifend" in content
        assert reverse("core:system_retention") in content


@pytest.mark.django_db
class TestSystemRetentionAggregation:
    """Aggregations-Logik der Retention-View."""

    def test_pending_count_and_overdue(
        self,
        client,
        super_admin_user,
        facility,
        retention_proposal_pending_overdue,
        retention_proposal_pending_future,
    ):
        """Zwei PENDING in einer Facility — eines ueberfaellig, eines nicht.

        Erwartet: PENDING-Count=2, overdue_count=1, is_critical=True.
        """
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 200

        rows = response.context["rows"]
        # Ein Row pro Facility.
        row = next(r for r in rows if r["facility"].pk == facility.pk)
        assert row["count_pending"] == 2
        assert row["overdue_count"] == 1
        assert row["is_critical"] is True
        # next_due_date = min(deletion_due_at) der PENDING — der ueberfaellige.
        assert row["next_due_date"] == retention_proposal_pending_overdue.deletion_due_at

    def test_status_counts_pivot(self, client, super_admin_user, facility, sample_event):
        """Verschiedene Status werden korrekt pivotiert."""
        # Approved
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=uuid.uuid4(),
            deletion_due_at=date.today(),
            status=RetentionProposal.Status.APPROVED,
            retention_category="anonymous",
        )
        # Held
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=uuid.uuid4(),
            deletion_due_at=date.today(),
            status=RetentionProposal.Status.HELD,
            retention_category="anonymous",
        )
        # Deferred
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=uuid.uuid4(),
            deletion_due_at=date.today(),
            status=RetentionProposal.Status.DEFERRED,
            retention_category="anonymous",
        )
        # Rejected (status=REJECTED nicht im unique_active-Set, geht nochmal)
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=uuid.uuid4(),
            deletion_due_at=date.today(),
            status=RetentionProposal.Status.REJECTED,
            retention_category="anonymous",
        )
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 200

        rows = response.context["rows"]
        row = next(r for r in rows if r["facility"].pk == facility.pk)
        assert row["count_approved"] == 1
        assert row["count_held"] == 1
        assert row["count_deferred"] == 1
        assert row["count_rejected"] == 1

    def test_cross_facility_aggregation(
        self,
        client,
        super_admin_user,
        facility,
        second_facility,
        retention_proposal_pending_overdue,
        retention_proposal_second_facility,
    ):
        """Beide Facilities tauchen in der Liste auf, mit getrennten Counts."""
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 200

        rows = response.context["rows"]
        # Beide Facilities vorhanden.
        facility_ids = {r["facility"].pk for r in rows}
        assert facility.pk in facility_ids
        assert second_facility.pk in facility_ids

        first = next(r for r in rows if r["facility"].pk == facility.pk)
        second = next(r for r in rows if r["facility"].pk == second_facility.pk)

        # Erste Facility: 1 PENDING (ueberfaellig), 0 APPROVED
        assert first["count_pending"] == 1
        assert first["count_approved"] == 0
        # Zweite Facility: 0 PENDING, 1 APPROVED
        assert second["count_pending"] == 0
        assert second["count_approved"] == 1

    def test_facility_without_proposals_listed(self, client, super_admin_user, facility, second_facility):
        """Auch Facilities ohne Proposals tauchen in der Liste auf (mit Nullen)."""
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 200

        rows = response.context["rows"]
        assert len(rows) >= 2
        for row in rows:
            assert row["count_pending"] == 0
            assert row["overdue_count"] == 0
            assert row["is_critical"] is False
            assert row["next_due_date"] is None

    def test_totals_aggregate(
        self,
        client,
        super_admin_user,
        facility,
        second_facility,
        retention_proposal_pending_overdue,
        retention_proposal_pending_future,
        retention_proposal_second_facility,
    ):
        """Summen-Zeile addiert ueber alle Facilities."""
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_retention"))
        assert response.status_code == 200

        totals = response.context["totals"]
        assert totals["count_pending"] == 2  # beide in facility
        assert totals["count_approved"] == 1  # in second_facility
        assert totals["overdue_count"] == 1


# ---------------------------------------------------------------------------
# Tier 2: Cross-Facility-Legal-Holds (Refs #877)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSystemLegalHoldAccess:
    """``GET /system/legal-holds/`` — Zugriffsschutz."""

    def test_anonymous_redirects_to_login(self, client):
        response = client.get(reverse("core:system_legal_hold_list"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_legal_hold_list"))
        assert response.status_code == 403

    def test_super_admin_ok(self, client, super_admin_user):
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_legal_hold_list"))
        assert response.status_code == 200
        content = response.content.decode("utf-8", errors="replace")
        assert "facility-übergreifend" in content


@pytest.mark.django_db
class TestSystemLegalHoldList:
    """Listing- und Filter-Logik."""

    def test_lists_holds_cross_facility(
        self,
        client,
        super_admin_user,
        facility,
        second_facility,
        lead_user,
        second_facility_user,
    ):
        """Holds beider Facilities werden gelistet, sortiert nach created_at DESC."""
        h1 = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Erste Begruendung",
            created_by=lead_user,
        )
        h2 = LegalHold.objects.create(
            facility=second_facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Zweite Begruendung",
            created_by=second_facility_user,
        )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_legal_hold_list"))
        assert response.status_code == 200

        page_obj = response.context["page_obj"]
        ids = [h.pk for h in page_obj.object_list]
        assert h1.pk in ids
        assert h2.pk in ids
        # Sortierung created_at DESC: h2 (spaeter erstellt) zuerst.
        assert ids.index(h2.pk) < ids.index(h1.pk)

    def test_filter_by_facility(
        self,
        client,
        super_admin_user,
        facility,
        second_facility,
        lead_user,
        second_facility_user,
    ):
        h1 = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Begruendung A",
            created_by=lead_user,
        )
        LegalHold.objects.create(
            facility=second_facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Begruendung B",
            created_by=second_facility_user,
        )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_legal_hold_list"), {"facility": facility.pk})
        assert response.status_code == 200
        page_obj = response.context["page_obj"]
        ids = [h.pk for h in page_obj.object_list]
        assert ids == [h1.pk]

    def test_filter_status_active(self, client, super_admin_user, facility, lead_user):
        active_hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Aktiv",
            created_by=lead_user,
        )
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Aufgehoben",
            created_by=lead_user,
            dismissed_at=timezone.now(),
            dismissed_by=lead_user,
        )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_legal_hold_list"), {"status": "active"})
        assert response.status_code == 200
        page_obj = response.context["page_obj"]
        ids = [h.pk for h in page_obj.object_list]
        assert ids == [active_hold.pk]

    def test_filter_status_dismissed(self, client, super_admin_user, facility, lead_user):
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Aktiv",
            created_by=lead_user,
        )
        dismissed_hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=uuid.uuid4(),
            reason="Aufgehoben",
            created_by=lead_user,
            dismissed_at=timezone.now(),
            dismissed_by=lead_user,
        )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_legal_hold_list"), {"status": "dismissed"})
        assert response.status_code == 200
        page_obj = response.context["page_obj"]
        ids = [h.pk for h in page_obj.object_list]
        assert ids == [dismissed_hold.pk]

    def test_writes_system_view_audit(self, client, super_admin_user, facility, lead_user):
        """Auch die Legal-Hold-View loggt SYSTEM_VIEW pro Aufruf."""
        before = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_legal_hold_list"))
        assert response.status_code == 200

        after = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        assert after == before + 1
        latest = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).order_by("-timestamp").first()
        assert latest.target_type == "SystemLegalHoldListView"


# ---------------------------------------------------------------------------
# Tier 2: VVT View (Refs #876) — Schmaler Smoke-Test (Konstante in test_vvt.py)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSystemVVTAccess:
    """``GET /system/vvt/`` — Zugriffsschutz und Smoke-Test."""

    def test_anonymous_redirects_to_login(self, client):
        response = client.get(reverse("core:system_vvt"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_vvt"))
        assert response.status_code == 403

    def test_super_admin_ok(self, client, super_admin_user):
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_vvt"))
        assert response.status_code == 200

        # Mindestens 6 Verarbeitungstaetigkeiten im Context.
        activities = response.context["activities"]
        assert len(activities) >= 6

        content = response.content.decode("utf-8", errors="replace")
        # Banner sichtbar.
        assert "facility-übergreifend" in content
        # Druck-Button vorhanden (Print-CSS-MVP).
        assert "system-vvt-print-button" in content

    def test_writes_system_view_audit(self, client, super_admin_user):
        """SYSTEM_VIEW-Audit auch fuer die VVT-View."""
        before = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_vvt"))
        assert response.status_code == 200
        after = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()
        assert after == before + 1
        latest = AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).order_by("-timestamp").first()
        assert latest.target_type == "SystemVVTView"


# ---------------------------------------------------------------------------
# Tier 1: Health-Card im Dashboard (Refs #871)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSystemDashboardHealthCard:
    """Refs #871: Dashboard zeigt eine Health-Card mit DB/Migrations/Disk/Backup/Versions."""

    def test_health_dict_in_context(self, client, super_admin_user):
        """Context enthaelt ``health`` mit allen erwarteten Keys."""
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 200
        health = response.context["health"]
        assert "db" in health
        assert "migrations_pending" in health
        assert "migrations_pending_count" in health
        assert "disk" in health
        assert "backup" in health
        assert "versions" in health
        # DB-Erreichbarkeit ist im Test-Setup True.
        assert health["db"] is True

    def test_health_card_rendered_in_template(self, client, super_admin_user):
        """Template enthaelt das Test-Selektor-Marker fuer die Health-Card."""
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_dashboard"))
        content = response.content.decode("utf-8", errors="replace")
        assert 'data-testid="system-health-card"' in content


# ---------------------------------------------------------------------------
# Tier 1: Sperrkonten + Unlock (Refs #872)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSystemLockoutListAccess:
    """``GET /system/lockouts/`` — Zugriffsschutz und List-Logik."""

    def test_anonymous_redirects_to_login(self, client):
        response = client.get(reverse("core:system_lockout_list"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_lockout_list"))
        assert response.status_code == 403

    def test_super_admin_sees_locked_user(self, client, super_admin_user, staff_user, facility):
        """User mit >=THRESHOLD LOGIN_FAILED-Audits taucht in der Liste auf."""
        from core.services.login_lockout import LOCKOUT_THRESHOLD

        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN_FAILED,
                detail={"username": staff_user.username},
            )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_lockout_list"))
        assert response.status_code == 200
        rows = response.context["locked_rows"]
        assert any(r["user"].pk == staff_user.pk for r in rows), (
            f"Gesperrter User {staff_user.username!r} taucht nicht in locked_rows auf."
        )

    def test_super_admin_sees_only_locked_users(self, client, super_admin_user, staff_user, facility):
        """User mit weniger als Threshold-Fehlversuchen erscheint NICHT."""
        from core.services.login_lockout import LOCKOUT_THRESHOLD

        # Threshold - 1 -> nicht gesperrt.
        for _ in range(LOCKOUT_THRESHOLD - 1):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN_FAILED,
            )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_lockout_list"))
        rows = response.context["locked_rows"]
        assert all(r["user"].pk != staff_user.pk for r in rows), (
            "User mit count<THRESHOLD darf nicht in der Sperrkonten-Liste auftauchen."
        )

    def test_super_admin_excluded_from_list(self, client, super_admin_user):
        """Refs #872: super_admin selbst taucht nie als Sperrkonto auf —
        auch wenn theoretisch viele Failed-Audits existieren."""
        from core.services.login_lockout import LOCKOUT_THRESHOLD

        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=None,  # super_admin hat keine Facility
                user=super_admin_user,
                action=AuditLog.Action.LOGIN_FAILED,
            )
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_lockout_list"))
        rows = response.context["locked_rows"]
        assert all(r["user"].pk != super_admin_user.pk for r in rows)

    def test_unlock_resets_visibility(self, client, super_admin_user, staff_user, facility):
        """Nach einem LOGIN_UNLOCK ohne neue Failed-Audits ist der User
        nicht mehr in der Liste — Cutoff greift."""
        from core.services.login_lockout import LOCKOUT_THRESHOLD, unlock

        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN_FAILED,
            )
        unlock(staff_user, unlocked_by=super_admin_user)

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_lockout_list"))
        rows = response.context["locked_rows"]
        assert all(r["user"].pk != staff_user.pk for r in rows)


@pytest.mark.django_db
class TestSystemUnlockView:
    """``POST /system/lockouts/unlock/`` — Unlock-Flow."""

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(reverse("core:system_unlock"), {"username": "any"})
        assert response.status_code == 403

    def test_super_admin_unlock_writes_audit(self, client, super_admin_user, staff_user, facility):
        """Refs #872: erfolgreicher Unlock schreibt LOGIN_UNLOCK mit
        ``unlocked_by=<super_admin.pk>``."""
        from core.services.login_lockout import LOCKOUT_THRESHOLD

        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN_FAILED,
            )

        client.force_login(super_admin_user)
        response = client.post(reverse("core:system_unlock"), {"username": staff_user.username})
        assert response.status_code == 302
        assert reverse("core:system_lockout_list") in response.url

        unlocks = AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN_UNLOCK)
        assert unlocks.count() == 1
        entry = unlocks.first()
        assert entry.detail.get("unlocked_by") == str(super_admin_user.pk)

    def test_unlock_unknown_user_redirects_with_error(self, client, super_admin_user):
        """Unbekannter Username -> Redirect ohne AuditLog-Schreib."""
        client.force_login(super_admin_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.LOGIN_UNLOCK).count()
        response = client.post(reverse("core:system_unlock"), {"username": "ghost_user"})
        assert response.status_code == 302
        after = AuditLog.objects.filter(action=AuditLog.Action.LOGIN_UNLOCK).count()
        assert after == before, "Unbekannter User darf keinen LOGIN_UNLOCK-Audit schreiben."

    def test_unlock_super_admin_is_not_supported(self, client, super_admin_user):
        """Refs #872: super_admin-Selbstunlock ist konzeptionell nicht
        vorgesehen — der Service-Flow filtert ihn aus."""
        client.force_login(super_admin_user)
        response = client.post(reverse("core:system_unlock"), {"username": super_admin_user.username})
        assert response.status_code == 302
        # Kein LOGIN_UNLOCK-Audit fuer super_admin.
        assert not AuditLog.objects.filter(user=super_admin_user, action=AuditLog.Action.LOGIN_UNLOCK).exists()


# ---------------------------------------------------------------------------
# Tier 1: AuditLog-Export CSV/JSON (Refs #873)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSystemAuditLogExport:
    """``GET /system/audit/export/?format=csv|json`` — Streaming-Exports."""

    def test_anonymous_redirects_to_login(self, client):
        response = client.get(reverse("core:system_audit_export"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_audit_export"))
        assert response.status_code == 403

    def test_csv_export_has_header_and_rows(self, client, super_admin_user, facility, staff_user):
        """CSV-Export enthaelt Header-Zeile + jeden Audit-Eintrag."""
        AuditLog.objects.create(
            facility=facility,
            user=staff_user,
            action=AuditLog.Action.LOGIN,
        )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_export") + "?format=csv")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment" in response["Content-Disposition"]

        body = b"".join(response.streaming_content).decode("utf-8")
        # Header
        assert "timestamp,user,action" in body
        # Mindestens unsere Login-Action sollte drinstehen
        assert "login" in body

    def test_json_export_is_valid_json_array(self, client, super_admin_user, facility, staff_user):
        """JSON-Export liefert ein gueltiges JSON-Array."""
        import json

        AuditLog.objects.create(
            facility=facility,
            user=staff_user,
            action=AuditLog.Action.LOGIN,
        )

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_export") + "?format=json")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")

        body = b"".join(response.streaming_content).decode("utf-8")
        data = json.loads(body)
        assert isinstance(data, list)
        # Mindestens ein Eintrag (unser ``LOGIN``)
        assert len(data) >= 1
        # Schema-Check fuer die Felder
        first = data[0]
        for key in ("timestamp", "user", "action", "target_type", "target_id", "facility", "ip_address", "detail"):
            assert key in first, f"Schluessel {key!r} fehlt im JSON-Export."

    def test_audit_export_writes_audit_entry(self, client, super_admin_user):
        """Refs #873: vor dem Streaming wird ein AUDIT_EXPORT-AuditLog
        geschrieben (DSGVO-Spur)."""
        client.force_login(super_admin_user)
        before = AuditLog.objects.filter(action=AuditLog.Action.AUDIT_EXPORT).count()
        response = client.get(reverse("core:system_audit_export") + "?format=csv")
        # Konsumiere den Stream, damit der Request abgeschlossen ist.
        b"".join(response.streaming_content)
        after = AuditLog.objects.filter(action=AuditLog.Action.AUDIT_EXPORT).count()
        assert after == before + 1, "AUDIT_EXPORT-Audit wurde nicht geschrieben."

        latest = AuditLog.objects.filter(action=AuditLog.Action.AUDIT_EXPORT).order_by("-timestamp").first()
        assert latest.detail.get("format") == "csv"
        assert "filter_count" in latest.detail
        assert latest.facility is None  # System-Event

    def test_export_respects_action_filter(self, client, super_admin_user, facility, staff_user):
        """Filter ``?action=login`` reduziert die Export-Zeilen."""
        AuditLog.objects.create(facility=facility, user=staff_user, action=AuditLog.Action.LOGIN)
        AuditLog.objects.create(facility=facility, user=staff_user, action=AuditLog.Action.LOGOUT)

        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_export") + "?format=csv&action=login")
        body = b"".join(response.streaming_content).decode("utf-8")
        # ``logout``-Audit darf in dieser gefilterten Variante nicht
        # auftauchen.
        assert ",logout," not in body


# ---------------------------------------------------------------------------
# Tier 1: Maintenance-Mode-Toggle (Refs #874)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSystemMaintenanceView:
    """``GET/POST /system/maintenance/`` — Wartungsmodus-Toggle."""

    def test_anonymous_redirects_to_login(self, client):
        response = client.get(reverse("core:system_maintenance"))
        assert response.status_code == 302
        assert "/login/" in response.url

    def test_facility_admin_forbidden(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:system_maintenance"))
        assert response.status_code == 403

    def test_get_shows_inactive_when_no_flag_file(self, client, super_admin_user, tmp_path):
        """Default: Flag-Datei existiert nicht -> ``is_active=False``."""
        from django.test import override_settings

        flag = tmp_path / "maintenance.flag"
        if flag.exists():
            flag.unlink()
        with override_settings(MAINTENANCE_FLAG_FILE=str(flag)):
            client.force_login(super_admin_user)
            response = client.get(reverse("core:system_maintenance"))
            assert response.status_code == 200
            assert response.context["is_active"] is False
            assert response.context["configured"] is True

    def test_get_shows_active_when_flag_exists(self, client, super_admin_user, tmp_path):
        from django.test import override_settings

        flag = tmp_path / "maintenance.flag"
        flag.write_text("Test-Notiz")
        with override_settings(MAINTENANCE_FLAG_FILE=str(flag)):
            client.force_login(super_admin_user)
            response = client.get(reverse("core:system_maintenance"))
            assert response.status_code == 200
            assert response.context["is_active"] is True
            assert response.context["note"] == "Test-Notiz"

    def test_get_shows_unconfigured_when_setting_none(self, client, super_admin_user):
        """``MAINTENANCE_FLAG_FILE=None`` -> Hinweis, kein Toggle."""
        from django.test import override_settings

        with override_settings(MAINTENANCE_FLAG_FILE=None):
            client.force_login(super_admin_user)
            response = client.get(reverse("core:system_maintenance"))
            assert response.status_code == 200
            assert response.context["configured"] is False

    def test_post_enable_creates_flag_and_audit(self, client, super_admin_user, tmp_path):
        from django.test import override_settings

        flag = tmp_path / "maintenance.flag"
        with override_settings(MAINTENANCE_FLAG_FILE=str(flag)):
            client.force_login(super_admin_user)
            before = AuditLog.objects.filter(action=AuditLog.Action.MAINTENANCE_ENABLED).count()
            response = client.post(
                reverse("core:system_maintenance"),
                {"action": "enable", "note": "Testwartung"},
            )
            assert response.status_code == 302
            assert flag.exists(), "Flag-Datei wurde nicht angelegt."
            assert flag.read_text() == "Testwartung"
            after = AuditLog.objects.filter(action=AuditLog.Action.MAINTENANCE_ENABLED).count()
            assert after == before + 1
            latest = AuditLog.objects.filter(action=AuditLog.Action.MAINTENANCE_ENABLED).order_by("-timestamp").first()
            assert latest.detail.get("note") == "Testwartung"

    def test_post_disable_removes_flag_and_audit(self, client, super_admin_user, tmp_path):
        from django.test import override_settings

        flag = tmp_path / "maintenance.flag"
        flag.write_text("active")
        with override_settings(MAINTENANCE_FLAG_FILE=str(flag)):
            client.force_login(super_admin_user)
            before = AuditLog.objects.filter(action=AuditLog.Action.MAINTENANCE_DISABLED).count()
            response = client.post(reverse("core:system_maintenance"), {"action": "disable"})
            assert response.status_code == 302
            assert not flag.exists(), "Flag-Datei sollte entfernt sein."
            after = AuditLog.objects.filter(action=AuditLog.Action.MAINTENANCE_DISABLED).count()
            assert after == before + 1

    def test_post_unconfigured_shows_error(self, client, super_admin_user):
        """Ohne Setting: POST darf keine Datei anlegen, kein AuditLog."""
        from django.test import override_settings

        with override_settings(MAINTENANCE_FLAG_FILE=None):
            client.force_login(super_admin_user)
            before_enabled = AuditLog.objects.filter(action=AuditLog.Action.MAINTENANCE_ENABLED).count()
            response = client.post(reverse("core:system_maintenance"), {"action": "enable"})
            assert response.status_code == 302
            after_enabled = AuditLog.objects.filter(action=AuditLog.Action.MAINTENANCE_ENABLED).count()
            assert after_enabled == before_enabled, "Ohne Setting darf KEIN MAINTENANCE_ENABLED-Audit entstehen."
