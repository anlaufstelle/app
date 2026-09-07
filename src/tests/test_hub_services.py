"""Kennzahlen der Bereichs-Übersichten (Refs #1813, Konzept #1801 §4.2).

Der tragende Grundsatz dieser Übersichten ist **eine Zahl = eine Wahrheit**:

    Eine Kachel, die eine andere Zahl nennt als die Liste dahinter, ist
    schlimmer als keine Kachel.

Deshalb vergleichen die Tests hier die Kennzahl nicht mit einer im Test
nachgebauten Query, sondern mit dem, was die **Zielseite selbst** zählt (ihr
Render-Context). Ein abweichender Filter — etwa die ``is_deleted``/``is_active``-
Klauseln der Berichtigungsliste oder der ``pending``-Begriff des
Löschfristen-Dashboards — fällt damit sofort auf, statt erst der Nutzerin.

Zweite Aussage: **der Service filtert selbst nach Rolle.** Die Leitung bekommt
die beiden ``ADMIN_ONLY``-Zeilen gar nicht erst in die Liste — das Template
braucht kein ``{% if %}``-Geflecht (``test_authz_template_links`` bleibt
strukturell sauber) und die zugehörigen Queries laufen für sie nicht.
"""

from __future__ import annotations

import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Client, RetentionProposal
from core.models.workitem import DeletionRequest
from core.services.hubs import datenschutz_overview, verwaltung_metrics, verwaltung_overview

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Datenbestand: je Kennzahl echte Treffer UND einen Nicht-Treffer (Köder),
# damit ein zu weiter Filter auffällt statt zufällig zu passen.
# ---------------------------------------------------------------------------


@pytest.fixture
def hub_data(facility, staff_user, lead_user, client_identified, sample_event):
    # 2 offene Löschanträge + 1 entschiedener (Köder).
    for _ in range(2):
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=uuid.uuid4(),
            reason="Hub-Kennzahl",
            requested_by=staff_user,
            status=DeletionRequest.Status.PENDING,
        )
    DeletionRequest.objects.create(
        facility=facility,
        target_type=DeletionRequest.TargetType.CLIENT,
        target_id=uuid.uuid4(),
        reason="Hub-Kennzahl (entschieden)",
        requested_by=staff_user,
        status=DeletionRequest.Status.APPROVED,
        reviewed_by=lead_user,
        reviewed_at=timezone.now(),
    )

    # 1 offene Berichtigung + 1 offene Berichtigung einer INAKTIVEN Person
    # (Köder: die Zielseite blendet sie aus, weil sie kein prüfbarer Vorgang
    # mehr ist — ``RectificationListView``).
    from core.services.client.rectification import request_rectification

    inactive = Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="HUB-INAKTIV",
        created_by=staff_user,
    )
    for target in (client_identified, inactive):
        request_rectification(target, staff_user, "notes", "Hub-Wunschwert", "Hub-Kennzahl")
    Client.objects.filter(pk=inactive.pk).update(is_active=False)

    # 1 offener Löschvorschlag + 1 zurückgestellter (Köder).
    for status in (RetentionProposal.Status.PENDING, RetentionProposal.Status.DEFERRED):
        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=uuid.uuid4(),
            deletion_due_at=timezone.localdate(),
            retention_category="anonymous",
            status=status,
        )

    return facility


@pytest.fixture
def dsgvo_download(facility, admin_user):
    """Ein EXPORT-Audit-Eintrag, wie ihn ``DSGVODocumentDownloadView`` schreibt."""
    return AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.EXPORT,
        target_type="DSGVO-Dokument",
        target_id="vvt",
        detail={"document": "vvt"},
    )


def _metric(metrics, key):
    found = [m for m in metrics if m.key == key]
    assert found, f"Kennzahl {key!r} fehlt (vorhanden: {[m.key for m in metrics]})"
    return found[0]


# ---------------------------------------------------------------------------
# Kachel-Zahl == Zahl der Zielseite
# ---------------------------------------------------------------------------


class TestDatenschutzMetricsMatchTargetPages:
    def test_deletion_requests_match_the_list_page(self, client, facility, admin_user, hub_data):
        client.force_login(admin_user)
        page = client.get(reverse("core:deletion_request_list"))
        expected = len(page.context["pending_requests"])

        assert _metric(datenschutz_overview(facility, admin_user), "deletion-requests").value == expected
        assert expected == 2, "Sanity: der entschiedene Antrag darf nicht mitzählen."

    def test_rectifications_match_the_list_page(self, client, facility, admin_user, hub_data):
        """Filter-Parität inklusive der ``is_active``-Klausel der Zielseite."""
        client.force_login(admin_user)
        page = client.get(reverse("core:rectification_list"))
        expected = len(page.context["pending_requests"])

        assert _metric(datenschutz_overview(facility, admin_user), "rectification-requests").value == expected
        assert expected == 1, "Sanity: die Berichtigung der inaktiven Person darf nicht mitzählen."

    def test_retention_proposals_match_the_dashboard(self, client, facility, admin_user, hub_data):
        """``pending_count`` des Löschfristen-Dashboards, nicht ein eigener Fristablauf-Filter."""
        client.force_login(admin_user)
        page = client.get(reverse("core:retention_dashboard"))
        expected = page.context["pending_count"]

        assert _metric(datenschutz_overview(facility, admin_user), "retention").value == expected
        assert expected == 1, "Sanity: der zurückgestellte Vorschlag darf nicht mitzählen."

    def test_audit_row_names_the_latest_entry_date(self, facility, admin_user, hub_data):
        """Ersatz für „Audit-Kette zuletzt geprüft" (Befund N1): der Datenpunkt für
        eine Ketten-Prüfung existiert nicht — der letzte Eintrag beantwortet die
        Frage „lebt das Protokoll?" im Rahmen des Ableitbaren."""
        latest = AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.LOGIN,
            detail={"username": admin_user.username},
        )
        row = _metric(datenschutz_overview(facility, admin_user), "audit")

        assert row.value is None, "Datumszeile — keine Zahl."
        assert timezone.localtime(latest.timestamp).strftime("%d.%m.%Y") in row.detail

    def test_dsgvo_row_names_the_last_download(self, facility, admin_user, dsgvo_download):
        row = _metric(datenschutz_overview(facility, admin_user), "dsgvo")

        assert row.value is None
        assert timezone.localtime(dsgvo_download.timestamp).strftime("%d.%m.%Y") in row.detail

    def test_dsgvo_row_ignores_other_exports(self, facility, admin_user):
        """Nur DSGVO-Dokument-Exporte zählen — nicht jeder EXPORT-Eintrag."""
        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.EXPORT,
            target_type="Client",
            target_id=str(uuid.uuid4()),
        )
        row = _metric(datenschutz_overview(facility, admin_user), "dsgvo")

        assert row.value is None
        assert not any(char.isdigit() for char in row.detail), (
            f"Ein Fremd-Export darf kein Download-Datum vortäuschen: {row.detail!r}"
        )


# ---------------------------------------------------------------------------
# Rollenfilter und Query-Budget
# ---------------------------------------------------------------------------


class TestMetricRoleFilterAndBudget:
    def test_lead_gets_three_metrics_admin_gets_five(self, facility, lead_user, admin_user, hub_data):
        lead_keys = [m.key for m in datenschutz_overview(facility, lead_user)]
        admin_keys = [m.key for m in datenschutz_overview(facility, admin_user)]

        assert lead_keys == ["deletion-requests", "rectification-requests", "retention"]
        assert admin_keys == lead_keys + ["audit", "dsgvo"]

    def test_lead_does_not_pay_for_the_admin_only_queries(
        self, facility, lead_user, hub_data, django_assert_num_queries
    ):
        """Der Rollenfilter spart die Queries, er blendet nicht nur aus."""
        with django_assert_num_queries(3):
            datenschutz_overview(facility, lead_user)

    def test_metric_query_count_is_at_most_five(self, facility, admin_user, hub_data, django_assert_max_num_queries):
        with django_assert_max_num_queries(5):
            datenschutz_overview(facility, admin_user)

    def test_verwaltung_metric_query_count_is_at_most_five(self, facility, admin_user, django_assert_max_num_queries):
        """Die vier Zeilen der Verwaltungs-Übersicht; die Doctype-Zeile nennt zwei
        Zahlen aus **einem** Aggregat, die Speicher-Zeile (Refs #2129) drei aus
        einem Aggregat plus einer Settings-Abfrage."""
        with django_assert_max_num_queries(5):
            verwaltung_metrics(facility)


class TestVerwaltungOverview:
    def test_metrics_name_doctypes_fieldtemplates_imports_and_storage(
        self, facility, admin_user, field_template, import_job
    ):
        from core.models import DocumentType

        DocumentType.objects.create(facility=facility, name="Hub-Typ aktiv")
        DocumentType.objects.create(facility=facility, name="Hub-Typ inaktiv", is_active=False)

        metrics = verwaltung_metrics(facility)

        # Refs #2129: vierte Zeile "Speicher" — die Anwendungsbetreuung muss
        # sehen, wie voll das Kontingent der Einrichtung ist, bevor der erste
        # Upload daran scheitert.
        assert [m.key for m in metrics] == ["config", "config-fieldtemplates", "import", "storage"]
        # Zwei Zahlen aus EINEM Aggregat: Bestand und davon aktiv.
        assert _metric(metrics, "config").value == 2
        assert "1" in _metric(metrics, "config").detail
        assert _metric(metrics, "config-fieldtemplates").value == 1
        assert _metric(metrics, "import").value == 1

    def test_committed_import_is_not_an_open_run(self, facility, admin_user, import_job):
        from core.models import ImportJob

        ImportJob.objects.filter(pk=import_job.pk).update(status=ImportJob.Status.COMMITTED)

        assert _metric(verwaltung_metrics(facility), "import").value == 0

    def test_overview_reuses_the_facility_admin_dashboard_context(self, facility, admin_user, settings_obj):
        """Die Warnliste und die MFA-Zahlen kommen 1:1 aus dem bisherigen
        Rollen-Dashboard — die Anwendungsbetreuung verliert beim Umzug nichts."""
        context = verwaltung_overview(facility, admin_user)

        assert [m.key for m in context["metrics"]] == ["config", "config-fieldtemplates", "import", "storage"]
        assert context["users_total"] >= 1
        assert "users_without_mfa" in context
        assert isinstance(context["settings_warnings"], list)
