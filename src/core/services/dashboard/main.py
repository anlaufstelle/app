"""Rollenbezogene Arbeitszentrale (Refs #920).

Service-Layer fuer die vier rollenspezifischen Landingpages: Fachkraft/
Assistent, Leitung, Facility-Admin, Super-Admin. Pro Rolle eine Funktion
``..._dashboard_context(user, facility)`` (Super-Admin ohne Facility), die
einen Dict-Context mit Counts/Listen liefert. Keine neuen Modelle — nur
bestehende Daten verdichtet aggregiert.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import (
    AuditLog,
    Client,
    DeletionRequest,
    Event,
    Facility,
    LegalHold,
    RetentionProposal,
    Settings,
    StatisticsSnapshot,
    User,
    WorkItem,
)


def staff_dashboard_context(user, facility) -> dict:
    """Daten fuer die Fachkraft-/Assistent-Arbeitszentrale.

    Karten: heutige Kontakte, eigene offene Aufgaben, zuletzt
    bearbeitete Personen.
    """
    today = timezone.localdate()
    today_start = timezone.make_aware(datetime.combine(today, time.min))
    today_end = today_start + timedelta(days=1)

    today_events_count = Event.objects.filter(
        facility=facility,
        occurred_at__gte=today_start,
        occurred_at__lt=today_end,
        is_deleted=False,
    ).count()

    my_open_workitems = (
        WorkItem.objects.filter(
            facility=facility,
            assigned_to=user,
            status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS],
        )
        .select_related("client")
        .order_by("due_date", "-created_at")[:5]
    )

    recent_clients = list(Client.objects.filter(facility=facility, is_active=True).order_by("-updated_at")[:5])

    return {
        "today_events_count": today_events_count,
        "my_open_workitems": list(my_open_workitems),
        "my_open_workitems_count": my_open_workitems.count(),
        "recent_clients": recent_clients,
    }


def lead_dashboard_context(user, facility) -> dict:
    """Daten fuer die Leitungs-Arbeitszentrale.

    Karten: ausstehende Loeschantraege, Retention-Vorschlaege, aktive
    Legal Holds, juengste Statistik-Snapshots.
    """
    pending_deletion_requests = DeletionRequest.objects.filter(
        facility=facility,
        status=DeletionRequest.Status.PENDING,
    ).count()

    pending_retention_proposals = RetentionProposal.objects.filter(
        facility=facility,
        status=RetentionProposal.Status.PENDING,
    ).count()

    active_legal_holds = sum(
        1 for h in LegalHold.objects.filter(facility=facility, dismissed_at__isnull=True) if h.is_active
    )

    last_snapshot = StatisticsSnapshot.objects.filter(facility=facility).order_by("-year", "-month").first()

    return {
        "pending_deletion_requests": pending_deletion_requests,
        "pending_retention_proposals": pending_retention_proposals,
        "active_legal_holds": active_legal_holds,
        "last_snapshot": last_snapshot,
    }


def facility_admin_dashboard_context(user, facility) -> dict:
    """Daten fuer die Facility-Admin-Arbeitszentrale.

    Karten: User ohne MFA, Settings-Warnungen (z.B. MFA nicht erzwungen,
    keine K-Anon), aktive Login-Lockouts.
    """
    users_qs = User.objects.filter(facility=facility, is_active=True)
    users_without_mfa = sum(1 for u in users_qs if not u.has_confirmed_totp_device)

    warnings: list[str] = []
    try:
        settings_obj = facility.settings
    except Settings.DoesNotExist:
        settings_obj = None
    if settings_obj is None:
        warnings.append(_("Keine Settings konfiguriert — bitte initial einrichten."))
    else:
        if not settings_obj.mfa_enforced_facility_wide:
            warnings.append(
                _("MFA-Pflicht (2FA) ist einrichtungsweit nicht aktiv — Login bleibt ohne zweiten Faktor moeglich.")
            )
        if not settings_obj.retention_use_k_anonymization:
            warnings.append(
                _(
                    "K-Anonymisierung ist deaktiviert — Loeschlauf entfernt Datensaetze hart. "
                    "Falls Statistik-Erhalt gewuenscht, in Settings aktivieren."
                )
            )

    return {
        "users_without_mfa": users_without_mfa,
        "users_total": users_qs.count(),
        "settings_warnings": warnings,
    }


def super_admin_dashboard_context(user) -> dict:
    """Daten fuer die Super-Admin-Arbeitszentrale.

    Cross-Facility: Anzahl Einrichtungen, juengste Audit-Ereignisse
    (24 h), kritische Audit-Aktionen.
    """
    cutoff = timezone.now() - timedelta(hours=24)
    recent_audit_events_count = AuditLog.objects.filter(timestamp__gte=cutoff).count()

    critical_actions = [
        AuditLog.Action.LOGIN_FAILED,
        AuditLog.Action.SECURITY_VIOLATION,
        AuditLog.Action.DELETE,
        AuditLog.Action.DELETION_APPROVED,
    ]
    critical_recent = AuditLog.objects.filter(
        timestamp__gte=cutoff,
        action__in=critical_actions,
    ).count()

    return {
        "facilities_count": Facility.objects.count(),
        "users_total": User.objects.filter(is_active=True).count(),
        "recent_audit_events_count": recent_audit_events_count,
        "critical_recent": critical_recent,
    }
