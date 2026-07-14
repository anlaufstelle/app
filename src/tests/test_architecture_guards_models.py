"""Architecture-Guards — Model-Guards (Facility-Scoping, Event-Access-Policy) (Refs #929)."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture


_GET_OBJECT_OR_404_CALL = re.compile(r"get_object_or_404\s*\(")
_MODEL_TOKEN = re.compile(r"\(\s*([A-Za-z_][A-Za-z0-9_]*)")
_FACILITY_KWARG = re.compile(r"\bfacility\s*=")


def _extract_balanced_call(source: str, open_paren_idx: int) -> str:
    """Extrahiert den ``get_object_or_404(...)``-Aufruf per Klammer-Balance.

    Multiline-Aufrufe (die typische ``get_object_or_404(\\n    Model,\\n    pk=pk,\\n)``-
    Form) werden dadurch vollstaendig erfasst, unabhaengig von Zeilenumbruechen.
    """
    depth = 0
    for i in range(open_paren_idx, len(source)):
        ch = source[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return source[open_paren_idx : i + 1]
    return source[open_paren_idx:]


def _facility_scoped_model_names() -> set[str]:
    """Model-Namen mit direktem ``facility``-Feld — dynamisch aus core.models
    introspiziert statt hart codiert, damit neue Models automatisch erfasst
    werden."""
    import core.models as core_models

    names = set()
    for name in core_models.__all__:
        model = getattr(core_models, name)
        field_names = {f.name for f in model._meta.get_fields()}
        if "facility" in field_names:
            names.add(name)
    return names


def find_unscoped_get_object_or_404(scan_dir: Path, *, whitelist_prefixes: tuple[str, ...] = ()) -> list[str]:
    """Findet ``get_object_or_404(<FacilityModel>, ...)``-Aufrufe ohne ``facility=``.

    Ein Model gilt als facility-gescopt, wenn es (per Introspektion) einen
    direkten ``facility``-Feldnamen traegt. Kind-Models ohne eigenen FK
    (Episode, OutcomeGoal, Milestone, ...) fallen automatisch nicht unter
    diesen Guard — sie sind bereits transitiv ueber ihr Parent-Objekt
    gescoped (``case=``/``goal__case=``).
    """
    scoped_names = _facility_scoped_model_names()
    violations: list[str] = []
    for py_file in sorted(scan_dir.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        rel = py_file.relative_to(scan_dir).as_posix()
        if any(rel.startswith(prefix) for prefix in whitelist_prefixes):
            continue
        source = py_file.read_text()
        for match in _GET_OBJECT_OR_404_CALL.finditer(source):
            call_text = _extract_balanced_call(source, match.end() - 1)
            model_match = _MODEL_TOKEN.match(call_text)
            if not model_match:
                continue
            model_name = model_match.group(1)
            if model_name not in scoped_names:
                continue
            if _FACILITY_KWARG.search(call_text):
                continue
            line_no = source.count("\n", 0, match.start()) + 1
            violations.append(f"{rel}:{line_no}: get_object_or_404({model_name}, ...) ohne facility=")
    return violations


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


class TestScopedObjectGuard:
    """Kein ``get_object_or_404(<FacilityModel>, ...)`` ohne ``facility=``-Scope (Refs #1346).

    ``core.services.scoping.get_scoped_object`` macht das Facility-Filter fuer
    neue Call-Sites strukturell unvergesslich; dieser Guard verhindert das
    Wiederauftauchen des alten copy-paste-Musters OHNE Filter — genau das
    latente IDOR-Risiko, das der Scoping-Review #1346 identifiziert hat. Ein
    Model gilt als "facility-gescopt", wenn es (dynamisch introspektiert)
    einen direkten ``facility``-Feldnamen traegt. Transitive Kind-Lookups
    (``Episode``/``OutcomeGoal``/``Milestone`` ueber ``case=``/``goal__case=``)
    haben keinen eigenen FK und werden dadurch automatisch nicht erfasst.
    """

    _WHITELIST_PREFIXES = ("system/",)

    def test_synthetic_violation_is_detected(self, tmp_path):
        (tmp_path / "offender.py").write_text(
            "from django.shortcuts import get_object_or_404\n"
            "from core.models import Client\n\n"
            "def view(request, pk):\n"
            "    return get_object_or_404(Client, pk=pk)\n"
        )
        violations = find_unscoped_get_object_or_404(tmp_path)
        assert violations
        assert "offender.py" in violations[0]

    def test_scoped_call_with_facility_kwarg_is_not_flagged(self, tmp_path):
        (tmp_path / "ok.py").write_text(
            "from django.shortcuts import get_object_or_404\n"
            "from core.models import Client\n\n"
            "def view(request, pk):\n"
            "    return get_object_or_404(Client, pk=pk, facility=request.current_facility)\n"
        )
        assert not find_unscoped_get_object_or_404(tmp_path)

    def test_transitive_case_lookup_is_not_flagged(self, tmp_path):
        (tmp_path / "ok.py").write_text(
            "from django.shortcuts import get_object_or_404\n"
            "from core.models.episode import Episode\n\n"
            "def view(request, case, pk):\n"
            "    return get_object_or_404(Episode, pk=pk, case=case)\n"
        )
        assert not find_unscoped_get_object_or_404(tmp_path)

    def test_whitelisted_prefix_is_skipped(self, tmp_path):
        system_dir = tmp_path / "system"
        system_dir.mkdir()
        (system_dir / "audit.py").write_text(
            "from django.shortcuts import get_object_or_404\n"
            "from core.models import AuditLog\n\n"
            "def view(request, pk):\n"
            "    return get_object_or_404(AuditLog, pk=pk)\n"
        )
        assert not find_unscoped_get_object_or_404(tmp_path, whitelist_prefixes=self._WHITELIST_PREFIXES)

    def test_multiline_call_without_facility_is_detected(self, tmp_path):
        (tmp_path / "offender.py").write_text(
            "from django.shortcuts import get_object_or_404\n"
            "from core.models import WorkItem\n\n"
            "def view(request, pk):\n"
            "    return get_object_or_404(\n"
            "        WorkItem.objects.select_for_update(),\n"
            "        pk=pk,\n"
            "    )\n"
        )
        assert find_unscoped_get_object_or_404(tmp_path)

    def test_multiline_call_with_facility_is_not_flagged(self, tmp_path):
        (tmp_path / "ok.py").write_text(
            "from django.shortcuts import get_object_or_404\n"
            "from core.models import WorkItem\n\n"
            "def view(request, pk, facility):\n"
            "    return get_object_or_404(\n"
            "        WorkItem.objects.select_for_update(),\n"
            "        pk=pk,\n"
            "        facility=facility,\n"
            "    )\n"
        )
        assert not find_unscoped_get_object_or_404(tmp_path)

    def test_no_unscoped_get_object_or_404_in_views(self):
        views_dir = Path("src/core/views")
        violations = find_unscoped_get_object_or_404(views_dir, whitelist_prefixes=self._WHITELIST_PREFIXES)
        assert not violations, (
            "get_object_or_404(<FacilityModel>, ...) ohne facility=-Kwarg gefunden — "
            "core.services.scoping.get_scoped_object nutzen (Refs #1346).\n"
            f"Betroffen: {violations}"
        )


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
                    "use core.services.compliance.sensitivity.get_visible_event_or_404 instead"
                )
        assert not violations, f"Event access policy violations: {violations}"
