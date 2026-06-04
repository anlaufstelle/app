"""Architecture-Guards — Template-Guards (CSP, Alpine, SVG-A11y, i18n, Kommentare) (Refs #929)."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture


class TestNoInlineScriptBlocksGuard:
    """Templates dürfen keine Inline-``<script>``-Blöcke enthalten.

    Die produktive CSP (``src/anlaufstelle/settings/base.py:240``)
    setzt ``script-src 'self' 'unsafe-eval'`` ohne ``unsafe-inline``.
    Inline-Scripts werden vom Browser stumm blockiert — genau der Bug
    aus #618:
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
    # CSP ohne 'unsafe-inline' stumm blockiert (siehe #662). Daher
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
            "(Refs #662).\n"
            f"Betroffen: {violations}"
        )


class TestNoJavascriptUriGuard:
    """Templates dürfen keine ``javascript:``-URIs in Attributwerten nutzen.

    Die produktive CSP setzt ``script-src 'self'`` **ohne** ``'unsafe-inline'``
    (``src/anlaufstelle/settings/base.py``). Eine Navigation zu einer
    ``javascript:``-URI — etwa ``<a href="javascript:location.reload()">`` —
    wird dadurch vom Browser stumm blockiert: Der Link/Button ist für den
    Nutzer funktionslos und löst zusätzlich einen CSP-Report aus. Genau dieser
    Bug betraf ``403_csrf.html``, ``offline.html`` und ``503.html`` (#1016, C1).

    Reload/Retry gehört CSP-konform über ``href=""`` gelöst (Navigation zur
    aktuellen URL = Neuladen, reines HTML). Für ``offline.html``/``503.html``
    ist das die einzige Option, weil diese Templates ohne Static-Pipeline
    ausgeliefert werden (kein externes ``<script src>`` möglich, kein Nonce).

    Refs #1016 (C1), #618, #662 (CSP-Härtung ``script-src``).
    """

    _TEMPLATES_DIR = Path("src/templates")
    # Matcht ``javascript:`` als Beginn eines Attributwerts:
    # ``href="javascript:…"``, ``src='javascript:…'``, ``formaction=…`` usw.
    # Reiner Prosa-Text (\"JavaScript: …\") wird nicht erfasst, weil das
    # ``="`` / ``='`` davor verlangt wird.
    _JS_URI = re.compile(r"""=\s*["']\s*javascript:""", re.IGNORECASE)

    def test_no_javascript_uri_in_templates(self):
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for template_file in self._TEMPLATES_DIR.rglob("*.html"):
            source = template_file.read_text(errors="ignore")
            for match in self._JS_URI.finditer(source):
                line = source[: match.start()].count("\n") + 1
                violations.append(f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line}")
        assert not violations, (
            "``javascript:``-URIs werden von der CSP (``script-src 'self'`` ohne "
            "``'unsafe-inline'``) stumm blockiert — der Link/Button ist tot und "
            'erzeugt einen CSP-Report. Bitte Reload/Retry über ``href=""`` '
            "(Navigation zur aktuellen URL) lösen.\n"
            "Refs #1016 (C1), #618, #662.\n"
            f"Betroffen: {violations}"
        )


class TestAlpineCspCompatibilityGuard:
    """Alpine-Komponenten muessen CSP-konform definiert werden.

    Hintergrund: Standard-Alpine wertet ``x-data="{ ... }"``-Inline-Objekte
    per dynamischer Funktionsauswertung aus und benoetigt deshalb
    ``script-src 'unsafe-eval'``.
    Die offizielle CSP-Variante (``@alpinejs/csp``) verzichtet auf
    Eval, laesst dafuer nur registrierte Komponenten zu — also
    ``x-data="myComponent"`` mit ``Alpine.data('myComponent', () => ({ ... }))``
    in einer eigenen JS-Datei.

    Dieser Guard verbietet neue Inline-Objekt-x-data-Stellen, sodass
    der spaetere Build-Wechsel nicht von neuen Verstoessen blockiert wird.

    Refs #669
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
            "Bitte Komponente in einem Modul unter src/static/js/alpine/ "
            "(base-layout/widgets/auth/forms/dashboards) per "
            "Alpine.data('name', () => ({ ... })) registrieren und im "
            "Template als 'x-data=\"name\"' referenzieren. "
            "Refs #669, #911 (Subpackage-Split)\n"
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
        attributes_in_templates`` (Refs #662) bereits verboten — daher
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
            "CSP script-src enthaelt verbotene Lockerungen. "
            "ist mit dem Wechsel auf @alpinejs/csp adressiert; Inline-Scripts "
            "und -Event-Attribute werden durch Architektur-Tests "
            "ausgeschlossen.\n"
            f"Verbotene Tokens: {violations}\n"
            f"Aktueller script-src: {script_src}\n"
            "Refs #672 (CSP-Migration), #669, #618, #662."
        )


class TestSvgAccessibilityGuard:
    """Jedes ``<svg>`` in einem Template muss WCAG-1.1.1-konform sein:
    entweder ``aria-hidden=\"true\"`` (dekorativ, vom Screen Reader ignoriert),
    ``aria-label=\"...\"``, ``role=\"img\"`` mit ``<title>``-Child oder ein
    ``<title>``-Element direkt im SVG.

    Hintergrund: Der Audit (Refs #670) hat 23+ Templates mit
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
            "Refs #669 (Phase G), #670.\n"
            f"Betroffen: {violations}"
        )


class TestNoFStringInGettextCallsGuard:
    """``_(f"…")`` und ``gettext_lazy(f"…")`` sind unbrauchbar fuer gettext.

    Der Extraktor sieht nur den fertig interpolierten Laufzeit-String,
    nicht den stabilen Quellstring mit Platzhaltern. Damit landen weder
    Variants in der ``.po``-Datei, noch koennen Uebersetzer eine sinnvolle
    Vorlage erkennen. Stattdessen ``_("…%(name)s…") % {"name": value}``
    nutzen (Refs #662).
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
            "(Refs #662).\n"
            f"Betroffen: {violations}"
        )


class TestNoMultilineDjangoCommentsGuard:
    """Django-Inline-Kommentare ``{# ... #}`` dürfen nicht über mehrere Zeilen gehen.

    Der Django-Template-Parser erkennt ``{# ... #}`` nur einzeilig
    ([Django-Docs](https://docs.djangoproject.com/en/5.1/ref/templates/language/#comments)).
    Mehrzeilige Formen werden ohne Fehlermeldung als Text ausgegeben und
    erscheinen im gerenderten HTML — Meldung aus
    #618: der Kommentartext
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
