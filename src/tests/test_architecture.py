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


class TestEventEncryptionBypassGuard:
    """Refs #736 / #713 (Audit-Massnahme #11): Verhindert, dass irgendein
    Code-Pfad die Encryption-Pipeline in ``Event.save()``/``encryption.py``
    umgeht.

    Drei Patterns sind verboten:
    - ``Event.objects.bulk_create(...)`` — kein ``save()``-Hook
    - ``Event.objects.filter(...).update(data_json=...)`` — Raw-Update
    - ``Event.objects.update_or_create(defaults={"data_json": ...})`` — auch ohne ``save()``-Hook

    Allowlist (legitime Bypaesse):
    - ``src/core/services/encryption.py`` — die Encryption-Pipeline selbst
    - ``src/core/seed/`` — Seed-Daten sind deterministische Test-Fixtures
      ohne reale Art-9-Inhalte
    - ``src/core/migrations/`` — Schema-Migrationen
    - ``src/tests/`` — Testfixtures duerfen direkt schreiben

    Erweiterung der Allowlist erfordert separaten Commit + Begruendung.
    """

    _CORE_DIR = Path("src/core")
    _ALLOWLIST = (
        Path("src/core/services/encryption.py"),
        Path("src/core/seed"),
        Path("src/core/migrations"),
    )
    # Drei Bypass-Patterns:
    # 1) Event.objects.bulk_create(...) — direkt
    # 2) Event.objects[.filter(...)|.exclude(...)|...].update(data_json=...)
    # 3) Event.objects.update_or_create(..., data_json=...)
    # Pattern 2 erlaubt chained QuerySet-Methoden zwischen ``objects`` und
    # ``update(``; ``[^=\n]{0,200}?`` schliesst Multi-line-Statements aus
    # und limitiert das Matching auf eine sinnvolle Distanz.
    _BYPASS_BULK_CREATE = re.compile(r"Event\.objects\.bulk_create\b")
    # Erlaubt chained QuerySet-Methoden zwischen ``objects`` und ``update(`` —
    # ``Event.objects.filter(...).update(data_json=...)`` ist der haeufigste
    # Bypass-Pfad. Die ``\.update\(``-Klausel matcht ``update`` exakt (nicht
    # ``update_or_create``, weil dort ``_or_create`` zwischen ``update`` und
    # ``(`` steht).
    _BYPASS_UPDATE = re.compile(
        r"Event\.objects(?:\.[a-zA-Z_]+\([^)]*\))*\.update\([^)]*\bdata_json\b",
    )
    _BYPASS_UPDATE_OR_CREATE = re.compile(
        r"Event\.objects\.update_or_create\([^)]*\bdata_json\b",
    )

    def _is_allowlisted(self, path: Path) -> bool:
        return any(path == allow or allow in path.parents for allow in self._ALLOWLIST)

    _BYPASS_PATTERNS = (
        ("bulk_create", _BYPASS_BULK_CREATE),
        ("update(data_json=...)", _BYPASS_UPDATE),
        ("update_or_create(data_json=...)", _BYPASS_UPDATE_OR_CREATE),
    )

    def test_no_event_encryption_bypass(self):
        if not self._CORE_DIR.exists():
            pytest.skip(f"{self._CORE_DIR} nicht vorhanden")
        violations = []
        for py_file in self._CORE_DIR.rglob("*.py"):
            if self._is_allowlisted(py_file):
                continue
            source = py_file.read_text(errors="ignore")
            for label, pattern in self._BYPASS_PATTERNS:
                for match in pattern.finditer(source):
                    line = source[: match.start()].count("\n") + 1
                    violations.append(f"{py_file}:{line} [{label}] — {match.group(0)[:120]}")
        assert not violations, (
            "Folgende Stellen umgehen die Event-Encryption-Pipeline. Encryption "
            "lebt in services/encryption.py und wird per Event.save() angewendet — "
            "bulk_create / .update(data_json=...) / .update_or_create(data_json=...) "
            "schreiben Klartext direkt in JSONB.\n"
            "Refs #736 / #713 (Audit-Massnahme #11).\n"
            f"Verstoesse: {violations}"
        )


class TestServiceLayerDirectionGuard:
    """Models dürfen nicht modul-weit aus ``core.services`` importieren.

    Schichtregel aus [`CLAUDE.md`](https://github.com/tobiasnix/anlaufstelle/blob/main/CLAUDE.md):
    Business-Logik gehört in ``services/``, nicht in Models. Modul-Level-
    Imports von Services in Models drehen die Schicht-Richtung um und
    schaffen zirkuläre Import-Risiken.

    Function-local Imports (innerhalb von Methoden) sind erlaubt und
    notwendig, um Zirkular-Imports zu vermeiden — z. B.
    [`Client.anonymize()`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/models/client.py)
    delegiert an ``services/clients.py:anonymize_client``.

    Refs #743 (Audit-Befund: ``Client.anonymize`` durchbrach Aggregat-Grenzen).
    """

    _MODELS_DIR = Path("src/core/models")
    # Top-of-file region: alles bis zur ersten ``class ``/``def `` Zeile.
    _SERVICE_IMPORT = re.compile(r"^\s*(from core\.services|import core\.services)", re.MULTILINE)

    def test_no_module_level_service_imports_in_models(self):
        violations = []
        for py_file in self._MODELS_DIR.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            source = py_file.read_text()
            # Truncate at first top-level class/def to ignore function-local imports.
            top_match = re.search(r"^(class|def) ", source, re.MULTILINE)
            top_region = source[: top_match.start()] if top_match else source
            if self._SERVICE_IMPORT.search(top_region):
                violations.append(py_file.name)
        assert not violations, (
            "Diese Model-Dateien importieren ``core.services`` auf Modul-Ebene. "
            "Das verstößt gegen die Schichtregel (Models ⟵ Services, nicht "
            "umgekehrt). Imports in Methoden verschieben oder Logik in den "
            "Service-Layer ziehen.\n"
            f"Betroffen: {violations}"
        )


class TestNoInlineScriptBlocksGuard:
    """Templates dürfen keine Inline-``<script>``-Blöcke enthalten.

    Die produktive CSP ([`base.py:240`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/anlaufstelle/settings/base.py#L240))
    setzt ``script-src 'self' 'unsafe-eval'`` ohne ``unsafe-inline``.
    Inline-Scripts werden vom Browser stumm blockiert — genau der Bug
    aus [#618](https://github.com/tobiasnix/anlaufstelle/issues/618):
    Alpine-Komponenten, die in einem Inline-Script definiert waren,
    standen nicht zur Verfügung und Buttons sahen für den Nutzer wie
    funktionslos aus. Fix: alle JS-Funktionen in eigene Dateien
    auslagern und per ``<script src="...">`` laden.
    """

    _TEMPLATES_DIR = Path("src/templates")
    # Matcht ``<script>`` ohne Attribut direkt nach dem Tag. Rein
    # ``<script src="…">`` ist erlaubt, weil der Browser die externe
    # Datei lädt — kein CSP-Konflikt. Auch ``<script defer src>`` etc.
    # sind erlaubt.
    _INLINE_SCRIPT = re.compile(r"<script\s*>", re.IGNORECASE)

    def test_no_inline_script_blocks_in_templates(self):
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for template_file in self._TEMPLATES_DIR.rglob("*.html"):
            source = template_file.read_text(errors="ignore")
            if self._INLINE_SCRIPT.search(source):
                violations.append(str(template_file.relative_to(self._TEMPLATES_DIR)))
        assert not violations, (
            "Diese Templates enthalten Inline-<script>-Blöcke. Die CSP blockt die "
            "stumm, Alpine-Komponenten werden dadurch unsichtbar kaputt "
            "(Refs #618). Bitte JS in eigene static/js/*.js-Dateien auslagern "
            "und via <script src=\"{% static 'js/…' %}\"></script> laden.\n"
            f"Betroffen: {violations}"
        )

    # Inline-Event-Attribute (onchange, onclick, onsubmit, ...) werden von der
    # CSP ohne 'unsafe-inline' stumm blockiert (siehe #662 FND-01). Daher
    # alle ``\son[a-z]+=`` in Templates verbieten — Listener gehoeren in
    # eigene static/js/*.js-Dateien.
    _INLINE_EVENT = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)

    def test_no_inline_event_attributes_in_templates(self):
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for template_file in self._TEMPLATES_DIR.rglob("*.html"):
            source = template_file.read_text(errors="ignore")
            for match in self._INLINE_EVENT.finditer(source):
                line = source[: match.start()].count("\n") + 1
                violations.append(f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line}")
        assert not violations, (
            "Inline-Event-Attribute (z. B. onchange=, onclick=) werden von der CSP "
            "ohne 'unsafe-inline' stumm blockiert. Bitte JS-Listener in eigene "
            "static/js/*.js-Dateien auslegen und ueber data-Attribute anbinden "
            "(Refs #662 FND-01).\n"
            f"Betroffen: {violations}"
        )


class TestAlpineCspCompatibilityGuard:
    """Alpine-Komponenten muessen CSP-konform definiert werden.

    Hintergrund: Standard-Alpine wertet ``x-data="{ ... }"``-Inline-Objekte
    per dynamischer Funktionsauswertung aus und benoetigt deshalb
    ``script-src 'unsafe-eval'`` (Audit-Finding S-6 aus
    [`docs/audits/2026-04-21-tiefenanalyse-v0.10.md`](https://github.com/tobiasnix/anlaufstelle/blob/main/docs/audits/2026-04-21-tiefenanalyse-v0.10.md)).
    Die offizielle CSP-Variante (``@alpinejs/csp``) verzichtet auf
    Eval, laesst dafuer nur registrierte Komponenten zu — also
    ``x-data="myComponent"`` mit ``Alpine.data('myComponent', () => ({ ... }))``
    in einer eigenen JS-Datei.

    Dieser Guard verbietet neue Inline-Objekt-x-data-Stellen, sodass
    der spaetere Build-Wechsel nicht von neuen Verstoessen blockiert wird.

    Refs [#669](https://github.com/tobiasnix/anlaufstelle/issues/669)
    """

    _TEMPLATES_DIR = Path("src/templates")
    # Matcht ``x-data="{...}"`` aber nicht ``x-data="myComponent"``.
    # Auch mehrzeilige Inline-Objekte werden erfasst, weil das ``"{``
    # direkt im Attribut steht.
    _INLINE_X_DATA = re.compile(r'x-data\s*=\s*"\s*\{', re.IGNORECASE)

    def test_no_inline_x_data_objects_in_templates(self):
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for template_file in self._TEMPLATES_DIR.rglob("*.html"):
            source = template_file.read_text(errors="ignore")
            for match in self._INLINE_X_DATA.finditer(source):
                line = source[: match.start()].count("\n") + 1
                violations.append(f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line}")
        assert not violations, (
            "Inline-Objekt-x-data ('x-data=\"{ ... }\"') ist nicht CSP-kompatibel. "
            "Bitte Komponente in src/static/js/alpine-components.js (oder eigener "
            "Datei) per Alpine.data('name', () => ({ ... })) registrieren und im "
            "Template als 'x-data=\"name\"' referenzieren. "
            "Refs #669 (Phase 1, S-6)\n"
            f"Betroffen: {violations}"
        )

    # Alpine-Direktiven, deren Werte unter ``@alpinejs/csp`` nur einfache
    # Property-Zugriffe, Vergleiche und Object-Literale enthalten duerfen.
    # Ternaeres ``? :``, logische ``||`` / ``&&`` und Method-Calls mit
    # Argumenten loesen unter dem CSP-Build Eval aus und brechen darum
    # silent. Refs #693.
    _ALPINE_VALUE_DIRECTIVE = re.compile(
        r"""(?:^|\s)
            (?::[a-zA-Z][\w:-]*           # :class, :value, :aria-selected, ...
            |x-text
            |x-html
            |x-show
            |x-if
            |x-effect
            |x-bind:[a-zA-Z][\w:-]*
            |x-model(?::[\w.]+)?
            )
            \s*=\s*
            "(?P<value>[^"]*)"
        """,
        re.VERBOSE,
    )
    # ``hx-on::evt`` und ``hx-on:evt`` werden intern via ``Function()``
    # ausgewertet und brauchen ``script-src 'unsafe-eval'``. Refs #692.
    _HTMX_INLINE_HANDLER = re.compile(r"\shx-on:", re.IGNORECASE)

    def test_no_unsafe_alpine_expressions_in_templates(self):
        """Alpine-Direktiven duerfen keine Ternaries, ||/&& oder
        Method-Calls mit Argumenten enthalten — sonst bricht
        ``@alpinejs/csp`` (Refs #693).
        """
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for template_file in self._TEMPLATES_DIR.rglob("*.html"):
            source = template_file.read_text(errors="ignore")
            for match in self._ALPINE_VALUE_DIRECTIVE.finditer(source):
                value = match.group("value")
                stripped = value.strip()
                # Object-Literale (``{ 'foo': bar }``) — der gevendor'te
                # ``@alpinejs/csp`` 3.14.8 kann sie nicht zuverlaessig
                # tokenisieren (verschluckt das schliessende ``}``).
                # Bitte ueber String-Property bzw. Getter binden.
                if stripped.startswith("{") and stripped.endswith("}"):
                    line = source[: match.start()].count("\n") + 1
                    violations.append(
                        f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line} "
                        f"Object-Literal in Alpine-Direktive (CSP-Build "
                        f"3.14.8 inkompatibel): {value!r}"
                    )
                    continue
                # Ternary ``? ... :`` (mit Whitespace, aber Doppelpunkt
                # nicht in Object-Keys).
                if re.search(r"\?[^?]*:", value):
                    line = source[: match.start()].count("\n") + 1
                    violations.append(
                        f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line} "
                        f"ternary in Alpine-Direktive: {value!r}"
                    )
                    continue
                if "||" in value or "&&" in value:
                    line = source[: match.start()].count("\n") + 1
                    violations.append(
                        f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line} "
                        f"logischer Operator in Alpine-Direktive: {value!r}"
                    )
                    continue
                # Method-Call mit Argumenten: ``foo(...)`` mit Inhalt in
                # Klammern (auch ``a.b(c)``). Aufruf ohne Argumente
                # (``foo()``) ist unter @alpinejs/csp ebenfalls verboten,
                # bleibt aber als eigene Heuristik fuer kuenftige
                # Verschaerfungen ungenutzt. Verweis auf Property-Getter.
                if re.search(r"[a-zA-Z_]\w*\([^)]+\)", value):
                    line = source[: match.start()].count("\n") + 1
                    violations.append(
                        f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line} "
                        f"Method-Call mit Argumenten in Alpine-Direktive: {value!r}"
                    )
        assert not violations, (
            "Alpine-Direktiven enthalten Ausdruecke, die der "
            "@alpinejs/csp-Build nicht ausfuehrt. Bitte Ternaries durch "
            "Object-Syntax ``:class=\"{ 'foo': cond }\"`` oder einen "
            "Computed-Getter ersetzen, ``||`` / ``&&`` in einen Getter "
            "auslagern, Method-Calls in property-getter umbauen.\n"
            "Refs #693, #672.\n"
            f"Betroffen: {violations}"
        )

    def test_no_htmx_inline_handlers_in_templates(self):
        """``hx-on::evt`` Inline-Handler werden intern per ``Function()``
        evaluiert und brauchen ``script-src 'unsafe-eval'``. Refs #692.
        """
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for template_file in self._TEMPLATES_DIR.rglob("*.html"):
            source = template_file.read_text(errors="ignore")
            for match in self._HTMX_INLINE_HANDLER.finditer(source):
                line = source[: match.start()].count("\n") + 1
                violations.append(f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line}")
        assert not violations, (
            "HTMX-Inline-Handler ``hx-on::`` / ``hx-on:`` werden vom "
            "Browser per ``Function()`` ausgewertet und brauchen "
            "``script-src 'unsafe-eval'``. Bitte Listener in eigene "
            "static/js/*.js-Datei auslegen und auf ``htmx:beforeRequest`` "
            "(o. a.) am ``document.body`` reagieren.\n"
            "Refs #692.\n"
            f"Betroffen: {violations}"
        )

    def test_csp_script_src_is_strict(self):
        """CSP ``script-src`` darf weder ``'unsafe-inline'`` noch
        ``'unsafe-eval'`` enthalten.

        Inline-Scripts werden durch ``TestNoInlineScriptBlocksGuard`` (Refs
        #618) und Inline-Event-Attribute durch ``test_no_inline_event_-
        attributes_in_templates`` (Refs #662 FND-01) bereits verboten — daher
        darf ``'unsafe-inline'`` nie noetig sein.

        ``'unsafe-eval'`` wurde mit dem Wechsel auf den ``@alpinejs/csp``-Build
        entfernt (Refs #672). Inline-Objekt-x-data ist durch
        ``TestAlpineCspCompatibilityGuard`` ausgeschlossen, alle komplexen
        Expressions sind in Component-Methoden ausgelagert, alle Komponenten
        per ``Alpine.data()`` registriert.
        """
        from anlaufstelle.settings.base import CONTENT_SECURITY_POLICY

        script_src = CONTENT_SECURITY_POLICY["DIRECTIVES"].get("script-src", [])
        forbidden = ["'unsafe-inline'", "'unsafe-eval'"]
        violations = [t for t in forbidden if t in script_src]
        assert not violations, (
            "CSP script-src enthaelt verbotene Lockerungen. Audit-Finding S-6 "
            "ist mit dem Wechsel auf @alpinejs/csp adressiert; Inline-Scripts "
            "und -Event-Attribute werden durch Architektur-Tests "
            "ausgeschlossen.\n"
            f"Verbotene Tokens: {violations}\n"
            f"Aktueller script-src: {script_src}\n"
            "Refs #672 (CSP-Migration), #669 (Phase 1A), #618, #662 FND-01."
        )


class TestRateLimitOnAllMutations:
    """Jede CBV-Klasse mit ``def post(...)`` in ``src/core/views/`` muss
    einen ``@ratelimit``-Decorator (per ``method_decorator`` oder direkt) tragen.

    Hintergrund: Brute-Force-/Spray-Schutz auf Mutationen ist Pflicht. Der
    Audit (Refs #670 FND-14) hat 19 ungeschuetzte Handler gezeigt — von
    ``MFADisableView`` ueber ``ClientCreateView`` bis ``RetentionApproveView``.

    Ausnahmen muessen in der Allowlist stehen und mit Begruendung dokumentiert
    sein.
    """

    _VIEWS_DIR = Path("src/core/views")
    _ALLOWLIST = {
        # name format "<file>:<class>" — keep empty unless a handler is
        # genuinely safe to leave unrestricted (e.g. internal-only HTMX
        # partial that requires already-authenticated session and has its
        # own per-record locking). Document the reason inline.
    }

    def test_all_post_handlers_have_ratelimit(self):
        if not self._VIEWS_DIR.exists():
            pytest.skip(f"{self._VIEWS_DIR} nicht vorhanden")
        violations = []
        for py_file in sorted(self._VIEWS_DIR.glob("*.py")):
            if py_file.name == "__init__.py":
                continue
            source = py_file.read_text()
            for cls_match in re.finditer(r"^class (\w+)\(", source, re.MULTILINE):
                cls_name = cls_match.group(1)
                cls_body_start = cls_match.end()
                next_cls = re.search(r"^class \w+\(", source[cls_body_start:], re.MULTILINE)
                cls_body = source[cls_body_start : cls_body_start + (next_cls.start() if next_cls else 10**9)]
                # Capture decorator block above ``def post(self, ...)``
                post_match = re.search(
                    r"((?:^    @[^\n]+\n)*)^    def post\(self",
                    cls_body,
                    re.MULTILINE,
                )
                if not post_match:
                    continue
                decorators = post_match.group(1) or ""
                # Class-level decorators above the ``class`` line. Multi-line
                # decorators (``@method_decorator(\n    ratelimit(...),\n)``)
                # erstrecken sich ueber mehrere Zeilen; daher den ganzen
                # Block ab der ersten ``@``-Zeile bis zur class-Zeile lesen.
                pre = source[: cls_match.start()]
                last_block = pre.rsplit("\n\n", 1)[-1] if "\n\n" in pre else pre
                class_decos = last_block if "@" in last_block else ""
                if "ratelimit" in decorators or "ratelimit" in class_decos:
                    continue
                identifier = f"{py_file.name}:{cls_name}"
                if identifier in self._ALLOWLIST:
                    continue
                violations.append(identifier)
        assert not violations, (
            "POST-Handler ohne @ratelimit-Decorator. Bitte "
            "@method_decorator(ratelimit(key='user', rate=RATELIMIT_MUTATION, "
            "method='POST', block=True)) ergaenzen — fuer Bulk-Aktionen "
            "RATELIMIT_BULK_ACTION (30/h) verwenden, sonst RATELIMIT_MUTATION "
            "(60/h). Echte Ausnahmen in TestRateLimitOnAllMutations._ALLOWLIST "
            "mit Begruendung eintragen.\n"
            "Refs #669 (Phase F), #670 FND-14.\n"
            f"Betroffen: {violations}"
        )


class TestRateLimitOnSensitiveGetEndpoints:
    """Refs #737 / #713 (Audit-Massnahme #13): GET-Endpoints mit
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
    _SENSITIVE_GET_VIEWS = (("clients.py", "ClientAutocompleteView"),)

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
            "Refs #737 / #713 (Audit-Massnahme #13).\n"
            f"Verstoesse: {violations}"
        )


class TestSvgAccessibilityGuard:
    """Jedes ``<svg>`` in einem Template muss WCAG-1.1.1-konform sein:
    entweder ``aria-hidden=\"true\"`` (dekorativ, vom Screen Reader ignoriert),
    ``aria-label=\"...\"``, ``role=\"img\"`` mit ``<title>``-Child oder ein
    ``<title>``-Element direkt im SVG.

    Hintergrund: Der Audit (Refs #670 FND-15) hat 23+ Templates mit
    dekorativen SVGs ohne ``aria-hidden`` gefunden — Screen Reader lesen
    sonst die XML-Source-Tokens vor (z.B. \"image\", Path-Daten). Default
    in der App ist ``aria-hidden=\"true\"``; nur SVG-Icon-Buttons ohne
    Text-Label brauchen ``aria-label`` (auf dem ``<button>`` ODER auf
    dem SVG selbst).
    """

    _TEMPLATES_DIR = Path("src/templates")
    _SVG_OPEN = re.compile(r"<svg\b([^>]*)>", re.IGNORECASE)
    _ARIA_HIDDEN = re.compile(r'aria-hidden\s*=\s*"true"', re.IGNORECASE)
    _ARIA_LABEL = re.compile(r"aria-label\s*=", re.IGNORECASE)
    _ROLE_IMG = re.compile(r'role\s*=\s*"img"', re.IGNORECASE)

    def test_all_svgs_have_a11y_attribute(self):
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for template_file in self._TEMPLATES_DIR.rglob("*.html"):
            source = template_file.read_text(errors="ignore")
            for match in self._SVG_OPEN.finditer(source):
                attrs = match.group(1)
                if self._ARIA_HIDDEN.search(attrs) or self._ARIA_LABEL.search(attrs) or self._ROLE_IMG.search(attrs):
                    continue
                # Look ahead for a <title> child within this svg block
                tail = source[match.end() :]
                close = tail.lower().find("</svg>")
                if close >= 0 and "<title" in tail[:close].lower():
                    continue
                line = source[: match.start()].count("\n") + 1
                violations.append(f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line}")
        assert not violations, (
            'Diese SVGs haben weder aria-hidden="true" noch aria-label noch '
            'role="img" mit <title>. WCAG 2.1 SC 1.1.1: dekorative SVGs '
            "muessen vom Screen Reader ignoriert werden, nicht-dekorative "
            "muessen einen Text-Alternative bieten. "
            "Refs #669 (Phase G), #670 FND-15.\n"
            f"Betroffen: {violations}"
        )


class TestNoFStringInGettextCallsGuard:
    """``_(f"…")`` und ``gettext_lazy(f"…")`` sind unbrauchbar fuer gettext.

    Der Extraktor sieht nur den fertig interpolierten Laufzeit-String,
    nicht den stabilen Quellstring mit Platzhaltern. Damit landen weder
    Variants in der ``.po``-Datei, noch koennen Uebersetzer eine sinnvolle
    Vorlage erkennen. Stattdessen ``_("…%(name)s…") % {"name": value}``
    nutzen (Refs #662 FND-07).
    """

    _SRC_DIR = Path("src/core")
    # Greift ``_(f"…")`` und ``gettext(_lazy)?(f"…")`` mit ein- oder doppelten
    # Anfuehrungszeichen, ueber whitespace (auch newline) hinweg.
    _PATTERN = re.compile(r"(?:gettext(?:_lazy)?|_)\(\s*f[\"\']", re.IGNORECASE)

    def test_no_fstring_in_gettext_calls(self):
        if not self._SRC_DIR.exists():
            pytest.skip(f"{self._SRC_DIR} nicht vorhanden")
        violations = []
        for py_file in self._SRC_DIR.rglob("*.py"):
            # Tests selbst duerfen f-Strings auch in gettext-Aufrufen demoen.
            if "/tests/" in str(py_file):
                continue
            source = py_file.read_text(errors="ignore")
            for match in self._PATTERN.finditer(source):
                line = source[: match.start()].count("\n") + 1
                violations.append(f"{py_file.relative_to(self._SRC_DIR.parent)}:{line}")
        assert not violations, (
            "f-Strings in ``_(...)`` sind unbrauchbar fuer gettext: der Extraktor "
            "sieht nur den interpolierten Laufzeit-String. Bitte auf "
            '``_("...%(name)s...") % {"name": value}`` umstellen '
            "(Refs #662 FND-07).\n"
            f"Betroffen: {violations}"
        )


class TestNoMultilineDjangoCommentsGuard:
    """Django-Inline-Kommentare ``{# ... #}`` dürfen nicht über mehrere Zeilen gehen.

    Der Django-Template-Parser erkennt ``{# ... #}`` nur einzeilig
    ([Django-Docs](https://docs.djangoproject.com/en/5.1/ref/templates/language/#comments)).
    Mehrzeilige Formen werden ohne Fehlermeldung als Text ausgegeben und
    erscheinen im gerenderten HTML — Meldung aus
    [#618](https://github.com/tobiasnix/anlaufstelle/issues/618): der Kommentartext
    stand roh zwischen Tabelle und Toast auf der Klientel-Liste. Für
    mehrzeilige Kommentare ``{% comment %} ... {% endcomment %}`` nutzen —
    oder den Kommentar ganz weglassen, da Commit-Message und Code-Historie
    die Begründung ohnehin tragen.
    """

    _TEMPLATES_DIR = Path("src/templates")
    _INLINE_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)

    def test_no_multiline_django_comments_in_templates(self):
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for template_file in self._TEMPLATES_DIR.rglob("*.html"):
            source = template_file.read_text(errors="ignore")
            for match in self._INLINE_COMMENT.finditer(source):
                if "\n" in match.group(0):
                    line = source[: match.start()].count("\n") + 1
                    violations.append(f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line}")
        assert not violations, (
            "Mehrzeilige ``{# ... #}``-Kommentare werden von Django als Text "
            "ausgegeben (Refs #618). Bitte ``{% comment %} ... {% endcomment %}`` "
            "nutzen oder den Kommentar weglassen.\n"
            f"Betroffen: {violations}"
        )


class TestUserFacingEntryPointGuard:
    """Jede user-facing Route muss irgendwo als ``{% url '<name>' %}`` auftauchen.

    Wenn ein Feature hinzugefügt wird, aber kein Template einen Link/Button
    rendert, ist es ein halb-eingebauter Zustand — genau das Muster, das
    [#605](https://github.com/tobiasnix/anlaufstelle/issues/605) vermeiden
    soll. Die Allowlist enthält nur solche URLs, die bewusst nur per Deep-
    Link, aus dem Code oder aus HTMX-Partials heraus aufgerufen werden.
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

        missing = []
        for url_name in self._REQUIRED_URL_NAMES:
            if url_name in self._ALLOWLIST:
                continue
            # `{% url 'name' ... %}` oder `{% url "name" ... %}`
            pattern = re.compile(rf"""\{{%\s*url\s+['"]{re.escape(url_name)}['"]""")
            if not pattern.search(templates_text):
                missing.append(url_name)
        assert not missing, (
            "Diese URL-Names haben keinen sichtbaren Einstieg in irgendeinem Template — "
            "das Feature ist halb-eingebaut. Entweder Template-Link ergänzen oder "
            "URL-Name aus _REQUIRED_URL_NAMES entfernen (falls bewusst deep-linked). "
            f"Fehlend: {sorted(missing)}"
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
