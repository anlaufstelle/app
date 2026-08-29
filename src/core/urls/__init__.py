"""URL configuration for the core app.

Refs #1835: Package-Split von ehemals ``core/urls.py`` (592 Zeilen,
147 Patterns) in ``_config.py``/``_system.py``/``_offline.py``. Eingebunden
per ``include()`` **ohne eigenen Namespace** — die Submodule setzen bewusst
kein ``app_name``, damit ``include()`` keinen Sub-Namespace aufspannt und
``app_name = "core"`` (unten) der einzige Namespace bleibt. Jedes
``{% url "core:…" %}``/``reverse("core:…")`` verhaelt sich damit unveraendert.
"""

from django.urls import include, path
from django.views.generic import RedirectView

from core.views.account import AccountProfileView
from core.views.attachments import (
    AttachmentDownloadView,
    AttachmentListView,
    AttachmentPurgeView,
    CaseAttachmentDownloadView,
    ClientAttachmentDownloadView,
)
from core.views.audit import AuditLogDetailView, AuditLogListView
from core.views.case_deletion import (
    CaseAnonymizeView,
    CaseDeleteRequestView,
    CaseRestoreView,
    CaseTrashView,
)
from core.views.case_episodes import (
    EpisodeCloseView,
    EpisodeCreateView,
    EpisodeUpdateView,
)
from core.views.case_goals import (
    GoalCreateView,
    GoalToggleView,
    GoalUpdateView,
    MilestoneCreateView,
    MilestoneDeleteView,
    MilestoneToggleView,
)
from core.views.cases import (
    CaseAssignEventView,
    CaseCloseView,
    CaseCreateView,
    CaseDetailView,
    CaseFieldsPartialView,
    CaseListView,
    CaseRemoveEventView,
    CaseReopenView,
    CasesForClientView,
    CaseUpdateView,
)
from core.views.client_deletion import (
    ClientAnonymizeView,
    ClientDeleteRequestView,
    ClientRestoreView,
    ClientTrashView,
)
from core.views.client_merge import ClientMergeView, ClientSimilarHintView, ClientUnmergeView
from core.views.clients import (
    ClientAutocompleteView,
    ClientCreateView,
    ClientDataExportJSONView,
    ClientDataExportPDFView,
    ClientDetailView,
    ClientFieldsPartialView,
    ClientInlineCreateView,
    ClientLiftProcessingRestrictionView,
    ClientListView,
    ClientRenameWarningView,
    ClientRestrictProcessingView,
    ClientUpdateView,
)
from core.views.dashboard import RoleDashboardView
from core.views.dsgvo import DSGVODocumentDownloadView, DSGVOPackageView
from core.views.event_deletion import DeletionRequestListView, DeletionRequestReviewView
from core.views.events import (
    EventCreateView,
    EventDeleteView,
    EventDetailView,
    EventFieldsPartialView,
    EventUpdateView,
    TallyIncrementView,
    TallyUndoView,
)
from core.views.facility_settings import FacilitySettingsView
from core.views.hubs import DatenschutzUebersichtView, VerwaltungUebersichtView
from core.views.import_wizard import (
    ImportAbortView,
    ImportCommitView,
    ImportCreateView,
    ImportDetailView,
    ImportListView,
    ImportMappingView,
    ImportPreviewView,
)
from core.views.lockout_recovery import (
    LockoutRecoveryBackupCodeView,
    LockoutRecoveryConfirmView,
    LockoutRecoveryRequestView,
    LockoutRecoverySentView,
)
from core.views.rectification import (
    RectificationCreateView,
    RectificationListView,
    RectificationReviewView,
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
    DatasetExportView,
    DatasetReportPanelView,
    ExternalReportView,
    PDFExportView,
    ReportReleaseLogView,
    SampleReport1ExportView,
    StatisticsView,
)
from core.views.user_access import (
    UserAccessCreateView,
    UserAccessInviteView,
    UserAccessListView,
    UserAccessUnlockView,
    UserAccessUpdateView,
)
from core.views.workitem_actions import (
    WorkItemCreateView,
    WorkItemStatusUpdateView,
    WorkItemUpdateView,
)
from core.views.workitem_bulk import (
    WorkItemBulkAssignView,
    WorkItemBulkPriorityView,
    WorkItemBulkStatusView,
)
from core.views.workitems import (
    WorkItemDetailView,
    WorkItemInboxView,
)
from core.views.zeitstrom import (
    ZeitstromBansPartialView,
    ZeitstromCurrentShiftPartialView,
    ZeitstromFeedPartialView,
    ZeitstromFocusBoxPartialView,
    ZeitstromTallyCountsPartialView,
    ZeitstromView,
    ZeitstromWatchView,
)

app_name = "core"

urlpatterns = [
    # Zeitstrom (Startseite)
    path("", ZeitstromView.as_view(), name="zeitstrom"),
    # Rollenbezogene Arbeitszentrale (Refs #920)
    path("start/", RoleDashboardView.as_view(), name="dashboard"),
    # Bereichs-Hubs (Refs #1813, Konzept #1801 §4.1): genau zwei neue Routen —
    # alle Mitgliedsseiten behalten ihre Pfade (null Redirects, null
    # Bookmark-Bruch, null Aenderung an Service-Worker-Routen).
    path("datenschutz/", DatenschutzUebersichtView.as_view(), name="datenschutz_uebersicht"),
    path("verwaltung/", VerwaltungUebersichtView.as_view(), name="verwaltung_uebersicht"),
    path("verwaltung/einstellungen/", FacilitySettingsView.as_view(), name="facility_settings"),
    path("verwaltung/zugaenge/", UserAccessListView.as_view(), name="user_access_list"),
    path("verwaltung/zugaenge/neu/", UserAccessCreateView.as_view(), name="user_access_create"),
    path("verwaltung/zugaenge/<int:pk>/", UserAccessUpdateView.as_view(), name="user_access_update"),
    path(
        "verwaltung/zugaenge/<int:pk>/einladung/",
        UserAccessInviteView.as_view(),
        name="user_access_invite",
    ),
    path(
        "verwaltung/zugaenge/<int:pk>/entsperren/",
        UserAccessUnlockView.as_view(),
        name="user_access_unlock",
    ),
    # Übergabe → in den Zeitstrom gefaltet (?view=uebergabe). Refs #1124.
    path("uebergabe/", RedirectView.as_view(url="/?view=uebergabe", permanent=True), name="handover"),
    # Redirects (alte URLs)
    path("aktivitaetslog/", RedirectView.as_view(pattern_name="core:zeitstrom", permanent=True)),
    path("timeline/", RedirectView.as_view(pattern_name="core:zeitstrom", permanent=True)),
    # Clients
    path("clients/", ClientListView.as_view(), name="client_list"),
    path("clients/new/", ClientCreateView.as_view(), name="client_create"),
    path("clients/<uuid:pk>/", ClientDetailView.as_view(), name="client_detail"),
    path("clients/<uuid:pk>/edit/", ClientUpdateView.as_view(), name="client_update"),
    # M24/AP4 (Refs #1738, ADR-039 §12): personen-eigene Anhaenge. Kwarg-Signatur
    # identisch zur Bestands-Event-Route (``pk`` = Owner, ``attachment_pk`` = Anhang).
    path(
        "clients/<uuid:pk>/attachments/<uuid:attachment_pk>/download/",
        ClientAttachmentDownloadView.as_view(),
        name="client_attachment_download",
    ),
    path("clients/<uuid:pk>/export/json/", ClientDataExportJSONView.as_view(), name="client_export_json"),
    path("clients/<uuid:pk>/export/pdf/", ClientDataExportPDFView.as_view(), name="client_export_pdf"),
    # Client-Lösch-Workflow (#626) — Vier-Augen-Antrag, Wiederherstellung, Papierkorb
    path("clients/trash/", ClientTrashView.as_view(), name="client_trash"),
    path("clients/<uuid:pk>/delete/", ClientDeleteRequestView.as_view(), name="client_delete_request"),
    path("clients/<uuid:pk>/restore/", ClientRestoreView.as_view(), name="client_restore"),
    path("clients/<uuid:pk>/anonymize/", ClientAnonymizeView.as_view(), name="client_anonymize"),
    # Duplikat-Merge (Refs #1876, ADR-045) — gestaffelter Einstieg wie der
    # Loeschantrag, Aufheben als Papierkorb-Pflege (Rollen-Gefaelle s. Views).
    # Art.-18-Sperrvermerk (Refs #2045) — POST-only, Einstieg aus der Akte.
    # Setzen ist staff+, Aufheben vier-aeugig ODER durch die Leitung; beide
    # Grenzen zieht der Service, nicht die Route.
    path(
        "clients/<uuid:pk>/restrict-processing/",
        ClientRestrictProcessingView.as_view(),
        name="client_restrict_processing",
    ),
    path(
        "clients/<uuid:pk>/lift-processing-restriction/",
        ClientLiftProcessingRestrictionView.as_view(),
        name="client_lift_processing_restriction",
    ),
    path("clients/<uuid:pk>/merge/", ClientMergeView.as_view(), name="client_merge"),
    path("clients/<uuid:pk>/unmerge/", ClientUnmergeView.as_view(), name="client_unmerge"),
    # Cases
    path("cases/", CaseListView.as_view(), name="case_list"),
    path("cases/new/", CaseCreateView.as_view(), name="case_create"),
    path("cases/<uuid:pk>/", CaseDetailView.as_view(), name="case_detail"),
    path("cases/<uuid:pk>/edit/", CaseUpdateView.as_view(), name="case_update"),
    # M24/AP4 (Refs #1738, ADR-039 §12): fall-eigene Anhaenge (STAFF_PLUS).
    path(
        "cases/<uuid:pk>/attachments/<uuid:attachment_pk>/download/",
        CaseAttachmentDownloadView.as_view(),
        name="case_attachment_download",
    ),
    # Fall-Loesch-Workflow (Refs #1818, ADR-044) — gestaffelter Antrag,
    # Papierkorb, Wiederherstellung, Sofort-Tilgung. Aufbau und Reihenfolge wie
    # beim Personen-Gegenstueck vier Bloecke weiter oben.
    path("cases/trash/", CaseTrashView.as_view(), name="case_trash"),
    path("cases/<uuid:pk>/delete/", CaseDeleteRequestView.as_view(), name="case_delete_request"),
    path("cases/<uuid:pk>/restore/", CaseRestoreView.as_view(), name="case_restore"),
    path("cases/<uuid:pk>/anonymize/", CaseAnonymizeView.as_view(), name="case_anonymize"),
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
    # Refs #1874: ersatzloses Loeschen einer Versionskette (Bestaetigung + POST).
    # Owner-frei adressiert (nur die Anhang-PK), weil die Uebersicht owner-generisch
    # ist; den Owner leitet der View aus der Zeile ab und gatet ihn wie beim Download.
    path("attachments/<uuid:pk>/delete/", AttachmentPurgeView.as_view(), name="attachment_delete"),
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
    # --- M4 Rectification (Refs #1060)
    path(
        "clients/<uuid:pk>/rectification/new/",
        RectificationCreateView.as_view(),
        name="rectification_create",
    ),
    path("rectification/", RectificationListView.as_view(), name="rectification_list"),
    path("rectification/<uuid:pk>/review/", RectificationReviewView.as_view(), name="rectification_review"),
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
    # System (super_admin / installations-weit, Refs #867) — ausgelagert, Refs #1835.
    path("", include("core.urls._system")),
    # Config — Formular-Konfiguration (M0, Refs #1056) — ausgelagert, Refs #1835.
    path("", include("core.urls._config")),
    # Statistics
    path("statistics/", StatisticsView.as_view(), name="statistics"),
    path("statistics/export/csv/", CSVExportView.as_view(), name="statistics_csv_export"),
    path("statistics/export/pdf/", PDFExportView.as_view(), name="statistics_pdf_export"),
    path(
        "statistics/export/sample-report-1/",
        SampleReport1ExportView.as_view(),
        name="statistics_sample_report_1_export",
    ),
    path("statistics/chart-data/", ChartDataView.as_view(), name="statistics_chart_data"),
    path("statistics/external/", ExternalReportView.as_view(), name="statistics_external_report"),
    # M5 P5 (Refs #1061): Berichts-Plugin-Panel + generischer Export.
    path("statistics/reports/panel/", DatasetReportPanelView.as_view(), name="statistics_report_panel"),
    path("statistics/reports/export/", DatasetExportView.as_view(), name="statistics_report_export"),
    # K4a (Refs #1770): Freigabe-Uebersicht herausgegebener Berichtszeitraeume.
    path("statistics/reports/releases/", ReportReleaseLogView.as_view(), name="statistics_report_releases"),
    # DSGVO
    path("dsgvo/", DSGVOPackageView.as_view(), name="dsgvo_package"),
    path("dsgvo/<slug:document>/", DSGVODocumentDownloadView.as_view(), name="dsgvo_document"),
    # --- M4 Import (Refs #1060) ---
    path("import/", ImportListView.as_view(), name="import_list"),
    path("import/neu/", ImportCreateView.as_view(), name="import_create"),
    path("import/<uuid:pk>/", ImportDetailView.as_view(), name="import_detail"),
    path("import/<uuid:pk>/zuordnung/", ImportMappingView.as_view(), name="import_mapping"),
    path("import/<uuid:pk>/probelauf/", ImportPreviewView.as_view(), name="import_preview"),
    path("import/<uuid:pk>/uebernehmen/", ImportCommitView.as_view(), name="import_commit"),
    path("import/<uuid:pk>/abbrechen/", ImportAbortView.as_view(), name="import_abort"),
    # Account
    path("account/", AccountProfileView.as_view(), name="account_profile"),
    # Refs #869: Lockout-Recovery-Flows (Token-Mail + Backup-Code).
    path(
        "account/recovery/",
        LockoutRecoveryRequestView.as_view(),
        name="lockout_recovery_request",
    ),
    path(
        "account/recovery/sent/",
        LockoutRecoverySentView.as_view(),
        name="lockout_recovery_sent",
    ),
    path(
        "account/recovery/confirm/<str:token>/",
        LockoutRecoveryConfirmView.as_view(),
        name="lockout_recovery_confirm",
    ),
    path(
        "account/recovery/backup-code/",
        LockoutRecoveryBackupCodeView.as_view(),
        name="lockout_recovery_backup_code",
    ),
    # HTMX-Fragmente unter /partials/<feature>/, JSON-APIs unter /api/v1/<feature>/ (Refs #848)
    path("partials/workitems/<uuid:pk>/status/", WorkItemStatusUpdateView.as_view(), name="workitem_status_update"),
    path("partials/clients/autocomplete/", ClientAutocompleteView.as_view(), name="client_autocomplete"),
    path("partials/clients/inline-create/", ClientInlineCreateView.as_view(), name="client_inline_create"),
    path("partials/clients/rename-warning/", ClientRenameWarningView.as_view(), name="client_rename_warning"),
    # Refs #1876: nicht-blockierender Duplikat-Hinweis im Personen-Anlagepfad.
    path("partials/clients/similar/", ClientSimilarHintView.as_view(), name="client_similar_hint"),
    path("partials/clients/fields/", ClientFieldsPartialView.as_view(), name="client_fields_partial"),
    path("partials/events/fields/", EventFieldsPartialView.as_view(), name="event_fields_partial"),
    path("partials/tally/increment/", TallyIncrementView.as_view(), name="tally_increment"),
    path("partials/tally/undo/", TallyUndoView.as_view(), name="tally_undo"),
    path("partials/zeitstrom/feed/", ZeitstromFeedPartialView.as_view(), name="zeitstrom_feed_partial"),
    path(
        "partials/zeitstrom/tally-counts/",
        ZeitstromTallyCountsPartialView.as_view(),
        name="zeitstrom_tally_counts_partial",
    ),
    path(
        "partials/zeitstrom/current-shift/",
        ZeitstromCurrentShiftPartialView.as_view(),
        name="zeitstrom_current_shift_partial",
    ),
    path(
        "partials/zeitstrom/focus-box/",
        ZeitstromFocusBoxPartialView.as_view(),
        name="zeitstrom_focus_box_partial",
    ),
    path(
        "partials/zeitstrom/bans/",
        ZeitstromBansPartialView.as_view(),
        name="zeitstrom_bans_partial",
    ),
    path("api/v1/zeitstrom/watch/", ZeitstromWatchView.as_view(), name="zeitstrom_watch"),
    path("partials/cases/fields/", CaseFieldsPartialView.as_view(), name="case_fields_partial"),
    path("partials/cases/for-client/", CasesForClientView.as_view(), name="cases_for_client"),
    path("partials/search/global/", GlobalSearchPartialView.as_view(), name="global_search"),
    path("partials/retention/<uuid:pk>/approve/", RetentionApproveView.as_view(), name="retention_approve"),
    path("partials/retention/<uuid:pk>/hold/", RetentionHoldView.as_view(), name="retention_hold"),
    path(
        "partials/retention/hold/<uuid:pk>/dismiss/",
        RetentionDismissHoldView.as_view(),
        name="retention_dismiss_hold",
    ),
    # Offline (Streetwork Stage 2/3) + JSON-API — ausgelagert, Refs #1835.
    path("", include("core.urls._offline")),
]
