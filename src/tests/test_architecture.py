"""Architecture tests to guard against facility scoping regressions."""

import re
from pathlib import Path

import pytest


@pytest.mark.django_db
class TestFacilityScopingGuard:
    """Ensure views always scope queries to the current facility."""

    def test_no_unfiltered_objects_all_in_views(self):
        """Views must not use Model.objects.all() without facility filter."""
        views_dir = Path("src/core/views")
        violations = []
        for py_file in views_dir.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            source = py_file.read_text()
            # Check for .objects.all() which could be cross-facility
            if ".objects.all()" in source:
                violations.append(f"{py_file.name}: uses .objects.all()")
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
