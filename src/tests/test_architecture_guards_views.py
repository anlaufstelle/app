"""Architecture-Guards — View-Guards (Rate-Limit, Redirect-Hygiene, Routes, ProdSettings) (Refs #929)."""

import ast
import difflib
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture


class TestRateLimitOnAllMutations:
    """Jede CBV-Klasse mit ``def post(...)`` in ``src/core/views/`` muss
    einen ``@ratelimit``-Decorator (per ``method_decorator`` oder direkt) tragen.

    Hintergrund: Brute-Force-/Spray-Schutz auf Mutationen ist Pflicht. Der
    Audit (Refs #670) hat 19 ungeschuetzte Handler gezeigt — von
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
            "Refs #669 (Phase F), #670.\n"
            f"Betroffen: {violations}"
        )


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
        # Refs #1813 (Konzept #1801 §7.1): der externe Bericht war die einzige
        # Seite im Repo mit NULL Links — nur per Direkt-URL erreichbar. Seit der
        # Statistik-Reiterzeile hat er einen Einstieg; der Eintrag hier haelt den
        # Orphan-Fix fest, statt ihn dem naechsten Umbau zu ueberlassen.
        "core:statistics_external_report",
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

    **Was der Guard NICHT leistet:** Er sieht nur Abweichungen auf der Ebene
    ganzer Anweisungen erster Ebene. Aendert sich etwas **innerhalb** einer
    bereits als abweichend gezaehlten Anweisung (z.B. im grossen ``try``-Block
    von ``EntityUpdateView.post``), bleibt die Zahl gleich und der Guard still.
    Unpaarige Einheiten (``_get_case_event_context``, ``ClientAutocompleteView``,
    ``CaseCloseView``, …) vergleicht er gar nicht — sie haben kein Gegenstueck.
    Und er prueft Struktur, nicht Verhalten: dass die Kopien dasselbe TUN,
    beweisen die Zwillings-Testdateien ``test_clients_dynamic_ui.py`` /
    ``test_cases_dynamic_ui.py``, nicht dieser Guard.
    """

    _CLIENT_MODULE = Path("src/core/views/clients.py")
    _CASE_MODULE = Path("src/core/views/cases.py")

    # Entity-Token-Normalisierung. Reihenfolge ist unkritisch: kein Ersatzwert
    # enthaelt ein weiteres Suchmuster. Plural- und Ableitungsformen fallen
    # ueber die Teilstring-Ersetzung mit ab (``clients``/``cases`` → ``entitys``,
    # ``CaseModel`` → ``EntityModel``, ``cases_with_live_client`` →
    # ``entitys_with_live_entity``).
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
        "EntityFieldsPartialView._assigned_entity": (
            1,
            "Der Personen-Lookup filtert die beiden Widerrufs-Zustaende explizit "
            "(``is_deleted=False``, ``is_active=True``, Refs #1737/#1771); der Fall-Lookup erbt "
            "dieselbe Grenze ueber ``cases_with_live_client()`` (Refs #1777). Gleiche Zusicherung, "
            "verschiedene Bauweise — die Grenze selbst ist in beiden Faellen hart.",
        ),
    }

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
