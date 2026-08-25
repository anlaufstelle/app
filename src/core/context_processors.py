"""Template context processors for Anlaufstelle."""

import datetime
import os
from functools import lru_cache

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from core.models import WorkItem


@lru_cache(maxsize=1)
def _app_version_info():
    """SemVer aus ``pyproject.toml`` + optionaler Build-Zusatz (Refs #1050).

    Die Image-Builds backen das ENV ``APP_VERSION`` ein (Dev-Image:
    ``main-<sha>``, Release: ``v<semver>``). Auf Releases ist der Wert
    redundant zum SemVer und wird unterdrueckt — sichtbar bleibt der
    Build-Zusatz nur auf dev-Builds.
    """
    from core.services.system.health import app_versions

    semver = app_versions()["app_version"]
    # Refs #1504: der ENV-Fallback (kein lesbares pyproject.toml, z.B. in
    # Container-Deploys ohne die pyproject-COPY) traegt per ADR-028 ein
    # fuehrendes "v" (z.B. "v0.20.0"); der lokale pyproject.toml-Lesepfad
    # nicht. base.html prependet im Footer immer genau ein "v" -- ohne
    # Normalisierung wuerde daraus "vv0.20.0".
    semver = "" if semver == "unknown" else semver.removeprefix("v")
    build = os.environ.get("APP_VERSION", "")
    if build in ("", "dev", "unknown", semver, f"v{semver}"):
        build = ""
    return semver, build


def source_code(request):
    """Refs #835 (C-68): exponiere SOURCE_CODE_URL/SOURCE_CODE_VERSION
    fuer den AGPL-§13-Footer. Wird in jedem Template gerendert.
    Refs #1050: zusaetzlich app_version/app_build fuer die
    Versionsanzeige im eingeloggten Footer.
    """
    app_version, app_build = _app_version_info()
    return {
        "SOURCE_CODE_URL": settings.SOURCE_CODE_URL,
        "SOURCE_CODE_VERSION": settings.SOURCE_CODE_VERSION,
        "app_version": app_version,
        "app_build": app_build,
    }


def workitem_counts(request):
    """Open and overdue WorkItem counts for navigation badges.

    Refs #1611: Der Badge zählt bewusst eine **feste** Sicht — offene bzw. in
    Bearbeitung befindliche Aufgaben, die mir zugewiesen oder unzugewiesen sind
    — und ist von der Filterleiste der Aufgabenübersicht **entkoppelt**. Er
    beantwortet "Wie viel liegt insgesamt für mich an?", nicht "Wie viele
    Treffer hat mein aktueller Filter?". Deshalb kann neben einer durch einen
    Filter geleerten Liste weiterhin z.B. "Aufgaben 3" stehen. Ob Badge und
    Liste stattdessen gekoppelt werden sollen, ist eine offene
    Produktentscheidung (Trade-off im PR zu #1611 benannt) und wird hier
    absichtlich NICHT eigenmächtig geändert.
    """
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {}

    # HTMX partials never render the navigation — badge count unnecessary.
    if request.headers.get("HX-Request"):
        return {}

    facility = getattr(request, "current_facility", None)
    if not facility:
        return {}

    active_filter = Q(status__in=[WorkItem.Status.OPEN, WorkItem.Status.IN_PROGRESS])
    user_filter = Q(assigned_to=request.user) | Q(assigned_to__isnull=True)

    base_qs = WorkItem.objects.for_facility(facility).filter(active_filter).filter(user_filter)

    count = base_qs.count()
    # Refs #1478 (COD-02): Überfälligkeits-Anker über ``timezone.localdate()``
    # (Europe/Berlin) statt des naiven, server-lokalen ``date.today()`` — sonst
    # weicht der Nav-Badge nahe Mitternacht um einen Tag von Dashboard/Focus-Box
    # ab, die bereits die lokale Kalenderdatum-Grenze nutzen (analog #1191).
    overdue_count = base_qs.filter(due_date__lt=timezone.localdate()).count()

    return {
        "open_workitems_count": count,
        "overdue_workitems_count": overdue_count,
        "current_facility": facility,
    }


def review_counts(request):
    """Offene Prüf-/Arbeitsvorgänge für die Navigations-Badges (Refs #1984).

    Umsetzung aus #1870/ADR-046: datensparsame In-App-Zähler statt
    Notification-Modell — nur Anzahlen, nie Pseudonym/Titel/Grund, kein
    Gelesen-Zustand. Die Zähler spiegeln EXAKT die Sichtmengen der
    Zielansichten:

    - Löschanträge (``DeletionRequestListView``): Leitung/Genehmiger:innen
      (``sees_all_deletion_requests``) zählen alle PENDING der Einrichtung,
      reine Antragsteller:innen nur die eigenen; wer keinen Listen-Zugang hat,
      bekommt KEINEN Key — ein 0-Badge wäre zwar unsichtbar, aber der Zähler
      dürfte nicht einmal intern die Existenz fremder Anträge abfragen.
    - Berichtigungen (``RectificationListView``): Leitung/Anwendungsbetreuung;
      exakt die ``pending``-Definition der Liste — Anträge zu widerrufenen/
      inaktiven Personen sind kein prüfbarer Vorgang mehr und zählen nicht
      (sonst zeigte der Badge dauerhaft eine Zahl ohne Listenzeile).

    ``datenschutz_review_count`` ist die Summe fürs Hub-Anker-Badge (im
    Processor berechnet — Template-Addition über bedingt vorhandene Keys wäre
    fehleranfällig). Query-Budget: läuft einmal pro Request (Context-
    Processor); ``can_access_deletion_requests`` teilt sich die
    ``cached_property``-Query mit dem Nav-Gate (Refs #1755), HTMX-Partials
    rendern die Navigation nie und werden wie in ``workitem_counts``
    übersprungen.
    """
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {}
    if request.headers.get("HX-Request"):
        return {}
    facility = getattr(request, "current_facility", None)
    if not facility:
        return {}

    from core.models import RectificationRequest
    from core.models.workitem import DeletionRequest

    user = request.user
    counts = {}
    if user.can_access_deletion_requests:
        deletion_qs = DeletionRequest.objects.for_facility(facility).filter(status=DeletionRequest.Status.PENDING)
        if not user.sees_all_deletion_requests:
            deletion_qs = deletion_qs.filter(requested_by=user)
        counts["open_deletion_requests_count"] = deletion_qs.count()
    if user.is_lead_or_admin:
        counts["open_rectification_requests_count"] = (
            RectificationRequest.objects.for_facility(facility)
            .filter(
                status=RectificationRequest.Status.PENDING,
                target_client__is_deleted=False,
                target_client__is_active=True,
            )
            .count()
        )
    if counts:
        counts["datenschutz_review_count"] = sum(counts.values())
    return counts


def mfa_setup_reminder(request):
    """Refs #689: Reminder-Banner-Kontext für die 2FA-Setup-Grace-Frist.

    Zeigt ab ``MFA_SETUP_REMINDER_DAYS`` Resttagen einen Banner mit Resttage-
    Zähler, solange ein grace-behaftet rollen-erzwungener Nutzer (staff/assistant
    [, lead], Refs #689) noch kein bestätigtes 2FA-Gerät eingerichtet hat und die
    Grace-Frist läuft. Sofort erzwungene Zwänge — bewusst gesetzt
    (``mfa_required``/facility-weit) ODER privilegierte Rolle
    (super_admin/facility_admin) — haben keine Frist: für sie greift die
    Middleware sofort (Redirect ohne Gerät), ein Countdown-Banner wäre sinnlos.
    HTMX-Partials rendern die Navigation/den Banner nicht — dort No-Op (spart die
    Queries).
    """
    from core.constants import MFA_SETUP_REMINDER_DAYS

    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {}
    if request.headers.get("HX-Request"):
        return {}
    user = request.user
    deadline = getattr(user, "mfa_setup_deadline", None)
    if deadline is None or not user.mfa_enforced_by_role_grace or user.has_confirmed_mfa_device:
        return {}
    now = timezone.now()
    if deadline <= now:
        # Frist abgelaufen — die Middleware erzwingt bereits den Setup, kein
        # Countdown mehr.
        return {}
    # Aufgerundete volle Resttage: ein angebrochener Tag zählt als ganzer
    # (z.B. 4h vor Ablauf = noch "1 Tag").
    delta = deadline - now
    days_remaining = delta.days + (1 if delta.seconds or delta.microseconds else 0)
    if days_remaining > MFA_SETUP_REMINDER_DAYS:
        return {"mfa_setup_show_banner": False}
    return {
        "mfa_setup_show_banner": True,
        "mfa_setup_days_remaining": days_remaining,
        "mfa_setup_deadline": deadline,
    }


def umami(request):
    """Refs #1602/#1604: Umami-Web-Analytics-Konfiguration in den Template-Context.

    Standardmaessig AUS (``UMAMI_WEBSITE_ID=None``) — lokale dev/test/prod
    bleiben tracking-frei. Konkrete Werte tragen nur devlive
    (dev.anlaufstelle.app) und demo (demo.anlaufstelle.app). Das
    base.html-Include ``partials/_umami.html`` rendert den Tracker nur, wenn
    eine Website-ID gesetzt ist; ``login.html`` erbt bewusst nicht von
    base.html und bleibt damit tracking-frei.
    """
    return {
        "UMAMI_WEBSITE_ID": getattr(settings, "UMAMI_WEBSITE_ID", None),
        "UMAMI_SCRIPT_SRC": getattr(settings, "UMAMI_SCRIPT_SRC", ""),
        "UMAMI_RECORDER_SRC": getattr(settings, "UMAMI_RECORDER_SRC", ""),
        "UMAMI_REQUIRE_CONSENT": getattr(settings, "UMAMI_REQUIRE_CONSENT", False),
        "DEMO_LEGAL_IMPRINT_URL": getattr(settings, "DEMO_LEGAL_IMPRINT_URL", ""),
        "DEMO_LEGAL_PRIVACY_URL": getattr(settings, "DEMO_LEGAL_PRIVACY_URL", ""),
    }


def demo(request):
    """Refs #1062: Demo-Banner + Login-Zugangsdaten-Panel (nur bei DEMO_MODE).

    Exponiert die oeffentlich kommunizierten Seed-Logins und den naechsten
    Reset-Zeitpunkt fuer die oeffentliche Demo-Instanz. Der stuendliche
    Reset-Timer laeuft ``OnCalendar=hourly`` -> naechster Reset ist die
    kommende volle Stunde. Ausserhalb der Demo (Default) ein No-Op.
    """
    if not getattr(settings, "DEMO_MODE", False):
        return {"demo_mode": False}
    now = timezone.localtime()
    next_reset = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
    return {
        "demo_mode": True,
        "demo_logins": getattr(settings, "DEMO_LOGINS", []),
        "demo_password": getattr(settings, "DEMO_PASSWORD", ""),
        "demo_next_reset": next_reset,
    }
