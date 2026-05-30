"""Architecture-Guards — Model-Guards (Facility-Scoping, Event-Access-Policy) (Refs Welle 6 #929)."""

import re
from pathlib import Path
from typing import ClassVar

import pytest


@pytest.mark.django_db
class TestFacilityScopingGuard:
    """Ensure views always scope queries to the current facility."""

    # Refs #867 / #904: das ``views/system/``-Subpackage ist der
    # Superadmin-/System-Bereich. Cross-facility-Lookups sind dort *die
    # Aufgabe* — RLS-Bypass via ``app.is_super_admin`` (Migration 0085)
    # gibt super_admin alle Zeilen frei, der Manager-Filter waere
    # kontraproduktiv. Whitelist statt Manager-Workaround, damit die
    # Absicht des Subpackages explizit bleibt.
    #
    # Pfade relativ zu ``src/core/views/`` — alles unter ``system/``
    # ist erlaubt; Top-Level-System-Datei gibt es nach #904 nicht mehr.
    _OBJECTS_ALL_WHITELIST_PREFIXES = ("system/",)

    def test_no_unfiltered_objects_all_in_views(self):
        """Views must not use Model.objects.all() without facility filter."""
        views_dir = Path("src/core/views")
        violations = []
        for py_file in views_dir.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            rel = py_file.relative_to(views_dir).as_posix()
            if any(rel.startswith(prefix) for prefix in self._OBJECTS_ALL_WHITELIST_PREFIXES):
                continue
            source = py_file.read_text()
            # Check for .objects.all() which could be cross-facility
            if ".objects.all()" in source:
                violations.append(f"{rel}: uses .objects.all()")
        assert not violations, f"Facility scoping violations: {violations}"


class TestEventAccessPolicyGuard:
    """Direct Event loads must go through get_visible_event_or_404.

    Reason: views bypassing the central loader leak the existence of
    higher-sensitivity events to lower roles via 403/masked-200 responses.
    """

    _EVENT_GET_PATTERN = re.compile(
        r"get_object_or_404\s*\(\s*Event(\s|\.|,|\()",
    )

    def test_no_direct_event_get_object_or_404_in_views(self):
        views_dir = Path("src/core/views")
        violations = []
        for py_file in views_dir.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            source = py_file.read_text()
            if self._EVENT_GET_PATTERN.search(source):
                violations.append(
                    f"{py_file.name}: direct get_object_or_404(Event, ...) — "
                    "use core.services.sensitivity.get_visible_event_or_404 instead"
                )
        assert not violations, f"Event access policy violations: {violations}"
