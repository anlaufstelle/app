"""Rollenbezogene Arbeitszentrale (Refs #920).

Service-Layer fuer die vier rollenspezifischen Landingpages: Fachkraft/
Assistent, Leitung, Facility-Admin, Super-Admin. Pro Rolle eine Funktion
``..._dashboard_context(user, facility)`` (Super-Admin ohne Facility), die
einen Dict-Context mit Counts/Listen liefert. Keine neuen Modelle — nur
bestehende Daten verdichtet aggregiert.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import (
    AuditLog,
    DeletionRequest,
    EncryptedTOTPDevice,
    Event,
    Facility,
    RetentionProposal,
    Settings,
    StatisticsSnapshot,
    User,
    WorkItem,
)
from core.services.file_vault.quota import facility_storage_usage, storage_warning
from core.services.workitem_visibility import workitems_for_read


def staff_dashboard_context(user, facility) -> dict:
    """Daten fuer die Fachkraft-/Assistent-Arbeitszentrale.

    Nur Handlungsbedarf: heutige Kontakte, eigene offene Aufgaben mit
    Faelligkeit zuerst (ueberfaellige oben), Ueberfaellig-Zaehler.
    Persoenlich-historische Listen leben im Profil (Refs #1124).
    """
    today = timezone.localdate()
    today_start = timezone.make_aware(datetime.combine(today, time.min))
    today_end = today_start + timedelta(days=1)

    today_events_count = (
        Event.objects.for_facility(facility)
        .visible_to(user)
        .exclude(client__is_deleted=True)
        .exclude(client__is_active=False)
        .filter(occurred_at__gte=today_start, occurred_at__lt=today_end, is_deleted=False)
        .count()
    )

    open_qs = (
        workitems_for_read(facility)
        .filter(assigned_to=user, status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS])
        .select_related("client")
    )

    my_open_workitems = open_qs.order_by(F("due_date").asc(nulls_last=True), "-created_at")[:5]

    return {
        "today_events_count": today_events_count,
        "my_open_workitems": list(my_open_workitems),
        "my_open_workitems_count": open_qs.count(),
        "my_overdue_count": open_qs.filter(due_date__lt=today).count(),
    }


def lead_dashboard_context(user, facility) -> dict:
    """Daten fuer die Leitungs-Arbeitszentrale.

    Karten: ausstehende Loeschantraege, Retention-Vorschlaege, juengste
    Statistik-Snapshots, Speicherbelegung der Einrichtung (Refs #2129).
    Legal Holds bleiben reines Superadmin-Werkzeug
    (``system_legal_hold_list``, ``SuperAdminRequiredMixin``) — die frueher
    hier mitgelieferte ``active_legal_holds``-Kachel war fuer die Leitung
    hinter dem #1735-Guard immer unsichtbar und ist entfernt (Refs #1759).
    """
    pending_deletion_requests = (
        DeletionRequest.objects.for_facility(facility).filter(status=DeletionRequest.Status.PENDING).count()
    )

    pending_retention_proposals = (
        RetentionProposal.objects.for_facility(facility).filter(status=RetentionProposal.Status.PENDING).count()
    )

    # Refs #2425: a deletion run also writes the running month's row (it holds
    # the counts of deleted events until the month-end snapshot run); the card
    # names the latest completed month.
    today = timezone.localdate()
    last_snapshot = (
        StatisticsSnapshot.objects.for_facility(facility)
        .filter(Q(year__lt=today.year) | Q(year=today.year, month__lt=today.month))
        .order_by("-year", "-month")
        .first()
    )

    # Refs #2129: Die Leitung hat eine eigene Arbeitszentrale — der
    # Verwaltungs-Hub mit derselben Zahl ist ``FacilityAdminRequiredMixin`` und
    # fuer sie unerreichbar. Ohne diese Karte erfuehre sie von einem vollen
    # Kontingent erst, wenn eine Fachkraft eine Datei nicht mehr hochladen kann.
    storage_usage = facility_storage_usage(facility)

    return {
        "pending_deletion_requests": pending_deletion_requests,
        "pending_retention_proposals": pending_retention_proposals,
        "last_snapshot": last_snapshot,
        "storage_usage": storage_usage,
        # Refs #2129: Der Warnzustand darf nicht allein an der
        # Textfarbe haengen (WCAG 1.4.1) — derselbe Satz wie in der
        # Warnliste der Anwendungsbetreuung, aus einer Quelle.
        "storage_warning": storage_warning(storage_usage),
    }


def facility_admin_dashboard_context(user, facility) -> dict:
    """Daten fuer die Facility-Admin-Arbeitszentrale.

    Karten: User ohne MFA, Settings-Warnungen (z.B. MFA nicht erzwungen,
    keine K-Anon), aktive Login-Lockouts.
    """
    # Refs #1816: Die Pruefung auf ein bestaetigtes TOTP-Device laeuft als
    # Subquery mit, statt je Nutzer die Python-Property
    # ``User.has_confirmed_totp_device`` zu befragen — die kostete eine eigene
    # Query pro aktivem Nutzer (N+1 auf einer Seite, die jede Leitung bei jedem
    # Aufruf von /verwaltung/ sieht).
    users_qs = User.objects.filter(facility=facility, is_active=True).annotate(
        hat_bestaetigtes_totp=Exists(EncryptedTOTPDevice.objects.filter(user=OuterRef("pk"), confirmed=True))
    )
    users_without_mfa = users_qs.filter(hat_bestaetigtes_totp=False).count()

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
                _("MFA-Pflicht (2FA) ist einrichtungsweit nicht aktiv — Login bleibt ohne zweiten Faktor möglich.")
            )
        if not settings_obj.retention_use_k_anonymization:
            warnings.append(
                _(
                    "K-Anonymisierung ist deaktiviert — Löschlauf entfernt Datensätze hart. "
                    "Falls Statistik-Erhalt gewünscht, in Settings aktivieren."
                )
            )

    # Refs #2129: Die Warnung haengt sich in die bestehende Liste statt eine
    # eigene UI-Sektion zu bauen — der orange Banner in ``verwaltung.html`` ist
    # bereits die Stelle, an der die Anwendungsbetreuung Konfigurationsprobleme
    # sieht. Bewusst nur bei erreichter Schwelle: eine Dauerwarnung ist keine.
    storage_usage = facility_storage_usage(facility)
    quota_warning = storage_warning(storage_usage)
    if quota_warning:
        warnings.append(quota_warning)

    return {
        "users_without_mfa": users_without_mfa,
        "users_total": users_qs.count(),
        "settings_warnings": warnings,
        "storage_usage": storage_usage,
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
