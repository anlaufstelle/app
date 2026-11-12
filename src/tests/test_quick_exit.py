"""Mitschauen-/Shoulder-Surfing-Schutz — Settings, Markup, Audit (Refs #2136).

Befund **N-2** der Fachlich-/Datenschutz-Analyse: Die Oberflaeche hatte keinen
Quick-Exit, kein Tastenkuerzel und keinen Sichtschutz. Fuer die Einsatzzwecke
Frauenhaus/Gewaltberatung und Awareness-Teams ist das eine fachliche
Vorbedingung, nicht Komfort — dokumentiert wird dort in Gegenwart von Personen,
vor denen der Bildschirminhalt geheim bleiben muss.

Was hier festgehalten wird (das Verhalten des Skripts steht in
``tests/js/quick-exit.test.mjs``, der Browser-Pfad in
``src/tests/e2e/test_quick_exit.py``):

* die beiden Einrichtungs-Felder samt Vorgaben und Schema-Einschraenkung,
* dass sie ueber das Formular editierbar UND auditiert sind (die Whitelist in
  ``update_settings`` wirft sonst zur Laufzeit — ein 500er beim Speichern),
* dass jede angemeldete Seite Knopf, Sichtblende und Konfiguration traegt,
  anonyme Seiten dagegen nicht,
* dass eine Systemrolle ohne Einrichtung nicht in einen Fehler laeuft, sondern
  die Vorgabe des Skripts bekommt,
* und dass die Vorgabe-Adresse in Python und JavaScript nicht auseinanderlaeuft.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.forms.facility_settings import FacilitySettingsForm
from core.models import AuditLog
from core.models.settings import DEFAULT_QUICK_EXIT_URL
from core.services.system.settings import _AUDIT_FIELDS, _AUDIT_VALUE_FIELDS, update_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
QUICK_EXIT_JS = REPO_ROOT / "src" / "static" / "js" / "quick-exit.js"
SERVICE_WORKER_JS = REPO_ROOT / "src" / "static" / "js" / "sw.js"


@pytest.mark.django_db
class TestSettingsFelder:
    def test_vorgaben_stehen_an_der_zeile(self, settings_obj):
        assert settings_obj.quick_exit_url == DEFAULT_QUICK_EXIT_URL
        assert settings_obj.privacy_screen_on_blur is False

    def test_vorgabeziel_ist_https(self):
        """Eine http-Vorgabe waere ein Downgrade auf jedem fremden WLAN."""
        assert DEFAULT_QUICK_EXIT_URL.startswith("https://")

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.org/",
            "https://example.org/nachrichten",
        ],
    )
    def test_http_und_https_sind_gueltig(self, settings_obj, url):
        settings_obj.quick_exit_url = url
        settings_obj.full_clean()  # wirft nicht

    @pytest.mark.parametrize(
        "url",
        [
            # ftp/ftps laesst Djangos URLField-Default durch — der zusaetzliche
            # Validator schneidet sie weg (kein Navigationsziel im Browser).
            "ftp://example.org/",
            "ftps://example.org/",
            # Die eigentliche Gefahr: ein Skript-Schema in location.replace().
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "kein-schema",
        ],
    )
    def test_andere_schemata_werden_abgelehnt(self, settings_obj, url):
        settings_obj.quick_exit_url = url
        with pytest.raises(ValidationError) as exc:
            settings_obj.full_clean()
        assert "quick_exit_url" in exc.value.error_dict

    def test_leeres_ziel_ist_ungueltig(self, settings_obj):
        """Pflichtfeld: ein leeres Ziel liesse den Knopf ins Nichts zeigen."""
        settings_obj.quick_exit_url = ""
        with pytest.raises(ValidationError) as exc:
            settings_obj.full_clean()
        assert "quick_exit_url" in exc.value.error_dict


@pytest.mark.django_db
class TestFormular:
    def test_beide_felder_sind_editierbar(self, facility, settings_obj):
        form = FacilitySettingsForm(instance=settings_obj, facility=facility)
        assert "quick_exit_url" in form.fields
        assert "privacy_screen_on_blur" in form.fields
        assert form.fields["quick_exit_url"].required is True

    def test_eigene_gruppe_mitschauen_schutz(self, facility, settings_obj):
        """Nicht unter „Sicherheit" versteckt: die Einrichtung soll die
        Fluchtseite bewusst waehlen, nicht zwischen zwei Sitzungs-Timeouts."""
        form = FacilitySettingsForm(instance=settings_obj, facility=facility)
        gruppen = {str(titel): [feld.name for feld in felder] for titel, felder in form.grouped_fields}
        assert gruppen["Mitschauen-Schutz"] == ["quick_exit_url", "privacy_screen_on_blur"]


@pytest.mark.django_db
class TestAudit:
    def test_beide_felder_sind_auditiert(self):
        assert "quick_exit_url" in _AUDIT_FIELDS
        assert "privacy_screen_on_blur" in _AUDIT_FIELDS

    def test_update_settings_speichert_beide_felder(self, settings_obj, admin_user):
        """Ohne Whitelist-Eintrag wirft ``update_settings`` einen ``ValueError``
        — die View reicht ALLE Formularfelder durch, das waere ein 500er beim
        Speichern und kein Formularfehler."""
        update_settings(
            settings_obj,
            admin_user,
            quick_exit_url="https://example.org/",
            privacy_screen_on_blur=True,
        )
        settings_obj.refresh_from_db()
        assert settings_obj.quick_exit_url == "https://example.org/"
        assert settings_obj.privacy_screen_on_blur is True

    def test_auditdetail_traegt_alt_und_neuwert(self, settings_obj, admin_user):
        assert {"quick_exit_url", "privacy_screen_on_blur"} <= _AUDIT_VALUE_FIELDS
        update_settings(
            settings_obj,
            admin_user,
            quick_exit_url="https://example.org/",
            privacy_screen_on_blur=True,
        )
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.SETTINGS_CHANGE,
            target_id=str(settings_obj.pk),
        ).latest("timestamp")
        assert set(entry.detail["changed_fields"]) == {"quick_exit_url", "privacy_screen_on_blur"}
        assert entry.detail["changes"]["quick_exit_url"] == {
            "old": DEFAULT_QUICK_EXIT_URL,
            "new": "https://example.org/",
        }
        assert entry.detail["changes"]["privacy_screen_on_blur"] == {"old": False, "new": True}


@pytest.mark.django_db
class TestAngemeldeteSeiten:
    """Knopf, Sichtblende und Konfiguration auf jeder Seite mit ``base.html``."""

    def _zeitstrom(self, client, user):
        client.force_login(user)
        response = client.get(reverse("core:zeitstrom"))
        assert response.status_code == 200
        return response.content.decode()

    def test_knopf_und_kuerzel_stehen_auf_der_seite(self, client, staff_user, settings_obj):
        body = self._zeitstrom(client, staff_user)
        assert 'data-testid="quick-exit-button"' in body
        assert "Schnell verlassen" in body
        assert 'aria-keyshortcuts="Control+Shift+X Shift+Escape"' in body

    def test_sichtblende_liegt_statisch_im_markup(self, client, staff_user, settings_obj):
        """Die Blende steht IMMER im DOM — auch bei abgeschaltetem Sichtschutz.

        Sie traegt zwei Rollen: den opt-in-Sichtschutz und das sofortige
        Verdecken beim Verlassen. Das Verdecken darf nicht von einer
        Einrichtungs-Einstellung abhaengen.
        """
        body = self._zeitstrom(client, staff_user)
        assert 'id="privacy-screen"' in body
        assert 'id="privacy-screen-resume"' in body
        assert 'role="dialog"' in body

    def test_konfiguration_steht_am_body(self, client, staff_user, settings_obj):
        settings_obj.quick_exit_url = "https://example.org/wetter"
        settings_obj.privacy_screen_on_blur = True
        settings_obj.save(update_fields=["quick_exit_url", "privacy_screen_on_blur"])

        body = self._zeitstrom(client, staff_user)

        assert 'data-quick-exit-url="https://example.org/wetter"' in body
        assert 'data-privacy-screen="1"' in body
        assert 'data-quick-exit-logout-url="/logout/"' in body

    def test_sichtschutz_ist_ohne_einstellung_aus(self, client, staff_user, settings_obj):
        body = self._zeitstrom(client, staff_user)
        assert 'data-privacy-screen="0"' in body

    def test_skript_wird_geladen(self, client, staff_user, settings_obj):
        body = self._zeitstrom(client, staff_user)
        assert re.search(r'<script src="[^"]*js/quick-exit\.js[^"]*" defer></script>', body)

    def test_knopf_ist_das_erste_element_nach_dem_skiplink(self, client, staff_user, settings_obj):
        """WCAG 2.1.1/2.4.3: ein Tab nach dem Skip-Link erreicht den Knopf.

        Markup-Ordnung statt ``tabindex`` — eine positive Tabreihenfolge waere
        genau der Fehler, den der a11y-Nachweis aus #1059 abgeraeumt hat. Der
        funktionale Beleg im echten Browser steht in
        ``src/tests/e2e/test_quick_exit.py``.
        """
        body = self._zeitstrom(client, staff_user)
        skiplink = body.index('href="#main-content"')
        knopf = body.index('id="quick-exit-button"')
        navigation = body.index("<nav")
        assert skiplink < knopf < navigation
        assert "tabindex" not in body[knopf : knopf + 400]


@pytest.mark.django_db
class TestAnonymeSeiten:
    def test_login_traegt_keinen_quick_exit(self, client):
        """Die Anmeldemaske zeigt nichts, was zu verbergen waere — und sie erbt
        ``base.html`` gar nicht erst."""
        body = client.get(reverse("login")).content.decode()
        assert "quick-exit-button" not in body
        assert "privacy-screen" not in body


@pytest.mark.django_db
class TestSystemrolleOhneEinrichtung:
    def test_kein_crash_und_leeres_ziel(self, client, super_admin_user):
        """``request.current_facility`` ist fuer die Systemrolle ``None``.

        Der Template-Zugriff faellt still auf den leeren String zurueck
        (``ObjectDoesNotExist.silent_variable_failure``); das Skript nimmt dann
        seine eigene Vorgabe. Ein 500er waere hier die eigentliche Regression.
        """
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_dashboard"))
        assert response.status_code == 200
        body = response.content.decode()
        assert 'data-quick-exit-url=""' in body
        assert 'data-privacy-screen="0"' in body
        assert 'data-testid="quick-exit-button"' in body


class TestVorgabeLaeuftNichtAuseinander:
    """Die Vorgabe-Adresse steht zweimal — sie muss identisch bleiben.

    In Python, weil das Settings-Feld sie als Default braucht, und in
    ``quick-exit.js``, weil die precachte Offline-Shell keinen Server hat, der
    sie rendern koennte. Ein Auseinanderlaufen faellt im Betrieb erst dann auf,
    wenn jemand offline auf den Knopf drueckt.
    """

    def test_javascript_traegt_dieselbe_vorgabe(self):
        quelle = QUICK_EXIT_JS.read_text(encoding="utf-8")
        treffer = re.search(r'var DEFAULT_URL = "([^"]+)";', quelle)
        assert treffer, "DEFAULT_URL nicht in quick-exit.js gefunden"
        assert treffer.group(1) == DEFAULT_QUICK_EXIT_URL


class TestOfflinePrecache:
    def test_skript_steht_im_app_shell(self):
        """Ohne Pre-Cache waere der Knopf offline ohne Verhalten."""
        quelle = SERVICE_WORKER_JS.read_text(encoding="utf-8")
        assert '"/static/js/quick-exit.js"' in quelle


@pytest.mark.django_db
class TestOfflineShell:
    def test_shell_traegt_knopf_und_skript_ohne_sitzungsbezug(self, client):
        """Die precachte Shell bekommt dasselbe statische Markup.

        Byte-Gleichheit anonym vs. angemeldet pinnt
        ``src/tests/offline/test_offline_shell_neutrality.py``; hier geht es
        darum, dass der Schutz offline ueberhaupt vorhanden ist.
        """
        body = client.get(reverse("core:offline_client_shell")).content.decode()
        assert 'data-testid="quick-exit-button"' in body
        assert 'id="privacy-screen"' in body
        assert "js/quick-exit.js" in body
        # Kein Sitzungszustand: die Konfiguration kommt offline aus dem
        # localStorage-Spiegel, nicht aus dem Template.
        assert "data-quick-exit-url" not in body
        assert "data-quick-exit-logout-url" not in body


class _ButtonContext(HTMLParser):
    """Collect the quick-exit button, its ancestors and the focusables before it.

    Walks the rendered document once and records, for the element with
    ``id="quick-exit-button"``: the attribute dicts of every ancestor, its own
    attributes, the raw inner markup, and every focusable element opened before
    it (skip link included). Void elements never get pushed on the stack.
    """

    _VOID = {"meta", "link", "input", "img", "br", "hr", "source", "path"}
    _FOCUSABLE = {"a", "button", "input", "select", "textarea"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.ancestors = []
        self.button = None
        self.focusables_before = []
        self.label_spans = []
        self._inside_button = False
        self._button_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.button is None and attributes.get("id") == "quick-exit-button":
            self.button = attributes
            self.ancestors = [dict(entry) for _, entry in self.stack]
            self._inside_button = True
            self._button_depth = len(self.stack)
        elif self.button is None and tag in self._FOCUSABLE and "hidden" not in attributes:
            self.focusables_before.append((tag, attributes))
        elif self._inside_button and tag == "span":
            self.label_spans.append({"attrs": attributes, "text": ""})
        if tag not in self._VOID:
            self.stack.append((tag, attributes))

    def handle_endtag(self, tag):
        if tag in self._VOID:
            return
        while self.stack:
            popped, _ = self.stack.pop()
            if popped == tag:
                break
        if self._inside_button and len(self.stack) == self._button_depth:
            self._inside_button = False

    def handle_data(self, data):
        if self._inside_button and self.label_spans:
            self.label_spans[-1]["text"] += data


def _classes(attributes):
    return set((attributes.get("class") or "").split())


def _parse_button_context(html):
    parser = _ButtonContext()
    parser.feed(html)
    assert parser.button is not None, "quick-exit button missing from the document"
    return parser


def _button_markup(html):
    start = html.index('<button type="button" id="quick-exit-button"')
    return re.sub(r"\s+", " ", html[start : html.index("</button>", start) + len("</button>")])


@pytest.fixture(params=["online", "offline"])
def shell_html(request, client, staff_user, settings_obj):
    """Rendered ``base.html`` (online) and ``offline_base.html`` (offline shell)."""
    if request.param == "online":
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
    else:
        response = client.get(reverse("core:offline_client_shell"))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
class TestCompactButtonPlacement:
    """Refs #2660: the quick-exit control is a compact button, not an opaque bar.

    The old wrapper painted a full-width ``bg-canvas`` row of 44-52 px above the
    content; scrolled content was cut off beneath it. Both layouts (online and
    the precached offline shell) must carry the same compact button.
    """

    def test_no_opaque_bar_above_the_content(self, shell_html):
        context = _parse_button_context(shell_html)
        for ancestor in context.ancestors:
            assert "bg-canvas" not in _classes(ancestor), "the quick-exit wrapper must not be opaque"
        bar = next(a for a in context.ancestors if a.get("id") == "quick-exit-bar")
        bar_classes = _classes(bar)
        assert "h-0" in bar_classes, "the bar must take no height of its own"
        assert not any(c.startswith(("min-h-", "py-", "pt-", "pb-")) for c in bar_classes)

    def test_button_is_the_first_focusable_after_the_skip_link(self, shell_html):
        context = _parse_button_context(shell_html)
        assert [tag for tag, _ in context.focusables_before] == ["a"]
        assert context.focusables_before[0][1].get("href") == "#main-content"
        assert "tabindex" not in context.button

    def test_button_keeps_shortcuts_title_and_test_id(self, shell_html):
        button = _parse_button_context(shell_html).button
        assert button["aria-keyshortcuts"] == "Control+Shift+X Shift+Escape"
        assert "Strg+Umschalt+X" in button["title"]
        assert button["data-testid"] == "quick-exit-button"
        assert button["aria-label"] == "Schnell verlassen"

    def test_button_label_is_short_on_mobile_and_full_from_md(self, shell_html):
        spans = _parse_button_context(shell_html).label_spans
        short = [s for s in spans if s["text"].strip() == "Verlassen"]
        full = [s for s in spans if s["text"].strip() == "Schnell verlassen"]
        assert len(short) == 1 and "md:hidden" in _classes(short[0]["attrs"])
        assert len(full) == 1 and {"hidden", "md:inline"} <= _classes(full[0]["attrs"])

    def test_button_has_a_44px_target_and_a_solid_face(self, shell_html):
        classes = _classes(_parse_button_context(shell_html).button)
        assert {"min-h-[44px]", "min-w-[44px]", "text-red-700", "bg-red-50"} <= classes

    def test_first_content_row_with_a_title_leaves_room_for_the_button_on_mobile(self, shell_html):
        """The row holding the page title must not run underneath the button."""
        assert "[&>#flash-messages+*:has(>h1)]:max-md:pr-28" in shell_html
        assert 'id="offline-status" class="pr-32' in shell_html.replace("md:ml-60 ", "")

    def test_wrapper_carries_no_inline_style(self, shell_html):
        context = _parse_button_context(shell_html)
        assert all("style" not in ancestor for ancestor in context.ancestors)
        assert "style" not in context.button

    def test_online_and_offline_shell_render_the_same_button(self, client, staff_user, settings_obj):
        client.force_login(staff_user)
        online = client.get(reverse("core:zeitstrom")).content.decode()
        offline = client.get(reverse("core:offline_client_shell")).content.decode()
        assert _button_markup(online) == _button_markup(offline)
        online_bar = next(a for a in _parse_button_context(online).ancestors if a.get("id") == "quick-exit-bar")
        offline_bar = next(a for a in _parse_button_context(offline).ancestors if a.get("id") == "quick-exit-bar")
        assert _classes(online_bar) == _classes(offline_bar)
