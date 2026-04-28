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

    def test_csp_script_src_has_no_unsafe_inline(self):
        """CSP ``script-src`` darf kein ``'unsafe-inline'`` enthalten.

        Inline-Scripts werden durch ``TestNoInlineScriptBlocksGuard`` (Refs
        #618) und Inline-Event-Attribute durch ``test_no_inline_event_-
        attributes_in_templates`` (Refs #662 FND-01) bereits verboten — daher
        darf ``'unsafe-inline'`` nie noetig sein.

        Hinweis: ``'unsafe-eval'`` ist aktuell bewusst akzeptiert, weil
        Alpine.js (Standard-Build) `x-show`/`@event`-Expressions per dyn-
        Funktionsauswertung auswertet. Der Wechsel auf @alpinejs/csp scheitert
        am restriktiven Expression-Subset des CSP-Builds und braucht eine
        eigene Migrations-Phase (Folge-Issue zu #669).
        """
        from anlaufstelle.settings.base import CONTENT_SECURITY_POLICY

        script_src = CONTENT_SECURITY_POLICY["DIRECTIVES"].get("script-src", [])
        assert "'unsafe-inline'" not in script_src, (
            "CSP script-src enthaelt 'unsafe-inline'. Inline-Scripts/Event-"
            "Attribute werden durch Architektur-Tests bereits ausgeschlossen "
            "(Refs #618, #662 FND-01) — daher darf 'unsafe-inline' nicht im "
            "script-src stehen.\n"
            f"Aktueller script-src: {script_src}"
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
                next_cls = re.search(
                    r"^class \w+\(", source[cls_body_start:], re.MULTILINE
                )
                cls_body = source[
                    cls_body_start : cls_body_start
                    + (next_cls.start() if next_cls else 10**9)
                ]
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
                last_block = (
                    pre.rsplit("\n\n", 1)[-1] if "\n\n" in pre else pre
                )
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
    _ARIA_LABEL = re.compile(r'aria-label\s*=', re.IGNORECASE)
    _ROLE_IMG = re.compile(r'role\s*=\s*"img"', re.IGNORECASE)

    def test_all_svgs_have_a11y_attribute(self):
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for template_file in self._TEMPLATES_DIR.rglob("*.html"):
            source = template_file.read_text(errors="ignore")
            for match in self._SVG_OPEN.finditer(source):
                attrs = match.group(1)
                if (
                    self._ARIA_HIDDEN.search(attrs)
                    or self._ARIA_LABEL.search(attrs)
                    or self._ROLE_IMG.search(attrs)
                ):
                    continue
                # Look ahead for a <title> child within this svg block
                tail = source[match.end() :]
                close = tail.lower().find("</svg>")
                if close >= 0 and "<title" in tail[:close].lower():
                    continue
                line = source[: match.start()].count("\n") + 1
                violations.append(
                    f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line}"
                )
        assert not violations, (
            "Diese SVGs haben weder aria-hidden=\"true\" noch aria-label noch "
            "role=\"img\" mit <title>. WCAG 2.1 SC 1.1.1: dekorative SVGs "
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
