"""Architecture-Guards — Template-Guards (CSP, Alpine, SVG-A11y, i18n, Kommentare) (Refs #929)."""

import base64
import hashlib
import json
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
    # Refs #1836: ``x-data="{{ autocomplete_component }}"`` (Django-Variable,
    # rendert zum NAMEN einer registrierten Komponente) ist CSP-konform und
    # wird per negativem Lookahead auf ``{{`` ausgenommen — ein echtes
    # Inline-Objekt beginnt mit ``{`` gefolgt von Nicht-``{``.
    _INLINE_X_DATA = re.compile(r'x-data\s*=\s*"\s*\{(?!\{)', re.IGNORECASE)

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

    # Event-Handler (``@click``/``x-on:click`` inkl. Modifiern) und ``x-init``
    # duerfen unter @alpinejs/csp nur EINEN Ausdruck enthalten. Anders als bei
    # ``_ALPINE_VALUE_DIRECTIVE`` sind Methodenaufrufe MIT Argumenten hier
    # erlaubt (z. B. ``retryQueueEntry(item.id)``) — verboten sind nur echte
    # Mehrfach-Statements: ``;``-Verkettung und Zuweisungen. Refs #1763
    # (Nachzuegler zu #1747, das nur wertbindende Direktiven wie x-text/
    # x-show erfasste, keine Event-Handler).
    _ALPINE_EVENT_HANDLER = re.compile(
        r"""(?:^|\s)
            (?:@[a-zA-Z][\w.:\-]*          # @click, @click.away, @htmx:after-swap.window, ...
            |x-on:[a-zA-Z][\w.:\-]*        # x-on:click, x-on:htmx:after-swap.window, ...
            |x-init
            )
            \s*=\s*
            "(?P<value>[^"]*)"
        """,
        re.VERBOSE,
    )
    # Zuweisung ``=`` ausserhalb von Vergleichen (``==``, ``===``, ``!=``,
    # ``!==``, ``<=``, ``>=``) und Arrow-Defaults (``=>``): kein ``=``/``!``/
    # ``<``/``>`` unmittelbar davor, kein ``=``/``>`` unmittelbar danach.
    _ASSIGNMENT_OUTSIDE_COMPARISON = re.compile(r"(?<![=!<>])=(?!=|>)")
    # Statement-Keyword am Ausdrucksanfang (Kontrollfluss/Deklaration): der
    # CSP-Parser wertet nur EINEN Ausdruck aus, solche Keywords sind immer
    # ein Statement, nie ein Ausdruck. ``\b`` verhindert Treffer auf
    # Identifiern, die nur mit dem Keyword beginnen (``forceRefresh()``,
    # ``constructor``, ...). Refs #1763 Review Fix-Runde 1.
    _STATEMENT_KEYWORD = re.compile(r"^\s*(?:if|for|while|return|var|let|const)\b")

    @staticmethod
    def _has_top_level_comma(value: str) -> bool:
        """True bei einem Komma AUSSERHALB von Klammern/Brackets (Komma-
        Operator-Sequenz wie ``a(), b()``) — Argument-Kommas innerhalb eines
        einzelnen Aufrufs (``foo(a, b)``) bleiben erlaubt, weil sie zu genau
        einem Ausdruck gehoeren. Refs #1763 Review Fix-Runde 1.

        Refs #1775: String-Literale werden uebersprungen. Ohne das meldete
        ``$dispatch('toast', 'a, b')`` — Komma IM Literal, Klammer-Tiefe egal —
        einen Verstoss, den es nicht gibt: ein False Positive dieses Guards
        blockiert einen korrekten Handler und ist nur durch Umformulieren des
        UI-Textes zu umgehen. Der Attributwert ist doppelt gequotet, Literale
        darin sind also ``'``/`````; ``"`` wird der Vollstaendigkeit halber
        mitgefuehrt.
        """
        depth = 0
        quote: str | None = None
        escaped = False
        for ch in value:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if quote is not None:
                if ch == quote:
                    quote = None
                continue
            if ch in "'\"`":
                quote = ch
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                return True
        return False

    @classmethod
    def _event_handler_violation(cls, value: str) -> str | None:
        """Verstoss-Beschreibung oder ``None``, wenn ``value`` als
        Event-Handler-Wert unter @alpinejs/csp zulaessig ist (ein einzelner
        Ausdruck — Methodenaufrufe mit Argumenten, ``$dispatch(...)``,
        ternaere Ausdruecke bleiben erlaubt). Verboten: ``;``-Verkettung,
        Statement-Keywords (if/for/while/return/var/let/const),
        Top-Level-Komma-Sequenzen und Zuweisungen ausserhalb Vergleich/
        Arrow-Default.
        """
        if ";" in value:
            return f"Mehrfach-Statement (';'): {value!r}"
        if cls._STATEMENT_KEYWORD.match(value):
            return f"Statement-Keyword (if/for/while/return/var/let/const): {value!r}"
        if cls._has_top_level_comma(value):
            return f"Komma-Sequenz (Komma ausserhalb Klammern): {value!r}"
        if cls._ASSIGNMENT_OUTSIDE_COMPARISON.search(value):
            return f"Zuweisung: {value!r}"
        return None

    def test_no_statement_expressions_in_event_handlers(self):
        """Event-Handler-Statements brechen den @alpinejs/csp-Build silent,
        genau wie bei den bereits erfassten wertbindenden Direktiven (Refs
        #693). Erfasst ``;``-Verkettung, Statement-Keywords
        (if/for/while/return/var/let/const) und Top-Level-Komma-Sequenzen
        sowie Zuweisungen. Methodenaufrufe mit Argumenten, ``$dispatch(...)``
        und ternaere Ausdruecke bleiben erlaubt — sie sind ein einzelner
        Ausdruck. Refs #1763 (inkl. Review Fix-Runde 1: Event-Namen mit
        ``:`` wie ``htmx:after-swap`` wurden vorher gar nicht erst erkannt,
        und ein Inline-``if (...)`` ohne ``;``/``=`` schlug durch).
        """
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for template_file in self._TEMPLATES_DIR.rglob("*.html"):
            source = template_file.read_text(errors="ignore")
            for match in self._ALPINE_EVENT_HANDLER.finditer(source):
                reason = self._event_handler_violation(match.group("value"))
                if reason is None:
                    continue
                line = source[: match.start()].count("\n") + 1
                violations.append(f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line} {reason}")
        assert not violations, (
            "Event-Handler (@..., x-on:..., x-init) enthalten echte Statements "
            "(';'-Verkettung, Statement-Keyword, Komma-Sequenz oder Zuweisung). "
            "Der @alpinejs/csp-Build wertet nur einen einzelnen Ausdruck aus "
            "(Methodenaufrufe mit Argumenten sind erlaubt) — bitte in eine "
            "benannte Component-Methode (Alpine.data(...)) auslagern, die "
            "intern alle Schritte ausfuehrt.\n"
            "Refs #1763, #1747, #693.\n"
            f"Betroffen: {violations}"
        )

    def test_event_handler_name_regex_matches_colon_events(self):
        """Review Fix-Runde 1 (#1763): Event-Namen mit ``:`` (HTMX-Custom-
        Events wie ``htmx:after-swap``) muessen als Event-Handler erkannt
        werden — sonst greift die Statement-Erkennung oben gar nicht erst.
        Beide Formen sind die empirischen #1747-Regressionsfixtures aus dem
        Review (die reale #1747-Stelle betraf eine wertbindende Direktive
        und ist laengst gefixt; hier wird nur das Namensmuster gegen diese
        Klasse abgesichert, damit ein kuenftiger ``@htmx:after-swap.window``-
        oder ``x-on:htmx:after-swap.window``-Handler nicht erneut durchrutscht).
        """
        at_form = '<div @htmx:after-swap.window="if (open) fetchAndRender()"></div>'
        xon_form = '<div x-on:htmx:after-swap.window="close(); toggleOffline()"></div>'
        for html in (at_form, xon_form):
            matches = list(self._ALPINE_EVENT_HANDLER.finditer(html))
            assert matches, f"Event-Handler-Regex erkennt {html!r} nicht (':' im Event-Namen fehlt)."
            assert self._event_handler_violation(matches[0].group("value")) is not None

    def test_if_statement_in_event_handler_is_a_violation(self):
        """Die zweite #1747-Fixture: ``if (...) ...`` hat weder ``;`` noch
        ``=`` — ohne die Keyword-Regel waere das ein blinder Fleck."""
        assert self._event_handler_violation("if (open) fetchAndRender()") is not None

    def test_comma_sequence_in_event_handler_is_a_violation(self):
        assert self._event_handler_violation("retryQueueEntry(item.id), discardQueueEntry(item.id)") is not None

    # Refs #1775: String-Literal-Bewusstsein von ``_has_top_level_comma``.
    # Die Klammer-Zaehlung sah in Literale hinein: eine Klammer IM Text
    # verschob die Tiefe und liess ein Argument-Komma als Komma-Operator
    # erscheinen (False Positive) bzw. ein echtes Komma verschwinden
    # (False Negative). Beide Faelle sind unten belegt — ohne das Tracking
    # liefert die alte Fassung genau das falsche Ergebnis.
    @pytest.mark.parametrize(
        "value",
        [
            # Schliessende Klammer im UI-Text: alte Fassung sah Tiefe 0 am
            # Argument-Komma und meldete einen Verstoss, den es nicht gibt.
            "notify('Smiley :) im Text', item.id)",
            "setMessage('Bitte pruefen :)', true)",
            "$dispatch('toast', 'Gespeichert, bitte pruefen')",
            "setMessage(`Ja, gern`)",
            "notify('a\\', b')",  # maskiertes Quote im Literal
        ],
    )
    def test_comma_inside_string_literal_is_not_a_violation(self, value):
        assert self._has_top_level_comma(value) is False, value
        assert self._event_handler_violation(value) is None, value

    @pytest.mark.parametrize(
        "value",
        [
            # Oeffnende Klammer im Literal: alte Fassung stand am echten
            # Komma auf Tiefe 2 und uebersah den Verstoss.
            "emit('('), reset()",
            "setMessage('a'), other()",
            "notify('a, b'), other()",
            "a, b",
        ],
    )
    def test_comma_outside_string_literal_stays_a_violation(self, value):
        """Gegenprobe: das Tracking darf die Erkennung nicht abschalten."""
        assert self._has_top_level_comma(value) is True, value
        assert self._event_handler_violation(value) is not None, value

    def test_ternary_in_event_handler_remains_allowed(self):
        assert self._event_handler_violation("open ? close() : toggle()") is None

    def test_method_call_with_arguments_in_event_handler_remains_allowed(self):
        assert self._event_handler_violation("retryQueueEntry(item.id)") is None

    def test_bare_identifier_event_handler_remains_allowed(self):
        assert self._event_handler_violation("toggle") is None

    def test_dispatch_call_in_event_handler_remains_allowed(self):
        assert self._event_handler_violation("$dispatch('retention-bulk-change')") is None

    def test_identifier_with_keyword_prefix_is_not_a_false_positive(self):
        """``forceRefresh()`` beginnt mit ``for``, ist aber kein for-Statement
        — die Keyword-Regel braucht eine Wortgrenze, kein Praefix-Match."""
        assert self._event_handler_violation("forceRefresh()") is None

    # Zwei identisch benannte Alpine-Event-Attribute auf demselben Tag: der
    # HTML-Parser verwirft das zweite stillschweigend, nur der erste Handler
    # laeuft je (Refs #1764). Die Rueckreferenz ``\1`` erzwingt denselben
    # Attributnamen; ``\s+`` zwischen den beiden Treffern ueberschreitet nie
    # eine Tag-Grenze (dafuer waere ein literales ``>``/``<`` noetig), daher
    # ist kein vollstaendiges Tag-Parsing noetig.
    _DUPLICATE_ALPINE_EVENT_ATTR = re.compile(r'((?:@|x-on:)[a-zA-Z][\w.:\-]*)\s*=\s*"[^"]*"\s+\1\s*=\s*"[^"]*"')

    def test_no_duplicate_alpine_event_attribute_per_tag(self):
        """Ein Tag darf ein Alpine-Event-Attribut (``@change`` etc.) nicht
        zweimal tragen: der HTML-Parser verwirft das Duplikat, nur der erste
        Handler wird je ausgefuehrt — Refs #1764 (``@change="setDocTypeFilter"
        @change="fetchAndRender"`` liess den Chart-Refetch beim
        Dokumentationstyp-Filter ungewollt aus).
        """
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for template_file in self._TEMPLATES_DIR.rglob("*.html"):
            source = template_file.read_text(errors="ignore")
            for match in self._DUPLICATE_ALPINE_EVENT_ATTR.finditer(source):
                line = source[: match.start()].count("\n") + 1
                violations.append(
                    f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line} "
                    f"doppeltes Event-Attribut: {match.group(0)!r}"
                )
        assert not violations, (
            "Diese Tags tragen dasselbe Alpine-Event-Attribut (z. B. @change) "
            "zweimal direkt hintereinander. Der HTML-Parser verwirft das "
            "Duplikat — nur der erste Handler laeuft. Bitte beide Schritte in "
            "einer benannten Component-Methode zusammenfassen und ein "
            "einzelnes Attribut binden.\nRefs #1764.\n"
            f"Betroffen: {violations}"
        )

    def test_duplicate_colon_event_name_is_detected(self):
        """Review Fix-Runde 2 (#1763): dieselbe ':'-Luecke wie bei
        ``_ALPINE_EVENT_HANDLER`` (Fix-Runde 1) bestand auch im Namensmuster
        der Duplikat-Regel — ein doppelt gebundenes kolon-benanntes
        Custom-Event (HTMX-Events wie ``htmx:after-swap``) entging der
        Pruefung, weil ``:`` im Attributnamen nicht Teil der Zeichenklasse
        war."""
        html = '<div @htmx:after-swap.window="a()" @htmx:after-swap.window="b()"></div>'
        matches = list(self._DUPLICATE_ALPINE_EVENT_ATTR.finditer(html))
        assert matches, f"Duplikat-Regex erkennt {html!r} nicht (':' im Event-Namen fehlt)."

    # Ein Tag, zwei Reaktionswege auf dasselbe ``change`` (Refs #1797). Anders
    # als beim Duplikat-Guard oben stehen die beiden Attribute nicht zwingend
    # nebeneinander, daher wird der Tag-Rumpf als Ganzes betrachtet: ein
    # einzelner Regex-Durchlauf ueber ``<tag …>``, kein HTML-Parsing. Gequotete
    # Attributwerte duerfen ``<``/``>`` tragen, deshalb die Alternation.
    _TAG_WITH_ATTRS = re.compile(
        r"""<(?P<name>[a-zA-Z][\w:-]*)
            (?P<attrs>(?:"[^"]*"|'[^']*'|[^<>"'])*)
            /?>""",
        re.VERBOSE,
    )
    # ``@change``/``x-on:change`` inkl. Modifiern (``@change.debounce.300ms``),
    # aber NICHT gleichnamige Praefixe wie ``@changed``.
    _ALPINE_CHANGE_ATTR = re.compile(r"""(?:^|\s)(?:@|x-on:)change(?:\.[\w.\-]+)?\s*=\s*["']""")
    _HX_TRIGGER_ATTR = re.compile(r"""(?:^|\s)hx-trigger\s*=\s*["'](?P<value>[^"']*)["']""")
    _HX_REQUEST_ATTR = re.compile(r"""(?:^|\s)hx-(?:get|post|put|patch|delete)\s*=\s*["']""")
    # Elemente, deren htmx-Default-Trigger ohne ``hx-trigger`` ``change`` ist.
    _HTMX_CHANGE_DEFAULT_TAGS = frozenset({"select", "input", "textarea"})

    @classmethod
    def _double_change_channel(cls, tag_name: str, attrs: str) -> str | None:
        """Beschreibt den Verstoss, wenn ein Tag auf ``change`` zwei Wege faehrt."""
        if not cls._ALPINE_CHANGE_ATTR.search(attrs):
            return None
        trigger = cls._HX_TRIGGER_ATTR.search(attrs)
        if trigger is not None:
            # ``changed`` ist ein htmx-Filter-Modifier, kein change-Event —
            # die Wortgrenze haelt ``keyup changed delay:300ms`` heraus.
            if re.search(r"\bchange\b", trigger.group("value")):
                return f'hx-trigger="{trigger.group("value")}" neben Alpine-@change'
            return None
        if cls._HX_REQUEST_ATTR.search(attrs) and tag_name.lower() in cls._HTMX_CHANGE_DEFAULT_TAGS:
            return (
                f"impliziter change-Default (hx-get/hx-post ohne hx-trigger am "
                f"<{tag_name.lower()}>) neben Alpine-@change"
            )
        return None

    def test_no_double_change_channel_per_tag(self):
        """Ein Tag darf auf ``change`` nicht gleichzeitig htmx-swappen UND
        einen Alpine-``@change``-Handler ausfuehren — Refs #1797.

        Praezisierung (Review-Befund #1797): Ein Alpine-``@change`` ist ein
        zweiter LISTENER, kein zweiter Request-Emitter — die doppelten Swaps
        im Ereignisformular kamen aus zwei ``change``-EVENTS (synthetischer
        Autosave-Restore-Dispatch + Nutzerauswahl; dagegen ``hx-sync``) bzw.
        aus Interaktion mit den Alt-Knoten waehrend des laufenden Swaps
        (dagegen ``hx-disabled-elt``). Der Guard verhindert die Kanal-DRIFT:
        ein separater ``@change`` laeuft auch fuer Events, deren htmx-Request
        nie startet oder abgebrochen wird, und sein Zustand laeuft dann am
        Swap-Kanal vorbei. Ein Kanal genuegt: die Alpine-Logik am
        htmx-Lebenszyklus (``htmx:beforeRequest``) registrieren.

        Bekannte Grenze: der Scan sieht nur Templates. Trigger, die in
        Python-Widget-``attrs`` stehen (``forms.Select(attrs={"hx-trigger": …})``),
        erfasst er nicht — die werden erst beim Rendern zu HTML.
        """
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for template_file in self._TEMPLATES_DIR.rglob("*.html"):
            source = template_file.read_text(errors="ignore")
            for match in self._TAG_WITH_ATTRS.finditer(source):
                reason = self._double_change_channel(match.group("name"), match.group("attrs"))
                if reason is None:
                    continue
                line = source[: match.start()].count("\n") + 1
                violations.append(f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line} {reason}")
        assert not violations, (
            "Diese Tags reagieren doppelt auf ``change``: htmx swappt UND ein "
            "Alpine-@change-Handler laeuft. Der Alpine-Handler driftet dann vom "
            "Swap-Kanal weg (er laeuft auch fuer Events ohne bzw. mit "
            "abgebrochenem Request). Bitte die Alpine-Logik in ``init()`` an "
            "``htmx:beforeRequest`` des Elements haengen; gegen doppelte "
            'change-EVENTS zusaetzlich ``hx-sync="this:replace"`` setzen.\n'
            "Refs #1797.\n"
            f"Betroffen: {violations}"
        )

    def test_alpine_change_without_htmx_is_not_a_violation(self):
        """Gegenprobe: ``@change`` ohne htmx-Attribute ist voellig in Ordnung
        (z. B. der Chart-Filter in ``statistics/partials/dashboard_content.html``).
        """
        html = '<select :value="docTypeFilter" @change="onDocTypeChange" class="x">'
        match = self._TAG_WITH_ATTRS.search(html)
        assert match is not None
        assert self._double_change_channel(match.group("name"), match.group("attrs")) is None

    def test_implicit_htmx_change_default_on_select_is_detected(self):
        """Ohne ``hx-trigger`` triggert htmx ein ``<select>`` auf ``change`` —
        zusammen mit ``@change`` ist das derselbe Doppelkanal."""
        html = '<select @change="onDocTypeChange" hx-get="/x/" hx-target="#dynamic-fields">'
        match = self._TAG_WITH_ATTRS.search(html)
        assert match is not None
        assert self._double_change_channel(match.group("name"), match.group("attrs")) is not None

    def test_changed_filter_modifier_is_not_mistaken_for_change_event(self):
        """``hx-trigger="keyup changed delay:300ms"`` feuert auf ``keyup``;
        ``changed`` ist nur ein Filter — kein Doppelkanal."""
        html = '<input @change="mark" hx-get="/x/" hx-trigger="keyup changed delay:300ms">'
        match = self._TAG_WITH_ATTRS.search(html)
        assert match is not None
        assert self._double_change_channel(match.group("name"), match.group("attrs")) is None

    def test_tag_scan_survives_gt_inside_quoted_attribute(self):
        """Ein ``>`` in einem gequoteten Attributwert darf den Tag-Rumpf nicht
        vorzeitig abschneiden — sonst entginge das dahinter stehende
        ``hx-trigger`` der Pruefung."""
        html = '<select title="a > b" @change="f" hx-get="/x/" hx-trigger="change">'
        match = self._TAG_WITH_ATTRS.search(html)
        assert match is not None
        assert "hx-trigger" in match.group("attrs")
        assert self._double_change_channel(match.group("name"), match.group("attrs")) is not None

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


class TestNoInlineStylesGuard:
    """CSP ``style-src`` ist strikt: kein ``'unsafe-inline'`` (Refs #1660, I5 aus #1376).

    Ohne ``'unsafe-inline'`` blockt der Browser **stumm**

    * jedes ``style="…"``-Attribut im Markup (auch ``style="--var: x"``) und
    * jeden ``<style>``-Block, dessen Inhalt nicht per ``'sha256-…'``/Nonce
      freigegeben ist.

    Nicht blockiert ist die CSSOM-API (``el.style.setProperty(…)``) — genau
    darüber setzen Alpine (``x-show``/``x-cloak``) und Chart.js ihre Styles.

    Zwei Ausnahmen, beide bewusst:

    * ``core/export/*_pdf.html`` rendern **serverseitig** über WeasyPrint
      (``weasyprint.HTML(string=…)``) zu PDF und erreichen nie einen Browser —
      dort gilt keine CSP.
    * Die selbsttragenden Sonderseiten ``500.html``/``503.html``/``offline.html``
      müssen auch dann korrekt aussehen, wenn ``/static/`` nicht ausgeliefert
      wird (Serverfehler, Wartungsmodus) bzw. wenn das Netz weg ist
      (Service-Worker-Precache). Ihr ``<style>``-Block bleibt inline und ist
      per sha256-Hash in ``style-src`` gepinnt — Hashes sind gegenüber Nonces
      stabil, weil der SW gecachtes HTML mit altem Header ausliefert.
    """

    _TEMPLATES_DIR = Path("src/templates")
    # Serverseitig via WeasyPrint gerendert -> keine Browser-CSP.
    _PDF_TEMPLATE_PREFIX = "core/export/"
    # Sonderseiten mit gehashtem Inline-``<style>``.
    _HASHED_STYLE_TEMPLATES = ("500.html", "503.html", "offline.html")

    # ``style=`` als Attribut (mit fuehrendem Whitespace, um ``data-style=``
    # o. ae. nicht zu treffen).
    _STYLE_ATTR = re.compile(r"""\sstyle\s*=\s*["']""", re.IGNORECASE)
    _STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)

    @classmethod
    def _browser_templates(cls):
        for template_file in sorted(cls._TEMPLATES_DIR.rglob("*.html")):
            rel = template_file.relative_to(cls._TEMPLATES_DIR).as_posix()
            if rel.startswith(cls._PDF_TEMPLATE_PREFIX):
                continue
            yield rel, template_file

    @staticmethod
    def _csp_hash(block: str) -> str:
        digest = hashlib.sha256(block.encode("utf-8")).digest()
        return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"

    def test_no_style_attributes_in_browser_templates(self):
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for rel, template_file in self._browser_templates():
            source = template_file.read_text(errors="ignore")
            for match in self._STYLE_ATTR.finditer(source):
                violations.append(f"{rel}:{source[: match.start()].count('\n') + 1}")
        assert not violations, (
            "Diese Templates setzen Inline-``style=``-Attribute. Die CSP "
            "(style-src ohne 'unsafe-inline') blockt die stumm — das Element "
            "rendert ungestylt, ohne Fehlermeldung.\n"
            "Loesung: statische Werte als Tailwind-Utility/CSS-Klasse "
            "(src/static/css/input.css), dynamische Werte aus JS heraus per "
            "``el.style.setProperty(...)`` (CSSOM ist CSP-frei). Auch "
            '``style="--var: x"`` ist ein Inline-Style und braucht '
            "'unsafe-hashes' — daher ebenfalls verboten.\n"
            "Refs #1660 (I5 aus #1376).\n"
            f"Betroffen: {violations}"
        )

    def test_no_unhashed_style_blocks_in_browser_templates(self):
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for rel, template_file in self._browser_templates():
            if rel in self._HASHED_STYLE_TEMPLATES:
                continue
            source = template_file.read_text(errors="ignore")
            if self._STYLE_BLOCK.search(source):
                violations.append(rel)
        assert not violations, (
            "Diese Templates enthalten Inline-``<style>``-Bloecke. Die CSP "
            "(style-src ohne 'unsafe-inline') blockt die stumm.\n"
            "Loesung: CSS nach src/static/css/input.css auslagern (wird ueber "
            "styles.css ausgeliefert). Nur selbsttragende Sonderseiten, die "
            "OHNE /static/ funktionieren muessen, duerfen inline bleiben — "
            "dann Hash in CONTENT_SECURITY_POLICY['DIRECTIVES']['style-src'] "
            "eintragen und hier allowlisten.\n"
            "Refs #1660 (I5 aus #1376).\n"
            f"Betroffen: {violations}"
        )

    def test_hashed_style_blocks_are_pinned_in_csp(self):
        """Jeder erlaubte Inline-``<style>``-Block muss per sha256 in
        ``style-src`` stehen — sonst blockt der Browser ihn stumm.

        Der Test rechnet die Hashes aus dem Template neu aus: schon ein
        geaendertes Leerzeichen bricht den Hash und faellt hier auf, statt
        erst im Browser (ungestylte Fehler-/Offline-Seite).
        """
        from anlaufstelle.settings.base import CONTENT_SECURITY_POLICY

        style_src = CONTENT_SECURITY_POLICY["DIRECTIVES"].get("style-src", [])
        missing = []
        expected = []
        for name in self._HASHED_STYLE_TEMPLATES:
            template_file = self._TEMPLATES_DIR / name
            if not template_file.exists():
                pytest.skip(f"{template_file} nicht vorhanden")
            blocks = self._STYLE_BLOCK.findall(template_file.read_text())
            assert blocks, f"{name} enthaelt keinen <style>-Block mehr — Allowlist bereinigen."
            for block in blocks:
                digest = self._csp_hash(block)
                expected.append(f"{name} -> {digest}")
                if digest not in style_src:
                    missing.append(f"{name} -> {digest}")
        assert not missing, (
            "Der Inline-``<style>``-Block dieser Sonderseiten ist nicht (mehr) "
            "per Hash in der CSP freigegeben — der Browser blockt ihn stumm, "
            "die Seite rendert ungestylt.\n"
            "Loesung: Hash in src/anlaufstelle/settings/base.py "
            "(CONTENT_SECURITY_POLICY -> style-src) aktualisieren.\n"
            "Refs #1660.\n"
            f"Fehlend: {missing}\n"
            f"Soll: {expected}\n"
            f"Aktueller style-src: {style_src}"
        )

    def test_csp_style_src_is_strict(self):
        """``style-src`` darf kein ``'unsafe-inline'`` und kein
        ``'unsafe-hashes'`` enthalten.

        Inline-``style=``-Attribute und ungehashte ``<style>``-Bloecke sind
        durch die Guards oben ausgeschlossen — ``'unsafe-inline'`` ist damit
        nirgends noetig. ``'unsafe-hashes'`` wuerde die Hash-Freigabe wieder
        auf ``style=``-Attribute ausdehnen und den Gewinn zunichtemachen.
        """
        from anlaufstelle.settings.base import CONTENT_SECURITY_POLICY

        style_src = CONTENT_SECURITY_POLICY["DIRECTIVES"].get("style-src", [])
        forbidden = ["'unsafe-inline'", "'unsafe-hashes'"]
        violations = [t for t in forbidden if t in style_src]
        assert not violations, (
            "CSP style-src enthaelt verbotene Lockerungen. Inline-Styles sind "
            "durch TestNoInlineStylesGuard ausgeschlossen; selbsttragende "
            "Sonderseiten laufen ueber sha256-Hashes.\n"
            f"Verbotene Tokens: {violations}\n"
            f"Aktueller style-src: {style_src}\n"
            "Refs #1660 (I5 aus #1376)."
        )

    def test_htmx_indicator_styles_are_disabled(self):
        """htmx injiziert ohne ``includeIndicatorStyles: false`` selbst einen
        Inline-``<style>``-Block fuer ``.htmx-indicator`` (htmx 2.0.10,
        ``Wn()``) — der waere ohne ``'unsafe-inline'`` blockiert und alle
        Lade-Spinner blieben dauerhaft sichtbar. base.html schaltet das per
        ``<meta name="htmx-config">`` ab, input.css liefert die Regeln
        statisch nach.
        """
        base_template = self._TEMPLATES_DIR / "base.html"
        if not base_template.exists():
            pytest.skip("base.html nicht vorhanden")
        source = base_template.read_text()
        assert 'name="htmx-config"' in source and '"includeIndicatorStyles"' in source, (
            'base.html muss ``<meta name="htmx-config" '
            "content='{\"includeIndicatorStyles\": false}'>`` setzen, sonst "
            "injiziert htmx einen von der CSP blockierten Inline-<style>.\n"
            "Refs #1660."
        )
        css = Path("src/static/css/input.css")
        assert css.exists() and ".htmx-indicator" in css.read_text(), (
            "src/static/css/input.css muss die .htmx-indicator-Regeln statisch "
            "enthalten (Ersatz fuer die abgeschaltete htmx-Injektion).\nRefs #1660."
        )

    def test_htmx_does_not_settle_style_attributes(self):
        """htmx settled per Default ``class,style,width,height`` zwischen
        id-gleichen Swap-Elementen via ``setAttribute`` — fuer ``style`` ist das
        unter ``style-src`` ohne ``'unsafe-hashes'`` ein stummer CSP-Verstoss
        (Konsolen-/csp-report-Rauschen; Befund R12 aus dem Persona-Pilot
        #1501; Material sind von Alpine ``x-show`` materialisierte
        ``style``-Attribute). base.html muss ``attributesToSettle`` OHNE
        ``style`` konfigurieren.
        """
        base_template = self._TEMPLATES_DIR / "base.html"
        if not base_template.exists():
            pytest.skip("base.html nicht vorhanden")
        source = base_template.read_text()
        match = re.search(r'name="htmx-config"\s+content=\'(?P<cfg>\{.*?\})\'', source)
        assert match, 'base.html: <meta name="htmx-config"> nicht gefunden.'
        config = json.loads(match.group("cfg"))
        settle = config.get("attributesToSettle")
        assert settle is not None and "style" not in settle, (
            "htmx-config in base.html muss attributesToSettle ohne 'style' "
            'setzen (z. B. ["class", "width", "height"]) — sonst kopiert '
            "der Settle-Schritt style-Attribute per setAttribute und die CSP "
            "blockiert das als Inline-Style.\nRefs #1660, #1501 (R12)."
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


class TestLogoutIsAlwaysPostGuard:
    """Logout darf nie über einen GET-``<a href>`` ausgelöst werden.

    ``CustomLogoutView`` erbt von Djangos ``auth_views.LogoutView``, das seit
    Django 5 **POST-only** ist — ein GET auf ``/logout/`` liefert 405 (Refs
    #1031). Genau das passierte auf der MFA-Login-/Setup-Seite, deren
    „Abmelden"-Link ein ``<a href="{% url 'logout' %}">`` war. Logout muss
    immer über ein ``<form method="post">`` (mit ``{% csrf_token %}``) laufen,
    wie in ``partials/_navigation_desktop.html``.
    """

    _TEMPLATES_DIR = Path("src/templates")
    # ``<a … href="{% url 'logout' %}" …>`` — Anchor (GET) auf die Logout-URL.
    _GET_LOGOUT_ANCHOR = re.compile(
        r"""<a\b[^>]*href=["']\{%\s*url\s+['"]logout['"]\s*%\}["']""",
        re.IGNORECASE,
    )

    def test_no_get_anchor_triggers_logout(self):
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = []
        for template_file in self._TEMPLATES_DIR.rglob("*.html"):
            source = template_file.read_text(errors="ignore")
            for match in self._GET_LOGOUT_ANCHOR.finditer(source):
                line = source[: match.start()].count("\n") + 1
                violations.append(f"{template_file.relative_to(self._TEMPLATES_DIR)}:{line}")
        assert not violations, (
            "Logout über GET-<a href> liefert 405 (POST-only LogoutView, Refs "
            '#1031). Bitte ein <form method="post" action="{% url \'logout\' %}"> '
            "mit {% csrf_token %} + Submit-Button nutzen (Muster: "
            "partials/_navigation_desktop.html).\n"
            f"Betroffen: {violations}"
        )


class TestStandaloneDocumentGuard:
    """Jedes eigenstaendige HTML-Dokument traegt Kopf- und Fussbaustein (Refs #2102).

    Templates mit eigenem ``<html>`` erben NICHT von ``base.html`` und pflegten
    ihren ``<head>`` und ihren Footer bis #2102 jeweils von Hand. Ergebnis: nur
    ``login.html`` hatte den Kopf vollstaendig, zehn Seiten (2FA-Code,
    Backup-Code, MFA-Setup, Passwortwechsel, Lockout-Recovery, facility_*,
    500/503, offline) hatten **gar keinen** Footer — und damit auch keinen
    AGPL-§13-Quellcode-Link (Refs #835) und ein ``/favicon.ico``-404 (Refs
    #973, dort nur fuer Login behoben).

    Der Guard faengt die naechste neue Standalone-Seite sofort ab, statt erst
    im Handtest. Zwei Ausnahmegruppen, beide begruendet:

    * ``core/export/*_pdf.html`` rendern serverseitig via WeasyPrint zu PDF und
      erreichen nie einen Browser — kein Favicon, kein Footer.
    * ``500.html``/``503.html``/``offline.html`` tragen **keinen Footer**: sie
      muessen ohne ``/static/`` und ohne DB funktionieren (Serverfehler,
      Wartungsmodus, Netz weg) und werden ohne Context-Processor-Stack
      gerendert (``render_to_string`` ohne ``request``) — ``SOURCE_CODE_URL``
      und die Rechtstext-URLs sind dort schlicht nicht im Kontext. Der
      Kopfbaustein bleibt dort Pflicht (nur ``<link>``/``<meta>``, der
      gehashte ``<style>``-Block bleibt unberuehrt).
    """

    _TEMPLATES_DIR = Path("src/templates")
    # Serverseitig via WeasyPrint gerendert -> kein Browser-Dokument.
    _PDF_TEMPLATE_PREFIX = "core/export/"
    _HEAD_INCLUDE = "components/_document_head.html"
    _FOOTER_INCLUDE = "components/_public_footer.html"
    # Begruendete Ausnahmen vom Footer — siehe Klassen-Docstring.
    _FOOTERLESS_TEMPLATES = ("500.html", "503.html", "offline.html")

    _HTML_TAG = re.compile(r"<html\b", re.IGNORECASE)
    _HARDCODED_LANG = re.compile(r"""<html\b[^>]*\slang\s*=\s*["']de["']""", re.IGNORECASE)

    @classmethod
    def _standalone_documents(cls, root=None):
        """Alle Templates unterhalb ``root`` mit eigenem ``<html>``-Element.

        ``root`` ist parametrisiert, damit der Negativtest gegen ``tmp_path``
        laufen kann, statt eine Probe in den echten Template-Baum zu legen
        (xdist-Race).
        """
        root = Path(root) if root is not None else cls._TEMPLATES_DIR
        for template_file in sorted(root.rglob("*.html")):
            rel = template_file.relative_to(root).as_posix()
            if rel.startswith(cls._PDF_TEMPLATE_PREFIX):
                continue
            source = template_file.read_text(errors="ignore")
            if not cls._HTML_TAG.search(source):
                continue
            yield rel, source

    def test_every_standalone_document_includes_the_head_block(self):
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        documents = list(self._standalone_documents())
        assert documents, "Kein Standalone-Template gefunden — Scanner defekt."
        violations = [rel for rel, source in documents if self._HEAD_INCLUDE not in source]
        assert not violations, (
            "Diese eigenstaendigen HTML-Dokumente binden den gemeinsamen "
            f'Kopfbaustein nicht ein: {{% include "{self._HEAD_INCLUDE}" %}}. '
            "Ohne ihn fehlen Favicon, apple-touch-icon, Manifest und "
            "theme-color — /favicon.ico liefert 404 (Refs #973, #2102).\n"
            f"Betroffen: {violations}"
        )

    def test_every_standalone_document_includes_the_public_footer(self):
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = [
            rel
            for rel, source in self._standalone_documents()
            if rel not in self._FOOTERLESS_TEMPLATES and self._FOOTER_INCLUDE not in source
        ]
        assert not violations, (
            "Diese eigenstaendigen HTML-Dokumente binden den gemeinsamen "
            f'Fussbaustein nicht ein: {{% include "{self._FOOTER_INCLUDE}" %}}. '
            "Damit fehlt der AGPL-§13-Quellcode-Link (Refs #835, #2102). "
            "Neue Ausnahmen gehoeren begruendet in _FOOTERLESS_TEMPLATES, nicht "
            "stillschweigend in die Vorlage.\n"
            f"Betroffen: {violations}"
        )

    def test_no_standalone_document_hardcodes_german_lang(self):
        if not self._TEMPLATES_DIR.exists():
            pytest.skip(f"{self._TEMPLATES_DIR} nicht vorhanden")
        violations = [rel for rel, source in self._standalone_documents() if self._HARDCODED_LANG.search(source)]
        assert not violations, (
            'Diese Dokumente stehen hart auf <html lang="de">. Bei aktiver '
            "EN-Praeferenz ist das fuer Screenreader falsch ausgezeichnet "
            "(Refs #2067, #2102).\n"
            "Loesung: lang=\"{{ LANGUAGE_CODE|default:'de' }}\" (i18n-Context-"
            "Processor) bzw. lang=\"{{ page_language|default:'de' }}\" auf den "
            "Middleware-Seiten.\n"
            f"Betroffen: {violations}"
        )

    def test_scanner_flags_a_synthetic_standalone_document(self, tmp_path):
        """Negativtest gegen ``tmp_path``: der Scanner erkennt ein Dokument
        ohne Bausteine und mit hartem ``lang`` — und ueberspringt PDF-Exports.

        Ohne diesen Test koennte der Scanner still auf 0 Treffer laufen und
        die drei Guards oben waeren dauerhaft gruen, ohne etwas zu pruefen.
        """
        (tmp_path / "kaputt.html").write_text(
            '<!DOCTYPE html>\n<html lang="de">\n<head></head><body></body>\n</html>\n',
            encoding="utf-8",
        )
        (tmp_path / "partial.html").write_text('<div class="x">kein Dokument</div>\n', encoding="utf-8")
        pdf_dir = tmp_path / "core" / "export"
        pdf_dir.mkdir(parents=True)
        (pdf_dir / "report_pdf.html").write_text('<html lang="de"><body></body></html>\n', encoding="utf-8")

        found = dict(self._standalone_documents(root=tmp_path))

        assert list(found) == ["kaputt.html"], "Scanner darf nur echte Browser-Dokumente melden."
        source = found["kaputt.html"]
        assert self._HEAD_INCLUDE not in source
        assert self._FOOTER_INCLUDE not in source
        assert self._HARDCODED_LANG.search(source)

    def test_shared_building_blocks_stay_csp_clean(self):
        """Kopf- und Fussbaustein duerfen weder Skript noch Inline-Style tragen.

        Beide werden in ``login.html`` eingebunden, das bewusst tracking-frei
        bleibt (``context_processors.py`` — Umami-Kommentar), und in die
        precachten Offline-Shells, die keine Auth-Verzweigung tragen duerfen.
        """
        for name in (self._HEAD_INCLUDE, self._FOOTER_INCLUDE):
            path = self._TEMPLATES_DIR / name
            assert path.exists(), f"{name} fehlt — der gemeinsame Baustein ist die einzige Quelle (Refs #2102)."
            source = path.read_text(encoding="utf-8")
            assert "<script" not in source.lower(), (
                f"{name} darf kein <script> tragen (CSP + tracking-freie Login-Seite)."
            )
            assert not re.search(r"""\sstyle\s*=\s*["']""", source, re.IGNORECASE), (
                f"{name} darf kein Inline-style=-Attribut tragen (CSP style-src ohne 'unsafe-inline')."
            )
