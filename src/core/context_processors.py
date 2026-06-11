"""Template context processors for Anlaufstelle."""

import datetime
import os
from functools import lru_cache

from django.conf import settings
from django.db.models import Q

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
    if semver == "unknown":
        semver = ""
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
    """Open and overdue WorkItem counts for navigation badges."""
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
    overdue_count = base_qs.filter(due_date__lt=datetime.date.today()).count()

    return {
        "open_workitems_count": count,
        "overdue_workitems_count": overdue_count,
        "current_facility": facility,
    }
