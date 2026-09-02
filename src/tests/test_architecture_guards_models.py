"""Architecture-Guards — Model-Guards (Facility-Scoping, Event-Access-Policy) (Refs #929)."""

import ast
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


# --- Tenant-Model-Scope-Guard (Refs #1658) -----------------------------------
#
# ``core_user``/``core_facility``/``core_organization`` liegen ausserhalb von
# RLS (Auth-Grenze, siehe ``NOT_RLS_SCOPED`` in ``src/tests/test_rls.py``).
# Fuer diese drei Models ist der explizite Filter die einzige Huerde.
_TENANT_MODELS = frozenset({"User", "Facility", "Organization"})

# Scope-Evidenz: taucht eines dieser Tokens im *entparsten Quelltext der
# Manager-Kette* auf, gilt der Zugriff als gescoped. Bewusst tokenbasiert
# (statt Keyword-Introspektion), damit auch ``Q(facility=...)``-Ausdruecke,
# ``**{"facility": …}``-Lookups und Follow-Filter in derselben Kette zaehlen.
# ``request.user``/``current_facility`` decken den Selbst-Scope ab
# (``filter(pk=request.user.pk)``) — ein reines ``pk=<irgendwas>`` gilt
# absichtlich NICHT als Scope, genau das ist das IDOR-Muster.
_SCOPE_EVIDENCE = (
    "facility=",
    "facility_id=",
    "facility__",
    "current_facility",
    "request.user",
)


def _parent_map(tree: "ast.AST") -> dict:
    """child -> parent fuer den gesamten Baum (ast kennt keine Rueckwaertskante)."""
    parents: dict = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_qualname(parents: dict, node: "ast.AST") -> str:
    """``Klasse.methode`` des umschliessenden def/class — stabiler als Zeilennummern."""
    parts: list[str] = []
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parts.append(current.name)
        current = parents.get(current)
    return ".".join(reversed(parts)) or "<module>"


def _chain_top(parents: dict, node: "ast.AST") -> "ast.AST":
    """Aeusserster Ausdruck der Attribut-/Call-Kette ab ``node``.

    ``User.objects`` -> ``User.objects.filter(...).order_by(...)``. Damit
    erfasst der Guard multi-line-Ketten vollstaendig, ohne Regex-Heuristik.
    """
    current = node
    while True:
        parent = parents.get(current)
        extends_chain = (isinstance(parent, ast.Attribute) and parent.value is current) or (
            isinstance(parent, ast.Call) and parent.func is current
        )
        if not extends_chain:
            return current
        current = parent


def find_unscoped_tenant_access(
    scan_dir: Path,
    *,
    allowlist: dict[str, str] | None = None,
    whitelist_prefixes: tuple[str, ...] = (),
) -> list[str]:
    """Findet ``User``/``Facility``/``Organization``-Manager-Zugriffe ohne Facility-Scope.

    AST-basiert: gesucht wird der Knoten ``<Model>.objects``; bewertet wird die
    gesamte umschliessende Kette. Kommentare, Docstrings und Strings koennen
    dadurch nicht faelschlich matchen (anders als bei reiner Textsuche).

    ``allowlist``-Schluessel: ``"<relpath>:<qualname>:<Model>"``, Wert = kurze
    Begruendung. Die Granularitaet ist bewusst zeilenunabhaengig, aber pro
    Model getrennt — ein neuer ``User``-Zugriff in einer nur fuer ``Facility``
    freigegebenen Funktion faellt weiterhin auf.
    """
    allowlist = allowlist or {}
    violations: list[str] = []
    for py_file in sorted(scan_dir.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        rel = py_file.relative_to(scan_dir).as_posix()
        if any(rel.startswith(prefix) for prefix in whitelist_prefixes):
            continue
        tree = ast.parse(py_file.read_text())
        parents = _parent_map(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute) and node.attr == "objects"):
                continue
            if not (isinstance(node.value, ast.Name) and node.value.id in _TENANT_MODELS):
                continue
            model = node.value.id
            chain_source = ast.unparse(_chain_top(parents, node))
            if any(token in chain_source for token in _SCOPE_EVIDENCE):
                continue
            identifier = f"{rel}:{_enclosing_qualname(parents, node)}:{model}"
            if identifier in allowlist:
                continue
            violations.append(f"{identifier} (Zeile {node.lineno}): {chain_source[:120]}")
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


class TestTenantModelScopeGuard:
    """Kein ungescopter ``User``/``Facility``/``Organization``-Manager-Zugriff (Refs #1658).

    ``core_user``, ``core_facility`` und ``core_organization`` liegen bewusst
    **ausserhalb** von RLS (Auth-Grenze — siehe ``NOT_RLS_SCOPED`` in
    ``src/tests/test_rls.py`` und ADR-005). Fuer alle anderen facility-
    tragenden Tabellen faengt Postgres ein vergessenes Filter noch ab; hier
    ist der explizite ``facility=``-Filter die **einzige** Huerde — und auf
    SQLite (dev/test) gibt es ohnehin keinen RLS-Backstop.

    Der Guard scannt die request-nahen Schichten (``views/``, ``forms/``,
    ``services/``) nach ``<Model>.objects``-Ketten und verlangt, dass die
    Kette selbst eine Scope-Evidenz traegt (``facility=``/``facility__``/
    ``request.user``/``current_facility``). Alles andere muss mit Begruendung
    in ``_ALLOWLIST`` stehen.
    """

    def test_synthetic_unscoped_filter_is_detected(self, tmp_path):
        (tmp_path / "offender.py").write_text(
            "from core.models import User\n\ndef view(request):\n    return User.objects.filter(is_active=True)\n"
        )
        violations = find_unscoped_tenant_access(tmp_path)
        assert violations
        assert "offender.py" in violations[0]
        assert "User.objects" in violations[0]

    def test_synthetic_unscoped_facility_access_is_detected(self, tmp_path):
        (tmp_path / "offender.py").write_text(
            "from core.models import Facility\n\ndef view(request):\n    return Facility.objects.all()\n"
        )
        assert find_unscoped_tenant_access(tmp_path)

    def test_facility_kwarg_is_not_flagged(self, tmp_path):
        (tmp_path / "ok.py").write_text(
            "from core.models import User\n\n"
            "def view(request, facility):\n"
            "    return User.objects.filter(facility=facility, is_active=True)\n"
        )
        assert not find_unscoped_tenant_access(tmp_path)

    def test_multiline_chain_with_facility_kwarg_is_not_flagged(self, tmp_path):
        (tmp_path / "ok.py").write_text(
            "from core.models import User\n\n"
            "def view(request, facility):\n"
            "    return (\n"
            "        User.objects.filter(\n"
            "            facility=facility,\n"
            "        )\n"
            "        .order_by('username')\n"
            "    )\n"
        )
        assert not find_unscoped_tenant_access(tmp_path)

    def test_self_scoped_request_user_is_not_flagged(self, tmp_path):
        (tmp_path / "ok.py").write_text(
            "from core.models import User\n\n"
            "def view(request):\n"
            "    return User.objects.filter(pk=request.user.pk).update(preferred_language='de')\n"
        )
        assert not find_unscoped_tenant_access(tmp_path)

    def test_non_tenant_model_is_not_flagged(self, tmp_path):
        (tmp_path / "ok.py").write_text(
            "from core.models import Client\n\ndef view(request):\n    return Client.objects.filter(is_active=True)\n"
        )
        assert not find_unscoped_tenant_access(tmp_path)

    def test_allowlisted_call_site_is_skipped(self, tmp_path):
        (tmp_path / "auth.py").write_text(
            "from core.models import User\n\n"
            "class ResetView:\n"
            "    def form_valid(self, form):\n"
            "        return User.objects.filter(email__iexact='a@b.c').first()\n"
        )
        assert find_unscoped_tenant_access(tmp_path)
        assert not find_unscoped_tenant_access(
            tmp_path,
            allowlist={"auth.py:ResetView.form_valid:User": "pre-auth"},
        )

    def test_allowlist_is_per_model_not_per_function(self, tmp_path):
        """Ein Freibrief fuer ``Facility`` deckt keinen ``User``-Zugriff mit ab."""
        (tmp_path / "mixed.py").write_text(
            "from core.models import Facility, User\n\n"
            "def report(request):\n"
            "    return Facility.objects.count(), User.objects.count()\n"
        )
        remaining = find_unscoped_tenant_access(
            tmp_path,
            allowlist={"mixed.py:report:Facility": "systemweite Kennzahl"},
        )
        assert len(remaining) == 1
        assert "mixed.py:report:User" in remaining[0]

    def test_whitelisted_prefix_is_skipped(self, tmp_path):
        system_dir = tmp_path / "system"
        system_dir.mkdir()
        (system_dir / "dashboard.py").write_text(
            "from core.models import Facility\n\ndef view(request):\n    return Facility.objects.all()\n"
        )
        assert not find_unscoped_tenant_access(tmp_path, whitelist_prefixes=("system/",))

    # --- Bestandsaufnahme: Scan-Bereiche + begruendete Allowlist ------------
    #
    # Gescannt werden die drei request-nahen Schichten. ``views/system/`` ist
    # wie bei ``TestFacilityScopingGuard._OBJECTS_ALL_WHITELIST_PREFIXES``
    # ausgenommen (Refs #867/#904): cross-facility-Sicht ist dort die Aufgabe
    # des Subpackages, RLS gibt super_admin per ``app.is_super_admin`` ohnehin
    # alle Zeilen frei.
    _VIEWS_DIR = Path("src/core/views")
    _FORMS_DIR = Path("src/core/forms")
    _SERVICES_DIR = Path("src/core/services")
    _VIEW_WHITELIST_PREFIXES = ("system/",)

    # Pre-Auth-Identitaetslookups: zum Zeitpunkt des Lookups existiert noch
    # keine Session und damit kein ``request.current_facility`` — die Facility
    # ist erst das *Ergebnis* der Identifikation, nicht ihre Voraussetzung.
    _VIEWS_ALLOWLIST = {
        "auth.py:RateLimitedPasswordResetView.form_valid:User": (
            "Passwort-Reset: E-Mail-Lookup vor jeder Authentifizierung; Anti-Enumeration "
            "ist separat geloest (identische Antwort mit/ohne Treffer, Refs #598/#791)."
        ),
        "lockout_recovery.py:LockoutRecoveryRequestView.form_valid:User": (
            "Lockout-Recovery per E-Mail — pre-auth, kein Facility-Kontext vorhanden."
        ),
        "lockout_recovery.py:LockoutRecoveryBackupCodeView.form_valid:User": (
            "Backup-Code-Recovery per Benutzername — pre-auth, kein Facility-Kontext."
        ),
    }

    # Forms bauen ausschliesslich facility-gefilterte Auswahl-Querysets
    # (``assigned_to``/``lead_user``) — bewusst leer.
    _FORMS_ALLOWLIST: dict[str, str] = {}

    # Zwei legitime Kategorien: (1) deploymentweite Compliance-/Super-Admin-
    # Kennzahlen, (2) Identitaets-Reloads eines bereits serverseitig
    # aufgeloesten Subjekts per Primaerschluessel.
    _SERVICES_ALLOWLIST = {
        "compliance/app_superuser.py:_app_superuser_checks:User": (
            "Compliance-Check auf DB-Superuser — deploymentweit per Definition; "
            "gerendert nur im Super-Admin-Bereich (views/system/compliance.py)."
        ),
        "compliance/retention.py:_retention_config_checks:Facility": (
            "Aggregierte Warnung fuer explizit unbegrenzte Audit-Aufbewahrung — "
            "deploymentweit per Definition, vollstaendig in rls_bypass_for_read gekapselt "
            "und nur im Super-Admin-Compliance-Bereich gerendert (Refs #2053)."
        ),
        "compliance/mfa.py:_mfa_checks:User": (
            "MFA-Quote ueber alle MFA-pflichtigen Rollen — deploymentweite Kennzahl "
            "des Compliance-Dashboards, nicht facility-bezogen."
        ),
        "dashboard/main.py:super_admin_dashboard_context:Facility": (
            "Super-Admin-Dashboard: Anzahl aller Einrichtungen ist der Zweck der Kachel."
        ),
        "dashboard/main.py:super_admin_dashboard_context:User": (
            "Super-Admin-Dashboard: deploymentweite Nutzerzahl ist der Zweck der Kachel."
        ),
        "compliance/breach_detection.py:record_finding:User": (
            "Reload einer serverseitig ermittelten ``user_id`` aus dem Scan-Finding — "
            "kein Request-Input; die Facility steckt bereits im Finding."
        ),
        "security/credentials.py:rotate_offline_salt:User": (
            "Signalfreies ``update()`` auf die EIGENE, bereits aufgeloeste PK des Users "
            "(Salt-Rotation im Credential-Widerruf, Refs #1965) — kein Request-Input. "
            "``QuerySet.update()`` ist hier Pflicht, nicht Stil: die Rotation laeuft auch "
            "aus dem ``post_save``-Receiver des Rechteentzugs, ein ``save()`` wuerde sich "
            "selbst erneut ausloesen. Die Stelle lag bis #1965 in "
            "``signals/offline_invalidation.py`` und damit ausserhalb dieses Scans."
        ),
        "security/lockout_recovery.py:verify_recovery_token:User": (
            "Aufloesung der PK aus einem signierten Recovery-Token — pre-auth."
        ),
        "security/login_lockout.py:is_locked:User": (
            "``select_for_update()``-Reload des bereits aufgeloesten Users per eigener "
            "PK (Lock-Reihenfolge, Refs #1000) — kein Request-Input."
        ),
    }

    _FAILURE_HINT = (
        "Ungescopter Manager-Zugriff auf User/Facility/Organization gefunden. Diese drei "
        "Tabellen liegen ausserhalb von RLS (Auth-Grenze, siehe NOT_RLS_SCOPED in "
        "src/tests/test_rls.py) — der explizite Filter ist die einzige Huerde, und auf "
        "SQLite gibt es keinen DB-Backstop. Bitte ``facility=…`` ergaenzen (bzw. "
        "``core.services.scoping.get_scoped_object`` nutzen). Ist der Zugriff bewusst "
        "global, gehoert er mit Begruendung in die passende Allowlist von "
        "TestTenantModelScopeGuard.\nRefs #1658 / #1376.\nBetroffen: "
    )

    def test_no_unscoped_tenant_access_in_views(self):
        violations = find_unscoped_tenant_access(
            self._VIEWS_DIR,
            allowlist=self._VIEWS_ALLOWLIST,
            whitelist_prefixes=self._VIEW_WHITELIST_PREFIXES,
        )
        assert not violations, f"{self._FAILURE_HINT}{violations}"

    def test_no_unscoped_tenant_access_in_forms(self):
        violations = find_unscoped_tenant_access(self._FORMS_DIR, allowlist=self._FORMS_ALLOWLIST)
        assert not violations, f"{self._FAILURE_HINT}{violations}"

    def test_no_unscoped_tenant_access_in_services(self):
        violations = find_unscoped_tenant_access(self._SERVICES_DIR, allowlist=self._SERVICES_ALLOWLIST)
        assert not violations, f"{self._FAILURE_HINT}{violations}"

    @pytest.mark.parametrize(
        ("scan_dir_attr", "allowlist_attr", "prefixes_attr"),
        [
            ("_VIEWS_DIR", "_VIEWS_ALLOWLIST", "_VIEW_WHITELIST_PREFIXES"),
            ("_FORMS_DIR", "_FORMS_ALLOWLIST", None),
            ("_SERVICES_DIR", "_SERVICES_ALLOWLIST", None),
        ],
    )
    def test_allowlist_has_no_stale_entries(self, scan_dir_attr, allowlist_attr, prefixes_attr):
        """Jeder Allowlist-Eintrag muss einer real existierenden Call-Site entsprechen.

        Verhindert, dass ein Freibrief nach einem Refactor stehen bleibt und
        spaeter unbemerkt eine *neue* ungescopte Stelle mit abdeckt.
        """
        prefixes = getattr(self, prefixes_attr) if prefixes_attr else ()
        found = {
            violation.split(" (Zeile ")[0]
            for violation in find_unscoped_tenant_access(getattr(self, scan_dir_attr), whitelist_prefixes=prefixes)
        }
        stale = sorted(set(getattr(self, allowlist_attr)) - found)
        assert not stale, (
            "Veraltete Allowlist-Eintraege in TestTenantModelScopeGuard — die Call-Site "
            f"existiert nicht mehr oder ist inzwischen gescoped. Bitte entfernen: {stale}"
        )


# --- Slug-Bulk-Mutation-Guard (Refs #1709, #1058) ----------------------------
#
# FieldGroup/FieldTemplate/DocumentType.slug ist der M2-Paketformat-Identifier
# und seit Migration 0116 (FieldGroup/FieldTemplate) bzw. 0117 (DocumentType)
# per DB-Trigger unveraenderlich (ENABLE ALWAYS). Der Trigger ist die
# Wahrheit — dieser Guard verhindert nur, dass ein ORM-Bulk-Pfad, der ihn
# zwangslaeufig zum Scheitern bringen wuerde (statt frueh und lesbar im Code
# aufzufallen), ueberhaupt erst geschrieben wird.
_SLUG_IMMUTABLE_MODELS = frozenset({"FieldGroup", "FieldTemplate", "DocumentType"})


def _guarded_core_python_files(scan_dir: Path) -> list[Path]:
    """Core-``.py``-Dateien fuer den Slug-Bulk-Mutation-Guard.

    Migrationen sind bewusst ausgenommen: Backfill-``RunPython``-Schritte
    operieren auf eingefrorenen historischen Modellen (``apps.get_model``)
    und laufen kontrolliert VOR dem Anlegen des jeweiligen Slug-Immutable-
    Triggers fuer die betroffene Tabelle (z. B. Migration 0117 backfuellt
    ``DocumentType.slug`` per ``bulk_update`` und legt den Trigger erst
    danach an) — ein manuell reviewter Einzelfall pro Migration, kein
    Laufzeit-ORM-Bypass-Risiko wie im App-Code, gegen das dieser Guard
    schuetzt.
    """
    return [
        f
        for f in sorted(scan_dir.rglob("*.py"))
        if f.name != "__init__.py" and "migrations" not in f.relative_to(scan_dir).parts
    ]


def find_slug_mutating_bulk_calls(source: str) -> list[str]:
    """Findet ``.update(slug=...)`` / ``bulk_update(..., ["slug", ...])`` auf
    ``FieldGroup``/``FieldTemplate``/``DocumentType`` (siehe
    ``_SLUG_IMMUTABLE_MODELS``).

    AST-basiert (Muster ``find_unscoped_tenant_access``): Kommentare/Docstrings/
    Strings ausserhalb echter Call-Argumente koennen nicht faelschlich matchen.

    (a) ``<chain>.update(slug=…)``: die volle Call-Kette muss den Modellnamen
        ``FieldGroup``/``FieldTemplate``/``DocumentType`` als Token enthalten
        (z.B. ``FieldGroup.objects.filter(pk=x)``) — ein Update auf einem
        anderen Modell mit zufaellig gleichnamigem ``slug=``-Kwarg wird NICHT
        geflaggt.
    (b) ``<chain>.bulk_update(objs, [...])``: die Feldliste (2. Positions-
        argument) enthaelt das String-Literal ``"slug"`` UND die Call-Kette
        nennt eines der drei Modelle.
    """
    tree = ast.parse(source)
    parents = _parent_map(tree)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue

        if func.attr == "update":
            if not any(kw.arg == "slug" for kw in node.keywords):
                continue
        elif func.attr == "bulk_update":
            if len(node.args) < 2 or not isinstance(node.args[1], (ast.List, ast.Tuple)):
                continue
            if not any(isinstance(elt, ast.Constant) and elt.value == "slug" for elt in node.args[1].elts):
                continue
        else:
            continue

        chain_source = ast.unparse(_chain_top(parents, node))
        if not any(model in chain_source for model in _SLUG_IMMUTABLE_MODELS):
            continue
        violations.append(f"Zeile {node.lineno} ({_enclosing_qualname(parents, node)}): {chain_source[:160]}")
    return violations


class TestSlugMutationGuard:
    """Kein ``.update(slug=…)``/``bulk_update(…, ["slug"])`` auf FieldGroup/
    FieldTemplate/DocumentType (Refs #1709, #1058) — der DB-Trigger aus
    Migration 0116 (FieldGroup/FieldTemplate) bzw. 0117 (DocumentType)
    wuerde jeden solchen Aufruf ohnehin mit ``DatabaseError`` scheitern
    lassen; der Guard macht das strukturell unmoeglich statt es dem Trigger
    zur Laufzeit zu ueberlassen.
    """

    def test_synthetic_update_call_is_detected(self) -> None:
        source = (
            "from core.models import FieldGroup\n\n"
            "def reorder(pk):\n"
            "    FieldGroup.objects.filter(pk=pk).update(slug='x')\n"
        )
        violations = find_slug_mutating_bulk_calls(source)
        assert violations
        assert "FieldGroup" in violations[0]

    def test_synthetic_bulk_update_call_is_detected(self) -> None:
        source = (
            "from core.models import FieldTemplate\n\n"
            "def sync(objs):\n"
            "    FieldTemplate.objects.bulk_update(objs, ['name', 'slug'])\n"
        )
        assert find_slug_mutating_bulk_calls(source)

    def test_documenttype_update_call_is_detected(self) -> None:
        """M2 (Refs #1058): DocumentType.slug ist seit Migration 0117 ebenso
        per DB-Trigger unveraenderlich wie FieldGroup/FieldTemplate.slug."""
        source = (
            "from core.models import DocumentType\n\n"
            "def rename(pk, value):\n"
            "    DocumentType.objects.filter(pk=pk).update(slug=value)\n"
        )
        violations = find_slug_mutating_bulk_calls(source)
        assert violations
        assert "DocumentType" in violations[0]

    def test_sort_order_update_is_not_flagged(self) -> None:
        source = (
            "from core.models import FieldGroup\n\n"
            "def reorder(pk, position):\n"
            "    FieldGroup.objects.filter(pk=pk).update(sort_order=position)\n"
        )
        assert not find_slug_mutating_bulk_calls(source)

    def test_unrelated_model_with_slug_kwarg_is_not_flagged(self) -> None:
        # DocumentType war hier vor M2/#1058 das Beispiel fuer ein
        # "unbeteiligtes" Modell — seit Migration 0117 gehoert es selbst zu
        # ``_SLUG_IMMUTABLE_MODELS`` (siehe
        # ``test_documenttype_update_call_is_detected`` oben), daher hier ein
        # echt unbeteiligtes Modell.
        source = (
            "from core.models import Client\n\n"
            "def rename(pk, value):\n"
            "    Client.objects.filter(pk=pk).update(slug=value)\n"
        )
        assert not find_slug_mutating_bulk_calls(source)

    def test_bulk_update_without_slug_in_fields_is_not_flagged(self) -> None:
        source = (
            "from core.models import FieldGroup\n\n"
            "def sync(objs):\n"
            "    FieldGroup.objects.bulk_update(objs, ['name', 'sort_order'])\n"
        )
        assert not find_slug_mutating_bulk_calls(source)

    def test_bare_slug_string_literal_outside_bulk_update_is_not_flagged(self) -> None:
        source = (
            "from core.models import FieldGroup\n\n"
            "def audit_payload(group):\n"
            "    return {'slug': group.slug, 'operation': 'slug'}\n"
        )
        assert not find_slug_mutating_bulk_calls(source)

    def test_migrations_directory_is_excluded_from_scan(self) -> None:
        # Refs #1058: Migration 0117 backfuellt DocumentType.slug per
        # ``bulk_update`` innerhalb ihres eigenen RunPython-Schritts, bevor
        # ihr eigener Slug-Immutable-Trigger existiert — ein legitimer,
        # manuell reviewter Fall, den dieser Guard nicht als Verstoss werten
        # darf (sonst waere die Migration selbst nicht schreibbar).
        files = _guarded_core_python_files(Path("src/core"))
        assert not any("migrations" in f.parts for f in files)
        assert any(f.name == "document_type.py" for f in files), "Sanity-Check: Modelle bleiben im Scan"

    def test_no_slug_mutating_bulk_calls_in_core(self) -> None:
        scan_dir = Path("src/core")
        violations = []
        for py_file in _guarded_core_python_files(scan_dir):
            rel = py_file.relative_to(scan_dir).as_posix()
            for hit in find_slug_mutating_bulk_calls(py_file.read_text()):
                violations.append(f"{rel}: {hit}")
        assert not violations, (
            "Bulk-Update auf FieldGroup/FieldTemplate/DocumentType.slug gefunden — verletzt den DB-Trigger "
            f"aus Migration 0116/0117 (Refs #1709, #1058). Betroffen: {violations}"
        )


class TestModelModuleLayout:
    """Guard: ein Model pro Datei (AGENTS.md § Projekt & Architektur, Refs #1834).

    ``document_type.py`` hielt vier Models gleichzeitig — der groesste
    Verstoss gegen die Konvention. Der Guard pinnt den Split: jedes der vier
    Models lebt in seinem eigenen Modul, und die geteilten Slug-Helfer liegen
    in ``core.models.slug`` (sonst wandern sie beim naechsten Split wieder mit
    einem der Models mit).
    """

    _EXPECTED_MODULES = {
        "DocumentType": "core.models.document_type",
        "FieldTemplate": "core.models.field_template",
        "FieldGroup": "core.models.field_group",
        "DocumentTypeField": "core.models.document_type_field",
    }

    @pytest.mark.parametrize(("model_name", "module"), sorted(_EXPECTED_MODULES.items()))
    def test_model_lives_in_its_own_module(self, model_name: str, module: str) -> None:
        import core.models as core_models

        assert getattr(core_models, model_name).__module__ == module

    @pytest.mark.parametrize("module", sorted(set(_EXPECTED_MODULES.values())))
    def test_module_holds_exactly_one_model(self, module: str) -> None:
        from django.apps import apps

        defined = [m.__name__ for m in apps.get_models() if m.__module__ == module]
        assert len(defined) == 1, f"{module} definiert mehr als ein Model: {defined}"

    def test_slug_helpers_live_in_their_own_module(self) -> None:
        from core.models import slug as slug_module

        for helper in ("_UMLAUT_MAP", "_SLUG_RE", "_RESERVED_SLUGS", "slug_base", "generate_unique_slug"):
            assert hasattr(slug_module, helper), f"{helper} fehlt in core.models.slug"

    def test_model_modules_do_not_re_export_the_slug_helpers(self) -> None:
        """Kein Bequemlichkeits-Re-Export — sonst driften die Importpfade wieder."""
        import core.models.document_type as document_type_module

        for helper in ("_RESERVED_SLUGS", "slug_base", "generate_unique_slug"):
            assert not hasattr(document_type_module, helper), (
                f"{helper} ist aus core.models.document_type importierbar — Helfer gehoeren nach core.models.slug"
            )


# --- for_facility()-Konvention (Refs #1834) ----------------------------------
#
# ``Model.objects.filter(facility=f)`` und ``Model.objects.for_facility(f)``
# erzeugen dasselbe SQL — die Hausform ist trotzdem ``for_facility()``: sie
# benennt die Absicht (Mandanten-Scope, nicht irgendein Filter), sie ist die
# Stelle, an der ein kuenftiger Scope-Zusatz EINMAL landet, und sie macht die
# ungescopten Stellen im Review sichtbar.
#
# Ausnahmen: ``User`` und ``AuditLog``. Beide tragen eine ``facility``, die
# fachlich auch NULL sein darf (Systemnutzer, systemweite Audit-Zeilen); ihre
# Manager bieten ``for_facility`` gar nicht erst an bzw. sollen nicht so
# gelesen werden.
_FOR_FACILITY_EXEMPT_MODELS = frozenset({"User", "AuditLog"})


def _for_facility_model_names() -> set[str]:
    """Models, deren Default-Manager ``for_facility()`` anbietet."""
    import core.models as core_models

    names = set()
    for name in core_models.__all__:
        if name in _FOR_FACILITY_EXEMPT_MODELS:
            continue
        if hasattr(getattr(core_models, name)._default_manager, "for_facility"):
            names.add(name)
    return names


def find_adhoc_facility_filter(source: str) -> list[str]:
    """Findet ``<Model>.objects.filter(facility=…)`` statt ``.for_facility(…)``.

    AST-basiert und bewusst eng: gesucht ist ``.filter()`` **direkt** am
    Manager mit einem ``facility``-Keyword. Ketten wie
    ``Event.objects.visible_to(user).filter(facility=…)`` bleiben ausserhalb —
    sie laufen ueber einen fachlichen Einstiegspunkt, und der Guard soll die
    Hausform einfordern, nicht jede Kette umschreiben.
    """
    scoped = _for_facility_model_names()
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "filter"):
            continue
        manager = func.value
        if not (isinstance(manager, ast.Attribute) and manager.attr == "objects"):
            continue
        if not (isinstance(manager.value, ast.Name) and manager.value.id in scoped):
            continue
        if not any(kw.arg == "facility" for kw in node.keywords):
            continue
        hits.append(f"Zeile {node.lineno}: {manager.value.id}.objects.filter(facility=…)")
    return hits


class TestForFacilityConventionGuard:
    """Der Facility-Scope wird als ``for_facility()`` geschrieben (Refs #1834)."""

    # Refs #1834/#1835: ``core/reporting/`` ist seit dem Backend-Sweep
    # (Merge beider Worktrees) ebenfalls auf der Hausform und wird mitbewacht.
    @pytest.mark.parametrize("scan_dir", ["src/core/services", "src/core/views", "src/core/reporting"])
    def test_no_adhoc_facility_filter(self, scan_dir: str) -> None:
        root = Path(scan_dir)
        violations = []
        for py_file in sorted(root.rglob("*.py")):
            rel = py_file.relative_to(root).as_posix()
            for hit in find_adhoc_facility_filter(py_file.read_text()):
                violations.append(f"{rel}: {hit}")
        assert not violations, (
            "objects.filter(facility=…) gefunden — bitte .for_facility(…) verwenden "
            f"(Refs #1834). Betroffen: {violations}"
        )

    def test_guard_detects_the_pattern(self) -> None:
        source = (
            "from core.models import Client\n\n"
            "def q(f):\n"
            "    return Client.objects.filter(facility=f, is_active=True)\n"
        )
        assert find_adhoc_facility_filter(source)

    def test_for_facility_is_not_flagged(self) -> None:
        source = (
            "from core.models import Client\n\n"
            "def q(f):\n"
            "    return Client.objects.for_facility(f).filter(is_active=True)\n"
        )
        assert not find_adhoc_facility_filter(source)

    def test_exempt_models_are_not_flagged(self) -> None:
        source = (
            "from core.models import AuditLog, User\n\n"
            "def q(f):\n"
            "    User.objects.filter(facility=f)\n"
            "    return AuditLog.objects.filter(facility=f)\n"
        )
        assert not find_adhoc_facility_filter(source)

    def test_create_and_get_are_not_flagged(self) -> None:
        """``for_facility()`` ist ein Queryset-Einstieg — Schreib-/Einzelzugriffe bleiben."""
        source = (
            "from core.models import Client, WorkItem\n\n"
            "def q(f):\n"
            "    WorkItem.objects.create(facility=f, title='x')\n"
            "    return Client.objects.get(pk=1, facility=f)\n"
        )
        assert not find_adhoc_facility_filter(source)


# --- Refs #1884: Facility <-> Settings ist 1:1 und muss ab Anlage bestehen ---

_FACILITY_CREATING_CALLS = {"create", "get_or_create", "update_or_create", "bulk_create"}
_SETTINGS_ENSURING_CALLS = {"create", "get_or_create", "update_or_create"}
_SETTINGS_ENSURING_HELPER = "ensure_settings_for_facility"


def find_facility_creations(source: str) -> list[str]:
    """Findet Aufrufe, die eine ``Facility``-Zeile anlegen.

    Erfasst ``Facility(...)`` (Direktkonstruktion) ebenso wie
    ``Facility.objects.create/get_or_create/update_or_create/bulk_create(...)``.
    """
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "Facility":
            hits.append(f"Zeile {node.lineno}: Facility(...)")
            continue
        if isinstance(func, ast.Attribute) and func.attr in _FACILITY_CREATING_CALLS:
            owner = func.value
            if isinstance(owner, ast.Attribute) and owner.attr == "objects":
                owner = owner.value
            if isinstance(owner, ast.Name) and owner.id == "Facility":
                hits.append(f"Zeile {node.lineno}: Facility.objects.{func.attr}(...)")
    return hits


def ensures_settings_row(source: str) -> bool:
    """Prueft, ob dieselbe Quelle auch eine ``Settings``-Zeile garantiert."""
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == _SETTINGS_ENSURING_HELPER:
            return True
        if isinstance(func, ast.Attribute):
            if func.attr == _SETTINGS_ENSURING_HELPER:
                return True
            if func.attr in _SETTINGS_ENSURING_CALLS:
                owner = func.value
                if isinstance(owner, ast.Attribute) and owner.attr == "objects":
                    owner = owner.value
                if isinstance(owner, ast.Name) and owner.id == "Settings":
                    return True
    return False


def _facility_creation_scan_files() -> list[Path]:
    """Produktionscode unter ``src/core`` fuer den Facility/Settings-Guard.

    Ausgenommen: ``migrations/`` (historische Modelle, kontrolliert reviewt)
    und ``seed/`` — die Architektur-Guards behandeln ``core/seed`` als
    Test-/Fixture-Territorium, nicht als Laufzeit-Anlagepfad.
    """
    scan_dir = Path("src/core")
    return [
        f for f in sorted(scan_dir.rglob("*.py")) if not {"migrations", "seed"} & set(f.relative_to(scan_dir).parts)
    ]


class TestFacilitySettingsRowGuard:
    """Refs #1884: Keine Einrichtung ohne ``Settings``-Zeile.

    ``enforce_retention`` und ``create_statistics_snapshots`` ueberspringen
    Einrichtungen ohne ``Settings`` — fuer sie laeuft dann **keine**
    Aufbewahrungsfrist (Art. 5 Abs. 1 lit. e DSGVO) und es entstehen keine
    Statistik-Snapshots. Der ``post_migrate``-Seed
    (``core/signals/settings_seed.py``) schliesst die Luecke erst beim
    naechsten Deploy. Diese beiden Guards pinnen, dass kein **zweiter**
    Laufzeit-Anlagepfad die Luecke still wieder aufreisst.
    """

    def test_facility_creating_modules_also_ensure_settings(self) -> None:
        violations = []
        for py_file in _facility_creation_scan_files():
            source = py_file.read_text()
            hits = find_facility_creations(source)
            if hits and not ensures_settings_row(source):
                rel = py_file.as_posix()
                violations.append(f"{rel}: {hits}")
        assert not violations, (
            "Facility-Anlage ohne garantierte Settings-Zeile gefunden — betroffene Einrichtungen "
            "werden von enforce_retention/create_statistics_snapshots uebersprungen (Refs #1884). "
            f"Betroffen: {violations}"
        )

    def test_facility_admin_overrides_save_model(self) -> None:
        """Der Admin ist der einzige Laufzeit-Anlagepfad — sein ``save_model`` haelt den Hook.

        Geprueft wird das **Herkunftsmodul**, nicht bloss die Abweichung von
        ``django.contrib.admin.ModelAdmin``: django-unfold bringt ein eigenes
        ``save_model`` mit, gegen das ein reiner Identitaetsvergleich immer
        gruen waere.
        """
        from core.admin_site import anlaufstelle_admin_site
        from core.models import Facility

        registered = anlaufstelle_admin_site._registry.get(Facility)
        assert registered is not None, "Facility ist im Anlaufstelle-Admin nicht registriert"
        origin = type(registered).save_model.__module__
        assert origin.startswith("core."), (
            f"FacilityAdmin.save_model kommt aus '{origin}' statt aus core/ — im Admin "
            "angelegte Einrichtungen bekommen dann keine Settings-Zeile (Refs #1884)"
        )

    def test_guard_detects_the_pattern(self) -> None:
        source = (
            "from core.models import Facility\n\n"
            "def make(org):\n"
            "    return Facility.objects.create(organization=org, name='x')\n"
        )
        assert find_facility_creations(source)
        assert not ensures_settings_row(source)

    def test_guard_accepts_a_module_that_seeds_settings(self) -> None:
        source = (
            "from core.models import Facility, Settings\n\n"
            "def make(org):\n"
            "    facility = Facility.objects.get_or_create(organization=org, name='x')[0]\n"
            "    Settings.objects.get_or_create(facility=facility)\n"
            "    return facility\n"
        )
        assert find_facility_creations(source)
        assert ensures_settings_row(source)

    def test_guard_accepts_the_shared_helper(self) -> None:
        source = (
            "from core.models import Facility\n"
            "from core.services.facility import ensure_settings_for_facility\n\n"
            "def make(org):\n"
            "    facility = Facility(organization=org, name='x')\n"
            "    facility.save()\n"
            "    ensure_settings_for_facility(facility)\n"
            "    return facility\n"
        )
        assert find_facility_creations(source)
        assert ensures_settings_row(source)

    def test_seed_and_migrations_are_excluded_from_scan(self) -> None:
        files = _facility_creation_scan_files()
        assert not any("migrations" in f.parts or "seed" in f.parts for f in files)
        assert any(f.as_posix().endswith("core/admin/organization.py") for f in files), (
            "Sanity-Check: der Admin bleibt im Scan"
        )


# --- Refs #1970: eine angelegte Einrichtung ist ohne Grundausstattung unbenutzbar ---

_BASELINE_ENSURING_HELPER = "ensure_facility_baseline"


def ensures_facility_baseline(source: str) -> bool:
    """Prueft, ob eine Quelle die Grundausstattung einer Einrichtung anstoesst.

    Erkannt wird der Aufruf des einen Einstiegs
    ``core.services.facility_bootstrap.ensure_facility_baseline`` — als
    direkter Name (``ensure_facility_baseline(f)``) wie als Attributzugriff
    (``facility_bootstrap.ensure_facility_baseline(f)``). Bewusst **nicht**
    erkannt werden ``DocumentType.objects.create``/``TimeFilter.objects.create``
    & Co.: der Guard soll genau den einen Einstieg erzwingen und nicht eine
    zweite, handgeschriebene Grundausstattung durchwinken — das war die Falle
    aus Refs #1884.
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == _BASELINE_ENSURING_HELPER:
            return True
        if isinstance(func, ast.Attribute) and func.attr == _BASELINE_ENSURING_HELPER:
            return True
    return False


class TestFacilityBaselineGuard:
    """Refs #1970: Wer eine Einrichtung anlegt, stattet sie auch aus.

    Ohne mindestens einen Dokumentationstyp mit ``entity_type: event`` bleibt
    die Typ-Auswahl in ``EventCreateView`` leer — es laesst sich **kein
    einziger Kontakt** dokumentieren, die Einrichtung ist fachlich unbenutzbar.
    Schichtfenster und Schnellvorlagen sind Komfort und blockieren nichts,
    gehoeren aber zum selben Startbestand.

    Der Guard laeuft ueber dieselbe Dateiliste wie der Settings-Guard
    (Refs #1884) — ``migrations/`` und ``seed/`` bleiben aussen vor.
    """

    def test_facility_creating_modules_also_provision_it(self) -> None:
        violations = []
        for py_file in _facility_creation_scan_files():
            source = py_file.read_text()
            hits = find_facility_creations(source)
            if hits and not ensures_facility_baseline(source):
                violations.append(f"{py_file.as_posix()}: {hits}")
        assert not violations, (
            "Facility-Anlage ohne Grundausstattung gefunden — die Einrichtung startet ohne "
            "Dokumentationstypen und ist damit nicht dokumentierbar (Refs #1970). "
            f"Betroffen: {violations}"
        )

    def test_facility_admin_save_model_provisions_it(self) -> None:
        """Der Admin legt die Zeile nicht selbst an — sein ``save_model`` haelt den Hook.

        Das Herkunftsmodul kommt aus der Admin-Registrierung, damit ein
        Verschieben von ``FacilityAdmin`` den Guard nicht ins Leere laufen
        laesst.
        """
        import importlib
        import inspect

        from core.admin_site import anlaufstelle_admin_site
        from core.models import Facility

        registered = anlaufstelle_admin_site._registry.get(Facility)
        assert registered is not None, "Facility ist im Anlaufstelle-Admin nicht registriert"
        module = importlib.import_module(type(registered).save_model.__module__)
        source = Path(inspect.getfile(module)).read_text()
        assert ensures_facility_baseline(source), (
            "FacilityAdmin.save_model stoesst keine Grundausstattung an — im Admin angelegte "
            "Einrichtungen starten dann ohne Dokumentationstypen (Refs #1970)"
        )

    def test_guard_detects_the_pattern(self) -> None:
        source = (
            "from core.models import Facility\n"
            "from core.services.facility import ensure_settings_for_facility\n\n"
            "def make(org):\n"
            "    facility = Facility.objects.create(organization=org, name='x')\n"
            "    ensure_settings_for_facility(facility)\n"
            "    return facility\n"
        )
        assert find_facility_creations(source)
        assert ensures_settings_row(source), "Settings-Guard bleibt gruen — nur die Ausstattung fehlt"
        assert not ensures_facility_baseline(source)

    def test_guard_accepts_the_shared_helper(self) -> None:
        source = (
            "from core.models import Facility\n"
            "from core.services.facility_bootstrap import ensure_facility_baseline\n\n"
            "def make(org):\n"
            "    facility = Facility.objects.create(organization=org, name='x')\n"
            "    ensure_facility_baseline(facility)\n"
            "    return facility\n"
        )
        assert ensures_facility_baseline(source)

    def test_guard_accepts_the_qualified_call(self) -> None:
        source = (
            "from core.models import Facility\n"
            "from core.services import facility_bootstrap\n\n"
            "def make(org):\n"
            "    facility = Facility.objects.create(organization=org, name='x')\n"
            "    facility_bootstrap.ensure_facility_baseline(facility)\n"
            "    return facility\n"
        )
        assert ensures_facility_baseline(source)

    def test_a_hand_written_second_bootstrap_does_not_satisfy_the_guard(self) -> None:
        """Kein Freibrief fuer einen zweiten Weg mit abweichendem Ergebnis (Refs #1884)."""
        source = (
            "from core.models import DocumentType, Facility\n\n"
            "def make(org):\n"
            "    facility = Facility.objects.create(organization=org, name='x')\n"
            "    DocumentType.objects.create(facility=facility, slug='kontakt', name='Kontakt')\n"
            "    return facility\n"
        )
        assert find_facility_creations(source)
        assert not ensures_facility_baseline(source)
