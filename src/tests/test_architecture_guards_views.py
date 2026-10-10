"""Architecture-Guards — View-Guards (Rate-Limit, Redirect-Hygiene, Routes, ProdSettings) (Refs #929)."""

import ast
import builtins
import difflib
import importlib
import inspect
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture


class TestRateLimitOnAllMutations:
    """Scan every view module, including inherited handlers and POST functions."""

    _VIEWS_DIR = Path("src/core/views")
    _ALLOWLIST = {
        "auth.py:CustomLogoutView.post": (
            "Logout must always flush the session, including quick exit on shared devices; "
            "it exposes no factor verification or data mutation beyond ending that session (Refs #2529)."
        ),
        "clients.py:ClientRenameWarningView.post": (
            "Read-only pseudonym warning probe; must not consume the actual update quota (Refs #2044)."
        ),
    }
    _METHODS = {"post", "put", "patch", "delete"}

    def test_all_post_handlers_have_ratelimit(self):
        from django.views import generic
        from django_ratelimit import ALL
        from django_ratelimit.core import _split_rate

        assert self._VIEWS_DIR.exists(), f"Missing view directory: {self._VIEWS_DIR}"
        modules = {}
        classes = {}
        external_mros = {}

        def load(path):
            path = path.resolve()
            if path not in modules:
                tree = ast.parse(path.read_text())
                imports = {}
                for node in tree.body:
                    if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                        for alias in node.names:
                            imports[alias.asname or alias.name] = (node.module, alias.name)
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            imports[alias.asname or alias.name.split(".")[0]] = (
                                alias.name if alias.asname else alias.name.split(".")[0],
                                "",
                            )
                modules[path] = tree, imports
                for node in tree.body:
                    if isinstance(node, ast.ClassDef):
                        classes[path, node.name] = node
            return path

        for path in sorted(self._VIEWS_DIR.rglob("*.py")):
            load(path)
        endpoints = list(classes)
        view_fallbacks = {name: getattr(generic, name) for name in dir(generic) if name.endswith("View")}

        def imported(path, node):
            if isinstance(node, ast.Name):
                module, attribute = modules[path][1][node.id]
                return (
                    getattr(importlib.import_module(module), attribute)
                    if attribute
                    else importlib.import_module(module)
                )
            if isinstance(node, ast.Attribute):
                return getattr(imported(path, node.value), node.attr)
            raise ValueError("Unsupported imported expression")

        def symbol(path, node):
            if isinstance(node, ast.Name):
                return modules[path][1].get(node.id, ("", node.id))[1]
            return node.attr if isinstance(node, ast.Attribute) else ""

        def value(path, node):
            try:
                return ast.literal_eval(node)
            except (ValueError, TypeError):
                if isinstance(node, ast.Name):
                    for statement in modules[path][0].body:
                        targets = statement.targets if isinstance(statement, ast.Assign) else []
                        if any(isinstance(target, ast.Name) and target.id == node.id for target in targets):
                            return value(path, statement.value)
                try:
                    return imported(path, node)
                except (KeyError, AttributeError, ValueError):
                    return None

        def limited(path, decorators, method, *, target=None):
            for decorator in decorators:
                if target:
                    if not isinstance(decorator, ast.Call) or symbol(path, decorator.func) != "method_decorator":
                        continue
                    if not any(kw.arg == "name" and value(path, kw.value) == target for kw in decorator.keywords):
                        continue
                applied = (
                    decorator.args[:1]
                    if (isinstance(decorator, ast.Call) and symbol(path, decorator.func) == "method_decorator")
                    else [decorator]
                )
                calls = [
                    item
                    for node in applied
                    for item in (node.elts if isinstance(node, (ast.List, ast.Tuple)) else [node])
                ]
                for call in calls:
                    if not isinstance(call, ast.Call) or symbol(path, call.func) != "ratelimit":
                        continue
                    options = {kw.arg: kw.value for kw in call.keywords}
                    if value(path, options.get("block")) is not True:
                        continue
                    rate = value(path, options.get("rate"))
                    try:
                        count, seconds = _split_rate(rate)
                        if not isinstance(count, int) or not isinstance(seconds, int) or count < 0 or seconds <= 0:
                            continue
                    except (TypeError, ValueError, AttributeError):
                        continue
                    methods = value(path, options["method"]) if "method" in options else ALL
                    if methods == ALL:
                        return True
                    if isinstance(methods, str):
                        methods = [methods]
                    if isinstance(methods, (list, tuple)) and any(
                        isinstance(item, str) and item.upper() == method.upper() for item in methods
                    ):
                        return True
            return False

        def external(cls):
            if cls.__module__ == "builtins":
                return None
            key = load(Path(inspect.getfile(cls))), cls.__name__
            assert key in classes, f"Cannot inspect external base: {cls}"
            if key not in external_mros:
                external_mros[key] = [
                    parent_key for base in cls.__mro__[1:] if (parent_key := external(base)) is not None
                ]
            return key

        def base_key(path, node):
            if isinstance(node, ast.Name) and (path, node.id) in classes:
                return path, node.id
            try:
                return external(imported(path, node))
            except KeyError:
                if isinstance(node, ast.Name) and node.id in view_fallbacks:
                    return external(view_fallbacks[node.id])
                if isinstance(node, ast.Name) and isinstance(getattr(builtins, node.id, None), type):
                    return None
                raise AssertionError(f"Unresolved base in {path.name}: {ast.unparse(node)}") from None

        mros = {}

        def mro(key):
            if key in external_mros:
                return [key, *external_mros[key]]
            if key not in mros:
                parents = [parent for node in classes[key].bases if (parent := base_key(key[0], node))]
                sequences = [mro(parent).copy() for parent in parents] + [parents.copy()]
                result = [key]
                while sequences := [seq for seq in sequences if seq]:
                    candidate = next(
                        (seq[0] for seq in sequences if all(seq[0] not in other[1:] for other in sequences)), None
                    )
                    assert candidate is not None, f"Inconsistent MRO: {key}"
                    result.append(candidate)
                    for seq in sequences:
                        if seq[0] == candidate:
                            seq.pop(0)
                mros[key] = result
            return mros[key]

        def handler(chain, method):
            for index, key in enumerate(chain):
                for node in classes[key].body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method:
                        return index, node
            return None

        def handler_limit(chain, target, method):
            found = handler(chain, target)
            if found is None:
                return False
            index, node = found
            return limited(chain[index][0], node.decorator_list, method) or any(
                limited(key[0], classes[key].decorator_list, method, target=target) for key in chain[: index + 1]
            )

        def dispatch_routes(chain):
            # Only Django's terminal dispatcher is trusted to select request.method.
            routes = []
            for key in chain:
                found = handler([key], "dispatch")
                if found is None or key == external(generic.View):
                    continue
                for node in ast.walk(found[1]):
                    if isinstance(node, ast.Attribute) and node.attr in self._METHODS | {
                        "get",
                        "head",
                        "options",
                        "dispatch",
                    }:
                        receiver = node.value
                        uses_self = isinstance(receiver, ast.Name) and receiver.id == "self"
                        uses_super = (
                            isinstance(receiver, ast.Call)
                            and isinstance(receiver.func, ast.Name)
                            and receiver.func.id == "super"
                        )
                        uses_base = isinstance(receiver, ast.Name) and symbol(key[0], receiver) in {
                            owner[1] for owner in chain
                        }
                        if not (uses_self or uses_super or uses_base):
                            continue
                        if (
                            node.attr == "dispatch"
                            and isinstance(receiver, ast.Call)
                            and isinstance(receiver.func, ast.Name)
                            and receiver.func.id == "super"
                        ):
                            continue
                        routes.append((node.attr, isinstance(receiver, ast.Name) and receiver.id == "self"))
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
                        if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "self":
                            routes.append(("dynamic", False))
            return routes

        violations = []
        for key in endpoints:
            identifier = f"{key[0].relative_to(self._VIEWS_DIR.resolve()).as_posix()}:{key[1]}"
            chain = mro(key)
            routes = dispatch_routes(chain)
            allowed_methods = None
            for owner in chain:
                for statement in classes[owner].body:
                    if isinstance(statement, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == "http_method_names"
                        for target in statement.targets
                    ):
                        allowed_methods = value(owner[0], statement.value)
                        break
                if allowed_methods is not None:
                    break
            for method in self._METHODS:
                if self._ALLOWLIST.get(f"{identifier}.{method}", "").strip():
                    continue
                if not routes and allowed_methods is not None and method not in allowed_methods:
                    continue
                if not routes and handler(chain, method) is None:
                    continue
                if handler_limit(chain, "dispatch", method):
                    continue
                if all(target == method and uses_self for target, uses_self in routes) and handler_limit(
                    chain, method, method
                ):
                    continue
                violations.append(f"{identifier}.{method}")
        for path in sorted(self._VIEWS_DIR.rglob("*.py")):
            path = path.resolve()
            for node in modules[path][0].body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                methods = set()
                for decorator in node.decorator_list:
                    if symbol(path, decorator) == "require_POST":
                        methods.add("post")
                    if isinstance(decorator, ast.Call) and symbol(path, decorator.func) == "require_http_methods":
                        methods.update(
                            str(item.value).lower()
                            for item in ast.walk(decorator)
                            if isinstance(item, ast.Constant) and str(item.value).lower() in self._METHODS
                        )
                for method in methods:
                    if not limited(path, node.decorator_list, method):
                        violations.append(f"{path.relative_to(self._VIEWS_DIR.resolve())}:{node.name}.{method}")
        assert not violations, f"Mutation handlers without a blocking rate limit: {sorted(violations)}"


class TestRateLimitOnSensitiveGetEndpoints:
    """Refs #737 / #713: GET-Endpoints mit
    Pseudonym-/Schluessel-/Detection-Bezug muessen rate-limited sein.

    Anders als ``TestRateLimitOnAllMutations`` (das alle POST-Handler
    erzwingt) ist dieser Test eine **Allowlist** sensibler GET-Pfade —
    Endpoints, die zwar nur lesen, aber bei Spam Information leaken oder
    eine Probing-Surface bilden:

    - ``ClientAutocompleteView`` — leakt Pseudonyme bei iteriertem Spam

    Bewusst nicht in der Liste:
    - ``OfflineKeySaltView`` ist POST-only — abgedeckt durch
      ``TestRateLimitOnAllMutations``
    - ``CSPReportView`` ist POST-only — abgedeckt durch
      ``TestRateLimitOnAllMutations``

    Jeder Eintrag muss einen ``@ratelimit(..., method=\"GET\", block=True)``-
    Decorator (oder mehrteilig per Class-Decorator) tragen. ``block=True``
    ist wichtig — ohne den Flag liefert django-ratelimit weiter 200, der
    Limit ist effektiv unwirksam.
    """

    _VIEWS_DIR = Path("src/core/views")
    # Liste sensibler GET-Endpoints. Format: (datei, klasse).
    _SENSITIVE_GET_VIEWS = (
        ("clients.py", "ClientAutocompleteView"),
        # Refs #1084 (S3): teure/sensible GET-Endpoints — DSGVO-Dokument-
        # Rendering und Attachment-Entschluesselung laufen pro Request.
        # M24/AP4 (Refs #1738): ``get`` liegt seit der Owner-Generik EINMAL in
        # ``BaseAttachmentDownloadView``; die drei Owner-Subklassen
        # (Ereignis/Person/Fall) erben Handler UND Decorator und teilen sich
        # bewusst den Eimer ``group="attachment_download"``.
        ("attachments.py", "BaseAttachmentDownloadView"),
        ("dsgvo.py", "DSGVOPackageView"),
        ("dsgvo.py", "DSGVODocumentDownloadView"),
    )

    def _collect_decorators_above(self, lines: list[str], def_idx: int) -> str:
        """Sammelt zusammenhaengende ``@...``-Zeilen oberhalb ``def_idx``.

        Linienbasiert (kein Regex-Backtracking). Multi-line-Decorators
        ``@method_decorator(\\n    ratelimit(...),\\n    name='post',\\n)``
        werden ueber das Klammer-Ende erkannt.
        """
        collected: list[str] = []
        i = def_idx - 1
        while i >= 0:
            line = lines[i]
            stripped = line.lstrip()
            if stripped.startswith("@"):
                # Pruefe, ob der Decorator multi-line ist — naechste Zeilen
                # zwischen i und def_idx sammeln.
                collected.insert(0, "\n".join(lines[i:def_idx]))
                def_idx = i
                i -= 1
                continue
            if stripped == "" or stripped.startswith("#"):
                i -= 1
                continue
            # Eingerueckte Continuation-Zeile eines Multi-line-Decorators
            # weiter oben — schon eingesammelt, skippen.
            if line.startswith(" ") and not stripped.startswith("def ") and not stripped.startswith("class "):
                i -= 1
                continue
            break
        return "\n".join(collected)

    def test_sensitive_get_endpoints_are_rate_limited_with_block(self):
        if not self._VIEWS_DIR.exists():
            pytest.skip(f"{self._VIEWS_DIR} nicht vorhanden")
        violations = []
        for filename, cls_name in self._SENSITIVE_GET_VIEWS:
            py_file = self._VIEWS_DIR / filename
            if not py_file.exists():
                violations.append(f"{filename}: Datei nicht gefunden")
                continue
            source = py_file.read_text()
            lines = source.split("\n")
            # Klassengrenze finden.
            cls_line_idx = None
            for idx, line in enumerate(lines):
                if line.startswith(f"class {cls_name}("):
                    cls_line_idx = idx
                    break
            if cls_line_idx is None:
                violations.append(f"{filename}:{cls_name}: Klasse nicht gefunden")
                continue
            # Naechste class-Definition als Ende-Marker.
            cls_end_idx = len(lines)
            for idx in range(cls_line_idx + 1, len(lines)):
                if lines[idx].startswith("class "):
                    cls_end_idx = idx
                    break
            # def get(self, ...) innerhalb der Klasse suchen.
            get_idx = None
            for idx in range(cls_line_idx + 1, cls_end_idx):
                if re.match(r"\s+def get\(self", lines[idx]):
                    get_idx = idx
                    break
            if get_idx is None:
                violations.append(f"{filename}:{cls_name}: kein def get(self, ...) gefunden")
                continue
            method_decos = self._collect_decorators_above(lines, get_idx)
            class_decos = self._collect_decorators_above(lines, cls_line_idx)
            full_decos = method_decos + "\n" + class_decos
            if "ratelimit" not in full_decos:
                violations.append(f"{filename}:{cls_name}: kein @ratelimit auf GET")
                continue
            if "block=True" not in full_decos:
                violations.append(
                    f"{filename}:{cls_name}: @ratelimit ohne block=True — "
                    "Limit ist ohne block=True effektiv unwirksam (Refs #737)"
                )
        assert not violations, (
            "Sensible GET-Endpoints muessen @ratelimit(..., method='GET', block=True) "
            "tragen. Ohne block=True liefert django-ratelimit weiter 200 trotz Verstoss.\n"
            "Refs #737 / #713.\n"
            f"Verstoesse: {violations}"
        )


class TestProdSettingsSudoModeRequiredGuard:
    """Refs #775: ``settings/prod.py`` muss verhindern, dass
    ``SUDO_MODE_ENABLED=False`` in Produktion stillschweigend MFA-Disable,
    DSGVO-Export und Pseudonym-Daten-Download oeffnet.

    Test-Settings (``settings/test.py``) setzen das Flag bewusst auf False,
    damit Tests nicht jedes Re-Auth-Form passieren muessen — wenn das
    Test-Setting versehentlich nach Prod uebernommen wird, schlaegt der
    Server-Start frueh fehl statt stille Defense-Erosion.
    """

    _PROD_SETTINGS = Path("src/anlaufstelle/settings/prod.py")
    _TEST_SETTINGS = Path("src/anlaufstelle/settings/test.py")

    def test_prod_settings_raises_on_sudo_mode_disabled(self):
        if not self._PROD_SETTINGS.exists():
            pytest.skip(f"{self._PROD_SETTINGS} nicht vorhanden")
        source = self._PROD_SETTINGS.read_text()
        assert "SUDO_MODE_ENABLED" in source, (
            "settings/prod.py muss SUDO_MODE_ENABLED explizit pruefen — sonst "
            "kippt ein versehentlich uebernommenes Test-Setting MFA-Disable, "
            "DSGVO-Export und Pseudonym-Download in einem Schritt. Refs #775."
        )
        assert "ImproperlyConfigured" in source
        # Der Guard muss tatsaechlich eine Exception werfen, nicht nur erwaehnen.
        # Heuristik: zwischen ``SUDO_MODE_ENABLED`` und dem naechsten ``raise``-
        # Statement duerfen keine 500 Zeichen liegen.
        for match in re.finditer(r"SUDO_MODE_ENABLED", source):
            tail = source[match.end() : match.end() + 500]
            if "raise ImproperlyConfigured" in tail:
                return
        pytest.fail("SUDO_MODE_ENABLED-Pruefung in prod.py wirft kein ImproperlyConfigured. Refs #775.")

    def test_test_settings_keep_sudo_mode_disabled(self):
        """Sanity: settings/test.py darf weiterhin SUDO_MODE_ENABLED=False
        setzen — sonst muesste jeder Test das Re-Auth-Form passieren."""
        if not self._TEST_SETTINGS.exists():
            pytest.skip(f"{self._TEST_SETTINGS} nicht vorhanden")
        source = self._TEST_SETTINGS.read_text()
        assert "SUDO_MODE_ENABLED = False" in source, (
            "settings/test.py muss SUDO_MODE_ENABLED=False behalten — sonst "
            "muss jeder Test-Flow das Re-Auth-Form passieren."
        )


class TestNoUncheckedNextRedirectGuard:
    """Refs #770: ``redirect(request.POST/GET.get("next"))`` muss durch
    ``safe_redirect_path`` laufen.

    ``startswith("/")`` allein laesst ``//evil.example/login`` durch — der
    Browser interpretiert das als protokoll-relative URL und springt auf
    eine fremde Origin (Phishing-Vektor). Der zentrale Helper
    ``core.views.utils.safe_redirect_path`` schliesst die Luecke.

    Heuristik: Jede View-Datei, in der ``request.POST/GET.get('next')``
    auftaucht und ein ``redirect(...)`` aufgerufen wird, muss auch
    ``safe_redirect_path`` importieren/nutzen. Verhindert Wiederauferstehen
    der naiven ``startswith('/')``-Pruefung in neuen Views.
    """

    _VIEWS_DIR = Path("src/core/views")
    _NEXT_FETCH = re.compile(r"""request\.(POST|GET)\.get\(\s*["']next["']""")
    _REDIRECT_CALL = re.compile(r"\bredirect\s*\(")

    def test_no_unchecked_next_redirect(self):
        if not self._VIEWS_DIR.exists():
            pytest.skip(f"{self._VIEWS_DIR} nicht vorhanden")
        violations = []
        for py_file in self._VIEWS_DIR.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            source = py_file.read_text()
            if not (self._NEXT_FETCH.search(source) and self._REDIRECT_CALL.search(source)):
                continue
            if "safe_redirect_path" not in source:
                violations.append(py_file.name)
        assert not violations, (
            "Diese Views lesen ``next`` aus Request und rufen ``redirect()`` "
            "auf, ohne ``safe_redirect_path`` zu nutzen. ``startswith('/')`` "
            "allein laesst protokoll-relative URLs (``//evil/``) durch. Bitte "
            "``from core.views.utils import safe_redirect_path`` importieren "
            "und ``redirect(safe_redirect_path(...))`` nutzen.\n"
            "Refs #770.\n"
            f"Betroffen: {violations}"
        )


class TestUserFacingEntryPointGuard:
    """Jede user-facing Route muss einen sichtbaren Einstieg haben.

    Wenn ein Feature hinzugefügt wird, aber kein Template einen Link/Button
    rendert, ist es ein halb-eingebauter Zustand — genau das Muster, das
    #605 vermeiden
    soll. Die Allowlist enthält nur solche URLs, die bewusst nur per Deep-
    Link, aus dem Code oder aus HTMX-Partials heraus aufgerufen werden.

    Zwei gleichwertige Quellen zählen als Einstieg:

    1. ein ``{% url '<name>' %}`` in irgendeinem Template, und
    2. ein ``url_name="<name>"`` in der Navigations-Registry
       ``src/core/nav.py`` (Refs #1813).

    Quelle 2 kam mit der Registry dazu: die Hauptnavigation baut ihre ``href``
    seither in Python (``reverse()`` im Inclusion-Tag), das Template enthält
    nur noch ``{{ item.href }}``. Der Einstieg ist damit **nicht** verschwunden,
    er hat den Ort gewechselt — und ist dort sogar stärker abgesichert:
    ``test_nav_registry.py`` beweist, dass jeder Registry-Eintrag auflösbar ist,
    während ein ``{% url %}`` auch in totem Markup stehen könnte.
    """

    _TEMPLATES_DIR = Path("src/templates")
    # URL-Names, für die mindestens ein ``{% url '<name>' ... %}`` in einem
    # Template existieren muss.  Wächst mit neuen Features.
    _REQUIRED_URL_NAMES = {
        "core:zeitstrom",
        "core:client_list",
        "core:client_create",
        "core:case_list",
        "core:case_create",
        "core:workitem_inbox",
        "core:workitem_create",
        "core:event_create",
        "core:attachment_list",
        "core:retention_dashboard",
        "core:deletion_request_list",
        "core:audit_log",
        "core:statistics",
        "core:global_search",
        "core:account_profile",
        "core:dsgvo_package",
        # Refs #1717: 'Feldvorlagen' fehlte bislang komplett als Nav-Einstieg
        # (nur Deep-Link von der Dokumentationstyp-Konfiguration aus
        # erreichbar) — Guard-Härtung, damit diese Lücken-Klasse nicht
        # unbemerkt wieder entsteht.
        "core:config_fieldtemplate_list",
    }
    # Routes, die bewusst nur deep-verlinkt / HTMX-getrieben sind.
    _ALLOWLIST: set[str] = {
        # HTMX-Partials werden über `{% url %}` in dem Template des Parents
        # aufgerufen — das ist keine user-facing Seite, aber der Test zählt
        # beides sowieso als Link.
    }

    def test_all_required_url_names_have_template_link(self):
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        templates_text = ""
        for template_file in self._TEMPLATES_DIR.rglob("*.html"):
            templates_text += template_file.read_text(errors="ignore") + "\n"
        # Registry-Beleg über die ECHTEN Sammlungen (Sidebar, Mobil-Mehr,
        # Bereichs-Reiter), nicht über einen Quelltext-Regex: ein NavItem,
        # das definiert, aber in keiner Sammlung referenziert ist (Orphan),
        # wird nirgends gerendert und zaehlt nicht als Einstieg
        # (Verify-Befund #1813).
        from core import nav as nav_registry

        registry_url_names = {item.url_name for item in nav_registry.all_items()}

        missing = []
        for url_name in self._REQUIRED_URL_NAMES:
            if url_name in self._ALLOWLIST:
                continue
            # `{% url 'name' ... %}` oder `{% url "name" ... %}`
            in_template = re.search(rf"""\{{%\s*url\s+['"]{re.escape(url_name)}['"]""", templates_text)
            in_registry = url_name in registry_url_names
            if not (in_template or in_registry):
                missing.append(url_name)
        assert not missing, (
            "Diese URL-Names haben keinen sichtbaren Einstieg — weder als {% url %} in einem "
            "Template noch als Eintrag in src/core/nav.py. Das Feature ist halb-eingebaut. "
            "Entweder Einstieg ergänzen oder URL-Name aus _REQUIRED_URL_NAMES entfernen "
            f"(falls bewusst deep-linked). Fehlend: {sorted(missing)}"
        )


class TestDocumentedRoutesGuard:
    """Alle im Handbuch erwähnten URL-Pfade müssen im URL-Router auflösbar sein.

    Verhindert Doku-Drift bei Route-Renames (Refs #605). Scannt
    ``docs/user-guide.md`` nach Inline-Code-Pfaden, ersetzt Platzhalter
    (``<uuid>`` usw.) durch valide Beispielwerte und ruft
    :func:`django.urls.resolve`. Schlägt der Resolver fehl, ist der Pfad
    entweder veraltet oder nicht existent.
    """

    _DOC = Path("docs/user-guide.md")
    # `/…/` in Inline-Code, aber keine äußeren URLs (https://…), keine leeren
    # Segmente und keine Code-Fences (``` ``` ```). Muss mit `/` anfangen.
    _PATH_PATTERN = re.compile(r"`(/[A-Za-z0-9_\-./<>:]*)`")
    # Platzhalter → valide Beispielwerte für den URL-Resolver.
    _PLACEHOLDERS = {
        "<uuid>": "00000000-0000-0000-0000-000000000000",
        "<id>": "00000000-0000-0000-0000-000000000000",
        "<pk>": "00000000-0000-0000-0000-000000000000",
        "<uidb64>": "Mg",
        "<token>": "abcd-efgh-ijkl",
        "<int>": "1",
    }
    # Bewusste Ausnahmen — Pfade, die Beispiele im Guide sind, aber nicht
    # vom Router beansprucht werden (z.B. Django-Admin-Subpfade, externer
    # Host im Beispiel).
    _ALLOWLIST = {
        "/static/foo.css",  # Beispielpfad für statische Dateien
        "/admin-mgmt/",  # Django-Admin (nicht über core.urls erreichbar, aber registriert)
    }

    def _substitute(self, path: str) -> str:
        for needle, replacement in self._PLACEHOLDERS.items():
            path = path.replace(needle, replacement)
        return path

    def test_all_documented_paths_resolve(self):
        from django.urls import Resolver404, resolve

        if not self._DOC.exists():
            pytest.skip(f"{self._DOC} nicht vorhanden")
        text = self._DOC.read_text()
        candidates: set[str] = set()
        for match in self._PATH_PATTERN.finditer(text):
            candidate = match.group(1)
            # Nur sinnvolle Pfade (beginnen mit bekannten Prefixes).
            # Heuristik: am ersten Slash geteilte Pfade mit nicht-leerem Kopf.
            parts = [p for p in candidate.split("/") if p]
            if not parts:
                continue
            candidates.add(candidate)

        unresolved = []
        for raw in sorted(candidates):
            if raw in self._ALLOWLIST:
                continue
            path = self._substitute(raw)
            try:
                resolve(path)
            except Resolver404:
                unresolved.append(raw)
        assert not unresolved, (
            "Pfade in docs/user-guide.md sind nicht mehr im URL-Router auflösbar — "
            "entweder Doku anpassen oder Route wiederherstellen: "
            f"{unresolved}"
        )


class TestClientCaseViewMirrorParity:
    """Der 1:1-Spiegel zwischen ``views/clients.py`` und ``views/cases.py`` darf nicht still driften.

    Refs #1830 (Maintainer-Entscheid Variante B): Die Personen- und die
    Fall-View sind bewusst Kopien voneinander — eine gemeinsame Fabrik haette
    die drei Sicherheitsgrenzen (Facility, ``entity_type``, Sensitivity) hinter
    einer Indirektion versteckt (``views/cases.py``, Modulkommentar). Der Preis
    dieser Entscheidung ist Doppelpflege: [b27be80b] musste denselben Fix
    zweimal anwenden. Dieser Guard macht genau diese Drift zum Testfehler statt
    zum stillen Doppel-Fix.

    **Was der Guard leistet** (und nur das):

    * Er **leitet** die zu vergleichenden Paare aus dem Code ab — es gibt keine
      gepflegte Namensliste. Verglichen wird jede Top-Level-Funktion und jede
      Methode beider Module; gepaart wird ueber den entity-normalisierten
      Qualnamen (``_client_data_form``/``_case_data_form`` →
      ``_entity_data_form``, ``ClientUpdateView.post``/``CaseUpdateView.post``
      → ``EntityUpdateView.post``). Ein NEUER gespiegelter Helfer — etwa das
      ``_render_context`` beider Update-Views — wird dadurch ohne jede
      Testaenderung mitgeprueft.
    * Verglichen wird der **normalisierte AST** (``ast.dump``), nicht der Text:
      Kommentare, Docstrings, Zeilenumbrueche und Zeilennummern sind
      irrelevant. Normalisiert werden Entity-Token in Bezeichnern, Attributen,
      Argumentnamen und **String-Konstanten** (``Client``/``Case`` → ``Entity``,
      ``client``/``case`` → ``entity``, ``CLIENT``/``CASE`` → ``ENTITY``,
      ``Person``/``Fall`` → ``Entity``). Als Vergleichseinheiten dienen die
      Signatur samt Dekoratoren plus die Anweisungen erster Ebene.
    * Paare ohne Eintrag in :attr:`_EXPECTED_DIVERGENCES` muessen **strukturell
      identisch** sein.
    * Paare mit Eintrag duerfen abweichen, aber nur in der dort **gezaehlten**
      Anzahl von Anweisungen und nur mit ausformulierter Begruendung. Eine
      zusaetzliche abweichende Anweisung reisst den Guard; ein veralteter
      Eintrag (Paar existiert nicht mehr oder ist inzwischen identisch)
      ebenfalls.

    **Was der Guard NICHT leistet:**

    * (a) Er sieht nur Abweichungen auf der Ebene ganzer Anweisungen erster
      Ebene. Aendert sich etwas **innerhalb** einer bereits als abweichend
      gezaehlten Anweisung (z.B. im grossen ``try``-Block von
      ``EntityUpdateView.post``), bleibt die Zahl gleich und der Guard still.
      Deckt der Ausnahme-Eintrag **alle** Slots eines Paares ab — so bei
      ``EntityUpdateView._render_context`` (Signatur plus die einzige
      Top-Level-Anweisung ``return {...}``) —, dann vergleicht der Guard an
      diesem Paar ueberhaupt nichts mehr. Fuer genau dieses Paar haengt die
      Zusicherung deshalb an einem eigenen Test,
      :meth:`test_render_context_key_sets_stay_congruent`.
    * (b) Unpaarige Einheiten (``_get_case_event_context``,
      ``ClientAutocompleteView``, ``CaseCloseView``, …) vergleicht er gar nicht
      — sie haben kein Gegenstueck.
    * (c) Die Entity-Normalisierung macht **vertauschte** Bezeichner
      unsichtbar, und das reicht weit ueber Meldungstexte hinaus: Routen
      (``core:client_detail`` und ``core:case_detail`` → beide
      ``core:entity_detail``), Templates (``core/clients/form.html`` und
      ``core/cases/form.html`` → beide ``core/entitys/form.html``) und Modelle
      (``get_scoped_object(Client, …)`` und ``get_scoped_object(Case, …)`` →
      beide ``get_scoped_object(Entity, …)``) fallen alle zusammen. Wuerde die
      Personen-View auf die Fall-Route umleiten, das Fall-Template rendern
      oder das Fall-Model laden, bliebe dieser Guard still — dagegen helfen
      nur die Verhaltens-Tests aus (d).
    * (d) Er prueft Struktur, nicht Verhalten: dass die Kopien dasselbe TUN,
      beweist die Zwillings-Suite ``test_entity_dynamic_ui.py``, nicht dieser
      Guard.

    **Selbstsicherung gegen Uebernormalisierung:** Wuerde die Normalisierung
    kaputt zu breit (ein Token-Paar, das echte Unterschiede wegbuegelt), dann
    wuerden die ausgenommenen Paare identisch — ihre Divergenz-Zahl faellt auf
    0, und :meth:`test_no_stale_expected_divergences` reisst mit „Paar ist
    inzwischen identisch". Der Guard kann sich also nicht unbemerkt ins Leere
    normalisieren, solange ueberhaupt Ausnahmen eingetragen sind.
    """

    _CLIENT_MODULE = Path("src/core/views/clients.py")
    _CASE_MODULE = Path("src/core/views/cases.py")

    # Entity-Token-Normalisierung. Reihenfolge ist unkritisch: kein Ersatzwert
    # enthaelt ein weiteres Suchmuster. Plural- und Ableitungsformen fallen
    # ueber die Teilstring-Ersetzung mit ab (``clients``/``cases`` → ``entitys``,
    # ``CaseModel`` → ``EntityModel``, ``cases_with_live_client`` →
    # ``entitys_with_live_entity``).
    #
    # Die Teilstring-Ersetzung trifft auch Fremdbezeichner: Djangos
    # ``Case(When(...))``-Expression (``django.db.models.Case``, in
    # ``ClientDetailView.get`` fuer die Aufgaben-Sortierung) wird samt
    # Import-Alias zu ``Entity(When(...))`` mitnormalisiert. Harmlos, solange
    # daraus keine Namenskollision entsteht — und genau die faengt der
    # ``assert key not in units`` in :meth:`_units` laut ab, statt still eine
    # Vergleichseinheit zu ueberschreiben.
    _ENTITY_TOKENS = (
        ("Client", "Entity"),
        ("Case", "Entity"),
        ("client", "entity"),
        ("case", "entity"),
        ("CLIENT", "ENTITY"),
        ("CASE", "ENTITY"),
        # Nur fuer String-Konstanten relevant (Meldungstexte/404-Texte); als
        # Bezeichner kommen die beiden Woerter im Repo nicht vor. Preis der
        # Normalisierung: ein vertauschtes „Person"/„Fall" in einer Meldung
        # faellt dem Guard nicht auf.
        ("Person", "Entity"),
        ("Fall", "Entity"),
    )

    # Paare, die BEWUSST abweichen. Wert: (Anzahl abweichender Anweisungen,
    # Begruendung). Die Anzahl ist Teil der Zusicherung — eine weitere
    # Abweichung im selben Paar reisst den Guard.
    _EXPECTED_DIVERGENCES: dict[str, tuple[int, str]] = {
        "EntityListView.get": (
            3,
            "Basis-Queryset (``Client.objects.for_facility(...).filter(is_active, is_deleted)`` vs. "
            "``cases_with_live_client()``, Refs #1777), Sortierung/Annotation (``last_contact`` ueber "
            "Ereignisse vs. ``select_related``) und Kontext (Kontaktstufen-/Alters-Filter plus "
            "Merge-Badges, Refs #1876, vs. Status-Filter) sind fachlich verschieden.",
        ),
        "EntityDetailView.get": (
            8,
            "Die beiden Detailseiten zeigen verschiedene Fachobjekte: Person = Ereignis-Timeline, "
            "Aufgaben, Hausverbote, offene Faelle, Art.-16-Antraege (Refs #1060); Fall = Episoden, "
            "Wirkungsziele, Ereignis-Zuordnung (Refs #605). Gemeinsam ist nur der abschliessende "
            "``build_dynamic_data_context``-Aufruf, und der ist identisch.",
        ),
        "EntityCreateView.get": (
            6,
            "Die Fall-Anlage nimmt ``?client=`` entgegen und pinnt die Person vor "
            "(``get_client_or_none`` → ``client_id``/``client_pseudonym``, Refs #1786); die "
            "Personen-Anlage kennt keinen Vorbelegungs-Parameter und holt ``facility`` gar nicht "
            "erst in eine lokale Variable.",
        ),
        "EntityCreateView.post": (
            5,
            "Das Rate-Limit sitzt beim Fall am Methoden-, bei der Person am Klassen-Dekorator "
            "(beides von ``TestRateLimitOnAllMutations`` abgedeckt); die drei Render-Zweige des "
            "Falls tragen zusaetzlich ``client_id``/``client_pseudonym``; ``create_client`` und "
            "``create_case`` haben verschiedene Pflichtfelder, und die Erfolgsmeldung lautet "
            "``angelegt`` vs. ``erstellt``.",
        ),
        "EntityUpdateView.get": (
            5,
            "Fall-spezifisch: lokale ``facility``-Variable, Queryset ``cases_with_live_client()`` "
            "(Refs #1777) statt der beiden Personen-Widerrufs-Filter, ``CaseForm(pinned_client=…)`` "
            "plus ``client``-Initial und die Personen-Pinning-Schluessel im Kontext (Refs #1786). "
            "Die Personen-View hat zu keinem davon ein Gegenstueck.",
        ),
        "EntityUpdateView.post": (
            7,
            "Dieselben vier Fall-Eigenheiten wie im GET, dazu drei echte Verhaltensunterschiede: "
            "die Person fuehrt einen ``old_stage``-Diff fuer den Audit (Kontaktstufen-Wechsel), der "
            'Fall verwirft ``fields.pop("client")``, weil ``update_case`` die Person nicht mehr '
            "umhaengt (Refs #1786), und nur ``update_client`` bekommt ``old_stage`` durchgereicht.",
        ),
        "EntityUpdateView._render_context": (
            2,
            "VOLLSTAENDIG ausgenommen — ehrlich gesagt: das Paar hat nur zwei Slots (Signatur "
            "und die einzige Top-Level-Anweisung ``return {...}``), und beide weichen ab. Der "
            "Struktur-Vergleich prueft an diesem Paar also nichts; die gezaehlte 2 ist eine "
            "Bestandsangabe, keine Zusicherung ueber den Kontext-Inhalt. Sachgrund ist das "
            "Personen-Pinning des Fall-Formulars: die drei Schluessel "
            "``client_locked``/``client_id``/``client_pseudonym`` (Refs #1786), deren Quelle "
            "sich zwischen GET (``case.client``) und POST-Re-Render (``request.POST``) "
            "unterscheidet — daher zwei zusaetzliche Parameter in der Signatur. Die "
            "Schluessel-Kongruenz des uebrigen Kontexts pinnt separat "
            "``test_render_context_key_sets_stay_congruent``.",
        ),
        "EntityFieldsPartialView._assigned_entity": (
            1,
            "Der Personen-Lookup filtert die beiden Widerrufs-Zustaende explizit "
            "(``is_deleted=False``, ``is_active=True``, Refs #1737/#1771); der Fall-Lookup erbt "
            "dieselbe Grenze ueber ``cases_with_live_client()`` (Refs #1777). Gleiche Zusicherung, "
            "verschiedene Bauweise — die Grenze selbst ist in beiden Faellen hart.",
        ),
    }

    # Die einzigen Kontext-Schluessel, die ``CaseUpdateView._render_context``
    # mehr fuehren darf als das Personen-Gegenstueck: das Personen-Pinning des
    # Fall-Formulars (Refs #1786). Jeder weitere Schluessel auf nur einer Seite
    # ist Drift — vgl. :meth:`test_render_context_key_sets_stay_congruent`.
    _RENDER_CONTEXT_CASE_ONLY_KEYS = ("client_locked", "client_id", "client_pseudonym")

    # Bewusste Ausnahme (Refs #2044): Nur Personen besitzen
    # ein Pseudonym und damit einen Rename-Warnzustand. Der Fall-Zwilling hat
    # fachlich keinen entsprechenden Kontext-Schluessel.
    _RENDER_CONTEXT_CLIENT_ONLY_KEYS = ("pseudonym_changed",)

    # ------------------------------------------------------------------ Ableitung

    @classmethod
    def _normalize_token(cls, text: str) -> str:
        for needle, replacement in cls._ENTITY_TOKENS:
            text = text.replace(needle, replacement)
        return text

    @classmethod
    def _normalizer(cls):
        normalize = cls._normalize_token

        class _EntityNormalizer(ast.NodeTransformer):
            """Ersetzt Entity-Token in allen Namenstraegern des AST."""

            def visit_Name(self, node):
                node.id = normalize(node.id)
                return self.generic_visit(node)

            def visit_Attribute(self, node):
                node.attr = normalize(node.attr)
                return self.generic_visit(node)

            def visit_arg(self, node):
                node.arg = normalize(node.arg)
                return self.generic_visit(node)

            def visit_keyword(self, node):
                if node.arg is not None:
                    node.arg = normalize(node.arg)
                return self.generic_visit(node)

            def visit_Constant(self, node):
                if isinstance(node.value, str):
                    node.value = normalize(node.value)
                return node

            def visit_FunctionDef(self, node):
                node.name = normalize(node.name)
                return self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node):
                node.name = normalize(node.name)
                return self.generic_visit(node)

            def visit_ClassDef(self, node):
                node.name = normalize(node.name)
                return self.generic_visit(node)

            def visit_ExceptHandler(self, node):
                if node.name is not None:
                    node.name = normalize(node.name)
                return self.generic_visit(node)

            def visit_alias(self, node):
                node.name = normalize(node.name)
                if node.asname is not None:
                    node.asname = normalize(node.asname)
                return node

            def visit_ImportFrom(self, node):
                if node.module is not None:
                    node.module = normalize(node.module)
                return self.generic_visit(node)

        return _EntityNormalizer()

    @classmethod
    def _units(cls, module: Path) -> dict[str, list[str]]:
        """Vergleichseinheiten eines Moduls: normalisierter Qualname → Anweisungs-Dumps.

        Element 0 ist die Signatur samt Dekoratoren, danach folgt je eine
        Anweisung erster Ebene (ohne Docstring).
        """
        tree = cls._normalizer().visit(ast.parse(module.read_text(encoding="utf-8")))
        units: dict[str, list[str]] = {}

        def add(key: str, func: ast.FunctionDef) -> None:
            assert key not in units, f"Namenskollision nach Normalisierung in {module}: {key}"
            body = list(func.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    body = body[1:]
            head = ast.dump(func.args) + "|" + "|".join(ast.dump(deco) for deco in func.decorator_list)
            units[key] = [head] + [ast.dump(stmt) for stmt in body]

        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                add(node.name, node)
            elif isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, ast.FunctionDef):
                        add(f"{node.name}.{child.name}", child)
        return units

    @classmethod
    def _pairs(cls) -> dict[str, tuple[list[str], list[str]]]:
        client_units = cls._units(cls._CLIENT_MODULE)
        case_units = cls._units(cls._CASE_MODULE)
        return {key: (client_units[key], case_units[key]) for key in sorted(client_units.keys() & case_units.keys())}

    @staticmethod
    def _divergence_count(left: list[str], right: list[str]) -> int:
        """Anzahl abweichender Anweisungs-Slots (difflib, reihenfolgesensitiv)."""
        matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
        return sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal")

    @classmethod
    def _divergences(cls) -> dict[str, int]:
        return {
            key: count for key, (left, right) in cls._pairs().items() if (count := cls._divergence_count(left, right))
        }

    @classmethod
    def _render_context_keys(cls, module: Path) -> set[str]:
        """Entity-normalisierte Schluessel des Dicts, das ``_render_context`` zurueckgibt.

        Bewusst auf dem NORMALISIERTEN Baum: nur so faellt der Entity-Schluessel
        selbst (``"client"`` vs. ``"case"``) als ``"entity"`` zusammen, statt als
        Unterschied durchzuschlagen und die Aussage zu verwaessern.
        """
        tree = cls._normalizer().visit(ast.parse(module.read_text(encoding="utf-8")))
        helpers = [
            child
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            for child in node.body
            if isinstance(child, ast.FunctionDef) and child.name == "_render_context"
        ]
        assert len(helpers) == 1, f"{module}: genau ein ``_render_context`` erwartet, gefunden {len(helpers)}"
        returns = [
            stmt for stmt in helpers[0].body if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Dict)
        ]
        assert len(returns) == 1, f"{module}: ``_render_context`` gibt kein einzelnes Dict-Literal zurueck"
        keys = returns[0].value.keys
        dynamic = [key for key in keys if not (isinstance(key, ast.Constant) and isinstance(key.value, str))]
        assert not dynamic, (
            f"{module}: ``_render_context`` baut Schluessel dynamisch (z.B. ``**extra``) — "
            "der Kongruenz-Vergleich kann sie nicht mehr statisch lesen und wuerde still luecken."
        )
        return {key.value for key in keys}

    # ------------------------------------------------------------------- Tests

    def test_derivation_finds_the_mirrored_module_helpers(self):
        """Positiv-Kontrolle: die Ableitung findet die bekannten Modul-Helfer.

        Ohne diesen Anker waere der Paritaets-Test still gruen, sobald die
        Extraktion (z.B. nach einem Umbau der Modulstruktur) leer zurueckkaeme.
        """
        pairs = set(self._pairs())
        expected = {
            "_entity_data_form",
            "_file_field_slugs",
            "_rerender_data_form",
            "_lock_invisible_document_type",
            "_active_attachment_chains",
            "_values_at_risk",
        }
        assert expected <= pairs, f"AST-Ableitung unvollstaendig: {sorted(pairs)}"

        # Bestandssicherung des Paar-Vorrats. Die Ableitung schneidet die
        # Schluesselmengen beider Module (``client_units.keys() & case_units``),
        # d.h. ein Spiegel-Paar, das kuenftig in NUR EINEM Modul entfernt oder
        # umbenannt wird, faellt lautlos aus dem Vergleich — der Guard bliebe
        # gruen, obwohl er weniger prueft. Diese Zahl macht so einen Schwund zum
        # Testfehler. Beim bewussten Entfernen/Zusammenfuehren eines Paares hier
        # mit anpassen (und pruefen, ob der Zwilling wirklich mit weg soll).
        assert len(pairs) == 15, (
            f"Der abgeleitete Paar-Vorrat hat sich geaendert: {len(pairs)} statt 15 Paare "
            f"({sorted(pairs)}). Entweder ist ein Spiegel-Paar nur in einem der beiden "
            "Module verschwunden/umbenannt — dann im Zwilling nachziehen —, oder die "
            "Aenderung ist gewollt und die Zahl gehoert angepasst."
        )

    def test_mirrored_pairs_stay_structurally_identical(self):
        divergences = self._divergences()
        unexpected = {
            key: count for key, count in divergences.items() if self._EXPECTED_DIVERGENCES.get(key, (0, ""))[0] != count
        }
        assert not unexpected, (
            "Der Client/Case-Spiegel ist gedriftet — diese Paare weichen "
            "strukturell anders ab als zugesichert (Refs #1830):\n"
            + "\n".join(
                f"  {key}: {count} abweichende Anweisung(en), "
                f"erwartet {self._EXPECTED_DIVERGENCES.get(key, (0, ''))[0]}"
                for key, count in sorted(unexpected.items())
            )
            + "\nEntweder den Fix im Zwilling nachziehen oder die Abweichung in "
            "_EXPECTED_DIVERGENCES mit Anzahl und Begruendung eintragen."
        )

    def test_render_context_key_sets_stay_congruent(self):
        """Beide ``_render_context``-Zwillinge liefern denselben Schluesselsatz (Refs #1830).

        Warum als EIGENER Test: Der Ausnahme-Eintrag
        ``EntityUpdateView._render_context`` deckt beide Slots des Paares ab
        (Signatur + die einzige Anweisung ``return {...}``) — der
        Struktur-Vergleich prueft an diesem Paar also nichts mehr (Grenze (a)
        im Klassen-Docstring). Empirisch blieb er gruen, als testweise der
        Schluessel ``original_document_type_id`` fehlte, ``existing_attachments``
        fehlte und ``dynamic_values_at_risk`` auf ``None`` stand.

        Dieser Test pinnt genau die motivierende Drift-Klasse aus [b27be80b]:
        ein Kontext-Schluessel, der nur in EINEM der beiden Zwillinge steht — der
        Fix wurde in der einen View gemacht und in der anderen vergessen. Werte
        prueft er bewusst nicht (das tut ``test_entity_dynamic_ui.py``), nur
        den Bestand der Schluessel.
        """
        client_keys = self._render_context_keys(self._CLIENT_MODULE)
        case_keys = self._render_context_keys(self._CASE_MODULE)
        allowed_case_extras = {self._normalize_token(key) for key in self._RENDER_CONTEXT_CASE_ONLY_KEYS}
        allowed_client_extras = {self._normalize_token(key) for key in self._RENDER_CONTEXT_CLIENT_ONLY_KEYS}

        case_only = case_keys - client_keys
        assert case_only == allowed_case_extras, (
            "Der Fall-Kontext fuehrt andere Zusatz-Schluessel als zugesichert (Refs #1830):\n"
            f"  nur im Fall: {sorted(case_only)}\n"
            f"  zugesichert: {sorted(allowed_case_extras)}\n"
            "Entweder den Schluessel im Personen-Zwilling nachziehen oder ihn in "
            "_RENDER_CONTEXT_CASE_ONLY_KEYS mit Begruendung aufnehmen."
        )

        client_only = client_keys - case_keys
        assert client_only == allowed_client_extras, (
            "Der Personen-Kontext fuehrt andere Zusatz-Schluessel als zugesichert (Refs #1830):\n"
            f"  nur in der Person: {sorted(client_only)}\n"
            f"  zugesichert: {sorted(allowed_client_extras)}\n"
            "Entweder den Schluessel im Fall-Zwilling nachziehen oder ihn in "
            "_RENDER_CONTEXT_CLIENT_ONLY_KEYS mit Begruendung aufnehmen."
        )

    def test_no_stale_expected_divergences(self):
        pairs = set(self._pairs())
        divergences = self._divergences()
        stale = {
            key: ("Paar existiert nicht mehr" if key not in pairs else "Paar ist inzwischen identisch")
            for key in self._EXPECTED_DIVERGENCES
            if key not in divergences
        }
        assert not stale, "Veraltete Eintraege in _EXPECTED_DIVERGENCES — entfernen:\n" + "\n".join(
            f"  {key}: {reason}" for key, reason in sorted(stale.items())
        )

    def test_every_expected_divergence_carries_a_reason(self):
        thin = [key for key, (_count, reason) in self._EXPECTED_DIVERGENCES.items() if len(reason.strip()) < 40]
        assert not thin, f"Diese Ausnahmen tragen keine belastbare Begruendung: {sorted(thin)}"
