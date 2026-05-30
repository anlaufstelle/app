"""Architecture-Guards — View-Guards (Rate-Limit, Redirect-Hygiene, Routes, ProdSettings) (Refs Welle 6 #929)."""

import re
from pathlib import Path
from typing import ClassVar

import pytest


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
    """Jede user-facing Route muss irgendwo als ``{% url '<name>' %}`` auftauchen.

    Wenn ein Feature hinzugefügt wird, aber kein Template einen Link/Button
    rendert, ist es ein halb-eingebauter Zustand — genau das Muster, das
    #605 vermeiden
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
