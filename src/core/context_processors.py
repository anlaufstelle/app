"""Template context processors for Anlaufstelle."""

import datetime
import os
from functools import lru_cache
from urllib.parse import urlsplit

from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core.models import WorkItem
from core.services.workitem_visibility import workitems_for_read


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
    """Expose source-code settings and authentication state for the footer.

    ``SOURCE_CODE_URL`` is rendered by the AGPL Section 13 footer link.
    ``SOURCE_CODE_VERSION`` remains in the context but is never rendered in the
    public footer; the GDPR document service reads it directly from settings.
    Refs #835, #2532.

    Refs #1050, #2286: app_version/app_build remain in the context, but the
    footer renders version/build/help only with the authenticated layout flag
    and ``session_fully_authenticated``: authenticated, no pending second
    factor, and no forced password change.
    """
    from core.services.security.offline_keys import offline_key_max_age
    from core.services.security.session_state import session_fully_authenticated

    project_url = getattr(settings, "PROJECT_WEBSITE_URL", "")
    try:
        parsed_url = urlsplit(project_url)
        if parsed_url.scheme.lower() not in {"http", "https"} or not parsed_url.hostname:
            project_url = ""
    except ValueError:
        project_url = ""
    app_version, app_build = _app_version_info()
    return {
        "SOURCE_CODE_URL": settings.SOURCE_CODE_URL,
        "PROJECT_WEBSITE_URL": project_url,
        "SOURCE_CODE_VERSION": settings.SOURCE_CODE_VERSION,
        "app_version": app_version,
        "app_build": app_build,
        "session_fully_authenticated": session_fully_authenticated(request),
        "offline_key_max_age": offline_key_max_age(getattr(request, "current_facility", None)),
    }


def legal_pages(request):
    """Expose only legal-page links whose targets are available (Refs #2076)."""
    if getattr(settings, "DEMO_MODE", False):
        return {
            "legal_imprint_url": getattr(settings, "DEMO_LEGAL_IMPRINT_URL", ""),
            "legal_privacy_url": getattr(settings, "DEMO_LEGAL_PRIVACY_URL", ""),
        }

    from core.views.legal import configured_legal_file_available

    return {
        "legal_imprint_url": (reverse("demo_imprint") if configured_legal_file_available("LEGAL_IMPRINT_FILE") else ""),
        "legal_privacy_url": (reverse("demo_privacy") if configured_legal_file_available("LEGAL_PRIVACY_FILE") else ""),
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

    base_qs = workitems_for_read(facility).filter(active_filter).filter(user_filter)

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
    """Expose demo credentials, scenario account hints and the next hourly reset."""
    if not getattr(settings, "DEMO_MODE", False):
        return {"demo_mode": False}
    from core.demo import DEMO_OFFLINE_BOOTSTRAP_ERROR, DEMO_OFFLINE_BOOTSTRAP_PENDING, DEMO_PASSWORD
    from core.models import User
    from core.seed.constants import USER_TEMPLATES
    from core.seed.scenarios import SCENARIOS
    from core.seed.users import SOLO_USER_TEMPLATES

    logins = [{**login, "role": User.Role(login["role"]).label} for login in getattr(settings, "DEMO_LOGINS", [])]
    login_scenarios = []
    for index, scenario in enumerate(SCENARIOS):
        suffix = f"_{index}" if index else ""
        templates = SOLO_USER_TEMPLATES if scenario.solo else USER_TEMPLATES
        login_scenarios.append(
            {
                "facility_name": scenario.facility_name,
                "suffix": suffix,
                "solo": scenario.solo,
                "usernames": [f"{username}{suffix}" for username, *_ in templates],
            }
        )

    session = getattr(request, "session", {})
    # Error pages and bare requests may render without the authentication middleware.
    user = getattr(request, "user", None)
    bootstrap_pending = bool(user is not None and user.is_authenticated) and session.get(
        DEMO_OFFLINE_BOOTSTRAP_PENDING, False
    )
    now = timezone.localtime()
    next_reset = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
    return {
        "demo_mode": True,
        "demo_logins": logins,
        "demo_login_scenarios": login_scenarios,
        "demo_password": DEMO_PASSWORD,
        "demo_offline_password": DEMO_PASSWORD if bootstrap_pending else "",
        "demo_offline_bootstrap_error": DEMO_OFFLINE_BOOTSTRAP_ERROR,
        "demo_next_reset": next_reset,
    }
