"""URL configuration for the core app."""

from django.urls import path
from django.views.generic import RedirectView

from core.views.account import AccountProfileView
from core.views.attachments import AttachmentDownloadView, AttachmentListView
from core.views.audit import AuditLogDetailView, AuditLogListView
from core.views.cases import (
    CaseAssignEventView,
    CaseCloseView,
    CaseCreateView,
    CaseDetailView,
    CaseListView,
    CaseRemoveEventView,
    CaseReopenView,
    CasesForClientView,
    CaseUpdateView,
    EpisodeCloseView,
    EpisodeCreateView,
    EpisodeUpdateView,
    GoalCreateView,
    GoalToggleView,
    GoalUpdateView,
    MilestoneCreateView,
    MilestoneDeleteView,
    MilestoneToggleView,
)
from core.views.clients import (
    ClientAutocompleteView,
    ClientCreateView,
    ClientDataExportJSONView,
    ClientDataExportPDFView,
    ClientDetailView,
    ClientListView,
    ClientUpdateView,
)
from core.views.dsgvo import DSGVODocumentDownloadView, DSGVOPackageView
from core.views.event_deletion import DeletionRequestListView, DeletionRequestReviewView
from core.views.events import (
    EventCreateView,
    EventDeleteView,
    EventDetailView,
    EventFieldsPartialView,
    EventUpdateView,
)
from core.views.handover import HandoverView
from core.views.offline import (
    OfflineClientBundleView,
    OfflineClientDetailView,
    OfflineConflictListView,
    OfflineConflictReviewView,
)
from core.views.retention import (
    RetentionApproveView,
    RetentionBulkApproveView,
    RetentionBulkDeferView,
    RetentionBulkRejectView,
    RetentionDashboardView,
    RetentionDismissHoldView,
    RetentionHoldView,
)
from core.views.search import GlobalSearchPartialView, SearchView
from core.views.statistics import (
    ChartDataView,
    CSVExportView,
    JugendamtExportView,
    PDFExportView,
    StatisticsView,
)
from core.views.workitems import (
    WorkItemBulkAssignView,
    WorkItemBulkPriorityView,
    WorkItemBulkStatusView,
    WorkItemCreateView,
    WorkItemDetailView,
    WorkItemInboxView,
    WorkItemStatusUpdateView,
    WorkItemUpdateView,
)
from core.views.zeitstrom import ZeitstromFeedPartialView, ZeitstromView

app_name = "core"

urlpatterns = [
    # Zeitstrom (Startseite)
    path("", ZeitstromView.as_view(), name="zeitstrom"),
    # Übergabe
    path("uebergabe/", HandoverView.as_view(), name="handover"),
    # Redirects (alte URLs)
    path("aktivitaetslog/", RedirectView.as_view(pattern_name="core:zeitstrom", permanent=True)),
    path("timeline/", RedirectView.as_view(pattern_name="core:zeitstrom", permanent=True)),
    # Clients
    path("clients/", ClientListView.as_view(), name="client_list"),
    path("clients/new/", ClientCreateView.as_view(), name="client_create"),
    path("clients/<uuid:pk>/", ClientDetailView.as_view(), name="client_detail"),
    path("clients/<uuid:pk>/edit/", ClientUpdateView.as_view(), name="client_update"),
    path("clients/<uuid:pk>/export/json/", ClientDataExportJSONView.as_view(), name="client_export_json"),
    path("clients/<uuid:pk>/export/pdf/", ClientDataExportPDFView.as_view(), name="client_export_pdf"),
    # Cases
    path("cases/", CaseListView.as_view(), name="case_list"),
    path("cases/new/", CaseCreateView.as_view(), name="case_create"),
    path("cases/<uuid:pk>/", CaseDetailView.as_view(), name="case_detail"),
    path("cases/<uuid:pk>/edit/", CaseUpdateView.as_view(), name="case_update"),
    path("cases/<uuid:pk>/close/", CaseCloseView.as_view(), name="case_close"),
    path("cases/<uuid:pk>/reopen/", CaseReopenView.as_view(), name="case_reopen"),
    path("cases/<uuid:pk>/assign-event/", CaseAssignEventView.as_view(), name="case_assign_event"),
    path("cases/<uuid:pk>/remove-event/<uuid:event_pk>/", CaseRemoveEventView.as_view(), name="case_remove_event"),
    # Episodes (nested under cases)
    path("cases/<uuid:case_pk>/episodes/new/", EpisodeCreateView.as_view(), name="episode_create"),
    path("cases/<uuid:case_pk>/episodes/<uuid:pk>/edit/", EpisodeUpdateView.as_view(), name="episode_update"),
    path("cases/<uuid:case_pk>/episodes/<uuid:pk>/close/", EpisodeCloseView.as_view(), name="episode_close"),
    # Goals & Milestones (nested under cases)
    path("cases/<uuid:case_pk>/goals/new/", GoalCreateView.as_view(), name="goal_create"),
    path("cases/<uuid:case_pk>/goals/<uuid:pk>/edit/", GoalUpdateView.as_view(), name="goal_update"),
    path("cases/<uuid:case_pk>/goals/<uuid:pk>/toggle/", GoalToggleView.as_view(), name="goal_toggle"),
    path(
        "cases/<uuid:case_pk>/goals/<uuid:goal_pk>/milestones/new/",
        MilestoneCreateView.as_view(),
        name="milestone_create",
    ),
    path("cases/<uuid:case_pk>/milestones/<uuid:pk>/toggle/", MilestoneToggleView.as_view(), name="milestone_toggle"),
    path("cases/<uuid:case_pk>/milestones/<uuid:pk>/delete/", MilestoneDeleteView.as_view(), name="milestone_delete"),
    # Attachments (central file overview)
    path("attachments/", AttachmentListView.as_view(), name="attachment_list"),
    # Events
    path("events/new/", EventCreateView.as_view(), name="event_create"),
    path("events/<uuid:pk>/", EventDetailView.as_view(), name="event_detail"),
    path("events/<uuid:pk>/edit/", EventUpdateView.as_view(), name="event_update"),
    path("events/<uuid:pk>/delete/", EventDeleteView.as_view(), name="event_delete"),
    path(
        "events/<uuid:pk>/attachments/<uuid:attachment_pk>/download/",
        AttachmentDownloadView.as_view(),
        name="attachment_download",
    ),
    # Deletion Requests
    path("deletion-requests/", DeletionRequestListView.as_view(), name="deletion_request_list"),
    path("deletion-requests/<uuid:pk>/review/", DeletionRequestReviewView.as_view(), name="deletion_review"),
    # Retention Dashboard
    path("retention/", RetentionDashboardView.as_view(), name="retention_dashboard"),
    path("retention/bulk-approve/", RetentionBulkApproveView.as_view(), name="retention_bulk_approve"),
    path("retention/bulk-defer/", RetentionBulkDeferView.as_view(), name="retention_bulk_defer"),
    path("retention/bulk-reject/", RetentionBulkRejectView.as_view(), name="retention_bulk_reject"),
    # WorkItems
    path("workitems/", WorkItemInboxView.as_view(), name="workitem_inbox"),
    path("workitems/new/", WorkItemCreateView.as_view(), name="workitem_create"),
    path("workitems/bulk-status/", WorkItemBulkStatusView.as_view(), name="workitem_bulk_status"),
    path("workitems/bulk-priority/", WorkItemBulkPriorityView.as_view(), name="workitem_bulk_priority"),
    path("workitems/bulk-assign/", WorkItemBulkAssignView.as_view(), name="workitem_bulk_assign"),
    path("workitems/<uuid:pk>/", WorkItemDetailView.as_view(), name="workitem_detail"),
    path("workitems/<uuid:pk>/edit/", WorkItemUpdateView.as_view(), name="workitem_update"),
    # Search
    path("search/", SearchView.as_view(), name="search"),
    # Audit
    path("audit/", AuditLogListView.as_view(), name="audit_log"),
    path("audit/<uuid:pk>/", AuditLogDetailView.as_view(), name="audit_detail"),
    # Statistics
    path("statistics/", StatisticsView.as_view(), name="statistics"),
    path("statistics/export/csv/", CSVExportView.as_view(), name="statistics_csv_export"),
    path("statistics/export/pdf/", PDFExportView.as_view(), name="statistics_pdf_export"),
    path("statistics/export/jugendamt/", JugendamtExportView.as_view(), name="statistics_jugendamt_export"),
    path("statistics/chart-data/", ChartDataView.as_view(), name="statistics_chart_data"),
    # DSGVO
    path("dsgvo/", DSGVOPackageView.as_view(), name="dsgvo_package"),
    path("dsgvo/<slug:document>/", DSGVODocumentDownloadView.as_view(), name="dsgvo_document"),
    # Account
    path("account/", AccountProfileView.as_view(), name="account_profile"),
    # HTMX/API Endpoints
    path("api/workitems/<uuid:pk>/status/", WorkItemStatusUpdateView.as_view(), name="workitem_status_update"),
    path("api/clients/autocomplete/", ClientAutocompleteView.as_view(), name="client_autocomplete"),
    path("api/events/fields/", EventFieldsPartialView.as_view(), name="event_fields_partial"),
    path("api/zeitstrom/feed/", ZeitstromFeedPartialView.as_view(), name="zeitstrom_feed_partial"),
    path("api/cases/for-client/", CasesForClientView.as_view(), name="cases_for_client"),
    path("api/search/global/", GlobalSearchPartialView.as_view(), name="global_search"),
    path("api/retention/<uuid:pk>/approve/", RetentionApproveView.as_view(), name="retention_approve"),
    path("api/retention/<uuid:pk>/hold/", RetentionHoldView.as_view(), name="retention_hold"),
    path("api/retention/hold/<uuid:pk>/dismiss/", RetentionDismissHoldView.as_view(), name="retention_dismiss_hold"),
    # Offline (Streetwork Stage 2)
    path(
        "api/offline/bundle/client/<uuid:pk>/",
        OfflineClientBundleView.as_view(),
        name="offline_bundle",
    ),
    path(
        "offline/clients/<uuid:pk>/",
        OfflineClientDetailView.as_view(),
        name="offline_client_detail",
    ),
    # Offline (Streetwork Stage 3) — conflict review UI
    path(
        "offline/conflicts/",
        OfflineConflictListView.as_view(),
        name="offline_conflict_list",
    ),
    path(
        "offline/conflicts/<uuid:pk>/",
        OfflineConflictReviewView.as_view(),
        name="offline_conflict_review",
    ),
]
