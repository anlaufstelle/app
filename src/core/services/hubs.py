"""Kennzahlen der Bereichs-Übersichten „Datenschutz" und „Verwaltung" (Refs #1813).

Konzept [#1801] §4.2: keine Kachelwand, sondern eine **Statusliste mit maximal
sechs Zeilen** — je Zeile Reitername, *eine* handlungsrelevante Zahl (oder ein
Datum) und „Öffnen". Die Übersicht beantwortet genau eine Frage: *Was in diesem
Bereich wartet auf mich?*

Zwei Regeln, die dieses Modul trägt:

1. **Kachel-Zahl == Zahl der Zielseite.** Jede Kennzahl spiegelt exakt den
   Filter ihrer Zielseite — die Berichtigungs-Zahl übernimmt die
   ``is_deleted``/``is_active``-Klauseln aus ``RectificationListView``, die
   Löschfristen-Zahl den ``pending``-Begriff von
   ``build_retention_dashboard_context``. Eine Kachel, die eine andere Zahl
   nennt als die Liste dahinter, ist schlimmer als keine Kachel
   (Guard: ``src/tests/test_hub_services.py``).
2. **Der Service filtert selbst nach Rolle.** Eine Leitung bekommt die beiden
   ``ADMIN_ONLY``-Zeilen gar nicht erst in die Liste. Das Template rendert
   ``{% for %}`` ohne ``{% if %}``-Geflecht (``test_authz_template_links``
   bleibt strukturell sauber) **und** die zugehörigen Queries laufen für sie
   nicht — der Rollenfilter spart Arbeit, er blendet nicht nur aus.

Nicht ableitbar und deshalb bewusst ersetzt (Befund N1): „Audit-Kette zuletzt
geprüft". ``verify_audit_chain`` schreibt keinen ``AuditLog``-Eintrag, und die
Chain-Compliance-Checks sind ``SUPER_ONLY``; ein voller Kettendurchlauf im
Request wäre inakzeptabel. Stattdessen nennt die Zeile den **letzten
Audit-Eintrag** — dieselbe Frage („lebt das Protokoll?"), im Rahmen des
Möglichen beantwortet. Für die Original-Zeile braucht es ein Folgeticket
(``verify_audit_chain`` schreibt ``AUDIT_CHAIN_VERIFIED``).

[#1801]: https://github.com/anlaufstelle/app/issues/1801
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db.models import Count, Q
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from core.models import (
    AuditLog,
    DocumentType,
    FieldTemplate,
    ImportJob,
    RectificationRequest,
    RetentionProposal,
)
from core.models.workitem import DeletionRequest
from core.services.dashboard import facility_admin_dashboard_context


@dataclass(frozen=True)
class HubMetric:
    """Eine Zeile der Statusliste.

    ``value is None`` markiert eine reine Datumszeile (kein Zähler) —
    ``detail`` trägt dann den formatierten Satz. ``is_actionable`` steuert nur
    die Hervorhebung: eine offene Aufgabe ist handlungsrelevant, ein Bestand
    („12 Dokumentationstypen") nicht.
    """

    key: str
    label: str
    value: int | None
    detail: str
    url_name: str
    testid: str
    is_actionable: bool = False


def _date_line(moment: datetime | None, present: str, absent: str) -> str:
    """``present`` mit lokalisiertem Datum — oder ``absent``, wenn es keins gibt."""
    if moment is None:
        return absent
    return present % {"date": date_format(timezone.localtime(moment), "SHORT_DATE_FORMAT")}


# ---------------------------------------------------------------------------
# Datenschutz
# ---------------------------------------------------------------------------


def datenschutz_overview(facility: Any, user: Any) -> list[HubMetric]:
    """Statuszeilen der Datenschutz-Übersicht (max. 5 Zeilen, max. 5 Queries).

    Drei Zeilen für Leitung und Anwendungsbetreuung, zwei weitere nur für die
    Anwendungsbetreuung — deren Zielseiten (``/audit/``, ``/dsgvo/``) sind
    ``ADMIN_ONLY``.
    """
    metrics: list[HubMetric] = []

    if user.is_lead_or_admin:
        pending_deletions = (
            DeletionRequest.objects.for_facility(facility).filter(status=DeletionRequest.Status.PENDING).count()
        )
        metrics.append(
            HubMetric(
                key="deletion-requests",
                label=_("Löschanträge"),
                value=pending_deletions,
                detail=_("warten auf Bestätigung"),
                url_name="core:deletion_request_list",
                testid="hub-metric-deletion-requests",
                is_actionable=pending_deletions > 0,
            )
        )

        # Filter wörtlich wie ``RectificationListView``: Anträge widerrufener
        # oder inaktiver Personen sind kein prüfbarer Vorgang mehr.
        pending_rectifications = (
            RectificationRequest.objects.for_facility(facility)
            .filter(
                status=RectificationRequest.Status.PENDING,
                target_client__is_deleted=False,
                target_client__is_active=True,
            )
            .count()
        )
        metrics.append(
            HubMetric(
                key="rectification-requests",
                label=_("Berichtigungen"),
                value=pending_rectifications,
                detail=_("offen"),
                url_name="core:rectification_list",
                testid="hub-metric-rectification-requests",
                is_actionable=pending_rectifications > 0,
            )
        )

        # ``pending_count`` des Löschfristen-Dashboards — bewusst nicht ein
        # eigener Fristablauf-Filter, der eine andere Zahl nennen würde.
        pending_proposals = (
            RetentionProposal.objects.for_facility(facility).filter(status=RetentionProposal.Status.PENDING).count()
        )
        metrics.append(
            HubMetric(
                key="retention",
                label=_("Löschfristen"),
                value=pending_proposals,
                detail=_("Löschvorschläge offen"),
                url_name="core:retention_dashboard",
                testid="hub-metric-retention",
                is_actionable=pending_proposals > 0,
            )
        )

    if user.is_facility_admin:
        last_entry = (
            AuditLog.objects.filter(facility=facility)
            .order_by("-timestamp")
            .values_list("timestamp", flat=True)
            .first()
        )
        metrics.append(
            HubMetric(
                key="audit",
                label=_("Audit-Log"),
                value=None,
                detail=_date_line(
                    last_entry,
                    _("letzter Eintrag am %(date)s"),
                    _("noch keine Einträge"),
                ),
                url_name="core:audit_log",
                testid="hub-metric-audit",
            )
        )

        last_download = (
            AuditLog.objects.filter(
                facility=facility,
                action=AuditLog.Action.EXPORT,
                target_type="DSGVO-Dokument",
            )
            .order_by("-timestamp")
            .values_list("timestamp", flat=True)
            .first()
        )
        metrics.append(
            HubMetric(
                key="dsgvo",
                label=_("DSGVO-Paket"),
                value=None,
                detail=_date_line(
                    last_download,
                    _("zuletzt heruntergeladen am %(date)s"),
                    _("noch nie heruntergeladen"),
                ),
                url_name="core:dsgvo_package",
                testid="hub-metric-dsgvo",
            )
        )

    return metrics


# ---------------------------------------------------------------------------
# Verwaltung
# ---------------------------------------------------------------------------

#: Importläufe, die noch Arbeit bedeuten — das Gegenstück zu
#: ``ImportJob.FINAL_STATUSES`` (übernommen/abgebrochen).
_OPEN_IMPORT_STATES = tuple(status for status in ImportJob.Status.values if status not in set(ImportJob.FINAL_STATUSES))


def verwaltung_metrics(facility: Any) -> list[HubMetric]:
    """Die drei Zähler-Zeilen der Verwaltungs-Übersicht (3 Queries).

    Alle drei Zielseiten sind ``ADMIN_ONLY`` wie die Übersicht selbst — hier
    gibt es nichts rollenabhängig zu filtern.
    """
    doctypes = DocumentType.objects.for_facility(facility).aggregate(
        total=Count("pk"),
        active=Count("pk", filter=Q(is_active=True)),
    )
    fieldtemplates = FieldTemplate.objects.for_facility(facility).count()
    open_imports = ImportJob.objects.for_facility(facility).filter(status__in=_OPEN_IMPORT_STATES).count()

    return [
        HubMetric(
            key="config",
            label=_("Dokumentationstypen"),
            value=doctypes["total"],
            detail=_("davon %(count)s aktiv") % {"count": doctypes["active"]},
            url_name="core:config_doctype_list",
            testid="hub-metric-config",
        ),
        HubMetric(
            key="config-fieldtemplates",
            label=_("Feldvorlagen"),
            value=fieldtemplates,
            detail=_("angelegt"),
            url_name="core:config_fieldtemplate_list",
            testid="hub-metric-config-fieldtemplates",
        ),
        HubMetric(
            key="import",
            label=_("Datenimport"),
            value=open_imports,
            detail=_("Importläufe offen"),
            url_name="core:import_list",
            testid="hub-metric-import",
            is_actionable=open_imports > 0,
        ),
    ]


def verwaltung_overview(facility: Any, user: Any) -> dict[str, Any]:
    """Kompletter Context der Verwaltungs-Übersicht.

    Die MFA-Zahlen und die Warnliste kommen **1:1** aus
    :func:`facility_admin_dashboard_context` — die Anwendungsbetreuung verliert
    beim Umzug ihrer Rollen-Seite hierher nichts.

    Befund N3 (N+1 über ``has_confirmed_totp_device`` je User) ist mit
    [#1816] behoben: ``facility_admin_dashboard_context`` annotiert die
    TOTP-Prüfung als ``Exists``-Subquery, die Query-Zahl ist unabhängig von
    der Nutzerzahl der Einrichtung.

    [#1816]: https://github.com/anlaufstelle/app/issues/1816
    """
    return {"metrics": verwaltung_metrics(facility), **facility_admin_dashboard_context(user, facility)}
