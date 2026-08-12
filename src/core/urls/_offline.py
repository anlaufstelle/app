"""Offline-Routen (Streetwork Stage 2/3) — JSON-API + In-Place-Shells.

Ausgelagert aus ``core/urls.py`` (Refs #1835) — siehe Modul-Docstring von
``core/urls/_system.py`` zur ``include()``-Namensraum-Freiheit.

Reihenfolge unveraendert uebernommen: ``offline_client_list_shell`` (pk-los)
steht bewusst vor ``offline_client_detail`` (``<uuid:pk>``) — Liste vor
Detail, wie ``clients/`` vor ``clients/<uuid:pk>/`` online (Refs #1531/#1499,
Muster #1322). Die beiden Patterns sind zwar disjunkt (literal ohne
pk-Segment vs. UUID-Segment), die Reihenfolge bleibt trotzdem 1:1 erhalten,
um keine neue Fallunterscheidung in den Split hineinzutragen.
"""

from django.urls import path

from core.views.offline import (
    OfflineClientBundleView,
    OfflineClientDetailView,
    OfflineClientListShellView,
    OfflineClientRevocationsView,
    OfflineClientShellView,
    OfflineConflictListView,
    OfflineConflictReviewView,
    OfflineConflictShellView,
    OfflineCsrfTokenView,
    OfflineEventCreateShellView,
    OfflineFacilityBundleView,
    OfflineNoteExportAuditView,
    OfflineWorkItemCreateShellView,
    OfflineWorkItemListShellView,
    OfflineZeitstromShellView,
)

urlpatterns = [
    # Offline (Streetwork Stage 2) — JSON-API für Service Worker
    path(
        "api/v1/offline/bundle/client/<uuid:pk>/",
        OfflineClientBundleView.as_view(),
        name="offline_bundle",
    ),
    # Refs #1518 (#1499): personenloses Facility-Meta-Bundle (Offline-Create-
    # Katalog) — kein pk, kein Roster-PII.
    path(
        "api/v1/offline/bundle/facility/",
        OfflineFacilityBundleView.as_view(),
        name="offline_facility_bundle",
    ),
    # Refs #1760: personen-genaue Widerrufsliste — was ein Geraet aus seinem
    # IndexedDB loeschen MUSS (Gegenrichtung zum Bundle-Endpoint). PII-frei,
    # daher audit-frei; der Client pollt bei jedem Sync und beim Boot.
    path(
        "api/v1/offline/revocations/",
        OfflineClientRevocationsView.as_view(),
        name="offline_revocations",
    ),
    # Refs #1408: dedizierter CSRF-Token-Endpoint fuer den Offline-Replay-
    # Refresh-Pfad (loest das fragile /login/-HTML-Scraping ab).
    path(
        "api/v1/offline/csrf/",
        OfflineCsrfTokenView.as_view(),
        name="offline_csrf",
    ),
    # Refs #1476 (R10): Audit-Spur fuer den Offline-Notiz-Export
    # (entschluesselte Art.-9-PII als Klartext-Datei). Client meldet online
    # direkt, offline ueber die Replay-Queue.
    path(
        "api/v1/offline/audit/note-export/",
        OfflineNoteExportAuditView.as_view(),
        name="offline_note_export_audit",
    ),
    # Refs #1531 (#1499): generischer, pk-loser Shell fuer In-Place-
    # Offline-Rendern der Personenliste an der kanonischen URL /clients/
    # (Service Worker serviert ihn offline) — Muster #1322. MUSS vor
    # offline_client_detail stehen (Liste vor Detail, wie clients/ vor
    # clients/<uuid:pk>/ online) — kollidiert nicht: literal ohne pk-Segment
    # vs. <uuid:pk>-Segment sind disjunkte Patterns.
    path(
        "offline/clients/",
        OfflineClientListShellView.as_view(),
        name="offline_client_list_shell",
    ),
    path(
        "offline/clients/<uuid:pk>/",
        OfflineClientDetailView.as_view(),
        name="offline_client_detail",
    ),
    # Refs #1322: generischer, pk-loser Shell fuer In-Place-Rendern an der
    # kanonischen URL /clients/<pk>/ (Service Worker serviert ihn offline).
    path(
        "offline/client-shell/",
        OfflineClientShellView.as_view(),
        name="offline_client_shell",
    ),
    # Refs #1521 (#1499): generischer, pk-loser Shell fuer die In-Place-
    # Offline-Erfassung eines Ereignisses an der kanonischen URL /events/new/
    # (Service Worker serviert ihn offline) — Muster #1322.
    path(
        "offline/event-shell/",
        OfflineEventCreateShellView.as_view(),
        name="offline_event_shell",
    ),
    # Refs #1522 (#1499): generischer, pk-loser Shell fuer die In-Place-
    # Offline-Erfassung einer Aufgabe an der kanonischen URL /workitems/new/
    # (Service Worker serviert ihn offline) — Muster #1322. Staff+-Gate liegt
    # im Client (assignable_users == Staff+-Marker), nicht am Server-View.
    path(
        "offline/workitem-shell/",
        OfflineWorkItemCreateShellView.as_view(),
        name="offline_workitem_shell",
    ),
    # Refs #1541 (#1499): generischer, pk-loser Shell fuer In-Place-
    # Offline-Rendern der Aufgabenliste an der kanonischen URL /workitems/
    # (Service Worker serviert ihn offline) — Muster #1322. PII-frei; die
    # aggregierten Offline-Aufgaben liest die Alpine-Komponente aus IndexedDB.
    path(
        "offline/workitems/",
        OfflineWorkItemListShellView.as_view(),
        name="offline_workitem_list_shell",
    ),
    # Refs #1542 (#1499): generischer, pk-loser Shell fuer In-Place-
    # Offline-Rendern des Zeitstrom-Feeds an der kanonischen URL / (Service
    # Worker serviert ihn offline) — Muster #1322. PII-frei; die aggregierte
    # lokale Chronik liest die Alpine-Komponente aus IndexedDB.
    path(
        "offline/zeitstrom/",
        OfflineZeitstromShellView.as_view(),
        name="offline_zeitstrom_shell",
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
    # Refs #1396: generischer, pk-loser Shell fuer In-Place-Rendern an der
    # kanonischen URL /offline/conflicts/<pk>/ (Service Worker serviert ihn
    # offline) — Muster #1322 (OfflineClientShellView).
    path(
        "offline/conflict-shell/",
        OfflineConflictShellView.as_view(),
        name="offline_conflict_shell",
    ),
]
