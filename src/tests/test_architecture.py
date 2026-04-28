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
